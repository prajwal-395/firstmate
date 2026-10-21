"""The delivered FILE's audio is verified, on the file, before delivery.

The defect (QA-004, 2026-10-20): the delivery path judged a delivered
file's audio by LOUDNESS alone. Integrated loudness is a GATED
measurement - BS.1770 drops everything below the absolute gate - so a
delivered file that LOST its dialogue still measured on target, and
the delivery state read "delivered" over it. Measured on this path
before the fix: a file whose last 16 of 20 seconds were digital
silence under picture came back ``passed: True`` from BOTH
``reel_deliver.verify_deliverable`` and
``deliverable_qc.run_deliverable_qc``.

The other half of the same defect: a file that is silent ON PURPOSE
(the captain's declared silent end card) failed the loudness leg with
"-inf LUFS is inf dB off the target" - a number nobody can act on, for
a file that is correct.

What these prove:

* a delivered file that lost its dialogue is NOT delivered, and the
  verdict names the seconds that played over digital silence;
* a DECLARED silent ending PASSES - the declaration excuses it and
  nothing else does;
* a file with no audible content has no loudness to be wrong, and the
  silence leg is the one that decides;
* a file that lost a channel is not delivered;
* the DELIVERY STATE cannot read "delivered" when the file fails its
  audio spec: the delivery is refused and the record says so.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from library.tools import deliver_and_verify, reel_deliver, resolve_lock
from library.tools.delivery_spec import DEFAULT_DELIVERY_SPEC
from tests.resolve_double import FakeTimeline, make_project

FFMPEG = shutil.which("ffmpeg")
TIMELINE_NAME = "Reel 01 - a-reel"
SECONDS = 20


def _render(tmp_path, name: str, *, audio: list, gain_db: float = 8.0) -> str:
    """A real file: grey picture, and the audio the case needs.

    `audio` is a list of ffmpeg lavfi sources for the audio, joined
    end to end, so a case can put the words in the first four seconds
    and nothing after them - the shape a lost dialogue leaves behind.
    """
    target = tmp_path / f"{name}.mp4"
    sources = []
    for index, source in enumerate(audio):
        sources += ["-f", "lavfi", "-i", source]
    parts = [f"[{index + 1}:a]volume={gain_db}dB[a{index}]"
             for index in range(len(audio))]
    if len(audio) > 1:
        parts.append("".join(f"[a{index}]" for index in range(len(audio)))
                     + f"concat=n={len(audio)}:v=0:a=1[a]")
    else:
        parts.append("[a0]anull[a]")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"color=c=0x345678:s=320x180:r=24:d={SECONDS}",
         *sources,
         "-filter_complex", ";".join(parts),
         "-map", "0:v:0", "-map", "[a]", "-c:v", "libx264",
         "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-t", str(SECONDS), str(target)],
        check=True, capture_output=True)
    return str(target)


@pytest.fixture(scope="module")
def files(tmp_path_factory) -> dict:
    """The three files the cases judge: all audio, no dialogue, silent."""
    if FFMPEG is None:
        pytest.skip("ffmpeg not on PATH")
    folder = tmp_path_factory.mktemp("delivered_audio")
    return {
        "all_audio": _render(
            folder, "all_audio",
            audio=[f"sine=frequency=440:sample_rate=48000:d={SECONDS}"]),
        # The words stop at four seconds; the picture does not.
        "no_dialogue": _render(
            folder, "no_dialogue",
            audio=["sine=frequency=440:sample_rate=48000:d=4",
                   "anullsrc=r=48000:cl=mono:d=16"]),
        # Nothing at all: an intentionally silent deliverable.
        "silent": _render(
            folder, "silent",
            audio=[f"anullsrc=r=48000:cl=mono:d={SECONDS}"], gain_db=0.0),
    }


def test_a_delivered_file_that_lost_its_dialogue_is_not_delivered(files):
    """The defect: loudness cannot see missing dialogue, so the file
    passed. The silence under picture can, and it names the seconds."""
    verdict = reel_deliver.verify_deliverable(
        files["no_dialogue"], float(SECONDS), 320, 180, "H264",
        DEFAULT_DELIVERY_SPEC)

    assert verdict["audio_spec"]["silence"]["passed"] is False
    assert verdict["passed"] is False
    # The loudness leg passed - that is WHY the file slipped through
    # before: the words that survived measured on target.
    assert verdict["audio_spec"]["loudness"]["passed"] is True
    assert "undeclared digital silence" in (
        verdict["audio_spec"]["silence"]["detail"])


def test_a_declared_silent_ending_passes_the_audio_spec(files):
    """The captain's declared silent end card is CORRECT: the silence
    the declaration excuses passes, and the same file without the
    declaration does not."""
    declared = reel_deliver.verify_deliverable(
        files["silent"], float(SECONDS), 320, 180, "H264",
        DEFAULT_DELIVERY_SPEC,
        declared_silence_spans=[(0.0, float(SECONDS))])
    assert declared["audio_spec"]["silence"]["passed"] is True
    assert declared["passed"] is True

    undeclared = reel_deliver.verify_deliverable(
        files["silent"], float(SECONDS), 320, 180, "H264",
        DEFAULT_DELIVERY_SPEC)
    assert undeclared["passed"] is False


def test_a_file_with_no_audible_content_has_no_loudness_to_be_wrong(files):
    """The defect: "-inf LUFS is inf dB off the target" is not a thing
    a reader can act on. A file with nothing audible is not judged on
    loudness - the silence leg is the one that decides it."""
    verdict = reel_deliver.verify_deliverable(
        files["silent"], float(SECONDS), 320, 180, "H264",
        DEFAULT_DELIVERY_SPEC)
    loudness = verdict["audio_spec"]["loudness"]
    assert loudness["passed"] is True
    assert "no audible content" in loudness["detail"]
    assert verdict["audio_spec"]["silence"]["passed"] is False


def test_a_file_that_lost_a_channel_is_not_delivered(tmp_path):
    """Channel presence: a stereo file whose right channel carries no
    signal at all lost that channel, and the loudness measurement
    cannot see it - a silent channel contributes nothing to the
    integrated value either."""
    if FFMPEG is None:
        pytest.skip("ffmpeg not on PATH")
    target = tmp_path / "one_channel.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"color=c=0x345678:s=320x180:r=24:d={SECONDS}",
         "-f", "lavfi", "-i",
         f"sine=frequency=440:sample_rate=48000:d={SECONDS}",
         "-filter_complex",
         "[1:a]asplit=2[l][r];[r]volume=-120dB[rs];[l][rs]join=inputs=2:channel_layout=stereo[a]",
         "-map", "0:v:0", "-map", "[a]", "-c:v", "libx264",
         "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-t", str(SECONDS), str(target)],
        check=True, capture_output=True)

    verdict = reel_deliver.verify_deliverable(
        str(target), float(SECONDS), 320, 180, "H264",
        DEFAULT_DELIVERY_SPEC)
    assert verdict["audio_spec"]["channels"]["passed"] is False
    assert verdict["passed"] is False


def test_the_qc_gate_and_the_delivery_verdict_measure_the_same_audio(files):
    """The defect: two audio checks, two answers. The QC gate and the
    delivery verdict read ONE implementation, so they cannot disagree
    about a file's audio."""
    from library.tools import deliverable_qc

    verdict = reel_deliver.verify_deliverable(
        files["no_dialogue"], float(SECONDS), 320, 180, "H264",
        DEFAULT_DELIVERY_SPEC)
    qc = deliverable_qc.run_deliverable_qc(
        files["no_dialogue"], DEFAULT_DELIVERY_SPEC, float(SECONDS))
    assert qc.passed is verdict["passed"] is False
    assert qc.audio.silence.passed == (
        verdict["audio_spec"]["silence"]["passed"])


