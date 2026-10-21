# ren-resolved

The Resolve broker daemon in Rust: the queue, scheduler, watchdog and job
store behind the one Resolve instance.

Speaks the EXISTING Unix-socket JSON protocol of
`library/tools/resolved/server.py` byte-compatibly, so
`client.call(...)` does not care whether the daemon on the other side is
Python or Rust. The Rust daemon is the default; set
`REN_RESOLVED_DAEMON=python` to select the Python fallback for one migration
cycle.

## What it owns

- The job queue, priorities and scheduling (`scheduler.rs` - a port of
  `library/tools/resolved/scheduler.py`).
- Exclusive/shared resource admission, idempotency and coalescing
  (`broker.rs` - a port of `server.py`'s `Broker`).
- The watchdog (`watchdog.rs` - a port of `resolve_watchdog.py`).
- The continuous timeline shadow watcher, hosted by a Python process that
  submits observations through the broker socket
  (`library/tools/resolved/shadow_watcher_host.py`).
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

## A refusal says what kind of block it was

A refused submission is answered `{"error", "refused", "blocker",
"next_action"}` - the same four keys the Python broker answers with
(`server.py` catches `jobs.JobRefused`, whose kind of block and next action
come from `library/tools/refusal_taxonomy.py`). The Python worker names the
pair for every refusal it raises and the daemon carries it to the client
unchanged, so a caller reads one refusal shape whichever daemon served it.
A REJECTED job names no kind of block, and the reply says so by leaving the
keys out, because the Python broker answers that one with the error alone.

## The lease

The worker holds the instance lease for the whole job, connection
included - the daemon is a Rust process and the flock lives in Python.
The watchdog probe takes a shared lease (it only reads), with
`honor_captain=False`; a lease another writer holds is skipped rather
than marked. Both are the 2026-10-05 regressions' fixes, preserved.

## Building

    cargo build --release          # the binary at native/target/release/ren-resolved

The client's `_start_detached` finds it there (release first, then
debug). `scripts/build_ren_resolved.sh` builds it, and `native_refresh`
keeps it current when a broker needs to start.

## Tests

The daemon is tested through the unchanged client:
`REN_RESOLVED_DAEMON=rust pytest tests/unit/resolve/` runs the Python
broker suite against it. `tests/tooling/protocol_parity.py` compares
every request type's reply between the two daemons;
`tests/tooling/crash_recovery.py` kills the daemon mid-job and checks
the store recovers; `tests/unit/resolve/test_rust_daemon_lease.py` pins
the two lease regressions.
