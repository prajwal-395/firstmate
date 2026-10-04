"""The runner dispatches only graph-ready work and keeps worker ledgers local."""
from __future__ import annotations

import json
from concurrent.futures import Future
from pathlib import Path

import pytest

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


def test_producer_backed_predicate_keeps_readiness_without_a_data_edge():
    from library.tools import requirements

    dag = {
        "nodes": [{"id": node} for node in
                  ("review_rough_cut", "plan_subtitles")],
        "edges": [],
    }
    selected = ["review_rough_cut", "plan_subtitles"]
    readiness_edges = requirements.readiness_edges(
        selected, requirements.HAND_WRITTEN)

    assert ("review_rough_cut", "plan_subtitles") in readiness_edges
    assert runner.ready_set(
        dag, selected, set(), extra_edges=readiness_edges,
    ) == ["review_rough_cut"]
    assert runner.ready_set(
        dag, selected, {"review_rough_cut"}, extra_edges=readiness_edges,
    ) == ["plan_subtitles"]


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


def _finished(value=None, error=None):
    future = Future()
    if error is not None:
        future.set_exception(error)
    else:
        future.set_result(value)
    return future


def test_coordinator_selects_any_finished_worker_without_waiting_for_earlier_node():
    in_flight = {
        "slow": {"future": Future()},
        "fast": {"future": _finished("done")},
    }

    assert runner._next_completed_node(in_flight, ["slow", "fast"]) == "fast"


def test_simultaneous_completions_use_stable_dag_tie_order():
    def pick(completion_order):
        in_flight = {node: {"future": Future()}
                     for node in ("first", "second")}
        for node in completion_order:
            in_flight[node]["future"].set_result(node)
        return runner._next_completed_node(
            in_flight, ["first", "second"])

    assert pick(("first", "second")) == "first"
    assert pick(("second", "first")) == "first"


def test_failed_worker_blocks_dependents_while_finished_sibling_commits():
    dag = {
        "nodes": [{"id": node} for node in
                  ("root", "failed", "dependent", "sibling")],
        "edges": [
            {"from": "root", "to": "failed"},
            {"from": "failed", "to": "dependent"},
            {"from": "root", "to": "sibling"},
        ],
    }
    selected = ["root", "failed", "dependent", "sibling"]
    in_flight = {
        "failed": {"future": _finished(error=RuntimeError("failed"))},
        "sibling": {"future": _finished("sibling output")},
    }

    first = runner._next_completed_node(in_flight, selected)
    assert first == "failed"
    with pytest.raises(RuntimeError, match="failed"):
        in_flight.pop(first)["future"].result()

    ready = runner.ready_set(dag, selected, {"root"}, blocked={"failed"})
    assert ready == ["sibling"]
    assert runner._next_completed_node(in_flight, selected) == "sibling"
    committed = [in_flight.pop("sibling")["future"].result()]
    assert committed == ["sibling output"]
    assert "dependent" not in ready


