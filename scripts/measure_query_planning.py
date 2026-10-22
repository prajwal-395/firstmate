"""R03 measurement: deterministic filter extraction and step order.

For every query in tests/fixtures/query_planning/queries.json, two
routes are scored against the hand-recorded expectations:

* ``before`` - the substrate that exists today: requirement_graph's
  parse plus compile, mapped onto the filter slots (a speech topic
  becomes the transcript phrase, an image presence becomes the object,
  an action candidate becomes the transition, a technical cue becomes
  the shot context; person, ordinal, time range and camera have no
  slot and always miss). Step order is the requirements' textual
  order. The baseline runs on the current substrate, which can only
  flatter it: every stopword and ordering fix in the substrate helps
  ``before`` as much as ``after``.
* ``after`` - library.tools.query_planner.plan_query.

Two metrics, both denominators stated:

1. filter exactness - the share of queries whose every slot matches
   (an expected slot matches when its tokens are all extracted; an
   absent slot matches when nothing is extracted), plus slot recall
   over all expected slots;
2. step order - the share of multi-step plans whose executable steps
   run cheap filters before expensive verification (refused and
   superseded steps do not execute and are excluded; single-step
   plans are reported separately, never as ordered).

Nothing here loads a model, renders, or touches a project: the
fixture is strings, the planners are closed tables.
"""

from __future__ import annotations

import argparse
import json
import sys
from itertools import pairwise
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from library.tools import query_planner as qp
from library.tools import requirement_graph as rg

FILTER_SLOTS = (
    "person",
    "source_camera",
    "transcript_phrase",
    "time_range",
    "object_category",
    "action_transition",
    "shot_scene",
    "ordinal",
)


def baseline_filters(text: str) -> dict:
    """The slots the substrate's parse fills, without the R03 stage."""
    try:
        graph = rg.parse_query(text)
    except (ValueError, rg.RequirementGraphError):
        return {}
    out: dict = {}
    for req in graph.get("requirements") or []:
        family, kind = req.get("family"), req.get("kind")
        value = req.get("value")
        if family == "speech" and kind == "topic" and value:
            out.setdefault("transcript_phrase", {"phrase": value, "match": "topic"})
        elif family == "image" and kind == "presence" and value:
            out.setdefault("object_category", []).append(str(value))
        elif family == "action" and value:
            out.setdefault("action_transition", []).append(
                {"candidate_kind": value}
                if kind == "candidate"
                else {"verb": str(value)}
            )
        elif family == "technical" and value:
            out.setdefault("shot_scene", {}).setdefault("framing", []).append(
                str(value)
            )
    return out


def baseline_order_ok(text: str) -> bool | None:
    """Whether textual requirement order already runs cheap first."""
    try:
        graph = rg.parse_query(text)
        compiled = rg.compile_graph(graph)
    except (ValueError, rg.RequirementGraphError):
        return None
    tiers = [
        qp._tier_of(item)
        for item in compiled.get("compiled") or []
        if item.get("kind") != "unanswerable"
    ]
    if len(tiers) < 2:
        return None
    rank = {qp.CHEAP: 0, qp.EXPENSIVE: 1}
    return all(rank[a] <= rank[b] for a, b in pairwise(tiers))


def _texts(value) -> str:
    if isinstance(value, dict):
        return " ".join(str(part) for part in value.values())
    if isinstance(value, list):
        return " ".join(_texts(part) for part in value)
    return str(value)


def slot_matches(slot: str, expected, extracted: dict) -> bool:
    """One slot: expected tokens all present, or both absent."""
    got = extracted.get(slot)
    if expected is None:
        return got in (None, [], {})
    if got in (None, [], {}):
        return False
    if slot == "ordinal":
        return got == expected
    if slot == "time_range":
        return (
            isinstance(got, dict)
            and abs(got.get("start", -1) - expected[0]) < 1e-9
            and abs(got.get("end", -1) - expected[1]) < 1e-9
        )
    if slot == "person":
        if not isinstance(got, dict):
            return False
        if "track" in expected:
            return got.get("track") == expected["track"]
        if "role" in expected:
            return got.get("role") == expected["role"]
        return False
    if slot == "transcript_phrase":
        hay = _texts(got).lower()
        tokens = expected if isinstance(expected, list) else [expected]
        return all(token.lower() in hay for token in tokens)
    if slot == "object_category":
        hay = _texts(got).lower()
        return all(token.lower() in hay for token in expected)
    if slot == "action_transition":
        hay = _texts(got).lower()
        return all(token.lower() in hay for token in expected)
    if slot == "shot_scene":
        hay = _texts(got).lower()
        return all(token.lower() in hay for token in expected)
    if slot == "source_camera":
        hay = _texts(got).lower()
        tokens = expected if isinstance(expected, list) else [expected]
        return all(token.lower() in hay for token in tokens)
    return False


