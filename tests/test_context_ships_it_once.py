"""Nothing reaches a prompt twice, and no raw array reaches one at all.

These are properties of the step manifests' `context_fields`, asserted on
the manifests (AGENTS.md 10.1). History: docs/evidence/context_projection.md.
"""

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

STEPS = REPO / "library" / "steps"

ALL_MANIFESTS = sorted(STEPS.glob("*/manifest.json"))


def context_fields(path: Path) -> list:
    return json.loads(path.read_text(encoding="utf-8")).get("context_fields") or []


# `vision_schema_adapter.scene_prose` renders `scene[]`; `camera_prose`
# renders `camera[]`.  Either is a legitimate thing to send.  Both is not.
DERIVED_FROM = {
    "semantic_analysis_documents.*.analysis.scene":
        "semantic_analysis_documents.*.scene",
    "semantic_analysis_documents.*.analysis.motion":
        "semantic_analysis_documents.*.camera",
}


def test_no_step_gets_a_summary_and_its_own_source():
    assert ALL_MANIFESTS
    offenders = [
        f"{path.parent.name}: {prose!r} + {raw!r}"
        for path in ALL_MANIFESTS
        for prose, raw in DERIVED_FROM.items()
        if {prose, raw} <= set(context_fields(path))
    ]
    assert not offenders, (
        "a step declares a vision prose field AND the structure it was "
        f"rendered from - send one of them: {offenders}"
    )


RAW_MUSIC_ARRAYS = (
    "music_analysis.tempo.beats",
    "music_analysis.tempo.downbeats",
    "music_analysis.energy_dynamics.energy_curve_1hz",
)


def test_the_raw_beat_grid_never_reaches_a_prompt():
    """A step routed the whole analysis must drop the three value lists
    with `-` paths (the grid is read in code through `beat_grid.py`)."""
    offenders = [
        f"{path.parent.name}: {array}"
        for path in ALL_MANIFESTS
        if "music_analysis" in context_fields(path)
        for array in RAW_MUSIC_ARRAYS
        if f"-{array}" not in context_fields(path)
    ]
    assert not offenders, (
        f"whole `music_analysis` routed without dropping raw arrays: {offenders}"
    )


def test_the_transcript_reaches_speech_sequence_exactly_once():
    """2.02's pre-bridge builds `transcripts_toon`, so it must not also
    declare the view; 2.01 has no pre-bridge, so the view is its route."""
    step = STEPS / "step_2_02_speech_sequence"
    declared = context_fields(step / "manifest.json")
    assert "view:transcript" not in declared
    assert '"transcripts_toon"' in (step / "bridge.py").read_text(encoding="utf-8")
    assert "view:transcript" in context_fields(
        STEPS / "step_2_01_creative_direction" / "manifest.json")


SPEECH_TIMING_PATHS = (
    "speech_sequence.body_sequence.*.word_timestamps",
    "speech_sequence.hook_segment.word_timestamps",
)


def test_the_rough_cut_review_gets_no_word_timings():
    declared = context_fields(
        STEPS / "step_3_03_review_rough_cut" / "manifest.json")
    for timing in SPEECH_TIMING_PATHS:
        assert f"-{timing}" in declared, (
            f"3.03 routes the whole `speech_sequence` and does not drop "
            f"{timing!r}. Word timings do not reach a prompt (AGENTS.md 10.1)."
        )


def test_validate_is_not_sent_the_qa_report_and_its_own_summary():
    declared = context_fields(
        STEPS / "step_6_02_validate_output" / "manifest.json")
    assert "-deterministic_validation.qa_report" in declared, (
        "6.02 is handed its own QA report's raw rows beside the summary "
        "rendered from them. Send one of them."
    )
