"""Window tracking: the Tracker press and the coverage verdict.

Each test names the defect it catches. Coverage runs on synthetic
stills (PIL + numpy, in-process - no renders, no Resolve), so the
whole file runs offline. The live proof presses the measured menu on
the qualification clips.
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image, ImageDraw

from library.tools.computer_use.client import DriverClient
from library.tools.computer_use.scripts import (
    ScriptError,
)
from library.tools.computer_use import window_track
from library.tools.computer_use.window_track import (
    build_window_track_script,
    verify_window_coverage,
)

W, H = 160, 90


def _frame(path, disc=None, level=128, boost=60):
    img = Image.new("L", (W, H), level)
    if disc is not None:
        draw = ImageDraw.Draw(img)
        x, y, r = disc
        draw.ellipse([x - r, y - r, x + r, y + r], fill=level + boost)
    img.save(path)


def _stills(tmp_path, second="same", shift=0):
    base = tmp_path / "base.png"
    regrab = tmp_path / "regrab.png"
    ref = tmp_path / "ref.png"
    other = tmp_path / "other.png"
    _frame(base)
    _frame(regrab)
    _frame(ref, disc=(80, 45, 20))
    if second == "same":
        _frame(other, disc=(80, 45, 20))
    elif second == "vanished":
        _frame(other)
    elif second == "shifted":
        _frame(other, disc=(80 + shift, 45, 20))
    return base, regrab, ref, other


def test_held_coverage_passes(tmp_path):
    """The tracked window stays: identical sampled frames verify."""
    base, regrab, ref, other = _stills(tmp_path)
    verdict = verify_window_coverage(
        baseline=str(base), regrab=str(regrab),
        windowed=[str(ref), str(other)])
    assert verdict["passed"] is True
    assert verdict["coverage"][1] == pytest.approx(1.0)
    assert verdict["centroid_walk"][1] == pytest.approx(0.0)


def test_a_vanished_window_fails(tmp_path):
    """Defect: a tracker that lost the window would verify clean if
    presence were never checked - the empty frame fails."""
    base, regrab, ref, other = _stills(tmp_path, second="vanished")
    verdict = verify_window_coverage(
        baseline=str(base), regrab=str(regrab),
        windowed=[str(ref), str(other)])
    assert verdict["passed"] is False
    assert verdict["coverage"][1] == pytest.approx(0.0)


def test_a_fully_shifted_window_fails(tmp_path):
    """Defect: a window that drifted off the subject overlaps nothing
    of the reference - coverage 0 fails."""
    base, regrab, ref, other = _stills(tmp_path, second="shifted", shift=80)
    verdict = verify_window_coverage(
        baseline=str(base), regrab=str(regrab),
        windowed=[str(ref), str(other)])
    assert verdict["passed"] is False


def test_a_reference_showing_no_window_is_not_coverage(tmp_path):
    """Defect: an undrawn overlay is not rendered - judging empty
    frames against each other would pass a window that was never
    there. The empty reference refuses the verdict."""
    base, regrab, _, _ = _stills(tmp_path)
    verdict = verify_window_coverage(
        baseline=str(base), regrab=str(regrab),
        windowed=[str(base), str(regrab)])
    assert verdict["passed"] is False
    assert verdict["reason"] == "reference shows no window"


def test_mixed_still_sizes_are_refused(tmp_path):
    """Defect: comparing frames of two geometries measures the crop,
    not the track. Refused."""
    base, regrab, ref, _ = _stills(tmp_path)
    small = tmp_path / "small.png"
    Image.new("L", (80, 45), 128).save(small)
    with pytest.raises(ScriptError, match="geometry"):
        verify_window_coverage(
            baseline=str(base), regrab=str(regrab),
            windowed=[str(ref), str(small)])


def test_no_sampled_frames_is_refused():
    """Coverage with no frames judges nothing."""
    with pytest.raises(ScriptError, match="no sampled frames"):
        verify_window_coverage(baseline="a", regrab="b", windowed=[])


def test_the_builder_presses_the_direction_s_menu_item():
    """Forward presses Color > Tracker > Forward; an invented
    direction is refused before anything moves."""
    script = build_window_track_script("forward")
    assert script.steps[0].anchors[0].params["path"] == [
        "Color", "Tracker", "Forward"]
    reverse = build_window_track_script("reverse")
    assert reverse.steps[0].anchors[0].params["path"][-1] == "Reverse"
    with pytest.raises(ScriptError, match="backward"):
        build_window_track_script("backward")


def test_the_track_runner_refuses_an_unheld_clip(client: DriverClient):
    """A track naming a clip the project does not hold presses nothing."""
    class _Project:
        def GetName(self):
            return "Ren Qualification"

        def GetTimelineCount(self):
            return 0

    class _Manager:
        def LoadProject(self, _name):
            return _Project()

        def GetCurrentProject(self):
            return _Project()

    class _Resolve:
        def GetProjectManager(self):
            return _Manager()

        def OpenPage(self, _page):
            return True

    with pytest.raises(ScriptError, match="NO_CLIP|no clip"):
        window_track.run_window_track(
            client, _Resolve(),
            {"unique_id": "missing", "direction": "forward"})
