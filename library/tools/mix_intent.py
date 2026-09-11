"""A hand-set audio level the rebuild must keep.

A fader the captain moves by hand on a timeline clip (bed down under a
whisper, one SFX tamed) is a decision about seconds the build
re-delivers from the plan every time: `audio_mix` turns the spine's
`music_behavior` words into dB, `mix_targets` curves them, and the next
build paints the plan's numbers straight over the hand move. Until now
that class had no owning layer - the plan owns the curve, nothing owns
the overrule.

The pin lives where other captain-supplied state lives,
`<project>/external/mix_intent.json` (the `overlay_intent.json`
precedent: checked, never asserted), anchored the way every durable
decision here is anchored - to SPOKEN WORDS, never to a timecode or a
frame::

    {"version": 1,
     "pins": [{"anchor_phrase": "it barely leaves a whisper",
               "target": "bed", "level_db": -28.0,
               "reason": "captain 2026-09-11: bed buries the whisper"}]}

`target` is `bed` (the music curve under the anchor's words) or `clip`
(the non-bed audio clips playing those words: SFX, stingers). The pin
applies AFTER the plan curve is built - the held value is the
captain's, never the plan's - and is stamped `"declared"` on what it
touches, so the coherence check can compare declared against applied.

What the applier needs, and why the builder supplies it
-------------------------------------------------------
A pin names master-transcript seconds (words); an OTIO target carries
clip-relative keyframes. The join between those two coordinate systems
is the placement the builder computed, so the caller annotates each
target with its master range (`target["master"] = (start, end)`) and
`apply_mix_intent` does the rest. A target with no master range is
SKIPPED loudly, never silently: applying a level to the wrong seconds
is worse than not applying it. The builder wire-up is five lines at
the `mix_targets` call site (annotate from placements, call this);
it is named here and not landed here because only a lane with a live
Resolve can prove a mix against a render.

A pin matching no timed words reports STALE; a `level_db` outside
`[-60, 12]` dB is REFUSED at write time (below hearing, above
Fairlight sense); a partial pin is refused, never merged.

`tests/test_orphan_owners.py`.
"""

from __future__ import annotations

import json
import math
import os

#: Schema version this reader honours.
INTENT_VERSION = 1

#: The file basename, under the project's external-inputs area.
INTENT_FILENAME = "mix_intent.json"

#: Pin targets: the music bed under the words, or the clips on them.
TARGETS = ("bed", "clip")

#: A level outside this is hearing damage or silence misfiled as
#: intent, and is refused rather than recorded.
MIN_DB = -60.0
MAX_DB = 12.0


class MixIntentError(ValueError):
    """The declared mix intent cannot be honoured as written."""


# ── Recording: validation ──────────────────────────────────────────

def _normalize(text: str) -> str:
    import re

    return re.sub(r"\s+", " ",
                  re.sub(r"[^\w\s']", "", (text or "").lower())).strip()


def validate_pins(value) -> list:
    """Structural check. Raises `MixIntentError` naming what is wrong."""
    if not isinstance(value, list) or not value:
        raise MixIntentError(
            "mix_intent pins must be a non-empty list of pins. An empty "
            "one is the absence of intent - leave the file out instead.")
    for index, pin in enumerate(value):
        label = f"mix_intent.pins[{index}]"
        if not isinstance(pin, dict):
            raise MixIntentError(f"{label} is not an object")
        anchor = pin.get("anchor_phrase")
        if not isinstance(anchor, str) or not _normalize(anchor):
            raise MixIntentError(
                f"{label}.anchor_phrase names no spoken words - a pin "
                f"is anchored to what was SAID, which is what survives "
                f"a rebuild.")
        for field in ("anchor_frame", "start", "end", "timeline_start",
                      "timeline_end", "frame"):
            if field in pin:
                raise MixIntentError(
                    f"{label} carries {field!r}: a timecode pin breaks "
                    f"the moment anything upstream re-times. Anchor to "
                    f"spoken words instead.")
        target = pin.get("target")
        if target not in TARGETS:
            raise MixIntentError(
                f"{label} names target {target!r}: one of "
                f"{', '.join(TARGETS)} - the bed under the words, or "
                f"the clips on them.")
        level = pin.get("level_db")
        if (isinstance(level, bool) or not isinstance(level, (int, float))
                or math.isnan(level) or level in (float("inf"),
                                                  float("-inf"))):
            raise MixIntentError(
                f"{label} carries level_db {level!r}: a pin names the "
                f"dB the target must hold.")
        if not MIN_DB <= float(level) <= MAX_DB:
            raise MixIntentError(
                f"{label} wants {level!r} dB: outside "
                f"[{MIN_DB:g}, {MAX_DB:g}] is hearing damage or "
                f"silence misfiled as intent. Silence is a spine "
                f"behaviour word, not a pin.")
        reason = pin.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise MixIntentError(
                f"{label} carries no 'reason' - the captain's own "
                f"words, which is what any listing reads back.")
    return value


