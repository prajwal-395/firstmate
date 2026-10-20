"""A blind head-to-head: what the evaluator cannot see through.

Each test names a defect: an evaluator-facing view carrying the origin, a
systematic side order that hands the answer over, a description that
gives the answer away, and an undecided pair counted as a loss for
somebody.
"""

from __future__ import annotations

import pytest

from library.tools import blind_comparison as eu


def _treatments():
    ren = {"treatment_id": "ren-1", "name": "Claim first",
           "plan_summary": "Opens on the spoken claim.",
           "evaluation": [{"criterion": "the claim lands", "verdict": "pass",
                           "reason": "the claim is spoken first"}]}
    professional = {"treatment_id": "pro-1", "name": "Question first",
                    "plan_summary": "Opens on a question.",
                    "evaluation": [{"criterion": "the claim lands",
                                    "verdict": "fail",
                                    "reason": "the claim is buried"}]}
    return ren, professional


# ── The editorial score is not the technical gate ─────────────────────


def test_the_evaluator_view_names_no_origin():
    """Catches: an evaluator-facing view carrying the origin, so the
    comparison was not blind at all."""
    ren, professional = _treatments()
    record = eu.comparison("req-1", "the opening feels slow", ren,
                            professional)
    view = eu.evaluator_view(record)
    assert "blinding" not in view
    for secret in ("ren-1", "pro-1", "Claim first", "Question first"):
        assert secret not in str(view), secret
    for side in view["sides"]:
        assert set(side) == {"side", "plan_summary", "evaluation"}


def test_a_plan_summary_that_names_its_treatment_is_refused():
    """Catches: a side whose description gave the answer away."""
    ren, professional = _treatments()
    record = eu.comparison("req-1", "the opening feels slow", ren,
                            professional)
    record["sides"][0]["treatment"] = dict(
        record["sides"][0]["treatment"],
        plan_summary="ren-1 opens on the spoken claim.")
    with pytest.raises(ValueError, match="which side this is"):
        eu.evaluator_view(record)


def test_the_side_order_is_not_systematically_ren_first():
    """Catches: an order that always put Ren's treatment on the same
    side, so an evaluator could read the answer off the layout."""
    ren, professional = _treatments()
    orders = set()
    for index in range(40):
        record = eu.comparison(f"req-{index}", "the opening feels slow", ren,
                                professional)
        orders.add(record["sides"][0]["origin"])
    assert orders == {"ren", "professional"}


def test_the_same_comparison_always_shows_the_same_order():
    """Catches: an order that changed between showings, so a reviewer
    could not repeat the comparison they were asked about."""
    ren, professional = _treatments()
    first = eu.comparison("req-1", "the opening feels slow", ren,
                           professional)
    again = eu.comparison("req-1", "the opening feels slow", ren,
                           professional)
    assert first["blinding"] == again["blinding"]


def test_unblinding_names_the_treatment_the_evaluator_preferred():
    """Catches: a preference that stayed a side, so the answer said
    nothing about the two treatments."""
    ren, professional = _treatments()
    record = eu.comparison("req-1", "the opening feels slow", ren,
                            professional)
    ren_side = record["sides"][0]["origin"] == "ren" and "first" or "second"
    unblinded = eu.unblind(record, ren_side)
    assert unblinded["preferred"] == eu.REN_PREFERRED
    assert unblinded["ren_chosen"] is True


def test_an_undecided_pair_is_not_a_loss_for_either_side():
    """Catches: an evaluator who could not decide counted against Ren."""
    ren, professional = _treatments()
    record = eu.comparison("req-1", "the opening feels slow", ren,
                            professional)
    undecided = eu.unblind(record, eu.PREFERRED_UNSURE)
    assert undecided["preferred"] == eu.UNDECIDED
    totals = eu.agreement([{**record, "preference": eu.PREFERRED_UNSURE}])
    assert totals["counts"][eu.REN_PREFERRED] == 0
    assert totals["counts"][eu.PROFESSIONAL_PREFERRED] == 0
    assert totals["ren_preferred_rate"] is None


def test_a_comparison_with_no_plan_summary_is_refused():
    """Catches: a side the evaluator is asked to judge with nothing to
    read."""
    ren, professional = _treatments()
    ren = {**ren, "plan_summary": "  "}
    with pytest.raises(ValueError, match="plan summary"):
        eu.comparison("req-1", "the opening feels slow", ren, professional)


def test_an_unknown_preference_is_refused():
    """Catches: a preference outside the vocabulary counted as one of
    them."""
    ren, professional = _treatments()
    record = eu.comparison("req-1", "the opening feels slow", ren,
                            professional)
    with pytest.raises(ValueError, match="unknown preference"):
        eu.unblind(record, "maybe")


def test_the_evaluator_page_carries_no_origin():
    """Catches: a page handed to the evaluator that named a treatment, so
    the comparison was not blind."""
    ren, professional = _treatments()
    record = eu.comparison("req-1", "the opening feels slow", ren,
                            professional)
    page = eu.render_evaluator_page([record])
    for secret in ("ren-1", "pro-1", "Claim first", "Question first"):
        assert secret not in page, secret
    assert "blinding" not in page


def test_the_evaluator_page_is_self_contained():
    """Catches: a page that reached the network for a script or a
    stylesheet, so the evaluator's kit stopped working offline."""
    ren, professional = _treatments()
    record = eu.comparison("req-1", "the opening feels slow", ren,
                            professional)
    page = eu.render_evaluator_page([record])
    for marker in ("<script src=", "<link ", "http://", "https://",
                   "fetch(", "__MANIFEST__"):
        assert marker not in page, marker


def test_an_answer_file_without_an_evaluator_is_refused():
    """Catches: a preference set with nobody behind it."""
    with pytest.raises(ValueError, match="evaluator"):
        eu.read_preferences({"schema": eu.ANSWERS_SCHEMA, "choices": []})


def test_an_answer_file_with_an_unknown_preference_is_refused():
    """Catches: a preference outside the vocabulary counted as one of
    them."""
    with pytest.raises(ValueError, match="invalid preference"):
        eu.read_preferences({"schema": eu.ANSWERS_SCHEMA, "labeller": "e",
                             "choices": [{"comparison_id": "c",
                                          "preference": "maybe"}]})


def test_a_preference_for_a_comparison_nobody_built_is_reported():
    """Catches: an unmatched answer dropped silently, so the measurement
    understated itself."""
    ren, professional = _treatments()
    record = eu.comparison("req-1", "the opening feels slow", ren,
                            professional)
    scored = eu.score_preferences([record], [
        {"comparison_id": "not-built", "preference": "first"}])
    assert scored["unmatched_comparison_ids"] == ["not-built"]
    assert scored["agreement"]["comparison_count"] == 0


def test_the_preferences_unblind_into_a_measurement():
    """Catches: a preference that stayed a side, so the answer said
    nothing about the two treatments."""
    ren, professional = _treatments()
    record = eu.comparison("req-1", "the opening feels slow", ren,
                            professional)
    ren_side = "first" if record["sides"][0]["origin"] == "ren" else "second"
    scored = eu.score_preferences([record], [
        {"comparison_id": record["comparison_id"], "preference": ren_side,
         "note": "the claim lands first"}])
    assert scored["agreement"]["counts"][eu.REN_PREFERRED] == 1
    assert scored["results"][0]["preferred"] == eu.REN_PREFERRED
    assert scored["results"][0]["note"] == "the claim lands first"
