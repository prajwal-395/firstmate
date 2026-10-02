"""Single-track diarization fallback: routing, turns, voice prints.

Each test names a defect it would catch:

- the fallback taken when per-ISO tracks exist (multi-path timelines
  must keep the unchanged per-ISO path);
- the fallback taken for a declared monologue, or a declared roster's
  count not reaching the clusterer;
- the fallback skipped by `--no-diarize-single-track`;
- union-run seams double-claimed (the eval measured this before the
  truncation existed);
- the k search accepting a singleton collapse;
- a segment voice print averaged over the wrong windows;
- a rebind dropping or corrupting the new `voice_embedding` field.

ffmpeg is real here; the fixtures are tiny generated tones. The ML
stack is NOT: embeddings are small synthetic arrays and `diarize_track`
is monkeypatched to raise when the path under test must not hear audio.
"""

from __future__ import annotations

import itertools
import shutil
import subprocess
import types
from pathlib import Path

import numpy as np
import pytest

from library.tools import single_track_diarization as std
from library.tools import timeline_transcript as tt
from library.tools.timeline_ingest import TimelineClip

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe are required; CI installs them (AGENTS.md 9)")

FPS = 24000 / 1001


def _tone(path: Path, seconds: float, freq: int = 440) -> Path:
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
         "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(path)],
        check=True)
    return path


def _clip(source, src_in, src_out, tl_start, tl_end, uid="uid",
          speaker="A"):
    return TimelineClip(
        resolve_item_id=uid, track_type="video", track_index=1,
        track_name=speaker, speaker=speaker, source_file=str(source),
        source_in=src_in, source_out=src_out,
        source_in_frame=round(src_in * FPS),
        source_out_frame=round(src_out * FPS),
        source_frames=None,
        timeline_start=tl_start, timeline_end=tl_end, name="clip")


def _snapshot(clips):
    return types.SimpleNamespace(
        picture_clips=lambda: list(clips),
        clips=list(clips),
        project_name="probe",
        timeline_name="probe-timeline",
        fps=FPS,
        duration=max((c.timeline_end for c in clips), default=0.0))


def _aligned(text, start, end, words=4):
    step = (end - start) / words
    return {"segments": [{
        "text": text, "start": start, "end": end,
        "words": [{"word": f"w{i}", "start": start + i * step,
                   "end": start + (i + 1) * step, "timed": True}
                  for i in range(words)]}]}


def _refuse_diarize(*args, **kwargs):
    raise AssertionError("fallback taken when it should not be")


# ── routing: the fallback taken when it should not be ────────────────

def _refuse_missing(*args, **kwargs):
    raise std.DiarizationUnavailable("no checkout")


# (case, project.yaml, clip speakers, diarize flag, diarizer stub,
#  expected path, reason fragment)
SINGLE_LABEL_ROUTES = [
    # Two timeline speakers never reach the diarizer, even with no
    # declared roster: the per-ISO path owns multi-track timelines.
    ("two audio paths", None, ["Akshita", "Craig"], True, _refuse_diarize,
     "per-iso", None),
    # One path plus a one-name roster keeps the single label: diarizing a
    # declared monologue could only split one voice into invented speakers.
    ("declared monologue", "source:\n  speakers:\n    - {name: Craig}\n",
     [None], True, _refuse_diarize, "single-label", None),
    # `--no-diarize-single-track` holds even an eligible timeline.
    ("opt-out flag", None, [None], False, _refuse_diarize, "single-label",
     "no-diarize-single-track"),
    # No weights on the machine reads as single-label with the reason on
    # the record - never a crash, never silence.
    ("encoder unavailable", None, [None], True, _refuse_missing,
     "single-label", "unavailable"),
]


