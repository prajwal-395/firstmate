"""The step-replay bench measures; it must not reach a project or a step.

Three things are held here:

1. The snapshot notices when it goes stale.  A bench that silently
   compares against state that has moved is worse than no bench, so the
   seal and the reference listings are checked, and both failure modes are
   driven rather than described.
2. A reconstruction really is the runner's own assembly - the DAG edge
   walk, the projector, the serializer and the handoff - driven end to end
   against a project built under `tmp_path`.
3. Token counts name their tokenizer, and the pipeline's own word-count
   heuristic can never be returned under one.

Every project here is built under `tmp_path`; nothing reads
`PROJECTS_ROOT`.  See AGENTS.md, "No test reaches a real project".
"""

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.tools.replay_bench import bench, tokens, trees
from library.tools.replay_bench import snapshot as snapshot_mod

# ── A project small enough to build, real enough to reconstruct ──────

def _write_project(root: Path) -> Path:
    """A two-clip project whose state satisfies creative_direction's edges."""
    project = root / "proj"
    (project / "raw").mkdir(parents=True)
    (project / "raw" / "clip_001.mov").write_bytes(b"not really a movie")
    (project / "project.yaml").write_text(
        "name: replay bench fixture\nslug: replay-fixture\n", encoding="utf-8")

    docs = [
        {"clip_id": f"clip_{i:03d}", "duration_s": 10.0 + i,
         "scene": [{"start": 0.0, "end": 4.0, "location": f"room {i}"}],
         "camera": [{"start": 0.0, "end": 4.0, "shot_size": "wide"}],
         "analysis": {"scene": f"a room, number {i}"},
         "assessment": {"content_type": "b_roll", "keywords": ["room"],
                        "usable_ranges": [[0.0, 10.0 + i]]}}
        for i in (1, 2)
    ]
    state = {
        "project_folder": str(project),
        "preflight_completed": ["scan", "catalog", "semantic_analysis",
                                "temporal_index", "prosody_analysis"],
        "edit_completed": [],
        "step_outputs": {
            # Shaped to what creative_direction's manifest really declares
            # (`clip_catalog.*.filename`, `.duration_seconds`): the real
            # projector runs here and raises when every item projects empty.
            "catalog": {"clip_catalog": [
                {"clip_id": "clip_001", "filename": "clip_001.mov",
                 "duration_seconds": 11.0, "clip_type": "b_roll"},
                {"clip_id": "clip_002", "filename": "clip_002.mov",
                 "duration_seconds": 12.0, "clip_type": "a_roll"},
            ]},
            "semantic_analysis": {"semantic_analysis_documents": docs,
                                  "total_clips_analyzed": 2},
            "temporal_index": {"temporal_event_indices": [
                {"clip_id": "clip_001",
                 "speech_regions": [{"start": 0.5, "end": 3.0, "text": "hello"}],
                 "scene_boundaries": [{"time": 0.0, "type": "start"}]},
                {"clip_id": "clip_002",
                 "speech_regions": [{"start": 1.0, "end": 2.0, "text": "world"}],
                 "scene_boundaries": [{"time": 0.0, "type": "start"}]},
            ]},
            "prosody_analysis": {"prosody_analysis": {
                "profiles": {"clip_001": {
                    "clip_id": "clip_001",
                    "prosody": {"method": "praat",
                                "pitch_stats": {"mean_f0_hz": 120.0}}}},
                "total_clips": 1}},
        },
    }
    (project / "pipeline_data.json").write_text(
        json.dumps(state, indent=2), encoding="utf-8")
    return project


@pytest.fixture
def project(tmp_path):
    return _write_project(tmp_path)


@pytest.fixture
def store(tmp_path):
    return tmp_path / "snapshots"


# ── 1. Staleness ────────────────────────────────────────────────────

def test_a_snapshot_edited_after_capture_reports_a_broken_seal(project, store):
    snap = snapshot_mod.capture(str(project), snapshot_id="fx", store=store)
    (snap.project_dir / "pipeline_data.json").write_text("{}", encoding="utf-8")
    report = snap.verify()
    assert not report["sealed"]
    assert any("pipeline_data.json" in b for b in report["seal_breaks"])


def test_a_referenced_area_that_moves_is_reported_not_used(project, store):
    """The whole point: the bench notices the world moved under it."""
    snap = snapshot_mod.capture(str(project), snapshot_id="fx", store=store)
    assert snap.verify()["references_drifted"] == []
    (project / "raw" / "clip_002.mov").write_bytes(b"a new clip appeared")
    drift = snap.verify()["references_drifted"]
    assert drift and any(d.startswith("raw:") for d in drift)


