"""The project folder read back as the story of a run.

The captain is walking all 28 steps by hand and needs to open the folder
and answer, without asking anyone: what is this file, which step made it,
what did that step read, and did it work.

`project_layout` organises by KIND. That is the substrate. This is the
other axis - WHO wrote this, WHEN, FROM WHAT - and the property that
matters most is not coverage but honesty: an attribution the pipeline
observed and an attribution it merely declares must never read the same,
and a file nothing accounts for must read as unaccounted for rather than
being handed to whichever step is nearest.
"""

import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from library.tools import provenance
from library.tools.project_layout import AREAS, Area, ProjectLayout
from library.tools.provenance import (
    DECLARED,
    OBSERVED,
    UNKNOWN,
    ProvenanceLedger,
    new_run_id,
    read_declared_sources,
)
from library.tools.run_traceback import (
    ARTIFACT_INDEX_FILE,
    TRACEBACK_FILE,
    build_traceback,
    render_artifact_index,
    render_traceback,
    write_traceback,
)

FIXTURE_STATE = Path(__file__).parent / "fixtures" / "e2e_pipeline_data.json"

DAG = {
    "id": "edit_video",
    "nodes": [
        {"id": "scan", "name": "Scan Project Folder",
         "step_ref": "steps/step_1_01_scan_project"},
        {"id": "temporal_index", "name": "Temporal Index",
         "step_ref": "steps/step_1_04_temporal_index"},
        {"id": "prosody_analysis", "name": "Prosody Analysis",
         "step_ref": "steps/step_1_05_prosody_analysis"},
        {"id": "render", "name": "Render",
         "step_ref": "steps/step_6_01_render"},
    ],
    "edges": [
        {"from": "scan", "to": "temporal_index",
         "data_mapping": {"raw_footage_files": "raw_footage_files"}},
        {"from": "temporal_index", "to": "prosody_analysis",
         "data_mapping": {"temporal_index": "temporal_index"}},
        {"from": "scan", "to": "prosody_analysis",
         "data_mapping": {"raw_footage_files": "raw_footage_files"}},
    ],
}


@pytest.fixture
def project(tmp_path):
    layout = ProjectLayout(tmp_path).ensure()
    (tmp_path / "project.yaml").write_text("slug: t\n", encoding="utf-8")
    layout.pipeline_data_path.write_text(json.dumps({
        "started_at": "2026-08-17T10:00:00",
        "last_updated": "2026-08-21T20:00:00",
        "preflight_completed": {
            "scan": {"completed_at": "2026-08-17T10:34:06", "elapsed_s": 0.4},
            "temporal_index": {"completed_at": "2026-08-17T17:09:28",
                               "elapsed_s": 580.3},
        },
        "edit_completed": {},
        "step_outputs": {
            "scan": {"raw_footage_files": [], "total_files": 0},
            "temporal_index": {"index_dir": "x"},
        },
        "failed_steps": ["render"],
        "step_errors": {"render": "Step failed (exit 1):\n  no timeline"},
        "source_fingerprints": {
            "clip_001": {"path": str(tmp_path / "raw" / "IMG_1806.MOV"),
                         "size_bytes": 7354921, "content_digest": "abc"},
        },
    }), encoding="utf-8")
    return tmp_path


def _write(layout, area, name, payload):
    p = layout.write_path(area, name)
    p.write_text(json.dumps(payload) if isinstance(payload, dict) else payload,
                 encoding="utf-8")
    return p


# ── Observation: the strong kind ────────────────────────────────────

def test_a_file_written_during_a_step_is_attributed_to_that_step(project):
    layout = ProjectLayout(project)
    ledger = ProvenanceLedger(project)
    before = ledger.snapshot()
    _write(layout, Area.PROSODY, "clip_001_prosody.json", {"clip_id": "clip_001"})
    recs = ledger.observe("prosody_analysis", "run-1", before, ledger.snapshot())

    assert [r.path for r in recs] == [
        "pipeline_output/steps/1_05_prosody_analysis/clip_001_prosody.json"]
    assert recs[0].step_id == "prosody_analysis"
    assert recs[0].run_id == "run-1"
    assert recs[0].method == OBSERVED
    assert recs[0].area == Area.PROSODY.value


