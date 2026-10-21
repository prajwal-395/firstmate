"""The pileup: an aborted job and a lane's own change must not refuse
the next edit.

Two defects, measured on a scratch timeline two lanes shared
(2026-10-20, `docs/benchmarks/2026-10-20-live-user-visible-latency.md`):

- a job killed between its durable intent and its first Resolve write
  left a transaction the AXI-048 gate read as an outstanding recovery,
  so every later edit on that timeline was refused and each retry added
  another outstanding transaction (finding 4);
- a lane's own in-place write read as an "unattributed editor change",
  because the replace guard's baseline is a different ledger from the
  one the write was recorded in, so the difference refused the next
  edit.

A change nobody recorded - a human change - still refuses. That is the
line these tests hold.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest

# ── the journal a killed worker leaves behind ─────────────────────────


def _dead_pid() -> int:
    """A pid that is certainly not running any more."""
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return int(process.pid)


def _journal(project_folder, **fields) -> str:
    """Append one transaction event, as a worker would have written it.

    Written straight to the log because the point of the test is a
    worker that is NOT this process: the event carries that worker's pid
    and host, which is how a dead job is told from a live one.
    """
    from library.tools import event_log

    path = event_log.log_path(str(project_folder))
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "seq": 1,
        "at": str(fields.pop("at", datetime.now(UTC).isoformat(
            timespec="seconds"))),
        "host": str(fields.pop("host", socket.gethostname())),
        "pid": int(fields.pop("pid", os.getpid())),
        "type": event_log.EDIT_TRANSACTION_STAGE,
        "task": "axi-transaction",
        "fields": fields,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")
    return str(path)


def _last_stage(project_folder, transaction_id) -> str:
    from library.tools import event_log

    stages = [str((event.get("fields") or {}).get("stage") or "")
              for event in event_log.read_events(str(project_folder))
              if str((event.get("fields") or {}).get("transaction_id") or "")
              == transaction_id]
    return stages[-1] if stages else ""


# ── the recovery pileup ───────────────────────────────────────────────


def test_a_killed_job_that_never_wrote_does_not_refuse_the_next_edit(
        tmp_path):
    """Defect: one aborted job refused every later edit on the timeline.

    The job died after journalling its intent and before its first
    Resolve write, so it owed no recovery - but the gate read its
    transaction as outstanding and refused the next write, and every
    retry added another outstanding transaction.
    """
    from library.tools import axi_transaction

    project = tmp_path / "project"
    _journal(project, transaction_id="tx-killed", stage="prepared",
             project="Podcast", timeline="Reel 09", pid=_dead_pid())
    axi_transaction._JOURNAL_CURSORS.clear()

    state = axi_transaction.require_recovery_clear(
        str(project), project="Podcast", timeline="Reel 09")

    assert not state.unresolved
    assert _last_stage(project, "tx-killed") == "abandoned"


def test_a_transaction_that_may_have_written_still_refuses(tmp_path):
    """The recovery that is real is never auto-accepted.

    A job killed mid-write may have changed the timeline, so its
    transaction keeps its hold until a human recovers or abandons it.
    """
    from library.tools import axi_transaction

    project = tmp_path / "project"
    _journal(project, transaction_id="tx-uncertain", stage="operation_applying",
             project="Podcast", timeline="Reel 09", pid=_dead_pid(),
             operations=[{"op": "clip.delete"}])
    axi_transaction._JOURNAL_CURSORS.clear()

    with pytest.raises(axi_transaction.TransactionRefused,
                       match="unresolved recovery"):
        axi_transaction.require_recovery_clear(
            str(project), project="Podcast", timeline="Reel 09")

    assert _last_stage(project, "tx-uncertain") == "operation_applying"


def test_a_live_workers_transaction_is_not_swept_away(tmp_path):
    """A worker that is still running is not an aborted job.

    Its transaction is closed only when the worker that prepared it is
    gone; a live worker's prepared transaction is left alone.
    """
    from library.tools import axi_transaction

    project = tmp_path / "project"
    _journal(project, transaction_id="tx-live", stage="prepared",
             project="Podcast", timeline="Reel 09", pid=os.getpid())
    axi_transaction._JOURNAL_CURSORS.clear()

    with pytest.raises(axi_transaction.TransactionRefused,
                       match="unresolved recovery"):
        axi_transaction.require_recovery_clear(
            str(project), project="Podcast", timeline="Reel 09")

    assert _last_stage(project, "tx-live") == "prepared"


def test_the_sweep_only_touches_the_timeline_it_was_asked_about(tmp_path):
    """A closed transaction on another timeline is left as it was."""
    from library.tools import axi_transaction

    project = tmp_path / "project"
    _journal(project, transaction_id="tx-other", stage="prepared",
             project="Podcast", timeline="Reel 08", pid=_dead_pid())
    axi_transaction._JOURNAL_CURSORS.clear()

    state = axi_transaction.require_recovery_clear(
        str(project), project="Podcast", timeline="Reel 09")

    assert not state.unresolved
    assert _last_stage(project, "tx-other") == "prepared"


# ── an interrupted write closes its own transaction ───────────────────


def test_an_interrupted_write_that_never_reached_resolve_closes_itself(
        tmp_path, monkeypatch):
    """Defect: an interrupted patch left its transaction at 'prepared'.

    The AXI-048 gate then read it as an outstanding recovery, and the
    next edit on that timeline was refused for a write that never
    happened.
    """
    from library.tools import edit_patch, event_log
    from library.tools import timeline_shadow as shadow
    from library.tools.resolve_lock import assume_sole_writer
    from tests.resolve_double import (
        FakeResolve,
        FakeTimeline,
        make_pool_clip,
        make_project,
        place_clip,
    )

    project = make_project("Podcast")
    timeline = project.adopt(FakeTimeline("Reel 09", project=project))
    item = place_clip(timeline, make_pool_clip("a.mov"), 0, 47)
    project.SetCurrentTimeline(timeline)
    store = shadow.ShadowStore(tmp_path / "shadow.db")
    project_folder = tmp_path / "project"

    def interrupted(*args, **kwargs):
        raise RuntimeError("the worker was killed before the write")

    with assume_sole_writer("canonical Resolve double"):
        base = shadow.observe(project, timeline, store)
        monkeypatch.setattr(edit_patch, "execute_physical_patch", interrupted)
        patch = {
            "id": "patch-interrupted", "project": "Podcast",
            "timeline": "Reel 09", "base_generation": base.generation,
            "capability": "reel.touchup", "affected_spans": [(0, 48)],
            "conflict_domains": ["timeline_structure"],
            "operations": [{"op": "clip.delete",
                            "unique_id": item.GetUniqueId()}]}
        with pytest.raises(RuntimeError):
            edit_patch.apply_patch(
                patch, resolve=FakeResolve(project), project=project,
                timeline=timeline, store=store,
                project_folder=str(project_folder))

    stages = [str((event.get("fields") or {}).get("stage") or "")
              for event in event_log.read_events(str(project_folder))]
    assert stages[-1] == "abandoned"
    assert "prepared" in stages


def test_an_interrupted_write_that_may_have_landed_keeps_its_hold(
        tmp_path, monkeypatch):
    """A write that was attempted keeps the honest hold."""
    from library.tools import edit_patch, event_log
    from library.tools import timeline_shadow as shadow
    from library.tools.resolve_lock import assume_sole_writer
    from tests.resolve_double import (
        FakeResolve,
        FakeTimeline,
        make_pool_clip,
        make_project,
        place_clip,
    )

    project = make_project("Podcast")
    timeline = project.adopt(FakeTimeline("Reel 09", project=project))
    item = place_clip(timeline, make_pool_clip("a.mov"), 0, 47)
    project.SetCurrentTimeline(timeline)
    store = shadow.ShadowStore(tmp_path / "shadow.db")
    project_folder = tmp_path / "project"

    def interrupted(*args, **kwargs):
        raise RuntimeError("the worker was killed mid-write")

    with assume_sole_writer("canonical Resolve double"):
        base = shadow.observe(project, timeline, store)
        monkeypatch.setattr(edit_patch, "execute_physical_patch", interrupted)
        patch = {
            "id": "patch-midwrite", "project": "Podcast",
            "timeline": "Reel 09", "base_generation": base.generation,
            "capability": "reel.touchup", "affected_spans": [(0, 48)],
            "conflict_domains": ["timeline_structure"],
            "operations": [{"op": "clip.delete",
                            "unique_id": item.GetUniqueId()}]}
        # The write is attempted: the journal says so before the call.
        original = edit_patch._emit_transaction_stage

        def journalling(folder, patch_obj, stage, **fields):
            if stage == "applying":
                fields["index"] = 0
            return original(folder, patch_obj, stage, **fields)

        monkeypatch.setattr(edit_patch, "_emit_transaction_stage", journalling)
        monkeypatch.setattr(
            edit_patch, "execute_physical_patch",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("the worker was killed mid-write")))

        # The attempt is recorded, then the write raises.
        def attempted_then_failed(patch_obj, **kwargs):
            attempted = kwargs.get("attempted")
            if attempted is not None:
                attempted["write"] = True
            raise RuntimeError("the worker was killed mid-write")

        monkeypatch.setattr(edit_patch, "execute_physical_patch",
                            attempted_then_failed)
        with pytest.raises(RuntimeError):
            edit_patch.apply_patch(
                patch, resolve=FakeResolve(project), project=project,
                timeline=timeline, store=store,
                project_folder=str(project_folder))

    stages = [str((event.get("fields") or {}).get("stage") or "")
              for event in event_log.read_events(str(project_folder))]
    assert stages[-1] == "recovery_required"


# ── a lane's own change is attributed ─────────────────────────────────


def _item(**overrides) -> dict:
    item = {
        "track_type": "video",
        "track_index": 1,
        "track_name": "V1",
        "name": "a.mov",
        "source_file": "/media/a.mov",
        "media_pool_item_id": "mp-1",
        "unique_id": "clip-1",
        "source_identity": "file:/media/a.mov",
        "source_in_frame": 0,
        "source_out_frame": 100,
        "record_in": 0,
        "record_out": 100,
        "duration": 100,
        "enabled": True,
        "transform": {},
        "composite": {},
        "fusion": {},
        "color": {},
        "clip_color": "",
        "flags": [],
        "markers": [],
    }
    item.update(overrides)
    return item


def _tracks(*items) -> list:
    return [{"type": "video", "index": 1, "name": "V1",
             "clips": [dict(item) for item in items]}]


def test_a_lane_write_is_attributed_and_does_not_refuse_the_next_edit(
        tmp_path):
    """Defect: a lane's own in-place write refused the next edit.

    The write was recorded in the project's event journal with the state
    it read back, but the replace guard's baseline is the project's
    timeline snapshot - a different ledger. The difference therefore read
    as an "unattributed editor change" and refused every later edit on
    the shared scratch timeline.
    """
    from library.tools import plan_provenance, reel_replace_guard

    final = "Reel 09 - lane write"
    project_folder = tmp_path / "project"
    review_dir = project_folder / "pipeline_output" / "review"
    before_item = _item()
    after_item = _item(record_in=10, record_out=110)
    before = {"items": reel_replace_guard.snapshot_items(_tracks(before_item))}
    after = {"items": reel_replace_guard.snapshot_items(_tracks(after_item))}
    plan_provenance.record_timeline_snapshot(
        str(review_dir), final, before, action="test baseline")

    # The lane's own write, recorded with the state it read back.
    _journal(project_folder, transaction_id="tx-lane",
             stage="observed", project="Podcast", timeline=final,
             at=(datetime.now(UTC) + timedelta(seconds=5)).isoformat(
                 timespec="seconds"),
             actual_state={"tracks": _tracks(after_item)})

    detection = reel_replace_guard.detect_editor_changes(
        str(project_folder), final, after, before)

    assert detection["pending"] == []
    assert detection["detected"][0]["status"] == "attributed"
    assert detection["detected"][0]["attribution"]["transaction_id"] \
        == "tx-lane"
    # And the guard does not refuse the edit.
    report = reel_replace_guard.protect_editor_changes(
        str(project_folder), final, after, before, after, detection=detection)
    assert report["detected"][0]["status"] == "attributed"


def test_a_human_change_is_still_refused(tmp_path):
    """The line: a change nobody recorded is never auto-accepted.

    The journal records a lane's write, but the live timeline is not
    that state - something else moved it. The difference stays
    unattributed and the guard refuses.
    """
    from library.tools import plan_provenance, reel_replace_guard

    final = "Reel 09 - human change"
    project_folder = tmp_path / "project"
    review_dir = project_folder / "pipeline_output" / "review"
    before_item = _item()
    written_item = _item(record_in=10, record_out=110)
    human_item = _item(record_in=20, record_out=120)
    before = {"items": reel_replace_guard.snapshot_items(_tracks(before_item))}
    after = {"items": reel_replace_guard.snapshot_items(_tracks(human_item))}
    plan_provenance.record_timeline_snapshot(
        str(review_dir), final, before, action="test baseline")
    _journal(project_folder, transaction_id="tx-lane",
             stage="observed", project="Podcast", timeline=final,
             at=(datetime.now(UTC) + timedelta(seconds=5)).isoformat(
                 timespec="seconds"),
             actual_state={"tracks": _tracks(written_item)})

    detection = reel_replace_guard.detect_editor_changes(
        str(project_folder), final, after, before)

    assert detection["pending"]
    assert detection["pending"][0]["status"] == "pending"
    # The rebuild's staged timeline does not carry the change, so the
    # guard refuses to replace the timeline over it.
    with pytest.raises(reel_replace_guard.EditorChangeRefused):
        reel_replace_guard.protect_editor_changes(
            str(project_folder), final, after, before, before,
            detection=detection)


def test_another_lanes_staging_timeline_is_not_filed_as_the_editors(
        tmp_path):
    """Defect: a lane's staging copy read as the editor's timeline.

    Two lanes working one scratch project each create their own staging
    copy there. Filing the other lane's copy as the editor's made the
    next lane refuse to touch its own work.
    """
    from library.tools import plan_provenance

    review_dir = tmp_path / "pipeline_output" / "review"
    staging = {"name": "Reel 09 - lane [staging]", "unique_id": "tl-2"}
    editor = {"name": "The captain's timeline", "unique_id": "tl-9"}

    # The first lane's operation, which declared the staging copy.
    operation = plan_provenance.begin_timeline_inventory(
        str(review_dir), "build promotion", [{"name": "Reel 09", "unique_id": "tl-1"}],
        ren_created_names={"Reel 09 - lane [staging]"},
        ren_created_ids={"tl-2"})
    plan_provenance.finish_timeline_inventory(
        str(review_dir), operation, [staging])

    # The second lane inventories the project and finds the first lane's
    # staging copy - created while its own inventory was open.
    plan_provenance.record_unattributed_timeline_changes(
        str(review_dir), [{"name": "Reel 09", "unique_id": "tl-1"}],
        [staging, editor], operation="touchup")

    filed = plan_provenance.editor_timeline_identities(str(review_dir))
    assert "id:tl-2" not in filed
    # A timeline nobody declared is still the editor's.
    assert "id:tl-9" in filed
