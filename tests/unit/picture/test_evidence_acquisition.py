"""L5 acquisition: existing-evidence-first, budgets, refusal paths."""

import pytest

from library.tools import evidence_acquisition as ea
from library.tools import evidence_query as eq
from library.tools import requirement_graph as rg


def _obs(oid, lane, modality, clock, start, end, mtype=None, value=None,
         freshness="fresh", coverage="complete"):
    native = None
    if mtype is not None:
        native = {"measurement_type": mtype, "value": value,
                  "coverage_status": coverage, "coverage": coverage,
                  "freshness": freshness, "verified_status": "verified",
                  "producer_version": "test-v1"}
    payload = {"evidence_contract": native} if native else {}
    if isinstance(value, str) and mtype == "speech.word":
        payload = dict(payload, text=value)
    return {"id": oid, "provenance_id": f"prov-digest1-{lane}",
            "asset_id": "asset1", "modality": modality,
            "interval": {"clock_id": clock, "start": start, "end": end},
            "payload": payload}


def _plan_for(query, model, **kwargs):
    graph = rg.parse_query(query)
    compiled = rg.compile_graph(graph)
    defaults = {"source_digest": "digest1", "duration_seconds": 120.0,
                "scope": {"clock_id": "clk", "asset_id": "asset1"}}
    defaults.update(kwargs)
    return ea.plan_acquisition(compiled, model, **defaults), compiled


def test_satisfied_requirement_schedules_no_work():
    model = {"observations": [
        _obs("w1", "m1", "speech_word", "clk", 10000, 20000,
             "speech.word", "revenue discussion here"),
    ]}
    plan, _ = _plan_for("where does the speaker discuss revenue", model)
    assert plan["action"] == "no_run"
    assert plan["steps"] == []
    assert plan["satisfied"] == {"req-1": True}
    assert plan["estimated_cost_seconds"] == 0.0


def test_missing_lane_selects_cheapest_measured_producer():
    plan, _ = _plan_for("where does the speaker discuss revenue",
                        {"observations": []})
    assert plan["action"] == "refine"
    assert plan["steps"] != []
    assert all(step["producer"] == "m1-cached-transcript"
               for step in plan["steps"])
    assert all("why" in step and step["why"] for step in plan["steps"])


def test_conjunction_narrows_acquisition_to_present_intervals():
    model = {"observations": [
        _obs("w1", "m1", "speech_word", "clk", 10000, 20000,
             "speech.word", "revenue discussion here"),
    ]}
    plan, _ = _plan_for(
        "speaker discussing revenue while a chart is visible", model)
    assert plan["satisfied"]["req-1"] is True
    assert plan["steps"] != []
    assert all(step["interval"] == {"start_s": 10.0, "end_s": 20.0}
               for step in plan["steps"])
    assert plan["estimated_cost_seconds"] == 1.6


def test_commercial_client_refuses_unresolved_licenses():
    selection = ea.select_producers(["m1"], edition="commercial",
                                    purpose="commercial_client",
                                    fidelity="standard")
    assert selection["m1"]["selected"] is None
    assert selection["m1"]["refused"] != []
    assert "commercial" in selection["m1"]["refused"][0]["reasons"][0]


def test_research_purpose_records_pending_review():
    selection = ea.select_producers(["object_detection"],
                                    purpose="permitted_research")
    assert selection["object_detection"]["selected"]["id"] == (
        "object-detection-vision-nominations")
    assert "pending" in selection["object_detection"]["selected"][
        "registry_note"]


def test_memory_ceiling_defers_heaviest_first():
    selection = ea.select_producers(
        ["object_detection"], purpose="permitted_research",
        fidelity="exhaustive", allow_escalation=True,
        memory_ceiling_gb=0.05)
    assert selection["object_detection"]["selected"] is None
    assert "ceiling" in selection["object_detection"]["deferred_memory"]


