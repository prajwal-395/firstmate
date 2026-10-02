"""The overlay carriage: what the artefact IS, and that the picture survives it.

The check that matters here is NOT "did we call `SetClipProperty`".  It
is "is the composited frame the picture it should be", measured on real
frames DaVinci Resolve exported on 2026-09-12 while the defect was live
and again after it was fixed - `tests/fixtures/overlay_carriage/`:

    plate.png                         the 0x808080 plate, overlay absent
    composite_data_level_auto.png     the same frame with a qtrle overlay
                                      imported on Resolve's default Auto
    composite_data_level_full.png     the same frame with Data Level Full
    overlay_alpha.png                 that overlay's OWN alpha plane

All four are the same 512x256 crop of timeline frame 52, taken from
16-bit PNGs rendered through Deliver.  The `auto` frame is 18.63 of 255
darker than the plate on pixels where the overlay's alpha is ZERO - a
whole-frame defect that would read as a grade problem rather than a
codec one.  A gate that stops detecting that fails these tests.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from library.tools.overlay_carriage import (  # noqa: E402
    ALPHA_MODE_PREMULTIPLIED,
    DATA_LEVEL_FULL,
    OVERLAY_PIXEL_FORMAT,
    OVERLAY_VIDEO_CODEC,
    OverlayCarriageRefused,
    TransparentRegionDarkened,
    apply_clip_attributes,
    assert_transparent_region_unchanged,
    carries_alpha,
    data_level_for,
    restamp_carriage,
    transparent_region_deviation,
)

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                        "fixtures", "overlay_carriage")

numpy = pytest.importorskip("numpy")


def _has_ffmpeg() -> bool:
    return bool(shutil.which("ffmpeg")) and bool(shutil.which("ffprobe"))


needs_ffmpeg = pytest.mark.skipif(
    not _has_ffmpeg(),
    reason="ffmpeg/ffprobe not on PATH; CI installs them "
           "(.github/workflows/ci.yml) and so does a dev machine per "
           "AGENTS.md 9, so this runs everywhere the repo is set up")


def _read(name: str, gray: bool = False):
    """One fixture frame as an array, through ffmpeg so the PNG bit depth
    is not something this test has to know."""
    path = os.path.join(FIXTURES, name)
    assert os.path.isfile(path), f"missing fixture {path}"
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-pix_fmt",
         "gray" if gray else "rgb48le", "-f", "rawvideo", "-"],
        capture_output=True, check=True,
    )
    if gray:
        return numpy.frombuffer(result.stdout, dtype=numpy.uint8).reshape(256, 512)
    raw = numpy.frombuffer(result.stdout, dtype="<u2").reshape(256, 512, 3)
    # onto the 0..255 scale the tolerance is stated on
    return raw.astype(numpy.float64) / 257.0


# ── The check, on the real frames ────────────────────────────────────

@needs_ffmpeg
def test_the_gate_reads_the_defect_off_the_real_exported_frames():
    """Data Level Full: the plate comes through untouched under alpha 0.
    Data Level Auto: the WHOLE frame is dark, transparent pixels too - and
    the gate fails on the picture, not on whether a property was set."""
    plate = _read("plate.png")
    alpha = _read("overlay_alpha.png", gray=True)
    worst = assert_transparent_region_unchanged(
        _read("composite_data_level_full.png"), plate, alpha,
        what="qtrle overlay at Data Level Full")
    assert worst == 0.0, (
        f"the measured value is exactly zero and this read {worst}; if "
        f"the fixtures changed, the recorded measurement in "
        f"library/tools/overlay_carriage.py changed with them")

    darkened = _read("composite_data_level_auto.png")
    with pytest.raises(TransparentRegionDarkened) as caught:
        assert_transparent_region_unchanged(
            darkened, plate, alpha, what="qtrle overlay at Data Level Auto")
    assert "16/255" in str(caught.value) or "data level" in str(caught.value)
    # How dark, on pixels the overlay does not draw on at all.
    worst, count = transparent_region_deviation(darkened, plate, alpha)
    assert 18.0 < worst < 19.5, (
        f"the exported frames measured 18.63 of 255 and this reads "
        f"{worst:.3f}")
    assert count > 50000, (
        f"the defect covers the transparent canvas, not a corner of it; "
        f"only {count} pixels exceeded the tolerance")


def test_an_opaque_frame_cannot_answer_and_the_tolerance_is_two_sided():
    """An opaque overlay says nothing about whether the plate survived."""
    plate = numpy.zeros((4, 4, 3), dtype=numpy.float64)
    with pytest.raises(ValueError, match="opaque on every pixel"):
        transparent_region_deviation(plate, plate,
                                     numpy.full((4, 4), 255, numpy.uint8))
    # The tolerance neither fails correct output (AGENTS.md 10.4) nor
    # passes the defect.
    alpha = numpy.zeros((4, 4), dtype=numpy.uint8)
    plate = numpy.full((4, 4, 3), 128.0)
    # half a code value of colour-managed drift
    assert_transparent_region_unchanged(plate + 0.5, plate, alpha)
    with pytest.raises(TransparentRegionDarkened):
        assert_transparent_region_unchanged(plate - 16.0, plate, alpha)


# ── The alpha sniff R8 widened ───────────────────────────────────────

def test_the_alpha_sniff_reads_the_codec():
    assert carries_alpha(codec_name="qtrle", pix_fmt="argb", profile="")
    assert not carries_alpha(codec_name="prores", pix_fmt="yuv422p10le",
                             profile="HQ")


# ── The data level is per codec, and that is the whole point ─────────

class _FakeItem:
    """A pool item that remembers, and can refuse, like Resolve's."""

    def __init__(self, refuse: str = ""):
        self.properties = {"Alpha mode": "Straight", "Data Level": "Auto"}
        self._refuse = refuse

    def SetClipProperty(self, name, value):
        if name == self._refuse:
            return False
        self.properties[name] = value
        return True

    def GetClipProperty(self, name):
        return self.properties.get(name)


