"""The suite refuses real scheduler memory reservations, fast.

On 2026-10-16 the pooled full suite (pool 186) hung past the
15-minute faulthandler dump in
`test_lazy_semantic_model_holds_ram_but_not_gpu_during_host_wait`:
a real 15 GB reservation taken through `heavy_work_reservation`
waited on machine memory that never freed. `tests/conftest.py` now
fails any unit or scenario test that reaches a real RAM-declaring
`Scheduler.acquire` instead of queueing; this pins both halves -
the refusal, and that ordinary (RAM-free) admissions still pass
through to the real scheduler.
"""

import pytest

from library.tools import resource_scheduler


def test_real_scheduler_memory_reservation_fails_fast(tmp_path):
    """A RAM-declaring acquire refuses instead of waiting on memory."""
    from _pytest.outcomes import Failed

    scheduler = resource_scheduler.Scheduler(tmp_path / "heavy-work.lock")
    with pytest.raises(Failed, match="fake admission"):
        scheduler.acquire("guard probe", {"ram_gb": 15},
                          announce=lambda line: None)
    assert scheduler.jobs() == []


def test_ram_free_admission_still_reaches_the_scheduler(tmp_path):
    """The guard only refuses RAM demands; plain work admits."""
    scheduler = resource_scheduler.Scheduler(tmp_path / "heavy-work.lock")
    token = scheduler.acquire("plain work", {},
                              announce=lambda line: None)
    try:
        assert scheduler.running_demand(token) == {}
    finally:
        scheduler.release(token)
    assert scheduler.jobs() == []


def test_genuine_admission_tests_opt_out_explicitly():
    """Every real RAM reservation in the suite carries the marker.

    The scheduler's own admission tests prove the gate they stand
    behind, so each one opts out with `@pytest.mark.real_scheduler_admission`.
    If this set grows, each addition names the admission behavior it
    proves; anything else must use a fake admission.
    """
    import pathlib

    path = (pathlib.Path(__file__).resolve().parents[1] / "unit"
            / "context" / "test_concurrency.py")
    lines = path.read_text(encoding="utf-8").splitlines()
    marked = set()
    for index, line in enumerate(lines):
        if line.strip() != "@pytest.mark.real_scheduler_admission":
            continue
        for following in lines[index + 1:index + 4]:
            stripped = following.strip()
            if stripped.startswith("def test_"):
                marked.add(stripped[len("def "):].split("(")[0])
                break
    assert marked == {
        "test_measured_gemma_reservation_waits_while_plain_work_admits",
        "test_impossible_memory_reservation_is_refused_immediately",
        "test_small_waiter_backfills_past_large_waiter_that_does_not_fit",
        "test_model_memory_reservation_admits_only_bounded_nested_inference",
        "test_memory_session_folds_and_rejects_exclusive_build_profiles",
        "test_model_memory_reservation_folds_into_a_covering_enclosing_grant",
    }, f"real-scheduler-admission opt-outs changed: {sorted(marked)}"
