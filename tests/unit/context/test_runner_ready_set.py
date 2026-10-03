"""The runner dispatches only graph-ready work and keeps worker ledgers local."""
from __future__ import annotations

import json
from pathlib import Path

from library.processes.edit_video import run_pipeline as runner
from library.tools.breakpoints import Breakpoints
from library.tools import perf_ledger


def test_ready_set_releases_independent_post_rough_cut_work_in_graph_order():
    dag = runner.load_dag()
    selected = [
        "review_rough_cut", "plan_subtitles", "plan_transitions",
        "plan_vfx", "plan_sfx",
    ]

    ready = runner.ready_set(dag, selected, {"review_rough_cut"})

    assert ready == ["plan_subtitles", "plan_transitions", "plan_vfx"]
    assert "plan_sfx" not in ready
    assert runner.ready_set(
        dag, selected,
        {"review_rough_cut", "plan_transitions"},
    ) == ["plan_subtitles", "plan_vfx", "plan_sfx"]


def test_failed_branch_does_not_hold_or_release_an_independent_sibling():
    dag = {
        "nodes": [{"id": node} for node in
                  ("root", "failed", "dependent", "sibling")],
        "edges": [
            {"from": "root", "to": "failed"},
            {"from": "failed", "to": "dependent"},
            {"from": "root", "to": "sibling"},
        ],
    }

    ready = runner.ready_set(
        dag, ["root", "failed", "dependent", "sibling"],
        {"root"}, blocked={"failed"},
    )

    assert ready == ["sibling"]


def test_a_scoped_step_is_ready_when_its_parent_is_outside_the_run():
    dag = {
        "nodes": [{"id": node} for node in ("supplied", "consumer")],
        "edges": [{"from": "supplied", "to": "consumer"}],
    }

    assert runner.ready_set(dag, ["consumer"], set()) == ["consumer"]


def test_review_gates_keep_the_run_serial_until_the_pause_is_resolved():
    selected = ["first", "second"]
    no_gate = Breakpoints()
    gate = Breakpoints(steps=("first",))

    assert runner.parallel_workers_enabled(
        "agent", None, selected, no_gate)
    assert not runner.parallel_workers_enabled(
        "agent", None, selected, gate)
    assert not runner.parallel_workers_enabled(
        "agent", None, selected, no_gate, pending_gate=True)


def test_worker_span_rows_wait_for_the_coordinator_to_commit(tmp_path):
    project = str(tmp_path / "project")
    script = tmp_path / "step.py"
    repo_root = Path(__file__).resolve().parents[3]
    script.write_text(
        "import json, sys\n"
        f"sys.path.insert(0, {str(repo_root)!r})\n"
        "from library.tools import perf_ledger\n"
        "with perf_ledger.span('worker_span') as cost:\n"
        "    cost['calls'] = 1\n"
        "print(json.dumps({'result': 'ok'}))\n",
        encoding="utf-8",
    )

    result = runner._run_step_work(
        project, "worker", {"type": "deterministic", "entry": str(script),
                             "node": {}},
        {}, False, None, 30, "run-1",
    )

    shared_ledger = perf_ledger.ledger_path(project)
    assert result["success"]
    assert result["output"] == {"result": "ok"}
    assert [row.get("layer") for row in result["perf_rows"]
            if row.get("kind") == perf_ledger.SPAN] == ["worker_span"]
    assert not shared_ledger.exists()

    perf_ledger.commit_rows(project, result["perf_rows"])
    rows = perf_ledger.read_rows(project)
    assert [(row["kind"], row.get("layer")) for row in rows] == [
        (perf_ledger.SPAN, "worker_span"), (perf_ledger.CAPABILITY, None),
    ]
