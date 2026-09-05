"""The identity of the code that produced a cached preflight value.

The problem this exists to remove
---------------------------------
The preflight cache is invalidated by the FOOTAGE and never by the CODE
that wrote it.  A fix to a preflight step - a new field, a corrected
measurement, a changed prompt - is silently invisible on any project
that already has a cache: the system reports success while the work did
not happen.  Three commits of work read a path that never fired.

The identity check here is the companion to
``library/tools/footage_identity.py``.  That one watches the footage;
this one watches the code.  Together they make "preflight is skipped
once done" safe rather than merely fast.

What this hashes
----------------
Every ``.py`` and ``.json`` file in the step's own directory, sorted by
name, excluding ``__pycache__``.  This is the step's declared source: its
``step.py``, ``bridge.py``, ``manifest.json`` and ``handoff.md`` are all
there.  ``.md`` files are excluded because they are prose documentation,
not executable code - a typo fix in a handoff doc should not cost 69
minutes of vision analysis.

What this deliberately does NOT hash
-------------------------------------
*Not the step's imports.*  A change to ``library/tools/vision_pipeline_v3.py``
does not change the hash of step 1.03.  This is the right trade: a shared
utility serves many steps, and a change to it should not invalidate all of
them.  When a utility change matters, the step that calls it must change
too (even a version bump in the manifest), and that changes its hash.

*Not the model weights.*  A WhisperX upgrade is not a code change to step
1.04.  The operator can ``--rerun temporal_index`` for that.

Adoption on first encounter
----------------------------
A ledger entry with no recorded ``code_hash`` is from before this check
existed.  It is ADOPTED - the current hash is recorded and the step is
not invalidated.  This matches the pattern ``apply_source_identity`` uses
for footage fingerprints (line 668-670 of run_pipeline.py), and avoids a
69-minute cold pass on the first run after the upgrade.

The trade-off, stated rather than hidden: an existing cached value
produced by buggy code survives one more run under adoption.  From the
second run onward, the hash is recorded and any code change invalidates
the cache.  An operator who wants to force the recompute can
``--rerun preflight`` once.
"""

import hashlib
import os
from pathlib import Path
from typing import Dict, Optional


# The extensions that count as executable source for a step.
# .md is deliberately excluded - a prose edit in handoff.md is not a code
# change and should not cost a 69-minute recomputation.
_SOURCE_EXTENSIONS = {".py", ".json"}


def step_code_hash(step_dir: str) -> Optional[str]:
    """A content hash of every source file in a step's directory.

    Returns None if the directory does not exist or contains no source
    files - callers treat that as "no identity to check".

    The hash is deterministic: files are sorted by name, each one
    contributes its relative name and its content, and the result is a
    single SHA-256 hex digest.
    """
    step_path = Path(step_dir)
    if not step_path.is_dir():
        return None

    digest = hashlib.sha256()
    found = False
    for child in sorted(step_path.iterdir()):
        if child.is_dir():
            continue
        if child.suffix not in _SOURCE_EXTENSIONS:
            continue
        found = True
        # Include the filename so that renaming a file changes the hash.
        digest.update(child.name.encode("utf-8"))
        digest.update(child.read_bytes())

    return digest.hexdigest() if found else None


def code_hashes_for(step_dirs: Dict[str, str]) -> Dict[str, str]:
    """{node_id: hash} for every step directory provided.

    A step whose directory is missing or empty is simply absent from the
    result.
    """
    out: Dict[str, str] = {}
    for node_id, step_dir in step_dirs.items():
        h = step_code_hash(step_dir)
        if h is not None:
            out[node_id] = h
    return out
