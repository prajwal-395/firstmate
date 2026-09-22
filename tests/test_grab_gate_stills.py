"""The gate-stills entry point refuses what it cannot do.

`grab_gate_stills` (reached as `reel.gate_stills`) grabs named frames
off one built reel timeline into the project's own gate-stills
directory. These tests pin its boundaries without grabbing anything
and without Resolve: malformed input raises rather than being guessed
at, and an unreachable Resolve returns a report saying so - never an
empty success.
"""

from __future__ import annotations

import pytest

import library.steps.step_7_02_verify_reels.step as v702
from library.tools import operations


def test_no_project_folder_is_refused(tmp_path):
    with pytest.raises(ValueError, match="project_folder"):
        v702.grab_gate_stills("", "reel21", "Reel 21", [20])


def test_no_reel_label_is_refused(tmp_path):
    with pytest.raises(ValueError, match="reel_label"):
        v702.grab_gate_stills(str(tmp_path), "", "Reel 21", [20])


def test_no_timeline_name_is_refused(tmp_path):
    with pytest.raises(ValueError, match="timeline_name"):
        v702.grab_gate_stills(str(tmp_path), "reel21", "", [20])


def test_no_frames_is_refused(tmp_path):
    with pytest.raises(ValueError, match="at least one frame"):
        v702.grab_gate_stills(str(tmp_path), "reel21", "Reel 21", [])


def test_a_non_int_frame_is_refused(tmp_path):
    with pytest.raises(ValueError, match="not an int"):
        v702.grab_gate_stills(str(tmp_path), "reel21", "Reel 21", [20, "x"])


def test_a_bool_frame_is_refused(tmp_path):
    with pytest.raises(ValueError, match="not an int"):
        v702.grab_gate_stills(str(tmp_path), "reel21", "Reel 21", [True])


def test_without_resolve_the_refusal_returns_not_raises(tmp_path):
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


def test_the_registry_reaches_the_step_own_function():
    op = operations.get("reel.gate_stills")
    assert op.owning_node == "verify_reels"
    assert op.caller_supplied is True
    # By name and by file, not by identity: the registry loads the
    # step body under a synthetic module name, so it is the same code
    # under a different module object.
    assert op.run.__name__ == "grab_gate_stills"
    assert op.run.__code__.co_filename == v702.__file__
