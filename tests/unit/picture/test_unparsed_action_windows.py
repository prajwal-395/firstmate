"""An unparsed action window is not an empty one, and must not vanish.

The data distinguishes an unparsed VLM window (`parse_error`) from a
genuinely empty one, and the picture view shows it as unmeasured rather
than omitting it. Incident: `docs/evidence/unparsed_action_windows.md`.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from library.tools.context_views import build_view
from library.tools.vision_schema_adapter import (
    UNPARSED_WINDOW_VISUAL,
    _blocks_from_actions,
    adapt_semantic_document,
)


# ── Faithful reconstruction of the two known windows ─────────────────
#
# clip_004/IMG_1809: three 10s windows.  [0,10] and [20,30] parsed fine;
# [10,20] took 6.25s (fastest of the three) and returned actions:[],
# consistent with a short or empty response that failed to parse.
#
# clip_015/IMG_1820: one 10s window.  [0,10] took 73.94s (by far the
# longest), consistent with a long malformed response.

IMG_1809_PROFILE = {
    "clip_id": "IMG_1809_v3",
    "file_path": "/footage/IMG_1809.MOV",
    "duration_s": 30.0,
    "fps": 30.0,
    "resolution": [1920, 1080],
    "transcript": "",
    "scene": [{"start": 0.0, "end": 30.0, "location": "parking lot",
               "type": "outdoor", "lighting": "daylight",
               "notable_features": []}],
    "camera": [{"start": 0, "end": 30, "mode": "handheld",
                "framing": "wide", "stability": "stable",
                "movement": "stationary"}],
    "actions": [
        {
            "window": [0, 10],
            "actions": [
                {"start": 0, "end": 10,
                 "action": "The person walks across the parking lot.",
                 "speech_cue": None,
                 "body_language": "relaxed stride"},
            ],
            "analysis_time_s": 12.50,
        },
        {
            # THE UNPARSED WINDOW - parse_json_object returned {}.
            "window": [10, 20],
            "actions": [],
            "analysis_time_s": 6.25,
            "parse_error": True,
        },
        {
            "window": [20, 30],
            "actions": [
                {"start": 20, "end": 30,
                 "action": "The person stops and looks up.",
                 "speech_cue": None,
                 "body_language": "head tilted back"},
            ],
            "analysis_time_s": 14.00,
        },
    ],
    "objects": [],
    "assessment": {
        "content_type": "person_talking_to_camera",
        "usable_ranges": [[0, 30.0]],
        "usable_ranges_method": "deterministic_v1",
        "camera_stability": "stable",
    },
    "analysis_metadata": {"pipeline_version": "v3", "frames_extracted": 7},
}

IMG_1820_PROFILE = {
    "clip_id": "IMG_1820_v3",
    "file_path": "/footage/IMG_1820.MOV",
    "duration_s": 10.0,
    "fps": 30.0,
    "resolution": [1920, 1080],
    "transcript": "",
    "scene": [{"start": 0.0, "end": 10.0, "location": "sidewalk",
               "type": "outdoor", "lighting": "daylight",
               "notable_features": []}],
    "camera": [{"start": 0, "end": 10, "mode": "handheld",
                "framing": "medium", "stability": "shaky",
                "movement": "walking"}],
    "actions": [
        {
            # THE UNPARSED WINDOW - 73.94s, long malformed response.
            "window": [0, 10],
            "actions": [],
            "analysis_time_s": 73.94,
            "parse_error": True,
        },
    ],
    "objects": [],
    "assessment": {
        "content_type": "scenery",
        "usable_ranges": [[0, 10.0]],
        "usable_ranges_method": "deterministic_v1",
        "camera_stability": "shaky",
    },
    "analysis_metadata": {"pipeline_version": "v3", "frames_extracted": 3},
}

# A clip with genuinely empty actions (no parse error).
GENUINELY_EMPTY_PROFILE = {
    "clip_id": "IMG_1850_v3",
    "file_path": "/footage/IMG_1850.MOV",
    "duration_s": 10.0,
    "fps": 30.0,
    "resolution": [1920, 1080],
    "transcript": "",
    "scene": [{"start": 0.0, "end": 10.0, "location": "empty room",
               "type": "indoor", "lighting": "dim",
               "notable_features": []}],
    "camera": [{"start": 0, "end": 10, "mode": "mounted",
                "framing": "wide", "stability": "stable",
                "movement": "stationary"}],
    "actions": [
        {
            "window": [0, 10],
            "actions": [],
            "analysis_time_s": 8.00,
            # No parse_error - the model genuinely saw nothing.
        },
    ],
    "objects": [],
    "assessment": {
        "content_type": "scenery",
        "usable_ranges": [[0, 10.0]],
        "usable_ranges_method": "deterministic_v1",
        "camera_stability": "stable",
    },
    "analysis_metadata": {"pipeline_version": "v3", "frames_extracted": 3},
}


# ── Part 1: The data distinguishes unparsed from genuinely empty ─────


# ── Part 2: The sentinel block reaches the adapted document ──────────


class TestSentinelBlock:
    """An unparsed window produces a sentinel block so it is visible."""

    def test_unparsed_window_produces_a_block_with_visual(self):
        """The block exists and carries the unmeasured marker."""
        blocks = _blocks_from_actions(IMG_1809_PROFILE)
        # Three windows: two parsed with actions, one unparsed.
        assert len(blocks) == 3
        unparsed_block = blocks[1]
        assert unparsed_block["visual"] == UNPARSED_WINDOW_VISUAL
        assert unparsed_block["start"] == 10
        assert unparsed_block["end"] == 20
        assert unparsed_block["parse_error"] is True

# ── Part 3: The picture view shows unmeasured rather than omitting ───


def _adapted_docs():
    return [
        adapt_semantic_document(IMG_1809_PROFILE),
        adapt_semantic_document(IMG_1820_PROFILE),
        adapt_semantic_document(GENUINELY_EMPTY_PROFILE),
    ]


class TestPictureView:
    """The picture view reports unparsed windows as unmeasured."""

    def test_unparsed_window_appears_in_the_view(self):
        """IMG_1809 [10,20] used to be omitted.  Now it is a row."""
        view = build_view("picture", {
            "semantic_analysis_documents": _adapted_docs()})
        rows = view["picture"]["observed"]
        img_1809_rows = [r for r in rows if r["clip_id"] == "IMG_1809_v3"]
        # Three rows: [0,10], [10,20] (unmeasured), [20,30]
        assert len(img_1809_rows) == 3
        unmeasured_row = img_1809_rows[1]
        assert unmeasured_row["start"] == 10.0
        assert unmeasured_row["end"] == 20.0
        assert unmeasured_row["visual"] == UNPARSED_WINDOW_VISUAL

        # IMG_1820 (only window unparsed) used to be listed as
        # 'not_described'. Now it has a row, so it is described.
        img_1820_rows = [r for r in rows if r["clip_id"] == "IMG_1820_v3"]
        assert len(img_1820_rows) == 1
        assert img_1820_rows[0]["visual"] == UNPARSED_WINDOW_VISUAL
        assert "IMG_1820_v3" not in view["picture"].get("not_described", "")

    def test_genuinely_empty_clip_is_still_undescribed(self):
        """A window with no actions and no parse error still produces no
        block, so the clip is listed as not_described."""
        view = build_view("picture", {
            "semantic_analysis_documents": _adapted_docs()})
        not_described = view["picture"].get("not_described", "")
        assert "IMG_1850_v3" in not_described

    def test_unparsed_windows_field_names_the_gaps(self):
        """The view explicitly reports which windows were unparsed."""
        view = build_view("picture", {
            "semantic_analysis_documents": _adapted_docs()})
        unparsed = view["picture"].get("unparsed_windows", "")
        assert "IMG_1809_v3" in unparsed
        assert "IMG_1820_v3" in unparsed
        assert "2 window(s)" in unparsed

        # Normal operation: no field clutter.
        clean = adapt_semantic_document(dict(
            GENUINELY_EMPTY_PROFILE, clip_id="clean_v3", actions=[{
                "window": [0, 10],
                "actions": [{"start": 0, "end": 10,
                             "action": "Walks forward.",
                             "speech_cue": None,
                             "body_language": "relaxed"}]}]))
        view = build_view("picture", {"semantic_analysis_documents": [clean]})
        assert "unparsed_windows" not in view["picture"]

# ── Known unknowns, stated ───────────────────────────────────────────
