"""The closing animation is four beats, and each one is a measurement.

The captain's words were "a quick soft flash like a light bulb ... and
then the animation of the logo just fades out to nothing", which is
taste - but every property that produces it is a number. This file pins
the numbers, in both directions wherever a direction exists (AGENTS.md
10.4): the flash is shown peaking ON the completion frame AND nowhere
else, the attack is shown shorter than the release AND the release
longer than the attack, the fade is shown reaching zero AND the
delivered tail not, the light is shown only ever brightening the ground
AND the ink still occluding it.

Nothing here reads the captain's real ``logo_reveal_23976.mov``. The
fixtures are synthetic marks built in memory with the delivered file's
measured shape - an orange stroke, a navy base, an authored collar - on
a sequence with the delivered file's measured TIMING: a draw-on, a
hold, and a fade that stops short of zero. So the answers are known
before anything is measured, and no test touches a real project
(AGENTS.md 8).
"""
from __future__ import annotations

import itertools
import shutil
import subprocess

import numpy as np
import pytest

from library.tools import logo_bulb as lb
from library.tools.logo_bulb import ClosingProfile, SourceNotClosed

ORANGE = (255 / 255, 170 / 255, 77 / 255)
NAVY = (39 / 255, 51 / 255, 66 / 255)
LUCIE_GROUND = (0x25 / 255, 0x37 / 255, 0x46 / 255)

RATE = 24000 / 1001


# ── Fixtures whose answers are known before anything is measured ─────

def _mark(size: int, drawn: float, level: float) -> np.ndarray:
    """One frame: an orange bar and a navy bar, drawn ``drawn`` of the
    way across, at ``level`` alpha, inside a soft authored collar.
    """
    from scipy import ndimage

    frame = np.zeros((size, size, 4), dtype=np.float64)
    bright = slice(int(size * 0.23), int(size * 0.31))
    dark = slice(int(size * 0.59), int(size * 0.66))
    span = slice(int(size * 0.16), int(size * 0.16 + size * 0.68 * drawn))
    ink = np.zeros((size, size), dtype=np.float64)
    if span.stop > span.start:
        ink[bright, span] = 1.0
        ink[dark, span] = 1.0
        frame[bright, span, :3] = ORANGE
        frame[dark, span, :3] = NAVY
    collar = ndimage.gaussian_filter(ink, sigma=8.0 * size / 1080.0)
    frame[..., 3] = np.clip(ink + 0.42 * collar * (1.0 - ink), 0.0, 1.0)
    frame[..., 3] *= level
    return frame


DRAW = 24
HOLD = 8
FADE = 10
TAIL = 0.164
"""The delivered animation's own timing, in frames and in alpha.

``logo_reveal_23976.mov`` draws on, holds, and then fades LINEARLY to
0.164 of full alpha on its last frame and stops there. That last number
is the one the captain's "fades out to nothing" is about, and it is why
these fixtures end short of zero rather than at it."""


def _sequence(size: int = 192) -> list:
    """Draw on, hold, then fade - and stop at :data:`TAIL`, not at zero."""
    out = [_mark(size, (index + 1) / DRAW, 1.0) for index in range(DRAW)]
    out += [_mark(size, 1.0, 1.0) for _ in range(HOLD)]
    for index in range(FADE):
        share = (index + 1) / FADE
        out.append(_mark(size, 1.0, 1.0 - (1.0 - TAIL) * share))
    return out


COMPLETION = DRAW - 1
"""The frame the fixture's mark finishes arriving on."""


def _profile(**kwargs) -> ClosingProfile:
    return ClosingProfile(ground=LUCIE_GROUND, **kwargs)


# ── Beat 3: one flash, and it lands on the completion frame ──────────

def test_completion_is_the_first_frame_the_mark_is_whole_on():
    frames = _sequence()
    areas, _ = lb.mark_measurements(frames, _profile())
    assert lb.completion_index(areas, _profile()) == COMPLETION


