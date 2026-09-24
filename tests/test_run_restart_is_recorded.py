"""A restarted run is visible in its own provenance, and events name real steps.

On 29 Aug 2026 project 001 halted at 12:32:35 on the spine contract and
resumed at 12:33:24.  Nothing in `pipeline_data.json`, the assembly
manifest or `pipeline_output/reasoning/` recorded the interruption: the
rejected 52.1s spine and its rejection lived only in
`logs/pipeline_log.jsonl`, and anyone reading the outputs afterwards saw
a clean single pass.  The cause was one line of behaviour -
`begin_run_status` replaced `pipeline_run.json` wholesale, so the
outgoing account of how a run ended was overwritten by the run that
followed it.

The second half of this file is a VERIFICATION rather than a fix: PR #409
(merged 2 Sep 2026) claimed to resolve `step_error`/`step_end` events
recorded against `unknown_step`.  It did - `step_timer` now binds the
decorated function's signature instead of reading kwargs only, so a
`node_id` passed positionally resolves.  The tests below pin that, so the
attribution cannot silently regress the way it silently broke.
"""
import json
from pathlib import Path

import pytest

from library.tools import pipeline_logger, run_control, run_restart
from library.tools.provenance import ProvenanceLedger, RunRecord


# ── How the previous run ended ──────────────────────────────────────

def test_a_failed_run_is_classified_with_its_cause():
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


def test_a_run_that_wrote_no_ending_is_interrupted_not_failed():
    """A killed process never reported a failure, and saying it did would
    be an attribution the pipeline does not have."""
    restart = run_restart.classify(
        {"status": "running", "current_step": "render", "finished_at": None},
        {})
    assert restart.basis == run_restart.INTERRUPTED
    assert restart.basis != run_restart.AFTER_FAILURE
    assert "render" in restart.cause


@pytest.mark.parametrize("previous,expected", [
    ({"status": "success", "finished_at": "12:00"}, run_restart.CLEAN),
    ({"status": "held", "held_before_step": "render", "finished_at": "12:00"},
     run_restart.AFTER_HOLD),
    ({"status": "gate_pending", "paused_at_gate": "review_rough_cut",
      "finished_at": "12:00"}, run_restart.AFTER_GATE),
    ({}, run_restart.UNKNOWN),
])
def test_every_ending_has_its_own_basis(previous, expected):
    assert run_restart.classify(previous, {}).basis == expected




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
