"""PLAN-003 measured on the representative corpus (BASE-003).

This tool lives OUTSIDE the engine on purpose: the corpus's answer key
is sealed, and the engine must not be able to read it.  It measures the
same thing the eight-brief benchmark measures, over the thirteen briefs
of the representative corpus:

- the target roles each brief names, and how many of them GROUND to a
  single measured item (verified), how many are OBSERVED (the evidence
  carries several candidates), how many are CLAIMED and how many are
  UNRESOLVED;
- the destructive operations the grounding stage REFUSES, because the
  role they name is not verified;
- the planning number, which the grounding stage must not move.

A role is grounded against TWO surfaces, and the corpus's own sealed
evidence is one of them:

- the MEASURED MOMENTS the premise names (or that the premise's prose
  names, by the moment's own file or source interval).  A moment is a
  verified identity with a source interval: somebody pulled the frame
  and looked at it;
- the RECORDED TIMELINE of the substrate, which carries the items
  themselves.

    python3 evaluation/tools/grounding_corpus.py [--report <path>]

The measurement reads the corpus and the machine's recorded timelines.
It contacts no Resolve and changes no project.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

sys.path.insert(0, str(Path(__file__).resolve().parent))

from corpus import (
    load_sealed_briefs,
    load_sealed_substrates,
    premise_audit,
)

from library.tools import target_grounding
from library.tools.editorial_acceptance import briefs as brief_oracle
from library.tools.editorial_acceptance import grounding_stage

SCHEMA = "ren-editorial-corpus-grounding/1"

#: The project the corpus's substrates were measured in.  The sealed
#: evidence names it in the alias of every substrate.
SCRATCH_PROJECT = "Ren Scratch - breaktest"


def _row_of(row: str) -> str:
    """The corpus's row names in the engine's: video6 is V6."""
    text = str(row or "").strip().lower()
    if text.startswith("video"):
        return "V" + text[len("video"):]
    if text.startswith("audio"):
        return "A" + text[len("audio"):]
    return text.upper()


def _moment_key(moment: Mapping[str, Any]) -> tuple:
    """One moment's own key, so two records of one thing are one thing."""
    name = str(moment.get("clip") or "")
    row = _row_of(str(moment.get("row") or ""))
    frames = moment.get("timeline_frames")
    if isinstance(frames, (list, tuple)) and len(frames) == 2:
        return ("timeline", name, row, frames[0], frames[1])
    source = str(moment.get("source_file") or "")
    start = moment.get("source_start")
    end = moment.get("source_end")
    if source:
        return ("source", source.rsplit("/", 1)[-1], "", start, end)
    return ("timeline", name, row, moment.get("timeline_start"),
            moment.get("timeline_end"))


def _material_key(material: Mapping[str, Any]) -> tuple:
    """One recorded timeline material's key, in the same space."""
    span = material.get("record_span")
    span = span if isinstance(span, (list, tuple)) else (None, None)
    return ("timeline", str(material.get("name") or ""),
            str(material.get("row") or ""), span[0], span[1])


def _names_material(prose: str, moment: Mapping[str, Any]) -> bool:
    """Whether the premise's own words name this moment.

    A moment is named when the prose carries its file (the clip's own
    name) or both numbers of its source interval: the premise said
    where it is, in the words somebody measured.
    """
    text = str(prose or "").lower()
    if not text:
        return False
    for key in ("clip", "source_file"):
        value = str(moment.get(key) or "")
        if value and value.rsplit("/", 1)[-1].lower() in text:
            return True
    start = moment.get("source_start")
    end = moment.get("source_end")
    if isinstance(start, (int, float)) and isinstance(end, (int, float)):
        return (f"{float(start)}" in text and f"{float(end)}" in text)
    return False


def _recorded_items(aliases: Sequence[str]) -> tuple[list[dict[str, Any]], str]:
    """The recorded timeline's materials for a substrate's aliases."""
    from library.tools import timeline_shadow

    note = "no recorded timeline carries this substrate's aliases"
    try:
        store = timeline_shadow.ShadowStore()
    except Exception as exc:  # noqa: BLE001 - a refusal is an answer
        return [], f"the recorded timeline store could not be opened: {exc}"
    for alias in aliases:
        try:
            snapshots = store.current_snapshots(SCRATCH_PROJECT)
        except Exception as exc:  # noqa: BLE001 - a refusal is an answer
            return [], (f"the recorded timelines could not be read: {exc}")
        matches = [(generation, snapshot) for generation, snapshot in snapshots
                   if generation.timeline_name == alias]
        if not matches:
            continue
        from library.tools import timeline_read

        clips: list[dict[str, Any]] = []
        for _generation, snapshot in matches:
            clips.extend(timeline_read.clip_records(snapshot))
        if not clips:
            continue
        note = (f"{len(matches)} recorded generation(s) of {alias!r} in "
                f"project {SCRATCH_PROJECT!r} carry {len(clips)} clips")
        return target_grounding.items_of(clips), note
    return [], note


