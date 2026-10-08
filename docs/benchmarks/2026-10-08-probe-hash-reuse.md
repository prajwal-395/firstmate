# Probe and hash reuse measurement

Date: 2026-10-08. Scope: successful ffprobe JSON metadata queries and exact
whole-file SHA-256 measurements, reused by canonical path, device, inode, byte
size, mtime and ctime. Cache entries live under the user's XDG cache (or
`~/.cache/ren`), outside project data.

## Existing run evidence

The available audit and captured run records do not contain per-file ffprobe
or full-file hash invocation records. The captured `pipeline_run.json` is
partial and the accessible fixture run has no `perf_ledger.jsonl`; therefore
real-project duplicate counts cannot be reconstructed. The audit measured a
single ffprobe at 79.90 ms and a whole-file hash of 446.8 MB at 237.89 ms,
but did not establish how often either repeated in a real run.

## Same-workload measurements

The local fixture workload invokes the same full catalog probe and full-file
hash twice against the same media identity. Before runs used direct ffprobe
and streaming SHA-256 twice. After runs used the cache-backed catalog probe
and SHA-256 twice, with an empty cache at start. Times are wall-clock on this
machine; ffprobe output was parsed through the existing catalog path.

| Input | Measure | Before | After |
|---|---|---:|---:|
| 12,604-byte generated MP4, 2 repeats | ffprobe calls / wall | 2 / 32.81 ms | 1 / 18.34 ms |
| same MP4, 2 repeats | full hashes / wall | 2 / 0.19 ms | 1 / 0.84 ms |
| 446,800,000-byte sparse file, 2 repeats | full hashes / wall | 2 / 386.3 ms | 1 / 174.5 ms |

The tiny MP4 hash is faster without a disk cache lookup; the cache targets
large inputs, where a second full read dominates lookup cost. The sparse file
is the same byte length as the audit's measured clip, but is not a media
fixture and its timings are not a replacement for that measurement.

## Correctness and limits

Successful probe results are cached per exact option list. Failed probes are
not cached. File identity includes path, device, inode, size, mtime_ns and
ctime_ns; an identity change causes a fresh probe or hash. Including ctime
also catches ordinary in-place edits when a caller restores mtime. No metadata
cache can detect an adversarial writer that changes bytes while preserving
every filesystem identity field. The cache is advisory and does not change
project artifacts or pipeline output schemas.
