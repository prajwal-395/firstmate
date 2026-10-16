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
    model = {"id": "model-evidenced", "revision": 7, "assets": [], "clocks": [],
            "observations": [
                _word_obs("obs-word-1", "speech.word", "c1",
                          12000, 13000, "m1"),
                _word_obs("obs-turn-1", "person.voice_turn", "c1",
                          12000, 15000, "m3b"),
                _word_obs("obs-word-2", "speech.word", "c1",
                          16000, 17000, "m1"),
                _word_obs("obs-turn-2", "person.voice_turn", "c1",
                          15000, 18000, "m3b"),
            ],
            "signals": [
                {"id": "sig-1", "measurement_type": "speech",
                 "processing_status": "analyzed",
                 "interval": {"clock_id": "c1", "start": 12000, "end": 15000},
                 "samples": [{"tick": 13000}]},
            ]}

    model["observations"][0]["payload"]["evidence_contract"]["value"] = {
        "text": "question?"}
    model["observations"][1]["payload"]["evidence_contract"]["value"] = {
        "voice_track": "voice_001"}
    model["observations"][2]["payload"]["evidence_contract"]["value"] = {
        "text": "answer."}
    model["observations"][3]["payload"]["evidence_contract"]["value"] = {
        "voice_track": "voice_002"}
    return model


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
    cut_reason = diagnostics["cuts"][0]["reason"]
    assert cut_reason["creative_rationale"] == "Keep the complete beat."
    assert cut_reason["dialogue_exchange"] == {
        "speakers": ["voice_001", "voice_002"],
        "complete_turn_count": 2}
    assert cut_reason["camera_selection"]["status"] == "abstained"
    assert (diagnostics["cuts"][0]["start_seconds"],
            diagnostics["cuts"][0]["end_seconds"]) == (12.0, 18.0)
    assert cut_reason["evidence_ids"]
    assert {row["measurement_type"] for row in
            cut_reason["factual_evidence"]} == {
                "speech.word", "person.voice_turn"}
    assert cut_reason["technical_measurements"] == []


def test_project_model_word_measurements_build_speech_blocks_by_default(
        tmp_path):
    model = _evidenced_model()
    word = model["observations"][0]
    word["interval"].update(start=12000, end=13000)
    word["payload"]["evidence_contract"]["value"] = {"text": "hello"}

    entry, diagnostics = runner.run_task(
        _brief(), model, split="calibration", run_name="run-a",
        output_root=tmp_path, source_durations={"LCATL0012": 288.955})

    assert diagnostics["cuts"]
    assert diagnostics["ungraphable"] == []
    assert diagnostics["graph_node_count"] > 0
    assert entry["runs"][0]["stages"]["edit_graph"] == "complete"
    graph = json.loads(
        (tmp_path / "dialogue-multicam-cut" / "run-a" / "edit-graph.json")
        .read_text(encoding="utf-8"))
    block = next(node for node in graph["nodes"]
                 if node["node_type"] == "spine_block")
    assert block["payload"]["word_timestamp_count"] == 2


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


def test_single_speaker_turns_do_not_claim_a_complete_exchange(tmp_path):
    model = _evidenced_model()
    model["observations"][3]["payload"]["evidence_contract"]["value"][
        "voice_track"] = "voice_001"

    _, diagnostics = runner.run_task(
        _brief(), model, split="calibration", run_name="run-a",
        output_root=tmp_path, source_durations={"LCATL0012": 288.955})

    assert diagnostics["cuts"] == []
    assert diagnostics["abstentions"][0]["reason"].startswith(
        "no complete exchange")


