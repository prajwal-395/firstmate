"""Task-dependent editorial precision (F02): what each task may place.

A browse and a cut used to receive the same rows, and a
frame-approximate observation could land on the timeline as though it
were an exact event boundary. Every test here names that defect.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from library.tools import edit_candidates as ec
from library.tools import edit_precision
from library.tools.analysis import footage_query
from library.tools.analysis.footage_query import FootageIndex, build_index


def _write(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def project(tmp_path) -> Path:
    """One clip that speaks on camera: an exact row and several approximate ones."""
    root = tmp_path / "proj"
    steps = root / "pipeline_output" / "steps"
    _write(root / "pipeline_data.json", {
        "step_outputs": {"catalog": {"clip_catalog": [
            {"clip_id": "clip_001", "filename": "IMG_0001.MOV",
             "source_file": str(root / "raw" / "IMG_0001.MOV"),
             "path": str(root / "raw" / "IMG_0001.MOV"),
             "duration_seconds": 20.0},
        ]}},
    })
    _write(steps / "1_04_temporal_index" / "index" / "clip_001.json", {
        "clip_id": "clip_001", "duration": 20.0,
        "speech_regions": [
            {"start": 1.0, "end": 3.0, "text": "we need to find a parking spot",
             "words": [{"word": "parking", "start": 2.0, "end": 2.4}]},
        ],
        "motion_energy": {"sample_rate_hz": 1, "values": [0.5] * 20},
    })
    _write(steps / "1_03_semantic_analysis" / "clip_profile_IMG_0001_v3.json", {
        "clip_id": "IMG_0001",
        "file_path": str(root / "raw" / "IMG_0001.MOV"),
        "duration_s": 20.0,
        "scene": [{"start": 0.0, "end": 20.0, "location": "Outdoor parking lot",
                   "type": "outdoor", "lighting": "Daylight",
                   "notable_features": ["Parked cars"]}],
        "camera": [{"start": 0, "end": 20, "mode": "selfie",
                    "framing": "close-up", "stability": "shaky",
                    "movement": "walking"}],
        "actions": [{"window": [0, 10], "actions": [
            {"start": 0, "end": 10, "action": "The man walks across the lot",
             "body_language": "He smiles broadly", "speech_cue": None}]}],
        "objects": [{"label": "red bicycle", "category": "object",
                     "role": "background", "readable_text": None,
                     "appearances": [[2.0, 5.0]]}],
        "assessment": {"content_type": "person_talking_to_camera"},
        "analysis_metadata": {"pipeline_version": "v3"},
    })
    return root


def _stub_embedder():
    def encode(texts):
        rows = []
        for text in texts:
            vector = np.zeros(26, dtype="float32")
            for char in (text or "").lower():
                if "a" <= char <= "z":
                    vector[ord(char) - 97] += 1.0
            norm = np.linalg.norm(vector) or 1.0
            rows.append(vector / norm)
        return np.vstack(rows) if rows else np.zeros((0, 26), dtype="float32")
    return encode, "test-stub"


@pytest.fixture
def index(project, tmp_path, monkeypatch):
    monkeypatch.setattr(footage_query, "load_embedder", _stub_embedder)
    index_dir = tmp_path / "index"
    build_index(project, index_dir=index_dir)
    return FootageIndex(project, index_dir=index_dir)


# ─── The vocabulary ───────────────────────────────────────────────


def test_four_precisions_coarsest_first():
    assert edit_precision.precisions() == (
        "browse", "concept", "transient", "cut")


def test_every_precision_has_a_requirement_with_a_reader():
    for name in edit_precision.precisions():
        requirement = edit_precision.requirement_for(name)
        assert requirement["description"], name
        assert requirement["verification"], name
        assert "allows_approximate" in requirement, name


def test_unknown_precision_fails_loudly():
    with pytest.raises(KeyError):
        edit_precision.requirement_for("cinematic")
    with pytest.raises(KeyError):
        edit_precision.annotate_hit({"kind": "scene"}, "cinematic")
    with pytest.raises(KeyError):
        ec.candidates_from_retrieval([], precision="cinematic")


# ─── What each row carries ────────────────────────────────────────


def test_only_measured_words_are_frame_exact():
    """A speech hit's matching words carry their own source-clock
    timings; every other row's span is where to look, not a boundary."""
    exact = edit_precision.classify_hit(
        {"kind": "speech",
         "word_hits": [{"word": "parking", "start": 2.0, "end": 2.4}]})
    assert exact["temporal_precision"] == "frame_exact"
    for hit in ({"kind": "speech"},
                {"kind": "scene"}, {"kind": "action"},
                {"kind": "object"}, {"kind": "camera"},
                {"kind": "chunk"}, {"kind": "ocr"}, {"kind": "sound"}):
        classified = edit_precision.classify_hit(hit)
        assert classified["temporal_precision"] == "frame_approximate", hit
        assert classified["basis"], hit


def test_a_carried_classification_is_kept_not_redecided():
    hit = {"kind": "speech",
           "word_hits": [{"word": "parking", "start": 2.0, "end": 2.4}],
           "temporal_precision": "frame_approximate"}
    assert edit_precision.classify_hit(hit)["temporal_precision"] == \
        "frame_approximate"


