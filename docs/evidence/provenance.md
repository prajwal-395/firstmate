# `library.tools.provenance` - operations recorded as truthfully as steps

Moved verbatim from `tests/unit/context/test_provenance_operations.py` (test-suite
halving, 2026-10-02). The tests keep the invariants; this keeps the history.

## Module docstring

```text
An OPERATION that writes a file is recorded as truthfully as a step is.

Increment 2 of the LLM-native refactor
--------------------------------------
`docs/architecture/llm_native_design.md` establishes that the runner
wraps eighteen services around every step body and that every one of
them is keyed by a DAG node id, so anything invoked outside the DAG gets
none of them.  Provenance is the ONE of the eighteen that already
generalises, because it attributes a file by DIFFING A DIRECTORY
SNAPSHOT rather than by anything the producer declares - it does not
care what ran.  That is why it is the cheapest first thread, and this
is it.

What is actually being tested here
----------------------------------
Not that the happy path works - that was nearly free.  The properties
worth pinning are the REFUSALS, because the whole design rests on the
claim that the new layers are subject to AGENTS.md 10.4 rather than
exempt from it, and a contract never observed refusing is the
vacuous-gate defect this repository has already been bitten by three
times.

So every refusal below is paired with the same call SUCCEEDING once the
thing it complained about is supplied.  A gate that always says no is no
more evidence than one that always says yes.
```

## The refusal that was once asserted the other way

```text
The correction. This test asserted the opposite and was wrong.

    It originally read `test_a_ledger_that_declares_nothing_does_not
    _refuse`, on the reasoning that a ledger with no declarations should
    stay usable rather than refuse everything. That reasoning produced a
    guard that was OFF BY DEFAULT - `if self.operation_ids and ...`
    checks nothing when the list is absent - which is the
    gate-that-cannot-fail defect this whole change exists to remove,
    sitting inside the change itself. Found by cross-PR measurement, not
    by this suite, which is the honest thing to record about it.

    A producer that cannot be CHECKED is not recorded as fact. That is
    the same reasoning `UNKNOWN` already rests on.
```
