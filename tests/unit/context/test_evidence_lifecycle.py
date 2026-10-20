"""Derived-artifact lifecycle over throwaway project stores.

Stores are built under tmp_path with the real canonical fixture; no
real project is touched and no media is read.
"""
import json
from pathlib import Path

import pytest

from library.tools import evidence_lifecycle, project_model_store
from library.tools.evidence_lifecycle import (
    KNOWN_DERIVED,
    check_artifacts,
    collection_history,
    invalidate_artifact,
    normalize_envelope_preprocessing,
    observation_history,
    record_derivation,
    register_artifact,
    registered_artifacts,
    transform_drift_history,
    typed_confidence,
)

FIXTURE = json.loads(
    Path("native/ren-protocol/fixtures/project-model.json").read_text(encoding="utf-8")
)


def _obs(obs_id, start=0, end=24, note="first"):
    return {"id": obs_id, "asset_id": "asset-source",
            "interval": {"clock_id": "clock-source", "start": start, "end": end},
            "modality": "scene", "payload": {"note": note},
            "provenance_id": "prov-sha256:abc-m4", "confidence": None}


def _project(tmp_path):
    project = tmp_path / "project"
    project_model_store.commit_project_model(
        project, FIXTURE, expected_revision=0, reason="fixture")
    return project


def test_known_derived_names_kinds_not_rebuilds():
    assert {entry["name"] for entry in KNOWN_DERIVED} >= {
        "m9_compat_view", "bm25_index", "minilm_index", "clip_index",
        "unified_anchors", "signals_projection", "one_second_bins",
        "footage_graph_cache"}
    assert all(entry["kind"] and entry["sources"] for entry in KNOWN_DERIVED)


def test_register_check_current_then_stale_model(tmp_path):
    project = _project(tmp_path)
    record = register_artifact(
        project, name="minilm_index", kind="search_index",
        source_digests=["sha256:abc"], model_revision=1,
        producer_versions={"m4": "v3"}, rebuild="owners.search:rebuild",
        owner="vep-mu-retrieval")
    assert record["status"] == "current"
    assert registered_artifacts(project)["minilm_index"]["owner"] == "vep-mu-retrieval"
    report = check_artifacts(project, FIXTURE, model_revision=1)
    assert report["minilm_index"]["status"] == "current"
    project_model_store.merge_evidence_records(
        project, observations=[_obs("obs-new")], expected_revision=1)
    report = check_artifacts(project, FIXTURE, model_revision=2)
    assert report["minilm_index"]["status"] == "stale_model"


def test_unowned_until_owner_registers_rebuild(tmp_path):
    project = _project(tmp_path)
    register_artifact(project, name="bm25_index", kind="search_index",
                      source_digests=["sha256:abc"], model_revision=1)
    report = check_artifacts(project, FIXTURE, model_revision=1)
    assert report["bm25_index"]["status"] == "unowned"


def test_stale_source_and_producer_are_named(tmp_path):
    project = _project(tmp_path)
    register_artifact(project, name="clip_index", kind="search_index",
                      source_digests=["sha256:gone"], model_revision=1,
                      producer_versions={"segmentation": "v1"},
                      rebuild="owners.detection:rebuild", owner="vep-mu-detect")
    report = check_artifacts(project, FIXTURE, model_revision=1,
                             current_producers={"segmentation": "v2"})
    assert report["clip_index"]["status"] == "stale_source"
    assert "sha256:gone" in report["clip_index"]["status_reason"]
    register_artifact(project, name="clip_index", kind="search_index",
                      source_digests=["sha256:abc"], model_revision=1,
                      producer_versions={"segmentation": "v1"},
                      rebuild="owners.detection:rebuild", owner="vep-mu-detect")
    report = check_artifacts(project, FIXTURE, model_revision=1,
                             current_producers={"segmentation": "v2"})
    assert report["clip_index"]["status"] == "stale_producer"


