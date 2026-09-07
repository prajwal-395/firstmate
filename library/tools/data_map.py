"""Every piece of information the pipeline carries, at FIELD level.

The captain's question, 2026-09-07: *"continue to flush out the data
management until you have mapped out exactly what each piece of
information is and where/how it is being used (so we know that our
pipeline is perfectly lean)"*.

Four questions, asked of every field:

    what is it     - its type, and an example of its shape
    who WRITES it  - the document it lives in has one writer
    who READS it   - code, a prompt, something outside Python, or nothing
    what BREAKS    - derived from the SHAPE of the read, not guessed

Why this is a third contract and not a widening of the other two
----------------------------------------------------------------
`input_contract.py` asks who REFUSES when a declared input is absent.
`output_contract.py` asks who READS what a step produces.  Both work on
DECLARED STEP OUTPUTS, and both say so.  Two whole classes of data are
outside them:

* **Data that is not a declared step output.**  `timeline_transcript`'s
  `transcript.json`, `reel_proposals_v2.json`, `plan_provenance.json`,
  the Remotion caption props, the run archives, `project.yaml`, the
  environment.  `DOCUMENTS` below is the enumeration.
* **The FIELD inside a document.**  `output_contract` counts a document
  with thirty keys as one output.  Every defect found in the two days
  before this map was made was at field level - `frame_rate` versus
  `project_fps`, a caption hash fed the wrong object, a transcriber
  confidence dropped at write time.  The field is where the errors live.

And a third route neither of them models: a field can be unread by any
line of code and still be load-bearing, because it reaches a MODEL.
`context_fields` is that route and it is declared per step in dotted
paths, so it can be joined to the same field names.

How the map is derived
----------------------
Three sources, joined on `(origin, field path)`:

1. **What the code reads** - `library/tools/field_flow.py`, which
   follows the value rather than matching the name.  Its docstring is
   where the reasoning for that lives, and #601 is why it has to.
2. **What reaches a prompt** - every step manifest's `context_fields`,
   read through `context_projector.declared_context_fields` so this
   agrees with the projection the runner actually performs.
3. **What is really there** - `data_map_observed.json`, the SHAPE of the
   documents a real run wrote: every field path, its type, how many
   times it occurred and how many bytes of the artefact it is.  No
   values, so it carries no project content; regenerated with
   `--observe`.

The three disagree in ways that are findings rather than noise:

    observed, no reader anywhere    CARRIED AND UNUSED - with its cost
    read, never observed            a reader of a key that cannot exist
    declared to a prompt, absent    a prompt describing an empty table

Being sceptical of this instrument
----------------------------------
An analyzer that loses a value reports a field as LESS read, never more
(`field_flow`'s docstring says why the asymmetry holds).  So:

* a field this map says IS read really is read - the evidence is a file
  and a line, and `--field` prints it;
* a field this map says is UNREAD is a QUESTION.  Every one is
  adjudicated in `CARRIED_NOT_READ` with what it costs to keep, or it is
  a new finding and `disagreements()` fails.

`KNOWN_BLIND_SPOTS` is the list of places the derivation cannot see, each
with a MEASURED size, because a blind spot with no size reads as
coverage.

    python3 -m library.tools.data_map                    # the summary
    python3 -m library.tools.data_map --document reel_proposals_v2
    python3 -m library.tools.data_map --field OUT@catalog#clip_catalog[].width
    python3 -m library.tools.data_map --unread
    python3 -m library.tools.data_map --bad              # the gate
    python3 -m library.tools.data_map --observe <project folder>

`tests/test_data_map.py`, and `docs/DATA_MAP.md` for the prose half.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence, Set, Tuple

from library.tools import field_flow

_LIBRARY_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _LIBRARY_ROOT.parent

OBSERVED_FILE = _LIBRARY_ROOT / "tools" / "data_map_observed.json"


# ── The documents ────────────────────────────────────────────────────
#
# ENUMERATED, never globbed.  The same reasoning
# `check_agents_md_preservation` uses for its corpus: a glob would let a
# document count as mapped because some unrelated file happened to match,
# and the enumeration IS the reviewable list.
#
# `literals` are the strings this repository actually spells the name
# with, because that is what `field_flow` seeds on.  A name composed at
# runtime (`f"{stem}_prosody.json"`) is spelled here as the constant part
# the code contains.

@dataclass(frozen=True)
class Document:
    id: str
    literals: Tuple[str, ...]
    what: str
    writer: str
    """Module (and function) that writes it, or the step that does."""
    kind: str
    """`state`, `declaration`, `analysis`, `plan`, `review`, `archive`."""
    lives: str
    """Where under the project folder, in `project_layout.Area` terms."""
    envelope_only: bool = False
    """Record only this document's TOP-LEVEL keys.

    For the archives.  An `llm_requests/<step>.json` is the context one
    step sent a model, so its fields are a COPY of the fields of every
    document that reached that prompt.  Mapping them again would double
    the field count and would report a change to `creative_direction` as
    two unrelated fields."""

    under: str = ""
    """A path fragment a file must be under to count as this document.

    Only for the names a filename cannot decide.  `external_state` files
    are named for the state key they stand in for, so the only thing
    that identifies one is that it is in `external/`; without this it
    matched every `.json` in the project and swallowed 968 of
    geo-podcast's 1,640 observed field paths."""


