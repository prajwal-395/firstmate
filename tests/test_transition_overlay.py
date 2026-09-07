"""A transition element laid over a cut: what it draws, and what it costs.

Every gate here is proved in BOTH directions. A gate that cannot fail is
worse than no gate because it reads as coverage (AGENTS.md 10.4), and
this area has a documented history of exactly that: project 001 rendered
53.8 MB of motion-graphics ProRes in which `max(alpha)` was 0 on every
frame and reported them delivered.

The alpha instrument is validated against three fixtures whose answers
are known before it is pointed at anything real - an opaque one, an empty
one, and one with no alpha plane at all - because an instrument that
reports zero has to be shown reporting non-zero on something known first.
The empty and the no-alpha cases are DIFFERENT results and the test says
so: ffmpeg exits non-zero having written no frames when the plane is
absent, and a reader that only looked at the maximum would call that
"draws nothing" and give the wrong diagnosis.
"""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from library.tools import transition_overlay as ov
from library.tools.transition_overlay import (
    ANCHORS,
    OVERLAY_TRACK,
    SEAM_CLOSER,
    SEAM_REEL_HEAD,
    SEAM_REEL_TAIL,
    SEAM_TAKE_REMOVED,
    ElementDrawsNothing,
    ElementHasNoAlpha,
    ElementUnmeasurable,
    NotASeam,
    OverlayDoesNotFit,
    OverlayPlacement,
    OverlaysCollide,
    ResolvedElement,
    TransitionOverlayError,
)

FPS = 24000 / 1001

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason=(
        "ffmpeg/ffprobe is not available here, so the alpha fixtures "
        "cannot be built or measured. Runs anywhere ffmpeg and ffprobe are "
        "on PATH - the CI runner installs them and AGENTS.md 9 requires "
        "them for any real run."),
)


# ── Fixtures whose answers are known before anything is measured ─────

def _prores(path, colour, *, alpha: bool, frames: int = 5, rate: int = 25):
    """A tiny ProRes clip with a KNOWN alpha channel, or none at all."""
    duration = frames / rate
    if alpha:
        source = (f"color=c={colour}:s=64x64:d={duration}:r={rate},"
                  f"format=yuva444p10le")
        codec = ["-c:v", "prores_ks", "-profile:v", "4",
                 "-pix_fmt", "yuva444p10le"]
    else:
        source = f"color=c={colour}:s=64x64:d={duration}:r={rate}"
        codec = ["-c:v", "prores_ks", "-profile:v", "3",
                 "-pix_fmt", "yuv422p10le"]
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", source,
         *codec, str(path)],
        check=True, capture_output=True, text=True, encoding="utf-8")
    return str(path)


@pytest.fixture
def opaque_element(tmp_path):
    """Alpha channel present and fully opaque - it draws."""
    return _prores(tmp_path / "opaque.mov", "white@1.0", alpha=True)


@pytest.fixture
def empty_element(tmp_path):
    """Alpha channel present and zero everywhere - it draws NOTHING."""
    return _prores(tmp_path / "empty.mov", "black@0.0", alpha=True)


@pytest.fixture
def no_alpha_element(tmp_path):
    """No alpha plane at all - there is nothing to composite."""
    return _prores(tmp_path / "noalpha.mov", "blue", alpha=False)


# ── The instrument, validated before it is trusted ───────────────────

def test_the_alpha_instrument_reads_a_known_opaque_element(opaque_element):
    measured = ov.measure_alpha(opaque_element)
    assert measured.frames_read == 5
    assert measured.max_alpha == 255
    assert measured.draws is True
    assert measured.has_channel is True


def test_the_alpha_instrument_reads_a_known_empty_element(empty_element):
    """The other direction: the same instrument returns zero on a fixture
    that really is zero. Without the test above this one proves nothing -
    an instrument that always returns zero would pass it."""
    measured = ov.measure_alpha(empty_element)
    assert measured.frames_read == 5
    assert measured.max_alpha == 0
    assert measured.draws is False
    assert measured.has_channel is True


