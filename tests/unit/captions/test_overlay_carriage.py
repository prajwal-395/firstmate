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
import inspect


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


# --------------------------------------------------------------------------
# From test_overlay_mode.py
#
# How a project chooses to carry its caption overlays, if it chooses.
#
# Two independent axes, defaulting to tight since 2026-09-10 (the
# captain's reversal of full-frame-by-default):
#
# - GEOMETRY: `tight` renders only the drawn bounds
#   (`library/tools/tight_box.py`) and places the small clip at an
#   offset in Resolve, so it stays movable after the fact. `full`
#   renders at the delivery frame (1080x1920) and needs no transform.
# - CONTAINER: `video` stitches frames into one ProRes 4444 mov.
#   `frames` keeps the PNG sequence on disk and hands it to Resolve
#   directly - no intermediate video is rendered first.
#
# A project declares either, both, or neither under `pipeline:` in its
# project.yaml:
#
#     pipeline:
#       subtitle_overlay_geometry: tight
#       subtitle_overlay_container: frames
#
# Declaring nothing is the tight path. An unknown value
# raises rather than falling back, because a silently ignored declaration
# is a choice the project thinks it made and did not.

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


from library.tools.overlay_mode import (  # noqa: E402
    CONTAINERS,
    GEOMETRIES,
    resolve_motion_graphics_geometry,
    resolve_overlay_container,
    resolve_overlay_geometry,
)


def _project_with(pipeline: dict, tmp_path) -> str:
    root = tmp_path / "proj"
    root.mkdir(exist_ok=True)
    lines = ["name: t", "slug: t", "pipeline:"]
    for key, value in pipeline.items():
        lines.append(f"  {key}: {value}")
    (root / "project.yaml").write_text("\n".join(lines) + "\n",
                                       encoding="utf-8")
    return str(root)


def test_unknown_values_raise(tmp_path):
    project = _project_with({"subtitle_overlay_geometry": "small"},
                            tmp_path)
    with pytest.raises(ValueError, match="subtitle_overlay_geometry"):
        resolve_overlay_geometry(project)
    project = _project_with({"subtitle_overlay_container": "gif"},
                            tmp_path)
    with pytest.raises(ValueError, match="subtitle_overlay_container"):
        resolve_overlay_container(project)


# --------------------------------------------------------------------------
# From test_overlay_renderers_state_the_frame.py
#
# Overlay renderers state the frame; none assumes one.
#
# Slice 2 of the delivery-format generalisation. On project 001 a
# landscape delivery let these renderers keep drawing vertical - they
# carried ``width=1080, height=1920`` as default parameters - and the
# overlays composited as a visible lighter band down the central 1080px
# of the picture, which a vision model named unprompted on five of eight
# sampled frames. See library/tools/delivery_format.py.
#
# The rule: every named overlay renderer takes the frame from the
# declared format. A default is what let these drift; making the caller
# state the frame stops it recurring. No new hardcoded constant anywhere
# - values come from ``delivery_format.py``, which owns them.
#
# The input that breaks each test below is stated on the test: omit the
# frame, or hand a horizontal one.

sys.path.insert(0, PROJECT_ROOT)

from library.steps.step_4_05_render_subtitles.generate_remotion_props import (  # noqa: E402
    generate_subtitle_props_per_block,
)
from library.steps.step_4_06_render_motion_graphics.generate_motion_props import (  # noqa: E402
    generate_motion_props,
)
from library.tools import overlay_verify  # noqa: E402

VERTICAL = (1080, 1920)
HORIZONTAL = (1920, 1080)


def _no_frame_default(fn, *names):
    """The named parameters exist and carry no default.

    Fails the moment a renderer regresses to assuming a frame: any
    default - 1080x1920, 1920x1080, or None - lets a caller drift.
    """
    params = inspect.signature(fn).parameters
    for name in names:
        assert name in params, f"{fn.__name__} lost its {name} parameter"
        assert params[name].default is inspect.Parameter.empty, (
            f"{fn.__name__} defaults {name} to "
            f"{params[name].default!r}; the caller must state the frame")


