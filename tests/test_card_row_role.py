"""The closing card's row is DECLARED, not defaulted.

The closing logo animation landed on V1 - Akshita's camera row - on
every reel because `build_reel_timeline` placed it on the first a-roll
row by POSITION, with nothing declared to say otherwise (2026-09-12,
captain's blue marker on Reel 13). The row is now one declared value -
`effect.card_row_role`, a track-plan ROLE - resolved through
`rows_for_role` like every other overlay element, and refused when
absent.

Covered here:
  A. the declaration itself (project wins, bad values refused, the
     actionable refusal when cards exist and no role does);
  B. the layout: card spans mint the role's row, and the placer replays
     the plan's own packing to find each card's lane;
  C. the placement, against fake Resolve: a tail card lands on the row
     NAMED for its role on layouts with different track counts - never
     V1 by index - and cards without a role refuse before anything is
     placed;
  D. the verifier: F13 fails a card on a row whose NAME does not match
     its declared role, and the snapshot classifier keeps a card on an
     overlay row inside the picture bucket;
  E. the rebuild digest carries the role where cards exist, and stays
     byte-identical where they do not.
"""
from __future__ import annotations

import pytest

from library.tools import full_frame_element as ffe
from library.tools import timeline_layout as layout

FPS = 24000 / 1001


# ── A. The declaration ─────────────────────────────────────────────

def test_no_declaration_is_no_role_not_a_default(tmp_path):
    assert ffe.resolve_card_row_role({}, str(tmp_path)) is None
    assert ffe.resolve_card_row_role(None, None) is None


def _project(tmp_path, effect: dict):
    import yaml
    (tmp_path / "project.yaml").write_text(
        yaml.safe_dump({"effect": effect}), encoding="utf-8")
    return str(tmp_path)


def test_the_brand_template_may_declare_the_row(tmp_path):
    assert ffe.resolve_card_row_role(
        {"card_row_role": "semantic"}, str(tmp_path)) == "semantic"


def test_the_project_wins_over_the_brand(tmp_path):
    project = _project(tmp_path, {"card_row_role": "motion_graphics"})
    assert ffe.resolve_card_row_role(
        {"card_row_role": "semantic"}, project) == "motion_graphics"


def test_the_project_may_declare_what_the_brand_does_not(tmp_path):
    project = _project(tmp_path, {"card_row_role": "semantic"})
    assert ffe.resolve_card_row_role({}, project) == "semantic"


@pytest.mark.parametrize("bad", ["V5", "1", "a_roll", "captions", ""])
def test_anything_but_the_two_roles_is_refused_by_name(bad, tmp_path):
    with pytest.raises(ffe.CardRowRoleError, match="card_row_role"):
        ffe.resolve_card_row_role({"card_row_role": bad}, str(tmp_path))


def test_a_non_mapping_effect_is_refused(tmp_path):
    _project(tmp_path, ["not", "a", "mapping"])
    with pytest.raises(ffe.CardRowRoleError, match="must be a mapping"):
        ffe.resolve_card_row_role({}, str(tmp_path))


def test_requiring_names_the_choice_when_cards_exist_but_no_role(tmp_path):
    with pytest.raises(ffe.CardRowRoleError, match="card_row_role"):
        ffe.require_card_row_role({}, str(tmp_path))


def test_requiring_returns_the_declared_role(tmp_path):
    project = _project(tmp_path, {"card_row_role": "semantic"})
    assert ffe.require_card_row_role({}, project) == "semantic"


# ── B. The layout ──────────────────────────────────────────────────

def _angles():
    return [{"key": "1", "label": "Akshita",
             "speech_name": "Akshita CH1", "program_channel": 1},
            {"key": "2", "label": "Craig",
             "speech_name": "Craig CH1", "program_channel": 1}]


def test_card_spans_mint_the_role_row_with_no_overlays():
    """A row exists because something goes on it: the card alone is
    enough for a Semantic row."""
    plan = layout.plan_layout({
        "angles": _angles(), "caption_spans": [(0, 100)],
        "card_role": "semantic", "card_spans": [(480, 551)]})
    names = [(t.index, t.name) for t in plan.video_tracks]
    assert names == [(1, "Akshita"), (2, "Craig"), (3, "Subtitles"),
                     (4, "Semantic")]
    assert plan.rows_for_role("semantic")[0].index == 4


