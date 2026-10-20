"""Promotion history must point back to the durable promotion transaction."""

from library.tools import reel_build


def test_promotion_history_links_its_version_to_promotion_transaction(
        monkeypatch, tmp_path):
    from library.tools import reel_component_manifest, reel_edit_history
    from library.tools.versions import reel_versions

    final = "Reel 07"
    transaction_id = "promotion-transaction-07"
    recorded = []
    monkeypatch.setattr(
        "library.tools.plan_provenance.provenance_with_registry",
        lambda _review_dir: {"built_with": {}})
    monkeypatch.setattr("library.tools.reel_proposal.proposal_path",
                        lambda _project_folder: tmp_path / "missing-proposal.json")
    monkeypatch.setattr(reel_versions, "versions_of", lambda *_args: [])
    monkeypatch.setattr(reel_versions, "take_pending_redo", lambda *_args: None)
    monkeypatch.setattr(reel_versions, "take_pending_rollback",
                        lambda *_args: None)
    monkeypatch.setattr(reel_component_manifest, "build_manifest",
                        lambda *_args: None)

    def record(_project_folder, _final, **kwargs):
        assert kwargs["promotion_transaction_id"] == transaction_id
        return {"version": 3, "rows_digest": "rows-3"}

    def history(_review_dir, _final, **kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(reel_versions, "record", record)
    monkeypatch.setattr(reel_edit_history, "try_record_ren_act", history)

    reel_build._record_reel_versions(
        str(tmp_path), {final: []}, transaction_id=transaction_id)

    assert recorded[0]["refs"]["version"] == 3
    assert recorded[0]["refs"]["rows_digest_after"] == "rows-3"
    assert recorded[0]["refs"]["promotion_transaction_id"] == transaction_id