def parse_intent(body: dict, source: str = INTENT_FILENAME) -> list:
    """Validated pins from a decoded intent file, or a refusal."""
    if not isinstance(body, dict):
        raise MixIntentError(
            f"{source} must be a JSON object, not "
            f"{type(body).__name__}.")
    if body.get("version") != INTENT_VERSION:
        raise MixIntentError(
            f"{source} declares version {body.get('version')!r}: this "
            f"reader honours version {INTENT_VERSION}.")
    pins = body.get("pins", [])
    if not isinstance(pins, list):
        raise MixIntentError(
            f"{source} carries pins={pins!r}, which is not a list.")
    if not pins:
        return []
    return validate_pins(pins)


def load_intent(project_folder=None,
                intent_file: str | None = None) -> list:
    """Validated pins for a project, or `[]` where none are declared."""
    if intent_file:
        if not os.path.isfile(intent_file):
            raise MixIntentError(
                f"intent file {intent_file!r} does not exist: refusing "
                f"rather than mixing without the declared levels.")
        with open(intent_file, encoding="utf-8") as handle:
            try:
                body = json.load(handle)
            except json.JSONDecodeError as exc:
                raise MixIntentError(
                    f"intent file {intent_file!r} is not JSON: "
                    f"{exc}") from exc
        return parse_intent(body, source=intent_file)
    if not project_folder:
        return []
    from library.tools.external_inputs import external_dir

    path = os.path.join(str(external_dir(project_folder)), INTENT_FILENAME)
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as handle:
        try:
            body = json.load(handle)
        except json.JSONDecodeError as exc:
            raise MixIntentError(
                f"{path} is not JSON: {exc}") from exc
    return parse_intent(body, source=path)


# ── Matching: words to master seconds ──────────────────────────────

def _word_stream(transcript: dict) -> list:
    stream = []
    for segment in (transcript or {}).get("segments") or ():
        for word in segment.get("words") or ():
            if not word.get("timed"):
                continue
            try:
                start, end = float(word["start"]), float(word["end"])
            except (KeyError, TypeError, ValueError):
                continue
            token = _normalize(str(word.get("word") or ""))
            if token and end > start:
                stream.append((token, start, end))
    return stream


def match_pins(transcript: dict, pins: list) -> tuple:
    """Each pin to the master spans its words occupy.

    Returns `(matched, stale)`: `matched` entries carry the pin plus
    `spans`, one `(start, end)` per occurrence; `stale` carries pins
    whose words the transcript no longer speaks, LOUDLY.
    """
    matched, stale = [], []
    stream = _word_stream(transcript or {})
    tokens = [token for token, _, _ in stream]
    for pin in pins or []:
        import re

        needle = _normalize(pin["anchor_phrase"]).split()
        occurrences = []
        for i in range(len(tokens) - len(needle) + 1):
            if needle and tokens[i:i + len(needle)] == needle:
                occurrences.append((stream[i][1],
                                    stream[i + len(needle) - 1][2]))
        if not occurrences:
            stale.append(
                {**pin, "reason": (
                    f"STALE: mix pin for {pin['anchor_phrase']!r} no "
                    f"longer applies - those words are spoken nowhere. "
                    f"Original request: "
                    f"{pin.get('reason', '')}".strip())})
            continue
        matched.append({**pin, "spans": occurrences})
    return matched, stale


# ── Applying: after the plan curve ─────────────────────────────────

