"""A transition element laid over a cut: what it draws, and what it costs.

Every gate is proved in BOTH directions (AGENTS.md 10.4): the alpha
instrument is validated against known opaque, empty and no-alpha fixtures
first. History: docs/evidence/transition_overlay.md#the-tests.
"""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from library.tools import transition_overlay as ov
from library.tools.transition_overlay import (
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

def test_the_alpha_instrument_reads_known_opaque_and_empty_elements(
        opaque_element, empty_element):
    """Both directions: an instrument that always returned zero would pass
    the empty fixture alone. Reachability is this measurement."""
    measured = ov.measure_alpha(opaque_element)
    assert (measured.frames_read, measured.max_alpha) == (5, 255)
    assert measured.draws is True and measured.has_channel is True
    measured = ov.measure_alpha(empty_element)
    assert (measured.frames_read, measured.max_alpha) == (5, 0)
    assert measured.draws is False and measured.has_channel is True

    ok, why = ov.element_is_reachable(opaque_element)
    assert ok and why == ""
    ok, why = ov.element_is_reachable(empty_element)
    assert not ok and "zero on all" in why


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


# ── The declaration ──────────────────────────────────────────────────

def _declaration(path, **over):
    base = {"element": {"asset": path}, "anchor": "centre",
            "on_cuts": [SEAM_TAKE_REMOVED]}
    base.update(over)
    return base


def _malformed_declarations(asset):
    """(name, declaration, match) - every one must raise by name."""
    no_anchor = _declaration(asset)
    del no_anchor["anchor"]
    both = _declaration(asset)
    both["element"].update(composition="TransitionBumper",
                           duration_seconds=0.2)
    long = _declaration(asset)
    long["element"]["duration_seconds"] = 3.0   # the file is 0.2s
    return [
        ("no anchor", no_anchor, "no default"),
        ("unknown anchor", _declaration(asset, anchor="sideways"), None),
        ("asset and composition", both, "both"),
        ("neither", {"element": {}, "anchor": "centre"}, "neither"),
        ("composition without duration",
         {"element": {"composition": "TransitionBumper"}, "anchor": "centre"},
         "duration_seconds"),
        # NO STEP OF THE REELS PROCESS RENDERS A COMPOSITION: placing
        # nothing would be a vocabulary entry that does not draw.
        ("composition nothing renders",
         {"element": {"composition": "TransitionBumper",
                      "duration_seconds": 1.5}, "anchor": "after"},
         "NO STEP OF THE REELS PROCESS RENDERS A COMPOSITION(.|\n)*asset:"),
        ("duration disagrees with the file", long, "measures"),
        ("asset not on disk", _declaration("brand_assets/absent.mov"),
         "not found"),
    ]


def test_each_malformed_declaration_is_refused_by_name(opaque_element,
                                                      tmp_path):
    for name, declaration, match in _malformed_declarations(opaque_element):
        with pytest.raises(TransitionOverlayError, match=match):
            ov.resolve_element(declaration, str(tmp_path))
            pytest.fail(name)


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


def test_the_closer_seam_is_named_when_there_is_one():
    frames = ov.reel_frame_count(RANGES[:-1], FPS)
    seams = ov.reel_seams(RANGES, FPS, closer_seam_frame=frames)
    kinds = [s.kind for s in seams if s.carries]
    assert kinds == [SEAM_TAKE_REMOVED, SEAM_CLOSER]


def test_a_one_range_reel_has_no_cut_at_all():
    seams = ov.reel_seams([(0.0, 5.0)], FPS)
    assert ov.carrying_seams(seams) == []
    assert {s.kind for s in seams} == {SEAM_REEL_HEAD, SEAM_REEL_TAIL}


# ── Selecting seams ──────────────────────────────────────────────────

def test_selecting_seams_refuses_what_names_no_real_seam():
    seams = ov.reel_seams(RANGES, FPS)
    with pytest.raises(TransitionOverlayError, match="no default"):
        ov.select_seams({"anchor": "centre"}, seams)
    with pytest.raises(NotASeam):
        ov.select_seams({"seams": [99]}, seams)
    with pytest.raises(TransitionOverlayError, match="not"):
        ov.select_seams({"on_cuts": ["whenever_it_feels_right"]}, seams)


def test_selecting_seams_by_kind_and_by_index():
    seams = ov.reel_seams(RANGES, FPS)
    assert ov.select_seams({"seams": [2]}, seams) == [2]
    # A reel with no closer is a real answer, not an error.
    assert ov.select_seams({"on_cuts": [SEAM_CLOSER]}, seams) == []
    frames = ov.reel_frame_count(RANGES[:-1], FPS)
    seams = ov.reel_seams(RANGES, FPS, closer_seam_frame=frames)
    chosen = ov.select_seams({"on_cuts": [SEAM_CLOSER]}, seams)
    assert [seams[i].kind for i in chosen] == [SEAM_CLOSER]


# ── Placing ──────────────────────────────────────────────────────────

def _element(seconds=1.0, anchor="centre"):
    return ResolvedElement(anchor=anchor, mode="asset", path="/e.mov",
                           composition=None, source=None,
                           duration_seconds=seconds,
                           declared_duration_seconds=None)


def test_each_anchor_puts_the_element_where_it_says():
    for anchor, expected in (("before", 70), ("after", 100), ("centre", 85)):
        assert ov.anchor_record_frame(100, 30, anchor) == expected, anchor
    # An odd length gives the extra frame to the outgoing side: 16 + 15.
    assert ov.anchor_record_frame(100, 31, "centre") == 84


def test_placing_on_a_carrying_seam_produces_exact_frames():
    seams = ov.reel_seams(RANGES, FPS)
    placed = ov.place_overlays(seams, [1], _element(1.5), FPS,
                               ov.reel_frame_count(RANGES, FPS))
    assert len(placed) == 1
    assert placed[0].track_index == OVERLAY_TRACK
    assert placed[0].duration_frames == int(round(1.5 * FPS))
    assert placed[0].seam_kind == SEAM_TAKE_REMOVED
    # AppendToTimeline takes frames at the POOL ITEM's own rate (AGENTS.md
    # 5), so the placement carries the element's length in SECONDS.
    assert placed[0].element_seconds == 1.5


def test_a_placement_that_cannot_sit_on_the_reel_is_refused():
    seams = ov.reel_seams(RANGES, FPS)
    frames = ov.reel_frame_count(RANGES, FPS)
    with pytest.raises(NotASeam, match="reel_head"):
        ov.place_overlays(seams, [0], _element(0.2), FPS, frames)
    # The first cut is 4s in; a 20s element anchored `before` cannot fit.
    with pytest.raises(OverlayDoesNotFit, match="before the reel begins"):
        ov.place_overlays(seams, [1], _element(20.0, "before"), FPS, frames)
    with pytest.raises(TransitionOverlayError, match="under one frame"):
        ov.place_overlays(seams, [1], _element(0.001), FPS, frames)


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


# ── The whole reel pass ──────────────────────────────────────────────

def test_a_project_with_no_declaration_gets_an_empty_plan_that_says_why():
    assert ov.declared_overlay(None) is None
    assert ov.declared_overlay({"motion_accents": True}) is None
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
        empty_element, no_alpha_element, tmp_path):
    effect = {"transition_overlay": _declaration(empty_element)}
    with pytest.raises(ElementDrawsNothing):
        ov.plan_reel_overlays(effect, RANGES, FPS, str(tmp_path))
    effect = {"transition_overlay": _declaration(no_alpha_element)}
    with pytest.raises(ElementHasNoAlpha):
        ov.plan_reel_overlays(effect, RANGES, FPS, str(tmp_path))


