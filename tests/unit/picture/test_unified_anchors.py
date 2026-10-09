"""The unified five-anchor index: one EG2 space, linked windows, honest abstain.

Media comes from ffmpeg-generated test clips (real cutting path); only
the encoders are stubbed.  No test reads a real project or loads a
model.  The default text path is untouched - every test here reaches
the unified lane explicitly.
"""
import json
import subprocess
import sys
import textwrap
from pathlib import Path

import numpy as np
import pytest

from library.tools.analysis import unified_anchors as ua
from library.tools.analysis.unified_anchors import (
    UNIFIED_ANCHORS,
    UNIFIED_AUDIO_RATE,
    UNIFIED_BATCH_WINDOWS,
    UNIFIED_DESCRIPTORS_FILE,
    UNIFIED_WINDOW_S,
    UnifiedIndex,
    build_unified,
    extract_window_audio,
)
from library.tools.ren_refusal import RenRefusal


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _clip(path, seconds):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         f"testsrc=duration={seconds}:size=64x64:rate=1",
         "-pix_fmt", "yuv420p", str(path)],
        check=True)


@pytest.fixture
def media_project(tmp_path):
    root = tmp_path / "proj"
    _clip(root / "raw" / "A.mp4", 16)
    _write(root / "pipeline_data.json", {
        "step_outputs": {"catalog": {"clip_catalog": [
            {"clip_id": "clip_A", "filename": "A.mp4",
             "source_file": str(root / "raw" / "A.mp4"),
             "path": str(root / "raw" / "A.mp4"),
             "duration_seconds": 16.0},
            {"clip_id": "clip_gone", "filename": "GONE.mp4",
             "source_file": str(root / "raw" / "GONE.mp4"),
             "path": str(root / "raw" / "GONE.mp4"),
             "duration_seconds": 8.0},
        ]}},
    })
    return root


def _fake_256(items):
    n = len(items)
    base = np.tile(np.arange(256, dtype="float32"), (n, 1))
    return base / np.linalg.norm(base, axis=1, keepdims=True)


def _install_encoders(monkeypatch, audio_ok=True):
    def fake_audio(items):
        return _fake_256(items)

    def extract(source, start, end):
        if "GONE" in str(source):
            raise RenRefusal("no media", "fixture", "stay unembedded")
        return [np.zeros((8, 8, 3), dtype="uint8")] * 8

    def audio(source, start, end):
        if not audio_ok or "GONE" in str(source):
            raise RenRefusal("no audio", "fixture", "stay unembedded")
        return {"array": np.zeros(160, dtype="float32"),
                "sampling_rate": UNIFIED_AUDIO_RATE}

    monkeypatch.setattr(ua, "extract_chunk_frames", extract)
    monkeypatch.setattr(ua, "extract_window_audio", audio)
    monkeypatch.setattr(ua, "load_eg2_video_embedder",
                        lambda: (_fake_256, "video-test"))
    monkeypatch.setattr(ua, "load_eg2_image_embedder",
                        lambda: (_fake_256, "image-test"))
    monkeypatch.setattr(ua, "load_eg2_audio_embedder",
                        lambda: (fake_audio, "audio-test"))
    monkeypatch.setattr(ua, "load_eg2_text_embedder",
                        lambda: (lambda texts, **k: _fake_256(texts), "text-test"))


def test_five_anchors_in_one_window_grid():
    assert UNIFIED_ANCHORS == ("video", "still", "audio", "description", "transcript")
    assert UNIFIED_WINDOW_S == 8.0
    assert UNIFIED_BATCH_WINDOWS == 4
    from library.tools.analysis.footage_segments import CHUNK_S, cut_chunks
    assert UNIFIED_WINDOW_S == CHUNK_S
    assert ua.cut_video_windows(20.0) == cut_chunks(20.0)


def test_audio_extraction_refuses_a_missing_file(tmp_path):
    with pytest.raises(RenRefusal):
        extract_window_audio(str(tmp_path / "nope.mp4"), 0.0, 8.0)


