"""No assessment field reports a default as though it were measured.

The family, found one field at a time:

- ``camera_stability`` read back as the literal ``"unknown"`` while
  ``camera[]`` held the answer (#273, read-side).
- ``usable_ranges`` asserting ``[[0, duration]]`` while the three fields
  beside it said nobody looked (#248, producer side).
- ``speech_coverage: 0.0`` with ``speech_coverage_method:
  "temporal_index"`` whenever the temporal index carried no speech
  regions - and ``speech_present: False`` beside it. On project 001 that
  is 17 of 17 clips, 10 of them talking to camera.
- ``primary_subject_visible: []`` when the model answered without the key.
- ``clip_type: "b_roll"`` derived from a ``content_type`` of ``"unknown"``.

This file is the sweep, kept executable: the deterministic half of the
assessment is computed with nothing to measure, and every field it
produces has to be an admitted absence rather than a value.


Rules relocated from AGENTS.md 10.3
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.3 keeps the headline
and points here.

**No assessment field reports a default as though it were measured. That is the whole rule, and it holds for every field.**
`compute_deterministic_assessment` is where the deterministic half is decided and `tests/test_assessment_reports_no_default_as_measured.py` is the sweep, kept executable: the assessment is computed with nothing to measure and every field it produces must be an admitted absence. The family was found one field at a time, so assume another exists until the sweep says otherwise. [why - the four found in #301, and what a re-run of 001 would and would not fix](docs/RULE_EVIDENCE.md#no-assessment-field-reports-a-default)
- **An empty `speech_regions` list is not a measurement of silence.** `detect_speech_regions` returns `([], transcription)` both when the seam ran and heard nothing and when it raised. **`speech_present` is `True` or `None`, never `False`**, and `speech_coverage_method` says `temporal_index` only once a coverage has been computed.

Every field of `compute_deterministic_assessment` with nothing to measure must be an admitted absence. [why](docs/RULE_EVIDENCE.md#no-assessment-field-reports-a-default)
- **`speech_present` is `True` or `None`, never `False`.**
- **An answer that came back without a key is not an answer of `[]`.** `primary_subject_visible` is `None` when the model omitted it and `[]` only when the model really said the subject is nowhere.
- A rendering of an absent measurement is not a measurement either: `_derived_clip_type` returns `""` for a `content_type` of `"unknown"` rather than classifying the clip `b_roll`.
- **A method field travels with the number it qualifies.** The four manifests routing `assessment.usable_ranges` route `usable_ranges_method` beside it, and 2.02 routes `speech_coverage_method` beside `speech_coverage`; an allow-list that selects the number alone cannot tell a measurement from a default.
"""

import pytest

try:
    from library.tools.analysis.vision_pipeline_v3 import (
        compute_deterministic_assessment,
    )
except ImportError:
    compute_deterministic_assessment = None

from library.tools.semantic_index import clip_observations
from library.tools.vision_schema_adapter import (
    UNMEASURED_SUMMARY,
    adapt_semantic_document,
)

pytestmark = pytest.mark.skipif(
    compute_deterministic_assessment is None,
    reason='could not import "mlx_vlm" - mlx is a macOS-only dependency',
)

# Every value that reads as "nothing measured this". A field of the
# deterministic assessment must hold one of these when nothing did.
ADMITTED_ABSENCES = (None, "unknown", "unmeasured", "", [], {})


def _index_without_speech(duration=188.578):
    """A temporal index that exists and measured no speech.

    `detect_speech_regions` returns `([], transcription)` both when the
    seam ran and heard nothing and when it raised, so `[]` is not a
    measurement of silence and nothing here may read it as one.
    """
    return {"duration_s": duration, "speech_regions": []}


# ── The sweep ───────────────────────────────────────────────────────────

def test_no_deterministic_field_holds_a_value_when_nothing_measured():
    """The whole assessment, computed with nothing to measure."""
    result = compute_deterministic_assessment(
        _index_without_speech(), transcript="", duration=188.578)

    reported = {
        key: value for key, value in result.items()
        if value not in ADMITTED_ABSENCES
    }
    assert not reported, (
        f"these assessment fields claim a value nothing measured: {reported}"
    )


