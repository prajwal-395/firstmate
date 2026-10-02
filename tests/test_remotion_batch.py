"""One bundle for many cards, and a failed card is not a failed batch.

Issue #530.  Every overlay was rendered by shelling out to
`npx remotion render`, once per card - 763 bundle-and-launch cycles for a
nineteen-reel caption pass.  That startup is the cost: eight concurrent
invocations took a 10-core machine from load 4.42 to 18.12 in twenty
seconds, and deriving the pool from `os.cpu_count()` while pinning each
renderer's frame concurrency to 1 still measured 17.42.  Rationing
invocations cannot remove what an invocation costs.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from library.tools.remotion_batch import (
    RemotionBatchError,
    RenderJob,
    _require_dependencies as _real_require_dependencies,
    card_concurrency,
    encoder_threads,
    frame_concurrency,
    render_batch,
    renderer_limits,
    remotion_dir,
    run_caption_card_workers,
)


@pytest.fixture(autouse=True)
def _node_store_bound():
    """The batch plumbing is tested with the renderer mocked out, so it
    must not depend on this checkout being bound to the shared Node
    store.

    2026-09-15: six tests in this file failed in every fresh worktree -
    not because the code was wrong but because `render_batch` and
    `PersistentRenderer.start` refuse an unbound checkout BEFORE reaching
    the mocked subprocess, so the gate reported an environment gap as six
    test failures. The tests that really render skip through the
    `remotion` capability instead and narrow the verdict by name; these
    unit tests pin the binding aside, and the refusal itself is pinned by
    the two `unbound_checkout` tests below against a tmp layout.
    """
    with patch("library.tools.remotion_batch._require_dependencies",
               lambda directory, error: None):
        yield


def _jobs(n: int, tmp_path: Path):
    return [RenderJob(props={"durationInFrames": 24, "n": i},
                      out_path=str(tmp_path / f"card_{i}.mov"))
            for i in range(n)]


class _Proc:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


def test_many_cards_are_one_subprocess(tmp_path):
    """The whole point: N cards, ONE invocation with the measured fan-out."""
    jobs = _jobs(5, tmp_path)
    out = "\n".join(json.dumps({"ok": True, "out": j.out_path}) for j in jobs)
    with patch("subprocess.run", return_value=_Proc(out)) as run:
        results = render_batch(jobs, composition="SubtitleOverlay",
                               work_dir=str(tmp_path))
    assert run.call_count == 1
    assert len(results) == 5
    assert all(r["ok"] for r in results)
    spec_path = run.call_args.args[0][-1]
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    assert spec["cardConcurrency"] == card_concurrency()


def test_a_failed_card_is_not_a_failed_batch(tmp_path):
    """A card that will not draw must not discard the ones that did -
    the caller places what rendered and reports what did not, by name."""
    jobs = _jobs(3, tmp_path)
    out = "\n".join([
        json.dumps({"ok": True, "out": jobs[0].out_path}),
        json.dumps({"ok": False, "out": jobs[1].out_path, "error": "boom"}),
        json.dumps({"ok": True, "out": jobs[2].out_path}),
    ])
    with patch("subprocess.run", return_value=_Proc(out, returncode=1)):
        results = render_batch(jobs, composition="SubtitleOverlay",
                               work_dir=str(tmp_path))
    assert [r["ok"] for r in results] == [True, False, True]


def test_a_batch_that_could_not_start_raises(tmp_path):
    """No results AND a non-zero exit is not 'every card failed', it is
    'the renderer never ran' - which is not a property of any card."""
    jobs = _jobs(2, tmp_path)
    with patch("subprocess.run", return_value=_Proc("", returncode=1, stderr="node: not found")):
        with pytest.raises(RemotionBatchError) as excinfo:
            render_batch(jobs, composition="SubtitleOverlay",
                         work_dir=str(tmp_path))
    assert "no results" in str(excinfo.value)


def _unbound_layout(tmp_path):
    """A checkout-shaped directory with the script but no node_modules -
    the shape a fresh worktree has before it is bound to the store.

    Carries the real lockfile so the refusal names the store entry and
    the install command rather than reporting an incomplete checkout,
    which is a different defect."""
    checkout = tmp_path / "checkout"
    remote = checkout / "remotion-subtitles"
    remote.mkdir(parents=True)
    (remote / "render-batch.mjs").write_text(
        "// stand-in: only presence matters here, nothing is executed")
    real = remotion_dir()
    for name in ("package.json", "package-lock.json"):
        (remote / name).write_bytes((real / name).read_bytes())
    return checkout


def test_render_batch_refuses_an_unbound_checkout_with_the_remedy(tmp_path):
    """The refusal the autouse fixture pins aside still fires where it
    should: through the real entry point, against a layout with no
    node_modules, naming the command that fixes it."""
    checkout = _unbound_layout(tmp_path)
    with patch("library.tools.remotion_batch._require_dependencies",
               _real_require_dependencies):
        with pytest.raises(RemotionBatchError) as excinfo:
            render_batch(_jobs(1, tmp_path), composition="SubtitleOverlay",
                         work_dir=str(tmp_path / "work"),
                         repo_root=str(checkout))
    assert "install_node_deps" in str(excinfo.value)


def test_the_persistent_renderer_refuses_an_unbound_checkout(tmp_path):
    """Same refusal through the other entry point. It must fire before
    any child is spawned, so nothing is left in the open registry."""
    from library.tools.remotion_batch import (
        PersistentRenderer,
        RendererUnavailable,
    )

    checkout = _unbound_layout(tmp_path)
    before = list(PersistentRenderer._open)
    with patch("library.tools.remotion_batch._require_dependencies",
               _real_require_dependencies):
        with pytest.raises(RendererUnavailable) as excinfo:
            PersistentRenderer(composition="X",
                               repo_root=str(checkout)).start()
    assert "install_node_deps" in str(excinfo.value)
    assert PersistentRenderer._open == before


# ── The bound, now that it is the dominant term ──────────────────────
#
# With one bundle and one browser the per-invocation startup is gone, so
# Remotion's own frame concurrency is finally what costs. Measured on the
# captain's 10-core machine, twelve real caption cards each time:
#
#     concurrency 1   21.5s   load 5.94 -> 7.24   (+1.30)
#     concurrency 2   17.1s   load 7.06 -> 8.02   (+0.96)
#     concurrency 4   14.9s   load 8.02 -> 14.75  (+6.73)
#
# Sharply non-linear: 2 to 4 buys 2.2 seconds and costs seven points of
# load. An earlier bound was removed because it was the wrong lever
# against a startup-dominated cost; this is a different lever against a
# different bottleneck, and these tests hold it derived.

def _conc(cpus, load):
    with patch("os.cpu_count", return_value=cpus), \
         patch("os.getloadavg", return_value=(load, load, load)):
        return frame_concurrency()


def test_the_bound_follows_the_FREE_cores_not_the_total():
    """Above the floor, the bound still tracks what is FREE.

    The captain renders while working with Resolve open, so a bound
    derived from an idle machine is wrong on a busy one - that intent is
    unchanged. What changed is the BOTTOM: the free-core term alone
    collapsed to 1 on a busy box, and 1 was measured slower than the npx
    path this replaces. A floor derived from TOTAL cores now holds
    underneath it.
    """
    assert _conc(10, 1.0) == 4      # idle 10-core box
    assert _conc(10, 6.0) == 2      # his usual working load
    assert _conc(10, 9.5) == 2      # busy - floor, no longer a collapse
    assert _conc(64, 4.0) == 30     # scales with the machine


def test_the_bound_never_falls_below_the_measured_floor():
    """It was "never zero"; it is now "never below a fifth of the box".

    Never-zero was too weak. A bound of 1 renders, so it passed - and
    measured on 10 cores at load ~11 it took 48.01s against the npx
    path's 35.52s, so the renderer was slower than the thing it exists
    to beat while still satisfying "not zero". The floor is the smallest
    allocation MEASURED to win.
    """
    assert _conc(10, 10.0) == 2
    assert _conc(10, 40.0) == 2
    assert _conc(1, 0.0) == 1       # a one-core box has nothing to split


def test_no_load_reading_is_not_an_excuse_to_take_the_machine():
    """Where the average is unavailable the bound still holds to half the
    cores rather than falling back to unbounded."""
    with patch("os.cpu_count", return_value=10), \
         patch("os.getloadavg", side_effect=OSError):
        assert frame_concurrency() == 5


def test_the_encoder_is_bounded_too():
    """The negative result that mattered. With `concurrency` already at
    its floor of 1, a full pass still ran the captain's machine to 18.00,
    and a process reading mid-pass showed remotion's bundled ffmpeg at
    763% CPU - 7.6 of ten cores. `concurrency` bounds browser tabs and
    says nothing about the encoder, which takes every core it can see.
    """
    with patch("os.cpu_count", return_value=10), \
         patch("os.getloadavg", return_value=(1.0, 1.0, 1.0)):
        assert encoder_threads() == 4
    # A BUSY box no longer collapses to 1. It used to, and that was the
    # defect: measured on 10 cores at load ~11 against the npx path it
    # replaces (35.52s), a renderer pinned to 1 took 48.01s - SLOWER
    # than the thing it exists to beat - while 2 took 30.35s. The floor
    # is a fifth of TOTAL cores so a busy moment cannot move it.
    with patch("os.cpu_count", return_value=10), \
         patch("os.getloadavg", return_value=(9.5, 9.5, 9.5)):
        assert encoder_threads() == 2


def test_card_fanout_is_cpu_sized_and_caps_at_the_measured_four():
    """The measured four-card winner must not open four slots on every host."""
    with patch("os.cpu_count", return_value=1):
        assert card_concurrency() == 1
    with patch("os.cpu_count", return_value=4):
        assert card_concurrency() == 2
    with patch("os.cpu_count", return_value=10):
        assert card_concurrency() == 4
    with patch("os.cpu_count", return_value=64):
        assert card_concurrency() == 4

    with patch("os.cpu_count", return_value=10), \
         patch("os.getloadavg", return_value=(0.0, 0.0, 0.0)):
        assert renderer_limits() == (4, 2, 2)


def test_caption_card_workers_respect_the_bound_and_return_plan_order(
        monkeypatch):
    """A fast card must not reorder the caption plan or exceed its bound."""
    import threading

    monkeypatch.setattr(
        "library.tools.remotion_batch.card_concurrency", lambda: 2)
    guard = threading.Lock()
    active = [0]
    maximum = [0]
    completed = []

    def render(index, card):
        with guard:
            active[0] += 1
            maximum[0] = max(maximum[0], active[0])
        time.sleep(0.01 * (4 - index))
        with guard:
            active[0] -= 1
        return card * 10

    results = run_caption_card_workers(
        [1, 2, 3, 4], render,
        on_result=lambda index, result: completed.append((index, result)))
    assert results == [10, 20, 30, 40]
    assert completed == [(0, 10), (1, 20), (2, 30), (3, 40)]
    assert maximum[0] == 2


def test_caption_card_failure_stops_new_submissions_and_keeps_completed(
        monkeypatch):
    """A dead renderer must not fan out failures for cards never started."""
    import threading

    monkeypatch.setattr(
        "library.tools.remotion_batch.card_concurrency", lambda: 2)
    both_started = threading.Barrier(2)
    failure_signaled = threading.Event()
    started = []
    completed = []

    def render(index, card):
        started.append(index)
        both_started.wait(timeout=2)
        if index == 0:
            failure_signaled.set()
            raise RendererUnavailable("renderer died")
        failure_signaled.wait(timeout=2)
        time.sleep(0.05)
        return card

    with pytest.raises(RendererUnavailable, match="renderer died"):
        run_caption_card_workers(
            [0, 1, 2, 3], render,
            on_result=lambda index, result: completed.append((index, result)))
    assert sorted(started) == [0, 1]
    assert completed == [(1, 1)]


# ── The persistent renderer's LIFECYCLE ─────────────────────────────
#
# A long-lived node process holding a browser is exactly the thing that
# leaks, so the lifecycle is tested rather than described. The death path
# especially: a renderer that quietly falls back to per-card `npx` would
# restore the whole cost this module removes while still reporting
# success, which is a vacuous gate wearing performance clothing.

import subprocess as _subprocess
import time

from library.tools.remotion_batch import (
    PersistentRenderer,
    RendererUnavailable,
)


class _FakeProc:
    """A child that is alive until told otherwise."""

    def __init__(self, answers=(), returncode=None):
        self.stdin = _FakePipe()
        self.stdout = _FakePipe(list(answers))
        self.stderr = _FakePipe()
        self._returncode = returncode
        self.pid = 4242
        self.terminated = self.killed = False

    @property
    def returncode(self):
        # Real Popen exposes this, and the refusal quotes it.
        return self._returncode

    def poll(self):
        return self._returncode

    def wait(self, timeout=None):
        self._returncode = self._returncode if self._returncode is not None else 0
        return self._returncode

    def terminate(self):
        self.terminated = True
        self._returncode = -15

    def kill(self):
        self.killed = True
        self._returncode = -9


class _FakePipe:
    def __init__(self, lines=None):
        self.lines = lines or []
        self.closed = False
        self.written = []

    def readline(self):
        return self.lines.pop(0) if self.lines else ""

    def write(self, text):
        if self.closed:
            raise ValueError("write to closed pipe")
        self.written.append(text)

    def flush(self):
        pass

    def close(self):
        self.closed = True


def _started(monkeypatch, tmp_path, answers=(), render_timeout=5):
    """A renderer whose child is a fake that has already reported ready.

    The real reader thread is attached over the fake pipe, so these
    exercise `_pump`/`_next_line` rather than a stand-in for them - which
    matters, because the FIRST version of this fixture set `_proc`
    without a pump and every read then waited out the full 600s
    `render_timeout`. The hang was the fixture, but a fixture that
    bypasses the mechanism under test would have hidden a real one.
    """
    import threading as _threading

    renderer = PersistentRenderer(composition="X",
                                  render_timeout=render_timeout)
    proc = _FakeProc(answers=answers)
    renderer._proc = proc
    renderer._spec_path = tmp_path / "spec.json"
    renderer._spec_path.write_text("{}")
    renderer._reader = _threading.Thread(
        target=renderer._pump, args=(proc.stdout,), daemon=True)
    renderer._reader.start()
    PersistentRenderer._open.append(renderer)
    return renderer, proc


def test_a_dead_child_refuses_rather_than_falling_back(monkeypatch, tmp_path):
    """The condition that matters most.

    A fallback to per-card `npx` here would silently restore the startup
    cost while reporting success. So a dead child RAISES, and the raise
    is not the same event as a card failing to draw.
    """
    renderer, proc = _started(monkeypatch, tmp_path)
    proc._returncode = -9                       # killed mid-run
    props = tmp_path / "p.json"
    props.write_text("{}")

    with pytest.raises(RendererUnavailable, match="exited"):
        renderer.render(str(props), str(tmp_path / "out.mov"))
    renderer.close()


def test_a_card_failure_is_NOT_a_renderer_failure(monkeypatch, tmp_path):
    """The distinction the whole design turns on.

    One bad card returns (False, error) and the renderer stays up,
    because the bundle is the expensive thing.
    """
    renderer, proc = _started(
        monkeypatch, tmp_path,
        answers=[('{"requestId": 0, "ok": false, "out": "x.mov", '
                  '"error": "bad font"}\n')])
    props = tmp_path / "p.json"
    props.write_text("{}")
    ok, error = renderer.render(str(props), str(tmp_path / "out.mov"))
    assert ok is False and "bad font" in error
    assert renderer.alive, "one bad card must not take the renderer down"
    renderer.close()


def test_concurrent_card_answers_are_matched_to_their_request_ids(
        monkeypatch, tmp_path):
    """A fast second card must not answer the first card's blocked caller."""
    import queue as _queue
    import threading as _threading
    from concurrent.futures import ThreadPoolExecutor

    class _QueuedOutput:
        def __init__(self):
            self.lines = _queue.Queue()
            self.closed = False

        def readline(self):
            return self.lines.get()

        def close(self):
            self.closed = True

    class _RespondingInput(_FakePipe):
        def __init__(self, output):
            super().__init__()
            self.output = output
            self.requests = []

        def write(self, text):
            request = json.loads(text)
            self.written.append(text)
            self.requests.append(request)
            if len(self.requests) == 2:
                for item in reversed(self.requests):
                    self.output.lines.put(json.dumps({
                        "requestId": item["requestId"],
                        "ok": item["requestId"] == 1,
                        "out": item["out"],
                        "error": "first card failed" if item["requestId"] == 0 else "",
                    }) + "\n")

        def close(self):
            super().close()
            self.output.lines.put("")

    class _ConcurrentProc(_FakeProc):
        def __init__(self):
            self.stdout = _QueuedOutput()
            self.stdin = _RespondingInput(self.stdout)
            self.stderr = _FakePipe()
            self._returncode = None
            self.pid = 4243
            self.terminated = self.killed = False

    renderer = PersistentRenderer(composition="X", render_timeout=5)
    proc = _ConcurrentProc()
    renderer._proc = proc
    renderer._spec_path = tmp_path / "spec.json"
    renderer._spec_path.write_text("{}")
    renderer._reader = _threading.Thread(
        target=renderer._pump, args=(proc.stdout,), daemon=True)
    renderer._reader.start()
    PersistentRenderer._open.append(renderer)
    props = tmp_path / "p.json"
    props.write_text("{}")

    with ThreadPoolExecutor(max_workers=2) as pool:
        answers = list(pool.map(
            lambda name: renderer.render(str(props), str(tmp_path / name)),
            ["first.mov", "second.mov"]))
    assert answers == [(False, "first card failed"), (True, "")]
    assert sorted(request["requestId"] for request in proc.stdin.requests) == [0, 1]
    renderer.close()


