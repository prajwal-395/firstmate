"""The CLI and in-process AXI SDK share one dispatcher and typed receipts."""

from library.tools import resolve_axi
from library.tools.resolve_axi_core import (
    AxiOperation,
    AxiTransaction,
    execute,
    render_json,
)
from library.tools.resolve_axi_sdk import call


def test_sdk_and_core_use_same_dispatch_and_version_output():
    direct = execute(AxiOperation("--version"))
    sdk = call("--version")
    assert direct == sdk
    assert direct.ok
    assert direct.stdout == "0.7.0\n"
    assert render_json(direct) == (
        '{"ok":true,"exit":0,"stdout":"0.7.0\\n","stderr":""}'
    )


def test_typed_transaction_keeps_one_editpatch_receipt_shape():
    transaction = AxiTransaction(
        id="stable-key",
        project="Ren Qualification",
        timeline="Main",
        base_generation=7,
        capability="reel.touchup",
        affected_spans=((100, 120),),
        conflict_domains=("picture_transform",),
        operations=(
            {
                "op": "clip.set_property",
                "unique_id": "item-1",
                "key": "Pan",
                "value": 24.0,
            },
            {
                "op": "clip.set_property",
                "unique_id": "item-1",
                "key": "Tilt",
                "value": 18.0,
            },
        ),
    )
    patch = transaction.patch()
    assert patch["id"] == "stable-key"
    assert patch["affected_spans"] == [[100, 120]]
    assert len(patch["operations"]) == 2


def test_transform_apply_refuses_unverified_compound_write(capsys):
    args = resolve_axi.build_parser().parse_args([
        "edit", "transform", "--item", "clip-1", "--move-x", "40",
        "--apply",
    ])
    assert resolve_axi.cmd_edit_transform(args) == 1
    assert "requires --verify" in capsys.readouterr().out


def test_transaction_submit_returns_one_broker_receipt(monkeypatch):
    from library.tools.resolve_axi_core import submit_transaction
    from library.tools.resolved import client

    seen = []
    monkeypatch.setattr(client, "ensure", lambda: None)

    def submit(kind, params, owner=""):
        seen.append((kind, params, owner))
        return {"id": "job-1"}

    monkeypatch.setattr(client, "submit", submit)
    monkeypatch.setattr(client, "result", lambda job_id, wait: {
        "result": {"status": "committed", "patch_id": "stable-key"}})
    receipt = submit_transaction(AxiTransaction(
        id="stable-key", project="Ren Qualification", timeline="Main",
        base_generation=7, capability="reel.touchup",
        affected_spans=((100, 120),), conflict_domains=("picture_transform",),
        operations=({"op": "clip.set_property", "unique_id": "item-1",
                    "key": "Pan", "value": 24.0},),
    ))
    assert receipt == {"status": "committed", "patch_id": "stable-key"}
    assert seen[0][0] == "timeline.apply_patch"
    assert seen[0][1]["patch"]["id"] == "stable-key"


def test_transaction_supports_async_submit_and_retrieve(monkeypatch):
    from library.tools.resolve_axi_core import (
        retrieve_transaction,
        submit_transaction_async,
    )
    from library.tools.resolved import client

    transaction = AxiTransaction(
        id="stable-key", project="Ren Qualification", timeline="Main",
        base_generation=7, capability="reel.touchup",
        affected_spans=((100, 120),), conflict_domains=("picture_transform",),
        operations=({"op": "clip.set_property", "unique_id": "item-1",
                    "key": "Pan", "value": 24.0},),
    )
    monkeypatch.setattr(client, "ensure", lambda: None)
    monkeypatch.setattr(client, "submit", lambda *args, **kwargs: {
        "id": "job-1", "status": "queued"})
    monkeypatch.setattr(client, "result", lambda job_id, wait: {
        "id": job_id, "status": "committed",
        "result": {"status": "committed", "patch_id": "stable-key"}})

    job = submit_transaction_async(transaction)
    assert job == {"id": "job-1", "status": "queued"}
    assert retrieve_transaction("job-1", wait=3) == {
        "status": "committed", "patch_id": "stable-key"}


