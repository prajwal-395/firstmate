"""The end logo reads as light, and each half of that is measured.

The captain's complaint was "a thick blob", which is a judgement - but
every property that produces the judgement is a number, and this file
pins the numbers rather than asserting the taste. Each one is proved in
both directions where a direction exists (AGENTS.md 10.4): the halo is
shown being discarded AND the ink being kept, the dark base is shown not
emitting AND the bright one emitting, the light is shown reaching 128 px
AND the delivered profile not.

Nothing here reads the captain's real ``logo_reveal.mov``. The fixtures
are synthetic marks built in memory with the delivered file's measured
shape - a bright orange stroke, a dark navy base, and a soft collar of
authored halo around both - so the answers are known before anything is
measured, and no test touches a real project (AGENTS.md 8).
"""
from __future__ import annotations

import shutil
import subprocess

import numpy as np
import pytest
from scipy import ndimage

from library.tools import logo_relight as lr
from library.tools.logo_relight import LightProfile, SourceNotCarried

# The two colours measured off the delivered asset, in [0, 1].
ORANGE = (255 / 255, 170 / 255, 77 / 255)
NAVY = (39 / 255, 51 / 255, 66 / 255)


# ── Fixtures whose answers are known before anything is measured ─────

def _rows(size: int):
    """Where the two bars sit, as fractions of the frame."""
    scale = size / 256.0
    return (slice(int(60 * scale), int(80 * scale)),
            slice(int(150 * scale), int(170 * scale)),
            slice(int(40 * scale), int(216 * scale)))


def _mark(size: int = 256, collar_peak: float = 0.42) -> np.ndarray:
    """A synthetic mark in the delivered file's shape.

    An orange bar and a navy bar, both hard-edged and opaque, wrapped in
    a soft collar of authored halo - the single Gaussian at moderate
    opacity that the real file carries and that this module exists to
    replace. The mark scales with ``size``; the collar does NOT, because
    the collar is the thing under test and it was measured at the
    delivery width - one Gaussian of about 8 px, spent by 21.
    """
    collar_sigma = 8.0 * size / 1080.0
    bright, dark, span = _rows(size)
    frame = np.zeros((size, size, 4), dtype=np.float64)
    ink = np.zeros((size, size), dtype=np.float64)
    ink[bright, span] = 1.0           # the bright stroke
    ink[dark, span] = 1.0             # the dark base

    colour = np.zeros((size, size, 3), dtype=np.float64)
    colour[bright, span] = ORANGE
    colour[dark, span] = NAVY

    collar = ndimage.gaussian_filter(ink, sigma=collar_sigma)
    if collar.max() > 0:
        collar = collar / collar.max() * collar_peak
    spread = ndimage.gaussian_filter(colour, sigma=(collar_sigma,
                                                    collar_sigma, 0))
    carried = np.where(ink[..., None] > 0, colour, spread)

    frame[..., :3] = carried
    frame[..., 3] = np.maximum(ink, collar)
    return frame


def _sequence(frames: int = 24, size: int = 256) -> list:
    """A mark that draws itself on over half the sequence, then holds."""
    out = []
    for index in range(frames):
        grown = min(1.0, (index + 1) / (frames / 2))
        frame = _mark(size)
        cut = 40 + int((216 - 40) * grown)
        frame[:, cut:, :] = 0.0
        out.append(frame)
    return out


def _bands(alpha: np.ndarray, core: np.ndarray):
    """(outer distance, mean alpha) outside ``core``, doubling each band."""
    distance = ndimage.distance_transform_edt(~core)
    rows = []
    edges = [0, 2, 4, 8, 16, 32, 64, 128, 256]
    for lo, hi in zip(edges[:-1], edges[1:]):
        band = (~core) & (distance > lo) & (distance <= hi)
        rows.append((hi, float(alpha[band].mean()) if band.any() else 0.0))
    return rows


def _decay(alpha: np.ndarray, core: np.ndarray, out_to: int = 128):
    """How much the light falls by, band over band."""
    means = [mean for hi, mean in _bands(alpha, core) if hi <= out_to]
    return [before / after if after > 0 else float("inf")
            for before, after in zip(means, means[1:])]


