"""The frame-level CLIP prototype: ranges, honesty labels, and scope.

Companion to `test_footage_query_prototype.py` (which owns the stay-unwired
guard - `footage_frames` is in its `PROTOTYPE_MODULES`).  Every test here
names a defect it would catch; no count, existence or snapshot tests.

All CLIP/ffmpeg work is stubbed: a test must not download weights, run a
model, or shell out.  The retrieval arithmetic and the scope gates are the
things under test.
"""

import json
from pathlib import Path

import numpy as np
import pytest

from library.tools.analysis import footage_frames
from library.tools.analysis.footage_frames import FrameIndex


def _write(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def project(tmp_path) -> Path:
    """A two-clip project whose video files really exist (staleness stats them)."""
    root = tmp_path / "proj"
    catalog = []
    for clip_id, name, duration in (("clip_001", "TAKE_001.MOV", 120.0),
                                    ("other_002", "TAKE_002.MOV", 60.0)):
        raw = root / "raw" / name
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_bytes(b"fake-video-bytes")
        catalog.append({"clip_id": clip_id, "filename": name,
                        "source_file": str(raw), "duration_seconds": duration})
    _write(root / "pipeline_data.json", {
        "step_outputs": {"catalog": {"clip_catalog": catalog}},
    })
    return root


@pytest.fixture
def stub_clip(monkeypatch):
    """Deterministic stand-in for CLIP: image row i is one-hot(i).

    Query vectors are plain rows in the same space, so cosine order is the
    order of the query vector's own entries - enough for "higher ranks
    first" and "merging reads the ranking" to hold.
    """
    calls = {"images": 0}

    def fake_loader():
        def encode_images(paths):
            n = len(paths)
            calls["images"] += 1
            dim = max(n, 1)
            mat = np.zeros((n, dim), dtype="float32")
            for i in range(n):
                mat[i, i % dim] = 1.0
            return mat

        def encode_text(texts):
            # Prefers frame 0, then 1, then 2; frame 3+ score ~0.
            dim = 5
            vec = np.array([0.6, 0.5, 0.4, 0.01, 0.0], dtype="float32")
            vec = vec / np.linalg.norm(vec)
            return np.tile(vec[:dim], (len(list(texts)), 1))

        return encode_images, encode_text, "test-stub"

    monkeypatch.setattr(footage_frames, "_load_clip", fake_loader)
    return calls


@pytest.fixture
def stub_extract(monkeypatch):
    """Frame times per clip, no ffmpeg: clip_001 at t=0/10/20/100,
    other_002 at t=5.  The clip comes off the output directory, the way the
    real extractor's one-clip-per-call contract provides it."""
    def fake_extract(video_path, out_pattern, step_s, width):
        clip_id = Path(str(out_pattern).rsplit("%", 1)[0]).parent.name
        times = {"clip_001": [0.0, 10.0, 20.0, 100.0],
                 "other_002": [5.0]}[clip_id]
        parent = Path(str(out_pattern).rsplit("%", 1)[0]).parent
        parent.mkdir(parents=True, exist_ok=True)
        out = []
        for t in times:
            path = parent / f"{clip_id}_t{int(t):06d}.jpg"
            path.write_bytes(b"fake-still")
            out.append((str(path), t))
        return out

    monkeypatch.setattr(footage_frames, "_extract_frames", fake_extract)


@pytest.fixture
def frame_index(project, tmp_path, stub_clip, stub_extract):
    index_dir = tmp_path / "findex"
    footage_frames.build_frame_index(project, index_dir=index_dir)
    return FrameIndex(project, index_dir=index_dir)


# ─── Ranges: the unit a person scrubs ───────────────────────────────


def test_adjacent_hits_merge_and_distant_ones_do_not(frame_index):
    """t=0/10/20 on one clip are one range; t=100 and the other clip are not."""
    report = frame_index.search_ranges("a laptop on a table", top_ranges=5)
    assert report["refused"] is None
    ranges = report["ranges"]
    assert [(r["clip_id"], r["start"], r["end"]) for r in ranges] == [
        ("clip_001", 0.0, 20.0),
        ("clip_001", 100.0, 100.0),
        ("other_002", 5.0, 5.0),
    ], "adjacent pool hits merge per clip; anything else stays split"
    head = ranges[0]
    assert head["n_frames"] == 3
    assert head["timecode"] == "00:00.000-00:20.000"
    assert head["best_score"] == pytest.approx(max(
        h["score"] for h in report["results"] if h["clip_id"] == "clip_001"
        and h["t"] <= 20.0))


def test_a_gap_wider_than_merge_gap_splits(frame_index):
    report = frame_index.search_ranges("a laptop on a table", top_ranges=5,
                                       merge_gap_s=5.0)
    clips = [(r["clip_id"], r["start"], r["end"]) for r in report["ranges"]]
    assert ("clip_001", 0.0, 0.0) in clips
    assert ("clip_001", 10.0, 10.0) in clips, (
        "a 10 s gap with a 5 s merger must not merge")


# ─── Honesty: ranked, unverified, never absence ─────────────────────


def test_every_hit_is_unverified_and_absence_is_never_claimed(frame_index):
    report = frame_index.search_ranges("a laptop on a table")
    assert report["notice"] and "cannot be concluded" in report["notice"]
    for hit in report["results"]:
        assert hit["verified"] is False
    for span in report["ranges"]:
        assert span["verified"] is False


def test_low_scores_still_rank_rather_than_abstain(frame_index, monkeypatch):
    """A floor here would repeat the failure the text floor fixed: an index
    that cannot say "not here" must rank, not go silent."""
    def flat_loader():
        def encode_images(paths):
            return np.zeros((len(paths), 4), dtype="float32")

        def encode_text(texts):
            return np.zeros((len(list(texts)), 5), dtype="float32")

        return encode_images, encode_text, "test-stub"

    monkeypatch.setattr(footage_frames, "_load_clip", flat_loader)
    report = frame_index.search_frames("a birthday cake with candles", top_k=3)
    assert report["refused"] is None
    assert len(report["results"]) == 3, (
        "all-zero scores still return top-k: ranking is the answer")


# ─── Scope: actions belong to the pose/hand lane ────────────────────


@pytest.mark.parametrize("query", [
    "a person covering their mouth with their hand",
    "a person drinking from a cup",
    "a hand gesturing in the foreground",
])
def test_action_queries_refuse_and_name_the_lane(frame_index, query):
    report = frame_index.search_frames(query)
    assert report["results"] == []
    assert report["refused"] is not None
    assert "pose/hand" in report["refused"]["hint"]


def test_action_override_ranks_when_asked(frame_index):
    report = frame_index.search_frames(
        "a person drinking from a cup", include_actions=True)
    assert report["refused"] is None
    assert len(report["results"]) == 5


def test_object_queries_pass_the_gate(frame_index):
    assert footage_frames.action_refusal_match("a laptop on a table") is None
    assert footage_frames.action_refusal_match("two people at a table") is None


# ─── Building: only the index dir, and staleness sees a moved source ──


def test_build_writes_only_into_the_index_dir(project, tmp_path, stub_clip,
                                              stub_extract):
    before = sorted(p.relative_to(project) for p in project.rglob("*")
                    if p.is_file())
    index_dir = tmp_path / "elsewhere"
    stats = footage_frames.build_frame_index(project, index_dir=index_dir)
    after = sorted(p.relative_to(project) for p in project.rglob("*")
                   if p.is_file())
    assert before == after, "building the frame index must not touch the project"
    assert (index_dir / footage_frames.FRAME_INDEX_FILE).exists()
    assert stats["frame_count"] == 5


def test_a_replaced_source_video_reads_as_stale(frame_index, project):
    assert frame_index.staleness()["stale"] is False
    raw = project / "raw" / "TAKE_001.MOV"
    raw.write_bytes(b"fake-video-bytes-CHANGED")
    stale = frame_index.staleness()
    assert stale["stale"] is True
    assert stale["changed"] == ["clip_001"]


# ─── The transformers 5.x boundary ──────────────────────────────────


def test_pooled_unwrap_handles_both_clip_output_shapes():
    """Measured 2026-09-30: `get_*_features` returns BaseModelOutputWithPooling
    from transformers 5.x up, and `.norm` on that raises AttributeError."""

    class _Tensor:
        def norm(self, p=2, dim=-1, keepdim=True):
            return self

        def __truediv__(self, other):
            return "NORMALIZED"

    class _FiveX:
        """5.x shape: no .norm, carries pooler_output."""

    five = _FiveX()
    five.pooler_output = _Tensor()
    assert footage_frames._pooled_clip_features(five) == "NORMALIZED"
    assert footage_frames._pooled_clip_features(_Tensor()) == "NORMALIZED"
