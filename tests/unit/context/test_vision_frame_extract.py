"""The coarse-frame extraction runs in waves, and the frames are the same.

Measured 2026-10-20 on the Ren-owned scratch copy (scout
`scout-opt-latency`, FIX 5): the evidence stage spawned ONE ffmpeg per
frame, serially - 1,096 processes for 1,096 frames, 519.6 s of a run's
552.6 s of measured work. The frames are independent, so they are
extracted in waves now.

Two things are pinned here, and the second one is the one that matters:

* the waves HAPPEN (the extraction overlaps instead of running one
  frame after another), and
* the frames are BYTE-IDENTICAL to the ones the serial loop wrote -
  the same command at the same timestamp, so a wave changes how many
  extractions are in flight and nothing else.

A parallelisation that changed a byte would be a silent change to what
the vision pass sees, and nothing downstream would say so.
"""

import hashlib
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from library.tools.analysis import vision_pipeline_v3 as vp


def _synth_clip(path, duration_s, size="320x240"):
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-f", "lavfi", "-i", f"testsrc=duration={duration_s}:size={size}:rate=24",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, capture_output=True, check=True)
    return path


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_coarse_frames_are_extracted_in_overlapping_waves(tmp_path, monkeypatch):
    """The defect: one ffmpeg process per frame, one after another.

    1,096 frames cost 519.6 s that way. The frames are independent, so
    the extractor now keeps several in flight. This pins the OVERLAP:
    a serial extractor observes a maximum of one live extraction, and
    this test fails against it.
    """
    source = tmp_path / "source.mov"
    source.write_bytes(b"source")

    lock = threading.Lock()
    live = 0
    peak = 0

    def fake_run(command, **_kwargs):
        nonlocal live, peak
        with lock:
            live += 1
            peak = max(peak, live)
        try:
            # Long enough that a serial extractor never overlaps.
            time.sleep(0.05)
            Path(command[-1]).write_bytes(b"\xff\xd8frame")
        finally:
            with lock:
                live -= 1
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(vp.subprocess, "run", fake_run)
    monkeypatch.setenv(vp.FRAME_EXTRACT_WORKERS_ENV, "4")

    frames = vp.extract_frames(source, 20.0, tmp_path / "cache")

    assert peak > 1, (
        "the coarse frames were extracted one at a time; the evidence "
        f"stage paid one ffmpeg process per frame (peak concurrency {peak})")
    # Every frame is still there, at its own timestamp, in plan order.
    assert [row["timestamp"] for row in frames] == [
        0.0, 5.0, 10.0, 15.0, 19.9]
    assert [Path(row["path"]).name for row in frames] == [
        f"frame_{i:04d}.jpg" for i in range(5)]


def test_a_wave_writes_the_same_bytes_as_the_serial_loop(tmp_path, monkeypatch):
    """The fidelity half: the frames do not change, only the schedule.

    The same source, extracted with one worker and with eight, must
    yield the same bytes for every frame. A parallelisation that changed
    a byte would silently change what the vision pass sees.
    """
    if shutil.which("ffmpeg") is None:
        pytest.skip("needs ffmpeg")

    source = _synth_clip(tmp_path / "source.mp4", 20.0)

    monkeypatch.setenv(vp.FRAME_EXTRACT_WORKERS_ENV, "1")
    serial = vp.extract_frames(source, 20.0, tmp_path / "serial")

    monkeypatch.setenv(vp.FRAME_EXTRACT_WORKERS_ENV, "8")
    waved = vp.extract_frames(source, 20.0, tmp_path / "waved")

    assert [row["timestamp"] for row in serial] == [
        row["timestamp"] for row in waved]
    assert len(serial) == len(waved) == 5
    for one, other in zip(serial, waved):
        assert Path(one["path"]).name == Path(other["path"]).name
        assert _sha256(one["path"]) == _sha256(other["path"]), (
            f"frame {Path(one['path']).name} differs between the serial "
            "extraction and the wave")


def test_the_declared_width_is_the_one_that_runs(tmp_path, monkeypatch):
    """The width is DECIDED, and the decision is honoured.

    An explicit `PIPELINE_FRAME_EXTRACT_WORKERS` wins over the machine's
    core count, and a width outside the declared range is REFUSED
    rather than silently clamped: a typo that quietly halved the
    evidence stage would be a lie about the width.
    """
    monkeypatch.setenv(vp.FRAME_EXTRACT_WORKERS_ENV, "3")
    assert vp.frame_extract_workers() == 3

    monkeypatch.setenv(vp.FRAME_EXTRACT_WORKERS_ENV, "0")
    with pytest.raises(ValueError, match="from 1 to"):
        vp.frame_extract_workers()

    monkeypatch.setenv(vp.FRAME_EXTRACT_WORKERS_ENV, "not-a-number")
    with pytest.raises(ValueError, match="must be an integer"):
        vp.frame_extract_workers()

    monkeypatch.delenv(vp.FRAME_EXTRACT_WORKERS_ENV)
    lanes = vp.frame_extract_workers()
    assert 1 <= lanes <= vp.DEFAULT_FRAME_EXTRACT_WORKERS
    assert lanes <= (os.cpu_count() or 1)


def test_a_cached_frame_is_not_extracted_again(tmp_path, monkeypatch):
    """A wave does not re-decode what the cache already holds.

    The cache is what makes a second run cheap; a parallel extractor
    that re-decoded the cached frames would make it dearer, not cheaper.
    """
    source = tmp_path / "source.mov"
    source.write_bytes(b"source")
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        Path(command[-1]).write_bytes(b"frame")
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(vp.subprocess, "run", fake_run)
    first = vp.extract_frames(source, 20.0, tmp_path / "cache")
    assert len(calls) == 5
    assert len(first) == 5

    repeated = vp.extract_frames(source, 20.0, tmp_path / "cache")
    assert len(calls) == 5, "the cached frames were decoded again"
    assert [row["path"] for row in repeated] == [
        row["path"] for row in first]


def test_the_waves_still_report_every_frame_to_the_progress_channel(
        tmp_path, monkeypatch):
    """The parent's liveness signal survives the waves.

    The parent watches the progress channel to know the child is
    moving: it counts the units that finished and it holds the OLDEST
    unit still in flight to a phase bound. A wave that reported one
    unit for the whole wave, or none at all, would leave the parent
    unable to tell a working child from a wedged one.

    Every frame is declared and every frame is finished - the count is
    the frame count - and nothing is left in flight when the
    extraction returns.
    """
    from library.tools import vision_progress

    source = tmp_path / "source.mov"
    source.write_bytes(b"source")

    def fake_run(command, **_kwargs):
        Path(command[-1]).write_bytes(b"frame")
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(vp.subprocess, "run", fake_run)
    monkeypatch.setenv(vp.FRAME_EXTRACT_WORKERS_ENV, "4")

    progress = vision_progress.VisionProgress(tmp_path / "progress.json")
    frames = vp.extract_frames(source, 20.0, tmp_path / "cache",
                               progress=progress)

    record = vision_progress.read_progress(progress.path)
    assert len(frames) == 5
    assert record["units"] == len(frames)
    assert record["inflight"] == []
