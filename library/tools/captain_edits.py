"""The captain's edits, as small readable deltas that survive a rebuild.

The captain, 2026-09-09: *"if i ask to remove a piece of the video and
replace it with something else, and then ask you to rebuild the timeline,
those changes should persist"*. And: *"going into the subtitles and making
corrections that i ask of you so it's there"*.

What already worked is whole-value supply (`library/tools/external_inputs.py`):
handing over the ENTIRE `speech_sequence` to remove one fragment. That is
unreadable, unreviewable, and goes stale the moment anything upstream
legitimately changes. An edit here is a DELTA - one anchor, one decision,
in the captain's own words - expressed against something STABLE.

What the delta is anchored to, and why
--------------------------------------
The anchor is the SPOKEN WORDS (`anchor_phrase`). Three candidates break:

- a FRAME NUMBER is not stable across a rebuild: re-transcription moves
  every boundary, and the marker work already refuses to delete by a
  moved frame for exactly this reason. An edit carrying `anchor_frame`
  (or any frame field) is REFUSED at write time, not lost at rebuild.
- a SOURCE TIMECODE in the original MXF is stable until the transcript
  re-segments the passage around it; then it names the wrong seconds.
- a SEGMENT IDENTITY (`segment_id`, block position) is pipeline-assigned
  and renumbers whenever anything upstream legitimately changes.

Spoken words survive all three: a rebuild that re-transcribes, re-cuts
and renumbers still says the same words in the same order. What breaks
it is honest and named: a re-transcription that REWORDS the passage, or
a fragment that is already gone. That edit reports STALE - loudly, on
stderr and in the step output - and never silently vanishes.

Correction versus edit
----------------------
The sibling lane (`transcript_corrections`, in flight) owns CORRECTIONS:
a fact about the world that applies everywhere and forever ("the audio
this project transcribed as Lucy is Lucie"). This module owns EDITS: a
decision about THIS ONE PIECE ("drop the 'so what do they' fragment at
22 seconds", "this caption reads X"). A correction lives in
`learned_context/` and is applied at the transcript root; an edit lives
in `<project>/external/captain_edits.json` and is applied where the
decision lands (spine blocks, caption cards). Collapsing them would
either scope a world-fact down to one card or promote a one-piece
decision into every future video - both wrong, so both exist.

Never picture without sound
---------------------------
Removing speech moves everything after it. `apply_drop_fragments`
re-derives every later block's `timeline_start`/`timeline_end` (and
frame fields) from the surviving durations, so picture and sound move
together. A caption fix changes TEXT ONLY - timings untouched - and
re-derives what depends on the text (`word_count`, `emphasis_words`).

Redrawing a closer
-------------------
Keep exclusions can only REMOVE seconds, so no existing mechanism can
EXTEND a closer backwards - the shared call to action [321.61, 328.23]
opened mid-sentence ("we're calling the lucy visibility system ...")
and the captain ruled 2026-09-10 it must open on "it's exactly why
we've been building this platform we're calling ...", at 319.358 with
the end fixed at 328.231. That pin is a `redraw_closer` edit, and it
lives HERE rather than in `transcript_corrections` for one reason: a
correction is a fact about the world, everywhere and forever ("the
audio transcribed as Lucy is Lucie"), while where one shared closer
starts is a decision about THIS ONE PIECE - an edit, anchored to
spoken words like every other edit here, refusing timecode pins by
construction. A closer pinned to 319.358 breaks the moment anything
upstream re-times; the words survive.

A pin names both ends in words: `anchor_phrase` (what the closer must
open on) and `from_phrase` (what it opens on now). The second is
REQUIRED, not courtesy: without it the pin would redraw every closer
in the batch, including invitations the captain never heard, and the
only thing stopping that overreach would be remembering not to. The
shared span is identified by what it SAYS, never by its seconds.

Two layers enforce it, the same shape keep exclusions take: step 3.04
redraws regenerated proposals, and the reel build redraws approved
moments in memory (the file keeps what the captain ruled on, exactly
like the word-edge repair beside it). Applying a recorded pin to an
approved moment is obedience, not re-decision - the approved-moment
guard stops the ENGINE re-deciding a range under the captain, and the
pin IS the captain's judgement with their reason. The extension adds
seconds where an exclusion only removes them, so the added seconds are
checked like a new span: real speech inside, whole segments at both
edges (the end never moves), and no overlap with the reel's own body,
which would play those seconds twice. Anything failing that is
reported LOUDLY and that reel keeps its span.

`tests/test_closer_redraw.py`.

A hand move in the Inspector
----------------------------
The captain drags a clip's Position X by hand (Reel 09, 2026-09-10:
Akshita's clip from the pipeline's Pan 14 to Pan -35) and the next
rebuild re-aims the punch-in onto the measured subject, throwing the
hand move away. That pin is a `transform_override` edit: the same
word anchor (the words the moved shot speaks), naming one Edit-page
transform property (`Pan`, `Tilt`, `ZoomX`, `ZoomY` - what the build
sets and the Inspector shows as Position/Zoom) and the number it must
hold. Zoom must be positive; Pan/Tilt must sit inside what Resolve
holds on a 1080x1920 timeline (`PAN_TILT_RAIL_1080X1920`, measured in
`library/tools/tight_box.py`), because
past it Resolve clamps silently and the held value would not be the
recorded one.

An override may carry `reel`: the timeline name it holds on, matched
by prefix (the `reel_ending` convention). A shot four reels share
speaks one anchor on all four; the per-reel Pan is the same words
with a reel scope, holding there and reporting routine STALE
elsewhere. No `reel` holds everywhere, exactly as before - and where
a scoped override and an unscoped one meet on one span, the scoped
one wins that span while the general one still holds everywhere
else.

The build applies overrides AFTER aiming the punch-in, so the held
value is the captain's, and re-proves coverage (`assert_punch_took`):
an override that uncovered an edge raises rather than shipping black.
An override matching no placed span reports STALE like every other
kind. `tests/test_transform_override.py`.

Recording a decision: the one route
------------------------------------
`python3 -m library.tools.captain_edits <project> <verb>`:

- `list` (the default): what is in force, in plain language.
- `record-closer --anchor ... --from ... --reason ...`: pin a closer.
- `record-transform --anchor ... --property Pan --value -35
  --reason ...`: the typed fallback for a decision settled in words.
- `record-retime --anchor ... --edge head --reason ...`: the typed
  fallback for a hand trim - one placed span's head (or tail) onto
  the anchor's own word edge. Trims only. The write stamps where
  the anchor resolves now (`recorded_edge`), so a later
  re-transcription that moves those words reports DRIFTED
  pre-build rather than following them silently.
- `capture-transform --reel 9 --timeline 'Reel 09 - ...' --words ...
  [--property Pan] [--reason ...]`: read the value out of the LIVE
  Resolve timeline - the hand move, which exists nowhere else - and
  record what Resolve holds. Footage items only (the master snapshot
  decides membership, never an extension guess); ambiguity - the
  words twice on the reel, two items on one track, stacked angles
  without `--track` - refuses, naming what matched.

Every record refuses before writing: structurally, and against the
measured transcript where one is on file (a typo fails here, not on
the next build). A re-ruling of the same decision SUPERSEDES it; an
exact duplicate is refused as already in force. No second store
beside this one: a placement value fits here because the ANCHOR is
the same stable thing every other kind anchors to - the spoken
words - and only the payload differs (a number held, not a range
redrawn or a fragment dropped).
"""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

from library.tools.resolve_lock import under_lease

CAPTAIN_EDITS_KEY = "captain_edits"
"""The state key, and the file name: `<project>/external/captain_edits.json`."""

KINDS = ("caption_fix", "drop_fragment", "redraw_closer",
          "transform_override", "span_retime")
"""The complete vocabulary. `caption_fix` rewrites caption text;
`drop_fragment` removes the speech (and so the picture) that says it;
`redraw_closer` moves a shared closer's start to the anchor's words,
end fixed; `transform_override` holds one Edit-page transform property
(Pan, Tilt, ZoomX, ZoomY) at the captain's value on every shot that
speaks the anchor - or, with `reel`, on that reel's shots alone - a
hand move in the Inspector that a rebuild would
otherwise throw away; `span_retime` moves one placed span's head or
tail to the anchor's own word edge - a hand trim on the timeline
(Reel 13's trims, destroyed twice by rebuilds) that recomputation
otherwise throws away with it."""


TRANSFORM_PROPERTIES = ("Pan", "Tilt", "ZoomX", "ZoomY")
"""The properties a `transform_override` may name: the Edit-page
transform the build itself sets (the punch-in), which is what the
Inspector shows as Position and Zoom. Anything else - a Fusion
Center, an opacity, a volume - is another mechanism's business and is
refused here rather than half-applied."""


PAN_TILT_RAIL_1080X1920 = 3840.0
"""What Resolve holds for |Pan| and |Tilt| on a 1080x1920 timeline.

Measured on the live timeline 2026-09-09 (`library/tools/tight_box.py`):
37 of 39 caption items pinned at exactly Tilt -3840.0 across 17 box
geometries - a constant in PROPERTY space, not a clip-relative cap, so
it binds footage clips the same as overlays. The retired carriage
believed 4320; the measured bracket is [3366.4, 4316.1] and 3840 lies
inside it. The measurement is on Tilt; Pan is taken symmetric (same
property space, and every recorded Pan sits two orders of magnitude
inside the rail either way). Past this Resolve returns True and holds
the clamp, so an override past it is REFUSED rather than recorded -
a recorded clamp is a pin the build cannot hold.
"""


class CaptainEditError(ValueError):
    """An edit that cannot be what it claims to be, refused by name."""


# ── The anchor ───────────────────────────────────────────────────────

def normalize(text: str) -> str:
    """Spoken words, comparable across a rebuild: lower-cased,
    punctuation-stripped, whitespace-collapsed."""
    return re.sub(r"\s+", " ",
                  re.sub(r"[^\w\s']", "", (text or "").lower())).strip()


def _tokens(text: str) -> list:
    return normalize(text).split()


# ── Validation: an edit is small, readable, and anchored to words ────

