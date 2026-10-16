# Reusable video features: V-JEPA 2.1 ViT-B, PE-Core-S vs incumbents (2026-10-16)

TMP-033..041 on calibration footage only (9 motion + 11 static agent-labeled
windows over Apple 001 takes, 12 unlabeled podcast windows for cost and
correlation). No held-out footage was touched; held-out labels do not exist
yet (corpus manifest marks tuning access false). Scorecard:
[`2026-10-16-video-features-scorecard.json`](2026-10-16-video-features-scorecard.json),
built with `library/tools/evaluation_scorecard.py` from the 14 frozen
`ren_evaluation_report/1` files beside it. Fixture:
[`2026-10-16-mu33-calibration-windows.json`](2026-10-16-mu33-calibration-windows.json).
Bench code: `docs/benchmarks/mu33_video_features_bench.py`,
`mu33_video_features_windows.py`. Adapter: `library/tools/video_features/`.

## Headline numbers (calibration)

| Measure | V-JEPA 2.1 ViT-B | Incumbent | PE-Core-S | CLIP (incumbent) |
|---|---|---|---|---|
| Motion vs static AUC (n=20) | 0.82 (d_half) / 0.74 (energy p90) | 0.71 Farneback peak | - | - |
| Action-ranking AP / P@5 / P@10 | 0.63 / 0.60 / 0.70 | 0.68 / 0.60 / 0.60 | - | - |
| Motion agreement w/ Farneback (Spearman, n=32) | 0.80 (energy) / 0.66 (d_half) | - | - | - |
| Temporal correspondence margin (11 windows) | 0.048 top-1, 0.074 trace | - | - | - |
| Spatial correspondence margin (11 windows) | - | - | 0.014 top-1, 0.078 trace (layer 7) | - |
| Same-take similarity AUC | - | - | 0.90 | 0.999 |
| Warm inference | 2.8 s/window (16f @384px) | 7.9-23.3 s/window (10 Hz Farneback) | 0.07 s/frame | 0.43 s/frame |
| Process peak RSS | ~1.0 GB | 0.11 GB | 0.95 GB | 0.42 GB |
| Precision | fp32 | - | fp32 | fp32 |

Farneback wall time varied 10.8 s to 31.8 s per window between the two runs
(shared-machine contention; inference ratio direction held both times).
Small-sample warning: separation/ranking use 9 motion + 11 static windows,
so AUC differences of ~0.1 are not decisive - reported as measured, with the
sample size in every unit.

## Per-item verdicts

