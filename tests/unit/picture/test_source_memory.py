"""The per-source footage memory: staleness, precedence and selection.

Every test builds its project and its memory under `tmp_path`. No
test reaches a real project (§8), runs ffmpeg or voz, or opens
Resolve: digests are computed off small scratch files, and M1
documents are written directly in the shape `build_source` emits.
"""
import json
from pathlib import Path
import pytest
from library.tools import source_memory
from library.tools.analysis import footage_segments
import textwrap
from unittest.mock import patch
from library.tools.analysis import measurement_layers as ml
from library.tools.analysis import vision_pipeline_v3 as vp
import importlib.util
import os
import sys
from types import SimpleNamespace
import numpy as np
from library.tools import footage_identity, person_entity
from library.tools import (
    face_identity_study,
)


@pytest.fixture
def memory_root(tmp_path, monkeypatch):
    """An isolated memory root: nothing touches the machine store."""
    root = tmp_path / "memory"
    monkeypatch.setenv(source_memory.MEMORY_ROOT_ENV, str(root))
    return root


def _write(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _media(tmp_path: Path, name: str, seed: bytes) -> Path:
    """A stand-in source file with stable content (and a real digest)."""
    path = tmp_path / "raw" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    # 3 MiB so first and last mebibyte windows do not overlap.
    path.write_bytes(seed * (3 * 1024 * 1024 // len(seed) + 1))
    return path


def _m1_doc(digest: str, words=("hello", "world")) -> dict:
    utterances = [{
        "start": 1.0, "end": 3.0, "text": " ".join(words),
        "words": [{"word": w, "start": 1.0 + i, "end": 1.5 + i}
                  for i, w in enumerate(words)],
        "confidence": 0.0, "method": "hybrid-mfa",
    }]
    return {
        "content_digest": digest,
        "source_file": "/nowhere/FILE.MXF",
        "status": source_memory.M1_STATUS_TRANSCRIBED,
        "utterance_cut": "hybrid-windows",
        "program_track": {"channel": 1, "basis": "single",
                          "measured_levels_db": {"CH1": -20.0}},
        "utterances": utterances,
        "utterance_count": len(utterances),
        "word_count": len(words),
        "speech_seconds": 2.0,
        "instrument": {"arm": "hybrid", "aligner": "mfa"},
    }


def _m0_doc(path: Path, digest: str, size: int) -> dict:
    return {
        "content_digest": digest, "size_bytes": size,
        "observed_paths": [str(path)], "duration_seconds": 10.0,
        "video_streams": [], "audio_streams": [],
        "measured_levels_db": {"CH1": -20.0},
        "program_track": {"channel": 1, "basis": "single",
                          "measured_levels_db": {"CH1": -20.0}},
        "gop_frames": None,
    }


def _project_with_clip(tmp_path: Path, clip_id: str, media: Path,
                       digest: str) -> Path:
    """A project whose runner record names one clip and its digest."""
    root = tmp_path / "proj"
    _write(root / "pipeline_data.json", {
        "step_outputs": {"catalog": {"clip_catalog": [
            {"clip_id": clip_id, "filename": media.name,
             "source_file": str(media), "path": str(media),
             "duration_seconds": 10.0},
        ]}},
        "source_fingerprints": {
            clip_id: {"path": str(media),
                      "size_bytes": media.stat().st_size,
                      "content_digest": digest},
        },
    })
    return root


# ── Staleness ──────────────────────────────────────────────────────


def test_replaced_footage_is_stale_not_served(tmp_path, memory_root):
    """A file replaced after transcription is never served the old words.

    The memory is keyed by digest, so replaced bytes look up a digest
    directory that was never built (`missing`); a record that
    disagrees with the file it names reads `stale`. Either way the
    previous take's transcript never reaches the timeline - and `ren
    search` never finds words that are no longer in the footage.
    """
    from library.tools import footage_identity

    media = _media(tmp_path, "TAKE.MXF", b"take-one")
    digest = footage_identity.fingerprint(str(media))["content_digest"]
    size = media.stat().st_size
    project = _project_with_clip(tmp_path, "clip_001", media, digest)
    target = memory_root / digest
    source_memory.write_json(target / source_memory.SLOT_SOURCE,
                             _m0_doc(media, digest, size))
    source_memory.write_json(target / source_memory.SLOT_TRANSCRIPT,
                             _m1_doc(digest))

    clip = {"clip_id": "clip_001", "source_file": str(media)}
    doc, status = source_memory.read_m1_for_clip(str(project), clip)
    assert status == "fresh"
    assert doc["word_count"] == 2

    # The file is replaced: same name, other bytes, other length.
    media.write_bytes(b"take-two" * (4 * 1024 * 1024 // len(b"take-two") + 1))
    doc, status = source_memory.read_m1_for_clip(str(project), clip)
    assert (doc, status) == (None, "missing")

    # A record that disagrees with its own file reads stale, not fresh.
    media.write_bytes(b"take-one" * (3 * 1024 * 1024 // len(b"take-one") + 1))
    tampered = _m0_doc(media, digest, size + 1)
    source_memory.write_json(target / source_memory.SLOT_SOURCE, tampered)
    doc, status = source_memory.read_m1_for_clip(str(project), clip)
    assert (doc, status) == (None, "stale")


def test_search_works_with_footage_offline(tmp_path, memory_root):
    """Recorded digests resolve when the media is gone.

    The defect this pins is the other direction of the one above: a
    memory that only resolves live files stops search the moment a
    drive is unplugged, even though every byte it needs is the
    transcript it already holds.
    """
    from library.tools import footage_identity

    media = _media(tmp_path, "TAKE.MXF", b"take-one")
    digest = footage_identity.fingerprint(str(media))["content_digest"]
    project = _project_with_clip(tmp_path, "clip_001", media, digest)
    target = memory_root / digest
    source_memory.write_json(target / source_memory.SLOT_TRANSCRIPT,
                             _m1_doc(digest))
    media.unlink()

    clip = {"clip_id": "clip_001", "source_file": str(media)}
    doc, status = source_memory.read_m1_for_clip(str(project), clip)
    assert status == "fresh"
    assert doc["utterance_count"] == 1


# ── Precedence over the 1.04 regions ───────────────────────────────


def _project_with_temporal_index(tmp_path: Path, with_media: bool):
    root = tmp_path / "proj"
    clips = [
        {"clip_id": "clip_001", "filename": "A.MXF",
         "source_file": str(root / "raw" / "A.MXF"),
         "path": str(root / "raw" / "A.MXF"), "duration_seconds": 20.0},
        {"clip_id": "clip_002", "filename": "B.MXF",
         "source_file": str(root / "raw" / "B.MXF"),
         "path": str(root / "raw" / "B.MXF"), "duration_seconds": 10.0},
    ]
    fingerprints = {}
    if with_media:
        for clip in clips:
            media = _media(tmp_path, clip["filename"],
                           clip["filename"].encode())
            clip["source_file"] = clip["path"] = str(media)
            from library.tools import footage_identity

            fp = footage_identity.fingerprint(str(media))
            fingerprints[clip["clip_id"]] = {
                "path": str(media), **fp}
    else:
        fingerprints = {
            "clip_001": {"path": clips[0]["source_file"],
                         "size_bytes": 1,
                         "content_digest": "digest-clip-001"},
            "clip_002": {"path": clips[1]["source_file"],
                         "size_bytes": 1,
                         "content_digest": "digest-clip-002"},
        }
    _write(root / "pipeline_data.json", {
        "step_outputs": {"catalog": {"clip_catalog": clips}},
        "source_fingerprints": fingerprints,
    })
    steps = root / "pipeline_output" / "steps"
    _write(steps / "1_04_temporal_index" / "index" / "clip_001.json", {
        "clip_id": "clip_001", "duration": 20.0,
        "speech_regions": [
            {"start": 1.0, "end": 3.0, "text": "old words here",
             "words": [{"word": "old", "start": 1.0, "end": 1.5}]}],
    })
    _write(steps / "1_04_temporal_index" / "index" / "clip_002.json", {
        "clip_id": "clip_002", "duration": 10.0,
        "speech_regions": [
            {"start": 2.0, "end": 4.0, "text": "second clip speaks",
             "words": [{"word": "second", "start": 2.0, "end": 2.5}]}],
    })
    return root, fingerprints


def test_memory_transcript_supersedes_without_doubling(tmp_path,
                                                       memory_root):
    """One clip, one transcript: memory words replace 1.04's, not add.

    Both transcribe the whole file, so serving both would index every
    word twice and every query would answer each utterance two times.
    The clip with no memory keeps its 1.04 regions.
    """
    project, fingerprints = _project_with_temporal_index(
        tmp_path, with_media=False)
    digest = fingerprints["clip_001"]["content_digest"]
    source_memory.write_json(
        memory_root / digest / source_memory.SLOT_TRANSCRIPT,
        _m1_doc(digest, words=("memory", "words", "here")))

    segments = footage_segments.build_segments(str(project))
    speech = [s for s in segments if s.kind == "speech"]
    assert len(speech) == 2, [s.segment_id for s in speech]

    first = [s for s in speech if s.clip_id == "clip_001"][0]
    assert first.facets["transcript_source"] == "source-memory"
    assert first.text == "memory words here"
    second = [s for s in speech if s.clip_id == "clip_002"][0]
    assert second.facets["transcript_source"] == "temporal-index"
    assert second.text == "second clip speaks"


def test_index_notices_a_landed_transcript(tmp_path, memory_root):
    """A transcript landing changes the ingest fingerprint.

    Without this, `ren search-index` would keep reporting a fresh
    index after the memory build filled it - and `ren search` would
    keep answering from the pre-memory words with no way to know.
    """
    project, fingerprints = _project_with_temporal_index(
        tmp_path, with_media=False)
    before = footage_segments.ingest_fingerprint(str(project))

    digest = fingerprints["clip_001"]["content_digest"]
    source_memory.write_json(
        memory_root / digest / source_memory.SLOT_TRANSCRIPT,
        _m1_doc(digest))

    after = footage_segments.ingest_fingerprint(str(project))
    assert after["digest"] != before["digest"]
    # The clip count is stable - what moved is what a clip remembers,
    # which is exactly the change the digest must catch.
    assert after["files"] == before["files"]


def test_coverage_counts_what_search_can_hear(tmp_path, memory_root):
    """coverage_report names the memory utterances beside the 1.04 ones."""
    project, fingerprints = _project_with_temporal_index(
        tmp_path, with_media=False)
    report = footage_segments.coverage_report(str(project))
    assert report["utterances"] == 2
    assert report["memory_utterances"] == 0

    digest = fingerprints["clip_002"]["content_digest"]
    source_memory.write_json(
        memory_root / digest / source_memory.SLOT_TRANSCRIPT,
        _m1_doc(digest, words=("a", "b", "c")))
    report = footage_segments.coverage_report(str(project))
    assert report["memory_transcribed_clips"] == 1
    assert report["memory_utterances"] == 1
    assert report["memory_words"] == 3


# ── Program-track selection ────────────────────────────────────────


def test_program_track_selection():
    """The empty MXF stream (-69 dB) is not transcribed as the mix."""
    channel, selection = source_memory.select_program_track(
        {1: -69.4, 2: -21.2, 3: -22.8, 4: -21.9})
    assert channel == 2
    assert selection["basis"] == "loudest-live"
    assert selection["measured_levels_db"]["CH1"] == -69.4

    # No live track means no program - never stream zero by default.
    channel, selection = source_memory.select_program_track(
        {1: -70.1, 2: -69.8})
    assert channel is None
    assert selection["basis"] == "no-live-track"

    # `source.program_stream` names the mix; a dead one is refused.
    channel, selection = source_memory.select_program_track(
        {1: -20.0, 2: -21.0}, declaration=2)
    assert (channel, selection["basis"]) == (2, "declared")
    with pytest.raises(ValueError):
        source_memory.select_program_track({1: -20.0, 2: -70.0},
                                           declaration=2)


# ── The utterance shape ────────────────────────────────────────────


def test_utterance_cut_keeps_region_keys():
    """M1 utterances speak the region shape the search already reads."""
    aligned = {"segments": [
        {"text": "Hello There",
         "words": [{"word": "Hello", "start": 1.0, "end": 1.4},
                   {"word": "There", "start": 1.5, "end": 2.0},
                   {"word": "lost"}]},
        {"text": "   ", "words": []},
    ]}
    regions = source_memory.utterances_from_aligned(aligned, "hybrid-mfa")
    assert len(regions) == 1
    region = regions[0]
    assert region["text"] == "hello there"
    assert [w["word"] for w in region["words"]] == ["hello", "there"]
    assert region["method"] == "hybrid-mfa"
    assert region["confidence"] == 0.0


# ── Layering ───────────────────────────────────────────────────────


# ── M2: the shared frame sample ────────────────────────────────────


def test_select_frames_at_step_matches_old_decode_cadence():
    """Nearest-neighbour thinning of a dense M2 sample must land on the
    same marks a direct `fps=1/step_s` decode would - the only thing
    that changed is who decodes, not which seconds get embedded."""
    from library.tools.analysis.footage_frames import _select_m2_frames

    dense = [{"t": t} for t in
             [0.0, 0.5, 1.0, 1.5, 2.0, 9.5, 10.0, 10.5, 20.0, 100.0]]
    selected = [f["t"] for f in _select_m2_frames(dense, step_s=10.0)]
    assert selected == [0.0, 10.0, 20.0, 100.0]


def test_build_frames_writes_m2_and_fills_gop_in_m0(tmp_path, memory_root,
                                                     monkeypatch):
    """`build_frames` writes the frame index and backfills M0's
    `gop_frames` from the measured keyframe spacing - the field stays
    null (§ the M0 contract) until a sample has actually been taken."""
    from library.tools import footage_identity

    media = _media(tmp_path, "TAKE.MXF", b"iframe-source")
    digest = footage_identity.fingerprint(str(media))["content_digest"]
    size = media.stat().st_size
    m0 = _m0_doc(media, digest, size)
    m0["video_streams"] = [{"avg_frame_rate": "24/1"}]
    source_memory.write_json(
        source_memory.source_dir(digest) / source_memory.SLOT_SOURCE, m0)

    pairs = [("/fake/frame_000000.jpg", 0.0), ("/fake/frame_000001.jpg", 0.5),
             ("/fake/frame_000002.jpg", 1.0)]

    def fake_extract(source_file, out_dir, width, use_hwaccel):
        return pairs, True

    monkeypatch.setattr(source_memory, "extract_iframes", fake_extract)
    account = source_memory.build_frames(str(media), digest)

    assert account["reused"] is False
    assert account["frame_count"] == 3
    # 0.5 s spacing at 24 fps is a 12-frame GOP.
    assert account["gop_frames"] == 12

    doc = source_memory.read_m2(digest)
    assert doc["frame_count"] == 3
    assert doc["instrument"]["hwaccel"] == "videotoolbox"
    assert doc["gop_frames"] == 12

    updated_m0 = source_memory.read_m0(digest)
    assert updated_m0["gop_frames"] == 12

    # A second call does not re-decode a sample that still fingerprints
    # to the live file - `reused: true`, like `build_source`.
    def must_not_extract(*_args, **_kwargs):
        raise AssertionError("a fresh sample was decoded again")

    monkeypatch.setattr(source_memory, "extract_iframes", must_not_extract)
    assert source_memory.build_frames(str(media), digest)["reused"] is True


def test_m2_project_report_skips_offline_media(tmp_path, memory_root):
    """`build_project_frames` reports an offline clip rather than failing
    the whole project - the same shape `build_project` uses for M0/M1."""
    root = tmp_path / "proj"
    _write(root / "pipeline_data.json", {
        "step_outputs": {"catalog": {"clip_catalog": [
            {"clip_id": "clip_001", "filename": "GONE.MXF",
             "source_file": str(tmp_path / "GONE.MXF")},
        ]}},
    })
    report = source_memory.build_project_frames(str(root))
    assert report["clips"][0]["skipped"] == "media-offline"
    assert report["failed"] == []


def test_memory_reaches_no_step_or_process():
    """The search stays importable without the pipeline behind it.

    `footage_segments` (and the memory it reads) is imported by `ren
    search-index`; a steps/process import here would drag the DAG into
    that process. The search guard pins its own modules - this
    pins the new dependency they gained.
    """
    import re

    forbidden = re.compile(
        r"from library\.(steps|processes)|import library\.(steps|processes)")
    for module in ("source_memory",):
        source = (Path(source_memory.__file__).parent
                  / f"{module}.py").read_text(encoding="utf-8")
        assert not forbidden.search(source), f"{module}.py reaches the pipeline"


# ── Source primitives: measured once, read by every lane ───────────


def _tone_wav(path: str, seconds: float = 1.0, amplitude: int = 8000):
    import wave
    with wave.open(path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        frames = int(16000 * seconds)
        wav.writeframes(b"".join(
            (amplitude if i % 2 else -amplitude).to_bytes(2, "little",
                                                         signed=True)
            for i in range(frames)))


def _iphone_probe(_path):
    """An iPhone MOV: stereo AAC plus 4-channel spatial audio (APAC)."""
    return {"duration_seconds": 1.0, "streams": [
        {"index": 0, "codec_type": "video", "codec": "hevc",
         "width": 1920, "height": 1080, "avg_frame_rate": "30/1"},
        {"index": 1, "codec_type": "audio", "codec": "aac",
         "channel": 1, "channels": 2, "sample_rate": 48000},
        {"index": 2, "codec_type": "audio", "codec": "apple_apac",
         "channel": 2, "channels": 4, "sample_rate": 48000}]}


def test_an_undecodable_stream_is_recorded_not_fatal(tmp_path, memory_root,
                                                     monkeypatch):
    """Measured on IMG_1759.MOV: ffmpeg here has no `apple_apac`
    decoder, so demuxing every audio stream in one pass failed the
    WHOLE file and iPhone footage never got a transcript. The stream
    no decoder reads is now recorded and left out of the demux."""
    from library.tools import source_primitives

    media = _media(tmp_path, "IMG_1759.MOV", b"iphone")
    demuxed = []

    def _demux(source, channels, out_dir):
        demuxed.append(list(channels))
        paths = {}
        for ch in channels:
            paths[ch] = str(Path(out_dir) / f"track_CH{ch}.wav")
            _tone_wav(paths[ch])
        return paths

    monkeypatch.setattr(source_memory, "probe_streams", _iphone_probe)
    monkeypatch.setattr(source_memory, "demux_audio_tracks", _demux)
    monkeypatch.setattr(source_primitives, "available_decoders",
                        lambda: frozenset({"aac", "hevc"}))

    first = source_primitives.ensure(str(media))
    assert demuxed == [[1]]
    m0 = first["m0"]
    assert m0["undecodable_audio_streams"] == [
        {"channel": 2, "codec": "apple_apac"}]
    assert m0["program_track"]["channel"] == 1
    assert Path(first["program_wav"]).is_file()

    # Every later consumer reads it: no second probe or demux.
    monkeypatch.setattr(source_memory, "probe_streams",
                        lambda _p: pytest.fail("probed twice"))
    monkeypatch.setattr(source_memory, "demux_audio_tracks",
                        lambda *a: pytest.fail("demuxed twice"))
    again = source_primitives.ensure(str(media))
    assert again["reused"] is True
    assert again["program_wav"] == first["program_wav"]
    # ...unless the program decision is asked under another declaration.
    assert source_primitives.read(str(media), 2) is None


# --------------------------------------------------------------------------
# From test_measurement_layers.py
#
# Per-layer measurement cache for the semantic profile.
#
# The defect: any change to step 1.03's eleven identity files deleted
# every profile and re-measured every clip whole (project 001: 17 clips,
# 3,167 s of model time, 29 times in September 2026). These pin the three
# things the fix rests on: a layer's key moves with its OWN code and no
# one else's, a compose from cached layers calls no model and yields the
# profile the measurement did, and the store is source memory's.

MODULE = textwrap.dedent('''
    """Module docstring."""
    from library.tools.helper import shared
    LIMIT = 5

    def _clamp(x):
        return min(x, LIMIT)

    def windows(x):
        """Windows pass."""
        return _clamp(x) + shared(x)

    def objects(x):
        return x * 2
''')


def _tree(tmp_path, module=MODULE, helper="def shared(x):\n    return x\n"):
    (tmp_path / "library" / "tools").mkdir(parents=True, exist_ok=True)
    (tmp_path / "library" / "tools" / "helper.py").write_text(helper)
    path = tmp_path / "mod.py"
    path.write_text(module)
    return {layer: ml.method_digest(path, [layer], tmp_path)
            for layer in ("windows", "objects")}


def test_a_layer_key_moves_with_its_own_code_and_no_one_elses(tmp_path):
    base = _tree(tmp_path)

    # An objects-only edit leaves the windows layer alone.
    edited = _tree(tmp_path, MODULE.replace("x * 2", "x * 3"))
    assert edited["objects"] != base["objects"]
    assert edited["windows"] == base["windows"]

    # A helper two names down, and an imported repo module, are both
    # part of the windows identity without anyone listing them.
    assert _tree(tmp_path, MODULE.replace("LIMIT = 5", "LIMIT = 6")
                 )["windows"] != base["windows"]
    moved = _tree(tmp_path, helper="def shared(x):\n    return -x\n")
    assert moved["windows"] != base["windows"]
    assert moved["objects"] == base["objects"]

    # Prose is not method.
    prose = _tree(tmp_path, MODULE.replace("Windows pass.", "Reworded.")
                  .replace("return x * 2", "return x * 2  # comment"))
    assert prose == base


def test_the_store_is_source_memory(tmp_path, monkeypatch):
    for env in ({}, {"PIPELINE_VEP_HOME": str(tmp_path / "home")},
                {"XDG_DATA_HOME": str(tmp_path / "xdg")},
                {"PIPELINE_SOURCE_MEMORY_ROOT": str(tmp_path / "mem")}):
        for name in ("PIPELINE_SOURCE_MEMORY_ROOT", "PIPELINE_VEP_HOME",
                     "XDG_DATA_HOME"):
            monkeypatch.delenv(name, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        assert ml.store_root() == source_memory.memory_root()


class _CountingAnalyzer:
    """Canned answers that count every model call."""

    def __init__(self):
        self.calls = 0

    def analyze_with_retry(self, prompt, parse_fn, images=None, video=None,
                           max_tokens=512, label="pass", audio=None):
        self.calls += 1
        if label.startswith("Objects"):
            result = [{"label": "mug", "appearances": [[0.0, 10.0]]}]
            return result, json.dumps(result), 0.2
        result = {
            "actions": [{"action": "talks", "start": 0.0, "end": 9.0}],
            "scene": [{"start": 0.0, "end": 10.0, "description": "room"}],
            "camera": [{"start": 0.0, "end": 10.0, "mode": "static"}],
            "assessment": {"content_type": "person_talking_to_camera",
                           "primary_subject_visible": [[0, 10]]},
        }
        return result, json.dumps(result), 0.3


def test_a_compose_from_cached_layers_calls_no_model(tmp_path):
    source = tmp_path / "clip.mov"
    source.write_bytes(b"not really a movie")
    meta = {"clip_id": "clip", "file_path": str(source), "duration_s": 10.0,
            "fps": 30.0, "resolution": [1920, 1080]}
    frames = [{"timestamp": 0.0, "path": "/tmp/f0.jpg"}]
    video_clips = [{"index": 0, "start": 0.0, "end": 10.0,
                    "path": "/tmp/stub.mp4", "has_audio": False}]
    methods = {"windows": "w1", "objects": "o1", "picture": "p1"}

    def compose(analyzer, cache, measurable=True):
        soft = [{"start": 1.0, "end": 2.0}] if measurable else None
        with patch.object(vp.picture_quality, "measure_soft_picture",
                          return_value=soft) as picture:
            profile = vp.analyze_clip(
                analyzer, meta, frames, video_clips, "", None,
                str(tmp_path / "cache"), layer_cache=cache)
        return profile, picture.call_count

    uncached, _ = compose(_CountingAnalyzer(), None)

    first = _CountingAnalyzer()
    measured, picture_calls = compose(first, ml.LayerCache.for_source(
        source, methods, {}, root=tmp_path / "mem"))
    assert first.calls and picture_calls == 1

    again = _CountingAnalyzer()
    reused, picture_calls = compose(again, ml.LayerCache.for_source(
        source, methods, {}, root=tmp_path / "mem"), measurable=False)
    assert again.calls == 0 and picture_calls == 0

    layers = {}
    for profile in (measured, reused):
        layers[id(profile)] = profile["analysis_metadata"].pop(
            "measurement_layers")
        for entry in profile["actions"]:
            entry.pop("analysis_time_s")
    for entry in uncached["actions"]:
        entry.pop("analysis_time_s")
    assert {v["outcome"] for v in layers[id(measured)].values()} == {
        "measured"}
    assert {v["outcome"] for v in layers[id(reused)].values()} == {"reused"}
    # The same profile, whether measured with no cache, measured into
    # one, or composed back out of it.
    for profile in (measured, reused, uncached):
        profile["analysis_metadata"].pop("window_inference_wall_s")
    assert measured == uncached
    assert reused == measured

    # A moved objects method re-measures objects alone.
    partial = _CountingAnalyzer()
    compose(partial, ml.LayerCache.for_source(
        source, {**methods, "objects": "o2"}, {}, root=tmp_path / "mem"))
    assert partial.calls == 1


# --------------------------------------------------------------------------
# From test_method_change_reanalyzes.py
#
# A cached analysis must not survive its own method changing.
#
# Finding 8, execution-frontier report 2026-09-24: music_analysis reused an
# August librosa grid after beat_this landed ("reusing cached result"),
# and semantic analysis printed "Step code changed ... invalidating" and
# then reused all 17 August profiles ("17 already analyzed"). The
# ledger-level invalidation promises a re-run; these tests pin the
# second half - the step's own resume logic must treat artifacts written
# by older code as missing.
#
# No pipeline run, no model, no vision. Step 1.03 is exercised through
# its collection half only (copied profiles, ``raw_footage_files: []`` -
# never against a real project). Step 2.06 is exercised through its
# cache check only: a hit returns without running the pipeline, a miss
# proceeds to it (and reports unavailable on an empty scratch track
# rather than reusing the stale grid).

PILOT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PILOT_ROOT))

from library.tools import code_identity
from library.tools.project_layout import Area, ProjectLayout


def _load_step_module(step_dir_name: str, module_name: str):
    path = (PILOT_ROOT / "library" / "steps" / step_dir_name / "step.py")
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


semantic_step = _load_step_module(
    "step_1_03_semantic_analysis", "step_1_03_under_test")
music_step = _load_step_module(
    "step_2_06_music_analysis", "step_2_06_under_test")

def _v3_profile_bytes() -> bytes:
    """A minimal v3 profile the collection half accepts."""
    return json.dumps({
        "analysis_metadata": {"pipeline_version": "3"},
        "scene": [],
        "camera": [],
        "actions": [],
        "objects": [],
        "assessment": {},
    }).encode("utf-8")


def _analysis_dir(project_folder: str) -> str:
    return str(ProjectLayout(project_folder).write_dir(
        Area.VISION_ANALYSIS, step="semantic_analysis"))


# ── step 1.03: profiles from older code are not reused ───────────

def test_semantic_profiles_from_an_old_method_are_not_reused(tmp_path):
    """The finding: 17 August profiles reused after the method moved.

    The old method's stamp differs from the current source hash, so its
    profiles are removed instead of returned as current measurements.
    """
    project = tmp_path / "proj"
    project.mkdir()
    analysis_dir = _analysis_dir(str(project))
    (Path(analysis_dir) / "clip_profile_IMG_1806_v3.json").write_bytes(
        _v3_profile_bytes())
    (Path(analysis_dir) / code_identity.CODE_STAMP_FILENAME).write_text(
        "old-semantic-method-hash\n", encoding="utf-8")

    result = semantic_step.analyse_semantics(
        [], project_folder=str(project))

    assert result["semantic_analysis_documents"] == []


# ── step 2.06: a grid from an older method is not a hit ──────────

def _music_dir(project_folder: str) -> str:
    return str(ProjectLayout(project_folder).write_dir(
        Area.MUSIC_ANALYSIS, step="music_analysis"))


def test_music_method_change_does_not_reuse_or_relabel_the_old_grid(
        tmp_path, monkeypatch):
    """A successful process exit without output must not bless stale data.

    Before the fix, the step could read the still-present August file
    after a no-output re-analysis and write the current method hash onto
    it, making the next run trust the old grid.
    """
    project = tmp_path / "proj"
    project.mkdir()
    track = project / "bed.wav"
    track.write_bytes(b"\x00" * 64)
    music_dir = _music_dir(str(project))
    analysis_path = Path(music_dir) / "music_analysis.json"
    analysis_path.write_text(json.dumps({
        "file": str(track),
        "method_hash": "old-music-method-hash",
        "tempo": {"method": "librosa-beat-track", "bpm": 88.2},
    }), encoding="utf-8")
    monkeypatch.setattr(
        music_step.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""))

    result = music_step.analyse_music(
        {"audio_path": str(track)}, project_folder=str(project))

    assert result["music_analysis"]["available"] is False
    assert "not found" in result["music_analysis"]["error"]


# --------------------------------------------------------------------------
# From test_person_entity.py
#
# The person entity store (M3b): clustering, linking and cross-source
# resolution.
#
# Every test builds its project and its memory under `tmp_path` and never
# calls insightface, ffmpeg or the ECAPA encoder - the measured parts
# (face-identity FAR, diarization DER) are proven in
# `data/vep-person-entity-store/eval/results.md` and PR #1482; these tests
# guard the LOGIC this task adds on top of those measurements: clustering,
# the speech-face join, and cross-source person resolution.

@pytest.fixture
def memory_root_2(tmp_path, monkeypatch):
    root = tmp_path / "memory"
    monkeypatch.setenv(source_memory.MEMORY_ROOT_ENV, str(root))
    return root


def _unit(vec: np.ndarray) -> tuple:
    return tuple((vec / np.linalg.norm(vec)).tolist())


def _embedding(dim: int, seed: int) -> tuple:
    rng = np.random.default_rng(seed)
    return _unit(rng.normal(size=dim))


def _jitter(base: tuple, seed: int, scale: float = 0.05) -> tuple:
    rng = np.random.default_rng(seed)
    v = np.asarray(base) + rng.normal(scale=scale, size=len(base))
    return _unit(v)


def _media_2(tmp_path: Path, name: str, seed: bytes) -> Path:
    path = tmp_path / "raw" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(seed * (3 * 1024 * 1024 // len(seed) + 1))
    return path


def _project_with_clips(tmp_path: Path, clips: list, extra_source: dict = None) -> Path:
    """`clips`: `[(clip_id, media_path, digest)]`."""
    root = tmp_path / "proj"
    catalog = [{"clip_id": cid, "filename": media.name,
               "source_file": str(media), "path": str(media),
               "duration_seconds": 10.0} for cid, media, _ in clips]
    fingerprints = {cid: {"path": str(media), "size_bytes": media.stat().st_size,
                         "content_digest": digest}
                   for cid, media, digest in clips}
    data = {"step_outputs": {"catalog": {"clip_catalog": catalog}},
           "source_fingerprints": fingerprints}
    (root).mkdir(parents=True, exist_ok=True)
    (root / "pipeline_data.json").write_text(json.dumps(data), encoding="utf-8")
    if extra_source is not None:
        (root / "project.yaml").write_text(
            "source:\n" + "\n".join(f"  {k}: {v}" for k, v in extra_source.items()),
            encoding="utf-8")
    return root


# ── sample_timestamps ─────────────────────────────────────────────────


def test_sample_timestamps_stay_inside_the_source_and_the_cap():
    """A source too short for a safe margin gets no samples, never a
    span that claims presence at a timestamp past the file's own
    length."""
    assert person_entity.sample_timestamps(0.5) == []
    assert person_entity.sample_timestamps(0.0) == []
    # A long source is capped at max_samples, inside its own length.
    out = person_entity.sample_timestamps(10000.0, interval_s=10.0,
                                          max_samples=20)
    assert len(out) <= 20
    assert out[0] > 0.0
    assert out[-1] < 10000.0


def test_extract_frame_scales_before_writing_the_jpeg(tmp_path, monkeypatch):
    output = tmp_path / "frame.jpg"
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        Path(command[command.index("-q:v") + 2]).write_bytes(b"jpeg")
        return type("Completed", (), {"returncode": 0})()

    monkeypatch.setattr(person_entity.subprocess, "run", fake_run)

    assert person_entity.extract_frame("source.MXF", 5.0, str(output))
    command = captured["command"]
    assert command[command.index("-vf") + 1] == (
        f"scale='min({person_entity.FACE_INPUT_MAX_WIDTH},iw)':-2")
    assert output.read_bytes() == b"jpeg"


# ── resolve_sample_frames (M2 reuse vs own decode) ──────────────────────


def test_a_fresh_m2_sample_gives_its_times_never_its_thumbnails(tmp_path, memory_root_2,
                                                                monkeypatch):
    """With a fresh M2 the frames are decoded from source at M2's I-frame
    times, then capped to the measured face input width. ArcFace on the
    384 px thumbnails themselves fails the face study; the times still
    have to be M2's so M7 can join M3b to M3."""
    media = _media_2(tmp_path, "A.MXF", b"source-a")
    digest = footage_identity.fingerprint(str(media))["content_digest"]
    source_memory.write_json(memory_root_2 / digest / source_memory.SLOT_FRAMES_INDEX, {
        "content_digest": digest, "size_bytes": media.stat().st_size,
        "status": "sampled", "frame_count": 3,
        "frames": [{"file": "frames/frame_000001.jpg", "t": 0.5},
                  {"file": "frames/frame_000002.jpg", "t": 5.0},
                  {"file": "frames/frame_000003.jpg", "t": 9.9}],
    })
    decoded = []

    def fake_extract(source_file, timestamp, out_path):
        decoded.append((source_file, timestamp))
        Path(out_path).write_bytes(b"x")
        return True

    monkeypatch.setattr(person_entity, "extract_frame", fake_extract)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    frames, source = person_entity.resolve_sample_frames(
        str(media), digest, duration_seconds=10.0, root=None,
        scratch_dir=str(scratch), interval_s=4.0, max_samples=10)

    assert source == person_entity.FRAME_SOURCE_M2_TIMES
    assert sorted(decoded) == [(str(media), 0.5), (str(media), 5.0), (str(media), 9.9)]
    assert [t for t, _path, _owned in frames] == [0.5, 5.0, 9.9]
    assert all(owned and "frames/frame_" not in path for _t, path, owned in frames)


def test_resolve_sample_frames_falls_back_when_m2_is_stale_or_absent(tmp_path, memory_root_2,
                                                            monkeypatch):
    """A stale M2 record (size/digest disagree with the live file) must
    never be served - the fallback path (own decode) must be taken
    instead, the same freshness rule M1 already follows."""
    media = _media_2(tmp_path, "A.MXF", b"source-a")
    digest = footage_identity.fingerprint(str(media))["content_digest"]
    source_memory.write_json(memory_root_2 / digest / source_memory.SLOT_FRAMES_INDEX, {
        "content_digest": digest, "size_bytes": media.stat().st_size + 1,  # stale
        "status": "sampled", "frame_count": 1,
        "frames": [{"file": "frames/frame_000001.jpg", "t": 0.5}],
    })

    calls = []

    def fake_extract(source_file, timestamp, out_path):
        calls.append(timestamp)
        Path(out_path).write_bytes(b"\xff\xd8\xff")  # not a real jpeg, just non-empty
        return True

    monkeypatch.setattr(person_entity, "extract_frame", fake_extract)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    frames, source = person_entity.resolve_sample_frames(
        str(media), digest, duration_seconds=10.0, root=None,
        scratch_dir=str(scratch), interval_s=5.0, max_samples=10)

    assert source == person_entity.FRAME_SOURCE_OWN_DECODE
    assert calls  # ffmpeg extraction was actually invoked
    assert all(owned is True for _t, _path, owned in frames)

    # With no M2 record at all, the same fallback.
    media = _media_2(tmp_path, "B.MXF", b"source-b")
    digest = footage_identity.fingerprint(str(media))["content_digest"]
    monkeypatch.setattr(person_entity, "extract_frame",
                        lambda source_file, timestamp, out_path:
                        Path(out_path).write_bytes(b"x") or True)
    frames, source = person_entity.resolve_sample_frames(
        str(media), digest, duration_seconds=10.0, root=None,
        scratch_dir=str(scratch), interval_s=5.0, max_samples=10)

    assert source == person_entity.FRAME_SOURCE_OWN_DECODE
    assert frames


# ── frames_at parallelism ───────────────────────────────────────────


def test_frames_at_returns_timestamp_order_whatever_finishes_first(
        tmp_path, memory_root_2, monkeypatch):
    """Parallel seeks complete out of order - the returned frames must
    still be timestamp-sorted. The defect this guards is completion
    order leaking into `measure_face_observations`, whose cluster spans
    assume time order."""
    import time as _time

    media = _media_2(tmp_path, "A.MXF", b"source-a")
    digest = footage_identity.fingerprint(str(media))["content_digest"]

    def slow_first(source_file, timestamp, out_path):
        _time.sleep(max(0.0, 0.05 - timestamp * 0.01))
        Path(out_path).write_bytes(b"x")
        return True

    monkeypatch.setattr(person_entity, "extract_frame", slow_first)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    frames, _source = person_entity.frames_at(
        str(media), digest, [5.0, 1.0, 3.0, 2.0, 4.0], None, str(scratch))

    assert [t for t, _path, _owned in frames] == [1.0, 2.0, 3.0, 4.0, 5.0]

    # One corrupt seek must not fail the lane - the frame is omitted
    # and the surviving frames still arrive sorted.
    def flaky(source_file, timestamp, out_path):
        if timestamp == 3.0:
            return False
        Path(out_path).write_bytes(b"x")
        return True

    monkeypatch.setattr(person_entity, "extract_frame", flaky)
    frames, _source = person_entity.frames_at(
        str(media), digest, [1.0, 2.0, 3.0, 4.0], None, str(scratch))

    assert [t for t, _path, _owned in frames] == [1.0, 2.0, 4.0]

    # No timestamps means no work - in particular no zero-worker pool,
    # which `ThreadPoolExecutor` refuses.
    def boom(source_file, timestamp, out_path):  # pragma: no cover
        raise AssertionError("must not decode")

    monkeypatch.setattr(person_entity, "extract_frame", boom)
    frames, source = person_entity.frames_at(
        str(media), digest, [], None, str(scratch))
    assert frames == []
    assert source == person_entity.FRAME_SOURCE_OWN_DECODE


def test_nearest_m2_times_deduplicates_shared_nearest_frame():
    """Two planned timestamps landing on the same nearest M2 frame must
    contribute it once, not twice - a regression here would double-count
    one frame's face toward the cluster."""
    m2_frames = [{"file": "frames/frame_000001.jpg", "t": 1.0},
                {"file": "frames/frame_000002.jpg", "t": 20.0}]
    assert person_entity._nearest_m2_times(m2_frames, [0.9, 1.1, 19.0]) == [1.0, 20.0]


def test_face_cache_key_tracks_the_sample_plan_and_input_width(monkeypatch):
    args = ("digest", person_entity.FRAME_SOURCE_OWN_DECODE, [1.0, 2.0],
            person_entity.FRAME_SAMPLE_INTERVAL_S, person_entity.MAX_FRAME_SAMPLES)
    original = person_entity._face_cache_key(*args)
    changed_times = person_entity._face_cache_key(
        args[0], args[1], [1.0, 2.5], args[3], args[4])

    monkeypatch.setattr(person_entity, "FACE_INPUT_MAX_WIDTH", 640)
    changed_width = person_entity._face_cache_key(*args)

    assert original != changed_times
    assert original != changed_width


def test_face_cache_rejects_a_record_with_pixels_above_the_width_cap():
    cache_key = person_entity._face_cache_key(
        "digest", person_entity.FRAME_SOURCE_OWN_DECODE, [5.0], 10.0,
        person_entity.MAX_FRAME_SAMPLES)
    identity = {"status": person_entity.STATUS_MEASURED,
                "face_match_threshold": person_entity.FACE_MATCH_THRESHOLD,
                "instrument": {
                    "face_cache_key": cache_key,
                    "sample_count": 1,
                    "face_input_max_width": person_entity.FACE_INPUT_MAX_WIDTH,
                    "face_model_pack": person_entity.shared_environment.INSIGHTFACE_PACK_NAME,
                    "frame_pixels": [3840, 2160],
                }}

    assert not person_entity._cached_face_record(identity, cache_key, 1)


def test_build_source_identity_reuses_a_matching_face_measurement(
        tmp_path, memory_root_2, monkeypatch):
    from library.tools import source_primitives

    media = _media_2(tmp_path, "A.MXF", b"source-a")
    digest = footage_identity.fingerprint(str(media))["content_digest"]
    m0 = {"video_streams": [{"width": 3840, "height": 2160}],
          "duration_seconds": 10.0,
          "program_track": {"basis": "no-audio-streams"}}
    primitives = {"content_digest": digest, "m0": m0,
                  "program_wav": None}
    monkeypatch.setattr(source_primitives, "ensure",
                        lambda *_args, **_kwargs: primitives)

    timestamps = person_entity.sample_timestamps(10.0)
    cache_key = person_entity._face_cache_key(
        digest, person_entity.FRAME_SOURCE_OWN_DECODE, timestamps,
        person_entity.FRAME_SAMPLE_INTERVAL_S, person_entity.MAX_FRAME_SAMPLES)
    faces = [{"track_id": "face_001", "embedding": [1.0, 0.0],
              "spans": [{"start": 4.9, "end": 5.1,
                         "box": [0.0, 0.0, 1.0, 1.0], "det_score": 0.9}]}]
    source_memory.write_json(source_memory.source_dir(digest, memory_root_2)
                             / source_memory.SLOT_IDENTITY, {
        "content_digest": digest, "status": person_entity.STATUS_MEASURED,
        "face_match_threshold": person_entity.FACE_MATCH_THRESHOLD,
        "faces": faces, "instrument": {
            "face_cache_key": cache_key,
            "sample_count": len(timestamps),
            "face_input_max_width": person_entity.FACE_INPUT_MAX_WIDTH,
            "face_model_pack": person_entity.shared_environment.INSIGHTFACE_PACK_NAME,
            "frame_source": person_entity.FRAME_SOURCE_OWN_DECODE,
            "frame_pixels": [person_entity.FACE_INPUT_MAX_WIDTH, 288],
        },
    })

    def unexpected(*_args, **_kwargs):
        pytest.fail("a valid face cache must bypass decode and embedding")

    monkeypatch.setattr(person_entity, "resolve_sample_frames", unexpected)
    monkeypatch.setattr(person_entity, "measure_face_observations", unexpected)

    result = person_entity.build_source_identity(str(media), root=memory_root_2)

    assert result["face_measurement_reused"] is True
    assert result["faces"] == 1
    assert person_entity.read_identity(digest, memory_root_2)["faces"] == faces


def test_voice_cache_key_tracks_audio_device_parameters_and_model(monkeypatch):
    from library.tools import shared_environment
    from library.tools import single_track_diarization as std

    original = person_entity._voice_cache_key("pcm", 2, "mps")
    changed = {
        "pcm": person_entity._voice_cache_key("other-pcm", 2, "mps"),
        "speakers": person_entity._voice_cache_key("pcm", None, "mps"),
        "device": person_entity._voice_cache_key("pcm", 2, "cpu"),
    }
    monkeypatch.setattr(std, "VAD_DB_BELOW_PEAK", std.VAD_DB_BELOW_PEAK + 1)
    changed["vad"] = person_entity._voice_cache_key("pcm", 2, "mps")
    monkeypatch.undo()
    monkeypatch.setattr(shared_environment, "ECAPA_REVISION", "another")
    changed["model"] = person_entity._voice_cache_key("pcm", 2, "mps")

    assert all(key != original for key in changed.values()), changed
    assert person_entity._voice_cache_key(None, 2, "mps") is None
    assert person_entity._voice_cache_key("pcm", 2, None) is None


def test_build_source_identity_reuses_voices_but_never_a_refusal(
        tmp_path, memory_root_2, monkeypatch):
    from library.tools import shared_environment, source_primitives
    from library.tools import single_track_diarization as std

    media = _media_2(tmp_path, "A.MXF", b"source-a")
    digest = footage_identity.fingerprint(str(media))["content_digest"]
    m0 = {"video_streams": [{"width": 3840, "height": 2160}],
          "duration_seconds": 10.0, "program_track": {"channel": 1}}
    primitives = {"content_digest": digest, "m0": m0,
                  "program_wav": str(tmp_path / "program.16k.wav"),
                  "program_pcm_sha256": "pcm"}
    monkeypatch.setattr(source_primitives, "ensure",
                        lambda *_args, **_kwargs: primitives)

    def no_insightface():
        raise shared_environment.InsightfaceEnvironmentMissing("absent")

    monkeypatch.setattr(shared_environment, "require_insightface",
                        no_insightface)
    monkeypatch.setattr(std, "embedding_device", lambda: "cpu")
    voices = [{"track_id": "voice_001", "embedding": [1.0, 0.0],
               "spans": [[0.0, 4.0]]}]
    answers = [([], "speechbrain is not installed"), (voices, None)]
    calls = []

    def measure(*args):
        calls.append(args)
        return answers[len(calls) - 1]

    monkeypatch.setattr(person_entity, "measure_voice_tracks", measure)

    refused = person_entity.build_source_identity(str(media), root=memory_root_2)
    measured = person_entity.build_source_identity(str(media), root=memory_root_2)
    reused = person_entity.build_source_identity(str(media), root=memory_root_2)

    assert [refused["voice_measurement_reused"],
            measured["voice_measurement_reused"],
            reused["voice_measurement_reused"]] == [False, False, True]
    assert len(calls) == 2
    record = person_entity.read_identity(digest, memory_root_2)
    assert record["voices"] == voices
    assert record["instrument"]["voice_device"] == "cpu"


# ── cluster_face_observations ─────────────────────────────────────────


def test_cluster_face_observations_groups_same_person_apart_from_different():
    """Two people's jittered embeddings must land in two tracks, not one -
    the defect this guards is a threshold applied so loosely (or
    inverted) that every face collapses into a single track."""
    base_a = _embedding(512, 1)
    base_b = _embedding(512, 2)
    obs = [person_entity.FaceObservation(t, (0, 0, 10, 10), 0.9,
                                         _jitter(base_a, seed=10 + i))
          for i, t in enumerate([1.0, 2.0, 3.0])]
    obs += [person_entity.FaceObservation(t, (0, 0, 10, 10), 0.9,
                                          _jitter(base_b, seed=20 + i))
           for i, t in enumerate([10.0, 11.0])]

    tracks = person_entity.cluster_face_observations(obs)

    assert len(tracks) == 2
    assert {len(t["spans"]) for t in tracks} == {3, 2}


# ── link_speech_to_face ────────────────────────────────────────────────


def _face_track(track_id: str, spans: list) -> dict:
    return {"track_id": track_id, "embedding": [0.0], "spans": spans}


def test_link_speech_to_face_only_links_overlapping_spans():
    """A face span with no covering voice turn gets no link - the
    defect this guards is a 'nearest turn' fallback that would invent
    an attribution past what the spans actually overlap (the module
    docstring states this is never invented)."""
    faces = [_face_track("face_001", [{"start": 1.0, "end": 1.2}]),
            _face_track("face_002", [{"start": 50.0, "end": 50.2}])]
    voices = [{"track_id": "voice_001", "embedding": [0.0],
              "spans": [[0.5, 3.5]]}]
    links = person_entity.link_speech_to_face(faces, voices)
    assert len(links) == 1
    assert links[0]["face_track"] == "face_001"
    assert links[0]["voice_track"] == "voice_001"
    # No voice tracks at all (ECAPA unreachable, a silent source) gives
    # zero links, never a fabricated one.
    assert person_entity.link_speech_to_face(faces, []) == []


# ── declared_person_names ─────────────────────────────────────────────


def test_declared_person_names_reads_yaml_and_drops_non_string_values(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "project.yaml").write_text(
        "source:\n"
        "  person_names:\n"
        "    person_001: Craig\n"
        "    person_002: 7\n",
        encoding="utf-8")
    names = person_entity.declared_person_names(str(project))
    assert names == {"person_001": "Craig"}
    (project / "project.yaml").write_text("name: x\n", encoding="utf-8")
    assert person_entity.declared_person_names(str(project)) == {}


# ── resolve_person_tracks / find_person ────────────────────────────────


def _write_identity(root: Path, digest: str, record: dict):
    source_memory.write_json(root / digest / source_memory.SLOT_IDENTITY,
                             record)


def test_resolve_person_tracks_merges_same_person_across_sources(
        tmp_path, memory_root_2):
    """The same face embedding appearing in two different source digests
    must resolve to ONE person, with spans from both clips - this is the
    cross-video identity the report asked for."""
    base = _embedding(512, 42)
    media_a = _media_2(tmp_path, "A.MXF", b"source-a")
    media_b = _media_2(tmp_path, "B.MXF", b"source-b")
    digest_a = footage_identity.fingerprint(str(media_a))["content_digest"]
    digest_b = footage_identity.fingerprint(str(media_b))["content_digest"]
    project = _project_with_clips(
        tmp_path, [("clip_001", media_a, digest_a),
                  ("clip_002", media_b, digest_b)])

    _write_identity(memory_root_2, digest_a, {
        "content_digest": digest_a, "status": "measured",
        "faces": [{"track_id": "face_001",
                  "embedding": list(_jitter(base, seed=1)),
                  "spans": [{"start": 1.0, "end": 1.2, "box": [0, 0, 1, 1],
                            "det_score": 0.9}]}],
        "voices": [], "speech_face_links": []})
    _write_identity(memory_root_2, digest_b, {
        "content_digest": digest_b, "status": "measured",
        "faces": [{"track_id": "face_001",
                  "embedding": list(_jitter(base, seed=2)),
                  "spans": [{"start": 5.0, "end": 5.2, "box": [0, 0, 1, 1],
                            "det_score": 0.9}]}],
        "voices": [], "speech_face_links": []})

    roster = person_entity.resolve_person_tracks(str(project))
    assert len(roster["persons"]) == 1
    person = roster["persons"][0]
    clip_ids = {span["clip_id"] for span in person["face_spans"]}
    assert clip_ids == {"clip_001", "clip_002"}


def test_resolve_person_tracks_never_merges_on_voice_alone(
        tmp_path, memory_root_2):
    """Two sources whose VOICE embeddings are identical but whose FACE
    embeddings are unrelated must stay two different people - the
    module's stated design decision (voice never merges cross-source
    identity, only face does, because only face has a measured FAR).
    A regression that started matching on voice similarity would merge
    these into one person silently."""
    identical_voice = list(_embedding(192, 99))
    base_a = _embedding(512, 1)
    base_b = _embedding(512, 2)
    media_a = _media_2(tmp_path, "A.MXF", b"source-a")
    media_b = _media_2(tmp_path, "B.MXF", b"source-b")
    digest_a = footage_identity.fingerprint(str(media_a))["content_digest"]
    digest_b = footage_identity.fingerprint(str(media_b))["content_digest"]
    project = _project_with_clips(
        tmp_path, [("clip_001", media_a, digest_a),
                  ("clip_002", media_b, digest_b)])

    for digest, base in ((digest_a, base_a), (digest_b, base_b)):
        _write_identity(memory_root_2, digest, {
            "content_digest": digest, "status": "measured",
            "faces": [{"track_id": "face_001", "embedding": list(base),
                      "spans": [{"start": 1.0, "end": 1.2,
                                "box": [0, 0, 1, 1], "det_score": 0.9}]}],
            "voices": [{"track_id": "voice_001",
                       "embedding": identical_voice,
                       "spans": [[0.5, 2.0]]}],
            "speech_face_links": [{"t": 1.1, "face_track": "face_001",
                                   "voice_track": "voice_001",
                                   "basis": "co-occurrence: voice span + "
                                           "face span overlap"}]})

    roster = person_entity.resolve_person_tracks(str(project))
    assert len(roster["persons"]) == 2


def test_find_person_resolves_declared_name_and_reports_none_unmatched(
        tmp_path, memory_root_2):
    base = _embedding(512, 1)
    media = _media_2(tmp_path, "A.MXF", b"source-a")
    digest = footage_identity.fingerprint(str(media))["content_digest"]
    project = _project_with_clips(
        tmp_path, [("clip_001", media, digest)],
        extra_source={"person_names": "{person_001: Craig}"})

    _write_identity(memory_root_2, digest, {
        "content_digest": digest, "status": "measured",
        "faces": [{"track_id": "face_001", "embedding": list(base),
                  "spans": [{"start": 1.0, "end": 1.2, "box": [0, 0, 1, 1],
                            "det_score": 0.9}]}],
        "voices": [], "speech_face_links": []})

    found = person_entity.find_person(str(project), "craig")
    assert found is not None
    assert found["person_id"] == "person_001"

    assert person_entity.find_person(str(project), "nobody") is None


# --------------------------------------------------------------------------
# From test_face_identity_study.py
#
# The face-identity study measures the frames production decodes.
#
# The first run measured 4K stills while production ran ArcFace on 384 px
# M2 thumbnails. The study now reaches production's source decode and
# measured output-width cap. Nothing here runs ffmpeg or insightface.

@pytest.fixture
def memory_root_3(tmp_path, monkeypatch):
    root = tmp_path / "memory"
    monkeypatch.setenv(source_memory.MEMORY_ROOT_ENV, str(root))
    return root


def test_the_study_takes_its_frames_from_productions_decode_path(tmp_path, memory_root_3,
                                                                  monkeypatch):
    """The study's frames come through `person_entity.frames_at` - the
    path production's faces take - and its report says what it measured
    on, so a study on stills production never decodes cannot pass."""
    media = tmp_path / "A.MXF"
    media.write_bytes(b"a" * 4096)
    digest = footage_identity.fingerprint(str(media))["content_digest"]
    source_memory.write_json(memory_root_3 / digest / source_memory.SLOT_SOURCE,
                             {"content_digest": digest, "duration_seconds": 30.0})
    seen = []

    def frames_at(source_file, dig, timestamps, root, scratch):
        seen.append(list(timestamps))
        return [(t, f"{scratch}/f_{t}.jpg", True) for t in timestamps], "decoded-here"

    def measure(frames):
        rng = np.random.default_rng(0)
        return [person_entity.FaceObservation(t, (0, 0, 1, 1), 0.9,
                                              tuple(rng.normal(size=8)))
                for t, _p, _o in frames], [3840, 2160]

    monkeypatch.setattr(person_entity, "frames_at", frames_at)
    monkeypatch.setattr(person_entity, "measure_face_observations", measure)

    out = face_identity_study.measure_source(str(media), "craig", [12.5])

    assert seen == [sorted(set(person_entity.sample_timestamps(30.0)) | {12.5})]
    assert (out["frame_source"], out["frame_pixels"]) == ("decoded-here", [3840, 2160])
    assert len(out["faces"]) == len(seen[0])


def test_one_same_person_pair_below_the_threshold_fails_the_study():
    """The bound is FAR=0 AND FRR=0: a single weak same-person pair - the
    thumbnails' profile frames - fails it, and is named."""
    a = (1.0, 0.0, 0.0)
    faces = [("craig@1", "craig", a), ("craig@2", "craig", (0.95, 0.31, 0.0)),
             ("craig@3", "craig", (0.2, 0.0, 0.98)), ("akshita@1", "akshita", (0.0, 1.0, 0.0))]

    report = face_identity_study.pair_report(faces, threshold=0.25)

    assert not report["passes"]
    assert report["at_threshold"]["false_rejects"] == 2
    assert {key for key, _n in report["frames_in_failing_pairs"]} >= {"craig@3"}
    json.dumps(report)
