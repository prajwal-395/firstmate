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

BASE_LANDS = 0.865
BASE_LANDS_AT = 0.656
"""When the navy base arrives, as a fraction of the draw-on, and how
much of itself it arrives with.

``logo_reveal_23976.mov`` draws its filament from frame 12 to frame 49
and does not touch the screw base until frame 44 - 0.865 of the way
through - where 0.656 of it lands at once and the rest creeps in by the
completion frame. The base therefore arrives against a field the mark
has already lit, which is why
:data:`~library.tools.logo_bulb.SEPARATION_FLOOR` is reachable at all.
A fixture that drew the base from the first frame would be measuring a
timing the delivered file does not have."""


def _mark(size: int, drawn: float, level: float) -> np.ndarray:
    """One frame: an orange bar and a navy bar, drawn ``drawn`` of the
    way across, at ``level`` alpha, inside a soft authored collar.

    The navy bar lands late, as the delivered file's base does
    (:data:`BASE_LANDS`).
    """
    from scipy import ndimage

    frame = np.zeros((size, size, 4), dtype=np.float64)
    bright = slice(int(size * 0.23), int(size * 0.31))
    dark = slice(int(size * 0.59), int(size * 0.66))
    base_drawn = 0.0
    if drawn >= BASE_LANDS:
        base_drawn = BASE_LANDS_AT + (1.0 - BASE_LANDS_AT) * (
            (drawn - BASE_LANDS) / (1.0 - BASE_LANDS))
    span = slice(int(size * 0.16), int(size * 0.16 + size * 0.68 * drawn))
    under = slice(int(size * 0.16),
                  int(size * 0.16 + size * 0.68 * base_drawn))
    ink = np.zeros((size, size), dtype=np.float64)
    if span.stop > span.start:
        ink[bright, span] = 1.0
        frame[bright, span, :3] = ORANGE
    if under.stop > under.start:
        ink[dark, under] = 1.0
        frame[dark, under, :3] = NAVY
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
    """A glow that blends can darken what it falls on. This one adds.

    Measured against the ground that is STANDING on each frame, because
    beat 5 takes the ground itself to black - the light must never
    darken the field, but the ending is allowed to.
    """
    profile = _profile()
    frames = _sequence(size=96)
    out = lb.bulb_sequence(frames, RATE, profile)
    _, levels = lb.mark_measurements(frames, profile)
    standing = lb.picture_fade(levels, profile)
    for frame, source, left in zip(out, frames, standing):
        away = source[..., 3] <= 0.0
        if away.any():
            ground = np.asarray(LUCIE_GROUND) * left
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


# ── Beat 5: the ground goes with them, and it ends on black ──────────

def test_the_ground_stands_at_full_until_the_mark_starts_to_go():
    """The flash is his and it happens on the navy. Nothing about the
    field may move before the fade the captain timed starts."""
    _, levels = lb.mark_measurements(_sequence(), _profile())
    standing = lb.picture_fade(levels, _profile())
    assert all(value == 1.0 for value in standing[:DRAW + HOLD])


def test_the_ground_travels_to_black_on_the_captain_s_own_slope():
    """One gesture, not two: the field rides the same curve the mark's
    own fade does, and the two are the SAME numbers."""
    _, levels = lb.mark_measurements(_sequence(), _profile())
    profile = _profile()
    standing = lb.picture_fade(levels, profile)
    carried = [level * scale for level, scale
               in zip(levels, lb.fade_scale(levels, profile))]
    top = max(carried)
    assert standing == pytest.approx([value / top for value in carried])
    assert standing[-1] == 0.0
    assert all(a >= b for a, b in itertools.pairwise(standing))


def test_the_last_frame_is_black_and_holds_nothing():
    """The direction the whole ruling is measured against: today the
    navy stood to the last frame."""
    frames = _sequence(size=96)
    out = lb.bulb_sequence(frames, RATE, _profile())
    assert float(np.abs(out[-1][..., :3]).max()) == 0.0
    # and it really is the ending that did it, not an empty source
    assert float(np.abs(out[DRAW + HOLD - 1][..., :3]).max()) > 0.0


