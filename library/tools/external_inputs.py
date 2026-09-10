"""State the pipeline did not produce, offered to a step, and CHECKED.

The gap this closes
-------------------
A step's prerequisites could be met two ways: a step in this run
produces the value, or a previous run recorded it.  Both are statements
about LINEAGE - which step made it.  The captain's model (#260,
2026-08-28) is a statement about STATE:

    "it should be possible to have as much or as little in the number of
    steps in the pipeline (given that all necessary prerequisties have
    been fulfilled -- like for example you should not be able to add
    transitions or effects when there exists no roughcut either already
    on the timeline manually or automated by the LLM during the process)"

"already on the timeline manually" is the third way, and it did not
exist.  This module is that third way, and the whole of it.

Why this is not a flag
----------------------
An unchecked "trust me, it exists" flag would dissolve exactly the
contract enforcement the captain asked to keep, so nothing here is
asserted.  A prerequisite is satisfied from outside by SUPPLYING THE
VALUE, in a file, which is then verified against a check registered for
that state key - and the same verified value is what
`gather_step_inputs` hands the step.  So the thing that satisfies the
resolver is the thing the step receives; there is no state of the world
where the resolver believed something the run then could not use.

That is the same standard the recorded-output path meets, where a ledger
entry alone is not enough and the `step_outputs` value has to be there
too.

What can be asserted, and what cannot
-------------------------------------
`CHECKS` is the enumeration.  A key that is not in it CANNOT be
supplied - the file is refused by name, saying so - because a check that
does not exist is not a check that passes.  `WITHDRAWN` records the
claims somebody would reasonably try and why they are not assertable;
the first of them is the captain's own words, and the answer to it is
that a hand-built Resolve timeline is not refusable at resolve time,
while the artifact describing it is.

Where it lives
--------------
`<project>/external/<state_key>.json`, an `Area.EXTERNAL_STATE` input
area: `write_dir` raises for it, `ensure()` does not create it, and no
step may write there.  Each file is:

    {
      "key": "assembly_manifest",
      "source": "assembled by hand in Resolve and exported, 2026-08-28",
      "value": { ... }
    }

`source` is RECORDED, never trusted - it is what a reader of
`RUN-TRACEBACK.md` needs in order to know the value did not come from a
step.  The verdict comes from the check, not from the sentence.

    python3 -m library.tools.external_inputs <project_folder>

`tests/test_external_inputs.py`.


Rules relocated from AGENTS.md 3
--------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 3 keeps the headline
and points here.

**A prerequisite may be satisfied from outside the pipeline, and it is CHECKED, never asserted.**
One enumeration, `library/tools/external_inputs.py`.
- The value is SUPPLIED, in `<project>/external/<state_key>.json` carrying `key`, `source` and `value` - not claimed by a flag. The same verified value is what `gather_step_inputs` hands the step, so the resolver can never believe something the run cannot use.
- **The file is named for the STATE key, which is the PRODUCER's name for it.** Step 6.01 records `render_output`; step 6.02 calls the same value `rendered_output`. Offering the consumer's name is refused, naming the producer's.
- **`CHECKS` is the whole of what can be supplied. A key that is not in it is refused by name**, because a check that does not exist is not a check that passes.
- **A SUPPLIED value is a request; a recorded one is history.** A step every one of whose routed outputs is supplied does not run, on any run shape - `run_scope.supplied_producers` derives which, and naming such a step on the command line is REFUSED rather than silently overwriting what was handed in.
- **An empty value is refused unless the key is in `EMPTY_IS_A_STATEMENT`**, which is `b_roll_interjections` alone: leaving a file out and supplying nothing are different requests, and only the second can stop `select_broll` inventing cutaways.  **A Resolve timeline in a CLOSED project is not one of them**: it is not refusable at resolve time, so supply the artifact that describes it instead.  A LIVE one IS readable - `library/tools/timeline_ingest.py` is the producer, and what it writes is verified here like anything else.  It is not skipped.
- `tests/test_external_inputs.py`.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional

from library.tools.project_layout import Area, ProjectLayout


class ExternalStateError(ValueError):
    """A supplied value that does not check out, refused before the run."""


SUFFIX = ".json"
_MIN_RENDER_BYTES = 100_000
"""A master under this is not a delivered video. `render_qa` reads a
single FRAME as broken under 2KB; a whole render is three orders bigger,
and the point of the floor is to catch an empty or truncated file, not
to judge the picture."""


@dataclass(frozen=True)
class Supplied:
    """One verified external value."""

    key: str
    """The state key it supplies - what `step_outputs[producer][key]`
    would have held."""

    value: object
    source: str
    path: Path
    checked: str
    """One sentence naming what was actually verified, for the run log."""


@dataclass(frozen=True)
class Context:
    """What a check may consult besides the value itself."""

    project_folder: Path
    state: Mapping
    """The project's `pipeline_data.json`, for cross-checks that are free
    when the pipeline has already measured something - the catalog's
    per-clip durations, for instance. A check must still be able to
    reach a verdict without it."""


# ── The checks ───────────────────────────────────────────────────────

def _number(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExternalStateError(f"{label} is not a number: {value!r}")
    return float(value)


def _existing_file(path, label: str) -> Path:
    if not isinstance(path, str) or not path:
        raise ExternalStateError(f"{label} names no file")
    resolved = Path(path)
    if not resolved.is_absolute():
        raise ExternalStateError(
            f"{label} is {path!r}, a relative path. Media is addressed "
            f"absolutely everywhere else in this pipeline, and a relative "
            f"one resolves against whichever directory a step happens to "
            f"run in.")
    if not resolved.is_file():
        raise ExternalStateError(f"{label} names {path}, which is not a file")
    return resolved


def _catalog_durations(context: Context) -> Dict[str, float]:
    catalog = ((context.state.get("step_outputs") or {})
               .get("catalog") or {}).get("clip_catalog") or []
    out = {}
    for clip in catalog:
        if isinstance(clip, dict) and clip.get("clip_id"):
            duration = clip.get("duration_seconds")
            if isinstance(duration, (int, float)):
                out[clip["clip_id"]] = float(duration)
    return out


def _check_a_roll_assignments(value, context: Context) -> str:
    """A cut somebody made elsewhere: which clip plays, from where, when.

    Checkable because every claim it makes is about a file on disk and a
    range inside it. What is NOT checked is whether the cut is any good;
    that was never the pipeline's job.
    """
    if not isinstance(value, list) or not value:
        raise ExternalStateError(
            "a_roll_assignments must be a non-empty list of assignments. "
            "An empty one is the absence of a rough cut, not a rough cut "
            "supplied from outside.")
    durations = _catalog_durations(context)
    checked_against_catalog = 0
    for index, entry in enumerate(value):
        label = f"a_roll_assignments[{index}]"
        if not isinstance(entry, dict):
            raise ExternalStateError(f"{label} is not an object")
        source = _existing_file(entry.get("source_file"),
                                f"{label}.source_file")
        video_in = _number(entry.get("video_in"), f"{label}.video_in")
        video_out = _number(entry.get("video_out"), f"{label}.video_out")
        start = _number(entry.get("timeline_start"),
                        f"{label}.timeline_start")
        end = _number(entry.get("timeline_end"), f"{label}.timeline_end")
        if video_out <= video_in:
            raise ExternalStateError(
                f"{label} plays {source.name} from {video_in} to "
                f"{video_out}, which is not a range")
        if end <= start:
            raise ExternalStateError(
                f"{label} occupies {start} to {end} on the timeline, "
                f"which is not a range")
        clip_id = entry.get("clip_id") or entry.get("source_clip_id")
        if clip_id in durations:
            checked_against_catalog += 1
            if video_out > durations[clip_id] + 0.001:
                raise ExternalStateError(
                    f"{label} plays {clip_id} to {video_out}s and the "
                    f"catalog measured that clip at "
                    f"{durations[clip_id]}s")
    against = (f", {checked_against_catalog} of them against the "
               f"catalog's measured durations" if checked_against_catalog
               else ", and the catalog is not on file so no duration "
                    "could be cross-checked")
    return (f"{len(value)} assignments, every source file present on disk "
            f"and every range non-empty{against}")


def _check_audio_spine(value, context: Context) -> str:
    """The cut's structure, checked with the pipeline's own contract."""
    from library.tools import spine_contract

    if not isinstance(value, dict):
        raise ExternalStateError("audio_spine must be an object")
    blocks = value.get("structure")
    if not isinstance(blocks, list) or not blocks:
        raise ExternalStateError(
            "audio_spine.structure must be a non-empty list of blocks")
    try:
        spine_contract.validate_spine_blocks(blocks)
    except spine_contract.SpineContractError as exc:
        raise ExternalStateError(
            f"audio_spine fails the spine contract every step downstream "
            f"reads it through (library/tools/spine_contract.py): "
            f"{exc}") from exc
    return (f"{len(blocks)} blocks, passing "
            f"spine_contract.validate_spine_blocks")


def _check_assembly_manifest(value, context: Context) -> str:
    """A manifest assembled elsewhere, checked as the compiler checks its
    own: structure, then the semantic half, then the media."""
    from library.tools import manifest_validator

    if not isinstance(value, dict):
        raise ExternalStateError("assembly_manifest must be an object")
    errors = list(manifest_validator.validate_manifest(value))
    errors += list(manifest_validator.validate_manifest_semantics(value))
    if errors:
        raise ExternalStateError(
            "assembly_manifest fails the validator step 5.04 runs on its "
            "own output:\n  - " + "\n  - ".join(str(e) for e in errors))
    files = 0
    for track, data in (value.get("tracks") or {}).items():
        for index, clip in enumerate(data.get("clips") or []):
            _existing_file(clip.get("source_file"),
                           f"tracks.{track}.clips[{index}].source_file")
            files += 1
    if not files:
        raise ExternalStateError(
            "assembly_manifest places no clip on any track")
    return (f"passes manifest_validator's structural and semantic halves, "
            f"and all {files} placed clips resolve to a file on disk")


def _check_render_output(value, context: Context) -> str:
    """A master rendered outside the pipeline.

    Named `render_output`, which is what step 6.01 records it as -
    `validate` calls the same value `rendered_output` on its own side of
    the edge. A file here is named for the STATE, so it is the
    producer's name that counts (AGENTS.md section 10.1 on key-name
    mismatches); `_alias_hint` says so when somebody uses the other one.

    The one check here that is not about JSON: ffprobe has to find a
    video stream in the named file. A path that exists is not a video,
    and step 6.02 would otherwise measure the letterbox bars of a text
    file.
    """
    if not isinstance(value, dict):
        raise ExternalStateError("render_output must be an object")
    path = _existing_file(value.get("output_path"),
                          "render_output.output_path")
    size = path.stat().st_size
    if size < _MIN_RENDER_BYTES:
        raise ExternalStateError(
            f"render_output names {path.name}, which is {size} bytes. "
            f"A delivered master is not that small.")
    streams = _probe_streams(path)
    if streams is None:
        raise ExternalStateError(
            f"ffprobe could not read {path.name}. A file that is not "
            f"decodable is not a render.")
    if "video" not in streams:
        raise ExternalStateError(
            f"ffprobe found no video stream in {path.name} "
            f"(streams: {sorted(streams) or 'none'})")
    return (f"{path.name}, {size // 1024} KiB, ffprobe reports "
            f"{'+'.join(sorted(streams))}")


def _check_speech_sequence(value, context: Context) -> str:
    """A spoken order MEASURED off a live timeline, not one invented.

    `speech_sequence` was withdrawn wholesale, and the reason was sound
    for the case it had in mind: "no check exists that could tell a real
    creative decision from a plausible-looking one".  A sequence a model
    proposes is taste, and a shape check would pass anything shaped
    right.

    The captain's ruling (2026-09-04) is that a sequence THEY CUT BY HAND
    is a different thing: a fact to be read, not taste to be invented.
    What makes that case checkable is the same property that makes
    `a_roll_assignments` checkable - every claim it makes is about a file
    on disk and a range inside it.  So this check verifies the
    MEASUREMENT and refuses anything that is merely well shaped:

    - every segment names a source file that EXISTS,
    - every source range is non-empty,
    - every timeline range is non-empty,
    - the declared order is a permutation of 0..n-1 with no gaps or
      repeats, and the forward and backward links agree with it.

    The last of those is what a plausible-looking invention fails.  A
    model asked for a narrative order emits positions; it does not emit
    a self-consistent doubly-linked chain over stable Resolve item ids
    that also agrees with the timeline positions of files on disk.

    What is still NOT checked is whether the cut is any good, which was
    never the pipeline's job.
    """
    if not isinstance(value, dict):
        raise ExternalStateError("speech_sequence must be an object")
    segments = value.get("segments")
    if not isinstance(segments, list) or not segments:
        raise ExternalStateError(
            "speech_sequence.segments must be a non-empty list. An empty "
            "one is the absence of a spoken order, not one supplied from "
            "outside.")

    orders, ids = [], []
    for index, entry in enumerate(segments):
        label = f"speech_sequence.segments[{index}]"
        if not isinstance(entry, dict):
            raise ExternalStateError(f"{label} is not an object")
        _existing_file(entry.get("source_file"), f"{label}.source_file")
        source_start = _number(entry.get("source_start"),
                               f"{label}.source_start")
        source_end = _number(entry.get("source_end"), f"{label}.source_end")
        timeline_start = _number(entry.get("timeline_start"),
                                 f"{label}.timeline_start")
        timeline_end = _number(entry.get("timeline_end"),
                               f"{label}.timeline_end")
        if source_end <= source_start:
            raise ExternalStateError(
                f"{label} plays {source_start} to {source_end} of its "
                f"source, which is not a range")
        if timeline_end <= timeline_start:
            raise ExternalStateError(
                f"{label} occupies {timeline_start} to {timeline_end} on "
                f"the timeline, which is not a range")
        segment_id = entry.get("segment_id")
        if not isinstance(segment_id, str) or not segment_id:
            raise ExternalStateError(
                f"{label}.segment_id is missing. A measured segment is "
                f"addressable - it is what lets one portion be "
                f"re-indexed later without re-reading the rest.")
        orders.append(_number(entry.get("order"), f"{label}.order"))
        ids.append(segment_id)

    if sorted(orders) != [float(i) for i in range(len(segments))]:
        raise ExternalStateError(
            f"speech_sequence.order is not a permutation of "
            f"0..{len(segments) - 1}: a spoken order with a gap or a "
            f"repeat is not an order. Got {sorted(orders)!r}.")
    if len(set(ids)) != len(ids):
        raise ExternalStateError(
            "speech_sequence segment_ids are not unique, so a segment "
            "cannot be addressed by id")

    by_order = {int(o): (i, s) for o, i, s in
                zip(orders, ids, segments)}
    for position in range(len(segments)):
        segment_id, entry = by_order[position]
        expected_prev = by_order[position - 1][0] if position else None
        expected_next = (by_order[position + 1][0]
                         if position + 1 < len(segments) else None)
        if entry.get("previous_segment_id") != expected_prev:
            raise ExternalStateError(
                f"segment {segment_id!r} at order {position} says its "
                f"predecessor is {entry.get('previous_segment_id')!r}; "
                f"the order says {expected_prev!r}. The links and the "
                f"order disagree, so one of them is not a measurement.")
        if entry.get("next_segment_id") != expected_next:
            raise ExternalStateError(
                f"segment {segment_id!r} at order {position} says its "
                f"successor is {entry.get('next_segment_id')!r}; the "
                f"order says {expected_next!r}. The links and the order "
                f"disagree, so one of them is not a measurement.")

    speakers = value.get("speakers") or []
    said = (f", across {len(speakers)} speakers ({', '.join(map(str, speakers))})"
            if speakers else ", naming no speaker")
    return (f"{len(segments)} segments in a self-consistent order, every "
            f"source file present on disk and every range non-empty"
            f"{said}")


# ── The rough cut, and the music spine, made somewhere else ──────────
#
# Three entry points the pipeline had no way in for, and each is a set of
# STATE KEYS rather than a mode:
#
#   the rough cut is already an input   audio_spine + timed_spine +
#                                       speech_sequence +
#                                       a_roll_assignments +
#                                       b_roll_assignments +
#                                       b_roll_interjections
#   the music spine is already placed   music_selection
#
# The third - re-editing a named section - is an ADDRESS rather than
# state, and lives in `library/tools/operations.py`.  What each of the
# three did before any of this, and what each still needs, is measured
# in `docs/ENTRY_POINTS_MEASURED.md`.
#
# There is no "rough cut mode" and there must not be one: the run shape
# falls out of `run_scope.supplied_producers`, which leaves out exactly
# the steps whose whole output is on file.  A mode would be a second
# selection mechanism beside the one that already derives this.


def _check_timed_spine(value, context: Context) -> str:
    """The cut's structure, under the OTHER name mesh_spine records it as.

    `step_2_05_mesh_spine/post_bridge.py:313` is literally
    `result["timed_spine"] = result["audio_spine"]` - they are the same
    object today - and four steps read `timed_spine` while three read
    `audio_spine`.

    Supplying one does NOT supply the other, and that is deliberate
    rather than an oversight worth a convenience alias.  They are two
    state keys with two sets of readers; the day they stop being the same
    object, an alias would have quietly handed four steps the wrong one.
    Two files, both checked, and this docstring names the line that makes
    them identical so a captain can copy rather than author twice.
    """
    return _check_audio_spine(value, context)


def _check_v2_placements(value, context: Context, label: str) -> str:
    """Cutaways somebody chose elsewhere: which clip covers what, when.

    The same standard `_check_a_roll_assignments` meets - every claim is
    about a file on disk and a range inside it - plus the one invariant
    that is specific to the layer: V2 SHOWS ONE CLIP AT A TIME.  Step
    3.02 enforces it when it places its own cutaways
    (`_place_without_overlap`), and a supplied set that overlaps itself
    would reach `compile_manifest`, where overlap resolution deletes one
    of the two silently.  So it is refused here, naming both entries.

    Whether an EMPTY list may be supplied is not decided here.  `verify`
    owns that question for every key at once, and `EMPTY_IS_A_STATEMENT`
    is the one place a key says `[]` means something.  Two owners for one
    question is how the two come to disagree.
    """
    if not isinstance(value, list):
        raise ExternalStateError(f"{label} must be a list")
    durations = _catalog_durations(context)
    spans = []
    checked_against_catalog = 0
    for index, entry in enumerate(value):
        where = f"{label}[{index}]"
        if not isinstance(entry, dict):
            raise ExternalStateError(f"{where} is not an object")
        source = _existing_file(entry.get("source_file"),
                               f"{where}.source_file")
        video_in = _number(entry.get("video_in"), f"{where}.video_in")
        video_out = _number(entry.get("video_out"), f"{where}.video_out")
        start = _number(entry.get("timeline_start"),
                        f"{where}.timeline_start")
        end = _number(entry.get("timeline_end"), f"{where}.timeline_end")
        if video_out <= video_in:
            raise ExternalStateError(
                f"{where} plays {source.name} from {video_in} to "
                f"{video_out}, which is not a range")
        if end <= start:
            raise ExternalStateError(
                f"{where} occupies {start} to {end} on the timeline, "
                f"which is not a range")
        clip_id = entry.get("clip_id") or entry.get("source_clip_id")
        if clip_id in durations:
            checked_against_catalog += 1
            if video_out > durations[clip_id] + 0.001:
                raise ExternalStateError(
                    f"{where} plays {clip_id} to {video_out}s and the "
                    f"catalog measured that clip at {durations[clip_id]}s")
        spans.append((start, end, index))

    spans.sort()
    for (start, end, index), (next_start, _, next_index) in zip(spans,
                                                                spans[1:]):
        if next_start < end - 0.001:
            raise ExternalStateError(
                f"{label}[{index}] runs to {end}s and {label}"
                f"[{next_index}] starts at {next_start}s. V2 shows one "
                f"clip at a time, so two overlapping placements are two "
                f"descriptions of the same seconds - compile_manifest "
                f"would delete one of them without saying which.")

    against = (f", {checked_against_catalog} of them against the catalog's "
               f"measured durations" if checked_against_catalog else "")
    return (f"{len(value)} placements, none overlapping on V2, every "
            f"source file present on disk and every range non-empty"
            f"{against}")


def _check_b_roll_assignments(value, context: Context) -> str:
    return _check_v2_placements(value, context, "b_roll_assignments")


def _check_b_roll_interjections(value, context: Context) -> str:
    return _check_v2_placements(value, context, "b_roll_interjections")


def _check_music_selection(value, context: Context) -> str:
    """A bed the captain chose themselves, checked against the audio file.

    The captain's second entry point: *"the song/music spine being put in
    already"*.  Checkable by the same standard as `a_roll_assignments` -
    every claim is about a file on disk and a range inside it - and by
    nothing more.  WHY this track suits the piece is taste and is not
    asserted here; `direction_justification` is carried through
    unexamined, exactly as a step's own output would be.

    Which key names the track is `requirements.resolve_track_path`, the
    same reading step 2.06 does, so this and the step it feeds cannot
    disagree about which file is the bed.

    The section is where the silent failure lives.  `section.source_in`
    is the second of the TRACK the bed starts at, and a value past the
    end of the file yields a bed of silence that nothing downstream
    notices - `music_bed` splices from it, `otio_mix` places it, and the
    render is quiet where the music was meant to be.  ffprobe measures
    the real length, so that is EXACT rather than a threshold.
    """
    from library.tools.requirements import TRACK_PATH_KEYS, resolve_track_path

    if not isinstance(value, dict):
        raise ExternalStateError("music_selection must be an object")
    path_text = resolve_track_path(value)
    if not path_text:
        raise ExternalStateError(
            f"music_selection names no track under any of "
            f"{', '.join(TRACK_PATH_KEYS)} or tracks[0]. A selection with "
            f"no file is the absence of a choice, not a choice made "
            f"elsewhere.")
    path = _existing_file(path_text, "music_selection track path")
    streams = _probe_streams(path)
    if streams is None:
        raise ExternalStateError(
            f"ffprobe could not read {path.name}. A file that is not "
            f"decodable is not a music bed.")
    if "audio" not in streams:
        raise ExternalStateError(
            f"ffprobe found no audio stream in {path.name} "
            f"(streams: {', '.join(sorted(streams)) or 'none'})")
    measured = _probe_duration(path)
    if measured is None:
        raise ExternalStateError(
            f"ffprobe read {path.name} but reported no duration, so "
            f"nothing here can say whether the sections named below are "
            f"inside it.")

    extra = 0
    for index, track in enumerate(value.get("tracks") or []):
        if not isinstance(track, dict):
            raise ExternalStateError(f"music_selection.tracks[{index}] "
                                     f"is not an object")
        other = resolve_track_path(track)
        if not other:
            raise ExternalStateError(
                f"music_selection.tracks[{index}] names no audio_path; "
                f"the bed is a SEQUENCE and every track it may splice "
                f"from has to be a file this run can open.")
        _existing_file(other, f"music_selection.tracks[{index}]")
        extra += 1

    checked_spans = 0
    section = value.get("section")
    if isinstance(section, dict) and section.get("source_in") is not None:
        start = _number(section.get("source_in"),
                        "music_selection.section.source_in")
        if start < 0 or start >= measured:
            raise ExternalStateError(
                f"music_selection.section.source_in is {start}s and "
                f"{path.name} is {measured:.3f}s long. The bed would "
                f"start past the end of the track and play silence.")
        checked_spans += 1

    for index, splice in enumerate(value.get("splices") or []):
        if not isinstance(splice, dict):
            raise ExternalStateError(
                f"music_selection.splices[{index}] is not an object")
        # A splice may name ANOTHER track (music_bed's sequence), and
        # this check only measured the primary. Its own file was checked
        # above; the span is checked against the file it really names.
        against = measured if not splice.get("track") else None
        span_in = _number(splice.get("source_in"),
                          f"music_selection.splices[{index}].source_in")
        span_out = _number(splice.get("source_out"),
                           f"music_selection.splices[{index}].source_out")
        if span_out <= span_in:
            raise ExternalStateError(
                f"music_selection.splices[{index}] runs {span_in} to "
                f"{span_out}, which is not a range")
        if against is not None:
            if span_out > against + 0.001:
                raise ExternalStateError(
                    f"music_selection.splices[{index}] plays to "
                    f"{span_out}s and {path.name} is {against:.3f}s long")
            checked_spans += 1

    others = f", {extra} further track(s) present on disk" if extra else ""
    spans = (f", {checked_spans} named span(s) inside it" if checked_spans
             else ", naming no span so the bed plays from the head")
    return (f"{path.name}, {measured:.3f}s, ffprobe reports "
            f"{'+'.join(sorted(streams))}{others}{spans}")


def _speech_texts(context: Context) -> Optional[str]:
    """Every spoken word the pipeline has measured, as one string.

    Returns None where no speech is on file yet (a first run, before
    any spine exists) - the check must reach a verdict without state,
    and drift then moves to apply time, where it reports STALE loudly.
    Reads the spine the pipeline built AND the speech sequence, because
    a captain's anchor may name words in either; `compile_manifest`
    reads `pipeline_data.json` the same way rather than one file.
    """
    outputs = (context.state.get("step_outputs") or {})
    parts = []
    for producer in ("mesh_spine", "assign_aroll", "speech_sequence",
                     "catalog"):
        payload = outputs.get(producer) or {}
        for key in ("audio_spine", "timed_spine"):
            spine = payload.get(key) or {}
            for block in spine.get("structure") or []:
                content = (block or {}).get("content") or {}
                text = content.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text)
        sequence = payload.get("body_sequence") or []
        for passage in sequence:
            if isinstance(passage, dict):
                text = passage.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text)
            elif isinstance(passage, str) and passage.strip():
                parts.append(passage)
    joined = " ".join(parts)
    return joined if joined.strip() else None


def _check_captain_edits(value, context: Context) -> str:
    """The captain's edits as small readable deltas, not replacements.

    A whole-value handoff (`speech_sequence` wholesale) is unreadable,
    unreviewable and goes stale the moment anything upstream
    legitimately changes; a delta names one anchor and one decision.
    `captain_edits.validate_edits` owns the structure - kind, anchor,
    replacement, reason, and the refusal of frame-anchored edits - so
    this check never restates it.

    The correspondence half lives HERE, because captions have no other
    external route: where the pipeline has already measured speech,
    every edit's anchor must still occur in it. A caption fix whose
    anchor is gone is drift - refused by name rather than applied to
    the wrong seconds. Where no speech is on file yet the edits verify
    structurally, and drift reports STALE at apply time instead of
    vanishing (library/tools/captain_edits.py).
    """
    from library.tools import captain_edits

    try:
        edits = captain_edits.validate_edits(value)
    except captain_edits.CaptainEditError as exc:
        raise ExternalStateError(f"captain_edits is not a list of "
                                 f"edits: {exc}") from exc
    speech = _speech_texts(context)
    if speech is not None:
        import re as _re

        haystack = _re.sub(r"\s+", " ",
                            _re.sub(r"[^\w\s']", "", speech.lower()))
        for index, edit in enumerate(edits):
            phrases = [edit["anchor_phrase"]]
            if edit.get("kind") == "redraw_closer":
                # Both ends of a pin must still be spoken: the words
                # the closer must open on, and the opening identifying
                # which closer moves (it stays inside the redrawn span).
                phrases.append(edit["from_phrase"])
            for phrase in phrases:
                anchor = captain_edits.normalize(phrase)
                if anchor not in haystack:
                    raise ExternalStateError(
                        f"captain_edits[{index}] anchors to "
                        f"{phrase!r}, and those words are in "
                        f"no speech the pipeline has measured. A supplied "
                        f"caption fix that no longer corresponds to its "
                        f"speech is drift, not an edit - re-anchor it to "
                        f"words the reel still says, or drop it.")
        return (f"{len(edits)} edit(s), every anchor still spoken by "
                f"the measured speech")
    kinds = {}
    for edit in edits:
        kinds[edit["kind"]] = kinds.get(edit["kind"], 0) + 1
    breakdown = ", ".join(f"{count} {kind}" for kind, count in
                           sorted(kinds.items()))
    return (f"{len(edits)} edit(s) ({breakdown}); no speech measured "
            f"yet, so correspondence could not be cross-checked and "
            f"drift will report STALE at apply time")


def _probe_duration(path: Path) -> Optional[float]:
    """The file's real length in seconds, or None if ffprobe cannot say."""
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", str(path)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60, check=False)
        if proc.returncode != 0:
            return None
        duration = json.loads(proc.stdout).get("format", {}).get("duration")
        return float(duration) if duration is not None else None
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return None


