"""Model registry policy and edition manifest invariants."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from library.tools import model_registry


def test_registry_matches_json_schema_and_projected_manifests():
    import jsonschema

    registry = model_registry.load_registry()
    jsonschema.validate(registry, json.loads(
        model_registry.SCHEMA_PATH.read_text(encoding="utf-8")))
    manifests = model_registry.edition_manifests(registry)
    commercial = manifests["commercial"]
    personal = manifests["personal"]
    assert set(commercial["capabilities"]) <= set(personal["capabilities"])
    assert {row["id"] for row in commercial["implementations"]} <= {
        row["id"] for row in personal["implementations"]}
    assert "audio_flamingo.nvidia_next" not in {
        row["id"] for row in commercial["implementations"]}
    assert "insightface.buffalo_l" not in {
        row["id"] for row in commercial["implementations"]}


def test_research_only_implementations_are_not_client_eligible():
    registry = model_registry.load_registry()
    for implementation in registry["implementations"]:
        if implementation["id"] in {"audio_flamingo.nvidia_next", "insightface.buffalo_l"}:
            assert not model_registry._authorized(
                implementation, "personal", "commercial_client")
            assert model_registry._authorized(
                implementation, "personal", "permitted_research")


def test_unknown_license_status_fails_closed():
    registry = model_registry.load_registry()
    target = copy.deepcopy(registry["implementations"][0])
    target["license_evidence"]["approval_status"] = "unresolved"
    target["editions"] = ["commercial", "personal"]
    target["id"] += ".unknown_license_case"
    registry["implementations"].append(target)
    with pytest.raises(model_registry.ModelRegistryError, match="unresolved license"):
        model_registry.validate_registry(registry)


def test_component_policy_cannot_be_widened_by_model_registry():
    registry = model_registry.load_registry()
    target = next(row for row in registry["implementations"]
                  if row["id"] == "insightface.buffalo_l")
    target["editions"] = ["commercial", "personal"]
    with pytest.raises(model_registry.ModelRegistryError, match="edition claim exceeds"):
        model_registry.validate_registry(registry)


def test_personal_manifest_derives_same_known_good_client_routes():
    manifests = model_registry.edition_manifests()
    commercial_ids = {row["id"] for row in manifests["commercial"]["implementations"]}
    personal_ids = {row["id"] for row in manifests["personal"]["implementations"]}
    assert commercial_ids == personal_ids


def _qualified_registry():
    registry = model_registry.load_registry()
    for index, row in enumerate(registry["implementations"]):
        row["license_evidence"].pop("primary_evidence", None)
        row["license_evidence"]["approval_status"] = "approved"
        if row["component_id"] != "model.audio_flamingo_next" and row["component_id"] != "model.insightface_buffalo_l":
            row["editions"] = ["commercial", "personal"]
        row["purposes"] = ["commercial_client", "personal_noncommercial", "permitted_research"]
        row["qualification"] = "qualified"
        row["qualification_metrics"] = {
            "quality": 0.7 + index * 0.05,
            "latency_cold_seconds": 3.0 + index,
            "latency_warm_seconds": 1.0 + index,
            "peak_memory_gb": 2.0 + index,
        }
        row["resources"] = {"runtime_seconds": 1.0 + index,
                            "memory_gb": 2.0 + index,
                            "resident_memory_gb": 0.5}
        row["hardware"] = {"accelerators": ["gpu", "cpu"]}
    return registry


def test_quality_first_route_records_reasons_resources_and_fallbacks():
    registry = _qualified_registry()
    result = model_registry.route(
        "audio.detect_event", purpose="commercial_client",
        mode="quality_first", registry=registry)
    assert result["selected_implementation"] == "deepfilternet.v0_5_6"
    assert result["alternatives"]
    assert result["fallback_chain"][0] == result["selected_implementation"]
    assert any("purpose" in row["reason"] or "edition" in row["reason"]
               for row in result["rejected"])
    assert result["resource_estimates"]["memory_gb"] is not None


def test_fastest_acceptable_uses_minimum_quality_floor():
    registry = _qualified_registry()
    for row in registry["implementations"]:
        if "audio.detect_event" in row["capabilities"] and row["qualification"] == "qualified":
            row["qualification_metrics"]["quality"] = 0.9
    result = model_registry.route(
        "audio.detect_event", purpose="commercial_client",
        mode="fastest_acceptable", quality_floor=0.8, registry=registry)
    chosen = next(row for row in registry["implementations"]
                  if row["id"] == result["selected_implementation"])
    assert chosen["qualification_metrics"]["quality"] >= 0.8
    assert result["mode"] == "fastest_acceptable"


def test_explicit_override_cannot_bypass_research_license_or_unknown_quality():
    registry = _qualified_registry()
    with pytest.raises(model_registry.ModelRegistryError, match="not authorized|license status is rejected|not included"):
        model_registry.route(
            "audio.detect_event", purpose="commercial_client",
            mode="balanced", override="audio_flamingo.nvidia_next",
            registry=registry)
    with pytest.raises(model_registry.ModelRegistryError, match="not authorized"):
        model_registry.route(
            "visual.describe", purpose="commercial_client",
            mode="quality_first", override="gemma.vision_4_12b_it_4bit")


def test_route_refuses_resource_incompatible_and_unknown_purpose():
    registry = _qualified_registry()
    with pytest.raises(model_registry.ModelRegistryError, match="memory_gb exceeds"):
        model_registry.route(
            "audio.detect_event", purpose="commercial_client",
            mode="balanced", hardware={"max_memory_gb": 0.5}, registry=registry)
    with pytest.raises(model_registry.ModelRegistryError, match="unknown workload purpose"):
        model_registry.route(
            "audio.detect_event", purpose="other", mode="balanced",
            registry=registry)


def test_runtime_component_gate_uses_registry_and_workload_purpose(monkeypatch):
    from ren import edition

    monkeypatch.setenv(edition.EDITION_ENV, edition.PERSONAL)
    monkeypatch.setenv("REN_WORKLOAD_PURPOSE", "commercial_client")
    with pytest.raises(edition.ComponentRefused, match="not authorized"):
        edition.require_component("model.audio_flamingo_next", action="load")
    monkeypatch.setenv("REN_WORKLOAD_PURPOSE", "permitted_research")
    assert edition.require_component(
        "model.audio_flamingo_next", action="load").id == "model.audio_flamingo_next"


def test_model_notices_exclude_rejected_weights_and_preserve_attribution():
    text = model_registry.notices()
    assert "PANNs Cnn14 DecisionLevelMax" in text
    assert "Obligation:" in text and "attribution" in text.lower()
    assert "Audio Flamingo Next" not in text
    assert "InsightFace Buffalo-L" not in text


def test_installed_capability_report_distinguishes_blocked_and_unqualified():
    report = model_registry.installed_capability_report(
        {"model.panns", "model.audio_flamingo_next"},
        purpose="commercial_client", edition_name="personal")
    statuses = {item["implementation_id"]: item["status"]
                for item in report["implementations"]}
    assert statuses["audio_flamingo.nvidia_next"] == "blocked"
    assert statuses["panns.cnn14_decision_max"] == "unqualified"


def test_registry_license_ingestion_maps_primary_evidence(tmp_path):
    registry = model_registry.load_registry()
    record = {
        "artifact_id": "clip",
        "upstream_url": "https://example.invalid/clip",
        "exact_revision": "rev-clip-1",
        "retrieved_at": "2026-10-16T00:00:00Z",
        "license": "MIT",
        "attribution": "attribute CLIP",
        "redistribution": "redistribution permitted with notice",
        "conditions": "commercial use permitted subject to notice",
        "status": "quarantine",
        "evidence_sources": ["https://example.invalid/clip-card"],
    }
    (tmp_path / "clip.json").write_text(json.dumps(record), encoding="utf-8")
    imported, unmapped = model_registry.ingest_license_records(registry, tmp_path)
    clip = next(row for row in imported["implementations"]
                if row["id"] == "clip.openai_vit_large_patch14")
    assert clip["license_evidence"]["primary_evidence"]["artifact_id"] == "clip"
    assert clip["license_evidence"]["approval_status"] == "unresolved"
    assert "commercial_client" not in clip["purposes"]
    assert not unmapped


def test_license_ingestion_refuses_without_records_dir():
    with pytest.raises(model_registry.ModelRegistryError, match="requires records_dir"):
        model_registry.ingest_license_records(model_registry.load_registry(), None)
    with pytest.raises(model_registry.ModelRegistryError, match="requires records_dir"):
        model_registry.unmapped_license_records(None)