def _premise_candidates(premise: Mapping[str, Any],
                        moments: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The measured moments the premise names."""
    entries = [str(entry) for entry in (premise.get("evidence") or ())]
    prose = " ".join(entries)
    named: list[dict[str, Any]] = []
    for entry in entries:
        if not entry.startswith("moment:"):
            continue
        moment = moments.get(entry[len("moment:"):])
        if isinstance(moment, Mapping):
            named.append(dict(moment))
    for name, moment in moments.items():
        if not isinstance(moment, Mapping):
            continue
        if any(entry == f"moment:{name}" for entry in entries):
            continue
        if _names_material(prose, moment):
            named.append(dict(moment))
    return named


def _merge(candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One entry per thing: two records of one thing are one thing."""
    merged: dict[tuple, dict[str, Any]] = {}
    for candidate in candidates:
        key = candidate.get("_key")
        if not isinstance(key, tuple):
            continue
        merged.setdefault(key, {"key": list(key),
                                "identity": dict(
                                    candidate.get("identity") or {}),
                                "interval": dict(
                                    candidate.get("interval") or {})})
    return list(merged.values())


def ground_brief(brief: Mapping[str, Any], catalogue: Mapping[str, Any],
                 audit_row: Mapping[str, Any],
                 evidence: Mapping[str, Any],
                 timelines: Mapping[str, tuple[list[dict[str, Any]], str]],
                 ) -> dict[str, Any]:
    """One corpus brief's grounding, per target role."""
    key = brief_oracle.oracle(brief)
    substrate = str(audit_row.get("substrate") or "")
    substrates = evidence.get("substrates")
    substrates = substrates if isinstance(substrates, Mapping) else {}
    moments = evidence.get("moments")
    moments = moments if isinstance(moments, Mapping) else {}
    items, note = timelines.get(substrate, ([], "no recorded timeline"))

    rows: list[dict[str, Any]] = []
    for role in (key.get("expected_target_roles") or ()):
        feature = str((key.get("role_premises") or {}).get(role) or "")
        premise = substrates.get(substrate)
        premise = premise.get(feature) if isinstance(premise, Mapping) else None
        if not isinstance(premise, Mapping) or str(premise.get("state")) != "present":
            rows.append({
                "role": str(role),
                "premise_feature": feature,
                "state": target_grounding.UNRESOLVED,
                "reason": ("the substrate does not carry what the request "
                           "presupposes: there is nothing to point at"),
                "candidates": [],
            })
            continue
        candidates: list[dict[str, Any]] = []
        for moment in _premise_candidates(premise, moments):
            key_tuple = _moment_key(moment)
            candidates.append({
                "_key": key_tuple,
                "identity": {"kind": "measured_moment",
                             "name": str(moment.get("clip")
                                         or moment.get("source_file") or ""),
                             "key": list(key_tuple)},
                "interval": {
                    "file": str(moment.get("source_file")
                                or moment.get("clip") or ""),
                    "start": moment.get("source_start",
                                        (moment.get("timeline_frames") or [None])[0]),
                    "end": moment.get("source_end",
                                      (moment.get("timeline_frames") or [None, None])[1]),
                },
            })
        claim = grounding_stage.selectors_of(
            " ".join(str(entry) for entry in (premise.get("evidence") or ())))
        materials = target_grounding._timeline_candidates(claim, items)
        for material in materials:
            key_tuple = _material_key(material)
            candidates.append({
                "_key": key_tuple,
                "identity": {"kind": "timeline_item",
                             "unique_id": (material.get("identities") or [""])[0],
                             "name": str(material.get("name") or ""),
                             "row": str(material.get("row") or "")},
                "interval": {"file": str(
                    (material.get("source") or {}).get("file") or ""),
                    "start_frame": (material.get("source") or {}).get(
                        "start_frame"),
                    "end_frame": (material.get("source") or {}).get(
                        "end_frame")},
            })
        merged = _merge(candidates)
        if len(merged) == 1:
            state = target_grounding.VERIFIED
            reason = ""
        elif merged:
            state = target_grounding.OBSERVED
            reason = (f"{len(merged)} candidates: a unique target was not "
                      "established")
        else:
            state = target_grounding.UNRESOLVED
            reason = ("the premise is carried, but it names no single item "
                      "the stage can point at")
        rows.append({
            "role": str(role),
            "premise_feature": feature,
            "state": state,
            "reason": reason,
            "identity": (merged[0]["identity"] if len(merged) == 1 else None),
            "interval": (merged[0]["interval"] if len(merged) == 1 else None),
            "candidates": merged,
        })

    operations = [{"op": str(op), "target_role": ""}
                  for op in (key.get("expected_operations") or ())]
    roles = [str(role) for role in (key.get("expected_target_roles") or ())]
    if len(operations) == len(roles):
        for operation, role in zip(operations, roles):
            operation["target_role"] = role
    elif operations and roles:
        # The plan does not say which role an operation names, so no
        # destructive operation can be shown to have a verified target.
        for operation in operations:
            operation["target_role"] = roles[0]
    refusals = target_grounding.refuse_ungrounded(operations, {"targets": rows})
    counts = {state: 0 for state in target_grounding.STATES}
    for row in rows:
        counts[str(row["state"])] = counts.get(str(row["state"]), 0) + 1
    return {
        "brief_id": str(brief.get("id") or ""),
        "outcome": str(brief.get("outcome") or ""),
        "substrate": substrate,
        "recorded_timeline": note,
        "role_instances": len(rows),
        "grounded_role_instances": counts[target_grounding.VERIFIED],
        "observed_role_instances": counts[target_grounding.OBSERVED],
        "claimed_role_instances": counts[target_grounding.CLAIMED],
        "unresolved_role_instances": counts[target_grounding.UNRESOLVED],
        "planned_operations": len(operations),
        "destructive_operations": len([operation for operation in operations
                                       if target_grounding.is_destructive(
                                           operation["op"])]),
        "destructive_operations_refused": len(refusals),
        "refusals": refusals,
        "targets": rows,
    }


def measure() -> dict[str, Any]:
    """The measurement over the corpus."""
    from corpus import SEALED_EVIDENCE

    brief_set = load_sealed_briefs()
    catalogue = load_sealed_substrates()
    evidence = json.loads(SEALED_EVIDENCE.read_text(encoding="utf-8"))
    audit = premise_audit(brief_set, catalogue)
    timelines: dict[str, tuple[list[dict[str, Any]], str]] = {}
    for row in audit["rows"]:
        substrate = str(row.get("substrate") or "")
        if not substrate or substrate in timelines:
            continue
        aliases = []
        for entry in catalogue.get("substrates") or ():
            if isinstance(entry, Mapping) and str(entry.get("id")) == substrate:
                aliases = [str(alias) for alias in (entry.get("aliases") or ())]
        timelines[substrate] = _recorded_items(aliases)
    briefs = []
    for brief, row in zip(brief_set["briefs"], audit["rows"]):
        briefs.append(ground_brief(brief, catalogue, row, evidence, timelines))
    roles = sum(row["role_instances"] for row in briefs)
    grounded = sum(row["grounded_role_instances"] for row in briefs)
    observed = sum(row["observed_role_instances"] for row in briefs)
    claimed = sum(row["claimed_role_instances"] for row in briefs)
    unresolved = sum(row["unresolved_role_instances"] for row in briefs)
    destructive = sum(row["destructive_operations"] for row in briefs)
    refused = sum(row["destructive_operations_refused"] for row in briefs)
    return {
        "schema": SCHEMA,
        "brief_count": len(briefs),
        "role_instances": roles,
        "grounded_role_instances": grounded,
        "observed_role_instances": observed,
        "claimed_role_instances": claimed,
        "unresolved_role_instances": unresolved,
        "destructive_operations": destructive,
        "destructive_operations_refused": refused,
        "value": ((grounded / roles) if roles else None),
        "briefs": briefs,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="grounding_corpus.py",
        description=("Measure PLAN-003 on the representative corpus: the "
                     "target roles grounded, and the destructive plans "
                     "refused for a target that could not be established."))
    parser.add_argument("--report", default=None,
                        help="where to write the report")
    args = parser.parse_args(list(argv) if argv is not None else None)

    payload = measure()
    text = json.dumps(payload, indent=2, sort_keys=True, default=str)
    if args.report:
        Path(args.report).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
