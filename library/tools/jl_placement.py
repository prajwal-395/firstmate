"""J/L-cut placement: propose where the audio can cross at a speech boundary.

A J cut lets the ear cross before the picture; an L cut lets the old
audio linger past it. Both need ROOM on the timeline for the audio to
move into: a J cut reaches back into the outgoing block's trailing
pause, an L cut reaches forward into the incoming block's leading
pause. This module measures those pauses from the spine's word
timestamps and proposes a placement the measurement can actually
support - no invented offsets, no cut that would trim speech.

What counts as a speaker change here
------------------------------------
Speaker identity is not a measurement this engine takes (the semantic
analysis deliberately does not attribute utterances to persons). What
IS measured is the cut: a speech-to-speech boundary where the clip
changes is where the picture cuts between sources - the visible mark
of a speaker change or a new angle on one. Placement is proposed at
those boundaries and only there; a boundary inside one clip is the
same source continuing and gets nothing.

The rule, stated
----------------
- Both sides must carry speech: a J/L cut is an audio handoff between
  two speakers, so a boundary with a non-speech side is skipped.
- A block with no SOURCE range has no word edge: the spine contract
  allows a non-speech block to carry `source_start`/`source_end` as
  None, and a word span is anchored to the timeline through that
  range. Such a block carries no word spans, so it sizes no pause and
  no placement is proposed across it.
- The outgoing block's trailing pause (its end minus its last word)
  sizes a J cut; the incoming block's leading pause (its first word
  minus its start) sizes an L cut. The offset IS the measured pause -
  the most room the measurement supports; the plan's model may pull it
  tighter, never past the word.
- When both sides pause, the longer pause wins: the cut takes the
  side with more measured room. When only one side pauses, that is the
  kind. When neither pauses, there is no room and no placement - a cut
  there would trim speech, which `jl_cut` refuses.

Addressing matches `jl_cut` itself: the boundary is named by the
INCOMING block's position (`cut_point_position`), so a plan entry and
a placement finding address the same join.

This module imports nothing from steps or processes: it reads the
measurements a state carries, the same reader every consumer has.
"""
from __future__ import annotations

from library.tools.jl_cut import JL_KINDS


def _fmt_pause(value) -> str:
    """A measured pause for the record, or 'none' when there is not one."""
    return f"{value:.3f}s" if value is not None else "none"


def _block_source_range(block: dict):
    """The block's source range as (start, end), or None when it has none.

    The spine contract lets a NON-SPEECH block carry `source_start` and
    `source_end` as None - the key is present and the value is None,
    which is legal (`library/tools/spine_contract.py`). A word span is
    placed on the timeline THROUGH that range, so a block with no
    source range has no word spans at all: there is no anchor to put a
    word on the timeline with, and reading the None as 0.0 would place
    every word at the block's own start - a number that is not a
    measurement.
    """
    start = block.get("source_start")
    end = block.get("source_end")
    if start is None or end is None:
        return None
    try:
        return float(start), float(end)
    except (TypeError, ValueError):
        return None


def _block_clip_id(block: dict):
    """The block's source clip, or None when it names no clip.

    Same shape as the source range: a non-speech block carries
    `clip_id: None`. `str(None)` is the four-letter word "None", which
    is TRUTHY, so a slot with no clip read as a clip called "None" -
    and a cut was offered across it.
    """
    clip_id = block.get("clip_id")
    if clip_id is None:
        return None
    clip_id = str(clip_id).strip()
    return clip_id or None


