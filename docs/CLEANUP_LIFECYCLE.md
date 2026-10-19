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
Review that manifest, then use `ren storage collect PATH` to move its entries
into `pipeline_output/quarantine/lifecycle/`; exact identities move with them
and original paths remain in the lifecycle event log.
`ren storage purge PATH` permanently
removes those exact entries through the ownership contract. `ren storage
restore PATH` returns still-present quarantined entries to disposable state.
Operations that cannot prove ownership, reachability, identity, or allowed
state refuse with a reason. Plan before applying any change.

The minimum free-space reserve is `REN_MIN_FREE_BYTES` (default 10 GiB).
Pressure is critical below the reserve, elevated below 1.5 times the reserve,
and normal otherwise. `storage_lifecycle.require_free_space()` is the
preflight for storage-heavy work. Render builds estimate output and staging
space from approved reel durations and delivery dimensions; setup uses its
measured model/pack sizes. Preflight refuses when the requested work would
consume the reserve and never removes protected assets to make room.

This interface preserves the existing `ren gc`, `ren purge`, and
`ren workspace-gc` commands and their manifests. Project retention and
machine-wide caches remain subject to their owning lifecycle implementations.
The storage report aggregates cache artifacts recorded by projects against
`REN_CACHE_BUDGET_BYTES`; planned cache evictions distribute that machine-wide
budget across registered projects, reduce it under elevated or critical disk
pressure, and remain value-aware through `library/tools/cache_lifecycle.py`.
They are included in the reviewed storage manifest and applied through the
same exact-identity lifecycle guards.

The MCP catalog exposes read-only `storage-status` and typed `storage-plan`,
`storage-collect`, `storage-purge`, and `storage-restore` operations. Mutating
operations call the same guarded lifecycle functions as the CLI and return
structured refusals when ownership, manifest, or filesystem checks fail.
