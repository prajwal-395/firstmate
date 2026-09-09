"""On-demand Gemma model server: a tiny shim with an idle reaper.

The full `mlx_vlm` model server holds ~7 GB resident (measured 2026-09-09:
`top` MEM 7502M while serving, freed only when the process stops -
`POST /unload` frees ~145 MB, a cache reset, not an unload). The captain
does not want that held all day.

So this module answers the FIXED provider URL (`:8080`, which opencode's
config already points at) with a ~40 MB shim that:

- starts the real backend (`python3 -m mlx_vlm server ...` on 127.0.0.1,
  default port 8081) at the FIRST request that needs the model,
- waits for its `/health`, then reverse-proxies (streaming-safe, so
  opencode's SSE chat works),
- stops the backend process after IDLE_TIMEOUT_S with no in-flight
  requests, no holds, and no traffic - idle means all three, so a
  teardown can never kill live work.

What this module is NOT: it never edits the captain's opencode config or
LaunchAgents (proposed in docs/GEMMA_SERVER.md, applied by them), it never
touches `POST /unload` (does not free memory), and it never replaces
`vision_model.py`'s in-process fallback (still the path when even the
shim is unreachable).

Cold-start cost, measured 2026-09-09 on the captain's 24 GB machine with
the model already in the HuggingFace cache: ~12 s process spawn to
`/health` healthy, ~4 s more to the first chat answer - ~16 s total. That
number, not taste, sets the default idle timeout (600 s): a 60 s timeout
would bill the captain 16 s after every minute idle; 600 s holds the 7 GB
only across a plausible working session, and every proxied request resets
the clock. See docs/GEMMA_SERVER.md.

Stdlib only, so the shim runs on any interpreter the repo supports.
"""

import argparse
import contextlib
import http.client
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MODEL_ID = "mlx-community/gemma-4-12b-it-4bit"

# See module docstring: ~16 s measured cold start -> 600 s idle default.
DEFAULT_IDLE_TIMEOUT_S = 600.0
DEFAULT_STARTUP_TIMEOUT_S = 120.0
DEFAULT_SHIM_PORT = 8080
DEFAULT_BACKEND_PORT = 8081

_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


class BackendUnavailable(RuntimeError):
    """The backend could not be started; the shim answers 503, never hangs."""


@dataclass
class ShimConfig:
    shim_host: str = "127.0.0.1"
    shim_port: int = DEFAULT_SHIM_PORT
    backend_host: str = "127.0.0.1"
    backend_port: int = DEFAULT_BACKEND_PORT
    model: str = MODEL_ID
    idle_timeout_s: float = DEFAULT_IDLE_TIMEOUT_S
    startup_timeout_s: float = DEFAULT_STARTUP_TIMEOUT_S
    reap_interval_s: float = 5.0
    # Full argv used to spawn the backend. None means the real mlx_vlm
    # server; tests pass a fake (stdlib http.server) command instead.
    backend_cmd: list | None = None
    # Where the backend's stdout/stderr goes. None discards it.
    backend_log_path: str | None = None

    def effective_backend_cmd(self) -> list:
        if self.backend_cmd is not None:
            return list(self.backend_cmd)
        return [
            sys.executable,
            "-m",
            "mlx_vlm",
            "server",
            "--model",
            self.model,
            "--host",
            self.backend_host,
            "--port",
            str(self.backend_port),
        ]


def config_from_env() -> ShimConfig:
    """Build a ShimConfig from GEMMA_* env vars (CLI flags win elsewhere)."""

    def _float(name: str, default: float) -> float:
        try:
            return float(os.environ.get(name, default))
        except ValueError:
            return default

    def _int(name: str, default: int) -> int:
        try:
            return int(os.environ.get(name, default))
        except ValueError:
            return default

    return ShimConfig(
        shim_port=_int("GEMMA_SHIM_PORT", DEFAULT_SHIM_PORT),
        backend_port=_int("GEMMA_BACKEND_PORT", DEFAULT_BACKEND_PORT),
        model=os.environ.get("GEMMA_MODEL", MODEL_ID),
        idle_timeout_s=_float("GEMMA_IDLE_TIMEOUT", DEFAULT_IDLE_TIMEOUT_S),
        startup_timeout_s=_float(
            "GEMMA_STARTUP_TIMEOUT", DEFAULT_STARTUP_TIMEOUT_S
        ),
    )


