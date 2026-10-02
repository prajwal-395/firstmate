"""A caption-only timing change, and why it is not a `span_retime`.

The defect this closes
----------------------
Reel 13, 2026-09-11.  The captain moved his five closing caption cards
and left the picture frame-identical.  Measured off his live timeline
against the rebuild beside it::

    live     1607-1685  1688-1721  1731-1807  1822-1891  1900-1903
    rebuild  1600-1678  1681-1714  1724-1800  1815-1884  1887-1903

Four cards MOVED seven frames later, duration for duration; the fifth
had its head pulled from 1887 to 1900 with its tail held at 1903.  The
picture under all of it - Akshita's closing shot, reel frames
1599-1909 - is identical in both.

`span_retime` (`library/tools/captain_edits.py`) is the owner for clip
timing and it is correctly wired into the reel path, but it cannot
represent ANY of that:

* it expresses a keep range's HEAD or TAIL edge, and a keep range is
  picture.  Moving a caption card off the picture under it is not an
  edge of anything it holds;
* it trims and refuses to extend, so it has no vocabulary for a SHIFT
  at all - and four of the five edits above are shifts, identical in
  duration, seven frames later;
* it anchors to spoken words in the master transcript, which is the
  right anchor for a shot and the wrong one for a card: two cards in
  one sentence anchor to the same words.

So the eleventh edit class was not a missing wire - it was a class the
existing vocabulary cannot say.  Extending `span_retime` with a "which
row" field would give one word two meanings (a picture edge and a card
offset) measured in two different clocks, which is the
`music_behavior` two-word-vocabulary mistake in a new place.  This
module sits BESIDE it instead, and `edit_depth.OWNERS` carries both
rows.

What a declaration says
-----------------------
`<project>/external/caption_timing.json`, checked and never asserted::

    {"version": 1,
     "pins": [{"scope": {"speaker": "akshita",
                         "source_start_at_or_after": 500.78},
               "offset_frames": 7,
               "reason": "captain 2026-09-11: closer cards run early"}]}

A pin ADDRESSES cards by the source audio they caption: `scope` may name
`speaker`, `source_clip_id`, an exact `source_start`, or the half-open
window `source_start_at_or_after` / `source_start_before`.  Those come
out of the segment's own recorded `binding`
(`library/tools/subtitle_segment_id.py`), which is derived from the
transcript - so a pin survives a re-render, a re-group and a renumber,
and it points at the same words after a rebuild moves every frame
number.  An EMPTY scope is refused: a pin that matches every card in
the project is not a pin.

A pin MAY also name `timeline`: the placement the hand edit was
measured on.  The same source words can close any number of reels -
the captain's format closes every reel on a shared call to action, so
one source span is five reels' closing cards - and a hand trim on one
of them is per-placement, not per-words.  Measured 2026-09-12: two
pins recorded for Reel 13's closing cards matched the SAME source
words on Reel 23 and moved five cards seven frames late and trimmed
the last card's head by thirteen, which the captain marked as drift.
A `timeline` scope narrows the pin to that placement; a pin without
one behaves exactly as before.  The comparison is by the final reel
name, and a container built from it - a rebuild's staging, a
beside-build's suffix - matches through the parenthesised tail, so a
pin survives the build that applies it.  The paren boundary is
load-bearing: "Reel 1" never matches "Reel 13 - ...".

A pin ADJUSTS with any of `offset_frames` (move the whole card,
duration preserved - the shift `span_retime` has no word for),
`head_frames` and `tail_frames` (trim that edge in, tail or head
held).  At least one is required.

What it deliberately does not judge
-----------------------------------
Nothing here enforces a minimum card length.  The captain's own fifth
card is three frames, and a gate that refuses correct output is no
more coverage than one that cannot fail (AGENTS.md 10.4) - so a card
trimmed under the craft floor is REPORTED with its new length and
placed.  A card trimmed to nothing draws nothing and IS refused, and a
pin that matches no card reports STALE rather than vanishing, which is
how the same trim came to be lost twice before anyone noticed.

`tests/unit/captions/test_caption_timing.py`, `tests/unit/resolve/test_captain_edits.py`.
"""

from __future__ import annotations

import json
import os
import sys

from library.tools.ren_refusal import RenRefusal

#: Schema version this reader honours.
CAPTION_TIMING_VERSION = 1