def test_a_snapshot_is_immutable_unless_replacement_is_asked_for(project, store):
    snapshot_mod.capture(str(project), snapshot_id="fx", store=store)
    with pytest.raises(FileExistsError):
        snapshot_mod.capture(str(project), snapshot_id="fx", store=store)
    snapshot_mod.capture(str(project), snapshot_id="fx", store=store, force=True)


# ── 1b. A replay never writes the live project ──────────────────────

def _source_files(project: Path) -> dict:
    return {str(p.relative_to(project)): p.stat().st_mtime_ns
            for p in project.rglob("*")}


def _with_live_output(project: Path) -> Path:
    """A project whose state names an absolute path into its own output."""
    out = project / "pipeline_output" / "steps" / "3_04_select_reels"
    out.mkdir(parents=True)
    (out / "reel_candidate_diagnostics.md").write_text("from the real run\n",
                                                       encoding="utf-8")
    # A link the project itself carries, pointing back into the project.
    os.symlink(project / "raw", project / "pipeline_output" / "raw_link")
    state = json.loads((project / "pipeline_data.json").read_text("utf-8"))
    state["step_outputs"]["select_reels"] = {
        "diagnostics_path": str(out / "reel_candidate_diagnostics.md")}
    (project / "pipeline_data.json").write_text(json.dumps(state),
                                                encoding="utf-8")
    return project


def test_no_path_in_a_replay_workspace_resolves_inside_the_source_project(
        project, store):
    """The snapshot REFERENCES `pipeline_output/` by symlink, so a replay
    that ran against the snapshot directory wrote straight into the live
    project: 3.04's diagnostics, 3.02's footage analysis and frames, 4.04's
    catalogue. Measured 2026-10-01 on 001 - 395 files written into a
    project whose last run was 2026-08-30. A replay gets a clone instead,
    and every path in it - including one the state names absolutely -
    resolves outside the source."""
    _with_live_output(project)
    snap = snapshot_mod.capture(str(project), snapshot_id="fx", store=store)
    roots = [os.path.realpath(project)]

    with snapshot_mod.isolated_project(snap) as ws:
        for p in [ws, *ws.rglob("*")]:
            assert not os.path.realpath(p).startswith(roots[0] + os.sep), p
        assert snapshot_mod.escaping_paths(ws, roots) == []
        assert (ws / "pipeline_output" / "steps" / "3_04_select_reels"
                / "reel_candidate_diagnostics.md").read_text("utf-8") \
            == "from the real run\n", "reads still see the project's output"
        state = json.loads((ws / "pipeline_data.json").read_text("utf-8"))
        named = Path(state["step_outputs"]["select_reels"]["diagnostics_path"])
        assert named.is_relative_to(ws), named
        assert state["project_folder"] == str(ws)
    assert not ws.exists(), "the clone is thrown away"


def test_a_replay_that_writes_leaves_the_source_project_untouched(
        project, store, tmp_path, monkeypatch):
    """End to end through `_run_worker`: a worker that writes the way the
    bridges do - into its project folder's layout, and to an absolute path
    it read from state - must change nothing in the source project."""
    _with_live_output(project)
    snapshot_mod.capture(str(project), snapshot_id="fx", store=store)
    before = _source_files(project)

    worker = tmp_path / "writing_worker.py"
    worker.write_text(
        "import json, sys, pathlib\n"
        "a = sys.argv[1:]; arg = lambda k: a[a.index(k) + 1]\n"
        "proj = pathlib.Path(arg('--project-dir'))\n"
        "state = json.loads(pathlib.Path(arg('--state')).read_text())\n"
        "doc = proj / 'pipeline_output/steps/3_02_select_broll/footage.md'\n"
        "doc.parent.mkdir(parents=True, exist_ok=True)\n"
        "doc.write_text('written by a replay')\n"
        "pathlib.Path(state['step_outputs']['select_reels']"
        "['diagnostics_path']).write_text('overwritten by a replay')\n"
        "pathlib.Path(arg('--out')).write_text(json.dumps({'ok': True}))\n",
        encoding="utf-8")
    monkeypatch.setattr(bench, "WORKER", worker)

    snap = snapshot_mod.load("fx", store)
    assert bench._run_worker(REPO_ROOT, snap, "select_reels") == {"ok": True}

    assert _source_files(project) == before
    assert (project / "pipeline_output" / "steps" / "3_04_select_reels"
            / "reel_candidate_diagnostics.md").read_text("utf-8") \
        == "from the real run\n"
    assert snap.verify()["references_drifted"] == []


