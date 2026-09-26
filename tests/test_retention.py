"""Lean retention deletes, so every test here is a file that must SURVIVE.

The captain's D6 (2026-09-23): once a reel is signed off, purge the
bloat - but never anything a live timeline references, never anything
an unsigned reel still needs, never the project's inputs or live
records. Each gate is proven in both directions on one shape: the file
that must go goes, and its twin that must stay stays.
"""
from __future__ import annotations

import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools import processes, reel_signoff, retention
from library.tools.timeline_transcript import transcript_path
from tests.test_build_sweep import (
    _asset_dir,
    _database,
    _entry,
    _ledger,
    _write,
)

SIGNED = "Reel 09 - your-website-is-only-20-percent"
UNSIGNED = "Reel 12 - ai-isnt-making-things-up"


@pytest.fixture(autouse=True)
def _lean(monkeypatch):
    monkeypatch.setenv(retention.SETTING, retention.LEAN)


def _project(tmp_path):
    root = str(tmp_path / "project")
    os.makedirs(root)
    return root


def _mov(root, name):
    return _write(os.path.join(_asset_dir(root), name + ".mov"))


def _planned(root, db):
    return {c.path for c in retention.plan_purge(root, [db]).candidates}


def test_a_file_a_live_timeline_places_is_never_removed(tmp_path):
    """No pipeline record names either file; only the timeline does. The
    unplaced one goes, the placed one stays - through plan AND apply."""
    root = _project(tmp_path)
    placed, loose = _mov(root, "sub_a_c_1-2_aaaaaaaa"), \
        _mov(root, "sub_a_c_1-2_bbbbbbbb")
    db = _database(str(tmp_path / "db" / "Project.db"), [placed], [SIGNED])

    plan = retention.plan_purge(root, [db])
    assert {c.path for c in plan.candidates} == {loose}
    manifest = retention.write_plan(plan)
    retention.apply_purge(manifest, [db], project_folder=root)

    assert os.path.isfile(placed)
    assert not os.path.exists(loose)


def test_an_unsigned_reels_renders_survive_its_timeline_being_gone(
        tmp_path):
    """Between builds a reel has no timeline, and the ledger stops pinning
    an entry whose timeline is gone. For a signed-off reel that release
    is the point; for an unsigned one it would delete what it needs."""
    root = _project(tmp_path)
    asset_dir = _asset_dir(root)
    signed = _mov(root, "sub_a_c_1-2_aaaaaaaa")
    unsigned = _mov(root, "sub_a_c_3-4_bbbbbbbb")
    _ledger(asset_dir, [_entry(asset_dir, "sub_a_c_1-2_aaaaaaaa", SIGNED),
                        _entry(asset_dir, "sub_a_c_3-4_bbbbbbbb", UNSIGNED)])
    reel_signoff.sign_off(root, SIGNED)
    db = _database(str(tmp_path / "db" / "Project.db"), [], ["Master"])

    planned = _planned(root, db)
    assert signed in planned
    assert unsigned not in planned


def test_apply_refuses_when_a_listed_path_became_referenced(tmp_path):
    """The plan is a claim about an earlier moment. A file a timeline
    placed after it was planned must refuse the whole apply."""
    root = _project(tmp_path)
    first, second = _mov(root, "sub_a_c_1-2_aaaaaaaa"), \
        _mov(root, "sub_a_c_3-4_bbbbbbbb")
    db_path = str(tmp_path / "db" / "Project.db")
    db = _database(db_path, [], [SIGNED])
    manifest = retention.write_plan(retention.plan_purge(root, [db]))

    os.unlink(db_path)
    _database(db_path, [second], [SIGNED])
    with pytest.raises(retention.PurgeRefused):
        retention.apply_purge(manifest, [db_path], project_folder=root)
    assert os.path.isfile(first) and os.path.isfile(second)


def test_no_timeline_database_refuses_rather_than_purging(tmp_path):
    """Without the timelines nothing can be shown unreferenced."""
    root = _project(tmp_path)
    _mov(root, "sub_a_c_1-2_aaaaaaaa")
    with pytest.raises(retention.PurgeRefused):
        retention.plan_purge(root, [])
    with pytest.raises(retention.PurgeRefused):
        retention.plan_purge(root, [str(tmp_path / "missing.db")])