def test_speaker_camera_requires_face_backed_speaking_spans():
    def measurement(evidence_id, measurement_type, value, start, end,
                    asset_id="cam-a"):
        return {"evidence_id": evidence_id, "measurement_type": measurement_type,
                "value": value, "asset_id": asset_id,
                "interval": {"clock_id": asset_id, "start": start,
                             "end": end}}

    answer = {"present_intervals": [{
        "plan": "speaker_camera", "measurements": [
            measurement("speak-a", "event.speaking_span",
                        {"voice_track": "voice_001", "face_track": "face_1"},
                        10000, 12000),
            measurement("face-a", "person.face_frame", {"face": {}},
                        11000, 11001),
            measurement("speak-b", "event.speaking_span",
                        {"voice_track": "voice_002", "face_track": "face_2"},
                        12000, 14000),
            measurement("face-b", "person.face_frame", {"face": {}},
                        13000, 13001),
        ]}]}

    mapping = runner._speaker_camera_mapping(
        answer, ["voice_001", "voice_002"])

    assert mapping == {"status": "mapped", "speaker_to_camera": {
        "voice_001": {"cam-a": {"face_track": "face_1",
                                  "evidence_ids": ["speak-a", "face-a"]}},
        "voice_002": {"cam-a": {"face_track": "face_2",
                                  "evidence_ids": ["speak-b", "face-b"]}}}}


def test_speaker_camera_exposes_lrasd_as_read_only_candidate():
    relation = {
        "subject_id": "entity-speaker-1",
        "predicate": "speaking_person_candidate",
        "object_id": "entity-visible-1",
        "interval": {"clock_id": "camera-clock", "start": 12000,
                     "end": 15000},
        "confidence": 0.73,
        "evidence_ids": ["obs-lrasd-1"],
        "status": "candidate",
    }
    answer = {
        "present_intervals": [],
        "speaker_camera_candidates": [relation],
        "clocks": [{"id": "camera-clock",
                    "domain": {"asset_id": "asset-camera"}}],
        "assets": [{"id": "asset-camera", "uri": "/camera-b.mxf"}],
    }

    result = runner._speaker_camera_mapping(answer, ["voice_001"])

    assert result["status"] == "candidate"
    assert result["speaker_to_camera_candidates"] == [{
        "speaker_entity_id": "entity-speaker-1",
        "speaker_track_id": None,
        "visible_person_entity_id": "entity-visible-1",
        "camera_asset_id": "asset-camera",
        "camera_uri": "/camera-b.mxf",
        "source_clock_id": "camera-clock",
        "source_start": 12000,
        "source_end": 15000,
        "candidate_status": "candidate",
        "raw_lr_asd_score": 0.73,
        "evidence_ids": ["obs-lrasd-1"],
    }]


def test_object_evidence_cannot_be_assumed_to_prove_an_action(tmp_path):
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

    assert diagnostics["cuts"] == []
    assert diagnostics["abstentions"][0]["required_plans"] == [
        ["action_drink_from_cup", "action_pick_up_put_down"], ["objects"]]
    assert entry["runs"][0]["stages"]["edit_graph"] == "incomplete"
    assert any(row["decision"].startswith("abstain on v1")
               for row in entry["runs"][0]["evidence_decisions"])


