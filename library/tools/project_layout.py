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


Rules relocated from AGENTS.md 8
--------------------------------
These are the engine's rules for this module.  They lived in
AGENTS.md until it was split by subsystem; the wording is unchanged,
so each rule is findable by its own words, and AGENTS.md 8
keeps the headline and points here.

**One module owns the project-side layout: `library/tools/project_layout.py`.**
`paths.py` owns the REPO and the MACHINE; this owns one PROJECT, which is a folder passed in rather than a constant.

**`pipeline_output/steps/` IS the pipeline.** One directory per step, in run order; the captain audits by walking the folder.
- `STEPS` is the ordered table of every step, in DAG order, with the directory it owns. `AreaSpec.step` names the owning step for a step-owned area.
- Directory names use the STEP number, the same spelling `library/steps/` and every "step 1.04" citation uses; `README-LAYOUT.md` renders true run order, which diverges from that sort in two places.
- Each step directory holds `output.json` and `summary.md` (what `step_exporter` writes) plus whatever files the step produced.
- **A step writes only inside its own directory.** Pass `step=` to `write_dir`/`write_path` and another step's area raises; `assert_step_owns` is the same guard for a path from outside the layout.
- **Not everything is a step's product.** `logs/`, `gates/`, `review/`, `llm_*/`, `backups/`, `migrations/`, `provenance/`, `scratch/`, `unsorted/` and `exports/` stay at project level.
- `exports/` is the one area TWO steps legitimately write: 6.01 the render and 6.02 the QA report. `produced_by` names both, and provenance leaves `step_id` None rather than picking one.
- Every place inside a project folder is a row in `AREAS`, keyed by `Area`. A place that is not a row does not exist, and asking for one raises.
- **A step never composes a project path.** It names an `Area` and gets a path via `write_dir`/`write_path`/`read_dir`/`read_path`. `write_dir`/`write_path` to write, `read_dir`/`read_path` to read, `resolve_project_relative` for a path recorded in state.
- **Inputs are structurally protected.** `raw/`, `music/`, `assets/`, `brand_assets/`, `compositions/`, `external/state/`, `external/declarations/` and `profiles/` are `Kind.INPUT`: `write_dir`/`write_path` raise for them, `ensure()` does not create them, and `assert_writable` refuses any path underneath. Outside the project, at the bare project root, or inside an input area all raise.
- **A project explains itself.** `ensure()` renders `README-LAYOUT.md` from the same table the code reads - the steps in run order, what each reads and what each writes - and runs on `manage_project.py new` and at step 1.01 of every run.
- **`classification.per_clip_artifacts` names an AREA, not a directory**: `{area:temporal_index}/{clip_id}.json`.
- The scaffold is not a second list.
- Anything the pipeline FETCHES rather than computes - a downloaded music track - is output, and goes to `Area.ACQUIRED_MEDIA` under the step that fetched it, not into `music/`.

**Backups of `pipeline_data.json` are automatic and bounded.**
`pipeline_output/backups/pipeline_data/`, one per RUN, newest `MAX_PIPELINE_DATA_BACKUPS` kept.
- The pruner only ever considers files matching its own naming pattern, so a hand-made backup dropped in beside them is never deleted. Pre-policy backups live in `backups/pipeline_data/legacy/`.
- One per run, not one per save: `save_pipeline_state` runs after every step, and the thing worth keeping is the state as it stood BEFORE a run.
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
    """The runner's account of itself.  Written by the runner and its
    tooling only, through `run_control.py` and the state loader."""

    BACKUP = "backup"
    """Bounded, automatic copies of run state.  See RETENTION below."""

    SCRATCH = "scratch"
    """Working files with no reader after the step that wrote them.
    Safe to discard at any moment, including mid-run."""

    CAPTURED = "captured"
    """The captain's own material, RECORDED off somewhere the pipeline
    cannot re-read - a note typed onto a DaVinci Resolve timeline.  Like
    an input it is irreproducible, and unlike an input the pipeline is
    what writes it.  A re-run must never delete it: the thing that
    destroys the original is precisely a re-run."""

    UNSORTED = "unsorted"
    """Files whose purpose could not be determined.  Nothing writes here
    at run time; the migration puts admitted unknowns here rather than
    guessing, and rather than deleting them."""


# ── The steps, in the order the DAG runs them ───────────────────────
#
# The directory name is the step's own number, the same spelling
# `library/steps/` uses and the same one every "step 1.04" citation in
# AGENTS.md uses.  That makes `ls pipeline_output/steps/` sort into
# pipeline order - with TWO inversions, because the DAG runs
# `music_analysis` (2.06) before `mesh_spine` (2.05) and
# `compile_manifest` (5.04) before `creative_cohesion` (5.03).  The table
# below is in true run order and the generated README renders from it, so
# the order the captain READS is exact even where the sort is not.
#
# Numbering by step rather than by DAG position is deliberate: a
# topological position renumbers every later directory the moment a step
# is inserted, and the step number is the vocabulary the docs and the
# code comments already share.

@dataclass(frozen=True)
class StepDir:
    node_id: str
    """The DAG node id. Four are shortened: `step_1_01_scan_project` is
    the node `scan`, and catalog / validate / mesh_spine are the same
    shape. The id is what `pipeline_data.json` keys everything by."""

    dirname: str
    """`1_04_temporal_index` - the step directory, named for the step."""

    wired: bool = True
    """False for a step that is implemented but that no DAG node runs.
    Every step directory currently has a node (`prosody_analysis`
    (1.05) was re-wired on 2026-09-01, `object_segmentation` (1.06)
    on 2026-09-24) - so nothing is listed here, and the field stays
    for the next step that needs it.

    Not to be confused with a step that IS wired and is DESELECTED BY
    DEFAULT - `ocr_extraction` (1.07). That step has a DAG node, runs
    whenever a selection names it, and its reason lives in
    `library/tools/run_scope.DESELECTED_BY_DEFAULT`. Unwired means no
    node exists; deselected means the node exists and this run declined
    it."""

    unwired_reason: str = ""
    """Non-empty when ``wired`` is False. Records WHY the step is not in
    the DAG, so the next person does not have to re-investigate.
    ``__post_init__`` enforces that ``wired=False`` without a reason is
    an error - a step directory that exists but never runs is a trap
    unless the absence is explained."""

    def __post_init__(self):
        if not self.wired and not self.unwired_reason:
            raise ValueError(
                f"StepDir {self.node_id!r} has wired=False but no "
                f"unwired_reason. Every unwired step must document why."
            )