def test_an_overlay_renderer_defaults_no_frame():
    """B1 collapse of the renderer and verifier variants - the same
    property twice.

    Breaks on: restoring ``width=1080, height=1920`` (or any default)
    on a renderer - the exact shape that drew project 001 vertical -
    or restoring ``full_wh=(1080, 1920)`` on the verifier, judging a
    landscape timeline against a vertical frame."""
    for fn, names in ((generate_subtitle_props_per_block, ("width", "height")),
                      (generate_motion_props, ("width", "height")),
                      (overlay_verify.verify_values, ("full_wh",))):
        _no_frame_default(fn, *names)
    # And omitting the frame really is a TypeError at the call.
    with pytest.raises(TypeError):
        generate_subtitle_props_per_block({"subtitle_entries": [], "style": {}})
    with pytest.raises(TypeError):
        generate_motion_props([], {})


def _subtitle_data():
    return {
        "subtitle_entries": [{
            "text": "hello", "timeline_start": 0.0, "timeline_end": 1.0,
            "spine_block_position": 0, "emphasis_words": [], "words": [],
        }],
        "style": {"font": "Montserrat", "size": 58},
    }


def test_a_horizontal_frame_reaches_the_subtitle_and_motion_props():
    """Breaks on: subtitle props carrying anything but the frame handed
    in. Input: a 1920x1080 delivery - project 001's landscape shape."""
    props = generate_subtitle_props_per_block(
        _subtitle_data(), fps=30,
        width=HORIZONTAL[0], height=HORIZONTAL[1])
    assert props[0]["width"] == 1920
    assert props[0]["height"] == 1080
    # And the motion props are measured against it too.
    spine = {"structure": [{
        "block_type": "speech", "position": 1,
        "timeline_start": 0.0, "timeline_end": 4.0,
    }]}
    segments, resolved = generate_motion_props(
        [{"element": "title_lockup", "start_seconds": 0.5,
          "duration_seconds": 2.0, "anchor": "top_left",
          "copy": {"display": "A NAME"}, "color": "#F5F5F0"}],
        spine, width=HORIZONTAL[0], height=HORIZONTAL[1],
        project_folder=None)
    assert segments, resolved.basis_record()
    assert segments[0]["props"]["width"] == 1920
    assert segments[0]["props"]["height"] == 1080


def _timed_text_declaration():
    return {"timed_text_overlay": {
        "font_family": "Helvetica",
        "moments": [{
            "text": "Night 1", "color": "#FFFFFF",
            "font_size": 42, "font_weight": 400,
            "text_shadow": "none",
            "start_frame": 0, "duration_frames": 60,
            "x": 0.5, "y": 0.5,
            "fade_in_frames": 10, "fade_out_frames": 10,
        }],
    }}


# --------------------------------------------------------------------------
# From test_subtitle_overlay_modes.py
#
# The overlay option: the frames container, and the tight geometry.
#
# `render_one_segment` behind stub renderers, so what is pinned is the
# unit's own decisions - names, records, reuse, refusal - without paying
# for Remotion. Explicit full-canvas video is asserted unchanged: the
# option adds, it does not move. The default is tight (since
# 2026-09-10); the tight render tests below serve canned decodable
# renders at the constant canvas because a byte stub cannot feed the
# edge guard.

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.steps.step_4_05_render_subtitles.step import (
    FAILED,
    RENDERED,
    REUSED,
    _qa_frame_sequence,
    render_one_segment,
)
from library.tools.tight_box import (
    constant_caption_box,
)

NEEDS_FFMPEG = shutil.which("ffmpeg") is None
FFMPEG_REASON = "needs ffmpeg; runs in CI, which installs it (AGENTS.md 9)"

# A known ink rectangle on the constant canvas, in (x0, y0, x1, y1).
RECT = (302, 200, 602, 280)
N_FRAMES = 30

# The constant canvas for the `_props` style (840px wrap): the number
# the structural bound derives, not a literal restated here.
CONSTANT_W = 904
CONSTANT_H = 480

SAFE = {"top": 120, "right": 120, "bottom": 320, "left": 90}


