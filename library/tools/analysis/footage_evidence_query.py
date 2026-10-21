"""Typed Project Model evidence queries for the `ren evidence` Footage verb."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from library.tools.ren_refusal import REFUSAL_EXIT_CODE, RenRefusal


def _object_plan(model: dict, args) -> dict:
    """The plan an --object request compiles to.

    The request names the object and the requirement it carries; the
    scope is the asset it is about, resolved from the Project Model
    rather than typed by the caller.
    """
    from library.tools import evidence_query

    assets = [row for row in model.get("assets") or []
              if isinstance(row, dict) and row.get("id")]
    if args.asset_id:
        assets = [row for row in assets if row.get("id") == args.asset_id]
    if not assets:
        raise RenRefusal(
            "the request names no asset to look at",
            "the project's Project Model holds no asset (or none with "
            "that id)",
            "run the preflight first, or pass --asset-id")
    if len(assets) > 1:
        raise RenRefusal(
            "the request names more than one asset to look at",
            "the project's Project Model holds several assets",
            "pass --asset-id to say which one")
    asset = assets[0]
    clocks = [row for row in model.get("clocks") or []
              if isinstance(row, dict)
              and (row.get("domain") or {}).get("asset_id") == asset["id"]]
    if not clocks:
        raise RenRefusal(
            "the asset has no clock to measure on",
            "the Project Model carries no clock for this asset",
            "run the preflight first")
    scope = {"clock_id": clocks[0]["id"], "asset_id": asset["id"]}
    if args.at:
        parts = str(args.at).replace(":", "-").split("-")
        if len(parts) != 2:
            raise RenRefusal(
                "the window is not a range",
                "--at takes START-END seconds",
                "ren evidence <project> --object <name> --at 30-40")
        try:
            scope["start_s"] = float(parts[0])
            scope["end_s"] = float(parts[1])
        except ValueError:
            raise RenRefusal(
                "the window is not a range of seconds",
                "--at takes START-END seconds",
                "ren evidence <project> --object <name> --at 30-40") from None
    return evidence_query.compile_plan(
        [{"predicate": "object_presence", "phrase": args.object,
          "requirement": args.requirement}], scope=scope)



def main(argv: list[str] | None = None) -> int:
    from ren import hosts

    hosts.ensure_harness_output_env()
    parser = argparse.ArgumentParser(
        prog="ren evidence",
        description=("Query typed Project Model evidence, or trace one "
                     "entity, event or observation through it; acquisition is "
                     "opt-in."),
    )
    parser.add_argument("project")
    parser.add_argument(
        "plan", nargs="?",
        help="JSON file containing a compiled evidence query plan")
    parser.add_argument(
        "--trace", metavar="REF",
        help=("trace one entity, event, observation or track through its "
              "observations, identity links, interpretations and source "
              "evidence: why does Ren believe this?"),
    )
    parser.add_argument(
        "--hops", type=int, default=2,
        help="how many relation and identity-link hops the trace walks "
             "(default: 2)")
    parser.add_argument(
        "--object",
        help=("ask for a named object; the plan is compiled here, so the "
              "plan file is optional"),
    )
    parser.add_argument(
        "--requirement",
        choices=("presence", "grounded"),
        default="grounded",
        help=("what the object request needs: 'presence' asks whether the "
              "object is in the window, 'grounded' asks where it is"),
    )
    parser.add_argument(
        "--asset-id",
        help=("the asset an --object request is about (the only asset when "
              "the project holds one)"),
    )
    parser.add_argument(
        "--at",
        help="the window an --object request covers, as START-END seconds",
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
        "--no-record",
        action="store_true",
        help="answer without filing the result in the project's model",
    )
    parser.add_argument(
        "--purpose",
        choices=("commercial_client", "personal_noncommercial", "permitted_research"),
        default="commercial_client",
        help="workload purpose used to authorize model evidence acquisition",
    )
    args = parser.parse_args(argv)

    if not args.trace and not args.plan and not args.object:
        raise RenRefusal(
            "an evidence query needs a plan, a reference to trace, or an "
            "object to look for",
            "the verb was given neither a compiled plan file, nor --trace, "
            "nor --object",
            "pass a compiled plan file, `ren evidence <project> --trace "
            "<entity|event|observation>`, or `ren evidence <project> "
            "--object <name>`",
        )
    if args.trace and (args.plan or args.object):
        raise RenRefusal(
            "a trace and a plan ask two different questions",
            "a trace was given together with a plan file or --object",
            "run the trace on its own, or the plan on its own",
        )
    if args.plan and args.object:
        raise RenRefusal(
            "a plan and an object request ask two different questions",
            "both a plan file and --object were given",
            "run the plan on its own, or the object request on its own",
        )

    from library.tools import evidence_query, project_model_store, source_memory
    from library.tools.project_model_cli import resolve_project_root

    project = resolve_project_root(args.project)
    source_revision, model = project_model_store.load_project_model(project)
    from library.tools import project_model_acquisition, model_registry

    model = project_model_acquisition.filter_m4_by_reuse_policy(
        model, project,
        edition=("commercial" if args.purpose == "commercial_client"
                 else "personal"),
        purpose=args.purpose, registry=model_registry.load_registry(),
    )
    if args.trace:
        from library.tools import evidence_trace

        answer = evidence_trace.trace(model, args.trace, project=str(project),
                                      hops=args.hops)
        print(json.dumps({"answer": answer,
                          "edit_ready": False,
                          "source_revision": source_revision,
                          "result_revision": source_revision}, indent=2))
        return 0
    if args.object:
        plan = _object_plan(model, args)
    else:
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    if not args.acquire:
        answer = evidence_query.evidence_for(model, plan,
                                             project=str(project))
        recorded = _record(project, model, answer, source_revision,
                           enabled=not args.no_record)
        print(
            json.dumps(
                {"answer": answer, "edit_ready": answer.get("status") == "present",
                 "source_revision": source_revision,
                 "result_revision": recorded["revision"],
                 "query_result": recorded["row"]},
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
        duration = (asset.get("metadata") or {}).get("duration_seconds")
    if not duration:
        # The object lane measures the source itself, so a project whose
        # M0 has not been built can still answer an object request: the
        # duration is read off the media.
        from library.tools.analysis import object_grounding
        try:
            duration = object_grounding.probe_source(
                asset["uri"]).get("duration_seconds")
        except (OSError, ValueError, KeyError):
            duration = None
    if not duration:
        raise RenRefusal(
            "evidence acquisition needs source duration",
            "neither M0, the asset metadata nor the media carries a "
            "duration",
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
    recorded = _record(project, model,
                       answer.get("requery") or answer.get("answer") or answer,
                       source_revision, enabled=not args.no_record)
    answer = {**answer, "result_revision": recorded["revision"],
              "query_result": recorded["row"]}
    print(json.dumps(answer, indent=2))
    return 0


def _record(project, model: dict, answer: dict, revision: int, *,
            enabled: bool) -> dict:
    """File the answer's row in the model's ``query_results``.

    An answer that cannot be filed is still an answer: the row is
    reported as unfiled with its reason, and the answer itself is
    returned. A query never fails because its own bookkeeping could
    not be written.
    """
    if not enabled:
        return {"revision": revision, "row": None, "filed": False,
                "reason": "recording was declined (--no-record)"}
    from library.tools import project_model_store, query_results

    row = query_results.row_for_answer(answer)
    try:
        new_revision = project_model_store.record_query_result(
            project, model, row, expected_revision=revision)
    except Exception as exc:  # noqa: BLE001 - the answer still stands
        return {"revision": revision, "row": row, "filed": False,
                "reason": f"the result could not be filed: {exc}"}
    return {"revision": new_revision, "row": row, "filed": True,
            "reason": None}


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
