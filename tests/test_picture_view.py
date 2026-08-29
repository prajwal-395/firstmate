"""Every step that decides from a shot sees the whole clip, not its opening.

`analysis.scene` is `scene[]` rendered as prose, and on project 001
`scene[]` describes 374.2 s of 807.0 s - 46.4%.  `IMG_1816_v3` is 188.6
seconds, supplies seven of the eleven A-roll blocks in the finished cut,
and is described for 18.9 of them.  Five of the sixteen source windows
the video actually plays fall outside the described range; all sixteen
fall inside the vision pass's per-window action records, which reach the
last second of all seventeen clips.

`view:picture` is the reading of those records - what happens, in which
clip, between which two seconds - and 2.01, 2.02, 3.02, 4.02, 4.03 and
4.04 all declare it.

**It is not a substitute for `scene[]`.**  `scene[]` says WHERE (location,
type, lighting, notable features); the action windows say WHAT HAPPENS.
The view goes beside `analysis.scene`, never in place of it, and a step
that drops the place axis fails below.

Rows are keyed by the CATALOG clip id wherever a routed clip list makes
that join possible, because that is the id every other table in a
planning step's context uses (AGENTS.md 10.1).

What a scene boundary should MEAN is a separate, open question (#225);
why `scene[]` covers 46.4% is #302.  Neither is touched here.
"""

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from library.tools.context_projector import project_fields
from library.tools.context_views import build_view
from library.tools.toon_serializer import json_to_toon

STEPS = REPO / "library" / "steps"

# One long clip described across its whole length, one clip the vision
# pass described no action for at all.
DOCS = [
    {
        "clip_id": "IMG_1816_v3",
        "duration_s": 188.578,
        "analysis": {"scene": "[0.0-18.9s] Outdoor urban area"},
        "scene": [{"start": 0.0, "end": 18.9, "location": "Outdoor urban area"}],
        "blocks": [
            {"timestamp_range": "0:00-0:10", "start": 0, "end": 10,
             "visual": "The person is looking at the camera.",
             "body_language": "smiling"},
            {"timestamp_range": "3:00-3:08", "start": 180, "end": 188.578,
             "visual": "The person is looking upwards as if speaking.",
             "body_language": "head tilted back"},
        ],
    },
    {"clip_id": "IMG_1819_v3", "duration_s": 4.7, "blocks": []},
]


def manifest(step_dir: str) -> dict:
    return json.loads((STEPS / step_dir / "manifest.json").read_text())


def test_the_view_spans_the_whole_clip():
    view = build_view("picture", {"semantic_analysis_documents": DOCS})
    rows = view["picture"]["observed"]
    assert [r["clip_id"] for r in rows] == ["IMG_1816_v3"] * 2
    assert rows[0]["start"] == 0.0
    assert rows[-1]["end"] == 188.6, (
        "the last record has to reach the end of the clip, or the step is "
        "still reading a prefix of it"
    )
    assert rows[0]["visual"] == "The person is looking at the camera."


def test_a_clip_with_no_observed_action_is_named():
    """State the absence - the rule `view:prosody` follows."""
    view = build_view("picture", {"semantic_analysis_documents": DOCS})
    assert "IMG_1819_v3" in view["picture"]["not_described"]


def test_the_view_carries_neither_body_language_nor_the_raw_record():
    view = build_view("picture", {"semantic_analysis_documents": DOCS})
    rows = view["picture"]["observed"]
    assert set(rows[0]) == {"clip_id", "start", "end", "visual"}
    assert "smiling" not in json_to_toon(view)


def test_a_view_whose_source_is_not_routed_contributes_nothing():
    assert build_view("picture", {"clip_catalog": []}) == {}


def test_creative_direction_declares_the_view_and_the_input_it_reads():
    """A view is not routing. The step still has to be sent the input."""
    m = manifest("step_2_01_creative_direction")
    assert "view:picture" in m["context_fields"]
    assert "semantic_analysis_documents" in {
        i["name"] for i in m["interface"]["inputs"]}


def test_the_view_reaches_the_prompt_and_survives_a_second_projection():
    """`creative_direction` is `llm_only`, so it is projected twice.

    The second pass sees a tree the first one already took `blocks` out
    of, so a view that rebuilt itself from scratch would delete what it
    had just built.
    """
    cf = manifest("step_2_01_creative_direction")["context_fields"]
    once = project_fields({"semantic_analysis_documents": DOCS}, cf)
    twice = project_fields(once, cf)
    assert twice["picture"] == once["picture"]
    assert "The person is looking upwards as if speaking." in json_to_toon(twice)


# The join. Documents are keyed by file stem; every table a planning step
# reasons over is keyed by the catalog's synthetic id (AGENTS.md 10.1), and
# a table the step cannot join to its own spine says nothing - which is
# what step 2.02 reported about `topics_toon`.
CATALOG = [
    {"clip_id": "clip_011", "file_path": "/p/raw/IMG_1816.MOV"},
    {"clip_id": "clip_014", "file_path": "/p/raw/IMG_1819.MOV"},
]

JOINABLE_DOCS = [
    {**DOCS[0], "file_path": "/p/raw/IMG_1816.MOV"},
    {**DOCS[1], "file_path": "/p/raw/IMG_1819.MOV"},
]


