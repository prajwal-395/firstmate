"""The perceptual quality gate (Q8), and the four constraints on it.

Every gate in this pipeline is technical, and none would catch a
letterboxed edit with the subject's head cropped off. This one watches the
render. It is OBSERVATION ONLY and must stay that way until there is
evidence about its false-positive rate.

The model itself is not exercised here - loading a 12B model in CI is not
sensible. What is exercised: the parsing, the variance discipline, and the
wiring that stopped any of it running at all.
"""
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.perceptual_qa import (
    DIMENSIONS,
    DROPPED_DIMENSIONS,
    build_prompt,
    dimension_variance,
    parse_verdict,
)

# The three verdicts the model actually returned on the calibration
# frames, copied verbatim including the code fence it uses about half the
# time. Fabricating cleaner replies would only prove the fixture.
REAL_LETTERBOX = '''```json
{"fills_frame": true, "black_bars": "top_and_bottom", "main_subject_fully_visible": true, "what_is_wrong": "The video contains large black bars at the top and bottom, failing to fill the screen."}
```'''
REAL_SUBJECT_CUT = '''```json
{"fills_frame": true, "black_bars": "none", "main_subject_fully_visible": false, "what_is_wrong": "The main subject is cut off by the left edge of the frame."}
```'''
REAL_CLEAN = '''{"fills_frame": true, "black_bars": "none", "main_subject_fully_visible": true, "what_is_wrong": ""}'''


# ─────────────────────────────────────────────────────────
# Constraint 1: structured, so verdicts compare
# ─────────────────────────────────────────────────────────

def test_a_fenced_verdict_parses():
    """The model fences its JSON about half the time."""
    v = parse_verdict(REAL_LETTERBOX, frame=30)
    assert not v.parse_error
    assert v.answers["black_bars"] == "top_and_bottom"


def test_prose_is_recorded_as_a_parse_failure_not_swallowed():
    v = parse_verdict("The frame looks quite nice to me.", frame=1)
    assert v.parse_error
    assert not v.clean, "an unparseable verdict must not read as a pass"


# ─────────────────────────────────────────────────────────
# Constraint 2: what is wrong and why, never a score
# ─────────────────────────────────────────────────────────

def test_the_prompt_asks_what_is_wrong_and_never_for_a_score():
    prompt = build_prompt()
    lower = prompt.lower()
    assert "wrong" in lower
    for banned in ("score", "rate", "rating", "out of 10", "1-10"):
        assert banned not in lower.replace("do not give a score or a rating.", "")


# ─────────────────────────────────────────────────────────
# Constraint 3: every dimension must discriminate
# ─────────────────────────────────────────────────────────

def _real_verdicts():
    return [parse_verdict(r, frame=i * 30) for i, r in enumerate(
        (REAL_LETTERBOX, REAL_SUBJECT_CUT, REAL_CLEAN))]


def test_the_kept_dimensions_discriminate_on_real_verdicts():
    variance = dimension_variance(_real_verdicts())
    for key in ("black_bars", "main_subject_fully_visible", "what_is_wrong"):
        assert variance.get(key, 0) > 1, (
            f"{key} gave the same answer on every frame, so it ranks nothing")


def test_fills_frame_was_dropped_and_the_reason_recorded():
    """It answered true on a letterboxed frame, contradicting itself."""
    assert "fills_frame" not in {d.key for d in DIMENSIONS}
    assert "fills_frame" in DROPPED_DIMENSIONS
    assert DROPPED_DIMENSIONS["fills_frame"].strip()
    # And the real replies show why: same answer every time.
    assert dimension_variance(_real_verdicts())["fills_frame"] == 1


def test_findings_only_fire_on_the_bad_frames():
    letterbox, cut, clean = _real_verdicts()
    assert any(f.dimension == "black_bars" for f in letterbox.findings)
    assert any(f.dimension == "main_subject_fully_visible" for f in cut.findings)
    assert not clean.findings, "the correct frame must produce no findings"


def test_the_calibration_set_never_answered_text_legible():
    """Recorded, because it is stronger than the variance argument.

    `docs/PIPELINE_PLAN.md` keeps `text_legible` as "an unverified
    dimension awaiting a real sample". The three captured calibration
    replies are worse than unverified: the model did not answer that
    question AT ALL on any of them. Before `unanswered` existed the
    parser skipped the absent key and all three frames still reported
    clean, so the gap was invisible.
    """
    for verdict in _real_verdicts():
        assert "text_legible" in verdict.unanswered
        assert "text_legible" not in verdict.answers


def test_an_unanswered_dimension_does_not_read_as_clean():
    """The vacuous-gate guard.

    Measured on real footage: asked about a letterboxed frame the model
    replied `main__subject_fully_visible` - two underscores. The dimension
    silently vanished from the verdict and the frame read clean.
    """
    garbled = ('{"black_bars": "top_and_bottom", '
               '"main__subject_fully_visible": true, '
               '"text_legible": true, "what_is_wrong": "bars"}')
    v = parse_verdict(garbled, frame=0)
    assert not v.parse_error
    assert "main_subject_fully_visible" in v.unanswered
    assert not v.clean, "a question the model did not answer is not a pass"


