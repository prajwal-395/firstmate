#!/usr/bin/env python3
"""Step 3.4 post-bridge: the model's chosen reels, CHECKED against the cut.

What is checked here is that every claim the answer makes is about the
material: the span is inside the timeline, it is not empty, real speech
was measured inside it, and it does not cut a transcript segment in half
so the short would open or close mid-sentence.

What is NOT checked is whether the choice is any good. Which stretches
are worth a short is the model's judgement and this file has no opinion
about it - no count is required, no length is enforced, and a moment
carrying a call to action is not treated as a defect.

A moment may also name a `cta` - the spoken closer it ends on, taken from
ANYWHERE in the episode, which the reel plays after its body. It gets the
same treatment the body span gets and no more: snapped out to whole
segments, then checked to be real seconds somebody really speaks in.
WHICH passage is a good closer, and which reel it suits, is the model's
call; nothing here scores one, ranks them, or prefers the nearest.
Several moments naming the SAME cta is expected rather than a defect -
this episode says about six calls to action and the format asks every
reel to close on one.

Every moment comes out PROPOSED. The captain approves before anything is
built, and `reel_proposal.assert_approved` is the gate that holds it.
"""

from __future__ import annotations

import sys
from typing import List


def _call_to_action(entry: dict, body_start: float, body_end: float,
                    transcript: dict):
    """The closer this moment ends on, or (None, None) if it names none.

    Returns `(cta, drop_reason)`. A moment that names no `cta` gets None
    and no reason - a reel with no declared closer ends where its body
    ends, and nothing here supplies one. Never author, template, pad or
    synthesise a call to action: every second of a closer is speech the
    episode really contains, and `validate_proposal` refuses a range
    where the transcript measured nothing said.

    A `cta` sitting INSIDE this moment's own body is a bad PICK rather
    than an invented timecode - the model told the truth about the
    seconds, it just named a closer the reel already plays - so the
    moment is dropped with a reason instead of raising and taking the
    whole batch with it.
    """
    from library.tools.reel_proposal import CallToAction, snap_to_speech

    raw = entry.get("cta") or entry.get("call_to_action")
    if not raw:
        return None, None
    try:
        cta_start = float(raw["start"])
        cta_end = float(raw["end"])
    except (KeyError, TypeError, ValueError):
        return None, ("names a cta with no usable start/end. A closer is "
                      "a real span of the episode or it is nothing.")

    cta_start, cta_end = snap_to_speech(cta_start, cta_end, transcript)
    if min(cta_end, body_end) > max(cta_start, body_start):
        return None, (
            f"its cta ({cta_start:.1f}-{cta_end:.1f}s) sits inside its own "
            f"body ({body_start:.1f}-{body_end:.1f}s), so the reel would "
            f"play those seconds twice. Name a closer from elsewhere in "
            f"the episode, or none.")
    return CallToAction(
        timeline_start=cta_start,
        timeline_end=cta_end,
        note=str(raw.get("note") or raw.get("reason") or "").strip(),
    ), None


