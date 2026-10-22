"""Q01: the evidence-grounded editing contract.

Defect-naming tests only (cheap: stored observations and stubs, no
model, no Resolve, no media). Each test names the defect it pins; the
Q01 measurement itself is ``test_recorded_plans_before_and_after``,
which audits the same recorded proposals before the contract (bare
``evidence_ids`` strings, as the engine carries them today) and after
grounding them through it, with denominators.
"""

from __future__ import annotations

import pytest

from library.tools import edit_evidence_contract as contract
from library.tools import source_reference

STREAM = "stream-test-aaa"
CLOCK = "clock-test-aaa"


def _interval(start: int, end: int) -> dict:
    return source_reference.interval(STREAM, CLOCK, start, end)


def _observation(observation_id: str, start: int = 1000, end: int = 4000) -> dict:
    return {
        "id": observation_id,
        "asset_id": "asset-test-aaa",
        "interval": {"clock_id": CLOCK, "start": start, "end": end},
    }


def _speak_ref(
    event_id: str = "obs-speak-1",
    uncertainty: float = 0.08,
    precision: str = contract.PRECISION_TICK,
    **extra,
) -> contract.EvidenceReference:
    return contract.new_reference(
        event_id=event_id,
        event_kind="speaking_span",
        source=_interval(1000, 4000),
        precision=precision,
        certainty="inferred",
        uncertainty=uncertainty,
        supporting_observation_ids=(event_id,),
        **extra,
    )


def test_a_note_id_points_at_no_footage():
    # Defect: the revise path grounds a decision on
    # "revision-note:<id>" - a human's words about the timeline - and
    # the audit read it as evidence because a string was all it asked
    # for.
    decision = {
        "decision_id": "revise:7:decision:0",
        "evidence_ids": ["revision-note:7"],
    }
    report = contract.audit_decision(decision, {}, ())
    assert report["grounded"] is False
    assert report["ungrounded_ids"] == ["revision-note:7"]
    with pytest.raises(contract.EditEvidenceContractError):
        contract.reference_from_observation(
            {"note": "take out the pause"},
            event_kind="speaking_span",
            source=_interval(1000, 4000),
        )


def test_an_inference_without_uncertainty_is_a_guess():
    # Defect: a speaking span with no uncertainty filed as grounding -
    # an inference that asserts nothing about how sure it was.
    with pytest.raises(contract.EditEvidenceContractError):
        contract.new_reference(
            event_id="obs-speak-1",
            event_kind="speaking_span",
            source=_interval(1000, 4000),
            certainty="inferred",
            uncertainty=None,
            supporting_observation_ids=("obs-speak-1",),
        )


def test_a_detector_span_filed_as_measured_is_refused():
    # Defect: a model-produced span filed as a measurement - certainty
    # is what the producer earned, not what the proposer wishes.
    with pytest.raises(contract.EditEvidenceContractError):
        contract.new_reference(
            event_id="obs-speak-1",
            event_kind="speaking_span",
            source=_interval(1000, 4000),
            certainty="measured",
            supporting_observation_ids=("obs-speak-1",),
        )


def test_a_model_verdict_does_not_promote_a_description():
    # Defect: a model confirming a model read as a measurement. The
    # promotion gate takes deterministic verifiers only.
    with pytest.raises(contract.EditEvidenceContractError):
        contract.new_reference(
            event_id="obs-speak-1",
            event_kind="speaking_span",
            source=_interval(1000, 4000),
            certainty="measured",
            supporting_observation_ids=("obs-speak-1",),
            verification={"verifier_kind": "model", "answer": True},
        )


def test_a_deterministic_verification_promotes_openly():
    # The other half of the gate: an instrument check recorded against
    # the claim files it as measured, and says so.
    reference = contract.new_reference(
        event_id="obs-speak-1",
        event_kind="speaking_span",
        source=_interval(1000, 4000),
        certainty="measured",
        supporting_observation_ids=("obs-speak-1",),
        verification={"verifier_kind": "instrument", "check": "word-edge alignment"},
    )
    assert reference.is_measured is True


def test_an_unknown_event_kind_is_refused_not_defaulted():
    # Defect: a claim nobody declared read as a measurement because
    # nothing said otherwise. Unknown stays refused.
    with pytest.raises(contract.EditEvidenceContractError):
        contract.new_reference(
            event_id="obs-x-1",
            event_kind="Director's favourite moment",
            source=_interval(1000, 4000),
            certainty="measured",
            supporting_observation_ids=("obs-x-1",),
        )


def test_a_file_alone_names_no_place_in_it():
    # Defect: an asset reference filed as the source interval - the
    # file is not a place in it.
    with pytest.raises(contract.EditEvidenceContractError):
        contract.new_reference(
            event_id="obs-speak-1",
            event_kind="speaking_span",
            source=source_reference.asset("asset-test-aaa"),
            certainty="inferred",
            uncertainty=0.1,
            supporting_observation_ids=("obs-speak-1",),
        )


