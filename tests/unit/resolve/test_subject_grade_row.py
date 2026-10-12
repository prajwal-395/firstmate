"""The subject-grade build path (MU-10): compile's graded-subject windows
reach a Resolve row that sits on the picture.

The defect this names: compile wrote `subject_grade_overlay` segments
and the build never placed them - no row minted, no segment surfaced -
so the grade was planned, precomposited, and absent from the timeline.
On main `_prepare_timeline_build` knows no `subject_grade_overlay` key,
so the row assertion fails there. Placement itself runs under the
cursor lease in Ren Qualification; what is hermetic here is everything
before it: the segments surface, the material carries their spans, and
the plan mints the subject-grade row above the picture.
"""

import shutil
import subprocess

import numpy as np
import pytest


needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="needs ffmpeg and ffprobe - CI installs both")


@needs_ffmpeg
def _graded_overlay(tmp_path):
    """A real graded-subject window, through the shipped precomposite."""
    from PIL import Image

    from library.tools import subject_grade

    plate_path = tmp_path / "plate.png"
    Image.fromarray(
        np.full((24, 32, 3), 128, dtype=np.uint8),
        mode="RGB").save(plate_path)
    source = str(tmp_path / "c1.mov")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-loop", "1", "-framerate", "10",
         "-i", str(plate_path), "-frames:v", "40",
         "-c:v", "png", "-pix_fmt", "rgb24", source],
        check=True, capture_output=True, timeout=120)
    matte = np.zeros((24, 32), dtype=np.uint8)
    matte[6:18, 10:22] = 255
    files = []
    for i in range(5):
        path = str(tmp_path / f"m_{i:05d}.png")
        Image.fromarray(matte, mode="L").save(path)
        files.append(path)
    out = str(tmp_path / "c1_subject_grade.mov")
    subject_grade.precomposite_subject_grade(
        source, files, out, source_in=0.0, timeline_fps=10.0,
        grade={"gain": 1.4})
    return source, out


def _manifest(source, overlay):
    return {
        "project": {"name": "SG row", "resolution": [32, 24],
                    "frame_rate": 10, "duration_seconds": 4.0},
        "tracks": {"V1": {"clips": [{
            "source_file": source, "timeline_in": 0.0,
            "timeline_out": 4.0, "timeline_in_frame": 0,
            "video_only": True}]}},
        "subject_grade_overlay": {"segments": [{
            "segment_id": "c1_g0_w0", "layer": "subject_grade",
            "clip_id": "c1", "clip_label": "clip1",
            "source_file": source, "overlay_path": overlay,
            "timeline_start": 0.0, "timeline_end": 0.5,
            "total_frames": 5, "format": "qtrle", "has_alpha": True,
            "fps": 10.0, "grade": {"gain": 1.4},
            "matte_route": "sam2_held"}]},
    }


@needs_ffmpeg
def test_a_graded_subject_reaches_its_row_before_the_lease(tmp_path):
    from library.steps.step_6_01_render import resolve_build_timeline

    source, overlay = _graded_overlay(tmp_path)
    manifest = _manifest(source, overlay)
    prepared = resolve_build_timeline._prepare_timeline_build(
        manifest, None, None, str(tmp_path))
    assert prepared["errors"] == []
    assert [s["overlay_path"] for s in prepared["sg_segments"]] == [overlay]
    assert overlay in prepared["available_paths"]
    rows = [t for t in prepared["track_plan"].video_tracks
            if t.role == "subject_grade"]
    assert len(rows) == 1
    assert rows[0].name == "Subject Grade"
    # Directly above the picture it replaces, under everything drawn
    # over it: after the last picture row, before captions and titles.
    roles = [t.role for t in prepared["track_plan"].video_tracks]
    assert roles == ["a_roll", "subject_grade"]


@needs_ffmpeg
def test_no_graded_subject_means_no_subject_grade_row(tmp_path):
    from library.steps.step_6_01_render import resolve_build_timeline

    source, _ = _graded_overlay(tmp_path)
    manifest = _manifest(source, str(tmp_path / "absent.mov"))
    manifest["subject_grade_overlay"] = {"segments": []}
    prepared = resolve_build_timeline._prepare_timeline_build(
        manifest, None, None, str(tmp_path))
    assert prepared["errors"] == []
    assert prepared["sg_segments"] == []
    assert [t.role for t in prepared["track_plan"].video_tracks] == [
        "a_roll"]