def test_invalidate_needs_reason_and_sticks(tmp_path):
    project = _project(tmp_path)
    register_artifact(project, name="m9_compat_view", kind="compat_view",
                      source_digests=["sha256:abc"], model_revision=1,
                      rebuild="owners.compat:rebuild", owner="vep-mim")
    with pytest.raises(ValueError):
        invalidate_artifact(project, "m9_compat_view", "  ")
    record = invalidate_artifact(project, "m9_compat_view", "source re-cut")
    assert record["status"] == "invalidated"
    report = check_artifacts(project, FIXTURE, model_revision=1)
    assert report["m9_compat_view"]["status"] == "invalidated"
    with pytest.raises(KeyError):
        invalidate_artifact(project, "missing", "nope")
    with pytest.raises(ValueError):
        register_artifact(project, name="", kind="search_index",
                          source_digests=[], model_revision=1)


def test_observation_history_tracks_add_modify_carry(tmp_path):
    project = _project(tmp_path)
    project_model_store.merge_evidence_records(
        project, observations=[_obs("obs-h")], expected_revision=1)
    project_model_store.merge_evidence_records(
        project, observations=[_obs("obs-h", note="second")], expected_revision=2)
    project_model_store.merge_evidence_records(
        project, observations=[_obs("obs-other")], expected_revision=3)
    chain = observation_history(project, "obs-h")
    assert [entry["change"] for entry in chain] == [
        "added", "modified", "carried"]
    assert [entry["revision"] for entry in chain] == [2, 3, 4]
    assert all(entry["event_hash"] for entry in chain)
    assert observation_history(project, "never-seen") == []
    kept = [row for row in project_model_store.load_project_model(project)[1][
        "observations"] if row["id"] != "obs-h"]
    project_model_store.append_model_patch(
        project, {"observations": kept}, expected_revision=4,
        reason="drop superseded row")
    project_model_store.merge_evidence_records(
        project, observations=[_obs("obs-later")], expected_revision=5)
    chain = observation_history(project, "obs-h")
    assert [entry["change"] for entry in chain] == [
        "added", "modified", "carried", "removed"]
    assert chain[-1]["revision"] == 5
    with pytest.raises(ValueError):
        observation_history(project, "")
    with pytest.raises(FileNotFoundError):
        observation_history(tmp_path / "empty", "obs-h")


def test_record_derivation_is_deterministic_and_json_safe():
    first = record_derivation(
        producer="rf-detr", producer_version="nano-v1",
        preprocessing={"resize": [640, 640]}, sampling_policy={"fps": 2},
        source_revision="sha256:abc")
    second = record_derivation(
        producer="rf-detr", producer_version="nano-v1",
        preprocessing={"resize": [640, 640]}, sampling_policy={"fps": 2},
        source_revision="sha256:abc")
    assert first == second
    assert first["derivation_digest"].startswith("sha256:")
    with pytest.raises(ValueError):
        record_derivation(producer="", producer_version="v1")
    with pytest.raises(ValueError):
        record_derivation(producer="p", producer_version="v",
                          preprocessing={"bad": object()})


def _transform_row(uncertainty=11):
    return {"id": "transform-sha256:abc-to-sha256:def",
            "from_clock": "clock-source", "to_clock": "clock-timeline",
            "segments": [{"from": {"clock_id": "clock-source",
                                   "start": 0, "end": 24},
                          "to": {"clock_id": "clock-timeline",
                                 "start": 2, "end": 26},
                          "slope": {"numerator": 1, "denominator": 1},
                          "offset_ticks": 2,
                          "uncertainty_ticks": uncertainty,
                          "invertible": True}],
            "provenance_id": "prov-sha256:abc-m6"}


def _m6_direct():
    return {"content_digest": "sha256:abc",
            "direct_measurement": {"p5_seconds": -0.01,
                                   "p95_seconds": 0.01,
                                   "drift_seconds": 0.002,
                                   "drift_exceeds_tolerance": False}}