def test_build_links_all_five_anchors(media_project, monkeypatch):
    _install_encoders(monkeypatch)
    index_dir = media_project / "uidx"
    stats = build_unified(str(media_project), index_dir=index_dir)
    assert stats["backend"] == "unified-eg2"
    # Two 8 s windows from clip_A; the gone clip is named, never silent.
    assert stats["window_count"] == 2
    assert [u["clip_id"] for u in stats["unified_unembedded"]] == ["clip_gone"]
    payload = json.loads((index_dir / UNIFIED_DESCRIPTORS_FILE).read_text(
        encoding="utf-8"))
    assert payload["anchors"] == list(UNIFIED_ANCHORS)
    assert [d["anchor_id"] for d in payload["descriptors"]] == [
        "clip_A#u#000", "clip_A#u#001"]
    for anchor in ("video", "still", "audio"):
        assert payload["anchor_dimensions"][anchor] == 256
        mat = np.load(index_dir / ua._UNIFIED_MATRIX_FILES[anchor])
        assert mat.shape == (2, 256)
    # No analysis text in this fixture: the text anchors stay absent and
    # say which windows they missed, rather than zero-filling.
    assert payload["anchor_dimensions"]["description"] == 0
    assert len(payload["anchor_unembedded"]["description"]) == 2
    assert not list(index_dir.glob("*.partial.npy"))
    assert not list(index_dir.glob("*.checkpoint.json"))


def test_audio_anchor_names_its_missing_windows(media_project, monkeypatch):
    _install_encoders(monkeypatch, audio_ok=False)
    index_dir = media_project / "uidx-noaudio"
    stats = build_unified(str(media_project), index_dir=index_dir)
    assert stats["anchor_dimensions"]["audio"] == 0
    assert stats["anchor_unembedded_counts"]["audio"] == 2


def test_search_fuses_and_abstains(media_project, monkeypatch):
    _install_encoders(monkeypatch)
    index_dir = media_project / "uidx-search"
    build_unified(str(media_project), index_dir=index_dir)
    idx = UnifiedIndex(str(media_project), index_dir=index_dir)
    # The stub encoder scores every input alike, so calibration parks
    # the floors above any query - the rank path is exercised with an
    # explicit open floor, and the recorded calibration is asserted
    # beside it rather than through it.
    assert idx.payload["anchor_floors"]["video"]["confident"] > 1.0
    found = idx.search_unified("a person pointing at the camera", floor=0.0)
    assert found["backend"] == "unified-eg2"
    assert found["abstained"] is False
    assert len(found["results"]) == 2
    assert found["results"][0]["anchor_id"] == "clip_A#u#000"
    assert "video_score" in found["results"][0]
    # An explicit strict floor abstains honestly and names the near miss.
    missed = idx.search_unified("a person pointing at the camera", floor=2.0)
    assert missed["results"] == []
    assert missed["abstained"] is True
    assert missed["answer_status"] in ("abstained", "not_found")


def test_visual_intent_without_visual_evidence_is_not_analysed(tmp_path, monkeypatch):
    _install_encoders(monkeypatch)
    # No picture evidence anywhere: the visual anchors cannot load and
    # the fixture carries no vision profiles or OCR - but the audio
    # anchor still builds, so the index is real, just not visual.
    monkeypatch.setattr(ua, "load_eg2_video_embedder", lambda: (None, "off"))
    monkeypatch.setattr(ua, "load_eg2_image_embedder", lambda: (None, "off"))
    root = tmp_path / "novis"
    _clip(root / "raw" / "A.mp4", 8)
    _write(root / "pipeline_data.json", {
        "step_outputs": {"catalog": {"clip_catalog": [
            {"clip_id": "clip_A", "filename": "A.mp4",
             "source_file": str(root / "raw" / "A.mp4"),
             "path": str(root / "raw" / "A.mp4"),
             "duration_seconds": 8.0}]}}})
    index_dir = root / "uidx"
    build_unified(str(root), index_dir=index_dir)
    idx = UnifiedIndex(str(root), index_dir=index_dir)
    report = idx.search_unified("show me the wide shot of the parking lot")
    assert report["answer_status"] == "not_analysed"
    assert report["results"] == []


def test_image_query_scores_the_visual_anchors(media_project, monkeypatch):
    import PIL.Image
    _install_encoders(monkeypatch)
    captured = {}

    def fake_image(images):
        captured["n"] = len(images)
        return _fake_256(images)

    monkeypatch.setattr(ua, "load_eg2_image_embedder",
                        lambda: (fake_image, "image-test"))
    index_dir = media_project / "uidx-img"
    build_unified(str(media_project), index_dir=index_dir)
    idx = UnifiedIndex(str(media_project), index_dir=index_dir)
    image = PIL.Image.new("RGB", (8, 8))
    report = idx.search_unified("", query_image=image, floor=0.0)
    assert report["results"]
    assert any(k == "video@image_score" or k == "still@image_score"
               for k in report["results"][0])
    assert captured["n"] == 1


