# Agent labels round 1 - OCR calibration (GEO-013..021)

No human labels, no reference edits: two independent blind agent
labellers on different model families plus a third-family tie-breaker,
per the standing captain rule. Reported as agent labels.

- Labeller A (`data/vep-labels-r1-a/round5/vep-mu-ocr/labels.json`):
  64 frames, 22 rows, 46 empty. Transcribed exact strings with
  eye-estimated boxes; legible-only threshold.
- Labeller B (`data/vep-labels-r1-b/round5/vep-mu-ocr/labeller-b.json`,
  method in `method-note-b.md`): 64 frames, 17 rows, 57 empty.
  Uniform >= ~15px threshold; dial numerals, map micro-labels and
  equipment brand marks omitted; cropped strings as shown.
- Tie-breaker C, third family
  (`data/vep-tiebreak-r2/work/ocr_c.json`, annotator recorded inside):
  resolved the 23 disagreeing frames (24 rows).

Agreement (measured, `ocr_eval.normalize_text` + box IoU>=0.5):

- 41/64 frames in full agreement (all empty on both sides).
- 23/64 frames to tie-break (presence/threshold splits such as SONY
  lettering and SW chips, transcription splits on cropped door vinyl,
  box-placement splits under IoU 0.5).
- Matched-pair text-exact rate 0.667; mean matched IoU 0.589.
- Final calibration labels: 24 rows over 23 frames, 41 frames empty
  (frozen in `corpus-real-calibration.json`).

Calibration scores against these labels (frozen in
`scorecard-real-calibration.json`):

| Candidate | frame-exact | CER | det F1 | mean IoU | warm | peak |
|---|---|---|---|---|---|---|
| PaddleOCR-VL-1.6 MLX bf16 | 0.781 | 0.193 | n/a (text-only) | n/a | 1.99s | 2.69GB |
| Apple Vision OCR | 0.766 | 0.226 | 0.372 | 0.705 | 0.044s | unmeasured (OS) |
| EasyOCR (incumbent) | 0.734 | 0.252 | 0.444 | 0.608 | 0.515s | 1.06GB |
