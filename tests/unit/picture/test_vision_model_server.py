"""Server-first Gemma path in library/tools/vision_model.py.

The pipeline talks HTTP to the resident mlx_vlm server when it is up and
falls back to the in-process model when it is not. The fallback must be
LOUD (stderr), never silent; HTTP 4xx (our payload is malformed) must
propagate rather than mask a client bug behind a slow in-process run.
"""

import io
import json
import types
import urllib.error

import pytest

from library.tools import vision_model


def _stub_in_process(monkeypatch, answer):
    """Stub the in-process path so no model is loaded."""
    monkeypatch.setattr(vision_model.VisionModel, "_ensure_loaded", lambda self: None)
    monkeypatch.setattr(vision_model, "load", lambda *a: (object(), object()))
    monkeypatch.setattr(
        vision_model, "apply_chat_template", lambda *a, **k: "formatted"
    )

    class _R:
        text = answer

    monkeypatch.setattr(vision_model, "generate", lambda *a, **k: _R())
    inst = vision_model.VisionModel()
    inst._model = types.SimpleNamespace(config=None)
    inst._proc = object()
    return inst


MODEL = "mlx-community/gemma-4-12b-it-4bit"


class _FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _chat_payload(text):
    return {"choices": [{"message": {"content": text}}]}


def test_server_answers_without_model_load(monkeypatch, capsys):
    """Server up: analyze_image returns server text, in-process never loads."""
    monkeypatch.setattr(vision_model, "SERVER_URL", "http://127.0.0.1:8080")
    monkeypatch.setattr(
        vision_model.urllib.request,
        "urlopen",
        lambda req, timeout=None: _FakeResponse(_chat_payload("SERVER_SAYS_RED")),
    )
    loaded = []
    monkeypatch.setattr(
        vision_model.VisionModel, "_ensure_loaded", lambda self: loaded.append(True)
    )
    out = vision_model.VisionModel().analyze_image("/tmp/still.png", "What color?")
    assert out == "SERVER_SAYS_RED"
    assert loaded == []
    assert "GEMMA SERVER answered analyze_image" in capsys.readouterr().err


def test_unreachable_server_falls_back_loudly(monkeypatch, capsys):
    """Server down: loud stderr line, then the in-process path answers."""
    monkeypatch.setattr(vision_model, "SERVER_URL", "http://127.0.0.1:9")

    def _boom(req, timeout=None):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(vision_model.urllib.request, "urlopen", _boom)
    inst = _stub_in_process(monkeypatch, "IN_PROCESS_ANSWER")
    out = inst.analyze_image("/tmp/still.png", "What color?")
    assert out == "IN_PROCESS_ANSWER"
    err = capsys.readouterr().err
    assert "GEMMA SERVER unreachable" in err
    assert "falling back to in-process" in err


def test_client_error_propagates_instead_of_fallback(monkeypatch):
    """HTTP 400 is our bug: raise, do not hide it behind a fallback run."""
    monkeypatch.setattr(vision_model, "SERVER_URL", "http://127.0.0.1:8080")

    def _bad(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 400, "Bad Request", {}, io.BytesIO(b"{}")
        )

    monkeypatch.setattr(vision_model.urllib.request, "urlopen", _bad)
    with pytest.raises(urllib.error.HTTPError):
        vision_model.VisionModel().analyze_images(
            ["/tmp/a.png", "/tmp/b.png"], "Compare."
        )


def test_server_path_disabled_by_empty_url(monkeypatch, capsys):
    """GEMMA_SERVER_URL="" goes straight in-process with no unreachable line."""
    monkeypatch.setattr(vision_model, "SERVER_URL", "")
    calls = []
    monkeypatch.setattr(
        vision_model.urllib.request,
        "urlopen",
        lambda req, timeout=None: calls.append(req) or _FakeResponse(_chat_payload("X")),
    )
    inst = _stub_in_process(monkeypatch, "DIRECT")
    out = inst.analyze_video("/tmp/clip.mp4", "Describe.")
    assert out == "DIRECT"
    assert calls == []
    err = capsys.readouterr().err
    assert "unreachable" not in err
    assert "GEMMA SERVER path disabled" in err
