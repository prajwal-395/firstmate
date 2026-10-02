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
from __future__ import annotations
import json
import os
from pathlib import Path
import pytest
from library.tools import capability_outputs
from library.processes.edit_video import run_pipeline as runner
from library.tools import footage_identity, step_ledger
from library.tools.project_layout import Area, ProjectLayout
from library.tools import capability_outputs as co
from library.tools import captain_edits, edit_ledger
from library.tools.edit_ledger import EditLedgerError
from library.tools import ledger_pr_body
import sys
import importlib
import threading
import time
from library.tools import perf_ledger


PILOT_ROOT = Path(__file__).resolve().parents[3]
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
    return _make_project(tmp_path / "proj")


def _make_project(root):
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


def test_resetting_the_edit_run_cannot_discard_enrichment(project):
    """THE done-check: reset the edit run, every preflight entry survives."""
    root, state, _files = project

    preflight_before = dict(state[step_ledger.LEDGER_KEY[step_ledger.PREFLIGHT]])
    outputs_before = {n: capability_outputs.node_output(state, n)
                      for n, s in STAGE_BY_NODE.items()
                      if s == step_ledger.PREFLIGHT}
    artifacts_before = sorted(
        str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())

    cleared = step_ledger.reset_stage(state, step_ledger.EDIT, STAGE_BY_NODE)

    # Every edit step is gone from its ledger and its output discarded.
    assert sorted(cleared) == sorted(
        n for n, s in STAGE_BY_NODE.items() if s == step_ledger.EDIT)
    assert state[step_ledger.LEDGER_KEY[step_ledger.EDIT]] == {}
    for node in cleared:
        assert node not in capability_outputs.node_outputs(state)

    # Every preflight entry, output and artifact is untouched.
    assert state[step_ledger.LEDGER_KEY[step_ledger.PREFLIGHT]] == preflight_before
    for node, output in outputs_before.items():
        assert capability_outputs.node_output(state, node) == output
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

    def fake_index_clip(video_path, clip_id, layout, whisper_model_size,
                        language="en", pretranscribed=None):
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


def test_source_identity_never_wipes_what_it_cannot_be_sure_of(tmp_path):
    """Three cases that must cost nothing:

    * a touched-but-unchanged file (a restore, a `cp` without -p, a sync
      client) moves mtime without a byte - identity is content, so none
      of them costs forty minutes of WhisperX;
    * an empty raw directory (an unmounted volume) refuses to invalidate;
    * a project with no recorded fingerprints adopts rather than wipes.
    """
    def files_under(root):
        return sorted(str(p.relative_to(root)) for p in root.rglob("*")
                      if p.is_file())

    root, state, files = _make_project(tmp_path / "touched")
    for entry in files:
        os.utime(entry["path"], (1_600_000_000, 1_600_000_000))
    before = files_under(root)
    delta = runner.apply_source_identity(str(root), state, STAGE_BY_NODE,
                                         MANIFESTS)
    assert not delta.footage_changed
    assert files_under(root) == before
    for node in STAGE_BY_NODE:
        assert step_ledger.is_completed(state, node), node

    root, state, files = _make_project(tmp_path / "unmounted")
    for entry in files:
        os.remove(entry["path"])
    assert runner.apply_source_identity(str(root), state, STAGE_BY_NODE,
                                        MANIFESTS) is None
    assert (ProjectLayout(root).read_path(
        Area.TEMPORAL_INDEX, "clip_001.json")).exists()
    for node in STAGE_BY_NODE:
        assert step_ledger.is_completed(state, node), node

    root, state, _files = _make_project(tmp_path / "unrecorded")
    state.pop(step_ledger.SOURCE_FINGERPRINTS_KEY)
    before = files_under(root)
    assert runner.apply_source_identity(str(root), state, STAGE_BY_NODE,
                                        MANIFESTS) is None
    assert files_under(root) == before
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


# ── 4. Code identity ────────────────────────────────────────────────

from library.tools import code_identity


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

    # A manifest edit is a code change too.
    h_before = code_identity.step_code_hash(str(temporal_dir))
    (temporal_dir / "manifest.json").write_text('{"id": "temporal_index", "v": 2}')
    assert code_identity.step_code_hash(str(temporal_dir)) != h_before


# ── 5. Shared implementation files are part of the identity (D1) ──

def _scratch_repo_with_semantic_analysis(tmp_path):
    """A scratch repo tree where step 1.03 executes a shared script.

    Mirrors the real layout - ``library/steps/<dir>`` plus
    ``library/tools/analysis/<script>`` - so ``apply_code_identity``
    resolves the declared implementation file under the scratch root
    instead of the real one.
    """
    lib = tmp_path / "library"
    step_dir = lib / "steps" / "step_1_03_semantic_analysis"
    step_dir.mkdir(parents=True)
    (step_dir / "step.py").write_text("# launcher: runs vision_pipeline_v3.py")
    (step_dir / "manifest.json").write_text('{"id": "semantic_analysis"}')
    shared = lib / "tools" / "analysis" / "vision_pipeline_v3.py"
    shared.parent.mkdir(parents=True)
    shared.write_text("# original measurement code")
    return lib, step_dir, shared


def test_shared_implementation_change_invalidates_preflight_cache(
        tmp_path, monkeypatch):
    """D1: a fix to shared measurement code invalidates the step that
    executes it.

    Step 1.03's directory holds a launcher; the algorithm lives in
    ``vision_pipeline_v3.py``.  Editing that file must invalidate the
    cached ``semantic_analysis`` output, while editing an unrelated
    shared file must not.
    """
    lib, _step_dir, shared = _scratch_repo_with_semantic_analysis(tmp_path)
    monkeypatch.setattr(runner, "LIBRARY_ROOT", lib)

    fake_nodes = {
        "semantic_analysis": {
            "step_ref": "steps/step_1_03_semantic_analysis"},
    }
    stage_by_node = {"semantic_analysis": step_ledger.PREFLIGHT}
    manifests = {"semantic_analysis": MANIFESTS["semantic_analysis"]}
    state = {
        step_ledger.LEDGER_KEY[step_ledger.PREFLIGHT]: {
            "semantic_analysis": {"completed_at": "2026-08-20T10:00:00"},
        },
    }

    # First run: adopt current hashes, cache survives.
    assert runner.apply_code_identity(
        state, stage_by_node, manifests, fake_nodes) == []
    assert step_ledger.is_completed(state, "semantic_analysis")

    # An unrelated shared file changes: the cache must survive.
    (lib / "tools" / "analysis" / "other_pipeline.py").write_text("# v1")
    (lib / "tools" / "analysis" / "other_pipeline.py").write_text("# v2")
    assert runner.apply_code_identity(
        state, stage_by_node, manifests, fake_nodes) == []
    assert step_ledger.is_completed(state, "semantic_analysis")

    # The executed measurement changes: the cache must go.
    shared.write_text("# FIXED measurement code with a new gate")
    result = runner.apply_code_identity(
        state, stage_by_node, manifests, fake_nodes)
    assert result == ["semantic_analysis"], (
        "a change to the shared measurement code must invalidate "
        "the step that executes it")
    assert not step_ledger.is_completed(state, "semantic_analysis")


