"""A real Resolve render judged by the deliverable QC loop, live.

Renders the qualification project's "Q Spine" (synthetic media, known
9.0 s / 1920x1080 / toned timeline) through the same calls the deliver
path makes, then judges the file the way every deliverable is judged:
codec, duration, resolution and black frames via `verify_deliverable`,
loudness via the mastering + gate pair, and the color tags reported
(not gated - this run ESTABLISHES whether Resolve writes them, so a
later slice knows if a file-level transform check can ever fire).

Drives the live Resolve, so it never runs by default: skips unless
`REN_LIVE_RESOLVE=1` inside a granted borrow-resolve window with the
"Ren Qualification" project open (`ren qualification reset` builds
it). Nothing touches the captain's project: the render lands in
pytest's tmp dir, and the fixture lease holds the instance for the
file's duration.
"""
from __future__ import annotations

import json
import subprocess

import pytest

pytestmark = pytest.mark.resolve_live


def _timeline_by_name(project, name: str):
    for index in range(1, int(project.GetTimelineCount() or 0) + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline and timeline.GetName() == name:
            return timeline
    raise AssertionError(
        f"no timeline {name!r} in {project.GetName()!r}: run "
        f"`ren qualification reset` first")


def test_a_real_render_passes_the_deliverable_qc_loop(
        resolve_session, tmp_path):
    """The track's loop closed on pixels: a Resolve render mastered to
    the default spec passes every file check the deliver path runs."""
    from library.tools import render_qa
    from library.tools.delivery_spec import DEFAULT_DELIVERY_SPEC
    from library.tools.deliverable_qc import run_deliverable_qc
    from library.tools.execution import resolve_render
    from library.tools.marker_feedback import connect_resolve
    from library.tools.master_loudness import master_render_report
    from library.tools.reel_deliver import verify_deliverable

    resolve = connect_resolve()
    project = resolve.GetProjectManager().GetCurrentProject()
    assert project.GetName() == "Ren Qualification"
    timeline = _timeline_by_name(project, "Q Spine")
    assert (timeline.GetEndFrame() - timeline.GetStartFrame()) == 216

    report = resolve_render.render_timeline(
        timeline_name="Q Spine", output_dir=str(tmp_path),
        output_name="q_spine_pre_master", fmt="mp4", codec="H264",
        timeout_seconds=600)
    raw_path = report["output_path"]

    final_path = str(tmp_path / "q_spine_master.mp4")
    mastered = master_render_report(
        report, final_path,
        target_lufs=DEFAULT_DELIVERY_SPEC.loudness_target_lufs,
        true_peak_ceiling=(
            DEFAULT_DELIVERY_SPEC.true_peak_ceiling_dbtp))

    verdict = verify_deliverable(
        mastered["output_path"], 9.0, 1920, 1080,
        DEFAULT_DELIVERY_SPEC.codec)
    assert verdict["passed"], json.dumps(verdict, indent=2, default=str)

    qc = run_deliverable_qc(
        mastered["output_path"], DEFAULT_DELIVERY_SPEC, 9.0)
    assert qc.passed, (
        f"duration={qc.duration.passed} black={qc.black_frames.passed} "
        f"loudness={qc.loudness.passed} "
        f"detail={qc.loudness.detail}")

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries",
         "stream=color_space,color_transfer,color_primaries",
         "-of", "json", mastered["output_path"]],
        capture_output=True, encoding="utf-8", timeout=60, check=False)
    assert probe.returncode == 0, probe.stderr.strip()
    streams = (json.loads(probe.stdout or "{}").get("streams") or [{}])
    tags = streams[0]
    assert isinstance(tags, dict)
    print(f"\ncolor tags on a Resolve H264 render: {tags}")