def test_the_fallback_is_not_taken_where_it_must_not_be(tmp_path,
                                                        monkeypatch):
    for (case, yaml_text, speakers, diarize, stub, path,
         reason) in SINGLE_LABEL_ROUTES:
        folder = tmp_path / case.replace(" ", "_")
        folder.mkdir()
        if yaml_text:
            (folder / "project.yaml").write_text(yaml_text, encoding="utf-8")
        src = _tone(folder / "src.wav", 2.0)
        span = 2.0 / len(speakers)
        clips = [_clip(src, 0.0, span, i * span, (i + 1) * span,
                       uid=f"u{i}", speaker=who)
                 for i, who in enumerate(speakers)]
        monkeypatch.setattr(std, "diarize_track", stub)
        monkeypatch.setattr(
            tt, "transcribe_audio",
            lambda p, span=span, **kw: (
                _aligned("hello", 0.0, span, 2), {"arm": "test"}))
        document = tt.build_and_transcribe(
            str(folder), _snapshot(clips), diarize=diarize)
        assert document["diarization"]["path"] == path, case
        assert document["segments_with_voice_embedding"] == 0, case
        assert document["segment_count"] > 0, case
        if reason:
            assert reason in document["diarization"]["reason"], case
        if case == "two audio paths":
            assert sorted(document["speakers"]) == ["Akshita", "Craig"]


def test_declared_roster_count_reaches_k(tmp_path, monkeypatch):
    """A two-name roster on one mic is the case the fallback exists for,
    and its declared count must reach the clusterer: estimating k when
    the project already said it would throw away the stronger signal."""
    (tmp_path / "project.yaml").write_text(
        "source:\n  speakers:\n    - {name: Akshita}\n    - {name: Craig}\n",
        encoding="utf-8")
    src = _tone(tmp_path / "src.wav", 2.0)
    clips = [_clip(src, 0.0, 2.0, 0.0, 2.0, uid="a", speaker=None)]
    asked = []

    def _diarize(*args, **kwargs):
        asked.append(kwargs.get("num_speakers"))
        return _canned_diarization()

    monkeypatch.setattr(std, "diarize_track", _diarize)
    monkeypatch.setattr(
        tt, "transcribe_audio",
        lambda path, **kw: (_aligned("hello", 0.0, 1.0, 2), {"arm": "test"}))
    document = tt.build_and_transcribe(str(tmp_path), _snapshot(clips))
    assert asked == [2]
    assert document["diarization"]["path"] == "single-track-fallback"


def _canned_diarization():
    return std.Diarization(
        clusters=[
            std.SpeakerCluster(label="speaker_01", turns=[(0.0, 1.0)],
                               centroid=(1.0, 0.0, 0.0, 0.0)),
            std.SpeakerCluster(label="speaker_02", turns=[(1.0, 2.0)],
                               centroid=(0.0, 1.0, 0.0, 0.0))],
        window_embeddings=np.array([[1.0, 0.0, 0.0, 0.0],
                                    [0.6, 0.8, 0.0, 0.0],
                                    [0.0, 1.0, 0.0, 0.0]]),
        window_starts=np.array([0.0, 0.5, 1.0]),
        window_labels=[0, 0, 1],
        speakers_estimated=2,
        inference_seconds=0.1,
        method="test",
        weights="test")


def test_eligible_single_path_diarizes_and_prints_voices(tmp_path,
                                                         monkeypatch):
    """One path, no roster: clusters transcribe separately and every row
    carries its voice print for the person-entity task."""
    src = _tone(tmp_path / "src.wav", 2.0)
    clips = [_clip(src, 0.0, 2.0, 0.0, 2.0, uid="a", speaker=None)]
    monkeypatch.setattr(std, "diarize_track",
                        lambda *a, **k: _canned_diarization())

    def _hear(path, **kw):
        label = kw.get("label", "")
        if label == "speaker_01":
            return _aligned("first voice", 0.0, 1.0, 2), {"arm": "test"}
        return _aligned("second voice", 1.0, 2.0, 2), {"arm": "test"}

    monkeypatch.setattr(tt, "transcribe_audio", _hear)
    document = tt.build_and_transcribe(str(tmp_path), _snapshot(clips))
    assert document["diarization"]["path"] == "single-track-fallback"
    assert document["diarization"]["speakers_estimated"] == 2
    assert document["speakers"] == ["speaker_01", "speaker_02"]
    assert (document["segments_with_voice_embedding"]
            == document["segment_count"] > 0)
    first = next(s for s in document["segments"]
                 if s["speaker"] == "speaker_01")
    # Windows [0.0, 1.5] and [0.5, 2.0] overlap the (0, 1) span: the
    # print is their normalized mean, not one window's.
    assert first["voice_embedding"][:2] == pytest.approx([0.8944, 0.4472],
                                                         abs=0.01)
    assert len(first["voice_embedding"]) == 4


# ── turns: the seam a dropped window leaves ──────────────────────────

