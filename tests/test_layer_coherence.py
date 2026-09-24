"""Layer coherence: each layer agrees with the one it derives from.

Read-only by construction: the check never writes to the project, so
running it over a fixture project must create no files, and running
it over the captain's project changes nothing. Fixtures live under
`tmp_path` (AGENTS.md 8).
"""

import json
import os

from library.tools import layer_coherence


def _write(path, document):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(document, handle)


def _fixture_project(root, heard="lucy", correct="Lucie"):
    _write(os.path.join(root, "learned_context", "learnings.json"), [
        {"id": "lc-0001", "kind": "correction", "status": "active",
         "statement": "spelling", "read_by": ["*"],
         "source": {"correction_type": "transcript_spelling",
                     "heard": heard, "correct": correct},
         "detail": "vetting"}])
    words = [{"word": "say", "start": 1.0, "end": 1.2, "timed": True},
             {"word": correct, "start": 1.3, "end": 1.7,
              "timed": True}]
    _write(os.path.join(
        root, "pipeline_output", "scratch", "timeline_transcript",
        "transcript.json"),
        {"segments": [{"text": f"say {correct}", "words": words}],
         "transcript_corrections_applied": [
             {"id": "lc-0001", "heard": heard, "correct": correct,
              "replacements": 1}]})
    return root


def test_heard_form_in_display_is_named_with_both_values(tmp_path):
    root = _fixture_project(str(tmp_path))
    _write(os.path.join(root, "subtitle_plans", "a_subtitles.json"),
           {"cards": [{"text": "say lucy to the camera"}]})
    report = layer_coherence.check_project(root)
    assert len(report["wording"]) == 1
    row = report["wording"][0]
    assert row["found"] == "lucy" and row["should_be"] == "Lucie"
    assert "a_subtitles.json" in row["layer_file"]
    assert "say lucy" in row["context"]


def test_check_creates_no_files(tmp_path):
    root = _fixture_project(str(tmp_path))
    before = set()
    for dirpath, _, filenames in os.walk(root):
        before.update(os.path.join(dirpath, f) for f in filenames)
    layer_coherence.check_project(root)
    after = set()
    for dirpath, _, filenames in os.walk(root):
        after.update(os.path.join(dirpath, f) for f in filenames)
    assert after == before


def _file_like_build(root, report):
    """Store a report the way a reels build stores it, and return bytes.

    `reel_build.rebuild_reels_in_project` files the full report at the
    project root (`layer_coherence.write_coherence_report`, outside
    every scanned root) and keeps only counts in `pipeline_data.json`
    (`layer_coherence.summarize_coherence`). The state writer rewrites
    the whole of `pipeline_data.json` after every step.
    """
    layer_coherence.write_coherence_report(root, report)
    path = os.path.join(root, "pipeline_data.json")
    document = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
    document.setdefault("step_outputs", {}).setdefault(
        "build_reels", {}).setdefault(
            "reel_build", {})["coherence_summary"] = (
                layer_coherence.summarize_coherence(report))
    _write(path, document)
    return os.path.getsize(path)


def test_no_pruning_filter_remains(tmp_path):
    """The loop is cut at the cause, not hidden behind a filter.

    A helper that lifts stored rows out of the document before the
    scan reads it leaves the feedback in place and hides it - which is
    how one project's state file reached 12.2 GB unnoticed. Findings
    live outside the scan now, so there is nothing to prune and no
    pruner may exist.
    """
    assert not hasattr(layer_coherence, "_without_own_report"), (
        "the stored-rows filter is back: cut the loop structurally "
        "instead (findings outside the scan, counts in state)")
    assert not hasattr(layer_coherence, "OWN_REPORT_ROUTE"), (
        "the stored-report route is back: nothing about this report "
        "may live where the scan reads")


def test_the_scan_does_not_find_its_own_filed_findings(tmp_path):
    """The feedback loop that grew one project's state file to 12.2 GB.

    `check_wording` scans `pipeline_data.json` (regenerated step
    outputs are a display) and a reels build used to store this report
    back into that same file. Each run then re-found the previous
    run's rows - quoted inside their own `found` and `context` fields -
    and the file doubled on every build. Measured on the captain's
    `geo-podcast` 2026-09-16: 31,982 of 32,011 rows were self-inflicted.

    The cut is structural: the full report is filed outside the scan
    and only counts reach the state file. Two runs through the REAL
    build storage path is enough to show it: run two must find nothing
    new, and the state file must not grow from having been scanned.
    """
    root = _fixture_project(str(tmp_path))
    _write(os.path.join(root, "subtitle_plans", "a_subtitles.json"),
           {"cards": [{"text": "say lucy to the camera"}]})

    first = layer_coherence.check_project(root)["wording"]
    assert len(first) == 1, "the real divergence is the one in the plan"
    first_bytes = _file_like_build(root, layer_coherence.check_project(root))

    second = layer_coherence.check_project(root)["wording"]
    second_bytes = _file_like_build(
        root, layer_coherence.check_project(root))

    assert len(second) == len(first), (
        f"run two found {len(second)} row(s) where run one found "
        f"{len(first)}: the scan is reading its own filed output")
    assert second_bytes == first_bytes, (
        f"the state file grew {first_bytes} -> {second_bytes} bytes "
        f"across two runs that changed nothing")

    third = layer_coherence.check_project(root)["wording"]
    assert len(third) == len(first)
    # The row that survives is the real one, by identity - not merely
    # a count that happens to match.
    assert third[0]["found"] == "lucy"
    assert "a_subtitles.json" in third[0]["layer_file"]


