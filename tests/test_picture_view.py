"""Every step that decides from a shot sees the whole clip, not its opening.

`view:picture` reads the vision pass's per-window action records - what
happens, in which clip, between which two seconds - across the WHOLE
clip, beside (never instead of) `analysis.scene`, keyed by the catalog
clip id. Incident (scene[] covered 46.4% of 001): `docs/evidence/picture_view.md`.
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
    # A clip with no observed action is NAMED - the absence is stated.
    assert "IMG_1819_v3" in view["picture"]["not_described"]


def test_the_view_carries_neither_body_language_nor_the_raw_record():
    view = build_view("picture", {"semantic_analysis_documents": DOCS})
    rows = view["picture"]["observed"]
    assert set(rows[0]) == {"clip_id", "start", "end", "visual"}
    assert "smiling" not in json_to_toon(view)


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

    # `plan_vfx` is routed the A-roll assignments, not the catalog: they
    # serve as the clip list.
    view = build_view("picture", {
        "semantic_analysis_documents": JOINABLE_DOCS,
        "a_roll_assignments": [{"video_segments": [
            {"clip_id": "clip_011", "source_file": "/p/raw/IMG_1816.MOV"}]}],
    })
    assert {r["clip_id"] for r in view["picture"]["observed"]} == {"clip_011"}

    # A document no routed clip list names keeps its own id and SAYS so:
    # a mixed-id table is the dangerous one.
    view = build_view("picture", {
        "semantic_analysis_documents": JOINABLE_DOCS,
        "clip_catalog": [CATALOG[1]],
    })
    assert {r["clip_id"] for r in view["picture"]["observed"]} == {"IMG_1816_v3"}
    assert "IMG_1816_v3" in view["picture"]["not_in_the_clip_list"]
