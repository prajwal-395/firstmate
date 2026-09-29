"""Shared machine lock for work that competes for local CPU, memory or GPU.

The owner file carries a random token in ``VEP_HEAVY_WORK_OWNER``. Child
processes inherit it and can re-enter the lock without waiting on their own
parent. Lock directories are never reclaimed automatically: a waiter will
name the recorded owner and wait rather than risk deleting a live owner's
lock.

When an operation needs both this lock and Resolve's instance lease, it
acquires the Resolve lease first and this lock second. Waiting on Resolve
while holding the machine-wide heavy-work lock would block unrelated heavy
work and could deadlock a caller that takes them in the opposite order.
"""

from __future__ import annotations

import functools
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, TypeVar


HEAVY_LOCK_DIR = Path.home() / ".local" / "share" / "vep" / "heavy-work.lock"
OWNER_FILE = "owner"
OWNER_ENV = "VEP_HEAVY_WORK_OWNER"
POLL_SECONDS = 1.0

_state_guard = threading.Lock()
_held_state: dict | None = None
_F = TypeVar("_F", bound=Callable)


def _read_owner() -> dict:
    try:
        raw = (HEAVY_LOCK_DIR / OWNER_FILE).read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        value = json.loads(raw)
    except ValueError:
        # Read the original eval_harness format while an older lane still
        # holds the machine lock.
        return {"owner": raw.strip()} if raw.strip() else {}
    return value if isinstance(value, dict) else {}


def _owner_label(owner: dict) -> str:
    if not owner:
        return "unknown owner"
    label = str(owner.get("owner") or "unknown owner")
    pid = owner.get("pid")
    host = owner.get("host")
    if pid is not None:
        label += f" (pid {pid}"
        if host:
            label += f"@{host}"
        label += ")"
    return label


def _current_process_owns_lock() -> bool:
    token = os.environ.get(OWNER_ENV)
    return bool(token and _read_owner().get("token") == token)


def take_heavy_lock(owner: str) -> None:
    """Acquire the machine lock, re-entering when this owner already holds it."""
    global _held_state
    with _state_guard:
        if _held_state is not None:
            _held_state["depth"] += 1
            return

        inherited_token = os.environ.get(OWNER_ENV)
        if inherited_token and _read_owner().get("token") == inherited_token:
            _held_state = {
                "depth": 1,
                "token": inherited_token,
                "owned": False,
                "previous_env": inherited_token,
            }
            return

        HEAVY_LOCK_DIR.parent.mkdir(parents=True, exist_ok=True)
        announced = False
        ownerless_since = None
        while True:
            try:
                HEAVY_LOCK_DIR.mkdir(exist_ok=False)
                break
            except FileExistsError:
                current = _read_owner()
                if current:
                    if not announced:
                        print("heavy-work: waiting for lock held by "
                              f"{_owner_label(current)}", flush=True)
                        announced = True
                    time.sleep(POLL_SECONDS)
                else:
                    # A newly-created lock can briefly have no owner file
                    # while its owner publishes the record. Never remove it.
                    if ownerless_since is None:
                        ownerless_since = time.monotonic()
                    elif (not announced and time.monotonic()
                          - ownerless_since >= POLL_SECONDS):
                        print("heavy-work: waiting for lock held by "
                              "unknown owner (owner file missing)", flush=True)
                        announced = True
                    time.sleep(0.05)

        token = uuid.uuid4().hex
        previous_env = os.environ.get(OWNER_ENV)
        owner_record = {
            "owner": owner,
            "token": token,
            "pid": os.getpid(),
            "host": socket.gethostname(),
        }
        owner_path = HEAVY_LOCK_DIR / OWNER_FILE
        temp_path = HEAVY_LOCK_DIR / f".{OWNER_FILE}.{token}.tmp"
        _held_state = {
            "depth": 1,
            "token": token,
            "owned": True,
            "previous_env": previous_env,
        }
        try:
            temp_path.write_text(json.dumps(owner_record) + "\n",
                                 encoding="utf-8")
            os.replace(temp_path, owner_path)
            os.environ[OWNER_ENV] = token
        except BaseException:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            try:
                if _read_owner().get("token") == token:
                    owner_path.unlink()
                HEAVY_LOCK_DIR.rmdir()
            except OSError:
                pass
            if previous_env is None:
                os.environ.pop(OWNER_ENV, None)
            else:
                os.environ[OWNER_ENV] = previous_env
            _held_state = None
            raise


