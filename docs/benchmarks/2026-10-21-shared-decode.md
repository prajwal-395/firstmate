# PERF-007 - one decode for the frames two lanes measure

## The defect

Two analysis lanes measured the same source frames, at the same geometry,
and each ran its own whole-source decode:

| lane | where it runs | what it decodes |
|---|---|---|
| picture sampler | step 1.03 (`library/tools/analysis/picture_quality.py`) | the whole source at 5 Hz, 180 px short side, greyscale |
| VI-03 source sampler | step 1.04 (`library/tools/analysis/image_defects.py`) | the whole source at 5 Hz, 180 px short side, greyscale |

The two commands were almost the same and the frames were the same
frames: the picture sampler's filter was `fps=5,scale=W:H` and the
VI-03 sampler's was `fps=5,scale=W:H:in_range=auto:out_range=full`.  On
the geo-podcast sources the difference changed nothing - the decoded
greyscale bytes are identical (measured below) - so the second decode
was pure repetition.

**What it cost.**  Measured on this machine on 2026-10-21, at a load
average of 60-80 on 10 cores, on 120 s of `LC4932.MXF` (4K, 23.976 fps):

| decode | wall |
|---|---:|
| picture sampler's command | 3 m 29 s |
| VI-03 sampler's command | 3 m 56 s |
| both, byte-identical output | 7 m 25 s for one set of frames |

That is 0.57x realtime: one whole-source decode of `LC4932.MXF` (4,941 s)
is about 2.4 hours at that rate, so the project paid it twice per source
per run - once in step 1.03's soft-picture pass and once in step 1.04's
source sampler.

The memory was worse.  The picture sampler held the whole sample in
memory: the raw buffer and a float32 copy of it.  At 5 Hz and 180 px
that is 288 kB per second of source, so 1.42 GB of raw frames and 5.7 GB
of float32 frames for an 82-minute source.

## The inventory: which lanes decode the same source frames

Every analysis lane that decodes a source, and whether its frames can be
another lane's frames:

| lane | decode | frames | shareable? |
|---|---|---|---|
| M2 (`source_memory.extract_iframes`) | `-skip_frame nokey`, whole file, 384 px JPEG | the source's I-frames | no - a different frame set (keyframes, not a 5 Hz grid) and a different geometry |
| M3d (`person_regions`) | the same I-frame pass at 1152 px | the same source frames, at another size | no - the size is a measured quality decision (the module's own benchmark: 1152 px masks match a 1920 px decode at median IoU 0.991; 384 px leaks the table reflection) |
| M3 (`person_measurements`) | none | reads M2's frames | already shared |
| picture sampler | whole file, 5 Hz, 180 px grey | the 5 Hz grid | **yes - with VI-03** |
| VI-03 (`image_defects`) | whole file, 5 Hz, 180 px grey | the 5 Hz grid | **yes - with the picture sampler** |
| temporal index (`capabilities/temporal/media_decode`) | one ffmpeg fan-out: 30 Hz 160x90 grey motion, 5 Hz 160x90 grey flow, 5 Hz face-sized RGB, 1 Hz 80x45 RGB hue | its own set | no - every branch has its own geometry; this lane already fans one decode out to all of its own measurements |
| VLM coarse frames (`vision_pipeline_v3.extract_frames`) | one seek per frame, full resolution JPEG | 5 s-spaced full-resolution frames | no - different frames and a different geometry |
| VLM window clips (`extract_video_clips`) | one transcode per 10 s window, 720 px | transcoded windows | no |
| detector / SAM 2 (`object_segmentation`) | whole file at 2 fps, full-resolution JPEG | the 2 fps grid | no - a different frame set and a different geometry |
| audio (`audio_quality`, `speech_loudness`) | per-channel PCM and per-range `loudnorm` | audio, not frames | not done - see "What was left" |

So exactly one pair shares.  That is the honest answer to the punchlist's
question: the redundant decode is the whole-source 5 Hz greyscale pass,
and it is paid twice.

## The rule

A decoded frame may be reused by another lane only when the SOURCE FRAME
and the PIXEL GEOMETRY are the same, so an entry is keyed by both:

* the source's content identity (`footage_identity.fingerprint`: size
  plus a digest of both file ends) - a re-recorded file never serves
  another file's frames; and
* the RECIPE - the rate, the frame size, the pixel format and the
  interval.

That is the punchlist's "distinguish artifacts that are safe to cache
from those dependent on analysis parameters": a recipe that differs is a
different artifact with its own decode, and the cache never serves one
lane's geometry to another.  The tests
(`tests/unit/picture/test_shared_decode.py`) pin each half: a different
size, a different rate and a different source each get their own decode.

## The change

`library/tools/shared_decode.py` is the shared decode: one command, one
cache entry per (source, recipe), one file of frames on disk, and a
reader that maps it instead of holding it.

* The two lanes now ask the cache for their frames.  The picture sampler
  asks for the whole source at 5 Hz; VI-03 asks for the same thing; the
  second one to arrive reads what the first one wrote.
