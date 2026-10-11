"""Load-aware scheduling: the dispatch matrix the captain asked to tune.

Defects caught: a quiet machine serialising like a busy one (latency
for nothing), a loaded machine fanning out like a quiet one (the
captain's editor stutters), pytest -n multiplying jobs instead of
dividing them, and an unreadable machine running hot instead of
conservative.
"""

from library.tools.local_ci import load


def _snap(load1=0.0, cpus=10, mem=16.0, heavy=(), blind=False,
          swap=0.0, cpu_busy=None, pageout=0.0, swapout=0.0):
    snap = {"load1": load1, "cpus": cpus, "mem_free_gb": mem,
            "heavy": list(heavy), "blind": blind,
            "disk_free_gb": 100.0, "swap_used_gb": swap,
            "pageout_per_sec": pageout, "swapout_per_sec": swapout}
    if cpu_busy is not None:
        snap["cpu_busy"] = cpu_busy
    return snap


def test_a_quiet_machine_runs_more_with_a_short_window():
    plan = load.targets(_snap(load1=1.0))
    assert plan["tier1_jobs"] == load.TIER1_MAX
    assert plan["window"] == load.WINDOW_MIN


def test_a_loaded_machine_serialises_with_a_long_window():
    plan = load.targets(_snap(load1=19.0))  # the October batch norm
    assert plan["tier1_jobs"] == 1
    assert plan["window"] == load.WINDOW_MAX
    assert plan["pytest_n"] == 2  # one job may fan out


def test_many_jobs_narrow_pytest():
    plan = load.targets(_snap(load1=0.5))
    assert plan["tier1_jobs"] == load.TIER1_MAX
    assert plan["pytest_n"] == 1  # fan-out multiplies jobs, not runs


def test_memory_pressure_floors_to_one_job():
    plan = load.targets(_snap(load1=0.5, mem=1.2))
    assert plan["tier1_jobs"] == 1
    assert plan["window"] == load.WINDOW_MAX


def test_an_unreadable_machine_runs_conservative():
    plan = load.targets(_snap(load1=0.0, mem=None, blind=True))
    assert plan["tier1_jobs"] == 1
    assert plan["window"] == load.WINDOW_MAX


def test_heavy_foreground_denies_full_fanout():
    plan = load.targets(_snap(load1=0.5, heavy=["Resolve"]))
    assert plan["tier1_jobs"] < load.TIER1_MAX


def test_bounds_always_hold():
    for jobs_load in (0.0, 5.0, 10.0, 40.0):
        plan = load.targets(_snap(load1=jobs_load))
        assert 1 <= plan["tier1_jobs"] <= load.TIER1_MAX
        assert 1 <= plan["pytest_n"] <= 2
        assert load.WINDOW_MIN <= plan["window"] <= load.WINDOW_MAX


def test_measure_returns_a_sane_snapshot():
    snap = load.measure()
    assert snap["cpus"] >= 1
    assert snap["load1"] >= 0.0
    assert isinstance(snap["heavy"], list)
    plan = load.targets(snap)  # the live machine always yields a plan
    assert 1 <= plan["tier1_jobs"] <= load.TIER1_MAX


def test_idle_agent_lanes_do_not_serialise_the_machine():
    # ~15 agent lanes pin the 1-minute load at 20-25 on 10 cores while
    # the CPUs are mostly idle. Busy cores govern: 2 busy cores of 10
    # still fans out, even with an inflated load average.
    plan = load.targets(_snap(load1=22.0, cpu_busy=2.0))
    assert plan["tier1_jobs"] > 1
    assert plan["headroom_source"] == "cpu"


def test_a_busy_cpu_still_serialises():
    plan = load.targets(_snap(load1=1.0, cpu_busy=9.5))
    assert plan["tier1_jobs"] == 1
    assert plan["window"] == load.WINDOW_MAX


def test_paging_pressure_caps_then_floors():
    assert load.targets(
        _snap(cpu_busy=1.0, swapout=50.0))["tier1_jobs"] <= 2
    assert load.targets(
        _snap(cpu_busy=1.0, pageout=800.0))["tier1_jobs"] <= 2
    capped = load.targets(_snap(cpu_busy=1.0, swapout=300.0))
    assert capped["tier1_jobs"] == 1
    assert capped["window"] == load.WINDOW_MAX
    capped = load.targets(_snap(cpu_busy=1.0, pageout=5000.0))
    assert capped["tier1_jobs"] == 1
    assert capped["window"] == load.WINDOW_MAX


def test_stale_swap_used_never_gates_dispatch():
    # 2026-10-10: macOS keeps swap used high (~12 GB) long after
    # pressure clears (~6 GB free, no paging). Swap used is
    # observability only: a quiet machine with stale swap still fans out.
    plan = load.targets(_snap(cpu_busy=1.0, swap=12.0))
    assert plan["tier1_jobs"] == load.TIER1_MAX
    assert plan["window"] == load.WINDOW_MIN


def test_paging_rates_between_samples_are_bounded():
    assert load._rates_between(None, 10.0, 100, 100) == (0.0, 0.0)
    assert load._rates_between((0.0, 100, 100), 10.0, 200, 150) == (
        10.0, 5.0)
    # Backwards counters, stale intervals and missing counters are not
    # pressure - they are clock or sample artefacts.
    assert load._rates_between((10.0, 200, 150), 5.0, 300, 200) == (
        0.0, 0.0)
    assert load._rates_between((0.0, 100, 100), 500.0, 10**9, 10**9) == (
        0.0, 0.0)
    assert load._rates_between((0.0, 300, 200), 10.0, 100, 100) == (
        0.0, 0.0)


def test_load_average_survives_as_a_fallback():
    # Old snapshots without a CPU sample still yield the old matrix.
    assert load.targets(_snap(load1=1.0))["tier1_jobs"] == load.TIER1_MAX
    assert load.targets(_snap(load1=19.0))["tier1_jobs"] == 1
