"""The v3 vision schema must reach the steps that read the retired one.

`vision_pipeline_v3.py` measures framing, camera stability, usable ranges
and subject visibility per time range, and every consumer addressed a
schema that has none of those keys.  Nothing joined the two, so a re-run
of the analysis fed step 3.02 documents it could not describe and the
step failed outright.  These tests pin the join.
"""

import json
import os

import pytest

from library.tools.semantic_index import (
    build_semantic_lookup,
    clip_observations,
    clip_tags,
    describe_clip,
)
from library.tools.vision_schema_adapter import (
    adapt_semantic_document,
    adapt_semantic_documents,
    is_v3_profile,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Shaped exactly like a profile the v3 analyser writes - see
# pipeline_output/clip_profile_IMG_1814_v3.json in a real project.
V3_PROFILE = {
    "clip_id": "IMG_1814",
    "file_path": "/footage/IMG_1814.MOV",
    "duration_s": 45.943,
    "fps": 30.0,
    "resolution": [1920, 1080],
    "transcript": "today is... what even is today?",
    "scene": [
        {
            "start": 0.0,
            "end": 46.0,
            "location": "Outdoor parking lot and construction site",
            "type": "outdoor",
            "lighting": "Daylight",
            "notable_features": ["Construction site with steel frame"],
        }
    ],
    "camera": [
        {"start": 0, "end": 12, "mode": "handheld", "framing": "wide",
         "stability": "stable", "movement": "stationary"},
        {"start": 12, "end": 46, "mode": "handheld", "framing": "close-up",
         "stability": "unstable", "movement": "panning_right"},
    ],
    "actions": [
        {
            "window": [0, 10],
            "actions": [
                {"start": 0, "end": 10,
                 "action": "The person turns away and walks forward.",
                 "speech_cue": None,
                 "body_language": "Neutral expression, then turns right."},
            ],
        }
    ],
    "objects": [
        {"label": "young man in a black baseball cap", "role": "primary_subject",
         "category": "person", "appearances": [[0.0, 45.8]], "readable_text": None},
        {"label": "steel frame", "role": "background", "category": "structure",
         "appearances": [[0.0, 20.0]], "readable_text": None},
    ],
    "assessment": {
        "speech_present": True,
        "speech_coverage": 0.32,
        "camera_stability": "unstable",
        "usable_ranges": [[0, 45.943]],
        "unusable_ranges": [],
        "usable_ranges_method": "deterministic_v1",
        "usable_ranges_signals": ["motion_energy"],
        "content_type": "person_talking_to_camera",
        "primary_subject_visible": [[0, 46]],
    },
    "analysis_metadata": {"pipeline_version": "v3", "frames_extracted": 11},
}

# The retired shape, as the 2026-07-12 profiles on disk still carry it.
LEGACY_PROFILE = {
    "clip_id": "IMG_1814",
    "file_path": "/footage/IMG_1814.MOV",
    "duration_s": 45.943,
    "blocks": [{"label": "Intro", "timestamp_range": "0:00-0:09",
                "visual": "A man walks into a construction site."}],
    "analysis": {"scene": "The image depicts an outdoor urban plaza.",
                 "motion": "The camera is static.",
                 "mood": "pensive"},
    "assessment": {"clip_type": "a_roll", "interest_score": 6,
                   "keywords": ["vlog", "talking_head"]},
}


def test_v3_profile_is_detected_and_legacy_is_not():
    assert is_v3_profile(V3_PROFILE)
    assert not is_v3_profile(LEGACY_PROFILE)


def test_legacy_documents_pass_through_untouched():
    """A project whose stored state predates v3 must keep working."""
    assert adapt_semantic_document(LEGACY_PROFILE) == LEGACY_PROFILE


def test_v3_fields_survive_adaptation():
    """The adapter adds a view; it must not replace the observations."""
    adapted = adapt_semantic_document(V3_PROFILE)
    assert adapted["camera"] == V3_PROFILE["camera"]
    assert adapted["scene"] == V3_PROFILE["scene"]
    assert adapted["objects"] == V3_PROFILE["objects"]
    assert adapted["assessment"]["usable_ranges"] == [[0, 45.943]]
    assert adapted["assessment"]["primary_subject_visible"] == [[0, 46]]


def test_v3_scene_segments_become_a_time_bounded_description():
    adapted = adapt_semantic_document(V3_PROFILE)
    scene = adapted["analysis"]["scene"]
    assert "0.0-46.0s" in scene
    assert "Outdoor parking lot and construction site" in scene
    assert "Construction site with steel frame" in scene


def test_v3_camera_segments_carry_framing_and_stability_per_range():
    """The audit's headline unreachable fields, rendered where steps read."""
    adapted = adapt_semantic_document(V3_PROFILE)
    motion = adapted["analysis"]["motion"]
    assert "wide framing" in motion
    assert "close-up framing" in motion
    assert "[0.0-12.0s]" in motion
    assert "[12.0-46.0s]" in motion
    assert "stable" in motion and "unstable" in motion


def test_v3_actions_become_time_resolved_blocks():
    adapted = adapt_semantic_document(V3_PROFILE)
    blocks = adapted["blocks"]
    assert len(blocks) == 1
    assert blocks[0]["timestamp_range"] == "0:00-0:10"
    assert "walks forward" in blocks[0]["visual"]
    assert blocks[0]["label"] == "Outdoor parking lot and construction site"


def test_adapter_does_not_invent_fields_v3_never_measured():
    """An absent field warns downstream; a fabricated one misleads."""
    assessment = adapt_semantic_document(V3_PROFILE)["assessment"]
    assert "interest_score" not in assessment
    assert "moment_type" not in assessment
    assert "mood" not in adapt_semantic_document(V3_PROFILE)["analysis"]


def test_derived_assessment_fields_are_renderings_of_real_observations():
    assessment = adapt_semantic_document(V3_PROFILE)["assessment"]
    # content_type says the subject is talking to camera.
    assert assessment["clip_type"] == "a_roll"
    assert assessment["usable_portions"] == "0.0-45.9s"
    assert "person" in assessment["keywords"]
    assert "outdoor" in assessment["keywords"]
    assert "close-up" in assessment["keywords"]


def test_a_fully_excluded_clip_says_so_rather_than_rendering_blank():
    """`usable_ranges: []` is a verdict, and a blank cell does not carry it.

    An empty string is what an unmeasured clip renders as, so a consumer
    reading one cannot tell "no bound was measured" from "no footage here
    is usable" - the strongest signal the measurement can give.
    """
    excluded = dict(V3_PROFILE)
    excluded["assessment"] = dict(
        V3_PROFILE["assessment"],
        usable_ranges=[],
        unusable_ranges=[
            {"start": 0.0, "end": 20.0, "reason": "sustained_high_motion"},
            {"start": 20.0, "end": 45.9, "reason": "subject_absent"},
        ],
    )

    portions = adapt_semantic_document(excluded)["assessment"]["usable_portions"]
    assert portions == (
        "none - whole clip excluded (sustained_high_motion, subject_absent)")
    assert clip_observations(excluded)["usable_ranges"] == portions


def test_an_unmeasured_clip_renders_no_usable_range_claim():
    unmeasured = dict(V3_PROFILE)
    unmeasured["assessment"] = dict(
        V3_PROFILE["assessment"],
        usable_ranges=[],
        unusable_ranges=[],
        usable_ranges_method="unmeasured",
        usable_ranges_signals=[],
    )

    assessment = adapt_semantic_document(unmeasured)["assessment"]
    assert "usable_portions" not in assessment
    assert clip_observations(unmeasured)["usable_ranges"] == ""


def test_scenery_content_type_is_not_labelled_a_roll():
    scenery = dict(V3_PROFILE)
    scenery["assessment"] = dict(V3_PROFILE["assessment"],
                                 content_type="scenery")
    assert adapt_semantic_document(scenery)["assessment"]["clip_type"] == "b_roll"


def test_describe_clip_handles_a_raw_v3_profile():
    """This is the failure that broke step 3.02: "" for every clip."""
    described = describe_clip(V3_PROFILE)
    assert described
    assert "Outdoor parking lot" in described


def test_clip_observations_expose_framing_stability_and_bounds():
    observed = clip_observations(V3_PROFILE)
    assert observed["framing"] == "wide -> close-up"
    assert observed["stability"] == "unstable"
    assert observed["movement"] == "stationary -> panning_right"
    assert observed["content_type"] == "person_talking_to_camera"
    assert observed["usable_ranges"] == "0.0-45.9s"
    assert observed["subjects"].startswith("young man in a black baseball cap")
    assert "0.0-46.0s" in observed["description"]


def test_clip_observations_degrade_without_inventing_on_legacy_docs():
    observed = clip_observations(LEGACY_PROFILE)
    assert observed["description"] == "The image depicts an outdoor urban plaza."
    assert observed["activity"] == "The camera is static."
    # v3 never ran on this clip, so these were never measured.
    assert observed["framing"] == ""
    assert observed["stability"] == ""


def test_clip_tags_reach_v3_derived_keywords():
    assert "outdoor" in clip_tags(V3_PROFILE)
    assert "vlog" in clip_tags(LEGACY_PROFILE)


def test_lookup_join_survives_adaptation():
    """The catalog join is by file path, and the adapter must not break it."""
    docs = adapt_semantic_documents([V3_PROFILE])
    lookup = build_semantic_lookup(
        docs, [{"clip_id": "clip_009", "path": "/footage/IMG_1814.MOV"}])
    assert "clip_009" in lookup
    assert describe_clip(lookup["clip_009"])


def test_adapt_documents_leaves_non_dicts_alone():
    assert adapt_semantic_documents(["junk", 3]) == ["junk", 3]
    assert adapt_semantic_documents("not a list") == "not a list"


def test_adapted_document_is_json_serialisable():
    """It is written straight into pipeline_data.json."""
    json.dumps(adapt_semantic_document(V3_PROFILE))


# ─── The measured values must survive all the way into a prompt ───

CONSUMERS = [
    "step_2_01_creative_direction",
    "step_2_02_speech_sequence",
    "step_3_02_select_broll",
    "step_4_03_plan_vfx",
    "step_4_04_plan_sfx",
]


def _semantic_context(step_id, docs):
    """Project + serialise docs exactly as the orchestrator would."""
    from library.tools.context_projector import project_fields
    from library.tools.toon_serializer import json_to_toon

    manifest_path = os.path.join(
        REPO_ROOT, "library", "steps", step_id, "manifest.json")
    with open(manifest_path) as f:
        fields = json.load(f)["context_fields"]
    fields = [f for f in fields if f.startswith("semantic_analysis_documents")]
    return json_to_toon(
        project_fields({"semantic_analysis_documents": docs}, fields))


def test_framing_and_usable_ranges_reach_the_broll_prompt():
    """A real measured value, end to end: analyser -> allow-list -> prompt.

    The allow-list used to admit seven paths, none of which exists in v3.
    Every document projected to clip_id and duration and the model was
    told to reason about framing it could not see.
    """
    context = _semantic_context(
        "step_3_02_select_broll", adapt_semantic_documents([V3_PROFILE]))
    assert "close-up" in context
    assert "unstable" in context
    assert "usable_ranges" in context
    assert "person_talking_to_camera" in context


@pytest.mark.parametrize("step_id", CONSUMERS)
def test_every_semantic_consumer_projects_all_three_document_shapes(step_id):
    """Adapted v3, raw v3 and retired-schema documents must all survive.

    `project_fields` raises when a non-empty list projects to nothing but
    empty dicts, which is exactly what a stale allow-list produces.
    """
    for docs in (adapt_semantic_documents([V3_PROFILE]),
                 [V3_PROFILE],
                 [LEGACY_PROFILE]):
        assert _semantic_context(step_id, docs).strip()