def _block_words_timeline(block: dict) -> list:
    """The block's word spans in TIMELINE seconds, as (start, end) pairs.

    A block with no source range has no word spans (see
    `_block_source_range`), so it has no word edge to cut behind.
    """
    spans = []
    source_range = _block_source_range(block)
    if source_range is None:
        return spans
    src_start = source_range[0]
    tl_start = float(block.get("timeline_start", 0.0))
    for entry in block.get("word_timestamps") or []:
        if not isinstance(entry, dict):
            continue
        try:
            start = float(entry["source_start"])
            end = float(entry["source_end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end > start:
            spans.append((tl_start + (start - src_start),
                          tl_start + (end - src_start)))
    return spans


def _trailing_pause(block: dict):
    """Seconds between the block's last word and its end, or None.

    None when the block carries no speech - a non-speech block has no
    word edge to cut behind.
    """
    spans = _block_words_timeline(block)
    if not spans:
        return None
    tl_end = float(block.get("timeline_end", 0.0))
    return round(tl_end - spans[-1][1], 3)


def _leading_pause(block: dict):
    """Seconds between the block's start and its first word, or None."""
    spans = _block_words_timeline(block)
    if not spans:
        return None
    tl_start = float(block.get("timeline_start", 0.0))
    return round(spans[0][0] - tl_start, 3)


def find_jl_placements(spine_blocks: list) -> list:
    """Propose J/L-cut placements at the sequence's speech boundaries.

    Args:
        spine_blocks: the spine blocks, each with clip_id,
            source_start/source_end, timeline_start/timeline_end and
            word_timestamps.

    Returns a list of placement records, one per speech-to-speech
    boundary where the clip changes and the measured pauses leave room
    for the audio to cross, each with:
        - cut_point_position: the incoming block's position (jl_cut's
          own addressing)
        - timeline_position: where the boundary sits on the timeline
        - outgoing_clip_id, incoming_clip_id: the sources it joins
        - kind: j_cut or l_cut
        - offset_seconds: the measured pause the cut fits into
        - outgoing_trailing_pause, incoming_leading_pause: the
          measurements the choice rested on
        - reason: why this kind at this boundary
    """
    placements = []

    blocks = [b for b in spine_blocks or [] if isinstance(b, dict)]
    for outgoing, incoming in zip(blocks, blocks[1:]):
        outgoing_clip = _block_clip_id(outgoing)
        incoming_clip = _block_clip_id(incoming)
        if not outgoing_clip or not incoming_clip:
            continue
        if outgoing_clip == incoming_clip:
            continue

        trailing = _trailing_pause(outgoing)
        leading = _leading_pause(incoming)
        # A speaker change is an audio handoff between two speakers: both
        # sides must carry speech. A zero pause is no room - a cut sized
        # to it would trim speech, which jl_cut refuses.
        if trailing is None or leading is None:
            continue
        if not trailing and not leading:
            continue

        try:
            position = incoming.get("position")
            timeline_position = round(
                float(incoming.get("timeline_start", 0.0)), 3)
        except (TypeError, ValueError):
            continue

        if trailing and (not leading or trailing >= leading):
            kind = "j_cut"
            offset = trailing
            if not leading:
                rationale = ("the incoming block's first word runs to "
                             "its start, so the only measured room is "
                             "the outgoing trailing pause and the ear "
                             "crosses early")
            elif trailing > leading:
                rationale = ("the outgoing trailing pause is the longer "
                             "measured room, so the ear crosses early")
            else:
                rationale = ("both pauses measure the same, so the J "
                             "cut is proposed and the L cut is equally "
                             "supported")
        elif leading:
            kind = "l_cut"
            offset = leading
            if not trailing:
                rationale = ("the outgoing block's last word runs to "
                             "its end, so the only measured room is "
                             "the incoming leading pause and the old "
                             "audio lingers")
            else:
                rationale = ("the incoming leading pause is the longer "
                             "measured room, so the old audio lingers "
                             "past the picture cut")
        else:
            continue

        reason = (
            f"outgoing trailing pause {_fmt_pause(trailing)}, incoming "
            f"leading pause {_fmt_pause(leading)}: {rationale}"
        )

        placements.append({
            "cut_point_position": position,
            "timeline_position": timeline_position,
            "outgoing_clip_id": outgoing_clip,
            "incoming_clip_id": incoming_clip,
            "kind": kind,
            "offset_seconds": offset,
            "outgoing_trailing_pause": trailing,
            "incoming_leading_pause": leading,
            "reason": reason,
        })

    return placements


def placements_to_audio_offsets(placements: list, spine_blocks: list,
                                fps: float) -> list:
    """Convert J/L placement findings to audio_offset entries.

    Each placement proposes a J/L cut at a speech boundary. This
    converts them to the audio_offset format `apply_jl_cuts` consumes,
    so the measurement can become sound rather than only a report.

    The outgoing block's position is found from the spine: the
    placement addresses the incoming block, and the outgoing block is
    the one before it. A placement whose incoming block is the first
    block (no outgoing) is skipped.

    The audio cut is placed at the measured pause from the boundary:
    a J cut reaches back (audio before picture), an L cut forward
    (audio after picture). The offset IS the measured pause, so no
    number is invented here.

    Args:
        placements: the placement records from find_jl_placements.
        spine_blocks: the spine blocks, each with position,
            timeline_start/timeline_end.
        fps: the project frame rate.

    Returns a list of audio_offset entries, each with kind,
    picture_cut_timeline/frame, audio_cut_timeline/frame, lead_seconds,
    method, outgoing_position and incoming_position.
    """
    offsets = []
    blocks = [b for b in spine_blocks or [] if isinstance(b, dict)]

    for placement in placements or []:
        if not isinstance(placement, dict):
            continue
        incoming_pos = placement.get("cut_point_position")
        if incoming_pos is None:
            continue

        outgoing_pos = None
        for i, block in enumerate(blocks):
            if block.get("position") == incoming_pos:
                if i > 0:
                    outgoing_pos = blocks[i - 1].get("position")
                break
        if outgoing_pos is None:
            continue

        kind = placement.get("kind")
        if kind not in ("j_cut", "l_cut"):
            continue
        try:
            offset = float(placement.get("offset_seconds", 0.0))
            picture_cut = float(placement.get("timeline_position", 0.0))
        except (TypeError, ValueError):
            continue

        if kind == "j_cut":
            audio_cut = picture_cut - offset
        else:
            audio_cut = picture_cut + offset

        offsets.append({
            "kind": kind,
            "picture_cut_timeline": round(picture_cut, 3),
            "picture_cut_frame": int(round(picture_cut * fps)),
            "audio_cut_timeline": round(audio_cut, 3),
            "audio_cut_frame": int(round(audio_cut * fps)),
            "audio_cut_exact": round(audio_cut, 3),
            "lead_seconds": round(offset, 3),
            "method": "measured-pause-placement",
            "outgoing_position": outgoing_pos,
            "incoming_position": incoming_pos,
        })

    return offsets
