"""The plan judged against the creative quality, proved against named defects.

The gap (E11): the conformance verifier proves the timeline matches the
plan and says nothing about whether the plan's own choices are good, so a
structurally conformant but creatively weak plan passes.  Each test here
names one defect that closes: a declared key moment the plan drops, a
declared pacing target the plan misses, and the anti-vacuity cases where
the check has nothing to read and must report UNMEASURED rather than pass
quietly (AGENTS.md 10.4).

No test asserts a registry, schema or count - the model is built, mutated
and queried, so a test passes only when the check actually does what the
defect requires, and a correct plan is never flagged.
"""
from __future__ import annotations

from library.tools import plan_creative_quality as pcq
from library.tools.reel_proposal import ReelMoment


# ── Fixtures ─────────────────────────────────────────────────────────

CATALOG = [
    {"clip_id": "clip_001", "source_file": "/raw/episode_a.mxf",
     "width": 1920, "height": 1080},
    {"clip_id": "clip_002", "source_file": "/raw/episode_b.mxf",
     "width": 1920, "height": 1080},
]


def _moment(number, slug, spans, start=0.0, end=10.0):
    """A plan moment playing the given source spans."""
    return ReelMoment(
        number=number, slug=slug, reason="",
        timeline_start=start, timeline_end=end,
        source_spans=tuple(spans),
    )


def _span(source_file, start, end):
    return {"source_file": source_file, "source_start": start,
            "source_end": end}


def _model(key_moments=None, pacing=None):
    """An intent model with key moments and a pacing curve."""
    return {
        "beats": [
            {"beat_id": "beat:1", "purpose": "set up the problem",
             "span": {"clip_id": "clip_001", "source_start": 0.0,
                      "source_end": 20.0}},
            {"beat_id": "beat:2", "purpose": "land the payoff",
             "span": {"clip_id": "clip_002", "source_start": 0.0,
                      "source_end": 20.0}},
        ],
        "claims": [],
        "key_moments": key_moments if key_moments is not None else [],
        "pacing_curve": pacing if pacing is not None else [],
    }


def _key_moment(moment_id="moment:1", description="the reveal",
                clip_id="clip_001", start=2.0, end=4.0):
    return {"moment_id": moment_id, "description": description,
            "span": {"clip_id": clip_id, "source_start": start,
                     "source_end": end}}


def _pacing(beat_id="beat:1", target_asl=4.0, energy=0.5):
    return {"beat_id": beat_id, "energy": energy, "target_asl": target_asl}


# ── Check 1: a declared key moment the plan drops ───────────────────


def test_a_plan_that_drops_a_declared_key_moment_is_flagged():
    """The fidelity test: a structurally conformant plan that omits a
    moment the direction says MUST appear is a creative defect no
    structural gate catches."""
    model = _model(key_moments=[_key_moment(start=2.0, end=4.0)])
    moments = [
        _moment(1, "the-question", [_span("/raw/episode_a.mxf", 10.0, 14.0)]),
    ]
    report = pcq.verify_plan_creative_quality(moments, model, CATALOG)
    assert report.key_moments_total == 1
    assert report.key_moments_covered == 0
    assert any(f.code == pcq.KEY_MOMENT_MISSING for f in report.findings)
    finding = next(f for f in report.findings
                   if f.code == pcq.KEY_MOMENT_MISSING)
    assert "moment:1" in finding.message
    assert "the reveal" in finding.message


def test_a_plan_that_plays_a_declared_key_moment_is_not_flagged():
    """The same moment, played by the plan - a correct plan is never
    flagged (AGENTS.md 10.4)."""
    model = _model(key_moments=[_key_moment(start=2.0, end=4.0)])
    moments = [
        _moment(1, "the-reveal", [_span("/raw/episode_a.mxf", 2.0, 4.0)]),
    ]
    report = pcq.verify_plan_creative_quality(moments, model, CATALOG)
    assert report.key_moments_covered == 1
    assert not any(f.code == pcq.KEY_MOMENT_MISSING for f in report.findings)


