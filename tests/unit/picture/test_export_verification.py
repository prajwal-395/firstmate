"""The eight final-output acceptance criteria, one verdict each.

`library/tools/export_verification.py` is the captain's section 6
("Final-Output Verification and Quality Control") as ONE capability.
These tests name the defects it exists to catch:

- a clip the plan declared and the build dropped (QA-001),
- a clip that plays somewhere other than where the plan put it (QA-002),
- a speech clip out of sync with the picture beside it (QA-002),
- a hole in the picture, and a B-roll clip that is NOT a hole (QA-003),
- a caption the build never carried (QA-005),
- and the failure of the whole thing: an export that cannot be checked
  reading as verified.

The file-level checks (`render_qa`) are stubbed: each one has its own
tests, and what is under test here is the capability that CALLS them
and says what their results mean.  The plan's own media is written to
disk, because the plan validator checks that the media it names is
there - a plan whose footage has moved is not a valid plan.
"""

from __future__ import annotations

import pytest

from library.tools import export_verification as ev
from library.tools import render_qa
from library.tools.render_qa import RenderQAResult


def _pass(metric, value=None):
    return RenderQAResult(metric, True, value, None, "info", f"{metric} ok")


def _stub_file_checks(monkeypatch):
    """Every file-level check the capability calls, passing."""
    monkeypatch.setattr(
        render_qa, "detect_black_frames",
        lambda path, **kwargs: RenderQAResult(
            "black_frames", True, [], None, "info", "no black"))
    monkeypatch.setattr(
        render_qa, "detect_freeze_frames",
        lambda path, **kwargs: RenderQAResult(
            "freeze_frames", True, [], None, "info", "no freeze"))
    monkeypatch.setattr(
        render_qa, "verify_resolution",
        lambda path, **kwargs: _pass("resolution", {"width": 1080,
                                                     "height": 1920}))
    monkeypatch.setattr(
        render_qa, "verify_framerate",
        lambda path, **kwargs: _pass("framerate", 30.0))
    monkeypatch.setattr(
        render_qa, "verify_duration",
        lambda path, expected, **kwargs: _pass("duration", expected))
    monkeypatch.setattr(
        render_qa, "verify_encoding",
        lambda path, **kwargs: _pass("encoding", {"codec": "h264"}))
    monkeypatch.setattr(
        render_qa, "verify_channel_configuration",
        lambda path, **kwargs: _pass("channel_configuration",
                                     [{"channels": 2}]))
    monkeypatch.setattr(
        render_qa, "detect_clipping",
        lambda path, **kwargs: _pass("clipping", {"clipped_samples": 0}))
    monkeypatch.setattr(
        render_qa, "measure_lufs",
        lambda path, **kwargs: _pass("lufs", {"input_i": -14.0}))
    monkeypatch.setattr(
        render_qa, "measure_graphics_render",
        lambda path, spans=None, **kwargs: _pass("graphics_render"))


# ── The plan and the build, as the pipeline writes them ──
#
# The source ranges are NOT whole seconds: the plan validator refuses a
# V1 clip whose source in and out both land on a whole second, because
# a speech cut to word boundaries never does (see
# `manifest_validator._check_no_fabricated_source_ranges`).  They are
# whole FRAMES, so the plan's seconds and the build's frames are the
# same range stated twice.

FPS = 30.0


def _frames(seconds):
    return round(seconds * FPS)


def _media(tmp_path):
    """The media the plan names, on disk.

    The plan validator refuses a plan that names a file that is not
    there, so a plan under test needs its footage to exist.
    """
    footage = tmp_path / "footage"
    footage.mkdir(exist_ok=True)
    overlays = tmp_path / "overlays"
    overlays.mkdir(exist_ok=True)
    for name in ("a.mov", "b.mov", "c.mov"):
        (footage / name).write_bytes(b"\x00" * 32)
    (overlays / "cap.mov").write_bytes(b"\x00" * 32)
    return {"footage": str(footage), "overlays": str(overlays)}


