# Recovery archive measurements

Measured 2026-10-19 from the real `geo-podcast` project in read-only mode.
The project already had uncommitted and untracked changes; no project files,
Git index entries, or Resolve timelines were changed by this inspection.

## Observed baseline

| Measure | Result | Method |
| --- | ---: | --- |
| Working-tree `.drt` files | 219 files, 29,460,133 bytes (28.1 MiB) | Enumerated only `pipeline_output/review/recovery_archives/*.drt` |
| Working archive size | 45,667 to 234,274 bytes; mean 134,521 bytes | File sizes |
| Exact duplicates in working archives | 0 of 219 | SHA-256 over every archive |
| Archives tracked at `HEAD` | 215 files, 29,243,455 bytes (27.9 MiB) | `git ls-tree -r -l HEAD` |
| Historical unique archive blobs | 223, 30,378,207 uncompressed bytes; 30,366,732 bytes on disk | `git rev-list --objects --all`, `git cat-file -s`, `git verify-pack`, and loose object file sizes |
| Current whole-repository packed objects | 233,113 KiB | `git count-objects -v`; not attributable to archives alone |

The working tree has four more `.drt` files than `HEAD`, consistent with its
existing dirty state. Across all historical archive blobs, physical Git
storage is about 30.37 MB. The checked out `HEAD` archive payload is 29.2 MB,
so that is the archive portion of a checkout's file materialization; elapsed
checkout time was not measured.

Resolve export time cannot be measured read-only from these files. It requires
calling `Timeline.Export` against a live Resolve timeline, which this task's
no-testing rule forbids. The restore-equivalence check into a disposable Resolve
project is likewise not run. Neither number is inferred from file timestamps.

## Recommendation

Keep the current named `.drt` recovery mechanism. Git already stores identical
blobs once, while this project's inspected revisions had no exact duplicate
archive bytes to deduplicate. Git LFS would move payload transfer behind LFS
and would still materialize the same archive bytes in an ordinary checkout;
without measured checkout latency or duplicate bytes, it has no demonstrated
benefit here. The opt-in project-local SHA-256 object store added with VC-33
provides portable immutable object names and verifies content on publication,
but it duplicates current files when enabled. Leave it disabled until broader
measurements show enough cross-project or cross-revision duplication to offset
that cost and a disposable Resolve restore proves equivalent recovery.

## Identity and integrity

New archive export results carry `content_id` (`sha256:<digest>`), `sha256`,
and byte length. The version ledger rejects changing an already attached
archive receipt and validates any supplied content identity against its file.
Callers may explicitly publish a verified archive under
`recovery_archives/objects/sha256/<digest>.drt`; existing named archives remain
the default recovery path. No `.drt` mechanism was removed or replaced.
