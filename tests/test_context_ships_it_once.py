"""Nothing reaches a prompt twice, and no raw array reaches one at all.

An audit of the ten LLM request payloads the 2026-08-26 clean run of 001
wrote to `pipeline_output/llm_requests/` found the same mistake three
times over: the stored artifact and what a reader needs were treated as
the same object, so the layer that assembles context shipped both.

  * `speech_sequence` received all 110 transcript lines as `transcript`
    (the view) and again as `transcripts_toon` (its own pre-bridge),
    19,844 characters and a quarter of the step's context - and the two
    copies disagreed about which column was the start time.
  * `analysis.scene` is `scene[]` rendered as prose and `analysis.motion`
    is `camera[]` rendered as prose.  Five steps were handed the prose
    AND the structure it was rendered from, in the same row.
  * `music_analysis` carries 274 beat times, 69 downbeats and a 198-point
    energy curve.  They reached `mesh_spine`, `plan_sfx` and
    `plan_transitions` one value per line.  Nothing reads them: beat
    proximity is decided in `plan_transitions`' post-bridge, off the
    UNPROJECTED inputs, before any model sees a context - which is what
    `tests/test_beat_grid.py` and the post-bridge's own `beat_aligned`
    column already record.

These are properties of the manifests, so they are asserted on the
manifests rather than on one project's bytes.

The 2026-09-12 audit found four more of the same shape and priced
every model call the pipeline makes: [`docs/MODEL_CALL_COST_AUDIT.md`](../docs/MODEL_CALL_COST_AUDIT.md).
"""

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

STEPS = REPO / "library" / "steps"

ALL_MANIFESTS = sorted(STEPS.glob("*/manifest.json"))


def context_fields(path: Path) -> list:
    return json.loads(path.read_text(encoding="utf-8")).get("context_fields") or []


def ids(paths):
    return [p.parent.name for p in paths]


# ── The vision prose and the structure it was rendered from ───────────

# `vision_schema_adapter.scene_prose` renders `scene[]`; `camera_prose`
# renders `camera[]`.  Either is a legitimate thing to send.  Both is not.
DERIVED_FROM = {
    "semantic_analysis_documents.*.analysis.scene":
        "semantic_analysis_documents.*.scene",
    "semantic_analysis_documents.*.analysis.motion":
        "semantic_analysis_documents.*.camera",
}


@pytest.mark.parametrize("path", ALL_MANIFESTS, ids=ids(ALL_MANIFESTS))
def test_no_step_gets_a_summary_and_its_own_source(path):
    declared = set(context_fields(path))
    for prose, raw in DERIVED_FROM.items():
        assert not (prose in declared and raw in declared), (
            f"{path.parent.name} declares {prose!r} and {raw!r}. The first "
            f"is the second rendered as prose by vision_schema_adapter - "
            f"send one of them."
        )


# ── The raw beat grid ─────────────────────────────────────────────────

RAW_MUSIC_ARRAYS = (
    "music_analysis.tempo.beats",
    "music_analysis.tempo.downbeats",
    "music_analysis.energy_dynamics.energy_curve_1hz",
)


@pytest.mark.parametrize("path", ALL_MANIFESTS, ids=ids(ALL_MANIFESTS))
def test_the_raw_beat_grid_never_reaches_a_prompt(path):
    """A step routed the whole analysis must drop the three value lists.

    `-` drop paths rather than an allow-list, for the reason AGENTS.md
    10.1 gives: naming the twenty keys to keep stops delivering the
    twenty-first.  The code that needs the grid reads it through
    `library/tools/beat_grid.py` off the unprojected inputs.
    """
    declared = context_fields(path)
    if "music_analysis" not in declared:
        return
    for array in RAW_MUSIC_ARRAYS:
        assert f"-{array}" in declared, (
            f"{path.parent.name} routes the whole `music_analysis` to its "
            f"prompt and does not drop {array!r}, which is a list of "
            f"numbers no prompt reads"
        )


# ── The transcript ────────────────────────────────────────────────────

def test_the_transcript_reaches_speech_sequence_exactly_once():
    """Its pre-bridge builds the copy the handoff names.

    `transcripts_toon` is built straight off the per-clip index files with
    a deliberate `clip_id,start,end,text` header, and 2.02's handoff tells
    the model to look up "the `transcripts_toon` data".  The view is the
    route for a step with no bridge - `creative_direction` - and declaring
    both put the same 110 lines in the prompt twice.
    """
    step = STEPS / "step_2_02_speech_sequence"
    declared = context_fields(step / "manifest.json")
    assert "view:transcript" not in declared, (
        "2.02 declares the transcript view again; its pre-bridge already "
        "builds `transcripts_toon` and the handoff reads that one"
    )
    assert '"transcripts_toon"' in (step / "bridge.py").read_text(encoding="utf-8")
    assert "view:transcript" in context_fields(
        STEPS / "step_2_01_creative_direction" / "manifest.json"), (
        "2.01 has no pre-bridge, so the view is the only route the "
        "transcript has into the creative direction's prompt"
    )


