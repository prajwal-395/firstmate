# The editorial intent contract and the editorial decision graph

**Punch list:** "Ren - Detailed Capability Improvement Punch List" (baseline
2026-10-20), section 3 "Autonomous Editorial Reasoning and Decision-Making",
items EDIT-001 to EDIT-015.

**Lane:** `cap-edit-intent`.

**What this lane owns:** the canonical representation of what was ASKED FOR,
and the record of what was DECIDED on what evidence - compiled into AXI edit
requests without the execution layer inventing creative intent.

**The two modules:**

- `library/tools/editorial_intent.py` - the canonical `EditorialIntent`.
- `library/tools/editorial_decisions.py` - the editorial decision graph and
  its compiler.

**The shape of the thing:**

    OBSERVATION          a measurement in the Project Model
    INTERPRETATION       what someone read out of the observations
    CANDIDATE DECISION   what someone proposes, with its evidence
    APPROVED DECISION    the candidate somebody accepted
    PHYSICAL OPERATION   the algebra op that carries it into Resolve

A decision REFERENCES observations and interpretations; it never contains
one, so a decision can never be read as a measurement. Only an APPROVED
decision compiles, and the compiler refuses rather than completing anything.

---

## Per item: what was already done, and what this lane added

### EDIT-001 - a canonical `EditorialIntent` distinct from physical operations

**Already done.** The Rust protocol declared the type
(`native/ren-protocol/src/project_model.rs:702`): `id`, `brief_ref`,
`constraints`, `evidence_links`, with `intents` a required collection of
`ProjectModel` and `intent_id` links on `ClipUse` and `EditOperation`. The
Python side had the neighbouring half - `library/tools/intent_model.py`
(beats, claims, key moments, a pacing curve, and `editorial_spec`) and
`library/tools/scoped_intent.py` (declaration precedence). The type was a
DECLARED SLOT with no producer, no consumer and no validator: the Python
validator checked `observations`, `relations`, `events`, `interpretations`
and `edits`, and never `intents`.

**Added.** `library/tools/editorial_intent.py` is the canonical
representation, and it is separate from operations by construction: an intent
says what the viewer should get, an operation says which clip moves.
`to_project_model_row` projects it into the shape the Rust protocol already
declares, and `from_project_model_row` reads it back - the durable record and
the canonical record are one record read two ways, proved by
`test_the_intent_round_trips_through_the_project_model_row`.
`commit_intent` is the producer the collection never had.
`library/tools/project_model.py` now validates the collection: unique ids,
evidence links that resolve, and `intent_id` links from edits and clip uses
that resolve.

### EDIT-002 - extract the user's explicit requirements, constraints, references and desired outcome

**Already done.** `intent_model.editorial_spec` (written by step 2.01) already
separated `requirements` from `preferences`, carried `target_duration`,
`platform`, `audience`, `pacing`, `subjects`, `narrative_goals`,
`desired_content`, `prohibited_content`, `references`, `unresolved_decisions`
and a `revision` ledger. `library/tools/brief_attachment.py`,
`brief_reference.py` and `brief_snapshot.py` carry the brief itself and its
bytes.

**Added.** The canonical intent reads that commission and states the four
things the punch list names as FIELDS: `requirements`, `constraints`,
`references`, `desired_outcome`. `build_intent` is the extraction, and
`from_state` reads it out of a run. The desired outcome is the narrative
goal; the desired CONTENT stays its own list, because what must appear and
what the viewer should get are different things.

### EDIT-003 - record inferred preferences separately from explicit instructions

**Already done.** `library/tools/scoped_intent.py` resolves declarations by
precedence (`creator` > `brand` > `project` > `inference`) and refuses to
mark an inference explicit.

**Added.** The canonical intent holds THREE lists - `requirements`,
`preferences`, `inferred_preferences` - and every declaration carries an
`explicit` flag that must agree with the list it is in. An inference in the
explicit list and an explicit statement in the inferred list are both
refused, by name. `inferred_from_view` reads the inference rows out of the
scoped intent view, so the two mechanisms cannot disagree about which
declarations are inferred.

### EDIT-004 - distinguish objective requirements from subjective creative goals

**Already done.** `library/tools/editorial_types.py` types every editorial
state key as fact, judgment or request, and refuses a judgment resting on no
fact or request.

**Added.** Within the intent, every declaration carries `objective`: whether
it can be checked against a measurement. A requirement that is not objective
is refused - a mandatory statement nobody can verify is a goal wearing a
requirement's name. A preference MAY be objective (a measurable preference is
still a preference), and the classification is per entry, not per list.

### EDIT-005 - model requests such as improve pacing, strengthen an opening, remove repetition, improve continuity, choose a better angle and tighten dialogue

**Already done.** Nothing. The six named requests appeared only as prose in
briefs.

