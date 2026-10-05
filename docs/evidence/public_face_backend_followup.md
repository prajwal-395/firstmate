# Public face backend follow-up: dlib, AuraFace, BlazeFace

**Decision (2026-10-05): still no public face backend is wired.** The follow-up
qualified the candidates the decision board named. dlib's ResNet - the board's
top pick - **fails the quality bar decisively**: on Ren's 83-frame fixture its
embeddings do not separate the two people at all (every same- and
different-person cosine is above 0.95). AuraFace is the strongest new lead but
clears neither bar: it misses the three full-profile frames and its zero-error
margin on the rest is only 0.029 wide (versus 0.168 for `buffalo_l`), and its
training-data provenance is unnamed upstream. BlazeFace was not measured (see
[Runtime note](#blazeface-runtime-note)); its own model cards exclude identity
recognition and looking-away faces, which is a use-case blocker for Ren's
speaker-identity task regardless of detection quality. Keep the measured
InsightFace path for the personal edition. This extends
[public_face_backend_qualification.md](public_face_backend_qualification.md)
(#1676); it is evidence for the public-edition lane, not a general license
determination.

## Audit claims checked on `origin/main`

Checked against `origin/main` at `de9bf4d8` on 2026-10-05.

| Audit claim | Finding on `origin/main` |
|---|---|
| `buffalo_l` is non-commercial unless separately licensed | **Holds.** `scripts/install_insightface.sh` states the pack ships under insightface's non-commercial research term; `requirements/identity.txt` carries a LICENCE SPLIT note; `THIRD_PARTY_NOTICES` inventories it. |
| `requirements/identity.txt` still calls the `buffalo_l` weights MIT | **No longer holds.** The file now distinguishes MIT/Apache package code from the non-commercial model weights. |
| The live face backend is `buffalo_l` | **Holds.** `person_entity._face_app()` constructs insightface `FaceAnalysis` with the `buffalo_l` pack and CPU inference. |
| The pack download is pinned and hash-verified | **Does not hold.** `scripts/install_insightface.sh` invokes insightface's own downloader; no pack version or hash verification is recorded. |

## Candidate and provenance

| Component | Source license statement | Qualification note |
|---|---|---|
| dlib MMOD detector, `mmod_human_face_detector.dat` | dlib's model author calls the repository's trained model files public domain. | SHA-256 `be467b1a76f482693de3b0f6a1ff91d092319be71523d4d4b0628f6a53fcb87a`. Training set includes AFLW, WIDER FACE, ImageNet, Pascal VOC, VGG, FaceScrub. |
| dlib 5-point landmarks, `shape_predictor_5_face_landmarks.dat` | Same public-domain statement. | SHA-256 `c4b1e9804792707d3a405c2c16a80a20269e6675021f64a41d30fffafbc41888`. Trained on 7,198 internet-downloaded faces; no source-image licence stated. |
| dlib ResNet recognizer, `dlib_face_recognition_resnet_model_v1.dat` | Same public-domain statement. | SHA-256 `55533b28a95800a551ba546ba62fe69625c7e95a7061c338adffead08719da30`. Trained on ~3M faces; about half from FaceScrub (CC BY-NC-ND 3.0) and VGG Face (CC BY-NC 4.0), the rest internet-scraped. |
| AuraFace-v1, `glintr100.onnx` | Hugging Face repository declares Apache-2.0; the model card says it was "trained on commercially and publicly available data sources to enable its usage in commercial setting". | SHA-256 `a7933ea5330113b01c9b60351d8f4c33003f145d8470ac5f0e52ee2effe25c60` (matches the hash published on the Hugging Face file page). The card does not name the dataset or give per-source licences; a [provenance request](https://huggingface.co/fal/AuraFace-v1/discussions/8) to fal was unanswered as of 2026-10-05. Reported LFW 0.99650, CFP-FP 0.95186. |
| MediaPipe BlazeFace, `blaze_face_full_range.tflite` | Model card declares Apache-2.0. | SHA-256 `3698b18f063835bc609069ef052228fbe86d9c9a6dc8dcb7c7c2d69aed2b181b`. Both model cards list identity recognition and faces looking away as out of scope. |

The dlib stack's public-domain weight grant is the clearest artifact permission
found in this search, but the training corpora include sources whose published
terms are non-commercial (FaceScrub CC BY-NC-ND 3.0, VGG Face CC BY-NC 4.0,
AFLW non-commercial research, WIDER FACE CC BY-NC-ND, ImageNet
non-commercial). Whether those dataset terms govern the resulting weights is a
legal question the sources do not settle; on the available evidence the dlib
stack is not commercially cleared. AuraFace's Apache-2.0 grant plus a
commercial-intent claim is a stronger license signal, but "commercially and
publicly available data sources" without a named dataset or per-source terms
does not document the training-data provenance the public edition requires.

## Side-by-side measurement

Same fixture and method as #1676: the private 83-frame, real-footage set (two
people, seven source clips, profile and low-light cases), resized to the
production width cap of 1408 pixels (1408x792 inputs). No video or still was
copied into this repository. Labels follow the existing file-level evaluation
(LC49xx = one person, LCATL00xx = the other). A frame with anything other than
exactly one detection at the operating point is excluded and named. The
`buffalo_l` and YuNet+SFace controls reproduce #1676's numbers exactly, which
validates the harness.

| Measure | `buffalo_l` (control) | dlib MMOD+5pt+ResNet | YuNet0.8 + AuraFace | YuNet0.8 + SFace (#1676) |
|---|---:|---:|---:|---:|
| Frames with one usable face | 83 / 83 | 80 / 83 | 80 / 83 | 80 / 83 |
| Same-person / different-person pairs | 1701 / 1702 | 1569 / 1591 | 1569 / 1591 | 1569 / 1591 |
| Closest different-person cosine | 0.1333 | 0.9217 | 0.1310 | 0.2605 |
| Weakest same-person cosine | 0.3015 | 0.8579 | 0.1596 | 0.3267 |
| Zero-error threshold interval | `0.1333 < t <= 0.3015` | none (overlap) | `0.1310 < t <= 0.1596` | `0.2605 < t <= 0.3267` |
| FAR / FRR at `t = 0.25` | 0 / 0 | 1591 / 0 | 0 / 33 | 1 / 0 |
| FAR / FRR at `t = 0.30` | 0 / 0 | 1591 / 0 | 0 / 39 | 0 / 0 |

**dlib fails the quality bar outright.** Its embeddings do not separate the two
people: the closest different-person pair (0.9217) is more similar than the
weakest same-person pair (0.8579), and every pairwise cosine on a 12-frame
spot check was above 0.95 regardless of whether the pair was the same or a
different person. The model maps all of these faces into a narrow cone of the
embedding space. This is a genuine model limitation, not a preprocessing
artifact: the stack uses dlib's own MMOD detector, 5-point shape predictor and
ResNet descriptor on RGB input, exactly as dlib's example prescribes. dlib's
ResNet is a 2017 LFW-era model; on controlled frontal faces it reaches ~99.3%,
but it does not generalize to Ren's cross-camera, profile and low-light
footage. The MMOD detector also misses the three full-profile frames
(`LCATL0011_t211/212/213`), the same frames YuNet misses.

**AuraFace is the best new candidate but does not clear the bar.** It separates
the two people on the 80 non-profile frames (closest different-person 0.1310,
weaker than SFace's 0.2605), but its same-person consistency is poor: the
weakest same-person pair is 0.1596, so the zero-error interval is only 0.029
wide and a 0.25 threshold produces 33 false rejects. Like every YuNet-based
stack it misses the three full-profile frames. AuraFace's margin is roughly a
fifth of `buffalo_l`'s 0.168 on the same frames, and it does not cover the
profile cases at all.

The 0.25/0.30 results are diagnostic thresholds chosen from this same small
fixture, not an independent validation. As in #1676, the profile frames are the
binding constraint: no candidate detector finds them, and forcing detections
onto them (the #1676 diagnostic) collapses the margin for the embedders tried
so far.

## BlazeFace runtime note

BlazeFace was not measured. The official `blaze_face_full_range.tflite` from
`storage.googleapis.com/mediapipe-models` cannot be run on this machine:

- mediapipe 1.0.x (the first release whose FaceDetector graph accepts this
  asset) crashes on macOS arm64 in `TensorsToDetectionsCalculator::Open` with
  `DrishtiMetalHelper ... service_ Service is unavailable`, on both CPU and GPU
  delegates. This is a known mediapipe 1.0.x bug on macOS arm64, not a local
  configuration problem.
- mediapipe 0.10.x runs on this machine but rejects the asset: the model's
  output tensor layout does not match the 0.10.x decoder
  (`raw_box_tensor->shape().dims[1])==(num_boxes_)`).
- `tflite-runtime` has no macOS wheels, so a hand-rolled decoder was not
  available either.

This is a measurement gap, not a finding that BlazeFace detects or misses the
profile frames. The model cards already state that faces looking away from the
camera and identity recognition are out of scope, which is a use-case blocker
for Ren's speaker-identity task on its own; a measurement would not change
that determination.

## Remaining gap

No candidate tested across #1676 and this follow-up meets both requirements
for the public edition: face-identity quality on Ren's hard frames and a clear
commercial-use grant with documented training-data provenance. The two bars
now have named owners:

- **Quality:** `buffalo_l` is the only stack that covers all 83 frames with a
  wide zero-error margin. Every public candidate misses the three full-profile
  frames; dlib additionally does not separate the two people at all, and
  AuraFace/SFace separate them only with a narrow margin.
- **Provenance:** AuraFace is the strongest license signal (Apache-2.0 plus a
  commercial-intent claim) but its training dataset is unnamed and the
  provenance request is unanswered. dlib's public-domain weights carry
  non-commercial training sources. SFace's training corpus is undocumented.

A future candidate needs an explicit commercial-use basis and traceable
training-data terms, then must retain profile coverage and produce a useful
zero-error margin on this fixture plus an independent, synchronized
speaker/video evaluation. Until then, the public face-identity backend remains
unselected.
