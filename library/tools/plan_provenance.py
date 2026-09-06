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


def caption_content_hash(cards) -> str:
    """A stable digest of the caption cards a build actually placed.

    The MOMENT plan is a file on disk and hashes itself. The caption
    plan is derived at build time and was never written down, so
    `check_plan_matches_provenance` could report a clean match while the
    card grouping underneath had changed completely - measured on the
    captain's nineteen, the moment hash matched and today's planner
    produced 39 cards where the build had placed 28.

    Hashed from what a comparison actually needs: each card's start, its
    length and its text, in play order. Styling is deliberately out -
    a caption's look belongs to the project (AGENTS.md 14) and no
    duration check reads it, so a restyle must not read as a different
    plan.
    """
    digest = hashlib.sha256()
    for card in cards:
        start = getattr(card, "start_seconds", None)
        if start is None and isinstance(card, dict):
            start = card.get("reel_start", card.get("start_seconds"))
        frames = getattr(card, "frames", None)
        if frames is None and isinstance(card, dict):
            frames = card.get("frames")
        text = getattr(card, "text", None)
        if text is None and isinstance(card, dict):
            text = card.get("text", "")
        digest.update(
            f"{float(start or 0.0):.3f}|{int(frames or 0)}|{text}\n"
            .encode("utf-8"))
    return digest.hexdigest()


def write_provenance(
    review_dir: str,
    plan_path: str,
    reel_names: list[str],
    caption_hashes: Optional[dict] = None,
) -> str:
    """Record which plan the builder used and which reels it built.

    **A PARTIAL rebuild MERGES; it never replaces the whole record.**
    This wrote `built_reels: sorted(reel_names)` unconditionally, so
    rebuilding one reel replaced a nineteen-reel record with a one-reel
    record and the other eighteen lost their provenance - after which
    `check_reels_in_provenance` reports them missing and every check
    that depends on it is grading against a baseline that was silently
    deleted. Data loss wearing the shape of a write.

    So an existing record is read first and this build's reels are
    UNIONED into it. `caption_hashes` is per reel, so a rebuilt reel
    updates only its own entry and its neighbours keep theirs.

    **A different plan is the one case that does NOT merge.** If the
    stored `plan_content_hash` differs, the old entries describe reels
    built from a plan this one is not, and carrying them forward would
    assert a provenance that never existed. Those are dropped and the
    record says so in `superseded_plan_hash`.

    Returns the path to the provenance file.
    """
    content_hash = plan_content_hash(plan_path)
    existing = read_provenance(review_dir) or {}
    superseded = None
    built = set(reel_names)
    captions = dict(caption_hashes or {})

    if existing:
        if existing.get("plan_content_hash") == content_hash:
            built |= set(existing.get("built_reels") or [])
            merged = dict(existing.get("caption_hashes") or {})
            merged.update(captions)
            captions = merged
        else:
            superseded = existing.get("plan_content_hash")

    doc = {
        "plan_path": os.path.abspath(plan_path),
        "plan_content_hash": content_hash,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "built_reels": sorted(built),
        # Per reel, because a partial rebuild must not speak for its
        # neighbours. A reel with no entry has no recorded caption plan,
        # which is a REFUSAL to grade rather than a pass.
        "caption_hashes": captions,
    }
    if superseded:
        doc["superseded_plan_hash"] = superseded
    out = Path(review_dir) / PROVENANCE_FILENAME
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return str(out)


def check_captions_match_provenance(
    reel_name: str,
    cards,
    provenance: Optional[dict],
) -> tuple[bool, str]:
    """May caption durations be graded on this reel, and why not.

    REFUSES on absence. A check that grades against an unknown baseline
    is the defect this whole file exists to stop: F2 produced 701
    findings against a caption plan nobody had recorded, and every one
    of them read like a placement defect.

    Returns `(may_grade, reason)`. `False` is never "the captions are
    wrong" - it is "nothing here can say", and the caller must report
    the refusal rather than skip quietly.
    """
    if not provenance:
        return False, (
            f"{reel_name}: no provenance record exists, so nothing "
            f"states which caption cards this timeline was built from. "
            f"Caption durations are NOT graded - a baseline that is "
            f"re-derived from today's code is not the one the build "
            f"used.")
    recorded = (provenance.get("caption_hashes") or {}).get(reel_name)
    if not recorded:
        return False, (
            f"{reel_name}: provenance records the moment plan but no "
            f"caption plan for this reel, so the card grouping the "
            f"build placed is unknown. Caption durations are NOT "
            f"graded. Rebuild it, or accept that F2 and F14 cannot "
            f"speak to this timeline.")
    actual = caption_content_hash(cards)
    if actual == recorded:
        return True, f"{reel_name}: caption plan matches provenance"
    return False, (
        f"{reel_name}: the caption plan derived now ({actual[:16]}...) "
        f"is not the one this timeline was built from "
        f"({recorded[:16]}...) - the card grouping has changed since "
        f"the build. Caption durations are NOT graded.")


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
