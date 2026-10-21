"""Admission to the machine's heavy work, through the resource scheduler.

A heavy section names the PROFILE of work it is (`resource_scheduler.
profiles()`). The scheduler permits concurrent work, protects one writer
per native build directory, reserves declared `ram_gb`, and checks live OS
memory headroom. CPU, GPU, and disk declarations do not serialize jobs;
Resolve's own lease and locks arbitrate Resolve work. `machine` is an empty
profile.

The job's token is carried in ``VEP_HEAVY_WORK_OWNER``. Child processes
inherit it and re-enter without waiting on their own parent, as does a
nested section in the same process - but only for a demand the held
grant already covers. A nested demand it does not cover RAISES: growing
a grant while holding one is how two jobs deadlock.

Resolve's instance lease remains the authority over its cursor.
"""

from __future__ import annotations

import functools
import math
import os
import signal
import subprocess
import sys
import threading
import time
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
_memory_reservation: dict | None = None
_exit_hook_installed = False
_F = TypeVar("_F", bound=Callable)


def _ensure_exit_hook() -> None:
    """Release owned grants on a clean interpreter exit.

    The `with` blocks already release on every exception unwind; this
    covers the abnormal path that never unwinds them (a `take_` without
    its `release_`, `os._exit` aside): the scheduler's heartbeat lease
    is the `kill -9` fallback past this hook.
    """
    global _exit_hook_installed
    if _exit_hook_installed:
        return
    import atexit

    def _release_at_exit() -> None:
        global _held_state, _memory_reservation
        with _state_guard:
            held, _held_state = _held_state, None
            reserved, _memory_reservation = _memory_reservation, None
        for state, is_memory in ((held, False), (reserved, True)):
            if state is None or not state.get("owned", True):
                continue
            try:
                sampler = state.get("sampler")
                measured = sampler.stop() if sampler is not None else None
                _scheduler().release(state["token"],
                                     state.get("profile", ""), measured)
            except BaseException:
                pass

    atexit.register(_release_at_exit)
    _exit_hook_installed = True


def _announce(line: str) -> None:
    """Scheduler wait notices go to stderr, never stdout: stdout is the
    machine-readable channel (`ren analyze --json`), and a wait notice
    there breaks every consumer that parses it."""
    print(line, file=sys.stderr, flush=True)


def _stop_over_reservation(owner: str, profile: str,
                          footprint_gb: float,
                          reservation_gb: float) -> None:
    """Stop this exact scheduler owner when its process tree exceeds RAM."""
    pid = os.getpid()
    _announce(
        f"heavy-work: stopping {owner!r} (pid {pid}): process-tree "
        f"physical footprint {footprint_gb:.2f} GB exceeds declared "
        f"reservation {reservation_gb:.2f} GB for {profile}")
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


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
                    cancelled: Callable[[], bool] | None = None,
                    timeout: float | None = None,
                    ram_gb: float | None = None) -> None:
    """Acquire `profile`'s resources, re-entering a grant that covers them.

    `cancelled` ends a wait early (`resource_scheduler.Cancelled`)."""
    global _held_state
    demand = resource_scheduler.demand_for(profile)
    if ram_gb is not None:
        if not math.isfinite(ram_gb) or ram_gb <= 0:
            raise ValueError("heavy-work: --ram-gb must be a finite number > 0")
        demand["ram_gb"] = ram_gb
    with _state_guard:
        if _held_state is not None:
            if not resource_scheduler.covers(_held_state["demand"], demand):
                raise GrantTooSmall(
                    f"heavy-work: {owner!r} needs {demand} inside a grant "
                    f"of {_held_state['demand']}; take the larger profile "
                    "at the outer section")
            _held_state["depth"] += 1
            return

        if (_memory_reservation is not None
                and profile not in _memory_reservation["nested_profiles"]):
            raise GrantTooSmall(
                f"heavy-work: {owner!r} requested {profile!r} while holding "
                f"a persistent memory reservation; only its declared "
                f"admissions {_memory_reservation['nested_profiles']} may "
                "be nested")

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
            watch_resolve=(bool(demand.get("resolve_cursor"))
                           or resource_scheduler.watches_resolve(profile)),
            reservation_gb=demand.get("ram_gb"),
            over_reservation=lambda footprint, reservation:
            _stop_over_reservation(owner, profile, footprint, reservation))
        try:
            token = _scheduler().acquire(
                owner, demand, announce=_announce,
                cancelled=cancelled, timeout=timeout)
        except BaseException:
            sampler.stop()
            raise
        timing = _scheduler().timing(token)
        _held_state = {"depth": 1, "token": token, "owned": True,
                       "demand": demand, "previous_env": inherited,
                       "profile": profile, "sampler": sampler,
                       "schedule_owner": owner,
                       "schedule_started_at": timing[1] if timing else time.time(),
                       "schedule_queue_wait_s": (
                           max(0.0, timing[1] - timing[0]) if timing else None),
                       "schedule_execution_started": time.perf_counter()}
        os.environ[OWNER_ENV] = token
        sampler.enforce_reservation()
        _ensure_exit_hook()