def validate_edits(value) -> list:
    """The structural half of the external check. Raises
    `CaptainEditError` naming what is wrong; returns the edits."""
    if not isinstance(value, list) or not value:
        raise CaptainEditError(
            "captain_edits must be a non-empty list of edits. An empty "
            "one is the absence of edits, not edits supplied from "
            "outside - leave the file out instead.")
    for index, edit in enumerate(value):
        label = f"captain_edits[{index}]"
        if not isinstance(edit, dict):
            raise CaptainEditError(f"{label} is not an object")
        kind = edit.get("kind")
        if kind not in KINDS:
            raise CaptainEditError(
                f"{label}.kind is {kind!r}. Known kinds: "
                f"{', '.join(KINDS)}.")
        anchor = edit.get("anchor_phrase")
        if not isinstance(anchor, str) or not normalize(anchor):
            raise CaptainEditError(
                f"{label}.anchor_phrase names no spoken words. An edit "
                f"is anchored to what was SAID - that is what survives "
                f"a rebuild.")
        for field in ("anchor_frame", "anchor_frames", "frame",
                      "timeline_start", "timeline_end"):
            if field in edit:
                raise CaptainEditError(
                    f"{label} carries {field!r}: a frame number (or a "
                    f"timeline second) is not stable across a rebuild - "
                    f"re-transcription moves every boundary. Anchor to "
                    f"the spoken words instead.")
        if kind == "caption_fix":
            replacement = edit.get("replacement")
            if not isinstance(replacement, str) or not replacement.strip():
                raise CaptainEditError(
                    f"{label} is a caption_fix with no 'replacement'. "
                    f"Name what the caption should read.")
        if kind == "redraw_closer":
            opening = edit.get("from_phrase")
            if not isinstance(opening, str) or not normalize(opening):
                raise CaptainEditError(
                    f"{label} is a redraw_closer with no 'from_phrase'. "
                    f"A pin must name WHICH closer it moves, in the "
                    f"words that closer opens on now - without it the "
                    f"pin would redraw every closer in the batch, "
                    f"including invitations the captain never heard.")
            for field in ("new_start", "new_end", "from_start",
                          "from_end", "start", "end"):
                if field in edit:
                    raise CaptainEditError(
                        f"{label} carries {field!r}: a closer pinned to "
                        f"a timecode breaks the moment anything upstream "
                        f"re-times. Name the opening in spoken words "
                        f"(`anchor_phrase`, `from_phrase`) instead.")
        if kind == "transform_override":
            prop = edit.get("property")
            if prop not in TRANSFORM_PROPERTIES:
                raise CaptainEditError(
                    f"{label} names property {prop!r}. A transform "
                    f"override holds one Edit-page transform the build "
                    f"itself sets: {', '.join(TRANSFORM_PROPERTIES)}.")
            number = edit.get("value")
            if (isinstance(number, bool)
                    or not isinstance(number, (int, float))
                    or math.isnan(number)
                    or number in (float("inf"), float("-inf"))):
                raise CaptainEditError(
                    f"{label} carries value {number!r}: a transform "
                    f"override names the number {prop} must hold.")
            if prop in ("ZoomX", "ZoomY") and not number > 0:
                raise CaptainEditError(
                    f"{label} wants {prop} {number!r}: a zoom of zero or "
                    f"less draws nothing - the picture would vanish "
                    f"rather than move.")
            if prop in ("Pan", "Tilt"):
                if abs(number) > PAN_TILT_RAIL_1080X1920:
                    raise CaptainEditError(
                        f"{label} wants {prop} {number:g}: Resolve holds "
                        f"only +- {PAN_TILT_RAIL_1080X1920:.0f} on a "
                        "1080x1920 timeline "
                        "and clamps past it silently, so the held value "
                        "would not be the recorded one. Aim inside what "
                        "Resolve holds.")
            reel = edit.get("reel")
            if reel is not None and (
                    not isinstance(reel, str) or not reel.strip()):
                raise CaptainEditError(
                    f"{label} carries reel={reel!r}: a per-reel scope "
                    f"names the reel's timeline name (matched by "
                    f"prefix, the `reel_ending` convention), or is left "
                    f"out - an override with no `reel` holds on every "
                    f"reel speaking the anchor.")
        if kind == "span_retime":
            edge = edit.get("edge")
            if edge not in ("head", "tail"):
                raise CaptainEditError(
                    f"{label} names edge {edge!r}. A span retime moves "
                    f"one placed span's head (its opening) or its tail "
                    f"(its close) to the anchor's own word edge - name "
                    f"which one.")
            for field in ("new_start", "new_end", "from_start",
                          "from_end", "start", "end"):
                if field in edit:
                    raise CaptainEditError(
                        f"{label} carries {field!r}: a trim pinned to "
                        f"a timecode breaks the moment anything upstream "
                        f"re-times. Name the edge in spoken words "
                        f"(`anchor_phrase`) with `edge` head/tail "
                        f"instead.")
            recorded = edit.get("recorded_edge")
            if recorded is not None and (
                    isinstance(recorded, bool)
                    or not isinstance(recorded, (int, float))
                    or not math.isfinite(recorded) or recorded < 0):
                raise CaptainEditError(
                    f"{label} carries recorded_edge {recorded!r}: the "
                    f"baseline is the anchor's word-edge time in master "
                    f"seconds as resolved when the trim was recorded - "
                    f"a real second, never a pin. The build places from "
                    f"the words, not from this number.")
        reason = edit.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise CaptainEditError(
                f"{label} carries no 'reason'. The reason is the "
                f"captain's own words, and it is what the plain-language "
                f"listing reads back - an edit nobody can read back is "
                f"not reviewable.")
    return value


# ── Reading: the file the captain writes ─────────────────────────────

def edits_path(project_folder) -> Path:
    from library.tools.project_layout import Area, ProjectLayout

    return Path(ProjectLayout(str(project_folder)).read_dir(
        Area.EXTERNAL_STATE)) / f"{CAPTAIN_EDITS_KEY}.json"


def load_edits(project_folder) -> list:
    """The verified edits in force, or [] where the captain wrote none.

    Reads the external file and validates it structurally, PLUS the
    edit ledger's rows of the five plan-level kinds
    (`library/tools/edit_ledger.py`) as one merged view - so every
    existing applier replays ledger rows with no second
    implementation. Drift against current speech is NOT judged here
    - the file must be readable before any spine exists (a first
    run), and drift is where it is LOUD: the external check refuses
    it pre-run where the spine is on file, and apply time reports it
    STALE otherwise."""
    if not project_folder:
        return []
    try:
        path = edits_path(project_folder)
    except (KeyError, ValueError):
        return []
    edits: list = []
    if path.is_file():
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CaptainEditError(
                f"{path} cannot be read: {exc}") from exc
        if not isinstance(document, dict) or "value" not in document:
            raise CaptainEditError(
                f"{path.name} must be an object with 'key', 'source' and "
                f"'value' (library/tools/external_inputs.py).")
        if document.get("key") != CAPTAIN_EDITS_KEY:
            raise CaptainEditError(
                f"{path.name} declares key {document.get('key')!r}; the file "
                f"name IS the state key, so it must be {CAPTAIN_EDITS_KEY!r}.")
        edits = validate_edits(document["value"])
    try:
        from library.tools import edit_ledger as _ledger
        edits = edits + _ledger.project_onto_captain_edits(
            _ledger.load_rows(project_folder))
    except _ledger.EditLedgerError as exc:
        raise CaptainEditError(
            f"edit_ledger cannot be read: {exc}. A recorded decision "
            f"the build cannot read must refuse, never build silently "
            f"past it.") from exc
    if not edits:
        return []
    return validate_edits(edits)


# ── Caption fixes: text only, timings never move ─────────────────────

def _fix_span(text: str, anchor: str, replacement: str) -> tuple:
    """Replace the anchor's word-run inside `text`, preserving the
    surrounding casing style. Returns `(new_text, count)`; 0 where the
    anchor's words do not occur in order (punctuation may intervene)."""
    anchor_tokens = _tokens(anchor)
    if not anchor_tokens:
        return text, 0
    words = text.split()
    normed = [normalize(w) for w in words]
    n = len(anchor_tokens)
    count = 0
    for i in range(len(words) - n + 1):
        if normed[i:i + n] == anchor_tokens:
            words[i:i + n] = [replacement] if n == 1 else (
                [replacement] + [""] * (n - 1))
            normed[i:i + n] = [""] * n
            count += 1
    return " ".join(w for w in words if w != ""), count


def apply_caption_fixes(entries: list, edits: list) -> tuple:
    """Rewrite caption text, word-anchored. Returns
    `(entries, applied, stale)`: `applied` names each edit and how many
    cards it touched; `stale` carries the edits whose anchor matched
    nothing, with the reason saying the edit no longer applies.

    Timings are NEVER touched - a text change that moved the
    picture/sound would make the two disagree. What depends on the text
    (`word_count`, `emphasis_words`) is re-derived on every touched
    card."""
    fixes = [e for e in (edits or []) if e.get("kind") == "caption_fix"]
    applied, stale = [], []
    for edit in fixes:
        anchor = edit["anchor_phrase"]
        touched = 0
        for entry in entries:
            if not isinstance(entry.get("text"), str):
                continue
            new_text, n = _fix_span(entry["text"], anchor,
                                    edit["replacement"])
            if n:
                entry["text"] = new_text.strip()
                entry["word_count"] = len(entry["text"].split())
                try:
                    from library.steps.step_4_01_plan_subtitles.step import (
                        identify_emphasis_words)
                    entry["emphasis_words"] = identify_emphasis_words(
                        entry["text"])
                except ImportError:
                    pass
                touched += n
        if touched:
            applied.append({"anchor_phrase": anchor,
                            "replacement": edit["replacement"],
                            "cards_touched": touched,
                            "reason": edit.get("reason", "")})
        else:
            stale.append({"kind": "caption_fix",
                          "anchor_phrase": anchor,
                          "reason": (
                              f"STALE: caption fix for {anchor!r} no "
                              f"longer applies - those words are in no "
                              f"caption card. The speech was reworded, "
                              f"re-transcribed, or already removed. "
                              f"Original request: "
                              f"{edit.get('reason', '')}".strip())})
    return entries, applied, stale


# ── Drops: speech goes, everything after moves with it ───────────────

def _block_text(block: dict) -> str:
    content = block.get("content") or {}
    return str(content.get("text", "") or "")


