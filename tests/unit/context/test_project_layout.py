"""One module owns where a project's output lands, and every step uses it.

Before this existed, `library/tools/paths.py` owned the repo side and
NOTHING owned the project side.  Fifteen steps joined `project_folder`
with a directory name of their own choosing, so:

  * the scaffold in `project_registry` created `pipeline_output/subtitles`
    and `pipeline_output/motion_graphics` while the steps that render
    those wrote `subtitle_segments` and `motion_graphics_segments` - two
    empty directories nobody opened, and two the scaffold never made;
  * step 1.03 and step 1.07 wrote their analysis into `raw/`, the
    captain's own footage directory;
  * steps 4.05 and 4.06 fell back to `<repo>/pipeline_output/` when they
    were handed no project, which in a disposable worktree means the
    render is gone the moment the worktree is;
  * `pipeline_data.json` was protected by nine hand-made `.bak*` files at
    the project root with no policy and no way to tell which mattered.

These tests hold the three properties that make that unrepeatable: the
count of steps on the owner, the guard that refuses a write outside the
layout, and the bounded backup store.
"""
from __future__ import annotations
import json
import re
from pathlib import Path
import pytest
from library.tools.project_layout import (
    AREAS,
    MAX_PIPELINE_DATA_BACKUPS,
    WRITABLE_KINDS,
    Area,
    ProjectLayout,
    ProjectLayoutViolation,
)
from library.tools.project_migration import (
    organize_project,
    plan_organization,
    revert_from_manifest,
)
import os
import sys
from library.tools.versions import store, worktrees
import shutil
from library.processes.edit_video import run_pipeline as runner
from library.tools import footage_identity, step_ledger


REPO_ROOT = Path(__file__).resolve().parents[3]
STEPS_ROOT = REPO_ROOT / "library" / "steps"

# ── The count ───────────────────────────────────────────────────────


def test_no_step_joins_a_project_folder_with_a_directory_name():
    """The pattern the owner exists to replace, banned at the source."""
    offenders = []
    pattern = re.compile(
        r"""(os\.path\.join\(\s*(project_folder|project_dir)\s*,\s*["']"""
        r"""|Path\(\s*(project_folder|project_dir)\s*\)\s*/\s*["'])"""
    )
    for py in STEPS_ROOT.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        for n, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{py.relative_to(REPO_ROOT)}:{n}: {line.strip()}")
    assert not offenders, (
        "a step is composing a project path inline again:\n  "
        + "\n  ".join(offenders)
    )


# ── The guard ───────────────────────────────────────────────────────

def test_input_areas_refuse_every_write(tmp_path):
    """INPUT means INPUT: the captain's footage and subtitle directories
    refuse write_dir, write_path and assert_writable, and a refused
    write creates nothing on the way out."""
    layout = ProjectLayout(tmp_path)
    with pytest.raises(ProjectLayoutViolation, match="input"):
        layout.write_dir(Area.RAW)
    assert not (tmp_path / "raw").exists()
    for area in (Area.RAW, Area("subtitle_plans"), Area("subtitle_overlays"),
                 Area.RUN_PROFILES, Area.EXTERNAL_STATE,
                 Area.EXTERNAL_DECLARATIONS):
        with pytest.raises(ProjectLayoutViolation):
            layout.write_dir(area)
        with pytest.raises(ProjectLayoutViolation):
            layout.write_path(area, "anything.json")
        with pytest.raises(ProjectLayoutViolation):
            layout.assert_writable(layout.read_dir(area) / "x.json")


