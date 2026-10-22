#!/usr/bin/env python3
"""Measure how much of the MODEL-FACING PLAN is bound to one NLE.

The punch list asks (INFRA-004) to "avoid embedding Resolve-specific
commands in high-level creative plans", and the sibling lane
(`scripts/measure_intent_neutrality.py`) took the intent record and the
machine plan out of Resolve's hands.  The surface this script measures
is the one the MODEL reads and writes:

1. the CONTEXT - the rows `edit_spec` hands the model, which used to
   carry every recorded Resolve item id;
2. the PLAN - the operation targets the model writes, as the engine
   RECORDS them after reading the model's answer.

The measurement answers two questions with denominators:

* How many fields of the model-facing plan carry a Resolve object
  identifier?  Run this script on the pre-change revision and on the
  change, over the SAME committed corpus, and the two numbers are
  comparable.
* Do the recorded requests still route identically?  Every recorded
  request is rebuilt, compiled and digested, and the digest of the
  request written WITHOUT a Resolve id is compared with the digest of
  the request as it was recorded.

The corpus is committed recorded evidence: the recorded reel-26 timeline
export and the recorded live receipts.  Nothing here contacts Resolve,
and nothing writes to a project.

    python3 scripts/measure_model_plan_neutrality.py \
        --corpus tests/fixtures/intent_neutrality/corpus.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from library.tools import edit_preparation, edit_request, edit_spec

#: The fields whose value IS a Resolve object identifier.  The same
#: enumeration the engine uses (`intent_neutrality`), read here
#: directly so this script runs on the revision BEFORE that module
#: existed - which is the point of it.
RESOLVE_SPECIFIC_FIELDS = ("unique_id", "resolve_item_id",
                           "media_pool_item_id", "media_pool_item",
                           "timeline_id")


def scan(record) -> list[str]:
    """Every path in a record whose key names a Resolve object."""
    hits: list[str] = []

    def walk(node, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                here = f"{path}.{key}" if path else str(key)
                if (str(key) in RESOLVE_SPECIFIC_FIELDS
                        and isinstance(value, str) and value.strip()):
                    hits.append(here)
                walk(value, here)
        elif isinstance(node, (list, tuple)):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")

    walk(record, "")
    return hits


def count_fields(record) -> int:
    """How many keyed fields a record holds - the denominator."""
    total = 0

    def walk(node) -> None:
        nonlocal total
        if isinstance(node, dict):
            total += len(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                walk(value)

    walk(record)
    return total


def measure(record) -> dict:
    fields = count_fields(record)
    hits = scan(record)
    return {"fields": fields, "resolve_specific_fields": len(hits),
            "paths": hits}


def digest(payload) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False,
                   default=str).encode("utf-8")).hexdigest()[:16]


def router_input(compiled) -> dict:
    """The `(compiled_edit, context)` pair the router consumes."""
    return {"compiled": compiled.to_router_input()[0],
            "order": list(compiled.order),
            "kinds": list(compiled.kinds)}


def _address_of_clip(clip: dict) -> dict:
    """The neutral address of one recorded clip, read off the snapshot.

    The rule is the engine's own (`intent_neutrality.address_of`), read
    here directly so the script runs on the revision BEFORE that module
    existed.
    """
    source_file = str(clip.get("source_file") or "")
    source_in = clip.get("source_in_frame")
    source_out = clip.get("source_out_frame")
    address = {
        "source_file": source_file,
        "source_in_frame": source_in,
        "source_out_frame": source_out,
        "track_type": str(clip.get("track_type") or ""),
        "track_index": clip.get("track_index"),
        "record_in": clip.get("record_in"),
        "record_out": clip.get("record_out"),
    }
    if not source_file or source_in is None or source_out is None:
        # An item with no material: the address is its placement.
        return {"source_file": "", "source_in_frame": None,
                "source_out_frame": None,
                "track_type": address["track_type"],
                "track_index": address["track_index"],
                "record_in": address["record_in"],
                "record_out": address["record_out"]}
    return address


def _model_answer(request: dict) -> dict:
    """The model's answer, as the recorded plan states its targets.

    The recorded corpus says WHAT the model wrote: an operation naming
    its target by the recorded instance id.  This rebuilds that answer
    as an `edit_spec` program, so the engine's own reader can be
    measured on it.
    """
    ops = []
    for index, operation in enumerate(request.get("operations") or ()):
        params = {key: value for key, value in operation.items()
                  if key not in {"op", "target", "kind", "expected_effect"}}
        target = operation.get("target") or {}
        for key, value in target.items():
            if key in {"editorial_identity"}:
                continue
            params.setdefault(key, value)
        ops.append({"op": operation.get("op"), "text": "recorded operation",
                    "params": params})
    return {"format": edit_spec.FORMAT,
            "request": request.get("id") or "",
            "program": {"kind": "edit_program",
                        "base_generation": str(
                            request.get("expected_base_generation") or 1),
                        "idempotency_key": request.get("id") or "",
                        "ops": ops}}


def _read_rows(snapshot: dict) -> list[dict]:
    """The rows the model is shown, as the engine builds them."""
    return edit_spec._timeline_rows(snapshot)


def _recorded_plan(spec: dict, timeline: dict, snapshot: dict) -> dict:
    """The program as the engine RECORDS it after reading the answer.

    On the revision before this lane, the reader had no address to
    translate an instance id into, so the id stayed in the recorded
    plan.  On the revision after it, the same reader translates the id
    into the material it stands for.
    """
    try:
        edit_spec._clarify_unlisted_program_targets(spec, timeline, snapshot)
    except TypeError:
        edit_spec._clarify_unlisted_program_targets(spec, timeline)
    return spec


def _bound_compile(request, resolved, snapshot):
    """The compiled edit a request becomes once its identities bind."""
    bind = getattr(edit_preparation, "bind_request", None)
    if bind is None:
        bind = edit_preparation._bind_request
    bound, _notes = bind(request, resolved)
    return edit_request.compile_request(bound or request, snapshot)


def run(corpus: dict) -> dict:
    snapshot = corpus.get("snapshot") or {}
    report: dict = {
        "schema": "ren-model-plan-neutrality-measurement/1",
        "corpus": corpus.get("provenance") or {},
    }
    requests = list(corpus.get("requests") or ())

    # 1. The CONTEXT the model reads.
    rows = _read_rows(snapshot)
    context = measure({"rows": rows})
    report["model_facing_context"] = {
        "rows": len(rows),
        "items": sum(len(row.get("items") or ()) for row in rows),
        "fields": context["fields"],
        "resolve_specific_fields": context["resolve_specific_fields"],
    }

    # 2. The PLAN the model writes, as the engine records it.
    timeline = {"available": True, "rows": rows}
    plan_fields = 0
    plan_hits = 0
    recorded = 0
    for request in requests:
        answer = _model_answer(request)
        recorded_spec = _recorded_plan(answer, timeline, snapshot)
        counted = measure(recorded_spec)
        plan_fields += counted["fields"]
        plan_hits += counted["resolve_specific_fields"]
        recorded += 1
    report["model_facing_plan"] = {
        "answers": recorded,
        "fields": plan_fields,
        "resolve_specific_fields": plan_hits,
    }

    # 3. Routing: the recorded requests, and the same requests written
    #    without a Resolve id.
    digests = []
    neutral_digests = []
    neutral_supported = True
    resolved_by_address = 0
    prepared = 0
    for request in requests:
        built = edit_request.EditRequest.from_dict(request)
        result = edit_preparation.prepare(
            built, base_snapshot=snapshot, base_generation=1)
        if not result.ready:
            continue
        prepared += 1
        digests.append(digest(router_input(
            edit_request.compile_request(built, snapshot))))
        neutral = _neutral_request(request, snapshot)
        if neutral is None:
            neutral_supported = False
            continue
        try:
            neutral_built = edit_request.EditRequest.from_dict(neutral)
            neutral_result = edit_preparation.prepare(
                neutral_built, base_snapshot=snapshot, base_generation=1)
            if not neutral_result.ready:
                neutral_supported = False
                continue
            for row in neutral_result.resolved:
                if row.status == "resolved" and "address" in row.note:
                    resolved_by_address += 1
            compiled = _bound_compile(neutral_built, neutral_result.resolved,
                                      snapshot)
        except Exception:  # noqa: BLE001 - a revision that cannot bind it
            neutral_supported = False
            continue
        neutral_digests.append(digest(router_input(compiled)))
    report["routing"] = {
        "requests": len(requests),
        "prepared": prepared,
        "recorded_digest": digest(digests),
        "neutral_digest": (digest(neutral_digests)
                            if neutral_digests else None),
        "neutral_supported": neutral_supported,
        "neutral_resolved_by_address": resolved_by_address,
        "same_route": bool(neutral_digests) and (
            digest(neutral_digests) == digest(digests)),
    }
    return report


def _neutral_request(request: dict, snapshot: dict) -> dict | None:
    """The same request with every target written as an ADDRESS.

    The target of each operation becomes the material it plays (or the
    placement it occupies), so the request names no Resolve object at
    all.  `None` when the recorded corpus cannot state a target
    neutrally - which the report says, rather than hiding.
    """
    clips = {}
    for track in snapshot.get("tracks") or ():
        for clip in track.get("clips") or ():
            unique_id = str(clip.get("unique_id") or "")
            if unique_id:
                clips[unique_id] = clip
    neutral = json.loads(json.dumps(request))
    for operation in neutral.get("operations") or ():
        target = operation.get("target") or {}
        unique_id = str(target.get("unique_id") or "")
        clip = clips.get(unique_id)
        if clip is None:
            return None
        address = _address_of_clip(clip)
        if not (address["source_file"]
                and address["source_in_frame"] is not None
                and address["source_out_frame"] is not None):
            return None
        operation["target"] = {
            "source_file": address["source_file"],
            "source_in_frame": address["source_in_frame"],
            "source_out_frame": address["source_out_frame"],
            "track_type": address["track_type"],
            "track_index": address["track_index"],
        }
    return neutral


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus",
                        default="tests/fixtures/intent_neutrality/corpus.json")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    corpus = json.loads(Path(args.corpus).read_text(encoding="utf-8"))
    report = run(corpus)
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
