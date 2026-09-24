"""Still-frame inspection goes to the driver first, gemma as fallback.

Pins the captain's 2026-09-24 ruling: a driving host with vision
answers still-frame inspection through the file handshake (the stills
as the request's `images` list); with no host - or a blind one -
gemma4 answers exactly as before. Whole-video passes never route.

Each test names what it pins: a request that strands the host with a
relative or missing still path, a host answer accepted without the
`text` shape, a harness whose vision was guessed instead of declared,
a timeout silently becoming a gemma answer, and a video pass that
starts filing handshakes.
"""

import json
import threading
import time

import pytest

from library.tools import llm_handshake, still_vision
from library.tools.still_vision import (
    StillVisionTimeout,
    UnknownHost,
    host_sees_images,
    inspect_stills,
    request_host_answer,
    request_step_id,
    resolve_harness,
)


def _still(tmp_path, name="still_00.png"):
    path = tmp_path / name
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    return str(path)


# ── The request schema: `images` optional, absolute, on disk ──

def test_request_without_images_has_no_images_key():
    req = llm_handshake.build_request(
        "creative_direction", "prompt", "", "ctx", "[]",
        "/proj", "2026-09-24T00:00:00+00:00")
    assert "images" not in req


def test_request_with_images_carries_absolute_paths(tmp_path):
    still = _still(tmp_path)
    req = llm_handshake.build_request(
        "objects__stills", "look", "", "ctx", "[]",
        str(tmp_path), "2026-09-24T00:00:00+00:00",
        images=[still])
    assert req["images"] == [still]


def test_request_with_relative_image_refuses(tmp_path):
    with pytest.raises(ValueError, match="not an absolute path"):
        llm_handshake.build_request(
            "s", "p", "", "c", "[]", str(tmp_path), "t",
            images=["relative/still.png"])


def test_request_with_missing_image_refuses(tmp_path):
    with pytest.raises(ValueError, match="not a file on disk"):
        llm_handshake.build_request(
            "s", "p", "", "c", "[]", str(tmp_path), "t",
            images=[str(tmp_path / "never_extracted.png")])


def test_request_with_empty_images_refuses(tmp_path):
    with pytest.raises(ValueError, match="non-empty list"):
        llm_handshake.build_request(
            "s", "p", "", "c", "[]", str(tmp_path), "t", images=[])


# ── The response validates exactly as today, plus the text shape ──

def test_validate_response_still_accepts_objects():
    assert llm_handshake.validate_response(
        "s", '{"text": "x"}', "/proj") == {"text": "x"}


def test_validate_response_still_refuses_arrays():
    with pytest.raises(llm_handshake.HandshakeRefusal):
        llm_handshake.validate_response("s", '["x"]', "/proj")


def test_require_text_answer_returns_text(tmp_path):
    assert llm_handshake.require_text_answer(
        {"text": "  the subject is cut off  "}, "s",
        str(tmp_path)) == "  the subject is cut off  "


def test_require_text_answer_without_text_refuses(tmp_path):
    with pytest.raises(llm_handshake.HandshakeRefusal,
                       match="no usable `text`"):
        llm_handshake.require_text_answer(
            {"verdict": "pass"}, "objects__stills", str(tmp_path))


def test_require_text_answer_with_blank_text_refuses(tmp_path):
    with pytest.raises(llm_handshake.HandshakeRefusal):
        llm_handshake.require_text_answer(
            {"text": "   "}, "objects__stills", str(tmp_path))


# ── The host capability is declared, never guessed ──

def test_declared_hosts_see_images():
    assert host_sees_images("agent") is True
    assert host_sees_images("mock") is True


def test_undeclared_harness_raises_not_guesses():
    with pytest.raises(UnknownHost, match="not in HOST_SEES_IMAGES"):
        host_sees_images("api")
    with pytest.raises(UnknownHost, match="not in HOST_SEES_IMAGES"):
        host_sees_images("codex")


def test_resolve_harness_prefers_explicit_over_env(monkeypatch):
    monkeypatch.setenv(still_vision.HARNESS_ENV_VAR, "agent")
    assert resolve_harness("mock") == "mock"
    assert resolve_harness() == "agent"


def test_resolve_harness_empty_env_means_no_host(monkeypatch):
    monkeypatch.delenv(still_vision.HARNESS_ENV_VAR, raising=False)
    assert resolve_harness() is None
    monkeypatch.setenv(still_vision.HARNESS_ENV_VAR, "  ")
    assert resolve_harness() is None


def test_request_step_id_never_shares_the_step_stem():
    assert request_step_id("select_broll") == "select_broll__stills"


# ── No host: gemma, single vs multi, same calls as before ──

def test_no_host_answers_single_still_via_gemma(tmp_path, monkeypatch):
    from library.tools import vision_model

    seen = {}

    def fake_single(self, image_path, prompt, max_tokens=600):
        seen["single"] = (image_path, prompt)
        return "single answer"

    def fake_multi(self, image_paths, prompt, max_tokens=800):
        raise AssertionError("single still must not take the multi path")

    monkeypatch.setattr(vision_model.VisionModel, "analyze_image",
                        fake_single)
    monkeypatch.setattr(vision_model.VisionModel, "analyze_images",
                        fake_multi)
    monkeypatch.delenv(still_vision.HARNESS_ENV_VAR, raising=False)
    still = _still(tmp_path)
    assert inspect_stills("Is it sharp?", [still]) == "single answer"
    assert seen["single"][0] == still


