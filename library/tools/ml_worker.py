"""The ML worker: a stdio JSON-lines server for one ML capability.

The Python side of the typed ML worker protocol defined in
`native/ren-protocol/src/ml_worker.rs`. A Rust client spawns this
process, sends one typed request over stdin, and reads one typed
response over stdout. The wire format is newline-delimited JSON; every
message carries a `message` tag.

The capability served here is the simplest existing ML call in the
pipeline: the sentence-transformer embedding used by footage search
(`library/tools/analysis/footage_query.py:load_embedder`). Input is a
list of strings; output is a list of normalized vectors. No images, no
Resolve, no project state.

The worker validates every request against the generated JSON Schema
(`ml-worker-request.schema.json`) through `protocol_conformance`, so a
malformed request is a typed refusal, not a crash. A request for an
unknown capability is a typed refusal. A capability that raises is a
typed error. The worker process itself does not crash on a bad request;
it answers and keeps serving.

Usage:
    python3 -m library.tools.ml_worker
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

from library.tools import protocol_conformance
from library.tools.analysis.footage_query import EMBED_MODEL, load_embedder

CAPABILITY_ID = "ml.embed"

_REFUSAL_UNKNOWN_CAPABILITY = "ML_WORKER_UNKNOWN_CAPABILITY"
_REFUSAL_INVALID_PAYLOAD = "ML_WORKER_INVALID_PAYLOAD"
_REFUSAL_CANCEL_UNKNOWN = "ML_WORKER_CANCEL_UNKNOWN"
_ERROR_CAPABILITY_RAISED = "ML_WORKER_CAPABILITY_RAISED"


def _refusal(code: str, what: str, why: str, fix: str) -> dict:
    return {"code": code, "what": what, "why": why, "fix": fix}


def _error(code: str, what: str, detail: str) -> dict:
    return {"code": code, "what": what, "detail": detail}


class _Embedder:
    """Lazy wrapper around the shared footage-query embedder."""

    def __init__(self) -> None:
        self._encode = None
        self._backend: str | None = None

    def load(self) -> None:
        if self._encode is not None:
            return
        encode, backend = load_embedder()
        if encode is None:
            raise RuntimeError(backend)
        self._encode = encode
        self._backend = backend

    @property
    def model(self) -> str:
        return EMBED_MODEL

    @property
    def loaded(self) -> bool:
        return self._encode is not None

    def encode(self, texts: list) -> Any:
        self.load()
        return self._encode(texts)


def _handle_request(request: dict, embedder: _Embedder, state: dict) -> list:
    """Handle one request message. Returns a list of messages to emit."""
    request_id = request["request_id"]
    capability_id = request["capability_id"]
    payload = request["payload"]

    if capability_id != CAPABILITY_ID:
        return [
            {
                "message": "response",
                "request_id": request_id,
                "status": "refused",
                "refusal": _refusal(
                    _REFUSAL_UNKNOWN_CAPABILITY,
                    f"unknown capability '{capability_id}'",
                    f"this worker serves only '{CAPABILITY_ID}'",
                    f"send capability_id='{CAPABILITY_ID}'",
                ),
            }
        ]

    if not isinstance(payload, dict) or "texts" not in payload:
        return [
            {
                "message": "response",
                "request_id": request_id,
                "status": "refused",
                "refusal": _refusal(
                    _REFUSAL_INVALID_PAYLOAD,
                    "payload has no 'texts'",
                    f"the '{CAPABILITY_ID}' capability requires a 'texts' list",
                    'send {"texts": ["...", "..."]}',
                ),
            }
        ]

    texts = payload["texts"]
    if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
        return [
            {
                "message": "response",
                "request_id": request_id,
                "status": "refused",
                "refusal": _refusal(
                    _REFUSAL_INVALID_PAYLOAD,
                    "'texts' is not a list of strings",
                    "every element must be a string",
                    'send {"texts": ["...", "..."]}',
                ),
            }
        ]

    messages = []

    messages.append(
        {
            "message": "progress",
            "request_id": request_id,
            "phase": "load_model",
            "fraction": 0.1,
            "message": "loading embedding model",
        }
    )

    try:
        embedder.load()
    except Exception as exc:
        return [
            {
                "message": "response",
                "request_id": request_id,
                "status": "error",
                "error": _error(
                    _ERROR_CAPABILITY_RAISED,
                    "model load failed",
                    f"{type(exc).__name__}: {exc}",
                ),
            }
        ]

    messages.append(
        {
            "message": "progress",
            "request_id": request_id,
            "phase": "encode",
            "fraction": 0.5,
            "message": f"encoding {len(texts)} texts",
        }
    )

    try:
        matrix = embedder.encode(texts)
    except Exception as exc:
        return [
            {
                "message": "response",
                "request_id": request_id,
                "status": "error",
                "error": _error(
                    _ERROR_CAPABILITY_RAISED,
                    "encoding failed",
                    f"{type(exc).__name__}: {exc}",
                ),
            }
        ]

    vectors = matrix.tolist()
    messages.append(
        {
            "message": "progress",
            "request_id": request_id,
            "phase": "done",
            "fraction": 1.0,
            "message": f"encoded {len(texts)} texts",
        }
    )
    messages.append(
        {
            "message": "response",
            "request_id": request_id,
            "status": "ok",
            "payload": {
                "vectors": vectors,
                "dimension": len(vectors[0]) if vectors else 0,
                "model": embedder.model,
                "backend": embedder._backend,
            },
        }
    )
    return messages


def _handle_cancel(cancel: dict, state: dict) -> list:
    request_id = cancel["request_id"]
    if request_id not in state["in_flight"]:
        return [
            {
                "message": "response",
                "request_id": request_id,
                "status": "refused",
                "refusal": _refusal(
                    _REFUSAL_CANCEL_UNKNOWN,
                    f"no in-flight request '{request_id}'",
                    "the request is unknown or already finished",
                    "send a new request, or check the request_id",
                ),
            }
        ]
    state["cancelled"].add(request_id)
    return [
        {
            "message": "response",
            "request_id": request_id,
            "status": "ok",
            "payload": {"cancelled": True},
        }
    ]


def _handle_health(health: dict, embedder: _Embedder, state: dict) -> list:
    return [
        {
            "message": "health_report",
            "request_id": health["request_id"],
            "ready": True,
            "model": embedder.model,
            "model_loaded": embedder.loaded,
            "uptime_seconds": time.time() - state["started_at"],
        }
    ]


def serve(stdin=None, stdout=None) -> int:
    """Run the stdio server. Returns the process exit code."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    embedder = _Embedder()
    state = {
        "started_at": time.time(),
        "in_flight": set(),
        "cancelled": set(),
    }

    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            response = {
                "message": "response",
                "request_id": "",
                "status": "error",
                "error": _error(
                    _ERROR_CAPABILITY_RAISED,
                    "invalid JSON on stdin",
                    str(exc),
                ),
            }
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()
            continue

        msg_type = message.get("message")
        if msg_type == "request":
            try:
                protocol_conformance.validate(message, "ml-worker-request")
            except protocol_conformance.ProtocolConformanceError as exc:
                response = {
                    "message": "response",
                    "request_id": message.get("request_id", ""),
                    "status": "refused",
                    "refusal": _refusal(
                        _REFUSAL_INVALID_PAYLOAD,
                        "request does not conform to ml-worker-request schema",
                        str(exc),
                        "fix the request against the generated schema",
                    ),
                }
                stdout.write(json.dumps(response) + "\n")
                stdout.flush()
                continue

            request_id = message["request_id"]
            state["in_flight"].add(request_id)
            try:
                responses = _handle_request(message, embedder, state)
            except Exception as exc:
                responses = [
                    {
                        "message": "response",
                        "request_id": request_id,
                        "status": "error",
                        "error": _error(
                            _ERROR_CAPABILITY_RAISED,
                            "worker raised",
                            f"{type(exc).__name__}: {exc}",
                        ),
                    }
                ]
            state["in_flight"].discard(request_id)
            for response in responses:
                stdout.write(json.dumps(response) + "\n")
            stdout.flush()

        elif msg_type == "cancel":
            responses = _handle_cancel(message, state)
            for response in responses:
                stdout.write(json.dumps(response) + "\n")
            stdout.flush()

        elif msg_type == "health":
            responses = _handle_health(message, embedder, state)
            for response in responses:
                stdout.write(json.dumps(response) + "\n")
            stdout.flush()

        else:
            response = {
                "message": "response",
                "request_id": message.get("request_id", ""),
                "status": "refused",
                "refusal": _refusal(
                    _REFUSAL_UNKNOWN_CAPABILITY,
                    f"unknown message type '{msg_type}'",
                    "the worker speaks request, cancel and health",
                    "send a message with message='request', 'cancel' or 'health'",
                ),
            }
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()

    return 0


def main() -> int:
    return serve()


if __name__ == "__main__":
    sys.exit(main())
