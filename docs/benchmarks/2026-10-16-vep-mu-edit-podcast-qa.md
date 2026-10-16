# vep-mu-edit podcast Q/A calibration (EDIT-005/006, real words)

Real Voz words on the calibration pair LCATL0012 + LC4931 (0-150 s each, incumbent `da voz --json`, cached weights, no downloads). Speaker attribution by file identity (basis stated); measured diarization turns are the open EDIT-010 gap. Reference comparison reuses vep-agent-refedit-b agent labels (calibration pair only).

| measurement | value | basis |
|---|---|---|
| voz_words_lc12 | 407 | da voz --json, LCATL0012 0-150s |
| voz_words_lc31 | 409 | da voz --json, LC4931 0-150s |
| phrases_lc12 | 55 | phrase_spans over real Voz words |
| pause_cuts_lc12 | 69 | pause_center_cuts over real Voz words |
| phrases_lc31 | 59 | phrase_spans over real Voz words |
| pause_cuts_lc31 | 59 | pause_center_cuts over real Voz words |
| turns_lc12 | 50 | Voz sentence spans as turns |
| turns_lc31 | 54 | Voz sentence spans as turns |
| qa_units_found | 12 | question_answer_units across the synchronized pair |
| qa_unit_0_question | [0.6699999999999999, 6.640000000000001] | found unit question span (LCATL0012 clock) |
| qa_unit_0_answer | [6.96, 8.88] | found unit answer span (LC4931 clock) |
| qa_unit_1_question | [14.0, 14.95] | found unit question span (LCATL0012 clock) |
| qa_unit_1_answer | [15.75, 29.9699375] | found unit answer span (LC4931 clock) |
| qa_unit_2_question | [16.07, 29.03] | found unit question span (LCATL0012 clock) |
| qa_unit_2_answer | [30.08, 31.2] | found unit answer span (LC4931 clock) |
| qa_unit_3_question | [31.28, 32.08] | found unit question span (LCATL0012 clock) |
| qa_unit_3_answer | [32.42, 34.5799375] | found unit answer span (LC4931 clock) |
| qa_unit_4_question | [32.42, 34.5799375] | found unit question span (LCATL0012 clock) |
| qa_unit_4_answer | [34.64, 36.31] | found unit answer span (LC4931 clock) |
| qa_unit_5_question | [58.059999999999995, 64.94] | found unit question span (LCATL0012 clock) |
| qa_unit_5_answer | [65.5, 66.12993750000001] | found unit answer span (LC4931 clock) |
| qa_unit_6_question | [58.300000000000004, 65.4899375] | found unit question span (LCATL0012 clock) |
| qa_unit_6_answer | [65.82, 67.48] | found unit answer span (LC4931 clock) |
| qa_unit_7_question | [113.50999999999999, 114.47] | found unit question span (LCATL0012 clock) |
| qa_unit_7_answer | [116.82000000000001, 118.0999375] | found unit answer span (LC4931 clock) |
| qa_unit_8_question | [117.42, 118.3699375] | found unit question span (LCATL0012 clock) |
| qa_unit_8_answer | [118.5, 120.26] | found unit answer span (LC4931 clock) |
| qa_unit_9_question | [131.43, 132.62993749999998] | found unit question span (LCATL0012 clock) |
| qa_unit_9_answer | [132.93, 133.64993750000002] | found unit answer span (LC4931 clock) |
| qa_unit_10_question | [131.73000000000002, 132.92993750000002] | found unit question span (LCATL0012 clock) |
| qa_unit_10_answer | [135.03, 135.9899375] | found unit answer span (LC4931 clock) |
| qa_unit_11_question | [140.8, 141.38000000000002] | found unit question span (LCATL0012 clock) |
| qa_unit_11_answer | [141.97, 142.64000000000001] | found unit answer span (LC4931 clock) |
| qa_best_mean_iou_vs_refedit_b | 0.139 | mean of question/answer IoU vs refedit-b calibration pair |
| qa_best_question_iou | 0.279 | question span IoU vs LCATL0012 40.24-64.94 |
| qa_best_answer_iou | 0.0 | answer span IoU vs LC4931 79.21-102.63 |
| qa_candidate_strength | strong | build_candidate over the best cross-file unit |
| qa_demo_editing_recall | 1.0 | retrieval-vs-edit split on the found pair |
