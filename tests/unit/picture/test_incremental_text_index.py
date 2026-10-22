"""The text index re-embeds only rows whose words changed (C02).

A transcript correction must update only the corrected records and
their necessary index metadata - not re-embed the project.  Every
test builds its project under `tmp_path`; no test reads a real
project (§8), downloads a model, or runs a measurement the brief
did not ask for.
"""
import json
from pathlib import Path

import numpy as np

from library.tools.analysis import footage_query
from library.tools.analysis.footage_query import (
    FootageIndex,
    build_index,
    plan_text_reuse,
    segment_stable_id,
    segment_text_cache_key,
    segment_text_hash,
)


def _write(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _project(root: Path) -> Path:
    """Two clips: clip_001 speaks twice on camera, clip_002 is scenery."""
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
        "clip_id": "clip_001", "duration": 20.0,
        "speech_regions": [
            {"start": 1.0, "end": 3.0, "text": "we need to find a parking spot",
             "words": [{"word": "parking", "start": 2.0, "end": 2.4}]},
            {"start": 6.0, "end": 7.5, "text": "the pollen is terrible today",
             "words": [{"word": "pollen", "start": 6.2, "end": 6.6}]},
        ],
        "speech_activity": {"sample_rate_hz": 1, "values": [0.0] * 20},
        "motion_energy": {"sample_rate_hz": 1, "values": [0.5] * 20},
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
        "actions": [], "objects": [],
        "assessment": {"content_type": "person_talking_to_camera"},
        "analysis_metadata": {"pipeline_version": "v3"},
    })
    _write(steps / "1_03_semantic_analysis" / "clip_profile_IMG_0002_v3.json", {
        "clip_id": "IMG_0002",
        "file_path": str(root / "raw" / "IMG_0002.MOV"),
        "duration_s": 10.0,
        "scene": [{"start": 0.0, "end": 10.0, "location": "Empty street",
                   "type": "outdoor", "lighting": "Overcast",
                   "notable_features": []}],
        "camera": [{"start": 0, "end": 10, "mode": "mounted",
                    "framing": "wide", "stability": "stable",
                    "movement": "static"}],
        "actions": [], "objects": [],
        "assessment": {"content_type": "scenery"},
        "analysis_metadata": {"pipeline_version": "v3"},
    })
    return root


class _CountingStub:
    """Deterministic stand-in embedder that records what it was asked."""

    def __init__(self):
        self.calls: list = []

    def __call__(self):
        def encode(texts):
            self.calls.append(list(texts))
            rows = []
            for text in texts:
                vector = np.zeros(26, dtype="float32")
                for char in (text or "").lower():
                    if "a" <= char <= "z":
                        vector[ord(char) - 97] += 1.0
                norm = np.linalg.norm(vector) or 1.0
                rows.append(vector / norm)
            return (np.vstack(rows) if rows
                    else np.zeros((0, 26), dtype="float32"))

        return encode, "test-stub"


def _correct_first_utterance(project: Path, text: str):
    path = (project / "pipeline_output" / "steps" / "1_04_temporal_index"
            / "index" / "clip_001.json")
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["speech_regions"][0]["text"] = text
    path.write_text(json.dumps(doc), encoding="utf-8")


def test_a_transcript_correction_reembeds_only_the_corrected_row(
        tmp_path, monkeypatch):
    project = _project(tmp_path / "proj")
    stub = _CountingStub()
    monkeypatch.setattr(footage_query, "load_embedder", stub)
    index_dir = tmp_path / "index"
    first = build_index(project, index_dir=index_dir)
    embedded_first = sum(len(call) for call in stub.calls)
    assert embedded_first == first["segment_count"] > 0

    _correct_first_utterance(project, "we need to find a parking garage")
    stub.calls.clear()
    second = build_index(project, index_dir=index_dir)

    # Two rows carry the corrected words: the utterance itself and the
    # chunk window joining it. Both changed, so both re-embed; every
    # other row is reused untouched.
    assert second["incremental"] is True
    assert second["segments_reembedded"] == 2
    embedded = [t for call in stub.calls for t in call]
    assert len(embedded) == 2
    assert all("parking garage" in t for t in embedded)
    assert second["segments_reused"] == first["segment_count"] - 2
    assert second["segments_removed"] == 0


