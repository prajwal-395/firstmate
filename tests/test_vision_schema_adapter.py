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
    UNMEASURED_SUMMARY,
    adapt_semantic_document,
    adapt_semantic_documents,
    is_v3_profile,
    stability_summary,
    usable_ranges_summary,
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




def test_legacy_documents_pass_through_untouched():
    """A project whose stored state predates v3 must keep working."""
    assert adapt_semantic_document(LEGACY_PROFILE) == LEGACY_PROFILE










def test_adapter_does_not_invent_fields_v3_never_measured():
    """An absent field warns downstream; a fabricated one misleads."""
    assessment = adapt_semantic_document(V3_PROFILE)["assessment"]
    assert "interest_score" not in assessment
    assert "moment_type" not in assessment
    assert "mood" not in adapt_semantic_document(V3_PROFILE)["analysis"]




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


def test_an_unmeasured_clip_says_it_was_never_measured():
    """An absent measurement is said in words, not left blank.

    Blank is what "no bound" looks like, and the B-roll selector reads
    this cell to decide which seconds of a clip it may cut.  It has to be
    able to tell "you may cut anywhere" from "nobody looked".
    """
    unmeasured = dict(V3_PROFILE)
    unmeasured["assessment"] = dict(
        V3_PROFILE["assessment"],
        usable_ranges=[],
        unusable_ranges=[],
        usable_ranges_method="unmeasured",
        usable_ranges_signals=[],
    )

    assessment = adapt_semantic_document(unmeasured)["assessment"]
    assert assessment["usable_portions"] == UNMEASURED_SUMMARY
    assert clip_observations(unmeasured)["usable_ranges"] == UNMEASURED_SUMMARY
    assert "0.0-45.9s" not in clip_observations(unmeasured)["usable_ranges"]


def test_a_legacy_document_without_the_method_field_still_renders_blank():
    """A document that predates the field is not an unmeasured verdict.

    `semantic_index` falls back to the document's own `usable_portions`
    prose when the summary is empty, and that fallback has to stay
    reachable.
    """
    legacy = dict(V3_PROFILE)
    legacy["assessment"] = {
        k: v for k, v in V3_PROFILE["assessment"].items()
        if not k.startswith("usable_ranges")
    }
    legacy["assessment"]["usable_portions"] = "0.0-12.0s"
    assert usable_ranges_summary(legacy["assessment"]) == ""
    assert clip_observations(legacy)["usable_ranges"] == "0.0-12.0s"




def test_describe_clip_handles_a_raw_v3_profile():
    """This is the failure that broke step 3.02: "" for every clip."""
    described = describe_clip(V3_PROFILE)
    assert described
    assert "Outdoor parking lot" in described








def test_lookup_join_survives_adaptation():
    """The catalog join is by file path, and the adapter must not break it."""
    docs = adapt_semantic_documents([V3_PROFILE])
    lookup = build_semantic_lookup(
        docs, [{"clip_id": "clip_009", "path": "/footage/IMG_1814.MOV"}])
    assert "clip_009" in lookup
    assert describe_clip(lookup["clip_009"])




# ─── The measured values must survive all the way into a prompt ───

CONSUMERS = [
    "step_2_01_creative_direction",
    "step_2_02_speech_sequence",
    "step_3_02_select_broll",
    "step_4_03_plan_vfx",
    "step_4_04_plan_sfx",
]


# A step whose measured values reach the prompt through its own
# PRE-BRIDGE rather than through a `semantic_analysis_documents` path.
# The document-path route and the pre-bridge route are both real routes,
# and this file tests whichever one a step actually uses - what it must
# never do is stop checking, which is what an allow-list slice of a step
# with no such paths quietly becomes (it projects to `{}`, and `"{}"` is
# a truthy string).
PRE_BRIDGE_ROUTE = {"step_3_02_select_broll"}


