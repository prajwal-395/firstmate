"""Sub-block anchors: one address vocabulary for every plan entry placed in time.

Measured defect (fidelity probe P5): plans address whole blocks. Every
VFX, SFX and transition entry attaches to a spine block, often 3-10 s
long, and no field anchored to a word, a beat or a frame - so a plan
asking "punch 15% on 'quit', whoosh exactly on it" put the whoosh at
the block start, 3.06 s early, and stretched the punch across the whole
10 s block. An `at_word` key nobody read was the silent form of the
same defect; rung 1 (PR #1373) made unread keys refuse instead.

This module is the vocabulary rung 2 adds: a word anchor (the word,
resolved to its transcript word and occurrence), a beat or downbeat
anchor (bar and beat on the detected grid), and a frame anchor
(timeline frame), each with an optional offset, alongside the existing
block reference. It is defined ONCE, here, and every placement-bearing
post-bridge reads it from here:

- step 4.02 `plan_transitions` (cut points),
- step 4.03 `plan_vfx` (effect spans),
- step 4.04 `plan_sfx` (sound placements).

Surveyed and NOT carrying anchors: step 4.06 `render_motion_graphics`
has its own anchored-timing form already (`anchor`/`anchor_phrase` plus
`start_seconds`/`duration_seconds`); step 4.01/4.05 subtitles are
word-timed by construction; `mesh_spine` blocks are what anchors
address INTO, not placed by them. No step emits speed or retime entries
today - `speed_ramp` is dropped as `unknown_effect_type` (rung 3) - so
there is no speed entry to carry one; the vocabulary is defined here so
a future speed step inherits it instead of inventing a second one.

Resolution happens at post-bridge time, to an exact timeline frame, and
an anchor that cannot resolve REFUSES in the `RenRefusal` shape - never
falls back to the block start silently:

- word not in the block (or occurrence past its matches),
- beat/downbeat asked of an empty grid,
- `grid: detected` asked of an estimated grid,
- frame (or offset result) outside the block.

Word timings are read through the spine contract - the block's own
`word_timestamps`, mapped with `source_to_timeline` - which is the
existing transcript interface. Nothing here touches step 1.04
transcription (a concurrent lane owns it).

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
# lets them through, then resolves them below. `anchor` is the moment (a
# cut, a hit, an effect's start); `anchor_end` is an effect's end, which
# is what makes a punch span one word instead of the block.
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
                   music_selection=None, frame_rate: float = 30.0,
                   step: str = "?", plan: str = "?",
                   index=0, end: str = "anchor") -> dict:
    """Resolve one anchor dict to an exact timeline frame.

    `block` is the spine block the entry addresses (the existing block
    reference stays mandatory - the anchor refines inside it, it never
    replaces it). Returns `{"timeline_seconds", "frame", "method"}`.
    Raises `AnchorRefused` where the anchor names nothing placeable.

    Forms (exactly one address key per anchor):

        {"word": "quit"}                          the word's start
        {"word": "quit", "occurrence": 2}          its second saying
        {"word": "quit", "edge": "end"}            the word's end
        {"beat": 17}                              17th grid beat
        {"bar": 4, "beat": 2}                     2nd beat of bar 4
        {"downbeat": 4}                           4th bar start
        {"frame": 343}                            timeline frame 343

    Any form takes `offset_seconds` and/or `offset_frames`, applied
    after the address resolves. The addressed moment must lie inside
    the block - a frame (or an offset result) outside it refuses,
    because an anchor is sub-block addressing, not a second position.
    """
    from library.tools.frame_utils import seconds_to_frame
    from library.tools.spine_contract import source_to_timeline

    if not isinstance(anchor, dict):
        raise _refuse(step, plan, index, end,
                      f"anchor {anchor!r} is not an object",
                      f"re-plan entry {index} of `{plan}` with an "
                      f"anchor object (one of word / beat / bar+beat / "
                      f"downbeat / frame), or drop the anchor.")
    address = [k for k in ("word", "beat", "bar", "downbeat", "frame")
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
            f"address - word, beat, bar (+ beat), downbeat or frame "
            f"- or drop the anchor.")
    known_modifiers = {"occurrence", "edge", "offset_seconds",
                       "offset_frames", "grid"}
    for key in anchor:
        if key in ("word", "beat", "bar", "downbeat", "frame"):
            continue
        if key not in known_modifiers:
            raise _refuse(step, plan, index, end,
                          f"anchor key {key!r} is not read",
                          f"re-plan entry {index} of `{plan}` without "
                          f"{key!r} (read: address plus occurrence, "
                          f"edge, offset_seconds, offset_frames, grid), "
                          f"or drop the anchor.")

    if (("word" in anchor or "frame" in anchor)
            and anchor.get("grid", GRID_ANY) not in (GRID_ANY,)):
        which = "word" if "word" in anchor else "frame"
        raise _refuse(step, plan, index, end,
                      f"grid applies to beat anchors, not {which} ones",
                      f"re-plan entry {index} of `{plan}` without "
                      f"the grid key, or drop the anchor.")

    if "word" in anchor:
        source_time, method = _resolve_word(anchor, block, step, plan,
                                            index, end)
        moment = source_to_timeline(float(source_time), block)
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
    if not (start - 1e-9 <= moment <= stop + 1e-9):
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
