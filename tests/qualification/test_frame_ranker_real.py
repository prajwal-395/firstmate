"""The frame ranker's production path, run with the REAL weights.

The unit tests (``tests/unit/picture/test_picture_quality.py``) pin the
two bounds - the fail-closed sharpness gate and the hold-point exclusion -
with stub scorers.  This tier is the other half: it drives
``frame_ranker.extract_ranked_clip_thumbnail`` end to end with the real
CLIP backbone and the real LAION head, so the wiring (probe -> extract ->
score -> sharpness -> choose) is exercised against the 1.6 GB of weights
that will actually run, not a stub.

It SKIPS when the weights cannot load (no torch/transformers, no network,
no head) - a running environment is named, never silently skipped past.
The aesthetic verdict on real footage is ``docs/evidence/frame_ranker.md``;
this test asserts the machinery, not the taste.
"""
import os
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.real_model

pytest.importorskip("torch")
pytest.importorskip("transformers")


def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@pytest.fixture(scope="module")
def real_scorer():
    """The shipped scorer, or SKIP naming why it cannot load."""
    from library.tools import frame_ranker

    if not _have_ffmpeg():
        pytest.skip("ffmpeg/ffprobe not on PATH")
    try:
        head = frame_ranker.download_head(
            os.path.join(os.environ.get("XDG_CACHE_HOME",
                                        os.path.expanduser("~/.cache")),
                         "vep", "frame-ranker"))
        return frame_ranker.LaionAestheticScorer(head).load()
    except frame_ranker.FrameRankerUnavailable as exc:
        pytest.skip(f"real aesthetic weights unavailable: {exc}")


@pytest.fixture(scope="module")
def synthetic_clip(tmp_path_factory):
    """A short moving test pattern - enough distinct frames to rank."""
    if not _have_ffmpeg():
        pytest.skip("ffmpeg/ffprobe not on PATH")
    clip = tmp_path_factory.mktemp("frame-ranker") / "synth.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc2=duration=10:size=960x540:rate=30",
         "-pix_fmt", "yuv420p", str(clip)],
        capture_output=True, timeout=120, check=True,
    )
    return str(clip)


def test_extract_ranked_clip_thumbnail_runs_the_real_weights(
        real_scorer, synthetic_clip, tmp_path):
    """The production path with real weights: probe, extract, score,
    sharpness-gate, choose - and the winner is a sharp frame that was
    actually measured, not a default."""
    from library.tools import frame_ranker
    from library.tools.analysis import picture_quality as _pq

    work = tmp_path / "frames"
    result = frame_ranker.extract_ranked_clip_thumbnail(
        synthetic_clip, real_scorer, work_dir=str(work))

    assert result["duration"] > 0
    assert result["legacy_timestamp"] == frame_ranker.legacy_timestamp(
        result["duration"])
    # the legacy pick is a candidate, so the old rule stays in the run
    legacy_ts = result["legacy_timestamp"]
    assert any(c["timestamp"] == legacy_ts
               for c in result["candidates"]), "legacy pick not a candidate"
    # every candidate was really scored and really measured
    for cand in result["candidates"]:
        assert cand["aesthetic_score"] is not None
        assert cand["sharpness"] is not None
        assert cand["sharpness"] >= _pq.SHARPNESS_ABSOLUTE_FLOOR
        assert os.path.isfile(cand["path"])
    # the winner is the highest-scoring eligible candidate, and it is
    # never the legacy pick unless legacy genuinely won
    winner = result["winner"]
    assert winner is not None, "a 10s test pattern must yield a winner"
    assert winner["sharpness"] >= _pq.SHARPNESS_ABSOLUTE_FLOOR
    best = max(result["candidates"], key=lambda c: c["aesthetic_score"])
    assert winner["id"] == best["id"]
    assert result["fallback"] is None
    assert result["report"]["reason"] == (
        "highest_aesthetic_score_above_sharpness_floor")


def test_a_clip_nobody_can_rank_falls_back_to_the_legacy_timestamp(
        real_scorer, tmp_path):
    """Fail-closed on the real path too: an unprobeable clip raises
    rather than returning a partial ranking, and a clip whose every
    candidate is below the floor yields no winner and names the legacy
    fallback.  The second half is driven through ``choose`` with real
    measured sharpness values, since manufacturing a genuinely all-soft
    real clip is not worth the ffmpeg gymnastics."""
    from library.tools import frame_ranker

    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    with pytest.raises(frame_ranker.FrameRankerUnavailable):
        frame_ranker.extract_ranked_clip_thumbnail(
            str(empty), real_scorer, work_dir=str(tmp_path / "w"))

    winner, report = frame_ranker.choose([
        {"id": "soft", "timestamp": 1.0,
         "aesthetic_score": 5.9, "sharpness": 10.0},
    ])
    assert winner is None
    assert report["reason"] == "no_candidate_passed_sharpness_gate"