def test_a_file_the_step_did_not_touch_is_not_attributed_to_it(project):
    layout = ProjectLayout(project)
    ledger = ProvenanceLedger(project)
    _write(layout, Area.PROSODY, "old.json", {"a": 1})
    before = ledger.snapshot()
    _write(layout, Area.SUBTITLE_SEGMENTS, "new.json", {"b": 2})
    recs = ledger.observe("render_subtitles", "run-1", before, ledger.snapshot())
    assert [r.path for r in recs] == [
        "pipeline_output/steps/4_05_render_subtitles/new.json"]


def test_a_rewritten_file_is_attributed_to_the_step_that_rewrote_it(project):
    layout = ProjectLayout(project)
    ledger = ProvenanceLedger(project)
    p = _write(layout, Area.PROSODY, "clip_001_prosody.json", {"v": 1})
    ledger.observe("prosody_analysis", "run-1", {}, ledger.snapshot())
    before = ledger.snapshot()
    p.write_text(json.dumps({"v": 2, "changed": "yes"}), encoding="utf-8")
    recs = ledger.observe("prosody_analysis", "run-2", before, ledger.snapshot())
    assert len(recs) == 1
    assert ledger.of(p).run_id == "run-2", "the newest run owns the file now"


def test_the_provenance_store_is_append_only(project):
    """A run is a thing that happened. Run 4 does not unmake run 3."""
    layout = ProjectLayout(project)
    ledger = ProvenanceLedger(project)
    p = _write(layout, Area.PROSODY, "c.json", {"v": 1})
    ledger.observe("prosody_analysis", "run-1", {}, ledger.snapshot())
    before = ledger.snapshot()
    p.write_text(json.dumps({"v": 2}), encoding="utf-8")
    ledger.observe("prosody_analysis", "run-2", before, ledger.snapshot())

    raw = layout.read_path(Area.PROVENANCE, "artifacts.jsonl").read_text(
        encoding="utf-8").strip().splitlines()
    assert len(raw) == 2
    assert {json.loads(r)["run_id"] for r in raw} == {"run-1", "run-2"}


def test_run_ids_sort_chronologically(project):
    a = new_run_id(now=1_700_000_000, pid=11)
    b = new_run_id(now=1_700_000_060, pid=11)
    assert a < b
    assert new_run_id(now=1_700_000_000, pid=11) != new_run_id(
        now=1_700_000_000, pid=12)


# ── Declaration: the weaker kind, labelled as such ──────────────────

def test_an_unobserved_file_falls_back_to_the_layouts_declaration(project):
    layout = ProjectLayout(project)
    p = _write(layout, Area.TEMPORAL_INDEX, "clip_001.json", {"clip_id": "c1"})
    rec = ProvenanceLedger(project).of(p)
    assert rec.method == DECLARED
    assert rec.step_id == "temporal_index"
    assert rec.run_id is None, "a declaration knows no run"


def test_an_observation_beats_the_declaration(project):
    layout = ProjectLayout(project)
    ledger = ProvenanceLedger(project)
    before = ledger.snapshot()
    p = _write(layout, Area.TEMPORAL_INDEX, "clip_001.json", {"c": 1})
    ledger.observe("temporal_index", "run-9", before, ledger.snapshot())
    rec = ledger.of(p)
    assert rec.method == OBSERVED and rec.run_id == "run-9"


def test_every_declared_producer_is_a_real_step_or_a_named_non_step():
    """A declaration pointing at a step id that does not exist is worse
    than no declaration - it reads as an answer."""
    from library.tools.project_layout import NON_STEP_PRODUCERS
    from library.tools.run_traceback import implemented_step_ids, load_dag

    known = ({n["id"] for n in load_dag()["nodes"]}
             | implemented_step_ids()
             | set(NON_STEP_PRODUCERS))
    for area, spec in AREAS.items():
        for producer in spec.produced_by:
            assert producer in known, (
                f"{area.value} declares producer {producer!r}, which is "
                f"neither a DAG node nor a NON_STEP_PRODUCERS entry")