def take_heavy_memory_reservation(
        owner: str, profile: str, nested_profiles: tuple[str, ...],
        cancelled: Callable[[], bool] | None = None,
        timeout: float | None = None) -> None:
    """Track a resident model session and permit named nested profiles.

    Model memory is reserved by the profile's `ram_gb` demand and live OS
    headroom. The scheduler grant records the session lifetime; nested
    profile limits remain process-local.

    `timeout` bounds the WAIT for that reservation. It is None by
    default, which waits as long as the machine needs - and a machine
    that cannot free the memory never says so. A caller that cannot
    measure without the model passes a bound and gets a refusal instead
    of a silent wait.
    """
    global _memory_reservation
    demand = resource_scheduler.demand_for(profile)
    if demand and set(demand) != {"ram_gb"}:
        raise ValueError(
            f"heavy-work: persistent memory session {profile!r} must be "
            "empty or RAM-only")
    admissions = {}
    for nested_profile in nested_profiles:
        nested_demand = resource_scheduler.demand_for(nested_profile)
        if "ram_gb" in nested_demand:
            raise ValueError(
                f"heavy-work: nested admission {nested_profile!r} may not "
                "demand RAM under a persistent memory reservation")
        admissions[nested_profile] = nested_demand

    # The state guard makes nested-profile limits a process-local contract.
    with _state_guard:
        if _memory_reservation is not None:
            raise RuntimeError(
                "heavy-work: only one persistent memory reservation may be "
                "active in a process")
        if _held_state is not None:
            if resource_scheduler.covers(_held_state["demand"], demand):
                _memory_reservation = {
                    "token": _held_state["token"], "owned": False,
                    "profile": profile, "demand": demand,
                    "sampler": None,
                    "nested_profiles": frozenset(admissions),
                    # The enclosing grant the release must still see:
                    # anything else surrounding it is an unreleased
                    # nested admission.
                    "folded_into": (_held_state["token"],
                                    _held_state["depth"]),
                }
                _ensure_exit_hook()
                return
            raise GrantTooSmall(
                f"heavy-work: {owner!r} must reserve model memory before "
                "taking any ordinary grant")
        inherited = _inherited_grant()
        if inherited is not None:
            if resource_scheduler.covers(inherited, demand):
                _memory_reservation = {
                    "token": os.environ.get(OWNER_ENV), "owned": False,
                    "profile": profile, "demand": demand,
                    "sampler": None,
                    "nested_profiles": frozenset(admissions),
                    # Folded onto an inherited grant: no local grant
                    # existed at take time, so any local grant at
                    # release time is an unreleased nested admission.
                    "folded_into": (os.environ.get(OWNER_ENV), None),
                }
                _ensure_exit_hook()
                return
            raise GrantTooSmall(
                f"heavy-work: {owner!r} must reserve model memory outside "
                f"an inherited grant of {inherited}; take a profile "
                "covering the model memory in the parent instead")

        sampler = resource_scheduler.TreeSampler(
            reservation_gb=demand.get("ram_gb"),
            over_reservation=lambda footprint, reservation:
            _stop_over_reservation(owner, profile, footprint, reservation))
        try:
            token = _scheduler().acquire(
                owner, demand, announce=_announce,
                cancelled=cancelled, timeout=timeout)
        except BaseException:
            sampler.stop()
            raise
        _memory_reservation = {
            "token": token, "profile": profile, "demand": demand,
            "sampler": sampler, "nested_profiles": frozenset(admissions),
            "folded_into": None,
        }
        sampler.enforce_reservation()
        _ensure_exit_hook()


