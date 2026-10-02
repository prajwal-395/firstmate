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
from pathlib import Path

import pytest

from library.tools.project_layout import Area, ProjectLayout
from library.tools.project_migration import (
    organize_project,
    plan_organization,
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
    assert {a["action"] for a in m["actions"]} <= {
        "move", "copy", "remove_empty_dir"}


def test_a_name_collision_never_overwrites(messy):
    layout = ProjectLayout(messy)
    dest = layout.write_path(Area.UNSORTED, "media", "001.mov")
    dest.write_bytes(b"already here")
    organize_project(messy, apply=True)
    assert dest.read_bytes() == b"already here"
    assert (dest.parent / "001__2.mov").exists()


# ── Input directories are not modified ──────────────────────────────

def test_input_directories_are_left_exactly_as_they_were_found(messy):
    """Untouched, and no action names a destination inside one."""
    before = {d: sorted(p.name for p in (messy / d).rglob("*"))
              for d in ("raw", "music")}
    m = organize_project(messy, apply=True)
    for d, names in before.items():
        assert sorted(p.name for p in (messy / d).rglob("*")) == names, d
    layout = ProjectLayout(messy)
    for a in m["actions"]:
        if a["action"] != "remove_empty_dir":
            layout.assert_writable(a["dest"])


def test_a_vision_cache_inside_raw_is_copied_out_not_moved(messy):
    m = organize_project(messy, apply=True)
    acts = _by_name(m, "clip_profile_IMG_1806_v3.json")
    assert len(acts) == 1 and acts[0]["action"] == "copy"
    assert (messy / "raw" / "analysis"
            / "clip_profile_IMG_1806_v3.json").exists()
    assert (ProjectLayout(messy).read_path(
        Area.VISION_ANALYSIS, "clip_profile_IMG_1806_v3.json")).exists()


# ── Where things go ─────────────────────────────────────────────────

def test_each_kind_of_stray_lands_in_its_labelled_bucket(messy):
    """Hand-made backups go to the legacy store by name; unattributed
    media is admitted (with a reason) rather than guessed at; loose
    scripts, status files and mock data are kept and labelled."""
    m = organize_project(messy, apply=True)
    layout = ProjectLayout(messy)
    assert sorted(p.name for p in layout.legacy_backup_dir().iterdir()) == [
        "pipeline_data.json.bak",
        "pipeline_data.json.bak2",
        "pipeline_data.json.bak_phase1_landscape",
    ]
    assert not list(messy.glob("*.bak*")), "none left at the root"

    names = {u["path"] for u in m["unidentified"]}
    assert {"001.mov", "fully loaded demo v0.mov"} <= names
    for u in m["unidentified"]:
        assert u["why_unknown"], f"{u['path']} was moved with no stated reason"
    unsorted = layout.read_dir(Area.UNSORTED)
    assert (unsorted / "media" / "001.mov").exists()
    assert (unsorted / "media" / "README.md").is_file(), (
        "a bucket must say what it is")
    assert (unsorted / "loose_scripts" / "parse_broll.py").exists()
    assert (unsorted / "status_files" / "e2e-v4.status").exists()
    assert (unsorted / "status_files" / "state" / "live-test.status").exists()
    assert (unsorted / "mock_data" / "mock_sfx" / "profiles" / "a.json").exists()


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
        assert a["src"] and a["reason"]
        if a["action"] != "remove_empty_dir":
            assert a["dest"], "a move/copy must have a destination"
        assert a["digest"], "a fingerprint is what matches a file to where it went"
    from pathlib import Path
    stored = json.loads(
        Path(m["manifest_path"]).read_text(encoding="utf-8"))
    assert stored["applied"] is True


def test_a_reorganisation_can_be_undone_by_reading_the_manifest(messy):
    before = sorted(p.name for p in messy.iterdir())
    m = organize_project(messy, apply=True)
    undone = revert_from_manifest(m["manifest_path"], apply=True)
    assert undone
    after = sorted(p.name for p in messy.iterdir())
    assert set(before) - set(after) == set(), (
        f"revert did not restore: {set(before) - set(after)}")


def test_organizing_twice_is_a_no_op_the_second_time(messy):
    organize_project(messy, apply=True)
    second = organize_project(messy, apply=True)
    assert not second["actions"], f"the layout is not stable: {second['actions']}"


def test_an_already_tidy_project_needs_nothing(tmp_path):
    ProjectLayout(tmp_path).ensure()
    (tmp_path / "project.yaml").write_text("slug: x\n", encoding="utf-8")
    assert not plan_organization(tmp_path).actions


# ── Moving a by-kind project onto the by-step layout ────────────────

@pytest.fixture
def by_kind(tmp_path):
    """A project laid out the way the first version of the layout did."""
    out = tmp_path / "pipeline_output"
    (tmp_path / "project.yaml").write_text("slug: t\n", encoding="utf-8")
    (out / "prosody").mkdir(parents=True)
    (out / "prosody" / "clip_001_prosody.json").write_text(
        '{"clip_id": "clip_001"}', encoding="utf-8")
    (out / "temporal_index").mkdir()
    (out / "temporal_index" / "clip_001.json").write_text("{}", encoding="utf-8")
    (out / "audio_cache").mkdir()
    (out / "audio_cache" / "clip_001.wav").write_bytes(b"\x00" * 8)
    (out / "temporal_index.json").write_text('{"index_dir": "x"}', encoding="utf-8")
    (out / "temporal_index.summary.md").write_text("# summary\n", encoding="utf-8")
    (out / "pipeline_log.jsonl").write_text('{"a":1}\n', encoding="utf-8")
    return tmp_path


def test_a_by_kind_tree_moves_under_its_producing_step(by_kind):
    organize_project(by_kind, apply=True)
    layout = ProjectLayout(by_kind)
    assert (layout.read_path(Area.PROSODY, "clip_001_prosody.json")).is_file()
    assert (layout.read_path(Area.TEMPORAL_INDEX, "clip_001.json")).is_file()
    assert (layout.read_path(Area.AUDIO_CACHE, "clip_001.wav")).is_file()
    assert layout.step_of(
        layout.read_path(Area.PROSODY, "clip_001_prosody.json")
    ) == "prosody_analysis"
    assert not (by_kind / "pipeline_output" / "prosody"
                / "clip_001_prosody.json").exists()
    # The emptied by-kind husks go in the same run: `ls pipeline_output/`
    # is what this change exists to make legible.
    for husk in ("prosody", "temporal_index", "audio_cache"):
        assert not (by_kind / "pipeline_output" / husk).exists(), husk


def test_a_legacy_directory_holding_anything_is_not_removed(by_kind):
    """The one action that removes anything, bounded so it cannot matter.

    Everything under a legacy directory relocates with the step, so in a
    real run the directory IS empty by the time this looks. The bound is
    what matters: told that nothing is moving out, it plans no removal.
    """
    from library.tools.project_migration import _plan_legacy_dir_cleanup

    planned = _plan_legacy_dir_cleanup(ProjectLayout(by_kind), moving=())
    assert not [a for a in planned
                if a.src.endswith("prosody")], (
        "prosody/ still holds a file; removing it is not this tool's call")


def test_a_directory_the_layout_does_not_name_is_never_removed(by_kind):
    """Only the by-kind names, and nothing else the captain may have made."""
    from library.tools.project_migration import (
        LEGACY_AREA_DIRS,
        _plan_legacy_dir_cleanup,
    )

    stray = by_kind / "pipeline_output" / "captains_own_notes"
    stray.mkdir()
    organize_project(by_kind, apply=True)
    assert stray.is_dir(), "an unrecognised empty directory is not ours to remove"
    planned = _plan_legacy_dir_cleanup(ProjectLayout(by_kind))
    assert all(Path(a.src).name in LEGACY_AREA_DIRS for a in planned)


# ── Cleaning up empty pre-created directories ───────────────────────

@pytest.fixture
def scaffolded(tmp_path):
    """A project that was scaffolded by the old ensure() which
    pre-created every step directory and project-level area."""
    (tmp_path / "project.yaml").write_text("slug: t\n", encoding="utf-8")
    layout = ProjectLayout(tmp_path)
    layout.ensure()

    from library.tools.project_layout import STEPS, WRITABLE_KINDS, _OUT, _STEPS

    # Simulate the old ensure() by creating all step dirs and areas.
    for step in STEPS:
        (tmp_path / _STEPS / step.dirname).mkdir(parents=True, exist_ok=True)
    for area, spec in Area.__members__.items():
        area_spec = layout.spec(spec)
        if area_spec.kind in WRITABLE_KINDS and area_spec.relpath != ".":
            (tmp_path / area_spec.relpath).mkdir(parents=True, exist_ok=True)

    # One step has real output - it should NOT be removed.
    step_dir = tmp_path / _STEPS / "1_04_temporal_index"
    (step_dir / "output.json").write_text('{"ok": true}', encoding="utf-8")

    # One project-level area has content - it should NOT be removed.
    (tmp_path / _OUT / "logs" / "run_001.log").write_text(
        "log", encoding="utf-8")

    return tmp_path


def test_empty_scaffold_step_dirs_are_removed(scaffolded):
    from library.tools.project_layout import STEPS, _STEPS

    m = organize_project(scaffolded, apply=True)
    # The step that has output stays, output and all.
    assert (scaffolded / _STEPS / "1_04_temporal_index"
            / "output.json").is_file()
    # Empty step directories are gone.
    removed = [a for a in m["actions"]
               if a["action"] == "remove_empty_dir"
               and "step directory" in a["reason"]]
    # 28 steps, one has content, so 27 should be removed.
    assert len(removed) == len(STEPS) - 1