def _strip_prose(text):
    """Remove triple-quoted strings and #-comments for reference scanning.

    The scanner below must see code references, not prose: a docstring
    mentioning a module is not a dependency on it.
    """
    import re
    text = re.sub(r'"""[\s\S]*?"""', "", text)
    text = re.sub(r"'''[\s\S]*?'''", "", text)
    return "\n".join(
        line.split("#", 1)[0] for line in text.splitlines())


def _resolve_dotted(base, names, root):
    """Repo files a ``from library... import ...`` line can mean.

    A name that resolves to its own file wins (``from
    library.tools.analysis import picture_quality`` is picture_quality.py,
    not the package ``__init__``); otherwise the base itself answers.
    Only files that exist on disk are returned.
    """
    found = []
    base_path = root / Path(*base.split("."))
    for name in names:
        name = name.strip()
        if not name or name == "*":
            continue
        candidate = base_path / (name.split(" as ")[0].strip() + ".py")
        if candidate.is_file():
            found.append(candidate)
    if found:
        return found
    for candidate in (base_path.with_suffix(".py"),
                      base_path / "__init__.py"):
        if candidate.is_file():
            return [candidate]
    return []


def _referenced_repo_files(source_path, root):
    """Every ``library/**/*.py`` file one source file references as code.

    Two shapes: ``library.tools...`` imports, and ``library/tools/...``
    path literals including ``os.path.join(PILOT_ROOT, 'library',
    'tools', ...)`` constructions.  Returns repo-relative posix paths.
    """
    import re
    text = _strip_prose(Path(source_path).read_text(encoding="utf-8"))
    refs = set()

    for match in re.finditer(
            r"^\s*from\s+(library\.[\w.]+)\s+import\s+(.+)$", text,
            re.MULTILINE):
        for found in _resolve_dotted(match.group(1),
                                     match.group(2).split(","), root):
            refs.add(found.relative_to(root).as_posix())
    for match in re.finditer(
            r"^\s*import\s+(library\.[\w.]+(?:\s*,\s*library\.[\w.]+)*)",
            text, re.MULTILINE):
        for base in re.findall(r"library\.[\w.]+", match.group(1)):
            for found in _resolve_dotted(base, [], root):
                refs.add(found.relative_to(root).as_posix())

    for match in re.finditer(r"library/tools/[\w\-/]+\.py", text):
        candidate = root / match.group(0)
        if candidate.is_file():
            refs.add(match.group(0))
    # os.path.join(PILOT_ROOT, 'library', 'tools', 'analysis', 'x.py')
    for match in re.finditer(
            r"['\"]library['\"]\s*,\s*['\"]tools['\"]"
            r"((?:\s*,\s*['\"][\w\-.]+['\"])+)", text):
        segments = re.findall(r"['\"]([\w\-.]+)['\"]", match.group(1))
        candidate = root / "library" / "tools" / Path(*segments)
        if candidate.suffix == ".py" and candidate.is_file():
            refs.add(candidate.relative_to(root).as_posix())

    return refs


def _dag_preflight_step_dirs():
    """{node_id: step dir} for every DAG node staged as preflight."""
    dag = json.loads(
        (PILOT_ROOT / "library" / "processes" / "edit_video"
         / "dag.json").read_text(encoding="utf-8"))
    out = {}
    for node in dag.get("nodes", []):
        node_id = node.get("id", "")
        step_ref = node.get("step_ref", "")
        if not node_id or not step_ref:
            continue
        manifest_path = (
            PILOT_ROOT / "library" / step_ref / "manifest.json")
        if not manifest_path.is_file():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if step_ledger.stage_of(manifest, node_id) == step_ledger.PREFLIGHT:
            out[node_id] = PILOT_ROOT / "library" / step_ref
    return out


def test_preflight_shared_code_references_are_declared():
    """No DAG-preflight step may reference shared measurement code the
    identity check does not watch - that gap is D1.

    For each preflight step, every repo file its step.py references as
    code (minus plumbing in EXEMPT_IMPORTS) must be declared in
    STEP_IMPLEMENTATION_DEPS, and every declared file must be referenced
    either by the step or by another declared file - so the map can
    neither miss the next shared script nor rot into over-invalidation.
    Every declared file must exist.
    """
    deps = getattr(code_identity, "STEP_IMPLEMENTATION_DEPS", {})
    exempt = getattr(code_identity, "EXEMPT_IMPORTS", set())
    assert deps, (
        "no step declares shared implementation files, so a fix to "
        "shared measurement code is invisible to the preflight cache")

    for node_id, step_dir in sorted(_dag_preflight_step_dirs().items()):
        step_py = Path(step_dir) / "step.py"
        assert step_py.is_file(), f"{node_id} has no step.py to scan"
        declared = set(deps.get(Path(step_dir).name, ()))
        for rel in sorted(declared):
            assert (PILOT_ROOT / rel).is_file(), (
                f"{node_id} declares {rel}, which does not exist")

        required = (_referenced_repo_files(step_py, PILOT_ROOT) - set(exempt))
        for rel in sorted(declared):
            required |= (
                _referenced_repo_files(PILOT_ROOT / rel, PILOT_ROOT)
                - set(exempt))
        assert required == declared, (
            f"{node_id}: referenced-but-undeclared {sorted(required - declared)} "
            f"would survive a code fix in cache; "
            f"declared-but-unreferenced {sorted(declared - required)} "
            f"would invalidate good cache on unrelated edits")


def test_a_node_run_completes_its_project_capabilities_only():
    """The ledgers are keyed by node; reading them by capability must
    not credit a region splice or a touch-up with a node run that never
    executed it, and an entry naming its operation completes only that
    capability."""
    state = {step_ledger.LEDGER_KEY[step_ledger.EDIT]: {
        "plan_subtitles": {}, "build_reels": {}},
        step_ledger.LEDGER_KEY[step_ledger.PREFLIGHT]: {
        "temporal_index": {"operation": "transcript.reindex"}}}
    done = step_ledger.completed_capabilities(state)
    assert {"subtitles.plan", "reel.build"} <= done
    assert not {"subtitles.splice", "reel.touchup", "temporal.index"} & done
    assert "transcript.reindex" in done


# --------------------------------------------------------------------------
# From test_capability_outputs.py
#
# A node run is recorded under its capabilities and nothing is lost:
# a record must never outlive the output it was split from (a rerun, a
# revised gate or a cleared stage that left it behind would serve the old
# state to every reader), and a project recorded before the records still
# reads (`library/tools/capability_outputs.py`).

def test_a_node_record_is_split_by_what_each_capability_produces():
    state = {}
    co.record(state, "mesh_spine", {"audio_spine": {"v": 1},
                                    "timed_spine": {"v": 1},
                                    "duration_zone": {"z": 1}})
    assert state[co.KEY]["spine.mesh"] == {"audio_spine": {"v": 1},
                                           "timed_spine": {"v": 1}}
    assert state[co.KEY]["duration_zone.build"] == {"duration_zone": {"z": 1}}
    assert co.value(state, "spine.mesh", "audio_spine") == {"v": 1}