def _props():
    return {
        "_block_position": 1,
        "_timeline_start": 0.0,
        "_timeline_end": 2.0,
        "_source_in_frame": 0,
        "_source_out_frame": 60,
        "_speaker": None,
        "_source_clip_id": "clip_001",
        "_source_start": 10.0,
        "_source_end": 12.0,
        "durationInFrames": 60,
        "fps": 30,
        "width": 1080,
        "height": 1920,
        "style": {
            "fontFamily": "Montserrat",
            "fontSize": 58,
            "fontWeight": 800,
            "fontColor": "#FFFFFF",
            "accentColor": "#FBF0B8",
            "outlineColor": "#000000",
            "outlineWidth": 4,
            "position": "bottom",
            "safeArea": dict(SAFE),
            "captionMaxWidth": 840,
        },
        "subtitles": [{
            "text": "and so my very",
            "startFrame": 0,
            "endFrame": 21,
            "emphasisWords": [],
            "words": [
                {"word": "and", "startFrame": 0, "endFrame": 5},
                {"word": "so", "startFrame": 5, "endFrame": 9},
                {"word": "my", "startFrame": 9, "endFrame": 13},
                {"word": "very,", "startFrame": 13, "endFrame": 21},
            ],
            "fitScale": 1.0,
        }],
    }


class _StubRenderer:
    """Acts like the CLI renderer: video writes a file, sequence fills
    a directory with one PNG per rendered frame.

    Sequence frames are drawn at the canvas the props promise - a real
    renderer draws what it was asked - so the guard reads frames of
    the size the box derived. They draw nothing, which is what sends
    a tight attempt down the draws-nothing full fallback."""

    def __init__(self, frames=60):
        self.frames = frames
        self.calls = []

    def render(self, props_path, overlay_path, sequence=False):
        self.calls.append((props_path, overlay_path, sequence))
        if sequence:
            try:
                with open(props_path) as handle:
                    asked = json.load(handle)
                size = (int(asked.get("width", 8)),
                        int(asked.get("height", 8)))
            except (OSError, ValueError, TypeError):
                size = (8, 8)
            os.makedirs(overlay_path, exist_ok=True)
            from PIL import Image
            for i in range(self.frames):
                Image.new("RGBA", size, (255, 255, 255, 0)).save(
                    os.path.join(overlay_path, f"frame-{i:02d}.png"))
        else:
            with open(overlay_path, "wb") as handle:
                handle.write(b"pixels")
        return True, ""

    def close(self):
        pass


class _ServingRenderer:
    """Serves one canned render per call, in order.

    The tight path renders its constant canvas natively - exactly one
    engine call per carrying - so there is no probe to tell apart from
    a main render. A test needing two different files (the edge-touch
    fallback) passes two sources; every other test passes one.
    """

    def __init__(self, *sources):
        self.sources = list(sources)
        self.calls = []

    def render(self, props_path, overlay_path, sequence=False):
        self.calls.append((props_path, overlay_path, sequence))
        index = min(len(self.calls) - 1, len(self.sources) - 1)
        src = self.sources[index]
        if sequence:
            shutil.copytree(src, overlay_path, dirs_exist_ok=True)
        else:
            shutil.copy(src, overlay_path)
        return True, ""

    def close(self):
        pass


def _small_mov(path, rect=RECT, size=(CONSTANT_W, CONSTANT_H),
               frames=N_FRAMES):
    """A synthetic constant-canvas render: transparent frame, opaque
    rect. The step's edge guard reads this file's own alpha plane, so
    the canned render has to be decodable, not bytes.

    `format=rgba` lives INSIDE each lavfi source: without it the
    transparent base arrives opaque (measured 2026-09-09 - the PNG
    muxer settles on yuv and alpha is lost before the overlay runs).
    """
    w, h = size
    x0, y0, x1, y1 = rect
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"color=c=black@0:s={w}x{h}:d=1:r=30,format=rgba",
         "-f", "lavfi",
         "-i", f"color=white:s={x1 - x0}x{y1 - y0}:d=1:r=30,format=rgba",
         "-filter_complex",
         f"[0][1]overlay={x0}:{y0},format=rgba",
         "-frames:v", str(frames), "-c:v", "prores_ks",
         "-profile:v", "4444", "-pix_fmt", "yuva444p10le", path],
        check=True,
    )
    return path


