"""Learned context: what the run writes back so it stops repeating itself.

A SEPARATE pipeline-owned area (`learned_context/`), never the captain's
`context/`; a learning nothing reads is refused at record time; one is
retired or corrected with a reason, never silently edited. Background:
docs/evidence/context_projection.md#learned-context.
"""

import json
import sys
import threading
from pathlib import Path

import pytest

from library.tools import learned_context

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))


def test_a_learning_with_no_reader_is_refused(tmp_path):
    """AGENTS.md 10.4's rule, at record time: a learned fact nothing
    consumes is the defect, so it never lands."""
    from library.tools import learned_context
    with pytest.raises(learned_context.LearnedContextError):
        learned_context.record(
            str(tmp_path), kind="correction",
            statement="Something was learned by nobody in particular.",
            read_by=[],
        )
    with pytest.raises(learned_context.LearnedContextError):
        learned_context.record(
            str(tmp_path), kind="correction",
            statement="Something was learned for a step that is not a step.",
            read_by=["plan_everything"],
        )


def test_a_pending_proposal_reaches_no_prompt_until_promoted(tmp_path):
    """The 2026-09-19 Sheehan defect: an uncertain model proposal
    recorded ACTIVE auto-applied. Pending is recorded, never enforced -
    until a human promotes it with the confirmation as the reason."""
    from library.tools import learned_context
    rec = learned_context.record(
        str(tmp_path), kind="mistake_fix",
        statement="Model-proposed spelling: Sheehan reads as she even.",
        read_by=["*"], status="pending")
    assert rec["status"] == "pending"
    assert learned_context.active_for_step(str(tmp_path), "*") == []
    assert learned_context.render_for_prompt(
        str(tmp_path), "music_selection") == ""
    assert [l["id"] for l in learned_context.pending(
        str(tmp_path))] == [rec["id"]]
    promoted = learned_context.promote(
        str(tmp_path), rec["id"],
        reason="Captain 2026-09-19: it's not a name, keep the fix.")
    assert promoted["status"] == "active"
    assert promoted["history"][-1]["event"] == "promoted"
    assert len(learned_context.active_for_step(
        str(tmp_path), "*")) == 1


def test_pending_is_refused_a_reasonless_promotion(tmp_path):
    from library.tools import learned_context
    rec = learned_context.record(
        str(tmp_path), kind="mistake_fix",
        statement="Unsure guess.", read_by=["*"], status="pending")
    with pytest.raises(learned_context.LearnedContextError):
        learned_context.promote(str(tmp_path), rec["id"], reason="  ")
    # ...and only a pending learning promotes at all.
    active = learned_context.record(
        str(tmp_path), kind="mistake_fix",
        statement="Confident fix.", read_by=["*"])
    with pytest.raises(learned_context.LearnedContextError):
        learned_context.promote(
            str(tmp_path), active["id"], reason="Already decided.")
    with pytest.raises(learned_context.LearnedContextError):
        learned_context.record(
            str(tmp_path), kind="mistake_fix",
            statement="A learning that starts ended.",
            read_by=["*"], status="retired")


def test_a_pending_proposal_retires_without_promotion(tmp_path):
    """Rejecting a held proposal must not require confirming it first:
    promoting in order to retire would write a confirmation that never
    happened."""
    from library.tools import learned_context
    rec = learned_context.record(
        str(tmp_path), kind="mistake_fix",
        statement="Unsure suppression.", read_by=["*"], status="pending")
    retired = learned_context.retire(
        str(tmp_path), rec["id"],
        reason="Captain: that's her voice, not a stutter.")
    assert retired["status"] == "retired"
    assert learned_context.pending(str(tmp_path)) == []


def test_a_pending_proposal_is_corrected_only_after_promotion(tmp_path):
    from library.tools import learned_context
    rec = learned_context.record(
        str(tmp_path), kind="mistake_fix",
        statement="Unsure guess.", read_by=["*"], status="pending")
    with pytest.raises(learned_context.LearnedContextError):
        learned_context.correct(
            str(tmp_path), rec["id"], new_statement="Better guess.",
            reason="Settled it.")
    learned_context.promote(
        str(tmp_path), rec["id"], reason="Captain: keep, with a tweak.")
    new = learned_context.correct(
        str(tmp_path), rec["id"], new_statement="Better guess.",
        reason="Settled it.")
    assert new["supersedes"] == rec["id"]


def test_concurrent_records_keep_every_record_with_unique_ids(tmp_path):
    """The retake-detection lane's refusal, exercised: N writers racing
    `record()` must lose nothing and mint no id twice. Barrier-synced
    threads maximise the overlap of the old load-mint-save window; on
    the unlocked code this fails with lost records and duplicate ids.
    Uses a temporary store only - never a real project store."""
    writers, per_writer = 8, 10
    total = writers * per_writer
    barrier = threading.Barrier(writers)
    failures = []

    def write_batch(slot):
        try:
            barrier.wait(timeout=30)
            for n in range(per_writer):
                learned_context.record(
                    str(tmp_path), kind="correction",
                    statement=f"Writer {slot} learning {n}.",
                    read_by=["plan_transitions"])
        except Exception as exc:  # noqa: BLE001 - collected, then raised
            failures.append(exc)

    threads = [threading.Thread(target=write_batch, args=(s,))
               for s in range(writers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)

    assert not failures, f"writers raised: {failures!r}"
    store = json.loads(
        (tmp_path / "learned_context" / "learnings.json").read_text(
            encoding="utf-8"))
    assert len(store) == total, (
        f"{total - len(store)} concurrent records lost")
    ids = [l["id"] for l in store]
    assert len(set(ids)) == total, "two writers minted the same id"
    assert set(ids) == {f"lc-{n:04d}" for n in range(1, total + 1)}


def test_concurrent_retires_do_not_clobber_each_other(tmp_path):
    """Two lanes retiring different learnings at once: the classic
    last-writer-wins clobber on the unlocked code un-retired one of
    them. Every retirement must survive."""
    count = 8
    ids = [learned_context.record(
        str(tmp_path), kind="mistake_fix",
        statement=f"Fix {n}.", read_by=["music_selection"])["id"]
        for n in range(count)]
    barrier = threading.Barrier(count)
    failures = []

    def retire_one(learning_id):
        try:
            barrier.wait(timeout=30)
            learned_context.retire(
                str(tmp_path), learning_id,
                reason=f"{learning_id} no longer applies.")
        except Exception as exc:  # noqa: BLE001 - collected, then raised
            failures.append(exc)

    threads = [threading.Thread(target=retire_one, args=(i,)) for i in ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)

    assert not failures, f"retirers raised: {failures!r}"
    store = json.loads(
        (tmp_path / "learned_context" / "learnings.json").read_text(
            encoding="utf-8"))
    assert len(store) == count
    assert {l["status"] for l in store} == {"retired"}, (
        "a concurrent retirement was clobbered away")


def test_learnings_reach_a_declaring_step_through_gather(tmp_path):
    from library.processes.edit_video.run_pipeline import gather_step_inputs
    from library.tools import learned_context
    learned_context.record(
        str(tmp_path), kind="correction",
        statement="No crash zooms on eating shots.",
        read_by=["plan_transitions"])
    dag = {"edges": []}
    manifest = {"interface": {"inputs": [
        {"name": "project_context", "type": "string", "required": False},
        {"name": "project_folder", "type": "string", "required": False},
    ]}}
    inputs = gather_step_inputs(
        "plan_transitions", dag, {"project_folder": str(tmp_path)},
        manifest=manifest, step_type="llm_only")
    assert "No crash zooms" in inputs.get("project_context", "")
