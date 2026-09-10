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
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

CAPTAIN_EDITS_KEY = "captain_edits"
"""The state key, and the file name: `<project>/external/captain_edits.json`."""

KINDS = ("caption_fix", "drop_fragment", "redraw_closer")
"""The complete vocabulary. `caption_fix` rewrites caption text;
`drop_fragment` removes the speech (and so the picture) that says it;
`redraw_closer` moves a shared closer's start to the anchor's words,
end fixed."""


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

    Reads the external file and validates it structurally. Drift
    against current speech is NOT judged here - the file must be
    readable before any spine exists (a first run), and drift is
    where it is LOUD: the external check refuses it pre-run where the
    spine is on file, and apply time reports it STALE otherwise."""
    if not project_folder:
        return []
    try:
        path = edits_path(project_folder)
    except (KeyError, ValueError):
        return []
    if not path.is_file():
        return []
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
    return validate_edits(document["value"])


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
    and play seconds twice, or an anchor no longer sitting on a clean
    segment edge. A reel closing on a different invitation is none of
    these - untouched, and the pin is not stale for it.
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
                if abs(snapped[0] - new_start) > 1e-6:
                    stale.append(
                        {"kind": "redraw_closer",
                         "reel": int(moment.number),
                         "anchor_phrase": anchor,
                         "reason": (
                             f"CANNOT APPLY on reel {int(moment.number)}: "
                             f"the anchor {anchor!r} at {new_start:.3f}s no "
                             f"longer sits on a clean segment edge (snaps "
                             f"to {snapped[0]:.3f}s) - the transcript "
                             f"re-segmented around it. Re-anchor to words "
                             f"the transcript still bounds. Original "
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


def main(argv=None) -> int:
    """`python3 -m library.tools.captain_edits <project_folder>` - the
    plain-language listing of edits in force."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: python3 -m library.tools.captain_edits "
              "<project_folder>", file=sys.stderr)
        return 2
    try:
        edits = load_edits(argv[0])
    except CaptainEditError as exc:
        print(f"REFUSED\n\n{exc}\n")
        return 1
    if not edits:
        print(f"No captain edits in force for {argv[0]}.")
        return 0
    for line in describe_edits(edits):
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
