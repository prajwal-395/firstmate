"""The preflight/edit split, and the three things it has to guarantee.

1. Resetting the edit run is STRUCTURALLY INCAPABLE of discarding
   enrichment.  Not "does not happen to" - there is no code path from
   ``reset_stage(state, EDIT, ...)`` to the preflight ledger, the
   preflight outputs, or the per-clip artifacts on disk.
2. A per-clip re-run request re-runs exactly one clip.
3. Replaced footage invalidates exactly its own stale analysis.

Everything here runs on a handful of one-byte files and a stubbed
indexer.  No pipeline run, no model, no render.
"""

import json
import os
from pathlib import Path

import pytest

from library.processes.edit_video import run_pipeline as runner
from library.tools import footage_identity, step_ledger
from library.tools.project_layout import Area, ProjectLayout

PILOT_ROOT = Path(__file__).resolve().parents[1]
STEPS_ROOT = PILOT_ROOT / "library" / "steps"


# ── Fixtures ─────────────────────────────────────────────────────────

def _manifest(step_dir_name):
    with open(STEPS_ROOT / step_dir_name / "manifest.json") as f:
        return json.load(f)


# The three preflight steps that own per-clip artifacts, plus the two that
# describe the footage set, plus one edit step - keyed by DAG node id.
MANIFESTS = {
    "scan": _manifest("step_1_01_scan_project"),
    "catalog": _manifest("step_1_02_catalog_footage"),
    "semantic_analysis": _manifest("step_1_03_semantic_analysis"),
    "temporal_index": _manifest("step_1_04_temporal_index"),
    "prosody_analysis": _manifest("step_1_05_prosody_analysis"),
    "creative_direction": _manifest("step_2_01_creative_direction"),
    "music_analysis": _manifest("step_2_06_music_analysis"),
    "compile_manifest": _manifest("step_5_04_compile_manifest"),
    "render": _manifest("step_6_01_render"),
}
STAGE_BY_NODE = {node: step_ledger.stage_of(m, node)
                 for node, m in MANIFESTS.items()}


@pytest.fixture
def project(tmp_path):
    """A project with three clips and the artifacts preflight would leave."""
    root = tmp_path / "proj"
    raw = root / "raw"
    raw.mkdir(parents=True)
    names = ["b_second.mov", "c_third.mov", "a_first.mov"]
    for i, name in enumerate(names):
        (raw / name).write_bytes(b"video-bytes-" + bytes([65 + i]) * 8)

    files, _skipped = footage_identity.enumerate_footage(str(root))
    # sorted by path: a_first -> clip_001, b_second -> clip_002, c_third -> clip_003
    assert [e["filename"] for e in files] == ["a_first.mov", "b_second.mov",
                                              "c_third.mov"]

    _write_preflight_artifacts(root, files)

    state = {
        "project_folder": str(root),
        step_ledger.LEDGER_KEY[step_ledger.PREFLIGHT]: {
            node: {"completed_at": "2026-08-20T10:00:00", "elapsed_s": 2400}
            for node, stage in STAGE_BY_NODE.items()
            if stage == step_ledger.PREFLIGHT
        },
        step_ledger.LEDGER_KEY[step_ledger.EDIT]: {
            node: {"completed_at": "2026-08-20T11:00:00", "elapsed_s": 3}
            for node, stage in STAGE_BY_NODE.items()
            if stage == step_ledger.EDIT
        },
        "step_outputs": {node: {"payload": node} for node in STAGE_BY_NODE},
        step_ledger.SOURCE_FINGERPRINTS_KEY:
            footage_identity.fingerprints_for(files),
    }
    return root, state, files


def _index_document(clip_id):
    """The shape step 1.04 writes, minus the megabytes of real signal."""
    return {
        "clip_id": clip_id,
        "scene_boundaries": [{"time": 0.0, "score": 1.0, "type": "start"}],
        "speech_regions": [{"start": 0.5, "end": 1.5, "words": []}],
        "energy_curve": {"peak_times": []},
        "audio_events": [],
        "motion_energy": {"high_motion_times": []},
    }