def test_the_card_joins_the_role_packing_not_beside_it():
    """An overlay overlapping the card mints a second row rather than
    sharing one - and the replay finds the card's lane."""
    overlay = (400, 500)
    card = (480, 551)
    plan = layout.plan_layout({
        "angles": _angles(),
        "semantic_spans": [overlay],
        "card_role": "semantic", "card_spans": [card]})
    rows = plan.rows_for_role("semantic")
    assert [t.name for t in rows] == ["Semantic", "Semantic 2"]
    ordered = layout.card_spans_for_role(plan.material, "semantic")
    assert ordered == [overlay, card]
    assert layout.lane_of_span(ordered, 0) == 0
    assert layout.lane_of_span(ordered, 1) == 1
    assert rows[layout.lane_of_span(ordered, 1)].name == "Semantic 2"


def test_non_overlapping_card_shares_the_role_row():
    """The tail card plays after the body, so it shares the row - one
    row, both lanes."""
    plan = layout.plan_layout({
        "angles": _angles(),
        "semantic_spans": [(0, 100)],
        "card_role": "semantic", "card_spans": [(480, 551)]})
    rows = plan.rows_for_role("semantic")
    assert [t.name for t in rows] == ["Semantic"]
    ordered = layout.card_spans_for_role(plan.material, "semantic")
    assert layout.lane_of_span(ordered, 1) == 0


def test_a_motion_graphics_card_row_uses_the_mg_packing():
    plan = layout.plan_layout({
        "angles": _angles(),
        "mg_spans": [(0, 100)],
        "card_role": "motion_graphics", "card_spans": [(480, 551)]})
    rows = plan.rows_for_role("motion_graphics")
    assert [t.name for t in rows] == ["Motion Graphics"]


def test_an_unknown_card_role_is_refused_at_the_plan():
    with pytest.raises(ValueError, match="card_role"):
        layout.plan_layout({"angles": _angles(), "card_role": "V5"})


def test_card_spans_without_a_role_are_refused_at_the_plan():
    with pytest.raises(ValueError, match="card_role"):
        layout.plan_layout(
            {"angles": _angles(), "card_spans": [(480, 551)]})


# ── C. The placement, against fake Resolve ─────────────────────────
# The faithful fakes live with the SOP proof; this file only adds the
# card to the world they already build.

from tests.test_reel_build_sop_conformance import (  # noqa: E402
    FakeMoment,
    _caption,
    _master_clips,
    _semantic,
    _transcript,
    _world,
)
from library.tools.full_frame_element import PlannedCard  # noqa: E402
from library.tools.reel_build import (  # noqa: E402
    ReelBuildError,
    build_reel_timeline,
    reel_track_material,
)


def _tail_card(tmp_path, start=480, frames=71):
    path = tmp_path / "logo_reveal.mov"
    path.write_bytes(b"\x00")
    return PlannedCard(
        index=1, element="full_frame_clip", placement="tail",
        reel_start_frame=start, duration_seconds=3.0,
        duration_frames=frames, props={}, render_name="logo_reveal",
        source_frames=90, rendered_path=str(path))


def _card_row_names(timeline):
    return {i: timeline.GetTrackName("video", i)
            for i in range(1, timeline.GetTrackCount("video") + 1)}


def _row_of(timeline, name):
    for i in range(1, timeline.GetTrackCount("video") + 1):
        if any(item.GetName() == name
               for item in timeline.GetItemListInTrack("video", i)):
            return i
    return None


