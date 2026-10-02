"""The project folder read back as the story of a run.

The captain is walking all 28 steps by hand and needs to open the folder
and answer, without asking anyone: what is this file, which step made it,
what did that step read, and did it work.

`project_layout` organises by KIND. That is the substrate. This is the
other axis - WHO wrote this, WHEN, FROM WHAT - and the property that
matters most is not coverage but honesty: an attribution the pipeline
observed and an attribution it merely declares must never read the same,
and a file nothing accounts for must read as unaccounted for rather than
being handed to whichever step is nearest.
"""
from __future__ import annotations
import json
from pathlib import Path
from unittest.mock import patch
import pytest
from library.tools import provenance
from library.tools.project_layout import AREAS, Area, ProjectLayout
from library.tools.provenance import (
    OBSERVED,
    UNKNOWN,
    ProvenanceLedger,
    read_declared_sources,
)
from library.tools.run_traceback import (
    build_traceback,
    render_artifact_index,
    render_traceback,
    write_traceback,
)
from library.tools import pipeline_logger, run_control, run_restart
from library.tools.provenance import RunRecord
from types import SimpleNamespace
import os
from library.processes.edit_video.run_pipeline import present_llm_step
from library.tools import awaiting_model_answers as awaiting
from library.tools import reel_semantic_visual as sem
from library.tools.reel_conformance_verifier import (
    FindingClass,
    check_semantic_visuals,
)


FIXTURE_STATE = Path(__file__).resolve().parents[2] / "fixtures" / "e2e_pipeline_data.json"

DAG = {
    "id": "edit_video",
    "nodes": [
        {"id": "scan", "name": "Scan Project Folder",
         "step_ref": "steps/step_1_01_scan_project"},
        {"id": "temporal_index", "name": "Temporal Index",
         "step_ref": "steps/step_1_04_temporal_index"},
        {"id": "prosody_analysis", "name": "Prosody Analysis",
         "step_ref": "steps/step_1_05_prosody_analysis"},
        {"id": "render", "name": "Render",
         "step_ref": "steps/step_6_01_render"},
    ],
    "edges": [
        {"from": "scan", "to": "temporal_index",
         "data_mapping": {"raw_footage_files": "raw_footage_files"}},
        {"from": "temporal_index", "to": "prosody_analysis",
         "data_mapping": {"temporal_index": "temporal_index"}},
        {"from": "scan", "to": "prosody_analysis",
         "data_mapping": {"raw_footage_files": "raw_footage_files"}},
    ],
}


@pytest.fixture
def project(tmp_path):
    layout = ProjectLayout(tmp_path).ensure()
    (tmp_path / "project.yaml").write_text("slug: t\n", encoding="utf-8")
    layout.pipeline_data_path.write_text(json.dumps({
        "started_at": "2026-08-17T10:00:00",
        "last_updated": "2026-08-21T20:00:00",
        "preflight_completed": {
            "scan": {"completed_at": "2026-08-17T10:34:06", "elapsed_s": 0.4},
            "temporal_index": {"completed_at": "2026-08-17T17:09:28",
                               "elapsed_s": 580.3},
        },
        "edit_completed": {},
        "step_outputs": {
            "scan": {"raw_footage_files": [], "total_files": 0},
            "temporal_index": {"index_dir": "x"},
        },
        "failed_steps": ["render"],
        "step_errors": {"render": "Step failed (exit 1):\n  no timeline"},
        "source_fingerprints": {
            "clip_001": {"path": str(tmp_path / "raw" / "IMG_1806.MOV"),
                         "size_bytes": 7354921, "content_digest": "abc"},
        },
    }), encoding="utf-8")
    return tmp_path


def _write(layout, area, name, payload):
    p = layout.write_path(area, name)
    p.write_text(json.dumps(payload) if isinstance(payload, dict) else payload,
                 encoding="utf-8")
    return p


# ── Observation: the strong kind ────────────────────────────────────

