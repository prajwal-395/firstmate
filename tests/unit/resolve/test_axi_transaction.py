"""Defect-naming tests for the AXI compound-transaction envelope.

Every test here names a defect that was open before the transaction
envelope existed: a batch shape with no undo path, a near-miss project
name treated as a match, a retry that wrote twice, an unresolved
recovery that did not block the next write, a receipt that did not say
what was left alone, and a ladder with no PROMOTED state.
"""
from __future__ import annotations

import json

import pytest

from library.tools import axi_qualification, axi_transaction, transaction_receipt


def _event(**fields):
    return {"fields": dict(fields)}


def _ops(*pairs):
    return [{"op": op, "unique_id": unique_id} for op, unique_id in pairs]


# ── AXI-043 / AXI-044: the declared compound transactions ─────────────

def test_a_batch_shape_with_no_executor_and_no_undo_path_is_refused():
    plan = axi_transaction.plan_operations(
        operations=_ops(("clip.move", "a"), ("marker.add", "b")),
        backend="resolve_axi", project="p", timeline="t")

    assert plan.supported is False
    assert plan.refusal_category == "unsupported"
    assert "no supported transaction shape" in plan.refusal


def test_a_declared_compound_without_an_execution_path_is_refused_with_its_evidence():
    plan = axi_transaction.plan_operations(
        operations=_ops(("clip.remove", "a"), ("clip.ripple", "b")),
        backend="resolve_axi", project="p", timeline="t")

    assert plan.supported is False
    assert plan.scenario == "remove_ripple"
    assert plan.qualification_gaps
    assert "live" in plan.qualification_gaps[0].lower() or \
        "resolve" in plan.qualification_gaps[0].lower()


def test_every_ax044_shape_is_declared_with_its_qualification_boundaries():
    declared = {name for name, entry in axi_transaction.COMPOUND_SCENARIOS.items()
                if entry.punch_list_item == "AXI-044"}

    assert declared == {"trim_insert", "remove_ripple", "replace_caption",
                        "broll_over_linked_audio"}
    for name in declared:
        entry = axi_transaction.COMPOUND_SCENARIOS[name]
        assert entry.operations
        assert entry.qualification
        assert entry.live_evidence


def test_a_compound_shape_declares_the_predicate_fields_the_registry_reads():
    from library.tools.axi_qualification import PREDICATE_FIELDS

    for entry in axi_transaction.COMPOUND_SCENARIOS.values():
        for predicate in entry.qualification:
            assert predicate["field"] in PREDICATE_FIELDS
            assert predicate["operator"] in {"equals", "one_of"}


def test_a_recoverable_batch_names_its_undo_strategy():
    plan = axi_transaction.plan_operations(
        operations=_ops(("clip.trim", "a"), ("clip.insert", "b")),
        backend="reel_touchup", project="p", timeline="t")

    assert plan.supported is True
    assert plan.scenario == "trim_insert"
    assert plan.atomicity == "recoverable_sequence"
    assert plan.recovery == "reverse_order_undo"


def test_a_batch_whose_targets_do_not_relate_as_declared_is_not_supported():
    # trim_insert declares DISTINCT targets: a trim and an insert of the
    # same row is a different edit, not that compound.
    plan = axi_transaction.plan_operations(
        operations=_ops(("clip.trim", "a"), ("clip.insert", "a")),
        backend="reel_touchup", project="p", timeline="t")

    assert plan.supported is False


# ── AXI-045: conflict detection before mutation ──────────────────────

def test_a_near_miss_project_name_is_a_conflict_not_a_tolerance():
    conflict = axi_transaction.detect_conflict(
        expected_project="Reel One", expected_timeline="t",
        observed_project="Reel  One", observed_timeline="t")

    assert conflict.detected is True
    assert conflict.category == "identity"


def test_a_near_miss_timeline_name_is_a_conflict():
    conflict = axi_transaction.detect_conflict(
        expected_project="p", expected_timeline="Timeline 1",
        observed_project="p", observed_timeline="Timeline 1 ")

    assert conflict.detected is True
    assert conflict.category == "identity"


