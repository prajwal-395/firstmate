"""R03: the explicit query-planning stage plans before anything searches.

Defect-naming tests only: every test names one way a plan goes wrong
(a filter the query states but the plan drops, an expensive step
running before the cheap filter that would narrow it, a budget with
no rule behind it, a replan that silently widens). Closed tables and
fixtures only: no model, no footage, no project.
"""

import pytest

from library.tools import query_planner as qp
from library.tools import requirement_graph as rg

ACC = "Find the close-up where the speaker picks up the phone after saying goodbye"


def executable(plan):
    return [
        step
        for step in plan["steps"]
        if step["kind"] in ("filter", "verify", "inference")
        and step.get("status", "active") == "active"
    ]


def test_acceptance_query_extracts_every_deterministic_filter():
    plan = qp.plan_query(ACC)
    filters = plan["filters"]
    assert filters["person"] == {"role": "speaker", "resolved": False}
    assert filters["object_category"] == ["phone"]
    assert filters["action_transition"] == [
        {"candidate_kind": "pick_up_put_down", "inferred": False}
    ]
    assert filters["shot_scene"] == {"framing": ["close-up"]}
    assert filters["transcript_phrase"]["phrase"] == "goodbye"


def test_acceptance_query_orders_speech_before_the_action():
    plan = qp.plan_query(ACC)
    assert plan["order"] != []
    action = next(
        step
        for step in plan["steps"]
        if step.get("detail", {}).get("hint", {}).get("candidate_kind")
        == "pick_up_put_down"
    )
    speech = next(
        step
        for step in plan["steps"]
        if (
            step.get("detail", {})
            .get("plan", {})
            .get("predicates", [{}])[0]
            .get("equals")
            == "goodbye"
        )
    )
    assert speech["id"] in action["depends_on"], (
        "the pick-up runs after its anchor, never before it"
    )


def test_cheap_filters_precede_expensive_verification():
    plan = qp.plan_query(ACC)
    tiers = [step["tier"] for step in executable(plan)]
    assert tiers, "a plan with nothing executable plans nothing"
    assert tiers == sorted(tiers, key={qp.CHEAP: 0, qp.EXPENSIVE: 1}.__getitem__), (
        "measurement lookups run before bounded reanalysis, always"
    )


def test_verification_is_required_exactly_when_verify_survives():
    assert qp.plan_query(ACC)["budgets"]["verification"]["required"] is True
    assert qp.plan_query(ACC)["budgets"]["verification"]["lanes"] == ["m7"]
    simple = qp.plan_query("what is face_001 doing at 125.06s")
    assert simple["budgets"]["verification"]["required"] is False


def test_budgets_scale_with_complexity_by_a_stated_rule():
    simple = qp.plan_query("what is face_001 doing at 125.06s")
    hard = qp.plan_query(ACC)
    assert (
        simple["complexity"]["band"] == "simple"
        and simple["budgets"]["total_candidates"] == 100
    )
    assert (
        hard["complexity"]["band"] == "complex"
        and hard["budgets"]["total_candidates"] == 300
    )
    assert (
        hard["budgets"]["per_step_candidates"]["step-1"]
        > hard["budgets"]["per_step_candidates"]["step-3"]
    ), "a cheap filter earns room; a verify step earns a narrow cap"


def test_ordinal_and_track_supersede_their_own_words():
    plan = qp.plan_query("the second time face_001 covers their mouth")
    assert plan["filters"]["ordinal"] == 2
    assert plan["filters"]["person"] == {"track": "face_001", "resolved": True}
    superseded = {
        step["superseded_by"]
        for step in plan["steps"]
        if step.get("status") == "superseded"
    }
    assert superseded == {"ordinal", "person"}, (
        "the second/face words are consumed by their filters, never searched twice"
    )
    assert all(
        plan["budgets"]["per_step_candidates"][step["id"]] == 0
        for step in plan["steps"]
        if step.get("status") == "superseded"
    )


def test_time_range_and_person_are_deterministic_filters():
    plan = qp.plan_query("what is face_001 doing at 125.06s")
    assert plan["filters"]["time_range"] == {"start": 125.06, "end": 125.06}
    assert plan["filters"]["person"]["track"] == "face_001"


def test_ambiguous_dimensions_name_their_inference():
    plan = qp.plan_query(ACC)
    dimensions = {row["dimension"] for row in plan["ambiguous"]}
    assert dimensions == {"person_identity", "framing_measure", "phrase_exactness"}
    assert all(row["needs"] for row in plan["ambiguous"]), (
        "an ambiguity with no named inference is a shrug, not a plan"
    )


def test_open_verb_without_a_kind_is_ambiguous_not_silent():
    plan = qp.plan_query("the moment she lifts the box")
    verbs = [row.get("verb") for row in plan["filters"].get("action_transition") or []]
    assert "lifts" in verbs
    assert "action_kind" in {row["dimension"] for row in plan["ambiguous"]}


def test_counted_people_are_not_presence():
    filters = qp.extract_filters(
        "the comparison of a ten person agency against a company"
    )
    assert "object_category" not in filters, (
        "'ten person' counts people; it does not show one"
    )


def test_decimal_point_is_not_a_gesture():
    filters = qp.extract_filters("calling GEO just SEO two point oh")
    assert "action_transition" not in filters


def test_replan_drops_absent_lanes_and_logs_every_change():
    plan = qp.plan_query(ACC)
    revised = qp.replan(
        plan,
        {
            "lanes_absent": ["m7"],
            "lanes_partial": ["object_detection"],
            "contradictions": [],
        },
    )
    assert revised["replan_revision"] == 1
    dropped = [step for step in revised["steps"] if step.get("status") == "dropped"]
    assert dropped and all("never built" in step["drop_reason"] for step in dropped)
    narrowed = [step for step in revised["steps"] if step.get("status") == "narrowed"]
    assert narrowed and all(
        revised["budgets"]["per_step_candidates"][step["id"]]
        == qp.CHEAP_STEP_BUDGET // 2
        for step in narrowed
    )
    assert len(revised["replan_log"]) == len(dropped) + len(narrowed)


def test_replan_holds_contradicted_steps_for_review():
    plan = qp.plan_query(ACC)
    target = executable(plan)[0]["id"]
    revised = qp.replan(
        plan,
        {
            "lanes_absent": [],
            "lanes_partial": [],
            "contradictions": [{"steps": [target], "note": "two transcripts disagree"}],
        },
    )
    held = next(step for step in revised["steps"] if step["id"] == target)
    assert held["status"] == "needs_review"
    assert "disagree" in held["review_reason"]


def test_replan_that_changes_nothing_says_so():
    plan = qp.plan_query(ACC)
    revised = qp.replan(
        plan, {"lanes_absent": [], "lanes_partial": [], "contradictions": []}
    )
    assert revised["replan_log"] == [
        {
            "step": None,
            "change": "unchanged",
            "reason": ("every step's lane is built and no contradiction names a step"),
        }
    ]


def test_a_query_nobody_can_plan_is_refused():
    with pytest.raises((ValueError, rg.RequirementGraphError)):
        qp.plan_query("")
    with pytest.raises((ValueError, rg.RequirementGraphError)):
        qp.plan_query("cats or dogs")


def test_planning_is_deterministic():
    assert qp.plan_query(ACC) == qp.plan_query(ACC)
