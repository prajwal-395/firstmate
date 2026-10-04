"""Still-frame requests use isolated, bounded Codex calls or Gemma fallback."""
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from library.tools import still_vision
from library.tools.still_vision import (
    StillVisionCallError,
    UnknownHost,
    host_sees_images,
    inspect_stills,
    max_concurrent_calls,
    request_direct_answer,
    resolve_harness,
)


def _still(tmp_path, name="still_00.png"):
    path = tmp_path / name
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    return str(path)


def _fake_codex(monkeypatch, tmp_path):
    """Install a small executable that records its own prompt and images."""
    executable = tmp_path / "codex"
    executable.write_text("""#!/usr/bin/env python3
import json
import os
import sys
import time
from pathlib import Path
args = sys.argv[1:]
answer = Path(args[args.index('--output-last-message') + 1])
images = [args[i + 1] for i, arg in enumerate(args[:-1]) if arg == '--image']
prompt = sys.stdin.read()
time.sleep(0.04 if prompt.startswith('slow') else 0.005)
record = {'prompt': prompt, 'images': images, 'answer_path': str(answer)}
with open(os.environ['FAKE_CODEX_RECORD'], 'a', encoding='utf-8') as fh:
    fh.write(json.dumps(record) + '\\n')
answer.write_text(json.dumps(record), encoding='utf-8')
print(json.dumps({'type': 'turn.completed', 'model': 'codex-test',
                  'usage': {'input_tokens': 12, 'output_tokens': 3}}))
""", encoding="utf-8")
    executable.chmod(0o755)
    record_path = tmp_path / "codex-record.jsonl"
    monkeypatch.setenv("FAKE_CODEX_RECORD", str(record_path))
    monkeypatch.setattr(
        still_vision.shutil, "which",
        lambda name: str(executable) if name == "codex" else None)
    return str(executable), record_path


# ── Host and route declarations ──


def test_host_vision_is_declared_and_direct_cli_is_selected(monkeypatch):
    assert host_sees_images("agent") is True
    assert host_sees_images("mock") is True
    for undeclared in ("api", "codex"):
        with pytest.raises(UnknownHost, match="not in HOST_SEES_IMAGES"):
            host_sees_images(undeclared)
    monkeypatch.setenv(still_vision.HARNESS_ENV_VAR, "agent")
    assert resolve_harness("mock") == "mock"
    assert resolve_harness() == "agent"
    monkeypatch.setenv(still_vision.HARNESS_ENV_VAR, "  ")
    assert resolve_harness() is None
    monkeypatch.delenv(still_vision.HARNESS_ENV_VAR, raising=False)
    assert resolve_harness() is None


def test_still_concurrency_configuration_is_bounded(monkeypatch):
    monkeypatch.delenv(still_vision.MAX_CONCURRENCY_ENV_VAR, raising=False)
    assert max_concurrent_calls() == 10
    monkeypatch.setenv(still_vision.MAX_CONCURRENCY_ENV_VAR, "3")
    assert max_concurrent_calls() == 3
    for invalid in ("0", "33", "many"):
        monkeypatch.setenv(still_vision.MAX_CONCURRENCY_ENV_VAR, invalid)
        with pytest.raises(ValueError, match=still_vision.MAX_CONCURRENCY_ENV_VAR):
            max_concurrent_calls()


# ── Gemma remains the fallback only when no direct CLI is available ──


