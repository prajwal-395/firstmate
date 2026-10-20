"""overlay.position and overlay.resize over a RENDERED movie overlay.

Defects this names:

- the live brief `name-bar-fights-the-captions` refused the name bar
  because the bar IS a rendered `.mov` and the route covered only a
  native PNG still: *"edit 0 (position_overlay) targets unsupported
  media 'b-soft-focus-craig.mov' - the first route is limited to a native
  PNG still without a drawing Fusion comp"*
  (docs/benchmarks/2026-10-20-unfamiliar-brief-acceptance.md).
- a rendered movie overlay's canvas is its OWN resolution - what Resolve
  draws it at - and never the delivery frame, so the projection had no
  canvas to place when it measured the asset with PIL.
- a rendered movie overlay with no alpha plane cannot composite over the
  picture, and a RETIMED one cannot say which of its frames draws, so
  both are refused by name rather than moved blind.
- the still route must keep working: the fix is a widened coverage, not
  a replaced one.

Left for the full suite: these tests run there, not locally, per the
captain's no-local-tests rule. Production stays fail-closed until the
live-requests lane records qualifying evidence; nothing here marks the
operation qualified.
"""

from __future__ import annotations

import shutil
import subprocess
from types import SimpleNamespace

import pytest

from library.tools import overlay_media, reel_touchup

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason=(
        "ffmpeg/ffprobe are not on PATH here, so the rendered overlay "
        "fixture cannot be built or measured. AGENTS.md 9 requires them "
        "for any real run."),
)

FRAME = (1080, 1920)
CANVAS = (320, 120)

#: What Resolve stores for an item at identity: centre anchor, no zoom,
#: native scaling.
IDENTITY = {
    "ZoomX": 1.0, "ZoomY": 1.0, "Pan": 0.0, "Tilt": 0.0,
    "RotationAngle": 0.0, "AnchorPointX": 0.5, "AnchorPointY": 0.5,
    "Scaling": 1.0,
}


def _prores(path, *, alpha: bool = True, frames: int = 6, rate: int = 24):
    """A tiny rendered overlay with a KNOWN alpha plane, or none at all."""
    duration = frames / rate
    if alpha:
        source = (f"color=c=white@0.5:s={CANVAS[0]}x{CANVAS[1]}:"
                  f"d={duration}:r={rate},format=yuva444p10le")
        codec = ["-c:v", "prores_ks", "-profile:v", "4",
                 "-pix_fmt", "yuva444p10le"]
    else:
        source = (f"color=c=white:s={CANVAS[0]}x{CANVAS[1]}:"
                  f"d={duration}:r={rate}")
        codec = ["-c:v", "prores_ks", "-profile:v", "3",
                 "-pix_fmt", "yuv422p10le"]
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", source,
         *codec, str(path)],
        check=True, capture_output=True, text=True, encoding="utf-8")
    return str(path)


def _png(path):
    """A tight transparent PNG still, the media the route always covered."""
    from PIL import Image

    image = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    for x in range(20, CANVAS[0] - 20):
        for y in range(20, CANVAS[1] - 20):
            image.putpixel((x, y), (255, 255, 255, 255))
    image.save(str(path))
    return str(path)


class _PoolItem:
    """Resolve's media-pool item, read for its file and its kind."""

    def __init__(self, path: str, media_type: str = "Video"):
        self._props = {"File Path": path, "Type": media_type,
                       "Resolution": f"{CANVAS[0]}x{CANVAS[1]}"}

    def GetClipProperty(self, key):
        return self._props.get(key, "")


class _Item:
    """One placed timeline item, read for its transform and its span."""

    def __init__(self, path: str, media_type: str = "Video",
                 properties=None):
        self._pool = _PoolItem(path, media_type)
        self._properties = dict(properties or IDENTITY)

    def GetMediaPoolItem(self):
        return self._pool

    def GetProperty(self):
        return dict(self._properties)

    def GetStart(self):
        return 0

    def GetDuration(self):
        return 60

    def GetSourceStartFrame(self):
        return 0


def _timeline():
    return SimpleNamespace(
        GetSetting=lambda key: {"timelineResolutionWidth": FRAME[0],
                                 "timelineResolutionHeight": FRAME[1]}[key])


def _project():
    return SimpleNamespace(
        GetSetting=lambda key: "Original"
        if key == "perfProxyResolutionRatio" else "")


def _clip(path: str, media_type: str, *, speed=100.0):
    """One overlay clip as the touch-up gate reads it."""
    return {"record_in": 0, "record_out": 60, "duration": 60,
            "source_file": path, "media_type": media_type,
            "enabled": True, "speed": speed}


def _placed_box(properties: dict):
    """Where the written transform puts the overlay, read back."""
    from library.tools.layout_model import (
        LayoutItem,
        LayoutModel,
        Transform,
    )

    model = LayoutModel(*FRAME, "original", "original")
    transform = Transform.from_dict({**IDENTITY, **properties,
                                     "pan_units": "original",
                                     "tilt_units": "original"})
    return model.placed_box(LayoutItem(*CANVAS, transform))


def _identity_box():
    """Where the overlay draws before anything is written."""
    return _placed_box({})


def _qualify(clip, edit):
    """Qualify one overlay edit against one overlay clip."""
    tracks = [{"type": "video", "index": 3, "clips": [clip]}]
    return reel_touchup.qualify(
        tracks, {"reel": 1, "edits": [edit]})


