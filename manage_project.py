#!/usr/bin/env python3
"""
manage_project.py - Top-level CLI for managing video projects.

This is the user-facing entry point for project management. It wraps
the project_registry module and provides commands for creating, listing,
running, and archiving video projects.

The repo is the engine. Projects (assets, footage, pipeline output) live
in PIPELINE_PROJECTS_ROOT (set in ~/.config/ren/config.env; see `ren config`).

Usage:
    python3 manage_project.py list
    python3 manage_project.py list --client personal --status in_progress
    python3 manage_project.py new my-vlog --name "Beltline Vlog"
    python3 manage_project.py new my-show --name "My Show" --client personal
    python3 manage_project.py status my-show
    python3 manage_project.py status "/abs/path/to/a/project"
    python3 manage_project.py run my-show
    python3 manage_project.py run my-show --from creative_direction
    python3 manage_project.py archive my-show
    python3 manage_project.py init-root

Only `run` needs the ML virtual environment; see ML_DEPENDENT_COMMANDS.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from library.tools.ren_refusal import REFUSAL_EXIT_CODE, RenRefusal
from library.tools.resolve_lock import under_lease

# Add repo root to path
REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))


# ─── The ML preflight, and the commands it is for ─────────────
#
# The heavy ML packages are imported by PIPELINE STEPS
# (step_1_04_temporal_index, and the analysis tools under
# library/tools/analysis/), never by this CLI.  Everything else this
# file does - listing projects, reading a project.yaml, regenerating a
# traceback - reaches none of them.
#
# So the check belongs to the commands that need it, and `run` is the
# whole list: it launches library/processes/edit_video/run_pipeline.py
# with sys.executable, so the interpreter running THIS process is the
# one the steps will import from, and checking it here is a real check
# of the child rather than a guess about it.
#
# It used to run at import time, before argparse had seen the command,
# and the captain could not open the review dashboard for want of a
# transcription library a command that needed none of it. (P2 retired
# the dashboard; the preflight design it forced stays.)
ML_DEPENDENT_COMMANDS = ("run",)

# Every subcommand main() registers, so the message above can say which
# ones still work. main() asserts this against the parser it built, so
# adding a subcommand without listing it here fails loudly rather than
# leaving the advice quietly wrong.
ALL_COMMANDS = (
    "init-root", "list", "new", "propose-reels", "build-reels", "drift",
    "post-header", "shift-rows", "scale-rows", "fit-picture",
    "caption-width",
    "safe-zones",
    "deliver-reel",
    "watch-reel", "hear-reel", "touch-reel", "undo", "ren-dry-run",
    "status",
    "info", "trace", "organize", "resolve-organize", "resolve-prune",
    "resolve-mark-master",
    "check", "run", "archive", "notes", "take-pick-preview",
    "round-diff", "pr-body", "sign-off", "purge", "discharge-uncarried", "variant",
    "relink", "reindex", "setup-hooks",
)

ML_REQUIRED_PACKAGES = ("mlx_vlm", "easyocr", "torch")

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
# An import check answers "is the package here", never "is it the
# version this pipeline declares".  The incident that built this check,
# kept because the failure mode is what the floor below guards against:
# on 2026-09-05 every ML environment on the build machine carried
# whisperx 3.2.0 against a declared `whisperx>=3.8,<4`, because they
# were built on Python 3.14 - which requirements.txt forbids in its own
# header, in capitals, for exactly this reason.  3.2.0 imports
# perfectly and then raises
#
#     TypeError: TranscriptionOptions.__init__() missing 2 required
#     positional arguments: 'multilingual' and 'hotwords'
#
# on every transcribe call.  step_1_04 catches that per clip and carries
# on, so the measured outcome on project 001 was 17 clips reporting
# "0 regions, 0.0s speech, 0 words", no spine, no subtitles - the entire
# edit missing, reported as success.  requirements.txt has documented
# that whole chain since 2026-08-17 and nothing ever checked it.
# (whisperx itself left the venv and the manifest on 2026-09-24; the
# check stays because mlx_vlm carries a floor with the same shape.)
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
    from packaging.utils import canonicalize_name

    wanted = {canonicalize_name(ML_DISTRIBUTION_NAMES.get(p, p))
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
        # Canonicalized: `Requirement` keeps `mlx_vlm` as written while
        # the manifest names the distribution `mlx-vlm`, and an
        # un-normalized comparison silently enforces nothing. Found
        # 2026-09-24 when the mlx_vlm floor this check exists for never
        # matched its own requirements line.
        if canonicalize_name(req.name) in wanted and str(req.specifier):
            found[canonicalize_name(req.name)] = req.specifier
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
    # ONE PER MACHINE, not one per checkout.  This advice used to name
    # `<checkout>/.venv` first, which is 4 GB of the same packages per
    # lane and dies with the lane; `docs/ML_ENVIRONMENT.md` had already
    # moved the real environment outside every checkout for that reason,
    # and the advice had not followed it.  Same ruling as the Node store
    # (`docs/SHARED_ENVIRONMENT.md`), same day.
    from library.tools import shared_environment

    durable = shared_environment.vep_home() / shared_environment.DURABLE_VENV_DIRNAME
    return [
        "No Python interpreter carrying the ML stack was found.",
        f"Build it ONCE PER MACHINE, on PYTHON 3.12 - requirements.txt "
        f"explains why no other version works - at {durable}:",
        f"    uv venv --python 3.12 {durable}",
        f"    uv pip install --python {durable / 'bin' / 'python3'} "
        f"-r {repo_root / 'requirements.txt'}",
        "",
        "Without uv, any real 3.12 interpreter works:",
        f"    python3.12 -m venv {durable}",
        f"    {durable / 'bin' / 'pip'} install -r {repo_root / 'requirements.txt'}",
        "",
        f"docs/ML_ENVIRONMENT.md is the full procedure and its four checks. "
        f"A per-checkout {repo_root / '.venv'} still works and is still "
        f"looked for, after the durable one - but it dies with the checkout.",
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
        print("  mlx_vlm below 0.7.2 raises ValueError on every vision load "
              "call - the gemma4 weights need a newer loader - and vision "
              "is broken on every real project until it is upgraded.")
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


def _interview_new(args) -> dict:
    """Ask the captain what a project needs, in the terminal.

    Runs only on a TTY when an intake answer is missing and
    `--non-interactive` was not passed, so the LLM driving `new`
    end-to-end (pipes, flags) never blocks on a question. Every
    answer has a non-interactive flag; the interview writes the same
    values the flags would. An empty answer keeps the default, which
    is always "undeclared" - never a guess dressed as one.
    """
    answers = {}

    def ask(prompt: str, default: str = "") -> str:
        suffix = f" [{default}]" if default else ""
        try:
            reply = input(f"    {prompt}{suffix}: ").strip()
        except EOFError:
            return default
        return reply or default

    if not args.shape:
        print("    What is this video built from?")
        print("      speech   - talk-led, like the podcast")
        print("      music    - a music bed carries it")
        print("      both     - speech and music interleaved or layered")
        print("      picture-led - montage / music video, no speech needed")
        shape = ask("Shape (speech/music/both/picture-led, blank=undecided)")
        shape = shape.strip().lower()
        if shape and shape not in ("speech", "music", "both",
                                   "picture-led"):
            print(f"    Not a shape - leaving undeclared.", file=sys.stderr)
            shape = ""
        answers["shape"] = shape

    if not args.language:
        answers["language"] = ask(
            "Language spoken in the footage (BCP-47)", "en")

    if not args.speaker and not args.no_speakers:
        print("    Who speaks in this footage? One `Name` or `Name:role` "
              "per line, blank line to finish, blank first line for "
              "undecided.")
        speakers = []
        while True:
            try:
                line = input("    Speaker: ").strip()
            except EOFError:
                break
            if not line:
                break
            name, _, role = line.partition(":")
            name = name.strip()
            if not name:
                continue
            entry = {"name": name}
            if role.strip():
                entry["role"] = role.strip()
            speakers.append(entry)
        answers["speakers"] = speakers
        answers["speakers_undecided"] = not speakers

    if not args.brief_title:
        answers["brief_title"] = ask(
            "Brief title (blank keeps the project name)")

    if not args.brand_series:
        answers["brand_series"] = ask(
            "Brand series id for brand.json (blank keeps the slug)")

    return answers


def cmd_new(args):
    """Create a new project, interviewing for what flags don't say."""
    # getattr throughout: namespaces built before these flags existed
    # (and the fractional-fps regression test) carry no intake attrs.
    shape = getattr(args, "shape", None)
    language = getattr(args, "language", None)
    cli_speakers = getattr(args, "speaker", None)
    no_speakers = getattr(args, "no_speakers", False)
    brief_title = getattr(args, "brief_title", None)
    brand_series = getattr(args, "brand_series", None)
    interactive = (sys.stdin.isatty()
                   and not getattr(args, "non_interactive", False))
    interviewed = {}
    if interactive and (
            not shape or not language
            or (not cli_speakers and not no_speakers)
            or not brief_title or not brand_series):
        print(f"\n  New project: {args.slug} - a few questions "
              f"(blank keeps the default):")
        interviewed = _interview_new(args)

    if no_speakers and cli_speakers:
        print("  Error: --no-speakers and --speaker cannot be combined: "
              "one declares zero voices, the other names them.",
              file=sys.stderr)
        sys.exit(2)

    if no_speakers:
        speakers = []
    elif cli_speakers:
        speakers = []
        for raw in cli_speakers:
            name, _, role = raw.partition(":")
            name = name.strip()
            if not name:
                print(f"  Error: --speaker {raw!r} names nobody.",
                      file=sys.stderr)
                sys.exit(2)
            entry = {"name": name}
            if role.strip():
                entry["role"] = role.strip()
            speakers.append(entry)
    elif interviewed.get("speakers_undecided"):
        speakers = None
    else:
        speakers = interviewed.get("speakers")

    try:
        config = create_project(
            slug=args.slug,
            name=args.name or args.slug.replace("-", " ").title(),
            client=args.client or "",
            template=args.template or "",
            fps=float(args.fps) if args.fps else 30,
            resolve_project_name=args.resolve_name or "",
            tags=args.tags.split(",") if args.tags else [],
            description=args.description or "",
            language=(language
                      or interviewed.get("language") or "en"),
            shape=shape or interviewed.get("shape") or "",
            speakers=speakers,
            brief_title=(brief_title
                         or interviewed.get("brief_title") or ""),
            brand_series=(brand_series
                          or interviewed.get("brand_series") or ""),
        )
        print(f"\n  ✓ Created project: {config.name}")
        print(f"    Slug:     {config.slug}")
        print(f"    Path:     {config.project_root}")
        print(f"    Resolve:  {config.resolve.project_name}")
        print(f"    Template: {config.pipeline.brand_template}")
        print(f"    Language: {config.source.language}")
        print(f"    Shape:    {config.source.shape or '(undecided)'}")
        if config.source.speakers is None:
            print(f"    Speakers: (undecided)")
        elif not config.source.speakers:
            print(f"    Speakers: none declared (music / montage)")
        else:
            print(f"    Speakers: "
                  f"{', '.join(s.get('name', '') for s in config.source.speakers)}")
        print(f"\n  Scaffolded: project.yaml, brand.json, brief.md, "
              f"style.yaml, video.yaml")
        print(f"\n  Next steps:")
        print(f"    1. Copy raw footage to: {config.raw_dir}")
        print(f"    2. Fill brief.md, then run pipeline: python3 manage_project.py run {config.slug}")

    except FileExistsError as e:
        raise RenRefusal(
            f"{e}",
            "a project with this slug already exists",
            f"run `ren status {args.slug}` to see it, or create under "
            f"another slug") from e
    except ValueError as e:
        raise RenRefusal(
            f"{e}",
            "one of the new-project values does not check out",
            "fix the flagged value and re-run `ren new`") from e


def cmd_status(args):
    """Show project status."""
    try:
        info = project_status(args.slug)
    except FileNotFoundError as e:
        raise RenRefusal(
            f"{e}",
            "no project with this slug is under PIPELINE_PROJECTS_ROOT",
            f"run `ren new {args.slug}` to create it, `ren projects` "
            f"to list them, or pass the project's absolute path "
            f"instead of the slug") from e

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


def cmd_take_pick_preview(args):
    """Print one reel's take-pick question in a single output.

    What a worker currently gathers between reels by re-reading
    several large records - each candidate take's freshness, the
    boundary-snap deltas, and the words at each boundary once.
    Read-only: no Resolve, no model, no state write. See
    library/tools/take_pick_preview.py.
    """
    from library.tools import take_pick_preview

    try:
        config = get_project(args.slug)
        project_folder = str(config.project_root)
    except FileNotFoundError:
        project_folder = args.slug

    argv = [project_folder, "--reel", args.reel]
    if args.threshold is not None:
        argv += ["--threshold", str(args.threshold)]
    if args.json:
        argv.append("--json")
    sys.exit(take_pick_preview.main(argv))


