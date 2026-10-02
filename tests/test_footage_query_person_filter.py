"""`ren search --person` / `filter --person`: the join between the
footage index and the person entity store (M3b).

Builds its project and memory under `tmp_path`; no real media, ffmpeg,
insightface or ECAPA involved - the measured parts live in
`data/vep-person-entity-store/eval/results.md` and PR #1482, this guards
the JOIN logic `footage_query.FootageIndex.filter(person=...)` adds.
"""

import json
from pathlib import Path

import numpy as np
import pytest

from library.tools import footage_identity, source_memory
from library.tools.analysis import footage_query
from library.tools.analysis.footage_query import FootageIndex, build_index
from library.tools.ren_refusal import RenRefusal


@pytest.fixture
def memory_root(tmp_path, monkeypatch):
    root = tmp_path / "memory"
    monkeypatch.setenv(source_memory.MEMORY_ROOT_ENV, str(root))
    return root


def _write(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _media(tmp_path: Path, name: str, seed: bytes) -> Path:
    path = tmp_path / "raw" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(seed * (3 * 1024 * 1024 // len(seed) + 1))
    return path


@pytest.fixture
def project(tmp_path):
    """One project, two clips, each with its own speaking person."""
    root = tmp_path / "proj"
    steps = root / "pipeline_output" / "steps"

    media_1 = _media(tmp_path, "IMG_0001.MOV", b"clip-one")
    media_2 = _media(tmp_path, "IMG_0002.MOV", b"clip-two")
    digest_1 = footage_identity.fingerprint(str(media_1))["content_digest"]
    digest_2 = footage_identity.fingerprint(str(media_2))["content_digest"]

    _write(root / "pipeline_data.json", {
        "step_outputs": {"catalog": {"clip_catalog": [
            {"clip_id": "clip_001", "filename": media_1.name,
             "source_file": str(media_1), "path": str(media_1),
             "duration_seconds": 20.0},
            {"clip_id": "clip_002", "filename": media_2.name,
             "source_file": str(media_2), "path": str(media_2),
             "duration_seconds": 10.0},
        ]}},
    })

    _write(steps / "1_04_temporal_index" / "index" / "clip_001.json", {
        "clip_id": "clip_001", "duration": 20.0,
        "speech_regions": [
            {"start": 1.0, "end": 3.0, "text": "we need to find a parking spot",
             "words": [{"word": "parking", "start": 2.0, "end": 2.4}]},
        ],
    })
    _write(steps / "1_04_temporal_index" / "index" / "clip_002.json", {
        "clip_id": "clip_002", "duration": 10.0,
        "speech_regions": [
            {"start": 2.0, "end": 4.0, "text": "okay let's begin the podcast",
             "words": [{"word": "okay", "start": 2.0, "end": 2.3}]},
        ],
    })

    return root, digest_1, digest_2


@pytest.fixture
def offline_index(project, tmp_path, monkeypatch):
    root, digest_1, digest_2 = project

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

    monkeypatch.setattr(footage_query, "_load_embedder", fake_loader)
    index_dir = tmp_path / "index"
    build_index(root, index_dir=index_dir)
    return FootageIndex(root, index_dir=index_dir), digest_1, digest_2


def _write_identity(root: Path, digest: str, speech_start: float, speech_end: float):
    source_memory.write_json(root / digest / source_memory.SLOT_IDENTITY, {
        "content_digest": digest, "status": "measured",
        "faces": [{"track_id": "face_001", "embedding": [1.0] + [0.0] * 511,
                  "spans": [{"start": speech_start, "end": speech_end,
                            "box": [0, 0, 1, 1], "det_score": 0.9}]}],
        "voices": [{"track_id": "voice_001", "embedding": [0.0] * 192,
                   "spans": [[speech_start, speech_end]]}],
        "speech_face_links": [{"t": (speech_start + speech_end) / 2,
                              "face_track": "face_001",
                              "voice_track": "voice_001",
                              "basis": "co-occurrence: voice span + "
                                      "face span overlap"}],
    })


def test_person_filter_keeps_only_that_person_s_clip(offline_index, memory_root):
    idx, digest_1, digest_2 = offline_index
    _write_identity(memory_root, digest_1, 1.0, 3.0)

    results = idx.filter(person="person_001")
    assert len(results) == 1
    assert results[0]["clip_id"] == "clip_001"

    # A span that does not cover the segment's time range does not match
    # it: 10.0-10.2s is nowhere near the 1.0-3.0s speech segment.
    _write_identity(memory_root, digest_1, 10.0, 10.2)
    assert idx.filter(person="person_001") == []


def test_person_filter_unknown_name_raises_rather_than_matching_nothing(
        offline_index, memory_root):
    idx, digest_1, digest_2 = offline_index
    _write_identity(memory_root, digest_1, 1.0, 3.0)

    with pytest.raises(RenRefusal):
        idx.filter(person="nobody-by-this-name")


def test_search_report_with_person_filter_answers_who_says_okay(
        offline_index, memory_root):
    """The captain's example from the scout report: 'who says okay' -
    speaker-attributed text search, through the real `search_report`
    path (text match + person filter), not just `filter` alone."""
    idx, digest_1, digest_2 = offline_index
    _write_identity(memory_root, digest_2, 2.0, 4.0)

    report = idx.search_report("okay", mode="lexical",
                               filters={"person": "person_001"})
    assert report["results"]
    assert report["results"][0]["clip_id"] == "clip_002"