def _write_preflight_artifacts(root, files):
    """Everything the preflight steps declare, one file per clip."""
    for entry in files:
        clip_id = entry["clip_id"]
        stem = Path(entry["path"]).stem
        for node in ("semantic_analysis", "temporal_index", "prosody_analysis"):
            patterns = step_ledger.per_clip_artifacts(MANIFESTS[node])
            for path in step_ledger.artifact_paths(str(root), patterns,
                                                   clip_id, stem):
                os.makedirs(os.path.dirname(path), exist_ok=True)
                if path.endswith(".json"):
                    Path(path).write_text(json.dumps(_index_document(clip_id)))
                else:
                    Path(path).write_bytes(b"cached-" + clip_id.encode())


# ── 1. The split, and what it is incapable of ────────────────────────

def test_every_step_declares_a_stage():
    """No step may sit outside the split, and none may sit in both."""
    for step_dir in sorted(STEPS_ROOT.iterdir()):
        manifest_path = step_dir / "manifest.json"
        if not manifest_path.exists():
            continue
        with open(manifest_path) as f:
            manifest = json.load(f)
        stage = step_ledger.stage_of(manifest, step_dir.name)
        assert stage in step_ledger.STAGES


def test_the_two_arguable_steps_are_on_the_recorded_side():
    """0.01 validates a SHARED library; 2.06 enriches a CHOSEN asset.

    Both are edit-stage, and both are the kind of call a future reader
    will want to re-litigate - so the reasoning is in
    library/tools/step_ledger.py and the outcome is asserted here.
    """
    assert step_ledger.stage_of(_manifest("step_0_01_validate_sfx_library")) \
        == step_ledger.EDIT
    assert step_ledger.stage_of(_manifest("step_2_06_music_analysis")) \
        == step_ledger.EDIT
    # And the whole of the preflight stage really is the footage work.
    for name in ("step_1_01_scan_project", "step_1_02_catalog_footage",
                 "step_1_03_semantic_analysis", "step_1_04_temporal_index",
                 "step_1_05_prosody_analysis", "step_1_06_object_segmentation",
                 "step_1_07_ocr_extraction"):
        assert step_ledger.stage_of(_manifest(name)) == step_ledger.PREFLIGHT


def test_stage_is_required_not_defaulted():
    with pytest.raises(step_ledger.LedgerError):
        step_ledger.stage_of({"classification": {}}, "nameless")
    with pytest.raises(step_ledger.LedgerError):
        step_ledger.stage_of({"classification": {"stage": "phase_1"}}, "wrong")


def test_resetting_the_edit_run_cannot_discard_enrichment(project):
    """THE done-check: reset the edit run, every preflight entry survives."""
    root, state, _files = project

    preflight_before = dict(state[step_ledger.LEDGER_KEY[step_ledger.PREFLIGHT]])
    outputs_before = {n: state["step_outputs"][n] for n, s in STAGE_BY_NODE.items()
                      if s == step_ledger.PREFLIGHT}
    artifacts_before = sorted(
        str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())

    cleared = step_ledger.reset_stage(state, step_ledger.EDIT, STAGE_BY_NODE)

    # Every edit step is gone from its ledger and its output discarded.
    assert sorted(cleared) == sorted(
        n for n, s in STAGE_BY_NODE.items() if s == step_ledger.EDIT)
    assert state[step_ledger.LEDGER_KEY[step_ledger.EDIT]] == {}
    for node in cleared:
        assert node not in state["step_outputs"]

    # Every preflight entry, output and artifact is untouched.
    assert state[step_ledger.LEDGER_KEY[step_ledger.PREFLIGHT]] == preflight_before
    for node, output in outputs_before.items():
        assert state["step_outputs"][node] == output
    artifacts_after = sorted(
        str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())
    assert artifacts_after == artifacts_before


def test_a_preflight_failure_does_not_re_arm_the_expensive_work(project):
    """A failed step loses its ledger entry and keeps its artifacts.

    That is what makes the re-run cost the remainder rather than the lot -
    the ledger is bookkeeping, the per-clip files are the cache.
    """
    root, state, _files = project
    index_dir = ProjectLayout(root).read_dir(Area.TEMPORAL_INDEX)
    before = sorted(p.name for p in index_dir.iterdir())

    runner._record_step_failure(state, "temporal_index", "whisperx blew up")

    assert not step_ledger.is_completed(state, "temporal_index")
    assert "temporal_index" in state["failed_steps"]
    assert sorted(p.name for p in index_dir.iterdir()) == before


