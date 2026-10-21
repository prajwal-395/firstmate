"""Model rights attach to individual evidence and gate every reuse."""
import json

import pytest

from library.tools import model_evidence_provenance as provenance

APPROVED = "fixture.approved_detector"
RESTRICTED = "fixture.restricted_detector"
ALL_PURPOSES = ["commercial_client", "personal_noncommercial",
                "permitted_research"]


def _registry():
    def implementation(implementation_id, status, purposes, editions,
                       capabilities=("visual.detect",)):
        return {
            "id": implementation_id,
            "component_id": "model.clip_search",
            "label": implementation_id,
            "capabilities": list(capabilities),
            "checkpoint": {"version": "revision-1", "weights_hash": None,
                           "provider": "publisher", "conversion": None,
                           "runtime": "fixture"},
            "license_evidence": {
                "upstream_url": "https://example.invalid/model",
                "exact_version": "revision-1", "retrieved_at": "2026-10-16",
                "license": "Apache-2.0", "obligations": [],
                "approval_status": status,
            },
            "purposes": list(purposes), "editions": list(editions),
            "qualification": "qualified",
            "output_reuse": {
                "class": ("commercial" if status == "approved" else
                          "research_only" if status == "rejected" else
                          "personal_noncommercial"),
                "permitted_editions": (["commercial", "personal"]
                                        if status == "approved" else
                                        ["personal"] if status == "conditional" else []),
                "reuse_scope": ("any_edition" if status == "approved" else
                                "personal_edition" if status == "conditional" else
                                "benchmark_only"),
                "license_evidence_ref": f"implementations.{implementation_id}.license_evidence",
                "review_required": False, "review_note": None,
            },
        }
    return {
        "schema_version": 1,
        "capabilities": [{"id": "visual.detect", "description": "fixture"}],
        "implementations": [
            implementation(APPROVED, "approved", ALL_PURPOSES,
                           ["commercial", "personal"]),
            implementation(RESTRICTED, "rejected",
                           ["personal_noncommercial", "permitted_research"],
                           ["personal"]),
        ],
    }



def _row(evidence_id="obs-1"):
    return {"id": evidence_id, "certainty": "interpreted",
            "confidence": 0.4, "payload": {"claim": "person speaks"}}


def _share_policy(root, project_id, other_id):
    root.mkdir(parents=True)
    (root / "cross_project_policy.json").write_text(json.dumps({
        "project_id": project_id, "share_with": [other_id],
    }), encoding="utf-8")


def test_record_binds_model_checkpoint_license_and_purpose_without_certainty():
    record = provenance.record_for_generation(
        ["obs-1"], APPROVED, "commercial_client",
        registry=_registry(), generated_at="2026-10-16T12:00:00Z")
    assert record["implementation_id"] == APPROVED
    assert record["checkpoint"]["version"] == "revision-1"
    assert record["license_context"]["approval_status"] == "approved"
    assert record["workload_purpose"] == "commercial_client"
    assert record["output_reuse"]["class"] == "commercial"
    assert record["output_reuse"]["license_evidence_ref"].startswith(
        "implementations.")
    assert "certainty" not in record and "confidence" not in record


def test_the_licence_verdict_travels_onto_the_observation(tmp_path):
    """The row a reader holds names the model, the checkpoint and the
    licence the registry ruled on (B-06)."""
    record = provenance.record_for_generation(
        ["obs-1"], APPROVED, "commercial_client",
        registry=_registry(), generated_at="2026-10-16T12:00:00Z")
    payload = provenance.stamp_observation_license(
        {"claim": "a cup is on the table"}, record)
    context = payload[provenance.LICENSE_CONTEXT_KEY]
    assert context["implementation_id"] == APPROVED
    assert context["approval_status"] == "approved"
    assert context["checkpoint_revision"] == "revision-1"
    assert context["workload_purpose"] == "commercial_client"
    assert payload["checkpoint_revision"] == "revision-1"
    assert payload["claim"] == "a cup is on the table"


