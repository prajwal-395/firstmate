"""Ren's orphans move to trash; footage and placed media never do.

The collection has two rules - never-referenced and never-footage -
and these tests prove each refuses on its own: a placed clip is
excluded even when generated, an unplaced file is excluded even when
orphaned when it lives outside the project, and a plan carrying
either refuses as a whole. The trash half moves files and writes the
manifest that maps them back; nothing here unlinks. Each fails on
main, where `asset_gc` does not exist.
"""

import json
from pathlib import Path

import pytest

from library.tools import asset_gc
from library.tools.orphan_removal import Removal
from library.tools.resolve_organization import Artefact


def _clip(item_id, name, path, placed=()):
    return Artefact(item_id=item_id, name=name, kind="clip",
                    file_path=path, placed_by=tuple(placed),
                    folder_path=("Master",))


def test_unplaced_generated_clip_is_collected(tmp_path):
    orphan = tmp_path / "still_ab12.png"
    orphan.write_bytes(b"pixels")
    plan = asset_gc.plan_gc(
        [_clip("1", "still_ab12.png", str(orphan))],
        str(tmp_path), set(), {str(orphan): 6})
    assert [r.file_path for r in plan] == [str(orphan)]
    assert plan[0].file_state == "delete"


def test_placed_clip_is_never_collected(tmp_path):
    live = tmp_path / "still_live.png"
    live.write_bytes(b"pixels")
    plan = asset_gc.plan_gc(
        [_clip("1", "still_live.png", str(live), placed=("Reel 07",))],
        str(tmp_path), set(), {str(live): 6})
    assert plan == []


def test_unplaced_footage_is_never_collected(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    footage = tmp_path / "footage" / "interview.mov"
    footage.parent.mkdir()
    footage.write_bytes(b"footage")
    plan = asset_gc.plan_gc(
        [_clip("1", "interview.mov", str(footage))],
        str(project), set(), {str(footage): 7})
    assert plan == []


def test_database_placed_path_is_keep_shared_not_delete(tmp_path):
    shared = tmp_path / "shared.mov"
    shared.write_bytes(b"shared")
    plan = asset_gc.plan_gc(
        [_clip("1", "shared.mov", str(shared))],
        str(tmp_path), {str(shared)}, {str(shared): 6})
    assert [r.file_state for r in plan] == ["keep_shared"]
    assert asset_gc.files_to_trash(plan) == []


def test_gate_refuses_a_referenced_plan(tmp_path):
    live = str(tmp_path / "still_live.png")
    plan = [Removal(item_id="1", name="still_live.png", file_path=live,
                    folder_path=("Master",), file_state="delete")]
    with pytest.raises(asset_gc.GcRefused):
        asset_gc.assert_gc_safe(plan, {live}, str(tmp_path))


def test_gate_refuses_a_footage_plan(tmp_path):
    outside = str(tmp_path / "footage" / "interview.mov")
    plan = [Removal(item_id="1", name="interview.mov", file_path=outside,
                    folder_path=("Master",), file_state="delete")]
    with pytest.raises(asset_gc.GcRefused):
        asset_gc.assert_gc_safe(plan, set(), str(tmp_path / "project"))


def test_trash_moves_files_and_writes_the_manifest(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    first = project / "replaced_a.mov"
    second = project / "replaced_b.mov"
    first.write_bytes(b"a" * 100)
    second.write_bytes(b"b" * 200)
    trash = asset_gc.trash_dir_for(str(project), "2026-10-07T00")
    record = asset_gc.move_to_trash([str(first), str(second)], set(),
                                    trash)
    assert not first.exists() and not second.exists()
    assert sorted(record["moved"]) == [str(first), str(second)]
    assert record["bytes_trashed"] == 300
    manifest = json.loads(
        Path(trash, asset_gc.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert {entry["original"] for entry in manifest["entries"]} == {
        str(first), str(second)}
    assert all(entry["trashed"] for entry in manifest["entries"])


def test_trash_refuses_a_file_a_timeline_plays(tmp_path):
    live = tmp_path / "still_live.png"
    live.write_bytes(b"pixels")
    gone = tmp_path / "orphan.png"
    gone.write_bytes(b"orphan")
    trash = asset_gc.trash_dir_for(str(tmp_path), "2026-10-07T01")
    with pytest.raises(Exception):
        asset_gc.move_to_trash([str(gone), str(live)], {str(live)},
                               trash)


def test_missing_files_are_recorded_not_created(tmp_path):
    trash = asset_gc.trash_dir_for(str(tmp_path), "2026-10-07T02")
    record = asset_gc.move_to_trash([str(tmp_path / "gone.mov")], set(),
                                    trash)
    assert record["moved"] == []
    assert record["already_gone"] == [str(tmp_path / "gone.mov")]


def test_dry_run_report_names_sizes_and_trash(tmp_path):
    orphan = str(tmp_path / "replaced.mov")
    plan = [Removal(item_id="1", name="replaced.mov", file_path=orphan,
                    folder_path=("Master",), file_state="delete",
                    size_bytes=6 * 1024 * 1024)]
    counts = {"items": 1, "delete_files": 1, "keep_shared": 0,
              "already_gone": 0, "bytes_freed": 6 * 1024 * 1024,
              "bytes_kept_shared": 0}
    report = asset_gc.render_gc_report(plan, counts, "/proj/trash/x")
    assert orphan in report and "/proj/trash/x" in report
    assert "--apply" in report


def test_the_gc_verb_is_registered_in_both_front_doors():
    from pathlib import Path

    import ren.commands as ren_commands

    by_name = {verb.name: verb for verb in ren_commands.VERBS}
    assert by_name["gc"].subcommand == "asset-gc"
    native = (Path(__file__).resolve().parents[3] / "native" / "ren-cli"
              / "src" / "commands.rs").read_text(encoding="utf-8")
    assert 'name: "gc"' in native
