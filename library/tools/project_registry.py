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
    config = get_project("geo-podcast")

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


# ─── Standard project directory structure ─────────────────────

PROJECT_DIRS = [
    "raw",
    "pipeline_output",
    "pipeline_output/fusion_comps",
    "pipeline_output/music",
    "pipeline_output/prosody",
    "pipeline_output/subtitles",
    "pipeline_output/motion_graphics",
    "brand_assets",
    "compositions",
    "exports",
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


def get_project(slug: str, root: Path = None) -> ProjectConfig:
    """Load a project by slug.

    Searches PROJECTS_ROOT for a project.yaml with a matching slug.
    Raises FileNotFoundError if not found.
    """
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

    available = [c.slug for c in configs]
    raise FileNotFoundError(
        f"Project '{slug}' not found in {root}. "
        f"Available projects: {available}"
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
    template: str = "default_brand",
    source_type: str = "iphone_mov",
    resolution: str = "1080x1920",
    fps: int = 30,
    resolve_project_name: str = "",
    resolve_folder: str = "",
    tags: list[str] = None,
    description: str = "",
    root: Path = None,
) -> ProjectConfig:
    """Scaffold a new project directory with standard structure.

    Creates:
        <root>/[<client>/]<slug>/
            project.yaml
            raw/
            pipeline_output/
            exports/
            ...
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

    # Create directory structure
    project_dir.mkdir(parents=True, exist_ok=True)
    for subdir in PROJECT_DIRS:
        (project_dir / subdir).mkdir(parents=True, exist_ok=True)

    # Build config
    config = ProjectConfig(
        name=name,
        slug=slug,
        client=client,
        created=time.strftime("%Y-%m-%d"),
        status=ProjectStatus.DRAFT,
        source=SourceConfig(type=source_type, resolution=resolution, fps=fps),
        pipeline=PipelineConfig(brand_template=template),
        resolve=ResolveConfig(
            project_name=resolve_project_name or name,
            folder=resolve_folder or client,
        ),
        tags=tags or [],
        description=description,
    )
    config._project_root = project_dir

    # Write project.yaml
    _write_project_yaml(project_dir / "project.yaml", config)

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

    steps_completed = list(pipeline_state.get("steps_completed", {}).keys())

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
