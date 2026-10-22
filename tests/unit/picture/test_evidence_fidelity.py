"""The typed Project Model path says which resolution answered it.

Every test here names a defect.  A test that cannot fail is worse than
no test, because it reads as coverage (AGENTS.md 10.4).

The defect these name: an answer from `ren evidence` that carried no
resolution at all, so a reader could not tell a claim about a span of
the source from a claim about the model's own structure - and the two
read the same in the answer.
"""

from __future__ import annotations

import pytest

from library.tools import evidence_contract, evidence_query
from library.tools.evidence_query import CONFIDENTLY_ABSENT, PRESENT

D1 = "digest-evidence-fidelity"
D2 = "digest-evidence-fidelity-silent"


def _native(measurement_type, value, *, digest, lane, start_tick=0,
            end_tick=20000, coverage="complete", samples=1,
            freshness="fresh", method="event-spans-v1 over M3+M3b"):
    build = (evidence_contract.new_inference
             if evidence_contract.is_model_produced(measurement_type)
             else evidence_contract.new_measurement)
    extra = ({"uncertainty": evidence_contract.UNCERTAINTY_UNSTATED}
             if evidence_contract.is_model_produced(measurement_type)
             else {})
    record = build(
        asset_id=f"asset-{digest}", clock_id=f"clock-{digest}",
        start_tick=start_tick, end_tick=end_tick,
        measurement_type=measurement_type, value=value, method=method,
        provenance_id=f"prov-{digest}-{lane}",
        coverage=evidence_contract.CoverageKind(coverage),
        coverage_samples=samples, coverage_sample_rate_hz=0.0, **extra)
    native = evidence_contract.to_rust_evidence(
        _with_freshness(record, freshness))
    native.update({
        "source_stream_id": f"stream-{digest}",
        "sampling_method": method,
        "temporal_coverage": [{"clock_id": f"clock-{digest}",
                               "start": start_tick, "end": end_tick}],
        "coverage_status": coverage,
        "verified_status": "verified",
        "producer": "fixture",
        "producer_version": "1",
    })
    return native


def _with_freshness(record, freshness):
    from dataclasses import replace

    return replace(record, freshness=freshness)


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


def _model(*observations, **extra):
    model = {"id": "project-fixture", "schema_version": 1,
             "clocks": [{"id": f"clock-{digest}", "ticks_per_second": 1000}
                        for digest in (D1, D2)],
             "assets": [{"id": f"asset-{digest}", "content_digest": digest,
                         "uri": f"/media/{digest}.MXF"}
                        for digest in (D1, D2)],
             "entities": [], "observations": list(observations),
             "identity_links": [], "tracks": [],
             "provenance": [
                 {"id": f"prov-{digest}-{lane}", "producer": "fixture"}
                 for digest in (D1, D2) for lane in ("m1", "m7")],
             "clip_uses": [], "renders": [], "frame_maps": [],
             "time_transforms": []}
    model.update(extra)
    return model


def _speaking_model():
    return _model(_obs(
        "obs-speaking-1", "event_span",
        {"predicate": "speaking", "voice_track": "v1", "face_track": "f1"},
        digest=D1, start_s=1.0, end_s=3.0,
        native=_native("event.speaking_span", {"predicate": "speaking"},
                       digest=D1, lane="m7", start_tick=1000, end_tick=3000)))


def _speaking_plan():
    return evidence_query.compile_plan(
        [{"predicate": "event_span", "event": "speaking"}])


def _silent_model():
    """A lane that covered the whole scope and found nothing."""
    return _model(_obs(
        "obs-silence-1", "event_span",
        {"predicate": "speaking", "voice_track": None,
         "face_track": None, "spans": []},
        digest=D2, start_s=0.0, end_s=20.0,
        native=_native("event.speaking_span", [], digest=D2, lane="m7",
                       start_tick=0, end_tick=20000,
                       coverage="complete", samples=0)))


# ─── The declaration ──────────────────────────────────────────────


def test_the_typed_path_says_which_resolution_answered_it():
    """An answer that does not say its resolution is a finding without
    a resolution.

    The defect this names: `ren evidence` answering "the model holds
    this record" in the same voice as "the source shows this", so a
    reader could not tell a lookup from a measurement.
    """
    answer = evidence_query.evidence_for(_speaking_model(), _speaking_plan())
    assert answer["status"] == PRESENT
    assert answer["fidelity"]["tier"] == "detailed"
    assert answer["fidelity"]["lane"] == "typed_project_model"
    assert answer["fidelity"]["establishes"]
    assert answer["fidelity"]["does_not_establish"]


