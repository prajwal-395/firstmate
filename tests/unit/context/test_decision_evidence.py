"""The evidence an APPROVED project decision still cites.

The defects these tests name, one at a time:

- a collector deleting the measurements of footage an APPROVED reel was
  cut from, because the clip left the catalog and nothing else named
  it;
- a PROPOSED moment's footage treated as protected, so nothing may
  ever be collected;
- an approval record that cannot be read read as "no approvals", so the
  evidence it protected is collected;
- a Project Model edit that rests on evidence protecting nothing,
  because its observations were not followed to their asset;
- a cited source file identified by its PATH alone, so a collector
  matching on content identity could not keep it.

The projects here are throwaway folders under tmp_path. No real
project is touched and no media is read.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from library.tools import decision_evidence, project_model_store

FIXTURE = json.loads(
    Path("native/ren-protocol/fixtures/project-model.json").read_text(
        encoding="utf-8")
)

DIGEST = "sha256:abc"
SOURCE = "/media/LC4930.MXF"


def _proposal(project: Path, approval: str) -> Path:
    """A proposal file with one moment, approved or not."""
    path = project / "pipeline_output" / "review" / "reel_proposals_v2.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "format": "reel_proposal/1",
        "moments": [{
            "number": 1, "slug": "parking", "reason": "the point",
            "timeline_start": 10.0, "timeline_end": 25.0,
            "approval": approval,
            "source_spans": [{"source_file": SOURCE,
                               "source_start": 1.0, "source_end": 16.0}],
        }],
    }), encoding="utf-8")
    return path


def _project(tmp_path, approval="approved", with_model=True):
    project = tmp_path / "proj"
    project.mkdir(parents=True, exist_ok=True)
    (project / "pipeline_data.json").write_text(json.dumps({
        "source_fingerprints": {"clip_001": {
            "path": SOURCE, "size_bytes": 10, "content_digest": DIGEST}},
    }), encoding="utf-8")
    _proposal(project, approval)
    if with_model:
        project_model_store.commit_project_model(
            project, json.loads(json.dumps(FIXTURE)), expected_revision=0,
            reason="fixture")
    return project


def test_an_approved_moment_cites_its_source_footage(tmp_path):
    project = _project(tmp_path, with_model=False)
    report = decision_evidence.cited_evidence(project)
    assert report["digests"] == [DIGEST]
    assert report["paths"] == [SOURCE]
    assert report["decisions"][0]["kind"] == "proposal_moment"
    assert report["decisions"][0]["cited_paths"] == [SOURCE]


def test_a_proposed_moment_cites_nothing(tmp_path):
    """Only an approval protects. A proposal is a question, not a ruling."""
    project = _project(tmp_path, approval="proposed", with_model=False)
    report = decision_evidence.cited_evidence(project)
    assert report["digests"] == []
    assert report["paths"] == []


def test_an_evidence_backed_edit_cites_its_observations(tmp_path):
    """The edit rests on the observation, and the observation rests on
    the asset's bytes."""
    project = _project(tmp_path, approval="proposed", with_model=True)
    report = decision_evidence.cited_evidence(project)
    assert "obs-1" in report["observations"]
    assert DIGEST in report["digests"]
    kinds = {row["kind"] for row in report["decisions"]}
    assert "evidence_backed_edit" in kinds


def test_an_unreadable_approval_is_reported_not_ignored(tmp_path):
    """A collector that cannot read the approvals must not collect the
    evidence they protect."""
    project = _project(tmp_path)
    (project / "pipeline_output" / "review" / "reel_proposals_v2.json").write_text(
        json.dumps({"format": "something/else", "moments": []}),
        encoding="utf-8")
    report = decision_evidence.cited_evidence(project)
    assert report["unreadable"]
    with pytest.raises(decision_evidence.DecisionEvidenceError):
        decision_evidence.cited_digests(project)


def test_the_workspace_collector_keeps_what_an_approved_decision_cites(tmp_path):
    """The collector's own reachability set carries the approved digest."""
    from library.tools import workspace_gc
    project = _project(tmp_path)
    found = workspace_gc._referenced_digests([project], [])
    assert DIGEST in found


def test_the_workspace_collector_refuses_when_the_approvals_cannot_be_read(
        tmp_path):
    from library.tools import workspace_gc
    project = _project(tmp_path)
    (project / "pipeline_output" / "review" / "reel_proposals_v2.json").write_text(
        json.dumps({"format": "something/else", "moments": []}),
        encoding="utf-8")
    with pytest.raises(workspace_gc.WorkspaceGcRefused):
        workspace_gc._referenced_digests([project], [])