def test_latency_budget_keeps_highest_value_work():
    plan, _ = _plan_for(
        "speaker discussing revenue while a chart is visible",
        {"observations": []}, latency_budget_s=1.0)
    # The zero-cost transcript lookup survives; the 1.6 s detector
    # windows do not fit the 1.0 s budget and are refused by name.
    assert all(step["cost_budget_seconds"] == 0.0
               for step in plan["steps"])
    assert any(step["lane"] == "object_detection" for step in plan["steps"]) \
        is False
    assert any("budget" in row["reason"] for row in plan["refused"])


def test_zero_cost_lookups_fit_any_latency_budget():
    plan, _ = _plan_for("footage with laughter and applause",
                        {"observations": []}, latency_budget_s=0.0)
    assert plan["action"] == "refine"
    assert plan["estimated_cost_seconds"] == 0.0


def test_ledger_dedup_skips_identical_evidence():
    key = ea.ledger_key("digest1", "m1",
                        {"start_s": 0.0, "end_s": 120.0},
                        "m1-cached-transcript")
    plan, _ = _plan_for("where does the speaker discuss revenue",
                        {"observations": []}, ledger={key: {"status": "a"}})
    assert plan["action"] == "no_run"
    assert any("already acquired" in row["reason"]
               for row in plan["refused"])


def test_unavailable_producer_defers_without_changing_the_answer():
    graph = rg.parse_query("where does the speaker discuss revenue")
    compiled = rg.compile_graph(graph)
    plan = ea.plan_acquisition(
        compiled, {"observations": []}, source_digest="digest1",
        duration_seconds=120.0,
        scope={"clock_id": "clk", "asset_id": "asset1"})
    assert plan["action"] == "refine"
    report = ea.run_acquisition(
        plan, cached=lambda **kw: None, producers={},
        refresh_model=lambda old: {"observations": []})
    assert report["landed"] is False
    assert report["requery"] is None
    assert all(row["status"] == "deferred"
               for row in report["acquisition"]["steps"])


def _registry_with_verifier():
    """Fixture registry with an authorized (conditional) verifier.

    Planner behaviour must not depend on live license data: the
    verify producer the catalog names is authorized here with its
    condition recorded, while the live registry may quarantine it.
    """
    return {
        "schema_version": 1,
        "capabilities": [
            {"id": "video.locate_event",
             "description": "fixture event localization"},
        ],
        "implementations": [
            {
                "id": "gemma.vision_4_12b_it_4bit",
                "component_id": "model.gemma_vision",
                "label": "fixture verifier",
                "capabilities": ["video.locate_event"],
                "checkpoint": {
                    "version": "rev-pinned",
                    "weights_hash": "sha256:abc",
                    "provider": "example",
                    "conversion": None,
                    "runtime": "mlx",
                },
                "license_evidence": {
                    "upstream_url": "https://example.invalid/verifier",
                    "exact_version": "rev-pinned",
                    "retrieved_at": "2026-10-16",
                    "license": "apache-2.0",
                    "obligations": ["run the pinned revision"],
                    "approval_status": "conditional",
                },
                "purposes": ["commercial_client", "personal_noncommercial",
                             "permitted_research"],
                "editions": ["commercial", "personal"],
                "qualification": "qualified",
            },
        ],
    }


def test_verify_without_verifier_defers_with_reason():
    plan, _ = _plan_for("the person on the left holding the product",
                        {"observations": []}, registry=_registry_with_verifier())
    assert any(step.get("verify_task") for step in plan["steps"])
    report = ea.run_acquisition(
        plan, cached=lambda **kw: None, producers={},
        refresh_model=lambda old: {"observations": []})
    assert any(row["status"] == "deferred"
               and "verifier" in row["reason"]
               for row in report["verifications"])


def test_verify_lane_refused_without_authorized_producer():
    # Live license truth (no fixture): the planner refuses the verify
    # lane by name instead of scheduling an unauthorized producer.
    # Fail-closed and recorded, never silent.
    plan, _ = _plan_for("the person on the left holding the product",
                        {"observations": []})
    assert not any(step.get("verify_task") for step in plan["steps"])
    assert any("verify-bounded-span" in row.get("reason", "")
               for row in plan["refused"])