def test_visual_action_and_object_evidence_ground_only_verified_action(tmp_path):
    task = _brief(task_id="visual-action-object-cut",
                  task_types=("visual_action", "visual_object"),
                  moments=[_moment(moment_id="v1", source_id="IMG_1818",
                                  start=5.0, end=20.0)])
    model = {
        "id": "model-visual",
        "revision": 1,
        "assets": [{"id": "asset-1818", "uri": "/media/IMG_1818.MOV"}],
        "clocks": [{"id": "clock-1818", "domain": {
            "kind": "source", "asset_id": "asset-1818"}}],
        "observations": [
            {**_word_obs("obs-object", "object.detection", "clock-1818",
                         5000, 20000, "segmentation"),
             "asset_id": "asset-1818",
             "payload": {"evidence_contract": {
                 "measurement_type": "object.detection",
                 "freshness": "fresh", "coverage": "complete",
                 "value": {"label": "cup", "detector": "owlv2"}}}},
            {**_word_obs("obs-visibility", "object.visibility",
                         "clock-1818", 5000, 20000, "segmentation"),
             "asset_id": "asset-1818",
             "payload": {"evidence_contract": {
                 "measurement_type": "object.visibility",
                 "freshness": "fresh", "coverage": "complete",
                 "value": {"visible_ticks": [5000, 20000]}}}},
            {"id": "obs-action", "asset_id": "asset-1818",
             "provenance_id": "prov-d-m7",
             "interval": {"clock_id": "clock-1818", "start": 7000,
                           "end": 12000},
             "modality": "event_candidate",
             "payload": {"predicate": "pick_up_put_down",
                         "verified": True,
                         "evidence_contract": {
                             "measurement_type": "event.candidate_span",
                             "freshness": "fresh", "coverage": "complete"}}},
        ],
        "signals": [],
    }

    entry, diagnostics = runner.run_task(
        task, model, split="held_out", run_name="run-a",
        output_root=tmp_path, source_durations={"IMG_1818": 85.518})

    assert len(diagnostics["cuts"]) == 1
    queries = [row for row in entry["runs"][0]["evidence_decisions"]
               if row["evidence_source"] == "evidence_query"]
    assert any(row["decision"].startswith("query action_pick_up_put_down")
               and row["evidence_ids"] == ["obs-action"] for row in queries)
    assert any(row["decision"].startswith("query objects")
               and row["evidence_ids"] == ["obs-object", "obs-visibility"]
               for row in queries)


def test_source_scope_resolves_corpus_id_from_asset_uri():
    model = {
        "assets": [
            {"id": "asset-1818", "uri": "/media/IMG_1818.MOV"},
            {"id": "asset-1812", "uri": "/media/IMG_1812.MOV"},
        ],
        "clocks": [
            {"id": "clock-1818", "domain": {
                "kind": "source", "asset_id": "asset-1818"}},
            {"id": "clock-1812", "domain": {
                "kind": "source", "asset_id": "asset-1812"}},
        ],
    }

    assert runner._scope_for(model, _moment(source_id="IMG_1818")) == {
        "start_s": 10.0, "end_s": 20.0,
        "asset_id": "asset-1818", "clock_id": "clock-1818"}


def test_verified_drink_candidate_is_an_alternative_visual_action(tmp_path):
    task = _brief(task_id="visual-action-cut",
                  task_types=("visual_action",),
                  moments=[_moment(moment_id="v1", source_id="IMG_1818",
                                  start=5.0, end=20.0)])
    model = {
        "id": "model-action",
        "revision": 1,
        "assets": [{"id": "asset-1818", "uri": "/media/IMG_1818.MOV"}],
        "clocks": [{"id": "clock-1818", "domain": {
            "kind": "source", "asset_id": "asset-1818"}}],
        "observations": [{
            "id": "obs-drink", "asset_id": "asset-1818",
            "provenance_id": "prov-d-m7",
            "interval": {"clock_id": "clock-1818", "start": 7000,
                          "end": 12000},
            "modality": "event_candidate",
            "payload": {"predicate": "drink_from_cup", "verified": True,
                        "evidence_contract": {
                            "measurement_type": "event.candidate_span",
                            "freshness": "fresh", "coverage": "complete"}},
        }],
        "signals": [],
    }

    _, diagnostics = runner.run_task(
        task, model, split="held_out", run_name="run-a",
        output_root=tmp_path, source_durations={"IMG_1818": 85.518})

    assert diagnostics["cuts"]


def test_cut_point_verification_uses_source_clock_signal_and_is_written(tmp_path):
    model = _evidenced_model()
    model["signals"].append({
        "id": "sig-phoneme-source-a", "measurement_type": "phoneme",
        "processing_status": "analyzed",
        "interval": {"clock_id": "c1", "start": 11000, "end": 16000},
        "samples": [{"tick": 13000}],
    })
    model["signals"].append({
        "id": "sig-phoneme-source-b", "measurement_type": "phoneme",
        "processing_status": "analyzed",
        "interval": {"clock_id": "other-clock", "start": 11000,
                      "end": 16000},
        "samples": [{"tick": 12500}],
    })

    _, diagnostics = runner.run_task(
        _brief(), model, split="calibration", run_name="run-a",
        output_root=tmp_path, source_durations={"LCATL0012": 288.955},
        word_rows_by_source=_word_rows())

    verification = diagnostics["cuts"][0]["reason"][
        "edit_point_verification"]
    assert verification == {"status": "verified", "tick_seconds": 13.0,
                            "measurement_ids": ["sig-1",
                                                "sig-phoneme-source-a"]}
    timeline = json.loads(
        (tmp_path / "dialogue-multicam-cut" / "run-a" / "edit.otio.json")
        .read_text(encoding="utf-8"))
    assert timeline["tracks"][0]["clips"][0]["reason"][
        "edit_point_verification"] == verification


