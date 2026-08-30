"""The run configuration, driven through the real runner.

`tests/test_run_profile.py` and `tests/test_breakpoints.py` hold the two
declarations honest on their own.  This drives `run_pipeline` itself,
because the thing worth proving is not that the modules agree - it is
that the RUN stops at the armed step and nowhere else, that a `revised`
answer reaches the next step's inputs, and that an impossible profile
refuses before anything is written.

The DAG is mocked down to three deterministic steps for speed; the code
under test is the runner's own, unpatched.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from library.processes.edit_video.run_pipeline import (
    load_pipeline_state,
    run_pipeline,
)
from library.tools import review_gate, run_control
from library.tools.project_layout import Area, ProjectLayout


# a -> b -> c, all deterministic, all cheap.
THREE_STEPS = {
    "id": "edit_video",
    "nodes": [
        {"id": "scan", "name": "Scan Project Folder",
         "step_ref": "steps/step_1_01_scan_project"},
        {"id": "catalog", "name": "Catalog Footage",
         "step_ref": "steps/step_1_02_catalog_footage"},
        {"id": "temporal_index", "name": "Temporal Index",
         "step_ref": "steps/step_1_04_temporal_index"},
    ],
    "edges": [
        {"from": "scan", "to": "catalog",
         "data_mapping": {"raw_footage_files": "raw_footage_files"}},
        {"from": "catalog", "to": "temporal_index",
         "data_mapping": {"catalog": "catalog"}},
    ],
}


@pytest.fixture
def project(tmp_path):
    """A project with nothing done yet, so every step really runs."""
    folder = tmp_path / "mock_project"
    folder.mkdir()
    (folder / "pipeline_data.json").write_text(json.dumps({
        "project_folder": str(folder),
        "preflight_completed": {},
        "edit_completed": {},
        "failed_steps": [],
        "step_outputs": {},
    }), encoding="utf-8")
    (folder / "project.yaml").write_text(
        "name: Mock Project\nslug: mock-project\n", encoding="utf-8")
    return folder


def _write_profile(folder: Path, name: str, body: dict) -> Path:
    directory = ProjectLayout(folder).read_dir(Area.RUN_PROFILES)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.yaml"
    path.write_text(yaml.safe_dump(body, sort_keys=False), encoding="utf-8")
    return path


# What each mock step records, so the edges above really carry a key and
# `gather_step_inputs` behaves as it does in a real run.
_OUTPUT = {
    "scan": {"raw_footage_files": ["one.mov", "two.mov", "three.mov"]},
    "catalog": {"catalog": [{"clip_id": "clip_001"}]},
    "temporal_index": {"temporal_event_indices": []},
}


class _Runner:
    """Drives the runner over `THREE_STEPS`, recording what each step was
    handed - which is how a `revised` answer is shown to have ARRIVED
    rather than merely been written to a file."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.seen = []

    def _implementation(self, step_dir):
        return {"type": "deterministic",
                "entry": str(Path(step_dir) / "step.py"), "manifest": {}}

    def _node_of(self, entry) -> str:
        directory = Path(entry).parent.name
        for node in THREE_STEPS["nodes"]:
            if node["step_ref"].endswith(directory):
                return node["id"]
        raise AssertionError(f"unmapped step directory {directory}")

    def _deterministic(self, entry, inputs):
        node_id = self._node_of(entry)
        self.seen.append((node_id, dict(inputs)))
        return dict(_OUTPUT[node_id])

    @property
    def ran(self):
        return [node_id for node_id, _ in self.seen]

    def inputs_of(self, node_id):
        for name, inputs in self.seen:
            if name == node_id:
                return inputs
        return None

    def run(self, **kw):
        with patch("library.processes.edit_video.run_pipeline.load_dag",
                   return_value=THREE_STEPS), \
             patch("library.processes.edit_video.run_pipeline"
                   ".get_step_implementation",
                   side_effect=self._implementation), \
             patch("library.processes.edit_video.run_pipeline"
                   ".run_deterministic_step", side_effect=self._deterministic), \
             patch("library.processes.edit_video.run_pipeline"
                   ".apply_source_identity", return_value=None):
            return run_pipeline(str(self.folder), **kw)


