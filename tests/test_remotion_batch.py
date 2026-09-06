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
    encoder_threads,
    frame_concurrency,
    render_batch,
    remotion_dir,
)


def _jobs(n: int, tmp_path: Path):
    return [RenderJob(props={"durationInFrames": 24, "n": i},
                      out_path=str(tmp_path / f"card_{i}.mov"))
            for i in range(n)]


class _Proc:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


def test_many_cards_are_one_subprocess(tmp_path):
    """The whole point: N cards, ONE invocation."""
    jobs = _jobs(5, tmp_path)
    out = "\n".join(json.dumps({"ok": True, "out": j.out_path}) for j in jobs)
    with patch("subprocess.run", return_value=_Proc(out)) as run:
        results = render_batch(jobs, composition="SubtitleOverlay",
                               work_dir=str(tmp_path))
    assert run.call_count == 1
    assert len(results) == 5
    assert all(r["ok"] for r in results)


def test_the_job_file_carries_every_card_and_the_composition(tmp_path):
    jobs = _jobs(3, tmp_path)
    with patch("subprocess.run", return_value=_Proc("")) as run:
        render_batch(jobs, composition="SubtitleOverlay",
                     work_dir=str(tmp_path))
    spec = json.loads((tmp_path / "render_batch_jobs.json").read_text())
    assert spec["composition"] == "SubtitleOverlay"
    assert len(spec["jobs"]) == 3
    assert spec["jobs"][0]["props"]["n"] == 0
    assert spec["concurrency"] == frame_concurrency()
    assert spec["encoderThreads"] == encoder_threads()
    # The script is invoked once, with the job file.
    assert run.call_args.args[0][0] == "node"


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


def test_no_jobs_runs_nothing(tmp_path):
    with patch("subprocess.run") as run:
        assert render_batch([], composition="SubtitleOverlay",
                            work_dir=str(tmp_path)) == []
    run.assert_not_called()


def test_a_missing_batch_script_is_refused(tmp_path):
    jobs = _jobs(1, tmp_path)
    with patch("library.tools.remotion_batch.remotion_dir",
               return_value=tmp_path / "nowhere"):
        with pytest.raises(RemotionBatchError) as excinfo:
            render_batch(jobs, composition="SubtitleOverlay",
                         work_dir=str(tmp_path))
    assert "does not exist" in str(excinfo.value)


def test_the_batch_script_ships():
    """A step does not draw its own overlay; the Remotion surface does."""
    assert (remotion_dir() / "render-batch.mjs").exists()


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


def test_no_ceiling_constant_is_fitted_to_one_machine():
    """The target was "do not cross 12 on his box". 12 is not in here -
    the rule is half the free cores, and it yields that target on his
    machine rather than encoding it."""
    import inspect

    from library.tools import remotion_batch

    source = inspect.getsource(remotion_batch.frame_concurrency)
    assert "12" not in source


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


def test_the_batch_script_bounds_the_encoder_through_the_supported_hook():
    """`ffmpegOverride` is Remotion's own hook for rewriting encoder
    args. A test here because the number is useless if the script does
    not apply it."""
    script = (remotion_dir() / "render-batch.mjs").read_text()
    assert "ffmpegOverride" in script
    assert '"-threads"' in script


def test_render_output_is_excluded_from_spotlight(tmp_path):
    """Render intermediates are transient and nobody searches them.

    Note this measured NO effect - `corespotlightd` stayed at 0.0% with
    the marker and without it, on 66 MB of ProRes written into the real
    indexed directory. The test pins the behaviour, not a benefit."""
    from library.tools.remotion_batch import SPOTLIGHT_OPT_OUT

    work = tmp_path / "out"
    with patch("subprocess.run", return_value=_Proc("")):
        render_batch(_jobs(1, tmp_path), composition="SubtitleOverlay",
                     work_dir=str(work))
    assert (work / SPOTLIGHT_OPT_OUT).exists()


