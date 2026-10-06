# ren-resolved

The Resolve broker daemon in Rust: the queue, scheduler, watchdog and job
store behind the one Resolve instance.

Speaks the EXISTING Unix-socket JSON protocol of
`library/tools/resolved/server.py` byte-compatibly, so
`client.call(...)` does not care whether the daemon on the other side is
Python or Rust - a strangler migration. The Python daemon stays the
default; this daemon is selectable by `REN_RESOLVED_DAEMON=rust` (read by
`client._start_detached`, the one place that starts a daemon).

## What it owns

- The job queue, priorities and scheduling (`scheduler.rs` - a port of
  `library/tools/resolved/scheduler.py`).
- Exclusive/shared resource admission, idempotency and coalescing
  (`broker.rs` - a port of `server.py`'s `Broker`).
- The watchdog (`watchdog.rs` - a port of `resolve_watchdog.py`).
- SQLite persistence - the same schema and file as the Python daemon, so
  a client that survives a daemon switch sees one continuous job history
  (`store.rs`).
- Process lifecycle: serve on the socket, shut down on `shutdown`.

## What stays in Python

Execution against Resolve. Each job is handed to a Python worker process
(`library/tools/resolved/worker.py`) over a typed stdio protocol. The
worker takes the instance lease, connects to Resolve through the
existing Python code, runs the job, and serializes the observation back.
The daemon keeps two workers: a PREPARE worker (the submission gate,
`jobs.prepare` - fast, no Resolve) and an EXECUTION worker (jobs, probes,
health - takes the lease, keeps the Resolve connection).

The worker marks itself the broker's executor (`client.mark_executor()`),
as the Python broker's execute loop does: `resolve_axi.main` asks
`client.serving()` whether a broker is serving, and a worker that does
not mark itself would route every `resolve_axi` job back through the
broker - a loop.

## The lease

The worker holds the instance lease for the whole job, connection
included - the daemon is a Rust process and the flock lives in Python.
The watchdog probe takes a shared lease (it only reads), with
`honor_captain=False`; a lease another writer holds is skipped rather
than marked. Both are the 2026-10-05 regressions' fixes, preserved.

## Building

    cargo build --release          # the binary at native/target/release/ren-resolved

The client's `_start_detached` finds it there (release first, then
debug) when `REN_RESOLVED_DAEMON=rust` is set.

## Tests

The daemon is tested through the unchanged client:
`REN_RESOLVED_DAEMON=rust pytest tests/unit/resolve/` runs the Python
broker suite against it. `tests/tooling/protocol_parity.py` compares
every request type's reply between the two daemons;
`tests/tooling/crash_recovery.py` kills the daemon mid-job and checks
the store recovers; `tests/unit/resolve/test_rust_daemon_lease.py` pins
the two lease regressions.
