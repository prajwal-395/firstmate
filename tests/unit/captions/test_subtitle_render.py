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
import os
import sys
import copy
import importlib.util
import unittest.mock as mock
from library.steps.step_4_05_render_subtitles.step import render_one_segment
from library.tools import operations
from library.tools import reel_spine
from library.tools.reel_build import reel_subtitle_segments
from library.tools.subtitle_segment_id import (
    assert_no_content_collision,
    SegmentNameCollision,
    provenance_stem,
    segment_binding,
    segment_identifier,
    slug,
    speaker_slug_from_segment_id,
    stable_prefix,
    timeline_scope,
)
from library.steps.step_4_01_plan_subtitles.step import generate_subtitles
from library.steps.step_1_04_temporal_index.step import splice_region_index
from library.steps.step_4_01_plan_subtitles.step import (
    splice_region_plan,
)
from library.tools import scope as scope_mod
from library.tools import state_splice
from library.tools.subtitle_splice import (
    SpliceRefused, assert_durations_preserved, outside_region, splice_plan,
    splice_report,
)


@pytest.fixture
def _node_store_bound():
    """The plumbing is tested with the renderer mocked out, so it must not
    depend on this checkout being bound to the shared Node store; the
    refusal itself is pinned by the `unbound_checkout` test below.
    History: docs/evidence/remotion_batch.md."""
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


@pytest.mark.usefixtures("_node_store_bound")
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


@pytest.mark.usefixtures("_node_store_bound")
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


@pytest.mark.usefixtures("_node_store_bound")
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


@pytest.mark.usefixtures("_node_store_bound")
def test_both_entry_points_refuse_an_unbound_checkout_with_the_remedy(tmp_path):
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

    # Same refusal through the persistent renderer, before any child is
    # spawned, so nothing is left in the open registry.
    from library.tools.remotion_batch import (
        PersistentRenderer,
        RendererUnavailable,
    )
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


@pytest.mark.usefixtures("_node_store_bound")
def test_the_frame_bound_follows_free_cores_above_a_measured_floor():
    """The bound tracks FREE cores (the captain renders with Resolve open),
    but never below a fifth of TOTAL cores: a bound of 1 measured 48.01s
    against the npx path's 35.52s on 10 cores at load ~11, slower than
    the thing it replaces. No load reading holds to half the cores."""
    assert _conc(10, 1.0) == 4      # idle 10-core box
    assert _conc(10, 6.0) == 2      # his usual working load
    assert _conc(10, 9.5) == 2      # busy - floor, no longer a collapse
    assert _conc(64, 4.0) == 30     # scales with the machine
    assert _conc(10, 10.0) == 2
    assert _conc(10, 40.0) == 2
    assert _conc(1, 0.0) == 1       # a one-core box has nothing to split
    with patch("os.cpu_count", return_value=10), \
         patch("os.getloadavg", side_effect=OSError):
        assert frame_concurrency() == 5


@pytest.mark.usefixtures("_node_store_bound")
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


@pytest.mark.usefixtures("_node_store_bound")
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


@pytest.mark.usefixtures("_node_store_bound")
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


@pytest.mark.usefixtures("_node_store_bound")
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


@pytest.mark.usefixtures("_node_store_bound")
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


@pytest.mark.usefixtures("_node_store_bound")
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


@pytest.mark.usefixtures("_node_store_bound")
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


@pytest.mark.usefixtures("_node_store_bound")
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


@pytest.mark.usefixtures("_node_store_bound")
def test_a_closed_renderer_or_a_sequence_refuses(tmp_path):
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
    # A sequence it cannot draw refuses loudly rather than reporting
    # success (the seam is `render(props, overlay, sequence=False)`).
    with pytest.raises(ValueError, match="sequence"):
        PersistentRenderer(composition="X").render(
            "props.json", "out", sequence=True)


@pytest.mark.usefixtures("_node_store_bound")
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


@pytest.mark.usefixtures("_node_store_bound")
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

@pytest.mark.usefixtures("_node_store_bound")
def test_the_bundle_is_lazy_so_a_pass_that_draws_nothing_pays_nothing():
    """A region-scoped pass may draw no card, and 4.05 builds ONE
    renderer per pass, so the bundle is paid on the first render, not at
    construction. Pinned at the single spawn path (`Popen`), never by
    counting processes: docs/evidence/remotion_batch.md."""
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


@pytest.mark.usefixtures("_node_store_bound")
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

    root = Path(__file__).resolve().parents[3] / "remotion-subtitles"
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


# --------------------------------------------------------------------------
# From test_caption_asset_gc.py
#
# A garbage collector for rendered caption assets, built on reachability.
#
# The defect this closes
# ----------------------
# `pipeline_output/steps/4_05_render_subtitles/` on the field project held
# 13 GB across ~5,700 files: superseded generations of re-rendered cards
# and older title slugs no run cleans up, because no run owns the
# directory's lifetime. The tempting collector - by name, slug or age - is
# WRONG: an older-named asset may still be placed on a timeline the
# captain keeps, and deleting a referenced asset silently breaks it.
#
# The only safe test is REACHABILITY: an asset is garbage when NOTHING
# references it. The roots are every timeline in the captain's Resolve
# project (read through a COPY of the database, never the live file), the
# pipeline's own current step records and manifests, and anything a later
# step consumes downstream. An asset reachable from any root is LIVE.
# Everything else is a candidate, and a candidate is not a deletion until
# the captain or firstmate says so - the sweep MOVES to quarantine and
# never deletes.
#
# Three parts: a read-only `mark`, a `sweep` that refuses on a stale mark
# or an unreadable root, and a retention rule that orphans a superseded
# generation at re-render time.

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.caption_asset_gc import (  # noqa: E402
    LIVE,
    ORPHAN,
    RENDER_LEDGER_NAME,
    LedgerUnreadable,
    SweepRefused,
    collect_pipeline_roots,
    collect_resolve_roots,
    ledger_path_for,
    mark,
    reconcile_render_ledger,
    record_rendered_segments,
    sweep,
)


def _write(path, size=100):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(b"x" * size)
    return path


def _asset_dir(root):
    return os.path.join(
        root, "pipeline_output", "steps", "4_05_render_subtitles")


def _mov_names():
    return [
        "sub_tl_akshita_1_10000-12000_aaaaaaaa.mov",
        "sub_tl_akshita_1_10000-12000_bbbbbbbb.mov",
        "sub_tl_craig_2_20000-22000_cccccccc.mov",
    ]


def _populate(asset_dir):
    """Three movs (two generations of one card), each with siblings."""
    for name in _mov_names():
        stem = name[:-4]
        _write(os.path.join(asset_dir, name), size=1000)
        _write(os.path.join(asset_dir, stem + "_props.json"), size=100)
        _write(os.path.join(asset_dir, stem + "_reuse_key.txt"), size=10)
    # A lone sibling: props whose mov never rendered (failed render).
    _write(os.path.join(
        asset_dir, "sub_tl_akshita_9_90000-92000_dddddddd_props.json"),
        size=100)
    return asset_dir


# --------------------------------------- the unreadable-root refusal


def test_unreadable_root_refuses_sweep(tmp_path):
    """A root that could not be read refuses the sweep, never widens it.

    An unreadable database returns nothing, which reads exactly like a
    project with nothing on any timeline - and would mark every file
    deletable, including the captain's live captions. That failure
    direction deletes work, so the sweep stops instead. This is the
    behaviour the whole module is built around, and it is tested first.
    """
    project = str(tmp_path)
    asset_dir = _populate(_asset_dir(project))
    db_path = os.path.join(project, "no-such-database", "Project.db")
    roots = collect_resolve_roots([db_path])
    assert roots[0].status == "unreadable"
    result = mark(project, asset_dir, roots)
    orphans = [a for a in result.assets if a.status == ORPHAN]
    assert orphans, "an unreadable root must not read as 'everything live'"
    mark_path = os.path.join(project, "mark.json")
    result.write_json(mark_path)
    with pytest.raises(SweepRefused) as refused:
        sweep(mark_path, project_folder=project)
    assert "unreadable" in str(refused.value).lower()
    # Nothing moved: every file is still where it was.
    assert sorted(os.listdir(asset_dir)) == sorted(_mov_names()
        + [n[:-4] + "_props.json" for n in _mov_names()]
        + [n[:-4] + "_reuse_key.txt" for n in _mov_names()]
        + ["sub_tl_akshita_9_90000-92000_dddddddd_props.json"])


def test_sweep_refuses_when_a_root_became_unreadable_since_mark(tmp_path):
    """    The mark was fine, but the database is gone at sweep time.

    The mark is a claim about a moment and the move happens in a later
    one, so readability is re-proved then, not carried over.
    """
    project = str(tmp_path)
    asset_dir = _populate(_asset_dir(project))
    live = os.path.join(asset_dir, _mov_names()[0])
    roots = [_ok_root("resolve:test", {live})]
    result = mark(project, asset_dir, roots)
    mark_path = os.path.join(project, "mark.json")
    result.write_json(mark_path)
    gone = os.path.join(project, "moved-away.db")
    with pytest.raises(SweepRefused):
        sweep(mark_path, project_folder=project,
              db_paths=[gone])


def _ok_root(name, paths):
    from library.tools.caption_asset_gc import RootResult
    return RootResult(name=name, status="ok", paths=set(paths), detail="")


# ------------------------------------------------------------- the mark


def test_siblings_and_box_sidecars_follow_their_mov(tmp_path):
    """A .json/.txt sibling has no lifetime of its own.

    It lives when its mov lives and goes when its mov goes, because
    nothing references a props file - timelines place the mov. A sibling
    whose mov is absent (a render that failed after writing props) is an
    orphan on its own.
    """
    project = str(tmp_path)
    asset_dir = _populate(_asset_dir(project))
    live_mov = os.path.join(asset_dir, _mov_names()[2])
    roots = [_ok_root("resolve:test", {live_mov})]
    result = mark(project, asset_dir, roots)
    by_path = {a.path: a for a in result.assets}
    live_stem = live_mov[:-4]
    assert by_path[live_stem + "_props.json"].status == LIVE
    assert by_path[live_stem + "_reuse_key.txt"].status == LIVE
    orphan_mov = os.path.join(asset_dir, _mov_names()[0])
    assert by_path[orphan_mov[:-4] + "_props.json"].status == ORPHAN
    lone = os.path.join(
        asset_dir, "sub_tl_akshita_9_90000-92000_dddddddd_props.json")
    assert by_path[lone].status == ORPHAN
    assert "mov" in by_path[lone].reason.lower()

    # The tight box sidecar too (2026-09-10: Reel 26's thirteen live
    # `_box.json` sidecars were quarantined as `unknown`, and the next
    # build re-rendered every caption).
    live_box = live_mov[:-4] + "_box.json"
    _write(live_box, size=100)
    orphan_mov = os.path.join(asset_dir, _mov_names()[0])
    orphan_box = orphan_mov[:-4] + "_box.json"
    _write(orphan_box, size=100)
    lone_box = os.path.join(
        asset_dir, "sub_tl_akshita_9_90000-92000_dddddddd_box.json")
    _write(lone_box, size=100)
    roots = [_ok_root("resolve:test", {live_mov})]
    result = mark(project, asset_dir, roots)
    by_path = {a.path: a for a in result.assets}
    assert by_path[live_box].status == LIVE
    assert by_path[live_box].kind == "sibling"
    assert by_path[orphan_box].status == ORPHAN
    assert by_path[lone_box].status == ORPHAN
    assert by_path[lone_box].kind == "lone-sibling"


def test_reachability_beats_name_and_age(tmp_path):
    """An old, oddly-named asset in the reference set stays LIVE.

    This pins the design insight: no collector by name, slug or age.
    The asset below looks stale by every heuristic and is placed on a
    timeline, so it lives.
    """
    project = str(tmp_path)
    asset_dir = _asset_dir(project)
    old = _write(os.path.join(
        asset_dir, "sub_reel-23_keyword-stuffing-is-hurting-your-ai-visi"
        "_akshita_3_460413-461208_7c2c1df7.mov"), size=500)
    ancient = os.path.getmtime(old) - 90 * 86400
    os.utime(old, (ancient, ancient))
    roots = [_ok_root("resolve:test", {old})]
    result = mark(project, asset_dir, roots)
    assert {a.path: a for a in result.assets}[old].status == LIVE


def test_mark_is_read_only(tmp_path):
    """The mark never writes into the asset directory: safe any time."""
    project = str(tmp_path)
    asset_dir = _populate(_asset_dir(project))
    before = {}
    for dirpath, _dirnames, filenames in os.walk(asset_dir):
        for name in filenames:
            path = os.path.join(dirpath, name)
            before[path] = (os.path.getsize(path),
                            os.stat(path).st_mtime_ns)
    roots = [_ok_root("resolve:test", set())]
    mark(project, asset_dir, roots)
    after = {}
    for dirpath, _dirnames, filenames in os.walk(asset_dir):
        for name in filenames:
            path = os.path.join(dirpath, name)
            after[path] = (os.path.getsize(path),
                           os.stat(path).st_mtime_ns)
    assert before == after