def test_close_escalates_to_kill_when_the_child_will_not_go(monkeypatch,
                                                            tmp_path):
    """Bounded at every step: a wedged child cannot hang the run."""
    renderer, proc = _started(monkeypatch, tmp_path)

    def _stubborn(timeout=None):
        raise _subprocess.TimeoutExpired(cmd="node", timeout=timeout or 0)

    proc.wait = _stubborn
    renderer.close()
    assert proc.terminated, "close() must terminate a child that ignores EOF"
    assert proc.killed, "close() must kill a child that ignores terminate"


def test_a_closed_renderer_refuses_rather_than_restarting(tmp_path):
    """Lazy start must not become silent RESTART.

    `render()` builds the bundle on first use, but a renderer that has
    been closed is finished: quietly spawning a second process would
    hide a caller bug and pay the bundle twice.
    """
    renderer = PersistentRenderer(composition="X")
    renderer.close()
    props = tmp_path / "p.json"
    props.write_text("{}")
    with pytest.raises(RendererUnavailable, match="been closed"):
        renderer.render(str(props), str(tmp_path / "o.mov"))


def test_the_persistent_renderer_never_shells_out_to_npx():
    """The no-silent-fallback rule, read off the code.

    If this class ever grows an `npx` INVOCATION it has grown the
    fallback the refusal exists to prevent.

    Checked structurally rather than by substring: the refusals here
    legitimately NAME `env.npx` when telling an operator what to install,
    and a substring test failed on its own remedy text. What matters is
    whether "npx" is an argument to a process launch, not whether the
    word appears.
    """
    import ast
    import inspect
    import textwrap

    from library.tools import remotion_batch

    tree = ast.parse(textwrap.dedent(
        inspect.getsource(remotion_batch.PersistentRenderer)))
    launched = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for arg in node.args:
            if isinstance(arg, (ast.List, ast.Tuple)):
                for element in arg.elts:
                    if (isinstance(element, ast.Constant)
                            and element.value == "npx"):
                        launched.append(ast.dump(node)[:80])
    assert launched == [], (
        f"PersistentRenderer launches npx: {launched}. That is the "
        f"per-card fallback that silently restores the startup cost this "
        f"class exists to remove.")


