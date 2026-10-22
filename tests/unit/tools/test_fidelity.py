"""The ladder of resolutions: what an answer says about itself.

Every test here names a defect.  A test that cannot fail is worse than
no test, because it reads as coverage (AGENTS.md 10.4).
"""

from __future__ import annotations

import pytest

from library.tools import fidelity


def test_the_ladder_names_three_resolutions_of_the_same_evidence():
    """A ranking, a measurement, and the media itself.

    Three tiers, in order, each saying what it ESTABLISHES and what it
    does not: a ranking cannot establish a claim, and a measurement
    cannot establish what it never inspected.
    """
    assert [tier.name for tier in fidelity.TIERS] == [
        "coarse", "detailed", "original"]
    coarse, detailed, original = fidelity.TIERS
    assert coarse.order < detailed.order < original.order
    assert coarse.establishes == "where to look"
    assert "establishes the claim" in detailed.establishes
    assert coarse.does_not_establish
    assert detailed.does_not_establish
    assert original.establishes


def test_an_unknown_tier_is_refused_rather_than_defaulted():
    """A ladder with a hole in it answers a question nobody asked.

    The defect this names: a tier name that is not in the vocabulary
    falling back to the coarsest tier, so a caller asking for the
    measured tier would be served the ranking and would never know.
    """
    with pytest.raises(ValueError):
        fidelity.tier("high-resolution")
    with pytest.raises(ValueError):
        fidelity.provenance(tier="high-resolution")


def test_the_next_tier_above_the_top_is_none_and_not_an_error():
    """At the top of the ladder there is nowhere to escalate to.

    The defect this names: an escalation that names a tier that does
    not exist, or a crash at the top of the ladder.
    """
    assert fidelity.next_tier("coarse").name == "detailed"
    assert fidelity.next_tier("detailed").name == "original"
    assert fidelity.next_tier("original") is None
    assert fidelity.escalation(from_tier="original") == {}


def test_an_answer_carries_the_resolution_it_was_reached_at():
    """The provenance block names the tier and what it establishes."""
    block = fidelity.provenance(
        tier="coarse", lane="visual_frame_index",
        representation={"frames": 1092}, source_digest="abc",
        producer="clip-vit-large-patch14", producer_version="v1",
        coverage={"ran": True})
    assert block["tier"] == "coarse"
    assert block["lane"] == "visual_frame_index"
    assert block["representation"] == {"frames": 1092}
    assert block["source_digest"] == "abc"
    assert block["coverage"] == {"ran": True}
    assert block["does_not_establish"], (
        "a tier that cannot establish the claim must say so, or a reader "
        "takes a ranking for a finding")


def test_a_derived_answer_names_the_answer_it_was_derived_from():
    """Provenance travels DOWN the ladder as well as up it.

    The defect this names: a detailed answer that cannot be walked back
    to the coarse ranking that proposed its candidate, so a reader who
    doubts the verdict cannot see what the verdict was about.
    """
    block = fidelity.provenance(
        tier="detailed", derived_from={"tier": "coarse", "report_id": "abc123"})
    assert block["derived_from"]["tier"] == "coarse"
    assert block["derived_from"]["report_id"] == "abc123"


def test_an_escalation_names_what_it_would_establish_and_what_it_costs():
    """The escalation is a request with a price on it.

    The defect this names: a caller told that a higher tier exists but
    not what it would establish, so it cannot decide whether to pay for
    it.
    """
    block = fidelity.escalation(
        from_tier="coarse",
        request={"lane": "visual_verification", "candidates_at_most": 15},
        cost_seconds=1215.0, cost_basis="15 calls at 81 s each")
    assert block["from"] == "coarse"
    assert block["to"] == "detailed"
    assert block["establishes"] == fidelity.TIERS[1].establishes
    assert block["request"]["candidates_at_most"] == 15
    assert block["cost_seconds_estimate"] == 1215.0
    assert "81 s" in block["cost_basis"]


def test_annotating_an_answer_leaves_the_answer_it_was_made_from_alone():
    """The annotation is a copy.

    The defect this names: the annotation mutating the report the
    caller still holds, so the coarse answer and the detailed answer
    end up sharing one dict and one of them lies about its tier.
    """
    report = {"ranges": [], "answer_status": "found"}
    out = fidelity.annotate(report, fidelity=fidelity.provenance(
        tier="coarse"), escalation=fidelity.escalation(from_tier="coarse"))
    assert out is not report
    assert "fidelity" not in report
    assert out["fidelity"]["tier"] == "coarse"
    assert out["escalation"]["to"] == "detailed"