def test_completion_is_the_first_full_frame_not_the_biggest_one():
    """The mark holds after it completes. The flash belongs to the
    arrival, so a later held frame measuring a hair more must not win."""
    areas, _ = lb.mark_measurements(_sequence(), _profile())
    assert np.argmax(areas) >= COMPLETION
    biggest = int(np.argmax(areas))
    chosen = lb.completion_index(areas, _profile())
    assert chosen <= biggest
    assert areas[chosen] >= 0.995 * areas[biggest]


def test_the_flash_peaks_on_the_completion_frame_and_nowhere_else():
    envelope = lb.flash_envelope(DRAW + HOLD + FADE, COMPLETION, RATE,
                                 _profile())
    assert envelope[COMPLETION] == pytest.approx(1.0)
    others = envelope[:COMPLETION] + envelope[COMPLETION + 1:]
    assert max(others) < 1.0


def test_the_flash_is_quick_up_and_slower_down():
    """The whole resolution of "quick soft flash": a short attack reads
    as quick, and a release two and a half times longer reads as soft.
    Symmetry was available and would give a swell, not a switch."""
    profile = _profile()
    envelope = lb.flash_envelope(64, 32, RATE, profile)
    rise = sum(1 for value in envelope[:33] if value > 0.05)
    fall = sum(1 for value in envelope[33:] if value > 0.05)
    assert rise < fall
    assert fall / rise > 2.0
    assert profile.release_seconds > 2.0 * profile.attack_seconds


def test_the_flash_leaves_and_arrives_without_a_step():
    """Soft means no edge, at either end of the rise. A smoothstep has
    zero slope at both, so the two frames either side of each end move
    less than the two in the middle of the rise."""
    envelope = lb.flash_envelope(64, 32, RATE, _profile())
    attack = max(1, round(lb.ATTACK_SECONDS * RATE))
    steps = np.diff(envelope[32 - attack:33])
    assert steps[0] < steps.max()
    assert steps[-1] < steps.max()
    assert envelope[32 - attack] == pytest.approx(0.0)


def test_the_light_sits_at_the_base_until_the_flash_and_after_it():
    profile = _profile()
    envelope = lb.intensity_envelope(64, 32, RATE, profile)
    assert envelope[0] == pytest.approx(profile.base_light)
    assert envelope[32] == pytest.approx(profile.flash_light)
    assert envelope[-1] == pytest.approx(profile.base_light, abs=1e-3)
    assert profile.flash_light > 5.0 * profile.base_light


def test_the_flash_is_over_within_the_declared_release():
    """A tenth of the flash left, after ``RELEASE_SECONDS``. That is what
    makes it an event rather than the hold the captain rejected."""
    profile = _profile()
    envelope = lb.flash_envelope(120, 20, RATE, profile)
    tenth = 20 + round(profile.release_seconds * RATE)
    assert envelope[tenth] <= 0.1
    assert envelope[tenth - 1] > 0.1


# ── Beat 4: it fades out to NOTHING ──────────────────────────────────

def test_the_delivered_tail_does_not_reach_zero_by_itself():
    """The direction the fix is measured against."""
    _, levels = lb.mark_measurements(_sequence(), _profile())
    assert levels[-1] / max(levels) == pytest.approx(TAIL, rel=0.02)
    assert levels[-1] > 0.0


def test_the_carried_fade_reaches_exactly_zero():
    _, levels = lb.mark_measurements(_sequence(), _profile())
    scale = lb.fade_scale(levels, _profile())
    assert scale[-1] == 0.0
    assert levels[-1] * scale[-1] == 0.0


def test_nothing_before_the_fade_is_moved_by_it():
    """The captain timed the draw-on and the hold. Only the tail changes."""
    _, levels = lb.mark_measurements(_sequence(), _profile())
    scale = lb.fade_scale(levels, _profile())
    assert all(value == 1.0 for value in scale[:DRAW + HOLD])


