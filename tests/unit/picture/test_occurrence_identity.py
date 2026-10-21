"""M0-L1: occurrence identity in the canonical model.

Every project here is a hand-made Project Model dict - no real project,
no footage, no model (§8). The events are built the way the migration
builds them: an event cites the observation that is its evidence, and
the observation carries the native evidence envelope.

What this establishes, in the order the lane needs it:

- the canonical model can NAME the second occurrence of a repeated
  event (before this lane it could not: there was no ordinal anywhere
  in the model, the query layer or the receipts);
- the number is the event's TIME ORDER inside its class and subject,
  not the order the lanes happened to write;
- the number survives a re-measure of the same source, which rewrites
  the records and their ids;
- the series is scoped to ONE source: the second occurrence in another
  take is a different moment, and a query that does not say which take
  is refused rather than answered from whichever came first;
- asking for an occurrence nobody measured is NOT an answer.

Every test fails on main: `library.tools.occurrence_identity` does not
exist there, and the model has no occurrence axis.
"""
import pytest

from library.tools import (
    evidence_contract,
    evidence_query,
    occurrence_identity,
    project_model,
)
from library.tools.evidence_query import INCONCLUSIVE, PRESENT

DIGEST = "digest-m0l1"
ASSET = f"asset-{DIGEST}"
CLOCK = f"clock-{DIGEST}"
PERSON = "person-m0l1-1"
OTHER_PERSON = "person-m0l1-2"


def _native(measurement_type, value, *, start_tick, end_tick,
            lane="m7", coverage="complete"):
    # A model-produced type is built as an INFERENCE, with the
    # uncertainty its producer earned; the contract refuses to build one
    # any other way (`new_measurement` raises for a model type).
    build = (evidence_contract.new_inference
             if evidence_contract.is_model_produced(measurement_type)
             else evidence_contract.new_measurement)
    extra = ({"uncertainty": evidence_contract.UNCERTAINTY_UNSTATED}
             if evidence_contract.is_model_produced(measurement_type)
             else {})
    record = build(
        asset_id=ASSET, clock_id=CLOCK,
        start_tick=start_tick, end_tick=end_tick,
        measurement_type=measurement_type, value=value,
        method="event-spans-v1 over M3",
        provenance_id=f"prov-{DIGEST}-{lane}",
        coverage=evidence_contract.CoverageKind(coverage),
        coverage_samples=1, coverage_sample_rate_hz=0.0, **extra)
    native = evidence_contract.to_rust_evidence(record)
    native.update({
        "source_stream_id": f"stream-{DIGEST}",
        "sampling_method": "event-spans-v1 over M3",
        "temporal_coverage": [{"clock_id": CLOCK, "start": start_tick,
                               "end": end_tick}],
        "coverage_status": coverage,
        "verified_status": "verified",
        "producer": "fixture",
        "producer_version": "1",
    })
    return native


def _observation(index, klass, start_s, end_s, *, subject=PERSON):
    """One event candidate's observation: the evidence of one occurrence."""
    obs_id = f"obs-{DIGEST}-m7-{index:04d}"
    start, end = int(start_s * 1000), int(end_s * 1000)
    payload = {"predicate": klass, "face_track": "face-1",
               "verdicts": [], "verified": True}
    payload["evidence_contract"] = _native(
        "event.candidate_span",
        {"predicate": klass, "face_track": "face-1", "verdicts": [],
         "verified": True},
        start_tick=start, end_tick=end)
    return {"id": obs_id, "asset_id": ASSET,
            "interval": {"clock_id": CLOCK, "start": start, "end": end},
            "modality": "event_candidate", "payload": payload,
            "confidence": None,
            "provenance_id": f"prov-{DIGEST}-m7"}


def _event(index, klass, start_s, end_s, *, subject=PERSON,
           observation=None, event_id=None):
    """One event, citing the observation that is its evidence."""
    obs = observation or _observation(index, klass, start_s, end_s,
                                      subject=subject)
    participants = [subject] if subject else []
    return {"id": event_id or f"event-{index}", "kind": klass,
            "interval": {"clock_id": CLOCK, "start": int(start_s * 1000),
                         "end": int(end_s * 1000)},
            "participant_ids": participants,
            "status": "verified", "certainty": "inferred",
            "evidence_ids": [obs["id"]]}, obs


