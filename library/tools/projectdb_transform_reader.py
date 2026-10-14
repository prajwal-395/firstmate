"""Read measured Pan and Opacity values from a copied Resolve Project.db.

This is an offline diagnostic reader. It refuses a database with any open
process handle, opens accepted copies with SQLite ``mode=ro&immutable=1``, and
does not contact Resolve. Only the two experimentally identified parameters
are decoded; every other transform parameter is explicitly unmeasured.
"""

from __future__ import annotations

import hashlib
import math
import os
import shutil
import sqlite3
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote


class ProjectDbReadError(RuntimeError):
    """The database cannot safely or correctly be read by this reader."""


@dataclass(frozen=True)
class ParameterValue:
    value: float
    byte_offset: int


@dataclass(frozen=True)
class ClipTransform:
    timeline: str
    track: str
    clip: str
    start: str
    duration: str
    pan: float
    opacity: float
    database_sha256: str
    row: int
    pan_byte_offset: int | None
    opacity_byte_offset: int | None
    unmeasured: tuple[str, ...] = ("Tilt", "Zoom", "Rotation", "Crop")


def _assert_not_open(path: Path) -> None:
    """Refuse paths held open by any process; this catches a live Resolve DB."""
    lsof = shutil.which("lsof")
    if not lsof:
        raise ProjectDbReadError("cannot prove the database is not live: lsof is unavailable")
    try:
        result = subprocess.run(
            [lsof, "-t", "--", str(path)], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProjectDbReadError(f"cannot check whether the database is live: {exc}") from exc
    if result.returncode not in (0, 1):
        raise ProjectDbReadError(
            f"cannot prove the database is not live (lsof exit {result.returncode})"
        )
    holders = result.stdout.split()
    if holders:
        raise ProjectDbReadError(
            f"database is open by process(es) {', '.join(holders)}; use a closed copy"
        )


def _decompress_envelope(value: bytes | str) -> bytes:
    """Decode Resolve's 9-byte envelope and its zstd frame."""
    if isinstance(value, str):
        try:
            raw = bytes.fromhex(value)
        except ValueError as exc:
            raise ProjectDbReadError("EffectFiltersBA is not hexadecimal envelope data") from exc
    else:
        raw = bytes(value)
        # Resolve stores this field as text on measured databases, but accept
        # the equivalent binary representation for schema-compatible copies.
        if raw[:4] != b"\x00\x00\x00\x02" and len(raw) % 2 == 0:
            try:
                decoded = bytes.fromhex(raw.decode("ascii"))
            except (UnicodeDecodeError, ValueError):
                decoded = raw
            if decoded[:4] == b"\x00\x00\x00\x02":
                raw = decoded
    if len(raw) < 9 or raw[:4] != b"\x00\x00\x00\x02":
        raise ProjectDbReadError("EffectFiltersBA has an unknown envelope")
    frame = raw[9:]
    if not frame.startswith(b"\x28\xb5\x2f\xfd"):
        raise ProjectDbReadError("EffectFiltersBA has no zstd frame at byte offset 9")
    executable = shutil.which("zstd")
    if not executable:
        raise ProjectDbReadError("zstd executable is required to decode EffectFiltersBA")
    try:
        result = subprocess.run(
            [executable, "-d", "-q", "-c"], input=frame,
            capture_output=True, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProjectDbReadError(f"zstd decompression failed: {exc}") from exc
    if result.returncode:
        raise ProjectDbReadError("zstd could not decompress EffectFiltersBA")
    return result.stdout


def _varint(data: bytes, offset: int) -> tuple[int, int]:
    result = shift = 0
    while offset < len(data) and shift < 70:
        byte = data[offset]
        offset += 1
        result |= (byte & 0x7f) << shift
        if byte < 0x80:
            return result, offset
        shift += 7
    raise ProjectDbReadError("malformed protobuf varint in EffectFiltersBA")


def _parameter_values(data: bytes) -> dict[int, ParameterValue]:
    """Read outer field-1 parameter groups and the measured fixed64 value."""
    values: dict[int, ParameterValue] = {}
    offset = 0
    while offset < len(data):
        tag, after_tag = _varint(data, offset)
        field, wire = tag >> 3, tag & 7
        if wire != 2:
            raise ProjectDbReadError("unexpected EffectFiltersBA protobuf structure")
        length, start = _varint(data, after_tag)
        end = start + length
        if end > len(data):
            raise ProjectDbReadError("truncated parameter group in EffectFiltersBA")
        group = data[start:end]
        if field == 1:
            inner_tag, inner_offset = _varint(group, 0)
            if inner_tag == 8:
                parameter_id, inner_offset = _varint(group, inner_offset)
                # Measured set-value shape: f9/L15 -> f1/v1 -> f3/L11 ->
                # f1/L9 -> f1/fixed64. The final 8 bytes are the f64.
                needle = b"\x4a\x0f\x08\x01\x1a\x0b\x0a\x09\x09"
                match = group.find(needle, inner_offset)
                if match >= 0 and match + len(needle) + 8 <= len(group):
                    value_offset = start + match + len(needle)
                    value = struct.unpack_from("<d", data, value_offset)[0]
                    if not math.isfinite(value):
                        raise ProjectDbReadError("non-finite transform value in EffectFiltersBA")
                    values[parameter_id] = ParameterValue(value, value_offset)
        offset = end
    return values


def read_transforms(
    database_copy: str | os.PathLike[str], *, timeline_width: int,
) -> list[ClipTransform]:
    """Return Pan (pixels) and Opacity (%) for clip rows in a closed DB copy.

    ``timeline_width`` is explicit because Pan is stored as a fraction of
    project width. Missing values resolve to measured defaults (Pan 0,
    Opacity 100); no project resolution is inferred from the blob.
    ``row`` is SQLite's Sm2TiItem rowid; byte offsets refer to the decompressed
    EffectFiltersBA payload and are ``None`` when the parameter is defaulted.
    """
    path = Path(database_copy).expanduser().resolve(strict=True)
    if not path.is_file() or timeline_width <= 0:
        raise ProjectDbReadError("a Project.db file and positive timeline width are required")
    for suffix in ("-wal", "-shm", "-journal"):
        if Path(str(path) + suffix).exists():
            raise ProjectDbReadError(f"database sidecar {suffix} exists; use a clean closed copy")
    _assert_not_open(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    uri = f"file:{quote(str(path), safe='/')}?mode=ro&immutable=1"
    try:
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT item.rowid AS item_row, timeline.Name AS timeline, "
            "track.UserDefinedName AS track, item.Name AS clip, item.Start AS start, "
            "item.Duration AS duration, item.EffectFiltersBA AS effects "
            "FROM Sm2Timeline timeline "
            "JOIN Sm2Sequence sequence ON sequence.Sm2Sequence_id=timeline.Sequence "
            "JOIN Sm2SequenceContainer container "
            "ON container.Sm2Sequence_id=sequence.Sm2Sequence_id "
            "JOIN Sm2SequenceContainer_Sm2TiTrack container_track "
            "ON container_track.DbOwner=container.Sm2SequenceContainer_id "
            "JOIN Sm2TiTrack track ON track.Sm2TiTrack_id=container_track.DbAssociate "
            "JOIN Sm2TiItem_Sm2TiTrack link ON link.DbOwner=track.Sm2TiTrack_id "
            "JOIN Sm2TiItem item ON item.Sm2TiItem_id=link.DbAssociate "
            "WHERE track.Type=0 ORDER BY timeline.Name, track.rowid, item.rowid"
        ).fetchall()
    except sqlite3.Error as exc:
        raise ProjectDbReadError(f"cannot read the expected Resolve timeline join: {exc}") from exc
    finally:
        if "connection" in locals():
            connection.close()

    result: list[ClipTransform] = []
    for row in rows:
        decoded = _parameter_values(_decompress_envelope(row["effects"])) if row["effects"] else {}
        pan = decoded.get(4)
        opacity = decoded.get(2)
        result.append(ClipTransform(
            timeline=str(row["timeline"]), track=str(row["track"] or ""),
            clip=str(row["clip"]), start=str(row["start"]), duration=str(row["duration"]),
            pan=(pan.value * timeline_width if pan else 0.0),
            opacity=(opacity.value if opacity else 100.0),
            database_sha256=digest, row=int(row["item_row"]),
            pan_byte_offset=pan.byte_offset if pan else None,
            opacity_byte_offset=opacity.byte_offset if opacity else None,
        ))
    return result