def _log(msg: str) -> None:
    print(f"GEMMA SHIM {msg}", file=sys.stderr, flush=True)


class GemmaShim:
    """The always-listening shim; owns exactly one backend process."""

    def __init__(self, config: ShimConfig):
        self.config = config
        # RLock: ensure_backend() holds the lock while calling
        # backend_state(), which locks again (a plain Lock deadlocks).
        self._lock = threading.RLock()
        self._state: str = "stopped"  # stopped | starting | ready
        self._proc: subprocess.Popen | None = None
        self._ready = threading.Event()
        self._in_flight = 0
        self._holds = 0
        self._last_activity = time.monotonic()
        self._server: ThreadingHTTPServer | None = None
        self._serve_thread: threading.Thread | None = None
        self._reap_thread: threading.Thread | None = None
        self._stopping = False

    # -- introspection (also what /_shim/status reports) ----------------

    def backend_state(self) -> str:
        with self._lock:
            if self._state == "ready" and self._proc is not None:
                if self._proc.poll() is not None:
                    self._state = "stopped"
                    self._proc = None
            return self._state

    def backend_proc(self) -> subprocess.Popen | None:
        with self._lock:
            return self._proc

    def status_dict(self) -> dict:
        with self._lock:
            idle_in = max(
                0.0,
                self.config.idle_timeout_s
                - (time.monotonic() - self._last_activity),
            )
            return {
                "backend": self._state,
                "backend_pid": (
                    self._proc.pid
                    if self._proc is not None and self._proc.poll() is None
                    else None
                ),
                "in_flight": self._in_flight,
                "holds": self._holds,
                "idle_in_s": round(idle_in, 1),
                "idle_timeout_s": self.config.idle_timeout_s,
                "model": self.config.model,
            }

    # -- lifecycle ------------------------------------------------------

    def start(self) -> None:
        handler = self._make_handler()
        self._server = ThreadingHTTPServer(
            (self.config.shim_host, self.config.shim_port), handler
        )
        self._server.daemon_threads = True
        self._serve_thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.2},
            daemon=True,
        )
        self._serve_thread.start()
        self._reap_thread = threading.Thread(
            target=self._reap_loop, daemon=True
        )
        self._reap_thread.start()
        _log(
            f"listening on {self.config.shim_host}:{self.config.shim_port} "
            f"-> backend {self.config.backend_host}:{self.config.backend_port}"
        )

    def stop(self) -> None:
        self._stopping = True
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        self._stop_backend_locked(reason="shim stopping")
        if self._serve_thread is not None:
            self._serve_thread.join(timeout=10)
        if self._reap_thread is not None:
            self._reap_thread.join(timeout=10)

    @property
    def base_url(self) -> str:
        return f"http://{self.config.shim_host}:{self.config.shim_port}"

    # -- backend management ----------------------------------------------

    def ensure_backend(self) -> None:
        """Start the backend if needed and wait until /health is healthy.

        Concurrent callers serialize on one spawn: exactly one process is
        ever started per cold period. Bounded by startup_timeout_s - raises
        BackendUnavailable, never blocks forever.
        """
        with self._lock:
            self.backend_state()  # reaps a backend that died on its own
            if self._state == "ready":
                return
            if self._state == "starting":
                starting = True
            else:
                starting = False
                self._state = "starting"
                self._ready.clear()
        if starting:
            if not self._ready.wait(timeout=self.config.startup_timeout_s):
                raise BackendUnavailable(
                    "backend did not become healthy within "
                    f"{self.config.startup_timeout_s:.0f}s"
                )
            with self._lock:
                if self._state != "ready":
                    raise BackendUnavailable(
                        "backend startup failed while waiting"
                    )
            return
        # This caller owns the spawn.
        try:
            self._spawn_and_wait()
        except Exception:
            with self._lock:
                proc = self._proc
                self._state = "stopped"
                self._proc = None
                self._ready.set()  # release fellow waiters to see failure
            # Never leak the half-started backend: a failed start must
            # still return its memory, or every failed cold start costs
            # gigabytes until someone notices.
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=10)
            raise
        with self._lock:
            self._state = "ready"
            self._last_activity = time.monotonic()
            self._ready.set()

    def _spawn_and_wait(self) -> None:
        cmd = self.config.effective_backend_cmd()
        log_path = self.config.backend_log_path
        if log_path:
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            log_file = open(log_path, "a", encoding="utf-8")  # noqa: PTH123
        else:
            log_file = open(os.devnull, "w", encoding="utf-8")  # noqa: PTH123
        with log_file:
            _log(f"cold start: {' '.join(cmd)}")
            proc = subprocess.Popen(
                cmd,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            with self._lock:
                self._proc = proc
            deadline = time.monotonic() + self.config.startup_timeout_s
            health_url = (
                f"http://{self.config.backend_host}:"
                f"{self.config.backend_port}/health"
            )
            last_error = "no attempt yet"
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    tail = self._read_log_tail(log_path)
                    raise BackendUnavailable(
                        f"backend exited with code {proc.returncode} "
                        f"during startup{tail}"
                    )
                try:
                    with urllib.request.urlopen(health_url, timeout=5) as resp:
                        if resp.status == 200:
                            _log(
                                "backend healthy "
                                f"(pid {proc.pid})"
                            )
                            return
                        last_error = f"health HTTP {resp.status}"
                except (urllib.error.URLError, OSError) as exc:
                    last_error = str(exc) or repr(exc)
                time.sleep(0.5)
            # Timeout: leave the process to the caller's except block,
            # which terminates it - a half-started backend is still ~GBs.
            raise BackendUnavailable(
                f"backend did not become healthy within "
                f"{self.config.startup_timeout_s:.0f}s "
                f"(last: {last_error})"
            )

    def _read_log_tail(self, log_path: str | None, lines: int = 20) -> str:
        if not log_path:
            return ""
        try:
            with open(log_path, encoding="utf-8", errors="replace") as fh:  # noqa: PTH123
                tail = "".join(fh.readlines()[-lines:]).strip()
        except OSError:
            return ""
        return f"; log tail:\n{tail}" if tail else ""

    def stop_backend_now(self) -> tuple[bool, str]:
        """Stop the backend immediately. Refuses while work is live.

        Returns (stopped, reason): (False, ...) with 409 semantics when
        in-flight requests or holds exist - a teardown must never kill
        live work. Idle-timeout reaping calls the same refusal check.
        """
        with self._lock:
            if self._in_flight > 0:
                return False, (
                    f"{self._in_flight} request(s) in flight"
                )
            if self._holds > 0:
                return False, f"{self._holds} hold(s) active"
            if self._state == "stopped":
                return True, "already stopped"
            self._stop_backend_locked(reason="requested")
            return True, "stopped"

    def _stop_backend_locked(self, reason: str) -> None:
        proc, self._proc = self._proc, None
        self._state = "stopped"
        if proc is None or proc.poll() is not None:
            return
        _log(f"stopping backend pid {proc.pid} ({reason})")
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _log(f"backend pid {proc.pid} ignored SIGTERM; killing")
            proc.kill()
            proc.wait(timeout=10)
        _log("backend stopped; ~7 GB released")

    def _reap_loop(self) -> None:
        while not self._stopping:
            time.sleep(self.config.reap_interval_s)
            if self._stopping:
                return
            with self._lock:
                idle_for = time.monotonic() - self._last_activity
                if (
                    self._state == "ready"
                    and self._in_flight == 0
                    and self._holds == 0
                    and idle_for >= self.config.idle_timeout_s
                ):
                    self._stop_backend_locked(
                        reason=f"idle {idle_for:.0f}s "
                        f"(timeout {self.config.idle_timeout_s:.0f}s)"
                    )

    # -- HTTP surface ----------------------------------------------------

    def _make_handler(self) -> type:
        shim = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "GemmaShim/1"
            protocol_version = "HTTP/1.1"

            def log_message(self, *args) -> None:  # noqa: ANN002, ANN202
                pass

            def _json(self, code: int, payload: dict) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)
                self.close_connection = True

            def do_GET(self) -> None:  # noqa: ANN202
                if self.path == "/health":
                    shim._handle_health(self)
                elif self.path == "/_shim/status":
                    self._json(200, shim.status_dict())
                else:
                    shim._handle_proxy(self)

            def do_POST(self) -> None:  # noqa: ANN202
                if self.path == "/_shim/hold":
                    self._drain_body()
                    shim._note_activity()
                    with shim._lock:
                        shim._holds += 1
                        holds = shim._holds
                    self._json(200, {"holds": holds})
                elif self.path == "/_shim/release":
                    self._drain_body()
                    with shim._lock:
                        shim._holds = max(0, shim._holds - 1)
                        holds = shim._holds
                    shim._note_activity()
                    self._json(200, {"holds": holds})
                elif self.path == "/_shim/stop-backend":
                    self._drain_body()
                    ok, reason = shim.stop_backend_now()
                    self._json(
                        200 if ok else 409, {"ok": ok, "reason": reason}
                    )
                elif self.path == "/_shim/prewarm":
                    self._drain_body()
                    try:
                        shim.ensure_backend()
                    except BackendUnavailable as exc:
                        shim._unavailable(self, str(exc))
                        return
                    self._json(200, {"backend": "ready"})
                else:
                    shim._handle_proxy(self)

            # POST owns /_shim/*; PUT/PATCH/DELETE always proxy.
            def do_PUT(self) -> None:  # noqa: ANN202
                shim._handle_proxy(self)

            def do_PATCH(self) -> None:  # noqa: ANN202
                shim._handle_proxy(self)

            def do_DELETE(self) -> None:  # noqa: ANN202
                shim._handle_proxy(self)

            def _drain_body(self) -> None:
                try:
                    n = int(self.headers.get("Content-Length", 0))
                except ValueError:
                    n = 0
                if n > 0:
                    self.rfile.read(n)

        return Handler

    def _note_activity(self) -> None:
        with self._lock:
            self._last_activity = time.monotonic()

    def _unavailable(
        self, handler: BaseHTTPRequestHandler, reason: str
    ) -> None:
        body = json.dumps(
            {
                "error": {
                    "message": (
                        "Gemma model server is not available: "
                        f"{reason}. Start it with `gemma up --prewarm` "
                        "or check `gemma status` for the backend log."
                    ),
                    "type": "server_unavailable",
                }
            }
        ).encode("utf-8")
        handler.send_response(503)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.wfile.write(body)
        handler.close_connection = True

    def _handle_health(self, handler: BaseHTTPRequestHandler) -> None:
        # /health answers WITHOUT starting the backend: a health check
        # must never cost a 16 s model load. When the backend is up, its
        # own /health is proxied verbatim so existing checks keep working.
        if self.backend_state() == "ready":
            self._handle_proxy(handler)
            return
        payload = {"status": "idle", "backend": "stopped"}
        payload.update(self.status_dict())
        body = json.dumps(payload).encode("utf-8")
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.wfile.write(body)
        handler.close_connection = True

    def _handle_proxy(self, handler: BaseHTTPRequestHandler) -> None:
        try:
            self.ensure_backend()
        except BackendUnavailable as exc:
            self._unavailable(handler, str(exc))
            return
        with self._lock:
            self._in_flight += 1
            self._last_activity = time.monotonic()
        try:
            self._proxy_to_backend(handler)
        finally:
            with self._lock:
                self._in_flight -= 1
                self._last_activity = time.monotonic()

    def _proxy_to_backend(self, handler: BaseHTTPRequestHandler) -> None:
        try:
            n = int(handler.headers.get("Content-Length", 0))
        except ValueError:
            n = 0
        body = handler.rfile.read(n) if n > 0 else None
        forward = {
            k: v
            for k, v in handler.headers.items()
            if k.lower() not in _HOP_BY_HOP and k.lower() != "host"
        }
        conn = http.client.HTTPConnection(
            self.config.backend_host, self.config.backend_port, timeout=300
        )
        try:
            conn.request(handler.command, handler.path, body=body, headers=forward)
            resp = conn.getresponse()
        except OSError as exc:
            # Backend died between health and proxy: one retry after
            # forcing a fresh start, then a clear 503 - never a hang.
            with self._lock:
                self._state = "stopped"
                self._proc = None
            try:
                self.ensure_backend()
            except BackendUnavailable as exc2:
                self._unavailable(handler, str(exc2))
                return
            try:
                conn = http.client.HTTPConnection(
                    self.config.backend_host,
                    self.config.backend_port,
                    timeout=300,
                )
                conn.request(
                    handler.command, handler.path, body=body, headers=forward
                )
                resp = conn.getresponse()
            except OSError as exc3:
                self._unavailable(handler, f"proxy failed: {exc3}")
                return
        try:
            handler.send_response(resp.status, resp.reason)
            for key, value in resp.getheaders():
                if key.lower() in _HOP_BY_HOP:
                    continue
                handler.send_header(key, value)
            handler.send_header("Connection", "close")
            handler.end_headers()
            shutil.copyfileobj(resp, handler.wfile)
        finally:
            conn.close()
        handler.close_connection = True