def test_a_file_written_during_a_step_is_attributed_to_that_step(project):
    layout = ProjectLayout(project)
    ledger = ProvenanceLedger(project)
    before = ledger.snapshot()
    _write(layout, Area.PROSODY, "clip_001_prosody.json", {"clip_id": "clip_001"})
    recs = ledger.observe("prosody_analysis", "run-1", before, ledger.snapshot())

    assert [r.path for r in recs] == [
        "pipeline_output/steps/1_05_prosody_analysis/clip_001_prosody.json"]
    assert recs[0].step_id == "prosody_analysis"
    assert recs[0].run_id == "run-1"
    assert recs[0].method == OBSERVED
    assert recs[0].area == Area.PROSODY.value

    # A file the step did not touch in its window is not attributed to it.
    before = ledger.snapshot()
    _write(layout, Area.SUBTITLE_SEGMENTS, "new.json", {"b": 2})
    recs = ledger.observe("render_subtitles", "run-1", before, ledger.snapshot())
    assert [r.path for r in recs] == [
        "pipeline_output/steps/4_05_render_subtitles/new.json"]


def test_the_provenance_store_is_append_only(project):
    """A run is a thing that happened. Run 4 does not unmake run 3."""
    layout = ProjectLayout(project)
    ledger = ProvenanceLedger(project)
    p = _write(layout, Area.PROSODY, "c.json", {"v": 1})
    ledger.observe("prosody_analysis", "run-1", {}, ledger.snapshot())
    before = ledger.snapshot()
    p.write_text(json.dumps({"v": 2}), encoding="utf-8")
    ledger.observe("prosody_analysis", "run-2", before, ledger.snapshot())

    raw = layout.read_path(Area.PROVENANCE, "artifacts.jsonl").read_text(
        encoding="utf-8").strip().splitlines()
    assert len(raw) == 2
    assert {json.loads(r)["run_id"] for r in raw} == {"run-1", "run-2"}

    # The store does not record itself: the next observation names only
    # the step's own new file, not artifacts.jsonl.
    before = ledger.snapshot()
    _write(layout, Area.PROSODY, "d.json", {"a": 2})
    recs = ledger.observe("prosody_analysis", "run-3", before, ledger.snapshot())
    assert [r.path for r in recs] == [
        "pipeline_output/steps/1_05_prosody_analysis/d.json"]


# ── Declaration: the weaker kind, labelled as such ──────────────────

def test_every_declared_producer_is_a_real_step_or_a_named_non_step():
    """A declaration pointing at a step id that does not exist is worse
    than no declaration - it reads as an answer."""
    from library.tools.project_layout import NON_STEP_PRODUCERS
    from library.tools.run_traceback import implemented_step_ids, load_dag

    known = ({n["id"] for n in load_dag()["nodes"]}
             | implemented_step_ids()
             | set(NON_STEP_PRODUCERS))
    for area, spec in AREAS.items():
        for producer in spec.produced_by:
            assert producer in known, (
                f"{area.value} declares producer {producer!r}, which is "
                f"neither a DAG node nor a NON_STEP_PRODUCERS entry")


# ── Unknown stays unknown ───────────────────────────────────────────

def test_an_unsorted_file_reads_as_unknown_not_as_organizes_work(project):
    """`organize` MOVED these. Saying it PRODUCED them would turn an
    admitted unknown back into an attribution."""
    layout = ProjectLayout(project)
    p = _write(layout, Area.UNSORTED, "media/001.mov", "x")
    rec = ProvenanceLedger(project).of(p)
    assert rec.method == UNKNOWN
    assert rec.step_id is None


# ── Derived-from: read, never inferred ──────────────────────────────

def test_recorded_sources_are_read_never_inferred(project, monkeypatch):
    layout = ProjectLayout(project)
    src = str(project / "raw" / "IMG_1806.MOV")
    p = _write(layout, Area.PROSODY, "source_file.json", {"source_file": src})
    assert read_declared_sources(p) == [src]

    p = _write(layout, Area.PROSODY, "c.json", {"clip_id": "clip_001"})
    assert read_declared_sources(p) == [], (
        "a clip_id is not a path; guessing the file from it is the "
        "inference this must not make")

    # A huge JSON artifact is skipped rather than parsed.
    monkeypatch.setattr(provenance, "SOURCE_SCAN_CEILING_BYTES", 8)
    p = _write(layout, Area.PROSODY, "big.json", {"source_file": "/a/b.mov"})
    assert read_declared_sources(p) == []


