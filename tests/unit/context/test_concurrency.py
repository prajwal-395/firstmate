"""The routing rule, applied without judgement.

The captain, 2026-09-12: *"today that judgement is mine and I have got
it wrong twice today"*. So the table has to answer the dispatch
question on its own, and these are the answers a supervisor reads.
"""
from library.tools.concurrency_routing import (
    FREE,
    may_run_together,
    route,
)
import json
from pathlib import Path
import pytest
from library.tools import declaration_keys as dk
from library.tools.declaration_keys import (
    DeclarationConflict,
    DeclarationKeyError,
    edit_declaration,
    entry_key,
    read_entries,
    write_entries,
)
import os
import select
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import closing
from library.tools import heavy_work_lock, resource_scheduler


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
        cwd=REPO_ROOT_2, env=_environment(home), timeout=5, check=False)

    assert result.returncode == 0
    assert not _lock_path(home).exists()


def test_gate_runner_releases_lock_after_child_error_exit(tmp_path):
    """A failed full-suite child must not strand the machine lock."""
    home = tmp_path / "home"
    home.mkdir()
    result = subprocess.run(
        [sys.executable, "-m", LOCK_MODULE, "run", "--owner", "test gate",
         "--", sys.executable, "-c", "raise SystemExit(7)"],
        cwd=REPO_ROOT_2, env=_environment(home), timeout=5, check=False)

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
    deadline = time.monotonic() + 3
    while not lock_path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)

    assert lock_path.exists()
    process.send_signal(signal.SIGTERM)
    assert process.wait(timeout=5) == 128 + signal.SIGTERM
    assert not lock_path.exists()


