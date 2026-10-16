# VLM round-2 blind-label agreement - 2026-10-16

Scope: SEM-009, SEM-010, SEM-011 label foundation. Two independent blind
agent labellers on different model families over the calibration blind-label
packet (`blind-packet.md` in the lane data dir): labeller-a (openai) vs
labeller-b (opencode muse-spark). Eight windows: IMG_1808 [0,10)/[10,20),
IMG_1814 [0,10)/[10,20), LCATL0012 [60,70)/[300,310), LC4931 [60,70)/[300,310).
Reported as agent labels, never human or gold labels.

## Matching rule v1 (stated before scoring)

- Windows pair by (media basename, start, end).
- Scene: each side's type maps through a fixed map
  (vehicle-cabin / outdoor-plaza / indoor-studio / unknown); agreement iff
  the canonical classes are equal. Free-text settings are never scored.
- Objects and actions: token-overlap similarity >= 0.4 AND temporal
  IoU >= 0.5, greedy one-to-one by similarity * IoU. Matches are agreed
  with consensus span = mean of boundaries; unmatched rows are disputes.
- Rows labelled `unknown` are abstentions, not claims. static-vs-unknown
  camera is an abstention dispute: excluded from the agreed set but not a
  conflict.

## Result

| Field | Agreed | Of | Notes |
|---|---|---|---|
| scene | 8 | 8 | all windows incl. both unobservable [300,310) |
| object | 8 | 34 | podcast windows 5/5; Apple windows 3/29 |
| action | 2 | 13 | +2 abstained `unknown` rows on the [300,310) windows |
| camera | 6 | 10 | 4 static-vs-unknown abstentions on IMG_1808, no true conflicts |

Consensus: `2026-10-16-vlm-round2-consensus.json` (10 agreed object/action
rows, evaluator-only row shape). Generator: `vlm_round2_agreement.py`.

The Apple-window object disagreement is mostly granularity, not
contradiction: one side writes `young man`, the other
`young_man_in_light_shirt_and_gray_cap` (similarity 0.25 < 0.4), and
`pergola structure` vs `THE_WORKS_steel_and_wood_entry_structure`
(similarity 0.125). The rule counts these as disputes honestly; the
tiebreaker resolves the coreference. Substantive disputes that survive any
rule: LC4931 [60,70) talking-with-gestures vs listening-shifting-hands,
IMG_1808 cap span [0,10) vs [7,10), IMG_1808 [10,20) seatbelt reach vs
posture shift, IMG_1814 standing [0,3) present vs absent.

## Verdict impact

37 disputed items over 6 observable windows against 10 agreed rows: the
disputes can flip candidate precision/recall points, so they could change
SEM-005..008 adoption verdicts. The neutral tiebreak packet (windows +
questions only, no answers, no Ren outputs) went to firstmate at
`data/vep-mu-vlm/tiebreak-request.md`; the third-family tiebreak has since
landed (see below), so scoring uses the 21-row consensus and no verdict
waits on labels.
SEM-008 (Molmo2-4B) is separately deferred: the license lane records it
unresolved over third-party training-dataset terms, so no weights are
downloaded for it.

## Tiebreak disposition (third family: anthropic-claude Sonnet 5.5)

Adjudication rule: a disputed claim joins the consensus iff a tiebreak row
matches it under the same rule (similarity >= 0.4, IoU >= 0.5); consensus
span = mean of labeller and tiebreaker boundaries. Tiebreak-only rows
(single-labeller support) are recorded in the consensus file, never scored.

- confirmed labeller-a claims: 8 (cap full-window spans, seatbelt, young
  man IMG_1808 [10,20), reclining, IMG_1814 man [0,10), walking-away
  IMG_1814 [10,20), cap adjustment action).
- confirmed labeller-b claims: 3 (mirror-device naming x2, potted plant).
- unresolved: 26, incl. the substantive LC4931 [60,70) talking-vs-listening
  split: the tiebreak finds looking-off-camera [60,64.5) then talking
  [65,70), matching neither side under the rule, so neither claim scores.
- tiebreak-only: 29 rows recorded, not scored.
- consensus total: 21 rows (10 agreed + 11 tiebreak-confirmed).

Generator change: `vlm_round2_agreement.py --tiebreak labels.jsonl`.