STEPS: tuple = (
    StepDir("validate_sfx_library", "0_01_validate_sfx_library"),
    StepDir("scan", "1_01_scan_project"),
    StepDir("catalog", "1_02_catalog_footage"),
    StepDir("semantic_analysis", "1_03_semantic_analysis"),
    StepDir("temporal_index", "1_04_temporal_index"),
    StepDir("prosody_analysis", "1_05_prosody_analysis"),
    StepDir("object_segmentation", "1_06_object_segmentation"),    StepDir("ocr_extraction", "1_07_ocr_extraction"),
    StepDir("creative_direction", "2_01_creative_direction"),
    StepDir("speech_sequence", "2_02_speech_sequence"),
    StepDir("music_selection", "2_04_music_selection"),
    StepDir("music_analysis", "2_06_music_analysis"),
    StepDir("mesh_spine", "2_05_mesh_spine"),
    StepDir("assign_aroll", "3_01_assign_aroll"),
    StepDir("select_broll", "3_02_select_broll"),
    StepDir("review_rough_cut", "3_03_review_rough_cut"),
    StepDir("select_reels", "3_04_select_reels"),
    StepDir("judge_reels", "3_05_judge_reels"),
    StepDir("plan_subtitles", "4_01_plan_subtitles"),
    StepDir("plan_transitions", "4_02_plan_transitions"),
    StepDir("plan_vfx", "4_03_plan_vfx"),
    StepDir("plan_sfx", "4_04_plan_sfx"),
    StepDir("render_subtitles", "4_05_render_subtitles"),
    StepDir("render_motion_graphics", "4_06_render_motion_graphics"),
    StepDir("color_grade", "5_01_color_grade"),
    StepDir("audio_mix", "5_02_audio_mix"),
    StepDir("compile_manifest", "5_04_compile_manifest"),
    StepDir("creative_cohesion", "5_03_creative_cohesion"),
    StepDir("render", "6_01_render"),
    StepDir("validate", "6_02_validate_output"),
    # Phase 7 belongs to the OTHER process. `library/steps/` is one tree
    # and belongs to the repository rather than to a process, so these
    # two sit here beside the rest; their DAG nodes are in
    # `library/processes/reels/dag.json`, not in edit_video's, and a
    # plain `run` never schedules them.  `library/tools/processes.py` is
    # what knows which graph declares which node.
    StepDir("build_reels", "7_01_build_reels"),
    StepDir("verify_reels", "7_02_verify_reels"),
)

STEP_BY_ID: dict = {s.node_id: s for s in STEPS}
STEP_ORDER: dict = {s.node_id: i for i, s in enumerate(STEPS)}
STEP_BY_DIRNAME: dict = {s.dirname: s for s in STEPS}


def node_id_for(step_identifier: str) -> str:
    """The DAG node id for a step named in EITHER vocabulary.

    A step has two names and they are not interchangeable.  The runner,
    `pipeline_data.json` and the two ledgers know `scan`; the step's own
    manifest and its directory know `step_1_01_scan_project`.  No rule
    connects them - `scan` is not a prefix of `scan_project` - so the
    STEPS table is the only thing that can translate, and any code that
    holds one vocabulary while its caller holds the other must come
    through here.  `TemplateLoader.get_brand_constraints` did not, and
    every brand constraint it ever produced was discarded unread.

    An identifier in neither vocabulary comes back unchanged: many
    callers pass an id that names no step in this pipeline at all, and
    inventing an answer for one would be worse than passing it through.
    """
    if step_identifier in STEP_BY_ID:
        return step_identifier
    dirname = step_identifier[len("step_"):] if step_identifier.startswith(
        "step_") else step_identifier
    step = STEP_BY_DIRNAME.get(dirname)
    return step.node_id if step else step_identifier

# Writers that are not steps.  Named so an area they own does not have to
# read as unattributed.
RUNNER = "runner"
REVIEW_CHANNEL = "review_channel"
"""`library/dashboard/review_channel.py`, the one part of the retired
dashboard that survives (AGENTS.md 4)."""
ORGANIZE = "organize"
MARKER_PULL = "marker_feedback"
MARKER_CAPTURE = "marker_capture"
FOOTAGE_ANALYSIS_RUN = "footage_analysis"
PROJECT_FORMAT = "project_format"
"""`ren analyze` (library/tools/footage_analysis.py) - not a step: it
orchestrates steps and the per-source memory lanes."""
NON_STEP_PRODUCERS = (RUNNER, REVIEW_CHANNEL, ORGANIZE, MARKER_PULL,
                      MARKER_CAPTURE, FOOTAGE_ANALYSIS_RUN, PROJECT_FORMAT)

_OUT = "pipeline_output"
_STEPS_DIRNAME = "steps"
_STEPS = f"{_OUT}/{_STEPS_DIRNAME}"

# What a step's own JSON output and human summary are called inside its
# directory.  Not `<step_id>.json`: inside `1_04_temporal_index/` the
# step id is the directory name, and repeating it is noise.
STEP_OUTPUT_FILE = "output.json"
STEP_SUMMARY_FILE = "summary.md"


def _step_path(node_id: str, *parts) -> str:
    """`pipeline_output/steps/1_04_temporal_index[/parts...]`."""
    step = STEP_BY_ID[node_id]
    return "/".join((_STEPS, step.dirname, *parts))