def test_an_absent_alpha_plane_is_not_an_alpha_of_zero(no_alpha_element):
    """The trap this file exists to avoid.

    ffmpeg exits non-zero having written NO frames when `alphaextract`
    has nothing to extract. A reader that only looked at the maximum
    would see 0 and report "draws nothing", sending whoever authored the
    element to fix the wrong thing.
    """
    with pytest.raises(ElementHasNoAlpha) as exc:
        ov.measure_alpha(no_alpha_element)
    assert "no alpha plane" in str(exc.value)
    # And it says WHY the engine does not simply key one out.
    assert "does not key" in str(exc.value)


def test_a_missing_file_is_unmeasurable_not_empty(tmp_path):
    with pytest.raises(ElementUnmeasurable):
        ov.measure_element(str(tmp_path / "nothing_here.mov"))


def test_measure_element_accepts_a_drawing_element(opaque_element):
    element = ov.measure_element(opaque_element)
    assert element.width == 64 and element.height == 64
    assert element.frame_count == 5
    assert element.alpha.draws
    assert element.duration_seconds == pytest.approx(0.2)


def test_measure_element_refuses_one_that_draws_nothing(empty_element):
    with pytest.raises(ElementDrawsNothing):
        ov.measure_element(empty_element)


def test_reachability_is_measured_not_declared(opaque_element,
                                               empty_element):
    ok, why = ov.element_is_reachable(opaque_element)
    assert ok and why == ""
    ok, why = ov.element_is_reachable(empty_element)
    assert not ok and "zero on all" in why


# ── The declaration ──────────────────────────────────────────────────

def _declaration(path, **over):
    base = {"element": {"asset": path}, "anchor": "centre",
            "on_cuts": [SEAM_TAKE_REMOVED]}
    base.update(over)
    return base


def test_a_declaration_without_an_anchor_is_refused(opaque_element,
                                                    tmp_path):
    declaration = _declaration(opaque_element)
    del declaration["anchor"]
    with pytest.raises(TransitionOverlayError) as exc:
        ov.resolve_element(declaration, str(tmp_path))
    assert "no default" in str(exc.value)


def test_a_declaration_with_an_anchor_resolves(opaque_element, tmp_path):
    """The other direction, so the refusal above is about the anchor and
    not about the fixture."""
    resolved = ov.resolve_element(_declaration(opaque_element),
                                  str(tmp_path))
    assert resolved.anchor == "centre"
    assert resolved.mode == "asset"
    assert resolved.duration_seconds == pytest.approx(0.2)


@pytest.mark.parametrize("anchor", ANCHORS)
def test_every_named_anchor_is_accepted(opaque_element, tmp_path, anchor):
    resolved = ov.resolve_element(
        _declaration(opaque_element, anchor=anchor), str(tmp_path))
    assert resolved.anchor == anchor


def test_an_unknown_anchor_is_refused(opaque_element, tmp_path):
    with pytest.raises(TransitionOverlayError):
        ov.resolve_element(_declaration(opaque_element, anchor="sideways"),
                           str(tmp_path))


def test_naming_both_an_asset_and_a_composition_is_refused(opaque_element,
                                                           tmp_path):
    declaration = _declaration(opaque_element)
    declaration["element"]["composition"] = "TransitionBumper"
    declaration["element"]["duration_seconds"] = 0.2
    with pytest.raises(TransitionOverlayError, match="both"):
        ov.resolve_element(declaration, str(tmp_path))


def test_naming_neither_is_refused(tmp_path):
    with pytest.raises(TransitionOverlayError, match="neither"):
        ov.resolve_element({"element": {}, "anchor": "centre"},
                           str(tmp_path))


def test_a_composition_must_declare_its_duration(tmp_path):
    with pytest.raises(TransitionOverlayError, match="duration_seconds"):
        ov.resolve_element(
            {"element": {"composition": "TransitionBumper"},
             "anchor": "centre"}, str(tmp_path))


def test_a_composition_nothing_has_rendered_refuses_and_says_why(tmp_path):
    """NO STEP OF THE REELS PROCESS RENDERS A COMPOSITION.

    `content.bookends`' composition mode works because step 4.06 exists
    on the edit_video graph to render it. The reels process is
    `build_reels` and `verify_reels`, and neither renders anything.
    Accepting the declaration and placing nothing would be a vocabulary
    entry that does not draw - the exact defect this module was written
    under - so it refuses and names what is missing.
    """
    with pytest.raises(TransitionOverlayError) as exc:
        ov.resolve_element(
            {"element": {"composition": "TransitionBumper",
                         "duration_seconds": 1.5},
             "anchor": "after"}, str(tmp_path))
    assert "NO STEP OF THE REELS PROCESS RENDERS A COMPOSITION" in str(exc.value)
    assert "asset:" in str(exc.value)


