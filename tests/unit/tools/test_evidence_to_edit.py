"""EVAL-04 runner: evidence is consulted, grounded, or honestly refused.

Every test names a defect the runner must not have: a cut placed where
no evidence is present, a fabricated human score, a reviewable output
outside the output root, a window past the end of its measured source,
or an in-flight scorer silently treated as measured.
"""
import json

import pytest

from library.tools import editorial_task_benchmark as benchmark
from library.tools import evidence_to_edit as runner


def _moment(moment_id="m1", source_id="LCATL0012", start=10.0, end=20.0):
    return {"id": moment_id, "source_id": source_id,
            "start_seconds": start, "end_seconds": end,
            "description": "proposed window", "evidence_ids": ["corpus:x"]}


def _brief(task_id="dialogue-multicam-cut",
           task_types=("dialogue_multicam",), moments=None):
    moments = moments if moments is not None else [_moment()]
    return {
        "id": task_id,
        "task_types": list(task_types),
        "brief": {
            "intent": "Cut a tight exchange grounded in measured evidence.",
            "expected_source_moments": moments,
            "cut_ranges": [
                {"source_id": moment["source_id"],
                 "start_seconds": moment["start_seconds"],
                 "end_seconds": moment["end_seconds"],
                 "moment_id": moment["id"],
                 "reason": "Keep the complete beat."}
                for moment in moments
            ],
            "coverage_requirements": [
                {"id": "cover", "moment_ids": [moment["id"] for moment in moments]}],
            "continuity_constraints": [
                {"id": "speaker-match", "rule": "Show the speaker."}],
            "preference_rubric": [
                {"id": "clarity", "description": "Clarity", "scale": [1, 5]}],
        },
    }


def _empty_model(model_id="model-test"):
    return {"id": model_id, "revision": 3, "assets": [], "clocks": [],
            "observations": [], "signals": []}


def _word_obs(obs_id, measurement_type, clock_id, start, end, lane):
    return {"id": obs_id, "asset_id": "a1",
            "provenance_id": f"prov-d-{lane}",
            "interval": {"clock_id": clock_id, "start": start, "end": end},
            "payload": {"evidence_contract": {
                "measurement_type": measurement_type,
                "freshness": "fresh", "coverage": "full"}}}


def _evidenced_model():
    return {"id": "model-evidenced", "revision": 7, "assets": [], "clocks": [],
            "observations": [
                _word_obs("obs-word-1", "speech.word", "c1",
                          10000, 15000, "m1"),
                _word_obs("obs-turn-1", "person.voice_turn", "c1",
                          12000, 18000, "m3b"),
            ],
            "signals": [
                {"id": "sig-1", "measurement_type": "speech",
                 "processing_status": "analyzed",
                 "interval": {"clock_id": "c1", "start": 12000, "end": 15000},
                 "samples": [{"tick": 13000}]},
            ]}


def _word_rows():
    return {"LCATL0012": [
        {"word": "hello", "source_start": 12.0, "source_end": 13.0},
        {"word": "world", "source_start": 13.0, "source_end": 14.0},
    ]}


def test_empty_model_records_all_four_sources_and_abstains(tmp_path):
    task = _brief()
    entry, diagnostics = runner.run_task(
        task, _empty_model(), split="calibration", run_name="run-a",
        output_root=tmp_path, source_durations={"LCATL0012": 288.955})

    run = entry["runs"][0]
    sources = {row["evidence_source"] for row in run["evidence_decisions"]}
    assert sources == set(runner.EVIDENCE_SOURCES)
    assert run["stages"]["evidence"] == "complete"
    assert run["stages"]["render"] == "incomplete"
    assert run["stages"]["verify"] == "incomplete"
    assert "metrics" not in run
    assert diagnostics["cuts"] == []
    assert diagnostics["abstentions"] != []
    assert diagnostics["graph_node_count"] == 0
    assert (tmp_path / run["reviewable_output"]).is_file()

    result = benchmark.validate_report({"tasks": [entry]}, output_root=tmp_path)
    assert result["valid"] is False
    assert any("metrics" in error for error in result["errors"])


def test_evidenced_model_grounds_cuts_inside_brief_ranges(tmp_path):
    task = _brief()
    entry, diagnostics = runner.run_task(
        task, _evidenced_model(), split="calibration", run_name="run-a",
        output_root=tmp_path, source_durations={"LCATL0012": 288.955},
        word_rows_by_source=_word_rows())

    run = entry["runs"][0]
    assert diagnostics["cuts"] != []
    for cut in diagnostics["cuts"]:
        assert 10.0 <= cut["start_seconds"] < cut["end_seconds"] <= 20.0
    assert run["stages"]["edit_graph"] == "complete"
    assert diagnostics["graph_node_count"] > 0
    graph = json.loads(
        (tmp_path / "dialogue-multicam-cut" / "run-a" / "edit-graph.json")
        .read_text(encoding="utf-8"))
    assert graph["node_count"] == diagnostics["graph_node_count"]
    assert any(node["node_type"] == "spine_block" for node in graph["nodes"])


