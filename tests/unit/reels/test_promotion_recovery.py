"""Interrupted promotion recovery resumes only identity-matched renames."""

import json

from library.tools import reel_build
from tests.resolve_double import FakeProject, FakeTimeline


def test_interrupted_promotion_finishes_candidate_already_preserving_backup(
        monkeypatch, tmp_path):
    final = "Reel 07"
    staging = f"{final} (rebuild staging)"
    backup = f"{final} (backup)"
    transaction_id = "b" * 32
    original = FakeTimeline(backup, uid="approved-timeline-id")
    candidate = FakeTimeline(staging, uid="candidate-timeline-id")
    project = FakeProject([FakeTimeline("Podcast - Synced"),
                           original, candidate])
    journal_dir = (tmp_path / "pipeline_output" / "review"
                   / "promotion_transactions")
    journal_dir.mkdir(parents=True)
    journal_path = journal_dir / f"{transaction_id}.json"
    journal_path.write_text(json.dumps({
        "version": 1,
        "transaction_id": transaction_id,
        "project": project.GetName(),
        "state": "prepared",
        "preserve_originals": [],
        "rows": [{
            "final": final,
            "staging": staging,
            "backup": backup,
            "candidate_id": "candidate-timeline-id",
            "candidate_key": "",
            "candidate_transaction_id": None,
            "original_id": "approved-timeline-id",
            "base_revision": None,
            "candidate_snapshot": {"timeline": {"name": staging},
                                   "items": []},
            "original_snapshot": {"timeline": {"name": backup},
                                  "items": []},
            "incoming_rows": [],
        }],
    }), encoding="utf-8")

    monkeypatch.setattr(
        "library.tools.reel_replace_guard.full_timeline_snapshot",
        lambda timeline, _project, _folder=None: {
            "timeline": {"name": timeline.GetName()}, "items": []})
    monkeypatch.setattr("library.tools.plan_provenance.read_provenance",
                        lambda _review_dir: {"built_reels": []})
    monkeypatch.setattr("library.tools.plan_provenance.drop_reel_entries",
                        lambda *_args: None)
    monkeypatch.setattr("library.tools.plan_provenance.rename_reel_entries",
                        lambda *_args: None)
    monkeypatch.setattr(reel_build, "_rename_overlay_records",
                        lambda *_args: None)
    monkeypatch.setattr("library.tools.explainer_plan.rename_plan_reels",
                        lambda *_args: None)
    monkeypatch.setattr("library.tools.reel_semantic_visual.rename_record_reels",
                        lambda *_args: None)
    monkeypatch.setattr(
        "library.tools.reel_semantic_visual.rename_span_record_reels",
        lambda *_args: None)
    monkeypatch.setattr("library.tools.speaker_identity.rename_plan_reels",
                        lambda *_args: None)
    monkeypatch.setattr("library.tools.reel_post_header.rename_plan_reels",
                        lambda *_args: None)
    monkeypatch.setattr("library.tools.caption_asset_gc.rename_ledger_timelines",
                        lambda *_args: None)
    monkeypatch.setattr("library.tools.versions.rounds.stamp_promotion",
                        lambda *_args: {"round": 4})
    monkeypatch.setattr("library.tools.reel_build._record_reel_versions",
                        lambda *_args, **_kwargs: [])
    monkeypatch.setattr("library.tools.plan_provenance.record_timeline_snapshot",
                        lambda *_args, **_kwargs: None)
    monkeypatch.setattr("library.tools.reel_retirement.delete_backups",
                        lambda *_args, **_kwargs: [])
    monkeypatch.setattr("library.tools.staging_holds.release_holds",
                        lambda *_args: None)

    recovered = reel_build.recover_interrupted_promotions(project, str(tmp_path))

    assert recovered == [transaction_id]
    assert candidate.GetName() == final
    assert original.GetName() == backup
    assert not journal_path.exists()