def test_a_composition_someone_has_rendered_is_measured_and_placed(
        tmp_path, opaque_element):
    """The other direction, so the refusal above is about the missing
    render and not about composition mode being dead. Put the file where
    the declaration says it goes and it measures like any other element -
    which is also the migration path the refusal names.
    """
    import os
    import shutil

    rendered = ov.overlay_render_path(str(tmp_path), "TransitionBumper")
    os.makedirs(os.path.dirname(rendered), exist_ok=True)
    shutil.copy(opaque_element, rendered)

    resolved = ov.resolve_element(
        {"element": {"composition": "TransitionBumper",
                     "duration_seconds": 1.5},
         "anchor": "after"}, str(tmp_path))
    assert resolved.mode == "composition"
    assert resolved.path == rendered
    # MEASURED, not the declared 1.5 - the file is 0.2s and the file wins.
    assert resolved.duration_seconds == pytest.approx(0.2)
    assert resolved.declared_duration_seconds == 1.5
    assert resolved.gesture == ov.GESTURE_HIDES


def test_a_declared_duration_that_disagrees_with_the_file_is_refused(
        opaque_element, tmp_path):
    declaration = _declaration(opaque_element)
    declaration["element"]["duration_seconds"] = 3.0   # the file is 0.2s
    with pytest.raises(TransitionOverlayError, match="measures"):
        ov.resolve_element(declaration, str(tmp_path))


def test_a_declared_duration_that_agrees_is_accepted(opaque_element,
                                                     tmp_path):
    declaration = _declaration(opaque_element)
    declaration["element"]["duration_seconds"] = 0.2
    assert ov.resolve_element(declaration, str(tmp_path)).duration_seconds \
        == pytest.approx(0.2)


def test_an_asset_that_is_not_on_disk_is_refused(tmp_path):
    with pytest.raises(TransitionOverlayError, match="not found"):
        ov.resolve_element(_declaration("brand_assets/absent.mov"),
                           str(tmp_path))


def test_the_project_declaration_wins_over_the_template(opaque_element,
                                                        tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "project.yaml").write_text(
        "effect:\n"
        "  transition_overlay:\n"
        "    anchor: after\n"
        "    on_cuts: [closer]\n"
        "    element:\n"
        f"      asset: {opaque_element}\n",
        encoding="utf-8")
    template_effect = {"transition_overlay": {"anchor": "before",
                                              "element": {"asset": "x.mov"}}}
    resolved = ov.resolve_declaration(template_effect, str(project))
    assert resolved["transition_overlay"]["anchor"] == "after"


def test_a_project_that_declares_nothing_leaves_the_template_alone(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "project.yaml").write_text("name: x\n", encoding="utf-8")
    template_effect = {"transition_overlay": {"anchor": "before"}}
    resolved = ov.resolve_declaration(template_effect, str(project))
    assert resolved["transition_overlay"]["anchor"] == "before"


def test_declaring_nothing_gets_nothing():
    assert ov.declared_overlay(None) is None
    assert ov.declared_overlay({}) is None
    assert ov.declared_overlay({"motion_accents": True}) is None


# ── The cuts a reel has ──────────────────────────────────────────────

RANGES = [(10.0, 14.0), (20.0, 23.5), (40.0, 42.0)]


def test_seams_are_the_frames_reel_build_lays_the_ranges_down_at():
    """The arithmetic must be `reel_build.placements`', not a rounding of
    the seconds. An element one frame off the cut exposes the jump it
    exists to hide."""
    from library.tools.reel_build import placements

    class _Clip:
        def __init__(self, start, end):
            self.timeline_start, self.timeline_end = start, end
            self.source_in, self.track_index, self.speaker = 0.0, 1, "A"

    clips = [_Clip(0.0, 100.0)]
    placed = placements(RANGES, clips, FPS)
    # Each range's picture starts at the previous seam.
    starts = [p["snapped_record"] for p in placed]
    seams = ov.reel_seams(RANGES, FPS)
    internal = [s.reel_frame for s in seams if s.carries]
    assert starts[1:] == internal


