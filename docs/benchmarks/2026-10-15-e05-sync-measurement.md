# E05 source synchronization measurements

Measured 2026-10-15 from raw media stream timestamps, the existing saved
podcast Resolve timeline transcript, and the existing source-memory M1
transcripts. The 001 Resolve project remained closed. These are mechanical
timestamp measurements; no human sync labels or perceptual judgments were
added.

## Media stream clocks

`ffprobe` reported every audio and video stream beginning at 0.000000 seconds
for all seven podcast MXFs and all 17 001 MOVs. On the podcast MXFs, audio and
video stream durations matched exactly at ffprobe's reported precision. On the
001 MOVs, the largest audio/video stream end difference was 0.001667 seconds
(1.667 ms); no stream start offset was measured.

The 001 footage includes one material rate difference: `IMG_1820.MOV` reports
30 fps nominal and 29.803026 fps average, a 0.6566% difference. This exceeds
the catalog's 0.1% VFR boundary. The other 16 clips were below that boundary.
`IMG_1820.MOV` therefore needs VFR-aware source-time mapping; treating its
nominal frame number as a constant-rate clock would accumulate error. The
catalog entry in this existing project predates the current nominal/average/
VFR fields, so its saved catalog does not itself record that classification.

## Podcast multicamera clock and timeline readback

For each listed pair, M1 unique word 4-grams measured the source-clock offset
(`A time = B time + offset`). Drift is the robust difference between median
offsets in the first and last temporal windows of matched phrases. Resolve
readback is measured independently from adjacent camera-switch segments in the
saved `GEO Podcast - Synced` timeline transcript; MAD is the median absolute
deviation across those boundaries.

| Camera A / B | M1 offset | Matches / agreement | Drift over matched span | Saved timeline cuts: median / MAD | Timeline minus M1 |
|---|---:|---:|---:|---:|---:|
| LC4932 / LCATL0013 | +3.800 s | 7,438 / 0.9956 | 0.000 s / 4,179.27 s | +3.875 s / 0.400 s (61) | +0.075 s |
| LC4931 / LCATL0012 | +0.300 s | 561 / 0.9982 | -0.010 s / 283.95 s | +0.052 s / 0.510 s (7) | -0.248 s |
| LC4930 / LCATL0011 | +11.310 s | 393 / 0.9874 | 0.000 s / 204.65 s | +37.775 s / 26.869 s (2) | +26.465 s |
| LC4932 / LCATL0014 | +4,103.390 s | 1,845 / 0.9962 | -0.010 s / 3,297.77 s | +4,103.715 s / 0.158 s (10) | +0.325 s |

No measured M1 pair exceeded the 1-second drift tolerance. Three timeline
readbacks broadly corroborate the content-derived offset. The LC4930 / LCATL0011
readback does not: only two boundaries are available and they scatter widely,
so it is inconclusive rather than evidence to change the measured clock.

The current conversation-clock implementation previously returned a single
constant offset and did not expose temporal change. It now reports end-to-end
drift, its measurement span, and whether the change exceeds the existing
1-second sync tolerance. On the synthetic regression fixture, the measured
change is -2.310 seconds over 98.880 seconds and is flagged; the measured
podcast pairs above remain below tolerance.

## 001 measurement boundary

The 001 result covers source audio/video timestamp starts and stream ends for
all 17 raw MOVs. No saved timeline transcript/readback exists in the 001
project tree, and the Resolve project remained closed as required. The
measurements therefore establish container-level A/V timestamp alignment and
the VFR rate exception, not an independent Resolve placement residual or a
perceptual sync verdict.
