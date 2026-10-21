"""QA-008: final acceptance is an explicit state of the task, not a stage.

WHAT WAS WRONG
--------------
Every leg of an edit's acceptance existed, and none of them was joined
into a STATE.  AXI-013 (PR 3069) compares two fresh reads around every
mutation; QA-001 (PR 3084) reads a whole timeline back structurally;
QA-005 (PR 3097) reads an approved shot's LOOK back across a revision;
QA-004, QA-002 and QA-003 (PRs 3046, 3071, 3082) judge the delivered
file's audio, picture and captions; QA-006 (PR 3088) is the structured
editorial review of that file.  Each of them answers for ITSELF and
none of them is asked whether the OTHER ones were run.

So the record said `delivered` and the reader was left to believe that
a task which executed cleanly was a task which was EDITORIALLY FINISHED.
It is not.  A delivered file can fail its audio spec, a timeline can
carry a hole, a shot can lose its grade, and an editor can read the
result and refuse it - and every one of those outcomes read the same on
the task: `delivered`.

The punch list's QA-008 asks for the opposite in five lines, and this
module is those five lines:

- **Do not equate successful execution with editorial completion.**  A
  task is ACCEPTED only when every APPLICABLE leg was measured and
  passed.  Execution is one of the legs, not the verdict.
- **Require the operation-level checks and applicable final-file
  checks.**  Both are legs here, and a leg that does not apply is
  recorded as NOT APPLICABLE - with the fact that made it inapplicable
  - rather than dropped.
- **Attach reviewer approval where the workflow calls for it.**  The
  approval is a leg of its own, and it applies only where the workflow
  asks for a reviewer.  A missing approval is UNMEASURED; a refused
  approval is a REJECTION.
- **Keep technically valid but editorially rejected work as a distinct
  outcome.**  `editorially_rejected` is not `failed`: every technical
  leg passed, and the work was refused on editorial grounds.  The two
  are never folded together, because the fix for each is different.
- **Record incomplete checks as unmeasured.**  A check that could not
  run is `unmeasured`, never a pass, and never zero.

THE TWO VOCABULARIES
--------------------
The OUTCOME is what the acceptance gate decided, and it is one of four:
`accepted`, `editorially_rejected`, `failed` (a technical leg failed)
and `unmeasured` (an applicable leg could not be measured).  The
outcome is the precise reading and it lives in the record.

The STATE is the task's own, and it is one of two: `accepted` and
`rejected`.  A gate that cannot decide does not accept - an unmeasured
leg is a fail-closed `rejected` - so the state says whether the work
was accepted, and the outcome says why it was not.

APPLICABILITY IS A FACT, AND AN UNKNOWN FACT IS NOT A PASS
-----------------------------------------------------------
Each leg names the FACT that decides whether it applies (a delivered
file exists, a mutation was applied, the workflow calls for a
reviewer).  A fact is TRUE, FALSE, or UNKNOWN, and the third is not
the second: an unknown fact leaves the leg UNMEASURED, because a leg
whose applicability nobody established was not checked.  This is the
same rule the rest of the engine follows - an absent measurement is
never an implicit pass (`library/tools/operation_acceptance.py`).

WHERE THE RESULTS COME FROM
---------------------------
This module does not re-measure anything.  Every leg is JUDGED by the
engine's own measurement, and the helpers below call those
measurements: `operation_result` (AXI-013), `structure_result` (QA-001),
`look_result` (QA-005), `delivered_results` (QA-002, QA-003, QA-004),
`editorial_result` (QA-006) and `approval_result` (the reviewer's
sign-off).  A caller that has a result from somewhere else may pass it
in directly; what it may not do is pass in a pass for a check that
never ran.

The record it writes lives in the assignment's own `acceptance` section
(`library/tools/assignment_lifecycle.py`), so the task record, the
`ren assignment` envelope and `ren work-state` all read the SAME
answer, and the task's STATE says `accepted` only when the gate
accepted it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

SCHEMA = "ren-final-acceptance/1"

# ── the vocabulary ────────────────────────────────────────────────────────

#: The kinds of leg.  A TECHNICAL leg is a measurement the engine made;
#: an EDITORIAL leg is a reading, and an APPROVAL leg is a person's
#: yes.  The kinds are kept apart because they are cleared by different
#: things, and a technical pass may not stand in for an editorial one.
TECHNICAL = "technical"
EDITORIAL = "editorial"
APPROVAL = "approval"

#: How one leg stands.  `unmeasured` is the load-bearing one: a check
#: that could not run is not a pass, and it is not a failure either.
PASSED = "passed"
FAILED = "failed"
UNMEASURED = "unmeasured"
NOT_APPLICABLE = "not_applicable"

STATUSES: tuple[str, ...] = (PASSED, FAILED, UNMEASURED, NOT_APPLICABLE)

#: The four outcomes the gate can reach.
ACCEPTED = "accepted"
EDITORIALLY_REJECTED = "editorially_rejected"
TECHNICALLY_FAILED = "failed"
OUTCOME_UNMEASURED = "unmeasured"

OUTCOMES: tuple[str, ...] = (ACCEPTED, EDITORIALLY_REJECTED,
                              TECHNICALLY_FAILED, OUTCOME_UNMEASURED)

#: The two task states the acceptance gate can leave behind.  A gate
#: that cannot decide does not accept: `unmeasured` is fail-closed.
STATE_ACCEPTED = "accepted"
STATE_REJECTED = "rejected"

ACCEPTANCE_STATES: tuple[str, ...] = (STATE_ACCEPTED, STATE_REJECTED)


class AcceptanceError(ValueError):
    """The acceptance gate cannot be run, or was asked to lie."""


class Leg:
    """One leg of the final acceptance.

    `requires` names the FACT that decides whether this leg applies to
    a task.  The fact is read by the caller, because only the caller
    knows what the task produced; the leg itself only says which fact
    it needs, so the enumeration stays in one place.
    """

    __slots__ = ("citation", "id", "kind", "requires", "statement")

    def __init__(self, leg_id: str, statement: str, kind: str,
                 citation: str, requires: str) -> None:
        self.id = leg_id
        self.statement = statement
        self.kind = kind
        self.citation = citation
        self.requires = requires

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "statement": self.statement,
                "kind": self.kind, "citation": self.citation,
                "requires": self.requires}


#: Every leg of the final acceptance, in the order a reader wants them:
#: what the WRITES did, what the TIMELINE is, what the PICTURE looks
#: like, what the FILE is, what the EDITOR thinks, and whether a person
#: said yes.
LEGS: tuple[Leg, ...] = (
    Leg(
        "operation_pre_post",
        "Every mutation this task applied reads back against a fresh "
        "pre/post state, with nothing unauthorized changed.",
        TECHNICAL,
        "library.tools.mutation_verification:verify_delta (AXI-013, "
        "PR 3069)",
        "applied_mutations"),
    Leg(
        "timeline_structure",
        "The whole timeline reads back as a structure: no hole, no "
        "double-placed item, no link pointing at nothing.",
        TECHNICAL,
        "library.tools.timeline_structure:compare (QA-001, PR 3084)",
        "has_timeline"),
    Leg(
        "look_preservation",
        "Every approved shot's look survives the work: its grade, its "
        "comps, its canvas, its transform and its keyframes.",
        TECHNICAL,
        "library.tools.look_preservation:compare_look (QA-005, PR 3097)",
        "has_approved_look"),
    Leg(
        "delivered_audio",
        "The delivered file's own audio spec: its loudness against the "
        "spec, the silence under picture, and the channel presence.",
        TECHNICAL,
        "library.tools.deliverable_qc:verify_audio_spec (QA-004, PR 3046)",
        "has_delivered_file"),
    Leg(
        "delivered_picture",
        "The delivered file's own picture: the frame it was delivered "
        "at, whether it fills it, and no undeclared black or frozen "
        "frames.",
        TECHNICAL,
        "library.tools.deliverable_qc:verify_picture_spec (QA-002, PR 3071)",
        "has_delivered_file"),
    Leg(
        "delivered_captions",
        "The delivered file's own captions: the words that were "
        "declared, where they are drawn, and whether they can be read.",
        TECHNICAL,
        "library.tools.delivered_caption_spec:verify_delivered_captions "
        "(QA-003, PR 3082)",
        "has_captions"),
    Leg(
        "editorial_review",
        "The editorial review of the delivered file: every leg read, "
        "each with its evidence and its uncertainty.",
        EDITORIAL,
        "library.tools.editorial_acceptance.judgement:review (QA-006, "
        "PR 3088)",
        "has_delivered_file"),
    Leg(
        "reviewer_approval",
        "The reviewer's approval, where the workflow calls for one.",
        APPROVAL,
        "library.tools.reel_signoff:signoff_for (the captain's own words)",
        "requires_approval"),
)

LEG_IDS: tuple[str, ...] = tuple(leg.id for leg in LEGS)

#: Every fact a leg's applicability rests on.  A fact the caller does
#: not answer is UNKNOWN, and an unknown fact leaves its leg
#: unmeasured - never inapplicable.
FACTS: tuple[str, ...] = tuple(dict.fromkeys(
    leg.requires for leg in LEGS))


def leg(leg_id: str) -> Leg:
    """The leg with this id, or a refusal."""
    for entry in LEGS:
        if entry.id == leg_id:
            return entry
    raise AcceptanceError(
        f"{leg_id!r} is not a leg of the final acceptance. The legs are: "
        + ", ".join(LEG_IDS))


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ── the gate ──────────────────────────────────────────────────────────────

def _applicability(fact: Any) -> bool | None:
    """True, False, or None for a fact nobody established."""
    if fact is True:
        return True
    if fact is False:
        return False
    return None


def evaluate(facts: Mapping[str, Any],
             results: Mapping[str, Mapping[str, Any]],
             legs: Sequence[str] | None = None) -> dict[str, Any]:
    """Judge the legs, and say what the task's acceptance is.

    `facts` answers which legs apply (see :data:`FACTS`); `results`
    carries what each applicable leg MEASURED.  A leg that applies and
    has no result is unmeasured, and so is a leg whose applicability
    nobody established.

    `legs` narrows the gate to a SUBSET of :data:`LEGS`, for a caller
    that is measuring something the other legs cannot speak for.  The
    finished-video benchmark (QA-010) is that caller: it judges a
    DELIVERED FILE, and the legs about the writes and the timeline a
    file was built from are not its to answer.  The gate, the
    vocabulary and the outcomes are the same ones - a subset is not a
    second gate - and a leg named here that is not a leg of this gate
    RAISES rather than being ignored.

    The outcome, in the order the questions are asked:

    - a technical leg FAILED - the work is not technically valid;
    - an editorial or approval leg FAILED - the work IS technically
      valid and was refused on editorial grounds, which is a different
      outcome with a different fix;
    - an applicable leg is UNMEASURED - nothing was established about
      it, so the work is not accepted;
    - otherwise - every applicable leg passed, and the work is
      ACCEPTED.

    The counts carry their denominator: `applicable` legs out of
    `total` legs, and the unmeasured count is never folded into the
    passed one.
    """
    entries = LEGS if legs is None else tuple(
        leg(leg_id) for leg_id in legs)
    rows: list[dict[str, Any]] = []
    for entry in entries:
        fact = facts.get(entry.requires)
        applies = _applicability(fact)
        result = results.get(entry.id)
        if applies is False:
            status = NOT_APPLICABLE
            observation = (f"this leg does not apply to this task: "
                           f"{entry.requires} is false")
            artifact = ""
        elif applies is None:
            status = UNMEASURED
            observation = (f"whether this leg applies was not established: "
                           f"{entry.requires} is unknown, and a leg nobody "
                           f"established is a leg nobody checked")
            artifact = ""
        elif result is None:
            status = UNMEASURED
            observation = ("this leg applies to this task and was not "
                           "measured")
            artifact = ""
        else:
            if not isinstance(result, Mapping):
                raise AcceptanceError(
                    f"the {entry.id!r} leg's result is not an object, so it "
                    "cannot be read")
            status = str(result.get("status") or "")
            if status not in (PASSED, FAILED, UNMEASURED):
                raise AcceptanceError(
                    f"the {entry.id!r} leg has status {status!r}, which is "
                    f"not one of {PASSED}, {FAILED} or {UNMEASURED}")
            observation = str(result.get("observation") or "").strip()
            if not observation:
                raise AcceptanceError(
                    f"the {entry.id!r} leg carries no observation: a "
                    "verdict with no reason is not a reading, and this "
                    "gate refuses one")
            artifact = str(result.get("artifact") or "")
        rows.append({
            "id": entry.id,
            "statement": entry.statement,
            "kind": entry.kind,
            "citation": entry.citation,
            "requires": entry.requires,
            "applicable": applies is not False,
            "status": status,
            "observation": observation,
            "artifact": artifact,
            # What the caller supplied, kept even when the leg does not
            # apply: a measurement nobody dropped is a measurement a
            # reader can check.
            "result": (dict(result) if isinstance(result, Mapping)
                       else None),
        })

    applicable = [row for row in rows if row["status"] != NOT_APPLICABLE]
    failed = [row for row in applicable if row["status"] == FAILED]
    unmeasured = [row for row in applicable if row["status"] == UNMEASURED]
    technical_failures = [row for row in failed if row["kind"] == TECHNICAL]
    refusals = [row for row in failed if row["kind"] in (EDITORIAL, APPROVAL)]

    if not applicable:
        outcome = OUTCOME_UNMEASURED
        reason = ("no leg applied to this task, so nothing was established "
                  "about it")
    elif technical_failures:
        outcome = TECHNICALLY_FAILED
        reason = (f"{len(technical_failures)} technical leg(s) failed: "
                  + "; ".join(row["id"] for row in technical_failures))
    elif refusals:
        outcome = EDITORIALLY_REJECTED
        reason = (
            f"{len(refusals)} editorial leg(s) refused the work and every "
            "technical leg passed: the work is technically valid and was "
            "rejected on editorial grounds ("
            + "; ".join(row["id"] for row in refusals) + ")")
    elif unmeasured:
        outcome = OUTCOME_UNMEASURED
        reason = (f"{len(unmeasured)} applicable leg(s) were not measured, "
                  "so nothing was established about them: "
                  + "; ".join(row["id"] for row in unmeasured))
    else:
        outcome = ACCEPTED
        reason = (f"every applicable leg was measured and passed "
                  f"({len(applicable)} of {len(rows)} legs)")

    counts = {
        "total": len(rows),
        "applicable": len(applicable),
        "passed": sum(1 for row in applicable if row["status"] == PASSED),
        "failed": len(failed),
        "unmeasured": len(unmeasured),
        "not_applicable": sum(1 for row in rows
                               if row["status"] == NOT_APPLICABLE),
    }
    return {
        "schema": SCHEMA,
        "outcome": outcome,
        "state": (STATE_ACCEPTED if outcome == ACCEPTED else STATE_REJECTED),
        "reason": reason,
        "legs": rows,
        "counts": counts,
        "facts": {name: facts.get(name) for name in FACTS
                  if name in {entry.requires for entry in entries}},
    }


# ── the record ────────────────────────────────────────────────────────────

def record(project: str, assignment_id: str, *,
           facts: Mapping[str, Any],
           results: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Run the gate for a task and write the answer onto its record.

    The answer lands in the assignment's own `acceptance` section, so
    the task record, the `ren assignment` envelope and `ren work-state`
    read the SAME verdict, and the task's STATE says `accepted` only
    when the gate accepted it.

    A task that has not been DELIVERED cannot be accepted, and the gate
    refuses to write a verdict for one: the work is still in flight,
    and a verdict on work in flight is a guess.
    """
    from library.tools import assignment_lifecycle as lifecycle

    evaluated = evaluate(facts, results)
    with lifecycle.assignment_lock(project, assignment_id):
        stored = lifecycle.load(project, assignment_id)
        if stored is None:
            raise FileNotFoundError(
                f"assignment {assignment_id!r} has no checkpoint")
        state = str(stored.get("state") or "brief")
        if state not in ("delivered", STATE_ACCEPTED, STATE_REJECTED):
            raise AcceptanceError(
                f"the task is {state}, not delivered: a task in flight has "
                "no final acceptance to record")
        section = stored.get("acceptance")
        if not isinstance(section, dict):
            section = {"criteria": [], "evidence": {}}
        final = dict(evaluated)
        final["at"] = _now()
        section["final"] = final
        stored["acceptance"] = section
        lifecycle.save(project, stored)
        target = evaluated["state"]
        if target != state:
            lifecycle.transition(project, assignment_id, target,
                                 event="final_acceptance",
                                 detail={"outcome": evaluated["outcome"],
                                         "reason": evaluated["reason"]})
    return evaluated