def cmd_round_diff(args):
    """What changed between two rounds of the captain's feedback.

    A ROUND is the version object (library/tools/versions/rounds.py): one
    version per batch of the captain's feedback, across every reel that
    batch touched. The diff is `reel_read.rows_of` through
    `reel_replace_guard.diff_rows` over two stored snapshots - off
    disk, in milliseconds, with Resolve closed. See
    library/tools/versions/rounds.py.
    """
    from library.tools.versions import rounds

    try:
        config = get_project(args.slug)
        project_folder = str(config.project_root)
    except FileNotFoundError:
        project_folder = args.slug

    argv = [project_folder]
    if args.backfill:
        argv.append("--backfill")
    if args.list_rounds:
        argv.append("--list")
    if args.earlier is not None:
        argv += ["--from", str(args.earlier)]
    if args.later is not None:
        argv += ["--to", str(args.later)]
    sys.exit(rounds.main(argv))


def cmd_pr_body(args):
    """Generate the per-item enumeration section of a PR body.

    Reads the edit ledger (`external/edit_ledger.json`) and prints
    which reels, cards or placements a change touched with before and
    after values, in the shape a worker pastes into the PR body -
    so a small edit is not hand-enumerated card by card. Read only:
    no ledger schema change, nothing written. See
    library/tools/ledger_pr_body.py.
    """
    from library.tools import ledger_pr_body

    try:
        config = get_project(args.slug)
        project_folder = str(config.project_root)
    except FileNotFoundError:
        project_folder = args.slug

    argv = [project_folder]
    for prefix in args.reel or []:
        argv += ["--reel", prefix]
    sys.exit(ledger_pr_body.main(argv))


def cmd_sign_off(args):
    """Sign off a BUILT reel, or list what is signed off.

    Approval used to live only on a PROPOSED moment before a build, so
    the newest build under the final name was always the answer and an
    approved reel could be silently replaced. A sign-off is durable,
    attaches to the built reel and the round it was built in, and
    promotion refuses over it unless it is declared with
    `build-reels --supersede`. See library/tools/reel_signoff.py.
    """
    from library.tools import reel_signoff

    try:
        config = get_project(args.slug)
        project_folder = str(config.project_root)
    except FileNotFoundError:
        project_folder = args.slug

    if not args.reel:
        live = reel_signoff.signed_off(project_folder)
        if not live:
            print("No reel in this project is signed off.")
            return
        print(f"── {len(live)} signed-off reel(s) ──")
        for reel in sorted(live):
            print(f"  {reel_signoff.describe(project_folder, reel)}")
            note = (live[reel].get("note") or "").strip()
            if note:
                print(f'      "{note}"')
        return
    if args.withdraw:
        if reel_signoff.withdraw(project_folder, args.reel,
                                 why=args.note):
            print(f"Withdrew the sign-off on {args.reel!r}. The "
                  f"withdrawal is recorded, not erased.")
        else:
            print(f"{args.reel!r} was not signed off; nothing changed.")
        return
    try:
        entry = reel_signoff.sign_off(project_folder, args.reel,
                                      note=args.note, by=args.by)
    except reel_signoff.UncarriedNotesOpen as owed:
        print(owed.render(), file=sys.stderr)
        sys.exit(REFUSAL_EXIT_CODE)
    print(f"Signed off {entry['reel']!r}"
          + (f" in round {entry['round']}" if entry.get("round") else "")
          + f", by {entry['by']}.")
    print(f"  A promotion that would replace it now REFUSES unless it "
           f"declares `build-reels --supersede {entry['reel']!r}`.")
    from library.tools import retention
    print(f"  {retention.after_signoff(project_folder)}")


def cmd_purge(args):
    """Plan, or apply, the lean-retention purge. See library/tools/retention.py.

    Without `--apply` this only PLANS: it writes the manifest of what
    would go and removes nothing. `--apply <manifest>` removes exactly
    what that manifest still lists, after re-proving every path.
    """
    from library.tools import retention

    try:
        config = get_project(args.slug)
        project_folder = str(config.project_root)
    except FileNotFoundError:
        project_folder = args.slug
    try:
        db_paths = args.db or retention.database_paths(project_folder)
        if args.apply:
            record = retention.apply_purge(args.apply, db_paths,
                                           project_folder=project_folder)
            print(f"Removed {record['removed_count']} path(s), "
                  f"{record['bytes'] / (1024 ** 3):.2f} GiB. The manifest "
                  f"stays at {args.apply}.")
            return
        plan = retention.plan_purge(project_folder, db_paths)
    except (retention.PurgeRefused,
            retention.RetentionSettingInvalid) as refused:
        print(refused.render(), file=sys.stderr)
        sys.exit(REFUSAL_EXIT_CODE)
    if plan.mode == retention.KEEP:
        print(f"Retention is {retention.KEEP!r}: nothing is purged.")
        return
    manifest = retention.write_plan(plan)
    print(f"{len(plan.candidates)} path(s), "
          f"{plan.reclaimable_bytes / (1024 ** 3):.2f} GiB would go - "
          f"listed in {manifest}. Nothing was removed; apply with "
          f"`ren purge {args.slug} --apply {manifest}`.")


def cmd_discharge_uncarried(args):
    """Discharge an uncarried-note obligation, or list what is owed.

    A promotion that drops the captain's note files an obligation
    (`library/tools/uncarried_notes.py`), and the reel cannot be signed
    off until each one is discharged with a stated reason - what
    happened to those words. Omitted reel lists everything open.
    """
    from library.tools import uncarried_notes as owed
    from library.tools import reel_signoff

    try:
        config = get_project(args.slug)
        project_folder = str(config.project_root)
    except FileNotFoundError:
        project_folder = args.slug

    if not args.reel:
        open_notes = owed.open_for(project_folder)
        if not open_notes:
            print("No reel owes an uncarried note.")
            return
        print(f"-- {len(open_notes)} open uncarried-note obligation(s) --")
        for entry in open_notes:
            words = " ".join(
                (entry.get("text") or "").split())[:120]
            print(f"  {entry.get('reel')}: \"{words}\"")
            print(f"    {entry['identity']}")
        return
    reel = reel_signoff.base_name(args.reel)
    if not args.identity:
        open_notes = owed.open_for(project_folder, reel)
        if len(open_notes) != 1:
            if not open_notes:
                print(f"{reel!r} owes nothing - nothing discharged.")
                return
            listing = "\n".join(
                f"  --identity {entry['identity']} "
                f"\"{' '.join((entry.get('text') or '').split())[:120]}\""
                for entry in open_notes)
            raise RenRefusal(
                f"{reel!r} owes {len(open_notes)} notes - name one",
                f"{listing}",
                f"re-run with --identity one of the listed ids, e.g. "
                f"`ren discharge {args.slug} {reel} --identity "
                f"{open_notes[0]['identity']} --note <what happened>`")
        args.identity = open_notes[0]["identity"]
    try:
        entry = owed.discharge(project_folder, reel, args.identity,
                               by=args.by, note=args.note)
    except (owed.DischargeRefused, owed.UncarriedNoteUnknown) as refused:
        print(refused.render(), file=sys.stderr)
        sys.exit(REFUSAL_EXIT_CODE)
    words = " ".join((entry.get("text") or "").split())[:120]
    print(f"Discharged {entry['identity']} on {entry['reel']!r}: "
          f"\"{words}\" - by {entry['discharged']['by']}.")
    print(f"  The discharge is recorded, never erased.")


def cmd_variant(args):
    """Two versions of one reel, alive at once, compared, one chosen.

    The captain's routine comparison (`data/vep-can-it-hold-up-in-a-
    real-editing-workflow` §7): they pick between two treatments of the
    same moment by WATCHING both. Everything under this command exists
    so that is a normal thing to do rather than a scripted one-off.

    A variant is ONE object with two projections. Its DECLARATIONS live
    on a git branch (`library/tools/versions/variants.py`) and its
    PICTURE lives on a Resolve timeline; the two names derive from each
    other, both ways. Git holds one branch at a time and Resolve holds
    every timeline at once, which is why the declarations are merged
    one at a time and the pictures are compared side by side.

        new      declare a variant and branch for it
        build    build every declared variant of a reel, beside the reel
        list     what is declared and what is alive
        diff     what actually differs between two built variants
        choose   make one of them the reel; the other goes to the archive
        merge    merge a variant branch's declarations back

    See library/tools/versions/variants.py (the spec, the branch, what is
    alive, the comparison and the choice) and AGENTS.md 10.4.
    """
    import json as _json

    from library.tools.versions import variants

    project_folder = _reel_project_folder(args.project)

    def _base_final(reel_number):
        """The approved timeline name for a reel, off the plan."""
        from library.tools.reel_proposal import proposal_path, read_proposal

        for moment in read_proposal(str(proposal_path(project_folder))):
            if int(moment.number) == int(reel_number):
                return moment.timeline_name
        raise RenRefusal(
            f"the plan names no reel {reel_number}",
            "a variant branches a reel the plan describes - it never "
            "invents one",
            f"run `ren propose {args.project}` first, or branch a reel "
            f"the plan names") from None

    if args.variant_command == "new":
        spec = {"suffix": args.suffix}
        for key in ("j_cut", "cutaway", "cover"):
            raw = getattr(args, key)
            if raw:
                try:
                    spec[key] = _json.loads(raw)
                except ValueError as bad:
                    print(f"Error: --{key.replace('_', '-')} is not "
                          f"JSON: {bad}", file=sys.stderr)
                    sys.exit(2)
        if args.declares:
            spec["declares"] = sorted(set(args.declares))
        if args.watch:
            spec["watch"] = args.watch
        result = variants.create_variation(
            project_folder, args.reel, _base_final(args.reel), spec)
        if not result.get("created"):
            print(f"REFUSED: {result.get('reason')}", file=sys.stderr)
            sys.exit(REFUSAL_EXIT_CODE)
        print(f"Declared {result['timeline_name']!r} on branch "
              f"{result['branch']} ({result['commit']}).")
        if spec.get("declares"):
            print(f"  It differs in {spec['declares']} - edit "
                  f"external/<store>.json ON THIS BRANCH, commit, then "
                  f"`variant build`. A declaring variant is built from "
                  f"its own branch and nowhere else.")
        print(f"  Build it with: manage_project.py variant build "
              f"{args.project} {args.reel}")
        return

    if args.variant_command == "list":
        record = variants.read_variant_specs(project_folder)
        declared = record.get("variants") or {}
        alive = variants.read_builds(project_folder).get("builds") or {}
        if not declared and not alive:
            print("No variant is declared in this project.")
            print("  Declare one with: manage_project.py variant new "
                  f"{args.project} <reel> --suffix ' (name)' ...")
            return
        print(f"── variants on branch "
              f"{variants.current_branch(project_folder) or '(no repo)'} "
              f"──")
        for reel in sorted(declared, key=lambda r: (int(r), r)):
            base = _base_final(int(reel))
            print(f"  Reel {int(reel)}: {base}")
            for spec in declared[reel] or ():
                name = variants.variant_timeline_name(base,
                                                      spec["suffix"])
                built = alive.get(name)
                differs = (sorted(spec.get("declares") or ())
                           or [k for k in ("j_cut", "cutaway")
                               if spec.get(k)])
                print(f"    {spec['suffix'].strip()} -> {name}")
                print(f"      branch  {variants.variant_branch_name(int(reel), spec['suffix'])}")
                print(f"      differs {differs}")
                if spec.get("watch"):
                    print(f"      watch   {spec['watch']}")
                print(f"      status  "
                      + (f"BUILT {built.get('built_at', '')[:19]}"
                         if built else "not built"))
        return

    if args.variant_command == "build":
        from library.tools import reel_build

        base = _base_final(args.reel)
        specs = variants.specs_for_reel(project_folder, args.reel)
        if args.suffix:
            specs = [s for s in specs if s["suffix"] in set(args.suffix)]
        if not specs:
            raise RenRefusal(
                f"reel {args.reel} declares no variant"
                + (f" with suffix {args.suffix}" if args.suffix else ""),
                "a build needs a declared variant to build",
                f"declare one first: `ren variant {args.project} new "
                f"{args.reel} --suffix ' (NAME)'`") from None
        print(f"Building {len(specs)} variant(s) of {base!r} beside it.")
        try:
            result = reel_build.build_reel_variants(
                project_folder, args.reel, specs)
        except Exception as refused:                        # noqa: BLE001
            print(f"REFUSED: {refused}", file=sys.stderr)
            sys.exit(1)
        for name, record in sorted(result["variants"].items()):
            print(f"  {name}: conformance-clean"
                  + (f" - watch: {record['watch']}"
                     if record.get("watch") else ""))
        print(f"  Compare them: manage_project.py variant diff "
              f"{args.project} {args.reel} <suffix-a> <suffix-b>")
        print(f"  Choose one:   manage_project.py variant choose "
              f"{args.project} {args.reel} <suffix> --why '...'")
        return

    if args.variant_command == "diff":
        try:
            diff = variants.compare(project_folder, args.reel,
                                  args.earlier, args.later)
        except variants.ChoiceRefused as refused:
            print(refused.render(), file=sys.stderr)
            sys.exit(REFUSAL_EXIT_CODE)
        print(variants.render_comparison(diff))
        return

    if args.variant_command == "merge":
        result = variants.merge_variations(project_folder, args.branch,
                                           target_branch=args.target)
        print(_json.dumps(result, indent=2))
        sys.exit(0 if result.get("merged") else 1)

    # choose - the only subcommand that drives Resolve. The connection
    # goes through `reel_build`'s own helper, so the locale wrapper and
    # the exact-name rule (AGENTS.md 5) are honoured here exactly as
    # they are in a build.
    import yaml as _yaml

    from library.tools.reel_build import _connect_resolve_project

    base = _base_final(args.reel)
    with open(os.path.join(project_folder, "project.yaml"),
              encoding="utf-8") as handle:
        resolve_name = (_yaml.safe_load(handle).get("resolve") or {}).get(
            "project_name", os.path.basename(project_folder))
    project = _connect_resolve_project(resolve_name)
    try:
        report = variants.choose(
            project, project.GetMediaPool(), project_folder, args.reel,
            base, args.suffix, args.why,
            variant_names=variants.declared_variant_timelines(
                project_folder),
            supersede_declared=args.supersede or ())
    except Exception as refused:                            # noqa: BLE001
        print(f"REFUSED: {refused}", file=sys.stderr)
        sys.exit(1)
    print(variants.render_choice(report))


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


