"""PLAN-003: grounding is a required planning stage.

A plan names TARGET ROLES - "the caption that misstates the number",
"the name bar", "the opening section".  Nothing may be staged until
every named role has been resolved to an actual source media item or a
timeline item, and the resolution carries that item's identity and its
source interval.  Four answers are kept apart, because they are four
different things and a reader that merges them reads a claim as a fact:

``claimed``
    The plan asserted an identity and no surface carries it.  The
    assertion is the run's own; nothing confirms it.
``observed``
    The surfaces carried SEVERAL candidates.  The role is grounded to a
    set, not to a thing, so no unique target was established.
``verified``
    Exactly one candidate, with its identity and its source interval.
``unresolved``
    Nothing was asserted, and nothing was found.

Two rules the stage keeps.

**A target that stays ambiguous is RE-QUERIED, not guessed.**  The
timeline surface is asked first; when it answers with more than one
candidate the evidence surface is asked again, and BOTH answers are
recorded in the receipt, so a reader can see what was asked and what
came back.  A role that is still ambiguous afterwards is ``observed``,
never ``verified``.

**A surface that was ASKED is not a surface that was READ.**  The
evidence attempt records whether a model was there to answer, because
the two states are different questions: a surface that answered "no"
says the thing is not there, and a surface nobody could read says
nothing was looked for.

**A destructive change needs a verified target.**  An operation that
removes, replaces or rewrites material is refused when the role it
names is not ``verified``: the plan says which thing, and the thing
has to BE the thing.  A role that is merely claimed or merely observed
is not a thing yet.

The surfaces are the ones the engine already has, and this module
stores nothing new:

- the RECORDED timeline (:mod:`library.tools.timeline_shadow`), read
  through the same clip records the verification path reads;
- the typed evidence query (:mod:`library.tools.evidence_query`);
- the occurrence identity (:mod:`library.tools.occurrence_identity`),
  which names WHICH time a repeated event happened.

The destructive vocabulary is the engine's own safety class
(:mod:`library.tools.edit_algebra`): an operation whose class is
``SAFETY_STRUCTURAL`` changes topology, timing or identity, and the
three that rewrite or swap material in place are added to it by name.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

FORMAT = "target_grounding/1"
"""The format of a grounding receipt."""

CLAIMED = "claimed"
OBSERVED = "observed"
VERIFIED = "verified"
UNRESOLVED = "unresolved"

STATES: tuple[str, ...] = (CLAIMED, OBSERVED, VERIFIED, UNRESOLVED)
"""The four states one target can be in.  Complete: an answer carries
exactly one."""


#: The operations that rewrite, replace or remove material in place, on
#: top of the structural class.  They destroy what they act on: the
#: words of a caption, the source of a clip, the angle of a moment, the
#: pixels of an overlay.
DESTRUCTIVE_IN_PLACE: frozenset[str] = frozenset({
    "clip.replace",
    "multicam.select_angle",
    "caption_fix",
    "overlay.remove",
    "overlay.replace",
})


def is_destructive(op_id: str) -> bool:
    """Whether an operation removes, replaces or rewrites material.

    The structural class is the engine's own: topology, timing and
    identity effects.  An operation the engine does not know is NOT
    destructive here - the router refuses it before it can act, and
    guessing a class would put a rule in this module that the engine
    does not have.
    """
    from library.tools import edit_algebra

    op = str(op_id or "")
    if op in DESTRUCTIVE_IN_PLACE:
        return True
    return edit_algebra.OP_SAFETY_CLASS.get(op) == edit_algebra.SAFETY_STRUCTURAL


# ── The surfaces ─────────────────────────────────────────────────────


def items_of(clips: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The recorded timeline's clips, as grounding MATERIALS.

    Each material carries the identity a later reader checks (the ids it
    was recorded under), where it lives (its row and its record span)
    and the SOURCE interval it was cut from (its file and its source
    frames).  A clip that carries no id is skipped: an item nobody can
    name cannot ground a target.

    **Two recordings of one clip are one material.**  A timeline that
    was rebuilt records the same clip under a new id, so the same lower
    third can carry two ids in two recorded generations.  Those are ONE
    target, not two candidates: the ids are kept, and the material is
    counted once.  Two DIFFERENT clips are two candidates, and a target
    that names both is ambiguous.
    """
    materials: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for clip in clips or ():
        if not isinstance(clip, Mapping):
            continue
        uid = str(clip.get("unique_id") or "").strip()
        if not uid:
            continue
        record_in = _int(clip.get("record_in"))
        record_out = _int(clip.get("record_out"))
        track_type = str(clip.get("track_type") or "")
        track_index = _int(clip.get("track_index"))
        row = _row_name(track_type, track_index)
        source = {
            "file": str(clip.get("source_file") or ""),
            "start_frame": _int(clip.get("source_in_frame")),
            "end_frame": _int(clip.get("source_out_frame")),
        }
        key = "|".join([
            str(clip.get("name") or ""), row,
            str(record_in), str(record_out),
            source["file"], str(source["start_frame"]),
            str(source["end_frame"]),
        ])
        material = materials.get(key)
        if material is None:
            material = {
                "material_key": key,
                "identities": [],
                "name": str(clip.get("name") or ""),
                "row": row,
                "track_type": track_type,
                "track_index": track_index,
                "record_span": (record_in, record_out),
                "source": source,
                "media_type": str(clip.get("media_type") or ""),
            }
            materials[key] = material
            order.append(key)
        if uid not in material["identities"]:
            material["identities"].append(uid)
    return [materials[key] for key in order]


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _row_name(track_type: str, track_index: int | None) -> str:
    if not track_type:
        return ""
    prefix = "V" if track_type.lower().startswith("v") else (
        "A" if track_type.lower().startswith("a") else track_type[:1].upper())
    return f"{prefix}{track_index}"