def test_no_host_answers_via_gemma_single_or_multi(tmp_path, monkeypatch):
    from library.tools import vision_model

    seen = {}

    def fake_single(self, image_path, prompt, max_tokens=600,
                    _route_metadata=None):
        seen["single"] = (image_path, prompt)
        if _route_metadata is not None:
            _route_metadata.update(backend="gemma_server", model="gemma-test",
                                   model_version="gemma-test")
        return "single answer"

    def fake_multi(self, image_paths, prompt, max_tokens=800,
                   _route_metadata=None):
        seen["multi"] = list(image_paths)
        return "multi answer"

    monkeypatch.setattr(vision_model.VisionModel, "analyze_image", fake_single)
    monkeypatch.setattr(vision_model.VisionModel, "analyze_images", fake_multi)
    monkeypatch.delenv(still_vision.HARNESS_ENV_VAR, raising=False)
    still = _still(tmp_path)
    route = {}
    assert inspect_stills("Is it sharp?", [still],
                          route_metadata=route) == "single answer"
    assert seen["single"][0] == still
    assert route["backend"] == "gemma_server"
    assert route["fallback_causes"] == [
        {"stage": "still_route", "cause": "no_host_configured"}]
    assert "multi" not in seen
    stills = [_still(tmp_path, f"s{i:02d}.png") for i in range(2)]
    assert inspect_stills("Compare.", stills) == "multi answer"
    assert seen["multi"] == stills
    with pytest.raises(ValueError, match="no stills to inspect"):
        inspect_stills("Look.", [])


def test_image_capable_host_without_codex_uses_gemma(tmp_path, monkeypatch):
    from library.tools import vision_model

    monkeypatch.setattr(still_vision, "direct_cli_for_harness",
                        lambda _harness: None)
    monkeypatch.setattr(vision_model.VisionModel, "analyze_image",
                        lambda self, *_args, **_kwargs: "gemma answer")
    route = {}
    assert inspect_stills("Look.", [_still(tmp_path)], harness="agent",
                          route_metadata=route) == "gemma answer"
    assert route["fallback_causes"] == [
        {"stage": "still_route", "cause": "direct_cli_unavailable"}]


# ── Every direct call owns its exact prompt, image list, and response ──


def test_parallel_calls_keep_prompts_images_and_answers_paired(tmp_path,
                                                               monkeypatch):
    _executable, record_path = _fake_codex(monkeypatch, tmp_path)
    first = _still(tmp_path, "first.png")
    second = _still(tmp_path, "second.png")
    routes = [{}, {}]

    def call(prompt, image, index):
        return inspect_stills(
            prompt, [image], harness="agent", step_id=f"clip-{index}",
            label=f"request-{index}", max_tokens=64,
            route_metadata=routes[index])

    with ThreadPoolExecutor(max_workers=2) as pool:
        slow = pool.submit(call, "slow first prompt", first, 0)
        fast = pool.submit(call, "fast second prompt", second, 1)
        first_answer = json.loads(slow.result(timeout=5))
        second_answer = json.loads(fast.result(timeout=5))

    assert first_answer["images"] == [first]
    assert first_answer["prompt"].startswith("slow first prompt\n\n")
    assert second_answer["images"] == [second]
    assert second_answer["prompt"].startswith("fast second prompt\n\n")
    assert routes == [
        {"backend": "codex_exec", "model": "codex-test",
         "model_version": "cli-default", "fallback_causes": [],
         "input_tokens": 12, "output_tokens": 3},
        {"backend": "codex_exec", "model": "codex-test",
         "model_version": "cli-default", "fallback_causes": [],
         "input_tokens": 12, "output_tokens": 3},
    ]
    records = [json.loads(line) for line in record_path.read_text().splitlines()]
    assert {Path(record["images"][0]).name for record in records} == {
        "first.png", "second.png"}
    assert len({record["answer_path"] for record in records}) == 2


def test_direct_calls_obey_configured_concurrency(tmp_path, monkeypatch):
    monkeypatch.setenv(still_vision.MAX_CONCURRENCY_ENV_VAR, "2")
    monkeypatch.setattr(still_vision.shutil, "which", lambda _name: "codex")
    lock = threading.Lock()
    active = 0
    peak = 0

    def fake_run(command, *, input, **kwargs):
        nonlocal active, peak
        answer_path = Path(command[command.index("--output-last-message") + 1])
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.04)
        answer_path.write_text(input, encoding="utf-8")
        with lock:
            active -= 1
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"usage": {"input_tokens": 1,
                                         "output_tokens": 1}}),
            stderr="")

    monkeypatch.setattr(still_vision.subprocess, "run", fake_run)
    images = [_still(tmp_path, f"frame-{i}.png") for i in range(5)]
    with ThreadPoolExecutor(max_workers=5) as pool:
        answers = list(pool.map(
            lambda pair: inspect_stills(
                f"request-{pair[0]}", [pair[1]], harness="agent",
                step_id=f"step-{pair[0]}"), enumerate(images)))
    assert peak == 2
    assert all(answer.startswith(f"request-{i}\n\n")
               for i, answer in enumerate(answers))


