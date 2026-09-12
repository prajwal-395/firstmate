"""The instance lease, the cursor fence, and the refusal that wires them.

The contention here is REAL where it can be: `test_a_waiter_waits`
spawns two processes that contend for one `flock` on disk. Everything
about exclusion that is only reasoned about is not evidence, which is
how a lock with zero callers came to read as coverage for two days.
"""

import json
import os
import re
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from library.tools import resolve_lock
from library.tools.resolve_lock import (
    ResolveBusy,
    ResolveRaceError,
    UnguardedPlacementError,
    assert_current_timeline,
    cursor_fence,
    prefer_lease,
    resolve_lease,
)

REPO_ROOT = str(Path(__file__).resolve().parent.parent)
REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def lock_dir(tmp_path, monkeypatch):
    """A lease of our own, never the machine's.

    `PIPELINE_RESOLVE_LOCK_DIR` exists for exactly this: a test that
    competed for the real lease would block on - or block - the
    captain's live session.
    """
    directory = tmp_path / "locks"
    directory.mkdir()
    monkeypatch.setenv(resolve_lock.LOCK_DIR_ENV, str(directory))
    return directory


@pytest.fixture
def unguarded(monkeypatch):
    """Undo conftest's session-wide sole-writer declaration.

    The suite declares itself the sole writer because its Resolve is a
    mock. A test ABOUT the guard has to stand outside that.
    """
    monkeypatch.setattr(resolve_lock, "_sole_writer_reason", None)


class FakeTimeline:
    def __init__(self, name, uid=None):
        self.name = name
        self.uid = uid or name

    def GetName(self):
        return self.name

    def GetUniqueId(self):
        return self.uid


class FakeProject:
    """A Resolve project whose cursor can be moved from outside.

    `moves` is the uncooperative writer: a list of timelines the
    project silently becomes current on, one per `GetCurrentTimeline`
    call, standing in for the captain clicking another tab.
    """

    def __init__(self, current=None, moves=()):
        self.current = current
        self.moves = list(moves)
        self.set_calls = []

    def SetCurrentTimeline(self, timeline):
        self.set_calls.append(timeline.GetName())
        self.current = timeline
        return True

    def GetCurrentTimeline(self):
        if self.moves:
            self.current = self.moves.pop(0)
        return self.current


# ── The per-write check: the two refusals ───────────────────────────

def test_assert_current_timeline_fails_on_mismatch(lock_dir):
    expected = FakeTimeline("Reel 03", uid="a")
    project = FakeProject(current=expected)
    # The mutator: the cursor is something else by the time it is read.
    project.moves = [FakeTimeline("Reel 01", uid="b")]
    with resolve_lease("test", timeout=1.0):
        with pytest.raises(ResolveRaceError, match="Timeline race"):
            assert_current_timeline(project, expected)


def test_assert_current_timeline_passes_on_match(lock_dir):
    expected = FakeTimeline("Reel 03", uid="a")
    project = FakeProject(current=expected)
    with resolve_lease("test", timeout=1.0):
        assert_current_timeline(project, expected)
    assert project.set_calls == ["Reel 03"]


def test_a_write_without_the_lease_is_refused(lock_dir, unguarded):
    """The whole repair: taking the lease is not a rule a caller may forget."""
    expected = FakeTimeline("Reel 03")
    project = FakeProject(current=expected)
    with pytest.raises(UnguardedPlacementError) as raised:
        assert_current_timeline(project, expected)
    assert "Reel 03" in str(raised.value)
    assert "cursor_fence" in str(raised.value)
    # And it refused BEFORE moving anything.
    assert project.set_calls == []


def test_the_lease_satisfies_the_refusal(lock_dir, unguarded):
    expected = FakeTimeline("Reel 03")
    project = FakeProject(current=expected)
    with resolve_lease("build reels", timeout=1.0):
        assert_current_timeline(project, expected)
    with pytest.raises(UnguardedPlacementError):
        assert_current_timeline(project, expected)