#: The file basename, under the project's external-inputs area.
CAPTION_TIMING_FILENAME = "caption_timing.json"

#: Everything a pin's scope may name. All optional, at least one
#: required.  Every key but `timeline` is a property of the SPEECH,
#: which is what makes a pin survive a rebuild; `timeline` names the
#: placement the hand edit was measured on, for the shared-closer case
#: where one span of source words is several reels' cards.
SCOPE_KEYS = ("speaker", "source_clip_id", "source_start",
              "source_start_at_or_after", "source_start_before",
              "timeline")

#: Everything a pin may adjust. At least one required.
ADJUST_KEYS = ("offset_frames", "head_frames", "tail_frames")

#: How close two recorded source spans must be to be the same span.
#: One millisecond - the resolution the segment's own name is written
#: at (`subtitle_segment_id._span_token`), so a pin copied off a
#: filename matches the binding it was copied from.
SPAN_TOLERANCE_SECONDS = 0.0011

#: Below this the caption craft gate calls a card too short to read
#: (`manifest_validator`). Reported here, never refused: the captain
#: may cut a card to a flash and this module does not overrule him.
SHORT_CARD_SECONDS = 0.5


class CaptionTimingError(ValueError):
    """The declared caption timing cannot be honoured as written."""


class CaptionRebaseRefused(RenRefusal, CaptionTimingError):
    """A pin rebase that would address no audio.

    The module's historical base (`CaptionTimingError`) so every
    existing `except` still catches it, and the one refusal shape
    (`RenRefusal`: what, why, fix) so the boundary prints it like
    every other refusal.
    """


# ── Recording: validation ──────────────────────────────────────────

def validate_pins(value) -> list:
    """Structural check. Raises `CaptionTimingError` naming what is wrong."""
    if not isinstance(value, list) or not value:
        raise CaptionTimingError(
            "caption_timing pins must be a non-empty list. An empty one "
            "is the absence of pins - leave the file out instead.")
    for index, pin in enumerate(value):
        label = f"pins[{index}]"
        if not isinstance(pin, dict):
            raise CaptionTimingError(f"{label} is not an object")
        scope = pin.get("scope")
        if not isinstance(scope, dict) or not scope:
            raise CaptionTimingError(
                f"{label} has no scope. A pin with no scope moves every "
                f"caption in the project, which is not a pin - name at "
                f"least one of {', '.join(SCOPE_KEYS)}.")
        unknown = sorted(set(scope) - set(SCOPE_KEYS))
        if unknown:
            raise CaptionTimingError(
                f"{label} scope names {unknown}, which nothing reads: "
                f"one of {', '.join(SCOPE_KEYS)}. A scope key with no "
                f"reader silently widens the pin to every card.")
        for key in ("source_start", "source_start_at_or_after",
                    "source_start_before"):
            if key in scope and not isinstance(scope[key], (int, float)):
                raise CaptionTimingError(
                    f"{label} scope {key} must be seconds into the "
                    f"source, got {scope[key]!r}.")
        if ("timeline" in scope
                and (not isinstance(scope["timeline"], str)
                     or not scope["timeline"].strip())):
            raise CaptionTimingError(
                f"{label} scope timeline must name the placement the "
                f"hand edit was measured on, got "
                f"{scope['timeline']!r}. An empty timeline matches "
                f"nothing - leave the key out for a pin that is "
                f"about the words wherever they play.")
        adjusts = {k: pin[k] for k in ADJUST_KEYS if k in pin}
        if not adjusts:
            raise CaptionTimingError(
                f"{label} adjusts nothing: name at least one of "
                f"{', '.join(ADJUST_KEYS)}.")
        for key, amount in adjusts.items():
            if not isinstance(amount, int) or isinstance(amount, bool):
                raise CaptionTimingError(
                    f"{label} {key} must be a whole number of FRAMES, "
                    f"got {amount!r}. Seconds would round differently "
                    f"from the placer and land the card a frame off "
                    f"the one that was measured.")
            if key in ("head_frames", "tail_frames") and amount < 0:
                raise CaptionTimingError(
                    f"{label} {key} is {amount}: a trim removes frames. "
                    f"To move a card, use offset_frames, which may be "
                    f"negative.")
        reason = pin.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise CaptionTimingError(
                f"{label} records no reason. A pin outlives the "
                f"conversation that produced it.")
    return list(value)


