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
from pathlib import Path
from unittest.mock import patch

import pytest

from library.tools import provenance
from library.tools.project_layout import AREAS, Area, ProjectLayout
from library.tools.provenance import (
    OBSERVED,
    UNKNOWN,
    ProvenanceLedger,
    read_declared_sources,
)
from library.tools.run_traceback import (
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

    # A file the step did not touch in its window is not attributed to it.
    before = ledger.snapshot()
    _write(layout, Area.SUBTITLE_SEGMENTS, "new.json", {"b": 2})
    recs = ledger.observe("render_subtitles", "run-1", before, ledger.snapshot())
    assert [r.path for r in recs] == [
        "pipeline_output/steps/4_05_render_subtitles/new.json"]


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

    # The store does not record itself: the next observation names only
    # the step's own new file, not artifacts.jsonl.
    before = ledger.snapshot()
    _write(layout, Area.PROSODY, "d.json", {"a": 2})
    recs = ledger.observe("prosody_analysis", "run-3", before, ledger.snapshot())
    assert [r.path for r in recs] == [
        "pipeline_output/steps/1_05_prosody_analysis/d.json"]


# ── Declaration: the weaker kind, labelled as such ──────────────────

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

def test_an_unsorted_file_reads_as_unknown_not_as_organizes_work(project):
    """`organize` MOVED these. Saying it PRODUCED them would turn an
    admitted unknown back into an attribution."""
    layout = ProjectLayout(project)
    p = _write(layout, Area.UNSORTED, "media/001.mov", "x")
    rec = ProvenanceLedger(project).of(p)
    assert rec.method == UNKNOWN
    assert rec.step_id is None


# ── Derived-from: read, never inferred ──────────────────────────────

def test_recorded_sources_are_read_never_inferred(project, monkeypatch):
    layout = ProjectLayout(project)
    src = str(project / "raw" / "IMG_1806.MOV")
    p = _write(layout, Area.PROSODY, "source_file.json", {"source_file": src})
    assert read_declared_sources(p) == [src]

    p = _write(layout, Area.PROSODY, "c.json", {"clip_id": "clip_001"})
    assert read_declared_sources(p) == [], (
        "a clip_id is not a path; guessing the file from it is the "
        "inference this must not make")

    # A huge JSON artifact is skipped rather than parsed.
    monkeypatch.setattr(provenance, "SOURCE_SCAN_CEILING_BYTES", 8)
    p = _write(layout, Area.PROSODY, "big.json", {"source_file": "/a/b.mov"})
    assert read_declared_sources(p) == []


# ── The per-step export ─────────────────────────────────────────────

# ── What gets walked ────────────────────────────────────────────────

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


# ── The traceback document ──────────────────────────────────────────

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
    # The exports are walked: "what is Pipeline_Edit.mp4" is the first
    # question anyone asks.
    assert "exports/Pipeline_Edit.mp4" in [
        r.path for r in ProvenanceLedger(project).all_artifacts()]

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
