"""The instance lease, the cursor fence, and the refusal that wires them.

The contention here is REAL where it can be: `test_a_waiter_waits`
spawns two processes that contend for one `flock` on disk. Everything
about exclusion that is only reasoned about is not evidence, which is
how a lock with zero callers came to read as coverage for two days.
"""

import os
import re
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from library.tools import resolve_lock
from library.tools.resolve_lock import (
    ResolveBusy,
    ResolveRaceError,
    UnguardedPlacementError,
    assert_current_timeline,
    cursor_excursion,
    cursor_fence,
    prefer_lease,
    resolve_lease,
)
from tests.resolve_double import FakeProject, FakeTimeline

REPO_ROOT = str(Path(__file__).resolve().parents[3])
REPO = Path(__file__).resolve().parents[3]


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


# ── The per-write check: the two refusals ───────────────────────────

def test_assert_current_timeline_fails_on_mismatch(lock_dir, unguarded):
    expected = FakeTimeline("Reel 03", uid="a")
    project = FakeProject(current=expected)
    # The mutator: the cursor is something else by the time it is read.
    project.moves = [FakeTimeline("Reel 01", uid="b")]
    with resolve_lease("test", timeout=1.0):
        with pytest.raises(ResolveRaceError, match="Timeline race"):
            assert_current_timeline(project, expected)


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


# ── The lease ───────────────────────────────────────────────────────

def test_the_lease_is_reentrant_within_a_process(lock_dir, unguarded):
    with resolve_lease("outer", timeout=1.0):
        with resolve_lease("inner", timeout=1.0):
            assert resolve_lock.held()
        assert resolve_lock.held()


def test_a_shared_holder_may_not_upgrade_in_place(lock_dir, unguarded):
    with resolve_lease("read", exclusive=False, timeout=1.0):
        with pytest.raises(ResolveBusy, match="cannot upgrade in place"):
            with resolve_lease("write", exclusive=True, timeout=1.0):
                pass


_SHARED_HOLDER = textwrap.dedent("""
    import sys
    sys.path.insert(0, {repo!r})
    from library.tools.resolve_lock import resolve_lease
    with resolve_lease("holding a shared read", exclusive=False,
                       timeout=5.0):
        print("HELD", flush=True)
        sys.stdin.readline()
    print("RELEASED", flush=True)
""")


