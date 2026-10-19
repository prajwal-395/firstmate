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