def test_the_current_overlay_codec_gets_both_attributes():
    """The artefact the encoder writes today reads as an overlay and gets
    Premultiplied + Data Level Full; other codecs get no forced level."""
    assert carries_alpha(codec_name=OVERLAY_VIDEO_CODEC,
                         pix_fmt=OVERLAY_PIXEL_FORMAT, profile="")
    assert data_level_for(OVERLAY_VIDEO_CODEC) == DATA_LEVEL_FULL
    assert data_level_for("png") is None
    item = _FakeItem()
    applied = apply_clip_attributes(
        item, "x.mov", probe={"codec_name": OVERLAY_VIDEO_CODEC,
                              "pix_fmt": OVERLAY_PIXEL_FORMAT,
                              "profile": ""})
    assert item.properties["Alpha mode"] == ALPHA_MODE_PREMULTIPLIED
    assert item.properties["Data Level"] == DATA_LEVEL_FULL
    assert applied["alpha"] is True


def test_a_refused_data_level_raises_rather_than_warning():
    """AGENTS.md 5: judge a Resolve call by what it RETURNS.

    A silently-refused Data Level is the whole failure - the build would
    carry on and ship a darkened picture.
    """
    item = _FakeItem(refuse="Data Level")
    with pytest.raises(OverlayCarriageRefused, match="Data Level"):
        apply_clip_attributes(
            item, "x.mov", probe={"codec_name": "qtrle", "pix_fmt": "argb",
                                  "profile": ""})


def test_an_unreadable_file_claims_nothing():
    item = _FakeItem()
    out = apply_clip_attributes(item, "x.mov", probe={})
    assert out["alpha"] is None, (
        "a file the probe cannot read is a different fact from a file "
        "read and found to have no alpha")


# ── The encode arguments ─────────────────────────────────────────────


# ── The migration ────────────────────────────────────────────────────


def test_a_carriage_stamp_moves_only_where_it_matches(tmp_path):
    key = tmp_path / "seg_reuse_key.txt"
    key.write_text("digest+fingerprint+tight-480-3", encoding="utf-8")
    box = tmp_path / "seg_box.json"
    box.write_text(json.dumps({"carriage": "tight-480-3", "width": 840}),
                   encoding="utf-8")
    other = tmp_path / "old_reuse_key.txt"
    other.write_text("digest+fingerprint+frame-baked-1", encoding="utf-8")

    out = restamp_carriage([str(key), str(box), str(other)],
                           "tight-480-3", "tight-480-4")
    assert key.read_text(encoding="utf-8") == "digest+fingerprint+tight-480-4"
    assert json.loads(box.read_text(encoding="utf-8"))["carriage"] == \
        "tight-480-4"
    assert other.read_text(encoding="utf-8") == \
        "digest+fingerprint+frame-baked-1", (
        "a stamp from a different carriage is not this migration's to move")
    assert str(other) in out["skipped"]
    assert json.loads(box.read_text(encoding="utf-8"))["width"] == 840, (
        "restamping must not lose the rest of the sidecar")


# ── The stamp moves with the codec, both directions ──────────────────

def test_an_old_carriage_artefact_still_reads():
    """A `tight-480-3`-era ProRes 4444 file still imports correctly: the
    reader is keyed to the FILE, not the current stamp, and leaves Data
    Level alone (ProRes at Full measured 16/255 too BRIGHT)."""
    old_fields = {"codec_name": "prores", "pix_fmt": "yuva444p10le",
                  "profile": "4444"}
    assert carries_alpha(**old_fields)
    assert data_level_for(old_fields["codec_name"]) is None

    item = _FakeItem()
    applied = apply_clip_attributes(item, "old_carriage.mov",
                                    probe=dict(old_fields))
    assert applied["alpha"] is True
    assert item.properties["Alpha mode"] == ALPHA_MODE_PREMULTIPLIED
    assert item.properties["Data Level"] == "Auto", (
        "forcing Full onto a ProRes overlay renders the transparent "
        "region 16/255 too bright - the same defect in the other "
        "direction")
