from __future__ import annotations

from pathlib import Path

import scripts.install_preservation as preservation
from scripts.install_preservation import compare, snapshot


def test_snapshot_records_relative_paths_without_reading_contents(tmp_path, monkeypatch):
    payload = tmp_path / "nested" / "file.txt"
    payload.parent.mkdir()
    payload.write_text("content", encoding="utf-8")

    monkeypatch.setattr(preservation, "_find", lambda root, owner_uid=None: ([str(payload)], []))
    assert snapshot(tmp_path) == {
        "files": {"nested/file.txt": -1}, "owned": [], "owner_uid": None,
        "skipped": [],
    }


def test_compare_limits_path_diff_to_vep_and_allows_named_live_state_changes():
    before = {"files": {
        "ren-resolved.sqlite3": -1,
    }, "owned": [], "owner_uid": None, "skipped": []}
    after = {"files": {
        "ordinary.txt": -1,
        "new-unrelated-home-file.txt": -1,
        ".local/share/vep/resource-scheduler.sqlite3-wal": -1,
        ".local/share/vep/logs/resolve.log": -1,
        ".local/share/vep/source_memory/digest/source.json": -1,
    }, "owned": [], "owner_uid": 700, "skipped": []}

    added, removed, failures, skipped = compare(before, after, renqualify_uid=700)

    assert len(added) == 3
    assert removed == [".local/share/vep/ren-resolved.sqlite3"]
    assert failures == []
    assert skipped == []


def test_compare_fails_for_unexpected_vep_paths_and_test_account_ownership():
    before = {"files": {"cache.txt": -1}, "owned": [], "owner_uid": None,
              "skipped": []}
    after = {"files": {
        ".local/share/vep/cache.txt": -1,
        ".local/share/vep/unexpected.txt": -1,
    }, "owned": ["Library/ren-owned.log"], "owner_uid": 700, "skipped": []}
    added, removed, failures, skipped = compare(before, after, renqualify_uid=700)

    assert added == [".local/share/vep/unexpected.txt"]
    assert removed == []
    assert failures == [".local/share/vep/unexpected.txt", "Library/ren-owned.log"]
    assert skipped == []


def test_snapshot_records_find_errors_as_skipped(tmp_path, monkeypatch):
    payload = tmp_path / "visible.txt"
    payload.write_text("ok", encoding="utf-8")
    monkeypatch.setattr(preservation, "_find", lambda root, owner_uid=None: (
        ([str(payload)], [f"{tmp_path}/.Trash: Operation not permitted"])))

    result = snapshot(tmp_path)

    assert result["files"] == {"visible.txt": -1}
    assert result["skipped"] == [".Trash"]


def test_snapshot_limits_path_capture_but_checks_owner_across_home(tmp_path, monkeypatch):
    vep = tmp_path / ".local" / "share" / "vep"
    vep.mkdir(parents=True)
    state_path = vep / "settings.json"
    other_path = tmp_path / "Library" / "renqualify-owned.txt"
    calls = []

    def fake_find(root, owner_uid=None):
        calls.append((root, owner_uid))
        return ([str(state_path)] if owner_uid is None else [str(other_path)], [])

    monkeypatch.setattr(preservation, "_find", fake_find)
    result = snapshot(tmp_path, owner_uid=700, path_prefix=vep)

    assert result == {
        "files": {".local/share/vep/settings.json": -1},
        "owned": ["Library/renqualify-owned.txt"],
        "owner_uid": 700, "skipped": [],
    }
    assert calls == [(vep, None), (tmp_path, 700)]