def test_visual_object_cut_carries_measurements_separate_from_rationale(tmp_path):
    task = _brief(task_id="visual-object-cut", task_types=("visual_object",),
                  moments=[_moment(moment_id="v1", source_id="IMG_1818",
                                   start=5.0, end=20.0)])
    model = {"id": "model-object", "revision": 1, "assets": [], "clocks": [],
             "observations": [
                 _word_obs("obs-det-1", "object.detection", "c1",
                           5000, 20000, "m4"),
                 _word_obs("obs-vis-1", "object.visibility", "c1",
                           5000, 20000, "m4"),
             ], "signals": []}
    for observation in model["observations"]:
        observation["payload"]["evidence_contract"]["value"] = {
            "confidence": 0.91, "entity_id": "object-1"}

    _, diagnostics = runner.run_task(
        task, model, split="calibration", run_name="run-a",
        output_root=tmp_path, source_durations={"IMG_1818": 85.518})

    assert diagnostics["cuts"]
    reason = diagnostics["cuts"][0]["reason"]
    assert reason["evidence_ids"] == ["obs-det-1", "obs-vis-1"]
    assert reason["creative_rationale"] == "Keep the complete beat."
    assert {row["measurement_type"] for row in
            reason["technical_measurements"]} == {
                "object.detection", "object.visibility"}


def test_sound_energy_alone_cannot_justify_a_visual_cut(tmp_path):
    task = _brief(task_id="visual-object-cut", task_types=("visual_object",),
                  moments=[_moment(moment_id="v1", source_id="IMG_1818",
                                   start=5.0, end=20.0)])
    model = {"id": "model-sound", "revision": 1, "assets": [], "clocks": [],
             "observations": [_word_obs("obs-sound", "audio.sound_event",
                                         "c1", 5000, 20000, "m5")],
             "signals": []}

    _, diagnostics = runner.run_task(
        task, model, split="calibration", run_name="run-a",
        output_root=tmp_path, source_durations={"IMG_1818": 85.518})

    assert diagnostics["cuts"] == []
    assert diagnostics["abstentions"]
    assert diagnostics["abstentions"][0]["required_plans"] == [["objects"]]


def test_combined_selection_requires_required_evidence_to_overlap():
    task = _brief(task_id="visual-action-object-cut",
                  task_types=("visual_action", "visual_object"),
                  moments=[_moment("v1", "IMG_1818", 5.0, 20.0)])
    answer = {"present_intervals": [
        {"plan": "action", "start_seconds": 5.0, "end_seconds": 7.0,
         "evidence_ids": ["obs-action"], "measurements": [{"value": {}}]},
        {"plan": "objects", "start_seconds": 7.0, "end_seconds": 9.0,
         "evidence_ids": ["obs-object"], "measurements": [{"value": {}}]},
    ]}

    cuts, abstentions = runner.ground_cuts(
        task, task["task_types"], {"v1": answer}, [])

    assert cuts == []
    assert abstentions[0]["reason"].endswith(
        "no shared interval in the brief range")


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

    assert diagnostics["cuts"] == []
    assert diagnostics["abstentions"][0]["required_plans"] == [
        ["action_drink_from_cup", "action_pick_up_put_down"], ["objects"]]
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


