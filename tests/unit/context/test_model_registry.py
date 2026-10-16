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
    # Manifests are eligibility lists (license, edition, workload
    # purpose), not selection: an authorized but not-yet-qualified
    # implementation is listed, because nothing is qualified before
    # the benchmark lanes run and an eligibility list that
    # requires qualification is empty. Qualification gates
    # selection in route(), never eligibility here.
    for row in commercial["implementations"] + personal["implementations"]:
        assert row["license_evidence"]["approval_status"] in {
            "approved", "conditional"}
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


def test_qualification_gates_selection_not_eligibility():
    registry = model_registry.load_registry()
    manifests = model_registry.edition_manifests(registry)
    candidate = next(row for row in manifests["commercial"]["implementations"]
                     if row["qualification"] != "qualified")
    capability = candidate["capabilities"][0]
    assert candidate["id"] in {
        row["id"] for row in manifests["commercial"]["implementations"]}
    with pytest.raises(model_registry.ModelRegistryError,
                       match="qualification"):
        model_registry.route(
            capability, purpose="commercial_client", mode="quality_first",
            override=candidate["id"], registry=registry)


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
        {"model.audio_flamingo_next"},
        purpose="commercial_client", edition_name="personal")
    statuses = {item["implementation_id"]: item["status"]
                for item in report["implementations"]}
    assert statuses["audio_flamingo.nvidia_next"] == "blocked"
    assert statuses["panns.cnn14_decision_max"] == "missing"
    installed = model_registry.installed_capability_report(
        {"model.panns"}, purpose="commercial_client", edition_name="personal")
    assert next(row for row in installed["implementations"]
                if row["implementation_id"] == "panns.cnn14_decision_max")["status"] == "unqualified"


def test_registry_license_ingestion_maps_primary_evidence(tmp_path):
    registry = model_registry.load_registry()
    record = next(row for row in model_registry._snapshot_records()
                  if row["artifact_id"] == "clip")
    (tmp_path / "clip.json").write_text(json.dumps(record), encoding="utf-8")
    imported, unmapped = model_registry.ingest_license_records(registry, tmp_path)
    clip = next(row for row in imported["implementations"]
                if row["id"] == "clip.openai_vit_large_patch14")
    assert clip["license_evidence"]["primary_evidence"]["artifact_id"] == "clip"
    assert clip["license_evidence"]["approval_status"] == "unresolved"
    assert "commercial_client" not in clip["purposes"]
    assert not unmapped


def test_all_primary_license_records_are_preserved_in_schema_valid_snapshot():
    import jsonschema

    snapshot = json.loads(model_registry.EVIDENCE_SNAPSHOT_PATH.read_text())
    jsonschema.validate(snapshot, json.loads(
        (ROOT / "library/model_registry/license_evidence.schema.json").read_text()))
    ids = [row["artifact_id"] for row in snapshot["records"]]
    assert len(ids) == len(set(ids)) == 62
    assert len(model_registry.unmapped_license_records()) == 49


def test_new_optional_fields_validate_against_json_schema():
    import jsonschema

    schema = json.loads(model_registry.SCHEMA_PATH.read_text(encoding="utf-8"))
    registry = model_registry.load_registry()
    target = next(row for row in registry["implementations"]
                  if row["id"] == "panns.cnn14_decision_max")
    target["runtime_env"] = {"name": "ml-experimental", "isolated": True,
                             "requirements": "requirements/ml-experimental.txt",
                             "python": "3.11"}
    target["user_license"] = {"license_name": "Acme Enterprise",
                              "entitlement_env": "REN_MODEL_LICENSES"}
    jsonschema.validate(registry, schema)
    model_registry.validate_registry(registry)
    assert model_registry.isolated_runtime_envs(registry) == {
        "ml-experimental": ["panns.cnn14_decision_max"]}


def test_isolated_runtime_env_requires_name_and_requirements():
    registry = model_registry.load_registry()
    target = next(row for row in registry["implementations"]
                  if row["id"] == "panns.cnn14_decision_max")
    target["runtime_env"] = {"name": "", "isolated": True, "requirements": None, "python": None}
    with pytest.raises(model_registry.ModelRegistryError, match="must name its environment"):
        model_registry.validate_registry(registry)
    target["runtime_env"] = {"name": "ml-experimental", "isolated": True,
                             "requirements": None, "python": None}
    with pytest.raises(model_registry.ModelRegistryError, match="must record its requirements"):
        model_registry.validate_registry(registry)


def test_isolated_runtime_env_is_reported_and_decided():
    registry = _qualified_registry()
    target = next(row for row in registry["implementations"]
                  if row["id"] == "panns.cnn14_decision_max")
    target["runtime_env"] = {"name": "ml-experimental", "isolated": True,
                             "requirements": "requirements/ml-experimental.txt",
                             "python": None}
    decision = model_registry.route(
        "audio.detect_event", purpose="commercial_client",
        mode="quality_first", override="panns.cnn14_decision_max", registry=registry)
    assert decision["runtime_env"]["name"] == "ml-experimental"
    report = model_registry.installed_capability_report(
        {"model.panns"}, purpose="commercial_client", edition_name="commercial",
        registry=registry)
    row = next(item for item in report["implementations"]
               if item["implementation_id"] == "panns.cnn14_decision_max")
    assert row["runtime_env"] == "ml-experimental"
    assert row["status"] == "available"


def test_user_license_slot_requires_names():
    registry = model_registry.load_registry()
    target = next(row for row in registry["implementations"]
                  if row["id"] == "panns.cnn14_decision_max")
    target["user_license"] = {"license_name": "", "entitlement_env": "REN_MODEL_LICENSES"}
    with pytest.raises(model_registry.ModelRegistryError, match="must name its license"):
        model_registry.validate_registry(registry)


