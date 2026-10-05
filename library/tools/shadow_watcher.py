"""Continuous edit detection: watch the shadow while a project is open.

The gap this closes (F-04): detection was point-in-time - `timeline_shadow.observe`
ran only when Ren next touched the reel, so a hand edit between builds was
invisible until then, and a later build could silently overwrite it.

This module schedules the observe at a slow cadence while a project is open,
and infers a soft "captain may be editing" flag from the `observed` generations
that appear. When the flag is fresh, a build refuses before placement rather
than risk writing beside a live hand edit.

The watcher is opt-in (`ren shadow-watch`), never started automatically. Its
snapshot jobs use the broker's lowest priority so they never delay the captain
or a build.

Read-only against Resolve: the watcher submits `timeline.snapshot` jobs to the
broker (which takes whatever lease the read needs) and never takes a lease
itself. It writes only the shadow store (via the job), the edit history, and
the soft flag.
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

#: How often the watcher observes every reel in the open project.
CADENCE_SECONDS = 30.0

#: How long the soft flag stays fresh. A build that starts within this
#: window of an observed hand edit refuses before placement; after it, the
#: flag is stale and reads as absent.
INFERENCE_TIMEOUT_SECONDS = 300.0

#: The broker's lowest priority, so a watcher job never delays the captain
#: or a build.
BROKER_PRIORITY = "export"

FLAG_NAME = "resolve_captain_inferred.flag"


def inferred_flag_path() -> Path:
    return lock_dir() / FLAG_NAME


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


class ShadowWatcher:
    """Observe every reel in the open project on a schedule.

    The watcher is a thin scheduling loop: it submits `timeline.snapshot`
    jobs to the broker and reads the results. It never takes a Resolve lease
    itself - the broker's job takes whatever the read needs (a shared
    RESOLVE_READ for the read, a brief cursor excursion to make the timeline
    current). The watcher writes only the shadow store (via the job), the
    edit history, and the soft flag.

    Opt-in: `start()` is never called automatically. `ren shadow-watch`
    starts the background thread; nothing else does.
    """

    def __init__(self, store=None, cadence_seconds: float = CADENCE_SECONDS,
                 clock: Callable[[], float] = time.time) -> None:
        self.store = store or timeline_shadow.ShadowStore()
        self.cadence_seconds = cadence_seconds
        self._clock = clock
        self._stopping = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def tick(self, project_name: str, project_folder: str = "") -> list:
        """Observe every reel in the project once; return new observed generations.

        A reel whose live timeline changed since the last recorded generation
        gets a `manual_edit` entry filed and the soft flag set. A reel whose
        digest is unchanged is a no-op.
        """
        from library.tools.resolved import client

        heads = self.store.heads(project_name)
        detected = []
        for head in heads:
            job = client.submit("timeline.snapshot", {
                "project": project_name,
                "timeline": head.timeline_name,
                "priority": BROKER_PRIORITY,
            }, owner="shadow_watcher")
            result = client.result(job["id"], wait=60.0) or {}
            returned_generation = int(result.get("generation", 0) or 0)
            if (returned_generation > head.generation
                    and result.get("source") == timeline_shadow.OBSERVED):
                generation = self.store.head(project_name, head.timeline_id)
                if generation is not None:
                    self._file_manual_edit(project_folder, head.timeline_name,
                                           generation)
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
              project_folder_factory: Callable[[], str] = lambda: "") -> None:
        """Start the background watcher thread.

        `project_name_factory` returns the open project's name, or None
        when no project is open. `project_folder_factory` returns the
        project's folder on disk (empty when unknown).
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
        while not self._stopping.is_set():
            project_name = project_name_factory()
            if project_name:
                try:
                    self.tick(project_name, project_folder_factory())
                except Exception:
                    pass
            self._stopping.wait(self.cadence_seconds)


def main(argv=None) -> int:
    """`ren shadow-watch <project_folder>`: start the watcher on a project.

    Opt-in: this runs only when the captain asks for it. It never starts
    automatically. The watcher observes the open project's reels at the
    configured cadence, using the broker's lowest priority so it never
    delays the captain or a build.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="ren shadow-watch",
        description="Watch a project's reels for hand edits on a schedule.")
    parser.add_argument("project_folder",
                        help="the project folder to watch")
    parser.add_argument("--cadence", type=float, default=CADENCE_SECONDS,
                        help=f"seconds between observations "
                             f"(default {CADENCE_SECONDS})")
    args = parser.parse_args(argv)

    from library.tools.resolved import jobs as broker_jobs

    def project_name():
        try:
            return broker_jobs.open_project_name(_resolve_handle())
        except Exception:
            return None

    watcher = ShadowWatcher(cadence_seconds=args.cadence)
    watcher.start(project_name, lambda: args.project_folder)
    print(f"shadow watcher started on {args.project_folder} - observing "
          f"every reel at {args.cadence}s cadence", file=sys.stderr)
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        watcher.stop()
    return 0


def _resolve_handle():
    from library.tools.marker_feedback import connect_resolve
    return connect_resolve()


if __name__ == "__main__":
    raise SystemExit(main())