@pytest.fixture
def runner(project):
    return _Runner(project)


# ── A breakpoint stops the run, at that step and nowhere else ────────

def test_the_run_stops_at_the_armed_step_and_nowhere_else(runner, project):
    summary = runner.run(break_at=["catalog"])

    assert summary["status"] == "PARTIAL"
    assert summary["paused_at_gate"] == "catalog"
    assert runner.ran == ["scan", "catalog"], (
        "the run should have stopped after catalog, before temporal_index")

    gate = ProjectLayout(project).read_path(Area.GATES, "catalog")
    assert (gate / "snapshot.json").exists()
    assert review_gate.get_gate_status(str(project), "catalog") == "pending"
    # And nothing was armed at the step before it.
    assert review_gate.get_gate_status(str(project), "scan") == "none"


def test_review_still_gates_after_every_step(runner, project):
    """The flag predates this and must behave exactly as it did: one
    pause, at the first step."""
    summary = runner.run(review_mode=True)
    assert summary["paused_at_gate"] == "scan"
    assert runner.ran == ["scan"]
    assert review_gate.get_gate_status(str(project), "scan") == "pending"


def test_no_breakpoint_runs_straight_through(runner):
    summary = runner.run()
    assert summary.get("paused_at_gate") is None
    assert len(runner.seen) == 3


# ── The three actions ────────────────────────────────────────────────

def test_approved_carries_on_from_where_it_stopped(project):
    first = _Runner(project)
    first.run(break_at=["scan"])
    review_gate.save_gate_feedback(str(project), "scan", "approved",
                                   feedback="looks right")

    second = _Runner(project)
    summary = second.run(resume_mode=True)
    assert second.ran == ["catalog", "temporal_index"]
    assert summary["status"] == "SUCCESS"


def test_rejected_halts_the_run_and_records_the_failure(project):
    first = _Runner(project)
    first.run(break_at=["scan"])
    review_gate.save_gate_feedback(str(project), "scan", "rejected",
                                   feedback="wrong footage")

    second = _Runner(project)
    summary = second.run(resume_mode=True)
    assert summary["status"] == "FAILED"
    assert second.seen == [], "nothing downstream should have run"
    state = load_pipeline_state(str(project))
    assert "scan" in state["failed_steps"]


def test_a_revised_answer_reaches_the_next_step(project):
    """The one that matters. A revision is not a note - it is applied to
    the step's recorded output before the run continues, so the next
    step is handed the captain's value and not the pipeline's."""
    first = _Runner(project)
    first.run(break_at=["scan"])
    original = load_pipeline_state(str(project))["step_outputs"]["scan"]

    review_gate.save_gate_feedback(
        str(project), "scan", "revised",
        feedback="only these two clips",
        revisions={"raw_footage_files": ["a.mov", "b.mov"]})

    second = _Runner(project)
    second.run(resume_mode=True)

    received = second.inputs_of("catalog")
    assert received is not None, "catalog did not run"
    assert received["raw_footage_files"] == ["a.mov", "b.mov"], (
        f"catalog was handed {received.get('raw_footage_files')!r}, not the "
        f"revised value")
    assert original.get("raw_footage_files") != ["a.mov", "b.mov"]

    state = load_pipeline_state(str(project))
    assert state["step_outputs"]["scan"]["__revised"] is True
    assert state["step_outputs"]["scan"]["__revision_feedback"] == \
        "only these two clips"


def test_re_arming_a_gate_throws_away_the_last_answer(project):
    """A gate that pauses is by definition unanswered. This used to keep
    the previous run's `approved`, so a --resume sailed through a pause
    the captain never saw."""
    first = _Runner(project)
    first.run(break_at=["scan"])
    review_gate.save_gate_feedback(str(project), "scan", "approved")
    assert review_gate.get_gate_status(str(project), "scan") == "approved"

    review_gate.save_gate_snapshot(str(project), "scan", "Scan",
                                   {"raw_footage_files": []})
    assert review_gate.get_gate_status(str(project), "scan") == "pending"
    assert review_gate.load_gate_feedback(str(project), "scan") is None


# ── A profile drives the run ─────────────────────────────────────────