DOCUMENTS: Tuple[Document, ...] = (
    # ── run state ────────────────────────────────────────────────────
    Document(
        "pipeline_data", ("pipeline_data.json",),
        "The run's whole state. `step_outputs` is the declared-output "
        "half that `output_contract` already covers; the other thirteen "
        "top-level keys are this map's.",
        "library/processes/edit_video/run_pipeline.save_pipeline_state",
        "state", "project root"),
    Document(
        "pipeline_run", ("pipeline_run.json",),
        "The runner's own account of itself - mode, current step, how it "
        "ended. Written by the runner, read by the dashboard.",
        "library/tools/run_control.py", "state", "project root"),
    Document(
        "step_output", ("output.json",),
        "One step's export - the step's declared output, verbatim. Its "
        "FIELDS are therefore `OUT@<node>`'s fields, and `observe` files "
        "them there rather than counting them twice.",
        "library/tools/step_exporter.py", "state", "Area.STEPS_ROOT"),
    Document(
        "step_summary", ("summary.md",),
        "The human-readable half of a step's export, rendered generically "
        "from whatever keys the output carries.",
        "library/tools/step_exporter.generate_summary", "state",
        "Area.STEPS_ROOT"),
    Document(
        "run_traceback", ("RUN-TRACEBACK.md", "ARTIFACTS.md"),
        "Two generated documents regenerated on every run: what ran, and "
        "which file each step produced.",
        "library/tools/run_traceback.py", "state", "project root"),
    Document(
        "provenance", ("artifacts.jsonl", "runs.jsonl"),
        "Which run wrote a file and from what - two append-only JSONL "
        "ledgers. Prose in `library/tools/provenance.py`.",
        "library/tools/provenance.py", "state", "Area.PROVENANCE"),
    Document(
        "migration", ("migration_*.json",),
        "What `manage_project.py organize` moved, so a migration can be "
        "read back.",
        "library/tools/project_migration.py", "state", "Area.MIGRATIONS"),

    # ── declarations the captain owns ────────────────────────────────
    Document(
        "project_config", ("project.yaml",),
        "The project's own declarations: source settings, pipeline "
        "options, Resolve bindings, delivery format, subtitle style. "
        "Schema: `library/schemas/project_config.py`.",
        "the captain (and `manage_project.py new`)", "declaration",
        "project root"),
    Document(
        "brand_template", ("brand.json",),
        "A brand's declared style, effect and content slots. The engine "
        "ships templates; a project names one or names none (AGENTS.md "
        "10.1).",
        "library/templates/ (checked in)", "declaration", "the repository"),
    Document(
        "hooks", ("hooks.json",),
        "A project's declared hooks - what runs before and after a "
        "step. Prose in `library/tools/hooks.py`.",
        "library/tools/hooks.py", "declaration", "project root"),
    Document(
        "external_state", (".json",),
        "State a producer outside the pipeline supplied, verified before "
        "it is believed (AGENTS.md 3). Named for the state key it stands "
        "in for, so only its directory identifies it.",
        "the captain, checked by `library/tools/external_inputs.py`",
        "declaration", "Area.EXTERNAL_STATE", under="/external/"),

    # ── what the footage was measured to be ──────────────────────────
    Document(
        "vision_profile", ("clip_profile_*.json", "_v3.json"),
        "One clip's v3 vision observations - scene, camera, actions, "
        "objects, assessment.",
        "library/tools/analysis/vision_pipeline_v3.py (step 1.03)",
        "analysis", "Area.VISION_ANALYSIS"),
    Document(
        "vision_index", ("vision_index_v3.json",),
        "The index over the per-clip vision profiles.",
        "library/tools/analysis/vision_pipeline_v3.py (step 1.03)",
        "analysis", "Area.VISION_ANALYSIS"),
    Document(
        "temporal_index_clip", ("clip_*.json",),
        "One clip's temporal index: transcript, faces, motion, sampled "
        "frames.",
        "library/steps/step_1_04_temporal_index/step.py",
        "analysis", "Area.TEMPORAL_INDEX"),
    Document(
        "prosody", ("_prosody.json", "clip_*_prosody.json"),
        "One clip's deterministic prosody measurements (step 1.05).",
        "library/steps/step_1_05_prosody_analysis/step.py",
        "analysis", "Area.PROSODY"),
    Document(
        "segmentation", ("_segmentation.json",),
        "SAM 2 subject masks. Step 1.06 is UNWIRED - nothing consumes "
        "them (AGENTS.md 3).",
        "library/steps/step_1_06_object_segmentation/step.py",
        "analysis", "Area.SEGMENTATION"),
    Document(
        "ocr_result", ("ocr_result.json",),
        "On-screen text off every frame. Step 1.07 is wired and "
        "DESELECTED BY DEFAULT.",
        "library/steps/step_1_07_ocr_extraction/step.py",
        "analysis", "Area.OCR"),
    Document(
        "music_analysis_doc", ("music_analysis.json",),
        "The chosen track's measurements - tempo, beats, energy.",
        "library/steps/step_2_06_music_analysis/step.py",
        "analysis", "Area.MUSIC_ANALYSIS"),
    Document(
        "footage_index", ("footage_index.json",),
        "The cross-clip footage search index. A PROTOTYPE in the "
        "DASHBOARD, out of the pipeline (AGENTS.md 2).",
        "library/tools/analysis/footage_query.py", "analysis",
        "Area.SCRATCH"),
    Document(
        "sfx_index", ("sfx_index.json", "library_analysis.json",
                      "library_semantic.json"),
        "The sound-effect library's own index and its two profile "
        "documents.",
        "the SFX library (outside the project)", "analysis",
        "PIPELINE_SFX_LIBRARY"),

    # ── the plan, and what it delivers ───────────────────────────────
    Document(
        "assembly_manifest", ("assembly_manifest.json",),
        "Every decision consolidated into the document that drives the "
        "Resolve render.",
        "library/steps/step_5_04_compile_manifest/step.py", "plan",
        "Area.ASSEMBLY_MANIFEST"),
    Document(
        "subtitle_props", ("_props.json",),
        "One rendered caption segment's Remotion props. READ IN "
        "TYPESCRIPT - see `EXTERNAL_READERS`.",
        "library/steps/step_4_05_render_subtitles/generate_remotion_props.py",
        "plan", "Area.SUBTITLE_SEGMENTS"),
    Document(
        "render_batch_jobs", ("render_batch_jobs.json",),
        "The batch of Remotion renders to run, as one spec file.",
        "library/tools/remotion_batch.py", "plan", "Area.SCRATCH"),
    Document(
        "timeline_decisions", ("timeline_decisions.json",),
        "Which decision produced each clip on the built timeline "
        "(AGENTS.md 15).",
        "library/tools/timeline_decisions.py", "plan", "step 6.01's dir"),
    Document(
        "plan_provenance", ("plan_provenance.json",),
        "What a built plan was built FROM, so a rebuild can say what "
        "changed.",
        "library/tools/plan_provenance.py", "plan", "Area.REVIEW"),
    Document(
        "qa_report", ("qa_report.json",),
        "Step 6.02's findings on the RENDER.",
        "library/steps/step_6_02_validate_output/", "plan", "Area.EXPORTS"),

    # ── the reel path ────────────────────────────────────────────────
    Document(
        "timeline_transcript", ("transcript.json",),
        "The master timeline's speech, REBUILT from source rather than "
        "rendered (AGENTS.md 5). Produced outside the DAG.",
        "library/tools/timeline_transcript.py", "analysis",
        "Area.SCRATCH/timeline_transcript"),
    Document(
        "reel_proposals_v2", ("reel_proposals_v2.json",),
        "The captain's review surface, and the file `build-reels` reads. "
        "Spelled once, in `reel_proposal.PROPOSAL_FILENAME`. The "
        "timestamped copies beside it are the same shape.",
        "library/tools/reel_proposal.write_proposal", "review",
        "Area.REVIEW"),
    Document(
        "reel_judgement", ("reel_judgement.json",),
        "Step 3.05's reading of the proposals against the quality bar.",
        "library/tools/reel_quality_bar.py", "review", "Area.REVIEW"),
    Document(
        "conformance_report", ("conformance_report.json",),
        "What a built reel timeline was measured to be, against the plan "
        "that asked for it.",
        "library/tools/reel_build.py", "review", "Area.REVIEW"),
    Document(
        "resolve_placements", ("resolve_placements",),
        "The journal of what was placed into the Resolve media pool. "
        "Named by prefix plus a timestamp, so the literal is the stem.",
        "library/tools/execution/organise_media_pool.py", "review",
        "Area.REVIEW"),

    # ── the review return channel, and the captain's notes ───────────
    Document(
        "review_channel", ("channel.json",),
        "The reviewer's notes, each anchored to an element and recording "
        "the view it was written on (AGENTS.md 4).",
        "library/dashboard/review_channel.py", "review", "Area.REVIEW"),
    Document(
        "gate_status", ("status.json",),
        "A review gate's state: waiting, approved, rejected, revised.",
        "library/tools/review_gate.py", "review", "Area.GATES"),
    Document(
        "gate_feedback", ("feedback.json",),
        "What the reviewer said at a gate.",
        "library/tools/review_gate.py", "review", "Area.GATES"),
    Document(
        "gate_snapshot", ("snapshot.json",),
        "The step output as it stood when a gate paused. TWO WRITERS "
        "share this filename - the review gate and "
        "`replay_bench/snapshot.py` - so a read attributed here may be "
        "either; `KNOWN_BLIND_SPOTS` records the size of that.",
        "library/tools/review_gate.py and library/tools/replay_bench/",
        "review", "Area.GATES"),
    Document(
        "marker_pull", (".markers.json",),
        "The notes the captain typed onto a Resolve timeline, as "
        "collected (AGENTS.md 15).",
        "library/tools/marker_feedback.py", "review",
        "Area.MARKER_FEEDBACK"),
    Document(
        "marker_routing", (".routing.json", "ROUTED-NOTES.md"),
        "Where each collected note was routed, and why.",
        "library/tools/marker_routing.py", "review",
        "Area.MARKER_FEEDBACK"),

    # ── the archives ─────────────────────────────────────────────────
    Document(
        "llm_request", ("*.json",),
        "The exact context one hybrid step sent a model. One file per "
        "step, LAST WRITE WINS - a re-run destroys the previous one; "
        "`run_archives/` keeps a per-run copy of the same document, "
        "which is why the archive is not a document of its own.",
        "library/processes/edit_video/run_pipeline.py", "archive",
        "Area.LLM_REQUESTS", envelope_only=True, under="/llm_requests/"),
    Document(
        "llm_response", ("*.json",),
        "What the model answered, before the post-bridge.",
        "library/processes/edit_video/run_pipeline.py", "archive",
        "Area.LLM_RESPONSES", envelope_only=True, under="/llm_responses/"),
)

DOCUMENTS_BY_ID = {d.id: d for d in DOCUMENTS}


# ── How a value reaches a step that no edge carries it to ────────────
#
# `gather_step_inputs` assembles a step's inputs from the DAG's
# `data_mapping` edges and then from six named routes that are not
# edges.  A map that resolved only the edges would report every one of
# these as coming from nowhere.  Each entry is the state key or document
# the value really comes from.

NON_EDGE_INPUTS: Dict[str, str] = {
    "project_folder": "DOC@pipeline_data#project_folder",
    "project_config": "DOC@pipeline_data#project_config",
    "sfx_library": "DOC@pipeline_data#sfx_library",
    "music_library": "DOC@pipeline_data#music_library",
    "creative_brief": "DOC@pipeline_data#creative_brief",
    "render_qa_findings": "DOC@qa_report",
    "timeline_transcript": "DOC@timeline_transcript",
    "brand_template": "DOC@brand_template",
    "brand_style": "DOC@brand_template",
    "brand_effect": "DOC@brand_template",
    "brand_content": "DOC@brand_template",
    "captain_notes": "DOC@marker_routing",
}


# ── Fields whose reader is not in Python ─────────────────────────────
#
# A capability is only real where the renderer reads it (AGENTS.md 10.2),
# and three renderers here are not Python: Remotion (TypeScript), Fusion
# (a .comp the compositor evaluates) and Resolve's own scripting host.
# A field handed to one of them is READ - by something this analyzer
# cannot parse - and calling it unread would be a survey that fails
# correct output.
#
# Each entry names the file on the other side, so the claim is checkable
# rather than asserted.

EXTERNAL_READERS: Dict[str, str] = {
    "DOC@subtitle_props": "remotion-subtitles/src/ - the caption "
                          "compositions read these props in TypeScript.",
    "DOC@render_batch_jobs": "remotion-subtitles/ - the batch renderer "
                             "reads the job spec.",
}


# ── What the derivation cannot see, with the size of each ────────────
#
# A blind spot with no size reads as coverage.  Every entry here is
# MEASURED by `blind_spot_sizes()` from the same analysis the map is
# built on, so a number that drifts is visible.

KNOWN_BLIND_SPOTS: Dict[str, str] = {
    "dynamic keys":
        "`row[key]` where `key` is a variable reads the CONTAINER, not a "
        "named field. Honest, but a field reached only that way looks "
        "unread. Counted by `blind_spot_sizes()['dynamic_reads']`.",
    "two documents, one filename":
        "`snapshot.json` is written by `review_gate` and by "
        "`replay_bench/snapshot.py`, and `output.json` by all 32 steps. "
        "A read seeded on the literal cannot say which. The step "
        "outputs' own shape is uniform so `output.json` costs nothing; "
        "`snapshot.json` is one document with two shapes.",
    "values that leave Python":
        "`EXTERNAL_READERS`. A field in a Remotion props file or a "
        "Fusion .comp is read by TypeScript or by the compositor.",
    "the model's own judgement":
        "`context_fields` says a field REACHED a prompt. Whether the "
        "model used it is not a question any static reading can answer, "
        "and AGENTS.md 10.4 is why nothing here pretends otherwise.",
}


# ── The observed shape of a real run ────────────────────────────────

@dataclass(frozen=True)
class Observation:
    """One field path, as a real artefact actually carried it."""

    origin: str
    path: str
    types: Tuple[str, ...]
    occurrences: int
    bytes: int
    """Serialised size of every value at this path, summed. This is the
    answer to "what does keeping it cost"."""

    @property
    def tag(self) -> str:
        return f"{self.origin}{field_flow.TAG_SEPARATOR}{self.path}"