# ── The per-step export ─────────────────────────────────────────────

# ── What gets walked ────────────────────────────────────────────────

def test_archived_copies_are_counted_not_attributed(project):
    """An archived copy is not the artifact the step wrote."""
    layout = ProjectLayout(project)
    p = layout.write_path(Area.BACKUPS, "run_archives", "old", "clip_001.json")
    p.write_text("{}", encoding="utf-8")
    ledger = ProvenanceLedger(project)
    assert not [r for r in ledger.all_artifacts() if "run_archives" in r.path]
    assert ledger.archived_file_count() >= 1
    archived = [r.path for r in ledger.all_artifacts(include_archives=True)
                if "run_archives" in r.path]
    assert archived, "asking for them explicitly must list them"


# ── The traceback document ──────────────────────────────────────────

def test_an_area_with_two_declared_producers_names_both(project):
    """`exports/` is written by `render` AND `validate`. Printing the
    first would be an answer the pipeline does not have."""
    from library.tools.provenance import ProvenanceLedger

    layout = ProjectLayout(project)
    p = layout.write_path(Area.EXPORTS, "Pipeline_Edit.mp4")
    p.write_bytes(b"\x00" * 8)
    rec = ProvenanceLedger(project).of(p)
    assert rec.step_id is None
    assert set(rec.candidates) == {"render", "validate"}
    # The exports are walked: "what is Pipeline_Edit.mp4" is the first
    # question anyone asks.
    assert "exports/Pipeline_Edit.mp4" in [
        r.path for r in ProvenanceLedger(project).all_artifacts()]

    data = build_traceback(project, dag=DAG)
    md = render_artifact_index(data)
    assert "`render` or `validate`" in md
    tb = render_traceback(data)
    assert "shared, not attributed" in tb
    assert "no run has been observed that would settle it" in tb


def test_a_failed_step_shows_the_reason_not_just_the_preamble(project):
    """"Step failed (exit 1):" is the preamble. The reason is two lines
    further down, and it is the whole point of reading this."""
    layout = ProjectLayout(project)
    state = json.loads(layout.pipeline_data_path.read_text(encoding="utf-8"))
    state["step_errors"]["render"] = (
        "Step failed (exit 1):\n  stderr: Validation failed: 1 issue(s)\n"
        "  - [audio_levels] LUFS: -17.47, True Peak: 1.85\n")
    layout.pipeline_data_path.write_text(json.dumps(state), encoding="utf-8")
    md = render_traceback(build_traceback(project, dag=DAG))
    assert "True Peak: 1.85" in md


def test_it_works_on_a_project_with_no_provenance_ledger_at_all(project):
    """Project 001. The run already happened; nothing was watching."""
    layout = ProjectLayout(project)
    _write(layout, Area.TEMPORAL_INDEX, "clip_001.json", {"clip_id": "c1"})
    assert not layout.read_path(Area.PROVENANCE, "artifacts.jsonl").exists()
    with patch("library.tools.run_traceback.load_dag", return_value=DAG):
        w = write_traceback(project)
    assert w["observed"] == 0 and w["declared"] >= 1
    assert Path(w["traceback"]).is_file()
    assert Path(w["artifact_index"]).is_file()


# --------------------------------------------------------------------------
# From test_run_restart_is_recorded.py
#
# A restarted run is visible in its own provenance, and events name real steps.
#
# On 29 Aug 2026 project 001 halted at 12:32:35 on the spine contract and
# resumed at 12:33:24.  Nothing in `pipeline_data.json`, the assembly
# manifest or `pipeline_output/reasoning/` recorded the interruption: the
# rejected 52.1s spine and its rejection lived only in
# `logs/pipeline_log.jsonl`, and anyone reading the outputs afterwards saw
# a clean single pass.  The cause was one line of behaviour -
# `begin_run_status` replaced `pipeline_run.json` wholesale, so the
# outgoing account of how a run ended was overwritten by the run that
# followed it.
#
# The second half of this file is a VERIFICATION rather than a fix: PR #409
# (merged 2 Sep 2026) claimed to resolve `step_error`/`step_end` events
# recorded against `unknown_step`.  It did - `step_timer` now binds the
# decorated function's signature instead of reading kwargs only, so a
# `node_id` passed positionally resolves.  The tests below pin that, so the
# attribution cannot silently regress the way it silently broke.

