# PERF-008 - a targeted request measures the frames it needs, and reuses the rest

## The defect

The request-driven object lane (`library/tools/analysis/object_grounding.py`)
answers a QUESTION - "where is the cell phone in this window?" - by
sampling the window's frames and asking a detector about each one.  It is
the demand-driven half of the object lane: step 1.06 answers PLANS, and a
request that names no plan found no objects at all.

Two things were wrong with it.

**It measured every frame of every window, always.**  The pass had one
cache and it was the Project Model's own rows, and a row was found by the
WINDOW it covered.  A request for a window wider than an earlier pass
covered therefore re-measured every frame of the wider window - including
the frames the earlier pass had already measured.  On the OWLv2 producer
(7.269 s per frame, measured on the M0 fixture) that is the whole cost of
the answer paid twice.

**It could not tell one measurement from another.**  Nothing recorded
WHICH model answered, in WHICH configuration, or by WHICH version of the
analysis, so a stored pass was served for a request it never answered: a
`presence` request (the cheap COCO detector) was served the `grounded`
request's answer, a pass made for "cell phone" was served for "handheld
device", and a pass made by an older version of the analysis was served as
the current one.  That is the punch list's "reuse previous evidence
without treating stale analysis as current", inverted: the evidence was
reused, and the staleness was invisible.

## The change

`library/tools/analysis_cache.py` owns the key a cached analysis is filed
under, and the store it is filed in.

**The key names four things**, and only those four: the SOURCE's identity
(the content digest of the media), the MODEL that answered, the
CONFIGURATION the question was asked in (the phrases, the sample rate, the
score threshold, the detector), and the ANALYSIS VERSION (the producer's
own version string).  `AnalysisKey.signature()` is the name the entry is
filed under, and an entry whose key is not the wanted key is never served:
`differing_components()` names which of the four moved, so a refusal is a
fact about the run rather than a silence.

**The store is per source identity.**  It lives in the source memory,
beside the other measurements of the same content digest
(`<source memory root>/<digest>/analysis_cache.json`), so a camera file
measured once is measured once for the project that owns it, and the store
follows the same project scoping every other measurement follows.

**The unit of reuse is the FRAME.**  A detection pass samples the source at
a rate, and every sampled frame is one measured thing: the boxes the
detector answered on it, and the second of source it stands for.  The
frame indices are SOURCE frame numbers, so a frame is the same frame in
two passes or it is not the same frame at all.  A request asks the cache
for the frames it samples; the frames it holds come back as the answer,
and only the frames it does not hold are extracted and detected.

**What was skipped is recorded.**  The pass reports its reused frames and
its measured frames (`cache.hits`, `cache.misses`,
`cache.reused_frames`, `cache.measured_frames`), and the acquisition
ledger carries them as `skipped` rows with the reason.  A stale entry that
was refused is recorded as `refused_stale`, with the components that
differ.

**The M4 lane got the same treatment.**  The scene-window cache in the
source memory stored the `producer_version` that measured each window, and
the reader returned that version without COMPARING it: a window measured by
an older version of the analysis was served as the current one.  It is now
compared, and a window that does not match is refused and recorded
(`project_model_acquisition.cached`, the `m4` branch).

`REN_ANALYSIS_CACHE=off` turns the cache off, and each lane then measures
everything it is asked for - the escape hatch the measurement's independent
arm needs.

## The measurement

`library/tools/analysis/bench_demand_analysis.py`, on a Ren-owned scratch
copy of the podcast camera's source: 90 s of `LC4932.MXF` (4K MXF, 23.976
fps) re-encoded to a 960x540 scratch mp4 (`/tmp/perf008-scratch/probe90.mp4`).
The captain's media was read only; the copy is scratch.

    ~/.local/share/vep/venv-py312/bin/python3 \
        -m library.tools.analysis.bench_demand_analysis \
        --source /tmp/perf008-scratch/probe90.mp4 \
        --phrase "cell phone" --first 0-10 --second 0-20 \
        --root /tmp/perf008-scratch/memory