def _rewrite_bed_keys(keys: dict, start_f: int, end_f: int,
                      level: float) -> dict:
    """A plateau at `level` across `[start_f, end_f)`, ramps preserved.

    Keys strictly inside the span are replaced by the plateau edge
    keys; boundary keys stand, so the plan's ramps into and out of the
    pinned seconds survive and only the seconds themselves obey.
    """
    out = {int(f): float(v) for f, v in (keys or {}).items()}
    for frame in [f for f in out if start_f < f < end_f]:
        del out[frame]
    out[int(start_f)] = float(level)
    if end_f > start_f:
        out[int(end_f)] = out.get(int(end_f), float(level))
    return dict(sorted(out.items()))


def apply_mix_intent(targets: list, transcript: dict, pins: list,
                     fps: float = 24000 / 1001) -> tuple:
    """Hold the captain's levels on the built mix targets. Returns
    `(targets, applied, skipped, stale)`.

    Each target needs `target["master"] = (start, end)` - the
    master-transcript range it plays, annotated by the builder from
    placements. A target with no master range is SKIPPED loudly: a
    level applied to unlocated seconds is a silent content change.
    `bed` pins plateau the music curve across the overlap; `clip`
    pins shift the clip's whole curve by the delta (shape preserved,
    so a de-click ramp stays a ramp). Everything touched is stamped
    `provenance: "declared"`.
    """
    matched, stale = match_pins(transcript, pins or [])
    targets, applied, skipped = _hold_levels(targets, matched, fps)
    return targets, applied, skipped, stale


def _hold_levels(targets: list, matched: list,
                 fps: float = 24000 / 1001) -> tuple:
    """Plateau matched pins onto annotated targets. Returns
    `(targets, applied, skipped)`.

    The shared core: `apply_mix_intent` matches in master-timeline
    clock and holds here; `apply_declared_mix` maps pins into edit
    clock first (`pins_in_edit_clock`) and holds the same way. One
    holding, never two answers to which seconds obey.
    """
    applied, skipped = [], []
    for target in targets or []:
        master = target.get("master") if isinstance(target, dict) else None
        try:
            if master is None:
                raise TypeError
            target_start, target_end = float(master[0]), float(master[1])
        except (TypeError, ValueError, IndexError):
            skipped.append(
                {"label": (target.get("label", "?") if isinstance(
                    target, dict) else "?"),
                 "reason": ("no master range annotated - the builder "
                            "did not say which seconds this clip plays, "
                            "so no pin touches it")})
            continue
        if not target_end > target_start:
            continue
        keys = dict(target.get("keyframes") or {})
        frames = sorted(keys)
        last = frames[-1] if frames else 0
        role = target.get("role", "")
        for pin in matched:
            if pin["target"] == "bed" and role != "music":
                continue
            if pin["target"] == "clip" and role == "music":
                continue
            level = float(pin["level_db"])
            for span_start, span_end in pin["spans"]:
                overlap_start = max(target_start, span_start)
                overlap_end = min(target_end, span_end)
                if not overlap_end > overlap_start:
                    continue
                span = target_end - target_start
                start_f = int(round((overlap_start - target_start)
                                    / span * last))
                end_f = int(round((overlap_end - target_start)
                                  / span * last))
                if pin["target"] == "bed":
                    target["keyframes"] = _rewrite_bed_keys(
                        target.get("keyframes") or {}, start_f, end_f,
                        level)
                else:
                    old = float(target.get("level_db", level))
                    delta = level - old
                    target["level_db"] = level
                    target["keyframes"] = {
                        f: round(v + delta, 3)
                        for f, v in (target.get("keyframes") or {}).items()}
                target["provenance"] = "declared"
                applied.append(
                    {"label": target.get("label", "?"),
                     "anchor_phrase": pin["anchor_phrase"],
                     "target": pin["target"], "level_db": level,
                     "master_span": [round(overlap_start, 3),
                                     round(overlap_end, 3)],
                      "reason": pin.get("reason", "")})
    return targets, applied, skipped


# ── Edit-clock join: the OTIO builder's half ─────────────────────────