FULL = 1080
"""The falloff tests run at the delivery width, because the octaves are
stated at it and scale with it - a 256 px fixture would be measuring a
256 px light."""


# ── The authored halo goes; the authored ink stays ───────────────────

def test_the_authored_collar_is_discarded():
    profile = LightProfile()
    frame = _mark()
    _, ink_alpha = lr.separate_ink(frame, profile)

    solid = frame[..., 3] >= 0.999
    collar = (frame[..., 3] > 0.02) & ~solid

    assert collar.sum() > 1000, "the fixture must carry a collar to discard"
    assert ink_alpha[collar].max() < 0.02, (
        "the authored halo survived the separation")


def test_the_authored_ink_survives_unchanged():
    profile = LightProfile()
    frame = _mark()
    ink_rgb, ink_alpha = lr.separate_ink(frame, profile)

    solid = frame[..., 3] >= 0.999
    assert np.allclose(ink_alpha[solid], 1.0)
    assert np.allclose(ink_rgb[solid], frame[..., :3][solid])


def test_the_split_follows_the_frame_not_a_fixed_number():
    """The reveal ramps from 1/255 and the tail fades to 46/255."""
    profile = LightProfile()
    faded = _mark()
    faded[..., 3] *= 46 / 255

    _, ink_alpha = lr.separate_ink(faded, profile)
    solid = _mark()[..., 3] >= 0.999

    assert ink_alpha[solid].min() > 0.9 * (46 / 255), (
        "a fixed threshold would have erased the mark during the fade")
    assert ink_alpha[~solid].max() < 0.02 * (46 / 255) + 0.005


# ── A dark object does not emit ──────────────────────────────────────

def test_the_dark_base_casts_no_light_and_the_bright_stroke_does():
    profile = LightProfile()
    frame = _mark()
    emit = lr.emission(*lr.separate_ink(frame, profile), profile)

    rows_bright, rows_dark, span = _rows(frame.shape[0])
    bright = np.zeros(frame.shape[:2], dtype=bool)
    bright[rows_bright, span] = True
    dark = np.zeros(frame.shape[:2], dtype=bool)
    dark[rows_dark, span] = True

    assert emit[bright].min() > 0.9, "the filament must emit"
    assert emit[dark].max() == 0.0, "a dark object cannot emit"


def test_the_base_still_receives_the_light_it_is_lit_by():
    profile = LightProfile()
    frame = _mark()
    relit = lr.relight_frame(frame, profile.glow_gain, profile)

    _, rows_dark, span = _rows(frame.shape[0])
    dark = np.zeros(frame.shape[:2], dtype=bool)
    dark[rows_dark, span] = True
    assert relit[..., 3][dark].min() >= 0.999, (
        "the base is still drawn, it just does not glow")


# ── The falloff is light-shaped, and the delivered one is not ────────

def test_the_light_reaches_where_the_delivered_collar_does_not():
    profile = LightProfile()
    frame = _mark(size=FULL)
    relit = lr.relight_frame(frame, profile.glow_gain, profile)
    core = frame[..., 3] >= 0.999

    delivered = dict(_bands(frame[..., 3], core))
    new = dict(_bands(relit[..., 3], core))

    assert delivered[32] < 0.004, (
        "the delivered collar is spent by 32 px - that is the premise")
    assert delivered[128] < 1e-5
    assert new[128] > 0.01, "the new light must still carry at 128 px"


def test_the_falloff_decays_all_the_way_out():
    profile = LightProfile()
    relit = lr.relight_frame(_mark(size=FULL), profile.glow_gain, profile)
    core = _mark(size=FULL)[..., 3] >= 0.999

    means = [mean for _, mean in _bands(relit[..., 3], core)]
    assert all(a > b for a, b in zip(means, means[1:])), (
        f"light must fall off monotonically, got {means}")


