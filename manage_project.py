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
    python3 manage_project.py list --client personal --status in_progress
    python3 manage_project.py new my-vlog --name "Beltline Vlog"
    python3 manage_project.py new my-show --name "My Show" --client personal
    python3 manage_project.py status my-show
    python3 manage_project.py status "/abs/path/to/a/project"
    python3 manage_project.py run my-show
    python3 manage_project.py run my-show --from creative_direction
    python3 manage_project.py dashboard my-show
    python3 manage_project.py dashboard "/abs/path/to/a/project"
    python3 manage_project.py archive my-show
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
    "init-root", "list", "new", "propose-reels", "build-reels", "status",
    "info", "trace", "organize", "resolve-organize", "resolve-prune",
    "resolve-mark-master",
    "check", "run", "dashboard", "archive", "notes", "relink",
)

ML_REQUIRED_PACKAGES = ("mlx_vlm", "whisperx", "easyocr", "torch")

# Import name -> the distribution name on PyPI, for the ones that differ.
# Needed because the version check below asks `importlib.metadata` for an
# installed version, and it answers by DISTRIBUTION name.
ML_DISTRIBUTION_NAMES = {
    "mlx_vlm": "mlx-vlm",
}


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


# ─── Importable is not the same as USABLE ─────────────────────────
#
# An import check answers "is a whisperx here", never "is it the
# whisperx this pipeline declares".  On 2026-09-05 every ML environment
# on the build machine carried whisperx 3.2.0 against a declared
# `whisperx>=3.8,<4`, because they were built on Python 3.14 - which
# requirements.txt forbids in its own header, in capitals, for exactly
# this reason.  3.2.0 imports perfectly and then raises
#
#     TypeError: TranscriptionOptions.__init__() missing 2 required
#     positional arguments: 'multilingual' and 'hotwords'
#
# on every transcribe call.  step_1_04 catches that per clip and carries
# on, so the measured outcome on project 001 was 17 clips reporting
# "0 regions, 0.0s speech, 0 words", no spine, no subtitles - the entire
# edit missing, reported as success.  requirements.txt has documented
# that whole chain since 2026-08-17 and nothing ever checked it.
#
# The declared version is read from requirements.txt rather than
# restated here.  A second copy of the pin in this file is the local
# answer that CI never sees, and it drifts from the manifest the moment
# either moves - which is the same defect one level up.

def _declared_specifiers(repo_root: Path) -> dict:
    """{distribution: specifier} for the ML packages requirements.txt pins.

    A package the manifest does not constrain simply has no entry: there
    is nothing to be non-compliant with, and inventing a bound here would
    be this file having an opinion the manifest does not.
    """
    from packaging.requirements import Requirement

    wanted = {ML_DISTRIBUTION_NAMES.get(p, p).lower()
              for p in ML_REQUIRED_PACKAGES}
    found = {}
    path = repo_root / "requirements.txt"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return found
    for raw in lines:
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        try:
            req = Requirement(line)
        except Exception:
            continue
        if req.name.lower() in wanted and str(req.specifier):
            found[req.name.lower()] = req.specifier
    return found


def _noncompliant_ml_packages(repo_root: Path = None) -> list:
    """ML packages whose INSTALLED version the manifest does not allow.

    Each entry is `(import_name, installed, specifier)`.  A package whose
    version cannot be determined is REPORTED as undetermined rather than
    passed: an environment that cannot say what it has is not an
    environment that has been shown to comply.
    """
    from importlib.metadata import PackageNotFoundError, version

    repo_root = REPO_ROOT if repo_root is None else repo_root
    try:
        declared = _declared_specifiers(repo_root)
    except ImportError:
        # `packaging` is a transitive dependency of the ML stack itself,
        # so this only happens in an environment that has already failed
        # the import check above.  Say nothing rather than guess.
        return []

    problems = []
    for pkg in ML_REQUIRED_PACKAGES:
        dist = ML_DISTRIBUTION_NAMES.get(pkg, pkg).lower()
        specifier = declared.get(dist)
        if specifier is None:
            continue
        try:
            installed = version(dist)
        except PackageNotFoundError:
            problems.append((pkg, "unknown", specifier))
            continue
        if not specifier.contains(installed, prereleases=True):
            problems.append((pkg, installed, specifier))
    return problems


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
    # 3.12 EXPLICITLY, not bare `python3`.  This advice used to say
    # `python3 -m venv`, and on a machine whose `python3` is 3.14 that
    # instruction rebuilds the exact environment requirements.txt forbids
    # in its header - the resolver finds no ctranslate2 wheel, settles on
    # a whisperx below the declared floor, and transcription is broken
    # again in a way that imports cleanly.  Following the advice has to
    # produce a working environment or it is not advice.
    return [
        f"This checkout has no virtual environment: {repo_root / '.venv'} does not exist.",
        "Create one on PYTHON 3.12 - requirements.txt explains why no other "
        "version works - and install the pipeline's dependencies:",
        f"    python3.12 -m venv {repo_root / '.venv'}",
        f"    source {repo_root / '.venv' / 'bin' / 'activate'}",
        f"    pip install -r {repo_root / 'requirements.txt'}",
        "",
        "With uv, which resolves this stack faster and can fetch 3.12 itself:",
        f"    uv venv --python 3.12 {repo_root / '.venv'}",
        f"    uv pip install --python {repo_root / '.venv' / 'bin' / 'python3'} "
        f"-r {repo_root / 'requirements.txt'}",
    ]


