"""WHAT CREATING A VERSION COSTS, PER EDIT TYPE (VC-009).

The captain's punchlist, 2026-10-20, VC-009: *"Measure branching and
promotion overhead. Determine which state must be copied, archived, or
reconciled for each edit type. Avoid repeated full reconstruction of
unchanged generations. Compare incremental version creation against
full version materialization. Require equivalent restoration and
preservation before optimizing away a safety step."*

Three buckets, one per word
---------------------------
Every path that creates a version pays for the same three things, and
the punchlist names them: the state that is COPIED (the new
generation's materialization - the staging duplicate a build or a
touch edits), the state that is ARCHIVED (the recovery archive of the
generation being replaced), and the state that is RECONCILED (the
rows snapshot, the digest and the version/revision ledger write).

So a measurement here is three numbers per edit type - seconds and
bytes per bucket - plus the denominators they are read against: how
many items the generation holds, how much media it plays, how big the
archive is, how many versions the reel already had. A number without
its denominator is a story, and this module refuses to record one.

The edit types are the paths that create a version:

- `promotion` a rebuild promoted over a generation;
- `touch`    a touch-up promoted over a generation;
- `adopt`    the captain's hand edit, adopted as a version (there is
             no copy: the captain edited the live timeline himself);
- `variant`  a variant build promoted over a generation.

Where the measurement runs
--------------------------
The Resolve half runs INSIDE the broker's turn - the script that
drives it is submitted as a `resolve_axi` job, so the measurement
never opens a second scripting client and never asks for a quiet
window. The module itself is a library with one CLI verb (`report`);
the measurement is a function the broker's script calls.

`library/tools/versions/replaced_generation.py` (the archive half),
`library/tools/versions/reel_versions.py` (the reconcile half).
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

from library.tools.versions import store

LEDGER_FILENAME = "version_overhead.json"
LEDGER_FORMAT = "version_overhead/1"

#: The paths that create a version, and what each one pays for.
EDIT_TYPES = ("promotion", "touch", "adopt", "variant")

#: The three costs a version creation pays, in the punchlist's words.
BUCKETS = ("copy", "archive", "reconcile")

#: The edit types that materialize a new generation (a staging
#: duplicate) before editing it, and the ones that do not.
COPIES = ("promotion", "touch", "variant")


class OverheadRefused(RuntimeError):
    """The measurement cannot be taken as asked."""


def ledger_path(project_folder: str) -> Path:
    """Where the ledger of measurements lives in a project."""
    return Path(str(project_folder)) / "pipeline_output" / "review" \
        / LEDGER_FILENAME


def read_rows(project_folder: str) -> list:
    """Every measurement row recorded for a project, oldest first."""
    path = ledger_path(project_folder)
    if not path.is_file():
        return []
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = document.get("rows") if isinstance(document, Mapping) else None
    return list(rows or ())


def record_row(project_folder: str, row: Mapping) -> str:
    """Append one measurement row to the project's ledger."""
    document = {"format": LEDGER_FORMAT, "rows": read_rows(project_folder)}
    document["rows"].append(dict(row))
    return store.write_record(str(ledger_path(project_folder)), document,
                              prefix=".version-overhead-")


def _seconds(started: float) -> float:
    return round(time.perf_counter() - started, 3)


def _media_of(tracks: Sequence[Mapping]) -> list[str]:
    media = set()
    for track in tracks or ():
        for clip in track.get("clips") or ():
            source = str(clip.get("source_file") or "")
            if source:
                media.add(source)
    return sorted(media)


