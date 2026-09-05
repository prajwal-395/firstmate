"""Plan provenance: which plan produced which built reels.

The problem
-----------
Each selector run overwrites ``reel_proposals_v2.json`` with a new
selection of different moments.  The conformance verifier grades built
timelines against whatever plan is current - even if that plan describes
completely different spans.  The result is confident, precise, and
meaningless: 42 errors about a reel that matches its ACTUAL plan perfectly.

Two halves of the fix
---------------------
1. **Archival** - every selection is written to a timestamped copy
   alongside the live file, so old plans survive.
2. **Provenance** - the builder records WHICH plan it built from
   (path and content hash), and the verifier refuses when the plan
   does not match the timelines.

Identity
--------
A plan is identified by the SHA-256 of its canonical JSON content.  This
cannot collide: reel names can repeat across selections, but a different
set of spans or a different count of moments produces a different hash.

Ownership
---------
The BUILDER writes the provenance, not the selector.  The selector does
not know which plan will actually be built; the builder is the one that
commits "I built from THIS plan", so it is the natural owner of that
record.

Retroactive fitness
-------------------
The 16 reels built before this change have no provenance record.  They
are honestly lost to verification against a specific plan - the verifier
says so plainly rather than inventing a reconstruction.

``tests/test_plan_provenance.py``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def plan_content_hash(plan_path: str) -> str:
    """SHA-256 of the plan file's content.

    The hash is over the raw bytes, not a parsed re-serialization, so it
    is reproducible and independent of JSON key ordering in memory.
    """
    data = Path(plan_path).read_bytes()
    return hashlib.sha256(data).hexdigest()


def archive_plan(plan_path: str, archive_dir: Optional[str] = None) -> str:
    """Copy the plan to a timestamped archive file.

    Returns the path to the archived copy.  The archive sits alongside
    the live file (or in ``archive_dir`` if given), named with an ISO
    timestamp so it sorts chronologically and never collides.

    The live file is NOT deleted or renamed - callers that overwrite it
    do so independently.
    """
    plan = Path(plan_path)
    if not plan.is_file():
        raise FileNotFoundError(f"Plan file does not exist: {plan_path}")

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = plan.stem           # e.g. "reel_proposals_v2"
    suffix = plan.suffix       # e.g. ".json"
    archive_name = f"{stem}_{ts}{suffix}"

    dest_dir = Path(archive_dir) if archive_dir else plan.parent
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / archive_name

    shutil.copy2(str(plan), str(dest))
    return str(dest)


# ---- Provenance sidecar ---------------------------------------------------

PROVENANCE_FILENAME = "plan_provenance.json"
"""Written by the builder next to conformance_report.json."""


def write_provenance(
    review_dir: str,
    plan_path: str,
    reel_names: list[str],
) -> str:
    """Record which plan the builder used and which reels it built.

    Returns the path to the provenance file.
    """
    content_hash = plan_content_hash(plan_path)
    doc = {
        "plan_path": os.path.abspath(plan_path),
        "plan_content_hash": content_hash,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "built_reels": sorted(reel_names),
    }
    out = Path(review_dir) / PROVENANCE_FILENAME
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return str(out)


def read_provenance(review_dir: str) -> Optional[dict]:
    """Load the provenance sidecar, or None if it does not exist."""
    p = Path(review_dir) / PROVENANCE_FILENAME
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def check_plan_matches_provenance(
    plan_path: str,
    provenance: dict,
) -> tuple[bool, str]:
    """Compare a plan file against recorded provenance.

    Returns (matches, reason).
    """
    actual_hash = plan_content_hash(plan_path)
    expected_hash = provenance.get("plan_content_hash", "")
    if actual_hash == expected_hash:
        return True, "plan content hash matches provenance"
    return False, (
        f"plan content hash {actual_hash[:16]}... does not match "
        f"provenance {expected_hash[:16]}... - the plan has changed "
        f"since these reels were built"
    )


def check_reels_in_provenance(
    reel_names: list[str],
    provenance: dict,
) -> tuple[bool, list[str]]:
    """Check which reel names appear in the provenance record.

    Returns (all_present, missing_names).
    """
    built = set(provenance.get("built_reels", []))
    missing = [name for name in reel_names if name not in built]
    return len(missing) == 0, missing
