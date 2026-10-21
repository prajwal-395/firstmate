"""PERF-011: the repeated failure costs the preparation once, not twice.

The measurement the punchlist asks for: a step that refuses is run
twice, and the second run must not pay for the preparation again. The
pair is run twice - once with the memo answering the repeat and once
with it answering nothing, which is the behaviour the engine shipped
before the memo existed - and the difference is what the change is
worth.

The defect this names: a lane that refuses a step and is re-run pays
for the same decision twice. On the run the punchlist measured, the
planning step was reconstructed for 3,220 s and four build attempts
each refused the same way.

The numbers this prints are the evidence in
`docs/evidence/perf011-retry-loops.md`.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from library.processes.edit_video import run_pipeline as runner
from library.tools.ren_refusal import RenRefusal

PREPARATION_SECONDS = 1.0
"""What the step's preparation costs here. The real figure is minutes;
the shape of the measurement is the same."""


def _project(root: Path, name: str) -> Path:
    project = root / name
    project.mkdir(parents=True)
    (project / "project.yaml").write_text(
        "name: PERF-011\nslug: perf011-measure\n", encoding="utf-8")
    (project / "pipeline_data.json").write_text(json.dumps({
        "project_folder": str(project),
        "preflight_completed": {},
        "edit_completed": {},
        "failed_steps": [],
        "capability_outputs": {},
    }), encoding="utf-8")
    return project


def _drive(monkeypatch, executed: list):
    dag = {
        "id": "edit_video",
        "nodes": [{"id": "scan", "name": "scan",
                   "step_ref": "steps/step_1_01_scan_project"}],
        "edges": [],
    }
    manifests = {"scan": {
        "id": "step_scan",
        "interface": {"inputs": [],
                      "outputs": [{"name": "result", "type": "string"}]},
        "classification": {"stage": "edit"},
    }}

    def implementation(step_dir):
        return {"type": "deterministic",
                "entry": str(Path(step_dir) / "step.py"),
                "step_dir": Path(step_dir),
                "manifest": manifests["scan"]}

    def run_step(entry, _inputs):
        executed.append("scan")
        time.sleep(PREPARATION_SECONDS)
        raise RenRefusal(
            what="the plan names an overlay that is not on disk",
            why="the overlay segment was planned and never rendered",
            fix="render the overlay, then re-run the step",
            code="REN_TEST_OVERLAY_MISSING", blocker="missing_media")

    monkeypatch.setattr(runner, "load_dag", lambda: dag)
    monkeypatch.setattr(runner, "get_step_implementation", implementation)
    monkeypatch.setattr(runner, "run_deterministic_step", run_step)
    monkeypatch.setattr(runner, "apply_source_identity", lambda *_a: None)
    monkeypatch.setattr(
        runner, "parallel_workers_enabled", lambda *_a, **_k: False)
    from library.tools import capabilities
    monkeypatch.setattr(capabilities, "all", lambda: ())
    monkeypatch.setattr(
        runner.briefing_chat, "conduct_if_needed",
        lambda _project, state, *_a, **_k: state)
    from library.tools import requirements as R
    monkeypatch.setattr(
        R, "all_requirements", lambda dag=None, manifests=None: [])
    # The identity the memo matches on. Production computes the planner
    # cache's own identity, which is not cheap to hash; the measurement
    # is about the SECOND run, not about which identity it matched.
    monkeypatch.setattr(
        runner, "_step_identity_key", lambda *a, **k: "perf011-key")


def _pair(root: Path, monkeypatch, memo: bool) -> tuple[float, list]:
    """Two runs of a step that refuses. Returns (seconds, executions)."""
    from library.tools import retry_guard
    project = _project(root, "with-memo" if memo else "without-memo")
    executed: list = []
    _drive(monkeypatch, executed)
    if not memo:
        # The behaviour before the memo existed: the repeat is answered
        # by nothing, so the step runs again and pays again.
        monkeypatch.setattr(
            retry_guard, "repeated", lambda *a, **k: None)
    started = time.time()
    runner.run_pipeline(str(project), only=["scan"])
    runner.run_pipeline(str(project), only=["scan"])
    return time.time() - started, executed


def test_the_repeat_pays_the_preparation_once_not_twice(tmp_path, monkeypatch):
    with monkeypatch.context() as without:
        without_memo, ran_without = _pair(
            tmp_path, without, memo=False)
    with_memo, ran_with = _pair(tmp_path, monkeypatch, memo=True)

    # The defect: without the memo the step ran twice; with it, once.
    assert ran_without == ["scan", "scan"]
    assert ran_with == ["scan"]

    saved = without_memo - with_memo
    print(f"\nPERF-011 repeated-failure measurement: "
          f"preparation {PREPARATION_SECONDS:.1f}s per attempt")
    print(f"  before (no memo): {without_memo:.2f}s for two runs "
          f"({len(ran_without)} preparations)")
    print(f"  after  (memo):    {with_memo:.2f}s for two runs "
          f"({len(ran_with)} preparation)")
    print(f"  saved:            {saved:.2f}s "
          f"({PREPARATION_SECONDS:.1f}s of preparation not paid again)")

    assert saved >= PREPARATION_SECONDS * 0.5, (
        f"the repeat did not save the preparation: {saved:.2f}s")