* The frames are READ, not held: the picture sampler's whole sample used
  to be one in-memory buffer, and is now a memory map.
* `REN_SHARED_DECODE=off` turns sharing off and each lane decodes its
  own - the escape hatch, and the benchmark's independent arm.
* The entries are registered with the existing cache lifecycle
  (`kind="decoded_frame"`), so the budget and the eviction that already
  govern decoded frames govern these.
* `shared_decode.purge(source, recipe)` removes one source's frames.

## The measurement

`library/tools/analysis/bench_shared_decode.py`, on a Ren-owned scratch
copy of the podcast camera's 4K source (the first 120 s of `LC4932.MXF`,
re-encoded to a scratch mp4; the captain's media was read only):

    python3 -m library.tools.analysis.bench_shared_decode \
        --source /tmp/perf007-scratch/probe120.mp4 \
        --root /tmp/perf007-scratch/cache120

Every arm runs in its own process, so the peak-memory figure belongs to
that arm.

| arm | wall | peak RSS |
|---|---:|---:|
| original picture command (its own decode) | 17.1 s | 58.6 MB |
| original VI-03 command (its own decode) | 30.0 s | 61.3 MB |
| **independent total (what the run paid)** | **47.1 s** | |
| shared decode (one decode) | 25.4 s | 62.5 MB |
| shared read (the second lane) | 0.23 s | 65.6 MB |
| picture sampler, old implementation | 21.7 s | **260.7 MB** |
| picture sampler, sharing off | 25.0 s | 68.2 MB |
| picture sampler, sharing on | 0.41 s | **93.0 MB** |
| VI-03 sampler, sharing off | 28.1 s | 84.6 MB |
| VI-03 sampler, sharing on | 6.9 s | 80.2 MB |

**Decode seconds.**  47.1 s -> 25.4 s of decode for the same frames: the
second decode is gone.  An earlier run of the same benchmark on the same
machine (load average 60-80 throughout) read 117.3 s -> 54.2 s.  The
wall times are noisy because the machine is loaded, but the COUNTS are
not: two decodes became one, in both runs, and the read that replaced
the second decode is 0.23 s.

**Peak memory.**  260.7 MB -> 93.0 MB for the picture sampler on a 120 s
sample: the old path held the raw buffer and a float32 copy of it
(34.5 MB + 138 MB), and the new path maps the frames instead.  The old
path's memory grew with the source - 288 kB per second of source raw,
times four for the float32 copy - so about 7.1 GB for an 82-minute
source.  The new path holds one frame (57.6 kB).

**Cache bytes.**  34,560,000 bytes for 120 s of source - 288 kB per
second of source, so 1.42 GB for an 82-minute source, 288 kB per second
of every source in a project.  The entry is a raw greyscale sample, not
a re-encode, and it is removable (`purge`) and evictable (the cache
lifecycle's `decoded_frame` budget).

**Outputs byte-identical.**  The frames hash the same three ways:

    original_picture_command  f60c306a7fe48ecad99030059af342c7bfcdafa993125f456912ada43a189591
    original_defects_command  f60c306a7fe48ecad99030059af342c7bfcdafa993125f456912ada43a189591
    shared_miss               f60c306a7fe48ecad99030059af342c7bfcdafa993125f456912ada43a189591

and each lane's measurement is the same with sharing on and off:

    picture sampler  ad8983784e92... (old implementation, sharing off and on)
    VI-03 sampler   9f6dd919672f... (sharing off and on)

The same comparison on the captain's own `LC4932.MXF` (120 s window,
read-only): the picture command and the VI-03 command hash the same
(`756331d56fcae4321333cf5b4fbfefe73e4f14439ded947e72cf2dd513dc6391`), so
the VI-03 command's range flags never changed the decoded greyscale.

## What was left undone

* **The audio measurements.**  `audio_quality` (per-channel PCM and
  BS.1770 loudness) and `speech_loudness` (per-range `loudnorm`) each
  decode the same source's audio on their own.  They measure different
  things over different ranges, so sharing them needs a different
  service - a shared audio sample with its own recipe - and this change
  does not attempt it.
* **The temporal index's fan-out.**  It already fans ONE decode out to
  all of its own measurements (`capabilities/temporal/media_decode`),
  which is the pattern the shared decode follows.  Its decode could
  publish the 5 Hz greyscale branch into the same cache, but the picture
  pass runs in step 1.03 and the temporal index in step 1.04, so the
  ordering would have to change for it to pay.
* **The other lanes' decodes.**  M2, M3d, the VLM's coarse frames and
  the detector's 2 fps frames are not the same frames as the 5 Hz grid,
  so they keep their own decodes.  Making them share would mean
  changing what they measure, which is a different decision.
* **The decode itself.**  The shared decode is still one whole-source
  sequential decode.  A keyframe-only pass would be cheaper still, but
  it would not produce the 5 Hz grid the two lanes measure.
