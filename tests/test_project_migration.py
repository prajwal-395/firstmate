"""Organising a project folder never deletes and never touches inputs.

Project 001 - the captain's real work, and the only project that exists -
had nine hand-made `pipeline_data.json` backups, six one-off Python
scripts, two unattributed .mov files totalling 1.14 GB, a mock SFX
directory, an ad-hoc `state/` directory and a 517 MB `_archive_...`
directory, all at its top level.

These tests hold the three rules that make organising it safe: nothing is
deleted, input directories are not modified, and every action is recorded
in a manifest that can be read back to undo it.
"""

import json

import pytest

from library.tools.project_layout import Area, ProjectLayout
from library.tools.project_migration import (
    organize_project,
    plan_organization,
    render_manifest_markdown,
    revert_from_manifest,
)


@pytest.fixture
def messy(tmp_path):
    """A folder shaped like project 001 before it was organised."""
    (tmp_path / "project.yaml").write_text("slug: test\n", encoding="utf-8")
    (tmp_path / "pipeline_data.json").write_text('{"a": 1}', encoding="utf-8")
    (tmp_path / "pipeline_run.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".DS_Store").write_bytes(b"\x00" * 16)

    for name in ("pipeline_data.json.bak", "pipeline_data.json.bak2",
                 "pipeline_data.json.bak_phase1_landscape"):
        (tmp_path / name).write_text('{"old": true}', encoding="utf-8")

    for name in ("parse_broll.py", "generate_response.py"):
        (tmp_path / name).write_text("# one-off\n", encoding="utf-8")

    (tmp_path / "001.mov").write_bytes(b"\x00" * 4096)
    (tmp_path / "fully loaded demo v0.mov").write_bytes(b"\x00" * 2048)
    (tmp_path / "001.PNG").write_bytes(b"\x89PNG" + b"\x00" * 128)
    (tmp_path / "e2e-v4.status").write_text("E2E Run Passed", encoding="utf-8")

    (tmp_path / "mock_sfx" / "profiles").mkdir(parents=True)
    (tmp_path / "mock_sfx" / "profiles" / "a.json").write_text("{}", encoding="utf-8")
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "live-test.status").write_text("ok", encoding="utf-8")
    arch = tmp_path / "_archive_2026-08-17_pre_rerun"
    (arch / "pipeline_output").mkdir(parents=True)
    (arch / "pipeline_data.json").write_text("{}", encoding="utf-8")

    raw = tmp_path / "raw"
    (raw / "analysis").mkdir(parents=True)
    (raw / "IMG_1806.MOV").write_bytes(b"\x00" * 1024)
    (raw / "analysis" / "clip_profile_IMG_1806_v3.json").write_text(
        '{"v": 3}', encoding="utf-8")
    music = tmp_path / "music"
    music.mkdir()
    (music / "track.wav").write_bytes(b"\x00" * 512)

    (tmp_path / "exports").mkdir()
    (tmp_path / "exports" / "Pipeline_Edit.mp4").write_bytes(b"\x00" * 256)
    return tmp_path


def _by_name(manifest, name):
    return [a for a in manifest["actions"] if a["src"].endswith(name)]


# ── Nothing is deleted ──────────────────────────────────────────────

def _every_file(root):
    """(name, size) for every file in the tree - the accounting unit."""
    from collections import Counter
    return Counter(
        (p.name, p.stat().st_size) for p in root.rglob("*") if p.is_file())


def test_every_file_that_was_there_is_still_there_afterwards(messy):
    before = _every_file(messy)
    m = organize_project(messy, apply=True)
    after = _every_file(messy)
    lost = before - after
    assert not lost, f"the migration lost {list(lost)}"
    # It only ever grows, and only by what it wrote: the copies out of
    # raw/, plus README-LAYOUT.md, the bucket READMEs and the manifest.
    assert m["totals"]["after_bytes"] >= m["totals"]["before_bytes"]


def test_no_action_is_a_delete(messy):
    m = organize_project(messy, apply=True)
    assert {a["action"] for a in m["actions"]} <= {"move", "copy"}