# ── Reading: the declaration ───────────────────────────────────────

def pins_path(project_folder: str) -> str:
    """Where a project's declared caption timing lives."""
    return os.path.join(project_folder, "external",
                        CAPTION_TIMING_FILENAME)


def load_pins(project_folder: str) -> list:
    """Declared caption-timing pins, or `[]`.

    A file that exists and cannot be read RAISES, for the reason
    `reel_ending.load_endings` states: a declaration the build cannot
    parse is refused, never built silently past.
    """
    path = pins_path(project_folder)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError) as unreadable:
        raise CaptionTimingError(
            f"{path} cannot be read ({unreadable}). A declared caption "
            f"timing the build cannot parse is refused, never ignored.") \
        from unreadable
    version = document.get("version")
    if version != CAPTION_TIMING_VERSION:
        raise CaptionTimingError(
            f"{path} declares version {version!r}; this reader honours "
            f"{CAPTION_TIMING_VERSION}.")
    return validate_pins(document.get("pins"))


# ── Matching ───────────────────────────────────────────────────────

def _binding(segment: dict) -> dict:
    return (segment.get("binding") or {}) if isinstance(segment, dict) else {}


def _slug_match(declared: str, recorded) -> bool:
    """A clip id matches its own slug, and a slug matches its id.

    The captain reads ids off a rendered filename, where they are
    slugged and truncated (`subtitle_segment_id.slug`). A pin copied
    from there must still address the binding it came from, so the
    comparison is a prefix either way round rather than equality.
    """
    if recorded is None:
        return False
    from library.tools.subtitle_segment_id import slug

    want = str(declared).strip().lower()
    got = str(recorded).strip().lower()
    return (got.startswith(want) or want.startswith(got)
            or slug(got, "noclip").startswith(slug(want, "noclip")))


def _timeline_match(declared: str, recorded) -> bool:
    """Whether a pin's `timeline` scope names this card's placement.

    The final reel name, or a container built from it: a rebuild
    stages into "<final> (rebuild staging)" and a beside-build into
    "<final> (<suffix>)", so the parenthesised tail is the build's,
    never the reel's.  The paren boundary is the whole check - "Reel 1"
    is not a prefix of "Reel 13 - ..." here.  A card whose binding
    names no timeline matches no timeline scope: a pin about one
    placement does not move cards of unknown placement (fail-closed).
    """
    want = str(declared or "").strip().lower()
    got = str(recorded or "").strip().lower()
    if not want or not got:
        return False
    if want == got:
        return True
    return (got.startswith(want + " (") or want.startswith(got + " ("))


def matches(segment: dict, scope: dict) -> bool:
    """Whether one rendered caption segment is in a pin's scope."""
    binding = _binding(segment)
    if "timeline" in scope:
        if not _timeline_match(scope["timeline"],
                               binding.get("timeline")):
            return False
    if "speaker" in scope:
        speaker = binding.get("speaker")
        if speaker is None or str(speaker).strip().lower() != str(
                scope["speaker"]).strip().lower():
            return False
    if "source_clip_id" in scope:
        if not _slug_match(scope["source_clip_id"],
                           binding.get("source_clip_id")):
            return False
    start = binding.get("source_start")
    needs_start = any(k in scope for k in
                      ("source_start", "source_start_at_or_after",
                       "source_start_before"))
    if needs_start:
        if start is None:
            return False
        start = float(start)
        if "source_start" in scope and abs(
                start - float(scope["source_start"])) > SPAN_TOLERANCE_SECONDS:
            return False
        if "source_start_at_or_after" in scope and start < float(
                scope["source_start_at_or_after"]) - SPAN_TOLERANCE_SECONDS:
            return False
        if "source_start_before" in scope and start >= float(
                scope["source_start_before"]) - SPAN_TOLERANCE_SECONDS:
            return False
    return True


# ── Applying: the segments seam ────────────────────────────────────

