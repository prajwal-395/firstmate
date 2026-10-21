"""PROD-003: every refusal says what KIND of thing blocked it, and what
the caller can actually do about it.

The defects named here, each one a thing that used to be true:

- a refusal said what happened and what to do, but never WHAT KIND of
  block it was, so a host could not tell missing media from an
  unsupported operation from a busy resource;
- a refusal could carry NO next step at all - 49 of the AXI's 96
  refusals named no command or edit that answers them (measured on
  2026-10-21, base a37842eff);
- a refusal could recommend an argument the caller cannot supply: the
  trim refusal that named an `--edits` ordering field the edit does not
  have (PR 3076), and a test fixture that named a `--supersede` flag
  the touch-up path does accept only on another verb;
- the broker's refusals said nothing at all about the block: "ren-resolved
  closed the connection" was the whole of it.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from library.tools import refusal_taxonomy
from library.tools.ren_refusal import RenRefusal

ROOT = Path(__file__).resolve().parents[3]


# ── The guard: no category, no next action ──────────────────────────


def test_a_refusal_without_a_kind_of_block_fails_at_the_raise_site():
    """The defect: a refusal that says what happened but not what KIND
    of block it was. Nothing may raise one."""
    with pytest.raises(ValueError) as refused:
        RenRefusal("the plan names no reel 9", "it names nothing",
                   "run `ren propose <project>` first")

    assert "no blocker is classified" in str(refused.value)


def test_a_refusal_with_an_unknown_kind_of_block_is_refused():
    """A kind outside the six is a guess, and a guess is refused."""
    with pytest.raises(ValueError) as refused:
        RenRefusal("what", "why", "fix", blocker="something_else")

    assert "blocker must be one of" in str(refused.value)


def test_a_refusal_without_a_next_action_is_refused():
    """A next action outside the vocabulary is advice nobody can read."""
    with pytest.raises(ValueError) as refused:
        RenRefusal("what", "why", "fix", blocker="missing_media",
                   next_action="try_later_maybe")

    assert "next_action must be one of" in str(refused.value)


def test_the_six_kinds_are_the_only_kinds():
    """The punch list's six, and no seventh."""
    assert refusal_taxonomy.BLOCKERS == (
        "missing_media", "unsupported_operation", "unsafe_state",
        "ambiguous_intent", "insufficient_evidence", "resource_contention")


def test_every_kind_has_a_plain_sentence_and_a_next_action():
    """A kind nobody can read in plain language is not a kind."""
    for blocker in refusal_taxonomy.BLOCKERS:
        plain = refusal_taxonomy.plain_reason(blocker)
        assert plain and plain == plain.lower()
        action = refusal_taxonomy.next_action_for(blocker)
        assert action in refusal_taxonomy.NEXT_ACTIONS


def test_a_refusal_names_its_kind_of_block_in_plain_language():
    """The first line a caller reads says what kind of block this is."""
    refused = RenRefusal("the plan names no reel 9", "it names nothing",
                         "run `ren propose <project>` first",
                         blocker="missing_media")
    rendered = refused.render()
    assert "blocker: missing_media" in rendered
    assert refusal_taxonomy.plain_reason("missing_media") in rendered
    assert "next action: supply_the_missing_media" in rendered


# ── The blocker vocabulary joins the ones that already existed ─────


def test_the_execution_failure_categories_join_the_blockers():
    """A stale base is a failure KIND and an `unsafe_state` blocker."""
    from library.tools.transaction_orchestration import ExecutionRefusal

    assert (refusal_taxonomy.blocker_for_failure_category("stale")
            == "unsafe_state")
    assert (refusal_taxonomy.blocker_for_failure_category("unsupported")
            == "unsupported_operation")
    for category in ExecutionRefusal.CATEGORIES:
        assert refusal_taxonomy.blocker_for_failure_category(category)


def test_the_ask_reasons_join_the_blockers():
    """An unauthorized change is an unsafe state, not an ambiguous one."""
    assert (refusal_taxonomy.blocker_for_ask_reason("ambiguous_intent")
            == "ambiguous_intent")
    assert (refusal_taxonomy.blocker_for_ask_reason("missing_authorization")
            == "unsafe_state")
    assert (refusal_taxonomy.blocker_for_ask_reason("insufficient_evidence")
            == "insufficient_evidence")


# ── Never recommend what the caller cannot supply ──────────────────


def test_a_touchup_fix_naming_a_field_the_edit_cannot_carry_is_refused():
    """The defect PR 3076 fixed, kept fixed by a guard.

    A `retime` edit carries no ordering field, so a refusal that says
    "state the ordering in the --edits JSON" recommends something the
    caller cannot supply.  The guard refuses the refusal at the raise
    site, where the mistake is.
    """
    from library.tools.reel_touchup import TouchupRefused

    with pytest.raises(ValueError) as refused:
        TouchupRefused("the plan overlaps on V2", "the plan collides",
                       "state the `ordering` explicitly in the --edits JSON")

    assert "cannot supply" in str(refused.value)


def test_a_touchup_fix_naming_what_the_edit_can_carry_is_accepted():
    """The guard does not refuse advice the caller CAN act on."""
    from library.tools.reel_touchup import TouchupRefused

    refused = TouchupRefused(
        "the plan overlaps on V2", "the plan collides",
        "exclude it from the move (\"exclude\": [[\"V2\", 3]] on the edit "
        "in the --edits JSON)")
    assert refused.blocker == "unsafe_state"
    assert refused.next_action == "settle_the_state_first"


