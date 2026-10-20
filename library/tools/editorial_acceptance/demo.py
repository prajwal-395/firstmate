"""The recurring demonstration: one complete professional edit.

The punch list's section 12 asks for one benchmark task, run again and
again, so that effort cannot fragment into isolated components. It names
ten obligations and ten pieces of evidence, and it says plainly what the
milestone is NOT: it is not achieved because Ren ran every command. It
is achieved when the finished output fulfils the user's editorial
intent.

So this module holds three things:

- the ASSIGNMENT: the ten obligations, quoted from the brief the
  captain set.
- the EVIDENCE CONTRACT: the ten things that have to be shown, and what
  shows each one.
- the scorer, which reads a task record against them and says which are
  established and which are not.

The run is RECURRING because the demonstration is run again on different
source material, different project layouts and different creative
objectives. `compare_runs` reads two runs against each other, so a
regression is a number rather than a memory.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

SCHEMA = "ren-editorial-demonstration/1"

#: The ten obligations, from the captain's demonstration assignment.
OBLIGATIONS: tuple[dict[str, str], ...] = (
    {"id": "opening",
     "statement": "Identify the most engaging opening moment."},
    {"id": "pauses",
     "statement": "Remove unnecessary pauses and repetition."},
    {"id": "angle",
     "statement": "Replace one weak camera angle."},
    {"id": "broll",
     "statement": "Choose and insert relevant B-roll."},
    {"id": "caption",
     "statement": "Correct an inaccurate caption."},
    {"id": "lower-third",
     "statement": "Adjust a lower-third graphic."},
    {"id": "levels",
     "statement": "Balance relevant audio levels."},
    {"id": "preservation",
     "statement": "Preserve existing grading, transitions, transforms, "
                  "linked audio, and unrelated human edits."},
    {"id": "export",
     "statement": "Export the finished reel."},
    {"id": "verify",
     "statement": "Verify the actual exported video."},
)

#: The ten pieces of evidence the demonstration has to produce, and what
#: produces each one.
EVIDENCE: tuple[dict[str, str], ...] = (
    {"id": "interpreted",
     "statement": "The brief is interpreted without evaluator-supplied "
                  "edit commands.",
     "proves": "the run stated what it understood and named the work "
               "itself"},
    {"id": "evidence-backed",
     "statement": "All selected source moments are supported by recorded "
                  "evidence.",
     "proves": "every retrieved moment carries at least one evidence id"},
    {"id": "plan-valid",
     "statement": "The edit plan is semantically valid.",
     "proves": "the plan covers what the request asked for and names "
               "nothing it did not"},
    {"id": "qualified-route",
     "statement": "Every requested operation is routed through qualified "
                  "execution.",
     "proves": "the receipt reached a verified state and the route is "
               "named"},
    {"id": "unrelated-unchanged",
     "statement": "Unrelated approved state remains unchanged.",
     "proves": "no collateral change was recorded and the preservation "
               "check passed"},
    {"id": "no-open-transaction",
     "statement": "No unresolved transaction or recovery state remains.",
     "proves": "the run recorded no pending recovery, or a verified one"},
    {"id": "export-carries-changes",
     "statement": "The exported video contains the requested changes.",
     "proves": "the file carries a digest and the judge read the changes "
               "in it"},
    {"id": "technical-pass",
     "statement": "Technical audio/video constraints pass.",
     "proves": "every technical check that ran passed, and none was left "
               "unmeasured"},
    {"id": "independent-judgment",
     "statement": "An independent editor judges the resulting treatment "
                  "acceptable against the brief.",
     "proves": "a named judge read the file and passed the brief"},
    {"id": "latency-and-resource",
     "statement": "Total latency and peak resource usage are measured.",
     "proves": "the run recorded its wall time and its peak resident size"},
)


def _block(record: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    block = record.get(name)
    return block if isinstance(block, Mapping) else {}


def _operations(record: Mapping[str, Any]) -> list[str]:
    plan = _block(record, "planning")
    return [str(entry.get("op")) for entry in (plan.get("operations") or [])
            if isinstance(entry, Mapping)]


def _obligations(record: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Which of the ten obligations the record shows."""
    plan = _operations(record)
    execution = _block(record, "execution")
    checks = execution.get("checks")
    checks = checks if isinstance(checks, Mapping) else {}
    delivery = _block(record, "delivery")
    assessment = _block(record, "assessment")
    editorial = assessment.get("editorial")
    editorial = editorial if isinstance(editorial, Mapping) else {}
    technical = assessment.get("technical")
    technical = technical if isinstance(technical, Mapping) else {}
    collateral = list(execution.get("collateral_changes") or [])
    rows = {
        "opening": ("reel.opening" in plan,
                    "the plan opens the reel on a moment"),
        "pauses": (any(op in plan for op in ("clip.trim", "clip.remove")),
                   "the plan removes pauses or repetition"),
        "angle": ("multicam.select_angle" in plan,
                  "the plan replaces a camera angle"),
        "broll": ("broll.insert" in plan,
                  "the plan inserts cutaway material"),
        "caption": ("caption_fix" in plan,
                    "the plan corrects a caption"),
        "lower-third": (any(op in plan for op in ("overlay.position",
                                                  "overlay.resize",
                                                  "overlay.replace")),
                        "the plan adjusts the lower third"),
        "levels": (any(op in plan for op in ("audio.gain", "audio.fade",
                                             "audio.pan")),
                   "the plan balances the audio"),
        "preservation": (
            not collateral
            and checks.get("approved_edits_preserved") is True,
            "no unrelated state changed and the preservation check passed"),
        "export": (bool(delivery.get("sha256")),
                   "a file was delivered, with its digest"),
        "verify": (bool(technical.get("status"))
                   and str(technical.get("status")) in {"pass", "fail"},
                   "the delivered file was inspected"),
    }
    return {
        obligation["id"]: {
            "id": obligation["id"],
            "statement": obligation["statement"],
            "established": bool(rows[obligation["id"]][0]),
            "detail": rows[obligation["id"]][1],
        }
        for obligation in OBLIGATIONS
    }


