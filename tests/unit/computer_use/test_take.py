"""The UI take's contract: one bounded exclusive lease per take.

Each test names the defect it catches. The two that matter: a take
whose bound already passed refuses before touching Resolve (an
unbounded take wedges the fleet behind it - measured 818 s in
2026-10-04), and every verb enters through the same choke point so a
snapshot, reset or sibling job cannot run between two steps of a take.
"""

from __future__ import annotations

from contextlib import contextmanager, nullcontext

import pytest

from library.tools.computer_use.take import (
    TakeExpired,
    take_length_seconds,
    ui_take,
)


def _stub_take(monkeypatch, entered: list, connected: list):
    borrowed_from = {
        "project": "Podcast (field test)",
        "timeline_id": "timeline-11",
        "timeline": "Reel 11",
        "page": "edit",
    }
    monkeypatch.setattr(
        "library.tools.resolve_lock.resolve_lease",
        lambda purpose, **kw: entered.append((purpose, kw)) or nullcontext(),
    )
    monkeypatch.setattr(
        "library.tools.marker_feedback.connect_resolve",
        lambda: connected.append(True) or object(),
    )
    monkeypatch.setattr(
        "library.tools.qualification_project.borrowed",
        lambda resolve: nullcontext(borrowed_from),
    )


def test_an_expired_take_refuses_before_touching_resolve(monkeypatch):
    """A take whose bound already passed raises TakeExpired before the
    handshake, the borrow or any UI: a take that cannot fit its bound
    never starts."""
    entered: list = []
    connected: list = []
    _stub_take(monkeypatch, entered, connected)
    with pytest.raises(TakeExpired) as exc:
        with ui_take("computer-use run", take_seconds=0):
            raise AssertionError("an expired take must not run its body")
    assert exc.value.code == "TAKE_EXPIRED"
    assert entered == []
    assert connected == []


def test_the_take_purpose_names_the_bound(monkeypatch):
    """The lease purpose carries the take and its bound, so the broker
    grant and the lease file show what is busy and for how long. A
    bare purpose would read as any other lease."""
    entered: list = []
    connected: list = []
    _stub_take(monkeypatch, entered, connected)
    with ui_take("computer-use run", take_seconds=60) as take:
        assert take.resolve is not None
        assert take.deadline > 0
        assert take.borrowed_from == {
            "project": "Podcast (field test)",
            "timeline_id": "timeline-11",
            "timeline": "Reel 11",
            "page": "edit",
        }
    assert take.restore_verified is True
    assert len(entered) == 1
    purpose, kwargs = entered[0]
    assert "take" in purpose and "60s" in purpose
    assert kwargs.get("exclusive") is True


def test_a_restore_mismatch_survives_the_take_context(monkeypatch):
    """The take exposes a failed borrowed() read-back so the live
    replay can stop before opening another take on a mismatched cursor."""
    entered: list = []
    connected: list = []
    _stub_take(monkeypatch, entered, connected)

    @contextmanager
    def mismatched_restore(_resolve):
        yield {"project": "Podcast", "timeline_id": "timeline-11",
               "timeline": "Reel 11", "page": "edit"}
        raise RuntimeError("timeline id did not read back")

    monkeypatch.setattr(
        "library.tools.qualification_project.borrowed", mismatched_restore)
    take = None
    with pytest.raises(RuntimeError, match="timeline id"):
        with ui_take("computer-use run", take_seconds=60) as take:
            pass
    assert take is not None
    assert take.restore_verified is False
    assert "timeline id did not read back" in take.restore_error


def test_the_take_bound_defaults_and_overrides(monkeypatch):
    """The bound defaults to the documented length and honors
    `$COMPUTER_USE_TAKE_SECONDS`: an operator throttling a slow model
    has one knob, not code edits."""
    from library.tools.computer_use.take import TAKE_LENGTH_SECONDS

    monkeypatch.delenv("COMPUTER_USE_TAKE_SECONDS", raising=False)
    assert take_length_seconds() == TAKE_LENGTH_SECONDS
    monkeypatch.setenv("COMPUTER_USE_TAKE_SECONDS", "120")
    assert take_length_seconds() == 120.0
    monkeypatch.setenv("COMPUTER_USE_TAKE_SECONDS", "nonsense")
    assert take_length_seconds() == TAKE_LENGTH_SECONDS
