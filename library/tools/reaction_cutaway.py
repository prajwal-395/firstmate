"""Reaction cutaway selection: choose reaction shots from the project graph.

Consumes `project_graph.find_reaction_shots` - the graph lane's query for
shots where another person is visible while a given person speaks. This
is the measured reaction evidence: person tracks from M3b + resolve,
not keyword matching.

The selection rule
-----------------
- For each spine block with speech, find who speaks (the graph's
  `speaks_in` edges for that clip).
- For each speaking person, ask the graph for reaction shots: shots
  where another person is visible while that person speaks.
- A reaction shot qualifies when it is not already in the spine (a shot
  already placed is not a cutaway candidate) and the reacting person is
  not the speaker (a shot is not a reaction to itself).
- The reaction lands at the end of the speech moment it reacts to - the
  punchline point. A caller may state its own offset.

Every selection records its evidence: the speaking person, the reacting
person, the measured spans, and the timing.

This module imports nothing from steps or processes: it reads the
measurements a state carries, the same reader every consumer has.
"""
from __future__ import annotations

#: Where the reaction lands relative to the speech moment, in seconds.
#: Zero - the reaction comes in at the end of the speech it reacts to.
#: A caller may state its own offset; nothing here invents one.
REACTION_LAND_OFFSET = 0.0


def _source_to_timeline(source_seconds: float, block: dict) -> float:
    """Map a source-domain second to the timeline domain for this block."""
    src_start = float(block.get("source_start", 0.0))
    tl_start = float(block.get("timeline_start", 0.0))
    return tl_start + (source_seconds - src_start)


def _speech_moment(block: dict):
    """The timeline second where the block's speech lands, or None.

    The reaction point is the end of the block's last word - that is
    where the punchline lands. A block with no word timestamps has no
    speech moment and no reaction.
    """
    words = block.get("word_timestamps") or []
    ends = []
    for entry in words:
        if not isinstance(entry, dict):
            continue
        try:
            ends.append(float(entry["source_end"]))
        except (KeyError, TypeError, ValueError):
            continue
    if not ends:
        return None
    return _source_to_timeline(max(ends), block)


def _speaking_persons(graph: dict, clip_id: str) -> list:
    """Persons who speak in this clip, from the graph's speaks_in edges.

    Returns a list of (person_id, speech_start, speech_end) tuples.
    An empty list means the clip has no measured speaker - unmeasured,
    not silent.
    """
    persons = []
    shot_id = f"shot:{clip_id}"
    for edge in (graph.get("edges") or []):
        if not isinstance(edge, dict):
            continue
        if edge.get("type") != "speaks_in":
            continue
        if edge.get("to") != shot_id:
            continue
        person_id = str(edge.get("from", ""))
        if not person_id:
            continue
        payload = edge.get("payload") or {}
        start = payload.get("start", 0)
        end = payload.get("end", 0)
        persons.append((person_id, start, end))
    return persons


def select_reaction_cutaways(
    spine_blocks: list,
    graph: dict,
    reaction_offset: float = REACTION_LAND_OFFSET,
) -> list:
    """Select reaction cutaways for the sequence's speech moments.

    Args:
        spine_blocks: the spine blocks. Each block with word timestamps
            is a speech moment that can take a reaction cutaway.
        graph: the project graph, from project_graph.load_or_build.
        reaction_offset: seconds after the speech moment where the
            reaction lands. The default is the end of the speech moment
            itself; a project may state its own.

    Returns a list of reaction cutaway records, one per speech moment
    that has a qualifying reaction shot, each with:
        - speech_block_position, speech_clip_id: what is being reacted to
        - speaking_person_id: who is speaking
        - speech_timeline_start, speech_timeline_end: the speech span
        - reaction_clip_id, reacting_person_id: the reaction shot
        - reaction_source_start, reaction_source_end: the measured span
        - timing: speech_moment, reaction_land, offset_seconds
        - reason: why this is a reaction cutaway candidate
    """
    results = []
    spine_clips = {
        str(b.get("clip_id", ""))
        for b in spine_blocks or []
        if isinstance(b, dict)
    }

    for block in spine_blocks or []:
        if not isinstance(block, dict):
            continue

        speech_moment = _speech_moment(block)
        if speech_moment is None:
            continue

        speech_clip = str(block.get("clip_id", ""))
        if not speech_clip:
            continue

        try:
            tl_start = round(float(block.get("timeline_start", 0.0)), 3)
            tl_end = round(float(block.get("timeline_end", 0.0)), 3)
        except (TypeError, ValueError):
            continue

        speaking_persons = _speaking_persons(graph, speech_clip)
        if not speaking_persons:
            continue

        for person_id, _speech_start, _speech_end in speaking_persons:
            try:
                from library.tools import project_graph
                reactions = project_graph.find_reaction_shots(
                    graph, person_id, _speech_start)
            except Exception:
                continue

            for reaction in reactions or []:
                if not isinstance(reaction, dict):
                    continue
                reaction_shot = str(reaction.get("shot_id", ""))
                if not reaction_shot:
                    continue
                reaction_clip = reaction_shot.replace("shot:", "", 1)

                # A shot already in the spine is not a cutaway candidate
                if reaction_clip in spine_clips:
                    continue

                reacting_person = str(reaction.get("person_id", ""))
                if not reacting_person or reacting_person == person_id:
                    continue

                try:
                    reaction_start = round(
                        float(reaction.get("reaction_start", 0.0)), 3)
                    reaction_end = round(
                        float(reaction.get("reaction_end", 0.0)), 3)
                except (TypeError, ValueError):
                    continue

                reaction_land = round(speech_moment + reaction_offset, 3)

                results.append({
                    "speech_block_position": block.get("position"),
                    "speech_clip_id": speech_clip,
                    "speaking_person_id": person_id,
                    "speech_timeline_start": tl_start,
                    "speech_timeline_end": tl_end,
                    "reaction_clip_id": reaction_clip,
                    "reacting_person_id": reacting_person,
                    "reaction_source_start": reaction_start,
                    "reaction_source_end": reaction_end,
                    "timing": {
                        "speech_moment": round(speech_moment, 3),
                        "reaction_land": reaction_land,
                        "offset_seconds": reaction_offset,
                    },
                    "reason": (
                        f"{reacting_person} is visible in "
                        f"{reaction_clip} from {reaction_start:.3f}s to "
                        f"{reaction_end:.3f}s while {person_id} speaks - "
                        f"a reaction cutaway candidate"
                    ),
                })

    return results
