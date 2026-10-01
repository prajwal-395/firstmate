"""
Project registry - discovers, creates, and manages video projects.

Projects live in a dedicated directory (PROJECTS_ROOT) outside this repo.
Each project is a directory containing a project.yaml config file and
standardized subdirectories for raw footage, pipeline output, and exports.

The registry scans PROJECTS_ROOT for project.yaml files, provides lookup
by slug, and scaffolds new projects with the correct directory structure.

Usage:
    from library.tools.project_registry import (
        scan_projects, get_project, create_project, list_projects,
    )

    # Discover all projects
    catalog = scan_projects()

    # Get a specific project
    config = get_project("my-show")

    # Create a new project
    config = create_project("my-vlog", name="Beltline Vlog", client="personal")
"""

import os
import sys
import time
from pathlib import Path
from typing import Optional

# Ensure library imports work
_PILOT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PILOT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PILOT_ROOT))

from library.schemas.project_config import (
    ProjectConfig,
    ProjectStatus,
    PipelineConfig,
    ResolveConfig,
    SourceConfig,
    load_project_config,
    project_config_to_dict,
)
from library.tools.paths import PROJECTS_ROOT, LIBRARY_ROOT
from library.tools.project_layout import ProjectLayout


# ─── Standard project directory structure ─────────────────────

# The scaffold is NOT a list here.  It was, and it drifted: it promised
# `pipeline_output/subtitles` and `pipeline_output/motion_graphics` while
# the steps that render those wrote `subtitle_segments` and
# `motion_graphics_segments`, so `create_project` made two empty
# directories nothing ever opened and never made the two that filled up.
# `ProjectLayout.ensure()` builds the layout from the same table the
# steps read, which is the only way the two cannot disagree.
# See library/tools/project_layout.py.
#
# Input directories a new project starts with. Only raw/ is universal:
# every project needs footage, and step 1.01 scans this directory.
#
# music/, assets/, brand_assets/ and compositions/ are declared as
# Kind.INPUT in the layout but NOT scaffolded - a directory whose
# presence means the captain has that kind of material is more useful
# than five empty directories. The captain creates them when needed,
# and the pipeline handles their absence gracefully (music_selection
# checks os.path.isdir before listing, brand_assets is optional for
# Remotion, etc.).
PROJECT_INPUT_DIRS = [
    "raw",
]


# ─── Registry Operations ─────────────────────────────────────

def scan_projects(root: Path = None) -> list[ProjectConfig]:
    """Walk PROJECTS_ROOT and find all project.yaml files.

    Supports both flat and client-grouped layouts:
        projects_root/my-project/project.yaml           (flat)
        projects_root/client-name/my-project/project.yaml (grouped)

    Returns a list of validated ProjectConfig objects.
    """
    root = root or PROJECTS_ROOT
    if not root.exists():
        return []

    configs = []

    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.name.startswith((".", "_")):
            continue

        # Check for project.yaml directly (flat layout)
        yaml_path = entry / "project.yaml"
        if yaml_path.exists():
            try:
                configs.append(load_project_config(yaml_path))
            except (ValueError, FileNotFoundError) as e:
                print(f"  Warning: skipping {yaml_path}: {e}", file=sys.stderr)
            continue

        # Check for client-grouped layout (one level deeper)
        for sub_entry in sorted(entry.iterdir()):
            if not sub_entry.is_dir() or sub_entry.name.startswith((".", "_")):
                continue
            sub_yaml = sub_entry / "project.yaml"
            if sub_yaml.exists():
                try:
                    configs.append(load_project_config(sub_yaml))
                except (ValueError, FileNotFoundError) as e:
                    print(f"  Warning: skipping {sub_yaml}: {e}", file=sys.stderr)

    return configs


def resolve_project_path(ref: str) -> Optional[Path]:
    """Resolve a filesystem project reference to its project.yaml path.

    A project reference may address a project living anywhere on disk,
    not just under PROJECTS_ROOT.  Accepted forms:

        /abs/path/to/project/            -> /abs/path/to/project/project.yaml
        /abs/path/to/project/project.yaml
        ./relative/path/to/project/      (resolved against the cwd)
        ~/path/to/project/

    Returns the project.yaml Path, or None when *ref* does not name an
    existing project on disk (in which case it is treated as a slug).
    """
    if not ref:
        return None
    # A bare slug never contains a separator and never starts with ~ or .
    if not (os.sep in ref or ref.startswith("~") or ref.startswith(".")):
        return None

    candidate = Path(ref).expanduser()
    try:
        candidate = candidate.resolve()
    except OSError:
        return None

    if candidate.is_file() and candidate.name == "project.yaml":
        return candidate
    if candidate.is_dir() and (candidate / "project.yaml").is_file():
        return candidate / "project.yaml"
    return None


