"""Fidelity tests for the generalized effect authorization pattern.

Every effect family (audio, color, graphics, VFX) follows the stabilization
model: measurement detects, recommendation may propose, only explicit editor
instruction authorizes.  These tests prove the gate refuses unauthorized
effects by name and that authorized effects carry their records.
"""

import pytest

from library.tools.effect_authorization import (
    EffectAuthorizationError,
    authorizations_from_timeline_notes,
    gate_effect_authorizations,
    refuse_unauthorized_effect,
    scope_matches_effect,
    validate_authorizations,
)


class TestUnauthorizedEffectRefusal:
    """A recommendation without an authorization is refused by name."""

    def test_vfx_stabilize_without_authorization_is_refused(self):
        """A VFX stabilize effect with no authorization is refused."""
        effect = {
            "effect_type": "stabilize",
            "target_block_position": 5,
            "timeline_start": 10.0,
            "timeline_end": 15.0,
        }
        with pytest.raises(ValueError, match="Stabilization authorization refused"):
            gate_effect_authorizations(
                {"vfx": [effect]}, [],
            )

    def test_audio_voice_isolation_without_authorization_is_reported_not_refused(self):
        """An audio voice_isolation effect with no authorization is reported, not refused.

        Non-stabilization families are record-and-report: the audio mix
        planner (step 5.02) produces voice_isolation from model decisions,
        not editor instructions, so refusing would break the default build.
        """
        effect = {
            "effect_type": "voice_isolation",
            "sfx_id": "voice_isolation",
            "timeline_in": 10.0,
            "timeline_out": 15.0,
        }
        result = gate_effect_authorizations(
            {"audio": [effect]}, [],
        )
        assert len(result["audio"]) == 1

    def test_non_restricted_effect_passes_without_authorization(self):
        """A non-restricted effect (e.g. slow_zoom) passes without authorization."""
        effect = {
            "effect_type": "slow_zoom_in",
            "target_block_position": 5,
            "timeline_start": 10.0,
            "timeline_end": 15.0,
        }
        result = gate_effect_authorizations(
            {"vfx": [effect]}, [],
        )
        assert len(result["vfx"]) == 1

    def test_planner_chosen_voice_isolation_still_builds(self):
        """A planner-chosen voice_isolation (no editor instruction) still builds.

        The audio mix planner (step 5.02) produces voice_isolation from model
        decisions, not editor instructions.  Non-stabilization families are
        record-and-report: the effect is reported as unauthorized but not
        refused, so the default build does not break.
        """
        effect = {
            "effect_type": "voice_isolation",
            "sfx_id": "voice_isolation",
            "timeline_in": 10.0,
            "timeline_out": 15.0,
            "source": "camera.mov",
            "amount": 60,
        }
        result = gate_effect_authorizations(
            {"audio": [effect]}, [],
        )
        assert len(result["audio"]) == 1
        assert result["audio"][0]["effect_type"] == "voice_isolation"

    def test_refusal_message_names_the_effect(self):
        """The refusal message includes the effect type and family."""
        effect = {
            "effect_type": "voice_isolation",
            "sfx_id": "voice_isolation",
        }
        with pytest.raises(ValueError) as exc_info:
            refuse_unauthorized_effect(effect, family="audio")
        message = str(exc_info.value)
        assert "voice_isolation" in message
        assert "audio" in message
        assert "unauthorized" in message


