import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

def test_missing_deterministic_output_fails_the_step():
    """A step reporting success while measuring nothing is the exact defect
    class this repo exists to prevent. If the deterministic half emits
    nothing or malformed JSON, post_bridge must read it as a failure."""
    post_bridge_path = Path("library/steps/step_6_02_validate_output/post_bridge.py")
    
    # Input with NO deterministic_validation
    input_data = {
        "validation_result": {"status": "pass", "summary": "LLM says fine"}
    }
    
    result = subprocess.run(
        [sys.executable, str(post_bridge_path)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
        check=True
    )
    
    output = json.loads(result.stdout)
    v = output["validation_result"]
    assert v["status"] == "fail"
    assert v["distribution_ready"] is False
    assert "Deterministic validation failed" in v["summary"]

def test_missing_llm_output_fails_the_step():
    post_bridge_path = Path("library/steps/step_6_02_validate_output/post_bridge.py")
    input_data = {
        "deterministic_validation": {"status": "pass", "summary": "Det says fine"}
    }
    result = subprocess.run(
        [sys.executable, str(post_bridge_path)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
        check=True
    )
    output = json.loads(result.stdout)
    v = output["validation_result"]
    assert v["status"] == "fail"
    assert v["distribution_ready"] is False

def test_both_pass_yields_pass():
    post_bridge_path = Path("library/steps/step_6_02_validate_output/post_bridge.py")
    input_data = {
        "deterministic_validation": {"status": "pass", "summary": "Det fine"},
        "validation_result": {"status": "pass", "summary": "LLM fine"}
    }
    result = subprocess.run(
        [sys.executable, str(post_bridge_path)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
        check=True
    )
    output = json.loads(result.stdout)
    v = output["validation_result"]
    assert v["status"] == "pass"
    assert v["distribution_ready"] is True

def test_undetermined_yields_undetermined_and_not_ready():
    post_bridge_path = Path("library/steps/step_6_02_validate_output/post_bridge.py")
    input_data = {
        "deterministic_validation": {"status": "pass", "summary": "Det fine"},
        "validation_result": {"status": "undetermined", "summary": "LLM says undetermined"}
    }
    result = subprocess.run(
        [sys.executable, str(post_bridge_path)],
        input=json.dumps(input_data),
        capture_output=True,
        text=True,
        check=True
    )
    output = json.loads(result.stdout)
    v = output["validation_result"]
    assert v["status"] == "undetermined"
    assert v["distribution_ready"] is False


def test_qa_toolkit_crash_fails_validation_loudly(tmp_path):
    """A QA toolkit that did not run must not read as a passing one.

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


def test_subtitle_qa_crash_is_unvalidated_not_clean(tmp_path):
    """A subtitle pass that never ran must not read as a clean one.

    Same fail-closed shape as the render_qa path beside it: on
    exception the bridge appends a FAILING result, so the verdict says
    UNVALIDATED instead of reporting pass with zero measurements.
    """
    from library.steps.step_6_02_validate_output import bridge as validate

    video = tmp_path / "master.mp4"
    video.write_bytes(b"\x00" * 200_000)
    manifest = {"project": {"resolution": [1080, 1920], "frame_rate": 30.0,
                            "duration_seconds": 30.0},
                "tracks": {},
                "subtitles": [{"timeline_start": 0.0, "timeline_end": 1.0,
                               "text": "hello"}]}

    def _boom(*args, **kwargs):
        raise RuntimeError("aligner exploded")

    with patch.object(validate, "run_full_render_qa", return_value=[]), \
         patch.object(validate, "verify_subtitle_timing", side_effect=_boom):
        result = validate.validate_output({"output_path": str(video)},
                                          manifest)

    assert result["status"] == "fail"
    assert result["distribution_ready"] is False
    assert result["checks"]["technical"]["pass"] is False
    assert any("did not run" in i and "aligner exploded" in i
               for i in result["all_issues"])
    report = result["qa_report"]
    assert any(r["metric"] == "subtitle_qa" and r["passed"] is False
               for r in report)