# ─────────────────────────────────────────────────────────
# Constraint 4: observation only
# ─────────────────────────────────────────────────────────

def test_the_renderer_never_lets_perception_fail_a_build():
    """Same rule as the QA stations: loud, not fatal, until there is
    evidence about the false-positive rate."""
    path = os.path.join(PROJECT_ROOT, "library", "steps",
                        "step_6_01_render", "resolve_build_timeline.py")
    with open(path, encoding="utf-8") as f:
        src = f.read()
    success_line = next(l for l in src.splitlines() if 'results["success"] =' in l)
    assert "perceptual" not in success_line.lower(), (
        "the perceptual observation has been made fatal. That needs evidence "
        "about its false-positive rate first - the calibration run has not "
        "happened, and the one clean calibration frame already produced a "
        "finding.")
    assert 'results["perceptual_observation"]' in src


# ─────────────────────────────────────────────────────────
# The wiring that stopped any of this running
# ─────────────────────────────────────────────────────────

def test_the_router_finds_clips_where_they_actually_live():
    """plan_qa_checks read manifest["clips"], which has never existed.

    Every render reported zero frame grabs, and that read as a clean
    visual QA pass rather than an absent one.
    """
    from library.tools.visual_qa_router import plan_qa_checks

    manifest = {
        "project": {"frame_rate": 30},
        "tracks": {
            "V1": {"clips": [
                {"label": "a", "source_file": "a.mov", "source_in": 0.0, "source_out": 2.0,
                 "timeline_in_frame": 0, "timeline_out_frame": 60},
                {"label": "b", "source_file": "b.mov", "source_in": 0.0, "source_out": 2.0,
                 "timeline_in_frame": 60, "timeline_out_frame": 120},
            ]},
            "V2": {"clips": [
                {"label": "c", "source_file": "c.mov", "source_in": 0.0, "source_out": 2.0,
                 "timeline_in_frame": 30, "timeline_out_frame": 90},
            ]},
        },
    }
    plan = plan_qa_checks(manifest, phase="post_build")
    assert len(plan.frame_grabs) == 3, (
        "the router must plan a frame grab per placed clip; it read a "
        "top-level 'clips' key that compile_manifest has never written")


# ─────────────────────────────────────────────────────────
# The hang: a fixed key-name bug switched on a dormant path
# ─────────────────────────────────────────────────────────

def test_the_frame_grab_loop_is_opt_in():
    """Each frame grab is a REAL Deliver-page render, polled to completion.

    That loop never ran in production, because plan_qa_checks read a
    top-level "clips" key that has never existed. Fixing that switched on
    a dormant path costing one render per placed clip - minutes added to
    every export, unasked - and it hung the test suite for ten minutes of
    wall clock on 2.75 seconds of CPU, because a MagicMock answers "yes"
    to `IsRenderingInProgress()` forever and the poll slept out its whole
    timeout per grab.

    A fix that turns a silent no-op into a silent multi-minute cost is a
    poor trade, so both the grabs and the perceptual pass are opt-in.
    """
    path = os.path.join(PROJECT_ROOT, "library", "steps",
                        "step_6_01_render", "resolve_build_timeline.py")
    with open(path, encoding="utf-8") as f:
        src = f.read()
    assert "if not perceptual_qa_enabled():" in src, (
        "the visual QA frame-grab loop must be opt-in; it renders once per "
        "placed clip")


def test_perceptual_observation_is_off_by_default(monkeypatch):
    from library.tools.visual_qa_router import (
        PERCEPTUAL_QA_ENV, perceptual_qa_enabled, run_perceptual_observation)
    monkeypatch.delenv(PERCEPTUAL_QA_ENV, raising=False)
    assert perceptual_qa_enabled() is False
    manifest = {"tracks": {"V1": {"clips": [
        {"timeline_in_frame": 0, "timeline_out_frame": 60}]}}}
    # Must not touch Resolve or the model when it is off.
    assert run_perceptual_observation(None, None, None, manifest) is None


def test_importing_the_router_does_not_pull_in_the_vision_model():
    """A module that costs gigabytes to import poisons every consumer.

    `vision_model` imports mlx_vlm; the router must not drag it in just
    because something wanted `plan_qa_checks`.
    """
    import subprocess
    probe = (
        "import sys;"
        "import library.tools.visual_qa_router as r;"
        "print('mlx_vlm' in sys.modules, 'library.tools.vision_model' in sys.modules)"
    )
    out = subprocess.run([sys.executable, "-c", probe], cwd=PROJECT_ROOT,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-500:]
    assert out.stdout.strip() == "False False", (
        f"importing visual_qa_router pulled in the model stack: {out.stdout!r}")


def test_the_fusion_subprocess_is_bounded():
    """An unbounded subprocess in the renderer can hang a real render
    with no diagnostic, indistinguishable from a slow Fusion pass."""
    path = os.path.join(PROJECT_ROOT, "library", "steps",
                        "step_6_01_render", "resolve_build_timeline.py")
    with open(path, encoding="utf-8") as f:
        src = f.read()
    assert "FUSION_SUBPROCESS_TIMEOUT_S" in src
    assert "timeout=FUSION_SUBPROCESS_TIMEOUT_S" in src
    assert "subprocess.TimeoutExpired" in src