# ── speech_coverage / speech_present ────────────────────────────────────

def test_speech_present_is_never_false():
    """False is a claim of silence, and nothing here can measure one."""
    for transcript in ("", "   ", None):
        for index in (None, {}, _index_without_speech()):
            result = compute_deterministic_assessment(
                index, transcript=transcript, duration=10.0)
            assert result["speech_present"] is not False


def test_a_transcript_alone_measures_presence_and_not_coverage():
    result = compute_deterministic_assessment(
        _index_without_speech(duration=10.0),
        transcript="i can feel the silent judgment", duration=10.0)

    assert result["speech_present"] is True
    assert result["speech_coverage"] is None, (
        "one string of words carries no timings, so how much of the clip "
        "is speech is still unmeasured")
    assert result["speech_coverage_method"] == "unmeasured"


def test_regions_measure_both_and_say_which_measured_them():
    index = {"duration_s": 10.0,
             "speech_regions": [{"start": 1.0, "end": 6.0}]}
    result = compute_deterministic_assessment(index, "", duration=10.0)

    assert result["speech_present"] is True
    assert result["speech_coverage"] == 0.5
    assert result["speech_coverage_method"] == "temporal_index"


# ── primary_subject_visible ─────────────────────────────────────────────

def _assessment_from_model(answer):
    from library.tools.analysis import vision_pipeline_v3 as vp
    content_type, psv = vp._merge_assessment_votes(
        [{"window": [0.0, 10.0], "assessment": answer}])
    return vp._finish_assessment(
        {"speech_present": None, "camera_stability": "unknown"},
        content_type, psv, None, 10.0, [])


def test_an_answer_of_empty_is_kept_because_the_model_made_it():
    assessment = _assessment_from_model(
        {"content_type": "scenery", "primary_subject_visible": []})
    assert assessment["primary_subject_visible"] == []


# ── usable_ranges: the display reads the method, not the ranges ─────────

STALE_001_ASSESSMENT = {
    # Exactly the shape every one of 001's 17 documents carries.
    "usable_ranges": [[0, 3.567]],
    "unusable_ranges": [],
    "usable_ranges_method": "unmeasured",
    "usable_ranges_signals": [],
    "content_type": "scenery",
}


def test_the_broll_candidate_cell_says_unmeasured():
    """The cell the B-roll selector cut project 001's first interjection on."""
    doc = {
        "clip_id": "clip_001",
        "vision_schema_version": "3.0",
        "scene": [{"type": "exterior", "start": 0, "end": 3.5,
                   "description": "a street corner"}],
        "camera": [{"framing": "wide", "stability": "shaky",
                    "movement": "panning_right"}],
        "actions": [],
        "objects": [],
        "assessment": dict(STALE_001_ASSESSMENT),
    }
    assert clip_observations(doc)["usable_ranges"] == UNMEASURED_SUMMARY
    # A stale usable_portions string is replaced, not deferred to.
    doc = {
        "clip_id": "clip_001",
        "vision_schema_version": "3.0",
        "scene": [], "camera": [], "actions": [], "objects": [],
        "assessment": dict(STALE_001_ASSESSMENT, usable_portions="0.0-3.5s"),
    }
    adapted = adapt_semantic_document(doc)
    assert adapted["assessment"]["usable_portions"] == UNMEASURED_SUMMARY


# ── clip_type ───────────────────────────────────────────────────────────

def test_an_unknown_content_type_derives_no_clip_type():
    doc = {
        "clip_id": "clip_001",
        "vision_schema_version": "3.0",
        "scene": [], "camera": [], "actions": [], "objects": [],
        "assessment": {"content_type": "unknown"},
    }
    adapted = adapt_semantic_document(doc)
    assert "clip_type" not in adapted["assessment"], (
        "b_roll would classify a clip nothing classified")