def test_a_wedged_child_times_out_rather_than_hanging_the_run(tmp_path):
    """ALIVE but not answering - which `poll()` cannot see.

    A child that is SIGSTOPped, deadlocked, or stuck in a render that
    never returns is not dead, so every death check above passes and the
    read blocks forever. `render_timeout` bounds it. This test exists
    because the first version of this class DECLARED `render_timeout`,
    stored it, and never enforced it - a declaration nothing reads, which
    is the defect this repository spent a day removing everywhere else.
    """
    renderer = PersistentRenderer(composition="X", render_timeout=1)
    proc = _FakeProc(answers=[])        # alive, and never answers
    renderer._proc = proc
    renderer._spec_path = tmp_path / "spec.json"
    renderer._spec_path.write_text("{}")
    # No pump thread: nothing will ever arrive, which is what a wedged
    # child looks like from here.
    PersistentRenderer._open.append(renderer)

    props = tmp_path / "p.json"
    props.write_text("{}")
    started = time.time()
    with pytest.raises(RendererUnavailable, match="did not answer within"):
        renderer.render(str(props), str(tmp_path / "out.mov"))
    elapsed = time.time() - started
    assert elapsed < 15, (
        f"the wait was not bounded: {elapsed:.1f}s for a 1s timeout")
    renderer.close()