def apply_drop_fragments(blocks: list, edits: list) -> tuple:
    """Remove spine blocks whose speech contains the anchor. Returns
    `(blocks, applied, stale)`.

    Every surviving block's `timeline_start`/`timeline_end` (and frame
    fields) is re-derived from the surviving durations in order, so
    everything after the cut moves EARLIER by exactly the removed
    seconds - picture and sound move together, and no caption pinned to
    an old time becomes a lie. Non-speech blocks (music beds, cards)
    are never dropped: an anchor is spoken words, and they speak none.
    A drop whose anchor matches nothing reports STALE rather than
    vanishing."""
    drops = [e for e in (edits or []) if e.get("kind") == "drop_fragment"]
    applied, stale = [], []
    surviving = list(blocks or [])
    for edit in drops:
        anchor_tokens = _tokens(edit["anchor_phrase"])
        struck = [b for b in surviving
                  if b.get("block_type") in ("hook", "speech")
                  and _contains_run(_tokens(_block_text(b)),
                                    anchor_tokens)]
        if not struck:
            stale.append(
                {"kind": "drop_fragment",
                 "anchor_phrase": edit["anchor_phrase"],
                 "reason": (
                     f"STALE: drop of {edit['anchor_phrase']!r} no longer "
                     f"applies - those words are in no spine block. The "
                     f"passage was reworded, already removed, or never "
                     f"reached this reel. Original request: "
                     f"{edit.get('reason', '')}".strip())})
            continue
        struck_ids = {id(b) for b in struck}
        surviving = [b for b in surviving if id(b) not in struck_ids]
        removed = sum(float(b.get("duration_seconds", 0) or 0)
                      for b in struck)
        applied.append(
            {"anchor_phrase": edit["anchor_phrase"],
             "blocks_removed": [b.get("position") for b in struck],
             "seconds_removed": round(removed, 3),
             "reason": edit.get("reason", "")})
    _rederive_timings(surviving)
    return surviving, applied, stale


def _contains_run(haystack: list, needle: list) -> bool:
    if not needle:
        return False
    return any(haystack[i:i + len(needle)] == needle
               for i in range(len(haystack) - len(needle) + 1))


def _rederive_timings(blocks: list) -> None:
    """Lay the surviving blocks end to end from zero. In place."""
    cursor = 0.0
    for block in blocks:
        dur = float(block.get("duration_seconds", 0) or 0)
        block["timeline_start"] = round(cursor, 3)
        block["timeline_end"] = round(cursor + dur, 3)
        if "timeline_start_frame" in block or "timeline_end_frame" in block:
            try:
                from library.tools.frame_utils import seconds_to_frame
                import math
                fps = 30.0
                block["timeline_start_frame"] = seconds_to_frame(
                    block["timeline_start"], fps)
                block["timeline_end_frame"] = seconds_to_frame(
                    block["timeline_end"], fps)
                block["duration_frames"] = (
                    block["timeline_end_frame"]
                    - block["timeline_start_frame"])
            except ImportError:
                pass
        cursor += dur


# ── Closer redraws: a shared start, pinned to spoken words ──────────

def _word_stream(transcript: dict) -> list:
    """Every timed word in transcript order: `(token, start, end)`.

    Across segments, because an anchor ("... this platform we're
    calling ...") may start in one ASR row and finish in the next.
    Untimed words cannot place a boundary and are not offered as
    evidence that one lands anywhere - the same rule the proposal gate
    applies.
    """
    stream = []
    for segment in (transcript or {}).get("segments") or ():
        for word in segment.get("words") or ():
            if not word.get("timed"):
                continue
            try:
                start, end = float(word["start"]), float(word["end"])
            except (KeyError, TypeError, ValueError):
                continue
            token = normalize(str(word.get("word") or ""))
            if token and end > start:
                stream.append((token, start, end))
    return stream


def _run_starts(stream: list, phrase: str) -> list:
    """Indices where `phrase` occurs as an ordered token run."""
    needle = _tokens(phrase)
    if not needle:
        return []
    haystack = [token for token, _, _ in stream]
    n = len(needle)
    return [i for i in range(len(haystack) - n + 1)
            if haystack[i:i + n] == needle]


def _resolve_single_edge(transcript: dict, anchor: str,
                         edge: str | None) -> float | None:
    """The anchor's word-edge time where it occurs exactly once.

    `head` reads the first word's start, anything else the last
    word's end - the same edges `match_span_retimes` trims onto.
    None where the anchor occurs zero times (the caller refuses
    that first) or more than once (no single baseline to stamp).
    Pipeline millisecond precision, like every other master second.
    """
    try:
        stream = _word_stream(transcript or {})
        at = _run_starts(stream, anchor)
        if len(at) != 1:
            return None
        tokens = _tokens(anchor)
        if edge == "head":
            return round(float(stream[at[0]][1]), 3)
        return round(float(stream[at[0] + len(tokens) - 1][2]), 3)
    except (TypeError, ValueError, IndexError):
        return None


def _opens_on_word_edge(when: float, transcript: dict,
                          edge: str = "start") -> bool:
    """Whether `when` lands exactly on a timed word's edge, inside none.

    `edge="start"` is a span opening (some word starts there);
    `edge="end"` is a span closing (some word ends there). The pin's
    start comes from a timed word's own start, so it is a word edge by
    construction - what breaks it is honest and named: a
    re-transcription that re-timed the passage (no word bounds there
    anymore) or an overlapping speaker's word that strictly contains
    the boundary (starting there would cut their word in half). Either
    refuses; a mid-ROW boundary on a clean word edge is the captain's
    ruling standing over WhisperX's rowing, not damage, and passes.
    """
    lands = False
    for segment in (transcript or {}).get("segments") or ():
        for word in segment.get("words") or ():
            if not word.get("timed"):
                continue
            try:
                start, end = float(word["start"]), float(word["end"])
            except (KeyError, TypeError, ValueError):
                # A TIMED word with no readable span cannot testify
                # about this edge: it might strictly contain `when`
                # (which refuses) or open exactly on it (which lands).
                # Skipping it would read the edge as clean on the
                # evidence of a parse failure, so the edge reads
                # unproven - False - and the caller takes its CANNOT
                # APPLY path.
                return False
            if end <= start:
                continue
            if start < when < end:
                return False
            bound = start if edge == "start" else end
            if abs(bound - when) <= 1e-6:
                lands = True
    return lands


def _closer_opening_tokens(transcript: dict, cta_start: float,
                           cta_end: float, count: int) -> list:
    """The first `count` tokens the closer plays, from timed words.

    Falls back to segment text where no words were timed: a closer
    with no word timings can still be IDENTIFIED by what it says,
    while only a timed occurrence can MOVE its start.
    """
    stream = _word_stream(transcript)
    inside = [token for token, start, _ in stream
              if start >= cta_start - 1e-6 and start < cta_end - 1e-6]
    if len(inside) >= count:
        return inside[:count]
    words: list = []
    for segment in (transcript or {}).get("segments") or ():
        try:
            seg_start = float(segment.get("timeline_start") or 0.0)
            seg_end = float(segment.get("timeline_end") or 0.0)
        except (TypeError, ValueError):
            continue
        if seg_end > cta_start and seg_start < cta_end:
            words.extend(_tokens(str(segment.get("text") or "")))
    return words[:count]


def apply_closer_redraws(moments: list, transcript: dict,
                         edits: list) -> tuple:
    """Move pinned closers' starts to their anchors' words. Returns
    `(moments, applied, held, stale)`.

    `applied` names each redrawn reel with its old and new span (the
    end never moves); `held` names reels already opening on the anchor
    - the second rebuild reports here, which is how "persisted through
    iterations" reads rather than as a stale pin; `stale` carries every
    pin that could not redraw, LOUDLY: an anchor the transcript no
    longer speaks, an extension that would overlap the reel's own body
    and play seconds twice, or an anchor no longer opening on a timed
    word edge. A mid-row opening on a clean word edge APPLIES - the row
    is chunking and the captain ruled the words. A reel closing on a
    different invitation is none of these - untouched, and the pin is
    not stale for it.
    """
    from dataclasses import replace as _replace

    redraws = [e for e in (edits or [])
               if e.get("kind") == "redraw_closer"]
    if not redraws:
        return list(moments or []), [], [], []
    try:
        from library.tools.reel_proposal import enrich as _enrich
        from library.tools.reel_proposal import (
            snap_to_speech as _snap)
    except ImportError:
        _enrich, _snap = None, None
    out = list(moments or [])
    applied, held, stale = [], [], []
    stream = _word_stream(transcript or {})
    for edit in redraws:
        anchor, opening = edit["anchor_phrase"], edit["from_phrase"]
        anchor_tokens = _tokens(anchor)
        opening_tokens = _tokens(opening)
        anchor_at = _run_starts(stream, anchor)
        if not anchor_at:
            stale.append(
                {"kind": "redraw_closer", "anchor_phrase": anchor,
                 "reason": (
                     f"STALE: closer pin for {anchor!r} no longer "
                     f"applies - those words are spoken nowhere in "
                     f"this transcript. The passage was reworded, "
                     f"re-transcribed, or removed. Original request: "
                     f"{edit.get('reason', '')}".strip())})
            continue
        for index, moment in enumerate(out):
            cta = getattr(moment, "call_to_action", None)
            if cta is None:
                continue
            cta_start = float(cta.timeline_start)
            cta_end = float(cta.timeline_end)
            head = _closer_opening_tokens(
                transcript, cta_start, cta_end, len(opening_tokens))
            if head != opening_tokens:
                if _closer_opening_tokens(
                        transcript, cta_start, cta_end,
                        len(anchor_tokens)) == anchor_tokens:
                    held.append(
                        {"reel": int(moment.number),
                         "span": [round(cta_start, 3),
                                  round(cta_end, 3)],
                         "anchor_phrase": anchor,
                         "reason": (
                             f"already opens on {anchor!r} - the pin "
                             f"is in force, nothing moved")})
                continue
            # The nearest occurrence to the closer it opens: a sentence
            # spoken twice is still pinned where THIS closer is, and
            # the alternates are named rather than silently dropped.
            starts = sorted(
                (stream[i][1] for i in anchor_at),
                key=lambda s: abs(s - cta_start))
            new_start = starts[0]
            was = [round(cta_start, 3), round(cta_end, 3)]
            body_start = float(moment.timeline_start)
            body_end = float(moment.timeline_end)
            if new_start >= cta_end:
                stale.append(
                    {"kind": "redraw_closer", "reel": int(moment.number),
                     "anchor_phrase": anchor,
                     "reason": (
                         f"CANNOT APPLY on reel {int(moment.number)}: "
                         f"the anchor {anchor!r} sits at {new_start:.3f}s, "
                         f"past this closer's end at {cta_end:.3f}s. "
                         f"Original request: "
                         f"{edit.get('reason', '')}".strip())})
                continue
            if max(new_start, body_start) < min(cta_end, body_end) - 1e-9:
                stale.append(
                    {"kind": "redraw_closer", "reel": int(moment.number),
                     "anchor_phrase": anchor,
                     "reason": (
                         f"CANNOT APPLY on reel {int(moment.number)}: "
                         f"extending this closer back to {new_start:.3f}s "
                         f"would overlap its own body "
                         f"({body_start:.2f}-{body_end:.2f}s) by "
                         f"{min(cta_end, body_end) - max(new_start, body_start):.2f}s, "
                         f"so the reel would play those seconds twice. "
                         f"Original request: "
                         f"{edit.get('reason', '')}".strip())})
                continue
            if _snap is not None:
                snapped = _snap(new_start, cta_end, transcript or {})
                if abs(snapped[1] - cta_end) > 1e-6:
                    stale.append(
                        {"kind": "redraw_closer",
                         "reel": int(moment.number),
                         "anchor_phrase": anchor,
                         "reason": (
                             f"CANNOT APPLY on reel {int(moment.number)}: "
                             f"the redrawn span would not hold its end at "
                             f"{cta_end:.3f}s (snaps to {snapped[1]:.3f}s) "
                             f"and the end never moves. Original request: "
                             f"{edit.get('reason', '')}".strip())})
                    continue
                # Mid-ROW on a clean word edge: the row is WhisperX's
                # chunking, and the captain ruled the opening twice.
                # The ruling stands over the rowing - every
                # downstream gate tests boundaries against WORDS
                # (F8, `_word_at`, strictly inside), so a word-edge
                # start is one no gate can fail.
                if (abs(snapped[0] - new_start) > 1e-6
                        and not _opens_on_word_edge(new_start,
                                                    transcript or {})):
                    stale.append(
                        {"kind": "redraw_closer",
                         "reel": int(moment.number),
                         "anchor_phrase": anchor,
                         "reason": (
                             f"CANNOT APPLY on reel {int(moment.number)}: "
                             f"the anchor {anchor!r} at {new_start:.3f}s "
                             f"is no longer on a timed word edge - the "
                             f"transcript re-timed around it, or an "
                             f"overlapping word now contains the "
                             f"opening. Re-anchor to words the "
                             f"transcript still bounds. Original "
                             f"request: "
                             f"{edit.get('reason', '')}".strip())})
                    continue
            redrawn = _replace(
                moment,
                call_to_action=_replace(cta, timeline_start=new_start))
            # The measured text must describe the NEW span, or the bar
            # reads a report about seconds the reel no longer opens on.
            out[index] = _enrich(redrawn, transcript) if _enrich else redrawn
            record = {
                "reel": int(moment.number), "was": was,
                "now": [round(new_start, 3), round(cta_end, 3)],
                "anchor_phrase": anchor, "from_phrase": opening,
                "reason": edit.get("reason", "")}
            if len(starts) > 1:
                record["alternates_not_taken"] = [
                    round(s, 3) for s in starts[1:]]
            applied.append(record)
    return out, applied, held, stale