def _blank_mov(path, size=(CONSTANT_W, CONSTANT_H), frames=N_FRAMES):
    """A fully transparent constant-canvas render: nothing to bound."""
    w, h = size
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"color=c=black@0:s={w}x{h}:d=1:r=30,format=rgba",
         "-frames:v", str(frames), "-c:v", "prores_ks",
         "-profile:v", "4444", "-pix_fmt", "yuva444p10le", path],
        check=True,
    )
    return path


def _rect_frames(d, size, rect, n):
    """Constant-canvas frames with a known ink rectangle, PIL only."""
    from PIL import Image, ImageDraw
    os.makedirs(d, exist_ok=True)
    x0, y0, x1, y1 = rect
    for i in range(n):
        img = Image.new("RGBA", size, (0, 0, 0, 0))
        ImageDraw.Draw(img).rectangle([x0, y0, x1 - 1, y1 - 1],
                                      fill=(255, 255, 255, 255))
        img.save(os.path.join(d, f"frame-{i:02d}.png"))
    return d


def _small_frames_setup(tmp_path, name, n=5, rect=RECT):
    """A canned constant-canvas sequence, box included.

    The frames ARE the artefact the step guards - the step renders
    its constant canvas natively and reads the PNGs' own alpha plane
    - so the expectation is derived the way the step derives it, from
    the props, never measured off the pixels.
    """
    props = _props_frames(_props(), n)
    tight_canned = _rect_frames(os.path.join(str(tmp_path), f"{name}-tight"),
                                (CONSTANT_W, CONSTANT_H), rect, n)
    box = constant_caption_box(props)
    assert box is not None
    return props, tight_canned, box


def _props_frames(props, n):
    props = dict(props)
    props["durationInFrames"] = n
    props["_source_out_frame"] = n
    return props


def _probe_leftovers(out_dir):
    return [n for n in os.listdir(out_dir) if n.startswith("probe_")]


def test_explicit_full_path_is_unchanged(tmp_path):
    out = render_one_segment(_props(), str(tmp_path), "tl",
                             remotion_dir="/none",
                             renderer=_StubRenderer(),
                             overlay_geometry="full")
    assert out["provenance"] == RENDERED
    assert out["overlay_path"].endswith(".mov")
    assert "_tight" not in os.path.basename(out["overlay_path"])
    assert out["geometry"] == "full"
    assert out["container"] == "video"
    assert out["tight_box"] is None
    assert out["tight_fallback"] == ""
    assert out["frames"] is None


def test_incomplete_sequence_is_a_failure_not_a_render(tmp_path):
    stub = _StubRenderer(frames=3)
    out = render_one_segment(_props(), str(tmp_path), "tl",
                             remotion_dir="/none",
                             renderer=stub,
                             overlay_container="frames")
    assert out["provenance"] == FAILED
    assert "sequence" in out["failure"]


