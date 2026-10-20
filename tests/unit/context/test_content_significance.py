"""What a reduction must not take with it, proved against named defects.

EDIT-023 said "preserve important informational and emotional content
during reductions", and nothing in the repository checked that promise:
the pipeline could remove a passage, remove a beat, or remove the only
place a claim was made, and nothing downstream said so.
`library/tools/reel_replace_guard.py` guards a REPLACEMENT - a reel's
row count against a declared diff - which is a different promise.

Each test names one defect this module closes:

- a required claim the cut no longer carries reads SATISFIED, so a
  reduction that dropped the point looks clean;
- a claim nobody required is treated as fatal, so every reduction is
  reported as damage;
- a required beat the cut no longer serves is not noticed, so the
  direction's own "required" is ignored;
- the measured content type the cut no longer shows is not noticed, so
  the only product shot can be dropped without a word;
- the module SCORES a passage's significance, so "worth 0.7" becomes
  the answer instead of what the passage carries.

None of them asserts that the module contains a particular entry or a
particular count: passages are built and checked, so a test passes only
when the module actually does what the defect requires.
"""
from __future__ import annotations

from library.tools import content_significance
from library.tools.editorial_policy import parse_policy


def _passage(passage_id, clip_id="clip_001", text="we shipped it",
             serves_beats=()):
    return {"passage_id": passage_id, "clip_id": clip_id, "text": text,
            "serves_beats": list(serves_beats)}


def _claim(claim_id="claim:1", statement="the launch failed",
           passage_ids=("p1",)):
    return {"claim_id": claim_id, "statement": statement,
            "passage_ids": list(passage_ids)}


def _beat(beat_id="beat:1", purpose="set up the problem", required=False,
          passage_ids=("p1",)):
    return {"beat_id": beat_id, "purpose": purpose, "required": required,
            "passage_ids": list(passage_ids)}


def _policy(**slots):
    return parse_policy({"objective": "interview", "slots": slots},
                        project_id="demo")


def _check(before, after, **kwargs):
    return content_significance.check_reduction(before, after, **kwargs)


# ── The claims ────────────────────────────────────────────────────

def test_a_required_claim_the_cut_drops_is_damage():
    """A reduction that dropped the point is not a reduction."""
    found = _check([_passage("p1")], [_passage("p2")],
                   claims=[_claim()],
                   policy=_policy(content={"required_claims": ["claim:1"]}))
    assert found["verdict"] == content_significance.DAMAGED
    kinds = {row["kind"] for row in found["findings"]}
    assert content_significance.DROPPED_REQUIRED_CLAIM in kinds


def test_a_claim_nobody_required_is_reported_but_not_damage():
    """A dropped claim nobody required is a decision, not a failure."""
    found = _check([_passage("p1")], [_passage("p2")], claims=[_claim()])
    assert found["verdict"] == content_significance.PRESERVED
    kinds = {row["kind"] for row in found["findings"]}
    assert content_significance.DROPPED_CLAIM in kinds


def test_a_claim_the_cut_still_carries_is_not_reported():
    """The check reads the cut, not the plan."""
    found = _check([_passage("p1")], [_passage("p1")], claims=[_claim()])
    assert found["verdict"] == content_significance.PRESERVED
    assert found["findings"] == []


# ── The beats ─────────────────────────────────────────────────────

def test_a_required_beat_the_cut_stops_serving_is_damage():
    """The direction marked it required, and the direction is read."""
    found = _check([_passage("p1", serves_beats=("beat:1",))],
                   [_passage("p2", serves_beats=("beat:2",))],
                   beats=[_beat(required=True),
                          _beat(beat_id="beat:2", passage_ids=("p2",))])
    assert found["verdict"] == content_significance.DAMAGED
    kinds = {row["kind"] for row in found["findings"]}
    assert content_significance.DROPPED_REQUIRED_BEAT in kinds


def test_an_optional_beat_the_cut_stops_serving_is_reported():
    """A beat nobody required is a decision, and it is reported."""
    found = _check([_passage("p1", serves_beats=("beat:1",))],
                   [_passage("p2", serves_beats=("beat:2",))],
                   beats=[_beat(),
                          _beat(beat_id="beat:2", passage_ids=("p2",))])
    assert found["verdict"] == content_significance.PRESERVED
    kinds = {row["kind"] for row in found["findings"]}
    assert content_significance.DROPPED_BEAT in kinds


# ── The measured picture ──────────────────────────────────────────

def test_the_measured_content_type_the_cut_no_longer_shows_is_reported():
    """The only product shot can be dropped without a word."""
    before = [_passage("p1", clip_id="clip_001")]
    after = [_passage("p2", clip_id="clip_002")]
    found = _check(before, after, clip_content={
        "clip_001": {"content_type": "product_showcase"},
        "clip_002": {"content_type": "person_talking_to_camera"},
    })
    kinds = {row["kind"] for row in found["findings"]}
    assert content_significance.DROPPED_CONTENT_TYPE in kinds


def test_the_dropped_passage_is_named_with_what_it_carried():
    """A dropped passage is named with its evidence."""
    before = [_passage("p1", serves_beats=("beat:1",))]
    after = []
    found = _check(before, after, beats=[_beat()])
    dropped = [row for row in found["findings"]
               if row["kind"] == "dropped_passages"]
    assert dropped
    assert dropped[0]["passages"][0]["passage_id"] == "p1"
    assert dropped[0]["passages"][0]["sole_carrier_of_beats"] == ["beat:1"]


# ── What the significance is ───────────────────────────────────────

def test_the_significance_names_what_a_passage_carries_and_never_scores_it():
    """A passage is the only place for something, or it is not."""
    found = content_significance.passage_significance(
        [_passage("p1"), _passage("p2")],
        claims=[_claim(passage_ids=("p1",))],
        beats=[_beat(passage_ids=("p1",))])
    rows = {row["passage_id"]: row for row in found["passages"]}
    assert rows["p1"]["sole_carrier_of_claims"] == ["claim:1"]
    assert rows["p1"]["sole_carrier_of_beats"] == ["beat:1"]
    assert rows["p2"]["sole_carrier_of_claims"] == []
    assert "score" not in str(found).lower()


def test_a_cut_with_nothing_to_compare_reads_undetermined():
    """Nothing to check is not a passing check."""
    found = _check([], [])
    assert found["verdict"] == content_significance.UNDETERMINED