@contextlib.contextmanager
def server_scope(base_url: str, timeout: float = 10.0):
    """Hold the backend across a pipeline vision burst, then release it.

    A burst with gaps longer than the idle timeout would otherwise let
    the reaper stop the backend mid-batch. Usage::

        with server_scope("http://127.0.0.1:8080"):
            vision_model.get_model().analyze_images(...)
    """
    base = base_url.rstrip("/")

    def _call(path: str) -> dict:
        req = urllib.request.Request(
            f"{base}{path}",
            data=b"{}",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"gemma shim {path} refused: HTTP {exc.code} "
                f"{exc.read().decode('utf-8', 'replace')[:200]}"
            ) from exc

    _call("/_shim/hold")
    try:
        yield
    finally:
        try:
            _call("/_shim/release")
        except Exception as exc:  # release must not mask the batch error
            print(f"GEMMA SHIM release failed: {exc}", file=sys.stderr)


# -- `gemma` command: up / down / status over a detached shim -----------

STATE_ENV = "GEMMA_SHIM_STATE_FILE"


def _default_state_file() -> str:
    return os.environ.get(
        STATE_ENV, str(Path.home() / ".gemma-shim" / "state.json")
    )


def _default_log_file() -> str:
    return str(Path.home() / ".gemma-shim" / "shim.log")