# ── How the previous run ended ──────────────────────────────────────

def test_every_ending_has_its_own_basis():
    """A failure carries its step and cause; a run that wrote no ending
    was killed, so it is INTERRUPTED and never claimed as a failure (an
    attribution the pipeline does not have); every other ending maps to
    its own basis."""
    restart = run_restart.classify(
        {"status": "failed", "mode": "full run", "started_at": "12:30:00",
         "finished_at": "12:32:35"},
        {"failed_steps": ["mesh_spine"],
         "step_errors": {"mesh_spine":
                         "Post-bridge failed: spine duration 52.1s"}},
    )
    assert restart.basis == run_restart.AFTER_FAILURE
    assert restart.is_restart
    assert restart.stopped_at_step == "mesh_spine"
    assert "52.1s" in restart.cause
    assert restart.previous_finished_at == "12:32:35"

    restart = run_restart.classify(
        {"status": "running", "current_step": "render", "finished_at": None},
        {})
    assert restart.basis == run_restart.INTERRUPTED
    assert "render" in restart.cause

    for previous, expected in [
        ({"status": "success", "finished_at": "12:00"}, run_restart.CLEAN),
        ({"status": "held", "held_before_step": "render",
          "finished_at": "12:00"}, run_restart.AFTER_HOLD),
        ({"status": "gate_pending", "paused_at_gate": "review_rough_cut",
          "finished_at": "12:00"}, run_restart.AFTER_GATE),
        ({}, run_restart.UNKNOWN),
    ]:
        assert run_restart.classify(previous, {}).basis == expected, previous


# ── The status file keeps what it used to overwrite ─────────────────

def test_the_previous_account_survives_the_next_run(tmp_path):
    project = str(tmp_path)
    run_control.begin_run_status(project, "full run", ["mesh_spine"])
    run_control.write_run_status(project, status="failed",
                                 finished_at="2026-08-29T12:32:35",
                                 summary_status="FAILED")

    state = {"failed_steps": ["mesh_spine"],
             "step_errors": {"mesh_spine": "Post-bridge failed: spine 52.1s"}}
    record = run_control.begin_run_status(
        project, "full run", ["mesh_spine"], state=state)

    assert record["restart"], (
        "the run that followed a failure records no restart - which is "
        "how the 29 Aug interruption became invisible"
    )
    assert record["restart"]["basis"] == run_restart.AFTER_FAILURE
    assert "52.1s" in record["restart"]["cause"]
    assert record["run_history"], "the outgoing account was thrown away"
    assert record["run_history"][-1]["status"] == "failed"

    on_disk = json.loads(
        (tmp_path / run_control.RUN_STATUS_FILE).read_text(encoding="utf-8"))
    assert on_disk["restart"]["stopped_at_step"] == "mesh_spine"


# ── It reaches the outputs, not only a log ──────────────────────────

def test_the_state_file_carries_the_restart():
    state = {}
    restart = run_restart.classify(
        {"status": "failed", "finished_at": "x"},
        {"failed_steps": ["mesh_spine"],
         "step_errors": {"mesh_spine": "Post-bridge failed"}})
    run_restart.append_to_state(state, restart)
    assert state[run_restart.STATE_KEY][0]["basis"] == run_restart.AFTER_FAILURE


# ── What can be recovered for runs already on disk ──────────────────

def test_past_restarts_are_reconstructed_without_inventing_a_cause():
    """The ledger records THAT a run ended and its status.  The step and
    the words lived in the state file, which the next run overwrote - so
    a reconstruction says so rather than guessing."""
    rows = run_restart.reconstruct_from_ledger([
        RunRecord("20260829T123000-1", "s", status="failed", ended_at="e"),
        RunRecord("20260829T123324-1", "s", status="success", ended_at="e"),
        RunRecord("20260830T090000-1", "s", status="success", ended_at="e"),
    ])
    assert len(rows) == 1
    assert rows[0]["run_id"] == "20260829T123324-1"
    assert rows[0]["follows_run_id"] == "20260829T123000-1"
    assert rows[0]["basis"] == run_restart.AFTER_FAILURE
    assert rows[0]["cause"] == "", "a reconstructed cause would be invented"
    assert rows[0]["reconstructed"] is True


