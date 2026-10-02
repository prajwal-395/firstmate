import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

POST_BRIDGE = Path("library/steps/step_6_02_validate_output/post_bridge.py")


def test_a_missing_or_undetermined_half_is_never_distribution_ready():
    """A step reporting success while measuring nothing is the defect
    class this repo exists to prevent: a missing deterministic or LLM
    half reads as `fail`, an undetermined verdict stays `undetermined`,
    and none is distribution-ready."""
    cases = [
        ({"validation_result": {"status": "pass", "summary": "LLM fine"}},
         "fail", "Deterministic validation failed"),
        ({"deterministic_validation": {"status": "pass", "summary": "Det"}},
         "fail", None),
        ({"deterministic_validation": {"status": "pass", "summary": "Det"},
          "validation_result": {"status": "undetermined", "summary": "?"}},
         "undetermined", None),
    ]
    for input_data, status, summary in cases:
        result = subprocess.run(
            [sys.executable, str(POST_BRIDGE)], input=json.dumps(input_data),
            capture_output=True, encoding="utf-8", check=True)
        v = json.loads(result.stdout)["validation_result"]
        assert v["status"] == status, input_data
        assert v["distribution_ready"] is False, input_data
        if summary:
            assert summary in v["summary"]


def test_a_qa_pass_that_crashed_fails_validation_loudly(tmp_path):
    """A QA toolkit (render or subtitle) that did not run must not read
    as a passing one.

    Every individual `render_qa` measurement fails closed on its own
    error, so the aggregate does the same: when `run_full_render_qa`
    raises, the step reports fail with the crash named, instead of
    reporting pass with zero measurements (the file-exists check alone
    would still pass, which is exactly how an unvalidated render would
    ship as `distribution_ready: true`).
    """
    from library.steps.step_6_02_validate_output import bridge as validate

    video = tmp_path / "master.mp4"
    video.write_bytes(b"\x00" * 200_000)
    manifest = {"project": {"resolution": [1080, 1920], "frame_rate": 30.0,
                            "duration_seconds": 10.0},
                "tracks": {}, "subtitles": []}

    def _boom(*args, **kwargs):
        raise RuntimeError("probe exploded")

    with patch.object(validate, "run_full_render_qa", side_effect=_boom):
        result = validate.validate_output({"output_path": str(video)},
                                          manifest)

    assert result["status"] == "fail"
    assert result["distribution_ready"] is False
    assert result["critical_checks_passed"] is False
    assert result["checks"]["technical"]["pass"] is False
    assert any("did not run" in i and "probe exploded" in i
               for i in result["all_issues"])
    report = result["qa_report"]
    assert any(r["metric"] == "render_qa" and r["passed"] is False
               for r in report)

    # The subtitle pass fails closed the same way.
    manifest["subtitles"] = [{"timeline_start": 0.0, "timeline_end": 1.0,
                              "text": "hello"}]

    def _aligner_boom(*args, **kwargs):
        raise RuntimeError("aligner exploded")

    with patch.object(validate, "run_full_render_qa", return_value=[]), \
         patch.object(validate, "verify_subtitle_timing",
                      side_effect=_aligner_boom):
        result = validate.validate_output({"output_path": str(video)},
                                          manifest)
    assert result["status"] == "fail"
    assert result["distribution_ready"] is False
    assert result["checks"]["technical"]["pass"] is False
    assert any("did not run" in i and "aligner exploded" in i
               for i in result["all_issues"])
    assert any(r["metric"] == "subtitle_qa" and r["passed"] is False
               for r in result["qa_report"])


def test_declared_audio_delivery_targets_reach_export_measurement(tmp_path):
    """The export gate measures against the plan's exact delivery targets."""
    from library.steps.step_6_02_validate_output import bridge as validate

    video = tmp_path / "master.mp4"
    video.write_bytes(b"\x00" * 200_000)
    manifest = {
        "project": {"resolution": [1080, 1920], "frame_rate": 30.0,
                    "duration_seconds": 10.0},
        "tracks": {}, "subtitles": [],
        "audio_mix": {"delivery_lufs_target": -16.0,
                       "delivery_true_peak_ceiling_dbtp": -1.5},
    }

    with patch.object(validate, "run_full_render_qa", return_value=[]) as qa:
        validate.validate_output({"output_path": str(video)}, manifest)

    assert qa.call_args.kwargs["target_lufs"] == -16.0
    assert qa.call_args.kwargs["true_peak_ceiling"] == -1.5


def test_failing_subtitles_reach_the_verdict_without_gating_it(tmp_path):
    """Subtitle quality is measured AND read: the six subtitle metrics
    land in checks["subtitles"], all_issues and qa_report.

    Reporting-only, deliberately: whether a subtitle metric should FAIL
    a build, and at what threshold, is the captain's call, so a reel
    failing every subtitle metric still validates - but the verdict now
    SAYS so instead of carrying no subtitle key at all.
    """
    from library.steps.step_6_02_validate_output import bridge as validate

    video = tmp_path / "master.mp4"
    video.write_bytes(b"\x00" * 200_000)
    manifest = {"project": {"resolution": [1080, 1920], "frame_rate": 30.0,
                            "duration_seconds": 30.0},
                "tracks": {},
                "subtitles": [
                    {"timeline_start": 0.0, "timeline_end": 0.2,
                     "text": "Hi"},
                    {"timeline_start": 0.1, "timeline_end": 8.0,
                     "text": "x" * 400},
                    {"timeline_start": 20.0, "timeline_end": 21.0,
                     "text": "ok"},
                ]}

    with patch.object(validate, "run_full_render_qa", return_value=[]):
        result = validate.validate_output({"output_path": str(video)},
                                          manifest)

    sub = result["checks"]["subtitles"]
    assert sub["pass"] is False
    assert len(sub["issues"]) == 5
    # Visible in the verdict three ways: the check, the issue list, the
    # report. (subtitle_overflow is the sixth metric; the bridge never
    # passes total_duration, so that row cannot exist here.)
    assert any(i.startswith("[subtitles]") for i in result["all_issues"])
    metrics = {r["metric"] for r in result["qa_report"]}
    assert {"subtitle_overlap", "subtitle_too_short", "subtitle_too_long",
            "subtitle_gaps", "subtitle_read_speed"} <= metrics
    # ...but no threshold was chosen here, so nothing new gates.
    assert result["status"] == "pass"
    assert result["distribution_ready"] is True