@under_lease("organize Resolve media pool")
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

    if args.remove_proof is not None:
        from library.tools.execution.organise_media_pool import read_pool
        from library.tools.execution import remove_proof as proof_ex
        from library.tools.execution.retire_empty_bins import read_bin_tree
        from library.tools.proof_cleanup import (
            discover_proof_bins,
            discover_superseded_bins,
            is_authorised_superseded,
            plan_proof_removal,
            plan_superseded_removal,
        )
        artefacts, _, _, _ = read_pool(project)
        tree = read_bin_tree(project)
        if is_authorised_superseded(args.remove_proof):
            plan_fn = plan_superseded_removal
            candidate_bins = discover_superseded_bins(
                artefacts, list(tree), args.remove_proof)
        else:
            plan_fn = plan_proof_removal
            candidate_bins = discover_proof_bins(artefacts, list(tree))
        if not args.proof_bin:
            candidates = candidate_bins
            if candidates and not args.timeline_only:
                print("  Proof/demo caption bins in this pool - pass the "
                      "ones to remove back with --proof-bin:")
                for path in candidates:
                    print(f"    {'/'.join(path)}")
                print("  Nothing was changed.")
                return
            if candidates and args.timeline_only:
                listing = "\n".join(f"    {'/'.join(path)}"
                                    for path in candidates)
                raise RenRefusal(
                    "candidate bins exist and --timeline-only was passed",
                    f"{listing}",
                    "pass them with --proof-bin, or drop --timeline-only") \
                    from None
            if not candidates:
                print("  No proof/demo caption bins in this pool - "
                      "removing the timeline alone.")
        try:
            plan = plan_fn(
                artefacts, list(tree),
                timeline_name=args.remove_proof,
                bin_names=args.proof_bin,
                project_root=project_folder, master_name=master)
        except Exception as exc:
            print(f"  REFUSED: {exc}")
            sys.exit(1)
        result = proof_ex.remove_proof(
            project, plan, proof_ex.journal_path_for(project_folder))
        print(f"  Removed proof timeline {result['timeline']!r}: "
              f"{result['removed_items']} pool item(s), "
              f"{len(result['bins'])} bin(s):")
        for bin_path in result["bins"]:
            print(f"    {bin_path}")
        print("  Verified untouched:")
        for name in result["verified_untouched"]:
            print(f"    {name}")
        print(f"  Journal: {result['journal_path']}")
        print("  Files were NOT deleted - pool items only. There is no "
              "undo for a deleted timeline.")
        return

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
            print(f"  Retired {len(retired['retired'])} bin(s)"
                  + (f" with {retired['removed_items']} pool item(s)"
                     if retired.get("removed_items") else "") + ":")
            for bin_path in retired["retired"]:
                print(f"    {bin_path}")
            if retired.get("removed_items"):
                print("  Removed items were pool items only - their files "
                      "are still on disk, named in the journal.")
            print(f"  Retirement journal: {retired['journal_path']}")
            print(f"  Undo it with: --revert {retired['journal_path']}")
        for held in result.get("held_for_dead_sweep") or []:
            print(f"  Held for the dead-bin sweep: {held['name']} "
                  f"(in {held['bin']})")
    else:
        if result["retirements"]:
            print(f"\n{len(result['retirements'])} bin(s) "
                  f"would retire on --apply.")
        print("\nNothing was changed. Pass --apply to perform this.")
    print()
    print(render_unplaced(result["unplaced"]))


@under_lease("prune Resolve media pool")
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


@under_lease("mark Resolve master timeline")
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
        raise RenRefusal(
            f"{e}",
            "no project with this slug is under PIPELINE_PROJECTS_ROOT",
            f"run `ren new {args.slug}` to create it, `ren projects` "
            f"to list them, or pass the project's absolute path "
            f"instead of the slug") from e

    project_dir = str(config._project_root)
    print(f"Checking project: {config.name} ({project_dir})")

    # 1. Brand template resolves
    print("Checking brand template...")
    from library.tools.brand_registry import resolve_template_reference
    try:
        template = resolve_template_reference(
            config.pipeline.brand_template,
            project_folder=str(config._project_root))
    except Exception as e:
        raise RenRefusal(
            f"brand template failed to resolve: {e}",
            "the check cannot verify a template it cannot read",
            "fix pipeline.brand_template in project.yaml (or clear it "
            "for no template), then re-run `ren check`") from e

    # 2. creative_brief path resolves IF declared
    print("Checking creative_brief...")
    if config.pipeline.creative_brief:
        cb_path = config._project_root / config.pipeline.creative_brief
        if not cb_path.exists():
            raise RenRefusal(
                f"declared creative_brief path does not exist: {cb_path}",
                "the check cannot verify a brief it cannot read",
                "fix pipeline.creative_brief in project.yaml (or clear "
                "it), then re-run `ren check`") from None

    # 3. Environment variables exist
    print("Checking environment paths...")
    from library.tools.paths import SFX_LIBRARY, MUSIC_LIBRARY, PROJECTS_ROOT
    if not SFX_LIBRARY.exists():
        raise RenRefusal(
            f"PIPELINE_SFX_LIBRARY does not exist: {SFX_LIBRARY}",
            "the check cannot verify a library that is not there",
            "set PIPELINE_SFX_LIBRARY (`ren config --init`, then edit "
            "it), then re-run `ren check`") from None
    if not MUSIC_LIBRARY.exists():
        raise RenRefusal(
            f"PIPELINE_MUSIC_LIBRARY does not exist: {MUSIC_LIBRARY}",
            "the check cannot verify a library that is not there",
            "set PIPELINE_MUSIC_LIBRARY (`ren config --init`, then edit "
            "it), then re-run `ren check`") from None
    if not PROJECTS_ROOT.exists():
        raise RenRefusal(
            f"PIPELINE_PROJECTS_ROOT does not exist: {PROJECTS_ROOT}",
            "the check cannot verify a projects root that is not there",
            "create it (`ren init`), then re-run `ren check`") from None

    # 4. ffprobe reads every footage file
    print("Checking footage files with ffprobe...")
    from library.tools.footage_identity import enumerate_footage
    raw_footage_files, _ = enumerate_footage(project_dir)

    if not raw_footage_files:
        raise RenRefusal(
            "no footage files found in project",
            "the check cannot verify footage that is not there",
            f"copy raw footage to the project's raw footage dir, then "
            f"re-run `ren check {args.slug}`") from None

    from library.steps.step_1_02_catalog_footage.step import extract_metadata

    fps_counts = {}
    res_counts = {}

    for file_info in raw_footage_files:
        filepath = file_info["path"]
        # print(f"  Probing {file_info['filename']}...")
        metadata = extract_metadata(filepath)
        if metadata is None or "error" in metadata:
            err = metadata.get("error", "unknown error") if metadata else "unknown error"
            raise RenRefusal(
                f"ffprobe failed to read {filepath}: {err}",
                "the check cannot verify a file ffprobe cannot read",
                "fix or replace the file, then re-run "
                f"`ren check {args.slug}`") from None

        for field in ["duration_seconds", "width", "height", "frame_rate"]:
            if metadata.get(field) is None:
                raise RenRefusal(
                    f"missing required field '{field}' in file "
                    f"'{file_info['filename']}'",
                    "the pipeline cannot cut a file it cannot measure",
                    "re-encode the file so ffprobe reports every field, "
                    f"then re-run `ren check {args.slug}`") from None

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
        print(e.render(), file=sys.stderr)
        sys.exit(REFUSAL_EXIT_CODE)

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
        raise RenRefusal(
            f"{e}",
            "no project with this slug is under PIPELINE_PROJECTS_ROOT",
            f"run `ren new {args.slug}` to create it, `ren projects` "
            f"to list them, or pass the project's absolute path "
            f"instead of the slug") from e

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


def cmd_reindex(args):
    """Move a project's words onto Voz plus MFA, when asked.

    Legacy temporal-index files (timed by the old WhisperX path, or
    by nothing) are invalidated so the next transcription makes them
    with the current seam; files already current are untouched. Then
    the pipeline's temporal_index step runs, re-transcribing exactly
    the invalidated clips.
    """
    try:
        config = get_project(args.slug)
    except FileNotFoundError as e:
        raise RenRefusal(
            f"{e}",
            "no project with this slug is under PIPELINE_PROJECTS_ROOT",
            f"run `ren new {args.slug}` to create it, `ren projects` "
            f"to list them, or pass the project's absolute path "
            f"instead of the slug") from e

    from library.steps.step_1_04_temporal_index.step import (
        invalidate_legacy_indexes)
    report = invalidate_legacy_indexes(str(config.project_root))
    invalidated = report["invalidated"]
    current = report["current"]
    unrecognized = report["unrecognized"]
    print(f"  Temporal index: {len(current)} current, "
          f"{len(invalidated)} invalidated, "
          f"{len(unrecognized)} unrecognized (left alone)",
          file=sys.stderr)
    for name in invalidated:
        print(f"    invalidated: {name}", file=sys.stderr)
    for name in unrecognized:
        print(f"    unrecognized: {name}", file=sys.stderr)
    if not invalidated:
        print("  Nothing to re-transcribe: every index names a "
              "current instrument.", file=sys.stderr)
        return

    runner = PILOT_ROOT / "library" / "processes" / "edit_video" / "run_pipeline.py"
    cmd = [sys.executable, str(runner), "--project",
           str(config.project_root), "--only", "temporal_index"]
    print(f"  Running pipeline for: {config.name}")
    print(f"  Project: {config.project_root}")
    print(f"  Command: {' '.join(cmd)}")
    print(f"")

    import subprocess
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PILOT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(cmd, env=env, check=False)
    sys.exit(result.returncode)


def cmd_archive(args):
    """Archive a completed project."""
    try:
        new_path = archive_project(args.slug)
        print(f"  ✓ Archived project '{args.slug}' to: {new_path}")
    except FileNotFoundError as e:
        raise RenRefusal(
            f"{e}",
            "no project with this slug is under PIPELINE_PROJECTS_ROOT",
            f"run `ren projects` to list them, or pass the project's "
            f"absolute path instead of the slug") from e


def cmd_info(args):
    """Show project configuration details."""
    try:
        config = get_project(args.slug)
    except FileNotFoundError as e:
        raise RenRefusal(
            f"{e}",
            "no project with this slug is under PIPELINE_PROJECTS_ROOT",
            f"run `ren new {args.slug}` to create it, `ren projects` "
            f"to list them, or pass the project's absolute path "
            f"instead of the slug") from e

    from library.schemas.project_config import project_config_to_dict
    data = project_config_to_dict(config)
    print(json.dumps(data, indent=2))


