"""
project_layout.py - the one owner of where a project's output lands.

`paths.py` owns the REPO side: presets, assets, the Resolve support
directories, the external asset libraries.  Those are machine facts,
fixed at import, and they are the same for every project.

This module owns the PROJECT side, and a project folder is not a machine
fact.  It is an argument.  A project has inputs the captain owns and must
never be written to, outputs the pipeline may regenerate at will, run
state that only the runner may touch, scratch that is safe to throw away,
and a bounded store of backups.  None of that is expressible as a module
constant, so it lives here as one enumeration plus one object that binds
it to a folder.

    from library.tools.project_layout import ProjectLayout, Area

    layout = ProjectLayout(project_folder)
    out    = layout.write_dir(Area.PROSODY)          # creates it, guarded
    src    = layout.read_dir(Area.RAW)               # read-only, never created
    layout.write_dir(Area.RAW)                       # raises: RAW is an input

Why a step may not compose its own path
---------------------------------------
Fifteen steps used to join `project_folder` with a directory name of
their own choosing.  Nothing reconciled those names, so the scaffold in
`project_registry.PROJECT_DIRS` promised `pipeline_output/subtitles` while
the step that renders subtitles wrote `pipeline_output/subtitle_segments`,
and two steps wrote their analysis into `raw/`, the captain's own footage
directory.  Every path a step writes to now comes from `Area`, and an
`Area` that is not in the table raises rather than being created.

Discoverability
---------------
`ProjectLayout.write_readme()` renders `README-LAYOUT.md` from the same
table the code reads, so the folder explains itself without anyone
opening this file.  `manage_project.py new` writes it, and so does the
first output write of any run.
"""

from __future__ import annotations

import os
import re
import shutil
import time
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

# ── What kind of place a directory is ───────────────────────────────

class Kind(str, Enum):
    """Why a directory exists, and therefore who may write to it."""

    INPUT = "input"
    """The captain's own material.  The pipeline READS it and never
    writes to it.  Deleting it loses work that cannot be recomputed."""

    OUTPUT = "output"
    """Computed by the pipeline from the inputs.  Safe to delete: a
    re-run reproduces it (at the cost of the compute)."""

    DELIVERABLE = "deliverable"
    """What the run is FOR.  Computed, but the captain keeps it."""

    RUN_STATE = "run_state"
    """The runner's account of itself.  Written by the runner and the
    dashboard only, through `run_control.py` and the state loader."""

    BACKUP = "backup"
    """Bounded, automatic copies of run state.  See RETENTION below."""

    SCRATCH = "scratch"
    """Working files with no reader after the step that wrote them.
    Safe to discard at any moment, including mid-run."""

    UNSORTED = "unsorted"
    """Files whose purpose could not be determined.  Nothing writes here
    at run time; the migration puts admitted unknowns here rather than
    guessing, and rather than deleting them."""


# ── The table ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class AreaSpec:
    relpath: str
    kind: Kind
    purpose: str