def _probe_streams(path: Path) -> Optional[set]:
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_streams", str(path)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60, check=False)
        if proc.returncode != 0:
            return None
        data = json.loads(proc.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return {stream.get("codec_type") for stream in data.get("streams", [])
            if stream.get("codec_type")}


Check = Callable[[object, Context], str]

CHECKS: Dict[str, Check] = {
    "a_roll_assignments": _check_a_roll_assignments,
    "audio_spine": _check_audio_spine,
    "timed_spine": _check_timed_spine,
    "b_roll_assignments": _check_b_roll_assignments,
    "b_roll_interjections": _check_b_roll_interjections,
    "assembly_manifest": _check_assembly_manifest,
    "captain_edits": _check_captain_edits,
    "music_selection": _check_music_selection,
    "render_output": _check_render_output,
    "speech_sequence": _check_speech_sequence,
}


EMPTY_IS_A_STATEMENT: frozenset = frozenset({"b_roll_interjections"})
"""Keys where an EMPTY value is a decision, not the absence of one.

The default is the other way and stays that way: "nothing is not a
value; leave the file out instead" is right for a rough cut, a manifest
or a render, where an empty file is somebody who meant to write one and
did not.

`b_roll_interjections` is the exception because LEAVING IT OUT AND
SUPPLYING NOTHING ARE DIFFERENT REQUESTS.  `select_broll` emits both it
and `b_roll_assignments`, and `run_scope.supplied_producers` needs ALL
of a producer's routed keys before the step stops running - so a hand
cut with cutaways over its speech but no standalone ones has no way to
say so, and the step runs and invents some.  `[]` here is AGENTS.md
10.5's line exactly: the absence of decoration, not the absence of a
decision.

An entry is added here for a key where that argument can be made, never
because a check was inconvenient.  Everything in this set is still
CHECKED - `CHECKS[key]` runs on the empty value like any other.
"""


# ── What cannot be asserted ──────────────────────────────────────────
#
# Recorded rather than left to a puzzled reader, because "there is no
# check for that" is a real answer and the alternative - a flag taken on
# faith - is the thing this module exists to avoid.

WITHDRAWN: Dict[str, str] = {
    "a Resolve timeline in a CLOSED project":
        "NARROWED 2026-09-04, and the narrowing is the point. This "
        "entry used to read 'a Resolve timeline built by hand' and rule "
        "out the captain's own case. It conflated two different things. "
        "A CLOSED project really is unreadable at resolve time: it lives "
        "in Resolve's project database, reading it means copying that "
        "database and opening it as SQLite (AGENTS.md section 5), and "
        "nothing there maps its clips back onto a typed pipeline key. "
        "That half stands and is what remains withdrawn. A LIVE project "
        "is a different object: its scripting API answers, per timeline "
        "item, GetMediaPoolItem().GetClipProperty('File Path'), "
        "GetSourceStartTime()/GetSourceEndTime(), GetStart()/GetEnd() "
        "and GetUniqueId() - which is every field "
        "`_check_a_roll_assignments` and `_check_speech_sequence` "
        "already demand, with no database copy at all. So the live case "
        "is now a PRODUCER, `library/tools/timeline_ingest.py`, and the "
        "contract did not weaken to admit it: what that producer writes "
        "is verified here like anything else. For a closed project, "
        "supply the artifact DESCRIBING the cut instead - `audio_spine` "
        "for the structure, `assembly_manifest` for a whole assembly, "
        "`a_roll_assignments` for which clip plays when.",
    "transition_spec / enhancement_spec / sfx_spec / color_grade_spec":
        "Nothing to assert. Since #260 these are OPTIONAL inputs of "
        "`compile_manifest`, so a run that does not want them simply "
        "leaves their planners out and the manifest compiles with hard "
        "cuts, no effects, no sound design and no grade.",
    "creative_direction / the planning outputs, as TASTE":
        "No check exists that could tell a real creative decision from a "
        "plausible-looking one, and a shape check would pass anything "
        "shaped right. Supplying taste from outside is a different "
        "feature from satisfying a prerequisite, and it would need a "
        "reviewer rather than a validator. NARROWED 2026-09-04: "
        "`speech_sequence` left this entry, because the captain ruled "
        "that a sequence THEY CUT BY HAND is a fact to be read rather "
        "than taste to be invented, and a measured one is checkable by "
        "the same standard as `a_roll_assignments` - every claim it "
        "makes is about a file on disk and a range inside it. See "
        "`_check_speech_sequence`, which refuses a merely well-shaped "
        "one. A sequence a MODEL proposes is still taste and is still "
        "not suppliable; nothing here can tell the two apart except that "
        "the measured one carries a self-consistent chain over stable "
        "Resolve item ids that agree with files on disk. NARROWED AGAIN "
        "2026-09-06 for `music_selection`, on the captain's third entry "
        "point - 'the song/music spine being put in already'. A track "
        "the captain CHOSE is a fact about a file on disk and the "
        "seconds of it that play, and `_check_music_selection` refuses a "
        "well-shaped one whose file is missing, carries no audio, or "
        "names a section past the end of the track. WHY it suits the "
        "piece is still taste: `direction_justification` is carried "
        "through unexamined and nothing here grades it.",
}


# ── Reading a project's external state ───────────────────────────────

def external_dir(project_folder) -> Path:
    """Where a project's supplied values live. Never created here: an
    INPUT area exists because the captain made it."""
    return Path(ProjectLayout(str(project_folder)).read_dir(
        Area.EXTERNAL_STATE))


def _alias_hint(key: str) -> str:
    """Say so when the offered name is a CONSUMER'S name for something
    that IS checkable under the producer's name.

    `validate` declares `rendered_output`; step 6.01 records
    `render_output`. A file supplies STATE, so the producer's name is
    the one that counts - and a captain who read the consumer's manifest
    has no way to know that without being told.
    """
    from library.tools import run_scope

    for edge in run_scope.load_dag().get("edges", []):
        for source_key, destination in (edge.get("data_mapping") or {}).items():
            if destination == key and source_key in CHECKS:
                return (f"  {key!r} is what {edge['to']} calls it. The "
                        f"STATE key is {source_key!r} - {edge['from']} "
                        f"records it under that name - so the file is "
                        f"{source_key}{SUFFIX}.\n")
    return ""


def verify(path: Path, context: Context) -> Supplied:
    """One file, checked. Raises `ExternalStateError` with the reason."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ExternalStateError(f"{path} cannot be read: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ExternalStateError(f"{path} is not valid JSON: {exc}") from exc

    if not isinstance(document, dict):
        raise ExternalStateError(
            f"{path.name} must be an object with 'key', 'source' and "
            f"'value'.")

    key = document.get("key")
    if key != path.stem:
        raise ExternalStateError(
            f"{path.name} declares key {key!r}. The file name IS the "
            f"state key it supplies, so name the file {key}{SUFFIX} or "
            f"fix the key.")
    if key not in CHECKS:
        raise ExternalStateError(
            f"{key!r} cannot be supplied from outside the pipeline: "
            f"nothing here can check it. A check that does not exist is "
            f"not a check that passes.\n"
            f"  Checkable: {', '.join(sorted(CHECKS))}\n"
            f"{_alias_hint(key)}"
            f"  See library/tools/external_inputs.WITHDRAWN for what is "
            f"not, and why.")

    source = document.get("source")
    if not isinstance(source, str) or not source.strip():
        raise ExternalStateError(
            f"{path.name} declares no 'source'. It is recorded, not "
            f"trusted - a run has to be able to say where a value that "
            f"no step produced came from.")

    if "value" not in document:
        raise ExternalStateError(f"{path.name} carries no 'value'")
    value = document["value"]
    if value is None or (isinstance(value, (list, dict, str))
                         and len(value) == 0
                         and key not in EMPTY_IS_A_STATEMENT):
        raise ExternalStateError(
            f"{path.name} supplies an empty {type(value).__name__}. "
            f"Nothing is not a value; leave the file out instead."
            + (f"\n  ({key!r} is not one of the keys where empty means "
               f"something: {', '.join(sorted(EMPTY_IS_A_STATEMENT))})"
               if EMPTY_IS_A_STATEMENT else ""))

    checked = CHECKS[key](value, context)
    return Supplied(key=key, value=value, source=source.strip(), path=path,
                    checked=checked)


def load(project_folder, state: Optional[Mapping] = None
         ) -> Dict[str, Supplied]:
    """Every verified external value for a project, `{state key: value}`.

    A file that does not check out RAISES. It is a claim the captain
    made about their own project, and skipping it would leave the run to
    fail later for a reason that names a step instead of the file.
    """
    if not project_folder:
        return {}
    try:
        directory = external_dir(project_folder)
    except (KeyError, ValueError):
        return {}
    if not directory.is_dir():
        return {}

    context = Context(project_folder=Path(project_folder),
                      state=state or {})
    supplied: Dict[str, Supplied] = {}
    for path in sorted(directory.glob(f"*{SUFFIX}")):
        entry = verify(path, context)
        supplied[entry.key] = entry
    return supplied


def describe(supplied: Mapping[str, Supplied]) -> List[str]:
    """The lines a run prints about state it did not produce."""
    lines = []
    for key in sorted(supplied):
        entry = supplied[key]
        lines.append(f"  Supplied from outside: {key} - {entry.checked}")
        lines.append(f"      source (recorded, not checked): {entry.source}")
    return lines


def main(argv=None) -> int:
    import sys

    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: python3 -m library.tools.external_inputs "
              "<project_folder>", file=sys.stderr)
        return 2
    project_folder = argv[0]
    state_path = ProjectLayout(project_folder).pipeline_data_path
    state = {}
    if os.path.exists(state_path):
        try:
            state = json.loads(Path(state_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
    try:
        supplied = load(project_folder, state)
    except ExternalStateError as exc:
        print(f"REFUSED\n\n{exc}\n")
        return 1
    if not supplied:
        print(f"No external state in "
              f"{external_dir(project_folder)}.\n"
              f"Checkable keys: {', '.join(sorted(CHECKS))}")
        return 0
    print("\n".join(describe(supplied)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