def test_contradictory_evidence_is_named_never_averaged():
    model = {"observations": [
        _obs("a1", "m1", "speech_word", "clk", 10000, 20000,
             "speech.word", "revenue up"),
        _obs("a2", "m1", "speech_word", "clk", 12000, 22000,
             "speech.word", "revenue down"),
    ]}
    graph = rg.parse_query("where does the speaker discuss revenue")
    compiled = rg.compile_graph(graph)
    rows = ea.find_contradictions(model, compiled)
    assert len(rows) == 1
    assert rows[0]["evidence_ids"] == ["a1", "a2"]


def test_agreeing_evidence_is_not_a_contradiction():
    model = {"observations": [
        _obs("a1", "m1", "speech_word", "clk", 10000, 20000,
             "speech.word", "Revenue up"),
        _obs("a2", "m1", "speech_word", "clk", 12000, 22000,
             "speech.word", "revenue UP"),
    ]}
    graph = rg.parse_query("where does the speaker discuss revenue")
    compiled = rg.compile_graph(graph)
    assert ea.find_contradictions(model, compiled) == []


def test_ocr_absence_without_coverage_is_not_confident():
    graph = rg.parse_query("where does the text read revenue appear")
    compiled = rg.compile_graph(graph)
    row = next(item for item in compiled["compiled"])
    answer = eq.evidence_for({"observations": []}, row["plan"])
    assert answer["status"] == "not_yet_analysed"
    assert answer["required_missing_lanes"] == ["ocr_extraction"]


def test_negated_requirement_fails_when_present():
    model = {"observations": [
        _obs("f1", "m7", "event_span", "clk", 0, 120000,
             "event.speaking_span", {"predicate": "speaking"}),
    ]}
    model["observations"][0]["payload"] = {"predicate": "on_screen"}
    graph = rg.parse_query("footage with no visible people")
    compiled = rg.compile_graph(graph)
    plan = ea.plan_acquisition(
        compiled, model, source_digest="digest1", duration_seconds=120.0,
        scope={"clock_id": "clk", "asset_id": "asset1"})
    assert plan["satisfied"] == {"req-1": False}
    assert "absence" in (plan["failure"] or "")


def test_marginal_gain_reports_answers_per_second():
    before = {"req-1": True, "req-2": False}
    after = {"req-1": True, "req-2": True}
    row = ea.marginal_gain_per_second(before, after, 1.6)
    assert row["utility_before"] == 0.5
    assert row["utility_after"] == 1.0
    assert row["answers_per_second"] == pytest.approx(0.5 / 1.6)


def test_zero_gain_is_zero_not_negative():
    row = ea.marginal_gain_per_second({"a": True}, {"a": True}, 3.0)
    assert row["answers_per_second"] == 0.0


def test_summaries_carry_intervals_and_ids_only():
    answer = {"status": "present",
              "results": [{
                  "interval": {"clock_id": "clk", "start": 10000,
                               "end": 20000, "start_s": 10.0,
                               "end_s": 20.0},
                  "evidence_ids": ["w1"], "lanes": ["m1"]}],
              "reasons": [], "required_missing_lanes": []}
    summary = ea.summarize_answer(answer)
    assert summary["intervals"][0]["evidence_ids"] == ["w1"]
    assert "payload" not in str(summary)


def test_edit_candidates_link_evidence_without_rationale():
    answers = {"req-1": {
        "status": "present",
        "results": [{
            "interval": {"clock_id": "clk", "start_s": 10.0,
                         "end_s": 20.0},
            "evidence_ids": ["w1"], "provenance_ids": ["prov-x"]}]}}
    rows = ea.to_edit_candidates("query-abc", answers)
    assert rows[0]["evidence_ids"] == ["w1"]
    assert rows[0]["requirement_id"] == "req-1"
    assert "rationale" not in rows[0]


def test_ambiguities_name_lane_and_interval():
    model = {"observations": [{
        "id": "cand-1", "provenance_id": "prov-digest1-m7",
        "modality": "event_candidate",
        "payload": {"predicate": "pick_up_put_down"},
        "interval": {"clock_id": "clk", "start": 10000, "end": 15000}}]}
    graph = rg.parse_query("she picks up the phone")
    compiled = rg.compile_graph(graph)
    rows = ea.ambiguities_from_candidates(model, compiled)
    assert rows == [{"lane": "m7", "start_s": 10.0, "end_s": 15.0,
                     "candidate_id": "cand-1"}]


