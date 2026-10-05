# ren-cli: the native front door

The first crate of the ONE Cargo workspace at `native/Cargo.toml` (shared
target dir `native/target`, gitignored, never committed). Boring Rust:
concrete structs and enums, limited generics, simple traits, explicit
ownership, no async trait hierarchies.

The `ren` binary replaces the bash `bin/ren` an engine tree ships. Two
kinds of work happen in it.

**The front door itself** resolves the engine root and an interpreter,
then `exec`s `python -m ren.cli` with the original argv - so stdout,
stderr, exit codes and signals pass through untouched, and `ren` is one
process end to end for the verbs that need Python. Interpreter discovery
asks the repo's one ladder (`library.tools.shared_environment`'s
`INTERPRETER_CANDIDATES`: `$PIPELINE_PYTHON`, the durable venv under
`vep_home`, the checkout's own `.venv`) natively - no Python helper
process, so finding the interpreter starts nothing. A missing interpreter
is refused with the ladder's own message, verbatim; there is no
stock-interpreter fallback.

**The file-reading verbs** are served natively, with no Python process
started: `--version`, `--help`, `info`, `status`, `check`. Each mirrors
the Python function it replaces, so its stdout, stderr and exit code are
identical - `tests/native_commands.rs` runs both and compares, so a later
Python change cannot silently drift. Where the Python reaches a part
this crate does not port (a format migration, the safe-zone layout
engine), the verb falls back to execing Python rather than drifting.

`scripts/build_ren_cli.sh` builds the binary into a built tree's
`bin/ren` (binaries are never committed). `ren/package_engine.py`
invokes it after copying `bin/`; without cargo the script exits 4 and
the bash door is kept, so packaging never fails for lack of Rust. The
Python entry point (`python -m ren.cli`) is the fallback module
invocation either way.
