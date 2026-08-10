#!/usr/bin/env python3
"""
manage_project.py - Top-level CLI for managing video projects.

This is the user-facing entry point for project management. It wraps
the project_registry module and provides commands for creating, listing,
running, and archiving video projects.

The repo is the engine. Projects (assets, footage, pipeline output) live
in PIPELINE_PROJECTS_ROOT (default: ~/Documents/content_stuff/video_projects).

Usage:
    python3 manage_project.py list
    python3 manage_project.py list --client lucie --status in_progress
    python3 manage_project.py new my-vlog --name "Beltline Vlog"
    python3 manage_project.py new geo-podcast --name "GEO Podcast" --client lucie
    python3 manage_project.py status geo-podcast
    python3 manage_project.py run geo-podcast
    python3 manage_project.py run geo-podcast --from creative_direction
    python3 manage_project.py archive geo-podcast
    python3 manage_project.py init-root
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Add repo root to path
REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

from library.tools.paths import PROJECTS_ROOT, PILOT_ROOT
from library.tools.project_registry import (
    scan_projects,
    get_project,
    create_project,
    list_projects,
    project_status,
    archive_project,
)
from library.schemas.project_config import ProjectStatus


def cmd_init_root(args):
    """Initialize the projects root directory."""
    root = PROJECTS_ROOT
    if root.exists():
        print(f"  Projects root already exists: {root}")
    else:
        root.mkdir(parents=True)
        print(f"  Created projects root: {root}")

    # Create a README
    readme = root / "README.md"
    if not readme.exists():
        readme.write_text(
            "# Video Projects\n\n"
            "Each subdirectory is a video project managed by the "
            "[video_editing_pilot](../video_editing_pilot) pipeline.\n\n"
            "## Structure\n\n"
            "```\n"
            "<project-slug>/\n"
            "  project.yaml          # Project configuration\n"
            "  raw/                   # Source footage\n"
            "  pipeline_output/       # All pipeline intermediates\n"
            "  exports/               # Final rendered outputs\n"
            "  brand_assets/          # Project-specific brand files\n"
            "  compositions/          # Remotion compositions\n"
            "```\n\n"
            "Projects can be organized flat or grouped by client:\n"
            "```\n"
            "video_projects/\n"
            "  my-vlog/               # flat\n"
            "  lucie/                  # client group\n"
            "    geo-podcast/\n"
            "    monthly-recap/\n"
            "```\n"
        )

    print(f"\n  Projects root: {root}")
    print(f"  Create projects with: python3 manage_project.py new <slug> --name '<name>'")


def cmd_list(args):
    """List all known projects."""
    status_filter = ProjectStatus(args.status) if args.status else None
    configs = list_projects(status=status_filter, client=args.client)

    if not configs:
        print("  No projects found.")
        print(f"  Projects root: {PROJECTS_ROOT}")
        if not PROJECTS_ROOT.exists():
            print(f"  (directory doesn't exist yet - run 'init-root' first)")
        return

    # Header
    print(f"\n  {'Slug':<25} {'Name':<30} {'Status':<14} {'Client':<12} {'Path'}")
    print(f"  {'─'*25} {'─'*30} {'─'*14} {'─'*12} {'─'*40}")

    for c in configs:
        status_icon = {
            ProjectStatus.DRAFT: "📝",
            ProjectStatus.IN_PROGRESS: "🔧",
            ProjectStatus.REVIEW: "👀",
            ProjectStatus.COMPLETE: "✅",
            ProjectStatus.ARCHIVED: "📦",
        }.get(c.status, "  ")

        print(
            f"  {c.slug:<25} {c.name:<30} "
            f"{status_icon} {c.status.value:<11} {c.client:<12} "
            f"{c.project_root}"
        )

    print(f"\n  {len(configs)} project(s) found")


def cmd_new(args):
    """Create a new project."""
    try:
        config = create_project(
            slug=args.slug,
            name=args.name or args.slug.replace("-", " ").title(),
            client=args.client or "",
            template=args.template or "default_brand",
            source_type=args.source_type or "iphone_mov",
            resolution=args.resolution or "1080x1920",
            fps=int(args.fps) if args.fps else 30,
            resolve_project_name=args.resolve_name or "",
            tags=args.tags.split(",") if args.tags else [],
            description=args.description or "",
        )
        print(f"\n  ✓ Created project: {config.name}")
        print(f"    Slug:     {config.slug}")
        print(f"    Path:     {config.project_root}")
        print(f"    Resolve:  {config.resolve.project_name}")
        print(f"    Template: {config.pipeline.brand_template}")
        print(f"\n  Next steps:")
        print(f"    1. Copy raw footage to: {config.raw_dir}")
        print(f"    2. Run pipeline: python3 manage_project.py run {config.slug}")

    except FileExistsError as e:
        print(f"  Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_status(args):
    """Show project status."""
    try:
        info = project_status(args.slug)
    except FileNotFoundError as e:
        print(f"  Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"\n  Project: {info['name']}")
    print(f"  Slug:    {info['slug']}")
    print(f"  Status:  {info['status']}")
    print(f"  Client:  {info['client'] or '(none)'}")
    print(f"  Path:    {info['project_root']}")
    print(f"")
    print(f"  Raw footage:     {info['raw_footage_count']} files")
    print(f"  Pipeline data:   {'yes' if info['pipeline_data_exists'] else 'no'}")
    print(f"  Steps completed: {info['steps_completed_count']}")
    if info['steps_completed']:
        for step in info['steps_completed']:
            print(f"    ✓ {step}")
    print(f"  Exports:         {len(info['exports'])} files")
    print(f"")
    print(f"  Resolve project: {info['resolve_project']}")
    print(f"  Resolve folder:  {info['resolve_folder'] or '(root)'}")


def cmd_run(args):
    """Run the pipeline for a project."""
    try:
        config = get_project(args.slug)
    except FileNotFoundError as e:
        print(f"  Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Build the run_pipeline.py command
    runner = PILOT_ROOT / "library" / "processes" / "edit_video" / "run_pipeline.py"
    cmd = [sys.executable, str(runner), "--project", str(config.project_root)]

    if args.from_step:
        cmd.extend(["--from", args.from_step])
    if args.step:
        cmd.extend(["--step", args.step])
    if args.dry_run:
        cmd.append("--dry-run")
    if args.auto:
        cmd.append("--auto")
    if args.review:
        cmd.append("--review")
    if args.resume:
        cmd.append("--resume")
    if getattr(args, "full_auto", None):
        cmd.extend(["--full-auto", args.full_auto])
    if getattr(args, "llm_timeout", None):
        cmd.extend(["--llm-timeout", str(args.llm_timeout)])

    print(f"  Running pipeline for: {config.name}")
    print(f"  Project: {config.project_root}")
    print(f"  Command: {' '.join(cmd)}")
    print(f"")

    import subprocess
    result = subprocess.run(cmd)
    sys.exit(result.returncode)


def cmd_archive(args):
    """Archive a completed project."""
    try:
        new_path = archive_project(args.slug)
        print(f"  ✓ Archived project '{args.slug}' to: {new_path}")
    except FileNotFoundError as e:
        print(f"  Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_info(args):
    """Show project configuration details."""
    try:
        config = get_project(args.slug)
    except FileNotFoundError as e:
        print(f"  Error: {e}", file=sys.stderr)
        sys.exit(1)

    from library.schemas.project_config import project_config_to_dict
    data = project_config_to_dict(config)
    print(json.dumps(data, indent=2))


def cmd_relink(args):
    """Relink offline media in Resolve after project migration."""
    try:
        from library.tools.resolve_relinker import relink_project
    except ImportError:
        print("  Error: Could not import resolve_relinker", file=sys.stderr)
        sys.exit(1)

    result = relink_project(args.slug, dry_run=args.scan)

    if not result.get("success"):
        print(f"  Error: {result.get('error', 'Unknown error')}", file=sys.stderr)
        sys.exit(1)

    offline = result.get("offline_count", 0)
    if offline == 0:
        print("  No offline clips found - all media is linked.")
        return

    if args.scan:
        fixable = result.get("fixable", [])
        unfixable = result.get("unfixable", [])
        print(f"\n  Offline clips: {offline}")
        print(f"  Fixable: {len(fixable)}")
        for c in fixable[:10]:
            print(f"    {c['name']}: {c['old']} -> {c['new']}")
        if len(fixable) > 10:
            print(f"    ... and {len(fixable) - 10} more")
        if unfixable:
            print(f"  Cannot fix: {len(unfixable)}")
            for c in unfixable:
                print(f"    {c['name']}: {c['old']}")
        print(f"\n  Run without --scan to execute relinking.")
    else:
        relinked = result.get("relinked", 0)
        failed = result.get("failed", 0)
        print(f"\n  Relinked: {relinked}")
        if failed:
            print(f"  Failed: {failed}")
        unfixable = result.get("unfixable_count", 0)
        if unfixable:
            print(f"  Could not locate: {unfixable}")


def cmd_dashboard(args):
    """Start the review dashboard for a project."""
    try:
        from library.dashboard.server import start_server
    except ImportError as e:
        print(f"  Error: Could not import dashboard server: {e}", file=sys.stderr)
        print(f"  Install dependencies: pip install fastapi uvicorn", file=sys.stderr)
        sys.exit(1)

    if args.slug:
        try:
            config = get_project(args.slug)
            project_dir = str(config.project_root)
            slug = args.slug
        except FileNotFoundError as e:
            print(f"  Error: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        # No slug provided - check if PROJECTS_ROOT has any projects
        configs = list_projects()
        if not configs:
            print("  No projects found. Create one first:", file=sys.stderr)
            print("    python3 manage_project.py new <slug> --name '<name>'", file=sys.stderr)
            sys.exit(1)
        # Use the most recently modified project
        config = configs[0]
        project_dir = str(config.project_root)
        slug = config.slug
        print(f"  No slug provided, using: {slug}")

    start_server(project_dir, slug=slug, port=args.port)


def main():
    parser = argparse.ArgumentParser(
        description="Manage video editing projects",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", help="Command to run")

    # init-root
    p_init = sub.add_parser("init-root", help="Initialize the projects root directory")
    p_init.set_defaults(func=cmd_init_root)

    # list
    p_list = sub.add_parser("list", help="List all projects")
    p_list.add_argument("--status", choices=[s.value for s in ProjectStatus])
    p_list.add_argument("--client", help="Filter by client")
    p_list.set_defaults(func=cmd_list)

    # new
    p_new = sub.add_parser("new", help="Create a new project")
    p_new.add_argument("slug", help="Project slug (filesystem-safe identifier)")
    p_new.add_argument("--name", help="Human-readable project name")
    p_new.add_argument("--client", help="Client grouping (creates client/slug/ structure)")
    p_new.add_argument("--template", help="Brand template name (default: default_brand)")
    p_new.add_argument("--source-type", help="Source media type (default: iphone_mov)")
    p_new.add_argument("--resolution", help="Resolution WxH (default: 1080x1920)")
    p_new.add_argument("--fps", help="Frame rate (default: 30)")
    p_new.add_argument("--resolve-name", help="DaVinci Resolve project name")
    p_new.add_argument("--tags", help="Comma-separated tags")
    p_new.add_argument("--description", help="Project description")
    p_new.set_defaults(func=cmd_new)

    # status
    p_status = sub.add_parser("status", help="Show project status")
    p_status.add_argument("slug", help="Project slug")
    p_status.set_defaults(func=cmd_status)

    # info
    p_info = sub.add_parser("info", help="Show project configuration as JSON")
    p_info.add_argument("slug", help="Project slug")
    p_info.set_defaults(func=cmd_info)

    # run
    p_run = sub.add_parser("run", help="Run the pipeline for a project")
    p_run.add_argument("slug", help="Project slug")
    p_run.add_argument("--from", "--start-from", dest="from_step", help="Start from this step")
    p_run.add_argument("--step", help="Run only this step")
    p_run.add_argument("--dry-run", action="store_true", help="Show plan without executing")
    p_run.add_argument("--auto", action="store_true", help="Auto-complete hybrid steps")
    p_run.add_argument("--review", action="store_true",
                       help="Enable review gates for dashboard inspection")
    p_run.add_argument("--resume", action="store_true",
                       help="Resume pipeline from pending gates")
    p_run.add_argument("--full-auto", choices=["agy", "api"],
                       help="Run full pipeline autonomously using specified LLM backend")
    p_run.add_argument("--llm-timeout", type=int, default=300,
                       help="Timeout for LLM response in agy backend")
    p_run.set_defaults(func=cmd_run)

    # dashboard
    p_dash = sub.add_parser("dashboard", help="Start the review dashboard for a project")
    p_dash.add_argument("slug", nargs="?", default="", help="Project slug (optional)")
    p_dash.add_argument("--port", type=int, default=8420, help="Server port (default: 8420)")
    p_dash.set_defaults(func=cmd_dashboard)

    # archive
    p_archive = sub.add_parser("archive", help="Archive a completed project")
    p_archive.add_argument("slug", help="Project slug")
    p_archive.set_defaults(func=cmd_archive)

    # relink
    p_relink = sub.add_parser("relink", help="Relink offline media in Resolve after migration")
    p_relink.add_argument("slug", nargs="?", default="", help="Project slug (optional)")
    p_relink.add_argument("--scan", action="store_true", help="Scan only, don't relink")
    p_relink.set_defaults(func=cmd_relink)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