**The scenario.**  A targeted edit request names a window of the footage
and asks what is in it.  The engine answers it.  Then the SAME request is
asked over a WIDER window - the edit that named the moment, asked with
more of the cut around it.  The wider window contains the frames the first
answer measured.

**The arms.**  `before` is the analysis cache off, so the wider request
measures every frame of the wider window again; `after` is the cache on.
Both arms run in the same process, on the same media, with the same
detector, and the model is loaded once before either arm (a model load is a
cost of the first measurement on this machine, not of the request).

The producer is OWLv2 (`owlv2_named`), the STANDARD tier: 7.269 s per
frame measured on the M0 fixture.  `rf_detr_nano` is not installed on this
machine, so the cheap tier could not be measured here.

| arm | pass | frames DETECTED | frames reused | analysis seconds |
|---|---|---:|---:|---:|
| before (cache off) | narrow 0-10 | 10 | 0 | 71.7 |
| before (cache off) | **wide 0-20** | **20** | **0** | **98.4** |
| after (cache on) | narrow 0-10 | 10 | 0 | 51.2 |
| after (cache on) | **wide 0-20** | **10** | **10** | **59.6** |
| after (cache on) | wide 0-20, asked again | 0 | 20 | 0.5 |

The wide request - the one that carries the answer - cost **98.4 s ->
59.6 s** of analysis (38.8 s saved, 1.65x), with the cache hits going
**0 -> 10**.  The same request asked a second time cost **0.5 s** of
analysis: every frame it needs was already measured.

**The frame count is the deterministic measure and the seconds follow
it.**  The wide request DETECTED 20 frames before and 10 after: half the
detector work, on the same 20 frames of source.  The seconds are that
halving plus the extraction and the identity resolution, which both arms
pay for all 20 frames (the extraction cost is in the source, not in the
number of frames asked for, and the identity half needs every
detection's frame on disk beside it).

**The narrow request is the CONTROL, and it is also the machine's own
variance.**  Both arms detected the same ten frames there, in 71.7 s and
51.2 s - the same work, 28% apart, on a machine that was serving other
lanes while this ran.  The wide request's saving (38.8 s) is larger than
that spread, but the honest reading is the frame count: 20 detections
became 10.

An earlier run of the same bench, before the identity half (VI-007,
merged from main) was part of this lane, measured 150.4 s -> 51.1 s
(2.94x) on the same media and the same windows: the identity resolution
is now part of what both arms pay for, and it is what the smaller ratio
above is made of.

**The denominators.**  The wider request samples 20 frames of source at
1 Hz (20 frames inspected, 20.0 s of source covered, 20 detections
requested).  `before` measured all 20; `after` measured 10 of them and
reused 10.

**The fidelity half.**  The two arms' answers are compared: the
detections (phrase, box, score, detector, frame index, time) and the
coverage map (scope, sample rate, inspected intervals, unexamined
intervals).  They are EQUAL, and so are the Project Model rows the two
answers file - the rows a later edit reads.  A difference is the bench's
non-zero exit, not a number.

## What was left undone

- **The cross-project reuse of a cached measurement is not implemented.**
  The store is per project (it lives in the project's source memory), and
  the licence and share machinery that governs model-derived evidence
  across projects (`model_evidence_provenance.reuse_evidence`) was not
  extended to the object lane.  A second project over the same footage
  still measures it again.  That is the same shape as the M4 lane's
  provenance-gated cross-project reuse, and it is the next slice.
- **Only the object lane uses the frame cache.**  The M4 scene windows are
  cached per window (source memory `window_coverage`), not per frame, so a
  request for a window that overlaps two cached windows re-measures both.
- **The segmentation lane (1.06) is still plan-triggered and its own
  content-keyed skip.**  It was not moved onto this cache.
- **The measurement is one request on one source.**  The frame-level reuse
  was measured on the object lane only; the same shape would have to be
  measured again for any other lane that adopts it.