def _start_shared_holder(lock_dir):
    holder = subprocess.Popen(
        [sys.executable, "-c", _SHARED_HOLDER.format(repo=REPO_ROOT)],
        env={**os.environ, resolve_lock.LOCK_DIR_ENV: str(lock_dir)},
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8")
    assert holder.stdout.readline().strip() == "HELD"
    return holder


def test_cursor_excursion_refuses_a_shared_holder(lock_dir, unguarded):
    """A cursor excursion cannot join another shared critical section."""
    home = FakeTimeline("home", uid="home")
    target = FakeTimeline("read target", uid="target")
    project = FakeProject(current=home)
    holder = _start_shared_holder(lock_dir)
    try:
        with resolve_lease("second shared reader", exclusive=False,
                           timeout=1.0):
            with pytest.raises(ResolveBusy, match="cannot upgrade in place"):
                with cursor_excursion(project, target, "stub read"):
                    pytest.fail(
                        "cursor excursion ran alongside a shared holder")
    finally:
        holder.stdin.write("release\n")
        holder.stdin.flush()
        holder.communicate(timeout=10)

    assert project.set_calls == []


def test_cursor_excursion_waits_for_another_shared_holder(lock_dir,
                                                          unguarded):
    """An unshared cursor read takes exclusive and waits for shared readers."""
    home = FakeTimeline("home", uid="home")
    target = FakeTimeline("read target", uid="target")
    project = FakeProject(current=home)
    holder = _start_shared_holder(lock_dir)
    entered = threading.Event()
    errors = []

    def read_target():
        try:
            with cursor_excursion(project, target, "stub read"):
                entered.set()
                assert project.GetCurrentTimeline() is target
        except Exception as exc:  # surfaced in the test thread below
            errors.append(exc)

    reader = threading.Thread(target=read_target)
    reader.start()
    try:
        assert not entered.wait(0.35), (
            "cursor excursion entered while another process held a shared "
            "lease")
        assert project.set_calls == [], (
            "cursor moved before the shared holder released its lease")
    finally:
        holder.stdin.write("release\n")
        holder.stdin.flush()
        holder.communicate(timeout=10)
    reader.join(timeout=10)

    assert not reader.is_alive(), "cursor excursion did not finish"
    assert not errors, errors
    assert entered.is_set()
    assert project.set_calls == ["read target", "home"]
    assert project.GetCurrentTimeline() is home


def test_cursor_write_guard_refuses_a_shared_lease(lock_dir, unguarded):
    target = FakeTimeline("write target", uid="target")
    project = FakeProject(current=target)

    with resolve_lease("shared reader", exclusive=False, timeout=1.0):
        with pytest.raises(UnguardedPlacementError, match="(?i)exclusive"):
            assert_current_timeline(project, target)

    assert project.set_calls == []


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


_SYNCHRONIZED_WAITER = textwrap.dedent("""
    import sys
    sys.path.insert(0, {repo!r})
    from library.tools import resolve_lock
    original_flock = resolve_lock._flock
    def observed_flock(handle, exclusive, blocking):
        acquired = original_flock(handle, exclusive, blocking)
        if not acquired:
            print("CONTENDED", flush=True)
        return acquired
    resolve_lock._flock = observed_flock
    with resolve_lock.resolve_lease("waiting for the demonstration",
                                    owner="waiter", timeout=10.0) as lease:
        print("ACQUIRED %s" % lease.waited_on, flush=True)
""")


def test_a_waiter_waits(lock_dir):
    """Two processes, one guarded operation, and the second one waits.

    The demonstration the design is worth nothing without: not a mock
    of a lock, two real processes on one real `flock`.
    """
    holder = _spawn(_HOLDER.format(repo=REPO_ROOT, seconds=60.0), lock_dir)
    assert holder.stdout.readline().strip() == "HELD"

    waiter = _spawn(_SYNCHRONIZED_WAITER.format(repo=REPO_ROOT), lock_dir)
    assert waiter.stdout.readline().strip() == "CONTENDED"
    # Observe a failed non-blocking flock while the holder is alive.
    holder.kill()
    holder.communicate(timeout=30)
    acquired = waiter.stdout.readline().strip()
    waiter.communicate(timeout=30)

    assert acquired.startswith("ACQUIRED"), acquired
    assert "holder" in acquired and "holding for the demonstration" in acquired


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


# ── Contention measurement: every acquisition says what it waited ──

_WAITER_STATS = textwrap.dedent("""
    import sys, time
    sys.path.insert(0, {repo!r})
    from library.tools.resolve_lock import resolve_lease, ResolveBusy
    try:
        with resolve_lease("waiting for the demonstration", owner="waiter",
                           timeout={timeout}) as lease:
            print("ACQUIRED %.2f %s" % (lease.wait_seconds,
                                        lease.waited_on), flush=True)
    except ResolveBusy as busy:
        print("BUSY %s" % (busy,), flush=True)
""")


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


# ── The human, who never queues ─────────────────────────────────────

def test_the_captains_button_does_not_queue_behind_a_build(lock_dir,
                                                           unguarded):
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


def test_a_child_the_holder_spawned_inherits_the_lease(lock_dir,
                                                           unguarded):
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


_SHARED_CHILD = textwrap.dedent("""
    import sys
    sys.path.insert(0, {repo!r})
    from library.tools.resolve_lock import resolve_lease, ResolveBusy
    try:
        with resolve_lease("child cursor write", timeout=1.0):
            print("UNSAFE", flush=True)
    except ResolveBusy:
        print("REFUSED", flush=True)
""")


def test_a_child_cannot_upgrade_an_inherited_shared_lease(lock_dir,
                                                            unguarded):
    with resolve_lease("shared parent read", exclusive=False, timeout=1.0):
        child = _spawn(_SHARED_CHILD.format(repo=REPO_ROOT), lock_dir)
        out, err = child.communicate(timeout=10)

    assert out.strip() == "REFUSED", (out, err)


def test_a_stale_inherited_pid_does_not_grant_the_lease(lock_dir,
                                                        monkeypatch):
    """A killed holder must not leave its children writing unguarded."""
    monkeypatch.setenv(resolve_lock.INHERIT_ENV, "999999")
    assert resolve_lock.inherited_holder() is None


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
    roots = ("library", "tests", "scripts")
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


# ── A holder's own cursor excursion is not interference ─────────────

def test_an_excursion_inside_a_fence_is_not_reported_as_drift(lock_dir):
    """The holder reading another timeline is not a foreign writer.

    A carried read-back has to make the reel it reads CURRENT - what
    Resolve returns for a transform depends on which timeline that is
    (`reel_rebuild_need.carried_digest_live`). Measured on the merge of
    #1040: done with a bare `assert_current_timeline`, the enclosing
    fence charged the holder's own move to `drift_seen` and raised at
    exit naming the captain, while the cursor had in fact been put back
    exactly where the fence expected it. A guard that cries wolf about
    its own holder is worse than no guard.
    """
    home = FakeTimeline("home reel", uid="id-home")
    other = FakeTimeline("reel being read", uid="id-other")
    project = FakeProject(current=home)
    with resolve_lock.cursor_fence(project, home, "a section",
                                   timeout=1.0) as fence:
        with resolve_lock.cursor_excursion(project, other, "read"):
            assert project.GetCurrentTimeline() is other
        assert project.GetCurrentTimeline() is home
    assert fence.drift_seen == []


# ── The captain's signal: deference, not just detection ─────────────
#
# The asymmetry under test: an agent that finds the captain present
# WAITS, where a fence that finds a foreign cursor move RAISES. Both
# behaviours are correct for their own direction, and the lease is the
# choke point that carries the new one - `cursor_fence` and
# `under_lease` honour it by calling `resolve_lease`, `prefer_lease`
# bypasses it because a human-initiated action never queues behind its
# own owner's hold.

def test_an_acquisition_waits_while_the_captain_is_in_resolve(lock_dir,
                                                               unguarded):
    """Set signal, acquire in a thread, prove it waits, clear it, proceed.

    Fails on the old shape, where no signal exists to wait on: the
    thread would walk straight into the lease instead of holding.
    """
    resolve_lock.captain_hold("grading the finale")
    acquired = threading.Event()
    outcome = {}

    def wait_for_it():
        try:
            with resolve_lease("an agent build", timeout=30.0):
                outcome["lease"] = True
        except Exception as error:  # noqa: BLE001 - reported below
            outcome["error"] = error
        finally:
            acquired.set()

    waiter = threading.Thread(target=wait_for_it, daemon=True)
    try:
        waiter.start()
        assert not acquired.wait(1.5), (
            "the acquisition did not wait for the captain's signal - "
            f"{outcome}")
        assert "error" not in outcome, outcome.get("error")
        assert resolve_lock.captain_release() is True
        assert acquired.wait(30), (
            "the acquisition never proceeded after the signal cleared")
        assert "error" not in outcome, outcome.get("error")
        assert outcome.get("lease") is True
    finally:
        resolve_lock.captain_release()
        waiter.join(timeout=30)


def test_a_signal_that_never_clears_raises_instead_of_wedging(lock_dir,
                                                               unguarded):
    """The stale decision: bounded wait, then a refusal that names the hold.

    Fails on the old shape, where the acquisition would take the lease
    straight through the captain's hold. Nothing auto-clears it and
    nothing proceeds past it - the raise IS the guarantee, stated.
    """
    resolve_lock.captain_hold("left on overnight")
    try:
        started = time.time()
        with pytest.raises(resolve_lock.CaptainPresent) as raised:
            with resolve_lease("an agent build", timeout=1.0):
                pass  # pragma: no cover - the wait never clears
        elapsed = time.time() - started
        assert elapsed >= 1.0, elapsed
        assert elapsed < 10.0, elapsed
        message = str(raised.value)
        assert resolve_lock.CAPTAIN_FILENAME in message, message
        assert "captain-release" in message, message
        assert issubclass(resolve_lock.CaptainPresent,
                          resolve_lock.ResolveBusy)
    finally:
        resolve_lock.captain_release()


def test_a_held_lease_is_never_revoked_by_the_signal(lock_dir, unguarded):
    """The holder finishes its section; finishing is not clearing.

    A half-written timeline is worse than a delayed one, so the signal
    gates ACQUISITION only. The fence still detects any cursor move
    that actually happened meanwhile - that half is unchanged.
    """
    mine = FakeTimeline("Reel 03", uid="a")
    project = FakeProject(current=mine)
    with resolve_lease("an agent build", timeout=5.0):
        resolve_lock.captain_hold("walked in mid-build")
        assert_current_timeline(project, mine)
    assert project.set_calls == ["Reel 03"]
    try:
        assert resolve_lock.captain_present() is not None
    finally:
        resolve_lock.captain_release()


def test_an_unreadable_signal_still_counts_as_set(lock_dir):
    """Fail-closed, like `pipeline.hold`: presence is the signal."""
    resolve_lock.captain_path().write_text("not json{{{", encoding="utf-8")
    try:
        assert resolve_lock.captain_present() is not None
    finally:
        resolve_lock.captain_release()


def test_the_unguarded_prefer_path_never_establishes_the_cursor():
    """`prefer_lease` proceeds unguarded on contention, so the path it
    guards must not move the cursor at all.

    2026-09-20: an unleased `SetCurrentTimeline` collided with a
    sibling lane's Fusion pass. The captain's button (the sole
    `prefer=True` entry point, `marker_capture.capture`) takes the
    lease when free and goes ahead when held - safe only because it
    never establishes the cursor: playhead and stills read off the
    passed timeline handle, markers land on that same handle. A
    holder's fence watches the cursor, so a path that cannot move it
    cannot trip one. If this path ever needs the cursor current, it
    must take the real lease first rather than grow a setter here.
    """
    import ast
    from pathlib import Path
    source = (Path(__file__).resolve().parents[3]
              / "library" / "tools" / "marker_capture.py").read_text(
                  encoding="utf-8")
    setters = [node.lineno for node in ast.walk(ast.parse(source))
               if isinstance(node, ast.Attribute)
               and node.attr == "SetCurrentTimeline"]
    assert setters == [], (
        f"marker_capture.py establishes the cursor at lines {setters}: "
        f"the prefer_lease path goes ahead unguarded on contention, so "
        f"a setter here would walk a sibling holder's cursor with no "
        f"lease and no refusal.")
