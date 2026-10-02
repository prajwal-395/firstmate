"""Admission to the machine's heavy work, through the resource scheduler.

A heavy section names the PROFILE of work it is (`resource_scheduler.
profiles()`), and `library/tools/resource_scheduler.py` admits it while
every resource stays within capacity: sections that can coexist do, and
the rest queue. A section that names no profile demands the whole
machine (`machine`), which is the old global mutex exactly.

The job's token is carried in ``VEP_HEAVY_WORK_OWNER``. Child processes
inherit it and re-enter without waiting on their own parent, as does a
nested section in the same process - but only for a demand the held
grant already covers. A nested demand it does not cover RAISES: growing
a grant while holding one is how two jobs deadlock.

When an operation needs both this and Resolve's instance lease, it
acquires the Resolve lease first and this second. Waiting on Resolve
while holding machine resources would block unrelated heavy work and
could deadlock a caller that takes them in the opposite order.
"""

from __future__ import annotations

import functools
import os
import signal
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, TypeVar

from library.tools import resource_scheduler


LOCK_DIR_ENV = "VEP_HEAVY_WORK_LOCK_DIR"
"""Overrides where the scheduler lives: its store sits beside this old
lock directory, which it holds while any job runs (the bridge in
`resource_scheduler`). The test suite points it at a private directory
(tests/conftest.py) so a mocked build never queues behind the machine's
real heavy work; nothing else sets it."""
HEAVY_LOCK_DIR = Path(
    os.environ.get(LOCK_DIR_ENV)
    or Path.home() / ".local" / "share" / "vep" / "heavy-work.lock")
OWNER_ENV = "VEP_HEAVY_WORK_OWNER"
MACHINE = "machine"

_state_guard = threading.Lock()
_held_state: dict | None = None
_F = TypeVar("_F", bound=Callable)


def _scheduler() -> resource_scheduler.Scheduler:
    return resource_scheduler.Scheduler(HEAVY_LOCK_DIR)


class GrantTooSmall(RuntimeError):
    """A nested section asked for more than the grant it runs under."""


def _inherited_grant() -> dict | None:
    token = os.environ.get(OWNER_ENV)
    return _scheduler().running_demand(token) if token else None


def owns(profile: str = MACHINE) -> bool:
    """Whether this process already runs under a grant covering `profile`."""
    held = _inherited_grant()
    return held is not None and resource_scheduler.covers(
        held, resource_scheduler.demand_for(profile))


def take_heavy_lock(owner: str, profile: str = MACHINE,
                    cancelled: Callable[[], bool] | None = None) -> None:
    """Acquire `profile`'s resources, re-entering a grant that covers them.

    `cancelled` ends a wait early (`resource_scheduler.Cancelled`)."""
    global _held_state
    demand = resource_scheduler.demand_for(profile)
    with _state_guard:
        if _held_state is not None:
            if not resource_scheduler.covers(_held_state["demand"], demand):
                raise GrantTooSmall(
                    f"heavy-work: {owner!r} needs {demand} inside a grant "
                    f"of {_held_state['demand']}; take the larger profile "
                    "at the outer section")
            _held_state["depth"] += 1
            return

        inherited = os.environ.get(OWNER_ENV)
        held = _inherited_grant()
        if held is not None:
            if not resource_scheduler.covers(held, demand):
                raise GrantTooSmall(
                    f"heavy-work: {owner!r} needs {demand} inside an "
                    f"inherited grant of {held}; take the larger profile "
                    "in the parent")
            _held_state = {"depth": 1, "token": inherited, "owned": False,
                           "demand": held, "previous_env": inherited}
            return

        # Built BEFORE the grant: nothing may run between a successful
        # acquire and `_held_state`, or a signal there (SIGTERM to a
        # gate) leaves the grant held with nobody to release it.
        sampler = resource_scheduler.TreeSampler(
            watch_resolve=bool(demand.get("resolve_cursor")))
        try:
            token = _scheduler().acquire(
                owner, demand, announce=lambda line: print(line, flush=True),
                cancelled=cancelled)
        except BaseException:
            sampler.stop()
            raise
        _held_state = {"depth": 1, "token": token, "owned": True,
                       "demand": demand, "previous_env": inherited,
                       "profile": profile, "sampler": sampler}
        os.environ[OWNER_ENV] = token


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
                _scheduler().release(held["token"], held["profile"],
                                     held["sampler"].stop())
        finally:
            previous_env = held["previous_env"]
            if previous_env is None:
                os.environ.pop(OWNER_ENV, None)
            else:
                os.environ[OWNER_ENV] = previous_env
            _held_state = None


@contextmanager
def heavy_work_lock(owner: str, profile: str = MACHINE,
                    cancelled: Callable[[], bool] | None = None
                    ) -> Iterator[None]:
    """Context manager for one heavy-work section."""
    take_heavy_lock(owner, profile, cancelled)
    try:
        yield
    finally:
        release_heavy_lock()


