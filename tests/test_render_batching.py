"""A QA pass renders through Resolve ONCE, and only what needs the composite.

Before: every frame grab and every segment check was its own
configure-render-cleanup on the Deliver page, the perceptual observation
rendered the same clip midpoints a second time, and `clip_placement` -
"is this the right footage?", which the source file answers - was
rendered through Resolve too. `qa_fidelity` classifies each check;
`segment_renderer.render_batch` renders a pass's composite ranges in one
batch and ffmpeg cuts the frames out of it.
"""
import shutil
import subprocess

import pytest

from library.tools import segment_renderer, visual_qa_router
from library.tools.qa_fidelity import (
    GENERATED_ASSET, RESOLVE_COMPOSITE, SOURCE_PIXEL)
from tests.resolve_double import FakeProject, FakeResolve, FakeTimeline


def _project(lie_on=None):
    """A 0-1665 reel; ``lie_on`` is the MarkIn whose job queues the whole
    timeline anyway (the 2026-09-11 shape)."""
    tl = FakeTimeline("T", end_frame=1665)
    project = FakeProject([tl], current=tl)
    project.ignore_render_marks = {lie_on} if lie_on is not None else False
    return tl, project


def test_neighbouring_ranges_merge_and_distant_ones_do_not():
    assert segment_renderer.merge_ranges(
        [(372, 420), (300, 369), (900, 900)], max_gap_frames=5) == [
        (300, 420), (900, 900)]
    assert segment_renderer.merge_ranges([(10, 10), (11, 11)], 0) == [(10, 11)]
    assert segment_renderer.merge_ranges([(10, 10), (12, 12)], 0) == [
        (10, 10), (12, 12)]


def test_a_batch_starts_every_job_together_and_deletes_only_its_own(tmp_path):
    tl, project = _project()
    project.render_jobs.append({"JobId": "captain", "MarkIn": 0, "MarkOut": 9})
    segment_renderer.render_batch(
        FakeResolve(project), project, tl, [(300, 300), (310, 340), (2000, 2000)],
        output_dir=str(tmp_path), max_gap_frames=30)
    assert project.started_renders == [["job-2", "job-3"]], (
        "one StartRendering for the whole batch, one job per merged range")
    assert sorted(project.deleted_render_jobs) == ["job-2", "job-3"]
    assert [job["JobId"] for job in project.GetRenderJobList()] == ["captain"]


def test_one_range_that_did_not_take_refuses_the_whole_batch(tmp_path):
    tl, project = _project(lie_on=2000)
    batch = segment_renderer.render_batch(
        FakeResolve(project), project, tl, [(300, 300), (2000, 2000)],
        output_dir=str(tmp_path))
    assert project.started_renders == [], "a whole-timeline job must never start"
    assert batch.error and "1665" in batch.error
    assert sorted(project.deleted_render_jobs) == ["job-1", "job-2"]


def _manifest():
    clip = {"source_file": "/media/a.mov", "source_in": 4.0, "source_out": 8.0,
            "timeline_in_frame": 0, "timeline_out_frame": 120}
    card = {"source_file": "/cards/end.mov", "source_in": 0.0, "source_out": 3.0,
            "timeline_in_frame": 120, "timeline_out_frame": 210,
            "bookend": "end_card"}
    return {
        "tracks": {"V1": {"clips": [clip, card]}},
        "transitions": [{"timeline_frame": 118, "duration_frames": 4}],
        "vfx": [{"timeline_in_frame": 40, "timeline_out_frame": 80}],
    }


def test_clip_placement_reads_the_file_and_never_renders(monkeypatch, tmp_path):
    plan = visual_qa_router.plan_qa_checks(_manifest(), phase="post_build")
    by_type = {}
    for g in plan.frame_grabs:
        by_type.setdefault(g.check_type, []).append(g.fidelity)
    assert by_type["clip_placement"] == [SOURCE_PIXEL, GENERATED_ASSET]
    assert by_type["vfx"] == [RESOLVE_COMPOSITE]

    read = []
    monkeypatch.setattr(visual_qa_router, "extract_source_frame",
                        lambda f, t, d=None: read.append((f, t)) or None)
    rendered = []
    monkeypatch.setattr(visual_qa_router, "render_batch",
                        lambda *a, **k: rendered.append(a[3]) or
                        segment_renderer.BatchRenderResult(segments=[
                            segment_renderer.SegmentRenderResult(
                                path="", mark_in=a, mark_out=b,
                                duration_frames=b - a + 1, width=1, height=1,
                                success=False, error="fake")
                            for a, b in segment_renderer.merge_ranges(a[3])]))
    monkeypatch.setattr(visual_qa_router, "_analyze_segment",
                        lambda req, seg, *a, **k: seg)

    out = visual_qa_router.execute_qa_plan(
        None, None, None, plan, extra_frames=[60, 165],
        output_dir=str(tmp_path))

    assert read == [("/media/a.mov", 6.0), ("/cards/end.mov", 1.5)]
    assert len(rendered) == 1, "the whole pass is ONE Resolve render"
    frames = {a for a, b in rendered[0] if a == b}
    assert frames == {60, 165}, (
        "only the composite grab and the extra (perceptual) frames render; "
        "the clip_placement midpoints do not")
    assert (113, 127) in rendered[0], "the transition segment is in the batch"
    assert len(out.frame_results) == len(plan.frame_grabs)
    assert all(r.check.passed is False for r in out.frame_results), (
        "a frame nothing produced is a FAILED check, never a pass")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_frames_cut_from_a_merged_render_are_the_frames_asked_for(tmp_path):
    """Frame 150 of a render of 100-219 is decoded frame 50, exactly."""
    seg_path = tmp_path / "seg.mov"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
         "testsrc2=size=160x120:rate=30", "-frames:v", "120",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(seg_path)],
        check=True, capture_output=True)
    seg = segment_renderer.SegmentRenderResult(
        path=str(seg_path), mark_in=100, mark_out=219, duration_frames=120,
        width=160, height=120, success=True)

    got = segment_renderer.extract_frames(seg, [150, 219, 500], str(tmp_path))
    assert set(got) == {150, 219}

    def md5s(path):
        out = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", path, "-pix_fmt", "rgb24",
             "-f", "framemd5", "-"], check=True, capture_output=True,
            encoding="utf-8").stdout
        return [ln.rsplit(",", 1)[1].strip() for ln in out.splitlines()
                if ln and not ln.startswith("#")]

    every = md5s(str(seg_path))
    assert md5s(got[150]) == [every[50]]
    assert md5s(got[219]) == [every[119]]