def cmd_relink(args):
    """Relink offline media in Resolve after project migration."""
    try:
        from library.tools.resolve_relinker import relink_project
    except ImportError:
        raise RenRefusal(
            "could not import resolve_relinker",
            "relinking runs through Resolve scripting, which this "
            "interpreter cannot load",
            "run under the pipeline interpreter (`bin/vep "
            "manage_project.py relink ...`), then re-run") from None

    result = relink_project(args.slug, dry_run=args.scan)

    if not result.get("success"):
        raise RenRefusal(
            f"{result.get('error', 'Unknown error')}",
            "the relink did not succeed",
            "fix the cause above (usually media paths), then re-run "
            "`ren relink`") from None

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


def cmd_setup_hooks(args):
    """Install the marker-feedback hook for a host (plan by default)."""
    from ren.setup_hooks import APPS, _target, install
    if getattr(args, "list", False):
        for app in APPS:
            print(f"{app}: {_target(app)}")
        return
    raise SystemExit(install(args.app.lower() if args.app else "",
                             write=args.write))


def cmd_propose_reels(args):
    """Publish step 3.4's chosen moments as the captain's review file."""
    from library.tools.reel_proposal import write_from_step_output
    try:
        config = get_project(args.project)
    except FileNotFoundError as e:
        raise RenRefusal(
            f"{e}",
            "no project with this slug is under PIPELINE_PROJECTS_ROOT",
            f"run `ren projects` to list them, or pass the project's "
            f"absolute path instead of the slug") from e
    path = write_from_step_output(str(config._project_root), force=args.force)
    print(f"Wrote {path}")


def cmd_build_reels(args):
    """Run the `reels` PROCESS through its own runner.

    The node order, the requirement checks, the node records and the
    closing commit are the runner's (`library/processes/reels/
    run_reels.py`); this subcommand resolves the project and calls it,
    exactly as `run` calls the edit_video runner. No build path lives
    here.
    """
    from library.processes.reels import run_reels

    code = run_reels.run(_reel_project_folder(args.project), args)
    if code:
        sys.exit(code)



def cmd_drift(args):
    """Compare every reel's build snapshot against its live self-read.

    The caller `library/tools/transform_drift.py` never had: for each
    reel, the newest `pipeline_output/review/<name>.timeline.json`
    against the live timeline read with THAT timeline current,
    printing the per-reel factor. The same check runs at the start and
    the end of every reel build; this verb runs it on demand.

    It REPORTS, never repairs: exit 0 where every compared reel holds
    what its build wrote, 1 where any reel moved or any reading
    failed (an unreadable reel is never a matching one), 2 where the
    check itself could not run. Reading is the only Resolve act, but
    the self-read moves the cursor to do it, and moving the cursor is
    a write - so this takes the instance and puts the cursor back,
    reading it back to prove it did.
    """
    from library.tools import drift_check
    from library.tools import resolve_lock

    project_folder = _reel_project_folder(args.project)
    try:
        with resolve_lock.resolve_lease("transform drift check",
                                        exclusive=True):
            report = drift_check.check_project(project_folder)
    except resolve_lock.ResolveBusy as busy:
        print(busy.render(), file=sys.stderr)
        sys.exit(REFUSAL_EXIT_CODE)
    if report.get("error"):
        print(f"drift: {report['error']}", file=sys.stderr)
        sys.exit(2)
    if report.get("drifted") or report.get("unreadable"):
        sys.exit(1)


def cmd_post_header(args):
    """Put each reel's social-post header on its EXISTING final timeline.

    `library/tools/reel_post_header.touch_spec` composes one touch-reel
    change per reel - a new "Post Header" row, the header over the
    picture, and (unless `--keep-graphics`) every Semantic-row graphic
    switched OFF, never deleted - and `reel_touchup` stages, verifies,
    promotes and journals it. One batch: `ren undo` reverses it whole.
    `--dry-run` reads the reels and prints each change without writing.
    A reel that already carries a header has it swapped for the tight
    one in place, placed with `--draw-gain`.
    """
    import json as _json

    from library.tools import reel_post_header as rph
    from library.tools import reel_touchup

    project_folder = _reel_project_folder(args.project)
    records: dict = {}

    def spec_for(tracks, number):
        spec = rph.touch_spec(project_folder, number, tracks,
                              fps=24000 / 1001, draw_gain=args.draw_gain,
                              disable_graphics=not args.keep_graphics)
        records[number] = spec.pop("post_header")
        return spec

    def dry(folder, spec):
        return {"dry_run": True, "edits": spec["edits"]}

    try:
        result = reel_touchup.touchup_all_reels(
            project_folder, reels=args.reel or None, spec_for=spec_for,
            applier=dry if args.dry_run else None,
            supersede=args.supersede or None)
    except rph.PostHeaderError as exc:
        print(f"post-header: {exc}", file=sys.stderr)
        sys.exit(2)
    for entry in result["reels"]:
        edits = ((entry.get("receipt") or {}).get("edits")
                 if args.dry_run else None)
        status = ("PLAN" if args.dry_run else "DONE") if entry["ok"] \
            else "REFUSED"
        print(f"{status} reel {entry['reel']} {entry['final']}"
              + (f": {entry['refused']}" if entry.get("refused") else ""))
        if edits:
            for edit in edits:
                print(f"    {_json.dumps(edit)}")
    if not args.dry_run:
        landed = {entry["final"]: entry["reel"] for entry in result["reels"]
                  if entry["ok"]}
        rph.write_plans(project_folder, [
            {"reel": final, "number": number, "basis": rph.HEADER_PLACED,
             **records[number]}
            for final, number in landed.items() if number in records])
    print(f"{result['landed']} reel(s) {'planned' if args.dry_run else 'done'}"
          f", {result['refused']} refused")
    if result["refused"]:
        sys.exit(1)


def cmd_shift_rows(args):
    """Move named rows of each built reel by delivery pixels, as a touch.

    `library/tools/row_shift.py`: one journaled `set_properties` Tilt per
    item, converted from pixels by each item's own media size and the
    draw gain the caller MEASURED. `--dry-run` prints without writing.
    """
    import json as _json

    from library.tools import reel_touchup, row_shift

    project_folder = _reel_project_folder(args.project)
    try:
        moves = row_shift.parse_moves(args.move)
    except row_shift.RowShiftError as exc:
        print(f"shift-rows: {exc}", file=sys.stderr)
        sys.exit(2)

    def spec_for(tracks, number):
        return row_shift.shift_spec(
            tracks, number, moves, frame=(1080, 1920),
            draw_gain=args.draw_gain, skip_prefixes=args.skip)

    def dry(folder, spec):
        return {"edits": spec["edits"]}

    result = reel_touchup.touchup_all_reels(
        project_folder, reels=args.reel or None, spec_for=spec_for,
        applier=dry if args.dry_run else None,
        supersede=args.supersede or None)
    for entry in result["reels"]:
        status = ("PLAN" if args.dry_run else "DONE") if entry["ok"] \
            else "REFUSED"
        print(f"{status} reel {entry['reel']} {entry['final']}"
              + (f": {entry['refused']}" if entry.get("refused") else ""))
        if args.dry_run and entry["ok"]:
            for edit in (entry.get("receipt") or {}).get("edits") or ():
                print(f"    {_json.dumps(edit)}")
    print(f"{result['landed']} reel(s) {'planned' if args.dry_run else 'done'}"
          f", {result['refused']} refused")
    if result["refused"]:
        sys.exit(1)


def cmd_scale_rows(args):
    """Shrink (or grow) named rows of each built reel about a point, as a touch.

    `library/tools/row_shift.scale_spec`: zoom times the factor and the
    drawn centre pulled toward the anchor, one journaled
    `set_properties` per item. `@picture` names the camera rows under
    the TV frame (`fit-picture` scales the frame with them);
    `--move-with` rows keep their size and their place on the picture. `--fit` takes the factor the project's safe-zone policy
    needs for its TV-frame picture (`safe-zones --describe` prints it)
    and the anchor defaults to the frame's own centre.
    """
    import json as _json

    from library.tools import reel_touchup, row_shift
    from library.tools.tv_frame import picture_fit, resolve_tv_frame

    project_folder = _reel_project_folder(args.project)
    look = resolve_tv_frame(project_folder)
    factor = args.by
    if args.fit:
        fit = picture_fit(project_folder)
        if fit is None:
            print("scale-rows: --fit needs a TV frame (pipeline.tv_frame)",
                  file=sys.stderr)
            sys.exit(2)
        factor = fit["scale_needed"] / fit["scale"]
        print(f"--fit: the picture needs x{factor:.4f} to sit inside "
              f"x {fit['visible'][0]}..{fit['visible'][2]}")
    if factor is None:
        print("scale-rows: give --by FACTOR or --fit", file=sys.stderr)
        sys.exit(2)
    if args.about:
        try:
            x, y = (float(v) for v in args.about.split(","))
        except ValueError:
            print(f"scale-rows: --about {args.about!r} is not X,Y",
                  file=sys.stderr)
            sys.exit(2)
    elif look is not None:
        x, y = 540.0, 960.0 + int(look.get("offset_y") or 0)
    else:
        print("scale-rows: no TV frame to scale about; give --about X,Y",
              file=sys.stderr)
        sys.exit(2)
    rows = [r.strip() for r in args.rows.split(",") if r.strip()]
    move = [r.strip() for r in (args.move_with or "").split(",")
            if r.strip()]

    def spec_for(tracks, number):
        spec = row_shift.scale_spec(
            tracks, number, rows, factor, move_rows=move, anchor=(x, y),
            frame=(1080, 1920), draw_gain=args.draw_gain,
            skip_prefixes=args.skip)
        spec.pop("rows", None)
        for row in spec.pop("absent", ()):
            print(f"  reel {number}: no {row!r} row, nothing rides on its "
                  f"picture there", file=sys.stderr)
        return spec

    def dry(folder, spec):
        return {"edits": spec["edits"]}

    try:
        result = reel_touchup.touchup_all_reels(
            project_folder, reels=args.reel or None, spec_for=spec_for,
            applier=dry if args.dry_run else None,
            supersede=args.supersede or None)
    except row_shift.RowShiftError as exc:
        print(f"scale-rows: {exc}", file=sys.stderr)
        sys.exit(2)
    for entry in result["reels"]:
        status = ("PLAN" if args.dry_run else "DONE") if entry["ok"] \
            else "REFUSED"
        print(f"{status} reel {entry['reel']} {entry['final']}"
              + (f": {entry['refused']}" if entry.get("refused") else ""))
        if args.dry_run and entry["ok"]:
            for edit in (entry.get("receipt") or {}).get("edits") or ():
                print(f"    {_json.dumps(edit)}")
    print(f"{result['landed']} reel(s) {'planned' if args.dry_run else 'done'}"
          f", {result['refused']} refused")
    if result["refused"]:
        sys.exit(1)


def cmd_fit_picture(args):
    """Put each built reel's TV picture at a scale, as a journaled touch.

    `library/tools/reel_look.fit_picture_spec`: the camera rows zoom and
    close in on the frame's centre, the frame's pixels are swapped for the
    TV drawn at that scale inside an opaque black surround, and
    `--move-with` rows keep their size and place on the picture. `--fit`
    takes the scale the project's safe-zone policy needs
    (`safe-zones --describe`). Declare the same `pipeline.tv_frame.scale`
    afterwards so a rebuild lands where the touch put the reels.
    """
    import json as _json

    from library.tools import reel_look, reel_touchup, row_shift
    from library.tools.tv_frame import picture_fit

    project_folder = _reel_project_folder(args.project)
    scale = args.scale
    if args.fit:
        fit = picture_fit(project_folder)
        if fit is None:
            print("fit-picture: the project declares no TV frame",
                  file=sys.stderr)
            sys.exit(2)
        scale = fit["scale_needed"]
        print(f"--fit: tv_frame.scale {scale} puts the picture inside "
              f"x {fit['visible'][0]}..{fit['visible'][2]}")
    if scale is None or not 0 < scale <= 1:
        print("fit-picture: give --scale (0 < S <= 1) or --fit",
              file=sys.stderr)
        sys.exit(2)
    move = [r.strip() for r in (args.move_with or "").split(",")
            if r.strip()]

    def spec_for(tracks, number):
        spec = reel_look.fit_picture_spec(
            project_folder, tracks, number, scale, fps=24000 / 1001,
            draw_gain=args.draw_gain, move_rows=move,
            skip_prefixes=args.skip)
        spec.pop("rows", None)
        for row in spec.pop("absent", ()):
            print(f"  reel {number}: no {row!r} row, nothing rides on its "
                  f"picture there", file=sys.stderr)
        return spec

    def dry(folder, spec):
        return {"edits": spec["edits"]}

    try:
        result = reel_touchup.touchup_all_reels(
            project_folder, reels=args.reel or None, spec_for=spec_for,
            applier=dry if args.dry_run else None,
            supersede=args.supersede or None)
    except row_shift.RowShiftError as exc:
        print(f"fit-picture: {exc}", file=sys.stderr)
        sys.exit(2)
    for entry in result["reels"]:
        status = ("PLAN" if args.dry_run else "DONE") if entry["ok"] \
            else "REFUSED"
        print(f"{status} reel {entry['reel']} {entry['final']}"
              + (f": {entry['refused']}" if entry.get("refused") else ""))
        if args.dry_run and entry["ok"]:
            for edit in (entry.get("receipt") or {}).get("edits") or ():
                print(f"    {_json.dumps(edit)}")
    print(f"{result['landed']} reel(s) {'planned' if args.dry_run else 'done'}"
          f", {result['refused']} refused")
    if not args.dry_run and result["landed"]:
        print(f"declare pipeline.tv_frame.scale: {scale} so a rebuild "
              f"lands where these reels now are")
    if result["refused"]:
        sys.exit(1)