def test_a_ground_that_did_not_travel_would_leave_the_navy_standing():
    """The gate can fail: hold the ground up and the last frame is the
    declared navy rather than black."""
    frames = _sequence(size=96)
    profile = _profile()
    held = lb.bulb_frame(frames[-1], profile.base_light, 0.0, profile)
    assert np.allclose(held[..., :3], np.asarray(LUCIE_GROUND))


# ── The base has to stay a base ──────────────────────────────────────

def test_the_base_sinks_into_the_navy_with_no_lift():
    """The direction. This is the measurement that sent the second
    version back: a navy base on a navy field."""
    frames = _sequence(size=192)
    flat = _profile(field_lift=0.0)
    closed = lb.bulb_sequence(frames, RATE, flat)
    report = lb.separation_report(frames, closed, flat)
    assert report["base_drawn"] is True
    assert report["clears_floor"] is False


def test_the_field_lift_is_what_lifts_the_base_off_the_field():
    """The lift, and only the lift, is what moves that number.

    :data:`~library.tools.logo_bulb.SEPARATION_FLOOR` is 8.5 read off
    the real lockup, and these bars are not that lockup - the number
    they reach is their own. What is pinned here is that the declared
    lift more than doubles it and that it rises with the lift, because
    those are the properties the real asset's clearance rests on.
    """
    frames = _sequence(size=192)
    report = lb.separation_report(
        frames, lb.bulb_sequence(frames, RATE, _profile()), _profile())
    flat = _profile(field_lift=0.0)
    without = lb.separation_report(
        frames, lb.bulb_sequence(frames, RATE, flat), flat)
    assert report["worst_separation"] > 2.0 * without["worst_separation"]
    assert report["base_first_drawn_frame"] == without[
        "base_first_drawn_frame"]

    more = _profile(field_lift=2.0 * lb.FIELD_LIFT)
    assert lb.separation_report(
        frames, lb.bulb_sequence(frames, RATE, more), more
    )["worst_separation"] > report["worst_separation"]


def test_the_report_says_yes_as_well_as_no():
    """A gate that can only say no reads as coverage without being it."""
    frames = _sequence(size=192)
    enough = _profile(field_lift=2.0 * lb.FIELD_LIFT)
    report = lb.separation_report(
        frames, lb.bulb_sequence(frames, RATE, enough), enough)
    assert report["worst_separation"] >= lb.SEPARATION_FLOOR
    assert report["clears_floor"] is True


def test_the_lift_moves_the_ground_s_value_and_not_its_hue():
    """A lift is not a different blue. The declared ground multiplies,
    so the channel ratios - hue and saturation - come through exactly."""
    profile = _profile()
    mark = _mark(96, 1.0, 1.0)
    _, ink = lb.separate_ink(mark, profile.light)
    spread, normaliser = lb.field_geometry([mark], profile)
    pool = lb.field_pool(ink, spread, normaliser)
    lifted = lb.bulb_frame(mark, 0.0, 1.0, profile, 1.0, pool)

    behind = (ink <= 0.0) & (pool >= 0.9 * pool.max())
    assert behind.any()
    painted = lifted[..., :3][behind]
    declared = np.asarray(LUCIE_GROUND)
    assert (painted >= declared - 1e-9).all()
    ratio = painted / declared
    assert np.allclose(ratio, ratio[:, :1], atol=1e-9)


def test_a_black_ground_is_not_lifted_at_all():
    """Nothing times anything is nothing - and that is the right answer,
    because a navy base already reads on black."""
    frames = _sequence(size=96)
    black = ClosingProfile()
    flat = ClosingProfile(field_lift=0.0)
    for lifted, plain in zip(lb.bulb_sequence(frames, RATE, black),
                             lb.bulb_sequence(frames, RATE, flat)):
        assert np.array_equal(lifted, plain)