# ── Unknown stays unknown ───────────────────────────────────────────

def test_a_step_that_is_not_in_the_dag_is_named_as_not_wired(project):
    """`object_segmentation` is implemented and unwired (AGENTS.md
    section 3). Its area declares it, which is true - and the reader has
    to be told nothing runs it."""
    layout = ProjectLayout(project)
    _write(layout, Area.SEGMENTATION, "clip_001_segmentation.json", {"a": 1})
    md = render_artifact_index(build_traceback(project, dag=DAG))
    assert "NOT wired into the DAG" in md
    assert "`object_segmentation`" in md


def test_the_unwired_steps_are_exactly_the_ones_agents_md_names():
    """One step is UNWIRED - `object_segmentation` (1.06).
    `prosody_analysis` (1.05) was re-wired on 2026-09-01 - the captain
    ruled that its deterministic measurements are unbiased signal the
    model lacks. `ocr_extraction` is not unwired either: since #245 it
    has a DAG node and is DESELECTED BY DEFAULT instead, which is a
    property of a run rather than of the pipeline. `select_reels` (3.04)
    was created unwired by PR #504 (reel selection runs on a finished
    cut, nothing in the DAG consumes its output); PR #513 wired it in
    and deselected it by default, reversing #504's explicit choice -
    a DAG node with declared dependencies is auditable by the same tests
    that check every other step, while a separately driven caller is
    invisible to them."""
    from library.tools.run_traceback import unwired_step_ids
    from library.tools.run_scope import DESELECTED_BY_DEFAULT

    assert unwired_step_ids() == {"object_segmentation"}
    assert "prosody_analysis" not in unwired_step_ids()
    assert "ocr_extraction" not in unwired_step_ids()
    assert "ocr_extraction" in DESELECTED_BY_DEFAULT
    assert "select_reels" not in unwired_step_ids()
    assert "select_reels" in DESELECTED_BY_DEFAULT


def test_an_unsorted_file_reads_as_unknown_not_as_organizes_work(project):
    """`organize` MOVED these. Saying it PRODUCED them would turn an
    admitted unknown back into an attribution."""
    layout = ProjectLayout(project)
    p = _write(layout, Area.UNSORTED, "media/001.mov", "x")
    rec = ProvenanceLedger(project).of(p)
    assert rec.method == UNKNOWN
    assert rec.step_id is None


def test_the_artifact_index_gives_unknowns_their_own_section(project):
    layout = ProjectLayout(project)
    _write(layout, Area.UNSORTED, "media/001.mov", "x")
    md = render_artifact_index(build_traceback(project, dag=DAG))
    assert "Still unaccounted for" in md
    assert "001.mov" in md


# ── Derived-from: read, never inferred ──────────────────────────────

def test_a_source_is_read_out_of_the_artifact(project, tmp_path):
    layout = ProjectLayout(project)
    src = str(tmp_path / "raw" / "IMG_1806.MOV")
    p = _write(layout, Area.TEMPORAL_INDEX, "clip_001.json",
               {"clip_id": "clip_001", "source_file": src})
    assert ProvenanceLedger(project).of(p).derived_from == [src]


@pytest.mark.parametrize("key", [
    "source_file", "file_path", "audio_file", "clip_path", "video_path"])
def test_each_recorded_source_key_is_read(project, key, tmp_path):
    layout = ProjectLayout(project)
    src = str(tmp_path / "raw" / "IMG_1806.MOV")
    p = _write(layout, Area.PROSODY, f"{key}.json", {key: src})
    assert read_declared_sources(p) == [src]