def test_transaction_plan_validates_recorded_base_without_resolve(monkeypatch):
    from types import SimpleNamespace

    from library.tools import edit_patch, timeline_shadow
    from library.tools.resolve_axi_core import plan_transaction

    snapshot = {"clips": []}
    generation = SimpleNamespace(generation=7, timeline_name="Main")

    class Store:
        def current_snapshots(self, project):
            assert project == "Ren Qualification"
            return [(generation, snapshot)]

    monkeypatch.setattr(timeline_shadow, "ShadowStore", Store)
    monkeypatch.setattr(edit_patch, "validate", lambda patch, base: None)
    monkeypatch.setattr(edit_patch, "_judge", lambda conditions, snap, base: [])
    plan = plan_transaction(AxiTransaction(
        id="stable-key", project="Ren Qualification", timeline="Main",
        base_generation=7, capability="reel.touchup",
        affected_spans=((100, 120),), conflict_domains=("picture_transform",),
        operations=({"op": "clip.set_property", "unique_id": "item-1",
                    "key": "Pan", "value": 24.0},),
    ))
    assert plan["status"] == "planned"
    assert plan["backend"] == "edit_patch"
    assert plan["authorization_required"] is True
    assert plan["resolve_contacted"] is False
    assert plan["mutated"] is False


def test_semantic_submission_requires_authorization_before_preparation():
    import pytest

    from library.tools.resolve_axi_core import submit_edit_request_async

    with pytest.raises(PermissionError, match="explicit authorization"):
        submit_edit_request_async({}, authorized=False)


def test_registered_execution_uses_canonical_verified_receipt():
    from types import SimpleNamespace

    from library.tools.resolve_axi_core import _registered_execution_receipt

    operation = {"op": "clip.trim", "unique_id": "clip-1",
                 "in_frame": 12, "out_frame": 20}
    adapter_receipt = {
        "status": "committed", "backend": "resolve_axi",
        "readback": {"record_range": [12, 20]},
        "execution_contract": {
            "verification": {
                "status": "verified",
                "checks": ({"operation": "clip.trim", "passed": True},),
                "evidence": {"readback": {"record_range": [12, 20]}},
            },
            "recovery": {"status": "recorded", "action": "restore"},
        },
    }
    receipt = _registered_execution_receipt(
        backend="incremental", plan={"routing": {"backend": "incremental"}},
        request=SimpleNamespace(
            id="request-1", idempotency_key="request-1",
            project="Project", timeline="Timeline"),
        operations=[operation], receipts=[adapter_receipt],
        base_generation=4, committed_generation=5,
    )

    assert receipt["status"] == "committed"
    assert receipt["request_id"] == "request-1"
    assert receipt["transaction_id"] == "request-1"
    assert receipt["backend"] == "incremental"
    assert receipt["generation"] == 5
    assert receipt["affected_items"] == ["clip-1"]
    assert receipt["expected_changes"] == [operation]
    assert receipt["actual_changes"] == [adapter_receipt]
    assert receipt["lifecycle"]["stage"] == "COMMITTED"
    assert receipt["execution_contract"]["verification"]["status"] == "verified"
    assert receipt["execution_contract"]["verification"]["layers"] == {
        "stored_state": {"status": "verified", "checks": 1},
        "rendered_pixels": {"status": "not_measured", "evidence": {}},
        "rendered_audio": {"status": "not_measured", "evidence": {}},
    }
    assert receipt["promotion"]["status"] == "awaiting_promotion"
    assert receipt["delivered"]["status"] == "not_requested"
    assert receipt["recovery"]["status"] == "unavailable"


