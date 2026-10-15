from pathlib import Path

from library.capabilities.temporal import index as step
from library.tools import source_memory


def test_audio_preparation_batches_identical_source_by_digest(monkeypatch, tmp_path):
    source = tmp_path / "camera.mov"
    source.write_bytes(b"same source bytes")
    alias = tmp_path / "alias.mov"
    alias.write_bytes(source.read_bytes())
    index_dir = tmp_path / "indices"
    index_dir.mkdir()
    audio_dir = tmp_path / "audio"

    class Layout:
        def write_dir(self, _area, step=None):
            audio_dir.mkdir(exist_ok=True)
            return audio_dir

    monkeypatch.setattr(step, "_load_cached_index", lambda *a, **k: None)
    decoded = []

    def fake_audio(path, output_dir, clip_id=None):
        decoded.append((path, clip_id))
        wav = Path(output_dir) / f"{clip_id}.wav"
        wav.write_bytes(b"pcm")
        return str(wav)

    monkeypatch.setattr(step, "clip_audio_16k", fake_audio)
    monkeypatch.setattr(step, "detect_onsets_with_strength",
                        lambda path: ([0.25], [0.75]))
    monkeypatch.setattr(step, "transcribe_clips_batched",
                        lambda requests, **kwargs: {
                            request["key"]: ([{"start": 0.0}], {"text": "same"})
                            for request in requests
                        })

    prepared = step._pretranscribe_misses([
        ("clip_a", str(source), str(index_dir / "a.json"), True),
        ("clip_b", str(alias), str(index_dir / "b.json"), True),
    ], Layout(), "large-v3", "en")

    assert len(decoded) == 1
    assert prepared["clip_a"][0].endswith("/clip_a.wav")
    assert prepared["clip_b"][0].endswith("/clip_b.wav")
    assert Path(prepared["clip_a"][0]).read_bytes() == b"pcm"
    assert Path(prepared["clip_b"][0]).read_bytes() == b"pcm"
    assert prepared["clip_a"][1:] == prepared["clip_b"][1:]
    assert prepared["clip_a"][1:3] == ([0.25], [0.75])
    assert prepared.resource_counters == {
        "audio_decode_requests": 1,
        "audio_unique_digests": 1,
        "audio_duplicate_decodes_avoided": 1,
    }


def test_frame_decode_reuses_digest_across_source_paths(
        monkeypatch, tmp_path):
    first = tmp_path / "first.mov"
    first.write_bytes(b"same frame source")
    second = tmp_path / "second.mov"
    second.write_bytes(first.read_bytes())
    decode_calls = []

    def fake_extract(path, output_dir, width, use_hwaccel):
        decode_calls.append(path)
        frame = Path(output_dir) / "frame_000000.jpg"
        frame.write_bytes(b"frame parity")
        return [(str(frame), 0.0)], False

    monkeypatch.setattr(source_memory, "extract_iframes", fake_extract)
    digest = source_memory.footage_identity.fingerprint(str(first))[
        "content_digest"]
    baseline = source_memory.build_frames(str(first), digest, root=tmp_path / "m2")
    baseline_doc = source_memory.read_m2(digest, tmp_path / "m2")
    reused = source_memory.build_frames(str(second), digest, root=tmp_path / "m2")
    reused_doc = source_memory.read_m2(digest, tmp_path / "m2")

    assert baseline["reused"] is False
    assert reused["reused"] is True
    assert len(decode_calls) == 1
    assert reused_doc["frames"] == baseline_doc["frames"]
    assert reused_doc["instrument"] == baseline_doc["instrument"]