def test_transient_names_what_would_verify_it():
    annotated = edit_precision.annotate_hit({"kind": "action"}, "transient")
    block = annotated["precision"]
    assert block["cut_allowed"] is False
    assert block["needs_verification"] is True
    assert "frame_exact" in block["verify_with"]


# ─── The edit path refuses an approximate boundary for a cut ──────


def _recorded_plan_hits():
    """A recorded plan's shape: one exact speech row among approximate ones."""
    return [
        {"source_id": "IMG_0001", "start_seconds": 0.0, "end_seconds": 20.0,
         "rank": 0, "kind": "scene"},
        {"source_id": "IMG_0001", "start_seconds": 0.0, "end_seconds": 10.0,
         "rank": 1, "kind": "action"},
        {"source_id": "IMG_0001", "start_seconds": 2.0, "end_seconds": 2.4,
         "rank": 2, "kind": "speech",
         "word_hits": [{"word": "parking", "start": 2.0, "end": 2.4}]},
    ]


def test_browse_places_everything_as_before():
    out = ec.candidates_from_retrieval(_recorded_plan_hits())
    assert len(out[0]["moments"]) == 3
    assert out[0]["dropped"] == []
    assert out[0]["precision"] == "browse"


def test_cut_refuses_the_approximate_and_reports_each_refusal():
    """The acceptance: no frame-approximate observation reaches the
    timeline as an exact event boundary, and no refusal is silent."""
    out = ec.candidates_from_retrieval(_recorded_plan_hits(),
                                       precision="cut")
    assert len(out[0]["moments"]) == 1
    moment = out[0]["moments"][0]
    assert (moment["start_seconds"], moment["end_seconds"]) == (2.0, 2.4)
    assert len(out[0]["dropped"]) == 2
    for dropped in out[0]["dropped"]:
        assert "approximate" in dropped["reason"]


def test_the_named_measurement_before_vs_after():
    """Cuts whose boundary came from an approximate observation, on a
    recorded plan: before (browse) vs after (cut), with denominators."""
    hits = _recorded_plan_hits()
    before = ec.candidates_from_retrieval(hits, precision="browse")
    after = ec.candidates_from_retrieval(hits, precision="cut")
    approximate = [h for h in hits
                   if not edit_precision.cut_allowed(h)]
    assert len(approximate) == 2, "the plan carries two approximate rows of three"
    assert len(before[0]["moments"]) == 3
    placed_approximate_before = 2
    placed_approximate_after = sum(
        1 for m in after[0]["moments"]
        if not edit_precision.cut_allowed(
            next(h for h in hits
                 if h["start_seconds"] == m["start_seconds"]
                 and h["end_seconds"] == m["end_seconds"])))
    assert (placed_approximate_before, placed_approximate_after) == (2, 0)
    assert len(after[0]["dropped"]) == 2


def test_check_boundary_for_cut():
    assert edit_precision.check_boundary_for_cut(
        temporal_precision="frame_exact")["status"] == "placed"
    for kwargs in ({"temporal_precision": "frame_approximate"},
                   {"boundary_status": "estimated"},
                   {"boundary_status": "open"},
                   {},
                   {"boundary_precision_seconds": 0.5,
                    "tolerance_seconds": 0.04}):
        verdict = edit_precision.check_boundary_for_cut(**kwargs)
        assert verdict["status"] == "refused", kwargs
        assert verdict["reasons"], kwargs


# ─── The query parameter ──────────────────────────────────────────


def test_search_marks_every_row_and_browse_changes_nothing(index):
    report = index.search_report("parking", top_k=5, mode="lexical")
    assert report["precision_requested"] == "browse"
    assert report["precision_refused"] == []
    assert report["results"], "the fixture must answer this query"
    for row in report["results"]:
        assert row["precision"]["requested"] == "browse"
        assert row["precision"]["temporal_precision"] in (
            "frame_exact", "frame_approximate")


def test_search_at_cut_keeps_only_the_word_timed_row(index):
    browse = index.search_report("parking", top_k=5, mode="lexical")
    cut = index.search_report("parking", top_k=5, mode="lexical",
                              precision="cut")
    assert cut["precision_requested"] == "cut"
    assert all(row["precision"]["cut_allowed"] for row in cut["results"])
    assert all(row["kind"] == "speech" and row.get("word_hits")
               for row in cut["results"])
    refused_kinds = {row["kind"] for row in [
        {"kind": r["kind"]} for r in cut["precision_refused"]]}
    assert refused_kinds - {"speech"}, (
        "the approximate rows must be named in the refusal")
    assert len(cut["results"]) + len(cut["precision_refused"]) == \
        len(browse["results"])


def test_search_at_cut_with_nothing_exact_abstains_honestly(index):
    cut = index.search_report("bicycle", top_k=5, mode="lexical",
                              precision="cut")
    assert cut["results"] == []
    assert cut["abstained"] is True
    assert cut["precision_refused"], "the refusal must name what it refused"
    assert "precision_note" in cut


def test_unknown_precision_refuses_the_query(index):
    with pytest.raises(KeyError):
        index.search_report("parking", precision="cinematic")
