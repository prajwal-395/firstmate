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
    applied, skipped = [], []
    if not matched:
        return targets, applied, skipped, stale
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
    return targets, applied, skipped, stale
