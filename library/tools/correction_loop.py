"""correction_loop.py - the close-the-loop coordinator.

The evaluation track's foundation.  Ren's verification stack can already
MEASURE a render - `render_qa` gates technical QA, `measure_caption_obscuring`
intersects caption boxes against detected faces, `measure_music_curve` fits
the bed against the render, `audio_verify.verify_mix` checks the mix against
intent.  What none of them do is CLOSE THE LOOP: detect a defect, compute a
correction, apply it, re-measure, and iterate until the render passes or the
loop names why it cannot.  A measured finding with no correction path is a
report, not a loop.

This module is the generic coordinator.  A loop is three caller-supplied
callables over one piece of mutable state:

    measure()  -> dict with at least {"passed": bool, ...domain data...}
    correct(measurement) -> correction dict, or None when the defect cannot
                           be corrected mechanically (the loop then names why)
    apply(correction)     -> mutates the state the next measure() reads

`CorrectionLoop.run` iterates measure -> correct -> apply -> re-measure up to
`max_iterations`, records every iteration (what was measured, what was
applied, the outcome), and returns a `CorrectionResult` carrying the verdict
and the full iteration history.  The verdict is one of:

    "pass"                    - a measurement passed
    "cannot_correct"          - correct() returned None; the reason is recorded
    "max_iterations_exceeded" - still failing after max_iterations corrections

The coordinator knows nothing about captions, audio or edits.  Each domain
supplies its own measure/correct/apply and reuses this loop, so the iteration
and verdict discipline is written once.  The graphics loop
(`caption_correction.py`) is the first consumer; the audio and edit loops
follow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


#: The verdict strings a `CorrectionResult` carries.
VERDICT_PASS = "pass"
VERDICT_CANNOT_CORRECT = "cannot_correct"
VERDICT_MAX_ITERATIONS = "max_iterations_exceeded"


@dataclass
class CorrectionIteration:
    """One measure -> correct -> apply cycle.

    `correction` is None on the iteration that passed (nothing to apply) and
    on a `cannot_correct` iteration.  `outcome` is one of "pass",
    "corrected", "cannot_correct".
    """

    iteration: int
    measurement: Dict[str, Any]
    correction: Optional[Dict[str, Any]]
    outcome: str


@dataclass
class CorrectionResult:
    """The whole loop: a verdict and every iteration that led to it."""

    passed: bool
    verdict: str
    iterations: List[CorrectionIteration] = field(default_factory=list)
    detail: str = ""

    @property
    def iteration_count(self) -> int:
        return len(self.iterations)

    def corrections_applied(self) -> List[Dict[str, Any]]:
        """The corrections that were applied, in order."""
        return [it.correction for it in self.iterations
                if it.correction is not None]

    def as_dict(self) -> dict:
        """The loop as a record: verdict plus the full iteration history."""
        return {
            "passed": self.passed,
            "verdict": self.verdict,
            "detail": self.detail,
            "iteration_count": self.iteration_count,
            "iterations": [
                {
                    "iteration": it.iteration,
                    "outcome": it.outcome,
                    "measurement": it.measurement,
                    "correction": it.correction,
                }
                for it in self.iterations
            ],
        }


class CorrectionLoop:
    """Iterate measure -> correct -> apply -> re-measure until pass or a stop.

    `measure` returns a dict that must carry at least a boolean ``passed``.
    When it passes, the loop stops with verdict "pass".  When it fails,
    `correct` is called with the measurement; returning a correction dict
    applies it and re-measures, returning None stops the loop with verdict
    "cannot_correct" and the reason `correct` put under the measurement's
    ``cannot_correct_reason`` key (or a default).  A loop that is still
    failing after `max_iterations` corrections stops with verdict
    "max_iterations_exceeded".
    """

    def __init__(self,
                 measure: Callable[[], Dict[str, Any]],
                 correct: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]],
                 apply: Callable[[Dict[str, Any]], None],
                 max_iterations: int = 3,
                 name: str = "correction"):
        self._measure = measure
        self._correct = correct
        self._apply = apply
        self.max_iterations = max(1, int(max_iterations))
        self.name = name

    def run(self) -> CorrectionResult:
        iterations: List[CorrectionIteration] = []

        for attempt in range(1, self.max_iterations + 1):
            measurement = self._measure()

            if measurement.get("passed"):
                iterations.append(CorrectionIteration(
                    iteration=attempt,
                    measurement=_summarise(measurement),
                    correction=None,
                    outcome="pass",
                ))
                return CorrectionResult(
                    passed=True,
                    verdict=VERDICT_PASS,
                    iterations=iterations,
                    detail=_pass_detail(measurement),
                )

            correction = self._correct(measurement)
            if correction is None:
                reason = str(measurement.get("cannot_correct_reason")
                            or "no mechanical correction exists for this defect")
                iterations.append(CorrectionIteration(
                    iteration=attempt,
                    measurement=_summarise(measurement),
                    correction=None,
                    outcome="cannot_correct",
                ))
                return CorrectionResult(
                    passed=False,
                    verdict=VERDICT_CANNOT_CORRECT,
                    iterations=iterations,
                    detail=reason,
                )

            self._apply(correction)
            iterations.append(CorrectionIteration(
                iteration=attempt,
                measurement=_summarise(measurement),
                correction=correction,
                outcome="corrected",
            ))

        return CorrectionResult(
            passed=False,
            verdict=VERDICT_MAX_ITERATIONS,
            iterations=iterations,
            detail=(f"still failing after {self.max_iterations} correction(s); "
                    f"the last measurement is recorded in the iterations"),
        )


def _summarise(measurement: Dict[str, Any]) -> Dict[str, Any]:
    """The measurement as recorded, minus any bulky raw arrays.

    A measurement may carry large raw data (every face box, every window);
    the iteration record keeps it, because the loop's value is the history,
    but callers building a report can project it down.
    """
    return dict(measurement)


def _pass_detail(measurement: Dict[str, Any]) -> str:
    if measurement.get("detail"):
        return str(measurement["detail"])
    return "the measurement passed"