def test_a_key_no_capability_declares_is_kept_with_the_run():
    """A pre-bridge's table or a model answer the step returns beside its
    result: the node view must still carry it (an edge may read it)."""
    state = {}
    co.record(state, "review_rough_cut", {"rough_cut_review": {"ok": 1},
                                          "cut_decisions": [1]})
    assert co.node_output(state, "review_rough_cut") == {
        "rough_cut_review": {"ok": 1}, "cut_decisions": [1]}


def test_a_rerecorded_node_leaves_no_stale_capability_record():
    state = {}
    co.record(state, "mesh_spine", {"audio_spine": {"v": 1},
                                    "duration_zone": {"z": 1}})
    co.record(state, "mesh_spine", {"audio_spine": {"v": 2}})
    assert co.value(state, "spine.mesh", "audio_spine") == {"v": 2}
    assert "duration_zone.build" not in state[co.KEY]


def test_a_forgotten_node_is_gone():
    state = {"step_outputs": {"mesh_spine": {"audio_spine": {"v": 0}}}}
    co.record(state, "mesh_spine", {"audio_spine": {"v": 1}})
    co.forget(state, "mesh_spine")
    assert "spine.mesh" not in state[co.KEY]
    assert co.value(state, "spine.mesh", "audio_spine") is None


def test_a_project_recorded_before_the_key_still_reads():
    state = {"step_outputs": {"mesh_spine": {"audio_spine": {"v": 0}}}}
    assert co.value(state, "spine.mesh", "audio_spine") == {"v": 0}


def test_a_write_carries_the_old_slot_over_as_records():
    """The next write migrates: no legacy key survives it, and what the
    slot held for other nodes is still read."""
    state = {"step_outputs": {"scan": {"total_files": 3},
                              "mesh_spine": {"audio_spine": {"v": 0}}}}
    co.record(state, "mesh_spine", {"audio_spine": {"v": 1}})
    assert co.LEGACY_KEY not in state
    assert co.value(state, "footage.scan", "total_files") == 3
    assert co.value(state, "spine.mesh", "audio_spine") == {"v": 1}


# --------------------------------------------------------------------------
# From test_edit_ledger.py
#
# The edit ledger: the K3 paint-over defect, caught per row.
#
# Cluster K3 (`data/vep-ren-execution-frontier/report.md`): hands exist
# but live outside the plan - resolve-axi can isolate voice and set a
# LUT, and the next build paints every one of those over, because no
# declared store carries them. Each test here names the defect it
# catches: a row that cannot be recorded, cannot be replayed, survives
# a spine change, or vanishes silently.