def test_a_rebuild_with_no_changes_embeds_nothing_and_answers_identically(
        tmp_path, monkeypatch):
    project = _project(tmp_path / "proj")
    _stub = _CountingStub()
    monkeypatch.setattr(footage_query, "load_embedder", _stub)
    index_dir = tmp_path / "index"
    build_index(project, index_dir=index_dir)
    before = FootageIndex(project, index_dir=index_dir).search_report(
        "parking", top_k=5)

    _stub = _CountingStub()
    monkeypatch.setattr(footage_query, "load_embedder", _stub)
    second = build_index(project, index_dir=index_dir)
    after = FootageIndex(project, index_dir=index_dir).search_report(
        "parking", top_k=5)

    assert second["incremental"] is True
    assert second["segments_reembedded"] == 0
    assert second["segments_removed"] == 0
    for key in ("results", "weak"):
        assert [(r["segment_id"], round(float(r.get("dense_score", 0)), 6),
                 round(float(r.get("lexical_score", 0)), 6))
                for r in after[key]] == [
            (r["segment_id"], round(float(r.get("dense_score", 0)), 6),
             round(float(r.get("lexical_score", 0)), 6))
            for r in before[key]]


def test_a_removed_clip_drops_its_rows_and_embeds_nothing(
        tmp_path, monkeypatch):
    project = _project(tmp_path / "proj")
    _stub = _CountingStub()
    monkeypatch.setattr(footage_query, "load_embedder", _stub)
    index_dir = tmp_path / "index"
    first = build_index(project, index_dir=index_dir)
    payload = json.loads((index_dir / "footage_index.json").read_text(
        encoding="utf-8"))
    dropped = [r for r in payload["segments"]
               if r["clip_id"] == "clip_002"]
    assert dropped

    state_path = project / "pipeline_data.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["step_outputs"]["catalog"]["clip_catalog"] = [
        c for c in state["step_outputs"]["catalog"]["clip_catalog"]
        if c["clip_id"] != "clip_002"]
    state_path.write_text(json.dumps(state), encoding="utf-8")

    second = build_index(project, index_dir=index_dir)
    payload = json.loads((index_dir / "footage_index.json").read_text(
        encoding="utf-8"))
    assert second["incremental"] is True
    assert second["segments_reembedded"] == 0
    assert second["segments_removed"] == len(dropped)
    assert second["segment_count"] == first["segment_count"] - len(dropped)
    assert all(r["clip_id"] != "clip_002" for r in payload["segments"])
    assert FootageIndex(project, index_dir=index_dir).matrix.shape[0] == len(
        payload["segments"])


def test_a_partial_clip_update_leaves_unrelated_rows_byte_identical(
        tmp_path, monkeypatch):
    project = _project(tmp_path / "proj")
    _stub = _CountingStub()
    monkeypatch.setattr(footage_query, "load_embedder", _stub)
    index_dir = tmp_path / "index"
    build_index(project, index_dir=index_dir)
    payload = json.loads((index_dir / "footage_index.json").read_text(
        encoding="utf-8"))
    old_kept = [r for r in payload["segments"]
                if r["clip_id"] == "clip_001"]
    old_matrix = np.load(index_dir / "footage_embeddings.npy").copy()
    old_pos = {r["stable_id"]: i for i, r in enumerate(payload["segments"])}

    path = (project / "pipeline_output" / "steps" / "1_04_temporal_index"
            / "index" / "clip_002.json")
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["speech_regions"] = [
        {"start": 1.0, "end": 2.0, "text": "a dog barks loudly outside",
         "words": [{"word": "barks", "start": 1.2, "end": 1.5}]}]
    path.write_text(json.dumps(doc), encoding="utf-8")

    second = build_index(project, index_dir=index_dir,
                         clip_ids=["clip_002"])
    payload = json.loads((index_dir / "footage_index.json").read_text(
        encoding="utf-8"))
    assert second["incremental"] is True
    assert payload["text_index"]["scoped_clips"] == ["clip_002"]
    new_kept = [r for r in payload["segments"]
                if r["clip_id"] == "clip_001"]
    assert new_kept == old_kept
    matrix = np.load(index_dir / "footage_embeddings.npy")
    for row in new_kept:
        assert np.array_equal(matrix[old_pos[row["stable_id"]]],
                              old_matrix[old_pos[row["stable_id"]]])
    assert any(r["clip_id"] == "clip_002" and "barks" in r["text"]
               for r in payload["segments"])