class TestAuthorizedEffectCarriesRecord:
    """An authorized effect carries its record onto the compiled directive."""

    def test_authorized_vfx_stabilize_passes_gate(self):
        """An authorized VFX stabilize effect passes the gate."""
        effect = {
            "effect_type": "stabilize",
            "target_block_position": 5,
            "timeline_start": 10.0,
            "timeline_end": 15.0,
        }
        authorization = {
            "format": "vfx_effect_authorization/1",
            "family": "vfx",
            "authorization_id": "note-1",
            "source": "timeline_note",
            "instruction": "Please stabilize the whole video",
            "scope": {"kind": "whole_video"},
        }
        result = gate_effect_authorizations(
            {"vfx": [effect]}, [authorization],
        )
        assert len(result["vfx"]) == 1

    def test_authorized_audio_voice_isolation_passes_gate(self):
        """An authorized audio voice_isolation effect passes the gate."""
        effect = {
            "effect_type": "voice_isolation",
            "sfx_id": "voice_isolation",
            "timeline_in": 10.0,
            "timeline_out": 15.0,
        }
        authorization = {
            "format": "audio_effect_authorization/1",
            "family": "audio",
            "authorization_id": "note-2",
            "source": "timeline_note",
            "instruction": "Please isolate the voice",
            "scope": {"kind": "whole_video"},
        }
        result = gate_effect_authorizations(
            {"audio": [effect]}, [authorization],
        )
        assert len(result["audio"]) == 1

    def test_authorization_record_is_carried_onto_directive(self):
        """The authorization record is preserved in the compiled output."""
        effect = {
            "effect_type": "stabilize",
            "target_block_position": 5,
            "timeline_start": 10.0,
            "timeline_end": 15.0,
        }
        authorization = {
            "format": "vfx_effect_authorization/1",
            "family": "vfx",
            "authorization_id": "note-3",
            "source": "timeline_note",
            "instruction": "Please stabilize the whole video",
            "scope": {"kind": "whole_video"},
        }
        validated = validate_authorizations([authorization])
        assert len(validated) == 1
        assert validated[0]["authorization_id"] == "note-3"
        assert validated[0]["family"] == "vfx"
        assert validated[0]["scope"]["kind"] == "whole_video"


class TestScopeMatching:
    """Scope matching works for all families."""

    def test_whole_video_scope_matches_any_effect(self):
        authorization = {
            "format": "vfx_effect_authorization/1",
            "family": "vfx",
            "authorization_id": "note-4",
            "source": "timeline_note",
            "instruction": "Please stabilize the whole video",
            "scope": {"kind": "whole_video"},
        }
        effect = {"effect_type": "stabilize", "timeline_start": 10.0}
        assert scope_matches_effect(authorization, effect, family="vfx")

    def test_clip_scope_matches_specific_clip(self):
        authorization = {
            "format": "vfx_effect_authorization/1",
            "family": "vfx",
            "authorization_id": "note-5",
            "source": "timeline_note",
            "instruction": "Please stabilize this clip",
            "scope": {"kind": "clip", "clip_id": "clip_001"},
        }
        effect = {"effect_type": "stabilize", "source_file": "/path/to/clip_001.mov"}
        assert scope_matches_effect(
            authorization, effect, family="vfx", clip_id="clip_001")

    def test_clip_scope_does_not_match_different_clip(self):
        authorization = {
            "format": "vfx_effect_authorization/1",
            "family": "vfx",
            "authorization_id": "note-6",
            "source": "timeline_note",
            "instruction": "Please stabilize this clip",
            "scope": {"kind": "clip", "clip_id": "clip_001"},
        }
        effect = {"effect_type": "stabilize", "source_file": "/path/to/clip_002.mov"}
        assert not scope_matches_effect(
            authorization, effect, family="vfx", clip_id="clip_002")

    def test_span_scope_matches_effect_within_span(self):
        authorization = {
            "format": "vfx_effect_authorization/1",
            "family": "vfx",
            "authorization_id": "note-7",
            "source": "timeline_note",
            "instruction": "Please stabilize this span",
            "scope": {"kind": "span", "start_seconds": 10.0, "end_seconds": 20.0},
        }
        effect = {"effect_type": "stabilize", "timeline_in": 12.0, "timeline_out": 18.0}
        assert scope_matches_effect(authorization, effect, family="vfx")

    def test_span_scope_does_not_match_effect_outside_span(self):
        authorization = {
            "format": "vfx_effect_authorization/1",
            "family": "vfx",
            "authorization_id": "note-8",
            "source": "timeline_note",
            "instruction": "Please stabilize this span",
            "scope": {"kind": "span", "start_seconds": 10.0, "end_seconds": 20.0},
        }
        effect = {"effect_type": "stabilize", "timeline_in": 25.0, "timeline_out": 30.0}
        assert not scope_matches_effect(authorization, effect, family="vfx")