def test_a_moved_generation_is_a_revision_conflict():
    conflict = axi_transaction.detect_conflict(
        expected_project="p", expected_timeline="t",
        observed_project="p", observed_timeline="t",
        expected_base_generation=7, observed_generation=8)

    assert conflict.detected is True
    assert conflict.category == "revision"


def test_a_moved_project_model_revision_is_a_revision_conflict():
    conflict = axi_transaction.detect_conflict(
        expected_project="p", expected_timeline="t",
        observed_project="p", observed_timeline="t",
        expected_model_revision=3, observed_model_revision=4)

    assert conflict.detected is True
    assert conflict.category == "revision"


def test_the_same_identity_and_revision_is_not_a_conflict():
    conflict = axi_transaction.detect_conflict(
        expected_project="p", expected_timeline="t",
        observed_project="p", observed_timeline="t",
        expected_base_generation=7, observed_generation=7,
        expected_model_revision=3, observed_model_revision=3)

    assert conflict.detected is False


# ── AXI-046: idempotent submission ────────────────────────────────────

def test_a_retry_of_a_committed_transaction_answers_with_the_prior_outcome(
        monkeypatch, tmp_path):
    digest = axi_transaction.intent_digest(_ops(("clip.delete", "a")))
    monkeypatch.setattr(axi_transaction, "_journal_events", lambda _folder: [
        _event(transaction_id="tx-1", stage="committed",
               intent_digest=digest, receipt={"status": "committed"})])

    outcome = axi_transaction.prior_outcome(
        str(tmp_path), "tx-1", operations=_ops(("clip.delete", "a")))

    assert outcome["status"] == "committed"
    assert outcome["receipt"]["status"] == "committed"


def test_the_same_transaction_id_with_different_operations_is_a_refusal(
        monkeypatch, tmp_path):
    digest = axi_transaction.intent_digest(_ops(("clip.delete", "a")))
    monkeypatch.setattr(axi_transaction, "_journal_events", lambda _folder: [
        _event(transaction_id="tx-1", stage="committed", intent_digest=digest)])

    outcome = axi_transaction.prior_outcome(
        str(tmp_path), "tx-1", operations=_ops(("clip.delete", "b")))

    assert outcome["status"] == "conflict"


def test_a_transaction_that_was_never_submitted_has_no_prior_outcome(
        monkeypatch, tmp_path):
    monkeypatch.setattr(axi_transaction, "_journal_events", lambda _folder: [])

    outcome = axi_transaction.prior_outcome(
        str(tmp_path), "tx-1", operations=_ops(("clip.delete", "a")))

    assert outcome is None


# ── AXI-047 / AXI-048: the recovery state ─────────────────────────────

def test_an_unresolved_recovery_blocks_a_further_mutation(monkeypatch, tmp_path):
    monkeypatch.setattr(axi_transaction, "_journal_events", lambda _folder: [
        _event(transaction_id="tx-old", stage="operation_applying",
               project="p", timeline="t", index=0,
               operations=[{"op": "clip.delete"}])])

    with pytest.raises(axi_transaction.TransactionRefused) as refusal:
        axi_transaction.require_recovery_clear(
            str(tmp_path), project="p", timeline="t")

    assert refusal.value.category == "recovery_required"
    assert "tx-old" in refusal.value.reason


def test_a_committed_transaction_does_not_block_a_further_mutation(
        monkeypatch, tmp_path):
    monkeypatch.setattr(axi_transaction, "_journal_events", lambda _folder: [
        _event(transaction_id="tx-old", stage="committed",
               project="p", timeline="t")])

    state = axi_transaction.require_recovery_clear(
        str(tmp_path), project="p", timeline="t")

    assert state.unresolved is False


def test_a_write_that_failed_verification_keeps_blocking(monkeypatch, tmp_path):
    monkeypatch.setattr(axi_transaction, "_journal_events", lambda _folder: [
        _event(transaction_id="tx-old", stage="recorded",
               status="verification_failed", project="p", timeline="t")])

    state = axi_transaction.recovery_state(str(tmp_path), project="p",
                                           timeline="t")

    assert state.unresolved is True
    assert state.transactions[0]["transaction_id"] == "tx-old"


