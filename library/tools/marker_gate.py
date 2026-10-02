"""A promotion that loses a marker fails here, even when its own report says it did not.

The defect this closes
----------------------
Measured 2026-09-20: a promotion that destroyed two of the captain's
clip-anchored markers printed "carried x3, uncarried 0" - a FALSE
ALL-CLEAR, which is strictly worse than silence, because a clean
report ends a check while silence invites one. Anyone auditing that
promotion from its own output would have closed the question and
moved on; only an external count of his blue markers contradicted it.

So the count comes from OUTSIDE the machinery being audited. The
carry (`library/tools/marker_carry.py`) now reads both planes and the
uncarried report is filed as an obligation
(`library/tools/uncarried_notes.py`) - both landed after the
false-all-clear - but both are still the machinery auditing itself:
whatever the carry plan claims, this gate re-reads the live timeline
after the rename, both planes, and diffs by identity against a
capture taken before it. A marker the plan said was carried but the
live timeline does not hold fails here by name, with his words.

What the gate still adds once carry-plus-obligations exist
----------------------------------------------------------
Three things neither of them is:

* an INDEPENDENT read. The carry plan's carried/uncarried split is
  trusted for what it REPORTS (a reported loss is accounted, never
  re-alarmed) but never for what SURVIVED: survival is established by
  a fresh `GetMarkers` read off the renamed timeline, not by the
  plan's own lists. A place-decline swallowed, a read bug, or a new
  plane nobody taught the carry all fail here, because none of them
  can forge the live inventory.
* a RECOVERY record for the clip plane. `uncarried_notes` files
  timeline-plane asks only, and the retired backup holding the words
  is DELETED by default in the same promotion. The capture this gate
  writes per reel per operation holds full CONTENT - both planes,
  every colour, full text, clip anchors with source file and frame
  range - so a lost note can be put back where it belongs rather
  than where its words suggest. The worked case: a note destroyed
  at frame 551 belonged at 523 after a 78-frame cut upstream, and
  the recovered TEXT could never have produced 523 - only the
  anchor in the capture can.
* a FLEET backstop. The three 2026-09-20 losses were found
  fleet-wide; a per-reel check would have found them faster, but a
  per-reel check cannot see cross-reel damage. Reels this operation
  did not touch must read back exactly what they held before it.

The gate compares COUNTS to alarm; the capture holds CONTENT to
recover. A count-only snapshot has already destroyed a note on this
project: it detected nothing and left nothing to restore from.

When it runs
------------
After every promotion, on each reel that had anything to lose -
`reel_build.promote_staged_reels` and `reel_touchup._promote` call
`verify_promotion` after placement and before the backup is deleted
or retired, so a refusal still has the retired generation to recover
from. The baseline is ALWAYS the capture taken immediately before
the rename in the same run, never a stored constant: a hardcoded
count rots the moment the captain adds a note, and a rotted
baseline fails in the reassuring direction.

What fails, and what only reports
---------------------------------
* A pre-operation marker that is neither on the live timeline nor in
  the carry's reported-loss lists is a SILENT loss: `MarkerGateLost`
  names every one with colour, words and anchor, and the promotion
  refuses. Extra markers the operation ADDED (seam Blues, replies)
  never fail - the gate proves nothing was lost, not that nothing
  changed.
* A fleet decrease on a reel this operation did not touch also
  refuses: nothing in the promotion writes to other reels, so a
  smaller count there is either damage or a concurrent edit, and
  both deserve a stopped run saying exactly which reel shrank.
  Fleet increases and unreadable fleet rows only report: the
  captain adding a note mid-run is legitimate, and an unreadable
  row is not a loss.
* An unreadable LIVE re-read refuses (`MarkerCarryUnreadable`
  propagates): a promotion that cannot see the words it just moved
  must not proceed to delete the generation that still holds them.

`tests/unit/resolve/test_marker_carry.py`.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from typing import Mapping

#: What a capture file on disk declares itself as.
CAPTURE_FORMAT = "marker_gate_capture/1"

#: Captures live beside the sign-offs, holds and uncarried obligations,
#: on the version-control allow-list's `review/**` line - so no
#: allow-list change was needed. One file per reel per operation;
#: they accumulate deliberately, because the retired backup they
#: describe is deleted by default and the capture is then the only
#: record holding the anchors a recovery needs.
CAPTURES_DIRNAME = "marker_captures"


class MarkerGateLost(RuntimeError):
    """The live inventory after an operation is missing a captured marker."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _words(entry: Mapping) -> str:
    """A marker's comparable words, through the ledger's own precedence."""
    from library.tools import feedback_ledger as _ledger

    return _ledger.normalise_text(_ledger.note_text(entry or {}))


