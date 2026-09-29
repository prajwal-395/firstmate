"""Sub-block anchors: one address vocabulary for every plan entry placed in time.

Measured defect (fidelity probe P5): plans address whole blocks. Every
VFX, SFX and transition entry attaches to a spine block, often 3-10 s
long, and no field anchored to a word, a beat or a frame - so a plan
asking "punch 15% on 'quit', whoosh exactly on it" put the whoosh at
the block start, 3.06 s early, and stretched the punch across the whole
10 s block. An `at_word` key nobody read was the silent form of the
same defect; rung 1 (PR #1373) made unread keys refuse instead.

This module is the vocabulary rung 2 adds - rung 4d extends it to the
picture's own motion and rung 5e to the soundtrack's own events: a word
anchor (the word, resolved to its transcript word and occurrence), a
beat or downbeat anchor (bar and beat on the detected grid), a section
anchor (a functional label from the measured section grid, resolved to
its first downbeat), a motion anchor (an action onset or a motion apex
from the clip's measured motion peaks, resolved through the routed
temporal summaries), an event anchor (a non-speech sound the clip's
measured event layer names - a laugh, an impact, a music entrance -
resolved through the same summaries), and a frame anchor (timeline
frame), each with an optional offset, alongside the existing block
reference. It is defined ONCE, here, and every placement-bearing
post-bridge reads it from here:

- step 4.02 `plan_transitions` (cut points),
- step 4.03 `plan_vfx` (effect spans),
- step 4.04 `plan_sfx` (sound placements).

Surveyed and NOT carrying anchors: step 4.06 `render_motion_graphics`
has its own anchored-timing form already (`anchor`/`anchor_phrase` plus
`start_seconds`/`duration_seconds`); step 4.01/4.05 subtitles are
word-timed by construction; `mesh_spine` blocks are what anchors
address INTO, not placed by them. Native speed entries (`speed_ramp`,
`freeze_frame`) inherit the vocabulary through 4.03's shared entry
keys - a ramp step lands on an anchored span like any other effect.

Resolution happens at post-bridge time, to an exact timeline frame, and
an anchor that cannot resolve REFUSES in the `RenRefusal` shape - never
falls back to the block start silently:

- word not in the block (or occurrence past its matches),
- beat/downbeat asked of an empty grid,
- `grid: detected` asked of an estimated grid,
- section label not in the measured grid (or occurrence past its spans),
- event label not measured on the block's clip (or occurrence past
  its spans),
- frame (or offset result) outside the block.

Word timings are read through the spine contract - the block's own
`word_timestamps`, mapped with `source_to_timeline` - which is the
existing transcript interface. Motion peaks AND sound events are read
through the routed temporal summaries (the per-clip `motion_peaks`
and `sound_events` in source seconds, mapped the same way) - nothing
here opens the per-clip index files, so the vocabulary stays hermetic
to the post-bridge's inputs and to the replay bench.

The section grid is read through `library/tools/music_sections.py`,
the one module that knows the producer's shape - the same position
`beat_grid.py` holds for the beat series. A section anchor resolves to
the section's FIRST DOWNBEAT (the bar-aligned moment the grid
measured), or to its end with `edge: end`.

The beat grid is read through `library/tools/beat_grid.py`, the one
module that knows the producer's shape. Frame anchors are divided by
the run's own frame rate and converted with `frame_utils`
(`seconds_to_frame` is the single rounding boundary).
"""

from __future__ import annotations

import string

from library.tools.ren_refusal import RenRefusal

# The top-level plan-entry keys carrying anchors. Each placement-bearing
# post-bridge unions these into its own ENTRY_KEYS so `refuse_unknown_keys`
# lets them through, then resolves them below. `anchor` is the first
# material moment and `anchor_end` the second. Most effects use those as
# their start/end; `zoom_emphasis` uses them as the peak/release-start pair,
# then expands the rendered window around its two independent ramps.
ANCHOR_ENTRY_KEYS = frozenset({"anchor", "anchor_end"})

# What an addressed beat is measured against when the plan says nothing.
# `grid: "detected"` demands the tracker-heard grid; anything else takes
# the best available grid and says which one answered.
GRID_ANY = "any"
GRID_DETECTED = "detected"

# Word edges: the anchor resolves to the word's start or its end.
EDGE_START = "start"
EDGE_END = "end"