def test_an_unresolved_recovery_on_another_timeline_does_not_block(
        monkeypatch, tmp_path):
    monkeypatch.setattr(axi_transaction, "_journal_events", lambda _folder: [
        _event(transaction_id="tx-old", stage="operation_applying",
               project="p", timeline="other", index=0,
               operations=[{"op": "clip.delete"}])])

    state = axi_transaction.recovery_state(str(tmp_path), project="p",
                                           timeline="t")

    assert state.unresolved is False


def test_a_recovery_can_be_closed_and_the_block_lifts(monkeypatch, tmp_path):
    from library.tools import event_log

    events = [_event(transaction_id="tx-old", stage="operation_applying",
                     project="p", timeline="t", index=0,
                     operations=[{"op": "clip.delete"}])]
    monkeypatch.setattr(axi_transaction, "_journal_events",
                        lambda _folder: list(events))
    monkeypatch.setattr(event_log, "emit",
                        lambda _folder, _kind, **fields: (
                            events.append({"fields": dict(fields)})) or {})

    outcome = axi_transaction.resolve_recovery(
        str(tmp_path), "tx-old", action="abandoned",
        reason="the captain accepted the timeline as it stands")

    assert outcome["action"] == "abandoned"
    state = axi_transaction.recovery_state(str(tmp_path), project="p",
                                           timeline="t")
    assert state.unresolved is False


def test_closing_a_recovery_that_was_never_outstanding_is_refused(
        monkeypatch, tmp_path):
    monkeypatch.setattr(axi_transaction, "_journal_events", lambda _folder: [])

    with pytest.raises(axi_transaction.TransactionRefused,
                       match="no outstanding recovery"):
        axi_transaction.resolve_recovery(str(tmp_path), "tx-old")


def test_a_recovery_cannot_be_closed_as_something_else(tmp_path):
    with pytest.raises(ValueError):
        axi_transaction.resolve_recovery(str(tmp_path), "tx-old",
                                         action="forgotten")


# ── the gate reads the journal incrementally ──────────────────────────

def _journal(tmp_path):
    from library.tools import event_log

    path = event_log.log_path(str(tmp_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def test_the_recovery_gate_parses_only_what_was_journalled_since(tmp_path,
                                                                 monkeypatch):
    """Defect: a gate that re-parsed the whole journal before every write
    made a build quadratic in its own history."""
    path = _journal(tmp_path)
    path.write_text("", encoding="utf-8")
    calls = []
    real = json.loads

    def counting(text, *args, **kwargs):
        calls.append(text)
        return real(text, *args, **kwargs)

    monkeypatch.setattr(axi_transaction.json, "loads", counting)
    for index in range(5):
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(
                {"fields": {"transaction_id": f"tx-{index}",
                            "stage": "committed"}}) + "\n")

    axi_transaction.recovery_state(str(tmp_path))
    assert len(calls) == 5

    calls.clear()
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(
            {"fields": {"transaction_id": "tx-5", "stage": "committed"}}) + "\n")

    axi_transaction.recovery_state(str(tmp_path))
    assert len(calls) == 1


def test_a_replaced_journal_is_read_again_from_the_start(tmp_path):
    """Defect: a cursor that trusted its offset past a replaced journal
    would answer from a history that no longer exists."""
    path = _journal(tmp_path)
    path.write_text("", encoding="utf-8")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(
            {"fields": {"transaction_id": "tx-old",
                        "stage": "operation_applying", "project": "p",
                        "timeline": "t", "index": 0,
                        "operations": [{"op": "clip.delete"}]}}) + "\n")
    assert axi_transaction.recovery_state(str(tmp_path)).unresolved is True

    path.write_text("", encoding="utf-8")

    assert axi_transaction.recovery_state(str(tmp_path)).unresolved is False


# ── AXI-049 / AXI-050: the receipt ────────────────────────────────────