def _segment_bridge(transcript: dict) -> list:
    """Each segment as a master-timeline to source-media bridge.

    Transcript words tick in master-timeline seconds (`timeline_start`
    names the same clock the first word starts on); V1 manifest clips
    name source-media seconds (`source_in`). The segment carries both
    plus the file, so it is the bridge: `source = when + offset`.
    Segments naming no file bridge nothing - words the transcript
    cannot place on media cannot be placed on the edit either.
    """
    rows = []
    for segment in (transcript or {}).get("segments") or ():
        if not isinstance(segment, dict):
            continue
        try:
            tl_start = float(segment.get("timeline_start",
                                         segment.get("start", 0)))
            tl_end = float(segment.get("timeline_end",
                                       segment.get("end", 0)))
            src_start = float(segment.get("source_start", tl_start))
        except (TypeError, ValueError):
            continue
        if not tl_end > tl_start:
            continue
        rows.append({"tl": (tl_start, tl_end),
                     "offset": src_start - tl_start,
                     "file": segment.get("source_file") or ""})
    return rows


def _to_source(rows: list, when: float):
    """`(source_file, source_seconds)` for a master-timeline instant."""
    for row in rows:
        start, end = row["tl"]
        if start - 1e-6 <= when <= end + 1e-6 and row["file"]:
            return row["file"], when + row["offset"]
    return None


def _v1_covering(manifest: dict, source_file: str, src_start: float,
                 src_end: float) -> list:
    """V1 clips playing `(src_start, src_end)` of `source_file`.

    Exact path first; a basename match counts only when it names ONE
    clip - two files sharing a name is ambiguity, and a level applied
    to the wrong seconds is worse than no level. Returns
    `[(edit_start, edit_end)]`, piecewise across clip edges.
    """
    v1 = [c for c in ((manifest or {}).get("tracks", {})
                      .get("V1", {}).get("clips", [])) or []
          if isinstance(c, dict) and c.get("source_file")]
    exact = [c for c in v1 if c.get("source_file") == source_file]
    pool = exact if exact else [
        c for c in v1
        if os.path.basename(str(c.get("source_file", "")))
        == os.path.basename(source_file)]
    if not exact and len({c.get("source_file") for c in pool}) > 1:
        return []
    out = []
    for clip in pool:
        try:
            clip_src = (float(clip["source_in"]),
                        float(clip["source_out"]))
            clip_edit = (float(clip["timeline_in"]),
                         float(clip["timeline_out"]))
        except (KeyError, TypeError, ValueError):
            continue
        overlap_start = max(clip_src[0], src_start)
        overlap_end = min(clip_src[1], src_end)
        if not overlap_end > overlap_start:
            continue
        out.append((clip_edit[0] + overlap_start - clip_src[0],
                    clip_edit[0] + overlap_end - clip_src[0]))
    return sorted(out)


def pins_in_edit_clock(transcript: dict, manifest: dict,
                       pins: list) -> tuple:
    """Rewrite each pin's spans from master-timeline into edit seconds.

    Returns `(edit_pins, stale)`: `edit_pins` carry `spans` on the
    manifest's clock, ready for `_hold_levels`; `stale` carries every
    occurrence the edit no longer plays - words the spine dropped have
    no V1 clip, so a level under them is a decision about silence, and
    is named as one - plus every pin `match_pins` already found stale.
    """
    matched, stale = match_pins(transcript, pins or [])
    rows = _segment_bridge(transcript or {})
    edit_pins, still_stale = [], list(stale)
    for pin in matched:
        edit_spans: list = []
        for span_start, span_end in pin.get("spans", []):
            mapped = _span_to_edit(rows, manifest, span_start, span_end)
            if not mapped:
                still_stale.append(
                    {**pin, "spans": [],
                     "reason": (
                         f"STALE: mix pin for {pin['anchor_phrase']!r} "
                         f"({span_start:.3f}-{span_end:.3f}s) plays "
                         f"nowhere in this edit - those words reached "
                         f"no V1 clip. Original request: "
                         f"{pin.get('reason', '')}".strip())})
                continue
            edit_spans.extend(mapped)
        if edit_spans:
            edit_pins.append({**pin, "spans": edit_spans})
        elif not any(s.get("anchor_phrase") == pin.get("anchor_phrase")
                     for s in still_stale):
            still_stale.append(
                {**pin, "reason": (
                    f"STALE: mix pin for {pin['anchor_phrase']!r} maps "
                    f"to nothing the edit plays. Original request: "
                    f"{pin.get('reason', '')}".strip())})
    return edit_pins, still_stale