def _plan(media, picture=2, captions=False, audio=True):
    clips = [
        {"label": "a", "source_file": f"{media['footage']}/a.mov",
         "source_in": 4 / FPS, "source_out": 62 / FPS,
         "timeline_in": 0.0, "timeline_out": 62 / FPS},
        {"label": "b", "source_file": f"{media['footage']}/b.mov",
         "source_in": 151 / FPS, "source_out": 211 / FPS,
         "timeline_in": 62 / FPS, "timeline_out": 124 / FPS},
    ][:picture]
    tracks = {"V1": {"label": "A-Roll", "clips": clips}}
    if audio:
        tracks["A1"] = {"label": "Speech",
                        "clips": [dict(clip) for clip in clips]}
    document = {
        "project": {"name": "Test", "resolution": [1080, 1920],
                    "frame_rate": FPS, "duration_seconds": 124 / FPS},
        "tracks": tracks,
        "subtitles": [],
        "_spine_blocks": [],
    }
    if captions:
        document["subtitle_overlay"] = {"segments": [
            {"overlay_path": f"{media['overlays']}/cap.mov",
             "timeline_start": 0.0, "timeline_end": 30 / FPS}]}
    return document


def _built(media, picture=2, captions=False, speech=True, drift=None,
           speech_source=None, sync_offset=None):
    """The built timeline's own record, in the serializer's shape."""
    clips = [
        {"name": "a", "record_in": 0, "record_out": 62,
         "source_in_frame": 4, "source_out_frame": 62,
         "file_path": f"{media['footage']}/a.mov"},
        {"name": "b", "record_in": 62, "record_out": 124,
         "source_in_frame": 151, "source_out_frame": 211,
         "file_path": f"{media['footage']}/b.mov"},
    ][:picture]
    if drift is not None:
        clips[1]["record_in"] = 62 + drift
        clips[1]["record_out"] = 124 + drift
    tracks = [{"type": "video", "index": 1, "name": "A-Roll", "clips": clips}]
    if speech:
        speech_clips = [dict(clip) for clip in clips]
        if speech_source is not None:
            speech_clips[0]["source_in_frame"] = speech_source
        for clip in speech_clips:
            clip["audio"] = {"AudioSyncOffset": sync_offset or 0}
        tracks.append({"type": "audio", "index": 1, "name": "Speech",
                       "clips": speech_clips})
    if captions:
        tracks.append({"type": "video", "index": 2, "name": "Captions",
                       "clips": [
                           {"name": "cap", "record_in": 0, "record_out": 30,
                            "source_in_frame": 0, "source_out_frame": 30,
                            "file_path": f"{media['overlays']}/cap.mov"}]})
    return {"metadata": {"name": "Reel 01", "fps": FPS}, "tracks": tracks}


def _verify(tmp_path, monkeypatch, expectation):
    _stub_file_checks(monkeypatch)
    export = tmp_path / "export.mp4"
    export.write_bytes(b"\x00" * 16)
    return ev.verify_export(str(export), expectation)


# ── The enumeration ──


def test_the_criteria_are_the_punch_lists_eight():
    """The capability answers the punch list's section 6, item for item."""
    assert ev.CRITERION_IDS == (
        "QA-001", "QA-002", "QA-003", "QA-004",
        "QA-005", "QA-006", "QA-007", "QA-008")
    titles = {criterion.id: criterion.title for criterion in ev.CRITERIA}
    assert "clip and track structure" in titles["QA-001"]
    assert "timing, source ranges, media identity" in titles["QA-002"]
    assert "black frames, frozen frames" in titles["QA-003"]
    assert "frame rate, dimensions, duration, and" in titles["QA-004"]
    assert "subtitle/caption timing and presence" in titles["QA-005"]
    assert "placement and" in titles["QA-006"]
    assert "loudness, peaks, clipping, and channel" in titles["QA-007"]
    assert "export identity and provenance" in titles["QA-008"]


# ── QA-001: structure ──


def test_a_clip_the_build_dropped_fails_the_structure_criterion(
        tmp_path, monkeypatch):
    """The defect: the plan declared two clips and the build laid one.
    The criterion names the clip that is missing."""
    media = _media(tmp_path)
    report = _verify(tmp_path, monkeypatch, ev.ExportExpectation(
        plan=_plan(media), built=_built(media, picture=1)))
    result = report.result_for("QA-001")
    assert result.verdict == ev.FAIL
    assert "not on the timeline" in result.detail
    assert report.passed is False


def test_a_build_that_carries_the_plan_passes_the_structure_criterion(
        tmp_path, monkeypatch):
    """The everyday case: the build carries every planned clip."""
    media = _media(tmp_path)
    report = _verify(tmp_path, monkeypatch, ev.ExportExpectation(
        plan=_plan(media), built=_built(media)))
    assert report.result_for("QA-001").verdict == ev.PASS


