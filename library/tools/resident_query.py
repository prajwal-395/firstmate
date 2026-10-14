"""Opt-in resident query worker (scout item 6, second half).

A user-session process that pays the embedder load once and answers
repeated identical requests warm, behind a Unix socket in a runtime dir
the invoker names. Nothing starts it automatically: it runs only when
asked (`serve`), holds no Resolve lease, touches no timeline, and exits
after a bounded idle interval so a forgotten worker cannot linger
beside Resolve, ffmpeg and the ML workers.

What it keeps warm, and what it reloads:

- the embedder loads lazily on the first query and never again for the
  worker's lifetime - 20 subsequent identical requests must not reload
  it (the contract test pins exactly this with a fake 2 s load);
- the index reloads whenever its recorded fingerprint moves under it
  (a rebuilt index is picked up without a restart, and without paying
  the embedder load again) - a stale fingerprint recovers by reload,
  never by serving stale rows;
- a restart is a fresh generation: the next query reloads both, pays
  load once, and carries on;
- idle eviction exits the process cleanly; the next client request
  starts a new worker rather than meeting a dead socket.

The fake backend (`--embedder fake`) exists so the whole lifecycle is
provable without a model: controlled load/encode sleeps over a tiny
JSON index. The real backends (`minilm`, `eg2`) serve the project's own
footage index through `FootageIndex.search_report`. Enabling anything
beyond opt-in use waits on the one real-model benchmark pass
(`library/tools/query_benchmark.py --real`).

Usage:

    python3 -m library.tools.resident_query serve --socket-dir DIR \\
        --embedder fake --index-file IDX.json --idle-seconds 300
    python3 -m library.tools.resident_query query "parking" --socket-dir DIR
    python3 -m library.tools.resident_query status --socket-dir DIR
    python3 -m library.tools.resident_query stop --socket-dir DIR

Protocol: one JSON object per connection, one JSON reply, then close.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

from library.tools.ren_refusal import RenRefusal

SOCKET_NAME = "query.sock"
PID_NAME = "worker.pid"
META_NAME = "worker.json"
PROTOCOL_VERSION = 1
DEFAULT_IDLE_SECONDS = 300.0
MAX_SOCKET_PATH_LEN = 100
ACCEPT_TIMEOUT_S = 0.5
START_TIMEOUT_S = 30.0

REAL_EMBEDDERS = ("minilm", "eg2")


def default_socket_dir() -> str:
    """Where the socket lives when the invoker names no dir."""
    override = os.environ.get("REN_RESIDENT_QUERY_SOCKDIR")
    if override:
        return override
    return os.path.join(tempfile.gettempdir(), f"ren-resident-query-{os.getuid()}")


def socket_path_for(socket_dir) -> str:
    """The socket path for a runtime dir."""
    return str(Path(socket_dir) / SOCKET_NAME)


def _rss_self_mb() -> float:
    """This worker process's peak RSS in MiB; -1 when unmeasurable."""
    try:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if sys.platform == "darwin":
            return round(peak / 1024 / 1024, 2)
        return round(peak / 1024, 2)
    except Exception:  # noqa: BLE001 - RSS is telemetry, never fatal
        return -1.0


class StaleSocket(ConnectionError):
    """The socket path answers to nobody: no worker is listening."""


def _send_receive(socket_path: str, payload: dict, timeout: float = 30.0) -> dict:
    """Send one request object, read one reply object."""
    if len(socket_path) > MAX_SOCKET_PATH_LEN:
        raise StaleSocket(
            f"socket path is {len(socket_path)} characters - no worker "
            f"could ever bind at {socket_path}; use a shorter "
            f"--socket-dir"
        )
    raw = (json.dumps(payload) + "\n").encode("utf-8")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        try:
            sock.connect(socket_path)
        except (FileNotFoundError, ConnectionRefusedError) as exc:
            raise StaleSocket(f"no resident query worker at {socket_path}") from exc
        except OSError as exc:
            if exc.errno == errno.ENAMETOOLONG:
                raise StaleSocket(
                    f"socket path is {len(socket_path)} characters - "
                    f"no worker could ever bind at {socket_path}; use a "
                    f"shorter --socket-dir"
                ) from exc
            raise
        sock.sendall(raw)
        try:
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        chunks = []
        while True:
            try:
                piece = sock.recv(65536)
            except TimeoutError as exc:
                raise TimeoutError(
                    f"resident query worker at {socket_path} "
                    f"did not answer in {timeout}s"
                ) from exc
            if not piece:
                break
            chunks.append(piece)
    finally:
        sock.close()
    try:
        return json.loads(b"".join(chunks).decode("utf-8"))
    except ValueError as exc:
        raise RuntimeError(
            f"resident query worker at {socket_path} answered unparsable bytes"
        ) from exc


