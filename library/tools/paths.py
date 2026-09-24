"""
Centralized path configuration for the video editing pipeline.

This is the single source of truth for every filesystem path the pipeline
uses - repo directories, DaVinci Resolve application support paths, and
external asset libraries. All paths are computed relative to the repo root
or read from environment variables - loaded on import from the per-user
config file and the checkout's .env (see "Configuration" below).

Usage:
    from library.tools.paths import (
        PILOT_ROOT, LIBRARY_ROOT, PRESETS_ROOT, ASSETS_ROOT,
        SFX_LIBRARY, RESOLVE_SUPPORT, RESOLVE_LUT_DIR,
        project_output_dir, comp_dir,
    )

    # Or from within library/ code using relative imports:
    from tools.paths import SFX_LIBRARY
"""

import os
from pathlib import Path

try:
    from library.tools.shared_environment import REMOTION_DIRNAME
except ImportError:  # imported as `tools.paths` from inside library/
    from tools.shared_environment import REMOTION_DIRNAME


# ─── Configuration: environment, then the user's file, then .env ──
#
# Three layers, first one to name a key wins:
#
#   1. the process environment - an explicit `export` always wins;
#   2. the PER-USER config file, `user_config_path()` - one file per
#      person, outside every checkout, so a machine's paths are written
#      down once rather than once per worktree;
#   3. the per-checkout `.env`, which keeps working for anyone who has one.
#
# Then the code default at each `os.environ.get(...)` below. A code
# default names no one's machine: the engine is installed on machines
# that are not the one it was written on (README "Quickstart").
#
# Both files use the same KEY=value format, parsed here with no
# dependency, because this module is imported by Resolve's own Python
# (Fusion subprocesses) where neither python-dotenv nor tomllib can be
# assumed.

USER_CONFIG_ENV = "REN_CONFIG"
"""Names the per-user config file outright, for a test or a second user."""


def user_config_path() -> Path:
    """Where the per-user config lives: `$REN_CONFIG`, else XDG.

    `~/.config/ren/config.env` (or `$XDG_CONFIG_HOME/ren/config.env`),
    not `~/Library/Application Support`: it is a file a person edits by
    hand from a shell, and that path carries a space every command would
    have to quote. `gh`, `git` and `opencode` keep theirs here too.
    """
    explicit = os.environ.get(USER_CONFIG_ENV)
    if explicit:
        return Path(explicit).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base).expanduser() / "ren" / "config.env"


def read_env_file(env_path: Path) -> dict:
    """KEY=value pairs from a .env-format file; {} when there is no file.

    Supports:
      - Comments (lines starting with #)
      - Blank lines
      - Quoted values (single or double quotes are stripped)
      - $HOME / ~ expansion in values
    """
    if not env_path.is_file():
        return {}
    # encoding="utf-8", not the locale default.
    #
    # `open()` with no encoding decodes with locale.getpreferredencoding(),
    # which is ASCII inside the Python that Resolve's Fusion subprocess
    # runs. The shipped .env carries box-drawing characters in its section
    # headers, so on project 001 this line raised
    #   UnicodeDecodeError: 'ascii' codec can't decode byte 0xe2
    # at import of library.tools.paths, which killed
    # apply_fusion_comps before it drew anything. The render still
    # produced a file: every planned Fusion effect was simply absent, and
    # the only reason anyone knows is that verify_fusion_comps reported
    # "expected 7 clips carrying a Fusion comp, got 0".
    #
    # AGENTS.md records this hazard for subprocess text decoding. It is
    # the same hazard for every file read: name the encoding.
    values = {}
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            # Strip surrounding quotes
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            # Expand $HOME and ~ in paths
            value = os.path.expandvars(value)
            value = os.path.expanduser(value)
            values[key] = value
    return values


def load_configuration(files, environ=None) -> dict:
    """Fill `environ` from `files` in order, never overriding a key.

    An earlier file beats a later one, and anything already in `environ`
    beats both. Returns `{key: source}` for every key a file supplied
    or the environment already held, where source is the file's path or
    "environment" - what `ren config` reports.
    """
    if environ is None:
        environ = os.environ
    sources = {}
    for env_path in files:
        for key, value in read_env_file(Path(env_path)).items():
            if key in environ:
                sources.setdefault(key, "environment")
                continue
            environ[key] = value
            sources[key] = str(env_path)
    return sources