def test_the_marker_is_idempotent_and_never_fatal(tmp_path):
    from library.tools.remotion_batch import exclude_from_indexing

    exclude_from_indexing(tmp_path)
    exclude_from_indexing(tmp_path)          # again, no raise
    with patch("pathlib.Path.touch", side_effect=OSError):
        exclude_from_indexing(tmp_path)      # unwritable, still no raise


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


def test_the_refusal_says_it_will_not_fall_back(monkeypatch, tmp_path):
    renderer, proc = _started(monkeypatch, tmp_path)
    proc._returncode = 1
    props = tmp_path / "p.json"
    props.write_text("{}")
    with pytest.raises(RendererUnavailable) as caught:
        renderer.render(str(props), str(tmp_path / "out.mov"))
    assert "fallback" in str(caught.value)
    renderer.close()


def test_a_child_that_dies_without_answering_refuses(monkeypatch, tmp_path):
    """Alive at the check, gone by the read - the real race."""
    renderer, proc = _started(monkeypatch, tmp_path, answers=[])  # EOF
    props = tmp_path / "p.json"
    props.write_text("{}")
    with pytest.raises(RendererUnavailable, match="died mid-run"):
        renderer.render(str(props), str(tmp_path / "out.mov"))
    renderer.close()


def test_a_card_failure_is_NOT_a_renderer_failure(monkeypatch, tmp_path):
    """The distinction the whole design turns on.

    One bad card returns (False, error) and the renderer stays up,
    because the bundle is the expensive thing.
    """
    renderer, proc = _started(
        monkeypatch, tmp_path,
        answers=['{"ok": false, "out": "x.mov", "error": "bad font"}\n'])
    props = tmp_path / "p.json"
    props.write_text("{}")
    ok, error = renderer.render(str(props), str(tmp_path / "out.mov"))
    assert ok is False and "bad font" in error
    assert renderer.alive, "one bad card must not take the renderer down"
    renderer.close()


def test_close_is_idempotent_and_deregisters(monkeypatch, tmp_path):
    renderer, proc = _started(monkeypatch, tmp_path)
    renderer.close()
    assert renderer not in PersistentRenderer._open
    renderer.close()                            # must not raise
    assert renderer._proc is None


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


def test_exit_closes_even_when_the_body_raises(monkeypatch, tmp_path):
    """Shutdown on exception is structural, not remembered."""
    renderer, proc = _started(monkeypatch, tmp_path)
    monkeypatch.setattr(PersistentRenderer, "start", lambda self: self)
    with pytest.raises(RuntimeError):
        with renderer:
            raise RuntimeError("boom")
    assert renderer._proc is None
    assert renderer not in PersistentRenderer._open


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


def test_there_is_an_atexit_backstop():
    """A browser must not outlive the run that made it, even if a
    caller forgets to close and no context manager was used."""
    import atexit
    import inspect

    from library.tools import remotion_batch

    source = inspect.getsource(remotion_batch)
    assert "@atexit.register" in source
    assert "_close_any_open_renderers" in source
    # and the registry it walks really tracks open renderers
    assert isinstance(PersistentRenderer._open, list)


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


def test_render_timeout_is_actually_enforced_not_just_stored():
    """The mirror of the defect: the attribute must reach the read."""
    import inspect

    from library.tools import remotion_batch

    source = inspect.getsource(remotion_batch.PersistentRenderer.render)
    assert "self.render_timeout" in source, (
        "render() no longer passes render_timeout to its read, so the "
        "timeout is declared and unenforced again")


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

