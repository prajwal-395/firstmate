#!/usr/bin/env python3
"""Step 3.0: Build the Global Picture Plan

Builds a global picture plan that selects shots against the whole edit.
The plan considers gesture coverage, continuity, variety, and illustration
of the spoken content for each block, globally across the entire edit.

This step runs after mesh_spine, catalog, and semantic_analysis, and
before assign_aroll and select_broll. The picture plan is a constraint
that assign_aroll and select_broll satisfy, not the decision itself.

Classification: Deterministic / Data Transformation
Input:  { "timed_spine": {...}, "clip_catalog": [...], "semantic_analysis_documents": {...} }
Output: { "picture_plan": {...} }
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
from library.tools.picture_plan import build_picture_plan  # noqa: E402


def main():
    try:
        data = json.loads(sys.stdin.read())
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

    required = ["timed_spine", "clip_catalog"]
    missing = [k for k in required if k not in data]
    if missing:
        print(json.dumps({
            "error": f"Missing required keys: {missing}",
            "step": "3.0_plan_picture",
        }))
        sys.exit(1)

    timed_spine = data["timed_spine"]
    clip_catalog = data["clip_catalog"]
    semantic_docs = data.get("semantic_analysis_documents", {})
    duplicate_groups = data.get("duplicate_groups", [])

    # The footage index vouches per block, best-effort and
    # backend-agnostic. A missing or degraded index is the catalog path,
    # which plans exactly what it planned before; the plan says which.
    search_hints = None
    project_folder = data.get("project_folder") or ""
    if project_folder:
        try:
            from library.tools import search_in_planning as sip
            search_hints = sip.search_hints_for_blocks(
                project_folder,
                (timed_spine or {}).get("structure") or [],
                clip_catalog)
            filled = sum(1 for h in search_hints.values()
                         if h.get("status") == "filled")
            print(f"  Footage search: {filled}/{len(search_hints)} "
                  f"block(s) vouched", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 - assist, never gate
            print(f"  Footage search hints unavailable: {exc}",
                  file=sys.stderr)
            search_hints = None

    plan = build_picture_plan(
        timed_spine, clip_catalog, semantic_docs, duplicate_groups,
        search_hints=search_hints)
    if search_hints is None:
        plan["search_status"] = "catalog_fallback:hints_unavailable"
    else:
        plan["search_status"] = (
            f"search:{sum(1 for h in search_hints.values() if h.get('status') == 'filled')}"
            f"/{len(search_hints)} vouched")

    print(json.dumps({"picture_plan": plan}))


if __name__ == "__main__":
    main()
