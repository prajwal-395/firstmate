"""Which of the hearing pass's numbers are dials, and which are not.

The captain asked for the pass to be configurable. The risk in
answering that is not being too restrictive - it is making a MEASURED
property look like a preference, so that the first noisy report is
"fixed" by raising the floor until nothing is found. These tests pin
both halves: a chosen dial really moves, and a measured one that moves
is RECORDED as having moved.

No test reaches a real project: every project here is built under
`tmp_path`.
"""
import pytest
import yaml

from library.tools import hearing_settings, reel_hearing


def _project(tmp_path, block=None):
    """A project whose `project.yaml` declares what the test needs."""
    folder = tmp_path / "project"
    folder.mkdir(exist_ok=True)
    config = {"name": "test", "pipeline": {}}
    if block is not None:
        config["pipeline"]["reel_hearing"] = block
    (folder / "project.yaml").write_text(yaml.safe_dump(config),
                                         encoding="utf-8")
    return str(folder)


# ── 1. The split is declared, and every row justifies itself ─────────

def test_every_dial_says_what_its_value_rests_on():
    """A dial with no basis is a constant with better paperwork.

    Refused at import; this pins that the guard can fail.
    """
    hearing_settings.assert_dials_are_well_formed()
    for name, basis in (("no basis", ""), ("blank basis", "   ")):
        broken = hearing_settings.Dial(name, hearing_settings.MEASURED, 1.0,
                                       "does a thing", basis)
        original = hearing_settings.DIALS
        try:
            hearing_settings.DIALS = original + (broken,)
            with pytest.raises(hearing_settings.MalformedHearingDeclaration):
                hearing_settings.assert_dials_are_well_formed()
        finally:
            hearing_settings.DIALS = original


def test_the_drift_floor_is_MEASURED_and_the_other_two_are_CHOSEN():
    """The whole answer to "configure it a bit more", as an enumeration.

    The floor is a property of how two transcribers disagree, measured
    over 6,983 words. The run length and the coverage bar are
    judgements about what is worth a reader's attention.
    """
    kinds = {dial.name: dial.kind for dial in hearing_settings.DIALS}
    assert kinds["drift_noise_floor_seconds"] == hearing_settings.MEASURED
    assert kinds["drift_run_min_words"] == hearing_settings.CHOSEN
    assert kinds["caption_coverage_floor"] == hearing_settings.CHOSEN


def test_the_defaults_are_the_pass_s_own_constants_and_not_a_second_copy():
    """A bound spelled twice drifts (AGENTS.md 10.1)."""
    by_name = {dial.name: dial.default for dial in hearing_settings.DIALS}
    assert by_name["drift_noise_floor_seconds"] is \
        reel_hearing.DRIFT_NOISE_FLOOR_SECONDS
    assert by_name["drift_run_min_words"] is reel_hearing.DRIFT_RUN_MIN_WORDS
    assert by_name["caption_coverage_floor"] is \
        reel_hearing.CAPTION_HALF_COVERED


# ── 2. Nothing asked: the measurement answers, and says so ───────────

def test_a_project_that_declares_nothing_gets_the_measurement(tmp_path):
    settings = hearing_settings.resolve(_project(tmp_path))
    assert settings.drift_noise_floor_seconds == \
        reel_hearing.DRIFT_NOISE_FLOOR_SECONDS
    assert set(settings.readings.values()) == {hearing_settings.PINNED}
    assert settings.moved_pinned == []
    assert hearing_settings.warning_lines(settings) == []


def test_no_project_at_all_is_not_an_error():
    """A one-off hearing of a loose file has no project.yaml to read."""
    assert hearing_settings.resolve(None).readings[
        "caption_coverage_floor"] == hearing_settings.PINNED


# ── 3. A CHOSEN dial really moves ────────────────────────────────────

def test_a_project_may_declare_its_own_caption_bar(tmp_path):
    folder = _project(tmp_path, {"caption_coverage_floor": 0.8})
    settings = hearing_settings.resolve(folder)
    assert settings.caption_coverage_floor == 0.8
    assert settings.readings["caption_coverage_floor"] == \
        hearing_settings.PROJECT
    assert settings.moved_pinned == []


def test_this_run_beats_the_project_and_the_project_beats_the_measurement(
        tmp_path):
    folder = _project(tmp_path, {"caption_coverage_floor": 0.8,
                                 "drift_run_min_words": 5})
    settings = hearing_settings.resolve(
        folder, {"caption_coverage_floor": 0.3})
    assert settings.caption_coverage_floor == 0.3
    assert settings.readings["caption_coverage_floor"] == hearing_settings.RUN
    assert settings.drift_run_min_words == 5
    assert settings.readings["drift_run_min_words"] == \
        hearing_settings.PROJECT


