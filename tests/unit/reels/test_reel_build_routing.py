"""AXI-35 / AXI-37 / AXI-40: the ROUTER chooses the build strategy, and
the reel build publishes through the common execution contract.

Defects named here, each one a thing that used to be true:

- the reel build decided for itself whether an incremental refusal may
  be followed by a full rebuild, and it read the refusal's category with
  a permissive default (`getattr(refused, 'category', 'unsupported')`) -
  so a category nobody recognised became the ONE category a rebuild may
  follow, and an edit the captain had approved was rebuilt over without
  anyone deciding to;
- a stale base, a conflicting live edit and an unreadable timeline were
  three refusals the build knew about and the router did not, because
  the build never asked it;
- the OTIO placement fallback was PRINTED, and the build record did not
  carry it, so a reader of the record could not tell which placement
  route produced it;
- the reel build's receipt had its own shape: no `execution_contract`,
  no common lifecycle, so a build and a semantic patch could not be
  compared;
- the incremental arm asked `route_incremental` directly while the
  rebuild arm asked the router - two entry points for one decision.
"""
from __future__ import annotations

import pytest

from library.tools import reel_build, reel_execution_contract
from library.tools.execution_router import (
    STRATEGY_FULL_BUILD_ID,
    STRATEGY_INCREMENTAL_ID,
    IncrementalRoutingRefused,
    route_compiled_edit,
)
from library.tools.reel_incremental import (
    IncrementalBroken,
    IncrementalConflict,
    IncrementalRefused,
    IncrementalStale,
)


def test_an_unsupported_incremental_refusal_may_be_followed_by_a_rebuild():
    """The router owns the fallback, and it permits this one."""
    decision = reel_build.strategy_after_incremental_refusal(
        IncrementalRefused("the plan is not incrementally applicable"))

    assert decision["backend"] == "full_rebuild"
    assert decision["mode"] == "staged_full_rebuild"
    assert decision["rejected_alternatives"][0]["reason"] == (
        "unsupported: the plan is not incrementally applicable")


@pytest.mark.parametrize("cls", [IncrementalStale, IncrementalConflict,
                                 IncrementalBroken],
                         ids=("stale", "conflict", "broken"))
def test_a_stale_conflicting_or_broken_base_is_never_rebuilt_over(cls):
    """Three refusals, three facts: none of them may be rebuilt over."""
    with pytest.raises(cls) as refused:
        reel_build.strategy_after_incremental_refusal(
            cls("the live timeline disagrees with the plan"))

    assert refused.value.category == cls.category
    assert refused.value.fix
    # The router's refusal is the reason it was not rebuilt over.
    cause = refused.value.__cause__
    assert isinstance(cause, IncrementalRoutingRefused)
    assert cause.category == cls.category


def test_the_rebuild_refusal_records_the_reason_not_the_whole_refusal():
    """The receipt records the reason, not a rendered refusal block."""
    decision = reel_build.strategy_after_incremental_refusal(
        IncrementalRefused("only the full build replays the ledger rows"))

    reason = decision["rejected_alternatives"][0]["reason"]
    assert reason == "unsupported: only the full build replays the ledger rows"
    assert "ren: refused" not in reason


def test_the_router_refuses_an_unknown_category_instead_of_rebuilding():
    """An unrecognised category must not become the rebuildable one."""
    with pytest.raises(Exception) as refused:
        route_compiled_edit(
            {"operations": [{"op": STRATEGY_FULL_BUILD_ID}]},
            context={"request_incremental": True,
                     "allow_full_rebuild": True,
                     "ineligibility_category": "not_a_category",
                     "ineligibility_reason": "typo"})
    assert "not_a_category" in str(refused.value)


def test_both_arms_ask_the_same_router_entry_point():
    """One decision, one entry point: the strategy ids route alike."""
    incremental = route_compiled_edit(
        {"operations": [{"op": STRATEGY_INCREMENTAL_ID}]},
        context={"incremental_eligibility": True,
                 "reason": "the eligibility checks passed"})

    assert incremental.backend == "incremental"
    assert incremental.mode == "staged"
    assert incremental.reasons == ("the eligibility checks passed",)


def test_the_build_publishes_the_common_execution_contract():
    """A build and a semantic patch answer the same receipt questions."""
    record = reel_execution_contract.publish({
        "timeline_name": "Reel 1",
        "placement_check": {"passed": True, "repair_attempted": False},
        "stream_enforcement": {"checked": 2, "deleted": [], "unverified": []},
        "overlay_sweep": None,
        "placements": [{"unique_id": "clip-1"}],
    }, backend="full_rebuild")

    contract = record["execution_contract"]
    assert contract["backend"] == "full_rebuild"
    assert contract["status"] == "verified"
    assert {row["operation"] for row in contract["verification"]["checks"]} == {
        "reel.placement", "reel.streams"}
    assert record["lifecycle"]["stage"] == "VERIFIED"
    assert record["lifecycle"]["backend"] == "full_rebuild"


def test_the_build_names_what_it_did_not_measure():
    """A build does not render: the rendered layers stay unmeasured."""
    record = reel_execution_contract.publish({
        "timeline_name": "Reel 1",
        "placement_check": {"passed": True, "repair_attempted": False},
        "stream_enforcement": {"checked": 2, "deleted": [], "unverified": []},
        "overlay_sweep": None,
    }, backend="full_rebuild")

    verification = record["execution_contract"]["verification"]
    assert "rendered_pixels" in verification["missing"]
    assert "rendered_audio" in verification["missing"]
    assert "overlay_sweep_verdict" in verification["missing"]
    assert verification["layers"]["rendered_pixels"]["status"] == "not_measured"
    assert verification["layers"]["rendered_audio"]["status"] == "not_measured"


def test_a_failed_build_check_is_reported_as_failed_not_as_missing():
    """A measurement that failed is a failure, not an absent one."""
    record = reel_execution_contract.publish({
        "timeline_name": "Reel 1",
        "placement_check": {"passed": False, "repair_attempted": True},
        "stream_enforcement": {"checked": 2, "deleted": [], "unverified": []},
    }, backend="full_rebuild")

    contract = record["execution_contract"]
    assert contract["status"] == "failed"
    failed = [row for row in contract["verification"]["checks"]
              if row["passed"] is False]
    assert [row["operation"] for row in failed] == ["reel.placement"]
    assert record["lifecycle"]["stage"] == "APPLIED"


def test_the_publish_is_additive():
    """The build record keeps every key it had."""
    original = {"timeline_name": "Reel 1",
                "placement_check": {"passed": True, "repair_attempted": False},
                "stream_enforcement": {"checked": 0, "deleted": [],
                                       "unverified": []}}
    published = reel_execution_contract.publish(original,
                                                backend="full_rebuild")

    for key, value in original.items():
        assert published[key] == value
