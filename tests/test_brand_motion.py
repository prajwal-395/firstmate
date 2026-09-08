"""Brand motion in a Remotion render: the slot, not the choice.

Every gate here is proved in BOTH directions. A gate that cannot fail is
worse than no gate because it reads as coverage (AGENTS.md 10.4): the
whole point of this module is refusing to render what the renderer
cannot read, so each refusal is shown firing on a fixture that deserves
it AND staying quiet on one that does not.

The fixtures are built by ffmpeg under ``tmp_path`` - never the
captain's real ``logo_reveal.mov`` / ``transition_bumper.mov`` (AGENTS.md
8: a test builds its project under ``tmp_path``, or it skips). Their
answers are known before anything is measured: a ProRes file with opaque
alpha and an audio stream (the real assets' shape), a ProRes file with
no alpha plane at all, a ProRes file with an empty one, and a VP9 WebM
with alpha (the mezzanine's shape).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from library.tools import brand_motion as bm
from library.tools import transition_overlay as ov
from library.tools.brand_motion import (
    BrandMotionError,
    BrandMotionUnmeasurable,
    ConformNotBuilt,
    ConformNotDeclared,
    GeometryMismatch,
)

FPS = 24000 / 1001

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason=(
        "ffmpeg/ffprobe is not available here, so the brand-motion "
        "fixtures cannot be built or measured. Runs anywhere ffmpeg and "
        "ffprobe are on PATH - the CI runner installs them and AGENTS.md 9 "
        "requires them for any real run."
    ),
)


# ── Fixtures whose answers are known before anything is measured ─────

def _prores(path, *, alpha: bool, empty: bool = False,
            frames: int = 6, rate: int = 30, audio: bool = True):
    """A tiny ProRes clip: opaque alpha, empty alpha, or none at all."""
    duration = frames / rate
    cmd = ["ffmpeg", "-v", "error", "-y"]
    if alpha:
        colour = "black@0.0" if empty else "white@1.0"
        cmd += ["-f", "lavfi", "-i",
                (f"color=c={colour}:s=64x64:d={duration}:r={rate},"
                 f"format=yuva444p10le")]
    else:
        cmd += ["-f", "lavfi", "-i",
                (f"color=c=blue:s=64x64:d={duration}:r={rate}")]
    if audio:
        cmd += ["-f", "lavfi", "-i",
                f"sine=frequency=440:duration={duration}"]
    # Output options AFTER every input: between two -i flags ffmpeg
    # reads them as belonging to the second input and refuses.
    if alpha:
        cmd += ["-c:v", "prores_ks", "-profile:v", "4",
                "-pix_fmt", "yuva444p10le"]
    else:
        cmd += ["-c:v", "prores_ks", "-profile:v", "3",
                "-pix_fmt", "yuv422p10le"]
    if audio:
        cmd += ["-c:a", "pcm_s16le", "-shortest"]
    cmd.append(str(path))
    subprocess.run(cmd, check=True, capture_output=True, text=True,
                   encoding="utf-8")
    return str(path)


def _vp9_alpha(path, *, frames: int = 6, rate: int = 30):
    """A tiny VP9 WebM whose alpha plane draws - the mezzanine's shape."""
    duration = frames / rate
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i",
         f"color=c=red:s=64x64:d={duration}:r={rate}",
         "-f", "lavfi", "-i",
         f"color=c=gray:s=64x64:d={duration}:r={rate},format=gray",
         "-filter_complex", "[0:v][1:v]alphamerge,format=yuva420p",
         "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
         "-auto-alt-ref", "0", "-b:v", "0", "-crf", "30",
         str(path)],
        check=True, capture_output=True, text=True, encoding="utf-8")
    return str(path)


@pytest.fixture
def prores_with_sound(tmp_path):
    """Opaque alpha + an audio stream: the real assets' shape."""
    return _prores(tmp_path / "sting.mov", alpha=True)


@pytest.fixture
def prores_no_alpha(tmp_path):
    """No alpha plane at all - there is nothing to composite."""
    return _prores(tmp_path / "flat.mov", alpha=False, audio=False)


@pytest.fixture
def prores_empty_alpha(tmp_path):
    """Alpha plane present and zero everywhere - it draws NOTHING."""
    return _prores(tmp_path / "empty.mov", alpha=True, empty=True,
                   audio=False)


@pytest.fixture
def webm_with_alpha(tmp_path):
    """VP9 WebM with drawing alpha - what a mezzanine looks like."""
    return _vp9_alpha(tmp_path / "sting.webm")


# ── Measuring a source ───────────────────────────────────────────────

