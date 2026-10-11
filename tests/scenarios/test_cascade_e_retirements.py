"""Lane E (cascade report section 5): retire the redundant stores.

E1 (R5): the legacy `subtitle_overlays/` renders are ignored - the
wording scan and the drift witness read the pipeline's own caption
outputs (step-area segments plus the render ledger) instead.
E2 (R6): the standalone `touchup_*.json` receipt files are folded
into the undo journal entry, which already carries the request.
E3 (R2/R9): `build_signatures` and the duplicate caption hashes
retire into the per-reel derived registry at build time and the
model's render receipt (`details.build`) at deliver time.
E4 (R7): the generated docs render the Project Model linkage - the
run's model revision in `RUN-TRACEBACK.md`/`ARTIFACTS.md`, the
note's stable id and clip-use anchor in `ROUTED-NOTES.md`.

Every fixture is built under ``tmp_path``; no test reaches a real
project, Resolve, or ffmpeg. Each test proves the reader gets the
same value from the new source it got from the retired store.
"""

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from library.tools import cascade_graph as cascade
from library.tools import display_drift
from library.tools import layer_coherence
from library.tools import plan_provenance as provenance
from library.tools import reel_layer_links as links
from library.tools import reel_rebuild_need as need
from library.tools import undo_journal as journal
from library.tools.marker_routing import (
    OUTCOME_UNROUTED,
    RoutedNote,
    render_report,
)
from library.tools.project_layout import ProjectLayout
from library.tools.provenance import (
    ArtifactRecord,
    ProvenanceLedger,
)
from library.tools.run_traceback import (
    render_artifact_index,
    render_traceback,
)


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    ProjectLayout(root).ensure()
    (root / "project.yaml").write_text("slug: t\n", encoding="utf-8")
    return root


# ── E1 (R5): the legacy overlays are ignored ─────────────────────────


def test_retired_subtitle_overlays_are_ignored_but_step_output_still_flags(
        tmp_path):
    """The wording scan and the drift witness dropped the legacy root.

    The same heard form in the pipeline's own step output still
    flags; the legacy-only copy is ignored, never flagged and never
    fingerprinted.
    """
    root = _project(tmp_path)
    legacy = root / "subtitle_overlays"
    legacy.mkdir(parents=True)
    (legacy / "o.json").write_text(json.dumps(
        {"cards": [{"text": "say lucy to the camera"}]}))
    steps = root / "pipeline_output" / "steps"
    steps.mkdir(parents=True, exist_ok=True)
    cards = steps / "cards.json"
    cards.write_text(json.dumps(
        {"cards": [{"text": "say lucy to the camera"}]}))
    corrections = [{"heard": "lucy", "correct": "Lucie", "id": "t1"}]

    rows = layer_coherence.check_wording(str(root), corrections)
    assert rows, "the step-area copy must still flag"
    # Relative to the project: the tmp dir itself may name the
    # legacy store (pytest names it after this test), so compare
    # the project-relative path, not the absolute one.
    rels = [os.path.relpath(row["layer_file"], str(root)) for row in rows]
    assert all(not rel.startswith("subtitle_overlays" + os.sep)
               for rel in rels)
    assert any(rel.startswith("pipeline_output" + os.sep) for rel in rels)

    cards.unlink()
    assert layer_coherence.check_wording(str(root), corrections) == [], (
        "a legacy-only file is ignored, never flagged")

    assert not [p for p in display_drift.display_files(str(root))
                if p.startswith("subtitle_overlays")], (
        "the drift witness fingerprints no legacy file")


# ── E2 (R6): the touchup receipt folds into the journal ──────────────


