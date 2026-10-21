"""Derivation-aware invalidation over throwaway project stores.

Every project here is built under ``tmp_path``; no real project, no
real media and no real model is touched.
"""
import json
from pathlib import Path

import pytest

from library.tools import derivation_staleness, project_model_store

FIXTURE = json.loads(
    Path("native/ren-protocol/fixtures/project-model.json").read_text(
        encoding="utf-8"))


def _observation(obs_id: str, digest: str, payload: dict | None = None) -> dict:
    body = {"note": obs_id}
    if digest is not None:
        body["evidence_contract"] = {
            "derivation": {"derivation_digest": digest}}
    body.update(payload or {})
    return {"id": obs_id, "asset_id": "asset-source",
            "interval": {"clock_id": "clock-source", "start": 0, "end": 24},
            "modality": "scene", "payload": body,
            "provenance_id": "prov-1", "confidence": None}


def _project(tmp_path, observations=None, events=None, interpretations=None,
             links=None):
    """A project holding the canonical fixture plus the rows under test.

    The fixture's own rows stay: the intent and the clip use reference
    ``obs-1``, and a model that drops them would not validate.
    """
    project = tmp_path / "project"
    model = json.loads(json.dumps(FIXTURE))
    model["observations"] = model["observations"] + list(observations or [])
    model["events"] = model["events"] + list(events or [])
    model["interpretations"] = model["interpretations"] + list(
        interpretations or [])
    model["identity_links"] = model["identity_links"] + list(links or [])
    project_model_store.commit_project_model(
        project, model, expected_revision=0, reason="fixture")
    return project


def _event(event_id: str, evidence_ids: list[str]) -> dict:
    return {"id": event_id,
            "interval": {"clock_id": "clock-source", "start": 0, "end": 24},
            "kind": "cinematography_scene", "participant_ids": [],
            "status": "candidate", "certainty": "inferred",
            "evidence_ids": evidence_ids}


def _interpretation(row_id: str, evidence_ids: list[str]) -> dict:
    return {"id": row_id, "kind": "profile", "author": "fixture",
            "objective": "describe", "policy_id": None,
            "evidence_ids": evidence_ids, "scope": {"asset_id": "asset-source"},
            "uncertainty": None, "certainty": "interpreted"}


def test_edges_cite_the_observation_and_its_digest(tmp_path):
    project = _project(
        tmp_path,
        observations=[_observation("obs-a", "sha256:a"),
                      _observation("obs-b", "sha256:b")],
        events=[_event("event-a", ["obs-a"])],
        interpretations=[_interpretation("interp-a", ["obs-a"])])
    edges = derivation_staleness.build_edges(
        project_model_store.load_project_model(project)[1])
    assert edges["event-a"]["collection"] == "events"
    assert edges["event-a"]["observation_ids"] == ["obs-a"]
    assert edges["event-a"]["derivation_digests"] == {"obs-a": "sha256:a"}
    assert edges["interp-a"]["collection"] == "interpretations"


def test_an_observation_without_a_derivation_has_no_digest(tmp_path):
    project = _project(
        tmp_path,
        observations=[_observation("obs-a", None)],
        events=[_event("event-a", ["obs-a"])])
    edges = derivation_staleness.build_edges(
        project_model_store.load_project_model(project)[1])
    assert edges["event-a"]["derivation_digests"] == {"obs-a": None}


