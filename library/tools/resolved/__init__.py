"""ren-resolved: the one process that schedules the one Resolve.

One Resolve per machine (`resolve_lock`), so Resolve is a scarce
execution engine and every agent is a client of it. The broker is a
Unix-socket daemon that owns the QUEUE in front of that engine:

* An ASYNC job API - `submit` returns an id at once, `status` and
  `result` read it back - so a worker that wants a read or a prepared
  write does not sit blocked on Resolve itself (`client.py`).
* ORDER, not FIFO: priority with aging, then project/timeline locality,
  then arrival (`scheduler.py`). Head-of-line within a band: a blocked
  head is skipped only by same-band jobs, so a stream of readers cannot
  starve a writer and aging means nothing starves.
* SINGLE-FLIGHT: an identical read already queued or running is joined,
  not repeated, and its receipt counts who joined (`jobs.coalesce_key`).
* A RECEIPT per job - who asked, what, how long it waited, how long it
  held Resolve, what it returned or why it was refused (`store.py`).
* QUALIFICATION is fenced: a job marked as a test may only target the
  dedicated `jobs.QUALIFICATION_PROJECT`, and the broker checks the OPEN
  project before it runs one (`jobs.refuse_qualification`).

Two shapes of job, because Resolve's handles cannot cross a process:

* EXECUTED - the broker runs it on its own Resolve connection:
  `timeline.snapshot`, `timeline.observe_current`, `timeline.apply_patch`,
  `timeline.semantic_edit`,
  `resolve_axi`,
  `resolve.ui_transaction`, `reel.promote_staged` (typed and restricted
  to the already-open `Ren Qualification` project), and
  `timeline.drt_export_probe` (typed, Qualification-only DRT export),
  `timeline.drt_recovery_probe` (exclusive disposable-project DRT import,
  verified and retired through the scratch-project lifecycle),
  and `reel.copy_tv_ending` (typed, exact-project reel ending copy with a
  target-specific freeze, verified Fusion splines and two-line logo).
* GRANT (`lease`) - an in-process critical section the CALLER runs.
  `resolve_lock.resolve_lease` asks the broker for its turn whenever a
  broker is serving, so every existing lease call site is scheduled
  here without being rewritten. The flock stays underneath as the
  fence against writers that never asked (an older checkout, a human).
* SESSION (`library.tools.resolve_session`) - ONE exclusive grant held
  across a whole multi-call operation (a reels build, a profile run, a
  delivery), so no other lane's turn is granted between two of that
  operation's calls. The operation's own leases are served by that one
  claim instead of asking for another.

The broker never switches PROJECTS: an executed job names the open one
or is refused. It moves the timeline cursor only inside its own
exclusive section, through `resolve_lock.cursor_fence`.

    ren resolved serve            # foreground daemon
    ren resolved ensure           # start it detached if none serves
    ren resolved status|list|stop
    ren resolved kpi [--hours N | --since EPOCH] [--until EPOCH]
                                       # what Resolve cost (`kpi.py`)
    ren resolved submit <kind> '<json params>' [--wait SECONDS]
    ren resolved result <id> [--wait SECONDS]

The broker is a STANDALONE service, not a child of any worker:
`client.ensure` starts it detached (its own session, stdio to a log
beside the socket) so it outlives the worker that started it, and
every client that needs the broker ensures it first - the lease path
(`resolve_lock._broker_turn`), `resolve-axi`, and `client.call`. A
starter lock serializes concurrent starters, so one broker serves a
socket; `ren resolved stop` is the clean stop. A broker that dies is
restarted by the next client that calls.

The broker's identity is the MACHINE's, not a checkout's: its socket,
database, log, starter lock, bind lock and cancellation markers live
in ONE directory per machine, beside the machine-wide instance lease
(`resolved/broker_paths`). Every checkout, worktree and lane on this
machine therefore names the SAME broker - `ensure` finds the broker
that is serving and uses it, `serve` refuses to start a second one,
and `status` reports the one that serves. Measured 2026-10-20: the
broker's socket followed a lane's private lock dir, so three brokers
served at once against the one Resolve (four writers, and the
project-switch and timeline-count races that came with them) while
`ren resolved status` from the primary checkout reported "no broker is
serving". A deliberately private broker still exists - one that NAMES
its own socket path (the qualification harness) - and keeps its own
queue, state and markers.
`tests/unit/resolve/test_resolve_lock.py`,
`tests/unit/resolve/test_ensure.py`,
`tests/unit/resolve/test_single_broker.py`.
"""