def test_touchup_receipt_folds_into_the_journal_entry(tmp_path):
    """The reader (`read_entry`) serves the whole act from the entry.

    The account lands under `receipt`, the request (`spec`) is
    untouched, and no standalone `touchup_*.json` file is written.
    """
    from library.tools import master_touchup, reel_touchup

    project = _project(tmp_path)
    entry_id = "20260101T000000Z-reel-01-abc123"
    journal.write_entry(str(project), {
        "format": journal.JOURNAL_FORMAT, "id": entry_id, "reel": 1,
        "final": "Reel 01", "resolve_project": "P", "batch": "",
        "spec": {"edits": [{"op": "swap"}]}, "gate_class": "composed",
        "status": journal.STATUS_APPLIED,
        "before": {"tracks": []}, "after": {"tracks": []},
    })
    receipt = {"journal": entry_id, "seconds": 1.5,
               "summary": "swapped the cover"}

    for writer in (reel_touchup._write_receipt,
                   master_touchup._write_receipt):
        path = writer(str(project), "Reel 01", dict(receipt), entry_id)
        back = journal.read_entry(str(project), entry_id)
        assert back["receipt"]["seconds"] == 1.5
        assert back["receipt"]["summary"] == "swapped the cover"
        assert back["receipt"]["receipt_path"] == path
        assert path == str(Path(journal.entry_dir(str(project), entry_id))
                           / journal.ENTRY_FILENAME)
        assert back["spec"] == {"edits": [{"op": "swap"}]}, (
            "the folded request is untouched")

    review = project / "pipeline_output" / "review"
    assert [p for p in review.iterdir()
            if p.name.startswith("touchup_")
            or p.name.startswith("master_touchup_")] == [], (
        "no standalone receipt file is written")


# ── E3 (R2/R9): the registry and the receipt carry the hashes ────────


def _legacy_provenance() -> dict:
    return {
        "plan_content_hash": "p" * 64,
        "built_reels": ["Reel 01", "Reel 09"],
        "built_at_reels": {"Reel 01": "t", "Reel 09": "t"},
        "built_with": {"Reel 01": "c", "Reel 09": "c"},
        "caption_hashes": {"Reel 01": "v1:cap01",
                           "Reel 09": "v1:cap09"},
        "footage_binding_hashes": {"Reel 01": "v1:bind01",
                                   "Reel 09": "v1:bind09"},
        "build_signatures": {
            "Reel 01": {"derivation": "d" * 64, "carried": "c" * 64},
            "Reel 09": {"derivation": "e" * 64, "carried": "f" * 64},
        },
    }


def _review_with_both(tmp_path: Path) -> Path:
    review = tmp_path / "project" / "pipeline_output" / "review"
    review.mkdir(parents=True)
    (review / provenance.PROVENANCE_FILENAME).write_text(
        json.dumps(_legacy_provenance()))
    # The registry carries Reel 01's values under the edge names -
    # the same values the build filed in both stores.
    cascade.update_registry_from_build(
        str(review), "Reel 01", derivation="d" * 64, carried="c" * 64,
        caption_binding="v1:cap01", footage_binding="v1:bind01")
    return review


def test_rebuild_reader_gets_the_same_hashes_from_the_registry(tmp_path):
    """The dirty walk decides identically off the registry alone.

    With the legacy maps present the helper answers from the
    registry; with them deleted it answers the same. A reel the
    registry never named still answers from the legacy maps.
    """
    review = _review_with_both(tmp_path)

    found = provenance.reel_content_hashes(str(review), "Reel 01")
    assert found["source"] == "registry"
    assert (found["derivation"], found["carried"],
            found["caption_binding"], found["footage_binding"]) == (
        "d" * 64, "c" * 64, "v1:cap01", "v1:bind01")

    doc = json.loads((review / provenance.PROVENANCE_FILENAME)
                     .read_text(encoding="utf-8"))
    merged = provenance.provenance_with_registry(str(review)) or {}
    assert (merged.get("caption_hashes") or {}).get("Reel 01") == "v1:cap01"
    assert (merged.get("build_signatures") or {}).get("Reel 01") == {
        "derivation": "d" * 64, "carried": "c" * 64}

    legacy_only = provenance.reel_content_hashes(str(review), "Reel 09")
    assert legacy_only["source"] == "provenance", (
        "a reel that predates the registry still answers")
    assert (legacy_only["caption_binding"],
            legacy_only["footage_binding"]) == ("v1:cap09", "v1:bind09")

    for key in ("caption_hashes", "footage_binding_hashes",
                "build_signatures"):
        doc.pop(key, None)
    (review / provenance.PROVENANCE_FILENAME).write_text(json.dumps(doc))
    again = provenance.reel_content_hashes(str(review), "Reel 01")
    assert again == found, (
        "deleting the retired maps changes nothing the reader sees")

    assert provenance.reel_content_hashes(
        str(review), "Reel 99")["source"] == "absent"

    # The dirty walk itself: the recorded half from the helper
    # decides exactly as the recorded half from the legacy maps.
    recorded_legacy = {"derivation": "d" * 64, "carried": "c" * 64}
    recorded_new = {"derivation": found["derivation"],
                    "carried": found["carried"]}
    first = need.decide("Reel 01", "d" * 64, "c" * 64, recorded_legacy)
    second = need.decide("Reel 01", "d" * 64, "c" * 64, recorded_new)
    assert (second.action, second.reason) == (first.action, first.reason)
    assert second.leave_alone, "matching halves still leave the reel alone"