def preflight_check(command: str, repo_root: Path = REPO_ROOT) -> None:
    """Exit with an explanation when `command` cannot reach its ML stack.

    A command outside ML_DEPENDENT_COMMANDS is not checked at all.
    """
    if command not in ML_DEPENDENT_COMMANDS:
        return
    missing = _missing_ml_packages()
    if missing:
        print(f"ERROR: '{command}' needs ML dependencies this interpreter "
              f"cannot import: {', '.join(missing)}")
        _print_ml_advice(command, repo_root)
        sys.exit(1)

    # Present, but is it the one the manifest declares?  See the note
    # above `_declared_specifiers`.
    wrong = _noncompliant_ml_packages(repo_root)
    if wrong:
        print(f"ERROR: '{command}' has ML dependencies this interpreter can "
              f"import but requirements.txt does not allow:")
        for pkg, installed, specifier in wrong:
            print(f"  {pkg}: installed {installed}, requires {specifier}")
        print("")
        print("  A version outside the declared range imports fine and then "
              "fails inside the step.")
        print("  whisperx below 3.8 raises TypeError on every transcribe "
              "call, step 1.04 catches it per clip, and the run reports "
              "success with no transcript, no spine and no subtitles.")
        print("  requirements.txt's header has the full chain, and "
              "docs/ML_ENVIRONMENT.md is how to rebuild this environment.")
        _print_ml_advice(command, repo_root)
        sys.exit(1)


def _print_ml_advice(command: str, repo_root: Path) -> None:
    """The shared tail of both ML preflight refusals."""
    print(f"  Interpreter: {sys.executable}")
    print("")
    for line in _venv_advice(repo_root):
        print(line)
    print("")
    others = ", ".join(c for c in ALL_COMMANDS if c not in ML_DEPENDENT_COMMANDS)
    print(f"Only {', '.join(ML_DEPENDENT_COMMANDS)} needs them. These still "
          f"work from this interpreter:")
    print(f"    {others}")


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
            "  acme/                  # client group\n"
            "    my-podcast/\n"
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


def cmd_notes(args):
    """Show which of the captain's timeline notes went to which step.

    Reads the pull files `marker_feedback pull` wrote and routes each
    note to the step that owns the decision it is about. Nothing here
    touches Resolve; nothing is written unless --write is passed. See
    library/tools/marker_routing.py.
    """
    from library.tools import marker_routing

    try:
        config = get_project(args.slug)
        project_folder = str(config.project_root)
    except FileNotFoundError:
        project_folder = args.slug

    argv = ["write" if args.write else "report", "--project", project_folder]
    sys.exit(marker_routing.main(argv))


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


