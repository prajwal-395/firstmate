# Memory admission, measured 2026-10-15

> Superseded for SAM 2 on 2026-10-22: the reservation this doc produced
> (8 GB) is below the peak measured on the 24 GiB box (10.15 GB), and is
> now 11 GB. See
> `docs/benchmarks/2026-10-22-prod006-resource-constrained.md`.

Which scheduler job kinds actually push the captain's Mac to the edge
beside Resolve, and the reservations that follow. This doc is the
measurement basis for `MEASURED_MEMORY_RESERVATIONS_GB` in
`library/tools/resource_scheduler.py`: only the kinds below marked
**reserve** carry a `ram_gb` demand and wait on memory; every other
kind admits regardless of free memory (PR 2597's gate).

Machine: captain's Mac, 24 GiB, with Resolve live (tree footprint
5.28 GB at measure time) and other lanes running. Available memory at
measure time ~6 GB with 6.9 GB in the compressor: these are
loaded-machine numbers, which is the point - admission is judged
against the edge, not an idle box. No model downloads: every fresh
run used cached weights offline (`HF_HUB_OFFLINE=1`).

## Method

Peak memory is `phys_footprint` (macOS `footprint -t` summary over the
grant's process tree, sampled every 2 s by `TreeSampler`), not RSS.
RSS understates by an order of magnitude: one Gemma load plus a
single inference billed **1.69 GB peak RSS but 14.43 GB peak
footprint**. Fresh runs went through the narrowest matching profile
under the scheduler, so each left its own grant row (owner
`mem-measure-*`) beside the historical rows. History is peak RSS only:
the footprint columns did not exist before this lane.

## The table

| job kind (profile) | peak footprint, fresh | peak RSS, history (grants) | pushes the edge beside Resolve? | reservation |
|---|---|---|---|---|
| `semantics.analyse:model_memory` (Gemma 4 12B residency) | **14.43 GB** (load + 1 inference, 37 s) | 7.43 GB max (27) | **yes** - 14.4 + 5.3 Resolve = 19.7 of 24 | `ram_gb: 15` |
| `semantics.analyse:inference` (nested pass) | 14.44 GB (same tree) | 7.43 GB max (1492) | covered by the outer reservation; nested admissions must stay RAM-free | none |
| `objects.segment:model_memory` (SAM 2 residency) | **7.41 GB** (3 s clip, 55 s) | 5.53 GB max (1, this lane) | **yes** - 7.4 + 5.3 Resolve leaves no room for a second model beside it | `ram_gb: 8` |
| `objects.segment:inference` (nested pass) | 7.42 GB (same tree) | 5.53 GB max (4) | covered by the outer reservation | none |
| `temporal.index:image_defects` | 0.03 GB (synthetic 3 s) | 2.55 GB max (72) | no | none |
| `music.analyse:model_memory` (Demucs) | not run - `demucs` is not installed in the venv, so the workload cannot run on this machine today | 1.78 GB max (15) | no, on current evidence | none |
| `full_suite_gate` | not run - forbidden (firstmate runs the pooled gate) | 9.58 GB max (522) | high but pageable pytest tree, not the resident-model shape; reserving it would serialize the fleet's gate | none |
| `machine` (lane wrappers) | not run - lane-owned | 8.73 GB max (729) | same as above | none |
| `reel.build:placement` / `reel.build:apply` / `native_refresh:ren-native` / `render.build:render` / `reel_preview` / `query_refinement` | - | 3.77 / 2.27 / 3.74 / 1.95 / 2.0 / 1.63 GB max | no | none |

Admission thresholds on this 24 GB machine (floor 2.4 GB): a Gemma
session admits only with >= 17.4 GB available; SAM 2 only with >=
10.4 GB. A 16 GB host refuses the Gemma reservation outright
(`can provide at most 14 GB`), which the suite pins.

## Panic-day evidence, and where it disagrees

`/Library/Logs/DiagnosticReports/panic-full-2026-10-08-234707.0002.panic`
and `panic-full-2026-10-12-230730.0002.panic` are both **watchdog
timeouts** (`no checkins from watchdogd`), not memorystatus kills, and
both record the compressor within limits at capture (24% and 32% of
the compressed-pages limit, swap OK / LOW on the 12th). The stackshots
are binary-encoded, so no per-process footprint was recovered from
them. The memory-exhaustion case for this change therefore rests on
the fresh footprints above plus live pressure (compressor 6.9 GB,
~6 GB available with Resolve open), not on the panic files - which
neither confirm nor refute a 12-17 GB python beside Resolve earlier
that day. Scheduler history does show `full_suite_gate` at 9.36 GB
RSS on 10-05 and 8.49 GB on 10-04, inside the panic window.

## Re-measure triggers

- A semantic session shape change (longer residencies, bigger
  weights): the 15 GB number covers load plus generation transient;
  past it, re-run `probe_gemma.py`.
- A SAM clip shape change (the 7.41 GB number is a 3 s clip at 2
  fps; longer clips track more frames): re-run `probe_sam.py`.
- Demucs becomes runnable here (`demucs` installed): measure before
  reserving; history RSS alone is not the criterion.