def _read_state(state_file: str) -> dict:
    try:
        with open(state_file, encoding="utf-8") as fh:  # noqa: PTH123
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _write_state(state_file: str, state: dict) -> None:
    Path(state_file).parent.mkdir(parents=True, exist_ok=True)
    with open(state_file, "w", encoding="utf-8") as fh:  # noqa: PTH123
        json.dump(state, fh)


def _shim_base(state: dict, config: ShimConfig) -> str:
    port = state.get("shim_port", config.shim_port)
    return f"http://127.0.0.1:{port}"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (OSError, ValueError, OverflowError):
        return False
    return True


def _shim_answers(base: str) -> dict | None:
    try:
        with urllib.request.urlopen(
            f"{base}/_shim/status", timeout=5
        ) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def cmd_up(args: argparse.Namespace, config: ShimConfig) -> int:
    """Ensure the shim daemon is listening (starts it detached if not)."""
    state_file = _default_state_file()
    state = _read_state(state_file)
    pid = state.get("shim_pid")
    base = _shim_base(state, config)
    if isinstance(pid, int) and _pid_alive(pid) and _shim_answers(base):
        print(f"shim already running (pid {pid}) on {base}")
    else:
        repo_root = Path(__file__).resolve().parents[2]
        log_file = _default_log_file()
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        with open(log_file, "a", encoding="utf-8") as log:  # noqa: PTH123
            proc = subprocess.Popen(
                [sys.executable, "-m", "library.tools.gemma_shim", "run"],
                cwd=str(repo_root),
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        _write_state(
            state_file,
            {"shim_pid": proc.pid, "shim_port": config.shim_port},
        )
        base = f"http://127.0.0.1:{config.shim_port}"
        for _ in range(40):
            if _shim_answers(base) is not None:
                break
            time.sleep(0.5)
        else:
            print(
                f"shim did not answer on {base}; see {log_file}",
                file=sys.stderr,
            )
            return 1
        print(f"shim started (pid {proc.pid}) on {base}")
    if args.prewarm:
        req = urllib.request.Request(
            f"{base}/_shim/prewarm",
            data=b"{}",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=config.startup_timeout_s + 60) as resp:
                print(f"backend prewarmed: {resp.read().decode()}")
        except urllib.error.HTTPError as exc:
            print(
                f"backend failed to start: HTTP {exc.code} "
                f"{exc.read().decode('utf-8', 'replace')[:500]}",
                file=sys.stderr,
            )
            return 1
    print("provider URL stays http://127.0.0.1:8080/v1 (opencode unchanged)")
    return 0


def cmd_down(args: argparse.Namespace, config: ShimConfig) -> int:
    """Stop the backend now (refused while work is live); --all stops the shim."""
    state = _read_state(_default_state_file())
    base = _shim_base(state, config)
    status = _shim_answers(base)
    if status is None:
        print(f"shim not answering on {base} (already down?)")
        return 0 if args.all else 1
    req = urllib.request.Request(
        f"{base}/_shim/stop-backend",
        data=b"{}",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            print(f"backend: {resp.read().decode()}")
    except urllib.error.HTTPError as exc:
        print(
            f"refused: HTTP {exc.code} "
            f"{exc.read().decode('utf-8', 'replace')[:300]} "
            "(work is live; try again when idle)",
            file=sys.stderr,
        )
        return 1
    if args.all:
        pid = state.get("shim_pid")
        if isinstance(pid, int) and _pid_alive(pid):
            os.kill(pid, signal.SIGTERM)
            print(f"shim pid {pid} stopped; NOTE: opencode's provider "
                  "http://127.0.0.1:8080/v1 will refuse until `gemma up`.")
        Path(_default_state_file()).unlink(missing_ok=True)
    return 0


def cmd_status(args: argparse.Namespace, config: ShimConfig) -> int:
    """Shim + backend state and the backend's resident memory."""
    del args
    state = _read_state(_default_state_file())
    base = _shim_base(state, config)
    status = _shim_answers(base)
    if status is None:
        print(f"shim: down (nothing on {base}); `gemma up` to start")
        return 1
    print(f"shim: up on {base}")
    print(
        f"backend: {status.get('backend')} "
        f"(in_flight={status.get('in_flight')} "
        f"holds={status.get('holds')} "
        f"idle_in_s={status.get('idle_in_s')})"
    )
    pid = status.get("backend_pid")
    if pid:
        try:
            out = subprocess.run(
                ["ps", "-o", "rss=", "-p", str(pid)],
                capture_output=True,
                text=False,
                check=False,
                timeout=10,
            )
            rss_kb = int(out.stdout.decode().strip().split()[0])
            print(f"backend pid {pid}: RSS {rss_kb / 1024 / 1024:.1f} GB")
        except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
            print(f"backend pid {pid}: RSS unknown")
    else:
        print("backend: no process (~7 GB free)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gemma",
        description="On-demand Gemma model server (shim + idle reaper).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_up = sub.add_parser("up", help="ensure the shim is listening")
    p_up.add_argument(
        "--prewarm",
        action="store_true",
        help="load the model now instead of at the first request",
    )
    p_up.set_defaults(func=cmd_up)
    p_down = sub.add_parser("down", help="stop the backend now")
    p_down.add_argument(
        "--all",
        action="store_true",
        help="also stop the shim (opencode provider goes dark)",
    )
    p_down.set_defaults(func=cmd_down)
    p_status = sub.add_parser("status", help="shim + backend state")
    p_status.set_defaults(func=cmd_status)
    p_run = sub.add_parser("run", help="run the shim in the foreground")
    p_run.set_defaults(func=None)
    for p in (parser, p_up, p_run):
        p.add_argument("--shim-port", type=int, default=None)
        p.add_argument("--backend-port", type=int, default=None)
        p.add_argument("--idle-timeout", type=float, default=None)
    return parser


def main(argv: list | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = config_from_env()
    if args.shim_port is not None:
        config.shim_port = args.shim_port
    if args.backend_port is not None:
        config.backend_port = args.backend_port
    if args.idle_timeout is not None:
        config.idle_timeout_s = args.idle_timeout
    if args.command == "run":
        shim = GemmaShim(config)
        shim.start()
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        finally:
            shim.stop()
        return 0
    return args.func(args, config)


if __name__ == "__main__":
    raise SystemExit(main())
