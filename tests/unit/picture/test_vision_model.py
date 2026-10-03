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
import sys
from unittest.mock import MagicMock, patch
from library.tools import video_segment_analyzer
from library.tools.analysis.ocr_extractor import (
    TextDetection, Track
)


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
    route = {}
    out = vision_model.VisionModel().analyze_image(
        "/tmp/still.png", "What color?", _route_metadata=route)
    assert out == "SERVER_SAYS_RED"
    assert loaded == []
    assert route["backend"] == "gemma_server"
    assert route["model"] == vision_model.SERVER_MODEL
    assert route["fallback_causes"] == []
    assert "GEMMA SERVER answered analyze_image" in capsys.readouterr().err


def test_unreachable_server_falls_back_loudly(monkeypatch, capsys):
    """Server down: loud stderr line, then the in-process path answers."""
    monkeypatch.setattr(vision_model, "SERVER_URL", "http://127.0.0.1:9")

    def _boom(req, timeout=None):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(vision_model.urllib.request, "urlopen", _boom)
    inst = _stub_in_process(monkeypatch, "IN_PROCESS_ANSWER")
    route = {}
    out = inst.analyze_image("/tmp/still.png", "What color?",
                             _route_metadata=route)
    assert out == "IN_PROCESS_ANSWER"
    assert route["backend"] == "mlx_vlm"
    assert route["fallback_causes"][0]["stage"] == "gemma_server"
    assert "Connection refused" in route["fallback_causes"][0]["cause"]
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


# --------------------------------------------------------------------------
# From test_video_segment_analyzer.py
#
# `video_segment_analyzer analyze --cleanup` deletes the analysed video,
# and without the flag the footage is left exactly where it was.

def _run(video, *flags):
    argv = ["video_segment_analyzer.py", "analyze", "--video", str(video),
            *flags]
    with patch("library.tools.video_segment_analyzer.run_analysis",
               return_value={"status": "ok"}), \
            patch.object(sys, "argv", argv), \
            patch("sys.stdout", new=MagicMock()):
        try:
            video_segment_analyzer.main()
        except SystemExit:
            pass


def test_only_cleanup_deletes_the_video(tmp_path):
    video = tmp_path / "dummy.mp4"
    video.write_text("dummy content")
    _run(video)
    assert video.exists(), "without --cleanup the footage must survive"
    _run(video, "--cleanup")
    assert not video.exists()


# --------------------------------------------------------------------------
# From test_ocr_extractor.py

@pytest.fixture
def mock_easyocr():
    with patch('library.tools.analysis.ocr_extractor.easyocr.Reader') as mock_reader:
        instance = mock_reader.return_value
        yield instance

def test_track_merging():
    det1 = TextDetection("EXIT", 0.9, (10, 10, 50, 20), (0.1, 0.1, 0.5, 0.2))
    track = Track(det1, 1.0)
    
    # Add detection within 2 seconds
    det2 = TextDetection("EXIT", 0.95, (12, 11, 48, 19), (0.12, 0.11, 0.48, 0.19))
    track.add(det2, 2.5)
    
    result = track.to_tracked_text()
    assert result.text == "EXIT"
    assert len(result.appearances) == 1
    assert result.appearances[0] == (1.0, 2.5)
    assert result.is_static == True

    # Add detection after 3 seconds (gap > 2.0)
    det3 = TextDetection("EXIT", 0.8, (10, 10, 50, 20), (0.1, 0.1, 0.5, 0.2))
    track.add(det3, 6.0)
    
    result2 = track.to_tracked_text()
    assert len(result2.appearances) == 2
    assert result2.appearances[0] == (1.0, 2.5)
    assert result2.appearances[1] == (6.0, 6.0)
    
