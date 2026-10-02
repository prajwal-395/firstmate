"""ren-resolved: the one process that schedules the one Resolve.

One Resolve per machine (`resolve_lock`), so Resolve is a scarce
execution engine and every agent is a client of it. The broker is a
Unix-socket daemon that owns the QUEUE in front of that engine:

* An ASYNC job API - `submit` returns an id at once, `status` and
  `result` read it back - so a worker that wants a read or a prepared
  write does not sit blocked on Resolve itself (`client.py`).
* ORDER, not FIFO: priority with aging, then project/timeline locality,
  then arrival (`scheduler.py`). Strict head-of-line, so a stream of
  readers cannot starve a writer and aging means nothing starves.
* SINGLE-FLIGHT: an identical read already queued or running is joined,
  not repeated, and its receipt counts who joined (`jobs.coalesce_key`).
* A RECEIPT per job - who asked, what, how long it waited, how long it
  held Resolve, what it returned or why it was refused (`store.py`).
* QUALIFICATION is fenced: a job marked as a test may only target the
  dedicated `jobs.QUALIFICATION_PROJECT`, and the broker checks the OPEN
  project before it runs one (`jobs.refuse_qualification`).

Two shapes of job, because Resolve's handles cannot cross a process:

* EXECUTED - the broker runs it on its own Resolve connection:
  `timeline.snapshot`, `timeline.apply_patch`, `resolve_axi`.
* GRANT (`lease`) - an in-process critical section the CALLER runs.
  `resolve_lock.resolve_lease` asks the broker for its turn whenever a
  broker is serving, so every existing lease call site is scheduled
  here without being rewritten. The flock stays underneath as the
  fence against writers that never asked (an older checkout, a human).

The broker never switches PROJECTS: an executed job names the open one
or is refused. It moves the timeline cursor only inside its own
exclusive section, through `resolve_lock.cursor_fence`.

    ren resolved serve            # foreground daemon
    ren resolved status|list|stop
    ren resolved submit <kind> '<json params>' [--wait SECONDS]
    ren resolved result <id> [--wait SECONDS]

Socket and database live in `resolve_lock.lock_dir()`, next to the
lease, so `PIPELINE_RESOLVE_LOCK_DIR` isolates a test's broker exactly
as it isolates a test's lease. `tests/test_resolved.py`.
"""
