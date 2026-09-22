"""Adopting the LAION aesthetic ranker behind its sharpness gate.

FIRSTMATE VERDICT 2026-09-18 over the spike's "do not adopt": pairs 1 and
3 show a clear win in the same direction, and what the ranker replaces is
a systematically bad rule (about one second into a clip, reliably
mid-movement) rather than a good one.  The spike's measurements all stand
- these tests reuse its own numbers, never re-derived ones:

- blur-blindness: Laplacian variance 65 outscored a sharp pipeline pick
  4.55 to 4.09 - so composition alone must never choose a frame;
- hold-point noise: +0.04 over the current hold, preferring the softer
  frame - so the ranker stays out of the ending-freeze path entirely.

No test here runs the real head (1.6 GB, CPU-heavy - the captain's
standing rule pauses exactly that work): scorers, extraction and
sharpness are all stubs.  Nothing here touches ffmpeg, renders,
transcription or a real project - every path builds under `tmp_path`.
"""

from pathlib import Path

from library.tools import frame_ranker
from library.tools import thumbnail_extractor


# ── Bound 1: a blurred frame cannot win on composition alone ───────────

def test_blurred_frame_cannot_win_on_composition_alone():
    """The spike's own finding 2, wired in: 4.55 @ sharpness 65 loses to
    4.09 @ sharpness 322."""
    winner, report = frame_ranker.choose([
        {"id": "blurred", "timestamp": 14.1,
         "aesthetic_score": 4.55, "sharpness": 65.0},
        {"id": "sharp", "timestamp": 98.5,
         "aesthetic_score": 4.09, "sharpness": 322.0},
    ])
    assert winner["id"] == "sharp"
    assert report["reason"] == "highest_aesthetic_score_above_sharpness_floor"


def test_all_blurred_means_no_winner_not_a_soft_winner():
    winner, report = frame_ranker.choose([
        {"id": "a", "timestamp": 1.0,
         "aesthetic_score": 4.55, "sharpness": 65.0},
        {"id": "b", "timestamp": 2.0,
         "aesthetic_score": 4.57, "sharpness": 60.0},
    ])
    assert winner is None
    assert report["reason"] == "no_candidate_passed_sharpness_gate"


def test_unknown_sharpness_cannot_win():
    """Unmeasured is ineligible, not "probably fine": the gate is
    fail-closed on missing signal too."""
    winner, _ = frame_ranker.choose([
        {"id": "unknown", "timestamp": 50.0,
         "aesthetic_score": 9.99, "sharpness": None},
        {"id": "measured", "timestamp": 60.0,
         "aesthetic_score": 3.00, "sharpness": 300.0},
    ])
    assert winner["id"] == "measured"


def test_tie_breaks_to_earliest_timestamp():
    winner, _ = frame_ranker.choose([
        {"id": "late", "timestamp": 90.0,
         "aesthetic_score": 5.0, "sharpness": 300.0},
        {"id": "early", "timestamp": 10.0,
         "aesthetic_score": 5.0, "sharpness": 300.0},
    ])
    assert winner["id"] == "early"


# ── The replaced rule, stated once and kept as fallback ───────────────

def test_legacy_timestamp_matches_the_old_rule():
    assert frame_ranker.legacy_timestamp(225.0) == 1.0
    assert frame_ranker.legacy_timestamp(5.0) == 0.5
    assert frame_ranker.legacy_timestamp(0) == 1.0


def test_candidates_include_the_legacy_pick_and_stay_in_clip():
    times = frame_ranker.candidate_timestamps(225.0)
    assert times == sorted(times)
    assert all(0.0 <= t <= 225.0 for t in times)
    assert 1.0 in times
    assert len(times) == frame_ranker.N_THUMBNAIL_CANDIDATES


# ── The real scorer fails loudly, never silently ──────────────────────

def test_unloaded_scorer_raises_instead_of_scoring_silently():
    scorer = frame_ranker.LaionAestheticScorer("/nonexistent/head.pth")
    try:
        scorer.score_files(["/nonexistent/frame.jpg"])
    except frame_ranker.FrameRankerUnavailable:
        return
    raise AssertionError("an unloaded scorer must refuse, not score")


# ── Wiring: the ranker reaches thumbnails, the gate travels with it ───

