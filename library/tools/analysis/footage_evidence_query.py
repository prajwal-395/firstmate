"""Typed Project Model evidence queries for the `ren evidence` Footage verb."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from library.tools.ren_refusal import REFUSAL_EXIT_CODE, RenRefusal


def main(argv: list[str] | None = None) -> int:
    from ren import hosts

    hosts.ensure_harness_output_env()
    parser = argparse.ArgumentParser(
        prog="ren evidence",
        description="Query typed Project Model evidence; acquisition is opt-in.",
    )
    parser.add_argument("project")
    parser.add_argument(
        "plan", help="JSON file containing a compiled evidence query plan"
    )
    parser.add_argument(
        "--acquire",
        action="store_true",
        help="opt in to bounded acquisition of missing M4 scene evidence",
    )
    parser.add_argument(
        "--max-cost-seconds",
        type=float,
        default=90.0,
        help="hard acquisition cost ceiling (default: one 10s M4 window)",
    )
    parser.add_argument("--memory-root")
    parser.add_argument(
        "--purpose",
        choices=("commercial_client", "personal_noncommercial", "permitted_research"),
        default="commercial_client",
        help="workload purpose used to authorize model evidence acquisition",
    )
    args = parser.parse_args(argv)

    from library.tools import evidence_query, project_model_store, source_memory
    from library.tools.project_model_cli import resolve_project_root

    project = resolve_project_root(args.project)
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    source_revision, model = project_model_store.load_project_model(project)
    from library.tools import project_model_acquisition, model_registry

    model = project_model_acquisition.filter_m4_by_reuse_policy(
        model, project,
        edition=("commercial" if args.purpose == "commercial_client"
                 else "personal"),
        purpose=args.purpose, registry=model_registry.load_registry(),
    )
    if not args.acquire:
        answer = evidence_query.evidence_for(model, plan)
        print(
            json.dumps(
                {"answer": answer, "edit_ready": answer.get("status") == "present",
                 "source_revision": source_revision,
                 "result_revision": source_revision},
                indent=2,
            )
        )
        return 0

    scope = plan.get("scope") or {}
    asset_id = scope.get("asset_id")
    asset = next((row for row in model["assets"] if row.get("id") == asset_id), None)
    if asset is None:
        raise RenRefusal(
            "evidence acquisition needs one scoped asset",
            "the plan must identify an asset_id in scope",
            "compile the plan with scope.asset_id set",
        )
    digest = asset["content_digest"]
    memory_root = Path(args.memory_root).expanduser() if args.memory_root else None
    m0 = source_memory.read_m0(digest, memory_root) or {}
    duration = m0.get("duration_seconds")
    if not duration:
        raise RenRefusal(
            "evidence acquisition needs source duration",
            "M0 duration is unavailable for this asset",
            "run source metadata analysis before acquiring evidence",
        )
    from library.tools.project_model_acquisition import query_project

    answer = query_project(
        project,
        model,
        plan,
        source_digest=digest,
        source_file=asset["uri"],
        duration_seconds=float(duration),
        max_cost_seconds=args.max_cost_seconds,
        workload_purpose=args.purpose,
        memory_root=memory_root,
    )
    print(json.dumps(answer, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RenRefusal as refused:
        from library.tools.ren_refusal import emit_refusal_result

        print(refused.render(), file=sys.stderr)
        emit_refusal_result(refused)
        sys.exit(REFUSAL_EXIT_CODE)
    except Exception as exc:  # noqa: BLE001 - unexpected errors are data too
        from library.tools.ren_refusal import report_unexpected

        sys.exit(report_unexpected(exc))