# ── Transform overrides: a hand move the rebuild must keep ─────────

def _span_word_tokens(transcript: dict, start: float, end: float) -> list:
    """Normalized timed-word tokens spoken inside `[start, end)`.

    Timed words only, for the reason `_word_stream` states: an untimed
    word cannot place anything and is not offered as evidence. Words
    are claimed by their START so a word straddling the span's end
    belongs to the span it opens, the same edge rule `_word_at`
    grades by."""
    tokens = []
    for segment in (transcript or {}).get("segments") or ():
        for word in segment.get("words") or ():
            if not word.get("timed"):
                continue
            try:
                wstart = float(word["start"])
            except (KeyError, TypeError, ValueError):
                continue
            token = normalize(str(word.get("word") or ""))
            if token and start - 1e-6 <= wstart < end - 1e-6:
                tokens.append(token)
    return tokens


def _reel_in_scope(scope: str, reel_name: str) -> bool:
    """Whether an override scoped to `scope` holds on this build.

    The `reel_ending` prefix convention, spelled once: the build names
    the timeline it is laying (`Reel 01 - ... (scratch ...) (rebuild
    staging)`), the declaration names the reel, and a staging suffix
    does not make it another reel. An empty build name holds nothing
    scoped - a scoped decision applied where no reel is named is the
    silent cross-reel hold this scope exists to stop.
    """
    return bool(scope) and bool(reel_name) and (
        reel_name == scope or reel_name.startswith(scope))


def match_transform_overrides(spans: list, transcript: dict,
                               edits: list,
                               reel_name: str = "") -> tuple:
    """Which placed spans speak each recorded override's anchor. Returns
    `(matched, stale)`.

    `spans` are the placed picture spans in play order, each carrying
    its master-transcript range as `span["master"] = (start, end)` -
    the reel build's `placements()` entries, which is the ONE place
    that order is spelled. A span matches when the anchor's words occur
    in it as an ordered run, the same containment `apply_drop_fragments`
    uses for blocks.

    A freeze hold (`span["freeze"]`, from `reel_ending.freeze_placement`)
    speaks nothing, so its `master` is empty and never matches - but it
    carries `span["held_master"]`, the tail span the held frame was
    taken from, and the anchor is tested against THAT. A hand-declared
    speaker value therefore names the freeze built from that clip
    directly: without this the hold takes the value only through the
    build-time timeline copy (`reel_build._inherit_freeze_treatment`),
    which is why a moved shot and its own held frame could disagree
    (Reels 30 and 31, 2026-09-17 - the live speaker at Pan -26 with
    the freeze still at the engine aim).

    An override may carry `reel`: the reel's timeline name, matched by
    prefix (the `reel_ending` convention - `reel_name == reel or
    reel_name.startswith(reel)`). A scoped override holds only on that
    reel's build; everywhere else it reports STALE with scope
    `"reel"`, the routine kind. An override with no `reel` holds on
    every reel speaking the anchor, exactly as before - and where a
    scoped override and an unscoped one would hold the same property
    on the same span, the scoped one wins: the captain narrowed that
    reel's decision, and the general one still holds everywhere else.

    Every matching span is named, like `caption_fix` names every card:
    a rebuild that re-cuts one shot into two keeps both under the
    captain's decision rather than splitting it silently. An override
    matching nothing reports STALE rather than vanishing.

    **A stale override says WHICH KIND of stale it is**, because the
    two carry opposite risk and used to print the same sentence
    (`docs/RULE_EVIDENCE.md#one-word-for-two-kinds-of-stale`):

    - `scope="reel"` - the anchor IS spoken in the transcript, just
      not in a span this reel placed. Routine: every recorded
      override is matched against every reel, so building one reel
      reports every other reel's overrides this way. This project
      prints eight of them on a Reel 09 build.
    - `scope="transcript"` - the anchor is spoken NOWHERE. The
      decision is unreachable for good: no rebuild of any reel will
      apply it again and the engine's own aim plays instead. This is
      the captain's hand move being overwritten, and it is what
      `lost_overrides` selects."""
    overrides = [e for e in (edits or [])
                  if e.get("kind") == "transform_override"]
    matched, stale = [], []
    stream = _word_stream(transcript or {})
    # Scoped overrides first, so a per-reel narrowing wins over the
    # general decision wherever both would hold the same property on
    # the same span - regardless of the order the two were recorded
    # in. `held_by_scope` is (span index, property) pairs the reel's
    # own decisions already hold; the general pass skips those.
    held_by_scope = set()
    ordered = sorted(overrides,
                     key=lambda e: 0 if e.get("reel") is not None else 1)
    for edit in ordered:
        anchor, prop = edit["anchor_phrase"], edit["property"]
        scope = edit.get("reel")
        if scope is not None and not _reel_in_scope(scope,
                                                    reel_name or ""):
            reason = (
                f"STALE - not on this reel: transform override for "
                f"{anchor!r} is scoped to reel {scope!r} and this "
                f"build is {reel_name or '(no reel named)'!r}, so it "
                f"holds nothing here. Expected on every build of a "
                f"reel the decision is not about. Original request: "
                f"{edit.get('reason', '')}").strip()
            stale.append(
                {"kind": "transform_override",
                 "anchor_phrase": anchor, "property": prop,
                 "value": edit["value"],
                 "scope": "reel",
                 "reason": reason})
            continue
        anchor_tokens = _tokens(anchor)
        hits = []
        for index, span in enumerate(spans or []):
            master = (span.get("master") if isinstance(span, dict)
                      else None)
            if isinstance(span, dict) and span.get("freeze"):
                held = span.get("held_master") or None
                try:
                    if (held is not None
                            and float(held[1]) > float(held[0])):
                        master = held
                except (TypeError, ValueError, IndexError):
                    pass
            if not master:
                continue
            try:
                span_start, span_end = float(master[0]), float(master[1])
            except (TypeError, ValueError, IndexError):
                continue
            tokens = _span_word_tokens(transcript, span_start, span_end)
            if _contains_run(tokens, anchor_tokens):
                if (scope is not None
                        or (index, prop) not in held_by_scope):
                    hits.append(index)
        if scope is not None:
            for index in hits:
                held_by_scope.add((index, prop))
        if not hits:
            spoken = bool(_run_starts(stream, anchor))
            if spoken:
                reason = (
                    f"STALE - not on this reel: transform override for "
                    f"{anchor!r} is spoken in the transcript but in no "
                    f"span THIS reel placed, so it holds nothing here. "
                    f"Expected on every build of a reel the decision is "
                    f"not about. Original request: "
                    f"{edit.get('reason', '')}").strip()
            else:
                reason = (
                    f"STALE - LOST: transform override for {anchor!r} is "
                    f"spoken NOWHERE in the measured transcript, so no "
                    f"rebuild of any reel can apply it again - "
                    f"{prop}={edit['value']:g} is gone and the engine's "
                    f"own aim plays in its place. The passage was "
                    f"reworded or re-transcribed. Re-capture it against "
                    f"the words now spoken: `python3 -m "
                    f"library.tools.captain_edits <project> "
                    f"capture-transform --reel N --timeline ... --words "
                    f"...`. Original request: "
                    f"{edit.get('reason', '')}").strip()
            stale.append(
                {"kind": "transform_override",
                 "anchor_phrase": anchor, "property": prop,
                 "value": edit["value"],
                 "scope": "reel" if spoken else "transcript",
                 "reason": reason})
            continue
        for index in hits:
            matched.append(
                {"span_index": index, "property": prop,
                 "value": edit["value"], "anchor_phrase": anchor,
                 "reason": edit.get("reason", "")})
    return matched, stale


def lost_overrides(stale: list) -> list:
    """The stale transform overrides that no rebuild can ever apply.

    A captain's transform survives a rebuild by being re-applied from
    its words (`match_transform_overrides`). When those words stop
    being spoken the value is gone - not for this reel, for every
    future build of every reel - and nothing else in the engine
    notices. This is the selection that lets a caller say so.
    """
    return [record for record in (stale or [])
            if record.get("kind") == "transform_override"
            and record.get("scope") == "transcript"]


# ── Span retimes: a hand trim the rebuild must keep ────────────────

