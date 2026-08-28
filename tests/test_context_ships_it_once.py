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
