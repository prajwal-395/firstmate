"""M2 frame sampling runs per-clip units through the Fabric scheduler.

The defect this pins: `build_project_frames` sampled every clip in one
serial loop, so a project's frame samples decoded one at a time while
the machine's other cores idled. The units are independent (one
whole-file ffmpeg decode per clip into its own digest directory), so
they are one scheduler task each, serialized only where they share a
digest. The report shape is unchanged - rows in catalog order, offline
clips reported, one clip's failure never stops the others.
"""
import json
import os
import threading
from pathlib import Path

from library.tools import source_memory


def _write(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _project_with_clips(tmp_path: Path, count: int,
                        same_bytes: bool = False) -> Path:
    """A project folder whose catalog names `count` real files."""
    root = tmp_path / "proj"
    clips = []
    for index in range(count):
        seed = b"shared-seed" if same_bytes else f"clip-{index}-".encode()
        media = tmp_path / "raw" / f"C{index:03d}.MXF"
        media.parent.mkdir(parents=True, exist_ok=True)
        media.write_bytes(seed * (3 * 1024 * 1024 // len(seed) + 1))
        clips.append({"clip_id": f"clip_{index:03d}",
                      "source_file": str(media)})
    _write(root / "pipeline_data.json",
           {"step_outputs": {"catalog": {"clip_catalog": clips}}})
    return root


def _pairs():
    return [(f"/fake/frame_{i:06d}.jpg", i * 0.5) for i in range(3)], True


def test_clips_sample_concurrently(tmp_path, monkeypatch):
    """Every clip's decode starts without waiting for another's to
    finish.

    Defect caught: the serial loop decoded one clip at a time, so M2
    over a project cost the sum of its clips' decodes. The barrier
    trips only when every clip is inside its decode at once - under
    the serial loop the first call blocks until the barrier times
    out, and every clip fails.
    """
    monkeypatch.setenv(source_memory.MEMORY_ROOT_ENV,
                       str(tmp_path / "memory"))
    slots = os.cpu_count() or 1
    count = min(3, slots)
    root = _project_with_clips(tmp_path, count)
    barrier = threading.Barrier(count, timeout=30)

    def rendezvous(source_file, out_dir, width, use_hwaccel):
        barrier.wait()
        return _pairs()

    monkeypatch.setattr(source_memory, "extract_iframes", rendezvous)
    report = source_memory.build_project_frames(str(root))

    assert barrier.n_waiting == 0
    assert report["failed"] == []
    assert [row["clip_id"] for row in report["clips"]] == [
        f"clip_{index:03d}" for index in range(count)]


def test_each_actionable_clip_builds_exactly_once_in_catalog_order(
        tmp_path, monkeypatch):
    """The scheduled run reports what the serial loop reported: one row
    per catalog clip in catalog order, offline media reported, and no
    clip built twice (a second build would re-decode a fresh sample).

    Guard for the refactor: the scheduler must not drop, duplicate or
    reorder the lane's rows.
    """
    monkeypatch.setenv(source_memory.MEMORY_ROOT_ENV,
                       str(tmp_path / "memory"))
    root = _project_with_clips(tmp_path, 3)
    calls = []
    monkeypatch.setattr(source_memory, "extract_iframes",
                        lambda *args: (calls.append(args[0]), _pairs())[1])
    report = source_memory.build_project_frames(str(root))

    assert [row["clip_id"] for row in report["clips"]] == [
        "clip_000", "clip_001", "clip_002"]
    assert sorted(Path(call).name for call in calls) == [
        "C000.MXF", "C001.MXF", "C002.MXF"]
    assert report["failed"] == []


def test_one_failing_clip_does_not_stop_the_others(tmp_path, monkeypatch):
    """A clip whose decode raises is FAILED with its reason while its
    siblings still build - the lane's failure isolation, preserved
    across the refactor onto threads."""
    monkeypatch.setenv(source_memory.MEMORY_ROOT_ENV,
                       str(tmp_path / "memory"))
    root = _project_with_clips(tmp_path, 3)

    def flaky(source_file, out_dir, width, use_hwaccel):
        if "C001" in source_file:
            raise RuntimeError("boom")
        return _pairs()

    monkeypatch.setattr(source_memory, "extract_iframes", flaky)
    report = source_memory.build_project_frames(str(root))

    by_id = {row["clip_id"]: row for row in report["clips"]}
    assert "boom" in by_id["clip_001"]["failed"]
    assert by_id["clip_000"]["frame_count"] == 3
    assert by_id["clip_002"]["frame_count"] == 3
    assert report["failed"] == ["clip_001"]


def _without_timings(rows):
    """Wall-clock timings are the run's, not the measurement's."""
    cleaned = []
    for row in rows:
        row = dict(row)
        row.pop("timings", None)
        cleaned.append(row)
    return cleaned


def test_scheduled_and_inline_sampling_measure_identical_bytes(
        tmp_path, monkeypatch):
    """The scheduler decides where and when each clip's decode runs,
    never the bytes it measures: the lane report for a fixture is
    identical through the scheduler and through direct per-clip
    calls. This pins the EXEMPT_IMPORTS rationale - a fix to
    placement, leases or the graph record cannot change a cached
    preflight value, so those files stay out of the step identity.
    """
    def extract(source_file, out_dir, width, use_hwaccel):
        return _pairs()

    monkeypatch.setattr(source_memory, "extract_iframes", extract)
    root = _project_with_clips(tmp_path, 3)

    monkeypatch.setenv(source_memory.MEMORY_ROOT_ENV,
                       str(tmp_path / "memory-scheduled"))
    scheduled = source_memory.build_project_frames(str(root))

    monkeypatch.setenv(source_memory.MEMORY_ROOT_ENV,
                       str(tmp_path / "memory-inline"))
    catalog = source_memory.load_catalog(str(root))
    recorded = source_memory.load_recorded_fingerprints(str(root))
    inline = []
    for clip in catalog:
        clip_id = clip.get("clip_id")
        path = clip.get("source_file") or clip.get("path") or ""
        digest, _ = source_memory.digest_for_clip(str(root), clip, recorded)
        account = source_memory.build_frames(path, digest)
        inline.append({"clip_id": clip_id, **account})

    assert (_without_timings(scheduled["clips"])
            == _without_timings(inline))
    assert scheduled["failed"] == []
