"""EDIT-020: what each camera angle MEASURES against, and how to choose one.

Ren could place an angle and could refuse an angle it had no plan for
(`library/tools/reel_angle_plan.py` resolves a DECLARED angle plan and
refuses the build rather than falling back to the master's stacked
picture rows).  What it could not do was CHOOSE one, because choosing an
angle is a judgement and there was nowhere for the judgement to be made
from: no module read the speaker, the action, the eyeline or the
continuity of the candidates side by side.

This module is that table.

**Four criteria, and where each one's evidence comes from.**

- **Speaker identity** - is the person talking in this passage the person
  this angle shows?  The evidence is the angle's own measured subject
  list and the passage's speaker.
- **Action visibility** - does this angle show what is being talked
  about?  The evidence is the angle's measured actions and the passage's
  actions.
- **Eyeline** - which way does the subject face, and does this angle
  agree with the shot before it?  The evidence is the measured subject
  centre (`face_presence.face_center_x`), read through
  `library/tools/subject_framing.subject_center_reading` and
  `library/tools/continuity.direction_cell_for_cut` - the same readers
  every other consumer uses, so the "possible line cross" reading stays
  POSSIBLE.  A flip is also what shot/reverse-shot looks like, and
  `library/tools/continuity.py` has always said so.
- **Continuity** - is this the same scene, and a different camera from
  the shot before it?  The evidence is the angle's measured scene and
  camera identity.

**What it decides and what it does not.**  It MEASURES each candidate
against each criterion and orders the candidates by how many criteria
they support.  The order is the only ranking in the repository that is
derived from the criteria themselves rather than from a house style, and
it is overridable: `policy.angle.order` names the criteria in the order
the project wants them weighed.  A candidate that supports no criterion
and one that supports every criterion are not close, and the criteria
are the four the item names, so ordering by them is not taste.

**The verdict a criterion returns.**  `supports`, `opposes` or
`undetermined` - never a number and never a probability.  A criterion
whose evidence was never measured says `undetermined`, and a candidate
whose evidence is missing is never counted as one that supports.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from library.tools.continuity import direction_cell_for_cut

SUPPORTS = "supports"
OPPOSES = "opposes"
UNDETERMINED = "undetermined"

VERDICTS = (SUPPORTS, OPPOSES, UNDETERMINED)

#: The criteria, in the order the item names them.  The order is the
#: default weighing; a project may reorder it through its editorial
#: policy, and may not add a criterion this module cannot measure.
CRITERIA = ("speaker_identity", "action_visibility", "eyeline", "continuity")

CRITERION_TEXT = {
    "speaker_identity":
        "does this angle show the person who is speaking in this passage",
    "action_visibility":
        "does this angle show what the passage is talking about",
    "eyeline":
        "does this angle agree with the shot before it about which way the "
        "subject faces",
    "continuity":
        "is this the same scene, and a different camera from the shot "
        "before it",
}


class AngleEvidenceError(ValueError):
    """An angle candidate or its context could not be read."""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _names(value: Any) -> set[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return set()
    return {_text(row).lower() for row in value if _text(row)}


def _overlaps(a_start: float, a_end: float, b_start: float, b_end: float) -> bool:
    return a_end > b_start and a_start < b_end


def _measure_speaker_identity(candidate: Mapping[str, Any],
                              context: Mapping[str, Any]) -> dict[str, Any]:
    speaking = _names(context.get("speaking_speaker"))
    shown = _names(candidate.get("speakers"))
    if not speaking:
        return {"criterion": "speaker_identity", "verdict": UNDETERMINED,
                "detail": "the passage names no speaker"}
    if not shown:
        return {"criterion": "speaker_identity", "verdict": UNDETERMINED,
                "detail": "this angle names no speaker"}
    if speaking & shown:
        return {"criterion": "speaker_identity", "verdict": SUPPORTS,
                "detail": f"this angle shows {', '.join(sorted(speaking & shown))}"}
    return {"criterion": "speaker_identity", "verdict": OPPOSES,
            "detail": f"this angle shows {', '.join(sorted(shown))}, "
                      f"and {', '.join(sorted(speaking))} is speaking"}


def _measure_action_visibility(candidate: Mapping[str, Any],
                               context: Mapping[str, Any]) -> dict[str, Any]:
    """Does this angle cover the moment the action happens?

    The evidence is the MEASURED action windows of the passage's clip -
    the vision pass writes one window per observed action, with its own
    time bounds - and the candidate's own source range.  An angle whose
    range covers the action window SHOWS the action; an angle whose
    range is somewhere else does not.

    This is a range comparison rather than a comparison of two prose
    descriptions, because two cameras of the same moment are described
    in different words by a model that never saw either of them
    together, and a difference of wording is not a difference of what
    is happening.
    """
    windows = context.get("action_windows")
    if not isinstance(windows, (list, tuple)) or not windows:
        return {"criterion": "action_visibility", "verdict": UNDETERMINED,
                "detail": "the passage has no measured action window"}
    try:
        start = float(candidate["source_start"])
        end = float(candidate["source_end"])
    except (KeyError, TypeError, ValueError):
        return {"criterion": "action_visibility", "verdict": UNDETERMINED,
                "detail": "this angle has no measurable range"}
    for window in windows:
        if not isinstance(window, Mapping):
            continue
        try:
            window_start = float(window["start_seconds"])
            window_end = float(window["end_seconds"])
        except (KeyError, TypeError, ValueError):
            continue
        if end > window_start and start < window_end:
            description = _text(window.get("description"))
            return {"criterion": "action_visibility", "verdict": SUPPORTS,
                    "detail": (f"this angle covers the measured action"
                               + (f" ({description})" if description else ""))}
    return {"criterion": "action_visibility", "verdict": OPPOSES,
            "detail": "this angle covers none of the passage's measured "
                      "action windows"}


def _measure_eyeline(candidate: Mapping[str, Any],
                     context: Mapping[str, Any]) -> dict[str, Any]:
    """Does this angle agree with the shot before it about which way the
    subject faces?

    The reading is `library/tools/continuity.direction_cell_for_cut` -
    the ONE reader of the measured subject centre every other consumer
    uses, so the "possible line cross" reading stays POSSIBLE.  A flip
    is also what a shot/reverse-shot dialogue cut looks like, and
    `library/tools/continuity.py` has always said so.
    """
    previous = context.get("previous_angle")
    if not isinstance(previous, Mapping):
        return {"criterion": "eyeline", "verdict": UNDETERMINED,
                "detail": "there is no shot before this one to agree with"}
    indices = context.get("temporal_indices") or {}
    outgoing = {
        "clip_id": _text(previous.get("clip_id")),
        "source_start": previous.get("source_start"),
        "source_end": previous.get("source_end"),
    }
    incoming = {
        "clip_id": _text(candidate.get("clip_id")),
        "source_start": candidate.get("source_start"),
        "source_end": candidate.get("source_end"),
    }
    cell = direction_cell_for_cut(
        outgoing, incoming,
        indices.get(outgoing["clip_id"]) or {},
        indices.get(incoming["clip_id"]) or {})
    if cell == "unmeasured":
        return {"criterion": "eyeline", "verdict": UNDETERMINED,
                "detail": "one of the two shots has no measured subject "
                          "centre"}
    if "centre" in cell:
        return {"criterion": "eyeline", "verdict": SUPPORTS,
                "detail": "the subject is centred in one of the two shots, "
                          "so there is no side to disagree about"}
    if "potential line cross" in cell:
        return {"criterion": "eyeline", "verdict": OPPOSES,
                "detail": "this angle places the subject on the other side "
                          "of the frame from the shot before it - a "
                          "POSSIBLE line cross, which is also what "
                          "shot/reverse-shot looks like",
                "possible": True}
    return {"criterion": "eyeline", "verdict": SUPPORTS,
            "detail": "both shots place the subject on the same side of "
                      "the frame"}


def _measure_continuity(candidate: Mapping[str, Any],
                        context: Mapping[str, Any]) -> dict[str, Any]:
    previous = context.get("previous_angle")
    scene = _text(candidate.get("scene"))
    previous_scene = _text(previous.get("scene")) if isinstance(previous, Mapping) else ""
    camera = _text(candidate.get("camera_id"))
    previous_camera = (_text(previous.get("camera_id"))
                       if isinstance(previous, Mapping) else "")
    if previous_scene and scene and previous_scene != scene:
        return {"criterion": "continuity", "verdict": OPPOSES,
                "detail": f"this angle is {scene} and the shot before it is "
                          f"{previous_scene}"}
    if previous_camera and camera and camera == previous_camera:
        return {"criterion": "continuity", "verdict": OPPOSES,
                "detail": f"this is the same camera as the shot before it "
                          f"({camera})"}
    if not scene and not camera:
        return {"criterion": "continuity", "verdict": UNDETERMINED,
                "detail": "this angle names neither a scene nor a camera"}
    return {"criterion": "continuity", "verdict": SUPPORTS,
            "detail": "this angle continues the scene before it"}


_MEASURERS = {
    "speaker_identity": _measure_speaker_identity,
    "action_visibility": _measure_action_visibility,
    "eyeline": _measure_eyeline,
    "continuity": _measure_continuity,
}


def measure_candidate(candidate: Mapping[str, Any],
                      context: Mapping[str, Any], *,
                      criteria: Sequence[str] = CRITERIA) -> dict[str, Any]:
    """One angle candidate, measured against every criterion."""
    if not isinstance(candidate, Mapping):
        raise AngleEvidenceError("an angle candidate must be a mapping")
    angle_id = _text(candidate.get("angle_id"))
    if not angle_id:
        raise AngleEvidenceError("an angle candidate needs an angle_id")
    verdicts = []
    for name in criteria:
        measurer = _MEASURERS.get(name)
        if measurer is None:
            raise AngleEvidenceError(
                f"unknown angle criterion {name!r}; known criteria are "
                f"{list(CRITERIA)}")
        verdicts.append(measurer(candidate, context))
    supporting = sum(1 for row in verdicts if row["verdict"] == SUPPORTS)
    opposing = sum(1 for row in verdicts if row["verdict"] == OPPOSES)
    determinable = sum(1 for row in verdicts if row["verdict"] != UNDETERMINED)
    return {
        "angle_id": angle_id,
        "clip_id": _text(candidate.get("clip_id")),
        "label": _text(candidate.get("label")),
        "camera_id": _text(candidate.get("camera_id")),
        "scene": _text(candidate.get("scene")),
        "source_start": candidate.get("source_start"),
        "source_end": candidate.get("source_end"),
        "criteria": verdicts,
        "supporting": supporting,
        "opposing": opposing,
        "determinable": determinable,
        "undetermined": [row["criterion"] for row in verdicts
                         if row["verdict"] == UNDETERMINED],
    }


def mechanical_exclusions(candidate: Mapping[str, Any],
                          passage: Mapping[str, Any]) -> list[str]:
    """The reasons this angle cannot be placed, whatever the criteria say.

    These are not editorial.  An angle whose measured range does not
    cover the passage's range cannot carry the passage's words, and a
    candidate naming no clip cannot be placed at all.  A criterion that
    cannot be measured is never one of these: an unmeasured criterion is
    `undetermined`, and unmeasured is not a fault.
    """
    reasons = []
    if not _text(candidate.get("clip_id")):
        reasons.append("no_clip")
    try:
        start = float(candidate["source_start"])
        end = float(candidate["source_end"])
        passage_start = float(passage["source_start"])
        passage_end = float(passage["source_end"])
    except (KeyError, TypeError, ValueError):
        reasons.append("no_measurable_range")
        return reasons
    if end <= start:
        reasons.append("empty_range")
    if not _overlaps(start, end, passage_start, passage_end):
        reasons.append("range_does_not_cover_the_passage")
    return reasons


def rank_candidates(candidates: Sequence[Mapping[str, Any]],
                    context: Mapping[str, Any], *,
                    criteria: Sequence[str] = CRITERIA) -> dict[str, Any]:
    """Every candidate, measured and ordered.

    The order is the criteria's own order, heaviest first: a candidate
    that supports a criterion the one before it opposes comes first.  The
    comparison is over the criteria in order, so the first criterion
    decides between two candidates that agree on it, and the ones after
    it break a tie.  Nothing here is a score - a candidate is never
    "better by 0.4", it is ahead on named criteria and behind on named
    criteria.
    """
    measured = []
    excluded = []
    passage = context.get("passage") or {}
    for candidate in candidates or ():
        if not isinstance(candidate, Mapping):
            continue
        reasons = mechanical_exclusions(candidate, passage)
        row = measure_candidate(candidate, context, criteria=criteria)
        row["excluded"] = reasons
        if reasons:
            excluded.append(row)
        else:
            measured.append(row)

    order = list(criteria)

    def sort_key(row: Mapping[str, Any]):
        verdicts = {entry["criterion"]: entry["verdict"]
                    for entry in row["criteria"]}
        return tuple(0 if verdicts.get(name) == SUPPORTS
                     else (1 if verdicts.get(name) == OPPOSES else 2)
                     for name in order)

    measured.sort(key=sort_key)
    for position, row in enumerate(measured):
        row["position"] = position + 1
    return {
        "criteria": list(criteria),
        "candidates": measured,
        "excluded": excluded,
        "undetermined_criterion": CRITERION_TEXT,
    }


def criterion_order(policy: Any = None) -> tuple[str, ...]:
    """The order the criteria are weighed in.

    The project's `angle.order` slot names them; an undeclared slot
    reads as the item's own order, which is not a house style - it is
    the four criteria the item names.
    """
    declared = None
    if policy is not None:
        try:
            declared = policy.slot("angle.order")
        except Exception:  # noqa: BLE001 - an unreadable slot reads undeclared
            declared = None
    if not declared:
        return tuple(CRITERIA)
    if not isinstance(declared, (list, tuple)):
        raise AngleEvidenceError("angle.order must be a list of criterion names")
    names = tuple(_text(name) for name in declared)
    unknown = [name for name in names if name not in CRITERIA]
    if unknown:
        raise AngleEvidenceError(
            f"angle.order names {unknown}, which this module cannot "
            f"measure; known criteria are {list(CRITERIA)}")
    return names


def select_angle(candidates: Sequence[Mapping[str, Any]],
                 context: Mapping[str, Any], *,
                 policy: Any = None) -> dict[str, Any]:
    """The angles for one passage, measured, ordered and explained.

    The first candidate is the one the criteria support.  It is a
    RECOMMENDATION and the reasoning is beside it, because every
    criterion here is a judgement one level up from the measurement:
    an eyeline reading is a possible line cross, and the model that
    reads this table is the one that decides whether the flip was
    intended.
    """
    criteria = criterion_order(policy)
    ranked = rank_candidates(candidates, context, criteria=criteria)
    chosen = ranked["candidates"][0] if ranked["candidates"] else None
    return {
        "criteria": list(criteria),
        "candidates": ranked["candidates"],
        "excluded": ranked["excluded"],
        "chosen": chosen,
        "why": (
            f"{chosen['angle_id']} is ahead on "
            f"{chosen['supporting']} of {len(criteria)} criteria"
            if chosen else
            "no candidate could be placed on this passage"),
    }