def test_a_span_proposes_but_never_places_a_cut_alone():
    # Defect: an event extent (boundary-approximate) executed as a cut
    # point with nobody confirming the frame.
    tick_ref = _speak_ref(precision=contract.PRECISION_TICK)
    span_ref = _speak_ref(precision=contract.PRECISION_SPAN)
    tick_gate = contract.gate_autonomy([tick_ref], autonomy="autonomous")
    span_gate = contract.gate_autonomy([span_ref], autonomy="autonomous")
    assert tick_gate["verdict"] == "autonomous"
    assert span_gate["verdict"] == "needs_approval"
    assert any("boundary-approximate" in reason for reason in span_gate["reasons"])


def _sound_ref(
    uncertainty: float, precision: str = contract.PRECISION_TICK
) -> contract.EvidenceReference:
    return contract.new_reference(
        event_id="obs-sound-1",
        event_kind="sound_event",
        source=_interval(1000, 4000),
        precision=precision,
        certainty="inferred",
        uncertainty=uncertainty,
        supporting_observation_ids=("obs-sound-1",),
    )


def test_high_uncertainty_needs_a_person():
    # Defect: a 0.7-certain sound label executing alone. The bar is the
    # contract's declared v1 value, at or under to pass.
    calm = _sound_ref(
        contract.AUTONOMOUS_MAX_UNCERTAINTY, precision=contract.PRECISION_SPAN
    )
    assert (
        contract.gate_autonomy([calm], autonomy="autonomous")["verdict"]
        == "needs_approval"
    )  # span precision still proposes only
    tick_calm = _sound_ref(contract.AUTONOMOUS_MAX_UNCERTAINTY)
    tick_wild = _sound_ref(contract.AUTONOMOUS_MAX_UNCERTAINTY + 0.01)
    assert (
        contract.gate_autonomy([tick_calm], autonomy="autonomous")["verdict"]
        == "autonomous"
    )
    assert (
        contract.gate_autonomy([tick_wild], autonomy="autonomous")["verdict"]
        == "needs_approval"
    )


def test_ambiguity_the_proposal_never_owned_is_incomplete():
    # Defect: two overlapping candidate spans, the proposal naming one
    # and the audit counting it grounded - the other candidate is
    # ambiguity nobody listed.
    reference = _speak_ref(event_id="obs-speak-1")
    observations = {"obs-speak-1": _observation("obs-speak-1")}
    candidates = {
        "obs-speak-1": {"start_tick": 1000, "end_tick": 4000},
        "obs-speak-2": {"start_tick": 2000, "end_tick": 5000},
    }
    problems = contract.audit_reference(reference, observations, candidates)
    assert any("ambiguous" in problem for problem in problems)


def test_listed_alternatives_keep_the_choice_with_a_person():
    # The other half: alternatives listed is honest, and honesty does
    # not execute - the choice between candidates belongs to a person.
    reference = _speak_ref(event_id="obs-speak-1", alternatives=("obs-speak-2",))
    candidates = {
        "obs-speak-1": {"start_tick": 1000, "end_tick": 4000},
        "obs-speak-2": {"start_tick": 2000, "end_tick": 5000},
    }
    observations = {"obs-speak-1": _observation("obs-speak-1")}
    assert contract.audit_reference(reference, observations, candidates) == []
    gate = contract.gate_autonomy([reference], autonomy="autonomous")
    assert gate["verdict"] == "needs_approval"
    assert any("alternatives" in reason for reason in gate["reasons"])


def test_a_judgement_names_its_judge_or_authorises_nothing():
    # Defect: an interpreted claim with nobody behind it standing in
    # for evidence. A judgement executes never - it is confirmed.
    with pytest.raises(contract.EditEvidenceContractError):
        contract.new_reference(
            event_id="obs-judge-1",
            event_kind="judgement",
            source=_interval(1000, 4000),
            certainty="interpreted",
            supporting_observation_ids=("obs-judge-1",),
        )
    judged = contract.new_reference(
        event_id="obs-judge-1",
        event_kind="judgement",
        source=_interval(1000, 4000),
        certainty="interpreted",
        supporting_observation_ids=("obs-judge-1",),
        authorized_by="captain",
    )
    gate = contract.gate_autonomy([judged], autonomy="autonomous")
    assert gate["verdict"] == "needs_approval"


def test_a_measured_claim_carries_no_numeric_uncertainty():
    # Defect: a number on a measurement's certainty, reading as
    # measured when nothing measured it.
    with pytest.raises(contract.EditEvidenceContractError):
        contract.new_reference(
            event_id="obs-bound-1",
            event_kind="scene_boundary",
            source=_interval(1000, 4000),
            certainty="measured",
            uncertainty=0.05,
            supporting_observation_ids=("obs-bound-1",),
        )


def test_the_gate_never_widens_what_the_request_granted():
    # Defect: perfect evidence executing under a request that never
    # granted autonomy. The gate narrows only.
    reference = contract.new_reference(
        event_id="obs-bound-1",
        event_kind="scene_boundary",
        source=_interval(1000, 4000),
        certainty="measured",
        supporting_observation_ids=("obs-bound-1",),
    )
    gate = contract.gate_autonomy([reference], autonomy="needs_approval")
    assert gate["verdict"] == "needs_approval"


