"""Search wired into planning: filled, refused, and fallen back - offline.

Proves `library/tools/search_in_planning.py` against fixture projects
built under `tmp_path` (never a real project, §8): a slot search fills
with the right clip on an analysed corpus, a visual slot on a
transcript-only corpus is refused rather than filled, and a degraded
index falls back to the catalog path. No encoder loads: the filled and
refused cases query in lexical mode, the degraded case carries no dense
matrix at all.
"""

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.steps.step_3_02_select_broll.post_bridge import resolve_broll
from library.tools import search_in_planning as sip
from library.tools.picture_plan import build_picture_plan

INDEX_REL = Path("pipeline_output") / "scratch" / "footage_index"
SEGMENTS_FILE = "footage_index.json"


def _write_index(
    project: Path, segments: list, per_clip: dict, embed_dimension: int = 0
):
    path = project / INDEX_REL
    path.mkdir(parents=True, exist_ok=True)
    (path / SEGMENTS_FILE).write_text(
        json.dumps(
            {
                "segments": segments,
                "embed_model": "all-MiniLM-L6-v2",
                "embed_backend": "sentence-transformers",
                "embed_dimension": embed_dimension,
                "coverage": {"per_clip": per_clip},
            }
        ),
        encoding="utf-8",
    )


def _seg(segment_id, clip_id, kind, start, end, text):
    return {
        "segment_id": segment_id,
        "clip_id": clip_id,
        "filename": f"{clip_id}.MOV",
        "kind": kind,
        "start": start,
        "end": end,
        "duration": round(end - start, 3),
        "text": text,
        "facets": {},
    }


def _analysed_project(tmp_path: Path) -> Path:
    """001-style corpus: two clips with vision rows and speech rows."""
    project = tmp_path / "analysed"
    _write_index(
        project,
        [
            _seg(
                "clip_001#action#000",
                "clip_001",
                "action",
                0.0,
                5.0,
                "wide view of parked cars panning right",
            ),
            _seg(
                "clip_001#speech#000",
                "clip_001",
                "speech",
                1.0,
                3.0,
                "we need to find a parking spot",
            ),
            _seg(
                "clip_002#action#000",
                "clip_002",
                "action",
                0.0,
                5.0,
                "close detail of a brick wall and a window",
            ),
            _seg(
                "clip_002#speech#000",
                "clip_002",
                "speech",
                1.0,
                2.0,
                "the pollen is terrible today",
            ),
        ],
        {
            "clip_001": {
                "picture": True,
                "scene": True,
                "action": True,
                "object": True,
            },
            "clip_002": {
                "picture": True,
                "scene": True,
                "action": True,
                "object": True,
            },
        },
    )
    return project


def _catalog():
    return [
        {"clip_id": "clip_001", "duration_seconds": 20.0},
        {"clip_id": "clip_002", "duration_seconds": 10.0},
    ]


def test_visual_slot_fills_with_the_right_clip(tmp_path):
    """The analysed corpus: a parking-lot slot is filled with clip_001."""
    project = _analysed_project(tmp_path)
    result = sip.slot_search(
        str(project),
        "show me the parked cars",
        slot_kind="visual",
        mode="lexical",
        catalog=_catalog(),
    )
    assert result["status"] == "filled", result
    assert result["clip_id"] == "clip_001", result
    assert result["evidence"]["kind"] == "action", result
    assert result["evidence"]["segment_id"] == "clip_001#action#000"
    assert result["span"] == {
        "start": 0.0,
        "end": 5.0,
        "timecode": "00:00.000-00:05.000",
    }
    assert result["evidence"]["backend_answered"] == "bm25", result