def test_the_carried_fade_keeps_the_delivered_ramp_monotone():
    _, levels = lb.mark_measurements(_sequence(), _profile())
    scale = lb.fade_scale(levels, _profile())
    carried = [level * value for level, value in zip(levels, scale)]
    tail = carried[DRAW + HOLD - 1:]
    assert all(a >= b for a, b in itertools.pairwise(tail))


def test_a_source_that_already_goes_out_is_left_alone():
    _, levels = lb.mark_measurements(_sequence(), _profile())
    levels = list(levels) + [0.0]
    assert lb.fade_scale(levels, _profile()) == [1.0] * len(levels)


def test_a_source_with_no_fade_to_carry_is_refused():
    """Authoring an ending is the captain's decision, not this module's."""
    _, levels = lb.mark_measurements(_sequence(), _profile())
    holding = list(levels[:DRAW + HOLD])
    with pytest.raises(SourceNotClosed, match="no delivered fade to finish"):
        lb.fade_scale(holding, _profile())


# ── Beat 1: the dark ground ──────────────────────────────────────────

def test_a_frame_with_no_mark_is_exactly_the_ground():
    profile = _profile()
    empty = np.zeros((32, 32, 4), dtype=np.float64)
    out = lb.bulb_frame(empty, profile.flash_light, 1.0, profile)
    assert np.allclose(out[..., :3], np.asarray(LUCIE_GROUND))


def test_the_closing_frame_is_opaque_everywhere():
    """The ground is part of the asset now, so there is no alpha left to
    composite it over the reel with - and nothing behind it to show."""
    frames = _sequence(size=96)
    out = lb.bulb_sequence(frames, RATE, _profile())
    assert all(float(frame[..., 3].min()) == 1.0 for frame in out)


def test_the_light_only_ever_brightens_the_ground():
    """A glow that blends can darken what it falls on. This one adds."""
    profile = _profile()
    frames = _sequence(size=96)
    out = lb.bulb_sequence(frames, RATE, profile)
    ground = np.asarray(LUCIE_GROUND)
    for frame, source in zip(out, frames):
        away = source[..., 3] <= 0.0
        if away.any():
            assert (frame[..., :3][away] >= ground - 1e-9).all()


def test_the_ink_still_occludes_the_ground_it_sits_on():
    """Ink is an object: over, not added. On a WHITE field, solid ink
    must still read as its own colour - if the ground were added in
    rather than covered, every stroke would wash out to white."""
    profile = ClosingProfile(ground=(1.0, 1.0, 1.0),
                             base_light=0.0, flash_light=0.0)
    frame = _mark(96, 1.0, 1.0)
    out = lb.bulb_frame(frame, 0.0, 1.0, profile)
    solid = frame[..., 3] >= 0.999
    assert solid.any()
    painted = out[..., :3][solid]
    assert np.allclose(np.unique(painted.round(6), axis=0),
                       np.unique(np.array([ORANGE, NAVY]).round(6), axis=0))


def test_the_ground_is_black_when_nothing_declares_one():
    assert ClosingProfile().ground == lb.BLACK
    assert lb.parse_ground("black") == (0.0, 0.0, 0.0)


# ── Where the ground comes from ──────────────────────────────────────

def test_a_declared_ground_is_read_off_the_brand_template(tmp_path):
    template = tmp_path / "brand.yaml"
    template.write_text(
        "content:\n  bookends:\n    end_card:\n      props:\n"
        '        bgColor: "#253746"\n', encoding="utf-8")
    assert lb.ground_from_brand_template(str(template)) == pytest.approx(
        LUCIE_GROUND)


def test_a_template_declaring_no_ground_is_refused_rather_than_defaulted(
        tmp_path):
    template = tmp_path / "brand.yaml"
    template.write_text("content:\n  bookends: {}\n", encoding="utf-8")
    with pytest.raises(SourceNotClosed, match="states no ground"):
        lb.ground_from_brand_template(str(template))


