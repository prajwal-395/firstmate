"""Dialogue Leveler application: set per clip, judged by re-read.

The defect these tests pin: per-clip dynamics had no API-applied
route - Voice Isolation owns the track call, and anything else was a
stem or a UI click. Now `apply_dialogue_leveler` sets the six
`TimelineItemProperties` keys through `SetProperties` and claims the
write only when `GetProperties` re-reads them equal, with the same
refusal discipline as Voice Isolation. No Resolve, no model: fakes
judge every path, including the older Resolve whose properties lack
the leveler keys and the EQ band key that refuses as unknown (that
refusal is the recorded measurement that track EQ has no scripting
API).
"""
import math
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.dialogue_cleanup import (
    LEVELER_PROPERTY_KEYS,
    DialogueCleanupRefused,
    apply_dialogue_leveler,
    validate_leveler_properties,
)


def _props(**overrides):
    props = {
        "AudioDialogueLevelerEnabled": True,
        "AudioDialogueLevelerMode": 1.0,
        "AudioDialogueLevelerReduceLoudDialogue": True,
        "AudioDialogueLevelerLiftSoftDialogue": True,
        "AudioDialogueLevelerBackgroundReduction": False,
        "AudioDialogueLevelerOutputGain": 0.0,
    }
    props.update(overrides)
    return props


class _Clip:
    """A timeline item double: property store with read-back."""

    def __init__(self, initial, write_answers=True):
        self._store = dict(initial)
        self._write_answers = write_answers
        self.set_calls = []

    def GetProperties(self):
        return dict(self._store)

    def SetProperties(self, properties):
        self.set_calls.append(dict(properties))
        if self._write_answers:
            self._store.update(properties)
        return self._write_answers


def _modern_clip():
    return _Clip({key: (False if isinstance(_props()[key], bool)
                        else 0.0)
                  for key in LEVELER_PROPERTY_KEYS})


# ── validation ───────────────────────────────────────────────────────

def test_valid_properties_pass_through_verbatim():
    assert validate_leveler_properties(_props()) == _props()


def test_unknown_keys_refuse_naming_them():
    props = _props(EQBand1Frequency=80.0)
    with pytest.raises(DialogueCleanupRefused, match="EQBand1Frequency"):
        validate_leveler_properties(props)


def test_missing_keys_refuse():
    props = _props()
    del props["AudioDialogueLevelerMode"]
    with pytest.raises(DialogueCleanupRefused, match="missing"):
        validate_leveler_properties(props)


def test_a_non_boolean_switch_refuses():
    with pytest.raises(DialogueCleanupRefused, match="not a boolean"):
        validate_leveler_properties(_props(
            AudioDialogueLevelerReduceLoudDialogue="yes"))


def test_gain_outside_resolves_scale_refuses():
    with pytest.raises(DialogueCleanupRefused, match="outside"):
        validate_leveler_properties(_props(
            AudioDialogueLevelerOutputGain=12.0))


def test_gain_endpoints_validate():
    for gain in (0.0, 6.0):
        assert validate_leveler_properties(_props(
            AudioDialogueLevelerOutputGain=gain))[
                "AudioDialogueLevelerOutputGain"] == gain


# ── application ──────────────────────────────────────────────────────

def test_apply_sets_and_reads_back_equal():
    clip = _modern_clip()
    record = apply_dialogue_leveler(clip, _props())
    assert record["verified"] == "re-reads equal"
    assert len(clip.set_calls) == 1
    assert clip.GetProperties()["AudioDialogueLevelerEnabled"] is True


def test_apply_reads_back_ints_as_booleans():
    """Resolve may re-read a boolean as 0/1 - the read-back compares
    by value, never by identity, or every real write would fail."""

    class _IntClip(_Clip):
        def GetProperties(self):
            return {key: (1 if value is True else
                          0 if value is False else value)
                    for key, value in self._store.items()}

    clip = _IntClip({key: False for key in LEVELER_PROPERTY_KEYS})
    record = apply_dialogue_leveler(clip, _props())
    assert record["verified"] == "re-reads equal"


def test_a_declined_write_is_refused():
    clip = _modern_clip()
    clip._write_answers = False
    with pytest.raises(DialogueCleanupRefused, match="answered False"):
        apply_dialogue_leveler(clip, _props())


def test_a_read_back_mismatch_is_refused():
    clip = _modern_clip()
    original = clip.GetProperties

    def _frozen():
        state = original()
        state["AudioDialogueLevelerOutputGain"] = 0.0
        return state

    clip.GetProperties = _frozen
    with pytest.raises(DialogueCleanupRefused, match="re-reads"):
        apply_dialogue_leveler(
            clip, _props(AudioDialogueLevelerOutputGain=3.0))


def test_a_resolve_without_leveler_keys_is_refused_by_name():
    clip = _Clip({"AudioVolume": 0.0})
    with pytest.raises(DialogueCleanupRefused,
                       match="AudioDialogueLevelerEnabled"):
        apply_dialogue_leveler(clip, _props())