def _validate(validate, video, manifest):
    with patch.object(validate, "run_full_render_qa", return_value=[]):
        return validate.validate_output({"output_path": str(video)}, manifest)


def test_a_drawn_transition_must_sit_on_its_cut(tmp_path):
    """A planned transition floating off its cut point fails the gate -
    the arithmetic the bridge holds so the prompt no longer carries the
    rows. CUT types draw nothing (`transition_vocabulary.CUT_TYPES`), so
    a hard-cut row far from any edge gates nothing: the 001 snapshots
    carry one 0.3s off its edge on renders that are correct."""
    from library.steps.step_6_02_validate_output import bridge as validate

    video = tmp_path / "master.mp4"
    video.write_bytes(b"\x00" * 200_000)
    clips = [_v1_clip("a", 0.0, 2.0), _v1_clip("b", 2.0, 5.0)]
    result = _validate(validate, video, _geometry_manifest(
        clips=clips,
        transitions=[
            {"transition_id": "t1", "transition_type": "hard_cut",
             "cut_point_timeline": 2.0},
            {"transition_id": "t2", "transition_type": "glow",
             "cut_point_timeline": 3.0, "duration_frames": 12},
        ],
    ))
    check = result["checks"]["transitions_at_seams"]
    assert check["pass"] is False
    assert any("t2" in i and "3.000" in i for i in check["issues"])
    assert result["checks"]["v1_tiling"]["pass"] is True
    assert result["status"] == "fail"
    assert result["distribution_ready"] is False

    result = _validate(validate, video, _geometry_manifest(
        clips=clips,
        transitions=[{"transition_id": "t1", "transition_type": "hard_cut",
                      "cut_point_timeline": 3.5}],
    ))
    assert result["checks"]["transitions_at_seams"]["pass"] is True
    assert result["status"] == "pass"


def test_a_v1_gap_fails_unless_declared_or_covered(tmp_path):
    """A V1 track that does not tile fails the gate - unless the spine
    validly declared the gap a black beat (compile_manifest lets it
    through, so the render gate must too), or a B-roll cutaway covers it
    end to end (measured on round3: four V1 gaps, each exactly a V2
    clip, a correct render). Partially covered does not count."""
    from library.steps.step_6_02_validate_output import bridge as validate

    video = tmp_path / "master.mp4"
    video.write_bytes(b"\x00" * 200_000)
    clips = [_v1_clip("a", 0.0, 2.0), _v1_clip("b", 2.5, 5.0)]

    result = _validate(validate, video,
                       _geometry_manifest(clips=clips, transitions=[]))
    check = result["checks"]["v1_tiling"]
    assert check["pass"] is False
    assert any("0.500" in i for i in check["issues"])
    assert result["status"] == "fail"
    assert result["distribution_ready"] is False

    result = _validate(validate, video, _geometry_manifest(
        clips=clips, transitions=[],
        spine_blocks=[{
            "position": 1, "block_type": "transition_slot",
            "timeline_start": 2.0, "timeline_end": 2.5,
            "intentional_black_beat": True,
            "black_beat_reason": "deliberate breath before the reveal",
        }],
    ))
    assert result["checks"]["v1_tiling"]["pass"] is True
    assert result["status"] == "pass"

    manifest = _geometry_manifest(clips=clips, transitions=[],
                                  v2_clips=[_v1_clip("cover", 2.0, 2.5)])
    result = _validate(validate, video, manifest)
    assert result["checks"]["v1_tiling"]["pass"] is True
    assert result["status"] == "pass"

    manifest["tracks"]["V2"]["clips"] = [_v1_clip("short", 2.1, 2.4)]
    result = _validate(validate, video, manifest)
    assert result["checks"]["v1_tiling"]["pass"] is False
    assert result["status"] == "fail"


def _v1_clip(label, timeline_in, timeline_out):
    return {
        "label": label,
        "source_file": "/nonexistent/source.mp4",
        "source_in": 0.0,
        "source_out": timeline_out - timeline_in,
        "timeline_in": timeline_in,
        "timeline_out": timeline_out,
    }


def _geometry_manifest(clips, transitions, spine_blocks=(), v2_clips=()):
    manifest = {
        "project": {"resolution": [1080, 1920], "frame_rate": 30.0,
                    "duration_seconds": 10.0},
        "tracks": {"V1": {"clips": clips}},
        "transitions": transitions,
        "subtitles": [],
        "_spine_blocks": list(spine_blocks),
    }
    if v2_clips:
        manifest["tracks"]["V2"] = {"clips": list(v2_clips)}
    return manifest
