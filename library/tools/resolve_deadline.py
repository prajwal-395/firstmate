"""A deadline around a Resolve call that may never return.

2026-10-02: sizing a fresh reel timeline - `SetSetting` of
`useCustomSettings` / `timelineResolutionWidth` /
`timelineResolutionHeight` on a timeline that had JUST been made
current - deadlocked Resolve. The main thread sat in
`Fusion::FusionApp::SyncProjectSettings ->
Fusion::RenderTask::ObtainRenderLock`, sleeping in a loop, until
SIGTERM seventeen minutes later (https://github.com/prajwal-395/video_editing_pilot/pull/1594
body, "the timeline-resolution race"). The same sequence completed
three times, so it is a race, not a certainty.

Two halves, and this module is only the second:

1. AVOID the race: size the timeline BEFORE it becomes current (the
   callers do that - a fresh timeline nothing is rendering yet holds
   no render lock for the settings sync to contend with).
2. BOUND it: every setting write and read-back in
   `apply_timeline_resolution` runs under `call_with_deadline`, so a
   hang that still happens raises `ResolveCallTimeout` naming the key
   instead of waiting indefinitely for the Python call to return, and
   the caller refuses the build by name.

The bound is best-effort by construction, and that is written down
rather than implied: the worker can only report what the binding lets
it observe. A scripting call that blocks inside the binding WITHOUT
releasing the GIL holds the interpreter with it, and then the
deadline cannot fire until the call returns - no Python-level timeout
can. What the deadline buys is every other shape of hang: a call
that releases the GIL while it waits (the usual scripting-bridge
shape) becomes a named failure after `TIMELINE_SETTING_TIMEOUT_S`.
The wedged worker is a daemon thread, so it never blocks interpreter
exit either way.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from typing import Any

from library.tools.ren_refusal import RenRefusal

#: How long one timeline-setting write may take before it is a hang.
#: A live return is sub-second; the 2026-10-02 hang lasted until
#: SIGTERM seventeen minutes later. Generous on purpose: firing here
#: refuses a build, so the cost of too short is a refused reel, while
#: the cost of too long is an unnecessarily long build wait.
TIMELINE_SETTING_TIMEOUT_S = 30.0

#: The three writes that size a reel timeline, in the order they must
#: run: `useCustomSettings` snaps a timeline to 1920x1080 first, so the
#: width and height go on AFTER it or the reel lands transposed
#: (`overlay_placement`'s 2026-09-11 measurement).
_RESOLUTION_KEYS = ("useCustomSettings", "timelineResolutionWidth",
                    "timelineResolutionHeight")


class ResolveCallTimeout(RenRefusal):
    """A Resolve call did not return within its deadline.

    A timeout is a REFUSAL, not a crash: nothing was written, the
    timeline is as it was, and the next step is known - restart Resolve
    and run the command again. Before this it was a bare `RuntimeError`
    with a sentence, so a host that met one had to work out for itself
    whether a retry was safe. The message still reads as it did (the
    call sites embed it in their own prose); the typed refusal fields
    are what a host keys off.
    """

    CODE = "REN_RESOLVE_CALL_TIMEOUT"
    RECOVERY_CLASS = "needs_question"
    ASK_REASON = "insufficient_evidence"
    """Resolve stopped answering, so what is on the record does not settle
    whether the instance is healthy - a person has to look before the
    step is run again."""
    USER_MESSAGE = ("Resolve stopped answering, so I changed nothing. "
                    "Restart Resolve and run the step again.")

    def __init__(self, message: str, *, what: str = "", why: str = "",
                 fix: str = ""):
        self.message = message
        super().__init__(
            what or message,
            why or "Resolve did not answer a scripting call in time",
            fix or ("restart Resolve, then run the command again - the "
                    "call was refused before it wrote anything"),
        )

    def __str__(self) -> str:
        return self.message


class ResolutionNotApplied(RenRefusal):
    """The resolution writes returned but the read-back disagrees."""

    CODE = "REN_RESOLUTION_NOT_APPLIED"
    RECOVERY_CLASS = "needs_question"
    ASK_REASON = "missing_authorization"
    """The write was refused and the size is the person's to set on the
    timeline: nobody has said yes to a differently sized reel."""
    USER_MESSAGE = ("Resolve did not take the size this reel needs, so I "
                    "stopped rather than build on a wrongly sized "
                    "timeline. Read the values below and set them by hand.")

    def __init__(self, message: str, *, what: str = "", why: str = "",
                 fix: str = ""):
        self.message = message
        super().__init__(
            what or message,
            why or "the read-back disagreed with the write",
            fix or ("read the timeline's delivery size and set it by hand - "
                    "the write was refused rather than trusted"),
        )

    def __str__(self) -> str:
        return self.message


class DeadlineProxy:
    """A Resolve object whose API methods all run behind a deadline.

    Resolve returns handles at each level (project, timeline, clip,
    media-pool item). This proxy follows those handles and wraps method
    results nested in lists, tuples and mappings, so a caller cannot
    accidentally leave one API call unbounded while walking a graph.
    The proxy itself stays on the caller's thread; only each native API
    method call runs in the deadline worker, matching
    :func:`call_with_deadline`'s existing contract.
    """

    __slots__ = ("_cache", "_label", "_target", "_timeout_s")

    def __init__(self, target: Any, label: str = "Resolve",
                 cache: dict[int, DeadlineProxy] | None = None,
                 timeout_s: float | None = None) -> None:
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "_label", label)
        object.__setattr__(self, "_cache", cache if cache is not None else {})
        object.__setattr__(self, "_timeout_s", timeout_s)
        self._cache.setdefault(id(target), self)

    @classmethod
    def wrap(cls, value: Any, label: str, cache: dict[int, DeadlineProxy],
             timeout_s: float | None) -> Any:
        value_type = type(value)
        if value is None or issubclass(
                value_type, (str, bytes, int, float, bool)):
            return value
        if issubclass(value_type, list):
            return [cls.wrap(item, f"{label}[{index}]", cache, timeout_s)
                    for index, item in enumerate(value)]
        if issubclass(value_type, tuple):
            return tuple(cls.wrap(item, f"{label}[{index}]", cache, timeout_s)
                         for index, item in enumerate(value))
        if issubclass(value_type, Mapping):
            return {key: cls.wrap(item, f"{label}[{key!r}]", cache, timeout_s)
                    for key, item in value.items()}
        found = cache.get(id(value))
        if found is not None:
            return found
        return cls(value, label=label, cache=cache, timeout_s=timeout_s)

    @classmethod
    def unwrap(cls, value: Any) -> Any:
        """Pass native handles, never proxy facades, back into Resolve."""
        value_type = type(value)
        if issubclass(value_type, cls):
            return object.__getattribute__(value, "_target")
        if issubclass(value_type, list):
            return [cls.unwrap(item) for item in value]
        if issubclass(value_type, tuple):
            return tuple(cls.unwrap(item) for item in value)
        if issubclass(value_type, Mapping):
            return {key: cls.unwrap(item) for key, item in value.items()}
        return value

    def __getattr__(self, name: str) -> Any:
        target = object.__getattribute__(self, "_target")
        label = f"{object.__getattribute__(self, '_label')}.{name}"
        value = getattr(target, name)
        if not callable(value):
            return self.wrap(
                value, label, object.__getattribute__(self, "_cache"),
                object.__getattribute__(self, "_timeout_s"))

        def invoke(*args, **kwargs):
            result = call_with_deadline(
                label, value,
                *(self.unwrap(arg) for arg in args),
                timeout_s=object.__getattribute__(self, "_timeout_s"),
                **{key: self.unwrap(item)
                   for key, item in kwargs.items()})
            return self.wrap(
                result, label, object.__getattribute__(self, "_cache"),
                object.__getattribute__(self, "_timeout_s"))

        return invoke

    def __bool__(self) -> bool:
        return bool(object.__getattribute__(self, "_target"))

    def __repr__(self) -> str:
        target = object.__getattribute__(self, "_target")
        return f"DeadlineProxy({target!r})"


def deadline_proxy(resolve: Any, label: str = "Resolve",
                    timeout_s: float | None = None) -> DeadlineProxy:
    """Wrap a Resolve handle and every object it returns."""
    return DeadlineProxy(resolve, label=label, timeout_s=timeout_s)


def call_with_deadline(call_label: str, func: Callable[..., Any], *args,
                       timeout_s: float | None = None, **kwargs) -> Any:
    """Run `func(*args, **kwargs)` and return its value, with a deadline.

    `label` names the call in the failure - the timeline setting key,
    never a bare "Resolve call". An exception the call raises
    propagates unchanged; a call still running after `timeout_s`
    raises `ResolveCallTimeout`. `None` reads the module default at
    call time, so a test may shorten it without touching production
    call sites.
    """
    timeout = (TIMELINE_SETTING_TIMEOUT_S if timeout_s is None
               else timeout_s)
    box: dict[str, Any] = {}
    try:
        from library.tools import perf_ledger
        perf_ledger.count_call("resolve_api_calls", 1, api_call=call_label)
        measurement = perf_ledger.span(
            "resolve_api_call", calls=1, api_call=call_label)
    except Exception:  # noqa: BLE001 - instrumentation must not affect Resolve
        perf_ledger = None
        measurement = None

    def _run() -> None:
        try:
            box["value"] = func(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - must cross the thread boundary
            box["error"] = exc

    worker = threading.Thread(target=_run,
                              name=f"resolve-deadline:{call_label}",
                              daemon=True)
    if measurement is not None:
        with measurement:
            worker.start()
            worker.join(timeout)
    else:
        worker.start()
        worker.join(timeout)
    if worker.is_alive():
        raise ResolveCallTimeout(
            f"{call_label} did not return within {timeout:g}s - Resolve is "
            f"not answering scripting calls, so this refuses rather "
            f"than waiting indefinitely for the call (the 2026-10-02 Fusion "
            f"render-lock race hung this same write until SIGTERM).")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def apply_timeline_resolution(timeline, width: int, height: int,
                              timeout_s: float | None = None) -> None:
    """Size a reel timeline at the delivery frame, under a deadline.

    Writes `useCustomSettings` then width then height (the order the
    snap requires), EACH under `call_with_deadline`, then reads all
    three back under the same deadline: the verdict is the read-back,
    never the return, so a write Resolve silently drops refuses as
    `ResolutionNotApplied` naming the key rather than building onto a
    wrongly sized timeline. Call BEFORE the timeline becomes current.
    """
    values = {"useCustomSettings": "1",
              "timelineResolutionWidth": str(int(width)),
              "timelineResolutionHeight": str(int(height))}
    for key in _RESOLUTION_KEYS:
        call_with_deadline(f"timeline SetSetting {key}={values[key]}",
                           timeline.SetSetting, key, values[key],
                           timeout_s=timeout_s)
    mismatched = []
    for key in _RESOLUTION_KEYS:
        try:
            read = call_with_deadline(
                f"timeline GetSetting {key}", timeline.GetSetting, key,
                timeout_s=timeout_s)
        except ResolveCallTimeout:
            raise
        except Exception:  # noqa: BLE001 - an unreadable setting is a mismatch
            read = None
        if read is None or str(read) != values[key]:
            mismatched.append(f"{key} (wrote {values[key]!r}, "
                              f"reads back {read!r})")
    if mismatched:
        raise ResolutionNotApplied(
            "the timeline did not take its delivery size: "
            + "; ".join(mismatched))
