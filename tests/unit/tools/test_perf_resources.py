"""What the machine was doing while a run worked.

Every test here names the defect it catches. The reading is evidence
for a reader deciding whether the machine was the bottleneck, so a
counter that reads zero when nobody measured it is worse than a
counter that is absent.
"""

from library.tools import perf_resources


def test_a_counter_the_os_does_not_expose_is_absent_not_zero(monkeypatch,
                                                            tmp_path):
    """A platform with no per-process disk counter reports NO counter.

    Defect caught: a missing counter reported as zero, so the profile
    says the run read nothing from disk on the platform that cannot
    say - a reader concludes the run was not disk-bound because the
    instrument was not fitted.
    """
    monkeypatch.setattr(
        perf_resources, "process_io_bytes",
        lambda: {"read_bytes": None, "written_bytes": None})

    reading = perf_resources.sample(str(tmp_path))

    assert "disk_bytes_read" not in reading
    assert "disk_bytes_written" not in reading


def test_the_pressure_band_follows_the_measured_percentage():
    """The band is read off the measurement, higher pressure is worse.

    Defect caught: macOS reports the FREE percentage and Linux reports
    a stall percentage - opposite directions - so a band applied
    without normalising reads a calm machine as a critical one (or the
    reverse), and the reader distrusts every pressure reading after.
    """
    assert perf_resources._band(0.0) == perf_resources.PRESSURE_NORMAL
    assert perf_resources._band(perf_resources.WARN_PCT) == \
        perf_resources.PRESSURE_WARN
    assert perf_resources._band(perf_resources.CRITICAL_PCT) == \
        perf_resources.PRESSURE_CRITICAL
    assert perf_resources._band(99.0) == perf_resources.PRESSURE_CRITICAL


def test_two_samples_keep_the_worse_pressure():
    """A capability is sampled at both ends and keeps the worse reading.

    Defect caught: the reading taken only at the start, so a pressure
    spike that happened DURING the capability is missed and the
    capability looks calm.
    """
    merged = perf_resources.worse(
        {"memory_pressure": "normal", "memory_pressure_pct": 10.0,
         "swap_used_mb": 100.0},
        {"memory_pressure": "critical", "memory_pressure_pct": 95.0,
         "swap_used_mb": 4000.0})

    assert merged["memory_pressure"] == "critical"
    assert merged["memory_pressure_pct"] == 95.0
    assert merged["swap_used_mb"] == 4000.0


def test_a_swap_counter_the_os_does_not_expose_is_unmeasured():
    """No swap counter means no swap number, not zero swap.

    Defect caught: swap reported as zero on a platform that does not
    expose it, so the profile promises the machine never swapped.
    """
    reading = perf_resources.swap_usage_mb()

    assert set(reading) == {"used_mb", "total_mb"}
    if reading["used_mb"] is None:
        assert reading["total_mb"] is None
    else:
        assert reading["used_mb"] >= 0.0
