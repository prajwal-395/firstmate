"""AXI-003: one acceptance outcome, and the readers that must agree.

Defect-named tests.  Each one names the failure it forbids:

- a MISSING acceptance contract is not a FAILED acceptance contract;
- a receipt that was never measured carries no recovery, because
  nothing is known to be wrong;
- a REFUSAL is a decision, and it does not arrive as an unexpected
  backend exception;
- a semantic patch and a staged reel build publish the SAME receipt
  shape, so a reader asks one question of both.
"""

from __future__ import annotations

import json

import pytest

from library.tools import (acceptance_state, edit_execution_adapters,
                           operation_acceptance, reel_execution_contract,
                           transaction_orchestration, transaction_receipt)
from library.tools.ren_refusal import RenRefusal


# ── the missing contract is not a failed contract ───────────────────

def test_a_contract_with_missing_evidence_is_unmeasured_not_failed():
    """A check nobody measured is a gap, not a wrong answer."""
    result = operation_acceptance.evaluate(
        {"op": "overlay.add", "row": 2, "record_frame": 10},
        readback_passed=True)

    assert result["status"] == operation_acceptance.UNMEASURED
    assert result["failed"] == []
    assert "rendered_pixels_match_request" in result["missing"]


def test_a_contract_with_a_failed_check_is_failed():
    """The distinction is not a way of softening a failure."""
    result = operation_acceptance.evaluate(
        {"op": "clip.move"}, readback_passed=True,
        evidence={"target_identity": False})

    assert result["status"] == operation_acceptance.FAILED
    assert "target_identity" in result["failed"]


def test_a_receipt_with_no_contract_is_unmeasured_and_owes_no_recovery():
    """Defect: a build that worked read as `failed` and carried a
    recovery nothing had to recover."""
    receipt = transaction_receipt.standardize({
        "status": "committed", "patch_id": "p-1",
        "base_generation": 4, "generation": 5,
        "operations": [{"returned": True}],
    })

    assert receipt["verified"]["status"] == "unmeasured"
    assert receipt["verified"]["acceptance_status"] == "unmeasured"
    assert receipt["recovery"] is None
    assert receipt["recovery_result"]["status"] == "not_required"
    assert transaction_receipt.acceptance_outcome(receipt) == "unmeasured"


def test_a_receipt_with_a_failed_contract_still_owes_a_recovery():
    """The other half of the distinction: a real failure still fails."""
    receipt = transaction_receipt.standardize({
        "status": "committed", "patch_id": "p-2",
        "base_generation": 4, "generation": 5,
        "operations": [{"returned": True}],
        "acceptance_contracts": [{"operation": "clip.move",
                                  "status": "failed", "passed": False,
                                  "failed": ["target_identity"],
                                  "missing": []}],
    })

    assert receipt["verified"]["status"] == "failed"
    assert receipt["recovery"] is not None
    assert transaction_receipt.acceptance_outcome(receipt) == "failed"


def test_the_six_lifecycle_blocks_answer_six_separate_questions():
    """A write that landed and an acceptance nobody measured are two
    facts, and the receipt says both."""
    receipt = transaction_receipt.standardize({
        "status": "committed", "patch_id": "p-3",
        "base_generation": 4, "generation": 5,
        "operations": [{"returned": True}],
    })

    assert receipt["applied"]["status"] == "applied"
    assert receipt["applied"]["complete"] is True
    assert receipt["verified"]["status"] == "unmeasured"
    assert receipt["committed"]["status"] == "readback_persisted_unmeasured"
    assert receipt["promoted"]["status"] == "not_requested"
    assert receipt["delivered"]["status"] == "not_requested"


# ── one answer per receipt ──────────────────────────────────────────

def test_every_reader_answers_the_acceptance_the_same_way():
    """Defect: the acceptance contract said `unmeasured`, the lifecycle
    said `failed`, and the two disagreed about the same operation."""
    receipt = transaction_receipt.standardize({
        "status": "committed", "patch_id": "p-4",
        "base_generation": 4, "generation": 5,
        "operations": [{"returned": True}],
        "acceptance_contracts": [operation_acceptance.evaluate(
            {"op": "overlay.add", "row": 1, "record_frame": 4},
            readback_passed=True)],
    })

    contract = receipt["acceptance_contracts"][0]
    assert contract["status"] == operation_acceptance.UNMEASURED
    assert receipt["verified"]["acceptance_status"] == "unmeasured"

    published = transaction_orchestration.standardize_receipt(dict(receipt))
    verification = published["execution_contract"]["verification"]
    assert verification["status"] == "missing_evidence"
    assert verification["layers"]["stored_state"]["status"] == "missing_evidence"

    leg = acceptance_state.operation_result(
        [{"op": contract["operation"], "readback_passed": True,
          "evidence": {}}])
    assert leg["status"] == acceptance_state.UNMEASURED


def test_the_canonical_outcome_is_one_answer_for_a_receipt():
    """One question, one answer: a reader cannot get two."""
    accepted = transaction_receipt.standardize({
        "status": "committed", "patch_id": "p-5",
        "base_generation": 4, "generation": 5,
        "operations": [{"returned": True}],
        "acceptance_contracts": [{"operation": "clip.move",
                                  "status": "passed", "passed": True,
                                  "failed": [], "missing": []}],
    })
    assert transaction_receipt.acceptance_outcome(accepted) == "accepted"

    refused = transaction_receipt.standardize({
        "status": "refused", "patch_id": "p-6",
        "operations": [],
        "refusal": {"category": "conflict", "reason": "nothing ran"},
    })
    assert transaction_receipt.acceptance_outcome(refused) == "refused"


