"""Budgeting from measurements: scaled timeouts and cold-start fallbacks.

Defects caught: a Tier-2 full-suite gate killed at a fixed timeout
under agent load at 98% complete (the whole run wasted), and a
budget computed from nothing (zero or missing measurements) that
pins the timeout at the floor while claiming to be measured.
"""

from library.tools.local_ci import config, history


def test_scaled_timeout_multiplies_the_measurement():
    assert history.tier2_timeout_seconds(700.0, 2.0) == 1400


def test_scaled_timeout_never_drops_below_the_floor():
    assert history.tier2_timeout_seconds(10.0, 2.0) == (
        config.TIER2_TIMEOUT_FLOOR_SECONDS)


def test_scaled_timeout_never_exceeds_the_ceiling():
    assert history.tier2_timeout_seconds(100_000.0, 2.0) == (
        config.TIER2_TIMEOUT_CEILING_SECONDS)


def test_an_unmeasured_suite_times_out_at_the_floor():
    assert history.tier2_timeout_seconds(0.0, 2.0) == (
        config.TIER2_TIMEOUT_FLOOR_SECONDS)
    assert history.tier2_timeout_seconds(-5.0, 2.0) == (
        config.TIER2_TIMEOUT_FLOOR_SECONDS)
    assert history.tier2_timeout_seconds(700.0, 0.0) == (
        config.TIER2_TIMEOUT_FLOOR_SECONDS)


def test_tier2_wall_keeps_the_longest_measured_run(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "tier2-100.log").write_text(
        "====== 9 failed, 7869 passed in 698.90s (0:11:38) ======\n",
        encoding="utf-8")
    (logs / "tier2-200.log").write_text(
        "====== 7900 passed in 44.91s ======\n",
        encoding="utf-8")
    # The quick red run must not shrink the bound below the suite's
    # demonstrated cost: the maximum sizes the timeout.
    assert history.tier2_wall_seconds(str(tmp_path)) == 698.90


def test_tier2_wall_is_none_without_a_parseable_elapsed(tmp_path):
    assert history.tier2_wall_seconds(str(tmp_path)) is None
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "tier2-100.log").write_text("no pytest summary here\n",
                                        encoding="utf-8")
    assert history.tier2_wall_seconds(str(tmp_path)) is None


def test_full_suite_wall_falls_back_through_telemetry_to_constant(
        monkeypatch, tmp_path):
    monkeypatch.setattr(history, "tier2_wall_seconds", lambda _d: None)
    monkeypatch.setattr(history, "test_durations",
                        lambda: {"a.py": 100.0, "b.py": 100.0})
    assert history.full_suite_wall_seconds(str(tmp_path)) == 100.0
    monkeypatch.setattr(history, "test_durations", lambda: {})
    assert history.full_suite_wall_seconds(str(tmp_path)) == (
        config.TIER2_MEASURED_FALLBACK_WALL_SECONDS)


def test_default_timeout_scales_the_measured_wall(monkeypatch, tmp_path):
    monkeypatch.setattr(history, "full_suite_wall_seconds",
                        lambda _d: 700.0)
    assert history.default_tier2_timeout(str(tmp_path)) == 1400


def test_failure_rates_are_empty_without_a_recorded_hook(
        monkeypatch, tmp_path):
    monkeypatch.delenv("VEP_TEST_FAILURE_RATES_FILE", raising=False)
    assert history.failure_rates() == {}
    bad = tmp_path / "rates.json"
    bad.write_text("not json", encoding="utf-8")
    assert history.failure_rates(str(bad)) == {}
    wrong = tmp_path / "wrong.json"
    wrong.write_text("[1, 2]", encoding="utf-8")
    assert history.failure_rates(str(wrong)) == {}


def test_failure_rates_keep_only_unit_rates(tmp_path):
    rates = tmp_path / "rates.json"
    rates.write_text(
        '{"tests/a.py": 0.4, "tests/b.py": 2.0, "tests/c.py": "x"}',
        encoding="utf-8")
    assert history.failure_rates(str(rates)) == {"tests/a.py": 0.4}


def test_durations_cold_start_is_empty_not_an_error(monkeypatch):
    monkeypatch.setattr(history, "_timing_reader", lambda: None)
    assert history.test_durations() == {}


def test_estimate_keeps_the_measurement():
    assert history.estimate_duration(
        "tests/unit/x/test_a.py", {"tests/unit/x/test_a.py": 7.5}) == 7.5


def test_estimate_prices_unknown_at_the_subsystem_median():
    durations = {"tests/unit/resolve/test_old.py": 10.0,
                 "tests/unit/resolve/test_older.py": 20.0,
                 "tests/scenarios/test_slow.py": 500.0}
    # Subsystem median (10, 20) -> 15, not the 20.0 suite median and
    # not the flat default.
    assert history.estimate_duration(
        "tests/unit/resolve/test_new.py", durations) == 15.0


def test_estimate_falls_back_to_the_suite_median():
    durations = {"tests/unit/a/test_a.py": 4.0,
                 "tests/unit/b/test_b.py": 8.0}
    assert history.estimate_duration(
        "tests/unit/empty/test_new.py", durations) == 6.0


def test_estimate_falls_back_to_the_flat_default_without_telemetry():
    assert history.estimate_duration(
        "tests/unit/x/test_new.py", {}) == (
            config.TIER1_UNKNOWN_FILE_ESTIMATE_SECONDS)


def test_estimate_ignores_unusable_measurements():
    # Zero, negative and non-numeric entries are not measurements: a
    # zero-cost file would bypass the budget entirely.
    durations = {"tests/unit/x/test_zero.py": 0.0,
                 "tests/unit/x/test_neg.py": -3.0,
                 "tests/unit/x/test_nan.py": "soon"}
    assert history.estimate_duration(
        "tests/unit/x/test_new.py", durations) == (
            config.TIER1_UNKNOWN_FILE_ESTIMATE_SECONDS)
    assert history.estimate_duration(
        "tests/unit/x/test_zero.py", durations) == (
            config.TIER1_UNKNOWN_FILE_ESTIMATE_SECONDS)