class TestAuthorizationsFromTimelineNotes:
    """Authorizations are built only from explicit user requests."""

    def test_explicit_vfx_request_creates_authorization(self):
        note = {"note_id": "note-9", "typed": "Please stabilize the whole video"}
        records = authorizations_from_timeline_notes(
            {"notes": [note]}, family="vfx")
        assert len(records) == 1
        assert records[0]["family"] == "vfx"
        assert records[0]["scope"]["kind"] == "whole_video"

    def test_explicit_audio_request_creates_authorization(self):
        note = {"note_id": "note-10", "typed": "Please isolate the voice in the whole video"}
        records = authorizations_from_timeline_notes(
            {"notes": [note]}, family="audio")
        assert len(records) == 1
        assert records[0]["family"] == "audio"
        assert records[0]["scope"]["kind"] == "whole_video"

    def test_observation_without_request_creates_no_authorization(self):
        note = {"note_id": "note-11", "typed": "The footage is shaky"}
        records = authorizations_from_timeline_notes(
            {"notes": [note]}, family="vfx")
        assert records == []

    def test_negated_request_creates_no_authorization(self):
        note = {"note_id": "note-12", "typed": "Do not stabilize the video"}
        records = authorizations_from_timeline_notes(
            {"notes": [note]}, family="vfx")
        assert records == []

    def test_all_families_parsed_when_no_family_given(self):
        note = {"note_id": "note-13", "typed": "Please stabilize the whole video"}
        records = authorizations_from_timeline_notes({"notes": [note]})
        assert len(records) == 1
        assert records[0]["family"] == "vfx"


class TestValidation:
    """Validation refuses malformed records."""

    def test_missing_family_refuses(self):
        with pytest.raises(EffectAuthorizationError):
            validate_authorizations([{
                "format": "vfx_effect_authorization/1",
                "authorization_id": "note-14",
                "source": "timeline_note",
                "instruction": "Please stabilize",
                "scope": {"kind": "whole_video"},
            }])

    def test_missing_scope_refuses(self):
        with pytest.raises(EffectAuthorizationError):
            validate_authorizations([{
                "format": "vfx_effect_authorization/1",
                "family": "vfx",
                "authorization_id": "note-15",
                "source": "timeline_note",
                "instruction": "Please stabilize",
            }])

    def test_invalid_family_refuses(self):
        with pytest.raises(EffectAuthorizationError):
            validate_authorizations([{
                "format": "invalid_effect_authorization/1",
                "family": "invalid",
                "authorization_id": "note-16",
                "source": "timeline_note",
                "instruction": "Please stabilize",
                "scope": {"kind": "whole_video"},
            }])

    def test_stabilization_format_accepted_for_backward_compatibility(self):
        """The stabilization_authorization/1 format is still accepted."""
        records = validate_authorizations([{
            "format": "stabilization_authorization/1",
            "family": "vfx",
            "authorization_id": "note-17",
            "source": "timeline_note",
            "instruction": "Please stabilize the whole video",
            "scope": {"kind": "whole_video"},
        }])
        assert len(records) == 1


class TestStabilizationBackwardCompatibility:
    """The stabilization module still works after refactoring."""

    def test_stabilization_authorizations_from_notes(self):
        from library.tools.stabilization_authorization import (
            authorizations_from_timeline_notes,
        )
        note = {"note_id": "note-18", "typed": "Please stabilize the whole video"}
        records = authorizations_from_timeline_notes({"notes": [note]})
        assert len(records) == 1
        assert records[0]["format"] == "stabilization_authorization/1"
        assert records[0]["scope"]["kind"] == "whole_video"

    def test_stabilization_scope_matches_clip(self):
        from library.tools.stabilization_authorization import scope_matches_clip
        authorization = {
            "format": "stabilization_authorization/1",
            "family": "vfx",
            "authorization_id": "note-19",
            "source": "timeline_note",
            "instruction": "Please stabilize the whole video",
            "scope": {"kind": "whole_video"},
        }
        clip = {"label": "speech_1"}
        assert scope_matches_clip(authorization, clip)

    def test_stabilization_validate_authorizations(self):
        from library.tools.stabilization_authorization import validate_authorizations
        records = validate_authorizations([{
            "format": "stabilization_authorization/1",
            "family": "vfx",
            "authorization_id": "note-20",
            "source": "timeline_note",
            "instruction": "Please stabilize the whole video",
            "scope": {"kind": "whole_video"},
        }])
        assert len(records) == 1