_WORD_STRIP = string.punctuation + " \t\n\r…—–"


class AnchorRefused(RenRefusal):
    """A sub-block anchor names something that cannot be placed."""


def _refuse(step: str, plan: str, index, end: str, detail: str,
            fix: str) -> AnchorRefused:
    return AnchorRefused(
        what=(f"step {step} plan entry {index} {end} cannot "
              f"resolve: {detail}"),
        why=(f"an anchor that resolves nowhere is the defect this "
             f"vocabulary exists to remove: entry {index} of `{plan}` "
             f"names {detail}, and placing it on the block start "
             f"instead would ship timing nobody decided on."),
        fix=fix,
    )


def _number(value, name: str, step: str, plan: str, index, end: str):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _refuse(step, plan, index, end,
                      f"{name} {value!r} is not a number",
                      f"re-plan entry {index} of `{plan}` with {name} "
                      f"as a number, or drop the anchor.")
    return float(value)


def _positive_int(value, name: str, step: str, plan: str, index, end: str):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _refuse(step, plan, index, end,
                      f"{name} {value!r} is not a positive integer",
                      f"re-plan entry {index} of `{plan}` with {name} "
                      f"as a 1-based count, or drop the anchor.")
    return value


def _normalise_word(text) -> str:
    return str(text).strip(_WORD_STRIP).lower()


def _word_hits(block: dict, word: str) -> list:
    """Every transcript word in the block matching `word`, in order."""
    target = _normalise_word(word)
    hits = []
    for entry in block.get("word_timestamps") or []:
        if not isinstance(entry, dict):
            continue
        if _normalise_word(entry.get("word", "")) == target:
            hits.append(entry)
    return hits


def _grid_source(music_analysis) -> str:
    tempo = (music_analysis or {}).get("tempo") or {}
    return tempo.get("downbeat_source") or "unknown"


def _resolve_word(anchor: dict, block: dict, step: str, plan: str,
                  index, end: str):
    """A word anchor to (source_start, source_end) of one transcript word."""
    raw = anchor.get("word")
    if not isinstance(raw, str) or not _normalise_word(raw):
        raise _refuse(step, plan, index, end,
                      f"word {raw!r} names no word",
                      f"re-plan entry {index} of `{plan}` with "
                      f"`anchor: {{word: <the word as spoken>}}`, or "
                      f"drop the anchor.")
    hits = _word_hits(block, raw)
    if not hits:
        raise _refuse(
            step, plan, index, end,
            f"word {raw!r} is not spoken in block "
            f"{block.get('position')!r}",
            f"re-plan entry {index} of `{plan}` with a word the block "
            f"actually says (its line is in `timed_spine`), or drop "
            f"the anchor.")
    occurrence = anchor.get("occurrence", 1)
    occurrence = _positive_int(occurrence, "occurrence",
                               step, plan, index, end)
    if occurrence > len(hits):
        raise _refuse(
            step, plan, index, end,
            f"word {raw!r} occurs {len(hits)} time(s) in block "
            f"{block.get('position')!r} but occurrence "
            f"{occurrence} was asked for",
            f"re-plan entry {index} of `{plan}` with occurrence 1.."
            f"{len(hits)}, or drop the anchor.")
    hit = hits[occurrence - 1]
    edge = anchor.get("edge", EDGE_START)
    if edge not in (EDGE_START, EDGE_END):
        raise _refuse(step, plan, index, end,
                      f"edge {edge!r} is not 'start' or 'end'",
                      f"re-plan entry {index} of `{plan}` with edge "
                      f"'start' or 'end', or drop it (start is the "
                      f"default).")
    source_time = hit["source_end"] if edge == EDGE_END else hit["source_start"]
    return float(source_time), (
        f"word {raw!r} occurrence {occurrence} {edge} "
        f"(block {block.get('position')!r})")


def _motion_summaries(temporal_indices) -> dict:
    """The routed temporal summaries keyed by clip id.

    Tolerates the wrapped shape (`{"temporal_event_indices": [...]}`)
    the runners sometimes hand over alongside the bare list. Entries
    without a clip id carry nothing an anchor can join to and are
    skipped - a summary that cannot be addressed is not an address.
    """
    if isinstance(temporal_indices, dict):
        temporal_indices = temporal_indices.get(
            "temporal_event_indices", [])
    by_clip: dict = {}
    for entry in temporal_indices or []:
        if isinstance(entry, dict) and entry.get("clip_id"):
            by_clip[str(entry["clip_id"])] = entry
    return by_clip


