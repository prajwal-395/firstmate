"""Named failure injection for the edit lifecycle's QA-02 boundaries."""

from __future__ import annotations

from collections import Counter

QA02_FAULT_POINTS = (
    "candidate_creation",
    "media_placement",
    "fusion",
    "read_back",
    "project_model_persistence",
    "promotion_rename",
    "version_recording",
    "archive_creation",
    "cleanup",
    "broker_restart_or_termination",
)


class InjectedFailure(RuntimeError):
    """An intentional failure at a named edit-lifecycle boundary."""


class FaultInjector:
    """Wrap real or fake boundary functions with a one-shot fault.

    Tests bind a point to the production function they are exercising;
    the injector does not substitute a second implementation of that
    boundary. ``occurrence`` selects a later call, such as the post-write
    read-back after the precondition read.
    """

    def __init__(self, point: str, *, occurrence: int = 1) -> None:
        if point not in QA02_FAULT_POINTS:
            raise ValueError(f"unknown QA-02 fault point: {point}")
        if occurrence < 1:
            raise ValueError("fault occurrence must be positive")
        self.point = point
        self.occurrence = occurrence
        self.calls: Counter[str] = Counter()
        self.triggered = False

    def wrap(self, monkeypatch, owner, attribute: str):
        original = getattr(owner, attribute)

        def injected(*args, **kwargs):
            self.calls[self.point] += 1
            if self.calls[self.point] == self.occurrence:
                self.triggered = True
                raise InjectedFailure(
                    f"injected failure at {self.point} ({attribute})")
            return original(*args, **kwargs)

        monkeypatch.setattr(owner, attribute, injected)
        return injected