def match_span_retimes(spans: list, transcript: dict,
                       edits: list) -> tuple:
    """Which placed spans get their head or tail moved to spoken words.

    Returns `(matched, held, stale)`. `spans` are the placed picture
    spans in play order, each carrying its master-transcript range as
    `span["master"] = (start, end)` - the reel build's `placements()`
    entries, the same join `match_transform_overrides` reads. A span
    matches when the anchor's words occur in it as a fully-inside
    ordered run.

    A retime TRIMS only: `head` moves the span's opening later onto
    the anchor's first word start, `tail` moves its close earlier onto
    the anchor's last word end. Extending a span past its edge is a
    new editorial decision (it plays seconds nothing approved), so an
    anchor outside the edge reports CANNOT-APPLY rather than moving
    it. An edge already on the words reports HELD - the second rebuild
    reads here, which is how "persisted through iterations" shows.
    Anything matching nothing reports STALE.
    """
    retimes = [e for e in (edits or [])
               if e.get("kind") == "span_retime"]
    matched, held, stale = [], [], []
    if not retimes:
        return matched, held, stale
    stream = _word_stream(transcript or {})
    for edit in retimes:
        anchor, edge = edit["anchor_phrase"], edit.get("edge")
        anchor_tokens = _tokens(anchor)
        at = _run_starts(stream, anchor)
        if not at:
            stale.append(
                {"kind": "span_retime",
                 "anchor_phrase": anchor, "edge": edge,
                 "reason": (
                     f"STALE: span retime for {anchor!r} no longer "
                     f"applies - those words are spoken nowhere in "
                     f"this transcript. Original request: "
                     f"{edit.get('reason', '')}".strip())})
            continue
        # Occurrence bounds in master seconds, in stream order.
        occurrences = []
        for occurrence in at:
            first_start = stream[occurrence][1]
            last_end = stream[occurrence + len(anchor_tokens) - 1][2]
            occurrences.append((first_start, last_end))
        hits = 0
        for index, span in enumerate(spans or []):
            master = (span.get("master") if isinstance(span, dict)
                      else None)
            if not master:
                continue
            try:
                span_start, span_end = float(master[0]), float(master[1])
            except (TypeError, ValueError, IndexError):
                continue
            inside = [(s, e) for s, e in occurrences
                      if s >= span_start - 1e-6 and e <= span_end + 1e-6]
            if not inside:
                # An occurrence straddling the edge it would move is
                # not "unmatched" - it is an extension asking to play
                # unapproved seconds, and says so by name.
                straddles = [(s, e) for s, e in occurrences
                             if s < span_end - 1e-6
                             and e > span_start + 1e-6]
                if straddles:
                    stale.append(
                        {"kind": "span_retime", "span_index": index,
                         "anchor_phrase": anchor, "edge": edge,
                         "reason": (
                             f"CANNOT APPLY on span {index}: "
                             f"{anchor!r} straddles its {edge} "
                             f"({span_start:.3f}-{span_end:.3f}s), so "
                             f"moving the {edge} onto those words "
                             f"would EXTEND past it, playing seconds "
                             f"nothing approved. A trim keeps; an "
                             f"extension re-decides. Original "
                             f"request: "
                             f"{edit.get('reason', '')}".strip())})
                continue
            # The occurrence nearest the edge it moves: a sentence
            # spoken twice in one span still pins deterministically.
            if edge == "head":
                chosen = min(inside, key=lambda se: abs(se[0]
                                                        - span_start))
                new_edge = chosen[0]
                old_edge = span_start
                shortens = new_edge > old_edge + 1e-6
                extends = new_edge < old_edge - 1e-6
            else:
                chosen = min(inside, key=lambda se: abs(se[1]
                                                        - span_end))
                new_edge = chosen[1]
                old_edge = span_end
                shortens = new_edge < old_edge - 1e-6
                extends = new_edge > old_edge + 1e-6
            if extends:
                stale.append(
                    {"kind": "span_retime", "span_index": index,
                     "anchor_phrase": anchor, "edge": edge,
                     "reason": (
                         f"CANNOT APPLY on span {index}: moving its "
                         f"{edge} to {anchor!r} ({new_edge:.3f}s) "
                         f"would EXTEND past {old_edge:.3f}s, playing "
                         f"seconds nothing approved. A trim keeps; an "
                         f"extension re-decides. Original request: "
                         f"{edit.get('reason', '')}".strip())})
                continue
            if not shortens:
                held.append(
                    {"span_index": index, "edge": edge,
                     "anchor_phrase": anchor,
                     "reason": (
                         f"span {index}'s {edge} already sits on "
                         f"{anchor!r} - the pin is in force, nothing "
                         f"moved")})
                continue
            hits += 1
            matched.append(
                {"span_index": index, "edge": edge,
                 "new_edge": new_edge, "old_edge": old_edge,
                 "anchor_phrase": anchor,
                 "reason": edit.get("reason", "")})
        if not hits and not any(
                h.get("anchor_phrase") == anchor
                for h in held) and not any(
                s.get("anchor_phrase") == anchor
                and s.get("edge") == edge for s in stale):
            stale.append(
                {"kind": "span_retime",
                 "anchor_phrase": anchor, "edge": edge,
                 "reason": (
                     f"STALE: span retime for {anchor!r} no longer "
                     f"applies - those words are in no placed span. "
                     f"The passage was reworded, re-cut out of this "
                     f"reel, or never reached it. Original request: "
                     f"{edit.get('reason', '')}".strip())})
    return matched, held, stale


# ── Freshness: an anchor that moved since it was recorded ────────────

def check_span_retime_freshness(spans: list, transcript: dict,
                                edits: list,
                                fps: float = 24000 / 1001) -> tuple:
    """`(drifted, stale)`: the enumerable pre-build state of recorded trims.

    Call this on the same placements probe the trim is about to
    apply to, BEFORE it applies: an anchor that no longer resolves
    is stale (loud today - kept loud here, and returned so a
    caller can file it), and an anchor that resolves to a
    DIFFERENT PLACE than the one it was recorded at is drifted -
    silent by construction until this check, and the Reel 17
    defect (2026-09-21: the head pin followed "So" 1407.830 ->
    1407.970 and a caption-only rebuild shipped 7 frames shorter
    with nothing said at all).

    Drift is judged in FRAMES, not seconds: the baseline is the
    `recorded_edge` stamped at write time (`record_edit`), the
    resolution is the word edge the trim is about to land on, and
    a move inside one frame changes nothing downstream while a
    move across frames changes what the reel plays. A pin with no
    `recorded_edge` - recorded before the stamp existed, or
    stamped ambiguous - can never drift; it matches and applies
    exactly as before.
    """
    from library.tools.frame_utils import seconds_to_frame

    retimes = [e for e in (edits or [])
               if e.get("kind") == "span_retime"]
    if not retimes:
        return [], []
    matched, held, stale = match_span_retimes(spans, transcript, edits)
    baselines: dict = {}
    for edit in retimes:
        recorded = edit.get("recorded_edge")
        if (isinstance(recorded, bool)
                or not isinstance(recorded, (int, float))
                or not math.isfinite(recorded)):
            continue
        baselines.setdefault(
            (edit.get("anchor_phrase"), edit.get("edge")),
            float(recorded))
    drifted = []

    def _drift_record(span_index, edge, anchor, baseline,
                      resolved, in_force, reason) -> dict:
        old_frame = seconds_to_frame(baseline, fps)
        new_frame = seconds_to_frame(resolved, fps)
        if new_frame == old_frame:
            return {}
        moved = new_frame - old_frame
        return {
            "kind": "span_retime", "span_index": span_index,
            "edge": edge, "anchor_phrase": anchor,
            "recorded_edge": round(baseline, 3),
            "resolved_edge": round(float(resolved), 3),
            "frames_moved": moved,
            "reason": (
                f"DRIFTED on span {span_index}: {anchor!r} now "
                f"resolves to {float(resolved):.3f}s - recorded at "
                f"{baseline:.3f}s ({moved:+d} frames). "
                f"Re-transcription re-timed the anchor and the "
                f"{edge} trim follows the words, so this build "
                f"trims to different seconds than the recorded "
                f"decision - {in_force}. Re-capture the trim "
                f"against the words now spoken if the new edge "
                f"is wrong, or re-record it to adopt the new "
                f"timing as the baseline. Original request: "
                f"{reason}".strip())}

    for record in matched:
        baseline = baselines.get(
            (record.get("anchor_phrase"), record.get("edge")))
        if baseline is None:
            continue
        entry = _drift_record(
            record["span_index"], record["edge"],
            record["anchor_phrase"], baseline, record["new_edge"],
            "the trim below lands on the moved words",
            record.get("reason", ""))
        if entry:
            drifted.append(entry)
    for record in held:
        baseline = baselines.get(
            (record.get("anchor_phrase"), record.get("edge")))
        if baseline is None:
            continue
        try:
            master = spans[record["span_index"]].get("master")
            edge_now = float(
                master[0] if record.get("edge") == "head"
                else master[1])
        except (TypeError, ValueError, IndexError, KeyError,
                AttributeError):
            continue
        entry = _drift_record(
            record["span_index"], record["edge"],
            record["anchor_phrase"], baseline, edge_now,
            "the pin is already in force at the moved words",
            record.get("reason", ""))
        if entry:
            drifted.append(entry)
    return drifted, list(stale)


