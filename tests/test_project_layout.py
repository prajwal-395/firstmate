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

import json
import re
from pathlib import Path

import pytest

from library.tools.project_layout import (
    AREAS,
    MAX_PIPELINE_DATA_BACKUPS,
    WRITABLE_KINDS,
    Area,
    Kind,
    ProjectLayout,
    ProjectLayoutViolation,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
STEPS_ROOT = REPO_ROOT / "library" / "steps"

# Every step that composes a project-relative path.  This is the list the
# migration had to cover; a step that stops using the owner fails here.
STEPS_ON_THE_LAYOUT = {
    "step_1_01_scan_project",
    "step_1_03_semantic_analysis",
    "step_1_04_temporal_index",
    "step_1_05_prosody_analysis",
    "step_1_06_object_segmentation",
    "step_1_07_ocr_extraction",
    "step_2_02_speech_sequence",
    "step_2_04_music_selection",
    "step_2_06_music_analysis",
    "step_3_03_review_rough_cut",
    "step_4_05_render_subtitles",
    "step_4_06_render_motion_graphics",
    "step_5_01_color_grade",
    "step_5_04_compile_manifest",
    "step_6_01_render",
}


def _steps_importing_the_layout() -> set:
    found = set()
    for py in STEPS_ROOT.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        if "project_layout import" in py.read_text(encoding="utf-8"):
            found.add(py.relative_to(STEPS_ROOT).parts[0])
    return found


# ── The count ───────────────────────────────────────────────────────

def test_every_step_that_composes_a_project_path_is_on_the_owner():
    missing = STEPS_ON_THE_LAYOUT - _steps_importing_the_layout()
    assert not missing, (
        f"{len(missing)} step(s) compose their own project paths again: "
        f"{sorted(missing)}. Name an Area and let "
        f"library/tools/project_layout.py return the path."
    )


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

def test_a_step_cannot_write_into_the_captains_footage(tmp_path):
    layout = ProjectLayout(tmp_path)
    with pytest.raises(ProjectLayoutViolation) as exc:
        layout.write_dir(Area.RAW)
    assert "input" in str(exc.value)
    assert not (tmp_path / "raw").exists(), (
        "a refused write must not create the directory on the way out")


@pytest.mark.parametrize("area", [
    Area.RAW, Area.MUSIC, Area.ASSETS, Area.BRAND_ASSETS, Area.COMPOSITIONS,
])
def test_no_input_area_is_writable(tmp_path, area):
    with pytest.raises(ProjectLayoutViolation):
        ProjectLayout(tmp_path).write_path(area, "anything.json")


def test_an_input_area_is_still_readable(tmp_path):
    """Protection is about writing. Steps read raw/ constantly."""
    p = ProjectLayout(tmp_path).read_path(Area.RAW, "IMG_1806.MOV")
    assert p == tmp_path / "raw" / "IMG_1806.MOV"


def test_assert_writable_refuses_a_path_inside_an_input_area(tmp_path):
    with pytest.raises(ProjectLayoutViolation) as exc:
        ProjectLayout(tmp_path).assert_writable(tmp_path / "music" / "track.wav")
    assert "must not write" in str(exc.value)


def test_assert_writable_refuses_a_path_outside_the_project(tmp_path):
    with pytest.raises(ProjectLayoutViolation) as exc:
        ProjectLayout(tmp_path).assert_writable("/etc/passwd")
    assert "outside the project" in str(exc.value)


def test_assert_writable_refuses_the_bare_project_root(tmp_path):
    """The root holds project.yaml and run state. Nothing else."""
    with pytest.raises(ProjectLayoutViolation):
        ProjectLayout(tmp_path).assert_writable(tmp_path / "stray.mov")


def test_assert_writable_accepts_an_output_path(tmp_path):
    layout = ProjectLayout(tmp_path)
    p = layout.write_path(Area.PROSODY, "clip_001_prosody.json")
    assert layout.assert_writable(p) == p
    assert layout.assert_writable(tmp_path / "exports" / "Pipeline_Edit.mp4")


def test_parts_cannot_escape_their_area(tmp_path):
    with pytest.raises(ProjectLayoutViolation):
        ProjectLayout(tmp_path).write_path(Area.PROSODY, "..", "..", "raw", "x")


def test_an_unknown_area_raises_rather_than_being_created(tmp_path):
    with pytest.raises(ProjectLayoutViolation) as exc:
        ProjectLayout(tmp_path).write_dir("wherever_i_like")
    assert "Unknown project area" in str(exc.value)


def test_an_empty_project_folder_raises():
    """The fallback that used to write pipeline output into the repo."""
    for empty in ("", "   ", None):
        with pytest.raises(ProjectLayoutViolation):
            ProjectLayout(empty)


# ── A step writes only in its own directory ─────────────────────────

def test_a_step_cannot_write_into_another_steps_directory(tmp_path):
    """`pipeline_output/steps/` only means anything if this holds. A
    directory named for a step has to mean that step wrote it."""
    layout = ProjectLayout(tmp_path)
    with pytest.raises(ProjectLayoutViolation) as exc:
        layout.write_dir(Area.PROSODY, step="render_subtitles")
    assert "belongs to step 'prosody_analysis'" in str(exc.value)
    assert "writes only inside its own directory" in str(exc.value)


def test_a_step_may_write_into_its_own_directory(tmp_path):
    layout = ProjectLayout(tmp_path)
    d = layout.write_dir(Area.PROSODY, step="prosody_analysis")
    assert layout.step_of(d) == "prosody_analysis"


def test_a_step_may_write_to_an_area_no_step_owns(tmp_path):
    """`exports/` is shared by 6.01 and 6.02, and `scratch/` by anyone."""
    layout = ProjectLayout(tmp_path)
    assert layout.write_dir(Area.EXPORTS, step="render")
    assert layout.write_dir(Area.EXPORTS, step="validate")
    assert layout.write_dir(Area.SCRATCH, step="prosody_analysis")


def test_assert_step_owns_refuses_a_path_in_another_steps_directory(tmp_path):
    """The guard for a path that arrived from outside the layout."""
    layout = ProjectLayout(tmp_path)
    stray = layout.write_path(Area.PROSODY, "x.json", step="prosody_analysis")
    layout.assert_step_owns("prosody_analysis", stray)
    with pytest.raises(ProjectLayoutViolation):
        layout.assert_step_owns("render_subtitles", stray)


def test_an_unknown_step_raises_rather_than_getting_a_directory(tmp_path):
    with pytest.raises(ProjectLayoutViolation) as exc:
        ProjectLayout(tmp_path).step_dir("nope")
    assert "Unknown step" in str(exc.value)


def test_a_files_directory_names_the_step_that_wrote_it(tmp_path):
    """The payoff: no index and no sidecar - the answer is the path."""
    layout = ProjectLayout(tmp_path)
    for area, spec in AREAS.items():
        if not spec.step:
            continue
        p = layout.write_path(area, "f.json", step=spec.step)
        assert layout.step_of(p) == spec.step, f"{area.value} is misfiled"


def test_a_path_outside_steps_belongs_to_no_step(tmp_path):
    layout = ProjectLayout(tmp_path)
    assert layout.step_of(layout.write_path(Area.LOGS, "run.log")) is None
    assert layout.step_of(layout.write_dir(Area.EXPORTS)) is None


# ── The step table matches the pipeline ─────────────────────────────

def test_every_step_in_the_table_is_a_real_step_directory():
    from library.tools.project_layout import STEPS

    repo = Path(__file__).resolve().parent.parent / "library" / "steps"
    on_disk = {p.name for p in repo.iterdir() if p.name.startswith("step_")}
    for step in STEPS:
        assert f"step_{step.dirname}" in on_disk, (
            f"{step.node_id} names directory {step.dirname}, which is not "
            f"a step in library/steps/")


def test_every_step_directory_is_in_the_table():
    from library.tools.project_layout import STEPS

    repo = Path(__file__).resolve().parent.parent / "library" / "steps"
    on_disk = {p.name[len("step_"):] for p in repo.iterdir()
               if p.name.startswith("step_") and p.is_dir()}
    assert on_disk == {s.dirname for s in STEPS}


def test_the_table_is_in_dag_order_and_names_the_dag_nodes():
    from library.tools.project_layout import STEPS
    from library.tools.run_traceback import load_dag

    dag_ids = [n["id"] for n in load_dag()["nodes"]]
    wired = [s.node_id for s in STEPS if s.wired]
    assert wired == dag_ids, (
        "STEPS must list the wired steps in the order the DAG runs them - "
        "the generated README renders from it")


def test_the_unwired_steps_are_marked_unwired():
    from library.tools.project_layout import STEPS
    from library.tools.run_traceback import load_dag

    dag_ids = {n["id"] for n in load_dag()["nodes"]}
    for step in STEPS:
        assert step.wired == (step.node_id in dag_ids), (
            f"{step.node_id}: wired={step.wired} disagrees with the DAG")


def test_every_step_owned_area_lives_under_its_step(tmp_path):
    from library.tools.project_layout import STEP_BY_ID

    for area, spec in AREAS.items():
        if not spec.step:
            continue
        expected = f"pipeline_output/steps/{STEP_BY_ID[spec.step].dirname}"
        assert spec.relpath == expected or spec.relpath.startswith(expected + "/"), (
            f"{area.value} declares step {spec.step} but sits at {spec.relpath}")


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


def test_the_folder_explains_itself(tmp_path):
    """Someone opening this in six months reads one file, not the code."""
    ProjectLayout(tmp_path).ensure()
    readme = (tmp_path / "README-LAYOUT.md").read_text(encoding="utf-8")
    for area, spec in AREAS.items():
        if spec.relpath in (".", "pipeline_output", "pipeline_output/steps"):
            continue  # the containers; their contents are what is described
        assert spec.purpose in readme, f"{area.value} has no stated purpose"


def test_the_readme_walks_the_pipeline_in_the_order_it_runs(tmp_path):
    """The whole point: `ls` and the README both read as the pipeline."""
    from library.tools.project_layout import STEPS

    ProjectLayout(tmp_path).ensure()
    readme = (tmp_path / "README-LAYOUT.md").read_text(encoding="utf-8")
    positions = [readme.index(f"steps/{s.dirname}/") for s in STEPS]
    assert positions == sorted(positions), (
        "the README must render steps in run order, not alphabetically")


def test_no_step_directory_exists_before_the_step_writes(tmp_path):
    """A directory that was pre-created carries no information.  A
    directory that appears only when something writes tells you the step
    has run and produced output."""
    from library.tools.project_layout import STEPS

    ProjectLayout(tmp_path).ensure()
    steps_root = tmp_path / "pipeline_output" / "steps"
    assert not list(steps_root.iterdir()), (
        "ensure() should not pre-create step directories")


def test_a_step_directory_appears_when_the_step_writes(tmp_path):
    """write_dir creates the step directory on demand."""
    layout = ProjectLayout(tmp_path)
    layout.ensure()
    steps_root = tmp_path / "pipeline_output" / "steps"
    assert not list(steps_root.iterdir())
    # Writing to an area creates just that step's directory.
    layout.write_dir(Area.PROSODY, step="prosody_analysis")
    on_disk = [p.name for p in steps_root.iterdir()]
    assert on_disk == ["1_05_prosody_analysis"]


# The two places where sorting by step number is not run order. The DAG
# runs 2.06 before 2.05 and 5.04 before 5.03. Numbering by DAG position
# instead would renumber every later directory whenever a step is
# inserted, and would stop matching the "step 1.04" vocabulary the docs
# and the code comments already share - so the inversions are accepted
# and stated, and the README renders true run order.
KNOWN_SORT_INVERSIONS = {
    "2_05_mesh_spine", "2_06_music_analysis",
    "5_03_creative_cohesion", "5_04_compile_manifest",
}


def test_the_step_directories_sort_into_pipeline_order(tmp_path):
    from library.tools.project_layout import STEPS

    layout = ProjectLayout(tmp_path)
    layout.ensure()
    # Simulate what happens after all steps run: each step's directory
    # is created when the step writes its output.
    for step in STEPS:
        layout.step_dir(step.node_id, create=True)
    listing = sorted(p.name for p in
                     (tmp_path / "pipeline_output" / "steps").iterdir())
    run_order = [s.dirname for s in STEPS]
    diverging = {a for a, b in zip(listing, run_order) if a != b}
    assert diverging <= KNOWN_SORT_INVERSIONS, (
        f"the listing diverges from run order beyond the two known "
        f"inversions: {sorted(diverging - KNOWN_SORT_INVERSIONS)}")


def test_every_area_has_a_purpose_sentence():
    for area, spec in AREAS.items():
        assert spec.purpose.strip(), f"{area.value} has no purpose"
        assert spec.purpose.strip().endswith("."), (
            f"{area.value}'s purpose should be a sentence")


def test_no_two_areas_claim_the_same_directory():
    seen = {}
    for area, spec in AREAS.items():
        if spec.relpath == ".":
            continue
        assert spec.relpath not in seen, (
            f"{area.value} and {seen[spec.relpath]} both claim "
            f"{spec.relpath}")
        seen[spec.relpath] = area.value


def test_the_input_areas_are_the_captains_material():
    """`external_state` is here for the same reason `raw` is: the
    captain made it and no step may write it. It carries state produced
    outside the pipeline and offered to a step that would otherwise need
    the step that makes it - see library/tools/external_inputs.py."""
    inputs = {a.value for a, s in AREAS.items() if s.kind is Kind.INPUT}
    assert inputs == {
        "project_root", "raw", "music", "assets",
        "brand_assets", "compositions", "external_state",
    }


# ── Backups ─────────────────────────────────────────────────────────

def _state(tmp_path, marker):
    ProjectLayout(tmp_path).pipeline_data_path.write_text(
        json.dumps({"marker": marker}), encoding="utf-8")


def test_a_backup_is_a_copy_of_the_state_as_it_stood(tmp_path):
    _state(tmp_path, "before")
    layout = ProjectLayout(tmp_path)
    made = layout.backup_pipeline_data(label="run")
    _state(tmp_path, "after")
    assert json.loads(made.read_text(encoding="utf-8"))["marker"] == "before"


def test_nothing_to_back_up_is_not_an_error(tmp_path):
    assert ProjectLayout(tmp_path).backup_pipeline_data() is None


def test_the_backup_store_is_bounded(tmp_path):
    _state(tmp_path, "x")
    layout = ProjectLayout(tmp_path)
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


def test_the_backup_store_lives_under_the_output_root(tmp_path):
    _state(tmp_path, "x")
    made = ProjectLayout(tmp_path).backup_pipeline_data()
    assert made.parent.parent.parent.name == "pipeline_output"
    assert made.parent.name == "pipeline_data"
    assert not list(tmp_path.glob("*.bak*")), (
        "backups do not sit beside the thing they back up")


def test_a_backup_is_named_so_the_pruner_can_recognise_it(tmp_path):
    _state(tmp_path, "x")
    made = ProjectLayout(tmp_path).backup_pipeline_data(
        label="pre trans rerun", now=1_700_000_000)
    assert made.name.startswith("pipeline_data.")
    assert made.name.endswith(".pre-trans-rerun.json")
    assert ProjectLayout(tmp_path).automatic_backups() == [made]


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


# ── Nothing else may own a project path ─────────────────────────────

def test_the_project_config_schema_delegates_rather_than_deciding(tmp_path):
    """Two definitions of "where the exports go" is one too many."""
    from library.schemas.project_config import ProjectConfig

    cfg = ProjectConfig(name="x", slug="x")
    object.__setattr__(cfg, "_project_root", Path(tmp_path))
    layout = ProjectLayout(tmp_path)
    assert cfg.raw_dir == layout.read_dir(Area.RAW)
    assert cfg.pipeline_output_dir == layout.read_dir(Area.OUTPUT_ROOT)
    assert cfg.exports_dir == layout.read_dir(Area.EXPORTS)
    assert cfg.pipeline_data_path == layout.pipeline_data_path


def test_paths_py_forwards_the_project_helpers_it_still_publishes(tmp_path):
    from library.tools import paths

    layout = ProjectLayout(tmp_path)
    assert paths.project_output_dir(str(tmp_path)) == layout.read_dir(
        Area.OUTPUT_ROOT)
    assert paths.comp_dir(str(tmp_path)) == layout.read_dir(Area.FUSION_COMPS)


def test_the_new_project_scaffold_is_the_layout(tmp_path):
    """The scaffold drifted from the steps once. It cannot again."""
    from library.tools.project_registry import create_project

    create_project("scaffold-test", name="Scaffold Test", root=tmp_path)
    root = tmp_path / "scaffold-test"
    # The containers and raw/ exist.
    assert (root / "pipeline_output").is_dir()
    assert (root / "pipeline_output" / "steps").is_dir()
    assert (root / "README-LAYOUT.md").is_file()
    assert (root / "raw").is_dir()
    # Only raw/ is scaffolded on the input side.  music/, assets/,
    # brand_assets/ and compositions/ appear when the captain puts
    # material there.
    assert not (root / "music").exists()
    assert not (root / "assets").exists()
    assert not (root / "brand_assets").exists()
    assert not (root / "compositions").exists()
    # Step dirs and project-level areas are NOT pre-created.
    steps_root = root / "pipeline_output" / "steps"
    assert not list(steps_root.iterdir()), (
        "new project should not have pre-created step directories")


def test_the_timed_text_render_dirname_is_the_layouts(tmp_path):
    """Two names for one directory is the bug class this replaces."""
    from library.tools.timed_text_render import TIMED_TEXT_RENDER_DIRNAME

    assert TIMED_TEXT_RENDER_DIRNAME == Path(
        AREAS[Area.TIMED_TEXT_SEGMENTS].relpath).name


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
        get_all_gate_statuses, get_gate_status,
        load_gate_feedback, load_gate_snapshot,
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
    for label, dirs in sources.items():
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
