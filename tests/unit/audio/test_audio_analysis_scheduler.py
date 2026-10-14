from library.tools.audio_analysis_scheduler import (
    LANES,
    compare_exhaustive_control,
    execute_audio_plan,
    plan_audio_analysis,
)


def test_requests_and_activity_schedule_only_supported_audio_windows():
    plan = plan_audio_analysis(
        100,
        requests=[{"lane": "transcript", "start": 20, "end": 25},
                  {"lane": "diarization", "start": 20, "end": 25}],
        activity={"acoustic": [{"start": 60, "end": 62}],
                  "audio_classification": [{"start": 60, "end": 62}]},
        activity_coverage={"transcript": [{"start": 0, "end": 30}],
                           "diarization": [{"start": 0, "end": 30}],
                           "acoustic": [{"start": 0, "end": 100}],
                           "audio_classification": [{"start": 0, "end": 100}]},
        padding_seconds=1,
    )

    assert plan["lanes"]["transcript"]["scheduled"] == [
        {"start": 19.0, "end": 26.0}]
    assert plan["lanes"]["diarization"]["scheduled"] == [
        {"start": 19.0, "end": 26.0}]
    assert plan["lanes"]["acoustic"]["scheduled"] == [
        {"start": 59.0, "end": 63.0}]
    assert plan["lanes"]["audio_classification"]["scheduled"] == [
        {"start": 59.0, "end": 63.0}]
    assert plan["lanes"]["transcript"]["coverage_fraction"] == 0.3


def test_unmeasured_intervals_remain_unresolved_and_exhaustive_control_reports_recall():
    plan = plan_audio_analysis(
        10,
        requests=[{"lane": "transcript", "start": 2, "end": 4}],
        activity={"acoustic": [{"start": 7, "end": 8}]},
        activity_coverage={"transcript": [{"start": 0, "end": 5}],
                           "acoustic": [{"start": 0, "end": 10}]},
    )
    comparison = compare_exhaustive_control(plan)

    assert plan["lanes"]["transcript"]["unresolved"] == [
        {"start": 5.0, "end": 10.0,
         "reason": "no request or measured activity coverage"}]
    assert plan["lanes"]["diarization"]["coverage_fraction"] == 0
    assert plan["lanes"]["diarization"]["unresolved"]
    assert comparison["control_seconds"] == 40
    assert comparison["adaptive_seconds"] == 3
    assert comparison["lanes"]["transcript"]["required_coverage_fraction"] == 1
    assert comparison["lanes"]["diarization"]["required_coverage_fraction"] is None
    assert comparison["work_reduction_fraction"] == 0.925


def test_full_source_request_matches_exhaustive_control():
    plan = plan_audio_analysis(12, requests=[{"lane": lane} for lane in LANES])
    comparison = compare_exhaustive_control(plan)

    assert plan["scheduled_seconds"] == 48
    assert comparison["adaptive_seconds"] == comparison["control_seconds"] == 48
    assert comparison["saved_seconds"] == 0
    assert all(row["required_coverage_fraction"] == 1
               for row in comparison["lanes"].values())


def test_invalid_activity_and_requests_do_not_create_coverage():
    plan = plan_audio_analysis(
        5,
        requests=[{"lane": "invented", "start": 0, "end": 3}],
        activity={"acoustic": [{"start": 4, "end": 2}]},
    )

    assert plan["invalid_requests"]
    assert plan["lanes"]["acoustic"]["scheduled"] == []
    assert plan["lanes"]["acoustic"]["coverage_fraction"] == 0
    assert plan["lanes"]["acoustic"]["unresolved"]


def test_executor_runs_serially_and_failed_or_unavailable_work_stays_unresolved():
    plan = plan_audio_analysis(
        10, requests=[{"lane": "transcript", "start": 1, "end": 2},
                      {"lane": "transcript", "start": 4, "end": 5},
                      {"lane": "diarization", "start": 6, "end": 7}])
    calls = []

    def transcribe(*, start, end):
        calls.append((start, end))
        if start == 4:
            raise RuntimeError("model refused")
        return "ok"

    report = execute_audio_plan(plan, {"transcript": transcribe})

    assert calls == [(1.0, 2.0), (4.0, 5.0)]
    assert report["lanes"]["transcript"]["coverage"] == [
        {"start": 1.0, "end": 2.0}]
    assert report["lanes"]["transcript"]["coverage_fraction"] == 0.1
    assert any("model refused" in row["reason"] for row in report["unresolved_areas"])
    assert any(row["reason"] == "analyzer unavailable"
               for row in report["unresolved_areas"])
    assert report["exhaustive_control"]["adaptive_seconds"] == 3