def test_the_declared_ground_is_what_the_frame_reads_away_from_the_mark():
    """The pool is BEHIND the lockup, and it dies. The field itself is
    #253746 - on the real 1080x1920 asset every corner is more than
    four spreads from the mark, which is what this canvas reproduces."""
    profile = _profile()
    pad = 300
    mark = np.zeros((192 + 2 * pad, 192 + 2 * pad, 4), dtype=np.float64)
    mark[pad:pad + 192, pad:pad + 192] = _mark(192, 1.0, 1.0)
    _, ink = lb.separate_ink(mark, profile.light)
    spread, normaliser = lb.field_geometry([mark], profile)
    pool = lb.field_pool(ink, spread, normaliser)
    out = lb.bulb_frame(mark, 0.0, 1.0, profile, 1.0, pool)
    assert np.allclose(out[0, 0, :3], np.asarray(LUCIE_GROUND), atol=1e-4)
    assert pool.max() == pytest.approx(1.0, abs=1e-9)


def test_the_pool_arrives_with_the_mark_and_leaves_with_it():
    profile = _profile()
    whole = _mark(96, 1.0, 1.0)
    spread, normaliser = lb.field_geometry([whole], profile)
    _, ink = lb.separate_ink(whole, profile.light)
    assert lb.field_pool(ink, spread, normaliser).max() == pytest.approx(
        1.0, abs=1e-9)
    assert lb.field_pool(np.zeros_like(ink), spread, normaliser).max() == 0.0
    part = lb.field_pool(ink * 0.25, spread, normaliser).max()
    assert 0.0 < part < 1.0


def test_a_mark_that_covers_no_pixel_has_no_field_to_lift():
    with pytest.raises(SourceNotClosed):
        lb.field_geometry([np.zeros((16, 16, 4), dtype=np.float64)],
                          _profile())


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
    assert profile.field_spread == lb.FIELD_SPREAD
    assert profile.field_lift == lb.FIELD_LIFT
    assert profile.separation_floor == lb.SEPARATION_FLOOR
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


# ── The two-line variant ─────────────────────────────────────────────

LUCIE_LINES = ("See your brand the way AI does", "luciecontent.com")
NEAR_WHITE = (0xF5 / 255, 0xF5 / 255, 0xF5 / 255)


def _text(lines=LUCIE_LINES, color=NEAR_WHITE) -> lb.ClosingText:
    return lb.ClosingText(lines=tuple(lines), color=color)


def _lockup_template(tmp_path, **keys):
    content = dict(keys.pop("content", {}))
    template = tmp_path / "brand.yaml"
    import yaml
    template.write_text(
        yaml.safe_dump({"content": content, **keys}), encoding="utf-8")
    return str(template)


def test_no_lines_is_todays_animation_pixel_identical():
    """The "including none" half, proved rather than asserted: an empty
    ClosingText takes the exact code path the logo-only render always
    took, so every frame must be bit-identical, not just close."""
    frames = _sequence(size=96)
    profile = _profile()
    plain = lb.bulb_sequence(frames, RATE, profile)
    with_text = lb.bulb_sequence(frames, RATE, profile, lb.ClosingText())
    assert len(with_text) == len(plain)
    for closed, opened in zip(with_text, plain):
        assert np.array_equal(closed, opened)


def test_lines_from_brand_template_reads_the_declared_pair(tmp_path):
    path = _lockup_template(tmp_path, content={
        "closing_lockup": {
            "lines": list(LUCIE_LINES), "color": "#F5F5F5"}})
    assert lb.lines_from_brand_template(path) == _text()


def test_absent_closing_lockup_is_no_text(tmp_path):
    """A template declaring no closing_lockup gets today's animation -
    which is every template but one."""
    path = _lockup_template(tmp_path, content={})
    assert lb.lines_from_brand_template(path) == lb.ClosingText()
    assert lb.lines_from_brand_template(path).lines == ()


def test_end_card_props_are_not_the_lockup(tmp_path):
    """The stale headline/tagline belong to the LucieEndCard
    composition. A template carrying them and no closing_lockup sets
    no type - reading them here would couple two surfaces to one
    edit."""
    path = _lockup_template(tmp_path, content={
        "bookends": {"end_card": {"props": {
            "headline": "We power what AI knows about you.",
            "tagline": "Strategic storytelling built for human trust "
                       "and AI visibility.",
            "websiteUrl": "luciecontent.com",
            "bgColor": "#253746"}}}})
    assert lb.lines_from_brand_template(path).lines == ()