def _resolve_motion(anchor: dict, block: dict, temporal_indices,
                    kind: str, step: str, plan: str, index, end: str):
    """A motion anchor to one measured peak inside the block.

    `kind` is `motion_peak` (an apex, where the action PEAKS) or
    `action_onset` (an onset, where it STARTS). The anchor's value is
    the 1-based occurrence among that kind inside the block's source
    range - `{motion_peak: 2}` is the block's second apex. A peak is
    a point: `edge` refuses here, and offsets apply after like every
    other form.
    """
    clip_id = block.get("clip_id")
    if not clip_id:
        raise _refuse(
            step, plan, index, end,
            f"a {kind} anchor on block {block.get('position')!r}, "
            f"which names no source clip",
            f"re-plan entry {index} of `{plan}` with a motion anchor "
            f"on a block cut from a source clip (a cutaway-covered "
            f"block names none - its motion is the cutaway's, which "
            f"no block range addresses), or drop the anchor.")
    summaries = _motion_summaries(temporal_indices)
    if not summaries:
        raise _refuse(step, plan, index, end,
                      "no motion measurement is routed to this step",
                      f"re-plan entry {index} of `{plan}` without "
                      f"the motion anchor, or drop the anchor.")
    summary = summaries.get(str(clip_id))
    if summary is None:
        raise _refuse(
            step, plan, index, end,
            f"clip {clip_id!r} has no motion measurement in the "
            f"routed summaries",
            f"re-plan entry {index} of `{plan}` without the motion "
            f"anchor, or drop the anchor.")
    peaks = summary.get("motion_peaks")
    if (not isinstance(peaks, list)
            or summary.get("motion_method", "unmeasured")
            == "unmeasured"):
        raise _refuse(
            step, plan, index, end,
            f"clip {clip_id!r} motion is unmeasured "
            f"({summary.get('motion_method', 'unmeasured')})",
            f"re-plan entry {index} of `{plan}` without the motion "
            f"anchor, or drop the anchor.")
    if "edge" in anchor:
        raise _refuse(step, plan, index, end,
                      f"edge on a {kind} anchor: a peak is a point",
                      f"re-plan entry {index} of `{plan}` without "
                      f"the edge (start/end is a word and section "
                      f"idea), or drop the anchor.")
    raw = anchor.get(kind)
    occurrence = (1 if raw is None
                  else _positive_int(raw, kind, step, plan,
                                     index, end))
    want = "apex" if kind == "motion_peak" else "onset"
    src_start = block.get("source_start")
    src_end = block.get("source_end")
    if (isinstance(src_start, bool) or isinstance(src_end, bool)
            or not isinstance(src_start, (int, float))
            or not isinstance(src_end, (int, float))):
        raise _refuse(
            step, plan, index, end,
            f"a {kind} anchor on block {block.get('position')!r}, "
            f"which names no source range",
            f"re-plan entry {index} of `{plan}` without the motion "
            f"anchor, or drop the anchor.")
    candidates = []
    for peak in peaks:
        if not isinstance(peak, dict) or peak.get("kind") != want:
            continue
        try:
            moment = float(peak.get("time", float("nan")))
        except (TypeError, ValueError):
            continue
        if float(src_start) - 0.1 <= moment <= float(src_end) + 0.1:
            candidates.append((moment, peak))
    candidates.sort(key=lambda c: c[0])
    if not candidates:
        raise _refuse(
            step, plan, index, end,
            f"no measured {want} inside block "
            f"{block.get('position')!r} (clip {clip_id!r} carries "
            f"{len([p for p in peaks if isinstance(p, dict) and p.get('kind') == want])} "
            f"in total)",
            f"re-plan entry {index} of `{plan}` with an anchor the "
            f"block's own motion carries - the `motion` view shows "
            f"its peaks - or drop the anchor.")
    if occurrence > len(candidates):
        raise _refuse(
            step, plan, index, end,
            f"{kind} occurrence {occurrence} was asked for but block "
            f"{block.get('position')!r} carries {len(candidates)} "
            f"{want}(s)",
            f"re-plan entry {index} of `{plan}` with {kind} "
            f"1..{len(candidates)}, or drop the anchor.")
    source_time = candidates[occurrence - 1][0]
    return float(source_time), (
        f"{want} {occurrence} of {len(candidates)} in block "
        f"{block.get('position')!r} (clip {clip_id!r})")


