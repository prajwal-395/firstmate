"""Finding 21: every caption segment lands on the declared row.

Scout B4/B2: one caption segment per build sat at Tilt 0, inside the
picture ("today is march" mid-frame at 288-324), while every other
segment rode its tight-box placement to the lower letterbox row
(Tilt -870). The placement chain has exactly one shape that ships
Tilt 0 silently: a TIGHT canvas reaching the placer with no
`tight_box.placement`, where `placement=None` reads as "full canvas,
nothing to do" and the clip sits centred.

The fix closes that shape at both ends:

- compile_manifest refuses a declared-tight segment with no placement,
  naming it (a render-side programming error - re-running the build
  cannot fix what the render did not record);
- the build refuses such a segment by name instead - skipped, warned,
  counted nowhere - so whatever arrives some other way (stale state,
  a hand edit) can never centre a caption mutely again.

Full-canvas segments draw their text natively and ride
untransformed, exactly as before.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.steps.step_6_01_render.resolve_build_timeline import (  # noqa: E402
    caption_segment_placement,
)
from library.steps.step_5_04_compile_manifest.step import (  # noqa: E402
    _assert_subtitle_overlay_matches_plan,
)


def _seg(segment_id="sub_hook_abc123", geometry="tight",
         placement=None, **extra):
    seg = {"segment_id": segment_id, "geometry": geometry,
           "overlay_path": f"/tmp/{segment_id}.mov",
           "timeline_start": 0.0, "timeline_end": 2.0,
           "block_position": 1}
    if placement is not None or geometry == "tight":
        seg["tight_box"] = ({"width": 480, "height": 120,
                             "placement": placement}
                            if placement is not None else None)
    seg.update(extra)
    return seg


_PLACEMENT = {"scaling": 1, "pan": 0.0, "tilt": -870.0}


# ── the build guard ──────────────────────────────────────────────

def test_the_build_guard_never_centres_a_caption_silently():
    """Finding 21's shape - a tight canvas with no placement - is refused
    by name rather than shipped at a silent Tilt 0; full canvas rides
    untransformed; a segment too old to declare its geometry rides its
    placement if it has one and is refused if it has none."""
    placement, refusal = caption_segment_placement(
        _seg(placement=_PLACEMENT), 0, 3)
    assert (placement, refusal) == (_PLACEMENT, "")

    seg = _seg(segment_id="sub_today_is_march_9f2c", placement=None)
    assert seg.get("tight_box") is None
    placement, refusal = caption_segment_placement(seg, 7, 3)
    assert placement is None
    assert "sub_today_is_march_9f2c" in refusal
    assert "Tilt 0" in refusal

    placement, refusal = caption_segment_placement(
        _seg(geometry="full", placement=None), 0, 3)
    assert (placement, refusal) == (None, "")

    seg = _seg(placement=_PLACEMENT)
    del seg["geometry"]
    assert caption_segment_placement(seg, 0, 3) == (_PLACEMENT, "")
    seg = _seg(placement=None)
    del seg["geometry"]
    seg.pop("tight_box", None)
    placement, refusal = caption_segment_placement(seg, 0, 3)
    assert placement is None and refusal != ""


# ── the compile refusal ──────────────────────────────────────────

def _manifest(*segments):
    subs = [{"spine_block_position": 1, "timeline_start": 0.0,
             "timeline_end": 2.0}]
    return {"subtitles": subs,
            "project": {"frame_rate": 30.0},
            "subtitle_overlay": {"segments": list(segments)}}


def test_compile_refuses_only_a_tight_segment_with_no_placement():
    import pytest
    seg = _seg(segment_id="sub_today_is_march_9f2c", placement=None)
    with pytest.raises(ValueError, match="sub_today_is_march_9f2c"):
        _assert_subtitle_overlay_matches_plan(_manifest(seg))
    _assert_subtitle_overlay_matches_plan(
        _manifest(_seg(geometry="full", placement=None)))
    _assert_subtitle_overlay_matches_plan(
        _manifest(_seg(placement=_PLACEMENT)))