# ── PR #409's half: step ids on the events ──────────────────────────

def _drain(tmp_path):
    (tmp_path / "pipeline_output" / "logs").mkdir(parents=True, exist_ok=True)
    pipeline_logger._logger_instance = pipeline_logger.PipelineLogger(
        str(tmp_path))
    log = tmp_path / "pipeline_output" / "logs" / "pipeline_log.jsonl"
    return log


def test_step_end_and_step_error_name_the_real_step(tmp_path):
    """VERIFIED, not fixed: PR #409 resolved this by binding the
    decorated signature.  Pinned here so it cannot regress silently -
    every per-step timing or error tally is built off these ids."""
    log = _drain(tmp_path)

    @pipeline_logger.step_timer(step_id_kwarg="node_id")
    def ran(prompt, inputs, node_id, manifest=None):
        return 1

    @pipeline_logger.step_timer(step_id_kwarg="node_id")
    def failed(prompt, inputs, node_id):
        raise RuntimeError("the spine contract rejected it")

    ran("p", {}, "mesh_spine")            # node_id passed POSITIONALLY
    with pytest.raises(RuntimeError):
        failed("p", {}, "plan_vfx")

    events = [json.loads(l) for l in
              log.read_text(encoding="utf-8").strip().splitlines()]
    by_type = {e["event_type"]: e["step_id"] for e in events}
    assert by_type["step_end"] == "mesh_spine"
    assert by_type["step_error"] == "plan_vfx"
    assert "unknown_step" not in by_type.values()


# --------------------------------------------------------------------------
# From test_run_group_wall_clock.py
#
# Run-group wall clock on the run's own record.
#
# Filed 2026-09-26: per-step `elapsed_s` exists and per-invocation
# start/finish exists, but one plan run is several CLI invocations
# (review gates, `--resume`, single steps) and each one started a fresh
# account - so nothing summed a full plan run end to end. `run_group` is
# that sum: a `--resume` carries the previous run's group id forward and
# each invocation stamps its own wall plus the group's running total.
#
# Fixture-only: nothing here runs a step, renders, transcribes or opens
# a real project.

def _begin(project, steps, carry=False):
    run_control.begin_run_status(
        str(project), "full run", steps, argv=[],
        profile=SimpleNamespace(name="", path="", source="",
                                adopted=False, description=""),
        breakpoints={}, state={}, carry_run_group=carry)


def _record(project):
    return run_control.read_run_status(str(project))


def _finish(project, started_at, finished_at):
    run_control.write_run_status(
        str(project), status="success", current_step=None,
        started_at=started_at, finished_at=finished_at)


def test_a_resume_carries_the_group_while_a_rerun_mints_a_new_one(tmp_path):
    """A `--resume` that minted a fresh group would split one plan run's
    wall across two groups, and the run-total would be unreadable -
    carrying is the whole point of the id."""
    project = tmp_path / "proj"
    project.mkdir()
    _begin(project, ["scan"])
    _finish(project, "2026-09-26T10:00:00", "2026-09-26T10:01:40")
    run_control.record_run_total(str(project))
    first = _record(project)
    assert first["invocation_wall_s"] == 100.0
    assert first["run_group_wall_s"] == 100.0

    _begin(project, ["catalog"], carry=True)
    resumed = _record(project)
    assert resumed["run_group"]["id"] == first["run_group"]["id"]
    assert resumed["run_group"]["prior_wall_s"] == 100.0

    _finish(project, "2026-09-26T11:00:00", "2026-09-26T11:00:30")
    run_control.record_run_total(str(project))
    second = _record(project)
    assert second["invocation_wall_s"] == 30.0
    assert second["run_group_wall_s"] == 130.0

    _begin(project, ["scan"])
    fresh = _record(project)
    assert fresh["run_group"]["id"] != first["run_group"]["id"]
    assert fresh["run_group"]["prior_wall_s"] == 0.0