# ── The lease ───────────────────────────────────────────────────────

def test_the_lease_is_reentrant_within_a_process(lock_dir):
    with resolve_lease("outer", timeout=1.0):
        with resolve_lease("inner", timeout=1.0):
            assert resolve_lock.held()
        assert resolve_lock.held()


def test_a_shared_holder_may_not_upgrade_in_place(lock_dir):
    with resolve_lease("read", exclusive=False, timeout=1.0):
        with pytest.raises(ResolveBusy, match="cannot upgrade in place"):
            with resolve_lease("write", exclusive=True, timeout=1.0):
                pass


def test_the_lease_names_its_holder_on_disk(lock_dir):
    with resolve_lease("build reels", owner="lane-12", timeout=1.0):
        recorded = json.loads(
            resolve_lock.lease_path().read_text(encoding="utf-8"))
        assert recorded["owner"] == "lane-12"
        assert recorded["purpose"] == "build reels"
        assert recorded["pid"] == os.getpid()
        assert "build reels" in resolve_lock.holder().describe()
    assert not resolve_lock.lease_path().exists()


# ── Real contention, across processes ───────────────────────────────

_HOLDER = textwrap.dedent("""
    import sys, time
    sys.path.insert(0, {repo!r})
    from library.tools.resolve_lock import resolve_lease
    with resolve_lease("holding for the demonstration", owner="holder",
                       timeout=5.0):
        print("HELD", flush=True)
        time.sleep({seconds})
    print("RELEASED", flush=True)
""")

_WAITER = textwrap.dedent("""
    import sys, time
    sys.path.insert(0, {repo!r})
    from library.tools.resolve_lock import resolve_lease, ResolveBusy
    started = time.time()
    try:
        with resolve_lease("waiting for the demonstration", owner="waiter",
                           timeout={timeout}):
            print("ACQUIRED %.2f" % (time.time() - started), flush=True)
    except ResolveBusy as busy:
        print("BUSY %.2f %s" % (time.time() - started, busy), flush=True)
""")