class Area(str, Enum):
    """Every named place inside a project folder.

    A place that is not here does not exist.  Adding a directory means
    adding a row, which is what keeps the scaffold, the README and the
    steps from drifting apart.
    """

    # Inputs - the captain's material.
    PROJECT_ROOT = "project_root"
    RAW = "raw"
    MUSIC = "music"
    ASSETS = "assets"
    BRAND_ASSETS = "brand_assets"
    COMPOSITIONS = "compositions"

    # The output root, and the per-step JSON/summary export that lands
    # directly in it.
    OUTPUT_ROOT = "output_root"

    # Preflight enrichment of this project's footage.
    VISION_ANALYSIS = "vision_analysis"
    TEMPORAL_INDEX = "temporal_index"
    AUDIO_CACHE = "audio_cache"
    PROSODY = "prosody"
    SEGMENTATION = "segmentation"
    OCR = "ocr"

    # Chosen and generated media.
    MUSIC_ANALYSIS = "music_analysis"
    ACQUIRED_MEDIA = "acquired_media"
    SUBTITLE_SEGMENTS = "subtitle_segments"
    MOTION_GRAPHICS_SEGMENTS = "motion_graphics_segments"
    TIMED_TEXT_SEGMENTS = "timed_text_segments"
    FUSION_COMPS = "fusion_comps"
    CARRIERS = "carriers"

    # Review, QA and the agent/human channel.
    QA_FRAMES = "qa_frames"
    THUMBNAILS = "thumbnails"
    GATES = "gates"
    ANNOTATIONS = "annotations"
    MESSAGES = "messages"
    REVIEW = "review"
    LLM_REQUESTS = "llm_requests"
    LLM_RESPONSES = "llm_responses"
    LLM_RESPONSES_BAK = "llm_responses_bak"
    LOGS = "logs"

    # Deliverables, state, backups, scratch, admitted unknowns.
    MIGRATIONS = "migrations"
    EXPORTS = "exports"
    RUN_STATE = "run_state"
    BACKUPS = "backups"
    SCRATCH = "scratch"
    UNSORTED = "unsorted"


_OUT = "pipeline_output"