def _resolve_event(anchor: dict, block: dict, temporal_indices,
                     step: str, plan: str, index, end: str):
    """An event anchor to one measured non-speech sound in the block.

    The anchor's value is the model's own AudioSet label -
    `{event: "Laughter"}` - matched case-insensitively against what
    the block's clip measured; `occurrence` (1-based, default 1) is
    the nth span of that label inside the block's source range. An
    event is a SPAN: the anchor resolves to its start (the onset, what
    a cut or hit lands on) or, with `edge: end`, to its end. Labels
    arrive verbatim from the measurement - a laugh on a run with no
    laughter stays absent, never guessed - and the refusal names what
    the clip DID measure. Read through `library/tools/sound_events.py`,
    the one module that knows the producer's shape, the same position
    `music_sections.py` holds for the section grid.
    """
    from library.tools.sound_events import (
        find_events,
        measured,
    )

    raw = anchor.get("event")
    if not isinstance(raw, str) or not raw.strip():
        raise _refuse(step, plan, index, end,
                      f"event {raw!r} names no event",
                      f"re-plan entry {index} of `{plan}` with "
                      f"`anchor: {{event: <label>}}` naming a label "
                      f"from the `soundevents` view, or drop the anchor.")
    label = raw.strip()
    clip_id = block.get("clip_id")
    if not clip_id:
        raise _refuse(
            step, plan, index, end,
            f"an event anchor on block {block.get('position')!r}, "
            f"which names no source clip",
            f"re-plan entry {index} of `{plan}` with an event anchor "
            f"on a block cut from a source clip (a cutaway-covered "
            f"block names none - its sound is the cutaway's, which "
            f"no block range addresses), or drop the anchor.")
    summaries = _motion_summaries(temporal_indices)
    if not summaries:
        raise _refuse(step, plan, index, end,
                      "no sound-event measurement is routed to this step",
                      f"re-plan entry {index} of `{plan}` without "
                      f"the event anchor, or drop the anchor.")
    summary = summaries.get(str(clip_id))
    if summary is None or not measured(summary):
        raise _refuse(
            step, plan, index, end,
            f"clip {clip_id!r} has no measured sound events",
            f"re-plan entry {index} of `{plan}` without the event "
            f"anchor - an unmeasured clip refuses by name, it never "
            f"serves a guessed event - or drop the anchor.")
    src_start = block.get("source_start")
    src_end = block.get("source_end")
    if (isinstance(src_start, bool) or isinstance(src_end, bool)
            or not isinstance(src_start, (int, float))
            or not isinstance(src_end, (int, float))):
        raise _refuse(
            step, plan, index, end,
            f"an event anchor on block {block.get('position')!r}, "
            f"which names no source range",
            f"re-plan entry {index} of `{plan}` without the event "
            f"anchor, or drop the anchor.")
    matches, present = find_events(summary, label)
    inside = [e for e in matches
              if e["start_seconds"] <= float(src_end) + 0.1
              and e["end_seconds"] >= float(src_start) - 0.1]
    inside.sort(key=lambda e: (e["start_seconds"], e["end_seconds"]))
    if not inside:
        have = ", ".join(present) if present else "none"
        raise _refuse(
            step, plan, index, end,
            f"event {label!r} is not measured inside block "
            f"{block.get('position')!r} (clip {clip_id!r} carries: "
            f"{have})",
            f"re-plan entry {index} of `{plan}` with an event the "
            f"block's own clip measured - the `soundevents` view "
            f"shows its spans - or drop the anchor.")
    occurrence = anchor.get("occurrence", 1)
    occurrence = _positive_int(occurrence, "occurrence",
                               step, plan, index, end)
    if occurrence > len(inside):
        raise _refuse(
            step, plan, index, end,
            f"event {label!r} occurs {len(inside)} time(s) inside "
            f"block {block.get('position')!r} but occurrence "
            f"{occurrence} was asked for",
            f"re-plan entry {index} of `{plan}` with occurrence 1.."
            f"{len(inside)}, or drop the anchor.")
    span = inside[occurrence - 1]
    edge = anchor.get("edge", EDGE_START)
    if edge not in (EDGE_START, EDGE_END):
        raise _refuse(step, plan, index, end,
                      f"edge {edge!r} is not 'start' or 'end'",
                      f"re-plan entry {index} of `{plan}` with edge "
                      f"'start' (the event's onset) or 'end', or drop "
                      f"it (start is the default).")
    source_time = (span["end_seconds"] if edge == EDGE_END
                   else span["start_seconds"])
    return float(source_time), (
        f"{edge} of event {span['label']!r} occurrence {occurrence} "
        f"of {len(inside)} in block {block.get('position')!r} "
        f"(clip {clip_id!r})")