def test_merge_runs_never_double_claims_a_seam():
    """A VAD-dropped window splits runs but both 1.5 s windows still
    cover the seam, and a speaker change overlaps the same way: without
    truncation the hypothesis claims the seam twice and every overlap
    counts double downstream. The earlier tail wins; the later turn
    starts where it ends."""
    cases = [
        ([0.0, 0.5, 1.0, 2.0, 2.5], [0, 0, 0, 0, 0],
         [(0.0, 2.5, 0), (2.5, 4.0, 0)]),
        ([0.0, 0.5, 1.0, 1.5], [0, 0, 1, 1],
         [(0.0, 2.0, 0), (2.0, 3.0, 1)]),
    ]
    for starts, labels, expected in cases:
        turns = std.merge_runs_to_turns(np.array(starts), labels)
        assert turns == expected
        for (first_start, first_end, _), (second_start, _, _) in (
                itertools.pairwise(turns)):
            assert first_start < first_end
            assert second_start >= first_end


# ── voice prints and the k search, without the ML stack ──────────────

def test_k_search_skips_singleton_collapse():
    """Two tight voices plus a silhouette scan must return 2, not a
    k whose cluster holds one window: the guard is what makes k=3
    unparsable input rather than a wrong answer."""
    rng = np.random.RandomState(7)
    dim = 8
    center_a = np.eye(dim)[0]
    center_b = np.eye(dim)[1]
    embeddings = np.array(
        [center_a + rng.normal(0, 0.01, dim) for _ in range(4)]
        + [center_b + rng.normal(0, 0.01, dim) for _ in range(4)])
    embeddings = embeddings / np.linalg.norm(embeddings, axis=1,
                                             keepdims=True)
    k, scores = std.estimate_speaker_count(embeddings, k_max=4)
    assert k == 2
    assert 3 not in scores


def test_segment_embeddings_average_member_windows():
    """A span's print is the normalized mean of its overlapping
    windows; a span in a VAD gap takes the nearest cluster's print -
    never a zero vector, which would read as a voice."""
    diarization = _canned_diarization()
    prints = std.segment_voice_embeddings([(0.0, 1.0), (1.0, 2.0)],
                                          diarization)
    # 1.5 s windows overlap generously: span (0, 1) hears windows 0.0
    # and 0.5, span (1, 2) hears all three (window 0.0 still covers
    # [1.0, 1.5]). Both prints are the normalized mean of what actually
    # overlapped.
    assert prints[0][:2] == pytest.approx([0.8944, 0.4472], abs=0.01)
    assert prints[1][:2] == pytest.approx([0.6644, 0.7474], abs=0.01)
    # A span in a VAD gap takes the nearest cluster's print - the
    # speaker_02 turn (1, 2) outranks speaker_01's (0, 1) past 1.5 s.
    gapped = std.segment_voice_embeddings([(5.0, 6.0)], diarization)
    assert gapped == [diarization.clusters[1].centroid]


def test_rebind_keeps_voice_prints(tmp_path):
    """A rebind must carry the measured prints through untouched: it
    re-derives bindings, never measurements. A row without one keeps
    None rather than gaining an empty tuple."""
    snapshot = _snapshot([])
    document = {
        "segments": [{
            "speaker": "speaker_01", "text": "hi",
            "timeline_start": 0.0, "timeline_end": 1.0,
            "source_file": "/m/a.MXF", "source_start": 0.0,
            "source_end": 1.0, "resolve_item_id": "clip-a",
            "words": [{"word": "hi", "start": 0.0, "end": 0.5,
                       "timed": True}],
            "read_from_words": False, "avg_logprob": None,
            "voice_embedding": [0.6, 0.8, 0.0, 0.0]},
            {"speaker": "speaker_01", "text": "old",
             "timeline_start": 2.0, "timeline_end": 3.0,
             "source_file": "/m/a.MXF", "source_start": 2.0,
             "source_end": 3.0, "resolve_item_id": "clip-b",
             "words": [{"word": "old", "start": 2.0, "end": 2.5,
                        "timed": True}],
             "read_from_words": False, "avg_logprob": None}],
        "transcription": {}, "mic_bleed_resolution": []}
    rebound = tt.rebind_document(document, snapshot)
    kept = {s["text"]: s for s in rebound["segments"]}
    assert kept["hi"]["voice_embedding"] == [0.6, 0.8, 0.0, 0.0]
    assert kept["old"]["voice_embedding"] is None