def apply_pins(segments, pins, fps: float) -> tuple:
    """Move and trim caption cards per the declared pins.

    `segments` are the rendered caption segments
    (`reel_build.reel_subtitle_segments`), each carrying
    `timeline_start` / `timeline_end` in REEL seconds and the
    `binding` a pin addresses.  Returns
    `(segments, applied, short, stale)`; the input dicts are not
    mutated, and the returned list preserves order and any list
    subclass attributes the caller attached
    (`_SegmentsWithEntries.caption_entries`).

    `short` names cards a pin left under the craft floor - reported
    with their new length, never refused (see the module docstring).
    `stale` names pins that matched nothing - except a pin whose
    `timeline` scope names another reel's placement, which is out of
    scope on this reel rather than stale, and stays silent.
    """
    from library.tools.frame_utils import span_frames

    if not pins:
        return segments, [], [], []
    out = [dict(s) for s in (segments or [])]
    applied, short, stale = [], [], []
    for pin in pins:
        scope = pin["scope"]
        if "timeline" in scope and not any(
                _timeline_match(scope["timeline"],
                                _binding(s).get("timeline")) for s in out):
            # Another reel's pin, not a stale one: a pin scoped to Reel
            # 13's placement evaluated on Reel 23's cards is out of
            # scope, not evidence its speech was reworded. Silent here;
            # on its own reel a scope that matches no card still
            # reports STALE below.
            continue
        offset = int(pin.get("offset_frames", 0))
        head = int(pin.get("head_frames", 0))
        tail = int(pin.get("tail_frames", 0))
        hits = 0
        for segment in out:
            if not matches(segment, scope):
                continue
            hits += 1
            start, end = span_frames(segment["timeline_start"],
                                     segment["timeline_end"], fps)
            new_start = start + offset + head
            new_end = end + offset - tail
            if new_start < 0:
                raise CaptionTimingError(
                    f"REFUSING to build: a caption pin moves "
                    f"{segment.get('segment_id', '?')!r} to frame "
                    f"{new_start}, before the reel begins. A card off "
                    f"the head of the timeline is placed nowhere, and "
                    f"placing it at zero would silently change the "
                    f"pin. Original request: {pin['reason']}")
            if new_end <= new_start:
                raise CaptionTimingError(
                    f"REFUSING to build: a caption pin trims "
                    f"{segment.get('segment_id', '?')!r} to "
                    f"{new_end - new_start} frames, so it draws "
                    f"nothing. A card trimmed out of existence is a "
                    f"card removed, which is a different decision - "
                    f"say that one instead. Original request: "
                    f"{pin['reason']}")
            segment["timeline_start"] = new_start / fps
            segment["timeline_end"] = new_end / fps
            record = {"segment_id": segment.get("segment_id", "?"),
                      "was": [start, end], "now": [new_start, new_end],
                      "offset_frames": offset, "head_frames": head,
                      "tail_frames": tail, "reason": pin["reason"]}
            applied.append(record)
            if (new_end - new_start) / fps < SHORT_CARD_SECONDS:
                short.append({**record,
                              "seconds": round((new_end - new_start) / fps, 3)})
        if not hits:
            stale.append({
                "kind": "caption_timing", "scope": scope,
                "reason": (
                    f"STALE: a caption timing pin scoped to {scope} "
                    f"matched no card on this reel. The speech it "
                    f"addresses was reworded, re-grouped or re-cut out, "
                    f"so nothing was moved. Original request: "
                    f"{pin['reason']}")})
    # The caller's list subclass carries the plan entries the caption
    # hash digests; rebuilding a plain list here would drop them
    # silently three functions away from the cause.
    if isinstance(segments, list) and type(segments) is not list:
        try:
            # Constructed EMPTY and extended: `_SegmentsWithEntries`
            # takes no iterable, and a subclass that will not rebuild
            # from this list degrades to a plain one rather than
            # taking the build down.
            carried = type(segments)()
            carried.extend(out)
        except TypeError:
            return out, applied, short, stale
        for attribute in ("caption_entries", "spine"):
            if hasattr(segments, attribute):
                setattr(carried, attribute, getattr(segments, attribute))
        return carried, applied, short, stale
    return out, applied, short, stale


# ── The grading side: spans, shifted back to the unpinned frame ──

