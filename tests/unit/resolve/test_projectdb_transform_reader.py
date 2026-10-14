"""Synthetic Project.db fixture for the measured transform reader."""

from __future__ import annotations

import shutil
import sqlite3
import struct
import subprocess

import pytest

from library.tools.projectdb_transform_reader import (
    ProjectDbReadError,
    read_transforms,
)


def _varint(value: int) -> bytes:
    result = bytearray()
    while value >= 0x80:
        result.append((value & 0x7f) | 0x80)
        value >>= 7
    result.append(value)
    return bytes(result)


def _value_group(parameter: int, value: float) -> bytes:
    payload = b"\x4a\x0f\x08\x01\x1a\x0b\x0a\x09\x09" + struct.pack("<d", value)
    group = b"\x08" + _varint(parameter) + b"\x4a" + _varint(len(payload)) + payload
    return b"\x0a" + _varint(len(group)) + group


def _envelope(payload: bytes) -> bytes:
    executable = shutil.which("zstd")
    if not executable:
        pytest.skip("zstd is required for the synthetic Resolve envelope")
    compressed = subprocess.run(
        [executable, "-q", "-c"], input=payload, capture_output=True, check=True,
    ).stdout
    return b"\x00\x00\x00\x02" + len(compressed).to_bytes(4, "big") + b"\x80" + compressed


def _database(path):
    db = sqlite3.connect(path)
    db.executescript("""
      CREATE TABLE Sm2Timeline (Sm2Timeline_id TEXT, Name TEXT, Sequence TEXT);
      CREATE TABLE Sm2Sequence (Sm2Sequence_id TEXT, Sm2SequenceContainer TEXT);
      CREATE TABLE Sm2SequenceContainer (Sm2SequenceContainer_id TEXT, Sm2Sequence_id TEXT);
      CREATE TABLE Sm2SequenceContainer_Sm2TiTrack (DbOwner TEXT, DbAssociate TEXT);
      CREATE TABLE Sm2TiTrack (Sm2TiTrack_id TEXT, Type INTEGER, UserDefinedName TEXT);
      CREATE TABLE Sm2TiItem_Sm2TiTrack (DbOwner TEXT, DbAssociate TEXT);
      CREATE TABLE Sm2TiItem (Sm2TiItem_id TEXT, Name TEXT, Start TEXT, Duration TEXT,
                              EffectFiltersBA BLOB);
      INSERT INTO Sm2Timeline VALUES ('tl', 'Qualification', 'seq');
      INSERT INTO Sm2Sequence VALUES ('seq', 'container');
      INSERT INTO Sm2SequenceContainer VALUES ('container', 'seq');
      INSERT INTO Sm2TiTrack VALUES ('video', 0, 'V1');
      INSERT INTO Sm2TiTrack VALUES ('audio', 1, 'A1');
      INSERT INTO Sm2SequenceContainer_Sm2TiTrack VALUES ('container', 'video');
      INSERT INTO Sm2SequenceContainer_Sm2TiTrack VALUES ('container', 'audio');
      INSERT INTO Sm2TiItem_Sm2TiTrack VALUES ('video', 'opacity');
      INSERT INTO Sm2TiItem_Sm2TiTrack VALUES ('video', 'pan');
      INSERT INTO Sm2TiItem_Sm2TiTrack VALUES ('audio', 'audio-item');
    """)
    db.execute("INSERT INTO Sm2TiItem VALUES ('opacity','Opacity clip','0','5',?)",
               (_envelope(_value_group(2, 50.0)),))
    db.execute("INSERT INTO Sm2TiItem VALUES ('pan','Pan clip','5','5',?)",
               (_envelope(_value_group(4, 200 / 1920)),))
    db.execute("INSERT INTO Sm2TiItem VALUES ('audio-item','Audio','0','10',NULL)")
    db.commit()
    db.close()


def test_reads_measured_values_with_copy_row_and_payload_offsets(tmp_path):
    path = tmp_path / "Project.db"
    _database(path)

    clips = read_transforms(path, timeline_width=1920)

    assert [clip.clip for clip in clips] == ["Opacity clip", "Pan clip"]
    assert clips[0].opacity == 50.0
    assert clips[0].pan == 0.0
    assert clips[1].pan == 200.0
    assert clips[1].opacity == 100.0
    assert clips[0].database_sha256 == clips[1].database_sha256
    assert clips[0].row != clips[1].row
    assert clips[0].opacity_byte_offset is not None
    assert clips[1].pan_byte_offset is not None
    assert clips[0].unmeasured == ("Tilt", "Zoom", "Rotation", "Crop")


def test_refuses_database_open_by_another_process_handle(tmp_path):
    path = tmp_path / "Project.db"
    _database(path)
    held = sqlite3.connect(path)
    try:
        with pytest.raises(ProjectDbReadError, match="open by process"):
            read_transforms(path, timeline_width=1920)
    finally:
        held.close()


def test_refuses_database_with_sqlite_sidecar(tmp_path):
    path = tmp_path / "Project.db"
    _database(path)
    (tmp_path / "Project.db-wal").touch()

    with pytest.raises(ProjectDbReadError, match="sidecar"):
        read_transforms(path, timeline_width=1920)