def test_every_moved_file_exists_at_its_destination(messy):
    from pathlib import Path
    m = organize_project(messy, apply=True)
    for a in m["actions"]:
        assert Path(a["dest"]).exists(), f"{a['dest']} is missing"


def test_a_name_collision_never_overwrites(messy):
    layout = ProjectLayout(messy)
    dest = layout.write_path(Area.UNSORTED, "media", "001.mov")
    dest.write_bytes(b"already here")
    organize_project(messy, apply=True)
    assert dest.read_bytes() == b"already here"
    assert (dest.parent / "001__2.mov").exists()


# ── Input directories are not modified ──────────────────────────────

def test_raw_is_left_exactly_as_it_was_found(messy):
    before = sorted(p.name for p in (messy / "raw").rglob("*"))
    organize_project(messy, apply=True)
    assert sorted(p.name for p in (messy / "raw").rglob("*")) == before


def test_music_is_left_exactly_as_it_was_found(messy):
    before = sorted(p.name for p in (messy / "music").rglob("*"))
    organize_project(messy, apply=True)
    assert sorted(p.name for p in (messy / "music").rglob("*")) == before


def test_a_vision_cache_inside_raw_is_copied_out_not_moved(messy):
    m = organize_project(messy, apply=True)
    acts = _by_name(m, "clip_profile_IMG_1806_v3.json")
    assert len(acts) == 1 and acts[0]["action"] == "copy"
    assert (messy / "raw" / "analysis"
            / "clip_profile_IMG_1806_v3.json").exists()
    assert (ProjectLayout(messy).read_path(
        Area.VISION_ANALYSIS, "clip_profile_IMG_1806_v3.json")).exists()


def test_no_destination_is_inside_an_input_directory(messy):
    layout = ProjectLayout(messy)
    for a in organize_project(messy, apply=True)["actions"]:
        layout.assert_writable(a["dest"])


# ── Where things go ─────────────────────────────────────────────────

def test_hand_made_backups_go_to_the_legacy_store_by_name(messy):
    organize_project(messy, apply=True)
    legacy = ProjectLayout(messy).legacy_backup_dir()
    assert sorted(p.name for p in legacy.iterdir()) == [
        "pipeline_data.json.bak",
        "pipeline_data.json.bak2",
        "pipeline_data.json.bak_phase1_landscape",
    ]
    assert not list(messy.glob("*.bak*")), "none left at the root"


def test_the_live_state_file_is_not_mistaken_for_a_backup(messy):
    organize_project(messy, apply=True)
    assert (messy / "pipeline_data.json").exists()
    assert json.loads(
        (messy / "pipeline_data.json").read_text(encoding="utf-8")) == {"a": 1}


def test_unattributed_media_is_admitted_rather_than_guessed_at(messy):
    m = organize_project(messy, apply=True)
    names = {u["path"] for u in m["unidentified"]}
    assert "001.mov" in names
    assert "fully loaded demo v0.mov" in names
    for u in m["unidentified"]:
        assert u["why_unknown"], f"{u['path']} was moved with no stated reason"
    dest = ProjectLayout(messy).read_dir(Area.UNSORTED) / "media"
    assert (dest / "001.mov").exists()
    assert (dest / "README.md").is_file(), "a bucket must say what it is"


def test_an_unattributed_file_is_still_a_measured_one(messy, monkeypatch):
    """"121 MB" says nothing. The measurements are what make it honest."""
    import library.tools.project_migration as mig

    monkeypatch.setattr(
        mig, "media_facts",
        lambda p: "61.7s, 1080x1920 h264, written by DaVinci Resolve Studio")
    m = organize_project(messy, apply=False)
    reason = _by_name(m, "fully loaded demo v0.mov")[0]["reason"]
    assert "DaVinci Resolve Studio" in reason
    assert "cannot be established" in reason


