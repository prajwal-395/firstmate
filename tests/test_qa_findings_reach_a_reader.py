"""Every render QA finding reaches a reader, and one that does not is reported.

Step 6.02 has measured the finished video since it was written, and
`exports/qa_report.json` has never been opened by anything.  On project
001's run of record it held all four of the captain's named shortfalls -
the caption gap, the caption read speed, the mix windows and the loudness
- filed as `info` and `warning` where a measurement that nobody reads is
indistinguishable from a measurement nobody took.

These tests hold the three properties of the reader that replaces that:

1. every metric either producer can emit has a declared reader, and a new
   check with no reader FAILS here rather than going quiet;
2. a finding whose metric nobody claims is named, first and loudest, in
   the run summary - the exact failure removed here is not reproducible
   through the new path;
3. severity survives the trip and an advisory finding blocks nothing.

Nothing here touches a real project: the four findings are the rows 001's
own report carries, replayed into a project built under `tmp_path`.
"""
import ast
import json
import os
from pathlib import Path

import pytest

from library.tools import qa_findings as qa
from library.tools import render_qa
from library.tools.subtitle_qa import verify_subtitle_timing

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RENDER_QA = PROJECT_ROOT / "library" / "tools" / "render_qa.py"
SUBTITLE_QA = PROJECT_ROOT / "library" / "tools" / "subtitle_qa.py"
RUN_PIPELINE = (PROJECT_ROOT / "library" / "processes" / "edit_video"
                / "run_pipeline.py")
REVIEW_MANIFEST = (PROJECT_ROOT / "library" / "steps"
                   / "step_3_03_review_rough_cut" / "manifest.json")
DAG = PROJECT_ROOT / "library" / "processes" / "edit_video" / "dag.json"


# ── The four findings, exactly as 001's report carries them ──────────
#
# Verbatim from that project's exports/qa_report.json, so a change that
# stops one of them surfacing fails on the real shape and not on a
# convenient one.

CAPTION_GAP = {
    "metric": "subtitle_gaps", "passed": False,
    "value": [[2, 3, 6.25]], "threshold": 2.0, "severity": "info",
    "detail": "Found 4 gaps longer than 2s",
}
CAPTION_READ_SPEED = {
    "metric": "subtitle_read_speed", "passed": False,
    "value": [[3, 27.4]], "threshold": 25.0, "severity": "warning",
    "detail": "Found 13 subtitles too fast to read (>25 chars/sec)",
}
MIX_WINDOWS = {
    "metric": "speech_above_bed", "passed": True,
    "value": 1, "threshold": 11, "severity": "warning",
    "detail": ("1 of 11 speech-bearing windows meet the margin the plan "
               "itself declared; worst 6.06-8.74s planned background, "
               "speech is +6.7 dB over the bed - REPORTED ONLY; see "
               "SPEECH_ABOVE_BED_GATES for why this is not a build "
               "failure yet"),
}
LOUDNESS = {
    "metric": "lufs", "passed": False,
    "value": -20.94, "threshold": -14.0, "severity": "error",
    "detail": ("LUFS: -20.94, True Peak: -1.62 - integrated -20.94 LUFS "
               "is 6.94 dB off the -14.0 target (tolerance +/-1.0)"),
}
THE_FOUR = [LOUDNESS, CAPTION_GAP, CAPTION_READ_SPEED, MIX_WINDOWS]

A_CLEAN_ROW = {
    "metric": "resolution", "passed": True, "value": "1080x1920",
    "threshold": "1080x1920", "severity": "info",
    "detail": "Resolution is 1080x1920",
}


def _project(tmp_path: Path, rows) -> Path:
    """A project carrying one qa_report.json, and nothing else."""
    project = tmp_path / "a_project"
    (project / "exports").mkdir(parents=True)
    (project / "exports" / "qa_report.json").write_text(
        json.dumps(rows), encoding="utf-8")
    return project


# ── 1. Every metric has a reader ─────────────────────────────────────

def _emitted_metrics(source_path: Path) -> set:
    """The metric names a producer can really construct.

    Read off the source rather than off a run, because reaching every
    branch of `render_qa` means rendering video, and the question here is
    which names EXIST - which the constructor calls answer exactly.
    """
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if getattr(func, "id", getattr(func, "attr", None)) != \
                "RenderQAResult":
            continue
        for kw in node.keywords:
            if kw.arg == "metric" and isinstance(kw.value, ast.Constant):
                names.add(kw.value.value)
    return names