def _model(events, *, observations=None, digest=DIGEST, asset=ASSET,
           clock=CLOCK, entities=None):
    obs_rows = []
    for event in events:
        obs_rows.append(event[1])
    if observations is not None:
        obs_rows = list(observations)
    return {
        "id": "project-m0l1", "schema_version": 1,
        "timebase_policy": "integer_ticks",
        "clocks": [{"id": clock, "ticks_per_second": 1000,
                    "domain": {"kind": "source", "asset_id": asset,
                               "stream_id": f"stream-{digest}"},
                    "frame_rate": {"numerator": 30, "denominator": 1}}],
        "assets": [{"id": asset, "kind": "video", "uri": "fixture.mov",
                    "content_digest": digest, "metadata": {},
                    "provenance": []}],
        "streams": [{"id": f"stream-{digest}", "asset_id": asset,
                     "clock_id": clock, "source_digest": digest,
                     "stream_index": 0, "metadata": {}}],
        "entities": entities if entities is not None else [
            {"id": PERSON, "kind": "person", "label": "person one",
             "object_class_id": None, "identity_scope": "project"},
            {"id": OTHER_PERSON, "kind": "person", "label": "person two",
             "object_class_id": None, "identity_scope": "project"}],
        "observations": obs_rows,
        "events": [row[0] if isinstance(row, tuple) else row
                   for row in events],
        "identity_links": [], "tracks": [], "relations": [],
        "provenance": [{"id": f"prov-{DIGEST}-m7", "producer": "fixture",
                        "producer_version": "1",
                        "source_digest": digest, "recorded_at": None}],
        "clip_uses": [], "edits": [], "effects": [], "renders": [],
        "frame_maps": [], "nle_bindings": [], "receipts": [],
        "signals": [], "masks": [], "object_classes": [],
        "intents": [], "timeline_states": [], "evaluation_policies": [],
        "interpretations": [], "time_transforms": [],
        "coordinate_spaces": [], "space_transforms": [],
        "query_results": [],
    }


def _three_occurrences():
    """Three times the person brings the device to their mouth."""
    first = _event(1, "hand_near_mouth", 17.0, 19.0)
    second = _event(2, "hand_near_mouth", 33.3, 33.7)
    third = _event(3, "hand_near_mouth", 47.0, 47.4)
    return first, second, third


def test_the_model_numbers_a_repeated_event_in_time_order():
    """The number is the order the events HAPPENED."""
    first, second, third = _three_occurrences()
    # Listed out of order on purpose: the lane wrote them as it found
    # them, and the index must not follow the list.
    model = _model([third, first, second])
    occurrence_identity.assign_occurrences(model)
    numbered = {event["id"]: event["occurrence"]
                for event in model["events"]}
    assert numbered["event-1"]["index"] == 1
    assert numbered["event-2"]["index"] == 2
    assert numbered["event-3"]["index"] == 3
    assert numbered["event-2"]["class"] == "hand_near_mouth"
    assert numbered["event-2"]["subject_id"] == PERSON


def test_the_query_verb_names_the_second_occurrence():
    """``nth_event`` answers with the event, its occurrence and its
    evidence."""
    first, second, third = _three_occurrences()
    model = _model([first, second, third])
    occurrence_identity.assign_occurrences(model)
    plan = evidence_query.compile_plan([
        {"predicate": "nth_event", "event_class": "hand_near_mouth",
         "nth": 2}])
    answer = evidence_query.evidence_for(model, plan)
    assert answer["status"] == PRESENT
    assert len(answer["results"]) == 1
    row = answer["results"][0]
    assert row["event_id"] == "event-2"
    assert row["occurrence"] == {"class": "hand_near_mouth",
                                 "subject_id": PERSON, "index": 2}
    assert row["evidence_ids"] == [second[1]["id"]]
    receipt = row["occurrence_receipt"]
    assert receipt["format"] == occurrence_identity.RECEIPT_FORMAT
    assert receipt["event_id"] == "event-2"
    assert receipt["occurrence_index"] == 2
    assert receipt["interval"]["start_s"] == pytest.approx(33.3)


