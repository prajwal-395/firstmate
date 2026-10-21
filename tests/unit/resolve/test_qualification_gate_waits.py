"""A gate that needs Resolve WAITS, and names who holds it.

THE DEFECT (measured 2026-10-21): the AXI qualification inspection
capped its own wait for the instance at two minutes, and the cap turned
a BUSY MACHINE into a REFUSAL. Every brief-level write attempt failed
this way, and the refusal the operator read was the wrong one:

```
no production-qualified executor is available for: clip.set_property
production routing fails closed (clip.set_property: runtime
qualification state was not inspected (ResolveBusy: ... 'axi
qualification inspection': timed out waiting for Resolve))
```

The engine refused a write because the machine was busy, which is not
the same as the write being wrong - and it refused it on a saturated
machine, where the whole point of the gate is to MEASURE.

Ren's lease owns contention, so a gate that needs Resolve rides out the
queue the way every other lease does: the lease's own budget, and the
holder named when the wait runs out.

These are the defects this could reintroduce:

1. the inspection takes the lease with the LEASE'S budget - it does not
   cap its own wait;
2. a refusal names who holds the instance;
3. a session that already holds the instance reuses the inspection it
   has, and takes no second lease.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


class _FakeResolve:
    """The handle the measurement reads the version from."""

    def GetVersionString(self) -> str:
        return "21.1.0.14"


def _runtime() -> dict:
    return {"resolve": _FakeResolve(), "rows": [], "tracks": [],
            "settings": {}}

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def test_the_inspection_rides_out_the_queue_on_the_lease_s_budget(monkeypatch):
    """The gate does not cap its own wait: the lease owns contention."""
    from library.tools import axi_qualification_state, resolve_lock

    taken = {}

    class _Lease:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    def _lease(purpose, exclusive=True, timeout=None, **_kwargs):
        taken["purpose"] = purpose
        taken["exclusive"] = exclusive
        taken["timeout"] = timeout
        return _Lease()

    monkeypatch.setattr(resolve_lock, "resolve_lease", _lease)
    monkeypatch.setattr(resolve_lock, "held", lambda: False)
    monkeypatch.setattr(axi_qualification_state, "_read_runtime",
                        lambda *_args, **_kwargs: _runtime())

    axi_qualification_state.inspect(project="Ren Scratch - x",
                                    timeline="Reel 01 - y")

    assert taken["purpose"] == "axi qualification inspection"
    assert taken["exclusive"] is False
    # The lease's OWN budget: `None` means the lease decides, and the
    # lease decides in quarters of an hour.
    assert taken["timeout"] is None
    assert axi_qualification_state.INSPECTION_LEASE_TIMEOUT_SECONDS is None


def test_a_refusal_names_who_holds_the_instance(monkeypatch):
    """The wait is named: the holder is in the reason."""
    from library.tools import axi_qualification_state, resolve_lock

    class _Lease:
        def __enter__(self):
            raise resolve_lock.ResolveBusy(
                "ren-resolved did not grant 'axi qualification inspection': "
                "timed out waiting for Resolve",
                "the broker schedules the one Resolve instance",
                "wait for the running jobs")

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(resolve_lock, "resolve_lease",
                        lambda *_a, **_k: _Lease())
    monkeypatch.setattr(resolve_lock, "held", lambda: False)

    class _Status:
        active = None

    class _Holder:
        def describe(self):
            return "pid-4242 (pid-4242 on Mac): reels build"

    status = _Status()
    status.active = _Holder()
    monkeypatch.setattr(resolve_lock, "lease_status", lambda: status)

    result = axi_qualification_state.inspect(project="Ren Scratch - x",
                                              timeline="Reel 01 - y")

    assert result["inspected"] is False
    assert "pid-4242" in result["reason"]
    assert "reels build" in result["reason"]


def test_a_session_that_holds_the_instance_reuses_its_inspection(monkeypatch):
    """A held session already HAS the inspection: it takes no lease."""
    from library.tools import axi_qualification_state, resolve_lock

    def _lease(*_args, **_kwargs):
        raise AssertionError("a session that holds the instance must not "
                             "ask for a second lease")

    monkeypatch.setattr(resolve_lock, "resolve_lease", _lease)
    monkeypatch.setattr(resolve_lock, "held", lambda: True)
    monkeypatch.setattr(axi_qualification_state, "_read_runtime",
                        lambda *_args, **_kwargs: _runtime())

    result = axi_qualification_state.inspect(project="Ren Scratch - x",
                                              timeline="Reel 01 - y")

    assert result["inspected"] is True
