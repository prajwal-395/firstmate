"""C01: per-frame CLIP embedding reuse across index (re)builds.

Every test builds its project and its source memory under `tmp_path`.
No test reads a real project, loads a model, or touches torch: the
encoder is a stub that derives deterministic vectors from the frame
file's bytes, so a reused row is bitwise what a fresh encode wrote.

The defects, each named by the test that would have caught it:

* a rebuild re-encodes every frame (the lane's current problem);
* adding one clip re-embeds the unchanged clips with it;
* a frame whose bytes changed is served its old vector;
* a moved cache key is served as current instead of re-encoded;
* a corrupt cache file fails the build instead of missing;
* a build with no model and nothing new still refuses the index it
  could have assembled from the cache;
* the heavy-work grant is taken on miss builds and not on full hits.
"""

import contextlib
import json
import os
from pathlib import Path

import numpy as np
import pytest

from library.tools import footage_identity, source_memory
from library.tools.analysis import footage_frames
from library.tools.analysis import frame_embedding_cache
from library.tools.ren_refusal import RenRefusal

DIM = 8


def _write(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def memory_root(tmp_path, monkeypatch):
    root = tmp_path / "memory"
    monkeypatch.setenv(source_memory.MEMORY_ROOT_ENV, str(root))
    return root


def _media(tmp_path: Path, name: str, seed: bytes) -> Path:
    path = tmp_path / "raw" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(seed * (256 * 1024 // len(seed) + 1))
    return path


def _m2(memory_root: Path, digest: str, size_bytes: int,
        frame_count: int = 3, seed: bytes = b"frame") -> Path:
    """A fresh M2 sample: the index plus frame files with distinct bytes."""
    digest_dir = Path(memory_root) / digest
    frames_dir = digest_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for number in range(frame_count):
        name = f"frame_{number + 1:06d}.jpg"
        (frames_dir / name).write_bytes(
            seed + b"|" + digest.encode("ascii")[:8] + b"|" + str(number).encode("ascii"))
        rows.append({"file": f"frames/{name}", "t": round(number * 10.0 + 0.083, 3),
                     "pts": round(number * 10.0 + 0.083, 6),
                     "frame_index": number * 250 + 2})
    _write(digest_dir / source_memory.SLOT_FRAMES_INDEX, {
        "content_digest": digest, "size_bytes": size_bytes,
        "status": source_memory.M2_STATUS_SAMPLED,
        "rate_hz_nominal": source_memory.M2_RATE_HZ_NOMINAL,
        "frame_count": len(rows), "frames": rows})
    return digest_dir


@pytest.fixture
def two_clip_project(tmp_path, memory_root):
    """One project, two clips, each with an M2 sample in the memory root."""
    root = tmp_path / "proj"
    media_1 = _media(tmp_path, "clip-a.mov", b"clip-one-bytes")
    media_2 = _media(tmp_path, "clip-b.mov", b"clip-two-bytes")
    live_1 = footage_identity.fingerprint(str(media_1))
    live_2 = footage_identity.fingerprint(str(media_2))
    _m2(memory_root, live_1["content_digest"], live_1["size_bytes"])
    _m2(memory_root, live_2["content_digest"], live_2["size_bytes"],
        seed=b"other-frames")
    _write(root / "pipeline_data.json", {
        "capability_outputs": {"footage.catalog": {"clip_catalog": [
            {"clip_id": "clip_001", "filename": media_1.name,
             "source_file": str(media_1), "path": str(media_1),
             "duration_seconds": 25.0},
            {"clip_id": "clip_002", "filename": media_2.name,
             "source_file": str(media_2), "path": str(media_2),
             "duration_seconds": 25.0},
        ]}},
    })
    return root


class _StubClip:
    """The encoder without the model: vectors from the file's bytes."""

    def __init__(self):
        self.calls: list = []

    def encode(self, paths):
        self.calls.append(list(paths))
        rows = []
        for path in paths:
            digest = footage_identity.fingerprint(path)["content_digest"]
            rng = np.random.RandomState(int(digest[:8], 16))
            vector = rng.rand(DIM).astype("float32")
            rows.append(vector / np.linalg.norm(vector))
        return np.vstack(rows) if rows else np.zeros((0, DIM), dtype="float32")

    def loader(self, processor=None):
        return self.encode, None, "test-stub"


@pytest.fixture
def stub_clip(monkeypatch):
    stub = _StubClip()
    monkeypatch.setattr(footage_frames, "_load_clip", stub.loader)
    monkeypatch.setattr(footage_frames, "_load_processor",
                        lambda: (None, "test has no processor"))
    monkeypatch.setattr(footage_frames, "heavy_work_lock",
                        lambda *args, **kwargs: contextlib.nullcontext())
    return stub


def _build(project, index_dir, **kwargs):
    return footage_frames.build_frame_index(str(project), index_dir=str(index_dir),
                                            **kwargs)


def _matrix(index_dir):
    return np.load(Path(index_dir) / footage_frames.FRAME_EMBEDDINGS_FILE)


# ── the reuse ──────────────────────────────────────────────────────


def test_second_build_encodes_nothing(two_clip_project, tmp_path, stub_clip):
    """A rebuild with nothing new about it encodes nothing.

    The defect: rebuilding a frame index re-encoded every collected
    frame, so every rebuild paid the full CLIP cost again.
    """
    first, second = tmp_path / "idx1", tmp_path / "idx2"
    stats_1 = _build(two_clip_project, first)
    assert stats_1["frame_count"] == 6
    assert stats_1["frames_embedded"] == 6
    assert stats_1["frames_reused"] == 0
    encoded_first = len(stub_clip.calls)

    stats_2 = _build(two_clip_project, second)
    assert stats_2["frames_embedded"] == 0
    assert stats_2["frames_reused"] == 6
    assert len(stub_clip.calls) == encoded_first
    assert np.array_equal(_matrix(first), _matrix(second))


def test_added_clip_encodes_only_its_frames(two_clip_project, tmp_path, stub_clip, memory_root):
    """Adding one clip does not re-embed the unchanged clips.

    The acceptance: the new digest's frames reach the encoder and no
    other frame does; the unchanged rows are bitwise what the first
    build wrote.
    """
    first = tmp_path / "idx1"
    stats_1 = _build(two_clip_project, first)
    before = _matrix(first)

    media_3 = _media(tmp_path, "clip-c.mov", b"clip-three-bytes")
    live_3 = footage_identity.fingerprint(str(media_3))
    _m2(memory_root, live_3["content_digest"], live_3["size_bytes"],
        seed=b"third-frames")
    state = json.loads((two_clip_project / "pipeline_data.json").read_text(encoding="utf-8"))
    state["capability_outputs"]["footage.catalog"]["clip_catalog"].append(
        {"clip_id": "clip_003", "filename": media_3.name,
         "source_file": str(media_3), "path": str(media_3),
         "duration_seconds": 25.0})
    (two_clip_project / "pipeline_data.json").write_text(json.dumps(state), encoding="utf-8")

    stub_clip.calls.clear()
    second = tmp_path / "idx2"
    stats_2 = _build(two_clip_project, second)
    assert stats_2["frame_count"] == 9
    assert stats_2["frames_embedded"] == 3
    assert stats_2["frames_reused"] == 6
    encoded_paths = [path for call in stub_clip.calls for path in call]
    assert len(encoded_paths) == 3
    assert all(path.startswith(str(Path(memory_root) / live_3["content_digest"]))
                   for path in encoded_paths)
    after = _matrix(second)
    assert np.array_equal(after[:6], before)


def test_changed_frame_bytes_reencode_that_frame(two_clip_project, tmp_path, stub_clip,
                                                 memory_root):
    """A frame whose bytes changed misses even though its time did not move.

    The defect: an identity of timestamps alone would serve the old
    pixels' vector for the new pixels.  The identity carries the
    content hash, so a re-extracted frame re-encodes.
    """
    first = tmp_path / "idx1"
    _build(two_clip_project, first)
    live_1 = footage_identity.fingerprint(str(tmp_path / "raw" / "clip-a.mov"))
    victim = (Path(memory_root) / live_1["content_digest"]
              / "frames" / "frame_000002.jpg")
    victim.write_bytes(b"re-extracted pixels, same second")

    stub_clip.calls.clear()
    second = tmp_path / "idx2"
    stats = _build(two_clip_project, second)
    assert stats["frames_embedded"] == 1
    assert stats["frames_reused"] == 5
    assert [path for call in stub_clip.calls for path in call] == [str(victim)]


def test_stale_key_reencodes_and_names_what_moved(two_clip_project, tmp_path, stub_clip,
                                                  memory_root):
    """A moved key component is a MISS, never a false hit.

    The defect: vectors filed under another model or recipe served as
    current.  Tampering the filed key's model re-encodes every row of
    that source and reports the component that moved.
    """
    first = tmp_path / "idx1"
    _build(two_clip_project, first)
    live_1 = footage_identity.fingerprint(str(tmp_path / "raw" / "clip-a.mov"))
    cache = (Path(memory_root) / live_1["content_digest"]
             / frame_embedding_cache.CACHE_FILENAME)
    raw = dict(np.load(cache, allow_pickle=False))
    provenance = json.loads(str(raw["__provenance__"]))
    provenance["key"]["model"] = "another-model@snapshot:0"
    np.savez_compressed(
        cache, embeddings=raw["embeddings"], identities=raw["identities"],
        __provenance__=np.array(frame_embedding_cache.cache_keys.canonical_json(
            provenance)))

    stub_clip.calls.clear()
    second = tmp_path / "idx2"
    stats = _build(two_clip_project, second)
    assert stats["frames_embedded"] == 3
    assert stats["frames_reused"] == 3
    assert stats["embed_invalidated"] == [{
        "digest": live_1["content_digest"], "status": "stale-key",
        "reason": "key moved: model", "differing": ["model"]}]


def test_corrupt_cache_is_a_miss_not_a_crash(two_clip_project, tmp_path, stub_clip,
                                             memory_root):
    """A half-written cache file degrades to a full encode of that source.

    The defect: a killed build's torn file failing every later build.
    Stores land atomically, but a foreign hand (or a full disk) can
    still tear one, and the build must survive it.
    """
    first = tmp_path / "idx1"
    _build(two_clip_project, first)
    live_1 = footage_identity.fingerprint(str(tmp_path / "raw" / "clip-a.mov"))
    cache = (Path(memory_root) / live_1["content_digest"]
             / frame_embedding_cache.CACHE_FILENAME)
    cache.write_bytes(b"torn halfway through")

    second = tmp_path / "idx2"
    stats = _build(two_clip_project, second)
    assert stats["frames_embedded"] == 3
    assert stats["frames_reused"] == 3
    assert stats["embed_invalidated"][0]["status"] == "corrupt"
    assert np.array_equal(_matrix(first), _matrix(second))


def test_full_hit_builds_without_a_model(two_clip_project, tmp_path, stub_clip,
                                         monkeypatch):
    """A build with nothing new needs no model at all.

    The defect this pins in the other direction: without reuse the
    index could not be rebuilt on a machine without the weights.
    Every row comes from the filed generation, and the payload still
    names the model and dimension.
    """
    first = tmp_path / "idx1"
    _build(two_clip_project, first)
    monkeypatch.setattr(footage_frames, "_load_clip",
                        lambda processor=None: (None, None, "no weights here"))

    second = tmp_path / "idx2"
    stats = _build(two_clip_project, second)
    assert stats["frame_count"] == 6
    assert stats["embed_dimension"] == DIM
    assert np.array_equal(_matrix(first), _matrix(second))
    payload = json.loads((second / footage_frames.FRAME_INDEX_FILE).read_text(
        encoding="utf-8"))
    assert payload["clip_model"] == footage_frames.CLIP_MODEL
    assert payload["embed_dimension"] == DIM


def test_grant_taken_on_miss_not_on_hit(two_clip_project, tmp_path, stub_clip,
                                        monkeypatch):
    """The heavy-work grant covers the encode, and only the encode.

    A full-hit rebuild must not queue behind another lane's hold for
    no reason; a miss build must not load weights outside the grant.
    """
    holds = [0]

    @contextlib.contextmanager
    def recording_lock(*args, **kwargs):
        holds[0] += 1
        yield

    monkeypatch.setattr(footage_frames, "heavy_work_lock", recording_lock)
    _build(two_clip_project, tmp_path / "idx1")
    assert holds[0] == 1
    _build(two_clip_project, tmp_path / "idx2")
    assert holds[0] == 1


# ── the key ────────────────────────────────────────────────────────


def _live_namespace(**overrides):
    fields = {"size": {"shortest_edge": 224},
              "crop_size": {"height": 224, "width": 224},
              "resample": 3,
              "rescale_factor": 1 / 255,
              "image_mean": [0.48145466, 0.4578275, 0.40821073],
              "image_std": [0.26862954, 0.26130258, 0.27577711],
              "do_convert_rgb": True}
    fields.update(overrides)

    class CLIPImageProcessorPil:
        pass

    class CLIPProcessor:
        pass

    inner, outer = CLIPImageProcessorPil(), CLIPProcessor()
    for key, value in fields.items():
        setattr(inner, key, value)
    outer.image_processor = inner
    return outer


def test_live_recipe_pin_accepts_itself():
    """The pin describes the processor it was measured from."""
    assert (frame_embedding_cache.assert_live_recipe(_live_namespace())
            == frame_embedding_cache.PREPROCESSING_RECIPE)


def test_live_recipe_drift_refuses():
    """A moved recipe refuses rather than filing under a lying key.

    The defect: vectors encoded under a new default served as the old
    recipe's.  The encode path measures the live recipe and refuses,
    naming the bump that re-keys the cache.
    """
    with pytest.raises(RenRefusal):
        frame_embedding_cache.assert_live_recipe(
            _live_namespace(image_mean=[0.5, 0.5, 0.5]))


def test_wanted_key_shape():
    """The key carries the four C05 components, and only the digest moves it."""
    one = frame_embedding_cache.wanted_key("digest-a", revision="r1",
                                           transformers="5.8.0")
    same = frame_embedding_cache.wanted_key("digest-a", revision="r1",
                                            transformers="5.8.0")
    assert one.signature() == same.signature()
    assert set(one.to_dict()) == {"source_identity", "model", "configuration",
                                  "analysis_version"}
    other = frame_embedding_cache.wanted_key("digest-b", revision="r1",
                                             transformers="5.8.0")
    assert (frame_embedding_cache.cache_keys.differing_components(
        one.to_dict(), other) == ["source_identity"])
    moved = frame_embedding_cache.wanted_key("digest-a", revision="r2",
                                             transformers="5.8.0")
    assert (frame_embedding_cache.cache_keys.differing_components(
        one.to_dict(), moved) == ["model", "configuration"])


def test_identity_changes_with_bytes():
    """The frame identity names the pixels, not just the second."""
    before = frame_embedding_cache.frame_identity(10.083, 250, "a" * 64)
    assert before != frame_embedding_cache.frame_identity(10.083, 250, "b" * 64)
    assert before != frame_embedding_cache.frame_identity(20.083, 500, "a" * 64)


def test_identity_pair_roundtrip():
    """The prune parses back the slot the identity was filed under."""
    identity = frame_embedding_cache.frame_identity(10.083, 252, "c" * 64)
    assert frame_embedding_cache.identity_pair(identity) == ("10.083", "252")
    assert (frame_embedding_cache.identity_pair(
        frame_embedding_cache.frame_identity(0.083, None, "c" * 64))
            == ("0.083", "-"))


def test_prune_drops_vanished_frames_and_superseded_bytes():
    """A filed generation sheds what the current sample disowns.

    The defect: rows for frames no longer sampled (a shrunk sample)
    and rows superseded by a re-encode (same second, new pixels)
    accumulated forever beside the live rows.
    """
    m2 = [{"t": 0.083, "frame_index": 2},
          {"t": 10.083, "frame_index": 252}]
    keep = frame_embedding_cache.frame_identity(0.083, 2, "a" * 64)
    superseded_old = frame_embedding_cache.frame_identity(10.083, 252, "b" * 64)
    superseded_new = frame_embedding_cache.frame_identity(10.083, 252, "c" * 64)
    vanished = frame_embedding_cache.frame_identity(20.083, 502, "d" * 64)
    rows = {keep: np.ones(DIM, dtype="float32"),
            superseded_old: np.ones(DIM, dtype="float32"),
            vanished: np.ones(DIM, dtype="float32"),
            superseded_new: np.zeros(DIM, dtype="float32")}
    pruned = frame_embedding_cache.prune_rows(rows, m2, fresh={superseded_new})
    assert set(pruned) == {keep, superseded_new}


def _dense_project(tmp_path, memory_root):
    """One clip whose M2 sample runs every 2 s, for cadence changes."""
    root = tmp_path / "dense-proj"
    media = _media(tmp_path, "dense.mov", b"dense-bytes")
    live = footage_identity.fingerprint(str(media))
    digest_dir = Path(memory_root) / live["content_digest"]
    frames_dir = digest_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for number in range(13):
        name = f"frame_{number + 1:06d}.jpg"
        (frames_dir / name).write_bytes(f"dense|{number}".encode("utf-8"))
        rows.append({"file": f"frames/{name}", "t": float(number * 2),
                     "frame_index": number * 50})
    _write(digest_dir / source_memory.SLOT_FRAMES_INDEX, {
        "content_digest": live["content_digest"],
        "size_bytes": live["size_bytes"],
        "status": source_memory.M2_STATUS_SAMPLED,
        "rate_hz_nominal": source_memory.M2_RATE_HZ_NOMINAL,
        "frame_count": len(rows), "frames": rows})
    _write(root / "pipeline_data.json", {
        "capability_outputs": {"footage.catalog": {"clip_catalog": [
            {"clip_id": "clip_001", "filename": media.name,
             "source_file": str(media), "path": str(media),
             "duration_seconds": 25.0}]}}})
    return root


def test_sampling_change_encodes_only_the_delta(tmp_path, memory_root, stub_clip):
    """A new cadence encodes its new frames and reuses the overlap.

    The defect: any selection change (cadence, sampling mode)
    re-encoded the whole source instead of the frames the new
    selection adds.
    """
    project = _dense_project(tmp_path, memory_root)
    first = _build(project, tmp_path / "s1", step_s=10.0)
    assert (first["frame_count"], first["frames_embedded"],
            first["frames_reused"]) == (3, 3, 0)
    stub_clip.calls.clear()
    second = _build(project, tmp_path / "s2", step_s=6.0)
    assert second["frame_count"] == 5
    assert second["frames_embedded"] == 4
    assert second["frames_reused"] == 1


def test_source_replacement_encodes_the_new_bytes_only(
        two_clip_project, tmp_path, stub_clip, memory_root):
    """A replaced source file re-encodes fully; the old cache is untouched.

    The defect: a re-recorded clip served the old bytes' vectors, or
    the rebuild disturbed the generation it did not own.  The digest
    changes with the bytes, so the new source misses everywhere and
    the old generation is never even opened for write.
    """
    first = tmp_path / "idx1"
    _build(two_clip_project, first)
    media_1 = tmp_path / "raw" / "clip-a.mov"
    old_digest = footage_identity.fingerprint(str(media_1))["content_digest"]
    old_cache = Path(memory_root) / old_digest / frame_embedding_cache.CACHE_FILENAME
    old_bytes = old_cache.read_bytes()

    media_1.write_bytes(b"re-recorded-take" * (256 * 1024 // 16 + 1))
    live = footage_identity.fingerprint(str(media_1))
    assert live["content_digest"] != old_digest
    _m2(memory_root, live["content_digest"], live["size_bytes"],
        seed=b"replacement-frames")

    stub_clip.calls.clear()
    second = tmp_path / "idx2"
    stats = _build(two_clip_project, second)
    assert stats["frames_embedded"] == 3
    assert stats["frames_reused"] == 3
    assert old_cache.read_bytes() == old_bytes
    assert (Path(memory_root) / live["content_digest"]
            / frame_embedding_cache.CACHE_FILENAME).exists()
    payload = json.loads((second / footage_frames.FRAME_INDEX_FILE).read_text(
        encoding="utf-8"))
    assert sorted({f["clip_id"] for f in payload["frames"]}) == [
        "clip_001", "clip_002"]


def test_version_bump_invalidates_every_generation(
        two_clip_project, tmp_path, stub_clip, monkeypatch):
    """A lane-version bump (the model-upgrade path) re-encodes everything.

    The defect: a new model served under the old key.  The version is
    half the key, so a bump misses everywhere and names the component
    that moved.
    """
    _build(two_clip_project, tmp_path / "idx1")
    monkeypatch.setattr(frame_embedding_cache, "CACHE_VERSION",
                        "clip-frame-embeddings-v2-test")
    stats = _build(two_clip_project, tmp_path / "idx2")
    assert stats["frames_embedded"] == 6
    assert stats["frames_reused"] == 0
    assert all(row["differing"] == ["analysis_version"]
               for row in stats["embed_invalidated"])
    assert all(row["status"] == "stale-key"
               for row in stats["embed_invalidated"])


def test_rebuild_prunes_rows_for_frames_no_longer_sampled(
        two_clip_project, tmp_path, stub_clip, memory_root):
    """A shrunk sample sheds the dropped frame's row on the next store.

    The defect in §prune_rows, through the build: the row survives
    forever because nothing re-hashes it.  Dropping the M2 row (the
    file stays, so the sample is still present) plus one changed
    frame to force a store must file exactly the live rows.
    """
    _build(two_clip_project, tmp_path / "idx1")
    media_1 = tmp_path / "raw" / "clip-a.mov"
    live_1 = footage_identity.fingerprint(str(media_1))
    digest_dir = Path(memory_root) / live_1["content_digest"]
    doc_path = digest_dir / source_memory.SLOT_FRAMES_INDEX
    doc = json.loads(doc_path.read_text(encoding="utf-8"))
    dropped, doc["frames"] = doc["frames"][-1], doc["frames"][:2]
    doc["frame_count"] = 2
    doc_path.write_text(json.dumps(doc), encoding="utf-8")
    (digest_dir / "frames" / "frame_000002.jpg").write_bytes(b"changed pixels")

    stats = _build(two_clip_project, tmp_path / "idx2")
    assert stats["frames_embedded"] == 1
    wanted = frame_embedding_cache.wanted_key(live_1["content_digest"])
    state = frame_embedding_cache.load(memory_root, live_1["content_digest"],
                                       wanted)
    assert state["status"] == "ready"
    assert len(state["rows"]) == 2
    assert not [identity for identity in state["rows"]
                if f"t={dropped['t']:.3f}" in identity]


def test_store_failure_keeps_the_old_generation(
        two_clip_project, tmp_path, stub_clip, memory_root, monkeypatch):
    """An interrupted store leaves the previous generation readable.

    The defect: a build killed mid-write tearing the file every later
    build reads.  Stores land on a temp file renamed over the old
    generation, so a failure keeps the old rows and litters no temp
    file; the build itself fails loudly rather than filing half.
    """
    _build(two_clip_project, tmp_path / "idx1")
    media_1 = tmp_path / "raw" / "clip-a.mov"
    live_1 = footage_identity.fingerprint(str(media_1))
    digest_dir = Path(memory_root) / live_1["content_digest"]
    cache = digest_dir / frame_embedding_cache.CACHE_FILENAME
    before = cache.read_bytes()
    (digest_dir / "frames" / "frame_000001.jpg").write_bytes(b"new pixels")

    real_replace = os.replace

    def failing_replace(src, dst, *args, **kwargs):
        if os.path.basename(str(dst)) == frame_embedding_cache.CACHE_FILENAME:
            raise OSError("disk full (test)")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", failing_replace)
    with pytest.raises(OSError, match="disk full"):
        _build(two_clip_project, tmp_path / "idx2")
    assert cache.read_bytes() == before
    assert not list(digest_dir.glob("*.tmp"))