def retime_placements(placements_list: list, transcript: dict,
                      edits: list, fps: float = 24000 / 1001) -> tuple:
    """Move pinned span edges to their words and close up what follows.

    Returns `(placements_list, applied, held, stale)`. Each matched
    span's master edge AND its source edge move together (a head trim
    of d seconds plays d seconds later of the same source), and every
    later span's `record`/`snapped_record` is re-derived in record
    order - picture and sound move together, and no caption pinned to
    an old reel time becomes a lie. A trim eating a whole span reports
    CANNOT-APPLY rather than placing nothing. In place, like the
    builder's own cursor math.
    """
    from library.tools.frame_utils import seconds_to_frame

    matched, held, stale = match_span_retimes(
        placements_list, transcript, edits)
    applied = []
    by_span: dict = {}
    for record in matched:
        by_span.setdefault(record["span_index"], []).append(record)
    for index, records in by_span.items():
        entry = placements_list[index]
        master_start, master_end = (float(entry["master"][0]),
                                    float(entry["master"][1]))
        source_in = float(entry.get("source_in", master_start))
        source_out = float(entry.get("source_out", master_end))
        was = [round(master_start, 3), round(master_end, 3)]
        for record in records:
            if record["edge"] == "head":
                shift = record["new_edge"] - master_start
                master_start = record["new_edge"]
                source_in += shift
            else:
                shift = master_end - record["new_edge"]
                master_end = record["new_edge"]
                source_out -= shift
        if not master_end - master_start > 1.0 / float(fps):
            stale.append(
                {"kind": "span_retime", "span_index": index,
                 "anchor_phrase": records[0]["anchor_phrase"],
                 "edge": records[0]["edge"],
                 "reason": (
                     f"CANNOT APPLY on span {index}: the trim would "
                     f"eat the whole span ({was[0]:.3f}-"
                     f"{was[1]:.3f}s). Re-anchor inside it. Original "
                     f"request: "
                     f"{records[0].get('reason', '')}".strip())})
            continue
        entry["master"] = (master_start, master_end)
        entry["source_in"] = source_in
        entry["source_out"] = source_out
        applied.append(
            {"span_index": index, "edge": records[0]["edge"],
             "was": was,
             "now": [round(master_start, 3), round(master_end, 3)],
             "anchor_phrase": records[0]["anchor_phrase"],
             "reason": records[0].get("reason", "")})
    if applied:
        ordered = sorted(placements_list,
                         key=lambda e: float(e.get("record", 0) or 0))
        cursor = 0.0
        for entry in ordered:
            master_start, master_end = (float(entry["master"][0]),
                                        float(entry["master"][1]))
            entry["record"] = round(cursor, 3)
            entry["snapped_record"] = seconds_to_frame(
                entry["record"], fps)
            cursor += master_end - master_start
    return placements_list, applied, held, stale


def retime_ranges(ranges: list, spans: list, transcript: dict,
                  edits: list, fps: float = 24000 / 1001) -> tuple:
    """Trim keep ranges per recorded `span_retime` pins. Returns
    `(ranges, applied, held, stale)`.

    The builder seam `retime_placements` cannot take: captions,
    explainers, overlays and the placements themselves all derive from
    the keep `ranges`, so trimming placements after captions were
    planned from untrimmed ranges plays trimmed picture under
    untrimmed cards. This trims the ranges FIRST, from a placements
    probe the caller builds over them - every downstream reader then
    sees the trimmed shape, and picture and captions move together.

    `spans` are placements-like dicts carrying `master` ranges over
    the same clock as `ranges` (the reel builder's `placements()`
    entries). They are COPIED, never mutated: the probe is matching
    evidence, not the build. The union of the trimmed masters is
    merged back to ranges with a one-frame epsilon - a trim that opens
    an interior gap SPLITS the range rather than bridging it, so
    removed seconds stay removed. No pins, or pins matching nothing
    trimmable, returns the ranges unchanged.

    Only edges a trim moved come back from the probe: every other
    bound is the input range's own, bit-identical. The probe masters
    are frame-quantised placement arithmetic, so rebuilding untouched
    edges from them moves words across the edge by dust - measured on
    Reel 16 (2026-09-19): a body trim rebuilt the untouched closer
    head 1168.2899999999997s as 1168.292s, and the opening "If" whose
    start the snap had landed exactly on the head fell outside every
    range and lost its caption while the audio still plays it.
    """
    retimes = [e for e in (edits or [])
               if e.get("kind") == "span_retime"]
    if not retimes:
        return list(ranges or []), [], [], []
    probe = [dict(span) for span in (spans or [])]
    for entry, original in zip(probe, spans or []):
        master = original.get("master")
        if master is not None:
            try:
                entry["master"] = (float(master[0]), float(master[1]))
            except (TypeError, ValueError, IndexError):
                pass
    _, applied, held, stale = retime_placements(
        probe, transcript, edits, fps=fps)
    if not applied:
        return list(ranges or []), applied, held, stale
    masters = sorted(
        (float(entry["master"][0]), float(entry["master"][1]))
        for entry in probe
        if isinstance(entry.get("master"), (list, tuple)))
    merged: list = []
    epsilon = 1.5 / float(fps)
    for start, end in masters:
        if end <= start:
            continue
        if merged and start <= merged[-1][1] + epsilon:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    # Play order, not master order. The caller lays `ranges` in the
    # order the reel plays them - body ranges, then the closing CTA,
    # which may come from anywhere in the episode - and every
    # downstream reader (the ending first among them) reads the LAST
    # range as the close. Merging above sorts by master time, so an
    # early-master CTA sorts before the body and the ending truncates
    # the body to the CTA's shot end and refuses (measured on Reel 16,
    # 2026-09-18: a head pin re-sorted the CTA first and the build
    # died in `apply_ending`). Trims only move edges, never reorder,
    # so each merged interval belongs to the input range it overlaps;
    # emit groups in input order, master order inside a group, and
    # anything overlapping no input range (which a pure edge-move
    # cannot produce) at the end rather than dropped.
    def _overlaps(interval, want) -> bool:
        try:
            lo, hi = float(interval[0]), float(interval[1])
            rs, re_ = float(want[0]), float(want[1])
        except (TypeError, ValueError, IndexError):
            return False
        return lo < re_ and hi > rs

    ordered: list = []
    claimed = [False] * len(merged)
    groups: list = []
    for want in ranges or []:
        group = []
        for index, interval in enumerate(merged):
            if not claimed[index] and _overlaps(interval, want):
                group.append(interval)
                claimed[index] = True
        if group:
            groups.append((want, group))
    leftovers = [interval for index, interval in enumerate(merged)
                 if not claimed[index]]
    # Which group edges did trims actually move? An applied trim carries
    # its new edge (`now`, a transcript word time): a group head moves
    # exactly when a head trim landed on it, a group tail when a tail
    # trim did - proximity within the merge epsilon, the scale on which
    # this function already refuses to tell two edges apart. Every
    # other bound below is the input range's own, bit-identical, never
    # rebuilt from the frame-quantised probe. An interior trim whose
    # span merely overlaps the group lands nowhere near its outer
    # edges and must never take them with it.
    head_nows: list = []
    tail_nows: list = []
    for record in applied:
        try:
            now = (float(record["now"][0]), float(record["now"][1]))
        except (TypeError, ValueError, IndexError):
            continue
        if record.get("edge") == "head":
            head_nows.append(now[0])
        else:
            tail_nows.append(now[1])
    for want, group in groups:
        try:
            bounds = (float(want[0]), float(want[1]))
        except (TypeError, ValueError, IndexError):
            ordered.extend(tuple(interval) for interval in group)
            continue
        first, last = group[0], group[-1]
        head_hit = [now for now in head_nows
                    if abs(now - float(first[0])) <= epsilon]
        head = min(head_hit) if head_hit else bounds[0]
        tail_hit = [now for now in tail_nows
                    if abs(now - float(last[1])) <= epsilon]
        tail = max(tail_hit) if tail_hit else bounds[1]
        if len(group) == 1:
            ordered.append((head, tail))
        else:
            # A trim-opened interior gap: the split stands (removed
            # seconds stay removed) and only the outer edges consult
            # trims; interior edges are the merged intervals' own.
            ordered.append((head, first[1]))
            ordered.extend(tuple(interval) for interval in group[1:-1])
            ordered.append((last[0], tail))
    ordered.extend(tuple(interval) for interval in leftovers)
    return (ordered, applied, held, stale)


# ── The captain reads what is in force ───────────────────────────────

def describe_edits(edits: list) -> list:
    """One plain-language line per edit, in the captain's own words.
    Printed AND returned - the dashboard and the run log read the
    return; the captain reads the print."""
    lines = []
    for number, edit in enumerate(edits or [], start=1):
        kind = edit.get("kind")
        anchor = edit.get("anchor_phrase", "")
        reason = (edit.get("reason") or "").strip()
        if kind == "caption_fix":
            lines.append(
                f"{number}. Captions: wherever the speech says "
                f"{anchor!r}, the caption reads "
                f"{edit.get('replacement', '')!r} - {reason}")
        elif kind == "drop_fragment":
            lines.append(
                f"{number}. Removed: the passage saying {anchor!r} is "
                f"cut from this video, and everything after it moves "
                f"up - {reason}")
        elif kind == "redraw_closer":
            lines.append(
                f"{number}. Redrawn: the closer opening on "
                f"{edit.get('from_phrase', '')!r} now opens on "
                f"{anchor!r}, end fixed - {reason}")
        elif kind == "transform_override":
            scoped = edit.get("reel")
            lines.append(
                f"{number}. Framing: wherever the speech says "
                f"{anchor!r}, {edit.get('property')} holds "
                f"{edit.get('value')}"
                f"{f' on reel {scoped!r}' if scoped else ''}"
                f" - {reason}")
        elif kind == "span_retime":
            lines.append(
                f"{number}. Trim: the {edit.get('edge')} of the span "
                f"speaking {anchor!r} sits on those words' own edge - "
                f"{reason}")
        else:
            lines.append(f"{number}. {kind}: {anchor!r} - {reason}")
    for line in lines:
        print(line)
    return lines


def report_stale(records: list) -> list:
    """A stale edit is LOUD: stderr, every record, every rebuild. A
    silently dropped edit is the same defect as a layer that shipped
    without a word - it must SAY it no longer applies."""
    lines = []
    for record in records or []:
        line = (f"STALE EDIT: {record.get('anchor_phrase', '')!r} - "
                f"{record.get('reason', '')}".strip())
        print(line, file=sys.stderr)
        lines.append(line)
    return lines


def report_drifted(records: list) -> list:
    """A drifted trim is LOUD like a stale one: stderr, every record,
    every rebuild. A trim that follows re-timed words onto different
    seconds without saying so is the Reel 17 defect - the build
    reports the move BEFORE it proceeds to place it."""
    lines = []
    for record in records or []:
        line = (f"DRIFTED EDIT: {record.get('anchor_phrase', '')!r} - "
                f"{record.get('reason', '')}".strip())
        print(line, file=sys.stderr)
        lines.append(line)
    return lines


# ── Recording a decision: the write side ──────────────────────────

def transcript_path(project_folder) -> Path:
    """The measured transcript, where the anchors resolve.

    The same file the reel build reads - a pin recorded against other
    words than these is drift, so the record commands check
    correspondence here at write time rather than failing the next
    build.  DELEGATED rather than composed: `timeline_transcript` owns
    where its own output lands, and a second spelling here is a second
    answer to "where does the transcript live"
    (`tests/test_operations.py`)."""
    from library.tools.timeline_transcript import (
        transcript_path as _owner_path)

    return _owner_path(project_folder)