def grade_spans(spans, pins, fps: float, timeline: str = "") -> list:
    """Card spans in the frame the conformance gate grades them in.

    The build moves pinned cards (`apply_pins`); the word-level gate
    (F25) compares placed cards against strict word timings and knows
    no pins, so it refuses the shift as dropped/added words. Measured
    2026-09-20 on Reel 13: the captain's 09-11 hand move (+7 frames on
    the closing cards) reads as ten word_mismatch errors on a reel
    whose words and cards agree exactly. A gate that fails the
    captain's recorded decision is no more coverage than one that
    cannot fail (AGENTS.md 10.4) - so pinned spans grade where they
    would sit unpinned, matched with the owner's own `matches` over
    the cards' recorded bindings, the same predicate the build moved
    them with. A reel cannot be built to one rule and checked
    against another.

    `spans` are `{card, reel_start, reel_end, binding?}` in reel
    seconds; `binding` is the card's recorded placement record
    (`subtitle_segment_id.SEGMENT_BINDING_KEYS` - the props artefact
    behind the placed card carries them) without the timeline, which
    `timeline` supplies: the reel being graded, final name, since a
    pin scopes the placement the hand edit was measured on and
    `_timeline_match` reads through the build's parenthesised
    staging tail. Returns copies; the input is never mutated.

    Only the span moves: the karaoke windows keep their placed times,
    so a finding that remains (a genuine drop past the pins) still
    reports where the viewer sees it. A card no pin matches grades
    exactly where it sits; a pin that matches no card changes
    nothing here (the build already reports it stale).
    """
    if not pins or not spans:
        return [dict(span) for span in spans or []]
    out = []
    for span in spans:
        binding = dict(span.get("binding") or {})
        binding["timeline"] = timeline or None
        probe = {"binding": binding}
        start_shift = 0
        end_shift = 0
        for pin in pins or []:
            scope = (pin.get("scope") or {}) if isinstance(pin, dict) \
                else {}
            if not matches(probe, scope):
                continue
            offset = int(pin.get("offset_frames") or 0)
            head = int(pin.get("head_frames") or 0)
            tail = int(pin.get("tail_frames") or 0)
            start_shift += offset + head
            end_shift += offset - tail
        graded = dict(span)
        if start_shift:
            graded["reel_start"] = span["reel_start"] - start_shift / fps
        if end_shift:
            graded["reel_end"] = span["reel_end"] - end_shift / fps
        out.append(graded)
    return out


# ── The plan side: entries, joined to the spine that timed them ────

def block_bindings(spine, timeline: str = "") -> dict:
    """Each spine block's source audio, keyed by block position.

    The same fields a rendered segment's `binding` carries
    (`library/tools/subtitle_segment_id.SEGMENT_BINDING_KEYS`): a pin
    addresses the SPEECH a card captions, and a reel's own spine is
    where that is recorded. One join, read by the builder when it
    records what it placed and by the conformance verifier when it
    re-derives the plan - a reel cannot be built to one rule and
    checked against another.

    `timeline` is the placement these bindings serve - the reel name
    the caller is building or grading. A pin scoping `timeline` is
    evaluated against it; left out, no placement is named and such a
    pin matches nothing here (fail-closed: a pin about one placement
    does not move cards of unknown placement).
    """
    out = {}
    for block in ((spine or {}).get("structure") or []):
        position = block.get("position")
        if position is None:
            continue
        out[str(position)] = {
            "speaker": (block.get("speaker")
                        or (block.get("content") or {}).get("speaker")),
            "source_clip_id": block.get("clip_id"),
            "source_start": block.get("source_start"),
            "source_end": block.get("source_end"),
            "timeline": timeline if timeline else None,
        }
    return out


def retime_entries(entries, spine, pins, fps: float,
                   timeline: str = "") -> tuple:
    """Apply the pins to step 4.01's own caption ENTRIES.

    The rendered segment carries one BLOCK; an entry is one CARD inside
    it, and the pins move the block, so every card in a pinned block
    moves with it. Returns the same four values `apply_pins` does.

    The builder digests these entries into the caption provenance hash
    (`plan_provenance.caption_content_hash`), which answers "what was
    said and WHEN in the reel" - so the hash must describe the pinned
    timings, or the verifier re-deriving them reads a pin as a changed
    grouping.

    `timeline` is the placement being hashed or graded - the reel name
    the caller is building or checking. It is what a `timeline`-scoped
    pin is evaluated against; without it such a pin matches nothing.
    """
    if not pins or not entries:
        return entries, [], [], []
    bindings = block_bindings(spine, timeline=timeline)
    probes = []
    for index, entry in enumerate(entries):
        position = entry.get("spine_block_position")
        probes.append({
            "segment_id": entry.get("id") or f"entry[{index}]",
            "binding": bindings.get(
                "" if position is None else str(position), {}),
            "timeline_start": entry["timeline_start"],
            "timeline_end": entry["timeline_end"]})
    moved, applied, short, stale = apply_pins(probes, pins, fps)
    out = []
    for entry, probe in zip(entries, moved):
        row = dict(entry)
        row["timeline_start"] = probe["timeline_start"]
        row["timeline_end"] = probe["timeline_end"]
        out.append(row)
    return out, applied, short, stale