def test_a_tail_card_lands_on_the_named_semantic_row_not_v1(tmp_path):
    """The defect, planted: V1 here is Akshita, and the card must not
    be on it."""
    timeline, pool, project = _world()
    record = build_reel_timeline(
        project, FakeMoment(), _master_clips(), [_caption(2.0, 4.0)],
        23.976, 1080, 1920, str(tmp_path), _transcript(),
        cards=[_tail_card(tmp_path)],
        semantic_segments=[_semantic(0.0, 48)],
        master_timeline=None, program_channels={"1": 1, "2": 1},
        card_row_role="semantic")
    names = _card_row_names(timeline)
    assert names[1] == "Akshita"
    assert _row_of(timeline, "logo_reveal.mov") == 4
    assert names[4] == "Semantic"
    # And the SOP proof still reads the built timeline clean: named
    # rows, nothing empty, picture linked to its speech.
    from library.tools.timeline_conformance import verify_timeline
    from library.tools.timeline_layout import TrackPlan, TrackSpec
    raw = record["track_plan"]
    plan = TrackPlan(
        video_tracks=[TrackSpec(**t) for t in raw["video_tracks"]],
        audio_tracks=[TrackSpec(**t) for t in raw["audio_tracks"]],
        material=raw.get("material", {}))
    report = verify_timeline(timeline, plan=plan)
    assert report["passed"], report["violations"]


def test_the_same_card_lands_on_motion_graphics_when_that_is_declared(
        tmp_path):
    """One declared value to change: the same reel, a different role,
    a different track count - and the card follows the NAME."""
    timeline, pool, project = _world()
    build_reel_timeline(
        project, FakeMoment(), _master_clips(), [_caption(2.0, 4.0)],
        23.976, 1080, 1920, str(tmp_path), _transcript(),
        cards=[_tail_card(tmp_path)],
        master_timeline=None, program_channels={"1": 1, "2": 1},
        card_row_role="motion_graphics")
    names = _card_row_names(timeline)
    assert names[1] == "Akshita"
    row = _row_of(timeline, "logo_reveal.mov")
    assert row is not None and row != 1
    assert names[row] == "Motion Graphics"


def test_the_card_follows_the_name_across_track_counts(tmp_path):
    """Explainer + semantic + captions is five video rows; the card is
    still on the row named Semantic rather than an index."""
    timeline, pool, project = _world()
    from tests.test_reel_build_sop_conformance import _semantic as _sem
    build_reel_timeline(
        project, FakeMoment(), _master_clips(), [_caption(2.0, 4.0)],
        23.976, 1080, 1920, str(tmp_path), _transcript(),
        cards=[_tail_card(tmp_path)],
        explainer_segments=[_sem(0.0, 48)],
        semantic_segments=[_sem(48.0, 96)],
        master_timeline=None, program_channels={"1": 1, "2": 1},
        card_row_role="semantic")
    names = _card_row_names(timeline)
    assert len(names) == 5
    row = _row_of(timeline, "logo_reveal.mov")
    assert names[row] == "Semantic"


def test_cards_without_a_declared_role_refuse_before_placing(tmp_path):
    timeline, pool, project = _world()
    with pytest.raises(ReelBuildError, match="card_row_role"):
        build_reel_timeline(
            project, FakeMoment(), _master_clips(), [], 23.976, 1080,
            1920, str(tmp_path), _transcript(),
            cards=[_tail_card(tmp_path)],
            master_timeline=None, program_channels={"1": 1, "2": 1},
            card_row_role=None)
    assert not hasattr(pool, "created_name"), \
        "the refusal must fire before a timeline exists"


def test_material_carries_the_card_spans_with_their_role():
    material = reel_track_material(
        [], {"1": 1}, card_role="semantic", card_spans=[(480, 551)])
    assert material["card_role"] == "semantic"
    assert material["card_spans"] == [(480, 551)]


def test_material_refuses_spans_with_no_role():
    with pytest.raises(ReelBuildError, match="card_row_role"):
        reel_track_material([], {"1": 1}, card_spans=[(480, 551)])


# ── D. The verifier ────────────────────────────────────────────────

from library.tools.reel_conformance_verifier import (  # noqa: E402
    FindingClass,
    PlannedCard as _PlanCard,
    TimelineItem,
    _snapshot_to_reel_timeline,
    check_delivered_framing,
    check_full_frame_cards,
    check_picture_holes,
)

CARD_FILE = "/p/logo_reveal.mov"
CARD_FRAMES = 71