def test_visual_slot_on_transcript_only_corpus_is_refused(tmp_path):
    """No visual coverage: the slot is refused, never filled from speech."""
    project = tmp_path / "transcript-only"
    _write_index(
        project,
        [
            _seg(
                "clip_001#speech#000",
                "clip_001",
                "speech",
                1.0,
                3.0,
                "we need to find a parking spot",
            ),
        ],
        {
            "clip_001": {
                "picture": False,
                "scene": False,
                "action": False,
                "object": False,
                "ocr": False,
                "video_embeddings": False,
            },
        },
    )
    result = sip.slot_search(
        str(project),
        "show me the parking spot",
        slot_kind="visual",
        mode="lexical",
        catalog=_catalog(),
    )
    assert result["status"] == "refused_not_analysed", result


def test_transcript_hit_never_fills_a_visual_slot(tmp_path):
    """Speech matches lexically, the picture does not: abstain, not fill."""
    project = _analysed_project(tmp_path)
    result = sip.slot_search(
        str(project),
        "show me the pollen today",
        slot_kind="visual",
        mode="lexical",
        catalog=_catalog(),
    )
    assert result["status"] == "abstained_no_evidence", result
    assert "transcript" in result["reason"], result


def test_degraded_index_falls_back_to_the_catalog_path(tmp_path):
    """No dense embeddings: keyword-only cannot judge absence, so fall back."""
    project = _analysed_project(tmp_path)
    result = sip.slot_search(
        str(project),
        "show me the parked cars",
        slot_kind="visual",
        mode="hybrid",
        catalog=_catalog(),
    )
    assert result["status"] == "fallback_degraded", result
    assert "keyword" in result["reason"], result


def test_missing_index_falls_back_to_the_catalog_path(tmp_path):
    project = tmp_path / "no-index"
    project.mkdir()
    result = sip.slot_search(
        str(project), "show me the parked cars", slot_kind="visual", catalog=_catalog()
    )
    assert result["status"] == "fallback_no_index", result


def test_span_past_the_file_is_not_placed(tmp_path):
    """Evidence on a span the file cannot hold: the next candidate wins."""
    project = tmp_path / "long-span"
    _write_index(
        project,
        [
            _seg(
                "clip_001#action#000",
                "clip_001",
                "action",
                0.0,
                50.0,
                "wide view of parked cars panning right",
            ),
            _seg(
                "clip_002#action#000",
                "clip_002",
                "action",
                0.0,
                5.0,
                "parked cars along the street curb",
            ),
        ],
        {
            "clip_001": {"picture": True, "action": True},
            "clip_002": {"picture": True, "action": True},
        },
    )
    result = sip.slot_search(
        str(project),
        "parked cars",
        slot_kind="visual",
        mode="lexical",
        catalog=_catalog(),
    )
    assert result["status"] == "filled", result
    # clip_001's 50 s span runs past its 20 s file, so clip_002 fills.
    assert result["clip_id"] == "clip_002", result


def test_verify_span_refuses_what_the_file_cannot_hold():
    catalog = _catalog()
    assert sip.verify_span("clip_001", 1.0, 5.0, catalog) == {"ok": True}
    assert sip.verify_span("clip_999", 1.0, 5.0, catalog)["ok"] is False
    assert sip.verify_span("clip_001", 5.0, 5.0, catalog)["ok"] is False
    assert sip.verify_span("clip_001", -1.0, 5.0, catalog)["ok"] is False
    assert sip.verify_span("clip_001", 0.0, 50.0, catalog)["ok"] is False


# ── Planning consumes the helper without changing without it ─────────


def _tie_catalog():
    return [
        {
            "clip_id": "clip_a",
            "source_file": "/footage/a.mp4",
            "duration_seconds": 60.0,
            "width": 1920,
            "height": 1080,
            "frame_rate": 30.0,
        },
        {
            "clip_id": "clip_b",
            "source_file": "/footage/b.mp4",
            "duration_seconds": 60.0,
            "width": 1920,
            "height": 1080,
            "frame_rate": 30.0,
        },
    ]