def test_legacy_external_files_migrate_into_separate_input_areas(tmp_path):
    from library.tools.project_layout import Kind

    layout = ProjectLayout(tmp_path)
    layout.ensure()
    legacy = tmp_path / "external"
    legacy.mkdir()
    files = {
        "assembly_manifest.json": b'{"legacy": "state"}',
        "overlay_intent.json": b'{"legacy": "declaration"}',
        "captain_edits.json": b'{"legacy": "keyed declaration"}',
        "unknown.json": b'{"legacy": "unknown"}',
    }
    for name, body in files.items():
        (legacy / name).write_bytes(body)

    plan = plan_organization(tmp_path)
    destinations = {
        Path(action.src).name: Path(action.dest).relative_to(tmp_path).as_posix()
        for action in plan.actions
        if action.src.startswith(str(legacy))
    }
    assert destinations == {
        "assembly_manifest.json": "external/state/assembly_manifest.json",
        "overlay_intent.json": "external/declarations/overlay_intent.json",
        "captain_edits.json": "external/declarations/captain_edits.json",
    }
    assert any(entry["path"] == "external/unknown.json"
               for entry in plan.left_in_place)

    manifest = organize_project(tmp_path, apply=True)
    expected_areas = {
        Area.EXTERNAL_STATE: "external/state",
        Area.EXTERNAL_DECLARATIONS: "external/declarations",
    }
    for area, relpath in expected_areas.items():
        assert AREAS[area].relpath == relpath
        assert AREAS[area].kind is Kind.INPUT
    for name in ("assembly_manifest.json", "overlay_intent.json",
                 "captain_edits.json"):
        path = tmp_path / destinations[name]
        assert path.read_bytes() == files[name]
        assert not (legacy / name).exists()
        with pytest.raises(ProjectLayoutViolation, match="input"):
            layout.assert_writable(path)
    assert (legacy / "unknown.json").read_bytes() == files["unknown.json"]
    moved = [action for action in manifest["actions"]
             if action["src"].startswith(str(legacy))]
    assert len(moved) == 3
    assert manifest["policy"]["external_input_relocation"]

    revert_from_manifest(manifest["manifest_path"], apply=True)
    for name, body in files.items():
        assert (legacy / name).read_bytes() == body


def test_writes_outside_the_layout_are_refused(tmp_path):
    """Outside the project, the bare root (project.yaml and run state
    only), an escape out of an area, an unknown area, and an empty
    project folder (the fallback that once wrote into the repo)."""
    layout = ProjectLayout(tmp_path)
    with pytest.raises(ProjectLayoutViolation, match="outside the project"):
        layout.assert_writable("/etc/passwd")
    with pytest.raises(ProjectLayoutViolation):
        layout.assert_writable(tmp_path / "stray.mov")
    with pytest.raises(ProjectLayoutViolation):
        layout.write_path(Area.PROSODY, "..", "..", "raw", "x")
    with pytest.raises(ProjectLayoutViolation, match="Unknown project area"):
        layout.write_dir("wherever_i_like")
    for empty in ("", "   ", None):
        with pytest.raises(ProjectLayoutViolation):
            ProjectLayout(empty)
    # The positive control: an output path is writable.
    p = layout.write_path(Area.PROSODY, "clip_001_prosody.json")
    assert layout.assert_writable(p) == p
    assert layout.assert_writable(tmp_path / "exports" / "Pipeline_Edit.mp4")


# ── A step writes only in its own directory ─────────────────────────

def test_a_step_writes_only_in_its_own_directory(tmp_path):
    """`pipeline_output/steps/` only means anything if this holds: a
    directory named for a step means that step wrote it, so the answer
    to "which step wrote this" is the path. Areas no step owns
    (`exports/` for 6.01 and 6.02, `scratch/` for anyone) are shared."""
    layout = ProjectLayout(tmp_path)
    with pytest.raises(ProjectLayoutViolation) as exc:
        layout.write_dir(Area.PROSODY, step="render_subtitles")
    assert "belongs to step 'prosody_analysis'" in str(exc.value)
    assert "writes only inside its own directory" in str(exc.value)

    assert layout.write_dir(Area.EXPORTS, step="render")
    assert layout.write_dir(Area.EXPORTS, step="validate")
    assert layout.write_dir(Area.SCRATCH, step="prosody_analysis")

    for area, spec in AREAS.items():
        if not spec.step:
            continue
        p = layout.write_path(area, "f.json", step=spec.step)
        assert layout.step_of(p) == spec.step, f"{area.value} is misfiled"


# ── The step table matches the pipeline ─────────────────────────────