def cmd_resolve_organize(args):
    """File a Resolve project's media pool the way its evidence says.

    Plans by default and changes nothing, the same bargain `organize`
    strikes with the project FOLDER. Nothing is ever deleted: a reel the
    live plan no longer names is moved and relabelled, never removed.
    See library/tools/resolve_organization.py.
    """
    from library.tools.execution.organise_media_pool import (
        journals,
        open_project,
        organise_project,
        render_unplaced,
        revert,
        survey_project,
    )
    from library.tools.execution import retire_empty_bins as retire

    project_folder = _reel_project_folder(args.project)
    project, master = open_project(project_folder)

    if args.revert is not None:
        if not args.revert:
            # No guessing which apply to undo: an operator three days
            # later is shown what there is and picks one.
            found = journals(project_folder)
            found_retire = retire.journals(project_folder)
            if not found and not found_retire:
                print("  No journal in this project - nothing has been "
                      "filed here.")
                return
            print("  Pass one of these to --revert (newest first):")
            for entry in found:
                state = (f"already reverted at {entry['reverted_at']}"
                         if entry["reverted_at"] else "not yet reverted")
                print(f"    {entry['path']}\n"
                      f"      {entry['moves']} move(s), "
                      f"{entry['stamps']} stamp(s), applied "
                      f"{entry['organised_at']} - {state}")
            for entry in found_retire:
                state = (f"already reverted at {entry['reverted_at']}"
                         if entry["reverted_at"] else "not yet reverted")
                print(f"    {entry['path']}\n"
                      f"      {entry['retired']} retired bin(s), applied "
                      f"{entry['retired_at']} - {state}")
            return
        if "resolve_retirements" in args.revert:
            undone = retire.revert(project, args.revert)
            print(f"  Re-created {len(undone['recreated'])} retired bin(s):")
            for bin_path in undone["recreated"]:
                print(f"    {bin_path}")
            return
        undone = revert(project, args.revert)
        print(f"  Moved back: {len(undone['moved_back'])} item(s)")
        for name in undone["not_found"]:
            print(f"  NOT FOUND, left alone: {name}")
        if undone["bins_left_behind"]:
            print("  Bins this cannot un-create, because undoing a create "
                  "means deleting and this tool does not delete:")
            for bin_path in undone["bins_left_behind"]:
                print(f"    {bin_path}")
        return

    if args.check:
        survey = survey_project(project, project_folder, master)
        found = survey["findings"]
        for finding in found:
            print(f"  [{finding['kind']}] {finding['detail']}")
        print(f"\n{len(found)} finding(s) on "
              f"{project.GetName()!r}.")
        print()
        print(survey["census"])
        # Reported, never a finding: see check_project's docstring.
        print(render_unplaced(survey["unplaced"]))
        sys.exit(1 if found else 0)

    result = organise_project(project, project_folder, master,
                              apply=args.apply)
    print(render_plan_from_dict(result["plan"]))
    for duplicate in result["duplicate_bins"]:
        print(f"  DUPLICATE BIN: {duplicate} - Resolve allows two bins of "
              f"one name and AddSubFolder makes one on every call")
    print()
    print(result["census"])
    if result["applied"]:
        journal = result["journal"]
        print(f"\n  Moved {len(journal['moves'])} item(s), stamped "
              f"{len(journal['stamps'])} reel timeline(s).")
        print(f"  Journal: {journal['journal_path']}")
        print(f"  Undo it with: --revert {journal['journal_path']}")
        retired = result.get("retirement", {})
        if retired.get("retired"):
            print(f"  Retired {len(retired['retired'])} empty legacy "
                  f"bin(s):")
            for bin_path in retired["retired"]:
                print(f"    {bin_path}")
            print(f"  Retirement journal: {retired['journal_path']}")
            print(f"  Undo it with: --revert {retired['journal_path']}")
    else:
        if result["retirements"]:
            print(f"\n{len(result['retirements'])} empty legacy bin(s) "
                  f"would retire on --apply.")
        print("\nNothing was changed. Pass --apply to perform this.")
    print()
    print(render_unplaced(result["unplaced"]))


def cmd_resolve_prune(args):
    """Remove the pool items no timeline plays, and delete their files.

    IRREVERSIBLE, so it is staged: `--apply` removes the pool ITEMS and
    stops, `--delete-files` is a second, separate consent for the files.
    The manifest naming every path is written BEFORE either happens.
    See library/tools/orphan_removal.py.
    """
    from library.tools.execution.organise_media_pool import open_project
    from library.tools.execution.prune_orphans import (
        journal_path_for,
        manifest_path_for,
        remove_pool_items,
        survey,
        save_project,
        timeline_digests,
        write_manifest,
    )
    from library.tools.orphan_removal import render_summary

    project_folder = _reel_project_folder(args.project)
    project, _master = open_project(project_folder)

    if args.delete_files and not args.apply:
        _delete_from_journal(project, project_folder, args.delete_files)
        return

    result = survey(project, project_folder)
    manifest = write_manifest(result, project, project_folder,
                              manifest_path_for(project_folder))
    print(render_summary(result["counts"]))
    print(f"\n  Manifest (every path): {manifest}")

    if not args.apply:
        print("\nNothing was changed. Pass --apply to remove the pool "
              "items; files are only deleted with --delete-files as well.")
        return

    # Saved BEFORE the "before" snapshot, so the comparison isolates
    # THIS removal: anything already pending in the open project would
    # otherwise land in the "after" save and read as damage done here.
    # SaveProject is on the project MANAGER; `project.SaveProject` is
    # None, and calling it raises TypeError rather than saving.
    save_project()
    before = timeline_digests(result["database_path"])
    journal = remove_pool_items(project, result,
                                journal_path_for(project_folder))
    print(f"\n  Removed {len(journal['removed'])} pool item(s). "
          f"No file on disk was touched.")
    print(f"  Journal: {journal['journal_path']}")

    save_project()
    after = timeline_digests(result["database_path"])
    moved = sorted(name for name in set(before) | set(after)
                   if before.get(name) != after.get(name))
    print(f"  Timelines: {len(before)} before, {len(after)} after; "
          f"{len(moved)} digest(s) moved.")
    if moved:
        for name in moved:
            print(f"    CHANGED: {name}")
        print("  Files were NOT deleted. Investigate before going further.")
        sys.exit(1)

    if not args.delete_files:
        print(f"\n  Files were NOT deleted. Now that the pool items are "
              f"gone and every timeline still hashes the same, delete "
              f"them with:\n    resolve-prune {args.project} "
              f"--delete-files {journal['journal_path']}")
        return

    _delete_from_journal(project, project_folder, journal["journal_path"])


