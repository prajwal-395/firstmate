# F03 - Stop unnecessary repeat verification: before/after measurement

Lane mm-f03-repeat-verify. Acceptance: "Repeated equivalent queries do
not repeatedly pay the same verification cost."

## 1. Where the repeat cost was

The expensive verification is the local-VLM verdict in
`library/tools/span_verification.py` (`verify_spans` / `verify_intervals`,
read by `event_spans.verified_query`, `retrieval/temporal_rerank`,
`project_model_acquisition`, `acquisition_l5_live`, `footage_frames`).
Module-measured cost: 257 calls in 17.3 min (~4.0 s/call) for spans;
13.1-81 s/call for intervals. The verdict store (`verdicts.json` per
content digest) already served exact repeats through ad-hoc frame-times
hashes (`verdict_key` / `interval_verdict_key`), but every NEAR-repeat
re-paid in full:

| # | Repeat shape (before) | Cost paid |
|---|---|---|
| 1 | Identical candidate, converged code | 1 VLM call: no key through the C05 vocabulary existed |
| 2 | Identical candidate, verdict filed pre-convergence | 1 VLM call: new lookup could not read the legacy entry |
| 3 | Overlapping window (same event, shifted bounds) over judged frames | 1 VLM call per window: exact frame-times key missed |
| 4 | Duplicate spans inside one batch | 1 VLM call per duplicate |
| 5 | Two recorded verdicts disagreeing about the same frames | 1 VLM call with the conflict unnamed and still in the store |
| 6 | Prompt version bumped since the verdict | old answer served (stale), or re-paid without the reason named |

## 2. What changed (after)

`library/tools/cache_keys.py`: `verification_context()` +
`verification_key()` - the interpretation key for one VLM verdict
(source identity, model, prompt version + template digest, normalized
statement, shown frame times, preprocessing, frame-selection recipe,
producer version). A recipe change moves the key: a stale verdict is a
MISS, never a false hit.

`library/tools/span_verification.py`, same lookup everywhere (pre-lock
check and per-span loop share `_lookup`):

1. Exact recorded verdict answers: legacy key, then the C05 key.
   A legacy hit migrates to the C05 key on read (family tag added), so
   the store converges without invalidating what is filed.
2. A recorded verdict whose judged frames DECIDE the requested ones
   answers next (`reuse: "overlap"`): `no` over a superset of the
   requested frames; `yes` whose establishing `frame_t` is shown again.
   A `yes` whose frame is NOT shown decides nothing (judging a window
   by an unseen frame is refused, not reused).
3. A recorded `yes` + `no` that both decide the frames REOPEN the
   question: exactly one fresh call, marked `reopened:
   "conflicting_verdicts"`.
4. Anything else - including a recipe change - pays one fresh call.
   New verdicts file under both keys with `family` and
   `producer_version`; the interval family also files `gap_seconds`,
   which is part of its question.

Stop-escalation falls out of the order: no fresh VLM call while recorded
evidence settles the window, and a batch every recorded verdict decides
skips heavy-work admission entirely (the pre-lock check runs the same
lookup, so no model loads at all).

Out of lane scope, audited, left unchanged:
`library/tools/on_demand_verification.py` - its producers are transcript
and frame-sample reads (no VLM call to dedup), and its store key already
names span, claim and kind exactly. No VLM repeat cost lives there.

## 3. Measurement: recorded request replay, before vs after

`tests/unit/picture/test_verification_reuse.py::test_recorded_request_replay_costs_no_repeat_verification`.
The seed store is what a previous run filed (three judged windows). The
repeat asks five windows: one identical, one shifted showing the
yes-frame, its in-batch duplicate, one narrowed inside a judged `no`,
one genuinely new. BEFORE is the legacy exact-key lookup only (the new
step neutralised - exactly the old code path); AFTER is the full reuse.
A scripted stub counts the calls the real VLM would have taken.

| | VLM calls | answers |
|---|---|---|
| Before (exact-key only) | 3 | baseline |
| After (F03 reuse) | 1 (the new window) | byte-identical to before (asserted) |

Projected VLM seconds at the module-measured ~4.0 s/span-call: ~12 s
before vs ~4 s after for this replay; every repeated equivalent query
over N already-judged windows saves N calls (N x 4 s spans, N x 13-81 s
intervals). Call counts are measured; per-call seconds are cited from
the module's own recorded measurements, not re-timed here (no model
loads in this lane by the memory rule).

Quality gate: the replay asserts identical `(answer, frame_t)` on all
five windows - reuse changes no answer. Any quality regression (a
reused verdict differing from the fresh one) fails the test.

## 4. Tests

`tests/unit/picture/test_verification_reuse.py`: 11 defect-naming
tests, no model, no media. Each fails on the defect named in its
docstring (exact reuse + C05 filing, legacy serve + migration, overlap
`no`, `yes`-frame-shown vs shifted, conflict reopen, recipe-change
reopen, in-batch duplicates, interval gap as part of the question, C05
key movement, decided-batch skips heavy-work admission, recorded-replay
before/after). Run: 11/11 pass.
Neighbors: `test_cache_keys`, `test_temporal_rerank`,
`test_event_spans_candidates`, `test_temporal_action_candidates`,
`test_world_model_query`, `test_claim_provenance`, `test_world_model`,
`test_relationships`, `test_plan_span_verification`: 134/134 pass.
`ruff check` with no config and with `ruff-ci-gate.toml`: clean.
No suite runs (lane rule); only the changed subsystem's unit tests plus
the direct consumers of the verdicts.

## 5. Left undone

* `verified_query` still verifies every tier candidate with no early
  stop; the per-call cost is now reuse-bounded, but an early-stop rule
  (stop asking once the answer is settled) belongs to a lane that owns
  that query's completeness contract, not to the verify layer.
* Legacy verdicts become overlap-eligible only after one exact hit
  migrates them (family tag required for overlap, so a span verdict can
  never answer an interval question). Stores that are never re-queried
  exactly keep exact-only reuse for their old entries.
* Cross-module C05 reuse (a future producer filing the same C05 key) is
  served by the lookup but has no second producer yet.
