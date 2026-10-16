# Video decode and preprocessing profile

Date: 2026-10-15  
Code under test: task branch based on `777ed3ba0`  
Machine: Apple Silicon, 24 GiB unified memory  
Evidence scope: one podcast source (`LCATL0011.MXF`) and one Apple 001
source (`IMG_1822.MOV`). Source media was read-only; fresh Source Memory
and the performance ledger were placed in a temporary directory.

## Existing implementation

- `library/capabilities/temporal/media_decode.py` fans one ffmpeg decode
  into scene-change metadata, 30 Hz motion frames, 5 Hz flow and face
  frames, and 1 Hz hue frames. `test_media_decode.py` checks byte parity
  against the previous independent ffmpeg paths.
- `library/tools/source_memory.py::build_frames` extracts scaled I-frames
  with `-skip_frame nokey`, requests VideoToolbox acceleration, and caches
  them by source fingerprint. Vision, CLIP, entity, and footage-frame
  consumers read this M2 sample. It falls back to software ffmpeg when
  VideoToolbox fails.
- `vision_pipeline_v3.py` has its own OpenCV `VideoCapture` window decode
  path. The profile in `docs/benchmarks/2026-10-08-analysis-scheduling-reuse-profile.md`
  measured decode/frame extraction at about 9 s versus 22,072 s total
  wall in a partial 17-clip cold analysis. That is a separate workload and
  older revision, not a direct before/after comparison for this change.
- `perf_ledger.media_decode` already attributes temporal-index decode by
  source fingerprint and purpose. M2 cold extraction and warm cache reuse
  previously bypassed that attribution; this change adds both rows.

## Cold and warm M2 extraction

Each source was run once against an empty isolated cache, then immediately
again against the populated cache. Cold wall time is the measured
`build_frames` decode interval. Warm time includes fingerprint validation
and cache lookup. VideoToolbox was requested and reported as used in both
cold rows.

| Source | File size | Duration | Sample frames | Cold extraction | Warm reuse | Bytes written | Peak Python RSS |
|---|---:|---:|---:|---:|---:|---:|---:|
| Podcast `LCATL0011.MXF` | 2,622,977,072 B | not present in M0 | 429 | 12.885 s | 0.001 s | 5,734,534 B | 25.9 MiB |
| Apple 001 `IMG_1822.MOV` | 171,765,203 B | not present in M0 | 89 | 0.897 s | 0.002 s | 1,074,944 B | 27.1 MiB |

Both cold ledger spans report one ffmpeg child, `backend=ffmpeg`,
`requested_hwaccel=videotoolbox`, `hwaccel_used=true`, and the matching
source fingerprint. Warm spans report `calls=0`, `cache_hits=1`, and
`backend=source_memory_cache`. The codec and source durations were not
recorded in these M0 records, so size-normalized or source-seconds-normalized
rates are intentionally omitted. The reported peak RSS is Python's process
high-water value; it does not include ffmpeg's aggregate process tree or
Resolve.

## Redundant decode findings

There are two useful forms of reuse already in place: M2 is shared across
per-frame consumers by content digest, and temporal indexing fans multiple
frame measurements out of one ffmpeg source decode. The new ledger identity
for M2 uses `source_fingerprint` plus `decode_purpose`, so duplicate source
work can now be counted alongside temporal-index rows. The existing 2026-10-08
profile does not show decode as a workflow bottleneck. These results support
keeping the current cache and fan-out paths; they do not establish that all
producers share one decode. In particular, the vision window extractor's
OpenCV path is independently scheduled and has different temporal windows
and representations.

## Candidate runtimes and limits of this run

- **AVFoundation / VideoToolbox:** the current M2 path already uses ffmpeg's
  VideoToolbox acceleration on this machine. A direct AVAssetReader versus
  ffmpeg comparison was not run, so no native decoder speed or pixel-parity
  claim is made.
