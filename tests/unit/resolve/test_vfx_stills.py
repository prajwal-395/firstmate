"""4.03 sees the picture its effects land on.

History: docs/evidence/resolve_test_history.md#test_vfx_stills.
"""
import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.steps.step_4_03_plan_vfx import bridge as vfx_bridge  # noqa: E402

SPINE = {"structure": [
    {"position": 1, "block_type": "speech", "clip_id": "clip_001",
     "source_start": 100.0, "source_end": 110.0,
     "timeline_start": 8.0, "timeline_end": 18.0},
    {"position": 2, "block_type": "speech", "clip_id": "clip_002",
     "source_start": 200.0, "source_end": 204.0,
     "timeline_start": 18.0, "timeline_end": 22.0},
    {"position": 3, "block_type": "transition_slot",
     "timeline_start": 22.0, "timeline_end": 24.0},
]}

AROLL = [{"video_segments": [
    {"clip_id": "clip_001", "source_file": "/nowhere/a.mp4"},
    {"clip_id": "clip_002", "source_file": "/nowhere/b.mp4"},
]}]

TEMPORAL = [
    {"clip_id": "clip_001", "motion_method": "farneback",
     "motion_peaks": [
         {"time": 99.0, "kind": "apex", "magnitude": 0.9},
         {"time": 103.0, "kind": "onset", "magnitude": 0.4},
         {"time": 104.0, "kind": "apex", "magnitude": 0.7}]},
    {"clip_id": "clip_002", "motion_method": "farneback",
     "motion_peaks": []},
]


def _data(**over):
    payload = {"timed_spine": SPINE, "a_roll_assignments": AROLL,
               "b_roll_assignments": [],
               "temporal_event_indices": TEMPORAL}
    payload.update(over)
    return payload


def test_still_moment_prefers_an_apex_then_the_middle():
    motion = vfx_bridge._motion_by_clip(_data())
    at, basis = vfx_bridge._block_still_moment(
        SPINE["structure"][0], "clip_001", motion)
    # The 99.0 apex sits outside the 100-110 range: skipped.
    assert (at, basis) == (104.0, "apex")
    # No apex in range: the middle. No range at all: unranged.
    assert vfx_bridge._block_still_moment(
        SPINE["structure"][1], "clip_002", motion) == (202.0, "middle")
    assert vfx_bridge._block_still_moment(
        SPINE["structure"][2], None, motion) == (None, "unranged")


def test_draw_names_what_it_could_not_draw(tmp_path, monkeypatch):
    """Two stills drawn at the right seconds; the rangeless block named."""
    import library.steps.step_4_03_plan_vfx.bridge as bridge_mod

    drawn = {}

    def fake_extract(source, out_path, at_seconds=None):
        drawn[out_path] = (source, at_seconds)
        Path(out_path).write_bytes(b"fake-jpeg")
        return True

    monkeypatch.setattr(bridge_mod, "extract_still", fake_extract)
    block, paths = vfx_bridge.draw_vfx_stills(
        _data(), str(tmp_path))
    assert len(paths) == 2
    assert drawn[paths[0]] == ("/nowhere/a.mp4", 104.0)
    assert drawn[paths[1]] == ("/nowhere/b.mp4", 202.0)
    assert "block_1__vfx.jpg" in block
    assert "NOT DRAWN: 3 (no source clip)" in block
    # Without a project folder nothing is drawn.
    assert vfx_bridge.draw_vfx_stills(_data(), "") == ("", [])


def test_observe_states_whose_vision_answered(monkeypatch, tmp_path):
    import library.steps.step_4_03_plan_vfx.bridge as bridge_mod

    still = tmp_path / "block_1__vfx.jpg"
    still.write_bytes(b"fake-jpeg")
    monkeypatch.setattr(
        bridge_mod.still_router, "inspect_stills",
        lambda *a, **k: "block_1__vfx.jpg: still, judged on the wall")
    monkeypatch.setattr(
        bridge_mod.still_router, "resolve_harness", lambda: None)
    notes = vfx_bridge.observe_vfx_stills([str(still)], str(tmp_path))
    assert notes["observed_by"].startswith("gemma4 fallback")
    assert "still" in notes["text"]
    # And why nothing answered, when nothing did.
    notes = vfx_bridge.observe_vfx_stills([], "/nowhere")
    assert notes["observed_by"] == "none"
    assert "no stills were drawn" in notes["reason"]
    notes = vfx_bridge.observe_vfx_stills(["/nowhere/x.jpg"], "")
    assert "no project_folder" in notes["reason"]


def test_bridge_emits_declared_still_keys(tmp_path, monkeypatch,
                                          tmp_path_factory=None):
    """The bridge output carries the stills beside the candidate table."""
    import subprocess

    proc = subprocess.run(
        [sys.executable, "library/steps/step_4_03_plan_vfx/bridge.py"],
        input=json.dumps(_data()), capture_output=True,
        encoding="utf-8", cwd=str(PROJECT_ROOT), check=False,
        env={"PYTHONPATH": str(PROJECT_ROOT), "PATH": "/usr/bin:/bin"})
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert "vfx_candidates_toon" in out
    # No project_folder: nothing drawn, absence stated.
    assert out["vfx_shot_stills"] == ""
    assert out["still_motion_notes"]["observed_by"] == "none"
