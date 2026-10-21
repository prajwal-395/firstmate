"""The delivery identity record: what a delivered file IS.

The defect this covers: a delivered file carried no record of what
produced it. `export_verification`'s identity criterion (QA-008)
answered "unknown" for every delivery that did not come from a reel,
and the section-12 demo shipped a hand-rolled export 9.18 dB under the
delivery target with nothing on record that said so.

Two halves, both here:

* the RENDER PRIMITIVE says its output is pre-master, so no route can
  hand over an unmastered file while calling it finished;
* the RECORD beside the file says what the file is - mastered or not,
  delivered or not - and carries the frame rate, the range, the length
  and the file's own digest.
"""

from __future__ import annotations

import json

from library.tools import delivery_identity


def _record(tmp_path, **overrides):
    video = tmp_path / "Reel 03 - hook.mp4"
    video.write_bytes(b"\x00" * 2048)
    fields = {"timeline_name": "Reel 03 - hook", "reel": 3, "fps": 30.0,
              "seconds": 10.0, "start_frame": 0, "end_frame": 299,
              "render": {"output_path": str(video)}, "delivered": True,
              "stage": delivery_identity.STAGE_MASTERED,
              "source": "ren deliver"}
    fields.update(overrides)
    record = delivery_identity.write_record(str(video), **fields)
    return video, record


def test_the_record_names_the_file_and_its_edit(tmp_path):
    """The record beside the file says which timeline made it, at what
    rate, over what range, and which bytes."""
    video, record = _record(tmp_path)

    assert record["record_path"] == str(tmp_path / "Reel 03 - hook.deliver.json")
    written = json.loads((tmp_path / "Reel 03 - hook.deliver.json").read_text(
        encoding="utf-8"))
    assert written["timeline_name"] == "Reel 03 - hook"
    assert written["timeline_fps"] == 30.0
    assert written["timeline_start_frame"] == 0
    assert written["timeline_end_frame"] == 299
    assert written["timeline_seconds"] == 10.0
    assert written["sha256"] == delivery_identity.sha256_file(str(video))
    assert written["size_bytes"] == 2048
    assert written["schema"] == delivery_identity.SCHEMA


def test_a_pre_master_render_is_not_a_delivery(tmp_path):
    """A rendered file that was never mastered says so, and is not one.

    This is the shape the demo's export had: on disk, carrying audio,
    and 9.18 dB under the delivery target. A record that called it
    delivered would be a lie the acceptance harness grades as a pass.
    """
    video, record = _record(
        tmp_path, delivered=False, stage=delivery_identity.STAGE_PRE_MASTER)

    assert record["mastered"] is False
    assert record["delivered"] is False
    assert delivery_identity.is_deliverable(str(video)) is False
    assert delivery_identity.stage_of(str(video)) == (
        delivery_identity.STAGE_PRE_MASTER)


def test_a_mastered_delivery_is_a_delivery(tmp_path):
    """The mastered, verified file is the one the identity criterion
    can tie to the edit that produced it."""
    video, record = _record(tmp_path)

    assert record["mastered"] is True
    assert record["delivered"] is True
    assert delivery_identity.is_deliverable(str(video)) is True
    assert delivery_identity.stage_of(str(video)) == (
        delivery_identity.STAGE_MASTERED)


def test_a_file_with_no_record_is_not_a_delivery(tmp_path):
    """Absence is not a pass: a file nobody recorded is not a delivered
    file, and says so rather than reading as one."""
    video = tmp_path / "orphan.mp4"
    video.write_bytes(b"\x00" * 16)

    assert delivery_identity.read_record(str(video)) is None
    assert delivery_identity.is_deliverable(str(video)) is False
    assert delivery_identity.stage_of(str(video)) == (
        delivery_identity.STAGE_PRE_MASTER)


def test_the_render_primitive_says_its_output_is_pre_master(tmp_path,
                                                            monkeypatch):
    """The render report names the file PRE-MASTER.

    This is what stops a route from shipping the raw render: the report
    the route reads says the file has not been brought to the delivery
    loudness, and the mastering pass is the thing that clears the flag.
    """
    from library.tools.execution import resolve_render

    video = tmp_path / "raw.mp4"
    video.write_bytes(b"\x00" * 200_000)
    # The probe for an audio stream is not what this test is about.
    monkeypatch.setattr(resolve_render, "_assert_has_audio",
                        lambda _path: None)
    report = resolve_render._validate_render_output({
        "output_dir": str(tmp_path),
        "output_name": "raw",
        "render_started_at": 0,
        "job_id": "j1",
        "job_status": "Complete",
        "timeline_name": "Reel 03 - hook",
        "format": "mp4",
        "codec": "H264",
    })

    assert report["pre_master"] is True
    assert report["mastered"] is False
    assert report["stage"] == delivery_identity.STAGE_PRE_MASTER


def test_the_mastering_pass_clears_the_pre_master_flag(tmp_path,
                                                       monkeypatch):
    """After the mastering pass the report says MASTERED, so a route
    that reads the report cannot mistake a master for a raw render."""
    from library.tools import master_loudness

    raw = tmp_path / "raw.mp4"
    raw.write_bytes(b"\x00" * 2048)
    master = tmp_path / "master.mp4"

    def fake_normalize(input_path, output_path, **_kwargs):
        class _Result:
            input_path = str(raw)
            output_path = str(master)
            input_i = -23.18
            input_tp = -4.49
            output_i = -14.34
            output_tp = -1.41
            target_lufs = -14.0
            tolerance = 1.0
            true_peak_ceiling = -1.0
            limiter_headroom_db = 0.5
            final_limiter_target = -1.5
            normalization_attempts = 1
            already_compliant = False

        master.write_bytes(b"\x00" * 2048)
        return _Result()

    monkeypatch.setattr(master_loudness, "normalize_to_delivery",
                        fake_normalize)
    report = master_loudness.master_render_report(
        {"output_path": str(raw), "pre_master": True, "mastered": False},
        str(master))

    assert report["pre_master"] is False
    assert report["mastered"] is True
    assert report["stage"] == "mastered"
    assert report["raw_output_path"] == str(raw)