@pytest.mark.parametrize("lockup,match", [
    ({"lines": [], "color": "#F5F5F5"}, "no lines"),
    ({"lines": ["one", "two", "three"], "color": "#F5F5F5"}, "layout"),
    ({"lines": ["fine", 7], "color": "#F5F5F5"}, "sets nothing"),
    ({"lines": list(LUCIE_LINES)}, "no color"),
    ({"lines": list(LUCIE_LINES), "color": "rebeccapurple"},
     "ground is #rrggbb"),
    ({"color": "#F5F5F5"}, "no lines"),
    ("just a string", "not a mapping"),
])
def test_a_half_declared_lockup_is_refused(tmp_path, lockup, match):
    path = _lockup_template(tmp_path, content={"closing_lockup": lockup})
    with pytest.raises(SourceNotClosed, match=match):
        lb.lines_from_brand_template(path)


def test_the_layer_sets_two_lines_clear_of_mark_and_margins():
    """The full-canvas geometry, without the glow path: PIL only, so
    this runs in milliseconds. The completed mark's ink ends at y
    1172 on the real source; both lines must sit well below it, well
    inside the frame, and read as ONE lockup - closer to each other
    than to anything else."""
    layer = lb.text_layer(_text(), 1080, 1920, _profile())
    assert layer is not None
    assert layer.shape == (1920, 1080, 4)
    assert np.allclose(layer[..., :3], np.asarray(NEAR_WHITE))
    rows = np.nonzero(layer[..., 3] > 0.01)[0]
    columns = np.nonzero(layer[..., 3] > 0.01)[1]
    assert rows.min() >= 1400, "the type must clear the mark by daylight"
    assert rows.max() <= 1660, "the type must clear the frame by daylight"
    assert columns.min() >= 85 and columns.max() <= 995
    empty = [y for y in range(rows.min(), rows.max() + 1)
             if not (layer[y, :, 3] > 0.01).any()]
    assert empty, "two lines must read as two lines, with navy between"


def test_a_line_too_wide_for_the_frame_is_refused():
    """Not shrunk: a shrunken line is a layout authored on the
    declaration's behalf, and a clipped one is worse."""
    with pytest.raises(SourceNotClosed, match="allows"):
        lb.text_layer(_text(), 200, 400, _profile())


def test_lines_with_no_color_are_refused_where_the_layer_is_built():
    with pytest.raises(SourceNotClosed, match="no color"):
        lb.text_layer(lb.ClosingText(lines=LUCIE_LINES, color=None),
                      1080, 1920, _profile())


def _fake_layer(size=32, color=NEAR_WHITE):
    layer = np.zeros((size, size, 4), dtype=np.float64)
    layer[12:20, 8:24, :3] = np.asarray(color)
    layer[12:20, 8:24, 3] = 1.0
    return layer


def test_text_arrives_with_the_cut_and_leaves_on_the_fade():
    """Up on frame 0 - the type is part of the ground, so it arrives
    with the cut and needs no timing of its own - and gone exactly
    when beat 5 has nothing left: text_present 0.0 must equal no text
    at all, and halfway must be halfway."""
    profile = _profile()
    empty = np.zeros((32, 32, 4), dtype=np.float64)
    fake = _fake_layer()
    full = lb.bulb_frame(empty, profile.base_light, 1.0, profile,
                         text=fake, text_present=1.0)
    assert np.allclose(full[12:20, 8:24, :3], np.asarray(NEAR_WHITE))
    gone = lb.bulb_frame(empty, profile.base_light, 0.0, profile,
                         text=fake, text_present=0.0)
    assert np.array_equal(
        gone, lb.bulb_frame(empty, profile.base_light, 0.0, profile))
    half = lb.bulb_frame(empty, profile.base_light, 1.0, profile,
                         text=fake, text_present=0.5)
    ground = np.asarray(LUCIE_GROUND)
    assert np.allclose(half[12:20, 8:24, :3],
                       0.5 * np.asarray(NEAR_WHITE) + 0.5 * ground)


