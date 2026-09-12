import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from library.tools import resolve_lock
from library.tools.resolve_lock import ResolveRaceError, assert_current_timeline

REPO = Path(__file__).resolve().parents[1]


def test_assert_current_timeline_fails_on_mismatch():
    project = MagicMock()
    expected = MagicMock()
    expected.GetUniqueId.return_value = "expected-123"

    current = MagicMock()
    current.GetUniqueId.return_value = "rogue-456"

    project.GetCurrentTimeline.return_value = current

    with pytest.raises(ResolveRaceError) as exc:
        assert_current_timeline(project, expected)

    assert "Mutator changed it" in str(exc.value)


def test_assert_current_timeline_passes_on_match():
    project = MagicMock()
    expected = MagicMock()
    expected.GetUniqueId.return_value = "expected-123"

    # current matches expected
    current = MagicMock()
    current.GetUniqueId.return_value = "expected-123"

    project.GetCurrentTimeline.return_value = current

    # Should not raise
    assert_current_timeline(project, expected)


# ── The placement lock, and why there is not one ─────────────────────
#
# `resolve_placement_lock` was removed on 2026-09-12 because nothing
# entered it. Two measurements agreed on that, taken from opposite
# sides and recorded in `docs/DUAL_WORKFLOW_SYNC_2026-09-12.md`: 366
# samples over twelve minutes found it UNHELD while a sibling lane
# built into the same Resolve project, and a run that HELD it
# exclusively for two minutes watched another process move the current
# timeline three times anyway. `flock` worked exactly as written; the
# state it guarded was reached by a route that did not pass through it.
#
# What is worth a test is not the lock - it is gone - but the pair of
# facts that justified removing it, which can drift apart silently. A
# lock that comes back with no callers is the original defect returning
# under its own name (AGENTS.md 10.4: a gate that cannot fail reads as
# coverage). A lock that comes back WIRED is fine and this test says so
# by passing.


def _placement_lock_call_sites() -> list:
    """Every entry into `resolve_placement_lock` in the repository.

    The definition and the import in this file are not entries, so
    `with`/`@` forms are matched rather than the bare name - a grep for
    the name alone counts the module that defines it and the test that
    checks it, and would report a dead lock as wired.
    """
    pattern = re.compile(r"(?:with|@)\s+(?:[\w.]+\.)?resolve_placement_lock\b")
    roots = ("library", "tests", "scripts", "resolve_scripts",
             "resolve_workflow_integration")
    found = []
    for root in roots:
        for path in (REPO / root).rglob("*.py"):
            if path == Path(__file__):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):  # pragma: no cover
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if pattern.search(line):
                    found.append(f"{path.relative_to(REPO)}:{number}")
    return found


def test_the_lock_exists_exactly_when_something_enters_it():
    """Defined iff called. Either half alone is the defect.

    This fails in both directions, which is the point. Re-adding the
    context manager without wiring it re-creates a guard nobody enters;
    wiring it without re-adding it cannot happen, but a caller left
    behind by the removal would be caught here rather than at import
    time on a build path.
    """
    defined = hasattr(resolve_lock, "resolve_placement_lock")
    callers = _placement_lock_call_sites()
    assert defined == bool(callers), (
        f"library/tools/resolve_lock.py "
        f"{'defines' if defined else 'does not define'} "
        f"resolve_placement_lock and the repository has "
        f"{len(callers)} caller(s) {callers}. A lock nothing enters "
        f"serialises nothing and reads as coverage (AGENTS.md 10.4) - "
        f"either wire it or leave it removed. If you are wiring it, "
        f"correct the removal note in resolve_lock.py and the "
        f"measurement in docs/DUAL_WORKFLOW_SYNC_2026-09-12.md in the "
        f"same commit."
    )


def test_the_removal_note_survives_and_says_why():
    """The module must keep stating what was removed and on what basis.

    The note is the only thing standing between the next reader and
    re-adding the lock for the reason it was added the first time. A
    silent deletion of the note would leave `assert_current_timeline`
    looking like cross-process serialisation, which is what the two
    measurements found it is not.
    """
    source = (REPO / "library/tools/resolve_lock.py").read_text(
        encoding="utf-8")
    assert "resolve_placement_lock" in source, (
        "the note recording why the placement lock was removed is gone "
        "from resolve_lock.py")
    assert "assert_current_timeline" in source
    assert "DUAL_WORKFLOW_SYNC_2026-09-12" in source, (
        "resolve_lock.py must point at the measurement that removed the "
        "lock, or the removal is an assertion with no evidence behind it")