def _face_row(evidence_id, asset_id, start_ms, end_ms, box):
    face = None if box is None else {"box": list(box)}
    return {"evidence_id": evidence_id,
            "measurement_type": "person.face_frame",
            "value": {"face_index": 0, "face": face, "hands": []},
            "asset_id": asset_id,
            "interval": {"clock_id": asset_id, "start": start_ms,
                         "end": end_ms}}


def _span_row(evidence_id, asset_id, start_ms, end_ms, voice, face):
    return {"evidence_id": evidence_id,
            "measurement_type": "event.speaking_span",
            "value": {"voice_track": voice, "face_track": face},
            "asset_id": asset_id,
            "interval": {"clock_id": asset_id, "start": start_ms,
                         "end": end_ms}}


def _word_row(evidence_id, start_ms, end_ms, text="hello"):
    return {"evidence_id": evidence_id, "measurement_type": "speech.word",
            "value": {"text": text}, "asset_id": "cam-a",
            "interval": {"start": start_ms, "end": end_ms}}


def _turn_row(evidence_id, start_ms, end_ms, voice, clock_id=None):
    interval = {"start": start_ms, "end": end_ms}
    if clock_id is not None:
        interval["clock_id"] = clock_id
    return {"evidence_id": evidence_id,
            "measurement_type": "person.voice_turn",
            "value": {"voice_track": voice}, "asset_id": "cam-a",
            "interval": interval}


def _sync_row(evidence_id, clock_id, offset):
    return {"evidence_id": evidence_id,
            "measurement_type": "sync.multicam_offset",
            "value": {"offset_to_reference_seconds": offset},
            "asset_id": "cam-b",
            "interval": {"clock_id": clock_id, "start": 0, "end": 20000}}


def test_face_geometry_rejects_anything_that_is_not_a_measurement():
    assert runner._face_geometry({"face": None}) is None
    assert runner._face_geometry({"face": {"box": [0.5, 0.2, 0.4, 0.5]}}) is None
    assert runner._face_geometry({"face": {"box": [-0.1, 0.2, 0.5, 0.5]}}) is None
    assert runner._face_geometry({"face": {"box": "wide"}}) is None
    assert runner._face_geometry("close-up") is None

    width, centre = runner._face_geometry(
        {"face": {"box": [0.5, 0.2, 0.65, 0.5]}})
    assert (round(width, 3), round(centre, 3)) == (0.15, 0.575)


def test_face_scale_boundaries_are_pinned():
    assert runner._face_scale(0.12) == "close"
    assert runner._face_scale(0.119) == "medium"
    assert runner._face_scale(0.04) == "medium"
    assert runner._face_scale(0.039) == "wide"


def test_framing_with_too_few_samples_stays_unknown():
    answer = {"present_intervals": [{
        "plan": "speaker_camera", "measurements": [
            _face_row("f1", "cam-a", 12100, 12200,
                      [0.5, 0.2, 0.65, 0.5]),
            _face_row("f2", "cam-a", 12500, 12600,
                      [0.5, 0.2, 0.65, 0.5]),
        ]}]}

    framing = runner.measure_camera_framing(answer, 12.0, 18.0)

    assert framing["cameras"]["cam-a"]["status"] == "unknown"
    assert "face_scale" not in framing["cameras"]["cam-a"]


def test_framing_measures_absence_instead_of_inferring_presence():
    answer = {"present_intervals": [{
        "plan": "speaker_camera", "measurements": [
            _face_row(f"f{i}", "cam-a", 12100 + i * 400, 12200 + i * 400,
                      None) for i in range(4)]}]}

    framing = runner.measure_camera_framing(answer, 12.0, 18.0)

    camera = framing["cameras"]["cam-a"]
    assert camera["status"] == "measured"
    assert camera["face_scale"] == "no_face"
    assert camera["face_fraction"] == 0.0