def load_transcript(project_folder):
    """The measured transcript, or None where nothing is on file."""
    path = transcript_path(project_folder)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def check_anchor_spoken(edit: dict, transcript) -> None:
    """Refuse an edit whose anchor is in no measured speech.

    The write-time half of the correspondence the external check
    enforces pre-run (`external_inputs._check_captain_edits`): a pin
    recorded against words the transcript never speaks - a typo in
    `from_phrase`, a reworded passage - would sit in force and match
    nothing, failing SILENTLY on reels that close on something else
    (`test_a_closer_opening_on_neither_phrase_is_left_alone`). Where
    no transcript is on file yet the edit verifies structurally, and
    drift reports STALE at apply time instead."""
    if transcript is None:
        return
    stream = _word_stream(transcript)
    phrases = [edit["anchor_phrase"]]
    if edit.get("kind") == "redraw_closer":
        phrases.append(edit["from_phrase"])
    for phrase in phrases:
        if not _run_starts(stream, phrase):
            raise CaptainEditError(
                f"{phrase!r} is spoken nowhere in this transcript - "
                f"the pin would sit in force and match nothing. "
                f"Re-anchor to words the reel still says.")


def _edit_identity(edit: dict) -> tuple:
    """What makes two edits the SAME decision: kind, anchor, and the
    field that scopes it (the property held, the opening moved, the
    text replaced) - plus the reel, where an override names one. A
    captain who re-rules the same decision SUPERSEDES it; a different
    scope is a different edit: Reel 02's Pan for a shared shot must
    not supersede Reel 01's, and neither supersedes the unscoped
    decision that holds everywhere else."""
    kind, anchor = edit.get("kind"), normalize(edit.get("anchor_phrase",
                                                         ""))
    if kind == "transform_override":
        return (kind, anchor, edit.get("property"),
                edit.get("reel") or "")
    if kind == "span_retime":
        return (kind, anchor, edit.get("edge"))
    if kind == "redraw_closer":
        return (kind, anchor, normalize(edit.get("from_phrase", "")))
    if kind == "caption_fix":
        return (kind, anchor)
    return (kind, anchor)


def record_edit(project_folder, edit: dict, source: str = "") -> tuple:
    """Append one decision to the durable store, or supersede it.

    Validates structurally BEFORE touching disk, checks the anchor
    against the measured transcript where one is on file, then reads
    the store the reader reads (`load_edits`): an exact duplicate is
    REFUSED as already in force, a re-ruling of the same decision
    REPLACES it, anything else appends. Returns `(edit, action)` with
    action one of `"recorded"`, `"superseded"`. The store is created
    (with its `external/` directory) where nothing was ever written -
    that absence was the whole defect."""
    validate_edits([edit])
    transcript = load_transcript(project_folder)
    check_anchor_spoken(edit, transcript)
    if (edit.get("kind") == "span_retime"
            and "recorded_edge" not in edit
            and transcript is not None):
        # The freshness baseline: where the anchor's own word edge
        # resolves RIGHT NOW, in master seconds. A rebuild re-snaps
        # the trim to whatever the transcript says then, so a later
        # re-transcription that re-times the anchor would move the
        # trim silently (Reel 17, 2026-09-21: the head pin followed
        # "So" 1407.830 -> 1407.970 and a caption-only rebuild
        # shipped 7 frames shorter with nothing said). The recorded
        # number is never placed from - the words are - it is only
        # what the pre-build freshness check compares against. One
        # occurrence only: an anchor spoken twice has no single
        # baseline, and such a pin stays unstamped rather than
        # guessing which telling it meant.
        resolved = _resolve_single_edge(
            transcript, edit["anchor_phrase"], edit.get("edge"))
        if resolved is not None:
            edit = dict(edit, recorded_edge=resolved)
    path = edits_path(project_folder)
    existing: list = []
    if path.is_file():
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise CaptainEditError(
                f"{path} cannot be read: {exc}") from exc
        if (not isinstance(document, dict) or "value" not in document
                or document.get("key") != CAPTAIN_EDITS_KEY):
            raise CaptainEditError(
                f"{path.name} must be an object with 'key', 'source' "
                f"and 'value' - it is not the store this reader reads.")
        existing = validate_edits(document["value"])
    if edit in existing:
        raise CaptainEditError(
            f"already in force: {describe_edits([edit])[0]} - "
            f"recording it again would list the same decision twice.")
    identity = _edit_identity(edit)
    action = "recorded"
    kept = [e for e in existing if _edit_identity(e) != identity]
    if len(kept) != len(existing):
        action = "superseded"
    validate_edits(kept + [edit])
    if not source:
        from datetime import datetime, timezone
        source = ("captain via firstmate, "
                  f"{datetime.now(timezone.utc):%Y-%m-%d}")

    # The write goes through the KEY SCHEME, not straight to disk.
    # This read-modify-write had no lock, no atomic rename and no
    # revision, so two agents recording two DIFFERENT decisions at the
    # same moment lost one of them silently - and the store's own
    # `source` field on `lucie/geo-podcast` already named two lanes.
    # `declaration_keys` merges per `_edit_identity`, which is the key
    # this function was already reasoning in: a decision another writer
    # landed in the meantime survives, and a genuine re-ruling of the
    # SAME decision by two writers raises rather than dropping one.
    from library.tools.declaration_keys import (
        entry_key, read_entries, write_entries)
    base, _envelope = read_entries(project_folder, "captain_edits")
    mine = dict(base)
    # The entry key IS `_edit_identity`, so a re-ruling REPLACES in
    # place - the supersede this function already performed, expressed
    # once rather than twice.
    mine[entry_key("captain_edits", edit)] = edit
    write_entries(project_folder, "captain_edits", mine, base,
                  envelope={"key": CAPTAIN_EDITS_KEY, "source": source})
    return edit, action


def anchor_reel_time(ranges: list, transcript: dict, anchor: str,
                     lead_seconds: float = 0.0) -> tuple:
    """Where the anchor's words play on the REEL, in reel seconds.

    The anchor must occur in exactly ONE of the reel's ranges - an
    occurrence nowhere is a stale pin, and two occurrences (the words
    spoken twice) need the captain to say which clip they moved, so
    both are named and nothing is guessed. Returns
    `(reel_start, master_start, master_end)` - the reel second the
    anchor's first word plays, with the master span for centring on
    the words rather than their edge."""
    needle = _tokens(anchor)
    if not needle:
        raise CaptainEditError("no anchor words named.")
    stream = _word_stream(transcript or {})
    at = _run_starts(stream, anchor)
    if not at:
        raise CaptainEditError(
            f"{anchor!r} is spoken nowhere in this transcript.")
    candidates = []
    for occurrence in at:
        master = stream[occurrence][1]
        master_end = stream[occurrence + len(needle) - 1][2]
        for order, (range_start, range_end) in enumerate(ranges):
            if range_start - 1e-6 <= master < range_end - 1e-6:
                reel = (lead_seconds + sum(
                    end - start for start, end in ranges[:order])
                    + (master - range_start))
                candidates.append((reel, master, master_end, order))
    if not candidates:
        raise CaptainEditError(
            f"{anchor!r} is spoken, but in no range this reel plays - "
            f"the passage was cut out of it.")
    if len(candidates) > 1:
        options = ", ".join(
            f"reel {reel:.2f}s (range {order})"
            for reel, _, _, order in candidates)
        raise CaptainEditError(
            f"{anchor!r} plays {len(candidates)} times on this reel: "
            f"{options}. Say which clip was moved - nothing is guessed.")
    reel, master, master_end, _ = candidates[0]
    return reel, master, master_end


