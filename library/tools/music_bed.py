"""The bed is a SEQUENCE, not a minute.

Captain, 2026-09-01, correcting a limit this pipeline had written up as a
good decision: *"its not one continuous stretch from the music we have to
use, like we can use bits and pieces, or multiple tracks, and splice
pieces from different tracks and all that, like i think u limited what the
music step is actually doing"*.  And: *"if you like two sections of a song
but they are disjoint, then you should still be able to use them"*.  And:
*"the prompt is telling us about how we should be trying to find
compositions and pieces to be able to properly conduct the music, not just
selecting a one minute stretch of music blindly"*.

Three ceilings, and none of them was in the prompt
--------------------------------------------------
Step 2.04's ``handoff.md`` has asked for splices since it was
written - *"a 3-minute track is never used in full; pick the sections that
fit particular moments"* - and for a *"single continuous section is as
valid as multiple splices"*.  The model has been answering.  What stopped
was everything below it:

1. **One section.**  ``music_section`` resolves ONE ``source_in`` and
   ``compile_manifest`` placed one clip there, so two disjoint sections of
   the same track were not expressible.
2. **One track.**  ``music_selection`` is a single object, so a second
   track had nowhere to be named at all.  A schema change, not a flag.
3. **No conducting.**  Nothing anywhere carried "this piece here, that
   piece there".

This module is the shape that carries all three, and
``music_section.py`` stays exactly what it was: the ONE-section reading,
which is still what a selection that declares no bed gets.

Where the two halves of the decision live, and why
--------------------------------------------------
**Which sections of which tracks are worth using** is step 2.04's, and it
is source-time only: ``splices``, each naming a track and a span of it.
2.04 runs before the spine exists, so it cannot name a timeline position
and is not asked to.

**Where each piece plays** is step 2.05 ``mesh_spine``'s, because that is
the first step that has the spine, the chosen tracks and the music
analysis together.  A segment is anchored to a SPINE BLOCK POSITION -
``starts_at_block`` - and runs until the next segment starts.

That anchor is not a taste ruling, it is what is representable
--------------------------------------------------------------
The open question was whether a splice boundary should land on a spine
BLOCK BOUNDARY or on a BEAT.  Investigating it settled it for today, and
the finding is recorded in :data:`THE_SNAP_QUESTION` rather than left as
folklore:

- The model at 2.05 is authoring block DURATIONS; the absolute timeline
  seconds are computed from them afterwards by that step's post-bridge,
  frame-snapped.  A boundary the model named in absolute seconds would
  drift against the spine it is writing in the same answer.
- ``mesh_spine``'s ``context_fields`` DROPS ``music_analysis.tempo.beats``
  and ``.downbeats``, because AGENTS.md 10.1 keeps raw value lists out of
  prompts.  The model there cannot see a beat time at all.

So a block position is the only boundary the answering step can name
without guessing.  Beat alignment is reachable and is not free: it needs a
derived table routed into 2.05 the way ``cuts_toon.beat_near_cut`` is
derived for 4.02, and a snap pass over the resolved bed.  Nothing here
snaps to anything - AGENTS.md 10.5 - so adding one later is additive and
costs no schema change.

The crossfade
-------------
**A splice with no declared crossfade is a hard splice, and that is the
absence of decoration rather than a choice of it** - the same reading
``transition_vocabulary.CUT_TYPES`` and ``series_look.NEUTRAL_CDL`` get.
No default length is invented here, because a crossfade length is a
creative number and inventing one is the defect this week has been
clearing.  A segment that wants one declares ``crossfade_seconds``.

A declared crossfade is delivered as a real OVERLAP: the outgoing segment
plays ``crossfade_seconds`` past the boundary while the incoming segment
plays from the boundary, and ``otio_mix`` ramps one down and the other up
across the overlap.  Two overlapping clips cannot share one Resolve audio
track, so the renderer allocates the bed across A2, A3, ... exactly the
way it already allocates overlapping SFX, and the SFX bucket starts above
whatever the bed used.

``tests/test_music_bed.py``.


Rules relocated from AGENTS.md 10.5
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.5 keeps the headline
and points here.

**The bed is a SEQUENCE, not one continuous minute of one track.**
One enumeration, `library/tools/music_bed.py`. Captain, 2026-09-01: *"we can use bits and pieces, or multiple tracks, and splice pieces from different tracks"*. `music_section` is unchanged and is still what a plan declaring no bed gets.
- **The decision has TWO halves and they live in two steps.** 2.04 chooses the PIECES, in source time only (`splices`, each naming a track; `tracks` names every chosen track beyond the primary) - it runs before the spine exists and cannot name a timeline position. 2.05 `mesh_spine` CONDUCTS them (`music_bed`), because it is the first step holding the spine, the tracks and the analysis together.
- **A segment is anchored to a SPINE BLOCK POSITION and runs until the next one comes in.** That is what is representable, not a taste ruling: the model at 2.05 is authoring block DURATIONS (absolute seconds are computed afterwards and frame-snapped), and 2.05's `context_fields` drops `tempo.beats`/`downbeats`, so it cannot see a beat time. `THE_SNAP_QUESTION` records what beat alignment would cost - a derived table routed in, the `cuts_toon.beat_near_cut` route, plus a snap pass. **Nothing snaps a boundary to anything.**
- **A splice with no declared `crossfade_seconds` is a HARD splice** - the absence of decoration, the `CUT_TYPES`/`NEUTRAL_CDL` reading - and no length is invented for one.
- **A declared crossfade is a real OVERLAP**: the outgoing piece plays past the boundary while the incoming one plays from it, `otio_mix.music_curve` ramps one down and the other up, and the renderer's `_allocate_audio_tracks` spreads the bed across A2, A3, ... exactly as it already does for SFX - with the SFX bucket starting above whatever the bed used. **A2's overlap check allows exactly the declared fade and nothing else**, in `compile_manifest` and in `manifest_validator`.
- **The beat grid has one offset PER SEGMENT.** `music_analysis` measures the PRIMARY track only, so a beat is on the timeline only where that file plays; `beat_positions`/`downbeat_positions` take the spine and the duration for this.
- `tests/test_music_bed.py`, `tests/test_spliced_bed_reaches_the_manifest.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from library.tools.music_section import (
    FIT_SLACK_SECONDS,
    MusicSectionError,
    read_section,
)

# The key the spine carries the RESOLVED bed under, and the key the model
# writes the unresolved one under.  Same word on purpose: 2.05's
# post-bridge resolves in place, and there is one name to grep for.
BED_KEY = "music_bed"

# The key step 2.04 carries additional tracks under.  The PRIMARY track
# stays where it has always been - `music_selection.audio_path` and the
# scalars beside it - so every reader that only ever wanted one track
# keeps working unchanged, and `music_analysis` keeps analysing the track
# it has always analysed.
TRACKS_KEY = "tracks"

THE_SNAP_QUESTION = (
    "Whether a splice boundary should be snapped to a spine block edge or "
    "to a beat is a taste question about how the edit feels, and nothing "
    "here answers it: a segment is anchored to the block position the "
    "model named and is moved by no rule. As routed today the answering "
    "step (2.05 mesh_spine) cannot see a beat time - AGENTS.md 10.1 drops "
    "tempo.beats and tempo.downbeats from its prompt - so beat alignment "
    "would need a derived table routed in, the way cuts_toon.beat_near_cut "
    "is derived for 4.02, plus a snap pass over the resolved bed. Both are "
    "additive to this shape and neither changes it."
)


class MusicBedError(MusicSectionError):
    """A declared bed cannot be played.

    A subclass of `MusicSectionError` on purpose: a bed is the ONE-section
    reading generalised, a refusal of one is a refusal of the other, and
    every catcher written before this module keeps working.
    """


@dataclass(frozen=True)
class BedSegment:
    """One piece of music, and where it plays.

    ``timeline_start``/``timeline_end`` are the span the segment is
    RESPONSIBLE for.  ``crossfade_out_seconds`` is how far past
    ``timeline_end`` this segment keeps playing so the next one can fade
    in over it; ``crossfade_in_seconds`` is the same number seen from the
    other side.  Both are 0.0 at a hard splice.
    """

    audio_path: str
    source_in: float
    timeline_start: float
    timeline_end: float
    starts_at_block: Any = None
    crossfade_in_seconds: float = 0.0
    crossfade_out_seconds: float = 0.0
    why: str = ""
    track_title: str = ""

    @property
    def played_seconds(self) -> float:
        """How long the file plays for, overlap included."""
        return round(self.timeline_end - self.timeline_start
                     + self.crossfade_out_seconds, 3)

    @property
    def source_out(self) -> float:
        return round(self.source_in + self.played_seconds, 3)

    @property
    def placed_start(self) -> float:
        """Where the clip is placed - the boundary, not the fade's start.

        The incoming segment starts AT the boundary and rises over the
        overlap; it is the OUTGOING one that runs long.  Placing the
        incoming early would play seconds of the file the model did not
        choose.
        """
        return self.timeline_start

    @property
    def placed_end(self) -> float:
        return round(self.timeline_end + self.crossfade_out_seconds, 3)


@dataclass(frozen=True)
class MusicBed:
    """The whole bed, and whether anybody conducted it."""

    segments: List[BedSegment] = field(default_factory=list)
    declared: bool = False
    reason: str = ""

    @property
    def tracks_used(self) -> List[str]:
        seen: List[str] = []
        for seg in self.segments:
            if seg.audio_path and seg.audio_path not in seen:
                seen.append(seg.audio_path)
        return seen

    @property
    def splice_count(self) -> int:
        return max(0, len(self.segments) - 1)


# ── The tracks a selection names ────────────────────────────────────

def track_table(music_selection: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Every track the selection chose, primary first.

    A selection that names only the primary gets a one-row table, which is
    what makes the single-track case the same code path rather than a
    special one.
    """
    if not isinstance(music_selection, dict):
        return []
    rows: List[Dict[str, Any]] = []
    primary = str(music_selection.get("audio_path") or "").strip()
    if primary:
        rows.append({
            "audio_path": primary,
            "title": str(music_selection.get("title") or "").strip(),
            "duration_seconds": music_selection.get("duration_seconds"),
            "role": "primary",
        })
    extra = music_selection.get(TRACKS_KEY)
    if isinstance(extra, list):
        for entry in extra:
            if not isinstance(entry, dict):
                raise MusicBedError(
                    f"music_selection.{TRACKS_KEY} must be a list of objects, "
                    f"each naming a track; got a {type(entry).__name__}")
            path = str(entry.get("audio_path") or "").strip()
            if not path:
                raise MusicBedError(
                    f"music_selection.{TRACKS_KEY} carries an entry with no "
                    f"audio_path. A track the bed can play has to name the "
                    f"file it plays from.")
            if any(r["audio_path"] == path for r in rows):
                continue
            rows.append({
                "audio_path": path,
                "title": str(entry.get("title") or "").strip(),
                "duration_seconds": entry.get("duration_seconds"),
                "role": "additional",
            })
    return rows


