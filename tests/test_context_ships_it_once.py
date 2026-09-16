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


# ── The music audit trail, out of the spines ─────────────────────────
#
# `mesh_spine`'s output used to embed the whole of `music_selection`,
# so every step routed `timed_spine` or `audio_spine` was handed the
# music selector's answer a second time (measured on 001's round3
# snapshot: 6,534 B per step, in four prompts, and in
# `plan_transitions` beside a top-level copy that was byte-identical
# after dedent). The nested copy went from the steps whose handoff
# never mentions music at all, via `-timed_spine.music_selection`
# (3.02, 4.03, 4.02) and two `-audio_spine.music_selection.*` paths
# (3.03), while `plan_sfx` stayed the last prompt carrier - the
# ratchet `data_map.UNREAD_BUDGET["OUT@mesh_spine"]` could only move
# down, so dropping the copy everywhere orphaned fields the pipeline
# still produced.
#
# Captain's ruling, 2026-09-16: stop producing it. `mesh_spine` writes
# no `music_selection` key into either spine, and the record lives in
# its own file - `library/tools/music_audit_trail.py`, written by step
# 2.04 into its own directory. The whole nested-copy apparatus below
# (drop paths, last-carrier pin) is therefore gone: a `-` path for a
# key nothing produces is a stale declaration, and a last carrier of
# nothing is a gate that cannot fail. What stays is the operational
# route - every step that conducts, analyses, snaps to or places the
# bed is routed the top-level `music_selection` and reads that.
SPINE_KEYS = ("timed_spine", "audio_spine")

# Every step that needs the music to do its job, by its top-level
# input - the route the nested copy never was. If one of these stops
# declaring it, the bed it conducts, snaps to or places loses its
# source, and this is the test that says so.
SPINE_MUSIC_OPERATIONAL_READERS = (
    "step_2_05_mesh_spine",
    "step_2_06_music_analysis",
    "step_4_02_plan_transitions",
    "step_4_04_plan_sfx",
    "step_5_02_audio_mix",
    "step_5_04_compile_manifest",
)


def test_no_spine_music_drop_paths_remain():
    """The nested copy is gone, so its drops are gone with it.

    A `-timed_spine.music_selection` / `-audio_spine.music_selection`
    path today matches nothing (`context_projector` treats an
    unmatched `-` path as a no-op), which is exactly how a stale
    declaration reads as coverage. No manifest may declare one.
    """
    stale = []
    for path in ALL_MANIFESTS:
        for entry in context_fields(path):
            if ".music_selection" in entry and (
                entry.startswith("-timed_spine.")
                or entry.startswith("-audio_spine.")
            ):
                stale.append(f"{path.parent.name}: {entry}")
    assert not stale, (
        "stale nested-music drop paths - `mesh_spine` no longer embeds "
        "`music_selection` in either spine, so these match nothing:\n  - "
        + "\n  - ".join(stale)
    )


def test_the_spine_steps_still_route_the_top_level_selection():
    """The conducting route survives the carrier move.

    Removing the nested copy must not take the top-level
    `music_selection` with it: these steps read the track, the
    section, the splices or the measurements off it, and none of
    them ever read the nested copy. Asserted on the manifest's
    declared inputs - the route itself, prompt or deterministic.
    """
    for step in SPINE_MUSIC_OPERATIONAL_READERS:
        manifest = json.loads(
            (STEPS / step / "manifest.json").read_text(encoding="utf-8"))
        names = [i.get("name") for i in
                 manifest.get("interface", {}).get("inputs", [])]
        assert "music_selection" in names, (
            f"{step} no longer declares the top-level `music_selection` "
            f"input - the carrier move took an operational reader with it."
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
