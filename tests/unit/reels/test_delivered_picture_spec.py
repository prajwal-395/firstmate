"""The delivered FILE's picture is verified, on the file, before delivery.

The defect (QA-002, 2026-10-20): the delivery path judged a delivered
file by its DURATION, its loudness and its codec, and never looked at
the picture. The captain caught what that cost twice by eye - an
overlay drawn at twice the size it was asked for, and a logo that was
not in the file at all - and both shipped as done. Nothing that reads
a TIMELINE can see either of them: Resolve stores the transform it was
given and reads it back cleanly, so the editor's own state is not
evidence of the final picture.

So the delivered file's picture is measured ON THE FILE, in one
implementation the delivery verdict and the QC gate both read
(`library/tools/deliverable_qc.verify_picture_spec`):

* the file's own dimensions, against the delivery format;
* whether the picture fills the delivery frame and keeps one geometry;
* whether every overlay the plan placed is IN the file, at the size it
  was asked for (`render_qa.measure_delivered_overlay_geometry`);
* whether the picture goes black, and whether it freezes, without the
  plan having declared it.

What these prove:

* a clean deliverable PASSES its picture spec;
* an overlay that is NOT in the file is caught, and named;
* an overlay drawn at TWICE the size it was asked for is caught, and
  the size it was drawn at is named;
* a black run in the delivered file is caught;
* a frozen run in the delivered file is caught;
* an overlay nobody declared is UNMEASURED, not passed and not failed;
* the DELIVERY VERDICT refuses a file whose picture is wrong, and the
  record beside the file says which leg failed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from library.tools import deliver_and_verify, reel_deliver, resolve_lock
from library.tools.delivery_spec import DEFAULT_DELIVERY_SPEC
from tests.resolve_double import FakeTimeline, make_project

FFMPEG = shutil.which("ffmpeg")
TIMELINE_NAME = "Reel 01 - a-reel"
SECONDS = 4
WIDTH, HEIGHT = 320, 180
OVERLAY = 64


def _overlay_png(path: Path) -> str:
    """A 64x64 overlay with a shape a correlation can find."""
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (OVERLAY, OVERLAY), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle([4, 8, 60, 56], outline=(255, 255, 255, 255), width=6)
    draw.line([8, 50, 56, 14], fill=(255, 255, 255, 255), width=5)
    image.save(str(path))
    return str(path)


def _render(target: Path, *, overlay: str | None = None,
            overlay_scale: int = 1, black_seconds: float = 0.0,
            freeze_seconds: float = 0.0) -> str:
    """A real file: a busy picture, and the defects the case needs.

    The picture is a test pattern on purpose - a flat field could not
    tell an overlay that was not drawn from an overlay that is
    invisible against its own background, and the measurement says so
    rather than guessing.
    """
    filters = []
    if freeze_seconds:
        # A HELD frame in the MIDDLE of the picture: the picture stops
        # moving.  A freeze that runs to the end of the file is not
        # what this case is about - `detect_freeze_frames` drops one
        # that is still running when the file ends.
        filters.append(
            f"[0:v]trim=0:1,setpts=PTS-STARTPTS[head];"
            f"[0:v]trim=0:0.05,setpts=PTS-STARTPTS,"
            f"loop=loop={int(freeze_seconds * 24) - 1}:size=1:start=0,"
            f"setpts=N/(24*TB)[hold];"
            f"[0:v]trim={SECONDS - 1}:{SECONDS},setpts=PTS-STARTPTS[tail];"
            f"[head][hold][tail]concat=n=3:v=1:a=0[v]")
    else:
        filters.append(f"[0:v]trim=0:{SECONDS},setpts=PTS-STARTPTS[v]")
    if overlay:
        scale = (f"[2:v]scale={OVERLAY * overlay_scale}:"
                 f"{OVERLAY * overlay_scale}")
        filters.append(f"{scale}[o]")
        filters.append("[v][o]overlay=0:0[vo]")
        filters.append("[vo]format=yuv420p[vout]")
    else:
        filters.append("[v]format=yuv420p[vout]")
    if black_seconds:
        # A black run in the MIDDLE of the picture, not at its edges.
        filters.append(
            f"[vout]trim=0:{SECONDS - black_seconds},setpts=PTS-STARTPTS[t1];"
            f"[vout]trim={SECONDS - black_seconds}:{SECONDS},"
            f"setpts=PTS-STARTPTS,drawbox=color=black:t=fill[t2];"
            f"[t1][t2]concat=n=2:v=1:a=0[vout2]")
        filters.append("[vout2]format=yuv420p[vfinal]")
    else:
        filters.append("[vout]null[vfinal]")

    command = ["ffmpeg", "-v", "error", "-y",
               "-f", "lavfi", "-i",
               f"testsrc2=s={WIDTH}x{HEIGHT}:r=24:d={SECONDS}",
               "-f", "lavfi", "-i",
               f"sine=frequency=440:sample_rate=48000:d={SECONDS}"]
    if overlay:
        command += ["-loop", "1", "-i", overlay]
    command += ["-filter_complex", ";".join(filters),
                "-map", "[vfinal]", "-map", "1:a",
                "-af", "loudnorm=I=-14:TP=-1.0:LRA=11",
                "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", "-c:a", "aac",
                "-t", str(SECONDS), str(target)]
    subprocess.run(command, check=True, capture_output=True)
    return str(target)


@pytest.fixture(scope="module")
def files(tmp_path_factory) -> dict:
    """The scratch renders: clean, and one per seeded defect."""
    if FFMPEG is None:
        pytest.skip("ffmpeg not on PATH")
    folder = tmp_path_factory.mktemp("delivered_picture")
    overlay = _overlay_png(folder / "overlay.png")
    return {
        "overlay": overlay,
        "clean": _render(folder / "clean.mp4", overlay=overlay),
        "missing": _render(folder / "missing.mp4"),
        "twice": _render(folder / "twice.mp4", overlay=overlay,
                         overlay_scale=2),
        "black": _render(folder / "black.mp4", overlay=overlay,
                         black_seconds=2.0),
        "frozen": _render(folder / "frozen.mp4", overlay=overlay,
                          freeze_seconds=2.0),
    }


def _expectation(files, **overrides):
    """The overlay the plan placed, and where it said it goes."""
    from library.tools import render_qa

    fields = dict(label="logo", overlay_path=files["overlay"],
                  timeline_start=0.0, timeline_end=float(SECONDS),
                  expected_box=(0.0, 0.0, float(OVERLAY), float(OVERLAY)))
    fields.update(overrides)
    return render_qa.OverlayExpectation(**fields)


def test_a_clean_deliverable_passes_its_picture_spec(files):
    """The acceptance: a correctly rendered file passes the picture
    spec - the overlays are in it, at the size they were placed at."""
    from library.tools.deliverable_qc import verify_picture_spec

    verdict = verify_picture_spec(
        files["clean"],
        expected_width=WIDTH, expected_height=HEIGHT,
        overlay_expectations=[_expectation(files)])

    assert verdict.dimensions.passed is True
    assert verdict.framing.passed is True
    assert verdict.overlays.passed is True
    assert verdict.black_frames.passed is True
    assert verdict.freeze_frames.passed is True
    assert verdict.passed is True


def test_a_graphic_that_is_not_in_the_file_is_caught(files):
    """The defect the captain caught: a logo that is not in the render
    at all. The picture's own edges are what the measurement is read
    against, so a graphic that never reached the file is named."""
    from library.tools.deliverable_qc import verify_picture_spec

    verdict = verify_picture_spec(
        files["missing"],
        expected_width=WIDTH, expected_height=HEIGHT,
        overlay_expectations=[_expectation(files)])

    assert verdict.overlays.passed is False
    assert verdict.passed is False
    assert "NOT in the delivered file" in verdict.overlays.detail
    assert verdict.overlays.value["overlays"][0]["label"] == "logo"


def test_an_overlay_drawn_at_twice_the_size_is_caught(files):
    """The defect the captain caught: an overlay at 2x. The size it
    was drawn at is read off the pixels, and named."""
    from library.tools.deliverable_qc import verify_picture_spec

    verdict = verify_picture_spec(
        files["twice"],
        expected_width=WIDTH, expected_height=HEIGHT,
        overlay_expectations=[_expectation(files)])

    assert verdict.overlays.passed is False
    assert verdict.passed is False
    overlay = verdict.overlays.value["overlays"][0]
    assert overlay["verdict"] == "defect"
    assert overlay["measured_scale"] == 2.0
    assert "2x the size it was asked for" in overlay["detail"]


def test_a_black_run_in_the_delivered_file_is_caught(files):
    """The picture going away is a defect in the delivered file, not a
    matter of taste."""
    from library.tools.deliverable_qc import verify_picture_spec

    verdict = verify_picture_spec(
        files["black"],
        expected_width=WIDTH, expected_height=HEIGHT,
        overlay_expectations=[_expectation(files)])

    assert verdict.black_frames.passed is False
    assert verdict.passed is False
    assert "undeclared black" in verdict.black_frames.detail


def test_a_frozen_run_in_the_delivered_file_is_caught(files):
    """The picture stopping is a defect in the delivered file, and
    nothing but a declaration excuses it."""
    from library.tools.deliverable_qc import verify_picture_spec

    verdict = verify_picture_spec(
        files["frozen"],
        expected_width=WIDTH, expected_height=HEIGHT,
        overlay_expectations=[_expectation(files)])

    assert verdict.freeze_frames.passed is False
    assert verdict.passed is False
    assert "frozen frame" in verdict.freeze_frames.detail


def test_a_declared_freeze_is_excused(files):
    """A declared ending may hold a frame: that is the ending, not a
    defect. The declaration excuses it and nothing else does."""
    from library.tools.deliverable_qc import verify_picture_spec

    verdict = verify_picture_spec(
        files["frozen"],
        expected_width=WIDTH, expected_height=HEIGHT,
        overlay_expectations=[_expectation(files)],
        declared_freeze_spans=[(0.0, float(SECONDS))])

    assert verdict.freeze_frames.passed is True


def test_an_overlay_nobody_declared_is_unmeasured_not_passed(files):
    """A leg nobody could measure says so. It is not a pass: the
    value names what was not measured."""
    from library.tools.deliverable_qc import verify_picture_spec

    verdict = verify_picture_spec(
        files["clean"], expected_width=WIDTH, expected_height=HEIGHT)

    assert verdict.overlays.passed is True
    assert "not measured" in verdict.overlays.detail
    assert verdict.overlays.value is None


def test_the_box_the_measurement_judges_against_is_the_engines_own(
        files, tmp_path):
    """The expectation is the engine's own record: the WHOLE FRAME for a
    full-canvas overlay, and the sidecar step 4.05 writes for a tight
    one. An asset with neither is UNMEASURED - a guess about where an
    unrecorded overlay went would be a guess about the defect."""
    from library.tools import render_qa

    full_canvas = tmp_path / "full.png"
    from PIL import Image

    Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0)).save(str(full_canvas))
    expectation = render_qa.delivered_overlay_expectation(
        str(full_canvas), (WIDTH, HEIGHT))
    assert expectation is not None
    assert expectation.expected_box == (0.0, 0.0, float(WIDTH),
                                        float(HEIGHT))

    # A tight overlay, with the sidecar the engine writes beside it.
    tight = tmp_path / "tight.mov"
    shutil.copyfile(files["overlay"], str(tight))
    sidecar = tmp_path / "tight_box.json"
    sidecar.write_text(json.dumps(
        {"union": {"x0": 10, "y0": 20, "x1": 74, "y1": 84}}),
        encoding="utf-8")
    expectation = render_qa.delivered_overlay_expectation(
        str(tight), (WIDTH, HEIGHT))
    assert expectation is not None
    assert expectation.expected_box == (10.0, 20.0, 74.0, 84.0)

    # The same tight overlay with NO sidecar: nowhere recorded.
    loose = tmp_path / "loose.png"
    shutil.copyfile(files["overlay"], str(loose))
    assert render_qa.delivered_overlay_expectation(
        str(loose), (WIDTH, HEIGHT)) is None


def test_the_delivery_verdict_refuses_a_file_whose_picture_is_wrong(files):
    """The acceptance: the delivery verdict grades the delivered
    file's PICTURE, and a file that fails it is not delivered."""
    verdict = reel_deliver.verify_deliverable(
        files["twice"], float(SECONDS), WIDTH, HEIGHT, "H264",
        DEFAULT_DELIVERY_SPEC,
        overlay_expectations=[_expectation(files)])

    assert verdict["picture_spec"]["overlays"]["passed"] is False
    assert verdict["picture_spec"]["passed"] is False
    assert verdict["passed"] is False


