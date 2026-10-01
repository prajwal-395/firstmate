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


def test_dead_tracks_are_never_program():
    """The empty MXF stream (-69 dB) is not transcribed as the mix."""
    channel, selection = source_memory.select_program_track(
        {1: -69.4, 2: -21.2, 3: -22.8, 4: -21.9})
    assert channel == 2
    assert selection["basis"] == "loudest-live"
    assert selection["measured_levels_db"]["CH1"] == -69.4


def test_all_silent_is_untranscribed_not_defaulted():
    """No live track means no program - never stream zero by default."""
    channel, selection = source_memory.select_program_track(
        {1: -70.1, 2: -69.8})
    assert channel is None
    assert selection["basis"] == "no-live-track"


def test_declaration_wins_and_a_dead_declaration_refuses():
    """`source.program_stream` names the mix; a dead one is refused."""
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


def test_build_frames_reuses_a_fresh_sample(tmp_path, memory_root, monkeypatch):
    """A second call does not re-decode a sample that still fingerprints
    to the live file - `reused: true` the same way `build_source` reuses
    a fresh transcript."""
    from library.tools import footage_identity

    media = _media(tmp_path, "TAKE.MXF", b"iframe-two")
    digest = footage_identity.fingerprint(str(media))["content_digest"]
    size = media.stat().st_size
    target = source_memory.source_dir(digest)
    source_memory.write_json(target / source_memory.SLOT_FRAMES_INDEX, {
        "content_digest": digest, "size_bytes": size, "frame_count": 1,
        "frames": [{"file": "frames/frame_000000.jpg", "t": 0.0}],
    })

    calls = {"n": 0}

    def fake_extract(source_file, out_dir, width, use_hwaccel):
        calls["n"] += 1
        return [("/fake/frame_000000.jpg", 0.0)], True

    monkeypatch.setattr(source_memory, "extract_iframes", fake_extract)
    account = source_memory.build_frames(str(media), digest)

    assert account["reused"] is True
    assert calls["n"] == 0


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