def test_stale_mark_refuses_sweep(tmp_path):
    """A file landing after the mark makes the mark stale: refuse."""
    project = str(tmp_path)
    asset_dir = _populate(_asset_dir(project))
    roots = [_ok_root("resolve:test", set())]
    result = mark(project, asset_dir, roots)
    mark_path = os.path.join(project, "mark.json")
    result.write_json(mark_path)
    _write(os.path.join(asset_dir, "sub_tl_new_1_1-2_eeeeeeee.mov"))
    with pytest.raises(SweepRefused) as refused:
        sweep(mark_path, project_folder=project)
    assert "stale" in str(refused.value).lower()


# ------------------------------------------------------------ the sweep


def test_sweep_moves_to_quarantine_and_manifest_comes_first(tmp_path):
    """Move, never delete: the full manifest is on disk before anything
    moves, and every moved path - small records included - is in it."""
    project = str(tmp_path)
    asset_dir = _populate(_asset_dir(project))
    live_mov = os.path.join(asset_dir, _mov_names()[2])
    roots = [_ok_root("resolve:test", {live_mov})]
    result = mark(project, asset_dir, roots)
    mark_path = os.path.join(project, "mark.json")
    result.write_json(mark_path)
    record = sweep(mark_path, project_folder=project, fresh_roots=roots)
    assert os.path.isfile(record["manifest_path"])
    manifest = open(record["manifest_path"], encoding="utf-8").read()
    # Every moved path is named in full - no size-filtered listing.
    for path in record["moved"]:
        assert path in manifest
    # Small records are listed too: the manifest is the only way back.
    assert "_reuse_key.txt" in manifest
    assert "_props.json" in manifest
    # The live asset and its siblings never moved.
    assert os.path.isfile(live_mov)
    assert os.path.isfile(live_mov[:-4] + "_props.json")
    # The orphans are in quarantine, not deleted: bytes accounted.
    assert record["bytes_reclaimed"] == sum(
        a.size_bytes for a in result.assets if a.status == ORPHAN)
    for path in record["moved"]:
        assert not os.path.exists(path), path
    quarantine_hits = []
    for dirpath, _dirnames, filenames in os.walk(record["quarantine_dir"]):
        quarantine_hits.extend(filenames)
    assert len(quarantine_hits) == len(record["moved"])


# ---------------------------------------------------- pipeline roots


def test_pipeline_roots_read_step_records(tmp_path):
    """The pipeline's own records are a root: what the current step
    output and manifest name is reachable even with no Resolve."""
    project = str(tmp_path)
    asset_dir = _populate(_asset_dir(project))
    overlay = os.path.join(asset_dir, _mov_names()[0])
    step_dir = os.path.join(
        project, "pipeline_output", "steps", "4_05_render_subtitles")
    os.makedirs(step_dir, exist_ok=True)
    with open(os.path.join(step_dir, "output.json"), "w",
              encoding="utf-8") as handle:
        json.dump({"subtitle_overlay": {"segments": [
            {"overlay_path": overlay},
        ]}}, handle)
    roots = collect_pipeline_roots(project)
    assert any(overlay in r.paths for r in roots if r.status == "ok")
    result = mark(project, asset_dir, roots)
    by_path = {a.path: a for a in result.assets}
    assert by_path[overlay].status == LIVE
    assert by_path[overlay].saved_by.startswith("pipeline:")


# ------------------------------------------------------ the retention


def test_a_rerender_orphans_only_a_generation_with_different_pixels(tmp_path):
    """Re-rendering a card orphans the generation it replaced, at once.

    Two renders of one card with genuinely different pixels (a text
    correction: same provenance stem, new content digest) leave the
    older mov superseded: named on the newer entry, without waiting
    for a sweep. Reachability still rules the mark - this only names
    the candidate at the moment it becomes one.
    """
    from library.steps.step_4_05_render_subtitles.step import (
        RENDERED,
        render_one_segment,
    )
    from tests.unit.captions.test_overlay_carriage import _props, _StubRenderer

    out_dir = str(tmp_path)
    first = render_one_segment(_props(), out_dir, "tl",
                               remotion_dir="/none",
                               renderer=_StubRenderer(),
                               overlay_geometry="full")
    assert first["provenance"] == RENDERED
    assert first["superseded"] == []

    changed = _props()
    changed["subtitles"] = [dict(changed["subtitles"][0],
                                 text="and so my very CHANGED")]
    second = render_one_segment(changed, out_dir, "tl",
                                remotion_dir="/none",
                                renderer=_StubRenderer(),
                                overlay_geometry="full")
    assert second["provenance"] == RENDERED
    assert second["overlay_path"] != first["overlay_path"]
    assert second["superseded"] == [first["overlay_path"]]
    # The old file is still on disk: orphaned, not deleted.
    assert os.path.isfile(first["overlay_path"])

    # A source shift inside the millisecond the filename carries changes
    # no pixel: same file, overwritten, nothing superseded.
    out_dir = str(tmp_path / "subpixel")
    os.makedirs(out_dir)
    first = render_one_segment(_props(), out_dir, "tl",
                               remotion_dir="/none",
                               renderer=_StubRenderer(),
                               overlay_geometry="full")

    changed = _props()
    changed["_source_start"] = 10.0004  # same ms token, same pixels
    second = render_one_segment(changed, out_dir, "tl",
                                remotion_dir="/none",
                                renderer=_StubRenderer(),
                                overlay_geometry="full")
    assert second["provenance"] == RENDERED
    assert second["overlay_path"] == first["overlay_path"]
    assert second["superseded"] == []


# --------------------------------------------- the render ledger


def _recorded_ids(asset_dir):
    with open(ledger_path_for(asset_dir), encoding="utf-8") as handle:
        data = json.load(handle)
    return [s["segment_id"]
            for s in data["subtitle_overlay"]["segments"]]


def test_render_one_segment_records_the_ledger(tmp_path):
    """A render outside any pipeline run still vouches for its pixels.

    The whole chain in one test: render one card straight into a
    project's step directory, and the pipeline root resolves it with no
    Resolve anywhere near it.
    """
    from library.steps.step_4_05_render_subtitles.step import (
        RENDERED,
        render_one_segment,
    )
    from tests.unit.captions.test_overlay_carriage import _props, _StubRenderer

    project = str(tmp_path)
    asset_dir = _asset_dir(project)
    os.makedirs(asset_dir, exist_ok=True)
    produced = render_one_segment(_props(), asset_dir, "tl",
                                   remotion_dir="/none",
                                   renderer=_StubRenderer(),
                                   overlay_geometry="full")
    assert produced["provenance"] == RENDERED
    ledger = ledger_path_for(asset_dir)
    assert os.path.isfile(ledger)
    assert produced["segment_id"] in _recorded_ids(asset_dir)
    roots = collect_pipeline_roots(project)
    render_root = next(r for r in roots
                       if r.name == "pipeline:render_subtitles")
    assert render_root.status == "ok"
    assert produced["overlay_path"] in render_root.paths
    result = mark(project, asset_dir, roots)
    by_path = {a.path: a for a in result.assets}
    assert by_path[produced["overlay_path"]].status == LIVE
    assert by_path[produced["overlay_path"]].saved_by == \
        "pipeline:render_subtitles"


def test_ledger_merge_never_narrows_and_drops_only_the_superseded(tmp_path):
    """One step directory holds several timelines' batches, and a pass
    covers one plan: recording batch B must not unprotect batch A."""
    project = str(tmp_path)
    asset_dir = _asset_dir(project)
    first = os.path.join(asset_dir, "sub_tl_a_1_1-2_aaaaaaaa.mov")
    second = os.path.join(asset_dir, "sub_tl_b_1_1-2_bbbbbbbb.mov")
    _write(first)
    _write(second)
    record_rendered_segments(asset_dir, [{
        "segment_id": "sub_tl_a_1_1-2_aaaaaaaa",
        "overlay_path": first, "provenance": "rendered",
        "superseded": []}])
    record_rendered_segments(asset_dir, [{
        "segment_id": "sub_tl_b_1_1-2_bbbbbbbb",
        "overlay_path": second, "provenance": "rendered",
        "superseded": []}])
    assert sorted(_recorded_ids(asset_dir)) == sorted(
        ["sub_tl_a_1_1-2_aaaaaaaa", "sub_tl_b_1_1-2_bbbbbbbb"])

    # A re-render unpins only the generation it replaced: the old file
    # becomes an orphan candidate naming its replacement.
    project = str(tmp_path / "second")
    asset_dir = _asset_dir(project)
    old = _write(os.path.join(asset_dir, "sub_tl_a_1_1-2_aaaaaaaa.mov"))
    new = _write(os.path.join(asset_dir, "sub_tl_a_1_1-2_bbbbbbbb.mov"))
    record_rendered_segments(asset_dir, [{
        "segment_id": "sub_tl_a_1_1-2_aaaaaaaa",
        "overlay_path": old, "provenance": "rendered",
        "superseded": []}])
    record_rendered_segments(asset_dir, [{
        "segment_id": "sub_tl_a_1_1-2_bbbbbbbb",
        "overlay_path": new, "provenance": "rendered",
        "superseded": [old]}])
    assert _recorded_ids(asset_dir) == ["sub_tl_a_1_1-2_bbbbbbbb"]
    roots = collect_pipeline_roots(project)
    result = mark(project, asset_dir, roots)
    by_path = {a.path: a for a in result.assets}
    assert by_path[new].status == LIVE
    assert by_path[old].status == ORPHAN
    assert by_path[old].superseded_by == new


def test_corrupt_ledger_reads_unreadable_and_refuses_sweep(tmp_path):
    """The failure direction: a record that could not be written (here
    a torn write, simulated as garbage bytes) reads UNREADABLE - never
    as an empty root - and the sweep moves nothing."""
    project = str(tmp_path)
    asset_dir = _populate(_asset_dir(project))
    ledger = ledger_path_for(asset_dir)
    record_rendered_segments(asset_dir, [{
        "segment_id": "sub_tl_a_1_1-2_aaaaaaaa",
        "overlay_path": os.path.join(asset_dir, _mov_names()[0]),
        "provenance": "rendered", "superseded": []}])
    with open(ledger, "wb") as handle:
        handle.write(b"{torn write, not json")
    roots = collect_pipeline_roots(project)
    render_root = next(r for r in roots
                       if r.name == "pipeline:render_subtitles")
    assert render_root.status == "unreadable"
    assert render_root.paths == set()
    result = mark(project, asset_dir, roots)
    mark_path = os.path.join(project, "mark.json")
    result.write_json(mark_path)
    with pytest.raises(SweepRefused) as refused:
        sweep(mark_path, project_folder=project, fresh_roots=roots)
    assert "unreadable" in str(refused.value).lower()
    assert sorted(os.listdir(asset_dir)) == sorted(
        _mov_names()
        + [n[:-4] + "_props.json" for n in _mov_names()]
        + [n[:-4] + "_reuse_key.txt" for n in _mov_names()]
        + ["sub_tl_akshita_9_90000-92000_dddddddd_props.json",
            RENDER_LEDGER_NAME])


def test_ledger_refuses_to_overwrite_itself_corrupt(tmp_path):
    """Recording onto a corrupt ledger raises instead of converting an
    UNREADABLE root into a freshly valid one naming only the latest
    pass - the overwrite that would silently unprotect everything the
    old record vouched for."""
    project = str(tmp_path)
    asset_dir = _asset_dir(project)
    os.makedirs(asset_dir, exist_ok=True)
    ledger = ledger_path_for(asset_dir)
    with open(ledger, "w", encoding="utf-8") as handle:
        handle.write("{torn write, not json")
    with pytest.raises(LedgerUnreadable):
        record_rendered_segments(asset_dir, [{
            "segment_id": "sub_tl_a_1_1-2_bbbbbbbb",
            "overlay_path": os.path.join(asset_dir, "sub_tl_new.mov"),
            "provenance": "rendered", "superseded": []}])
    assert open(ledger, encoding="utf-8").read() == \
        "{torn write, not json"


