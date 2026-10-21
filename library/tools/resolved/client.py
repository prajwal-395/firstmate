"""Talking to ren-resolved: submit, status, result, and a grant.

Every call is bounded. A socket file nobody answers is a dead broker,
and a caller treats it as no broker (`ping` returns None) rather than
waiting on it.

The broker is a STANDALONE service, not a child of any worker:
`ensure` starts it detached (its own session, stdio to a log) so it
outlives the worker that started it, and every client that needs the
broker calls `ensure` first - the lease path (`resolve_lock`),
`resolve-axi`, and `call` itself. A starter lock serializes
concurrent starters, so exactly one broker serves a socket.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from library.tools.ren_refusal import RenRefusal
from library.tools.resolved import broker_paths

#: Marks the broker's own executor thread: the work it runs must not
#: queue behind itself, and `resolve-axi` there runs rather than
#: forwarding back to the broker.
_executor = threading.local()


def mark_executor() -> None:
    _executor.active = True


def in_broker() -> bool:
    return getattr(_executor, "active", False)

CONNECT_TIMEOUT_SECONDS = 2.0

#: How long `ensure` waits for a broker it started to answer. The
#: broker connects to Resolve LAZILY (only when a job runs), so this
#: is interpreter startup and imports only - a few seconds at most.
START_TIMEOUT_SECONDS = 15.0

LOG_NAME = broker_paths.LOG_NAME
STARTER_LOCK_NAME = broker_paths.STARTER_LOCK_NAME

#: Opt-in for starting a DETACHED broker from inside the test suite.
#: `ensure` raises rather than spawning where pytest runs and this is
#: not "1" - a stray call then fails its test instead of leaking a
#: detached broker that outlives the session (measured 2026-10-06:
#: 144 orphaned test brokers holding the live Resolve port). A test
#: that means to spawn sets it (its lock-dir fixture does) and stops
#: what it started at teardown. The production default socket is
#: never spawned from tests, opt-in or not.
TESTS_ALLOW_SPAWN_ENV = "REN_TESTS_ALLOW_BROKER_SPAWN"


def _tests_allow_spawn() -> bool:
    """Whether a test run explicitly opted into detached brokers."""
    return os.environ.get(TESTS_ALLOW_SPAWN_ENV, "") == "1"


class TestSpawnRefused(RenRefusal):
    """`ensure` would have spawned a broker inside the test suite.

    A subclass of its own (not a bare `RenRefusal`) so the lease path
    can tell "no broker may be started here" from a production refusal
    it must propagate: `resolve_lock._broker_turn` treats this as "no
    broker serves" and yields None - the lease it grants is then the
    instance lease only, which is what runner-direct tests (a fake
    Resolve, no broker) need.
    """


class BrokerError(RuntimeError):
    """The broker answered with an error. `reply` is what it said."""

    def __init__(self, reply: dict):
        super().__init__(reply.get("error", "ren-resolved error"))
        self.reply = reply


def _path(path=None) -> Path:
    if path is not None:
        return Path(path)
    from library.tools.resolved import broker_paths
    return broker_paths.socket_path()


def _open(path=None) -> Optional[socket.socket]:
    target = _path(path)
    if not target.exists():
        return None
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(CONNECT_TIMEOUT_SECONDS)
    try:
        sock.connect(str(target))
    except OSError:
        sock.close()
        return None
    return sock


def _exchange(sock: socket.socket, stream, request: dict,
              timeout: Optional[float]) -> dict:
    sock.settimeout(timeout)
    sock.sendall((json.dumps(request) + "\n").encode("utf-8"))
    line = stream.readline()
    if not line:
        raise BrokerError({"error": "ren-resolved closed the connection"})
    reply = json.loads(line)
    if "error" in reply:
        raise BrokerError(reply)
    return reply


def call(request: dict, path=None, timeout: Optional[float] = 30.0) -> dict:
    """One request, one reply. Raises `BrokerError`, or `ConnectionError`
    where no broker is serving.

    Ensures a broker is serving first - the one op that must not is
    `shutdown`, which is the stop command and has to be able to say
    "nothing is serving" rather than starting what it was asked to
    stop.

    A `submit` inside a Resolve session (`library.tools.resolve_session`)
    is REFUSED: the session holds the broker's turn for its whole
    operation, so the job would queue behind the very session that is
    waiting for it - a deadlock with a queue in the middle of it.
    """
    if request.get("op") == "submit":
        from library.tools import resolve_session
        if resolve_session.claim_held():
            raise BrokerError({"error": (
                f"refusing to queue the broker job "
                f"{request.get('kind')!r}: {resolve_session.describe_claim()}"
                f" - a broker job cannot run inside a session, because it "
                f"would queue behind the session that is waiting for it")})
    if request.get("op") != "shutdown":
        ensure(path)
    sock = _open(path)
    if sock is None:
        raise ConnectionError(f"ren-resolved is not serving on {_path(path)}")
    with sock, sock.makefile("r", encoding="utf-8") as stream:
        from library.tools import perf_ledger
        perf_ledger.count_call("broker_requests", op=request.get("op"))
        with perf_ledger.span("broker_request", backend="ren-resolved",
                              calls=1, external_engine="resolve_broker"):
            return _exchange(sock, stream, request, timeout)


def ping(path=None) -> Optional[dict]:
    """The serving broker's answer, or None where none is serving.

    A successful Unix-socket connection proves a broker is serving even
    if its reply is slow. Keep that distinction for callers such as
    `ensure` and `serving`: a busy or delayed broker must not prompt a
    second broker to start on the same socket.
    """
    sock = _open(path)
    if sock is None:
        return None
    try:
        with sock, sock.makefile("r", encoding="utf-8") as stream:
            return _exchange(sock, stream, {"op": "ping"},
                             CONNECT_TIMEOUT_SECONDS)
    except (OSError, ValueError, BrokerError):
        from library.tools.resolve_lock import lease_status
        # The lease is machine-wide: a slow broker still reports the
        # instance holder, whatever socket was pinged. The FLOCK says
        # whether the instance is held; the record beside it says who
        # held it last, and is stale when nothing holds the flock
        # (2026-10-20: a dead holder's record read as "busy" for hours
        # after the lock was free).
        status = lease_status()
        return {
            "ok": False,
            "pid": None,
            "serving": True,
            "responsive": False,
            "busy": status.busy,
            "lease": (asdict(status.active)
                      if status.active is not None else None),
            "stale_lease": (asdict(status.recorded)
                            if status.stale else None),
        }


def serving(path=None) -> bool:
    """Whether a broker accepts connections, including while Resolve is busy."""
    return not in_broker() and ping(path) is not None


def health(path=None) -> dict:
    """Health status: separate `connected` from `responsive`.

    `connected` is True where a non-null Resolve handle exists.
    `responsive` is True where a short, harmless API round trip succeeds.
    """
    return call({"op": "health"}, path)


def post_restart_check(path=None) -> dict:
    """A short read-only project/timeline/count check after a restart."""
    return call({"op": "post_restart_check"}, path)


def watchdog_status(path=None) -> dict:
    """The watchdog's current state, for health reporting."""
    return call({"op": "watchdog_status"}, path)


