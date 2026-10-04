# Public face identity backend qualification

**Decision (2026-10-04): no public face backend is wired.** The measured YuNet + SFace stack is not a safe replacement for `buffalo_l` on Ren's hard-frame set: it misses all three full-profile frames at the detector setting that keeps one unambiguous face per frame, has a much narrower same/different-person margin on the remaining frames, and its SFace weight's training-data provenance is unresolved upstream. Keep the measured InsightFace path for the personal edition. This result is evidence for the public-edition lane, not a general license determination.

## Audit claims checked on `origin/main`

The checkout at qualification started on `origin/main` `ecaf059a`.

| Audit claim | Finding on `origin/main` |
|---|---|
| `buffalo_l` is non-commercial unless separately licensed | **Holds.** `THIRD_PARTY_NOTICES`, `requirements/identity.txt`, and `scripts/install_insightface.sh` all state non-commercial research terms. InsightFace's own README says its code is MIT but its pretrained models are non-commercial research only, and directs commercial licensing requests to `recognition-oss-pack@insightface.ai`. |
| `requirements/identity.txt` still calls the `buffalo_l` weights MIT | **No longer holds on this base.** The file now distinguishes MIT/Apache package code from non-commercial model weights. `THIRD_PARTY_NOTICES` records the historical correction. |
| The live face backend is `buffalo_l` | **Holds.** `person_entity._face_app()` constructs InsightFace `FaceAnalysis` with the `buffalo_l` pack and CPU inference. |
| The pack download is pinned and hash-verified | **Does not hold.** `scripts/install_insightface.sh` invokes InsightFace's downloader; the inventory records no pack version or hash verification. |

Sources: [InsightFace license statement](https://github.com/deepinsight/insightface), [Ren identity dependency](../../requirements/identity.txt), [Ren third-party inventory](../../THIRD_PARTY_NOTICES), [Ren installer](../../scripts/install_insightface.sh).

## Candidate and provenance

| Component | Source license statement | Qualification note |
|---|---|---|
| YuNet detector, `face_detection_yunet_2023mar.onnx` | OpenCV Zoo's model directory says all files are MIT. | SHA-256 `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`. |
| SFace recognizer, `face_recognition_sface_2021dec.onnx` | OpenCV Zoo's model directory says all files are Apache-2.0. | SHA-256 `0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79`. The model README does not identify which training corpus produced this exact weight. An [open upstream issue](https://github.com/opencv/opencv_zoo/issues/313) asks the maintainers to clarify the weight's license coverage and training-data provenance. I therefore did not mark this stack commercially cleared for the public edition. |

The official [YuNet model README](https://github.com/opencv/opencv_zoo/blob/main/models/face_detection_yunet/README.md) gives the MIT statement. The [SFace model README](https://github.com/opencv/opencv_zoo/blob/main/models/face_recognition_sface/README.md) gives the Apache-2.0 statement and its recognition results, but not a training-data mapping for this exact ONNX file.

Other plausible pretrained recognizers did not close the provenance gap: the dlib model author calls the recognition weights public domain, while documenting a training set partly drawn from FaceScrub, VGG Face, and internet-scraped images; Open Model Zoo's ArcFace card declares Apache-2.0 but identifies Refined MS-Celeb-1M as its training data. They were not run through this comparison because their public materials did not establish the product's required data-rights provenance. See the [dlib model provenance](https://github.com/davisking/dlib-models/blob/master/README.md) and [ONNX ArcFace model card](https://github.com/onnx/models/blob/main/validated/vision/body_analysis/arcface/README.md).

## Side-by-side measurement

The comparison used the existing private 83-frame, real-footage evaluation fixture (two people, seven source clips, including profile and low-light cases). No video or still was copied into this repository. The source frames were resized to the production width cap of 1408 pixels, yielding 1408x792 inputs for both stacks. Labels follow the existing file-level evaluation, so this remains a two-person fixture rather than a broad population study.

`buffalo_l` was re-run through Ren's current InsightFace `FaceAnalysis` path. The candidate used OpenCV 4.14.0 YuNet detection, `FaceRecognizerSF.alignCrop`, and SFace embeddings, all on CPU. YuNet confidence was swept at 0.9, 0.8, and 0.5. To respect the existing study rule, candidate frames with anything other than exactly one detection at the selected confidence were excluded from that operating-point comparison.

| Measure | Ren `buffalo_l` | YuNet + SFace at confidence 0.8 |
|---|---:|---:|
| Frames with one usable face | 83 / 83 | 80 / 83 |
| Same-person / different-person pairs | 1701 / 1702 | 1569 / 1591 |
| Closest different-person cosine | 0.1333 | 0.2605 |
| Weakest same-person cosine | 0.3015 | 0.3267 |
| Zero-error threshold interval | `0.1333 < t <= 0.3015` | `0.2605 < t <= 0.3267` |
| FAR / FRR at `t = 0.25` | 0 / 0 | 1 / 0 |
| FAR / FRR at `t = 0.30` | 0 / 0 | 0 / 0 |

At confidence 0.8 the candidate's three missed frames were all full-profile samples from one clip. Its zero-error interval on the other 80 frames is only 0.066 wide, compared with 0.250 for `buffalo_l` on those same 80 frames. At confidence 0.9 it yielded 77 unambiguous detections; at 0.5 it yielded 83 candidate frames but six had multiple detections and therefore were not admissible under the evaluation's one-face rule.

The 0.30 result for SFace is a diagnostic threshold chosen from this same small fixture, not an independent validation. With the highest-score YuNet detection forced into all 83 frames, including low-confidence and multiple-detection cases, there was no zero-error threshold interval: the SFace same-person minimum was 0.1562 and different-person maximum was 0.2605. At 0.25 this diagnostic produced one false accept and 71 false rejects. These ambiguous frames are not included in the table's primary comparison.

Model initialization plus processing took about 10.1 seconds for `buffalo_l` and 3.2 seconds for YuNet + SFace on this machine. That speed advantage does not offset the candidate's profile misses, narrower margin, unresolved provenance, and lack of an independent holdout.

## Remaining gap

No candidate tested here meets both requirements for the public edition: sufficient face-identity quality on Ren's hard frames and a clear commercial-use grant with documented training-data provenance. The 83-frame fixture also measures cross-camera face identity only. The existing local evaluation says its audio ground truth has no shared video clock, so it cannot qualify the real speaker-to-face join end to end; Ren's production join is described in [source-memory](../SOURCE_MEMORY.md).

A future candidate needs an explicit commercial-use basis and traceable training-data terms, then must retain profile coverage and produce a useful zero-error margin on this fixture plus an independent, synchronized speaker/video evaluation. Until then, the public face-identity backend remains unselected.