def _walk_shape(value, prefix: str, out: Dict[str, Dict], depth: int = 0
                ) -> None:
    """Every field path in a JSON value, with its type and its size.

    A dict whose keys are DATA - a per-clip lookup - would otherwise
    explode into one path per clip. `_looks_like_a_lookup` collapses it
    to `{}` so the shape describes the SCHEMA rather than the run.
    """
    if depth > 14:
        return
    if isinstance(value, dict):
        if _looks_like_a_lookup(value):
            for item in list(value.values())[:6]:
                _walk_shape(item, f"{prefix}{{}}", out, depth + 1)
            return
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else key
            record = out.setdefault(path, {"types": set(), "n": 0, "bytes": 0})
            record["types"].add(_type_of(item))
            record["n"] += 1
            record["bytes"] += _size_of(item)
            _walk_shape(item, path, out, depth + 1)
    elif isinstance(value, list):
        for item in value[:200]:
            _walk_shape(item, f"{prefix}[]", out, depth + 1)


_ID_KEY = re.compile(r"^(clip_\d+|IMG_\d+|[0-9a-f]{8,}|\d+|.*\.(mov|mp4|MOV|"
                     r"MP4|wav|mp3|json)|[A-Za-z0-9_\- ]+_\d+)$")


def _looks_like_a_lookup(value: dict) -> bool:
    """A dict keyed by DATA, not by field name.

    Three things turn on getting this right.  A lookup left uncollapsed
    puts one path per clip, per speaker or per finding class into the
    map and then reports all of them as unread - `conformance_report.
    by_class` alone was 558 paths where the schema has four.  It would
    also put PROJECT CONTENT into a committed file: `seconds_by_speaker`
    is keyed by the speakers' names.  And collapsing too eagerly loses
    real field names, which is why `track_levels` - keyed `A1_speech`,
    `A2_music`, `A3_sfx` with three DIFFERENT shapes under them - stays
    a schema.

    So: a key that starts with a lowercase letter is a field name, and
    the only lowercase exception is an id (`clip_003`), which needs a
    majority and at least three of them.  Where NO key is a field name,
    homogeneous values are the corroboration that the keys are data.
    """
    keys = [k for k in value.keys() if isinstance(k, str)]
    if len(keys) != len(value) or len(keys) < 2:
        return False

    nodes = _node_ids()
    if all(key in nodes for key in keys):
        # Keyed by NODE ID, which is data.  `step_errors` and
        # `edit_completed` are both, and the id list is the DAG's own -
        # derived, not a guess about what a key looks like.
        return True

    homogeneous = (len(keys) >= 3
                   and all(isinstance(item, dict) for item in value.values())
                   and len({tuple(sorted(item.keys()))
                            for item in value.values()}) == 1
                   and value[keys[0]])

    if any(_FIELD_NAME.match(key) for key in keys):
        # Field-name-shaped keys.  An id majority says they are data
        # (`clip_003`), and so does perfect structural homogeneity:
        # `neural_engine_directives` is keyed `broll_1`, `hook_hook`,
        # `speech_2_seg0` - all snake_case, no id majority - with the
        # same one-key shape under every one of them.
        if homogeneous:
            return True
        return len(keys) >= 3 and sum(
            1 for key in keys if _ID_KEY.match(key)) >= max(2, len(keys) * 0.6)

    values = list(value.values())
    if all(isinstance(item, dict) for item in values):
        shapes = {tuple(sorted(item.keys())) for item in values}
        return len(shapes) == 1 and len(values[0]) > 0
    return len({_type_of(item) for item in values}) == 1


_FIELD_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")