def test_the_falloff_is_a_power_law_rather_than_a_blur():
    """The shape of glare: the same rate of decay at every distance.

    A Gaussian falls off super-exponentially, so its band-over-band
    decay grows without bound and the light simply stops somewhere. A
    sum of octaves holds a roughly constant decay all the way out, which
    is what "steep near the source and shallow far from it" means once
    it is a number rather than a phrase.
    """
    profile = LightProfile()
    relit = lr.relight_frame(_mark(size=FULL), profile.glow_gain, profile)
    core = _mark(size=FULL)[..., 3] >= 0.999

    decay = _decay(relit[..., 3], core)
    assert min(decay) > 1.2, f"the light must keep falling off: {decay}"
    assert max(decay) < 4.0, f"and never fall off a cliff: {decay}"


def test_one_octave_alone_cannot_do_both_ends():
    """The other direction, and it has two halves.

    A blur tight enough to give the mark a hot rim has nothing left at
    64 px; a blur wide enough to reach 128 px is so flat that the rim
    and the far field are the same brightness. Neither is light. That is
    why the octaves are summed rather than chosen between.
    """
    core = _mark(size=FULL)[..., 3] >= 0.999

    tight = LightProfile(octaves=((12.0, 4.35),))
    decay = _decay(lr.relight_frame(_mark(size=FULL), tight.glow_gain,
                                    tight)[..., 3], core)
    assert max(decay) > 4.0, (
        f"a blur tight enough to be hot at the rim cliffs: {decay}")

    wide = LightProfile(octaves=((190.0, 0.28),))
    decay = _decay(lr.relight_frame(_mark(size=FULL), wide.glow_gain,
                                    wide)[..., 3], core)
    assert min(decay) < 1.2, (
        f"a blur wide enough to reach is flat at the rim: {decay}")


# ── Adding, not covering ─────────────────────────────────────────────

def test_the_light_only_ever_brightens():
    profile = LightProfile()
    frame = _mark()
    relit = lr.relight_frame(frame, profile.glow_gain, profile)

    _, ink_alpha = lr.separate_ink(frame, profile)
    assert (relit[..., 3] + 1e-9 >= ink_alpha).all(), (
        "an additive light cannot take alpha away from the mark")

    solid = frame[..., 3] >= 0.999
    luma_before = frame[..., :3][solid] @ [0.2126, 0.7152, 0.0722]
    luma_after = relit[..., :3][solid] @ [0.2126, 0.7152, 0.0722]
    assert (luma_after + 1e-9 >= luma_before).all()


def test_core_light_zero_leaves_the_mark_exactly_as_authored():
    """The captain's colour, byte for byte, when nothing is asked of it."""
    profile = LightProfile(core_light=0.0)
    frame = _mark()
    relit = lr.relight_frame(frame, profile.glow_gain, profile)

    solid = frame[..., 3] >= 0.999
    assert np.allclose(relit[..., :3][solid], frame[..., :3][solid],
                       atol=1e-9)


def test_the_declared_core_light_lifts_the_mark_and_holds_its_hue():
    profile = LightProfile()
    frame = _mark()
    relit = lr.relight_frame(frame, profile.glow_gain, profile)

    rows_bright, _, span = _rows(frame.shape[0])
    bright = np.zeros(frame.shape[:2], dtype=bool)
    bright[rows_bright.start + 4:rows_bright.stop - 4,
           span.start + 20:span.stop - 20] = True   # inside the stroke
    before = frame[..., :3][bright].mean(axis=0)
    after = relit[..., :3][bright].mean(axis=0)

    assert after[0] >= before[0]
    assert (after >= before - 1e-9).all(), "the lift is up its own ramp"
    assert after[1] - before[1] < 0.12, (
        f"0.05 of core light should be a step, not a repaint: {after}")


# ── Peak and settle ──────────────────────────────────────────────────

def test_the_envelope_peaks_while_the_mark_grows_and_settles_after():
    profile = LightProfile()
    frames = _sequence()
    totals, levels = [], []
    for frame in frames:
        ink_rgb, ink_alpha = lr.separate_ink(frame, profile)
        totals.append(float(lr.emission(ink_rgb, ink_alpha, profile).sum()))
        levels.append(float(ink_alpha.max()))
    envelope = lr.intensity_envelope(totals, levels, profile)

    peak = int(np.argmax(envelope))
    assert peak < len(frames) // 2 + 2, (
        f"the surge belongs to the draw-on, not the hold (peaked at {peak})")
    assert envelope[peak] > profile.glow_gain * 1.2
    assert envelope[-1] == pytest.approx(profile.glow_gain, abs=0.02), (
        "the surge must release to the settled level")


