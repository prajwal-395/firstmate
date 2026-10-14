"""FI-05: typed interval query over the Project Model.

Every project here is a hand-made Project Model dict under `tmp_path` -
no real project, no footage, no model (§8). Native evidence payloads
are built through `evidence_contract.new_measurement`, so the fixtures
carry the real envelope rather than a faked shape. The row's core
demand - empty/unanalysed and measured-absent yield DIFFERENT states -
is pinned by `test_empty_and_measured_absent_differ`. Every test fails
on main: `library.tools.evidence_query` does not exist there.
"""
import json
from dataclasses import replace
from pathlib import Path

import pytest

from library.tools import evidence_contract, evidence_query
from library.tools.analysis.footage_query import FootageIndex
from library.tools.evidence_query import (
    CONFIDENTLY_ABSENT,
    INCONCLUSIVE,
    NOT_YET_ANALYSED,
    PRESENT,
)

D1 = "digest-fi05-with-speech"
D2 = "digest-fi05-silent"


def _native(measurement_type, value, *, digest, lane, start_tick=0,
            end_tick=20000, coverage="complete", samples=1,
            freshness="fresh", method="event-spans-v1 over M3+M3b"):
    record = evidence_contract.new_measurement(
        asset_id=f"asset-{digest}", clock_id=f"clock-{digest}",
        start_tick=start_tick, end_tick=end_tick,
        measurement_type=measurement_type, value=value, method=method,
        provenance_id=f"prov-{digest}-{lane}",
        coverage=evidence_contract.CoverageKind(coverage),
        coverage_samples=samples, coverage_sample_rate_hz=0.0)
    return evidence_contract.to_rust_evidence(
        replace(record, freshness=freshness))


def _obs(obs_id, modality, payload, *, digest, start_s=0.0, end_s=20.0,
         lane="m7", native=None):
    obs = {"id": obs_id, "asset_id": f"asset-{digest}",
           "interval": {"clock_id": f"clock-{digest}",
                        "start": int(start_s * 1000),
                        "end": int(end_s * 1000)},
           "modality": modality, "payload": dict(payload),
           "confidence": None, "provenance_id": f"prov-{digest}-{lane}"}
    if native is not None:
        obs["payload"]["evidence_contract"] = native
    return obs


def _model(*observations):
    return {"id": "project-fixture", "schema_version": 1,
            "clocks": [
                {"id": f"clock-{digest}", "ticks_per_second": 1000}
                for digest in (D1, D2)],
            "assets": [{"id": f"asset-{digest}", "content_digest": digest}
                       for digest in (D1, D2)],
            "entities": [], "observations": list(observations),
            "identity_links": [], "tracks": [],
            "provenance": [
                {"id": f"prov-{digest}-{lane}", "producer": "fixture"}
                for digest in (D1, D2) for lane in ("m1", "m7")],
            "clip_uses": [], "renders": [], "frame_maps": []}


def _speaking_model():
    return _model(_obs(
        "obs-speaking-1", "event_span",
        {"predicate": "speaking", "voice_track": "v1",
         "face_track": "f1"},
        digest=D1, start_s=1.0, end_s=3.0,
        native=_native("event.speaking_span",
                       {"predicate": "speaking"}, digest=D1, lane="m7",
                       start_tick=1000, end_tick=3000)))


def _speaking_plan(**overrides):
    predicate = {"predicate": "event_span", "event": "speaking"}
    predicate.update(overrides)
    return evidence_query.compile_plan([predicate])


# ─── The row's core: present evidence ────────────────────────────


def test_speaking_span_is_present_with_interval_provenance_coverage():
    answer = evidence_query.evidence_for(_speaking_model(), _speaking_plan())
    assert answer["status"] == PRESENT
    assert len(answer["results"]) == 1
    result = answer["results"][0]
    assert result["interval"] == {"clock_id": f"clock-{D1}", "start": 1000,
                                 "end": 3000, "start_s": 1.0, "end_s": 3.0}
    assert result["evidence_ids"] == ["obs-speaking-1"]
    assert result["provenance_ids"] == [f"prov-{D1}-m7"]
    assert result["coverage"] == ["complete"]
    assert result["freshness"] == ["fresh"]
    assert answer["required_missing_lanes"] == []


