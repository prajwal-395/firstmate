"""A model-derived row the engine measured, and the licence that governs it.

The defects these tests name, one at a time:

- a model-derived row the engine measured carrying NO licence record,
  so the cross-project gate refused every row it had measured with a
  commercially reusable model;
- a project stamped with the Project Model's own id, so the row named a
  producer the grant does not know and the share refused the row it had
  just allowed;
- one generation record copied onto every row it names read as several
  records, and the gate refused the row for it;
- a share that decided a hundred thousand rows one at a time, flushing
  the append-only audit once per row;
- a share whose refusals left no line in the audit, so the log recorded
  only the rows the gate was asked about;
- a model rebuilt from the same measurements losing the licence record,
  so the rebuilt row was refused where the row it replaced was allowed.

The projects here are throwaway folders under tmp_path with small hand
built models. No real project is touched and no media is read. The
licence verdict is the REAL registry's, because a licence verdict
nobody checked is not a licence verdict.
"""

from __future__ import annotations

import json
from pathlib import Path

from library.tools import (
    cross_project_evidence,
    model_evidence_provenance,
    model_provenance_binding,
    project_model_store,
)

DIGEST = "d" * 64
ASSET_ID = f"asset-{DIGEST}"
CLOCK_ID = f"clock-{DIGEST}"
SOURCE_ID = "p1"
TARGET_ID = "p2"

FIXTURE = json.loads(
    Path("native/ren-protocol/fixtures/project-model.json").read_text(
        encoding="utf-8")
)


def _policy(root: Path, project_id: str, other_id: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "cross_project_policy.json").write_text(json.dumps({
        "project_id": project_id, "share_with": [other_id],
    }), encoding="utf-8")


def _model() -> dict:
    """The fixture, with a sound-event row a lane measured."""
    model = json.loads(json.dumps(FIXTURE))
    model["id"] = "project-demo"
    measured = json.loads(json.dumps(model["observations"][0]))
    measured.update({
        "id": f"obs-{DIGEST}-m5-0001",
        "asset_id": "asset-source",
        "modality": "sound_event",
        "provenance_id": f"prov-{DIGEST}-m5",
        "interval": {"clock_id": "clock-source", "start": 0, "end": 24},
        "payload": {"label": "Clicking"},
    })
    measured.pop("certainty", None)
    second = json.loads(json.dumps(measured))
    second["id"] = f"obs-{DIGEST}-m5-0002"
    second["interval"] = {"clock_id": "clock-source", "start": 24, "end": 48}
    second["payload"] = {"label": "Typing"}
    model["observations"].append(measured)
    model["observations"].append(second)
    return model


def _projects(tmp_path, *, model_id: str = "project-demo"):
    source = tmp_path / "source"
    target = tmp_path / "target"
    _policy(source, SOURCE_ID, TARGET_ID)
    _policy(target, TARGET_ID, SOURCE_ID)
    model = _model()
    model["id"] = model_id
    project_model_store.commit_project_model(
        source, model, expected_revision=0, reason="fixture")
    project_model_store.commit_project_model(
        target, json.loads(json.dumps(model)), expected_revision=0,
        reason="fixture")
    return source, target


def _bind(source: Path, purpose: str = "commercial_client") -> dict:
    return model_provenance_binding.bind_lane_measurement(
        source, DIGEST, "m5", workload_purpose=purpose)


def test_a_model_derived_row_with_a_licence_record_shares(tmp_path):
    """The row the engine measured with a licensed model crosses."""
    source, target = _projects(tmp_path)
    report = _bind(source)
    assert report["implementation_id"] == "panns.cnn14_decision_max"
    assert report["stamped_rows"] == 2
    share = cross_project_evidence.share_evidence(source, target)
    assert share["opt_in"] == "mutual"
    assert sorted(share["shared"]) == [f"obs-{DIGEST}-m5-0001",
                                       f"obs-{DIGEST}-m5-0002"]
    assert share["imported"] is True
    _revision, model = project_model_store.load_project_model(target)
    carried = [row for row in model["observations"]
               if row["id"] == f"obs-{DIGEST}-m5-0001"]
    assert carried and carried[0]["payload"]["model_provenance"]
    assert (carried[0]["payload"]["model_provenance_source"]["project_id"]
            == SOURCE_ID)


def test_a_row_with_no_licence_record_is_refused(tmp_path):
    """The gate fails closed on a row no record governs."""
    source, target = _projects(tmp_path)
    _bind(source)
    share = cross_project_evidence.share_evidence(source, target)
    refused = {row["evidence_id"]: row["reason"] for row in share["refused"]}
    assert "obs-1" in refused
    assert "model provenance" in refused["obs-1"]