def _timeline_anchor(entry: Mapping):
    """A timeline-plane anchor as `(source_file, source_frame)`, or None.

    Accepts the tuple `read_markers` records and the list a capture
    file deserialises it into, so a live read and a capture read back
    off disk compare equal.
    """
    anchor = (entry or {}).get("anchor")
    if anchor is None:
        return None
    if isinstance(anchor, Mapping):
        path, frame = anchor.get("source_file"), anchor.get("source_frame")
        if path is None:
            path, frame = anchor.get(0), anchor.get(1)
    else:
        try:
            path, frame = anchor[0], anchor[1]
        except (TypeError, IndexError, KeyError):
            return None
    if not isinstance(path, str) or not path:
        return None
    try:
        frame = int(frame)
    except (TypeError, ValueError):
        return None
    if isinstance(frame, bool):
        return None
    return (path, frame)


def _clip_anchor(entry: Mapping):
    """A clip-plane anchor as `(source_file, source_frame)`, or None."""
    anchor = (entry or {}).get("anchor")
    if not isinstance(anchor, Mapping):
        return None
    path, frame = anchor.get("source_file"), anchor.get("source_frame")
    if not isinstance(path, str) or not path:
        return None
    try:
        frame = int(frame)
    except (TypeError, ValueError):
        return None
    if isinstance(frame, bool):
        return None
    return (path, frame)


def _identity(plane: str, entry: Mapping) -> tuple:
    """What must match for a marker to count as survived.

    Colour, words and anchor - never the frame. A rebuild moves every
    frame, which is why the carry resolves by picture; a gate keyed on
    frames would fail every legitimate carry. The anchor stays because
    a carry that attaches his words to the wrong picture is the same
    class of loss wearing a passing count.
    """
    color = str((entry or {}).get("color") or "").strip()
    if plane == "clip":
        return ("clip", color, _words(entry), _clip_anchor(entry))
    return ("timeline", color, _words(entry), _timeline_anchor(entry))


def _multiset(entries, plane: str) -> dict:
    counts: dict = {}
    for entry in entries or ():
        if not isinstance(entry, Mapping):
            continue
        key = _identity(plane, entry)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _representative(entries, plane: str, key: tuple):
    """One full-content entry behind an identity, for the alarm."""
    for entry in entries or ():
        if isinstance(entry, Mapping) and _identity(plane, entry) == key:
            return entry
    return {}


def assemble_capture(reel_name: str, timeline_entries,
                   clip_entries) -> dict:
    """A capture around lists already read, with no further Resolve call.

    The promotion paths read both planes in phase 0 for the carry
    plan; the gate's baseline wraps those same lists rather than
    reading twice, so the capture and the plan start from one sighting.
    """
    return {
        "format": CAPTURE_FORMAT,
        "reel": str(reel_name or ""),
        "captured_at": _now(),
        "timeline": list(timeline_entries or ()),
        "clip": list(clip_entries or ()),
    }


def capture_reel(timeline, reel_name: str) -> dict:
    """The full-content baseline for one reel, read before the rename.

    Both planes, every colour, full text, anchors - the capture the
    gate alarms on and a recovery reads from. Raises
    `MarkerCarryUnreadable` when either plane will not read: a
    promotion that cannot see the words must not replace them.
    """
    from library.tools import marker_carry as _markers

    return assemble_capture(
        reel_name,
        _markers.read_markers(timeline, reel_name),
        _markers.read_clip_markers(timeline, reel_name))


def _jsonable(entry: Mapping) -> dict:
    """One capture entry as JSON-plain: tuples become lists."""
    out = dict(entry or {})
    anchor = out.get("anchor")
    if isinstance(anchor, tuple):
        out["anchor"] = list(anchor)
    return out


def capture_to_json(capture: Mapping) -> dict:
    """A capture as JSON-plain, for disk and for the `--check` CLI."""
    return {
        "format": capture.get("format") or CAPTURE_FORMAT,
        "reel": str(capture.get("reel") or ""),
        "captured_at": str(capture.get("captured_at") or ""),
        "timeline": [_jsonable(e) for e in (capture.get("timeline") or ())],
        "clip": [_jsonable(e) for e in (capture.get("clip") or ())],
    }