def _type_of(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _size_of(value) -> int:
    try:
        return len(json.dumps(value, default=str))
    except (TypeError, ValueError):
        return 0


def observe(project_folder: Path) -> Dict[str, Dict[str, Dict]]:
    """The shape of every document a real project carries.

    NO VALUES.  Only the path, the types seen at it, how many times it
    occurred and how many bytes it is - so the snapshot can be committed
    beside the code without carrying a frame of anybody's footage.

    A step's `output.json` is filed under `OUT@<node>`, not under
    `DOC@step_output`: it IS the step's declared output written out, and
    filing it twice would double every field the pipeline has.
    """
    from library.tools.project_layout import STEPS

    project_folder = Path(project_folder)
    by_dirname = {step.dirname: step.node_id for step in STEPS}
    shapes: Dict[str, Dict[str, Dict]] = {}

    def add(origin: str, value) -> None:
        _walk_shape(value, "", shapes.setdefault(origin, {}))

    state_file = project_folder / "pipeline_data.json"
    if state_file.is_file():
        state = json.loads(state_file.read_text(encoding="utf-8"))
        for node, output in (state.get("step_outputs") or {}).items():
            add(f"{field_flow.ROOT_OUTPUT}{node}", output)
        add(f"{field_flow.ROOT_DOC}pipeline_data",
            {k: v for k, v in state.items() if k != "step_outputs"})

    files = sorted(project_folder.rglob("*.json")) + \
        sorted(project_folder.rglob("*.jsonl"))
    for path in files:
        text = path.as_posix()
        if "/backups/" in text or "/archive/" in text or "/unsorted/" in text:
            continue
        if path.name == "pipeline_data.json":
            continue
        try:
            if path.suffix == ".jsonl":
                payload = [json.loads(line) for line in
                           path.read_text(encoding="utf-8").splitlines()
                           if line.strip()]
            else:
                payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if path.name == "output.json" and path.parent.name in by_dirname:
            add(f"{field_flow.ROOT_OUTPUT}{by_dirname[path.parent.name]}",
                payload)
            continue
        doc_id = document_for_filename(path.name, text)
        if not doc_id:
            shapes.setdefault("UNCLAIMED", {}).setdefault(
                path.name, {"types": set(), "n": 0, "bytes": 0}
            )["n"] += 1
            continue
        if DOCUMENTS_BY_ID[doc_id].envelope_only and isinstance(payload, dict):
            payload = {key: _summarise(value)
                       for key, value in payload.items()}
        add(f"{field_flow.ROOT_DOC}{doc_id}", payload)

    for path in sorted(project_folder.glob("*.yaml")):
        if path.name != "project.yaml":
            continue
        try:
            import yaml
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(payload, dict):
            add(f"{field_flow.ROOT_DOC}project_config", payload)

    return shapes


def _summarise(value):
    """The value's TYPE and size, with its own fields left out.

    For `envelope_only` documents.  Keeps the byte cost real - an
    archive's cost is what it stores - without re-mapping the fields of
    every document that reached the prompt."""
    return f"<{_type_of(value)} {_size_of(value)}B>"


def document_for_filename(name: str, full_path: str = "") -> str:
    """Which document a file on disk belongs to.

    The path is consulted only where the FILENAME cannot decide, which
    is the two cases `KNOWN_BLIND_SPOTS` names.
    """
    import fnmatch

    scoped = [d for d in DOCUMENTS if d.under and d.under in full_path]
    candidates = scoped or [d for d in DOCUMENTS if not d.under]

    # EXACT first, across every document, then the looser forms.  A
    # suffix literal is greedy: `vision_profile` carries `_v3.json`
    # (which is how `f"clip_profile_{stem}_v3.json"` is spelled in the
    # code), and matching in table order gave it `vision_index_v3.json`
    # - so the vision index read as a document no run has ever written.
    for document in candidates:
        if name in document.literals:
            return document.id
    for document in candidates:
        for literal in document.literals:
            if "*" in literal and fnmatch.fnmatch(name, literal):
                return document.id
    for document in candidates:
        for literal in document.literals:
            if literal[0] in "._" and name.endswith(literal):
                return document.id
    for document in candidates:
        for literal in document.literals:
            if "." not in literal and name.startswith(literal):
                # A stem: the file is that name plus a timestamp.
                return document.id
    return ""


def load_observed() -> Dict[str, Dict[str, Observation]]:
    """The committed snapshot, keyed by origin then path."""
    if not OBSERVED_FILE.is_file():
        return {}
    raw = json.loads(OBSERVED_FILE.read_text(encoding="utf-8"))
    found: Dict[str, Dict[str, Observation]] = {}
    for origin, paths in raw.get("shapes", {}).items():
        for path, row in paths.items():
            found.setdefault(origin, {})[path] = Observation(
                origin=origin, path=path, occurrences=int(row[0]),
                bytes=int(row[1]), types=tuple(row[2:]))
    return found


def observed_sources() -> List[dict]:
    if not OBSERVED_FILE.is_file():
        return []
    return json.loads(OBSERVED_FILE.read_text(encoding="utf-8")).get(
        "sources", [])


# ── The analysis, and how a tag becomes an ORIGIN ────────────────────

_WORLD = None


def analysed_world() -> field_flow.World:
    """`field_flow` run over this repository, seeded from `DOCUMENTS`.

    Cached: the fixpoint takes a couple of seconds and every entry point
    below wants the same answer.
    """
    global _WORLD
    if _WORLD is None:
        from library.tools import context_views

        views = {("library/tools/context_views.py", f"_{name}"): name
                 for name in context_views.CONTEXT_VIEWS}
        seeds = field_flow.Seeds(
            # A document identified by its DIRECTORY contributes no
            # literal.  `external_state`'s files are named for the state
            # key they stand in for - its literal is `.json` - and the
            # archives' is `*.json`, so seeding on those made every
            # `"<anything>.json"` in the tree name three documents at
            # once.  `compile_manifest.load(out_dir, "catalog.json")`
            # came back as the run archive.
            documents={d.id: d.literals for d in DOCUMENTS if not d.under},
            step_dirs=[p.name for p in (_LIBRARY_ROOT / "steps").iterdir()
                       if p.is_dir()],
            view_builders=views)
        _WORLD = field_flow.analyse(seeds)
    return _WORLD


def step_dir_to_node() -> Dict[str, str]:
    from library.tools.project_layout import STEPS

    found = {}
    for step in STEPS:
        found[f"step_{step.dirname}"] = step.node_id
    return found


def input_origins() -> Dict[str, Dict[str, str]]:
    """For every node, where each of its input keys comes FROM.

    This is `gather_step_inputs` read backwards, and it is the join that
    turns "step 3.01 reads `clip_catalog[].width`" into "step 1.02's
    `clip_catalog` carries a `width` that 3.01 reads".

    The DAG's edges answer most of it.  `NON_EDGE_INPUTS` answers the
    six routes that are not edges - without it every one of those would
    resolve to nowhere and the map would report the brief, the brand,
    the QA findings and the timeline transcript as unrouted.
    """
    from library.tools import processes

    found: Dict[str, Dict[str, str]] = {}
    for dag in processes.every_dag().values():
        for node in dag.get("nodes", []):
            found.setdefault(node["id"], {})
        for edge in dag.get("edges", []):
            mapping = edge.get("data_mapping") or {}
            table = found.setdefault(edge["to"], {})
            for source_key, destination_key in mapping.items():
                table[destination_key] = (
                    f"{field_flow.ROOT_OUTPUT}{edge['from']}"
                    f"{field_flow.TAG_SEPARATOR}{source_key}")
    for node, table in found.items():
        for key, origin in NON_EDGE_INPUTS.items():
            table.setdefault(key, origin)
    return found


def views_by_step() -> Dict[str, List[str]]:
    """Which steps declare which named context view."""
    from library.tools import context_projector, context_views

    found: Dict[str, List[str]] = {}
    for path in sorted((_LIBRARY_ROOT / "steps").glob("*/manifest.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        node = _node_for_step_dir(path.parent.name)
        for declared in (context_projector.declared_context_fields(manifest)
                         or ()):
            if context_views.is_view(declared):
                found.setdefault(context_views.view_name(declared),
                                 []).append(node or path.parent.name)
    return found


def talks_to_a_model(step_dir: Path) -> bool:
    """Whether a step's context reaches an LLM at all.

    The same three files `run_pipeline.detect_implementation` reads.  A
    DETERMINISTIC step with no `context_fields` sends nothing to a
    model, so its inputs are not a prompt route; a HYBRID or LLM_ONLY
    step with none sends everything.
    """
    return (step_dir / "handoff.md").is_file() or \
        (step_dir / "bridge.py").is_file() or \
        (step_dir / "post_bridge.py").is_file()


def _node_for_step_dir(step_dir: str) -> str:
    return step_dir_to_node().get(step_dir, "")


def resolve(tag: str, inputs: Dict[str, Dict[str, str]] = None,
            sources: Dict[Tuple[str, str], Set[str]] = None,
            depth: int = 0) -> str:
    """A tag as the MAP names it: an origin plus a field path.

    Four rewrites, and each closes a route the map would otherwise
    report as coming from nowhere:

      `IN@<step dir>#k...`             a step's input -> its producer
      `DOC@pipeline_data#step_outputs.n.k`  state -> that node's output
      `CLASS@mod:Cls#attr...`          a dataclass attribute -> the
                                       document field it was built from
      `VIEW@name#k...`                 handled by the caller, which
                                       knows which STEP declared it

    Bounded recursion: an input can resolve to an output that a class
    was built from, and two hops is as deep as this tree goes.
    """
    if depth > 4:
        return tag
    inputs = input_origins() if inputs is None else inputs
    sources = constructor_sources() if sources is None else sources
    root, path = field_flow.split(tag)

    if root.startswith(field_flow.ROOT_STEP_INPUTS):
        node = _node_for_step_dir(root[len(field_flow.ROOT_STEP_INPUTS):])
        head, marker, rest = _first_segment(path)
        origin = inputs.get(node, {}).get(head)
        if origin:
            return resolve(_join_path(origin + marker, rest), inputs,
                           sources, depth + 1)
        return tag

    if root == f"{field_flow.ROOT_DOC}pipeline_data" \
            and path.startswith("step_outputs."):
        rest = path[len("step_outputs."):]
        node, marker, tail = _first_segment(rest)
        if node in _node_ids():
            return resolve(
                _join_path(f"{field_flow.ROOT_OUTPUT}{node}"
                           f"{field_flow.TAG_SEPARATOR}", tail).replace(
                               f"{field_flow.TAG_SEPARATOR}.",
                               field_flow.TAG_SEPARATOR)
                if tail else f"{field_flow.ROOT_OUTPUT}{node}",
                inputs, sources, depth + 1)
        return tag

    if root.startswith(field_flow.ROOT_CLASS) and path:
        head, marker, rest = _first_segment(path)
        for source in sorted(sources.get(
                (root[len(field_flow.ROOT_CLASS):], head), ())):
            return resolve(_join_path(source + marker, rest), inputs,
                           sources, depth + 1)
    return tag


def _first_segment(path: str) -> Tuple[str, str, str]:
    """`clip_catalog[].width` -> `("clip_catalog", "[]", "width")`.

    The `[]` MARKER has to survive the rewrite.  Dropping it turned
    `reading.assumes_known[].quote` into `assumes_known.quote` - a field
    no artefact has - and reported it as a reader of a key that cannot
    exist, when the code is iterating the list correctly.
    """
    head, _, rest = path.partition(".")
    marker = ""
    if "[" in head:
        head, marker = head.split("[", 1)
        marker = "[" + marker
    return head, marker, rest


def _node_ids() -> Set[str]:
    from library.tools import processes

    found = set()
    for dag in processes.every_dag().values():
        for node in dag.get("nodes", []):
            found.add(node["id"])
    return found


_SOURCES = None


def constructor_sources() -> Dict[Tuple[str, str], Set[str]]:
    """`(module:Class, attribute) -> the tags it was constructed from`.

    Derived, not declared: `ReelMoment.from_dict` names every JSON key
    it reads beside the attribute it fills, so the mapping between the
    proposal document on disk and the object the reel path actually
    reads is mechanical.
    """
    global _SOURCES
    if _SOURCES is None:
        found: Dict[Tuple[str, str], Set[str]] = {}
        for source in analysed_world().constructor_sources:
            tag = field_flow.canonical(source.source_tag)
            if tag.startswith(field_flow.ROOT_CLASS):
                continue
            found.setdefault((source.cls, source.field), set()).add(tag)
        _SOURCES = found
    return _SOURCES


# ── The prompt route ────────────────────────────────────────────────

def prompt_fields() -> Dict[str, Set[str]]:
    """Every field a step's own `context_fields` puts in front of a model.

    Two shapes.  A DOTTED PATH names the field directly and its `*`
    means "every element", which is this map's `[]`.  A BARE INPUT NAME
    means the whole value travels, so every field under it is prompted -
    recorded as the prefix, and matched as one.

    `-`-prefixed paths are DROPS (`context_projector.project_fields`),
    so they are subtracted: `-timed_spine.structure.*.word_timestamps`
    is the reason no per-word timing reaches a planning prompt
    (AGENTS.md 10.1) and a map that ignored the minus would report the
    opposite.
    """
    from library.tools import context_projector, context_views

    inputs = input_origins()
    sources = constructor_sources()
    reached: Dict[str, Set[str]] = {}
    """Declared paths.  A PREFIX: a bare input name means the whole
    value travels."""
    exact: Dict[str, Set[str]] = {}
    """View-derived.  EXACT, never a prefix: a view builder reads its
    step's whole input on the way to building a small dict, and treating
    that intermediate read as a delivery would report every field of
    every input as reaching every prompt that declares a view."""
    dropped: Dict[str, Set[str]] = {}

    for path in sorted((_LIBRARY_ROOT / "steps").glob("*/manifest.json")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        node = _node_for_step_dir(path.parent.name) or path.parent.name
        declarations = context_projector.declared_context_fields(manifest)
        if declarations is None:
            # The step declares NOTHING and is handed every byte it was
            # routed (AGENTS.md 10.1).  For a step that talks to a model
            # that means every field of every input reaches the prompt,
            # and recording only the declared paths would report the
            # opposite.  `render` and `validate` are in this state by a
            # standing decision.
            if not talks_to_a_model(path.parent):
                continue
            for key, origin in inputs.get(node, {}).items():
                reached.setdefault(resolve(origin, inputs, sources),
                                   set()).add(f"{node} (undeclared)")
            continue
        for declared in declarations:
            if context_views.is_view(declared):
                continue
            drop = declared.startswith("-")
            text = declared[1:] if drop else declared
            head, _, rest = text.partition(".")
            origin = inputs.get(node, {}).get(head) or _own_output(
                node, path.parent.name, head)
            if not origin:
                continue
            tail = rest.replace(".*", "[]").replace("*.", "[].")
            tag = _join_path(origin, tail)
            tag = resolve(tag, inputs, sources)
            (dropped if drop else reached).setdefault(tag, set()).add(node)

    for view, nodes in views_by_step().items():
        for access in analysed_world().accesses:
            if access.root != f"{field_flow.ROOT_VIEW}{view}":
                continue
            head, _, rest = access.path.partition(".")
            head = head.split("[")[0]
            for node in nodes:
                origin = inputs.get(node, {}).get(head)
                if not origin:
                    continue
                marker = access.path[len(head):].split(".")[0]
                marker = marker if marker.startswith("[") else ""
                tag = _join_path(f"{origin}{marker}", rest)
                exact.setdefault(resolve(tag, inputs, sources),
                                 set()).add(f"{node} (view:{view})")
    return {"reached": reached, "dropped": dropped, "exact": exact}


# ── The map ─────────────────────────────────────────────────────────

ROUTE_CODE = "code"
ROUTE_PROMPT = "prompt"
ROUTE_EXTERNAL = "external"


@dataclass(frozen=True)
class FieldRow:
    """One field: what it is, who writes it, who reads it, what breaks."""

    origin: str
    path: str
    observation: object = None
    readers: Tuple = ()
    prompts: Tuple[str, ...] = ()
    name_read_somewhere: bool = False
    """The field's LEAF NAME appears in a read position somewhere in the
    tree, on some dict.

    A SECOND, independently written instrument -
    `output_contract._read_literals`, which knows nothing about
    documents or dataflow - looking at the same question from the only
    other angle available.  Where this is False and the field is unread,
    two instruments agree and there is no line anywhere that could be
    the missed reader.  Where it is True, the name occurs and this map
    says it occurs on a different dict; that is the case to check by
    hand with `--field`."""

    only_ambiguous: bool = False
    """Every reader of this field is on a line that resolves to more
    than one origin, so the READ is real but which document it lands on
    is not certain.  `phantom_reads` says how many lines are like this
    and why."""
    dropped_from: Tuple[str, ...] = ()
    """Steps whose `context_fields` explicitly DROP it with `-`.  Being
    dropped is a decision, and a field dropped by every step that could
    have carried it is a different answer from one nobody thought
    about."""

    @property
    def tag(self) -> str:
        return f"{self.origin}{field_flow.TAG_SEPARATOR}{self.path}"

    @property
    def external(self) -> str:
        return EXTERNAL_READERS.get(self.origin, "")

    @property
    def routes(self) -> Tuple[str, ...]:
        found = []
        if self.readers:
            found.append(ROUTE_CODE)
        if self.prompts:
            found.append(ROUTE_PROMPT)
        if self.external:
            found.append(ROUTE_EXTERNAL)
        return tuple(found)

    @property
    def unread(self) -> bool:
        return not self.routes

    @property
    def observed(self) -> bool:
        return self.observation is not None

    @property
    def bytes(self) -> int:
        return self.observation.bytes if self.observation else 0

    @property
    def types(self) -> Tuple[str, ...]:
        return self.observation.types if self.observation else ()

    @property
    def breaks(self) -> str:
        """The STRONGEST consequence of the field being absent.

        Derived from the read shapes, never guessed.  A field read once
        with `d["k"]` and five times with `.get(k, 0)` breaks the run,
        because the subscript is what happens first."""
        if not self.readers:
            if self.prompts:
                return ("nothing raises - the model plans without it "
                        "(reaches: " + ", ".join(sorted(self.prompts)[:3])
                        + ")")
            if self.external:
                return "read outside Python: " + self.external
            return "NOTHING - no reader, in code or in a prompt"
        shapes = {access.shape for access in self.readers}
        for shape in field_flow.BREAK_ORDER:
            if shape in shapes:
                if shape == field_flow.GET_DEFAULT:
                    defaults = sorted({a.default for a in self.readers
                                       if a.shape == shape and a.default})
                    if defaults:
                        return (field_flow.BREAKS[shape] + ": "
                                + ", ".join(defaults[:3]))
                return field_flow.BREAKS[shape]
        return ""

    def evidence(self, limit: int = 3) -> str:
        if self.readers:
            return ", ".join(f"{a.module}:{a.line}"
                             for a in self.readers[:limit])
        if self.prompts:
            return ", ".join(sorted(self.prompts)[:limit])
        return self.external


def survey() -> List[FieldRow]:
    """Every field the pipeline really carries, with its routes.

    The universe is WHAT WAS OBSERVED, plus what a prompt is declared to
    carry, plus every read that lands on an observed field.  A read of
    something no artefact ever had is not a field - it is either a step
    the snapshot's projects never ran, or a reader of a key that cannot
    exist, and `phantom_reads()` separates those two.
    """
    world = analysed_world()
    inputs = input_origins()
    sources = constructor_sources()
    observed = load_observed()
    prompts = prompt_fields()

    readers: Dict[str, List] = {}
    lines: Dict[Tuple[str, int, str], Set[str]] = {}
    for access in world.accesses:
        if access.root.startswith((field_flow.ROOT_VIEW, field_flow.ROOT_PATH)):
            continue  # the view is counted on the prompt side
        tag = resolve(access.tag, inputs, sources)
        if tag.startswith(field_flow.ROOT_CLASS):
            continue  # an object this map has no document for
        origin = field_flow.split(tag)[0]
        matched = observed_key(tag, observed)
        if matched:
            tag = f"{origin}{field_flow.TAG_SEPARATOR}{matched}"
        readers.setdefault(tag, []).append(access)
        lines.setdefault((access.module, access.line, access.shape),
                         set()).add(origin)

    ambiguous = {key for key, origins in lines.items() if len(origins) > 1}

    universe: Set[str] = set()
    for origin, paths in observed.items():
        for path in paths:
            universe.add(f"{origin}{field_flow.TAG_SEPARATOR}{path}")
    universe |= set(prompts["reached"])
    universe |= {tag for tag in readers if _is_observed(tag, observed)}

    rows: List[FieldRow] = []
    for tag in sorted(universe):
        origin, path = field_flow.split(tag)
        if not path:
            continue
        found = sorted(readers.get(tag, ()), key=lambda a: (a.module, a.line))
        reaches = _prompts_for(tag, prompts)
        rows.append(FieldRow(
            origin=origin, path=path,
            observation=observed.get(origin, {}).get(path),
            readers=tuple(found),
            name_read_somewhere=_leaf_of(path) in _names_in_read_position(),
            only_ambiguous=bool(found) and all(
                (a.module, a.line, a.shape) in ambiguous for a in found),
            prompts=tuple(sorted(reaches["reached"])),
            dropped_from=tuple(sorted(reaches["dropped"]))))
    return rows


_READ_NAMES = None


def _names_in_read_position() -> Set[str]:
    """Every string a module anywhere uses to READ a value.

    Borrowed whole from `output_contract`, deliberately: a corroboration
    is only worth something if the second instrument was written for a
    different purpose and shares no code with the first.
    """
    global _READ_NAMES
    if _READ_NAMES is None:
        from library.tools import output_contract

        found: Set[str] = set()
        for path in output_contract._python_files():
            found |= set(output_contract._read_literals(path))
        _READ_NAMES = found
    return _READ_NAMES


def _leaf_of(path: str) -> str:
    return path.split(".")[-1].split("[")[0].split("{")[0]


def _is_observed(tag: str, observed: Dict[str, Dict]) -> bool:
    return bool(observed_key(tag, observed))


_BY_HEAD: Dict[str, Dict[str, List[str]]] = {}


def observed_key(tag: str, observed: Dict[str, Dict]) -> str:
    """The observed path a read lands on, allowing for COLLAPSED keys.

    `observe` folds a dict keyed by data into `{}` - `tracks{}.clips`
    rather than one path per track - while the code reads
    `tracks["V1"]["clips"]` by name and `manifest["tracks"][name]`
    dynamically.  Matching literally left the whole manifest's track
    content reading as unread, which is the opposite of true: placing
    those clips is the renderer's entire job.

    So an observed `X{}` matches a read `X` followed by ONE data key,
    literal or dynamic, and everything else must match name for name.
    """
    origin, path = field_flow.split(tag)
    paths = observed.get(origin)
    if not paths:
        return ""
    if path in paths:
        return path
    if origin not in _BY_HEAD:
        index: Dict[str, List[str]] = {}
        for known in paths:
            index.setdefault(_components(known)[0][0], []).append(known)
        _BY_HEAD[origin] = index
    read = _components(path)
    if not read:
        return ""
    for known in _BY_HEAD[origin].get(read[0][0], ()):
        if _same_field(_components(known), read):
            return known
    return ""


def _components(path: str) -> List[Tuple[str, str]]:
    """`tracks{}.clips[].source_file` -> `[(tracks, {}), (clips, []),
    (source_file, )]`."""
    out: List[Tuple[str, str]] = []
    for chunk in path.split("."):
        marker = ""
        while chunk.endswith("[]") or chunk.endswith("{}"):
            marker = chunk[-2:] + marker
            chunk = chunk[:-2]
        out.append((chunk, marker))
    return out


def _same_field(known: List[Tuple[str, str]],
                read: List[Tuple[str, str]]) -> bool:
    known_at = read_at = 0
    while known_at < len(known) and read_at < len(read):
        name, marker = known[known_at]
        read_name, read_marker = read[read_at]
        if name != read_name:
            return False
        if marker == "{}":
            # The next read component is the KEY, which is data.
            if read_marker == "[]":
                read_at += 1          # the code indexed it dynamically
            else:
                read_at += 2          # the code named one key
        elif marker == read_marker:
            read_at += 1
        else:
            return False
        known_at += 1
    return known_at == len(known) and read_at == len(read)


def phantom_reads() -> Dict[str, List]:
    """Reads that land on nothing any observed artefact carried.

    Two very different things, and the difference is the whole point:

    * **unexercised** - the origin itself was never observed, because
      the snapshot's two projects did not run that step.  Says nothing.
    * **absent** - the origin WAS observed, its parent path WAS
      observed, and this key was not there.  That is a reader of a key
      that cannot exist, which is the defect
      `tests/test_asked_fields_have_readers.py` enforces on
      `creative_direction` and this generalises.

    A line that reads through a helper called with several different
    literals (`compile_manifest.load(out_dir, "<step>.json")`) resolves
    to every one of those step outputs at once, so it CANNOT answer the
    absent question - one of its origins is right and the rest are its
    shadow.  Those are counted and excluded, and the count is the size
    of that blind spot.
    """
    world = analysed_world()
    inputs = input_origins()
    sources = constructor_sources()
    observed = load_observed()

    lines: Dict[Tuple[str, int, str], Set[str]] = {}
    resolved: Dict[str, List] = {}
    for access in world.accesses:
        if access.root.startswith((field_flow.ROOT_VIEW, field_flow.ROOT_PATH)):
            continue
        tag = resolve(access.tag, inputs, sources)
        if tag.startswith(field_flow.ROOT_CLASS):
            continue
        matched = observed_key(tag, observed)
        if matched:
            tag = (f"{field_flow.split(tag)[0]}"
                   f"{field_flow.TAG_SEPARATOR}{matched}")
        resolved.setdefault(tag, []).append(access)
        lines.setdefault((access.module, access.line, access.shape),
                         set()).add(field_flow.split(tag)[0])

    out = {"unexercised": [], "absent": [], "ambiguous_line": [],
           "scalar_parent": []}
    for tag, accesses in sorted(resolved.items()):
        if _is_observed(tag, observed):
            continue
        origin, path = field_flow.split(tag)
        if origin not in observed:
            out["unexercised"].append((tag, accesses))
            continue
        parent = path.rsplit(".", 1)[0] if "." in path else ""
        parent = parent.rstrip("[]")
        if parent and parent not in observed[origin]:
            out["unexercised"].append((tag, accesses))
            continue
        if parent and not ({"object", "array"} &
                           set(observed[origin][parent].types)):
            # The parent is a NUMBER or a STRING in every artefact, so
            # it has no fields at all and this is not a claim about the
            # data - it is one local name holding two different things
            # in one function, which a flow-insensitive reading unions.
            # `render_qa` has `target` for both a spec dict and a level.
            out["scalar_parent"].append((tag, accesses))
            continue
        if all((a.module, a.line, a.shape) in
               {k for k, v in lines.items() if len(v) > 1}
               for a in accesses):
            out["ambiguous_line"].append((tag, accesses))
            continue
        out["absent"].append((tag, accesses))
    return out


def _prompts_for(tag: str, prompts: Dict[str, Dict[str, Set[str]]]
                 ) -> Dict[str, Set[str]]:
    """Which steps' prompts carry this field, by PREFIX.

    A bare input name in `context_fields` - `creative_direction`,
    `speech_sequence` - means the WHOLE value travels, so every field
    under it is in front of the model.  Matching exactly instead of by
    prefix reported the spine's `source_start` as reaching no prompt,
    when `mesh_spine` declares `speech_sequence` outright.

    A `-` path SUBTRACTS from what the paths above it selected
    (`context_projector.project_fields`), so a drop that is a prefix of
    the field removes it.  `-speech_sequence.body_sequence.*.
    word_timestamps` is why no per-word timing reaches a planning prompt
    (AGENTS.md 10.1) and a map that ignored the minus would say the
    opposite.
    """
    reached: Set[str] = set()
    dropped: Set[str] = set()
    for prefix, nodes in prompts["reached"].items():
        if _covers(prefix, tag):
            reached |= nodes
    reached |= prompts["exact"].get(tag, set())
    for prefix, nodes in prompts["dropped"].items():
        if _covers(prefix, tag):
            dropped |= nodes
    # A drop is the STEP's, whichever route delivered it, so the
    # subtraction is on the node and not on the label the route wrote.
    return {"reached": {node for node in reached
                        if _base_node(node) not in
                        {_base_node(n) for n in dropped}},
            "dropped": dropped}


def _own_output(node: str, step_dir: str, key: str) -> str:
    """A `context_fields` entry naming the step's OWN declared output.

    `output_contract`'s OWN-PROMPT route from this side: a pre-bridge
    emits a table and the step declares it as an output, and
    `run_pipeline.project_step_context` restores bridge-supplied keys BY
    NAME after projection precisely so the projection cannot drop it.
    Step 3.04's `reel_candidates` is 48,892 bytes of measurement that
    travels only this way, and resolving only INPUTS reported every byte
    of it as reaching nobody.
    """
    own = (f"{field_flow.ROOT_OUTPUT}{node}"
           f"{field_flow.TAG_SEPARATOR}{key}")
    manifest_path = _LIBRARY_ROOT / "steps" / step_dir / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for output in manifest.get("interface", {}).get("outputs", ()):
            if output.get("name") == key:
                return own
    bridge = _LIBRARY_ROOT / "steps" / step_dir / "bridge.py"
    if bridge.is_file() and _emits(bridge, key):
        # A key the bridge emits that the manifest does NOT declare as
        # an output.  `reel_candidates` is one: 3.04's manifest declares
        # `reel_selection` alone, so `output_contract` cannot see the
        # 48,892 bytes of candidate measurement its bridge builds, and
        # `context_fields` names it to keep the projector from dropping
        # it.  Recorded in `docs/DATA_MAP.md`.
        return own
    return ""


def _emits(bridge: Path, key: str) -> bool:
    """Does this pre-bridge write a dict key of this name?

    A dict-literal KEY, which is the WRITE polarity `output_contract.
    _string_constants` uses for exactly this route - the opposite of a
    read, and the distinction #601 turned on.
    """
    import ast

    try:
        tree = ast.parse(bridge.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for candidate in node.keys:
                if isinstance(candidate, ast.Constant) and \
                        candidate.value == key:
                    return True
    return False


def _join_path(base: str, tail: str) -> str:
    """`OUT@catalog#clip_catalog` + `[].width` -> `...clip_catalog[].width`.

    A dot before `[]` is a different field path, and the map joined on
    one until 2026-09-07: `clip_catalog.*.width` became
    `clip_catalog.[].width`, which matched nothing observed, so every
    per-clip field `select_broll` declares read as reaching no prompt.
    """
    if not tail:
        return base
    return base + tail if tail.startswith("[") else f"{base}.{tail}"


def _base_node(label: str) -> str:
    return label.split(" (")[0]


def _covers(prefix: str, tag: str) -> bool:
    return tag == prefix or tag.startswith(prefix + ".") or \
        tag.startswith(prefix + "[]")


def write_observed(projects: Sequence[Path], destination: Path = None) -> dict:
    """Merge the shapes of one or more real runs into the snapshot.

    Committed, because the map has to be checkable without reaching a
    real project - `tests/test_tests_never_reach_real_projects.py` says
    a test builds its project under `tmp_path` or it skips.  A structure
    snapshot is the way both can be true: the shape is committed, the
    project is not read at test time, and `--observe` regenerates it.

    The snapshot carries NO VALUES.  Field paths, the types seen at each,
    an occurrence count and a byte total - nothing that could reproduce
    a frame, a transcript line or a name.
    """
    destination = destination or OBSERVED_FILE
    merged: Dict[str, Dict[str, Dict]] = {}
    sources = []
    for project in projects:
        project = Path(project)
        shapes = observe(project)
        unclaimed = shapes.pop("UNCLAIMED", {})
        families: Dict[str, int] = {}
        for name in unclaimed:
            families[re.sub(r"\d+", "N", name)] = families.get(
                re.sub(r"\d+", "N", name), 0) + 1
        sources.append({
            "project": project.name,
            "origins": len(shapes),
            "paths": sum(len(v) for v in shapes.values()),
            "unclaimed_files": len(unclaimed),
            "unclaimed_families": dict(sorted(families.items())),
        })
        for origin, paths in shapes.items():
            target = merged.setdefault(origin, {})
            for path, record in paths.items():
                slot = target.setdefault(
                    path, {"types": set(), "n": 0, "bytes": 0})
                slot["types"] |= set(record["types"])
                slot["n"] += record["n"]
                slot["bytes"] += record["bytes"]

    payload = {
        "what": "The SHAPE of what real runs wrote. NO VALUES: a field "
                "path, the types seen at it, how many times it occurred "
                "and how many bytes of the artefact it is.",
        "regenerate": "python3 -m library.tools.data_map --observe "
                      "<project folder> [<project folder> ...]",
        "row": "[occurrences, bytes, types...]",
        "sources": sources,
        "shapes": {origin: {path: [record["n"], record["bytes"]]
                            + sorted(record["types"])
                            for path, record in sorted(paths.items())}
                   for origin, paths in sorted(merged.items())},
    }
    destination.write_text(_compact(payload), encoding="utf-8")
    return payload


def _compact(payload: dict) -> str:
    """One line per field path.

    `json.dumps(indent=...)` puts a three-element row on five lines and
    triples the file.  One line per path keeps a diff readable: a field
    that appears or disappears is one line."""
    lines = ["{"]
    for key in ("what", "regenerate", "row"):
        lines.append(f" {json.dumps(key)}: {json.dumps(payload[key])},")
    lines.append(f" \"sources\": {json.dumps(payload['sources'], indent=2)},")
    lines.append(' "shapes": {')
    origins = list(payload["shapes"].items())
    for index, (origin, paths) in enumerate(origins):
        lines.append(f"  {json.dumps(origin)}: {{")
        rows = list(paths.items())
        for row_index, (path, row) in enumerate(rows):
            comma = "," if row_index < len(rows) - 1 else ""
            lines.append(f"   {json.dumps(path)}: {json.dumps(row)}{comma}")
        lines.append("  }" + ("," if index < len(origins) - 1 else ""))
    lines.append(" }")
    lines.append("}")
    return "\n".join(lines) + "\n"


# ── The gate ────────────────────────────────────────────────────────
#
# Three checks, and each has to be able to fail in BOTH directions -
# AGENTS.md 10.4 calls a gate that cannot fail and a gate that fails
# correct output the same defect from either side.
#
#   ANCHORS          a field this map must find a reader for.  Breaks
#                    when the analyzer stops looking; does NOT break
#                    when a new unread field appears.
#   UNREAD_BUDGET    how many observed fields of each origin nothing
#                    reads.  Breaks when data is added that nothing
#                    reads; does NOT break when a reader is added - the
#                    count goes DOWN and the stale budget is what fails,
#                    which is the ratchet moving in the right direction.
#   NOT_OBSERVED     a document the snapshot has never seen.  Breaks
#                    when a document is added and nobody runs it; does
#                    not break when one starts being produced.

ANCHORS: Dict[str, str] = {
    "OUT@catalog#clip_catalog[].width":
        "3.01 decides `needs_conform` on it (step.py:34), through a "
        "lookup dict built by comprehension - the idiom that made every "
        "per-clip read invisible to the first version of this map.",
    "OUT@catalog#project_fps":
        "The timebase. Its manifest entry says in as many words that "
        "every consumer used to read its own 30.0 default because no "
        "edge carried it.",
    "OUT@mesh_spine#audio_spine.structure[].block_type":
        "The spine contract (AGENTS.md 6). A SUBSCRIPT, so its absence "
        "raises rather than defaulting.",
    "OUT@music_analysis#music_analysis.tempo.beats":
        "The beat grid (AGENTS.md 10.1), read through a `key` PARAMETER "
        "in `beat_grid._times` whose two callers pass the literals. If "
        "string-constant propagation regresses this is the field that "
        "goes first.",
    "OUT@music_selection#music_selection.section":
        "Which SECTION of the track plays (AGENTS.md 10.5), read via a "
        "module constant `SECTION_KEY` and through "
        "`compile_manifest.load(out_dir, \"music_selection.json\")` - "
        "two indirections at once.",
    "OUT@semantic_analysis#semantic_analysis_documents[].analysis.scene":
        "Declared by four steps' `context_fields` AND read by "
        "`step_5_01_color_grade/grade.py`. Both routes at once.",
    "OUT@temporal_index#temporal_event_indices[].speech_regions[].text":
        "Reaches two prompts through `view:transcript` and NOTHING "
        "else. If the view route regresses, the whole prompt side of "
        "the map goes quiet.",
    "DOC@reel_proposals_v2#moments[].timeline_start":
        "The reel path's spine. Read as an ATTRIBUTE off `ReelMoment`, "
        "which `from_dict` builds from this key - so it only resolves "
        "if the constructor mapping does.",
    "DOC@reel_proposals_v2#moments[].approval":
        "The gate (AGENTS.md 10.4): a reel is BUILT only once the "
        "captain approves it.",
    "DOC@timeline_transcript#segments[].text":
        "The master timeline's speech, and the reel quality bar reads "
        "it.",
}

NOT_READ_ANCHORS: Dict[str, str] = {
    "OUT@catalog#source_resolution":
        "`output_contract.REPORTED_NOT_CONSUMED` says the same, in "
        "detail, and says why it must stay unread. If this map ever "
        "reports a reader, one of the two instruments is wrong.",
    "OUT@speech_sequence#speech_sequence.body_sequence[].word_timestamps":
        "DROPPED with a `-` path by both steps that could carry the "
        "spine into a prompt, and read by no code. The minus is the "
        "only thing keeping 8,509 per-word timings out of the planning "
        "prompts (AGENTS.md 10.1); a map that lost the minus would "
        "report it as prompted.",
}


KNOWN_MISSED_READS: Dict[str, str] = {}
"""Fields this map reports as unread that a HAND CHECK found a reader for.

The map errs in one direction and this is what that direction looks
like when it happens.  An entry here is NOT a finding and must not be
ranked as carried-and-unused; it is a measured hole in the instrument,
with the reader named so the claim is checkable.

`disagreements` fails on a STALE entry - one whose field the map now
reads - because a recorded hole that has closed reads as a hole that is
still open.
"""


NOT_OBSERVED: Dict[str, str] = {
    "step_output":
        "Filed under `OUT@<node>` by `observe`, because it IS the step's "
        "declared output written out. Counting it twice would double "
        "every field in the pipeline.",
    "step_summary":
        "Markdown. `step_exporter.generate_summary` renders it "
        "GENERICALLY from whatever keys the output carries, so it has "
        "no fields of its own - which is also why `output_contract` "
        "says a value reaching only the summary is REPORTED, not read.",
    "run_traceback":
        "Markdown, regenerated from the ledgers on every run.",
    "brand_template":
        "Lives in the REPOSITORY (`library/templates/`), not in a "
        "project, and `observe` walks a project folder. Its fields "
        "reach steps as `brand_style`/`brand_effect`/`brand_content` "
        "(AGENTS.md 10.1).",
    "hooks": "Neither snapshot project declares any.",
    "external_state":
        "Neither snapshot project supplies state from outside the "
        "pipeline (AGENTS.md 3).",
    "prosody":
        "1.05's per-clip files survive only in 001's `backups/`, which "
        "`observe` skips; the live 1.05 directory has `output.json` "
        "alone. The measurements themselves are mapped as "
        "`OUT@prosody_analysis`.",
    "segmentation":
        "Step 1.06 is UNWIRED - nothing consumes masks (AGENTS.md 3), "
        "so nothing runs it.",
    "footage_index":
        "Built ONLY when the reviewer presses the button (AGENTS.md 4). "
        "001's copy is under `archive/`, which `observe` skips.",
    "sfx_index":
        "In the SFX library at `PIPELINE_SFX_LIBRARY`, outside any "
        "project folder.",
    "assembly_manifest":
        "Neither project carries one outside `backups/`. The manifest "
        "travels in state as `OUT@compile_manifest#assembly_manifest`, "
        "which IS mapped in full - the file is a copy of it, and "
        "`tests/test_manifest_readers.py` holds its top-level keys to "
        "named readers separately.",
    "review_channel": "No reviewer notes were sent on either project.",
    "marker_routing":
        "001 carries the collected notes (`marker_pull`, mapped) and no "
        "routed-notes file.",
}
"""Documents no run in the snapshot has produced, and why.

An entry that starts being produced is stale and `disagreements` says
so, because a recorded absence that is no longer true reads as coverage.
"""


UNREAD_BUDGET: Dict[str, int] = {
    "DOC@conformance_report": 203,
    "DOC@gate_feedback": 0,
    "DOC@gate_snapshot": 12,
    "DOC@gate_status": 2,
    "DOC@llm_request": 7,
    "DOC@llm_response": 15,
    "DOC@marker_pull": 31,
    "DOC@migration": 49,
    "DOC@music_analysis_doc": 47,
    "DOC@ocr_result": 4,
    "DOC@pipeline_data": 35,
    "DOC@pipeline_run": 24,
    "DOC@plan_provenance": 2,
    "DOC@project_config": 33,
    "DOC@provenance": 27,
    "DOC@qa_report": 78,
    "DOC@reel_judgement": 19,
    "DOC@reel_proposals_v2": 58,
    "DOC@render_batch_jobs": 0,
    "DOC@resolve_placements": 12,
    "DOC@subtitle_props": 0,
    "DOC@temporal_index_clip": 44,
    "DOC@timeline_decisions": 9,
    # 8 -> 7 on 2026-09-07: `segments[].words[].timed` gained its first
    # reader. The animated explainer anchors each stage to the WORD its
    # quote begins on (`reel_quality_bar.played_speech(with_words=True)`),
    # because the six sources reel 21 enumerates sit in TWO transcript
    # segments - at line precision the whole build collapses onto two
    # instants. Per-word timings had been written and never read.
    "DOC@timeline_transcript": 7,
    "DOC@vision_index": 64,
    "DOC@vision_profile": 17,
    "OUT@assign_aroll": 13,
    "OUT@audio_mix": 41,
    "OUT@build_reels": 5,
    "OUT@catalog": 12,
    "OUT@color_grade": 40,
    "OUT@compile_manifest": 171,
    "OUT@creative_cohesion": 16,
    "OUT@creative_direction": 0,
    "OUT@judge_reels": 35,
    "OUT@mesh_spine": 16,
    "OUT@music_analysis": 1,
    "OUT@music_selection": 0,
    "OUT@ocr_extraction": 17,
    "OUT@plan_sfx": 20,
    "OUT@plan_subtitles": 24,
    "OUT@plan_transitions": 15,
    "OUT@plan_vfx": 1,
    "OUT@prosody_analysis": 9,
    "OUT@render": 19,
    "OUT@render_motion_graphics": 7,
    "OUT@render_subtitles": 14,
    "OUT@review_rough_cut": 0,
    "OUT@scan": 4,
    "OUT@select_broll": 0,
    "OUT@select_reels": 66,
    "OUT@semantic_analysis": 18,
    "OUT@speech_sequence": 10,
    "OUT@temporal_index": 18,
    "OUT@validate_sfx_library": 8,
}
"""Observed fields with no reader, per origin. MAY ONLY MOVE DOWN.

The same ratchet `scripts/check_agents_md_size.py` uses on this
repository's own index file, for the same reason: the failure mode is
not one bad number, it is a slope. Every entry is a count of data the
pipeline carries and nothing consumes.

Measured 2026-09-07 over the two projects in the snapshot: 1,554 of
2,449 observed fields. `docs/DATA_MAP.md` ranks them by what they cost
and adjudicates the ones worth acting on. NOTHING WAS DELETED - the
captain asked to know first."""


def unread_counts(rows: Sequence[FieldRow] = None) -> Dict[str, int]:
    found: Dict[str, int] = {}
    for row in (rows if rows is not None else survey()):
        if row.observed:
            found.setdefault(row.origin, 0)
            if row.unread:
                found[row.origin] += 1
    return found


def unread_cost(rows: Sequence[FieldRow] = None) -> List[Tuple[str, int, int]]:
    """`(origin, fields nothing reads, bytes they occupy)`, worst first.

    The bytes are what a real run wrote, so this IS the answer to what
    keeping a field costs - not an estimate.
    """
    rows = list(rows if rows is not None else survey())
    found: Dict[str, List[int]] = {}
    for row in rows:
        if row.tag in KNOWN_MISSED_READS:
            continue
        if row.observed and row.unread:
            slot = found.setdefault(row.origin, [0, 0])
            slot[0] += 1
            slot[1] += row.bytes
    return sorted(((origin, count, size)
                   for origin, (count, size) in found.items()),
                  key=lambda item: (-item[2], -item[1]))


def disagreements(rows: Sequence[FieldRow] = None) -> List[str]:
    rows = list(rows if rows is not None else survey())
    by_tag = {row.tag: row for row in rows}
    problems: List[str] = []

    for tag, why in sorted(ANCHORS.items()):
        row = by_tag.get(tag)
        if row is None:
            problems.append(
                f"ANCHOR {tag}: the map does not carry this field at all. "
                f"It is here because: {why}")
        elif not row.routes:
            problems.append(
                f"ANCHOR {tag}: the map reports NO reader. It is here "
                f"because: {why}")
    for tag, why in sorted(NOT_READ_ANCHORS.items()):
        row = by_tag.get(tag)
        if row is not None and row.routes:
            problems.append(
                f"ANCHOR {tag}: the map now reports {'/'.join(row.routes)} "
                f"and this field is recorded as unread. {why}")

    for tag, why in sorted(KNOWN_MISSED_READS.items()):
        row = by_tag.get(tag)
        if row is None:
            problems.append(
                f"MISSED {tag}: recorded as a read this map cannot see, "
                f"and the field is not in the map at all. Delete the "
                f"entry or re-observe.")
        elif row.routes:
            problems.append(
                f"MISSED {tag}: recorded as a read this map cannot see, "
                f"and it now reports {'/'.join(row.routes)}. Delete the "
                f"entry - a recorded hole that has closed reads as one "
                f"that is still open.")

    counts = unread_counts(rows)
    for origin, count in sorted(counts.items()):
        budget = UNREAD_BUDGET.get(origin)
        if budget is None:
            problems.append(
                f"BUDGET {origin}: {count} observed field(s) nothing "
                f"reads, and no budget row. Add one - a row that does "
                f"not exist is not a bound.")
        elif count > budget:
            problems.append(
                f"BUDGET {origin}: {count} observed field(s) nothing "
                f"reads, budget {budget}. Give them a reader, or stop "
                f"producing them - the ratchet only moves down.")
        elif count < budget:
            problems.append(
                f"BUDGET {origin}: {count} unread, budget {budget}. "
                f"Lower it to {count}: a budget larger than what is "
                f"owed is a lie about what is still owed.")
    for origin in sorted(set(UNREAD_BUDGET) - set(counts)):
        problems.append(
            f"BUDGET {origin}: budgeted, and nothing observed carries "
            f"that origin any more. Delete the row.")

    observed = load_observed()
    for document in DOCUMENTS:
        origin = f"{field_flow.ROOT_DOC}{document.id}"
        if origin in observed:
            if document.id in NOT_OBSERVED:
                problems.append(
                    f"NOT_OBSERVED {document.id}: recorded as never "
                    f"produced and the snapshot now carries it. Delete "
                    f"the entry.")
        elif document.id not in NOT_OBSERVED:
            problems.append(
                f"NOT_OBSERVED {document.id}: this map declares the "
                f"document and no run in the snapshot has produced one, "
                f"so every field in it is unmapped. Record why, or "
                f"re-observe a project that has one.")
    return problems


def blind_spot_sizes() -> Dict[str, int]:
    """How big each thing the derivation cannot see actually is.

    `KNOWN_BLIND_SPOTS` names them; this measures them, because a blind
    spot with no size reads as coverage.
    """
    world = analysed_world()
    phantom = phantom_reads()
    observed = load_observed()
    return {
        "dynamic_reads": len(world.dynamic),
        "tags_dropped_too_deep": world.too_deep,
        "tags_dropped_too_nested": world.too_nested,
        "names_widened": world.widened,
        "reads_on_an_unexercised_origin": len(phantom["unexercised"]),
        "reads_the_artefacts_say_cannot_exist": len(phantom["absent"]),
        "reads_on_a_line_with_more_than_one_origin":
            len(phantom["ambiguous_line"]),
        "reads_whose_parent_is_a_scalar": len(phantom["scalar_parent"]),
        "unread_whose_name_is_read_nowhere_at_all": sum(
            1 for row in survey()
            if row.observed and row.unread and not row.name_read_somewhere),
        "documents_declared": len(DOCUMENTS),
        "documents_never_observed": len(NOT_OBSERVED),
        "observed_origins": len(observed),
        "observed_fields": sum(len(v) for v in observed.values()),
    }


# ── CLI ─────────────────────────────────────────────────────────────

def format_field(row: FieldRow) -> str:
    lines = [f"{row.tag}",
             f"  is        : {'/'.join(row.types) or 'not observed'}"
             f"  ({row.observation.occurrences if row.observation else 0} "
             f"occurrence(s), {row.bytes} bytes on the run of record)",
             f"  written by: {_writer_of(row.origin)}",
             f"  read by   : {'/'.join(row.routes) or 'NOBODY'}"]
    seen = set()
    for access in row.readers:
        key = (access.module, access.line, access.shape)
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"      {access.shape:<12} {access.module}:{access.line}"
                     f"  ({access.func})"
                     + (f"  default {access.default}" if access.default
                        else ""))
    for node in row.prompts:
        lines.append(f"      prompt       {node}")
    if row.dropped_from:
        lines.append(f"      DROPPED from {', '.join(row.dropped_from)}"
                     f"'s prompt with a `-` path")
    if row.external:
        lines.append(f"      external     {row.external}")
    if row.only_ambiguous:
        lines.append("      NOTE: every reader is on a line that resolves "
                     "to more than one origin.")
    lines.append(f"  breaks    : {row.breaks}")
    return "\n".join(lines)


def _writer_of(origin: str) -> str:
    if origin.startswith(field_flow.ROOT_DOC):
        document = DOCUMENTS_BY_ID.get(origin[len(field_flow.ROOT_DOC):])
        return document.writer if document else origin
    if origin.startswith(field_flow.ROOT_OUTPUT):
        return f"step node `{origin[len(field_flow.ROOT_OUTPUT):]}`"
    return origin


def main(argv=None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="What each field is, who writes it, who reads it, "
                    "and what breaks without it.")
    parser.add_argument("--document", default="",
                        help="Every field of one origin "
                             "(`reel_proposals_v2`, `OUT@catalog`).")
    parser.add_argument("--field", default="", help="One field, in full.")
    parser.add_argument("--unread", action="store_true",
                        help="Everything carried and not used, ranked by "
                             "what it costs.")
    parser.add_argument("--phantom", action="store_true",
                        help="Reads that land on nothing any artefact had.")
    parser.add_argument("--bad", action="store_true", help="The gate.")
    parser.add_argument("--blind", action="store_true",
                        help="The size of each thing this cannot see.")
    parser.add_argument("--observe", nargs="+", default=None,
                        metavar="PROJECT",
                        help="Regenerate the shape snapshot from real runs.")
    args = parser.parse_args(argv)

    if args.observe:
        payload = write_observed([Path(p) for p in args.observe])
        for source in payload["sources"]:
            print(f"{source['project']:<20} {source['origins']:>3} origins  "
                  f"{source['paths']:>5} field paths  "
                  f"{source['unclaimed_files']} unclaimed file(s)")
        print(f"\nwrote {OBSERVED_FILE}")
        return 0

    if args.blind:
        for name, size in sorted(blind_spot_sizes().items()):
            print(f"{size:>8}  {name}")
        print()
        for name, why in sorted(KNOWN_BLIND_SPOTS.items()):
            print(f"BLIND SPOT  {name}\n            {why}\n")
        return 0

    rows = survey()

    if args.field:
        for row in rows:
            if row.tag == args.field:
                print(format_field(row))
                return 0
        print(f"{args.field}: no such field in the map.", file=sys.stderr)
        return 1

    if args.document:
        wanted = args.document
        if not wanted.startswith((field_flow.ROOT_DOC,
                                  field_flow.ROOT_OUTPUT)):
            wanted = f"{field_flow.ROOT_DOC}{wanted}"
        found = [row for row in rows if row.origin == wanted]
        if not found:
            print(f"{wanted}: nothing in the map.", file=sys.stderr)
            return 1
        print(f"{wanted} - written by {_writer_of(wanted)}\n")
        for row in found:
            print(f"  {'/'.join(row.routes) or 'NOBODY':<14} "
                  f"{row.path:<58} {row.bytes:>8}B  {row.breaks[:60]}")
        print(f"\n{len(found)} field(s), "
              f"{sum(1 for r in found if r.unread)} with no reader.")
        return 0

    if args.phantom:
        phantom = phantom_reads()
        for tag, accesses in sorted(phantom["absent"]):
            print(f"ABSENT  {tag}\n        "
                  f"{accesses[0].module}:{accesses[0].line} "
                  f"({accesses[0].shape})")
        print(f"\n{len(phantom['absent'])} read(s) of a key no artefact "
              f"carries, where the parent path WAS observed.")
        for name in ("unexercised", "ambiguous_line", "scalar_parent"):
            print(f"{len(phantom[name]):>8}  {name} (not a finding - see "
                  f"`phantom_reads`)")
        return 0

    if args.unread:
        for origin, count, size in unread_cost(rows):
            print(f"{size:>10}B  {count:>4} field(s)  {origin}")
        total = sum(1 for r in rows if r.observed and r.unread)
        print(f"\n{total} observed field(s) that nothing reads, of "
              f"{sum(1 for r in rows if r.observed)} observed.")
        print("NOTHING HERE IS DELETED. docs/DATA_MAP.md ranks them.")
        return 0

    if not args.bad:
        counts: Dict[str, int] = {}
        for row in rows:
            counts["/".join(row.routes) or "NOBODY"] = counts.get(
                "/".join(row.routes) or "NOBODY", 0) + 1
        print(f"{len(rows)} fields across "
              f"{len({r.origin for r in rows})} origins.")
        for route, count in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"  {count:>6}  {route}")
        print()
        for name, size in sorted(blind_spot_sizes().items()):
            print(f"  {size:>6}  {name}")

    problems = disagreements(rows)
    for problem in problems:
        print(f"DISAGREEMENT  {problem}", file=sys.stderr)
    if problems:
        print(f"\n{len(problems)} disagreement(s).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
