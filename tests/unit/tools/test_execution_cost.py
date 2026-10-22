"""Cost-aware execution selection: the defects it must never have.

AXI-016.  The engine can reach a timeline three ways - a direct patch,
a staged incremental and a full rebuild - and the choice between them
is made on what is PROVEN, never on what is cheap, because the cost of
each was never measured.  `library/tools/execution_cost.py` measures
them and chooses.

The defects these tests name, one per test:

1. the selection ignored the measured cost and always took the
   smallest mutation, so a measured, cheaper route was never taken;
2. a strategy with no measurement was treated as a cheap one, so an
   unmeasured route displaced a measured, cheaper one;
3. a candidate that was NOT proven safe was offered to the selection
   and could be chosen;
4. a candidate that could not verify what the request needs was
   offered, so a route that verifies less could win;
5. nothing was eligible and the refusal did not say which candidates
   were excluded and why;
6. a strategy nobody measured was reported as costing nothing;
7. a row could be written for a strategy or an outcome nobody
   declared;
8. a rebuild could follow a qualification refusal nobody authorized -
   the quiet escape the punchlist names;
9. a touch wrote no row, so the strategies were never comparable.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import execution_cost as cost


def _candidates(*names: str) -> list[cost.Candidate]:
    return [cost.Candidate(name, True, f"{name} is proven safe here",
                            cost.profile(name).verification)
            for name in names]


def _costs(**measured: dict) -> dict:
    return {name: {"measured": True, "requests": 1, "wall_s_p50": seconds,
                   "wall_s_p95": seconds, "verified": 1, "refused": 0,
                   "failed": 0}
            for name, seconds in measured.items()}


# ── 1. The measured cost decides ─────────────────────────────────────


def test_the_cheapest_measured_strategy_is_chosen():
    """Defect: the selection took the smallest mutation whatever it
    cost, so the measurement it had read never changed anything."""
    selection = cost.select(
        _candidates("direct_patch", "staged_incremental"),
        costs=_costs(direct_patch=2.0, staged_incremental=58.0))

    assert selection.strategy == "direct_patch"
    assert selection.cost_basis == "measured"
    assert "cheapest measured" in selection.reason


def test_a_measured_cheaper_route_wins_over_a_smaller_one():
    """Defect: the preservation order was applied before the cost, so a
    route measured at 2 s lost to one measured at 58 s."""
    selection = cost.select(
        _candidates("direct_patch", "staged_incremental"),
        costs=_costs(direct_patch=58.0, staged_incremental=2.0))

    assert selection.strategy == "staged_incremental", (
        "the measurement is what the selection is FOR: two safe routes, "
        "one measured cheaper, and the cheaper one runs")


# ── 2. An unmeasured strategy is not a cheap one ─────────────────────


def test_an_unmeasured_cost_falls_back_to_the_preservation_order():
    """Defect: a strategy nobody measured was ranked as if it cost
    nothing, so it displaced a measured route for no reason."""
    selection = cost.select(
        _candidates("direct_patch", "staged_incremental"),
        costs=_costs(staged_incremental=58.0))

    assert selection.strategy == "direct_patch"
    assert selection.cost_basis == "unmeasured"
    assert "has not been measured" in selection.reason


def test_the_record_says_the_cost_was_unmeasured():
    selection = cost.select(_candidates("direct_patch"))

    entry = selection.comparison[0]
    assert entry["measured"] is False
    assert entry["wall_s_p50"] is None
    assert entry["requests"] == 0


# ── 3. Safety is a precondition, not a preference ───────────────────


def test_a_candidate_that_is_not_proven_safe_is_excluded():
    """Defect: the direct route was offered to the selection before
    anything had proved it safe for THIS request."""
    candidates = [cost.Candidate("direct_patch", False,
                                  "the router could not prove the direct "
                                  "mode against the measured runtime",
                                  cost.profile("direct_patch").verification),
                  * _candidates("staged_incremental")]
    selection = cost.select(candidates)

    assert selection.strategy == "staged_incremental"
    excluded = next(entry for entry in selection.comparison
                    if entry["strategy"] == "direct_patch")
    assert "not proven safe" in excluded["excluded"]
    assert "router could not prove" in excluded["excluded"]


# ── 4. The verification the request needs ────────────────────────────


def test_a_candidate_that_cannot_verify_the_request_is_excluded():
    """Defect: a route that verifies less was eligible, so a cheap
    route could land a change nobody read back."""
    candidates = [cost.Candidate("direct_patch", True, "proven safe",
                                  ("read back each written property",)),
                  * _candidates("staged_incremental")]
    selection = cost.select(
        candidates,
        required_verification=(("verify the promotion before the approved "
                                 "timeline is replaced"),))

    assert selection.strategy == "staged_incremental"
    excluded = next(entry for entry in selection.comparison
                    if entry["strategy"] == "direct_patch")
    assert "does not verify" in excluded["excluded"]


# ── 5. Nothing eligible ─────────────────────────────────────────────


def test_nothing_eligible_refuses_and_names_every_candidate():
    """Defect: an empty candidate set produced an exception with no
    reason, so the host could not say what to do next."""
    candidates = [cost.Candidate("direct_patch", False, "not proven safe",
                                  ())]
    with pytest.raises(cost.NoStrategyAvailable) as refused:
        cost.select(candidates)

    assert refused.value.comparison
    assert "not proven safe" in refused.value.why


# ── 6. Unmeasured is not zero ────────────────────────────────────────


def test_a_strategy_nobody_measured_is_absent_from_the_costs():
    """Defect: a strategy with no measurement was reported as costing
    zero seconds, which reads as 'it was free'."""
    measured = cost.measured_costs([
        {"layer": cost.STRATEGY_LAYER, "strategy": "direct_patch",
         "wall_s": 2.5, "verification": "verified"}])

    assert "direct_patch" in measured
    assert "staged_incremental" not in measured
    assert "full_rebuild" not in measured
    assert measured["direct_patch"]["requests"] == 1
    assert measured["direct_patch"]["verified"] == 1


def test_the_measured_costs_carry_their_denominator():
    measured = cost.measured_costs([
        {"layer": cost.STRATEGY_LAYER, "strategy": "staged_incremental",
         "wall_s": 10.0, "verification": "verified"},
        {"layer": cost.STRATEGY_LAYER, "strategy": "staged_incremental",
         "wall_s": 20.0, "verification": "verified"},
        {"layer": cost.STRATEGY_LAYER, "strategy": "staged_incremental",
         "wall_s": 30.0, "verification": "refused"}])

    entry = measured["staged_incremental"]
    assert entry["requests"] == 3
    assert entry["verified"] == 2
    assert entry["refused"] == 1
    assert entry["wall_s_p50"] == 20.0


# ── 7. Nothing outside the vocabulary ────────────────────────────────


def test_a_row_for_a_strategy_nobody_declared_is_refused():
    """Defect: an undeclared strategy could be recorded and then read
    back as a fourth route."""
    with pytest.raises(ValueError):
        cost.record_request(strategy="teleport", wall_s=1.0,
                            verification="verified")


def test_a_row_with_an_outcome_nobody_declared_is_refused():
    """Defect: an unrecognised verification outcome read as a success
    nobody checked."""
    with pytest.raises(ValueError):
        cost.record_request(strategy="direct_patch", wall_s=1.0,
                            verification="probably_fine")


def test_the_row_is_written_to_the_ledger(tmp_path):
    from library.tools import perf_ledger

    ledger = tmp_path / "perf_ledger.jsonl"
    with perf_ledger.recording(str(ledger), "run-cost", "reel.touchup"):
        cost.record_request(strategy="direct_patch", wall_s=1.25,
                            verification="verified", request_id="j-1",
                            target="Reel 01")

    rows = [json.loads(line) for line
            in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
    strategy_rows = [row for row in rows
                     if row.get("layer") == cost.STRATEGY_LAYER]
    assert len(strategy_rows) == 1, strategy_rows
    assert strategy_rows[0]["strategy"] == "direct_patch"
    assert strategy_rows[0]["wall_s"] == 1.25
    assert strategy_rows[0]["verification"] == "verified"


# ── 8. The rebuild is not an escape ──────────────────────────────────


def test_a_rebuild_over_an_unauthorized_refusal_is_refused():
    """Defect: a rebuild could follow a qualification refusal nobody
    authorized, and the quiet escape looked like a normal route."""
    decision = {"backend": "full_rebuild"}

    with pytest.raises(cost.RebuildEscapeRefused) as refused:
        cost.assert_documented_escalation(decision, refusal_category="stale")

    assert refused.value.category == "stale"


def test_an_unsupported_refusal_with_authorization_is_named_as_escalation():
    decision = {"backend": "full_rebuild"}

    escalation = cost.assert_documented_escalation(
        decision, refusal_category="unsupported", authorized=True)

    assert escalation["escalation"] is True
    assert escalation["category"] == "unsupported"


def test_a_construction_is_not_an_escalation():
    """Defect: the bulk build was treated as an escape from a refusal,
    so the route that CONSTRUCTS a reel looked like a workaround."""
    decision = {"backend": "full_rebuild"}

    escalation = cost.assert_documented_escalation(
        decision, refusal_category="", authorized=False, construction=True)

    assert escalation["escalation"] is False
    assert "constructs the reel" in escalation["reason"]


def test_a_targeted_route_is_never_an_escalation():
    decision = {"backend": "edit_patch.property"}

    escalation = cost.assert_documented_escalation(
        decision, refusal_category="stale")

    assert escalation["escalation"] is False


# ── The comparison the record and the decision share ─────────────────


def test_the_comparison_names_every_strategy_and_its_guarantees():
    rows = cost.comparison(_costs(direct_patch=2.0))

    names = [entry["strategy"] for entry in rows]
    assert names == list(cost.STRATEGIES)
    rebuild = next(entry for entry in rows
                  if entry["strategy"] == "full_rebuild")
    assert rebuild["constructs_whole_reel"] is True
    assert rebuild["preserves"]
    direct = next(entry for entry in rows
                  if entry["strategy"] == "direct_patch")
    assert direct["wall_s_p50"] == 2.0