def cmd_caption_width(args):
    """Narrow each built reel's captions to the width the platforms leave.

    `library/tools/caption_width.py`: only a card whose words wrap wider
    than the width is re-rendered, through the caption step's own
    renderer, and swapped in place as one journaled touch per reel. The
    width is `--max-width`, or the widest centred box clear of every
    platform's safe zones over the rows the captions draw on.
    """
    import json as _json

    from library.tools import caption_width, reel_touchup
    from library.tools.reel_touchup import resolve_final_name

    project_folder = _reel_project_folder(args.project)
    widths: dict = {}

    def spec_for(tracks, number):
        spec = caption_width.touch_spec(
            project_folder, number, tracks,
            timeline_label=resolve_final_name(project_folder, int(number)),
            draw_gain=args.draw_gain, max_width=args.max_width)
        widths[int(number)] = (spec.pop("max_width"), spec.pop("narrowed"),
                               spec.pop("cards"))
        return spec

    def dry(folder, spec):
        return {"edits": spec["edits"]}

    try:
        result = reel_touchup.touchup_all_reels(
            project_folder, reels=args.reel or None, spec_for=spec_for,
            applier=dry if args.dry_run else None,
            supersede=args.supersede or None)
    except caption_width.CaptionWidthError as exc:
        print(f"caption-width: {exc}", file=sys.stderr)
        sys.exit(2)
    for entry in result["reels"]:
        status = ("PLAN" if args.dry_run else "DONE") if entry["ok"] \
            else "REFUSED"
        known = widths.get(int(entry["reel"]))
        what = (f": {known[1]} of {known[2]} card(s) to {known[0]}px"
                if known and entry["ok"] else "")
        print(f"{status} reel {entry['reel']} {entry['final']}{what}"
              + (f": {entry['refused']}" if entry.get("refused") else ""))
        if args.dry_run and entry["ok"]:
            for edit in (entry.get("receipt") or {}).get("edits") or ():
                print(f"    {_json.dumps(edit)}")
    print(f"{result['landed']} reel(s) {'planned' if args.dry_run else 'done'}"
          f", {result['refused']} refused")
    if result["refused"]:
        sys.exit(1)


def cmd_safe_zones(args):
    """Place a platform safe-zone guide on reel timelines, switched off.

    `library/tools/safe_zone_guide.py`: the guide rides its own row
    above everything the build placed, and its clip is switched OFF and
    read back off, so it never reaches a render. `--remove` takes the
    row away. Moving the cursor is a write, so this takes the instance
    and puts the cursor back.
    """
    from library.tools import resolve_lock
    from library.tools import safe_zone_guide

    project_folder = _reel_project_folder(args.project)
    if args.describe:
        from library.tools.safe_zone_policy import (
            SafeZonePolicyError,
            project_layout,
        )
        from library.tools.tv_frame import picture_fit
        try:
            print("\n".join(project_layout(project_folder).describe()))
            fit = picture_fit(project_folder)
            if fit is not None:
                x0, _y0, x1, _y1 = fit["window"]
                print(f"picture (TV window) at tv_frame.scale "
                      f"{fit['scale']}: x {x0}..{x1} - "
                      + ("inside what every phone shows" if fit["fits"]
                         else f"cropped by the phones; tv_frame.scale "
                              f"{fit['scale_needed']} fits it"))
        except SafeZonePolicyError as exc:
            print(f"safe-zones: {exc}", file=sys.stderr)
            sys.exit(2)
        return
    overlay = None if args.remove else args.overlay
    try:
        with resolve_lock.resolve_lease("safe-zone guides", exclusive=True):
            safe_zone_guide.place_guides(project_folder, args.reel or None,
                                         overlay)
    except resolve_lock.ResolveBusy as busy:
        print(busy.render(), file=sys.stderr)
        sys.exit(REFUSAL_EXIT_CODE)
    except safe_zone_guide.SafeZoneGuideError as exc:
        print(f"safe-zones: {exc}", file=sys.stderr)
        sys.exit(2)


def cmd_deliver_reel(args):
    """Render ONE chosen reel timeline to a video file. The explicit verb.

    Nothing in the pipeline calls this: not `build-reels`, not `run`,
    not the DAGs, not the operation registry. The captain invokes it by
    hand with a reel number, because a timeline build is cheap and is
    the thing they judge while a render is the expensive thing they
    must ask for (standing ruling 2026-09-09 / 2026-09-10).

    The delivery preset (`pipeline.deliver_preset`) and file naming
    (`pipeline.deliver_naming`) are the project's own declarations.
    When they are absent the verb derives the frame from the declared
    delivery format and the container/codec/filename from the
    mechanism's fallback, and REPORTS both halves as needing the
    captain's word rather than defaulting them. See
    library/tools/reel_deliver.py.
    """
    from library.tools import reel_deliver

    project_folder = _reel_project_folder(args.project)
    try:
        full = reel_deliver.deliver_reel(
            project_folder,
            args.reel,
            output_dir=args.output_dir or "",
            file_name=args.name or "",
            timeout_seconds=args.timeout,
        )
    except reel_deliver.DeliverRefused as refused:
        print(refused.render(), file=sys.stderr)
        sys.exit(REFUSAL_EXIT_CODE)

    verification = full["verification"]
    print(f"Delivered {full['timeline_name']!r} -> "
          f"{verification['output_path']}")
    print(f"  duration:   {verification['duration']['detail']} "
          f"({'PASS' if verification['duration']['passed'] else 'FAIL'})")
    print(f"  resolution: {verification['resolution']['detail']} "
          f"({'PASS' if verification['resolution']['passed'] else 'FAIL'})")
    print(f"  audio:      {verification['audio']['detail']} "
          f"({'PASS' if verification['audio']['passed'] else 'FAIL'})")
    print(f"  content:    "
          f"{'picture present' if verification['content_present'] else 'NO PICTURE - black throughout'} "
          f"({'PASS' if verification['content_present'] else 'FAIL'})")
    if verification["opens_on_black"]:
        print("  note: the reel OPENS ON BLACK (head segment at 0s) - "
              "reported, not failed; see the TV-switch-on ruling in "
              "library/tools/reel_deliver.py.")
    for half in full["settings"]["needs_captain_word"]:
        print(f"  note: pipeline.{half} is UNDECLARED - rendered with "
              f"the mechanism fallback and this needs the captain's "
              f"word before it becomes a standard.")
    print(f"  report: {full['report_path']}")


def cmd_watch_reel(args):
    """Show a model the PICTURE of a delivered reel and ask what it sees.

    The reels process is `build_reels` then `verify_reels`, and both
    halves are structural: the conformance verifier grades format, item
    count, picture holes, audio holes and caption timing, and
    `reel_quality_bar` judges from the transcript. Nothing on that path
    had ever opened a frame. This verb does, and it asks the questions a
    frame answers that a structure cannot - is a graphic clipped, is
    text unreadable, is a shot of nothing.

    It REPORTS. It fails no build and refuses no render: the structural
    verifier is correct and stays the gate, and a gate that refuses on a
    model's opinion is a new failure mode (AGENTS.md 10.4).

    It RENDERS NOTHING. It reads the file `deliver-reel` already wrote,
    so watching costs what the captain already paid and not a second
    time. A reel that has not been delivered is refused with the verb
    that would deliver it.

    See library/tools/render_watch.py.
    """
    from library.tools import render_watch

    project_folder = _reel_project_folder(args.project)
    try:
        if args.video:
            video_path = os.path.abspath(args.video)
            if not os.path.isfile(video_path):
                raise render_watch.NotDelivered(
                    f"no file at {video_path}",
                    "watching is of the PICTURE, and there is no file "
                    "here",
                    "point --video at a rendered file, or deliver the "
                    "reel first (`ren deliver <project> <reel>`)") \
                    from None
            subject = f"the rendered file {os.path.basename(video_path)}"
        else:
            row = render_watch.delivered_reel(project_folder, args.reel)
            video_path = row["video_path"]
            subject = (f"Reel {int(row['reel']):02d}"
                       + (f" ({row['timeline_name']})"
                          if row["timeline_name"] else ""))

        frames_dir, record_path = render_watch.watch_paths(
            project_folder, video_path)

        if args.record:
            with open(args.record, encoding="utf-8") as handle:
                answer = json.load(handle)
            record = render_watch.record_answer(record_path, answer)
            print(f"Filed the watch answer onto {record_path}")
            print(f"  strips:   {record['strips']}")
            print(f"  gates:    no - this is a report, not a gate")
            return

        watch = render_watch.watch_video(
            video_path, frames_dir, record_path, subject)
    except (render_watch.NotDelivered,
            render_watch.NothingWasWatched) as refused:
        print(refused.render(), file=sys.stderr)
        sys.exit(REFUSAL_EXIT_CODE)

    record = watch["record"]
    print(f"Watching {subject}")
    print(f"  file:     {video_path}")
    print(f"  strips:   {record['strips']} covering "
          f"{record['duration']:.3f}s at "
          f"{record['seconds_unseen_between_samples']:g}s resolution")
    print(f"  frames:   {record['frames_directory']}")
    print(f"  record:   {watch['record_path']}")
    print(f"  prompt:   {record['block_path']}")
    if record["spans_not_drawn"]:
        print(f"  NOT DRAWN: {', '.join(record['spans_not_drawn'])}")
    print()
    print(watch["block"])
    print("Open the strips, answer the questions above, and file the "
          "answer with:")
    print(f"  manage_project.py watch-reel {args.project} "
          f"{args.reel if args.reel is not None else ''} "
          f"--record <answers.json>".replace("  ", " "))


def cmd_hear_reel(args):
    """Hear a delivered reel against its own plan, and report what diverged.

    The audio twin of `watch-reel`. `render_qa` measures the render
    against itself, `manifest_validator` checks the plan against itself,
    and nothing compared what the render is HEARD to say against what
    the plan says it says - which is the exact shape of a caption card
    sitting a second behind its own audio in a delivered file.

    It REPORTS. It fails no build and refuses no render: no gate reads
    the record it writes, and promoting any of these checks to a gate is
    the captain's call (`library/tools/reel_hearing.GATES`).

    It RENDERS NOTHING and it re-transcribes nothing upstream. It reads
    the file `deliver-reel` already wrote, the timeline `build_reels`
    already serialized, and the transcript the pipeline already made.

    See library/tools/reel_hearing.py, and
    library/skills/hear_the_reel/ for the on-demand half.
    """
    from library.skills.hear_the_reel import skill as hear
    from library.tools import reel_hearing, render_watch

    project_folder = _reel_project_folder(args.project)

    if args.all:
        rows = render_watch.delivered_reels(project_folder)
        if not rows:
            raise RenRefusal(
                "this project has no delivered reel to hear",
                "a reel becomes a file when you run deliver-reel",
                f"run `ren deliver {args.project} <reel>` first, then "
                f"hear it") from None
        targets = [{"video": row["video_path"], "reel": None}
                   for row in rows]
    else:
        targets = [{"video": args.video, "reel": args.reel}]

    heard_any = False
    for target in targets:
        observation = hear.run(
            project_folder, args.step_id, reel=target["reel"],
            video_path=target["video"], timeline_path=args.timeline,
            announce=args.announce, dials=hear.dials_from(args),
            decline=args.decline_check)
        if not observation["available"]:
            print(f"REFUSED: {observation['reason']}", file=sys.stderr)
            continue
        heard_any = True
        print("\n".join(observation["summary"]))
        print(f"  record:   {observation['record']}")
        if observation.get("announced"):
            for fired in observation["announced"]:
                print(f"  announced {fired['hook']}: {fired['outcome']} "
                      f"- {fired['detail']}")
        print()
    if not heard_any:
        sys.exit(1)