def test_rows_are_keyed_by_the_clip_id_the_rest_of_the_context_uses():
    view = build_view("picture", {
        "semantic_analysis_documents": JOINABLE_DOCS,
        "clip_catalog": CATALOG,
    })
    assert {r["clip_id"] for r in view["picture"]["observed"]} == {"clip_011"}
    assert "clip_014" in view["picture"]["not_described"]


def test_the_assignments_serve_as_the_clip_list_when_the_catalog_is_not_routed():
    """`plan_vfx` is routed the A-roll assignments, not the catalog."""
    view = build_view("picture", {
        "semantic_analysis_documents": JOINABLE_DOCS,
        "a_roll_assignments": [{"video_segments": [
            {"clip_id": "clip_011", "source_file": "/p/raw/IMG_1816.MOV"}]}],
    })
    assert {r["clip_id"] for r in view["picture"]["observed"]} == {"clip_011"}


def test_a_document_no_routed_clip_list_names_keeps_its_own_id_and_says_so():
    """A MIXED table is the dangerous one - nothing on a row says which id
    space it is in, so the absence is stated."""
    view = build_view("picture", {
        "semantic_analysis_documents": JOINABLE_DOCS,
        "clip_catalog": [CATALOG[1]],
    })
    assert {r["clip_id"] for r in view["picture"]["observed"]} == {"IMG_1816_v3"}
    assert "IMG_1816_v3" in view["picture"]["not_in_the_clip_list"]


def test_with_no_clip_list_at_all_nothing_is_flagged():
    view = build_view("picture", {"semantic_analysis_documents": DOCS})
    assert "not_in_the_clip_list" not in view["picture"]


def test_the_view_reads_the_nested_step_output_too():
    """`plan_transitions` is routed the whole step 1.03 output."""
    view = build_view("picture", {
        "semantic_analysis": {"semantic_analysis_documents": JOINABLE_DOCS},
        "clip_catalog": CATALOG,
    })
    assert {r["clip_id"] for r in view["picture"]["observed"]} == {"clip_011"}


# Which steps decide from what a shot looks like. Each of these was checked
# against its own handoff and its reasoning trace on the run of record; the
# other six of the twelve are named in the commit message with the reason
# they are not here.
PICTURE_DECIDING_STEPS = [
    "step_2_01_creative_direction",
    "step_2_02_speech_sequence",
    "step_3_02_select_broll",
    "step_4_02_plan_transitions",
    "step_4_03_plan_vfx",
    "step_4_04_plan_sfx",
]


def test_every_step_that_decides_from_a_shot_sees_the_whole_clip():
    for step in PICTURE_DECIDING_STEPS:
        m = manifest(step)
        cf = m["context_fields"]
        assert "view:picture" in cf, (
            f"{step} decides from what a shot looks like and does not "
            f"declare view:picture")
        routed = {i["name"] for i in m["interface"]["inputs"]}
        assert routed & {"semantic_analysis_documents", "semantic_analysis"}, (
            f"{step} declares the view but is not routed the input it reads")


def test_no_picture_deciding_step_still_reads_the_raw_blocks():
    """`blocks` in a TOON cell is 33,098 B of json.dumps on 001 against the
    view's 10,508, and `speech_sequence` was the one still getting it."""
    for step in PICTURE_DECIDING_STEPS:
        cf = manifest(step)["context_fields"]
        assert "semantic_analysis_documents.*.blocks" not in cf, step


# A step whose PRE-BRIDGE renders the place axis into its own table, and
# the source that must contain the renderer's name for that claim to hold.
# The exemption is from the `context_fields` path, never from the axis:
# `vision_schema_adapter.scene_prose` is the one renderer of `scene[]`, so
# a bridge that stops calling it fails this test rather than passing it
# by omission.
PLACE_AXIS_VIA_PRE_BRIDGE = {
    "step_4_02_plan_transitions": "library/steps/step_4_02_plan_transitions",
    # 3.02's `broll_candidates_toon.description` is `scene_prose` verbatim,
    # capped at 600 characters - a cap that binds on 0 of 001's 17 clips.
    # The raw structure moved to a REFERENCE when the step was carrying
    # three views of one analysis at 60.7% of its context; the place axis
    # did not move with it.
    "step_3_02_select_broll": "library/steps/step_3_02_select_broll",
}


def test_the_place_axis_is_not_dropped_for_the_action_axis():
    """`blocks[]` is not a substitute for `scene[]`.

    `scene[]` carries location, type, lighting and notable_features and on
    001 covers 374.2 s of 807.0 s; the action windows carry what happens
    and reach the last second of all seventeen clips. They are different
    axes, so the view is added beside `analysis.scene`, never in place of
    it. Two steps read the same measurements through their own pre-bridge
    table instead, and this checks that the table really renders them.
    """
    for step in PICTURE_DECIDING_STEPS:
        if step in PLACE_AXIS_VIA_PRE_BRIDGE:
            step_dir = REPO / PLACE_AXIS_VIA_PRE_BRIDGE[step]
            source = "".join(
                f.read_text(encoding="utf-8")
                for f in sorted(step_dir.glob("*.py")))
            reached = ("scene_prose" in source
                       or "clip_observations" in source
                       or "describe_clip" in source)
            assert reached, (
                f"{step} is exempted from declaring the place axis because "
                f"its pre-bridge renders it, and nothing in its own source "
                f"reaches scene_prose any more")
            continue
        cf = manifest(step)["context_fields"]
        assert any(p.endswith("analysis.scene") or p.endswith(".scene")
                   for p in cf), (
            f"{step} lost the only description of WHERE the clip is")
