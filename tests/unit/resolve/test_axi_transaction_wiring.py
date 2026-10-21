"""Defect-naming tests for the AXI transaction wiring.

Each test names a defect that was open in the execution path before the
transaction envelope was connected to it: a canonical submission the
broker refused before it could run, a batch written to a timeline whose
identity had moved, a write that landed while a recovery was still
unresolved, and a receipt that did not say what had been left alone.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


def _request(**overrides):
    request = SimpleNamespace(
        id="request-1", idempotency_key="request-1", project="Project",
        timeline="Timeline", expected_base_generation=7,
        expected_model_revision=None, preconditions=(), preservation=())
    for key, value in overrides.items():
        setattr(request, key, value)
    return request


def _head(**overrides):
    head = SimpleNamespace(project="Project", timeline_name="Timeline",
                           generation=7, model_revision=None)
    for key, value in overrides.items():
        setattr(head, key, value)
    return head


def _generation(generation=7):
    return SimpleNamespace(generation=generation, project="Project",
                           timeline_name="Timeline")


class _Compiled:
    """The compiled-edit shape the submit path reads."""

    operations = ({"op": "clip.trim", "unique_id": "clip-1",
                   "in_frame": 1, "out_frame": 2},)
    affected_spans = ((0, 10),)
    conflict_domains = ()

    def to_patch(self, capability, *, base_generation, project, timeline,
                 idempotency_key, preconditions=(), postconditions=(),
                 preservation=()):
        return {
            "id": idempotency_key, "project": project, "timeline": timeline,
            "base_generation": base_generation, "capability": capability,
            "affected_spans": [list(span) for span in self.affected_spans],
            "conflict_domains": list(self.conflict_domains),
            "operations": list(self.operations),
            "preconditions": list(preconditions),
            "postconditions": list(postconditions),
            "preservation": list(preservation),
        }


# ── AXI-046: the submission that could not happen at all ──────────────

def test_the_broker_refuses_a_second_idempotency_key_on_an_edit_patch_job():
    """The broker's own rule, stated so the wiring below cannot drift."""
    from library.tools.resolved import jobs

    patch = {"id": "request-1", "project": "Project", "timeline": "Timeline"}

    with pytest.raises(jobs.JobRefused, match="uses patch.id"):
        jobs.idempotency_request("timeline.apply_patch", {"patch": patch},
                                 "request-1")


def test_the_semantic_edit_patch_submission_sends_no_second_idempotency_key(
        monkeypatch):
    """Defect: the canonical route was refused at the broker.

    The patch id already IS the request's idempotency key, so passing
    the key a second time asked the broker to refuse the submission.
    """
    from library.tools import resolve_axi_core
    from library.tools.edit_request import EditRequest
    from library.tools.resolved import client

    request = EditRequest.from_dict({
        "id": "request-1", "project": "Project", "timeline": "Timeline",
        "operations": [{
            "op": "clip.trim", "target": {"unique_id": "clip-1"},
            "params": {"in_frame": 1, "out_frame": 2},
            "expected_effect": "Trim clip-1"}],
    })
    generation = SimpleNamespace(generation=7)
    compiled = _Compiled()
    monkeypatch.setattr(resolve_axi_core, "_patch_capability",
                        lambda _compiled: "edit_patch")
    monkeypatch.setattr(resolve_axi_core, "_prepare_request_plan",
                        lambda _request: (
                            {"backend": "edit_patch",
                             "routing": {"backend": "edit_patch"}},
                            request, object(), compiled, generation, {}))
    monkeypatch.setattr(client, "ensure", lambda *args, **kwargs: None)
    seen = []
    monkeypatch.setattr(client, "submit", lambda *args, **kwargs: (
        seen.append((args, kwargs)) or {"id": "job-1", "coalesced": False}))

    resolve_axi_core.submit_edit_request_async(request, authorized=True)

    assert seen[0][0][0] == "timeline.apply_patch"
    assert "idempotency_key" not in seen[0][1]


# ── AXI-045: the batch written to a timeline whose identity moved ──────

def test_a_batch_whose_live_timeline_is_not_the_one_named_is_refused():
    from library.tools.resolve_axi_core import _pre_mutation_refusal

    blocked = _pre_mutation_refusal(
        request=_request(), generation=_generation(),
        head=_head(timeline_name="Timeline 2"))

    assert blocked is not None
    assert blocked["category"] == "conflict"
    assert "Timeline 2" in blocked["reason"]