def test_the_receipt_names_what_was_asked_what_happened_and_what_was_left_alone():
    before = {"tracks": [{"clips": [
        {"unique_id": "a", "record_in": 0, "record_out": 50},
        {"unique_id": "b", "record_in": 50, "record_out": 100}]}]}
    after = {"tracks": [{"clips": [
        {"unique_id": "a", "record_in": 0, "record_out": 50},
        {"unique_id": "b", "record_in": 50, "record_out": 100}]}]}

    receipt = axi_transaction.build_receipt(
        request_id="tx-1", project="p", timeline="t", backend="edit_patch",
        operations=_ops(("clip.delete", "a")),
        actual_changes=[{"index": 0, "op": "clip.delete"}],
        verification_evidence=[{"operation": "clip.delete", "passed": True}],
        untouched=axi_transaction.untouched_regions(
            operations=_ops(("clip.delete", "a")), affected_spans=((0, 50),),
            before=before, after=after),
        route={"backend": "edit_patch"}, errors=[],
        recovery={"status": "not_required"})

    assert receipt["requested_changes"][0]["op"] == "clip.delete"
    assert receipt["actual_changes"][0]["op"] == "clip.delete"
    assert receipt["untouched_regions"][0]["unique_id"] == "b"
    assert receipt["untouched_regions"][0]["status"] == "unchanged"
    assert receipt["route"]["backend"] == "edit_patch"
    assert receipt["errors"] == []
    assert receipt["recovery"]["status"] == "not_required"
    assert receipt["verification_evidence"]


def test_a_region_the_transaction_did_change_is_not_reported_as_untouched():
    before = {"tracks": [{"clips": [
        {"unique_id": "a", "record_in": 0, "record_out": 50}]}]}
    after = {"tracks": [{"clips": [
        {"unique_id": "a", "record_in": 0, "record_out": 40}]}]}

    regions = axi_transaction.untouched_regions(
        operations=_ops(("clip.trim", "a")), affected_spans=((0, 50),),
        before=before, after=after)

    assert regions == []


def test_the_state_ladder_distinguishes_prepared_applied_verified_committed_promoted_delivered():
    ladder = axi_transaction.state_ladder(
        prepared=True, applied=True, verified=True, committed=True,
        promoted=False, delivered=False)

    assert ladder["stage"] == "COMMITTED"
    assert ladder["reached"] == ["PREPARED", "APPLIED", "VERIFIED", "COMMITTED"]
    assert ladder["states"]["PROMOTED"] is False
    assert ladder["states"]["DELIVERED"] is False


def test_a_promoted_transaction_reports_promoted_and_not_committed():
    ladder = axi_transaction.state_ladder(
        prepared=True, applied=True, verified=True, committed=True,
        promoted=True, delivered=False)

    assert ladder["stage"] == "PROMOTED"


def test_the_standardized_receipt_reports_prepared_and_promoted_apart():
    receipt = transaction_receipt.standardize({
        "status": "committed", "resolve_write_status": "committed",
        "patch_id": "tx-1", "project": "p", "timeline": "t",
        "base_generation": 4, "generation": 5,
        "operations": [{"failure": None}],
        "requested_operation_count": 1,
        "acceptance_contracts": [{"passed": True}],
        "journal": {"intent_persisted": True},
        "promotion": {"status": "awaiting_promotion"}})

    assert receipt["prepared"]["status"] == "prepared"
    assert receipt["promoted"]["status"] == "awaiting_promotion"
    assert receipt["lifecycle"]["stage"] == "COMMITTED"
    assert "PROMOTED" not in receipt["lifecycle"]["history"]
    assert "PREPARED" in receipt["lifecycle"]["history"]


def test_a_promoted_receipt_reports_the_promoted_stage():
    receipt = transaction_receipt.standardize({
        "status": "committed", "resolve_write_status": "committed",
        "patch_id": "tx-1", "project": "p", "timeline": "t",
        "base_generation": 4, "generation": 5,
        "operations": [{"failure": None}],
        "requested_operation_count": 1,
        "acceptance_contracts": [{"passed": True}],
        "journal": {"intent_persisted": True},
        "promotion": {"status": "promoted"}})

    assert receipt["promoted"]["status"] == "promoted"
    assert receipt["lifecycle"]["stage"] == "PROMOTED"
    assert "PROMOTED" in receipt["lifecycle"]["history"]