def test_cuts_without_word_timings_never_become_speech_blocks(tmp_path):
    task = _brief()
    _, diagnostics = runner.run_task(
        task, _evidenced_model(), split="calibration", run_name="run-a",
        output_root=tmp_path, source_durations={"LCATL0012": 288.955},
        word_rows_by_source={})

    assert diagnostics["cuts"] != []
    assert diagnostics["graph_node_count"] == 0
    assert all(item["reason"].startswith("no measured word timings")
               for item in diagnostics["ungraphable"])


def test_visual_cuts_become_action_beats_without_word_timings(tmp_path):
    task = _brief(task_id="visual-action-object-cut",
                  task_types=("visual_action", "visual_object"),
                  moments=[_moment(moment_id="v1", source_id="IMG_1818",
                                  start=5.0, end=20.0)])
    model = {"id": "model-visual", "revision": 1, "assets": [], "clocks": [],
             "observations": [
                 _word_obs("obs-det-1", "object.detection", "c1",
                           5000, 20000, "m4"),
                 _word_obs("obs-vis-1", "object.visibility", "c1",
                           5000, 20000, "m4"),
             ],
             "signals": []}

    entry, diagnostics = runner.run_task(
        task, model, split="calibration", run_name="run-a",
        output_root=tmp_path, source_durations={"IMG_1818": 85.518},
        word_rows_by_source={})

    assert diagnostics["cuts"] != []
    assert diagnostics["ungraphable"] == []
    assert entry["runs"][0]["stages"]["edit_graph"] == "complete"
    graph = json.loads(
        (tmp_path / "visual-action-object-cut" / "run-a" / "edit-graph.json")
        .read_text(encoding="utf-8"))
    block_types = {node["payload"].get("block_type")
                   for node in graph["nodes"]
                   if node["node_type"] == "spine_block"}
    assert block_types == {"action"}


def test_render_and_verify_complete_only_when_supplied(tmp_path):
    task = _brief()
    render_rel = "dialogue-multicam-cut/run-a/render.mp4"
    render_file = tmp_path / render_rel
    render_file.parent.mkdir(parents=True, exist_ok=True)
    render_file.write_text("render bytes", encoding="utf-8")

    entry, _ = runner.run_task(
        task, _evidenced_model(), split="calibration", run_name="run-a",
        output_root=tmp_path, source_durations={"LCATL0012": 288.955},
        word_rows_by_source=_word_rows(), render_output=render_rel,
        verify_passed=True)

    run = entry["runs"][0]
    assert run["stages"]["render"] == "complete"
    assert run["stages"]["verify"] == "complete"
    assert run["reviewable_output"] == render_rel


def test_verify_without_render_stays_incomplete(tmp_path):
    task = _brief()

    entry, _ = runner.run_task(
        task, _evidenced_model(), split="calibration", run_name="run-a",
        output_root=tmp_path, source_durations={"LCATL0012": 288.955},
        word_rows_by_source=_word_rows(), verify_passed=True)

    run = entry["runs"][0]
    assert run["stages"]["render"] == "incomplete"
    assert run["stages"]["verify"] == "incomplete"


def test_missing_render_file_is_refused(tmp_path):
    task = _brief()

    with pytest.raises(ValueError, match="render output.*missing"):
        runner.run_task(
            task, _evidenced_model(), split="calibration", run_name="run-a",
            output_root=tmp_path, source_durations={"LCATL0012": 288.955},
            word_rows_by_source=_word_rows(),
            render_output="dialogue-multicam-cut/run-a/absent.mp4")


