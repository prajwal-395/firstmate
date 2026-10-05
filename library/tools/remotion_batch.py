"""Render many Remotion overlay cards against ONE bundle.

The defect this replaces
------------------------
Every overlay was rendered by shelling out to `npx remotion render`, once
per card.  Each invocation re-bundled the project and launched its own
`chrome-headless-shell` with its own GPU and network child processes, so
a nineteen-reel caption pass was 763 bundle-and-launch cycles.

That startup, not the frame rendering, is the cost.  Measured on the
captain's machine, 2026-09-05: eight concurrent invocations took a
10-core box from load 4.42 to 18.12 in twenty seconds; deriving the pool
from `os.cpu_count()` and pinning each renderer's frame concurrency to 1
moved it only to 17.42, because rationing invocations does not remove
what each invocation costs.  See issue #530.

`render-batch.mjs` bundles once and renders every card through one
browser.  This module is the Python half, and it is SHARED: the subtitle
step and any other step that draws an overlay call it rather than each
growing its own render loop.

What it does not decide
-----------------------
Nothing here chooses a look, a duration or a composition.  A job carries
props somebody else authored and a path to write; this module knows only
how to get many of them rendered without paying startup 763 times.
"""

from __future__ import annotations

import atexit
import json
import os
import queue
import subprocess
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence, TypeVar

from library.tools import perf_ledger
from library.tools import shared_environment as _node_env

#: Re-exported so existing importers keep working. The definition, and
#: the location logic that used to sit in this module, belong to
#: `shared_environment` - the one place that answers where the renderer and
#: its dependencies are (AGENTS.md 9).
REMOTION_DIRNAME = _node_env.REMOTION_DIRNAME
BATCH_SCRIPT = "render-batch.mjs"


class RemotionBatchError(RuntimeError):
    """The batch could not be run at all - not that a card failed."""


@dataclass(frozen=True)
class RenderJob:
    """One card to draw: the props to draw it from, and where it goes."""

    props: dict
    out_path: str

    def as_dict(self) -> dict:
        return {"props": self.props, "out": self.out_path}


def remotion_dir(repo_root: Optional[str] = None) -> Path:
    """Forwards to `shared_environment`, which owns the answer.

    This module used to derive the location a second time, from
    `__file__`, in parallel with `paths.REMOTION_DIR`.  Two derivations
    of one path is two places to point somewhere else and one of them to
    be forgotten.
    """
    return _node_env.remotion_dir(repo_root)


def _require_dependencies(directory: Path, error: type) -> None:
    """Refuse a render whose dependencies are absent, by name.

    Without this the absence reaches `node`, which reports it as
    `ERR_MODULE_NOT_FOUND` for whichever package `render-batch.mjs`
    imports first - a name no step chose and no remedy follows from.
    `shared_environment.missing_message` names the store entry and the
    install command instead.

    Raised as the caller's own error type so this changes which MESSAGE
    a failing batch carries, never which exception it raises.
    """
    if _node_env.dependencies_present(directory):
        return
    raise error(_node_env.missing_message(directory))


def frame_concurrency() -> int:
    """Frames one card may render at once: HALF THE FREE CORES.

    Now that the batch bundles once and renders through one browser, this
    is the dominant term - which it was not before, when per-invocation
    startup swamped it.  Measured on the captain's 10-core machine,
    twelve real caption cards each time:

        concurrency 1   21.5s   load 5.94 -> 7.24   (+1.30)
        concurrency 2   17.1s   load 7.06 -> 8.02   (+0.96)
        concurrency 4   14.9s   load 8.02 -> 14.75  (+6.73)

    The cost is sharply non-linear: the step from 2 to 4 buys 2.2 seconds
    and costs seven points of load.  So the bound is worth having, and it
    has to be low.

    HEADROOM-AWARE, which is the part a `cpu_count` fraction alone cannot
    do.  What matters is not how many cores exist but how many are FREE:
    the captain renders while working, with Resolve open, and a bound
    derived from an idle machine is wrong on a busy one.  Reading the
    live 1-minute average and taking half of what is spare leaves the
    other half for him - so the same rule yields 4 on an idle 10-core
    box, 2 at load 6, and 1 when the machine is already busy, without a
    ceiling constant anywhere.

    Re-derived per batch, and the builder makes one batch per reel, so a
    nineteen-reel pass adapts as the machine changes under it.
    """
    return _cores_for_rendering()