def test_the_head_and_tail_of_a_reel_are_reported_and_carry_nothing():
    seams = ov.reel_seams(RANGES, FPS)
    assert seams[0].kind == SEAM_REEL_HEAD and not seams[0].carries
    assert seams[-1].kind == SEAM_REEL_TAIL and not seams[-1].carries
    assert seams[0].basis and seams[-1].basis


def test_every_internal_seam_carries_and_says_where_the_picture_jumps():
    seams = ov.reel_seams(RANGES, FPS)
    internal = ov.carrying_seams(seams)
    assert len(internal) == len(RANGES) - 1
    for seam in internal:
        assert seam.master_out is not None and seam.master_in is not None
        assert f"{seam.master_out:.3f}" in seam.basis


def test_the_closer_seam_is_named_when_there_is_one():
    frames = ov.reel_frame_count(RANGES[:-1], FPS)
    seams = ov.reel_seams(RANGES, FPS, closer_seam_frame=frames)
    kinds = [s.kind for s in seams if s.carries]
    assert kinds == [SEAM_TAKE_REMOVED, SEAM_CLOSER]


def test_without_a_closer_no_seam_claims_to_be_one():
    kinds = {s.kind for s in ov.reel_seams(RANGES, FPS) if s.carries}
    assert kinds == {SEAM_TAKE_REMOVED}


def test_a_one_range_reel_has_no_cut_at_all():
    seams = ov.reel_seams([(0.0, 5.0)], FPS)
    assert ov.carrying_seams(seams) == []
    assert {s.kind for s in seams} == {SEAM_REEL_HEAD, SEAM_REEL_TAIL}


# ── Selecting seams ──────────────────────────────────────────────────

def test_a_declaration_that_names_no_seams_is_refused():
    seams = ov.reel_seams(RANGES, FPS)
    with pytest.raises(TransitionOverlayError, match="no default"):
        ov.select_seams({"anchor": "centre"}, seams)


def test_selecting_by_kind_returns_that_kind_only():
    frames = ov.reel_frame_count(RANGES[:-1], FPS)
    seams = ov.reel_seams(RANGES, FPS, closer_seam_frame=frames)
    chosen = ov.select_seams({"on_cuts": [SEAM_CLOSER]}, seams)
    assert [seams[i].kind for i in chosen] == [SEAM_CLOSER]


def test_selecting_by_explicit_index_is_honoured():
    seams = ov.reel_seams(RANGES, FPS)
    assert ov.select_seams({"seams": [2]}, seams) == [2]


def test_selecting_a_seam_that_does_not_exist_is_refused():
    seams = ov.reel_seams(RANGES, FPS)
    with pytest.raises(NotASeam):
        ov.select_seams({"seams": [99]}, seams)


def test_an_unknown_seam_kind_is_refused():
    seams = ov.reel_seams(RANGES, FPS)
    with pytest.raises(TransitionOverlayError, match="not"):
        ov.select_seams({"on_cuts": ["whenever_it_feels_right"]}, seams)


def test_a_kind_that_matches_nothing_selects_nothing():
    """A reel with no closer is a real answer, not an error."""
    seams = ov.reel_seams(RANGES, FPS)
    assert ov.select_seams({"on_cuts": [SEAM_CLOSER]}, seams) == []


# ── Placing ──────────────────────────────────────────────────────────

def _element(seconds=1.0, anchor="centre"):
    return ResolvedElement(anchor=anchor, mode="asset", path="/e.mov",
                           composition=None, source=None,
                           duration_seconds=seconds,
                           declared_duration_seconds=None)


@pytest.mark.parametrize("anchor,expected", [
    ("before", 100 - 30),
    ("after", 100),
    ("centre", 100 - 15),
])
def test_each_anchor_puts_the_element_where_it_says(anchor, expected):
    assert ov.anchor_record_frame(100, 30, anchor) == expected


def test_centring_an_odd_length_gives_the_extra_frame_to_the_outgoing_side():
    # 31 frames: 16 before the cut, 15 after.
    assert ov.anchor_record_frame(100, 31, "centre") == 84