def test_escalation_schedules_heavier_tier_for_open_requirements():
    plan, _ = _plan_for(
        "speaker discussing revenue while a chart is visible",
        {"observations": []})
    answers = {"req-1": {"status": "present"},
               "req-2": {"status": "inconclusive"}}
    second = ea.plan_escalation(plan, answers)
    assert second["action"] == "refine"
    assert all(step["producer"] == "object-detection-owlv2-named"
               for step in second["steps"])
    assert all(step["requirement_id"] == "req-2"
               for step in second["steps"])
    assert second["escalation_round"] is True


def test_escalation_is_no_run_when_nothing_is_open():
    plan, _ = _plan_for(
        "speaker discussing revenue while a chart is visible",
        {"observations": []})
    answers = {"req-1": {"status": "present"},
               "req-2": {"status": "present"}}
    assert ea.plan_escalation(plan, answers)["action"] == "no_run"


def test_escalation_refuses_lanes_with_no_heavier_tier():
    plan, _ = _plan_for("where does the speaker discuss revenue",
                        {"observations": []})
    second = ea.plan_escalation(
        plan, {"req-1": {"status": "inconclusive"}})
    assert second["action"] == "no_run"
    assert "no heavier tier" in second["refused"][0]["reason"]


def test_full_run_lands_cached_evidence_and_requeries():
    model = {"observations": [
        _obs("w1", "m1", "speech_word", "clk", 10000, 20000,
             "speech.word", "revenue discussion here"),
    ]}
    graph = rg.parse_query(
        "speaker discussing revenue while a chart is visible")
    compiled = rg.compile_graph(graph)
    plan = ea.plan_acquisition(
        compiled, model, source_digest="digest1",
        duration_seconds=120.0,
        scope={"clock_id": "clk", "asset_id": "asset1"})
    assert plan["action"] == "refine"

    def cached(**kwargs):
        return {"sufficient": True, "coverage": "complete",
                "producer_version": "vision-test-v1"}

    refreshed = {"observations": list(model["observations"]) + [
        _obs("d1", "object_detection", "object_detection", "clk", 10000,
             20000, "object.detection",
             {"label": "chart", "category": "chart", "score": 0.9}),
    ]}
    report = ea.run_acquisition(
        plan, cached=cached, producers={},
        refresh_model=lambda old: refreshed)
    assert report["landed"] is True
    assert report["requery"] is refreshed


# ── PERF-009 / PERF-014: one acquisition per (lane, interval, producer) ──
#
# A requirement graph asks several questions of the same footage. Each
# question used to schedule its OWN copy of the same producer call over
# the same interval, so the producer ran N times for one measurement,
# the plan's cost estimate counted N times one measurement, and the run
# attributed the result to the LAST question - the earlier one paid for
# the measurement and lost it.


def test_two_questions_of_one_lane_schedule_one_acquisition():
    plan, _ = _plan_for("footage with laughter and applause",
                        {"observations": []})
    assert len(plan["steps"]) == 1
    assert plan["steps"][0]["requirement_ids"] == ["req-1", "req-2"]


def test_a_batched_acquisition_is_costed_once():
    # The detector's measured cost is 1.6 s per window. Twelve windows
    # cost 19.2 s, whatever the number of questions that asked for them.
    plan, _ = _plan_for("scene description and camera movement",
                        {"observations": []},
                        fidelity="exhaustive", allow_escalation=True)
    assert len(plan["steps"]) == 12
    assert plan["estimated_cost_seconds"] == pytest.approx(19.2)
    assert plan["batching"]["duplicate_acquisitions_removed"] == 24
    assert plan["batching"]["requirements_served"] == 36


def test_the_batched_step_names_every_question_it_answers():
    plan, _ = _plan_for("footage with laughter and applause",
                        {"observations": []})
    why = plan["steps"][0]["why"]
    assert "req-1" in why and "req-2" in why