def test_every_metric_the_producers_emit_has_a_reader():
    emitted = _emitted_metrics(RENDER_QA) | _emitted_metrics(SUBTITLE_QA)
    assert emitted, "no metric names were found - the harvest is broken"
    missing = sorted(emitted - set(qa.FINDING_READERS))
    assert not missing, (
        f"{len(missing)} QA metric(s) are measured and no reader claims "
        f"them: {missing}. Add a row to qa_findings.FINDING_READERS "
        f"naming the step that owns the decision and what a reader does "
        f"with it. A measurement nobody reads is the defect this module "
        f"was written to remove.")


def test_no_reader_is_declared_for_a_metric_nothing_emits():
    """The other direction, so the table cannot go stale."""
    emitted = _emitted_metrics(RENDER_QA) | _emitted_metrics(SUBTITLE_QA)
    stale = sorted(set(qa.FINDING_READERS) - emitted)
    assert not stale, (
        f"FINDING_READERS claims {stale}, which no producer emits.")


def test_the_subtitle_producer_really_emits_the_metrics_harvested():
    """The harvest is source-read; run the cheap producer to confirm it."""
    live = {r.metric for r in verify_subtitle_timing(
        [{"timeline_start": 0.0, "timeline_end": 12.0, "text": "x" * 400},
         {"timeline_start": 20.0, "timeline_end": 20.2, "text": "y"}],
        total_duration=10.0)}
    assert live <= set(qa.FINDING_READERS)
    assert "subtitle_read_speed" in live and "subtitle_gaps" in live


def test_every_owner_is_a_real_dag_node():
    nodes = {n["id"] for n in json.loads(DAG.read_text(encoding="utf-8"))["nodes"]}
    for reader in qa.FINDING_READERS.values():
        assert reader.owner in nodes, (
            f"{reader.metric} is owned by '{reader.owner}', which is not a "
            f"DAG node id. Owners are node ids "
            f"(project_layout.node_id_for), not step directory names.")
        assert reader.reading.strip(), f"{reader.metric} has no reading"


# ── 2. A finding that reaches no reader is itself reported ───────────

UNCLAIMED = {
    "metric": "a_check_nobody_wired_a_reader_for", "passed": False,
    "value": 1, "threshold": 0, "severity": "warning",
    "detail": "Something was measured and nothing claims it",
}


def test_an_unclaimed_finding_is_not_dropped():
    read = qa.read_qa_report([A_CLEAN_ROW, UNCLAIMED])
    assert [f.metric for f in read.unrouted] == [UNCLAIMED["metric"]]
    assert read.counts()["unrouted"] == 1


def test_an_unclaimed_finding_is_printed_first_and_loudest():
    lines = qa.summary_lines(qa.read_qa_report([A_CLEAN_ROW, UNCLAIMED]))
    body = [ln for ln in lines if UNCLAIMED["metric"] in ln]
    assert body, f"the unclaimed finding is not in the summary:\n" + "\n".join(lines)
    banner = next(i for i, ln in enumerate(lines) if "NO READER" in ln)
    named = next(i for i, ln in enumerate(lines) if UNCLAIMED["metric"] in ln)
    assert banner < named
    # Before every ordinary finding, not after them.
    ordinary = [i for i, ln in enumerate(lines)
                if "resolution" in ln or "✗ " in ln or "! " in ln]
    assert all(named < i for i in ordinary if i != named)


def test_an_unclaimed_finding_reaches_the_reviewing_step_too():
    payload = qa.findings_for_review(
        qa.read_qa_report([A_CLEAN_ROW, UNCLAIMED]))
    assert payload["unrouted_metrics"] == [UNCLAIMED["metric"]]
    assert "NO READER IS DECLARED" in payload["readings"][UNCLAIMED["metric"]]


# ── 3. The four named findings surface ───────────────────────────────

@pytest.mark.parametrize("row", THE_FOUR,
                         ids=[r["metric"] for r in THE_FOUR])
def test_each_named_finding_surfaces_in_the_run_summary(row, tmp_path):
    project = _project(tmp_path, THE_FOUR + [A_CLEAN_ROW])
    lines = qa.summary_lines(qa.load_findings(str(project), {}))
    printed = "\n".join(lines)
    assert row["metric"] in printed, printed
    assert row["detail"] in printed, printed
    assert row["severity"] in printed, printed


@pytest.mark.parametrize("row", THE_FOUR,
                         ids=[r["metric"] for r in THE_FOUR])
def test_each_named_finding_reaches_the_reviewing_step(row, tmp_path):
    project = _project(tmp_path, THE_FOUR + [A_CLEAN_ROW])
    payload = qa.findings_for_review(qa.load_findings(str(project), {}))
    seen = {f["metric"]: f for f in payload["findings"]}
    assert row["metric"] in seen
    assert seen[row["metric"]]["detail"] == row["detail"]
    assert seen[row["metric"]]["severity"] == row["severity"]
    assert seen[row["metric"]]["owned_by"]
    assert payload["readings"][row["metric"]]