def _capture_filename(reel_name: str, captured_at: str) -> str:
    stem = "".join(
        c if c.isalnum() or c in "-_." else "_" for c in (reel_name or "reel"))
    stamp = "".join(
        c if c.isalnum() else "" for c in (captured_at or ""))[:20] or "once"
    return f"{stem or 'reel'}-{stamp}.json"


def write_capture(project_folder: str, reel_name: str,
                  capture: Mapping) -> str | None:
    """File one reel's capture before the rename. Returns the path.

    Writes nothing and returns None when the reel held no markers on
    either plane: nothing to lose means nothing to recover. A write
    failure RAISES: the capture is the recovery record, and a
    promotion that cannot file it must not delete the generation
    that still holds the words.
    """
    timeline = list((capture or {}).get("timeline") or ())
    clip = list((capture or {}).get("clip") or ())
    if not timeline and not clip:
        return None
    review_dir = os.path.join(str(project_folder), "pipeline_output",
                              "review", CAPTURES_DIRNAME)
    os.makedirs(review_dir, exist_ok=True)
    path = os.path.join(
        review_dir,
        _capture_filename(str(reel_name or ""),
                          str((capture or {}).get("captured_at") or "")))
    staged, handle = tempfile.mkstemp(
        dir=review_dir, prefix=".capture-", suffix=".tmp")
    try:
        with os.fdopen(staged, "w", encoding="utf-8") as file:
            json.dump(capture_to_json(capture), file, indent=2,
                      ensure_ascii=False)
            file.write("\n")
        os.replace(handle, path)
    except BaseException:
        try:
            os.unlink(handle)
        except OSError:
            pass
        raise
    return path


def read_capture(path: str) -> dict:
    """A capture file back off disk, in capture shape."""
    with open(path, encoding="utf-8") as file:
        data = json.load(file)
    return {
        "format": data.get("format") or CAPTURE_FORMAT,
        "reel": str(data.get("reel") or ""),
        "captured_at": str(data.get("captured_at") or ""),
        "timeline": list(data.get("timeline") or ()),
        "clip": list(data.get("clip") or ()),
    }


def verify_reel(reel_name: str, capture: Mapping,
                 live_timeline, live_clip,
                 accounted_timeline=(), accounted_clip=()) -> dict:
    """Diff a capture against a fresh live inventory. Pure: reads nothing.

    Every captured marker must be EITHER on the live timeline (by
    identity - colour, words, anchor, never frame) OR in the carry's
    reported-loss lists (uncarried, declined, replace-declined - the
    losses the machinery already owns and the obligations file).
    Anything in neither is a SILENT loss: the false-all-clear shape,
    where the plan said "carried" and the words are gone.

    Returns `{"missing", "before", "after", "accounted"}`; `missing`
    holds full-content entries, so the alarm names the words and a
    recovery holds the anchors. Extra live markers never appear:
    the gate proves nothing was lost, not that nothing changed.
    """
    before_t = _multiset((capture or {}).get("timeline"), "timeline")
    before_c = _multiset((capture or {}).get("clip"), "clip")
    after_t = _multiset(live_timeline, "timeline")
    after_c = _multiset(live_clip, "clip")
    owned_t = _multiset(accounted_timeline, "timeline")
    owned_c = _multiset(accounted_clip, "clip")
    missing = []
    for plane, before, after, owned in (
            ("timeline", before_t, after_t, owned_t),
            ("clip", before_c, after_c, owned_c)):
        source = ((capture or {}).get(plane) or ())
        for key in sorted(before, key=repr):
            short = before[key] - after.get(key, 0) - owned.get(key, 0)
            for _ in range(max(0, short)):
                missing.append({"plane": plane,
                                **_representative(source, plane, key)})
    missing.sort(key=lambda e: (
        e.get("plane") or "",
        str((_timeline_anchor(e) or _clip_anchor(e) or ""))))
    return {
        "missing": missing,
        "before": sum(before_t.values()) + sum(before_c.values()),
        "after": sum(after_t.values()) + sum(after_c.values()),
        "accounted": sum(owned_t.values()) + sum(owned_c.values()),
    }