def _load_dotenv(env_path: Path) -> None:
    """Load one .env-format file into os.environ, never overriding a key."""
    load_configuration((env_path,))


# ─── Repo Structure ──────────────────────────────────────────
# Compute everything relative to this file's location:
#   paths.py lives at: <repo>/library/tools/paths.py
#   PILOT_ROOT =        <repo>/

PILOT_ROOT = Path(__file__).resolve().parent.parent.parent
LIBRARY_ROOT = PILOT_ROOT / "library"
PRESETS_ROOT = LIBRARY_ROOT / "presets"
ASSETS_ROOT = LIBRARY_ROOT / "assets"
STEPS_ROOT = LIBRARY_ROOT / "steps"
TOOLS_ROOT = LIBRARY_ROOT / "tools"

# Load AFTER computing PILOT_ROOT so we know where .env is.
CHECKOUT_ENV_FILE = PILOT_ROOT / ".env"
CONFIG_SOURCES = load_configuration((user_config_path(), CHECKOUT_ENV_FILE))


# ─── Preset Subdirectories ───────────────────────────────────

# The look is delivered by CDL plus Fusion from values committed in this
# repo (library/tools/series_look.py), so there are no luts/, dctls/ or
# powergrades/ preset directories to point at any more.
PRESETS_FUSION_MACROS = PRESETS_ROOT / "fusion-macros"
PRESETS_FAIRLIGHT = PRESETS_ROOT / "fairlight"


# ─── DaVinci Resolve Application Support ─────────────────────

RESOLVE_SUPPORT = Path(os.environ.get(
    "RESOLVE_SUPPORT_DIR",
    str(Path.home() / "Library" / "Application Support"
        / "Blackmagic Design" / "DaVinci Resolve"),
))

RESOLVE_LUT_DIR = RESOLVE_SUPPORT / "LUT"
RESOLVE_FUSION_DIR = RESOLVE_SUPPORT / "Fusion"
RESOLVE_FAIRLIGHT_DIR = RESOLVE_SUPPORT / "Fairlight"

# Subdirectories Resolve scans for specific asset types
RESOLVE_FUSION_MACROS = RESOLVE_FUSION_DIR / "Macros"
RESOLVE_FUSION_TEMPLATES = RESOLVE_FUSION_DIR / "Templates"
RESOLVE_FUSION_FUSES = RESOLVE_FUSION_DIR / "Fuses"
RESOLVE_FUSION_SCRIPTS = RESOLVE_FUSION_DIR / "Scripts"

# Resolve scripting API paths
RESOLVE_SCRIPT_API = Path(os.environ.get(
    "RESOLVE_SCRIPT_API",
    "/Library/Application Support/Blackmagic Design"
    "/DaVinci Resolve/Developer/Scripting",
))
RESOLVE_SCRIPT_LIB = Path(os.environ.get(
    "RESOLVE_SCRIPT_LIB",
    "/Applications/DaVinci Resolve/DaVinci Resolve.app"
    "/Contents/Libraries/Fusion/fusionscript.so",
))


# ─── External Asset Libraries ────────────────────────────────

# The code defaults sit under one neutral folder a new user can create
# (`ren init` creates the projects root). Anyone whose files live
# elsewhere sets these keys in `user_config_path()`.
REN_HOME_DEFAULT = Path.home() / "Movies" / "Ren"

SHARED_ASSETS_ROOT = Path(os.environ.get(
    "PIPELINE_SHARED_ASSETS",
    str(REN_HOME_DEFAULT / "assets"),
))

SFX_LIBRARY = Path(os.environ.get(
    "PIPELINE_SFX_LIBRARY",
    str(SHARED_ASSETS_ROOT / "sfx library"),
))
SFX_PROFILES = SFX_LIBRARY / "profiles"

MUSIC_LIBRARY = Path(os.environ.get(
    "PIPELINE_MUSIC_LIBRARY",
    str(SHARED_ASSETS_ROOT / "music"),
))

REMOTION_COMPOSITIONS = Path(os.environ.get(
    "PIPELINE_REMOTION_COMPOSITIONS",
    str(SHARED_ASSETS_ROOT / "remotion-compositions"),
))

