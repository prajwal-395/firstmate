"""The ML worker protocol: typed refusals, schema conformance, and the
serve loop.

The worker is the Python side of the typed protocol defined in
`native/ren-protocol/src/ml_worker.rs`. These tests pin the behaviors
the protocol promises: a bad request is a typed refusal (never a crash
or hang), a valid request conforms to the generated schema, and the
serve loop answers every message kind.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from library.tools import ml_worker, protocol_conformance

REPO_ROOT = Path(__file__).resolve().parents[3]
NATIVE_DIR = REPO_ROOT / "native"


def _serve(messages):
    """Run the serve loop over the given messages, return the responses."""
    stdin = io.StringIO("".join(json.dumps(m) + "\n" for m in messages))
    stdout = io.StringIO()
    ml_worker.serve(stdin, stdout)
    return [json.loads(line) for line in stdout.getvalue().splitlines()]


def _request(request_id="r1", capability_id="ml.embed", payload=None):
    return {
        "message": "request",
        "request_id": request_id,
        "capability_id": capability_id,
        "payload": payload if payload is not None else {"texts": ["hello"]},
    }


# ── Typed refusals ──────────────────────────────────────────────────


def test_unknown_capability_is_a_typed_refusal():
    responses = _serve([_request(capability_id="no.such_capability")])
    assert len(responses) == 1
    response = responses[0]
    assert response["message"] == "response"
    assert response["status"] == "refused"
    assert response["refusal"]["code"] == "ML_WORKER_UNKNOWN_CAPABILITY"
    assert "no.such_capability" in response["refusal"]["what"]


def test_payload_without_texts_is_a_typed_refusal():
    responses = _serve([_request(payload={"not_texts": []})])
    assert len(responses) == 1
    response = responses[0]
    assert response["status"] == "refused"
    assert response["refusal"]["code"] == "ML_WORKER_INVALID_PAYLOAD"


def test_texts_not_a_list_of_strings_is_a_typed_refusal():
    responses = _serve([_request(payload={"texts": [1, 2, 3]})])
    assert len(responses) == 1
    response = responses[0]
    assert response["status"] == "refused"
    assert response["refusal"]["code"] == "ML_WORKER_INVALID_PAYLOAD"


def test_unknown_message_type_is_a_typed_refusal():
    responses = _serve([{"message": "no_such_type", "request_id": "r1"}])
    assert len(responses) == 1
    response = responses[0]
    assert response["status"] == "refused"
    assert response["refusal"]["code"] == "ML_WORKER_UNKNOWN_CAPABILITY"


def test_cancel_of_unknown_request_is_a_typed_refusal():
    responses = _serve([{"message": "cancel", "request_id": "never-sent"}])
    assert len(responses) == 1
    response = responses[0]
    assert response["status"] == "refused"
    assert response["refusal"]["code"] == "ML_WORKER_CANCEL_UNKNOWN"


# ── Schema conformance ──────────────────────────────────────────────


def test_a_valid_request_conforms_to_the_generated_schema():
    protocol_conformance.validate(_request(), "ml-worker-request")


def test_a_request_missing_required_fields_is_refused_by_the_schema():
    bad = _request()
    del bad["request_id"]
    try:
        protocol_conformance.validate(bad, "ml-worker-request")
        assert False, "expected ProtocolConformanceError"
    except protocol_conformance.ProtocolConformanceError:
        pass


# ── Health ──────────────────────────────────────────────────────────


def test_health_returns_a_report():
    responses = _serve([{"message": "health", "request_id": "h1"}])
    assert len(responses) == 1
    report = responses[0]
    assert report["message"] == "health_report"
    assert report["request_id"] == "h1"
    assert report["ready"] is True
    assert report["model_loaded"] is False


# ── The serve loop does not crash on bad JSON ───────────────────────


def test_invalid_json_on_stdin_is_a_typed_error():
    stdin = io.StringIO("this is not json\n")
    stdout = io.StringIO()
    ml_worker.serve(stdin, stdout)
    responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert len(responses) == 1
    assert responses[0]["status"] == "error"
    assert responses[0]["error"]["code"] == "ML_WORKER_CAPABILITY_RAISED"


# ── Crash proof ──────────────────────────────────────────────────────


def test_a_crashing_worker_is_a_typed_error_not_a_hang(tmp_path):
    """A worker that exits before answering is a typed error, never a
    hang. The client must return ML_WORKER_CRASHED within a bounded
    time."""
    engine_root = tmp_path / "fake_engine"
    library_dir = engine_root / "library" / "tools"
    library_dir.mkdir(parents=True)
    (engine_root / "manage_project.py").touch()
    (library_dir / "__init__.py").touch()
    (library_dir / "ml_worker.py").write_text(
        "import sys\nsys.exit(1)\n", encoding="utf-8"
    )

    client = NATIVE_DIR / "target" / "debug" / "ml-worker-client"
    if not client.is_file():
        import pytest

        pytest.skip("ml-worker-client binary not built")

    env = {
        **os.environ,
        "REN_ENGINE_ROOT": str(engine_root),
        "PIPELINE_PYTHON": sys.executable,
        "PYTHONPATH": str(engine_root),
    }

    start = time.monotonic()
    result = subprocess.run(
        [str(client)],
        capture_output=True,
        encoding="utf-8",
        timeout=30,
        env=env,
        check=False,
    )
    elapsed = time.monotonic() - start

    assert result.returncode == 4
    assert "ML_WORKER_CRASHED" in result.stderr
    assert elapsed < 30
