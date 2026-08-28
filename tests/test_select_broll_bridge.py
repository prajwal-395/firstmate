"""The B-roll candidate table must describe the footage.

It used to be 12 alphabetically-ordered rows per slot, each carrying 180
characters of a single-frame caption, repeated for every slot.  The model
had nothing to choose on and picked straight down the list.  It also
returned zero rows - and failed the step - for any project analysed by
the current vision pipeline, because no v3 document yielded a
description.
"""

import json
import os
import subprocess
import sys

import pytest

from library.steps.step_3_02_select_broll.post_bridge import (
    find_best_segment,
    resolve_broll,
)
from tests.test_vision_schema_adapter import LEGACY_PROFILE, V3_PROFILE

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRIDGE = os.path.join(
    REPO_ROOT, "library", "steps", "step_3_02_select_broll", "bridge.py")

CATALOG = [
    {"clip_id": "clip_009", "path": "/footage/IMG_1814.MOV",
     "duration_seconds": 45.943},
    {"clip_id": "clip_001", "path": "/footage/IMG_1806.MOV",
     "duration_seconds": 3.567},
]

A_ROLL = [
    {"segment_id": "block_1", "spine_block_position": 1,
     "video_segments": [{"clip_id": "clip_009"}]},
    {"segment_id": "block_2", "spine_block_position": 2,
     "video_segments": [{"clip_id": "clip_009"}]},
]


def _second_clip_doc():
    """A second v3 document, so the catalog has two described clips."""
    doc = json.loads(json.dumps(V3_PROFILE))
    doc["clip_id"] = "IMG_1806"
    doc["file_path"] = "/footage/IMG_1806.MOV"
    doc["camera"] = [{"start": 0, "end": 3.567, "mode": "handheld",
                      "framing": "wide", "stability": "stable",
                      "movement": "stationary"}]
    doc["assessment"] = dict(V3_PROFILE["assessment"],
                             content_type="scenery",
                             camera_stability="stable",
                             usable_ranges=[[0, 3.567]])
    return doc


def run_bridge(payload: dict):
    """Invoke the bridge the way the orchestrator does: JSON on stdin."""
    env = dict(os.environ, PYTHONPATH=REPO_ROOT)
    proc = subprocess.run(
        [sys.executable, BRIDGE],
        input=json.dumps(payload),
        capture_output=True, text=True, encoding="utf-8", env=env,
    )
    return proc


def parse_table(toon: str):
    """Split a TOON table into (header_fields, [row dicts])."""
    lines = [line for line in toon.splitlines() if line.strip()]
    header = lines[0]
    fields = header[header.index("{") + 1:header.index("}")].split(",")
    rows = [dict(zip(fields, line.split("\t"))) for line in lines[1:]]
    return fields, rows


def test_v3_documents_produce_a_candidate_table():
    """This exact input used to exit 1 with "no usable description"."""
    proc = run_bridge({
        "clip_catalog": CATALOG,
        "a_roll_assignments": A_ROLL,
        "semantic_analysis_documents": [V3_PROFILE, _second_clip_doc()],
    })
    assert proc.returncode == 0, proc.stdout + proc.stderr
    _, rows = parse_table(json.loads(proc.stdout)["broll_candidates_toon"])
    assert {r["clip_id"] for r in rows} == {"clip_001", "clip_009"}


def test_candidate_rows_carry_measured_framing_and_bounds():
    proc = run_bridge({
        "clip_catalog": CATALOG,
        "a_roll_assignments": A_ROLL,
        "semantic_analysis_documents": [V3_PROFILE, _second_clip_doc()],
    })
    fields, rows = parse_table(
        json.loads(proc.stdout)["broll_candidates_toon"])
    for expected in ("framing", "stability", "content_type", "usable_range",
                     "subjects", "description"):
        assert expected in fields

    by_id = {r["clip_id"]: r for r in rows}
    assert by_id["clip_001"]["framing"] == "wide"
    assert by_id["clip_001"]["content_type"] == "scenery"
    # A 3.567s clip, rounded inwards: the range is a bound the model cuts
    # against, so it must never read longer than the footage really is.
    assert by_id["clip_001"]["usable_range"] == "0.0-3.5s"
    assert by_id["clip_009"]["framing"] == "wide -> close-up"
    assert by_id["clip_009"]["content_type"] == "person_talking_to_camera"
    assert "Outdoor parking lot" in by_id["clip_009"]["description"]


def test_one_row_per_clip_not_one_row_per_slot():
    """Two slots used to mean two identical copies of the same list."""
    proc = run_bridge({
        "clip_catalog": CATALOG,
        "a_roll_assignments": A_ROLL,
        "semantic_analysis_documents": [V3_PROFILE, _second_clip_doc()],
    })
    _, rows = parse_table(json.loads(proc.stdout)["broll_candidates_toon"])
    assert len(rows) == len(CATALOG)
    assert len(rows) == len({r["clip_id"] for r in rows})