def release_heavy_memory_reservation() -> None:
    """Release the RAM reservation after its model has been unloaded.

    A reservation folded into an enclosing grant (`owned` False) clears
    locally only: the enclosing grant's own release accounts the RAM.
    """
    global _memory_reservation
    with _state_guard:
        held = _memory_reservation
        if held is None:
            raise RuntimeError(
                "heavy-work: memory reservation release without an "
                "acquisition")
        folded_into = held.get("folded_into")
        if folded_into is None:
            if _held_state is not None:
                raise RuntimeError(
                    "heavy-work: cannot release model memory during an "
                    "active admission")
        elif folded_into[1] is None:
            # Folded onto an inherited grant: any local grant now is an
            # admission taken after the reservation and never released.
            if _held_state is not None:
                raise RuntimeError(
                    "heavy-work: cannot release model memory during an "
                    "active admission")
        elif (_held_state is None
                or (_held_state.get("token"), _held_state.get("depth"))
                != folded_into):
            raise RuntimeError(
                "heavy-work: cannot release model memory during an "
                "active admission")
        if held.get("owned", True):
            _scheduler().release(held["token"], held["profile"],
                                 held["sampler"].stop())
        _memory_reservation = None


def memory_reservation_contended() -> bool:
    """Whether current OS memory headroom is too low for new admissions."""
    with _state_guard:
        held = _memory_reservation
        if held is None or not held.get("owned", True):
            return False
    return not resource_scheduler._memory_allows_admission()


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
                if held.get("schedule_queue_wait_s") is not None:
                    from library.tools import perf_ledger
                    perf_ledger.record_scheduled_work(
                        held["schedule_owner"], held["profile"],
                        held["schedule_queue_wait_s"],
                        time.perf_counter()
                        - held["schedule_execution_started"],
                        held["schedule_started_at"])
        finally:
            previous_env = held["previous_env"]
            if previous_env is None:
                os.environ.pop(OWNER_ENV, None)
            else:
                os.environ[OWNER_ENV] = previous_env
            _held_state = None


@contextmanager
def heavy_work_lock(owner: str, profile: str = MACHINE,
                    cancelled: Callable[[], bool] | None = None,
                    timeout: float | None = None,
                    ram_gb: float | None = None
                    ) -> Iterator[None]:
    """Context manager for one heavy-work section."""
    take_heavy_lock(owner, profile, cancelled, timeout, ram_gb)
    try:
        yield
    finally:
        release_heavy_lock()


@contextmanager
def heavy_work_reservation(owner: str, profile: str, *,
                           nested_profiles: tuple[str, ...],
                           cancelled: Callable[[], bool] | None = None,
                           timeout: float | None = None
                           ) -> Iterator[None]:
    """Track a resident model and permit only its named nested profiles.

    `timeout` bounds the wait for the reservation; None waits as long as
    the machine needs.
    """
    take_heavy_memory_reservation(owner, profile, nested_profiles,
                                  cancelled, timeout)
    try:
        yield
    finally:
        release_heavy_memory_reservation()


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