# ── 2. The reconstruction is the runner's own assembly ──────────────

def test_replay_rebuilds_a_real_step_off_frozen_state(project, store):
    snapshot_mod.capture(str(project), snapshot_id="fx", store=store)
    result = bench.replay("fx", "creative_direction", rev=trees.WORKTREE,
                          store=store)

    assert result["step_type"] == "llm_only"
    # The projector really ran: creative_direction declares context_fields,
    # so the context carries the routed keys and not the whole state.
    # `transcript` and `picture` rather than `temporal_index`: both are
    # declared as `view:` readings of a routed input
    # (library/tools/context_views.py), and a view's name is the key it
    # writes.  `temporal_index` itself is no longer in the projection
    # since scene_boundaries was retired (#225) and the views are the
    # only consumers. `prosody` is also such a reading, re-wired to provide
    # deterministic measurements (2026-09-01).
    assert set(result["top_level_keys"]) >= {
        "clip_catalog", "semantic_analysis_documents", "transcript", "prosody"}
    assert "step_outputs" not in result["top_level_keys"]
    # The serializer really ran, on real content from the frozen state.
    assert "clip_001" in result["context"]
    assert "a room, number 1" in result["context"]
    # The handoff really was read, and the schema injected from the manifest.
    assert "creative director" in result["prompt"].lower()
    assert "creative_direction" in result["expected_schema"]
    # And the snapshot said whether it could still be trusted.
    assert result["staleness"]["sealed"]


def test_the_reconstruction_carries_what_the_runner_appends_to_the_schema(
        project, store):
    """`present_llm_step` appends `could_not_determine` to the RENDERED
    schema of the nine declaring steps and its instruction to the prompt.
    A reconstruction without it makes every declaring step read as a
    difference `verify` cannot account for - and `verify` is a gate, so a
    reconstruction that cannot reproduce the past cannot be trusted to
    compare futures."""
    from library.tools import undetermined
    snapshot_mod.capture(str(project), snapshot_id="fx", store=store)
    result = bench.replay("fx", "creative_direction", store=store)

    assert undetermined.declares("creative_direction")
    names = [o["name"] for o in json.loads(result["expected_schema"])]
    assert undetermined.FIELD in names, (
        "the runner asks for it; the reconstruction must too")
    assert undetermined.FIELD in result["prompt"]
    assert "Return `[]` when the material was sufficient" in result["prompt"]


def test_the_context_carries_the_project_folder_the_run_recorded(project, store):
    """A replay runs against the frozen copy and must not leak its path."""
    snap = snapshot_mod.capture(str(project), snapshot_id="fx", store=store)
    result = bench.replay("fx", "creative_direction", store=store)
    assert str(snap.project_dir) not in result["context"]
    assert snapshot_mod.WORKSPACES_DIR not in result["context"]
    assert result["project_folder_substitutions"] >= 1


def test_a_step_that_is_not_in_the_dag_is_named_not_guessed(project, store):
    snapshot_mod.capture(str(project), snapshot_id="fx", store=store)
    with pytest.raises(RuntimeError, match="not a node in this tree"):
        bench.replay("fx", "no_such_step", store=store)


def test_a_declared_creative_brief_reaches_the_reconstructed_context(
        project, store, tmp_path):
    """The bench has to rebuild the RUN-LEVEL half of state as well.

    `load_pipeline_state` is the runner's whole state assembly: it parses
    `pipeline_data.json` and then overlays the values that belong to the
    run rather than to any upstream step - `creative_brief` and
    `brand_template` off `project.yaml`, the asset libraries off the
    environment. The bench used to `json.load` the frozen state file and
    stop there, and that omission fails in the one direction that matters:
    a project pointing at a brief reconstructed as a project pointing at
    none, so the routing change read as a no-op. Measured on 001 the day
    it was pointed at the channel document (#214) - seven steps declare
    `creative_brief` and all seven replayed without it.
    """
    brief = tmp_path / "planning" / "channel_brief.md"
    brief.parent.mkdir(parents=True)
    brief.write_text("# Channel brief\n\nSENTINEL_BRIEF_IN_REPLAY_4c1e\n",
                     encoding="utf-8")
    yaml_path = project / "project.yaml"
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8")
        + f'creative_brief: "{brief}"\n', encoding="utf-8")

    snapshot_mod.capture(str(project), snapshot_id="fx", store=store)
    result = bench.replay("fx", "creative_direction", rev=trees.WORKTREE,
                          store=store)

    assert "creative_brief" in result["top_level_keys"]
    assert "SENTINEL_BRIEF_IN_REPLAY_4c1e" in result["context"]
    # And the reconstruction says where that key came from, so a reader
    # can tell a routed input from a run-level one.
    assert any("run-level state" in n for n in result["notes"]), result["notes"]


