"""Load-aware scheduling: the dispatch matrix the captain asked to tune.

Defects caught: a quiet machine serialising like a busy one (latency
for nothing), a loaded machine fanning out like a quiet one (the
captain's editor stutters), pytest -n multiplying jobs instead of
dividing them, and an unreadable machine running hot instead of
conservative.
"""

from library.tools.local_ci import load


def _snap(load1=0.0, cpus=10, mem=16.0, heavy=(), blind=False):
    return {"load1": load1, "cpus": cpus, "mem_free_gb": mem,
            "heavy": list(heavy), "blind": blind,
            "disk_free_gb": 100.0}


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