# ── The claim: what a plan says about a role ─────────────────────────


def _spans_of(claim: Mapping[str, Any]) -> list[tuple[int, int]]:
    """The record spans the claim names."""
    spans: list[tuple[int, int]] = []
    for span in (claim.get("record_spans") or ()):
        if isinstance(span, (list, tuple)) and len(span) == 2:
            low, high = _int(span[0]), _int(span[1])
            if low is not None and high is not None:
                spans.append((low, high))
    single = claim.get("record_span")
    if isinstance(single, (list, tuple)) and len(single) == 2:
        low, high = _int(single[0]), _int(single[1])
        if low is not None and high is not None:
            spans.append((low, high))
    return spans


def _names_of(claim: Mapping[str, Any]) -> list[str]:
    """The clip names the claim carries."""
    names: list[str] = []
    listed = claim.get("names")
    if isinstance(listed, (list, tuple)):
        names.extend(str(name) for name in listed if str(name))
    single = str(claim.get("name") or "")
    if single and single not in names:
        names.append(single)
    return names


def _asserts(claim: Mapping[str, Any]) -> bool:
    """Whether the claim asserts anything at all.

    A claim that names nothing is not a claim, and a target nobody
    asserted is UNRESOLVED rather than CLAIMED.
    """
    return bool(str(claim.get("unique_id") or "")
                or _names_of(claim)
                or _spans_of(claim)
                or isinstance(claim.get("event"), Mapping)
                or isinstance(claim.get("query"), Mapping))


def _timeline_candidates(claim: Mapping[str, Any],
                         items: Sequence[Mapping[str, Any]]
                         ) -> list[dict[str, Any]]:
    """The recorded timeline's items the claim names.

    A claim that names an identity matches that identity.  A claim that
    names a clip's name matches that name.  A claim that names a record
    span matches every item that OVERLAPS that span - a span is a
    region of the timeline, and the items in it are what the claim
    points at.
    """
    uid = str(claim.get("unique_id") or "")
    names = _names_of(claim)
    spans = _spans_of(claim)
    if not uid and not names and not spans:
        return []
    matched: list[dict[str, Any]] = []
    for item in items:
        if uid:
            if uid not in list(item.get("identities") or ()):
                continue
        elif names:
            if str(item.get("name") or "") not in names:
                continue
        else:
            if not _overlaps(item.get("record_span"), spans):
                continue
        matched.append(dict(item))
    # A row narrows a set that a broader claim left open.
    row = str(claim.get("row") or "")
    if row and len(matched) > 1:
        narrowed = [item for item in matched if str(item.get("row") or "") == row]
        if narrowed:
            matched = narrowed
    return matched


def _overlaps(span: Any, wanted: Sequence[tuple[int, int]]) -> bool:
    if not isinstance(span, (list, tuple)) or len(span) != 2:
        return False
    low, high = span
    if low is None or high is None:
        return False
    for start, end in wanted:
        if low < end and high > start:
            return True
    return False