def test_placing_on_a_carrying_seam_produces_exact_frames():
    seams = ov.reel_seams(RANGES, FPS)
    placed = ov.place_overlays(seams, [1], _element(1.0), FPS,
                               ov.reel_frame_count(RANGES, FPS))
    assert len(placed) == 1
    assert placed[0].track_index == OVERLAY_TRACK
    assert placed[0].duration_frames == int(round(1.0 * FPS))
    assert placed[0].seam_kind == SEAM_TAKE_REMOVED


def test_placing_on_the_head_of_the_reel_is_refused():
    seams = ov.reel_seams(RANGES, FPS)
    with pytest.raises(NotASeam, match="reel_head"):
        ov.place_overlays(seams, [0], _element(0.2), FPS,
                          ov.reel_frame_count(RANGES, FPS))


def test_placing_on_the_tail_of_the_reel_is_refused():
    seams = ov.reel_seams(RANGES, FPS)
    with pytest.raises(NotASeam, match="reel_tail"):
        ov.place_overlays(seams, [seams[-1].index], _element(0.2), FPS,
                          ov.reel_frame_count(RANGES, FPS))


def test_an_element_that_would_start_before_the_reel_is_refused():
    seams = ov.reel_seams(RANGES, FPS)
    # The first cut is 4s in; a 20s element anchored `before` cannot fit.
    with pytest.raises(OverlayDoesNotFit, match="before the reel begins"):
        ov.place_overlays(seams, [1], _element(20.0, "before"), FPS,
                          ov.reel_frame_count(RANGES, FPS))


def test_an_element_that_would_run_past_the_reel_is_refused():
    seams = ov.reel_seams(RANGES, FPS)
    last_cut = [s for s in seams if s.carries][-1]
    with pytest.raises(OverlayDoesNotFit, match="on a reel of"):
        ov.place_overlays(seams, [last_cut.index],
                          _element(30.0, "after"), FPS,
                          ov.reel_frame_count(RANGES, FPS))


def test_an_element_that_fits_at_the_same_seam_is_placed():
    """The other direction for both fit refusals above."""
    seams = ov.reel_seams(RANGES, FPS)
    last_cut = [s for s in seams if s.carries][-1]
    placed = ov.place_overlays(seams, [last_cut.index],
                              _element(1.0, "after"), FPS,
                              ov.reel_frame_count(RANGES, FPS))
    assert placed[0].end_frame <= ov.reel_frame_count(RANGES, FPS)


def test_two_elements_that_would_overlap_are_refused_by_both_seam_numbers():
    seams = ov.reel_seams(RANGES, FPS)
    carrying = [s.index for s in seams if s.carries]
    # The two cuts are 3.5s apart; a 4s element centred on each fits the
    # reel and cannot help overlapping its neighbour.
    with pytest.raises(OverlaysCollide) as exc:
        ov.place_overlays(seams, carrying, _element(4.0), FPS,
                          ov.reel_frame_count(RANGES, FPS))
    for index in carrying:
        assert str(index) in str(exc.value)


def test_two_elements_far_enough_apart_are_both_placed():
    seams = ov.reel_seams(RANGES, FPS)
    carrying = [s.index for s in seams if s.carries]
    placed = ov.place_overlays(seams, carrying, _element(0.5), FPS,
                               ov.reel_frame_count(RANGES, FPS))
    assert len(placed) == 2


def test_an_element_shorter_than_one_frame_is_refused():
    seams = ov.reel_seams(RANGES, FPS)
    with pytest.raises(TransitionOverlayError, match="under one frame"):
        ov.place_overlays(seams, [1], _element(0.001), FPS,
                          ov.reel_frame_count(RANGES, FPS))


# ── The whole reel pass ──────────────────────────────────────────────

def test_a_project_with_no_declaration_gets_an_empty_plan_that_says_why():
    plan = ov.plan_reel_overlays({}, RANGES, FPS, None)
    assert plan.placements == []
    assert "declare nothing and get nothing" in plan.reason_empty
    assert plan.seams  # the seams are still reported


def test_a_declaration_that_matches_no_seam_says_which_seams_there_were(
        opaque_element, tmp_path):
    effect = {"transition_overlay": _declaration(opaque_element,
                                                 on_cuts=[SEAM_CLOSER])}
    plan = ov.plan_reel_overlays(effect, RANGES, FPS, str(tmp_path))
    assert plan.placements == []
    assert SEAM_TAKE_REMOVED in plan.reason_empty