def _placed(track_index, track_name, start=480, frames=CARD_FRAMES,
            transform=None):
    return TimelineItem(
        track_type="video", track_index=track_index,
        start_frame=start, end_frame=start + frames,
        duration_frames=frames,
        source_start_frame=0, source_end_frame=90,
        source_file=CARD_FILE, speaker=None, name="logo_reveal.mov",
        track_name=track_name, transform=dict(transform or {}))


def _declared_card():
    return _PlanCard(render_name="logo_reveal", placement="tail",
                     reel_start_frame=480, duration_frames=CARD_FRAMES,
                     element="full_frame_clip")


def test_f13_fails_a_card_on_a_row_whose_name_breaks_its_role():
    """THE gate this task was missing: the logo on V1 Akshita, with
    semantic declared, is an error naming the declared row.

    The input that breaks it is a card item whose track NAME is a
    camera row while `card_row_role` declares an overlay role."""
    findings = check_full_frame_cards(
        "Reel 13", [_declared_card()], [_placed(1, "Akshita")],
        1080, 1920, FPS, card_row_role="semantic")
    assert [f.finding_class for f in findings] == [FindingClass.F13]
    assert "Semantic" in findings[0].message
    assert findings[0].detail["card_row_role"] == "semantic"


def test_f13_passes_the_card_on_its_declared_row():
    assert check_full_frame_cards(
        "Reel 13", [_declared_card()], [_placed(5, "Semantic")],
        1080, 1920, FPS, card_row_role="semantic") == []


def test_f13_reads_numbered_layer_rows_by_name():
    assert check_full_frame_cards(
        "Reel 13", [_declared_card()], [_placed(6, "Motion Graphics 2")],
        1080, 1920, FPS, card_row_role="motion_graphics") == []


def test_f13_legacy_cards_still_read_v1_without_a_role():
    assert check_full_frame_cards(
        "Reel 13", [_declared_card()], [_placed(1, "Akshita")],
        1080, 1920, FPS) == []


def test_f13_legacy_cards_above_v1_still_fail_without_a_role():
    findings = check_full_frame_cards(
        "Reel 13", [_declared_card()], [_placed(4, "Semantic")],
        1080, 1920, FPS)
    assert any("belongs on V1" in f.message for f in findings)


def test_a_span_stays_on_v1_whatever_the_card_row_is():
    span = _PlanCard(render_name="reel_13_span_01", placement="span",
                     reel_start_frame=0, duration_frames=480,
                     element="full_frame_span")
    item = TimelineItem(
        track_type="video", track_index=1,
        start_frame=0, end_frame=480, duration_frames=480,
        source_start_frame=0, source_end_frame=480,
        source_file="/p/reel_13_span_01.mov", speaker=None,
        name="reel_13_span_01.mov", track_name="Akshita")
    assert check_full_frame_cards(
        "Reel 13", [span], [item], 1080, 1920, FPS,
        card_row_role="semantic") == []


def test_the_classifier_keeps_a_card_on_an_overlay_row_as_picture():
    """Without the plan's names a card on V5 files as a semantic
    visual; with them it stays picture - and the picture extent reads
    past the footage end to the card's end."""

    class _Clip:
        def __init__(self, index, name, track_name, start, end):
            self.track_type = "video"
            self.track_index = index
            self.track_name = track_name
            self.timeline_start, self.timeline_end = start, end
            self.duration = end - start
            self.source_in_frame, self.source_out_frame = 0, 90
            self.source_file = name
            self.speaker = None
            self.name, self.resolve_item_id = name.rsplit("/", 1)[-1], ""

    class _Snapshot:
        fps = FPS
        timeline_name = "Reel 13"
        start_frame, end_frame = 0, 551
        width, height = 1080, 1920
        clips = [_Clip(1, "/m/a.MXF", "Akshita", 0.0, 20.0),
                 _Clip(5, CARD_FILE, "Semantic", 20.0, 23.0)]

    card = _declared_card()
    card = _PlanCard(render_name=card.render_name,
                     placement=card.placement,
                     reel_start_frame=int(round(20.0 * FPS)),
                     duration_frames=int(round(3.0 * FPS)),
                     element=card.element)
    plain = _snapshot_to_reel_timeline(_Snapshot())
    assert len(plain.video_items) == 1, \
        "without the plan's names the card files as decoration"
    named = _snapshot_to_reel_timeline(_Snapshot(), cards=[card])
    assert len(named.video_items) == 2
    assert named.semantic_items == ()
    assert named.picture_frames == int(round(23.0 * FPS))