def read(project: str, assignment_id: str) -> dict[str, Any] | None:
    """The acceptance this task carries, or None when it carries none.

    An absent verdict is NOT a pass: a task whose acceptance has not
    been evaluated says so, and this returns None for it.
    """
    from library.tools import assignment_lifecycle as lifecycle

    stored = lifecycle.load(project, assignment_id)
    if stored is None:
        return None
    section = stored.get("acceptance")
    if not isinstance(section, Mapping):
        return None
    final = section.get("final")
    return dict(final) if isinstance(final, Mapping) else None


# ── the engine's own measurements, read as leg results ───────────────────

def _result(status: str, observation: str, artifact: str = "") -> dict:
    return {"status": status, "observation": observation, "artifact": artifact}


def operation_result(operations: Sequence[Mapping[str, Any]],
                     *, artifact: str = "") -> dict[str, Any]:
    """The operation-level leg, off AXI-013's own acceptance contract.

    `operations` are the operations the task applied.  Each one may
    carry the evidence its own contract asks for (`evidence`) and the
    verdict of the engine's own pre/post comparison (`readback_passed`);
    an operation with neither is MISSING evidence, and a missing check
    is unmeasured rather than a pass.
    """
    from library.tools import operation_acceptance

    rows = []
    for operation in operations or ():
        if not isinstance(operation, Mapping):
            raise AcceptanceError(
                "an operation is not an object, so its acceptance cannot "
                "be judged")
        contract = operation_acceptance.evaluate(
            operation,
            readback_passed=operation.get("readback_passed"),
            evidence=operation.get("evidence"))
        rows.append(contract)
    if not rows:
        return _result(UNMEASURED,
                       "the task applied no operation, so the operation-level "
                       "check has nothing to judge")
    failed = [row for row in rows if row["failed"]]
    missing = [row for row in rows if row["missing"]]
    if failed:
        return _result(FAILED,
                       f"{len(failed)} of {len(rows)} operation(s) failed "
                       "their acceptance contract: "
                       + "; ".join(f"{row['operation']}: "
                                   f"{', '.join(row['failed'])}"
                                   for row in failed),
                       artifact)
    if missing:
        return _result(UNMEASURED,
                       f"{len(missing)} of {len(rows)} operation(s) carry "
                       "evidence nobody measured: "
                       + "; ".join(f"{row['operation']}: "
                                   f"{', '.join(row['missing'])}"
                                   for row in missing),
                       artifact)
    return _result(PASSED,
                   f"all {len(rows)} operation(s) read back against a "
                   "fresh pre/post state with nothing unauthorized "
                   "changed",
                   artifact)