def test_the_sidecar_lives_where_the_scan_never_reads(tmp_path):
    """The filed full rows must be invisible to the scan by location.

    Rows quote the heard form by construction, so any copy under a
    scanned root re-arms the loop no matter what the state file
    carries. The sidecar's path must sit outside every scanned root
    and must not be the state file itself.
    """
    root = _fixture_project(str(tmp_path))
    path = layer_coherence.coherence_report_path(root)
    for sub in layer_coherence.SCAN_SUBDIRS:
        assert not path.startswith(os.path.join(root, sub) + os.sep), (
            f"the sidecar {path} sits under scanned root {sub}")
    assert os.path.basename(path) != "pipeline_data.json"

    _write(os.path.join(root, "subtitle_plans", "a_subtitles.json"),
           {"cards": [{"text": "say lucy to the camera"}]})
    before = layer_coherence.check_project(root)["wording"]
    layer_coherence.write_coherence_report(
        root, layer_coherence.check_project(root))
    after = layer_coherence.check_project(root)["wording"]
    assert len(after) == len(before) == 1, (
        "filing the full report changed the next scan: the sidecar "
        "is being read")


def test_the_state_summary_cannot_self_match(tmp_path):
    """Counts in the state file quote nothing, so they match nothing.

    The summary is what reaches `pipeline_data.json` (which the scan
    reads), so it must carry no `found`, no `context` and no
    `should_be` - only per-class counts. Stored summaries then add
    rows to no future scan, whatever the heard form is.
    """
    root = _fixture_project(str(tmp_path))
    _write(os.path.join(root, "subtitle_plans", "a_subtitles.json"),
           {"cards": [{"text": "say lucy to the camera"}]})
    report = layer_coherence.check_project(root)
    assert len(report["wording"]) == 1
    summary = layer_coherence.summarize_coherence(report)
    assert summary["wording"] == 1
    blob = json.dumps(summary).lower()
    assert "lucy" not in blob, "the summary quotes the heard form"
    assert "found" not in summary and "context" not in summary, (
        "the summary carries row text under a new name")

    _write(os.path.join(root, "pipeline_data.json"),
           {"step_outputs": {"build_reels": {"reel_build": {
               "coherence_summary": summary}}}})
    assert len(layer_coherence.check_project(root)["wording"]) == 1


def test_real_findings_are_still_produced_and_readable(tmp_path):
    """The scan keeps reading the state file; the filed report keeps
    the rows.

    Dropping `pipeline_data.json` from the scan would have been the
    easy cut, but real divergences live in other step outputs there -
    so a real row is planted in the state file outside any report
    route, and the test demands it back. Whatever consumes the
    findings reads the filed sidecar (full rows) and the state
    summary (counts) - both are asserted here, not inspected.
    """
    root = _fixture_project(str(tmp_path))
    _write(os.path.join(root, "subtitle_plans", "a_subtitles.json"),
           {"cards": [{"text": "say lucy to the camera"}]})
    _write(os.path.join(root, "pipeline_data.json"),
           {"step_outputs": {"subtitle_plan": {
               "cards": [{"text": "say lucy again"}]}}})

    report = layer_coherence.check_project(root)
    assert len(report["wording"]) == 2, (
        "the scan must still read the state file for real rows in "
        "other step outputs")
    assert {row["found"] for row in report["wording"]} == {"lucy"}

    path = layer_coherence.write_coherence_report(root, report)
    filed = layer_coherence.read_coherence_report(root)
    assert filed is not None, f"nothing filed at {path}"
    assert len(filed["wording"]) == 2
    assert any(row["should_be"] == "Lucie"
               for row in filed["wording"]), (
        "the filed report must keep both values of each finding")
    summary = layer_coherence.summarize_coherence(report)
    assert summary["status"] == "ok" and summary["wording"] == 2
