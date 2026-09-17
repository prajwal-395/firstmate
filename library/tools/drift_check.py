"""Someone finally calls the drift detector (AGENTS.md 10.4).

`library/tools/transform_drift.py` holds the comparison - snapshot Pan/Tilt
against live Pan/Tilt, per placement - and states its own rule: a drift is
REPORTED, never repaired. Until this module nothing called it on any
schedule; it had to be run by hand, by someone who already suspected.
That is the masking condition the 2026-09-16 halving hid behind for
~21 hours (`data/vep-hunt-the-transform-drift-mechanism/report.md` in
firstmate's store): seven live reel timelines holding exactly half their
as-built Pan and Tilt, on every clip, with the archives correct.

What this adds, and nothing beyond it:

1. `check_project` - for every reel, the newest
   `pipeline_output/review/<name>.timeline.json` compared clip by clip
   against a SELF-READ of the live timeline, printing the per-reel
   factor. Run at the START and the END of every reel build, which is
   what turns a 21-hour bracket into a minutes-wide one next time.
2. `manage_project.py drift` - the same comparison on demand.

A SELF-READ IS LOAD-BEARING, not an implementation detail. A Resolve
transform read is scaled by whichever timeline is CURRENT, per axis, by
the current timeline's dimension over the read one
(`docs/READING_A_TRANSFORM.md`). Reading reel A while reel B is current
silently returns scaled numbers, so each timeline is made current for
its own read through `resolve_lock.cursor_excursion`, which puts the
cursor back through the guarded setter - and the guarded setter READS
THE CURSOR BACK (`assert_current_timeline`), so a restore that did not
land raises instead of digesting a different timeline. The sweep adds
its own proof on top: the entry cursor is recorded, and after the last
read the cursor is read again and compared. A mismatch is an error,
never a silent pass.

Two things the measurement handles, both observed 2026-09-17:

- Snapshot rounding. The serializer stores 4 decimals, so a handful of
  axes read 0.49996-0.50004 rather than exactly 0.5 (`2.307` stored for
  `2.3069958...`). That is one factor seen through rounding, not a
  second factor. `per_reel_factor` clusters within `FACTOR_TOLERANCE`
  and reports the median; `transform_drift`'s own strict `uniform_factor`
  (1e-6) is left exactly as measured.
- Zero-valued placements are immune - zero times anything is zero. A
  ratio there is undefined, not 1.0 (`drift_rows` records `None`), and
  the undefined ones never enter the median, so they cannot dilute a
  per-reel verdict either way.

Matching is on track index, record frame AND clip name: a clip replaced
by another at the same position reads as missing, never as a factor.
A placement the live timeline no longer has is a row, never a drop.

A drift is REPORTED, never repaired here - the module `transform_drift`
forbids "repair by dividing by the factor just measured", and so does
this one. There is no write path in this file: no `SetSetting`, no
transform set, no snapshot overwrite. A restore from an independent
record is a separate decision with the captain's name on it.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

from library.tools.transform_drift import (
    AXES,
    TransformDriftError,
    describe,
    drift_rows,
    uniform_factor,
)

#: How far two placements' ratios may differ and still be called one
#: factor. The serializer rounds to 4 decimals, so a true uniform 0.5
#: reads 0.49996-0.50004 on small values (measured 2026-09-17) - three
#: orders looser than `transform_drift.SAME_FACTOR`, and still four
#: orders tighter than the gap to any other factor this family has
#: ever shown (1.0, 2.0, 4.0).
FACTOR_TOLERANCE = 5e-4

#: Where the build's per-reel snapshots live, project-relative.
REVIEW_DIRNAME = Path("pipeline_output") / "review"


def newest_snapshots(review_dir) -> dict:
    """`{timeline name: path}` - the newest snapshot per reel.

    Names come from the snapshot's own `metadata.name` (what the
    timeline was called when the build wrote it), never from the
    filename: `record_reel_promotion` sanitises names into filenames,
    so two reels could in principle sanitise alike, while the recorded
    name is exact. Unreadable or nameless files are SKIPPED and
    reported through `skipped_snapshots`, never silently - an empty
    reading of an unreadable file is the silent pass.
    """
    newest: dict = {}
    skipped: list = []
    try:
        candidates = sorted(Path(review_dir).glob("*.timeline.json"),
                            key=lambda p: p.stat().st_mtime)
    except OSError:
        return {"snapshots": {}, "skipped": []}
    for path in candidates:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            skipped.append(str(path))
            continue
        name = (document.get("metadata") or {}).get("name")
        if not name:
            skipped.append(str(path))
            continue
        newest[str(name)] = path
    return {"snapshots": newest, "skipped": skipped}


def keyed_transforms(document) -> dict:
    """`{(track_index, record_in, name): {"Pan":…, "Tilt":…, "name":…}}`.

    The same placements `transform_drift.built_transforms` reads, keyed
    one level deeper so a clip replaced by another at the same position
    compares as missing rather than as a factor. A document with no
    `tracks` list RAISES, for the same reason the module it mirrors
    does: an empty reading of an unreadable file is the silent pass.
    """
    tracks = document.get("tracks")
    if not isinstance(tracks, list):
        raise TransformDriftError(
            "this is not a serialized timeline: it carries no `tracks` "
            "list, so there is nothing to compare a live reading "
            "against.")
    keyed: dict = {}
    for track in tracks:
        if not isinstance(track, dict) or track.get("type") != "video":
            continue
        try:
            index = int(track["index"])
        except (KeyError, TypeError, ValueError):
            continue
        for clip in track.get("clips") or ():
            transform = clip.get("transform") or {}
            if not all(axis in transform for axis in AXES):
                continue
            try:
                start = int(clip["record_in"])
            except (KeyError, TypeError, ValueError):
                continue
            name = str(clip.get("name") or "")
            row = {axis: float(transform[axis]) for axis in AXES}
            row["name"] = name
            keyed[(index, start, name)] = row
    return keyed


def per_reel_factor(rows):
    """The ONE factor every moved placement shares, or `None`.

    `transform_drift.uniform_factor` strict to representation noise;
    this tolerant to snapshot rounding (see `FACTOR_TOLERANCE`). `None`
    says what it always said: nothing moved, or what moved did not move
    by one factor. `None` factors - the zero-valued placements, where a
    ratio is undefined and not 1.0 - never enter the median.
    """
    seen = [factor
            for row in rows if row.get("moved")
            for factor in row.get("factors", {}).values()
            if factor is not None]
    if not seen:
        return None
    median = statistics.median(seen)
    if any(abs(factor - median) > FACTOR_TOLERANCE for factor in seen):
        return None
    return median


def summarize(name: str, rows) -> str:
    """One line per timeline, carrying the per-reel factor.

    `transform_drift.describe` where it can - the unmoved, missing and
    genuinely non-uniform readings keep their exact words. Where the
    strict 1e-6 clustering refuses what the snapshots resolve (the
    0.49996-0.50004 rounding band), the tolerant median names the
    factor instead of reporting NO single factor for what is one.
    """
    factor = per_reel_factor(rows)
    if factor is not None and uniform_factor(rows) is None:
        moved = [row for row in rows if row.get("moved")]
        missing = [row for row in rows if row.get("missing")]
        head = (f"{name}: {len(moved)} of {len(rows)} placement(s) "
                f"moved since the build, every one by x{factor:g}")
        if missing:
            head += f"; {len(missing)} the timeline no longer has"
        return head
    return describe(name, rows)


def find_live_timeline(project, name):
    """The live timeline EXACTLY called `name`, or `None`.

    Exact listed name, never a prefix (AGENTS.md 5): a prefix match
    here would self-read the wrong reel and report the right one.
    """
    try:
        count = project.GetTimelineCount() or 0
    except Exception:                                   # noqa: BLE001
        return None
    for index in range(1, count + 1):
        try:
            candidate = project.GetTimelineByIndex(index)
        except Exception:                               # noqa: BLE001, S112
            continue
        if candidate is not None and candidate.GetName() == name:
            return candidate
    return None


def read_live_document(project, timeline, resolve) -> dict:
    """Serialize the live timeline with THAT TIMELINE current.

    The cursor excursion moves it there and puts it back through the
    guarded setter, which reads the cursor back after setting - a
    restore that did not land raises `ResolveRaceError` instead of
    letting the next read digest a different timeline.
    """
    from library.tools.resolve_lock import cursor_excursion
    from library.tools.timeline_serializer import serialize_timeline_state
    with cursor_excursion(project, timeline,
                         "transform drift self-read"):
        return serialize_timeline_state(resolve_mock=resolve)


def _cursor_name(project):
    """What the cursor sits on, or `""` where nothing answers."""
    try:
        current = project.GetCurrentTimeline()
        return current.GetName() if current is not None else ""
    except Exception:                                   # noqa: BLE001
        return ""


def compare_documents(name: str, snapshot_doc, live_doc) -> dict:
    """Clip-by-clip comparison of two serializer-shaped documents."""
    rows = drift_rows(keyed_transforms(snapshot_doc),
                      keyed_transforms(live_doc))
    moved = sum(1 for row in rows if row.get("moved"))
    missing = sum(1 for row in rows if row.get("missing"))
    return {"name": name, "compared": True, "rows": rows,
            "factor": per_reel_factor(rows),
            "moved": moved, "missing": missing, "total": len(rows),
            "line": summarize(name, rows)}


def check_project(project_folder: str, *, when: str = "",
                  resolve=None, project=None) -> dict:
    """Compare every snapshotted reel against its live self-read.

    Prints the per-reel factor and returns the report. REPORTS, never
    repairs: nothing in this file writes a transform, a setting or a
    snapshot. Never raises for a reel - an unreadable snapshot or a
    failed live read is a line saying so, since an unreadable reel is
    never a matching one. A missing PROJECT (Resolve closed, or
    showing another project) is the one case that returns `error`
    instead of lines, because every reading after it would lie.

    Takes no lease of its own: both call sites - the reel build's
    start/end sweep and the `drift` CLI wrapper - already hold the
    instance, so the cursor moves refuse outside one exactly as every
    other write does.
    """
    from library.tools.resolve_lock import held

    report: dict = {"when": when, "reels": {}, "lines": [],
                    "skipped_snapshots": [], "drifted": False,
                    "unreadable": False, "error": ""}
    if not held():
        report["error"] = (
            "refusing: the drift check moves the cursor, which is a "
            "write, and there is no instance lease held")
        return report
    try:
        import yaml
        with open(Path(project_folder) / "project.yaml",
                   encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
    except (OSError, ValueError) as exc:
        report["error"] = f"cannot read project.yaml: {exc}"
        return report
    resolve_name = ((config.get("resolve") or {}).get("project_name")
                    or Path(project_folder).name)
    if project is None:
        if resolve is None:
            try:
                import os
                import sys

                from library.tools.resolve_locale import scriptapp_preserving_locale
                sys.path.append(os.path.join(
                    os.environ.get(
                        "RESOLVE_SCRIPT_API",
                        "/Library/Application Support/Blackmagic Design/"
                        "DaVinci Resolve/Developer/Scripting"), "Modules"))
                os.environ.setdefault(
                    "RESOLVE_SCRIPT_LIB",
                    "/Applications/DaVinci Resolve/DaVinci Resolve.app/"
                    "Contents/Libraries/Fusion/fusionscript.so")
                import DaVinciResolveScript as dvr
                resolve = scriptapp_preserving_locale(dvr, "Resolve")
            except Exception as exc:                    # noqa: BLE001
                report["error"] = f"cannot reach Resolve: {exc!r}"
                return report
        try:
            project = resolve.GetProjectManager().GetCurrentProject()
        except Exception as exc:                        # noqa: BLE001
            report["error"] = f"cannot read the current project: {exc!r}"
            return report
    try:
        live_name = project.GetName() if project is not None else None
    except Exception as exc:                            # noqa: BLE001
        report["error"] = f"cannot read the current project: {exc!r}"
        return report
    if live_name != resolve_name:
        report["error"] = (
            f"Resolve has {live_name!r} open, not {resolve_name!r} - "
            f"refusing to read another project's timelines as drift")
        return report

    entry_cursor = _cursor_name(project)
    try:
        found = newest_snapshots(Path(project_folder) / REVIEW_DIRNAME)
    except Exception as exc:                            # noqa: BLE001
        report["error"] = f"cannot list the build snapshots: {exc!r}"
        return report
    report["skipped_snapshots"] = found["skipped"]
    for name in sorted(found["snapshots"]):
        try:
            snapshot_doc = json.loads(
                found["snapshots"][name].read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            report["reels"][name] = {
                "name": name, "compared": False,
                "line": (f"{name}: the build snapshot cannot be read "
                         f"({exc}) - not comparable, and an unreadable "
                         f"reel is never a matching one")}
            report["unreadable"] = True
            continue
        target = find_live_timeline(project, name)
        if target is None:
            report["reels"][name] = {
                "name": name, "compared": False,
                "line": (f"{name}: no live timeline of that name - "
                         f"not comparable")}
            continue
        try:
            live_doc = read_live_document(project, target, resolve)
        except Exception as exc:                        # noqa: BLE001
            report["reels"][name] = {
                "name": name, "compared": False,
                "line": (f"{name}: the live self-read failed "
                         f"({exc!r}) - not comparable")}
            report["unreadable"] = True
            continue
        try:
            compared = compare_documents(name, snapshot_doc, live_doc)
        except TransformDriftError as exc:
            report["reels"][name] = {
                "name": name, "compared": False,
                "line": f"{name}: cannot be compared: {exc}"}
            report["unreadable"] = True
            continue
        report["reels"][name] = compared
        if compared["moved"] or compared["missing"]:
            report["drifted"] = True

    # The proof the cursor went back: read it and compare with the
    # entry reading. A mismatch is an error, never silence - the next
    # comparison after a stranded cursor would digest the wrong reel.
    if entry_cursor:
        landed = _cursor_name(project)
        if landed != entry_cursor:
            report["error"] = (
                f"the cursor did not return: entered on "
                f"{entry_cursor!r}, now on {landed!r}")
            report["unreadable"] = True
        else:
            report["cursor"] = (
                f"entered on {entry_cursor!r}, read back on "
                f"{landed!r}")
    for name in sorted(report["reels"]):
        report["lines"].append(report["reels"][name]["line"])
    if report["skipped_snapshots"]:
        report["lines"].append(
            f"{len(report['skipped_snapshots'])} snapshot file(s) "
            f"could not be read and were skipped, never passed")
    prefix = f"drift {when}: " if when else "drift: "
    for line in report["lines"]:
        print(f"{prefix}{line}", flush=True)
    if report.get("cursor"):
        print(f"{prefix}{report['cursor']}", flush=True)
    return report
