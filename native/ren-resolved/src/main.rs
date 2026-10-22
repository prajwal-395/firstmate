//! `ren-resolved`: the Resolve broker daemon in Rust.
//!
//! Speaks the EXISTING Unix-socket JSON protocol of
//! `library/tools/resolved/server.py` byte-compatibly, so
//! `client.call(...)` does not care whether the daemon on the other side
//! is Python or Rust. The Rust daemon is the default; the Python daemon is
//! selectable by `REN_RESOLVED_DAEMON=python` for one migration cycle.
//!
//! It owns the queue, priorities and scheduling, exclusive/shared resource
//! admission, idempotency and coalescing, leases, deadlines, the watchdog,
//! SQLite persistence (the same schema and file) and process lifecycle.
//! Execution against Resolve stays in Python: each job is handed to a
//! Python worker process over a typed stdio protocol, and the worker does
//! only receive op, execute through the existing Python code, return the
//! observation.
//!
//! Boring Rust over clever Rust: concrete structs and enums, no async
//! trait hierarchies, explicit ownership.

use std::path::PathBuf;
use std::sync::Arc;

mod broker;
mod protocol;
mod scheduler;
mod server;
mod store;
mod watchdog;
mod worker;

use server::Daemon;

fn broker_now() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let verb = argv.first().map(String::as_str).unwrap_or("serve");
    if verb != "serve" {
        eprintln!("ren-resolved: unknown verb {verb:?} (only 'serve' is served)");
        std::process::exit(2);
    }
    let socket_path = argv
        .get(1)
        .map(PathBuf::from)
        .unwrap_or_else(server::socket_path);
    // The broker's directory is the directory its socket lives in, and
    // the processes it starts are told where that is: a worker writes
    // its cancellation markers beside the broker that queued the job.
    server::set_socket_path(&socket_path);
    std::env::set_var(
        server::BROKER_DIR_TOLD_ENV,
        server::broker_dir().to_string_lossy().to_string(),
    );
    let db_path = socket_path
        .parent()
        .map(|dir| dir.join(server::DB_NAME))
        .unwrap_or_else(server::db_path);

    // Refuse to be the machine's broker from a worker worktree: it
    // would serve production Resolve calls on unmerged code, from a
    // directory a worktree tool may reclaim.
    if server::pointed_at_a_worker_worktree(&socket_path) {
        eprintln!(
            "ren-resolved: refusing to serve the machine broker on {} from a \
             linked worktree - it would serve production Resolve calls on \
             unmerged code; run it from the installed engine checkout",
            socket_path.display()
        );
        std::process::exit(1);
    }

    // Refuse a socket that already answers - one broker serves a socket.
    // The authoritative check happens under the bind lock, inside
    // `server::serve`; this one fails fast, before the store is opened.
    match server::probe(&socket_path) {
        server::Probe::Answer(answer) => {
            eprintln!(
                "ren-resolved: is already serving on {} (it answered: {})",
                socket_path.display(),
                answer.trim()
            );
            std::process::exit(1);
        }
        server::Probe::Silent => {
            eprintln!(
                "ren-resolved: {} is held by a broker that did not answer - \
                 refusing to take its socket",
                socket_path.display()
            );
            std::process::exit(1);
        }
        server::Probe::Absent => {}
    }

    // ONE BROKER SERVES THE MACHINE. A broker on the socket a plain
    // client reaches claims the machine (a machine-wide lock), and a
    // second one refuses while the first holds it: two brokers against
    // the one Resolve move the cursor under each other and block
    // project switches for every lane (measured 2026-10-21: the PROD-001
    // clean-install check started a second broker under /tmp and left it
    // serving for seven hours, with two workers). A PRIVATE broker -
    // one on a socket of its own - is left alone.
    let machine_claim = match server::claim_the_machine_broker(&socket_path) {
        Ok(claim) => claim,
        Err(message) => {
            eprintln!("{message}");
            std::process::exit(1);
        }
    };

    let store = match store::JobStore::open(&db_path) {
        Ok(store) => Arc::new(store),
        Err(exc) => {
            eprintln!("ren-resolved: cannot open {}: {exc}", db_path.display());
            std::process::exit(1);
        }
    };
    let daemon = Arc::new(Daemon::new(store));

    // Lifecycle lines on stderr - the broker's stdio is its log file,
    // so the next "already stopped, no known cause" is answerable:
    // every start and every stop lands here with when and which tree.
    let engine = std::env::current_dir()
        .map(|dir| dir.display().to_string())
        .unwrap_or_else(|_| String::from("unknown"));
    eprintln!(
        "{} ren-resolved: serving on {} (pid {}, engine {})",
        broker_now(),
        socket_path.display(),
        std::process::id(),
        engine,
    );

    // The blocking accept loop is woken by the shutdown handler after it
    // replies to the client.
    let daemon_for_serve = Arc::clone(&daemon);
    let path = socket_path.clone();
    let serve_thread = std::thread::spawn(move || server::serve(&path, daemon_for_serve));

    // Wait for the serve thread to finish (on shutdown, on a refusal, or
    // on an error). A refusal - somebody else already serves this
    // socket - is a failure to serve, and says so.
    match serve_thread.join() {
        Ok(Ok(())) => {}
        Ok(Err(error)) => {
            eprintln!("{error}");
            std::process::exit(1);
        }
        Err(_) => {
            eprintln!("ren-resolved: the serve thread panicked");
            std::process::exit(1);
        }
    }
    eprintln!(
        "{} ren-resolved: stopping (pid {})",
        broker_now(),
        std::process::id()
    );
    drop(machine_claim);
    daemon.watchdog.stop();
    daemon.broker.stop();
    daemon.stop();
    let _ = std::fs::remove_file(&socket_path);
}