def test_a_batch_whose_live_project_is_not_the_one_named_is_refused():
    from library.tools.resolve_axi_core import _pre_mutation_refusal

    blocked = _pre_mutation_refusal(
        request=_request(), generation=_generation(),
        head=_head(project="Project 2"))

    assert blocked is not None
    assert blocked["category"] == "conflict"


def test_a_batch_whose_generation_moved_is_refused_as_stale():
    from library.tools.resolve_axi_core import _pre_mutation_refusal

    blocked = _pre_mutation_refusal(
        request=_request(), generation=_generation(generation=7),
        head=_head(generation=8))

    assert blocked is not None
    assert blocked["category"] == "stale"


def test_a_batch_whose_project_model_revision_moved_is_refused():
    from library.tools.resolve_axi_core import _pre_mutation_refusal

    blocked = _pre_mutation_refusal(
        request=_request(expected_model_revision=3), generation=_generation(),
        head=_head(model_revision=4))

    assert blocked is not None
    assert blocked["category"] == "stale"


def test_a_batch_on_an_unchanged_identity_is_not_refused():
    from library.tools.resolve_axi_core import _pre_mutation_refusal

    assert _pre_mutation_refusal(
        request=_request(), generation=_generation(), head=_head()) is None


# ── AXI-048: a write that landed while a recovery was unresolved ──────

def test_a_batch_is_refused_while_the_timeline_owes_a_recovery(monkeypatch,
                                                               tmp_path):
    from library.tools import axi_transaction
    from library.tools.resolve_axi_core import _pre_mutation_refusal

    monkeypatch.setattr("library.tools.resolve_axi_core._project_folder_for",
                        lambda _request: str(tmp_path))
    monkeypatch.setattr(axi_transaction, "_journal_events", lambda _folder: [
        {"fields": {"transaction_id": "tx-old", "stage": "operation_applying",
                    "project": "Project", "timeline": "Timeline",
                    "index": 0, "operations": [{"op": "clip.delete"}]}}])

    blocked = _pre_mutation_refusal(
        request=_request(), generation=_generation(), head=_head())

    assert blocked is not None
    assert blocked["category"] == "recovery_required"
    assert "tx-old" in blocked["reason"]


def test_a_batch_is_allowed_once_the_recovery_has_committed(monkeypatch,
                                                            tmp_path):
    from library.tools import axi_transaction
    from library.tools.resolve_axi_core import _pre_mutation_refusal

    monkeypatch.setattr("library.tools.resolve_axi_core._project_folder_for",
                        lambda _request: str(tmp_path))
    monkeypatch.setattr(axi_transaction, "_journal_events", lambda _folder: [
        {"fields": {"transaction_id": "tx-old", "stage": "committed",
                    "project": "Project", "timeline": "Timeline"}}])

    assert _pre_mutation_refusal(
        request=_request(), generation=_generation(), head=_head()) is None


def test_the_edit_patch_path_refuses_a_write_while_a_recovery_is_unresolved(
        monkeypatch, tmp_path):
    """Defect: the single-operation path wrote onto an unsettled timeline."""
    from library.tools import axi_transaction, edit_patch
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
    with assume_sole_writer("canonical Resolve double"):
        base = shadow.observe(project, timeline, store)
        monkeypatch.setattr(axi_transaction, "_journal_events",
                            lambda _folder: [
            {"fields": {
                "transaction_id": "tx-old", "stage": "operation_applying",
                "project": "Podcast", "timeline": "Reel 09", "index": 0,
                "operations": [{"op": "clip.delete"}]}}])
        patch = {
            "id": "patch-1", "project": "Podcast", "timeline": "Reel 09",
            "base_generation": base.generation, "capability": "reel.touchup",
            "affected_spans": [(0, 48)], "conflict_domains": ["timeline_structure"],
            "operations": [{"op": "clip.delete",
                            "unique_id": item.GetUniqueId()}]}

        with pytest.raises(edit_patch.PatchRefused,
                           match="unresolved recovery"):
            edit_patch.apply_patch(
                patch, resolve=FakeResolve(project), project=project,
                timeline=timeline, store=store,
                project_folder=store.path.parent)


# ── the recovery anchor on the broker's patch path ───────────────────

