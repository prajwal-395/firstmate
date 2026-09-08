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
from typing import List, Optional


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

    fps: float = 30
    """FLOAT, not int. 23.976 and 29.97 are real project rates and an
    int silently truncated them to 23 and 29."""

    footage_root: str = ""
    """An ABSOLUTE directory holding this project's footage, when it does
    not live in `<project>/raw`.

    A project whose rough cut was cut in Resolve has its media wherever
    the editor put it, and copying or symlinking it into the project is a
    write to the captain's own material to work around a missing
    capability. Declaring it is the capability.

    Empty means the default: `<project>/raw`. See
    `library/tools/footage_identity.enumerate_footage`, which is the one
    place that resolves this."""

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
    # Per-project override of the frame the product ships in. Empty means
    # "take the brand template's", which in turn defaults to vertical.
    # A name from library/tools/delivery_format.DELIVERY_FORMATS; an
    # unknown one raises rather than quietly reverting to the default.
    delivery_format: str = ""
    # Per-project override of how much of that frame the picture fills:
    # 0.0 letterbox, 1.0 fill. None means "take the brand template's",
    # which in turn defaults to fill. See library/tools/framing_intent.py.
    framing_intent: Optional[float] = None
    # Per-project caption typography, in the same {font, size, weight}
    # shape a brand template's `style.typography` uses, overriding it key
    # by key. None means "take the template's". See
    # library/tools/subtitle_style.py, "And the PROJECT".
    subtitle_typography: Optional[dict] = None
    # How caption overlays are carried: the delivery frame or only the
    # drawn bounds (`subtitle_overlay_geometry`: full|tight), stitched
    # video or the frame sequence itself
    # (`subtitle_overlay_container`: video|frames). Undeclared means
    # today's path. See library/tools/overlay_mode.py, which is what
    # the steps read - these fields exist so manage_project.py
    # validates and round-trips the keys rather than dropping them.
    subtitle_overlay_geometry: str = "full"
    subtitle_overlay_container: str = "video"
    creative_brief: str = ""  # path to markdown creative brief (relative to project root)
    # Whether that brief is ATTACHED to the planning prompts. Three
    # states, and None is not "false": an undeclared key means the PATH
    # is the declaration. See library/tools/brief_attachment.py, which
    # is what the runner reads - this field exists so manage_project.py
    # validates and round-trips the key rather than dropping it.
    attach_creative_brief: Optional[bool] = None
    # Headings of the brief this project wants carried INLINE rather than
    # reached by path.  Clause 3 of the rule in
    # library/tools/brief_reference.py: which sections are about THIS
    # video is a judgement belonging to whoever owns the video, so the
    # engine keeps no default list.  Empty means "the map is enough".
    creative_brief_inline: List[str] = field(default_factory=list)
    # Which series this video belongs to - the membership anchor for a
    # channel brief that names several (#258, library/tools/
    # brief_reference.py).  Empty means the project names none, and the
    # run says so where the model reads the brief rather than leaving
    # the roster as a choice.  A per-video statement: the brand template
    # names its own series_id, but the template is a look, not the
    # project's word about which creative world this video is in.
    series: str = ""
    # Project-declared creative tasks: named pieces of craft judgement the
    # pipeline INVOKES, instead of adding a permanent step for work only
    # some projects need (reel selection is the first). Each entry is a
    # mapping - see library/tools/creative_tasks.py, which is the one
    # place that reads them. Empty means the project declares none.
    creative_tasks: List[dict] = field(default_factory=list)
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

    # Every one of these delegates to the layout owner
    # (library/tools/project_layout.py). Two definitions of "where the
    # exports go" is one too many, and the schema is not the owner.
    @property
    def _layout(self):
        from library.tools.project_layout import ProjectLayout
        return ProjectLayout(self.project_root)

    @property
    def raw_dir(self) -> Path:
        from library.tools.project_layout import Area
        return self._layout.read_dir(Area.RAW)

    @property
    def pipeline_output_dir(self) -> Path:
        from library.tools.project_layout import Area
        return self._layout.read_dir(Area.OUTPUT_ROOT)

    @property
    def exports_dir(self) -> Path:
        from library.tools.project_layout import Area
        return self._layout.read_dir(Area.EXPORTS)

    @property
    def pipeline_data_path(self) -> Path:
        return self._layout.pipeline_data_path

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
        if self.pipeline.delivery_format:
            from library.tools.delivery_format import DELIVERY_FORMATS
            if self.pipeline.delivery_format not in DELIVERY_FORMATS:
                errors.append(
                    f"Invalid pipeline.delivery_format: "
                    f"{self.pipeline.delivery_format} must be one of "
                    f"{sorted(DELIVERY_FORMATS)}"
                )
        if self.pipeline.framing_intent is not None:
            from library.tools.framing_intent import validate_framing_intent
            try:
                validate_framing_intent(self.pipeline.framing_intent,
                                        "pipeline.framing_intent")
            except (TypeError, ValueError) as exc:
                errors.append(str(exc))
        if self.pipeline.attach_creative_brief is not None:
            # The three-state reading is the whole point, so a value
            # that is neither boolean is refused rather than coerced -
            # a truthy string would silently attach a brief the project
            # meant to decline. library/tools/brief_attachment.py holds
            # the runtime half of the same refusal.
            if not isinstance(self.pipeline.attach_creative_brief, bool):
                errors.append(
                    "pipeline.attach_creative_brief must be true or false, "
                    f"got {type(self.pipeline.attach_creative_brief).__name__}")
            elif (self.pipeline.attach_creative_brief
                  and not self.pipeline.creative_brief):
                errors.append(
                    "pipeline.attach_creative_brief is true and no "
                    "pipeline.creative_brief path is declared. Declare the "
                    "path, or set attach_creative_brief to false.")
        if not isinstance(self.pipeline.series, str):
            errors.append(
                "pipeline.series must be the name of the series this "
                "video belongs to, got "
                f"{type(self.pipeline.series).__name__}.")
        from library.tools.overlay_mode import CONTAINERS, GEOMETRIES
        if self.pipeline.subtitle_overlay_geometry not in GEOMETRIES:
            errors.append(
                f"pipeline.subtitle_overlay_geometry must be one of "
                f"{list(GEOMETRIES)}, got "
                f"{self.pipeline.subtitle_overlay_geometry!r}.")
        if self.pipeline.subtitle_overlay_container not in CONTAINERS:
            errors.append(
                f"pipeline.subtitle_overlay_container must be one of "
                f"{list(CONTAINERS)}, got "
                f"{self.pipeline.subtitle_overlay_container!r}.")
        if self.pipeline.subtitle_typography is not None:
            from library.tools.subtitle_style import TYPOGRAPHY_KEYS
            declared = self.pipeline.subtitle_typography
            if not isinstance(declared, dict):
                errors.append(
                    f"pipeline.subtitle_typography must be a mapping of "
                    f"{list(TYPOGRAPHY_KEYS)}, got "
                    f"{type(declared).__name__}")
            else:
                unknown = sorted(set(declared) - set(TYPOGRAPHY_KEYS))
                if unknown:
                    errors.append(
                        f"pipeline.subtitle_typography declares {unknown}, "
                        f"which nothing reads. It takes "
                        f"{list(TYPOGRAPHY_KEYS)}.")
        if self.pipeline.creative_tasks is not None:
            # Structural only: that it is a list of mappings each naming
            # the task. Whether the role is complete, the handoff exists
            # and the prompt carries no floor is
            # library/tools/creative_tasks.py's refusal, where the project
            # root is available to read the handoff from.
            declared = self.pipeline.creative_tasks
            if not isinstance(declared, list):
                errors.append(
                    "pipeline.creative_tasks must be a list of task "
                    f"declarations, got {type(declared).__name__}.")
            else:
                for i, entry in enumerate(declared):
                    if not isinstance(entry, dict):
                        errors.append(
                            f"pipeline.creative_tasks[{i}] must be a "
                            f"mapping, got {type(entry).__name__}.")
                    elif not entry.get("name") or not isinstance(
                            entry.get("name"), str):
                        errors.append(
                            f"pipeline.creative_tasks[{i}] names no task: "
                            f"each entry needs a non-empty string `name`.")
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
        footage_root=source_data.get("footage_root", "") or "",
    )

    pipeline = PipelineConfig(
        brand_template=pipeline_data.get("brand_template", "default_brand"),
        delivery_format=pipeline_data.get("delivery_format", "") or "",
        framing_intent=pipeline_data.get("framing_intent"),
        subtitle_typography=pipeline_data.get("subtitle_typography"),
        subtitle_overlay_geometry=(
            pipeline_data.get("subtitle_overlay_geometry", "full")
            or "full"),
        subtitle_overlay_container=(
            pipeline_data.get("subtitle_overlay_container", "video")
            or "video"),
        creative_brief=pipeline_data.get("creative_brief", ""),
        attach_creative_brief=pipeline_data.get("attach_creative_brief"),
        creative_brief_inline=list(
            pipeline_data.get("creative_brief_inline", []) or []),
        # Top-level fallback: the runtime (`brief_reference.
        # project_series_identity`) reads `series` at the top level or
        # under `pipeline:`, so the schema must not drop a top-level
        # declaration the run would otherwise honour.
        series=(pipeline_data.get("series")
                if pipeline_data.get("series") is not None
                else data.get("series")) or "",
        creative_tasks=list(
            pipeline_data.get("creative_tasks", []) or []),
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
            # Only when declared. An empty `footage_root:` in every
            # project.yaml would read as a decision nobody made, and
            # "" and "not declared" are the same answer here anyway.
            **({"footage_root": config.source.footage_root}
               if config.source.footage_root else {}),
        },
        "pipeline": {
            "brand_template": config.pipeline.brand_template,
            "delivery_format": config.pipeline.delivery_format,
            # Only when declared: `framing_intent: null` in every
            # project.yaml would read as a decision nobody made, and 0.0
            # and "unset" are different answers here.
            **({} if config.pipeline.framing_intent is None
               else {"framing_intent": config.pipeline.framing_intent}),
            # Only when declared, for the same reason framing_intent is.
            **({} if config.pipeline.subtitle_typography is None
               else {"subtitle_typography":
                     dict(config.pipeline.subtitle_typography)}),
            # Only when non-default: `full`/`video` in every
            # project.yaml would read as decisions nobody made.
            **({} if config.pipeline.subtitle_overlay_geometry == "full"
               else {"subtitle_overlay_geometry":
                     config.pipeline.subtitle_overlay_geometry}),
            **({} if config.pipeline.subtitle_overlay_container == "video"
               else {"subtitle_overlay_container":
                     config.pipeline.subtitle_overlay_container}),
            "creative_brief": config.pipeline.creative_brief,
            # Omitted when undeclared: an explicit null in every
            # project.yaml reads as a decision nobody made, and the
            # undeclared reading is a real third state.
            **({} if config.pipeline.attach_creative_brief is None
               else {"attach_creative_brief":
                     bool(config.pipeline.attach_creative_brief)}),
            # Only when declared, for the same reason framing_intent is:
            # an empty list in every project.yaml reads as a decision
            # nobody made.
            **({} if not config.pipeline.creative_brief_inline
               else {"creative_brief_inline":
                     list(config.pipeline.creative_brief_inline)}),
            # Only when declared: an empty `series:` in every
            # project.yaml would read as a decision nobody made, and
            # the runtime reads "" and "not declared" as the same
            # absence anyway (brief_reference.project_series_identity).
            **({} if not config.pipeline.series
                else {"series": config.pipeline.series}),
            # Only when declared, for the same reason: an empty list in
            # every project.yaml reads as a decision nobody made.
            **({} if not config.pipeline.creative_tasks
               else {"creative_tasks":
                     [dict(t) for t in config.pipeline.creative_tasks]}),
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