def test_an_unreadable_record_leaves_the_row_alone():
    with pytest.raises(ValueError):
        provenance.stamp_observation_license({"claim": "x"}, {"format": "nope"})
    with pytest.raises(ValueError):
        provenance.stamp_observation_license({"claim": "x"}, {
            "format": provenance.FORMAT, "evidence_ids": ["obs-1"]})
    with pytest.raises(ValueError):
        provenance.stamp_observation_license("not-a-payload", {
            "format": provenance.FORMAT, "evidence_ids": ["obs-1"],
            "license_context": {}})


def test_generation_records_are_persisted_append_only_beside_project_model(tmp_path):
    registry = _registry()
    first = provenance.record_generation(
        tmp_path, ["obs-1"], APPROVED, "commercial_client",
        registry=registry,
        generated_at="2026-10-16T12:00:00Z")
    second = provenance.record_generation(
        tmp_path, ["obs-2"], APPROVED, "commercial_client",
        registry=registry,
        generated_at="2026-10-16T12:00:01Z")
    records = provenance.read_generation_records(tmp_path)
    assert records == [first, second]
    assert (tmp_path / "project_model" / provenance.PROVENANCE_FILE).is_file()


def test_unknown_registry_output_terms_fail_closed_and_audit(tmp_path):
    registry = _registry()
    registry["implementations"][0]["output_reuse"] = {
        "class": "unknown", "permitted_editions": [],
        "reuse_scope": "benchmark_only",
        "license_evidence_ref": "unresolved", "review_required": False,
        "review_note": None,
    }
    record = provenance.record_for_generation(
        ["obs-1"], APPROVED, "permitted_research", registry=registry)
    audit_path = tmp_path / provenance.AUDIT_FILE
    result = provenance.reuse_evidence(
        [_row()], [record], source_project_id="p1", target_project_id="p1",
        edition="commercial", purpose="commercial_client",
        source_project_root=tmp_path, target_project_root=tmp_path,
        audit_path=audit_path, registry=registry)
    assert result["reused"] == []
    assert result["refused"][0]["reason"] == (
        "research-only or unknown outputs are benchmark-only")
    event = json.loads(audit_path.read_text().splitlines()[0])
    assert event["action"] == "refused"
    assert event["target_project_id"] == "p1"


def test_restricted_evidence_is_recomputed_with_eligible_implementation(tmp_path):
    registry = _registry()
    record = provenance.record_for_generation(
        ["obs-1"], RESTRICTED, "permitted_research",
        registry=registry)
    calls = []

    def recompute(row, implementation_id, purpose):
        calls.append((row["id"], implementation_id, purpose))
        replacement = _row("obs-1-recomputed")
        replacement_record = provenance.record_for_generation(
            [replacement["id"]], implementation_id, purpose,
            registry=registry)
        return replacement, replacement_record

    result = provenance.reuse_evidence(
        [_row()], [record], source_project_id="p1", target_project_id="p1",
        edition="commercial", purpose="commercial_client",
        source_project_root=tmp_path, target_project_root=tmp_path,
        audit_path=tmp_path / provenance.AUDIT_FILE,
        registry=registry, recompute=recompute)
    assert calls == [("obs-1", APPROVED, "commercial_client")]
    assert result["reused"] == []
    assert result["refused"] == []
    assert result["recomputed"][0]["id"] == "obs-1-recomputed"
    assert result["audit"][0]["action"] == "recomputed"


def test_cross_project_use_requires_mutual_opt_in_result(tmp_path):
    registry = _registry()
    source_root, target_root = tmp_path / "source", tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    record = provenance.record_for_generation(
        ["obs-1"], APPROVED, "commercial_client",
        registry=registry)
    result = provenance.reuse_evidence(
        [_row()], [record], source_project_id="p1", target_project_id="p2",
        edition="commercial", purpose="commercial_client",
        source_project_root=source_root,
        target_project_root=target_root,
        audit_path=tmp_path / provenance.AUDIT_FILE, registry=registry)
    assert result["reused"] == []
    assert result["refused"][0]["reason"] == "mutual cross-project opt-in is absent"
    assert result["audit"][0]["action"] == "refused"


