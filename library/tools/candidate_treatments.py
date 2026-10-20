"""Candidate treatments for a subjective request: generate, judge, decide.

The gap this closes
-------------------
Ren can carry out one plan.  When an editor's request is SUBJECTIVE -
"the opening feels slow", "make it punchier" - the honest answer is
often several defensible plans, or no plan at all, because the material
is already adequate.  Nothing in the engine could say so:

* alternatives existed only for a reel's opening (`edit_spec`'s
  `opening_alternatives`), and nothing judged one against the brief;
* nothing estimated a plan's RISK, or whether it could be undone;
* "change nothing" had no first-class answer - a translation that
  concluded the material was adequate had nowhere to record that;
* the alternatives that were NOT chosen were not kept, so the reasoning
  behind a decision evaporated with the chat that produced it.

This module is the ONE contract for a set of candidate treatments: what
each candidate is, how it is judged, how risky it is, which one is
preferred, how one is chosen or combined, and how the whole set is kept
as a project-level decision record.

The rules
---------
* **A criterion is judged, never assumed.**  A brief criterion carries the
  verdict the model reached, with its reason and evidence; the engine
  never supplies one.  A measurable constraint is COMPUTED by the engine
  from the candidate's own declared effect, and a constraint whose
  measurement is absent is `not_measured` - never a pass.
* **An unmeasured criterion is not a pass.**  `not_measured` is reported
  and never counted as satisfaction.
* **The least change that achieves the improvement wins.**  Among the
  candidates that satisfy every criterion, the one touching fewest
  categories wins; a candidate that touches nothing and satisfies the
  criteria IS the no-change conclusion.
* **Risk is read off the operation, not asserted.**  A candidate whose
  operations cannot be undone is high risk, whatever its rationale says.
* **Combining is refused unless the elements are compatible.**  Two
  elements that act on the same target, or that make different choices
  about the same category, contradict each other; the refusal names them.
* **The whole set is kept, including the alternatives that lost.**  The
  decision record carries every candidate and its reasoning.

This module imports nothing from steps or processes: it reads the
operation registry through `library/tools/edit_algebra.py` and writes the
decision record through `library/tools/creative_revision.py`, the same
readers and writers every consumer has.

How this sits beside `library/tools/editorial_decisions.py`
-------------------------------------------------------------
That module records ONE editorial decision: what was proposed, what it
rests on, what it would change, the criteria that would show it landed,
and who accepted it.  Its subject is a decision; this module's subject
is a SET of competing treatments for a request that has more than one
honest answer.  The two are not two paths to the same thing:

* `editorial_decisions` owns the LAYERS of one decision (observation,
  interpretation, candidate, approved, physical operation) and compiles
  an approved decision into an edit request;
* this module owns the COMPARISON - what the alternatives were, how each
  one was judged, how risky each one is, which one is preferred, and
  whether the honest answer is to change nothing at all.

Where the two share a word they share the enumeration: the states a
decision moves through are `editorial_decisions`'s, and
`to_editorial_decisions` projects a candidate set into that vocabulary so
the alternatives that LOST are readable as decisions too, not only as
rows in this module's own record.
"""

from __future__ import annotations

import copy
import os
from collections.abc import Mapping, Sequence
from typing import Any

FORMAT = "candidate_treatments/1"

# ── The decisions a candidate set can end in ─────────────────────────

SELECTED = "selected"
COMBINED = "combined"
NO_CHANGE = "no_change"

DECISIONS = (SELECTED, COMBINED, NO_CHANGE)

# ── The verdict vocabulary ───────────────────────────────────────────
#
# A verdict is a MEASUREMENT or a JUDGEMENT.  `not_measured` is the
# honest third: the measurement the criterion needs was absent, and an
# absent measurement is not a pass (it is also not a fail).

PASS = "pass"
FAIL = "fail"
NOT_MEASURED = "not_measured"

VERDICTS = (PASS, FAIL, NOT_MEASURED)

# ── The criterion kinds ──────────────────────────────────────────────
#
# A brief criterion is the editor's creative goal, judged by the model
# against the evidence.  A constraint is a MEASURABLE limit the engine
# computes from the candidate's own declared effect.  The two never mix:
# the engine computes constraints and records brief verdicts; it never
# supplies one.

BRIEF = "brief"
CONSTRAINT = "constraint"

CRITERION_KINDS = (BRIEF, CONSTRAINT)

# ── The risk levels ──────────────────────────────────────────────────

LOW = "low"
MODERATE = "moderate"
HIGH = "high"

RISK_LEVELS = (LOW, MODERATE, HIGH)

# ── The categories a treatment touches ───────────────────────────────
#
# The same vocabulary `edit_spec` reports a proposal's changes under, so
# "what does this plan touch" has ONE answer across the product.  A
# category is the layer of the edit a change lands in; it is what the
# blast radius is counted in.

DIALOGUE = "dialogue"
SEQUENCE = "sequence"
CAPTIONS = "captions"
GRAPHICS = "graphics"
COLOR = "color"
AUDIO = "audio"