def test_reconcile_adopts_step_signature_outputs(tmp_path):
    """Pre-ledger renders are adopted from the render path's own write
    signature - a mov with a parseable props sibling. A mov without one
    is left for the other roots to judge, and the pre-reconcile mark is
    embedded beside the entries so the adoption is auditable."""
    project = str(tmp_path)
    asset_dir = _asset_dir(project)
    signed = _write(os.path.join(asset_dir, "sub_tl_a_1_1-2_aaaaaaaa.mov"))
    with open(signed[:-4] + "_props.json", "w", encoding="utf-8") as handle:
        json.dump({"subtitles": []}, handle)
    unsigned = _write(os.path.join(
        asset_dir, "hand_placed_1_1-2_bbbbbbbb.mov"))
    adopted = reconcile_render_ledger(project)
    assert adopted["recorded"] == 1
    with open(adopted["ledger"], encoding="utf-8") as handle:
        data = json.load(handle)
    entries = data["subtitle_overlay"]["segments"]
    assert [e["overlay_path"] for e in entries] == [signed]
    assert entries[0]["reconciled"] is True
    assert entries[0]["segment_id"] == "sub_tl_a_1_1-2_aaaaaaaa"
    assert data["pre_reconcile_mark"]["roots"][
        "pipeline:render_subtitles"]["paths"] == 0
    roots = collect_pipeline_roots(project)
    result = mark(project, asset_dir, roots)
    by_path = {a.path: a for a in result.assets}
    assert by_path[signed].status == LIVE
    assert by_path[signed].saved_by == "pipeline:render_subtitles"
    assert by_path[unsigned].status == ORPHAN