def encoder_threads() -> int:
    """How many threads the ProRes encoder may use: HALF THE FREE CORES.

    This is the term that actually costs, and finding that out took a
    negative result.  With `concurrency` already at its floor of 1 and
    the batch bundling once, a full pass still ran the machine to 18.00 -
    and a process reading taken mid-pass showed remotion's bundled
    `ffmpeg` at **763% CPU**, about 7.6 of ten cores, encoding ProRes
    4444.  `concurrency` bounds BROWSER TABS; it says nothing about the
    encoder, and ffmpeg with no `-threads` takes every core it can see.

    So the bound belongs here as well, applied through `ffmpegOverride` -
    Remotion's supported hook for rewriting the encoder's arguments.
    Same rule as the browser half, for the same reason: half of what is
    FREE, read live, so the captain keeps the other half while he works.
    """
    return _cores_for_rendering()


MAX_CARD_CONCURRENCY = 4
"""The largest measured card-level fan-out on one shared browser."""


def card_concurrency() -> int:
    """Cards rendered at once, capped at the measured shared-browser knee.

    On the 10-core caption machine, four cards in flight on one Chrome
    instance beat both four frames per card and two cards in flight. The
    cap keeps the pool finite on larger hosts; smaller hosts scale down
    with their CPU count rather than opening four tabs on every machine.
    """
    cpus = os.cpu_count() or 2
    return max(1, min(MAX_CARD_CONCURRENCY, cpus // 2))


def renderer_limits() -> tuple[int, int, int]:
    """Return card, frame, and encoder concurrency for one render server.

    The measured four-card configuration used two browser frames and two
    encoder threads per card. Keep those inner pools at that measured
    bound when card-level fan-out is active; the standalone frame sweep
    remains available when a host scales down to one card at a time.
    """
    cards = card_concurrency()
    frames = frame_concurrency()
    encoders = encoder_threads()
    if cards > 1:
        frames = min(frames, 2)
        encoders = min(encoders, 2)
    return cards, frames, encoders


CardT = TypeVar("CardT")
SegmentT = TypeVar("SegmentT")


def run_caption_card_workers(
        cards: Sequence[CardT],
        render_card: Callable[[int, CardT], SegmentT],
        on_result: Optional[Callable[[int, SegmentT], None]] = None,
        parallel: bool = True) -> List[SegmentT]:
    """Render cards with the machine-derived limit and preserve plan order.

    The caption step owns which cards are eligible and how results enter
    its manifest. This module owns the worker bound. On a fatal renderer
    error, no new cards are submitted; already-running cards finish and
    `on_result` receives every completed result before the original error
    is re-raised. This preserves startup-fallback and partial-pass
    accounting without putting a pool size at the call site.
    """
    workers = card_concurrency() if parallel else 1
    results: dict[int, SegmentT] = {}

    def record(index: int, result: SegmentT) -> None:
        results[index] = result
        if on_result is not None:
            on_result(index, result)

    if workers == 1:
        for index, card in enumerate(cards):
            record(index, render_card(index, card))
        return [results[index] for index in range(len(cards))]

    pending = {}
    next_index = 0
    failure = None
    with ThreadPoolExecutor(max_workers=workers) as executor:
        while next_index < len(cards) and len(pending) < workers:
            future = executor.submit(render_card, next_index, cards[next_index])
            pending[future] = next_index
            next_index += 1

        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in sorted(done, key=lambda item: pending[item]):
                index = pending.pop(future)
                try:
                    results[index] = future.result()
                except Exception as exc:
                    failure = failure or exc
            if failure:
                for future in pending:
                    future.cancel()
                break
            while next_index < len(cards) and len(pending) < workers:
                future = executor.submit(render_card, next_index, cards[next_index])
                pending[future] = next_index
                next_index += 1

    for future, index in sorted(
            pending.items(), key=lambda item: item[1]):
        if future.cancelled():
            continue
        try:
            results[index] = future.result()
        except Exception as exc:
            failure = failure or exc

    if on_result is not None:
        for index in sorted(results):
            on_result(index, results[index])
    if failure:
        raise failure
    return [results[index] for index in range(len(cards))]


RENDER_FLOOR_DIVISOR = 5
"""The floor is a FIFTH of the machine's TOTAL cores.

MEASURED, not chosen.  Six real caption cards, one 10-core machine, at
load 11-16, against the `npx remotion render` path this replaces
(35.52s):

    1/10 of the machine   48.01s   WORSE than npx
    2/10                  30.35s   beats npx
    3/10                  27.89s   beats npx
    4/10                  27.12s   beats npx

The crossing is between one tenth and two tenths, and the knee is sharp:
going 1 -> 2 buys 17.7 seconds while 2 -> 4 buys only 3.2 more.  So a
fifth of the machine is the smallest allocation measured to beat the
alternative, and that is what the floor is - not a round number that
looked safe.
"""


def _cores_for_rendering() -> int:
    """Half the FREE cores, but never below a fifth of the machine.

    WHY A LIVE-LOAD-ONLY RULE IS SELF-DEFEATING HERE, with the numbers,
    so nobody re-derives it.

    The adaptive term alone was `(cpus - loadavg) // 2`, and its whole
    intent is right: leave the operator half of what is spare, so the
    captain keeps working while a pass runs.  But it is a function of a
    TRANSIENT, and it collapses to 1 exactly when the box is busy -
    which is exactly when a long pass runs.  On this 10-core machine at
    load 10.4 it returns `max(1, int(-0.4 // 2))` = 1.

    Measured at that setting, the renderer this module exists to make
    fast became the slowest option available:

        persistent, throttled to 1/1   43.47s
        persistent, at 4/4             26.69s
        npx-per-card, unthrottled      29.91s

    So the throttle cost +2.80s per card while bundling once saves about
    0.54s per card - roughly five times more than the fix it was
    wrapping.  An ABBA run with the ordering confound removed measured
    the whole recovered path 55% slower than the thing it replaced,
    46.44s against 29.91s, which extrapolates to 35 minutes SLOWER over a
    763-card pass.  A module that shipped like that would have carried a
    docstring explaining how it made the pass faster.

    The fix is that the FLOOR must not be a function of the transient.
    It is a fraction of TOTAL cores - a fact about the machine that a
    busy moment cannot move - while the adaptive term still governs
    everything ABOVE it.  On an idle 10-core box this yields 5, at load
    6 it yields 2, and at load 10 it yields 2 rather than 1.  Adaptivity
    is kept; the collapse is not.
    """
    cpus = os.cpu_count() or 2
    floor = max(1, cpus // RENDER_FLOOR_DIVISOR)
    try:
        busy = os.getloadavg()[0]
    except (OSError, AttributeError):  # not POSIX, or unavailable
        busy = 0.0
    adaptive = int(max(0.0, cpus - busy) // 2)
    return max(floor, adaptive)


SPOTLIGHT_OPT_OUT = ".metadata_never_index"
"""macOS reads this filename and stops indexing the directory holding it."""


def exclude_from_indexing(directory) -> Path:
    """Tell Spotlight not to index a render output directory.

    **This measured NO effect, and the docstring says so rather than
    implying otherwise.**  It was added because `corespotlightd` was seen
    at 35.6% CPU during a caption pass and the ProRes output was the
    obvious suspect.  Tested afterwards - fourteen cards, 66 MB of ProRes
    4444, written into the real indexed project directory, with the
    marker and without it - `corespotlightd` stayed at 0.0% mean either
    way.  So that 35.6% was Spotlight doing something else, and this is
    not what fixed anything.

    Kept anyway, on principle rather than on evidence: these are render
    intermediates, transient and regenerated from a digest whenever the
    card changes, and nobody will ever search them.  It costs one empty
    file.  If it is ever found to cost more than that, delete it - there
    is no measurement here defending it.

    Creating the marker is idempotent and never fatal: a directory that
    cannot take one still renders.
    """
    marker = Path(directory) / SPOTLIGHT_OPT_OUT
    try:
        marker.touch(exist_ok=True)
    except OSError:  # noqa: BLE001 - an optimisation, never a gate
        pass
    return marker


def render_batch(jobs: Sequence[RenderJob],
                 composition: str,
                 work_dir: str,
                 repo_root: Optional[str] = None,
                 timeout: Optional[int] = None) -> List[dict]:
    """Render every job, returning one result dict per job.

    A card that fails does NOT fail the batch: its result carries
    `ok: False` and the error, so a caller can place the cards that
    rendered and report the ones that did not, by name.  A batch that
    could not START - no remotion directory, no script, no node - raises,
    because that is not a property of any card.
    """
    if not jobs:
        return []

    from ren.edition import require_component

    require_component("renderer.remotion", action="load")

    directory = remotion_dir(repo_root)
    script = directory / BATCH_SCRIPT
    if not script.exists():
        raise RemotionBatchError(
            f"{script} does not exist, so no overlay can be rendered. "
            f"The Remotion surface is the pipeline's renderer; a step "
            f"does not draw its own.")
    _require_dependencies(directory, RemotionBatchError)

    Path(work_dir).mkdir(parents=True, exist_ok=True)
    exclude_from_indexing(work_dir)
    spec_path = Path(work_dir) / "render_batch_jobs.json"
    cards, frames, encoders = renderer_limits()
    spec_path.write_text(json.dumps({
        "composition": composition,
        "cardConcurrency": cards,
        "concurrency": frames,
        "encoderThreads": encoders,
        "jobs": [job.as_dict() for job in jobs],
    }), encoding="utf-8")

    proc = subprocess.run(
        ["node", str(script), str(spec_path)],
        cwd=str(directory), capture_output=True, encoding="utf-8",
        check=False, timeout=timeout,
    )

    results: List[dict] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            results.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if not results and proc.returncode != 0:
        raise RemotionBatchError(
            f"the render batch produced no results (exit {proc.returncode}). "
            f"stderr: {(proc.stderr or '')[-800:]}")

    for res in results:
        if not res.get("ok"):
            print(f"  overlay failed: {os.path.basename(res.get('out', '?'))}"
                  f" - {res.get('error', '')[:200]}", file=sys.stderr)
    return results


# ── The persistent renderer, for a per-card seam ─────────────────────

class RendererUnavailable(RemotionBatchError):
    """The renderer process is gone, or never came up.

    DELIBERATELY NOT A PER-CARD FAILURE.  `render()` returns
    `(False, error)` when ONE card fails to draw and the renderer is
    fine; it RAISES this when the renderer itself is unusable, because
    those need opposite handling.  A dead renderer reported as a card
    failure would let a caller march 762 more cards into a closed pipe
    and report 762 failures instead of one fault.

    And it never falls back to `npx remotion render` per card.  A
    fallback would quietly restore the exact cost this module exists to
    remove while still reporting success - the vacuous-gate shape in
    performance clothing.
    """


class PersistentRenderer:
    """One node process, one bundle, many cards.

    WHY A PROCESS AND NOT A BATCH CALL.  `render_batch` above amortises
    the bundle across a list, which suits a caller that knows every card
    up front.  The pipeline step does not: its seam is
    `renderer.render(props_path, out_path) -> (ok, error)`, one card at a
    time, because the STEP decides which cards render and what comes
    back.  A per-card seam can only amortise a bundle if something stays
    alive between calls, so this holds the process open.

    LIFECYCLE, stated because a long-lived process holding a browser is
    exactly the thing that leaks:

    * CONSTRUCTION is explicit - `start()`, or the context manager.  It
      blocks until the child prints `{"ready": true}`, so a caller is
      never handed a renderer that is still bundling.
    * REUSE is the point: every `render()` goes to the same process and
      the same bundle.
    * SHUTDOWN is `close()`, and it is IDEMPOTENT.  It closes stdin,
      which is the child's shutdown signal, then waits, then terminates,
      then kills.  Each step is bounded, so a wedged child cannot hang
      the run.
    * SHUTDOWN ON EXCEPTION is structural, not remembered: `__exit__`
      closes on any path out of the block.
    * AND A BACKSTOP: `atexit` closes any renderer still open, so a
      caller who forgets - or a code path that raises past the context
      manager - still cannot leave a browser behind.  A renderer whose
      process outlives its run is worse than the launches it replaces.
    * CHILD DEATH MID-RUN is detected on the next `render()` and RAISES
      `RendererUnavailable`.  It is not retried and not worked around.

    Usage:

        with PersistentRenderer(composition="SubtitleOverlay") as r:
            ok, err = r.render(props_path, out_path)
    """

    #: Every renderer that has been started and not yet closed.
    _open: "List[PersistentRenderer]" = []

    def __init__(self, composition: str,
                 repo_root: Optional[str] = None,
                 startup_timeout: int = 300,
                 render_timeout: int = 600):
        self.composition = composition
        self.repo_root = repo_root
        self.startup_timeout = startup_timeout
        self.render_timeout = render_timeout
        self._proc: Optional[subprocess.Popen] = None
        self._spec_path: Optional[Path] = None
        self._lines: "queue.Queue" = queue.Queue()
        self._reader: Optional[threading.Thread] = None
        self._closed = False
        self._start_lock = threading.Lock()
        self._stdin_lock = threading.Lock()
        self._response_condition = threading.Condition()
        self._responses: dict[int, dict] = {}
        self._next_request_id = 0
        self._stdout_ended = False
        self._start_error: Optional[RendererUnavailable] = None

    # ── construction ────────────────────────────────────────────────

    def start(self) -> "PersistentRenderer":
        """Start once, even when the first cards arrive from worker threads."""
        with self._start_lock:
            if self._start_error is not None:
                raise self._start_error
            try:
                return self._start_locked()
            except RendererUnavailable as exc:
                # Every first-wave worker must observe one startup failure,
                # not retry the same bundle four times under the lock.
                self._start_error = exc
                raise

    def _start_locked(self) -> "PersistentRenderer":
        if self._proc is not None:
            return self
        from ren.edition import require_component

        require_component("renderer.remotion", action="load")
        directory = remotion_dir(self.repo_root)
        script = directory / BATCH_SCRIPT
        if not script.exists():
            raise RendererUnavailable(
                f"{script} does not exist, so no overlay can be rendered. "
                f"The Remotion surface is the pipeline's renderer; a step "
                f"does not draw its own.")
        _require_dependencies(directory, RendererUnavailable)

        import tempfile
        handle, spec = tempfile.mkstemp(suffix=".json",
                                        prefix="render_serve_")
        os.close(handle)
        self._spec_path = Path(spec)
        cards, frames, encoders = renderer_limits()
        self._spec_path.write_text(json.dumps({
            "composition": self.composition,
            "cardConcurrency": cards,
            "concurrency": frames,
            "encoderThreads": encoders,
            "jobs": [],
        }), encoding="utf-8")

        try:
            self._proc = subprocess.Popen(
                ["node", str(script), str(self._spec_path), "--serve"],
                cwd=str(directory), stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                encoding="utf-8", bufsize=1,
            )
        except (FileNotFoundError, OSError) as exc:
            # An ABSENT SYSTEM TOOL is one of the three things the
            # clean-room gate exists to catch, and it is the likeliest
            # failure on a fresh checkout. Refuse by name with the
            # remedy, rather than letting a bare FileNotFoundError('node')
            # surface from thirty frames down.
            self._spec_path.unlink(missing_ok=True)
            self._spec_path = None
            raise RendererUnavailable(
                f"could not start the renderer: {exc}. Node.js must be on "
                f"PATH and `npm install` must have been run in "
                f"{directory.name}/ - see the env.npx and "
                f"env.remotion_installed requirements, which report this "
                f"before a run starts.") from exc
        PersistentRenderer._open.append(self)

        # A READER THREAD, so a wedged child cannot hang the run.
        #
        # `readline()` on a pipe blocks forever, and a child that is
        # ALIVE but not answering - SIGSTOPped, deadlocked, stuck in a
        # render that never returns - is not detectable by `poll()`.
        # Every read below is therefore bounded by a queue timeout, and
        # `render_timeout` is enforced rather than merely declared.
        self._lines = queue.Queue()
        self._stdout_ended = False
        with self._response_condition:
            self._responses.clear()
        self._reader = threading.Thread(
            target=self._pump, args=(self._proc.stdout,), daemon=True)
        self._reader.start()

        ready = self._await_ready()
        if not ready:
            self.close()
            raise RendererUnavailable(
                "the renderer did not report ready; nothing was rendered")
        return self

    def _pump(self, stream) -> None:
        """Move lines off the pipe so a read can be given a deadline.

        Ends on EOF and posts `None` as the sentinel, so a waiting
        `render()` learns the child is gone rather than waiting out its
        whole timeout.
        """
        try:
            for line in iter(stream.readline, ""):
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    self._lines.put(line)
                    continue
                if isinstance(message, dict) and "requestId" in message:
                    with self._response_condition:
                        self._responses[message["requestId"]] = message
                        self._response_condition.notify_all()
                else:
                    self._lines.put(line)
        except (ValueError, OSError):
            pass                      # pipe closed under us; EOF is EOF
        finally:
            with self._response_condition:
                self._stdout_ended = True
                self._response_condition.notify_all()
            self._lines.put(None)

    def _next_line(self, timeout: float):
        """One line, or None at EOF. Raises on timeout."""
        try:
            return self._lines.get(timeout=timeout)
        except queue.Empty:
            raise RendererUnavailable(
                f"the renderer did not answer within {timeout:g}s. It is "
                f"alive but not responding, which `poll()` cannot see, so "
                f"the wait is bounded here. Nothing is retried and no "
                f"per-card fallback is attempted.") from None

    def _await_ready(self) -> bool:
        """Block until the child says it has bundled, or it dies."""
        deadline = time.time() + self.startup_timeout
        while time.time() < deadline:
            remaining = max(0.1, deadline - time.time())
            try:
                line = self._next_line(remaining)
            except RendererUnavailable:
                return False          # bundling outran its budget
            if line is None:
                return False          # child died before it was ready
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue              # remotion's own chatter
            if message.get("ready"):
                return True
        return False

    # ── the seam ────────────────────────────────────────────────────

    def render(self, props_path: str, overlay_path: str,
               sequence: bool = False):
        """`_render_card`, charged to `remotion_render` in the perf ledger
        (the first card's bundle included)."""
        with perf_ledger.span("remotion_render", backend="remotion_persistent",
                              calls=1):
            return self._render_card(props_path, overlay_path, sequence)

    def _render_card(self, props_path: str, overlay_path: str,
                     sequence: bool = False):
        """Draw one card. `(ok, error)` - safe for concurrent card workers.

        `sequence` is refused, loudly: this renderer stitches video
        through one bundle, and a sequence it cannot draw reported as
        drawn would be the stale-but-reported-fresh defect in another
        shape. Sequence renders go through the CLI renderer, which
        passes `--sequence` per card.

        Raises `RendererUnavailable` if the renderer is not usable. That
        is not the same event as a card failing to draw, and conflating
        them is how one fault becomes 763 reported failures.
        """
        if sequence:
            raise ValueError(
                "the persistent renderer stitches video and cannot "
                "render a frame sequence; use the CLI renderer with "
                "sequence=True.")
        # LAZY, and this is a requirement of the seam rather than a
        # nicety.  `render_one_segment` can return WITHOUT rendering:
        # with reuse on, a region-scoped pass skips most cards, so a
        # bundle paid at construction would be paid in full to render
        # one card - making the region path slower than the thing it
        # replaced.  The bundle is therefore paid on the first card that
        # actually draws, and a pass that draws nothing pays nothing.
        if self._proc is None and not self._closed:
            self.start()
        if self._proc is None:
            raise RendererUnavailable(
                "the renderer has been closed and cannot render again")
        if self._proc.poll() is not None:
            raise RendererUnavailable(
                f"the renderer process exited (code {self._proc.returncode}) "
                f"before this card. It is not restarted and no per-card "
                f"fallback is attempted, because falling back would "
                f"silently restore the startup cost this removes.")

        with self._response_condition:
            request_id = self._next_request_id
            self._next_request_id += 1
        request = json.dumps({
            "requestId": request_id,
            "props": json.loads(Path(props_path).read_text(encoding="utf-8")),
            "out": overlay_path,
        })
        try:
            with self._stdin_lock:
                self._proc.stdin.write(request + "\n")
                self._proc.stdin.flush()
        except (BrokenPipeError, ValueError) as exc:
            raise RendererUnavailable(
                f"the renderer's input pipe is closed: {exc}") from exc

        deadline = time.monotonic() + self.render_timeout
        with self._response_condition:
            while request_id not in self._responses:
                if self._stdout_ended:
                    raise RendererUnavailable(
                        "the renderer closed its output without answering; "
                        "it has died mid-run")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RendererUnavailable(
                        f"the renderer did not answer within "
                        f"{self.render_timeout:g}s. It is alive but not "
                        f"responding, so the wait is bounded here. Nothing "
                        f"is retried and no per-card fallback is attempted.")
                self._response_condition.wait(remaining)
            answer = self._responses.pop(request_id)
        return bool(answer.get("ok")), str(answer.get("error") or "")

    # ── shutdown ────────────────────────────────────────────────────

    def close(self) -> None:
        """Idempotent, bounded, and never leaves the child running."""
        self._closed = True
        proc, self._proc = self._proc, None
        if self in PersistentRenderer._open:
            PersistentRenderer._open.remove(self)
        if proc is not None:
            try:
                if proc.stdin and not proc.stdin.closed:
                    proc.stdin.close()      # the child's shutdown signal
            except (BrokenPipeError, OSError):
                pass
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()             # last resort, never optional
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
            for pipe in (proc.stdout, proc.stderr):
                try:
                    if pipe and not pipe.closed:
                        pipe.close()
                except OSError:
                    pass
        if self._spec_path is not None:
            try:
                self._spec_path.unlink(missing_ok=True)
            except OSError:
                pass
            self._spec_path = None

    def __enter__(self) -> "PersistentRenderer":
        # Deliberately does NOT start. The bundle is paid on the first
        # card that actually renders; a `with` block that skips every
        # card costs nothing. `close()` on a renderer that never started
        # is a no-op.
        return self

    def __exit__(self, *exc) -> bool:
        self.close()
        return False        # never swallow the exception that got us here

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None


@atexit.register
def _close_any_open_renderers() -> None:
    """The backstop. A browser must not outlive the run that made it."""
    for renderer in list(PersistentRenderer._open):
        try:
            renderer.close()
        except Exception:       # noqa: BLE001 - shutdown must not raise
            pass
