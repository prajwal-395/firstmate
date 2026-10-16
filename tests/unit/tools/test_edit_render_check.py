from library.tools import edit_render_check as erc


def test_mechanical_report_passes_on_matching_render():
    report = erc.mechanical_report(
        {"lufs": -14.0, "true_peak_dbtp": -1.5, "width": 1920,
         "height": 1080, "fps": 25.0, "duration_seconds": 10.0,
         "audio_streams": 1, "black_ratio": 0.0, "frozen_ratio": 0.0},
        {"width": 1920, "height": 1080, "fps": 25.0,
         "duration_seconds": 10.0})
    assert report["mechanically_correct"]
    assert all(check["status"] == "pass"
               for check in report["checks"])


def test_mechanical_report_fails_loudness_and_peak_separately():
    report = erc.mechanical_report(
        {"lufs": -26.0, "true_peak_dbtp": 4.4, "width": 1920,
         "height": 1080, "fps": 25.0, "duration_seconds": 10.0,
         "audio_streams": 1},
        {"width": 1920, "height": 1080, "fps": 25.0,
         "duration_seconds": 10.0})
    assert not report["mechanically_correct"]
    by_property = {check["property"]: check["status"]
                   for check in report["checks"]}
    assert by_property["loudness"] == "fail"
    assert by_property["true_peak"] == "fail"
    assert by_property["audio_stream"] == "pass"


def test_unmeasured_is_neither_pass_nor_fail():
    report = erc.mechanical_report({}, {})
    assert report["mechanically_correct"]
    assert all(check["status"] == "unmeasured"
               for check in report["checks"])


def test_storytelling_covers_required_moments():
    candidate = {"start_seconds": 0.0, "end_seconds": 30.0,
                 "exchange": {"gap_seconds": 1.6}}
    moments = [{"moment_id": "answer", "start_seconds": 3.0,
                "end_seconds": 4.2}]
    report = erc.storytelling_report(
        candidate, moments, require_complete_exchange=True)
    assert report == {"story_satisfied": True, "gaps": []}


def test_storytelling_gap_names_the_missing_moment():
    candidate = {"start_seconds": 0.0, "end_seconds": 2.0}
    moments = [{"moment_id": "answer", "start_seconds": 10.0,
                "end_seconds": 20.0}]
    report = erc.storytelling_report(
        candidate, moments, require_complete_exchange=True)
    assert not report["story_satisfied"]
    assert any("answer" in gap for gap in report["gaps"])
    assert any("question/answer" in gap for gap in report["gaps"])


def test_mechanical_correctness_never_implies_story():
    mechanical = erc.mechanical_report(
        {"lufs": -14.0, "true_peak_dbtp": -1.5, "width": 1920,
         "height": 1080, "fps": 25.0, "duration_seconds": 10.0,
         "audio_streams": 1, "black_ratio": 0.0, "frozen_ratio": 0.0},
        {"width": 1920, "height": 1080, "fps": 25.0,
         "duration_seconds": 10.0})
    story = erc.storytelling_report(
        {"start_seconds": 0.0, "end_seconds": 10.0},
        [{"moment_id": "answer", "start_seconds": 50.0,
          "end_seconds": 60.0}])
    assert mechanical["mechanically_correct"]
    assert not story["story_satisfied"]