def test_a_missing_node_refuses_by_name_with_the_remedy(tmp_path, monkeypatch):
    """An ABSENT SYSTEM TOOL is the likeliest failure on a fresh checkout,
    and one of the three classes the clean-room gate exists to catch.

    A bare `FileNotFoundError('node')` surfacing from thirty frames down
    tells the operator nothing about what to install.
    """
    from unittest.mock import patch

    with patch("subprocess.Popen", side_effect=FileNotFoundError("node")):
        with pytest.raises(RendererUnavailable) as caught:
            PersistentRenderer(composition="X").start()
    message = str(caught.value)
    assert "Node.js must be on PATH" in message
    assert "npm install" in message
    assert PersistentRenderer._open == [] or all(
        r.alive for r in PersistentRenderer._open), (
        "a renderer that failed to start must not be left in the open "
        "registry, or atexit will try to close a process that never was")


# ── The floor, and why it is a fraction of the MACHINE ──────────────

def test_the_bundle_is_lazy_so_a_pass_that_draws_nothing_pays_nothing():
    """A requirement of the seam, not a nicety.

    `render_one_segment` can return WITHOUT rendering - with reuse on, a
    region-scoped pass skips most cards. A bundle paid at construction
    would be paid in full to draw one card, making the region path
    slower than the thing it replaced. `step_4_05_render_subtitles`
    builds ONE renderer for the whole pass, so eager construction there
    would cost every scoped re-render a full bundle.

    Laziness is pinned by intercepting the single spawn path -
    `PersistentRenderer.start()` is the only caller of `Popen`, and only
    `render()` calls `start()` - rather than by counting processes named
    `render-batch.mjs` in the machine-global table. That counting was
    removed 2026-09-14 after it failed the full-suite gate with
    `assert running() == before` reading 1: `pgrep -f` matches ANY
    process whose argv carries the string, so a concurrent probe in
    another lane, an `rg` search, or a real render elsewhere on the box
    flips the count with nothing started here. Reproduced at will: this
    test fails ~5/25 beside three tight `pgrep` loops and 25/25 alone.
    An eager renderer still turns this red - `start()` reaching `Popen`
    raises out of the block.
    """
    calls = []

    def _no_spawn(*args, **kwargs):
        calls.append(args)
        raise AssertionError(
            "PersistentRenderer spawned a child before any card asked "
            "to be drawn - the bundle is paid lazily, on the first "
            "render, not at construction")

    with patch("subprocess.Popen", side_effect=_no_spawn):
        with PersistentRenderer(composition="SubtitleOverlay") as renderer:
            assert renderer._proc is None, (
                "entering the block started a process before any card asked "
                "to be drawn")
            assert calls == [], (
                f"entering the block spawned a child: {calls}")
        assert renderer._proc is None
        assert calls == [], (
            f"leaving the block spawned a child: {calls}")