# ── The delivery STATE cannot read "delivered" ──────────────────────


@pytest.fixture()
def world(tmp_path, monkeypatch, files):
    """A project with one live timeline, Resolve and the machine faked.

    The RENDER and the MASTERING are faked; the VERIFICATION is not -
    the point of this lane is that the file's own audio is measured.
    """
    cfg = {"name": "T", "slug": "t",
           "resolve": {"project_name": "Scratch"},
           "pipeline": {}}
    (tmp_path / "project.yaml").write_text(yaml.safe_dump(cfg),
                                           encoding="utf-8")
    folder = str(tmp_path)
    timeline = FakeTimeline(TIMELINE_NAME, start_frame=0, end_frame=479)
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
        shutil.copyfile(files["no_dialogue"], raw)
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


def test_a_delivery_that_fails_its_audio_spec_is_not_delivered(world, tmp_path):
    """The acceptance: a file that fails the requested audio
    specification cannot be reported as delivered successfully. The
    delivery is REFUSED, and the record beside the file says
    `delivered: false`."""
    with pytest.raises(deliver_and_verify.DeliverRefused,
                       match="verification FAILED"):
        deliver_and_verify.deliver_committed_edit(
            world, timeline_name=TIMELINE_NAME, source="ren deliver")

    record = json.loads(
        (tmp_path / "exports" / f"{TIMELINE_NAME}.deliver.json").read_text(
            encoding="utf-8"))
    assert record["delivered"] is False
    assert record["verification"]["passed"] is False
    assert record["verification"]["audio_spec"]["silence"]["passed"] is False
