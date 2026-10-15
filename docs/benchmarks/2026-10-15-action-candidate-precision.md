# Real podcast action-candidate precision audit (2026-10-15)

## Finding

The FI-01 “false positives” are M7 hand-near-mouth proposals rejected by M8,
not false positives against independent human action labels. The EVAL-01
manifest marks action annotation `not_started` and held-out label payloads
`not_created`. Its held-out precision, recall and boundary accuracy therefore
cannot be measured from this corpus yet.

An audit of 60 M8 `no` explanations found the dominant reported cause was a
near-face gesture that did not perform the queried contact or mouth-cover
action. Fifty-nine explanations fit `gesture-not-action`; one only described
hands on a table and was too unspecific to assign. The remaining buckets had
zero explicit examples in this sample: camera motion, occlusion, detector
noise, and label gaps. Those zeroes describe what M8 said, not proof those
causes never occur.

The sample is reproducible: take the first ten `no` verdicts per source after
sorting by SHA-256 of `clip_id + verdict_id`, among verdicts whose sampled
frames overlap an M7 `hand_near_mouth` span. Sources: LCATL0013, LC4932,
LCATL0014, LC4931, LC4930 and LCATL0012. The reason text was assigned one
primary category. A cached M2 still from one sampled rejection per source was
also inspected; the visible poses corroborated the gesture explanations. No
model inference or source re-decode was run for this audit.

## Cheap pre-verifier filter

M7 already records hand-to-lips distance in face widths and assigns tier 0 to
the measured strong band at `<= 1.25`, with tier 1 for the marginal band up to
the existing 1.5 proposal limit. `ren search --verify` now sends tier 0 by
default. `--max-tier 1` includes the marginal band. Older M7 sidecars without
a tier field derive the same tier from their recorded frame distances at read
time; source memory is not rewritten. Other candidate predicates keep their
existing all-tier default because this calibration applies only to
`hand_near_mouth`.

The cutoff was assessed on the EVAL-01 calibration sources only:

| Calibration source | M7 candidates before | Candidates at `<= 1.25` | Stored M8 yes retained |
|---|---:|---:|---:|
| LCATL0012 | 13 | 7 | 0/0 |
| LC4931 | 33 | 14 | 2/2 |
| **Total** | **46** | **21** | **2/2** |

These are recorded M8 decisions used only as a tuning proxy. The 1.25 cutoff
reduces the candidate set by 54% on calibration footage while retaining both
M8 `yes` decisions; this is not human-labeled recall.

## Held-out comparison

LC4930 is the only held-out source in FI-01 with measured action candidates and
M8 verdicts. Applying the calibration cutoff to its stored M7 distances gives:

| Metric | Before | After `<= 1.25` | Reference |
|---|---:|---:|---|
| Candidate spans / potential verifier calls | 26 | 6 | One call per candidate for this query |
| M8 agreement precision | 2/26 = 0.077 | 2/6 = 0.333 | Stored verifier decisions, not human truth |
| M8-positive retention | 2/2 = 1.00 | 2/2 = 1.00 | A verifier-positive retention proxy, not event recall |
| Mean boundary error | 0.38 s | 0.38 s | FI-01's boundary measure over the same two M8 `yes` spans |

No held-out inference or new VLM calls were run. The 6-call after count is the
number of candidates the default filter would submit; it is not a newly
recorded runtime call count. LCATL0011 remains unmeasured in FI-01. Independent
held-out precision, recall, boundary error and call cost remain unavailable
until EVAL-01 action labels are authored and released.

## Source records

- [FI-01 baseline report](2026-10-12-fi01-footage-intelligence-baseline.md)
- [EVAL-01 corpus manifest](2026-10-15-editorial-eval-corpus.json)
- `library/tools/event_spans.py` (`HAND_MOUTH_TIER0_DISTANCE` and
  `verified_query`)
