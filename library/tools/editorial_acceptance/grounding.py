"""The grounding half of an interpretation: could the work be pointed at?

Reading the request and finding the thing the request is about are two
measurements. The published interpretation stage folded them together,
and the result read as if the run had failed to understand eight
requests when it had understood all eight and could point at four of
eleven targets.

This module measures the GROUNDING half, per ROLE INSTANCE rather than
per brief, and it is published ALONGSIDE the headline - never instead of
it.

Three things are kept apart here, because they are three different
answers:

- **not applicable**: the brief's own premise - the thing the request
  presupposes of the footage - is not in the substrate. The run could
  not have pointed at it because there was nothing to point at. This is
  not a failure of the run, and it is not a pass either: the brief is
  simply not doable on this substrate.
- **resolved**: the run said which item the role names. This is the
  run's own claim, and a claim is not evidence.
- **verified**: the recorded timeline CARRIES the item the run named -
  the clip's own stable id, on the timeline the record says it worked
  in. A resolution with no evidence behind it is reported as
  unverified, and a resolution the recorded timeline does not carry is
  reported as NOT verified, which is a claim the evidence refuses.

The verification never guesses: no recorded timeline, an ambiguous
timeline name, or a target that names no clip leaves the instance
unverified rather than counting as resolved.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from library.tools.editorial_acceptance import footage

MEASURED = "measured"

#: The states one role instance can be in.
RESOLVED = "resolved"
UNRESOLVED = "unresolved"
NOT_APPLICABLE = "not_applicable"

#: The states a resolution's EVIDENCE can be in.
VERIFIED = "verified"
NOT_VERIFIED = "not_verified"
NO_EVIDENCE = "no_evidence"
UNAVAILABLE = "unavailable"

_UUID = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")


class RecordedTimeline:
    """The recorded generations of the timeline a record names.

    `available` is False when the recorded timeline could not be read -
    no store on this machine, a name two timelines carry, a timeline
    nobody has read. That is not a failure of the run, and it leaves
    every resolution UNVERIFIED rather than verified.
    """

    def __init__(self, clip_ids: Sequence[str] = (), *, available: bool = True,
                 note: str = "") -> None:
        self.clip_ids = frozenset(str(item) for item in clip_ids)
        self.available = bool(available)
        self.note = str(note or "")

    def carries(self, clip_id: str) -> bool:
        return str(clip_id) in self.clip_ids


def recorded_timeline(record: Mapping[str, Any]) -> RecordedTimeline:
    """The recorded timeline the record says it worked in.

    Read from Ren's own recorded generations - never Resolve, never a
    live read. A machine with no recorded timeline answers
    "unavailable" and says why.

    A record may name the timeline by its exact id, or by its name. A
    name TWO recorded timelines carry is ambiguous, and the claim is
    then checked against BOTH of them: the check asks whether the clip
    the run named is on a recorded generation of that timeline, and the
    answer that matters is whether it is on none of them.
    """
    project = record.get("project")
    project = project if isinstance(project, Mapping) else {}
    resolve_project = str(project.get("resolve_project") or "").strip()
    timeline = str(project.get("timeline") or "").strip()
    timeline_id = str(project.get("timeline_id") or "").strip()
    if not resolve_project or not (timeline or timeline_id):
        return RecordedTimeline(available=False,
                                note="the record names no project and "
                                     "timeline")
    try:
        from library.tools import timeline_read, timeline_shadow
    except Exception as exc:  # noqa: BLE001 - an import failure is data
        return RecordedTimeline(available=False,
                                note=f"the recorded timeline store could not "
                                     f"be opened: {exc}")
    try:
        if not timeline_shadow.default_db_path().exists():
            return RecordedTimeline(
                available=False,
                note="this machine holds no recorded timeline store")
        store = timeline_shadow.ShadowStore()
        current = store.current_snapshots(resolve_project)
    except Exception as exc:  # noqa: BLE001 - a refusal is an answer
        return RecordedTimeline(
            available=False,
            note=f"the recorded timeline could not be read: {exc}")
    if timeline_id:
        matches = [(generation, snapshot)
                   for generation, snapshot in current
                   if generation.timeline_id == timeline_id]
        if not matches:
            return RecordedTimeline(
                available=False,
                note=f"no recorded timeline with id {timeline_id!r} in "
                     f"project {resolve_project!r}")
    else:
        matches = [(generation, snapshot)
                   for generation, snapshot in current
                   if generation.timeline_name == timeline]
        if not matches:
            return RecordedTimeline(
                available=False,
                note=f"no recorded timeline named {timeline!r} has been "
                     f"read in project {resolve_project!r}")
    ids = []
    for _generation, snapshot in matches:
        for clip in timeline_read.clip_records(snapshot):
            if clip.get("unique_id"):
                ids.append(str(clip["unique_id"]))
    names = ", ".join(sorted(generation.timeline_id
                             for generation, _snapshot in matches))
    if len(matches) == 1:
        note = (f"generation {matches[0][0].generation} of timeline "
                f"{matches[0][0].timeline_id} in project {resolve_project}")
    else:
        note = (f"{len(matches)} recorded timelines carry the name "
                f"{timeline!r} in project {resolve_project} ({names}); the "
                f"claim was checked against all of them")
    return RecordedTimeline(ids, note=note)


def _claimed_clip_id(entry: Mapping[str, Any]) -> str:
    """The clip the run named, from its evidence or from its own words.

    A record may carry the evidence STRUCTURED - the clip's stable id,
    the row and the frame span - or, as the live lane writes it, inside
    the prose of the target it resolved. Both are read here, and a
    target that names no clip leaves the resolution unverified.
    """
    evidence = entry.get("evidence")
    evidence = evidence if isinstance(evidence, Mapping) else {}
    for field in ("clip_unique_id", "unique_id", "clip_id"):
        value = str(evidence.get(field) or "").strip()
        if value:
            return value
    text = str(entry.get("target") or "")
    found = _UUID.search(text)
    return found.group(0) if found else ""


def _requirement_states(premise: Mapping[str, Any]) -> dict[str, str]:
    """How each premise feature stands against the substrate."""
    states = {}
    for row in (premise.get("requirements") or ()):
        if not isinstance(row, Mapping):
            continue
        states[str(row.get("feature") or "")] = str(row.get("state") or "")
    return states


def measure(record: Mapping[str, Any], key: Mapping[str, Any],
            premise: Mapping[str, Any],
            timeline: RecordedTimeline | None = None) -> dict[str, Any]:
    """One brief's grounding, per role instance.

    `premise` is the brief's premise against the substrate, as the
    premise view measured it. A role instance whose premise the
    substrate does not carry is NOT APPLICABLE: the run could not have
    pointed at it because there was nothing to point at.
    """
    expected_roles = [str(role) for role in (key.get("expected_target_roles")
                                             or [])]
    role_premises = {}
    for role, feature in (key.get("role_premises") or {}).items():
        role_premises[str(role)] = str(feature)
    states = _requirement_states(premise)

    block = record.get("interpretation")
    block = block if isinstance(block, Mapping) else {}
    entries = {}
    for entry in (block.get("resolved_targets") or ()):
        if not isinstance(entry, Mapping):
            continue
        entries[str(entry.get("role") or "")] = entry

    recorded = timeline if timeline is not None else recorded_timeline(record)

    instances = []
    for role in expected_roles:
        entry = entries.get(role)
        resolved = bool(entry.get("resolved")) if entry else False
        feature = role_premises.get(role, "")
        state = states.get(feature, "") if feature else ""
        if feature and state in (footage.MISSING, footage.VIOLATED):
            status = NOT_APPLICABLE
            reason = ("the substrate does not carry what the request "
                      "presupposes: " + footage.FEATURES.get(feature,
                                                            feature))
        elif resolved:
            status = RESOLVED
            reason = ""
        else:
            status = UNRESOLVED
            reason = ""
        evidence = NO_EVIDENCE
        if status == RESOLVED:
            clip_id = _claimed_clip_id(entry or {})
            if not clip_id:
                evidence = NO_EVIDENCE
            elif not recorded.available:
                evidence = UNAVAILABLE
            elif recorded.carries(clip_id):
                evidence = VERIFIED
            else:
                evidence = NOT_VERIFIED
        instances.append({
            "role": role,
            "status": status,
            "claimed_resolved": resolved,
            "premise_feature": feature,
            "premise_state": state,
            "reason": reason,
            "evidence": evidence,
            "claimed": str((entry or {}).get("target") or ""),
        })

    applicable = [row for row in instances if row["status"] != NOT_APPLICABLE]
    resolved_applicable = [row for row in applicable
                            if row["status"] == RESOLVED]
    verified = [row for row in resolved_applicable
                if row["evidence"] == VERIFIED]
    claimed = [row for row in instances if row["claimed_resolved"]]
    return {
        "status": MEASURED,
        "passed": bool(applicable) and len(resolved_applicable) == len(
            applicable),
        "detail": {
            "role_instances": len(instances),
            "applicable_role_instances": len(applicable),
            "claimed_resolved_role_instances": len(claimed),
            "resolved_role_instances": len([row for row in instances
                                            if row["status"] == RESOLVED]),
            "resolved_applicable_role_instances": len(resolved_applicable),
            "not_applicable_role_instances": len(instances) - len(applicable),
            "verified_role_instances": len(verified),
            "unverified_role_instances": len(
                [row for row in resolved_applicable
                 if row["evidence"] in (NO_EVIDENCE, NOT_VERIFIED)]),
            "unavailable_role_instances": len(
                [row for row in resolved_applicable
                 if row["evidence"] == UNAVAILABLE]),
            "recorded_timeline": {
                "available": recorded.available,
                "note": recorded.note,
            },
            "instances": instances,
        },
    }


def summarize(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Every brief's grounding, on one denominator of ROLE INSTANCES.

    A brief is not the unit here: a brief that names seven roles and a
    brief that names one are not the same size of claim. The published
    number is the role instances the run could point at, over the role
    instances that were applicable at all - and the ones that were not
    applicable are counted beside it, never dropped from the report.
    """
    measured = [row for row in rows if row.get("status") == MEASURED]
    instances = 0
    applicable = 0
    resolved = 0
    claimed = 0
    resolved_applicable = 0
    verified = 0
    not_applicable = 0
    for row in measured:
        detail = row.get("detail")
        detail = detail if isinstance(detail, Mapping) else {}
        instances += int(detail.get("role_instances") or 0)
        applicable += int(detail.get("applicable_role_instances") or 0)
        resolved += int(detail.get("resolved_role_instances") or 0)
        claimed += int(detail.get("claimed_resolved_role_instances") or 0)
        resolved_applicable += int(
            detail.get("resolved_applicable_role_instances") or 0)
        verified += int(detail.get("verified_role_instances") or 0)
        not_applicable += int(detail.get("not_applicable_role_instances") or 0)
    return {
        "note": (
            "A supplementary view, published ALONGSIDE the headline and "
            "never instead of it: whether the run could POINT AT the thing "
            "the request is about, counted per role instance. A role "
            "instance whose premise the substrate does not carry is NOT "
            "APPLICABLE - there was nothing to point at - and is counted "
            "beside the number rather than dropped from the report. A "
            "resolution the recorded timeline does not carry is NOT "
            "VERIFIED: the run's own claim is not evidence."),
        "unit": "role instances",
        "role_instances": instances,
        "applicable_role_instances": applicable,
        "not_applicable_role_instances": not_applicable,
        "claimed_resolved_role_instances": claimed,
        "resolved_role_instances": resolved,
        "resolved_applicable_role_instances": resolved_applicable,
        "verified_role_instances": verified,
        "value": ((resolved_applicable / applicable) if applicable else None),
        "value_over_all_role_instances": ((resolved / instances)
                                          if instances else None),
        "briefs": {str(row.get("brief_id")): row for row in rows},
    }
