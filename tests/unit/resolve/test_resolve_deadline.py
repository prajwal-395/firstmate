"""Deadlines and read-back for the reel timeline resolution writes."""

import threading

import pytest

from library.tools.resolve_deadline import (
    ResolveCallTimeout,
    apply_timeline_resolution,
)
from tests.resolve_double import FakeTimeline


RESOLUTION_WRITES = [
    ("useCustomSettings", "1"),
    ("timelineResolutionWidth", "1920"),
    ("timelineResolutionHeight", "1080"),
]


def test_resolution_is_written_in_order_and_read_back():
    timeline = FakeTimeline()
    calls = []
    original_set_setting = timeline.SetSetting

    def record_setting(key, value):
        calls.append((key, value))
        return original_set_setting(key, value)

    timeline.SetSetting = record_setting

    apply_timeline_resolution(timeline, 1920, 1080)

    assert calls == RESOLUTION_WRITES
    assert timeline.GetSetting("useCustomSettings") == "1"
    assert timeline.GetSetting("timelineResolutionWidth") == "1920"
    assert timeline.GetSetting("timelineResolutionHeight") == "1080"


def test_a_blocked_setting_is_reported_by_key():
    timeline = FakeTimeline()
    release = threading.Event()
    finished = threading.Event()
    original_set_setting = timeline.SetSetting

    def block_width(key, value):
        if key != "timelineResolutionWidth":
            return original_set_setting(key, value)
        try:
            release.wait()
            return original_set_setting(key, value)
        finally:
            finished.set()

    timeline.SetSetting = block_width
    try:
        with pytest.raises(ResolveCallTimeout,
                           match="timelineResolutionWidth"):
            apply_timeline_resolution(timeline, 1920, 1080, timeout_s=0.01)
    finally:
        release.set()
        assert finished.wait(1)


def test_a_blocked_readback_is_reported_by_key():
    timeline = FakeTimeline()
    release = threading.Event()
    finished = threading.Event()
    original_get_setting = timeline.GetSetting

    def block_width(key=None):
        if key != "timelineResolutionWidth":
            return original_get_setting(key)
        try:
            release.wait()
            return original_get_setting(key)
        finally:
            finished.set()

    timeline.GetSetting = block_width
    try:
        with pytest.raises(ResolveCallTimeout,
                           match="timeline GetSetting timelineResolutionWidth"):
            apply_timeline_resolution(timeline, 1920, 1080, timeout_s=0.01)
    finally:
        release.set()
        assert finished.wait(1)