# ─── Remotion Engine ─────────────────────────────────────────
#
# The renderer's own SOURCE is tracked code, so it lives in the checkout
# by default, like every other repo directory above.  Its DEPENDENCIES do
# not: `library/tools/shared_environment.py` owns where those are stored
# and how a checkout binds to them, and it is the ONE place that answers
# either question.  Nothing else may recompute it.
#
# `PIPELINE_REMOTION_DIR` exists so this is not the one external path in
# this module without an override - which it was until 2026-09-14, and
# which is the reason 61 tests skipped in every checkout but one.
REMOTION_DIR = Path(os.environ.get(
    "PIPELINE_REMOTION_DIR",
    str(PILOT_ROOT / REMOTION_DIRNAME),
))

# ─── Video Projects Root ─────────────────────────────────────
# All video projects live here, outside the repo. Each project is
# a directory containing project.yaml and standardized subdirs.

PROJECTS_ROOT = Path(os.environ.get(
    "PIPELINE_PROJECTS_ROOT",
    str(REN_HOME_DEFAULT / "projects"),
))


# ─── Resolve Sync Namespace ──────────────────────────────────
# The subdirectory name used inside Resolve's directories for
# pipeline-managed symlinks. Keeps our stuff isolated.

RESOLVE_SYNC_NAMESPACE = os.environ.get("RESOLVE_SYNC_NAMESPACE", "Pipeline")


# ─── Project-Level Path Helpers ───────────────────────────────

def project_root(slug: str) -> Path:
    """Resolve a project slug to its root directory.

    Searches PROJECTS_ROOT for a matching project directory.
    Supports both flat and client-grouped layouts.  A slug that instead
    names a project directory (or project.yaml) anywhere on disk resolves
    to that directory in place.
    """
    # Filesystem path reference (project outside PROJECTS_ROOT)
    if os.sep in slug or slug.startswith(("~", ".")):
        candidate = Path(slug).expanduser().resolve()
        if candidate.is_file() and candidate.name == "project.yaml":
            return candidate.parent
        if candidate.is_dir() and (candidate / "project.yaml").is_file():
            return candidate

    # Direct match
    direct = PROJECTS_ROOT / slug
    if direct.is_dir() and (direct / "project.yaml").exists():
        return direct

    # Client-grouped: PROJECTS_ROOT/client/slug
    for entry in PROJECTS_ROOT.iterdir():
        if entry.is_dir() and not entry.name.startswith((".", "_")):
            grouped = entry / slug
            if grouped.is_dir() and (grouped / "project.yaml").exists():
                return grouped

    raise FileNotFoundError(
        f"Project '{slug}' not found in {PROJECTS_ROOT}. "
        f"Use 'python3 manage_project.py list' to see available projects."
    )


# ─── Project-side paths belong to project_layout ─────────────
#
# This module owns the REPO and the MACHINE: presets, the Resolve support
# directories, the shared asset libraries.  Those are the same for every
# project and are known at import.
#
# Where one PROJECT's files go is a different question with a different
# shape - it is parameterised by a folder, it distinguishes input from
# output, and it carries a retention policy - so it lives in
# library/tools/project_layout.py.  These two helpers stay as thin
# forwarders because they are part of this module's published surface.

def project_output_dir(project_folder: str) -> Path:
    """Standard pipeline output directory for a given project.

    Forwards to `project_layout.ProjectLayout`, which owns the answer.
    """
    from library.tools.project_layout import Area, ProjectLayout
    return ProjectLayout(project_folder).read_dir(Area.OUTPUT_ROOT)


def comp_dir(project_folder: str = None) -> Path:
    """Where generated Fusion .comp files live.

    With a project, the project's own comps area.  Without one, the
    repo-level location, for tests and dev.
    """
    if project_folder:
        from library.tools.project_layout import Area, ProjectLayout
        return ProjectLayout(project_folder).read_dir(Area.FUSION_COMPS)
    return STEPS_ROOT / "step_6_01_render" / "fusion_comps"


def sfx_library_path() -> str:
    """Return the SFX library path as a string (for backward compatibility)."""
    return str(SFX_LIBRARY)


def music_library_path() -> str:
    """Return the music library path as a string."""
    return str(MUSIC_LIBRARY)