# ─── The row's core: absent means measured-absent ────────────────


def _silent_model():
    return _model(_obs(
        "obs-silence-1", "event_span",
        {"predicate": "speaking", "voice_track": None,
         "face_track": None, "spans": []},
        digest=D2, start_s=0.0, end_s=20.0,
        native=_native("event.speaking_span", [], digest=D2, lane="m7",
                       start_tick=0, end_tick=20000,
                       coverage="complete", samples=0)))


def test_measured_absence_is_confidently_absent():
    answer = evidence_query.evidence_for(_silent_model(), _speaking_plan())
    assert answer["status"] == CONFIDENTLY_ABSENT
    assert answer["results"] == []
    assert answer["required_missing_lanes"] == []
    assert any(basis["lane"] == "m7"
               for basis in answer["completeness_basis"])


def test_lane_that_never_ran_is_not_yet_analysed():
    answer = evidence_query.evidence_for(_model(), _speaking_plan())
    assert answer["status"] == NOT_YET_ANALYSED
    assert answer["required_missing_lanes"] == ["m7"]


def test_empty_and_measured_absent_differ():
    unanalysed = evidence_query.evidence_for(_model(), _speaking_plan())
    absent = evidence_query.evidence_for(_silent_model(), _speaking_plan())
    assert unanalysed["status"] == NOT_YET_ANALYSED
    assert absent["status"] == CONFIDENTLY_ABSENT
    assert unanalysed["status"] != absent["status"], (
        "an empty lane and a measured-absent lane must never share a "
        "state: one may still be analysed, the other was looked at")


def test_four_states_match_the_rust_query_result_vocabulary():
    assert sorted(evidence_query.QUERY_STATES) == sorted(
        ["present", "confidently_absent", "not_yet_analysed",
         "inconclusive"])


# ─── Candidates, staleness and legacy coverage ───────────────────


def test_unverified_candidate_is_inconclusive_never_present():
    model = _model(_obs(
        "obs-candidate-1", "event_candidate",
        {"predicate": "pointing", "face_track": "f1", "verdicts": [],
         "verified": False},
        digest=D1, start_s=4.0, end_s=5.0,
        native=_native("event.candidate_span", {"predicate": "pointing"},
                       digest=D1, lane="m7", start_tick=4000,
                       end_tick=5000)))
    plan = evidence_query.compile_plan(
        [{"predicate": "event_span", "event": "pointing"}])
    answer = evidence_query.evidence_for(model, plan)
    assert answer["status"] == INCONCLUSIVE
    assert answer["results"] == []
    assert any("never answers" in reason for reason in answer["reasons"])


def test_verified_candidate_answers():
    model = _model(_obs(
        "obs-candidate-2", "event_candidate",
        {"predicate": "pointing", "face_track": "f1",
         "verdicts": [{"answer": "yes"}], "verified": True},
        digest=D1, start_s=4.0, end_s=5.0,
        native=_native("event.candidate_span", {"predicate": "pointing"},
                       digest=D1, lane="m7", start_tick=4000,
                       end_tick=5000)))
    plan = evidence_query.compile_plan(
        [{"predicate": "event_span", "event": "pointing"}])
    answer = evidence_query.evidence_for(model, plan)
    assert answer["status"] == PRESENT
    assert answer["results"][0]["evidence_ids"] == ["obs-candidate-2"]


def test_stale_evidence_is_inconclusive():
    model = _model(_obs(
        "obs-stale-1", "event_span",
        {"predicate": "speaking", "voice_track": "v1",
         "face_track": "f1"},
        digest=D1, start_s=1.0, end_s=3.0,
        native=_native("event.speaking_span", {"predicate": "speaking"},
                       digest=D1, lane="m7", start_tick=1000,
                       end_tick=3000, freshness="stale")))
    answer = evidence_query.evidence_for(model, _speaking_plan())
    assert answer["status"] == INCONCLUSIVE
    assert answer["results"] == []
    assert any("stale" in reason for reason in answer["reasons"])


