# OCR benchmark (GEO-013..027)

Real-footage text benchmark: EasyOCR (incumbent) vs Apple Vision OCR
(incumbent, OS-provided) vs PaddleOCR-VL-1.6 (candidate, transformers
route) vs PaddleOCR-VL-1.6 MLX (candidate, fidelity + throughput).

## Corpus (GEO-013, GEO-014)

Builder: `library/tools/ocr_benchmark_corpus.py`. Samples frames from the
two bounded sources only (`PODCAST_FOOTAGE_ROOT`, `APPLE_001_ROOT`;
defaults in the module) - 96 frames, 16 clips x 4-6 samples, split BY
CLIP (never by frame): 64 calibration, 32 held-out. Settings are chosen
on calibration; held-out is reported once.

Categories (GEO-014): `lower_third caption slide chart label
computer_interface scene_text`. Real frames carry `category_hints: []`
and empty `labels` until agent labelling lands; see the label request at
`data/vep-mu-ocr/label-request.md` (firstmate home, not this repo):
2 blind agent labellers on different families + third tie-breaker,
agreement recorded, reported as agent labels per standing rule (1).

`synthetic-corpus.json` (from `library/tools/ocr_synthetic_fixtures.py`)
is the always-runnable half: 6 rendered fixtures with exact-by-construction
labels covering numbers/currency/units/dates (GEO-020), a two-column table
(GEO-021) and a lower-third band (GEO-014). Real-footage agent labels are
still pending; synthetic numbers below are measured, real-footage
predictions are stored but unscored until labels land.

## Runners and scoring

- EasyOCR incumbent: `library/tools/ocr_easyocr_baseline.py`
  (`download_enabled=False`; weights are environment setup, never fetched
  by a run). Emits `ren_ocr_candidate_run/1` + timing split
  (cold/warm, decode/infer/serialize) and peak RSS.
- Apple Vision: `apple_vision_ocr.swift` (compiled, no download) +
  `library/tools/ocr_apple_convert.py`.
- PaddleOCR-VL-1.6 transformers route: `ocr_paddle_candidate.py`
  (transformers>=5, `trust_remote_code=True`, "Spotting:" prompt).
- PaddleOCR-VL-1.6 MLX: `ocr_mlx_candidate.py` against a pinned
  `mlx-community` conversion where one exists for the benchmarked
  version; otherwise a local `mlx-vlm convert` attempt is recorded with
  its outcome (no silent substitution).
- Scoring: `ocr_eval.py` - exact-text accuracy + CER at frame level
  (GEO-018), box IoU>=0.5 detection P/R/F1 + mean matched IoU (GEO-019),
  numeric-exact (document + box-matched, GEO-020), structural layout
  P/R/F1 over left-of/above/inside relations - geometric only, never
  semantic (GEO-021, GEO-026). Text-only models score frame/numeric
  without claiming box accuracy. `check_separation` REFUSES any
  prediction carrying meaning fields (GEO-026).

## Temporal intelligence (GEO-022, GEO-023, GEO-024)

`library/tools/analysis/ocr_temporal.py`: `detect_changes`
(appeared/disappeared/changed/moved between consecutive samples),
`link_detections` (text tracks across frames, case-insensitive text +
IoU join), `region_changed_fraction` + `frame_changed` (cheap
frame-difference signal; the single run/skip predicate) and `plan_skips`
(saved-pass bookkeeping). The per-frame recognizer stays the authority
on WHAT the text says; this module decides WHEN to run it. Step 1.07's
`OCRExtractor` keeps its own tracking; this module is the benchmarked,
shared statement of the policy.

## Storage and queries (GEO-025, GEO-026, GEO-027)

- GEO-025: step 1.07 artifacts (`ocr_result.json`, schema 2, tracked
  text with appearance intervals + normalized boxes) are the spatial
  artifact authority; `library/tools/ocr_project_model.py` migrates them
  into Project Model entities/observations/tracks/masks with source
  intervals, sample ticks and artifact refs; `footage_query` indexes
  them (`ocr_texts` per clip + `ocr_methods` naming the recognizer) and
  `ren search --ocr` answers from them.
- GEO-026: recognized strings and meaning never share a row - eval
  refuses meaning fields; layout relations are structural; Project Model
  observations carry `certainty: measured` with raw-score confidences.
- GEO-027: `FootageIndex.combined_search(said, shown)` joins speech
  segments against tracked on-screen text on source-clock overlap via
  `library/tools/analysis/ocr_combined_query.py`, citing both
  provenances. Unit-tested in
  `tests/unit/picture/test_ocr_combined_search.py`.

## Candidates and adoption (EVAL-032)

Scorecards aggregate frozen `ren_evaluation_report/1` rows per candidate
(`evaluation_scorecard.py`; no blending, held-out report-only). Each
candidate ships an adoption decision: adopt / retain baseline /
research-only / conditional fallback / reject.

The PaddleOCR-VL MLX text pass is wired into the 1.07 capability
(`library/capabilities/ocr/extract.py`, `ocr_backend` param or
`OCR_BACKEND` env, default stays EasyOCR): same `ocr_result.json`
schema plus `method`/`precision`/`model_revision` (GEO-025 provenance),
text-only tracks flagged `spatially_grounded: false`. They stay
searchable through the artifact and `ren search --ocr` / combined
search; the Project Model migration skips them for geometry (a
full-frame box would claim an unmeasured provenance) with a recorded
note. Cache reuse is backend-aware: an EasyOCR artifact never
satisfies an MLX request.

