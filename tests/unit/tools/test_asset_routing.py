"""The routing contract's behaviour: tier order, the decision record,
and the refusal that reaches the record instead of aborting routing."""
from __future__ import annotations

import json

import pytest

from library.tools.asset_routing import (
    NEED_KINDS,
    ROUTE_TIERS,
    Need,
    NeedRefused,
    append_decision,
    decision_id,
    read_decisions,
    register_handler,
    route_need,
)
from library.tools.ren_refusal import RenRefusal


@pytest.fixture(autouse=True)
def _clear_handlers():
    from library.tools import asset_routing
    saved = {tier: dict(handlers) for tier, handlers in
              asset_routing._HANDLERS.items()}
    for tier in ROUTE_TIERS:
        asset_routing._HANDLERS[tier].clear()
    try:
        yield
    finally:
        for tier in ROUTE_TIERS:
            asset_routing._HANDLERS[tier].clear()
            asset_routing._HANDLERS[tier].update(saved.get(tier, {}))


def _solving_handler(name, artifact=None, provenance=None):
    def handler(need, context):
        return {
            "handler": name,
            "solved": True,
            "artifact": artifact or {"path": f"{name}.wav",
                                     "media_type": "audio"},
            "provenance": provenance or {"producer": name,
                                         "method": "OBSERVED",
                                         "derived_from": [], "detail": ""},
            "reason": None,
            "refusal": None,
        }
    return handler


def _refusing_handler(name, why):
    def handler(need, context):
        raise RenRefusal(why, "no asset answers this need",
                         "configure a provider, or drop the need", blocker="unsupported_operation")
    return handler


def test_a_need_routes_to_the_first_tier_that_can_solve_it():
    register_handler("existing_asset", "library_search",
                     _refusing_handler("library_search",
                                       "no held asset answers"))
    register_handler("transformation", "room_tone_fill",
                     _solving_handler("room_tone_fill"))
    register_handler("generative", "video_provider",
                     _solving_handler("video_provider"))

    record = route_need(Need("room_tone", {"fill_seconds": 1.0}),
                        {"project_folder": "/tmp/p", "step_id": "s"})

    assert record["answered_by"] == "transformation"
    assert record["artifact"]["path"] == "room_tone_fill.wav"
    assert record["provenance"]["producer"] == "room_tone_fill"


def test_a_solved_need_does_not_consult_higher_tiers():
    register_handler("existing_asset", "library_search",
                     _solving_handler("library_search"))
    register_handler("transformation", "room_tone_fill",
                     _solving_handler("room_tone_fill"))

    record = route_need(Need("room_tone", {"fill_seconds": 1.0}))

    tiers = {t["tier"]: t for t in record["tiers"]}
    assert tiers["existing_asset"]["consulted"] is True
    assert tiers["transformation"]["consulted"] is False
    assert tiers["generative"]["consulted"] is False
    assert "answered by existing_asset" in tiers["transformation"]["reason"]


def test_an_unsolvable_need_records_every_tier_and_answers_none():
    register_handler("existing_asset", "library_search",
                     _refusing_handler("library_search", "nothing held"))
    register_handler("transformation", "freeze_frame",
                     _refusing_handler("freeze_frame", "no boundary"))

    record = route_need(Need("missing_frames", {"frames": 4}))

    assert record["answered_by"] is None
    assert record["artifact"] is None
    reasons = [o["reason"] for t in record["tiers"] for o in t["outcomes"]]
    assert reasons == ["nothing held", "no boundary"]


def test_a_handlers_refusal_is_recorded_by_name_not_raised():
    register_handler("generative", "video_provider",
                     _refusing_handler("video_provider",
                                       "no video generator is configured"))

    record = route_need(Need("establishing_shot", {"subject": "harbor"}))

    outcome = record["tiers"][-1]["outcomes"][-1]
    assert outcome["solved"] is False
    assert outcome["refusal"]["what"] == "no video generator is configured"
    assert outcome["refusal"]["code"] == "REN_REFUSAL"


def test_an_unknown_need_kind_refuses_with_the_vocabulary():
    with pytest.raises(NeedRefused) as excinfo:
        Need("telepathy", {})
    assert "telepathy" in excinfo.value.what
    assert "not a need it can route" in excinfo.value.what
    for kind in NEED_KINDS:
        assert kind in excinfo.value.fix


def test_decision_id_is_stable_per_need_and_sensitive_to_params():
    first = decision_id(Need("room_tone", {"fill_seconds": 1.0}))
    second = decision_id(Need("room_tone", {"fill_seconds": 1.0}))
    third = decision_id(Need("room_tone", {"fill_seconds": 2.0}))
    assert first == second
    assert first != third


def test_the_decision_ledger_appends_and_reads_back(tmp_path):
    record = route_need(Need("room_tone", {"fill_seconds": 1.0}),
                        {"project_folder": str(tmp_path), "step_id": "s"})
    append_decision(str(tmp_path), record)
    append_decision(str(tmp_path), route_need(Need("b_roll", {})))

    records = read_decisions(str(tmp_path))
    assert [r["need"]["kind"] for r in records] == ["room_tone", "b_roll"]
    with open(tmp_path / "pipeline_output" / "routing" / "decisions.jsonl",
              encoding="utf-8") as handle:
        lines = handle.read().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["decision_id"] == record["decision_id"]


def test_an_absent_ledger_reads_empty(tmp_path):
    assert read_decisions(str(tmp_path)) == []


def test_a_handler_returning_a_non_contract_outcome_refuses():
    def broken(need, context):
        return {"solved": "maybe"}

    register_handler("transformation", "broken", broken)
    with pytest.raises(NeedRefused):
        route_need(Need("room_tone", {}))


def test_registering_on_an_unknown_tier_refuses():
    with pytest.raises(NeedRefused) as excinfo:
        register_handler("wishful_thinking", "x", lambda n, c: {})
    assert "wishful_thinking" in excinfo.value.what
