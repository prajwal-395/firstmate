"""EDIT-023: what a reduction must not take with it.

A reduction is a promise that the video still says what it said.  Nothing
in the repository checked that promise: the pipeline could remove a
passage, remove a beat, or remove the only place a claim was made, and
nothing downstream said so.  `library/tools/reel_replace_guard.py`
guards a REPLACEMENT - a reel's row count against a declared diff - and
that is a different promise: this one is about CONTENT.

**What is content, measured.**  Three things, and all three are already
in the state:

- **the claims** - the intent model's declared statements.  A claim
  names the passages that carry it, so a passage that carries a claim is
  the only place that claim is said.
- **the beats** - the intent model's declared narrative units, each with
  a purpose.  A passage names the beats it serves.  A beat marked
  `required` is one the edit may not drop, and `library/tools/
  intent_model.py` already refuses a required beat that the speech
  sequence never reaches.
- **the measured picture** - the vision pass's own classification of
  what each clip shows: its `assessment.content_type`, its scene labels
  and its action labels.  A cut that drops the only clip of a
  `product_showcase` has lost something the footage had and the cut does
  not.

**What is emotional content, and what is not measured.**  The vision
pass does NOT measure mood: its own prompt asks for what is physically
visible and refuses interpretation (AGENTS.md 10.1).  So this module
does not pretend to measure feeling.  The emotional weight of a passage
is carried by the BEAT it serves - the beat's declared purpose is what
the passage is for - and by nothing else here.  A passage that serves no
beat has no measured emotional content, and the module says so rather
than scoring it.

**The verdict.**  `preserved`, `damaged` or `undetermined`.  Damaged
means something the cut was carrying is gone: a required claim, a
required beat, or - when the project asked for it - a declared claim.
Everything else the reduction dropped is REPORTED with its evidence,
because dropping the only clip of a product showcase is a decision, and
a decision nobody noticed is the defect.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

PRESERVED = "preserved"
DAMAGED = "damaged"
UNDETERMINED = "undetermined"

DROPPED_REQUIRED_CLAIM = "dropped_required_claim"
DROPPED_CLAIM = "dropped_claim"
DROPPED_REQUIRED_BEAT = "dropped_required_beat"
DROPPED_BEAT = "dropped_beat"
DROPPED_CONTENT_TYPE = "dropped_content_type"


class ContentSignificanceError(ValueError):
    """A significance request could not be read."""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _passage_rows(passages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for index, row in enumerate(passages or ()):
        if not isinstance(row, Mapping):
            continue
        rows.append({
            "passage_id": _text(row.get("passage_id")) or f"passage:{index + 1}",
            "clip_id": _text(row.get("clip_id")),
            "text": str(row.get("text") or ""),
            "serves_beats": list(row.get("serves_beats") or []),
            "claims": list(row.get("claims") or []),
        })
    return rows


def passage_significance(passages: Sequence[Mapping[str, Any]], *,
                         claims: Sequence[Mapping[str, Any]] = (),
                         beats: Sequence[Mapping[str, Any]] = (),
                         clip_content: Mapping[str, Mapping[str, Any]] | None = None
                         ) -> dict[str, Any]:
    """What each passage carries, measured.

    The significance of a passage is the set of things ONLY that passage
    carries: the claims it is the only passage for, the beats no other
    passage serves, and the measured content types that no other passage
    brings.  Nothing here is a score, and a passage that carries nothing
    unique is not "worth less" - it is simply not the only place any of
    it lives.
    """
    rows = _passage_rows(passages)
    content = dict(clip_content or {})

    claim_passages: dict[str, list[str]] = {}
    for claim in claims or ():
        if not isinstance(claim, Mapping):
            continue
        claim_id = _text(claim.get("claim_id"))
        if not claim_id:
            continue
        claim_passages[claim_id] = [_text(pid) for pid in claim.get("passage_ids") or []]

    beat_passages: dict[str, list[str]] = {}
    required_beats: set[str] = set()
    for beat in beats or ():
        if not isinstance(beat, Mapping):
            continue
        beat_id = _text(beat.get("beat_id"))
        if not beat_id:
            continue
        beat_passages[beat_id] = [_text(pid) for pid in beat.get("passage_ids") or []]
        if beat.get("required") is True:
            required_beats.add(beat_id)

    content_types: dict[str, list[str]] = {}
    for row in rows:
        measured = content.get(row["clip_id"]) or {}
        content_type = _text(measured.get("content_type"))
        if content_type:
            content_types.setdefault(content_type, []).append(row["passage_id"])

    out = []
    for row in rows:
        passage_claims = sorted(
            claim_id for claim_id, holders in claim_passages.items()
            if row["passage_id"] in holders)
        passage_beats = sorted(row["serves_beats"])
        measured = content.get(row["clip_id"]) or {}
        out.append({
            "passage_id": row["passage_id"],
            "clip_id": row["clip_id"],
            "word_count": len(row["text"].split()),
            "claims": passage_claims,
            "sole_carrier_of_claims": sorted(
                claim_id for claim_id, holders in claim_passages.items()
                if holders == [row["passage_id"]]),
            "serves_beats": passage_beats,
            "sole_carrier_of_beats": sorted(
                beat_id for beat_id, holders in beat_passages.items()
                if holders == [row["passage_id"]]),
            "required_beats_served": sorted(
                beat_id for beat_id in passage_beats if beat_id in required_beats),
            "content_type": _text(measured.get("content_type")),
            "scenes": list(measured.get("scenes") or []),
        })
    return {
        "passages": out,
        "required_beats": sorted(required_beats),
        "claims": claim_passages,
        "beats": beat_passages,
        "content_types": content_types,
    }


def check_reduction(before: Sequence[Mapping[str, Any]],
                    after: Sequence[Mapping[str, Any]], *,
                    claims: Sequence[Mapping[str, Any]] = (),
                    beats: Sequence[Mapping[str, Any]] = (),
                    clip_content: Mapping[str, Mapping[str, Any]] | None = None,
                    policy: Any = None) -> dict[str, Any]:
    """Did the reduction keep what the cut was carrying?

    `before` is the material as it was offered and `after` is what the
    cut plays.  The verdict is `damaged` when something the cut was
    carrying is gone and something REQUIRED it: a claim the project
    declared as required, or a beat the intent model marked `required`.
    Everything else the reduction dropped is reported with its
    evidence, because it is a decision somebody has to see.
    """
    required_claims: list[str] = []
    if policy is not None:
        try:
            declared = policy.slot("content.required_claims")
        except Exception:  # noqa: BLE001 - an unreadable slot reads undeclared
            declared = None
        if declared:
            required_claims = [str(row) for row in declared]

    before_rows = _passage_rows(before)
    after_rows = _passage_rows(after)
    after_ids = {row["passage_id"] for row in after_rows}

    claim_by_id: dict[str, Mapping[str, Any]] = {}
    for claim in claims or ():
        if isinstance(claim, Mapping) and _text(claim.get("claim_id")):
            claim_by_id[_text(claim.get("claim_id"))] = claim
    beat_by_id: dict[str, Mapping[str, Any]] = {}
    required_beats: set[str] = set()
    for beat in beats or ():
        if not isinstance(beat, Mapping):
            continue
        beat_id = _text(beat.get("beat_id"))
        if not beat_id:
            continue
        beat_by_id[beat_id] = beat
        if beat.get("required") is True:
            required_beats.add(beat_id)

    before_sig = passage_significance(before, claims=claims, beats=beats,
                                      clip_content=clip_content)

    carried_before = {claim_id for row in before_sig["passages"]
                      for claim_id in row["claims"]}
    carried_after = {claim_id for row in passage_significance(
        after, claims=claims, beats=beats, clip_content=clip_content)["passages"]
        for claim_id in row["claims"]}

    beats_before = {beat_id for row in before_sig["passages"]
                    for beat_id in row["serves_beats"]}
    beats_after = {beat_id for row in passage_significance(
        after, claims=claims, beats=beats, clip_content=clip_content)["passages"]
        for beat_id in row["serves_beats"]}

    types_before = set(before_sig["content_types"])
    types_after = set(passage_significance(
        after, claims=claims, beats=beats,
        clip_content=clip_content)["content_types"])

    findings: list[dict[str, Any]] = []
    verdict = PRESERVED

    for claim_id in sorted(carried_before - carried_after):
        required = claim_id in required_claims
        claim = claim_by_id.get(claim_id) or {}
        findings.append({
            "kind": DROPPED_REQUIRED_CLAIM if required else DROPPED_CLAIM,
            "claim_id": claim_id,
            "statement": _text(claim.get("statement")),
            "required": required,
            "detail": (
                f"the claim {claim_id} is not carried by the cut"
                + (" and the project declared it required" if required else "")),
        })
        if required:
            verdict = DAMAGED

    for beat_id in sorted(beats_before - beats_after):
        required = beat_id in required_beats
        beat = beat_by_id.get(beat_id) or {}
        findings.append({
            "kind": DROPPED_REQUIRED_BEAT if required else DROPPED_BEAT,
            "beat_id": beat_id,
            "purpose": _text(beat.get("purpose")),
            "required": required,
            "detail": (
                f"the beat {beat_id} is not served by the cut"
                + (" and the direction marked it required" if required else "")),
        })
        if required:
            verdict = DAMAGED

    for content_type in sorted(types_before - types_after):
        findings.append({
            "kind": DROPPED_CONTENT_TYPE,
            "content_type": content_type,
            "detail": f"the cut no longer shows the measured {content_type}",
        })

    dropped_passages = []
    for row in before_sig["passages"]:
        if row["passage_id"] in after_ids:
            continue
        dropped_passages.append({
            "passage_id": row["passage_id"],
            "clip_id": row["clip_id"],
            "sole_carrier_of_claims": row["sole_carrier_of_claims"],
            "sole_carrier_of_beats": row["sole_carrier_of_beats"],
            "required_beats_served": row["required_beats_served"],
            "content_type": row["content_type"],
        })
    if dropped_passages:
        findings.append({
            "kind": "dropped_passages",
            "passages": dropped_passages,
            "detail": f"{len(dropped_passages)} passage(s) the material "
                      f"offered are not in the cut",
        })

    if not before_rows:
        verdict = UNDETERMINED
    elif not findings:
        verdict = PRESERVED
    return {
        "verdict": verdict,
        "required_claims": sorted(required_claims),
        "required_beats": sorted(required_beats),
        "findings": findings,
        "before_count": len(before_rows),
        "after_count": len(after_rows),
    }