def test_the_step_and_area_tables_match_the_repository():
    """Every step directory is in STEPS; `wired` means SOME process
    declares a node (`build_reels`/`verify_reels` live in
    `library/processes/reels`, not edit_video); no two areas claim one
    directory."""
    from library.tools import processes
    from library.tools.project_layout import STEPS

    repo = Path(__file__).resolve().parents[3] / "library" / "steps"
    on_disk = {p.name[len("step_"):] for p in repo.iterdir()
               if p.name.startswith("step_") and p.is_dir()}
    assert on_disk == {s.dirname for s in STEPS}

    dag_ids = set(processes.node_owners())
    for step in STEPS:
        assert step.wired == (step.node_id in dag_ids), (
            f"{step.node_id}: wired={step.wired} disagrees with every DAG")

    seen = {}
    for area, spec in AREAS.items():
        if spec.relpath == ".":
            continue
        assert spec.relpath not in seen, (
            f"{area.value} and {seen[spec.relpath]} both claim "
            f"{spec.relpath}")
        seen[spec.relpath] = area.value


# ── The layout itself ───────────────────────────────────────────────

def test_ensure_creates_containers_and_not_the_input_side(tmp_path):
    """ensure() creates the two structural containers and the README.

    Individual step directories and project-level areas appear when
    something writes to them, not when the scaffold guesses.  An empty
    directory that was pre-created carries no information."""
    ProjectLayout(tmp_path).ensure()
    # The containers must exist.
    assert (tmp_path / "pipeline_output").is_dir()
    assert (tmp_path / "pipeline_output" / "steps").is_dir()
    # Input areas must NOT be created.
    for area, spec in AREAS.items():
        if spec.relpath == ".":
            continue
        if spec.kind not in WRITABLE_KINDS:
            assert not (tmp_path / spec.relpath).is_dir(), (
                f"{area.value} is {spec.kind.value}; creating it is the "
                f"first half of writing to it")
    # Step directories must NOT be pre-created.
    steps_root = tmp_path / "pipeline_output" / "steps"
    assert not list(steps_root.iterdir()), (
        "no step directory should exist before the step writes to it")
    # Project-level areas (gates/, annotations/ etc.) must NOT be pre-created.
    for area, spec in AREAS.items():
        if spec.step or spec.relpath in (".", "pipeline_output",
                                         "pipeline_output/steps"):
            continue
        if spec.kind in WRITABLE_KINDS:
            assert not (tmp_path / spec.relpath).is_dir(), (
                f"{area.value} should not be pre-created by ensure()")
    # Writing to an area creates just that step's directory.
    ProjectLayout(tmp_path).write_dir(Area.PROSODY, step="prosody_analysis")
    assert [p.name for p in steps_root.iterdir()] == ["1_05_prosody_analysis"]


# ── Backups ─────────────────────────────────────────────────────────

def _state(tmp_path, marker):
    ProjectLayout(tmp_path).pipeline_data_path.write_text(
        json.dumps({"marker": marker}), encoding="utf-8")


def test_a_backup_is_a_copy_and_the_store_is_bounded(tmp_path):
    _state(tmp_path, "before")
    layout = ProjectLayout(tmp_path)
    made = layout.backup_pipeline_data(label="run", now=1_699_000_000)
    _state(tmp_path, "after")
    assert json.loads(made.read_text(encoding="utf-8"))["marker"] == "before"

    for i in range(MAX_PIPELINE_DATA_BACKUPS + 7):
        layout.backup_pipeline_data(label=f"run{i}", now=1_700_000_000 + i * 60)
    kept = layout.automatic_backups()
    assert len(kept) == MAX_PIPELINE_DATA_BACKUPS, (
        "nine hand-made backups is what an unbounded store looks like")
    assert kept[-1].name.endswith(
        f"run{MAX_PIPELINE_DATA_BACKUPS + 6}.json"), "the newest must survive"