def test_queries_pin_to_the_moment_source_clock(tmp_path):
    model = {"id": "model-clocks", "revision": 1,
             "assets": [{"id": "SRC-A", "content_digest": "a"},
                        {"id": "SRC-B", "content_digest": "b"}],
             "clocks": [{"id": "clock-a", "domain": {"asset_id": "SRC-A"}},
                        {"id": "clock-b", "domain": {"asset_id": "SRC-B"}}],
             "observations": [
                 {"id": "obs-a", "asset_id": "SRC-A",
                  "provenance_id": "prov-a-m5",
                  "interval": {"clock_id": "clock-a", "start": 10000,
                               "end": 20000},
                  "payload": {"evidence_contract": {
                      "measurement_type": "audio.sound_event",
                      "freshness": "fresh", "coverage": "full"}}},
                 {"id": "obs-b", "asset_id": "SRC-B",
                  "provenance_id": "prov-b-m5",
                  "interval": {"clock_id": "clock-b", "start": 10000,
                               "end": 20000},
                  "payload": {"evidence_contract": {
                      "measurement_type": "audio.sound_event",
                      "freshness": "fresh", "coverage": "full"}}},
             ],
             "signals": []}
    task = _brief(task_id="visual-action-object-cut",
                  task_types=("visual_action", "visual_object"),
                  moments=[_moment("v1", "SRC-A", 10.0, 20.0)])

    entry, diagnostics = runner.run_task(
        task, model, split="calibration", run_name="run-a",
        output_root=tmp_path,
        source_durations={"SRC-A": 100.0, "SRC-B": 100.0})

    assert [cut["source_id"] for cut in diagnostics["cuts"]] == ["SRC-A"]
    sound_rows = [row for row in entry["runs"][0]["evidence_decisions"]
                  if row["decision"].startswith("query sound_energy")]
    assert sound_rows and all("obs-b" not in row["evidence_ids"]
                             for row in sound_rows)


def test_source_scoping_limits_moments_and_cuts(tmp_path):
    task = _brief(moments=[_moment("m1", "SRC-A", 10.0, 20.0),
                           _moment("m2", "SRC-B", 10.0, 20.0)])

    entry, diagnostics = runner.run_task(
        task, _empty_model(), split="calibration", run_name="run-a",
        output_root=tmp_path,
        source_durations={"SRC-A": 100.0, "SRC-B": 100.0},
        source_ids=["SRC-A"])

    assert diagnostics["scoped_sources"] == ["SRC-A"]
    assert len(entry["runs"][0]["evidence_decisions"]) < len(
        runner.run_task(task, _empty_model(), split="calibration",
                        run_name="run-b", output_root=tmp_path,
                        source_durations={"SRC-A": 100.0,
                                          "SRC-B": 100.0})[0]["runs"][0]
        ["evidence_decisions"])
    assert entry["brief"]["expected_source_moments"] != []


def test_window_past_measured_duration_is_refused():
    task = _brief(moments=[_moment(start=280.0, end=300.0)])

    with pytest.raises(ValueError, match="exceeds measured source duration"):
        runner.run_task(task, _empty_model(), split="calibration",
                        run_name="run-a", output_root=None,
                        source_durations={"LCATL0012": 288.955})


def test_unknown_source_duration_is_refused():
    task = _brief()

    with pytest.raises(ValueError, match="no measured duration"):
        runner.run_task(task, _empty_model(), split="calibration",
                        run_name="run-a", output_root=None,
                        source_durations={})


def test_reviewable_output_cannot_escape_the_root(tmp_path):
    with pytest.raises(ValueError, match="escapes the output root"):
        runner.write_reviewable_output(tmp_path, "task", "../../outside", {}, [],
                                       [])


def test_zero_budget_plans_but_never_executes_acquisition():
    rows, answer = runner.consult_moment(
        _empty_model(), _moment(), ["dialogue_multicam"],
        acquisition_budget_seconds=0.0, duration_seconds=288.955)

    acquisition_rows = [row for row in rows
                        if row["evidence_source"] == "bounded_acquisition"]
    assert acquisition_rows
    assert all("no_run" in row["decision"] or "exceeds" in row["rationale"]
               or "not declared" in row["rationale"]
               for row in acquisition_rows)
    assert answer["present_intervals"] == []


def test_frozen_brief_files_validate_and_fit_measured_durations():
    repo = __import__("pathlib").Path(__file__).resolve().parents[3]
    manifest = json.loads(
        (repo / "docs" / "benchmarks" / "2026-10-15-editorial-eval-corpus.json")
        .read_text(encoding="utf-8"))
    durations = {row["source_id"]: row["duration_seconds"]
                 for row in manifest["sources"]}
    briefs = [runner.load_brief(
        repo / "docs" / "benchmarks" / filename) for filename in (
            "eval04-brief-dialogue-multicam.json",
            "eval04-brief-visual-action-object.json")]

    assert [brief["id"] for brief in briefs] == [
        "dialogue-multicam-cut", "visual-action-object-cut"]
    assert briefs[1]["split"] == "held_out"
    assert runner.validate_briefs(briefs) == []
    assert [error for brief in briefs
            for error in runner.check_brief_windows(brief, durations)] == []
    task_types = {task_type for brief in briefs
                  for task_type in brief["task_types"]}
    assert task_types == set(benchmark.REQUIRED_TASK_TYPES)
    assert all(brief["brief"].get("gold_frozen") is False for brief in briefs)


def test_inflight_scorers_report_pending_without_guessed_values(tmp_path):
    status = runner.external_scorer_status(tmp_path)

    assert status["EVAL-02"]["state"] == "pending"
    assert status["EVAL-03"]["state"] == "pending"