# ── a refusal is a decision, not an exception ───────────────────────

def test_a_refusal_exception_is_not_normalized_as_a_backend_failure():
    """Defect: a refusal had no `category`, so the shared contract read
    it as `backend_failure` - an unexpected exception."""
    refusal = RenRefusal(
        "the timeline does not carry the clip",
        "the clip the request names is not on the timeline",
        "run `resolve-axi items --timeline \"Reel 1\"` and name a clip "
        "that is on it",
        blocker="unsupported_operation")

    normalized = transaction_orchestration.ExecutionRefusal.from_exception(
        refusal)

    assert normalized.category != "backend_failure"
    assert normalized.category == "unsupported"
    assert normalized.fix == refusal.fix
    assert normalized.recovery["blocker"] == "unsupported_operation"
    assert normalized.recovery["next_action"] == refusal.next_action


def test_a_refusal_receipt_is_refused_and_carries_the_refusal():
    """A refusal that ran nothing is not a failure, and it carries the
    refusal's own fix and next action."""
    receipt = transaction_receipt.standardize({
        "status": "refused", "patch_id": "p-7",
        "operations": [],
        "refusal": {"category": "conflict", "reason": "nothing ran",
                    "engine_refusal": {"code": "REN_UNSUPPORTED",
                                        "fix": "name a clip that is there",
                                        "blocker": "unsupported_operation",
                                        "next_action": "use_a_supported_operation"}},
    })

    assert transaction_receipt.acceptance_outcome(receipt) == "refused"
    assert receipt["refusal"]["engine_refusal"]["fix"] == (
        "name a clip that is there")


# ── one shape for every production path ─────────────────────────────

def test_the_build_publishes_the_same_receipt_shape_as_a_patch():
    """Defect: the build published its own shape, so a reader had to
    know which path produced a receipt before it could read it."""
    from scripts.axi003_capture_receipts import CORE_KEYS  # noqa: PLC0415

    patch = transaction_orchestration.standardize_receipt({
        "status": "committed", "patch_id": "p-8", "backend": "edit_patch",
        "base_generation": 1, "generation": 2,
        "operations": [{"index": 0, "op": "clip.set_property",
                        "returned": True, "failure": None}],
        "acceptance_contracts": [{"operation": "clip.set_property",
                                  "status": "passed", "passed": True,
                                  "failed": [], "missing": []}],
    })
    build = reel_execution_contract.publish({
        "timeline_name": "Reel 1",
        "placement_check": {"passed": True, "repair_attempted": False},
        "stream_enforcement": {"checked": 2, "deleted": [], "unverified": []},
        "overlay_sweep": None,
        "placements": [{"unique_id": "clip-1"}],
    }, backend="full_rebuild")

    def core(receipt):
        return tuple(key for key in CORE_KEYS if key in receipt)

    assert core(build) == core(patch)


def test_the_build_keeps_its_own_keys():
    """Publishing through the common shape is ADDITIVE."""
    original = {"timeline_name": "Reel 1",
                "placement_check": {"passed": True, "repair_attempted": False},
                "stream_enforcement": {"checked": 0, "deleted": [],
                                       "unverified": []}}
    published = reel_execution_contract.publish(original,
                                                backend="full_rebuild")

    for key, value in original.items():
        assert published[key] == value


def test_a_build_whose_check_failed_is_failed_not_unmeasured():
    """The build's own measurement is a measurement: it failed."""
    published = reel_execution_contract.publish({
        "timeline_name": "Reel 1",
        "placement_check": {"passed": False, "repair_attempted": True},
        "stream_enforcement": {"checked": 2, "deleted": [], "unverified": []},
    }, backend="full_rebuild")

    assert published["verified"]["status"] == "failed"
    assert published["verified"]["acceptance_status"] == "failed"


@pytest.mark.parametrize("module", [
    operation_acceptance, transaction_receipt, transaction_orchestration,
    reel_execution_contract, edit_execution_adapters,
])
def test_the_acceptance_vocabulary_is_one(module):
    """The three outcomes are named in one place and nowhere else."""
    from library.tools import operation_acceptance as contracts

    assert contracts.CONTRACT_STATUSES == ("passed", "failed", "unmeasured")
    assert transaction_receipt.ACCEPTANCE_OUTCOMES == (
        "accepted", "failed", "unmeasured", "refused")


# ── the measurement, as a standing guard ────────────────────────────

def test_the_census_over_the_recorded_live_receipts_finds_no_disagreement():
    """The AXI-003 census, run over today's recorded live receipts.

    Before the lane, 9 of 11 recorded execution receipts had readers
    that disagreed about whether the operation was accepted.  The
    measurement is the guard: it is a number, and it is recomputable
    from the committed fixture.
    """
    import importlib.util  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    repo = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location(
        "axi003_census", repo / "scripts" / "axi003_receipt_census.py")
    census = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(census)

    fixture = json.loads(
        (repo / "tests" / "fixtures" / "axi003_live_receipts.json")
        .read_text(encoding="utf-8"))
    samples = [row for row in fixture["samples"]
               if census.is_edit_receipt(row["receipt"])]
    assert samples, "the recorded set carries no execution receipt"

    for row in samples:
        answers = {reader(dict(row["receipt"]))
                   for _, reader in census.ACCEPTANCE_READERS}
        assert len(answers) == 1, (
            f"the readers disagree about {row['source']}: {answers}")

    published = {shape for _, shape in census.publisher_shapes()}
    assert len(published) == 1, (
        f"the production paths publish {len(published)} receipt shapes")