def test_registered_execution_claims_recovery_only_with_actionable_reference():
    from types import SimpleNamespace

    from library.tools.resolve_axi_core import _registered_execution_receipt

    operation = {"op": "clip.trim", "unique_id": "clip-1",
                 "in_frame": 12, "out_frame": 20}
    reference = {"kind": "undo_journal_entry",
                 "project_folder": "/project", "entry_id": "entry-1"}
    adapter_receipt = {
        "status": "committed", "backend": "resolve_axi",
        "execution_contract": {
            "verification": {
                "status": "verified",
                "checks": ({"operation": "clip.trim", "passed": True},),
                "evidence": {"readback": {"record_range": [12, 20]}},
            },
            "recovery": {"status": "available", "reference": reference},
        },
    }

    receipt = _registered_execution_receipt(
        backend="resolve_axi", plan={},
        request=SimpleNamespace(
            id="request-recoverable", idempotency_key="request-recoverable",
            project="Project", timeline="Timeline"),
        operations=[operation], receipts=[adapter_receipt],
        base_generation=4, committed_generation=5,
    )

    assert receipt["recovery"]["status"] == "available"
    assert receipt["recovery"]["operations"] == [
        adapter_receipt["execution_contract"]["recovery"]]


def test_registered_execution_missing_operation_evidence_is_not_verified():
    from types import SimpleNamespace

    from library.tools.resolve_axi_core import _registered_execution_receipt

    receipt = _registered_execution_receipt(
        backend="incremental", plan={},
        request=SimpleNamespace(
            id="request-2", idempotency_key="request-2",
            project="Project", timeline="Timeline"),
        operations=[{"op": "clip.trim"}],
        receipts=[{"status": "committed", "execution_contract": {
            "verification": {"status": "verified", "checks": [],
                              "evidence": {}}}}],
        base_generation=4, committed_generation=5,
    )

    assert receipt["status"] == "verification_failed"
    assert receipt["lifecycle"]["stage"] == "APPLIED"
    assert receipt["execution_contract"]["verification"]["status"] == "failed"
    assert receipt["execution_contract"]["verification"]["layers"][
        "rendered_pixels"]["status"] == "not_measured"
    assert receipt["committed"]["status"] == "readback_persisted_unverified"


def test_sdk_semantic_submit_uses_shared_request_pipeline(monkeypatch):
    from library.tools import resolve_axi_sdk

    seen = []

    def submit(request, *, authorized=False, owner="resolve-axi"):
        seen.append((request, authorized, owner))
        return {"id": "semantic-job", "status": "queued"}

    monkeypatch.setattr(resolve_axi_sdk, "submit_edit_request_async", submit)
    result = resolve_axi_sdk.submit_edit(
        {"id": "req-1"}, authorized=True, owner="test-owner")
    assert result == {"id": "semantic-job", "status": "queued"}
    assert seen == [({"id": "req-1"}, True, "test-owner")]


def test_registered_batch_compensates_successes_in_reverse_order(monkeypatch):
    from library.tools import undo_journal
    from library.tools.resolve_axi_core import _compensate_registered_operations

    calls = []

    def undo(project_folder, *, final, act):
        calls.append((project_folder, final, act))
        return [{"verified": True, "act": act}]

    monkeypatch.setattr(undo_journal, "undo", undo)
    receipts = [{
        "operation_index": index,
        "receipt": {"execution_contract": {"recovery": {
            "reference": {"kind": "undo_journal_entry",
                          "project_folder": "/project",
                          "entry_id": f"entry-{index}"}}}},
    } for index in range(2)]

    recovery = _compensate_registered_operations(
        receipts, timeline="Reel 01")

    assert calls == [
        ("/project", "Reel 01", "entry-1"),
        ("/project", "Reel 01", "entry-0"),
    ]
    assert recovery["status"] == "restored_original_verified"
    assert [action["operation_index"] for action in recovery["actions"]] == [1, 0]


