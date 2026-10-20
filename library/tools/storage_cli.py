"""`ren storage` command surface over the shared lifecycle contract."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from library.tools import project_registry, storage_lifecycle


def start_cleanup_workers(result: dict, *, changed_only: bool = False) -> list[int]:
    """Detach guarded cleanup workers so Ren's exec handoff cannot lose them."""
    engine_root = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        path for path in (str(engine_root), env.get("PYTHONPATH", "")) if path
    )
    env["REN_ENGINE_ROOT"] = str(engine_root)
    started = []
    for project in result.get("projects", []):
        if not project.get("pending_job_ids"):
            continue
        if changed_only and not project.get("worker_required"):
            continue
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "library.tools.storage_cli",
                "--resume-cleanup-worker",
                project["project"],
            ],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
        started.append(process.pid)
    return started


def _projects(project: str) -> list[str]:
    if project:
        path = Path(project).expanduser()
        if not path.is_dir():
            resolved = project_registry.resolve_project_path(project)
            if resolved is not None:
                path = resolved.parent
            else:
                config = next(
                    (
                        item
                        for item in project_registry.list_projects()
                        if item.slug == project
                    ),
                    None,
                )
                if config is not None:
                    path = config.project_root
                else:
                    raise ValueError(
                        f"project does not resolve to a directory: {project}"
                    )
        return [str(path.resolve())]
    return [str(config.project_root) for config in project_registry.list_projects()]


