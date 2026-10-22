#!/usr/bin/env python3
"""Measure how much of an intent record is bound to one NLE (INFRA-004).

The measurement answers two questions with denominators:

1. How many fields of the intent record and of the machine plan carry
   a Resolve object identifier?  Run this script on the pre-change
   revision and on the change, over the SAME committed corpus, and the
   two numbers are comparable.
2. Do the recorded requests still route and execute identically?  For
   every request the repo has RECORDED (the live receipts), the router
   input is digested before and after, and the two are compared.

The corpus is committed evidence: a recorded timeline export and the
recorded live receipts.  Nothing here contacts Resolve, and nothing
writes to a project.

    python3 scripts/measure_intent_neutrality.py \
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

from library.tools import edit_preparation, edit_request

#: The fields whose value IS a Resolve object identifier.  The same
#: enumeration the engine uses (`intent_neutrality`), read here
#: directly so this script runs on the revision BEFORE that module
#: existed - which is the point of it.
RESOLVE_SPECIFIC_FIELDS = ("unique_id", "resolve_item_id",
                           "media_pool_item_id", "timeline_id")


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


def router_input(request) -> dict:
    """The `(compiled_edit, context)` pair the router consumes."""
    compiled = edit_request.compile_request(request)
    return {"compiled": compiled.to_router_input()[0],
            "order": list(compiled.order),
            "kinds": list(compiled.kinds)}


def run(corpus: dict) -> dict:
    snapshot = corpus.get("snapshot") or {}
    report: dict = {
        "schema": "ren-intent-neutrality-measurement/1",
        "corpus": corpus.get("provenance") or {},
    }

    requests = list(corpus.get("requests") or ())
    intent_before = {"fields": 0, "resolve_specific_fields": 0}
    intent_after = {"fields": 0, "resolve_specific_fields": 0}
    plan_before = {"fields": 0, "resolve_specific_fields": 0}
    plan_after = {"fields": 0, "resolve_specific_fields": 0}
    plans = 0
    ready = 0
    neutral_available = False

    for raw in requests:
        request = edit_request.EditRequest.from_dict(raw)
        before = measure(request.to_dict())
        intent_before["fields"] += before["fields"]
        intent_before["resolve_specific_fields"] += (
            before["resolve_specific_fields"])

        neutral = getattr(request, "to_neutral", None)
        if neutral is not None:
            neutral_available = True
            after = measure(neutral().to_dict())
            intent_after["fields"] += after["fields"]
            intent_after["resolve_specific_fields"] += (
                after["resolve_specific_fields"])

        result = edit_preparation.prepare(
            request, base_snapshot=snapshot, base_generation=1)
        plans += 1
        if result.ready:
            ready += 1
        counted = measure(result.plan)
        if neutral_available:
            plan_after["fields"] += counted["fields"]
            plan_after["resolve_specific_fields"] += (
                counted["resolve_specific_fields"])
        else:
            plan_before["fields"] += counted["fields"]
            plan_before["resolve_specific_fields"] += (
                counted["resolve_specific_fields"])

    report["requests"] = {
        "count": len(requests),
        "plans_prepared": plans,
        "plans_ready": ready,
    }
    report["intent_record"] = {
        "before": intent_before,
        "after": intent_after if neutral_available else None,
    }
    report["machine_plan"] = {
        "before": plan_before if not neutral_available else None,
        "after": plan_after if neutral_available else None,
    }

    # The recorded requests: do they still route identically?
    recorded = list(corpus.get("recorded_requests") or ())
    routed = 0
    digests = []
    for entry in recorded:
        ops = entry.get("operations") or ()
        try:
            request = edit_request.from_algebra_ops(
                ops, project="Recorded", timeline="Recorded")
        except Exception:  # noqa: BLE001, S112 - an op the algebra refuses
            continue
        routed += 1
        digests.append(digest(router_input(request)))
    report["recorded_requests"] = {
        "recorded": len(recorded),
        "routed": routed,
        "router_input_digest": digest(digests),
    }
    return report


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