def _project_with_transform(tmp_path, uncertainty=11):
    project = _project(tmp_path)
    model = dict(project_model_store.load_project_model(project)[1])
    model["time_transforms"] = [_transform_row(uncertainty)]
    project_model_store.append_model_patch(
        project, {"time_transforms": model["time_transforms"]},
        expected_revision=1, reason="add measured transform")
    return project


def test_collection_history_carries_rows_per_revision(tmp_path):
    project = _project_with_transform(tmp_path)
    chain = collection_history(project, "time_transforms",
                               "transform-sha256:abc-to-sha256:def")
    assert [(entry["change"], entry["revision"]) for entry in chain] == [
        ("added", 2)]
    assert chain[0]["row"]["segments"][0]["uncertainty_ticks"] == 11
    assert collection_history(project, "time_transforms", "missing") == []
    with pytest.raises(ValueError):
        collection_history(project, "", "x")
    with pytest.raises(ValueError):
        collection_history(project, "time_transforms", "")
    with pytest.raises(FileNotFoundError):
        collection_history(tmp_path / "empty", "time_transforms", "x")


def test_drift_history_decomposes_and_matches_migration(tmp_path):
    project = _project_with_transform(tmp_path)
    report = transform_drift_history(
        project, "transform-sha256:abc-to-sha256:def",
        m6_reader=lambda *args: _m6_direct())
    assert report["found"] is True
    assert report["stability"] == "appeared"
    assert report["measurement"]["drift_seconds"] == 0.002
    decomposition = report["decomposition"]
    assert decomposition["spread_seconds"] == pytest.approx(0.02)
    assert decomposition["drift_seconds"] == 0.002
    assert decomposition["recomputed_uncertainty_ticks"] == 11
    assert decomposition["bound_matches_migration"] is True
    assert report["uncertainty_ticks_by_revision"] == [11]


def test_drift_history_tracks_growth_and_removal(tmp_path):
    project = _project_with_transform(tmp_path)
    grown = _transform_row(uncertainty=15)
    evidence_lifecycle.project_model_store.merge_evidence_model(
        project, {**project_model_store.load_project_model(project)[1],
                  "time_transforms": [grown]},
        expected_revision=2, author="ren", reason="re-measured")
    report = transform_drift_history(
        project, "transform-sha256:abc-to-sha256:def",
        m6_reader=lambda *args: _m6_direct())
    assert report["stability"] == "growing"
    assert report["uncertainty_ticks_by_revision"] == [11, 15]
    assert report["decomposition"]["bound_matches_migration"] is False
    model = project_model_store.load_project_model(project)[1]
    project_model_store.append_model_patch(
        project, {"time_transforms": []}, expected_revision=3,
        reason="source re-cut")
    report = transform_drift_history(
        project, "transform-sha256:abc-to-sha256:def",
        m6_reader=lambda *args: _m6_direct())
    assert report["stability"] == "removed"


def test_drift_history_absent_without_guessing(tmp_path):
    project = _project(tmp_path / "a")
    report = transform_drift_history(project, "transform-missing")
    assert report == {"transform_id": "transform-missing", "found": False,
                      "revisions": [], "measurement": None,
                      "decomposition": None, "stability": "absent"}
    project = _project_with_transform(tmp_path / "b")
    report = transform_drift_history(
        project, "transform-sha256:abc-to-sha256:def",
        m6_reader=lambda *args: None)
    assert report["measurement"] is None
    assert report["decomposition"] is None
    assert report["stability"] == "appeared"
    with pytest.raises(ValueError):
        transform_drift_history(project, "")