def test_hand_made_backups_are_never_pruned(tmp_path):
    """The pruner only touches files it could have written itself."""
    _state(tmp_path, "x")
    layout = ProjectLayout(tmp_path)
    legacy = layout.legacy_backup_dir() / "pipeline_data.json.bak_phase1_landscape"
    legacy.write_text("{}", encoding="utf-8")
    beside = layout.backup_dir() / "pipeline_data.json.bak3"
    beside.write_text("{}", encoding="utf-8")

    for i in range(MAX_PIPELINE_DATA_BACKUPS * 2):
        layout.backup_pipeline_data(now=1_700_000_000 + i * 60)

    assert legacy.exists(), "a legacy backup must survive every prune"
    assert beside.exists(), (
        "a file the naming policy did not write is not the pruner's to "
        "delete, wherever it sits")


# ── The runner takes one backup per run, not per save ───────────────

def test_the_runner_backs_up_once_per_run(tmp_path, monkeypatch):
    """save_pipeline_state runs after every step; 26 backups a run would
    spend the whole retention window inside one run."""
    from library.processes.edit_video import run_pipeline

    _state(tmp_path, "before")
    monkeypatch.setattr(run_pipeline, "_BACKED_UP_THIS_PROCESS", set())
    for i in range(5):
        run_pipeline.save_pipeline_state(str(tmp_path), {"step": i})

    assert len(ProjectLayout(tmp_path).automatic_backups()) == 1


# ── Read-side audit: no consumer crashes on a bare project ──────────

def test_read_paths_survive_a_project_with_no_directories(tmp_path):
    """The lazy ensure() creates only containers.  Every consumer that
    calls read_dir() must handle the case where the returned path does
    not exist.  This test exercises those code paths against a project
    whose directories have never been created - the case that could not
    previously occur and no existing test covered.
    """
    # A project with ONLY project.yaml and pipeline_data.json - nothing
    # else exists.  No ensure(), no input dirs, no output dirs.
    (tmp_path / "project.yaml").write_text(
        "name: bare\nslug: bare\n", encoding="utf-8")
    (tmp_path / "pipeline_data.json").write_text(
        '{"step_outputs": {}, "preflight_completed": {}, '
        '"edit_completed": {}, "failed_steps": []}',
        encoding="utf-8")

    layout = ProjectLayout(tmp_path)

    # 1. review_gate: every read function returns a safe default.
    from library.tools.review_gate import (
        get_all_gate_statuses,
        get_gate_status,
        load_gate_feedback,
        load_gate_snapshot,
    )
    assert get_all_gate_statuses(str(tmp_path)) == {}
    assert get_gate_status(str(tmp_path), "scan") == "none"
    assert load_gate_snapshot(str(tmp_path), "scan") is None
    assert load_gate_feedback(str(tmp_path), "scan") is None

    # 2. provenance: snapshot returns empty, no crash on missing dirs.
    from library.tools.provenance import ProvenanceLedger
    prov = ProvenanceLedger(tmp_path)
    assert prov.snapshot() == {}

    # 3. music_selection_contract: sources include paths but no crash.
    from library.tools.music_selection_contract import catalogue_sources
    sources = catalogue_sources(str(tmp_path))
    for dirs in sources.values():
        for d in dirs:
            # The path may not exist, but the caller is expected to
            # check os.path.isdir() before listing.
            import os
            if os.path.isdir(d):
                os.listdir(d)  # would crash if wrongly assumed to exist

    # 4. paths.py: returns a path without I/O.
    from library.tools.paths import project_output_dir
    p = project_output_dir(str(tmp_path))
    assert isinstance(p, Path)
    # p may not exist - that is fine, it is just a path.

    # 5. project_config: properties return paths without I/O.
    from library.schemas.project_config import ProjectConfig
    cfg = ProjectConfig(name="bare", slug="bare")
    object.__setattr__(cfg, "_project_root", Path(tmp_path))
    assert isinstance(cfg.raw_dir, Path)
    assert isinstance(cfg.pipeline_output_dir, Path)
    assert isinstance(cfg.exports_dir, Path)

    # 6. read_dir on every area returns a path without crashing.
    for area in Area:
        p = layout.read_dir(area)
        assert isinstance(p, Path)
        # None of these should exist, and that is the point.