# ── 3. Reading the difference ───────────────────────────────────────


# ── 4. Token counts name their tokenizer ────────────────────────────

def test_an_unknown_tokenizer_raises_rather_than_guessing():
    with pytest.raises(ValueError, match="unknown tokenizer"):
        tokens.count("x", "gpt-guess")


def test_the_pipelines_own_word_heuristic_is_never_a_tokenizer():
    """`present_llm_step` logs len(s.split())*1.3 and it is not a count."""
    assert "pipeline_heuristic" not in tokens.available()
    with pytest.raises(ValueError):
        tokens.count("a b c", "pipeline_heuristic")
    assert tokens.pipeline_heuristic("a b c") == int(3 * 1.3)


def test_o200k_is_absent_rather_than_estimated_when_tiktoken_is_missing():
    if tokens.O200K in tokens.available():
        pytest.skip("tiktoken is installed here; the absent path is elsewhere")
    with pytest.raises(RuntimeError, match="tiktoken"):
        tokens.count("x", tokens.O200K)


# ── 5. The bench measures; it is not part of the pipeline ───────────

PIPELINE_TREES = ("library/steps", "library/processes", "library/dashboard")


def test_no_step_process_or_dashboard_imports_the_bench():
    """The bench reads the pipeline.  The pipeline must not read the bench.

    A measuring tool that a step depends on stops being a measuring tool.
    """
    offenders = []
    for tree in PIPELINE_TREES:
        for path in (REPO_ROOT / tree).rglob("*"):
            if path.suffix not in (".py", ".json") or "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "replay_bench" in text:
                offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, (
        "the replay bench measures the pipeline and must stay out of it; "
        f"referenced by: {offenders}")


def test_the_bench_imports_nothing_from_library_at_worker_module_scope():
    """A comparison across two trees dies quietly if this ever regresses.

    `reconstruct.py` runs as a subprocess with the TARGET tree first on
    `sys.path`.  An import of `library.*` at module scope would bind
    whichever tree the parent was on, and both sides of a comparison would
    silently measure the same code.
    """
    import ast
    src = (REPO_ROOT / "library/tools/replay_bench/reconstruct.py").read_text(
        encoding="utf-8")
    module = ast.parse(src)
    for node in module.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""])
            assert not any(n.startswith("library") for n in names), (
                f"reconstruct.py imports {names} at module scope; it must "
                f"import from the target tree inside a function")


def test_capture_freezes_the_creative_brief_a_project_declares(tmp_path,
                                                               store):
    """A step that declares the brief must be replayable.

    Ten steps declare `creative_brief` and the runner RAISES rather than
    degrading when a declared brief cannot be read - correctly, because a
    step that reported success having read a filename was the defect that
    rule replaced. So a snapshot that did not carry the brief could not
    reconstruct ANY of those ten, which is every step that makes a
    creative judgement.

    Found 2026-09-05: step 3.4 regained its declaration and
    `replay_bench compare select_reels` stopped working entirely, with
    "declares creative_brief and the project points at
    <snapshot>/project/creative_brief.md, which cannot be read".
    """
    project = _write_project(tmp_path)
    (project / "project.yaml").write_text(
        "name: replay bench fixture\nslug: replay-fixture\n"
        "pipeline:\n  creative_brief: creative_brief.md\n",
        encoding="utf-8")
    (project / "creative_brief.md").write_text(
        "# what this episode wants\n\nnineteen reels, each ending its own "
        "way.\n", encoding="utf-8")

    snap = snapshot_mod.capture(str(project), store=store, snapshot_id="withbrief")

    frozen = snap.project_dir / "creative_brief.md"
    assert frozen.is_file(), "the declared brief must be inside the snapshot"
    assert "nineteen reels" in frozen.read_text(encoding="utf-8")

    # COPIED, not referenced: a later edit must not change what a replay
    # reconstructs, which is the whole point of freezing state.
    (project / "creative_brief.md").write_text("rewritten\n", encoding="utf-8")
    assert "nineteen reels" in frozen.read_text(encoding="utf-8")
