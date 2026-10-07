"""Per-clip Voice Isolation: set on the item, judged by re-read.

The defect these tests pin: isolation had only the track call, so a
single noisy clip meant isolating its whole track (or a stem, or a
click). Now `apply_clip_voice_isolation` sets the two
`TimelineItemProperties` keys through `SetProperties` and claims the
write only when `GetProperties` re-reads them equal. No Resolve, no
model: fakes judge every path, including the older Resolve whose
properties lack the keys.
"""
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.dialogue_cleanup import (
    CLIP_VI_PROPERTY_KEYS,
    DialogueCleanupRefused,
    apply_clip_voice_isolation,
)


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
    return _Clip({"AudioVoiceIsolationEnabled": False,
                  "AudioVoiceIsolationAmount": 0,
                  "AudioVolume": 0.0})


def test_apply_sets_and_reads_back_equal():
    clip = _modern_clip()
    record = apply_clip_voice_isolation(clip, 60)
    assert record == {"amount": 60, "verified": "re-reads equal"}
    assert clip.set_calls == [{"AudioVoiceIsolationEnabled": True,
                               "AudioVoiceIsolationAmount": 60}]


def test_apply_reads_back_ints_as_booleans():
    """Resolve may re-read a boolean as 0/1 - the read-back compares
    by value, never by identity, or every real write would fail."""

    class _IntClip(_Clip):
        def GetProperties(self):
            return {key: (1 if value is True else
                          0 if value is False else value)
                    for key, value in self._store.items()}

    clip = _IntClip({"AudioVoiceIsolationEnabled": False,
                     "AudioVoiceIsolationAmount": 0})
    record = apply_clip_voice_isolation(clip, 60)
    assert record["verified"] == "re-reads equal"


def test_amount_endpoints_validate():
    for amount in (0, 100):
        clip = _modern_clip()
        assert apply_clip_voice_isolation(clip, amount)["amount"] == amount


def test_an_out_of_range_amount_is_refused():
    with pytest.raises(DialogueCleanupRefused, match="outside"):
        apply_clip_voice_isolation(_modern_clip(), 120)


def test_a_non_numeric_amount_is_refused():
    with pytest.raises(DialogueCleanupRefused, match="not a number"):
        apply_clip_voice_isolation(_modern_clip(), "a lot")


def test_a_declined_write_is_refused():
    clip = _modern_clip()
    clip._write_answers = False
    with pytest.raises(DialogueCleanupRefused, match="answered False"):
        apply_clip_voice_isolation(clip, 60)


def test_a_read_back_mismatch_is_refused():
    clip = _modern_clip()
    original = clip.GetProperties

    def _frozen():
        state = original()
        state["AudioVoiceIsolationAmount"] = 0
        return state

    clip.GetProperties = _frozen
    with pytest.raises(DialogueCleanupRefused, match="re-reads"):
        apply_clip_voice_isolation(clip, 60)


def test_a_resolve_without_isolation_keys_is_refused_by_name():
    clip = _Clip({"AudioVolume": 0.0})
    with pytest.raises(DialogueCleanupRefused,
                       match="AudioVoiceIsolationEnabled"):
        apply_clip_voice_isolation(clip, 60)