# ── The delivery STATE cannot read "delivered" ──────────────────────


@pytest.fixture()
def world(tmp_path, monkeypatch, files):
    """A project with one live timeline, Resolve and the machine faked.

    The RENDER and the MASTERING are faked; the VERIFICATION is not -
    the point of this lane is that the file's own picture is measured.
    """
    import yaml

    cfg = {"name": "T", "slug": "t",
           "resolve": {"project_name": "Scratch"},
           "pipeline": {}}
    (tmp_path / "project.yaml").write_text(yaml.safe_dump(cfg),
                                           encoding="utf-8")
    folder = str(tmp_path)
    timeline = FakeTimeline(TIMELINE_NAME, start_frame=0, end_frame=95)
    project = make_project(name="Scratch", timelines=[timeline],
                           current=timeline, frame_rate=24)

    monkeypatch.setenv(resolve_lock.LOCK_DIR_ENV, str(tmp_path / "lock"))
    monkeypatch.setattr(resolve_lock, "_sole_writer_reason", None)
    monkeypatch.setattr(reel_deliver, "check_render", list)
    monkeypatch.setattr("library.tools.reel_build._connect_resolve_project",
                        lambda name: project)

    def fake_render(timeline_name="", output_dir="", output_name="",
                    fmt="", codec="", timeout_seconds=0, timeline_id="",
                    expected_project_name=""):
        raw = Path(output_dir) / f"{output_name}.mp4"
        raw.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(files["twice"], raw)
        return {"output_path": str(raw), "size_bytes": raw.stat().st_size,
                "job_id": "j1", "job_status": "Complete",
                "timeline_name": timeline_name, "format": fmt,
                "codec": codec}

    def fake_master(report, output_path, target_lufs=-14.0,
                    true_peak_ceiling=-1.0):
        Path(output_path).write_bytes(Path(report["output_path"]).read_bytes())
        return {**report, "raw_output_path": report["output_path"],
                "output_path": output_path, "mastered": True,
                "stage": "mastered", "pre_master": False,
                "mastering": {"output": {"lufs": target_lufs,
                                         "true_peak_dbtp": -1.5}}}

    from library.tools.execution import resolve_render

    monkeypatch.setattr(resolve_render, "render_timeline", fake_render)
    monkeypatch.setattr("library.tools.master_loudness.master_render_report",
                        fake_master)
    return folder


def test_a_delivery_whose_picture_is_wrong_is_not_delivered(world, tmp_path):
    """The acceptance: a file whose picture is wrong cannot be
    reported as delivered successfully. The delivery is REFUSED, and
    the record beside the file says which leg failed."""
    with pytest.raises(deliver_and_verify.DeliverRefused,
                       match="verification FAILED"):
        deliver_and_verify.deliver_committed_edit(
            world, timeline_name=TIMELINE_NAME, source="ren deliver")

    record = json.loads(
        (tmp_path / "exports" / f"{TIMELINE_NAME}.deliver.json").read_text(
            encoding="utf-8"))
    assert record["delivered"] is False
    assert record["verification"]["passed"] is False
    assert record["verification"]["picture_spec"]["passed"] is False