def test_an_explanation_traces_the_edit_to_footage():
    lines = contract.explain("cut:0", [_speak_ref()])
    assert len(lines) == 1
    line = lines[0]
    assert "speaking_span" in line and "obs-speak-1" in line
    assert STREAM in line and "1.000-4.000s" in line
    assert "inferred" in line
    assert contract.explain("cut:0", []) == [
        "cut:0: no evidence references - nothing traceable to footage"
    ]


# ── The Q01 measurement: recorded plans, before vs after ─────────────


def _recorded_plans() -> tuple[list[dict], dict]:
    """Six recorded proposals in the engine's own decision shape.

    The first three are evidence-shaped (dialogue, scene, sound); the
    fourth is the revise path's note-id grounding; the fifth is an
    ambiguous candidate pair; the sixth is the captain's own
    correction. Stored observations only - no model, no media.
    """
    observations = {
        "obs-speak-1": _observation("obs-speak-1", 1000, 4000),
        "obs-bound-1": _observation("obs-bound-1", 4000, 4001),
        "obs-sound-1": _observation("obs-sound-1", 1000, 4000),
        "obs-cand-1": _observation("obs-cand-1", 1500, 3500),
        "obs-cand-2": _observation("obs-cand-2", 2000, 4500),
        "obs-judge-1": _observation("obs-judge-1", 1000, 4000),
    }
    decisions = [
        {"decision_id": "dialogue-cut", "evidence_ids": ["obs-speak-1"]},
        {"decision_id": "scene-cut", "evidence_ids": ["obs-bound-1"]},
        {"decision_id": "sound-cut", "evidence_ids": ["obs-sound-1"]},
        {"decision_id": "revise-note", "evidence_ids": ["revision-note:7"]},
        {"decision_id": "ambiguous-cut", "evidence_ids": ["obs-cand-1"]},
        {"decision_id": "captain-correction", "evidence_ids": ["obs-judge-1"]},
    ]
    return decisions, observations


def _ground_recorded() -> dict[str, list]:
    return {
        "dialogue-cut": [
            contract.reference_from_observation(
                _observation("obs-speak-1", 1000, 4000),
                event_kind="speaking_span",
                source=_interval(1000, 4000),
                precision=contract.PRECISION_TICK,
                uncertainty=0.08,
            )
        ],
        "scene-cut": [
            contract.reference_from_observation(
                _observation("obs-bound-1", 4000, 4001),
                event_kind="scene_boundary",
                source=_interval(4000, 4001),
                precision=contract.PRECISION_TICK,
            )
        ],
        "sound-cut": [
            contract.reference_from_observation(
                _observation("obs-sound-1", 1000, 4000),
                event_kind="sound_event",
                source=_interval(1000, 4000),
                precision=contract.PRECISION_TICK,
                uncertainty=0.3,
            )
        ],
        "revise-note": [],
        "ambiguous-cut": [
            contract.reference_from_observation(
                _observation("obs-cand-1", 1500, 3500),
                event_kind="candidate_span",
                source=_interval(1500, 3500),
                precision=contract.PRECISION_TICK,
                uncertainty=0.1,
                alternatives=("obs-cand-2",),
            )
        ],
        "captain-correction": [
            contract.reference_from_observation(
                _observation("obs-judge-1", 1000, 4000),
                event_kind="judgement",
                source=_interval(1000, 4000),
                precision=contract.PRECISION_TICK,
                authorized_by="captain",
            )
        ],
    }


def test_recorded_plans_before_and_after():
    decisions, observations = _recorded_plans()

    before = contract.audit_graph(decisions, observations, {})
    assert before["proposed_edits"] == 6
    assert before["edits_naming_evidence"] == 6
    assert before["grounded_edits"] == 0
    assert before["complete_references"] == 0

    after = contract.audit_graph(decisions, observations, _ground_recorded())
    assert after["proposed_edits"] == 6
    assert after["grounded_edits"] == 5
    assert after["complete_references"] == 5
    assert after["measured_references"] == 1
    assert after["inferred_references"] == 3
    assert after["interpreted_references"] == 1
    assert after["ambiguous_references"] == 1
    rows = {row["decision_id"]: row for row in after["decisions"]}
    assert rows["revise-note"]["grounded"] is False
    assert rows["revise-note"]["ungrounded_ids"] == ["revision-note:7"]

    gates = {
        decision_id: contract.gate_autonomy(
            refs, autonomy="autonomous", observations=observations
        )
        for decision_id, refs in _ground_recorded().items()
    }
    assert gates["dialogue-cut"]["verdict"] == "autonomous"
    assert gates["scene-cut"]["verdict"] == "autonomous"
    assert gates["sound-cut"]["verdict"] == "needs_approval"
    assert gates["revise-note"]["verdict"] == "refused"
    assert gates["ambiguous-cut"]["verdict"] == "needs_approval"
    assert gates["captain-correction"]["verdict"] == "needs_approval"
