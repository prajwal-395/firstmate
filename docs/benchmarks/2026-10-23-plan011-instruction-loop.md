# PLAN-011 - the instruction loop, verified end to end

2026-10-23. The punch list's PLAN-011: "from a natural-language
request, prove that the agent inspects work state, gathers evidence,
compiles intent, validates a plan, executes, verifies, and reports", and
"ensure the final reply states the actual result rather than repeating
the original intention".

## What was built

One account, over the records the engine ALREADY keeps, of every stated
instruction a task carried and where each one ended up.

- `library/tools/instruction_loop.py` - the account, the seven stages of
  the loop, the final report, and the check that a reply states the
  RESULT.
- `library/tools/assignment_contract.py` - the completion gate reads the
  account, and a task is not complete while a stated instruction reached
  no verdict. A criterion may now name the instruction it carries
  (`instruction_id`).
- `library/tools/assignment_edit_workflow.py` - the plan-time gate: a
  plan must CARRY or DECLINE every instruction the intent states, and a
  decline carries a reason. This is the release gate, before the write.
- `library/tools/assignment_view.py` - `ren assignment-state` prints the
  account and names what reached no verdict.
- `library/tools/revise.py` - the reply is built from the record and
  checked against it, and a rebuild's summary states what was DONE
  rather than what was going to be done.

## What was wrong

A captain instruction reaches Ren as a sentence, and the engine keeps
five records of what was done with it: the canonical intent of what was
asked (PLAN-001), the plan that will serve it, the operations that ran
and their receipts, the acceptance contract and the evidence against it,
and the acceptance state the gate reached (QA-008).

Every one of those records was sound on its own, and none of them read
the others. So a task could serve ONE of the three requests its intent
carried, declare a contract of its own, finish with its criteria passed
and its delivery receipt clean - and report itself DONE while the other
two stated instructions had never been planned, never executed and never
mentioned. The gate read the contract; the contract did not know what the
person asked for.

## The measurement, and its denominators

`python3 evaluation/tools/plan011_instruction_loop.py`

### The corpus briefs (BASE-003)

13 briefs, 33 stated instructions (every acceptance criterion of every
brief).

| view | before | after |
|---|---|---|
| stated instructions the ENGINE's own verdict carries | **0/33** | **33/33** |
| stated instructions the RUN accounted for | 33/33 | 33/33 |

The two rows are different questions, and only the first one moved. The
second is what a careful agent could always reach - a criterion declared
with the same statement, with a verdict - and it is published so the
reader can see that what moved is the ENGINE's own account and not the
run's diligence. Before PLAN-011 the engine's verdict read the
acceptance contract and nothing else, so a stated instruction the
contract never carried had no entry anywhere: it was invisible.

The GATE, on a task that served one stated instruction and dropped the
other - a delivered task, its criteria passed, its receipt clean, so the
only thing that can hold it back is the account:

| | before | after |
|---|---|---|
| the task's own verdict | COMPLETE | NOT complete |
| the dropped instruction named | no | yes |

### The eight-brief set, over the live run's records

The only committed run data: the eight briefs of 2026-10-20, worked by
hand through the front door against a scratch copy of the podcast reel.

| view | before | after |
|---|---|---|
| the briefs that expect an EDIT | **15/22** | **22/22** |
| the briefs whose answer is to report back | 0/8 | 8/8 |
| all eight briefs | 15/30 | 30/30 |

The BEFORE column is the plan's own declaration - the only account the
engine had of a stated instruction, and the number the 2026-10-20 run
published ("15 of 22 acceptance criteria declared covered across the
five briefs that expect an edit"). The AFTER column is the end-to-end
account: a criterion the plan declared, or one the run's own evidence
settled, reaches a verdict either way.

| brief | criteria | before: declared | after: verdict | unaccounted |
|---|---|---|---|---|
| `keep-it-but-tighten-it` | 2 | 0 | 2 | - |
| `logo-not-in-the-project` | 3 | 0 | 3 | - |
| `name-bar-fights-the-captions` | 3 | 2 | 3 | - |
| `no-extra-footage` | 3 | 0 | 3 | - |
| `open-with-the-cost` | 3 | 1 | 3 | - |
| `the-whole-job` | 10 | 10 | 10 | - |
| `the-wrong-caption` | 3 | 1 | 3 | - |
| `two-voices-at-once` | 3 | 1 | 3 | - |

**The AFTER verdicts, over all 30: 0 met, 30 refused, 0 failed, 0
unaccounted.** The improvement is in the ACCOUNT, not in the run: that
run refused most of these briefs, and what changed is that the refusal
now reaches every stated instruction instead of the two thirds of them
the plan never mentioned. A refusal is a verdict.

## What was NOT measured

- **No run against the corpus briefs.** There are no task records for the
  thirteen BASE-003 briefs, so the RUN's account over the corpus is not
  measured - and it is not a zero. The corpus gave the denominator.
- **No live run of the eight briefs.** The records are the 2026-10-20
  ones. A plan that now DECLARES its coverage would change them, and
  nothing in this lane re-ran them: the corpus needs footage that
  carries each brief's premise, which is the substrate work the
  2026-10-21 report names.
- **No Resolve, no broker, no captain project.** The measurement is
  deterministic: the engine's own functions on scratch projects in a
  temporary directory, and the committed records. No timeline was read
  or written, and no media was touched.
- **The harness's own planning gate is unchanged.** The acceptance
  harness still fails a plan that is silent about a criterion, because
  that is a statement about a PLAN; this lane is about the end-to-end
  account, and the two are reported apart.

## The rule

A stated instruction is a requirement of the canonical intent, a request
of it, or a criterion of the acceptance contract. It is ACCOUNTED when
it reached a VERDICT - met, refused with a reason, or failed a check -
and a task is not DONE while one has not. Before the write, a plan must
carry or decline every instruction the intent states, and a decline
carries a reason. The final reply states the ACTUAL RESULT: it is built
from the record and checked against it, so a reply that restates the
note is refused as a result.

The incident is in
[docs/RULE_EVIDENCE.md](../RULE_EVIDENCE.md#the-task-that-dropped-half-the-request).
