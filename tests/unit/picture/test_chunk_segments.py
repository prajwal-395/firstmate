"""Chunk rows: every span of footage retrievable at 8 s granularity.

A clip whose ingest measured nothing contributes zero fine rows, so a
search over it answers silence for footage that exists. Chunk rows
are the coarse guarantee: fixed windows tiling each clip, text joined
from whatever the window overlaps, readable through the existing
search and filter. Every test builds its project under `tmp_path`;
no test reads a real project.
"""
import json

import numpy as np
import pytest

from library.tools.analysis import footage_query, footage_segments
from library.tools.analysis.footage_query import FootageIndex, build_index
from library.tools.analysis.footage_segments import (
    CHUNK_S,
    SEGMENT_KINDS,
    UNMEASURED_CHUNK_TEXT,
    build_segments,
    cut_chunks,
)


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    steps = root / "pipeline_output" / "steps"
    _write(root / "pipeline_data.json", {
        "step_outputs": {"catalog": {"clip_catalog": [
            {"clip_id": "clip_001", "filename": "IMG_0001.MOV",
             "source_file": str(root / "raw" / "IMG_0001.MOV"),
             "path": str(root / "raw" / "IMG_0001.MOV"),
             "duration_seconds": 20.0},
            {"clip_id": "clip_002", "filename": "IMG_0002.MOV",
             "source_file": str(root / "raw" / "IMG_0002.MOV"),
             "path": str(root / "raw" / "IMG_0002.MOV"),
             "duration_seconds": 10.0},
        ]}},
    })
    _write(steps / "1_04_temporal_index" / "index" / "clip_001.json", {
        "clip_id": "clip_001",
        "duration": 20.0,
        "speech_regions": [
            {"start": 1.0, "end": 3.0, "text": "we need to find a parking spot",
             "words": [{"word": "parking", "start": 2.0, "end": 2.4}]},
            {"start": 6.0, "end": 7.5, "text": "the pollen is terrible today",
             "words": []},
        ],
        "speech_activity": {"sample_rate_hz": 1, "values": [0.0] * 10 + [1.0] * 10},
        "motion_energy": {"sample_rate_hz": 1, "values": [0.5] * 20},
        "face_presence": {"sample_rate_hz": 1, "values": [1.0] * 20},
        "color_curves": {"sample_rate_hz": 1,
                         "brightness_values": [0.4] * 20,
                         "saturation_values": [0.2] * 20},
    })
    _write(steps / "1_04_temporal_index" / "index" / "clip_002.json", {
        "clip_id": "clip_002", "duration": 10.0, "speech_regions": [],
        "motion_energy": {"sample_rate_hz": 1, "values": [0.9] * 10},
    })
    _write(steps / "1_03_semantic_analysis" / "clip_profile_IMG_0001_v3.json", {
        "clip_id": "IMG_0001",
        "file_path": str(root / "raw" / "IMG_0001.MOV"),
        "duration_s": 20.0,
        "scene": [{"start": 0.0, "end": 20.0, "location": "Outdoor parking lot",
                   "type": "outdoor", "lighting": "Daylight",
                   "notable_features": ["Parked cars"]}],
        "camera": [{"start": 0, "end": 20, "mode": "selfie",
                    "framing": "close-up", "stability": "shaky",
                    "movement": "walking"}],
        "actions": [{"window": [0, 10], "actions": [
            {"start": 0, "end": 10,
             "action": "The man walks across the lot",
             "body_language": "He smiles broadly", "speech_cue": None}]}],
        "objects": [{"label": "red bicycle", "category": "object",
                     "role": "background", "readable_text": None,
                     "appearances": [[2.0, 5.0], [12.0, 15.0]]}],
        "assessment": {"content_type": "person_talking_to_camera"},
        "analysis_metadata": {"pipeline_version": "v3"},
    })
    return root


@pytest.fixture
def unmeasured_project(tmp_path):
    """A catalog clip the ingest never described: no 1.03, no 1.04."""
    root = tmp_path / "bare"
    _write(root / "pipeline_data.json", {
        "step_outputs": {"catalog": {"clip_catalog": [
            {"clip_id": "clip_009", "filename": "IMG_0009.MOV",
             "source_file": str(root / "raw" / "IMG_0009.MOV"),
             "path": str(root / "raw" / "IMG_0009.MOV"),
             "duration_seconds": 18.0},
        ]}},
    })
    return root