def _describe(entry: Mapping) -> str:
    """One missing marker as a human-readable alarm line."""
    plane = (entry or {}).get("plane") or "timeline"
    color = (entry or {}).get("color") or "?"
    name = (entry or {}).get("name") or ""
    words = str((entry or {}).get("note") or "").strip()
    anchor = (_clip_anchor(entry) if plane == "clip"
              else _timeline_anchor(entry))
    if anchor is not None:
        import os as _os

        where = (f"{_os.path.basename(anchor[0])} source {anchor[1]}")
    elif (entry or {}).get("frame") is not None:
        where = f"timeline frame {(entry or {}).get('frame')}"
    else:
        where = "no anchored picture"
    return (f"{plane} {color} {name!r} ({where}) - "
            f"the captain wrote: {words!r}")


def verify_promotion(reel_name: str, capture: Mapping, live_timeline_obj,
                     carry_record: Mapping | None) -> dict:
    """Re-read one promoted reel live and refuse on any silent loss.

    The OUTSIDE count: `live_timeline_obj` (the staging object post
    rename) is read fresh off Resolve here, both planes - never from
    the carry plan's lists. The plan is trusted only for what it
    REPORTED: its uncarried/declined/replace-declined entries account
    for markers the machinery already owns. Raises `MarkerGateLost`
    naming every missing marker with his words; a clean reel prints
    nothing and returns the count report.
    """
    from library.tools import marker_carry as _markers

    record = carry_record or {}
    accounted_timeline = list(record.get("uncarried") or ())
    for key in ("declined", "replace_declined"):
        accounted_timeline.extend(record.get(key) or ())
    accounted_clip = list(record.get("clip_uncarried") or ())
    for key in ("clip_declined",):
        accounted_clip.extend(record.get(key) or ())
    live_t = _markers.read_markers(live_timeline_obj, reel_name)
    live_c = _markers.read_clip_markers(live_timeline_obj, reel_name)
    result = verify_reel(reel_name, capture, live_t, live_c,
                         accounted_timeline, accounted_clip)
    if result["missing"]:
        lines = [
            f"MARKER GATE LOST on {reel_name!r}: "
            f"{len(result['missing'])} captured marker(s) are neither "
            f"on the live timeline nor in the carry's reported losses "
            f"(before {result['before']}, live {result['after']}, "
            f"reported {result['accounted']}). The carry's own report "
            f"cannot vouch for them - recover from the capture, not "
            f"from the report."]
        for entry in result["missing"]:
            lines.append(f"  LOST: {_describe(entry)}")
        print("\n".join(lines), file=sys.stderr, flush=True)
        raise MarkerGateLost("\n".join(lines))
    return result


def fleet_snapshot(project) -> dict:
    """A count-only inventory of every timeline, both planes.

    The cheap backstop: `{timeline name: {"timeline": n, "clip": n}}`,
    or `{"unreadable": reason}` for a row that will not read. An
    unreadable row REPORTS here rather than refusing - fleet staleness
    is not a loss, and the per-reel gate stays fail-closed where it
    counts.

    The counts come through `marker_carry`'s own readers, never a new
    direct `GetMarkers` here: the independence this gate adds is the
    COMPARISON against the pre-operation capture, not a second probe
    (`tests/unit/reels/test_reel_read.py` refuses new probes outside its reader
    list, and rightly so).
    """
    from library.tools import marker_carry as _markers

    snapshot: dict = {}
    try:
        count = int(project.GetTimelineCount() or 0)
    except Exception as unreadable:                       # noqa: BLE001
        return {"": {"unreadable": f"timeline count: {unreadable}"}}
    for index in range(1, count + 1):
        try:
            timeline = project.GetTimelineByIndex(index)
        except Exception as unreadable:                   # noqa: BLE001
            snapshot[f"#{index}"] = {"unreadable": str(unreadable)}
            continue
        if timeline is None:
            continue
        try:
            name = timeline.GetName()
        except Exception as unreadable:                   # noqa: BLE001
            snapshot[f"#{index}"] = {"unreadable": str(unreadable)}
            continue
        try:
            own = _markers.read_markers(timeline, name)
            timeline_count = len(own)
        except Exception as unreadable:                   # noqa: BLE001
            snapshot[name] = {"unreadable": f"timeline markers: "
                                            f"{unreadable}"}
            continue
        try:
            clip_count = len(_markers.read_clip_markers(timeline, name))
        except Exception as unreadable:                   # noqa: BLE001
            snapshot[name] = {"unreadable": f"clip markers: "
                                            f"{unreadable}"}
            continue
        snapshot[name] = {"timeline": timeline_count,
                          "clip": clip_count}
    return snapshot