def test_live_records_and_the_newest_journal_are_never_named(tmp_path):
    """Only a stamped journal older than its family's newest goes: the
    live proposal record shares the family's prefix and must survive."""
    root = _project(tmp_path)
    review = os.path.join(root, "pipeline_output", "review")
    live = _write(os.path.join(review, "reel_proposals_v2.json"))
    old = _write(os.path.join(review,
                              "reel_proposals_v2_20260920T143139Z.json"))
    new = _write(os.path.join(review,
                              "reel_proposals_v2_20260921T221430Z.json"))
    touchup = _write(os.path.join(
        review, "touchup_reel_09_x_20260920T143139Z.json"))
    db = _database(str(tmp_path / "db" / "Project.db"), [], [SIGNED])

    planned = _planned(root, db)
    assert old in planned
    assert not planned & {live, new, touchup}


def test_purge_keeps_and_names_the_declared_build_transcript(tmp_path):
    """The build reads this scratch file as a required input. Purge must
    exclude it, and refuse if a hand-edited manifest asks for it by name."""
    root = _project(tmp_path)
    inputs = processes.load_manifests(
        processes.load_dag(processes.REELS))["build_reels"]["interface"][
            "inputs"]
    transcript_decl = next(item for item in inputs
                           if item["name"] == "timeline_transcript")
    expected = transcript_path(root)
    assert transcript_decl["file_path"] == expected.relative_to(root).as_posix()
    expected.parent.mkdir(parents=True, exist_ok=True)
    expected.write_text('{"segments": []}', encoding="utf-8")
    loose = _write(os.path.join(root, "pipeline_output", "scratch",
                                "unneeded.tmp"))
    db = _database(str(tmp_path / "db" / "Project.db"), [], [SIGNED])

    plan = retention.plan_purge(root, [db])
    candidates = {candidate.path for candidate in plan.candidates}
    assert loose in candidates
    assert str(expected) not in candidates
    kept = next(item for item in plan.kept if item["path"] == str(expected))
    assert "build_reels.timeline_transcript" in kept["why"]

    manifest = retention.write_plan(plan)
    with open(manifest, "a", encoding="utf-8") as stream:
        stream.write(f"\n1\t{retention.SCRATCH}\t{expected}\n")
    with pytest.raises(retention.PurgeRefused,
                       match="build_reels.timeline_transcript"):
        retention.apply_purge(manifest, [db], project_folder=root)
    assert expected.is_file()
    assert os.path.isfile(loose)


def test_purge_protects_declared_build_input_in_quarantine(
        tmp_path, monkeypatch):
    """Protection follows the manifest's path, not a special scratch rule."""
    root = _project(tmp_path)
    dag = processes.load_dag(processes.REELS)
    manifests = processes.load_manifests(dag)
    input_decl = next(item for item in manifests["build_reels"]["interface"][
                      "inputs"] if item["name"] == "timeline_transcript")
    input_decl["file_path"] = (
        "pipeline_output/quarantine/declared-input/transcript.json")
    monkeypatch.setattr(processes, "load_manifests", lambda _dag: manifests)
    declared = os.path.join(root, input_decl["file_path"])
    _write(declared)
    db = _database(str(tmp_path / "db" / "Project.db"), [], [SIGNED])

    plan = retention.plan_purge(root, [db])

    assert declared not in {candidate.path for candidate in plan.candidates}
    kept = next(item for item in plan.kept if item["path"] == declared)
    assert "build_reels.timeline_transcript" in kept["why"]


def test_keep_plans_nothing_and_an_unknown_setting_refuses(
        tmp_path, monkeypatch):
    """`keep` is a user asking for every copy; a misspelt `keep` read as
    `lean` would delete them."""
    root = _project(tmp_path)
    _mov(root, "sub_a_c_1-2_aaaaaaaa")
    db = _database(str(tmp_path / "db" / "Project.db"), [], [SIGNED])
    monkeypatch.setenv(retention.SETTING, "keep")
    assert retention.plan_purge(root, [db]).candidates == []
    monkeypatch.setenv(retention.SETTING, "kepe")
    with pytest.raises(retention.RetentionSettingInvalid):
        retention.plan_purge(root, [db])