def test_an_occurrence_nobody_measured_is_not_an_answer():
    """Asking for the fifth time is not a present."""
    first, second, third = _three_occurrences()
    model = _model([first, second, third])
    occurrence_identity.assign_occurrences(model)
    plan = evidence_query.compile_plan([
        {"predicate": "nth_event", "event_class": "hand_near_mouth",
         "nth": 5}])
    answer = evidence_query.evidence_for(model, plan)
    assert answer["status"] != PRESENT
    assert answer["results"] == []
    assert any("no occurrence 5" in reason for reason in answer["reasons"])


def test_the_index_survives_a_re_measure():
    """A re-measure rewrites the records; it does not move the second
    occurrence after the third."""
    first, second, third = _three_occurrences()
    before = _model([first, second, third])
    occurrence_identity.assign_occurrences(before)
    keys_before = {event["id"]: occurrence_identity.occurrence_key(before, event)
                   for event in before["events"]}

    # The same source, measured again: new record ids, new event ids,
    # the same moments.
    again = []
    for index, (klass, start_s, end_s) in enumerate(
            (("hand_near_mouth", 17.0, 19.0),
             ("hand_near_mouth", 33.3, 33.7),
             ("hand_near_mouth", 47.0, 47.4)), start=1):
        observation = _observation(index, klass, start_s, end_s)
        observation["id"] = f"obs-{DIGEST}-m7-r2-{index:04d}"
        again.append(_event(index, klass, start_s, end_s,
                            observation=observation,
                            event_id=f"event-r2-{index}"))
    after = _model(again)
    occurrence_identity.assign_occurrences(after)
    keys_after = {event["id"]: occurrence_identity.occurrence_key(after, event)
                  for event in after["events"]}
    # The records were rewritten; the occurrence names the same moment.
    assert keys_before["event-1"] == keys_after["event-r2-1"]
    assert keys_before["event-2"] == keys_after["event-r2-2"]
    assert keys_before["event-3"] == keys_after["event-r2-3"]
    assert before["events"][1]["id"] != after["events"][1]["id"]
    assert occurrence_identity.nth_event(
        after, "hand_near_mouth", n=2)["id"] == "event-r2-2"


def test_two_people_are_numbered_separately():
    """The series is a class AND a subject."""
    mine = _event(1, "hand_near_mouth", 10.0, 11.0, subject=PERSON)
    yours = _event(2, "hand_near_mouth", 12.0, 13.0, subject=OTHER_PERSON)
    later = _event(3, "hand_near_mouth", 20.0, 21.0, subject=PERSON)
    model = _model([mine, yours, later])
    occurrence_identity.assign_occurrences(model)
    mine_occurrence = occurrence_identity.nth_event(
        model, "hand_near_mouth", subject_id=PERSON, n=2)
    yours_occurrence = occurrence_identity.nth_event(
        model, "hand_near_mouth", subject_id=OTHER_PERSON, n=1)
    assert mine_occurrence["id"] == "event-3"
    assert yours_occurrence["id"] == "event-2"


