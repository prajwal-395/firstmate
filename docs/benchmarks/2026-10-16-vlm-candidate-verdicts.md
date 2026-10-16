# VLM candidate verdicts - 2026-10-16

Scope: SEM-005..008, SEM-012. Five models measured on identical frozen
calibration inputs (9 Apple 001 takes, 0-10 s windows) against blind agent
consensus (evaluator-only consensus plus 21 round-2 rows: 10 agreed +
11 tiebreak-confirmed; unresolved disputes and tiebreak-only rows are not
scored). Machine-readable scorecards:
`2026-10-16-vlm-candidate-scorecards.json`. Reported as agent labels,
never human or gold labels. Held-out stays report-only and unconsumed.

## Headline numbers (warm latency per 5-frame sample; e2e incl. decode)

| Candidate | Cold | Warm | Peak MLX | E2E | Schema | Obj P/R (H/M) | Act P/R (H/M) |
|---|---|---|---|---|---|---|---|
| Gemma 4 12B 4-bit (incumbent) | 15 s | 82 s | 7.99 GB | 87 s | 0.889 | 0.167/0.214 (30/22) | 0.000/0.000 (3/11) |
| Qwen3.5-4B 4-bit | 5.9 s | 55 s | 5.38 GB | 56 s | 0.778 | 0.231/0.214 (20/22) | 0.250/0.182 (6/9) |
| Qwen3.5-4B 8-bit (gap ref) | 9.8 s | 166 s | 7.49 GB | 169 s | 0.778 | 0.308/0.286 (18/20) | 0.250/0.182 (6/9) |
| SmolVLM2-2.2B 4-bit | 4.0 s | 54 s | 3.97 GB | 56 s | 1.000 | 0/0 (9/28) | 0/0 (9/11) |
| SmolVLM2-500M full | 2.6 s | 13 s | 3.21 GB | 15 s | 0.000 | 0/0 (0/28) | 0/0 (0/11) |
| SmolVLM2-256M full | 5.9 s | 27 s | 2.70 GB | 31 s | 0.000 | vacuous | vacuous |

Fenced output (SEM-009): Gemma fenced 5/9, Qwen 7/9 — both need the
fence-stripping adapter the probe now carries; strict-parse rates understate
both. SmolVLM2-2.2B never fences (emits bare schema-shaped placeholders).
Hallucinated timestamps were not separately observed beyond wrong-span
claims; every timestamp stayed inside its window when schema held.

## Decisions (EVAL-032)

- **SEM-005 Qwen3.5-4B: conditional fallback.** Quality at or above the
  incumbent on objects (P 0.231 vs 0.167, equal R) and the only candidate
  with any action recall (0.182 vs 0.000), 1.5x faster warm, 5.4 vs
  8.0 GB peak. Adoptable ONLY as an opt-in fallback behind the existing
  capability interface: the MLX conversion carries no license field, so
  the license lane must clear the artifact first, and held-out must
  confirm. Default unchanged (ARCH-004/005).
- **SEM-006 SmolVLM2-2.2B: reject** for annotation. Schema-perfect,
  content-vacuous: it echoes the prompt's schema placeholders. Kept as
  research-only for a redesigned narrow prompt (no schema example).
- **SEM-007 SmolVLM2-500M/256M: reject** for narrow tasks under this
  prompt (arrays of placeholder rows / template echo). Research-only.
- **SEM-008 Molmo2-4B: deferred.** License lane records it unresolved
  over third-party training-dataset terms; no weights downloaded.

## SEM-012: which tasks benefit from a stronger model

