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

Product format versioning lives in `library/tools/project_format.py`:
this schema carries the `project_format_version` key it defines.
"""

import os
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from library.tools.project_format import (
    FORMAT_VERSION_KEY,
    PROJECT_FORMAT_VERSION,
)

# A language code: two or three letters, optional region (`en`,
# `es`, `pt-BR`). Permissive on purpose - the transcriber, not the
# schema, knows which codes it can hear.
_LANGUAGE_RE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z]{2,4})?$")


class ProjectStatus(str, Enum):
    """Lifecycle status of a video project."""
    DRAFT = "draft"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    COMPLETE = "complete"
    ARCHIVED = "archived"


@dataclass
class SourceConfig:
    """Describes the source media characteristics.

    `type` and `resolution` used to live here (`iphone_mov`,
    `1080x1920`) and were DROPPED: nothing ever read them. The
    catalog measures both off the footage (`project_fps`,
    `source_resolution`, `clip_catalog[].width/height`), so a
    declared duplicate could only rot. Project files that still
    carry them read fine - unknown keys are ignored - and new
    projects stop writing them.
    """

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

    program_stream: int | None = None
    """The 1-based audio channel ordinal that is this footage's program
    mix - the ONE channel that reaches a timeline.

    Field-recorder footage (the captain's MXF: four mono streams, one
    mix, two ISOs, one empty) plays every embedded channel unless a
    build is told which one is the mix. Measured 2026-09-19 across all
    seven geo-podcast sources: CH1 is the mix on every file, CH2 is
    empty, CH3/CH4 are ISOs - so that project declares `1`. Which
    channel is the main one is a property of the footage: declared
    here, or measured (`measure_program_stream`), never a bare constant
    in the code. See `library/steps/step_1_02_catalog_footage/step.py`.

    None means undeclared: the catalog refuses a multi-stream source
    rather than guessing, and the build refuses to place its audio."""

    measure_program_stream: bool = False
    """When true and `program_stream` is undeclared, the catalog measures
    the footage (per-stream loudness, whole file) and records the
    uniquely loudest stream as the program mix with its levels as
    evidence. An ambiguous or all-silent measurement refuses like an
    undeclared one - measurement decides only when it is decisive.
    A declaration always wins over a measurement."""

    language: str = "en"
    """The language spoken in the footage, as a BCP-47 code (`en`,
    `es`, `de`, ...). Step 1.04 transcribes and aligns in this
    language instead of forcing English. Undeclared means English,
    which is what every project transcribed before the setting
    existed."""

    shape: str = ""
    """What this video is built from: `speech`, `music`, `both` or
    `picture-led`. Undeclared ("") means the historical shape -
    speech-led - and reads the same everywhere. The spine is the
    reader; intake only scaffolds the declaration."""

    speakers: list | None = None
    """Who speaks in this footage, as `[{name, role?}]`. The COUNT is
    what the pipeline reads: reel selection no longer requires two
    voices, a one-speaker project is cut as a monologue, and a
    declared-empty list (`[]`) is a zero-speaker project - music,
    montage - where nothing spoken is selected. None means
    undeclared, which reads as the historical two. Roles are free
    strings the model weighs; no deterministic mapping reads them.

    None and `[]` are different answers and round-trip as such: an
    absent key means nobody said, an empty list means nobody speaks."""

    def validate_source(self) -> list[str]:
        """Refusals for the three intake declarations, by name."""
        errors = []
        if (not isinstance(self.language, str)
                or not _LANGUAGE_RE.match(self.language.strip())):
            errors.append(
                "source.language must be a language code like `en` or "
                f"`es`, got {self.language!r}.")
        if self.shape not in ("", "speech", "music", "both",
                              "picture-led"):
            errors.append(
                "source.shape must be one of speech, music, both or "
                f"picture-led, got {self.shape!r}.")
        if self.speakers is not None:
            if not isinstance(self.speakers, list):
                errors.append(
                    "source.speakers must be a list of {name, role?} "
                    f"entries, got {type(self.speakers).__name__}.")
            else:
                for i, entry in enumerate(self.speakers):
                    if not isinstance(entry, dict):
                        errors.append(
                            f"source.speakers[{i}] must be a mapping "
                            f"with a `name`, got "
                            f"{type(entry).__name__}.")
                        continue
                    name = entry.get("name")
                    if (not isinstance(name, str) or not name.strip()):
                        errors.append(
                            f"source.speakers[{i}] names no speaker: "
                            f"each entry needs a non-empty string `name`.")
                    role = entry.get("role")
                    if role is not None and not isinstance(role, str):
                        errors.append(
                            f"source.speakers[{i}] carries role "
                            f"{role!r}, which must be a string.")
                    unknown = sorted(set(entry) - {"name", "role"})
                    if unknown:
                        errors.append(
                            f"source.speakers[{i}] declares {unknown}, "
                            f"which nothing reads. A speaker takes "
                            f"`name` and an optional `role`.")
        return errors


@dataclass
class PipelineConfig:
    """Pipeline-specific configuration for this project."""
    # The brand this project renders under: a NAME the project carries a
    # `brand.json` for, or "" for none.  The product ships no templates
    # (captain, 2026-09-21) - an empty declaration loads NOTHING.
    brand_template: str = ""
    # Per-project override of the frame the product ships in. Empty means
    # "take the brand template's", which in turn defaults to vertical.
    # A name from library/tools/delivery_format.DELIVERY_FORMATS; an
    # unknown one raises rather than quietly reverting to the default.
    delivery_format: str = ""
    # The deliver verb's preset and file naming (`deliver-reel` reads
    # them through library/tools/reel_deliver.py). Both UNDECLARED by
    # default: an empty preset / empty naming means the captain has not
    # given the word, and the verb reports that rather than presenting
    # a fallback as a decision. `deliver_preset` takes `format` and
    # `codec` only - anything else is refused as a declaration nothing
    # reads. `deliver_naming` is a filename optionally carrying
    # `{timeline}` and `{ext}`.
    deliver_preset: dict | None = None
    deliver_naming: str = ""
    # Per-project override of how much of that frame the picture fills:
    # 0.0 letterbox, 1.0 fill. None means "take the brand template's",
    # which in turn defaults to fill. See library/tools/framing_intent.py.
    framing_intent: float | None = None
    # Per-project caption typography, in the same {font, size, weight}
    # shape a brand template's `style.typography` uses, overriding it key
    # by key. None means "take the template's". See
    # library/tools/subtitle_style.py, "And the PROJECT".
    subtitle_typography: dict | None = None
    # How caption overlays are carried: the delivery frame or only the
    # drawn bounds (`subtitle_overlay_geometry`: full|tight), stitched
    # video or the frame sequence itself
    # (`subtitle_overlay_container`: video|frames). Undeclared means
    # today's path. See library/tools/overlay_mode.py, which is what
    # the steps read - these fields exist so manage_project.py
    # validates and round-trips the keys rather than dropping them.
    subtitle_overlay_geometry: str = "full"
    subtitle_overlay_container: str = "video"
    # How motion-graphics overlays are carried: the delivery frame or
    # only the drawn union (`motion_graphics_overlay_geometry`:
    # full|tight). Undeclared means today's path, and the key is
    # SEPARATE from the caption one on purpose - see
    # library/tools/overlay_mode.py.
    motion_graphics_overlay_geometry: str = "full"
    # Which graphics engine draws the programmatic pictures
    # (`graphics_renderer`: remotion|hyperframes). Undeclared means
    # Remotion, which is today's path exactly - nothing renders
    # differently unless this names hyperframes. The project wins over
    # the user's PIPELINE_GRAPHICS_RENDERER, because the look of a
    # video belongs to the video. See
    # library/tools/graphics_renderer.py, which is what the steps
    # read - this field exists so manage_project.py validates and
    # round-trips the key rather than dropping it.
    graphics_renderer: str = "remotion"
    # The punched-in TV-frame look, in the {asset, punch_in, power}
    # shape a brand template's `style.tv_frame` uses and taking
    # precedence over it. None means "take the template's", which in
    # turn means no look unless the template declares one. The field
    # exists so manage_project.py validates and round-trips the key
    # rather than dropping it - `tv_frame.resolve_tv_frame` reads the
    # raw pipeline block, so a dropped key here is a look the editor
    # declared, the run honoured and the schema denied.
    tv_frame: dict | None = None
    # The project's safe-zone POLICY: which platforms and phones it is
    # made for, the room left round the apps' UI, and its own keep-out
    # rules. None means every platform on every modelled phone. Shape
    # and reader: library/tools/safe_zone_policy.py - this field exists
    # so manage_project.py validates and round-trips the key rather
    # than dropping it.
    safe_zones: dict | None = None
    creative_brief: str = ""  # path to markdown creative brief (relative to project root)
    # Whether that brief is ATTACHED to the planning prompts. Three
    # states, and None is not "false": an undeclared key means the PATH
    # is the declaration. See library/tools/brief_attachment.py, which
    # is what the runner reads - this field exists so manage_project.py
    # validates and round-trips the key rather than dropping it.
    attach_creative_brief: bool | None = None
    # Headings of the brief this project wants carried INLINE rather than
    # reached by path.  Clause 3 of the rule in
    # library/tools/brief_reference.py: which sections are about THIS
    # video is a judgement belonging to whoever owns the video, so the
    # engine keeps no default list.  Empty means "the map is enough".
    creative_brief_inline: list[str] = field(default_factory=list)
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
    creative_tasks: list[dict] = field(default_factory=list)
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

    # Product format version (`library/tools/project_format.py`).
    # Read off `project.yaml` (absent means 0, the unversioned legacy)
    # and always written back, so every project file names the format
    # it is in.  A value outside the engine's supported range never
    # reaches here: `_dict_to_project_config` refuses it first.  The
    # default is the constant, not a literal, so the schema can never
    # disagree with the registry about what this engine writes.
    project_format_version: int = PROJECT_FORMAT_VERSION

    source: SourceConfig = field(default_factory=SourceConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    resolve: ResolveConfig = field(default_factory=ResolveConfig)

    tags: list[str] = field(default_factory=list)
    description: str = ""

    # The Ren build that created the project (`ren --version`'s line:
    # version plus commit/channel metadata). Stamped by
    # `project_registry.create_project`; every executable, project and
    # support report then names the same build (P0 "Version the actual
    # product"). "" means the project predates the stamp - old files
    # read fine, and the key is omitted when serializing one.
    ren_version: str = ""

    # Runtime - not serialized to YAML
    _project_root: Path | None = field(default=None, repr=False)

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
        if (isinstance(self.project_format_version, bool)
                or not isinstance(self.project_format_version, int)):
            errors.append(
                "project_format_version must be an integer, got "
                f"{self.project_format_version!r}.")
        if not self.name:
            errors.append("'name' is required")
        if not self.slug:
            errors.append("'slug' is required")
        if not isinstance(self.ren_version, str):
            errors.append(
                f"'ren_version' must be the Ren build string, got "
                f"{type(self.ren_version).__name__}.")
        if not self.slug.replace("-", "").replace("_", "").isalnum():
            errors.append(f"'slug' must be alphanumeric with dashes/underscores: {self.slug}")
        try:
            ProjectStatus(self.status) if isinstance(self.status, str) else self.status
        except ValueError:
            errors.append(f"Invalid status: {self.status}")
        if self.source.program_stream is not None:
            # A channel ordinal names a real channel: positive int, no
            # guessing. A 0 or a "CH1" string would silently select
            # nothing (or the wrong thing) downstream.
            if (not isinstance(self.source.program_stream, bool)
                    and isinstance(self.source.program_stream, int)
                    and self.source.program_stream >= 1):
                pass
            else:
                errors.append(
                    "source.program_stream must be a 1-based audio "
                    "channel ordinal (a positive integer), got "
                    f"{self.source.program_stream!r}.")
        if not isinstance(self.source.measure_program_stream, bool):
            errors.append(
                "source.measure_program_stream must be true or false, "
                f"got {self.source.measure_program_stream!r}.")
        errors.extend(self.source.validate_source())
        if self.pipeline.delivery_format:
            from library.tools.delivery_format import DELIVERY_FORMATS
            if self.pipeline.delivery_format not in DELIVERY_FORMATS:
                errors.append(
                    f"Invalid pipeline.delivery_format: "
                    f"{self.pipeline.delivery_format} must be one of "
                    f"{sorted(DELIVERY_FORMATS)}"
                )
        if self.pipeline.deliver_preset is not None:
            # Structural only: a mapping of format/codec strings. Empty
            # (or absent) means undeclared, which is valid - the verb
            # derives its fallback and says so.
            preset = self.pipeline.deliver_preset
            if not isinstance(preset, dict):
                errors.append(
                    f"pipeline.deliver_preset must be a mapping of "
                    f"format/codec, got {type(preset).__name__}.")
            else:
                from library.tools.reel_deliver import DELIVER_PRESET_KEYS
                unknown = sorted(set(preset) - set(DELIVER_PRESET_KEYS))
                if unknown:
                    errors.append(
                        f"pipeline.deliver_preset declares {unknown}, "
                        f"which nothing reads. It takes "
                        f"{list(DELIVER_PRESET_KEYS)}.")
                for key, value in preset.items():
                    if not isinstance(value, str) or not value.strip():
                        errors.append(
                            f"pipeline.deliver_preset[{key!r}] must be a "
                            f"non-empty string, got {value!r}.")
        if self.pipeline.deliver_naming is not None and not isinstance(
                self.pipeline.deliver_naming, str):
            errors.append(
                f"pipeline.deliver_naming must be a string, got "
                f"{type(self.pipeline.deliver_naming).__name__}.")
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
        if self.pipeline.motion_graphics_overlay_geometry not in GEOMETRIES:
            errors.append(
                f"pipeline.motion_graphics_overlay_geometry must be one of "
                f"{list(GEOMETRIES)}, got "
                f"{self.pipeline.motion_graphics_overlay_geometry!r}.")
        from library.tools.graphics_renderer import ENGINES
        if self.pipeline.graphics_renderer not in ENGINES:
            errors.append(
                f"pipeline.graphics_renderer must be one of "
                f"{list(ENGINES)}, got "
                f"{self.pipeline.graphics_renderer!r}.")
        if self.pipeline.safe_zones is not None:
            from library.tools.safe_zone_policy import (
                SafeZonePolicyError,
                parse_policy,
            )
            try:
                parse_policy(self.pipeline.safe_zones)
            except SafeZonePolicyError as exc:
                errors.append(str(exc))
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


def _parse_language(raw) -> object:
    """`source.language` off YAML: normalised, never defaulted blind.

    Missing means English. Anything else passes through as written -
    normalised where it is a string - so `validate_source` refuses a
    malformed declaration by name instead of this quietly repairing
    it into English."""
    if raw is None:
        return "en"
    if isinstance(raw, str) and raw.strip():
        return raw.strip().lower()
    return raw


def _parse_speakers(raw) -> object:
    """`source.speakers` off YAML: None stays None, a list stays a list.

    A non-list passes through untouched so `validate_source` refuses
    it by name. `list("Bob")` would be `["B", "o", "b"]`, which is
    exactly the silent repair this avoids."""
    if raw is None:
        return None
    if isinstance(raw, list):
        return [dict(e) if isinstance(e, dict) else e for e in raw]
    return raw


def _parse_format_version(data: dict, where) -> object:
    """The format version off a raw `project.yaml` mapping.

    Absent reads as 0, the unversioned legacy - every project that
    predates versioning opens rather than failing on a key it never
    declared.  A non-integer passes through untouched so `validate`
    refuses it by name; an out-of-range integer refuses here.
    """
    raw = data.get(FORMAT_VERSION_KEY, 0)
    if isinstance(raw, bool) or not isinstance(raw, int):
        return raw
    from library.tools.project_format import validate_format_version
    return validate_format_version(raw, where)


def _dict_to_project_config(data: dict,
                            project_root: Path | None = None) -> ProjectConfig:
    """Convert a raw dict (from YAML) to a ProjectConfig dataclass."""
    source_data = data.get("source", {})
    pipeline_data = data.get("pipeline", {})
    resolve_data = data.get("resolve", {})
    format_version = _parse_format_version(data, project_root)

    source = SourceConfig(
        fps=source_data.get("fps", 30),
        footage_root=source_data.get("footage_root", "") or "",
        program_stream=source_data.get("program_stream"),
        measure_program_stream=source_data.get(
            "measure_program_stream", False),
        language=_parse_language(source_data.get("language")),
        shape=(source_data.get("shape", "") or "").strip().lower(),
        speakers=_parse_speakers(source_data.get("speakers")),
    )

    pipeline = PipelineConfig(
        brand_template=pipeline_data.get("brand_template", ""),
        delivery_format=pipeline_data.get("delivery_format", "") or "",
        deliver_preset=pipeline_data.get("deliver_preset"),
        deliver_naming=pipeline_data.get("deliver_naming", "") or "",
        framing_intent=pipeline_data.get("framing_intent"),
        subtitle_typography=pipeline_data.get("subtitle_typography"),
        subtitle_overlay_geometry=(
            pipeline_data.get("subtitle_overlay_geometry", "full")
            or "full"),
        subtitle_overlay_container=(
            pipeline_data.get("subtitle_overlay_container", "video")
            or "video"),
        motion_graphics_overlay_geometry=(
            pipeline_data.get("motion_graphics_overlay_geometry", "full")
            or "full"),
        graphics_renderer=(
            pipeline_data.get("graphics_renderer", "remotion")
            or "remotion"),
        tv_frame=pipeline_data.get("tv_frame"),
        safe_zones=pipeline_data.get("safe_zones"),
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
        project_format_version=format_version,
        source=source,
        pipeline=pipeline,
        resolve=resolve,
        tags=data.get("tags", []),
        description=data.get("description", ""),
        ren_version=data.get("ren_version", "") or "",
    )
    config._project_root = project_root
    return config


def project_config_to_dict(config: ProjectConfig) -> dict:
    """Serialize a ProjectConfig to a dict suitable for YAML output."""
    return {
        # First, and always: the product format version is a fact
        # about the file, not a declaration anyone chose.  Omitting it
        # on defaults would unstamp every project the next write
        # touches.
        FORMAT_VERSION_KEY: config.project_format_version,
        "name": config.name,
        "slug": config.slug,
        "client": config.client,
        "created": config.created,
        "status": config.status.value if isinstance(config.status, ProjectStatus) else config.status,
        # Only when stamped: a project that predates the stamp keeps a
        # file without the key, and "" and "not declared" read the same.
        **({} if not config.ren_version
           else {"ren_version": config.ren_version}),
        "source": {
            "fps": config.source.fps,
            # Only when declared. An empty `footage_root:` in every
            # project.yaml would read as a decision nobody made, and
            # "" and "not declared" are the same answer here anyway.
            **({"footage_root": config.source.footage_root}
               if config.source.footage_root else {}),
            # Only when non-default: every project before the setting
            # transcribed English, so writing `language: en` everywhere
            # would read as decisions nobody made.
            **({"language": config.source.language}
               if config.source.language != "en" else {}),
            # Only when declared: "" is the historical speech-led
            # shape, and writing it everywhere would claim an answer
            # nobody gave.
            **({"shape": config.source.shape}
               if config.source.shape else {}),
            # None OMITTED, [] WRITTEN: undeclared and declared-zero
            # are different answers (see SourceConfig.speakers).
            **({"speakers": [dict(s) for s in config.source.speakers]}
               if config.source.speakers is not None else {}),
            # Only when declared: an undeclared program mix must stay
            # absent so downstream reads it as "nothing chosen", never
            # as channel 1.
            **({"program_stream": config.source.program_stream}
               if config.source.program_stream is not None else {}),
            **({"measure_program_stream":
                config.source.measure_program_stream}
               if config.source.measure_program_stream else {}),
        },
        "pipeline": {
            "brand_template": config.pipeline.brand_template,
            "delivery_format": config.pipeline.delivery_format,
            # Only when declared: an empty preset/naming in every
            # project.yaml would read as decisions nobody made.
            **({} if not config.pipeline.deliver_preset
               else {"deliver_preset":
                     dict(config.pipeline.deliver_preset)}),
            **({} if not config.pipeline.deliver_naming
               else {"deliver_naming":
                     config.pipeline.deliver_naming}),
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
            **({} if config.pipeline.motion_graphics_overlay_geometry == "full"
               else {"motion_graphics_overlay_geometry":
                     config.pipeline.motion_graphics_overlay_geometry}),
            # Only when non-default: `remotion` in every project.yaml
            # would read as a decision nobody made.
            **({} if config.pipeline.graphics_renderer == "remotion"
               else {"graphics_renderer":
                     config.pipeline.graphics_renderer}),
            # Only when declared: the default policy is every platform
            # on every phone, and writing it out would read as a choice.
            **({} if config.pipeline.safe_zones is None
               else {"safe_zones": dict(config.pipeline.safe_zones)}),
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


def load_project_config(yaml_path: str | Path, *,
                        migrate_format: bool = True) -> ProjectConfig:
    """Load and validate a project.yaml file.

    Uses PyYAML if available, falls back to a simple parser for basic YAML.
    """
    yaml_path = Path(yaml_path)
    if not yaml_path.exists():
        raise FileNotFoundError(f"Project config not found: {yaml_path}")

    if migrate_format:
        from library.tools.project_format import ensure_project_format
        ensure_project_format(yaml_path.parent)

    try:
        import yaml
        with open(yaml_path, encoding="utf-8") as f:
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