AREAS: dict[Area, AreaSpec] = {
    Area.PROJECT_ROOT: AreaSpec(
        ".", Kind.INPUT,
        "The project itself. project.yaml lives here and is read, never written by a step."),
    Area.RAW: AreaSpec(
        "raw", Kind.INPUT,
        "Source footage as the captain shot it. Read-only to the pipeline."),
    Area.MUSIC: AreaSpec(
        "music", Kind.INPUT,
        "Music the captain put here by hand. Read-only to the pipeline; "
        "tracks the pipeline downloads land in pipeline_output/acquired_media/."),
    Area.ASSETS: AreaSpec(
        "assets", Kind.INPUT,
        "Project-owned artwork and presets referenced by name from project.yaml."),
    Area.BRAND_ASSETS: AreaSpec(
        "brand_assets", Kind.INPUT,
        "Fonts and logos this series owns, staged into Remotion by prep_remotion."),
    Area.COMPOSITIONS: AreaSpec(
        "compositions", Kind.INPUT,
        "Project-owned Remotion compositions, staged verbatim. The engine renders "
        "them and never edits one."),

    Area.OUTPUT_ROOT: AreaSpec(
        _OUT, Kind.OUTPUT,
        "Everything the pipeline computes. Per-step <step_id>.json and "
        "<step_id>.summary.md land directly here; the rest is in the subdirectories below."),

    Area.VISION_ANALYSIS: AreaSpec(
        f"{_OUT}/vision_analysis", Kind.OUTPUT,
        "Per-clip vision profiles from the v3 pipeline (step 1.03)."),
    Area.TEMPORAL_INDEX: AreaSpec(
        f"{_OUT}/temporal_index", Kind.OUTPUT,
        "Per-clip transcription, word timings and face presence (step 1.04)."),
    Area.AUDIO_CACHE: AreaSpec(
        f"{_OUT}/audio_cache", Kind.OUTPUT,
        "16 kHz mono audio extracted from each clip for transcription (step 1.04)."),
    Area.PROSODY: AreaSpec(
        f"{_OUT}/prosody", Kind.OUTPUT,
        "Per-clip prosody profiles (step 1.05)."),
    Area.SEGMENTATION: AreaSpec(
        f"{_OUT}/segmentation_data", Kind.OUTPUT,
        "Per-clip object masks (step 1.06, not wired into the DAG)."),
    Area.OCR: AreaSpec(
        f"{_OUT}/ocr_data", Kind.OUTPUT,
        "Per-clip on-screen text (step 1.07, not wired into the DAG)."),

    Area.MUSIC_ANALYSIS: AreaSpec(
        f"{_OUT}/music", Kind.OUTPUT,
        "Tempo, beat grid and structure of the CHOSEN track (step 2.06)."),
    Area.ACQUIRED_MEDIA: AreaSpec(
        f"{_OUT}/acquired_media", Kind.OUTPUT,
        "Media the pipeline fetched rather than the captain supplying it - "
        "notably a music track downloaded by step 2.04."),
    Area.SUBTITLE_SEGMENTS: AreaSpec(
        f"{_OUT}/subtitle_segments", Kind.OUTPUT,
        "Rendered per-block subtitle overlays, ProRes 4444 with alpha (step 4.05)."),
    Area.MOTION_GRAPHICS_SEGMENTS: AreaSpec(
        f"{_OUT}/motion_graphics_segments", Kind.OUTPUT,
        "Rendered per-block motion graphics overlays (step 4.06)."),
    Area.TIMED_TEXT_SEGMENTS: AreaSpec(
        f"{_OUT}/timed_text_segments", Kind.OUTPUT,
        "Rendered timed-text cards, placed on V6 (step 4.06)."),
    Area.FUSION_COMPS: AreaSpec(
        f"{_OUT}/fusion_comps", Kind.OUTPUT,
        "Generated Fusion .comp files imported onto timeline clips (step 6.01)."),
    Area.CARRIERS: AreaSpec(
        f"{_OUT}/carriers", Kind.OUTPUT,
        "Transparent ProRes carriers that generator effects are composited onto."),

    Area.QA_FRAMES: AreaSpec(
        f"{_OUT}/qa_frames", Kind.OUTPUT,
        "Single frames pulled off a render to check an effect drew (step 6.02)."),
    Area.THUMBNAILS: AreaSpec(
        f"{_OUT}/thumbnails", Kind.OUTPUT,
        "Footage-library thumbnails for the dashboard."),
    Area.GATES: AreaSpec(
        f"{_OUT}/gates", Kind.OUTPUT,
        "Review-gate records: what paused, and how the reviewer answered."),
    Area.ANNOTATIONS: AreaSpec(
        f"{_OUT}/annotations", Kind.OUTPUT,
        "Per-step annotations captured on the dashboard."),
    Area.MESSAGES: AreaSpec(
        f"{_OUT}/messages", Kind.OUTPUT,
        "Dashboard/agent message log."),
    Area.REVIEW: AreaSpec(
        f"{_OUT}/review", Kind.OUTPUT,
        "The anchored review channel - channel.json holds every note and reply."),
    Area.LLM_REQUESTS: AreaSpec(
        f"{_OUT}/llm_requests", Kind.OUTPUT,
        "The prompt each hybrid/LLM step was handed, as it was sent."),
    Area.LLM_RESPONSES: AreaSpec(
        f"{_OUT}/llm_responses", Kind.OUTPUT,
        "What the model returned for each hybrid/LLM step."),
    Area.LLM_RESPONSES_BAK: AreaSpec(
        f"{_OUT}/llm_responses_bak", Kind.OUTPUT,
        "The previous response for a step being re-run, kept for comparison."),
    Area.LOGS: AreaSpec(
        f"{_OUT}/logs", Kind.OUTPUT,
        "Stdout/stderr of runs the dashboard launched, one file per run."),

    Area.MIGRATIONS: AreaSpec(
        f"{_OUT}/migrations", Kind.OUTPUT,
        "One record per time this folder was reorganised onto the layout: "
        "what moved, from where, to where, and how big it was. Reading one "
        "of these is how a reorganisation is undone."),
    Area.EXPORTS: AreaSpec(
        "exports", Kind.DELIVERABLE,
        "Finished renders and their QA reports. This is what the run is for."),
    Area.RUN_STATE: AreaSpec(
        ".", Kind.RUN_STATE,
        "pipeline_data.json, pipeline_run.json, pipeline.pid and pipeline.hold "
        "sit at the project root. Only the runner and the dashboard write them."),
    Area.BACKUPS: AreaSpec(
        f"{_OUT}/backups", Kind.BACKUP,
        "Automatic, bounded copies of pipeline_data.json - one per run, a "
        "fixed number kept (MAX_PIPELINE_DATA_BACKUPS). Hand-made backups "
        "from before this policy are in backups/pipeline_data/legacy/ and "
        "are never pruned."),
    Area.SCRATCH: AreaSpec(
        f"{_OUT}/scratch", Kind.SCRATCH,
        "Working files with no reader after the step that wrote them. Safe to "
        "delete at any moment, including during a run."),
    Area.UNSORTED: AreaSpec(
        f"{_OUT}/unsorted", Kind.UNSORTED,
        "Files whose purpose could not be established. Nothing writes here at "
        "run time. An admitted unknown, never a guess and never a deletion."),
}

