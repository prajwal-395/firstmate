"""How the typed Project Model path declares the resolution it answered at.

The problem this removes
------------------------
The frame lane says which resolution answered it, and so does every
lane that reaches the ladder (``library/tools/fidelity.py``).  The
TYPED Project Model path did not: ``ren evidence`` answered "the model
holds this record, over this coverage" and "the model holds nothing"
in the same voice, and a reader could not tell a MEASUREMENT of the
source from a record the model DERIVED from one.  A scene observation
and a relation look the same in an answer, and they are not the same
kind of claim: one says what the source shows, the other says what
the model concluded from what it saw.

So an answer from this path could be read as a finding when it was a
lookup, and a lookup is not a finding: it says what the model HOLDS,
over the coverage the model DECLARES, and nothing about the media
behind it.

What this module owns
---------------------
The declaration, for this path only:

* **Which tier the answer was reached at.**  ``detailed`` when the
  claim is about a SPAN OF THE SOURCE - an object in a window, a word
  that was spoken, a shot that was measured - and ``coarse`` when the
  claim is about the MODEL'S OWN STRUCTURE: a relation between two
  things, an identity link, an interpretation, or the model's belief
  about a moment.  The tier follows the CLAIM, not the verb and not
  the record's epistemic class: a transcribed word is an inference and
  a relation is an inference too, and neither axis is the ladder's.

* **What it rests on.**  The lanes, the coverage, the freshness, the
  producers, the source digests and the epistemic classes of the
  records behind the answer.  A reader gets the precision from the
  block, not from the tier's own prose.

* **The way up.**  The escalation from wherever the answer stands to
  the next tier: the observation behind a derived claim, or the media
  at the span a finding names.

One ladder, one place
---------------------
The tier NAMES and the ladder's order come from
:mod:`library.tools.fidelity`; this module says which one this path
stands on and what it means here.  A second ladder of tier names would
be a second set of answers to the same question.
"""

from __future__ import annotations

from typing import Any

from library.tools import fidelity

LANE = "typed_project_model"
"""The lane this declaration belongs to."""


def declare(answer: dict[str, Any], model: dict[str, Any],
            plan: dict[str, Any] | None = None, *,
            revision: int | None = None,
            measured_now: bool = False,
            max_cost_seconds: float | None = None) -> dict[str, Any]:
    """The resolution this answer was reached at, and the way up.

    The block answers three questions a reader of this path has to ask:
    at what resolution was this answered, what does it rest on, and
    what would raise it.
    """
    records = _records(answer, model)
    classes = sorted({row["class"] for row in records if row["class"]})
    lanes = sorted({row["lane"] for row in records if row["lane"]})
    digests = _digests(answer, model, plan)
    basis = answer.get("completeness_basis") or []
    # The tier follows the CLAIM.  A row that names a span of the
    # source is a claim about the source; a row that names one of the
    # model's own rows is a claim about the model.  An answer with no
    # row at all rests on the coverage its basis declares, and a lane
    # that RAN and covered the scope measured the source - so an
    # absence measured over a complete lane is a detailed observation
    # of the source, while an answer whose lanes never ran is not.
    about_source = _claim_is_about_the_source(answer)
    tier = (fidelity.TIER_DETAILED if about_source
            else fidelity.TIER_COARSE)
    representation = {
        "kind": "project_model_observations",
        "project_model_revision": revision,
        "records": len(records),
        "epistemic_classes": classes,
        "lanes": lanes,
        "source_digests": digests,
    }
    if measured_now:
        representation["measured_now"] = True
    coverage = {
        "ran": (bool(records)
                or any(row.get("lane_state") == "present" for row in basis
                       if isinstance(row, dict))),
        "records": len(records),
        "answer_state": answer.get("answer_state"),
        "status": answer.get("status"),
        "about_the_source": about_source,
        "coverage": sorted({str(row.get("coverage")) for row in basis
                            if row.get("coverage")}),
        "freshness": sorted({str(row.get("freshness")) for row in basis
                             if row.get("freshness")}),
        "missing_lanes": list(answer.get("required_missing_lanes") or []),
    }
    block = fidelity.provenance(
        tier=tier,
        lane=LANE,
        representation=representation,
        source_digest=(digests[0] if len(digests) == 1 else None),
        coverage=coverage,
    )
    return {"fidelity": block,
            "escalation": _escalation(answer, model, tier=tier,
                                      max_cost_seconds=max_cost_seconds)}


def _claim_is_about_the_source(answer: dict[str, Any]) -> bool:
    """Whether the answer's claim is about a span of the source.

    A row that names an interval is a claim about the media; a row
    that names one of the model's own rows - a relation, an event, a
    track - is a claim about the model.  An answer with no row rests
    on its basis: a lane that ran over the scope measured the source,
    and a lane that never ran measured nothing.
    """
    results = [row for row in (answer.get("results") or [])
               if isinstance(row, dict)]
    if results:
        if any(row.get("row") is not None for row in results):
            return False
        return all(row.get("interval") for row in results)
    for row in answer.get("completeness_basis") or []:
        if not isinstance(row, dict):
            continue
        # An absence is a claim about the source only over a COMPLETE
        # lane: a sampled lane that found nothing has not looked at
        # the rest of the scope, and the lane's own vocabulary says so.
        if (row.get("lane_state") in ("present", "covers_absent")
                and row.get("coverage") == "complete"):
            return True
    return False


