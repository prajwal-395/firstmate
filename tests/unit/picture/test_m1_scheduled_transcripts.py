"""M0/M1 transcript builds run per-clip through the Fabric scheduler.

The defect this pins: `build_project` heard and aligned every clip in
one serial loop, so a project's transcripts cost the sum of their
hearings while the machine's other cores idled. The units are
independent (one demux plus Voz hearing plus MFA alignment per clip,
each in its own scratch directory), so they are one scheduler task
each, serialized only where they share a digest. The report shape is
unchanged: rows in catalog order, offline clips reported, one clip's
failure never stops the others.
"""
import json
import os
import threading
from pathlib import Path

from library.tools import source_memory


def _write(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _project_with_clips(tmp_path: Path, count: int) -> Path:
    """A project folder whose catalog names `count` real files."""
    root = tmp_path / "proj"
    clips = []
    for index in range(count):
        seed = f"hear-{index}-".encode()
        media = tmp_path / "raw" / f"S{index:03d}.MXF"
        media.parent.mkdir(parents=True, exist_ok=True)
        media.write_bytes(seed * (3 * 1024 * 1024 // len(seed) + 1))
        clips.append({"clip_id": f"clip_{index:03d}",
                      "source_file": str(media)})
    _write(root / "pipeline_data.json",
           {"step_outputs": {"catalog": {"clip_catalog": clips}}})
    return root


def _account(path):
    return {"source_file": str(path), "reused": False,
            "m1_status": "transcribed", "utterances": 2, "words": 5}


def test_clips_transcribe_concurrently(tmp_path, monkeypatch):
    """Every clip's hearing starts without waiting for another's to
    finish.

    Defect caught: the serial loop heard one clip at a time, so M0+M1
    over a project cost the sum of its hearings. The barrier trips
    only when every clip is inside its hearing at once - under the
    serial loop the first call blocks until the barrier times out,
    and every clip fails.
    """
    monkeypatch.setenv(source_memory.MEMORY_ROOT_ENV,
                       str(tmp_path / "memory"))
    slots = os.cpu_count() or 1
    count = min(3, slots)
    root = _project_with_clips(tmp_path, count)
    barrier = threading.Barrier(count, timeout=30)

    def rendezvous(path, declaration, root):
        barrier.wait()
        return _account(path)

    monkeypatch.setattr(source_memory, "build_source", rendezvous)
    report = source_memory.build_project(str(root))

    assert barrier.n_waiting == 0
    assert report["failed"] == []
    assert [row["clip_id"] for row in report["clips"]] == [
        f"clip_{index:03d}" for index in range(count)]


def test_each_actionable_clip_builds_exactly_once_in_catalog_order(
        tmp_path, monkeypatch):
    """The scheduled run reports what the serial loop reported: one row
    per catalog clip in catalog order, offline media reported, and no
    clip heard twice.

    Guard for the refactor: the scheduler must not drop, duplicate or
    reorder the lane's rows.
    """
    monkeypatch.setenv(source_memory.MEMORY_ROOT_ENV,
                       str(tmp_path / "memory"))
    root = _project_with_clips(tmp_path, 3)
    calls = []

    def record(path, declaration, root):
        calls.append(Path(path).name)
        return _account(path)

    monkeypatch.setattr(source_memory, "build_source", record)
    report = source_memory.build_project(str(root))

    assert [row["clip_id"] for row in report["clips"]] == [
        "clip_000", "clip_001", "clip_002"]
    assert sorted(calls) == ["S000.MXF", "S001.MXF", "S002.MXF"]
    assert report["failed"] == []


def test_one_failing_clip_does_not_stop_the_others(tmp_path, monkeypatch):
    """A clip whose hearing raises is FAILED with its reason while its
    siblings still build - the lane's failure isolation, preserved
    across the refactor onto threads."""
    monkeypatch.setenv(source_memory.MEMORY_ROOT_ENV,
                       str(tmp_path / "memory"))
    root = _project_with_clips(tmp_path, 3)

    def flaky(path, declaration, root):
        if "S001" in str(path):
            raise RuntimeError("boom")
        return _account(path)

    monkeypatch.setattr(source_memory, "build_source", flaky)
    report = source_memory.build_project(str(root))

    by_id = {row["clip_id"]: row for row in report["clips"]}
    assert "boom" in by_id["clip_001"]["failed"]
    assert by_id["clip_000"]["utterances"] == 2
    assert by_id["clip_002"]["utterances"] == 2
    assert report["failed"] == ["clip_001"]


def test_offline_media_is_reported_not_failed(tmp_path, monkeypatch):
    """A catalog clip whose file is gone is reported media-offline,
    never built and never failed - the same row the serial loop
    wrote."""
    monkeypatch.setenv(source_memory.MEMORY_ROOT_ENV,
                       str(tmp_path / "memory"))
    root = tmp_path / "proj"
    _write(root / "pipeline_data.json",
           {"step_outputs": {"catalog": {"clip_catalog": [
               {"clip_id": "clip_001", "filename": "GONE.MXF",
                "source_file": str(tmp_path / "GONE.MXF")},
           ]}}})

    def must_not_build(*args, **kwargs):
        raise AssertionError("an offline clip was built")

    monkeypatch.setattr(source_memory, "build_source", must_not_build)
    report = source_memory.build_project(str(root))

    assert report["clips"][0]["skipped"] == "media-offline"
    assert report["failed"] == []
