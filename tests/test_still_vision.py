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

def test_request_images_are_absolute_files_on_disk_or_refused(tmp_path):
    """No images: no key. Images: absolute paths to files on disk, or a
    refusal - a relative or missing still strands the host."""
    req = llm_handshake.build_request(
        "creative_direction", "prompt", "", "ctx", "[]",
        "/proj", "2026-09-24T00:00:00+00:00")
    assert "images" not in req
    still = _still(tmp_path)
    req = llm_handshake.build_request(
        "objects__stills", "look", "", "ctx", "[]",
        str(tmp_path), "2026-09-24T00:00:00+00:00",
        images=[still])
    assert req["images"] == [still]
    for images, match in (
        (["relative/still.png"], "not an absolute path"),
        ([str(tmp_path / "never_extracted.png")], "not a file on disk"),
        ([], "non-empty list"),
    ):
        with pytest.raises(ValueError, match=match):
            llm_handshake.build_request(
                "s", "p", "", "c", "[]", str(tmp_path), "t", images=images)


# ── The response validates exactly as today, plus the text shape ──

def test_a_host_answer_is_an_object_with_usable_text(tmp_path):
    """The response validates exactly as before (objects, never arrays),
    and a still answer must carry a non-blank `text`."""
    assert llm_handshake.validate_response(
        "s", '{"text": "x"}', "/proj") == {"text": "x"}
    with pytest.raises(llm_handshake.HandshakeRefusal):
        llm_handshake.validate_response("s", '["x"]', "/proj")
    assert llm_handshake.require_text_answer(
        {"text": "  the subject is cut off  "}, "s",
        str(tmp_path)) == "  the subject is cut off  "
    with pytest.raises(llm_handshake.HandshakeRefusal,
                       match="no usable `text`"):
        llm_handshake.require_text_answer(
            {"verdict": "pass"}, "objects__stills", str(tmp_path))
    with pytest.raises(llm_handshake.HandshakeRefusal):
        llm_handshake.require_text_answer(
            {"text": "   "}, "objects__stills", str(tmp_path))


# ── The host capability is declared, never guessed ──

def test_the_host_and_its_vision_are_declared_never_guessed(monkeypatch):
    assert host_sees_images("agent") is True
    assert host_sees_images("mock") is True
    for undeclared in ("api", "codex"):
        with pytest.raises(UnknownHost, match="not in HOST_SEES_IMAGES"):
            host_sees_images(undeclared)
    # Explicit beats the environment; an empty environment is no host.
    monkeypatch.setenv(still_vision.HARNESS_ENV_VAR, "agent")
    assert resolve_harness("mock") == "mock"
    assert resolve_harness() == "agent"
    monkeypatch.setenv(still_vision.HARNESS_ENV_VAR, "  ")
    assert resolve_harness() is None
    monkeypatch.delenv(still_vision.HARNESS_ENV_VAR, raising=False)
    assert resolve_harness() is None
    # The still request never shares the step's own handshake stem.
    assert request_step_id("select_broll") == "select_broll__stills"


# ── No host: gemma, single vs multi, same calls as before ──

def test_no_host_answers_via_gemma_single_or_multi(tmp_path, monkeypatch):
    """Same calls as before: one still takes the single path, several
    the multi path, and an empty list refuses before any model."""
    from library.tools import vision_model

    seen = {}

    def fake_single(self, image_path, prompt, max_tokens=600):
        seen["single"] = (image_path, prompt)
        return "single answer"

    def fake_multi(self, image_paths, prompt, max_tokens=800):
        seen["multi"] = list(image_paths)
        return "multi answer"

    monkeypatch.setattr(vision_model.VisionModel, "analyze_image",
                        fake_single)
    monkeypatch.setattr(vision_model.VisionModel, "analyze_images",
                        fake_multi)
    monkeypatch.delenv(still_vision.HARNESS_ENV_VAR, raising=False)
    still = _still(tmp_path)
    assert inspect_stills("Is it sharp?", [still]) == "single answer"
    assert seen["single"][0] == still
    assert "multi" not in seen, "single still must not take the multi path"
    stills = [_still(tmp_path, f"s{i:02d}.png") for i in range(2)]
    assert inspect_stills("Compare.", stills) == "multi answer"
    assert seen["multi"] == stills
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


def test_the_host_never_sees_a_half_written_request(tmp_path, monkeypatch):
    """The host polls the request file; under load it read one mid-write
    and died on the parse, so the request went unanswered for 60 s."""
    project = tmp_path / "proj"
    project.mkdir()
    req = project / "pipeline_output" / "llm_requests" / \
        "ask_the_footage__stills.json"
    seen_mid_write = []
    real_dump = json.dump

    def dump(obj, handle, **kwargs):
        seen_mid_write.append(req.exists())
        real_dump(obj, handle, **kwargs)

    monkeypatch.setattr(json, "dump", dump)
    with pytest.raises(StillVisionTimeout):
        request_host_answer("Look.", [_still(tmp_path)], str(project),
                            "ask_the_footage", timeout_seconds=0)
    assert seen_mid_write == [False]
    assert json.loads(req.read_text(encoding="utf-8"))["images"]


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


# ── Finding 1 (rung 0): the ready marker must never ride bridge stdout ──

def test_still_request_marker_leaves_bridge_stdout_pure_json(
        tmp_path, capsys):
    """Finding 1: every agent-mode run failed at colour grading because
    `request_host_answer` printed LLM_REQUEST_READY on stdout inside step
    5.01's bridge, whose stdout must be pure JSON
    (`PreBridgeError: bridge.py produced invalid JSON` - even when the
    stills request was answered in time). The marker is routed so bridge
    stdout stays JSON: the runner streams bridge stderr as the step's
    log, which is the channel the driver watches.
    """
    still = _still(tmp_path)
    project = tmp_path / "proj"
    project.mkdir()
    holder = {}
    thread = threading.Thread(
        target=lambda: holder.setdefault(
            "request", _answer_as_host(
                str(project), "step_5_01_color_grade__stills",
                "neutral, judged on the wall.")),
        daemon=True)
    thread.start()
    try:
        text = request_host_answer(
            "Judge the white balance.", [still], str(project),
            "step_5_01_color_grade", label="colour stills",
            timeout_seconds=60)
        # The bridge's own result, printed after the stills answer lands.
        print(json.dumps({"still_colour_notes": {"text": text}}))
    finally:
        thread.join(timeout=60)
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "still_colour_notes": {"text": "neutral, judged on the wall."}}
    assert llm_handshake.READY_MARKER in captured.err


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