def test_an_artifact_naming_no_source_gets_no_link(project):
    layout = ProjectLayout(project)
    p = _write(layout, Area.PROSODY, "c.json", {"clip_id": "clip_001"})
    assert read_declared_sources(p) == [], (
        "a clip_id is not a path; guessing the file from it is the "
        "inference this must not make")


def test_a_source_is_never_guessed_from_the_filename(project):
    layout = ProjectLayout(project)
    p = _write(layout, Area.TEMPORAL_INDEX, "clip_001.json", {"clip_id": "c"})
    assert ProvenanceLedger(project).of(p).derived_from == []


def test_a_non_json_artifact_is_not_opened_looking_for_a_source(project):
    layout = ProjectLayout(project)
    p = layout.write_path(Area.SUBTITLE_SEGMENTS, "sub_000.mov")
    p.write_bytes(b"\x00" * 32)
    assert read_declared_sources(p) == []


def test_a_huge_json_artifact_is_skipped_rather_than_parsed(project, monkeypatch):
    layout = ProjectLayout(project)
    monkeypatch.setattr(provenance, "SOURCE_SCAN_CEILING_BYTES", 8)
    p = _write(layout, Area.PROSODY, "big.json", {"source_file": "/a/b.mov"})
    assert read_declared_sources(p) == []


# ── The per-step export ─────────────────────────────────────────────

def test_a_per_step_export_is_attributed_to_its_step(project):
    layout = ProjectLayout(project)
    _write(layout, Area.OUTPUT_ROOT, "scan.json", {"total_files": 3})
    ledger = ProvenanceLedger(project, step_ids=["scan"])
    rec = ledger.of(layout.read_path(Area.OUTPUT_ROOT, "scan.json"))
    assert rec.step_id == "scan" and rec.method == DECLARED


def test_an_export_name_that_is_not_a_step_is_not_attributed(project):
    layout = ProjectLayout(project)
    _write(layout, Area.OUTPUT_ROOT, "notes.json", {"a": 1})
    rec = ProvenanceLedger(project, step_ids=["scan"]).of(
        layout.read_path(Area.OUTPUT_ROOT, "notes.json"))
    assert rec.step_id != "notes"


def test_the_export_naming_rule_lives_with_the_naming():
    """Re-deriving it in provenance.py is how the two drift apart."""
    from library.tools.step_exporter import step_id_for_export

    assert step_id_for_export("temporal_index.json") == "temporal_index"
    assert step_id_for_export("temporal_index.summary.md") == "temporal_index"
    assert step_id_for_export("thumbnail.png") is None


# ── What gets walked ────────────────────────────────────────────────

def test_the_exports_are_walked_too(project):
    """"What is Pipeline_Edit.mp4" is the first question anyone asks."""
    layout = ProjectLayout(project)
    p = layout.write_path(Area.EXPORTS, "Pipeline_Edit.mp4")
    p.write_bytes(b"\x00" * 16)
    paths = [r.path for r in ProvenanceLedger(project).all_artifacts()]
    assert "exports/Pipeline_Edit.mp4" in paths


def test_archived_copies_are_counted_not_attributed(project):
    """An archived copy is not the artifact the step wrote."""
    layout = ProjectLayout(project)
    p = layout.write_path(Area.BACKUPS, "run_archives", "old", "clip_001.json")
    p.write_text("{}", encoding="utf-8")
    ledger = ProvenanceLedger(project)
    assert not [r for r in ledger.all_artifacts() if "run_archives" in r.path]
    assert ledger.archived_file_count() >= 1
    archived = [r.path for r in ledger.all_artifacts(include_archives=True)
                if "run_archives" in r.path]
    assert archived, "asking for them explicitly must list them"


def test_the_provenance_store_does_not_record_itself(project):
    layout = ProjectLayout(project)
    ledger = ProvenanceLedger(project)
    _write(layout, Area.PROSODY, "c.json", {"a": 1})
    ledger.observe("prosody_analysis", "run-1", {}, ledger.snapshot())
    before = ledger.snapshot()
    _write(layout, Area.PROSODY, "d.json", {"a": 2})
    recs = ledger.observe("prosody_analysis", "run-2", before, ledger.snapshot())
    assert [r.path for r in recs] == [
        "pipeline_output/steps/1_05_prosody_analysis/d.json"]