def main(argv=None) -> int:
    """The write side beside the listing. One route into the store:

    `python3 -m library.tools.captain_edits <project> list`
        the plain-language listing of edits in force (the default -
        a bare `<project>` lists too).
    `... record-closer --anchor ... --from ... --reason ...`
        pin a shared closer's opening to the anchor's words, end
        fixed. Checked against the measured transcript at write time,
        so a typo fails here and not on the next build.
    `... record-transform --anchor ... --property Pan --value -35
    --reason ... [--on-reel 'Reel 01 - ...']`
        hold one Edit-page transform at the captain's number on every
        shot speaking the anchor. The typed fallback for a decision
        settled in words. `--on-reel` scopes it to one reel's build -
        the per-reel Pan on a shot several reels share.
    `... record-retime --anchor ... --edge head --reason ...`
        move one placed span's head (or tail) onto the anchor's own
        word edge - the typed fallback for a hand trim. Trims only;
        an extension is refused at apply time.
    `... capture-transform --reel 9 --timeline 'Reel 09 - ...'
    --words ... [--property Pan] --reason ...`
        read the value out of the LIVE Resolve timeline - the captain's
        hand move, which exists nowhere else - and record it. The
        route for a change made by hand in Resolve. `--on-reel`
        scopes the hold to that reel, like `record-transform`.

    Every record refuses before writing: structurally (`validate_edits`)
    and against the measured speech where a transcript is on file.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="library.tools.captain_edits",
        description="The captain's edits: record a settled decision so "
                    "a rebuild keeps it, or list what is in force.")
    parser.add_argument("project_folder")
    parser.add_argument("verb", nargs="?", default="list",
                        choices=["list", "record-closer",
                                 "record-transform", "record-retime",
                                 "capture-transform"])
    parser.add_argument("--anchor", default="")
    parser.add_argument("--from", dest="opening", default="")
    parser.add_argument("--property", dest="prop", default="")
    parser.add_argument("--value", default=None)
    parser.add_argument("--edge", default="")
    parser.add_argument("--on-reel", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--source", default="")
    parser.add_argument("--reel", default=None)
    parser.add_argument("--timeline", default="")
    parser.add_argument("--track", default=None)
    parser.add_argument("--words", default="")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    if args.verb == "list":
        try:
            edits = load_edits(args.project_folder)
        except CaptainEditError as exc:
            print(f"REFUSED\n\n{exc}\n")
            return 1
        if not edits:
            print(f"No captain edits in force for {args.project_folder}.")
            return 0
        for line in describe_edits(edits):
            pass
        return 0

    if not args.reason.strip():
        print("REFUSED\n\nRecording without a reason is not reviewable - "
              "carry the captain's own words in --reason.\n")
        return 1
    try:
        if args.verb == "record-closer":
            edit = {"kind": "redraw_closer",
                    "anchor_phrase": args.anchor,
                    "from_phrase": args.opening,
                    "reason": args.reason}
            _, action = record_edit(args.project_folder, edit,
                                    args.source)
        elif args.verb == "record-transform":
            try:
                number = float(args.value)
            except (TypeError, ValueError):
                raise CaptainEditError(
                    f"--value {args.value!r} is not a number: a "
                    f"transform override names the number the property "
                    f"must hold.")
            edit = {"kind": "transform_override",
                    "anchor_phrase": args.anchor,
                    "property": args.prop, "value": number,
                    "reason": args.reason}
            if args.on_reel.strip():
                edit["reel"] = args.on_reel.strip()
            _, action = record_edit(args.project_folder, edit,
                                    args.source)
        elif args.verb == "record-retime":
            edit = {"kind": "span_retime",
                    "anchor_phrase": args.anchor,
                    "edge": args.edge,
                    "reason": args.reason}
            _, action = record_edit(args.project_folder, edit,
                                    args.source)
        else:
            edit, action = _capture_transform(args)
    except CaptainEditError as exc:
        print(f"REFUSED\n\n{exc}\n")
        return 1
    print(f"{action}: {describe_edits([edit])[0]}")
    return 0


@under_lease("capture a Resolve transform", exclusive=False)
def _capture_transform(args) -> tuple:
    """Read the captain's hand move out of live Resolve and record it.

    The value is READ, never typed: the manual change lives only in
    the project file, and a typed number is a second chance to
    misremember it. The clip is named by the WORDS it speaks
    (`--words`), resolved to reel seconds through the reel's own
    ranges, and the live video item covering those seconds supplies
    the held value. Ambiguity refuses - two occurrences, two items on
    two tracks - naming what matched rather than guessing."""
    if not args.timeline:
        raise CaptainEditError(
            "capture-transform needs --timeline, the reel timeline's "
            "EXACT listed name - a near match lands elsewhere.")
    anchor = args.words or args.anchor
    if not normalize(anchor):
        raise CaptainEditError(
            "capture-transform needs --words: the words the moved clip "
            "speaks, which is what survives the next rebuild.")
    prop = args.prop or "Pan"
    if prop not in TRANSFORM_PROPERTIES:
        raise CaptainEditError(
            f"--property {prop!r} is not one the build sets: "
            f"{', '.join(TRANSFORM_PROPERTIES)}.")
    try:
        number = int(args.reel)
    except (TypeError, ValueError):
        raise CaptainEditError(
            f"--reel {args.reel!r} names no reel: capture reads the "
            f"approved moment to know which ranges the timeline plays.")
    transcript = load_transcript(args.project_folder)
    if transcript is None:
        raise CaptainEditError(
            "no transcript on file - without measured speech the words "
            "cannot resolve to a clip. Run the transcript first.")
    from library.tools.reel_proposal import (
        proposal_path as _proposal_path, read_proposal)
    from library.tools.reel_proposal import ProposalError as _ProposalError
    try:
        moments = read_proposal(str(_proposal_path(args.project_folder)))
    except (OSError, ValueError, _ProposalError) as exc:
        raise CaptainEditError(
            f"no readable reel proposal on file: {exc}. Capture reads "
            f"the approved moment to know which ranges the timeline "
            f"plays - without it the words cannot resolve to a clip.")
    moment = next((m for m in moments if int(m.number) == number), None)
    if moment is None:
        raise CaptainEditError(
            f"reel {number} is in no proposal on file.")
    from library.tools import reel_build as _build
    from library.tools import transcript_corrections as _tc
    try:
        keep_exclusions = _tc.keep_exclusions(args.project_folder)
        moment_cuts = _tc.grow_cuts_over_wordless_leadin(
            _tc.exclusion_cuts_for_span(
                moment.timeline_start, moment.timeline_end,
                keep_exclusions),
            transcript)
        moment_cuts, _ = _tc.grow_cuts_over_wordless_tail(
            moment_cuts, transcript)
        ranges = _build.reel_ranges(moment, transcript,
                                    extra_cuts=moment_cuts)
    except Exception as exc:  # noqa: BLE001 - the CLI edge reports
        raise CaptainEditError(
            f"reel {number}'s played ranges cannot be derived: "
            f"{exc}") from exc
    # A head card occupies reel seconds before any footage plays -
    # the words land that far later on the timeline. The same
    # arithmetic the build places from, so the capture reads the clip
    # the words actually play on. The frame is the declared delivery
    # format, resolved the same way the build resolves it - a capture
    # that plans cards against an assumed frame reads the wrong clip.
    _cap_w, _cap_h = _build.reel_resolution(args.project_folder)
    cards = _build.plan_cards(
        moment, transcript, ranges, args.project_folder,
        fps=24000 / 1001,
        width=_cap_w, height=_cap_h,
        declarations=_build.declared_cards(args.project_folder))
    lead = _build.lead_frames(cards, 24000 / 1001) / (24000 / 1001)
    reel_start, master_start, master_end = anchor_reel_time(
        ranges, transcript, anchor, lead_seconds=lead)
    # Centre on the words, not their edge: a frame at the anchor's
    # first word can round onto the previous item at a cut, and the
    # previous item is exactly the wrong clip to capture.
    try:
        import DaVinciResolveScript as dvr
    except ImportError:
        import os as _os
        _os.environ["RESOLVE_SCRIPT_API"] = (
            "/Library/Application Support/Blackmagic Design/"
            "DaVinci Resolve/Developer/Scripting")
        _os.environ["RESOLVE_SCRIPT_LIB"] = (
            "/Applications/DaVinci Resolve/DaVinci Resolve.app/"
            "Contents/Libraries/Fusion/libfusionscript.dylib")
        if "PYTHONPATH" not in _os.environ:
            _os.environ["PYTHONPATH"] = ""
        _os.environ["PYTHONPATH"] += ":" + _os.environ["RESOLVE_SCRIPT_API"] + "/Modules"
        sys.path.insert(0, _os.environ["RESOLVE_SCRIPT_API"] + "/Modules")
        import DaVinciResolveScript as dvr
    import yaml as _yaml
    with open(Path(str(args.project_folder)) / "project.yaml",
              encoding="utf-8") as handle:
        resolve_config = _yaml.safe_load(handle).get("resolve", {})
    resolve_name = resolve_config.get("project_name", "")
    master_name = resolve_config.get("timeline_name", "")
    from library.tools.resolve_locale import scriptapp_preserving_locale
    from library.tools.timeline_ingest import resolve_project_exactly
    resolve = scriptapp_preserving_locale(dvr, "Resolve")
    project = resolve_project_exactly(
        resolve.GetProjectManager(), resolve_name)
    timeline = None
    for index in range(1, project.GetTimelineCount() + 1):
        candidate = project.GetTimelineByIndex(index)
        if candidate.GetName() == args.timeline:
            timeline = candidate
            break
    if timeline is None:
        raise CaptainEditError(
            f"timeline {args.timeline!r} is not in Resolve project "
            f"{resolve_name!r}.")
    if args.track is not None:
        try:
            wanted = int(args.track)
        except (TypeError, ValueError):
            raise CaptainEditError(
                f"--track {args.track!r} names no video track.")
    else:
        wanted = None
    fps = float(timeline.GetSetting("timelineFrameRate") or 0) or 24000 / 1001
    start_frame = timeline.GetStartFrame()
    # Mid-anchor in reel seconds, so a cut exactly on the anchor's
    # first word cannot round the lookup onto the previous item.
    reel_second = reel_start + (master_end - master_start) / 2
    frame = start_frame + round(reel_second * fps)
    # Footage only: the frame, the captions and the motion graphics
    # all cover the same seconds, and none of them is the clip the
    # captain moved. What counts as footage is the master snapshot
    # the build places from - an exact membership, never an
    # extension guess.
    from library.tools.timeline_ingest import snapshot_timeline
    master = None
    for index in range(1, project.GetTimelineCount() + 1):
        candidate = project.GetTimelineByIndex(index)
        if candidate.GetName() == master_name:
            master = candidate
            break
    if master is None:
        raise CaptainEditError(
            f"master timeline {master_name!r} is not in Resolve "
            f"project {resolve_name!r} - the footage set cannot be "
            f"listed, so nothing is recorded.")
    footage = {clip.source_file for clip in
               snapshot_timeline(master, resolve_name).clips}
    covering = []
    for track in range(1, timeline.GetTrackCount("video") + 1):
        if wanted is not None and track != wanted:
            continue
        for item in timeline.GetItemListInTrack("video", track) or ():
            if not (item.GetStart() <= frame < item.GetEnd()):
                continue
            try:
                pool_item = item.GetMediaPoolItem()
                source = (pool_item.GetClipProperty("File Path")
                          if pool_item is not None else None)
            except Exception:  # noqa: BLE001 - unresolvable, skipped
                source = None
            if source in footage:
                covering.append((track, item))
    if not covering:
        raise CaptainEditError(
            f"no video item covers reel {reel_second:.2f}s "
            f"(frame {frame}) on {args.timeline!r} - the timeline "
            f"moved under the words.")
    tracks = sorted({track for track, _ in covering})
    if len(covering) > 1 and len(tracks) == 1:
        names = [item.GetName() for _, item in covering]
        raise CaptainEditError(
            f"{len(covering)} items on one track cover reel "
            f"{reel_second:.2f}s ({', '.join(names)}): the timeline "
            f"was re-cut under the words. Say which clip was moved.")
    value = None
    for track, item in covering:
        try:
            read = item.GetProperty(prop)
            value = float(read) if read is not None else None
        except Exception:  # noqa: BLE001 - unreadable, named below
            value = None
        if value is None:
            raise CaptainEditError(
                f"{item.GetName()!r} on V{track} does not serve "
                f"{prop} - the value cannot be read, so nothing is "
                f"recorded.")
    # Rounded to the pipeline's own precision (`punch_in_properties`
    # rounds Pan/Tilt to 3): the read-back float residue (-35.000...36)
    # is Resolve's, not the captain's, and the store keeps what the
    # captain can read back.
    #
    # CURRENCY WARNING (not enforced here): `value` above is read
    # through a by-index handle, and Pan/Tilt through a non-current
    # handle come back scaled by the current timeline's dimensions
    # over this one's (`reel_read.assert_timeline_current`,
    # docs/READING_A_TRANSFORM.md). A capture taken while another
    # timeline is current records a scaled value the build then
    # re-applies as truth. Open the reel (make it current) before
    # capturing; enforcing that here needs the cursor move this
    # read-only path deliberately avoids, so it stays a documented
    # condition until that tradeoff is decided.
    value = round(value, 3)
    if len(tracks) > 1:
        raise CaptainEditError(
            f"V{', V'.join(str(t) for t in tracks)} all cover reel "
            f"{reel_second:.2f}s ({anchor!r}): stacked angles need "
            f"one value each, and one capture cannot tell them apart. "
            f"Re-run per track after --track selects one.")
    edit = {"kind": "transform_override", "anchor_phrase": anchor,
            "property": prop, "value": value, "reason": args.reason}
    if args.on_reel.strip():
        edit["reel"] = args.on_reel.strip()
    return record_edit(args.project_folder, edit, args.source)


if __name__ == "__main__":
    raise SystemExit(main())