def test_a_pre_incremental_index_rebuilds_fully_and_says_why(
        tmp_path, monkeypatch):
    project = _project(tmp_path / "proj")
    _stub = _CountingStub()
    monkeypatch.setattr(footage_query, "load_embedder", _stub)
    index_dir = tmp_path / "index"
    first = build_index(project, index_dir=index_dir)
    payload_path = index_dir / "footage_index.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    for row in payload["segments"]:
        row.pop("stable_id", None)
        row.pop("text_hash", None)
        row.pop("text_cache_key", None)
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    stub = _CountingStub()
    monkeypatch.setattr(footage_query, "load_embedder", stub)
    second = build_index(project, index_dir=index_dir)
    assert second["incremental"] is False
    assert "pre-incremental" in second["incremental_reason"]
    assert (sum(len(call) for call in stub.calls)
            == first["segment_count"])


def test_an_inserted_utterance_reuses_untouched_texts_by_content(
        tmp_path, monkeypatch):
    project = _project(tmp_path / "proj")
    _stub = _CountingStub()
    monkeypatch.setattr(footage_query, "load_embedder", _stub)
    index_dir = tmp_path / "index"
    build_index(project, index_dir=index_dir)

    path = (project / "pipeline_output" / "steps" / "1_04_temporal_index"
            / "index" / "clip_001.json")
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["speech_regions"].insert(0, {
        "start": 0.0, "end": 0.8, "text": "okay rolling now",
        "words": [{"word": "rolling", "start": 0.3, "end": 0.6}]})
    path.write_text(json.dumps(doc), encoding="utf-8")

    second = build_index(project, index_dir=index_dir)
    # The insert renumbers the later #speech#NNN ids, but their words
    # did not change, so they keep their vectors by content match.
    # Re-embedded: the inserted utterance, plus the chunk window whose
    # joined text now contains it.
    assert second["incremental"] is True
    assert second["segments_reembedded"] == 2
    assert second["segments_removed"] == 0


def test_text_cache_keys_follow_the_c05_components():
    same_a = segment_text_cache_key(
        clip_digest="digest-1", clip_id="clip_001",
        text="parking spot", embed_model="model-x")
    same_b = segment_text_cache_key(
        clip_digest="digest-1", clip_id="clip_001",
        text="parking spot", embed_model="model-x")
    assert same_a == same_b
    assert (segment_text_cache_key(
        clip_digest="digest-1", clip_id="clip_001",
        text="parking garage", embed_model="model-x") != same_a)
    assert (segment_text_cache_key(
        clip_digest="digest-1", clip_id="clip_001",
        text="parking spot", embed_model="model-y") != same_a)
    assert (segment_text_cache_key(
        clip_digest="digest-2", clip_id="clip_001",
        text="parking spot", embed_model="model-x") != same_a)
    assert len(segment_text_hash("parking spot")) == 64
    assert segment_stable_id({"clip_id": "clip_001", "kind": "speech",
                              "start": 1.0, "end": 3.0}) == (
        segment_stable_id({"clip_id": "clip_001", "kind": "speech",
                           "start": 1.0, "end": 3.0}))


def test_plan_text_reuse_never_claims_one_stored_row_twice():
    old = [{"stable_id": "clip_001#speech#1.000-2.000",
            "text_hash": "h-same"},
           {"stable_id": "clip_001#speech#1.000-2.000",
            "text_hash": "h-same"}]
    new = [{"stable_id": "clip_001#speech#1.000-2.000", "text_hash": "h-same"},
           {"stable_id": "clip_001#speech#9.000-9.500", "text_hash": "h-same"}]
    reused, to_embed = plan_text_reuse(old, new)
    assert sorted(reused.values()) == [0, 1]
    assert to_embed == []