def _in_pytest() -> bool:
    """True when running under pytest (a test process or its child).

    A test's subprocess child is not imported under pytest, but it is
    still the suite's doing: `PYTEST_CURRENT_TEST` is inherited from
    the test that spawned it, so it counts too.
    """
    if "pytest" in sys.modules:
        return True
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def _default_socket_path() -> Path:
    """The production socket: the machine's broker, whatever this
    process isolated.

    Outside the test suite the broker's socket lives in the machine-wide
    directory (`broker_paths.production_dir()`), so this is the socket a
    call reaches when nothing isolated it - and the one a test must
    never start or connect to.
    """
    from library.tools.resolved import broker_paths
    return broker_paths.production_socket_path()


def _is_default_socket(target: Path) -> bool:
    """True where `target` is the production socket, symlinks resolved."""
    from library.tools.resolved import broker_paths
    return broker_paths.is_production_socket(target)


def _engine_root() -> Optional[Path]:
    """The checkout this package was imported from, or None.

    Found by walking up from this file to the directory that contains
    `library/tools/resolved/__init__.py` - so a packaged install (where
    `library` sits in site-packages) answers None and the subprocess
    relies on the inherited environment instead.
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / "library" / "tools" / "resolved" / "__init__.py").is_file():
            return parent
    return None


def _git_path(root: Path, flag: str) -> Optional[str]:
    """The absolute path of a git dir flag, or None where git cannot answer.

    `--path-format=absolute` (git 2.13+) is tried first; a relative
    answer from an older git is resolved against `root`.
    """
    for args in (["rev-parse", "--path-format=absolute", flag],
                 ["rev-parse", flag]):
        try:
            out = subprocess.run(
                ["git", "-C", str(root), *args],
                capture_output=True, text=True, timeout=5,
                check=True).stdout.strip()
        except (subprocess.SubprocessError, OSError):
            continue
        if out:
            return out if out.startswith("/") else str(root / out)
    return None


def _is_linked_worktree(root: Optional[Path]) -> bool:
    """True where `root` is a linked git worktree, never the installed engine.

    A linked worktree (`git rev-parse --git-dir` differing from
    `git rev-parse --git-common-dir`) is managed by a worktree tool and
    may be reclaimed at any time - the same signal the Resolve
    installers' durable-checkout guard used. A client running from one
    must not auto-start the production broker: the broker would serve
    production Resolve calls on unmerged code from a directory that may
    not exist tomorrow. A checkout that is not a git repo at all (a
    packaged install) is not a linked worktree and is allowed.
    """
    if root is None:
        return False
    git_dir = _git_path(root, "--git-dir")
    common_dir = _git_path(root, "--git-common-dir")
    if git_dir is None or common_dir is None:
        return False
    return os.path.realpath(git_dir) != os.path.realpath(common_dir)


def _broker_python() -> str:
    """The interpreter the broker serves under: Ren's venv, not the caller's.

    `sys.executable` is whatever ran the caller - a worker's homebrew
    Python when tests run outside `bin/vep` - and a broker started with
    it serves production Resolve calls on unmerged code with the wrong
    stack. The ladder in `shared_environment` is the one owner of which
    interpreter carries the stack; where it finds none (a machine with
    no venv yet) the caller's interpreter is all there is.
    """
    try:
        from library.tools.shared_environment import python_interpreter
        root = _engine_root()
        python, _ = python_interpreter(
            str(root) if root is not None else None)
    except Exception:  # noqa: BLE001 - finding must never refuse
        python = ""
    if python:
        return python
    return sys.executable


def _rust_daemon() -> Optional[Path]:
    """The Rust ren-resolved binary, where it is built.

    The Rust broker binary, selected by default unless
    `REN_RESOLVED_DAEMON=python` is set. The choice is the native-part
    owner's: the binary built from THIS tree, so a stale shipped
    prebuilt cannot shadow a fresh build. Measured 2026-10-20: the
    checkout had built `ren-resolved` from the tree and the broker
    still served the older `native/prebuilt/ren-resolved` beside it,
    so the broker ran code the tree had already replaced - `ren
    resolved stop` left its worker running, and every later writer
    timed out waiting for the one Resolve.
    """
    root = _engine_root()
    if root is None:
        return None
    try:
        from library.tools import native_refresh
        candidates = native_refresh.serving_binaries(root, "ren-resolved")
    except Exception:  # noqa: BLE001 - finding must never refuse
        candidates = []
    for candidate in candidates:
        if os.access(candidate, os.X_OK):
            return candidate
    return None


def _start_detached(path: Path) -> subprocess.Popen:
    """Start the broker in its OWN session, stdio to a log.

    `start_new_session` detaches it from the starter's process group,
    so killing the worker that started it (how agent sessions end)
    leaves the broker serving. Stdio goes to `LOG_NAME` beside the
    socket rather than the worker's pipes, which would otherwise stay
    open for the broker's lifetime.

    The Rust daemon is the default; `REN_RESOLVED_DAEMON=python` selects
    the Python fallback for one migration cycle. Both use the same
    protocol and socket.
    """
    env = dict(os.environ)
    root = _engine_root()
    if root is not None:
        env["PYTHONPATH"] = os.pathsep.join(
            p for p in (str(root), env.get("PYTHONPATH", "")) if p)
    from library.tools.resolved import broker_paths
    env[broker_paths.BROKER_DIR_TOLD_ENV] = str(path.parent)
    if _in_pytest():
        # A broker a test starts is a detached process the session
        # teardown stops; its shadow watcher must never read the live
        # Resolve against the machine's shadow store (measured
        # 2026-10-06: test brokers' watchers flooded the captain's
        # Resolve with timeline.snapshot jobs). Forced here, not
        # trusted to the test's environment.
        env["REN_SHADOW_WATCHER"] = "0"
    daemon = os.environ.get("REN_RESOLVED_DAEMON", "").strip().lower()
    if daemon != "python":
        binary = _rust_daemon()
        if binary is None and daemon == "rust":
            target = (root / "native" / "target" if root is not None
                      else "the engine's native/target directory")
            raise RuntimeError(
                "REN_RESOLVED_DAEMON=rust but ren-resolved is missing under "
                f"{target} - run scripts/build_ren_resolved.sh")
        if binary is None:
            argv = [_broker_python(), "-m", "library.tools.resolved", "serve",
                    "--socket", str(path)]
        else:
            # The socket path is passed, not derived: the daemon and the
            # client must never disagree about where the broker serves.
            argv = [str(binary), "serve", str(path)]
    else:
        argv = [_broker_python(), "-m", "library.tools.resolved", "serve",
                "--socket", str(path)]
    log = open(path.parent / LOG_NAME, "ab", buffering=0)
    try:
        return subprocess.Popen(
            argv,
            cwd=str(root) if root is not None else os.getcwd(),
            env=env, stdin=subprocess.DEVNULL, stdout=log,
            stderr=subprocess.STDOUT, start_new_session=True)
    finally:
        log.close()          # the child holds its own dup


def ensure(path=None, timeout: float = START_TIMEOUT_SECONDS) -> Optional[dict]:
    """The serving broker's answer, starting one first if none serves.

    This is what makes the broker a standalone service: any client
    that needs it calls `ensure`, and the broker it starts is detached
    and outlives the worker that started it. A no-op where this
    process IS the broker (`in_broker`), and where one is already
    serving it returns that broker's answer at once.

    Single instance is guaranteed twice over: a starter lock
    (`STARTER_LOCK_NAME` beside the socket) serializes concurrent
    starters - the second re-pings under the lock and finds the first's
    broker - and `serve` itself refuses a socket that answers. A
    broker that dies on startup is reported as None (fail fast, no
    waiting out the timeout) and the next client starts a fresh one.
    """
    if in_broker():
        return None
    target = _path(path)
    # Guard: never auto-start a broker on the DEFAULT socket from inside
    # the test suite. Judged by the TARGET, not by whether the lock dir
    # looks isolated: a test that forgets its lock dir, passes the
    # default path explicitly, or inherits the default into a
    # non-pytest child would otherwise leak a broker onto the
    # production socket, which then serves production Resolve calls on
    # unmerged code (measured 2026-10-05: 49 orphaned brokers, each
    # re-binding the default socket). The session isolation in
    # `tests/conftest.py` keeps every test off this path; this refusal
    # is the second line of defence.
    if _in_pytest() and _is_default_socket(target):
        return None
    answer = ping(target)
    if answer is not None and (
            answer.get("busy") or not _is_default_socket(target)
            or _is_linked_worktree(_engine_root())):
        return answer
    # Guard: never SPAWN a broker from inside the test suite unless
    # the test explicitly opted in. Judged here, where nothing is
    # serving and a spawn is what comes next - using a broker that is
    # already serving still works. A stray call then fails its test
    # (naming the fix) instead of leaking a detached process that
    # outlives the session (measured 2026-10-06: 144 orphaned test
    # brokers, each holding the live Resolve scripting port, respawned
    # by a later ensure). The default socket above still returns None:
    # every lease acquisition calls `ensure` there, and yielding "no
    # broker" is that path's answer.
    if _in_pytest() and not _tests_allow_spawn():
        raise TestSpawnRefused(
            what="not starting a test broker",
            why="this client runs inside the test suite and "
                f"{TESTS_ALLOW_SPAWN_ENV} is not '1' - a broker started "
                "here is detached and outlives its test",
            fix=f"set {TESTS_ALLOW_SPAWN_ENV}=1 in the test that means "
                "to spawn (its lock-dir fixture does), run the broker "
                "in-process instead (Broker/connect=..., as the "
                "ui_transaction and resolve_lock suites do), or stop "
                "calling ensure on this path")
    # Guard: a client from a linked worktree must not auto-start the
    # MACHINE's broker. The ping above means a broker already serving
    # is used; with none serving, starting one here would serve
    # production Resolve calls on unmerged code from a directory a
    # worktree tool may reclaim. `bin/ren` sets `REN_ENGINE_ROOT` to the
    # current checkout, so that variable cannot override this signal.
    # The test suite is exempt: its broker is on a private socket, and
    # the suite's own tests start brokers there.
    if (not _in_pytest()
            and _is_linked_worktree(_engine_root())
            and (_is_default_socket(target)
                 or broker_paths.is_machine_socket(target))):
        root = _engine_root()
        raise RenRefusal(
            what="not starting the production Resolve broker",
            why=f"this client runs from a linked worktree ({root}), not "
                "the installed engine - a broker started here would serve "
                "production Resolve calls on unmerged code from a "
                "directory a worktree tool may reclaim",
            fix="run from the installed engine checkout; a linked "
                "worktree cannot start the production broker")
    try:
        import fcntl
    except ImportError:  # pragma: no cover - not POSIX
        fcntl = None
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.parent / STARTER_LOCK_NAME
    with open(lock_path, "a+", encoding="utf-8") as lock:
        if fcntl is not None:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            answer = ping(target)
            if answer is not None:
                if answer.get("busy"):
                    return answer
                if (not _is_default_socket(target)
                        or _is_linked_worktree(_engine_root())):
                    return answer
                # Refresh opportunistically while the old broker is idle.
                # If this produces a new Rust binary, retire this idle
                # process and start the fresh one below.
                try:
                    from library.tools import native_refresh
                    refreshed = native_refresh.ensure_current(
                        _engine_root(), owner="ren-resolved-idle-refresh",
                        lock_timeout=0.25)
                except Exception:  # noqa: BLE001 - a serving broker wins
                    refreshed = {}
                if (os.environ.get("REN_RESOLVED_DAEMON", "").lower()
                        != "python"
                        and refreshed.get("ren-resolved") == "rebuilt"):
                    try:
                        print(
                            "ren-resolved: retiring the idle broker "
                            f"(pid {answer.get('pid')}) after rebuilding "
                            f"ren-resolved from {_engine_root()} - the "
                            "replacement starts below",
                            file=sys.stderr, flush=True)
                    except Exception:  # noqa: BLE001 - retirement is reported
                        pass
                    try:
                        call({"op": "shutdown"}, target)
                    except Exception:  # noqa: BLE001 - verify by ping below
                        pass
                    deadline = time.time() + min(timeout, 5.0)
                    while ping(target) is not None and time.time() < deadline:
                        time.sleep(0.05)
                    if ping(target) is not None:
                        return answer
                else:
                    return answer
            # Startup gets only a short chance at the heavy-work slot.
            # If it is occupied, a present Rust binary (or Python fallback)
            # starts now; a later idle safe point retries the refresh.
            try:
                from library.tools import native_refresh
                refresh = native_refresh.ensure_current(
                    _engine_root(), owner="ren-resolved-start",
                    lock_timeout=0.25)
                pending = [name for name, result in refresh.items()
                           if "pending" in result]
                if pending:
                    os.environ["REN_RESOLVED_REBUILD_PENDING"] = ",".join(pending)
            except Exception:  # noqa: BLE001 - refresh never breaks ensure
                pass
            process = _start_detached(target)
            deadline = time.time() + max(0.0, timeout)
            while True:
                answer = ping(target)
                if answer is not None:
                    return answer
                if process.poll() is not None:
                    return None
                if time.time() >= deadline:
                    return None
                time.sleep(0.1)
        finally:
            if fcntl is not None:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return None


def submit(kind: str, params: dict, owner: str = "",
           qualification: bool = False, path=None,
           idempotency_key: str | None = None) -> dict:
    """Queue one broker job.

    The owner is the CALLER, so a waiter can see who is ahead of it and
    a job whose submitter is gone can be told apart from a job nobody
    wants. A caller that names its own owner keeps it.

    REFUSED inside a Resolve session (`library.tools.resolve_session`):
    the session holds the broker's turn for its whole operation, so the
    job would queue behind the very session that is waiting for it -
    a deadlock with a queue in the middle of it. The refusal names the
    session, so the caller can say what it would have to give up.
    """
    request = {"op": "submit", "kind": kind, "params": params,
               "owner": owner or _caller_owner(), "qualification": qualification}
    if idempotency_key is not None:
        request["idempotency_key"] = idempotency_key
    return call(request, path)


def _caller_owner() -> str:
    """The calling process's name, with its pid.

    `resolve_lock.default_owner` names the lane when a crewmate is
    driving and the pid either way - the broker reads the pid to tell
    a live owner from a dead one.
    """
    from library.tools.resolve_lock import default_owner
    return default_owner()


def report_progress(job_id: str, progress: str, path=None) -> dict:
    """Update the one-line progress shown for a running Resolve job."""
    return call({"op": "progress", "id": job_id, "progress": progress}, path)


def result(job_id: str, wait: float = 0.0, path=None,
           until_done: bool = False, deadline: float = 300.0) -> dict:
    """The job's receipt.

    With `until_done`, the wait is the PROCEDURE the caller used to run
    by hand: ask again while the state is `queued` or `running`.  The
    infrastructure can perform that, so the caller is not asked to
    remember to poll - a model told to "repeat the command if the state
    is still queued or running" had to know the procedure at all.
    """
    if not until_done:
        reply = call({"op": "result", "id": job_id, "wait": wait}, path,
                     timeout=wait + 30.0)
        return reply["job"]
    from library.tools.resolved.store import TERMINAL

    end = time.monotonic() + max(0.0, deadline)
    while True:
        remaining = end - time.monotonic()
        if remaining <= 0:
            return result(job_id, 0.0, path)
        # The broker returns as soon as the job is terminal, so a longer
        # wait costs nothing when the job finishes early - and it keeps
        # the polling from becoming a chatty loop.
        round_wait = min(max(wait, 5.0), remaining)
        job = result(job_id, round_wait, path)
        if job is None or job.get("state") in TERMINAL:
            return job


@contextmanager
def grant(params: dict, owner: str = "", qualification: bool = False,
          wait: float = 900.0, path=None):
    """Hold the broker's turn for an in-process critical section.

    Yields the grant's job id. The turn is held exactly as long as this
    connection: leaving the block releases it, and so does dying.
    """
    sock = _open(path)
    if sock is None:
        raise ConnectionError(f"ren-resolved is not serving on {_path(path)}")
    with sock, sock.makefile("r", encoding="utf-8") as stream:
        reply = _exchange(sock, stream,
                          {"op": "acquire", "params": params, "owner": owner,
                           "qualification": qualification, "wait": wait},
                          wait + 30.0)
        try:
            yield reply["granted"]
        finally:
            try:
                _exchange(sock, stream, {"op": "release"}, 30.0)
            except (OSError, ValueError, BrokerError):
                pass        # closing the socket releases it as well