def _resolve_section(anchor: dict, music_analysis, music_selection,
                     step: str, plan: str, index, end: str):
    """A section anchor to the first downbeat of a measured span."""
    from library.tools.music_sections import (
        NON_ADDRESSABLE,
        available,
        find_sections,
    )

    raw = anchor.get("section")
    if not isinstance(raw, str) or not raw.strip():
        raise _refuse(step, plan, index, end,
                      f"section {raw!r} names no section",
                      f"re-plan entry {index} of `{plan}` with "
                      f"`anchor: {{section: <label>}}` naming a label "
                      f"from the `sectiongrid` view, or drop the anchor.")
    label = raw.strip()
    if not available(music_analysis):
        raise _refuse(step, plan, index, end,
                      "no usable section grid is routed",
                      f"re-plan entry {index} of `{plan}` without "
                      f"the section anchor, or drop the anchor.")
    if label in NON_ADDRESSABLE:
        raise _refuse(step, plan, index, end,
                      f"section {label!r} is grid bookkeeping, not music",
                      f"re-plan entry {index} of `{plan}` with a "
                      f"musical section from the `sectiongrid` view, "
                      f"or drop the anchor.")
    matches, present = find_sections(music_analysis, music_selection,
                                     label)
    if not matches:
        have = ", ".join(present) if present else "none"
        raise _refuse(
            step, plan, index, end,
            f"section {label!r} is not in the measured grid "
            f"(it carries: {have})",
            f"re-plan entry {index} of `{plan}` with a section the "
            f"grid measured - a section the model cannot give stays "
            f"absent, never guessed - or drop the anchor.")
    occurrence = anchor.get("occurrence", 1)
    occurrence = _positive_int(occurrence, "occurrence",
                               step, plan, index, end)
    if occurrence > len(matches):
        raise _refuse(
            step, plan, index, end,
            f"section {label!r} occurs {len(matches)} time(s) in the "
            f"grid but occurrence {occurrence} was asked for",
            f"re-plan entry {index} of `{plan}` with occurrence 1.."
            f"{len(matches)}, or drop the anchor.")
    span = matches[occurrence - 1]
    edge = anchor.get("edge", EDGE_START)
    if edge not in (EDGE_START, EDGE_END):
        raise _refuse(step, plan, index, end,
                      f"edge {edge!r} is not 'start' or 'end'",
                      f"re-plan entry {index} of `{plan}` with edge "
                      f"'start' (the section's first downbeat) or "
                      f"'end', or drop it (start is the default).")
    if edge == EDGE_END:
        return (span["end_seconds"],
                f"end of section {label!r} occurrence {occurrence}")
    return (span["first_downbeat_seconds"],
            f"first downbeat of section {label!r} occurrence {occurrence}")