def _project(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    ProjectLayout(str(project)).ensure()
    return project


def _isolate(track=1, amount=60, reel="Reel 09 - hook",
             stated_by="requester",
             reason="remove the background noise from the host mic"):
    return {"op": "voice_isolation", "anchor": {"kind": "reel"},
            "reel": reel, "params": {"track": track, "amount": amount},
            "stated_by": stated_by, "reason": reason}


def _lut(phrase="ive quit every single day", reel="Reel 09 - hook",
         lut="Film Looks/Kodak 2383", node=1):
    return {"op": "clip_lut",
            "anchor": {"kind": "words", "phrase": phrase},
            "reel": reel, "params": {"lut": lut, "node": node},
            "stated_by": "requester",
            "reason": "kodak print feel on the hook"}


_GRADE_PROVENANCE = {
    "source": "Resolve Color page export",
    "authorised_by": "captain",
    "licence": "captain's own asset",
}


def _transcript(words, start=10.0, step=0.4):
    segs = []
    cursor = start
    for word in words:
        segs.append({"word": word, "start": cursor, "end": cursor + 0.3,
                     "timed": True})
        cursor += step
    return {"segments": [{"words": segs}]}


def test_releveling_hands_edits_replaces_stale_values(tmp_path):
    """Re-leveling the same isolation track or LUT node must replace the
    old value; otherwise a rebuild replays stale decisions first."""
    project = _project(tmp_path)
    edit_ledger.record_row(str(project), _isolate(amount=60))
    edit_ledger.record_row(
        str(project), _lut(lut="Film Looks/Kodak 2383"))
    isolation80 = _isolate(amount=80)
    lut_revised = _lut(lut="Film Looks/Print 2383 Warm")
    _, action = edit_ledger.record_row(str(project), isolation80)
    assert action == "superseded"
    _, action = edit_ledger.record_row(str(project), lut_revised)
    assert action == "superseded"
    rows = edit_ledger.load_rows(str(project))
    assert isolation80 in rows
    assert lut_revised in rows
    assert _isolate(amount=60) not in rows
    assert _lut(lut="Film Looks/Kodak 2383") not in rows
    with pytest.raises(EditLedgerError, match="already in force"):
        edit_ledger.record_row(str(project), lut_revised)


def test_one_reels_rows_revert_alone(tmp_path):
    """Per-reel scoping (E2): dropping Reel 09's rows leaves Reel 28's
    intact - one reel's edits revert alone."""
    from library.tools.declaration_keys import edit_declaration

    project = _project(tmp_path)
    edit_ledger.record_row(str(project), _isolate(reel="Reel 09 - hook"))
    edit_ledger.record_row(str(project), _isolate(reel="Reel 28 - nail"))
    with edit_declaration(str(project), "edit_ledger") as entries:
        for key in [k for k, row in entries.items()
                    if row.get("reel", "").startswith("Reel 09")]:
            del entries[key]
    rows = edit_ledger.load_rows(str(project))
    assert [r["reel"] for r in rows] == ["Reel 28 - nail"]
    scoped = edit_ledger.rows_for_reel(rows, "Reel 28 - nail (rebuild)")
    assert len(scoped) == 1
    assert edit_ledger.rows_for_reel(rows, "Reel 09 - hook") == []


def test_editing_one_reel_invalidates_only_its_build_digest():
    """The other reel's build must not be invalidated by one reel's
    ledger row, while the edited reel must rebuild to replay that row."""
    from library.tools import reel_rebuild_need as _need

    kwargs = dict(engine_code="eng", project_wide="wide",
                  plan_content_hash="plan", transcript_hash="tx",
                  master_digest="m", ranges=[(0.0, 5.0)],
                  placements_list=[], cards=[], caption_segments=[],
                  explainer_segments=[], semantic_segments=[],
                  overlay_placements=[], motion_record={}, ending={},
                  look={}, grade_cdl={}, grade_look={}, power_grade={})
    bare = _need.derivation_digest(reel_number=9, **kwargs)
    row = _isolate()
    one = _need.derivation_digest(reel_number=9, extra={"edit_ledger": [
        row]}, **kwargs)
    other = _need.derivation_digest(
        reel_number=28, extra={"edit_ledger": [row]}, **kwargs)
    assert one != bare
    assert one != other
    assert _need.derivation_digest(reel_number=9, extra={}, **kwargs) \
        == bare
    camera = {
        "op": "angle_plan", "anchor": {"kind": "reel"},
        "reel": "Reel 09 - hook",
        "params": {"camera": "Akshita", "min_shot_seconds": 2,
                   "lead_frames": 0},
        "stated_by": "requester", "reason": "show the host",
    }
    with_angle = _need.derivation_digest(
        reel_number=9, extra={"edit_ledger": [row, camera]}, **kwargs)
    assert with_angle != one


# ── The merged view: one store, every existing applier ───────────────

def test_recorded_plan_edits_reach_the_existing_replayers(tmp_path):
    """A hand-recorded framing hold must reach captain_edits' applier,
    or the next spine build paints it over (the K3 defect)."""
    project = _project(tmp_path)
    hold = {"op": "transform_override",
            "anchor": {"kind": "words", "phrase": "akshitas line"},
            "params": {"property": "Pan", "value": -8.75,
                        "recorded_draw_gain": 4.0},
            "stated_by": "captain", "reason": "hand move in inspector"}
    edit_ledger.record_row(str(project), hold)
    edit_ledger.record_row(str(project), _isolate())
    carrier = {"op": "angle_plan",
               "anchor": {"kind": "words", "phrase": "akshitas line"},
               "reel": "Reel 09 - hook",
               "params": {"camera": "close-up",
                          "min_shot_seconds": 3, "lead_frames": 12},
               "stated_by": "requester", "reason": "cut to the speaker"}
    edit_ledger.record_row(str(project), carrier)
    edits = captain_edits.load_edits(str(project))
    assert len(edits) == 1
    assert edits[0]["kind"] == "transform_override"
    assert edits[0]["property"] == "Pan"
    assert edits[0]["recorded_draw_gain"] == pytest.approx(4.0)
    captain_edits.validate_edits(edits)


# ── Replay onto fakes: the paint-over, closed ────────────────────────

class _Graph:
    def __init__(self, nodes=2):
        self.nodes = nodes
        self.luts = {}

    def GetNumNodes(self):
        return self.nodes

    def SetLUT(self, node, lut):
        self.luts[node] = lut
        return True

    def GetLUT(self, node):
        return self.luts.get(node, "")

    def ApplyGradeFromDRX(self, path, _mode):
        self.drx = path
        return True


class _Item:
    _MISSING = object()

    def __init__(self, graph=_MISSING):
        self._graph = _Graph() if graph is _Item._MISSING else graph

    def GetNodeGraph(self):
        return self._graph


class _Timeline:
    def __init__(self, audio_tracks=2):
        self.audio_tracks = audio_tracks
        self.isolation = {}

    def GetTrackCount(self, kind):
        assert kind == "audio"
        return self.audio_tracks

    def SetVoiceIsolationState(self, track, state):
        self.isolation[track] = dict(state)
        return True

    def GetVoiceIsolationState(self, track):
        return dict(self.isolation.get(track, {}))


def _spans():
    return [{"master": (10.0, 20.0)}, {"master": (20.0, 30.0)}]


def _speech():
    return _transcript(
        ["hello", "there", "ive", "quit", "every", "single", "day",
         "for", "years", "and", "back", "again"])


def test_k3_hands_edits_replay_with_resolve_readback(tmp_path):
    """The MX2.1/C1.3 edits must survive a rebuild, with Resolve
    read-back proving the track isolation and speaking-clip LUT."""
    timeline = _Timeline()
    items = [_Item(), _Item()]
    report = edit_ledger.replay_on_timeline(
        "Reel 09 - hook", [_isolate(), _lut()], _spans(), _speech(),
        timeline, item_for_span=items.__getitem__,
        reel_name="Reel 09 - hook")
    assert report["unreplayable"] == []
    assert len(report["applied"]) == 2
    assert timeline.GetVoiceIsolationState(1) == {"isEnabled": True,
                                                 "amount": 60}
    assert items[0].GetNodeGraph().GetLUT(1) == "Film Looks/Kodak 2383"


def test_grade_row_replays_lut_on_each_picture_span():
    """A declared reel grade must be read by the rebuilt timeline, or
    a grade plan is painted over even though the ledger kept the row."""
    row = {"op": "grade", "anchor": {"kind": "reel"},
           "params": {"lut": "Film Looks/Kodak 2383", "node": 1},
           "stated_by": "requester", "reason": "print grade"}
    timeline = _Timeline()
    items = [_Item(), _Item()]
    report = edit_ledger.replay_on_timeline(
        "Reel 09 - hook", [row], _spans(), _speech(), timeline,
        item_for_span=items.__getitem__, reel_name="Reel 09 - hook")
    assert report["unreplayable"] == []
    assert len(report["applied"]) == 2
    assert all(item.GetNodeGraph().GetLUT(1) ==
               "Film Looks/Kodak 2383" for item in items)
    # Resolve's accepted write is not proof that pixels changed.
    assert all(applied["pixel_verification"] == edit_ledger.PIXELS_UNMEASURED
               for applied in report["applied"])


def test_power_grade_ledger_row_requires_authorisation(tmp_path):
    """A ledger row must use the same provenance gate as the project
    grade declaration, or it bypasses the captain's .drx ruling."""
    project = _project(tmp_path)
    path = project / "Podcast.drx"
    path.write_bytes(b"DRX")
    row = {"op": "grade", "anchor": {"kind": "reel"},
           "params": {"drx": "Podcast.drx"},
           "stated_by": "requester", "reason": "podcast look"}

    with pytest.raises(EditLedgerError, match="without provenance"):
        edit_ledger.record_row(str(project), row)
    assert edit_ledger.load_rows(str(project)) == []

    row["params"]["provenance"] = dict(_GRADE_PROVENANCE)
    stored, action = edit_ledger.record_row(str(project), row)
    assert action == "recorded"
    assert stored["params"]["drx"] == str(path)
    assert edit_ledger.load_rows(str(project))[0]["params"]["drx"] \
        == str(path)

    # An authorised path that is gone stops before Resolve creates a
    # reel with the declared look missing.
    gone = {"op": "grade", "anchor": {"kind": "reel"},
            "params": {"drx": "gone.drx",
                       "provenance": dict(_GRADE_PROVENANCE)},
            "stated_by": "requester", "reason": "other look"}
    with pytest.raises(EditLedgerError, match="not on disk"):
        edit_ledger.record_row(str(project), gone)
    assert len(edit_ledger.load_rows(str(project))) == 1


def test_power_grade_row_requires_node_graph_readback():
    """A hand-set PowerGrade survives only when the rebuilt item reads
    back the grade nodes; a True API return is not enough."""
    row = {"op": "grade", "anchor": {"kind": "reel"},
           "params": {"drx": "/looks/Podcast.drx",
                      "provenance": dict(_GRADE_PROVENANCE)},
           "stated_by": "requester", "reason": "podcast look"}
    timeline = _Timeline()
    item = _Item()
    report = edit_ledger.replay_on_timeline(
        "Reel 09 - hook", [row], _spans(), _speech(), timeline,
        item_for_span=lambda _index: item, reel_name="Reel 09 - hook")
    assert report["unreplayable"] == []
    assert len(report["applied"]) == 2
    assert all(row["write_readback"] == "fresh node graph read-back"
               for row in report["applied"])
    assert all(row["pixel_verification"] == edit_ledger.PIXELS_UNMEASURED
               for row in report["applied"])
    assert item.GetNodeGraph().drx == "/looks/Podcast.drx"


def test_grade_row_refuses_a_parameter_the_replayer_ignores():
    row = {"op": "grade", "anchor": {"kind": "reel"},
           "params": {"lut": "Film Looks/Kodak 2383", "mix": 50},
           "stated_by": "requester", "reason": "half strength"}

    with pytest.raises(EditLedgerError, match="does not read"):
        edit_ledger.validate_rows([row])


def test_word_anchor_survives_a_spine_change():
    """The version-control ruling, proved: the spine re-times around
    the words (same speech, new seconds) and the row still grades the
    clip speaking them - because it holds words, not frames."""
    timeline = _Timeline()
    items = [_Item(), _Item()]
    before = _speech()
    after = _transcript(
        ["hello", "there", "ive", "quit", "every", "single", "day",
         "for", "years", "and", "back", "again"],
        start=48.0, step=0.5)
    moved_spans = [{"master": (48.0, 58.0)}, {"master": (58.0, 68.0)}]
    row = _lut(reel="Reel 09")
    first = edit_ledger.replay_on_timeline(
        "Reel 09", [row], _spans(), before, timeline,
        item_for_span=items.__getitem__, reel_name="Reel 09")
    second = edit_ledger.replay_on_timeline(
        "Reel 09", [row], moved_spans, after, timeline,
        item_for_span=items.__getitem__, reel_name="Reel 09")
    assert first["unreplayable"] == []
    assert second["unreplayable"] == []
    assert items[0].GetNodeGraph().GetLUT(1) == "Film Looks/Kodak 2383"


def test_unreplayable_rows_are_reported_by_name(tmp_path, capsys):
    """A row the build cannot replay - no such track, no such node,
    anchor spoken nowhere, carrier with no replayer yet - is REPORTED
    BY NAME, never dropped silently (the K3 defect back again)."""
    timeline = _Timeline(audio_tracks=1)
    rows = [_isolate(track=4),
            _lut(node=9),
            _lut(phrase="words nobody ever spoke"),
            _retime("ive quit", 80)]
    report = edit_ledger.replay_on_timeline(
        "Reel 09 - hook", rows, _spans(), _speech(), timeline,
        item_for_span=lambda _i: _Item(),
        reel_name="Reel 09 - hook")
    assert report["applied"] == []
    # The retime is no carrier: it shaped the keep ranges before
    # placement (`rate_ranges`), so replay neither applies nor loses it.
    assert len(report["unreplayable"]) == 3
    names = " ".join(r["name"] for r in report["unreplayable"])
    assert "voice_isolation" in names
    assert "clip_lut" in names
    assert "retime" not in names
    out = capsys.readouterr().err
    assert out.count("UNREPLAYABLE LEDGER ROW") == 3

    # A clip with no node graph cannot hold a LUT - named, not claimed.
    report = edit_ledger.replay_on_timeline(
        "Reel 09", [_lut(reel="Reel 09")], _spans(), _speech(), _Timeline(),
        item_for_span=lambda _i: _Item(graph=None),
        reel_name="Reel 09")
    assert len(report["unreplayable"]) == 1
    assert "node graph" in report["unreplayable"][0]["reason"]


def _retime(phrase, percent, reel="Reel 09 - hook", **params):
    return {"op": "retime", "reel": reel,
            "anchor": {"kind": "words", "phrase": phrase},
            "params": {"percent": percent, **params},
            "stated_by": "requester", "reason": "pace the passage"}


def test_retime_is_durable_intent_on_the_keep_ranges():
    """Punch list 10: "make this passage 110% speed" re-derives on every
    rebuild - the passage, from its first word to its last, plays at
    its percent on the reel clock, inside its range (no new seam), and
    the placer lays it down at that rate with contiguous records."""
    from types import SimpleNamespace

    from library.tools import reel_clock
    from library.tools.reel_build import placements

    ranges, applied, lost = edit_ledger.rate_ranges(
        [(10.0, 15.0)], [_retime("quit every single day", 110)],
        _speech(), "Reel 09 - hook")
    assert lost == []
    assert applied[0]["passages"] == [[11.2, 12.7]]
    assert len(ranges) == 1 and tuple(ranges[0]) == (10.0, 15.0)
    assert [pytest.approx(piece) for piece in reel_clock.pieces(
        ranges[0])] == [(10.0, 11.2, 1.0), (11.2, 12.7, 1.1),
                        (12.7, 15.0, 1.0)]
    assert reel_clock.played_seconds(ranges[0]) == pytest.approx(
        1.2 + 1.5 / 1.1 + 2.3)

    clip = SimpleNamespace(timeline_start=0.0, timeline_end=60.0,
                           source_in=100.0, track_index=1, speaker="A",
                           track_type="video", source_file="/a.mov")
    placed = placements(ranges, [clip], 24.0)
    assert [p.get("rate", 1.0) for p in placed] == [1.0, 1.1, 1.0]
    middle = placed[1]
    assert middle["source_in"] == pytest.approx(111.2)
    assert round((middle["source_out"] - middle["source_in"]) * 24) == 36
    assert middle["record_frames"] == 33
    assert placed[2]["snapped_record"] == (middle["snapped_record"]
                                           + middle["record_frames"])
    assert (placed[2]["snapped_record"]
            + round((placed[2]["source_out"] - placed[2]["source_in"])
                    * 24)) == reel_clock.played_frames(ranges[0], 24.0)


def test_retime_reports_what_it_cannot_honour():
    """A retime the reel cannot play is reported by name and leaves the
    ranges alone; two retimes over one stretch of speech refuse."""
    ranges = [(10.0, 15.0)]
    rows = [_retime("words nobody ever spoke", 120),
            _retime("there ive", 120),
            _retime("and back", 90, segments=[{"percent": 90}])]
    rated, applied, lost = edit_ledger.rate_ranges(
        ranges, rows, _speech(), "Reel 09 - hook")
    assert rated == ranges and applied == []
    reasons = " ".join(record["reason"] for record in lost)
    assert "does not play those words" in reasons
    assert "'hello'" in reasons
    assert "stepped retime" in reasons
    with pytest.raises(EditLedgerError, match="one stretch of speech"):
        edit_ledger.rate_ranges(
            ranges, [_retime("quit every", 110), _retime("every single", 90)],
            _speech(), "Reel 09 - hook")


def test_anchor_typo_fails_at_record_not_next_build(tmp_path):
    """A word anchor the measured transcript never speaks is refused
    at record time (the captain_edits typo rule) - never stored to
    fail silently on the next build."""
    from library.tools import captain_edits as _edits

    project = _project(tmp_path)
    tpath = _edits.transcript_path(str(project))
    tpath.parent.mkdir(parents=True, exist_ok=True)
    tpath.write_text(json.dumps(_transcript(["hello", "there"])),
                     encoding="utf-8")
    row = _lut(phrase="words nobody ever spoke")
    with pytest.raises(Exception, match="spoken nowhere"):
        edit_ledger.record_row(str(project), row)
    assert edit_ledger.load_rows(str(project)) == []


# --------------------------------------------------------------------------
# From test_ledger_pr_body.py
#
# PR enumeration from the edit ledger: each test names its defect.
#
# The verb (`library/tools/ledger_pr_body.py`) replaces the hand-written
# 134-card BEFORE/AFTER body of the 09-18 captions fix. A rendering that
# drifts from that shape, drops rows under a filter, or loses a new op
# to a raw dump sends the correction round trip back.

def _caption(phrase, replacement, reel=None):
    row = {"op": "caption_fix",
           "anchor": {"kind": "words", "phrase": phrase},
           "params": {"replacement": replacement},
           "stated_by": "model",
           "reason": "acronyms read uppercase"}
    if reel is not None:
        row["reel"] = reel
    return row


def test_caption_fix_renders_before_after_item_for_item():
    """A drifted rendering (wrong quotes, swapped sides, missing
    arrow) would not match the hand-written 1209 body it replaces,
    and the worker would hand-edit every line - the enumeration
    saving nothing."""
    row = _caption("seo two point oh", "SEO 2.0",
                   reel="Reel 01 - geo-is-comprehension-not-position")
    text = ledger_pr_body.render_enumeration([row])
    assert "### Reel 01 - geo-is-comprehension-not-position (1)" in text
    assert "- BEFORE 'seo two point oh'  ->  AFTER 'SEO 2.0'" in text
    apostrophe = _caption("the link's bio.", "the link's in our bio.")
    line = ledger_pr_body.render_row(apostrophe)
    assert line == ('- BEFORE "the link\'s bio."  ->  '
                    'AFTER "the link\'s in our bio."')


def test_reel_filter_keeps_unscoped_rows():
    """An unscoped row holds on EVERY reel, so a `--reel` filter that
    dropped it would silently lose a decision from the pasted body -
    the reader approving a change that is narrower than what ships."""
    scoped = _caption("ai sees you", "AI sees you", reel="Reel 02 - x")
    unscoped = _caption("the links bio", "the link's in our bio")
    text = ledger_pr_body.render_enumeration(
        [scoped, unscoped], reel_prefixes=("Reel 02",))
    assert "AI sees you" in text
    assert "in our bio" in text
    assert "### Every reel in scope (1)" in text


def test_every_known_op_has_a_dedicated_rendering():
    """A new op added to the ledger vocabulary with no branch here
    would reach the PR body only as a raw params dump - reviewable
    in name only. This fails the moment one does."""
    samples = {
        "voice_isolation": {
            "anchor": {"kind": "reel"}, "reel": "Reel 09 - hook",
            "params": {"track": 1, "amount": 60}},
        "clip_lut": {
            "anchor": {"kind": "words", "phrase": "the hook"},
            "reel": "Reel 09 - hook",
            "params": {"lut": "Film Looks/Kodak 2383", "node": 1}},
        "transform_override": {
            "anchor": {"kind": "words", "phrase": "the hook"},
            "params": {"property": "Pan", "value": 12}},
        "span_retime": {
            "anchor": {"kind": "words", "phrase": "the hook"},
            "params": {"edge": "head"}},
        "drop_fragment": {
            "anchor": {"kind": "words", "phrase": "the aside"}},
        "caption_fix": {
            "anchor": {"kind": "words", "phrase": "seo team"},
            "params": {"replacement": "SEO team"}},
        "redraw_closer": {
            "anchor": {"kind": "words", "phrase": "come back tomorrow"},
            "params": {"from_phrase": "see you soon"}},
        "retime": {
            "anchor": {"kind": "words", "phrase": "the hook"},
            "params": {"percent": 110}},
        "grade": {
            "anchor": {"kind": "words", "phrase": "the hook"},
            "params": {"lut": "Film Looks/Kodak 2383"}},
        "angle_plan": {
            "anchor": {"kind": "words", "phrase": "the hook"},
            "params": {"camera": "wide"}},
        "plan_change": {
            "anchor": {"kind": "reel"},
            "params": {"operation_type": "transition",
                       "owner": "plan_transitions",
                       "values": {"duration": {
                           "value": 12, "unit": "frames",
                           "stated_by": "requester"}}}},
    }
    assert set(samples) == set(edit_ledger.OPS)
    for op, partial in samples.items():
        row = {"op": op, "stated_by": "requester",
               "reason": "the requester's words", **partial}
        line = ledger_pr_body.render_row(row)
        assert not line.startswith(f"- {op}:"), \
            f"{op} fell through to the raw-dump fallback"


def test_malformed_ledger_refuses_instead_of_pasting_half(tmp_path):
    """A ledger the reader cannot parse must REFUSE (exit 1), never
    paste a half enumeration the worker files as complete - a
    recorded decision the body cannot see is approved blind."""
    from library.tools.project_layout import ProjectLayout

    project = tmp_path / "project"
    project.mkdir()
    ProjectLayout(str(project)).ensure()
    ledger = edit_ledger.ledger_path(str(project))
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text('{"version": 1, "rows": [{"op": "nope"}]}',
                      encoding="utf-8")
    assert ledger_pr_body.main([str(project)]) == 1


# --------------------------------------------------------------------------
# From test_feedback_ledger.py
#
# A piece of captain feedback keeps its identity across a rebuild.
#
# The defect: `marker_routing._note_id` keys a note on
# ``timeline:source:frame``, and `marker_resolution` states the
# consequence itself - *"NOT [stable] across a rebuild that moves the
# frame"*.  Every fix in this pipeline rebuilds the timeline, so the act
# of answering a note destroys the only handle on it, and the next round
# it gets typed again.
#
# These tests pin the durable half: the same words on the same reel are
# one note whatever frame they are read at, and a marker we wrote back
# says so mechanically rather than by its colour.

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from library.tools import feedback_ledger as fl  # noqa: E402
from library.tools import marker_feedback as mf  # noqa: E402
from library.tools import marker_resolution as mr  # noqa: E402


REEL = "Reel 13 - the-accounting-firm-ai-called-healthcare"
ASK = ("the ending tv close animation needs to happen right after "
       "akshita finishes talking")


def pull(timeline, pulled_at, *notes):
    return (Path(f"{pulled_at}.markers.json"),
            {"format": "marker_feedback/1", "timeline": timeline,
             "pulled_at": pulled_at, "notes": list(notes)})


def note(text, frame=100, **extra):
    return {"name": "feedback", "note": text, "text": f"feedback\n\n{text}",
            "source": "timeline_marker", "frame": frame, **extra}


# ── The identity survives what a rebuild changes ─────────────────

def test_the_same_words_at_a_different_frame_are_ONE_note():
    """The whole point. Remove the identity and this is two notes.

    A rebuild moves every frame, which is the exact thing the existing
    `timeline:source:frame` id is made of.
    """
    before = fl.durable_identity(REEL, ASK)
    after = fl.durable_identity(REEL, ASK)
    assert before == after
    entries = fl.collect(None, [
        pull(REEL, "2026-09-11T04:00:00Z", note(ASK, frame=1902)),
        pull(REEL, "2026-09-11T18:00:00Z", note(ASK, frame=1907))])
    assert len(entries) == 1
    entry = next(iter(entries.values()))
    assert entry.pulls == 2
    assert sorted(entry.frames) == [1902, 1907]
    assert entry.first_asked == "2026-09-11T04:00:00Z"
    assert entry.last_seen == "2026-09-11T18:00:00Z"


def test_only_the_engines_own_reel_suffixes_fold_into_one_reel():
    """Staged, backed up, promoted and archived (`reel_retirement`'s
    `(archived round NNN[.M])`) are names for one reel. Hand-made
    parentheses the project has carried keep their own identity - a
    regex over any parenthesis would collide two reels. And
    normalisation never merges different words or different reels: a
    ledger that answered note B with note A's resolution is worse than
    one that files the same words twice."""
    from library.tools.resolve_bin_layout import STAGING_TIMELINE_SUFFIX

    same = fl.durable_identity(REEL, ASK)
    for alias in (f"{REEL}{STAGING_TIMELINE_SUFFIX}",
                  f"{REEL} (pre-rebuild backup)",
                  f"{REEL} (archived round 001)",
                  f"{REEL} (archived round 001.2)"):
        assert fl.durable_identity(alias, ASK) == same, alias
    assert fl.base_reel_name(f"{REEL} (archived round 001)") == REEL

    for suffix in ("(batch-1050)", "(final)", "(MFA timings)",
                   "(all three fixes)", "(baseline scratch)"):
        name = f"{REEL} {suffix}"
        assert fl.base_reel_name(name) == name
        assert fl.durable_identity(name, ASK) != same

    assert fl.durable_identity(REEL, ASK + "!") != same
    assert fl.durable_identity("Reel 01 - a", ASK) \
        != fl.durable_identity("Reel 23 - b", ASK)


# ── The state, and the join back to a resolution record ──────────

def resolution(status, resolved_at, text=ASK, timeline=REEL):
    return {"note_id": f"{timeline}:timeline_marker:1902",
            "status": status, "resolved_at": resolved_at,
            "timeline": timeline, "name": "feedback", "note": text,
            "text": f"feedback\n\n{text}", "check": "a_roll_two_rows",
            "verifier": "marker_resolution.CHECKS[a_roll_two_rows]",
            "marker_removed": True}


def test_a_resolution_reaches_its_note_ACROSS_a_rebuild():
    """The record keys on a frame; the join is on the WORDS.

    Remove the durable identity and this resolution reaches nothing,
    which is the state the project is in today.
    """
    document = fl.build(
        None,
        [pull(REEL, "2026-09-11T04:00:00Z", note(ASK, frame=1902))],
        resolutions=[resolution(mr.STATUS_RESOLVED_VERIFIED,
                                "2026-09-11T10:00:00Z")])
    entry = document["entries"][0]
    assert entry["state"] == mr.STATUS_RESOLVED_VERIFIED
    assert entry["resolution"]["check"] == "a_roll_two_rows"
    assert document["open"] == []


def test_a_note_seen_AFTER_being_recorded_resolved_is_RE_ASKED():
    """We said done, and the captain's marker is still there.

    The expensive failure the round report named, with a name and a
    count. Remove the re-ask reading and a fix that did not hold looks
    exactly like one that did.
    """
    document = fl.build(
        None,
        [pull(REEL, "2026-09-11T04:00:00Z", note(ASK, frame=1902)),
         pull(REEL, "2026-09-11T18:00:00Z", note(ASK, frame=1907))],
        resolutions=[resolution(mr.STATUS_RESOLVED_VERIFIED,
                                "2026-09-11T10:00:00Z")])
    assert document["entries"][0]["reasked"] is True
    assert len(document["reasked"]) == 1

    # Resolved AFTER its last pull: not a re-ask.
    document = fl.build(
        None, [pull(REEL, "2026-09-11T04:00:00Z", note(ASK))],
        resolutions=[resolution(mr.STATUS_RESOLVED_VERIFIED,
                                "2026-09-11T10:00:00Z")])
    assert document["entries"][0]["reasked"] is False
    assert document["reasked"] == []

    # Only a VERIFIED resolution can be contradicted: a decline never
    # claimed the thing was fixed.
    document = fl.build(
        None,
        [pull(REEL, "2026-09-11T04:00:00Z", note(ASK)),
         pull(REEL, "2026-09-11T18:00:00Z", note(ASK))],
        resolutions=[resolution(mr.STATUS_DECLINED,
                                "2026-09-11T10:00:00Z")])
    assert document["entries"][0]["reasked"] is False


# ── Our reply is OURS, by record and not by colour ───────────────

def reply_note(answers, frame=101):
    return {"name": "reply: done", "note": "we did it",
            "text": "reply: done\n\nwe did it",
            "source": "timeline_marker", "frame": frame,
            "custom_data_raw": mf.reply_custom_data(
                answers=answers, answers_text=ASK)}


def test_a_reply_of_ours_is_identified_by_its_own_record():
    """Remove the record and this counts as an open captain question."""
    identity = fl.identity_of(note(ASK), REEL)
    document = fl.build(
        None,
        [pull(REEL, "2026-09-11T04:00:00Z",
              note(ASK, frame=1902), reply_note(identity))],
        resolutions=[])
    kinds = {e["identity"]: e["kind"] for e in document["entries"]}
    assert kinds[identity] == fl.KIND_ASK
    assert fl.KIND_REPLY in kinds.values()
    assert document["open"] == [identity]
    # ...and links back to the question it answers (a blue marker
    # became a green reply and the question was gone).
    asked = next(e for e in document["entries"]
                 if e["identity"] == identity)
    assert len(asked["answered_by"]) == 1
    reply = next(e for e in document["entries"]
                 if e["kind"] == fl.KIND_REPLY)
    assert reply["answers"] == identity


def test_an_unmarked_note_defaults_to_the_CAPTAINS():
    """Getting this backwards LOSES a question. Unmarked is theirs.

    `marker_feedback` has no vocabulary of marker colours by design, so
    a green marker with no record and no `reply:` shape is still read
    as an ask.
    """
    document = fl.build(
        None,
        [pull(REEL, "2026-09-11T04:00:00Z",
              {"name": "feedback", "note": "x",
               "text": "feedback\n\nx", "frame": 5, "color": "Green"})],
        resolutions=[])
    assert document["entries"][0]["kind"] == fl.KIND_ASK

    # Only a FIRST line starting with `reply:` is the writer's stamp; a
    # captain note quoting the word deeper in is still theirs.
    document = fl.build(
        None,
        [pull(REEL, "2026-09-11T04:00:00Z",
              note("please reply to this note", frame=5))],
        resolutions=[])
    assert document["entries"][0]["kind"] == fl.KIND_ASK


def test_a_green_reply_shape_with_no_record_is_ours():
    """The live-project case: two green `reply:` markers, empty customData.

    Remove the shape half of `_authorship` and these count as the
    captain's open questions - the ledger then reports resolved
    feedback as outstanding.
    """
    shaped = {"name": "reply: tail breath for the TV switch-off",
              "note": "You asked (marker @1902): the ending plays early.",
              "text": ("reply: tail breath for the TV switch-off\n\n"
                       "You asked (marker @1902): the ending plays early."),
              "source": "timeline_marker", "frame": 1902,
              "color": "Green", "custom_data": {},
              "custom_data_raw": ""}
    document = fl.build(
        None, [pull(REEL, "2026-09-11T04:00:00Z",
                    note(ASK, frame=1902), shaped)],
        resolutions=[])
    kinds = {e["identity"]: e["kind"] for e in document["entries"]}
    assert list(kinds.values()).count(fl.KIND_REPLY) == 1
    assert kinds[fl.identity_of(note(ASK), REEL)] == fl.KIND_ASK
    assert "1 reply of ours" in fl.render(document)


def test_one_instruction_on_four_reels_is_reported_as_ONE(tmp_path):
    """"apply this to all of the reels" - one mechanism, not N edits."""
    words = "this animation here is something i want applied to all reels"
    document = fl.build(
        None,
        [pull(f"Reel {n} - x", "2026-09-11T04:00:00Z", note(words))
         for n in ("01", "23", "28", "31")],
        resolutions=[])
    echoed = document["echoes"]
    assert len(echoed) == 1
    assert len(next(iter(echoed.values()))) == 4


# ── The file, and what it is not ─────────────────────────────────

def test_the_ledger_is_rewritten_whole_from_the_durable_records(tmp_path):
    """It is DERIVED. Nothing accumulates state of its own here.

    A ledger that kept state the pulls and resolutions do not have
    would become the second source of truth beside the captain's
    markers, which is the one thing this must not be.
    """
    (tmp_path / "marker_feedback").mkdir()
    fl.write_ledger(tmp_path, {"format": fl.LEDGER_FORMAT,
                               "entries": [{"identity": "stale"}]})
    document = fl.build(None, [pull(REEL, "2026-09-11T04:00:00Z",
                                    note(ASK))], resolutions=[])
    fl.write_ledger(tmp_path, document)
    written = fl.read_ledger(tmp_path)
    assert [e["identity"] for e in written["entries"]] \
        != ["stale"]
    assert json.loads(fl.ledger_path(tmp_path).read_text(
        encoding="utf-8"))["format"] == fl.LEDGER_FORMAT


def test_render_shows_the_words_not_just_the_marker_name():
    """The first line of a marker's text is its NAME field.

    Printing it alone showed the word "feedback" for every note on
    `lucie/geo-podcast` and nothing the captain typed.
    """
    document = fl.build(None, [pull(REEL, "2026-09-11T04:00:00Z",
                                    note(ASK))], resolutions=[])
    printed = fl.render(document)
    assert "akshita finishes talking" in printed


# ── The identity grammar the `answers` single writer enforces ────────


def test_is_identity_rejects_prose_frames_and_fragments():
    """Every shape found on a live reel that is not an identity."""
    for bad in ("R04 blue feedback",
                "Reel 14 - why-ai-trusts-youtube@162",
                "Reel 29 - salvage#clip_marker@22",
                "", None, 0,
                "Reel_14", "Reel_14:xyz",
                "Reel_14:9f2c4a1b7e5d03a",
                "Reel_14:9f2c4a1b7e5d03aag"):
        assert fl.is_identity(bad) is False


# --------------------------------------------------------------------------
# From test_journal_paths_never_overwrite.py
#
# Every function that names a journal file names a NEW one inside the same second.
#
# The stamp is second-granularity, so a verify-twice pass or a retry inside
# one second would land the second record on top of the first - the only
# record of an irreversible act. Parameterised over the REAL functions, not
# the shared helper (`journal_path.unique_path`), so a seventh writer that
# hand-rolls its path fails here. History: docs/evidence/journal_paths.md

# (import path, attribute) for every function that names a journal file.
JOURNAL_PATH_FUNCTIONS = [
    ("library.tools.build_sweep", "journal_path_for"),
    ("library.tools.execution.organise_media_pool", "journal_path_for"),
    ("library.tools.execution.mark_master", "journal_path_for"),
    ("library.tools.execution.retire_empty_bins", "journal_path_for"),
    ("library.tools.execution.remove_proof", "journal_path_for"),
    ("library.tools.execution.prune_orphans", "journal_path_for"),
    ("library.tools.execution.prune_orphans", "manifest_path_for"),
]


@pytest.mark.parametrize("module_name,attr", JOURNAL_PATH_FUNCTIONS,
                         ids=[f"{m.rsplit('.', 1)[-1]}.{a}"
                              for m, a in JOURNAL_PATH_FUNCTIONS])
def test_a_second_journal_in_the_same_second_does_not_overwrite_the_first(
        module_name, attr, tmp_path):
    """Two calls with ONE stamp must name two different files.

    The second call only differs once the first file EXISTS - that is the
    real sequence, because the caller writes before it asks again - so the
    test writes the first journal before asking for the second.
    """
    module = importlib.import_module(module_name)
    name_it = getattr(module, attr)
    stamp = "20260912T101500Z"

    first = Path(name_it(str(tmp_path), stamp))
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_text(json.dumps({"record": "the first run's 102 files"}),
                     encoding="utf-8")

    second = Path(name_it(str(tmp_path), stamp))

    assert second != first, (
        f"{module_name}.{attr} named the SAME file twice inside one second.\n"
        f"  {first}\n"
        "The first journal is the only record of an irreversible act and the "
        "second write destroys it. Name the file through "
        "library/tools/journal_path.unique_path, which suffixes past a "
        "collision."
    )
    assert first.exists(), "the first journal must survive being asked again"
    assert second.parent == first.parent, (
        "the sibling journal must land beside the first, not somewhere else")


# --------------------------------------------------------------------------
# From test_perf_ledger.py
#
# The run profile charges each second once.
#
# Two defects this pins, both seen on the first smoke run of the ledger:
# a render fanned out over four threads summed to four times its wall,
# and a model load nested inside an inference span was counted in both
# layers. Either makes `ren profile` point at the wrong layer, which is
# the one thing it exists not to do.
#
# Fixture-only: no step, model or renderer runs.

def _shares(project):
    rows = perf_ledger.read_rows(project)
    report = perf_ledger.profile(rows, "r1")
    return report, {line["name"]: line["wall_s"] for line in report["lines"]}


def test_concurrent_and_nested_spans_are_charged_once(tmp_path):
    project = str(tmp_path)
    with perf_ledger.capability(project, "render_subtitles", "r1"):
        def card():
            with perf_ledger.span("remotion_render"):
                time.sleep(0.2)
        threads = [threading.Thread(target=card) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        with perf_ledger.span("gemma_inference"):
            time.sleep(0.05)
            with perf_ledger.span("model_load"):
                time.sleep(0.15)
            time.sleep(0.05)

    report, shares = _shares(project)
    rows = perf_ledger.read_rows(project)
    render_walls = [r["wall_s"] for r in rows
                    if r.get("layer") == "remotion_render"]
    # Only lower bounds are wall-clock facts (a sleep never returns
    # early); everything else is a relation that holds however a loaded
    # machine schedules the threads.
    assert len(render_walls) == 4
    assert max(render_walls) <= shares["remotion_render"] + 0.01
    assert shares["remotion_render"] < 0.5 * sum(render_walls)
    assert shares["model_load"] >= 0.15
    assert shares["gemma_inference"] >= 0.1
    assert report["overlap_s"] < 0.02
    assert abs(sum(shares.values()) - report["total_wall_s"]) < 0.02


def test_no_ledger_in_the_environment_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv(perf_ledger.LEDGER_ENV, raising=False)
    with perf_ledger.span("gemma_inference") as cost:
        cost["calls"] = 1
    perf_ledger.record("host_model", 1.0)
    assert not list(tmp_path.rglob("*.jsonl"))