def test_long_build_bounds_batches(media_project):
    project_data = json.loads((media_project / "pipeline_data.json").read_text())
    clips = project_data["step_outputs"]["catalog"]["clip_catalog"]
    clips[:] = clips[:1]
    clips[0]["duration_seconds"] = 3600.0
    _write(media_project / "pipeline_data.json", project_data)
    index_dir = media_project / "long-uidx"
    # The child must import the repo THIS test file runs against: its
    # cwd resolves `library` to whatever tree is first on the path, and
    # on the CI runner that has been a checkout without this module.
    repo_root = str(Path(ua.__file__).resolve().parents[3])
    script = textwrap.dedent("""
        import sys
        sys.path.insert(0, sys.argv[3])
        import numpy as np
        from library.tools.analysis import unified_anchors as ua
        from library.tools.analysis.unified_anchors import build_unified
        # A bad runtime override must not turn streaming back into an
        # all-windows allocation.
        ua.VIDEO_EMBED_BATCH_CHUNKS = 450
        batches = []
        def extract(_source, _start, _end):
            return [np.zeros((8, 8, 3), dtype="uint8")] * 8
        def audio(_source, _start, _end):
            return {"array": np.zeros(16, dtype="float32"), "sampling_rate": 16000}
        def encode(items):
            batches.append(len(items))
            base = np.tile(np.arange(256, dtype="float32"), (len(items), 1))
            return base / np.linalg.norm(base, axis=1, keepdims=True)
        ua.extract_chunk_frames = extract
        ua.extract_window_audio = audio
        ua.load_eg2_video_embedder = lambda: (encode, "t")
        ua.load_eg2_image_embedder = lambda: (encode, "t")
        ua.load_eg2_audio_embedder = lambda: (encode, "t")
        ua.load_eg2_text_embedder = lambda: (lambda texts, **k: encode(texts), "t")
        stats = build_unified(sys.argv[1], index_dir=sys.argv[2])
        print(f"windows={stats['window_count']} batch_max={max(batches)}")
    """)
    result = subprocess.run(
        [sys.executable, "-c", script, str(media_project), str(index_dir),
         repo_root],
        capture_output=True, text=True, encoding="utf-8", check=False)
    assert result.returncode == 0, (
        f"unified long build exited {result.returncode}\n"
        f"stdout: {result.stdout[-2000:]}\n"
        f"stderr: {result.stderr[-2000:]}")
    print(result.stdout.strip())
    line = result.stdout.strip().splitlines()[-1]
    metrics = dict(part.split("=") for part in line.split())
    assert int(metrics["windows"]) == 450
    assert int(metrics["batch_max"]) <= UNIFIED_BATCH_WINDOWS