def _render(data: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return
    print("REN STORAGE")
    for kind, totals in sorted(data.get("by_kind", {}).items()):
        print(
            f"{kind:28} {totals['logical_bytes']:>12,} bytes logical  "
            f"{totals['physical_bytes']:>12,} bytes physical"
        )
    print(f"Registered logical          {data.get('logical_bytes', 0):,} bytes")
    print(f"Physically accounted        {data.get('physical_bytes', 0):,} bytes")
    print(f"Safely reclaimable          {data.get('reclaimable_bytes', 0):,} bytes")
    print(
        f"Active / protected         {data.get('active_bytes', 0):,} / "
        f"{data.get('protected_bytes', 0):,} bytes"
    )
    if "free_bytes" in data:
        print(
            f"Disk pressure               {data['disk_pressure'].upper()} "
            f"({data['free_bytes']:,} bytes free)"
        )
    if data.get("legacy"):
        legacy = data["legacy"]
        print(
            f"Legacy entries               {legacy['entries']:,} "
            f"({legacy['physical_bytes']:,} bytes physical)"
        )
        print(
            f"Legacy review only           {legacy['review_only']:,} "
            f"({legacy['review_only_physical_bytes']:,} bytes physical)"
        )
    for project, totals in sorted(
            (data.get("by_project") or {}).items(),
            key=lambda item: item[1].get("physical_bytes", 0),
            reverse=True):
        legacy = totals.get("legacy") or {}
        print(f"PROJECT {project}")
        print(f"  registered                "
              f"{totals.get('artifacts', 0):,} artifacts, "
              f"{totals.get('physical_bytes', 0):,} bytes physical, "
              f"{totals.get('logical_bytes', 0):,} bytes logical")
        print(f"  reclaimable               "
              f"{totals.get('reclaimable_bytes', 0):,} bytes")
        print(f"  legacy                    "
              f"{legacy.get('entries', 0):,} entries, "
              f"{legacy.get('physical_bytes', 0):,} bytes physical")
        analysis = totals.get("analysis") or {}
        measured = (analysis.get("totals") or {})
        print(f"  analysis wall / cpu       "
              f"{measured.get('wall_s', 0):,.3f} s / "
              f"{measured.get('cpu_s', 0):,.3f} s, peak "
              f"{measured.get('peak_rss_mb', 0):,.1f} MB")
        if measured.get("capability_bytes_measured"):
            print(f"  analysis bytes written    "
                  f"{measured.get('capability_bytes_written', 0):,} bytes")
        else:
            print("  analysis bytes written    not measured on this run")
    if data.get("manifest"):
        print(f"Manifest                    {data['manifest']}")
    if data.get("candidates"):
        print(f"Candidates                  {len(data['candidates'])}")
        for row in data["candidates"]:
            print(
                f"  {row['kind']}: {row['path']} "
                f"({row['identity']['size_bytes']:,} bytes)"
            )
    if data.get("refused_policy"):
        print(
            f"Refused by policy           {len(data['refused_policy'])} "
            f"(the area protects them)"
        )
        for row in data["refused_policy"]:
            print(
                f"  {row['kind']}: {row['path']} "
                f"({row['retention_class']}) - {row['reason']}"
            )
    for key, label in (
        ("quarantined", "Quarantined"),
        ("restored", "Restored"),
        ("purged", "Purged"),
    ):
        if key in data:
            print(f"{label}                     {len(data[key])} artifact(s)")
    if "review_required" in data:
        print(f"Legacy resources             {len(data.get('resources', []))}")
        print(f"Review required               {data['review_required']}")
    census = data.get("resolve_census")
    if isinstance(census, dict):
        print(
            "Resolve census                "
            f"{len(census.get('recorded', []))} recorded identity(ies); "
            f"{census.get('review_required', 0)} require review"
        )
        for row in census.get("recorded", []):
            if row.get("census") != "present":
                print(f"  {row['census']}: {row['artifact_id']}")
        for row in census.get("legacy_unverified", []):
            label = row.get("object_name") or row.get("timeline") or "unknown"
            print(f"  unverified legacy Resolve resource: {label}")
    for project in data.get("projects", []):
        print(f"Reconciled                    {project['project']}")
        for key in (
            "abandoned_jobs_recovered",
            "refused_jobs_retried",
            "record_filesystem_drift",
            "expired_quarantine_review",
        ):
            if project.get(key):
                print(f"  {key}: {project[key]}")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) == 2 and argv[0] == "--resume-cleanup-worker":
        from library.tools.retention import _process_cleanup_queue

        _process_cleanup_queue(argv[1])
        return 0
    parser = argparse.ArgumentParser(prog="ren storage")
    sub = parser.add_subparsers(dest="operation", required=True)
    for name in ("status", "plan", "plan-preview"):
        command = sub.add_parser(name)
        command.add_argument(
            "project", nargs="?", help="project path; defaults to registered projects"
        )
        command.add_argument("--json", action="store_true")
        if name == "plan":
            command.add_argument("--manifest", default="ren-storage-plan.json")
    for name in ("collect", "purge", "restore"):
        command = sub.add_parser(name)
        command.add_argument("manifest")
        command.add_argument("--json", action="store_true")
    reconcile = sub.add_parser("reconcile")
    reconcile.add_argument(
        "project",
        nargs="?",
        help="project path or slug; defaults to all registered projects",
    )
    reconcile.add_argument(
        "--resolve-maintenance-window",
        action="store_true",
        help="take Resolve's exclusive lease and census the currently open project's exact IDs",
    )
    reconcile.add_argument("--json", action="store_true")
    legacy = sub.add_parser("legacy-inventory")
    legacy.add_argument("--manifest", default="ren-legacy-inventory.json")
    legacy.add_argument(
        "--root",
        action="append",
        default=[],
        metavar="CATEGORY=PATH",
        help="inventory an explicit legacy root (repeatable)",
    )
    legacy.add_argument(
        "--register-project-scratch",
        action="store_true",
        help="register exact observed-provenance scratch files as disposable",
    )
    legacy.add_argument(
        "--project",
        action="append",
        default=[],
        help="project path or slug to reconstruct (repeatable)",
    )
    legacy.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.operation == "status":
            result = storage_lifecycle.status(_projects(args.project))
        elif args.operation == "plan":
            result = storage_lifecycle.plan(
                _projects(args.project), output=args.manifest
            )
        elif args.operation == "plan-preview":
            result = storage_lifecycle.plan_document(_projects(args.project))
        elif args.operation == "reconcile":
            result = storage_lifecycle.reconcile(
                _projects(args.project),
                resolve_maintenance_window=args.resolve_maintenance_window,
            )
            result["cleanup_workers_started"] = start_cleanup_workers(result)
        elif args.operation == "legacy-inventory":
            roots = []
            for declaration in args.root:
                category, separator, path = declaration.partition("=")
                if not separator or not category or not path:
                    raise ValueError("--root must be CATEGORY=PATH")
                roots.append((category, path))
            result = storage_lifecycle.legacy_inventory(
                output=args.manifest, roots=roots or None
            )
            if args.register_project_scratch:
                if not args.project:
                    raise ValueError("--register-project-scratch requires --project")
                result["reconstruction"] = storage_lifecycle.register_legacy_artifacts(
                    [
                        path
                        for declaration in args.project
                        for path in _projects(declaration)
                    ]
                )
        elif args.operation == "collect":
            result = storage_lifecycle.collect(args.manifest)
        elif args.operation == "purge":
            result = storage_lifecycle.purge(args.manifest)
        else:
            result = storage_lifecycle.restore(args.manifest)
        _render(result, args.json)
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print(f"ren storage: refused - {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