def test_normalize_envelope_preserves_legacy_basis():
    envelope = {"record_version": 1, "source_digest": "sha256:abc",
                "producer": "mfa", "producer_version": "english_us_arpa",
                "source_stream_id": "stream-sha256:abc",
                "sampling_method": "whole-source-audio-transcription",
                "sample_resolution": None, "coverage_intervals": [],
                "coverage_status": "complete",
                "confidence_semantics": "producer_defined",
                "verified_status": "unverified", "inputs": []}
    record = normalize_envelope_preprocessing(envelope)
    assert record["producer"] == "mfa"
    assert record["source_revision"] == "sha256:abc"
    assert record["preprocessing"]["method"] == (
        "whole-source-audio-transcription")
    assert record["sampling_policy"]["coverage_status"] == "complete"
    assert record["derivation_digest"].startswith("sha256:")
    legacy = {"producer": "old", "producer_version": "1",
              "source_digest": "sha256:old"}
    record = normalize_envelope_preprocessing(legacy)
    assert record["preprocessing"]["method"] == "unknown"
    with pytest.raises(ValueError):
        normalize_envelope_preprocessing({"producer": "", "producer_version": "1"})
    with pytest.raises(ValueError):
        normalize_envelope_preprocessing("not-an-envelope")


def test_typed_confidence_uses_the_contract():
    detail = typed_confidence(0.7, "rank", "rf-detr")
    assert detail == {"value": 0.7, "kind": "rank", "producer": "rf-detr"}
    calibrated = typed_confidence(0.9, "calibrated_probability", "mfa",
                                   calibration_version="heldout-2026-10")
    assert calibrated["calibration_version"] == "heldout-2026-10"
    with pytest.raises(ValueError):
        typed_confidence(0.9, "calibrated_probability", "mfa")
    with pytest.raises(ValueError):
        typed_confidence(0.5, "probability", "mfa")
    with pytest.raises(ValueError):
        typed_confidence(float("nan"), "rank", "mfa")
    with pytest.raises(ValueError):
        typed_confidence(0.5, "rank", "")


def test_an_artifact_that_does_not_read_the_model_is_not_stale_because_the_model_moved(
        tmp_path):
    """A search index is built from the media and the ingest output.

    It does not read the Project Model, so a model revision moving must
    not mark it stale: that would send a reader to rebuild an index
    that is perfectly current.
    """
    project = _project(tmp_path)
    register_artifact(project, name="footage_index", kind="search_index",
                      source_digests=[], model_revision=None,
                      rebuild="owners.search:build_index", owner="footage_query")
    report = check_artifacts(project, FIXTURE, model_revision=99)
    assert report["footage_index"]["status"] == "current"
    assert report["footage_index"]["depends_on_model"] is False


def test_the_report_names_the_sources_that_moved(tmp_path):
    """Seven sources, one replaced: the report says which one.

    "Something changed" sends a reader to re-measure the project. "This
    source changed" sends them to re-measure one clip.
    """
    project = _project(tmp_path)
    register_artifact(project, name="footage_index", kind="search_index",
                      source_digests=["sha256:abc", "sha256:gone"],
                      model_revision=None,
                      rebuild="owners.search:build_index", owner="footage_query")
    report = check_artifacts(project, FIXTURE, model_revision=1,
                             live_source_digests=["sha256:abc", "sha256:new"])
    row = report["footage_index"]
    assert row["status"] == "stale_source"
    assert row["stale_sources"] == ["sha256:gone"]
    assert row["new_sources"] == ["sha256:new"]
    assert "sha256:gone" in row["status_reason"]


def test_derived_status_reports_nothing_derived_as_a_fact(tmp_path):
    """A project that has derived nothing answers with an empty report."""
    project = tmp_path / "bare"
    report = evidence_lifecycle.derived_status(project)
    assert report["artifacts"] == {}
    assert report["stale"] == []
    assert report["current"] == []
    assert report["model_revision"] == 0


def test_derived_status_names_the_stale_artifacts(tmp_path):
    project = _project(tmp_path)
    register_artifact(project, name="footage_index", kind="search_index",
                      source_digests=["sha256:gone"], model_revision=None,
                      rebuild="owners.search:build_index", owner="footage_query")
    report = evidence_lifecycle.derived_status(project)
    assert report["stale"] == ["footage_index"]
    assert report["artifacts"]["footage_index"]["stale_sources"] == [
        "sha256:gone"]
