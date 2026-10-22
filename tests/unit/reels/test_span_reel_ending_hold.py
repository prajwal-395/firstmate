"""A span reel's declared FREEZE is part of the plan the gate grades.

The defect this names
---------------------
The reel-13 build on the Ren-owned scratch copy (2026-10-21) carried a
`full_frame_span`: the span IS the reel's picture for its whole body,
so `reel_build` suppresses the footage video beneath it.

The conformance gate RE-DERIVES the plan it grades against, and for a
span reel it emptied the picture plan - the suppressed footage is not
on the timeline, so expecting it would report every span reel as
"planned N items, found 0". But the declared FREEZE is planned FROM
those very placements (`reel_ending.plan_freeze` holds the last frame
that plays) and the build places that hold even under a span - the
span's own suppression exempts it.

So the re-derived plan dropped the hold: the plan laid 1912 frames
where the built picture carried 1931 (the body plus the 19-frame
hold), and PLAN-MISMATCH refused a correct build for 19 frames nobody
had dropped. A gate that fails correct output is no more coverage than
one that cannot fail (AGENTS.md 10.4).

What this file proves
---------------------
- a span reel's re-derived plan COUNTS the declared freeze hold;
- the plan it derives is the plan the build laid, so the gate's own
  frame-exact comparison finds no mismatch.
"""
from __future__ import annotations

import pytest
import yaml

from library.tools import reel_clock
from library.tools import reel_conformance_verifier as verifier

FPS = 24000 / 1001
BODY = (10.0, 20.0)
CTA = (468.0, 476.0)


def _master():
    """A master with continuous picture over the reel's body."""
    from library.tools.timeline_ingest import TimelineClip, TimelineSnapshot

    clip = TimelineClip(
        resolve_item_id="uid-1", track_type="video", track_index=1,
        track_name="V1", speaker="Akshita", source_file="/master/LC4932.MXF",
        source_in=0.0, source_out=600.0, source_in_frame=0,
        source_out_frame=int(600 * FPS), source_frames=200000,
        timeline_start=0.0, timeline_end=600.0, name="LC4932.MXF")
    return TimelineSnapshot(
        project_name="P", timeline_name="Master", fps=FPS, reported_fps=24.0,
        width=3840, height=2160, start_frame=0,
        end_frame=int(600 * FPS), clips=(clip,))


def _transcript():
    """The body's own words, and the CTA's, so the reel inherits an
    ending from its call to action."""
    body_words = [{"word": token, "start": BODY[0] + index * 1.0,
                   "end": BODY[0] + index * 1.0 + 0.8, "timed": True}
                  for index, token in enumerate(
                      ("search", "didnt", "change"))]
    words = [{"word": token, "start": 468.0 + index * 0.5,
              "end": 468.0 + index * 0.5 + 0.4, "timed": True}
             for index, token in enumerate(
                 ("check", "it", "out", "now"))]
    return {"segments": [
        {"timeline_start": BODY[0], "timeline_end": BODY[1],
         "source_file": "/master/LC4932.MXF", "source_start": BODY[0],
         "source_end": BODY[1], "speaker": "Akshita",
         "text": "search didnt change", "words": body_words},
        {"timeline_start": 468.0, "timeline_end": 476.0,
         "source_file": "/master/LC4932.MXF", "source_start": 468.0,
         "source_end": 476.0, "speaker": "Akshita", "text": "check it out now",
         "words": words},
    ]}


def _moment():
    from library.tools.reel_proposal import CallToAction, ReelMoment

    return ReelMoment(
        number=13, slug="breaktest-selected", reason="a complete exchange",
        timeline_start=BODY[0], timeline_end=BODY[1],
        speakers=("Akshita",),
        call_to_action=CallToAction(timeline_start=CTA[0],
                                    timeline_end=CTA[1]))


def _project(tmp_path):
    """A project whose picture IS a span over the reel's body."""
    declaration = {
        "effect": {"full_frame_elements": [{
            "element": "full_frame_span",
            "placement": "span",
            "background": "#0B1B2B",
            "entrance": "draw",
            "exit": "fade",
            "font_family": "Montserrat",
            "word_sync": True,
            "segments": [{
                "window": [BODY[0], BODY[1]],
                "runs": [{"bind": "range_line", "type_role": "display",
                          "colour": "#FFFFFF"}],
            }],
        }]},
    }
    (tmp_path / "project.yaml").write_text(
        yaml.safe_dump(declaration), encoding="utf-8")
    return str(tmp_path)


def _plan(project_folder):
    from library.tools.reel_conformance_verifier import (
        _derive_plan_from_master)

    return _derive_plan_from_master(
        "Reel 13 - breaktest-selected", 13, BODY[0], BODY[1],
        _master(), _transcript(), moment=_moment(),
        project_folder=project_folder)


def test_a_span_reels_plan_counts_the_declared_freeze_hold(tmp_path):
    """THE DEFECT.  The hold is placed under the span, so the plan the
    gate grades against has to count the frames it occupies - or the
    gate refuses a correct build for frames nobody dropped."""
    from library.tools import reel_ending

    project_folder = _project(tmp_path)
    plan = _plan(project_folder)

    ending = reel_ending.resolve_ending(
        project_folder, plan.reel_name, _moment(), _transcript())
    assert ending is not None, "the reel inherits an ending from its CTA"
    hold_frames = reel_ending.ending_tail_frames(ending, None)

    assert hold_frames > 0, "the inherited ending holds a frame"
    assert plan.held_frames == hold_frames, (
        "the re-derived plan counts the declared hold, which the build "
        "places even under a span")


def test_the_span_reels_plan_describes_the_picture_the_build_places(tmp_path):
    """The gate's own frame-exact comparison: the re-derived plan and the
    built picture agree, so PLAN-MISMATCH stays quiet."""
    from library.tools import reel_ending

    project_folder = _project(tmp_path)
    plan = _plan(project_folder)
    ending = reel_ending.resolve_ending(
        project_folder, plan.reel_name, _moment(), _transcript())
    hold_frames = reel_ending.ending_tail_frames(ending, None)

    built_picture_frames = (
        reel_clock.total_played_frames(plan.keep_ranges, FPS)
        + hold_frames)

    findings = verifier.check_plan_describes_timeline(
        plan.reel_name, plan.keep_ranges, built_picture_frames, FPS,
        card_frames=0, held_frames=plan.held_frames,
        tail_frames=plan.tail_frames)

    assert findings == [], (
        "the plan the gate grades against is the plan the build laid")


def test_the_span_reels_plan_counts_the_hold_as_a_picture_item(tmp_path):
    """THE DEFECT (the second half).  The hold is a picture item the
    build PLACES - even under a span, whose own suppression exempts it
    - so a plan that leaves it out reports the reel as "planned 0
    picture items, found 1" (F4) and the hold's 19 frames as video no
    speaker in the plan owns."""
    plan = _plan(_project(tmp_path))

    assert len(plan.placements) == 1, (
        "the declared hold is the one picture item a span reel places")
    assert plan.placements[0].speaker == "Akshita", (
        "the hold holds the ending shot, so it carries that shot's speaker")
