"""The independent editorial assessment, and what a judge is allowed to see.

The punch list asks for editorial assessments recorded without exposing
the expected choices to the agent. Two halves, and they are separate on
purpose:

- the AGENT must not see the answer key, so it works the request out
  rather than recognising it. `briefs.assert_no_leak` holds that half.
- the JUDGE must not see the answer key either, or the assessment is an
  echo of the evaluator's plan rather than a reading of the film. That
  is this module: `judge_input` is the ONE construction of what a judge
  is shown, and it is built from the brief's public half and the file's
  own digest, evidence and length. Nothing else.

A panel of judges is read, not averaged. Two judges that disagree about
a criterion have not established it, and the disagreement is reported
where it happened rather than resolved by averaging two opinions into a
half-credit.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from library.tools.editorial_acceptance import briefs

PANEL_SCHEMA = "ren-editorial-assessment-panel/1"

#: What a judge is shown about the delivered file. The digest, the
#: evidence a judge can look at, and how long it is. Nothing about the
#: plan that produced it.
RENDER_KEYS: tuple[str, ...] = ("sha256", "evidence", "duration_seconds")


class JudgementError(ValueError):
    """The assessment cannot be taken, or is not independent."""


def assert_independent(brief: Mapping[str, Any]) -> None:
    """Refuse a brief that carries the answer key into the judge's hands.

    The judge's view is built from the public half of a brief, so a brief
    handed to a judge must not carry any of the evaluator's fields.
    """
    offending = sorted(key for key in briefs.ORACLE_KEYS if key in brief)
    if offending:
        raise JudgementError(
            "the brief handed to a judge carries the answer key: "
            + ", ".join(offending))
    unknown = sorted(key for key in brief if key not in briefs.PUBLIC_KEYS)
    if unknown:
        raise JudgementError(
            "the brief handed to a judge carries fields the public half "
            "does not have: " + ", ".join(unknown))


def judge_input(brief: Mapping[str, Any],
                render: Mapping[str, Any]) -> dict[str, Any]:
    """Everything a judge is shown, and nothing else.

    The brief's public half, plus the delivered file's digest, its
    evidence and its length. The answer key is not in here, and neither
    is anything about the plan that produced the file.
    """
    assert_independent(brief)
    missing = [key for key in RENDER_KEYS if key not in render]
    if missing:
        raise JudgementError(
            "the render handed to a judge is missing: " + ", ".join(missing))
    return {
        "brief": {
            "instruction": brief["instruction"],
            "project_state": brief["project_state"],
            "acceptance_criteria": brief["acceptance_criteria"],
        },
        "render": {key: render[key] for key in RENDER_KEYS},
    }


def assess(*, task_id: str, brief: Mapping[str, Any],
           render: Mapping[str, Any],
           judge: Callable[[Mapping[str, Any], Mapping[str, Any]],
                           Mapping[str, Any]]) -> dict[str, Any]:
    """One judge's reading of one delivered file, against one brief.

    The scorer is the engine's own (`library/tools/editorial_brief_scorer`),
    so the shape of a criterion judgment is the one the live harness
    already grades. What this adds is the independence guarantee: the
    judge is handed `judge_input` and nothing else, whatever the scorer
    passes in.
    """
    from library.tools import editorial_brief_scorer

    payload = judge_input(brief, render)

    def _scored_judge(_brief: Mapping[str, Any],
                      _render: Mapping[str, Any]) -> Mapping[str, Any]:
        return judge(payload["brief"], payload["render"])

    record = editorial_brief_scorer.score_brief(
        request_id=task_id, brief=brief, render=render, judge=_scored_judge)
    record["independence"] = {
        "judge_saw": sorted(payload["brief"]) + [
            f"render.{key}" for key in RENDER_KEYS],
        "judge_did_not_see": list(briefs.ORACLE_KEYS),
    }
    return record


def score_recorded(*, task_id: str, brief: Mapping[str, Any],
                   render: Mapping[str, Any],
                   criteria: Sequence[Mapping[str, Any]],
                   judge_name: str) -> dict[str, Any]:
    """A RECORDED judgment, scored by the engine's scorer.

    The per-criterion rows are the judge's work: what it saw in the
    delivered file and what it concluded. The COUNTS, the score and the
    verdict are the scorer's work, and they were being typed by hand -
    which is how a record came to say `passed 3, failed 4` over rows
    that were three passes, three failures and four unmeasured, with a
    score of `None` where the scorer computes a number.

    So the rows are kept and the counts are recomputed, here, by the
    same scorer the live harness grades with. The judge is not re-run:
    it already read the file, and re-running it would be a second
    reading, not a scoring.
    """
    if not str(judge_name or "").strip():
        raise JudgementError("a recorded judgment carries no judge to name")
    rows = [dict(row) for row in criteria if isinstance(row, Mapping)]

    def _replay(_brief: Mapping[str, Any],
                _render: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"judge": judge_name, "criteria": rows}

    return assess(task_id=task_id, brief=brief, render=render,
                  judge=_replay)


def _criterion_status(record: Mapping[str, Any], criterion_id: str) -> str:
    editorial = record.get("editorial")
    editorial = editorial if isinstance(editorial, Mapping) else {}
    for row in editorial.get("criteria") or []:
        if isinstance(row, Mapping) and row.get("id") == criterion_id:
            return str(row.get("status") or "not_measured")
    return "not_measured"


def panel(assessments: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Two or more independent readings, compared criterion by criterion.

    A criterion is established only when the judges agree about it. One
    that says pass and one that says fail have established nothing, and
    the panel says so instead of choosing a side.
    """
    records = [record for record in assessments
               if isinstance(record, Mapping)]
    judges = [str(record.get("judge") or "unnamed") for record in records]
    criterion_ids: list[str] = []
    for record in records:
        editorial = record.get("editorial")
        editorial = editorial if isinstance(editorial, Mapping) else {}
        for row in editorial.get("criteria") or []:
            if isinstance(row, Mapping) and row.get("id") not in criterion_ids:
                criterion_ids.append(str(row.get("id")))
    per_criterion: dict[str, Any] = {}
    disagreements: list[dict[str, Any]] = []
    for criterion_id in criterion_ids:
        statuses = [_criterion_status(record, criterion_id)
                    for record in records]
        unique = set(statuses)
        if len(unique) == 1:
            status = statuses[0]
            agreement = True
        else:
            status = "disagreement"
            agreement = False
            disagreements.append({"id": criterion_id, "statuses": statuses})
        per_criterion[criterion_id] = {
            "status": status,
            "statuses": statuses,
            "agreement": agreement,
        }
    agreed = sum(1 for row in per_criterion.values() if row["agreement"])
    return {
        "schema": PANEL_SCHEMA,
        "judges": judges,
        "judge_count": len(records),
        "criteria": per_criterion,
        "disagreements": disagreements,
        "agreement_rate": (agreed / len(per_criterion)
                           if per_criterion else None),
        "verdict": panel_verdict(per_criterion),
    }


def panel_verdict(per_criterion: Mapping[str, Any]) -> str:
    """The panel's reading of the whole brief.

    A failure the judges all agree on is a failure. A criterion they
    disagree about is not established, and the brief is not established
    either - which is not the same as a failure, and is not a pass.
    """
    statuses = {str(row.get("status")) for row in per_criterion.values()}
    if not statuses:
        return "not_measured"
    if "fail" in statuses:
        return "fail"
    if "disagreement" in statuses:
        return "disagreement"
    if "not_measured" in statuses:
        return "not_measured"
    return "pass"
