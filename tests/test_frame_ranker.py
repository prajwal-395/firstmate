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


# ── The replaced rule, stated once and kept as fallback ───────────────


# ── The real scorer fails loudly, never silently ──────────────────────

def test_unloaded_scorer_raises_instead_of_scoring_silently():
    scorer = frame_ranker.LaionAestheticScorer("/nonexistent/head.pth")
    try:
        scorer.score_files(["/nonexistent/frame.jpg"])
    except frame_ranker.FrameRankerUnavailable:
        return
    raise AssertionError("an unloaded scorer must refuse, not score")


# ── The transformers 5.x boundary ────────────────────────────────────


def test_feature_normalisation_handles_pooler_output():
    """`get_image_features` returns BaseModelOutputWithPooling from
    transformers 5.x up, and calling `.norm` on that raises AttributeError
    (measured 2026-09-30 against 5.15.0) - which is what `score_files` did
    until this helper unwrapped it."""

    class _Tensor:
        def norm(self, p=2, dim=-1, keepdim=True):
            return self

        def __truediv__(self, other):
            return "NORMALIZED"

    class _FiveX:
        """5.x shape: no .norm, carries pooler_output."""

    five = _FiveX()
    five.pooler_output = _Tensor()
    assert frame_ranker._normalized_clip_features(five) == "NORMALIZED"
    assert frame_ranker._normalized_clip_features(_Tensor()) == "NORMALIZED"


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
