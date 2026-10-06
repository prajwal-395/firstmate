"""The correction loop's iteration and verdict discipline.

These pin the behaviour the generic coordinator creates: a loop that keeps
failing stops at `max_iterations` and says so, rather than retrying forever.
"""

from __future__ import annotations

from library.tools.correction_loop import (
    VERDICT_MAX_ITERATIONS,
    VERDICT_PASS,
    CorrectionLoop,
)


def test_a_loop_that_never_passes_stops_at_max_iterations_and_says_so():
    # measure always fails, correct always returns a (no-op) correction.
    state = {"calls": 0}

    def measure():
        state["calls"] += 1
        return {"passed": False, "defect": "still wrong"}

    def correct(_measurement):
        return {"noop": True}

    def apply(_correction):
        pass

    loop = CorrectionLoop(measure, correct, apply, max_iterations=3)
    result = loop.run()

    assert not result.passed
    assert result.verdict == VERDICT_MAX_ITERATIONS
    assert result.iteration_count == 3
    assert all(it.outcome == "corrected" for it in result.iterations)
    assert "still failing after 3 correction(s)" in result.detail


def test_a_loop_that_passes_after_a_correction_records_both_iterations():
    state = {"failures": 1}

    def measure():
        if state["failures"] > 0:
            state["failures"] -= 1
            return {"passed": False, "defect": "wrong"}
        return {"passed": True, "defect": None}

    loop = CorrectionLoop(measure, lambda m: {"fix": True},
                          lambda c: None, max_iterations=3)
    result = loop.run()

    assert result.passed
    assert result.verdict == VERDICT_PASS
    assert result.iteration_count == 2
    assert [it.outcome for it in result.iterations] == ["corrected", "pass"]
