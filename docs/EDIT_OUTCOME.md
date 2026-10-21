# PROD-008: the product outcome of a generated edit

The punch list's **PROD-008** (2026-10-20):

> Add product-level feedback and outcome tracking.
> - Capture whether a user accepted or modified a generated edit.
> - Record correction type, time saved, and amount of manual repair.
> - Distinguish objective system failures from stylistic disagreement.
> - Keep media and project-level learning opt-in and appropriately isolated.

This is the module that answers it: `library/tools/edit_outcome.py`.

## What it reads

Nothing new is measured. The outcome is read off records the engine
already keeps:

| The record | Where it lives | What it carries |
|---|---|---|
| the edit history | `reel_edit_history` | that the change was the CAPTAIN's, when, and what it changed |
| the detected difference | `plan_provenance` (`unattributed_editor_changes`) | the before/after states and the fields that moved |
| the version ledger | `reel_versions` | the Ren build the captain was handed, and the ADOPTED version his hand edit produced |
| the delivery acceptance | `reel_acceptance` | that the reel was delivered and taken |
| the subject framing | `manual_edit_intent` | the one objective claim the engine makes about a LOOK |

## The outcome

One of three, per reel:

- `accepted` - the reel carries a delivery acceptance and no hand
  correction was recorded after it;
- `modified` - a hand correction is recorded after the Ren edit;
- `unmeasured` - nothing recorded decides it. **Not** "accepted": an
  absent measurement is never a default.

A reel that was accepted and then corrected is `modified`; the
acceptance is still carried in the record.

## The classification, and who decides it

The engine never decides a disagreement about taste on its own
(AGENTS.md 10.5). A correction is classified only when something
decides it, and it is otherwise `unclassified` - counted on its own,
never folded into either column:

| Class | Decided by |
|---|---|
| `objective_failure` | the captain's own words (filed against the edit), or the engine's own measurement of a defect at that place (the subject framing a hand edit broke) |
| `stylistic_disagreement` | the captain's own words, or a change of the LOOK that no measurement found broken |
| `unclassified` | nothing measured it and nobody said - which is **not** taste |

The captain's words are filed with the reason, through
`reel_edit_history.file_manual_edit_reason(..., classification=...)`.
The vocabulary is `objective_failure` and `stylistic_disagreement`, and
a classification with no reason behind it is refused.

## The amount and the time

The amount is measured in frames, because frames are what the engine
measures: the corrections, the rows they touched, the items they
changed, and the program they re-cut or re-timed. A correction of the
LOOK changes no program - the same frames play in the same place,
restyled - so it is not repaired program.

The time is two different things and they are never added together:

- `elapsed_to_correction_seconds` - the wall-clock window between the
  Ren build and the captain's hand correction. It is an **upper bound**
  on his repair time: the window also holds the time he watched the
  reel.
- `time_saved_seconds` - the program seconds that reached the captain
  without being re-cut or re-timed. It is a statement about the
  **program delivered**, not an estimate of a person's hours, and it
  carries its denominator.

Both are `unmeasured` when the measurement is absent, and an outcome
that is `unmeasured` claims no time saved at all.

## Learning is opt-in, and it is isolated

`edit_outcome_policy.json` in the project root declares what the
project opted into. Its **absence means nothing is opted in**: the
default is off.

```json
{
  "format": "edit_outcome_policy/1",
  "project_learning": true,
  "media_learning": false,
  "read_by": ["plan_picture"]
}
```

- A **project-level** learning goes into `learned_context` - the store
  that already exists for what a run learned - names the step that
  reads it, and never leaves the project. A policy that names no
  reader records nothing: a learning nothing consumes is the defect
  AGENTS.md 10.4 names.
- A **media-level** learning carries **no free text**: the captain's
  words, the reel name and the project name are stripped. A shareable
  fact about media must not carry the client with it.
- Media learnings reach another project only when **both** projects
  grant each other, by the engine's existing cross-project policy
  (`model_evidence_provenance._mutual_project_share`). The opt-in is
  checked **before** the source is read: a project that has not
  granted access is never opened.

## Wiring

`reel_adoption` records the outcome when it appends the captain's
hand-edited generation to the version ledger: a hand edit is a product
outcome as well as a version. Recording it is instrumentation - the
adoption stands even when the outcome cannot be written.

## Reading it

```bash
python3 -m library.tools.edit_outcome <project>            # every reel
python3 -m library.tools.edit_outcome <project> --reel "Reel 26"
python3 -m library.tools.edit_outcome <project> --json
```

The record lives at `pipeline_output/edit_outcomes/edit_outcomes.json`
(`project_layout.Area.EDIT_OUTCOMES`), one key per reel.

## The proof

`scripts/edit_outcome_proof.py` builds a scratch reel - a Ren build
and then hand corrections recorded after it - and answers PROD-008's
five questions before and after the change:

```
BEFORE (base <sha>)
  the recorded state could answer the product question: no
  product questions answerable: 0 of 5
AFTER
  product questions answerable: 5 of 5
...
DENOMINATORS: 5 reels, 3 corrections; 1 objective failure(s),
1 stylistic disagreement(s), 1 unclassified; outcomes:
accepted=1, modified=3, unmeasured=1
```

The proof is over the engine's own RECORDED states - the same shapes a
live run writes - and it never opens Resolve or touches a project.

## Tests

`tests/unit/reels/test_edit_outcome.py`.