def test_cross_project_opt_in_and_output_rights_allow_reuse(tmp_path):
    registry = _registry()
    source_root, target_root = tmp_path / "source", tmp_path / "target"
    _share_policy(source_root, "p1", "p2")
    _share_policy(target_root, "p2", "p1")
    record = provenance.record_for_generation(
        ["obs-1"], APPROVED, "commercial_client",
        registry=registry)
    result = provenance.reuse_evidence(
        [_row()], [record], source_project_id="p1", target_project_id="p2",
        edition="commercial", purpose="commercial_client",
        source_project_root=source_root, target_project_root=target_root,
        audit_path=tmp_path / provenance.AUDIT_FILE, registry=registry)
    assert result["reused"] == [_row()]
    assert result["audit"][0]["action"] == "reused"


def test_changed_checkpoint_revision_refuses_stale_evidence(tmp_path):
    registry = _registry()
    record = provenance.record_for_generation(
        ["obs-1"], APPROVED, "commercial_client",
        registry=registry)
    registry["implementations"][0]["checkpoint"]["version"] = "revision-2"
    result = provenance.reuse_evidence(
        [_row()], [record], source_project_id="p1", target_project_id="p1",
        edition="commercial", purpose="commercial_client",
        source_project_root=tmp_path, target_project_root=tmp_path,
        audit_path=tmp_path / provenance.AUDIT_FILE, registry=registry)
    assert result["reused"] == []
    assert result["refused"][0]["reason"] == (
        "recorded checkpoint revision is missing or stale")


def test_unpinned_checkpoint_cannot_produce_reusable_evidence(tmp_path):
    registry = _registry()
    registry["implementations"][0]["checkpoint"]["version"] = None
    record = provenance.record_for_generation(
        ["obs-1"], APPROVED, "commercial_client",
        registry=registry)
    result = provenance.reuse_evidence(
        [_row()], [record], source_project_id="p1", target_project_id="p1",
        edition="commercial", purpose="commercial_client",
        source_project_root=tmp_path, target_project_root=tmp_path,
        audit_path=tmp_path / provenance.AUDIT_FILE, registry=registry)
    assert result["reused"] == []
    assert result["refused"][0]["reason"] == (
        "recorded checkpoint revision is missing or stale")


def test_project_model_m4_reader_requires_current_reuse_provenance(tmp_path):
    from library.tools.project_model_acquisition import filter_m4_by_reuse_policy

    root = tmp_path / "project"
    root.mkdir()
    registry = _registry()
    evidence_id = f"m4-window:{'e' * 64}:0:1000"
    record = provenance.record_for_generation(
        [evidence_id], APPROVED, "commercial_client", registry=registry)
    row = {"id": "obs-scene", "provenance_id": "prov-m4",
           "payload": {"model_provenance": record,
                       "model_provenance_source": {
                           "project_id": "p1", "project_root": str(root)}}}
    model = {"id": "p1", "observations": [row]}

    filtered = filter_m4_by_reuse_policy(
        model, root, edition="commercial", purpose="commercial_client",
        registry=registry)

    assert filtered["observations"] == [row]
    assert json.loads((root / "project_model" / provenance.AUDIT_FILE)
                      .read_text(encoding="utf-8").splitlines()[0])["action"] == "reused"

    legacy = {"id": "old-scene", "provenance_id": "prov-m4", "payload": {}}
    model["observations"] = [legacy]
    assert filter_m4_by_reuse_policy(
        model, root, edition="commercial", purpose="commercial_client",
        registry=registry)["observations"] == []
