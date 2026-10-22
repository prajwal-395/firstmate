# Evidence - INFRA-006 lane D: the caption composite stays in Resolve

**Measured on:** 2026-10-22, macOS (Apple M5), repository at `873c70f70`
(detached worktree of `video-editing-pilot`).
**Population:** the Ren-owned scratch copy `Ren Scratch - breaktest`,
timeline `Reel 13 - breaktest-selected-dc7fa3f8
[candidate-7e3017c921214379b7effecdb618ba7c] (rebuild staging)` -
1931 frames, 80.6 s, 1080x1920 @ 23.976, 113 placed items, 51 of them
caption cards on the `Subtitles` row (video track 3). Read-only except
where the lane's own duplicate is named; the captain's projects,
timelines and media were never touched, and no broker was started.

## The question

The scout (lane D premise) composited this class of reel's caption
layer in ffmpeg in 13.6 s against a Resolve render of 110-127 s. The
saving was never measured: it is bounded by the caption layer's share
of the Resolve render, and that share needed the live A/B. This lane
ran it.

## The live A/B (one broker job, exclusive hold, cursor restored)

One recorded `run --unsafe --purpose` job on the serving broker, which
asserted the open project, fingerprinted the source timeline, rendered
it whole, duplicated it, deleted the duplicate's 51 caption-row items
(read back empty afterwards), rendered the duplicate, fingerprinted
both, and restored the cursor:

| measure | value |
|---|---:|
| full render (captions in Resolve) | **192.52 s**, `Complete`, 25,433,980 bytes |
| picture-only render (captions deleted on the duplicate) | **196.69 s**, `Complete`, 20,590,282 bytes |
| caption share of the Resolve render | **-4.17 s (noise; \|Δ\|/T ≈ 2%)** |
| duplicate items | 62 of 113 (113 − 51 removed, nothing else touched) |
| source fingerprint at render == fingerprint at inventory | exact sha256 match (`16a3faa8…`) |

The picture-only render took LONGER than the full render. Caption
compositing costs this Resolve render nothing measurable: the 193 s
are picture, Fusion and mix work, and the 51 caption overlays - which
carry no Fusion comps on this reel - disappear inside the noise.

## The ffmpeg composite (local, same machine)

`library/tools/caption_layer_composite.py` (new): the plan reads
record spans through `frame_utils.span_frames` (the placer's own
per-edge rounding), source windows through the placer's own
`source_in + span` arithmetic, positions off
`tight_box.absolute_canvas_box` (or the legacy `origin`) - never
placement floats. Unplaceable segments refuse by name; a ledger
`source_out` that disagrees with the placed window is noted, never
followed (measured on Reel 26: ledger 46 against placed 45, ledger 55
against placed 54; the read-back confirms Resolve plays exactly the
record span in both generations).

| measure | value |
|---|---:|
| cards planned / refused | **51 / 0** (9 ledger-source notes, all followed-the-placed) |
| composite (51 cards over the picture-only render, delivery encode) | **5.72 s** |
| whole-file SSIM, composite vs full Resolve render | **Y 0.9965 / All 0.9972** |
| per-card windowed SSIM (trimmed to each card's own span, 51/51) | **min 0.9715, mean 0.9806** |
| boundary ink agreement (worst 4 cards, frames RS−1/RS/RE−1/RE) | exact within encode noise (±30 bright px of ~14,000) |

The residual is encoder noise and compositor resampling, not
placement: every card lands on its own frames in both files.

## The verdict, with denominators

| | seconds | denominator |
|---|---:|---|
| Resolve renders today (captions inside) | 192.52 | 1 live full render |
| Resolve picture-only + ffmpeg caption composite | 196.69 + 5.72 = 202.41 | 1 live pic render + 1 local composite |
| saving | **−9.89 (none; the new path is slower and adds a lossy generation)** | same |

**The caption composite stays in Resolve.** The saving is not real:
captions are ~0 s of a 193 s render, and moving them out adds ~6 s
plus a second lossy encode for frames that already match at SSIM Y
0.9965. Nothing in the deliver path was changed; there is no fidelity
regression to revert because no behavior moved.

## What lands anyway, and why it is not redundant

`caption_layer_composite.py` + `tests/unit/captions/
test_caption_layer_composite.py` (14 tests, all passing): a measured,
fail-closed caption compositor with its parity instrument beside it
(whole-file SSIM plus per-caption-box SSIM). It is NOT wired into the
deliver path - wiring it in is exactly what the numbers refuse. It
lands as the instrument the next lane reaches for (caption previews
without Resolve, offline caption QA), the way
`composite_declared_treatments` exists beside the native path without
being the default deliver. A second implementation with no measured
reason would be redundant (INFRA-006's own rule); this one carries
its reason: the A/B above.

## Offline corroboration (Reel 26, recorded data only)

29 placed cards joined from the timeline read-back to the caption
ledger by file plus span match: plan 29/0 refused, 0 span mismatches
against what Resolve holds. Composite of the 29-card layer over the
delivered file (timing stand-in - it double-prints captions, so timing
only): 24.4 s at machine load 60-90, same chain 5.7 s-class at load
26. The scout's 13.6 s was the layer alone without a delivery encode;
the honest deliverable-composite number on this machine is 6-24 s
depending on load, and it does not matter: the Resolve side it would
replace costs ~0 s.

## Limits (stated, not hidden)

- One reel, one machine, one day. The A/B ran back-to-back under one
  exclusive hold, so no other writer interleaved; machine load still
  moves absolute seconds, which is why the verdict reads the DELTA
  (−4.17 s of 193 s) and not the absolutes.
- The source timeline was never modified and the cursor was
  restored to its origin on every job. The duplicate
  (`... capcost-pic 1792687647`) is retired by a cleanup job once
  `Ren Scratch - breaktest` is open again (the first attempt
  correctly refused with another project open); until then it is
  named debris in Ren-owned scratch, nothing the pipeline reads.
- The recorded `run` receipts in the broker's display truncate wide
  rows; the full caption inventory (51 rows with pool paths) was
  collected to a file by the job and joined locally. The A/B receipt
  and the inventory share one fingerprint, so the join is exact.

## Where the evidence lives

- the compositor: `library/tools/caption_layer_composite.py`
- the defect-naming tests: `tests/unit/captions/
  test_caption_layer_composite.py` (14 passed)
- the A/B render pair: `/tmp/capcost-1792687647/{full,pic}.mp4`
  (machine-local; regenerable by the procedure above)
- the composite under test: `/tmp/capcost-1792687647/composite.mp4`