def test_cache_eviction_recognizes_source_memory_cited_by_approved_decision(
        tmp_path, monkeypatch):
    """Eviction leaves source evidence cited after it left the catalog."""
    from library.tools import artifact_lifecycle, cache_lifecycle, event_log

    project = _project(tmp_path, with_model=False)
    cache_file = tmp_path / "cache" / DIGEST / "frames.index.json"
    cache_file.parent.mkdir(parents=True)
    cache_file.write_bytes(b"measured source evidence")
    identity = artifact_lifecycle.FileIdentity.capture(cache_file)
    artifact_id = "source-memory-artifact"
    monkeypatch.setattr(artifact_lifecycle, "inventory", lambda _project: {
        "artifacts": [{
            "artifact_id": artifact_id,
            "kind": "semantic_measurement",
            "state": artifact_lifecycle.CACHEABLE,
            "ownership_known": True,
            "identity_matches": True,
            "pinned": False,
            "dependencies": [],
            "path": str(cache_file),
            "identity": identity.__dict__,
        }],
    })
    monkeypatch.setattr(event_log, "read_events", lambda _project: [
        {"type": cache_lifecycle.VALUE_EVENT, "fields": {
            "artifact_id": artifact_id, "source_available": True,
            "compatible": False, "consumer_count": 0}},
        {"type": cache_lifecycle.METRIC_EVENT, "fields": {
            "artifact_id": artifact_id, "outcome": "hit",
            "compatible": False, "at_epoch": 1}},
    ])
    monkeypatch.setattr(event_log, "emit", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(cache_lifecycle, "cache_file_is_in_use",
                        lambda _path: False)

    plan = cache_lifecycle.plan_eviction(
        str(project), budget=cache_lifecycle.CacheBudget(total_bytes=100),
        now_epoch=10)
    assert plan["approved_decision_digests"] == [DIGEST]
    assert plan["selected"] == []


def test_caption_asset_roots_include_approved_decision_evidence_paths(
        tmp_path):
    """Caption sweeping preserves an exact asset path an approval cites."""
    from library.tools import caption_asset_gc

    project = _project(tmp_path, with_model=False)
    asset = project / "pipeline_output" / "steps" / "4_05_render_subtitles" / \
        "sub_nospeaker_clip-001_10000-12000_12345678.mov"
    asset.parent.mkdir(parents=True, exist_ok=True)
    asset.write_bytes(b"approved caption evidence")
    proposal = project / "pipeline_output" / "review" / "reel_proposals_v2.json"
    payload = json.loads(proposal.read_text(encoding="utf-8"))
    payload["moments"][0]["source_spans"][0]["source_file"] = str(asset)
    proposal.write_text(json.dumps(payload), encoding="utf-8")
    pipeline_data = project / "pipeline_data.json"
    state = json.loads(pipeline_data.read_text(encoding="utf-8"))
    state["source_fingerprints"]["clip_001"] = {
        "path": str(asset), "size_bytes": 10, "content_digest": DIGEST}
    pipeline_data.write_text(json.dumps(state), encoding="utf-8")

    roots = caption_asset_gc.collect_pipeline_roots(str(project))
    decisions = next(row for row in roots
                     if row.name == "project:approved-decisions")
    assert str(asset) in decisions.paths
    assert decisions.status == caption_asset_gc.OK


def test_caption_sweep_refuses_unreadable_approved_decision_evidence(
        tmp_path):
    from library.tools import caption_asset_gc

    project = _project(tmp_path, with_model=False)
    proposal = project / "pipeline_output" / "review" / "reel_proposals_v2.json"
    proposal.write_text('{"format":"wrong"}', encoding="utf-8")
    roots = caption_asset_gc.collect_pipeline_roots(str(project))
    decisions = next(row for row in roots
                     if row.name == "project:approved-decisions")
    assert decisions.status == caption_asset_gc.UNREADABLE


def test_a_project_with_no_decisions_cites_nothing(tmp_path):
    project = tmp_path / "bare"
    project.mkdir()
    report = decision_evidence.cited_evidence(project)
    assert report["digests"] == []
    assert report["paths"] == []
    assert report["unreadable"] == []
    assert decision_evidence.FORMAT == "decision_evidence/1"