def test_legacy_observation_can_present_but_never_absent():
    matching = _model(_obs(
        "obs-legacy-1", "event_span",
        {"predicate": "speaking", "voice_track": "v1",
         "face_track": "f1"},
        digest=D1, start_s=1.0, end_s=3.0))
    assert evidence_query.evidence_for(
        matching, _speaking_plan())["status"] == PRESENT
    # A legacy record with no match carries no coverage, so the lane
    # reads inconclusive rather than confidently absent.
    other = _model(_obs(
        "obs-legacy-2", "event_span",
        {"predicate": "on_screen", "face_track": "f1"},
        digest=D1, start_s=1.0, end_s=3.0))
    answer = evidence_query.evidence_for(other, _speaking_plan())
    assert answer["status"] == INCONCLUSIVE


def test_lane_that_ran_but_migrated_nothing_is_inconclusive():
    plan = _speaking_plan()
    without_hook = evidence_query.evidence_for(_model(), plan)
    assert without_hook["status"] == NOT_YET_ANALYSED
    presence = {(D1, "m7"): {"present": True, "freshness": "fresh"}}
    with_hook = evidence_query.evidence_for(
        _model(), plan, lane_presence=presence)
    assert with_hook["status"] == INCONCLUSIVE
    assert any("never migrated" in reason or "migrated nothing" in reason
               for reason in with_hook["reasons"])


# ─── Interval joins ──────────────────────────────────────────────


def _sound_model():
    return _model(
        _obs("obs-speaking-1", "event_span",
             {"predicate": "speaking", "voice_track": "v1",
              "face_track": "f1"},
             digest=D1, start_s=1.0, end_s=3.0,
             native=_native("event.speaking_span",
                            {"predicate": "speaking"}, digest=D1, lane="m7",
                            start_tick=1000, end_tick=3000)),
        _obs("obs-sound-1", "sound_event",
             {"events": [{"label": "Speech", "start": 2.0, "end": 4.0}]},
             digest=D1, start_s=2.0, end_s=4.0, lane="m5",
             native=_native("audio.sound_event",
                            {"events": [{"label": "Speech"}]}, digest=D1,
                            lane="m5", start_tick=2000, end_tick=4000)))


def test_interval_join_intersects_predicates():
    plan = evidence_query.compile_plan([
        {"predicate": "event_span", "event": "speaking"},
        {"predicate": "measurement",
         "measurement_type": "audio.sound_event"}])
    answer = evidence_query.evidence_for(_sound_model(), plan)
    assert answer["status"] == PRESENT
    assert len(answer["results"]) == 1
    result = answer["results"][0]
    assert (result["interval"]["start"], result["interval"]["end"]) == (
        2000, 3000)
    assert sorted(result["evidence_ids"]) == ["obs-sound-1",
                                              "obs-speaking-1"]
    assert sorted(result["lanes"]) == ["m5", "m7"]


def test_cross_clock_join_is_refused():
    model = _model(
        _obs("obs-a", "event_span",
             {"predicate": "speaking", "voice_track": "v1",
              "face_track": "f1"},
             digest=D1, start_s=1.0, end_s=3.0,
             native=_native("event.speaking_span",
                            {"predicate": "speaking"}, digest=D1, lane="m7",
                            start_tick=1000, end_tick=3000)),
        _obs("obs-b", "sound_event",
             {"events": [{"label": "Speech"}]},
             digest=D2, start_s=1.0, end_s=3.0, lane="m5",
             native=_native("audio.sound_event", {"events": [{}]},
                            digest=D2, lane="m5", start_tick=1000,
                            end_tick=3000)))
    plan = evidence_query.compile_plan([
        {"predicate": "event_span", "event": "speaking",
         "clock_id": f"clock-{D1}"},
        {"predicate": "measurement",
         "measurement_type": "audio.sound_event",
         "clock_id": f"clock-{D2}"}])
    answer = evidence_query.evidence_for(model, plan)
    assert answer["status"] == INCONCLUSIVE
    assert answer["results"] == []
    assert any("clock" in reason for reason in answer["reasons"])