def test_framing_reports_median_scale_side_and_provenance():
    faces = [_face_row(f"a{i}", "cam-a", 12100 + i * 400, 12200 + i * 400,
                       [0.5, 0.2, 0.65, 0.5]) for i in range(4)]
    faces += [_face_row(f"b{i}", "cam-b", 15000 + i * 400, 15100 + i * 400,
                        [0.3, 0.2, 0.36, 0.5]) for i in range(4)]
    faces.append(_face_row("orphan", None, 13000, 13100,
                           [0.5, 0.2, 0.65, 0.5]))
    answer = {"present_intervals": [
        {"plan": "speaker_camera", "measurements": faces}]}

    framing = runner.measure_camera_framing(answer, 12.0, 18.0)

    assert framing["cameras"]["cam-a"]["face_scale"] == "close"
    assert framing["cameras"]["cam-a"]["subject_position"] == "right"
    assert framing["cameras"]["cam-b"]["face_scale"] == "medium"
    assert framing["cameras"]["cam-b"]["subject_position"] == "left"
    assert "None" not in framing["cameras"]
    assert len(framing["measurements"]) == 8
    assert "orphan" not in framing["evidence_ids"]


def test_framing_compatibility_grades_every_pair_shape():
    def measured(scale, side):
        return {"status": "measured", "face_scale": scale,
                "subject_position": side, "evidence_ids": ["f1"]}

    framing = {"cameras": {
        "cam-a": measured("close", "right"),
        "cam-b": measured("medium", "left"),
        "cam-c": measured("close", "right"),
        "cam-d": measured("no_face", "unclear"),
        "cam-e": {"status": "unknown", "evidence_ids": []},
    }, "evidence_ids": ["f1"]}

    verdicts = {(row["camera_a"], row["camera_b"]): row["verdict"]
                for row in runner.framing_compatibility(framing)["pairs"]}

    assert verdicts[("cam-a", "cam-b")] == "compatible"
    assert verdicts[("cam-a", "cam-c")] == "same_framing"
    assert verdicts[("cam-a", "cam-d")] == "incompatible"
    assert verdicts[("cam-a", "cam-e")] == "unknown"


def test_camera_preferences_are_validated_not_half_read():
    assert runner.normalize_camera_preferences(None) == {
        "status": "no_preference"}
    assert runner.normalize_camera_preferences({}) == {
        "status": "no_preference"}
    assert runner.normalize_camera_preferences(
        {"preferred_camera": "cam-b"}) == {
            "status": "prefer", "preferred_camera": "cam-b",
            "avoid_cameras": []}

    with pytest.raises(TypeError, match="must be an object"):
        runner.normalize_camera_preferences("cam-b")
    with pytest.raises(ValueError, match="mode"):
        runner.normalize_camera_preferences({"mode": "always"})
    with pytest.raises(ValueError, match="avoid_cameras"):
        runner.normalize_camera_preferences({"avoid_cameras": "cam-b"})


def _switch_fixture():
    words = [_word_row("w1", 12000, 12400), _word_row("w2", 13000, 13400),
             _word_row("w3", 14600, 15000), _word_row("w4", 16000, 16400)]
    exchange = {
        "speakers": ["voice_001", "voice_002"],
        "evidence": [{"evidence_id": "w1"}, "w2"],
        "turns": [{"speaker": "voice_001", "start": 12.0, "end": 14.0},
                  {"speaker": "voice_002", "start": 14.6, "end": 17.0,
                   "clock_id": "cam-b"}],
    }
    answer = {"present_intervals": [
        {"plan": "speech_attributed", "measurements": words},
        {"plan": "multicam_sync",
         "measurements": [_sync_row("sync-b", "cam-b", -0.3)]}],
        "assets": [{"id": "cam-b", "uri": "/media/cam-b.mxf"}]}
    framing = {"cameras": {
        "cam-a": {"status": "measured", "face_scale": "close",
                  "subject_position": "right", "evidence_ids": ["fa"]},
        "cam-b": {"status": "measured", "face_scale": "medium",
                  "subject_position": "left", "evidence_ids": ["fb"]},
    }, "evidence_ids": ["fa", "fb"]}
    mapped = {"status": "mapped", "speaker_to_camera": {
        "voice_001": {"cam-a": {"face_track": "face_1",
                                "evidence_ids": ["s1"]}},
        "voice_002": {"cam-b": {"face_track": "face_2",
                                "evidence_ids": ["s2"]}}}}
    return exchange, answer, framing, mapped