def _delete_from_journal(project, project_folder: str, journal_path: str):
    """Delete the files a completed item-removal authorised.

    Driven by the JOURNAL rather than a fresh survey: once the pool
    items are gone a survey finds no orphans and would delete nothing
    while reporting success.
    """
    from library.tools.execution.prune_orphans import (
        current_referenced_paths,
        delete_files,
        files_from_journal,
    )

    paths = files_from_journal(journal_path)
    referenced = current_referenced_paths(project, project_folder)
    print(f"  {len(paths)} file(s) authorised by {journal_path}")
    print(f"  {len(referenced)} path(s) some timeline plays right now - "
          f"each deletion is proven against these, one file at a time.")
    record = delete_files(paths, referenced, journal_path)
    gib = record["bytes_freed"] / (1024 ** 3)
    print(f"\n  Deleted {len(record['deleted'])} file(s), freeing "
          f"{gib:.2f} GiB.")
    if record["vanished_before_deletion"]:
        print(f"  {len(record['vanished_before_deletion'])} file(s) had "
              f"already gone between the plan and the deletion.")


def cmd_resolve_mark_master(args):
    """Mark the master with where each reel was taken from.

    Markers only, and reversible: `--clear` removes exactly the ones
    this wrote. The master is never moved, recoloured, retimed or
    re-rendered (the captain's ruling of 2026-09-07).
    See library/tools/master_markers.py.
    """
    from library.tools.execution.mark_master import (
        apply_markers,
        clear_markers,
        journal_path_for,
        plan_markers,
    )
    from library.tools.execution.organise_media_pool import open_project
    from library.tools.execution.prune_orphans import save_project

    project_folder = _reel_project_folder(args.project)
    project, master_name = open_project(project_folder)

    plan = plan_markers(project, project_folder, master_name)
    if args.clear:
        removed = clear_markers(plan["master"])
        save_project()
        print(f"  Removed {len(removed)} marker(s) this pipeline wrote "
              f"from {master_name!r}.")
        print(f"  {len(plan['existing']) - len(removed)} marker(s) that "
              f"were not ours are untouched.")
        return

    frames = plan["master_frames"]
    covered = plan["covered_frames"]
    print(f"Master: {master_name!r}, {frames} frames.")
    print(f"  {len(plan['reels'])} reel(s) located on it by footage "
          f"overlap; {len(plan['unlocatable'])} could not be located.")
    for entry in plan["unlocatable"]:
        print(f"    NOT MARKED: {entry['reel']} - {entry['why']}")
    print(f"  {len(plan['markers'])} disjoint region(s) of the episode are "
          f"used, covering {covered} of {frames} frames "
          f"({100.0 * covered / frames:.1f}%).")
    by_colour = {}
    for marker in plan["markers"]:
        by_colour[marker.colour] = by_colour.get(marker.colour, 0) + 1
    print("  Regions by recorded state: " + ", ".join(
        f"{k}={v}" for k, v in sorted(by_colour.items())))
    print(f"  {len(plan['existing'])} marker(s) already on the master.")

    if not args.apply:
        print("\nNothing was changed. Pass --apply to write them.")
        return

    journal = apply_markers(plan, journal_path_for(project_folder))
    save_project()
    print(f"\n  Wrote {len(journal['written'])} marker(s); removed "
          f"{len(journal['removed_first'])} of this pipeline's own first.")
    print(f"  Journal: {journal['journal_path']}")
    print(f"  Remove them with: --clear")


