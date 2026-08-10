"""
Project configuration schema for the video editing pipeline.

Defines the structure of project.yaml files that live alongside
each video project's assets. These configs connect the three layers:
  - Engine (this repo's pipeline code)
  - Assets (raw footage, pipeline output, exports)
  - Platform (DaVinci Resolve project)

Usage:
    from library.schemas.project_config import ProjectConfig, load_project_config
    config = load_project_config("/path/to/project/project.yaml")
"""

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


class ProjectStatus(str, Enum):
    """Lifecycle status of a video project."""
    DRAFT = "draft"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    COMPLETE = "complete"
    ARCHIVED = "archived"


@dataclass
class SourceConfig:
    """Describes the source media characteristics."""
    type: str = "iphone_mov"       # iphone_mov | sony_raw | screen_capture | mixed
    resolution: str = "1080x1920"  # WxH
    fps: int = 30

    @property
    def width(self) -> int:
        return int(self.resolution.split("x")[0])

    @property
    def height(self) -> int:
        return int(self.resolution.split("x")[1])

    @property
    def is_vertical(self) -> bool:
        return self.height > self.width


@dataclass
class PipelineConfig:
    """Pipeline-specific configuration for this project."""
    brand_template: str = "default_brand"  # reference to library/templates/
    creative_brief: str = ""  # path to markdown creative brief (relative to project root)
    sfx_library: str = ""    # resolved from env if empty
    music_library: str = ""  # resolved from env if empty

    def resolve_paths(self) -> "PipelineConfig":
        """Expand env vars and ~ in paths, fall back to env defaults."""
        if not self.sfx_library:
            self.sfx_library = os.environ.get("PIPELINE_SFX_LIBRARY", "")
        else:
            self.sfx_library = os.path.expandvars(os.path.expanduser(self.sfx_library))

        if not self.music_library:
            self.music_library = os.environ.get("PIPELINE_MUSIC_LIBRARY", "")
        else:
            self.music_library = os.path.expandvars(os.path.expanduser(self.music_library))

        return self


@dataclass
class ResolveConfig:
    """DaVinci Resolve project binding."""
    project_name: str = ""        # exact name in Resolve project manager
    database: str = "local"       # local | cloud
    folder: str = ""              # Resolve PM folder path (e.g., "Client/SubFolder")
    timeline_name: str = "Main Edit"