def _resolve_beat(anchor: dict, music_analysis, music_selection,
                  step: str, plan: str, index, end: str):
    """A beat/downbeat/bar anchor to timeline seconds on the grid."""
    from library.tools.beat_grid import beat_positions, downbeat_positions

    beats = beat_positions(music_analysis, music_selection)
    downbeats = downbeat_positions(music_analysis, music_selection)
    source = _grid_source(music_analysis)
    demanded = anchor.get("grid", GRID_ANY)
    if demanded not in (GRID_ANY, GRID_DETECTED):
        raise _refuse(step, plan, index, end,
                      f"grid {demanded!r} is not 'any' or 'detected'",
                      f"re-plan entry {index} of `{plan}` with grid "
                      f"'detected' (tracker-heard bar starts only) or "
                      f"drop it (either grid may answer).")
    if demanded == GRID_DETECTED and source != GRID_DETECTED:
        raise _refuse(
            step, plan, index, end,
            f"a detected grid was asked for but the measured grid is "
            f"{source!r}",
            f"re-plan entry {index} of `{plan}` without `grid: "
            f"detected` (the estimate still answers, and the run "
            f"record says which grid it snapped to), or drop the "
            f"anchor.")
    if "downbeat" in anchor:
        if not downbeats:
            raise _refuse(step, plan, index, end,
                          "no usable downbeat grid is routed",
                          f"re-plan entry {index} of `{plan}` without "
                          f"the beat anchor, or drop the anchor.")
        number = _positive_int(anchor["downbeat"], "downbeat",
                               step, plan, index, end)
        if number > len(downbeats):
            raise _refuse(
                step, plan, index, end,
                f"downbeat {number} was asked for but the grid has "
                f"{len(downbeats)} bar starts",
                f"re-plan entry {index} of `{plan}` with downbeat "
                f"1..{len(downbeats)}, or drop the anchor.")
        return downbeats[number - 1], (
            f"downbeat {number} of the {source} grid")
    if "bar" in anchor:
        if not downbeats:
            raise _refuse(step, plan, index, end,
                          "no usable downbeat grid is routed",
                          f"re-plan entry {index} of `{plan}` without "
                          f"the beat anchor, or drop the anchor.")
        bar = _positive_int(anchor["bar"], "bar", step, plan, index, end)
        beat = _positive_int(anchor.get("beat", 1), "beat",
                             step, plan, index, end)
        if bar > len(downbeats):
            raise _refuse(
                step, plan, index, end,
                f"bar {bar} was asked for but the grid has "
                f"{len(downbeats)} bars",
                f"re-plan entry {index} of `{plan}` with bar "
                f"1..{len(downbeats)}, or drop the anchor.")
        start = downbeats[bar - 1]
        stop = downbeats[bar] if bar < len(downbeats) else float("inf")
        in_bar = [b for b in beats if start - 1e-9 <= b < stop - 1e-9]
        if beat > len(in_bar):
            raise _refuse(
                step, plan, index, end,
                f"bar {bar} beat {beat} was asked for but bar {bar} "
                f"carries {len(in_bar)} grid beat(s)",
                f"re-plan entry {index} of `{plan}` with beat "
                f"1..{max(len(in_bar), 1)} of bar {bar}, or drop the "
                f"anchor.")
        if not in_bar:
            raise _refuse(step, plan, index, end,
                          f"bar {bar} carries no grid beat",
                          f"re-plan entry {index} of `{plan}` with a "
                          f"downbeat anchor instead, or drop the anchor.")
        return in_bar[beat - 1], (
            f"bar {bar} beat {beat} of the {source} grid")
    # A bare beat index into the timeline grid.
    if not beats:
        raise _refuse(step, plan, index, end,
                      "no usable beat grid is routed",
                      f"re-plan entry {index} of `{plan}` without the "
                      f"beat anchor, or drop the anchor.")
    number = _positive_int(anchor["beat"], "beat", step, plan, index, end)
    if number > len(beats):
        raise _refuse(
            step, plan, index, end,
            f"beat {number} was asked for but the grid has "
            f"{len(beats)} beats",
            f"re-plan entry {index} of `{plan}` with beat "
            f"1..{len(beats)}, or drop the anchor.")
    return beats[number - 1], f"beat {number} of the {source} grid"