CATEGORIES = (DIALOGUE, SEQUENCE, CAPTIONS, GRAPHICS, COLOR, AUDIO)

OP_CATEGORIES: dict[str, str] = {
    # The algebra operations.  A plan touches a category; the category is
    # what the blast radius is counted in.
    "angle_plan": SEQUENCE,
    "audio.automation": AUDIO,
    "audio.fade": AUDIO,
    "audio.gain": AUDIO,
    "audio.pan": AUDIO,
    "audio_mix": AUDIO,
    "brand_asset": GRAPHICS,
    "broll.insert": SEQUENCE,
    "caption.style": CAPTIONS,
    "caption_fix": CAPTIONS,
    "clip.delete": SEQUENCE,
    "clip.insert": SEQUENCE,
    "clip.join": SEQUENCE,
    "clip.link": SEQUENCE,
    "clip.move": SEQUENCE,
    "clip.remove": SEQUENCE,
    "clip.replace": SEQUENCE,
    "clip.ripple": SEQUENCE,
    "clip.roll": SEQUENCE,
    "clip.set_enabled": SEQUENCE,
    "clip.set_property": SEQUENCE,
    "clip.slide": SEQUENCE,
    "clip.slip": SEQUENCE,
    "clip.smart_reframe": SEQUENCE,
    "clip.split": SEQUENCE,
    "clip.trim": SEQUENCE,
    "clip.unlink": SEQUENCE,
    "clip_lut": COLOR,
    "clip_marker.add": SEQUENCE,
    "clip_marker.delete": SEQUENCE,
    "color.qualifier.adjust": COLOR,
    "color.qualifier.sample": COLOR,
    "color.window.create": COLOR,
    "color.window.delete": COLOR,
    "color.window.modify": COLOR,
    "color.window.track": COLOR,
    "compound.create": SEQUENCE,
    "compound.flatten": SEQUENCE,
    "drop_fragment": SEQUENCE,
    "end_card": GRAPHICS,
    "grade": COLOR,
    "grade.node_add": COLOR,
    "grade.node_remove": COLOR,
    "grade.node_reorder": COLOR,
    "grade.node_set": COLOR,
    "hold.repair_window": SEQUENCE,
    "look": COLOR,
    "marker.add": SEQUENCE,
    "marker.delete": SEQUENCE,
    "mask.create": GRAPHICS,
    "mask.delete": GRAPHICS,
    "mask.edit": GRAPHICS,
    "mask.track": GRAPHICS,
    "motion_graphic": GRAPHICS,
    "multicam.angle": SEQUENCE,
    "multicam.select_angle": SEQUENCE,
    "music_selection": AUDIO,
    "overlay.add": GRAPHICS,
    "overlay.entry_motion": GRAPHICS,
    "overlay.position": GRAPHICS,
    "overlay.remove": GRAPHICS,
    "overlay.replace": GRAPHICS,
    "overlay.resize": GRAPHICS,
    "overlay.swap_pixels": GRAPHICS,
    "picture.keyframe": SEQUENCE,
    "redraw_closer": SEQUENCE,
    "reel.opening": SEQUENCE,
    "retime": SEQUENCE,
    "retime.segments": SEQUENCE,
    "shot_selection": SEQUENCE,
    "sound_effect": AUDIO,
    "span_retime": SEQUENCE,
    "speech_selection": DIALOGUE,
    "story_pacing": SEQUENCE,
    "subject_effect": GRAPHICS,
    "track.add": SEQUENCE,
    "track.delete": SEQUENCE,
    "track.rename": SEQUENCE,
    "track.reorder": SEQUENCE,
    "transform_override": SEQUENCE,
    "transition": SEQUENCE,
    "visual_effect": GRAPHICS,
    "voice_isolation": AUDIO,
}
"""The category each operation touches.

The vocabulary is COMPLETE over the operation registry: an operation
with no category would make the blast radius undercount, and an
undercount is a plan that looks smaller than it is.  `plan_change` is
the one polymorphic entry - it carries the category of the operation it
plans, read from `params.operation_type`.
"""

PLAN_CHANGE = "plan_change"
"""The wrapper operation that carries a planning decision."""


class CandidateError(ValueError):
    """A candidate set that cannot be judged, chosen or recorded."""


# ── What the operation registry knows about one operation ────────────


def _op_facts(op_id: str) -> dict[str, Any]:
    """The registry facts about one operation.

    `known` is False for an operation the algebra does not declare: the
    candidate cannot be judged against a registry that has never heard of
    it, and saying so is better than treating it as free.
    """
    from library.tools import edit_algebra

    entry = edit_algebra.status(op_id)
    if entry is None:
        return {
            "op": op_id,
            "known": False,
            "implemented": False,
            "production_enabled": False,
            "inverse": None,
            "irreversible_reason": "",
            "safety_class": edit_algebra.SAFETY_UNREPRESENTABLE,
        }
    inverse = dict(entry.inverse_recovery or {})
    return {
        "op": op_id,
        "known": True,
        "implemented": bool(entry.compilable),
        "production_enabled": bool(entry.production_enabled),
        "inverse": inverse.get("op"),
        "self_inverse": bool(inverse.get("self_inverse")),
        "irreversible_reason": str(inverse.get("irreversible_reason") or ""),
        "safety_class": entry.safety_class
        or edit_algebra.SAFETY_UNREPRESENTABLE,
    }


