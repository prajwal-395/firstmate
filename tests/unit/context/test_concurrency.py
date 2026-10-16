"""The routing rule, applied without judgement.

The captain, 2026-09-12: *"today that judgement is mine and I have got
it wrong twice today"*. So the table has to answer the dispatch
question on its own, and these are the answers a supervisor reads.
"""
import json
import os
import select
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import closing
from pathlib import Path

import pytest

from library.tools import declaration_keys as dk
from library.tools import heavy_work_lock, resource_scheduler
from library.tools.concurrency_routing import (
    FREE,
    may_run_together,
    route,
)
from library.tools.declaration_keys import (
    DeclarationConflict,
    DeclarationKeyError,
    edit_declaration,
    entry_key,
    read_entries,
    write_entries,
)

# Bound at import, before conftest's per-test memory fixture runs: the
# one test that asserts the reading comes from the running OS calls this.
_REAL_MEMORY_READING = resource_scheduler._memory_available_gb


BUILD = "library.tools.reel_build.rebuild_reels_in_project"
PROMOTE = "library.tools.reel_build.promote_staged_reels"
RENDER_EDIT = "library.steps.step_6_01_render.resolve_build_timeline"
READ = "library.tools.timeline_ingest.snapshot_timeline"
RECORD = "library.tools.captain_edits.record_edit"
ANALYSE = "library.steps.step_1_03_semantic_analysis"


# ── The rule itself ─────────────────────────────────────────────────

def test_a_cursor_operation_runs_beside_no_other_cursor_operation_or_read():
    """Either order. A read's handle survives a promotion only by luck:
    the promotion deletes timelines."""
    assert not may_run_together(PROMOTE, RENDER_EDIT)
    assert not may_run_together(RENDER_EDIT, PROMOTE)
    assert not may_run_together(PROMOTE, READ)
    assert not may_run_together(READ, PROMOTE)


def test_a_build_runs_beside_nothing_that_takes_the_lease():
    """The build holds the exclusive instance lease for its whole body.

    It used to hold the instance only around its own cursor sections,
    so a second build's derivation and a reader's read proceeded while
    the first build derived. Three concurrent live builds interleaved
    their writes in those gaps (2026-10-05): one build died reading a
    timeline count that no longer matched the project, another's
    staging timeline was deleted under it. The build now takes the
    exclusive lease at its first line and holds it to its last, so it
    runs beside nothing that takes the lease - a second build waits at
    its own first acquisition rather than interleaving.
    """
    assert not may_run_together(BUILD, READ)
    assert not may_run_together(READ, BUILD)
    assert not may_run_together(BUILD, BUILD)
    assert not may_run_together(BUILD, PROMOTE)
    assert not may_run_together(PROMOTE, BUILD)


def test_declaration_writes_are_dispatched_together():
    """They contend per KEY, in `declaration_keys`, not per dispatch.

    Serialising the dispatch would be the coarse answer the key scheme
    exists to avoid - two agents recording two different reels' edits
    have nothing to wait for.
    """
    assert may_run_together(RECORD, "library.tools.reel_signoff")
    assert may_run_together(RECORD, BUILD)


def test_an_unlisted_entry_point_reads_as_free_and_says_why():
    unknown = route("library.tools.nothing_in_particular")
    assert unknown.exclusion == FREE
    assert "Not in OPERATIONS" in unknown.why


# ── The classes mean what the table says they mean ──────────────────

def test_each_class_costs_what_it_declares():
    for entry, exclusive, lease in (
        (BUILD, True, True),
        (PROMOTE, True, True),
        (READ, False, True),
        (RECORD, False, False),
        (ANALYSE, False, False),
    ):
        op = route(entry)
        assert op.is_exclusive() is exclusive, entry
        assert op.takes_lease() is lease, entry


# --------------------------------------------------------------------------
# From test_declaration_keys.py
#
# Different reels never contend; the same decision twice SURFACES.
#
# The two properties the scheme is worth building for, and the second one
# demonstrated across real processes rather than argued.

REPO_ROOT = str(Path(__file__).resolve().parents[3])


@pytest.fixture
def project(tmp_path):
    """A project of our own. Never a real one (AGENTS.md 8)."""
    (tmp_path / "external").mkdir()
    (tmp_path / "project.yaml").write_text("name: contention\n",
                                           encoding="utf-8")
    return tmp_path


def ending(reel, phrase):
    return {"reel": reel, "ends_on": {"anchor_phrase": phrase},
            "tail_element": "none", "reason": f"test: {reel}"}


# ── The key scheme names what each owner already reasons in ─────────


def test_a_captain_edit_is_keyed_by_the_owners_own_identity():
    """Imported, not restated: two answers to one question would drift."""
    from library.tools.captain_edits import _edit_identity
    edit = {"kind": "redraw_closer", "anchor_phrase": "And So We Built",
            "from_phrase": "we built", "reason": "t"}
    assert json.loads(entry_key("captain_edits", edit)) == \
        list(_edit_identity(edit))
    # Case and spacing are the owner's normalisation, so a re-ruling
    # typed differently is still the SAME decision.
    louder = dict(edit, anchor_phrase="  and so  we built ")
    assert entry_key("captain_edits", louder) == entry_key(
        "captain_edits", edit)


def test_a_file_with_no_key_scheme_is_refused_rather_than_overwritten():
    with pytest.raises(DeclarationKeyError, match="not a keyed store"):
        entry_key("pipeline_data", {})


# ── Property one: different reels do not contend ────────────────────

def test_two_writers_on_different_reels_both_land(project):
    """Neither read the other's entry, and neither lost it."""
    base_a, _ = read_entries(project, "reel_ending")
    base_b, _ = read_entries(project, "reel_ending")
    assert base_a == base_b == {}

    write_entries(project, "reel_ending",
                  {"Reel 03": ending("Reel 03", "three")}, base_a)
    # B read BEFORE A wrote, and still does not clobber A.
    write_entries(project, "reel_ending",
                  {"Reel 07": ending("Reel 07", "seven")}, base_b)

    landed, _ = read_entries(project, "reel_ending")
    assert sorted(landed) == ["Reel 03", "Reel 07"]


def test_the_file_the_owners_reader_reads_is_what_lands(project):
    """A store created from nothing must satisfy its OWNER, not us."""
    from library.tools.reel_ending import load_endings
    with edit_declaration(project, "reel_ending") as endings:
        endings["Reel 03"] = ending("Reel 03", "so that is the play")
    assert [e["reel"] for e in load_endings(str(project))] == ["Reel 03"]