def test_caption_style_batch_restores_rendered_caption_generation_on_failure(
        monkeypatch):
    """Defect: restyle_captions could never recover a completed card after
    a later card failed; each prior render must swap back its prior pixels."""
    from types import SimpleNamespace

    from library.tools import operations
    from library.tools.resolve_axi_core import _compensate_registered_operations

    calls = []

    def rerender(project_folder, pairs, swap):
        calls.append((project_folder, pairs, swap))
        pair = pairs[0]
        return {"ok": True, "map": {pair["old_mov"]:
                                      "/project/old-caption.mov"},
                "swapped": [pair["old_mov"]]}

    monkeypatch.setattr(operations, "get", lambda _name: SimpleNamespace(
        run=rerender))
    receipts = [{
        "operation_index": 0,
        "receipt": {"execution_contract": {"recovery": {
            "reference": {
                "kind": "caption_swap_back",
                "project_folder": "/project",
                "previous_file": "/project/old-caption.mov",
                "current_file": "/project/new-caption.mov",
                "previous_props": {"text": "Keep these words"},
            }}}},
    }]

    recovery = _compensate_registered_operations(
        receipts, timeline="Reel 01")

    assert calls == [(
        "/project", [{"old_mov": "/project/new-caption.mov",
                       "timeline_label": "Reel 01",
                       "props": {"text": "Keep these words"}}], True)]
    assert recovery["status"] == "restored_original_verified"
    assert recovery["actions"][0]["kind"] == "caption_swap_back"


def test_restyle_captions_preflights_every_rendered_caption_before_mutation():
    """Defect: restyle_captions refused a batch by design instead of
    validating and executing each Ren-rendered caption with one style."""
    import pytest

    from library.tools.resolve_axi_core import _validate_caption_style_batch

    operations = [
        {"op": "caption.style", "unique_id": "cap-1", "style": "minimal"},
        {"op": "caption.style", "unique_id": "cap-2", "style": "minimal"},
    ]
    snapshot = {"tracks": [{"name": "Subtitles", "clips": [
        {"unique_id": "cap-1", "source_file": "/rendered/cap-1.mov"},
        {"unique_id": "cap-2", "source_file": "/rendered/cap-2.mov"},
    ]}]}

    assert _validate_caption_style_batch(operations, snapshot) is None
    with pytest.raises(ValueError, match="one supplied style"):
        _validate_caption_style_batch(
            [operations[0], {**operations[1], "style": "bold"}], snapshot)


def test_registered_batch_keeps_recovery_required_for_unknown_failed_write(
        monkeypatch):
    from library.tools import undo_journal
    from library.tools.resolve_axi_core import _compensate_registered_operations

    monkeypatch.setattr(undo_journal, "undo", lambda *_args, **_kwargs: [
        {"verified": True}])
    receipts = [{
        "operation_index": 0,
        "receipt": {"execution_contract": {"recovery": {
            "reference": {"kind": "undo_journal_entry",
                          "project_folder": "/project",
                          "entry_id": "entry-0"}}}},
    }]

    recovery = _compensate_registered_operations(
        receipts, timeline="Reel 01",
        failure_evidence={"mutation_state": "unknown", "operation_index": 1})

    assert recovery["status"] == "recovery_required"
    assert recovery["actions"][0]["verified"] is True
    assert recovery["remaining_operations"] == [1]


def test_touchup_move_resize_batch_requires_one_stable_overlay_identity():
    """Defect: move+resize had no declared, recoverable compound path."""
    from library.tools.resolve_axi_core import _registered_batch_supported

    operations = [
        {"op": "overlay.position", "unique_id": "overlay-1",
         "dx": 10, "dy": 4},
        {"op": "overlay.resize", "unique_id": "overlay-1",
         "width": 640, "height": 360},
    ]

    assert _registered_batch_supported("reel_touchup", operations)
    assert not _registered_batch_supported(
        "reel_touchup", [operations[0],
                         {**operations[1], "unique_id": "overlay-2"}])
    assert not _registered_batch_supported("resolve_axi", operations)