# ── The table ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class AreaSpec:
    relpath: str
    kind: Kind
    purpose: str
    step: str = ""
    """The DAG node id whose directory this is, for a step-owned area.

    Empty for everything that is not a step's product. This is what makes
    the folder walkable: a file's directory names the step that wrote it,
    so nothing has to be looked up.
    """

    produced_by: tuple = ()
    """Writers, for an area no single step owns.

    Only `exports/` and the project-level areas need this. It is a
    DECLARATION about the pipeline as built, never an observation of a
    particular file; `provenance.py` labels it accordingly.
    """

    @property
    def writers(self) -> tuple:
        return (self.step,) if self.step else self.produced_by


class Area(str, Enum):
    """Every named place inside a project folder.

    A place that is not here does not exist. Adding a directory means
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
    EXTERNAL_STATE = "external_state"
    EXTERNAL_DECLARATIONS = "external_declarations"
    RUN_PROFILES = "run_profiles"
    CONTEXT = "context"
    LEARNED_CONTEXT = "learned_context"
    SUBTITLE_PLANS = "subtitle_plans"
    SUBTITLE_OVERLAYS = "subtitle_overlays"

    # The output root, and the steps/ directory that is most of it.
    OUTPUT_ROOT = "output_root"
    STEPS_ROOT = "steps_root"

    # One per step that leaves files behind. The key still says what the
    # files ARE; the PATH says which step made them, which is what the
    # captain reads.
    VISION_ANALYSIS = "vision_analysis"
    TEMPORAL_INDEX = "temporal_index"
    AUDIO_CACHE = "audio_cache"
    PROSODY = "prosody"
    SEGMENTATION = "segmentation"
    OCR = "ocr"
    ACQUIRED_MEDIA = "acquired_media"
    MUSIC_ANALYSIS = "music_analysis"
    WINDOW_FRAMES = "window_frames"
    ROUGHCUT_FRAMES = "roughcut_frames"
    SFX_CATALOGUE = "sfx_catalogue"
    FOOTAGE_ANALYSIS = "footage_analysis"
    REEL_CANDIDATE_DIAGNOSTICS = "reel_candidate_diagnostics"
    SUBTITLE_SEGMENTS = "subtitle_segments"
    MOTION_GRAPHICS_SEGMENTS = "motion_graphics_segments"
    TIMED_TEXT_SEGMENTS = "timed_text_segments"
    ASSEMBLY_MANIFEST = "assembly_manifest"
    FUSION_COMPS = "fusion_comps"
    CARRIERS = "carriers"
    TIMELINE_INTERCHANGE = "timeline_interchange"
    QA_FRAMES = "qa_frames"
    GATE_STILLS = "gate_stills"
    SHOT_STILLS = "shot_stills"
    VFX_STILLS = "vfx_stills"

    # Project-level, and deliberately NOT under steps/: nesting these
    # under a step would be a lie about who wrote them.
    GATES = "gates"
    REVIEW = "review"
    LLM_REQUESTS = "llm_requests"
    LLM_RESPONSES = "llm_responses"
    LLM_RESPONSES_BAK = "llm_responses_bak"
    REASONING = "reasoning"
    RUN_ARCHIVES = "run_archives"
    LOGS = "logs"
    PROVENANCE = "provenance"
    FOOTAGE_MEMORY = "footage_memory"
    MIGRATIONS = "migrations"
    EXPORTS = "exports"
    RUN_STATE = "run_state"
    BACKUPS = "backups"
    SCRATCH = "scratch"
    UNSORTED = "unsorted"
    QUARANTINE = "quarantine"

    MARKER_FEEDBACK = "marker_feedback"

    VOX_TEST_RENDERS = "vox_test_renders"

    REEL_FRAME_OVERLAYS = "reel_frame_overlays"
    REEL_CARDS = "reel_cards"
    REEL_POST_HEADERS = "reel_post_headers"
    REEL_SAFE_ZONE_GUIDES = "reel_safe_zone_guides"


AREAS: dict[Area, AreaSpec] = {
    Area.PROJECT_ROOT: AreaSpec(
        ".", Kind.INPUT,
        "The project itself. project.yaml lives here and is read, never "
        "written by a step."),
    Area.RAW: AreaSpec(
        "raw", Kind.INPUT,
        "Source footage as the captain shot it. Read-only to the pipeline."),
    Area.MUSIC: AreaSpec(
        "music", Kind.INPUT,
        "Music the captain put here by hand. Read-only to the pipeline; "
        "tracks the pipeline downloads land under the step that fetched them."),
    Area.ASSETS: AreaSpec(
        "assets", Kind.INPUT,
        "Project-owned artwork and presets referenced by name from project.yaml."),
    Area.BRAND_ASSETS: AreaSpec(
        "brand_assets", Kind.INPUT,
        "Fonts and logos this series owns, staged into Remotion by prep_remotion."),
    Area.COMPOSITIONS: AreaSpec(
        "compositions", Kind.INPUT,
        "Project-owned Remotion compositions, staged verbatim. The engine "
        "renders them and never edits one."),
    Area.EXTERNAL_STATE: AreaSpec(
        "external/state", Kind.INPUT,
        "State the captain produced OUTSIDE the pipeline, offered to a "
        "step that would otherwise need the step that makes it. One "
        "file per state key, each CHECKED before it satisfies anything - "
        "see library/tools/external_inputs.py. Rebuildable state does not "
        "merge with the declarations that control it."),
    Area.EXTERNAL_DECLARATIONS: AreaSpec(
        "external/declarations", Kind.INPUT,
        "Standing project declarations anchored to words or other stable "
        "decisions. They outlive builds and are the source a branch merge "
        "must reconcile. Read by their owning modules; see "
        "library/tools/external_inputs.py."),
    Area.RUN_PROFILES: AreaSpec(
        "profiles", Kind.INPUT,
        "This project's own run profiles - which steps a run fires and "
        "where it stops for review. One YAML file per profile, named for "
        "the profile. A profile here SHADOWS an engine one of the same "
        "name; see library/tools/run_profile.py. Written by hand, never "
        "by a step."),
    Area.CONTEXT: AreaSpec(
        "context", Kind.INPUT,
        "The captain's context folder - documents, references, notes, "
        "links, images, anything that tells the model what kind of video "
        "to make. Read by every planning step that declares "
        "`project_context`, as a MAP with bodies fetched on demand; see "
        "library/tools/project_context.py. Written by the captain (and by "
        "the grillme skill as their scribe), never by a step."),
    Area.LEARNED_CONTEXT: AreaSpec(
        "learned_context", Kind.CAPTURED,
        "What the pipeline learned about this project and wrote back - "
        "captain corrections, fixed mistakes, settled decisions. Read "
        "alongside context/ on every later run, clearly attributed so "
        "the captain can tell what they said from what the pipeline "
        "concluded; see library/tools/learned_context.py. Recorded by "
        "the run, never deleted by a re-run: it is irreproducible "
        "judgement, the way marker_feedback/ is irreproducible notes.",
        produced_by=(RUNNER,)),
    Area.SUBTITLE_PLANS: AreaSpec(
        "subtitle_plans", Kind.INPUT,
        "Segment props the captain's own standalone scripts wrote - "
        "generate_podcast_subtitles.py joins SCRIPT_DIR, never the "
        "layout. Read-only to the pipeline; the pipeline's own subtitle "
        "plan is step 4.01's decision, and its own overlays render under "
        "steps/4_05_render_subtitles/."),
    Area.SUBTITLE_OVERLAYS: AreaSpec(
        "subtitle_overlays", Kind.INPUT,
        "Subtitle overlay renders from the captain's own standalone "
        "scripts - place_subtitles.py and render_subtitle_segments.py. "
        "Read-only to the pipeline, which renders its own overlays "
        "under steps/4_05_render_subtitles/."),

    Area.OUTPUT_ROOT: AreaSpec(
        _OUT, Kind.OUTPUT,
        "Everything the pipeline computes. Almost all of it is under steps/; "
        "what is not is listed below and belongs to the runner or the "
        "tooling rather than to any step.",
        produced_by=(RUNNER,)),
    Area.STEPS_ROOT: AreaSpec(
        _STEPS, Kind.OUTPUT,
        "One directory per step, numbered so the listing walks the pipeline "
        "in the order it runs. Open a step's directory to see exactly what "
        "that step produced.",
        produced_by=(RUNNER,)),

    # ── Step-owned ──────────────────────────────────────────────────
    Area.VISION_ANALYSIS: AreaSpec(
        _step_path("semantic_analysis"), Kind.OUTPUT,
        "Per-clip vision profiles from the v3 pipeline. Each names the clip "
        "it describes in its `file_path`. Versioned, clip_id-keyed records "
        "for temporal indexing are in `temporal_profile_stream_v1/`.",
        step="semantic_analysis"),
    Area.TEMPORAL_INDEX: AreaSpec(
        _step_path("temporal_index", "index"), Kind.OUTPUT,
        "One JSON per clip: transcription, word timings, face presence. Each "
        "names its clip in `source_file`. THE cache - deleting a file here "
        "is what makes that clip get re-transcribed.",
        step="temporal_index"),
    Area.AUDIO_CACHE: AreaSpec(
        _step_path("temporal_index", "audio_cache"), Kind.OUTPUT,
        "16 kHz mono audio extracted from each clip so WhisperX can read it.",
        step="temporal_index"),
    Area.PROSODY: AreaSpec(
        _step_path("prosody_analysis"), Kind.OUTPUT,
        "Per-clip prosody profiles. Each names the audio it measured.",
        step="prosody_analysis"),
    Area.SEGMENTATION: AreaSpec(
        _step_path("object_segmentation"), Kind.OUTPUT,
        "Per-clip object masks. The step runs matte-triggered, so a run "
        "with no subject grade or behind_subject plan produces nothing "
        "here.",
        step="object_segmentation"),
    Area.OCR: AreaSpec(
        _step_path("ocr_extraction"), Kind.OUTPUT,
        "Per-clip on-screen text. The step is wired into the DAG but "
        "DESELECTED BY DEFAULT, so a run produces nothing here unless it "
        "was asked to - see library/tools/run_scope.DESELECTED_BY_DEFAULT.",
        step="ocr_extraction"),
    Area.ACQUIRED_MEDIA: AreaSpec(
        _step_path("music_selection", "downloads"), Kind.OUTPUT,
        "A music track the step fetched rather than the captain supplying "
        "it. Output, so it is here and not in the read-only music/.",
        step="music_selection"),
    Area.MUSIC_ANALYSIS: AreaSpec(
        _step_path("music_analysis"), Kind.OUTPUT,
        "Tempo, beat grid and structure of the CHOSEN track.",
        step="music_analysis"),
    Area.FOOTAGE_ANALYSIS: AreaSpec(
        _step_path("select_broll"), Kind.OUTPUT,
        "The vision pass's per-clip analysis written out as one document "
        "per run, so the prompt can point at it instead of carrying "
        "35,813 B of a structure it already ships two renderings of. "
        "See library/tools/footage_reference.footage_document.",
        step="select_broll"),
    Area.WINDOW_FRAMES: AreaSpec(
        _step_path("select_broll", "window_frames"), Kind.OUTPUT,
        "One frame strip per candidate cutaway window, so the step that "
        "chooses a picture is shown one instead of only prose about it. "
        "See library/tools/window_frames.py.",
        step="select_broll"),
    Area.ROUGHCUT_FRAMES: AreaSpec(
        _step_path("review_rough_cut", "window_frames"), Kind.OUTPUT,
        "One frame strip per PLACED window - every A-roll segment and "
        "every B-roll overlay the cut plays - so the review judges the "
        "cut it was routed, not prose about it. "
        "See library/tools/window_frames.build_review_block.",
        step="review_rough_cut"),
    Area.REEL_CANDIDATE_DIAGNOSTICS: AreaSpec(
        _step_path("select_reels"), Kind.OUTPUT,
        "Each reel candidate's repeated-take evidence written out as one "
        "document per run, so the prompt can point at it instead of "
        "carrying ~19,850 tokens of it for every candidate. See "
        "library/tools/reel_diagnostics_reference.diagnostics_document.",
        step="select_reels"),
    Area.SFX_CATALOGUE: AreaSpec(
        _step_path("plan_sfx"), Kind.OUTPUT,
        "The SFX library written out as one document per run, so the "
        "prompt can point at it instead of carrying 44,575 B of "
        "catalogue. See library/tools/sfx_library.catalog_document.",
        step="plan_sfx"),
    Area.SUBTITLE_SEGMENTS: AreaSpec(
        _step_path("render_subtitles"), Kind.OUTPUT,
        "Rendered per-block subtitle overlays, QuickTime Animation RGBA "
        "with alpha (library/tools/overlay_carriage.py), plus "
        "the props each was rendered from.",
        step="render_subtitles"),
    Area.MOTION_GRAPHICS_SEGMENTS: AreaSpec(
        _step_path("render_motion_graphics", "motion_graphics"), Kind.OUTPUT,
        "Rendered per-block motion graphics overlays, plus their props.",
        step="render_motion_graphics"),
    Area.TIMED_TEXT_SEGMENTS: AreaSpec(
        _step_path("render_motion_graphics", "timed_text"), Kind.OUTPUT,
        "Rendered timed-text cards. The renderer places these on V6.",
        step="render_motion_graphics"),
    Area.ASSEMBLY_MANIFEST: AreaSpec(
        _step_path("compile_manifest"), Kind.OUTPUT,
        "assembly_manifest.json - every decision consolidated into the one "
        "document that drives the Resolve build.",
        step="compile_manifest"),
    Area.FUSION_COMPS: AreaSpec(
        _step_path("render", "fusion_comps"), Kind.OUTPUT,
        "Generated Fusion .comp files, imported onto timeline clips.",
        step="render"),
    Area.CARRIERS: AreaSpec(
        _step_path("render", "carriers"), Kind.OUTPUT,
        "Transparent ProRes carriers that generator effects composite onto.",
        step="render"),
    Area.TIMELINE_INTERCHANGE: AreaSpec(
        _step_path("render", "otio"), Kind.OUTPUT,
        "OpenTimelineIO exports of the built timeline, and the copy the "
        "mix was written into - the route the planned dB reach Fairlight "
        "by. See library/tools/otio_mix.py.",
        step="render"),
    Area.QA_FRAMES: AreaSpec(
        _step_path("validate", "qa_frames"), Kind.OUTPUT,
        "Single frames pulled off the render to check an effect drew.",
        step="validate"),
    Area.GATE_STILLS: AreaSpec(
        _step_path("verify_reels", "gate_stills"), Kind.OUTPUT,
        "Single frames grabbed off a built reel timeline to judge its "
        "gate - the `reel.gate_stills` entry point's stills, one file "
        "per named frame. See library/tools/gate_stills.py.",
        step="verify_reels"),
    Area.SHOT_STILLS: AreaSpec(
        _step_path("color_grade", "shot_stills"), Kind.OUTPUT,
        "One representative still per graded shot, of the seconds the "
        "shot-colour numbers describe, so the colourist sees the picture "
        "beside the measurement. See library/tools/shot_colour.py.",
        step="color_grade"),
    Area.VFX_STILLS: AreaSpec(
        _step_path("plan_vfx", "vfx_stills"), Kind.OUTPUT,
        "One representative still per VFX-candidate block, drawn at the "
        "block's first measured motion apex where one exists, so the "
        "motion designer sees the picture the effect lands on. See "
        "library/steps/step_4_03_plan_vfx/bridge.py.",
        step="plan_vfx"),

    # ── Project-level: not a step's product ─────────────────────────
    Area.GATES: AreaSpec(
        f"{_OUT}/gates", Kind.OUTPUT,
        "Review-gate records: what paused, and how the reviewer answered.",
        produced_by=(RUNNER,)),
    Area.REVIEW: AreaSpec(
        f"{_OUT}/review", Kind.OUTPUT,
        "The anchored review channel - channel.json holds every note and reply.",
        produced_by=(REVIEW_CHANNEL,)),
    Area.LLM_REQUESTS: AreaSpec(
        f"{_OUT}/llm_requests", Kind.OUTPUT,
        "The prompt each hybrid/LLM step was handed, as it was sent.",
        produced_by=(RUNNER,)),
    Area.LLM_RESPONSES: AreaSpec(
        f"{_OUT}/llm_responses", Kind.OUTPUT,
        "What the model returned for each hybrid/LLM step.",
        produced_by=(RUNNER,)),
    Area.LLM_RESPONSES_BAK: AreaSpec(
        f"{_OUT}/llm_responses_bak", Kind.OUTPUT,
        "The previous response for a step being re-run, kept for comparison.",
        produced_by=(RUNNER,)),
    Area.REASONING: AreaSpec(
        f"{_OUT}/reasoning", Kind.OUTPUT,
        "Per-step reasoning traces: what the agent read, rejected, was "
        "missing, its confidence, and what happened after the answer. "
        "Last-write-wins per step, so run_archives/ preserves them.",
        produced_by=(RUNNER,)),
    Area.RUN_ARCHIVES: AreaSpec(
        f"{_OUT}/run_archives", Kind.OUTPUT,
        "Per-run snapshots of the last-write-wins directories "
        "(llm_requests, llm_responses, reasoning) taken at the start "
        "of each run before any step can overwrite them. One "
        "subdirectory per run_id.",
        produced_by=(RUNNER,)),
    Area.LOGS: AreaSpec(
        f"{_OUT}/logs", Kind.OUTPUT,
        "Stdout/stderr of pipeline runs, one file per run, plus "
        "pipeline_log.jsonl and perf_ledger.jsonl (where each run spent "
        "its time - library/tools/perf_ledger.py).",
        produced_by=(RUNNER,)),
    Area.PROVENANCE: AreaSpec(
        f"{_OUT}/provenance", Kind.OUTPUT,
        "Which run wrote each artifact, and what that artifact names as its "
        "source. Append-only: a later run overwriting a file does not unmake "
        "the record of the earlier one.",
        produced_by=(RUNNER,)),
    Area.FOOTAGE_MEMORY: AreaSpec(
        f"{_OUT}/footage_memory", Kind.OUTPUT,
        "The analysis-only run's account of itself (analysis_run.json: per "
        "lane, per source, built / reused / failed and why) and the "
        "versioned, path-portable export of the per-source memory "
        "(footage_memory.v1.json, plus the local-only path map beside it). "
        "The memory itself lives per machine, outside the project - see "
        "docs/SOURCE_MEMORY.md.",
        produced_by=(FOOTAGE_ANALYSIS_RUN,)),
    Area.MIGRATIONS: AreaSpec(
        f"{_OUT}/migrations", Kind.OUTPUT,
        "Reversible records of project migrations: layout reorganisations "
        "and product-format upgrades. Reading one is how the change is "
        "undone.",
        produced_by=(ORGANIZE, PROJECT_FORMAT)),
    Area.EXPORTS: AreaSpec(
        "exports", Kind.DELIVERABLE,
        "Finished renders and their QA reports. This is what the run is for, "
        "so it sits at the project root rather than inside any one step - and "
        "TWO steps write here, 6.01 the render and 6.02 the QA report.",
        produced_by=("render", "validate")),
    Area.RUN_STATE: AreaSpec(
        ".", Kind.RUN_STATE,
        "pipeline_data.json, pipeline_run.json, pipeline.pid and pipeline.hold "
        "sit at the project root. Only the runner and its tooling write them.",
        produced_by=(RUNNER,)),
    Area.BACKUPS: AreaSpec(
        f"{_OUT}/backups", Kind.BACKUP,
        "Automatic, bounded copies of pipeline_data.json - one per run, a "
        "fixed number kept (MAX_PIPELINE_DATA_BACKUPS) - plus migration "
        "snapshots in their own named buckets. The state pruner only "
        "considers its pipeline_data filename pattern, so migration "
        "snapshots are never pruned.",
        produced_by=(RUNNER, ORGANIZE, PROJECT_FORMAT)),
    Area.SCRATCH: AreaSpec(
        f"{_OUT}/scratch", Kind.SCRATCH,
        "Working files with no reader after the step that wrote them. Safe to "
        "delete at any moment, including during a run. MUST NEVER hold a file "
        "a timeline places: reel frame overlays live in REEL_FRAME_OVERLAYS "
        "and reel cards in REEL_CARDS, both OUTPUT of build_reels. A placement "
        "that names a path under scratch/ is refused - see "
        "library/tools/reel_placed_assets.py."),
    Area.UNSORTED: AreaSpec(
        f"{_OUT}/unsorted", Kind.UNSORTED,
        "Files whose purpose could not be established. Nothing writes here at "
        "run time. An admitted unknown, never a guess and never a deletion.",
        produced_by=(ORGANIZE,)),
    Area.QUARANTINE: AreaSpec(
        f"{_OUT}/quarantine", Kind.OUTPUT,
        "Orphaned assets a sweep moved aside, plus the mark and sweep "
        "records that authorise them. The captain reviews quarantine "
        "before anything is deleted: unlike scratch/, nothing here is "
        "discarded automatically or mid-run. See "
        "library/tools/caption_asset_gc.py.",
        produced_by=(RUNNER,)),
    Area.MARKER_FEEDBACK: AreaSpec(
        "marker_feedback", Kind.CAPTURED,
        "Notes the captain typed onto a Resolve timeline, pulled off it by "
        "library/tools/marker_feedback.py. One file per pull, never "
        "overwritten. It sits at the project root and NOT under "
        "pipeline_output/ because everything there is reproducible by a "
        "re-run, and these are the one thing a re-run destroys: step 6.01 "
        "deletes the timeline before rebuilding it. Two writers: the pull "
        "records the notes, and the Workspace > Scripts capture button "
        "(library/tools/marker_capture.py) writes the frame the captain "
        "was looking at into stills/.",
        produced_by=(MARKER_PULL, MARKER_CAPTURE)),
    Area.VOX_TEST_RENDERS: AreaSpec(
        f"{_OUT}/vox_test_renders", Kind.CAPTURED,
        "Test renders exported off Resolve \"(vox test)\" timelines for "
        "the captain to refer back to. The timeline that made one is "
        "destroyed by a rebuild, so like marker_feedback/ a re-run must "
        "never delete these. Placed by hand; nothing in the pipeline "
        "writes here."),
    Area.REEL_FRAME_OVERLAYS: AreaSpec(
        _step_path("build_reels", "frame_overlays"), Kind.OUTPUT,
        "TV-frame overlays placed on reel timelines' frame track. A timeline "
        "places these, so they are OUTPUT of the step that builds reels, not "
        "scratch: the renderer drafts them under scratch/reel_look/ and "
        "reel_placed_assets promotes the placed copy here before anything is "
        "imported. Anything that trusts the SCRATCH declaration must never be "
        "able to remove them.",
        step="build_reels"),
    Area.REEL_POST_HEADERS: AreaSpec(
        _step_path("build_reels", "post_headers"), Kind.OUTPUT,
        "Social-post headers placed above a reel's picture "
        "(library/tools/reel_post_header.py). A timeline places these, so "
        "they are OUTPUT of the step that builds reels, never scratch.",
        step="build_reels"),
    Area.REEL_SAFE_ZONE_GUIDES: AreaSpec(
        _step_path("build_reels", "safe_zone_guides"), Kind.OUTPUT,
        "Platform safe-zone guides (library/tools/safe_zone_guide.py) "
        "carried as movies and placed on a DISABLED guide row of a reel "
        "timeline. A timeline places these, so never scratch.",
        step="build_reels"),
    Area.REEL_CARDS: AreaSpec(
        _step_path("build_reels", "reel_cards"), Kind.OUTPUT,
        "Full-frame cards placed on reel timelines' picture track. A timeline "
        "places these, so they are OUTPUT of the step that builds reels, not "
        "scratch: the renderer drafts them under scratch/reel_cards/ and "
        "reel_placed_assets promotes the placed copy here before anything is "
        "imported. Anything that trusts the SCRATCH declaration must never be "
        "able to remove them.",
        step="build_reels"),
}

# The areas a given step owns, in table order.
AREAS_BY_STEP: dict = {}
for _area, _spec in AREAS.items():
    if _spec.step:
        AREAS_BY_STEP.setdefault(_spec.step, []).append(_area)

WRITABLE_KINDS = frozenset({
    Kind.OUTPUT, Kind.DELIVERABLE, Kind.BACKUP, Kind.SCRATCH, Kind.UNSORTED,
    Kind.CAPTURED,
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

    def write_dir(self, area: Area, step: str = "") -> Path:
        """The directory for `area`, created, with the write guard applied.

        Pass `step` and the guard tightens: a step may write only inside
        its OWN directory, or into an area no step owns. That is what
        keeps `pipeline_output/steps/` honest - a directory named for a
        step has to mean that step wrote it.
        """
        spec = self.spec(area)
        if spec.kind not in WRITABLE_KINDS:
            raise ProjectLayoutViolation(
                f"{area.value} is {spec.kind.value}, not writable by a step: "
                f"{spec.purpose}"
            )
        if step and spec.step and spec.step != step:
            raise ProjectLayoutViolation(
                f"step {step!r} may not write to {area.value}, which belongs "
                f"to step {spec.step!r} ({spec.relpath}). A step writes only "
                f"inside its own directory."
            )
        d = self._join(area, ())
        d.mkdir(parents=True, exist_ok=True)
        return d

    def assert_step_owns(self, step: str, path) -> Path:
        """Raise unless `path` is inside `step`'s own directory.

        The by-step counterpart of `assert_writable`, for a path that
        arrived from outside the layout.
        """
        p = self.assert_writable(path)
        owner = self.step_of(p)
        if owner is not None and owner != step:
            raise ProjectLayoutViolation(
                f"{p} is in step {owner!r}'s directory; step {step!r} may not "
                f"write there. A step writes only inside its own directory."
            )
        return p

    def write_path(self, area: Area, *parts, step: str = "") -> Path:
        """A path inside `area`, with its parent created.

        The ONLY way a step gets a path it may write to.  Asking for an
        input area raises; asking for an area that is not in the table
        raises; and with `step` given, asking for another step's area
        raises.
        """
        self.write_dir(area, step=step)  # kind + ownership checks, base mkdir
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

    # ── A step's own directory ──────────────────────────────────────

    def step_dir(self, step_id: str, *parts, create: bool = False) -> Path:
        """`pipeline_output/steps/1_04_temporal_index[/parts...]`.

        The whole point of the layout: a file's directory names the step
        that wrote it, so the captain walks the folder instead of
        looking anything up.
        """
        step = STEP_BY_ID.get(step_id)
        if step is None:
            raise ProjectLayoutViolation(
                f"Unknown step {step_id!r}. Every step is a row in "
                f"project_layout.STEPS; add a row rather than composing a "
                f"path. Known: {sorted(STEP_BY_ID)}"
            )
        d = self.root / _STEPS / step.dirname
        for part in parts:
            if part:
                d = d / str(part)
        if create:
            self.assert_writable(d)
            d.mkdir(parents=True, exist_ok=True)
        return d

    def step_output_json(self, step_id: str) -> Path:
        return self.step_dir(step_id, create=True) / STEP_OUTPUT_FILE

    def step_output_summary(self, step_id: str) -> Path:
        return self.step_dir(step_id, create=True) / STEP_SUMMARY_FILE

    def step_of(self, path) -> str | None:
        """The step whose directory `path` is in, or None.

        This is the by-step layout paying off: no index, no sidecar - the
        answer is in the path.
        """
        p = Path(str(path))
        p = p if p.is_absolute() else self.root / p
        try:
            rel = Path(os.path.normpath(str(p))).relative_to(
                self.root / _STEPS).as_posix()
        except ValueError:
            return None
        head = rel.split("/", 1)[0]
        for step in STEPS:
            if step.dirname == head:
                return step.node_id
        return None

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

    def backup_file(self, source, *parts) -> Path:
        """Copy one project file into the named, never-pruned backup bucket.

        The automatic retention policy is intentionally specific to
        pipeline_data.json. Migration backups use an explicit bucket and
        lifetime instead, while still getting a project-layout-owned path.
        """
        src = Path(source)
        try:
            src.resolve().relative_to(self.root.resolve())
        except ValueError:
            raise ValueError(
                f"project backup source must be inside {self.root}: {src}") \
                from None
        if not src.is_file():
            raise FileNotFoundError(
                f"Cannot back up a missing project file: {src}")
        dest = self.write_path(Area.BACKUPS, *parts)
        if dest.exists():
            raise FileExistsError(
                f"Refusing to overwrite an existing project backup: {dest}")
        shutil.copy2(src, dest)
        return dest

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
        """Create the container directories and refresh the README.

        Only the two structural containers are pre-created:
        `pipeline_output/` and `pipeline_output/steps/`.  Everything
        else - individual step directories, project-level areas like
        `gates/` or `review/` - appears when something writes to
        it, through `write_dir` or `write_path`.

        A directory that exists because the scaffold guessed carries no
        information.  A directory that appears only when something writes
        tells you the step has run and produced output.

        Input areas are NOT created: a project with no `music/` should
        not sprout an empty one, and creating an input directory is the
        first half of writing to it.
        """
        (self.root / _OUT).mkdir(parents=True, exist_ok=True)
        (self.root / _STEPS).mkdir(parents=True, exist_ok=True)
        self.write_readme()
        return self

    def write_readme(self) -> Path:
        p = self.root / LAYOUT_README_FILE
        text = self.describe()
        if not p.exists() or p.read_text(encoding="utf-8") != text:
            p.write_text(text, encoding="utf-8")
        return p

    def describe(self, consumption=None) -> str:
        """The folder explained, walking the pipeline in the order it runs.

        Rendered from the same table the code reads, so it cannot drift
        from where files actually go. `consumption` is
        `{step: {input_key: [producing_step, ...]}}`; it is read off the
        DAG when not supplied, and omitted entirely if the DAG cannot be
        read, rather than guessed at.
        """
        if consumption is None:
            consumption = _dag_consumption()

        lines = [
            "# What is in this folder",
            "",
            "Generated from `library/tools/project_layout.py`, the one place in the",
            "pipeline that decides where a project's files go. Do not edit it by",
            "hand - edit the table and it regenerates.",
            "",
            "`pipeline_output/steps/` is the pipeline. One directory per step,",
            "numbered so the listing walks it in the order it runs, and named for the",
            "step rather than for the kind of thing inside. To follow what happened,",
            "read down that listing.",
            "",
            "A step directory appears when the step writes output and not before.",
            "If a directory is not there, that step has not run. If it is, open it",
            "to see exactly what it produced.",
            "",
            "Each step directory holds `" + STEP_OUTPUT_FILE + "` (what the step",
            "returned, also in `pipeline_data.json`) and `" + STEP_SUMMARY_FILE + "`",
            "(the same thing for a human), plus whatever files it produced.",
            "",
        ]

        lines += ["## The steps, in the order they run", ""]
        for i, step in enumerate(STEPS, 1):
            lines.append(f"### {i}. `{_STEPS_DIRNAME}/{step.dirname}/`")
            lines.append("")
            if not step.wired:
                lines += [
                    "Implemented but NOT wired into the DAG, so a run produces",
                    "nothing here.",
                    "",
                ]
            consumed = consumption.get(step.node_id) or {}
            if consumed:
                parts = [f"`{k}` from `{_STEPS_DIRNAME}/"
                         f"{STEP_BY_ID[v[0]].dirname}/`"
                         if v and v[0] in STEP_BY_ID else f"`{k}`"
                         for k, v in sorted(consumed.items())]
                lines.append("- reads: " + "; ".join(parts))
            elif consumption:
                lines.append("- reads: nothing - no inbound edge in the DAG")
            for area in AREAS_BY_STEP.get(step.node_id, []):
                spec = AREAS[area]
                leaf = spec.relpath[len(_STEPS) + len(step.dirname) + 2:]
                where = f"`{leaf}/`" if leaf else "(directly here)"
                lines.append(f"- writes {where} - {spec.purpose}")
            if not AREAS_BY_STEP.get(step.node_id):
                lines.append(
                    f"- writes `{STEP_OUTPUT_FILE}` and `{STEP_SUMMARY_FILE}` "
                    f"only - this step produces a decision, not files")
            lines.append("")

        lines += [
            "## Not a step's product",
            "",
            "These belong to the runner, the review channel or the tooling. Nesting them",
            "under a step would be a lie about who wrote them.",
            "",
        ]
        for area, spec in sorted(AREAS.items(), key=lambda r: r[1].relpath):
            if spec.step or spec.kind is Kind.INPUT or spec.relpath == ".":
                continue
            if spec.relpath in (_OUT, _STEPS):
                continue
            writers = ", ".join(f"`{w}`" for w in spec.writers) or "-"
            lines.append(f"- `{spec.relpath}/` ({writers}) - {spec.purpose}")
        lines.append("")

        lines += [
            "## Inputs - the captain's material",
            "",
            "Read by the pipeline, never written to. Asking the layout to write",
            "into one of these raises.",
            "",
            "Only `raw/` is scaffolded when a project is created: every project",
            "needs footage, and step 1.01 scans it. The others appear when the",
            "captain puts material there. A directory's presence tells you the",
            "captain has that kind of material; its absence tells you they do not.",
            "",
        ]
        for area, spec in sorted(AREAS.items(), key=lambda r: r[1].relpath):
            if spec.kind is not Kind.INPUT or spec.relpath == ".":
                continue
            lines.append(f"- `{spec.relpath}/` - {spec.purpose}")
        lines += [
            "",
            "## Run state",
            "",
            AREAS[Area.RUN_STATE].purpose,
            "",
            "## Rules this folder is kept to",
            "",
            "- A directory exists because something wrote to it, not because the scaffold",
            "  guessed. An empty directory is a bug, not a placeholder.",
            "- A step never composes a path. It names an `Area` and the layout owner",
            "  returns the path, so no two steps can disagree about where something goes.",
            "- A step writes only inside its own directory. Everything else raises.",
            "- Inputs are structurally protected: asking the layout to write into",
            "  " + ", ".join(
                f"`{AREAS[area].relpath}/`" for area in Area
                if AREAS[area].kind is Kind.INPUT
                and AREAS[area].relpath != ".") + " raises.",
            f"- `{_OUT}/backups/{BACKUP_SUBDIR}/` holds one backup of",
            f"  `pipeline_data.json` per run, newest {MAX_PIPELINE_DATA_BACKUPS} kept,",
            "  pruned automatically. Hand-made backups from before that policy are in",
            f"  `{LEGACY_BACKUP_SUBDIR}/` beside them and are never pruned.",
            f"- `{_OUT}/migrations/` records reversible project changes.",
            f"  Product-format upgrades keep their original `project.yaml` under",
            f"  `{_OUT}/backups/format/`; those snapshots are never pruned.",
            f"- `{AREAS[Area.MARKER_FEEDBACK].relpath}/` is the captain's own typed",
            "  notes, pulled off a Resolve timeline. It is the one computed thing a",
            "  re-run must never delete, which is why it is not under",
            f"  `{_OUT}/`. One file per pull, never overwritten.",
            f"- `{_OUT}/unsorted/` is where a file goes when nobody could say",
            "  what it was. An admitted unknown beats a confident wrong guess, and",
            "  beats deleting it.",
            "",
        ]
        return "\n".join(lines)


def _dag_consumption() -> dict:
    """`{step: {input_key: [producing_step, ...]}}` off the DAG's edges.

    Read here rather than imported, because `run_traceback` imports this
    module and the dependency must not go both ways. An unreadable DAG
    yields {}, and the README then omits the reads lines rather than
    inventing them.
    """
    import json

    dag_path = (Path(__file__).resolve().parents[1]
                / "processes" / "edit_video" / "dag.json")
    try:
        dag = json.loads(dag_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict = {}
    for edge in dag.get("edges", []):
        src, dst = edge.get("from"), edge.get("to")
        for in_key in (edge.get("data_mapping") or {}).values():
            producers = out.setdefault(dst, {}).setdefault(in_key, [])
            if src not in producers:
                producers.append(src)
    return out


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