# ─── The cutting ────────────────────────────────────────────────


def test_cut_chunks_tiles_with_a_short_tail():
    assert cut_chunks(20.0) == [(0.0, 8.0), (8.0, 16.0), (16.0, 20.0)]
    assert cut_chunks(8.0) == [(0.0, 8.0)]
    assert cut_chunks(3.0) == [(0.0, 3.0)]
    assert cut_chunks(0) == []
    assert cut_chunks(None) == []
    assert cut_chunks(-5) == []
    assert CHUNK_S == 8.0


def test_chunk_kind_is_registered():
    assert "chunk" in SEGMENT_KINDS


def test_chunks_cover_every_clip_fully(project):
    segments = build_segments(project, kinds=("chunk",))
    by_clip = {}
    for s in segments:
        by_clip.setdefault(s.clip_id, []).append((s.start, s.end))
    assert by_clip["clip_001"] == [(0.0, 8.0), (8.0, 16.0), (16.0, 20.0)]
    assert by_clip["clip_002"] == [(0.0, 8.0), (8.0, 10.0)]
    assert [s.segment_id for s in segments
            if s.clip_id == "clip_001"] == [
        "clip_001#chunk#000", "clip_001#chunk#001", "clip_001#chunk#002"]


def test_chunk_text_joins_what_its_window_overlaps(project):
    chunks = [s for s in build_segments(project, kinds=("chunk",))
              if s.clip_id == "clip_001"]
    first = chunks[0].text.lower()
    assert "parking spot" in first, "the overlapping speech regions"
    assert "parking lot" in first, "the overlapping scene observation"
    assert "walks across the lot" in first, "the overlapping action window"
    assert "red bicycle" in first, "the overlapping object appearance"
    assert chunks[0].facets["has_speech"] is True
    assert chunks[0].facets["measured"] is True

    last = chunks[2]
    assert "parking spot" not in last.text.lower()
    assert "pollen" not in last.text.lower()
    assert "parking lot" in last.text.lower(), "the scene spans the clip"


def test_unmeasured_footage_still_gets_rows(unmeasured_project):
    segments = build_segments(unmeasured_project)
    chunks = [s for s in segments if s.kind == "chunk"]
    assert [(c.start, c.end) for c in chunks] == [
        (0.0, 8.0), (8.0, 16.0), (16.0, 18.0)]
    assert all(c.text == UNMEASURED_CHUNK_TEXT for c in chunks)
    assert all(c.facets["measured"] is False for c in chunks)
    assert [s.kind for s in segments if s.kind != "chunk"] == [], (
        "no fine row is cut where nothing was measured - the chunks "
        "are the only rows this footage has")


# ─── The reader reads them ──────────────────────────────────────


@pytest.fixture
def offline_index(project, tmp_path, monkeypatch):
    """The existing stub-embedder pattern: retrieval without models."""

    def fake_loader():
        def encode(texts):
            rows = []
            for text in texts:
                vector = np.zeros(26, dtype="float32")
                for char in (text or "").lower():
                    if "a" <= char <= "z":
                        vector[ord(char) - 97] += 1.0
                norm = np.linalg.norm(vector) or 1.0
                rows.append(vector / norm)
            return np.vstack(rows) if rows else np.zeros((0, 26), dtype="float32")
        return encode, "test-stub"

    monkeypatch.setattr(footage_query, "load_embedder", fake_loader)
    index_dir = tmp_path / "index"
    build_index(project, index_dir=index_dir)
    return FootageIndex(project, index_dir=index_dir)


def test_search_returns_the_covering_chunk(offline_index):
    report = offline_index.search_report("parking", mode="hybrid", floor=0)
    chunks = [r for r in report["results"] if r["kind"] == "chunk"]
    assert chunks, "the chunk rows reach the ranking, not just the store"
    covering = [r for r in chunks
                if r["clip_id"] == "clip_001" and r["start"] <= 2.0 <= r["end"]]
    assert covering, "the 0-8s window answers for words spoken at 2s"
    assert "parking" in covering[0]["text"].lower()


def test_filter_selects_chunks(offline_index):
    rows = offline_index.filter(kind="chunk")
    assert rows and all(r["kind"] == "chunk" for r in rows)
    assert {r["clip_id"] for r in rows} == {"clip_001", "clip_002"}
