# VEP object detector benchmark on resolved agent labels

**Status: complete.** RF-DETR Nano, Apple Vision and OWLv2 completed the resolved crop set. OWLv2's full 543-crop run completed in one scheduled process with zero failed samples after fixing a large-image preprocessing peak.

## Evaluation set and rules

The label packet contains 521 adjudicated disagreement regions: 2,359 crop-level objects, including 2,128 certain and 231 uncertain labels. Nine regions with no identifiable object were excluded. The evaluation manifest adds 31 strict exact-class agreements from two independent blind agent labelers, for 543 crops total. Labels are agent judgments, not human or gold annotations. The third adjudicator was Claude Sonnet 5.5. Twenty target frames remain without a label submission.

Samples are split by clip or interval: 269 calibration crops and 274 held-out crops. The default confidence threshold (0.1) and IoU threshold (0.5) were fixed before held-out scoring; no threshold was tuned. Each crop's query phrases come from its labeled object classes, so these results measure class-conditioned localization on labeled crops. They do not measure open-world discovery or whole-video detection. Repeated objects in different crops remain separate observations because cross-crop identity was not adjudicated.

Certain and uncertain objects are matched one-to-one by exact class and IoU >= 0.5. Uncertain rows are reported separately, and unmatched detections overlapping uncertain boxes are withheld from false positives. False positives are counted only on adjudicated crops labeled exhaustively. The 31 two-labeler agreement crops are recall-only because their remaining content was not exhaustively labeled.

## Held-out quality

| Detector | Certain hits / boxes | Certain recall | Precision on exhaustive crops | Uncertain hits / boxes | Uncertain recall |
| --- | ---: | ---: | ---: | ---: | ---: |
| RF-DETR Nano | 402 / 1,172 | 34.3% | 27.2% | 8 / 117 | 6.8% |
| Apple Vision foreground proposals | 177 / 1,172 | 15.1% | 91.9% | 3 / 117 | 2.6% |
| OWLv2 incumbent, all 274 held-out crops | 801 / 1,172 | 68.3% | 34.2% | 29 / 117 | 24.8% |

For completeness, calibration results were:

| Detector | Certain hits / boxes | Certain recall | Precision on exhaustive crops | Uncertain hits / boxes | Uncertain recall |
| --- | ---: | ---: | ---: | ---: | ---: |
| RF-DETR Nano | 290 / 987 | 29.4% | 27.2% | 2 / 114 | 1.8% |
| Apple Vision foreground proposals | 149 / 987 | 15.1% | 84.9% | 2 / 114 | 1.8% |
| OWLv2 incumbent, all 269 calibration crops | 683 / 987 | 69.2% | 22.6% | 24 / 114 | 21.1% |

## Scheduler and speed measurements

Runs used offline cached weights and Ren's heavy-work scheduler. Detector jobs ran sequentially. RF-DETR Nano and Apple Vision restarted the detector process for every 60-crop chunk, so their total timings include repeated loads; OWLv2 ran all crops in one process. Warm latency is the median per-crop detector time after the first crop in the run.

| Detector | Samples | Device / precision | Cold first crop | Warm median per crop | Load plus inference | Peak guarded footprint |
| --- | ---: | --- | ---: | ---: | ---: | ---: |
| RF-DETR Nano | 543 | MPS / torch.float32 | 9.16 s | 85.5 ms | 117.44 s | 0.78-1.25 GB across 10 jobs |
| Apple Vision foreground proposals | 543 | Apple Vision / platform-managed | 0.407 s | 217.5 ms | 132.54 s | 0.05-0.21 GB across 10 jobs |
| OWLv2 incumbent | 543 | MPS / torch.float32 | 16.43 s load plus first crop | 2.464 s | 1,502.84 s in one run | 2.83 GB max in adjacent 60-crop trace; full-run reservation 8 GB |

The held-out resolution has no single-run production latency comparison: the RF-DETR and Vision load-plus-inference figures sum repeated loads across scheduler chunks, and the units are crops rather than source frames. The historical incumbent result remains 132/148 boxes recalled (89.2%) at 145.55 seconds over 40 frames; it used different, earlier hand labels and is not directly comparable to these agent-labeled crops.

## OWLv2 memory and adoption

A 52-crop trace before the fix (manifest samples 240-291) began at 2.154 GB, rose during MPS warm-up to 3.863 GB, then fluctuated below that peak. A separate 4K crop, D0297, exposed the actual high-water problem: the OWLv2 image processor pads to a square before resizing, briefly constructing a 3840-pixel square for a source that is ultimately evaluated on the model's existing 960-pixel canvas. That single crop reached about 10.25 GB in the detector process. The previously reported 9.636 GB process-tree diagnostic sum was inflated because `footprint -p <parent>` included descendants and the diagnostic summed those same descendants again; it was not evidence of multiple model workers. The live scheduler's per-process physical-footprint guard did measure the genuine preprocessing spike.

The fix thumbnails inputs larger than 960 pixels to the processor's existing 960-pixel canvas before preprocessing, retains the original source dimensions for returned-box normalization, pads query batches to four with fixed text length 16, and synchronizes/releases per-image MPS allocations. It does not reduce the processor's 960-pixel canvas. The 60-crop trace on samples 240-299 after the fix ranged from 1.579 to 2.827 GB (last ten mean 1.921 GB), with scheduler tree peak 2.8792 GB and no abort. D0297 then completed at 2.283 GB. The full 543-crop score completed under one 8 GB scheduler reservation; no full-run peak telemetry was retained, so the 2.8792 GB value is specifically the measured adjacent trace, not a claimed full-run peak.

The completed bakeoff does not change the adoption decision. OWLv2 remains the incumbent and has the strongest held-out certain recall (68.3%) and uncertain recall (24.8%), with 34.2% precision at the fixed 0.1 confidence threshold. Its 2.464-second warm median per crop is slower than RF-DETR Nano and Apple Vision. RF-DETR Nano remains a conditional fallback; Apple Vision remains a class-agnostic proposal path with higher precision and lower recall on this set. These are class-conditioned crop results, not whole-video detection or editorial-quality judgments.

## Reproduction and provenance

- Label adjudication report: `data/vep-tiebreak-r3/report.md` in the Firstmate task data.
- Manifest and raw detections are retained in Firstmate's `data/vep-mu-detect/` task data; this repository artifact contains the reviewable score summary.
- Models came from pre-existing local caches; no weights were downloaded by this evaluation.
- RF-DETR Nano: `Roboflow/rf-detr-nano`, revision `9f201577d9415f0378084748d27e68586e6b3600`, float32 on MPS.
- OWLv2: `google/owlv2-base-patch16-ensemble`, published revision `cfd3195ba4ea9592eec887ded089f4c08eff231d`, float32 on MPS; inputs are thumbnailed to its 960-pixel processor canvas and query batches use four slots with text length 16.
- Apple Vision uses the platform API; model revision and numeric precision are not exposed.
- This scorecard measures technical box detection only, not editorial quality.