# Areas a step may write to.  Everything else is read-only to a step.
WRITABLE_KINDS = frozenset({
    Kind.OUTPUT, Kind.DELIVERABLE, Kind.BACKUP, Kind.SCRATCH, Kind.UNSORTED,
})


# ── Files at the project root ───────────────────────────────────────
#
# `pipeline.pid`, `pipeline.hold` and `pipeline_run.json` are named by
# run_control.py, which owns the handbrake protocol end to end - the two
# processes speaking through those files must not learn their names from
# two places, so this module does not restate them.  What it does own is
# the state file the whole pipeline reads and the config file, plus the
# fact (in AREAS[Area.RUN_STATE]) that the root is for those and nothing
# else.

PIPELINE_DATA_FILE = "pipeline_data.json"
PROJECT_CONFIG_FILE = "project.yaml"
LAYOUT_README_FILE = "README-LAYOUT.md"


# ── Backup retention ────────────────────────────────────────────────
#
# The policy, and why it is these numbers:
#
#   WHERE   pipeline_output/backups/pipeline_data/
#           Under the output root, because a backup of computed state is
#           computed state.  Not at the project root, which is what
#           produced nine `.bak*` files sitting beside the thing they
#           back up with no way to tell which mattered.
#
#   WHEN    Once per RUN, not once per write.  `save_pipeline_state` runs
#           after every step, so a per-write policy would spend the whole
#           retention window on a single run and lose the state as it
#           stood before that run started - which is exactly what the
#           hand-made backups were protecting.
#
#   HOW MANY  Ten.  The nine hand-made backups on project 001 span
#           2026-08-10 to 2026-08-21, so ten covers about a fortnight at
#           the captain's real cadence.  At ~4 MB per copy that is ~40 MB
#           against a 6.8 GB project: bounded, and small enough that the
#           bound never has to be argued about again.
#
#   PRUNING The pruner only ever considers files it could have written
#           itself - `pipeline_data.<timestamp>[.label].json`.  A file
#           that does not match that pattern is left alone, which is what
#           makes `legacy/` safe.

BACKUP_SUBDIR = "pipeline_data"
LEGACY_BACKUP_SUBDIR = "legacy"
MAX_PIPELINE_DATA_BACKUPS = 10

_BACKUP_NAME_RE = re.compile(
    r"^pipeline_data\.(?P<stamp>\d{8}T\d{6})(?:\.(?P<label>[A-Za-z0-9_-]+))?\.json$"
)


class ProjectLayoutViolation(Exception):
    """A write was attempted somewhere the layout does not allow.

    Raised rather than silently redirected: a step that asks for a path
    outside the layout has a bug, and the bug is the thing to see.
    """