def test_a_declared_profile_selects_the_steps_and_arms_the_breakpoint(
        project, runner):
    _write_profile(project, "just_the_catalog", {
        "description": "Scan and catalog, stopping to look at the catalog.",
        "goals": ["catalog"],
        "breakpoints": ["catalog"],
    })
    summary = runner.run(profile="just_the_catalog")
    assert summary["paused_at_gate"] == "catalog"
    assert runner.ran == ["scan", "catalog"]

    record = run_control.read_run_status(str(project))
    assert record["profile"]["name"] == "just_the_catalog"
    assert record["breakpoints"]["steps"] == ["catalog"]


def test_a_project_adopted_profile_needs_no_flag(project, runner):
    (project / "project.yaml").write_text(
        "name: Mock Project\npipeline:\n  run_profile: adopted\n",
        encoding="utf-8")
    _write_profile(project, "adopted", {
        "description": "Adopted by the project.",
        "goals": ["scan"],
        "breakpoints": ["scan"],
    })
    summary = runner.run()
    assert summary["paused_at_gate"] == "scan"
    record = run_control.read_run_status(str(project))
    assert record["profile"]["adopted"] is True


def test_no_break_star_runs_the_profile_without_stopping(project, runner):
    _write_profile(project, "stoppy", {
        "description": "Stops everywhere.",
        "breakpoints": ["*"],
    })
    summary = runner.run(profile="stoppy", no_break_at=["*"])
    assert summary.get("paused_at_gate") is None
    assert len(runner.seen) == 3


def test_profile_none_declines_the_adopted_one(project, runner):
    (project / "project.yaml").write_text(
        "name: Mock Project\npipeline:\n  run_profile: adopted\n",
        encoding="utf-8")
    _write_profile(project, "adopted", {
        "description": "Adopted by the project.",
        "goals": ["scan"],
        "breakpoints": ["scan"],
    })
    summary = runner.run(profile="none")
    assert summary.get("paused_at_gate") is None
    assert len(runner.seen) == 3


# ── The refusal arrives before the run ───────────────────────────────

def test_an_impossible_profile_refuses_before_the_run_starts(project, runner):
    """A configuration layer that lets a bad selection through to a crash
    forty minutes in has failed its main job."""
    _write_profile(project, "impossible", {
        "description": "Wants the temporal index without the catalog it "
                       "declares required.",
        "goals": ["temporal_index"],
        "skip": ["catalog"],
    })
    before = (project / "pipeline_data.json").read_bytes()

    summary = runner.run(profile="impossible")

    assert summary["status"] == "REFUSED"
    assert "catalog" in summary["reason"]
    assert "Refusing before the run starts" in summary["reason"]
    assert runner.seen == [], "no step should have run"
    assert (project / "pipeline_data.json").read_bytes() == before, (
        "the refusal must not have written state")


def test_a_profile_that_cannot_be_read_refuses_by_name(project, runner):
    _write_profile(project, "typo", {"description": "d",
                                     "goals": ["not_a_step"]})
    summary = runner.run(profile="typo")
    assert summary["status"] == "REFUSED"
    assert "not_a_step" in summary["reason"]
    assert runner.seen == []


def test_an_unknown_breakpoint_refuses_by_name(project, runner):
    summary = runner.run(break_at=["not_a_step"])
    assert summary["status"] == "REFUSED"
    assert "not_a_step" in summary["reason"]
    assert runner.seen == []


# ── The plain flags do not regress ───────────────────────────────────

def test_the_existing_flags_still_work_with_no_profile(project, runner):
    summary = runner.run(only=["catalog"])
    assert runner.ran == ["scan", "catalog"]
    assert summary["status"] == "PARTIAL"


def test_a_dry_run_reports_the_configuration(project, runner):
    _write_profile(project, "dry", {
        "description": "d", "goals": ["catalog"], "breakpoints": ["catalog"]})
    summary = runner.run(profile="dry", dry_run=True)
    assert summary["status"] == "DRY_RUN"
    assert summary["profile"] == "dry"
    assert summary["breakpoints"]["steps"] == ["catalog"]
    assert summary["steps_to_run"] == ["scan", "catalog"]
    assert runner.seen == []