def test_f12_grades_a_card_on_its_declared_row_against_the_frame():
    """A card that moved off V1 must still pass through F12 - not
    leave it silently."""
    identity = {"ZoomX": 1.0, "ZoomY": 1.0, "Pan": 0.0, "Tilt": 0.0,
                "CropLeft": 0.0, "CropRight": 0.0,
                "CropTop": 0.0, "CropBottom": 0.0}
    item = _placed(5, "Semantic", transform=identity)
    assert check_delivered_framing(
        "Reel 13", [item], 1080, 1920, source_sizes={},
        declared_intent=0.0, cards=[_declared_card()]) == []


def test_a_tail_card_on_its_declared_row_leaves_no_picture_hole():
    footage = TimelineItem(
        track_type="video", track_index=1,
        start_frame=0, end_frame=480, duration_frames=480,
        source_start_frame=0, source_end_frame=480,
        source_file="/m/a.MXF", speaker="Akshita", name="a.MXF",
        track_name="Akshita")
    assert check_picture_holes(
        "Reel 13", [footage, _placed(5, "Semantic")]) == []


# ── E. The digest ──────────────────────────────────────────────────

from library.tools import reel_rebuild_need as need  # noqa: E402


def _derivation(**overrides):
    body = dict(
        reel_number=1,
        engine_code="e" * 64,
        project_wide="p" * 64,
        plan_content_hash="l" * 64,
        transcript_hash="t" * 64,
        master_digest="m" * 64,
        ranges=[(1.0, 2.0)],
        placements_list=[],
        cards=[],
        caption_segments=[],
        explainer_segments=[],
        semantic_segments=[],
        overlay_placements=[],
        motion_record={},
        ending=None,
        look=None,
        grade_cdl=None,
        grade_look=None,
        power_grade=None,
    )
    body.update(overrides)
    return need.derivation_digest(**body)


def _digest_card():
    return type("C", (), {"placement": "tail",
                          "render_name": "logo_reveal",
                          "reel_start_frame": 480,
                          "duration_frames": 71})()


def test_a_moved_card_row_changes_the_derivation():
    """A reel whose closing card moves rows IS stale: the digest must
    say so, or the fix never reaches the sixteen reels carrying the
    logo on V1."""
    before = _derivation(cards=[_digest_card()])
    after = _derivation(cards=[_digest_card()],
                        card_row_role="semantic")
    assert before != after


def test_the_role_changes_nothing_where_no_cards_exist():
    """A project that declares no cards digests byte-identically with
    or without the role - no fleet-wide rebuild for a declaration
    nobody there needs."""
    assert _derivation() == _derivation(card_row_role="semantic")


# ── E2. The neighbouring row-name gap, same class ──────────────────

from library.tools.reel_build import reel_angles  # noqa: E402
from library.tools.timeline_ingest import TimelineClip  # noqa: E402


def test_a_motion_graphics_row_is_a_layer_not_a_camera():
    """`speaker_identity.TRACK_NAME` is "Motion Graphics", and the reel
    builder tells angles from decoration by the layout owner's names -
    which omitted it. A master carrying lower thirds read as a third
    camera; it must not."""
    def _clip(index, name):
        return TimelineClip(
            resolve_item_id=f"mg-{index}", track_type="video",
            track_index=index, track_name=name, speaker=None,
            source_file="/m/mg.mov", source_in=0.0, source_out=1.0,
            source_in_frame=0, source_out_frame=24, source_frames=100,
            timeline_start=0.0, timeline_end=1.0, name="mg")
    clips = [_clip(1, "Akshita"), _clip(2, "Craig"),
             _clip(5, "Motion Graphics")]
    assert [a["key"] for a in reel_angles(clips)] == ["1", "2"]