def test_loose_scripts_and_status_files_are_kept_and_labelled(messy):
    organize_project(messy, apply=True)
    unsorted = ProjectLayout(messy).read_dir(Area.UNSORTED)
    assert (unsorted / "loose_scripts" / "parse_broll.py").exists()
    assert (unsorted / "status_files" / "e2e-v4.status").exists()
    assert (unsorted / "status_files" / "state" / "live-test.status").exists()
    assert (unsorted / "mock_data" / "mock_sfx" / "profiles" / "a.json").exists()


def test_a_run_archive_goes_with_the_backups(messy):
    organize_project(messy, apply=True)
    assert (ProjectLayout(messy).read_path(
        Area.BACKUPS, "run_archives", "_archive_2026-08-17_pre_rerun",
        "pipeline_data.json")).exists()


def test_run_state_and_config_stay_at_the_root(messy):
    m = organize_project(messy, apply=True)
    stayed = {e["path"] for e in m["left_in_place"]}
    for name in ("project.yaml", "pipeline_data.json", "pipeline_run.json",
                 ".DS_Store", "raw/", "music/", "exports/"):
        assert name in stayed, f"{name} should have been left alone"
        assert (messy / name.rstrip("/")).exists()


def test_exports_are_not_touched(messy):
    organize_project(messy, apply=True)
    assert (messy / "exports" / "Pipeline_Edit.mp4").exists()


# ── The manifest ────────────────────────────────────────────────────

def test_planning_changes_nothing(messy):
    before = sorted(p.name for p in messy.iterdir())
    plan = plan_organization(messy)
    assert plan.actions
    assert sorted(p.name for p in messy.iterdir()) == before


def test_the_manifest_records_every_action_with_a_reason(messy):
    m = organize_project(messy, apply=True)
    assert m["actions"]
    for a in m["actions"]:
        assert a["src"] and a["dest"] and a["reason"]
        assert a["digest"], "a fingerprint is what matches a file to where it went"
    from pathlib import Path
    stored = json.loads(
        Path(m["manifest_path"]).read_text(encoding="utf-8"))
    assert stored["applied"] is True


def test_the_manifest_reads_as_prose(messy):
    md = render_manifest_markdown(organize_project(messy, apply=True))
    assert "Nothing was deleted" in md
    assert "001.mov" in md
    assert "Could not be identified" in md


def test_a_reorganisation_can_be_undone_by_reading_the_manifest(messy):
    before = sorted(p.name for p in messy.iterdir())
    m = organize_project(messy, apply=True)
    undone = revert_from_manifest(m["manifest_path"], apply=True)
    assert undone
    after = sorted(p.name for p in messy.iterdir())
    assert set(before) - set(after) == set(), (
        f"revert did not restore: {set(before) - set(after)}")


def test_revert_does_not_undo_a_copy(messy):
    """Undoing a copy means deleting, and this tool does not delete."""
    m = organize_project(messy, apply=True)
    dest = ProjectLayout(messy).read_path(
        Area.VISION_ANALYSIS, "clip_profile_IMG_1806_v3.json")
    revert_from_manifest(m["manifest_path"], apply=True)
    assert dest.exists()


def test_organizing_twice_is_a_no_op_the_second_time(messy):
    organize_project(messy, apply=True)
    second = organize_project(messy, apply=True)
    assert not second["actions"], f"the layout is not stable: {second['actions']}"


def test_a_rescued_copy_is_not_copied_again(messy):
    """The source stays in raw/, so nothing else stops a second copy.

    A duplicate would land as `clip_profile_IMG_1806_v3__2.json`, and
    step 1.03 globs `clip_profile_*.json` - it would read that as a stem
    it has never analysed.
    """
    organize_project(messy, apply=True)
    organize_project(messy, apply=True)
    landed = sorted(
        p.name for p in ProjectLayout(messy).read_dir(
            Area.VISION_ANALYSIS).iterdir())
    assert landed == ["clip_profile_IMG_1806_v3.json"]


def test_an_already_tidy_project_needs_nothing(tmp_path):
    ProjectLayout(tmp_path).ensure()
    (tmp_path / "project.yaml").write_text("slug: x\n", encoding="utf-8")
    assert not plan_organization(tmp_path).actions
