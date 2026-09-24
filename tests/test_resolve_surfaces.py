"""A grade is measured on an EXPORT, and the roster refuses a viewer.

`library/tools/resolve_surfaces.py` is the enumeration of what each
DaVinci Resolve surface shows.  It exists because the Fusion page and the
Edit page draw ONE frame's pixels through two different viewers, and a
lane that samples the wrong one grades against a preview - which is what
the captain traced his over-cranked parameters to on 2026-09-10.

These tests need no Resolve: the roster is data, and the guard is a
refusal.  Each one can FAIL (AGENTS.md 10.4) - the last one proves that
by pointing the still capture at a viewer and requiring the refusal.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from library.tools import resolve_surfaces as rs


def test_both_pages_are_named_and_neither_of_them_ships():
    """The two surfaces the captain compared are both previews.

    Naming only one of them would leave the other looking authoritative,
    and the Fusion page matching the file's numbers today is a
    coincidence of it being unmanaged - not a licence to measure on it.
    """
    assert rs.SURFACES[rs.EDIT_PAGE_VIEWER].ships is False
    assert rs.SURFACES[rs.FUSION_PAGE_VIEWER].ships is False
    assert not rs.ships(rs.COLOR_PAGE_VIEWER)


def test_exactly_the_two_exports_ship():
    """A grade may be measured on an export and on nothing else."""
    shipping = {name for name, fact in rs.SURFACES.items() if fact.ships}
    assert shipping == {rs.GALLERY_STILL, rs.DELIVER_RENDER}


def test_a_viewer_is_refused_by_name_and_told_what_it_adds():
    for surface in (rs.EDIT_PAGE_VIEWER, rs.FUSION_PAGE_VIEWER,
                    rs.COLOR_PAGE_VIEWER, rs.FUSION_SAVER):
        with pytest.raises(rs.SurfaceNotMeasurable) as excinfo:
            rs.assert_measurable(surface)
        message = str(excinfo.value)
        assert surface in message, f"{surface} is not named in its own refusal"
        assert rs.SURFACES[surface].adds.split(",")[0][:20] in message
        assert rs.DELIVER_RENDER in message, "the refusal must say what to use instead"


def test_an_export_is_measurable():
    rs.assert_measurable(rs.DELIVER_RENDER)
    rs.assert_measurable(rs.GALLERY_STILL)


def test_a_surface_nobody_measured_is_refused_rather_than_assumed():
    with pytest.raises(rs.SurfaceNotMeasurable) as excinfo:
        rs.assert_measurable("scopes_panel")
    assert "has measured" in str(excinfo.value)


# ── The reader ──────────────────────────────────────────────────────


def test_the_capture_refuses_to_hand_back_a_still_from_a_viewer(monkeypatch,
                                                                tmp_path):
    """The guard in `grab_still` can FAIL.

    If that route is ever pointed at a surface that does not ship, the
    capture must raise rather than hand a lane a preview to grade
    against.  Pointing the default at a viewer is the only way to make
    the guard fire, so that is what this does.
    """
    from library.tools import marker_capture

    destination = tmp_path / "frame.png"

    class _Still:
        pass

    class _Album:
        def GetStills(self):
            return []

        def ExportStills(self, stills, directory, prefix, fmt):
            Path(directory, f"{prefix}_1.1.1.{fmt}").write_bytes(b"PNG-ish")
            return True

        def DeleteStills(self, stills):
            return True

    class _Gallery:
        def GetAlbumName(self, album):
            return "Stills 1"

        def GetCurrentStillAlbum(self):
            return _Album()

        def GetGalleryStillAlbums(self):
            return []

    class _Project:
        def GetGallery(self):
            return _Gallery()

    class _Timeline:
        def GrabStill(self):
            return _Still()

    # Unpatched, the capture succeeds and records the shipping surface.
    result = marker_capture.grab_still(_Timeline(), _Project(), destination)
    assert result.surface == rs.GALLERY_STILL

    # Pointed at a viewer, it refuses.
    monkeypatch.setattr(marker_capture, "CAPTURED_SURFACE",
                        rs.EDIT_PAGE_VIEWER)
    with pytest.raises(rs.SurfaceNotMeasurable):
        marker_capture.grab_still(_Timeline(), _Project(),
                                  tmp_path / "frame2.png")
