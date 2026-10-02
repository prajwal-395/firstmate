"""The analysis-only run (`ren analyze`) and its path-portable export.

Each test names the defect it catches. Every project and memory root is
built under `tmp_path` (§8): no ffmpeg, no model, no real footage.
"""

import json
import os
from pathlib import Path

import pytest

from library.tools import footage_analysis, footage_identity, memory_export
from library.tools import source_memory


@pytest.fixture
def memory_root(tmp_path, monkeypatch):
    root = tmp_path / "memory"
    monkeypatch.setenv(source_memory.MEMORY_ROOT_ENV, str(root))
    return root


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _project_with_one_source(tmp_path: Path) -> tuple:
    media = tmp_path / "footage" / "CAM_A.MXF"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"frame" * (3 * 1024 * 1024 // 5 + 1))
    fp = footage_identity.fingerprint(str(media))
    project = tmp_path / "collection"
    _write(project / "pipeline_data.json", {"step_outputs": {"catalog": {
        "clip_catalog": [{"clip_id": "clip_001", "source_file": str(media),
                          "duration_seconds": 10.0}]}}})
    return str(project), str(media), fp["content_digest"], fp["size_bytes"]


def _transcript(digest: str, media: str, instrument: dict) -> dict:
    return {"content_digest": digest, "source_file": media,
            "status": source_memory.M1_STATUS_TRANSCRIBED,
            "utterances": [{"start": 1.0, "end": 2.0, "text": "hello there",
                            "words": [{"word": "hello", "start": 1.0, "end": 1.4},
                                      {"word": "there", "start": 1.5, "end": 2.0}],
                            "confidence": 0.0, "method": "hybrid-mfa"}],
            "utterance_count": 1, "word_count": 2, "speech_seconds": 1.0,
            "instrument": instrument}


def test_the_analysis_run_executes_no_edit_capability():
    """Defect: an 'analysis-only' run that plans, renders or segments.

    Object segmentation waits for edit plans and OCR has no reader, so
    both stay out by default; nothing downstream of preflight may be
    selected, whatever `--with` names.
    """
    from library.tools import capabilities, footage_intelligence
    default = footage_intelligence.compose(footage_intelligence.select())
    assert {capabilities.get(c).legacy.node_id for c in default} == {
        "scan", "catalog", "semantic_analysis", "temporal_index",
        "prosody_analysis"}
    with pytest.raises(footage_intelligence.RenRefusal):
        footage_intelligence.select(with_=["object_segmentation"])

    # Ordered by the capabilities' derived requires/effects, not by the
    # edit DAG's topology: a shuffled selection comes out producers first.
    shuffled = tuple(reversed(footage_intelligence.ROSTER))
    assert footage_intelligence.compose(shuffled)[:5] == (
        "footage.scan", "footage.catalog", "semantics.analyse",
        "temporal.index", "prosody.analyse")


def test_persons_measured_off_a_rebuilt_frame_sample_are_rebuilt(
        tmp_path, memory_root):
    """Defect: reusing M3 because its digest matches, after the M2 frames
    it was measured from were re-sampled (its faces index other frames)."""
    lane = next(l for l in footage_analysis.LANES if l.name == "persons")
    digest = "ab" * 32
    sdir = source_memory.source_dir(digest)
    _write(sdir / source_memory.SLOT_PERSONS, {"content_digest": digest})
    _write(sdir / source_memory.SLOT_FRAMES_INDEX, {"content_digest": digest})
    old = os.stat(sdir / source_memory.SLOT_FRAMES_INDEX).st_mtime - 60
    os.utime(sdir / source_memory.SLOT_PERSONS, (old, old))
    assert not footage_analysis.slot_is_fresh(lane, digest, None)

    os.utime(sdir / source_memory.SLOT_PERSONS, None)  # now newer than M2
    assert footage_analysis.slot_is_fresh(lane, digest, None)


def test_export_carries_no_local_path_and_no_default_confidence(
        tmp_path, memory_root):
    """Defects: a shared export that names this machine's folders, and a
    transcript confidence of 0.0 that no transcriber measured."""
    project, media, digest, size = _project_with_one_source(tmp_path)
    sdir = source_memory.source_dir(digest)
    _write(sdir / source_memory.SLOT_SOURCE, {
        "content_digest": digest, "size_bytes": size,
        "observed_paths": [media], "duration_seconds": 10.0})
    _write(sdir / source_memory.SLOT_TRANSCRIPT,
           _transcript(digest, media, {"arm": "hybrid", "aligner": "mfa"}))

    portable, local = memory_export.build_export(project)
    text = json.dumps(portable)
    assert media not in text and str(tmp_path) not in text
    asset = portable["assets"][0]
    assert asset["asset_id"] == f"sha256:{digest}"
    assert asset["transcript"]["confidence"] is None
    assert asset["transcript"]["utterances"][0]["words"][0] == ["hello", 1.0, 1.4]
    # A slot nobody built is absent, not an empty measurement.
    assert asset["persons"] == {"status": "absent"}
    assert asset["status"] == "partial"
    assert local["assets"][f"sha256:{digest}"]["observed_path"] == media


def test_export_refuses_a_record_that_grows_a_path_field(tmp_path, memory_root):
    """Defect: a lane adds a path-bearing field to its record and the
    export ships it - the check must fail the export, not the reader."""
    project, media, digest, size = _project_with_one_source(tmp_path)
    sdir = source_memory.source_dir(digest)
    _write(sdir / source_memory.SLOT_SOURCE, {
        "content_digest": digest, "size_bytes": size, "duration_seconds": 10.0})
    _write(sdir / source_memory.SLOT_TRANSCRIPT, _transcript(
        digest, media, {"arm": "hybrid", "wav": media + ".ch1.wav"}))

    with pytest.raises(memory_export.PathLeak, match="instrument"):
        memory_export.build_export(project)


def test_a_clock_measured_after_every_transcript_is_not_rebuilt(memory_root):
    """Defect: re-analysing the bare folder rebuilds M6 from a collection
    with no Resolve timeline, overwriting the recorded cross-check with null.
    A transcript that lands after the clock still forces the rebuild."""
    a, b = "a1" * 32, "b2" * 32
    sources = [{"clip_id": "clip_001", "digest": a},
               {"clip_id": "clip_002", "digest": b}]
    for digest in (a, b):
        sdir = source_memory.source_dir(digest)
        _write(sdir / source_memory.SLOT_TRANSCRIPT, {"content_digest": digest})
        then = os.stat(sdir / source_memory.SLOT_TRANSCRIPT).st_mtime - 60
        os.utime(sdir / source_memory.SLOT_TRANSCRIPT, (then, then))
    _write(source_memory.source_dir(a) / source_memory.SLOT_CLOCK,
           {"content_digest": a})  # b is in no group: no record, by design
    assert footage_analysis.clock_is_fresh(sources, None)

    os.utime(source_memory.source_dir(b) / source_memory.SLOT_TRANSCRIPT, None)
    later = os.stat(source_memory.source_dir(b)
                    / source_memory.SLOT_TRANSCRIPT).st_mtime + 5
    os.utime(source_memory.source_dir(b) / source_memory.SLOT_TRANSCRIPT,
             (later, later))
    assert not footage_analysis.clock_is_fresh(sources, None)