def test_a_resume_with_no_previous_group_starts_one_rather_than_inheriting(
        tmp_path):
    """A group id that names no first invocation cannot be summed, so a
    resume after a deleted status file mints instead of carrying."""
    project = tmp_path / "proj"
    project.mkdir()
    _begin(project, ["scan"], carry=True)
    record = _record(project)
    assert record["run_group"]["id"].startswith("rg-")
    assert record["run_group"]["prior_wall_s"] == 0.0


def test_an_uncomputable_wall_is_left_unwritten_never_estimated(tmp_path):
    """A wall with no honest endpoints is absent, not zero: a zero would
    read as "this invocation was free" and silently shrink every group
    total it joins."""
    project = tmp_path / "proj"
    project.mkdir()
    _begin(project, ["scan"])
    assert run_control.record_run_total(str(project)) == _record(project)
    assert "invocation_wall_s" not in _record(project)
    assert "run_group_wall_s" not in _record(project)


# --------------------------------------------------------------------------
# From test_run_step_timings.py
#
# Per-step wall clock on the run's own record.
#
# Filed 2026-09-17: the captain asked how long a rebuild took versus the
# original build, and no run had ever written down what it cost - the
# answer had to be assembled from file counts and mtimes. `step_timings`
# in `pipeline_run.json` is that record: one honest duration for each
# step that RAN, and a `reused` mark for each step that was skipped
# because its recorded output was still good.
#
# Fixture-only: nothing here runs a step, renders, transcribes or opens
# a real project.

def _begin_2(project, steps):
    run_control.begin_run_status(
        str(project), "full run", steps, argv=[],
        profile=SimpleNamespace(name="", path="", source="",
                                adopted=False, description=""),
        breakpoints={}, state={})


