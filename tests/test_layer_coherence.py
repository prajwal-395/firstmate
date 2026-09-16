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


def test_clean_project_reports_nothing(tmp_path):
    root = _fixture_project(str(tmp_path))
    _write(os.path.join(root, "subtitle_plans", "a_subtitles.json"),
           {"cards": [{"text": "say Lucie"}]})
    report = layer_coherence.check_project(root)
    assert report["wording"] == []
    assert report["pins"] == []
    assert report["assets"] == []


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


def test_correction_stamp_and_history_are_not_divergence(tmp_path):
    root = _fixture_project(str(tmp_path))
    _write(os.path.join(
        root, "pipeline_output", "review",
        "reel_proposals_v2_20260911T000000Z.json"),
        {"moments": [{"text": "say lucy"}]})
    report = layer_coherence.check_project(root)
    assert report["wording"] == []


def test_stale_pin_anchor_is_reported(tmp_path):
    root = _fixture_project(str(tmp_path))
    _write(os.path.join(root, "external", "captain_edits.json"),
           {"key": "captain_edits", "source": "vetting",
            "value": [{"kind": "transform_override",
                       "anchor_phrase": "words never spoken here",
                       "property": "Pan", "value": -35.0,
                       "reason": "vetting"}]})
    report = layer_coherence.check_project(root)
    assert len(report["pins"]) == 1
    assert "matches nothing" in report["pins"][0]["found"]


def test_missing_transcript_says_so(tmp_path):
    root = str(tmp_path)
    _write(os.path.join(root, "learned_context", "learnings.json"), [])
    report = layer_coherence.check_project(root)
    assert report["pins"] and "no transcript" in report["pins"][0]["found"]


def test_missing_card_media_is_refused_loudly(tmp_path):
    root = _fixture_project(str(tmp_path))
    _write(os.path.join(root, "external", "placed_assets.json"),
           {"version": 1,
            "assets": [{"slot": "tail", "asset": "/abs/never.mov",
                        "duration_seconds": 5.0,
                        "reason": "vetting"}]})
    report = layer_coherence.check_project(root)
    assert len(report["assets"]) == 1
    assert "not on disk" in report["assets"][0]["found"]


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


def test_not_covered_names_its_missing_reader():
    assert "live timeline" in layer_coherence.NOT_COVERED["look_grade"]
    assert layer_coherence.main.__doc__ is not None


def _record_report(root, report):
    """Store a report where a reels build stores it, and return bytes.

    `reel_build.rebuild_reels_in_project` writes its record under
    `step_outputs.build_reels.reel_build`, and the state writer
    rewrites the whole of `pipeline_data.json` after every step.
    """
    path = os.path.join(root, "pipeline_data.json")
    document = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
    document.setdefault("step_outputs", {}).setdefault(
        "build_reels", {}).setdefault(
            "reel_build", {})["coherence"] = report
    _write(path, document)
    return os.path.getsize(path)


def test_the_scan_does_not_find_its_own_stored_findings(tmp_path):
    """The feedback loop that grew one project's state file to 12.2 GB.

    `check_wording` scans `pipeline_data.json` (regenerated step
    outputs are a display) and a reels build stores this report back
    into that same file. Before the fix each run re-found the previous
    run's rows - quoted inside their own `found` and `context` fields -
    and the file doubled on every build. Measured on the captain's
    `geo-podcast` 2026-09-16: 31,982 of 32,011 rows were self-inflicted.

    Two runs is enough to show it: run two must not be larger than run
    one, and the state file must not grow from having been scanned.
    """
    root = _fixture_project(str(tmp_path))
    _write(os.path.join(root, "subtitle_plans", "a_subtitles.json"),
           {"cards": [{"text": "say lucy to the camera"}]})

    first = layer_coherence.check_project(root)["wording"]
    assert len(first) == 1, "the real divergence is the one in the plan"
    first_bytes = _record_report(root, first)

    second = layer_coherence.check_project(root)["wording"]
    second_bytes = _record_report(root, second)

    assert len(second) == len(first), (
        f"run two found {len(second)} row(s) where run one found "
        f"{len(first)}: the scan is reading its own stored output")
    assert second_bytes == first_bytes, (
        f"the state file grew {first_bytes} -> {second_bytes} bytes "
        f"across two runs that changed nothing")

    third = layer_coherence.check_project(root)["wording"]
    assert len(third) == len(first)
    # The row that survives is the real one, by identity - not merely
    # a count that happens to match.
    assert third[0]["found"] == "lucy"
    assert "a_subtitles.json" in third[0]["layer_file"]
    assert all("pipeline_data.json" not in row["layer_file"]
               for row in third)