def _evidence_candidates(claim: Mapping[str, Any],
                         model: Mapping[str, Any] | None
                         ) -> tuple[list[dict[str, Any]], str]:
    """The evidence surface's answer for the claim, and why.

    Two routes, both the engine's own: an OCCURRENCE target names
    which time a repeated event happened (the occurrence identity), and
    a QUERY target names a typed measurement (the evidence query).

    A model nobody read answers nothing: the surface was not asked, so
    it cannot have refused.
    """
    if not isinstance(model, Mapping) or not model:
        return [], ""
    event = claim.get("event")
    if isinstance(event, Mapping):
        return _occurrence_candidates(model, event)
    query = claim.get("query")
    if isinstance(query, Mapping):
        return _query_candidates(model, query), ""
    return [], ""


def _occurrence_candidates(model: Mapping[str, Any],
                            target: Mapping[str, Any]
                            ) -> tuple[list[dict[str, Any]], str]:
    from library.tools import occurrence_identity

    try:
        answer = occurrence_identity.resolve_target(model, dict(target))
    except Exception as exc:  # noqa: BLE001 - a refusal is an answer
        return [], f"the occurrence could not be resolved: {exc}"
    status = str(answer.get("status") or "")
    if status != "resolved":
        return [], str(answer.get("reason") or "the occurrence did not resolve")
    receipt = answer.get("receipt")
    if not isinstance(receipt, Mapping):
        return [], "the occurrence resolved without a receipt"
    interval = receipt.get("interval")
    interval = interval if isinstance(interval, Mapping) else {}
    return [{
        "event_id": str(receipt.get("event_id") or ""),
        "occurrence": {
            "class": str(receipt.get("event_class") or ""),
            "index": receipt.get("occurrence_index"),
            "key": str(receipt.get("occurrence_key") or ""),
        },
        "subject_id": receipt.get("subject_id"),
        "interval": {
            "clock_id": interval.get("clock_id"),
            "start": interval.get("start"),
            "end": interval.get("end"),
            "start_s": interval.get("start_s"),
            "end_s": interval.get("end_s"),
        },
        "evidence_ids": [str(eid) for eid in (receipt.get("evidence_ids") or ())],
    }], ""


def _query_candidates(model: Mapping[str, Any],
                      query: Mapping[str, Any]) -> list[dict[str, Any]]:
    from library.tools import evidence_query

    try:
        answer = evidence_query.evidence_for(model, dict(query))
    except Exception:  # noqa: BLE001 - a refusal is an answer
        return []
    rows = answer.get("results")
    if not isinstance(rows, list):
        return []
    candidates = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        candidates.append({
            "observation_id": str(row.get("observation_id") or ""),
            "interval": row.get("interval"),
            "evidence_ids": [str(eid) for eid in (row.get("evidence_ids") or ())],
            "status": str(row.get("status") or ""),
        })
    return candidates


# ── The stage ────────────────────────────────────────────────────────


