"""The driver's Python client: spawn, call, element refs, error mapping.

The driver (`native/resolve-control`) speaks one JSON object per line on
stdin/stdout, one response per request, sequentially. This module is the
ONLY Python side of that protocol. It owns exactly four things:

* the process - spawn, detect death, respawn (the probe measured the
  driver's session dying mid-task: Resolve exited between two reads);
* request/response correlation - every call carries the request id, and
  a response whose id does not match the pending request is refused, not
  returned;
* the element-reference table - `find_element` and
  `get_accessibility_tree` return elements with an opaque `ref`;
  `press`, `set_value` and `focus` take that ref back. A ref this client
  never issued is a programmer error and is refused locally; a ref the
  DRIVER has invalidated (the AX tree changed) is the driver's
  `ELEMENT_STALE` to raise, and passes through untouched;
* error mapping - a driver refusal becomes a `DriverError` (a
  `RenRefusal`) carrying the driver's code, so the qualification gate,
  the foreground gate and the locked screen reach the caller the way
  every other Ren refusal does.

The client owns NO gates. Coordinates stay window-relative points, the
window frame and backing scale come back with every capture, and a
refusal from the driver is surfaced here, never worked around.

`RESOLVE_CONTROL_BIN` names the binary; tests point it at a mock. The
default is the release build beside the checkout, and a missing binary
is a refusal naming the path to build, not a silent skip.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Self

from library.tools.ren_refusal import RenRefusal

#: Where the driver binary is built (`cargo build --release` in
#: `native/resolve-control`). An explicit environment wins, so a test or
#: a machine with the binary elsewhere does not need a checkout.
ENV_BIN = "RESOLVE_CONTROL_BIN"

DEFAULT_BIN = ("native", "target", "release", "resolve-control")

#: Methods that return elements with a `ref` field the caller passes
#: back to `press`, `set_value` or `focus`.
REF_PRODUCING = frozenset({"find_element", "get_accessibility_tree"})

#: Methods that take an element ref. The ref must be one this client
#: issued; the driver decides whether it is still valid.
REF_CONSUMING = frozenset({"press", "set_value", "focus"})


class DriverError(RenRefusal):
    """A refusal the driver raised.

    `driver_code` is the driver's own code - the vocabulary a caller
    switches on (`PROJECT_NOT_QUALIFICATION`, `FOREGROUND_NOT_ALLOWED`,
    `OUTSIDE_RESOLVE`, `SCREEN_LOCKED`, `PERMISSION_DENIED`,
    `ELEMENT_NOT_FOUND`, `ELEMENT_STALE`, `ACTION_NOT_SUPPORTED`,
    `CAPTURE_FAILED`, `OCR_FAILED`, `RESOLVE_NOT_RUNNING`,
    `INVALID_PARAMS`, `INTERNAL`). `code` is the namespaced machine code
    (`DRIVER_<driver_code>`) the support bundle and structured log key
    off, kept in `RenRefusal.code` as every refusal's is. The message is
    the driver's own, which names the open project or the permission to
    grant. Nothing here interprets or softens it.
    """

    def __init__(self, driver_code: str, message: str):
        self.driver_code = driver_code
        super().__init__(
            f"the driver refused: {message}",
            f"the driver answered {driver_code}",
            "the fix is the driver's to make - see the message it sent",
            code=f"DRIVER_{driver_code}",
        )


class DriverDead(RenRefusal):
    """The driver process died and could not be respawned to answer."""


def default_binary() -> Path:
    """The release build beside this checkout, whatever the cwd."""
    from ren.engine_root import require_engine_root

    root = require_engine_root()
    return root.joinpath(*DEFAULT_BIN)


def resolve_binary() -> Path:
    """The binary to spawn: `$RESOLVE_CONTROL_BIN`, else the build.

    A missing binary is a refusal naming the build command - the driver
    is a build product, and skipping it would mock the whole layer.
    """
    override = os.environ.get(ENV_BIN, "")
    if override:
        path = Path(override)
        if not path.is_file():
            raise DriverDead(
                f"the driver binary {override} does not exist",
                f"${ENV_BIN} names a file that is not there",
                f"build it: cargo build --release in native/resolve-control, "
                f"or point ${ENV_BIN} at the binary you have",
            )
        return path
    path = default_binary()
    if not path.is_file():
        raise DriverDead(
            f"the driver binary is not built at {path}",
            "cargo build --release has not run in native/resolve-control",
            "run: cargo build --release in native/resolve-control",
        )
    return path


class DriverClient:
    """One driver process, one pending request at a time.

    The driver is single-threaded and sequential, so a client never has
    two requests in flight; the id check below is the guard against a
    response from a previous life of the process being returned for the
    current call after a respawn.
    """

    def __init__(self, binary: Path | None = None):
        self._refresh_native = binary is None and not os.environ.get(ENV_BIN)
        self._binary = binary if binary is not None else resolve_binary()
        self._process: subprocess.Popen | None = None
        self._refs: dict[str, dict] = {}
        self._last_id = 0

    # ── process ────────────────────────────────────────────────────

    def _spawn(self) -> subprocess.Popen:
        # A session start is a safe point: refresh the driver build
        # first, while it is not running. Best-effort - a refresh
        # failure must not break the spawn it precedes.
        if self._refresh_native:
            try:
                from library.tools import native_refresh
                from ren.engine_root import require_engine_root
                native_refresh.ensure_current(require_engine_root())
            except Exception:  # noqa: BLE001 - refresh never breaks a spawn
                pass
        process = subprocess.Popen(
            [str(self._binary)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        self._process = process
        return process

    def _ensure(self) -> subprocess.Popen:
        if self._process is None or self._process.poll() is not None:
            self._close_pipe()
            return self._spawn()
        return self._process

    def _close_pipe(self) -> None:
        proc, self._process = self._process, None
        if proc is None:
            return
        try:
            proc.stdin.close()  # type: ignore[union-attr]
        except (OSError, ValueError):
            pass
        try:
            proc.stdout.close()  # type: ignore[union-attr]
        except (OSError, ValueError):
            pass
        # Reap: a dead-but-unwaited driver is a zombie the broker's
        # process table carries. Bounded like close().
        try:
            proc.wait(timeout=self.CLOSE_WAIT_SECONDS)
        except Exception:  # noqa: BLE001 - TimeoutExpired or a dead handle
            pass

    #: How long a closing driver gets to exit on stdin EOF before it is
    #: terminated, and how long a terminated one gets before SIGKILL. A
    #: healthy driver exits on EOF promptly (observed instant in tests);
    #: the bounds cover a winding-down system call, and a close that
    #: only drops pipes leaves zombies (dead, unwaited) or orphans
    #: (stuck mid-call, reparented - measured hours-old with ppid
    #: broker). Operational bounds, recorded here.
    CLOSE_WAIT_SECONDS = 5.0

    def close(self) -> None:
        """End the session and reap the driver process.

        stdin EOF ends a healthy driver; then wait bounded, terminate,
        wait, kill - every stage reaped, nothing left for the broker to
        trip over. The driver holds no state past its process.
        """
        proc, self._process = self._process, None
        if proc is None:
            self._refs.clear()
            return
        try:
            proc.stdin.close()  # type: ignore[union-attr]
        except (OSError, ValueError):
            pass
        try:
            proc.stdout.close()  # type: ignore[union-attr]
        except (OSError, ValueError):
            pass
        try:
            proc.wait(timeout=self.CLOSE_WAIT_SECONDS)
            self._refs.clear()
            return
        except Exception:  # noqa: BLE001 - TimeoutExpired or a dead handle
            pass
        try:
            proc.terminate()
            proc.wait(timeout=self.CLOSE_WAIT_SECONDS)
        except Exception:  # noqa: BLE001 - already gone, or still stuck
            try:
                proc.kill()
                proc.wait(timeout=self.CLOSE_WAIT_SECONDS)
            except Exception:  # noqa: BLE001 - nothing more to do
                pass
        self._refs.clear()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # ── refs ───────────────────────────────────────────────────────

    def _store_refs(self, result: dict) -> None:
        elements = result.get("elements")
        if elements is None:
            tree = result.get("tree")
            if tree is not None and tree.get("ref"):
                self._refs[tree["ref"]] = tree
            return
        for element in elements:
            ref = element.get("ref")
            if ref:
                self._refs[ref] = element

    def _check_ref(self, method: str, params: dict) -> None:
        ref = params.get("element")
        if ref is None:
            return
        if ref not in self._refs:
            raise DriverError(
                "ELEMENT_NOT_FOUND",
                f"{method} was given element {ref!r}, which this client "
                f"never issued - call find_element first",
            )

    # ── the call ───────────────────────────────────────────────────

    def call(self, method: str, params: dict | None = None) -> dict:
        """One request, one response. Raises `DriverError` on refusal.

        A dead process is respawned once and the call retried: the probe
        measured the driver's session ending mid-task (Resolve exited
        between two reads), and a client that wedges on a dead stdin
        turns a recoverable death into a hung run.
        """
        body = dict(params or {})
        if method in REF_CONSUMING:
            self._check_ref(method, body)
        try:
            return self._exchange(method, body)
        except (BrokenPipeError, OSError, EOFError):
            self._close_pipe()
            try:
                return self._exchange(method, body)
            except (BrokenPipeError, OSError, EOFError) as exc:
                raise DriverDead(
                    f"the driver process died answering {method} and "
                    f"could not be respawned",
                    f"{type(exc).__name__}: {exc}",
                    "run it by hand: cargo run --release in "
                    "native/resolve-control, and read what it prints",
                ) from exc

    def _exchange(self, method: str, body: dict) -> dict:
        self._last_id += 1
        request = {"id": self._last_id, "method": method, "params": body}
        process = self._ensure()
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(json.dumps(request) + "\n")
        process.stdin.flush()
        line = process.stdout.readline()
        if not line:
            raise EOFError("the driver closed its stdout")
        response = json.loads(line)
        if response.get("id") != request["id"]:
            raise DriverError(
                "INTERNAL",
                f"the driver answered request {response.get('id')} for "
                f"request {request['id']}",
            )
        if not response.get("ok", False):
            error = response.get("error") or {}
            raise DriverError(
                str(error.get("code", "INTERNAL")),
                str(error.get("message", "the driver refused without a message")),
            )
        result = response.get("result")
        if not isinstance(result, dict):
            raise DriverError(
                "INTERNAL",
                f"{method} returned no result object",
            )
        if method in REF_PRODUCING:
            self._store_refs(result)
        return result