# ── The duration ruling, checked rather than asserted ────────────────

def test_an_overlay_is_additive_to_reel_length_and_footage_binding(
        opaque_element, tmp_path):
    """TIMING_IS_ADDITIVE, as a measurement: the reel length and
    `footage_binding_hash` (each speech block's clip, source span AND
    timeline span) are byte-identical either side of the overlay pass."""
    from library.tools.plan_provenance import footage_binding_hash

    spine = {"structure": [
        {"block_type": "speech", "clip_id": "clip_001",
         "source_start": 10.0, "source_end": 14.0,
         "timeline_start": 0.0, "timeline_end": 4.0},
        {"block_type": "speech", "clip_id": "clip_001",
         "source_start": 20.0, "source_end": 23.5,
         "timeline_start": 4.0, "timeline_end": 7.5},
    ]}
    hash_before = footage_binding_hash(spine)
    frames_before = ov.reel_frame_count(RANGES, FPS)

    effect = {"transition_overlay": _declaration(opaque_element)}
    plan = ov.plan_reel_overlays(effect, RANGES, FPS, str(tmp_path))
    assert plan.placements, "this proves nothing if nothing was placed"
    assert plan.reel_frames == frames_before
    assert footage_binding_hash(spine) == hash_before


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
    # No threshold: one frame of contact is still reported (AGENTS.md 10.5).
    covered = ov.captions_covered([_placement(100, 30)],
                                  [(129, 200, "card C")], FPS)
    assert covered and covered[0].covered_frames == 1


# ── One enumeration of transition types, two routes ──────────────────


def test_the_overlay_type_cannot_reach_the_per_clip_fusion_route():
    """`apply_fusion_comps` and `compile_manifest` both refuse a type
    `canonical_type` returns None for. An overlay element is not a
    per-clip comp, so it must never canonicalise into that route."""
    from library.tools import transition_vocabulary as tv
    assert tv.canonical_type("element_overlay") is None
    assert "element_overlay" not in tv.PLANNABLE_TYPES
    assert not tv.is_drawn("element_overlay")
    # A misroute is told it is one, not that the type is unknown - that
    # would send the reader to build a second one.
    reason = tv.withdrawal_reason("element_overlay")
    assert "misroute" in reason
    assert "transition_overlay.py" in reason
    # And the genuinely unknown case still reads as unknown.
    assert "not a transition type" in tv.withdrawal_reason("sparkle_swirl")


# ── The reel build places them and adds no track when it does not ────


def test_a_covering_element_hides_the_cut_and_a_partial_one_stamps_it(
        tmp_path, opaque_element):
    """The prediction that was checked against the picture: an element
    with a fully opaque frame hides the cut, one without stamps it.
    `docs/CHROMA_KEY_TRANSITIONS_MEASURED.md` section 5 has the render."""
    resolved = ov.resolve_element(_declaration(opaque_element),
                                  str(tmp_path))
    assert resolved.gesture == ov.GESTURE_HIDES
    # A 64x64 element inside a larger frame never covers it: it stamps.
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

    # The other direction: a rebuild that places nothing drops that reel's
    # stale record, or F18 reports an element missing from a correct reel.
    _write_overlay_records(str(review), ["Reel 02"], {})
    assert set(json.loads(path.read_text())) == {"Reel 01"}


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
