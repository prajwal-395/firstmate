# FIX 5 - the coarse-frame extraction runs in waves

## The defect the scout measured

`data/scout-opt-latency/report.md` (firstmate home), FIX 5, verbatim:

> ### FIX 5 - Parallelise the coarse-frame extraction (not batch it into one decode)
>
> - **Gain:** the evidence stage's 519.6 s (1,096 independent per-frame
>   ffmpeg spawns) becomes `ceil(1096/N)` waves. With N=8 on 8 cores:
>   **-450 s or so of the 520 s**. Across clips is even safer than within a
>   clip: the three clips are entirely independent (the ledger shows them
>   decoded one after another).
> - **Fidelity risk: none**, provided each frame is still produced by the
>   same command at the same timestamp (the frame cache is keyed by content
>   digest and index, so the writes do not collide). **The frames must be
>   byte-identical** - compare sha256 of the extracted JPEGs before and
>   after.
> - **Do NOT** replace the seeks with one sequential decode: measured at
>   **0.85x realtime** (339.5 s to decode 287.6 s), a full pass over the
>   4,941 s source would cost ~5,800 s against the 467.4 s the seeks cost
>   today. That "optimisation" is a 12x regression.

## The change

`library/tools/analysis/vision_pipeline_v3.py` - the coarse-frame
extractor (`_extract_frames`, reached through `extract_frames`) spawned
ONE `ffmpeg -y -ss T -i SRC -vframes 1 -q:v 2 out.jpg` per frame, one
after another.

The frames are independent - one seek, one output file each - so they are
now extracted in waves of `frame_extract_workers()` threads. The command,
the timestamp and the output path are UNCHANGED, so a frame written by a
wave is byte-identical to the one the serial loop wrote; what changes is
how many extractions are in flight.

The width is the machine's own core count (`concurrency_routing.lane_width`),
capped at 8 - the width the scout measured as the sweet spot - and
`PIPELINE_FRAME_EXTRACT_WORKERS` overrides it. An out-of-range or
unparseable override is REFUSED rather than silently clamped.

The seek pattern is UNCHANGED: each frame is still its own accurate seek.
There is no single sequential decode anywhere in this change, which the
scout measured as a 12x regression.

## The measurement

Measured on the Ren-owned scratch project's own declared source
(`LC4932.MXF`, 4K MXF, 4,941 s - the file the scout measured on), read
only, with the engine's own ledger reader
(`library.tools.perf_ledger.read_rows`) and the engine's own extractor.
25 s frame interval (199 frames), `PIPELINE_FRAME_EXTRACT_WORKERS` 1 vs 8:

| | extract wall | `media_decode` wall | frame requests | frames |
|---|---|---|---|---|
| serial (1 worker) | 135.6 s | 135.6 s | 199 | 199 |
| waves (8 workers) | 38.7 s | 38.7 s | 199 | 199 |

- **`media_decode` wall: 135.6 s -> 38.7 s (3.51x).**
- **Frame requests: 199 -> 199.** The waves change the SCHEDULE, not
  the work asked for.
- **Byte-identical frames: True** - all 199 frames compared by sha256;
  every digest equal between the serial and the waved extraction.

Three independent runs of the same measurement gave **3.51x, 2.77x and
3.24x** - the spread is the machine's load, not the change's
determinism. The serial wall was 131.8-135.6 s every time; the waved
wall was 38.7-47.6 s every time.

The same shape on the smaller `LC4931.MXF` (287.6 s, 59 frames): 44.1 s
-> 17.8 s in the ledger's `media_decode` wall, 59 -> 59 frame requests,
all 59 frames byte-identical.

### Why 2.77x-3.51x and not the predicted 8x

The scout's prediction assumed the machine was otherwise idle. It was
not: `uptime` reported a load average of 50-99 on 10 cores during these
measurements, with other lanes' ffmpeg processes resident. The CPU time
the extractions consumed was CONSTANT across widths (74.3 s at 1 worker,
75.7 s at 8, 76.8 s at 16) - the waves do not add CPU work, they overlap
it - so the wall falls in proportion to how much of the machine was
actually free. On an idle machine the same change should reach the
predicted width; on this one it reached 2.77x-3.51x across three runs.

Scaling measured on `LC4931.MXF` (wall per width, same frames, same
digests at every width): 33.1 s (1), 21.0 s (2), 20.7 s (4), 14.1 s (8),
13.2 s (16). Flat past 8, which is why the cap is 8.

Peak ffmpeg RSS during a wave: 1,855 MB against 1,343 MB serial - the
waves hold more frames in memory at once, and the cap is what bounds it.

## The tests

`tests/unit/context/test_vision_frame_extract.py`:

- `test_coarse_frames_are_extracted_in_overlapping_waves` - the defect
  itself: the extraction overlaps. A serial extractor observes a maximum
  of one live extraction and fails this test.
- `test_a_wave_writes_the_same_bytes_as_the_serial_loop` - the fidelity
  half: the same source extracted with one worker and with eight yields
  the same sha256 for every frame.
- `test_the_declared_width_is_the_one_that_runs` - the width is decided,
  the decision is honoured, and an out-of-range one is refused.
- `test_a_cached_frame_is_not_extracted_again` - a wave does not re-decode
  what the cache already holds.

`tests/unit/context/test_vision_pipeline.py`'s adaptive-extraction test
previously asserted the ORDER the ffmpeg processes were spawned in. That
order is not a contract any more - the waves decide it - so the test now
pins the times asked for and the frame-to-timestamp mapping, which are.

The progress channel (added on `main` by #3027, which landed while this
lane was in flight) is MERGED with the waves, not dropped: every frame
is still declared as a unit of work and finished, so the parent can
still tell a working child from a wedged one.
`test_the_waves_still_report_every_frame_to_the_progress_channel` pins
that: the unit count is the frame count, and nothing is left in flight
when the extraction returns.

## What was NOT done

The scout's note that "across clips is even safer than within a clip"
was not acted on: the clip-level driver in `run_pipeline` decodes the
clips one after another, and parallelising THAT is a different change in
a different module, with the model passes interleaved between the
extractions. The wave inside the extractor already overlaps the frames
of one clip, which is where the measured 519.6 s was spent.

No captain project, timeline or media was written. The measurement read
the scratch project's declared source and wrote only into a scratch
directory outside every project.