# ── Migrating: re-basing pins after an ingest retiming ──

#: Scope keys that name a second into the source audio. A pin
#: addresses cards by the speech they caption, and the speech's
#: recorded position moves when the instrument timing it changes -
#: so these are the keys a rebase shifts, and the only ones.
SOURCE_TIME_SCOPE_KEYS = ("source_start", "source_start_at_or_after",
                          "source_start_before")


def rebase_pins(pins, delta_seconds: float, *, reason: str) -> list:
    """Shift every source-time scope in `pins` by `delta_seconds`.

    When ingest is retimed - words that sat 40-55 ms late now sitting
    where the new instrument heard them - a pin scoped to the old
    positions detaches: it matches a different set of cards, or none,
    and the no-match case reports STALE for a trim that is still
    wanted. The fix the MFA adoption's landing condition names is to
    migrate the pins by the measured per-file delta, never to leave
    them to report stale.

    `delta_seconds` is NEW minus OLD in source seconds: positive when
    the new instrument hears later than the old one. Every
    `SOURCE_TIME_SCOPE_KEYS` entry moves by it, rounded to the
    millisecond the bindings are written at; frame adjustments
    (`offset_frames`, `head_frames`, `tail_frames`) are relative
    moves and travel unchanged. A pin with no source-time scope has
    nothing positional to shift and is returned as-is. Each moved
    pin's `reason` gains a bracketed note naming the migration, so
    the hand edit's own account stays attached to the numbers.

    The output is validated like a fresh declaration: a shift that
    drives a scope before the source start, or anything else the
    move breaks, REFUSES rather than writing a pin that addresses
    nothing.
    """
    if not isinstance(reason, str) or not reason.strip():
        raise CaptionTimingError(
            "a pin rebase records no reason. A migration nobody can "
            "attribute is how a hand edit gets lost twice.")
    if not pins:
        return []
    rebased = []
    for pin in pins or []:
        scope = dict(pin.get("scope") or {})
        moves = [key for key in SOURCE_TIME_SCOPE_KEYS if key in scope]
        if not moves:
            rebased.append(dict(pin))
            continue
        moved = dict(pin)
        moved_scope = dict(scope)
        for key in moves:
            shifted = round(float(scope[key]) + delta_seconds, 3)
            if shifted < 0:
                raise CaptionRebaseRefused(
                    f"refusing to rebase {key} {scope[key]!r} by "
                    f"{delta_seconds:+.3f}s: it lands before the "
                    f"source starts",
                    f"a pin that addresses no audio addresses no card "
                    f"(original request: {pin.get('reason')})",
                    "say what the pin should become instead - a scope "
                    "inside the source, or drop the pin if the words "
                    "it addressed are gone")
            moved_scope[key] = shifted
        moved["scope"] = moved_scope
        moved["reason"] = (f"{pin.get('reason')} [rebased "
                           f"{delta_seconds:+.3f}s: {reason}]")
        rebased.append(moved)
    return validate_pins(rebased)


def report(applied, short, stale) -> None:
    """Print every verdict the way the other pin owners print theirs."""
    for row in applied:
        print(f"  Caption timing: {row['segment_id']} "
              f"{row['was'][0]}-{row['was'][1]} -> "
              f"{row['now'][0]}-{row['now'][1]} "
              f"(offset {row['offset_frames']:+d}f, head "
              f"{row['head_frames']}f, tail {row['tail_frames']}f) - "
              f"{row['reason']}", flush=True)
    for row in short:
        print(f"  Caption timing: {row['segment_id']} is now "
              f"{row['seconds']:.3f}s, under the {SHORT_CARD_SECONDS}s "
              f"craft floor - placed as declared, reported not refused",
              file=sys.stderr, flush=True)
    for row in stale:
        print(f"  {row['reason']}", file=sys.stderr, flush=True)