def test_tight_video_renders_natively_at_the_constant_canvas(tmp_path):
    """The tight output is RENDERED at the constant canvas - no probe,
    no crop, no second render: the predictor sizes nothing that
    reaches a timeline, and the probe reaches nothing either. Tight
    and full carryings never share a filename: the content digest
    carries the geometry."""
    props = _props_frames(_props(), N_FRAMES)
    box = constant_caption_box(props)
    assert box is not None
    tight_mov = _small_mov(os.path.join(str(tmp_path), "tight.mov"))
    stub = _ServingRenderer(tight_mov)
    out = render_one_segment(props, str(tmp_path), "tl",
                             remotion_dir="/none",
                             renderer=stub,
                             overlay_geometry="tight")
    assert out["provenance"] == RENDERED
    assert out["overlay_path"].endswith(".mov")
    assert out["geometry"] == "tight"
    assert out["container"] == "video"
    assert out["frames"] is None
    box_out = out["tight_box"]
    assert box_out["width"] == CONSTANT_W == box.width
    assert box_out["height"] == CONSTANT_H == box.height
    assert box_out["width"] < 1080 and box_out["height"] < 1920
    assert box_out["placement"] == box.placement
    assert box_out["placement"]["scaling"] == 1
    # Arithmetic, not correspondence: the centred canvas sits centred.
    assert box_out["placement"]["pan"] == 0
    props_file = out["overlay_path"][:-len(".mov")] + "_props.json"
    with open(props_file) as handle:
        props_on_disk = json.load(handle)
    assert props_on_disk["width"] == CONSTANT_W
    assert props_on_disk["height"] == CONSTANT_H
    sidecar_file = out["overlay_path"][:-len(".mov")] + "_box.json"
    with open(sidecar_file) as handle:
        sidecar = json.load(handle)
    assert sidecar["placement"] == box_out["placement"]
    assert sidecar["edge_guard"]["touches_edge"] is False
    # THE guard: exactly one engine render. A second render call is
    # the probe this path exists to delete.
    assert stub.calls and len(stub.calls) == 1
    assert _probe_leftovers(str(tmp_path)) == []

    from library.tools.overlay_carriage import (
        OVERLAY_VIDEO_CODEC,
        probe_overlay,
    )

    # The codec survives the rewrite: Remotion writes ProRes, and the
    # transcode the crop used to pay for free now runs here.
    assert probe_overlay(out["overlay_path"])["codec_name"] == \
        OVERLAY_VIDEO_CODEC


@pytest.mark.skipif(NEEDS_FFMPEG, reason=FFMPEG_REASON)
def test_tight_video_edge_touch_falls_back_to_full_canvas(tmp_path):
    """Ink on the canvas edge is clipped pixels no repositioning can
    recover, so the card falls back to full canvas - re-rendered,
    because there is no probe to copy any more - and the run still
    says which card lost its tight carriage and why.

    Forced here with a canned render whose ink touches the edge (a
    native render that fits never does); the fallback file is carried
    into the overlay codec like every other artefact.
    """
    props = _props_frames(_props(), N_FRAMES)
    edge_mov = _small_mov(os.path.join(str(tmp_path), "edge.mov"),
                          rect=(302, 200, CONSTANT_W, 280))
    full_mov = _small_mov(os.path.join(str(tmp_path), "full.mov"),
                          rect=RECT, size=(1080, 1920))
    stub = _ServingRenderer(edge_mov, full_mov)
    out = render_one_segment(props, str(tmp_path), "tl",
                             remotion_dir="/none",
                             renderer=stub,
                             overlay_geometry="tight")
    assert out["provenance"] == RENDERED
    assert out["geometry"] == "full"
    assert out["tight_box"] is None
    assert "canvas edge" in out["tight_fallback"]
    assert out["overlay_path"].endswith(".mov")
    assert os.path.isfile(out["overlay_path"])

    from library.tools.overlay_carriage import (
        OVERLAY_VIDEO_CODEC,
        probe_overlay,
    )

    assert probe_overlay(out["overlay_path"])["codec_name"] == \
        OVERLAY_VIDEO_CODEC
    # Two renders: the tight one that touched the edge, and its full
    # replacement. No probe anywhere.
    assert stub.calls and len(stub.calls) == 2
    assert _probe_leftovers(str(tmp_path)) == []


@pytest.mark.skipif(NEEDS_FFMPEG, reason=FFMPEG_REASON)
def test_tight_render_drawing_nothing_records_full_geometry(tmp_path):
    """A card with nothing to bound is full canvas IN THE RECORD too.

    The file was always full canvas; the entry used to keep saying
    "tight" with no box, pinning a full-canvas file as a tight one in
    the only record a staging render leaves."""
    props = _props_frames(_props(), N_FRAMES)
    blank = _blank_mov(os.path.join(str(tmp_path), "blank.mov"))
    full_mov = _small_mov(os.path.join(str(tmp_path), "full.mov"),
                          rect=RECT, size=(1080, 1920))
    stub = _ServingRenderer(blank, full_mov)
    out = render_one_segment(props, str(tmp_path), "tl",
                             remotion_dir="/none",
                             renderer=stub,
                             overlay_geometry="tight")
    assert out["provenance"] == RENDERED
    assert out["geometry"] == "full"
    assert out["tight_box"] is None
    assert "draws nothing" in out["tight_fallback"]
    assert out["overlay_path"].endswith(".mov")
    assert os.path.isfile(out["overlay_path"])
    assert _probe_leftovers(str(tmp_path)) == []


