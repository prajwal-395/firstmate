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

Every moment comes out PROPOSED. The captain approves before anything is
built, and `reel_proposal.assert_approved` is the gate that holds it.
"""

from __future__ import annotations

from typing import List


def resolve(llm_output: dict, data: dict) -> dict:
    from library.tools.reel_proposal import (
        Approval, ReelMoment, enrich, is_conversation,
        overlaps_picture_hole, slugify, snap_to_speech,
        validate_proposal)

    transcript = data.get("timeline_transcript") or {}
    duration = float((transcript.get("derived_from") or {})
                     .get("duration_seconds") or 0.0)
    chosen = llm_output.get("moments") or llm_output.get("reels") or []

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
        enriched = enrich(ReelMoment(
            number=index,
            slug=slugify(entry.get("slug") or entry.get("title") or ""),
            reason=str(entry.get("reason") or "").strip(),
            timeline_start=start,
            timeline_end=end,
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
        moments.append(enriched)

    if moments:
        validate_proposal(moments, transcript, duration or max(
            m.timeline_end for m in moments))

    return {
        "reel_selection": {
            "moments": [m.as_dict() for m in moments],
            "considered": llm_output.get("considered") or [],
            "undetermined": llm_output.get("undetermined") or [],
            "dropped": dropped,
            "approval": (
                "every moment is PROPOSED. Nothing is built until the "
                "captain approves it - see reel_proposal.assert_approved."
            ),
        }
    }
