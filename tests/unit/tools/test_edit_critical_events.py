import pytest

from library.tools import edit_critical_events as ece


def test_ten_event_types_in_punchlist_order():
    assert ece.event_types() == (
        "speech_start_end",
        "word_phrase_boundary",
        "speaker_change",
        "action_state_change",
        "object_interaction",
        "scene_transition",
        "onscreen_text_change",
        "audio_event",
        "technical_defect",
        "retake_repeat",
    )


def test_every_type_has_a_fidelity_requirement_with_a_reader():
    for event_type in ece.event_types():
        requirement = ece.fidelity_requirement(event_type)
        assert requirement["temporal_tolerance_seconds"] > 0
        assert requirement["required_evidence"], event_type
        assert requirement["verification"], event_type
        assert requirement["abstain_when"], event_type
        assert ece.event_label(event_type)


def test_unknown_type_fails_loudly():
    with pytest.raises(KeyError):
        ece.event_label("cinematic_moment")
    with pytest.raises(KeyError):
        ece.fidelity_requirement("cinematic_moment")


def test_verify_reports_uncertain_not_invented():
    event = {"event_type": "speaker_change",
             "boundary_seconds": 12.0, "evidence": {}}
    verdict = ece.verify_event_fidelity(event, {})
    assert verdict["status"] == "uncertain"
    assert any("person.voice_turn" in reason
               for reason in verdict["reasons"])
    assert ece.verify_event_fidelity(
        {"event_type": "not_a_type"}, {})["status"] == "uncertain"


def test_verify_passes_on_present_evidence():
    event = {"event_type": "speaker_change", "boundary_seconds": 12.0,
             "boundary_precision_seconds": 0.05,
             "evidence": {"person.voice_turn": [{"tick": 1}],
                          "speech.word": [{"word": "yes"}]}}
    verdict = ece.verify_event_fidelity(event, {})
    assert verdict == {"status": "verified",
                       "event_type": "speaker_change", "reasons": []}


def test_tolerance_breach_is_uncertain():
    event = {"event_type": "speech_start_end", "boundary_seconds": 3.0,
             "boundary_precision_seconds": 0.5,
             "evidence": {"speech.word": [{"word": "go"}]}}
    verdict = ece.verify_event_fidelity(event, {})
    assert verdict["status"] == "uncertain"
    assert any("tolerance" in reason for reason in verdict["reasons"])