def render_plan_from_dict(plan: dict) -> str:
    """The plan as `organise_project` returns it, rendered for reading."""
    lines = [f"Media pool: {plan['root_bin']}", ""]
    counts = {}
    for move in plan["moves"]:
        counts[move["to"]] = counts.get(move["to"], 0) + 1
    lines.append(f"{len(plan['folders'])} bin(s) in the layout:")
    for folder in sorted(plan["folders"]):
        depth = folder.count("/")
        leaf = folder.rsplit("/", 1)[-1]
        lines.append(f"  {'  ' * depth}{leaf}"
                     f"    ({counts.get(folder, 0)} item(s) moving in)")
    lines.append("")
    lines.append(f"{len(plan['moves'])} item(s) would move.")
    for entry in plan["left_alone"]:
        lines.append(f"  left alone: {entry['name']} - {entry['why']}")
    by_state = {}
    for stamp in plan["stamps"]:
        by_state[stamp["state"]] = by_state.get(stamp["state"], 0) + 1
    if by_state:
        lines.append("")
        lines.append("Reel timelines by state: " + ", ".join(
            f"{k}={v}" for k, v in sorted(by_state.items())))
    return "\n".join(lines)


def cmd_check(args):
    """Run the readiness check for a project."""
    try:
        config = get_project(args.slug)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    project_dir = str(config._project_root)
    print(f"Checking project: {config.name} ({project_dir})")

    # 1. Brand template resolves
    print("Checking brand template...")
    from library.tools.brand_registry import resolve_template_reference
    try:
        template = resolve_template_reference(config.pipeline.brand_template)
    except Exception as e:
        print(f"Refusal: Brand template failed to resolve: {e}", file=sys.stderr)
        sys.exit(1)

    # 2. creative_brief path resolves IF declared
    print("Checking creative_brief...")
    if config.pipeline.creative_brief:
        cb_path = config._project_root / config.pipeline.creative_brief
        if not cb_path.exists():
            print(f"Refusal: Declared creative_brief path does not exist: {cb_path}", file=sys.stderr)
            sys.exit(1)

    # 3. Environment variables exist
    print("Checking environment paths...")
    from library.tools.paths import SFX_LIBRARY, MUSIC_LIBRARY, PROJECTS_ROOT
    if not SFX_LIBRARY.exists():
        print(f"Refusal: PIPELINE_SFX_LIBRARY does not exist: {SFX_LIBRARY}", file=sys.stderr)
        sys.exit(1)
    if not MUSIC_LIBRARY.exists():
        print(f"Refusal: PIPELINE_MUSIC_LIBRARY does not exist: {MUSIC_LIBRARY}", file=sys.stderr)
        sys.exit(1)
    if not PROJECTS_ROOT.exists():
        print(f"Refusal: PIPELINE_PROJECTS_ROOT does not exist: {PROJECTS_ROOT}", file=sys.stderr)
        sys.exit(1)

    # 4. ffprobe reads every footage file
    print("Checking footage files with ffprobe...")
    from library.tools.footage_identity import enumerate_footage
    raw_footage_files, _ = enumerate_footage(project_dir)

    if not raw_footage_files:
        print(f"Refusal: No footage files found in project", file=sys.stderr)
        sys.exit(1)

    from library.steps.step_1_02_catalog_footage.step import extract_metadata

    fps_counts = {}
    res_counts = {}

    for file_info in raw_footage_files:
        filepath = file_info["path"]
        # print(f"  Probing {file_info['filename']}...")
        metadata = extract_metadata(filepath)
        if metadata is None or "error" in metadata:
            err = metadata.get("error", "unknown error") if metadata else "unknown error"
            print(f"Refusal: ffprobe failed to read {filepath}: {err}", file=sys.stderr)
            sys.exit(1)

        for field in ["duration_seconds", "width", "height", "frame_rate"]:
            if metadata.get(field) is None:
                print(f"Refusal: Missing required field '{field}' in file '{file_info['filename']}'", file=sys.stderr)
                sys.exit(1)

        fps = metadata.get("frame_rate")
        w = metadata.get("width")
        h = metadata.get("height")

        fps_counts[fps] = fps_counts.get(fps, 0) + 1
        res_counts[(w, h)] = res_counts.get((w, h), 0) + 1

    # 5. Asset existence validation
    print("Checking project assets (bookends, fonts, timed text)...")
    from library.tools.bookends import declared_bookends, resolve_bookend
    from library.tools.project_asset import ProjectAssetNotFoundError, resolve_project_asset
    from library.tools.render_fonts import PROJECT_FONT_SOURCE_DIR
    import dataclasses

    try:
        # Bookends
        bookends = declared_bookends(dataclasses.asdict(template.content))
        for b in bookends:
            resolve_bookend(b, str(config.project_root))

        # Timed text overlay font
        overlay = template.effect.timed_text_overlay
        if overlay and overlay.get("font_file"):
            font_file = overlay.get("font_file")
            name = str(font_file).strip().lstrip("/")
            if not os.path.isabs(font_file):
                candidate = os.path.join(PROJECT_FONT_SOURCE_DIR, os.path.basename(name))
                resolve_project_asset(candidate, str(config.project_root))
            else:
                if not os.path.exists(font_file):
                    raise ProjectAssetNotFoundError(f"project asset '{font_file}' not found (checked absolute path)")
    except ProjectAssetNotFoundError as e:
        print(f"Refusal: {e}", file=sys.stderr)
        sys.exit(1)

    if len(fps_counts) > 1:
        print(f"REPORT: Mixed frame rates detected: {fps_counts}", file=sys.stderr)
    if len(res_counts) > 1:
        print(f"REPORT: Mixed resolutions detected: {res_counts}", file=sys.stderr)

    # 6. Standalone scripts beside the footage are VISIBLE, never refused.
    # Record 2026-09-06: three scripts in the captain's project folder
    # re-implemented pipeline steps outside every repository-side guard,
    # which all look at the REPOSITORY. A REPORT names them so a run
    # beside an unseen parallel implementation says so; a refusal would
    # dictate how the captain works in their own directories, and that
    # call is the captain's. See library/tools/project_scripts.py.
    print("Checking project folder for standalone scripts...")
    from library.tools.project_scripts import find_standalone_scripts
    for script in find_standalone_scripts(project_dir):
        print(f"REPORT: Standalone Python script in project folder: "
              f"{os.path.relpath(script, project_dir)}", file=sys.stderr)

    print("\nReadiness check PASS.")


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
    # The scope. See library/tools/run_scope.py.
    if getattr(args, "target", None):
        cmd.extend(["--target", args.target])
    for step_id in getattr(args, "only", None) or []:
        cmd.extend(["--only", step_id])
    for step_id in getattr(args, "skip", None) or []:
        cmd.extend(["--skip", step_id])
    for step_id in getattr(args, "with_steps", None) or []:
        cmd.extend(["--with", step_id])
    for requirement in getattr(args, "overrides", None) or []:
        cmd.extend(["--override", requirement])
    # The run configuration. See library/tools/run_profile.py and
    # library/tools/breakpoints.py.
    if getattr(args, "profile", None):
        cmd.extend(["--profile", args.profile])
    for step_id in getattr(args, "break_at", None) or []:
        cmd.extend(["--break", step_id])
    for step_id in getattr(args, "no_break_at", None) or []:
        cmd.extend(["--no-break", step_id])
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
    # check=False: the return code is inspected on the next line.
    result = subprocess.run(cmd, env=env, check=False)
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