def test_a_clean_check_is_counted_and_not_printed(tmp_path):
    project = _project(tmp_path, THE_FOUR + [A_CLEAN_ROW])
    findings = qa.load_findings(str(project), {})
    assert findings.counts() == {"total": 5, "failing": 3, "advisory": 1,
                                 "clean": 1, "unrouted": 0}
    printed = "\n".join(qa.summary_lines(findings))
    assert "Resolution is 1080x1920" not in printed
    # Counted, though - nothing is hidden.
    assert "1 clean" in printed


def test_a_clean_check_still_travels_to_the_reviewing_step(tmp_path):
    project = _project(tmp_path, THE_FOUR + [A_CLEAN_ROW])
    payload = qa.findings_for_review(qa.load_findings(str(project), {}))
    assert len(payload["findings"]) == 5
    assert any(f["metric"] == "resolution" for f in payload["findings"])


# ── 4. Severity is preserved, and advisory blocks nothing ────────────

def test_the_verdicts_are_what_the_report_says():
    read = qa.read_qa_report(THE_FOUR + [A_CLEAN_ROW])
    verdicts = {f.metric: f.verdict for f in read.findings}
    assert verdicts == {
        "lufs": qa.FAILING,
        "subtitle_gaps": qa.FAILING,
        "subtitle_read_speed": qa.FAILING,
        "speech_above_bed": qa.ADVISORY,
        "resolution": qa.CLEAN,
    }


def test_only_a_failing_error_is_of_blocking_class():
    blocking = {f.metric for f in qa.read_qa_report(THE_FOUR).findings
                if f.blocks}
    assert blocking == {"lufs"}, (
        "an advisory or a sub-error finding is being reported as though a "
        "gate could act on it")


def test_the_advisory_reading_is_an_enumeration_not_a_severity_rule():
    """A stale report must not turn a clean check into a finding.

    `subtitle_qa` used to stamp its failing severity on a PASSING result,
    so reports already on disk carry `subtitle_overlap` at `error` next
    to the words "No overlapping subtitles". Reading advisory off
    severity alone promotes every one of those to a finding.
    """
    stale = {"metric": "subtitle_overlap", "passed": True, "value": [],
             "threshold": 0, "severity": "error",
             "detail": "No overlapping subtitles"}
    assert qa.read_qa_report([stale]).findings[0].verdict == qa.CLEAN
    assert qa.REPORT_ONLY_METRICS == {"chroma_presence", "speech_above_bed"}
    assert render_qa.CHROMA_PRESENCE_GATES is False
    assert render_qa.SPEECH_ABOVE_BED_GATES is False


def test_the_producer_severity_now_moves_with_the_verdict():
    clean = verify_subtitle_timing(
        [{"timeline_start": 0.0, "timeline_end": 2.0, "text": "Hello"}])
    assert {r.severity for r in clean} == {"info"}, (
        "a subtitle check with nothing to report is shouting again")
    dirty = verify_subtitle_timing(
        [{"timeline_start": 0.0, "timeline_end": 0.2, "text": "Hi"}])
    short = next(r for r in dirty if r.metric == "subtitle_too_short")
    assert short.severity == "warning"


def test_reading_the_findings_cannot_change_the_run_status():
    """The summary block runs after the status is decided, and never assigns it."""
    tree = ast.parse(RUN_PIPELINE.read_text(encoding="utf-8"))
    imports = [n.lineno for n in ast.walk(tree)
               if isinstance(n, ast.ImportFrom)
               and any(a.name == "qa_findings" for a in n.names)]
    assert imports, "the run summary no longer reads the QA findings"
    qa_line = max(imports)
    status_writes = [
        n.lineno for n in ast.walk(tree)
        if isinstance(n, (ast.Assign, ast.AugAssign))
        for t in (n.targets if isinstance(n, ast.Assign) else [n.target])
        if isinstance(t, ast.Name) and t.id == "status"]
    assert status_writes, "the run summary no longer computes a status"
    assert max(status_writes) < qa_line, (
        "`status` is assigned after the QA findings are read. Reading is "
        "not gating: an advisory finding must never fail a run.")


# ── The seam into review_rough_cut ───────────────────────────────────

def test_review_rough_cut_declares_the_findings_optional_and_projects_them():
    manifest = json.loads(REVIEW_MANIFEST.read_text(encoding="utf-8"))
    declared = {i["name"]: i for i in manifest["interface"]["inputs"]}
    assert "render_qa_findings" in declared, (
        "step 3.03 no longer declares the render QA findings")
    assert declared["render_qa_findings"]["required"] is False, (
        "a project that has never rendered has no findings; requiring "
        "them would refuse its first run")
    assert "render_qa_findings" in manifest["context_fields"], (
        "declared but projected away - it would never reach the prompt")


