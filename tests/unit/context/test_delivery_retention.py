"""Defect named: a delivery's own intermediates were never reclaimed.

The sign-off queues the lean retention cleanup. The sign-off happens
BEFORE the render, so the delivery's own intermediates - the pre-master
render in `scratch/`, the variant work directories - were written after
that cleanup and survived it. Nothing in the delivery path queued a
reclaim of its own, so the scratch a delivery wrote stayed on disk
until the NEXT sign-off, if there ever was one.

The delivery is the act that finishes the run's work, so it owns the
retention decision, exactly as a sign-off does. This file names that
defect and pins the fix: the delivery queues the same durable job, and
the worker does not start until the render has returned.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from library.tools import artifact_lifecycle, retention


def _qualified(project: Path) -> None:
    """Record a complete storage lifecycle qualification for the project."""
    from library.tools import storage_lifecycle

    rows = []
    for scenario_id in sorted(storage_lifecycle.STORAGE_QUALIFICATION_SCENARIOS):
        evidence = project / f"{scenario_id}.json"
        evidence.write_text(
            f'{{"scenario_id": "{scenario_id}", "passed": true}}\n',
            encoding="utf-8")
        rows.append({"scenario_id": scenario_id, "passed": True,
                     "evidence": [str(evidence)]})
    storage_lifecycle.record_cleanup_qualification(
        str(project), scenarios=rows, record_id="lifecycle-v1")


def _queued(project: Path) -> list[dict]:
    return artifact_lifecycle.pending_cleanup_jobs(str(project))


def test_a_delivery_queues_a_durable_reclaim_of_its_own_intermediates(
        tmp_path, monkeypatch):
    """Named defect: the delivery wrote scratch and nothing reclaimed it."""
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(retention, "retention_mode",
                        lambda **_kwargs: retention.LEAN)
    _qualified(project)
    delivered = project / "exports" / "Reel 01.mp4"
    delivered.parent.mkdir(parents=True)
    delivered.write_bytes(b"the finished render")

    summary = retention.after_delivery(
        str(project),
        lifecycle_event_id=retention.delivery_event_id(
            str(project), [str(delivered)]),
        outputs=[str(delivered)],
        start_worker=False)

    assert "queued as durable job" in summary
    jobs = _queued(project)
    assert len(jobs) == 1
    assert jobs[0]["reason"] == "successful reel delivery"


def test_a_delivery_that_produced_nothing_queues_nothing(tmp_path,
                                                          monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(retention, "retention_mode",
                        lambda **_kwargs: retention.LEAN)
    _qualified(project)

    summary = retention.after_delivery(
        str(project), lifecycle_event_id="delivery-empty", outputs=[])

    assert "produced no file" in summary
    assert _queued(project) == []


def test_keep_keeps_the_delivery_intermediates_too(tmp_path, monkeypatch):
    """The per-user D6 policy reads the same for a delivery as a sign-off."""
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(retention, "retention_mode",
                        lambda **_kwargs: retention.KEEP)
    delivered = project / "exports" / "Reel 01.mp4"
    delivered.parent.mkdir(parents=True)
    delivered.write_bytes(b"the finished render")

    summary = retention.after_delivery(
        str(project), lifecycle_event_id="delivery-keep",
        outputs=[str(delivered)], start_worker=False)

    assert retention.KEEP in summary
    assert _queued(project) == []


def test_a_delivery_without_a_qualification_record_reclaims_nothing(
        tmp_path, monkeypatch):
    """Fail-closed: no qualification evidence, no automatic destruction."""
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(retention, "retention_mode",
                        lambda **_kwargs: retention.LEAN)
    delivered = project / "exports" / "Reel 01.mp4"
    delivered.parent.mkdir(parents=True)
    delivered.write_bytes(b"the finished render")

    summary = retention.after_delivery(
        str(project), lifecycle_event_id="delivery-unqualified",
        outputs=[str(delivered)], start_worker=False)

    assert "qualification record" in summary
    assert _queued(project) == []


def test_the_delivery_event_id_moves_with_the_file_it_delivered(tmp_path):
    """A re-delivery is a different act and reclaims its own intermediates."""
    project = tmp_path / "project"
    project.mkdir()
    delivered = project / "exports" / "Reel 01.mp4"
    delivered.parent.mkdir(parents=True)
    delivered.write_bytes(b"first render")

    first = retention.delivery_event_id(str(project), [str(delivered)])
    again = retention.delivery_event_id(str(project), [str(delivered)])
    delivered.write_bytes(b"a different, longer render")
    changed = retention.delivery_event_id(str(project), [str(delivered)])

    assert first == again
    assert changed != first


def _signed_off_project(tmp_path, monkeypatch) -> Path:
    """One approved, signed-off reel in a project under `tmp_path`."""
    project = tmp_path / "pricing-show"
    project.mkdir()
    (project / "project.yaml").write_text(
        'name: "Pricing Show"\nslug: pricing-show\nproject_format_version: 1\n',
        encoding="utf-8")
    monkeypatch.setenv("PIPELINE_PROJECTS_ROOT", str(tmp_path))
    monkeypatch.setenv("REN_DELIVER_REVEAL", "0")

    from library.tools.reel_proposal import (Approval, ReelMoment,
                                             proposal_path, write_proposal)

    write_proposal(proposal_path(str(project)), [
        ReelMoment(number=1, slug="pricing", reason="the price lands",
                   timeline_start=10.0, timeline_end=40.0,
                   approval=Approval.APPROVED)], {})

    import manage_project
    from library.tools import assignment_spine

    assignment_spine.on_propose(str(project))
    assignment_spine.on_approve(str(project))
    act = assignment_spine.begin_build(str(project))
    assignment_spine.end_act(act, ok=True)
    manage_project.cmd_sign_off(argparse.Namespace(
        slug=str(project), reel="Reel 01 - pricing", note="ship it",
        by="captain", withdraw=False))
    return project


def _render_double():
    def _render(folder, reel, **kwargs):
        output = Path(folder) / "exports" / "Reel 01 - pricing.mp4"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"fixture-render")
        check = {"passed": True, "value": 30.0, "detail": "fixture"}
        return {
            "timeline_name": "Reel 01 - pricing", "reel": 1,
            "settings": {"needs_captain_word": []},
            "render": {"output_path": str(output)},
            "verification": {
                "output_path": str(output), "duration": dict(check),
                "resolution": dict(check), "audio": dict(check),
                "content_present": True, "opens_on_black": False,
                "passed": True},
            "delivered": True,
            "report_path": str(output) + ".deliver.json"}

    return _render


def _qc_double():
    def _qc(path, spec, expected_seconds, *, declared_silence_spans=None):
        return {"platform": spec.platform, "passed": True,
                "duration": {"passed": True}, "black_frames": {"passed": True},
                "loudness": {"passed": True}, "grade": {"passed": True},
                "captions": None}

    return _qc


def test_the_batch_queues_one_reclaim_for_everything_it_delivered(tmp_path,
                                                                  monkeypatch):
    """The batch is a delivery act too, and it owns the variants."""
    from library.tools import deliver_all

    project = _signed_off_project(tmp_path, monkeypatch)
    monkeypatch.setattr(retention, "retention_mode",
                        lambda **_kwargs: retention.LEAN)
    _qualified(project)

    report = deliver_all.deliver_all_approved(
        str(project), deliver_one=_render_double(), qc_one=_qc_double())

    assert len(report["delivered"]) == 1
    assert "queued as durable job" in report["retention_summary"]
    assert len(_queued(project)) == 1


def test_the_batch_leaves_the_reclamation_to_the_batch():
    """One job covers the masters and the variants together."""
    from library.tools import deliver_all, reel_deliver

    wrapped = deliver_all._batch_renderer(reel_deliver.deliver_reel)
    assert wrapped.keywords == {"retention_job": False}

    def _double(folder, reel, **kwargs):
        return {}

    assert deliver_all._batch_renderer(_double) is _double


def test_the_delivery_does_not_start_the_reclaim_while_it_is_rendering(
        tmp_path, monkeypatch):
    """The reclaim must not race the render reading its own scratch."""
    from library.tools import deliver_all

    project = _signed_off_project(tmp_path, monkeypatch)
    monkeypatch.setattr(retention, "retention_mode",
                        lambda **_kwargs: retention.LEAN)
    _qualified(project)
    started = []
    monkeypatch.setattr(retention, "resume_cleanup_jobs",
                        lambda *args, **kwargs: started.append(args))

    deliver_all.deliver_all_approved(
        str(project), deliver_one=_render_double(), qc_one=_qc_double())

    assert started == []
    assert len(_queued(project)) == 1