def resolve_anchor(anchor: dict, *, block: dict, music_analysis=None,
                   music_selection=None, temporal_indices=None,
                   frame_rate: float = 30.0,
                   step: str = "?", plan: str = "?",
                   index=0, end: str = "anchor") -> dict:
    """Resolve one anchor dict to an exact timeline frame.

    `block` is the spine block the entry addresses (the existing block
    reference stays mandatory - the anchor refines inside it, it never
    replaces it). `temporal_indices` are the routed temporal summaries
    carrying each clip's `motion_peaks` - required only by the motion
    forms, which refuse without them. Returns `{"timeline_seconds",
    "frame", "method"}`. Raises `AnchorRefused` where the anchor names
    nothing placeable.

    Forms (exactly one address key per anchor):

        {"word": "quit"}                          the word's start
        {"word": "quit", "occurrence": 2}          its second saying
        {"word": "quit", "edge": "end"}            the word's end
        {"beat": 17}                              17th grid beat
        {"bar": 4, "beat": 2}                     2nd beat of bar 4
        {"downbeat": 4}                           4th bar start
        {"section": "chorus"}                     first downbeat of the chorus
        {"section": "verse", "occurrence": 2}     first downbeat of verse 2
        {"section": "bridge", "edge": "end"}      the bridge's end
        {"motion_peak": 1}                        the block's 1st motion apex
        {"motion_peak": 2}                        its 2nd apex
        {"action_onset": 1}                       the block's 1st action onset
        {"event": "Laughter"}                     the block's 1st laugh
        {"event": "Music", "occurrence": 2}       its 2nd music entrance
        {"event": "Crash cymbal", "edge": "end"}  the impact's end
        {"frame": 343}                            timeline frame 343

    A peak is a point: the motion forms take `occurrence` (1-based,
    default 1) but never `edge`. An event is a span: the event form
    takes `occurrence` (default 1) and `edge` (start is the onset,
    end is its end). Any form takes `offset_seconds`
    and/or `offset_frames`, applied after the address resolves. The
    A frame-based block compares the address on its shared frame grid. If a
    timestamp falls just outside the displayed seconds but rounds to the
    boundary frame, it resolves to that edge; a timestamp in a later frame
    still refuses. An anchor is sub-block addressing, not a second position.
    """
    from library.tools.frame_utils import frame_to_seconds, seconds_to_frame
    from library.tools.spine_contract import source_to_timeline

    if not isinstance(anchor, dict):
        raise _refuse(step, plan, index, end,
                      f"anchor {anchor!r} is not an object",
                      f"re-plan entry {index} of `{plan}` with an "
                      f"anchor object (one of word / beat / bar+beat / "
                      f"downbeat / frame), or drop the anchor.")
    address = [k for k in ("word", "beat", "bar", "downbeat",
                            "section", "frame", "event",
                            "motion_peak", "action_onset")
                if k in anchor]
    # `bar` without `beat` addresses the bar's downbeat; `beat` beside
    # `bar` is the beat inside it - one form, not two.
    forms = len(address) - (1 if ("bar" in anchor and "beat" in anchor)
                            else 0)
    if forms != 1:
        raise _refuse(
            step, plan, index, end,
            f"anchor {anchor!r} names {forms} addresses "
            f"({', '.join(address) or 'none'})",
            f"re-plan entry {index} of `{plan}` with exactly one "
            f"address - word, beat, bar (+ beat), downbeat, section, "
            f"event, motion_peak, action_onset or frame - or drop the "
            f"anchor.")
    known_modifiers = {"occurrence", "edge", "offset_seconds",
                       "offset_frames", "grid"}
    for key in anchor:
        if key in ("word", "beat", "bar", "downbeat", "section", "frame",
                   "event", "motion_peak", "action_onset"):
            continue
        if key not in known_modifiers:
            raise _refuse(step, plan, index, end,
                          f"anchor key {key!r} is not read",
                          f"re-plan entry {index} of `{plan}` without "
                          f"{key!r} (read: address plus occurrence, "
                          f"edge, offset_seconds, offset_frames, grid), "
                          f"or drop the anchor.")

    if (("word" in anchor or "frame" in anchor or "section" in anchor
            or "event" in anchor
            or "motion_peak" in anchor or "action_onset" in anchor)
            and anchor.get("grid", GRID_ANY) not in (GRID_ANY,)):
        which = ("word" if "word" in anchor
                 else "frame" if "frame" in anchor
                 else "event" if "event" in anchor
                 else "motion" if ("motion_peak" in anchor
                                   or "action_onset" in anchor)
                 else "section")
        raise _refuse(step, plan, index, end,
                      f"grid applies to beat anchors, not {which} ones",
                      f"re-plan entry {index} of `{plan}` without "
                      f"the grid key, or drop the anchor.")

    if "word" in anchor:
        source_time, method = _resolve_word(anchor, block, step, plan,
                                            index, end)
        moment = source_to_timeline(float(source_time), block)
    elif "motion_peak" in anchor or "action_onset" in anchor:
        kind = "motion_peak" if "motion_peak" in anchor else "action_onset"
        source_time, method = _resolve_motion(
            anchor, block, temporal_indices, kind,
            step, plan, index, end)
        moment = source_to_timeline(float(source_time), block)
    elif "event" in anchor:
        source_time, method = _resolve_event(
            anchor, block, temporal_indices,
            step, plan, index, end)
        moment = source_to_timeline(float(source_time), block)
    elif "section" in anchor:
        moment, method = _resolve_section(anchor, music_analysis,
                                          music_selection, step, plan,
                                          index, end)
    elif "frame" in anchor:
        number = anchor["frame"]
        if isinstance(number, bool) or not isinstance(number, int) \
                or number < 0:
            raise _refuse(step, plan, index, end,
                          f"frame {number!r} is not a non-negative integer",
                          f"re-plan entry {index} of `{plan}` with "
                          f"frame as a timeline frame number, or drop "
                          f"the anchor.")
        moment = number / float(frame_rate)
        method = f"timeline frame {number}"
    else:
        moment, method = _resolve_beat(anchor, music_analysis,
                                       music_selection, step, plan,
                                       index, end)

    offset_seconds = anchor.get("offset_seconds", 0.0)
    offset_seconds = _number(offset_seconds, "offset_seconds",
                             step, plan, index, end)
    offset_frames = anchor.get("offset_frames", 0)
    if isinstance(offset_frames, bool) \
            or not isinstance(offset_frames, int):
        raise _refuse(step, plan, index, end,
                      f"offset_frames {offset_frames!r} is not an integer",
                      f"re-plan entry {index} of `{plan}` with "
                      f"offset_frames as whole frames, or drop it.")
    moment = moment + offset_seconds + offset_frames / float(frame_rate)
    if offset_seconds or offset_frames:
        method += f" offset {offset_seconds:+g}s {offset_frames:+d}f"

    start = float(block["timeline_start"])
    stop = float(block["timeline_end"])
    # Frame-based spine boundaries are authored once on the shared frame
    # cursor. Their seconds fields are millisecond-rounded for older
    # consumers, so checking an exact frame anchor against those display
    # values can put a valid boundary a fraction of a frame outside its own
    # block. Compare on the same frame grid the build and spine share whenever
    # those fields are present; only pre-frame spine state needs the seconds
    # fallback.
    if "timeline_start_frame" in block or "timeline_end_frame" in block:
        start_frame = block.get("timeline_start_frame")
        end_frame = block.get("timeline_end_frame")
        if (isinstance(start_frame, bool)
                or not isinstance(start_frame, int)
                or isinstance(end_frame, bool)
                or not isinstance(end_frame, int)):
            raise _refuse(
                step, plan, index, end,
                f"block {block.get('position')!r} has an unreadable frame "
                f"span ({start_frame!r}-{end_frame!r})",
                "rebuild the frame-based spine so its shared frame "
                "boundaries reach the anchor planner.")
        anchor_frame = seconds_to_frame(moment, frame_rate)
        inside = start_frame <= anchor_frame <= end_frame
        # Reel motion blocks, like mesh-spine blocks, carry frame edges.
        # A transcript edge can land a few milliseconds beyond the same
        # frame-quantized block edge (for example 9.430s against a 9.426s
        # end at 23.976 fps). Treat that as the block edge, so a visual
        # treatment reaches the last frame the block plays. A word edge
        # that quantizes to any later frame still refuses below.
        if inside and moment > stop and anchor_frame == end_frame:
            moment = frame_to_seconds(end_frame, frame_rate)
            method += " (snapped to block end frame)"
        elif inside and moment < start and anchor_frame == start_frame:
            moment = frame_to_seconds(start_frame, frame_rate)
            method += " (snapped to block start frame)"
    else:
        inside = start - 1e-9 <= moment <= stop + 1e-9
    if not inside:
        raise _refuse(
            step, plan, index, end,
            f"{method} lands at {moment:.3f}s, outside block "
            f"{block.get('position')!r} ({start:.3f}-{stop:.3f}s)",
            f"re-plan entry {index} of `{plan}` with an anchor "
            f"inside block {block.get('position')!r}, or drop the "
            f"anchor.")

    frame = seconds_to_frame(moment, frame_rate)
    return {"timeline_seconds": round(moment, 3),
            "frame": frame,
            "method": method}