def test_no_dag_edge_carries_the_findings():
    """It cannot: the producer is the final node and the consumer is upstream."""
    dag = json.loads(DAG.read_text(encoding="utf-8"))
    order = [n["id"] for n in dag["nodes"]]
    assert order.index("validate") > order.index("review_rough_cut")
    for edge in dag["edges"]:
        assert not (edge["from"] == "validate"
                    and edge["to"] == "review_rough_cut"), (
            "an edge from validate to review_rough_cut is a back edge and "
            "the topological sort will refuse it")


def _state_for(dag: dict, node_id: str, project: Path) -> dict:
    """State carrying just enough upstream output to satisfy the mappings."""
    outputs: dict = {}
    for edge in dag["edges"]:
        if edge["to"] != node_id:
            continue
        for src_key in (edge.get("data_mapping") or {}):
            outputs.setdefault(edge["from"], {})[src_key] = []
    return {"project_folder": str(project), "step_outputs": outputs}


def test_the_runner_hands_the_findings_to_the_step_that_asked(tmp_path):
    from library.processes.edit_video.run_pipeline import gather_step_inputs

    project = _project(tmp_path, THE_FOUR + [A_CLEAN_ROW])
    dag = json.loads(DAG.read_text(encoding="utf-8"))
    manifest = json.loads(REVIEW_MANIFEST.read_text(encoding="utf-8"))
    state = _state_for(dag, "review_rough_cut", project)

    inputs = gather_step_inputs("review_rough_cut", dag, state, manifest,
                                "hybrid", external={})
    payload = inputs["render_qa_findings"]
    assert payload["source"] == qa.SOURCE_FILE
    assert {f["metric"] for f in payload["findings"]} == {
        r["metric"] for r in THE_FOUR + [A_CLEAN_ROW]}
    assert payload["legend"]["not_grounds_for_rejection"]


def test_a_step_that_did_not_ask_is_handed_nothing(tmp_path):
    from library.processes.edit_video.run_pipeline import gather_step_inputs

    project = _project(tmp_path, THE_FOUR)
    dag = json.loads(DAG.read_text(encoding="utf-8"))
    plan_vfx = json.loads(
        (PROJECT_ROOT / "library" / "steps" / "step_4_03_plan_vfx"
         / "manifest.json").read_text(encoding="utf-8"))
    assert "render_qa_findings" not in {
        i["name"] for i in plan_vfx["interface"]["inputs"]}
    state = _state_for(dag, "plan_vfx", project)
    inputs = gather_step_inputs("plan_vfx", dag, state, plan_vfx,
                                "hybrid", external={})
    assert "render_qa_findings" not in inputs


# ── Where the findings came from is recorded, never assumed ──────────

def test_this_runs_report_beats_the_one_on_disk(tmp_path):
    project = _project(tmp_path, THE_FOUR)
    state = {"step_outputs": {"validate": {"qa_report": [A_CLEAN_ROW]}}}
    findings = qa.load_findings(str(project), state)
    assert findings.source == qa.SOURCE_STATE
    assert [f.metric for f in findings.findings] == ["resolution"]


def test_a_report_read_off_disk_says_it_is_a_previous_render(tmp_path):
    project = _project(tmp_path, THE_FOUR)
    findings = qa.load_findings(str(project), {})
    assert findings.source == qa.SOURCE_FILE
    assert "previous render" in findings.source_detail
    assert "previous render" in "\n".join(qa.summary_lines(findings))


def test_a_project_that_has_never_rendered_says_so(tmp_path):
    project = tmp_path / "fresh"
    project.mkdir()
    findings = qa.load_findings(str(project), {})
    assert not findings
    assert findings.counts()["total"] == 0
    assert qa.summary_lines(findings) == [qa.NOTHING_TO_READ]


def test_an_unreadable_report_is_reported_and_does_not_raise(tmp_path):
    project = tmp_path / "broken"
    (project / "exports").mkdir(parents=True)
    (project / "exports" / "qa_report.json").write_text("{not json",
                                                        encoding="utf-8")
    findings = qa.load_findings(str(project), {})
    assert "could not be read" in findings.source_detail


def test_a_row_with_no_verdict_is_not_read_as_clean():
    """An absent judgement is not a judgement of fine."""
    read = qa.read_qa_report([{"metric": "lufs", "detail": "no verdict"}])
    assert read.findings[0].verdict == qa.FAILING