# ── 2. Re-run requests, at step and per-clip granularity ─────────────

def test_rerun_one_clip_removes_exactly_that_clips_artifacts(project):
    root, state, _files = project
    index_dir = ProjectLayout(root).read_dir(Area.TEMPORAL_INDEX)
    assert sorted(p.name for p in index_dir.iterdir()) == [
        "clip_001.json", "clip_002.json", "clip_003.json"]

    runner.apply_rerun_requests(str(root), state,
                                ["temporal_index:clip_002"],
                                STAGE_BY_NODE, MANIFESTS)

    assert sorted(p.name for p in index_dir.iterdir()) == [
        "clip_001.json", "clip_003.json"]
    # Only temporal_index is re-armed; the other preflight steps keep both
    # their ledger entry and every profile they wrote.
    assert not step_ledger.is_completed(state, "temporal_index")
    assert step_ledger.is_completed(state, "semantic_analysis")
    assert step_ledger.is_completed(state, "prosody_analysis")
    assert len(list(ProjectLayout(root).read_dir(Area.PROSODY).iterdir())) == 3


def test_rerun_one_clip_re_indexes_exactly_that_clip(project, monkeypatch):
    """The bookkeeping and the step agree: one clip in, one clip indexed."""
    root, state, files = project
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "temporal_index_step",
        STEPS_ROOT / "step_1_04_temporal_index" / "step.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    runner.apply_rerun_requests(str(root), state,
                                ["temporal_index:clip_002"],
                                STAGE_BY_NODE, MANIFESTS)

    indexed = []

    def fake_index_clip(video_path, clip_id, layout, whisper_model_size):
        indexed.append(clip_id)
        return {
            "clip_id": clip_id,
            "scene_boundaries": [],
            "speech_regions": [],
            "energy_curve": {"peak_times": []},
            "audio_events": [],
            "motion_energy": {"high_motion_times": []},
        }

    monkeypatch.setattr(module, "index_clip", fake_index_clip)
    result = module.build_temporal_index(
        files, ProjectLayout(root), "large-v3")

    assert indexed == ["clip_002"], (
        "only the clip whose artifact was removed may be re-indexed")
    assert result["total_reused"] == 2
    assert result["total_indexed"] == 3
    assert result["total_failed"] == 0


def test_rerun_a_whole_step_removes_every_clip(project):
    root, state, _files = project
    runner.apply_rerun_requests(str(root), state, ["temporal_index"],
                                STAGE_BY_NODE, MANIFESTS)
    index_dir = ProjectLayout(root).read_dir(Area.TEMPORAL_INDEX)
    assert list(index_dir.iterdir()) == []
    assert "temporal_index" not in state["step_outputs"]


def test_rerun_the_edit_stage_is_the_reset(project):
    root, state, _files = project
    runner.apply_rerun_requests(str(root), state, ["edit"],
                                STAGE_BY_NODE, MANIFESTS)
    assert state[step_ledger.LEDGER_KEY[step_ledger.EDIT]] == {}
    assert set(state[step_ledger.LEDGER_KEY[step_ledger.PREFLIGHT]]) == {
        n for n, s in STAGE_BY_NODE.items() if s == step_ledger.PREFLIGHT}
    assert (ProjectLayout(root).read_path(
        Area.TEMPORAL_INDEX, "clip_001.json")).exists()


def test_a_rerun_target_that_names_nothing_raises(project):
    root, state, _files = project
    for bad in ["temporal_indx", "temporal_index:", "phase_1",
                "temporal_index:clip_099"]:
        with pytest.raises(step_ledger.LedgerError):
            runner.apply_rerun_requests(str(root), state, [bad],
                                        STAGE_BY_NODE, MANIFESTS)


def test_per_clip_rerun_is_refused_where_there_are_no_per_clip_artifacts(project):
    root, state, _files = project
    with pytest.raises(step_ledger.LedgerError) as excinfo:
        runner.apply_rerun_requests(str(root), state, ["catalog:clip_001"],
                                    STAGE_BY_NODE, MANIFESTS)
    assert "per_clip_artifacts" in str(excinfo.value)


# ── 3. Source identity ───────────────────────────────────────────────

