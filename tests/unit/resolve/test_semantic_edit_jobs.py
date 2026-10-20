"""Semantic AXI requests use durable, broker-owned execution jobs."""

from types import SimpleNamespace

import pytest


def _request():
    from library.tools.edit_request import EditRequest

    return EditRequest.from_dict({
        "id": "semantic-request-1",
        "project": "Project",
        "timeline": "Timeline",
        "operations": [{
            "op": "clip.trim",
            "target": {"unique_id": "clip-1"},
            "params": {"in_frame": 10, "out_frame": 20},
            "expected_effect": "Trim clip-1 to the requested record range",
        }],
    })


def test_semantic_job_is_exclusive_durable_and_safely_cancellable():
    from library.tools.resolved import jobs

    request = _request()
    params = {"request": request.to_dict(), "backend": "incremental",
              "base_generation": 7}
    shape = jobs.prepare("timeline.semantic_edit", params)

    assert shape["mode"] == "exclusive"
    assert shape["executed"] is True
    assert shape["project"] == request.project
    assert shape["timeline"] == request.timeline
    assert shape["interrupt_policy"] == "commit:verified-semantic-generation"
    assert jobs.idempotency_request(
        "timeline.semantic_edit", params, request.idempotency_key)[0] == request.id


def test_semantic_submit_queues_registered_execution_through_broker(monkeypatch):
    from library.tools import resolve_axi_core
    from library.tools.resolved import client

    request = _request()
    generation = SimpleNamespace(generation=7)
    plan = {"backend": "incremental", "routing": {"backend": "incremental"}}
    monkeypatch.setattr(resolve_axi_core, "_prepare_request_plan", lambda _request: (
        plan, request, object(), object(), generation, {}))
    seen = []
    monkeypatch.setattr(client, "submit", lambda *args, **kwargs: (
        seen.append((args, kwargs)) or {"id": "broker-job-1", "coalesced": False}))

    submitted = resolve_axi_core.submit_edit_request_async(
        request, authorized=True, owner="ren revise")

    assert submitted["id"] == "broker-job-1"
    assert submitted["status"] == "queued"
    assert seen[0][0][0] == "timeline.semantic_edit"
    assert seen[0][0][1]["base_generation"] == 7
    assert seen[0][1]["idempotency_key"] == request.idempotency_key


def test_semantic_status_uses_persisted_job_state_and_durations(monkeypatch):
    from library.tools import resolve_axi_core
    from library.tools.resolved import client

    monkeypatch.setattr(client, "result", lambda job_id, wait: {
        "id": job_id, "state": "done", "result": {"status": "committed"},
        "wait_seconds": 0.2, "hold_seconds": 1.4})

    job = resolve_axi_core.retrieve_edit_request("broker-job-1", wait=2)

    assert job["status"] == "completed"
    assert job["state"] == "done"
    assert job["result"]["status"] == "committed"
    assert job["wait_seconds"] == 0.2
    assert job["hold_seconds"] == 1.4


def test_semantic_refusal_state_has_one_public_vocabulary(monkeypatch):
    from library.tools import resolve_axi_core
    from library.tools.resolved import client

    monkeypatch.setattr(client, "result", lambda job_id, wait: {
        "id": job_id, "state": "rejected", "result": {"status": "failed"},
        "error": "stale generation", "wait_seconds": 0.1,
        "hold_seconds": 0.0})

    job = resolve_axi_core.retrieve_edit_request("broker-job-2")

    assert job["status"] == "refused"
    assert job["state"] == "rejected"
    assert job["error"] == "stale generation"


def test_already_satisfied_semantic_request_does_not_start_broker(monkeypatch):
    from library.tools import resolve_axi_core
    from library.tools.resolved import client

    request = _request()
    generation = SimpleNamespace(generation=7)
    plan = {"backend": "no_op", "routing": {"backend": "no_op"}}
    monkeypatch.setattr(resolve_axi_core, "_prepare_request_plan", lambda _request: (
        plan, request, object(), object(), generation, {}))
    monkeypatch.setattr(client, "submit", lambda *args, **kwargs: (
        pytest.fail("already-satisfied request submitted to broker")))
    monkeypatch.setattr(client, "ensure", lambda *args, **kwargs: (
        pytest.fail("already-satisfied request started broker")))

    submitted = resolve_axi_core.submit_edit_request_async(
        request, authorized=True)

    assert submitted["status"] == "already_satisfied"
    assert submitted["receipt"]["lifecycle"]["stage"] == "VERIFIED"
    assert submitted["receipt"]["execution_contract"]["verification"][
        "evidence"]["resolve_contacted"] is False


def test_semantic_cancel_always_uses_broker_cancellation(monkeypatch):
    from library.tools import resolve_axi_core
    from library.tools.resolved import client

    calls = []
    monkeypatch.setattr(client, "call", lambda payload: (
        calls.append(payload) or {"job": {"id": payload["id"],
                                           "state": "cancelled"}}))

    result = resolve_axi_core.cancel_edit_request("broker-job-1")

    assert result["job"]["state"] == "cancelled"
    assert calls == [{"op": "cancel", "id": "broker-job-1"}]


def test_semantic_request_rejects_invalid_base_generation():
    from library.tools.resolved.jobs import JobRefused, prepare

    with pytest.raises(JobRefused, match="positive base_generation"):
        prepare("timeline.semantic_edit", {
            "request": _request().to_dict(), "backend": "incremental",
            "base_generation": 0,
        })