def _identity_of(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """The identity a candidate carries, in the shape a reader checks."""
    if "material_key" in candidate:
        identities = [str(uid) for uid in (candidate.get("identities") or ())]
        return {
            "kind": "timeline_item",
            "unique_id": identities[0] if identities else "",
            "recorded_under": identities,
            "name": str(candidate.get("name") or ""),
            "row": str(candidate.get("row") or ""),
        }
    if "event_id" in candidate:
        return {
            "kind": "occurrence",
            "event_id": str(candidate.get("event_id") or ""),
            "occurrence": candidate.get("occurrence"),
            "subject_id": candidate.get("subject_id"),
        }
    return {
        "kind": "observation",
        "observation_id": str(candidate.get("observation_id") or ""),
    }


def _interval_of(candidate: Mapping[str, Any]) -> dict[str, Any] | None:
    """The SOURCE interval the candidate was cut from."""
    if "source" in candidate:
        source = candidate.get("source")
        source = source if isinstance(source, Mapping) else {}
        start = source.get("start_frame")
        end = source.get("end_frame")
        if start is None and end is None:
            return None
        return {
            "file": str(source.get("file") or ""),
            "start_frame": start,
            "end_frame": end,
        }
    interval = candidate.get("interval")
    if isinstance(interval, Mapping):
        return dict(interval)
    return None


def _row(role: str, claim: Mapping[str, Any], state: str,
         candidates: Sequence[Mapping[str, Any]],
         attempts: Sequence[Mapping[str, Any]], reason: str
         ) -> dict[str, Any]:
    """One target's grounding, with everything a reader needs."""
    chosen = dict(candidates[0]) if len(candidates) == 1 else None
    return {
        "role": role,
        "state": state,
        "claim": dict(claim),
        "identity": _identity_of(chosen) if chosen else None,
        "interval": _interval_of(chosen) if chosen else None,
        "evidence": list(chosen.get("evidence_ids") or ()) if chosen else [],
        "candidates": [
            {"identity": _identity_of(candidate),
             "interval": _interval_of(candidate)}
            for candidate in candidates
        ],
        "candidate_count": len(candidates),
        "attempts": [dict(attempt) for attempt in attempts],
        "reason": reason,
    }


def ground_role(role: str, claim: Mapping[str, Any] | None, *,
                items: Sequence[Mapping[str, Any]] = (),
                model: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Ground ONE named target role.

    The timeline surface is asked first.  When it answers with more than
    one candidate the EVIDENCE surface is asked again - that is the
    re-query - and both answers are recorded.  A role that is still
    ambiguous afterwards is ``observed``: the surfaces saw it, and they
    did not agree on which thing it is.
    """
    claim = dict(claim or {})
    attempts: list[dict[str, Any]] = []
    candidates = _timeline_candidates(claim, items)
    attempts.append({"surface": "timeline", "candidates": len(candidates)})
    if len(candidates) == 1:
        return _row(role, claim, VERIFIED, candidates, attempts, "")
    second, note = _evidence_candidates(claim, model)
    if len(candidates) > 1 or second:
        attempts.append({"surface": "evidence", "candidates": len(second),
                         "model_read": bool(model)})
    if len(candidates) > 1:
        if len(second) == 1:
            return _row(role, claim, VERIFIED, second, attempts, "")
        return _row(role, claim, OBSERVED, list(candidates) + list(second),
                    attempts,
                    f"the timeline carried {len(candidates)} candidates and "
                    f"the evidence surface {len(second)}: a unique target "
                    "was not established")
    if second:
        if len(second) == 1:
            return _row(role, claim, VERIFIED, second, attempts, "")
        return _row(role, claim, OBSERVED, second, attempts,
                    f"the evidence surface carried {len(second)} candidates: "
                    "a unique target was not established")
    if _asserts(claim):
        reason = ("the plan asserted this target and no surface carries it")
        if note:
            reason = f"{reason}: {note}"
        return _row(role, claim, CLAIMED, [], attempts, reason)
    return _row(role, claim, UNRESOLVED, [], attempts,
                "no surface carries this target")


def ground_plan(operations: Sequence[Mapping[str, Any]], *,
                items: Sequence[Mapping[str, Any]] = (),
                model: Mapping[str, Any] | None = None,
                claims: Mapping[str, Mapping[str, Any]] | None = None,
                ) -> dict[str, Any]:
    """Ground every target role a plan names.

    `claims` says what the plan asserts about each role.  A role the
    plan names and says nothing about is UNRESOLVED: nothing was
    asserted and nothing was found.
    """
    claims = claims or {}
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for operation in operations or ():
        if not isinstance(operation, Mapping):
            continue
        role = str(operation.get("target_role") or "")
        if not role or role in seen:
            continue
        seen.add(role)
        claim = claims.get(role)
        rows.append(ground_role(role, claim if isinstance(claim, Mapping)
                                else None, items=items, model=model))
    counts = {state: 0 for state in STATES}
    for row in rows:
        counts[str(row["state"])] = counts.get(str(row["state"]), 0) + 1
    receipt = {
        "format": FORMAT,
        "targets": rows,
        "counts": {**counts, "total": len(rows)},
    }
    receipt["refusals"] = refuse_ungrounded(operations, receipt)
    return receipt


def refuse_ungrounded(operations: Sequence[Mapping[str, Any]],
                      grounding: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The destructive operations a grounding receipt refuses.

    An operation that removes, replaces or rewrites material needs a
    VERIFIED target.  A role that is claimed, merely observed or
    unresolved is not a thing the plan can act on yet, and the refusal
    says which role and which state it was.
    """
    rows = {str(row.get("role")): row
            for row in ((grounding.get("targets") or ())
                        if isinstance(grounding, Mapping) else ())}
    refusals: list[dict[str, Any]] = []
    for operation in operations or ():
        if not isinstance(operation, Mapping):
            continue
        op = str(operation.get("op") or "")
        role = str(operation.get("target_role") or "")
        if not op or not role or not is_destructive(op):
            continue
        row = rows.get(role)
        state = str((row or {}).get("state") or UNRESOLVED)
        if state == VERIFIED:
            continue
        refusals.append({
            "op": op,
            "target_role": role,
            "state": state,
            "reason": (f"{op} is a destructive change and its target role "
                       f"{role!r} is {state}: a unique target was not "
                       "established, so the change is refused"),
        })
    return refusals