def test_replacing_one_clip_invalidates_only_its_own_analysis(project):
    root, state, _files = project
    replaced = root / "raw" / "b_second.mov"     # clip_002
    replaced.write_bytes(b"completely different footage entirely")

    delta = runner.apply_source_identity(str(root), state, STAGE_BY_NODE,
                                         MANIFESTS)

    assert delta.changed == ["clip_002"]
    assert delta.stale_clip_ids == ["clip_002"]

    for node, area, names in [
        ("temporal_index", Area.TEMPORAL_INDEX,
         ["clip_001.json", "clip_003.json"]),
        ("prosody_analysis", Area.PROSODY,
         ["clip_001_prosody.json", "clip_003_prosody.json"]),
    ]:
        on_disk = sorted(p.name
                         for p in ProjectLayout(root).read_dir(area).iterdir())
        assert on_disk == names, f"{node} kept the wrong clips"

    # Vision profiles are keyed by file STEM, not clip id, so the
    # translation has to work or the wrong profile is deleted.
    profiles = sorted(p.name for p in
                      ProjectLayout(root).read_dir(Area.VISION_ANALYSIS).iterdir())
    assert profiles == [
        "clip_profile_a_first.json",
        "clip_profile_a_first_v3.json",
        "clip_profile_a_first_video_only.json",
        "clip_profile_c_third.json",
        "clip_profile_c_third_v3.json",
        "clip_profile_c_third_video_only.json",
    ]

    # Every preflight step re-runs (they all have to re-emit their output),
    # and the edit ledger is untouched by an identity check.
    for node, stage in STAGE_BY_NODE.items():
        if stage == step_ledger.PREFLIGHT:
            assert not step_ledger.is_completed(state, node), node
        else:
            assert step_ledger.is_completed(state, node), node


def test_a_touched_but_unchanged_file_invalidates_nothing(project):
    """The false positive this check must never produce.

    A restore, a `cp` without -p, a sync client or a backup tool moves
    mtime without touching a byte. Identity is content, so none of them
    costs forty minutes of WhisperX. See library/tools/footage_identity.py.
    """
    root, state, files = project
    for entry in files:
        os.utime(entry["path"], (1_600_000_000, 1_600_000_000))
    before = sorted(str(p.relative_to(root)) for p in root.rglob("*")
                    if p.is_file())

    delta = runner.apply_source_identity(str(root), state, STAGE_BY_NODE,
                                         MANIFESTS)

    assert not delta.footage_changed
    assert sorted(str(p.relative_to(root)) for p in root.rglob("*")
                  if p.is_file()) == before
    for node in STAGE_BY_NODE:
        assert step_ledger.is_completed(state, node), node


def test_same_length_different_content_is_caught(project):
    """Size alone is not identity: a same-size replacement is a replacement."""
    root, state, _files = project
    original = (root / "raw" / "b_second.mov").read_bytes()
    (root / "raw" / "b_second.mov").write_bytes(b"X" * len(original))

    delta = runner.apply_source_identity(str(root), state, STAGE_BY_NODE,
                                         MANIFESTS)

    assert delta.changed == ["clip_002"]


def test_a_record_without_a_digest_is_not_read_as_a_change(project):
    """Upgrading an older state file must not wipe the whole project."""
    root, state, _files = project
    for entry in state[step_ledger.SOURCE_FINGERPRINTS_KEY].values():
        entry.pop("content_digest")

    delta = runner.apply_source_identity(str(root), state, STAGE_BY_NODE,
                                         MANIFESTS)

    assert not delta.footage_changed
    assert (ProjectLayout(root).read_path(
        Area.TEMPORAL_INDEX, "clip_001.json")).exists()


def test_untouched_footage_invalidates_nothing(project):
    root, state, _files = project
    before = sorted(str(p.relative_to(root)) for p in root.rglob("*")
                    if p.is_file())

    delta = runner.apply_source_identity(str(root), state, STAGE_BY_NODE,
                                         MANIFESTS)

    assert delta is not None and not delta.footage_changed
    assert sorted(str(p.relative_to(root)) for p in root.rglob("*")
                  if p.is_file()) == before
    for node in STAGE_BY_NODE:
        assert step_ledger.is_completed(state, node), node