def test_the_surge_belongs_to_the_growth_and_releases_after_it():
    profile = LightProfile()
    envelope = lr.intensity_envelope([5.0] * 12, [1.0] * 12, profile)

    assert envelope[0] > profile.glow_gain, (
        "a mark that arrives at full size has switched on, and that surges")
    assert all(a >= b for a, b in zip(envelope, envelope[1:])), (
        "with no further growth the surge only releases")
    assert envelope[-1] == pytest.approx(profile.glow_gain, abs=0.02)


def test_an_empty_sequence_settles_rather_than_dividing_by_zero():
    profile = LightProfile()
    assert lr.intensity_envelope([0.0, 0.0], [0.0, 0.0],
                                 profile) == [profile.glow_gain] * 2


def test_the_light_goes_out_with_the_mark():
    """The delivered tail is where the relit tail has to land.

    The ink fades to 0.164 on the last frame of the delivered file. The
    light is ADDED to the ink, so without scaling by how lit the mark is
    the relit tail measured 0.40 - the logo stopped going out, which is
    a change to the captain's timing.
    """
    profile = LightProfile()
    frames = _sequence()
    for index, frame in enumerate(frames[len(frames) // 2:],
                                  start=len(frames) // 2):
        frames[index] = frame * np.array([1.0, 1.0, 1.0, 0.0])[None, None, :] \
            + frame * np.array([0.0, 0.0, 0.0, 1.0])[None, None, :] * 0.16

    relit = lr.relight_sequence(frames, profile)
    delivered_tail = float(frames[-1][..., 3].max())
    relit_tail = float(relit[-1][..., 3].max())

    assert relit_tail < delivered_tail * 1.35, (
        f"the light outlived the mark: {relit_tail:.3f} against "
        f"{delivered_tail:.3f}")
    assert relit_tail >= delivered_tail, "the mark itself must still be drawn"


# ── The pyramid is not a shortcut that shows ─────────────────────────

def test_the_decimated_octaves_match_the_full_resolution_field():
    profile = LightProfile()
    emit = lr.emission(*lr.separate_ink(_mark(size=FULL), profile), profile)

    fast = lr.glow_field(emit, profile)
    full = np.zeros_like(emit)
    for sigma, gain in profile.sigmas_for(FULL):
        full += gain * ndimage.gaussian_filter(emit, sigma=sigma,
                                               mode="constant")

    worst = float(np.abs(fast - full).max()) * profile.glow_gain
    assert worst * 255 < 0.5, (
        f"the pyramid drifts {worst * 255:.2f} of an 8-bit step")


def test_a_wide_octave_is_actually_decimated():
    """The other direction: the speed is real, not an unused branch."""
    profile = LightProfile()
    assert max(int(sigma / lr.WORKING_SIGMA)
               for sigma, _ in profile.sigmas_for(1080)) > 1


# ── Dither, because the far field is two code values deep ────────────

def test_dither_carries_a_signal_finer_than_one_code_value():
    """What plain rounding does to the far field, and what dither does.

    The far field is a gradient two or three code values deep spread
    over hundreds of pixels. Rounding lands every pixel of a wide band
    on one value - concentric rings. Triangular dither keeps the mean,
    so the band stays a gradient.
    """
    faint = np.full((256, 256), 0.4 / 255.0)

    plain = np.rint(faint * 255.0)
    dithered = lr._dither(faint, 255.0, np.random.default_rng(0))

    assert plain.mean() == 0.0, "plain rounding loses the whole signal"
    assert dithered.mean() == pytest.approx(0.4, abs=0.02)


def test_dither_does_not_move_a_value_that_is_already_on_a_level():
    ramp = np.full((64, 64), 7.0 / 255.0)
    dithered = lr._dither(ramp, 255.0, np.random.default_rng(1))
    assert dithered.mean() == pytest.approx(7.0, abs=0.05)
    assert dithered.min() >= 6.0 and dithered.max() <= 8.0


# ── The file comes through unchanged where it must ───────────────────

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason=(
        "ffmpeg/ffprobe is not available here, so no fixture can be "
        "encoded or measured. Runs anywhere ffmpeg and ffprobe are on "
        "PATH - the CI runner installs them and AGENTS.md 9 requires them "
        "for any real run."
    ),
)


def _fixture_mov(path, *, frames: int = 8, rate: str = "24000/1001") -> str:
    """A tiny ProRes 4444 clip with a real alpha plane and an audio track."""
    duration = frames / (24000 / 1001 if rate == "24000/1001"
                         else float(rate.split("/")[0]))
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i",
         (f"color=c=black@0.0:s=128x128:d={duration}:r={rate},"
          f"format=yuva444p10le"),
         "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=mono:d={duration}",
         "-c:v", "prores_ks", "-profile:v", "4444",
         "-pix_fmt", "yuva444p10le", "-c:a", "pcm_s16le",
         "-frames:v", str(frames), str(path)],
        check=True, capture_output=True, encoding="utf-8")
    return str(path)