def test_a_write_whose_intent_never_reached_the_journal_is_not_prepared():
    receipt = transaction_receipt.standardize({
        "status": "committed", "resolve_write_status": "committed",
        "patch_id": "tx-1", "project": "p", "timeline": "t",
        "base_generation": 4, "generation": 5,
        "operations": [{"failure": None}],
        "requested_operation_count": 1,
        "acceptance_contracts": [{"passed": True}],
        "journal": {"intent_persisted": False}})

    assert receipt["prepared"]["status"] == "not_prepared"


# ── the compound qualification registry ──────────────────────────────

def _compound_report(scenario="trim_insert", backend="reel_touchup"):
    """A COMPOUND qualification report in the current evidence format.

    A compound transaction is qualified by the same evidence rules as a
    single operation: the same environment, the same semantic boundary,
    the same coverage, the same predicates. Only the identity differs.
    """
    return {
        "schema": axi_qualification.COMPOUND_REPORT_SCHEMA,
        "scenario": scenario,
        "backend": backend,
        "result": "passed",
        "evidence": "tests/live/results/trim_insert.json",
        "resolve_version": "21.1.0.14",
        "environment": {"platform": "darwin",
                        "resolve_version": "21.1.0.14"},
        "semantic_boundary": {
            "applies_to": ["a trim and an insert on one timeline"],
            "does_not_apply_to": ["a trim and an insert on the same row"],
        },
        "coverage": {
            "source": "measured",
            "configurations": ["trim_then_insert_1080p24"],
            "dimensions": {
                "clip_composition": ["single_video_clip"],
                "existing_effects": ["none"],
                "frame_rate": ["24"],
                "linked_audio_configuration": ["no_linked_audio"],
                "media_type": ["video"],
                "project_settings": ["landscape_1920x1080"],
                "resolve_version": ["21.1.0.14"],
                "timeline_density": ["light"],
                "timeline_structure": ["multi_item_timeline"],
                "track_layout": ["video"],
                "track_occupancy": ["mixed"],
            },
            "uncovered": {},
        },
        "project_conditions": ["isolated qualification project"],
        "qualification_predicates": [
            {"field": "media_type", "operator": "equals", "value": "video"},
        ],
        "supported_media_types": ["video"],
        "expected_state": {"trim": "shortened"},
        "actual_state": {"trim": "shortened"},
        "preservation_checks": [{"name": "unrelated clips", "result": "passed"}],
        "preservation_limits": ["normal video clips"],
        "visual_verification": ["rendered frames verified"],
        "audio_verification": ["dialogue audio unchanged"],
        "recovery_status": "passed",
    }


def test_compound_evidence_enables_only_its_exact_scenario_and_backend(
        tmp_path):
    report_path = tmp_path / "compound.json"
    registry_path = tmp_path / "registry.json"
    report_path.write_text(json.dumps(_compound_report()), encoding="utf-8")

    pair = axi_qualification.import_result(report_path, registry_path)
    records = axi_qualification.load_compound_records(registry_path)

    assert pair == ("trim_insert", "reel_touchup")
    assert set(records) == {pair}
    assert records[pair]["production_enabled"] is True
    # A single-operation row is a different table.
    assert axi_qualification.load_qualification_records(registry_path) == {}


def test_compound_evidence_for_an_undeclared_shape_is_refused():
    report = _compound_report(scenario="not_a_shape")

    with pytest.raises(axi_qualification.QualificationReportError):
        axi_qualification.parse_result(report)


def test_compound_evidence_for_a_backend_without_a_path_is_refused():
    report = _compound_report(backend="edit_patch")

    with pytest.raises(axi_qualification.QualificationReportError):
        axi_qualification.parse_result(report)


def test_a_compound_report_missing_evidence_is_refused():
    report = _compound_report()
    report["preservation_checks"] = []

    with pytest.raises(axi_qualification.QualificationReportError):
        axi_qualification.parse_result(report)


def test_a_qualified_compound_shape_reports_no_qualification_gap(monkeypatch):
    monkeypatch.setattr(axi_transaction, "qualified_compound_records",
                        lambda: {("trim_insert", "reel_touchup"): {"result": "passed"}})

    gaps = axi_transaction.qualification_gaps(
        axi_transaction.COMPOUND_SCENARIOS["trim_insert"], "reel_touchup")

    assert gaps == ()