- **TMP-033 (benchmark V-JEPA 2.1 ViT-B): done.** Official publisher
  checkpoint `vjepa2_1_vitb_dist_vitG_384.pt` (86,833,152 params, ema_encoder,
  zero missing/unexpected keys against the publisher's `vit_base` recipe).
  Cold load 12.0 s, warm forward ~2.8 s per 16-frame 384px window on MPS,
  ~1 GB peak RSS. Full precision (80 M params, under the ~1.5 B line).
- **TMP-034 (motion/state-change sensitivity): measured, comparable.**
  V-JEPA half-clip distance AUC 0.82 and patch-energy AUC 0.74 vs Farneback
  0.71 on the same 20 windows. Real signal, no decisive win at n=20.
- **TMP-035 (spatial/temporal correspondence): positive.** Adjacent-slice
  top-1 match 0.873 vs first-last 0.825 (margin 0.048); same-position trace
  margin 0.074 against a 0.081 trace-vs-shuffle control. Nearby frames
  measurably correspond more than distant ones.
- **TMP-036 (compare with existing motion): strong agreement.** Spearman
  0.80 between V-JEPA patch-energy and Farneback peak over all 32 windows:
  the features see the same motion the incumbent measures, including whole-
  frame vehicle motion (IMG_1809/1810/1811) that is camera movement, not
  subject movement. Features do not separate the two either.
- **TMP-037 (improve action-candidate selection): not improved.**
  AP 0.63 vs incumbent 0.68; P@10 0.70 vs 0.60. A wash on 9 positives -
  no evidence the features improve selection.
- **TMP-038 (DINOv3): not benchmarked - gated.** DINOv3 weights are
  HF-gated (manual approval: name, date of birth, country, affiliation,
  license click-through, no token on this machine). Only a 16 KB metadata
  blob is cached; weights were never downloaded. PE-Core-S served as the
  available spatial-correspondence reference instead (Apache 2.0, ungated):
  real correspondence (trace margin 0.078 at layer 7, 0.045 final), but
  same-take similarity AUC 0.90 trails CLIP's 0.999. Needs gated access to
  close TMP-038 (vep-mu-features-dinov3-gate).
- **TMP-039 (reusable representations): none adoptable on this evidence.**
  V-JEPA shows motion + correspondence signal but beats no incumbent at a
  downstream task; PE-Core-S trails CLIP at similarity. No representation
  here supports multiple downstream tasks better than what Ren already runs.
- **TMP-040 (no expensive embeddings without benefit): enforced.** V-JEPA
  dense tokens cost ~14 MB fp32 per 16-frame window (4608 x 768); PE 512-d
  is 2 KB/frame; CLIP 768-d is 3 KB/frame. With no measured downstream win,
  the finding is to store nothing new: dense tokens stay recompute-on-
  demand through the adapter, never a persisted index.
- **TMP-041 (incrementally rebuildable): done by construction.**
  `library/tools/video_features/cache.py` keys each interval by source
  identity, model, precision and adapter version; `missing_keys` rebuilds
  only absent intervals. Unit-tested (store/load round-trip, corrupt entry
  refusal, scoped keys).

## Adoption decisions (EVAL-032)

- V-JEPA 2.1 ViT-B: **retain baseline** (Farneback regional motion stays the
  motion signal; CLIP stays the similarity signal). Features are
  **research-only**: available through `library/tools/video_features/`
  for on-demand analysis, no default change, no registry change, no stored
  embeddings. Rationale: ARCH-005 requires a measured improvement for a
  default change; none measured (TMP-037 wash, similarity not attempted
  against CLIP for V-JEPA since it is a video-motion encoder, not a
  text-aligned retriever).
- PE-Core-S16-384: **retain baseline** (CLIP). Research-only via the same
  adapter. Rationale: similarity AUC 0.90 vs 0.999; correspondence real but
  with no downstream task that pays for a second embedding tower.
- DINOv3 ViT-B: **deferred** pending gated access; no decision without data.

No capability interface changed; no incumbent removed (ARCH-004/013).

## License classes (publisher evidence; vep-mu-license owns final word)

- V-JEPA 2.1: repo MIT; Meta's release blog states code and checkpoints are
  shared "for commercial and research applications" - recorded as
  commercial-permitting pending license-lane verification.
- PE-Core-S16-384: Apache 2.0 (`facebook/PE-Core-S16-384@aabf3b99...`),
  commercial candidate.
- DINOv3: custom DINOv3 License + manual gate - unverified for any use here.
- CLIP ViT-L/14: MIT, incumbent, unchanged.

## Download record (all under approval vep-mu-downloads; /tmp only)

| Model | Revision | Size | Path | Disposition |
|---|---|---|---|---|
| vjepa2_1_vitb_dist_vitG_384.pt | dl.fbaipublicfiles.com, sha256 848a77c3... | 1,664,223,428 B | /tmp/vep-mu-features-models/ | deleted after benchmark (not adopted) |
| PE-Core-S16-384.pt | HF aabf3b99..., sha256 ccc8340a... | 348,852,712 B | /tmp/vep-mu-features-models/ | deleted after benchmark (not adopted) |
| publisher repos (vjepa2@204698b, perception_models@3e352cc) | git HEADs | - | /tmp/vep-mu-features-models/ | deleted (never vendored) |
| timm 1.0.30 (isolated target, shared venv untouched) | PyPI | - | /tmp/vep-mu-features-deps/ | deleted |

The shared HF cache retains only what `hf_hub_download` placed there
(PE-Core-S config card/README); no weights were added to the repo.

## Limitations

Small-sample agent labels (9 motion windows); static windows are
"no agent-labeled action", not certified still; podcast windows unlabeled;
Farneback ran without face exclusion (face_presence=None) where the
production path excludes face regions; V-JEPA was run at 16 uniform frames
per window, not the publisher's 64-frame recipe; DINOv3 unmeasured.
