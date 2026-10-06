"""Pin the ingest contract for variable frame rate and timecode.

The defects these close, each measured on a fixture:

* A variable-frame-rate file whose `r_frame_rate` is `0/0` (what phone
  and screen-recording footage reports) was cataloged with
  `frame_rate=None` and SKIPPED by `catalog_footage`'s required-field
  verification - the footage was lost, silently, one file at a time.
* A variable-file whose rates diverge (measured fixture: r=30/1,
  avg=1395/59 ~ 23.6, 93 frames over 3.93 s) was cataloged with no
  record that it was variable and no average for the consumers that
  need true timing.
* A container carrying a timecode tag synced by nothing: the catalog
  recorded no timecode, so timecode-referenced multicam footage had
  no timecode path at all.
* Two files the catalog called identical except for pixel format: a
  bt709 and a smpte170m file cataloged indistinguishably - only pix_fmt
  was read - although their pixels decode off different bases.

Every fixture is built under ``tmp_path``; no test reaches a real
project.  ffmpeg fixtures skip when ffmpeg is missing.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from library.steps.step_1_02_catalog_footage import step as catalog_step


def _ffmpeg() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _build(tmp_path, name, *ffmpeg_args):
    """A real media file, or skip: a fixture that cannot be measured
    proves nothing."""
    if not _ffmpeg():
        import pytest
        pytest.skip("ffmpeg/ffprobe not on PATH")
    out = tmp_path / name
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", *ffmpeg_args, str(out)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, check=False)
    if result.returncode != 0 or not out.exists():
        import pytest
        pytest.skip("ffmpeg cannot encode this fixture here")
    return str(out)


def _cfr_mp4(tmp_path):
    return _build(
        tmp_path, "cfr.mp4",
        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p")


def _vfr_divergent_mp4(tmp_path):
    """A real VFR file whose nominal and average rates differ: frames
    dropped without re-timing, so the container's base rate stays 30
    while the true average is ~23.6."""
    return _build(
        tmp_path, "vfr.mp4",
        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=4",
        "-vf", "mpdecimate=hi=64*8:lo=64*4:frac=0.05",
        "-fps_mode", "vfr", "-c:v", "libx264", "-pix_fmt", "yuv420p")


def _timecode_mov(tmp_path):
    return _build(
        tmp_path, "tc.mov",
        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-map", "0:v", "-map", "1:a", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-write_tmcd", "1", "-timecode", "01:02:03:04")


# ── Variable frame rate ──────────────────────────────────────────

def test_a_vfr_file_carries_the_rate_resolve_uses_and_the_average_beside_it(
        tmp_path):
    """Resolve places media at the container's nominal rate
    (`GetClipProperty("FPS")`), so the catalog keeps `frame_rate` at
    that nominal rate and records the measured average beside it for
    the consumers that need true timing.  On origin/main the average
    was unread and the file was not even recorded as variable."""
    path = _vfr_divergent_mp4(tmp_path)
    md = catalog_step.extract_metadata(path)
    assert md["frame_rate"] == 30.0
    assert md["frame_rate_nominal"] == 30.0
    assert md["avg_frame_rate"] == 23.644
    assert md["is_vfr"] is True


def test_a_constant_file_is_not_flagged_as_variable(tmp_path):
    path = _cfr_mp4(tmp_path)
    md = catalog_step.extract_metadata(path)
    assert md["is_vfr"] is False
    assert md["frame_rate"] == 30.0
    assert md["frame_rate_nominal"] == 30.0
    assert md["avg_frame_rate"] == 30.0


def test_a_zero_r_frame_rate_file_is_cataloged_not_skipped(
        tmp_path, monkeypatch):
    """`r_frame_rate` of `0/0` is what phone VFR footage reports.
    On origin/main the catalog read None and `catalog_footage`
    refused the file as missing a required field - the footage was
    lost.  The average rate is the only rate such a file has."""
    probe = {
        "streams": [{
            "index": 0, "codec_type": "video", "codec_name": "h264",
            "width": 1920, "height": 1080,
            "r_frame_rate": "0/0", "avg_frame_rate": "59/1",
            "pix_fmt": "yuv420p",
        }],
        "format": {"duration": "10.0"},
    }
    real_run = subprocess.run

    def fake_run(cmd, *args, **kwargs):
        if cmd[0] == "ffprobe":
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps(probe), stderr="")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(catalog_step.subprocess, "run", fake_run)
    md = catalog_step.extract_metadata("/fake/phone_vfr.mp4")
    assert md["frame_rate"] == 59.0
    assert md["frame_rate_nominal"] is None
    assert md["is_vfr"] is True


def test_catalog_footage_keeps_a_zero_rate_vfr_file(tmp_path, monkeypatch,
                                                    capsys):
    """End to end: the file reaches the catalog.  On origin/main the
    same probe makes `catalog_footage` raise the required-field
    refusal, so the footage never reaches a timeline."""
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "phone_vfr.mp4").write_bytes(b"fixture")
    probe = {
        "streams": [{
            "index": 0, "codec_type": "video", "codec_name": "h264",
            "width": 1920, "height": 1080,
            "r_frame_rate": "0/0", "avg_frame_rate": "59/1",
            "pix_fmt": "yuv420p",
        }],
        "format": {"duration": "10.0"},
    }
    real_run = subprocess.run

    def fake_run(cmd, *args, **kwargs):
        if cmd[0] == "ffprobe":
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps(probe), stderr="")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(catalog_step.subprocess, "run", fake_run)
    result = catalog_step.catalog_footage([{
        "path": str(raw / "phone_vfr.mp4"),
        "filename": "phone_vfr.mp4",
        "extension": ".mp4",
        "size_bytes": 7,
        "clip_id": "clip_001",
    }])
    assert result["total_clips"] == 1
    entry = result["clip_catalog"][0]
    assert entry["frame_rate"] == 59.0
    assert entry["is_vfr"] is True
    assert result["skipped_files"] == []
    assert "Variable frame rate" in capsys.readouterr().err


# ── Timecode ─────────────────────────────────────────────────────

def test_a_carried_timecode_is_recorded(tmp_path):
    """The sync reference a professional camera writes.  On origin/main
    the catalog recorded no timecode at all, so timecode-referenced
    footage had no timecode path into the pipeline."""
    path = _timecode_mov(tmp_path)
    md = catalog_step.extract_metadata(path)
    assert md["timecode_start"] == "01:02:03:04"


def test_a_file_without_timecode_records_none(tmp_path):
    path = _cfr_mp4(tmp_path)
    md = catalog_step.extract_metadata(path)
    assert md["timecode_start"] is None


# ── Source color tags ──────────────────────────────────────────

def _color_tagged_mp4(tmp_path, name, primaries, transfer, matrix):
    """A real file carrying VUI color tags, written by the
    h264_metadata bitstream filter (output `-colorspace` flags alone
    leave transfer/primaries as `unknown`)."""
    return _build(
        tmp_path, name,
        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-bsf:v",
        f"h264_metadata=colour_primaries={primaries}"
        f":transfer_characteristics={transfer}"
        f":matrix_coefficients={matrix}")


def test_bt709_and_smpte170m_files_catalog_distinctly(tmp_path):
    """The grade's numbers are decoded off these bases.  On origin/main
    the catalog read only pix_fmt, so these two files cataloged
    identically."""
    bt709 = _color_tagged_mp4(tmp_path, "bt709.mp4", 1, 1, 1)
    smpte = _color_tagged_mp4(tmp_path, "smpte.mp4", 6, 6, 6)
    md_bt709 = catalog_step.extract_metadata(bt709)
    md_smpte = catalog_step.extract_metadata(smpte)
    assert (md_bt709["color_space"], md_bt709["color_transfer"],
            md_bt709["color_primaries"]) == ("bt709", "bt709", "bt709")
    assert (md_smpte["color_space"], md_smpte["color_transfer"],
            md_smpte["color_primaries"]) == (
                "smpte170m", "smpte170m", "smpte170m")


def test_a_file_without_color_tags_records_none(tmp_path):
    """ffprobe's `unknown` is no tag at all: recorded as None, never a
    string two untagged files would falsely match on."""
    path = _cfr_mp4(tmp_path)
    md = catalog_step.extract_metadata(path)
    assert md["color_space"] is None
    assert md["color_transfer"] is None
    assert md["color_primaries"] is None


# ── Duplicate footage ─────────────────────────────────────────────

def test_identical_takes_under_different_names_are_grouped(tmp_path):
    """A card copied to a backup, or one take imported twice under a
    different name, is the same bytes.  On origin/main the catalog
    recorded no duplicate groups, so two copies of one take competed
    as two independent clips and the vision analysis was paid twice
    for identical footage."""
    first = _cfr_mp4(tmp_path)
    second = tmp_path / "take_b.mp4"
    shutil.copyfile(first, second)
    third = _build(
        tmp_path, "other.mp4",
        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p")
    result = catalog_step.catalog_footage([{
        "path": first,
        "filename": "take_a.mp4",
        "extension": ".mp4",
        "size_bytes": os.path.getsize(first),
        "clip_id": "clip_001",
    }, {
        "path": str(second),
        "filename": "take_b.mp4",
        "extension": ".mp4",
        "size_bytes": os.path.getsize(second),
        "clip_id": "clip_002",
    }, {
        "path": third,
        "filename": "other.mp4",
        "extension": ".mp4",
        "size_bytes": os.path.getsize(third),
        "clip_id": "clip_003",
    }])
    assert result["duplicate_groups"] == [["clip_001", "clip_002"]]
