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
    python3 manage_project.py status "/abs/path/to/a/project"
    python3 manage_project.py run geo-podcast
    python3 manage_project.py run geo-podcast --from creative_direction
    python3 manage_project.py dashboard geo-podcast
    python3 manage_project.py dashboard "/abs/path/to/a/project"
    python3 manage_project.py archive geo-podcast
    python3 manage_project.py init-root

Only `run` needs the ML virtual environment; see ML_DEPENDENT_COMMANDS.
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Add repo root to path
REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))


# ─── The ML preflight, and the commands it is for ─────────────
#
# The heavy ML packages are imported by PIPELINE STEPS
# (step_1_04_temporal_index, and the analysis tools under
# library/tools/analysis/), never by this CLI.  Everything else this
# file does - listing projects, reading a project.yaml, regenerating a
# traceback, serving the review dashboard - reaches none of them.
#
# So the check belongs to the commands that need it, and `run` is the
# whole list: it launches library/processes/edit_video/run_pipeline.py
# with sys.executable, so the interpreter running THIS process is the
# one the steps will import from, and checking it here is a real check
# of the child rather than a guess about it.
#
# It used to run at import time, before argparse had seen the command.
# `dashboard` needs none of these packages - the server is fully
# constructible with whisperx absent - and the captain could not open
# the dashboard because of a missing transcription library.
ML_DEPENDENT_COMMANDS = ("run",)

# Every subcommand main() registers, so the message above can say which
# ones still work. main() asserts this against the parser it built, so
# adding a subcommand without listing it here fails loudly rather than
# leaving the advice quietly wrong.
ALL_COMMANDS = (
    "init-root", "list", "new", "status", "info", "trace", "organize",
    "run", "dashboard", "archive", "relink",
)

ML_REQUIRED_PACKAGES = ("mlx_vlm", "whisperx", "easyocr", "torch")


def _missing_ml_packages():
    """Return the ML packages this interpreter cannot import."""
    try:
        import torchaudio
        if not hasattr(torchaudio, 'set_audio_backend'):
            torchaudio.set_audio_backend = lambda x: None
        if not hasattr(torchaudio, 'get_audio_backend'):
            torchaudio.get_audio_backend = lambda: "soundfile"
    except ImportError:
        pass
    missing = []
    for pkg in ML_REQUIRED_PACKAGES:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    return missing


def _venv_advice(repo_root: Path) -> list[str]:
    """Say what to actually do, naming only paths that exist.

    The old message printed `source .venv/bin/activate` unconditionally.
    There is no .venv in a fresh checkout, so following the instruction
    produced a second and more confusing error than the first.  An
    activate script that is really on disk is named by its absolute
    path; when there is none, this says so and gives the two commands
    that make one.
    """
    activate = repo_root / ".venv" / "bin" / "activate"
    if activate.is_file():
        return [
            "This checkout has a virtual environment. Activate it and try again:",
            f"    source {activate}",
        ]
    return [
        f"This checkout has no virtual environment: {repo_root / '.venv'} does not exist.",
        "Create one and install the pipeline's dependencies:",
        f"    python3 -m venv {repo_root / '.venv'}",
        f"    source {repo_root / '.venv' / 'bin' / 'activate'}",
        f"    pip install -r {repo_root / 'requirements.txt'}",
    ]


def preflight_check(command: str, repo_root: Path = REPO_ROOT) -> None:
    """Exit with an explanation when `command` cannot reach its ML stack.

    A command outside ML_DEPENDENT_COMMANDS is not checked at all.
    """
    if command not in ML_DEPENDENT_COMMANDS:
        return
    missing = _missing_ml_packages()
    if not missing:
        return
    print(f"ERROR: '{command}' needs ML dependencies this interpreter "
          f"cannot import: {', '.join(missing)}")
    print(f"  Interpreter: {sys.executable}")
    print("")
    for line in _venv_advice(repo_root):
        print(line)
    print("")
    others = ", ".join(c for c in ALL_COMMANDS if c not in ML_DEPENDENT_COMMANDS)
    print(f"Only {', '.join(ML_DEPENDENT_COMMANDS)} needs them. These still "
          f"work from this interpreter:")
    print(f"    {others}")
    sys.exit(1)


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