def test_clips_carrying_aroll_are_flagged_and_listed_last():
    proc = run_bridge({
        "clip_catalog": CATALOG,
        "a_roll_assignments": A_ROLL,
        "semantic_analysis_documents": [V3_PROFILE, _second_clip_doc()],
    })
    _, rows = parse_table(json.loads(proc.stdout)["broll_candidates_toon"])
    assert [r["used_as_aroll"] for r in rows] == ["no", "yes"]
    assert rows[-1]["clip_id"] == "clip_009"


def test_legacy_documents_still_produce_a_table():
    """The reference project's stored state is in the retired schema."""
    proc = run_bridge({
        "clip_catalog": [CATALOG[0]],
        "a_roll_assignments": A_ROLL,
        "semantic_analysis_documents": [LEGACY_PROFILE],
    })
    assert proc.returncode == 0, proc.stdout + proc.stderr
    _, rows = parse_table(json.loads(proc.stdout)["broll_candidates_toon"])
    assert rows[0]["clip_id"] == "clip_009"
    assert "outdoor urban plaza" in rows[0]["description"]


def test_no_describable_clip_still_fails_the_step():
    """An empty table means the model can only produce filler."""
    proc = run_bridge({
        "clip_catalog": CATALOG,
        "a_roll_assignments": A_ROLL,
        "semantic_analysis_documents": [],
    })
    assert proc.returncode == 1
    assert "usable semantic description" in json.loads(proc.stdout)["error"]


def test_undescribed_clips_are_reported_not_silently_dropped():
    proc = run_bridge({
        "clip_catalog": CATALOG,
        "a_roll_assignments": A_ROLL,
        "semantic_analysis_documents": [V3_PROFILE],
    })
    assert proc.returncode == 0
    assert "clip_001" in proc.stderr


# ── The post-bridge must seek to the moment it matched, not to a guess ──

# Blocks as the v3 adapter derives them: each action window carries the
# time it was actually observed at.
V3_BLOCKS = [
    {"label": "kitchen", "visual": "pouring coffee", "start": 30.0, "end": 34.0},
    {"label": "kitchen", "visual": "slicing bread", "start": 8.0, "end": 12.0},
]

# Retired-schema blocks: an ordered list with no time bounds at all.
LEGACY_BLOCKS = [
    {"label": "kitchen", "visual": "pouring coffee"},
    {"label": "kitchen", "visual": "slicing bread"},
]


def test_block_match_seeks_to_the_time_the_action_was_observed():
    """The matched block's own start, not its position in the list."""
    video_in, video_out = find_best_segment(
        "slicing bread", {"blocks": V3_BLOCKS}, {}, 40.0, 3.0)
    assert (video_in, video_out) == (8.0, 11.0)


def test_measured_start_wins_even_when_the_index_fraction_agrees_less():
    """Block 0 was observed at 30s, not at the head of the clip."""
    video_in, _ = find_best_segment(
        "pouring coffee", {"blocks": V3_BLOCKS}, {}, 40.0, 3.0)
    assert video_in == 30.0


def test_blocks_without_time_bounds_still_use_the_index_fraction():
    """A retired-schema document supports nothing better."""
    video_in, video_out = find_best_segment(
        "slicing bread", {"blocks": LEGACY_BLOCKS}, {}, 40.0, 3.0)
    assert (video_in, video_out) == (20.0, 23.0)


def test_scene_segment_is_scored_against_the_block_covering_it():
    """Strategy 1 knows each segment's real bounds - it must use them.

    The clip cuts at 20s.  The moment described sits at 8s, inside the
    first segment; mapping segment index onto block index by proportion
    would score it against the second block and pick the wrong scene.
    """
    temporal = {"scene_boundaries": [{"time": 0.0}, {"time": 20.0}]}
    video_in, _ = find_best_segment(
        "slicing bread", {"blocks": V3_BLOCKS}, temporal, 40.0, 3.0)
    assert video_in < 20.0


# ── resolve_broll: the post-bridge's own half ─────────────────────────
#
# `tests/test_bridges.py` claimed to cover this by importing a
# `post_bridge` function that has never existed, so the assertions below
# are the first this half has had.  They are the invariants the manifest
# validator and `_assert_timeline_fully_covered` go on to enforce, caught
# where they are decided instead of where they blow up.

SPINE = {"structure": [
    {"position": 1, "block_type": "speech", "clip_id": "clip_009",
     "timeline_start": 0.0, "timeline_end": 4.0},
    {"position": 2, "block_type": "speech", "clip_id": "clip_009",
     "timeline_start": 4.0, "timeline_end": 8.0},
]}

RESOLVE_CATALOG = [
    dict(CATALOG[0], width=1920, height=1080, rotation=0),
    dict(CATALOG[1], width=1080, height=1920, rotation=0),
]

RESOLVE_DOCS = [V3_PROFILE, _second_clip_doc()]


def _resolve(creative, interjections=(), spine=None, catalog=None):
    return resolve_broll(
        list(creative), list(interjections),
        catalog if catalog is not None else RESOLVE_CATALOG,
        RESOLVE_DOCS, [], spine if spine is not None else SPINE,
    )


