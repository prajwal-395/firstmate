# the phase that was the machine's uptime

**Lane:** INFRA-006, lane E (the perf ledger's phantom phase).
**Measured:** 2026-10-21, macOS (Apple M5), the shipped `geo-podcast`
project's own performance ledger, read only.

## What happened

The performance ledger records what a run spent its time on, and the reel
build's own phases are among them. `otio_placement.fusion_comps_s` - the
Fusion comp pass, the graphics and animation work INFRA-006 is about -
reads **131,422 to 167,228 seconds per placement** in the shipped
`geo-podcast` ledger: **734.4 hours across 17 rows**, for a phase that
took under a minute.

The number was the **MACHINE'S UPTIME**. The producer passed an elapsed
*AVERAGE* where a *START* belonged, and the recorder subtracted it from
`time.perf_counter()`, which on macOS **is** the uptime.

Both cost readers believed it:

- `library/tools/perf_ledger.py` (`STAGE_LAYERS`) charged the layer to the
  `axi` stage;
- `library/tools/critical_path.py` (`BUCKET_BY_LAYER`) charged it to
  `RESOLVE`.

So every "Resolve or the native renderer?" comparison read 36 hours of
phantom Resolve time, and the run's own wall window was stretched by the
same 46 hours.

The producer's own call site was corrected upstream in `9270adaf0`
(2026-10-18), *after* those 17 rows were written. Nothing checked the
number, so the stored ledger kept the lie and every reader of it
inherited it.

## The change

A duration is checked against the wall of the run that recorded it, and a
phase that fails the check is a **typed anomaly**, not a number: it keeps
the number it claimed (`claimed_wall_s`) and is charged to **no stage, no
engine and no resource**. Two bounds, one rule:

- **at write time** the run's wall so far is `now - the run's start`, and
  the run's start is in the run's own id (`run_id.started_at`), so every
  process that knows the id knows the run's age without reading a file;
- **at read time** the run's wall is what the run *recorded* - the sum of
  its capability walls - which is the only bound a stored row can be
  checked against.

And a **START is not a DURATION**: they are different types now
(`reel_otio_placement.PhaseStart`), so `record_duration` refuses anything
that is not a start, and the wrong shape cannot reach the recorder again.

One enumeration: `library/tools/ledger_anomaly.py` (`ANOMALY`,
`PHASE_EXCEEDS_RUN_WALL`, `run_wall_s`, `claim_exceeds_run_wall`,
`anomaly_row`, `clamp_rows`, `summarise`). The readers are
`perf_ledger.read_rows`, `perf_ledger.profile` and
`critical_path.analyse_run`; the write path is `perf_ledger._clamped`.
The measurement is `scripts/measure_phantom_phases.py`.

The ledger FILE is untouched: it stays append-only, and what a *reader*
gets is the clamped view. Nothing is lost - the claimed number is kept on
the anomaly row.

## Measured, before and after, with the denominators

The shipped `geo-podcast` ledger, read only. BEFORE is the code at
`3c04310c6`, AFTER is this change, on the **same rows**: 6,021 span rows
across 285 runs, of which 17 runs carried a phantom phase.

| | before | after |
|---|---:|---:|
| phantom phases | 17 | 17 (reported as anomalies) |
| span rows in the ledger | 6,021 | 6,021 |
| runs in the ledger | 285 | 285 |
| runs whose measured cost changed | - | 17 of 285 |
| seconds charged to RESOLVE, whole ledger | 2,663,921.2 s | **21,359.6 s** |
| seconds charged to `otio_placement.fusion_comps_s` | 2,642,561.7 s | **0.0 s** |
| seconds charged to graphics / disk / model / queue / own code | - | **unchanged** |

One run, read through the product's own report (`ren profile --run
run-20261018T074200Z-020e0b`):

- before: `28695% otio_placement.fusion_comps_s`; the `axi` stage read
  **167,229.3 s (28,694.8%)**;
- after: the phase is not charged at all; the `axi` stage reads
  **26.1 s (4.5%)**, and the report says why:

```
Anomalies: 1 phase(s) claimed 167228.1s - more than the run that recorded
them had. They are charged to nothing: otio_placement.fusion_comps_s
```

Reproduce it:

```sh
python3 scripts/measure_phantom_phases.py \
  "$HOME/Documents/content_stuff/video_projects/lucie/geo-podcast/pipeline_output/logs/perf_ledger.jsonl"
```

## Tests

`tests/unit/tools/test_ledger_anomaly.py`, defect-naming throughout:

- a phase longer than the run is charged to nothing (profile and critical
  path), and the run's window is not stretched by it;
- a phase that FITS inside the run is still charged, in both readers;
- the stored ledger is clamped on read, and the anomaly row carries no
  `wall_s` and no `started_at` (so no reader can charge it by accident);
- the write path stores an anomaly instead of a duration;
- a run with no recorded wall bounds nothing (unknown is never zero);
- a phase start cannot be a duration (`record_duration` refuses a float).

## Left undone

- The 17 historical rows stay in the file as they were written; they are
  clamped on read, not rewritten (the ledger is append-only).
- Lanes A-D and F-H of the INFRA-006 scout report are other lanes' work.