def test_it_satisfies_the_seam_the_step_declares():
    """The step's contract is two methods and no more. If this drifts,
    4.05 cannot use this renderer at all."""
    renderer = PersistentRenderer(composition="X")
    assert callable(getattr(renderer, "render", None))
    assert callable(getattr(renderer, "close", None))

    import inspect

    signature = inspect.signature(PersistentRenderer.render)
    assert list(signature.parameters) == ["self", "props_path",
                                          "overlay_path", "sequence"], (
        f"render() no longer matches the seam "
        f"`render(props_path, overlay_path, sequence=False) -> (ok, "
        f"error)`: {list(signature.parameters)}")
    # The persistent renderer stitches video; a sequence it cannot
    # draw must refuse loudly rather than report success.
    with pytest.raises(ValueError, match="sequence"):
        renderer.render("props.json", "out", sequence=True)


def test_batch_renderer_does_not_leak_chrome_when_a_card_fails():
    """Two cards share one Chrome process, and a failed card still closes it.

    The defect this catches: opening a browser inside each render, or
    forgetting to close the shared browser when the serve loop unwinds,
    leaks a Chrome process per card or after a failed render.
    """
    import shutil

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to exercise the Remotion batch lifecycle")

    root = Path(__file__).resolve().parents[1] / "remotion-subtitles"
    script = r"""
import assert from "node:assert/strict";
import { mapWithConcurrency, withBatchRenderer } from "./render-batch-core.mjs";

const browser = { closeCalls: 0, async close(options) {
  assert.deepEqual(options, {silent: true});
  this.closeCalls += 1;
}};
let openCalls = 0;
let selectCalls = 0;
let renderCalls = 0;
let activeRenders = 0;
let maxActiveRenders = 0;
const common = {
  serveUrl: "http://localhost:3000",
  compositionId: "SubtitleOverlay",
  concurrency: 2,
  encoderThreads: 2,
  boundEncoder: ({args}) => ["-threads", "2", ...args],
  openBrowser: async (name) => {
    assert.equal(name, "chrome");
    openCalls += 1;
    return browser;
  },
  selectComposition: async (options) => {
    assert.equal(options.puppeteerInstance, browser);
    selectCalls += 1;
    return {width: 10, height: 10, fps: 30, durationInFrames: 24};
  },
  renderMedia: async (options) => {
    assert.equal(options.puppeteerInstance, browser);
    renderCalls += 1;
    activeRenders += 1;
    maxActiveRenders = Math.max(maxActiveRenders, activeRenders);
    await new Promise((resolve) => setTimeout(resolve, 20));
    activeRenders -= 1;
    if (options.outputLocation === "second.mov") throw new Error("render failed");
  },
};

await assert.rejects(withBatchRenderer(common, async (renderOne) => {
  return mapWithConcurrency([
    {props: {durationInFrames: 24}, out: "first.mov"},
    {props: {durationInFrames: 24}, out: "second.mov"},
  ], 2, renderOne);
}), /render failed/);

assert.equal(openCalls, 1, "both cards must reuse one Chrome process");
assert.equal(selectCalls, 2);
assert.equal(renderCalls, 2);
assert.equal(maxActiveRenders, 2, "two cards must be in flight together");
assert.equal(browser.closeCalls, 1, "the shared Chrome process must close on failure");
"""
    result = _subprocess.run(
        [node, "--input-type=module", "-e", script],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr
