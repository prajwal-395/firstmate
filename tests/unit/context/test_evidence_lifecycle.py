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
    invalidate_artifact,
    observation_history,
    record_derivation,
    register_artifact,
    registered_artifacts,
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