def test_a_plan_that_is_not_structurally_valid_fails_the_criterion(
        tmp_path, monkeypatch):
    """The defect: the accepted plan itself does not hold together, so
    nothing built from it can be right."""
    media = _media(tmp_path)
    plan = _plan(media)
    plan["tracks"]["V1"]["clips"].append(
        dict(plan["tracks"]["V1"]["clips"][0]))
    report = _verify(tmp_path, monkeypatch, ev.ExportExpectation(
        plan=plan, built=_built(media)))
    result = report.result_for("QA-001")
    assert result.verdict == ev.FAIL
    assert "not structurally valid" in result.detail


# ── QA-002: timing, source ranges, identity, linked audio ──


def test_a_clip_that_plays_in_the_wrong_place_fails_the_timing_criterion(
        tmp_path, monkeypatch):
    """The defect: the clip is there and it plays 0.2s away from where
    the plan put it. The criterion says how far, not merely that it is
    wrong."""
    media = _media(tmp_path)
    report = _verify(tmp_path, monkeypatch, ev.ExportExpectation(
        plan=_plan(media), built=_built(media, drift=6)))
    result = report.result_for("QA-002")
    assert result.verdict == ev.FAIL
    assert "away from where the plan put it" in result.detail


def test_a_speech_clip_that_plays_a_different_part_of_its_source_fails(
        tmp_path, monkeypatch):
    """The defect: the speech beside the picture plays another part of
    the recording, so the words and the mouth do not agree."""
    media = _media(tmp_path)
    report = _verify(tmp_path, monkeypatch, ev.ExportExpectation(
        plan=_plan(media), built=_built(media, speech_source=999)))
    result = report.result_for("QA-002")
    assert result.verdict == ev.FAIL
    assert "different part" in result.detail


def test_a_speech_clip_carrying_a_sync_offset_fails(
        tmp_path, monkeypatch):
    """The defect: the speech clip is offset from the picture beside it
    by construction."""
    media = _media(tmp_path)
    report = _verify(tmp_path, monkeypatch, ev.ExportExpectation(
        plan=_plan(media), built=_built(media, sync_offset=12)))
    result = report.result_for("QA-002")
    assert result.verdict == ev.FAIL
    assert "sync offset" in result.detail


def test_a_build_that_agrees_with_the_plan_passes_the_timing_criterion(
        tmp_path, monkeypatch):
    """The everyday case: every clip lands where the plan put it, plays
    the part it declared, and sits in sync with the picture."""
    media = _media(tmp_path)
    report = _verify(tmp_path, monkeypatch, ev.ExportExpectation(
        plan=_plan(media), built=_built(media),
        source_frame_rates={f"{media['footage']}/a.mov": 30.0, f"{media['footage']}/b.mov": 30.0}))
    assert report.result_for("QA-002").verdict == ev.PASS


# ── QA-003: holes and overlaps ──


def test_a_hole_in_the_picture_fails_the_gap_check(tmp_path, monkeypatch):
    """The defect: a stretch of the timeline carries nothing."""
    media = _media(tmp_path)
    built = _built(media)
    built["tracks"][0]["clips"][1]["record_in"] = 90
    built["tracks"][0]["clips"][1]["record_out"] = 150
    built["tracks"][1]["clips"][1]["record_in"] = 90
    built["tracks"][1]["clips"][1]["record_out"] = 150
    report = _verify(tmp_path, monkeypatch, ev.ExportExpectation(
        plan=_plan(media), built=built))
    result = report.result_for("QA-003")
    assert result.verdict == ev.FAIL
    assert "gap" in result.detail