def _escalation(answer: dict[str, Any], model: dict[str, Any], *,
                tier: str, max_cost_seconds: float | None
                ) -> dict[str, Any]:
    """The request that raises this answer to the next tier.

    From a detailed answer the way up is the ORIGINAL media at the span
    the answer names - the pixels behind the finding, which the answer
    already points at.  From a coarse answer the way up is the
    OBSERVATION the derived claim was read from: the record that looked
    at the source behind a relation the model holds.
    """
    if tier == fidelity.TIER_DETAILED:
        windows = _original_windows(answer, model)
        if not windows:
            return {}
        return fidelity.escalation(
            from_tier=tier,
            request={"lane": "original_media", "windows": windows},
            cost_seconds=0.0,
            cost_basis=("no measurement is owed: the answer already names "
                        "the recording and the span the finding is about"))
    observations = _observation_ids(answer)
    if observations:
        return fidelity.escalation(
            from_tier=tier,
            request={"lane": "source_observations",
                     "observations": observations},
            cost_seconds=0.0,
            cost_basis=("the records are already in the model: reading them "
                        "costs a lookup, not a measurement"))
    # The model holds nothing about this, or holds it over a coverage
    # that cannot answer: the way up is to MEASURE the source, and the
    # caller's budget is the price of that.
    lanes = sorted({str(row.get("lane"))
                    for row in (answer.get("completeness_basis") or [])
                    if isinstance(row, dict) and row.get("lane")
                    and row.get("lane_state") != "present"})
    lanes = sorted(set(lanes) | set(
        str(lane) for lane in (answer.get("required_missing_lanes") or [])))
    if not lanes:
        return {}
    return fidelity.escalation(
        from_tier=tier,
        request={"lane": "evidence_acquisition", "acquire": True,
                 "lanes": lanes,
                 "answer_state": answer.get("answer_state")},
        cost_seconds=max_cost_seconds,
        cost_basis=("the acquisition lane's own ceiling for this "
                    "request: it measures the source inside that "
                    "budget, and the budget is the caller's to set"))


def _original_windows(answer: dict[str, Any],
                      model: dict[str, Any]) -> list[dict[str, Any]]:
    """The media behind each row of the answer."""
    windows = []
    for result in answer.get("results") or []:
        if not isinstance(result, dict):
            continue
        interval = result.get("interval") or {}
        asset = _asset(model, result.get("asset_id"))
        windows.append({
            "asset_id": result.get("asset_id"),
            "media": (asset or {}).get("uri"),
            "content_digest": (asset or {}).get("content_digest"),
            "start_s": interval.get("start_s"),
            "end_s": interval.get("end_s"),
            "clock_id": interval.get("clock_id"),
        })
    return windows


def _asset(model: dict[str, Any], asset_id: str | None) -> dict[str, Any] | None:
    for row in model.get("assets") or []:
        if isinstance(row, dict) and row.get("id") == asset_id:
            return row
    return None


def _digests(answer: dict[str, Any], model: dict[str, Any],
             plan: dict[str, Any] | None) -> list[str]:
    """The sources the answer is about."""
    scope = (plan or {}).get("scope") or {}
    wanted = [scope.get("asset_id")] if scope.get("asset_id") else []
    for result in answer.get("results") or []:
        if isinstance(result, dict) and result.get("asset_id"):
            wanted.append(result["asset_id"])
    digests = []
    for asset_id in wanted:
        asset = _asset(model, asset_id)
        digest = (asset or {}).get("content_digest")
        if digest and digest not in digests:
            digests.append(str(digest))
    return digests


def _records(answer: dict[str, Any],
             model: dict[str, Any]) -> list[dict[str, Any]]:
    """The records the answer rests on, and what kind each one is.

    The ids come from the answer itself - the rows it returned, the
    evidence it names, the leaves a trace followed - and the KIND comes
    from each record's own evidence contract, which is where the
    producer declared it.  A record with no contract is reported as
    unclassified rather than counted as a measurement.
    """
    from library.tools import epistemic_vocabulary

    by_id = {}
    for observation in model.get("observations") or []:
        if isinstance(observation, dict) and observation.get("id"):
            by_id[str(observation["id"])] = observation
    rows = []
    for observation_id in _observation_ids(answer):
        observation = by_id.get(observation_id)
        if observation is None:
            continue
        contract = epistemic_vocabulary.contract_of(observation) or {}
        rows.append({
            "id": observation_id,
            "class": str(contract.get("evidence_type") or ""),
            "lane": _lane_of(observation),
            "producer": contract.get("producer")
            or (observation.get("payload") or {}).get("producer"),
        })
    return rows


def _observation_ids(answer: dict[str, Any]) -> list[str]:
    """Every observation id the answer names, however it names it."""
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("evidence_ids", "leaf_observation_ids",
                           "supporting_observation_ids",
                           "candidate_evidence_ids") and isinstance(value, list):
                    for item in value:
                        if isinstance(item, str) and item not in found:
                            found.append(item)
                elif key == "observation_id" and isinstance(value, str):
                    if value not in found:
                        found.append(value)
                elif key == "observation" and isinstance(value, dict):
                    identifier = value.get("id")
                    if isinstance(identifier, str) and identifier not in found:
                        found.append(identifier)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(answer)
    return found


def _lane_of(observation: dict[str, Any]) -> str | None:
    """The lane that produced one record.

    The reading is `evidence_query`'s: a record's lane is named by its
    provenance, and one reading of that is enough.
    """
    from library.tools.evidence_query import lane_of

    return lane_of(observation)