# ── The captain's own working directories (geo-podcast, 2026-09-09) ────


def test_no_library_code_names_the_captains_stray_directories():
    """The three reconciled directories are captain-side names. Nothing
    under `library/` may compose them: the subtitle directories are
    written by the captain's own scripts via `SCRIPT_DIR`, and the vox
    test renders are hand exports off Resolve. A path-composing reference
    here would be a second writer the layout does not reconcile.

    Delimited so `render_subtitle_overlays` (the step 4.05 function) does
    not match: only a quoted or path-joined directory name counts. The
    owner itself is excluded: naming the row IS the reconciliation."""
    pattern = re.compile(
        r"""['"/]subtitle_plans['"/]|['"/]subtitle_overlays['"/]|"""
        r"""['"/]vox_test_renders['"/]"""
    )
    offenders = []
    for py in (REPO_ROOT / "library").rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        if py.name == "project_layout.py":
            continue  # the owner names every row; that is the fix
        for n, line in enumerate(
                py.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(
                    f"{py.relative_to(REPO_ROOT)}:{n}: {line.strip()}")
    assert not offenders, (
        "library code names a captain-side directory again:\n  "
        + "\n  ".join(offenders)
    )


# --------------------------------------------------------------------------
# From test_project_migration.py
#
# Organising a project folder never deletes and never touches inputs.
#
# Project 001 - the captain's real work, and the only project that exists -
# had nine hand-made `pipeline_data.json` backups, six one-off Python
# scripts, two unattributed .mov files totalling 1.14 GB, a mock SFX
# directory, an ad-hoc `state/` directory and a 517 MB `_archive_...`
# directory, all at its top level.
#
# These tests hold the three rules that make organising it safe: nothing is
# deleted, input directories are not modified, and every action is recorded
# in a manifest that can be read back to undo it.

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


# --------------------------------------------------------------------------
# From test_retention.py
#
# Lean retention deletes, so every test here is a file that must SURVIVE.
#
# The captain's D6 (2026-09-23): once a reel is signed off, purge the
# bloat - but never anything a live timeline references, never anything
# an unsigned reel still needs, never the project's inputs or live
# records. Each gate is proven in both directions on one shape: the file
# that must go goes, and its twin that must stay stays.

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools import processes, reel_signoff, retention
from library.tools.timeline_transcript import transcript_path
from tests.unit.resolve.test_build_sweep import (
    _asset_dir,
    _database,
    _entry,
    _ledger,
    _write,
)

SIGNED = "Reel 09 - your-website-is-only-20-percent"
UNSIGNED = "Reel 12 - ai-isnt-making-things-up"


@pytest.fixture
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


@pytest.mark.usefixtures("_lean")
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


@pytest.mark.usefixtures("_lean")
def test_applied_purge_receipt_counts_hardlinked_bytes_once(tmp_path):
    root = _project(tmp_path)
    directory = os.path.join(root, "pipeline_output", "quarantine",
                             "hardlinks")
    first = _write(os.path.join(directory, "first.bin"), size=1024)
    second = os.path.join(directory, "second.bin")
    os.link(first, second)
    db = _database(str(tmp_path / "db" / "Project.db"), [], [SIGNED])

    plan = retention.plan_purge(root, [db])
    manifest = retention.write_plan(plan)
    record = retention.apply_purge(manifest, [db], project_folder=root)

    assert plan.reclaimable_bytes == 1024
    assert record["removed_count"] == 2
    assert record["bytes"] == plan.reclaimable_bytes
    assert not os.path.exists(first) and not os.path.exists(second)


@pytest.mark.usefixtures("_lean")
def test_applied_purge_receipt_counts_only_links_removed(tmp_path):
    root = _project(tmp_path)
    directory = os.path.join(root, "pipeline_output", "quarantine",
                             "hardlinks")
    first = _write(os.path.join(directory, "first.bin"), size=1024)
    second = os.path.join(directory, "second.bin")
    os.link(first, second)
    db = _database(str(tmp_path / "db" / "Project.db"), [], [SIGNED])

    manifest = retention.write_plan(retention.plan_purge(root, [db]))
    lines = Path(manifest).read_text(encoding="utf-8").splitlines()
    Path(manifest).write_text(
        "\n".join(line for line in lines if not line.endswith("\t" + second))
        + "\n", encoding="utf-8")

    record = retention.apply_purge(manifest, [db], project_folder=root)

    assert record["removed"] == [first]
    assert record["bytes"] == 0
    assert not os.path.exists(first) and os.path.isfile(second)


@pytest.mark.usefixtures("_lean")
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


@pytest.mark.usefixtures("_lean")
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


@pytest.mark.usefixtures("_lean")
def test_no_timeline_database_refuses_rather_than_purging(tmp_path):
    """Without the timelines nothing can be shown unreferenced."""
    root = _project(tmp_path)
    _mov(root, "sub_a_c_1-2_aaaaaaaa")
    with pytest.raises(retention.PurgeRefused):
        retention.plan_purge(root, [])
    with pytest.raises(retention.PurgeRefused):
        retention.plan_purge(root, [str(tmp_path / "missing.db")])


@pytest.mark.usefixtures("_lean")
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


@pytest.mark.usefixtures("_lean")
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


@pytest.mark.usefixtures("_lean")
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


@pytest.mark.usefixtures("_lean")
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


# --------------------------------------------------------------------------
# From test_task_worktrees.py
#
# Concurrent tasks on one project each hold their own semantic state.
#
# The defect: the project store has ONE checkout and a branch is a property
# of it, so a task that branched (`variants.create_variation`) moved every
# other task's writes onto its branch - the captain's geo-podcast checkout
# sat on a variant branch for three days with 127 tracked edits on it.
# `library/tools/versions/worktrees.py`.

def _branch(folder) -> str:
    return store.git(folder, "rev-parse", "--abbrev-ref",
                     "HEAD").stdout.strip()


def _write_2(folder, rel, document) -> None:
    path = folder / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv(worktrees.ROOT_ENV, str(tmp_path / "worktrees"))
    folder = tmp_path / "project"
    folder.mkdir()
    assert store.init_project_repo(str(folder))["initialised"]
    (folder / "project.yaml").write_text("name: p\n", encoding="utf-8")
    _write_2(folder, "external/declarations/captain_edits.json", {"edits": []})
    _write_2(folder, "external/declarations/reel_ending.json", {"ending": "cut"})
    assert store.commit_build(str(folder), "base")["committed"]
    return folder


def test_two_tasks_hold_separate_states_and_both_merge(project):
    home = _branch(project)
    a = worktrees.add(str(project), "captions")
    b = worktrees.add(str(project), "Reel 07 ending")
    assert a["added"] and b["added"], (a, b)
    assert b["branch"] == "ren/reel-07-ending"

    # Each task edits a different declaration in its own checkout.
    _write_2(Path(a["path"]), "external/declarations/captain_edits.json",
           {"edits": ["caption 3 lower"]})
    _write_2(Path(b["path"]), "external/declarations/reel_ending.json",
           {"ending": "hold"})
    assert worktrees.commit(str(project), "captions", "a")["committed"]
    assert worktrees.commit(str(project), "reel 07 ending", "b")["committed"]

    # Neither task moved the project checkout or wrote into it.
    assert _branch(project) == home
    assert json.loads((project / "external/declarations/reel_ending.json").read_text())[
        "ending"] == "cut"
    assert {t["task"] for t in worktrees.list_tasks(str(project))} == {
        "captions", "reel-07-ending"}

    for task in ("captions", "reel 07 ending"):
        merged = worktrees.merge(str(project), task)
        assert merged["merged"], merged
        assert worktrees.remove(str(project), task)["branch_deleted"]
    assert _branch(project) == home
    assert json.loads((project / "external/declarations/captain_edits.json").read_text())[
        "edits"] == ["caption 3 lower"]
    assert json.loads((project / "external/declarations/reel_ending.json").read_text())[
        "ending"] == "hold"
    assert worktrees.list_tasks(str(project)) == []


def test_conflicting_declarations_reach_a_human_and_uncommitted_work_stays(
        project):
    for task, ending in (("one", "hold"), ("two", "fade")):
        opened = worktrees.add(str(project), task)
        _write_2(Path(opened["path"]), "external/declarations/reel_ending.json",
               {"ending": ending})
        if task == "two":
            # Uncommitted work is never merged and never removed.
            assert not worktrees.merge(str(project), task)["merged"]
            assert not worktrees.remove(str(project), task)["removed"]
        assert worktrees.commit(str(project), task, task)["committed"]

    assert worktrees.merge(str(project), "one")["merged"]
    second = worktrees.merge(str(project), "two")
    assert not second["merged"]
    assert second["conflicts"] == ["external/declarations/reel_ending.json"]


# --------------------------------------------------------------------------
# From test_project_relocation_keeps_preflight.py
#
# Copying a project must not invalidate its preflight analysis.
#
# Finding 7, execution-frontier report 2026-09-24: source fingerprints
# recorded the absolute path, so copying a project read every clip as
# replaced and deleted its per-clip analysis. Same content at a new path
# is a relocation; genuinely changed content at a new path is still
# replaced.
#
# This exercises the source check on a copied scratch project with one
# cached semantic profile. No pipeline run, model, or real project.

STEP_DIR = REPO_ROOT / "library" / "steps" / "step_1_03_semantic_analysis"
with open(STEP_DIR / "manifest.json", encoding="utf-8") as handle:
    SEMANTIC_MANIFEST = json.load(handle)
STAGES = {"semantic_analysis": step_ledger.PREFLIGHT}
MANIFESTS = {"semantic_analysis": SEMANTIC_MANIFEST}


def _build_project(root: Path):
    raw = root / "raw"
    raw.mkdir(parents=True)
    (raw / "clip.mov").write_bytes(b"same footage bytes" * 64)
    files, _skipped = footage_identity.enumerate_footage(str(root))
    fingerprints = footage_identity.fingerprints_for(files)

    clip_id = files[0]["clip_id"]
    profile_paths = step_ledger.artifact_paths(
        str(root),
        step_ledger.per_clip_artifacts(SEMANTIC_MANIFEST),
        clip_id,
        Path(files[0]["path"]).stem,
    )
    cached_profiles = {}
    for path in profile_paths:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"cached semantic analysis")
        cached_profiles[Path(path).relative_to(root)] = b"cached semantic analysis"

    state = {
        "project_folder": str(root),
        step_ledger.LEDGER_KEY[step_ledger.PREFLIGHT]: {
            "semantic_analysis": {
                "completed_at": "2026-08-20T10:00:00",
                "elapsed_s": 2400,
            },
        },
        step_ledger.SOURCE_FINGERPRINTS_KEY: fingerprints,
    }
    return state, cached_profiles


def test_copying_a_project_to_a_new_path_preserves_preflight(tmp_path):
    """Same-content paths keep the cache instead of marking it stale."""
    src = tmp_path / "original"
    state, cached_profiles = _build_project(src)
    dst = tmp_path / "elsewhere" / "copy"
    shutil.copytree(src, dst)

    delta = runner.apply_source_identity(str(dst), state, STAGES, MANIFESTS)

    assert not delta.stale_clip_ids
    assert step_ledger.is_completed(state, "semantic_analysis")
    for relative_path, contents in cached_profiles.items():
        assert (dst / relative_path).read_bytes() == contents


def test_changed_content_at_a_new_path_still_invalidates():
    """Different bytes at a new path cannot be mistaken for relocation."""
    recorded = {
        "clip_001": {"path": "/old/project/raw/a.mov",
                     "size_bytes": 100,
                     "content_digest": "abc"},
    }
    current = {
        "clip_001": {"path": "/new/project/raw/a.mov",
                     "size_bytes": 100,
                     "content_digest": "different-bytes"},
    }

    delta = footage_identity.compare(recorded, current)

    assert "clip_001" in delta.stale_clip_ids