def _run_locked(owner: str, profile: str, command: list[str],
                ram_gb: float | None = None) -> int:
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
    queue_wait_s = 0.0
    execution_started = None
    execution_s = 0.0

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
                             cancelled=lambda: bool(received),
                             ram_gb=ram_gb):
            if not received:
                token = os.environ.get(OWNER_ENV)
                timing = _scheduler().timing(token) if token else None
                if timing is not None:
                    queue_wait_s = max(0.0, timing[1] - timing[0])
                execution_started = time.monotonic()
                child = subprocess.Popen(command, start_new_session=True)
                # A signal between the check and `child` being set was
                # recorded but not forwarded: forward it now.
                for signum in received:
                    try:
                        os.killpg(child.pid, signum)
                    except ProcessLookupError:
                        pass
                return_code = child.wait()
                execution_s = max(0.0, time.monotonic() - execution_started)
    except resource_scheduler.Cancelled:
        pass
    finally:
        for sig, handler in previous_handlers.items():
            signal.signal(sig, handler)

    if received:
        return 128 + received[-1]
    if execution_started is not None:
        print(f"heavy-work: queue wait {queue_wait_s:.3f}s; "
              f"execution {execution_s:.3f}s", file=sys.stderr, flush=True)
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
            f"{name}: {len(rows)} grant(s), execution max {max(held):.0f}s, "
            f"queue wait max {max(waited):.0f}s")
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
            footprints = [r["peak_footprint_gb"] for r in sampled
                          if r["peak_footprint_gb"] is not None]
            if footprints:
                lines.append(
                    f"            peak phys_footprint max "
                    f"{max(footprints):.2f} GB")
            swaps = [r["peak_swap_gb"] for r in sampled
                     if r["peak_swap_gb"] is not None]
            if swaps:
                lines.append(
                    f"            machine peak swap max {max(swaps):.2f} GB")
            pressures = [r["peak_memory_pressure_pct"] for r in sampled
                         if r["peak_memory_pressure_pct"] is not None]
            if pressures:
                lines.append(
                    f"            machine peak memory pressure max "
                    f"{max(pressures):.0f}%")
            watched = [r for r in sampled
                       if r["resolve_peak_cores"] is not None]
            if watched:
                lines.append(
                    f"  Resolve   peak cores max "
                    f"{max(r['resolve_peak_cores'] for r in watched):.2f}, "
                    f"peak rss max "
                    f"{max(r['resolve_peak_rss_gb'] for r in watched):.2f}"
                    f" GB (includes the captain's own use)")
                resolve_footprints = [
                    r["resolve_peak_footprint_gb"] for r in watched
                    if r["resolve_peak_footprint_gb"] is not None]
                if resolve_footprints:
                    lines.append(
                        f"            Resolve peak phys_footprint max "
                        f"{max(resolve_footprints):.2f} GB")
    return "\n".join(lines)


USAGE = ("usage: python -m library.tools.heavy_work_lock "
         "owns [--profile NAME] | jobs | history [--days N] | machine | "
         "run --owner NAME [--profile NAME] [--ram-gb N] -- COMMAND [ARG ...]")


def _machine_reading() -> str:
    """One reading of what this machine can host right now.

    The scheduler's own view (live available memory, and the swap that
    says the machine is already paging) beside the OS pressure band.
    A stage's caller reads this before it reserves anything: a machine
    that cannot host the weights should be told so, not discovered by
    a wait.
    """
    from library.tools import perf_resources

    reading = resource_scheduler._memory_available_gb()
    swap_used, swap_total = resource_scheduler._swap_state_gb()
    pressure = perf_resources.memory_pressure()
    lines = []
    if reading is None:
        lines.append("available memory: not measured")
    else:
        available, total = reading
        lines.append(f"available memory: {available:.1f} GB of {total:.1f} GB")
    if swap_used is None or not swap_total:
        lines.append("swap: not measured")
    else:
        lines.append(f"swap: {swap_used:.1f} GB of {swap_total:.1f} GB "
                     f"({swap_used / swap_total:.0%})")
    lines.append(f"memory pressure: {pressure.get('level')} "
                 f"({pressure.get('pct')}%)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["owns"]:
        return 0 if owns(_option(args, "--profile", MACHINE)) else 1
    if args == ["jobs"]:
        for job in _scheduler().jobs():
            print(f"{job['state']:8} pid {job['pid']:>6}  "
                  f"{job['owner']}  {job['demand']}"
                  f"{'  STALLED (no CPU progress)' if job['stalled'] else ''}")
        return 0
    if args[:1] == ["history"]:
        print(_history(float(_option(args, "--days", "30"))))
        return 0
    if args[:1] == ["machine"]:
        print(_machine_reading())
        return 0
    if args[:1] == ["run"] and "--" in args:
        separator = args.index("--")
        head = args[:separator]
        try:
            owner = _option(head, "--owner", None)
            profile = _option(head, "--profile", MACHINE)
            ram_raw = _option(head, "--ram-gb", None)
            ram_gb = float(ram_raw) if ram_raw is not None else None
        except (IndexError, ValueError):
            raise SystemExit(USAGE)
        if owner:
            if ram_gb is not None and (not math.isfinite(ram_gb)
                                       or ram_gb <= 0):
                raise SystemExit("heavy-work: --ram-gb must be a finite number > 0")
            return _run_locked(owner, profile, args[separator + 1:], ram_gb)
    raise SystemExit(USAGE)

if __name__ == "__main__":
    raise SystemExit(main())