def cmd_propose_reels(args):
    """Publish step 3.4's chosen moments as the captain's review file."""
    from library.tools.reel_proposal import write_from_step_output
    try:
        config = get_project(args.project)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    path = write_from_step_output(str(config._project_root), force=args.force)
    print(f"Wrote {path}")


def cmd_build_reels(args):
    """Run the `reels` PROCESS, node by node, in its own DAG's order.

    This used to call `reel_build.rebuild_reels_in_project` directly, and
    that was the only way reels could be made: you had to know which
    subcommand to run, and nothing checked a single prerequisite before
    connecting to Resolve and deleting the existing reel timelines.  The
    captain's stop condition was that reels be created *"using the
    pipeline and not any standalone scripts"*, and a command that reaches
    past the pipeline into a tool is that gap however thin it is.

    So it is now a caller of `library/processes/reels`: the node ORDER
    comes off that process's own dag.json rather than being spelled here,
    and each node runs through the operation registry, which checks the
    node's DERIVED requirements first and REFUSES naming what is missing.
    No build path lives here - the operation resolves to the step's own
    body, and the step's body calls `reel_build`. One implementation.
    """
    import json as _json

    from library.tools import operations, processes
    from library.tools.project_layout import ProjectLayout

    project_folder = _reel_project_folder(args.project)

    for node_id in processes.execution_order(processes.REELS):
        for op in operations.by_node(node_id):
            result = op.execute(
                project_folder,
                skip_captions=args.skip_captions,
                only_reels=args.only_reel or None,
                timeline_name_suffix=args.name_suffix)
            if result.refused:
                print(f"REFUSED: {op.name}", file=sys.stderr)
                print(result.error, file=sys.stderr)
                sys.exit(1)

            # RECORD the node's output the way a run records one, so the
            # edge to the next node can carry it and so the build is
            # readable afterwards by everything that reads
            # `step_outputs` - the traceback, the dashboard, `status`.
            # `save_pipeline_state` is the runner's own writer, called
            # rather than copied, because it owns the backup rule
            # (AGENTS.md 8).
            path = ProjectLayout(project_folder).pipeline_data_path
            state = (_json.loads(Path(path).read_text(encoding="utf-8"))
                     if Path(path).is_file() else {})
            state["project_folder"] = project_folder
            state.setdefault("step_outputs", {})[node_id] = result.payload
            _edit_video_runner().save_pipeline_state(project_folder, state)
            print(f"{op.name}: {result.status}", file=sys.stderr)
            _report_reel_verification(result.payload)