def _slotted_registry():
    registry = _qualified_registry()
    target = next(row for row in registry["implementations"]
                  if row["id"] == "panns.cnn14_decision_max")
    target["license_evidence"]["approval_status"] = "conditional"
    target["user_license"] = {"license_name": "Acme Enterprise",
                              "entitlement_env": "REN_MODEL_LICENSES"}
    return registry, target


def test_user_license_entitlement_authorizes_conditional_for_client_work(monkeypatch):
    monkeypatch.delenv(model_registry.USER_LICENSES_ENV, raising=False)
    registry, target = _slotted_registry()
    with pytest.raises(model_registry.ModelRegistryError, match="not authorized"):
        model_registry.require_implementation(
            target["id"], action="load", purpose="commercial_client", registry=registry)
    entitled = {target["id"]: {"license_name": "Acme Enterprise", "key": "seat-123"}}
    assert model_registry.require_implementation(
        target["id"], action="load", purpose="commercial_client",
        registry=registry, user_licenses=entitled)["id"] == target["id"]
    decision = model_registry.route(
        "audio.detect_event", purpose="commercial_client",
        mode="quality_first", override=target["id"],
        registry=registry, user_licenses=entitled)
    assert decision["user_license_used"] == "Acme Enterprise"
    report = model_registry.installed_capability_report(
        {"model.panns"}, purpose="commercial_client", edition_name="commercial",
        registry=registry, user_licenses=entitled)
    assert next(row for row in report["implementations"]
                if row["implementation_id"] == target["id"])["status"] == "available"


def test_user_license_cannot_cure_rejected_or_unresolved():
    registry, target = _slotted_registry()
    entitled = {target["id"]: {"license_name": "Acme Enterprise", "key": "seat-123"}}
    for status in ("rejected", "unresolved"):
        registry, target = _slotted_registry()
        target["license_evidence"]["approval_status"] = status
        target["purposes"] = [purpose for purpose in target["purposes"]
                            if purpose != "commercial_client"]
        target["editions"] = ["personal"]
        entitled = {target["id"]: {"license_name": "Acme Enterprise", "key": "seat-123"}}
        with pytest.raises(model_registry.ModelRegistryError, match="not authorized"):
            model_registry.require_implementation(
                target["id"], action="load", purpose="commercial_client",
                registry=registry, user_licenses=entitled)
    registry, target = _slotted_registry()
    wrong_name = {target["id"]: {"license_name": "Other License", "key": "seat-123"}}
    with pytest.raises(model_registry.ModelRegistryError, match="not authorized"):
        model_registry.require_implementation(
            target["id"], action="load", purpose="commercial_client",
            registry=registry, user_licenses=wrong_name)


def test_user_entitlements_rejects_malformed_and_unknown(monkeypatch):
    registry = model_registry.load_registry()
    monkeypatch.setenv(model_registry.USER_LICENSES_ENV, "{not json")
    with pytest.raises(model_registry.ModelRegistryError, match="not valid JSON"):
        model_registry.user_entitlements(None, registry)
    with pytest.raises(model_registry.ModelRegistryError, match="non-empty key"):
        model_registry.user_entitlements(
            {"panns.cnn14_decision_max": {"license_name": "Acme", "key": ""}}, registry)
    with pytest.raises(model_registry.ModelRegistryError, match="unregistered implementations"):
        model_registry.user_entitlements(
            {"no.such_model": {"license_name": "Acme", "key": "seat-123"}}, registry)
    monkeypatch.delenv(model_registry.USER_LICENSES_ENV, raising=False)
    assert model_registry.user_entitlements(None, registry) == {}


def test_commercial_routes_resolve_identically_under_personal():
    registry = _qualified_registry()
    commercial = model_registry.manifest("commercial", "commercial_client", registry)
    assert commercial["capabilities"]
    for capability in commercial["capabilities"]:
        commercial_pick = model_registry.route(
            capability, purpose="commercial_client",
            mode="quality_first", registry=registry)["selected_implementation"]
        personal_pick = model_registry.route(
            capability, purpose="personal_noncommercial",
            mode="quality_first", registry=registry)["selected_implementation"]
        winner = next(row for row in registry["implementations"]
                      if row["id"] == commercial_pick)
        if "personal_noncommercial" in winner["purposes"] and "personal" in winner["editions"]:
            assert personal_pick == commercial_pick
    narrowed = copy.deepcopy(registry)
    commercial_pick = model_registry.route(
        "audio.detect_event", purpose="commercial_client",
        mode="quality_first", registry=registry)["selected_implementation"]
    for row in narrowed["implementations"]:
        if row["id"] == commercial_pick:
            row["editions"] = ["commercial"]
    personal_pick = model_registry.route(
        "audio.detect_event", purpose="personal_noncommercial",
        mode="quality_first", registry=narrowed)
    assert personal_pick["selected_implementation"] != commercial_pick
    assert any(row["implementation_id"] == commercial_pick and "edition" in row["reason"]
               for row in personal_pick["rejected"])


def test_license_records_dir_env_override(tmp_path, monkeypatch):
    registry = model_registry.load_registry()
    record = next(row for row in model_registry._snapshot_records()
                  if row["artifact_id"] == "clip")
    (tmp_path / "clip.json").write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setenv(model_registry.LICENSE_RECORDS_DIR_ENV, str(tmp_path))
    imported, unmapped = model_registry.ingest_license_records(registry, None)
    clip = next(row for row in imported["implementations"]
                if row["id"] == "clip.openai_vit_large_patch14")
    assert clip["license_evidence"]["primary_evidence"]["artifact_id"] == "clip"
    assert not unmapped
