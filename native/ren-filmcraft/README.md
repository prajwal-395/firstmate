# ren-filmcraft

Ren's headless FilmCraft adapter. It invokes only `filmcraft-cli`, never the desktop UI or its
crate graph. The pin currently lives in `src/lib.rs` as one immutable SHA; replace that source with
the intake lane's manifest constant when it lands. The CLI is located from `REN_FILMCRAFT_CLI` or
`FILMCRAFT_CLI`; otherwise it is installed on demand into a revision-addressed directory under
`~/.cache/ren/filmcraft/` using the exact Git revision and Cargo's lockfile.

The public API is a versioned typed request and receipt. `.fcproj` remains an engine projection,
and the adapter checks its explicit format identifier and schema version before accepting it.
Unknown adapter versions, project schemas, malformed command output and unrecognized command
response shapes are errors. The request vocabulary stays bounded to project create / import,
placement, razor, trim, move / insert, transition, audio gain, H.264 / ProRes / WAV export, still
render and inspection.

Run crate checks from the `native/` workspace. The fixture integration test uses the committed
short A/V clip and a real CLI built from the pinned SHA; it checks the known unlinked-audio razor
and trim-delta defects and requires an explicit loss report for either.