def test_a_write_leaves_no_half_file(project):
    """Temp plus replace. A reader never sees a partial store."""
    with edit_declaration(project, "reel_ending") as endings:
        endings["Reel 03"] = ending("Reel 03", "three")
    path = dk.store_path(project, "reel_ending")
    assert json.loads(path.read_text(encoding="utf-8"))["endings"]
    assert not [p for p in path.parent.iterdir() if p.name.endswith(".tmp")]


# ── Property two: a genuine conflict SURFACES ───────────────────────

def test_the_same_reel_changed_twice_raises_rather_than_losing_one(project):
    base, _ = read_entries(project, "reel_ending")
    write_entries(project, "reel_ending",
                  {"Reel 03": ending("Reel 03", "the other writer's words")},
                  base)

    with pytest.raises(DeclarationConflict) as raised:
        write_entries(project, "reel_ending",
                      {"Reel 03": ending("Reel 03", "my words")}, base)
    message = str(raised.value)
    assert "Reel 03" in message
    assert "the other writer's words" in message and "my words" in message
    assert "Nothing was written" in message

    # And nothing was: the other writer's decision is intact.
    landed, _ = read_entries(project, "reel_ending")
    assert landed["Reel 03"]["ends_on"]["anchor_phrase"] == \
        "the other writer's words"


def test_the_same_value_written_twice_is_not_a_conflict(project):
    """Agreement is not contention."""
    base, _ = read_entries(project, "reel_ending")
    same = {"Reel 03": ending("Reel 03", "three")}
    write_entries(project, "reel_ending", same, base)
    write_entries(project, "reel_ending", same, base)
    landed, _ = read_entries(project, "reel_ending")
    assert list(landed) == ["Reel 03"]


def test_a_deletion_is_a_change_like_any_other(project):
    with edit_declaration(project, "reel_ending") as endings:
        endings["Reel 03"] = ending("Reel 03", "three")
        endings["Reel 07"] = ending("Reel 07", "seven")
    with edit_declaration(project, "reel_ending") as endings:
        del endings["Reel 03"]
    landed, _ = read_entries(project, "reel_ending")
    assert list(landed) == ["Reel 07"]


# ── The captain's own store, wired ──────────────────────────────────

def test_record_edit_goes_through_the_key_scheme(project, monkeypatch):
    """The measured lost-update site, now merging per key."""
    import library.tools.captain_edits as ce
    monkeypatch.setattr(ce, "load_transcript", lambda _f: {})
    monkeypatch.setattr(ce, "check_anchor_spoken", lambda *_a: None)

    first = {"kind": "redraw_closer", "anchor_phrase": "one",
             "from_phrase": "a", "reason": "r"}
    second = {"kind": "redraw_closer", "anchor_phrase": "two",
              "from_phrase": "b", "reason": "r"}
    ce.record_edit(project, first, source="lane A")
    ce.record_edit(project, second, source="lane B")

    stored = json.loads(
        (project / "external" / "declarations" / "captain_edits.json")
        .read_text("utf-8"))
    assert sorted(e["anchor_phrase"] for e in stored["value"]) == ["one", "two"]
    assert stored["key"] == ce.CAPTAIN_EDITS_KEY


def test_record_edit_still_supersedes_the_same_decision(project, monkeypatch):
    import library.tools.captain_edits as ce
    monkeypatch.setattr(ce, "load_transcript", lambda _f: {})
    monkeypatch.setattr(ce, "check_anchor_spoken", lambda *_a: None)

    edit = {"kind": "redraw_closer", "anchor_phrase": "one",
            "from_phrase": "a", "reason": "first ruling"}
    ce.record_edit(project, edit)
    _, action = ce.record_edit(project, dict(edit, reason="second ruling"))
    assert action == "superseded"
    stored = json.loads(
        (project / "external" / "declarations" / "captain_edits.json")
        .read_text("utf-8"))
    assert [e["reason"] for e in stored["value"]] == ["second ruling"]


# --------------------------------------------------------------------------
# From test_heavy_work_lock.py
#
# Heavy-work admission: lock ownership, cleanup and the resource scheduler.

REPO_ROOT_2 = Path(__file__).resolve().parents[3]
LOCK_MODULE = "library.tools.heavy_work_lock"


def _environment(home: Path) -> dict:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env.pop("VEP_HEAVY_WORK_OWNER", None)
    env.pop(heavy_work_lock.LOCK_DIR_ENV, None)
    return env


def _lock_path(home: Path) -> Path:
    return home / ".local" / "share" / "vep" / "heavy-work.lock"


def test_reentrant_child_does_not_deadlock_on_its_parent_lock(tmp_path):
    """A gate or render child inherits the owner's token and re-enters."""
    home = tmp_path / "home"
    home.mkdir()
    child = (
        "from library.tools.heavy_work_lock import heavy_work_lock; "
        "exec(\"with heavy_work_lock('nested child'):\\n    pass\")"
    )
    result = subprocess.run(
        [sys.executable, "-m", LOCK_MODULE, "run", "--owner", "outer",
         "--", sys.executable, "-c", child],
        cwd=REPO_ROOT_2, env=_environment(home), timeout=30, check=False)

    assert result.returncode == 0
    assert not _lock_path(home).exists()


def test_gate_runner_releases_lock_after_child_error_exit(tmp_path):
    """A failed full-suite child must not strand the machine lock."""
    home = tmp_path / "home"
    home.mkdir()
    result = subprocess.run(
        [sys.executable, "-m", LOCK_MODULE, "run", "--owner", "test gate",
         "--", sys.executable, "-c", "raise SystemExit(7)"],
        cwd=REPO_ROOT_2, env=_environment(home), timeout=30, check=False)

    assert result.returncode == 7
    assert not _lock_path(home).exists()