def get_project(slug: str, root: Path = None) -> ProjectConfig:
    """Load a project by slug or by filesystem path.

    Searches PROJECTS_ROOT for a project.yaml with a matching slug.  If
    *slug* instead names a directory (or project.yaml) on disk, that
    project is loaded in place - it does not need to live under
    PROJECTS_ROOT.  Raises FileNotFoundError if not found.
    """
    direct_path = resolve_project_path(slug)
    if direct_path:
        return load_project_config(direct_path)

    root = root or PROJECTS_ROOT
    configs = scan_projects(root)

    for config in configs:
        if config.slug == slug:
            return config

    # Also try direct path lookup: root/slug/project.yaml
    direct = root / slug / "project.yaml"
    if direct.exists():
        return load_project_config(direct)

    # Try client-grouped: root/*/slug/project.yaml
    for entry in root.iterdir():
        if entry.is_dir():
            grouped = entry / slug / "project.yaml"
            if grouped.exists():
                return load_project_config(grouped)

    # Say which root was searched and what was in it. PROJECTS_ROOT is
    # not exclusive by design - resolve_project_path above loads a
    # project living anywhere on disk - so a slug that is not here is
    # usually a project kept elsewhere, and the message has to say how
    # to reach one rather than just that the slug is unknown.
    available = sorted(c.slug for c in configs)
    found = "\n".join(f"      {c}" for c in available) if available else "      (none)"
    raise FileNotFoundError(
        f"No project with slug '{slug}'.\n"
        f"    Searched: {root}\n"
        f"    Found there:\n{found}\n"
        f"    A project kept outside that root is addressed by its path "
        f"instead of its slug, for example:\n"
        f"      python3 manage_project.py info /path/to/{slug}"
    )


def list_projects(
    status: Optional[ProjectStatus] = None,
    client: Optional[str] = None,
    root: Path = None,
) -> list[ProjectConfig]:
    """List projects, optionally filtered by status and/or client."""
    configs = scan_projects(root)

    if status:
        configs = [c for c in configs if c.status == status]
    if client:
        configs = [c for c in configs if c.client == client]

    return configs


def create_project(
    slug: str,
    name: str,
    client: str = "",
    template: str = "",
    fps: float = 30,
    resolve_project_name: str = "",
    resolve_folder: str = "",
    tags: list[str] = None,
    description: str = "",
    root: Path = None,
    language: str = "en",
    shape: str = "",
    speakers: list | None = None,
    brief_title: str = "",
    brand_series: str = "",
) -> ProjectConfig:
    """Scaffold a new project directory with standard structure.

    Creates:
        <root>/[<client>/]<slug>/
            project.yaml
            brand.json        # starter: declares nothing, loads clean
            brief.md          # starter: intake answers as facts, the rest
                              # as open questions - no invented taste
            style.yaml        # starter: commented fields, nothing locked
            video.yaml        # starter: commented fields, no overrides
            raw/
            pipeline_output/
            exports/
            ...

    `language` is the `source.language` transcription code, `shape`
    one of speech/music/both/picture-led (or "" for undeclared),
    `speakers` the `source.speakers` roster ([{name, role?}], [] for
    a declared-zero project, None for undeclared). Every one of them
    is a project.yaml declaration the pipeline reads; the four files
    are what the interview behind `manage_project.py new` fills in.
    """
    root = root or PROJECTS_ROOT

    # Determine project path
    if client:
        project_dir = root / client / slug
    else:
        project_dir = root / slug

    if project_dir.exists():
        yaml_path = project_dir / "project.yaml"
        if yaml_path.exists():
            raise FileExistsError(
                f"Project already exists at {project_dir}. "
                f"Use get_project('{slug}') to load it."
            )

    # Build config first and validate BEFORE touching disk: a
    # refusal must not leave an empty directory behind.
    config = ProjectConfig(
        name=name,
        slug=slug,
        client=client,
        created=time.strftime("%Y-%m-%d"),
        status=ProjectStatus.DRAFT,
        source=SourceConfig(
            fps=fps,
            language=(language or "en"),
            shape=(shape or ""),
            speakers=speakers,
        ),
        pipeline=PipelineConfig(
            brand_template=template,
            creative_brief="brief.md",
        ),
        resolve=ResolveConfig(
            project_name=resolve_project_name or name,
            folder=resolve_folder or client,
        ),
        tags=tags or [],
        description=description,
    )
    config._project_root = project_dir

    errors = config.validate()
    if errors:
        raise ValueError(
            f"Refusing to scaffold {slug}: "
            f"{'; '.join(errors)}")

    # Create directory structure. The output side comes from the layout
    # owner, which also writes README-LAYOUT.md so the folder explains
    # itself; the input side is these three, empty and read-only to the
    # pipeline.
    project_dir.mkdir(parents=True, exist_ok=True)
    for subdir in PROJECT_INPUT_DIRS:
        (project_dir / subdir).mkdir(parents=True, exist_ok=True)
    ProjectLayout(project_dir).ensure()

    # Write project.yaml
    _write_project_yaml(project_dir / "project.yaml", config)

    # The four starters the interview fills in. Each loads clean and
    # changes nothing until the project declares something in it.
    _write_brand_starter(project_dir / "brand.json",
                         brand_series or slug)
    _write_brief_starter(project_dir / "brief.md", config,
                         brief_title or name)
    _write_style_starter(project_dir / "style.yaml")
    _write_video_starter(project_dir / "video.yaml")

    return config


