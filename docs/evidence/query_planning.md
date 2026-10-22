# R03: the explicit query-planning stage, measured

**Lane:** `mm-r03-query-plan` · **Date:** 2026-10-22 ·
**Punch-list item (quoted):** "R03 - Introduce an explicit query-planning
stage. Parse a natural-language editorial request into typed
constraints. Extract deterministic filters first: known speaker/person,
source/camera, transcript phrase, time range, object category,
action/state transition, shot or scene context. Identify ambiguous
dimensions requiring semantic inference. Generate a retrieval plan with
ordered cheap and expensive steps. Set candidate budgets and
verification requirements according to task complexity. Replan when
evidence is missing or contradictory."

**Acceptance (quoted):** "A query such as 'Find the close-up where the
speaker picks up the phone after saying goodbye' becomes a structured
search across speech, temporal ordering, framing, and object-action
evidence."

## What was built

`library/tools/query_planner.py` - the R03 stage, on top of the L5
typed requirement graph (`library/tools/requirement_graph.py`), which
it extends and does not rebuild. One entry point, one typed dict, no
model, no footage, no Resolve:

* `extract_filters` - the eight deterministic slots, each from a
  closed table or a stated anchor (track id, asset id, number, quote,
  say-verb topic, topic head, about-phrase, backward topic for
  verb-final relatives). A slot with nothing stated stays absent.
* `ambiguous_dimensions` - person_identity (role, no track),
  action_kind (open verb, no candidate kind), framing_measure
  (framing word, no calibrated floor), phrase_exactness (topic, not
  quote), words_or_picture (noun with no framing cue).
* `build_steps` - cheap measurement lookups first, then verify tasks,
  then inference, then recorded refusals; temporal order edges become
  `depends_on` links. A requirement whose cue a filter already
  consumed (the `face` of `face_001`, the `second` of an ordinal) is
  SUPERSEDED with a zero budget - kept for audit, never searched
  twice.
* `budgets_for` / `complexity_of` - per-step caps (cheap 50, verify
  20, inference 10) and a total by band (simple 100, moderate 200,
  complex 300); verification required exactly when a verify step
  survives.
* `replan` - absent lane drops the step, partial lane halves its
  budget, contradicted steps hold for review; every change logged, a
  no-op replan says it changed nothing.

Three substrate fixes in `requirement_graph.py`, each with a test:
order markers (`after`, `before`) never become object queries; the
after-side ordering prefers the event the marker governs (the anchor
attaches to the pick-up, not to the earliest mention); plurals fold
to the detector's singular label. Four scaffolding words
(`doing`, `ever`, `one`, `boundary`) stopped becoming queries.

## The measurement

Corpus (denominator 49): the 32 M0 questions from
`docs/benchmarks/2026-10-22-targeted-reasoning.json`, the 16 recorded
editorial queries in `library/tools/editorial_retrieval_set.json`,
and the R03 acceptance query. Expectations hand-recorded in
`tests/fixtures/query_planning/queries.json`. Harness:
`scripts/measure_query_planning.py`.

`before` is the substrate's parse plus compile mapped onto the
slots (a speech topic becomes the transcript phrase, an image
presence the object, an action candidate the transition, a technical
cue the shot context; person, ordinal, time and camera have no slot
and always miss). The baseline runs on the FIXED substrate, which can
only flatter it. `after` is `plan_query`.

| Measure | Before | After |
|---|---|---|
| filter-exact queries (all 8 slots) | 9/49 | **47/49** |
| filter slots (recall over 392) | 293/392 | **390/392** |
| step order (cheap before expensive) | 15/42 multi-step | **25/25 multi-step** (24 single-step) |

The two residual misses are both transcript phrases with no speech
anchor, and both are recorded, not smoothed over: E08 ("the one
calling GEO just SEO" - a naming construction no cue covers) and E16
("the twenty percent line" - `line` as a dialogue line is left
ambiguous on purpose: it also names queues and product lines). Their
object and action filters still extract.

The acceptance query plans as required: person role `speaker`
(unresolved, flagged), transcript topic `goodbye`, object `phone`,
action `pick_up_put_down`, framing `close-up`, with the order edge
`saying-goodbye -> picks-up` and the pick-up step depending on its
speech anchor. Verification is required on `m7`; the close-up is
recorded as a refusal plus a `framing_measure` inference need,
because the query states no number no floor may be compiled from.

## What was left undone

* Declared names ("Craig", "Akshata") resolve through no identity
  registry yet: the person slot takes tracks and roles only, and says
  so. E04/E05 briefly extracted brand/place names as people before
  the heuristic was removed; the removal is pinned by
  `test_counted_people_are_not_presence` and the E04/E05 rows.
* No `ren` verb and no `world_model_query` subcommand were added:
  the capability is reachable as
  `python3 -m library.tools.query_planner plan "..."`.
* The editorial text queries expose the closed table's edge honestly:
  "the comparison of X against Y" plans through the new topic-head
  rule, but constructions with no head, verb or about-anchor (E08)
  stay misses until a semantic-inference lane exists to take the
  `ambiguous` dimensions this stage now names.