def test_measure_source_reads_the_real_shape(prores_with_sound):
    source = bm.measure_source(prores_with_sound)
    assert source.width == 64
    assert source.height == 64
    assert source.fps == pytest.approx(30.0)
    assert source.frame_count == 6
    assert source.duration_seconds == pytest.approx(0.2)
    assert source.has_audio is True
    assert source.element.alpha.draws is True


def test_measure_source_missing_file_is_not_a_measurement_of_zero(tmp_path):
    with pytest.raises(BrandMotionUnmeasurable):
        bm.measure_source(str(tmp_path / "absent.mov"))


def test_measure_source_without_alpha_is_refused_as_such(prores_no_alpha):
    """The trap the shared taxonomy exists to avoid: absent is absent,
    not zero. Without this the refusal below proves nothing - an
    instrument that always raises would pass it."""
    with pytest.raises(ov.ElementHasNoAlpha):
        bm.measure_source(prores_no_alpha)


def test_measure_source_with_empty_alpha_is_refused_as_empty(
        prores_empty_alpha):
    """Present-and-empty is a DIFFERENT refusal from absent (AGENTS.md
    10.2: an overlay that draws nothing is not rendered)."""
    with pytest.raises(ov.ElementDrawsNothing):
        bm.measure_source(prores_empty_alpha)


def test_measure_source_reads_webm_alpha_through_the_decoder_that_sees_it(
        webm_with_alpha):
    """The other direction of the decoder gate: a VP9 WebM the renderer
    paints must MEASURE as drawing. With ffmpeg's default (native vp9)
    decoder the plane is dropped in transit and this file would be
    refused as drawing nothing - which is why the decoder is selected
    per container rather than assumed."""
    source = bm.measure_source(webm_with_alpha)
    assert source.element.alpha.draws is True
    assert source.element.alpha.frames_read == 6
    necessary, _ = bm.needs_mezzanine(source)
    assert necessary is False


# ── The decodability gate ────────────────────────────────────────────

def test_prores_needs_a_mezzanine_and_says_why(prores_with_sound):
    source = bm.measure_source(prores_with_sound)
    necessary, reason = bm.needs_mezzanine(source)
    assert necessary is True
    assert "ProRes" in reason or "prores" in reason


def test_webm_needs_no_mezzanine(webm_with_alpha):
    source = bm.measure_source(webm_with_alpha)
    necessary, _ = bm.needs_mezzanine(source)
    assert necessary is False


# ── The mezzanine ────────────────────────────────────────────────────

def test_mezzanine_is_same_rate_and_keeps_alpha(prores_with_sound,
                                               tmp_path):
    """The two proofs the transcode is a codec change and not a conform:
    every source frame survives 1:1, and the alpha still draws."""
    source = bm.measure_source(prores_with_sound)
    out = bm.ensure_mezzanine(source, str(tmp_path))
    assert out.endswith(".webm")
    assert os.path.isfile(out)
    rebuilt = bm.measure_source(out)
    assert rebuilt.frame_count == source.frame_count
    assert rebuilt.fps == pytest.approx(source.fps)
    assert rebuilt.element.alpha.draws is True


def test_mezzanine_is_reused_when_fresh(prores_with_sound, tmp_path):
    source = bm.measure_source(prores_with_sound)
    first = bm.ensure_mezzanine(source, str(tmp_path))
    mtime = os.path.getmtime(first)
    second = bm.ensure_mezzanine(source, str(tmp_path))
    assert second == first
    assert os.path.getmtime(first) == mtime


def test_mezzanine_rebuilds_when_the_source_is_newer(prores_with_sound,
                                                    tmp_path):
    import time
    source = bm.measure_source(prores_with_sound)
    first = bm.ensure_mezzanine(source, str(tmp_path))
    mtime = os.path.getmtime(first)
    time.sleep(1.05)
    os.utime(prores_with_sound, None)
    source = bm.measure_source(prores_with_sound)
    second = bm.ensure_mezzanine(source, str(tmp_path))
    assert second == first
    assert os.path.getmtime(second) > mtime


def test_stage_mezzanine_lands_one_named_file(webm_with_alpha, tmp_path):
    staged = bm.stage_mezzanine(webm_with_alpha, str(tmp_path))
    assert staged == "brand/sting.webm"
    assert os.path.isfile(tmp_path / "public" / "brand" / "sting.webm")


# ── The conform gate ─────────────────────────────────────────────────

def test_require_conform_passes_the_two_runnable_strategies():
    assert bm.require_conform("native_sample") == "native_sample"
    assert bm.require_conform({"conform": "resolve_native"}) == (
        "resolve_native")