def test_failed_or_empty_codex_answer_raises_without_gemma_fallback(
        tmp_path, monkeypatch):
    monkeypatch.setattr(still_vision.shutil, "which", lambda _name: "codex")
    monkeypatch.setattr(
        still_vision.subprocess, "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr=""))
    monkeypatch.setattr(still_vision, "answer_via_gemma",
                        lambda *_args, **_kwargs: pytest.fail("unexpected fallback"))
    with pytest.raises(StillVisionCallError, match="status 1"):
        inspect_stills("Look.", [_still(tmp_path)], harness="agent")


def test_direct_call_requires_absolute_existing_images(tmp_path):
    with pytest.raises(ValueError, match="not absolute"):
        request_direct_answer("Look.", ["relative.jpg"], "step",
                              executable="codex")
    with pytest.raises(ValueError, match="not a file"):
        request_direct_answer("Look.", [str(tmp_path / "missing.jpg")],
                              "step", executable="codex")


def test_analyzer_stills_use_codex_and_keep_raw_answer(tmp_path, monkeypatch):
    from library.tools.analysis import vision_pipeline_v3 as vp

    _fake_codex(monkeypatch, tmp_path)
    image = _still(tmp_path)
    analyzer = vp.VisionAnalyzer(
        None, None, harness="agent", project_folder=str(tmp_path),
        step_id="semantic_analysis")
    route = {}
    text, _elapsed = analyzer.analyze(
        "List objects.", images=[image], max_tokens=64, _route=route)
    answer = json.loads(text)
    assert answer["images"] == [image]
    assert answer["prompt"].startswith("List objects.\n\n")
    assert route["backend"] == "codex_exec"
    assert route["model"] == "codex-test"


def test_analyzer_gemma_path_files_no_handshake(tmp_path, monkeypatch):
    """No host: the analyzer delegates to gemma and files nothing."""
    from library.tools.analysis import vision_pipeline_v3 as vp

    monkeypatch.delenv(still_vision.HARNESS_ENV_VAR, raising=False)
    monkeypatch.setattr(
        still_vision, "answer_via_gemma",
        lambda prompt, images, max_tokens=800, route_metadata=None,
        **_kwargs: "gemma text")
    project = tmp_path / "proj"
    project.mkdir()
    analyzer = vp.VisionAnalyzer(None, None, harness=None,
                                 project_folder=str(project))
    route = {}
    text, _ = analyzer.analyze(
        "List objects.", images=[_still(tmp_path)], max_tokens=64,
        _route=route)
    assert text == "gemma text"
    assert route["fallback_causes"] == [
        {"stage": "still_route", "cause": "no_host_configured"}]
    assert not (project / "pipeline_output").exists()


# --------------------------------------------------------------------------
# From test_vfx_stills.py
#
# 4.03 sees the picture its effects land on.
#
# History: docs/evidence/resolve_test_history.md#test_vfx_stills.

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.steps.step_4_03_plan_vfx import bridge as vfx_bridge  # noqa: E402

SPINE = {"structure": [
    {"position": 1, "block_type": "speech", "clip_id": "clip_001",
     "source_start": 100.0, "source_end": 110.0,
     "timeline_start": 8.0, "timeline_end": 18.0},
    {"position": 2, "block_type": "speech", "clip_id": "clip_002",
     "source_start": 200.0, "source_end": 204.0,
     "timeline_start": 18.0, "timeline_end": 22.0},
    {"position": 3, "block_type": "transition_slot",
     "timeline_start": 22.0, "timeline_end": 24.0},
]}

AROLL = [{"video_segments": [
    {"clip_id": "clip_001", "source_file": "/nowhere/a.mp4"},
    {"clip_id": "clip_002", "source_file": "/nowhere/b.mp4"},
]}]

TEMPORAL = [
    {"clip_id": "clip_001", "motion_method": "farneback",
     "motion_peaks": [
         {"time": 99.0, "kind": "apex", "magnitude": 0.9},
         {"time": 103.0, "kind": "onset", "magnitude": 0.4},
         {"time": 104.0, "kind": "apex", "magnitude": 0.7}]},
    {"clip_id": "clip_002", "motion_method": "farneback",
     "motion_peaks": []},
]


def _data(**over):
    payload = {"timed_spine": SPINE, "a_roll_assignments": AROLL,
               "b_roll_assignments": [],
               "temporal_event_indices": TEMPORAL}
    payload.update(over)
    return payload


def test_still_moment_prefers_an_apex_then_the_middle():
    motion = vfx_bridge._motion_by_clip(_data())
    at, basis = vfx_bridge._block_still_moment(
        SPINE["structure"][0], "clip_001", motion)
    # The 99.0 apex sits outside the 100-110 range: skipped.
    assert (at, basis) == (104.0, "apex")
    # No apex in range: the middle. No range at all: unranged.
    assert vfx_bridge._block_still_moment(
        SPINE["structure"][1], "clip_002", motion) == (202.0, "middle")
    assert vfx_bridge._block_still_moment(
        SPINE["structure"][2], None, motion) == (None, "unranged")


def test_draw_names_what_it_could_not_draw(tmp_path, monkeypatch):
    """Two stills drawn at the right seconds; the rangeless block named."""
    import library.steps.step_4_03_plan_vfx.bridge as bridge_mod

    drawn = {}

    def fake_extract(source, out_path, at_seconds=None):
        drawn[out_path] = (source, at_seconds)
        Path(out_path).write_bytes(b"fake-jpeg")
        return True

    monkeypatch.setattr(bridge_mod, "extract_still", fake_extract)
    block, paths = vfx_bridge.draw_vfx_stills(
        _data(), str(tmp_path))
    assert len(paths) == 2
    assert drawn[paths[0]] == ("/nowhere/a.mp4", 104.0)
    assert drawn[paths[1]] == ("/nowhere/b.mp4", 202.0)
    assert "block_1__vfx.jpg" in block
    assert "NOT DRAWN: 3 (no source clip)" in block
    # Without a project folder nothing is drawn.
    assert vfx_bridge.draw_vfx_stills(_data(), "") == ("", [])


def test_observe_states_whose_vision_answered(monkeypatch, tmp_path):
    import library.steps.step_4_03_plan_vfx.bridge as bridge_mod

    still = tmp_path / "block_1__vfx.jpg"
    still.write_bytes(b"fake-jpeg")
    monkeypatch.setattr(
        bridge_mod.still_router, "inspect_stills",
        lambda *a, **k: "block_1__vfx.jpg: still, judged on the wall")
    monkeypatch.setattr(
        bridge_mod.still_router, "resolve_harness", lambda: None)
    notes = vfx_bridge.observe_vfx_stills([str(still)], str(tmp_path))
    assert notes["observed_by"].startswith("gemma4 fallback")
    assert "still" in notes["text"]
    # And why nothing answered, when nothing did.
    notes = vfx_bridge.observe_vfx_stills([], "/nowhere")
    assert notes["observed_by"] == "none"
    assert "no stills were drawn" in notes["reason"]
    notes = vfx_bridge.observe_vfx_stills(["/nowhere/x.jpg"], "")
    assert "no project_folder" in notes["reason"]


def test_bridge_emits_declared_still_keys(tmp_path, monkeypatch,
                                          tmp_path_factory=None):
    """The bridge output carries the stills beside the candidate table."""
    import subprocess

    proc = subprocess.run(
        [sys.executable, "library/steps/step_4_03_plan_vfx/bridge.py"],
        input=json.dumps(_data()), capture_output=True,
        encoding="utf-8", cwd=str(PROJECT_ROOT), check=False,
        env={"PYTHONPATH": str(PROJECT_ROOT), "PATH": "/usr/bin:/bin"})
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert "vfx_candidates_toon" in out
    # No project_folder: nothing drawn, absence stated.
    assert out["vfx_shot_stills"] == ""
    assert out["still_motion_notes"]["observed_by"] == "none"
