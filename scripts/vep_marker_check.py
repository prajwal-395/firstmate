#!/usr/bin/env python3
"""Per-reel marker before/after check: the exit code is the gate.

Four hand-written marker rules were followed correctly on 2026-09-20
while the thing each protected was destroyed - a read-back inside the
writing process, a capture reported rather than proven, a count that
was mechanically correct and wrong by four, a carry policy sound and
structurally blind. A rule an agent holds is worth nothing; a check
inside the command is worth everything. This is that check, narrowed
to one reel and one operation.

Two modes; both speak through `resolve-axi` (on PATH), never through
hand-written Resolve scripts:

- `capture --timeline "<exact full name>" --out <path>`: reads every
  marker on the reel, BOTH planes, full content - text, colour, name,
  plane, frame, duration, timecode, and for clip markers the anchor
  (track, item file, item start/end, clip-local offset, custom data) -
  and writes it to `<path>` (a durable path OUTSIDE the worktree).
- `verify --timeline "<exact full name>" --against <path>`: re-reads
  live and diffs against the capture. Exit 0 only when every captured
  marker is present with the same text, colour AND anchor. Anything
  missing or moved is named on stderr and the exit is 1. Usage and
  environment failures exit 2.

The intended loop, per reel, one at a time::

    vep_marker_check.py capture --timeline "Reel 09 - ..." --out r09.json
    ... the operation (swap, promotion, rebuild) ...
    vep_marker_check.py verify --timeline "Reel 09 - ..." --against r09.json

A non-zero verify is a STOP, not a warning: restore byte-identical at
the seam derived from the actual change, then continue. The check
reads from a SEPARATE process after the writing script has exited -
an in-script read-back is not evidence (measured 2026-09-20).

Two things this check deliberately does NOT do:

- It never writes a marker. Restore is by hand (or `resolve-axi
  markers reply --apply` for replies), because a check that can write
  is a second writer with its own failure modes.
- `resolve-axi markers snapshot` is NOT a substitute: it restores only
  the timeline plane, while 17 of the fleet's notes are clip-anchored.
  The clip half here exists for exactly that hole.

`compare()` is pure and unit-tested (`tests/unit/resolve/test_vep_marker_check.py`);
only the readers shell out to Resolve.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile

# Every subprocess call capturing text passes encoding="utf-8"
# (AGENTS.md 9): the pipeline writes UTF-8 status glyphs and
# text=True would decode with the locale codec.
_ENCODING = "utf-8"

_INVENTORY_SCRIPT = """\
result = []
for _ttype in ('video', 'audio'):
    for _tr in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12):
        try:
            _items = timeline.GetItemListInTrack(_ttype, _tr) or []
        except Exception:
            continue
        for _it in _items:
            try:
                _n = _it.GetName()
            except Exception:
                _n = '?'
            try:
                _s, _e = _it.GetStart(), _it.GetEnd()
            except Exception:
                _s, _e = -1, -1
            try:
                _marks = _it.GetMarkers() or {}
            except Exception:
                _marks = {}
            if not _marks:
                continue
            try:
                _pool = _it.GetMediaPoolItem()
                _f = _pool.GetClipProperty('File Path') if _pool is not None else ''
            except Exception:
                _f = 'unreadable'
            _out = []
            for _k, _v in _marks.items():
                _d = dict(_v) if isinstance(_v, dict) else {'repr': str(_v)}
                _out.append({'offset': int(_k),
                             'color': str(_d.get('color') or ''),
                             'duration': int(_d.get('duration') or 1),
                             'name': str(_d.get('name') or ''),
                             'note': str(_d.get('note') or ''),
                             'custom_data': str(_d.get('customData') or '')})
            result.append({'track': '%s%d' % (_ttype, _tr), 'item': _n,
                           'source_file': str(_f), 'start': _s, 'end': _e,
                           'markers': _out})