def cmd_touch_reel(args):
    """Apply a structured change to a built reel's existing timeline.

    The verb `composed_edit` was built for: instead of staging a
    fresh timeline and promoting it over the old one (`build-reels`),
    this duplicates the reel's own timeline into a staging copy,
    routes the change through `composed_edit.apply_composed_edit`,
    verifies by re-reading the track, and promotes by rename.
    The approved timeline is never edited directly.

    The change is stated structurally as JSON - which reel, which
    item, what changes - via `--edits` or `--edits-file`.  Mapping a
    captain's natural-language note onto such a change is a separate
    task; this verb is the mechanism it will call.  See
    library/tools/reel_touchup.py for the five ops and the
    qualification gate that refuses what it cannot classify.
    """
    from library.tools import reel_touchup as _touchup
    from library.tools import run_control as _hold

    project_folder = _reel_project_folder(args.project)
    hold = _hold.hold_requested(project_folder)
    if hold is not None:
        raise RenRefusal(
            f"the handbrake is engaged on this project "
            f"({hold.get('requested_by', 'unknown')}: "
            f"{hold.get('reason', '')})",
            "a touch-up under a held project could fight the holder",
            "delete pipeline.hold at the project root (it records who "
            "asked for it - check with them), then re-run `ren touch`") \
            from None

    if args.all_reels:
        if not args.new_media:
            print("Error: --all-reels swaps a NAMED clip on every reel - "
                  "pass --old-clip and --new-media (no --edits: each "
                  "reel's position is located, not stated).",
                  file=sys.stderr)
            sys.exit(2)
        try:
            summary = _touchup.touchup_all_reels(
                project_folder,
                old_clip=args.old_clip,
                new_media=args.new_media,
                row=args.row or "",
                allow_drops=args.allow_drop or None,
                supersede=args.supersede or None)
        except _touchup.TouchupRefused as refused:
            print(refused.render(), file=sys.stderr)
            sys.exit(REFUSAL_EXIT_CODE)
        except _touchup.TouchupError as failed:
            print(f"FAILED: {failed}", file=sys.stderr)
            sys.exit(1)
        for entry in summary.get("reels") or ():
            if entry.get("ok"):
                print(f"Touched {entry['final']!r} "
                      f"(reel {entry['reel']})")
            else:
                print(f"Reel {entry['reel']} "
                      f"({entry.get('final') or 'unnamed'}): REFUSED - "
                      f"{entry.get('refused')}")
        print(f"touched {summary.get('landed', 0)}/"
              f"{len(summary.get('reels') or ())} reel(s)")
        sys.exit(0 if summary.get("ok") else 1)

    if args.reel is None:
        print("Error: pass the reel number, or --all-reels for every "
              "reel.", file=sys.stderr)
        sys.exit(2)

    if args.edits_file:
        try:
            with open(args.edits_file, encoding="utf-8") as handle:
                edits = json.load(handle)
        except (OSError, ValueError) as bad:
            print(f"Error: cannot read edits file {args.edits_file}: "
                  f"{bad}", file=sys.stderr)
            sys.exit(2)
    elif args.edits:
        try:
            edits = json.loads(args.edits)
        except ValueError as bad:
            print(f"Error: --edits is not JSON: {bad}", file=sys.stderr)
            sys.exit(2)
    else:
        print("Error: pass the change with --edits JSON or "
              "--edits-file PATH.", file=sys.stderr)
        sys.exit(2)
    if isinstance(edits, dict) and "edits" not in edits:
        edits = {"edits": [edits]}
    spec = {"reel": args.reel, "edits": edits["edits"]
            if isinstance(edits, dict) else edits}

    try:
        receipt = _touchup.apply_touchup(
            project_folder, spec,
            allow_drops=args.allow_drop or None,
            supersede=args.supersede or None)
    except _touchup.TouchupRefused as refused:
        print(refused.render(), file=sys.stderr)
        sys.exit(REFUSAL_EXIT_CODE)
    except _touchup.TouchupError as failed:
        print(f"FAILED: {failed}", file=sys.stderr)
        sys.exit(1)

    gate = receipt.get("gate", {})
    print(f"Touched {receipt['final']!r} "
          f"({gate.get('class', 'unqualified')})")
    print(f"  cost: {gate.get('cost', '')}")
    for note in gate.get("notes", ()):
        print(f"  - {note}")
    composed = receipt.get("composed", {})
    print(f"  staged+composed+verified in "
          f"{receipt.get('seconds', '?')}s wall clock "
          f"(stage {receipt.get('stage_seconds', '?')}s, composed "
          f"{receipt.get('composed_seconds', '?')}s, verify "
          f"{receipt.get('verify_seconds', '?')}s)")
    print(f"  placed {composed.get('placed', {}).get('asked', '?')} "
          f"item(s), verified {composed.get('verified', {}).get('landed', '?')}")
    print(f"  verification read: {json.dumps(receipt.get('verification_read', {}))}")
    if receipt.get("retirement"):
        print(f"  {receipt['retirement']}")


def cmd_undo(args):
    """Reverse the newest Ren act on a reel: a touch IN PLACE, a rebuild by version.

    A touch-up is reversed on the same timeline from its undo journal
    and verified by re-reading it; one the live timeline has moved on
    from since is refused by name. A rebuild is rolled back to the
    version before it: that version's plan is restored and the reel is
    rebuilt. `--list` prints what would be undone, newest first. See
    library/tools/undo_journal.py.
    """
    from library.tools import run_control as _hold
    from library.tools import undo_journal as _undo

    project_folder = _reel_project_folder(args.project)
    final = ""
    if args.reel is not None:
        from library.tools.reel_touchup import (
            TouchupRefused,
            resolve_final_name,
        )
        try:
            final = resolve_final_name(project_folder, args.reel)
        except TouchupRefused as refused:
            print(refused.render(), file=sys.stderr)
            sys.exit(REFUSAL_EXIT_CODE)
    if args.list:
        print(_undo.render_stack(_undo.undo_stack(project_folder, final)))
        return
    hold = _hold.hold_requested(project_folder)
    if hold is not None:
        raise RenRefusal(
            f"the handbrake is engaged on this project "
            f"({hold.get('requested_by', 'unknown')}: "
            f"{hold.get('reason', '')})",
            "an undo under a held project could fight the holder",
            "delete pipeline.hold at the project root (it records who "
            "asked for it - check with them), then re-run `ren undo`") \
            from None
    try:
        receipts = _undo.undo(project_folder, final=final,
                              entry_id=args.entry,
                              supersede=args.supersede or ())
    except _undo.UndoRefused as refused:
        print(refused.render(), file=sys.stderr)
        sys.exit(REFUSAL_EXIT_CODE)
    except (_undo.UndoNotVerified, _undo.RollbackDiverged) as failed:
        print(f"FAILED: {failed}", file=sys.stderr)
        sys.exit(1)
    for receipt in receipts:
        if "entry" in receipt:
            print(f"Undid touch {receipt['entry']} in place - verified "
                  f"against the journal (version {receipt['version']}).")
            uncarried = receipt.get("markers", {}).get("uncarried") or []
            for note in uncarried:
                print(f"  note not carried: {note!r}")
        else:
            print(f"Rolled {receipt['final']!r} back from version "
                  f"{receipt['rolled_back']} to version {receipt['to']}"
                  f"{'; re-applied ' + ', '.join(receipt['replayed']) if receipt['replayed'] else ''}.")


