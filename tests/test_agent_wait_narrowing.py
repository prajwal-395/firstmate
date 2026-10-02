"""The agent wait loop is stubbed narrowly, never globally.

Measured 2026-09-15 (PR 1154's chain): a stray thread from an earlier test
spinning on `time.sleep(0.05)` kept running inside later agent-stub tests,
and because those stubbed the GLOBAL sleep, every stray sleep rewrote the
later test's own response file concurrent with its read - surfacing as an
unreproducible `LLMError` parse failure at suite scale only (12/12 in
isolation). PR 1154 stopped that one thread; this pins the amplifier shut:
`present_llm_step` waits through `model_task._agent_sleep` /
`model_task._agent_clock`, module-level names a test can stub without
touching the `time` module every other thread calls.

Two pins, one behavioral and one structural:

* a stray thread hammering the GLOBAL `time.sleep` while an agent-stub
  test runs must never trigger the stub - every stub call arrives on the
  test's own thread;
* no test may patch the global `time.sleep`/`time.time` again, in any of
  the shapes this tree has used (`patch("time.sleep")`,
  `monkeypatch.setattr(run_pipeline.time, "sleep", ...)`).

Note the shape the second test deliberately does NOT bless:
`monkeypatch.setattr(run_pipeline.time, "sleep", ...)` reads as narrowed
but is global - `run_pipeline.time` IS the shared `time` module object,
so setting an attribute on it patches every thread's sleep. The narrow
target is `model_task._agent_sleep` itself.
"""
import ast
import json
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from library.processes.edit_video import run_pipeline
from library.tools import model_task


def test_a_stray_thread_sleeping_does_not_answer_the_agent_wait(tmp_path):
    """The 1154 interference, replayed against the narrowed stub.

    A background thread spins on the GLOBAL `time.sleep` - the stray PR
    1154 removed - while `present_llm_step` runs with its wait stubbed.
    On the old shape the stray's sleeps executed the stub (rewriting this
    test's response file off-thread); on the narrowed shape they cannot:
    the stub fires only on the test's own thread and the answer parses.
    """
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()
    prompt_path = project_dir / "handoff.md"
    prompt_path.write_text("Test prompt")
    responses_dir = project_dir / "pipeline_output" / "llm_responses"
    res_file = responses_dir / "test_step.json"
    res_data = {"test_out": "agent_success"}

    main_ident = threading.get_ident()
    stub_idents = []

    def stubbed_wait(seconds):
        stub_idents.append(threading.get_ident())
        res_file.parent.mkdir(parents=True, exist_ok=True)
        res_file.write_text(json.dumps(res_data), encoding="utf-8")
        time.sleep(0.01)

    stop = threading.Event()
    stray_sleeps = {"n": 0}

    def stray():
        while not stop.is_set():
            time.sleep(0.001)
            stray_sleeps["n"] += 1

    thread = threading.Thread(target=stray, daemon=True)
    thread.start()
    try:
        with patch.object(model_task, "_agent_sleep",
                          side_effect=stubbed_wait):
            output = run_pipeline.present_llm_step(
                str(prompt_path), {"project_folder": str(project_dir)},
                "test_step", full_auto="agent", llm_timeout=30)
    finally:
        stop.set()
        thread.join(timeout=10)

    assert output == res_data
    assert stray_sleeps["n"] > 0, (
        "the stray thread never ran, so this proves nothing")
    assert stub_idents, "the stub never fired"
    assert all(ident == main_ident for ident in stub_idents), (
        f"the global sleep reached the agent stub from another thread: "
        f"{len(stub_idents)} stub calls, "
        f"{sum(1 for i in stub_idents if i != main_ident)} off-thread")


def _global_time_patches(tree: ast.AST):
    """Yield (lineno, description) for patches of the GLOBAL clock."""
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        strings = [a.value for a in node.args
                   if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        if (isinstance(func, ast.Name) and func.id == "patch"
                and any(s in ("time.sleep", "time.time") for s in strings)):
            offenders.append((node.lineno, f"patch({strings[0]!r})"))
        elif (isinstance(func, ast.Attribute) and func.attr == "object"
                and isinstance(func.value, ast.Name)
                and func.value.id == "patch"
                and len(strings) >= 2
                and strings[1] in ("sleep", "time")):
            offenders.append((node.lineno, f"patch.object(..., {strings[1]!r})"))
        elif (isinstance(func, ast.Attribute) and func.attr == "setattr"
                and len(node.args) >= 2
                and isinstance(node.args[0], ast.Attribute)
                and node.args[0].attr == "time"
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value in ("sleep", "time")):
            offenders.append((node.lineno,
                              f"setattr(<...>.time, {node.args[1].value!r})"))
    return offenders


def test_no_test_patches_the_global_sleep_or_clock():
    """The amplifier stays shut: no test stubs `time` itself.

    Fails naming every `file:line` that patches the global
    `time.sleep`/`time.time` (or `run_pipeline.time.sleep`, which is the
    same object). Stub `model_task._agent_sleep` /
    `model_task._agent_clock` instead - the wait the module under test
    performs, and nothing else.
    """
    offenders = []
    for path in sorted((REPO / "tests").glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for lineno, what in _global_time_patches(tree):
            offenders.append(f"{path.name}:{lineno} {what}")
    assert not offenders, (
        "tests patching the GLOBAL sleep/clock (a stray thread from any "
        "test calling time.sleep would execute these stubs off-thread, "
        "PR 1154's chain):\n" + "\n".join(offenders))