def test_full_runner_commits_finished_workers_and_keeps_independent_siblings(
        tmp_path, monkeypatch):
    from library.tools import capability_outputs

    project = tmp_path / "parallel-project"
    project.mkdir()
    (project / "project.yaml").write_text(
        "name: Parallel Project\nslug: parallel-project\n",
        encoding="utf-8")
    (project / "pipeline_data.json").write_text(json.dumps({
        "project_folder": str(project),
        "preflight_completed": {},
        "edit_completed": {},
        "failed_steps": [],
        "capability_outputs": {},
    }), encoding="utf-8")

    step_refs = {
        "scan": "step_1_01_scan_project",
        "catalog": "step_1_02_catalog_footage",
        "speech_sequence": "step_2_02_speech_sequence",
        "color_grade": "step_5_01_color_grade",
        "audio_mix": "step_5_02_audio_mix",
    }
    dag = {
        "id": "edit_video",
        "nodes": [{
            "id": node_id,
            "name": node_id,
            "step_ref": f"steps/{step_refs[node_id]}",
        } for node_id in step_refs],
        "edges": [
            {"from": "scan", "to": "catalog", "data_mapping": {}},
            {"from": "scan", "to": "speech_sequence", "data_mapping": {}},
            {"from": "scan", "to": "color_grade", "data_mapping": {}},
            {"from": "color_grade", "to": "audio_mix", "data_mapping": {}},
        ],
    }
    manifests = {
        node_id: {
            "id": f"step_{node_id}",
            "interface": {
                "inputs": [],
                "outputs": [{"name": "result", "type": "string"}],
            },
            "classification": {"stage": "edit"},
        }
        for node_id in step_refs
    }

    class ControlledExecutor:
        def __init__(self):
            self.jobs = {}
            self.node_by_future = {}
            self.submitted = []

        def submit(self, function, *args):
            future = Future()
            node_id = args[1]
            self.jobs[future] = (function, args)
            self.node_by_future[future] = node_id
            self.submitted.append(node_id)
            return future

        def shutdown(self, wait=True):
            assert all(future.done() for future in self.jobs)

    executor_holder = {}

    def make_executor(**_kwargs):
        executor_holder["executor"] = ControlledExecutor()
        return executor_holder["executor"]

    completion_order = ["scan", "speech_sequence", "color_grade", "catalog"]
    executed = []
    events = []

    def complete_one(futures, return_when):
        executor = executor_holder["executor"]
        future = next(
            future for node_id in completion_order
            for future in futures
            if executor.node_by_future[future] == node_id
            and not future.done())
        function, args = executor.jobs[future]
        try:
            future.set_result(function(*args))
        except BaseException as exc:
            future.set_exception(exc)
        return {future}, set(futures) - {future}

    def implementation(step_dir):
        node_id = next(node_id for node_id, name in step_refs.items()
                       if Path(step_dir).name == name)
        return {
            "type": "deterministic",
            "entry": str(Path(step_dir) / "step.py"),
            "step_dir": Path(step_dir),
            "manifest": manifests[node_id],
        }

    def run_step(entry, _inputs):
        node_id = next(node_id for node_id, name in step_refs.items()
                       if Path(entry).parent.name == name)
        executed.append(node_id)
        if node_id == "color_grade":
            raise RuntimeError("controlled worker failure")
        return {"result": node_id}

    original_record = capability_outputs.record
    original_failure = runner._record_step_failure

    def record(state, node_id, output, capability_id=""):
        if node_id in step_refs:
            events.append(("committed", node_id))
        return original_record(state, node_id, output, capability_id)

    def record_failure(state, node_id, message):
        if node_id in step_refs:
            events.append(("failed", node_id))
        return original_failure(state, node_id, message)

    monkeypatch.setattr(runner, "load_dag", lambda: dag)
    monkeypatch.setattr(runner, "get_step_implementation", implementation)
    monkeypatch.setattr(runner, "run_deterministic_step", run_step)
    monkeypatch.setattr(runner, "apply_source_identity", lambda *_args: None)
    monkeypatch.setattr(
        runner.briefing_chat, "conduct_if_needed",
        lambda _project, state, *_args, **_kwargs: state)
    monkeypatch.setattr(runner, "ProcessPoolExecutor", make_executor)
    monkeypatch.setattr(runner, "wait", complete_one)
    monkeypatch.setattr(capability_outputs, "record", record)
    monkeypatch.setattr(runner, "_record_step_failure", record_failure)

    summary = runner.run_pipeline(str(project), full_auto="agent")

    assert summary["status"] == "FAILED"
    assert executed == ["scan", "speech_sequence", "color_grade", "catalog"]
    assert events == [
        ("committed", "scan"),
        ("committed", "speech_sequence"),
        ("failed", "color_grade"),
        ("committed", "catalog"),
    ]
    executor = executor_holder["executor"]
    assert executor.submitted == [
        "scan", "catalog", "speech_sequence", "color_grade"]
    assert "audio_mix" not in executor.submitted


def test_only_confirmed_unused_mappings_were_removed():
    repo = Path(__file__).resolve().parents[3]
    dag = runner.load_dag()
    edges = dag["edges"]

    def carries(source, target, key):
        return any(
            edge["from"] == source and edge["to"] == target
            and key in (edge.get("data_mapping") or {})
            for edge in edges)

    assert not carries("review_rough_cut", "plan_subtitles",
                       "rough_cut_review")
    assert not carries("speech_sequence", "plan_subtitles",
                       "speech_sequence")
    assert not carries("plan_vfx", "creative_cohesion",
                       "enhancement_spec")
    assert not carries("temporal_index", "compile_manifest",
                       "temporal_event_indices")
    assert carries("object_segmentation", "compile_manifest",
                   "matte_trigger")
    assert carries("object_segmentation", "compile_manifest",
                   "object_segmentation")

    def inputs(step_dir):
        manifest = json.loads((repo / "library" / "steps" / step_dir
                               / "manifest.json").read_text(encoding="utf-8"))
        return {item["name"]: item for item in
                manifest["interface"]["inputs"]}

    assert set(inputs("step_4_01_plan_subtitles")) == {
        "audio_spine", "project_fps", "brand_effect", "brand_style"}
    assert "enhancement_spec" not in inputs("step_5_03_creative_cohesion")
    compile_inputs = inputs("step_5_04_compile_manifest")
    assert "temporal_index" not in compile_inputs
    assert compile_inputs["matte_trigger"]["required"] is False