def test_a_cutaway_is_resolved_to_a_source_range_and_a_timeline_range():
    out = _resolve([{"clip_id": "clip_001", "spine_block_position": 1,
                     "preferred_moment": "wide street"}])
    assert len(out["b_roll_assignments"]) == 1
    entry = out["b_roll_assignments"][0]
    assert entry["clip_id"] == "clip_001"
    assert entry["source_file"] == "/footage/IMG_1806.MOV"
    assert entry["timeline_start"] == 0.0
    assert entry["video_out"] > entry["video_in"]
    assert entry["duration_seconds"] == round(
        entry["video_out"] - entry["video_in"], 3)


def test_broll_audio_is_never_linked():
    """A cutaway that carries its own sound talks over the narration."""
    out = _resolve([{"clip_id": "clip_001", "spine_block_position": 1}])
    assert out["b_roll_assignments"][0]["video_only"] is True


def test_a_cutaway_never_claims_more_timeline_than_its_source_can_fill():
    """clip_001 is 3.567s of footage under a 4.0s block.

    Claiming the whole block would leave V2 showing a frozen or absent
    frame for the remainder - a hole `_assert_timeline_fully_covered`
    fails the build on.
    """
    out = _resolve([{"clip_id": "clip_001", "spine_block_position": 1}])
    entry = out["b_roll_assignments"][0]
    played = round(entry["video_out"] - entry["video_in"], 3)
    claimed = round(entry["timeline_end"] - entry["timeline_start"], 3)
    assert claimed <= played + 0.001, (
        f"claimed {claimed}s of timeline from {played}s of source")
    assert claimed < 4.0


def test_broll_matching_its_own_aroll_is_substituted_not_placed():
    """Cutting to the clip already on screen reads as a glitch, not a cut."""
    out = _resolve([{"clip_id": "clip_009", "spine_block_position": 1}])
    entry = out["b_roll_assignments"][0]
    assert entry["clip_id"] == "clip_001"
    assert "auto-substituted" in entry["selection_rationale"]


def test_broll_matching_its_own_aroll_is_dropped_when_nothing_replaces_it():
    """With no describable alternative there is no honest substitution."""
    out = _resolve(
        [{"clip_id": "clip_009", "spine_block_position": 1}],
        catalog=[RESOLVE_CATALOG[0]],
    )
    assert out["b_roll_assignments"] == []


def test_only_one_cutaway_reaches_a_block():
    """Two selections on one block claim the same stretch of V2."""
    out = _resolve([
        {"clip_id": "clip_001", "spine_block_position": 1},
        {"clip_id": "clip_001", "spine_block_position": 1},
    ])
    assert len(out["b_roll_assignments"]) == 1


def test_a_selection_naming_a_clip_the_catalog_does_not_have_is_dropped():
    out = _resolve([{"clip_id": "clip_404", "spine_block_position": 1}])
    assert out["b_roll_assignments"] == []


def test_a_selection_targeting_a_block_that_does_not_exist_is_dropped():
    out = _resolve([{"clip_id": "clip_001", "spine_block_position": 99}])
    assert out["b_roll_assignments"] == []


def test_conform_is_flagged_on_a_clip_that_is_not_the_delivery_shape():
    """clip_009 is 1920x1080 landscape; the frame is 1080x1920."""
    out = _resolve([{"clip_id": "clip_009", "spine_block_position": 1}],
                   spine={"structure": [dict(SPINE["structure"][0],
                                             clip_id="clip_001")]})
    entry = out["b_roll_assignments"][0]
    assert entry["clip_id"] == "clip_009"
    assert entry["needs_conform"] is True


def test_an_interjection_is_trimmed_around_broll_already_on_v2():
    """Two clips cannot share frames of a track.

    The interjection asks for 0-4s, which the block-1 assignment already
    holds; it must be trimmed into what is left rather than displacing it.
    """
    out = _resolve(
        [{"clip_id": "clip_001", "spine_block_position": 1}],
        [{"clip_id": "clip_001", "over_spine_block_position": 2,
          "timeline_start": 0.0, "timeline_end": 6.0}],
    )
    assignment = out["b_roll_assignments"][0]
    interjection = out["b_roll_interjections"][0]
    assert interjection["timeline_start"] >= assignment["timeline_end"]
    assert interjection["timeline_end"] <= 6.0


def test_an_interjection_with_no_free_window_is_dropped_not_placed_over():
    out = _resolve(
        [{"clip_id": "clip_001", "spine_block_position": 1}],
        [{"clip_id": "clip_001", "over_spine_block_position": 1,
          "timeline_start": 0.0, "timeline_end": 0.4}],
    )
    assert out["b_roll_assignments"] != []
    assert out["b_roll_interjections"] == []


def test_nothing_selected_resolves_to_nothing_placed():
    """There is no creative floor: an empty plan is an empty plan."""
    out = _resolve([])
    assert out == {"b_roll_assignments": [], "b_roll_interjections": []}