**Added.** `REQUEST_KINDS` is ONE enumeration of the six, and each kind
carries a declared `RequestProfile`: whether it is objective, its default
autonomy and its evidence bar. `match_request_kinds` maps a statement to a
kind by declared cue phrases, and `request_kind` REFUSES a statement that
names two kinds - the choice between them carries different evidence bars
and is the commissioner's to make.

### EDIT-006 - define what constitutes sufficient evidence to act on each request

**Already done.** `library/tools/evidence_contract.py` types the evidence
(measurement, inference, evaluation, creative judgment) and
`library/tools/evidence_query.py` routes it by lane; `edit_candidates.py`
verifies predicates per CANDIDATE.

**Added.** Each request kind declares the lanes whose evidence must be
present before Ren may act - `m1` (speech) for tightening dialogue,
`m6`/`m3b` (multicam, face) for choosing a better angle, and so on. A
request may NARROW its bar and may not widen it. The bar is EXECUTABLE: the
compiler refuses a decision serving a request whose bar is unmet, naming the
lanes.

### EDIT-007 - record which judgments Ren can make autonomously and which require clarification or approval

**Already done.** `library/tools/reel_proposal.py` has an approval enum and
`library/tools/assignment_lifecycle.py` a state machine, for REELS.

**Added.** Autonomy is a PER REQUEST field: `autonomous`,
`needs_clarification` or `needs_approval`, with the `autonomy_basis` that
decided it ("the declared default for remove_repetition"). A request that
needs clarification and asks no question is refused. A decision serving a
request that needs approval compiles only with the approval recorded, and a
decision serving a request that needs clarification does not compile at all.

### EDIT-008 - make uncertainty and missing evidence explicit rather than inventing justification

**Already done.** `library/tools/undetermined.py`,
`direction_contradiction.py`, `evidence_graph.answer_uncertainty` and
`evidence_contract.EvidenceRecord.uncertainty` all carry the "I do not know"
channel for STEPS.

**Added.** The intent carries it for REQUESTS. Every request has an
`uncertainty` number (0..1) and a `missing_evidence` list, and the BUILDER
fills that list with the lanes the bar names and the evidence lacks -
`not_yet_analysed`, unless the caller measured the absence and said so. A
hand-written record whose bar is unmet and which declares nothing about it
is refused: silence is not evidence.

### EDIT-009 - separate observations, interpretations, candidate decisions, approved decisions and physical operations

**Already done.** The Project Model already separates `observations`,
`interpretations` and `edits`; `library/tools/edit_graph.py` derives a graph
of the planners' decisions with intent links, evidence links and dependency
edges; `library/tools/revise.py` and `decision_implication.py` turn a note
into candidate decisions and a judged proposal.

**Added.** `library/tools/editorial_decisions.py` holds the five states in
ONE record: `CANDIDATE`, `APPROVED`, `COMPILED`, `REJECTED`, with the
physical operation living in the decision's `operations` and the evidence
living in `evidence_ids` by REFERENCE. A decision is never an observation and
never an interpretation. Only an APPROVED decision compiles.

### EDIT-010 - link every proposed edit to the source evidence supporting it

**Already done.** `edit_candidates.build_candidate` links a candidate to its
evidence rows; `evidence_to_edit` ledger rows carry a rationale and their
evidence ids; `project_model.py` requires an evidence-backed edit to carry
`evidence_ids` that resolve to observations.

**Added.** The decision record's `evidence_ids` is REQUIRED to approve and
REQUIRE to compile: `approve` refuses a decision with no evidence, and
`_check_compilable` refuses one again at compile time. The Project Model
boundary now also checks that an intent's `evidence_links` resolve, and that
an edit's `intent_id` names an intent that exists.

### EDIT-011 - represent temporal and narrative relationships across shots, dialogue, events and audio

**Already done.** `library/tools/relation_classes.py` derives temporal,
physical and semantic relations with a per-class evidence bar;
`narrative_relations.py` produces candidate narrative interpretations;
`project_model.py` refuses a semantic relation that claims to be verified.

**Added.** The decision record carries `relations`, each naming one of
`temporal`, `physical`, `narrative`, `dialogue`, `audio` and an id. A
relation naming no kind, or no id, is refused: a relationship with nothing
on either side is not one.

### EDIT-012 - represent dependencies between editorial decisions

**Already done.** `edit_graph.py` nodes carry `depends_on` and
`dependents_of` walks the blast radius; `edit_request._check_acyclic`
refuses a cycle in the operations.

**Added.** The decision graph carries `depends_on` between DECISIONS, not
between operations, and the compiler turns it into the operation order: a
decision that waits for another waits for every operation of that one. A
dependency naming a decision that is not in the graph is refused, and a
cycle is refused by name.

