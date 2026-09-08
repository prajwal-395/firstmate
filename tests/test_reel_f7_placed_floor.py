"""F7 measures PLACED item durations against the readability floor, with
no last-of-block exemption - and the same floor covers placed A/V items.

On 2026-09-08 the captain watched the fully-approved reels and reported
"subtitles misplaced". The investigation
(`data/vep-approved-reels-still-have-visible-defects/report.md`) found
caption cards on screen for THREE FRAMES - R02 card 0 "yeah." 3f, R03
card 3 "yeah" 3f, R19 card 32 "yeah." 2f at 23.976fps - each HELD by
`check_short_captions` as a warning because each placed item is
trivially the last card of its own block, the exemption's only clause.
A check whose exemption is always satisfied cannot refuse.

The exemption's intent was real: a block-final card cannot be lengthened
by regrouping (it leaves when the block does). But "nothing can lengthen
it" is a fact about the grouping, not a claim the card is readable - the
remedy belongs upstream (rejoin the fragment row to its sentence; do not
author sub-second keeps), and on the placed path the viewer sees a flash
either way. So F7 fails every placed item under the floor, whatever
block it ends with.

The floor is not chosen: it is `manifest_validator.
MIN_CAPTION_DISPLAY_SECONDS` (0.5s) - the pipeline's own hard floor
(AGENTS.md 10.4; `render_qa`'s `subtitle_too_short`; the manifest P6
check). At 23.976fps that is 12 frames; the 2-3 frame flash cards sit
an order of magnitude below it, so no correct reel is near the line.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from library.tools import reel_conformance_verifier
from library.tools.manifest_validator import MIN_CAPTION_DISPLAY_SECONDS
from library.tools.reel_conformance_verifier import (
    FindingClass,
    check_short_av_items,
    check_short_captions,
)

FPS = 24000 / 1001  # 23.976 exact - the reels' own frame rate


def _placed_card(frames, text="yeah.", last_of_own_block=True):
    """A placed caption card dict as `verify_reel` builds it off the
    timeline - plus, when asked, the block keys that used to exempt it.

    A one-card block's only card is trivially its last, which is the
    shape every flash card in the report takes.
    """
    card = {"reel_start": 0.0,
            "reel_end": frames / FPS,
            "text": text,
            "speaker": None,
            "frames": frames}
    if last_of_own_block:
        card["block_position"] = "7"
        card["block_end"] = frames / FPS
    return card


def _errors(findings):
    return [f for f in findings if f.severity == "error"]


def test_the_floor_is_the_pipelines_own_not_a_chosen_number():
    assert MIN_CAPTION_DISPLAY_SECONDS == 0.5
    import inspect
    default = inspect.signature(
        check_short_captions).parameters["min_duration_seconds"].default
    assert default == MIN_CAPTION_DISPLAY_SECONDS
    default_av = inspect.signature(
        check_short_av_items).parameters["min_duration_seconds"].default
    assert default_av == MIN_CAPTION_DISPLAY_SECONDS


def test_reel_02_flash_card_now_fails():
    """R02 card 0 "yeah." - 3 frames, last of its own block."""
    findings = check_short_captions(
        "Reel 02 - seo-that-hurts-your-ai-ranking",
        [_placed_card(3)], FPS)
    errors = _errors(findings)
    assert len(errors) == 1, [f.message for f in findings]
    assert errors[0].finding_class == FindingClass.F7
    assert errors[0].detail["duration_frames"] == 3


def test_reel_03_flash_card_now_fails():
    """R03 card 3 "yeah" - 3 frames, last of its own block."""
    findings = check_short_captions(
        "Reel 03 - search-didnt-change-the-question-did",
        [_placed_card(3, text="yeah")], FPS)
    errors = _errors(findings)
    assert len(errors) == 1, [f.message for f in findings]
    assert errors[0].finding_class == FindingClass.F7


def test_reel_19_flash_card_now_fails():
    """R19 card 32 "yeah." - 2 frames (0.08s), last of its own block."""
    findings = check_short_captions(
        "Reel 19 - can-you-game-ai", [_placed_card(2)], FPS)
    errors = _errors(findings)
    assert len(errors) == 1, [f.message for f in findings]
    assert errors[0].finding_class == FindingClass.F7
    assert errors[0].detail["duration_frames"] == 2


def test_no_card_is_held_quietly_or_loudly():
    """Nothing is HELD any more: no warning carries an exemption."""
    findings = check_short_captions(
        "Reel 02", [_placed_card(3), _placed_card(2)], FPS)
    assert [f for f in findings if f.severity == "warning"] == []
    assert len(_errors(findings)) == 2


def test_a_correct_length_card_still_passes():
    findings = check_short_captions(
        "Reel 01", [_placed_card(int(round(1.0 * FPS)),
                                   text="a full second of text")], FPS)
    assert findings == []


def test_the_floor_is_twelve_frames_at_reel_rate():
    """0.5s at 23.976fps is 11.99 frames: 12 passes, 11 fails."""
    assert check_short_captions("R", [_placed_card(12)], FPS) == []
    errors = _errors(check_short_captions("R", [_placed_card(11)], FPS))
    assert len(errors) == 1


def _item(track_type, track_index, duration_frames, name="clip"):
    return reel_conformance_verifier.TimelineItem(
        track_type=track_type, track_index=track_index,
        start_frame=0, end_frame=duration_frames,
        duration_frames=duration_frames,
        source_start_frame=0, source_end_frame=duration_frames,
        source_file="/m/a.MXF", speaker="Akshita", name=name)


def test_fragment_av_slivers_fail_against_the_same_floor():
    """The report's keep-range slivers: R02's 3f "their keywords" video
    chirp and R04's 5f "size." audio blip are under the same 12-frame
    floor as the flash cards."""
    findings = check_short_av_items(
        "Reel 02",
        [_item("video", 1, 3, name="their keywords"),
         _item("video", 1, 600, name="a real take")],
        [_item("audio", 1, 5, name="size."),
         _item("audio", 1, 600, name="a real take")],
        FPS)
    errors = _errors(findings)
    assert len(errors) == 2, [f.message for f in findings]
    assert all(f.finding_class == FindingClass.F7 for f in errors)
    assert {f.detail["duration_frames"] for f in errors} == {3, 5}


def test_full_length_av_items_pass():
    findings = check_short_av_items(
        "Reel 01", [_item("video", 1, 600)], [_item("audio", 1, 600)],
        FPS)
    assert findings == []


def test_verify_reel_grades_the_placed_card_not_the_plan():
    """End to end: the plan asks for a full-length card, the timeline
    carries a 2-frame flash - F7 must fail the flash, not pass the plan.
    """
    from library.tools.reel_conformance_verifier import (
        PlannedCaption,
        PlannedPlacement,
        ReelPlan,
        ReelTimeline,
        TimelineItem,
        verify_reel,
    )
    plan = ReelPlan(
        reel_name="Reel 19 - can-you-game-ai", reel_number=19,
        plan_seconds=30.0, plan_frames=round(30.0 * FPS, 1),
        span_start=0.0, span_end=30.0,
        placements=(PlannedPlacement(
            track_index=1, speaker="Akshita", record_seconds=0.0,
            source_in=0.0, source_out=30.0,
            source_file="/m/a.MXF"),),
        captions=(PlannedCaption(
            start_seconds=0.0, end_seconds=1.0, text="yeah.",
            speaker="Akshita", frames=int(round(1.0 * FPS)),
            block_position="7", block_end_seconds=1.0),),
        keep_ranges=((0.0, 30.0),))
    picture = TimelineItem(
        track_type="video", track_index=1, start_frame=0,
        end_frame=int(round(30.0 * FPS)),
        duration_frames=int(round(30.0 * FPS)),
        source_start_frame=0,
        source_end_frame=int(round(30.0 * FPS)),
        source_file="/m/a.MXF", speaker="Akshita", name="take")
    flash = TimelineItem(
        track_type="video", track_index=3, start_frame=0, end_frame=2,
        duration_frames=2, source_start_frame=0, source_end_frame=2,
        source_file="/m/a.MXF", speaker="Akshita", name="yeah.")
    timeline = ReelTimeline(
        reel_name="Reel 19 - can-you-game-ai", fps=FPS,
        total_frames=int(round(30.0 * FPS)),
        video_items=(picture,), audio_items=(picture,), caption_items=(flash,))
    result = verify_reel(plan, timeline)
    f7 = [f for f in result.findings
          if f.finding_class == FindingClass.F7
          and f.severity == "error"]
    assert len(f7) == 1, [f.message for f in result.findings]
    assert f7[0].detail["duration_frames"] == 2