def _report_reel_verification(payload) -> None:
    """Say WHICH plan and WHICH timelines the verify node graded.

    `verify_reels` returns a terminal record - `reel_verification` - and
    for a while nothing read it: the loop above printed the operation's
    status, so a build reported "nothing raised" and never said what had
    been looked at.  A gate whose account of itself is unread reads as
    coverage (AGENTS.md 10.4), and the record exists precisely so a run
    can name the plan it graded against.

    The RAISE inside `reel_build.verify_built_reels` is still the gate.
    This does not re-judge it; it reports what passed.
    """
    if not isinstance(payload, dict):
        return
    record = payload.get("reel_verification")
    if not isinstance(record, dict):
        return
    timelines = list(record.get("timelines_verified") or ())
    print(
        f"  verified {len(timelines)} reel timeline(s) in Resolve project "
        f"{record.get('resolve_project_name') or '?'!r} against "
        f"{record.get('plan_path') or '(no plan named)'}",
        file=sys.stderr)
    for name in timelines:
        print(f"    - {name}", file=sys.stderr)
    if not timelines:
        print("    (the record names no timeline - the plan graded none)",
              file=sys.stderr)


def _reel_project_folder(project: str) -> str:
    """A slug or a path, resolved to the project's own directory.

    `build-reels` has always taken either - `rebuild_reels_in_project`
    did this resolution itself - and an operation takes a FOLDER, because
    that is where its inputs and its requirement checks read from.
    """
    if os.path.isabs(project) and os.path.isdir(project):
        return project
    from library.tools.project_registry import get_project
    try:
        return str(get_project(project).project_root)
    except FileNotFoundError as unknown:
        print(f"Error: {unknown}", file=sys.stderr)
        sys.exit(1)