def test_position_accepts_a_rendered_movie_overlay(tmp_path):
    """The name bar is a rendered `.mov`, and it is now addressable.

    The live refusal named the file and the limit; this names the fix.
    """
    path = _prores(tmp_path / "b-soft-focus-craig.mov")
    qualification = _qualify(
        _clip(path, "Video"),
        {"op": "position_overlay", "row": "V3", "item": 0,
         "dx": 0.0, "dy": 100.0})
    assert [entry["kind"] for entry in qualification.in_place] == [
        "overlay_position"]
    assert qualification.in_place[0]["media_kind"] == "rendered_video"


def test_resize_accepts_a_rendered_movie_overlay(tmp_path):
    """The same limit held the resize route, and it is covered too."""
    path = _prores(tmp_path / "b-soft-focus-craig.mov")
    qualification = _qualify(
        _clip(path, "Video"),
        {"op": "resize_overlay", "row": "V3", "item": 0,
         "width": 640, "height": 240})
    assert [entry["kind"] for entry in qualification.in_place] == [
        "overlay_resize"]
    assert qualification.in_place[0]["media_kind"] == "rendered_video"


def test_a_rendered_movie_overlay_is_placed_by_its_own_canvas(tmp_path):
    """The written transform reads back the requested absolute box.

    The canvas is the media's OWN resolution - what Resolve draws it at -
    and the box is absolute canvas pixels, exactly as for a PNG still.
    """
    path = _prores(tmp_path / "b-soft-focus-craig.mov")
    entry = {"kind": "overlay_position", "row": "V3", "item_index": 0,
             "record_frame": 0, "dx": 0.0, "dy": 100.0,
             "media_kind": "rendered_video", "source_file": path}
    properties, record = reel_touchup._overlay_position_projection(
        _Item(path), _timeline(), _project(), entry)
    assert record["canvas_wh"] == CANVAS
    assert record["media_kind"] == "rendered_video"
    placed = _placed_box(properties)
    expected = tuple(map(float, record["absolute_box"]))
    assert placed[0] == pytest.approx(expected[0], abs=0.5)
    assert placed[1] == pytest.approx(expected[1], abs=0.5)
    assert placed[2] == pytest.approx(expected[2], abs=0.5)
    assert placed[3] == pytest.approx(expected[3], abs=0.5)
    # The overlay moved DOWN, and it moved by the requested 100 pixels.
    before = _identity_box()
    assert placed[1] > before[1]
    assert ((placed[1] + placed[3]) / 2) - ((before[1] + before[3]) / 2) \
        == pytest.approx(100.0, abs=0.5)


def test_a_rendered_movie_overlay_moves_by_the_requested_delta(tmp_path):
    """The delta is measured against where the overlay WAS, not guessed."""
    path = _prores(tmp_path / "b-soft-focus-craig.mov")
    entry = {"kind": "overlay_position", "row": "V3", "item_index": 0,
             "record_frame": 0, "dx": 40.0, "dy": 100.0,
             "media_kind": "rendered_video", "source_file": path}
    properties, record = reel_touchup._overlay_position_projection(
        _Item(path), _timeline(), _project(), entry)
    before = _identity_box()
    after = _placed_box(properties)
    assert ((after[0] + after[2]) / 2) - ((before[0] + before[2]) / 2) \
        == pytest.approx(40.0, abs=0.5)
    assert ((after[1] + after[3]) / 2) - ((before[1] + before[3]) / 2) \
        == pytest.approx(100.0, abs=0.5)
    assert record["requested_delta"] == [40.0, 100.0]


def test_a_native_png_still_is_still_covered(tmp_path):
    """The fix widens the coverage; it does not replace the still route."""
    path = _png(tmp_path / "overlay.png")
    qualification = _qualify(
        _clip(path, "Still"),
        {"op": "position_overlay", "row": "V3", "item": 0,
         "dx": 0.0, "dy": -40.0})
    assert qualification.in_place[0]["media_kind"] == "png_still"


def test_a_movie_overlay_without_an_alpha_plane_is_refused(tmp_path):
    """No alpha plane means nothing composites over the picture."""
    path = _prores(tmp_path / "opaque.mov", alpha=False)
    entry = {"kind": "overlay_position", "row": "V3", "item_index": 0,
             "record_frame": 0, "dx": 0.0, "dy": 100.0,
             "media_kind": "rendered_video", "source_file": path}
    with pytest.raises(reel_touchup.TouchupRefused, match="alpha"):
        reel_touchup._overlay_position_projection(
            _Item(path), _timeline(), _project(), entry)


def test_a_retimed_movie_overlay_is_refused(tmp_path):
    """A retimed overlay's frames do not map one to one onto its file."""
    path = _prores(tmp_path / "b-soft-focus-craig.mov")
    with pytest.raises(reel_touchup.TouchupRefused, match="normal speed"):
        _qualify(_clip(path, "Video", speed=50.0),
                 {"op": "position_overlay", "row": "V3", "item": 0,
                  "dx": 0.0, "dy": 100.0})


def test_media_the_route_does_not_cover_is_refused_by_name(tmp_path):
    """An audio file is not an overlay, and the refusal says which."""
    path = str(tmp_path / "voice.wav")
    with pytest.raises(reel_touchup.TouchupRefused,
                       match="unsupported media"):
        _qualify(_clip(path, "Audio"),
                 {"op": "position_overlay", "row": "V3", "item": 0,
                  "dx": 0.0, "dy": 100.0})


def test_the_media_kind_is_decided_by_the_media_not_by_its_name(tmp_path):
    """A `.mov` behind a Resolve `Still` is not a rendered movie overlay."""
    path = _prores(tmp_path / "b-soft-focus-craig.mov")
    kind, why = overlay_media.classify(
        {"source_file": path, "media_type": "Still"})
    assert kind is None
    assert "PNG still" in why