def test_the_floor_is_a_fraction_of_total_cores_not_of_free_ones():
    """The defect: `(cpus - loadavg) // 2` collapses to 1 exactly when
    the box is busy, which is exactly when a long pass runs.

    Measured on 10 cores at load ~11, six real cards, against the
    npx-per-card path it replaces (35.52s): pinned to 1 the renderer
    took 48.01s - SLOWER than the thing it exists to beat - while 2 took
    30.35s. So the floor must not be a function of the transient.
    """
    from library.tools.remotion_batch import frame_concurrency

    for load in (9.5, 10.4, 18.0, 40.0):
        with patch("os.cpu_count", return_value=10), \
             patch("os.getloadavg", return_value=(load,) * 3):
            assert frame_concurrency() == 2, (
                f"at load {load} on 10 cores the rule collapsed below the "
                f"floor; that is the regression this floor exists to stop")


def test_adaptivity_survives_above_the_floor():
    """A floor that always wins is not a floor, it is a constant - and
    would throw away being a good citizen on a shared box."""
    from library.tools.remotion_batch import frame_concurrency

    seen = []
    for load in (0.0, 1.0, 4.0, 6.0):
        with patch("os.cpu_count", return_value=10), \
             patch("os.getloadavg", return_value=(load,) * 3):
            seen.append(frame_concurrency())
    assert seen == sorted(seen, reverse=True), seen
    assert seen[0] > seen[-1], (
        f"the rule no longer responds to load at all: {seen}")


def test_the_floor_is_derived_from_the_machine_not_hardcoded():
    """No fresh magic constant. The floor is cpu_count // DIVISOR, so it
    is a different number on a different machine."""
    from library.tools.remotion_batch import (RENDER_FLOOR_DIVISOR,
                                              frame_concurrency)

    for cpus, expected_floor in ((10, 2), (20, 4), (5, 1)):
        with patch("os.cpu_count", return_value=cpus), \
             patch("os.getloadavg", return_value=(cpus * 2,) * 3):
            assert frame_concurrency() == max(
                1, cpus // RENDER_FLOOR_DIVISOR) == expected_floor


def test_the_derivation_records_the_measurement_that_set_it():
    """A number with no measurement behind it is a magic constant with a
    good comment. The numbers must be in the source."""
    import inspect

    from library.tools import remotion_batch

    doc = inspect.getdoc(remotion_batch._cores_for_rendering) or ""
    floor_doc = remotion_batch.__dict__.get("RENDER_FLOOR_DIVISOR")
    source = inspect.getsource(remotion_batch)
    assert floor_doc == 5
    assert "48.01" in source and "30.35" in source, (
        "the measurement that set the floor is no longer recorded next "
        "to it")
    assert "transient" in doc


def test_the_bundle_is_lazy_so_a_pass_that_draws_nothing_pays_nothing():
    """A requirement of the seam, not a nicety.

    `render_one_segment` can return WITHOUT rendering - with reuse on, a
    region-scoped pass skips most cards. A bundle paid at construction
    would be paid in full to draw one card, making the region path
    slower than the thing it replaced. `step_4_05_render_subtitles`
    builds ONE renderer for the whole pass, so eager construction there
    would cost every scoped re-render a full bundle.
    """
    import subprocess as sp

    def running():
        return int(sp.run("pgrep -f render-batch.mjs | wc -l", shell=True,
                          capture_output=True, text=True,
                          check=False).stdout.strip() or 0)

    before = running()
    with PersistentRenderer(composition="SubtitleOverlay") as renderer:
        assert renderer._proc is None, (
            "entering the block started a process before any card asked "
            "to be drawn")
        assert running() == before
    assert running() == before


def test_it_satisfies_the_seam_the_step_declares():
    """The step's contract is two methods and no more. If this drifts,
    4.05 cannot use this renderer at all."""
    renderer = PersistentRenderer(composition="X")
    assert callable(getattr(renderer, "render", None))
    assert callable(getattr(renderer, "close", None))

    import inspect

    signature = inspect.signature(PersistentRenderer.render)
    assert list(signature.parameters) == ["self", "props_path",
                                          "overlay_path"], (
        f"render() no longer matches the seam "
        f"`render(props_path, overlay_path) -> (ok, error)`: "
        f"{list(signature.parameters)}")
