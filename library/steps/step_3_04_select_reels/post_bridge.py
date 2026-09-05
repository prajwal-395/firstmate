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
        # Out to whole segments: a short must not open or close
        # mid-sentence, and 63 of this episode's 906 segments straddle a
        # cut and are excluded from the arithmetic entirely.
        start, end = snap_to_speech(start, end, transcript)
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

        # Overlap check against already accepted moments
        overlap_idx = None
        overlap_reason = None
        for i, existing in enumerate(moments):
            overlap_start = max(enriched.timeline_start, existing.timeline_start)
            overlap_end = min(enriched.timeline_end, existing.timeline_end)
            overlap_dur = overlap_end - overlap_start
            if overlap_dur >= 1.0:
                overlap_idx = i
                overlap_reason = (
                    f"overlaps {existing.timeline_name} ({existing.timeline_start:.1f}-{existing.timeline_end:.1f}s) "
                    f"by {overlap_dur:.1f}s. Two reels cannot share the same conversation."
                )
                break

        if overlap_idx is not None:
            existing = moments[overlap_idx]
            if enriched.duration > existing.duration:
                dropped.append({
                    "entry": existing.as_dict(),
                    "reason": (
                        f"overlaps longer/better-formed candidate {enriched.slug} "
                        f"({enriched.timeline_start:.1f}-{enriched.timeline_end:.1f}s) "
                        f"by {min(enriched.timeline_end, existing.timeline_end) - max(enriched.timeline_start, existing.timeline_start):.1f}s. "
                        f"Two reels cannot share the same conversation."
                    )
                })
                moments[overlap_idx] = enriched
            else:
                dropped.append({"entry": entry, "reason": overlap_reason})
            continue

        moments.append(enriched)

    from dataclasses import replace
    moments = [replace(m, number=idx) for idx, m in enumerate(moments, 1)]

    if moments:
        # The fallback duration is the furthest second any moment plays,
        # which is not always a body end: a closer may come from later in
        # the episode than every body, and a timeline shorter than the
        # material would refuse it as outside the timeline.
        furthest = max(
            max(m.timeline_end,
                m.call_to_action.timeline_end if m.call_to_action else 0.0)
            for m in moments)
        validate_proposal(moments, transcript, duration or furthest)

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

    return {
        "reel_selection": {
            "moments": [m.as_dict() for m in moments],
            "considered": considered,
            "undetermined": undetermined,
            "dropped": dropped,
            "approval": (
                "every moment is PROPOSED. Nothing is built until the "
                "captain approves it - see reel_proposal.assert_approved."
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