def test_switch_plan_names_the_mapped_camera_inside_the_word_gap():
    exchange, answer, framing, mapped = _switch_fixture()

    switches = runner.plan_camera_switches(
        exchange, answer, mapped, framing,
        {"status": "no_preference"}, 12.0, 18.0)

    assert len(switches) == 1
    switch = switches[0]
    assert switch["recommended_camera"] == "/media/cam-b.mxf"
    assert switch["recommended_camera_asset_id"] == "cam-b"
    assert (switch["safe_switch_start_seconds"],
            switch["safe_switch_end_seconds"]) == (13.4, 14.6)
    assert switch["reference_change_seconds"] == 14.3
    assert switch["reference_window"] == [13.1, 14.3]
    assert switch["framing_verdict"] == "compatible"
    assert switch["confidence"] == "high"
    assert {"w1", "w2", "fb"} <= set(switch["evidence_ids"])
    continuity = switch["continuity"]
    assert continuity["subject_correct"] is True
    assert continuity["dialogue_continuous"] is True
    assert continuity["eyeline_continuous"] is None
    assert continuity["axis_continuous"] is None
    assert continuity["framing_compatible"] is True


def test_switch_plan_never_spends_an_unverified_candidate():
    exchange, answer, framing, _ = _switch_fixture()
    candidate = {"status": "candidate", "speaker_to_camera_candidates": []}

    switches = runner.plan_camera_switches(
        exchange, answer, candidate, framing,
        {"status": "no_preference"}, 12.0, 18.0)

    assert switches[0]["recommended_camera"] == "no_switch"
    assert "candidate" in switches[0]["no_switch_reason"]
    assert switches[0]["continuity"]["subject_correct"] is None


def test_switch_plan_refuses_a_gap_that_would_clip_a_word():
    exchange, answer, framing, mapped = _switch_fixture()
    answer["present_intervals"][0]["measurements"] = [
        _word_row("w1", 12000, 14500), _word_row("w3", 14600, 15000)]

    switches = runner.plan_camera_switches(
        exchange, answer, mapped, framing,
        {"status": "no_preference"}, 12.0, 18.0)

    assert switches[0]["recommended_camera"] == "no_switch"
    assert "clipped word" in switches[0]["no_switch_reason"]


def test_switch_plan_honours_a_required_camera_avoidance():
    exchange, answer, framing, mapped = _switch_fixture()
    required = {"status": "require", "preferred_camera": None,
                "avoid_cameras": ["cam-b"]}

    switches = runner.plan_camera_switches(
        exchange, answer, mapped, framing, required, 12.0, 18.0)

    assert switches[0]["recommended_camera"] == "no_switch"
    assert "avoided" in switches[0]["no_switch_reason"]


def test_switch_plan_refuses_a_target_angle_with_no_face():
    exchange, answer, framing, mapped = _switch_fixture()
    framing["cameras"]["cam-b"] = {
        "status": "measured", "face_scale": "no_face",
        "subject_position": "unclear", "evidence_ids": ["fb"]}

    switches = runner.plan_camera_switches(
        exchange, answer, mapped, framing,
        {"status": "no_preference"}, 12.0, 18.0)

    assert switches[0]["recommended_camera"] == "no_switch"
    assert "no face" in switches[0]["no_switch_reason"]


