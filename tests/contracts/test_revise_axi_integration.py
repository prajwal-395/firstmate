"""Contract tests for AXI completion as recorded by ``ren revise``."""


def _receipt(**overrides):
    receipt = {
        "status": "committed",
        "generation": 12,
        "lifecycle": {"stage": "COMMITTED"},
        "execution_contract": {
            "verification": {
                "status": "verified",
                "checks": [{"passed": True, "operation": "clip.set_property"}],
            },
        },
        "committed": {"status": "committed", "generation": 12},
        "applied": {"complete": True},
    }
    receipt.update(overrides)
    return receipt


def test_revise_accepts_only_common_receipt_with_verified_committed_generation():
    """Catches: a lifecycle label alone clearing the revision issue."""
    from library.tools.revise import _verified_completion

    assert _verified_completion(_receipt()) is True
    assert _verified_completion(_receipt(
        lifecycle={"stage": "VERIFIED"},
        committed={"status": "readback_persisted_unverified",
                   "generation": 12})) is False


def test_revise_keeps_partial_or_missing_operation_evidence_unresolved():
    """Catches: one successful operation in a batch reporting full success."""
    from library.tools.revise import _verified_completion

    partial = _receipt(
        status="partial_failure",
        lifecycle={"stage": "APPLIED"},
        execution_contract={"verification": {
            "status": "failed",
            "checks": [{"passed": True}, {"passed": False}],
        }},
        applied={"complete": False})
    assert _verified_completion(partial) is False
    assert _verified_completion(_receipt(
        execution_contract={"verification": {
            "status": "verified", "checks": []}})) is False


def test_revise_accepts_verified_noop_without_inventing_a_commit():
    """Catches: an already-satisfied edit being treated as a failed write."""
    from library.tools.revise import _verified_completion

    assert _verified_completion(_receipt(
        status="already_satisfied", generation=None,
        lifecycle={"stage": "VERIFIED"},
        execution_contract={"verification": {
            "status": "verified", "checks": [{"passed": True}]}})) is True


def test_partial_receipt_stays_open_and_resume_retranslates():
    """Catches: an unresolved partial edit being retried on a stale plan."""
    from library.tools import revise

    result = revise._result("/project", {
        "revise_id": "revise_partial",
        "status": revise.STATUS_INCOMPLETE,
        "execution_receipt": {"status": "partial_failure"},
    })
    assert result["resume"].endswith("--retranslate")
    assert "remains unresolved" in result["reply"]