# ── The music selector's own answer, carried inside a spine ──────────

# `mesh_spine`'s output embeds the whole of `music_selection`, so every
# step routed `timed_spine` or `audio_spine` is handed the music
# selector's answer a second time.  Measured on 001's round3 snapshot:
# 6,534 B per step, in four prompts, and in `plan_transitions` beside a
# top-level copy that was byte-identical after dedent.
#
# The nested copy goes from the steps whose handoff never mentions
# music at all - `select_broll` and `plan_vfx` name it nowhere, and
# `plan_transitions` names `music_selections` in its Reads table and
# routes the top-level object for it.
#
# It STAYS in `plan_sfx`, and the reason is the data map.  Nothing in
# any tree reads `music_selection.candidates_evaluated`, and the only
# code reader of `direction_justification.why_not_forbidden` is
# `music_selection_contract.validate_selection`, which `field_flow`
# cannot follow across the call.  So the prompt IS the last route the
# map can see, and dropping it in all four steps moved
# `data_map.UNREAD_BUDGET["OUT@mesh_spine"]` from 16 to 22 - the ratchet
# firing correctly on data the pipeline still produces and now nobody
# reads.  `plan_sfx` is the right carrier of the four: its handoff is
# the only one that reasons about music ("match the music rhythm and
# energy", "SFX don't compete with prominent music moments").
#
# Stop producing it and this gate changes shape - but `data_map`'s
# counts come from `data_map_observed.json`, the SHAPE of documents a
# real run wrote, so a schema change is invisible to the ratchet until
# a project is re-observed.  That is the work this note is holding.
SPINE_KEYS = ("timed_spine", "audio_spine")

SPINE_MUSIC_CARRIER = "step_4_04_plan_sfx"

SPINE_MUSIC_DROPPED_BY = {
    "step_3_02_select_broll":
        "its handoff names music nowhere, and its Reads table lists "
        "`timed_spine` as \"structure with visual_notes and timeline "
        "positions\"",
    "step_4_03_plan_vfx":
        "its handoff names music nowhere",
    "step_4_02_plan_transitions":
        "it routes the top-level `music_selection` its handoff names, so "
        "the nested one is the same object twice",
}


@pytest.mark.parametrize("path", ALL_MANIFESTS, ids=ids(ALL_MANIFESTS))
def test_the_spine_music_copy_goes_where_nothing_reads_it(path):
    declared = context_fields(path)
    if "timed_spine" not in declared:
        return
    why = SPINE_MUSIC_DROPPED_BY.get(path.parent.name)
    if why is None:
        return
    assert "-timed_spine.music_selection" in declared, (
        f"{path.parent.name} routes `timed_spine`, which embeds the "
        f"whole of `music_selection`, and does not drop it: {why}."
    )


def test_one_step_still_carries_the_spine_music_copy():
    """The ratchet's other direction: it may not go to zero carriers.

    `data_map` counts a field with no prompt and no reader its analyzer
    can follow as carried-and-unused, and `UNREAD_BUDGET` only moves
    down.  Dropping the nested copy in `plan_sfx` too orphans six fields
    at `OUT@mesh_spine` that `mesh_spine` still writes, and
    `tests/test_data_map.py::test_the_gate_is_clean_on_this_tree` fails.
    The honest fix is to stop `mesh_spine` embedding the audit trail and
    re-observe a run - not to widen the budget.
    """
    declared = context_fields(STEPS / SPINE_MUSIC_CARRIER / "manifest.json")
    assert "timed_spine" in declared, (
        f"{SPINE_MUSIC_CARRIER} no longer routes `timed_spine`; the "
        f"spine's `music_selection` now reaches no prompt at all.")
    assert "-timed_spine.music_selection" not in declared, (
        f"{SPINE_MUSIC_CARRIER} is the last prompt carrier of "
        f"`timed_spine.music_selection`, and `mesh_spine` still writes "
        f"it. Dropping it here does not stop it being produced - it "
        f"only stops anything reading it. Stop `mesh_spine` embedding "
        f"the audit trail and re-observe, or leave this alone.")


