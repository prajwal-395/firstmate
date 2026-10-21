# PROD-006: resource-constrained operation on a 24 GiB Apple Silicon box

Measured 2026-10-22, on the captain's machine, with DaVinci Resolve open
and other lanes running. This is the punchlist item "PROD-006 - Test
resource-constrained operation", measured rather than asserted.

The machine has panicked from memory before (one 12-17 GB python beside
Resolve), so this lane's question was not "is the pipeline slow" but
"what does each heavyweight stage do to a 24 GiB box, and what happens
when the box cannot host it".

## The machine, at measure time

| reading | value |
|---|---|
| physical memory | 24.0 GiB (25.77 GB), 10 cores, Apple M5, macOS 26.3 |
| Resolve | open, live, pid 32626 |
| other lanes | 4+ concurrent pytest trees on the same box |
| available memory | 6.96 - 9.12 GB (`kern.memorystatus_level` 29-39% free) |
| memory pressure | 62-71% (`warn`), 78% during the detection pass |
| swap | **13.1 - 14.3 GB used of 14.0 - 15.4 GB (91-93%)** |

The swap figure is the headline: the machine was ALREADY paging hard
before this lane measured anything, and no part of the scheduler read
it. The macOS swap device grows and shrinks, so the fraction is the
stable reading, not the absolute gigabytes.

## Method

* Peak memory is `phys_footprint` (macOS `footprint -t` over the
  process tree), sampled every 2 s by the engine's own `TreeSampler`,
  because RSS understates by an order of magnitude on this hardware (the
  detection pass below: 0.22 GB RSS against 2.89 GB footprint).
* Swap and memory pressure come from `library/tools/perf_resources.py`
  (`vm.swapusage`, `kern.memorystatus_level`) - the module that already
  owned those readings.
* Live passes ran one at a time, under the scheduler, on Ren-owned
  scratch material: the (read-only) podcast source for detection, and
  real `FullFrameCard` scenes through the engine's FFrames path for the
  caption wave.
* The heavier stages were measured from this machine's OWN grant
  history (`~/.local/share/vep/resource-scheduler.sqlite3`): 11,671
  finished grants, of which 1,949 carry a footprint reading (the
  footprint columns post-date the rest). Denominators are stated with
  every number.

## The table

| stage | before (measured) | after (measured) | what changed |
|---|---|---|---|
| VLM pass (`semantics.analyse:model_memory`) | peak RSS 11.15 GB (n=177), peak footprint **14.43 GB** (n=8). Admission: **waited** - 20 s probe timeout, 600 s in production - for a grant the machine could not give | peak RSS/footprint unchanged (no re-run: 15 GB does not fit beside Resolve on a paging box). Admission: **refused in 0.48 s** with the reading: *"13.1 GB of 14.0 GB of swap in use (93%), and 9.1 GB of 24 GB of memory available"* | the swap is read, and a resident model that cannot be paged in is refused, not waited for |
| detection (open-vocabulary, one real frame) | grounding-dino-tiny: peak RSS 0.22 GB, peak **footprint 2.89 GB**, machine peak swap 14.81 GB, peak pressure 78%, 69 s, 26 boxes. Florence-2: **refused** - `DetectorNotAvailable` with the reason | unchanged - the detector's refusal already degrades with a reason | nothing to fix; recorded |
| segmentation (`objects.segment:model_memory`) | peak RSS 5.53 GB (n=16), peak **footprint 10.14 GB** (n=8), mean footprint 6.55 GB. The reservation was **8 GB** - below the measured peak, so an admitted session overran its reservation by up to 2.15 GB and paged the rest. Admission: waited, like the VLM | reservation **11 GB** (the measured peak plus a margin); admission **refused in 0.52 s** with the reading; the batch refuses ONCE with the reason instead of failing per clip | the reservation covers the peak; the refusal is a refusal, not a stall |
| caption wave, width 4 | 4 real `FullFrameCard` scenes through `run_native_card_wave`: peak tree RSS **0.68 GB**, peak footprint **1.53 GB**, machine peak swap 12.78 GB. The same 4 cards at width 1: 0.31 GB RSS / 0.62 GB footprint | unchanged - **measured, and it is not the machine's edge**: the wave costs ~0.9 GB more footprint than serial | no change; the width stays CPU-derived |
| reel build, Resolve open | `reel.build:apply`: peak RSS 5.20 GB, peak footprint 6.25 GB (n=175, footprint n=105); `reel.build:placement`: peak RSS 3.77 GB (n=60). **Resolve's own peak beside the build was never recorded** - no grant ever watched Resolve | the grant for a resolve-exclusive phase now samples Resolve's own cores, RSS and footprint beside the build's | the build's own peak is unchanged; what Resolve was doing while it ran is now evidence |

Two more readings the machine's own history carries, for the same
reason (they are what a resident model would land on top of):
`full_suite_gate` peaked at 9.58 GB RSS / 13.80 GB footprint (n=620,
footprint n=87), and the `machine` wrapper profile at 8.79 GB RSS /
14.30 GB footprint (n=1417, footprint n=585).

## The defects this measurement found

1. **The scheduler never read the swap.** Admission asked only how much
   memory was free; a machine at 93% of its swap device looked like a
   machine with 9 GB free. Now a RAM-reserving job is REFUSED when the
   swap device is at or above `SWAP_REFUSAL_FRACTION` (0.75) of its
   size AND the demand does not fit the free memory. Both readings have
   to agree: a demand that fits is still admitted, because refusing
   work that would have run is its own regression.
2. **The SAM 2 reservation was below its measured peak** (8 GB reserved,
   10.15 GB measured). Now 11 GB - the peak plus a margin.
3. **One model stage starved another.** A 15 GB waiter that could not
   fit the live headroom held the queue against a 4 GB job that could
   (proven: with the fix the 4 GB job is admitted; without it, the wait
   ends in the timeout). The older waiter is not an ELIGIBLE waiter
   while it cannot run, and eligibility is what the backfill rule asks.
4. **The swap a grant caused was never measured.** Every grant now
   records the machine's peak swap and peak memory pressure beside its
   own peak, and `python3 -m library.tools.heavy_work_lock machine`
   prints the live reading (available memory, swap, pressure band).
5. **Optional models failed the run instead of degrading it.** The OCR
   recognizer's warm-up sat outside the per-clip guard, so a recognizer
   that could not load failed the whole step with a stack trace; it now
   leaves the batch empty and says why. The SAM 2 batch refuses once,
   with the reason, instead of once per clip.

## What was NOT measured, and why

The VLM pass and the SAM 2 pass were not re-run live after the fix: on
this box they cannot be resident (9 GB available, 15 GB and 11 GB
wanted, 93% of swap in use), and forcing either is the panic this lane
exists to prevent. Their "before" numbers are this machine's own grant
history, with denominators; their "after" numbers are the refusal
path, measured on the same box.

The reel build was not re-run live either: it needs the serving broker
and an approved reel on a captain-facing project, which this lane does
not drive for a memory probe. The change to it is the RECORDING of
Resolve's peak beside the build's, and the build's own peak is
unchanged.

The card wave's numbers are four real cards, not the 103 of PR 3086: the
point here is the memory, and four cards at each width is enough to
show the wave's cost against serial.

## Supersession

`docs/benchmarks/2026-10-15-memory-admission.md` measured SAM 2 at
7.41 GB peak footprint on a 3 s clip. This lane's measurement on THIS
machine reached 10.15 GB over 8 sampled grants, so the 8 GB reservation
it produced is superseded by the 11 GB above.