def test_switch_score_reports_timing_and_subject_hits():
    predicted = [
        {"speaker_change_seconds": 14.6, "recommended_camera": "/cam-b.mxf"},
        {"speaker_change_seconds": 30.0, "recommended_camera": "/cam-a.mxf"},
    ]
    reference = {"switches": [
        {"speaker_change_seconds": 14.7,
         "safe_switch_start_seconds": 13.4,
         "safe_switch_end_seconds": 14.8,
         "recommended_camera": "/cam-b.mxf"},
        {"speaker_change_seconds": 40.0,
         "safe_switch_start_seconds": 39.5,
         "safe_switch_end_seconds": 40.5,
         "recommended_camera": "/cam-b.mxf"},
    ]}

    score = runner.score_multicam_switches(predicted, reference)

    assert score["status"] == "scored"
    assert score["timing_hits"] == 1
    assert score["timing_accuracy"] == 0.5
    assert score["subject_correct_count"] == 1
    assert score["mean_abs_timing_error_s"] == 5.05
    assert score["unmatched_reference_count"] == 0


def test_switch_score_says_unscorable_on_window_style_edits():
    assert runner.score_multicam_switches([], None)["status"] == "unscorable"
    assert runner.score_multicam_switches(
        [], {"cuts": [{"source_id": "LCATL0012"}]})["status"] == "unscorable"
    assert runner.score_multicam_switches([], [])["reason"].endswith(
        "switch list is empty")


def test_ground_cuts_carries_framing_switches_and_preferences():
    words = [_word_row("w1", 12000, 12400, "hello"),
             _word_row("w2", 13000, 13400, "there"),
             _word_row("w3", 14600, 15000, "yes"),
             _word_row("w4", 16000, 16400, "indeed")]
    turns = [_turn_row("t1", 12000, 14000, "voice_001"),
             _turn_row("t2", 14600, 17000, "voice_002")]
    faces = [_face_row(f"a{i}", "cam-a", 12100 + i * 400, 12200 + i * 400,
                       [0.5, 0.2, 0.65, 0.5]) for i in range(4)]
    faces += [_face_row(f"b{i}", "cam-b", 15000 + i * 400, 15100 + i * 400,
                        [0.3, 0.2, 0.36, 0.5]) for i in range(4)]
    spans = [_span_row("s1", "cam-a", 12000, 14000, "voice_001", "face_1"),
             _span_row("s2", "cam-b", 14600, 17000, "voice_002", "face_2")]
    answer = {
        "present_intervals": [
            {"plan": "speech_attributed", "clock_id": "cam-a",
             "start_seconds": 10.0,
             "end_seconds": 20.0,
             "evidence_ids": [row["evidence_id"] for row in words + turns],
             "measurements": words + turns},
            {"plan": "speaker_camera", "clock_id": "cam-a",
             "start_seconds": 10.0,
             "end_seconds": 20.0,
             "evidence_ids": [row["evidence_id"] for row in faces + spans],
             "measurements": faces + spans},
        ],
        "assets": [{"id": "cam-a", "uri": "/media/cam-a.mxf"},
                   {"id": "cam-b", "uri": "/media/cam-b.mxf"}],
    }
    task = _brief()
    task["brief"]["camera_preferences"] = {"preferred_camera": "cam-b"}

    cuts, abstentions = runner.ground_cuts(
        task, task["task_types"], {"m1": answer}, [])

    assert abstentions == []
    assert len(cuts) == 1
    reason = cuts[0]["reason"]
    assert reason["camera_selection"]["status"] == "mapped"
    assert reason["framing"]["cameras"]["cam-a"]["face_scale"] == "close"
    assert reason["framing"]["cameras"]["cam-b"]["face_scale"] == "medium"
    assert len(reason["switch_plan"]) == 1
    assert reason["switch_plan"][0][
        "recommended_camera_asset_id"] == "cam-b"
    assert reason["camera_preferences"] == {
        "status": "prefer", "preferred_camera": "cam-b",
        "avoid_cameras": []}
    assert {row["measurement_type"] for row in
            reason["technical_measurements"]} == {"person.face_frame",
                                                 "event.speaking_span"}


def test_ground_cuts_refuses_a_malformed_camera_preference():
    task = _brief()
    task["brief"]["camera_preferences"] = {"mode": "always"}
    answer = {"present_intervals": []}

    with pytest.raises(ValueError, match="mode"):
        runner.ground_cuts(task, task["task_types"], {"m1": answer}, [])