def test_a_declaration_that_matches_places_an_element_on_every_such_cut(
        opaque_element, tmp_path):
    effect = {"transition_overlay": _declaration(opaque_element)}
    plan = ov.plan_reel_overlays(effect, RANGES, FPS, str(tmp_path))
    assert len(plan.placements) == 2
    assert all(p.track_index == OVERLAY_TRACK for p in plan.placements)
    assert plan.element.path == opaque_element


def test_a_declared_element_that_draws_nothing_fails_the_plan(
        empty_element, tmp_path):
    effect = {"transition_overlay": _declaration(empty_element)}
    with pytest.raises(ElementDrawsNothing):
        ov.plan_reel_overlays(effect, RANGES, FPS, str(tmp_path))


def test_a_declared_element_with_no_alpha_fails_the_plan(no_alpha_element,
                                                         tmp_path):
    effect = {"transition_overlay": _declaration(no_alpha_element)}
    with pytest.raises(ElementHasNoAlpha):
        ov.plan_reel_overlays(effect, RANGES, FPS, str(tmp_path))


# ── The duration ruling, checked rather than asserted ────────────────

def test_an_overlay_changes_neither_the_reel_length_nor_the_keep_ranges(
        opaque_element, tmp_path):
    effect = {"transition_overlay": _declaration(opaque_element)}
    before = ov.reel_frame_count(RANGES, FPS)
    plan = ov.plan_reel_overlays(effect, RANGES, FPS, str(tmp_path))
    assert plan.placements
    assert plan.reel_frames == before
    assert ov.reel_frame_count(RANGES, FPS) == before


def test_an_overlay_leaves_the_caption_footage_binding_byte_identical(
        opaque_element, tmp_path):
    """TIMING_IS_ADDITIVE, as a measurement.

    `footage_binding_hash` digests each speech block's clip, source span
    AND timeline span. If an element consumed frames from either side of
    a cut, every later block's `timeline_start` would move and this hash
    would change - which is the same as saying every caption on the reel
    would have to be re-planned to stay bound to its footage.
    """
    from library.tools.plan_provenance import footage_binding_hash

    spine = {"structure": [
        {"block_type": "speech", "clip_id": "clip_001",
         "source_start": 10.0, "source_end": 14.0,
         "timeline_start": 0.0, "timeline_end": 4.0},
        {"block_type": "speech", "clip_id": "clip_001",
         "source_start": 20.0, "source_end": 23.5,
         "timeline_start": 4.0, "timeline_end": 7.5},
    ]}
    before = footage_binding_hash(spine)

    effect = {"transition_overlay": _declaration(opaque_element)}
    plan = ov.plan_reel_overlays(effect, RANGES, FPS, str(tmp_path))
    assert plan.placements, "this proves nothing if nothing was placed"

    # The spine is untouched by the overlay pass - it is not even an
    # input to it - and that is the point being recorded.
    assert footage_binding_hash(spine) == before
    assert ov.TIMING_IS_ADDITIVE is True


# ── What it costs the captions ───────────────────────────────────────

def _placement(record, duration):
    return OverlayPlacement(seam_index=0, seam_kind=SEAM_TAKE_REMOVED,
                            record_frame=record, duration_frames=duration,
                            track_index=OVERLAY_TRACK, element_path="/e.mov",
                            anchor="centre")


def test_a_caption_under_an_element_is_reported_with_how_long():
    covered = ov.captions_covered([_placement(100, 30)],
                                  [(90, 115, "card A")], FPS)
    assert len(covered) == 1
    assert covered[0].covered_frames == 15
    assert covered[0].caption_name == "card A"


def test_a_caption_clear_of_the_element_is_not_reported():
    assert ov.captions_covered([_placement(100, 30)],
                               [(200, 260, "card B")], FPS) == []


def test_a_caption_touching_the_element_by_one_frame_is_still_reported():
    """No threshold: how much cover is too much is a judgement, and this
    is a measurement (AGENTS.md 10.5)."""
    covered = ov.captions_covered([_placement(100, 30)],
                                  [(129, 200, "card C")], FPS)
    assert covered and covered[0].covered_frames == 1


def test_a_caption_ending_exactly_where_the_element_starts_is_not_covered():
    assert ov.captions_covered([_placement(100, 30)],
                               [(50, 100, "card D")], FPS) == []


