# Journal paths that overwrote the record an undo needs - history

Moved verbatim from the module docstring of
`tests/test_journal_paths_never_overwrite.py` (test-suite halving,
2026-10-02). The test keeps the invariant; this keeps the measurement.

```text
Six modules answered "where does this journal go", and four could lose one.

Every irreversible act in `library/tools/execution/` writes a journal so it
can be undone, and every one of the six implementations carried a docstring
saying a fixed filename was wrong because "a later run overwrites the record
an undo needs".  Only TWO of the six actually did anything about it.

Measured 2026-09-12 on `main` (244158bc), by calling each function twice with
the same UTC stamp:

    build_sweep.journal_path_for              suffixed  ->  _2   OK
    execution/remove_proof.journal_path_for   suffixed  ->  _2   OK
    execution/organise_media_pool             SAME PATH BOTH TIMES
    execution/mark_master                     SAME PATH BOTH TIMES
    execution/retire_empty_bins               SAME PATH BOTH TIMES
    execution/prune_orphans                   SAME PATH BOTH TIMES
    execution/prune_orphans.manifest_path_for SAME PATH BOTH TIMES

The stamp is second-granularity, so two runs inside one second - which is
exactly what a verify-twice pass or a `journals()`-driven retry does - land
the second record on top of the first.  `remove_proof` measured nine
consecutive removals landing in five journal files before it grew its own
loop; `build_sweep` measured an empty second pass overwriting a 102-file
record.  Both wrote the fix locally instead of where the other four could
reach it, which is why the other four still had the defect two days later.

This test is the mechanism the docstrings never had.  It is deliberately
parameterised over the REAL functions rather than over the shared helper, so
a seventh journal writer that hand-rolls its own path fails here.
```