def test_text_catches_no_flash_light():
    """Set ink, not the mark: the type's own pixels are its declared
    colour at the base level AND at the flash peak, because the light
    is never composited onto them."""
    profile = _profile()
    empty = np.zeros((32, 32, 4), dtype=np.float64)
    fake = _fake_layer()
    dim = lb.bulb_frame(empty, profile.base_light, 1.0, profile,
                        text=fake, text_present=1.0)
    peak = lb.bulb_frame(empty, profile.flash_light, 1.0, profile,
                         text=fake, text_present=1.0)
    assert np.allclose(dim[12:20, 8:24, :3], np.asarray(NEAR_WHITE))
    assert np.array_equal(dim[12:20, 8:24, :3], peak[12:20, 8:24, :3])


def test_text_contrast_report_says_yes_as_well_as_no():
    """A gate that can only say yes reads as coverage without being
    it: near-white on navy clears, navy on navy does not, and no
    layer is not a measurement."""
    profile = _profile()
    fake = _fake_layer()
    ground = np.full((32, 32, 4), (*LUCIE_GROUND, 1.0))
    drawn = ground.copy()
    drawn[12:20, 8:24, :3] = np.asarray(NEAR_WHITE)
    present = standing = [1.0]
    yes = lb.text_contrast_report(fake, [drawn], present, standing,
                                  profile)
    assert yes["type_set"] is True
    assert yes["clears_floor"] is True
    assert yes["worst_contrast"] >= lb.TEXT_CONTRAST_FLOOR
    navy_layer = _fake_layer(color=LUCIE_GROUND)
    no = lb.text_contrast_report(navy_layer, [ground], present, standing,
                                 profile)
    assert no["type_set"] is True
    assert no["clears_floor"] is False
    assert no["worst_contrast"] < lb.TEXT_CONTRAST_FLOOR
    assert lb.text_contrast_report(
        None, [ground], present, standing, profile)["type_set"] is False


def test_the_receipt_says_how_long_the_type_stands():
    """The number the captain judges the 72 frames with: full-type
    frames, full-lockup frames, and whether the stand clears the
    read-time floor. On the short synthetic sequence the stand is
    32 frames - 1.3 seconds - which must NOT clear a 2.0 floor, or
    the floor is decoration."""
    report = lb.describe(_sequence(), RATE, _profile(), _text())
    assert report["lines"] == list(LUCIE_LINES)
    assert report["type_full_frames"] == DRAW + HOLD
    assert report["type_full_seconds"] == pytest.approx(
        (DRAW + HOLD) / RATE, abs=0.01)
    assert report["type_clears_read_time"] is False
    assert report["lockup_full_frames"] == DRAW + HOLD - COMPLETION
    plain = lb.describe(_sequence(), RATE, _profile())
    assert "lines" not in plain
    assert "type_full_frames" not in plain


def test_every_text_value_is_on_the_profile():
    """A project that wants a different lockup passes a profile rather
    than editing a constant, so every constant has to be reachable -
    the same rule the closing's own values keep."""
    profile = ClosingProfile()
    assert profile.line1_px == lb.LINE1_PX
    assert profile.line2_px == lb.LINE2_PX
    assert profile.line1_center_y == lb.LINE1_CENTER_Y
    assert profile.line2_center_y == lb.LINE2_CENTER_Y
    assert profile.text_safe_margin_px == lb.TEXT_SAFE_MARGIN_PX
    assert profile.text_contrast_floor == lb.TEXT_CONTRAST_FLOOR
    assert profile.read_time_floor_seconds == lb.READ_TIME_FLOOR_SECONDS


def test_brand_template_from_dict_reads_closing_lockup():
    """The schema half of the declaration: from_dict must not choke
    on the new key, and absence must stay absence."""
    from library.schemas.brand_template import BrandTemplate
    tmpl = BrandTemplate.from_dict({
        "series_id": "s",
        "content": {"closing_lockup": {
            "lines": list(LUCIE_LINES), "color": "#F5F5F5"}}})
    assert tmpl.content.closing_lockup == {
        "lines": list(LUCIE_LINES), "color": "#F5F5F5"}
    bare = BrandTemplate.from_dict({"series_id": "s"})
    assert bare.content.closing_lockup is None