@needs_ffmpeg
def test_relight_carries_rate_geometry_and_frame_count(tmp_path):
    source = _fixture_mov(tmp_path / "in.mov")
    report = lr.relight_file(source, str(tmp_path / "out.mov"))

    for key in ("width", "height", "r_frame_rate", "nb_frames"):
        assert str(report["source"][key]) == str(report["relit"][key])
    assert report["frames"] == 8


@needs_ffmpeg
def test_relight_leaves_the_source_alone(tmp_path):
    source = _fixture_mov(tmp_path / "in.mov")
    before = (tmp_path / "in.mov").read_bytes()
    lr.relight_file(source, str(tmp_path / "out.mov"))
    assert (tmp_path / "in.mov").read_bytes() == before


@needs_ffmpeg
def test_a_source_with_no_picture_is_refused(tmp_path):
    audio = tmp_path / "audio.wav"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", "anullsrc=r=48000:cl=mono:d=0.2", str(audio)],
        check=True, capture_output=True, encoding="utf-8")

    with pytest.raises(SourceNotCarried):
        lr.probe(str(audio))


@needs_ffmpeg
def test_the_side_by_side_refuses_a_mismatched_pair(tmp_path):
    short = _fixture_mov(tmp_path / "short.mov", frames=4)
    long = _fixture_mov(tmp_path / "long.mov", frames=8)

    with pytest.raises(SourceNotCarried):
        lr.side_by_side(short, long, str(tmp_path / "cmp.mp4"))


@needs_ffmpeg
def test_the_side_by_side_encodes_a_double_width_clip(tmp_path):
    source = _fixture_mov(tmp_path / "in.mov")
    relit = str(tmp_path / "out.mov")
    lr.relight_file(source, relit)
    out = lr.side_by_side(source, relit, str(tmp_path / "cmp.mp4"), scale=2)

    probed = lr.probe(out)
    assert int(probed["width"]) == 128, "two 64-wide tiles side by side"
    assert int(probed["height"]) == 64


# ── The profile is one object, and changing it changes the render ────

def test_every_declared_value_is_on_the_profile():
    """A constant nobody can override is a constant somebody will edit."""
    profile = LightProfile()
    for name, value in (("ink_lo", lr.INK_LO), ("ink_hi", lr.INK_HI),
                        ("emit_lo", lr.EMIT_LO), ("emit_hi", lr.EMIT_HI),
                        ("octaves", lr.OCTAVES), ("hot", lr.HOT),
                        ("glow_gain", lr.GLOW_GAIN),
                        ("core_light", lr.CORE_LIGHT),
                        ("surge_gain", lr.SURGE_GAIN),
                        ("surge_release", lr.SURGE_RELEASE)):
        assert getattr(profile, name) == value


def test_the_sigmas_scale_with_the_frame():
    profile = LightProfile()
    at_1080 = profile.sigmas_for(1080)
    at_2160 = profile.sigmas_for(2160)
    assert [s for s, _ in at_2160] == [2 * s for s, _ in at_1080]


def test_a_zero_sigma_is_refused_rather_than_silently_skipped():
    profile = LightProfile(octaves=((0.0, 1.0),))
    with pytest.raises(ValueError):
        lr.glow_field(np.zeros((32, 32)), profile)