# ── The traceback document ──────────────────────────────────────────

def test_every_step_appears_in_execution_order(project):
    md = render_traceback(build_traceback(project, dag=DAG))
    positions = [md.index(f"`{n['id']}`") for n in DAG["nodes"]]
    assert positions == sorted(positions)


def test_a_step_says_what_it_consumed_and_from_which_step(project):
    md = render_traceback(build_traceback(project, dag=DAG))
    assert "`temporal_index` from `temporal_index`" in md
    assert "`raw_footage_files` from `scan`" in md


def test_a_step_with_no_inbound_edge_says_so(project):
    md = render_traceback(build_traceback(project, dag=DAG))
    assert "the DAG gives it no inbound edge" in md


def test_timing_and_verdict_come_from_the_ledgers(project):
    md = render_traceback(build_traceback(project, dag=DAG))
    assert "completed 2026-08-17T17:09:28 in 9m 40s" in md
    assert "preflight stage" in md


def test_a_failed_step_reads_as_failed_with_its_error(project):
    md = render_traceback(build_traceback(project, dag=DAG))
    assert "**FAILED**" in md
    assert "no timeline" in md or "Step failed (exit 1)" in md


def test_a_step_that_never_ran_says_so(project):
    md = render_traceback(build_traceback(project, dag=DAG))
    assert "not run - no entry in either ledger" in md


def test_the_header_separates_what_was_observed_from_what_was_declared(project):
    layout = ProjectLayout(project)
    _write(layout, Area.PROSODY, "c.json", {"a": 1})
    md = render_traceback(build_traceback(project, dag=DAG))
    assert "No run has been observed yet in this project" in md
    assert "the layout's declaration, not an" in md


def test_an_observed_run_stops_the_reconstruction_warning(project):
    layout = ProjectLayout(project)
    ledger = ProvenanceLedger(project)
    before = ledger.snapshot()
    _write(layout, Area.PROSODY, "c.json", {"a": 1})
    ledger.observe("prosody_analysis", "run-1", before, ledger.snapshot())
    ledger.start_run("run-1", mode="full-auto")
    md = render_traceback(build_traceback(project, dag=DAG))
    assert "No run has been observed yet" not in md
    assert "run-1" in md


def test_an_area_with_two_declared_producers_names_both(project):
    """`exports/` is written by `render` AND `validate`. Printing the
    first would be an answer the pipeline does not have."""
    from library.tools.provenance import ProvenanceLedger

    layout = ProjectLayout(project)
    p = layout.write_path(Area.EXPORTS, "Pipeline_Edit.mp4")
    p.write_bytes(b"\x00" * 8)
    rec = ProvenanceLedger(project).of(p)
    assert rec.step_id is None
    assert set(rec.candidates) == {"render", "validate"}

    data = build_traceback(project, dag=DAG)
    md = render_artifact_index(data)
    assert "`render` or `validate`" in md
    tb = render_traceback(data)
    assert "shared, not attributed" in tb
    assert "no run has been observed that would settle it" in tb


def test_a_failed_step_shows_the_reason_not_just_the_preamble(project):
    """"Step failed (exit 1):" is the preamble. The reason is two lines
    further down, and it is the whole point of reading this."""
    layout = ProjectLayout(project)
    state = json.loads(layout.pipeline_data_path.read_text(encoding="utf-8"))
    state["step_errors"]["render"] = (
        "Step failed (exit 1):\n  stderr: Validation failed: 1 issue(s)\n"
        "  - [audio_levels] LUFS: -17.47, True Peak: 1.85\n")
    layout.pipeline_data_path.write_text(json.dumps(state), encoding="utf-8")
    md = render_traceback(build_traceback(project, dag=DAG))
    assert "True Peak: 1.85" in md