@dataclass
class ProjectConfig:
    """Complete project configuration - deserialized from project.yaml."""
    name: str
    slug: str                     # filesystem-safe identifier (directory name)
    client: str = ""              # optional client grouping
    created: str = ""             # ISO date string
    status: ProjectStatus = ProjectStatus.DRAFT

    source: SourceConfig = field(default_factory=SourceConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    resolve: ResolveConfig = field(default_factory=ResolveConfig)

    tags: list[str] = field(default_factory=list)
    description: str = ""

    # Runtime - not serialized to YAML
    _project_root: Optional[Path] = field(default=None, repr=False)

    @property
    def project_root(self) -> Path:
        """Absolute path to the project directory."""
        if self._project_root is None:
            raise ValueError("project_root not set - load via load_project_config()")
        return self._project_root

    @property
    def raw_dir(self) -> Path:
        return self.project_root / "raw"

    @property
    def pipeline_output_dir(self) -> Path:
        return self.project_root / "pipeline_output"

    @property
    def exports_dir(self) -> Path:
        return self.project_root / "exports"

    @property
    def pipeline_data_path(self) -> Path:
        return self.project_root / "pipeline_data.json"

    def validate(self) -> list[str]:
        """Return a list of validation errors (empty = valid)."""
        errors = []
        if not self.name:
            errors.append("'name' is required")
        if not self.slug:
            errors.append("'slug' is required")
        if not self.slug.replace("-", "").replace("_", "").isalnum():
            errors.append(f"'slug' must be alphanumeric with dashes/underscores: {self.slug}")
        try:
            ProjectStatus(self.status) if isinstance(self.status, str) else self.status
        except ValueError:
            errors.append(f"Invalid status: {self.status}")
        return errors

@dataclass
class ProjectJsonConfig:
    """Schema for project.json configuration."""
    target_duration_seconds: int = 60
    style_preset: str = "shortform_vertical"
    subtitle_style: str = "word_by_word"

    @classmethod
    def from_dict(cls, data: dict) -> "ProjectJsonConfig":
        return cls(
            target_duration_seconds=data.get("target_duration_seconds", 60),
            style_preset=data.get("style_preset", "shortform_vertical"),
            subtitle_style=data.get("subtitle_style", "word_by_word"),
        )



def _dict_to_project_config(data: dict, project_root: Path = None) -> ProjectConfig:
    """Convert a raw dict (from YAML) to a ProjectConfig dataclass."""
    source_data = data.get("source", {})
    pipeline_data = data.get("pipeline", {})
    resolve_data = data.get("resolve", {})

    source = SourceConfig(
        type=source_data.get("type", "iphone_mov"),
        resolution=source_data.get("resolution", "1080x1920"),
        fps=source_data.get("fps", 30),
    )

    pipeline = PipelineConfig(
        brand_template=pipeline_data.get("brand_template", "default_brand"),
        creative_brief=pipeline_data.get("creative_brief", ""),
        sfx_library=pipeline_data.get("sfx_library", ""),
        music_library=pipeline_data.get("music_library", ""),
    )

    resolve = ResolveConfig(
        project_name=resolve_data.get("project_name", ""),
        database=resolve_data.get("database", "local"),
        folder=resolve_data.get("folder", ""),
        timeline_name=resolve_data.get("timeline_name", "Main Edit"),
    )

    status_raw = data.get("status", "draft")
    try:
        status = ProjectStatus(status_raw)
    except ValueError:
        status = ProjectStatus.DRAFT

    config = ProjectConfig(
        name=data.get("name", ""),
        slug=data.get("slug", ""),
        client=data.get("client", ""),
        created=data.get("created", ""),
        status=status,
        source=source,
        pipeline=pipeline,
        resolve=resolve,
        tags=data.get("tags", []),
        description=data.get("description", ""),
    )
    config._project_root = project_root
    return config


def project_config_to_dict(config: ProjectConfig) -> dict:
    """Serialize a ProjectConfig to a dict suitable for YAML output."""
    return {
        "name": config.name,
        "slug": config.slug,
        "client": config.client,
        "created": config.created,
        "status": config.status.value if isinstance(config.status, ProjectStatus) else config.status,
        "source": {
            "type": config.source.type,
            "resolution": config.source.resolution,
            "fps": config.source.fps,
        },
        "pipeline": {
            "brand_template": config.pipeline.brand_template,
            "creative_brief": config.pipeline.creative_brief,
            "sfx_library": config.pipeline.sfx_library,
            "music_library": config.pipeline.music_library,
        },
        "resolve": {
            "project_name": config.resolve.project_name,
            "database": config.resolve.database,
            "folder": config.resolve.folder,
            "timeline_name": config.resolve.timeline_name,
        },
        "tags": config.tags,
        "description": config.description,
    }


def load_project_config(yaml_path: str | Path) -> ProjectConfig:
    """Load and validate a project.yaml file.

    Uses PyYAML if available, falls back to a simple parser for basic YAML.
    """
    yaml_path = Path(yaml_path)
    if not yaml_path.exists():
        raise FileNotFoundError(f"Project config not found: {yaml_path}")

    try:
        import yaml
        with open(yaml_path) as f:
            data = yaml.safe_load(f)
    except ImportError:
        # Minimal fallback - just parse key: value pairs
        data = _minimal_yaml_parse(yaml_path)

    if not isinstance(data, dict):
        raise ValueError(f"Project config is not a YAML mapping: {yaml_path}")

    project_root = yaml_path.parent
    config = _dict_to_project_config(data, project_root=project_root)

    errors = config.validate()
    if errors:
        raise ValueError(f"Invalid project config {yaml_path}: {'; '.join(errors)}")

    # Resolve env vars in pipeline paths
    config.pipeline.resolve_paths()

    return config


def _minimal_yaml_parse(path: Path) -> dict:
    """Extremely minimal YAML parser for flat key: value files.

    Only handles top-level scalars and one level of nesting. Not a
    replacement for PyYAML - just enough to bootstrap if it's missing.
    """
    data = {}
    current_section = None

    with open(path) as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            indent = len(line) - len(line.lstrip())

            if indent == 0 and ":" in stripped:
                key, _, value = stripped.partition(":")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if value:
                    data[key] = value
                else:
                    data[key] = {}
                    current_section = key
            elif indent > 0 and current_section and ":" in stripped:
                key, _, value = stripped.partition(":")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if isinstance(data[current_section], dict):
                    data[current_section][key] = value

    return data
