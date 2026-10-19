"""`ren storage` command surface over the shared lifecycle contract."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from library.tools import project_registry, storage_lifecycle


def _projects(project: str) -> list[str]:
    if project:
        path = Path(project).expanduser()
        if not path.is_dir():
            resolved = project_registry.resolve_project_path(project)
            if resolved is not None:
                path = resolved.parent
            else:
                config = next((item for item in project_registry.list_projects()
                               if item.slug == project), None)
                if config is not None:
                    path = config.project_root
                else:
                    raise ValueError(
                        f"project does not resolve to a directory: {project}")
        return [str(path.resolve())]
    return [str(config.project_root) for config in project_registry.list_projects()]


def _render(data: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return
    print("REN STORAGE")
    for kind, totals in sorted(data.get("by_kind", {}).items()):
        print(f"{kind:28} {totals['logical_bytes']:>12,} bytes logical  "
              f"{totals['physical_bytes']:>12,} bytes physical")
    print(f"Registered logical          {data.get('logical_bytes', 0):,} bytes")
    print(f"Physically accounted        {data.get('physical_bytes', 0):,} bytes")
    print(f"Safely reclaimable          {data.get('reclaimable_bytes', 0):,} bytes")
    print(f"Active / protected         {data.get('active_bytes', 0):,} / "
          f"{data.get('protected_bytes', 0):,} bytes")
    if "free_bytes" in data:
        print(f"Disk pressure               {data['disk_pressure'].upper()} "
              f"({data['free_bytes']:,} bytes free)")
    if data.get("manifest"):
        print(f"Manifest                    {data['manifest']}")
    if data.get("candidates"):
        print(f"Candidates                  {len(data['candidates'])}")
        for row in data["candidates"]:
            print(f"  {row['kind']}: {row['path']} "
                  f"({row['identity']['size_bytes']:,} bytes)")
    for key, label in (("quarantined", "Quarantined"),
                       ("restored", "Restored"), ("purged", "Purged")):
        if key in data:
            print(f"{label}                     {len(data[key])} artifact(s)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ren storage")
    sub = parser.add_subparsers(dest="operation", required=True)
    for name in ("status", "plan", "plan-preview"):
        command = sub.add_parser(name)
        command.add_argument("project", nargs="?", help="project path; defaults to registered projects")
        command.add_argument("--json", action="store_true")
        if name == "plan":
            command.add_argument("--manifest", default="ren-storage-plan.json")
    for name in ("collect", "purge", "restore"):
        command = sub.add_parser(name)
        command.add_argument("manifest")
        command.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.operation == "status":
            result = storage_lifecycle.status(_projects(args.project))
        elif args.operation == "plan":
            result = storage_lifecycle.plan(
                _projects(args.project), output=args.manifest)
        elif args.operation == "plan-preview":
            result = storage_lifecycle.plan_document(_projects(args.project))
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