def test_a_value_of_None_is_not_an_answer(tmp_path):
    """Every CLI dial defaults to None, and None must not override."""
    settings = hearing_settings.resolve(
        _project(tmp_path), {"caption_coverage_floor": None})
    assert settings.readings["caption_coverage_floor"] == \
        hearing_settings.PINNED


# ── 4. A MEASURED dial may move, and can never move silently ─────────

def test_moving_the_measured_floor_is_RECORDED(tmp_path):
    settings = hearing_settings.resolve(
        _project(tmp_path), {"drift_noise_floor_seconds": 0.6})
    assert settings.drift_noise_floor_seconds == 0.6
    assert len(settings.moved_pinned) == 1
    row = settings.moved_pinned[0]
    assert row["dial"] == "drift_noise_floor_seconds"
    assert row["measured"] == reel_hearing.DRIFT_NOISE_FLOOR_SECONDS
    assert row["used"] == 0.6
    assert row["asked_by"] == hearing_settings.RUN
    assert "6,983 words" in row["measurement"]


def test_a_moved_measurement_is_SAID_out_loud(tmp_path):
    lines = hearing_settings.warning_lines(hearing_settings.resolve(
        _project(tmp_path), {"drift_noise_floor_seconds": 0.6}))
    assert any("MOVED" in line for line in lines)
    assert any("not comparable" in line for line in lines)


def test_moving_a_CHOSEN_dial_is_not_a_moved_measurement(tmp_path):
    settings = hearing_settings.resolve(
        _project(tmp_path), {"drift_run_min_words": 6})
    assert settings.moved_pinned == []
    lines = hearing_settings.warning_lines(settings)
    assert any("drift_run_min_words=6" in line for line in lines)
    assert not any("MOVED" in line for line in lines)


# ── 5. A declaration that cannot be acted on REFUSES ─────────────────

def test_a_block_that_is_not_a_mapping_refuses(tmp_path):
    with pytest.raises(hearing_settings.MalformedHearingDeclaration):
        hearing_settings.resolve(_project(tmp_path, ["nope"]))


def test_a_key_nothing_reads_refuses(tmp_path):
    """A preference the captain believes is in force and is not."""
    with pytest.raises(hearing_settings.MalformedHearingDeclaration) as bad:
        hearing_settings.resolve(_project(tmp_path, {"drift_floor": 0.4}))
    assert "which nothing reads" in str(bad.value)


def test_a_word_count_refuses_a_fraction(tmp_path):
    with pytest.raises(hearing_settings.MalformedHearingDeclaration) as bad:
        hearing_settings.resolve(_project(tmp_path),
                                 {"drift_run_min_words": 2.7})
    assert "whole number" in str(bad.value)


def test_a_dial_at_or_below_zero_refuses(tmp_path):
    """A floor of zero makes every word a finding."""
    for value in (0, -1):
        with pytest.raises(hearing_settings.MalformedHearingDeclaration):
            hearing_settings.resolve(_project(tmp_path),
                                     {"drift_noise_floor_seconds": value})


def test_a_boolean_is_not_a_number(tmp_path):
    with pytest.raises(hearing_settings.MalformedHearingDeclaration):
        hearing_settings.resolve(_project(tmp_path),
                                 {"caption_coverage_floor": True})


def test_a_run_asking_for_a_dial_that_does_not_exist_refuses(tmp_path):
    with pytest.raises(hearing_settings.MalformedHearingDeclaration) as bad:
        hearing_settings.resolve(_project(tmp_path), {"loudness": 1.0})
    assert "not a hearing dial" in str(bad.value)


# ── 6. Turning a check off is never the same as it being silent ──────

def test_a_declined_check_must_be_one_this_pass_makes(tmp_path):
    with pytest.raises(hearing_settings.MalformedHearingDeclaration) as bad:
        hearing_settings.resolve(_project(tmp_path), None, ["not_a_metric"])
    assert "does not measure" in str(bad.value)


def test_a_project_may_decline_a_check(tmp_path):
    folder = _project(tmp_path,
                      {"checks_declined": [reel_hearing.COVERAGE_METRIC]})
    settings = hearing_settings.resolve(folder)
    assert settings.runs(reel_hearing.COVERAGE_METRIC) is False
    assert settings.runs(reel_hearing.DRIFT_METRIC) is True


def test_every_metric_the_pass_makes_can_be_declined_by_name():
    """The choice set IS the producer's enumeration, not a copy."""
    for metric in reel_hearing.METRICS:
        assert hearing_settings.resolve(None, None, [metric]).runs(
            metric) is False
