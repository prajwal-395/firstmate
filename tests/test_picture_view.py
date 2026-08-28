"""The creative director can see past the first nineteen seconds.

`creative_direction` chose the story from `analysis.scene`, which is
`scene[]` rendered as prose.  On project 001 `scene[]` is ONE segment for
sixteen of the seventeen clips, so `IMG_1816_v3` - 188.6 seconds, and the
source of seven of the ten spoken lines in the final cut - was described
to the step as "[0.0-18.9s] Outdoor urban area with a parking lot and
construction site...".  That is 10% of the clip, and the step that decides
what the video is about was deciding it from the opening.

The material was already measured and already reaching other steps: the
vision pass writes an action window per ~10 seconds, 19 of them for that
clip, and `vision_schema_adapter` renders them as `blocks`.
`view:picture` is the reading of those records - what happens, in which
clip, between which two seconds.

What a scene boundary should MEAN is a separate, open question (#225);
this does not touch `scene[]`.
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