| Candidate | Precision shipped | License | Decision |
|---|---|---|---|
| EasyOCR (incumbent) | fp32 CPU/MPS per torch build | Apache-2.0 (reader + det/rec weights) | **retain baseline (default)**: best detection F1 (0.444) pending held-out; MLX wins text, Apple wins speed |
| Apple Vision OCR | OS framework default | OS-provided, no redistribution concern | **retain (available route)**: fastest (0.044s warm), best boxes (0.705 IoU); text below MLX |
| PaddleOCR-VL-1.6 (transformers) | incompatible: native 5.19 rejects rope layout; remote code needs ROPE_INIT_FUNCTIONS['default'] absent through 5.20.0 | Apache-2.0 publisher card | **reject** for this checkpoint (evidence in runner docstring); MLX covers the same weights |
| PaddleOCR-VL-1.6 MLX (bf16, local convert) | bfloat16 MLX, peak 2.69GB cal / 0.42GB synth | Apache-2.0 publisher card; license-lane confirmation pending | **adopt as alternate backend** (wired behind 1.07, default unchanged): wins calibration text (0.781 exact, 0.193 CER); text-only, no boxes |

Downloads (delete after benchmark unless adopted): PaddlePaddle/
PaddleOCR-VL-1.6 @ `676e2aa...` (1,917,255,968 B safetensors) in the
shared HF cache; local MLX bf16 conversion at
`/tmp/ocr-mlx-paddleocr-vl-1.6-bf16` (1.8GB, shortlisted - kept);
isolated paddle venv at `/tmp/ocr-paddle-env` (dropped route's runtime).

## Status

- Synthetic (measured, exact labels, frozen in `scorecard-synthetic.json`):
  EasyOCR frame-exact 0.83, CER 0.005, det F1 1.0, mean IoU 0.73,
  cold 15.2s, warm 2.5s, 477MB peak. Apple Vision frame-exact 0.83,
  CER 0.0, det F1 1.0, mean IoU 0.86, cold 3.2s, warm 1.0s (memory
  unmeasured - OS framework). PaddleOCR-VL-1.6 MLX bf16 frame-exact
  1.0, CER 0.0, numeric-exact 1.0, cold 24.5s, warm 7.0s, 422MB peak;
  text-only (no boxes, layout F1 0.0 - the "OCR:" prompt returns no
  geometry; boxed output is the pipeline route's job).
- PaddleOCR-VL-1.6 MLX conversion: local `mlx-vlm convert` of the pinned
  checkpoint (676e2aa, Apache-2.0) loads and runs (mlx-vlm 0.7.2,
  `paddleocr_vl` arch supported). Converter bug: `processor.save_pretrained`
  fails overwriting the read-only copied `tokenizer.json` (Permission
  denied) - worked around with chmod; weights (1.8GB bf16) are complete
  and load. 8-bit quantize converts (1.0GB) but the weights do NOT load
  (`load_weights` strict mismatch on quant scales/biases) - quality gap
  unmeasurable this way; 8-bit copy deleted, log kept.
  Scheduler rerun (2026-10-16): synthetic 1.0 exact, calibration 64/64
  text-identical to the first pass; cold 0.9-24.5s, warm mean 2.0s
  synthetic / real, peak RSS 0.42GB synthetic-only and 2.69GB over full
  calibration (under the 4GB bar).
- The official paddle pipeline route is DROPPED from the benchmark:
  init alone peaks at 9.48GB RSS (PP-DocLayoutV3 + VLM before the first
  image; construction flags skip the layout model but the VLM alone
  still exceeds 4GB), there is no batch-size knob (rejected) and no
  fp16 path on CPU. Same weights via MLX peak at 2.69GB with identical
  quality - the pipeline route measures runtime bloat, not recognition,
  so it is out. Probe evidence in 007: `/tmp/ocr-mem-p{0,1,2}.json`.
  `ocr_paddle_pipeline.py` stays as the attempt record, not a runner.
- Transformers route for PaddleOCR-VL-1.6: native 5.19 registration rejects
  the checkpoint's vision rope layout; the checkpoint's bundled remote
  modeling needs `ROPE_INIT_FUNCTIONS['default']`, absent through
  transformers 5.20.0. Official paddle pipeline (isolated venv,
  paddlepaddle 3.3.1 + paddleocr 3.7.0, macOS arm64) runs but costs
  ~78s/frame CPU - prohibitive for full calibration; resumable runner
  (`--done-file`) stands by for quiet-window completion.
- Real footage: 96 frames extracted; EasyOCR (30 boxes/64 cal), Apple
  Vision (19/64) and PaddleOCR-VL MLX bf16 (7 tracks/64) calibration
  predictions stored, unscored pending agent labels.
- Real footage, agent labels round 1 (frozen in
  `scorecard-real-calibration.json`, labels in
  `corpus-real-calibration.json`, method in `agent-labels-round1.md`):
  PaddleOCR-VL-1.6 MLX bf16 frame-exact 0.781, CER 0.193, warm 1.99s,
  peak 2.69GB. Apple Vision 0.766 / 0.226, det F1 0.372, mean IoU 0.705,
  warm 0.044s. EasyOCR 0.734 / 0.252, det F1 0.444, mean IoU 0.608,
  warm 0.515s, peak 1.06GB.
- Labels: round 1 complete (A + B + third-family tie-break, 24 rows).
  Held-out 32 untouched until a held-out run is declared.
