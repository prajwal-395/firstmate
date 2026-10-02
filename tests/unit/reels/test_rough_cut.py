"""A rejected rough cut refuses re-entry (`rough_cut.approved`); the
override is explicit, not reachable by default, and recorded with the
verdict it overrode.

History: docs/evidence/rough_cut_review.md.
"""
import json
import os
import sys
import pytest
from pathlib import Path


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from library.tools import requirements as R
from library.processes.edit_video.run_pipeline import (
    _merge_deterministic_llm_outputs,
)

CONSUMERS = ("plan_subtitles", "plan_transitions", "plan_vfx", "plan_sfx")


@pytest.fixture
def gate():
    return next(r for r in R.registry() if r.name == "rough_cut.approved")


def _ctx(review, overrides=()):
    return R.Context(
        run_set=frozenset({"plan_subtitles"}),
        state={"step_outputs": {"review_rough_cut": {
            "rough_cut_review": review}}} if review is not None else {},
        overrides=frozenset(overrides))


def test_narrative_review_does_not_erase_mechanical_pass():
    """The LLM half used to overwrite `rough_cut_review.passed`."""
    merged = _merge_deterministic_llm_outputs(
        {"rough_cut_review": {
            "passed": True,
            "mechanical_checks": {
                "timeline_continuity": {"passed": True},
            },
        }},
        {"rough_cut_review": {
            "narrative_flow": "The sections connect clearly.",
            "emotional_arc": "Self-conscious to resolved.",
        }},
    )

    review = merged["rough_cut_review"]
    assert review["passed"] is True
    assert review["mechanical_checks"]["timeline_continuity"]["passed"]
    assert review["narrative_flow"] == "The sections connect clearly."
    # Other shared dict outputs keep last-writer semantics.
    other = _merge_deterministic_llm_outputs(
        {"other_output": {"mechanical": True}},
        {"other_output": {"narrative": "new owner"}},
    )
    assert other["other_output"] == {"narrative": "new owner"}


# ── The requirement exists and binds where the prose claimed ─────────


# ── Mutation, both ways ─────────────────────────────────────────────

def test_a_rejected_cut_refuses(gate):
    verdict = gate.check(_ctx({"passed": False,
                               "rejection_reasons": ["gap at 12.4s"]}))
    assert verdict.is_unsatisfied
    assert "REJECTED" in verdict.reason
    assert "gap at 12.4s" in verdict.reason, (
        "the refusal must carry WHY the cut was rejected, or the "
        "operator has to go and look it up")
    assert verdict.produced_by == ("review_rough_cut",)
    approved = gate.check(_ctx({"passed": True}))
    assert approved.is_satisfied
    assert approved.source in R.SOURCES
    _a_missing_measurement_is_not_a_failed_one(gate)


def _a_missing_measurement_is_not_a_failed_one(gate):
    """AGENTS.md 10.3: no field reports a default as though it were
    measured. A review with no `passed` was never measured, and saying
    it FAILED would be inventing a verdict."""
    absent = gate.check(_ctx({"verdict": "looks fine"}))
    failed = gate.check(_ctx({"passed": False}))
    assert absent.is_unsatisfied and failed.is_unsatisfied
    assert absent.reason != failed.reason
    assert "never measured" in absent.reason
    assert "REJECTED" not in absent.reason


# ── Constraint 1 and 3: explicit, and not reachable by default ──────


def test_overriding_something_that_did_not_opt_in_is_refused():
    """Including a `state_key` requirement: those stop the mid-run
    crashes, and an override that reached them would put those back."""
    state_key = next(r for r in R.all_requirements()
                     if r.kind == R.KIND_STATE_KEY)
    for name in ("spine.word_timings", state_key.name):
        with pytest.raises(R.OverrideError, match="not overridable"):
            R.assert_overrides_are_real([name])


def _an_override_does_nothing_when_the_requirement_is_satisfied(gate):
    """Passing the flag on a healthy cut must not record an override -
    nothing was overridden."""
    approved = _ctx({"passed": True}, overrides=("rough_cut.approved",))
    verdict = R.evaluate({"plan_subtitles"}, approved, [gate])
    assert verdict.unmet == [] and verdict.overridden == []


# ── Constraint 2: the override is RECORDED ──────────────────────────

