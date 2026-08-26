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
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.tools.replay_bench import bench, diffing, tokens, trees
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
            "temporal_index": {"full_indices": [
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

def test_capture_seals_the_state_it_froze(project, store):
    snap = snapshot_mod.capture(str(project), snapshot_id="fx", store=store)
    report = snap.verify()
    assert report["sealed"], report["seal_breaks"]
    assert report["references_drifted"] == []
    assert snap.declared_project_folder == str(project)
    assert snap.state["step_outputs"]["catalog"]["clip_catalog"][0]["clip_id"] \
        == "clip_001"


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


def test_the_frozen_state_does_not_move_when_the_project_does(project, store):
    snap = snapshot_mod.capture(str(project), snapshot_id="fx", store=store)
    live = json.loads((project / "pipeline_data.json").read_text(encoding="utf-8"))
    live["step_outputs"]["catalog"]["clip_catalog"] = []
    (project / "pipeline_data.json").write_text(json.dumps(live), encoding="utf-8")
    assert snap.state["step_outputs"]["catalog"]["clip_catalog"] != []
    assert snap.verify()["sealed"]


def test_a_snapshot_is_immutable_unless_replacement_is_asked_for(project, store):
    snapshot_mod.capture(str(project), snapshot_id="fx", store=store)
    with pytest.raises(FileExistsError):
        snapshot_mod.capture(str(project), snapshot_id="fx", store=store)
    snapshot_mod.capture(str(project), snapshot_id="fx", store=store, force=True)


def test_a_capture_taken_mid_run_says_so(project, store):
    (project / "pipeline_run.json").write_text(
        json.dumps({"status": "running", "current_step": "mesh_spine"}),
        encoding="utf-8")
    snap = snapshot_mod.capture(str(project), snapshot_id="torn", store=store)
    assert snap.manifest["torn"] is True
    assert bench._staleness(snap)["torn_at_capture"] is True


# ── 2. The reconstruction is the runner's own assembly ──────────────

def test_replay_rebuilds_a_real_step_off_frozen_state(project, store):
    snapshot_mod.capture(str(project), snapshot_id="fx", store=store)
    result = bench.replay("fx", "creative_direction", rev=trees.WORKTREE,
                          store=store)

    assert result["step_type"] == "llm_only"
    # The projector really ran: creative_direction declares context_fields,
    # so the context carries the four routed keys and not the whole state.
    # `prosody` and `transcript` rather than `prosody_analysis` and
    # `temporal_index`: both are declared as `view:` readings of a routed
    # input (library/tools/context_views.py), and a view's name is the key
    # it writes.
    assert set(result["top_level_keys"]) >= {
        "clip_catalog", "prosody", "semantic_analysis_documents",
        "temporal_index", "transcript"}
    assert "step_outputs" not in result["top_level_keys"]
    # The serializer really ran, on real content from the frozen state.
    assert "clip_001" in result["context"]
    assert "a room, number 1" in result["context"]
    # The handoff really was read, and the schema injected from the manifest.
    assert "creative director" in result["prompt"].lower()
    assert "creative_direction" in result["expected_schema"]
    # And the snapshot said whether it could still be trusted.
    assert result["staleness"]["sealed"]


def test_the_context_carries_the_project_folder_the_run_recorded(project, store):
    """A replay runs against the frozen copy and must not leak its path."""
    snap = snapshot_mod.capture(str(project), snapshot_id="fx", store=store)
    result = bench.replay("fx", "creative_direction", store=store)
    assert str(snap.project_dir) not in result["context"]
    assert result["project_folder_substitutions"] >= 1


def test_compare_at_one_revision_against_itself_finds_no_difference(project, store):
    snapshot_mod.capture(str(project), snapshot_id="fx", store=store)
    out = bench.compare("fx", "creative_direction",
                        trees.WORKTREE, trees.WORKTREE, store=store)
    assert out["context_identical"] is True
    assert out["prompt_identical"] is True
    assert out["context_section_deltas"] == []


def test_compare_diffs_answers_when_both_are_supplied(project, store, tmp_path):
    snapshot_mod.capture(str(project), snapshot_id="fx", store=store)
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text(json.dumps({"creative_direction": {
        "tone": "wry", "cutaways": 5}}), encoding="utf-8")
    b.write_text(json.dumps({"creative_direction": {
        "tone": "earnest", "cutaways": 5, "palette": "cold"}}), encoding="utf-8")
    out = bench.compare("fx", "creative_direction", trees.WORKTREE,
                        trees.WORKTREE, store=store, answer_a=a, answer_b=b)
    assert out["answers"]["identical"] is False
    paths = {r["path"]: r["kind"] for r in out["answers"]["differing_paths"]}
    assert paths["creative_direction.tone"] == "changed"
    assert paths["creative_direction.palette"] == "added"
    assert "creative_direction.cutaways" not in paths


def test_a_step_that_is_not_in_the_dag_is_named_not_guessed(project, store):
    snapshot_mod.capture(str(project), snapshot_id="fx", store=store)
    with pytest.raises(RuntimeError, match="not a node in this tree"):
        bench.replay("fx", "no_such_step", store=store)


def test_verify_reports_a_step_whose_state_has_moved(project, store):
    """No archive here, so the archive is synthesised - and made wrong."""
    snap = snapshot_mod.capture(str(project), snapshot_id="fx", store=store)
    truth = bench.replay("fx", "creative_direction", store=store)
    snap.archive_dir.mkdir(parents=True, exist_ok=True)
    (snap.archive_dir / "creative_direction.json").write_text(json.dumps({
        "step_id": "creative_direction", "prompt": truth["prompt"],
        "context": truth["context"], "expected_schema": truth["expected_schema"],
    }), encoding="utf-8")
    report = bench.verify_archive("fx", store=store)
    assert report["total"] == 1
    assert report["exact"] == 1
    assert report["unaccounted"] == 0
    assert report["wrote_nothing"] is True

    stale = json.loads((snap.archive_dir / "creative_direction.json")
                       .read_text(encoding="utf-8"))
    stale["context"] = stale["context"].replace("a room, number 1",
                                                "a room, number 9")
    (snap.archive_dir / "creative_direction.json").write_text(
        json.dumps(stale), encoding="utf-8")
    report = bench.verify_archive("fx", store=store)
    assert report["unaccounted"] == 1
    assert report["rows"][0]["verdict"] == "DIFFERS"
    assert report["rows"][0]["residual_sections"]


def test_verify_reproduces_a_qa_retry_rather_than_excusing_it(project, store):
    """The archive is last-write-wins, so a surviving file may be a retry."""
    snap = snapshot_mod.capture(str(project), snapshot_id="fx", store=store)
    truth = bench.replay("fx", "creative_direction", store=store)
    snap.archive_dir.mkdir(parents=True, exist_ok=True)
    retry = (truth["context"] + diffing.QA_RETRY_MARKER
             + "\nThe previous output failed validation: nope\nPlease correct this.")
    (snap.archive_dir / "creative_direction.json").write_text(json.dumps({
        "step_id": "creative_direction", "prompt": truth["prompt"],
        "context": retry, "expected_schema": truth["expected_schema"],
    }), encoding="utf-8")
    report = bench.verify_archive("fx", store=store)
    row = report["rows"][0]
    assert row["exact"] is False
    assert row["verdict"] == "EXACT (explained)"
    assert row["explained_delta_bytes"] == 0
    assert any("QA RETRY" in e for e in row["explanations"])
    assert report["unaccounted"] == 0


# ── 3. Reading the difference ───────────────────────────────────────

def test_sections_split_on_the_top_level_key_the_projector_selected():
    context = ("clip_catalog:\n  [0] a\n  [1] b\n"
               "timed_spine:\n  structure:\n    x\n"
               "project_folder: /somewhere\n")
    parts = diffing.split_sections(context)
    assert sorted(parts) == ["clip_catalog", "project_folder", "timed_spine"]
    assert "structure" in parts["timed_spine"]


def test_a_delta_names_the_section_it_came_from():
    left = "a:\n  one\nb:\n  two\n"
    right = "a:\n  one\nb:\n  two two two\n"
    rows = diffing.section_deltas(left, right)
    assert [r["section"] for r in rows] == ["b"]
    assert rows[0]["delta_bytes"] < 0


def test_a_section_present_on_one_side_only_is_reported_as_such():
    rows = diffing.section_deltas("a:\n  one\nb:\n  two\n", "a:\n  one\n")
    assert rows[0]["section"] == "b"
    assert rows[0]["only_in"] == "left"


def test_a_qa_retry_block_splits_off_the_first_attempt():
    head, tail = diffing.split_qa_retry(
        "body" + diffing.QA_RETRY_MARKER + "\nfix it")
    assert head == "body"
    assert tail.startswith(diffing.QA_RETRY_MARKER)
    assert diffing.split_qa_retry("body") == ("body", "")


def test_answers_are_diffed_as_json_not_as_text():
    rows = diffing.json_answer_diff({"a": 1, "b": [1, 2]}, {"b": [1, 3], "a": 1})
    assert [r["path"] for r in rows] == ["b[1]"]


# ── 4. Token counts name their tokenizer ────────────────────────────

def test_utf8_bytes_is_exact_and_always_available():
    assert tokens.BYTES in tokens.available()
    assert tokens.count("héllo", tokens.BYTES) == len("héllo".encode())


def test_an_unknown_tokenizer_raises_rather_than_guessing():
    with pytest.raises(ValueError, match="unknown tokenizer"):
        tokens.count("x", "gpt-guess")


def test_measure_returns_only_tokenizers_this_environment_really_has():
    measured = tokens.measure("some context")
    assert set(measured) == set(tokens.available())
    assert all(isinstance(v, int) for v in measured.values())


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

    Same discipline as the footage-query prototype (AGENTS.md §2): a
    measuring tool that a step depends on stops being a measuring tool.
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


def test_the_committed_snapshot_manifest_carries_no_payload():
    """What is committed is the manifest, and only the manifest.

    The payload is one client's transcripts and vision documents and it
    goes stale the moment a step changes what it emits; the manifest is a
    few kilobytes of digests that let two people establish they hold the
    same bytes.  See library/tools/replay_bench/snapshot.py.
    """
    fixtures = REPO_ROOT / "tests/fixtures/replay_snapshots"
    if not fixtures.is_dir():
        pytest.skip("no snapshot manifests committed yet")
    for path in fixtures.glob("*.json"):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert {"snapshot_id", "files", "references",
                "declared_project_folder"} <= set(manifest)
        for entry in manifest["files"]:
            assert set(entry) >= {"path", "sha256", "bytes"}
            assert "content" not in entry
        assert path.stat().st_size < 200_000, (
            f"{path.name} is {path.stat().st_size} B - a manifest, not a payload")