def test_no_host_answers_multi_still_via_gemma(tmp_path, monkeypatch):
    from library.tools import vision_model

    def fake_multi(self, image_paths, prompt, max_tokens=800):
        assert len(image_paths) == 2
        return "multi answer"

    monkeypatch.setattr(vision_model.VisionModel, "analyze_images",
                        fake_multi)
    monkeypatch.delenv(still_vision.HARNESS_ENV_VAR, raising=False)
    stills = [_still(tmp_path, f"s{i:02d}.png") for i in range(2)]
    assert inspect_stills("Compare.", stills) == "multi answer"


def test_empty_still_list_refuses_before_any_model(tmp_path):
    with pytest.raises(ValueError, match="no stills to inspect"):
        inspect_stills("Look.", [])


# ── A driving host: the handshake, answered end to end ──

def _answer_as_host(project_folder, request_id, text):
    """Block until the request lands, then answer it as the host."""
    from pathlib import Path

    req = Path(project_folder) / "pipeline_output" / "llm_requests" / \
        f"{request_id}.json"
    res = Path(project_folder) / "pipeline_output" / "llm_responses" / \
        f"{request_id}.json"
    deadline = time.time() + 60
    while time.time() < deadline:
        if req.exists():
            request = json.loads(req.read_text(encoding="utf-8"))
            assert request["images"], "host must be handed the stills"
            for entry in request["images"]:
                assert Path(entry).is_absolute()
                assert Path(entry).is_file()
            res.parent.mkdir(parents=True, exist_ok=True)
            res.write_text(json.dumps({"text": text}), encoding="utf-8")
            return request
        time.sleep(0.05)
    raise AssertionError("still-vision request never filed")


def test_host_with_vision_answers_through_the_handshake(tmp_path):
    still = _still(tmp_path)
    project = tmp_path / "proj"
    project.mkdir()
    holder = {}
    thread = threading.Thread(
        target=lambda: holder.setdefault(
            "request", _answer_as_host(
                str(project), "ask_the_footage__stills",
                "The caption is readable.")),
        daemon=True)
    thread.start()
    try:
        out = inspect_stills(
            "Is the caption readable?", [still], harness="agent",
            project_folder=str(project), step_id="ask_the_footage",
            timeout_seconds=60)
    finally:
        thread.join(timeout=60)
    assert out == "The caption is readable."
    assert holder["request"]["images"] == [still]


def test_host_timeout_never_becomes_a_gemma_answer(tmp_path, monkeypatch):
    from library.tools import vision_model

    def fake_multi(self, image_paths, prompt, max_tokens=800):
        raise AssertionError("a timeout must raise, not fall back")

    monkeypatch.setattr(vision_model.VisionModel, "analyze_images",
                        fake_multi)
    project = tmp_path / "proj"
    project.mkdir()
    with pytest.raises(StillVisionTimeout, match="unanswered"):
        request_host_answer(
            "Look.", [_still(tmp_path)], str(project), "ask_the_footage",
            timeout_seconds=0)


def test_host_without_project_folder_refuses_loudly(tmp_path):
    with pytest.raises(ValueError, match="no project_folder"):
        inspect_stills("Look.", [_still(tmp_path)], harness="agent",
                       project_folder="", step_id="s",
                       timeout_seconds=0)


# ── The analyzer: stills route, video never files a handshake ──

def test_analyzer_stills_route_through_host_without_a_model(tmp_path):
    from library.tools.analysis import vision_pipeline_v3 as vp

    still = _still(tmp_path)
    project = tmp_path / "proj"
    project.mkdir()
    analyzer = vp.VisionAnalyzer(
        None, None, harness="agent", project_folder=str(project),
        step_id="semantic_analysis")
    holder = {}
    thread = threading.Thread(
        target=lambda: holder.setdefault(
            "request", _answer_as_host(
                str(project), "semantic_analysis__stills",
                '[{"label": "microphone"}]')),
        daemon=True)
    thread.start()
    try:
        text, _ = analyzer.analyze(
            "List objects.", images=[still], max_tokens=64)
    finally:
        thread.join(timeout=60)
    assert text == '[{"label": "microphone"}]'


def test_analyzer_gemma_path_files_no_handshake(tmp_path, monkeypatch):
    """No host: the analyzer delegates to the gemma fallback and files
    nothing - pinned without loading weights."""
    from library.tools.analysis import vision_pipeline_v3 as vp

    monkeypatch.delenv(still_vision.HARNESS_ENV_VAR, raising=False)
    monkeypatch.setattr(
        still_vision, "answer_via_gemma",
        lambda prompt, images, max_tokens=800: "gemma text")

    project = tmp_path / "proj"
    project.mkdir()
    analyzer = vp.VisionAnalyzer(None, None, harness=None,
                                 project_folder=str(project))
    text, _ = analyzer.analyze(
        "List objects.", images=[_still(tmp_path)], max_tokens=64)
    assert text == "gemma text"
    assert not (project / "pipeline_output").exists()