def test_gate_runner_releases_lock_after_term_signal(tmp_path):
    """A terminated full-suite child must release the lock before exiting."""
    home = tmp_path / "home"
    home.mkdir()
    lock_path = _lock_path(home)
    process = subprocess.Popen(
        [sys.executable, "-m", LOCK_MODULE, "run", "--owner", "test gate",
         "--", sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=REPO_ROOT_2, env=_environment(home),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 30
    while not lock_path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)

    assert lock_path.exists()
    process.send_signal(signal.SIGTERM)
    assert process.wait(timeout=30) == 128 + signal.SIGTERM
    assert not lock_path.exists()


def test_waiter_names_live_holder_once_and_waits_for_release(
        tmp_path, monkeypatch):
    """A live lock is reported once and stays intact until its owner exits."""
    home = tmp_path / "home"
    home.mkdir()
    lock_path = _lock_path(home)
    monkeypatch.setattr(heavy_work_lock, "HEAVY_LOCK_DIR", lock_path)
    process = None

    with heavy_work_lock.heavy_work_lock(
            "live test holder", "native_refresh:ren-native"):
        # The waiter admits through the live probe in its own process;
        # it inherits the session's plentiful-memory seam from the
        # environment (tests/conftest.py), never the host's pressure.
        process = subprocess.Popen(
            [sys.executable, "-m", LOCK_MODULE, "run", "--owner", "waiter",
             "--profile", "native_refresh:ren-native",
             "--", sys.executable, "-c", "pass"],
            cwd=REPO_ROOT_2, env=_environment(home),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        ready, _, _ = select.select([process.stdout], [], [], 30)
        assert ready
        line = process.stdout.readline()
        assert "live test holder" in line
        assert select.select([process.stdout], [], [], 0.1)[0] == []
        assert process.poll() is None
        assert lock_path.exists()

    assert process is not None
    assert process.wait(timeout=30) == 0
    assert not lock_path.exists()


# ── The resource scheduler behind the lock ───────────────────────────


def test_capability_resource_profiles_are_derived_from_execution_policy():
    profiles = resource_scheduler.profiles()
    assert profiles["semantics.analyse:model_memory"] == {}
    assert profiles["semantics.analyse:inference"] == {}
    assert profiles["render.build:render"] == {}
    assert all(not set(demand).intersection(
        {"cpu", "gpu", "ram_gb", "disk", "resolve_cursor",
         "resolve_render"}) for demand in profiles.values())


def test_admission_guard_uses_live_available_memory(monkeypatch):
    monkeypatch.setattr(resource_scheduler, "_memory_available_gb",
                        lambda: (3.0, 24.0))
    assert resource_scheduler._memory_allows_admission()
    monkeypatch.setattr(resource_scheduler, "_memory_available_gb",
                        lambda: (2.0, 24.0))
    assert not resource_scheduler._memory_allows_admission()


def test_non_memory_demands_never_probe_or_wait_on_memory(scheduler, monkeypatch):
    """Only a declared RAM demand participates in the memory guard."""
    def unexpected_probe():
        raise AssertionError("non-memory work probed live machine memory")

    monkeypatch.setattr(resource_scheduler, "_memory_available_gb",
                        unexpected_probe)
    token = scheduler.acquire(
        "native work",
        resource_scheduler.demand_for("native_refresh:ren-native"))
    try:
        assert scheduler.running_demand(token) == {
            "native_ren_native": 1}
    finally:
        scheduler.release(token)


def test_empty_demand_backfills_past_unsatisfiable_memory_waiter(
        scheduler, monkeypatch):
    """A stranded RAM waiter cannot serialize work with no RAM demand."""
    monkeypatch.setattr(resource_scheduler, "_memory_available_gb",
                        lambda: (0.0, 24.0))
    scheduler._transaction(lambda conn: None)
    now = time.time()
    with closing(sqlite3.connect(scheduler.db_path)) as conn, conn:
        conn.execute(
            "INSERT INTO jobs (token, owner, pid, host, machine_id, state,"
            " demand, enqueued_at, pid_start, heartbeat_at, policy_version)"
            " VALUES (?, ?, ?, ?, ?, 'waiting', ?, ?, ?, ?, ?)",
            ("impossible-memory-waiter", "impossible model", os.getpid(),
             "test-host", resource_scheduler._machine_id(),
             json.dumps({"ram_gb": 99}),
             now - resource_scheduler.BACKFILL_AGE_SECONDS - 60,
             resource_scheduler._pid_start(os.getpid()), now,
             resource_scheduler.SCHEDULER_POLICY_VERSION))

    token = scheduler.acquire("no reservation", {})
    try:
        assert [job["owner"] for job in scheduler.jobs()
                if job["state"] == "running"] == ["no reservation"]
    finally:
        scheduler.release(token)


def test_impossible_memory_reservation_is_refused_immediately(
        scheduler, monkeypatch):
    monkeypatch.setattr(resource_scheduler, "_memory_available_gb",
                        lambda: (20.0, 24.0))
    with pytest.raises(ValueError, match="can provide at most"):
        scheduler.acquire("impossible model", {"ram_gb": 22})


def test_memory_reservations_wait_when_their_sum_exceeds_headroom(
        scheduler, monkeypatch):
    """Two private scheduler grants cannot overbook measured headroom."""
    monkeypatch.setattr(resource_scheduler, "_memory_available_gb",
                        lambda: (12.0, 24.0))
    first = scheduler.acquire("first benchmark", {"ram_gb": 6})
    try:
        # Insert a waiting job into this test's private scheduler and ask the
        # actual admission transaction to evaluate it. No OS memory is reserved.
        token = "second-benchmark-waiter"
        now = time.time()
        with closing(sqlite3.connect(scheduler.db_path)) as conn, conn:
            conn.execute(
                "INSERT INTO jobs (token, owner, pid, host, machine_id, state,"
                " demand, enqueued_at, pid_start, heartbeat_at, policy_version)"
                " VALUES (?, ?, ?, ?, ?, 'waiting', ?, ?, ?, ?, ?)",
                (token, "second benchmark", os.getpid(), "test-host",
                 resource_scheduler._machine_id(),
                 json.dumps({"ram_gb": 6}), now,
                 resource_scheduler._pid_start(os.getpid()), now,
                 resource_scheduler.SCHEDULER_POLICY_VERSION))
        blockers = scheduler._transaction(lambda conn: scheduler._try_admit(
            conn, token, {"ram_gb": 6}, resource_scheduler.capacity()))
        assert blockers == ["live OS memory pressure"]
        assert [(job["owner"], job["state"]) for job in scheduler.jobs()
                if job["owner"].endswith("benchmark")] == [
                    ("first benchmark", "running"),
                    ("second benchmark", "waiting")]
    finally:
        scheduler.release(first)


def test_memory_reading_is_taken_from_the_running_os(monkeypatch):
    # conftest fakes plenty of memory for every test, so this one calls
    # the saved real implementation: the reference below is bound at
    # module import, before any per-test fixture runs. The session also
    # exports the probe's test seam to subprocesses, which the real
    # implementation reads first - deleted here so this is the OS.
    monkeypatch.delenv(resource_scheduler.MEMORY_OVERRIDE_ENV,
                       raising=False)
    reading = _REAL_MEMORY_READING()
    assert reading is not None
    available, total = reading
    assert 0 < available <= total
    monkeypatch.setattr(resource_scheduler, "_memory_available_gb",
                        lambda: None)
    assert not resource_scheduler._memory_allows_admission()


@pytest.fixture
def scheduler(tmp_path, monkeypatch):
    """A private scheduler on a fixed 10-core, 16 GB-usable machine."""
    monkeypatch.setattr(resource_scheduler, "capacity", lambda: {
        "resolve_cursor": 1, "resolve_render": 1, "cpu": 10, "gpu": 1,
        "ram_gb": 16, "disk": 4,
        **{"native_" + part: 1
           for part in resource_scheduler.NATIVE_PARTS}})
    monkeypatch.setattr(resource_scheduler, "_memory_allows_admission",
                        lambda: True)
    monkeypatch.setattr(resource_scheduler, "_memory_available_gb",
                        lambda: (16.0, 16.0))
    monkeypatch.setattr(resource_scheduler, "POLL_SECONDS", 0.02)
    return resource_scheduler.Scheduler(tmp_path / "heavy-work.lock")


def _acquire_in_thread(scheduler, owner, profile):
    """Start an acquisition; return (admitted event, token box)."""
    admitted, box = threading.Event(), []

    def run():
        demand = (profile if isinstance(profile, dict)
                  else resource_scheduler.demand_for(profile))
        box.append(scheduler.acquire(
            owner, demand,
            announce=lambda line: None))
        admitted.set()
    threading.Thread(target=run, daemon=True).start()
    return admitted, box


def test_release_tolerates_legacy_directory_disappearing_during_cleanup(
        scheduler, monkeypatch):
    """A legacy bridge removed during release cannot fail a completed job."""
    token = scheduler.acquire("cleanup race", {})
    assert scheduler.legacy_dir.is_dir()

    def remove_bridge():
        (scheduler.legacy_dir / resource_scheduler.OWNER_FILE).unlink()
        scheduler.legacy_dir.rmdir()

    # Simulate the directory disappearing after _drop_legacy_dir_if_idle's
    # exists check but before its iterdir call.
    monkeypatch.setattr(scheduler, "_legacy_owner", remove_bridge)
    scheduler.release(token, "machine")
    assert scheduler.jobs() == []


def test_heavy_work_run_preserves_child_exit_code_with_absent_legacy_dir(
        tmp_path):
    """A missing legacy bridge leaves the wrapped command's status intact."""
    lock_dir = tmp_path / "missing" / "heavy-work.lock"
    env = os.environ.copy()
    env[heavy_work_lock.LOCK_DIR_ENV] = str(lock_dir)
    result = subprocess.run(
        [sys.executable, "-m", "library.tools.heavy_work_lock", "run",
         "--owner", "exit-code-regression", "--profile", "machine", "--",
         sys.executable, "-c", "raise SystemExit(23)"],
        cwd=Path(__file__).resolve().parents[3], env=env,
        capture_output=True, encoding="utf-8", timeout=30, check=False)
    assert result.returncode == 23, result.stderr
    assert not lock_dir.exists()


def test_heavy_work_run_passes_explicit_ram_reservation(monkeypatch):
    calls = []
    monkeypatch.setattr(heavy_work_lock, "_run_locked",
                        lambda *args: calls.append(args) or 0)
    assert heavy_work_lock.main([
        "run", "--owner", "caption-benchmark", "--profile", "machine",
        "--ram-gb", "7.25", "--", "benchmark-command"]) == 0
    assert calls == [("caption-benchmark", "machine",
                      ["benchmark-command"], 7.25)]


def test_dead_legacy_owner_is_reclaimed_before_admission(scheduler):
    import json
    import socket

    scheduler.legacy_dir.mkdir(parents=True)
    (scheduler.legacy_dir / resource_scheduler.OWNER_FILE).write_text(
        json.dumps({"owner": "orphan", "pid": 2**30,
                    "host": socket.gethostname()}), encoding="utf-8")
    token = scheduler.acquire("replacement", {"cpu": 1},
                              announce=lambda line: None, timeout=10)
    scheduler.release(token)
    assert not scheduler.legacy_dir.exists()


def test_dead_job_reaps_after_network_hostname_changes(scheduler, monkeypatch):
    import json
    import time

    stable_id = "mac-test-machine"
    monkeypatch.setattr(resource_scheduler, "_machine_id", lambda: stable_id)
    old_host = "Mac.attlocal.net"
    monkeypatch.setattr(resource_scheduler.socket, "gethostname",
                        lambda: old_host)
    with scheduler._connect() as conn:
        conn.execute(
            "INSERT INTO jobs (token, owner, pid, host, machine_id, state,"
            " demand, enqueued_at) VALUES (?, ?, ?, ?, ?, 'waiting', ?, ?)",
            ("dead-waiter", "native refresh", 2**30, old_host, stable_id,
             json.dumps({"cpu": 1}), time.time()))
    monkeypatch.setattr(resource_scheduler.socket, "gethostname",
                        lambda: "MacBook-Pro-3.local")
    assert scheduler.jobs() == []


def test_scheduler_wait_timeout_removes_its_waiting_row(scheduler):
    # The defect is the leftover row, which any positive timeout
    # covers: 10 ms was scheduling granularity under load (a slow first
    # loop iteration expired before the waiter even queued), so the
    # bound is generous and the assertion stays on the row.
    profile = "native_refresh:ren-native"
    held = scheduler.acquire("holder", resource_scheduler.demand_for(profile))
    with pytest.raises(resource_scheduler.Cancelled, match="timed out"):
        scheduler.acquire("bounded waiter", resource_scheduler.demand_for(profile),
                          announce=lambda line: None, timeout=2)
    assert [job["owner"] for job in scheduler.jobs()] == ["holder"]
    scheduler.release(held)


def test_live_waiter_is_not_reaped_for_stale_queue_heartbeat(scheduler):
    """A separate live process keeps its row through stale heartbeat checks."""
    # The waiter admits in its own process through the live probe; it
    # inherits the session's plentiful-memory seam (tests/conftest.py),
    # so host pressure never reads as a dead waiter here.
    profile = "native_refresh:ren-native"
    held = scheduler.acquire("holder", resource_scheduler.demand_for(profile))
    waiter_code = (
        f"import sys; sys.path.insert(0, {str(REPO_ROOT_2)!r});"
        " from pathlib import Path;"
        " from library.tools import resource_scheduler as rs;"
        f" s=rs.Scheduler(Path({str(scheduler.legacy_dir)!r}));"
        f" t=s.acquire('live waiter', rs.demand_for({profile!r}),"
        " announce=lambda _: None, timeout=15); s.release(t)")
    waiter = subprocess.Popen([sys.executable, "-c", waiter_code],
                              cwd=REPO_ROOT_2, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True,
                              encoding="utf-8")
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if any(job["owner"] == "live waiter" and job["state"] == "waiting"
                   for job in scheduler.jobs()):
                break
            if waiter.poll() is not None:
                pytest.fail("waiter exited before entering the queue")
            time.sleep(0.02)
        else:
            pytest.fail("waiter did not enter the queue")

        # A recent heartbeat identifies the live waiter even when its
        # recorded start differs from the PID's current process start.
        with closing(sqlite3.connect(scheduler.db_path)) as conn, conn:
            conn.execute("UPDATE jobs SET pid_start = ? WHERE owner = ?",
                         ("different process start", "live waiter"))
        assert any(job["owner"] == "live waiter" for job in scheduler.jobs())

        # Once the identity matches again, lease expiry still must not reap
        # a live queue row whose poll was delayed.
        with closing(sqlite3.connect(scheduler.db_path)) as conn, conn:
            conn.execute("UPDATE jobs SET pid_start = ?, heartbeat_at = ?"
                         " WHERE owner = ?",
                         (resource_scheduler._pid_start(waiter.pid),
                          time.time() - resource_scheduler.LEASE_SECONDS - 1,
                          "live waiter"))
        assert [job["owner"] for job in scheduler.jobs()] == [
            "holder", "live waiter"]
        scheduler.release(held)
        stdout, stderr = waiter.communicate(timeout=30)
        assert waiter.returncode == 0, stdout + stderr
    finally:
        if waiter.poll() is None:
            waiter.terminate()
            waiter.wait(timeout=30)
        if scheduler.jobs() and any(job["owner"] == "holder"
                                    for job in scheduler.jobs()):
            scheduler.release(held)


def test_grant_receipt_separates_queue_wait_and_execution(scheduler):
    token = scheduler.acquire("timed grant", {"cpu": 1},
                              announce=lambda line: None)
    timing = scheduler.timing(token)
    assert timing is not None
    enqueued, admitted = timing
    scheduler.release(token)
    receipt = scheduler.history()[-1]
    assert receipt["queue_wait_s"] == pytest.approx(admitted - enqueued)
    assert receipt["execution_s"] == pytest.approx(
        receipt["finished_at"] - admitted)


def test_scheduler_marks_no_cpu_progress_as_stalled_without_killing(
        scheduler, monkeypatch):
    import sqlite3
    import subprocess
    import time

    token = scheduler.acquire("frozen-holder", {"cpu": 1},
                              announce=lambda line: None)
    with sqlite3.connect(scheduler.db_path) as conn:
        conn.execute("UPDATE jobs SET last_cpu=1, last_cpu_at=?, stalled=0",
                     (time.time() - resource_scheduler.STALL_SECONDS - 1,))
    real_run = resource_scheduler.subprocess.run

    def _frozen_cpu(*args, **kwargs):
        argv = args[0] if args else kwargs.get("args", [])
        parts = [str(a) for a in (argv or [])]
        # Only the stall probe's CPU-time read is frozen; pid identity
        # (`lstart`/`stat`) and the machine identity pass through, or the
        # reaper would read the frozen clock as a recycled pid.
        if "time=" in " ".join(parts):
            return subprocess.CompletedProcess(argv, 0, "00:00:01")
        return real_run(*args, **kwargs)

    monkeypatch.setattr(resource_scheduler.subprocess, "run", _frozen_cpu)
    rows = scheduler.jobs()
    assert rows[0]["stalled"] is True
    assert resource_scheduler._alive(os.getpid())
    scheduler.release(token)


def test_placement_and_gates_are_not_cpu_throttled(scheduler):
    """CPU-heavy gates and Resolve placement use their owning schedulers."""
    gate = scheduler.acquire(
        "gate", resource_scheduler.demand_for("full_suite_gate"))
    placement = scheduler.acquire(
        "placement", resource_scheduler.demand_for("reel.build:apply"))
    second_gate, box = _acquire_in_thread(scheduler, "gate 2",
                                          "full_suite_gate")

    assert second_gate.wait(30)
    scheduler.release(box[0])
    scheduler.release(placement)
    scheduler.release(gate)
    assert scheduler.jobs() == []
    assert not scheduler.legacy_dir.exists()


def test_small_waiter_backfills_past_large_waiter_that_does_not_fit(scheduler):
    """An unrelated empty demand backfills past an exclusive waiter.

    A waiter for the held native resource does not block work that needs no
    scheduler resource.
    """
    native = "native_ren_native"
    holder = scheduler.acquire("holder", {native: 1})
    waiter_token = "large-waiter"
    now = time.time()
    try:
        with closing(sqlite3.connect(scheduler.db_path)) as conn, conn:
            conn.execute(
                "INSERT INTO jobs (token, owner, pid, host, machine_id, state,"
                " demand, enqueued_at, pid_start, heartbeat_at, policy_version)"
                " VALUES (?, ?, ?, ?, ?, 'waiting', ?, ?, ?, ?, ?)",
                (waiter_token, "large waiter", os.getpid(), "test-host",
                 resource_scheduler._machine_id(), json.dumps({native: 1}),
                 now, resource_scheduler._pid_start(os.getpid()), now,
                 resource_scheduler.SCHEDULER_POLICY_VERSION))
        small = scheduler.acquire("small waiter", {})
        relevant = [(job["owner"], job["state"]) for job in scheduler.jobs()
                    if job["owner"] in {"holder", "large waiter", "small waiter"}]
        assert relevant == [("holder", "running"), ("large waiter", "waiting"),
                            ("small waiter", "running")]
        scheduler.release(small)
    finally:
        with closing(sqlite3.connect(scheduler.db_path)) as conn, conn:
            conn.execute("DELETE FROM jobs WHERE token = ?", (waiter_token,))
        scheduler.release(holder)


def test_aged_waiter_only_blocks_backfill_on_its_resource(scheduler):
    """An aged waiter blocks its resource but not unrelated backfill."""
    holder = scheduler.acquire(
        "holder",
        resource_scheduler.demand_for("native_refresh:ren-native"))
    # A separate process holds the grant, so this process's candidate is
    # not a nested request (nested requests backfill past waiters).
    other_process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"])
    with closing(sqlite3.connect(scheduler.db_path)) as conn, conn:
        conn.execute("UPDATE jobs SET pid = ?, pid_start = ?,"
                     " heartbeat_at = ? WHERE token = ?",
                     (other_process.pid,
                      resource_scheduler._pid_start(other_process.pid),
                      time.time(), holder))
    aged_demand = resource_scheduler.demand_for(
        "native_refresh:resolve-control")
    now = time.time()
    with closing(sqlite3.connect(scheduler.db_path)) as conn, conn:
        conn.execute(
            "INSERT INTO jobs (token, owner, pid, host, machine_id, state,"
            " demand, enqueued_at, pid_start, heartbeat_at, policy_version)"
            " VALUES (?, ?, ?, ?, ?, 'waiting', ?, ?, ?, ?, ?)",
            ("aged-waiter-token", "aged waiter", os.getpid(), "test-host",
             resource_scheduler._machine_id(),
             json.dumps(aged_demand, sort_keys=True),
             now - resource_scheduler.BACKFILL_AGE_SECONDS - 60,
             resource_scheduler._pid_start(os.getpid()), now,
             resource_scheduler.SCHEDULER_POLICY_VERSION))
    try:
        announced = []
        seen_aged_waiter = threading.Event()
        stop = threading.Event()
        outcome = {}

        unrelated = scheduler.acquire(
            "unrelated backfill",
            resource_scheduler.demand_for("native_refresh:ren-resolved"))
        scheduler.release(unrelated)

        # Same-resource work still waits behind the older eligible job.
        def wait_for_same_resource():
            def announce(line):
                announced.append(line)
                if "older eligible waiter aged waiter" in line:
                    seen_aged_waiter.set()

            try:
                scheduler.acquire(
                    "same-resource backfill", aged_demand,
                    announce=announce, cancelled=stop.is_set)
            except resource_scheduler.Cancelled as exc:
                outcome["cancelled"] = str(exc)

        backfill_waiter = threading.Thread(
            target=wait_for_same_resource, daemon=True)
        backfill_waiter.start()
        assert seen_aged_waiter.wait(30), (
            f"same-resource waiter was not prioritized: {announced}")
        stop.set()
        backfill_waiter.join(timeout=30)
        assert not backfill_waiter.is_alive(), (
            "backfill did not stop when cancelled")
        assert "stopped waiting" in outcome.get("cancelled", ""), (
            announced, outcome)
        # Making the waiter young still does not affect unrelated work.
        with closing(sqlite3.connect(scheduler.db_path)) as conn, conn:
            conn.execute("UPDATE jobs SET enqueued_at = ? WHERE token = ?",
                         (time.time(), "aged-waiter-token"))
        backfill = scheduler.acquire(
            "backfill",
            resource_scheduler.demand_for("native_refresh:ren-resolved"),
            announce=lambda line: None, timeout=30)
        scheduler.release(backfill)
    finally:
        with closing(sqlite3.connect(scheduler.db_path)) as conn, conn:
            conn.execute("DELETE FROM jobs WHERE token = ?",
                         ("aged-waiter-token",))
        scheduler.release(holder)
        if other_process.poll() is None:
            other_process.terminate()
            other_process.wait(timeout=30)
    assert scheduler.jobs() == []


def test_render_model_and_gate_are_not_static_resource_throttled(scheduler):
    """Resolve's lease and Resolve arbitrate without a static GPU budget."""
    gate = scheduler.acquire(
        "gate", resource_scheduler.demand_for("full_suite_gate"))
    vlm = scheduler.acquire("gemma", resource_scheduler.demand_for(
        "semantics.analyse:inference"))
    render, box = _acquire_in_thread(scheduler, "render",
                                     "render.build:render")
    assert render.wait(30)
    second, box2 = _acquire_in_thread(scheduler, "render 2",
                                      "render.build:render")
    assert second.wait(30), "Resolve's own lease arbitrates render access"
    for token in (box[0], box2[0], gate, vlm):
        scheduler.release(token)


def test_old_code_lock_dir_and_scheduler_exclude_each_other(scheduler):
    """Catches: a lane on pre-scheduler code (the bare mkdir lock) running
    its gate beside a scheduler gate during the switch-over."""
    legacy = scheduler.legacy_dir
    legacy.mkdir(parents=True)
    (legacy / "owner").write_text(json.dumps(
        {"owner": "scripts/full_suite_gate.sh", "pid": 1}), encoding="utf-8")
    admitted, box = _acquire_in_thread(scheduler, "placement",
                                       "reel.build:apply")
    assert not admitted.wait(0.3)

    (legacy / "owner").unlink()
    legacy.rmdir()
    assert admitted.wait(30)
    with pytest.raises(FileExistsError):
        legacy.mkdir()  # what old code does to take the lock
    scheduler.release(box[0])
    assert not legacy.exists()


def test_a_queued_large_job_is_not_starved_by_later_small_ones(scheduler):
    """A native writer waiter does not block unrelated build directories."""
    vlm = scheduler.acquire("gemma", resource_scheduler.demand_for(
        "native_refresh:ren-native"))
    # Model a separate process: nested requests from this process are now
    # allowed to backfill past waiters that require its grant to be released.
    other_process = subprocess.Popen([
        sys.executable, "-c", "import time; time.sleep(30)"])
    with closing(sqlite3.connect(scheduler.db_path)) as conn, conn:
        conn.execute("UPDATE jobs SET pid = ?, pid_start = ?,"
                     " heartbeat_at = ? WHERE token = ?",
                     (other_process.pid,
                      resource_scheduler._pid_start(other_process.pid),
                      time.time(), vlm))
    second, second_box = _acquire_in_thread(
        scheduler, "gemma 2", "native_refresh:ren-native")
    # Explicit synchronisation, not a fixed sleep: the second writer
    # must be observed in the queue before the placement tries to
    # backfill past it.
    queue_deadline = time.monotonic() + 30
    while time.monotonic() < queue_deadline:
        if any(job["owner"] == "gemma 2" and job["state"] == "waiting"
               for job in scheduler.jobs()):
            break
        time.sleep(0.01)
    else:
        pytest.fail("second build writer did not enter the queue")
    placement, box = _acquire_in_thread(scheduler, "placement",
                                        "native_refresh:resolve-control")

    assert placement.wait(30), "unrelated exclusive keys can run together"
    scheduler.release(box[0])
    scheduler.release(vlm)
    assert second.wait(30)
    scheduler.release(second_box[0])
    other_process.terminate()
    other_process.wait(timeout=30)


def test_nested_grant_bypasses_waiter_blocked_by_its_holder(scheduler):
    """A nested request cannot queue behind work waiting for its release."""
    memory = scheduler.acquire(
        "native build", resource_scheduler.demand_for(
            "native_refresh:ren-native"))
    queued = threading.Event()
    admitted, large_box = threading.Event(), []

    def acquire_same_directory():
        large_box.append(scheduler.acquire(
            "same native directory",
            resource_scheduler.demand_for("native_refresh:ren-native"),
            announce=lambda line: queued.set()))
        admitted.set()

    threading.Thread(target=acquire_same_directory, daemon=True).start()
    assert queued.wait(30), "same-directory writer did not enter the queue"

    inference = scheduler.acquire(
        "other native directory", resource_scheduler.demand_for(
            "native_refresh:resolve-control"),
        announce=lambda line: None, timeout=30)
    scheduler.release(inference)
    scheduler.release(memory)

    assert admitted.wait(30), "queued writer must run after holder releases"
    scheduler.release(large_box[0])


def test_a_dead_holder_is_reaped(scheduler):
    """Catches: a crashed job's row wedging every later admission."""
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    token = scheduler.acquire("crashed", resource_scheduler.demand_for(
        "machine"))
    with closing(sqlite3.connect(scheduler.db_path)) as conn, conn:
        conn.execute("UPDATE jobs SET pid = ? WHERE token = ?",
                     (dead.pid, token))

    scheduler.release(scheduler.acquire(
        "next", resource_scheduler.demand_for("machine")))
    assert scheduler.jobs() == []


def test_killed_holder_frees_machine_within_lease(scheduler):
    """Catches 2026-10-08 job 1845: a footage-analysis grant held 6+ min
    past its pid's exit, refusing all other heavy work until deleted by
    hand. A `kill -9` during the grant must free the next acquire within
    the lease bound, on every acquire and on jobs listing."""
    holder_code = (
        f"import sys; sys.path.insert(0, {str(REPO_ROOT_2)!r});"
        " from library.tools import resource_scheduler as rs;"
        f" s=rs.Scheduler(__import__('pathlib').Path({str(scheduler.legacy_dir)!r}));"
        " s.acquire('killed-holder', {'gpu': 1}, announce=lambda l: None);"
        " import time; time.sleep(30)")
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_code],
        cwd=REPO_ROOT_2, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if any(job["owner"] == "killed-holder" and job["state"] == "running"
               for job in scheduler.jobs()):
            break
        time.sleep(0.02)
    else:
        holder.kill()
        holder.wait(timeout=30)
        pytest.fail("holder never reached running")
    holder_pid = holder.pid
    holder.kill()
    holder.wait(timeout=30)
    assert not resource_scheduler._alive(holder_pid)

    started = time.monotonic()
    token = scheduler.acquire("next", {"gpu": 1},
                              announce=lambda line: None, timeout=30)
    elapsed = time.monotonic() - started
    assert elapsed < resource_scheduler.LEASE_SECONDS + 10
    scheduler.release(token)
    assert scheduler.jobs() == []


def test_recycled_pid_does_not_keep_dead_grant(scheduler):
    """A number recycled for a new process never keeps the dead grant.

    Pid liveness alone (`kill(pid, 0)`) answers True for the recycled
    number; the recorded wall-clock start distinguishes the new process
    from the dead holder, so the next acquire still succeeds."""
    token = scheduler.acquire("holder", resource_scheduler.demand_for(
        "machine"))
    with closing(sqlite3.connect(scheduler.db_path)) as conn, conn:
        conn.execute("UPDATE jobs SET pid = ? WHERE token = ?",
                     (1, token))
    assert scheduler.jobs() == []
    scheduler.release(scheduler.acquire(
        "next", resource_scheduler.demand_for("machine")))


def test_stale_heartbeat_lease_frees_grant(scheduler, monkeypatch):
    """The lease is the fallback past pid identity: a holder whose
    heartbeat stopped frees its grant even when its pid still answers."""
    monkeypatch.setattr(resource_scheduler, "LEASE_SECONDS", 0.3)
    monkeypatch.setattr(resource_scheduler, "HEARTBEAT_SECONDS", 0.05)
    token = scheduler.acquire("holder", {"gpu": 1},
                              announce=lambda line: None)
    resource_scheduler._stop_heartbeat(token)
    with closing(sqlite3.connect(scheduler.db_path)) as conn, conn:
        conn.execute("UPDATE jobs SET heartbeat_at = ? WHERE token = ?",
                     (time.time() - 5, token))
    assert scheduler.jobs() == []
    scheduler.release(scheduler.acquire(
        "next", {"gpu": 1}, announce=lambda line: None))


def test_zombie_holder_reads_as_dead():
    """A zombie still answers `kill(pid, 0)` but holds nothing."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    deadline = time.monotonic() + 30
    while proc.poll() is None:
        assert time.monotonic() < deadline, "short-lived child never exited"
        time.sleep(0.01)
    try:
        assert not resource_scheduler._alive(proc.pid)
    finally:
        proc.wait(timeout=5)


def test_abnormal_exit_without_release_frees_grant(scheduler, monkeypatch):
    """An `os._exit` under the grant (no release, no atexit) still frees
    the next acquire within the lease bound."""
    monkeypatch.setattr(resource_scheduler, "LEASE_SECONDS", 0.5)
    monkeypatch.setattr(resource_scheduler, "HEARTBEAT_SECONDS", 0.1)
    exiter_code = (
        f"import sys, os; sys.path.insert(0, {str(REPO_ROOT_2)!r});"
        " from library.tools import resource_scheduler as rs;"
        f" s=rs.Scheduler(__import__('pathlib').Path({str(scheduler.legacy_dir)!r}));"
        " s.acquire('abnormal-holder', {'gpu': 1},"
        " announce=lambda l: None);"
        " os._exit(3)")
    exiter = subprocess.Popen(
        [sys.executable, "-c", exiter_code], cwd=REPO_ROOT_2,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert exiter.wait(timeout=30) == 3
    started = time.monotonic()
    token = scheduler.acquire("next", {"gpu": 1},
                              announce=lambda line: None, timeout=30)
    assert time.monotonic() - started < 30
    scheduler.release(token)
    assert scheduler.jobs() == []


def test_a_nested_section_cannot_grow_its_grant(tmp_path, monkeypatch):
    """Nested code cannot grow into a second exclusive build directory."""
    monkeypatch.setattr(heavy_work_lock, "HEAVY_LOCK_DIR",
                        tmp_path / "heavy-work.lock")
    with (heavy_work_lock.heavy_work_lock(
            "outer", "native_refresh:ren-native"),
          pytest.raises(heavy_work_lock.GrantTooSmall)):
        heavy_work_lock.take_heavy_lock(
            "other writer", "native_refresh:resolve-control")


def test_model_memory_reservation_admits_only_bounded_nested_inference(
        scheduler, monkeypatch):
    """Model sessions constrain nested call sites without GPU budgeting."""
    monkeypatch.setattr(heavy_work_lock, "HEAVY_LOCK_DIR",
                        scheduler.legacy_dir)
    memory_profile = "semantics.analyse:model_memory"
    inference_profile = "semantics.analyse:inference"

    with heavy_work_lock.heavy_work_reservation(
            "semantic weights", memory_profile,
            nested_profiles=(inference_profile,)):
        assert [job["demand"] for job in scheduler.jobs()] == [{}]
        with (pytest.raises(heavy_work_lock.GrantTooSmall),
              heavy_work_lock.heavy_work_lock(
                  "unlisted nested work", "full_suite_gate")):
            pass

        with heavy_work_lock.heavy_work_lock(
                "semantic inference", inference_profile):
            demands = [job["demand"] for job in scheduler.jobs()]
            assert demands.count({}) == 2
            assert len(demands) == 2

            competing, box = _acquire_in_thread(
                scheduler, "second inference", inference_profile)
            assert competing.wait(30), "model passes are not GPU serialized"

        scheduler.release(box[0])
        assert [job["demand"] for job in scheduler.jobs()] == [{}]

    assert scheduler.jobs() == []


def test_sam_memory_session_is_scheduler_owned_and_has_a_ram_free_admission():
    memory = resource_scheduler.demand_for("objects.segment:model_memory")
    inference = resource_scheduler.demand_for("objects.segment:inference")
    assert memory == {}
    assert inference == {}


def test_model_memory_session_yields_when_os_memory_headroom_is_low(monkeypatch):
    from library.tools import heavy_work_lock as lock

    monkeypatch.setattr(lock, "_memory_reservation",
                        {"owned": True, "token": "sam"})
    monkeypatch.setattr(resource_scheduler, "_memory_allows_admission",
                        lambda: False)
    try:
        assert lock.memory_reservation_contended()
    finally:
        monkeypatch.setattr(lock, "_memory_reservation", None)


def test_folded_model_reservation_cannot_be_yielded_independently(monkeypatch):
    from library.tools import heavy_work_lock as lock

    def unexpected_scheduler():
        raise AssertionError("a folded reservation cannot be yielded")

    monkeypatch.setattr(lock, "_memory_reservation", {"owned": False})
    monkeypatch.setattr(lock, "_scheduler", unexpected_scheduler)
    try:
        assert not lock.memory_reservation_contended()
    finally:
        monkeypatch.setattr(lock, "_memory_reservation", None)


def test_memory_session_folds_and_rejects_exclusive_build_profiles(
        scheduler, monkeypatch):
    monkeypatch.setattr(heavy_work_lock, "HEAVY_LOCK_DIR",
                        scheduler.legacy_dir)
    with (heavy_work_lock.heavy_work_lock(
            "ordinary grant", "semantics.analyse:inference"),
          heavy_work_lock.heavy_work_reservation(
              "late memory", "semantics.analyse:model_memory",
              nested_profiles=("semantics.analyse:inference",))):
        pass

    with (pytest.raises(ValueError, match="empty or RAM-only"),
          heavy_work_lock.heavy_work_reservation(
              "not a memory profile", "native_refresh:ren-native",
              nested_profiles=())):
        pass


def test_model_memory_reservation_folds_into_a_covering_enclosing_grant(
        scheduler, monkeypatch):
    """A process-local model session folds into an enclosing heavy section."""
    monkeypatch.setattr(heavy_work_lock, "HEAVY_LOCK_DIR",
                        scheduler.legacy_dir)
    memory_profile = "semantics.analyse:model_memory"
    inference_profile = "semantics.analyse:inference"

    # Process-local outer grant: the reservation folds into it.
    with heavy_work_lock.heavy_work_lock("outer wrapper", "machine"):
        with heavy_work_lock.heavy_work_reservation(
                "Gemma semantic model memory", memory_profile,
                nested_profiles=(inference_profile,)):
            assert len(scheduler.jobs()) == 1
            with heavy_work_lock.heavy_work_lock(
                    "Gemma semantic inference", inference_profile):
                pass
        assert len(scheduler.jobs()) == 1
    assert scheduler.jobs() == []

    # Inherited outer grant: what `heavy_work_lock run --owner X -- ...`
    # hands a vision child. Same folding, same single job.
    token = scheduler.acquire(
        "outer wrapper",
        resource_scheduler.demand_for("machine"),
        announce=lambda line: None)
    monkeypatch.setenv(heavy_work_lock.OWNER_ENV, token)
    try:
        with heavy_work_lock.heavy_work_reservation(
                "Gemma semantic model memory", memory_profile,
                nested_profiles=(inference_profile,)):
            assert len(scheduler.jobs()) == 1
            with heavy_work_lock.heavy_work_lock(
                    "Gemma semantic inference", inference_profile):
                pass
        assert len(scheduler.jobs()) == 1
    finally:
        monkeypatch.delenv(heavy_work_lock.OWNER_ENV, raising=False)
        scheduler.release(token, "machine", {})
    assert scheduler.jobs() == []

    assert scheduler.jobs() == []


# ── Bounded FREE lanes: the Fabric's read-only form, one machine ────

def test_lanes_return_input_order_when_late_clips_finish_first():
    """A lane runner that returns completion order scrambles the clip
    sequence every downstream consumer reads in order (spine blocks,
    coverage, the manifest validator's consecutive-V1 assertion)."""
    from library.tools.concurrency_routing import run_lanes

    def staggered(i):
        time.sleep(0.05 * (5 - i))  # clip 0 finishes last
        return i

    assert run_lanes(staggered, range(6)) == [0, 1, 2, 3, 4, 5]


def test_lanes_match_the_serial_sequence_exactly():
    """The parallel run is identical to the serial run: same items,
    same order, same per-item values - including a failing clip's
    error record, which the caller returns rather than raises."""
    from library.tools.concurrency_routing import run_lanes

    def work(item):
        if item == "clip_002":
            return {"clip_id": item, "error": "boom"}
        time.sleep(0.01)
        return {"clip_id": item, "ok": True}

    items = ["clip_001", "clip_002", "clip_003"]
    assert run_lanes(work, items) == [work(i) for i in items]


def test_lane_width_is_the_machines_measured_cores(monkeypatch):
    """The bound comes from the machine, not from a number in the
    routing table: a two-core Mac and a sixteen-core Studio answer
    differently because they ARE different. A hardcoded width here
    would starve one and oversubscribe the other."""
    import os

    from library.tools.concurrency_routing import lane_width

    monkeypatch.setattr(os, "cpu_count", lambda: 3)
    assert lane_width() == 3
    monkeypatch.setattr(os, "cpu_count", lambda: 16)
    assert lane_width() == 16
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    assert lane_width() == 1


def test_a_raise_inside_a_lane_aborts_the_batch():
    """`fn` owns its per-item failures: a raise is the batch failing,
    not one clip's error record. Callers that must survive a bad clip
    (the temporal index) catch inside `fn`, exactly as their serial
    loop did."""
    from library.tools.concurrency_routing import run_lanes

    def bad(i):
        raise RuntimeError(f"clip {i} failed")

    with pytest.raises(RuntimeError, match="clip 0 failed"):
        run_lanes(bad, range(3))


def test_no_items_means_no_work():
    from library.tools.concurrency_routing import run_lanes

    assert run_lanes(lambda i: i, []) == []
