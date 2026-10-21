"""The delivery identity record: what a delivered file IS, and who made it.

One writer, one shape, beside the file it describes.

Why it exists
-------------
A delivered file used to be a file and nothing else. The engine could
render one, master one and check one, but nothing recorded WHICH edit
produced it - so `export_verification`'s identity criterion (QA-008)
answered "unknown" for every delivery that did not come from a reel,
and a hand-rolled export (the section-12 demo wrote
`/tmp/bench/render_export.sh` by hand) shipped a file 9.18 dB under
target with nothing on record that said so.

The record is the answer, and it is the same answer everywhere:

* the `render` half says what was rendered and where it came from;
* the `mastering` half (inside `render`) says the loudness was brought
  to the delivery target - so a file that was never mastered says so
  in the record itself;
* the `verification` half says what the engine measured on the file it
  handed over;
* the `timeline_*` half says the frame rate, the range and the length,
  so a note anchored to a delivered-file time maps back to the
  timeline;
* the `sha256` and `size_bytes` say WHICH file this record is about.

The stages
----------
A rendered file is a PRE-MASTER until the mastering pass has run on it.
The record says which it is (`stage`, `mastered`), so no route can
hand over an unmastered file while calling it verified. A record whose
`delivered` is false is not a delivery: it is the account of a render.

Who writes it
-------------
`deliver_and_verify` (the delivery path) writes the record of a
delivered file. Step 6.01 writes the record of a full pipeline run's
export. The AXI render route writes the record of the pre-master file
it rendered, so even an inspection render says what it is.

Readers: `export_verification.load_delivery` (the identity criterion),
`delivered_note_anchor` (a note's time in a delivered file),
`render_watch` (what the last delivery produced),
`post_delivery_qa` (the delivered file's own spec).
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from typing import Any

SIDECAR_SUFFIX = ".deliver.json"
"""The record's name beside the file: `<the file's stem>.deliver.json`."""

SCHEMA = "ren.delivery-identity/1"
"""The record's schema tag. A reader that does not know this tag says
so rather than guessing at the fields."""

STAGE_PRE_MASTER = "pre_master"
"""A rendered file that has NOT been brought to the delivery loudness."""

STAGE_MASTERED = "mastered"
"""A rendered file the mastering pass has brought to the delivery target."""


def sidecar_path(output_path: str) -> str:
    """The record's path beside `output_path`."""
    return os.path.splitext(str(output_path))[0] + SIDECAR_SUFFIX


def sha256_file(path: str) -> str:
    """The file's own digest, read in blocks so a long file is not
    held in memory."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_record(output_path: str, *, timeline_name: str = "",
                 reel: int | None = None, fps: float | None = None,
                 seconds: float | None = None,
                 start_frame: int | None = None,
                 end_frame: int | None = None,
                 settings: dict | None = None,
                 render: dict | None = None,
                 verification: dict | None = None,
                 delivered: bool = False,
                 stage: str = STAGE_MASTERED,
                 source: str = "",
                 project_name: str = "",
                 extra: dict | None = None) -> dict:
    """Write the identity record beside the file and return it.

    `stage` says what the file IS: `pre_master` for a render that has
    not been mastered, `mastered` for one that has. `delivered` says
    whether this record accounts for a handover. The two are separate
    on purpose: a mastered file that failed verification is not
    delivered, and a record that claimed otherwise would be a lie the
    acceptance harness would grade as a pass.

    The file's digest and size are read from the file itself, so the
    record cannot name a file that is not the one beside it.
    """
    path = sidecar_path(output_path)
    size_bytes = None
    digest = ""
    try:
        size_bytes = os.path.getsize(output_path)
        digest = sha256_file(output_path)
    except OSError:
        # A record for a file that is not there is still a record of
        # what was attempted; the digest stays empty and says so.
        digest = ""
    record: dict[str, Any] = {
        "schema": SCHEMA,
        "stage": stage,
        "mastered": stage == STAGE_MASTERED,
        "delivered": bool(delivered),
        "source": source,
        "timeline_name": timeline_name,
        "reel": reel,
        "timeline_fps": fps,
        "timeline_start_frame": start_frame,
        "timeline_end_frame": end_frame,
        "timeline_seconds": seconds,
        "settings": settings or {},
        "render": render or {},
        "verification": verification or {},
        "project": project_name,
        "output_path": str(output_path),
        "sha256": digest,
        "size_bytes": size_bytes,
        "created_at": datetime.now(UTC).isoformat(),
    }
    if extra:
        record.update(extra)
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2, default=str)
    os.replace(tmp, path)
    record["record_path"] = path
    return record


def read_record(output_path: str) -> dict | None:
    """The record beside `output_path`, or None when there is none.

    An unreadable or malformed record reads as None: a record nobody
    can read is not a record, and a caller that treats it as one would
    grade a delivery on evidence that does not exist.
    """
    path = sidecar_path(output_path)
    try:
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError):
        return None
    return document if isinstance(document, dict) else None


def is_deliverable(output_path: str) -> bool:
    """Whether this file is a DELIVERED file, by its own record.

    A file with no record is not a delivery, and neither is a
    pre-master render: both are refusals, not silent passes.
    """
    record = read_record(output_path)
    if not record:
        return False
    return bool(record.get("mastered")) and bool(record.get("delivered"))


def declarations(output_path: str) -> dict:
    """What the record DECLARES about the delivered file.

    The frame rate, the frame count, the length, the frame and the
    loudness target: the things a delivery is judged against and that
    a run used to type by hand. Empty when there is no record - an
    absent record declares nothing, and the caller says so rather than
    guessing.
    """
    record = read_record(output_path)
    if not record:
        return {}
    out: dict[str, Any] = {}
    fps = record.get("timeline_fps")
    if fps:
        out["fps"] = float(fps)
    start = record.get("timeline_start_frame")
    end = record.get("timeline_end_frame")
    if start is not None and end is not None:
        try:
            out["frames"] = int(end) - int(start) + 1
        except (TypeError, ValueError):
            pass
    seconds = record.get("timeline_seconds")
    if seconds:
        out["seconds"] = float(seconds)
    settings = record.get("settings") or {}
    width, height = settings.get("width"), settings.get("height")
    if width and height:
        out["resolution"] = (int(width), int(height))
    mastering = (record.get("render") or {}).get("mastering") or {}
    target = (mastering.get("target") or {}).get("lufs")
    if target is not None:
        out["target_lufs"] = float(target)
    return out


def stage_of(output_path: str) -> str:
    """The stage the file's own record names, or `pre_master` when it
    names none - an unrecorded file was never mastered."""
    record = read_record(output_path)
    if not record:
        return STAGE_PRE_MASTER
    stage = record.get("stage")
    return stage if stage in (STAGE_PRE_MASTER, STAGE_MASTERED) else (
        STAGE_PRE_MASTER)


__all__ = [
    "SCHEMA",
    "SIDECAR_SUFFIX",
    "STAGE_MASTERED",
    "STAGE_PRE_MASTER",
    "declarations",
    "is_deliverable",
    "read_record",
    "sha256_file",
    "sidecar_path",
    "stage_of",
    "write_record",
]