def test_added_footage_renumbers_clips_and_the_check_notices(project):
    """Adding a file that sorts first shifts every clip id after it."""
    root, state, _files = project
    (root / "raw" / "0_new.mov").write_bytes(b"brand new footage here")

    delta = runner.apply_source_identity(str(root), state, STAGE_BY_NODE,
                                         MANIFESTS)

    # 0_new takes clip_001, so all three original clips move up one and
    # every id now points at a different file.
    assert delta.stale_clip_ids == ["clip_001", "clip_002", "clip_003",
                                    "clip_004"]
    assert list(ProjectLayout(root).read_dir(Area.TEMPORAL_INDEX).iterdir()) == []
    assert not step_ledger.is_completed(state, "scan")
    assert not step_ledger.is_completed(state, "catalog")


def test_an_empty_raw_directory_refuses_to_invalidate(project):
    """An unmounted volume must not cost the project its enrichment."""
    root, state, files = project
    for entry in files:
        os.remove(entry["path"])

    assert runner.apply_source_identity(str(root), state, STAGE_BY_NODE,
                                        MANIFESTS) is None

    assert (ProjectLayout(root).read_path(
        Area.TEMPORAL_INDEX, "clip_001.json")).exists()
    for node in STAGE_BY_NODE:
        assert step_ledger.is_completed(state, node), node


def test_a_project_with_no_recorded_fingerprints_adopts_rather_than_wipes(project):
    """First run under the new bookkeeping must not invalidate anything."""
    root, state, _files = project
    state.pop(step_ledger.SOURCE_FINGERPRINTS_KEY)
    before = sorted(str(p.relative_to(root)) for p in root.rglob("*")
                    if p.is_file())

    assert runner.apply_source_identity(str(root), state, STAGE_BY_NODE,
                                        MANIFESTS) is None

    assert sorted(str(p.relative_to(root)) for p in root.rglob("*")
                  if p.is_file()) == before
    assert set(state[step_ledger.SOURCE_FINGERPRINTS_KEY]) == {
        "clip_001", "clip_002", "clip_003"}
    for node in STAGE_BY_NODE:
        assert step_ledger.is_completed(state, node), node


# ── Migration off the single flat ledger ─────────────────────────────

def test_a_pre_split_state_file_migrates_into_the_two_ledgers():
    state = {
        "steps_completed": {
            "temporal_index": {"elapsed_s": 2400},
            "creative_direction": {"elapsed_s": 12},
            "a_step_this_dag_does_not_have": {"elapsed_s": 1},
        },
    }
    migrated = step_ledger.migrate_legacy(state, STAGE_BY_NODE)

    assert migrated == ["creative_direction", "temporal_index"]
    assert "temporal_index" in state[step_ledger.LEDGER_KEY[step_ledger.PREFLIGHT]]
    assert "creative_direction" in state[step_ledger.LEDGER_KEY[step_ledger.EDIT]]
    # An entry for a step this pipeline no longer has is kept, not guessed
    # at and not deleted - it is still somebody's record of work done.
    assert state["steps_completed"] == {
        "a_step_this_dag_does_not_have": {"elapsed_s": 1}}
    assert "a_step_this_dag_does_not_have" in step_ledger.all_completed(state)


def test_migration_is_idempotent():
    state = {"steps_completed": {"temporal_index": {"elapsed_s": 2400}}}
    step_ledger.migrate_legacy(state, STAGE_BY_NODE)
    assert step_ledger.migrate_legacy(state, STAGE_BY_NODE) == []
    assert step_ledger.all_completed(state) == {
        "temporal_index": {"elapsed_s": 2400}}


# ── The declared per-clip artifacts are real declarations ────────────

def test_declared_per_clip_artifacts_name_an_area_and_a_clip(tmp_path):
    """A pattern spells no directory of its own.

    They used to. When the layout moved the vision profiles out of
    `raw/analysis/`, steps 1.03 and 1.07 were left declaring the old
    path - so `--rerun semantic_analysis:clip_007` deleted nothing and
    re-ran nothing, silently. The prefix is the layout's to state and
    only the filename is the step's.
    """
    for step_dir in sorted(STEPS_ROOT.iterdir()):
        manifest_path = step_dir / "manifest.json"
        if not manifest_path.exists():
            continue
        with open(manifest_path) as f:
            manifest = json.load(f)
        for pattern in step_ledger.per_clip_artifacts(manifest):
            assert not os.path.isabs(pattern), \
                f"{step_dir.name}: {pattern} must be project-relative"
            assert pattern.startswith("{area:"), (
                f"{step_dir.name}: {pattern} spells its own directory. Name "
                f"an area - see library/tools/project_layout.py.")
            assert "{clip_id}" in pattern or "{stem}" in pattern, \
                f"{step_dir.name}: {pattern} names no clip"
            # {area:...}, {clip_id} and {stem} are the only placeholders
            # the runner can fill; anything else renders a literal brace.
            rendered, = step_ledger.artifact_paths(
                str(tmp_path), [pattern], "clip_001", "IMG_1806")
            assert "{" not in rendered and "}" not in rendered