def structure_result(after: Mapping[str, Any],
                      *, before: Mapping[str, Any] | None = None,
                      expectation: Mapping[str, Any] | None = None,
                      expected_timeline: str = "",
                      artifact: str = "") -> dict[str, Any]:
    """The whole-timeline leg, off QA-001's own structural read-back."""
    from library.tools import timeline_structure

    report = timeline_structure.compare(before, after,
                                        expectation=expectation,
                                        expected_timeline=expected_timeline)
    errors = report.errors
    if errors:
        return _result(FAILED,
                       f"{len(errors)} structural error(s) on the timeline: "
                       + "; ".join(f"{row.check}: {row.detail}"
                                   for row in errors),
                       artifact)
    warnings = [row for row in report.findings]
    return _result(PASSED,
                   "the whole timeline reads back as a structure"
                   + (f" ({len(warnings)} warning(s) reported)"
                      if warnings else ""),
                   artifact)


def look_result(before: Mapping[str, Any], after: Mapping[str, Any], *,
                scope: Any = None, approved: set[str] | None = None,
                artifact: str = "") -> dict[str, Any]:
    """The look leg, off QA-005's own comparison of two recorded looks.

    A difference nobody can judge - no authorized scope to judge it
    against - is UNPROVABLE, and an unprovable difference is
    unmeasured, not a pass.  That is the difference between reading
    `verify_look_preservation`'s verdict and reading the comparison it
    makes, and the comparison is the one that says UNPROVABLE.
    """
    from library.tools import look_preservation

    before_look = look_preservation.capture_look(before)
    after_look = look_preservation.capture_look(after)
    state = look_preservation.compare_look(before_look, after_look,
                                           scope=scope, approved=approved)
    status = str(state.get("status") or "")
    if status == look_preservation.VIOLATED:
        return _result(FAILED,
                       "an approved shot's look changed and no operation "
                       "authorized it: "
                       + "; ".join(f"{row.get('shot')} {row.get('field')}"
                                   for row in state.get("findings") or ()),
                       artifact)
    if status == look_preservation.UNPROVABLE:
        return _result(UNMEASURED,
                       "an approved shot's look moved and no authorized "
                       "scope says whether it may: a difference nobody can "
                       "judge is not a pass",
                       artifact)
    return _result(PASSED,
                   f"every approved shot's look survived ({state.get('shots_compared')} "
                   "shot(s) compared)",
                   artifact)


