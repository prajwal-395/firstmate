"""AGENT-015: a failure names its next step, so no retry is improvised.

Three vocabularies already answered three different questions about a
failure, and nothing joined them: the recovery CLASS (should the host
act, ask or wait), the failure CATEGORY (what kind of failure was
this) and the recovery ACTION (what happens next). The host held all
three in its head and improvised the join - which is how a stale base
became "try it again" and a failed read-back became "retry the same
way".

These tests name the defects that made the join necessary:

- every failure category has a recovery action and a recovery class, so
  the next step is read rather than invented;
- a category with no named recovery REFUSES, because a silent fallback
  puts back the guess the instruction exists to remove;
- the action vocabulary has ONE owner - `transaction_receipt` - so the
  two cannot drift;
- the AXI failure receipt carries the instruction, and its
  `recovery.action` is the real next step rather than "none";
- a Resolve timeout and a degraded Resolve are typed refusals, so a
  host that meets one knows the next step instead of improvising it.
"""
from __future__ import annotations

import pytest

from library.tools import recovery_instruction as ri
from library.tools import transaction_orchestration, transaction_receipt


def test_every_failure_category_has_a_recovery_action_and_a_class():
    # The join is the missing piece. A category with no action is a
    # category the host has to improvise around.
    for category in transaction_orchestration.ExecutionRefusal.CATEGORIES:
        typed = ri.instruction(category)
        assert typed["action"], category
        assert typed["recovery_class"] in ri.RECOVERY_CLASSES, category
        assert typed["category"] == category


def test_a_category_with_no_named_recovery_refuses_rather_than_guessing():
    with pytest.raises(ri.UnknownRecoveryCategory):
        ri.instruction("something_new")
    with pytest.raises(ri.UnknownRecoveryCategory):
        ri.recovery_class_for("something_new")
    with pytest.raises(ri.UnknownRecoveryCategory):
        ri.retryable_for("something_new")


def test_the_action_comes_from_the_one_action_vocabulary():
    # `transaction_receipt.RECOVERY_ACTIONS` owns the action words. A
    # second list of them is how two readers disagree.
    for category in transaction_orchestration.ExecutionRefusal.CATEGORIES:
        typed = ri.instruction(category)
        assert typed["action"] in set(transaction_receipt.RECOVERY_ACTIONS.values())
        assert typed["recovery_type"] in transaction_receipt.RECOVERY_ACTIONS


def test_a_stale_base_is_auto_recoverable_and_a_missing_route_is_not():
    # Defect: every failure looked the same, so the host either
    # retried everything or asked about everything.
    assert ri.recovery_class_for("stale") == "auto_recoverable"
    assert ri.recovery_class_for("unsupported") == "blocked"
    assert ri.recovery_class_for("conflict") == "needs_question"
    assert ri.recovery_class_for("recovery_required") == "blocked"


def test_only_a_stale_base_may_be_retried_as_it_stands():
    # Defect: `retryable` was hard-coded False on the AXI path, which
    # said "not retryable" for a failure nobody had decided about.
    assert ri.retryable_for("stale") is True
    for category in ("unsupported", "conflict", "backend_failure",
                     "verification_failed", "recovery_required"):
        assert ri.retryable_for(category) is False, category


def test_the_execution_refusal_carries_its_instruction():
    refusal = transaction_orchestration.ExecutionRefusal(
        "stale", "the base generation moved")
    typed = refusal.recovery_instruction
    assert typed["category"] == "stale"
    assert typed["recovery_class"] == "auto_recoverable"
    assert typed["action"] == "rebase_if_provably_safe_else_replan"
    assert typed["reason"] == "the base generation moved"


def test_a_refusal_that_already_decided_its_class_keeps_that_class():
    # The class was decided where the refusal was raised; re-deciding it
    # here would be a second opinion.
    from library.tools.ren_refusal import RenRefusal

    refused = RenRefusal("what", "why", "fix", recovery_class="blocked", blocker="unsafe_state")
    typed = ri.refusal_instruction(refused, category="stale")
    assert typed["recovery_class"] == "blocked"
    assert typed["action"] == "rebase_if_provably_safe_else_replan"


def test_a_resolve_timeout_is_a_typed_refusal_and_keeps_its_message():
    # Defect: a timeout was a bare RuntimeError with a sentence, so a
    # host that met one had to work out whether a retry was safe.
    from library.tools.ren_refusal import RenRefusal
    from library.tools.resolve_deadline import ResolveCallTimeout

    timeout = ResolveCallTimeout(
        "timeline SetSetting did not return within 30s - Resolve is not "
        "answering scripting calls")
    assert isinstance(timeout, RenRefusal)
    assert timeout.code == "REN_RESOLVE_CALL_TIMEOUT"
    assert timeout.recovery_class == "needs_question"
    assert timeout.fix
    # The message still reads as it did: the call sites embed it in
    # their own prose.
    assert str(timeout).startswith("timeline SetSetting did not return")


def test_a_degraded_resolve_is_a_typed_refusal():
    from library.tools.ren_refusal import RenRefusal
    from library.tools.resolve_watchdog import ResolveDegraded

    degraded = ResolveDegraded("probe failed", holder="ren", job_id="j1")
    assert isinstance(degraded, RenRefusal)
    assert degraded.code == "REN_RESOLVE_DEGRADED"
    assert degraded.recovery_class == "blocked"
    assert str(degraded) == "probe failed"
    assert "ren" in degraded.why


def test_the_failure_receipt_publishes_the_next_step_not_none():
    # Defect: the AXI failure receipt carried no `recovery.action`, and
    # `standardize_receipt` publishes `recovery.action` as the
    # RecoveryResult's action - so the published action was "none" and a
    # host had a category name and nothing else.
    from types import SimpleNamespace

    from library.tools.resolve_axi_core import _failure_receipt

    receipt = _failure_receipt(
        request=SimpleNamespace(id="req-1", idempotency_key="req-1",
                                project="Podcast", timeline="Reel 29"),
        backend="incremental", generation=SimpleNamespace(generation=4),
        operations=[{"op": "clip.trim", "unique_id": "clip-1"}],
        receipts=[], decision={"backend": "incremental"},
        category="verification_failed", reason="the read-back disagreed",
        failed_index=0, evidence={"mutated": True})

    assert receipt["recovery"]["action"] == "preserve_readback_and_replan"
    assert receipt["execution_contract"]["recovery"]["action"] == (
        "preserve_readback_and_replan")
    instruction = receipt["recovery"]["instruction"]
    assert instruction["category"] == "verification_failed"
    assert instruction["recovery_class"] == "needs_question"
    assert receipt["refusal"]["retryable"] is False