def resolve_track(reference: Any,
                  tracks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The track a segment names, matched EXACTLY on path or on title.

    No nearest match: a near match is a chooser (AGENTS.md 10.5, the same
    line `sfx_library.resolve_sfx_id` holds).  A segment naming nothing
    plays the primary, which is the single-track case.
    """
    if not tracks:
        raise MusicBedError(
            "the bed names a track but the selection chose none - "
            "music_selection.audio_path is empty and it declares no "
            f"{TRACKS_KEY}")
    ref = str(reference or "").strip()
    if not ref:
        return tracks[0]
    for row in tracks:
        if row["audio_path"] == ref:
            return row
    for row in tracks:
        if row["title"] and row["title"] == ref:
            return row
    known = ", ".join(sorted(
        {r["audio_path"] for r in tracks} | {r["title"] for r in tracks if r["title"]}))
    raise MusicBedError(
        f"the bed names track {ref!r}, which the selection did not choose. "
        f"Name one of: {known}. No nearest match is taken - choosing a "
        f"different track is a decision, not a repair.")


# ── Resolving a declared bed against the spine ──────────────────────

def _blocks(audio_spine: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    structure = (audio_spine or {}).get("structure")
    return [b for b in (structure or []) if isinstance(b, dict)]


def _block_index(blocks: List[Dict[str, Any]], position: Any) -> int:
    for index, block in enumerate(blocks):
        if str(block.get("position")) == str(position):
            return index
    known = ", ".join(str(b.get("position")) for b in blocks)
    raise MusicBedError(
        f"the bed starts a segment at block {position!r}, which is not a "
        f"position on the spine. The spine's blocks are: {known}")


def read_bed(music_selection: Optional[Dict[str, Any]],
             audio_spine: Optional[Dict[str, Any]],
             timeline_duration: float) -> MusicBed:
    """The bed the plan declared, resolved onto the timeline.

    A spine that declares no bed falls back to the ONE-section reading -
    ``music_section.read_section`` - which is what every run before this
    got and is the absence of a decision, not one.

    Raises on a bed that cannot be played.  A bed is the audible spine of
    the piece; a malformed one is a mistake to report, never one to read
    as "play from the head".
    """
    declared = (audio_spine or {}).get(BED_KEY)
    if not declared:
        section = read_section(music_selection)
        tracks = track_table(music_selection)
        path = tracks[0]["audio_path"] if tracks else ""
        reason = (
            section.reason if not section.declared else
            "the spine declares no bed layout, so the one chosen section "
            "plays under the whole video")
        if not path:
            return MusicBed(segments=[], declared=False,
                            reason="no music track was chosen")
        return MusicBed(
            segments=[BedSegment(
                audio_path=path,
                source_in=section.source_in,
                timeline_start=0.0,
                timeline_end=round(float(timeline_duration), 3),
                why=section.why,
                track_title=tracks[0]["title"],
            )],
            declared=False,
            reason=reason,
        )

    if not isinstance(declared, list):
        raise MusicBedError(
            f"audio_spine.{BED_KEY} must be an ordered list of segments; got "
            f"{type(declared).__name__}")

    blocks = _blocks(audio_spine)
    tracks = track_table(music_selection)
    if not tracks:
        raise MusicBedError(
            "the spine declares a bed but the selection chose no track to "
            "play it from")

    raw: List[Dict[str, Any]] = []
    for index, entry in enumerate(declared):
        if not isinstance(entry, dict):
            raise MusicBedError(
                f"audio_spine.{BED_KEY}[{index}] must be an object naming "
                f"source_in and starts_at_block; got "
                f"{type(entry).__name__}")
        source_in = entry.get("source_in")
        if isinstance(source_in, bool) or not isinstance(source_in, (int, float)):
            raise MusicBedError(
                f"audio_spine.{BED_KEY}[{index}].source_in must be a number "
                f"of seconds into the track; got {source_in!r}")
        if source_in < 0:
            raise MusicBedError(
                f"audio_spine.{BED_KEY}[{index}].source_in is {source_in}; a "
                f"segment cannot start before its file does")
        track = resolve_track(entry.get("track"), tracks)
        position = entry.get("starts_at_block")
        if index == 0:
            start_index = 0
            if position is not None and blocks:
                start_index = _block_index(blocks, position)
                if start_index != 0:
                    raise MusicBedError(
                        f"audio_spine.{BED_KEY}[0] starts at block "
                        f"{position!r}, which is not the first block. The "
                        f"bed's first segment starts the video; a hole at "
                        f"the head is a planned silence and is declared as "
                        f"music_behavior on the blocks it covers.")
        else:
            if position is None:
                raise MusicBedError(
                    f"audio_spine.{BED_KEY}[{index}] names no "
                    f"starts_at_block. Every segment after the first says "
                    f"which spine block it comes in on.")
            start_index = _block_index(blocks, position)
        crossfade = entry.get("crossfade_seconds")
        if crossfade is None:
            crossfade = 0.0
        elif isinstance(crossfade, bool) or not isinstance(crossfade, (int, float)):
            raise MusicBedError(
                f"audio_spine.{BED_KEY}[{index}].crossfade_seconds must be a "
                f"number of seconds; got {crossfade!r}. Omit it for a hard "
                f"splice - that is the absence of a crossfade, not a "
                f"request for a default one.")
        elif crossfade < 0:
            raise MusicBedError(
                f"audio_spine.{BED_KEY}[{index}].crossfade_seconds is "
                f"{crossfade}; a crossfade cannot be negative")
        raw.append({
            "audio_path": track["audio_path"],
            "track_title": track["title"],
            "source_in": round(float(source_in), 3),
            "start_index": start_index,
            "starts_at_block": position if index else (
                blocks[0].get("position") if blocks else None),
            "crossfade": round(float(crossfade), 3),
            "why": str(entry.get("why") or "").strip(),
        })

    for index in range(1, len(raw)):
        if raw[index]["start_index"] <= raw[index - 1]["start_index"]:
            raise MusicBedError(
                f"audio_spine.{BED_KEY} is out of order: segment {index} "
                f"comes in at block {raw[index]['starts_at_block']!r}, which "
                f"is at or before where segment {index - 1} came in. The bed "
                f"is an ordered sequence and nothing here reorders it.")

    total = round(float(timeline_duration), 3)
    segments: List[BedSegment] = []
    for index, row in enumerate(raw):
        start = 0.0 if index == 0 else _block_start(blocks, row["start_index"])
        if index + 1 < len(raw):
            end = _block_start(blocks, raw[index + 1]["start_index"])
            crossfade_out = raw[index + 1]["crossfade"]
        else:
            end = total
            crossfade_out = 0.0
        segments.append(BedSegment(
            audio_path=row["audio_path"],
            track_title=row["track_title"],
            source_in=row["source_in"],
            timeline_start=round(start, 3),
            timeline_end=round(end, 3),
            starts_at_block=row["starts_at_block"],
            crossfade_in_seconds=row["crossfade"],
            crossfade_out_seconds=crossfade_out,
            why=row["why"],
        ))

    return MusicBed(segments=segments, declared=True,
                    reason="the spine conducts the bed")


def _block_start(blocks: List[Dict[str, Any]], index: int) -> float:
    if not blocks:
        return 0.0
    block = blocks[min(index, len(blocks) - 1)]
    try:
        return float(block.get("timeline_start", 0.0) or 0.0)
    except (TypeError, ValueError):
        raise MusicBedError(
            f"spine block {block.get('position')!r} carries a "
            f"timeline_start that is not a number: "
            f"{block.get('timeline_start')!r}")


def validate_bed(bed: MusicBed,
                 durations_by_path: Dict[str, Optional[float]]) -> List[str]:
    """Everything wrong with a resolved bed, as sentences.

    Empty means playable.  ``durations_by_path`` is what each file really
    measures; a path with no measured duration is not checked against one
    rather than checked against a guess.
    """
    errors: List[str] = []
    for index, seg in enumerate(bed.segments):
        if seg.timeline_end <= seg.timeline_start:
            errors.append(
                f"bed segment {index} covers {seg.timeline_start:.3f}s to "
                f"{seg.timeline_end:.3f}s, which is no time at all")
        duration = durations_by_path.get(seg.audio_path)
        try:
            duration = None if duration is None else float(duration)
        except (TypeError, ValueError):
            duration = None
        if duration and duration > 0:
            if seg.source_in >= duration:
                errors.append(
                    f"bed segment {index} starts at {seg.source_in:.1f}s of "
                    f"a {duration:.1f}s track - after the file ends")
            elif seg.source_out > duration + FIT_SLACK_SECONDS:
                errors.append(
                    f"bed segment {index} asks for {seg.played_seconds:.1f}s "
                    f"from {seg.source_in:.1f}s of a {duration:.1f}s track, "
                    f"which runs "
                    f"{seg.source_out - duration:.1f}s past the end of the "
                    f"file - and those seconds would be silent. Start it no "
                    f"later than "
                    f"{duration - seg.played_seconds:.1f}s, or come in on a "
                    f"later block so the segment is shorter.")
        span = seg.timeline_end - seg.timeline_start
        if seg.crossfade_in_seconds > span + 1e-6:
            errors.append(
                f"bed segment {index} declares a "
                f"{seg.crossfade_in_seconds:.2f}s crossfade into a "
                f"{span:.2f}s segment - the fade would outlast the piece it "
                f"is fading into")
        if index and seg.crossfade_in_seconds:
            previous = bed.segments[index - 1]
            previous_span = previous.timeline_end - previous.timeline_start
            if seg.crossfade_in_seconds > previous_span + 1e-6:
                errors.append(
                    f"bed segment {index} declares a "
                    f"{seg.crossfade_in_seconds:.2f}s crossfade over a "
                    f"{previous_span:.2f}s outgoing segment")
    return errors


def resolve_bed(music_selection: Optional[Dict[str, Any]],
                audio_spine: Optional[Dict[str, Any]],
                timeline_duration: float,
                durations_by_path: Optional[Dict[str, Optional[float]]] = None
                ) -> MusicBed:
    """The bed to place, or a refusal saying why it cannot be played."""
    bed = read_bed(music_selection, audio_spine, timeline_duration)
    if durations_by_path is None:
        durations_by_path = {
            row["audio_path"]: row.get("duration_seconds")
            for row in track_table(music_selection)
        }
    errors = validate_bed(bed, durations_by_path)
    if errors:
        raise MusicBedError(
            "The planned music bed cannot be placed:\n"
            + "\n".join(f"  - {e}" for e in errors)
            + "\n  Re-run step 2.05 (mesh_spine) to conduct the bed onto "
              "sections that fit, or step 2.04 (music_selection) to choose "
              "longer material."
        )
    return bed


def bed_clips(bed: MusicBed, *, fps: float) -> List[Dict[str, Any]]:
    """The A2 clips a resolved bed becomes, in play order.

    Frames travel beside seconds because the renderer's track allocator
    reads frames, and a bed with a crossfade OVERLAPS - which is exactly
    the condition that allocator exists to spread across tracks.
    """
    clips: List[Dict[str, Any]] = []
    for index, seg in enumerate(bed.segments):
        start, end = seg.placed_start, seg.placed_end
        clips.append({
            "source_file": seg.audio_path,
            "source_in": seg.source_in,
            "source_out": seg.source_out,
            "timeline_in": round(start, 3),
            "timeline_out": round(end, 3),
            "timeline_in_frame": int(round(start * fps)),
            "timeline_out_frame": int(round(end * fps)),
            "label": (f"background_music" if len(bed.segments) == 1
                      else f"background_music_{index + 1:02d}"),
            "bed_segment_index": index,
            "bed_segment_starts_at_block": seg.starts_at_block,
            "crossfade_in_seconds": seg.crossfade_in_seconds,
            "crossfade_out_seconds": seg.crossfade_out_seconds,
            "track_title": seg.track_title,
            "why": seg.why,
        })
    return clips


def describe(bed: MusicBed) -> str:
    """One line for the run log, so the conducting is visible."""
    if not bed.segments:
        return f"music bed: nothing placed - {bed.reason}"
    if not bed.declared:
        seg = bed.segments[0]
        return (f"music bed: one section from {seg.source_in:.1f}s - "
                f"{bed.reason}")
    pieces = ", ".join(
        f"{seg.source_in:.1f}s of "
        f"{seg.track_title or seg.audio_path.rsplit('/', 1)[-1]}"
        f" at block {seg.starts_at_block!r}"
        + (f" (x-fade {seg.crossfade_in_seconds:.2f}s)"
           if seg.crossfade_in_seconds else " (hard splice)" if index else "")
        for index, seg in enumerate(bed.segments)
    )
    return (f"music bed: {len(bed.segments)} segments across "
            f"{len(bed.tracks_used)} track(s) - {pieces}")


def beat_windows(bed: MusicBed, audio_path: str) -> List[Dict[str, float]]:
    """Where a beat grid measured on ``audio_path`` maps onto the timeline.

    ``music_analysis`` measures ONE track - the selection's primary - so a
    spliced bed's grid is valid exactly where that track plays, and a
    segment cut from a different track carries no beats.  Saying that is
    the point: a grid used across a splice would be off by the difference
    on every snapped cut, which is the defect `beat_grid` was written for.
    """
    windows = []
    for seg in bed.segments:
        if seg.audio_path != audio_path:
            continue
        windows.append({
            "source_in": seg.source_in,
            "source_out": seg.source_out,
            "timeline_start": seg.timeline_start,
            "offset": round(seg.source_in - seg.timeline_start, 4),
        })
    return windows