def _spawn(source, lock_dir):
    env = dict(os.environ)
    env[resolve_lock.LOCK_DIR_ENV] = str(lock_dir)
    return subprocess.Popen([sys.executable, "-c", source], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8")


def test_a_waiter_waits(lock_dir):
    """Two processes, one guarded operation, and the second one waits.

    The demonstration the design is worth nothing without: not a mock
    of a lock, two real processes on one real `flock`.
    """
    holder = _spawn(_HOLDER.format(repo=REPO_ROOT, seconds=1.0), lock_dir)
    assert holder.stdout.readline().strip() == "HELD"

    waiter = _spawn(_WAITER.format(repo=REPO_ROOT, timeout=10.0), lock_dir)
    waited, _ = waiter.communicate(timeout=30)
    holder.communicate(timeout=30)

    assert waited.startswith("ACQUIRED"), waited
    # It did not walk straight in: the holder was there for a second.
    assert float(waited.split()[1]) >= 0.7, waited


def test_a_waiter_that_gives_up_names_the_holder(lock_dir):
    """A bounded wait with a diagnostic, which is the anti-deadlock.

    Four full-suite runs wedged at 0% CPU on 2026-09-11 because the
    wait had no bound and said nothing. This is that repaired.
    """
    holder = _spawn(_HOLDER.format(repo=REPO_ROOT, seconds=3.0), lock_dir)
    assert holder.stdout.readline().strip() == "HELD"

    waiter = _spawn(_WAITER.format(repo=REPO_ROOT, timeout=0.5), lock_dir)
    gave_up, _ = waiter.communicate(timeout=30)
    holder.kill()
    holder.communicate(timeout=30)

    assert gave_up.startswith("BUSY"), gave_up
    assert "holder" in gave_up and "holding for the demonstration" in gave_up
    assert float(gave_up.split()[1]) < 3.0, gave_up


# ── The fence: an uncooperative writer, DETECTED ────────────────────

def test_the_fence_detects_a_foreign_cursor_move(lock_dir):
    """The captain clicks another timeline mid-section, and we find out.

    The lock held and the cursor moved underneath it three times on
    2026-09-12. Preventing that is not available - a human never takes
    a lock - so the requirement is that it is DETECTED and named.
    """
    mine = FakeTimeline("Reel 03", uid="a")
    theirs = FakeTimeline("Reel 01 (the captain opened this)", uid="b")
    project = FakeProject(current=mine)

    with pytest.raises(ResolveRaceError) as raised:
        with cursor_fence(project, mine, "place captions") as fence:
            # ... work happens ... and the captain clicks another tab.
            project.moves = [theirs]
            assert fence.drift_seen == []
    message = str(raised.value)
    assert "Reel 01 (the captain opened this)" in message
    assert "does not take the lease" in message


def test_the_fence_reports_a_move_it_corrected_mid_section(lock_dir):
    """Drift caught by a write's own check still fails the section.

    `assert_current_timeline` re-establishes the cursor, so the write
    after it lands correctly - but a write BEFORE it may not have, and
    a section that silently repaired a race it never reported is the
    same blind spot in a smaller window.
    """
    mine = FakeTimeline("Reel 03", uid="a")
    theirs = FakeTimeline("Reel 01", uid="b")
    project = FakeProject(current=mine)

    with pytest.raises(ResolveRaceError, match="inside 'place captions'"):
        with cursor_fence(project, mine, "place captions"):
            project.moves = [theirs]          # read by the check below
            assert_current_timeline(project, mine)


def test_a_fence_nobody_disturbed_passes_and_reports_nothing(lock_dir):
    mine = FakeTimeline("Reel 03", uid="a")
    project = FakeProject(current=mine)
    with cursor_fence(project, mine, "place captions") as fence:
        assert_current_timeline(project, mine)
    assert fence.drift_seen == []


# ── The human, who never queues ─────────────────────────────────────

def test_the_captains_button_does_not_queue_behind_a_build(lock_dir):
    holder = _spawn(_HOLDER.format(repo=REPO_ROOT, seconds=3.0), lock_dir)
    assert holder.stdout.readline().strip() == "HELD"
    try:
        started = time.time()
        with prefer_lease("capture a frame for firstmate", timeout=0.5) as got:
            elapsed = time.time() - started
            assert got is None, "the lease was free; the test proved nothing"
            # It went ahead - and a write inside it is permitted, because
            # refusing the captain's own button is not the answer.
            assert resolve_lock.held()
        assert elapsed < 2.5, elapsed
    finally:
        holder.kill()
        holder.communicate(timeout=30)


def test_the_captains_button_takes_the_lease_when_it_is_free(lock_dir):
    with prefer_lease("capture a frame for firstmate", timeout=1.0) as got:
        assert got is not None
        assert got.purpose == "capture a frame for firstmate"


# ── The exemption is explained, or it is not an exemption ───────────

def test_declaring_sole_writer_needs_a_reason():
    with pytest.raises(ValueError, match="needs a reason"):
        with resolve_lock.assume_sole_writer(""):
            pass


# ── A child the holder spawned must not deadlock on its parent ──────

_CHILD = textwrap.dedent("""
    import sys, time
    sys.path.insert(0, {repo!r})
    from library.tools.resolve_lock import resolve_lease, ResolveBusy
    started = time.time()
    try:
        with resolve_lease("the child's own section", timeout=3.0):
            print("INHERITED %.2f" % (time.time() - started), flush=True)
    except ResolveBusy:
        print("DEADLOCKED", flush=True)
""")


def test_a_child_the_holder_spawned_inherits_the_lease(lock_dir):
    """`resolve_build_timeline` holds it and launches `apply_fusion_comps`.

    The parent is blocked waiting on the child, so a child that queued
    for a lock its own parent holds is a self-deadlock with a
    fifteen-minute fuse. Without this the wiring would have converted
    every Fusion pass into an outage.
    """
    with resolve_lease("build the edit timeline", timeout=1.0):
        child = _spawn(_CHILD.format(repo=REPO_ROOT), lock_dir)
        out, err = child.communicate(timeout=30)
    assert out.startswith("INHERITED"), (out, err)
    assert float(out.split()[1]) < 1.0, out


def test_a_stale_inherited_pid_does_not_grant_the_lease(lock_dir,
                                                        monkeypatch):
    """A killed holder must not leave its children writing unguarded."""
    monkeypatch.setenv(resolve_lock.INHERIT_ENV, "999999")
    assert resolve_lock.inherited_holder() is None


def test_an_unrelated_process_does_not_inherit(lock_dir):
    """Only a DESCENDANT sees the variable - it travels downward only."""
    holder = _spawn(_HOLDER.format(repo=REPO_ROOT, seconds=2.0), lock_dir)
    assert holder.stdout.readline().strip() == "HELD"
    try:
        waiter = _spawn(_WAITER.format(repo=REPO_ROOT, timeout=0.5), lock_dir)
        out, _ = waiter.communicate(timeout=30)
        assert out.startswith("BUSY"), out
    finally:
        holder.kill()
        holder.communicate(timeout=30)


# ── The gate PR 1039 left behind, pointed at the lock that now exists ─
#
# It read: "a lock that comes back with no callers is the original
# defect returning under its own name ... a lock that comes back WIRED
# is fine and this test says so by passing", and it told a wiring lane
# to correct the removal note and the measurement in the same commit.
# Both were corrected. What it cannot be left checking is
# `resolve_placement_lock`, a name nothing defines and nothing calls:
# that comparison is now True == True forever and cannot fail, which
# is the shape AGENTS.md 10.4 forbids. So it checks the lease.

def _lease_call_sites() -> list:
    """Every entry into the instance lease in the repository.

    The definition and this file are not entries, so `with`/`@` forms
    are matched rather than the bare name - a grep for the name alone
    counts the module that defines it and the test that checks it, and
    would report a dead lock as wired.
    """
    pattern = re.compile(
        r"(?:with\s+|@)(?:[\w.]+\.)?(?:resolve_lease|under_lease|"
        r"cursor_fence|prefer_lease)\b")
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
    lease without wiring it re-creates a guard nobody enters; a caller
    left behind by a removal is caught here rather than at import time
    on a build path.
    """
    defined = hasattr(resolve_lock, "resolve_lease")
    callers = _lease_call_sites()
    assert defined == bool(callers), (
        f"library/tools/resolve_lock.py "
        f"{'defines' if defined else 'does not define'} resolve_lease "
        f"and the repository has {len(callers)} caller(s) {callers}. A "
        f"lock nothing enters serialises nothing and reads as coverage "
        f"(AGENTS.md 10.4) - either wire it or leave it removed.")


def test_the_lease_is_wired_into_the_paths_that_write():
    """Not "at least one caller" - the write paths, by name.

    `tests/test_resolve_guard_wiring.py` checks each entry point
    against `concurrency_routing`; this is the blunt count that would
    catch a mass unwiring the table was edited to match.
    """
    # Not resolve_lock.py itself: the module composes its own helpers,
    # and counting those would report a lock nobody else takes as wired
    # - the exact reading the removed one got away with.
    in_library = [site for site in _lease_call_sites()
                  if site.startswith("library/")
                  and "resolve_lock.py" not in site]
    assert len(in_library) >= 8, in_library


def test_the_removal_note_survives_and_says_why():
    """The module must keep stating what was removed and on what basis.

    The note is the only thing standing between the next reader and
    re-adding the ORIGINAL shape - a lock a caller had to remember to
    take - for the reason it was added the first time. It now also has
    to say what changed, because a module that carried only the
    removal would read as though nothing guards placement across
    processes, which is no longer true.
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
    assert "OVERTURNED" in source, (
        "that measurement concluded a reader racing a writer is "
        "unmediated by design. A SHARED lease mediates it, so the "
        "module must say which of its conclusions no longer holds "
        "rather than silently contradicting it")