Action localization is where the stronger models separate: only Gemma and
Qwen scored any action hits, and both are weak (R 0.000/0.182) — this is
the task class that most needs stronger reasoning or a dedicated
temporal-grounding model (vep-mu-tgrounding's lane). Object presence and
scene class show no incumbent advantage worth paying 8 GB for, PROVIDED a
lightweight model can be made to follow the response format — which none
tested here did. That is a prompt-design result, not a perception result,
and it is the explicit follow-up: re-test one SmolVLM2 variant with a
narrow no-example prompt before closing SEM-007 entirely.

## SEM-014 (kept separate throughout)

The probe prompt asks for directly visible evidence only and rejects
mood/inference; observation vs interpretation stays typed in the audit,
the policy module never scores taste, and no creative floor was added.

## Downloads (captain approval 2026-10-15, recorded per conditions)

| Model | Revision | Size | Path (deleted after benchmark) |
|---|---|---|---|
| mlx-community/Qwen3.5-4B-4bit | 0e7ffd5c629ef7719d4cbc04069232580bfa9d9c | 2.9 GB | /tmp/vep-mu-vlm-weights/Qwen3.5-4B-4bit |
| smdesai/SmolVLM2-2.2B-Instruct-4bit | 0a4ab666f99095e6c6bd0e1e49aca8822fc46df2 | 1.4 GB | /tmp/vep-mu-vlm-weights/SmolVLM2-2.2B-Instruct-4bit |
| mlx-community/SmolVLM2-500M-Video-Instruct-mlx | fa57db46815177fbdfd65cc85a2b3416a8332268 | 973 MB | /tmp/vep-mu-vlm-weights/SmolVLM2-500M-Video-Instruct-mlx |
| mlx-community/SmolVLM2-256M-Video-Instruct-mlx | 79901655c1a3d7ed6646c91325e3fde47e40e611 | 494 MB | /tmp/vep-mu-vlm-weights/SmolVLM2-256M-Video-Instruct-mlx |

| mlx-community/Qwen3.5-4B-8bit | 1704583a86670ba531b7f2c92530a2f725cfd45b | 4.8 GB | /tmp/vep-mu-vlm-weights/Qwen3.5-4B-8bit |

No model is adopted, so all five weight dirs were deleted after the runs.
Precision benchmarked is the precision Ren would ship (4-bit MLX for 2B+,
full precision for sub-1.5B).

## Quality gap: Qwen 8-bit over 4-bit

Obj P +0.08 (0.308 vs 0.231), R +0.07 (0.286 vs 0.214); actions identical
(0.250/0.182 both); schema/fenced identical. Cost: warm 166 s vs 55 s
(3x), peak 7.49 vs 5.38 GB. The gap is real but small and costs 3x
latency for the same memory class as the incumbent — 4-bit stays the
ship precision for the conditional fallback.

## Caveats

- n=9 calibration takes; confidence bands are wide and the verdicts say
  so. Held-out confirmation is owed before any default changes.
- One transient probe crash (traceback at main, no output) and two
  contention stalls (load avg 93) were observed; the per-sample error
  capture, incremental writes and progress logging added during this lane
  cover reruns. FINISHED-line numbers above are from clean completions.
- The 005.msg memory rule (one model process per lane, model_memory
  profile for 5 GB+ models, record footprints, delete weights) was
  followed throughout: all runs were sequential under
  `semantics.analyse:model_memory`; footprints are in the scorecards.

## Follow-up: narrow prompt and held-out inference

The SEM-007 follow-up used the same nine calibration takes with a no-example
prompt and a 768-token cap. SmolVLM2-2.2B 4-bit returned schema-valid
output on 6/9 takes, valid timestamps on 4/9, and scored 0 object/action
precision and recall against blind agent consensus. Peak MLX memory was
3.97 GB, peak RSS 1.41 GB, warm latency 24.69 s, and end-to-end cost
25.25 s per sample. It remains rejected for semantic annotation. The exact
checkpoint was `smdesai/SmolVLM2-2.2B-Instruct-4bit` revision
`0a4ab666f99095e6c6bd0e1e49aca8822fc46df2` (1,463,072,518 bytes); the
run downloaded it to the temporary path recorded in the scorecard and
removed it when finished.

A single held-out inference report is in
`2026-10-16-vlm-heldout-inference.json`. It contains no labels or reference
metrics. Qwen was run once across the seven held-out Apple 001 takes: JSON
and schema were valid on 6/7, but source-clock timestamps were valid on only
3/7. Gemma raw outputs already existed in an earlier temporary pass and
were reused; that pass mislabeled them as calibration and included
reference-derived metrics. Those metric fields were removed before this
report, leaving only its raw outputs and parse checks; its cold-load and
peak-memory metadata were unavailable. Gemma parsed valid JSON, schema, and
timestamps on 7/7. This is technical structured-output evidence only, not a
claim about perceived content. The held-out result does not confirm Qwen as a
reliable production fallback; it remains explicit opt-in only, and malformed,
empty, abstaining, failed, or mistimed lightweight replies now retry through
the lifecycle-supplied Gemma adapter.

The Qwen weight directory was present from prior calibration work during the
held-out run. It was removed afterwards from
`/tmp/vep-mu-vlm-weights/Qwen3.5-4B-4bit`.