def test_a_declared_area_that_does_not_exist_raises(tmp_path):
    with pytest.raises(step_ledger.LedgerError):
        step_ledger.artifact_paths(
            str(tmp_path), ["{area:somewhere_else}/{clip_id}.json"], "clip_001")


def test_every_declared_area_resolves_inside_the_declaring_steps_directory(tmp_path):
    """A step's per-clip artifacts belong in that step's directory."""
    layout = ProjectLayout(tmp_path)
    for node, manifest in MANIFESTS.items():
        for pattern in step_ledger.per_clip_artifacts(manifest):
            rendered, = step_ledger.artifact_paths(
                str(tmp_path), [pattern], "clip_001", "IMG_1806")
            assert layout.step_of(rendered) == node, (
                f"{node} declares {pattern}, which lands in "
                f"{layout.step_of(rendered)!r}'s directory")


# ── 4. Code identity ────────────────────────────────────────────────

from library.tools import code_identity


def test_step_code_hash_is_deterministic(tmp_path):
    """The same files produce the same hash every time."""
    step_dir = tmp_path / "step_1_99_test"
    step_dir.mkdir()
    (step_dir / "step.py").write_text("print('hello')")
    (step_dir / "manifest.json").write_text('{"id": "test"}')

    h1 = code_identity.step_code_hash(str(step_dir))
    h2 = code_identity.step_code_hash(str(step_dir))
    assert h1 is not None
    assert h1 == h2


def test_step_code_hash_changes_on_code_edit(tmp_path):
    """Editing a .py file produces a different hash."""
    step_dir = tmp_path / "step_1_99_test"
    step_dir.mkdir()
    (step_dir / "step.py").write_text("print('hello')")
    (step_dir / "manifest.json").write_text('{"id": "test"}')

    h_before = code_identity.step_code_hash(str(step_dir))
    (step_dir / "step.py").write_text("print('hello, world')")
    h_after = code_identity.step_code_hash(str(step_dir))

    assert h_before != h_after


def test_step_code_hash_changes_on_manifest_edit(tmp_path):
    """Editing a manifest.json file produces a different hash."""
    step_dir = tmp_path / "step_1_99_test"
    step_dir.mkdir()
    (step_dir / "step.py").write_text("print('hello')")
    (step_dir / "manifest.json").write_text('{"id": "test"}')

    h_before = code_identity.step_code_hash(str(step_dir))
    (step_dir / "manifest.json").write_text('{"id": "test", "v": 2}')
    h_after = code_identity.step_code_hash(str(step_dir))

    assert h_before != h_after


def test_step_code_hash_ignores_md_files(tmp_path):
    """Editing a .md file does NOT change the hash - prose is not code."""
    step_dir = tmp_path / "step_1_99_test"
    step_dir.mkdir()
    (step_dir / "step.py").write_text("print('hello')")
    (step_dir / "manifest.json").write_text('{"id": "test"}')
    (step_dir / "handoff.md").write_text("# Original")

    h_before = code_identity.step_code_hash(str(step_dir))
    (step_dir / "handoff.md").write_text("# Rewritten entirely")
    h_after = code_identity.step_code_hash(str(step_dir))

    assert h_before == h_after


def test_step_code_hash_ignores_pycache(tmp_path):
    """__pycache__ directories do not affect the hash."""
    step_dir = tmp_path / "step_1_99_test"
    step_dir.mkdir()
    (step_dir / "step.py").write_text("print('hello')")

    h_before = code_identity.step_code_hash(str(step_dir))
    pycache = step_dir / "__pycache__"
    pycache.mkdir()
    (pycache / "step.cpython-312.pyc").write_bytes(b"\x00\x00bytecode")
    h_after = code_identity.step_code_hash(str(step_dir))

    assert h_before == h_after