def test_the_broker_patch_path_finds_its_project_journal(monkeypatch, tmp_path):
    """Defect: the broker's patch job names the RESOLVE project and the
    journal is per Ren project, so the production path had no recovery
    anchor - and a write that failed there was owed a recovery nobody
    could see."""
    from library.tools import edit_patch

    monkeypatch.setattr(edit_patch, "_JOURNAL_FOLDERS", {})
    monkeypatch.setattr(
        "library.tools.project_registry.project_folder_for_resolve_name",
        lambda name, root=None: (str(tmp_path) if name == "Podcast"
                                  else None))

    assert edit_patch.journal_folder_for(None, "Podcast") == str(tmp_path)
    assert edit_patch.journal_folder_for(None, "Other") == ""
    assert edit_patch.journal_folder_for("/explicit", "Podcast") == "/explicit"


def test_a_broker_patch_journals_its_transaction(monkeypatch, tmp_path):
    """The broker names the Resolve project; the journal is per Ren
    project. The stages must still reach the journal, or the write has
    no recovery anchor."""
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
    monkeypatch.setattr(edit_patch, "_JOURNAL_FOLDERS", {})
    monkeypatch.setattr(
        "library.tools.project_registry.project_folder_for_resolve_name",
        lambda name, root=None: str(tmp_path))
    with assume_sole_writer("canonical Resolve double"):
        base = shadow.observe(project, timeline, store)
        patch = {
            "id": "broker-patch", "project": "Podcast", "timeline": "Reel 09",
            "base_generation": base.generation, "capability": "reel.touchup",
            "affected_spans": [(0, 48)],
            "conflict_domains": ["timeline_structure"],
            "operations": [{"op": "clip.delete",
                            "unique_id": item.GetUniqueId()}]}

        receipt = edit_patch.apply_patch(
            patch, resolve=FakeResolve(project), project=project,
            timeline=timeline, store=store)

    assert receipt["status"] == "committed"
    assert receipt["journal"]["intent_persisted"] is True
    stages = [event["fields"]["stage"] for event in
              event_log.read_events(str(tmp_path))]
    assert "prepared" in stages
    assert stages[-1] == "committed"


# ── AXI-049: what the receipt says ────────────────────────────────────
def test_the_batch_receipt_names_the_regions_it_left_alone():
    from library.tools.resolve_axi_core import _registered_execution_receipt

    operation = {"op": "clip.trim", "unique_id": "clip-1",
                 "in_frame": 12, "out_frame": 20}
    adapter_receipt = {
        "status": "committed", "backend": "resolve_axi",
        "execution_contract": {
            "verification": {
                "status": "verified",
                "checks": ({"operation": "clip.trim", "passed": True},),
                "evidence": {"readback": {"record_range": [12, 20]}},
            },
            "recovery": {"status": "available", "reference": {
                "kind": "undo_journal_entry", "project_folder": "/project",
                "entry_id": "entry-1"}},
        },
    }

    receipt = _registered_execution_receipt(
        backend="resolve_axi", plan={"routing": {"backend": "resolve_axi"}},
        request=_request(), operations=[operation],
        receipts=[adapter_receipt], base_generation=4, committed_generation=5,
        full_request_verification={"untouched_regions": [
            {"unique_id": "clip-2", "record_in": 20, "record_out": 60,
             "track_type": "video", "status": "unchanged"}]})

    assert receipt["requested_changes"] == [operation]
    assert receipt["actual_changes"] == [adapter_receipt]
    assert receipt["untouched_regions"][0]["unique_id"] == "clip-2"
    assert receipt["untouched_regions"][0]["status"] == "unchanged"
    assert receipt["route"]["backend"] == "resolve_axi"
    assert receipt["errors"] == []
    assert receipt["recovery"]["status"] == "available"
    assert receipt["verification_evidence"]
    assert receipt["state_ladder"]["stage"] == "COMMITTED"


def test_the_batch_receipt_reports_the_compound_shape_it_ran_as():
    from library.tools.resolve_axi_core import _registered_execution_receipt

    receipt = _registered_execution_receipt(
        backend="resolve_axi", plan={"routing": {"backend": "resolve_axi"}},
        request=_request(),
        operations=[{"op": "clip.trim", "unique_id": "clip-1",
                     "in_frame": 12, "out_frame": 20}],
        receipts=[{"status": "committed", "execution_contract": {
            "verification": {"status": "verified", "checks": (
                {"operation": "clip.trim", "passed": True},),
                "evidence": {"readback": {"record_range": [12, 20]}}},
            "recovery": {"status": "available", "reference": {
                "kind": "undo_journal_entry", "project_folder": "/project",
                "entry_id": "entry-1"}}}}],
        base_generation=4, committed_generation=5)

    # A single-operation write is not a compound transaction and does
    # not claim to be one.
    assert receipt["atomicity"] == "sequential_non_atomic"
