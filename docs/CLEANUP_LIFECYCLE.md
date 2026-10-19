# Cleanup lifecycle

Ren's shared artifact ownership and deletion contract lives in
`library/tools/artifact_lifecycle.py`. It records stable artifact identities,
the file identity observed at registration, lifecycle state, leases, and
guarded deletion. A file without a matching ownership record or exact identity
is protected.

Use `ren storage status [PROJECT] --json` to inspect registered artifacts,
storage by artifact kind, logical and physical bytes, cache budget use, and
free-space pressure. The inventory reads lifecycle event logs; it does not
discover candidates by recursively scanning storage directories. It reports
Ren-owned registered artifacts only. User assets and unknown or legacy files
are outside the inventory and remain protected from these operations.

Use `ren storage plan [PROJECT] --manifest PATH` to write a manifest for
artifacts that are already marked disposable and remain identity verified.
Review that manifest, then use `ren storage collect PATH` to place its entries
in the lifecycle quarantine state; the files stay at their recorded paths.
`ren storage purge PATH` permanently
removes those exact entries through the ownership contract. `ren storage
restore PATH` returns still-present quarantined entries to disposable state.
Operations that cannot prove ownership, reachability, identity, or allowed
state refuse with a reason. Plan before applying any change.

The minimum free-space reserve is `REN_MIN_FREE_BYTES` (default 10 GiB).
Pressure is critical below the reserve, elevated below 1.5 times the reserve,
and normal otherwise. `storage_lifecycle.require_free_space()` is the cheap
preflight for storage-heavy work; it refuses when the requested work would
consume the reserve and never removes protected assets to make room.

This interface preserves the existing `ren gc`, `ren purge`, and
`ren workspace-gc` commands and their manifests. Project retention and
machine-wide caches remain subject to their owning lifecycle implementations.
The storage report aggregates cache artifacts recorded by projects against
`REN_CACHE_BUDGET_BYTES`; cache eviction remains value-aware through
`library/tools/cache_lifecycle.py`.

The MCP catalog exposes read-only `storage-status` and `storage-plan` tools
and typed `storage-collect`, `storage-purge`, and `storage-restore` tools.
Ren's current MCP server refuses write tools before execution; use the CLI for
the three mutating operations. Storage-heavy callers can use
`require_free_space()` before starting work; this lane does not estimate a
job's working set on its behalf.