def test_a_key_moment_grounded_in_another_clip_is_not_covered():
    """Coverage is per-clip: a plan that plays the right seconds of the
    WRONG clip does not land the moment."""
    model = _model(key_moments=[_key_moment(clip_id="clip_002",
                                            start=2.0, end=4.0)])
    moments = [
        _moment(1, "the-reveal", [_span("/raw/episode_a.mxf", 2.0, 4.0)]),
    ]
    report = pcq.verify_plan_creative_quality(moments, model, CATALOG)
    assert report.key_moments_covered == 0
    assert any(f.code == pcq.KEY_MOMENT_MISSING for f in report.findings)


# ── Check 2: a declared pacing target the plan misses ───────────────


def test_a_plan_whose_pacing_misses_the_declared_target_is_flagged():
    """A beat the plan serves at 8s a shot against a declared 2s target
    is pacing that ignores the direction."""
    model = _model(pacing=[_pacing(beat_id="beat:1", target_asl=2.0)])
    moments = [
        _moment(1, "slow", [_span("/raw/episode_a.mxf", 0.0, 8.0)]),
    ]
    report = pcq.verify_plan_creative_quality(moments, model, CATALOG)
    assert report.beats_measured == 1
    assert any(f.code == pcq.PACING_MISSED for f in report.findings)
    finding = next(f for f in report.findings if f.code == pcq.PACING_MISSED)
    assert "beat:1" in finding.message
    assert "2.0" in finding.message and "8.0" in finding.message


def test_a_plan_whose_pacing_lands_on_target_is_not_flagged():
    """The same beat at its target - a correct plan is never flagged."""
    model = _model(pacing=[_pacing(beat_id="beat:1", target_asl=4.0)])
    moments = [
        _moment(1, "on-target", [_span("/raw/episode_a.mxf", 0.0, 4.0)]),
    ]
    report = pcq.verify_plan_creative_quality(moments, model, CATALOG)
    assert report.beats_measured == 1
    assert not any(f.code == pcq.PACING_MISSED for f in report.findings)


def test_a_beat_the_plan_does_not_serve_is_skipped_not_flagged():
    """A reel is an excerpt: a beat no planned moment draws on is a beat
    this reel does not cover, not a beat it paced wrongly."""
    model = _model(pacing=[_pacing(beat_id="beat:2", target_asl=2.0)])
    moments = [
        _moment(1, "only-beat-1", [_span("/raw/episode_a.mxf", 0.0, 8.0)]),
    ]
    report = pcq.verify_plan_creative_quality(moments, model, CATALOG)
    assert report.beats_measured == 0
    assert not any(f.code == pcq.PACING_MISSED for f in report.findings)


# ── Anti-vacuity: a check with nothing to read refuses ──────────────


def test_a_plan_with_no_intent_model_is_reported_unmeasured_not_passed():
    """A check with no creative direction to judge against must say so,
    never pass quietly (AGENTS.md 10.4)."""
    moments = [_moment(1, "anything", [_span("/raw/episode_a.mxf", 0.0, 4.0)])]
    report = pcq.verify_plan_creative_quality(moments, None, CATALOG)
    assert any(f.code == pcq.NO_INTENT_MODEL for f in report.findings)
    assert report.unmeasured


def test_a_key_moment_grounded_in_an_unknown_clip_is_unmeasured():
    """A key moment grounded in a clip the catalog does not carry cannot
    be checked - reported, not passed."""
    model = _model(key_moments=[_key_moment(clip_id="clip_999",
                                            start=2.0, end=4.0)])
    moments = [_moment(1, "x", [_span("/raw/episode_a.mxf", 0.0, 4.0)])]
    report = pcq.verify_plan_creative_quality(moments, model, CATALOG)
    assert report.key_moments_covered == 0
    assert not any(f.code == pcq.KEY_MOMENT_MISSING for f in report.findings)
    assert any("clip_999" in r for r in report.unmeasured)


def test_a_model_with_no_key_moments_and_no_pacing_is_not_a_defect():
    """An empty model is the model's own statement that the piece has no
    such structure - nothing to check is not a creative failure."""
    report = pcq.verify_plan_creative_quality([], _model(), CATALOG)
    assert report.key_moments_total == 0
    assert report.beats_total == 0
    assert not report.findings
