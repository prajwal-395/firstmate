//! Write the generated JSON Schemas to `schemas/`, or check the
//! committed ones are current.
//!
//!     cargo run --bin emit-schemas           # regenerate schemas/
//!     cargo run --bin emit-schemas -- --check # fail when stale
//!     cargo run --bin emit-schemas -- --check --schemas-dir /path/to/schemas
//!
//! The schemas directory is the crate's own `schemas/`, so the tool
//! works from any checkout without arguments.

use std::path::PathBuf;
use std::process::ExitCode;

use ren_protocol::schemas;

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let check = args.iter().any(|arg| arg == "--check");
    let dir = match args.iter().position(|arg| arg == "--schemas-dir") {
        Some(index) => match args.get(index + 1) {
            Some(path) => PathBuf::from(path),
            None => {
                eprintln!("--schemas-dir requires a path");
                return ExitCode::FAILURE;
            }
        },
        None => PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("schemas"),
    };

    if check {
        match schemas::check_schemas(&dir) {
            Ok(()) => {
                println!("schemas are current: {}", dir.display());
                return ExitCode::SUCCESS;
            }
            Err(problems) => {
                eprintln!("stale schemas:\n{problems}");
                return ExitCode::FAILURE;
            }
        }
    }

    match schemas::write_schemas(&dir) {
        Ok(written) => {
            println!("wrote {written} schemas to {}", dir.display());
            ExitCode::SUCCESS
        }
        Err(problem) => {
            eprintln!("{problem}");
            ExitCode::FAILURE
        }
    }
}