- **Metal preprocessing:** no controlled Metal versus CPU comparison was
  run. Existing consumer shapes differ (gray flow/motion, RGB face/hue, and
  model-specific resize/normalization/crop), so a meaningful comparison needs
  matched output tensors and parity checks for each consumer.
- **MLX-video:** the [AmiraniLabs project](https://github.com/AmiraniLabs/mlx-video)
  describes AVFoundation-backed frame reads and clip batching. It is a useful
  reference for an isolated decode adapter, not evidence of a measured
  advantage for Ren's sampling contract. The unrelated
  [Blaizzy MLX-Video project](https://github.com/Blaizzy/mlx-video) targets
  generation/inference and has a similar name.
- **MLX-CV:** its [published implementation](https://github.com/appautomaton/mlx-cv)
  advertises SAM 3.1 image/video, Depth Anything V3, LocateAnything-3B
  grounding, and RF-DETR Nano detection. I installed source revision
  `f9efadb87bc0efd807e1d3751ca384375a8cff33` (`mlx-cv` 0.0.4) in an
  isolated Python 3.13.16 environment at `/tmp/vep-mlx-cv-venv`, with MLX
  0.32.4. Its loader enumerated all seven family surfaces. The package's
  SAM 3.1, DA3, LocateAnything, and RF-DETR model-family unit suites passed
  32 tests with one skipped. These use synthetic fixture weights; no real
  checkpoint was loaded, so this establishes only the code paths and typed
  result contracts, not model quality or real-checkpoint latency. The
  temporary source, environment, and package cache were removed after the
  run. No checkpoint weights were downloaded.
- **MLX, Core ML, PyTorch/MPS, ONNX Runtime, and native Metal:** no common
  model/preprocessing workload was run across these stacks. This report
  does not recommend replacing a runtime or migrating Ren's core environment.
- **Reusable frame buffers:** current M2 uses disk-backed JPEG samples;
  temporal indexing writes its representations to raw scratch files. No
  in-memory buffer experiment was run, and JPEG-to-consumer decoding or
  cross-process transport may dominate any saved copy. A follow-up should
  isolate decode, resize/normalize/crop, and handoff costs separately.

## Addendum 2026-10-16 - decode-backend, preprocess, and reference-runtime measurements

Code under test: same task branch at `b793bdeae` plus this addendum (no
library code changed). Method: read-only sources, scratch under
`/tmp/vep-mu-decode/`, heavy steps wrapped in
`heavy_work_lock --profile temporal.index:image_defects`. Apple 001
(`IMG_1822.MOV`, 171,765,203 B, 85.82 s, H.264 1080p) is the
calibration source; podcast `LCATL0011.MXF` (2,622,977,072 B) is the
held-out confirmation, run once per backend with no tuning. No agent or
human labels were used: every claim below is latency, bytes, or
pixel-parity, never a quality judgement.

Correction to the 10-15 section: `vision_pipeline_v3.py`'s cv2 use is a
one-open-check per cut plus ffmpeg cuts, not a full window decode; the
genuinely independent cv2 decode path is `ocr_extractor.py:386`
(`cv2.VideoCapture` per clip).

### PERF-033 - VideoToolbox versus software on the M2 I-frame path

`extract_iframes` at width 480, once per backend per source (held-out
podcast run once each; frame counts match the width-384 M2 profile rows
above, so sampling is unchanged and only absolute times shift with width):

| Source | VideoToolbox | Software | Speedup | Frames equal | PTS equal | Pixel parity (8 samples) |
|---|---:|---:|---:|---|---|---|
| Apple 001 (calibration) | 1.526 s | 4.728 s | 3.1x | 89 = 89 | yes | mean abs diff 0.0, max 0.0 |
| Podcast (held-out) | 16.238 s | 262.871 s | 16.2x | 429 = 429 | yes | mean abs diff 0.0, max 0.0 |

Per-file JPEG bytes are identical between backends on both sources
(Apple 1,426,009 B; podcast 7,921,726 B). The shipped default -
VideoToolbox first with software fallback - is now measured, not
assumed: up to 16x faster with bit-identical output. No code change;
this confirms the incumbent under ARCH-004/ARCH-005.

The temporal shared fan-out (`decode_temporal_media`, 854x480 face) was
also tried with `-hwaccel videotoolbox` on Apple 001: 33.0 s versus
33.5 s software, i.e. no gain. That path is filter- and write-bound
(572 MB of raw scratch per 85 s clip, 528 MB of it face RGB), not
decode-bound, so acceleration is correctly scoped to the M2 path and is
not adopted for the shared decode. No code change.

### PERF-034 / PERF-040 - matched resize/normalize/crop across CPU runtimes

One Apple 001 frame decoded once (decode excluded); each target is
resize to the consumer shape plus to-float32 `/255` normalization;
parity is max abs diff against the cv2 result; 30 reps:

| Target | cv2 | PIL | torch CPU | torch MPS | numpy center-crop |
|---|---:|---:|---:|---:|---:|
| motion gray 160x90 | 0.021 ms | 5.056 ms (diff 0.263) | 1.923 ms (diff 0.004) | 7.886 ms (diff 0.004) | 0.0002 ms |
| face RGB 480x270 | 0.751 ms | 35.876 ms (diff 0.153) | 5.621 ms (diff 0.002) | 7.576 ms (diff 0.002) | 0.0002 ms |
| hue RGB 80x45 | 0.014 ms | 14.856 ms (diff 0.435) | 1.203 ms (diff 0.002) | 11.846 ms (diff 0.002) | 0.0003 ms |

cv2 wins every shape by 7x-1000x at matched outputs; torch MPS is
slower than torch CPU at these shapes (transfer-dominated), so GPU
offload is not worthwhile for consumer-size frames. Unavailable in the
core env and therefore unmeasured with no env migration (PERF-039):
coremltools (absent), a Metal python binding (absent), ONNX Runtime
resize (needs a model graph; the `onnx` package itself is absent).
MLX is importable in the core env but exposes no matching image-resize
primitive to benchmark. Nothing adopted.

### PERF-036 - reusable frame buffers

480p RGB frame: JPEG encode+decode roundtrip 5.198 ms versus numpy
memcpy 0.0061 ms (~850x), and the JPEG roundtrip changes pixels
(lossy) while the buffer copy is exact. The M2 disk-JPEG handoff costs
milliseconds per frame per consumer read; the temporal shared decode
already avoids this with raw scratch files. This bounds the prize for a
buffer redesign but does not by itself justify one: the shared fan-out
is 33.5 s of filter+write, of which JPEG handling is not the share.
No code change; the numbers go to the lifecycle lane's residency work.

### PERF-035 - MLX-video as a reference implementation

Cloned `AmiraniLabs/mlx-video` at `235ba9f` (code only, no weights)
and built 0.2.0 in an isolated Python 3.14 venv with MLX 0.32.4,
pip `cmake`/`ninja`, `nanobind==2.10.2` (their pin). Contract summary:
AVFoundation `AVAssetReader` batched clip reads with greedy-seek
batching (`_SEEK_RATIO = 2.0`), Metal fused kernels (bilinear resize,
normalize, color ops) via `mx.compile`, layout-aware THWC/CTHW
transforms. Two blocking findings: (1) every `_ext` entry point that
crosses an `mx.array` fails against MLX 0.32.4 - argument conversion
rejects `mx.array` and `video_read_frames` cannot return one - while
pure-metadata `video_info` works, so no AVFoundation-versus-ffmpeg or
Metal-versus-cv2 latency was measurable from this build; (2) the
package is LGPL-3.0-or-later, flagged for the license lane -vendoring
or linking it into Ren would carry copyleft obligations, so it stays a
reference only. No code vendored, no dependency added.

### PERF-037 / PERF-038 - MLX-CV real-checkpoint handoff (no download)

Hub metadata (no weights fetched): `appautomaton/locateanything-3b-bf16-mlx`
rev `91c54d9a` is 7,052,947,645 B (7.04 GB safetensors);
`appautomaton/sam3.1-multiplex-bf16-mlx` rev `c087cfe51b` is
1,748,029,937 B (1.75 GB safetensors). RF-DETR Nano and Depth Anything 3
have no published releases - their weights are author-local, so no
download is possible at all. Combined with the 10-15 synthetic-suite
result (32 passed, 1 skipped: code paths and typed contracts verified),
real-checkpoint latency and any quality claim are deferred to the
consumer lanes (detect, ground, depth, sam), which own the fixtures,
label protocol, and adoption scorecards; a latency-only smoke of a
1.7-7 GB BF16 model would cost this lane gigabytes of download for a
number it does not own. No weights downloaded.

### PERF-047 - workflow-level decode share (Apple 001, ledger-measured)

One `perf_ledger.recording` run: M2 cold build plus the temporal shared
decode on the 85.82 s calibration source, 35.0 s wall total:

| Row | Wall | Calls | Bytes written |
|---|---:|---:|---:|
| `source_memory.m2_iframe_sample` (ffmpeg/VideoToolbox) | 1.421 s | 1 | 1,074,944 (matches the 10-15 width-384 row exactly) |
| `temporal.shared_video_measurements` (software fan-out) | 33.517 s | 1 | 571,753,440 (motion 37 MB, flow 6 MB, face 528 MB, hue 1 MB) |

The mini-workflow runs at 0.41x real time and is ~100% decode by
construction (no inference included); the actionable split is that M2
sampling is 4% of decode wall and the shared fan-out 96%, with face RGB
at 92% of decode bytes. This lane adds observability, not speedup: the
before/after is the ledger attribution itself (M2 rows did not exist
before `b793bdeae`).

### Download and environment record

No model or checkpoint weights were downloaded for any item above. Code
cloned for reference only: `AmiraniLabs/mlx-video@235ba9f`,
`appautomaton/mlx-cv` HEAD (static analysis; the 10-15 synthetic-env
result stands). Isolated-env pip packages (never in the core env):
`mlx==0.32.4`, `cmake`, `ninja`, `setuptools`, `wheel`,
`nanobind==2.10.2`, `huggingface_hub`, `opencv-python-headless`. The
isolated venv, both clones, and all scratch decode output were removed
after the runs; only the JSON result files and harness scripts were
kept under `/tmp/vep-mu-decode/` (outside the worktree).

## Change and conclusion

`build_frames` now emits `media_decode` rows for both actual M2 extraction
and fresh-cache reuse, including source fingerprint, backend, acceleration,
frame count, bytes written, and requested sample width. This makes M2
decode requests visible in `ren profile` and comparable to other decode
producers. It adds observability, not a measured speedup. The only direct
workflow-level evidence currently available is the earlier profile showing
decode below 0.1% of that partial analysis run; the current two-source
experiment isolates M2 cold/warm behavior but is not an end-to-end workflow
comparison.

Measured and confirmed (no code change): the VideoToolbox-first M2 default
(3.1x Apple, 16.2x podcast, bit-identical output) and the one-decode
fan-out shapes (M2 shared by digest; temporal shared decode byte-stable).
Attempted and rejected on measurement: VideoToolbox for the shared
temporal fan-out (33.0 s vs 33.5 s - write-bound, not decode-bound).

No frame-buffer redesign, native decoder, Metal preprocessing backend, or
experimental model runtime is adopted. Remaining open with handoff notes
above: Metal-versus-cpu latency (blocked on the mlx-video ABI break),
MLX-CV real-checkpoint latency and all quality claims (consumer lanes,
with the label protocol), CoreML/ONNX coverage (absent from the core env,
no migration per PERF-039).