def release_heavy_lock() -> None:
    """Release one acquisition made by this process or reentrant context."""
    global _held_state
    with _state_guard:
        held = _held_state
        if held is None:
            raise RuntimeError(
                "heavy-work: release requested without an acquisition")
        held["depth"] -= 1
        if held["depth"]:
            return

        try:
            if held["owned"]:
                current = _read_owner()
                if current.get("token") != held["token"]:
                    raise RuntimeError(
                        "heavy-work: lock owner changed before release; "
                        "refusing to remove another owner's lock")
                (HEAVY_LOCK_DIR / OWNER_FILE).unlink()
                HEAVY_LOCK_DIR.rmdir()
        except OSError as exc:
            raise RuntimeError(
                f"heavy-work: could not release lock: {exc}") from exc
        finally:
            previous_env = held["previous_env"]
            if previous_env is None:
                os.environ.pop(OWNER_ENV, None)
            else:
                os.environ[OWNER_ENV] = previous_env
            _held_state = None


@contextmanager
def heavy_work_lock(owner: str) -> Iterator[None]:
    """Context manager for one heavy-work section."""
    take_heavy_lock(owner)
    try:
        yield
    finally:
        release_heavy_lock()


def heavy_work_locked(owner: str) -> Callable[[_F], _F]:
    """Decorate an entry point whose body is one locally heavy section."""
    def decorate(function: _F) -> _F:
        @functools.wraps(function)
        def wrapped(*args, **kwargs):
            with heavy_work_lock(owner):
                return function(*args, **kwargs)
        return wrapped  # type: ignore[return-value]
    return decorate


def _run_locked(owner: str, command: list[str]) -> int:
    """Run a child under the lock and forward termination signals to it."""
    if not command:
        raise ValueError("heavy-work: run requires a command after --")

    watched = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    previous_handlers = {sig: signal.getsignal(sig) for sig in watched}
    child: subprocess.Popen | None = None
    starting_child = False
    received: list[int] = []

    def forward(signum, _frame):
        received.append(signum)
        if child is None:
            if not starting_child:
                raise SystemExit(128 + signum)
            return
        try:
            os.killpg(child.pid, signum)
        except ProcessLookupError:
            pass

    for sig in watched:
        signal.signal(sig, forward)
    try:
        with heavy_work_lock(owner):
            starting_child = True
            child = subprocess.Popen(command, start_new_session=True)
            starting_child = False
            for signum in received:
                try:
                    os.killpg(child.pid, signum)
                except ProcessLookupError:
                    pass
            return_code = child.wait()
    finally:
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)

    if received:
        return 128 + received[-1]
    return return_code


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["owns"]:
        return 0 if _current_process_owns_lock() else 1
    if args and args[0] == "run":
        try:
            owner_index = args.index("--owner")
            owner = args[owner_index + 1]
            separator = args.index("--", owner_index + 2)
        except (ValueError, IndexError):
            raise SystemExit(
                "usage: python -m library.tools.heavy_work_lock run "
                "--owner NAME -- COMMAND [ARG ...]")
        return _run_locked(owner, args[separator + 1:])
    raise SystemExit(
        "usage: python -m library.tools.heavy_work_lock owns | "
        "run --owner NAME -- COMMAND [ARG ...]")


if __name__ == "__main__":
    raise SystemExit(main())