def delivered_results(video_path: str, spec: Any, *,
                       expected_duration: float = 0.0,
                       caption_intents: Sequence[Any] | None = None,
                       caption_spans: Sequence[tuple[float, float]] | None = None,
                       declared_black_spans: Sequence[tuple[float, float]] | None = None,
                       declared_silence_spans: Sequence[tuple[float, float]] | None = None,
                       expected_width: int | None = None,
                       expected_height: int | None = None,
                       qc: Any = None) -> dict[str, dict[str, Any]]:
    """The three delivered-file legs, off the product's own gates.

    QA-004 (the audio spec), QA-002 (the picture spec) and QA-003 (the
    captions) are the product's own measurements OF THE DELIVERED FILE,
    and this reads them rather than measuring the same numbers a second
    way.  A gate that could not run is reported as unmeasured, with the
    reason it gives.
    """
    from library.tools import deliverable_qc

    if qc is None:
        qc = deliverable_qc.run_deliverable_qc(
            video_path, spec, expected_duration,
            declared_black_beats=list(declared_black_spans or ()),
            declared_silence_spans=list(declared_silence_spans or ()),
            caption_intents=list(caption_intents)
            if caption_intents is not None else None,
            caption_spans=list(caption_spans or ()),
            expected_width=expected_width,
            expected_height=expected_height)

    results: dict[str, dict[str, Any]] = {}
    audio = qc.audio
    if audio is None:
        results["delivered_audio"] = _result(
            UNMEASURED,
            "the delivered file's audio spec did not run, so its audio was "
            "not measured")
    elif audio.passed:
        results["delivered_audio"] = _result(
            PASSED,
            "the delivered file's audio spec passed: "
            f"{audio.loudness.detail}; {audio.silence.detail}; "
            f"{audio.channels.detail}",
            video_path)
    else:
        results["delivered_audio"] = _result(
            FAILED,
            "the delivered file's audio spec FAILED: "
            f"{audio.loudness.detail}; {audio.silence.detail}; "
            f"{audio.channels.detail}",
            video_path)

    picture = qc.picture
    if picture is None:
        results["delivered_picture"] = _result(
            UNMEASURED,
            "the delivered file's picture spec did not run, so its picture "
            "was not measured")
    elif picture.passed:
        results["delivered_picture"] = _result(
            PASSED,
            "the delivered file's picture spec passed: "
            f"{picture.dimensions.detail}; {picture.framing.detail}",
            video_path)
    else:
        results["delivered_picture"] = _result(
            FAILED,
            "the delivered file's picture spec FAILED: "
            f"{picture.dimensions.detail}; {picture.framing.detail}",
            video_path)

    caption_spec = qc.caption_spec
    if caption_spec is None:
        results["delivered_captions"] = _result(
            UNMEASURED,
            "no caption intent was supplied, so the delivered file's "
            "captions had nothing to be compared against")
    elif not getattr(caption_spec, "checked", True):
        results["delivered_captions"] = _result(
            UNMEASURED,
            "the delivered file's captions could not be measured: "
            f"{getattr(caption_spec, 'reason', '') or 'the check did not run'}",
            video_path)
    elif caption_spec.passed:
        results["delivered_captions"] = _result(
            PASSED,
            "the delivered file's captions match what was declared for them",
            video_path)
    else:
        results["delivered_captions"] = _result(
            FAILED,
            "the delivered file's captions FAILED: "
            + ("; ".join(finding.detail
                         for finding in caption_spec.findings)
               or f"{len(caption_spec.findings)} finding(s)"),
            video_path)
    return results


