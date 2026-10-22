# R02 - Unify the retrieval entry points: evidence (2026-10-22)

Lane mm-r02-retrieval-contract. No models loaded, no media rendered, no
Resolve touched. Stubs only.

## What was measured, and on what

Recorded query set: `library/tools/retrieval/contract_set.json` (12
queries across text, visual, acoustic, OCR, compound, temporal and
relational plans). Harness: stub text/frame indexes (no embeddings, no
media) counting lane invocations per query through the shipped
`search_footage`, plus cross-entry consistency of returned rows.
`BEFORE` ran on the pre-change code, `AFTER` on the changed code, same
stubs. Reports: `/tmp/r02-before.json`, `/tmp/r02-after.json` (scratch,
not committed); per-query table below.

## Passes executed per query (denominator: 12 recorded queries)

| metric | before | after |
|---|---|---|
| total lane passes | 25 | 20 (-20%) |
| text embedding passes | 12/12 queries | 6/12 queries (-50%) |
| queries whose evidence is unchanged row-for-row | - | 6/12 |
| queries returning fewer rows | - | 5/12, all dropped rows text-lane kinds on plans with no textual requirement |
| queries returning more rows | - | 1/12 ("dog barking": +sound lane hit) |
| queries left with zero evidence | 0/12 | 0/12 |

Per-query lanes_ran, before -> after:

- "he explains the pricing rule": [text] -> [text], hits 2->2
- "wide shot of the parking lot": [text visual] -> [visual], hits 2->1
- "dog barking in the background": [text visual] -> [text sound visual], hits 2->3
- "applause after the speech": [text sound] -> [sound time], hits 3->1
- "laughter from the audience": [text sound] -> [sound], hits 3->1
- "text on screen that reads the price": [text ocr visual] -> same, hits 3->3
- "the second time she laughs on camera": [text sound visual] -> [sound visual time], hits 3->2
- "find the reaction shot after the announcement": [text visual] -> [visual time], hits 2->1
- "the moment he explains ... while the pricing slide is up": [text visual] -> [text visual time], hits 2->2
- "close-up of the host talking about refunds": [text visual] -> same, hits 2->2
- "silence before the announcement": [text sound] -> [sound time], hits 3->1
- "the red car drives past the building": [text visual] -> same, hits 2->2

(`time` appearing in AFTER lanes_ran is a reporting correction - the
timeline lookup always ran but never recorded itself - at zero
retrieval cost, not a new pass.)

## Why the dropped rows are not a recall regression

Every dropped row is a text-index row (speech/scene) on a query whose
decomposed plan names no textual requirement. Transcript rows are not
sound evidence and not picture evidence - the same rule the planning
slot search already enforces ("transcript hits never fill visual
slots"). No query goes to zero evidence, and the backstop (tested:
frame lane refused/missing, `use_visual=False`) runs the text pass
whenever a requirement no other lane answered still could be. The added
sound pass on "dog barking" is a LOOKUP (deterministic label join, no
embedding), and it repairs a routing gap: "bark/barks/barking/barked"
was missing from the cue table while the sound lane already knew how to
match the "Bark" label.

## Cross-entry-point consistency

Same query + same facet filters through the multilane text lane and
through a direct `search_report` return the same rows by segment_id
(pinned by test R02-5). Caller-supplied facets are forwarded to
`search_report` before ranking (R02-4); every hit on every route
carries the contract envelope (interval, uncertainty, provenance;
R02-6). Lookup vs semantic is typed on the route (R02-7), and every
skipped lane names its reason in the answer (R02-8).

## What changed

- NEW `library/tools/retrieval/contract.py`: entry-point map with
  production callers, 8-modality routing table (text/OCR/appearance/
  objects/action/audio/identity/temporal), LOOKUP vs SEMANTIC pass
  classes, `route_plan` / `text_backstop_needed`, `RetrievalRequest` /
  `RetrievalEvidence` envelope.
- `library/tools/retrieval/search.py`: routes through the contract;
  text runs as primary or backstop, skips with a named reason
  otherwise; new optional `filters` (forwarded only when set, so
  duck-typed indexes keep working); time lane records itself as ran;
  hits carry the evidence envelope (additive keys).
- `library/tools/retrieval/requirements.py`: bark family added to
  `SOUND_EVENT_CUES`.
- `library/tools/retrieval/__init__.py`: contract names exported.
- NEW `library/tools/retrieval/contract_set.json`: the recorded set.
- NEW `tests/unit/tools/test_retrieval_contract.py`: 26 tests, one
  defect family each (R02-1..R02-8).
- `tests/unit/tools/test_retrieval_plan.py`: one expectation updated -
  "where does he laugh" no longer runs a text pass (that prefix was
  the inefficiency, not the defect the test names).

## Left undone

- `slot_search`, `ren search` CLI and `World.search` consult the
  contract by convention, not by code: they were not re-plumbed onto
  one physical implementation, per the brief (logical contract only).
- No recall measurement on real footage: that needs model-loaded
  indexes and is held for the vision-model fix; the stub set measures
  passes and consistency, not ranking quality.
- The `RenRefusal` base class cannot be constructed directly (needs a
  taxonomy blocker); production raise sites that raise it bare would
  fail at construction. Not touched: outside this lane.