class ProjectLayout:
    """Where one project's files live.

    Construct it from a project folder and ask it for places.  It is the
    only thing in the pipeline that turns a project folder plus an
    intention into a path.
    """

    def __init__(self, project_folder) -> None:
        if not project_folder or not str(project_folder).strip():
            raise ProjectLayoutViolation(
                "ProjectLayout needs a project folder. An empty one used to "
                "fall back to the repo, which is how pipeline output ended up "
                "inside the checkout."
            )
        self.root = Path(project_folder).expanduser().resolve()

    # ── Places ──────────────────────────────────────────────────────

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"ProjectLayout({str(self.root)!r})"

    @staticmethod
    def spec(area: Area) -> AreaSpec:
        try:
            return AREAS[Area(area)]
        except (KeyError, ValueError):
            raise ProjectLayoutViolation(
                f"Unknown project area {area!r}. Every place inside a project "
                f"folder is a row in project_layout.AREAS; add a row rather "
                f"than composing a path."
            ) from None

    def _join(self, area: Area, parts: Iterable) -> Path:
        spec = self.spec(area)
        base = self.root if spec.relpath == "." else self.root / spec.relpath
        p = base
        for part in parts:
            if part in (None, ""):
                continue
            p = p / str(part)
        p = Path(os.path.normpath(str(p)))
        # A `..` in the caller's parts is the one way to leave the area
        # without naming another one.  It is always a mistake.
        try:
            p.relative_to(base if base.is_absolute() else base.resolve())
        except ValueError:
            raise ProjectLayoutViolation(
                f"{p} escapes {area.value} ({base}). Name the area you mean."
            ) from None
        return p

    def read_dir(self, area: Area) -> Path:
        """The directory for `area`.  Never created - reads may fail."""
        return self._join(area, ())

    def read_path(self, area: Area, *parts) -> Path:
        """A path inside `area`, for reading.  Nothing is created."""
        return self._join(area, parts)

    def write_dir(self, area: Area) -> Path:
        """The directory for `area`, created, with the write guard applied."""
        spec = self.spec(area)
        if spec.kind not in WRITABLE_KINDS:
            raise ProjectLayoutViolation(
                f"{area.value} is {spec.kind.value}, not writable by a step: "
                f"{spec.purpose}"
            )
        d = self._join(area, ())
        d.mkdir(parents=True, exist_ok=True)
        return d

    def write_path(self, area: Area, *parts) -> Path:
        """A path inside `area`, with its parent created.

        The ONLY way a step gets a path it may write to.  Asking for an
        input area raises; asking for an area that is not in the table
        raises.
        """
        self.write_dir(area)  # kind check + base mkdir
        p = self._join(area, parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    # ── Named files a lot of code wants ─────────────────────────────

    @property
    def output_root(self) -> Path:
        return self.write_dir(Area.OUTPUT_ROOT)

    @property
    def exports_dir(self) -> Path:
        return self.write_dir(Area.EXPORTS)

    @property
    def pipeline_data_path(self) -> Path:
        return self.root / PIPELINE_DATA_FILE

    @property
    def project_config_path(self) -> Path:
        return self.root / PROJECT_CONFIG_FILE

    def step_output_json(self, step_id: str) -> Path:
        return self.write_path(Area.OUTPUT_ROOT, f"{step_id}.json")

    def step_output_summary(self, step_id: str) -> Path:
        return self.write_path(Area.OUTPUT_ROOT, f"{step_id}.summary.md")

    def resolve_project_relative(self, path) -> Path:
        """Resolve a possibly-relative path recorded in state.

        Manifests and step outputs carry paths that are sometimes
        absolute and sometimes relative to the project folder.  A
        relative one is anchored HERE and nowhere else, so a step run
        from a different working directory reads the same file.
        """
        p = Path(str(path)).expanduser()
        return p if p.is_absolute() else (self.root / p)

    # ── The guard ───────────────────────────────────────────────────

    def assert_writable(self, path) -> Path:
        """Raise unless `path` is somewhere a step may write.

        For paths that arrive from outside the layout - a manifest key, a
        CLI flag, a step's own output - where the caller cannot name an
        Area.  Writable means: inside this project, and inside an area
        whose kind is in WRITABLE_KINDS.
        """
        p = Path(str(path)).expanduser()
        p = Path(os.path.normpath(str(p if p.is_absolute() else self.root / p)))
        try:
            rel = p.relative_to(self.root)
        except ValueError:
            raise ProjectLayoutViolation(
                f"{p} is outside the project folder {self.root}. Pipeline "
                f"output belongs to the project it came from."
            ) from None

        rel_posix = rel.as_posix()
        best: tuple[int, Area, AreaSpec] | None = None
        for area, spec in AREAS.items():
            if spec.relpath == ".":
                continue
            base = spec.relpath
            if rel_posix == base or rel_posix.startswith(base + "/"):
                depth = base.count("/") + 1
                if best is None or depth > best[0]:
                    best = (depth, area, spec)

        if best is None:
            raise ProjectLayoutViolation(
                f"{p} is at the project root, which holds project.yaml and run "
                f"state only. Name an area from project_layout.Area."
            )
        _, area, spec = best
        if spec.kind not in WRITABLE_KINDS:
            raise ProjectLayoutViolation(
                f"{p} is inside {area.value} ({spec.kind.value}), which the "
                f"pipeline must not write to: {spec.purpose}"
            )
        return p

    # ── Backups ─────────────────────────────────────────────────────

    def backup_dir(self) -> Path:
        d = self.write_path(Area.BACKUPS, BACKUP_SUBDIR)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def legacy_backup_dir(self) -> Path:
        d = self.write_path(Area.BACKUPS, BACKUP_SUBDIR, LEGACY_BACKUP_SUBDIR)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def backup_pipeline_data(self, label: str = "", now=None) -> Path | None:
        """Copy the CURRENT pipeline_data.json into the backup store.

        Returns the copy, or None when there is nothing to back up.
        Prunes to MAX_PIPELINE_DATA_BACKUPS afterwards, so the store is
        bounded without anyone remembering to tidy it.
        """
        src = self.pipeline_data_path
        if not src.is_file():
            return None
        stamp = time.strftime("%Y%m%dT%H%M%S", time.localtime(now))
        suffix = f".{_slug(label)}" if label else ""
        dest = self.backup_dir() / f"pipeline_data.{stamp}{suffix}.json"
        n = 1
        while dest.exists():
            n += 1
            dest = self.backup_dir() / f"pipeline_data.{stamp}{suffix}-{n}.json"
        shutil.copy2(src, dest)
        self.prune_backups()
        return dest

    def automatic_backups(self) -> list[Path]:
        """Backups this policy wrote, newest last.

        A file the pattern does not match was not written by the policy
        and is never a candidate for pruning.
        """
        d = self.read_path(Area.BACKUPS, BACKUP_SUBDIR)
        if not d.is_dir():
            return []
        matched = [p for p in d.iterdir()
                   if p.is_file() and _BACKUP_NAME_RE.match(p.name)]
        return sorted(matched, key=lambda p: p.name)

    def prune_backups(self, keep: int = MAX_PIPELINE_DATA_BACKUPS) -> list[Path]:
        """Delete the oldest automatic backups beyond `keep`.  Returns them."""
        existing = self.automatic_backups()
        doomed = existing[:max(0, len(existing) - keep)]
        for p in doomed:
            p.unlink()
        return doomed

    # ── Discoverability ─────────────────────────────────────────────

    def ensure(self) -> ProjectLayout:
        """Create the output side of the layout and refresh the README.

        Input areas are NOT created: a project with no `music/` should
        not sprout an empty one, and creating an input directory is the
        first half of writing to it.
        """
        for spec in AREAS.values():
            if spec.kind in WRITABLE_KINDS and spec.relpath != ".":
                (self.root / spec.relpath).mkdir(parents=True, exist_ok=True)
        self.write_readme()
        return self

    def write_readme(self) -> Path:
        p = self.root / LAYOUT_README_FILE
        text = self.describe()
        if not p.exists() or p.read_text(encoding="utf-8") != text:
            p.write_text(text, encoding="utf-8")
        return p

    def describe(self) -> str:
        """The layout, as prose, rendered from the same table the code reads."""
        by_kind: dict[Kind, list[tuple[Area, AreaSpec]]] = {}
        for area, spec in AREAS.items():
            by_kind.setdefault(spec.kind, []).append((area, spec))

        order = [Kind.INPUT, Kind.RUN_STATE, Kind.OUTPUT,
                 Kind.DELIVERABLE, Kind.BACKUP, Kind.SCRATCH, Kind.UNSORTED]
        headings = {
            Kind.INPUT: ("Inputs - the captain's material",
                         ("Read by the pipeline, never written to. "
                          "`ProjectLayout.write_dir` raises for these.")),
            Kind.RUN_STATE: ("Run state",
                             "The runner's account of itself, at the project root."),
            Kind.OUTPUT: ("Output - everything the pipeline computes",
                          "Safe to delete; a re-run reproduces it."),
            Kind.DELIVERABLE: ("Deliverables",
                               "What the run is for. Computed, but kept."),
            Kind.BACKUP: ("Backups",
                          (f"Automatic and bounded: one per run, newest "
                           f"{MAX_PIPELINE_DATA_BACKUPS} kept.")),
            Kind.SCRATCH: ("Scratch",
                           "Safe to discard at any moment, including mid-run."),
            Kind.UNSORTED: ("Unsorted",
                            "Admitted unknowns. Nothing writes here at run time."),
        }

        lines = [
            "# What is in this folder",
            "",
            "This file is generated from `library/tools/project_layout.py`, which is",
            "the one place in the pipeline that decides where a project's files go.",
            "Do not edit it by hand - edit the table and it regenerates.",
            "",
            "Every directory below has one job. If something is not listed here, the",
            "pipeline did not put it there.",
            "",
        ]
        for kind in order:
            rows = by_kind.get(kind, [])
            if not rows:
                continue
            title, blurb = headings[kind]
            lines += [f"## {title}", "", blurb, ""]
            for area, spec in sorted(rows, key=lambda r: r[1].relpath):
                shown = "(project root)" if spec.relpath == "." else f"`{spec.relpath}/`"
                lines.append(f"- {shown} - {spec.purpose}")
            lines.append("")

        lines += [
            "## Rules this folder is kept to",
            "",
            "- A step never composes a path. It names an `Area` and the layout owner",
            "  returns the path, so no two steps can disagree about where something goes.",
            "- Inputs are structurally protected: asking the layout to write into",
            "  `raw/`, `music/`, `assets/`, `brand_assets/` or `compositions/` raises.",
            f"- `pipeline_output/backups/{BACKUP_SUBDIR}/` holds one backup of",
            f"  `pipeline_data.json` per run, newest {MAX_PIPELINE_DATA_BACKUPS} kept,",
            "  pruned automatically. Hand-made backups from before that policy are in",
            f"  `{LEGACY_BACKUP_SUBDIR}/` beside them and are never pruned.",
            "- `pipeline_output/unsorted/` is where a file goes when nobody could say",
            "  what it was. An admitted unknown beats a confident wrong guess, and",
            "  beats deleting it.",
            "",
        ]
        return "\n".join(lines)


def _slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", str(text)).strip("-")
    return s[:40] or "run"


# ── Convenience ─────────────────────────────────────────────────────

def layout_for(project_folder) -> ProjectLayout:
    """`ProjectLayout(project_folder)`, spelled as a function.

    Steps read `project_folder` out of a dict, so this reads better at
    the call site than a constructor does.
    """
    return ProjectLayout(project_folder)


def output_dir(project_folder, area: Area = Area.OUTPUT_ROOT) -> str:
    """A writable directory as a string, for callers still on `os.path`."""
    return str(ProjectLayout(project_folder).write_dir(area))