def editorial_result(review: Mapping[str, Any],
                     *, artifact: str = "") -> dict[str, Any]:
    """The editorial leg, off QA-006's own review verdict.

    The review's four verdicts are kept apart on purpose: `pass` is a
    pass, `preference` is a REFUSAL on editorial grounds (the work is
    technically valid and was rejected), `fail` is an objective
    failure, and `not_measured` is a leg nobody read.
    """
    verdict = str(review.get("verdict") or "").strip()
    reason = str(review.get("verdict_reason") or review.get("reason") or "")
    if verdict == "pass":
        return _result(PASSED, reason or "the editorial review passed",
                       artifact)
    if verdict == "preference":
        return _result(FAILED,
                       reason or "the editorial review refused the work on "
                       "editorial grounds", artifact)
    if verdict == "fail":
        return _result(FAILED,
                       reason or "the editorial review found an objective "
                       "failure in the delivered file", artifact)
    if verdict in ("not_measured", ""):
        return _result(UNMEASURED,
                       reason or "the editorial review read no leg of the "
                       "delivered file, so nothing was established",
                       artifact)
    return _result(UNMEASURED,
                   f"the editorial review's verdict {verdict!r} is not one "
                   "this gate reads, so it established nothing",
                   artifact)


def approval_result(approval: Mapping[str, Any] | None, *,
                    artifact: str = "") -> dict[str, Any]:
    """The reviewer's approval, off the record the workflow keeps.

    An approval that is not there is UNMEASURED - nobody said no, and
    nobody said yes either.  An approval that says no is a REFUSAL.
    """
    if not approval:
        return _result(UNMEASURED,
                       "no reviewer's approval is on record, so the "
                       "workflow's approval was not established")
    approved = approval.get("approved")
    if approved is True:
        reviewer = str(approval.get("reviewer") or approval.get("signed_off_by")
                       or "the reviewer")
        return _result(PASSED,
                       f"{reviewer} approved this work", artifact)
    if approved is False:
        return _result(FAILED,
                       str(approval.get("why") or approval.get("note")
                           or "the reviewer refused this work"), artifact)
    return _result(UNMEASURED,
                   "the approval on record says neither yes nor no, so it "
                   "established nothing",
                   artifact)
