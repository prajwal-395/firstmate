# ren-cli: the native front door

The first crate of the ONE Cargo workspace at `native/Cargo.toml` (shared
target dir `native/target`, gitignored, never committed). Boring Rust:
concrete structs and enums, limited generics, simple traits, explicit
ownership, no async trait hierarchies.

The `ren` binary replaces the bash `bin/ren` an engine tree ships. It
resolves the engine root and an interpreter, then `exec`s
`python -m ren.cli` with the original argv - so stdout, stderr, exit
codes and signals pass through untouched, and `ren` is one process end
to end. Command logic stays in Python: the crate ports no verb.

Interpreter discovery asks the repo's one ladder
(`python3 -m library.tools.shared_environment --resolve-interpreter
--repo-root <engine>`), never a copied rung - the ladder is Python data
with two callers that cannot import it, and a second copy would drift.
A missing or broken interpreter is refused with the ladder's own
message, verbatim, and exit 3; there is no stock-interpreter fallback.

`scripts/build_ren_cli.sh` builds the binary into a built tree's
`bin/ren` (binaries are never committed). `ren/package_engine.py`
invokes it after copying `bin/`; without cargo the script exits 4 and
the bash door is kept, so packaging never fails for lack of Rust. The
Python entry point (`python -m ren.cli`) is the fallback module
invocation either way.