def test_the_binding_names_the_producer_by_the_projects_grant_id(tmp_path):
    """A row stamped with the model store's id names a producer the
    grant does not know, and the share refuses the row it just allowed."""
    source, target = _projects(tmp_path, model_id="project-not-the-grant")
    _bind(source)
    _revision, model = project_model_store.load_project_model(source)
    stamped = next(row for row in model["observations"]
                   if row["id"] == f"obs-{DIGEST}-m5-0001")
    assert stamped["payload"]["model_provenance_source"]["project_id"] == SOURCE_ID
    share = cross_project_evidence.share_evidence(source, target)
    assert sorted(share["shared"]) == [f"obs-{DIGEST}-m5-0001",
                                       f"obs-{DIGEST}-m5-0002"]


def test_one_record_naming_several_rows_is_one_record(tmp_path):
    """A record is copied onto every row it names, and the copies are
    one record - not several records governing the same row."""
    source, target = _projects(tmp_path)
    _bind(source)
    _revision, model = project_model_store.load_project_model(source)
    rows = [row for row in model["observations"]
            if row.get("provenance_id") == f"prov-{DIGEST}-m5"]
    records = [row["payload"]["model_provenance"] for row in rows]
    assert len(rows) == 2
    assert records[0] == records[1], "the record is one record, copied"
    share = cross_project_evidence.share_evidence(source, target)
    assert sorted(share["shared"]) == [f"obs-{DIGEST}-m5-0001",
                                       f"obs-{DIGEST}-m5-0002"]
    assert not [row for row in share["refused"]
                if "multiple model provenance" in row["reason"]]


def test_a_share_decides_every_row_in_one_append(tmp_path, monkeypatch):
    """A share that flushes the audit once per row took half an hour to
    decide a project's whole model."""
    source, target = _projects(tmp_path)
    _bind(source)
    appends: list[int] = []
    original = model_evidence_provenance._append_jsonl

    def counting(path, *records):
        appends.append(len(records))
        return original(path, *records)

    monkeypatch.setattr(model_evidence_provenance, "_append_jsonl", counting)
    share = cross_project_evidence.share_evidence(source, target)
    assert share["candidate_count"] >= 1
    assert len(appends) <= 2, appends


def test_every_refusal_is_audited(tmp_path):
    """A refusal that leaves no line is a decision nobody can account for."""
    source, target = _projects(tmp_path)
    _bind(source)
    cross_project_evidence.share_evidence(source, target)
    audit = (target / project_model_store.STORE_DIR /
             model_evidence_provenance.AUDIT_FILE)
    events = [json.loads(line) for line in
              audit.read_text(encoding="utf-8").splitlines()]
    audited = {event["evidence_id"] for event in events}
    assert "obs-1" in audited
    assert f"obs-{DIGEST}-m5-0001" in audited
    assert any(event["action"] == "reused" for event in events)
    assert any(event["action"] == "refused" for event in events)


def test_the_report_counts_what_was_shared_and_refused(tmp_path):
    source, target = _projects(tmp_path)
    _bind(source)
    share = cross_project_evidence.share_evidence(source, target)
    assert share["shared_count"] == len(share["shared"]) == 2
    assert share["refused_count"] == len(share["refused"])
    assert share["refused_count"] >= 1


def test_a_lane_that_names_no_model_is_not_bound(tmp_path):
    """An unnamed model authorises nothing, and a guessed one is worse."""
    source, _target = _projects(tmp_path)
    try:
        model_provenance_binding.bind_lane_measurement(source, DIGEST, "m9")
    except model_provenance_binding.ProvenanceBindingError as exc:
        assert "m9" in str(exc)
    else:
        raise AssertionError("a lane with no model must not bind")


def test_a_rebuilt_model_keeps_the_licence_record(tmp_path):
    """A model rebuilt from the same measurements must land on the same
    rows, licence record and all."""
    from library.tools import project_model_evidence

    source, _target = _projects(tmp_path)
    _bind(source)
    model = json.loads(json.dumps(_model()))
    stamped = project_model_evidence._stamp_generation_records(
        model, str(source))
    assert stamped == 2
    row = next(row for row in model["observations"]
               if row["id"] == f"obs-{DIGEST}-m5-0001")
    assert row["payload"]["model_provenance"]
    assert row["payload"]["license_context"]["implementation_id"] == (
        "panns.cnn14_decision_max")