def test_a_revised_observation_marks_its_dependants_and_nothing_else(tmp_path):
    project = _project(
        tmp_path,
        observations=[_observation("obs-a", "sha256:a"),
                      _observation("obs-b", "sha256:b")],
        events=[_event("event-a", ["obs-a"]), _event("event-b", ["obs-b"])],
        interpretations=[_interpretation("interp-a", ["obs-a"])])
    derivation_staleness.refresh(project)
    report = derivation_staleness.mark_stale(
        project, ["obs-a"], reason="the detector box was revised")

    assert report["stale_count"] == 2
    assert {row["id"] for row in report["stale_dependants"]} == {
        "event-a", "interp-a"}
    assert report["stale_by_collection"] == {"events": 1, "interpretations": 1}
    # The unrelated dependants and every other model row are untouched:
    # the fixture's own event, identity link and interpretation, and the
    # event that rests on the other observation.
    assert report["untouched_dependants"] == 4
    assert report["model_rows_untouched"] == 4
    assert all(row["id"] != "event-b"
               for row in report["stale_dependants"])

    marked = derivation_staleness.read_registry(project)["dependants"]
    assert marked["event-a"]["stale"] is True
    assert marked["event-a"]["stale_reason"] == "the detector box was revised"
    assert marked["event-a"]["stale_observations"] == ["obs-a"]
    assert marked["event-b"]["stale"] is False


def test_marking_stale_writes_no_model_row(tmp_path):
    project = _project(
        tmp_path,
        observations=[_observation("obs-a", "sha256:a")],
        events=[_event("event-a", ["obs-a"])])
    derivation_staleness.refresh(project)
    revision_before, model_before = project_model_store.load_project_model(project)
    derivation_staleness.mark_stale(project, ["obs-a"], reason="revised")
    revision_after, model_after = project_model_store.load_project_model(project)
    assert revision_after == revision_before
    assert model_after == model_before


def test_a_moved_digest_marks_its_dependant_without_being_named(tmp_path):
    project = _project(
        tmp_path,
        observations=[_observation("obs-a", "sha256:a")],
        events=[_event("event-a", ["obs-a"])])
    derivation_staleness.refresh(project)
    project_model_store.merge_evidence_records(
        project, observations=[_observation("obs-a", "sha256:a2")],
        expected_revision=1)
    # The caller names the observation that moved; the digest is what
    # decides which dependants are stale.
    report = derivation_staleness.mark_stale(
        project, ["obs-a"], reason="the observation was revised")
    assert {row["id"] for row in report["stale_dependants"]} == {"event-a"}


def test_the_deriver_re_running_clears_the_mark(tmp_path):
    project = _project(
        tmp_path,
        observations=[_observation("obs-a", "sha256:a")],
        events=[_event("event-a", ["obs-a"])])
    derivation_staleness.refresh(project)
    derivation_staleness.mark_stale(project, ["obs-a"], reason="revised")
    project_model_store.merge_evidence_records(
        project, observations=[_observation("obs-a", "sha256:a2")],
        expected_revision=1)
    refreshed = derivation_staleness.refresh(project)
    assert refreshed["resolved"] == ["event-a"]
    assert derivation_staleness.report(project)["stale_count"] == 0


def test_marking_needs_a_reason_and_a_known_observation(tmp_path):
    project = _project(
        tmp_path,
        observations=[_observation("obs-a", "sha256:a")],
        events=[_event("event-a", ["obs-a"])])
    with pytest.raises(ValueError):
        derivation_staleness.mark_stale(project, ["obs-a"], reason="  ")
    with pytest.raises(ValueError):
        derivation_staleness.mark_stale(project, [], reason="revised")
    with pytest.raises(ValueError):
        derivation_staleness.mark_stale(project, ["obs-nope"], reason="revised")


def test_the_registry_is_written_beside_the_model(tmp_path):
    project = _project(
        tmp_path,
        observations=[_observation("obs-a", "sha256:a")],
        events=[_event("event-a", ["obs-a"])])
    derivation_staleness.refresh(project)
    path = project / project_model_store.STORE_DIR / derivation_staleness.REGISTRY_FILE
    assert path.is_file()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["format"] == derivation_staleness.FORMAT
    assert payload["dependants"]["event-a"]["collection"] == "events"


def test_a_project_with_no_model_answers_with_a_reason(tmp_path):
    with pytest.raises(ValueError):
        derivation_staleness.mark_stale(
            tmp_path / "nothing", ["obs-a"], reason="revised")