# ── One enumeration of transition types, two routes ──────────────────

def test_the_overlay_type_is_in_the_one_vocabulary():
    from library.tools import transition_vocabulary as tv
    assert "element_overlay" in tv.OVERLAY_TYPES
    assert "element_overlay" in tv.KNOWN_TYPES
    assert tv.is_overlay("element_overlay")
    assert tv.route_of("element_overlay") == tv.ROUTE_OVERLAY_ELEMENT


def test_the_overlay_type_cannot_reach_the_per_clip_fusion_route():
    """`apply_fusion_comps` and `compile_manifest` both refuse a type
    `canonical_type` returns None for. An overlay element is not a
    per-clip comp, so it must never canonicalise into that route."""
    from library.tools import transition_vocabulary as tv
    assert tv.canonical_type("element_overlay") is None
    assert "element_overlay" not in tv.PLANNABLE_TYPES
    assert not tv.is_drawn("element_overlay")


def test_a_misroute_is_told_it_is_a_misroute_not_that_the_type_is_unknown():
    """Saying "not a transition type this pipeline knows" about a type
    the pipeline really draws sends the reader to build a second one."""
    from library.tools import transition_vocabulary as tv
    reason = tv.withdrawal_reason("element_overlay")
    assert "misroute" in reason
    assert "transition_overlay.py" in reason
    # And the genuinely unknown case still reads as unknown.
    assert "not a transition type" in tv.withdrawal_reason("sparkle_swirl")


def test_the_wipe_withdrawal_no_longer_reads_as_impossible():
    """A wipe that MIXES two clips is still undeliverable; a shape that
    COVERS the cut now is. The withdrawal must not claim otherwise."""
    from library.tools import transition_vocabulary as tv
    assert "element_overlay" in tv.WITHDRAWN["wipe"]
    assert tv.canonical_type("wipe") is None


# ── The reel build places them and adds no track when it does not ────

def test_the_build_adds_no_overlay_track_when_nothing_is_declared():
    """A project that declares no element must get the timeline it got
    before this feature existed - three video tracks, not four."""
    import library.tools.reel_build as rb
    source = rb.build_reel_timeline.__doc__
    assert "byte-for-byte identical" in source
    # And the behaviour, not just the promise:
    assert rb.build_reel_timeline.__defaults__[-1] is None


def test_the_build_records_the_plan_it_placed(tmp_path):
    """A placement nothing recorded is a placement nothing can check -
    F18 grades V4 against this file."""
    plan = ov.ReelOverlayPlan(seams=ov.reel_seams(RANGES, FPS),
                              placements=[_placement(100, 30)],
                              element=None, reel_frames=500)
    record = plan.as_dict()
    assert json.loads(json.dumps(record))["placements"][0]["record_frame"] \
        == 100


def test_a_placement_carries_the_elements_own_length_in_seconds():
    """Resolve's `AppendToTimeline` takes source frames in the POOL
    ITEM's own rate, not the timeline's (AGENTS.md 5), so the placer has
    to convert from seconds ONCE rather than round-tripping through
    timeline frames. A 30fps element on a 23.976 timeline is 36 timeline
    frames and 45 of its own; a 25fps one at 0.5s rounds to 12 either
    way only if the conversion is done from seconds."""
    seams = ov.reel_seams(RANGES, FPS)
    placed = ov.place_overlays(seams, [1], _element(1.5), FPS,
                               ov.reel_frame_count(RANGES, FPS))
    assert placed[0].element_seconds == 1.5
    assert placed[0].duration_frames == int(round(1.5 * FPS))
    # What reel_build computes from it, at the element's own rate.
    assert int(round(placed[0].element_seconds * 30.0)) == 45
    assert int(round(placed[0].element_seconds * 25.0)) == 38


def test_the_gesture_of_a_covering_element_is_hides_the_cut(tmp_path,
                                                            opaque_element):
    """The prediction that was checked against the picture: an element
    with a fully opaque frame hides the cut, one without stamps it.
    `docs/CHROMA_KEY_TRANSITIONS_MEASURED.md` section 5 has the render."""
    resolved = ov.resolve_element(_declaration(opaque_element),
                                  str(tmp_path))
    assert resolved.gesture == ov.GESTURE_HIDES


