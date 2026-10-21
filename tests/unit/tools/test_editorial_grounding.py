"""The grounding half: could the work be pointed at?

The defect this file exists for is a report that believed a boolean. A
record said "resolved: true" for a target, the harness read the
boolean, and the published number counted the claim as a fact - while
the record carried no evidence at all that the thing it named was
there.

Three things are kept apart here, because they are three different
answers:

- a role whose premise the substrate does not carry is NOT APPLICABLE:
  there was nothing to point at, and that is not the run's failure.
- a resolution the recorded timeline does not carry is NOT VERIFIED:
  the run's own claim is not evidence.
- a resolution with no evidence at all is unverified too, and says so.
"""

from __future__ import annotations

from library.tools.editorial_acceptance import footage, grounding


def _key(**overrides):
    key = {
        "edge_case": None,
        "expected_refusal": False,
        "expected_operations": ["overlay.position"],
        "expected_target_roles": ["name_lower_third"],
        "expected_moment_ids": [],
        "allowed_operations": ["overlay.position"],
        "footage_requirements": [],
        "stated_constraints": [],
        "role_premises": {},
        "notes": "",
    }
    key.update(overrides)
    return key


def _record(**overrides):
    record = {
        "task_id": "b",
        "interpretation": {
            "restated_outcome": "move the name bar",
            "selected_operations": ["overlay.position"],
            "resolved_targets": [{
                "role": "name_lower_third",
                "resolved": True,
                "target": "the lower third, unique_id 11111111-2222-3333-"
                          "4444-555555555555, frames 0-84",
            }],
        },
    }
    record.update(overrides)
    return record


def _premise(**states):
    return {
        "status": footage.PREMISE_CARRIED,
        "requirements": [{"feature": feature, "state": state}
                         for feature, state in states.items()],
    }


def _recorded(*clip_ids):
    return grounding.RecordedTimeline(list(clip_ids),
                                      note="a recorded generation")


def test_a_resolution_the_recorded_timeline_carries_is_verified():
    """The connection: the claim is checked against the timeline's own
    recorded generation, so a resolution that names a clip that IS there
    is established rather than asserted."""
    measured = grounding.measure(
        _record(), _key(role_premises={"name_lower_third":
                                       "lower_third_overlay"}),
        _premise(lower_third_overlay=footage.SATISFIED),
        timeline=_recorded("11111111-2222-3333-4444-555555555555"))
    detail = measured["detail"]
    assert detail["verified_role_instances"] == 1
    assert detail["instances"][0]["evidence"] == grounding.VERIFIED


def test_a_resolution_the_recorded_timeline_does_not_carry_is_not_verified():
    """The defect: the record said the target was resolved, the
    recorded timeline does not carry the clip it named, and the report
    counted the claim as a fact."""
    measured = grounding.measure(
        _record(), _key(),
        _premise(),
        timeline=_recorded("99999999-2222-3333-4444-555555555555"))
    detail = measured["detail"]
    assert detail["verified_role_instances"] == 0
    assert detail["instances"][0]["evidence"] == grounding.NOT_VERIFIED
    assert detail["instances"][0]["claimed_resolved"] is True


def test_a_resolution_with_no_evidence_is_unverified():
    """The defect: the run resolved a target and named nothing that
    could be checked, and the harness called it a resolution."""
    record = _record()
    record["interpretation"]["resolved_targets"] = [{
        "role": "name_lower_third", "resolved": True,
        "target": "the name bar somewhere on the timeline"}]
    measured = grounding.measure(record, _key(), _premise(),
                                 timeline=_recorded("11111111-2222-3333-4444-"
                                                   "555555555555"))
    assert measured["detail"]["instances"][0]["evidence"] == \
        grounding.NO_EVIDENCE
    assert measured["detail"]["verified_role_instances"] == 0


def test_a_resolution_cannot_be_verified_without_a_recorded_timeline():
    """The defect: no recorded timeline, and the harness called the
    claim verified anyway - a check nobody ran, reported as a check that
    passed."""
    measured = grounding.measure(
        _record(), _key(),
        _premise(),
        timeline=grounding.RecordedTimeline(available=False,
                                            note="no recorded timeline"))
    assert measured["detail"]["instances"][0]["evidence"] == \
        grounding.UNAVAILABLE
    assert measured["detail"]["verified_role_instances"] == 0


def test_a_role_whose_premise_the_substrate_lacks_is_not_applicable():
    """The defect: the substrate did not carry what the request
    presupposed, and the run was scored as if it had failed to point at
    something that was never there."""
    measured = grounding.measure(
        _record(), _key(role_premises={"name_lower_third": "music_bed"}),
        _premise(music_bed=footage.MISSING),
        timeline=_recorded("11111111-2222-3333-4444-555555555555"))
    detail = measured["detail"]
    assert detail["not_applicable_role_instances"] == 1
    assert detail["applicable_role_instances"] == 0
    assert detail["instances"][0]["status"] == grounding.NOT_APPLICABLE
    assert detail["instances"][0]["reason"]


def test_a_role_whose_premise_was_never_measured_still_counts():
    """The defect: nobody looked for the premise, and the role was
    excused as if the footage had been measured and found wanting."""
    measured = grounding.measure(
        _record(), _key(role_premises={"name_lower_third": "matching_broll"}),
        _premise(matching_broll=footage.REQUIREMENT_UNKNOWN),
        timeline=_recorded("11111111-2222-3333-4444-555555555555"))
    detail = measured["detail"]
    assert detail["applicable_role_instances"] == 1
    assert detail["not_applicable_role_instances"] == 0


def test_the_grounding_view_counts_role_instances_and_not_briefs():
    """The defect: a brief that names seven roles and a brief that names
    one are not the same size of claim, and a per-brief rate hides that."""
    rows = [
        {"brief_id": "a", **grounding.measure(
            _record(), _key(role_premises={"name_lower_third":
                                           "lower_third_overlay"}),
            _premise(lower_third_overlay=footage.SATISFIED),
            timeline=_recorded("11111111-2222-3333-4444-555555555555"))},
        {"brief_id": "b", **grounding.measure(
            _record(), _key(expected_target_roles=["name_lower_third",
                                                   "music_bed", "caption"],
                             role_premises={"music_bed": "music_bed"}),
            _premise(music_bed=footage.MISSING),
            timeline=_recorded())},
    ]
    summary = grounding.summarize(rows)
    assert summary["role_instances"] == 4
    assert summary["applicable_role_instances"] == 3
    assert summary["not_applicable_role_instances"] == 1
    assert summary["claimed_resolved_role_instances"] == 2
    assert summary["verified_role_instances"] == 1
    assert summary["value"] == 2 / 3