### EDIT-013 - record tradeoffs such as pace versus clarity, visual interest versus continuity, and brevity versus completeness

**Already done.** Nothing. `library/tools/cohesion_scope.py` splits a
review finding into an actionable item and an observation; it does not record
a tradeoff.

**Added.** `TRADEOFF_AXES` is one enumeration (`pace`, `clarity`,
`visual_interest`, `continuity`, `brevity`, `completeness`) and every
tradeoff names the axis it took, the axis it gave up, and HOW it was
resolved. A tradeoff weighing an axis against itself, naming an unknown
axis, or recording no resolution is refused - a tradeoff nobody resolved is
a worry, not a decision.

### EDIT-014 - ensure every proposed change includes the intended effect and its evaluation criteria

**Already done.** `library/tools/evaluation_policy.py` types requirements
and preferences as policy rules; `edit_render_check.py` and
`reel_hearing.py` measure a rendered result.

**Added.** Every decision carries `intended_effect` and
`evaluation_criteria`. An APPROVED decision without either is refused, and
the compiler refuses it again. A criterion naming no measure is refused: a
criterion nobody can look for is not one. The effect is the DECISION's, and
the compiled operation inherits it - the execution layer never writes one.

### EDIT-015 - compile accepted decisions into AXI operations without allowing the execution layer to invent creative intent

**Already done.** `library/tools/edit_request.py` owns the ONE structured
intent the backends compile from, and its `EditOperation` already refuses an
operation with no `expected_effect`. But `from_algebra_ops` and
`from_edit_program` FILL one in when it is absent
(`"apply clip.trim ..."`), and nothing recorded WHICH decision an operation
came from.

**Added.** `editorial_decisions.compile_to_edit_request` is the compiler, and
it refuses rather than completing anything: a candidate does not compile, a
decision with no evidence does not compile, a decision with no effect does
not compile, a decision with no criteria does not compile, a decision with no
operation does not compile, and a request whose bar is unmet or whose
autonomy is unmet does not compile. The compiled request names the decisions
it came from in its `originator`.

**Not a parallel path.** `editorial_decisions.request_from_program` reads the
program the existing translation paths already produce and returns the SAME
request `edit_request.from_edit_program` builds from it - the operations,
their order, their effects, the request id, the idempotency key and the base
generation are identical. Proved by
`test_the_decision_compiler_builds_the_same_request_the_program_reader_builds`.
The decision layer is a second READER of one program, and a second WRITER of
one record.

---

## What was deliberately NOT done, and why

- **The live `ren revise` path was not swapped over to the decision
  compiler.** The revise path's program is already a validated, atomic,
  captain-approved edit recorded in the edit ledger with its anchor and its
  reason. The decision layer's rules are STRICTER than that path's: it
  requires evidence and evaluation criteria, and the revise program carries
  neither. Swapping the live path over would have made a revision refuse
  for a reason the captain cannot act on. The connector exists
  (`request_from_program`), it is proved equivalent, and the lane left the
  live path alone rather than weaken the contract to fit it.
- **The Rust protocol type was not extended.** `EditorialIntent` carries
  `constraints: Vec<Value>`, an arbitrary-value list, so the canonical
  record's entries project into it without a schema change. The schemas in
  `native/ren-protocol/schemas/` are GENERATED by `cargo run --bin
  emit-schemas`, and no Rust toolchain is available in this lane, so a type
  change would have left the committed schemas stale. The projection is
  lossless in one direction and reads back through
  `from_project_model_row`.
- **No `ren` verb was added.** A new verb must be registered a SECOND time
  in `native/ren-cli/src/commands.rs`, and that crate cannot be built here.
  The capability is reachable the way the other tools are:
  `bin/vep -m library.tools.editorial_intent` and
  `bin/vep -m library.tools.editorial_decisions`.

## Where the capability is reachable

```sh
# what was asked, what was inferred, what evidence each request still needs
bin/vep -m library.tools.editorial_intent <project>

# check a decision document, and compile the approved decisions into a request
bin/vep -m library.tools.editorial_decisions check <decisions.json>
bin/vep -m library.tools.editorial_decisions compile <decisions.json> \
    --project <project> --timeline <timeline> --intent <intent.json>
```

The compiled request is an `edit_request.EditRequest` - the same semantic
interface AXI executes - and goes into the existing path unchanged:

```python
from library.tools import editorial_decisions, resolve_axi_core

request = editorial_decisions.compile_to_edit_request(
    graph, project=project, timeline=reel, intent=intent)
prepared = resolve_axi_core.prepare_edit_request(request)
```

## Tests

- `tests/unit/context/test_editorial_intent.py` - the intent contract.
- `tests/unit/context/test_editorial_decisions.py` - the decision contract
  and the compiler.
- `tests/contracts/test_editorial_intent_contract.py` - the Project Model
  boundary.