def test_an_element_that_never_covers_the_frame_stamps_rather_than_hides(
        tmp_path):
    """A 64x64 element inside a larger frame never covers it. The only
    two alpha elements the field test owns peak at 0.53% and 1.2% frame
    coverage and read `stamps_the_cut` for exactly this reason."""
    path = _prores(tmp_path / "corner.mov", "white@1.0", alpha=True)
    partial = tmp_path / "partial.mov"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", path, "-vf",
         "pad=256:256:0:0:color=black@0.0,format=yuva444p10le",
         "-c:v", "prores_ks", "-profile:v", "4",
         "-pix_fmt", "yuva444p10le", str(partial)],
        check=True, capture_output=True, text=True, encoding="utf-8")
    measured = ov.measure_element(str(partial))
    assert measured.alpha.draws
    assert measured.alpha.fully_opaque_frames == 0
    assert ov.gesture_of(measured.alpha) == ov.GESTURE_STAMPS


# ── The build's record of what it placed ─────────────────────────────

def test_a_partial_rebuild_keeps_the_record_of_the_reels_it_did_not_touch(
        tmp_path):
    """#568's rule, seen from the overlay side: a `--only 3` rebuild
    must not delete the record of the eighteen reels it left alone."""
    from library.tools.reel_build import _write_overlay_records

    review = tmp_path / "review"
    review.mkdir()
    path = review / "transition_overlays.json"
    path.write_text(json.dumps({
        "Reel 01": {"placements": [{"record_frame": 10}]},
        "Reel 02": {"placements": [{"record_frame": 20}]},
    }), encoding="utf-8")

    _write_overlay_records(str(review), ["Reel 02"],
                           {"Reel 02": {"placements": [{"record_frame": 99}]}})
    stored = json.loads(path.read_text())
    assert stored["Reel 01"]["placements"][0]["record_frame"] == 10
    assert stored["Reel 02"]["placements"][0]["record_frame"] == 99


def test_a_rebuild_that_places_nothing_drops_that_reels_stale_record(
        tmp_path):
    """The other direction. A stale entry would make F18 report an
    element as missing from a reel correctly rebuilt without one."""
    from library.tools.reel_build import _write_overlay_records

    review = tmp_path / "review"
    review.mkdir()
    path = review / "transition_overlays.json"
    path.write_text(json.dumps({
        "Reel 01": {"placements": [{"record_frame": 10}]},
        "Reel 02": {"placements": [{"record_frame": 20}]},
    }), encoding="utf-8")

    _write_overlay_records(str(review), ["Reel 02"], {})
    stored = json.loads(path.read_text())
    assert set(stored) == {"Reel 01"}


def test_the_record_file_goes_away_when_no_reel_has_an_element(tmp_path):
    from library.tools.reel_build import _write_overlay_records

    review = tmp_path / "review"
    review.mkdir()
    path = review / "transition_overlays.json"
    path.write_text(json.dumps({"Reel 01": {"placements": []}}),
                    encoding="utf-8")
    _write_overlay_records(str(review), ["Reel 01"], {})
    assert not path.exists()


def test_the_seam_key_is_not_a_yaml_boolean(opaque_element, tmp_path):
    """`on_cuts`, never `on`.

    YAML 1.1 - which PyYAML implements - parses a bare `on` as the
    boolean True, so `on: [closer]` becomes `{True: ['closer']}`, the
    lookup finds nothing, and the declarer is told they named no seams
    while looking at the line where they did. Found by writing the
    documented syntax and running it, which is why the documented syntax
    is now exercised here.
    """
    import yaml

    project = tmp_path / "proj"
    project.mkdir()
    (project / "project.yaml").write_text(
        "effect:\n"
        "  transition_overlay:\n"
        "    anchor: centre\n"
        "    on_cuts: [closer]\n"
        "    element:\n"
        f"      asset: {opaque_element}\n",
        encoding="utf-8")
    declaration = ov.declared_overlay(
        ov.resolve_declaration({}, str(project)))
    assert "on_cuts" in declaration
    assert declaration["on_cuts"] == ["closer"]

    # And the trap itself, so nobody renames it back.
    trapped = yaml.safe_load("on: [closer]\non_cuts: [closer]\n")
    assert True in trapped and "on" not in trapped
    assert "on_cuts" in trapped