def measure(project_folder: str, edit_type: str, *,
            resolve, project, timeline, final: str = "",
            staging: str = "", record: bool = True) -> dict:
    """Measure what creating a version of this generation costs.

    The three buckets are measured with the REAL functions the paths
    use, on this copy of the project:

    - `archive`  `replaced_generation.ensure_archived` - the recovery
      archive of the generation being replaced, and whether it was
      exported or REUSED (an unchanged generation is not exported
      again);
    - `copy`     `composed_edit.prepare_candidate` - the staging
      duplicate the new generation is materialized as;
    - `reconcile` `reel_versions.record` - the rows snapshot, the
      digest and the ledger write.

    The duplicate the copy measurement makes is deleted again before
    the measurement returns: the measurement leaves the project as it
    found it, apart from the version it recorded.

    Returns the row. With `record` the row is appended to the
    project's ledger.
    """
    if edit_type not in EDIT_TYPES:
        raise OverheadRefused(
            f"{edit_type!r} is not an edit type that creates a version "
            f"({', '.join(EDIT_TYPES)})")
    from library.tools import reel_read
    from library.tools.versions import (
        reel_versions,
        replaced_generation,
    )

    name = str(final or timeline.GetName() or "")
    tracks = reel_read.read_tracks(timeline)
    rows = reel_read.rows_of({"tracks": tracks})
    media = _media_of(tracks)
    rows_digest = replaced_generation.live_rows_digest(timeline, name)
    history = reel_versions.versions_of(project_folder, name)
    entry = history[-1] if history else None

    row: dict = {
        "format": LEDGER_FORMAT,
        "edit_type": edit_type,
        "timeline": name,
        "timeline_id": str(timeline.GetUniqueId() or ""),
        "rows_digest": rows_digest,
        "buckets": {},
        "denominators": {
            "rows": len(rows),
            "items": sum(len(entry.get("items") or ())
                         for entry in (rows or {}).values()),
            "media_files": len(media),
            "media_bytes": sum(_size(path) for path in media),
            "versions_before": len(history),
        },
    }

    # ── archive ────────────────────────────────────────────────────
    archive_started = time.perf_counter()
    outcome = replaced_generation.ensure_archived(
        resolve=resolve, project=project, timeline=timeline,
        project_folder=project_folder, entry=entry,
        label=f"overhead measurement, {edit_type} on {name}",
        rows_digest=rows_digest)
    archive_seconds = _seconds(archive_started)
    receipt = outcome.get("receipt") or {}
    archive_path = str(receipt.get("archive_path")
                       or receipt.get("path") or "")
    row["buckets"]["archive"] = {
        "seconds": archive_seconds,
        "bytes": _size(archive_path) if archive_path else 0,
        "status": outcome.get("status"),
        "exported": outcome.get("status") == "exported",
        "reused": outcome.get("status") == "reused",
        "reason": outcome.get("reason", ""),
    }

    # ── copy ───────────────────────────────────────────────────────
    if edit_type in COPIES:
        from library.tools.composed_edit import prepare_candidate

        staging_name = str(staging or f"{name} (overhead copy)")
        copy_started = time.perf_counter()
        try:
            duplicate = prepare_candidate(timeline, staging_name)
        except Exception as exc:  # noqa: BLE001 - the measurement reports it
            row["buckets"]["copy"] = {
                "seconds": _seconds(copy_started), "bytes": 0,
                "status": "refused", "reason": repr(exc)}
            duplicate = None
        else:
            row["buckets"]["copy"] = {
                "seconds": _seconds(copy_started), "bytes": 0,
                "status": "materialized",
                "reason": "the duplicate is Resolve-side state; it "
                          "carries no file bytes"}
            _delete_duplicate(project, duplicate)
    else:
        row["buckets"]["copy"] = {
            "seconds": 0.0, "bytes": 0, "status": "not_applicable",
            "reason": f"an {edit_type} edits the live timeline in place, "
                      "so no generation is materialized"}

    # ── reconcile ──────────────────────────────────────────────────
    ledger_before = _size(str(reel_versions.path_for(project_folder)))
    reconcile_started = time.perf_counter()
    recorded = reel_versions.record(
        project_folder, name, kind=reel_versions.KIND_BUILD, rows=rows,
        measured_edit_type=edit_type)
    reconcile_seconds = _seconds(reconcile_started)
    ledger_after = _size(str(reel_versions.path_for(project_folder)))
    row["buckets"]["reconcile"] = {
        "seconds": reconcile_seconds,
        "bytes": max(0, ledger_after - ledger_before),
        "status": "recorded",
        "version": int(recorded["version"]),
    }
    row["denominators"]["versions_after"] = int(recorded["version"])
    row["seconds_total"] = round(sum(
        float(bucket.get("seconds") or 0.0)
        for bucket in row["buckets"].values()), 3)
    row["bytes_total"] = sum(
        int(bucket.get("bytes") or 0) for bucket in row["buckets"].values())
    if record:
        record_row(project_folder, row)
    return row


def _size(path: str) -> int:
    try:
        return Path(path).stat().st_size
    except OSError:
        return 0


def _delete_duplicate(project, duplicate) -> None:
    """Delete the duplicate the copy measurement made."""
    if duplicate is None:
        return
    try:
        pool = project.GetMediaPool()
        pool.DeleteTimelines([duplicate])
    except Exception:  # noqa: BLE001, S110 - a measurement leaves no debris
        pass


def report(project_folder: str) -> str:
    """The ledger, as lines a reader can read."""
    rows = read_rows(project_folder)
    if not rows:
        return ("no version-overhead measurement is recorded for "
                f"{project_folder}")
    lines = [(f"version overhead - {project_folder} "
              f"({len(rows)} measurement(s))")]
    for row in rows:
        denominators = row.get("denominators") or {}
        lines.append(
            f"- {row.get('edit_type')} on {row.get('timeline')!r}: "
            f"{row.get('seconds_total')}s, {row.get('bytes_total')} bytes "
            f"over {denominators.get('items')} item(s), "
            f"{denominators.get('media_files')} media file(s), "
            f"{denominators.get('versions_before')} version(s) before")
        for name, bucket in (row.get("buckets") or {}).items():
            lines.append(
                f"    {name}: {bucket.get('seconds')}s, "
                f"{bucket.get('bytes')} bytes "
                f"({bucket.get('status')})")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """`report --project <folder>`: read the ledger back."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.versions.overhead",
        description="Read the measured version-creation overhead back.")
    parser.add_argument("verb", choices=("report",))
    parser.add_argument("--project", required=True)
    args = parser.parse_args(argv)
    print(report(args.project))
    return 0


__all__ = [
    "BUCKETS",
    "COPIES",
    "EDIT_TYPES",
    "OverheadRefused",
    "ledger_path",
    "measure",
    "read_rows",
    "record_row",
    "report",
]


if __name__ == "__main__":  # pragma: no cover - the CLI
    raise SystemExit(main())
