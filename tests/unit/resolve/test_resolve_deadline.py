"""Deadlines and read-back for the reel timeline resolution writes."""

import threading

import pytest

from library.tools.resolve_deadline import (
    deadline_proxy,
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


def test_deadline_proxy_wraps_nested_resolve_handles(monkeypatch):
    from library.tools import resolve_deadline

    child = type("Child", (), {"GetName": lambda self: "Q Reel"})()

    class Root:
        def __init__(self):
            self.child = None

        def GetChildren(self):
            return {"video": [child]}

        def SetChild(self, value):
            self.child = value
            return True

    calls = []

    def immediate(label, function, *args, timeout_s=None, **kwargs):
        calls.append((label, timeout_s))
        return function(*args, **kwargs)

    monkeypatch.setattr(resolve_deadline, "call_with_deadline", immediate)
    raw_root = Root()
    root = deadline_proxy(raw_root, label="qualification", timeout_s=7)

    assert root.GetChildren()["video"][0].GetName() == "Q Reel"
    wrapped_child = root.GetChildren()["video"][0]
    assert root.SetChild(wrapped_child) is True
    assert raw_root.child is child
    assert calls == [
        ("qualification.GetChildren", 7),
        ("qualification.GetChildren['video'][0].GetName", 7),
        ("qualification.GetChildren", 7),
        ("qualification.SetChild", 7),
    ]


def test_deadline_proxy_handles_api_objects_with_spoofed_class(monkeypatch):
    from library.tools import resolve_deadline

    class ApiHandle:
        @property
        def __class__(self):
            return None

        def GetName(self):
            return "Ren Qualification"

    class Root:
        def GetProjectManager(self):
            return ApiHandle()

    def immediate(_label, function, *args, timeout_s=None, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(resolve_deadline, "call_with_deadline", immediate)
    wrapped = deadline_proxy(Root(), label="reel.promote_staged")

    assert (wrapped.GetProjectManager().GetName()
            == "Ren Qualification")


def test_deadline_proxy_forwards_method_keyword_named_label():
    class Root:
        def SetInput(self, key, value, label=None):
            return key, value, label

    wrapped = deadline_proxy(Root(), label="Resolve")
    assert wrapped.SetInput("Gain", 1.0, label="highlight") == (
        "Gain", 1.0, "highlight")


def test_deadline_proxy_names_a_blocked_api_method():
    release = threading.Event()
    finished = threading.Event()

    class Root:
        def GetProjectManager(self):
            try:
                release.wait()
                return object()
            finally:
                finished.set()

    wrapped = deadline_proxy(Root(), label="reel.promote_staged",
                              timeout_s=0.01)
    try:
        with pytest.raises(ResolveCallTimeout,
                           match="reel.promote_staged.GetProjectManager"):
            wrapped.GetProjectManager()
    finally:
        release.set()
        assert finished.wait(1)