def _semantic_context(step_id, docs):
    """Project + serialise docs exactly as the orchestrator would.

    The allow-list slice, for a step that reads the documents by path.
    A step in `PRE_BRIDGE_ROUTE` has none, so asking this for one is a
    programming error rather than an empty answer.
    """
    from library.tools.context_projector import project_fields
    from library.tools.toon_serializer import json_to_toon

    fields = [f for f in _context_fields(step_id)
              if f.startswith("semantic_analysis_documents")]
    assert fields, (
        f"{step_id} declares no semantic_analysis_documents path, so this "
        f"helper measures nothing for it - use _assembled_context")
    return json_to_toon(
        project_fields({"semantic_analysis_documents": docs}, fields))


def _context_fields(step_id):
    manifest_path = os.path.join(
        REPO_ROOT, "library", "steps", step_id, "manifest.json")
    with open(manifest_path, encoding="utf-8") as f:
        return json.load(f)["context_fields"]


def _assembled_context(step_id, docs, project_folder):
    """The step's WHOLE prompt context, the way the runner builds it.

    The real `bridge.py` as a subprocess, the runner's own
    `project_step_context` and the real serializer - so a value is
    measured where the model reads it, whichever route carried it there.
    """
    import subprocess
    import sys

    from library.processes.edit_video.run_pipeline import project_step_context
    from library.tools.toon_serializer import json_to_toon

    step_dir = os.path.join(REPO_ROOT, "library", "steps", step_id)
    inputs = {
        "project_folder": str(project_folder),
        "semantic_analysis_documents": docs,
        "clip_catalog": [{"clip_id": "clip_009", "filename": "IMG_1814.MOV",
                          "path": "/footage/IMG_1814.MOV",
                          "duration_seconds": 45.943, "width": 1920,
                          "height": 1080, "rotation": 0,
                          "frame_rate": 30.0}],
        "a_roll_assignments": [
            {"segment_id": "block_1", "spine_block_position": 1,
             "video_segments": [{"clip_id": "clip_009"}]}],
        "temporal_event_indices": [{"clip_id": "clip_009"}],
        "timed_spine": {"structure": []},
        "creative_direction": {},
    }
    env = dict(os.environ)
    env["PYTHONPATH"] = REPO_ROOT + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, os.path.join(step_dir, "bridge.py")],
        input=json.dumps(inputs), capture_output=True, text=True,
        encoding="utf-8", cwd=REPO_ROOT, env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    pre = json.loads(proc.stdout)

    merged = dict(inputs)
    merged.update(pre)
    with open(os.path.join(step_dir, "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    return json_to_toon(project_step_context(merged, manifest, set(pre)))


def test_framing_and_usable_ranges_reach_the_broll_prompt(tmp_path):
    """A real measured value, end to end: analyser -> route -> prompt.

    The allow-list used to admit seven paths, none of which exists in v3.
    Every document projected to clip_id and duration and the model was
    told to reason about framing it could not see.

    The route changed and the property did not.  3.02 no longer declares
    a `semantic_analysis_documents` path at all - it carried THREE views
    of one analysis at 60.7% of its context, so the raw structure moved
    to a reference - and this now measures the WHOLE assembled prompt
    instead of one allow-list slice of it.  That is strictly harder to
    pass: the original defect still fails it (a stale allow-list leaves
    the values nowhere), and so does a collapse that drops a value from
    every route.
    """
    context = _assembled_context(
        "step_3_02_select_broll", adapt_semantic_documents([V3_PROFILE]),
        tmp_path)

    # The framing the vision pass measured for the SECOND camera segment.
    # `wide` alone would pass off the first segment and prove nothing.
    assert "close-up" in context
    assert "unstable" in context
    assert "person_talking_to_camera" in context

    # The usable range as a BOUND a cutaway is cut against. The old
    # assertion was the key name `usable_ranges`; the thing that has to
    # reach the model is the measurement, and a key name is not one.
    assert "0.0-45.9s" in context


# Whether a clip was measured is itself a measurement, and the prompt has
# to carry all three answers distinguishably: the B-roll handoff defines
# an EMPTY cell as "never measured, so the whole clip is fair game but
# unvetted", so a measured exclusion rendered blank tells the model the
# opposite of the truth.
USABLE_RANGE_STATES = [
    ({"usable_ranges": [[0, 45.943]],
      "usable_ranges_method": "deterministic_v1"}, "0.0-45.9s"),
    ({"usable_ranges": [], "usable_ranges_method": "deterministic_v1"},
     "none - whole clip excluded"),
    ({"usable_ranges": [], "usable_ranges_method": "unmeasured"},
     UNMEASURED_SUMMARY),
]


@pytest.mark.parametrize("assessment,expected", USABLE_RANGE_STATES)
def test_the_three_usable_range_states_reach_the_broll_prompt(
        assessment, expected, tmp_path):
    doc = json.loads(json.dumps(V3_PROFILE))
    doc["assessment"] = dict(doc["assessment"], **assessment)
    context = _assembled_context(
        "step_3_02_select_broll", adapt_semantic_documents([doc]), tmp_path)
    assert expected in context


@pytest.mark.parametrize("step_id", CONSUMERS)
def test_every_semantic_consumer_projects_all_three_document_shapes(
        step_id, tmp_path):
    """Adapted v3, raw v3 and retired-schema documents must all survive.

    `project_fields` raises when a non-empty list projects to nothing but
    empty dicts, which is exactly what a stale allow-list produces.  A
    step on the pre-bridge route is checked on its assembled prompt, and
    on the value rather than on the string being non-empty - `"{}"` is
    non-empty, which is how this could have gone quiet.
    """
    for docs in (adapt_semantic_documents([V3_PROFILE]),
                 [V3_PROFILE],
                 [LEGACY_PROFILE]):
        if step_id in PRE_BRIDGE_ROUTE:
            context = _assembled_context(step_id, docs, tmp_path)
            assert "clip_009" in context, (
                f"{step_id} assembled a prompt that names no clip from "
                f"these documents")
            continue
        context = _semantic_context(step_id, docs)
        assert context.strip() and context.strip() != "{}", context


def test_clip_observations_excludes_legacy_chain_of_thought_objects():
    """Do not surface raw model reasoning from legacy profiles.
    
    In older profiles, analysis.objects often contains verbose model
    chain-of-thought text (e.g., 'Based on the images provided...').
    This must not leak into the subjects column.
    """
    profile_with_cot = dict(LEGACY_PROFILE)
    profile_with_cot["analysis"] = dict(LEGACY_PROFILE.get("analysis", {}))
    profile_with_cot["analysis"]["objects"] = "Based on the images provided, here are the distinct elements:\n* People: A young man..."
    
    observed = clip_observations(profile_with_cot)
    assert observed["subjects"] == ""


def test_stability_summary_unknown_falls_through_to_camera_segments():
    """The literal "unknown" in assessment.camera_stability is treated as
    absent, so the summary reads the per-segment values the vision pass
    actually measured.  This is reading a measurement, not filling in the
    assessment field (AGENTS.md 10.3).
    """
    doc = {
        "scene": [{"start": 0, "end": 20, "type": "outdoor"}],
        "camera": [
            {"start": 0, "end": 20, "mode": "handheld", "framing": "wide",
             "stability": "stable", "movement": "stationary"},
        ],
        "assessment": {"camera_stability": "unknown"},
        "analysis_metadata": {"pipeline_version": "v3"},
    }
    assert stability_summary(doc) == "stable"


def test_stability_summary_real_assessment_still_wins():
    """When the assessment carries a real verdict (not "unknown"), it is
    still preferred over the per-segment values."""
    assert stability_summary(V3_PROFILE) == "unstable"