def test_retention_is_per_anchor_not_max_floor(tmp_path, monkeypatch):
    """A still hit below the video floor is still evidence.

    The bakeoff caught max-over-anchors retention silencing every
    non-transcript hit under the strict transcript floor; each anchor
    is judged by its own calibrated floor.
    """
    from library.tools.analysis import unified_anchors as ua_mod

    def stub_text(vec):
        def load():
            return (lambda texts, **k: np.array([vec], dtype="float32")), "t"
        return load

    monkeypatch.setattr(ua, "load_eg2_text_embedder",
                        stub_text([1.0] + [0.0] * 255))
    root = tmp_path / "pa"
    index_dir = root / "uidx"
    index_dir.mkdir(parents=True, exist_ok=True)
    descriptors = [
        {"anchor_id": "clip_A#u#000", "clip_id": "clip_A", "filename": "A.mp4",
         "start": 0.0, "end": 8.0, "description_text": "", "transcript_text": ""},
        {"anchor_id": "clip_A#u#001", "clip_id": "clip_A", "filename": "A.mp4",
         "start": 8.0, "end": 16.0, "description_text": "", "transcript_text": ""},
    ]
    payload = {
        "project_folder": str(root), "backend": "unified-eg2",
        "embed_model": "google/embeddinggemma-2", "embed_dimension": 256,
        "window_seconds": 8.0, "anchors": list(UNIFIED_ANCHORS),
        "anchor_backends": {}, "anchor_dimensions": {"video": 256, "still": 256},
        "window_count": 2, "coverage": {"per_clip": {}},
        "descriptors": descriptors,
        "anchor_rows": {"video": ["clip_A#u#000", "clip_A#u#001"],
                        "still": ["clip_A#u#000", "clip_A#u#001"]},
        "unified_unembedded": [], "anchor_unembedded": {},
        "anchor_floors": {
            "video": {"confident": 0.75, "weak": 0.60, "source": "t", "probes": {}},
            "still": {"confident": 0.45, "weak": 0.30, "source": "t", "probes": {}},
        },
        "ocr_texts": {}, "ocr_methods": {},
    }
    (index_dir / UNIFIED_DESCRIPTORS_FILE).write_text(
        json.dumps(payload), encoding="utf-8")
    # Window 0 is a video hit, window 1 only a still hit - under a
    # max-floor (0.75) window 1 would be refused.
    np.save(index_dir / ua_mod._UNIFIED_MATRIX_FILES["video"],
            np.array([[0.8] + [0.0] * 255, [0.5] + [0.0] * 255], dtype="float32"))
    np.save(index_dir / ua_mod._UNIFIED_MATRIX_FILES["still"],
            np.array([[0.4] + [0.0] * 255, [0.8] + [0.0] * 255], dtype="float32"))
    idx = UnifiedIndex(str(root), index_dir=index_dir)
    report = idx.search_unified("a person pointing at the camera")
    assert [h["anchor_id"] for h in report["results"]] == [
        "clip_A#u#000", "clip_A#u#001"]


def test_keep_finite_names_dropped_windows():
    from library.tools.analysis.unified_anchors import _keep_finite
    owners = [{"anchor_id": f"clip_A#u#{i:03d}", "clip_id": "clip_A"} for i in range(3)]
    vectors = np.ones((3, 256), dtype="float32")
    vectors[1, 7] = np.nan
    missing: dict = {"video": []}
    kept_vectors, kept_owners = _keep_finite(vectors, owners, "video", missing)
    assert kept_vectors.shape == (2, 256)
    assert [o["anchor_id"] for o in kept_owners] == ["clip_A#u#000", "clip_A#u#002"]
    assert missing["video"][0]["anchor_id"] == "clip_A#u#001"
    assert "non-finite" in missing["video"][0]["reason"]


def test_video_chunk_build_drops_non_finite_rows(media_project, monkeypatch):
    """The shared chunk lane never stores an encoder NaN either."""
    from library.tools.analysis import footage_query

    def extract(source, start, end):
        if "GONE" in str(source):
            raise RenRefusal("no media", "fixture", "stay unembedded")
        return [np.zeros((8, 8, 3), dtype="uint8")] * 8

    calls = []

    def encode(stacks):
        calls.append(len(stacks))
        vectors = _fake_256(stacks)
        if len(calls) == 1:
            vectors[0, 0] = np.nan
        return vectors

    monkeypatch.setattr(footage_query, "extract_chunk_frames", extract)
    monkeypatch.setattr(footage_query, "load_eg2_video_embedder",
                        lambda: (encode, "eg2-video-test"))
    monkeypatch.setattr(footage_query, "load_eg2_text_embedder",
                        lambda: (None, "fixture"))
    monkeypatch.setattr(footage_query, "load_embedder",
                        lambda: (None, "fixture"))
    index_dir = media_project / "nan-idx"
    stats = footage_query.build_index(str(media_project), index_dir=index_dir,
                                      video_chunks=True)
    mat = np.load(index_dir / footage_query.CHUNK_EMBEDDINGS_FILE)
    assert np.isfinite(mat).all()
    assert stats["video_chunk_count"] == 1
    assert any("non-finite" in u["reason"] for u in stats["video_unembedded"])


def test_cli_search_unified_choice(media_project, monkeypatch):
    from library.tools.analysis import footage_query
    _install_encoders(monkeypatch)
    monkeypatch.setattr(footage_query, "load_embedder", lambda: (None, "off"))
    index_dir = str(media_project / "cli-uidx")
    assert footage_query.main(["build", str(media_project), "--index-dir",
                               index_dir, "--unified-anchors"]) == 0
    idx = UnifiedIndex(str(media_project), index_dir=index_dir)
    assert idx.summary()["backend"] == "unified-eg2"