def check_fleet(before: Mapping, after: Mapping,
                operated: set | frozenset = frozenset()) -> dict:
    """Diff two fleet snapshots. Pure: reads nothing.

    Reels this operation touched (finals, stagings, backups) are
    excluded - the per-reel gate vouches for them by identity, and a
    count there legitimately moves (seam Blues, replies). Every other
    reel present in both snapshots must read back exactly what it
    held: a DECREASE there is damage or a concurrent edit, and both
    deserve a stopped run. An increase only reports - the captain
    adding a note mid-run is legitimate.
    """
    operated = set(operated or ())
    decreased, increased, unreadable = [], [], []
    for name, old in (before or {}).items():
        if name in operated or name not in (after or {}):
            continue
        new = after[name]
        if isinstance(new, Mapping) and new.get("unreadable"):
            unreadable.append({"reel": name,
                               "reason": str(new["unreadable"])})
            continue
        if not isinstance(old, Mapping) or old.get("unreadable"):
            continue
        for plane in ("timeline", "clip"):
            delta = int(new.get(plane, 0)) - int(old.get(plane, 0))
            if delta < 0:
                decreased.append({"reel": name, "plane": plane,
                                  "before": int(old.get(plane, 0)),
                                  "after": int(new.get(plane, 0))})
            elif delta > 0:
                increased.append({"reel": name, "plane": plane,
                                  "before": int(old.get(plane, 0)),
                                  "after": int(new.get(plane, 0))})
    decreased.sort(key=lambda e: (e["reel"], e["plane"]))
    increased.sort(key=lambda e: (e["reel"], e["plane"]))
    for entry in decreased:
        print(f"  MARKER GATE FLEET LOSS: {entry['reel']!r} {entry['plane']} "
              f"markers {entry['before']} -> {entry['after']} on a reel "
              f"this operation did not touch - nothing in this promotion "
              f"writes to other reels.",
              file=sys.stderr, flush=True)
    for entry in increased:
        print(f"  marker gate fleet note: {entry['reel']!r} "
              f"{entry['plane']} markers {entry['before']} -> "
              f"{entry['after']} (a note added mid-run reads this way)",
              flush=True)
    for entry in unreadable:
        print(f"  marker gate fleet unreadable: {entry['reel']!r} - "
              f"{entry['reason']}", file=sys.stderr, flush=True)
    return {"decreased": decreased, "increased": increased,
            "unreadable": unreadable}


def _main(argv: list) -> int:
    """`--check`: the comparator from a separate process.

    `python3 -m library.tools.marker_gate --check CAPTURE LIVE
    ACCOUNTED --reel NAME`, where CAPTURE is a capture file,
    LIVE holds `{"timeline": [...], "clip": [...]}`, and ACCOUNTED
    holds `{"timeline": [...], "clip": [...]}` in carry-report shape.
    Exit 0 when every captured marker is live or accounted, 2 when
    any is silently lost - naming each with its words.
    """
    import argparse as _argparse

    parser = _argparse.ArgumentParser(
        description="verify a marker capture against a live inventory")
    parser.add_argument("--check", nargs=3, metavar=("CAPTURE", "LIVE",
                                                     "ACCOUNTED"))
    parser.add_argument("--reel", default="")
    args = parser.parse_args(argv)
    if not args.check:
        parser.print_usage(sys.stderr)
        return 2
    capture_path, live_path, accounted_path = args.check
    with open(capture_path, encoding="utf-8") as file:
        capture = json.load(file)
    with open(live_path, encoding="utf-8") as file:
        live = json.load(file)
    with open(accounted_path, encoding="utf-8") as file:
        accounted = json.load(file)
    reel = args.reel or str(capture.get("reel") or "")
    result = verify_reel(reel, capture, live.get("timeline"),
                         live.get("clip"), accounted.get("timeline"),
                         accounted.get("clip"))
    if result["missing"]:
        print(f"MARKER GATE LOST on {reel!r}: "
              f"{len(result['missing'])} captured marker(s) neither "
              f"live nor accounted (before {result['before']}, live "
              f"{result['after']}, reported {result['accounted']}):",
              file=sys.stderr, flush=True)
        for entry in result["missing"]:
            print(f"  LOST: {_describe(entry)}", file=sys.stderr,
                  flush=True)
        return 2
    print(f"marker gate clean on {reel!r}: before {result['before']}, "
          f"live {result['after']}, reported {result['accounted']}",
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
