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
