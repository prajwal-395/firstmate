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


def test_transition_off_its_cut_fails_validation(tmp_path):
    """A planned transition floating off its cut point fails the gate.

    The executable contract behind dropping the transition rows from
    the prompt: the arithmetic - does `cut_point_timeline` coincide
    with a V1 clip boundary - is held by the bridge, so the model no
    longer needs the rows to hold it.
    """
    from library.steps.step_6_02_validate_output import bridge as validate

    video = tmp_path / "master.mp4"
    video.write_bytes(b"\x00" * 200_000)
    manifest = _geometry_manifest(
        clips=[_v1_clip("a", 0.0, 2.0), _v1_clip("b", 2.0, 5.0)],
        transitions=[
            {"transition_id": "t1", "transition_type": "hard_cut",
             "cut_point_timeline": 2.0},
            {"transition_id": "t2", "transition_type": "glow",
             "cut_point_timeline": 3.0, "duration_frames": 12},
        ],
    )

    with patch.object(validate, "run_full_render_qa", return_value=[]):
        result = validate.validate_output({"output_path": str(video)},
                                          manifest)

    check = result["checks"]["transitions_at_seams"]
    assert check["pass"] is False
    assert any("t2" in i and "3.000" in i for i in check["issues"])
    assert result["checks"]["v1_tiling"]["pass"] is True
    assert result["status"] == "fail"
    assert result["distribution_ready"] is False


def test_v1_gap_fails_validation(tmp_path):
    """A V1 track that does not tile fails the gate.

    The executable contract behind dropping the track rows from the
    prompt: consecutive V1 clips either abut or the render carries
    black, and that is arithmetic the bridge holds.
    """
    from library.steps.step_6_02_validate_output import bridge as validate

    video = tmp_path / "master.mp4"
    video.write_bytes(b"\x00" * 200_000)
    manifest = _geometry_manifest(
        clips=[_v1_clip("a", 0.0, 2.0), _v1_clip("b", 2.5, 5.0)],
        transitions=[],
    )

    with patch.object(validate, "run_full_render_qa", return_value=[]):
        result = validate.validate_output({"output_path": str(video)},
                                          manifest)

    check = result["checks"]["v1_tiling"]
    assert check["pass"] is False
    assert any("0.500" in i for i in check["issues"])
    assert result["status"] == "fail"
    assert result["distribution_ready"] is False




def test_declared_black_beat_excuses_a_v1_gap(tmp_path):
    """A gap the spine validly declared as a black beat is not a failure.

    `compile_manifest` lets a declared beat through, and the render
    carries the same ruling into this gate - so a beat that survived
    compilation is not failed here after a full render.
    """
    from library.steps.step_6_02_validate_output import bridge as validate

    video = tmp_path / "master.mp4"
    video.write_bytes(b"\x00" * 200_000)
    manifest = _geometry_manifest(
        clips=[_v1_clip("a", 0.0, 2.0), _v1_clip("b", 2.5, 5.0)],
        transitions=[],
        spine_blocks=[{
            "position": 1, "block_type": "transition_slot",
            "timeline_start": 2.0, "timeline_end": 2.5,
            "intentional_black_beat": True,
            "black_beat_reason": "deliberate breath before the reveal",
        }],
    )

    with patch.object(validate, "run_full_render_qa", return_value=[]):
        result = validate.validate_output({"output_path": str(video)},
                                          manifest)

    assert result["checks"]["v1_tiling"]["pass"] is True
    assert result["status"] == "pass"


def test_broll_cover_excuses_a_v1_gap(tmp_path):
    """A V1 gap a B-roll cutaway covers end to end is not a hole.

    Measured on the round3 snapshot: its V1 carries four multi-second
    gaps and every one is exactly a V2 clip, the combined picture is
    continuous, and the render is correct. A V1-only tiling check
    fails that render, so covering counts - partially covered does
    not.
    """
    from library.steps.step_6_02_validate_output import bridge as validate

    video = tmp_path / "master.mp4"
    video.write_bytes(b"\x00" * 200_000)
    manifest = _geometry_manifest(
        clips=[_v1_clip("a", 0.0, 2.0), _v1_clip("b", 2.5, 5.0)],
        transitions=[],
        v2_clips=[_v1_clip("cover", 2.0, 2.5)],
    )

    with patch.object(validate, "run_full_render_qa", return_value=[]):
        result = validate.validate_output({"output_path": str(video)},
                                          manifest)

    assert result["checks"]["v1_tiling"]["pass"] is True
    assert result["status"] == "pass"

    manifest["tracks"]["V2"]["clips"] = [_v1_clip("short", 2.1, 2.4)]
    with patch.object(validate, "run_full_render_qa", return_value=[]):
        result = validate.validate_output({"output_path": str(video)},
                                          manifest)

    assert result["checks"]["v1_tiling"]["pass"] is False
    assert result["status"] == "fail"


def test_cut_rows_carry_no_seating_obligation(tmp_path):
    """A hard-cut row far from any edge gates nothing.

    CUT types draw nothing (`transition_vocabulary.CUT_TYPES`), so the
    row's cut point is an aspiration recorded beside the edge the mesh
    actually cut on - the 001 snapshots carry a word-end+beat hard cut
    0.3s off its edge on renders that are correct. Holding no-op rows
    would fail those renders.
    """
    from library.steps.step_6_02_validate_output import bridge as validate

    video = tmp_path / "master.mp4"
    video.write_bytes(b"\x00" * 200_000)
    manifest = _geometry_manifest(
        clips=[_v1_clip("a", 0.0, 2.0), _v1_clip("b", 2.0, 5.0)],
        transitions=[
            {"transition_id": "t1", "transition_type": "hard_cut",
             "cut_point_timeline": 3.5},
        ],
    )

    with patch.object(validate, "run_full_render_qa", return_value=[]):
        result = validate.validate_output({"output_path": str(video)},
                                          manifest)

    assert result["checks"]["transitions_at_seams"]["pass"] is True
    assert result["status"] == "pass"


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
