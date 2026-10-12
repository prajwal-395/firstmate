"""Continuous edit detection: watch the shadow while a project is open.

The gap this closes (F-04): detection was point-in-time - `timeline_shadow.observe`
ran only when Ren next touched the reel, so a hand edit between builds was
invisible until then, and a later build could silently overwrite it.

This module schedules the observe at a slow cadence while a project is open,
and infers a soft "captain may be editing" flag from the `observed` generations
that appear. When the flag is fresh, a build refuses before placement rather
than risk writing beside a live hand edit.

The broker hosts the watcher, so it starts and stops with the broker and
follows the open project; `ren shadow-watch` is the standalone form. Its
snapshot jobs use the broker's lowest priority so they never delay the captain
or a build.

Read-only against Resolve: the watcher submits `timeline.observe_current`
jobs to the broker, which holds the exclusive lease for the read and never
moves the timeline cursor. The watcher writes only the shadow store (via the
job), the edit history, and the soft flag.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from library.tools import timeline_shadow
from library.tools.resolve_lock import lock_dir

#: How often the watcher observes Resolve's current timeline.
CADENCE_SECONDS = 30.0

#: The watcher's own sleep reference, patchable in tests without
#: touching the global `time.sleep`.
_sleep = time.sleep

#: How long the soft flag stays fresh. A build that starts within this
#: window of an observed hand edit refuses before placement; after it, the
#: flag is stale and reads as absent.
INFERENCE_TIMEOUT_SECONDS = 300.0

#: The broker's lowest priority, so a watcher job never delays the captain
#: or a build.
BROKER_PRIORITY = "export"

FLAG_NAME = "resolve_captain_inferred.flag"

#: The watcher's heartbeat file, written every tick so `ren doctor` can
#: report whether the watcher is alive. A watcher that dies without a
#: heartbeat is a dead watcher nobody reported.
HEARTBEAT_NAME = "shadow_watcher_heartbeat.json"

#: How many cadences without a heartbeat before the watcher reads as dead.
#: One missed tick is a slow tick; two is a dead watcher.
DEAD_AFTER_CADENCES = 2


def inferred_flag_path() -> Path:
    return lock_dir() / FLAG_NAME


def heartbeat_path() -> Path:
    return lock_dir() / HEARTBEAT_NAME


def write_heartbeat(*, project_name: str, project_folder: str,
                    cadence: float) -> None:
    """Write the watcher's heartbeat: when, who, what it watches, and where.

    `project_folder` is the project's absolute root on disk, or empty
    when the open project maps to no Ren project.
    """
    path = heartbeat_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "since": time.time(),
        "pid": os.getpid(),
        "project": project_name,
        "project_folder": project_folder,
        "cadence": cadence,
    }, indent=2) + "\n", encoding="utf-8")


def read_heartbeat() -> Optional[dict]:
    """The watcher's heartbeat when fresh, or None.

    Returns `{"since", "pid", "project", "project_folder", "cadence"}`
    when the heartbeat exists and is fresh (within `DEAD_AFTER_CADENCES`
    of its cadence), None when it is missing, unreadable, or stale.
    """
    try:
        record = json.loads(heartbeat_path().read_text(encoding="utf-8"))
        since = float(record.get("since", 0) or 0)
        cadence = float(record.get("cadence", 0) or 0)
    except (OSError, ValueError, TypeError):
        return None
    if not since or not cadence:
        return None
    if (time.time() - since) >= DEAD_AFTER_CADENCES * cadence:
        return None
    return {
        "since": since,
        "pid": int(record.get("pid", 0) or 0),
        "project": str(record.get("project", "")),
        "project_folder": str(record.get("project_folder", "")),
        "cadence": cadence,
    }


def infer_captain_editing(*, timeline: str, generation: int,
                          project: str = "") -> None:
    """Set the soft "captain may be editing" flag.

    Called by the watcher when an `observed` generation appears that no Ren
    act explains. The flag is a file, like `captain_hold`, so any process
    (the next build) can read it without shared memory state.
    """
    path = inferred_flag_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "since": time.time(),
        "project": project,
        "timeline": timeline,
        "generation": generation,
    }, indent=2) + "\n", encoding="utf-8")


def captain_may_be_editing() -> bool:
    """Read the soft flag; True if set and fresh.

    An absent or stale flag reads as False: the inference is a hint, not a
    lock, and a stale hint must not make every later build refuse.
    """
    return captain_editing_reel() is not None


def captain_editing_reel() -> Optional[dict]:
    """The soft flag's details when fresh, or None.

    Returns `{"timeline", "generation", "project", "since"}` when the flag
    is set and fresh, None otherwise. A build reads this to refuse before
    placement, naming the reel and the observed hand-edit generation.
    """
    path = inferred_flag_path()
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        since = float(record.get("since", 0) or 0)
    except (OSError, ValueError, TypeError):
        return None
    if not since:
        return None
    if (time.time() - since) >= INFERENCE_TIMEOUT_SECONDS:
        return None
    return {
        "timeline": str(record.get("timeline", "")),
        "generation": int(record.get("generation", 0) or 0),
        "project": str(record.get("project", "")),
        "since": since,
    }


def clear_captain_editing_inference() -> bool:
    """Clear the soft flag. True if one stood."""
    try:
        inferred_flag_path().unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


#: "0" keeps a broker from hosting the watcher. The test suite sets it for
#: the session, because a broker a test starts is a real process: its
#: watcher read the live Resolve project against the machine's shadow
#: store. Unset, every broker hosts it.
HOSTED_ENV = "REN_SHADOW_WATCHER"


def hosting_enabled() -> bool:
    """Whether a broker hosts the watcher (`HOSTED_ENV` is not "0")."""
    return os.environ.get(HOSTED_ENV, "") != "0"


class WatcherTickFailed(RuntimeError):
    """A tick observed nothing: a snapshot job failed or never finished."""


def should_stand_down() -> Optional[str]:
    """Why the watcher must not submit now, or None.

    A borrowed qualification session has Resolve: submitting a background
    observation then reads the temporary project and can race the borrower's
    timeline work - the collision a live IntelliSearch run measured (its
    observations disagreed with the scripting reads while snapshots ran).
    Stand-down is not failure: no jobs, no heartbeat, just a note in the log.
    Detection is delayed, never lost - heads persist in the store, so
    the first tick after resume compares across the blackout and files
    any hand edit made while away, before the next build reads the
    flag.
    """
    from library.tools import qualification_project

    session = qualification_project.qualification_session_active()
    if session is None:
        return None
    return (f"qualification session on {session['project']!r} "
            f"(pid {session['pid']})")


class ShadowWatcher:
    """Observe Resolve's current timeline on a schedule.

    The watcher is a thin scheduling loop: it submits
    `timeline.observe_current` jobs to the broker and reads the results.
    The broker fingerprints the timeline already current when the job runs,
    without moving Resolve's cursor, and refreshes the full shadow only when
    that fingerprint changes. Old shadow heads are not swept: a broker restart
    therefore observes only the timeline the captain already has open.
    The watcher writes only the shadow store (via the job), the edit history,
    and the soft flag.

    The broker hosts it (`resolved/server.serve`), so it runs whenever the
    broker does; `ren shadow-watch` is the standalone foreground form.
    """

    def __init__(self, store=None, cadence_seconds: float = CADENCE_SECONDS,
                 clock: Callable[[], float] = time.time,
                 submit: Optional[Callable] = None,
                 result: Optional[Callable] = None) -> None:
        """`submit(kind, params, owner=)` and `result(job_id, wait=)` reach
        the broker; by default its socket client. The broker that hosts the
        watcher passes its own queue instead: the client would `ensure` a
        broker, and from a thread inside one that spawns a second broker
        onto the same socket, or - once a test has restored its lock dir -
        onto the production one."""
        self.store = store or timeline_shadow.ShadowStore()
        self._submit = submit
        self._result = result
        self.cadence_seconds = cadence_seconds
        self._clock = clock
        self._stopping = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def tick(self, project_name: str, project_folder: str = "") -> list:
        """Observe the current timeline once; return new observed generations.

        A current timeline whose live state changed since its last recorded
        generation gets a `manual_edit` entry filed and the soft flag set. An
        unchanged timeline is a no-op. Old heads are not queried, including
        names of staging timelines Resolve no longer has.

        A job that fails, is rejected, or does not finish raises
        `WatcherTickFailed`, so the loop writes no heartbeat.

        A borrowed qualification session stands the tick down first: it
        returns [] having submitted nothing, which is an idle pass, not a
        failure - so no heartbeat either, and the loop stays quiet.
        """
        from library.tools.resolved import client

        reason = should_stand_down()
        if reason is not None:
            return []
        submit = self._submit or client.submit
        result_of = self._result or client.result
        previous_heads = {
            head.timeline_id: head for head in self.store.heads(project_name)
        }
        detected = []
        job = submit("timeline.observe_current", {
            "project": project_name,
            "priority": BROKER_PRIORITY,
        }, owner="shadow_watcher")
        finished = result_of(job["id"], wait=60.0)
        if finished is None or finished.get("state") != "done":
            raise WatcherTickFailed(
                f"timeline.observe_current did not finish: "
                f"{(finished or {}).get('state', 'missing')} "
                f"{(finished or {}).get('error') or ''}".rstrip())
        result = finished.get("result") or {}
        timeline_id = result["timeline_id"]
        returned_generation = int(result["generation"])
        previous = previous_heads.get(timeline_id)
        if (previous is not None
                and returned_generation > previous.generation
                and result["source"] == timeline_shadow.OBSERVED):
            generation = self.store.head(project_name, timeline_id)
            if generation is not None:
                self._file_manual_edit(project_folder,
                                       generation.timeline_name, generation)
                detected.append(generation)
        return detected

    def _file_manual_edit(self, project_folder: str, timeline_name: str,
                          generation) -> None:
        """File a manual-edit entry and set the soft flag."""
        infer_captain_editing(timeline=timeline_name,
                              generation=generation.generation,
                              project=generation.project)
        if not project_folder:
            return
        try:
            from library.tools import reel_edit_history
            from library.tools.reel_read import rows_of
            from library.tools.versions.rounds import digest_rows

            snapshot = self.store.snapshot(generation)
            rows = rows_of(snapshot)
            live_rows_digest = digest_rows(rows)
            review_dir = os.path.join(project_folder, "pipeline_output",
                                      "review")
            reel_edit_history.detect_manual_edit(
                review_dir, timeline_name, live_rows_digest,
                summary=f"watcher: generation {generation.generation} "
                        f"observed on {timeline_name}")
        except Exception:
            pass

    def start(self, project_name_factory: Callable[[], Optional[str]],
              project_folder_factory: Callable[[str], str] = lambda name: "") -> None:
        """Start the background watcher thread.

        `project_name_factory` returns the open project's name, or None
        when no project is open. `project_folder_factory` takes the
        project name and returns the project's folder on disk (empty
        when unknown).
        """
        if self._thread is not None:
            return
        self._stopping.clear()
        self._thread = threading.Thread(
            target=self._run, args=(project_name_factory,
                                   project_folder_factory),
            daemon=True, name="shadow_watcher")
        self._thread.start()

    def stop(self) -> None:
        """Stop the background watcher thread."""
        self._stopping.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    def _run(self, project_name_factory, project_folder_factory) -> None:
        stood_down = False
        while not self._stopping.is_set():
            project_name = project_name_factory()
            reason = should_stand_down() if project_name else None
            if project_name and reason is None:
                try:
                    project_folder = project_folder_factory(project_name)
                    self.tick(project_name, project_folder)
                    write_heartbeat(project_name=project_name,
                                    project_folder=project_folder,
                                    cadence=self.cadence_seconds)
                except Exception as exc:                      # noqa: BLE001
                    # No heartbeat, so doctor reads "not recording"; the
                    # reason goes to stderr (the broker's log when hosted).
                    print(f"shadow watcher: tick on {project_name!r} "
                          f"failed: {type(exc).__name__}: {exc}",
                          file=sys.stderr, flush=True)
                stood_down = False
            else:
                if reason is not None and not stood_down:
                    print(f"shadow watcher: standing down - {reason}",
                          file=sys.stderr, flush=True)
                stood_down = reason is not None
            self._stopping.wait(self.cadence_seconds)


def main(argv=None) -> int:
    """`ren shadow-watch <project_folder>`: start the watcher on a project.

    The watcher observes Resolve's current timeline in the open project at the
    configured cadence, using the broker's lowest priority so it never
    delays the captain or a build.

    The watcher is hosted by the broker - it starts when the broker
    starts and stops when the broker stops. This foreground form is
    for running it standalone (tests, debugging).
    """
    import argparse

    # Same default as the front door, for direct `-m` use: under a
    # harness the host parses `REN_RESULT` instead of prose (LE-03).
    # A `ren` verb already set it through the environment, and an
    # explicit value always wins.
    from ren import hosts
    hosts.ensure_harness_output_env()

    parser = argparse.ArgumentParser(
        prog="ren shadow-watch",
        description="Watch Resolve's current timeline for hand edits on a schedule.")
    parser.add_argument("project_folder",
                        help="the project folder to watch")
    parser.add_argument("--cadence", type=float, default=CADENCE_SECONDS,
                        help=f"seconds between observations "
                             f"(default {CADENCE_SECONDS})")
    args = parser.parse_args(argv)

    from library.schemas.project_config import load_project_config

    # The folder names its Resolve project. Asking Resolve which project is
    # open would need a scripting handshake, which is refused outside the
    # instance lease - so it returned None on every tick and the watcher
    # never observed. Where another project is open, the broker's snapshot
    # job refuses, the tick fails, and no heartbeat is written.
    project_folder = str(Path(args.project_folder).expanduser().absolute())
    name = load_project_config(
        Path(project_folder) / "project.yaml",
        migrate_format=False).resolve.project_name
    if not name:
        print(f"{project_folder}/project.yaml names no resolve.project_name",
              file=sys.stderr)
        return 2

    watcher = ShadowWatcher(cadence_seconds=args.cadence)
    watcher.start(lambda: name, lambda name: project_folder)
    print(f"shadow watcher started on {project_folder} ({name!r}) - "
          f"observing the current timeline at {args.cadence}s cadence",
          file=sys.stderr)
    try:
        while True:
            _sleep(1.0)
    except KeyboardInterrupt:
        watcher.stop()
    return 0



if __name__ == "__main__":
    # ONE place renders every refusal on this verb's path, in the same
    # shape as the front door. With `REN_OUTPUT=json` both a refusal
    # and an uncaught error end with one final `REN_RESULT` line (LE-03).
    from library.tools.ren_refusal import (
        REFUSAL_EXIT_CODE, RenRefusal, emit_refusal_result,
        report_unexpected)
    try:
        raise SystemExit(main())
    except RenRefusal as refused:
        print(refused.render(), file=sys.stderr)
        emit_refusal_result(refused)
        sys.exit(REFUSAL_EXIT_CODE)
    except Exception as exc:  # noqa: BLE001 - unexpected errors are data too
        sys.exit(report_unexpected(exc))