def test_an_axi_refusal_without_a_next_step_fails_at_the_raise_site():
    """49 of the AXI's 96 refusals named no command that answers them."""
    from library.tools.resolve_axi_core_commands import AxiError

    with pytest.raises(ValueError) as refused:
        AxiError("Resolve returned no live Project Manager")

    assert "must carry the command or edit that answers it" in str(
        refused.value)


def test_an_axi_fix_naming_a_flag_the_axi_does_not_have_is_refused():
    """A fix that names an argument the caller cannot pass is refused."""
    from library.tools.resolve_axi_core_commands import AxiError

    with pytest.raises(ValueError) as refused:
        AxiError("the conditions are not JSON",
                 "pass the measured state with --no-such-flag")

    assert "cannot supply" in str(refused.value)


def test_every_axi_refusal_carries_a_next_action():
    """The measurement, as a test: 96 of 96."""
    from library.tools.resolve_axi_core_commands import AxiError

    source = (ROOT / "library/tools/resolve_axi_core_commands.py").read_text(
        encoding="utf-8")
    tree = ast.parse(source)
    sites = 0
    with_a_fix = 0
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "AxiError"):
            continue
        sites += 1
        if len(node.args) > 1 or any(keyword.arg == "fix"
                                    for keyword in node.keywords):
            with_a_fix += 1
    assert sites >= 90
    assert with_a_fix == sites


# ── The gate, the grounding stage and the broker ───────────────────


def test_a_refusing_feasibility_finding_names_its_kind_of_block():
    """The feasibility gate's refusal says what kind of block it is."""
    from library.tools import plan_feasibility

    verdict = plan_feasibility.assess(
        [{"op": "no.such.operation"}],
        state=plan_feasibility.FeasibilityState())
    assert verdict.status == plan_feasibility.INFEASIBLE
    for finding in verdict.findings:
        if finding.status == plan_feasibility.FEASIBLE:
            continue
        assert finding.blocker in refusal_taxonomy.BLOCKERS
        assert finding.next_action in refusal_taxonomy.NEXT_ACTIONS

    refusal = plan_feasibility.refusal(verdict)
    assert refusal["blockers"]
    assert refusal["next_actions"]


def test_a_feasibility_check_that_refuses_without_a_blocker_is_refused():
    """The guard on the gate: no kind of block, no refusal."""
    from library.tools import plan_feasibility

    with pytest.raises(ValueError) as refused:
        plan_feasibility._finding(
            "clip.trim", "a_check_nobody_classified",
            plan_feasibility.INFEASIBLE, "it refused")

    assert "without a blocker" in str(refused.value)


def test_a_grounding_refusal_names_the_kind_of_block_for_each_state():
    """Three unverified states, three different blocks."""
    from library.tools import target_grounding

    rows = [{"role": role, "state": state}
            for role, state in (("the observed caption",
                                 target_grounding.OBSERVED),
                                ("the claimed caption",
                                 target_grounding.CLAIMED),
                                ("the unresolved caption",
                                 target_grounding.UNRESOLVED))]
    refusals = target_grounding.refuse_ungrounded(
        [{"op": "caption_fix", "target_role": row["role"]} for row in rows],
        {"targets": rows})
    assert len(refusals) == 3
    kinds = {row["state"]: row["blocker"] for row in refusals}
    assert kinds[target_grounding.OBSERVED] == "ambiguous_intent"
    assert kinds[target_grounding.CLAIMED] == "insufficient_evidence"
    assert kinds[target_grounding.UNRESOLVED] == "missing_media"
    for row in refusals:
        assert row["next_action"] in refusal_taxonomy.NEXT_ACTIONS


def test_a_broker_refusal_names_its_kind_of_block_and_next_action():
    """The broker's refusals say what kind of block they are."""
    from library.tools.resolved.jobs import JobRefused

    refused = JobRefused("unknown job kind 'nope'")
    assert refused.blocker == "unsupported_operation"
    assert refused.next_action == "use_a_supported_operation"
    assert refused.as_dict()["blocking_reason"]


def test_a_broker_that_closed_the_connection_is_resource_contention():
    """The defect: "ren-resolved closed the connection" was the whole
    of the refusal - no kind of block, no next step."""
    from library.tools.resolved import client

    refused = client.BrokerError(
        {"error": "ren-resolved closed the connection"})
    assert refused.blocker == "resource_contention"
    assert refused.next_action == "wait_for_the_resource"
    assert refused.as_dict()["blocking_reason"]


# ── The measurement ────────────────────────────────────────────────


def test_the_inventory_measures_the_five_surfaces_with_denominators():
    """The number the punch list asks for is re-measurable."""
    from library.tools import refusal_inventory

    report = refusal_inventory.measure()
    assert set(report["surfaces"]) == {
        "shape", "axi", "feasibility", "grounding", "broker"}
    for surface, row in report["surfaces"].items():
        assert row["measured"], surface
    assert report["surfaces"]["shape"]["refusal_families"] > 60
    assert (report["surfaces"]["shape"]["refusal_families_classified"]
            == report["surfaces"]["shape"]["refusal_families"])
    assert (report["surfaces"]["axi"]["refusal_constructions_with_a_next_action"]
            == report["surfaces"]["axi"]["refusal_constructions"])
