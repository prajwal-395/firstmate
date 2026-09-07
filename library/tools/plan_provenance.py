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

    Accepts both ``PlannedCaption`` objects (``start_seconds``,
    ``frames``, ``text``) and subtitle-entry dicts from step 4.01
    (``timeline_start``, ``timeline_end``, ``text``).  Frames are
    computed from the entry's own ``timeline_start``/``timeline_end``
    at 24000/1001 fps when the entry carries no ``frames`` field.

    **Raises ``ValueError`` when every card is contentless** - no text,
    no meaningful start and no frames.  That is what happened when the
    call site passed rendered segments instead of caption cards: fourteen
    empty dicts hashed identically to fourteen real cards, and the
    duration checks believed they had a baseline when they had none.
    """
    fps = 24000 / 1001
    digest = hashlib.sha256()
    any_content = False
    for card in cards:
        # -- start ------------------------------------------------
        start = getattr(card, "start_seconds", None)
        if start is None and isinstance(card, dict):
            start = card.get("timeline_start",
                             card.get("reel_start",
                                      card.get("start_seconds")))
        # -- frames (duration in frames) --------------------------
        frames = getattr(card, "frames", None)
        if frames is None and isinstance(card, dict):
            frames = card.get("frames")
        # Compute from timeline_start/timeline_end when no explicit
        # frames field exists (subtitle-entry dicts from step 4.01).
        if frames is None and isinstance(card, dict):
            tl_start = card.get("timeline_start")
            tl_end = card.get("timeline_end")
            if tl_start is not None and tl_end is not None:
                frames = max(int(round(
                    (float(tl_end) - float(tl_start)) * fps)), 1)
        # -- text -------------------------------------------------
        text = getattr(card, "text", None)
        if text is None and isinstance(card, dict):
            text = card.get("text", "")
        # Track whether at least one card carries real content.
        if text or (start is not None and start != 0.0) or (
                frames is not None and frames != 0):
            any_content = True
        digest.update(
            f"{float(start or 0.0):.3f}|{int(frames or 0)}|{text}\n"
            .encode("utf-8"))
    if cards and not any_content:
        raise ValueError(
            f"caption_content_hash was given {len(cards)} card(s) but "
            f"none carried text, a non-zero start or a non-zero frame "
            f"count. This means the caller passed rendered segments or "
            f"empty dicts instead of caption cards - the hash would be "
            f"hollow and indistinguishable from any other set of the "
            f"same size.")
    # Prefixed so hollow v0 hashes (bare hex, pre-fix) are trivially
    # distinguishable.  _is_v1_hash checks this prefix.
    return f"v1:{digest.hexdigest()}"


def footage_binding_hash(spine: dict) -> str:
    """A stable digest of the footage a reel's captions were computed against.

    The caption content hash (above) records WHAT was said and WHERE it
    appears in the reel.  This records WHERE IN THE SOURCE it came from:
    each speech block's ``clip_id``, ``source_start`` and ``source_end``.

    The two hashes answer different questions and neither replaces the
    other:

    - ``caption_content_hash`` changes when the text or timing changes.
    - ``footage_binding_hash`` changes when the PICTURE moves - a re-cut,
      a shifted boundary, or a different clip - even if the words stay
      identical.

    A caption that passes the content check but fails the binding check
    is a caption placed against footage that is no longer under it.
    That is exactly the defect the captain described: nothing bound them,
    so nothing noticed.

    The spine's ``structure`` is the source of truth.  Each block carries
    ``clip_id``, ``source_start``, ``source_end`` and ``timeline_start``,
    ``timeline_end`` under the spine contract (AGENTS.md section 6).
    Only speech and hook blocks are hashed - those are the ones that
    carry captions.

    **Raises ``ValueError``** when no block carries a clip_id, because
    a binding to nothing is not a binding.
    """
    digest = hashlib.sha256()
    any_binding = False
    for block in spine.get("structure", []):
        if block.get("block_type") not in ("speech", "hook"):
            continue
        clip_id = block.get("clip_id") or ""
        source_start = block.get("source_start")
        source_end = block.get("source_end")
        timeline_start = block.get("timeline_start")
        timeline_end = block.get("timeline_end")
        if clip_id and source_start is not None:
            any_binding = True
        digest.update(
            f"{clip_id}|{float(source_start or 0.0):.6f}|"
            f"{float(source_end or 0.0):.6f}|"
            f"{float(timeline_start or 0.0):.6f}|"
            f"{float(timeline_end or 0.0):.6f}\n"
            .encode("utf-8"))
    if not any_binding:
        raise ValueError(
            "footage_binding_hash was given a spine whose speech/hook "
            "blocks carry no clip_id. A binding to nothing is not a "
            "binding - the hash would be a digest of empty strings and "
            "would match any other spine with no bindings.")
    return f"v1:{digest.hexdigest()}"


_V1_PREFIX = "v1:"


def _is_v1_hash(h: str) -> bool:
    """True when ``h`` was produced by the fixed caption_content_hash."""
    return isinstance(h, str) and h.startswith(_V1_PREFIX)


def write_provenance(
    review_dir: str,
    plan_path: str,
    reel_names: list[str],
    caption_hashes: Optional[dict] = None,
    footage_binding_hashes: Optional[dict] = None,
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

    `footage_binding_hashes` records, per reel, a digest of the footage
    identity (clip_id, source_start, source_end) the captions were
    computed against. A rebuild updates only its own reel's entry.

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
    bindings = dict(footage_binding_hashes or {})

    if existing:
        if existing.get("plan_content_hash") == content_hash:
            built |= set(existing.get("built_reels") or [])
            merged = dict(existing.get("caption_hashes") or {})
            merged.update(captions)
            captions = merged
            merged_bindings = dict(
                existing.get("footage_binding_hashes") or {})
            merged_bindings.update(bindings)
            bindings = merged_bindings
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
        # Per reel: the footage each reel's captions were computed
        # against. A caption whose content hash matches but whose
        # footage binding does not is placed against footage that moved.
        "footage_binding_hashes": bindings,
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
    # A v0 hash (pre-fix) was computed from rendered segments that
    # carried no card content, so it is effectively the hash of the
    # segment COUNT and nothing else.  Detect it: a v1 hash always
    # starts from a version-tagged state, so it can never equal a v0
    # hash even if the content coincides.  Treat the hollow record as
    # absent so the duration checks refuse rather than falsely pass.
    if not _is_v1_hash(recorded):
        return False, (
            f"{reel_name}: the recorded caption hash is a hollow v0 "
            f"digest that was computed from rendered segments rather "
            f"than caption cards. It carries no card text, start or "
            f"length and would match any set of the same count. "
            f"Treated as absent - caption durations are NOT graded. "
            f"Rebuild to record a genuine baseline.")
    actual = caption_content_hash(cards)
    if actual == recorded:
        return True, f"{reel_name}: caption plan matches provenance"
    return False, (
        f"{reel_name}: the caption plan derived now ({actual[:16]}...) "
        f"is not the one this timeline was built from "
        f"({recorded[:16]}...) - the card grouping has changed since "
        f"the build. Caption durations are NOT graded.")


def check_footage_binding_matches_provenance(
    reel_name: str,
    spine: dict,
    provenance: Optional[dict],
) -> tuple[bool, str]:
    """Do the captions still belong to the footage under them.

    The footage binding check answers a different question from the
    caption content check: a caption whose text and timing are unchanged
    can still be WRONG if the picture underneath moved.  This detects
    that break.

    Returns ``(bound, reason)``.  ``False`` means the footage has changed
    since the captions were computed - the captions no longer match the
    picture.  The caller must report the break rather than skip quietly.
    """
    if not provenance:
        return False, (
            f"{reel_name}: no provenance record exists, so nothing "
            f"states which footage the captions were computed against. "
            f"Footage binding is NOT checked.")
    recorded = (provenance.get("footage_binding_hashes") or {}).get(reel_name)
    if not recorded:
        return False, (
            f"{reel_name}: provenance records no footage binding for "
            f"this reel. The captions may belong to different footage. "
            f"Rebuild to record a binding baseline.")
    if not _is_v1_hash(recorded):
        return False, (
            f"{reel_name}: the recorded footage binding hash is a "
            f"pre-v1 digest. Treated as absent.")
    actual = footage_binding_hash(spine)
    if actual == recorded:
        return True, (
            f"{reel_name}: footage binding matches provenance - "
            f"captions are still paired with their footage")
    return False, (
        f"{reel_name}: the footage under the captions has changed. "
        f"Binding now ({actual[:16]}...) differs from the build "
        f"({recorded[:16]}...). The captions were computed against "
        f"different footage and are no longer paired.")


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


def archived_timeline_names(review_dir: str, plan_stem: str = "reel_proposals_v2") -> set[str]:
    """Every timeline name any ARCHIVED plan ever asked for.

    `archive_plan` writes one timestamped copy per selection, so the
    archive directory is the only surviving account of what earlier plans
    named.  The live record (`built_reels`) drops those entries the
    moment the plan hash changes - deliberately, because carrying them
    forward would assert a provenance that never existed - and this is
    how a reel built from a replaced plan can still be told apart from
    one nothing ever planned.

    Read as raw JSON rather than through `reel_proposal.read_proposal`,
    because an archive is a record of what an OLD selector wrote and a
    schema the current reader rejects would make the whole archive
    silently empty.  A file that cannot be parsed is skipped and does
    not take the rest with it.

    Names ONLY - approval is not read.  A moment the captain rejected
    still got a name, and a timeline carrying that name was still built
    from that plan.

    A bare LIST of moments is accepted alongside the `{"moments": [...]}`
    document, because an archive is whatever an OLD selector wrote and
    both shapes are on disk.  Anything else in the file is skipped
    rather than raising: one unreadable archive must not empty the rest.
    """
    names: set[str] = set()
    directory = Path(review_dir)
    if not directory.is_dir():
        return names
    for path in sorted(directory.glob(f"{plan_stem}_*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        moments = doc if isinstance(doc, list) else (doc.get("moments") or [])
        for moment in moments:
            if not isinstance(moment, dict):
                continue
            name = moment.get("timeline_name")
            if name:
                names.add(str(name))
    return names