def test_tight_frames_records_dir_and_placement(tmp_path):
    props, tight_canned, box = _small_frames_setup(tmp_path, "frames")
    stub = _ServingRenderer(tight_canned)
    out = render_one_segment(props, str(tmp_path), "tl",
                             remotion_dir="/none",
                             renderer=stub,
                             overlay_geometry="tight",
                             overlay_container="frames")
    assert out["provenance"] == RENDERED
    assert out["overlay_path"] == ""
    assert out["geometry"] == "tight"
    assert out["container"] == "frames"
    assert out["tight_box"]["width"] == box.width
    assert out["tight_box"]["placement"]["scaling"] == 1
    frames = out["frames"]
    assert frames["count"] == 5
    assert os.path.isdir(frames["dir"])
    assert frames["dir"].endswith("_frames")
    assert (len([n for n in os.listdir(frames["dir"])
                 if n.endswith(".png")]) == 5)
    assert _probe_leftovers(str(tmp_path)) == []


def test_reuse_skips_identical_tight_frames(tmp_path):
    # The reuse key fingerprints the renderer tree; a directory
    # without one yields no key and reuse is refused - so this test
    # renders against the real composition tree, like the caption
    # reuse tests do. Placement on the second call is restored from
    # the box sidecar: no render runs at all.
    remotion = os.path.join(PROJECT_ROOT, "remotion-subtitles")
    props, tight_canned, _ = _small_frames_setup(tmp_path, "reuse")
    stub = _ServingRenderer(tight_canned)
    first = render_one_segment(
        props, str(tmp_path), "tl", remotion_dir=remotion,
        renderer=stub, reuse=True,
        overlay_geometry="tight", overlay_container="frames")
    assert first["provenance"] == RENDERED
    assert len(stub.calls) == 1
    fresh = _ServingRenderer(tight_canned)
    second = render_one_segment(
        props, str(tmp_path), "tl", remotion_dir=remotion,
        renderer=fresh, reuse=True,
        overlay_geometry="tight", overlay_container="frames")
    assert second["provenance"] == REUSED
    assert fresh.calls == []
    assert (second["tight_box"]["placement"]
            == first["tight_box"]["placement"])


def test_unknown_mode_is_refused(tmp_path):
    with pytest.raises(ValueError, match="overlay_geometry"):
        render_one_segment(_props(), str(tmp_path), "tl",
                           remotion_dir="/none",
                           renderer=_StubRenderer(),
                           overlay_geometry="small")
    with pytest.raises(ValueError, match="overlay_container"):
        render_one_segment(_props(), str(tmp_path), "tl",
                           remotion_dir="/none",
                           renderer=_StubRenderer(),
                           overlay_container="gif")


def _frame_dir(tmp_path, name, draw):
    from PIL import Image
    d = os.path.join(str(tmp_path), name)
    os.makedirs(d, exist_ok=True)
    for i, pixels in enumerate(draw):
        img = Image.new("RGBA", (100, 60), (0, 0, 0, 0))
        for x, y in pixels:
            img.putpixel((x, y), (255, 255, 255, 255))
        img.save(os.path.join(d, f"frame-{i:02d}.png"))
    return d


def test_frame_qa_refuses_a_blank_sequence(tmp_path):
    d = _frame_dir(tmp_path, "blank.frames", [[], []])
    with pytest.raises(RuntimeError, match="draws nothing"):
        _qa_frame_sequence({"frames": {"dir": d, "count": 2}})


def test_frame_qa_refuses_edge_clipped_ink(tmp_path):
    ink = [(x, y) for x in range(40) for y in range(35, 45)]
    d = _frame_dir(tmp_path, "clip.frames", [ink, ink])
    with pytest.raises(RuntimeError, match="edge"):
        _qa_frame_sequence({"frames": {"dir": d, "count": 2}})
