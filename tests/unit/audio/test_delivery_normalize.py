"""Delivery normalization: master to the spec's numbers, or refuse.

The defect these tests pin: the delivery spec resolved targets and
the QA judged renders against them, but nothing in the engine applied
them - mastering happened by hand or not at all, and a mix could
report the spec's numbers without ever being mastered to them. Now
`normalize_items` validates the options against the keys and modes
this Resolve answers and claims the write only on True. No Resolve,
no model: fakes judge every path.
"""
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.delivery_normalize import (
    DeliveryNormalizeRefused,
    normalize_items,
    normalize_options_for_spec,
)
from library.tools.delivery_spec import DeliverySpec

MODES = ["Sample Peak Program", "Loudness Program"]


def _spec(**overrides):
    values = {
        "platform": "test",
        "width": 1920,
        "height": 1080,
        "fps": 24.0,
        "codec": "H264",
        "container": "mp4",
        "loudness_target_lufs": -14.0,
        "loudness_tolerance_lufs": 1.0,
        "true_peak_ceiling_dbtp": -1.0,
        "caption_format": "srt",
        "source": "test",
    }
    values.update(overrides)
    return DeliverySpec(**values)


class _Timeline:
    def __init__(self, answers=True):
        self._answers = answers
        self.calls = []

    def NormalizeAudioLevel(self, items, options):
        self.calls.append((list(items), dict(options)))
        if isinstance(self._answers, Exception):
            raise self._answers
        return self._answers


# ── the options ──────────────────────────────────────────────────────

def test_options_carry_the_specs_own_numbers():
    options = normalize_options_for_spec(_spec(), "Loudness Program",
                                         MODES)
    assert options == {"normalizationMode": "Loudness Program",
                       "targetLoudness": -14.0}


def test_a_mode_nothing_answers_is_refused():
    with pytest.raises(DeliveryNormalizeRefused, match="not among"):
        normalize_options_for_spec(_spec(), "My Ears", MODES)


def test_a_spec_without_a_target_is_refused():
    with pytest.raises(DeliveryNormalizeRefused, match="no loudness"):
        normalize_options_for_spec(_spec(loudness_target_lufs=None),
                                   "Loudness Program", MODES)


# ── the apply ────────────────────────────────────────────────────────

def test_apply_calls_with_the_planned_options():
    timeline = _Timeline()
    record = normalize_items(
        timeline, ["item-1"],
        {"normalizationMode": "Loudness Program",
         "targetLoudness": -14.0}, MODES)
    assert record["normalized"] is True
    assert timeline.calls[0][1]["targetLoudness"] == -14.0


def test_an_unknown_option_key_is_refused():
    with pytest.raises(DeliveryNormalizeRefused, match="unknown"):
        normalize_items(
            _Timeline(), ["item-1"],
            {"normalizationMode": "Loudness Program",
             "targetLoudness": -14.0, "targetLoudnes": -14.0}, MODES)


def test_a_mode_outside_the_live_list_is_refused():
    with pytest.raises(DeliveryNormalizeRefused, match="not among"):
        normalize_items(
            _Timeline(), ["item-1"],
            {"normalizationMode": "Loudness Program",
             "targetLoudness": -14.0}, ["Sample Peak Program"])


def test_no_items_means_no_mastering_claim():
    with pytest.raises(DeliveryNormalizeRefused, match="no timeline"):
        normalize_items(
            _Timeline(), [],
            {"normalizationMode": "Loudness Program",
             "targetLoudness": -14.0}, MODES)


def test_a_declined_write_is_refused():
    with pytest.raises(DeliveryNormalizeRefused, match="answered False"):
        normalize_items(
            _Timeline(answers=False), ["item-1"],
            {"normalizationMode": "Loudness Program",
             "targetLoudness": -14.0}, MODES)


def test_a_raising_call_is_refused():
    with pytest.raises(DeliveryNormalizeRefused, match="raised"):
        normalize_items(
            _Timeline(answers=RuntimeError("busy")), ["item-1"],
            {"normalizationMode": "Loudness Program",
             "targetLoudness": -14.0}, MODES)