def test_changed_code_invalidates_preflight_cache(tmp_path, monkeypatch):
    """THE done-check for this fix: cache a value, change the code, the
    cached value is NOT reused.

    This is the defect that prompted this entire change - three commits
    of work read a path that never fired because the cache survived a
    code fix.
    """
    # Create fake step directories with source files.
    steps_root = tmp_path / "steps"
    scan_dir = steps_root / "step_1_01_scan_project"
    scan_dir.mkdir(parents=True)
    (scan_dir / "step.py").write_text("# original scan code")
    (scan_dir / "manifest.json").write_text('{"id": "scan"}')

    temporal_dir = steps_root / "step_1_04_temporal_index"
    temporal_dir.mkdir(parents=True)
    (temporal_dir / "step.py").write_text("# original temporal code")
    (temporal_dir / "manifest.json").write_text('{"id": "temporal_index"}')

    # Build nodes dict that get_step_dir can resolve.
    fake_nodes = {
        "scan": {"step_ref": "steps/step_1_01_scan_project"},
        "temporal_index": {"step_ref": "steps/step_1_04_temporal_index"},
        "creative_direction": {"step_ref": "steps/step_2_01_creative_direction"},
    }
    stage_by_node = {
        "scan": step_ledger.PREFLIGHT,
        "temporal_index": step_ledger.PREFLIGHT,
        "creative_direction": step_ledger.EDIT,
    }
    manifests = {
        "scan": MANIFESTS["scan"],
        "temporal_index": MANIFESTS["temporal_index"],
        "creative_direction": MANIFESTS["creative_direction"],
    }

    # Monkey-patch LIBRARY_ROOT to use our temp directory.
    monkeypatch.setattr(runner, "LIBRARY_ROOT", steps_root.parent)

    # State with completed preflight steps.
    state = {
        step_ledger.LEDGER_KEY[step_ledger.PREFLIGHT]: {
            "scan": {"completed_at": "2026-08-20T10:00:00"},
            "temporal_index": {"completed_at": "2026-08-20T10:00:00"},
        },
        step_ledger.LEDGER_KEY[step_ledger.EDIT]: {
            "creative_direction": {"completed_at": "2026-08-20T11:00:00"},
        },
    }

    # First run: adopt current hashes.
    result = runner.apply_code_identity(
        state, stage_by_node, manifests, fake_nodes)
    assert result == []  # nothing invalidated on adoption
    assert step_ledger.is_completed(state, "scan")
    assert step_ledger.is_completed(state, "temporal_index")
    assert step_ledger.CODE_FINGERPRINTS_KEY in state

    # Now change the scan step's code.
    (scan_dir / "step.py").write_text("# FIXED scan code with new field")

    # Second run: scan should be invalidated, temporal_index should survive.
    result = runner.apply_code_identity(
        state, stage_by_node, manifests, fake_nodes)
    assert "scan" in result, "changed code must invalidate the cache"
    assert "temporal_index" not in result, (
        "unchanged code must NOT invalidate the cache")

    assert not step_ledger.is_completed(state, "scan"), (
        "scan must be removed from the ledger after code change")
    assert step_ledger.is_completed(state, "temporal_index"), (
        "temporal_index must remain completed - its code did not change")
    assert step_ledger.is_completed(state, "creative_direction"), (
        "edit steps must be untouched by code identity checks")