def test_the_declaration_names_what_the_answer_rests_on():
    """A tier with nothing under it is a label.

    The defect this names: a resolution that names no lane, no record
    and no source, so a reader cannot check it.
    """
    answer = evidence_query.evidence_for(_speaking_model(), _speaking_plan())
    representation = answer["fidelity"]["representation"]
    assert representation["kind"] == "project_model_observations"
    assert representation["records"] == 1
    assert representation["lanes"] == ["m7"]
    assert representation["source_digests"] == [D1]
    assert representation["epistemic_classes"]
    assert answer["fidelity"]["coverage"]["about_the_source"] is True


def test_a_claim_about_the_models_own_structure_is_at_the_coarse_tier():
    """A relation the model holds is not a span of the source.

    The defect this names: a graph answer - "the model holds a
    relation between these two things" - filed at the detailed tier,
    so a derivation reads as a measurement of the footage.
    """
    model = _speaking_model()
    model["entities"] = [{"id": "entity-1", "label": "the host"}]
    model["identity_links"] = [{
        "id": "link-1", "observation_id": "obs-speaking-1",
        "entity_id": "entity-1"}]
    plan = evidence_query.compile_plan(
        [{"predicate": "entity", "entity_ref": "entity-1"}])
    answer = evidence_query.evidence_for(model, plan)
    assert answer["fidelity"]["tier"] == "coarse"
    assert answer["fidelity"]["coverage"]["about_the_source"] is False


def test_an_answer_whose_lanes_never_ran_is_at_the_coarse_tier():
    """Nothing measured is nothing measured.

    The defect this names: an answer that rests on no lane at all
    filed at the detailed tier, so "nobody looked" reads as "the
    source shows nothing".
    """
    model = _model()
    answer = evidence_query.evidence_for(model, _speaking_plan())
    assert answer["status"] != PRESENT
    assert answer["fidelity"]["tier"] == "coarse"
    assert answer["fidelity"]["coverage"]["ran"] is False


def test_a_measured_absence_over_a_complete_lane_is_at_the_detailed_tier():
    """An absence measured over the whole scope is about the source.

    The defect this names: a measured absence filed at the coarse tier
    as though the model had merely failed to hold a record, so a
    reader cannot tell "it is not there" from "nobody looked".
    """
    answer = evidence_query.evidence_for(_silent_model(), _speaking_plan())
    assert answer["status"] == CONFIDENTLY_ABSENT
    assert answer["fidelity"]["tier"] == "detailed"
    assert answer["fidelity"]["coverage"]["ran"] is True


# ─── The way up ───────────────────────────────────────────────────


def test_a_detailed_answer_carries_the_media_behind_its_finding():
    """The original tier is a pointer, not a description.

    The defect this names: a detailed answer that names no recording
    and no span, so the reader has to find the footage themselves.
    """
    answer = evidence_query.evidence_for(_speaking_model(), _speaking_plan())
    escalation = answer["escalation"]
    assert escalation["from"] == "detailed"
    assert escalation["to"] == "original"
    windows = escalation["request"]["windows"]
    assert windows[0]["media"] == f"/media/{D1}.MXF"
    assert windows[0]["start_s"] == 1.0
    assert windows[0]["end_s"] == 3.0


def test_an_answer_that_measured_nothing_says_what_would_measure_it():
    """The way up from nothing is the missing lane.

    The defect this names: an answer that holds nothing and names no
    way to get anything, so the caller cannot ask for more.
    """
    model = _model()
    answer = evidence_query.evidence_for(model, _speaking_plan())
    escalation = answer["escalation"]
    assert escalation["from"] == "coarse"
    assert escalation["to"] == "detailed"
    assert escalation["request"]["lane"] == "evidence_acquisition"
    assert escalation["request"]["acquire"] is True
    assert escalation["request"]["lanes"] == ["m7"]


def test_the_declaration_is_added_to_the_answer_and_not_to_the_model():
    """The model is the record; the answer is the reading.

    The defect this names: the declaration written into the Project
    Model, so a read-only query changed the model it read.
    """
    model = _speaking_model()
    before = repr(model)
    evidence_query.evidence_for(model, _speaking_plan())
    assert repr(model) == before
