"""The intent receipt: one end-to-end proof that the render satisfies the
creative direction (gap E12, scaffold).

The gap this closes
-------------------
Verification is fragmented. The intent checks each measure one creative
goal - heard-vs-planned, punch-in face, caption quality, B-roll
correspondence - and each reports its verdict on its own row of
`exports/qa_report.json`. Nothing composes them into a single answer to
the captain's question: "does this render satisfy the creative
direction?" A render can pass every structural gate and still fail a
creative goal, and the failure is scattered across rows nobody reads
together.

This module is the umbrella. It composes the intent-check verdicts into
one receipt: a verdict per creative goal, and an overall verdict that
names what failed. It reads the findings through the ONE reader
(`qa_findings`), the same reader the run summary and step 3.03 use, so
it cannot develop a private opinion about which findings matter.

The scaffold composes E1a-E1d. The full E12 drives the receipt from a
declared intent document and adds the remaining creative goals (music
behavior, grade delivery); the pattern proven here is the composition,
not the list of goals.

**This reports and does not gate.** The receipt composes verdicts; it
does not assign the run status. Promoting an intent check to a gate is
the boolean in its own module (`reel_hearing.GATES`,
`render_qa.PUNCH_IN_FACE_GATES`, `render_qa.CAPTION_QUALITY_GATES`,
`broll_correspondence.BROLL_CORRESPONDENCE_GATES`), all False. The
receipt is the proof document, not a gate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from library.tools import qa_findings, reel_hearing


PASS = "pass"
"""Every check that ran for this goal passed."""

FAIL = "fail"
"""At least one check for this goal failed - the goal is named."""

NOT_MEASURED = "not_measured"
"""No check ran for this goal. NOT a pass: an absent measurement is not
a verdict of fine (AGENTS.md 10.3)."""


@dataclass(frozen=True)
class GoalSpec:
    """One creative goal and the metrics that measure it."""

    goal: str
    question: str
    metrics: tuple


GOALS: tuple = (
    GoalSpec(
        "heard_vs_planned",
        "does the render say what the plan planned?",
        (reel_hearing.SCRIPT_METRIC, reel_hearing.DRIFT_METRIC,
         reel_hearing.COVERAGE_METRIC, reel_hearing.FIT_METRIC,
         reel_hearing.PAIRING_METRIC),
    ),
    GoalSpec(
        "punch_in_face",
        "did the punch-in keep the face?",
        ("punch_in_face",),
    ),
    GoalSpec(
        "caption_quality",
        "are captions legible and not obscuring?",
        ("caption_legibility", "caption_obscuring"),
    ),
    GoalSpec(
        "broll_correspondence",
        "do cutaways illustrate the speech?",
        ("broll_correspondence",),
    ),
)
"""The creative goals the scaffold composes, and the metrics that measure
each. One enumeration: a metric belongs to exactly one goal, and a goal
with no metrics in the report is NOT_MEASURED rather than passed."""


@dataclass(frozen=True)
class GoalReceipt:
    """One creative goal, composed from its checks."""

    goal: str
    question: str
    verdict: str
    findings: List[qa_findings.Finding] = field(default_factory=list)

    @property
    def failed_findings(self) -> List[qa_findings.Finding]:
        return [f for f in self.findings
                if f.verdict == qa_findings.FAILING]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "goal": self.goal,
            "question": self.question,
            "verdict": self.verdict,
            "checks": [f.as_dict() for f in self.findings],
        }


@dataclass
class IntentReceipt:
    """The end-to-end intent proof: every creative goal, composed."""

    goals: List[GoalReceipt] = field(default_factory=list)
    source: str = qa_findings.SOURCE_NONE
    source_detail: str = "no render QA has been recorded for this project"

    @property
    def measured(self) -> List[GoalReceipt]:
        return [g for g in self.goals if g.verdict != NOT_MEASURED]

    @property
    def goals_measured(self) -> int:
        return len(self.measured)

    @property
    def failures(self) -> List[GoalReceipt]:
        return [g for g in self.goals if g.verdict == FAIL]

    @property
    def clean(self) -> bool:
        """Whether every measured goal passed.

        A receipt with NO measured goals is NOT clean: a render no intent
        check measured is a render whose intent is unproven, not a render
        that satisfied it.
        """
        return bool(self.measured) and not self.failures

    def as_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "source_detail": self.source_detail,
            "clean": self.clean,
            "goals_measured": len(self.measured),
            "goals_total": len(self.goals),
            "failures": [g.goal for g in self.failures],
            "goals": [g.as_dict() for g in self.goals],
        }


def compose_receipt(qa: qa_findings.QAFindings) -> IntentReceipt:
    """Compose the intent receipt from the read QA findings.

    Reads through the ONE reader (`qa_findings`), never a private opinion
    about which findings matter. A goal whose metrics are absent from the
    report is NOT_MEASURED, never passed: the receipt is the proof
    document, and a proof with no measurements is not a proof.
    """
    by_metric: Dict[str, List[qa_findings.Finding]] = {}
    for finding in qa.findings:
        by_metric.setdefault(finding.metric, []).append(finding)

    goals: List[GoalReceipt] = []
    for spec in GOALS:
        findings: List[qa_findings.Finding] = []
        for metric in spec.metrics:
            findings.extend(by_metric.get(metric, []))
        if not findings:
            verdict = NOT_MEASURED
        elif any(f.verdict == qa_findings.FAILING for f in findings):
            verdict = FAIL
        else:
            verdict = PASS
        goals.append(GoalReceipt(spec.goal, spec.question, verdict, findings))
    return IntentReceipt(goals, qa.source, qa.source_detail)


def summary_lines(receipt: IntentReceipt) -> List[str]:
    """The run summary's intent-receipt block, as lines.

    Written as lines rather than printed so the same text is testable
    without capturing a whole pipeline run - the same reason
    `qa_findings.summary_lines` is written as lines.
    """
    if not receipt.measured:
        return ["  Intent receipt: no creative-intent checks ran "
                "(step 6.02 writes exports/qa_report.json)"]

    measured = len(receipt.measured)
    total = len(receipt.goals)
    failed = len(receipt.failures)
    lines = [f"  Intent receipt: {measured} of {total} creative goals "
             f"measured, {failed} failed"]
    lines.append(f"    source: {receipt.source_detail}")

    for goal in receipt.goals:
        if goal.verdict == NOT_MEASURED:
            lines.append(f"    · {goal.goal}: not measured")
            continue
        if goal.verdict == PASS:
            lines.append(f"    · {goal.goal}: pass")
            continue
        for finding in goal.failed_findings:
            lines.append(f"    ✗ {goal.goal}: {finding.detail}")

    lines.append("    Reading this does not change the run status; each "
                 "check's own module decides what gates.")
    return lines
