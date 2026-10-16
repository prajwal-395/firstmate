"""GEO-027: dialogue-plus-visible-text queries resolve to one interval.

A question like "when does he say price while $5.00 is on screen" joins
the speech lane (what was SAID, with source intervals) against step
1.07's tracked text (what was SHOWN, with appearance intervals and
normalized boxes). The join reports overlap with both provenances and
abstains honestly when either lane is missing. Engineered payload, stub
index, no models.
"""
import json

from library.tools.analysis.footage_query import (
    SEGMENTS_FILE,
    FootageIndex,
)

SEGMENTS = [
    {"segment_id": "clip_001#speech#0000", "clip_id": "clip_001",
     "filename": "A.MOV", "kind": "speech",
     "start": 10.0, "end": 12.0, "duration": 2.0,
     "text": "our special price this week is five dollars"},
    {"segment_id": "clip_001#speech#0001", "clip_id": "clip_001",
     "filename": "A.MOV", "kind": "speech",
     "start": 40.0, "end": 42.0, "duration": 2.0,
     "text": "welcome back to the show"},
]

OCR = [
    {"track_id": "text-0001", "text": "$5.00", "confidence": 0.9,
     "appearances": [[10.5, 13.0]],
     "bbox_normalized": [0.1, 0.8, 0.2, 0.1]},
    {"track_id": "text-0002", "text": "WELCOME", "confidence": 0.8,
     "appearances": [[40.5, 41.5]],
     "bbox_normalized": [0.1, 0.1, 0.2, 0.1]},
]


def _write_index(root):
    index_dir = root / "idx"
    index_dir.mkdir(parents=True, exist_ok=True)
    payload = {"project_folder": str(root),
               "embed_model": None, "embed_backend": "test",
               "embed_dimension": 0,
               "segment_count": len(SEGMENTS), "segments": SEGMENTS,
               "ocr_texts": {"clip_001": OCR},
               "ocr_methods": {"clip_001": "EasyOCR"}}
    (index_dir / SEGMENTS_FILE).write_text(json.dumps(payload),
                                           encoding="utf-8")
    return FootageIndex(str(root), index_dir=index_dir)


def test_combined_search_joins_speech_and_screen(tmp_path):
    idx = _write_index(tmp_path)
    report = idx.combined_search(said="price", shown="$5.00")
    assert report["abstained"] is False
    assert report["matched"] == 1
    hit = report["hits"][0]
    assert hit["clip_id"] == "clip_001"
    assert hit["overlap_seconds"] > 0
    assert hit["speech_provenance"]["segment_id"] == "clip_001#speech"
    assert hit["text_provenance"]["track_id"] == "text-0001"
    assert hit["text_provenance"]["artifact_ref"]["method"] == "EasyOCR"


def test_combined_search_requires_overlap(tmp_path):
    idx = _write_index(tmp_path)
    # "price" is said at 10-12s; WELCOME shows at 40.5-41.5s: no overlap.
    report = idx.combined_search(said="price", shown="WELCOME")
    assert report["hits"] == []
    assert report["abstained"] is True


def test_combined_search_abstains_without_ocr_lane(tmp_path):
    index_dir = tmp_path / "idx"
    index_dir.mkdir(parents=True, exist_ok=True)
    payload = {"project_folder": str(tmp_path),
               "segment_count": len(SEGMENTS), "segments": SEGMENTS}
    (index_dir / SEGMENTS_FILE).write_text(json.dumps(payload),
                                           encoding="utf-8")
    idx = FootageIndex(str(tmp_path), index_dir=index_dir)
    report = idx.combined_search(said="price", shown="$5.00")
    assert report["abstained"] is True
    assert "tracked on-screen text (0)" in report["error"]
