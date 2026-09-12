"""Six modules answered "where does this journal go", and four could lose one.

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
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

# (import path, attribute) for every function that names a journal file.
JOURNAL_PATH_FUNCTIONS = [
    ("library.tools.build_sweep", "journal_path_for"),
    ("library.tools.execution.organise_media_pool", "journal_path_for"),
    ("library.tools.execution.mark_master", "journal_path_for"),
    ("library.tools.execution.retire_empty_bins", "journal_path_for"),
    ("library.tools.execution.remove_proof", "journal_path_for"),
    ("library.tools.execution.prune_orphans", "journal_path_for"),
    ("library.tools.execution.prune_orphans", "manifest_path_for"),
]


@pytest.mark.parametrize("module_name,attr", JOURNAL_PATH_FUNCTIONS,
                         ids=[f"{m.rsplit('.', 1)[-1]}.{a}"
                              for m, a in JOURNAL_PATH_FUNCTIONS])
def test_a_second_journal_in_the_same_second_does_not_overwrite_the_first(
        module_name, attr, tmp_path):
    """Two calls with ONE stamp must name two different files.

    The second call only differs once the first file EXISTS - that is the
    real sequence, because the caller writes before it asks again - so the
    test writes the first journal before asking for the second.
    """
    module = importlib.import_module(module_name)
    name_it = getattr(module, attr)
    stamp = "20260912T101500Z"

    first = Path(name_it(str(tmp_path), stamp))
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_text(json.dumps({"record": "the first run's 102 files"}),
                     encoding="utf-8")

    second = Path(name_it(str(tmp_path), stamp))

    assert second != first, (
        f"{module_name}.{attr} named the SAME file twice inside one second.\n"
        f"  {first}\n"
        "The first journal is the only record of an irreversible act and the "
        "second write destroys it. Name the file through "
        "library/tools/journal_path.unique_path, which suffixes past a "
        "collision."
    )
    assert first.exists(), "the first journal must survive being asked again"
    assert second.parent == first.parent, (
        "the sibling journal must land beside the first, not somewhere else")