def _tie_semantic():
    def doc():
        return {
            "analysis_metadata": {"pipeline_version": "v3"},
            "scene": [
                {"start": 0, "end": 60, "location": "studio", "type": "interview"}
            ],
            "camera": [
                {"start": 0, "end": 60, "framing": "medium", "stability": "stable"}
            ],
            "assessment": {
                "content_type": "interview",
                "speech_present": True,
                "usable_portions": "0-60",
            },
            "actions": [
                {
                    "window": [0, 60],
                    "actions": [
                        {
                            "start": 0,
                            "end": 60,
                            "action": "person sitting talking",
                            "body_language": "neutral posture",
                            "speech_cue": "speaking",
                        }
                    ],
                }
            ],
        }

    return {"clip_a": doc(), "clip_b": doc()}


def _tie_spine():
    return {
        "structure": [
            {
                "position": 1,
                "block_type": "speech",
                "clip_id": "clip_main",
                "source_start": 0.0,
                "source_end": 10.0,
                "timeline_start": 0.0,
                "timeline_end": 10.0,
            }
        ]
    }


def test_picture_plan_unchanged_on_the_catalog_path():
    """Fallback hints vouch for nothing: selections match the no-hint plan."""
    catalog, semantic, spine = _tie_catalog(), _tie_semantic(), _tie_spine()
    plain = build_picture_plan(spine, catalog, semantic)
    fallback_hints = {
        "1": {
            "status": "fallback_no_index",
            "query": "studio",
            "reason": "no footage index",
        }
    }
    assisted = build_picture_plan(spine, catalog, semantic, search_hints=fallback_hints)
    assert [b["selected_clip_id"] for b in assisted["blocks"]] == [
        b["selected_clip_id"] for b in plain["blocks"]
    ]
    assert assisted["blocks"][0]["search"]["status"] == "fallback_no_index"


def test_picture_plan_vouch_breaks_ties_toward_evidence():
    """Identical scores: the vouched clip wins, and the record says so."""
    catalog, semantic, spine = _tie_catalog(), _tie_semantic(), _tie_spine()
    plain = build_picture_plan(spine, catalog, semantic)
    assert plain["blocks"][0]["selected_clip_id"] == "clip_a"
    hints = {
        "1": {
            "status": "filled",
            "query": "studio",
            "clip_id": "clip_b",
            "span": {"start": 0.0, "end": 10.0},
            "evidence": {
                "segment_id": "clip_b#scene#000",
                "kind": "scene",
                "text": "studio",
                "backend_answered": "bm25",
            },
        }
    }
    assisted = build_picture_plan(spine, catalog, semantic, search_hints=hints)
    block = assisted["blocks"][0]
    assert block["selected_clip_id"] == "clip_b", block
    assert block["search"]["status"] == "filled"
    assert block["search"]["vouched_selected"] is True
    assert assisted["summary"]["search_vouched_blocks"] == 1


def test_post_bridge_records_the_catalog_path_without_an_index(tmp_path):
    """No index on disk: the model's choice stands, said as catalog path."""
    project = tmp_path / "no-index"
    project.mkdir()
    result = resolve_broll(
        [
            {
                "clip_id": "clip_002",
                "spine_block_position": 1,
                "preferred_moment": "show me the brick wall",
                "selection_rationale": "detail cutaway",
            }
        ],
        [],
        _catalog(),
        [],
        [],
        {
            "structure": [
                {
                    "position": 1,
                    "block_type": "transition_slot",
                    "clip_id": "clip_001",
                    "timeline_start": 0.0,
                    "timeline_end": 2.0,
                }
            ]
        },
        target_resolution=(1080, 1920),
        project_folder=str(project),
    )
    assignment = result["b_roll_assignments"][0]
    assert assignment["clip_id"] == "clip_002", result
    assert assignment["search_status"] == "catalog_fallback:fallback_no_index", (
        assignment
    )
    assert assignment["search_evidence"] is None