def test_a_b_roll_clip_covering_a_gap_is_not_a_hole(tmp_path, monkeypatch):
    """The case that used to fail a correct render: V1 has a hole and
    V2 covers it, so the picture is continuous.  A V1-only tiling check
    refused exactly this (the round3 snapshot carried four)."""
    media = _media(tmp_path)
    plan = {
        "project": {"name": "Test", "resolution": [1080, 1920],
                    "frame_rate": FPS, "duration_seconds": 124 / FPS},
        "tracks": {
            "V1": {"label": "A-Roll", "clips": [
                {"label": "a", "source_file": f"{media['footage']}/a.mov",
                 "source_in": 4 / FPS, "source_out": 31 / FPS,
                 "timeline_in": 0.0, "timeline_out": 30 / FPS},
                {"label": "b", "source_file": f"{media['footage']}/b.mov",
                 "source_in": 151 / FPS, "source_out": 211 / FPS,
                 "timeline_in": 60 / FPS, "timeline_out": 120 / FPS}]},
            "V2": {"label": "B-Roll", "clips": [
                {"label": "cutaway", "source_file": f"{media['footage']}/c.mov",
                 "source_in": 34 / FPS, "source_out": 61 / FPS,
                 "timeline_in": 30 / FPS, "timeline_out": 60 / FPS}]},
        },
        "subtitles": [],
        "_spine_blocks": [],
    }
    built = {
        "metadata": {"name": "Reel 01", "fps": FPS},
        "tracks": [
            {"type": "video", "index": 1, "name": "A-Roll", "clips": [
                {"name": "a", "record_in": 0, "record_out": 30,
                 "source_in_frame": 4, "source_out_frame": 31,
                 "file_path": f"{media['footage']}/a.mov"},
                {"name": "b", "record_in": 60, "record_out": 120,
                 "source_in_frame": 151, "source_out_frame": 211,
                 "file_path": f"{media['footage']}/b.mov"}]},
            {"type": "video", "index": 2, "name": "B-Roll", "clips": [
                {"name": "cutaway", "record_in": 30, "record_out": 60,
                 "source_in_frame": 34, "source_out_frame": 61,
                 "file_path": f"{media['footage']}/c.mov"}]},
        ],
    }
    comparison = ev._compare_plan_and_build(plan, built, FPS)
    gaps, overlaps = ev._coverage_problems(comparison)
    assert gaps == []
    assert overlaps == []


# ── QA-005: captions ──


def test_a_caption_the_build_never_carried_fails_the_presence_check(
        tmp_path, monkeypatch):
    """The defect: the plan declared a caption and nothing on the
    timeline carries it."""
    media = _media(tmp_path)
    report = _verify(tmp_path, monkeypatch, ev.ExportExpectation(
        plan=_plan(media, captions=True),
        built=_built(media, captions=False)))
    result = report.result_for("QA-005")
    assert result.verdict == ev.FAIL
    assert "never reached the timeline" in result.detail


def test_a_caption_the_build_carried_passes_the_presence_check(
        tmp_path, monkeypatch):
    """The everyday case: the caption is on its own row, on the
    timeline, over the window the plan declared."""
    media = _media(tmp_path)
    report = _verify(tmp_path, monkeypatch, ev.ExportExpectation(
        plan=_plan(media, captions=True),
        built=_built(media, captions=True)))
    assert report.result_for("QA-005").verdict == ev.PASS


def test_a_project_with_no_captions_has_no_caption_criterion(
        tmp_path, monkeypatch):
    """A plan with no captions has nothing to verify, and says so
    rather than reading as a caption check that passed."""
    media = _media(tmp_path)
    report = _verify(tmp_path, monkeypatch, ev.ExportExpectation(
        plan=_plan(media), built=_built(media)))
    assert report.result_for("QA-005").verdict == ev.NOT_APPLICABLE


# ── The whole thing ──


def test_an_export_nobody_could_check_never_reads_as_verified(
        tmp_path, monkeypatch):
    """The defect this capability exists to prevent: the evidence was
    missing, and the export was called good anyway."""
    media = _media(tmp_path)
    report = _verify(tmp_path, monkeypatch, ev.ExportExpectation(
        plan=_plan(media), built=None))
    assert report.passed is False
    assert report.result_for("QA-001").verdict == ev.UNKNOWN
    assert report.result_for("QA-002").verdict == ev.UNKNOWN
    assert {result.criterion for result in report.unknowns()} >= {
        "QA-001", "QA-002"}


def test_an_absent_export_refuses_rather_than_failing(tmp_path):
    """An absent file is not a failing export; it is nothing to verify,
    and a refusal says so."""
    with pytest.raises(ev.ExportVerificationRefused):
        ev.verify_export(str(tmp_path / "nothing.mp4"), ev.ExportExpectation())


def test_every_criterion_names_its_reader(tmp_path, monkeypatch):
    """A finding nobody reads is a finding that will not be fixed, so
    every criterion names who acts on it."""
    media = _media(tmp_path)
    report = _verify(tmp_path, monkeypatch, ev.ExportExpectation(
        plan=_plan(media), built=_built(media)))
    for result in report.results:
        assert result.reader
        assert result.detail