@pytest.mark.parametrize("text", ["#25374", "rebeccapurple", "253746ff", ""])
def test_a_ground_that_is_not_a_colour_is_refused(text):
    with pytest.raises(SourceNotClosed):
        lb.parse_ground(text)


# ── Refusals ─────────────────────────────────────────────────────────

def test_an_empty_sequence_has_no_completion_frame():
    with pytest.raises(SourceNotClosed):
        lb.completion_index([], _profile())


def test_a_sequence_with_no_ink_is_refused():
    with pytest.raises(SourceNotClosed, match="any ink"):
        lb.completion_index([0.0, 0.0], _profile())


def test_a_rate_of_zero_is_refused_rather_than_dividing():
    with pytest.raises(SourceNotClosed, match="positive"):
        lb.flash_envelope(10, 5, 0.0, _profile())


def test_every_declared_value_is_on_the_profile():
    """A project that wants a different closing passes a profile rather
    than editing a constant, so every constant has to be reachable."""
    profile = ClosingProfile()
    assert profile.complete_fraction == lb.COMPLETE_FRACTION
    assert profile.attack_seconds == lb.ATTACK_SECONDS
    assert profile.release_seconds == lb.RELEASE_SECONDS
    assert profile.base_light == lb.BASE_LIGHT
    assert profile.flash_light == lb.FLASH_LIGHT
    assert profile.tail_ceiling == lb.TAIL_CEILING
    assert profile.with_ground(LUCIE_GROUND).ground == LUCIE_GROUND
    assert profile.with_ground(LUCIE_GROUND).base_light == lb.BASE_LIGHT


# ── The file ─────────────────────────────────────────────────────────

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason=(
        "ffmpeg/ffprobe is not available here, so no fixture can be "
        "encoded or measured. Runs anywhere ffmpeg and ffprobe are on "
        "PATH - the CI runner installs them and AGENTS.md 9 requires them "
        "for any real run."
    ),
)


def _fixture_mov(path, *, frames: int = 8) -> str:
    """A tiny ProRes 4444 clip with a real alpha plane."""
    duration = frames / RATE
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         (f"color=c=black@0.0:s=64x64:d={duration}:r=24000/1001,"
          f"format=yuva444p10le"),
         "-c:v", "prores_ks", "-profile:v", "4444",
         "-pix_fmt", "yuva444p10le", "-frames:v", str(frames), str(path)],
        check=True, capture_output=True, encoding="utf-8")
    return str(path)


@needs_ffmpeg
def test_a_source_with_no_mark_in_it_is_refused(tmp_path):
    """An empty alpha plane has no completion frame to flash on."""
    source = _fixture_mov(tmp_path / "in.mov")
    with pytest.raises(SourceNotClosed):
        lb.render_file(source, str(tmp_path / "out.mov"), _profile())


@needs_ffmpeg
def test_the_source_is_never_modified(tmp_path):
    source = _fixture_mov(tmp_path / "in.mov")
    before = (tmp_path / "in.mov").read_bytes()
    with pytest.raises(SourceNotClosed):
        lb.render_file(source, str(tmp_path / "out.mov"), _profile())
    assert (tmp_path / "in.mov").read_bytes() == before


@needs_ffmpeg
def test_an_empty_kept_span_is_refused(tmp_path):
    reel = _fixture_mov(tmp_path / "reel.mov")
    frames = lb.bulb_sequence(_sequence(size=32), RATE, _profile())
    with pytest.raises(SourceNotClosed, match="empty"):
        lb.over_tail(reel, frames, "24000/1001",
                     str(tmp_path / "out.mp4"), 1.0, 1.0)


@needs_ffmpeg
def test_an_in_reel_preview_with_no_animation_is_refused(tmp_path):
    reel = _fixture_mov(tmp_path / "reel.mov")
    with pytest.raises(SourceNotClosed, match="no animation"):
        lb.over_tail(reel, [], "24000/1001",
                     str(tmp_path / "out.mp4"), 0.0, 0.1)