def _edit_video_runner():
    """The runner module, imported the way `operations.Operation` does.

    There is no runner for the `reels` process and there must not be a
    second one: what `cmd_build_reels` borrows from this module is state
    persistence, which is not process-specific.
    """
    process_dir = PILOT_ROOT / "library" / "processes" / "edit_video"
    for entry in (str(PILOT_ROOT), str(PILOT_ROOT / "library"),
                  str(process_dir)):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    import run_pipeline
    return run_pipeline


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
    propose_reels_parser = sub.add_parser(
        "propose-reels",
        help="Publish step 3.4's chosen moments as the reel review file")
    propose_reels_parser.add_argument(
        "project", help="The project to publish reel proposals for")
    propose_reels_parser.add_argument(
        "--force", action="store_true",
        help="Overwrite a proposal file the captain has already ruled on")
    propose_reels_parser.set_defaults(func=cmd_propose_reels)

    build_reels_parser = sub.add_parser(
        "build-reels", help="Rebuild approved reels in Resolve")
    build_reels_parser.add_argument(
        "project", help="The project to rebuild reels for")
    build_reels_parser.add_argument(
        "--skip-captions", action="store_true", help="Skip rendering subtitles (saves CPU)")
    build_reels_parser.add_argument(
        "--only-reel", type=int, action="append", default=[], metavar="N",
        help="Build only this reel number; repeatable. Default: every "
             "approved moment. The build deletes only the timelines it is "
             "about to place, so this touches one timeline.")
    build_reels_parser.add_argument(
        "--name-suffix", default="", metavar="TEXT",
        help="Append this to the Resolve timeline name each reel is built "
             "into, and to its caption filenames. Default: the plan's own "
             "name, which REPLACES the timeline already called that.")
    build_reels_parser.set_defaults(func=cmd_build_reels)

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

    # resolve-organize
    p_rorg = sub.add_parser(
        "resolve-organize",
        help="File a Resolve project's media pool: reels by plan state, "
             "assets under the reel that uses them")
    p_rorg.add_argument("project", help="Project slug, or an absolute path "
                                        "to the project directory")
    p_rorg.add_argument("--apply", action="store_true",
                        help="Perform the filing (default: plan only)")
    p_rorg.add_argument("--revert", metavar="JOURNAL", nargs="?", const="",
                        help="Undo a filing by reading its journal; with no "
                             "path, list the journals in this project")
    p_rorg.add_argument("--check", action="store_true",
                        help="Report what is filed wrong; exit 1 if any")
    p_rorg.set_defaults(func=cmd_resolve_organize)

    # resolve-prune
    p_prune = sub.add_parser(
        "resolve-prune",
        help="Remove media-pool items no timeline plays, and delete their "
             "files. IRREVERSIBLE; plans by default")
    p_prune.add_argument("project", help="Project slug, or an absolute path "
                                         "to the project directory")
    p_prune.add_argument("--apply", action="store_true",
                         help="Remove the pool items (default: plan only). "
                              "Files are NOT deleted without --delete-files")
    p_prune.add_argument("--delete-files", metavar="JOURNAL", nargs="?",
                         const="", default=None,
                         help="Delete the files a completed removal "
                              "authorised, named by its journal. Given "
                              "alone this is the second, separate consent; "
                              "given with --apply it follows it directly")
    p_prune.set_defaults(func=cmd_resolve_prune)

    # resolve-mark-master
    p_mark = sub.add_parser(
        "resolve-mark-master",
        help="Mark the master timeline with where each reel was taken "
             "from. Markers only, and reversible")
    p_mark.add_argument("project", help="Project slug, or an absolute path "
                                        "to the project directory")
    p_mark.add_argument("--apply", action="store_true",
                        help="Write the markers (default: plan only)")
    p_mark.add_argument("--clear", action="store_true",
                        help="Remove the markers this pipeline wrote, and "
                             "only those")
    p_mark.set_defaults(func=cmd_resolve_mark_master)

    # check
    p_check = sub.add_parser("check", help="Run the readiness check for a project")
    p_check.add_argument("slug", metavar="PROJECT", help="Project slug, or an absolute/relative path to the project directory (or its project.yaml) for projects that live outside PIPELINE_PROJECTS_ROOT")
    p_check.set_defaults(func=cmd_check)

    # run
    p_run = sub.add_parser("run", help="Run the pipeline for a project")
    p_run.add_argument("slug", metavar="PROJECT", help="Project slug, or an absolute/relative path to the project directory (or its project.yaml) for projects that live outside PIPELINE_PROJECTS_ROOT")
    p_run.add_argument("--from", "--start-from", dest="from_step", help="Start from this step")
    p_run.add_argument("--step", help="Run only this step")
    p_run.add_argument("--dry-run", action="store_true", help="Show plan without executing")
    p_run.add_argument("--auto", action="store_true", help="Auto-complete hybrid steps")
    p_run.add_argument("--review", action="store_true",
                       help="Arm a review gate after EVERY step - the "
                            "every-step case of --break")
    p_run.add_argument(
        "--rerun", action="append", metavar="TARGET", default=[],
        help="Redo finished work. Repeatable. TARGET is a stage "
             "(preflight|edit), a step (temporal_index), or one clip of one "
             "step (temporal_index:clip_007). Preflight work is skipped once "
             "done, so this is how you ask for it again")
    # The scoping flags come from library/tools/run_scope.py, so this
    # wrapper and the runner it launches accept exactly the same words.
    from library.tools.run_scope import add_scope_arguments
    add_scope_arguments(p_run)
    # The run configuration, registered from its own modules for the same
    # reason: a flag defined in two CLIs is a flag that will differ.
    from library.tools.run_profile import add_profile_arguments
    from library.tools.breakpoints import add_breakpoint_arguments
    add_profile_arguments(p_run)
    add_breakpoint_arguments(p_run)
    p_run.add_argument("--resume", action="store_true",
                       help="Resume pipeline from pending gates")
    p_run.add_argument("--full-auto", choices=["agent", "agy", "api"],
                       help="Run full pipeline autonomously using specified LLM backend "
                            "(`agy` is a deprecated alias of `agent`)")
    p_run.add_argument("--llm-timeout", type=int, default=300,
                       help="Timeout for LLM response in agent backend")
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
    # notes
    p_notes = sub.add_parser(
        "notes",
        help="Show which timeline note the captain typed went to which step")
    p_notes.add_argument("slug", metavar="PROJECT",
                         help="Project slug, or an absolute path")
    p_notes.add_argument(
        "--write", action="store_true",
        help="also write the routing record and ROUTED-NOTES.md into "
             "the project's marker_feedback/ folder")
    p_notes.set_defaults(func=cmd_notes)

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


