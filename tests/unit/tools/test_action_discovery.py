"""PLAN-010: the action surface answers, and answers with the truth.

Each test here names a DEFECT it would fail on.  A test that cannot
fail is worse than no test, because it reads as coverage.
"""

from __future__ import annotations

import pytest

from library.tools import (
    action_discovery,
    edit_algebra,
    recovery_instruction,
    refusal_taxonomy,
)


def test_the_menu_offers_only_operations_production_routing_may_use():
    """A menu that offers an unroutable operation makes a planner name one.

    The defect: the surface lists an operation the router will refuse, so
    the planner's choice cannot be executed and the refusal arrives at
    the far end of the write path instead of at the query.
    """
    result = action_discovery.discover("move the name bar down")
    assert result["matches"], "the surface offered nothing at all"
    for match in result["matches"]:
        status = edit_algebra.status(match["id"])
        assert status is not None, f"{match['id']} is not in the registry"
        assert status.production_enabled, (
            f"{match['id']} is in the supported menu but production routing "
            "may not use it")


def test_the_compact_catalog_lists_every_supported_operation_and_no_other():
    """The catalog is a VIEW of the registry, never a second list."""
    catalog = action_discovery.catalog()
    supported = {op_id for op_id, status in edit_algebra.OP_STATUS.items()
                 if status.production_enabled}
    listed = {row["id"] for row in catalog["operations"]}
    assert listed == supported, (
        "the compact catalog and the registry's supported set disagree; a "
        "second list is the defect this surface exists to remove")
    assert catalog["total_operations"] == len(edit_algebra.OP_STATUS)


def test_a_declared_operation_is_reported_with_its_blocker_and_an_alternative():
    """A gap is named, with the way round it.

    The defect: an operation that exists but cannot be routed is simply
    absent from the answer, so a planner concludes the engine cannot do
    the thing at all instead of learning which operation IS routable.
    """
    result = action_discovery.discover("trim the clip source range")
    refused = {row["id"]: row for row in result["not_routable"]}
    assert "clip.trim" in refused, (
        "the surface did not report the declared operation that matches "
        "the request")
    row = refused["clip.trim"]
    assert row["blocker"] == "unsupported_operation"
    assert row["next_action"] == refusal_taxonomy.BLOCKER_NEXT_ACTION[
        "unsupported_operation"]
    assert row["supported_alternatives"], (
        "the surface refuses an operation without naming a supported "
        "alternative in its own domain")
    for alternative in row["supported_alternatives"]:
        assert edit_algebra.status(alternative).production_enabled


def test_the_card_carries_the_five_things_the_punch_list_names():
    """Schema and example, preservation, verification, refusals, recovery.

    The defect: a card that answers "what can I do" without answering
    "what does it cost, how is it checked, and what does it refuse with"
    leaves the planner to guess the parts that decide the edit.
    """
    card = action_discovery.card("overlay.position")
    assert card["schema"]["required"], "the card declares no schema"
    assert card["example"]["op"] == "overlay.position"
    for param in card["schema"]["required"]:
        assert param in card["example"], (
            f"the example does not carry the required parameter {param!r}")
    assert card["verifies"]["contract"], "the card declares no verification"
    assert "preserves" in card
    blockers = {row["blocker"] for row in card["refusals"]}
    assert blockers == set(refusal_taxonomy.BLOCKERS), (
        "the card's refusal categories are not the taxonomy's own six")
    for row in card["refusals"]:
        assert row["next_action"], "a refusal category with no next action"


def test_the_recovery_options_cover_every_execution_failure_category():
    """The recovery table is the recovery module's, not a shorter one.

    The defect: a category with no named recovery is something the host
    improvises around, which is exactly what the typed instruction
    exists to prevent.
    """
    options = action_discovery.discover("move the name bar down")
    categories = {row["category"] for row in options["recovery_options"]}
    assert categories == set(recovery_instruction.CATEGORY_RECOVERY_TYPE)
    for row in options["recovery_options"]:
        assert row["action"], f"{row['category']} has no recovery action"
        assert row["recovery_class"] in recovery_instruction.RECOVERY_CLASSES


def test_an_effect_nobody_names_still_returns_the_supported_menu():
    """An unmatched effect is answered, not emptied.

    The defect: a query that matches nothing returns nothing, and the
    agent concludes the engine has no actions at all.
    """
    result = action_discovery.discover("zzzqqq xyzzy plugh")
    assert not result["effect_match"]
    assert result["matches"], "an unmatched effect returned no menu"
    for match in result["matches"]:
        assert edit_algebra.status(match["id"]).production_enabled


def test_an_effect_in_the_agents_own_words_reaches_its_operation():
    """The surface answers a request, not a vocabulary.

    The defect: the surface only matches the operation's exact id, so an
    agent has to remember the registry's wording to ask it anything -
    which is the thing PLAN-010 says must not be required.
    """
    result = action_discovery.discover("remove a clip and close the gap")
    assert result["effect_match"]
    assert result["matches"][0]["id"] == "clip.remove"


def test_the_surface_declares_no_operation_of_its_own():
    """Every id the surface publishes is the registry's id.

    The defect: a hand-written operation list beside the registry, which
    is how a surface starts claiming operations the engine does not hold.
    """
    result = action_discovery.discover("move the name bar down")
    known = set(edit_algebra.OP_STATUS)
    for match in result["matches"]:
        assert match["id"] in known
    for row in result["not_routable"]:
        assert row["id"] in known
    for alternative in action_discovery.discover(
            "trim the clip source range")["not_routable"]:
        assert alternative["id"] in known


def test_the_cli_serves_the_action_query_locally():
    """The verb is reachable, and it never asks the broker for anything.

    The defect: a discovery query that goes to the broker for a read
    waits on the one Resolve and the one lease to answer a question
    about the registry.
    """
    from library.tools import resolve_axi_core_commands as axi

    parser = axi.build_parser()
    actions = [action for action in parser._actions
               if getattr(action, "_name_parser_map", None)]
    names = set()
    for action in actions:
        names.update(action._name_parser_map)
    assert "actions" in names, "the actions verb is not registered"
    assert "cmd_actions" in axi._LOCAL_COMMANDS, (
        "the actions verb would be brokered; a registry read must not wait "
        "on Resolve")


@pytest.mark.parametrize("request_text", [
    "move the name bar down",
    "replace this clip with the other camera",
    "remove a clip and close the gap",
])
def test_every_answer_carries_its_own_card(request_text):
    """A menu entry is a card, not a name.

    The defect: the surface returns ids and leaves the caller to ask a
    second question per id, which is the round trip PLAN-010 removes.
    """
    result = action_discovery.discover(request_text)
    for match in result["matches"]:
        card = match["card"]
        assert card["id"] == match["id"]
        assert card["effect"]
        assert card["schema"]["required"] is not None
        assert card["how_to_request"]["edit_request_routable"] in (True, False)
