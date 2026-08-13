"""
Centralized path configuration for the video editing pipeline.

This is the single source of truth for every filesystem path the pipeline
uses - repo directories, DaVinci Resolve application support paths, and
external asset libraries. All paths are computed relative to the repo root
or read from environment variables (loaded from .env on import).

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


# ─── .env Loader ─────────────────────────────────────────────
# Load .env file from repo root if it exists. We avoid adding a
# dependency on python-dotenv by doing a minimal parse ourselves.

def _load_dotenv(env_path: Path) -> None:
    """Load key=value pairs from a .env file into os.environ.

    Supports:
      - Comments (lines starting with #)
      - Blank lines
      - Quoted values (single or double quotes are stripped)
      - Inline comments after quoted values
    Does NOT override variables that are already set in the environment.
    """
    if not env_path.is_file():
        return
    with open(env_path) as f:
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
            # Don't override existing env vars (explicit env takes precedence)
            if key not in os.environ:
                os.environ[key] = value


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

# Load .env AFTER computing PILOT_ROOT so we know where to find it
_load_dotenv(PILOT_ROOT / ".env")


# ─── Preset Subdirectories ───────────────────────────────────

PRESETS_LUTS = PRESETS_ROOT / "luts"
PRESETS_DCTLS = PRESETS_ROOT / "dctls"
PRESETS_FUSION_MACROS = PRESETS_ROOT / "fusion-macros"
PRESETS_POWERGRADES = PRESETS_ROOT / "powergrades"
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

SHARED_ASSETS_ROOT = Path(os.environ.get(
    "PIPELINE_SHARED_ASSETS",
    str(Path.home() / "Documents" / "content_stuff"
        / "assets i used (just copied here for convenience)"),
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
REMOTION_DIR = PILOT_ROOT / "remotion-subtitles"

# ─── Video Projects Root ─────────────────────────────────────
# All video projects live here, outside the repo. Each project is
# a directory containing project.yaml and standardized subdirs.

PROJECTS_ROOT = Path(os.environ.get(
    "PIPELINE_PROJECTS_ROOT",
    str(Path.home() / "Documents" / "content_stuff" / "video_projects"),
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


def project_output_dir(project_folder: str) -> Path:
    """Standard pipeline output directory for a given project.

    All pipeline intermediates and generated assets live here:
      <project_folder>/pipeline_output/
    """
    return Path(project_folder) / "pipeline_output"


def comp_dir(project_folder: str = None) -> Path:
    """Where generated Fusion .comp files live.

    If a project_folder is provided, comps go into the project's output:
      <project_folder>/pipeline_output/fusion_comps/

    Otherwise falls back to the repo-level location (for tests/dev):
      library/steps/step_6_01_render/fusion_comps/
    """
    if project_folder:
        return project_output_dir(project_folder) / "fusion_comps"
    return STEPS_ROOT / "step_6_01_render" / "fusion_comps"


def sfx_library_path() -> str:
    """Return the SFX library path as a string (for backward compatibility)."""
    return str(SFX_LIBRARY)


def music_library_path() -> str:
    """Return the music library path as a string."""
    return str(MUSIC_LIBRARY)
