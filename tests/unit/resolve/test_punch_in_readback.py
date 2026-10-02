"""The punch-in is read back, not trusted - and refusal names the shot.

Reel 09, 2026-09-09: the staging the gate deleted carried six items at
the identity transform, each delivering (0, 656, 1080, 1264) against a
screen window of (18, 260, 1061, 1661).  `aim_picture_row` is the punch
pass extracted testable, with PR 862's discipline applied to it:
`SetProperty` returns True past Resolve's silent Pan/Tilt clamp, so
what was ASKED is judged by its return and what is HELD is graded
against the window with the same predicate F12 grades with
(`reel_look.uncovered_window_edges`).  A transform that did not take
raises `PunchInLeavesBlack` at placement - never as six identical
findings after a full build - and an incapacitated probe
(`SubjectProbeUnavailable`) becomes a `ReelLookRefused` naming the
shot, instead of six silent uncropped items the gate deletes.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from library.tools import reel_look
from library.tools.reel_build import aim_picture_row
from library.tools.subject_framing import SubjectProbeUnavailable


def _frame_asset(path):
    """A 1080x1920 frame asset with a transparent centre window."""
    from PIL import Image
    image = Image.new("RGBA", (1080, 1920), (10, 10, 10, 255))
    for x in range(100, 980):
        for y in range(100, 1820, 4):
            image.putpixel((x, y), (0, 0, 0, 0))
    image.save(path)
    return str(path)


def _look(path):
    from library.tools import tv_frame
    return {"asset": path, "punch_in": 2.3, "power": {},
            "origin": "test declaration",
            "rotate": tv_frame.AUTO_ROTATE}


def _window(look):
    from library.tools.tv_frame import screen_window_rect
    return screen_window_rect(look, 1080, 1920)


def _subject(cx=0.5, cy=0.32):
    return SimpleNamespace(center_x=cx, center_y=cy, width=0.1,
                           samples=12, detected=12, others=0)


def _place(source="/x/LC4932.MXF", start=0.0, end=5.0, record=0):
    return {"clip": SimpleNamespace(source_file=source),
            "source_in": start, "source_out": end,
            "snapped_record": record}


class _Item:
    """A timeline item. No `held` echoes what was set; a `held` dict
    simulates Resolve holding something else (the silent clamp)."""

    def __init__(self, held=None):
        self._set = {}
        self._held = held

    def SetProperty(self, key, value):
        self._set[key] = value
        return True

    def GetProperty(self, key=None):
        values = self._held if self._held is not None else self._set
        if key is None:
            return dict(values)
        return values.get(key)

    def GetName(self):
        return "shot"


def test_an_incapacitated_probe_refuses_the_row_naming_the_shot(tmp_path):
    """The build refuses with the cause; no doomed staging is shipped."""
    asset = _frame_asset(tmp_path / "frame.png")
    look = _look(asset)

    def _no_detector(source, start, end):
        raise SubjectProbeUnavailable("no face detector (test)")

    with pytest.raises(reel_look.ReelLookRefused) as excinfo:
        aim_picture_row("Reel 09", look, _window(look), 1080, 1920,
                        [_Item()], [_place()],
                        measure=_no_detector,
                        size_of=lambda item: (3840, 2160))
    assert "LC4932.MXF" in str(excinfo.value)


def test_a_transform_resolve_did_not_hold_is_refused(tmp_path):
    """The Reel 09 staging shape: asked for a punch-in, holding identity.

    The item reports success on every `SetProperty` but reads back the
    identity transform - what the failed staging delivered on all six
    items.  The read-back grades the HELD picture against the window
    and raises, instead of shipping six identical strips to the gate.
    """
    asset = _frame_asset(tmp_path / "frame.png")
    look = _look(asset)
    identity = {"ZoomX": 1.0, "ZoomY": 1.0, "Pan": 0.0, "Tilt": 0.0}
    with pytest.raises(reel_look.PunchInLeavesBlack) as excinfo:
        aim_picture_row("Reel 09", look, _window(look), 1080, 1920,
                        [_Item(held=dict(identity))], [_place()],
                        measure=lambda s, a, b: _subject(),
                        size_of=lambda item: (3840, 2160))
    assert "did not take" in str(excinfo.value)


def test_a_shot_with_no_face_plays_uncropped_and_is_said(
        tmp_path, capsys):
    """Genuine absence still refuses the punch, not the build.

    A detector that looked and found no face returns None, and the shot
    plays uncropped with the reason SAID - the captain's refuse-rather-
    than-guess ruling, unchanged.  (Under the look the gate will still
    grade that uncropped shot; what changed is only that an
    incapacitated probe can no longer wear this ruling's clothes.)
    """
    asset = _frame_asset(tmp_path / "frame.png")
    look = _look(asset)
    aimed = aim_picture_row(
        "Reel 09", look, _window(look), 1080, 1920,
        [_Item()], [_place()],
        measure=lambda s, a, b: None,
        size_of=lambda item: (3840, 2160))
    assert aimed == 0
    assert "NO PUNCH-IN" in capsys.readouterr().err
