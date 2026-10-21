# The capability coverage report

BASE-002 asked for one report over the eighty-four edit-algebra
operations, and for a question Ren can answer BEFORE it attempts an
edit.  Both are served by `library/tools/capability_coverage.py`, and
both are GENERATED: the operation rows come from
`edit_algebra.OP_STATUS`, the dimension coverage from
`library/tools/axi_qualification_records.json`, and the routing answers
from `library/tools/execution_router.py` itself.  Nothing here is a
hand-maintained roster, so the report cannot drift away from the
registry it describes.

## The report

```
bin/resolve-axi capability-coverage                  # the summary
bin/resolve-axi capability-coverage <operation>      # one operation, in full
bin/resolve-axi capability-coverage --full           # every operation, in full
```

The summary carries the counts, the production-enabled operations, the
operations the production route refuses, the qualification-gap priority
and the priority order.  One operation's row carries:

| field | what it says |
|---|---|
| `operation_qualification` | the static fact: the status, and per backend the qualification status, the evidence, the tested media types, the timeline conditions, the preservation limits and checks, the visual and audio verification, and the recovery qualification |
| `qualification_dimensions` | which qualification dimensions the evidence covers, partially covers (with the tested and untested values) and does not cover at all |
| `supported_states` | the media types and timeline conditions the operation declares, and its verification contract and inverse |
| `runtime_routing` | what the router would do: the production route, the protected experimental route and the full rebuild, each with the router's own reasons or refusal |
| `alternatives` | the declared and implemented backends, the unqualified ones, and the same three routes |
| `priority` | the measured editorial demand, the impact tier and the score the order was made from |

## Two qualifications, kept apart

An operation can be QUALIFIED - live evidence exists, under recorded
predicates - and still not be ROUTABLE on the timeline in front of you,
because the inspected state does not satisfy those predicates.  The
report keeps the two apart:

* `operation_qualification` is the static fact, read off the registry;
* `runtime_routing` is what the router would do with the state in front
  of it, and it says `unknown` - never `supported` - when no state was
  inspected.

An unread state is not a pass: a state nobody read cannot satisfy a
predicate, and the router refuses it.

## The read-only question

```
bin/resolve-axi support <operation> --conditions '<the measured state as JSON>'
bin/resolve-axi support <operation> --conditions-file <path.json>
```

`--conditions` is the measured state of the timeline in the
qualification dimension vocabulary
(`library/tools/axi_qualification_dimensions.py`): `media_type`,
`clip_composition`, `linked_audio_configuration`, `track_layout`,
`track_occupancy`, `timeline_density`, `timeline_structure`,
`project_settings`, `frame_rate`, `existing_effects` and
`resolve_version`.  A live timeline's state is measured by
`library/tools/axi_qualification_state.py`, which reads Resolve under the
instance lease and never writes to it.

The answer is JSON:

* `answer` - `supported`, `not_supported`, `unknown` or
  `unknown_operation`;
* `runtime_eligibility` - `eligible`, `not_eligible` or `unknown`;
* `why` - the router's own words: what happened, why, and the next
  action;
* `runtime_routing` - the three routes, each admitted or refused;
* `alternatives` - including whether a full rebuild would actually be
  admitted, and its refusal when it would not.

A rebuild is NOT a way around a qualification gap: the router
preflights the rebuild route with the same qualification policy as
every other route, so an unqualified operation is refused by the
rebuild too.  The report says that rather than implying a fallback
exists.

## Priority, and what it measures

The punchlist asked for prioritization by "real editorial frequency and
impact rather than operation count".  The engine keeps no telemetry of
completed edits, so the honest measurement of editorial demand is the
SHIPPED PIPELINE'S OWN REQUEST SITES: the production call sites that
construct the operation, measured by scanning `library/` for the
operations the code asks for.  The vocabulary declaration
(`edit_algebra.py`), the operation map and the report itself are
excluded, so declaring an operation never counts as asking for one.

The impact tier is derived from the algebra's own safety class and
state domain: a structural operation rewrites the reel's structure, a
localized one changes a clip, a reversible mutation changes a value, and
a plan operation writes nothing to the timeline.  The score is the
measured request count times the impact weight, and every row publishes
the inputs it was ranked by, so a reader can re-rank it.

`tests/unit/resolve/test_capability_coverage.py`.