def test_deliver_receipt_carries_the_same_hashes(tmp_path):
    """`details.build` converges the retired copies onto the receipt."""
    review = _review_with_both(tmp_path)
    hashes = provenance.reel_content_hashes(str(review), "Reel 01")
    model = {"receipts": [{"id": "receipt:reel-01", "status": "ok",
                           "details": {"expected_frames": 10}}]}
    updated, report = links.attach_build_hashes(
        model, "reel-01", hashes, plan_hash="p" * 64)
    assert report["attached"]
    assert updated["receipts"][0]["details"]["build"] == {
        "plan_hash": "p" * 64, "derivation": "d" * 64,
        "carried": "c" * 64, "caption_binding": "v1:cap01",
        "footage_binding": "v1:bind01",
    }
    assert updated["receipts"][0]["details"]["expected_frames"] == 10, (
        "whatever details already carried is kept")

    assert model["receipts"][0].get("details", {}).get("build") is None, (
        "the input model is never mutated")

    missing, report = links.attach_build_hashes(model, "reel-99", hashes)
    assert not report["attached"] and missing == model

    empty, report = links.attach_build_hashes(
        model, "reel-01", {"derivation": "", "carried": "",
                           "caption_binding": "", "footage_binding": ""})
    assert not report["attached"] and empty == model, (
        "a reel no store names refuses rather than stamping blanks")


# ── E4 (R7): the generated docs render the model linkage ─────────────


def test_generated_docs_render_the_runs_model_revision(tmp_path):
    """The ledger stamps the head revision; both docs render it.

    A run that predates the model renders `pre-model`, never a
    revision it did not have.
    """
    project = _project(tmp_path)
    ledger = ProvenanceLedger(project)
    ledger.start_run("run-7", mode="m")
    ledger.end_run("run-7", "ok", ["scan"], model_revision="7")
    ledger.start_run("run-old", mode="m")
    ledger.end_run("run-old", "ok", ["scan"], model_revision="")
    runs = {r.run_id: r for r in ledger.runs()}
    assert runs["run-7"].model_revision == "7"
    assert runs["run-old"].model_revision == "", (
        "a run with no model records pre-model, never revision zero")

    data = {
        "project": str(project), "runs": list(runs.values()),
        "artifacts": [
            ArtifactRecord(path="pipeline_output/steps/a.json",
                           method="observed", step_id="scan",
                           run_id="run-7"),
            ArtifactRecord(path="pipeline_output/steps/b.json",
                           method="observed", step_id="scan",
                           run_id="run-old"),
        ],
        "archived_file_count": 0, "started_at": "", "last_updated": "",
        "generated_at": "t", "steps": [], "source_fingerprints": {},
        "unwired_steps": [],
    }
    traceback = render_traceback(data)
    assert "| 7 |" in traceback
    assert "pre-model" in traceback

    index = render_artifact_index(data)
    assert "| 7 |" in index, "the file shows its run's model era"
    assert "pre-model" in index


def test_routed_notes_render_the_note_model_link(tmp_path):
    """A linked note names its stable id and clip use in the doc."""
    project = _project(tmp_path)

    def note(**overrides):
        fields = dict(note_id="timeline:clip:1", source="marker",
                      name="m", note="n", text="tighten this cut",
                      frame=108000, timecode="01:00:00:00",
                      frame_in_timeline_space=108000,
                      unplaced_reason="", collected_at="t",
                      timeline="Reel 01", pull_file="p.json",
                      target={"kind": "moment"},
                      outcome=OUTCOME_UNROUTED, reason="no route",
                      stable_note_id="note_abc",
                      clip_use_id="use:reel01:0001")
        fields.update(overrides)
        return RoutedNote(**fields)

    linked = render_report(str(project), [note()], timelines=None)
    assert "- **Model link**: stable `note_abc`; clip use " \
        "`use:reel01:0001`" in linked

    unlinked = render_report(
        str(project),
        [note(stable_note_id="", clip_use_id="")], timelines=None)
    assert "Model link" not in unlinked, (
        "a note that predates the linkage shows nothing, and the "
        "routing record keeps the full account")