def _write_project_yaml(path: Path, config: ProjectConfig) -> None:
    """Write a ProjectConfig to a YAML file."""
    data = project_config_to_dict(config)

    try:
        import yaml
        with open(path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
    except ImportError:
        # Manual YAML output for simple structures
        with open(path, "w") as f:
            _write_yaml_manual(f, data)


def _write_yaml_manual(f, data: dict, indent: int = 0) -> None:
    """Write a dict as YAML without PyYAML dependency."""
    prefix = "  " * indent
    for key, value in data.items():
        if isinstance(value, dict):
            f.write(f"{prefix}{key}:\n")
            _write_yaml_manual(f, value, indent + 1)
        elif isinstance(value, list):
            if not value:
                f.write(f"{prefix}{key}: []\n")
            else:
                f.write(f"{prefix}{key}:\n")
                for item in value:
                    f.write(f"{prefix}  - {item}\n")
        elif isinstance(value, bool):
            f.write(f"{prefix}{key}: {'true' if value else 'false'}\n")
        elif isinstance(value, (int, float)):
            f.write(f"{prefix}{key}: {value}\n")
        elif value == "":
            f.write(f"{prefix}{key}: \"\"\n")
        else:
            # Quote strings with special chars
            if any(c in str(value) for c in ":#{}[],$&*?|>!%@`"):
                f.write(f'{prefix}{key}: "{value}"\n')
            else:
                f.write(f"{prefix}{key}: {value}\n")


# ─── Intake starters ────────────────────────────────────────────
#
# What `manage_project.py new` scaffolds beside project.yaml. Each
# file loads clean through its own reader and changes NOTHING until
# the project declares something in it: a starter that silently
# altered a run would be a decision nobody made.


def _write_brand_starter(path: Path, series: str) -> None:
    """A brand.json that declares nothing and loads clean.

    The product ships no templates: the project's own brand.json IS
    the declaration (library/tools/brand_registry.BRAND_JSON), so a
    new project carries one with empty slots. Empty slots read as
    absence everywhere - no look, no palette, no bookends - which is
    exactly the no-brand run, stated in a file instead of inferred.
    """
    import json
    path.write_text(json.dumps({
        "series_id": series,
        "delivery_format": "",
        "style": {},
        "effect": {},
        "content": {},
    }, indent=2) + "\n", encoding="utf-8")


def _write_style_starter(path: Path) -> None:
    """A style.yaml with commented fields and nothing locked.

    YAML has comments, so the starter documents the shape
    (library/tools/video_prefs.VIDEO_PREF_FIELDS) without declaring
    it: the loader merges to nothing and returns None, which is
    today's behaviour exactly.
    """
    path.write_text(
        "# Project-level video preferences (library/tools/video_prefs).\n"
        "# Uncomment and set what this project wants locked for every\n"
        "# video; list locked names under `locked:` and a per-video\n"
        "# video.yaml can then override only what is not locked.\n"
        "#\n"
        "# style: calm\n"
        "# delivery_format: vertical_1080x1920\n"
        "# color_grade:\n"
        "#   path: brand_assets/grade.drx\n"
        "#   provenance: series colorist, 2026-09-24\n"
        "# subtitle_style: word_by_word\n"
        "# target_length_seconds: 60\n"
        "# content_rules:\n"
        "#   speakers_must_interact: [Craig, Akshita]\n"
        "#   require_value_add: true\n"
        "#   require_cta: true\n"
        "locked: []\n",
        encoding="utf-8")


def _write_video_starter(path: Path) -> None:
    """A video.yaml with commented fields and no overrides.

    Same contract as the style starter: documents the per-video
    layer (shared fields on top, `reels[R]` sections per reel)
    without declaring any, so the loader merges to nothing.
    """
    path.write_text(
        "# Per-video preferences (library/tools/video_prefs).\n"
        "# Shared fields on top override style.yaml where the project\n"
        "# left them unlocked; a `reels:` mapping keyed by reel lets\n"
        "# two reels of one multi-reel project differ.\n"
        "#\n"
        "# target_length_seconds: 45\n"
        "# content_rules:\n"
        "#   require_cta: false\n"
        "# reels:\n"
        "#   3:\n"
        "#     target_length_seconds: 90\n",
        encoding="utf-8")


def _write_brief_starter(path: Path, config: ProjectConfig,
                         title: str) -> None:
    """A brief.md seeded with the intake answers, and nothing else.

    Facts the interview collected (shape, language, speakers) arrive
    as facts. Everything else is an OPEN section - headings with no
    claims under them - because inventing taste (a mood, a promise,
    an audience) on the project's behalf is the one thing the
    pipeline must never do (AGENTS.md 10.5). The run attaches this
    file through `pipeline.creative_brief`; the open sections are
    what the planning steps ask about.
    """
    source = config.source
    if source.speakers is None:
        speakers_line = "Undecided - who speaks in this footage is not declared yet."
    elif not source.speakers:
        speakers_line = "None - a project with no voices (music / montage)."
    else:
        speakers_line = ", ".join(
            entry.get("name", "")
            + (f" ({entry['role']})" if entry.get("role") else "")
            for entry in source.speakers)
    shape_line = (source.shape or
                  "Undecided - speech, music, both or picture-led.")
    lines = [
        f"# {title}",
        "",
        "Creative brief. Facts first, then open questions - fill the",
        "open sections before the run plans anything, or the planning",
        "steps will ask about them.",
        "",
        "## Facts (from intake)",
        "",
        f"- Shape: {shape_line}",
        f"- Language: {source.language}",
        f"- Speakers: {speakers_line}",
        "",
        "## Audience",
        "",
        "Who is this for?",
        "",
        "## Promise",
        "",
        "What does the viewer get that they did not have before?",
        "",
        "## Close",
        "",
        "What should the viewer go and do after watching?",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def project_status(slug: str, root: Path = None) -> dict:
    """Return a status summary for a project.

    Includes pipeline progress, filesystem state, and resolve binding.
    """
    config = get_project(slug, root)
    project_dir = config.project_root

    # Check directory state
    raw_files = []
    if config.raw_dir.exists():
        raw_files = [
            f.name for f in config.raw_dir.iterdir()
            if f.suffix.lower() in (".mov", ".mp4", ".mxf", ".avi", ".mkv")
        ]

    # Check pipeline state
    pipeline_state = {}
    if config.pipeline_data_path.exists():
        import json
        with open(config.pipeline_data_path) as f:
            pipeline_state = json.load(f)

    # Both ledgers, merged for display. The split is real in the state
    # file - see library/tools/step_ledger.py - but a project listing
    # wants the whole picture.
    from library.tools import step_ledger
    steps_completed = list(step_ledger.all_completed(pipeline_state).keys())

    # Check exports
    exports = []
    if config.exports_dir.exists():
        exports = [f.name for f in config.exports_dir.iterdir() if not f.name.startswith(".")]

    return {
        "slug": config.slug,
        "name": config.name,
        "status": config.status.value,
        "client": config.client,
        "project_root": str(project_dir),
        "raw_footage_count": len(raw_files),
        "steps_completed": steps_completed,
        "steps_completed_count": len(steps_completed),
        "exports": exports,
        "resolve_project": config.resolve.project_name,
        "resolve_folder": config.resolve.folder,
        "pipeline_data_exists": config.pipeline_data_path.exists(),
    }


def archive_project(slug: str, root: Path = None) -> Path:
    """Move a project to the _archived/ directory under PROJECTS_ROOT.

    Updates the project status to ARCHIVED in project.yaml.
    Returns the new path.
    """
    config = get_project(slug, root)
    root = root or PROJECTS_ROOT
    if root not in config.project_root.parents:
        raise ValueError(
            f"Refusing to archive '{slug}': it lives at "
            f"{config.project_root}, outside the projects root {root}. "
            f"Archiving would move it. Move it yourself if that is intended."
        )
    archive_dir = root / "_archived"
    archive_dir.mkdir(exist_ok=True)

    src = config.project_root
    dst = archive_dir / src.name
    if dst.exists():
        # Add timestamp suffix
        ts = time.strftime("%Y%m%d_%H%M%S")
        dst = archive_dir / f"{src.name}_{ts}"

    # Update status before moving
    config.status = ProjectStatus.ARCHIVED
    _write_project_yaml(src / "project.yaml", config)

    # Move
    import shutil
    shutil.move(str(src), str(dst))

    return dst