def test_the_override_record_carries_the_verdict_it_overrode(gate):
    """Not merely THAT something was overridden - WHAT it refused with.

    A later reader asking "was the cut rejected when these captions were
    planned?" must get an answer from the record alone.
    """
    rejected = _ctx({"passed": False,
                     "rejection_reasons": ["timeline continuity: gap at 12.4s"]},
                    overrides=("rough_cut.approved",))
    verdict = R.evaluate({"plan_subtitles"}, rejected, [gate])
    assert verdict.unmet == [] and len(verdict.overridden) == 1
    record = verdict.overridden[0].as_record()

    assert record["requirement"] == "rough_cut.approved"
    assert "REJECTED" in record["refused_because"]
    assert "gap at 12.4s" in record["refused_because"], (
        "the record must carry the reason the cut was rejected, or it "
        "says only that a rule was bypassed")
    assert record["needed_by"]
    assert "subtitles.plan" in record["needed_by_capabilities"]
    assert json.loads(json.dumps(record)) == record, (
        "the record must survive being written to pipeline_data.json")
    _an_override_does_nothing_when_the_requirement_is_satisfied(gate)


# --------------------------------------------------------------------------
# From test_rough_cut_actual_script.py
#
# `build_actual_script` concatenates the spine's measured words per played
# A-roll range in timeline order; every empty lookup is named.
#
# History: docs/evidence/rough_cut_review.md.

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.steps.step_3_03_review_rough_cut.step import (  # noqa: E402
    build_actual_script,
)


def _words(*items):
    """(word, start, end) triples into spine word records."""
    return [
        {"word": w, "source_start": s, "source_end": e}
        for w, s, e in items
    ]


SPINE = {
    "structure": [
        {
            "position": 1,
            "block_type": "speech",
            "clip_id": "clip_001",
            "word_timestamps": _words(
                ("and", 17.6, 17.9),
                ("i", 18.0, 18.1),
                ("have", 18.2, 18.5),
                ("an", 18.6, 18.7),
                ("announcement", 18.8, 19.5),
                ("later", 60.0, 60.5),
            ),
        },
        {
            "position": 2,
            "block_type": "speech",
            "clip_id": "clip_002",
            "word_timestamps": _words(
                ("second", 3.0, 3.4),
                ("thing", 3.5, 3.9),
            ),
        },
    ],
}

A_ROLL = [
    {
        "spine_block_position": 2,
        "block_type": "speech",
        "timeline_start": 4.0,
        "timeline_end": 6.0,
        "video_segments": [
            {"clip_id": "clip_002", "video_in": 3.0, "video_out": 4.0},
        ],
    },
    {
        "spine_block_position": 1,
        "block_type": "speech",
        "timeline_start": 0.0,
        "timeline_end": 2.0,
        "video_segments": [
            {"clip_id": "clip_001", "video_in": 17.6, "video_out": 19.6},
        ],
    },
]


def test_words_are_concatenated_in_timeline_order():
    """Assignments arrive out of order; the script follows the
    timeline, and only the words inside each played range."""
    script = build_actual_script(A_ROLL, SPINE)
    assert [b["spine_block_position"] for b in script["blocks"]] == [1, 2]
    assert script["blocks"][0]["text"] == "and i have an announcement"
    assert script["blocks"][1]["text"] == "second thing"
    assert script["full_text"] == (
        "and i have an announcement second thing")
    assert script["unvoiced"] == []


def test_an_empty_lookup_is_named_not_skipped():
    """A played range with no measured words, and a segment with no
    source range, each land in `unvoiced` with a reason."""
    no_words = [{
        "spine_block_position": 1, "block_type": "speech",
        "timeline_start": 0.0, "timeline_end": 2.0,
        "video_segments": [
            {"clip_id": "clip_001", "video_in": 40.0, "video_out": 42.0},
        ],
    }]
    script = build_actual_script(no_words, SPINE)
    assert script["blocks"] == []
    assert script["full_text"] == ""
    assert len(script["unvoiced"]) == 1
    entry = script["unvoiced"][0]
    assert entry["spine_block_position"] == 1
    assert entry["source_in"] == 40.0
    assert "reason" in entry and entry["reason"]
    no_range = [{
        "spine_block_position": 1, "block_type": "speech",
        "timeline_start": 0.0, "timeline_end": 2.0,
        "video_segments": [{"clip_id": "clip_001"}],
    }]
    script = build_actual_script(no_range, SPINE)
    assert len(script["unvoiced"]) == 1
    assert "no source range" in script["unvoiced"][0]["reason"]
