from __future__ import annotations

from library.tools import creative_revision


def test_prepared_revision_is_distinct_from_project_model_revision(tmp_path):
    row = creative_revision.create(
        tmp_path, author="editor", intent="Move title",
        operations=[{"op": "move", "target": "title-1"}],
        base_generation=4, project_model_revision=91,
        affected_entities=["title-1"], status="prepared")

    assert row["revision_id"] != "91"
    assert row["status"] == "prepared"
    assert row["project_model_revision"] == 91
    assert creative_revision.get(tmp_path, row["revision_id"]) == row


def test_revision_refs_merge_without_copying_snapshots(tmp_path):
    row = creative_revision.create(
        tmp_path, intent="Touch", refs={"undo_journal": {"entry_id": "j1"}})
    updated = creative_revision.create(
        tmp_path, revision_id=row["revision_id"],
        refs={"reel_version": {"final": "Reel 01", "version": 2}},
        status="verified")

    assert set(updated["refs"]) == {"undo_journal", "reel_version"}
    assert "before" not in updated and "after" not in updated
    assert updated["status"] == "verified"


def test_stale_revision_writer_cannot_replace_newer_facts(tmp_path):
    row = creative_revision.create(
        tmp_path, author="editor", intent="Move title", status="recorded")
    updated = creative_revision.create(
        tmp_path, revision_id=row["revision_id"],
        expected_version=row["version"],
        refs={"timeline_shadow": {"generation": 9}})
    assert creative_revision.create(
        tmp_path, revision_id=row["revision_id"],
        expected_version=row["version"],
        refs={"timeline_shadow": {"generation": 9}}) == updated

    import pytest
    with pytest.raises(creative_revision.StaleRevision):
        creative_revision.create(
            tmp_path, revision_id=row["revision_id"],
            expected_version=row["version"],
            refs={"timeline_shadow": {"generation": 4}})
    assert creative_revision.get(
        tmp_path, row["revision_id"])["refs"]["timeline_shadow"][
            "generation"] == 9
    assert updated["version"] > row["version"]


def test_approved_revision_rejection_requires_idempotent_revocation_event(tmp_path):
    row = creative_revision.create(
        tmp_path, intent="Accept cut", status="approved",
        approval={"review_id": "review-1"})
    import pytest
    with pytest.raises(creative_revision.RevisionConflict):
        creative_revision.create(
            tmp_path, revision_id=row["revision_id"], status="rejected")

    decision = {"event_id": "revoke-1", "reason": "captain changed decision"}
    revoked = creative_revision.revoke(
        tmp_path, row["revision_id"], expected_version=row["version"],
        decision=decision)
    assert revoked["status"] == "rejected"
    assert creative_revision.revoke(
        tmp_path, row["revision_id"], expected_version=row["version"],
        decision=decision) == revoked


def test_reconstructed_legacy_ids_do_not_depend_on_project_path(tmp_path):
    moved = tmp_path / "moved" / "episode-copy"
    moved.mkdir(parents=True)
    source = tmp_path / "original" / "episode"
    source.mkdir(parents=True)
    declaration = "name: Podcast Episode 7\nslug: episode-7\ncreated: 2026-10-19\n"
    (source / "project.yaml").write_text(declaration, encoding="utf-8")
    (moved / "project.yaml").write_text(declaration, encoding="utf-8")
    identity = creative_revision._stable_project_identity(source)
    moved_identity = creative_revision._stable_project_identity(moved)
    assert identity == moved_identity
    first = creative_revision._legacy_revision_id(
        identity, "reel_version", "Reel 01:3")
    moved_copy = creative_revision._legacy_revision_id(
        moved_identity, "reel_version", "Reel 01:3")
    assert first == moved_copy