def cmd_trace(args):
    """Regenerate the two documents that describe what a run did.

    Works on a run that already happened: timings and verdicts come from
    the ledgers in pipeline_data.json, and file attribution falls back to
    the layout's declaration - labelled as a declaration, never dressed
    up as an observation. See library/tools/run_traceback.py.
    """
    from library.tools.run_traceback import write_traceback

    try:
        config = get_project(args.slug)
        project_folder = str(config.project_root)
    except FileNotFoundError:
        project_folder = args.slug

    w = write_traceback(project_folder)
    print(f"\n  Wrote {w['traceback']}")
    print(f"  Wrote {w['artifact_index']}")
    print(f"\n  {w['steps']} steps, {w['artifacts']} artifacts")
    print(f"    observed: {w['observed']}  (a run was watching)")
    print(f"    declared: {w['declared']}  (the layout says which step owns the area)")
    print(f"    unknown:  {w['unknown']}  (neither - left as unknown)")


def cmd_organize(args):
    """Bring a project folder onto the layout - or undo one that was.

    Plans by default and changes nothing. Nothing is ever deleted; a file
    whose purpose cannot be established goes to pipeline_output/unsorted/
    with a stated reason. See library/tools/project_migration.py.
    """
    from library.tools.project_migration import (
        organize_project,
        render_manifest_markdown,
        revert_from_manifest,
    )

    if args.revert:
        undone = revert_from_manifest(args.revert, apply=args.apply)
        verb = "Moved back" if args.apply else "Would move back"
        for u in undone:
            print(f"  {verb}: {u['from']} -> {u['to']}")
        print(f"\n  {verb.lower()} {len(undone)} entr"
              f"{'y' if len(undone) == 1 else 'ies'}"
              f"{'' if args.apply else ' (pass --apply to perform it)'}")
        print("  Copies are not undone: undoing a copy means deleting, and "
              "this tool does not delete.")
        return

    try:
        config = get_project(args.slug)
        project_folder = str(config.project_root)
    except FileNotFoundError:
        project_folder = args.slug

    manifest = organize_project(project_folder, apply=args.apply)
    print(render_manifest_markdown(manifest))
    if args.apply:
        print(f"\nManifest: {manifest['manifest_path']}")
        print(f"Readable: {manifest['manifest_markdown_path']}")
    else:
        print("\nNothing was changed. Pass --apply to perform this.")


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
    for target in getattr(args, "rerun", None) or []:
        cmd.extend(["--rerun", target])
    if getattr(args, "full_auto", None):
        cmd.extend(["--full-auto", args.full_auto])
    if getattr(args, "llm_timeout", None):
        cmd.extend(["--llm-timeout", str(args.llm_timeout)])

    print(f"  Running pipeline for: {config.name}")
    print(f"  Project: {config.project_root}")
    print(f"  Command: {' '.join(cmd)}")
    print(f"")

    import subprocess
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PILOT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(cmd, env=env)
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
            slug = config.slug
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
    p_status.add_argument("slug", metavar="PROJECT", help="Project slug, or an absolute/relative path to the project directory (or its project.yaml) for projects that live outside PIPELINE_PROJECTS_ROOT")
    p_status.set_defaults(func=cmd_status)

    # info
    p_info = sub.add_parser("info", help="Show project configuration as JSON")
    p_info.add_argument("slug", metavar="PROJECT", help="Project slug, or an absolute/relative path to the project directory (or its project.yaml) for projects that live outside PIPELINE_PROJECTS_ROOT")
    p_info.set_defaults(func=cmd_info)

    # trace
    p_trace = sub.add_parser(
        "trace",
        help="Regenerate the run traceback and the artifact index for a project")
    p_trace.add_argument("slug", metavar="PROJECT", help="Project slug, or an absolute/relative path to the project directory (or its project.yaml) for projects that live outside PIPELINE_PROJECTS_ROOT")
    p_trace.set_defaults(func=cmd_trace)

    # organize
    p_org = sub.add_parser(
        "organize",
        help="Bring an existing project folder onto the standard layout")
    p_org.add_argument("slug", metavar="PROJECT", help="Project slug, or an absolute/relative path to the project directory (or its project.yaml) for projects that live outside PIPELINE_PROJECTS_ROOT")
    p_org.add_argument("--apply", action="store_true",
                       help="Perform the reorganisation (default: plan only)")
    p_org.add_argument("--revert", metavar="MANIFEST",
                       help="Undo a reorganisation by reading its manifest")
    p_org.set_defaults(func=cmd_organize)

    # run
    p_run = sub.add_parser("run", help="Run the pipeline for a project")
    p_run.add_argument("slug", metavar="PROJECT", help="Project slug, or an absolute/relative path to the project directory (or its project.yaml) for projects that live outside PIPELINE_PROJECTS_ROOT")
    p_run.add_argument("--from", "--start-from", dest="from_step", help="Start from this step")
    p_run.add_argument("--step", help="Run only this step")
    p_run.add_argument("--dry-run", action="store_true", help="Show plan without executing")
    p_run.add_argument("--auto", action="store_true", help="Auto-complete hybrid steps")
    p_run.add_argument("--review", action="store_true",
                       help="Enable review gates for dashboard inspection")
    p_run.add_argument(
        "--rerun", action="append", metavar="TARGET", default=[],
        help="Redo finished work. Repeatable. TARGET is a stage "
             "(preflight|edit), a step (temporal_index), or one clip of one "
             "step (temporal_index:clip_007). Preflight work is skipped once "
             "done, so this is how you ask for it again")
    p_run.add_argument("--resume", action="store_true",
                       help="Resume pipeline from pending gates")
    p_run.add_argument("--full-auto", choices=["agy", "api"],
                       help="Run full pipeline autonomously using specified LLM backend")
    p_run.add_argument("--llm-timeout", type=int, default=300,
                       help="Timeout for LLM response in agy backend")
    p_run.set_defaults(func=cmd_run)

    # dashboard
    p_dash = sub.add_parser("dashboard", help="Start the review dashboard for a project")
    p_dash.add_argument("slug", nargs="?", default="", metavar="PROJECT", help="Project slug, or an absolute/relative path to the project directory (or its project.yaml) for projects that live outside PIPELINE_PROJECTS_ROOT (optional)")
    p_dash.add_argument("--port", type=int, default=8420, help="Server port (default: 8420)")
    p_dash.set_defaults(func=cmd_dashboard)

    # archive
    p_archive = sub.add_parser("archive", help="Archive a completed project")
    p_archive.add_argument("slug", metavar="PROJECT", help="Project slug, or an absolute/relative path to the project directory (or its project.yaml) for projects that live outside PIPELINE_PROJECTS_ROOT")
    p_archive.set_defaults(func=cmd_archive)

    # relink
    p_relink = sub.add_parser("relink", help="Relink offline media in Resolve after migration")
    p_relink.add_argument("slug", nargs="?", default="", metavar="PROJECT", help="Project slug (optional). Unlike run/status/info/dashboard, relink resolves the project by scanning PIPELINE_PROJECTS_ROOT, so a path is not accepted here")
    p_relink.add_argument("--scan", action="store_true", help="Scan only, don't relink")
    p_relink.set_defaults(func=cmd_relink)

    registered = tuple(sub.choices)
    if registered != ALL_COMMANDS:
        raise AssertionError(
            f"ALL_COMMANDS is out of step with the parser: "
            f"registered={registered} listed={ALL_COMMANDS}")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    # After argparse, so the check is asked only of the command that
    # needs it. See ML_DEPENDENT_COMMANDS.
    preflight_check(args.command)

    args.func(args)


if __name__ == "__main__":
    main()