def heavy_work_locked(owner: str, profile: str = MACHINE
                      ) -> Callable[[_F], _F]:
    """Decorate an entry point whose body is one locally heavy section."""
    def decorate(function: _F) -> _F:
        @functools.wraps(function)
        def wrapped(*args, **kwargs):
            with heavy_work_lock(owner, profile):
                return function(*args, **kwargs)
        return wrapped  # type: ignore[return-value]
    return decorate


def _run_locked(owner: str, profile: str, command: list[str]) -> int:
    """Run a child under the grant and forward termination signals to it.

    The handler only RECORDS a signal (and forwards it to a running
    child); it never raises. A handler that raised could land inside the
    admitting transaction - grant taken, token not yet held - and leave
    the machine-wide lock held by nobody. A signal before admission ends
    the wait through `cancelled`; one during admission is seen as soon as
    the grant is held, and leaving the `with` releases it.
    """
    if not command:
        raise ValueError("heavy-work: run requires a command after --")

    watched = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    previous_handlers = {sig: signal.getsignal(sig) for sig in watched}
    child: subprocess.Popen | None = None
    received: list[int] = []
    return_code = 0

    def forward(signum, _frame):
        received.append(signum)
        if child is not None:
            try:
                os.killpg(child.pid, signum)
            except ProcessLookupError:
                pass

    for sig in watched:
        signal.signal(sig, forward)
    try:
        with heavy_work_lock(owner, profile,
                             cancelled=lambda: bool(received)):
            if not received:
                child = subprocess.Popen(command, start_new_session=True)
                # A signal between the check and `child` being set was
                # recorded but not forwarded: forward it now.
                for signum in received:
                    try:
                        os.killpg(child.pid, signum)
                    except ProcessLookupError:
                        pass
                return_code = child.wait()
    except resource_scheduler.Cancelled:
        pass
    finally:
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)

    if received:
        return 128 + received[-1]
    return return_code


def _option(args: list[str], name: str, default: str | None) -> str | None:
    if name not in args:
        return default
    return args[args.index(name) + 1]


def _history(days: float) -> str:
    """Each profile's declared demand beside what its grants measured."""
    import time
    grants = _scheduler().history(time.time() - days * 86400)
    declared = resource_scheduler.profiles()
    by_profile: dict = {}
    for grant in grants:
        by_profile.setdefault(grant["profile"] or "?", []).append(grant)
    lines = [f"{len(grants)} grant(s) in {days:g} day(s); capacity "
             f"{resource_scheduler.capacity()}"]
    for name, rows in sorted(by_profile.items()):
        sampled = [r for r in rows if r["samples"]]
        held = [r["finished_at"] - r["started_at"] for r in rows]
        waited = [r["started_at"] - r["enqueued_at"] for r in rows]
        lines.append(
            f"{name}: {len(rows)} grant(s), held max {max(held):.0f}s, "
            f"waited max {max(waited):.0f}s")
        lines.append(f"  declared  {declared.get(name, '(not a profile)')}")
        if sampled:
            mean_cores = max(r["cpu_s"] / max(1.0, r["finished_at"]
                                              - r["started_at"])
                             for r in sampled)
            lines.append(
                f"  measured  peak cores max "
                f"{max(r['peak_cores'] for r in sampled):.2f}, mean cores "
                f"max {mean_cores:.2f}, peak rss max "
                f"{max(r['peak_rss_gb'] for r in sampled):.2f} GB")
            watched = [r for r in sampled
                       if r["resolve_peak_cores"] is not None]
            if watched:
                lines.append(
                    f"  Resolve   peak cores max "
                    f"{max(r['resolve_peak_cores'] for r in watched):.2f}, "
                    f"peak rss max "
                    f"{max(r['resolve_peak_rss_gb'] for r in watched):.2f}"
                    f" GB (includes the captain's own use)")
    return "\n".join(lines)


USAGE = ("usage: python -m library.tools.heavy_work_lock "
         "owns [--profile NAME] | jobs | history [--days N] | "
         "run --owner NAME [--profile NAME] -- COMMAND [ARG ...]")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["owns"]:
        return 0 if owns(_option(args, "--profile", MACHINE)) else 1
    if args == ["jobs"]:
        for job in _scheduler().jobs():
            print(f"{job['state']:8} pid {job['pid']:>6}  "
                  f"{job['owner']}  {job['demand']}")
        return 0
    if args[:1] == ["history"]:
        print(_history(float(_option(args, "--days", "30"))))
        return 0
    if args[:1] == ["run"] and "--" in args:
        separator = args.index("--")
        head = args[:separator]
        try:
            owner = _option(head, "--owner", None)
            profile = _option(head, "--profile", MACHINE)
        except IndexError:
            raise SystemExit(USAGE)
        if owner:
            return _run_locked(owner, profile, args[separator + 1:])
    raise SystemExit(USAGE)

if __name__ == "__main__":
    raise SystemExit(main())