def test_windowed_scope_clips_and_keeps_measured_absence():
    plan = evidence_query.compile_plan(
        [{"predicate": "event_span", "event": "speaking"}],
        scope={"clock_id": f"clock-{D2}", "start_s": 5.0, "end_s": 6.0})
    answer = evidence_query.evidence_for(_silent_model(), plan)
    assert answer["status"] == CONFIDENTLY_ABSENT
    outside = evidence_query.compile_plan(
        [{"predicate": "event_span", "event": "speaking"}],
        scope={"clock_id": f"clock-{D2}", "start_s": 25.0, "end_s": 30.0})
    assert evidence_query.evidence_for(
        _silent_model(), outside)["status"] == INCONCLUSIVE


# ─── Compile-time refusals ───────────────────────────────────────


def test_compile_refuses_what_it_cannot_evidence():
    with pytest.raises(evidence_query.EvidenceQueryError):
        evidence_query.compile_plan([])
    with pytest.raises(evidence_query.EvidenceQueryError):
        evidence_query.compile_plan(
            [{"predicate": "measurement",
              "measurement_type": "no.such.type"}])
    with pytest.raises(evidence_query.EvidenceQueryError):
        evidence_query.compile_plan(
            [{"predicate": "measurement",
              "measurement_type": "eval.sharpness_pass"}])
    with pytest.raises(evidence_query.EvidenceQueryError):
        evidence_query.compile_plan([{"predicate": "vibe"}])
    with pytest.raises(evidence_query.EvidenceQueryError):
        evidence_query.compile_plan(
            [{"predicate": "measurement",
              "measurement_type": "image.sharpness.laplacian_variance",
              "min": 5.0, "max": 1.0}])


def test_plan_from_filter_refuses_vlm_facets():
    with pytest.raises(evidence_query.EvidenceQueryError):
        evidence_query.plan_from_filter(framing="close-up")
    with pytest.raises(evidence_query.EvidenceQueryError):
        evidence_query.plan_from_filter(has_speech=False)
    plan = evidence_query.plan_from_filter(has_speech=True)
    assert plan["required_lanes"] == ["m7"]
    assert plan["predicates"] == [
        {"predicate": "event_span", "event": "speaking",
         "verified_only": False}]


# ─── Parity with FootageIndex.filter() ───────────────────────────


def _write_index(tmp_path: Path) -> FootageIndex:
    index_dir = tmp_path / "index"
    index_dir.mkdir(parents=True, exist_ok=True)
    segments = [
        {"segment_id": "clip_001#speech#0", "clip_id": "clip_001",
         "filename": "IMG_0001.MOV", "kind": "speech", "start": 1.0,
         "end": 3.0, "duration": 2.0, "text": "hello there",
         "facets": {"has_speech": True}},
        {"segment_id": "clip_002#scene#0", "clip_id": "clip_002",
         "filename": "IMG_0002.MOV", "kind": "scene", "start": 0.0,
         "end": 10.0, "duration": 10.0, "text": "an empty street",
         "facets": {"has_speech": False}},
    ]
    (index_dir / "footage_index.json").write_text(
        json.dumps({"segments": segments,
                    "audio_classification": {}}), encoding="utf-8")
    return FootageIndex(tmp_path / "proj", index_dir=index_dir)


def test_filter_and_evidence_plan_agree_on_speech(tmp_path):
    index = _write_index(tmp_path)
    # filter() semantics unchanged: the speech facet keeps its clip.
    kept = index.filter(has_speech=True)
    assert [hit["clip_id"] for hit in kept] == ["clip_001"]
    # The equivalent evidence plan over the migrated model names the
    # same positive: speech on the first source only.
    plan = evidence_query.plan_from_filter(has_speech=True)
    answer = evidence_query.evidence_for(_speaking_model(), plan)
    assert answer["status"] == PRESENT
    assert {result["asset_id"] for result in answer["results"]} == {
        f"asset-{D1}"}