def query(socket_dir, text: str, top_k: int = 5, timeout: float = 30.0) -> dict:
    """Ask the running worker one query. Raises StaleSocket when idle."""
    return _send_receive(
        socket_path_for(socket_dir),
        {"op": "query", "text": text, "top_k": top_k},
        timeout=timeout,
    )


def status(socket_dir, timeout: float = 10.0) -> dict:
    """Read the running worker's generations, counts and RSS."""
    return _send_receive(socket_path_for(socket_dir), {"op": "status"}, timeout=timeout)


def spawn_worker(
    socket_dir, extra_args: list | None = None, cwd: str | None = None, log_path=None
):
    """Start a worker as a child process. Returns the Popen handle."""
    args = [
        sys.executable,
        "-m",
        "library.tools.resident_query",
        "serve",
        "--socket-dir",
        str(socket_dir),
    ]
    args.extend(extra_args or [])
    if log_path is not None:
        with open(log_path, "ab") as log_file:
            return subprocess.Popen(args, stdout=log_file, stderr=log_file, cwd=cwd)
    return subprocess.Popen(
        args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=cwd
    )


def wait_until_ready(socket_dir, timeout: float = START_TIMEOUT_S) -> dict:
    """Poll `status` until the worker answers. Returns its status."""
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return status(socket_dir, timeout=2.0)
        except (
            StaleSocket,
            TimeoutError,
            RuntimeError,
            ConnectionError,
            OSError,
        ) as exc:
            last = exc
            time.sleep(0.1)
    raise RuntimeError(
        f"resident query worker in {socket_dir} never became ready: {last}"
    )


def ensure_started(
    socket_dir,
    extra_args: list | None = None,
    cwd: str | None = None,
    timeout: float = START_TIMEOUT_S,
):
    """Return a live worker: reuse the socket's owner or spawn one."""
    try:
        status(socket_dir, timeout=2.0)
        return None
    except (StaleSocket, TimeoutError, RuntimeError, ConnectionError, OSError):
        pass
    proc = spawn_worker(socket_dir, extra_args=extra_args, cwd=cwd)
    try:
        wait_until_ready(socket_dir, timeout=timeout)
    except Exception:
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001, S110 - already gone is fine
            pass
        raise
    return proc