def test_waiter_names_live_holder_once_and_waits_for_release(
        tmp_path, monkeypatch):
    """A live lock is reported once and stays intact until its owner exits."""
    home = tmp_path / "home"
    home.mkdir()
    lock_path = _lock_path(home)
    monkeypatch.setattr(heavy_work_lock, "HEAVY_LOCK_DIR", lock_path)
    process = None

    with heavy_work_lock.heavy_work_lock("live test holder"):
        process = subprocess.Popen(
            [sys.executable, "-m", LOCK_MODULE, "run", "--owner", "waiter",
             "--", sys.executable, "-c", "pass"],
            cwd=REPO_ROOT_2, env=_environment(home),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        ready, _, _ = select.select([process.stdout], [], [], 3)
        assert ready
        line = process.stdout.readline()
        assert "live test holder" in line
        assert select.select([process.stdout], [], [], 0.1)[0] == []
        assert process.poll() is None
        assert lock_path.exists()

    assert process is not None
    assert process.wait(timeout=5) == 0
    assert not lock_path.exists()


# ── The resource scheduler behind the lock ───────────────────────────


def test_capability_resource_profiles_are_derived_from_execution_policy():
    profiles = resource_scheduler.profiles()
    assert profiles["semantics.analyse:model_memory"] == {"ram_gb": 8}
    assert profiles["semantics.analyse:inference"] == {
        "cpu": 2, "gpu": 1}
    assert profiles["render.build:render"]["resolve_cursor"] == 1
    assert "local_vlm" not in profiles
    assert "resolve_render" not in profiles


@pytest.fixture
def scheduler(tmp_path, monkeypatch):
    """A private scheduler on a fixed 10-core, 16 GB-usable machine."""
    monkeypatch.setattr(resource_scheduler, "capacity", lambda: {
        "resolve_cursor": 1, "resolve_render": 1, "cpu": 10, "gpu": 1,
        "ram_gb": 16, "disk": 4})
    monkeypatch.setattr(resource_scheduler, "POLL_SECONDS", 0.02)
    return resource_scheduler.Scheduler(tmp_path / "heavy-work.lock")


def _acquire_in_thread(scheduler, owner, profile):
    """Start an acquisition; return (admitted event, token box)."""
    admitted, box = threading.Event(), []

    def run():
        box.append(scheduler.acquire(
            owner, resource_scheduler.demand_for(profile),
            announce=lambda line: None))
        admitted.set()
    threading.Thread(target=run, daemon=True).start()
    return admitted, box


def test_dead_legacy_owner_is_reclaimed_before_admission(scheduler):
    import json
    import socket

    scheduler.legacy_dir.mkdir(parents=True)
    (scheduler.legacy_dir / resource_scheduler.OWNER_FILE).write_text(
        json.dumps({"owner": "orphan", "pid": 2**30,
                    "host": socket.gethostname()}), encoding="utf-8")
    token = scheduler.acquire("replacement", {"cpu": 1},
                              announce=lambda line: None, timeout=1)
    scheduler.release(token)
    assert not scheduler.legacy_dir.exists()


def test_dead_job_reaps_after_network_hostname_changes(scheduler, monkeypatch):
    import json
    import sqlite3
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
    held = scheduler.acquire("holder", resource_scheduler.demand_for("machine"))
    with pytest.raises(resource_scheduler.Cancelled, match="timed out"):
        scheduler.acquire("bounded waiter", {"cpu": 1},
                          announce=lambda line: None, timeout=0.01)
    assert [job["owner"] for job in scheduler.jobs()] == ["holder"]
    scheduler.release(held)


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
    monkeypatch.setattr(resource_scheduler.subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess(a[0], 0, "00:00:01"))
    rows = scheduler.jobs()
    assert rows[0]["stalled"] is True
    assert resource_scheduler._alive(os.getpid())
    scheduler.release(token)


def test_placement_runs_beside_a_gate_and_a_second_gate_waits(scheduler):
    """Catches: the global mutex queueing a seconds-long Resolve placement
    behind a minutes-long gate, and two full-suite gates running at once."""
    gate = scheduler.acquire(
        "gate", resource_scheduler.demand_for("full_suite_gate"))
    placement = scheduler.acquire(
        "placement", resource_scheduler.demand_for("reel.build:apply"))
    second_gate, box = _acquire_in_thread(scheduler, "gate 2",
                                          "full_suite_gate")

    assert not second_gate.wait(0.3)
    scheduler.release(placement)
    assert not second_gate.wait(0.3)
    scheduler.release(gate)
    assert second_gate.wait(5)
    scheduler.release(box[0])
    assert scheduler.jobs() == []
    assert not scheduler.legacy_dir.exists()


def test_a_render_is_admitted_beside_a_gate_and_a_model_run(scheduler):
    """Catches: the render's unmeasured CPU/GPU/RAM demand queueing it
    behind a full-suite gate or a Gemma run while it already holds the
    Resolve lease - 249 s of an idle, held Resolve on 2026-10-03."""
    gate = scheduler.acquire(
        "gate", resource_scheduler.demand_for("full_suite_gate"))
    vlm = scheduler.acquire("gemma", resource_scheduler.demand_for(
        "semantics.analyse:inference"))
    render, box = _acquire_in_thread(scheduler, "render",
                                     "render.build:render")
    assert render.wait(5)
    second, box2 = _acquire_in_thread(scheduler, "render 2",
                                      "render.build:render")
    assert not second.wait(0.3), "one Resolve render engine"
    for token in (box[0], gate, vlm):
        scheduler.release(token)
    assert second.wait(5)
    scheduler.release(box2[0])


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
    assert admitted.wait(5)
    with pytest.raises(FileExistsError):
        legacy.mkdir()  # what old code does to take the lock
    scheduler.release(box[0])
    assert not legacy.exists()


def test_a_queued_large_job_is_not_starved_by_later_small_ones(scheduler):
    """Catches: placements arriving back to back keeping a second model
    run queued forever while each one fits beside the running one."""
    vlm = scheduler.acquire("gemma", resource_scheduler.demand_for(
        "semantics.analyse:inference"))
    # Model a separate process: nested requests from this process are now
    # allowed to backfill past waiters that require its grant to be released.
    other_process = subprocess.Popen([
        sys.executable, "-c", "import time; time.sleep(30)"])
    with closing(sqlite3.connect(scheduler.db_path)) as conn, conn:
        conn.execute("UPDATE jobs SET pid = ? WHERE token = ?",
                     (other_process.pid, vlm))
    second, _ = _acquire_in_thread(
        scheduler, "gemma 2", "semantics.analyse:inference")
    time.sleep(0.1)  # the second model run queues first, on the gpu
    placement, box = _acquire_in_thread(scheduler, "placement",
                                        "reel.build:apply")

    assert not placement.wait(0.3)
    scheduler.release(vlm)
    assert second.wait(5)
    other_process.terminate()
    other_process.wait(timeout=5)


def test_nested_grant_bypasses_waiter_blocked_by_its_holder(scheduler):
    """A nested request cannot queue behind work waiting for its release."""
    memory = scheduler.acquire(
        "analysis memory", resource_scheduler.demand_for(
            "semantics.analyse:model_memory"))
    large, large_box = _acquire_in_thread(scheduler, "full machine",
                                          "machine")
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if any(job["owner"] == "full machine" and job["state"] == "waiting"
               for job in scheduler.jobs()):
            break
        time.sleep(0.01)
    else:
        pytest.fail("full-machine job did not enter the queue")

    inference = scheduler.acquire(
        "analysis inference", resource_scheduler.demand_for(
            "semantics.analyse:inference"),
        announce=lambda line: None, timeout=2)
    scheduler.release(inference)
    scheduler.release(memory)

    assert large.wait(5), "queued full-machine job must run after holder releases"
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


def test_a_nested_section_cannot_grow_its_grant(tmp_path, monkeypatch):
    """Catches: a placement-sized grant silently admitting a nested render,
    which needs the gpu it never reserved."""
    monkeypatch.setattr(heavy_work_lock, "HEAVY_LOCK_DIR",
                        tmp_path / "heavy-work.lock")
    with heavy_work_lock.heavy_work_lock("outer", "reel.build:apply"):
        with pytest.raises(heavy_work_lock.GrantTooSmall):
            heavy_work_lock.take_heavy_lock("render", "render.build:render")


def test_model_memory_reservation_admits_only_bounded_nested_inference(
        scheduler, monkeypatch):
    """RAM stays held while GPU admissions come and go in a fixed order."""
    monkeypatch.setattr(heavy_work_lock, "HEAVY_LOCK_DIR",
                        scheduler.legacy_dir)
    memory_profile = "semantics.analyse:model_memory"
    inference_profile = "semantics.analyse:inference"

    with heavy_work_lock.heavy_work_reservation(
            "semantic weights", memory_profile,
            nested_profiles=(inference_profile,)):
        assert [job["demand"] for job in scheduler.jobs()] == [
            {"ram_gb": 8}]
        with pytest.raises(heavy_work_lock.GrantTooSmall):
            with heavy_work_lock.heavy_work_lock(
                    "unlisted nested work", "full_suite_gate"):
                pass

        with heavy_work_lock.heavy_work_lock(
                "semantic inference", inference_profile):
            demands = [job["demand"] for job in scheduler.jobs()]
            assert {"ram_gb": 8} in demands
            assert {"cpu": 2, "gpu": 1} in demands
            assert len(demands) == 2

            competing, box = _acquire_in_thread(
                scheduler, "second inference", inference_profile)
            assert not competing.wait(0.1), "the GPU admits one tenant"

        assert competing.wait(5)
        scheduler.release(box[0])
        assert [job["demand"] for job in scheduler.jobs()] == [
            {"ram_gb": 8}]

    assert scheduler.jobs() == []


def test_memory_reservation_must_be_outermost_and_ram_only(
        scheduler, monkeypatch):
    monkeypatch.setattr(heavy_work_lock, "HEAVY_LOCK_DIR",
                        scheduler.legacy_dir)
    with heavy_work_lock.heavy_work_lock(
            "ordinary grant", "semantics.analyse:inference"):
        with pytest.raises(heavy_work_lock.GrantTooSmall):
            with heavy_work_lock.heavy_work_reservation(
                    "late memory", "semantics.analyse:model_memory",
                    nested_profiles=("semantics.analyse:inference",)):
                pass

    with pytest.raises(ValueError, match="RAM only"):
        with heavy_work_lock.heavy_work_reservation(
                "not a memory profile", "semantics.analyse:inference",
                nested_profiles=()):
            pass


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
