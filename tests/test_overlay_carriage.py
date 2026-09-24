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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from library.tools.overlay_carriage import (  # noqa: E402
    ALPHA_MODE_PREMULTIPLIED,
    DATA_LEVEL_FULL,
    OVERLAY_ENCODE_ARGS,
    OVERLAY_PIXEL_FORMAT,
    OVERLAY_VIDEO_CODEC,
    OverlayCarriageRefused,
    TransparentRegionDarkened,
    apply_clip_attributes,
    assert_transparent_region_unchanged,
    carries_alpha,
    data_level_for,
    frames_are_identical,
    restamp_carriage,
    transcode_in_place,
    transparent_region_deviation,
)

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)),
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
def test_the_correct_composite_passes():
    """Data Level Full: the plate comes through untouched under alpha 0."""
    worst = assert_transparent_region_unchanged(
        _read("composite_data_level_full.png"),
        _read("plate.png"),
        _read("overlay_alpha.png", gray=True),
        what="qtrle overlay at Data Level Full")
    assert worst == 0.0, (
        f"the measured value is exactly zero and this read {worst}; if "
        f"the fixtures changed, the recorded measurement in "
        f"library/tools/overlay_carriage.py changed with them")


@needs_ffmpeg
def test_the_darkened_composite_fails():
    """Data Level Auto: the WHOLE frame is dark, transparent pixels too.

    The defect this gate exists for. It must fail on the picture, not on
    whether a property was set - these frames carry no record of any
    call, only of what Resolve drew.
    """
    with pytest.raises(TransparentRegionDarkened) as caught:
        assert_transparent_region_unchanged(
            _read("composite_data_level_auto.png"),
            _read("plate.png"),
            _read("overlay_alpha.png", gray=True),
            what="qtrle overlay at Data Level Auto")
    assert "16/255" in str(caught.value) or "data level" in str(caught.value)


@needs_ffmpeg
def test_the_darkness_is_measured_and_is_the_recorded_one():
    """How dark, on pixels the overlay does not draw on at all."""
    worst, count = transparent_region_deviation(
        _read("composite_data_level_auto.png"),
        _read("plate.png"),
        _read("overlay_alpha.png", gray=True))
    assert 18.0 < worst < 19.5, (
        f"the exported frames measured 18.63 of 255 and this reads "
        f"{worst:.3f}")
    assert count > 50000, (
        f"the defect covers the transparent canvas, not a corner of it; "
        f"only {count} pixels exceeded the tolerance")


def test_a_frame_the_overlay_covers_entirely_cannot_answer():
    """An opaque overlay says nothing about whether the plate survived."""
    plate = numpy.zeros((4, 4, 3), dtype=numpy.float64)
    with pytest.raises(ValueError, match="opaque on every pixel"):
        transparent_region_deviation(plate, plate,
                                     numpy.full((4, 4), 255, numpy.uint8))


def test_the_tolerance_neither_fails_correct_output_nor_passes_the_defect():
    """A gate that fails correct output is no coverage (AGENTS.md 10.4)."""
    alpha = numpy.zeros((4, 4), dtype=numpy.uint8)
    plate = numpy.full((4, 4, 3), 128.0)
    # half a code value of colour-managed drift
    assert_transparent_region_unchanged(plate + 0.5, plate, alpha)
    with pytest.raises(TransparentRegionDarkened):
        assert_transparent_region_unchanged(plate - 16.0, plate, alpha)


# ── The alpha sniff R8 widened ───────────────────────────────────────

@pytest.mark.parametrize("codec,pix_fmt,profile", [
    ("qtrle", "argb", ""),
])
def test_these_carry_alpha(codec, pix_fmt, profile):
    assert carries_alpha(codec_name=codec, pix_fmt=pix_fmt, profile=profile)


@pytest.mark.parametrize("codec,pix_fmt,profile", [
    ("prores", "yuv422p10le", "HQ"),
])
def test_these_do_not(codec, pix_fmt, profile):
    assert not carries_alpha(codec_name=codec, pix_fmt=pix_fmt,
                             profile=profile)


def _old_sniff(codec_name: str, pix_fmt: str, profile: str) -> bool:
    """What `qa/asset_qa.py` tested before 2026-09-12, kept verbatim.

    Not a reimplementation to be kind to: it is the line the gate used
    to run, so the test below can show that the artefacts this change
    produces are exactly what it rejected.
    """
    return "yuva" in pix_fmt.lower() or (
        codec_name.lower() == "prores" and "4444" in profile)




# ── The data level is per codec, and that is the whole point ─────────

def test_qtrle_needs_full_and_prores_must_be_left_alone():
    """Measured both ways: forcing Full onto ProRes is the same defect.

    ProRes 4444 at `Full` rendered the transparent region at 143.895 of
    255 where the plate is 127.957 - sixteen too BRIGHT - so a rule that
    set one value for every overlay would break the codec it is not for.
    """
    assert data_level_for("qtrle") == DATA_LEVEL_FULL
    assert data_level_for("prores") is None
    assert data_level_for("png") is None


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


def test_a_qtrle_item_gets_both_attributes():
    item = _FakeItem()
    applied = apply_clip_attributes(
        item, "x.mov", probe={"codec_name": "qtrle", "pix_fmt": "argb",
                              "profile": ""})
    assert item.properties["Alpha mode"] == ALPHA_MODE_PREMULTIPLIED
    assert item.properties["Data Level"] == DATA_LEVEL_FULL
    assert applied["alpha"] is True


def test_a_prores_item_keeps_auto():
    item = _FakeItem()
    apply_clip_attributes(
        item, "x.mov", probe={"codec_name": "prores",
                              "pix_fmt": "yuva444p10le", "profile": "4444"})
    assert item.properties["Alpha mode"] == ALPHA_MODE_PREMULTIPLIED
    assert item.properties["Data Level"] == "Auto"




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

def test_a_new_artefact_pairs_the_codec_with_the_current_stamp():
    """What an overlay artefact IS, both halves, in one place.

    The codec half lives in `overlay_carriage` (what the encoder is
    told, what the probe reads back, what level Resolve is told) and
    the stamp half in `overlay_mode.OVERLAY_CARRIAGE` (what the reuse
    key and the tight-box sidecar record). An artefact encoded the new
    way and stamped the old way is worse than either, because the
    stamp is what later readers trust - so the pairing itself is
    pinned: a file probing as the current codec must be readable as
    an overlay AND demand the current behaviour, under the current
    stamp, which must be the one that named this codec change.
    """
    from library.tools.overlay_mode import OVERLAY_CARRIAGE

    assert OVERLAY_CARRIAGE == "tight-480-4"
    assert OVERLAY_VIDEO_CODEC == "qtrle"
    assert carries_alpha(codec_name=OVERLAY_VIDEO_CODEC,
                         pix_fmt=OVERLAY_PIXEL_FORMAT, profile="")
    assert data_level_for(OVERLAY_VIDEO_CODEC) == DATA_LEVEL_FULL


def test_an_old_carriage_artefact_still_reads():
    """Existing artefacts must not become unreadable.

    A `tight-480-3`-era file on disk probes as ProRes 4444 with a
    `yuva` pixel format - the codec the current carriage replaced.
    The reader is keyed to the FILE, not to the current stamp, so it
    still recognises the alpha plane, still sets the premultiplied
    mode, and still leaves alone the one property ProRes needs left
    alone. Unmigrated files keep importing correctly; only their
    recorded stamps read as superseded, which is what earns them a
    transcode rather than a re-render.
    """
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
