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

fn main() {
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let verb = argv.first().map(String::as_str).unwrap_or("serve");
    if verb != "serve" {
        eprintln!("ren-resolved: unknown verb {verb:?} (only 'serve' is served)");
        std::process::exit(2);
    }
    let socket_path = argv.get(1).map(PathBuf::from).unwrap_or_else(server::socket_path);
    let db_path = socket_path
        .parent()
        .map(|dir| dir.join(server::DB_NAME))
        .unwrap_or_else(server::db_path);

    // Refuse a socket that already answers - one broker serves a socket.
    if let Ok(mut probe) = std::os::unix::net::UnixStream::connect(&socket_path) {
        use std::io::Write;
        let _ = probe.write_all(b"{\"op\": \"ping\"}\n");
        let _ = probe.flush();
        use std::io::Read;
        let mut answer = String::new();
        if probe.read_to_string(&mut answer).is_ok() && !answer.is_empty() {
            eprintln!(
                "ren-resolved: is already serving on {}",
                socket_path.display()
            );
            std::process::exit(1);
        }
    }

    let store = match store::JobStore::open(&db_path) {
        Ok(store) => Arc::new(store),
        Err(exc) => {
            eprintln!("ren-resolved: cannot open {}: {exc}", db_path.display());
            std::process::exit(1);
        }
    };
    let daemon = Arc::new(Daemon::new(store));

    // The blocking accept loop is woken by the shutdown handler after it
    // replies to the client.
    let daemon_for_serve = Arc::clone(&daemon);
    let path = socket_path.clone();
    let serve_thread = std::thread::spawn(move || {
        let _ = server::serve(&path, daemon_for_serve);
    });

    // Wait for the serve thread to finish (on shutdown or an error).
    let _ = serve_thread.join();
    daemon.watchdog.stop();
    daemon.broker.stop();
    daemon.stop();
    let _ = std::fs::remove_file(&socket_path);
}
