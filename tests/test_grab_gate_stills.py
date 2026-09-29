"""The gate-stills entry point refuses what it cannot do.

`grab_gate_stills` (reached as `reel.gate_stills`) grabs named frames
off one built reel timeline into the project's own gate-stills
directory. These tests pin its boundaries without grabbing anything
and without connecting to Resolve: malformed input raises rather than
being guessed at, and an unavailable Resolve returns a report saying
so - never an empty success. The refusal test injects a fake scripting
module so it cannot hang on a live Resolve handshake.
"""

from __future__ import annotations

import sys

import pytest

import library.steps.step_7_02_verify_reels.step as v702
from library.tools import operations
from library.tools import resolve_lock


def test_no_frames_is_refused(tmp_path):
    with pytest.raises(ValueError, match="at least one frame"):
        v702.grab_gate_stills(str(tmp_path), "reel21", "Reel 21", [])


def test_without_resolve_the_refusal_returns_not_raises(tmp_path, monkeypatch):
    monkeypatch.setenv(resolve_lock.LOCK_DIR_ENV, str(tmp_path / "resolve"))
    monkeypatch.setattr(resolve_lock, "_depth", 0)
    monkeypatch.setattr(resolve_lock, "_mode", None)
    monkeypatch.setattr(resolve_lock, "_sole_writer_reason", None)

    calls = []

    class ResolveUnavailable:
        @staticmethod
        def scriptapp(name):
            calls.append(name)
            return None

    # Keep the lease and the real connection wrapper in the path, but
    # make Resolve unavailable at the scripting-module boundary. This
    # avoids a live handshake, which can block indefinitely.
    monkeypatch.setitem(sys.modules, "DaVinciResolveScript",
                        ResolveUnavailable)

    report = v702.grab_gate_stills(str(tmp_path), "reel21", "Reel 21", [20])
    assert report["ok"] is False
    assert report["stills"] == []
    assert report["error"]
    assert len(report["failed"]) == 1
    assert report["failed"][0]["reel_frame"] == 20
    # The bank directory exists even so: the refusal is about Resolve,
    # never about where the stills would have gone.
    assert (tmp_path / "pipeline_output" / "steps"
            / "7_02_verify_reels" / "gate_stills").is_dir()
    assert calls == ["Resolve"]
