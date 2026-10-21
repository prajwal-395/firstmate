# Cleanup lifecycle

Ren's shared artifact ownership and deletion contract lives in
`library/tools/artifact_lifecycle.py`. It records stable artifact identities,
the file identity observed at registration, lifecycle state, leases, and
guarded deletion. A file without a matching ownership record or exact identity
is protected.

What a file IS, and therefore what may happen to it, is decided in
`library/tools/artifact_policy.py`. That is the one reading of the layout's
areas: each of them has a retention class, a regenerability, and the command
that brings its files back. A cleanup may name a file only when its AREA says
the file is disposable, and where the area and the writer's own recorded class
disagree, the more protective of the two wins. A file inside the project that
sits in no named area is protected, because the project root holds
`project.yaml`, `pipeline_data.json`, and `pipeline_run.json`. A refusal on
those grounds is reported in the plan as `refused_policy`, not dropped.

Use `ren storage status [PROJECT] --json` to inspect registered artifacts,
storage by artifact kind, logical and physical bytes, cache budget use, and
free-space pressure. The inventory reads lifecycle event logs; it does not
discover candidates by recursively scanning storage directories. It reports
Ren-owned registered artifacts and a separately measured legacy summary.
Legacy entries include logical bytes and allocated bytes, counted once per
device/inode for hardlinks. The legacy summary does not make an unverified
entry eligible for collection.

The report answers PER PROJECT as well as for the machine. `by_project` gives
each project's own registered bytes, what of them may be reclaimed, the legacy
entries that project owns, and `analysis`: the wall, the CPU, the peak memory
and the bytes each capability wrote, read back from that project's own
performance ledger. A measurement that was never taken is reported as not
taken; a zero would read as "nothing was written", which is a different fact.

Use `ren storage legacy-inventory --manifest PATH` for a complete read-only
inventory of known legacy roots. It records each file, directory, or symlink,
physical allocation, and the evidence source that did or did not establish
ownership. Exact build-sweep marks and their applied sweep manifests establish
ownership for files already in quarantine. `--register-project-scratch
--project <SLUG>` adopts project scratch only when observed provenance names
the exact path, run, and producing step, the recorded byte count still matches,
the file has no hardlinks, and retained project records do not reference its
name. Everything else remains review-only. Qualification reports, performance
ledgers, real-footage copies, fixtures, review data, and recovery references
remain protected.

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

## What the reconciliation settles

`ren storage reconcile` compares the ownership records with the filesystem
and settles what it can, because a finding nothing can settle is a warning
on every start of the machine. Three things used to be reported forever:

- **A record whose file Ren had replaced.** A recovery archive is
  re-exported at the same path, and the record that named it then named a
  file that was no longer there. The record now follows the file: the
  re-export rebinds it, and a reconciliation rebinds a record whose file
  was replaced by anything else. The identity the record had is kept beside
  the new one, so the replacement is an audited fact. A file that is GONE
  is not a rebind - nothing can be said about an artifact whose content
  vanished, so it stays a finding.
- **A quarantine whose grace period had expired.** The quarantine area's
  policy gives a moved-aside file 24 hours to be restored; after that the
  artifact is released to `DISPOSABLE` and the next storage plan names it.
  The reconciliation never removes it: the reviewed plan is what removes
  it, and only after the captain read the manifest. A quarantine that
  something protects - a pinned artifact, a dependency, a `.drt`, a
  protected area - is never released and stays a finding.
- **A cleanup job the worker refused.** A refusal re-queued on every start
  is a loop, not a retry. The job is retried three times with fresh safety
  checks, and after that it is reported as needing a person.

Nothing is deleted by a reconciliation, and `automatic_deletion` in the
report says so.

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

## When the reclaim is queued

Two ACTS queue the lean reclaim, and both write a durable job to the project
event log before anything is planned or removed:

- a reel **sign-off** (`library/tools/reel_signoff.py`), and
- a successful reel **delivery** (`library/tools/reel_deliver.py` and
  `library/tools/deliver_all.py`).

The sign-off alone was not enough: the sign-off happens BEFORE the render, so
the delivery's own intermediates - the pre-master render in `scratch/`, the
variant work directories - were written after the sign-off's cleanup and
survived it. The delivery is the act that finishes the run's work, so it owns
the retention decision too, and a programmatic delivery obeys the same
per-user policy as a sign-off.

`REN_RETENTION` chooses the mode: `lean` queues the reclaim, `keep` keeps
everything. A reclaim that lacks a complete storage lifecycle qualification
record is refused and says so; the act that landed is never rolled back. The
worker starts once the render has returned, because the reclaim must not race
the render that is still reading its own scratch.

## What is bounded

Two stores grew without bound before this existed, and both are bounded now.

- **The qualification media store.** Every distinct source copied in is a
  whole video. A copy is freed when no retained or pinned run points at it;
  a pinned run is a promise that the reproduction stays available, so the
  pinned run and the media it points at are both retained.
- **The scratch project's recovery export.** The scratch owner keeps the
  newest five exports of one project and removes the older ones with their
  receipts. This is separate from the rule that a `.drt`, `.drp` or `.otio`
  is never purged in any class: that rule protects a project's RECOVERY
  archive from a project cleanup, and this is a scratch project that no
  longer exists. Only the exact shape the scratch owner writes is named.