@pytest.mark.parametrize("path", ALL_MANIFESTS, ids=ids(ALL_MANIFESTS))
def test_no_step_gets_the_music_selection_twice(path):
    """Top level AND nested in a spine is the same object, twice.

    `plan_transitions` declared `music_selection` because its handoff
    names it, and `timed_spine` because it plans against the blocks;
    the two sections were byte-identical after dedent, 6,444 B and
    6,534 B in one prompt.
    """
    declared = context_fields(path)
    if "music_selection" not in declared:
        return
    for spine in SPINE_KEYS:
        if spine not in declared:
            continue
        assert f"-{spine}.music_selection" in declared, (
            f"{path.parent.name} routes `music_selection` at the top "
            f"level and `{spine}`, which embeds the same object. Drop "
            f"`{spine}.music_selection` and keep the one the handoff "
            f"names."
        )


# ── The word timings ─────────────────────────────────────────────────

# AGENTS.md 10.1: word timings do not reach a prompt.  `speech_sequence`
# carries them in TWO places - `body_sequence[].word_timestamps` and
# `hook_segment.word_timestamps` - and both steps routed the whole
# object dropped only the first, so the hook's eleven timings reached
# both prompts.
#
# 3.03 reviews a built cut and has no use for them.  2.05 keeps them,
# for the same reason `plan_sfx` keeps the spine's music copy: it is the
# LAST prompt carrier of a field the pipeline must go on producing -
# 2.05 builds the hook block's `content.word_timestamps` from it, and
# the spine contract (AGENTS.md 6) puts `word_timestamps` on every
# block. `data_map.NOT_READ_ANCHORS` already records the body passages'
# timings as read by no code at all.
SPEECH_TIMING_PATHS = (
    "speech_sequence.body_sequence.*.word_timestamps",
    "speech_sequence.hook_segment.word_timestamps",
)

HOOK_TIMING_CARRIER = "step_2_05_mesh_spine"


def test_the_rough_cut_review_gets_no_word_timings():
    declared = context_fields(
        STEPS / "step_3_03_review_rough_cut" / "manifest.json")
    for timing in SPEECH_TIMING_PATHS:
        assert f"-{timing}" in declared, (
            f"3.03 routes the whole `speech_sequence` and does not drop "
            f"{timing!r}. Word timings do not reach a prompt "
            f"(AGENTS.md 10.1); a reviewer of a built cut reads the "
            f"passage's own `source_start`/`source_end`."
        )


def test_the_spine_author_still_carries_the_hook_timings():
    """The ratchet's other direction, again.

    Dropping these in 2.05 as well orphans four fields at
    `OUT@speech_sequence` that 2.02 still writes and `data_map` can see
    no reader for, and the data-map gate fails. 2.02 must go on writing
    them - they are what 2.05 builds the hook block's
    `content.word_timestamps` from.
    """
    declared = context_fields(STEPS / HOOK_TIMING_CARRIER / "manifest.json")
    assert "-speech_sequence.body_sequence.*.word_timestamps" in declared, (
        "2.05 routes the whole `speech_sequence` and does not drop the "
        "body passages' word timings - 8,509 of them on 001.")
    assert ("-speech_sequence.hook_segment.word_timestamps"
            not in declared), (
        "2.05 is the last prompt carrier of "
        "`speech_sequence.hook_segment.word_timestamps`, and 2.02 still "
        "writes it. Dropping it here does not stop it being produced. "
        "Give it a reader or stop producing it and re-observe, rather "
        "than orphaning it.")


# ── The QA report, beside the summary rendered from it ────────────────

def test_validate_is_not_sent_the_qa_report_and_its_own_summary():
    """6.02's bridge writes both, and the prompt carried both.

    `deterministic_validation` arrives with `status`, `checks`,
    `all_issues` and `summary` - the reading - and with `qa_report`, the
    18 raw metric rows those were rendered from, 11,737 B of nested JSON
    in one prompt on 001.  `qa_report_path` names the file on disk, and
    6.02 declares the GATING `verify_render` skill, so the rows are
    reachable rather than withheld.  The rows are inside
    `deterministic_validation`, which 6.02 declares as an output, so
    dropping them from the PROMPT does not orphan them in the data map.
    """
    declared = context_fields(
        STEPS / "step_6_02_validate_output" / "manifest.json")
    assert "-deterministic_validation.qa_report" in declared, (
        "6.02 is handed its own QA report's raw rows beside the summary "
        "rendered from them. Send one of them."
    )