def cmd_ren_dry_run(args):
    """Dry-run the composed edit path for the Reel 26 ending swap.

    Part one of two, and it ends at the dry run: compose the reel goal,
    read the live timeline, qualify the touchup, print EXACTLY what it
    WOULD execute plus what the old path would have executed instead,
    and stop. Never executes.

    Owns no logic: every line of the join lives in
    `library/tools/ren_dry_run.py`, which wires the composer, the
    oracle and the touchup gate without reimplementing any of them.
    """
    from library.tools import ren_dry_run as _ren

    argv = [args.project,
            "--reel", str(args.reel),
            "--row", args.row,
            "--old-clip", args.old_clip,
            "--new-media", args.new_media,
            "--timeline", args.timeline,
            "--tracks-file", args.tracks_file,
            "--expected-rows", args.expected_rows,
            "--out", args.out]
    if args.all_reels:
        argv.append("--all-reels")
    if args.tracks_dir:
        argv.extend(["--tracks-dir", args.tracks_dir])
    sys.exit(_ren.main(argv))


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
        raise RenRefusal(
            f"{unknown}",
            "no project with this slug is under PIPELINE_PROJECTS_ROOT",
            "run `ren projects` to list them, or pass the project's "
            "absolute path instead of the slug") from unknown


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
    p_new.add_argument("--template", help="Brand template name the project's own brand.json carries (default: none)")
    p_new.add_argument("--fps", help="Frame rate (default: 30)")
    p_new.add_argument("--resolve-name", help="DaVinci Resolve project name")
    p_new.add_argument("--tags", help="Comma-separated tags")
    p_new.add_argument("--description", help="Project description")
    p_new.add_argument("--shape", choices=["speech", "music", "both", "picture-led"],
                       help="What the video is built from (default: undecided)")
    p_new.add_argument("--language", help="Language spoken in the footage, BCP-47 (default: en)")
    p_new.add_argument("--speaker", action="append", default=[],
                       help="A speaker as Name or Name:role; repeatable. "
                            "Omitted means undecided; --no-speakers declares zero.")
    p_new.add_argument("--no-speakers", action="store_true",
                       help="Declare the project has no voices (music / montage)")
    p_new.add_argument("--brief-title", help="Title line for the scaffolded brief.md (default: project name)")
    p_new.add_argument("--brand-series", help="Series id for the scaffolded brand.json (default: slug)")
    p_new.add_argument("--non-interactive", action="store_true",
                       help="Never prompt; missing answers stay undeclared. "
                            "Implied when stdin is not a terminal.")
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
    from library.processes.reels import run_reels
    run_reels.add_arguments(build_reels_parser)
    build_reels_parser.set_defaults(func=cmd_build_reels)

    drift_parser = sub.add_parser(
        "drift", help="Compare each reel's build snapshot against its "
                      "live timeline and report the per-reel factor")
    drift_parser.add_argument(
        "project", help="Project slug, or an absolute path "
                        "to the project directory")
    drift_parser.set_defaults(func=cmd_drift)

    post_header_parser = sub.add_parser(
        "post-header", help="Put each reel's social-post header on its "
                            "existing final timeline, as a journaled touch")
    post_header_parser.add_argument(
        "project", help="Project slug, or an absolute path "
                        "to the project directory")
    post_header_parser.add_argument(
        "reel", type=int, nargs="*",
        help="Approved reel numbers; none means every approved reel")
    post_header_parser.add_argument(
        "--draw-gain", type=float, required=True,
        help="What the renderer draws per Pan/Tilt unit on these "
             "timelines, as MEASURED (resolve_transform); the header is "
             "placed with it")
    post_header_parser.add_argument(
        "--keep-graphics", action="store_true",
        help="Leave the Semantic-row graphics on (default: switch them off)")
    post_header_parser.add_argument(
        "--supersede", action="append", default=[], metavar="REEL",
        help="A signed-off reel this touch may replace. Repeatable")
    post_header_parser.add_argument(
        "--dry-run", action="store_true",
        help="Read the reels and print each change; write nothing")
    post_header_parser.set_defaults(func=cmd_post_header)

    shift_rows_parser = sub.add_parser(
        "shift-rows", help="Move named rows of each built reel up or down "
                           "by delivery pixels, as a journaled touch")
    shift_rows_parser.add_argument(
        "project", help="Project slug, or an absolute path "
                        "to the project directory")
    shift_rows_parser.add_argument(
        "reel", type=int, nargs="*",
        help="Approved reel numbers; none means every approved reel")
    shift_rows_parser.add_argument(
        "--move", action="append", default=[], required=True,
        metavar="ROW[,ROW]=PIXELS",
        help="Rows by name and pixels to move them; positive is DOWN. "
             "Repeatable")
    shift_rows_parser.add_argument(
        "--draw-gain", type=float, required=True,
        help="What the renderer draws per Tilt unit on these timelines, "
             "as MEASURED (no default: a guessed gain moves the wrong "
             "number of pixels)")
    shift_rows_parser.add_argument(
        "--skip", action="append", default=[], metavar="PREFIX",
        help="Leave items whose name starts with this where they are")
    shift_rows_parser.add_argument(
        "--supersede", action="append", default=[], metavar="REEL",
        help="A signed-off reel this touch may replace. Repeatable")
    shift_rows_parser.add_argument(
        "--dry-run", action="store_true",
        help="Read the reels and print each change; write nothing")
    shift_rows_parser.set_defaults(func=cmd_shift_rows)

    scale_rows_parser = sub.add_parser(
        "scale-rows", help="Shrink or grow named rows of each built reel "
                           "about a point, as a journaled touch")
    scale_rows_parser.add_argument(
        "project", help="Project slug, or an absolute path "
                        "to the project directory")
    scale_rows_parser.add_argument(
        "reel", type=int, nargs="*",
        help="Approved reel numbers; none means every approved reel")
    scale_rows_parser.add_argument(
        "--rows", required=True, metavar="ROW[,ROW]",
        help="Rows to scale, by name; @picture is the camera rows under "
             "the TV frame (to scale the frame too, use fit-picture)")
    scale_rows_parser.add_argument(
        "--move-with", default="", metavar="ROW[,ROW]",
        help="Rows riding on the picture: moved with it, not resized")
    scale_rows_parser.add_argument(
        "--by", type=float, default=None, metavar="FACTOR",
        help="The scale, below 1 to shrink")
    scale_rows_parser.add_argument(
        "--fit", action="store_true",
        help="The scale the project's safe-zone policy needs for its "
             "TV-frame picture")
    scale_rows_parser.add_argument(
        "--about", default=None, metavar="X,Y",
        help="The point to scale about, in delivery pixels; default the "
             "TV frame's centre")
    scale_rows_parser.add_argument(
        "--draw-gain", type=float, required=True,
        help="What the renderer draws per Pan/Tilt unit on these "
             "timelines, as MEASURED")
    scale_rows_parser.add_argument(
        "--skip", action="append", default=[], metavar="PREFIX",
        help="Leave items whose name starts with this as they are")
    scale_rows_parser.add_argument(
        "--supersede", action="append", default=[], metavar="REEL",
        help="A signed-off reel this touch may replace. Repeatable")
    scale_rows_parser.add_argument(
        "--dry-run", action="store_true",
        help="Read the reels and print each change; write nothing")
    scale_rows_parser.set_defaults(func=cmd_scale_rows)

    fit_picture_parser = sub.add_parser(
        "fit-picture", help="Put each built reel's TV picture at a scale "
                            "no phone crops, as a journaled touch")
    fit_picture_parser.add_argument(
        "project", help="Project slug, or an absolute path "
                        "to the project directory")
    fit_picture_parser.add_argument(
        "reel", type=int, nargs="*",
        help="Approved reel numbers; none means every approved reel")
    fit_picture_parser.add_argument(
        "--scale", type=float, default=None, metavar="S",
        help="The TV frame's scale, as pipeline.tv_frame.scale states it")
    fit_picture_parser.add_argument(
        "--fit", action="store_true",
        help="The scale the project's safe-zone policy needs")
    fit_picture_parser.add_argument(
        "--move-with", default="", metavar="ROW[,ROW]",
        help="Rows riding on the picture: moved with it, not resized")
    fit_picture_parser.add_argument(
        "--draw-gain", type=float, required=True,
        help="What the renderer draws per Pan/Tilt unit on these "
             "timelines, as MEASURED")
    fit_picture_parser.add_argument(
        "--skip", action="append", default=[], metavar="PREFIX",
        help="Leave items whose name starts with this as they are")
    fit_picture_parser.add_argument(
        "--supersede", action="append", default=[], metavar="REEL",
        help="A signed-off reel this touch may replace. Repeatable")
    fit_picture_parser.add_argument(
        "--dry-run", action="store_true",
        help="Read the reels and print each change; write nothing")
    fit_picture_parser.set_defaults(func=cmd_fit_picture)

    caption_width_parser = sub.add_parser(
        "caption-width", help="Narrow each built reel's captions to the "
                              "width the platforms leave clear, as a "
                              "journaled touch")
    caption_width_parser.add_argument(
        "project", help="Project slug, or an absolute path "
                        "to the project directory")
    caption_width_parser.add_argument(
        "reel", type=int, nargs="*",
        help="Approved reel numbers; none means every approved reel")
    caption_width_parser.add_argument(
        "--draw-gain", type=float, required=True,
        help="What the renderer draws per Tilt unit on these timelines, "
             "as MEASURED - it says which rows the captions draw on")
    caption_width_parser.add_argument(
        "--max-width", type=int, default=None, metavar="PX",
        help="The wrap width; default: the widest centred box clear of "
             "every platform's safe zones over the captions' rows")
    caption_width_parser.add_argument(
        "--supersede", action="append", default=[], metavar="REEL",
        help="A signed-off reel this touch may replace. Repeatable")
    caption_width_parser.add_argument(
        "--dry-run", action="store_true",
        help="Render the narrowed cards and print each change; touch no "
             "timeline")
    caption_width_parser.set_defaults(func=cmd_caption_width)

    safe_zones_parser = sub.add_parser(
        "safe-zones", help="Place a platform safe-zone guide on reel "
                           "timelines, switched off so it never renders")
    safe_zones_parser.add_argument(
        "project", help="Project slug, or an absolute path "
                        "to the project directory")
    safe_zones_parser.add_argument(
        "reel", type=int, nargs="*",
        help="Approved reel numbers; none means every approved reel")
    from library.tools.platform_safe_zones import OVERLAY_NAMES
    from library.tools.safe_zone_policy import PROJECT_OVERLAY
    safe_zones_parser.add_argument(
        "--overlay", choices=(PROJECT_OVERLAY,) + OVERLAY_NAMES,
        default=PROJECT_OVERLAY,
        help="The project's own policy (default: pipeline.safe_zones, "
             "or every platform on every phone), one platform's guide, "
             "or all four combined")
    safe_zones_parser.add_argument(
        "--describe", action="store_true",
        help="Print the project's safe-zone policy and what it resolves "
             "to; touch nothing")
    safe_zones_parser.add_argument(
        "--remove", action="store_true",
        help="Take the guide row away instead of placing one")
    safe_zones_parser.set_defaults(func=cmd_safe_zones)

    deliver_reel_parser = sub.add_parser(
        "deliver-reel", help="Render ONE chosen reel timeline to a file")
    deliver_reel_parser.add_argument(
        "project", help="Project slug, or an absolute path "
                        "to the project directory")
    deliver_reel_parser.add_argument(
        "reel", type=int,
        help="The approved reel number to render. Required: rendering "
             "without naming one is refused, because that is the "
             "unasked render the standing ruling forbids")
    deliver_reel_parser.add_argument(
        "--output-dir", default="",
        help="Where to write the file. Default: the project's exports/")
    deliver_reel_parser.add_argument(
        "--name", default="",
        help="The output filename. Default: the project's "
             "pipeline.deliver_naming, or {timeline}.{ext}")
    deliver_reel_parser.add_argument(
        "--timeout", type=int, default=1800,
        help="Seconds to wait for Resolve to finish (default: 1800)")
    deliver_reel_parser.set_defaults(func=cmd_deliver_reel)

    watch_reel_parser = sub.add_parser(
        "watch-reel",
        help="Show a model the PICTURE of a delivered reel and ask what "
             "it sees. Renders nothing")
    watch_reel_parser.add_argument(
        "project", help="Project slug, or an absolute path "
                        "to the project directory")
    watch_reel_parser.add_argument(
        "reel", type=int, nargs="?", default=None,
        help="The reel number to watch. It must already have been "
             "rendered with deliver-reel: this verb reads that file and "
             "never starts a render")
    watch_reel_parser.add_argument(
        "--video", default="",
        help="Watch this file instead of looking a reel up by number")
    watch_reel_parser.add_argument(
        "--record", default="",
        help="A JSON file holding the answer to the questions this verb "
             "asked. Files it onto the watch record beside the video. "
             "The answer is REPORTED, never gated")
    watch_reel_parser.set_defaults(func=cmd_watch_reel)

    hear_reel_parser = sub.add_parser(
        "hear-reel",
        help="Hear a delivered reel against its plan and report what "
             "diverged. Renders nothing, gates nothing")
    hear_reel_parser.add_argument(
        "project", help="Project slug, or an absolute path "
                        "to the project directory")
    hear_reel_parser.add_argument(
        "reel", nargs="?", default=None,
        help="The reel number or timeline name to hear. It must already "
             "have been rendered with deliver-reel: this verb reads that "
             "file and never starts a render")
    hear_reel_parser.add_argument(
        "--all", action="store_true",
        help="Hear every delivered reel in the project - the batch pass, "
             "over the same core as one reel")
    hear_reel_parser.add_argument(
        "--video", default="",
        help="Hear this file instead of looking a reel up by number")
    hear_reel_parser.add_argument(
        "--timeline", default="",
        help="The serialized plan, when the render carries no "
             ".deliver.json naming its timeline")
    hear_reel_parser.add_argument(
        "--step-id", default="verify_reels",
        help="Which step this hearing is recorded against, for the "
             "skill receipt (default: verify_reels)")
    hear_reel_parser.add_argument(
        "--announce", action="store_true",
        help="Raise each finding on the project's hook layer, which is "
             "how one reaches the review channel. Nothing fires unless "
             "the project declares a hook for it")
    # The dials are registered FROM `hearing_settings.DIALS` rather than
    # listed here, so both CLIs offer exactly what the enumeration
    # carries and a new dial cannot reach one surface and not the other.
    from library.skills.hear_the_reel import skill as _hear_skill
    _hear_skill.add_dial_arguments(hear_reel_parser)
    hear_reel_parser.set_defaults(func=cmd_hear_reel)

    touch_reel_parser = sub.add_parser(
        "touch-reel",
        help="Apply a structured change to a built reel's existing "
             "timeline through composed_edit, staged and verified")
    touch_reel_parser.add_argument(
        "project", help="Project slug, or an absolute path "
                        "to the project directory")
    touch_reel_parser.add_argument(
        "reel", type=int, nargs="?", default=None,
        help="The built reel number to change. Required unless "
             "--all-reels: an edit without naming one is refused")
    touch_reel_parser.add_argument(
        "--edits", default="",
        help="The change as JSON: a list of edit objects (move, "
             "swap_pixels, add_overlay, remove_overlay, retime) or "
             "an object holding one under 'edits'. See "
             "library/tools/reel_touchup.py")
    touch_reel_parser.add_argument(
        "--edits-file", default="",
        help="Read the change JSON from this file instead of --edits")
    touch_reel_parser.add_argument(
        "--allow-drop", dest="allow_drop", action="append", default=[],
        metavar="SPEC",
        help="A row the replace guard may let shrink, by ROW never by "
             "blanket (issue #925): `ROW` or `FINAL::ROW`. Repeatable")
    touch_reel_parser.add_argument(
        "--supersede", dest="supersede", action="append", default=[],
        metavar="REEL",
        help="A reel whose durable captain SIGN-OFF this touch-up may "
             "replace. Repeatable. Absent means a touch-up over a "
             "signed-off reel refuses by name")
    touch_reel_parser.add_argument(
        "--all-reels", action="store_true",
        help="Swap the same NAMED clip on every reel the plan names "
             "(--old-clip/--new-media, with --row as an optional "
             "narrowing) instead of applying --edits to one reel. "
             "One reel's refusal never stops the rest.")
    touch_reel_parser.add_argument(
        "--old-clip", default="",
        help="With --all-reels: the clip name to swap out on each "
        "reel (located across the video rows, not stated by "
        "position)")
    touch_reel_parser.add_argument(
        "--new-media", default="",
        help="With --all-reels: the replacement file's path on disk. "
             "Required: a touchup never renders media")
    touch_reel_parser.add_argument(
        "--row", default="",
        help="With --all-reels: narrow the clip search to this row "
             "(default: search every video row)")
    touch_reel_parser.set_defaults(func=cmd_touch_reel)

    undo_parser = sub.add_parser(
        "undo",
        help="Reverse the newest Ren act: a touch-up in place from its "
             "journal, a rebuild by rolling back to the version before it")
    undo_parser.add_argument(
        "project", help="Project slug, or an absolute path "
                        "to the project directory")
    undo_parser.add_argument(
        "reel", type=int, nargs="?", default=None,
        help="Undo the newest act on this reel. Absent: the newest act "
             "on any reel (an --all-reels touch-up is undone whole)")
    undo_parser.add_argument(
        "--entry", default="",
        help="Undo this journal entry, by id; refused unless it is its "
             "reel's newest act")
    undo_parser.add_argument(
        "--list", action="store_true",
        help="Print what would be undone, newest first, and stop")
    undo_parser.add_argument(
        "--supersede", dest="supersede", action="append", default=[],
        metavar="REEL",
        help="For a rebuild rollback over a signed-off reel: the reel "
             "whose sign-off the rebuild may replace. Repeatable")
    undo_parser.set_defaults(func=cmd_undo)

    ren_dry_run_parser = sub.add_parser(
        "ren-dry-run",
        help="Dry-run the composed edit path (plan, read live, print, "
             "stop - never executes)")
    ren_dry_run_parser.add_argument(
        "project", help="Project slug, or an absolute path "
                        "to the project directory")
    ren_dry_run_parser.add_argument(
        "--reel", type=int, default=26,
        help="The built reel number to change (default 26)")
    ren_dry_run_parser.add_argument(
        "--row", default="",
        help="Narrow the clip search to this overlay row "
             "(default: search every video row)")
    ren_dry_run_parser.add_argument(
        "--old-clip", default="",
        help="The clip name on the live timeline to swap out. "
        "Required: the engine states no clip of its own.")
    ren_dry_run_parser.add_argument(
        "--new-media", default="",
        help="The replacement file's path on disk. Required: a touchup "
             "never renders media")
    ren_dry_run_parser.add_argument(
        "--timeline", default="",
        help="The timeline's EXACT name (default: the plan's approved "
             "name for --reel)")
    ren_dry_run_parser.add_argument(
        "--tracks-file", default="",
        help="JSON track read INSTEAD of reading Resolve "
             "(demonstration without a running Resolve)")
    ren_dry_run_parser.add_argument(
        "--expected-rows", default="",
        help="JSON file carrying the expected rows instead of the "
             "newest build snapshot")
    ren_dry_run_parser.add_argument(
        "--out", default="",
        help="Write the JSON record here as well")
    ren_dry_run_parser.add_argument(
        "--all-reels", action="store_true",
        help="Dry-run the same swap on every reel the plan names, "
             "reporting per reel - one reel's refusal never stops "
             "the rest. Still never executes.")
    ren_dry_run_parser.add_argument(
        "--tracks-dir", default="",
        help="Directory of per-reel JSON track reads named "
             "<REEL>.json for an --all-reels run without Resolve")
    ren_dry_run_parser.set_defaults(func=cmd_ren_dry_run)

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
    p_rorg.add_argument("--remove-proof", metavar="TIMELINE", default=None,
                         help="Delete a captain-authorised demo timeline "
                              "(firstmate proof or exact authorised demo "
                              "name) - or one of the two captain-authorised "
                              "superseded Reel 09 timelines - with its "
                              "caption bins. IRREVERSIBLE; "
                              "refuses protected timelines. Each bin must "
                              "be passed explicitly with --proof-bin")
    p_rorg.add_argument("--proof-bin", metavar="BIN", action="append",
                         default=[],
                         help="A caption bin the proof timeline created, "
                              "as shown by --check's census or by running "
                              "with --remove-proof and no --proof-bin "
                              "(repeatable)")
    p_rorg.add_argument("--timeline-only", action="store_true",
                         help="With --remove-proof: the timeline created "
                              "no bins, so remove it alone. Refused when "
                              "any candidate bin exists")
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

    p_take_pick = sub.add_parser(
        "take-pick-preview",
        help="Print one reel's candidate takes, trim freshness, "
             "snap deltas and boundary words in a single read")
    p_take_pick.add_argument("slug", metavar="PROJECT",
                             help="Project slug, or an absolute path")
    p_take_pick.add_argument("--reel", required=True, metavar="REEL",
                             help="Reel number, slug or full timeline "
                                  "name (e.g. 21, lucy-origin, "
                                  "'Reel 21 - lucy-origin')")
    p_take_pick.add_argument("--threshold", type=float, default=None,
                             help="Snap moves over this many seconds "
                                  "need a decision (default 2.0)")
    p_take_pick.add_argument("--json", action="store_true",
                             help="Print the report as JSON instead of "
                                  "text")
    p_take_pick.set_defaults(func=cmd_take_pick_preview)

    p_round = sub.add_parser(
        "round-diff",
        help="What changed between two rounds of the captain's feedback")
    p_round.add_argument("slug", metavar="PROJECT",
                         help="Project slug, or an absolute path")
    p_round.add_argument("--from", dest="earlier", type=int, default=None,
                         help="The earlier round. Default: the "
                              "second-to-last round that promoted a reel")
    p_round.add_argument("--to", dest="later", type=int, default=None,
                         help="The later round. Default: the last round "
                              "that promoted a reel")
    p_round.add_argument("--backfill", action="store_true",
                         help="reconstruct the rounds already in this "
                              "project's git history first, from the "
                              "committed timeline snapshots")
    p_round.add_argument("--list", dest="list_rounds", action="store_true",
                         help="list the rounds instead of diffing")
    p_round.set_defaults(func=cmd_round_diff)

    p_pr_body = sub.add_parser(
        "pr-body",
        help="Generate the PR-body enumeration for a ledger change "
             "(read only)")
    p_pr_body.add_argument("slug", metavar="PROJECT",
                           help="Project slug, or an absolute path")
    p_pr_body.add_argument("--reel", action="append", default=[],
                           metavar="PREFIX",
                           help="Only rows scoped to this reel-name "
                                "prefix (plus unscoped rows, which hold "
                                "everywhere). Repeatable")
    p_pr_body.set_defaults(func=cmd_pr_body)

    p_signoff = sub.add_parser(
        "sign-off",
        help="Sign off a BUILT reel, so promotion must declare before "
             "replacing it")
    p_signoff.add_argument("slug", metavar="PROJECT",
                           help="Project slug, or an absolute path")
    p_signoff.add_argument("reel", nargs="?", default="", metavar="REEL",
                           help="The reel timeline name. Omitted, the "
                                "current sign-offs are listed")
    p_signoff.add_argument("--note", default="",
                           help="The captain's own words about why this "
                                "cut is approved")
    p_signoff.add_argument("--by", default="captain")
    p_signoff.add_argument("--withdraw", action="store_true",
                           help="withdraw the sign-off on this reel; the "
                                "withdrawal is recorded, never erased")
    p_signoff.set_defaults(func=cmd_sign_off)

    p_purge = sub.add_parser(
        "purge",
        help="Plan (default) or apply the lean-retention purge of "
             "superseded renders, quarantine, scratch and stale journals")
    p_purge.add_argument("slug", metavar="PROJECT",
                         help="Project slug or absolute path")
    p_purge.add_argument("--apply", default="", metavar="MANIFEST",
                         help="remove what this plan's manifest still lists")
    p_purge.add_argument("--db", action="append", default=[],
                         help="Resolve Project.db path (repeatable); read "
                              "through a copy. Default: the running Resolve's")
    p_purge.set_defaults(func=cmd_purge)

    p_discharge = sub.add_parser(
        "discharge-uncarried",
        help="Discharge a dropped captain's note a promotion filed, "
             "so its reel can be signed off")
    p_discharge.add_argument("slug", metavar="PROJECT",
                             help="Project slug, or an absolute path")
    p_discharge.add_argument("reel", nargs="?", default="", metavar="REEL",
                             help="The reel timeline name. Omitted, every "
                                  "open obligation is listed")
    p_discharge.add_argument("--identity", default="",
                             help="The obligation to discharge. Omitted, "
                                  "the reel's only open one is taken; a "
                                  "reel owing several must name one")
    p_discharge.add_argument("--note", default="",
                             help="REQUIRED to discharge: what happened to "
                                  "the captain's words. A discharge with "
                                  "no reason is refused")
    p_discharge.add_argument("--by", default="captain")
    p_discharge.set_defaults(func=cmd_discharge_uncarried)

    p_variant = sub.add_parser(
        "variant",
        help="Two versions of one reel, alive at once, compared, chosen")
    p_variant.add_argument("project",
                           help="Project slug, or an absolute path")
    v_sub = p_variant.add_subparsers(dest="variant_command", required=True)

    v_new = v_sub.add_parser(
        "new", help="Declare a variant of a reel and branch for it")
    v_new.add_argument("reel", type=int, metavar="N")
    v_new.add_argument("--suffix", required=True, metavar="' (NAME)'",
                       help="The trailing-parens suffix the variant's "
                            "timeline carries and its branch name "
                            "derives from, e.g. ' (reaction-cutaway)'")
    v_new.add_argument("--j-cut", dest="j_cut", default="",
                       metavar="JSON",
                       help='Seam offset, e.g. \'{"join_seconds": 12.4, '
                            '"lead_seconds": 0.5}\'')
    v_new.add_argument("--cutaway", default="", metavar="JSON",
                       help='Seam offset, e.g. \'{"hide_angle": "A", '
                            '"window_seconds": [12.4, 12.7]}\'')
    v_new.add_argument("--cover", default="", metavar="JSON",
                       help="A cover span that rides with a cutaway: "
                            "source_file, source_in, source_out")
    v_new.add_argument(
        "--declares", action="append", default=[], metavar="STORE",
        help="A per-project DECLARATION this variant differs in - the "
             "stores `external_inputs.DECLARATIONS` enumerates "
             "(reel_ending, caption_timing, overlay_intent, mix_intent, "
             "placed_assets). Repeatable. The differing value lives in "
             "external/<store>.json ON THE VARIANT'S BRANCH, never in "
             "the spec, so a declaring variant is built from that "
             "branch and refuses elsewhere.")
    v_new.add_argument("--watch", default="",
                       help="What the captain should look and listen for")

    v_build = v_sub.add_parser(
        "build", help="Build a reel's declared variants beside the reel")
    v_build.add_argument("reel", type=int, metavar="N")
    v_build.add_argument("--suffix", action="append", default=[],
                         metavar="' (NAME)'",
                         help="Build only this declared variant; "
                              "repeatable. Default: every variant this "
                              "reel declares on the current branch, in "
                              "one atomic batch.")

    v_list = v_sub.add_parser(
        "list", help="What is declared, what it differs in, what is built")
    del v_list

    v_diff = v_sub.add_parser(
        "diff", help="What differs between two built variants, off disk")
    v_diff.add_argument("reel", type=int, metavar="N")
    v_diff.add_argument("earlier", metavar="' (NAME-A)'")
    v_diff.add_argument("later", metavar="' (NAME-B)'")

    v_choose = v_sub.add_parser(
        "choose",
        help="Make one variant the reel; the other goes to the archive")
    v_choose.add_argument("reel", type=int, metavar="N")
    v_choose.add_argument("suffix", metavar="' (NAME)'",
                          help="The variant that won")
    v_choose.add_argument("--why", required=True,
                          help="Why this treatment won. Recorded in the "
                               "round; a choice with no reason is not a "
                               "decision anybody can read later.")
    v_choose.add_argument(
        "--supersede", action="append", default=[], metavar="REEL",
        help="A reel whose durable captain SIGN-OFF this choice may "
             "replace. The chosen variant takes that reel's name, so a "
             "sign-off on it refuses by name without this.")

    v_merge = v_sub.add_parser(
        "merge", help="Merge a variant branch's declarations back")
    v_merge.add_argument("branch", metavar="BRANCH")
    v_merge.add_argument("--target", default=None)

    p_variant.set_defaults(func=cmd_variant)

    p_relink = sub.add_parser("relink", help="Relink offline media in Resolve after migration")
    p_relink.add_argument("slug", nargs="?", default="", metavar="PROJECT", help="Project slug (optional). Unlike run/status/info, relink resolves the project by scanning PIPELINE_PROJECTS_ROOT, so a path is not accepted here")
    p_relink.add_argument("--scan", action="store_true", help="Scan only, don't relink")
    p_relink.set_defaults(func=cmd_relink)

    p_reindex = sub.add_parser(
        "reindex",
        help="Move a project's words onto Voz plus MFA: invalidate "
             "legacy temporal indexes, then re-transcribe them")
    p_reindex.add_argument(
        "slug", metavar="PROJECT",
        help="Project slug, or an absolute/relative path to the "
             "project directory (or its project.yaml) for projects "
             "that live outside PIPELINE_PROJECTS_ROOT")
    p_reindex.set_defaults(func=cmd_reindex)

    p_setup_hooks = sub.add_parser(
        "setup-hooks",
        help="Install the marker-feedback hook for a host (plan by default)")
    p_setup_hooks.add_argument("--app", default="",
                               help="claude-code|opencode|codex (--list shows targets)")
    p_setup_hooks.add_argument("--write", action="store_true",
                               help="install; without it, plan only")
    p_setup_hooks.add_argument("--list", action="store_true",
                               help="list supported hosts and stop")
    p_setup_hooks.set_defaults(func=cmd_setup_hooks)

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

    # ONE place renders every refusal on every verb's path: the shape
    # (`library/tools/ren_refusal.py`) carries what happened, why, and
    # the fix, and the exit code contract names 4 for it. An unexpected
    # error keeps its traceback and exit 1 - it is a bug, not a refusal.
    from library.tools.ren_refusal import REFUSAL_EXIT_CODE, RenRefusal
    try:
        args.func(args)
    except RenRefusal as refused:
        print(refused.render(), file=sys.stderr)
        sys.exit(REFUSAL_EXIT_CODE)


if __name__ == "__main__":
    main()
