"""Score a delivered edit against its actual creative brief (AXI-11/13).

The scorer sees the brief and final audiovisual evidence. It does not receive
the expected AXI operations, target IDs, or evaluator edit plan. Technical
correctness remains a separate gate in the returned result.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from statistics import mean
from typing import Any

SCHEMA = "ren-editorial-brief-score/1"
VALID_STATUSES = {"pass", "fail", "not_measured"}


def _criterion_ids(brief: Mapping[str, Any]) -> list[str]:
    criteria = brief.get("acceptance_criteria")
    if not isinstance(criteria, list) or not criteria:
        raise ValueError("brief must declare at least one acceptance criterion")
    ids = []
    for criterion in criteria:
        if not isinstance(criterion, Mapping):
            raise TypeError("acceptance criteria must be objects")
        criterion_id = criterion.get("id")
        statement = criterion.get("statement")
        if not isinstance(criterion_id, str) or not criterion_id.strip():
            raise ValueError("every acceptance criterion needs a non-empty id")
        if not isinstance(statement, str) or not statement.strip():
            raise ValueError(f"criterion {criterion_id!r} needs a statement")
        ids.append(criterion_id)
    if len(set(ids)) != len(ids):
        raise ValueError("acceptance criterion IDs must be unique")
    return ids


def _validate_judgment(expected_ids: Sequence[str],
                       judgment: Mapping[str, Any]) -> list[dict[str, Any]]:
    results = judgment.get("criteria")
    if not isinstance(results, list):
        raise TypeError("judge must return a criteria list")
    by_id: dict[str, dict[str, Any]] = {}
    for row in results:
        if not isinstance(row, Mapping):
            raise TypeError("criterion judgments must be objects")
        criterion_id = row.get("id")
        if criterion_id not in expected_ids:
            raise ValueError(f"judge returned unknown criterion {criterion_id!r}")
        if criterion_id in by_id:
            raise ValueError(f"judge duplicated criterion {criterion_id!r}")
        status = row.get("status")
        if status not in VALID_STATUSES:
            raise ValueError(f"criterion {criterion_id!r} has invalid status")
        rationale = row.get("rationale")
        evidence = row.get("evidence")
        if not isinstance(rationale, str) or not rationale.strip():
            raise ValueError(f"criterion {criterion_id!r} lacks rationale")
        if status != "not_measured" and (
                not isinstance(evidence, list) or not evidence):
            raise ValueError(f"measured criterion {criterion_id!r} lacks evidence")
        if isinstance(evidence, list):
            for item in evidence:
                if (not isinstance(item, Mapping)
                        or not isinstance(item.get("artifact"), str)
                        or not item.get("artifact")
                        or not isinstance(item.get("observation"), str)
                        or not item.get("observation")):
                    raise ValueError(
                        f"criterion {criterion_id!r} evidence needs artifact and observation")
        by_id[criterion_id] = dict(row)
    return [by_id.get(criterion_id, {
        "id": criterion_id, "status": "not_measured",
        "rationale": "the independent judge supplied no result",
        "evidence": [],
    }) for criterion_id in expected_ids]


def score_brief(*, request_id: str, brief: Mapping[str, Any],
                render: Mapping[str, Any],
                judge: Callable[[Mapping[str, Any], Mapping[str, Any]],
                                Mapping[str, Any]]) -> dict[str, Any]:
    """Return an evidence-backed editorial score and separate technical gate.

    ``render`` contains final output metadata and the judge's allowed evidence
    (frames, audio, transcript, or a combination). It must carry a SHA-256 of
    the delivered media so the score can be tied to one exact output.
    """
    if not request_id.strip():
        raise ValueError("request_id is required")
    expected_ids = _criterion_ids(brief)
    render_hash = render.get("sha256")
    if not isinstance(render_hash, str) or len(render_hash) != 64:
        raise ValueError("render must carry the delivered media SHA-256")
    try:
        int(render_hash, 16)
    except ValueError as exc:
        raise ValueError("render SHA-256 must be hexadecimal") from exc
    if not render.get("evidence"):
        raise ValueError("render evidence must include frames, audio, or transcript")
    judge_render = {key: render[key] for key in (
        "sha256", "evidence", "duration_seconds") if key in render}
    judgment = judge(brief, judge_render)
    if not isinstance(judgment, Mapping):
        raise TypeError("independent judge must return a mapping")
    judge_name = judgment.get("judge")
    if not isinstance(judge_name, str) or not judge_name.strip():
        raise ValueError("judge must identify the independent scorer")
    criteria = _validate_judgment(expected_ids, judgment)
    measured = [row for row in criteria if row["status"] != "not_measured"]
    passed = sum(row["status"] == "pass" for row in measured)
    failed = sum(row["status"] == "fail" for row in measured)
    missing = len(criteria) - len(measured)
    score = passed / len(measured) if measured else None
    editorial_status = ("fail" if failed else "not_measured" if missing
                        or not measured else "pass")
    technical = render.get("technical_verification")
    if not isinstance(technical, Mapping) or not technical:
        technical_status = "not_measured"
    elif all(value is True for value in technical.values()):
        technical_status = "pass"
    elif any(value is False for value in technical.values()):
        technical_status = "fail"
    else:
        technical_status = "not_measured"
    return {
        "schema": SCHEMA,
        "evaluation_level": 4,
        "request_id": request_id,
        "brief_sha256": hashlib.sha256(json.dumps(
            brief, sort_keys=True, ensure_ascii=False,
            separators=(",", ":")).encode("utf-8")).hexdigest(),
        "render_sha256": render_hash,
        "judge": judge_name,
        "editorial": {
            "status": editorial_status,
            "score": score,
            "passed": passed,
            "failed": failed,
            "measured": len(measured),
            "not_measured": missing,
            "criteria": criteria,
        },
        "technical": {"status": technical_status,
                      "checks": dict(technical or {})},
    }


def score_batch(deliveries: Sequence[Mapping[str, Any]],
                judge: Callable[[Mapping[str, Any], Mapping[str, Any]],
                                Mapping[str, Any]]) -> dict[str, Any]:
    """Score delivered videos independently against their own briefs."""
    results = []
    for delivery in deliveries:
        request_id = str(delivery.get("request_id") or "unknown")
        try:
            results.append(score_brief(
                request_id=request_id, brief=delivery["brief"],
                render=delivery["render"], judge=judge))
        except (KeyError, TypeError, ValueError) as exc:
            results.append({
                "schema": SCHEMA, "evaluation_level": 4,
                "request_id": request_id,
                "editorial": {"status": "not_measured", "score": None,
                              "reason": f"{type(exc).__name__}: {exc}"},
                "technical": {"status": "not_measured", "checks": {}},
            })
    measured_scores = [row["editorial"]["score"] for row in results
                       if isinstance(row.get("editorial"), Mapping)
                       and isinstance(row["editorial"].get("score"),
                                      (int, float))]
    statuses = {status: sum(
        row.get("editorial", {}).get("status") == status for row in results)
        for status in ("pass", "fail", "not_measured")}
    return {
        "schema": "ren-editorial-brief-score-batch/1",
        "evaluation_level": 4,
        "request_count": len(results),
        "outcomes": statuses,
        "measured_request_scores": len(measured_scores),
        "mean_measured_request_score": (
            mean(measured_scores) if measured_scores else None),
        "results": results,
    }
