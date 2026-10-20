"""Share the source intelligence another project measured.

The defects these tests name, one at a time:

- a project that has NOT granted access being read anyway, so the
  opt-in is only enforced after the fact;
- a row the licence gate refuses being shared anyway, because this
  module decided instead of the gate;
- a row imported into a project that does not hold the clock it sits
  on, which would make the target's model invalid;
- a share that leaves no audit, so nothing records what was shared;
- a dry run that imports anyway;
- a deterministic measurement being laundered through a path meant for
  licensed, model-derived rows.
- a project's source-memory reader seeing another project's digest cache
  without a mutual grant, or importing it without a recorded consent event.

The projects here are throwaway folders under tmp_path with small hand
built models. No real project is touched and no media is read.
"""

from __future__ import annotations

import json
from pathlib import Path

from library.tools import (
    cross_project_evidence,
    model_evidence_provenance,
    project_model_store,
)

APPROVED = "fixture.approved_detector"
RESTRICTED = "fixture.restricted_detector"
ALL_PURPOSES = ["commercial_client", "personal_noncommercial",
                "permitted_research"]

FIXTURE = json.loads(
    Path("native/ren-protocol/fixtures/project-model.json").read_text(
        encoding="utf-8")
)


def _registry():
    def implementation(implementation_id, status, purposes, editions):
        return {
            "id": implementation_id,
            "component_id": "model.clip_search",
            "label": implementation_id,
            "capabilities": ["visual.detect"],
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
                "license_evidence_ref": (
                    f"implementations.{implementation_id}.license_evidence"),
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


def _policy(root: Path, project_id: str, other_id: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "cross_project_policy.json").write_text(json.dumps({
        "project_id": project_id, "share_with": [other_id],
    }), encoding="utf-8")


def _model_with_shared_observation(record, producer_id, producer_root,
                                   observation_id="obs-shared"):
    """The fixture plus one observation that carries its provenance."""
    model = json.loads(json.dumps(FIXTURE))
    model["observations"] = list(model["observations"]) + [{
        "id": observation_id,
        "asset_id": "asset-source",
        "interval": {"clock_id": "clock-source", "start": 0, "end": 24},
        "modality": "scene",
        "confidence": None,
        "payload": {"text": "a parking lot",
                    "model_provenance": record,
                    "model_provenance_source": {"project_id": producer_id,
                                                "project_root": producer_root}},
        "provenance_id": "prov-1",
    }]
    return model


def _projects(tmp_path, *, source_id="p1", target_id="p2",
              mutual=True, record=None, producer_id=None):
    source = tmp_path / "source"
    target = tmp_path / "target"
    _policy(source, source_id, target_id if mutual else "nobody")
    _policy(target, target_id, source_id if mutual else "nobody")
    registry = _registry()
    record = record if record is not None else \
        model_evidence_provenance.record_for_generation(
            ["obs-shared"], APPROVED, "commercial_client", registry=registry)
    source_model = _model_with_shared_observation(
        record, producer_id or source_id, str(source))
    project_model_store.commit_project_model(
        source, source_model, expected_revision=0, reason="fixture")
    project_model_store.commit_project_model(
        target, json.loads(json.dumps(FIXTURE)), expected_revision=0,
        reason="fixture")
    return source, target, registry


def test_a_project_that_has_not_granted_access_is_not_read(tmp_path):
    """The opt-in is checked BEFORE the source is opened."""
    source, target, registry = _projects(tmp_path, mutual=False)
    report = cross_project_evidence.share_evidence(
        source, target, registry=registry)
    assert report["opt_in"] == "absent"
    assert report["shared"] == []
    assert report["imported"] is False
    assert "grant" in report["reason"]


def test_a_granted_share_imports_the_gated_evidence(tmp_path):
    source, target, registry = _projects(tmp_path)
    report = cross_project_evidence.share_evidence(source, target,
                                                   registry=registry)
    assert report["opt_in"] == "mutual"
    assert report["shared"] == ["obs-shared"]
    assert report["imported"] is True
    assert report["target_revision"] == 2
    _revision, model = project_model_store.load_project_model(target)
    assert "obs-shared" in {row["id"] for row in model["observations"]}


def test_every_share_is_audited(tmp_path):
    """A share that leaves no record is a share nobody can account for."""
    source, target, registry = _projects(tmp_path)
    cross_project_evidence.share_evidence(source, target, registry=registry)
    audit = (target / project_model_store.STORE_DIR /
             model_evidence_provenance.AUDIT_FILE)
    events = [json.loads(line) for line in
              audit.read_text(encoding="utf-8").splitlines()]
    assert any(event["evidence_id"] == "obs-shared"
               and event["action"] == "reused" for event in events)


def test_a_row_the_licence_gate_refuses_is_refused_here_too(tmp_path):
    """This module does not decide; the gate does."""
    registry = _registry()
    record = model_evidence_provenance.record_for_generation(
        ["obs-shared"], RESTRICTED, "permitted_research", registry=registry)
    source, target, _ = _projects(tmp_path, record=record)
    report = cross_project_evidence.share_evidence(source, target,
                                                   registry=registry)
    assert report["shared"] == []
    assert report["imported"] is False
    reasons = [row["reason"] for row in report["refused"]]
    assert any("benchmark-only" in reason for reason in reasons)


def test_a_row_the_target_cannot_host_is_refused(tmp_path):
    """Importing a row whose clock the target lacks would make the
    target's model invalid, so the row is refused instead."""
    source, target, registry = _projects(tmp_path)
    _revision, model = project_model_store.load_project_model(source)
    model["clocks"] = list(model["clocks"]) + [{
        "id": "clock-only-here", "ticks_per_second": 1000,
        "frame_rate": None,
        "domain": {"kind": "source", "asset_id": "asset-source"}}]
    for row in model["observations"]:
        if row["id"] == "obs-shared":
            row["interval"] = {"clock_id": "clock-only-here",
                               "start": 0, "end": 24}
    project_model_store.commit_project_model(
        source, model, expected_revision=1, reason="move the row")
    report = cross_project_evidence.share_evidence(source, target,
                                                   registry=registry)
    assert report["shared"] == []
    assert report["imported"] is False
    assert any("clock" in row["reason"] for row in report["refused"])


def test_a_dry_run_shares_nothing(tmp_path):
    source, target, registry = _projects(tmp_path)
    report = cross_project_evidence.share_evidence(source, target,
                                                   registry=registry,
                                                   dry_run=True)
    assert report["shared"] == ["obs-shared"]
    assert report["imported"] is False
    _revision, model = project_model_store.load_project_model(target)
    assert "obs-shared" not in {row["id"] for row in model["observations"]}


def test_a_row_with_no_model_provenance_is_refused(tmp_path):
    """A deterministic measurement has no licence record.

    The gate fails closed on those, and this path does not have a
    second way around it.
    """
    source, target, registry = _projects(tmp_path)
    _revision, model = project_model_store.load_project_model(source)
    for row in model["observations"]:
        if row["id"] == "obs-shared":
            row["payload"].pop("model_provenance")
    project_model_store.commit_project_model(
        source, model, expected_revision=1, reason="drop the provenance")
    report = cross_project_evidence.share_evidence(source, target,
                                                   registry=registry)
    assert report["shared"] == []
    assert any("model provenance" in row["reason"]
               for row in report["refused"])


def test_a_project_with_no_model_shares_nothing(tmp_path):
    source = tmp_path / "bare"
    target = tmp_path / "other"
    _policy(source, "p1", "p2")
    _policy(target, "p2", "p1")
    report = cross_project_evidence.share_evidence(source, target,
                                                   registry=_registry())
    assert report["shared"] == []
    assert "Project Model" in report["reason"]


def test_explicit_mutual_grant_imports_only_deterministic_source_slots(
        tmp_path, monkeypatch):
    from library.tools import source_memory

    source, target, registry = _projects(tmp_path)
    digest = "a" * 64
    for project in (source, target):
        (project / "pipeline_data.json").write_text(json.dumps({
            "source_fingerprints": {"clip_001": {
                "path": "/media/clip.mov", "size_bytes": 12,
                "content_digest": digest}},
        }), encoding="utf-8")
    monkeypatch.setattr(source_memory, "vep_home", lambda: tmp_path / "ren-home")
    source_dir = source_memory.project_memory_root(source) / digest
    source_dir.mkdir(parents=True)
    (source_dir / source_memory.SLOT_FRAMES_DIR).mkdir()
    (source_dir / source_memory.SLOT_FRAMES_DIR / "frame_000001.jpg").write_bytes(
        b"thumbnail")
    (source_dir / source_memory.SLOT_FRAMES_INDEX).write_text(
        json.dumps({"content_digest": digest, "frames": [{
            "file": "frames/frame_000001.jpg", "t": 0.5}]}),
        encoding="utf-8")
    (source_dir / source_memory.SLOT_SCENES).write_text(
        json.dumps({"content_digest": digest, "scenes": []}),
        encoding="utf-8")

    report = cross_project_evidence.share_evidence(
        source, target, digest=digest, registry=registry)
    target_dir = source_memory.project_memory_root(target) / digest
    # The frame index and the thumbnails it names travel together: an
    # index whose samples are missing reads as a source with no frames.
    assert report["measurements_shared"] == {
        digest: [source_memory.SLOT_FRAMES_INDEX, source_memory.SLOT_FRAMES_DIR]}
    assert (target_dir / source_memory.SLOT_FRAMES_INDEX).is_file()
    assert (target_dir / source_memory.SLOT_FRAMES_DIR /
            "frame_000001.jpg").read_bytes() == b"thumbnail"
    assert not (target_dir / source_memory.SLOT_SCENES).exists()


def test_measurement_import_refuses_when_either_project_did_not_opt_in(
        tmp_path, monkeypatch):
    from library.tools import source_memory

    source, target, registry = _projects(tmp_path, mutual=False)
    digest = "b" * 64
    for project in (source, target):
        (project / "pipeline_data.json").write_text(json.dumps({
            "source_fingerprints": {"clip_001": {
                "path": "/media/clip.mov", "size_bytes": 12,
                "content_digest": digest}},
        }), encoding="utf-8")
    monkeypatch.setattr(source_memory, "vep_home", lambda: tmp_path / "ren-home")

    report = cross_project_evidence.share_evidence(
        source, target, digest=digest, registry=registry)
    assert report["measurements_shared"] == {}
    assert not source_memory.project_memory_root(target).exists()