def resolve(llm_output: dict, data: dict) -> dict:
    from library.tools.reel_proposal import (
        Approval, ReelMoment, enrich, is_conversation,
        overlaps_picture_hole, slugify, snap_to_speech,
        validate_proposal)

    transcript = data.get("timeline_transcript") or {}
    duration = float((transcript.get("derived_from") or {})
                     .get("duration_seconds") or 0.0)
    # Keep exclusions the captain recorded
    # (`library/tools/transcript_corrections.py`) are enforced HERE, on
    # every regenerated proposal, so a struck fragment stays out of the
    # reel no matter how often selection re-runs. An exclusion at a
    # moment's edge trims it; one in its middle drops the moment with
    # the reason - splitting one reel into two would be a new editorial
    # decision, and this step never makes one.
    project_folder = data.get("project_folder") or ""
    keep_exclusions: list = []
    if project_folder:
        try:
            from library.tools import transcript_corrections as _tc
            keep_exclusions = _tc.keep_exclusions(project_folder)
        except Exception:  # noqa: BLE001 - exclusions never break selection
            keep_exclusions = []
    chosen = (
        llm_output.get("moments")
        or llm_output.get("reels")
        or (llm_output.get("reel_selection") or {}).get("moments")
        or []
    )

    moments: List[ReelMoment] = []
    dropped: List[dict] = []
    for index, entry in enumerate(chosen, 1):
        try:
            start = float(entry["start"])
            end = float(entry["end"])
        except (KeyError, TypeError, ValueError):
            dropped.append({"entry": entry,
                            "reason": "no usable start/end"})
            continue
        # Out to whole segments, then out of any word interior: a short
        # must not open or close mid-sentence, and 63 of this episode's
        # 906 segments straddle a cut and are excluded from the segment
        # arithmetic entirely - while their WORDS still constrain the
        # boundary (`reel_proposal.snap_to_speech`).
        start, end = snap_to_speech(start, end, transcript)
        if keep_exclusions:
            from library.tools import transcript_corrections as _tc
            from library.tools.reel_proposal import bound_segments
            grid = [(float(s.get("timeline_start") or 0.0),
                     float(s.get("timeline_end") or 0.0))
                    for s in bound_segments(transcript)]
            kept, struck = _tc.apply_keep_exclusions(
                [{"start": start, "end": end, "entry": entry}],
                keep_exclusions, segments=grid)
            if struck:
                dropped.append({"entry": entry,
                                "reason": struck[0]["reason"]})
                continue
            start, end = kept[0]["start"], kept[0]["end"]
            entry = dict(entry, start=start, end=end,
                         trimmed_by=kept[0].get("trimmed_by", [])) \
                if kept[0].get("trimmed_by") else entry
        cta, cta_dropped = _call_to_action(entry, start, end, transcript)
        if cta_dropped:
            dropped.append({"entry": entry, "reason": cta_dropped})
            continue
        enriched = enrich(ReelMoment(
            number=index,
            slug=slugify(entry.get("slug") or entry.get("title") or ""),
            reason=str(entry.get("reason") or "").strip(),
            timeline_start=start,
            timeline_end=end,
            call_to_action=cta,
            approval=Approval.PROPOSED,
        ), transcript)
        # Drop bad PICKS, not integrity failures. The model told the
        # truth about the timecode; it just made a pick it could not
        # know was bad. Raising would discard the whole batch.
        hole_reason = overlaps_picture_hole(enriched, transcript)
        if hole_reason:
            dropped.append({"entry": entry, "reason": hole_reason})
            continue
        not_convo = is_conversation(enriched, transcript)
        if not_convo:
            dropped.append({"entry": entry, "reason": not_convo})
            continue

        for i, existing in enumerate(moments):
            overlap_start = max(enriched.timeline_start, existing.timeline_start)
            overlap_end = min(enriched.timeline_end, existing.timeline_end)
            overlap_dur = overlap_end - overlap_start
            if overlap_dur >= 1.0:
                from dataclasses import replace
                warning1 = f" [OVERLAP: shares {overlap_dur:.1f}s ({overlap_start:.1f}-{overlap_end:.1f}s) with {existing.slug}]"
                enriched = replace(enriched, reason=enriched.reason + warning1)
                warning2 = f" [OVERLAP: shares {overlap_dur:.1f}s ({overlap_start:.1f}-{overlap_end:.1f}s) with {enriched.slug}]"
                moments[i] = replace(existing, reason=existing.reason + warning2)

        moments.append(enriched)

    from dataclasses import replace
    moments = [replace(m, number=idx) for idx, m in enumerate(moments, 1)]

    # The captain's recorded closer pins
    # (`library/tools/captain_edits.py`, `redraw_closer`): a shared
    # closer the captain ruled must open on earlier words is redrawn on
    # every regenerated proposal, so selection cannot re-emit the old
    # start. Applied after enrich (identification reads the closer's
    # measured opening words) and before validation (the redrawn span
    # is checked like a new span). An unappliable pin is reported,
    # never silent - and never fatal to the batch: a reel the pin
    # cannot reach keeps its span with the reason.
    closer_redraws = {"applied": [], "held": [], "stale": []}
    if project_folder:
        try:
            from library.tools import captain_edits as _edits
            _all = _edits.load_edits(project_folder)
            if any(e.get("kind") == "redraw_closer" for e in _all):
                moments, _applied, _held, _stale = \
                    _edits.apply_closer_redraws(moments, transcript, _all)
                closer_redraws = {"applied": _applied, "held": _held,
                                  "stale": _stale}
                for record in _applied:
                    print(
                        f"  Captain edit: reel {record['reel']}'s closer "
                        f"{record['was'][0]:.3f}-{record['was'][1]:.3f}s "
                        f"now opens on {record['anchor_phrase']!r} "
                        f"({record['now'][0]:.3f}s) - "
                        f"{record['reason']}",
                        file=sys.stderr)
                for record in _held:
                    print(
                        f"  Captain edit: reel {record['reel']}'s closer "
                        f"already opens on "
                        f"{record['anchor_phrase']!r} - pin held",
                        file=sys.stderr)
                if _stale:
                    _edits.report_stale(_stale)
        except Exception as exc:  # noqa: BLE001 - pins never break selection
            print(f"WARNING: closer pins could not apply ({exc}); "
                  f"continuing without them.", file=sys.stderr)

    if moments:
        # The fallback duration is the furthest second any moment plays,
        # which is not always a body end: a closer may come from later in
        # the episode than every body, and a timeline shorter than the
        # material would refuse it as outside the timeline.
        furthest = max(
            max(m.timeline_end,
                m.call_to_action.timeline_end if m.call_to_action else 0.0)
            for m in moments)
        # Reels a recorded pin just redrew carry the captain's own
        # word-edge guarantee instead of the whole-segments one: the
        # pin may open mid-row where the row is chunking, and
        # refusing it here would make a recorded decision break every
        # future regeneration (`reel_proposal.validate_proposal`).
        pinned = frozenset(int(r["reel"]) for r in closer_redraws["applied"])
        validate_proposal(moments, transcript, duration or furthest,
                          pinned_cta_reels=pinned)

    considered = (
        llm_output.get("considered")
        or (llm_output.get("reel_selection") or {}).get("considered")
        or []
    )
    undetermined = (
        llm_output.get("undetermined")
        or (llm_output.get("reel_selection") or {}).get("undetermined")
        or []
    )

    # WAS one project's absolute path, hardcoded: every project this
    # step ever ran for was told its transcript lived in
    # `lucie/geo-podcast`. The engine serves a daily channel and client
    # work (AGENTS.md 14), so a project's own path is not the engine's to
    # state - and the module that WRITES the file already knows where it
    # goes.
    from library.tools.timeline_transcript import transcript_path

    project_folder = data.get("project_folder") or ""
    where = (f" The FULL transcript these were chosen from is at: "
             f"{transcript_path(project_folder)}." if project_folder else "")

    return {
        "reel_selection": {
            "moments": [m.as_dict() for m in moments],
            "considered": considered,
            "undetermined": undetermined,
            "dropped": dropped,
            "captain_closer_redraws": closer_redraws,
            "approval": (
                "every moment is PROPOSED. Nothing is built until the "
                "captain approves it - see reel_proposal.assert_approved."
                + where
            ),
        }
    }


def main():
    import sys
    import json
    import traceback
    try:
        data = json.loads(sys.stdin.read())
        print(json.dumps(resolve(data, data)))
    except Exception as e:
        sys.stderr.write(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