def _span_to_edit(rows: list, manifest: dict, span_start: float,
                  span_end: float):
    """One master-timeline span to edit spans, piecewise. `[]` unmapped."""
    out: list = []
    cursor = span_start
    bounds = sorted({row["tl"][0] for row in rows}
                    | {row["tl"][1] for row in rows})
    cuts = [b for b in bounds if span_start < b < span_end]
    for edge in cuts + [span_end]:
        piece = (cursor, edge)
        cursor = edge
        located = _to_source(rows, (piece[0] + piece[1]) / 2.0)
        if located is None:
            return []
        source_file, _ = located
        src_start = piece[0] + next(
            row["offset"] for row in rows
            if row["file"] == source_file
            and row["tl"][0] - 1e-6 <= piece[0] <= row["tl"][1] + 1e-6)
        src_end = src_start + (piece[1] - piece[0])
        covered = _v1_covering(manifest, source_file, src_start, src_end)
        if not covered:
            return []
        out.extend(covered)
    return out


def annotate_targets(targets: list, manifest: dict,
                     fps: float = 24000 / 1001) -> tuple:
    """Give each mix target the edit range it plays, from the manifest.

    Matched on `(source_file, start_frame)` - the pair `mix_targets`
    built it from - exact first, then a basename match naming exactly
    one clip. Returns `(targets, skipped)`: a target naming no
    manifest clip is SKIPPED loudly, never held to unlocated seconds.
    """
    clips = []
    for track in ((manifest or {}).get("tracks") or {}).values():
        for clip in (track or {}).get("clips", []) or []:
            if isinstance(clip, dict) and clip.get("source_file"):
                clips.append(clip)
    skipped = []
    for target in targets or []:
        if not isinstance(target, dict):
            continue
        if target.get("master") is not None:
            continue
        try:
            want = int(target.get("start_frame", -1))
        except (TypeError, ValueError):
            want = -1
        hits = []
        for clip in clips:
            if clip.get("source_file") != target.get("source_file"):
                continue
            try:
                if "timeline_in_frame" in clip:
                    got = int(clip["timeline_in_frame"])
                else:
                    got = int(round(float(clip.get("timeline_in", 0.0))
                                    * fps))
            except (TypeError, ValueError):
                continue
            if got == want:
                hits.append(clip)
        if not hits:
            base = os.path.basename(str(target.get("source_file") or ""))
            candidates = [
                c for c in clips
                if os.path.basename(str(c.get("source_file") or ""))
                == base]
            starts = set()
            for clip in candidates:
                try:
                    starts.add(int(clip.get("timeline_in_frame", -2)))
                except (TypeError, ValueError):
                    continue
            if len(candidates) == 1 and want in starts:
                hits = candidates
        if len(hits) != 1:
            skipped.append(
                {"label": target.get("label", "?"),
                 "reason": ("names no single manifest clip "
                            f"({len(hits)} match(es)) - the builder did "
                            f"not say which seconds this clip plays, so "
                            f"no pin touches it")})
            continue
        clip = hits[0]
        target["master"] = (float(clip.get("timeline_in", 0.0)),
                            float(clip.get("timeline_out", 0.0)))
    return targets, skipped


def apply_declared_mix(targets: list, manifest: dict, transcript: dict,
                       pins: list, fps: float = 24000 / 1001) -> tuple:
    """The OTIO builder's consultation: annotate, map, hold. Returns
    `(targets, applied, skipped, stale)`.

    `mix_targets` builds plan-clocked curves; this moves the captain's
    pins onto them - targets annotated with the edit ranges they play,
    pins rewritten from master-timeline into edit seconds, held
    post-plan and stamped declared by the one `_hold_levels` every
    path shares. No pins and the targets pass through untouched: a
    project that declares nothing gets exactly the mix it always got.
    """
    if not pins:
        return targets, [], [], []
    targets, skipped = annotate_targets(targets, manifest, fps)
    edit_pins, stale = pins_in_edit_clock(transcript, manifest,
                                          pins or [])
    targets, applied, held_skipped = _hold_levels(targets, edit_pins,
                                                  fps)
    # Annotate already named every unlocatable target: holding it
    # again would report the same silence twice.
    already = {row.get("label") for row in skipped}
    held_skipped = [row for row in held_skipped
                    if row.get("label") not in already]
    return targets, applied, skipped + held_skipped, stale
