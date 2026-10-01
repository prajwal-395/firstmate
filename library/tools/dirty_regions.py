"""What a touch-up changed, as DOMAINS and SPANS, so a check re-reads only that.

`reel_touchup.qualify` already knows structurally what a touch changes:
which row, which item, which record frames.  Nothing downstream was told.
Every verifier after a touch - `verify_render`, `ren watch` - re-read
the whole reel, so a caption swap at 32s paid for the other 44s too.

A touch receipt now carries a `dirty` block, and a consumer reads it with
`read_dirty`:

- `dirty_domains` - which KIND of check the change can move: `picture`
  (a video row), `audio` (an audio row), `structure` (a row was added;
  nothing plays differently until something is placed on it).
- `dirty_spans` - record-frame ranges `[start_frame, end_frame)` in the
  timeline's own frames, each widened by `HANDLE_SECONDS` on both sides
  and clamped to the reel, with the same range in reel seconds
  (`start_seconds`/`end_seconds`, from the timeline's first frame) and
  the CORE the edit itself touched (`core_start_seconds`/
  `core_end_seconds`).
- `whole_reel` - True, with `whole_reel_reason`, when the change cannot
  be bounded.  A consumer then checks everything, exactly as before.

What is NOT bounded, and falls back to the whole reel by name: an op this
module does not know, an item the pre-edit read cannot resolve, and a
timeline whose frame rate or first frame did not read.  A receipt written
before this module (no `dirty` block) reads as whole-reel too - never as
"nothing changed".

The handles are DERIVED, not chosen: a defect that starts inside the
edited span and runs on into untouched frames is only seen by a detector
shown at least that detector's minimum duration of it, so the handle is
the longest minimum duration a span-local detector in `render_qa` needs.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

PICTURE = "picture"
AUDIO = "audio"
STRUCTURE = "structure"
DOMAINS = (PICTURE, AUDIO, STRUCTURE)


def _handle_seconds() -> float:
    from library.tools import render_qa
    return max(render_qa.FREEZE_MIN_SECONDS, render_qa.BLACK_MIN_SECONDS)


#: Seconds each dirty span is widened by, both sides (module docstring).
HANDLE_SECONDS = _handle_seconds()


def _row_of(track: Mapping) -> str:
    prefix = "A" if str(track["type"]).lower().startswith("a") else "V"
    return f"{prefix}{int(track['index'])}"


def _domain_of(row: str) -> str:
    return AUDIO if str(row).upper().startswith("A") else PICTURE


def _clip(tracks: Sequence[Mapping], row: str, item) -> Mapping | None:
    for track in tracks:
        if _row_of(track) != str(row).upper():
            continue
        clips = list(track.get("clips") or ())
        try:
            index = int(item)
        except (TypeError, ValueError):
            return None
        return clips[index] if 0 <= index < len(clips) else None
    return None


def _clip_span(clip: Mapping) -> tuple:
    start = int(clip["record_in"])
    return start, start + int(clip["duration"])


def _whole(reason: str) -> dict:
    return {"whole_reel": True, "whole_reel_reason": reason,
            "dirty_domains": list(DOMAINS[:2]), "dirty_spans": []}


def _core_spans(edit: Mapping, tracks: Sequence[Mapping],
                reel_end: int) -> list | None:
    """`[(start, end, domain)]` the edit touches, or None if unbounded."""
    op = str(edit.get("op") or "")
    row = str(edit.get("row") or "").upper()
    if op == "add_row":
        return []
    if op == "add_overlay":
        try:
            start = int(edit["record"])
            return [(start, start + int(edit["duration"]), _domain_of(row))]
        except (KeyError, TypeError, ValueError):
            return None
    clip = _clip(tracks, row, edit.get("item"))
    if clip is None:
        return None
    start, end = _clip_span(clip)
    if op in ("set_properties", "swap_pixels", "set_enabled",
              "entry_motion", "remove_overlay"):
        return [(start, end, _domain_of(row))]
    if op == "move":
        try:
            to = int(edit["to_record"])
        except (KeyError, TypeError, ValueError):
            return None
        return [(start, end, _domain_of(row)),
                (to, to + (end - start), _domain_of(row))]
    if op == "retime":
        # A ripple shifts everything after the cut, on every row.
        try:
            grown = max(int(edit["duration"]) - (end - start), 0)
        except (KeyError, TypeError, ValueError):
            return None
        return [(start, reel_end + grown, PICTURE),
                (start, reel_end + grown, AUDIO)]
    return None


def dirty_from_spec(spec: Mapping, tracks: Sequence[Mapping], *,
                    first_frame, end_frame, fps) -> dict:
    """The `dirty` block for a touch, from its spec and the PRE-edit read.

    `tracks` is `reel_read.read_tracks` of the approved timeline before
    anything is staged; `first_frame`/`end_frame`/`fps` are what that
    timeline's `GetStartFrame`/`GetEndFrame`/`timelineFrameRate` returned.
    Pure: reads nothing else.
    """
    try:
        rate = float(fps)
        first = int(first_frame)
        last = int(end_frame)
    except (TypeError, ValueError):
        return _whole(f"the timeline's frame rate or frame range did not "
                      f"read (fps={fps!r}, first={first_frame!r}, "
                      f"end={end_frame!r})")
    if not rate > 0 or last < first:
        return _whole(f"the timeline's frame rate or frame range did not "
                      f"read (fps={fps!r}, first={first_frame!r}, "
                      f"end={end_frame!r})")

    domains: set = set()
    cores: list = []
    for position, edit in enumerate((spec or {}).get("edits") or ()):
        if not isinstance(edit, Mapping):
            return _whole(f"edit {position} is not a mapping")
        spans = _core_spans(edit, tracks, last)
        if spans is None:
            return _whole(f"edit {position} ({edit.get('op')!r} on "
                          f"{edit.get('row')!r}[{edit.get('item')!r}]) "
                          f"could not be bounded from the pre-edit read")
        if str(edit.get("op")) == "add_row":
            domains.add(STRUCTURE)
        for start, end, domain in spans:
            domains.add(domain)
            cores.append((start, end, domain))

    handle = math.ceil(HANDLE_SECONDS * rate)
    reel_end = max([last] + [end for _, end, _ in cores])
    out = []
    for start, end, domain in sorted(cores):
        out.append({
            "domain": domain,
            "start_frame": max(first, start - handle),
            "end_frame": min(reel_end, end + handle),
            "core_start_frame": start,
            "core_end_frame": end,
        })
    merged = _merge(out)
    for span in merged:
        for key in ("start", "end", "core_start", "core_end"):
            span[f"{key}_seconds"] = round(
                (span[f"{key}_frame"] - first) / rate, 3)
    return {"whole_reel": False, "whole_reel_reason": "",
            "dirty_domains": sorted(domains, key=DOMAINS.index),
            "dirty_spans": merged, "handle_seconds": HANDLE_SECONDS,
            "fps": rate, "first_frame": first}


def _merge(spans: list) -> list:
    """Overlapping spans of one domain become one; order is by start."""
    merged: list = []
    for span in sorted(spans, key=lambda s: (s["domain"], s["start_frame"])):
        last = merged[-1] if merged else None
        if (last and last["domain"] == span["domain"]
                and span["start_frame"] <= last["end_frame"]):
            for key, pick in (("end_frame", max), ("core_start_frame", min),
                              ("core_end_frame", max)):
                last[key] = pick(last[key], span[key])
            continue
        merged.append(dict(span))
    return sorted(merged, key=lambda s: (s["start_frame"], s["domain"]))


def read_dirty(receipts: Sequence[Mapping]) -> dict:
    """The union of several touch receipts' `dirty` blocks.

    Any receipt without one - written before this module, or by a path
    that does not emit one - makes the whole union whole-reel: a missing
    statement of what changed is never read as "nothing changed".
    """
    if not receipts:
        return _whole("no touch receipt was given")
    domains: set = set()
    spans: list = []
    for receipt in receipts:
        dirty = receipt.get("dirty") if isinstance(receipt, Mapping) else None
        if not isinstance(dirty, Mapping):
            return _whole(f"the receipt for "
                          f"{(receipt or {}).get('final')!r} carries no "
                          f"dirty block")
        if dirty["whole_reel"]:
            return _whole(dirty["whole_reel_reason"])
        domains.update(dirty["dirty_domains"])
        spans.extend(dirty["dirty_spans"])
    return {"whole_reel": False, "whole_reel_reason": "",
            "dirty_domains": sorted(domains, key=DOMAINS.index),
            "dirty_spans": _merge_seconds(spans)}


def _merge_seconds(spans: Sequence[Mapping]) -> list:
    merged: list = []
    for span in sorted(spans, key=lambda s: (s["domain"],
                                             s["start_seconds"])):
        last = merged[-1] if merged else None
        if (last and last["domain"] == span["domain"]
                and span["start_seconds"] <= last["end_seconds"]):
            for key, pick in (("end_seconds", max),
                              ("core_start_seconds", min),
                              ("core_end_seconds", max)):
                last[key] = pick(last[key], span[key])
            continue
        merged.append({k: span[k] for k in (
            "domain", "start_seconds", "end_seconds",
            "core_start_seconds", "core_end_seconds")})
    return sorted(merged, key=lambda s: (s["start_seconds"], s["domain"]))


def spans_for(dirty: Mapping, domains: Sequence[str]) -> list:
    """`[(start_s, end_s, core_start_s, core_end_s)]` over `domains`,
    merged across domains - one read of a second serves every domain."""
    rows = [s for s in dirty["dirty_spans"] if s["domain"] in domains]
    merged = _merge_seconds([dict(s, domain="any") for s in rows])
    return [(s["start_seconds"], s["end_seconds"], s["core_start_seconds"],
             s["core_end_seconds"]) for s in merged]


def load_receipts(paths: Sequence[str]) -> list:
    import json
    out = []
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            out.append(json.load(handle))
    return out


def predates(video_path: str, receipts: Sequence[Mapping]) -> str | None:
    """The receipt the file was rendered BEFORE, or None.

    A scoped check of a render taken before the touch would re-read the
    old pixels and pass them as the new ones.
    """
    import datetime as _dt
    import os
    rendered = _dt.datetime.fromtimestamp(os.path.getmtime(video_path),
                                          _dt.UTC)
    for receipt in receipts:
        started = receipt.get("started") if isinstance(receipt, Mapping) \
            else None
        if not started:
            continue
        if rendered < _dt.datetime.fromisoformat(started):
            return f"{receipt.get('final')!r} touched at {started}"
    return None


def describe(dirty: Mapping) -> str:
    """One line a person reads: what will be re-checked."""
    if dirty["whole_reel"]:
        return f"whole reel ({dirty['whole_reel_reason']})"
    spans = ", ".join(f"{s['domain']} {s['start_seconds']:.2f}-"
                      f"{s['end_seconds']:.2f}s" for s in dirty["dirty_spans"])
    return (f"domains {dirty['dirty_domains']}; spans "
            f"[{spans or 'none'}]")