def test_the_clip_to_footage_table_comes_from_the_fingerprints(project):
    md = render_traceback(build_traceback(project, dag=DAG))
    assert "clip_001" in md and "IMG_1806.MOV" in md


def test_both_documents_are_regenerable(project):
    layout = ProjectLayout(project)
    _write(layout, Area.PROSODY, "c.json", {"a": 1})
    with patch("library.tools.run_traceback.load_dag", return_value=DAG):
        first = write_traceback(project)
        a = Path(first["traceback"]).read_text(encoding="utf-8")
        second = write_traceback(project)
        b = Path(second["traceback"]).read_text(encoding="utf-8")
    # Only the generation stamp may differ.
    def strip(text):
        return "\n".join(line for line in text.splitlines()
                          if not line.startswith("Generated:"))

    assert strip(a) == strip(b)


def test_it_works_on_a_project_with_no_provenance_ledger_at_all(project):
    """Project 001. The run already happened; nothing was watching."""
    layout = ProjectLayout(project)
    _write(layout, Area.TEMPORAL_INDEX, "clip_001.json", {"clip_id": "c1"})
    assert not layout.read_path(Area.PROVENANCE, "artifacts.jsonl").exists()
    with patch("library.tools.run_traceback.load_dag", return_value=DAG):
        w = write_traceback(project)
    assert w["observed"] == 0 and w["declared"] >= 1
    assert Path(w["traceback"]).is_file()
    assert Path(w["artifact_index"]).is_file()


def test_the_documents_land_where_the_layout_says(project):
    with patch("library.tools.run_traceback.load_dag", return_value=DAG):
        w = write_traceback(project)
    assert Path(w["traceback"]).name == TRACEBACK_FILE
    assert Path(w["artifact_index"]).name == ARTIFACT_INDEX_FILE
    ProjectLayout(project).assert_writable(w["traceback"])


# ── The runner records it ───────────────────────────────────────────

@patch("library.processes.edit_video.run_pipeline.run_deterministic_step")
def test_a_real_run_records_what_it_wrote(mock_det, tmp_path):
    """The end-to-end property: run the runner, read the folder back."""
    from library.processes.edit_video.run_pipeline import run_pipeline

    project_dir = tmp_path / "mock_project"
    project_dir.mkdir()
    shutil.copy(FIXTURE_STATE, project_dir / "pipeline_data.json")
    (project_dir / "project.yaml").write_text(
        "name: Mock\nslug: mock\n", encoding="utf-8")
    mock_det.return_value = {"spine": {"structure": []}}

    small_dag = {
        "id": "edit_video",
        "nodes": [{"id": "mesh_spine", "name": "Mesh Audio Spine",
                   "step_ref": "steps/step_2_05_mesh_spine"}],
        "edges": [],
    }
    with patch("library.processes.edit_video.run_pipeline.load_dag",
               return_value=small_dag), \
         patch("library.processes.edit_video.run_pipeline."
               "get_step_implementation",
               return_value={"type": "deterministic", "entry": "step.py",
                             "manifest": {}}):
        run_pipeline(str(project_dir))

    ledger = ProvenanceLedger(project_dir)
    observed = ledger.observed_records()
    assert observed, "the run recorded nothing it wrote"
    assert {r.step_id for r in observed.values()} == {"mesh_spine"}
    assert all(r.method == OBSERVED for r in observed.values())

    runs = ledger.runs()
    assert len(runs) == 1 and runs[0].steps == ["mesh_spine"]
    assert runs[0].started_at and runs[0].ended_at

    layout = ProjectLayout(project_dir)
    assert layout.read_path(Area.OUTPUT_ROOT, TRACEBACK_FILE).is_file()
    md = layout.read_path(Area.OUTPUT_ROOT, TRACEBACK_FILE).read_text(
        encoding="utf-8")
    assert "No run has been observed yet" not in md
    assert runs[0].run_id in md
