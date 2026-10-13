# Object detector bakeoff: open-vocabulary seed for SAM 2

Date: 2026-10-13
Frames: 40 (40 stills extracted read-only from the captain podcast MXFs (see manifest.json in the MU-15 scratch))
Ground-truth boxes: 148 (hand-labelled boxes, every phrase verified to be in Gemma 4 12B parsed answer for its frame (build_gt.py))
Phrases: Gemma's object names (VisionModel().analyze_image stills prompt, gemma_raw.json)
Score floor: 0.1; recall at IoU >= 0.5

## Verdict

**owlv2** seeds SAM 2 in step two: recall 0.892 (132/148 boxes) at 3.95 s/frame median on mps.

## Per-model numbers

| Detector | Viable | Load (s) | Median s/frame | Recall | Hit / scored |
|---|---|---|---|---|---|
| vision_foreground | yes (ane; apple-vision) | 0.0 | 0.21 | 0.243 | 36/148 (+0 failed frames) |
| florence2 | no (Florence-2 weights microsoft/Florence-2-base did not load: AttributeError: 'Florence2LanguageConfig' object has no attribute 'forced_bos_token_id') | - | - | - | - |
| owlv2 | yes (mps; google/owlv2-base-patch16-ensemble) | 1.6 | 3.95 | 0.892 | 132/148 (+0 failed frames) |
| grounding_dino | yes (mps; IDEA-Research/grounding-dino-tiny) | 1.9 | 5.52 | 0.27 | 40/148 (+0 failed frames) |

## Method

- One slate, one ground truth, one score: every viable detector ran the same frames against the same phrases.
- A ground-truth box is recalled when any predicted box for its phrase overlaps it at IoU >= 0.5; the Vision baseline predicts no phrase, so any of its foreground instances may cover each box.
- A backend that refuses (`DetectorNotAvailable`) or fails to load is NOT-VIABLE with its reason, never a zero: the table tells 'cannot run' from 'ran, nothing there'.
- Step two (the winner's boxes seeding SAM 2 tracks, which become object entities with `ObjectClass`) is scored separately, on mask coverage of the named object.

## Step two: OWLv2 box seeds a SAM 2 laptop track

- Clip: LCATL0013 t=30..40 s cut read-only from the captain's MXF to a 960-wide scratch mp4 (captain media untouched); `ObjectSegmenter.segment_clip` at 2 fps = 20 sampled frames, seeded ONLY by the OWLv2 `black laptop` box on frame 0 (`seed_boxes`, no face, no blobs). SAM 2.1 hiera-small (`facebook/sam2.1-hiera-small`) on MPS.
- OWLv2 found a `black laptop` box on 20/20 sampled frames (reference, not ground truth).
- Score: hand box [0.15, 0.51, 0.36, 0.77], eye-estimated off the first/middle/last sampled frames (static camera, static laptop, verified by eye on all three). A frame is covered when the tracked mask fills >= 50% of the hand box.
- Result: 20/20 frames covered (fraction 1.0 >= 0.90). MU-15 step-two acceptance passes on real models: the winner's seed becomes a track whose masks cover the laptop on every sampled frame.
- The seeded object carries `seed=detector:owlv2`, label `black laptop`, category `laptop`, which is what mints the open-vocabulary `ObjectClass` on migration (`test_detector_seed_mints_open_vocabulary_class`).