def score_row(query: str, expected: dict, extracted: dict) -> dict:
    slots = {
        slot: slot_matches(slot, expected.get(slot), extracted) for slot in FILTER_SLOTS
    }
    return {"slots": slots, "exact": all(slots.values())}


def order_ok(plan: dict | None) -> bool | None:
    if plan is None:
        return None
    executable = [
        step
        for step in plan.get("steps") or []
        if step.get("kind") in ("filter", "verify", "inference")
        and step.get("status", "active") == "active"
    ]
    if len(executable) < 2:
        return None
    rank = {qp.CHEAP: 0, qp.EXPENSIVE: 1}
    return all(rank[a["tier"]] <= rank[b["tier"]] for a, b in pairwise(executable))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="R03 measurement: filter extraction and step order, "
        "before vs after."
    )
    parser.add_argument(
        "--fixture", default=str(REPO / "tests/fixtures/query_planning/queries.json")
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    rows = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    results = []
    for entry in rows:
        query, expected = entry["query"], entry.get("expected") or {}
        before = baseline_filters(query)
        try:
            plan = qp.plan_query(query)
            after = plan.get("filters") or {}
        except (ValueError, rg.RequirementGraphError):
            plan, after = None, {}
        results.append(
            {
                "id": entry["id"],
                "query": query,
                "before": score_row(query, expected, before),
                "after": score_row(query, expected, after),
                "order_before": baseline_order_ok(query),
                "order_after": order_ok(plan),
            }
        )

    exact_before = sum(1 for row in results if row["before"]["exact"])
    exact_after = sum(1 for row in results if row["after"]["exact"])
    slot_total = sum(1 for row in results for _ in FILTER_SLOTS)
    slots_before = sum(
        1 for row in results for ok in row["before"]["slots"].values() if ok
    )
    slots_after = sum(
        1 for row in results for ok in row["after"]["slots"].values() if ok
    )
    order_rows_before = [row for row in results if row["order_before"] is not None]
    order_rows_after = [row for row in results if row["order_after"] is not None]
    order_before = sum(1 for row in order_rows_before if row["order_before"])
    order_after = sum(1 for row in order_rows_after if row["order_after"])

    summary = {
        "n_queries": len(results),
        "filter_exact_before": [exact_before, len(results)],
        "filter_exact_after": [exact_after, len(results)],
        "filter_slots_before": [slots_before, slot_total],
        "filter_slots_after": [slots_after, slot_total],
        "order_before": [order_before, len(order_rows_before)],
        "order_after": [order_after, len(order_rows_after)],
        "single_step_or_trivial": [len(results) - len(order_rows_after), len(results)],
    }
    if args.json:
        print(json.dumps({"summary": summary, "rows": results}, indent=1))
        return 0

    print(f"queries: {len(results)}")
    print(
        f"filter-exact  before {exact_before}/{len(results)}  "
        f"after {exact_after}/{len(results)}"
    )
    print(
        f"filter-slots  before {slots_before}/{slot_total}  "
        f"after {slots_after}/{slot_total}"
    )
    print(
        f"step-order    before {order_before}/{len(order_rows_before)}  "
        f"after {order_after}/{len(order_rows_after)}  "
        f"({len(results) - len(order_rows_after)} single-step/trivial)"
    )
    print()
    print("rows where after is not exact:")
    for row in results:
        if not row["after"]["exact"]:
            missed = [slot for slot, ok in row["after"]["slots"].items() if not ok]
            print(f"  {row['id']}: miss {missed}")
            print(f"    q: {row['query'][:110]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