def test_completed_run_carries_a_duration_for_each_step_that_ran(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    _begin_2(project, ["scan", "catalog", "temporal_index"])
    run_control.record_step_timing(str(project), "scan", duration_s=12.34)
    run_control.record_step_timing(str(project), "catalog", duration_s=0.24)
    run_control.record_step_timing(
        str(project), "temporal_index", reused=True)
    record = _record(project)
    assert record["step_timings"]["scan"] == {
        "duration_s": 12.3, "reused": False}
    assert record["step_timings"]["catalog"] == {
        "duration_s": 0.2, "reused": False}
    # The reused step says so, and carries no invented duration.
    assert record["step_timings"]["temporal_index"] == {"reused": True}

    # A rerun overwrites the step's previous entry.
    run_control.record_step_timing(str(project), "temporal_index",
                                   duration_s=5.0)
    assert _record(project)["step_timings"]["temporal_index"] == {
        "duration_s": 5.0, "reused": False}


def test_new_run_starts_fresh_rather_than_carrying_costs_forward(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    _begin_2(project, ["scan"])
    run_control.record_step_timing(str(project), "scan", duration_s=60.0)
    _begin_2(project, ["scan"])
    assert _record(project)["step_timings"] == {}


# --------------------------------------------------------------------------
# From test_run_pipeline_silent_fail.py

def test_present_llm_step_raises_on_silent_no_answer(tmp_path):
    # Setup a mock step directory and manifest
    node_id = "step_6_01_render"
    prompt_path = tmp_path / "handoff.md"
    prompt_path.write_text("Hello LLM", encoding="utf-8")
    
    # We need a manifest that EXPECTS an LLM output, otherwise it skips the call.
    # The step we removed render_review from no longer expects one,
    # so we mock a manifest that STILL expects one to prove the mechanism raises.
    manifest = {
        "interface": {
            "outputs": [
                {"name": "some_llm_output", "type": "object", "required": False}
            ]
        }
    }
    
    inputs = {"project_folder": str(tmp_path)}
    
    # Run with a backend that doesn't exist or without a backend so it falls through to qa_loop.
    # It will fail QA (because parsed_result=None) and best_output will be None, which evaluates to {}.
    # With our fix, it should raise RuntimeError instead of returning {}.
    with pytest.raises(RuntimeError) as exc_info:
        present_llm_step(
            prompt_path=str(prompt_path),
            inputs=inputs,
            node_id=node_id,
            manifest=manifest,
            full_auto=None,
            llm_timeout=1
        )
    
    assert "produced no LLM output" in str(exc_info.value)


# --------------------------------------------------------------------------
# From test_awaiting_model_answers.py
#
# The outstanding-request surface: an awaiting reel must be NAMED, not passed silently.
#
# A build that owes a model answer used to report success and say nothing
# about it: F22 returned its findings unchanged for an
# `awaiting_model_answer` record with nothing placed, and no rollup
# anywhere counted what the build owed. These tests fail on that old
# shape (F22 `== []`, `collect` unknown) and pass on the new one.

FPS = 24000 / 1001


def _awaiting_record(reel="Reel 09"):
    return {"reel": reel, "basis": sem.AWAITING_MODEL_ANSWER,
            "entries": [], "dropped": [], "segments": []}


# ── F22 names the awaiting reel ──────────────────────────────────────

def test_an_awaiting_reel_with_nothing_placed_is_reported_not_passed():
    findings = check_semantic_visuals("Reel 09", [], _awaiting_record(),
                                      FPS)
    assert len(findings) == 1
    assert findings[0].finding_class == FindingClass.F22
    assert "awaiting_model_answer" in findings[0].message


def test_a_decision_for_nothing_still_passes_quietly():
    """`model_planned_none` is a decision, not an owed answer - F22
    must not start naming every reel the model deliberately left
    empty."""
    findings = check_semantic_visuals(
        "Reel 09", [],
        {"reel": "Reel 09", "basis": sem.MODEL_PLANNED_NONE,
         "entries": [], "dropped": [], "segments": []}, FPS)
    assert findings == []


# ── The rollup counts what the build wrote ───────────────────────────


def test_collect_reads_motion_from_state_when_not_built_this_call(tmp_path):
    """A partial build that did not touch a reel still counts the
    motion answer that reel owes, off the stored build record."""
    from library.tools import reel_look as look

    project = tmp_path / "proj"
    (project / "pipeline_output" / "review").mkdir(parents=True)
    state = {"step_outputs": {
        "build_reels": {"reel_build": {"picture_motion": [
            {"reel": "Reel 07", "basis": look.MOTION_AWAITING_ANSWER,
             "proposed": 0, "resolved": 0, "dropped": []}]}}}}
    report = awaiting.collect(str(project), state=state)
    assert report["outstanding_answers"] == 1
    assert report["reels"] == [
        {"reel": "Reel 07", "layers": ["picture_motion"]}]


def test_summary_lines_name_every_incomplete_reel():
    lines = awaiting.summary_lines({
        "reels": [
            {"reel": "Reel 09",
             "layers": ["picture_motion", "semantic_visuals"]},
            {"reel": "Reel 07", "layers": ["picture_motion"]}],
        "outstanding_answers": 3})
    text = "\n".join(lines)
    assert "3" in lines[0] and "incomplete" in lines[0]
    assert "Reel 09" in text and "Reel 07" in text


# ── End to end off a withheld answer, no Resolve, no model ──────────

class _Moment:
    number = 9
    timeline_name = "Reel 09 - your-website-is-only-20-percent"


def test_a_withheld_answer_travels_from_build_to_rollup(tmp_path):
    """The scratch-project proof: no answer file, so `build_for_reel`
    records `awaiting_model_answer`; once written, `collect` owes it
    and F22 names it instead of passing it."""
    project = tmp_path / "proj"
    (project / "pipeline_output" / "review").mkdir(parents=True)
    segments, record = sem.build_for_reel(
        _Moment(), {"structure": []}, [(631.12, 693.3)], str(project),
        fps=FPS, width=1080, height=1920)
    assert segments == []
    assert record["basis"] == sem.AWAITING_MODEL_ANSWER
    sem.write_records(str(project), [record])

    report = awaiting.collect(str(project), motion_records=[])
    assert report["outstanding_answers"] == 1
    assert report["reels"] == [
        {"reel": _Moment.timeline_name, "layers": ["semantic_visuals"]}]

    stored = sem.read_records(str(project))
    findings = check_semantic_visuals(
        _Moment.timeline_name, [],
        sem.record_for_reel(stored, _Moment.timeline_name), FPS)
    assert len(findings) == 1
    assert findings[0].severity == "warning"
