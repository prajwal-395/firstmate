"""Canonical JSON for files git merges (AGENTS.md 3: run state).

Git merges text by lines. A JSON file with unstable key ordering
produces a conflict on every merge even when both sides mean the same
thing, and a file rewritten with different bytes but identical content
dirties the tree and earns a spurious per-build commit. Every writer
below emits one spelling:

- keys sorted (`sort_keys=True`), so the same content is the same bytes
  no matter which order the dict was built in - LLM bridge answers,
  `stored.update()` merges and ledger insertions all arrive in
  arbitrary order;
- `indent=2`, so one meaningful value sits on its own line and a
  same-line conflict means a real disagreement, not a reflow;
- a trailing newline, so the last line diffs like every other line.

`dumps_stable` / `dump_stable` / `write_stable` are that spelling.
`ensure_ascii` stays at json's default (`True`) unless the caller
already wrote non-ASCII (marker pulls), so adopting this changes key
order and the final newline and nothing else about the bytes.

What this does NOT do: make generated run state mergeable. Sorting
keys cannot help `pipeline_data.json` (`last_updated` churns on every
save, ledgers grow on both sides) or step outputs whose content an
LLM re-authors every run. Those files are rebuild-not-merge, and
`library/tools/timeline_variants.py` auto-resolves them at merge time
instead of asking a human to hand-merge them.
"""

from __future__ import annotations

import json
from pathlib import Path


def dumps_stable(obj, ensure_ascii: bool = True) -> str:
    """The canonical text: sorted keys, indent 2, trailing newline."""
    return (json.dumps(obj, indent=2, sort_keys=True,
                       ensure_ascii=ensure_ascii) + "\n")


def dump_stable(obj, handle, ensure_ascii: bool = True) -> None:
    """Write the canonical text to an open handle."""
    handle.write(dumps_stable(obj, ensure_ascii=ensure_ascii))


def write_stable(path, obj, ensure_ascii: bool = True) -> str:
    """Atomically write the canonical text to `path`. Returns str(path)."""
    path = Path(path)
    if path.parent and str(path.parent):
        path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(dumps_stable(obj, ensure_ascii=ensure_ascii),
                   encoding="utf-8")
    tmp.replace(path)
    return str(path)