def test_registered_compensation_refuses_unverified_undo_receipt(monkeypatch):
    """Defect: an undo receipt without a positive verdict looked recovered."""
    from library.tools import undo_journal
    from library.tools.resolve_axi_core import _compensate_registered_operations

    monkeypatch.setattr(undo_journal, "undo", lambda *_args, **_kwargs: [
        {"entry_id": "entry-0"}])
    receipts = [{
        "operation_index": 0,
        "receipt": {"execution_contract": {"recovery": {
            "reference": {"kind": "undo_journal_entry",
                          "project_folder": "/project",
                          "entry_id": "entry-0"}}}},
    }]

    recovery = _compensate_registered_operations(
        receipts, timeline="Reel 01")

    assert recovery["status"] == "recovery_required"
    assert recovery["actions"] == []
    assert recovery["remaining_operations"] == [0]


def test_compatible_delete_batch_uses_one_editpatch_commit(monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace

    from library.tools import (
        edit_patch,
        project_registry,
        resolve_axi_core,
        timeline_addressing,
    )
    from library.tools import resolve_axi_core_commands as commands

    project = object()
    timeline = SimpleNamespace(GetName=lambda: "Reel 01")
    request = SimpleNamespace(
        id="request-1", idempotency_key="stable-batch-key",
        project="Project", timeline="Reel 01")
    compiled = SimpleNamespace(
        operations=({"op": "clip.delete", "unique_id": "clip-1"},
                    {"op": "clip.delete", "unique_id": "clip-2"}),
        conflict_domains=("timeline_structure",))
    generation = SimpleNamespace(generation=12)
    snapshot = {"tracks": [{"type": "video", "index": 1, "clips": [
        {"unique_id": "clip-1", "track_type": "video",
         "source_file": "/media/one.mov", "source_in_frame": 1,
         "source_out_frame": 20, "record_in": 100, "record_out": 119},
        {"unique_id": "clip-2", "track_type": "video",
         "source_file": "/media/two.mov", "source_in_frame": 2,
         "source_out_frame": 21, "record_in": 200, "record_out": 219},
    ]}]}
    calls = []

    monkeypatch.setattr(commands, "_connect", lambda: "resolve")
    monkeypatch.setattr(commands, "_project", lambda *_args: project)
    monkeypatch.setattr(commands, "_target_timeline", lambda *_args: (
        timeline, True, ""))
    monkeypatch.setattr(commands, "_assert_cursor_on", lambda *_args: None)
    monkeypatch.setattr(
        commands, "_item_span",
        lambda item: next({"record_in": clip["record_in"],
                           "record_out": clip["record_out"]}
                          for clip in snapshot["tracks"][0]["clips"]
                          if clip["unique_id"] == item.GetUniqueId()))
    monkeypatch.setattr(
        timeline_addressing, "resolve_item",
        lambda _timeline, selector: SimpleNamespace(item=SimpleNamespace(
            GetUniqueId=lambda: selector["resolve_item_id"])))
    monkeypatch.setattr(timeline_addressing, "verify_item", lambda *_args: None)
    monkeypatch.setattr(project_registry, "resolve_project_path", lambda _ref: (
        Path("/tmp/project/project.yaml")))
    monkeypatch.setattr(resolve_axi_core, "_patch_capability", lambda _compiled: (
        "reel.touchup"))
    monkeypatch.setattr(edit_patch, "apply_live_patch", lambda **kwargs: (
        calls.append(kwargs) or {"status": "committed", "operations": [
            {"index": 0, "verified": True},
            {"index": 1, "verified": True},
        ]}))

    receipt = resolve_axi_core._execute_registered_delete_batch(
        request=request, compiled=compiled, generation=generation,
        snapshot=snapshot,
        decision={"backend": "resolve_axi"}, owner="test")

    assert len(calls) == 1
    assert calls[0]["operations"] == compiled.operations
    assert calls[0]["idempotency_key"] == "stable-batch-key"
    assert receipt["atomicity"] == "single_edit_patch_commit"
    assert receipt["atomic"] is True
    assert receipt["operations"][0]["target_identity"]["source_file"] == (
        "/media/one.mov")