def _evidence(record: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Which of the ten pieces of evidence the record produces."""
    interpretation = _block(record, "interpretation")
    retrieval = _block(record, "retrieval")
    planning = _block(record, "planning")
    execution = _block(record, "execution")
    receipt = execution.get("receipt")
    receipt = receipt if isinstance(receipt, Mapping) else {}
    checks = execution.get("checks")
    checks = checks if isinstance(checks, Mapping) else {}
    delivery = _block(record, "delivery")
    assessment = _block(record, "assessment")
    editorial = assessment.get("editorial")
    editorial = editorial if isinstance(editorial, Mapping) else {}
    technical = assessment.get("technical")
    technical = isinstance(technical, Mapping) and technical or {}
    moments = [moment for moment in (retrieval.get("moments") or [])
               if isinstance(moment, Mapping)]
    recovery = execution.get("recovery")
    recovery = recovery if isinstance(recovery, Mapping) else {}
    run = _block(record, "run")
    resource = _block(record, "resource")
    unbacked = [moment for moment in moments
                if not list(moment.get("evidence_ids") or [])]
    established = {
        "interpreted": (
            bool(str(interpretation.get("restated_outcome") or "").strip())
            and bool(list(interpretation.get("selected_operations") or []))),
        "evidence-backed": (bool(moments) and not unbacked),
        "plan-valid": (bool(planning.get("operations"))
                       and not list(planning.get("unauthorised_operations")
                                    or [])),
        "qualified-route": (
            str(receipt.get("status") or "") in
            {"verified", "committed", "delivered"}
            and bool(str(execution.get("route") or "").strip())),
        "unrelated-unchanged": (
            not list(execution.get("collateral_changes") or [])
            and checks.get("approved_edits_preserved") is True),
        "no-open-transaction": (
            str(recovery.get("state") or "none") != "pending"
            and recovery.get("verified") is not False),
        "export-carries-changes": (
            bool(delivery.get("sha256"))
            and str(editorial.get("status") or "") == "pass"),
        "technical-pass": (
            bool(technical)
            and str(technical.get("status") or "") == "pass"
            and not list(delivery.get("technical_not_checked") or [])),
        "independent-judgment": (
            bool(str(assessment.get("judge") or "").strip())
            and str(editorial.get("status") or "") == "pass"),
        "latency-and-resource": (
            run.get("wall_seconds") is not None
            and resource.get("peak_rss_bytes") is not None),
    }
    return {
        row["id"]: {
            "id": row["id"],
            "statement": row["statement"],
            "proves": row["proves"],
            "established": bool(established[row["id"]]),
        }
        for row in EVIDENCE
    }


def score_demo(record: Mapping[str, Any], brief: Mapping[str, Any],
               ) -> dict[str, Any]:
    """One demonstration run, scored against the ten and the ten."""
    obligations = _obligations(record)
    evidence = _evidence(record)
    execution = _block(record, "execution")
    run = _block(record, "run")
    resource = _block(record, "resource")
    delivery = _block(record, "delivery")
    assessment = _block(record, "assessment")
    editorial = assessment.get("editorial")
    editorial = editorial if isinstance(editorial, Mapping) else {}
    technical = assessment.get("technical")
    technical = technical if isinstance(technical, Mapping) else {}
    obligations_done = sum(1 for row in obligations.values()
                           if row["established"])
    evidence_done = sum(1 for row in evidence.values() if row["established"])
    milestone = (obligations_done == len(OBLIGATIONS)
                 and evidence_done == len(EVIDENCE)
                 and str(editorial.get("status") or "") == "pass"
                 and str(technical.get("status") or "") == "pass")
    return {
        "schema": SCHEMA,
        "brief_id": str(brief.get("id") or ""),
        "instruction": str(brief.get("instruction") or ""),
        "obligations": list(obligations.values()),
        "obligations_established": obligations_done,
        "obligations_total": len(OBLIGATIONS),
        "evidence": list(evidence.values()),
        "evidence_established": evidence_done,
        "evidence_total": len(EVIDENCE),
        "milestone": milestone,
        "milestone_rule": (
            "every obligation established, every piece of evidence "
            "produced, and the independent judge passed the delivered "
            "file against the brief - not the plan, and not the commands "
            "that ran"),
        "latency": {
            "wall_seconds": run.get("wall_seconds"),
            "instruction_to_verified_seconds": run.get(
                "instruction_to_verified_seconds"),
        },
        "resource": {
            "peak_rss_bytes": resource.get("peak_rss_bytes"),
        },
        "delivery": {
            "sha256": delivery.get("sha256"),
            "duration_seconds": delivery.get("duration_seconds"),
        },
        "assessment": {
            "judge": assessment.get("judge"),
            "editorial_status": editorial.get("status"),
            "technical_status": technical.get("status"),
        },
        "execution": {
            "receipt_status": (execution.get("receipt") or {}).get("status")
            if isinstance(execution.get("receipt"), Mapping) else None,
            "route": execution.get("route"),
        },
    }


def unrun_report(brief: Mapping[str, Any]) -> dict[str, Any]:
    """The demonstration, before it has been run.

    The assignment and the evidence contract are in the report even when
    nothing has been run, so a reader can see what the next run has to
    produce rather than finding out afterwards.
    """
    return {
        "schema": SCHEMA,
        "brief_id": str(brief.get("id") or ""),
        "instruction": str(brief.get("instruction") or ""),
        "run_state": "not_run",
        "obligations": [
            {"id": row["id"], "statement": row["statement"],
             "established": False,
             "detail": "the demonstration has not been run"}
            for row in OBLIGATIONS],
        "obligations_established": 0,
        "obligations_total": len(OBLIGATIONS),
        "evidence": [
            {"id": row["id"], "statement": row["statement"],
             "proves": row["proves"], "established": False}
            for row in EVIDENCE],
        "evidence_established": 0,
        "evidence_total": len(EVIDENCE),
        "milestone": False,
        "milestone_rule": (
            "every obligation established, every piece of evidence "
            "produced, and the independent judge passed the delivered "
            "file against the brief - not the plan, and not the commands "
            "that ran"),
        "latency": {"wall_seconds": None,
                    "instruction_to_verified_seconds": None},
        "resource": {"peak_rss_bytes": None},
        "delivery": {"sha256": None, "duration_seconds": None},
        "assessment": {"judge": None, "editorial_status": None,
                       "technical_status": None},
        "execution": {"receipt_status": None, "route": None},
    }


def compare_runs(previous: Mapping[str, Any],
                 current: Mapping[str, Any]) -> dict[str, Any]:
    """This run against the last one, item by item.

    The demonstration is recurring, so a regression has to be a number
    rather than a memory of how the last one felt.
    """
    def _index(report: Mapping[str, Any], section: str) -> dict[str, Any]:
        return {str(row.get("id")): bool(row.get("established"))
                for row in (report.get(section) or [])
                if isinstance(row, Mapping)}

    def _delta(before: Mapping[str, Any], after: Mapping[str, Any]
               ) -> dict[str, Any]:
        changed = []
        for key in sorted(set(before) | set(after)):
            was = before.get(key)
            now = after.get(key)
            if was != now:
                changed.append({"id": key, "was": was, "is": now})
        return {"changed": changed, "regressions": [
            row for row in changed if row["was"] is True and row["is"] is False]}

    obligations = _delta(_index(previous, "obligations"),
                         _index(current, "obligations"))
    evidence = _delta(_index(previous, "evidence"),
                      _index(current, "evidence"))
    return {
        "schema": "ren-editorial-demonstration-comparison/1",
        "brief_id": str(current.get("brief_id") or ""),
        "obligations": obligations,
        "evidence": evidence,
        "milestone_was": bool(previous.get("milestone")),
        "milestone_is": bool(current.get("milestone")),
        "regressed": bool(obligations["regressions"] or evidence["regressions"]),
    }