def test_the_second_occurrence_in_another_take_is_a_different_moment():
    """The series is scoped to one source, and an unscoped query is
    refused rather than answered from whichever take came first."""
    first = _event(1, "hand_near_mouth", 17.0, 19.0)
    second = _event(2, "hand_near_mouth", 33.3, 33.7)
    other_first = _event(3, "hand_near_mouth", 5.0, 6.0)
    other_second = _event(4, "hand_near_mouth", 9.0, 10.0)
    model = _model([first, second])
    model["clocks"].append(
        {"id": "clock-other", "ticks_per_second": 1000,
         "domain": {"kind": "source", "asset_id": "asset-other",
                    "stream_id": "stream-other"},
         "frame_rate": {"numerator": 30, "denominator": 1}})
    model["assets"].append(
        {"id": "asset-other", "kind": "video", "uri": "other.mov",
         "content_digest": "digest-other", "metadata": {},
         "provenance": []})
    model["streams"].append(
        {"id": "stream-other", "asset_id": "asset-other",
         "clock_id": "clock-other", "source_digest": "digest-other",
         "stream_index": 0, "metadata": {}})
    for event, observation in ((other_first[0], other_first[1]),
                                (other_second[0], other_second[1])):
        observation["id"] = observation["id"].replace("-m7-", "-other-")
        observation["interval"]["clock_id"] = "clock-other"
        observation["asset_id"] = "asset-other"
        event["interval"]["clock_id"] = "clock-other"
        event["evidence_ids"] = [observation["id"]]
    model["events"].extend([other_first[0], other_second[0]])
    model["observations"].extend([other_first[1], other_second[1]])
    occurrence_identity.assign_occurrences(model)

    plan = evidence_query.compile_plan([
        {"predicate": "nth_event", "event_class": "hand_near_mouth",
         "nth": 2}])
    answer = evidence_query.evidence_for(model, plan)
    assert answer["status"] == INCONCLUSIVE
    assert answer["results"] == []
    assert any("sources" in reason for reason in answer["reasons"])

    scoped = evidence_query.compile_plan(
        [{"predicate": "nth_event", "event_class": "hand_near_mouth",
          "nth": 2}],
        scope={"asset_id": "asset-other"})
    scoped_answer = evidence_query.evidence_for(model, scoped)
    assert scoped_answer["status"] == PRESENT
    assert scoped_answer["results"][0]["event_id"] == "event-4"


def test_the_model_refuses_two_events_claiming_one_occurrence():
    """Two events may not hold the same place in one series."""
    first, second, third = _three_occurrences()
    model = _model([first, second, third])
    occurrence_identity.assign_occurrences(model)
    model["events"][2]["occurrence"] = dict(model["events"][1]["occurrence"])
    with pytest.raises(ValueError, match="occurrence"):
        project_model.validate_project_model(model)


def test_the_evidence_ledger_names_the_occurrence():
    """The observation that supports an occurrence says which one."""
    from library.tools import project_model_evidence

    first, second, third = _three_occurrences()
    model = _model([first, second, third])
    builder = project_model_evidence._Builder("project-m0l1")
    builder.model = model
    project_model_evidence._assign_occurrences(builder)
    rows = model["observations"][1]["payload"]["occurrences"]
    assert rows == [{"class": "hand_near_mouth", "index": 2,
                     "subject_id": PERSON, "event_id": "event-2"}]


def test_a_typed_target_resolves_to_one_event():
    """The target names one occurrence, and the receipt says which."""
    first, second, third = _three_occurrences()
    model = _model([first, second, third])
    occurrence_identity.assign_occurrences(model)
    resolved = occurrence_identity.resolve_target(
        model, {"event_class": "hand_near_mouth", "subject_id": PERSON,
                "nth": 2})
    assert resolved["status"] == "resolved"
    receipt = resolved["receipt"]
    assert receipt["event_id"] == "event-2"
    assert receipt["occurrence_index"] == 2
    assert receipt["occurrence_key"].endswith("|2")
    assert receipt["evidence_ids"] == [second[1]["id"]]
    assert receipt["interval"]["start_s"] == pytest.approx(33.3)
    assert receipt["interval"]["end_s"] == pytest.approx(33.7)

    missing = occurrence_identity.resolve_target(
        model, {"event_class": "hand_near_mouth", "subject_id": PERSON,
                "nth": 4})
    assert missing["status"] == "not_found"
    assert missing["receipt"] is None
    assert "no occurrence 4" in missing["reason"]


def test_the_ordinal_predicate_refuses_what_it_cannot_name():
    """A plan that cannot name its occurrence is refused at compile
    time, not widened into a guess."""
    with pytest.raises(evidence_query.EvidenceQueryError):
        evidence_query.compile_plan([{"predicate": "nth_event", "nth": 2}])
    with pytest.raises(evidence_query.EvidenceQueryError):
        evidence_query.compile_plan([
            {"predicate": "nth_event", "event_class": "hand_near_mouth",
             "nth": 0}])
    with pytest.raises(evidence_query.EvidenceQueryError):
        evidence_query.compile_plan([{"predicate": "nth_occurrance"}])
