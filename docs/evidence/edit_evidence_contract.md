# The evidence-grounded editing contract (Q01)

Code: `library/tools/edit_evidence_contract.py`.
Tests: `tests/unit/tools/test_edit_evidence_contract.py` (16 tests).

The punch list's **Q01**: "Require every proposed edit to reference
source evidence. Include event identity, source interval, precision,
certainty, and supporting observations. Distinguish what Ren measured
from what Ren inferred. Include alternative candidates when evidence is
ambiguous. Require explicit confidence thresholds for autonomous
execution." Acceptance: "Editorial decisions can be explained and traced
to actual footage."

## What the engine carried before

A proposed edit carries `evidence_ids` - bare strings. The revise path
(`library/tools/revise.py`) grounds them on `revision-note:<id>`: a
human's words about the timeline, which name no footage, no event, no
interval, no precision and no certainty. `audit_decision` over six
recorded proposals in the engine's own decision shape (dialogue cut,
scene cut, sound-led cut, revise note-id, ambiguous candidate pair,
captain's correction) found **0 of 6 grounded, 0 complete references**
- the trace ended at a string in every case.

## What the contract requires

One `EvidenceReference` per claim a proposal rests on: the event cut on
(a claim key the producers declare), the source interval on the
universal source reference (PR 3116), the precision (`tick`, `element`,
or boundary-approximate `span`), the certainty the claim earned
(measured / inferred / interpreted, PR 3137 - never defaulted), the
uncertainty exactly when the claim is inferred, and the supporting
observation ids. A portable record (PR 3163) stays the shape
observations travel in; the reference cites observation ids and reads
them back, it never re-stores them. The instruction loop (PRs 3145,
3152) stays the account of what ran; `gather_evidence` is where this
audit belongs.

Refusals the contract owns: unknown event kinds, certainty mismatches
(a detector span filed as measured), inferences without uncertainty,
numbers on a measurement's certainty, judgements naming nobody, files
filed as places in themselves, and the chosen event listing itself as
an alternative. Ambiguity is reported, not hidden: an overlapping
candidate the reference does not list fails the audit, and a listed
alternative keeps the choice on the approval path.

## The autonomy bar (declared, v1)

Autonomous execution needs every reference complete, every inferred
claim at or under uncertainty **0.2**, nothing boundary-approximate,
nothing ambiguous, no judgement standing in for evidence, AND a request
that granted autonomy. The 0.2 is the contract's declared bar, not a
measurement: producer scores are 0..1 confidences and the bar is set
where a shortfall is cheap to confirm and expensive to get wrong. A
project tightens it per call; moving the default is a contract-version
bump. The gate narrows only - it never grants what the request withheld.

## Measured on the recorded plans

Same six recorded proposals, grounded through
`reference_from_observation` against stored observations (no model, no
media):

- grounded edits: **0/6 before, 5/6 after** (the revise note-id names
  no observation and stays ungrounded - there is nothing to ground it
  to, which is the finding, not a gap in the audit);
- complete references: **0 before, 5 after** (1 measured, 3 inferred,
  1 interpreted; 1 ambiguous with the alternative listed);
- gate verdicts under an autonomous request: dialogue and scene cuts
  autonomous; sound-led (uncertainty 0.3), ambiguous, and interpreted
  need a person; the note-id is refused.

## What is still missing

- The revise path still grounds decisions on `revision-note:<id>`. The
  contract refuses such strings when asked, but no caller asks yet:
  wiring `audit_decision` into the revise/instruction-loop path is a
  separate change with its own rollout.
- `compile_to_edit_request` does not call the gate. The gate is
  available at the boundary; enforcing it there changes what compiles
  today and needs the recorded-plan rollout above first.
- Precision `element` has no producer yet: no lane cites a frame/sample
  index as the cut point, so every constructed reference today is an
  interval. The kind is carried so the first such producer does not
  need a contract bump.