class _RisingScorer:
    """Prefers later candidates - a stand-in for "the ranker disagrees
    with the timestamp rule", which is the whole point of the adoption."""

    def score_files(self, paths):
        return [float(i) for i, _ in enumerate(paths)]


def _stub_extraction(monkeypatch, calls):
    def fake_extract(video_path, output_path, timestamp_s=1.0, width=320):
        calls.append(round(float(timestamp_s), 3))
        Path(output_path).write_bytes(
            f"frame@{round(float(timestamp_s), 3)}".encode("utf-8"))
        return True

    monkeypatch.setattr(thumbnail_extractor, "extract_thumbnail",
                        fake_extract)


def test_ranked_thumbnail_promotes_the_sharp_winner(tmp_path, monkeypatch):
    calls = []
    _stub_extraction(monkeypatch, calls)
    url = thumbnail_extractor.extract_ranked_clip_thumbnail(
        str(tmp_path), "clip_1", "/nonexistent/src.mov", 225.0,
        _RisingScorer(), sharpness_fn=lambda path: 300.0)
    assert url == "/thumbnails/clip_1.jpg"
    # The ranker judged a spread, not one timestamp - and judged only:
    # a winner means no legacy fallback extraction ran.
    assert len(calls) == frame_ranker.N_THUMBNAIL_CANDIDATES
    # ... and the canonical file is the LAST candidate's frame: the
    # rising scorer's winner, sharp throughout.
    canonical = (tmp_path / "pipeline_output" / "thumbnails" / "clip_1.jpg")
    assert canonical.read_bytes() == f"frame@{calls[-1]}".encode("utf-8")
    # Losers are deleted, never served.
    leftovers = [p.name for p in canonical.parent.iterdir()]
    assert leftovers == ["clip_1.jpg"]


def test_ranked_thumbnail_falls_back_when_nothing_is_sharp(
        tmp_path, monkeypatch):
    calls = []
    _stub_extraction(monkeypatch, calls)
    url = thumbnail_extractor.extract_ranked_clip_thumbnail(
        str(tmp_path), "clip_1", "/nonexistent/src.mov", 225.0,
        _RisingScorer(), sharpness_fn=lambda path: 10.0)
    assert url == "/thumbnails/clip_1.jpg"
    # Nine judged extractions, then the legacy fallback at 1.0 s.
    assert calls[-1] == 1.0


def test_ranked_thumbnail_falls_back_when_scorer_is_unusable(
        tmp_path, monkeypatch):
    calls = []
    _stub_extraction(monkeypatch, calls)
    url = thumbnail_extractor.extract_ranked_clip_thumbnail(
        str(tmp_path), "clip_1", "/nonexistent/src.mov", 225.0,
        frame_ranker.LaionAestheticScorer("/nonexistent/head.pth"),
        sharpness_fn=lambda path: 300.0)
    assert url == "/thumbnails/clip_1.jpg"
    assert calls[-1] == 1.0


def test_catalog_without_scorer_keeps_the_legacy_rule(tmp_path, monkeypatch):
    """Default path byte-identical to before the adoption: one extraction
    at min(1.0, duration * 0.1)."""
    calls = []
    _stub_extraction(monkeypatch, calls)
    src = tmp_path / "LC4930.MXF"
    src.write_bytes(b"fake-video")
    out = thumbnail_extractor.extract_thumbnails_for_catalog(
        str(tmp_path),
        [{"clip_id": "c1", "filepath": str(src), "duration_s": 225.0}])
    assert out == {"c1": "/thumbnails/c1.jpg"}
    assert calls == [1.0]


# ── Bound 2: the ranker is not reachable from the hold-point path ─────

def test_ranker_is_not_reachable_from_the_hold_point_path():
    """The Reel 30 drift (+0.04, preferring the softer frame) stays out
    of this module's reach: the ending freeze hold and the named-frame
    grabs must never import the ranker.  Static on purpose - an import is
    the wiring, wherever it hides."""
    hold_point_owners = [
        "library/tools/reel_ending.py",
        "library/tools/reel_build.py",
        "library/tools/gate_stills.py",
        "library/steps/step_7_02_verify_reels/step.py",
    ]
    for rel in hold_point_owners:
        source = (Path(__file__).resolve().parents[1] / rel).read_text(
            encoding="utf-8")
        assert "frame_ranker" not in source, rel
