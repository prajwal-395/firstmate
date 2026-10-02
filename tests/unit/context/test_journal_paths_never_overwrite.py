"""Every function that names a journal file names a NEW one inside the same second.

The stamp is second-granularity, so a verify-twice pass or a retry inside
one second would land the second record on top of the first - the only
record of an irreversible act. Parameterised over the REAL functions, not
the shared helper (`journal_path.unique_path`), so a seventh writer that
hand-rolls its path fails here. History: docs/evidence/journal_paths.md
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