def test_require_conform_names_all_three_when_nothing_is_declared():
    with pytest.raises(ConformNotDeclared) as excinfo:
        bm.require_conform({})
    message = str(excinfo.value)
    assert "native_sample" in message
    assert "blended_conform" in message
    assert "resolve_native" in message


def test_require_conform_refuses_an_unknown_name_by_name():
    with pytest.raises(ConformNotDeclared) as excinfo:
        bm.require_conform("retime_with_blending")
    assert "retime_with_blending" in str(excinfo.value)


def test_blended_conform_is_named_but_not_built():
    """The taste question, stated as code: the refusal carries the two
    values (which blender, how much blend) nobody has stated, so the
    captain's answer can be exactly those."""
    with pytest.raises(ConformNotBuilt) as excinfo:
        bm.require_conform("blended_conform")
    message = str(excinfo.value).lower()
    assert "minterpolate" in message
    assert "framerate" in message


# ── Props for the composition ────────────────────────────────────────

def _props_source(prores_with_sound):
    return bm.measure_source(prores_with_sound)


def test_props_render_the_whole_file_at_the_composition_rate(
        prores_with_sound):
    source = _props_source(prores_with_sound)
    props = bm.brand_motion_props(
        "brand/sting.webm", source, fps=FPS, width=64, height=64,
        muted=True, conform="native_sample")
    assert props["src"] == "brand/sting.webm"
    assert props["durationInFrames"] == max(
        1, round(source.duration_seconds * FPS))
    assert props["muted"] is True
    assert props["volume"] == 1.0


def test_props_accept_explicit_silence_or_sound(prores_with_sound):
    source = _props_source(prores_with_sound)
    assert bm.brand_motion_props(
        "brand/sting.webm", source, fps=FPS, width=64, height=64,
        muted=False, conform="native_sample")["muted"] is False


def test_props_refuse_an_unstated_muted(prores_with_sound):
    """Both real assets carry an audio stream; silence vs sound is a
    choice, so an absent value is refused rather than defaulted."""
    source = _props_source(prores_with_sound)
    with pytest.raises(BrandMotionError):
        bm.brand_motion_props(
            "brand/sting.webm", source, fps=FPS, width=64, height=64,
            muted=None, conform="native_sample")


def test_props_refuse_a_geometry_mismatch(prores_with_sound):
    """Contain shrinks the mark, cover crops it - both are framing taste,
    so the engine fits nothing."""
    source = _props_source(prores_with_sound)
    with pytest.raises(GeometryMismatch):
        bm.brand_motion_props(
            "brand/sting.webm", source, fps=FPS, width=1080, height=1920,
            muted=True, conform="native_sample")


def test_props_refuse_anything_but_native_sample(prores_with_sound):
    """Only native_sample reaches a Remotion render: blended_conform is
    not built and resolve_native renders nothing. Props written against
    either must not reach the renderer under this strategy's name."""
    source = _props_source(prores_with_sound)
    with pytest.raises((ConformNotDeclared, ConformNotBuilt)):
        bm.brand_motion_props(
            "brand/sting.webm", source, fps=FPS, width=64, height=64,
            muted=True, conform="resolve_native")
    with pytest.raises((ConformNotDeclared, ConformNotBuilt)):
        bm.brand_motion_props(
            "brand/sting.webm", source, fps=FPS, width=64, height=64,
            muted=True, conform="blended_conform")


def test_props_refuse_an_empty_staged_path(prores_with_sound):
    source = _props_source(prores_with_sound)
    with pytest.raises(BrandMotionError):
        bm.brand_motion_props(
            "", source, fps=FPS, width=64, height=64,
            muted=True, conform="native_sample")


# ── The composition slot stays a video slot ──────────────────────────

def test_brand_motion_composition_reads_a_video_file():
    """The gap this task closed, pinned: BrandMotion must keep reading a
    staged video file through staticFile, and Root must keep registering
    it. If either half lapses, no composition reads video again."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    component = (root / "remotion-subtitles" / "src" / "compositions"
                 / "BrandMotion" / "index.tsx").read_text(
                     encoding="utf-8")
    assert "OffthreadVideo" in component
    assert "staticFile(src)" in component
    registered = (root / "remotion-subtitles" / "src" /
                  "Root.tsx").read_text(encoding="utf-8")
    assert 'id="BrandMotion"' in registered


# ── The CLI ──────────────────────────────────────────────────────────

def test_cli_measure_reports_the_slot(prores_with_sound):
    result = subprocess.run(
        ["python3", "-m", "library.tools.brand_motion",
         "--measure", prores_with_sound],
        capture_output=True, text=True, encoding="utf-8", check=False)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["frame_count"] == 6
    assert report["has_audio"] is True
    assert report["needs_mezzanine"] is True
