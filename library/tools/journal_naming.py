"""How an irreversible act's journal is NAMED, in ONE place.

Every destructive operation under `library/tools/execution/` writes a
journal so the act can be undone, and the name of that file is the whole
of what protects it: a fixed name lets the next run write over the record.

Six modules answered this question separately - `build_sweep`,
`organise_media_pool`, `mark_master`, `retire_empty_bins`, `remove_proof`
and `prune_orphans` - and every one of the six carried a docstring saying
a fixed filename was wrong.  Two of them had actually done something
about it.  The other four still named one file per SECOND, so a second
run inside that second destroyed the first run's journal.  Measured
2026-09-12 (`tests/unit/context/test_ledgers.py`): five of seven
path functions returned the same path twice.

Two of the six had already paid for the lesson in real data:

- `remove_proof`, 2026-09-10: nine consecutive removals landed in five
  journal files.
- `build_sweep`, 2026-09-10: a verify-twice run put its empty second pass
  on top of the first pass's 102-file record.

Both wrote their loop locally rather than somewhere the other four could
reach it, which is why the other four still had the defect two days
later.  That is the whole reason this module exists; it is not a wrapper
for tidiness.

The stamp stays human-readable - an operator reads these filenames to
pick which apply to undo - so only a disambiguator is appended:

    resolve_prune_20260912T101500Z.json
    resolve_prune_20260912T101500Z_2.json
    resolve_prune_20260912T101500Z_3.json

`JOURNAL_GLOB`-style patterns (`f"{PREFIX}*.json"`) still match, because
the suffix lands before the extension.

The module is `journal_naming`, not `journal_path`: `journal_path` is a
PARAMETER NAME in four of the six callers (`prune_orphans.remove_pool_items`,
`revert`, `files_from_journal`, `remove_proof`), and a module import by that
name would shadow it inside those functions.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

__all__ = ["unique_path", "utc_stamp"]


def utc_stamp() -> str:
    """The second-granularity UTC stamp every journal name is built from."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def unique_path(directory: str, prefix: str, when: str | None = None,
                suffix: str = ".json") -> str:
    """A path under `directory` that no existing file already occupies.

    `prefix` is the operation's own journal prefix (its `JOURNAL_PREFIX`);
    `when` overrides the stamp, which is what lets a test ask twice for
    one second.  `suffix` covers the `_manifest.md` written beside a
    journal, so a journal and its manifest are disambiguated the same way
    rather than one of the pair silently reusing a name.

    The check is `os.path.exists`, not a lock: these operations are
    serialised by the Resolve placement rule (`resolve_lock`) and run one
    at a time, so the collision being guarded is a SECOND RUN, not a
    concurrent one.
    """
    stamp = when or utc_stamp()
    base, ext = (prefix + "_" + stamp, suffix)
    candidate = os.path.join(directory, f"{base}{ext}")
    sibling = 2
    while os.path.exists(candidate):
        candidate = os.path.join(directory, f"{base}_{sibling}{ext}")
        sibling += 1
    return candidate