"""


def _run_axi(argv: list) -> str:
    proc = subprocess.run(
        ["resolve-axi"] + argv, capture_output=True,
        encoding=_ENCODING, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"resolve-axi {' '.join(argv)} exited {proc.returncode}: "
            f"{(proc.stderr or proc.stdout or '').strip()[-600:]}")
    return proc.stdout or ""


def _from_json_mixed(output: str):
    """The first JSON value in mixed CLI output (headers/help surround it)."""
    decoder = json.JSONDecoder()
    index = output.find("[")
    brace = output.find("{")
    if brace != -1 and (index == -1 or brace < index):
        return decoder.raw_decode(output[brace:])
    if index == -1:
        raise RuntimeError("no JSON in resolve-axi output")
    return decoder.raw_decode(output[index:])


def read_timeline_markers(timeline: str) -> list:
    """Every timeline-plane marker with full content, via snapshot file."""
    with tempfile.NamedTemporaryFile(suffix=".json",
                                     delete=False) as handle:
        path = handle.name
    try:
        _run_axi(["markers", "snapshot", "--timeline", timeline,
                  "--out", path])
        with open(path, encoding=_ENCODING) as handle:
            document = json.load(handle)
    finally:
        os.path.exists(path) and os.remove(path)
    if document.get("timeline") != timeline:
        raise RuntimeError(
            f"snapshot resolved to {document.get('timeline')!r}, not "
            f"{timeline!r}: resolve timelines by EXACT full name.")
    return document.get("notes") or []


def read_clip_markers(timeline: str) -> list:
    """Every clip-anchored marker with its anchor, via run --json."""
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w",
                                     delete=False,
                                     encoding=_ENCODING) as handle:
        handle.write(_INVENTORY_SCRIPT)
        script = handle.name
    try:
        output = _run_axi(["run", "--timeline", timeline,
                           "--file", script, "--json"])
    finally:
        os.path.exists(script) and os.remove(script)
    items, _ = _from_json_mixed(output)
    return items


def capture(timeline: str) -> dict:
    """Both planes, full content, one document."""
    return {"tool": "vep_marker_check/capture", "timeline": timeline,
            "timeline_markers": read_timeline_markers(timeline),
            "clip_items": read_clip_markers(timeline)}


def _timeline_key(note: dict) -> tuple:
    return ("timeline", str(note.get("color") or ""),
            str(note.get("name") or ""), str(note.get("note") or ""))


def _clip_key(item: dict, marker: dict) -> tuple:
    return ("clip", str(item.get("track") or ""),
            str(item.get("item") or ""), int(item.get("start") or -1),
            int(marker.get("offset") or -1),
            str(marker.get("color") or ""), str(marker.get("name") or ""),
            str(marker.get("note") or ""))


def compare(captured: dict, live: dict) -> list:
    """Names every captured marker the live read does not hold.

    Identity is text AND colour AND anchor: a note whose words survive
    under another colour, or on another item, is a changed note, not a
    kept one. Frame/duration drift on an otherwise identical note is
    reported inside the finding but does not fail on its own - the
    operation this gates (an overlay swap) moves nothing, so any drift
    is said, not punished... except it IS punished: a clip marker keys
    replies by clip-local frame, so a moved clip note orphans its
    thread. Anchor (timeline frame, item, offset) must match exactly.
    """
    failures = []

    live_timeline = {}
    for note in live.get("timeline_markers") or []:
        live_timeline.setdefault(_timeline_key(note), []).append(note)
    for note in captured.get("timeline_markers") or []:
        if note.get("source") not in (None, "", "timeline_marker"):
            continue
        candidates = live_timeline.get(_timeline_key(note), [])
        if not candidates:
            failures.append(
                f"missing timeline marker {note.get('color')}/{note.get('name')} "
                f"frame {note.get('frame')}: "
                f"{str(note.get('note') or '')[:120]}")
            continue
        frames = {c.get("frame") for c in candidates}
        if note.get("frame") not in frames:
            failures.append(
                f"moved timeline marker {note.get('color')}/{note.get('name')} "
                f"was frame {note.get('frame')}, now {sorted(frames)}: "
                f"{str(note.get('note') or '')[:120]}")

    live_clip = {}
    for item in live.get("clip_items") or []:
        for marker in item.get("markers") or []:
            live_clip.setdefault(_clip_key(item, marker), []).append(
                (item, marker))
    for item in captured.get("clip_items") or []:
        for marker in item.get("markers") or []:
            candidates = live_clip.get(_clip_key(item, marker), [])
            if not candidates:
                failures.append(
                    f"missing clip marker {marker.get('color')}/{marker.get('name')} "
                    f"{item.get('track')} {item.get('item')} "
                    f"start {item.get('start')} offset {marker.get('offset')}: "
                    f"{str(marker.get('note') or '')[:120]}")
    return failures


def cmd_capture(args) -> int:
    try:
        document = capture(args.timeline)
    except RuntimeError as exc:
        print(f"capture failed: {exc}", file=sys.stderr)
        return 2
    timeline_notes = sum(
        1 for n in document["timeline_markers"]
        if n.get("source") in (None, "", "timeline_marker"))
    clip_notes = sum(len(i.get("markers") or [])
                     for i in document["clip_items"])
    with open(args.out, "w", encoding=_ENCODING) as handle:
        json.dump(document, handle, indent=2)
    print(f"captured {args.timeline}: {timeline_notes} timeline notes, "
          f"{clip_notes} clip notes on "
          f"{len(document['clip_items'])} items -> {args.out}")
    return 0


def cmd_verify(args) -> int:
    try:
        with open(args.against, encoding=_ENCODING) as handle:
            captured = json.load(handle)
    except (OSError, ValueError) as exc:
        print(f"verify failed: cannot read {args.against} ({exc})",
              file=sys.stderr)
        return 2
    if captured.get("timeline") != args.timeline:
        print(f"verify failed: capture is for {captured.get('timeline')!r}, "
              f"not {args.timeline!r}", file=sys.stderr)
        return 2
    try:
        live = capture(args.timeline)
    except RuntimeError as exc:
        print(f"verify failed: live read failed ({exc})", file=sys.stderr)
        return 2
    failures = compare(captured, live)
    if failures:
        print(f"MARKER CHECK FAILED on {args.timeline} "
              f"({len(failures)} missing/moved):", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    timeline_notes = sum(
        1 for n in captured["timeline_markers"]
        if n.get("source") in (None, "", "timeline_marker"))
    clip_notes = sum(len(i.get("markers") or [])
                     for i in captured["clip_items"])
    print(f"marker check clean on {args.timeline}: {timeline_notes} "
          f"timeline + {clip_notes} clip notes all present with text, "
          f"colour and anchor")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Per-reel marker before/after check; the exit code "
                    "is the gate.")
    sub = parser.add_subparsers(dest="mode", required=True)
    cap = sub.add_parser("capture", help="write both planes to a file")
    cap.add_argument("--timeline", required=True)
    cap.add_argument("--out", required=True)
    cap.set_defaults(func=cmd_capture)
    ver = sub.add_parser("verify", help="diff live against a capture")
    ver.add_argument("--timeline", required=True)
    ver.add_argument("--against", required=True)
    ver.set_defaults(func=cmd_verify)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