def _operations(treatment: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The operations a candidate declares, in their declared order."""
    return [dict(op) for op in treatment.get("operations") or ()
            if isinstance(op, Mapping)]


def _op_name(operation: Mapping[str, Any]) -> str:
    """The algebra operation one declared operation names.

    A planning operation is carried as `plan_change` and names the
    operation it plans in `params.operation_type`; a structural edit
    inside a `reel.opening` names its ops in `edits`.  A candidate that
    names none carries an empty op, and the risk assessment says so
    rather than guessing.
    """
    name = str(operation.get("op") or "")
    if name == PLAN_CHANGE:
        params = operation.get("params")
        if isinstance(params, Mapping):
            planned = str(params.get("operation_type") or "")
            if planned:
                return planned
        return ""
    if name:
        return name
    edits = operation.get("edits")
    if isinstance(edits, list) and edits:
        names = {str(edit.get("op") or "") for edit in edits
                 if isinstance(edit, Mapping)}
        names.discard("")
        if len(names) == 1:
            return names.pop()
    return ""


def _nested_ops(operation: Mapping[str, Any]) -> list[str]:
    """Every algebra op one declared operation reaches, nested included.

    A `plan_change` contributes the operation it plans, not the wrapper:
    the wrapper is how a planning decision is carried, and the plan is
    what it changes.
    """
    names: list[str] = []
    name = _op_name(operation)
    if name:
        names.append(name)
    edits = operation.get("edits")
    if isinstance(edits, list):
        for edit in edits:
            if not isinstance(edit, Mapping):
                continue
            nested = str(edit.get("op") or "")
            if nested and nested not in names:
                names.append(nested)
    return names


def _categories(treatment: Mapping[str, Any]) -> list[str]:
    """The categories a candidate touches, from its own declaration.

    A candidate may declare its categories; when it does not, they are
    derived from the operations it names.  A category nobody can derive
    is not claimed: an operation the vocabulary does not know touches
    nothing the engine can count, and the risk assessment names it.
    """
    declared = [str(name) for name in treatment.get("categories") or ()
                if str(name)]
    if declared:
        return sorted(set(declared))
    found: set[str] = set()
    for operation in _operations(treatment):
        for name in _nested_ops(operation):
            category = OP_CATEGORIES.get(name)
            if category:
                found.add(category)
    return sorted(found)


# ── The measurable constraints the engine can compute ─────────────────
#
# A constraint names a MEASUREMENT of the candidate's own declared
# effect.  The engine runs it; it never asks the model what the answer
# is.  A constraint whose measurement the candidate does not carry is
# `not_measured`, which is not a pass.


def _check_within_target_duration(candidate: Mapping[str, Any],
                                  params: Mapping[str, Any]) -> dict[str, Any]:
    """The candidate's declared duration against a target window."""
    target = params.get("target_seconds")
    tolerance = params.get("tolerance_seconds", 0)
    if target is None:
        return {"verdict": NOT_MEASURED,
                "reason": "no target duration was declared"}
    duration = candidate.get("duration_seconds")
    if duration is None:
        return {"verdict": NOT_MEASURED,
                "reason": "the candidate declares no resulting duration"}
    try:
        target = float(target)
        tolerance = float(tolerance)
        duration = float(duration)
    except (TypeError, ValueError):
        return {"verdict": NOT_MEASURED,
                "reason": "the duration or the target is not a number"}
    delta = abs(duration - target)
    if delta <= tolerance:
        return {"verdict": PASS,
                "reason": (f"{duration:g}s is within {tolerance:g}s of "
                           f"{target:g}s")}
    return {"verdict": FAIL,
            "reason": (f"{duration:g}s is {delta:g}s from the {target:g}s "
                       f"target, outside the {tolerance:g}s window")}


def _check_touches_only(candidate: Mapping[str, Any],
                        params: Mapping[str, Any]) -> dict[str, Any]:
    """The candidate touches none of the categories it must not touch."""
    forbidden = [str(name) for name in params.get("categories") or ()
                 if str(name)]
    if not forbidden:
        return {"verdict": NOT_MEASURED,
                "reason": "no protected category was named"}
    touched = set(_categories(candidate))
    clashes = sorted(touched.intersection(forbidden))
    if clashes:
        return {"verdict": FAIL,
                "reason": ("the candidate touches protected "
                           f"categor{'y' if len(clashes) == 1 else 'ies'} "
                           + ", ".join(clashes))}
    return {"verdict": PASS,
            "reason": "the candidate touches no protected category"}


def _check_operation_available(candidate: Mapping[str, Any],
                               params: Mapping[str, Any]) -> dict[str, Any]:
    """Every operation the candidate names can be carried out."""
    names = _candidate_ops(candidate)
    if not names:
        return {"verdict": NOT_MEASURED,
                "reason": "the candidate names no operation"}
    unknown = [name for name in names if not _op_facts(name)["known"]]
    if unknown:
        return {"verdict": FAIL,
                "reason": ("the operation registry does not declare "
                           + ", ".join(sorted(unknown)))}
    unimplemented = [name for name in names
                     if not _op_facts(name)["implemented"]]
    if unimplemented:
        return {"verdict": FAIL,
                "reason": ("no executor carries "
                           + ", ".join(sorted(set(unimplemented))))}
    unproven = [name for name in names
                if not _op_facts(name)["production_enabled"]]
    if unproven:
        return {"verdict": NOT_MEASURED,
                "reason": ("not production-enabled yet: "
                           + ", ".join(sorted(set(unproven))))}
    return {"verdict": PASS,
            "reason": "every operation has a production-enabled executor"}


def _check_reversible(candidate: Mapping[str, Any],
                      params: Mapping[str, Any]) -> dict[str, Any]:
    """Every operation the candidate names can be undone."""
    names = _candidate_ops(candidate)
    if not names:
        return {"verdict": NOT_MEASURED,
                "reason": "the candidate names no operation"}
    irreversible = [name for name in names
                    if _op_facts(name)["irreversible_reason"]
                    or (not _op_facts(name)["inverse"]
                        and not _op_facts(name)["self_inverse"])]
    if irreversible:
        reasons = sorted({_op_facts(name)["irreversible_reason"]
                          for name in irreversible} - {""})
        suffix = f": {reasons[0]}" if reasons else ""
        return {"verdict": FAIL,
                "reason": ("cannot be undone: "
                           + ", ".join(sorted(set(irreversible))) + suffix)}
    return {"verdict": PASS, "reason": "every operation can be undone"}


CONSTRAINTS = {
    "within_target_duration": _check_within_target_duration,
    "touches_only": _check_touches_only,
    "operation_available": _check_operation_available,
    "reversible": _check_reversible,
}
"""The measurable constraints the engine computes, one enumeration."""


def _candidate_ops(candidate: Mapping[str, Any]) -> list[str]:
    """Every algebra op the candidate reaches, without duplicates."""
    names: list[str] = []
    for operation in _operations(candidate):
        for name in _nested_ops(operation):
            if name not in names:
                names.append(name)
    return names


# ── Building one candidate ───────────────────────────────────────────


def treatment(treatment_id: str, name: str, rationale: str,
              operations: Sequence[Mapping[str, Any]] = (), *,
              categories: Sequence[str] = (),
              elements: Sequence[Mapping[str, Any]] = (),
              evidence_refs: Sequence[str] = (),
              duration_seconds: float | None = None,
              criteria: Sequence[Mapping[str, Any]] = (),
              targets: Sequence[str] = ()) -> dict[str, Any]:
    """One candidate treatment, with its judgement and its risk computed.

    A treatment is a PLAN plus the reasoning that produced it: the
    operations it would carry out, the evidence it rests on, the criteria
    it is judged against and the categories it touches.  The evaluation
    and the risk are computed here so no caller can present an unjudged
    candidate as a judged one.
    """
    if not str(treatment_id or "").strip():
        raise CandidateError("a candidate needs an id")
    if not str(name or "").strip():
        raise CandidateError(f"candidate {treatment_id!r} needs a name")
    if not str(rationale or "").strip():
        raise CandidateError(
            f"candidate {treatment_id!r} needs a rationale: a plan nobody "
            "can weigh against another plan is not a candidate")
    candidate: dict[str, Any] = {
        "treatment_id": str(treatment_id),
        "name": str(name),
        "rationale": str(rationale),
        "operations": [dict(op) for op in operations
                       if isinstance(op, Mapping)],
        "categories": [str(item) for item in categories if str(item)],
        "elements": [dict(item) for item in elements
                     if isinstance(item, Mapping)],
        "evidence_refs": [str(item) for item in evidence_refs if str(item)],
        "targets": [str(item) for item in targets if str(item)],
        "duration_seconds": (float(duration_seconds)
                             if duration_seconds is not None else None),
    }
    candidate["evaluation"] = evaluate(candidate, criteria)
    candidate["risk"] = assess_risk(candidate)
    return candidate


def evaluate(candidate: Mapping[str, Any],
             criteria: Sequence[Mapping[str, Any]]
             ) -> list[dict[str, Any]]:
    """Judge one candidate against the criteria it is held to.

    A brief criterion carries the verdict the model reached; the engine
    records it with its reason and evidence and never supplies one.  A
    constraint is computed from the candidate's own declared effect; a
    constraint whose measurement is absent is `not_measured`, which is
    reported and never counted as satisfaction.
    """
    verdicts: list[dict[str, Any]] = []
    for index, criterion in enumerate(criteria or ()):
        if not isinstance(criterion, Mapping):
            raise CandidateError(f"criterion {index} must be an object")
        name = str(criterion.get("criterion") or "").strip()
        if not name:
            raise CandidateError(f"criterion {index} needs a name")
        kind = str(criterion.get("kind") or BRIEF)
        if kind not in CRITERION_KINDS:
            raise CandidateError(
                f"criterion {name!r} has kind {kind!r}; known kinds: "
                f"{list(CRITERION_KINDS)}")
        if kind == BRIEF:
            verdict = str(criterion.get("verdict") or "")
            if verdict not in VERDICTS:
                raise CandidateError(
                    f"criterion {name!r} carries verdict {verdict!r}; a "
                    f"brief criterion carries a verdict in {list(VERDICTS)}")
            reason = str(criterion.get("reason") or "").strip()
            if not reason:
                raise CandidateError(
                    f"criterion {name!r} has no reason: a verdict nobody "
                    "explained is not a judgement")
            verdicts.append({
                "criterion": name,
                "kind": BRIEF,
                "verdict": verdict,
                "reason": reason,
                "evidence_refs": [str(item)
                                  for item in criterion.get("evidence_refs") or ()
                                  if str(item)],
            })
            continue
        constraint = str(criterion.get("constraint") or "").strip()
        if constraint not in CONSTRAINTS:
            raise CandidateError(
                f"criterion {name!r} names constraint {constraint!r}; known "
                f"constraints: {sorted(CONSTRAINTS)}")
        params = criterion.get("params")
        if not isinstance(params, Mapping):
            params = {}
        outcome = CONSTRAINTS[constraint](candidate, params)
        verdicts.append({
            "criterion": name,
            "kind": CONSTRAINT,
            "constraint": constraint,
            "verdict": outcome["verdict"],
            "reason": outcome["reason"],
            "evidence_refs": [str(item)
                              for item in criterion.get("evidence_refs") or ()
                              if str(item)],
        })
    return verdicts


def satisfies(candidate: Mapping[str, Any]) -> bool:
    """Whether a candidate satisfies every criterion it was judged against.

    A candidate with NO criteria satisfies nothing and proves nothing:
    `not_measured` and `fail` both mean it did not achieve the goal.
    """
    verdicts = candidate.get("evaluation") or []
    if not verdicts:
        return False
    return all(row.get("verdict") == PASS for row in verdicts)


def unverified(candidate: Mapping[str, Any]) -> list[str]:
    """The criteria a candidate could not be measured against."""
    return [str(row.get("criterion"))
            for row in candidate.get("evaluation") or ()
            if row.get("verdict") == NOT_MEASURED]


# ── The risk and reversibility of one candidate ──────────────────────


def assess_risk(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Estimate the risk of one candidate and whether it can be undone.

    The estimate is read off the operations, never asserted by the
    candidate's own rationale:

    * `high` - an operation cannot be undone, or the registry does not
      carry it, or it has no executor;
    * `moderate` - the most restrictive safety class among the
      operations is structural or complex, or the candidate touches more
      than one category;
    * `low` - otherwise.
    """
    from library.tools import edit_algebra

    names = _candidate_ops(candidate)
    reasons: list[str] = []
    irreversible: list[dict[str, str]] = []
    unknown: list[str] = []
    unimplemented: list[str] = []
    reversible = True
    classes: list[str] = []

    for name in names:
        facts = _op_facts(name)
        if not facts["known"]:
            unknown.append(name)
            reversible = False
            continue
        if not facts["implemented"]:
            unimplemented.append(name)
        classes.append(facts["safety_class"])
        if facts["inverse"] or facts["self_inverse"]:
            continue
        reversible = False
        irreversible.append({"op": name,
                             "reason": facts["irreversible_reason"]
                             or "the registry declares no inverse"})

    if unknown:
        reasons.append("the operation registry does not declare "
                       + ", ".join(sorted(set(unknown))))
    if unimplemented:
        reasons.append("no executor carries "
                       + ", ".join(sorted(set(unimplemented))))
    if irreversible:
        reasons.append("cannot be undone: "
                       + ", ".join(sorted({row["op"] for row in irreversible})))

    categories = _categories(candidate)
    restrictive = [name for name in classes
                   if name in (edit_algebra.SAFETY_STRUCTURAL,
                               edit_algebra.SAFETY_COMPLEX_LOCALIZED,
                               edit_algebra.SAFETY_UNREPRESENTABLE)]
    if restrictive:
        reasons.append("the operations carry the "
                       + " / ".join(sorted(set(restrictive))) + " safety class")
    if len(categories) > 1:
        reasons.append("touches " + ", ".join(categories))

    if unknown or unimplemented or irreversible:
        level = HIGH
    elif restrictive or len(categories) > 1:
        level = MODERATE
    else:
        level = LOW
    if not reasons:
        reasons.append("every operation can be undone and touches one "
                       "category")
    return {
        "level": level,
        "reasons": reasons,
        "reversible": reversible,
        "irreversible_operations": irreversible,
        "unknown_operations": sorted(set(unknown)),
        "safety_class": edit_algebra.request_safety_class(names) or "",
        "categories": categories,
        "operation_count": len(names),
    }


# ── The candidate set ────────────────────────────────────────────────


def build(request: str, treatments: Sequence[Mapping[str, Any]] = (), *,
          criteria: Sequence[Mapping[str, Any]] = (),
          no_change_reason: str = "",
          no_change_evidence: Sequence[str] = ()) -> dict[str, Any]:
    """The candidate set for one subjective request.

    A set with no treatments and a `no_change_reason` is the explicit
    conclusion that the existing material is already adequate.  A set
    with treatments compares them.  Exactly one of the two, never
    neither: a set with neither says nothing at all.
    """
    reason = str(no_change_reason or "").strip()
    rows = [dict(item) for item in treatments or ()
            if isinstance(item, Mapping)]
    if rows and reason:
        raise CandidateError(
            "a candidate set either offers treatments or concludes that no "
            "change is needed, never both")
    if not rows and not reason:
        raise CandidateError(
            "a candidate set needs at least one treatment, or the reason "
            "the existing material is already adequate")
    seen: set[str] = set()
    for row in rows:
        treatment_id = str(row.get("treatment_id") or "")
        if not treatment_id:
            raise CandidateError("every candidate needs a treatment_id")
        if treatment_id in seen:
            raise CandidateError(
                f"two candidates share the id {treatment_id!r}")
        seen.add(treatment_id)
    return {
        "format": FORMAT,
        "request": str(request or ""),
        "treatments": rows,
        "criteria": [dict(item) for item in criteria or ()
                     if isinstance(item, Mapping)],
        "decision": NO_CHANGE if reason else "",
        "no_change": ({"reason": reason,
                       "evidence_refs": [str(item)
                                         for item in no_change_evidence or ()
                                         if str(item)]}
                      if reason else None),
    }


def is_no_change(candidate_set: Mapping[str, Any]) -> bool:
    """Whether a candidate set concludes that nothing should change."""
    if candidate_set.get("decision") == NO_CHANGE:
        return True
    return bool((candidate_set.get("no_change") or {}).get("reason"))


def no_change(request: str, reason: str, *,
              evidence_refs: Sequence[str] = (),
              criteria: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """The explicit conclusion that the existing material is adequate.

    The conclusion carries its reason and the evidence it rests on, so a
    reader can tell a considered "leave it" from a plan that never
    happened.
    """
    text = str(reason or "").strip()
    if not text:
        raise CandidateError(
            "a no-change conclusion needs a reason: 'nothing to do' "
            "without one reads as a plan nobody made")
    return build(request, (), criteria=criteria,
                 no_change_reason=text, no_change_evidence=evidence_refs)


# ── Comparing the candidates ─────────────────────────────────────────


def _minimal_key(candidate: Mapping[str, Any]) -> tuple:
    """The order key for "least change that still achieves the goal"."""
    categories = _categories(candidate)
    return (len(categories), len(_operations(candidate)),
            str(candidate.get("treatment_id") or ""))


def compare(candidate_set: Mapping[str, Any]) -> dict[str, Any]:
    """Order the candidates and say which one the evidence prefers.

    A candidate that satisfies every criterion is ELIGIBLE; one that
    fails a criterion is not, whatever its rationale says; one that could
    not be measured is UNVERIFIED and is neither preferred nor rejected.
    Among the eligible candidates the least change wins: fewest
    categories touched, then fewest operations, then the id - a total
    order, so the answer does not depend on the order the candidates
    arrived in.
    """
    eligible: list[dict[str, Any]] = []
    ineligible: list[dict[str, Any]] = []
    unverified_ids: list[str] = []
    for candidate in candidate_set.get("treatments") or ():
        treatment_id = str(candidate.get("treatment_id") or "")
        verdicts = candidate.get("evaluation") or []
        failures = [str(row.get("criterion")) for row in verdicts
                    if row.get("verdict") == FAIL]
        missing = unverified(candidate)
        if missing:
            unverified_ids.append(treatment_id)
        if not verdicts:
            ineligible.append({"treatment_id": treatment_id,
                               "reasons": ["no criteria to satisfy"]})
            continue
        if failures:
            ineligible.append({"treatment_id": treatment_id,
                               "reasons": [f"failed: {name}"
                                           for name in failures]})
            continue
        if missing:
            ineligible.append({"treatment_id": treatment_id,
                               "reasons": [f"not measured: {name}"
                                           for name in missing]})
            continue
        eligible.append(dict(candidate))
    ordered = sorted(eligible, key=_minimal_key)
    return {
        "order": [str(row.get("treatment_id")) for row in ordered],
        "eligible": [str(row.get("treatment_id")) for row in ordered],
        "ineligible": ineligible,
        "unverified": unverified_ids,
        "recommended": (str(ordered[0].get("treatment_id"))
                        if ordered else None),
        "minimal": (str(ordered[0].get("treatment_id"))
                    if ordered else None),
    }


# ── Choosing one, or combining several ──────────────────────────────


def _treatment(candidate_set: Mapping[str, Any],
               treatment_id: str) -> dict[str, Any]:
    for candidate in candidate_set.get("treatments") or ():
        if str(candidate.get("treatment_id")) == str(treatment_id):
            return dict(candidate)
    raise CandidateError(
        f"no candidate {treatment_id!r} in this set: "
        + ", ".join(sorted(str(row.get("treatment_id"))
                           for row in candidate_set.get("treatments") or ())))


def select(candidate_set: Mapping[str, Any], treatment_id: str, *,
           overruled_by: str = "", override_reason: str = "") -> dict[str, Any]:
    """Choose one candidate.

    Choosing is an ACT and it says which candidate was chosen and what
    it was chosen over.  A candidate the evidence rules out cannot be
    chosen quietly: the caller must name who overrules the evidence and
    why, and both go on the decision, so a reader can see that the
    choice went against the measurement rather than with it.
    """
    candidate = _treatment(candidate_set, treatment_id)
    comparison = compare(candidate_set)
    blocking = [row for row in comparison["ineligible"]
                if row["treatment_id"] == str(treatment_id)]
    decision: dict[str, Any] = {
        "decision": SELECTED,
        "treatment_id": str(treatment_id),
        "name": candidate.get("name") or "",
        "rationale": candidate.get("rationale") or "",
        "operations": copy.deepcopy(candidate.get("operations") or []),
        "elements": copy.deepcopy(candidate.get("elements") or []),
        "categories": _categories(candidate),
        "chosen_over": [row for row in comparison["eligible"]
                        if row != str(treatment_id)],
        "unverified": unverified(candidate),
    }
    if blocking:
        if not overruled_by:
            raise CandidateError(
                f"candidate {treatment_id!r} cannot be chosen: "
                + "; ".join(blocking[0]["reasons"])
                + ". Name who overrules the evidence and why to choose it "
                "anyway.")
        decision["overruled"] = blocking[0]["reasons"]
        decision["overruled_by"] = str(overruled_by)
        decision["override_reason"] = str(override_reason or "")
    return decision


def _elements(candidate: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The elements a candidate offers, each addressable on its own."""
    rows = [dict(item) for item in candidate.get("elements") or ()
            if isinstance(item, Mapping)]
    if rows:
        return rows
    # A candidate that declares no elements still offers its operations as
    # elements: one element per operation, in the declared order.
    elements = []
    for index, operation in enumerate(_operations(candidate)):
        name = _op_name(operation)
        elements.append({
            "element_id": f"{candidate.get('treatment_id')}:op{index}",
            "category": OP_CATEGORIES.get(name, ""),
            "op": name,
            "operation": operation,
            "target": str(operation.get("target")
                          or operation.get("reel")
                          or candidate.get("treatment_id") or ""),
        })
    return elements


def combine(candidate_set: Mapping[str, Any],
            picks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Combine compatible elements from several candidates.

    Two elements conflict when they act on the SAME target, or when they
    make different choices about the SAME category - two candidates'
    answers to one question contradict each other and cannot both stand.
    The refusal names the conflicting elements; a combination that
    conflicts is refused rather than silently resolved.
    """
    if not picks:
        raise CandidateError("a combination needs at least one element")
    chosen: list[dict[str, Any]] = []
    by_target: dict[str, str] = {}
    by_category: dict[str, str] = {}
    for index, pick in enumerate(picks):
        if not isinstance(pick, Mapping):
            raise CandidateError(f"pick {index} must be an object")
        treatment_id = str(pick.get("treatment_id") or "")
        candidate = _treatment(candidate_set, treatment_id)
        element_id = str(pick.get("element_id") or "")
        match = None
        for element in _elements(candidate):
            if str(element.get("element_id")) == element_id:
                match = element
                break
        if match is None:
            raise CandidateError(
                f"candidate {treatment_id!r} has no element {element_id!r}: "
                + ", ".join(str(item.get("element_id"))
                            for item in _elements(candidate)))
        target = str(match.get("target") or "")
        category = str(match.get("category") or "")
        if target and target in by_target:
            raise CandidateError(
                f"elements {by_target[target]!r} and {element_id!r} both "
                f"act on {target!r}: they cannot both stand")
        if category and category in by_category:
            raise CandidateError(
                f"elements {by_category[category]!r} and {element_id!r} "
                f"both choose the {category!r}: two answers to one "
                "question contradict each other")
        if target:
            by_target[target] = element_id
        if category:
            by_category[category] = element_id
        chosen.append({**dict(match),
                       "treatment_id": treatment_id})
    operations = [dict(item["operation"]) for item in chosen
                  if isinstance(item.get("operation"), Mapping)]
    return {
        "decision": COMBINED,
        "elements": chosen,
        "operations": operations,
        "categories": sorted({str(item.get("category"))
                              for item in chosen if item.get("category")}),
        "treatment_ids": sorted({str(item.get("treatment_id"))
                                 for item in chosen}),
    }


# ── The project-level decision record ────────────────────────────────


def to_editorial_decisions(candidate_set: Mapping[str, Any], *,
                           intent_id: str = "") -> list:
    """The candidate set in the shared editorial-decision vocabulary.

    `editorial_decisions` owns the layers of ONE decision.  This
    projection puts EVERY candidate into that vocabulary - the ones that
    lost as much as the one that won - so the alternatives are readable
    as decisions and not only as rows in this module's own record.  A
    candidate with no operation is a candidate, not a decision yet: it
    stays in the candidate state, and the compilation refuses it until
    somebody approves it.
    """
    from library.tools import editorial_decisions

    rows = []
    for candidate in candidate_set.get("treatments") or ():
        operations = []
        for operation in candidate.get("operations") or ():
            operations.append(editorial_decisions.DecisionOperation(
                op=str(operation.get("op") or ""),
                params=dict(operation.get("params") or {}),
                target=dict(operation.get("anchor") or {}),
                effect=str(operation.get("text") or "")))
        criteria = []
        for row in candidate.get("evaluation") or ():
            name = str(row.get("criterion") or "")
            if not name:
                continue
            criteria.append({
                "criterion": name,
                "measure": str(row.get("constraint") or name),
                "verdict": str(row.get("verdict") or ""),
                "reason": str(row.get("reason") or ""),
            })
        rows.append(editorial_decisions.EditorialDecision(
            decision_id=str(candidate.get("treatment_id") or ""),
            intent_id=str(intent_id or candidate_set.get("request") or ""),
            statement=str(candidate.get("rationale")
                          or candidate.get("name") or ""),
            state=editorial_decisions.CANDIDATE,
            stated_by="model",
            intended_effect=str(candidate.get("name") or ""),
            evidence_ids=tuple(str(item)
                               for item in candidate.get("evidence_refs") or ()),
            evaluation_criteria=tuple(criteria),
            operations=tuple(operations)))
    return rows


def _decision_refs(candidate_set: Mapping[str, Any]) -> dict[str, Any]:
    """Every candidate and its reasoning, for the decision record.

    The alternatives that LOST are kept with the same care as the one
    that won: a decision nobody can reconstruct the reasoning of is a
    decision nobody can revisit.
    """
    return {
        "format": FORMAT,
        "request": candidate_set.get("request") or "",
        "criteria": copy.deepcopy(candidate_set.get("criteria") or []),
        "no_change": copy.deepcopy(candidate_set.get("no_change")),
        "treatments": [
            {
                "treatment_id": row.get("treatment_id"),
                "name": row.get("name"),
                "rationale": row.get("rationale"),
                "categories": _categories(row),
                "evaluation": copy.deepcopy(row.get("evaluation") or []),
                "risk": copy.deepcopy(row.get("risk") or {}),
                "evidence_refs": list(row.get("evidence_refs") or []),
            }
            for row in candidate_set.get("treatments") or ()
        ],
        "decisions": [decision.to_dict() for decision
                      in to_editorial_decisions(candidate_set)],
    }


def record(project: os.PathLike | str, candidate_set: Mapping[str, Any],
           decision: Mapping[str, Any], *, author: str = "ren",
           reason: str = "") -> dict[str, Any]:
    """Keep the candidate set and the decision as a project-level record.

    The record is a creative revision - the project's canonical decision
    ledger - whose `refs` carry the WHOLE candidate set, so the
    alternatives that lost survive the decision that won.  Its
    `operations` are the chosen candidate's operations, and a no-change
    conclusion records none, which is the honest count.
    """
    from library.tools import creative_revision

    if not isinstance(decision, Mapping) or not decision:
        raise CandidateError("a recorded decision needs a decision")
    kind = str(decision.get("decision") or "")
    if kind not in DECISIONS:
        raise CandidateError(
            f"unknown decision {kind!r}; known decisions: {list(DECISIONS)}")
    operations = copy.deepcopy(decision.get("operations") or [])
    if kind == NO_CHANGE:
        operations = []
    affected = [str(item) for item in decision.get("categories") or ()]
    for element in decision.get("elements") or ():
        target = str(element.get("target") or "")
        if target:
            affected.append(target)
    approval = {"decision": kind, "by": str(author or "ren"),
                "why": str(reason or "")}
    return creative_revision.create(
        project,
        author=str(author or "ren"),
        intent=str(candidate_set.get("request") or ""),
        operations=operations,
        affected_entities=sorted(set(affected)),
        approval=approval,
        refs={"candidates": _decision_refs(candidate_set),
              "candidate_decision": {
                  "decision": kind,
                  "treatment_id": str(decision.get("treatment_id") or ""),
                  "treatment_ids": list(decision.get("treatment_ids") or []),
              }},
        status="recorded")


def read_records(project: os.PathLike | str) -> list[dict[str, Any]]:
    """The candidate decision records this project holds.

    The reader for what `record` wrote: a record nobody can read back is
    a record nobody can act on.
    """
    from library.tools import creative_revision

    rows = []
    for revision in creative_revision.list_revisions(project):
        refs = revision.get("refs") or {}
        candidates = refs.get("candidates")
        if not isinstance(candidates, Mapping):
            continue
        rows.append({
            "revision_id": revision.get("revision_id"),
            "request": candidates.get("request") or "",
            "status": revision.get("status"),
            "approval": dict(revision.get("approval") or {}),
            "candidate_decision": dict(refs.get("candidate_decision") or {}),
            "no_change": candidates.get("no_change"),
            "treatments": list(candidates.get("treatments") or []),
            "decisions": list(candidates.get("decisions") or []),
        })
    return rows
