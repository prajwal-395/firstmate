"""A pending transaction's candidate must survive promotion cleanup."""

import pytest

from library.tools import reel_build, staging_holds
from tests.resolve_double import FakeProject, FakeTimeline


def test_cleanup_keeps_candidate_with_unresolved_recovery_reference(
        monkeypatch, tmp_path):
    candidate = FakeTimeline("Reel 07 (rebuild staging)", uid="candidate-07")
    project = FakeProject("Podcast", timelines=[candidate])
    records = {
        "candidate-07-record": {
            "status": "prepared",
            "candidate_name": candidate.GetName(),
            "resolve_timeline_id": candidate.GetUniqueId(),
            "owning_transaction": "transaction-07",
            "recovery_required": {
                "transaction_id": "transaction-07",
                "status": "unresolved",
            },
        },
    }
    monkeypatch.setattr(staging_holds, "candidate_records",
                        lambda _folder: records)
    monkeypatch.setattr(staging_holds, "read_holds",
                        lambda _folder: {})
    transitions = []
    monkeypatch.setattr(staging_holds, "record_candidate",
                        lambda *_args, **kwargs: transitions.append(kwargs))
    monkeypatch.setattr(staging_holds, "release_hold",
                        lambda *_args: pytest.fail("recovery candidate hold released"))

    assert reel_build.cleanup_disposable_candidates(
        project, str(tmp_path)) == []
    assert project.deleted == []
    assert transitions == []


def test_cleanup_reads_unresolved_transaction_from_project_event_journal(
        monkeypatch, tmp_path):
    from library.tools import event_log

    candidate = FakeTimeline("Reel 08 (rebuild staging)", uid="candidate-08")
    project = FakeProject("Podcast", timelines=[candidate])
    records = {
        "candidate-08-record": {
            "status": "edited",
            "candidate_name": candidate.GetName(),
            "resolve_timeline_id": candidate.GetUniqueId(),
            "owning_transaction": "build-transaction-08",
        },
    }
    monkeypatch.setattr(staging_holds, "candidate_records",
                        lambda _folder: records)
    monkeypatch.setattr(staging_holds, "read_holds",
                        lambda _folder: {})
    monkeypatch.setattr(event_log, "read_events", lambda _folder: [{
        "fields": {
            "transaction_id": "patch-transaction-08",
            "timeline": candidate.GetName(),
            "timeline_id": candidate.GetUniqueId(),
            "stage": "recovery_required",
        },
    }])
    transitions = []
    monkeypatch.setattr(staging_holds, "record_candidate",
                        lambda *_args, **kwargs: transitions.append(kwargs))

    assert reel_build.cleanup_disposable_candidates(
        project, str(tmp_path)) == []
    assert project.deleted == []
    assert transitions == []