def _pid_exited(pid: int) -> bool:
    """Whether a pid has exited; a zombie counts as exited.

    `os.kill(pid, 0)` still succeeds for a zombie - the worker has
    already exited but its spawner has not reaped it yet (the test
    fixture reaps with `proc.wait()` right after `stop()`). Waiting
    on the reap measured six ~10 s teardowns in one file, so a
    zombie reads as gone here; the spawner's own wait still owns
    the leak check, which this does not weaken.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    try:
        out = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True, encoding="utf-8",
            check=False).stdout.strip()
        if out:
            return out.startswith("Z")
    except OSError:
        pass
    return False


def stop(socket_dir, timeout: float = 10.0) -> dict:
    """Ask the worker to exit, then wait for its PID to be gone."""
    try:
        reply = _send_receive(
            socket_path_for(socket_dir), {"op": "shutdown"}, timeout=timeout
        )
    except StaleSocket:
        return {"ok": True, "stopped": False, "note": "no worker was listening"}
    pid_file = Path(socket_dir) / PID_NAME
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return reply
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _pid_exited(pid):
            break
        time.sleep(0.05)
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    return reply


def _tokenize(text: str) -> list:
    return [
        t
        for t in "".join(
            ch.lower() if ch.isalnum() else " " for ch in (text or "")
        ).split()
        if t
    ]


class WorkerServer:
    """The serving half: lazy embedder, fingerprinted index, idle exit."""

    def __init__(
        self,
        socket_dir,
        embedder: str = "fake",
        index_file=None,
        project=None,
        index_dir=None,
        fake_load_s: float = 2.0,
        fake_encode_s: float = 0.02,
        idle_seconds: float = DEFAULT_IDLE_SECONDS,
        budget_mb: float = 512.0,
    ) -> None:
        self.socket_dir = Path(socket_dir)
        self.socket_path = socket_path_for(self.socket_dir)
        if len(self.socket_path) > MAX_SOCKET_PATH_LEN:
            raise RenRefusal(
                f"socket path is {len(self.socket_path)} characters",
                "Unix socket paths longer than ~104 characters cannot bind",
                f"pass a shorter --socket-dir (under "
                f"{MAX_SOCKET_PATH_LEN - len(SOCKET_NAME) - 1} characters)",
            )
        if embedder not in ("fake", *REAL_EMBEDDERS):
            raise RenRefusal(
                f"unknown embedder {embedder!r}",
                "the worker serves one named encoder",
                "use --embedder fake, minilm or eg2",
            )
        self.embedder_kind = embedder
        self.index_file = Path(index_file) if index_file else None
        self.project = project
        self.index_dir = index_dir
        self.fake_load_s = fake_load_s
        self.fake_encode_s = fake_encode_s
        self.idle_seconds = idle_seconds
        self.budget_mb = budget_mb
        self.generation = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.started_at = time.time()
        self._lock = threading.Lock()
        self._embedder = None
        self._model_loads = 0
        self._index = None
        self._index_fingerprint = None
        self._index_loads = 0
        self._index_generation = 0
        self._queries = 0
        self._last_activity = time.monotonic()
        self._stopping = threading.Event()

    # ── loading ──

    def _ensure_embedder(self) -> None:
        """Load the encoder once per worker lifetime."""
        if self._embedder is not None:
            return
        if self.embedder_kind == "fake":
            time.sleep(self.fake_load_s)
            self._embedder = "fake"
        elif self.embedder_kind == "minilm":
            from library.tools.analysis import footage_query

            encode, backend = footage_query.load_embedder()
            if encode is None:
                raise RenRefusal(
                    "no text embedder is available",
                    f"the MiniLM loader answered: {backend}",
                    "install sentence-transformers or run with --embedder fake",
                )
            self._embedder = ("minilm", encode, backend)
        else:
            from library.tools.analysis import footage_query

            encode, backend = footage_query.load_eg2_text_embedder()
            if encode is None:
                raise RenRefusal(
                    "no EmbeddingGemma text embedder is available",
                    f"the EG2 loader answered: {backend}",
                    "install sentence-transformers>=6.1 or run with --embedder fake",
                )
            self._embedder = ("eg2", encode, backend)
        self._model_loads += 1

    def _current_fingerprint(self) -> str | None:
        """The index fingerprint on disk right now, or None when absent."""
        if self.embedder_kind == "fake":
            if self.index_file is None or not self.index_file.is_file():
                return None
            try:
                payload = json.loads(self.index_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
            if payload.get("fingerprint"):
                return str(payload["fingerprint"])
            digest = hashlib.sha256(self.index_file.read_bytes()).hexdigest()
            return f"sha256:{digest[:16]}"
        from library.tools.analysis import footage_segments

        try:
            return footage_segments.ingest_fingerprint(self.project)
        except Exception:  # noqa: BLE001 - unreadable ingest is "absent"
            return None

    def _load_index(self) -> None:
        """(Re)read the index payload. Only runs on fingerprint change."""
        if self.embedder_kind == "fake":
            assert self.index_file is not None
            try:
                payload = json.loads(self.index_file.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise RenRefusal(
                    f"no readable query index at {self.index_file}",
                    f"the worker keeps no index of its own: {exc}",
                    "write the JSON index first, or point --index-file at one",
                ) from exc
            self._index = payload
            fingerprint = payload.get("fingerprint")
            if not fingerprint:
                digest = hashlib.sha256(self.index_file.read_bytes()).hexdigest()
                fingerprint = f"sha256:{digest[:16]}"
            self._index_fingerprint = str(fingerprint)
        else:
            from library.tools.analysis import footage_query

            self._index = footage_query.FootageIndex(self.project, self.index_dir)
            try:
                self._index_fingerprint = self._index.payload.get("ingest_fingerprint")
            except RenRefusal:
                raise
            except Exception as exc:
                raise RenRefusal(
                    f"no readable query index for {self.project}",
                    f"the worker keeps no index of its own: {exc}",
                    f"build it first: ren search-index '{self.project}'",
                ) from exc
        self._index_loads += 1
        self._index_generation += 1

    def _ensure_index(self) -> str | None:
        """Reload when the on-disk fingerprint moved. Returns why, if so."""
        current = self._current_fingerprint()
        if current is None:
            if self._index is None:
                self._load_index()
            return None
        if self._index is None or current != self._index_fingerprint:
            had = self._index is not None
            self._load_index()
            return "stale-index" if had else None
        return None

    # ── answering ──

    def _fake_search(self, text: str, top_k: int) -> list:
        """Token-overlap ranking over the fixture documents."""
        time.sleep(self.fake_encode_s)
        wanted = set(_tokenize(text))
        scored = []
        for doc in self._index.get("documents") or []:
            overlap = len(wanted & set(_tokenize(doc.get("text", ""))))
            scored.append((-overlap, doc.get("id", "?"), doc.get("text", "")))
        scored.sort()
        return [
            {"id": doc_id, "text": doc_text, "overlap": -neg}
            for neg, doc_id, doc_text in scored[:top_k]
        ]

    def _answer_query(self, text: str, top_k: int) -> dict:
        with self._lock:
            first = self._embedder is None
            self._ensure_embedder()
            reloaded = self._ensure_index()
            if self.embedder_kind == "fake":
                hits = self._fake_search(text, top_k)
                answer: dict = {"hits": hits, "backend": "fake"}
            else:
                try:
                    report = self._index.search_report(text, top_k=top_k)
                except RenRefusal as exc:
                    return {"ok": False, **exc.to_dict()}
                answer = {
                    "hits": report.get("results", []),
                    "abstained": report.get("abstained"),
                    "backend": report.get("embed_backend"),
                }
            self._queries += 1
            self._last_activity = time.monotonic()
            answer.update(
                {
                    "ok": True,
                    "cold": first,
                    "reloaded": reloaded,
                    "model_loads": self._model_loads,
                    "index_loads": self._index_loads,
                    "generation": self.generation,
                    "index_generation": self._index_generation,
                    "index_fingerprint": self._index_fingerprint,
                    "rss_mb": _rss_self_mb(),
                }
            )
            return answer

    def _answer_status(self) -> dict:
        with self._lock:
            rss = _rss_self_mb()
            return {
                "ok": True,
                "protocol": PROTOCOL_VERSION,
                "generation": self.generation,
                "pid": os.getpid(),
                "uptime_s": round(time.time() - self.started_at, 2),
                "queries": self._queries,
                "model_loads": self._model_loads,
                "index_loads": self._index_loads,
                "index_generation": self._index_generation,
                "index_fingerprint": self._index_fingerprint,
                "embedder": self.embedder_kind,
                "idle_seconds": self.idle_seconds,
                "rss_mb": rss,
                "budget_mb": self.budget_mb,
                "over_budget": rss >= 0 and rss > self.budget_mb,
            }

    def _handle(self, conn: socket.socket) -> None:
        try:
            chunks = []
            while True:
                piece = conn.recv(65536)
                if not piece:
                    break
                chunks.append(piece)
            try:
                request = json.loads(b"".join(chunks).decode("utf-8"))
            except ValueError:
                reply: dict = {
                    "ok": False,
                    "code": "REN_BAD_REQUEST",
                    "what": "the worker got bytes it cannot parse",
                    "why": "every request is one JSON object",
                    "fix": "send one JSON line per connection",
                }
            else:
                op = request.get("op")
                if op == "query":
                    try:
                        reply = self._answer_query(
                            str(request.get("text", "")), int(request.get("top_k", 5))
                        )
                    except RenRefusal as exc:
                        reply = {"ok": False, **exc.to_dict()}
                elif op == "status":
                    reply = self._answer_status()
                elif op == "shutdown":
                    reply = {"ok": True, "stopped": True, "generation": self.generation}
                    self._stopping.set()
                else:
                    reply = {
                        "ok": False,
                        "code": "REN_BAD_REQUEST",
                        "what": f"unknown op {op!r}",
                        "why": "the worker answers query, status and shutdown",
                        "fix": 'send {"op": "query", "text": "..."}',
                    }
            conn.sendall((json.dumps(reply) + "\n").encode("utf-8"))
        except (OSError, RuntimeError):
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    # ── serving ──

    def serve_forever(self) -> int:
        """Bind, answer until shutdown or idle eviction, clean up."""
        self.socket_dir.mkdir(parents=True, exist_ok=True)
        try:
            existing = _send_receive(self.socket_path, {"op": "status"}, timeout=2.0)
            raise RenRefusal(
                f"a resident query worker already owns {self.socket_path}",
                f"its generation is {existing.get('generation')}",
                f"query it, or run `stop --socket-dir {self.socket_dir}` first",
            )
        except StaleSocket:
            pass
        try:
            Path(self.socket_path).unlink()
        except OSError:
            pass
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            server.bind(self.socket_path)
        except OSError as exc:
            raise RenRefusal(
                f"cannot bind the query socket at {self.socket_path}",
                f"the bind failed: {exc}",
                "pass a writable --socket-dir with a short path",
            ) from exc
        server.listen(16)
        server.settimeout(ACCEPT_TIMEOUT_S)
        (self.socket_dir / PID_NAME).write_text(str(os.getpid()), encoding="utf-8")
        (self.socket_dir / META_NAME).write_text(
            json.dumps(
                {
                    "generation": self.generation,
                    "started_at": self.started_at,
                    "embedder": self.embedder_kind,
                }
            ),
            encoding="utf-8",
        )
        try:
            while not self._stopping.is_set():
                try:
                    conn, _ = server.accept()
                except TimeoutError:
                    pass
                else:
                    thread = threading.Thread(
                        target=self._handle, args=(conn,), daemon=True
                    )
                    thread.start()
                if (
                    self.idle_seconds > 0
                    and self._queries > 0
                    and (time.monotonic() - self._last_activity > self.idle_seconds)
                ):
                    break
        finally:
            try:
                server.close()
            except OSError:
                pass
            for name in (SOCKET_NAME, PID_NAME, META_NAME):
                try:
                    (self.socket_dir / name).unlink()
                except OSError:
                    pass
        return 0


def _serve_command(args) -> int:
    try:
        server = WorkerServer(
            args.socket_dir,
            embedder=args.embedder,
            index_file=args.index_file,
            project=args.project,
            index_dir=args.index_dir,
            fake_load_s=args.fake_load_s,
            fake_encode_s=args.fake_encode_s,
            idle_seconds=args.idle_seconds,
            budget_mb=args.budget_mb,
        )
    except RenRefusal as exc:
        print(exc.render(), file=sys.stderr)
        return 4
    try:
        return server.serve_forever()
    except RenRefusal as exc:
        print(exc.render(), file=sys.stderr)
        return 4


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="resident_query",
        description="Opt-in resident footage-query worker: serve warm "
        "answers over a local socket, or ask one.",
    )
    socket_opt = argparse.ArgumentParser(add_help=False)
    socket_opt.add_argument(
        "--socket-dir",
        default=None,
        help="runtime dir holding the socket (default: "
        "$REN_RESIDENT_QUERY_SOCKDIR or a user "
        "temp dir)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_serve = sub.add_parser(
        "serve", parents=[socket_opt], help="run the worker (opt-in only)"
    )
    p_serve.add_argument(
        "--embedder", default="fake", choices=("fake", *REAL_EMBEDDERS)
    )
    p_serve.add_argument("--index-file", help="fixture JSON index for --embedder fake")
    p_serve.add_argument("--project", help="Ren project for real backends")
    p_serve.add_argument("--index-dir", help="index dir override")
    p_serve.add_argument("--fake-load-s", type=float, default=2.0)
    p_serve.add_argument("--fake-encode-s", type=float, default=0.02)
    p_serve.add_argument(
        "--idle-seconds",
        type=float,
        default=DEFAULT_IDLE_SECONDS,
        help="exit after this long idle (0: never)",
    )
    p_serve.add_argument("--budget-mb", type=float, default=512.0)

    p_query = sub.add_parser(
        "query", parents=[socket_opt], help="ask the running worker once"
    )
    p_query.add_argument("text")
    p_query.add_argument("--top-k", type=int, default=5)
    p_query.add_argument("--timeout", type=float, default=30.0)
    p_query.add_argument("--json", action="store_true")

    sub.add_parser("status", parents=[socket_opt], help="read the worker's state")
    sub.add_parser("stop", parents=[socket_opt], help="ask the worker to exit")

    args = parser.parse_args(argv)
    socket_dir = args.socket_dir or default_socket_dir()
    if args.command == "serve":
        args.socket_dir = socket_dir
        return _serve_command(args)
    if args.command == "query":
        try:
            reply = query(socket_dir, args.text, top_k=args.top_k, timeout=args.timeout)
        except StaleSocket as exc:
            print(
                f"ren: refused - no resident query worker is listening\n"
                f"  what: {exc}\n"
                f"  why: the worker runs only when asked, and nothing "
                f"started one here\n"
                f"  fix: start one with serve --socket-dir {socket_dir}",
                file=sys.stderr,
            )
            return 4
        if args.json:
            print(json.dumps(reply, sort_keys=True))
        else:
            for hit in reply.get("hits", []):
                print(json.dumps(hit, sort_keys=True))
            if not reply.get("ok", True):
                print(json.dumps(reply, sort_keys=True), file=sys.stderr)
                return 4
        return 0
    if args.command == "status":
        try:
            print(json.dumps(status(socket_dir), indent=2, sort_keys=True))
        except StaleSocket as exc:
            print(
                f"ren: refused - no resident query worker is listening\n"
                f"  what: {exc}\n"
                f"  why: the worker runs only when asked\n"
                f"  fix: start one with serve --socket-dir {socket_dir}",
                file=sys.stderr,
            )
            return 4
        return 0
    if args.command == "stop":
        print(json.dumps(stop(socket_dir), sort_keys=True))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