def test_the_producer_is_paid_once_and_answers_every_question():
    plan, _ = _plan_for("footage with laughter and applause",
                        {"observations": []})
    calls = []

    def producer(**kwargs):
        calls.append(kwargs)
        return {"measured": True, "cost_seconds": 0.0, "peak_rss_gb": 0.1,
                "producer_version": "sound-test-v1"}

    report = ea.run_acquisition(
        plan, cached=lambda **kw: None, producers={"m5": producer},
        refresh_model=lambda old: {"observations": []})
    assert len(calls) == 1
    rows = report["acquisition"]["steps"]
    assert len(rows) == 1
    assert rows[0]["requirement_ids"] == ["req-1", "req-2"]


def test_a_batched_acquisition_is_recorded_once_in_the_ledger():
    plan, _ = _plan_for("footage with laughter and applause",
                        {"observations": []})
    ledger = {}

    def producer(**kwargs):
        return {"measured": True, "cost_seconds": 0.0, "peak_rss_gb": 0.1,
                "producer_version": "sound-test-v1"}

    ea.run_acquisition(plan, cached=lambda **kw: None,
                       producers={"m5": producer}, ledger=ledger,
                       refresh_model=lambda old: {"observations": []})
    key = ea.ledger_key("digest1", "m5", {"start_s": 0.0, "end_s": 120.0},
                        "m5-cached-events")
    assert list(ledger) == [key]


def test_escalation_batches_one_window_for_two_questions():
    plan, _ = _plan_for("scene description and camera movement",
                        {"observations": []},
                        fidelity="exhaustive", allow_escalation=True)
    answers = {"req-1": {"status": "inconclusive"},
               "req-2": {"status": "inconclusive"},
               "req-3": {"status": "inconclusive"}}
    second = ea.plan_escalation(plan, answers)
    windows = {(row["interval"]["start_s"], row["interval"]["end_s"])
               for row in second["steps"]}
    assert len(second["steps"]) == len(windows)
    assert all(row["requirement_ids"] == ["req-1", "req-2", "req-3"]
               for row in second["steps"])
    assert second["batching"]["duplicate_acquisitions_removed"] > 0


# ── Every acquisition names the question it answers ─────────────────
#
# A cost is only justifiable against a question. One measurement can
# answer several questions of the same footage, so EVERY question it
# serves is named on the step, and an escalation says which cheaper
# tier could not answer it.


def test_every_scheduled_inference_names_the_question_it_answers():
    plan, _ = _plan_for(
        "speaker discussing revenue while a chart is visible",
        {"observations": []})

    assert plan["steps"]
    for step in plan["steps"]:
        question = step["unresolved_question"]
        assert question["requirement_ids"] == step["requirement_ids"]
        assert question["lane"] == step["lane"]
        assert question["interval"] == step["interval"]
        assert question["answer_status"]
        assert set(question["answer_statuses"]) == set(
            step["requirement_ids"])


def test_a_batched_acquisition_names_every_question_it_answers():
    plan, _ = _plan_for("footage with laughter and applause",
                        {"observations": []})
    question = plan["steps"][0]["unresolved_question"]
    assert question["requirement_ids"] == ["req-1", "req-2"]
    assert set(question["answer_statuses"]) == {"req-1", "req-2"}


def test_an_escalation_names_the_cheaper_tier_that_could_not_answer_it():
    """The escalation is a second round bought with a cost, so it says
    which cheaper tier ran first and which question it answers."""
    plan, _ = _plan_for(
        "speaker discussing revenue while a chart is visible",
        {"observations": []})
    answers = {"req-1": {"status": "present"},
               "req-2": {"status": "inconclusive"}}
    second = ea.plan_escalation(plan, answers)

    assert second["steps"]
    for step in second["steps"]:
        assert step["escalated_from"], (
            "an escalation must name the tier that could not answer it")
        question = step["unresolved_question"]
        assert question["requirement_ids"] == step["requirement_ids"]
        assert "inconclusive" in set(question["answer_statuses"].values())