def test_unchanged_code_still_hits_cache(tmp_path, monkeypatch):
    """The cache still works for unchanged code - this is NOT a disabled
    cache with extra steps.

    Without this test passing alongside the invalidation test, the fix
    would be indistinguishable from simply disabling the cache, which
    costs 69 minutes of cold vision analysis per run.
    """
    steps_root = tmp_path / "steps"
    scan_dir = steps_root / "step_1_01_scan_project"
    scan_dir.mkdir(parents=True)
    (scan_dir / "step.py").write_text("# scan code")
    (scan_dir / "manifest.json").write_text('{"id": "scan"}')

    fake_nodes = {"scan": {"step_ref": "steps/step_1_01_scan_project"}}
    stage_by_node = {"scan": step_ledger.PREFLIGHT}
    manifests = {"scan": MANIFESTS["scan"]}

    monkeypatch.setattr(runner, "LIBRARY_ROOT", steps_root.parent)

    state = {
        step_ledger.LEDGER_KEY[step_ledger.PREFLIGHT]: {
            "scan": {"completed_at": "2026-08-20T10:00:00"},
        },
    }

    # First run: adopt.
    runner.apply_code_identity(state, stage_by_node, manifests, fake_nodes)
    assert step_ledger.is_completed(state, "scan")

    # Second run: same code, cache should survive.
    result = runner.apply_code_identity(
        state, stage_by_node, manifests, fake_nodes)
    assert result == [], "unchanged code must not invalidate"
    assert step_ledger.is_completed(state, "scan"), (
        "the cache must still work for unchanged code")

    # Third run: still the same code, still cached.
    result = runner.apply_code_identity(
        state, stage_by_node, manifests, fake_nodes)
    assert result == []
    assert step_ledger.is_completed(state, "scan")


def test_first_encounter_adopts_without_invalidating(tmp_path, monkeypatch):
    """A project that has never carried code hashes must not be wiped.

    This matches the adoption pattern apply_source_identity uses for
    footage fingerprints on first encounter.
    """
    steps_root = tmp_path / "steps"
    scan_dir = steps_root / "step_1_01_scan_project"
    scan_dir.mkdir(parents=True)
    (scan_dir / "step.py").write_text("# scan code")
    (scan_dir / "manifest.json").write_text('{"id": "scan"}')

    fake_nodes = {"scan": {"step_ref": "steps/step_1_01_scan_project"}}
    stage_by_node = {"scan": step_ledger.PREFLIGHT}
    manifests = {"scan": MANIFESTS["scan"]}

    monkeypatch.setattr(runner, "LIBRARY_ROOT", steps_root.parent)

    state = {
        step_ledger.LEDGER_KEY[step_ledger.PREFLIGHT]: {
            "scan": {"completed_at": "2026-08-20T10:00:00"},
        },
        # No CODE_FINGERPRINTS_KEY - simulates pre-upgrade state.
    }

    result = runner.apply_code_identity(
        state, stage_by_node, manifests, fake_nodes)
    assert result == [], "first encounter must adopt, not invalidate"
    assert step_ledger.is_completed(state, "scan"), (
        "the step must remain completed on first encounter")
    assert step_ledger.CODE_FINGERPRINTS_KEY in state, (
        "hashes must be recorded for next run")


def test_code_identity_does_not_touch_edit_steps(tmp_path, monkeypatch):
    """Edit steps are not checked or invalidated by code identity."""
    steps_root = tmp_path / "steps"
    edit_dir = steps_root / "step_2_01_creative_direction"
    edit_dir.mkdir(parents=True)
    (edit_dir / "step.py").write_text("# creative code")
    (edit_dir / "manifest.json").write_text('{"id": "creative_direction"}')

    fake_nodes = {
        "creative_direction": {
            "step_ref": "steps/step_2_01_creative_direction"
        },
    }
    stage_by_node = {"creative_direction": step_ledger.EDIT}
    manifests = {"creative_direction": MANIFESTS["creative_direction"]}

    monkeypatch.setattr(runner, "LIBRARY_ROOT", steps_root.parent)

    state = {
        step_ledger.LEDGER_KEY[step_ledger.EDIT]: {
            "creative_direction": {"completed_at": "2026-08-20T11:00:00"},
        },
        step_ledger.CODE_FINGERPRINTS_KEY: {},
    }

    result = runner.apply_code_identity(
        state, stage_by_node, manifests, fake_nodes)
    assert result == []
    assert step_ledger.is_completed(state, "creative_direction")


def test_real_step_directories_produce_a_hash():
    """Every real preflight step directory hashes without error."""
    preflight_steps = [
        "step_1_01_scan_project",
        "step_1_02_catalog_footage",
        "step_1_03_semantic_analysis",
        "step_1_04_temporal_index",
        "step_1_05_prosody_analysis",
    ]
    for name in preflight_steps:
        step_dir = STEPS_ROOT / name
        h = code_identity.step_code_hash(str(step_dir))
        assert h is not None, f"{name} produced no hash"
        assert len(h) == 64, f"{name} hash is not SHA-256"

