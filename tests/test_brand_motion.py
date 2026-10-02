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

import os
import shutil
import subprocess

import pytest

from library.tools import brand_motion as bm
from library.tools import transition_overlay as ov
from library.tools.brand_motion import (
    BrandMotionError,
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




def test_measure_source_refuses_absent_and_empty_alpha_differently(
        prores_no_alpha, prores_empty_alpha):
    """The trap the shared taxonomy exists to avoid: absent is absent,
    not zero. Without this the refusal below proves nothing - an
    instrument that always raises would pass it."""
    with pytest.raises(ov.ElementHasNoAlpha):
        bm.measure_source(prores_no_alpha)
    # Present-and-empty is a DIFFERENT refusal from absent (AGENTS.md
    # 10.2: an overlay that draws nothing is not rendered).
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








# ── The conform gate ─────────────────────────────────────────────────





def test_require_conform_refuses_unknown_and_unbuilt_by_name():
    with pytest.raises(ConformNotDeclared) as excinfo:
        bm.require_conform("retime_with_blending")
    assert "retime_with_blending" in str(excinfo.value)
    # blended_conform is named but not built: the refusal carries the
    # two values (which blender, how much blend) nobody has stated.
    with pytest.raises(ConformNotBuilt) as excinfo:
        bm.require_conform("blended_conform")
    message = str(excinfo.value).lower()
    assert "minterpolate" in message
    assert "framerate" in message


# ── Props for the composition ────────────────────────────────────────

def _props_source(prores_with_sound):
    return bm.measure_source(prores_with_sound)






def test_props_refuse_what_a_choice_or_the_renderer_has_not_settled(
        prores_with_sound):
    """Silence vs sound and contain vs cover are taste, so an absent
    `muted` and a geometry mismatch refuse rather than default; only
    native_sample reaches a Remotion render."""
    source = _props_source(prores_with_sound)
    with pytest.raises(BrandMotionError):
        bm.brand_motion_props(
            "brand/sting.webm", source, fps=FPS, width=64, height=64,
            muted=None, conform="native_sample")
    with pytest.raises(GeometryMismatch):
        bm.brand_motion_props(
            "brand/sting.webm", source, fps=FPS, width=1080, height=1920,
            muted=True, conform="native_sample")
    for conform in ("resolve_native", "blended_conform"):
        with pytest.raises((ConformNotDeclared, ConformNotBuilt)):
            bm.brand_motion_props(
                "brand/sting.webm", source, fps=FPS, width=64, height=64,
                muted=True, conform=conform)