def test_sweep_manifests_carry_their_area_and_never_share_a_path(tmp_path):
    """Two areas swept in the same second used to share one manifest
    filename - the motion-graphics record overwrote the subtitle one
    (measured 2026-09-10: 82 subtitle files survived only as loose
    files in quarantine).  The tag separates areas; the suffix
    separates repeats.  The clock is frozen so the shared stamp is
    certain, not likely."""
    import datetime as datetime_mod
    from unittest import mock

    frozen = datetime_mod.datetime(2026, 9, 10, 22, 16, 28,
                                   tzinfo=datetime_mod.timezone.utc)

    class _Frozen(datetime_mod.datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen

    project = str(tmp_path)
    asset_dir = _populate(_asset_dir(project))
    live_mov = os.path.join(asset_dir, _mov_names()[2])
    roots = [_ok_root("resolve:test", {live_mov})]
    with mock.patch("library.tools.caption_asset_gc.datetime", _Frozen):
        first_mark = mark(project, asset_dir, roots)
        first_path = os.path.join(project, "mark_a.json")
        first_mark.write_json(first_path)
        sub = sweep(first_path, project_folder=project,
                    fresh_roots=roots, manifest_tag="subtitle_segments")
        assert "subtitle_segments" in os.path.basename(
            sub["manifest_path"])
        assert os.path.isfile(sub["manifest_path"])

        other_mark = mark(project, asset_dir, roots)
        other_path = os.path.join(project, "mark_b.json")
        other_mark.write_json(other_path)
        mg = sweep(other_path, project_folder=project,
                   fresh_roots=roots,
                   manifest_tag="motion_graphics_segments")
        assert mg["manifest_path"] != sub["manifest_path"]
        assert os.path.isfile(sub["manifest_path"]), \
            "the second area's manifest must not overwrite the first's"

        _write(os.path.join(
            asset_dir,
            "sub_tl_akshita_9_90000-92000_eeeeeeee.mov"), size=500)
        repeat_mark = mark(project, asset_dir, roots)
        repeat_path = os.path.join(project, "mark_c.json")
        repeat_mark.write_json(repeat_path)
        repeat = sweep(repeat_path, project_folder=project,
                       fresh_roots=roots,
                       manifest_tag="subtitle_segments")
        assert repeat["manifest_path"] != sub["manifest_path"]
        assert repeat["manifest_path"].endswith("_2_manifest.md")
        assert os.path.isfile(sub["manifest_path"])
        with open(repeat["manifest_path"], encoding="utf-8") as handle:
            repeat_manifest = handle.read()
        for path in repeat["moved"]:
            assert path in repeat_manifest


# --------------------------------------------------------------------------
# From test_caption_pairs_dedupe.py
#
# Caption pairs that share pixels must share a filename (issue #915).
#
# Measured on geo-podcast: 145 pairs of caption movs holding byte-identical
# content under two content digests. Hash reconstruction proved the only
# drawing-side difference between each pair was `style.safeArea.bottom` -
# 331 (the engine's old row: profile 320 + lift 11) versus 540 (the
# project's declared caption row 1380) - rendered in two waves either side
# of the row declaration. The frame-relative insets move the probe's ink
# within the delivery frame, but the tight crop follows the ink, so two
# rows of one card cut byte-identical canvases. The digest hashed the
# insets anyway, so every row change re-rendered the whole directory
# beside itself: same content under two names, and deleting the copies
# leaves the producer producing them again.
#
# The fix has two halves, and this file pins both:
#
# - the tight filename is row-invariant (`_drawing_digest` drops
#   `style.safeArea` for the tight carrying only - the full carrying
#   keeps it, because there the row really moves pixels);
# - the box sidecar stamps the row it was measured for, and the reuse
#   path refuses a stamp that is missing or moved - because the unified
#   file is still placed per row, and a placement measured for the old
#   row reads back clean while drawing on the wrong one.
#
# Each test fails with the fix reverted: the digest and filename tests
# by producing two digests/files, the sidecar tests by restoring a
# placement they must refuse, the re-measure test by serving the stale
# file as REUSED.

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.steps.step_4_05_render_subtitles.step import (  # noqa: E402
    RENDERED,
    REUSED,
    _drawing_digest,
)
from library.tools.tight_box import (  # noqa: E402
    TightBoxMismatch,
    restore_reused_placement,
)
from tests.unit.captions.test_overlay_carriage import (  # noqa: E402
    _props,
    _ServingRenderer,
    _small_frames_setup,
)

REMOTION = os.path.join(PROJECT_ROOT, "remotion-subtitles")

ROW_OLD = 331
"""The engine's row before the project declared its own: profile
bottom 320 + the old lift 11 (pre-#979)."""
ROW_NEW = 540
"""The project's declared caption row: delivery row 1380 on the
1080x1920 frame (1920 - 1380)."""


def _row_props(bottom: int) -> dict:
    """The same card on one caption row: everything identical except
    the frame-relative bottom inset that positions the probe's ink."""
    props = _props()
    props = copy.deepcopy(props)
    props["style"]["safeArea"] = {
        "top": 120, "right": 120, "bottom": bottom, "left": 90,
    }
    return props


def test_tight_digest_ignores_the_caption_row():
    """The pair-maker, at the unit level: one card on the old row and
    the new row digests identically for the tight carrying."""
    assert _drawing_digest(_row_props(ROW_OLD), "tight") == \
        _drawing_digest(_row_props(ROW_NEW), "tight")


def test_full_digest_still_sees_the_caption_row():
    """The other direction: a full-canvas render really moves pixels
    with the row, so unifying it too would serve wrong pixels as a
    hit. The tight exemption must not leak across carryings."""
    assert _drawing_digest(_row_props(ROW_OLD), "full") != \
        _drawing_digest(_row_props(ROW_NEW), "full")


def test_same_card_on_two_rows_renders_one_file(tmp_path):
    """End to end behind canned frames: the old-row render and the
    new-row render compute one overlay path, so the second overwrites
    rather than duplicating. Without the fix this leaves two
    frames-dirs holding identical pixels - the filed pair."""
    out = str(tmp_path)
    props, tight_canned, _box = _small_frames_setup(
        tmp_path, "pairs")
    first_props = _row_props(ROW_OLD)
    first_props["durationInFrames"] = \
        props["durationInFrames"]
    first_props["_source_out_frame"] = props["_source_out_frame"]
    second_props = _row_props(ROW_NEW)
    second_props["durationInFrames"] = \
        props["durationInFrames"]
    second_props["_source_out_frame"] = props["_source_out_frame"]

    first = render_one_segment(
        first_props, out, "tl", remotion_dir=REMOTION,
        renderer=_ServingRenderer(tight_canned),
        reuse=False, overlay_geometry="tight",
        overlay_container="frames")
    assert first["provenance"] == RENDERED
    second = render_one_segment(
        second_props, out, "tl", remotion_dir=REMOTION,
        renderer=_ServingRenderer(tight_canned),
        reuse=False, overlay_geometry="tight",
        overlay_container="frames")
    assert second["provenance"] == RENDERED
    assert second["segment_id"] == first["segment_id"], (
        "one card on two rows must compute one filename - two names "
        "for identical pixels is the filed duplication")
    dirs = sorted(n for n in os.listdir(out) if n.endswith("_frames"))
    assert len(dirs) == 1, (
        f"two rows rendered two files holding the same pixels: {dirs}")


def test_row_change_remeasures_instead_of_serving_stale(tmp_path):
    """The unified file is still placed per row: a build after the row
    moves must re-measure (RENDERED, same path), never pair back to
    the old row's placement as REUSED."""
    out = str(tmp_path)
    props, tight_canned, _box = _small_frames_setup(
        tmp_path, "rowmove")
    first_props = _row_props(ROW_OLD)
    first_props["durationInFrames"] = \
        props["durationInFrames"]
    first_props["_source_out_frame"] = props["_source_out_frame"]
    second_props = _row_props(ROW_NEW)
    second_props["durationInFrames"] = \
        props["durationInFrames"]
    second_props["_source_out_frame"] = props["_source_out_frame"]

    first = render_one_segment(
        first_props, out, "tl", remotion_dir=REMOTION,
        renderer=_ServingRenderer(tight_canned),
        reuse=True, overlay_geometry="tight",
        overlay_container="frames")
    assert first["provenance"] == RENDERED
    engine = _ServingRenderer(tight_canned)
    second = render_one_segment(
        second_props, out, "tl", remotion_dir=REMOTION,
        renderer=engine, reuse=True, overlay_geometry="tight",
        overlay_container="frames")
    assert second["segment_id"] == first["segment_id"]
    assert second["provenance"] == RENDERED, (
        "the row moved since this file was placed: pairing back to it "
        "as REUSED would serve the old row's placement, which reads "
        "back clean and draws on the wrong row")
    assert engine.calls, "re-measuring must render, not restore"
    assert second["provenance"] != REUSED


def test_sidecar_from_a_moved_row_is_refused():
    """The guard the test above leans on, at the unit level: a sidecar
    stamped for the old row does not restore under the new one."""
    from library.tools.overlay_mode import OVERLAY_CARRIAGE

    props = _row_props(ROW_OLD)
    sidecar = {
        "width": 840,
        "height": 480,
        "placement": {"scaling": 1, "pan": 0.0, "tilt": -850.0},
        "carriage": OVERLAY_CARRIAGE,
        "safe_area": {"top": 120, "right": 120,
                      "bottom": ROW_OLD, "left": 90},
        "union": {"x0": 126, "y0": 1416, "x1": 937, "y1": 1596},
    }
    try:
        restore_reused_placement(sidecar, _row_props(ROW_NEW),
                                 (1080, 1920))
    except TightBoxMismatch as exc:
        assert "safeArea" in str(exc)
    else:
        raise AssertionError(
            "a placement measured for the old row restored under the "
            "new one - the unified filename would serve stale rows")


# --------------------------------------------------------------------------
# From test_caption_persistent_renderer.py
#
# The caption step can use the bundle-once renderer through its own seam.
#
# `library/tools/remotion_batch.py` ships `PersistentRenderer` - one node
# process, one bundle, many cards - with zero callers, while step 4.05's
# `SubprocessRenderer` pays one `npx remotion render` per card and names
# this exact swap as its intended design. This file pins the wiring:
#
# * the step offers the persistent renderer BESIDE the subprocess one,
#   and the default is the persistent one (captain's ruling, 2026-09-09);
#   the per-card subprocess path is the loud startup fallback;
# * the persistent renderer builds LAZILY - a pass that draws nothing
#   pays nothing;
# * the two failure kinds stay distinct: a bad card returns
#   `(False, error)` and the renderer stays up, while a dead renderer
#   RAISES and the pass stops rather than marching every remaining card
#   into a closed pipe and reporting each as failed.

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
REMOTION_2 = os.path.join(REPO, "remotion-subtitles")


def _load_405():
    """4.05 imports a sibling by bare name; the step directory is owned
    by tests/conftest.py, so this loader adds nothing to sys.path."""
    step_dir = os.path.join(REPO, "library/steps/step_4_05_render_subtitles")
    spec = importlib.util.spec_from_file_location(
        "s405_persistent_under_test", os.path.join(step_dir, "step.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


r405 = _load_405()


def _block(position, tl_start, duration, words, clip="clip_001", src=0.0):
    stamps = [{"word": w,
               "source_start": round(src + i * duration / len(words), 3),
               "source_end": round(src + (i + 0.8) * duration / len(words), 3)}
              for i, w in enumerate(words)]
    return {
        "position": position, "block_type": "speech", "clip_id": clip,
        "source_start": src, "source_end": round(src + duration, 3),
        "timeline_start": tl_start,
        "timeline_end": round(tl_start + duration, 3),
        "duration_seconds": duration,
        "word_timestamps": stamps, "alignment_method": "whisperx",
        "content": {"text": " ".join(words), "word_timestamps": stamps},
    }


def _spine_and_plan():
    from library.steps.step_4_01_plan_subtitles.step import (
        generate_subtitles,
    )
    spine = {"structure": [
        _block(1, 0.0, 3.0, ["alpha", "bravo"], src=10.0),
        _block(2, 3.0, 3.0, ["charlie", "delta"], src=20.0),
    ], "frame_rate": 30.0}
    return spine, generate_subtitles(spine)["subtitle_plan"]


class _NoPixelQA:
    """The wiring tests use stub renderers writing bytes, not ProRes, so
    the real pixel QA would correctly refuse them. That is the QA doing
    its job on a stub and is not what is pinned here."""

    def __init__(self, monkeypatch):
        import types
        module = types.ModuleType("tools.qa.subtitle_qa")
        module.run_subtitle_qa = lambda *a, **k: None
        monkeypatch.setitem(sys.modules, "tools.qa.subtitle_qa", module)


# ── The default is the bundle-once renderer ───────────────────────────

def test_the_default_renderer_is_now_the_persistent_one(monkeypatch,
                                                       tmp_path):
    """The captain's ruling, 2026-09-09: the persistent renderer is the
    default and the per-card subprocess path is the fallback. The
    previous pin on `"subprocess"` turned red on that word, as it said
    it would - this is its replacement, pinning the new default both
    as the signature value and as the renderer actually built."""
    import inspect
    _NoPixelQA(monkeypatch)
    spine, plan = _spine_and_plan()
    params = inspect.signature(r405.render_subtitle_overlays).parameters
    assert params["renderer_kind"].default == "persistent"

    built = []

    class _Stub:
        def __init__(self, remotion_dir):
            built.append(remotion_dir)

        def render(self, props_path, overlay_path, sequence=False):
            with open(overlay_path, "wb") as handle:
                handle.write(b"pixels")
            return True, ""

        def close(self):
            pass

    monkeypatch.setattr(r405, "PersistentCaptionRenderer", _Stub)
    # Full-canvas carrying: the stubs write undecodable bytes, and the
    # tight default would try to DECODE the probe off them. What is
    # pinned here is WHICH renderer is built, not the tight path - so
    # the carrying is explicit rather than left to the project default.
    out = r405.render_subtitle_overlays(
        plan, spine, project_folder=str(tmp_path),
        remotion_dir=REMOTION_2,
        overlay_geometry="full", overlay_container="video")
    assert len(built) == 1 and built[0] == REMOTION_2
    assert out["subtitle_overlay"]["renderer"] == "persistent"


def test_the_subprocess_path_stays_selectable(monkeypatch, tmp_path):
    """The old default is the explicit fallback: asking for it by name
    still builds exactly one per-card renderer."""
    _NoPixelQA(monkeypatch)
    spine, plan = _spine_and_plan()
    built = []

    class _Stub:
        def __init__(self, remotion_dir):
            built.append(remotion_dir)

        def render(self, props_path, overlay_path, sequence=False):
            with open(overlay_path, "wb") as handle:
                handle.write(b"pixels")
            return True, ""

        def close(self):
            pass

    monkeypatch.setattr(r405, "SubprocessRenderer", _Stub)
    # Full-canvas carrying, as above: the stub draws bytes, not video,
    # and what is pinned here is that the old path stays selectable.
    out = r405.render_subtitle_overlays(
        plan, spine, project_folder=str(tmp_path),
        remotion_dir=REMOTION_2, renderer_kind="subprocess",
        overlay_geometry="full", overlay_container="video")
    assert len(built) == 1 and built[0] == REMOTION_2
    assert out["subtitle_overlay"]["renderer"] == "subprocess"
    assert "renderer_fallback" not in out["subtitle_overlay"]


def test_an_unknown_renderer_kind_is_refused(tmp_path):
    """A misspelled kind must fail loudly, never fall back to a
    renderer the caller did not ask for - a silent fallback is the
    vacuous-gate shape this repository keeps removing."""
    spine, plan = _spine_and_plan()
    with pytest.raises(ValueError, match="renderer_kind"):
        r405.render_subtitle_overlays(plan, spine,
                                      project_folder=str(tmp_path),
                                      remotion_dir=REMOTION_2,
                                      renderer_kind="turbo")


# ── Lazy: a pass that draws nothing pays nothing ──────────────────────

def _no_spawn_recorder(calls):
    """The single spawn path, refused. `PersistentRenderer.start()` is
    the only caller of `Popen`, and only `render()` calls `start()`, so
    a laziness pin belongs here - not on a machine-global `pgrep`
    count, which a concurrent probe in another lane flips with nothing
    started here (measured 2026-09-14: the `pgrep -f render-batch.mjs`
    form of this pin failed the full-suite gate and fails ~5/25 beside
    tight probe loops). An eager adapter still turns these red: the
    first spawn raises out of construction or out of the block."""

    def _no_spawn(*args, **kwargs):
        calls.append(args)
        raise AssertionError(
            "a caption renderer spawned a child before any card asked "
            "to be drawn - the bundle is paid lazily, on the first "
            "render, not at construction")

    return _no_spawn


def test_the_persistent_renderer_builds_lazily(monkeypatch):
    """Construction must not spawn anything. `render_one_segment` can
    return without rendering - a region-scoped pass skips most cards -
    so an eager bundle would pay its whole cost to draw one card and
    make that path SLOWER than what it replaced."""
    import subprocess as sp

    calls = []
    monkeypatch.setattr(sp, "Popen", _no_spawn_recorder(calls))
    engine = r405.PersistentCaptionRenderer(REMOTION_2)
    assert engine._inner._proc is None, (
        "constructing the renderer started work")
    assert calls == [], (
        f"constructing the renderer spawned a child: {calls}")
    engine.close()  # never started: must be a no-op, never a raise
    assert calls == [], (
        f"closing an unstarted renderer spawned a child: {calls}")


# ── The two failure kinds are not the same ────────────────────────────

class _Inner:
    """Stands in for `PersistentRenderer` behind the adapter."""

    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls = 0
        self.closed = 0

    def render(self, props_path, overlay_path, sequence=False):
        self.calls += 1
        return self.behaviour(props_path, overlay_path)

    def close(self):
        self.closed += 1


def test_a_card_failure_is_returned_and_the_renderer_stays_up(tmp_path):
    """One bad card is `(False, error)`, not an exception - the bundle
    is the expensive thing and one bad card must not cost it."""
    from library.tools.remotion_batch import RendererUnavailable
    inner = _Inner(lambda p, o: (False, "bad font"))
    engine = r405.PersistentCaptionRenderer.__new__(
        r405.PersistentCaptionRenderer)
    engine._inner = inner
    props = tmp_path / "p.json"
    props.write_text("{}")
    ok, error = engine.render(str(props), str(tmp_path / "o.mov"))
    assert ok is False and "bad font" in error
    assert inner.calls == 1
    # And the renderer is still usable: the next card goes out too.
    ok2, _ = engine.render(str(props), str(tmp_path / "o2.mov"))
    assert ok2 is False
    assert inner.calls == 2
    assert not isinstance(ok2, type(RendererUnavailable("x")))


def test_a_dead_renderer_raises_rather_than_returning_failure(tmp_path):
    """Conflating these marches 762 more cards into a closed pipe and
    reports 762 failures instead of one fault."""
    from library.tools.remotion_batch import RendererUnavailable
    inner = _Inner(lambda p, o: (_ for _ in ()).throw(
        RendererUnavailable("the renderer process exited (code -9)")))
    engine = r405.PersistentCaptionRenderer.__new__(
        r405.PersistentCaptionRenderer)
    engine._inner = inner
    props = tmp_path / "p.json"
    props.write_text("{}")
    with pytest.raises(RendererUnavailable):
        engine.render(str(props), str(tmp_path / "o.mov"))


def test_the_pass_stops_on_a_dead_renderer_and_reports_one_fault(
        monkeypatch, tmp_path):
    """The orchestrator keeps the distinction: on `RendererUnavailable`
    it refuses the pass ONCE, carrying the cards rendered so far - it
    does not record a FAILED segment per remaining card."""
    _NoPixelQA(monkeypatch)
    from library.tools.remotion_batch import RendererUnavailable
    spine, plan = _spine_and_plan()

    calls = []

    class _Dying:
        def render(self, props_path, overlay_path, sequence=False):
            calls.append(overlay_path)
            raise RendererUnavailable("the renderer process exited")

        def close(self):
            calls.append("close")

    engine = _Dying()
    with pytest.raises(r405.SubtitleRenderRefused) as caught:
        r405.render_subtitle_overlays(plan, spine,
                                      project_folder=str(tmp_path),
                                      remotion_dir=REMOTION_2,
                                      renderer=engine)
    assert len(calls) == 1, (
        f"the second card must never be attempted: {calls}")
    payload = caught.value.payload["subtitle_overlay"]
    assert payload["available"] is False
    assert "renderer" in payload["error"].lower()


# ── The startup fallback: loud, once, never per card ─────────────────

def test_a_renderer_that_cannot_start_falls_back_loudly(monkeypatch,
                                                       tmp_path, capsys):
    """The fallback fires at STARTUP, not per card: the persistent
    renderer raises `RendererUnavailable` before a single card is drawn
    (no node, no bundle, bundling outran its budget), and the pass
    continues on the per-card subprocess renderer.

    It MUST SAY SO LOUDLY: the step's own stderr carries a FALLBACK
    banner, and the emitted payload records `renderer_fallback` with
    the reason - a pass that silently ran the slow way while reporting
    success is exactly what the design refuses."""
    _NoPixelQA(monkeypatch)
    from library.tools.remotion_batch import RendererUnavailable
    spine, plan = _spine_and_plan()

    dead = []

    class _CannotStart:
        def __init__(self, remotion_dir):
            dead.append(remotion_dir)

        def render(self, props_path, overlay_path, sequence=False):
            raise RendererUnavailable(
                "could not start the renderer: node is not on PATH")

        def close(self):
            dead.append("closed")

    drawn = []

    class _Subprocess:
        def __init__(self, remotion_dir):
            drawn.append(remotion_dir)

        def render(self, props_path, overlay_path, sequence=False):
            with open(overlay_path, "wb") as handle:
                handle.write(b"pixels")
            return True, ""

        def close(self):
            pass

    monkeypatch.setattr(r405, "PersistentCaptionRenderer", _CannotStart)
    monkeypatch.setattr(r405, "SubprocessRenderer", _Subprocess)
    # Full-canvas carrying: both stand-ins draw bytes, not video, and
    # the tight default would try to decode a probe off them. The
    # fallback being pinned is startup selection, not the tight path.
    out = r405.render_subtitle_overlays(
        plan, spine, project_folder=str(tmp_path),
        remotion_dir=REMOTION_2,
        overlay_geometry="full", overlay_container="video")

    overlay = out["subtitle_overlay"]
    assert overlay["available"] is True
    assert overlay["renderer"] == "subprocess"
    fallback = overlay["renderer_fallback"]
    assert fallback["requested"] == "persistent"
    assert "node is not on PATH" in fallback["reason"]
    assert len(drawn) == 1 and drawn[0] == REMOTION_2
    assert "closed" in dead, "the dead renderer is closed, not leaked"

    announcement = capsys.readouterr().err
    assert "FALLBACK" in announcement
    assert "persistent" in announcement.lower()
    assert "node is not on PATH" in announcement


def test_a_renderer_that_dies_mid_run_still_stops_the_pass(monkeypatch,
                                                           tmp_path):
    """The startup fallback must not become a per-card fallback: the
    first card draws, the renderer dies on the second, and the pass
    REFUSES with one fault rather than degrading quietly onto the slow
    path for the remaining cards."""
    _NoPixelQA(monkeypatch)
    from library.tools.remotion_batch import RendererUnavailable
    spine, plan = _spine_and_plan()

    class _DiesAfterOne:
        def __init__(self, remotion_dir):
            self.calls = 0

        def render(self, props_path, overlay_path, sequence=False):
            self.calls += 1
            if self.calls > 1:
                raise RendererUnavailable(
                    "the renderer process exited (code -9)")
            with open(overlay_path, "wb") as handle:
                handle.write(b"pixels")
            return True, ""

        def close(self):
            pass

    class _SubprocessMustNotRun:
        def __init__(self, remotion_dir):
            raise AssertionError(
                "mid-run death must not fall back to subprocess")

        def render(self, props_path, overlay_path, sequence=False):
            raise AssertionError("unreachable")

        def close(self):
            pass

    monkeypatch.setattr(r405, "PersistentCaptionRenderer", _DiesAfterOne)
    monkeypatch.setattr(r405, "SubprocessRenderer", _SubprocessMustNotRun)
    with pytest.raises(r405.SubtitleRenderRefused) as caught:
        r405.render_subtitle_overlays(
            plan, spine, project_folder=str(tmp_path),
            remotion_dir=REMOTION_2)
    payload = caught.value.payload["subtitle_overlay"]
    assert payload["available"] is False
    assert "renderer" in payload["error"].lower()
    assert len(payload["segments"]) == 1, (
        "one card drawn, then the fault - no FAILED entry per card "
        "that never had a chance")


# ── The codec path carries ────────────────────────────────────────────

def test_the_serve_path_renders_prores_4444_like_the_cli_path():
    """Bite 3 from the brief: the current call passes `--codec prores
    --prores-profile 4444`. The persistent path must carry the same
    codec and profile, or the finding is that it cannot - not a quiet
    format change. Pinned off the entry point and the shared renderer it
    imports, because both batch and serve modes use the latter."""
    script = "\n".join(open(
        os.path.join(REMOTION_2, filename), encoding="utf-8").read()
        for filename in ("render-batch.mjs", "render-batch-core.mjs"))
    assert 'codec: "prores"' in script
    assert 'proResProfile: "4444"' in script


# ── The unit-level default: one shared bundle-once renderer ──────────
#
# The pass-level default above is not where real caption work happens:
# region redos, reel fixes and agent-driven renders reach
# `render_one_segment` directly, and that unit built a fresh
# per-card subprocess renderer per call - the shape that rendered 136
# cards at 18.2s each on 2026-09-11 with no banner and no record. These
# pin that the unit shares one bundle-once engine per process instead.

def _unit_props(block=1, text="alpha", frames=30):
    return {"_block_position": block, "_timeline_start": 0.0,
            "_timeline_end": 1.0, "_source_in_frame": 0,
            "_source_out_frame": frames, "_speaker": None,
            "_source_clip_id": "clip_001", "_source_start": 0.0,
            "_source_end": 1.0, "durationInFrames": frames, "fps": 30,
            "width": 1080, "height": 1920, "style": {},
            "subtitles": [{"text": text}]}


def _unit_dirs(tmp_path):
    rdir = str(tmp_path / "remotion")
    os.makedirs(rdir)
    out_dir = str(tmp_path / "out")
    os.makedirs(out_dir)
    return rdir, out_dir


# --------------------------------------------------------------------------
# From test_reel_caption_reuse.py
#
# Rebuilds reuse unchanged captions instead of re-rendering them.
#
# vep-caption-assets-cache-and-cleanup, Part 2. Every rebuild regenerated
# every caption clip and orphaned the previous set (gigabytes in one
# night). ``caption_content_hash`` cannot key a per-card cache: it digests
# the whole card list into ONE per-reel hash
# (``library/tools/plan_provenance.py``). The per-card identity is the
# segment name (speaker + timeline + source span,
# ``library/tools/subtitle_segment_id.py``) plus the recorded reuse key
# (props digest + renderer fingerprint, step 4.05) - and the reel caption
# path opts into it with ``reuse=True``.
#
# These tests drive the real ``reel_subtitle_segments`` with the real
# ``render_one_segment`` behind a stub renderer, so what is pinned is the
# wiring, not either half alone. Reverting the ``reuse=True`` opt-in turns
# the first two tests red; the third names the pairing the cache stands
# on.

REMOTION_3 = os.path.join(REPO, "remotion-subtitles")
"""The real composition tree. The reuse key fingerprints it; a directory
without one yields no fingerprint and reuse is refused - which would
make the tests below render twice and fail for the wrong reason."""

FPS = 24000 / 1001


class _Renderer:
    """A renderer that writes bytes and counts its own calls."""

    def __init__(self):
        self.calls = 0

    def render(self, props_path, overlay_path, sequence=False):
        self.calls += 1
        with open(overlay_path, "wb") as handle:
            handle.write(b"pixels")
        return True, ""


def _entries(texts=("alpha bravo", "charlie delta")):
    entries = []
    for i, text in enumerate(texts):
        start = 20.0 + i * 5.0
        entries.append({
            "timeline_start": start,
            "timeline_end": start + 2.5,
            "text": text,
            "spine_block_position": f"body_{i + 1}",
            "speaker": "Craig",
            "words": [],
        })
    return entries


@pytest.fixture
def reel_run(monkeypatch, tmp_path):
    """The real reel caption path, plan stubbed, render REAL.

    Returns ``(run, engine, seen)`` where ``run(entries)`` builds the
    reel's captions for the given plan entries, ``engine`` is the stub
    renderer counting real renders, and ``seen`` records the ``reuse``
    flag each render call carried.
    """
    engine = _Renderer()
    seen = []
    entries_holder = {"entries": _entries()}

    def fake_spine(moment, transcript, keep_ranges, lead_seconds=0.0,
                   project_folder=""):
        return {}

    class FakePlan:
        def run(self, spine, **kwargs):
            return {"subtitle_plan": {
                "subtitle_entries": entries_holder["entries"],
                "style": {"fontFamily": "Montserrat"},
            }}

    class FakeRender:
        def run(self, props, out_dir, name, progress="", reuse=False,
                overlay_geometry=None, overlay_container=None,
                project_folder="", draw_gain=None):
            seen.append(reuse)
            # These tests pin the reuse wiring, not the carrying: the
            # byte stub cannot feed the tight probe (it writes no
            # decodable video), so the geometry is held at full while
            # the default is tight. Geometry itself is pinned in
            # test_overlay_mode.py and test_subtitle_overlay_modes.py.
            return render_one_segment(
                props, out_dir, name, remotion_dir=REMOTION_3,
                progress=progress, reuse=reuse, renderer=engine,
                overlay_geometry="full",
                overlay_container=overlay_container,
                project_folder=project_folder)

    def fake_get(name):
        if name == "subtitles.plan":
            return FakePlan()
        if name == "subtitles.render_segment":
            return FakeRender()
        raise AssertionError(f"unexpected operation {name!r}")

    monkeypatch.setattr(reel_spine, "spine_for_reel", fake_spine)
    monkeypatch.setattr(operations, "get", fake_get)

    def run(entries=None):
        if entries is not None:
            entries_holder["entries"] = entries
        return reel_subtitle_segments(
            mock.MagicMock(), {"segments": []}, [(0.0, 60.0)],
            str(tmp_path), FPS, 1080, 1920, timeline_name="Reel 01")

    return run, engine, seen


def test_reel_rebuild_reuses_unchanged_captions(reel_run):
    """An identical rebuild renders nothing twice.

    Fails while the reel path calls the render operation without
    ``reuse=True``: the plain-run default re-renders, the second build
    reports RENDERED, and the engine is called for every segment again.
    """
    run, engine, seen = reel_run
    first = run()
    second = run()
    assert [s["provenance"] for s in first] == ["rendered", "rendered"]
    assert [s["provenance"] for s in second] == ["reused", "reused"]
    assert engine.calls == 2, (
        f"the rebuild must not have rendered: {engine.calls} renders "
        f"for 2 unchanged segments built twice")
    assert seen == [True] * 4


def test_reel_rebuild_rerenders_a_changed_caption_only(reel_run):
    """The cache is per-card: a text correction re-renders its own card.

    The segment name carries no caption content, so the corrected card
    keeps its filename - skipping on presence would serve the stale
    overlay. The reuse key carries the props digest, so only the changed
    card re-renders and its neighbour is still paired back to disk.
    """
    run, engine, _seen = reel_run
    run()
    calls_after_first = engine.calls
    second = run(_entries(texts=("alpha bravo", "charlie DELTA")))
    provenances = sorted(s["provenance"] for s in second)
    assert provenances == ["rendered", "reused"], (
        f"exactly the changed card re-renders; got {provenances}")
    assert engine.calls == calls_after_first + 1


def test_tight_reuse_without_sidecar_falls_through_to_measured(tmp_path):
    """A key hit with no box sidecar re-measures instead of crashing.

    Measured 2026-09-10: Reel 26 reuses a Reel 09 render (same words,
    same drawing digest) whose box sidecar predates the sidecar
    mechanism. The open failed BEFORE the `TightBoxMismatch` import
    inside the try ran, so the except naming it raised
    UnboundLocalError - the "falls through to a fresh measured
    render" path had never been exercised (every reuse test above
    holds the geometry at full, so the tight branch never runs).

    The stub renderer writes bytes no render can decode, so the
    fall-through honestly FAILS the segment here; in production the
    fresh render decodes and the segment ships tight. What is pinned
    is the fall-through itself: no exception escapes, and the entry
    says failed rather than reused.
    """
    from library.steps.step_4_05_render_subtitles import step as seg_step
    from library.tools.subtitle_segment_id import (
        segment_binding,
        segment_identifier,
    )

    engine = _Renderer()
    out_dir = str(tmp_path)
    props = {
        "durationInFrames": 60,
        "fps": 30,
        "width": 1080,
        "height": 1920,
        "style": {
            "fontFamily": "Montserrat",
            "fontSize": 58,
            "fontWeight": 800,
            "position": "bottom",
            "safeArea": {"top": 120, "right": 120,
                         "bottom": 320, "left": 90},
            "captionMaxWidth": 840,
        },
        "subtitles": [{"text": "alpha bravo"}],
        "fontFamily": "Montserrat",
        "_block_position": "body_1",
        "_timeline_start": 20.0,
        "_timeline_end": 22.5,
        "_speaker": "Craig",
        "_source_clip_id": "clip_001",
        "_source_start": 100.0,
        "_source_end": 102.5,
    }
    binding = segment_binding(
        timeline="Reel 01",
        speaker=props["_speaker"],
        block_position=props["_block_position"],
        source_clip_id=props["_source_clip_id"],
        source_start=props["_source_start"],
        source_end=props["_source_end"])
    name = segment_identifier(
        binding, seg_step._drawing_digest(props, "tight", "video"))
    key = seg_step._reuse_key(props, REMOTION_3, "tight", "video")
    assert key, "the real remotion tree must fingerprint for this test"
    with open(os.path.join(out_dir, f"{name}.mov"), "wb") as handle:
        handle.write(b"pixels")
    with open(os.path.join(out_dir, f"{name}_reuse_key.txt"), "w",
              encoding="utf-8") as handle:
        handle.write(key)
    assert not os.path.exists(
        os.path.join(out_dir, f"{name}_box.json")), (
        "the sidecar must be absent: its absence is the case under test")

    entry = render_one_segment(
        dict(props), out_dir, "Reel 01", remotion_dir=REMOTION_3,
        reuse=True, renderer=engine,
        overlay_geometry="tight", overlay_container="video",
        project_folder="")

    assert entry["provenance"] == "failed", (
        f"the stub probe cannot decode, so the fall-through must "
        f"report failed, not {entry['provenance']!r}")
    assert "UnboundLocalError" not in str(entry.get("failure", ""))
    assert engine.calls >= 1, (
        "the fall-through must attempt a fresh measured render")


# --------------------------------------------------------------------------
# From test_subtitle_segment_id.py
#
# A rendered subtitle segment's name BINDS it to what it captions.
#
# Identity is PROVENANCE keyed by content - `sub_<speaker>_<clip>_<span>_<digest>`:
# no timeline or block ordinal names a file, so variants captioning the same
# words share one render, and different pixels can never overwrite each other.
# History (the `sub_block_<n>` collision, the timeline discriminator):
# docs/evidence/subtitle_segment_id.md.

def _binding(**overrides):
    base = dict(
        timeline="Studio Chat - Synced",
        speaker="Akshita",
        block_position="body_1",
        source_clip_id="clip_003",
        source_start=131.42295,
        source_end=151.69320,
    )
    base.update(overrides)
    return segment_binding(**base)


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


def _name(binding=None, digest=DIGEST_A):
    return segment_identifier(_binding() if binding is None else binding,
                              digest)


# ── The collision the captain reported ───────────────────────────────

def test_two_placements_of_different_pixels_never_share_a_name():
    """A reel can never overwrite the master's caption again.

    Same source span, different words: the digest differs, so the
    filenames differ, so no render overwrites the other.  This is the
    ordinal bug staying dead under the new identity.
    """
    assert _name() != _name(digest=DIGEST_B)


def test_provenance_changes_the_name_but_placement_does_not():
    """No provenance component is decorative; no placement component is
    load-bearing.  Change speaker, clip or span, get a different stem;
    change timeline or block ordinal, get the same stem - a wider ordinal
    would have separated neither case that matters."""
    base = _binding()
    for key, value in (("speaker", "Craig"),
                       ("source_clip_id", "clip_004"),
                       ("source_start", 99.0),
                       ("source_end", 199.0)):
        other = _binding(**{key: value})
        assert provenance_stem(base) != provenance_stem(other), key
        assert _name(base) != _name(other), key
    for key, value in (("timeline", "Reel 02 - rivers"),
                       ("block_position", "body_2")):
        other = _binding(**{key: value})
        assert provenance_stem(base) == provenance_stem(other), key
        assert _name(base) == _name(other), key


# ── The timeline left the identity entirely ──────────────────────────

def test_no_timeline_names_a_file():
    """Three variant timelines captioning the same words compute the
    same filename.  That sameness IS the cross-variant sharing - not a
    collision - and it is what the old timeline discriminator split
    apart into three renders of identical pixels."""
    variants = [
        "Reel 09 - your-website-is-only-20-percent (rebuild staging)",
        "Reel 09 - your-website-is-only-20-percent (j-cut)",
        "Reel 09 - your-website-is-only-20-percent (reaction-cutaway)",
    ]
    names = {_name(_binding(timeline=v)) for v in variants}
    assert len(names) == 1, names
    for name in names:
        assert "reel" not in name
        assert "j-cut" not in name
        assert "staging" not in name


def test_the_name_carries_speaker_and_source_span_readably():
    name = _name()
    assert "akshita" in name
    assert "clip-003" in name
    assert "131423-151693" in name


def test_speaker_reader_handles_current_and_legacy_names():
    current = segment_identifier(
        _binding(source_clip_id="b191411a-d2bf-4549-a09b"), DIGEST_A)
    legacy = ("sub_reel-17_akshita_body0_3135634-3141184_"
              "f32a24c3.mov")

    assert speaker_slug_from_segment_id(f"/rendered/{current}.mov") == \
        "akshita"
    assert speaker_slug_from_segment_id(legacy) == "akshita"
    assert speaker_slug_from_segment_id("sub_nospeaker_clip_0-1000_abc") \
        is None


def test_a_digest_and_every_binding_key_are_required_never_defaulted():
    """A provenance stem alone names WHERE the speech came from but not
    WHICH pixels, so "" is refused as a digest; a missing key is refused
    by name rather than defaulted."""
    with pytest.raises(ValueError):
        segment_identifier(_binding(), "")
    incomplete = _binding()
    del incomplete["speaker"]
    with pytest.raises(ValueError, match="speaker"):
        segment_identifier(incomplete, DIGEST_A)


# ── Absence is recorded, never dropped ───────────────────────────────

def test_an_absent_component_is_named_not_omitted():
    """Two different absences must not both become the empty string."""
    no_speaker = _binding(speaker=None)
    no_clip = _binding(source_clip_id=None)
    assert "nospeaker" in _name(no_speaker)
    assert "noclip" in _name(no_clip)
    assert _name(no_speaker) != _name(no_clip)


def test_slug_names_the_absence_and_truncates_on_a_word_boundary():
    assert slug(None, "nospeaker") == "nospeaker"
    assert slug("   ", "nospeaker") == "nospeaker"
    assert slug("!!!", "nospeaker") == "nospeaker"
    assert slug("Akshita Rao", "nospeaker") == "akshita-rao"
    # A truncated slug breaks on a word boundary, never mid-word (Reel
    # 05's `invisible-o`, `envisio`, `goo`) ...
    assert slug("Reel 05 - the-audit-that-was-eye-opening",
                "notimeline") == "reel-05-the-audit-that-was-eye"
    # ... and one unbreakable word keeps its hard cut (uniqueness rests on
    # the digest, not the readable half).
    from library.tools.subtitle_segment_id import _SLUG_MAX
    assert slug("a" * (_SLUG_MAX + 8), "x") == "a" * _SLUG_MAX


# ── Stability, so a rebuild overwrites ITSELF ────────────────────────


# ── One filename, two pixels is refused ──────────────────────────────

def test_one_name_behind_two_content_keys_is_refused():
    """The guard the timeline discriminator used to be: a shared
    filename with two content keys means a drawing input escaped the
    digest, and the second render would overwrite the first with
    nothing downstream reading content to notice."""
    name = _name()
    with pytest.raises(SegmentNameCollision) as exc:
        assert_no_content_collision([
            (name, f"{DIGEST_A}+fp+carriage"),
            (name, f"{DIGEST_B}+fp+carriage"),
        ])
    assert name in str(exc.value)
    # The sharing the identity exists for: one name, one key, passes.
    assert_no_content_collision([(name, f"{DIGEST_A}+fp+carriage")] * 3)


# ── Which timeline a placing belongs to ──────────────────────────────

def test_timeline_scope_prefers_a_measurement_over_a_declaration():
    from types import SimpleNamespace
    config = SimpleNamespace(resolve=SimpleNamespace(timeline_name="Main Edit"))
    spine = {"derived_from": {"timeline": "Studio Chat - Synced"}}
    assert timeline_scope(spine) == "Studio Chat - Synced"
    assert timeline_scope({}, project_config=config) == "Main Edit"
    assert timeline_scope(spine, project_config=config) == "Studio Chat - Synced"
    assert timeline_scope({}, project_config=None) == ""  # never invented


def test_stable_prefix_is_provenance_not_pixels():
    """The re-renderable half of a subtitle id: speaker, clip, span.

    Pins key on this (`overlay_intent`), because a re-render changes
    the digest and nothing else. The 2026-09-13 wipe proved it: every
    one of the captain's caption pins died on the digest while its
    prefix was still live.
    """
    assert stable_prefix(
        "sub_craig_341446bc-389b-468c-9add_1853716-1855056_1f0a29bf"
    ) == "sub_craig_341446bc-389b-468c-9add_1853716-1855056"
    assert stable_prefix(
        "sub_craig_341446bc-389b-468c-9add_1853716-1855056_fdc48282"
    ) == "sub_craig_341446bc-389b-468c-9add_1853716-1855056"
    # Two spans off one clip stay two keys: re-keying a pin can never
    # bind the neighbour.
    assert (stable_prefix(
        "sub_craig_341446bc-389b-468c-9add_1853716-1855056_1f0a29bf")
        != stable_prefix(
            "sub_craig_341446bc-389b-468c-9add_1855196-1856821_6b66c72d"))
    # A motion-graphics name is ALL digest past the project, and a kind
    # default or a bare prefix is already a key: left whole.
    assert stable_prefix("mg_geo-podcast_622f69cb") == "mg_geo-podcast_622f69cb"
    assert stable_prefix("caption") == "caption"
    assert (stable_prefix("sub_craig_341446bc-389b-468c-9add_1853716-1855056")
            == "sub_craig_341446bc-389b-468c-9add_1853716-1855056")
    assert stable_prefix(None) == ""


# --------------------------------------------------------------------------
# From test_subtitle_ids_are_block_local.py
#
# A caption id is stable under every change outside its own block.
#
# The id used to be a single counter across the whole timeline, so it named
# the card's ordinal position in the finished video.  Measured on project
# 001: forcing one spine block to produce five more cards renumbered 13
# entries in blocks that had not changed.
#
# Nothing reads the id, so that was harmless - until a region-scoped
# re-plan has to PROVE it changed only the region it was given, which it
# does by comparing the entries either side of it.  Under a global counter
# that comparison reports churn that is not there, and a proof that cries
# wolf is worth no more than one that cannot fail (AGENTS.md 10.4).
#
# `library/steps/step_4_01_plan_subtitles/step.py`.

def _speech_block(position, timeline_start, source_start, words,
                  duration=None):
    """One spine block.

    `duration` is settable so a fixture can change how many words a block
    holds without changing how long it lasts - the words are spread
    evenly across the span either way.  That separation matters: a
    duration change legitimately shifts every later block, so a test
    about card count has to hold duration steady or it measures the
    wrong thing.
    """
    duration = round(len(words) * 0.5, 3) if duration is None else duration
    stride = duration / max(len(words), 1)
    timestamps = [
        {
            "word": word,
            "source_start": round(source_start + i * stride, 3),
            "source_end": round(source_start + i * stride + stride * 0.8, 3),
        }
        for i, word in enumerate(words)
    ]
    return {
        "position": position,
        "block_type": "hook" if position == "hook" else "speech",
        "clip_id": "clip_001",
        "source_start": source_start,
        "source_end": round(source_start + duration, 3),
        "timeline_start": timeline_start,
        "timeline_end": round(timeline_start + duration, 3),
        "word_timestamps": timestamps,
        "alignment_method": "whisperx",
        "content": {"text": " ".join(words), "word_timestamps": timestamps},
    }


_BLOCK_3_SECONDS = 8.0
"""Block 3's span, held constant across every fixture in this file."""


def _spine(third_block_words):
    blocks = [
        _speech_block("hook", 0.0, 0.5, ["alpha", "bravo", "charlie"]),
        _speech_block(2, 1.5, 20.0, ["delta", "echo", "foxtrot"]),
        _speech_block(3, 3.0, 40.0, third_block_words,
                      duration=_BLOCK_3_SECONDS),
    ]
    last = blocks[-1]
    blocks.append(_speech_block(4, last["timeline_end"], 60.0,
                                ["yankee", "zulu"]))
    # Lay the blocks end to end the way mesh_spine's post_bridge does.
    cursor = 0.0
    for block in blocks:
        duration = block["timeline_end"] - block["timeline_start"]
        block["timeline_start"] = round(cursor, 3)
        block["timeline_end"] = round(cursor + duration, 3)
        cursor += duration
    return {"structure": blocks, "frame_rate": 30.0}


def _plan(spine):
    return generate_subtitles(spine)["subtitle_plan"]["subtitle_entries"]


def test_an_id_names_its_block_and_its_place_within_it():
    entries = _plan(_spine(["golf", "hotel"]))
    for entry in entries:
        assert entry["id"].startswith(
            f"sub_{str(entry['spine_block_position']).lower()}_")
    by_block = {}
    for entry in entries:
        by_block.setdefault(entry["spine_block_position"], []).append(entry["id"])
    for position, ids in by_block.items():
        assert ids == [f"sub_{str(position).lower()}_{n:03d}"
                       for n in range(1, len(ids) + 1)], position


def test_ids_outside_a_changed_block_do_not_move():
    """The regression the global counter caused, asserted directly.

    The two spines hold block 3 to the same span and give it a different
    number of words inside it - what a re-index that hears more words in
    the same audio produces - so the block splits into a different number
    of cards while every block's timing stays put.  That isolates the
    thing under test: a change to card count must not reach another
    block.  Letting the duration move instead would legitimately shift
    every later block (`step_2_05_mesh_spine/post_bridge.py` lays them
    end to end) and the test would pass or fail for the wrong reason.
    """
    few = _spine(["golf", "hotel", "india", "juliet"])
    many = _spine([f"word{n}" for n in range(24)])
    assert ([b["timeline_start"] for b in few["structure"]]
            == [b["timeline_start"] for b in many["structure"]]), \
        "the fixture must hold every block's timing steady"

    short, long = _plan(few), _plan(many)
    assert len(long) > len(short), "the change must alter the card count"

    def outside(entries):
        return {e["id"]: (e["text"], e["timeline_start"])
                for e in entries if e["spine_block_position"] != 3}

    assert set(outside(short)) == set(outside(long))
    assert outside(short) == outside(long)


def test_the_hook_block_gets_a_named_id_rather_than_an_ordinal():
    entries = _plan(_spine(["golf", "hotel"]))
    hook_ids = [e["id"] for e in entries if e["spine_block_position"] == "hook"]
    assert hook_ids and all(i.startswith("sub_hook_") for i in hook_ids)


# --------------------------------------------------------------------------
# From test_render_subtitles_empty_plan_is_not_hollow.py
#
# An empty subtitle plan must not fail the run.
#
# A speechless cut has no captions: `plan_subtitles` writes an empty plan
# and `render_subtitles` has no cards to draw. The step's own contract
# says that is not a refusal ("An empty plan is NOT a refusal"). But the
# empty-plan return carried `available: False`, which the runner's hollow
# gate (`run_pipeline.check_output_is_real`) reads anywhere in a step's
# output as a failed run - so no speechless project could ever reach
# `compile_manifest`.
#
# The motion-graphics renderer keeps the same distinction the same way:
# a plan of none renders `segments: []` with no `available` key, and a
# test pins that shape. This test pins the subtitle half: the empty-plan
# value passes the hollow gate, while a genuine `available: false` (a
# renderer that cannot start) still fails it.

REPO_2 = Path(__file__).resolve().parents[3]
if str(REPO_2) not in sys.path:
    sys.path.insert(0, str(REPO_2))

from library.steps.step_4_05_render_subtitles.step import _empty_overlay


def _check_output_is_real():
    spec = importlib.util.spec_from_file_location(
        "_run_pipeline_hollow_test",
        REPO_2 / "library" / "processes" / "edit_video" / "run_pipeline.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.check_output_is_real


def test_empty_plan_carries_no_cards_and_no_available_flag():
    overlay = _empty_overlay("No subtitle entries found in subtitle plan")
    assert overlay["segments"] == []
    assert "available" not in overlay
    assert overlay["reason"] == "No subtitle entries found in subtitle plan"


def test_empty_plan_passes_the_hollow_gate():
    check = _check_output_is_real()
    output = {
        "subtitle_overlay": _empty_overlay("No subtitle entries found in subtitle plan")
    }
    assert check("render_subtitles", output) == [], (
        "a speechless cut's empty overlay must not stop the run"
    )


def test_a_real_unavailable_still_fails_the_gate():
    """The control: `available: false` keeps its meaning for a renderer
    that cannot start, so this test can fail and is not coverage theatre."""
    check = _check_output_is_real()
    problems = check(
        "render_subtitles",
        {
            "subtitle_overlay": {
                "available": False,
                "error": "Remotion project not found at remotion-subtitles/",
            }
        },
    )
    assert problems, "the gate must still catch a dead renderer"
    assert "available=false" in problems[0]


# --------------------------------------------------------------------------
# From test_reel_caption_path_has_no_worker_pool.py
#
# No worker pool fans out renderer subprocesses on the caption path.
#
# The defect this pins (issue #530)
# ---------------------------------
# `library/tools/reel_build.py` rendered caption overlays through
# `ThreadPoolExecutor(max_workers=8)`, where each worker ran a full
# `npx remotion render` subprocess bringing its own headless browser with
# its own internal concurrency. Measured on the captain's 10-core machine,
# 2026-09-05: 1-minute load 4.42 before, 18.12 twenty seconds in, 27.75
# peak during teardown after the run was killed.
#
# `8` was a literal encoding an assumption about a machine, never measured
# against one (the pipeline holds no hardcoded values). The fix removed
# the pool outright (890a61b: captions render sequentially through step
# 4.05's renderer seam) and then removed the deeper cost - one bundle and
# browser launch per card - with the shared batch renderer whose
# concurrency is DERIVED from the machine (`library/tools/remotion_batch.py`,
# pinned by `tests/unit/captions/test_subtitle_render.py`).
#
# What this guards, and what it does not
# ---------------------------------------
# This guards the exact site the issue named and the sibling render loops
# with the same shape: a pool literal reintroduced in any of them
# recreates the load spike whatever the batch module does, because each
# worker still carries a full renderer's fan-out. It does NOT guard
# `remotion_batch.py` itself - that module deliberately manages
# concurrency (frame and encoder bounds derived from free cores), owns no
# pool literal, and is pinned by its own tests. A legitimate future need
# for parallel rendering belongs there, behind a derived bound, not as a
# pool literal at a call site.

REPO_ROOT = Path(__file__).resolve().parents[3]

RENDER_PATH_FILES = (
    # The file the issue named (`reel_build.py:549`).
    "library/tools/reel_build.py",
    # Step 4.05 renders each card today, one subprocess at a time.
    "library/steps/step_4_05_render_subtitles/step.py",
    # Sibling per-card render loops with the same fan-out shape.
    "library/steps/step_4_06_render_motion_graphics/post_bridge.py",
    "library/tools/timed_text_render.py",
    "library/tools/full_frame_element.py",
    "library/tools/bookend_render.py",
)

FORBIDDEN = (
    "ThreadPoolExecutor",
    "ProcessPoolExecutor",
    "concurrent.futures",
    "multiprocessing.Pool",
    "max_workers",
)


def pool_literals_in(paths) -> dict:
    """Map each file holding a worker-pool literal to the literals found."""
    found = {}
    for rel in paths:
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        hits = sorted(token for token in FORBIDDEN if token in text)
        if hits:
            found[rel] = hits
    return found


def test_no_worker_pool_fans_out_renderer_subprocesses():
    found = pool_literals_in(RENDER_PATH_FILES)
    assert not found, (
        f"a worker-pool literal is back on the render path: {found}. "
        f"Issue #530 measured 8 pooled `npx remotion render` workers "
        f"taking a 10-core box from load 4.42 to 18.12. Parallel "
        f"rendering belongs in `library/tools/remotion_batch.py` behind "
        f"a machine-derived bound, never as a pool literal at a call "
        f"site."
    )


# --------------------------------------------------------------------------
# From test_reel_caption_approval_gate.py
#
# The approval gate on the caption render path.
#
# `reel_proposal.assert_approved` had zero call sites in any render path,
# so captions were rendered for reels whose rejection was already on
# disk (measured on geo-podcast: full caption sets for quality-bar
# rejected reels 2, 3, 4, 6, 8, 11, 14, 19 and 22). Step 4.05's
# `render_one_segment` refuses a timeline label naming a REJECTED reel
# BEFORE reuse, probe or render, reading the verdict LIVE off
# `reel_proposals_v2.json` on every call - the captain rules on reels
# while renders are in flight.

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.reel_proposal import (
    NotApproved,
    refuse_rejected_reel_timeline,
)


def _moment(number, approval, slug="some-slug", note=""):
    return {
        "number": number,
        "slug": slug,
        "reason": "why",
        "timeline_start": 10.0,
        "timeline_end": 60.0,
        "approval": approval,
        "approval_note": note,
    }


def _project_with_proposals(tmp_path, moments):
    project = tmp_path / "proj"
    review = project / "pipeline_output" / "review"
    review.mkdir(parents=True)
    (review / "reel_proposals_v2.json").write_text(json.dumps({
        "format": "reel_proposal/1",
        "moments": moments,
    }), encoding="utf-8")
    return str(project)


def _props_2():
    return {
        "_block_position": 1,
        "_timeline_start": 0.0,
        "_timeline_end": 2.0,
        "_source_in_frame": 0,
        "_source_out_frame": 60,
        "_speaker": None,
        "_source_clip_id": "clip_001",
        "_source_start": 10.0,
        "_source_end": 12.0,
        "durationInFrames": 60,
        "fps": 30,
        "width": 1080,
        "height": 1920,
        "style": {"fontFamily": "Montserrat"},
        "subtitles": [],
    }


class _StubRenderer:
    def __init__(self):
        self.calls = []

    def render(self, props_path, overlay_path, sequence=False):
        self.calls.append(overlay_path)
        with open(overlay_path, "wb") as handle:
            handle.write(b"pixels")
        return True, ""

    def close(self):
        pass


# ── The helper reads the verdict, live ───────────────────────────────

def test_rejected_label_raises(tmp_path):
    project = _project_with_proposals(tmp_path, [
        _moment(22, "rejected", slug="a-score-is-not-a-fix",
                note="nothing quotable"),
    ])
    with pytest.raises(NotApproved, match="REJECTED"):
        refuse_rejected_reel_timeline(
            "Reel 22 - a-score-is-not-a-fix (rebuild staging)", project)


@pytest.mark.parametrize("case", [
    "approved_and_proposed_labels",
    "reel_number_not_proposed",
])
def test_non_rejected_labels_proceed_without_reading(tmp_path, case):
    """B1 collapse: the four proceed-without-reading passes pin one
    property, so one parametrized test."""
    if case == "approved_and_proposed_labels":
        project = _project_with_proposals(tmp_path, [
            _moment(9, "approved"),
            _moment(2, "proposed"),
        ])
        refuse_rejected_reel_timeline("Reel 09 - whatever", project)
        refuse_rejected_reel_timeline("Reel 02 - whatever", project)
    elif case == "master_and_unnamed_labels":
        project = _project_with_proposals(tmp_path, [_moment(9, "approved")])
        refuse_rejected_reel_timeline("GEO Podcast - Synced", project)
        refuse_rejected_reel_timeline("", project)
        refuse_rejected_reel_timeline(None, project)
    elif case == "missing_proposals_file":
        project = str(tmp_path / "proj")
        os.makedirs(project, exist_ok=True)
        refuse_rejected_reel_timeline("Reel 22 - whatever", project)
    else:
        project = _project_with_proposals(tmp_path, [_moment(9, "approved")])
        refuse_rejected_reel_timeline("Reel 40 - never-proposed", project)


# ── The render path refuses before drawing ───────────────────────────

def test_render_one_segment_refuses_a_rejected_reel_before_rendering(
        tmp_path):
    project = _project_with_proposals(tmp_path, [
        _moment(22, "rejected", note="nothing quotable"),
    ])
    stub = _StubRenderer()
    with pytest.raises(NotApproved, match="REJECTED"):
        render_one_segment(
            _props_2(), str(tmp_path / "out"),
            "Reel 22 - a-score-is-not-a-fix (rebuild staging)",
            renderer=stub, project_folder=project)
    assert stub.calls == []


def test_render_one_segment_renders_an_approved_reel(tmp_path):
    project = _project_with_proposals(tmp_path, [_moment(9, "approved")])
    stub = _StubRenderer()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    out = render_one_segment(
        _props_2(), str(out_dir),
        "Reel 09 - your-website-is-only-20-percent (rebuild staging)",
        renderer=stub, project_folder=project,
        overlay_geometry="full")
    assert out["provenance"] == "rendered"
    assert stub.calls != []


# --------------------------------------------------------------------------
# From test_region_subtitle_path.py
#
# The region-scoped caption path, and the honesty of what it reports.
#
# Increment 5. The captain's worked example: *"Regenerate just this small
# segment of subtitles ... and splice the refreshed subtitles back in."*
#
# Most of what is asserted here is a REFUSAL, and every refusal is paired
# with the case that must still PASS - a guard that cannot pass is not a
# guard, and a check that only ever refuses is the other half of the
# vacuous-gate problem this repository keeps removing.
#
# The rows below map to the twelve mutations the design named. Each is
# written so that reverting the behaviour it guards turns it red; the
# comment on each says which mutation.

REMOTION_4 = os.path.join(REPO, "remotion-subtitles")
"""The real composition tree. The reuse key hashes it, so a directory
without one yields no fingerprint and reuse is correctly refused - which
is what `test_an_unavailable_renderer_fingerprint_refuses_reuse` asserts
deliberately, and what every OTHER reuse test must avoid tripping over
accidentally."""


def _load_405_2():
    """4.05 imports a sibling by bare name, as every step body does.

    The step directory is owned by tests/conftest.py, so this loader
    adds nothing to sys.path itself.
    """
    step_dir = os.path.join(REPO, "library/steps/step_4_05_render_subtitles")
    spec = importlib.util.spec_from_file_location(
        "s405_under_test", os.path.join(step_dir, "step.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


r405_2 = _load_405_2()


# ── Fixtures: a spine small enough to read, shaped like the contract ──


@pytest.fixture
def spine():
    return {"structure": [
        _block(1, 0.0, 3.0, ["alpha", "bravo", "charlie"], src=10.0),
        _block(2, 3.0, 3.0, ["delta", "echo", "foxtrot"], src=20.0),
        _block(3, 6.0, 3.0, ["golf", "hotel", "india"], src=30.0),
    ], "frame_rate": 30.0}


@pytest.fixture
def plan(spine):
    return generate_subtitles(spine)["subtitle_plan"]


# ── The splice is bounded, and says so from measurement ──────────────

def test_the_splice_leaves_every_other_entry_byte_identical(spine, plan):
    out = splice_region_plan(spine, plan, scope_mod.region("3.0-6.0"))
    assert out["splice"]["outside_unchanged"] is True
    assert (outside_region(plan["subtitle_entries"], [2])
            == outside_region(out["subtitle_plan"]["subtitle_entries"], [2]))


def test_outside_unchanged_is_measured_and_can_be_false():
    """MUTATION 1: report `outside_unchanged: True` without checking."""
    stored = [{"id": "a", "spine_block_position": 1, "timeline_start": 0.0},
              {"id": "b", "spine_block_position": 2, "timeline_start": 1.0}]
    tampered = [{"id": "a", "spine_block_position": 1, "timeline_start": 9.9},
                {"id": "c", "spine_block_position": 2, "timeline_start": 1.0}]
    assert splice_report(stored, tampered, [2])["outside_unchanged"] is False
    honest = splice_plan(stored, [{"id": "c", "spine_block_position": 2,
                                   "timeline_start": 1.0}], [2])
    assert splice_report(stored, honest, [2])["outside_unchanged"] is True


def test_a_region_touching_no_block_is_refused_not_silently_empty(spine, plan):
    """MUTATION 2: return an empty plan for a typo'd region."""
    with pytest.raises(ValueError) as exc:
        splice_region_plan(spine, plan, scope_mod.region("100.0-110.0"))
    assert "touches no spine block" in str(exc.value)


def test_a_fresh_plan_may_not_carry_a_block_outside_the_region():
    """MUTATION 3: let a splice write blocks it was not asked for."""
    stored = [{"id": "a", "spine_block_position": 1, "timeline_start": 0.0}]
    with pytest.raises(SpliceRefused) as exc:
        splice_plan(stored, [{"id": "x", "spine_block_position": 9,
                              "timeline_start": 5.0}], [1])
    assert "not in the region" in str(exc.value)


def test_a_region_whose_fresh_plan_is_empty_still_clears_its_block():
    """A re-index finding silence must REMOVE the captions, not keep them.

    MUTATION 4: infer the target blocks from the fresh entries instead of
    taking them explicitly - then an empty result silently keeps the old
    captions, which is the one case a caller cannot see.
    """
    stored = [{"id": "a", "spine_block_position": 1, "timeline_start": 0.0},
              {"id": "b", "spine_block_position": 2, "timeline_start": 1.0}]
    assert splice_plan(stored, [], [2]) == [stored[0]]


# ── The duration-preserving refusal ──────────────────────────────────

def test_a_duration_or_block_count_changing_splice_is_refused(spine):
    """MUTATION 5: drop the duration check.

    mesh_spine's post_bridge lays blocks end to end from a cumulative
    cursor, so a longer block moves every block after it.
    """
    stretched = [dict(b) for b in spine["structure"]]
    stretched[1] = {**stretched[1], "duration_seconds": 3.5}
    with pytest.raises(SpliceRefused) as exc:
        assert_durations_preserved(spine["structure"], stretched)
    assert "3.0s -> 3.5s" in str(exc.value)
    assert "every block after the change would move" in str(exc.value)

    # MUTATION 6: compare pairwise by index instead of by position. A
    # count change preserves total duration while renumbering every
    # position downstream - the join key for 4.01, 4.05 and 5.04.
    with pytest.raises(SpliceRefused) as exc:
        assert_durations_preserved(
            spine["structure"],
            spine["structure"] + [_block(99, 9.0, 1.0, ["extra"])])
    assert "would ADD a block" in str(exc.value)


# ── Three-valued provenance, both directions ─────────────────────────

class _Renderer_2:
    """A renderer that does what the test tells it to."""

    def __init__(self, ok=True, error=""):
        self.ok, self.error, self.calls = ok, error, 0

    def render(self, props_path, overlay_path, sequence=False):
        self.calls += 1
        if self.ok:
            with open(overlay_path, "wb") as handle:
                handle.write(b"pixels")
        return self.ok, self.error


def _props_3(block=1, text="alpha"):
    return {"_block_position": block, "_timeline_start": 0.0,
            "_timeline_end": 1.0, "_source_in_frame": 0,
            "_source_out_frame": 30, "_speaker": None,
            "_source_clip_id": "clip_001", "_source_start": 0.0,
            "_source_end": 1.0, "durationInFrames": 30, "fps": 30,
            "width": 1080, "height": 1920,
            "style": {"position": "bottom",
                      "safeArea": {"top": 120, "right": 120,
                                   "bottom": 320, "left": 90},
                      "captionMaxWidth": 840},
            "subtitles": [{"text": text}]}


def test_a_failed_render_is_REPORTED_not_dropped(tmp_path):
    """MUTATION 8: return None on failure again.

    A dropped segment is one the manifest never learns about, and 5.04
    then refuses the compile citing a missing block rather than the
    render that actually failed.
    """
    seg = r405_2.render_one_segment(_props_3(), str(tmp_path), "tl",
                                  remotion_dir=REPO,
                                  renderer=_Renderer_2(ok=False, error="boom"))
    assert seg is not None
    assert seg["provenance"] == r405_2.FAILED
    assert "boom" in seg["failure"]


def test_an_unchanged_segment_is_reused_and_a_changed_one_never_is(tmp_path):
    """MUTATION 9: mark everything `rendered`."""
    engine = _Renderer_2()
    first = r405_2.render_one_segment(_props_3(), str(tmp_path), "tl",
                                    remotion_dir=REMOTION_4, renderer=engine,
                                    reuse=True,
                                    overlay_geometry="full")
    second = r405_2.render_one_segment(_props_3(), str(tmp_path), "tl",
                                     remotion_dir=REMOTION_4, renderer=engine,
                                     reuse=True,
                                     overlay_geometry="full")
    assert first["provenance"] == r405_2.RENDERED
    assert second["provenance"] == r405_2.REUSED
    assert engine.calls == 1, "the second call must not have rendered"

    # MUTATION 10, the one that matters most: a CHANGED segment is never
    # skipped. Skipping on PRESENCE would skip a text-only correction
    # (on 001, changing every caption in a block changed 0 of 8 names).
    again = r405_2.render_one_segment(_props_3(text="AFTER"), str(tmp_path), "tl",
                                    remotion_dir=REMOTION_4, renderer=engine,
                                    reuse=True,
                                    overlay_geometry="full")
    assert again["provenance"] == r405_2.RENDERED
    assert engine.calls == 2


def test_a_plain_run_re_renders_even_when_the_key_matches(tmp_path):
    """MUTATION 11: flip the `reuse` default to on.

    Firstmate's ruling: a plain run always re-renders. Reuse is opt-in.
    Without this, a one-character change to a default would make every
    run reuse, every other test would still pass, and the first symptom
    would be a stale caption in a delivered video.
    """
    engine = _Renderer_2()
    r405_2.render_one_segment(_props_3(), str(tmp_path), "tl",
                            remotion_dir=REMOTION_4, renderer=engine, reuse=True,
                            overlay_geometry="full")
    plain = r405_2.render_one_segment(_props_3(), str(tmp_path), "tl",
                                    remotion_dir=REMOTION_4, renderer=engine,
                                    overlay_geometry="full")
    assert plain["provenance"] == r405_2.RENDERED
    assert engine.calls == 2


def test_an_unavailable_renderer_fingerprint_refuses_reuse(tmp_path):
    """MUTATION 12: treat "cannot hash" as "matches".

    Unavailable evidence must never read as matching evidence.
    """
    assert r405_2.renderer_fingerprint(str(tmp_path / "nothing-here")) == ""
    engine = _Renderer_2()
    for _ in range(2):
        seg = r405_2.render_one_segment(
            _props_3(), str(tmp_path), "tl",
            remotion_dir=str(tmp_path / "nothing-here"),
            renderer=engine, reuse=True,
            overlay_geometry="full")
        assert seg["provenance"] == r405_2.RENDERED
    assert engine.calls == 2


# ── The transcript half ──────────────────────────────────────────────

def test_the_transcript_splice_replaces_by_overlap_and_rederives_word_ends():
    doc = {"speech_regions": [
        {"start": 0.0, "end": 1.5, "text": "before", "words": []},
        {"start": 1.8, "end": 4.0, "text": "straddles", "words": []},
        {"start": 6.0, "end": 8.0, "text": "after", "words": []}]}
    out = splice_region_index(doc, [{"start": 2.5, "end": 3.5,
                                     "text": "new", "words": []}], 1.9, 5.0)
    # "straddles" goes even though it is not CONTAINED: it was partly
    # re-measured, so keeping it would leave two descriptions of 1.9-4.0s.
    assert [r["text"] for r in out["speech_regions"]] == \
        ["before", "new", "after"]

    # word_end_times is re-derived, not left stale.
    doc = {"speech_regions": [{"start": 0.0, "end": 2.0, "words": [
               {"word": "a", "start": 0.0, "end": 2.0}]}],
           "word_end_times": [2.0]}
    out = splice_region_index(doc, [{"start": 3.0, "end": 4.0, "words": [
        {"word": "b", "start": 3.0, "end": 4.0}]}], 2.5, 5.0)
    assert out["word_end_times"] == [2.0, 4.0]


def test_a_re_measured_region_outside_its_span_is_refused():
    """Leaked padding would overwrite speech that was never re-measured."""
    doc = {"speech_regions": []}
    with pytest.raises(ValueError) as exc:
        splice_region_index(doc, [{"start": 0.5, "end": 3.0, "words": []}],
                            1.0, 4.0)
    assert "padding leaked" in str(exc.value)


# ── The partial write ────────────────────────────────────────────────

def _project(tmp_path, outputs):
    (tmp_path / "pipeline_data.json").write_text(
        json.dumps({"step_outputs": outputs}), encoding="utf-8")
    return str(tmp_path)


def test_a_refused_splice_leaves_the_file_byte_identical(tmp_path):
    project = _project(tmp_path, {"plan_subtitles": {"n": 1}})
    before = (tmp_path / "pipeline_data.json").read_bytes()

    def boom(_after):
        raise ValueError("not on my watch")

    with pytest.raises(state_splice.StateSpliceRefused):
        state_splice.splice_step_output(
            project, "plan_subtitles", lambda cur: {"n": 2},
            label="t", verify=boom)
    assert (tmp_path / "pipeline_data.json").read_bytes() == before

    # Splicing into a step that never ran is refused.
    with pytest.raises(state_splice.StateSpliceRefused) as exc:
        state_splice.splice_step_output(
            project, "render_subtitles", lambda cur: {}, label="t")
    assert "nothing to splice into" in str(exc.value)


# ── The fifth --rerun form ───────────────────────────────────────────

def _runner():
    """The runner module, by absolute package path.

    Production (`operations.Operation`) reaches it through a bare
    ``import run_pipeline`` with the process directory on sys.path; the
    tests need the same file's behaviour, and the bare name is
    collection-order-sensitive, so this route spells the package out.
    """
    from library.processes.edit_video import run_pipeline
    return run_pipeline


def test_the_runner_refuses_a_region_rerun_and_names_what_honours_one():
    """`--rerun <step>@<span>` PARSES, and the runner cannot honour it:
    steps run as subprocesses and no `main()` reads an address, so it
    used to redo the WHOLE video while printing that the region decided.
    It refuses, naming the region operation DERIVED from the registry -
    or saying plainly that none exists - and half-does nothing."""
    from library.tools.step_ledger import LedgerError

    runner = _runner()
    state = {}
    with pytest.raises(LedgerError) as exc:
        runner.apply_rerun_requests(
            "/nonexistent", state, ["plan_subtitles@32.0-48.0"],
            {"plan_subtitles": "edit"}, {})
    message = str(exc.value)
    assert "cannot re-run part of a step" in message
    assert "operations subtitles.plan" in message
    assert "--region 32.0-48.0" in message
    assert state == {}

    with pytest.raises(LedgerError) as exc:
        runner.apply_rerun_requests(
            "/nonexistent", {}, ["color_grade@32.0-48.0"],
            {"color_grade": "edit"}, {})
    assert "No operation on color_grade runs at a region" in str(exc.value)
    assert "--rerun color_grade" in str(exc.value)

    with pytest.raises(LedgerError) as exc:
        runner.apply_rerun_requests(
            "/nonexistent", {}, ["plan_transitions@32.0-48.0"],
            {"plan_transitions": "edit"}, {})
    assert "transitions.splice" in str(exc.value)


# ── The line that joins the two halves of the seam ───────────────────
#
# vep-audit-contracts builds the renderer; this file builds the caller.
# Without the construction site AND the teardown site, both halves are
# individually correct and the feature is absent - the vacuous-gate shape
# in wiring rather than in checking.


@pytest.fixture
def no_pixel_qa(monkeypatch):
    """Stub the rendered-overlay QA for the WIRING tests.

    Those tests use a fake renderer that writes a few bytes rather than a
    real ProRes file, so `subtitle_qa` correctly reports that the overlay
    draws nothing. That is the QA doing its job on a stub, and it is not
    what these tests are about - they assert who CONSTRUCTS and who
    CLOSES the renderer. The real QA is exercised by the 001
    demonstration, against real renders.
    """
    import types
    module = types.ModuleType("tools.qa.subtitle_qa")
    module.run_subtitle_qa = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "tools.qa.subtitle_qa", module)
    return module


class _CountingRenderer:
    def __init__(self):
        self.rendered, self.closed = 0, 0

    def render(self, props_path, overlay_path, sequence=False):
        self.rendered += 1
        with open(overlay_path, "wb") as handle:
            handle.write(b"pixels")
        return True, ""

    def close(self):
        self.closed += 1


def _spine_and_plan_for_render():
    spine = {"structure": [_block(1, 0.0, 3.0, ["alpha", "bravo"], src=10.0)],
             "frame_rate": 30.0}
    return spine, generate_subtitles(spine)["subtitle_plan"]


def test_a_renderer_we_BUILT_is_closed_even_when_the_pass_raises(
        tmp_path, monkeypatch):
    """MUTATION: drop the `finally`.

    A renderer holding a bundle or a browser owns an OS resource; over
    nineteen reels a leak per pass is nineteen leaks. The default build
    is the persistent renderer, so the bomb is planted there.
    """
    spine, plan = _spine_and_plan_for_render()
    built = []

    class _Boom(_CountingRenderer):
        def render(self, props_path, overlay_path, sequence=False):
            raise RuntimeError("mid-pass explosion")

    def factory(remotion_dir):
        engine = _Boom()
        built.append(engine)
        return engine

    monkeypatch.setattr(r405_2, "PersistentCaptionRenderer", factory)
    with pytest.raises(RuntimeError):
        r405_2.render_subtitle_overlays(plan, spine,
                                      project_folder=str(tmp_path),
                                      remotion_dir=REMOTION_4)
    assert built and built[0].closed == 1, "we built it, so we close it"
