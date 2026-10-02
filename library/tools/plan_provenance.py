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
------------------
The 16 reels built before this change have no provenance record.  They
are honestly lost to verification against a specific plan - the verifier
says so plainly rather than inventing a reconstruction.

Snapshot supersession
---------------------
Several `.timeline.json` snapshots can name the SAME live timeline: the
base snapshot, a `(batch-1050)` comparison copy, a `(final)` copy.
Measured 2026-09-19 they disagreed on Reel 09's bound (36510 against
36490) with nothing on disk saying which was authoritative, and a check
graded the stale one.  So promotion records, per live timeline name,
which snapshot file is the authoritative record of that timeline
(`snapshot_provenance[timeline]`) and which same-named files it
superseded.  The readers (`built_from_snapshot`,
`is_snapshot_superseded`) refuse where nothing was recorded rather
than grading a stale file.  This key lives here rather than in the
snapshot metadata because the fact belongs to no single snapshot file:
marking old files would rewrite committed history, while one
builder-owned record diffs as a one-hunk change beside the declaration
it was built from.

``tests/unit/context/test_snapshot_supersession.py``.

``tests/unit/context/test_plan_provenance.py``.
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


#: The producing code a reel build is identified by: the builder
#: itself plus the two DAG-step directories that call it. Hashed with
#: `code_identity.step_code_hash`'s shape - directory sources plus
#: named extras - so a fix to any of them changes the digest exactly
#: the way a preflight code change invalidates its cache. A git SHA
#: would lie here: lanes build from unmerged worktrees, so the commit
#: a reel was built from is not the code that built it.
REEL_BUILD_CODE_FILES = (
    "library/tools/reel_build.py",
    "library/steps/step_7_01_build_reels",
    "library/steps/step_7_02_verify_reels",
)


def reel_code_hash(repo_root=None) -> Optional[str]:
    """A content digest of the code that builds reels, or None.

    `None` when the tree is absent (tests hashing a scratch root that
    holds no engine) - callers record the absence rather than a
    hollow digest, so "no engine to hash" never reads as one revision.
    """
    from library.tools import code_identity as _identity

    root = Path(repo_root) if repo_root is not None \
        else Path(__file__).resolve().parents[2]
    digest = hashlib.sha256()
    found = False
    for rel in REEL_BUILD_CODE_FILES:
        target = root / rel
        if target.is_dir():
            part = _identity.step_code_hash(str(target))
        elif target.is_file():
            part = hashlib.sha256(target.read_bytes()).hexdigest()
        else:
            continue
        found = True
        digest.update(rel.encode("utf-8"))
        digest.update(part.encode("utf-8"))
    return digest.hexdigest() if found else None


def archive_plan(plan_path: str, archive_dir: Optional[str] = None,
                 only_reel_numbers=None) -> str:
    """Copy the plan, or the selected reel entries, to an archive file.

    Returns the path to the archived copy.  The archive sits alongside
    the live file (or in ``archive_dir`` if given), named with an ISO
    timestamp so it sorts chronologically and never collides.

    The live file is NOT deleted or renamed - callers that overwrite it
    do so independently.
    """
    plan = Path(plan_path)
    if not plan.is_file():
        raise FileNotFoundError(f"Plan file does not exist: {plan_path}")

    numbers = (None if only_reel_numbers is None else
               {int(number) for number in only_reel_numbers})
    ts_format = "%Y%m%dT%H%M%S%fZ" if numbers is not None else \
        "%Y%m%dT%H%M%SZ"
    ts = datetime.now(timezone.utc).strftime(ts_format)
    stem = plan.stem           # e.g. "reel_proposals_v2"
    suffix = plan.suffix       # e.g. ".json"
    scope = ("_reel_" + "_".join(map(str, sorted(numbers)))
             if numbers is not None else "")
    archive_name = f"{stem}{scope}_{ts}{suffix}"

    dest_dir = Path(archive_dir) if archive_dir else plan.parent
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / archive_name

    if numbers is None:
        shutil.copy2(str(plan), str(dest))
    else:
        document = json.loads(plan.read_text(encoding="utf-8"))
        if isinstance(document, list):
            document = [moment for moment in document
                        if int(moment["number"]) in numbers]
        else:
            document["moments"] = [
                moment for moment in (document.get("moments") or ())
                if int(moment["number"]) in numbers]
        dest.write_text(json.dumps(document, indent=2) + "\n",
                        encoding="utf-8")
    return str(dest)


# ---- Provenance sidecar ---------------------------------------------------

PROVENANCE_FILENAME = "plan_provenance.json"
"""Written by the builder next to conformance_report.json."""

SNAPSHOT_PROVENANCE_KEY = "snapshot_provenance"
REN_TIMELINE_SNAPSHOTS_KEY = "ren_timeline_snapshots"
EDITOR_CHANGES_KEY = "unattributed_editor_changes"
TIMELINE_INVENTORY_KEY = "timeline_inventory_history"
EDITOR_TIMELINES_KEY = "editor_timeline_identities"
"""Per live timeline name: which snapshot file is its authoritative
record and which same-named files that snapshot superseded.

``{timeline_name: {"snapshot": <review/-relative filename>,
"sha256": <hex of the file's bytes when recorded>,
"recorded_at": <iso>,
"superseded": [{"snapshot": <filename>, "sha256": <hex>,
"superseded_by": <filename>, "recorded_at": <iso>}]}}``.

A builder-owned record, written by `record_snapshot_supersession` at
promotion time - the only moment "what the live timeline holds" and
"which files name it" are both known.  Readers refuse on absence
rather than grading a stale file.
"""

CARRIED_EDITS_KEY = "carried_editor_edits"
"""Per final timeline name: the editor's timeline edits every rebuild,
swap and variant choice re-applies (`library/tools/editor_edit_carry.py`).
"""


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
    asset_hashes: Optional[dict] = None,
    build_signatures: Optional[dict] = None,
) -> str:
    """Serialize concurrent per-reel provenance merges for this project."""
    from library.tools.project_file_lock import lock_project_file

    path = Path(review_dir) / PROVENANCE_FILENAME
    with lock_project_file(path):
        return _write_provenance_unlocked(
            review_dir, plan_path, reel_names, caption_hashes,
            footage_binding_hashes, asset_hashes, build_signatures)


def _write_provenance_unlocked(
    review_dir: str,
    plan_path: str,
    reel_names: list[str],
    caption_hashes: Optional[dict] = None,
    footage_binding_hashes: Optional[dict] = None,
    asset_hashes: Optional[dict] = None,
    build_signatures: Optional[dict] = None,
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

    `built_at_reels` and `built_with` are per reel for the same
    reason, and are captured HERE - at build time - because neither
    can be recovered afterwards. Merge time is not build time (lanes
    build from unmerged worktrees), so a file-level stamp cannot say
    which engine revision a reel was built against once six of them
    silently diverge. A rebuilt reel's stamp is replaced; its
    neighbours keep theirs.

    `build_signatures` is per reel and is how the NEXT build answers
    "does this reel need a Resolve pass at all"
    (`library/tools/reel_rebuild_need.py`): `{reel: {"derivation":
    hex, "carried": hex}}`. Written here with the carried half still
    empty - the build places a STAGING container, so the only moment a
    reel's carried state means "what is approved" is after promotion,
    and `record_carried_digests` closes it there. Merged per reel for
    the same reason every other per-reel map here is.

    `asset_hashes` maps each declared asset's absolute path to the
    digest of its bytes at build time. Assets are project-shared, not
    per reel, so a provided mapping REPLACES the recorded one whole -
    it is this build's declaration set, authoritative now - while None
    (a caller with no declaration set) keeps what is there.

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
    now = datetime.now(timezone.utc).isoformat()
    code_hash = reel_code_hash()
    built_at_reels = {name: now for name in reel_names}
    built_with = ({name: code_hash for name in reel_names}
                  if code_hash is not None else {})
    assets = (dict(asset_hashes) if asset_hashes is not None
              else dict(existing.get("asset_hashes") or {}))
    signatures = dict(build_signatures or {})

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
            merged_at = dict(existing.get("built_at_reels") or {})
            merged_at.update(built_at_reels)
            built_at_reels = merged_at
            merged_with = dict(existing.get("built_with") or {})
            merged_with.update(built_with)
            built_with = merged_with
            merged_signatures = dict(
                existing.get("build_signatures") or {})
            merged_signatures.update(signatures)
            signatures = merged_signatures
            if asset_hashes is None:
                assets = dict(existing.get("asset_hashes") or {})
        else:
            superseded = existing.get("plan_content_hash")
            assets = dict(asset_hashes) if asset_hashes is not None else {}

    # The snapshot record is orthogonal to the plan: a plan change does
    # not un-build a timeline, so the table is carried whole in both
    # branches. A build that dropped it would delete the supersession
    # answer as a side effect of recording anything else.
    snapshots_table = dict(existing.get(SNAPSHOT_PROVENANCE_KEY) or {})
    ren_snapshots = dict(existing.get(REN_TIMELINE_SNAPSHOTS_KEY) or {})
    editor_changes = dict(existing.get(EDITOR_CHANGES_KEY) or {})
    inventory_history = list(existing.get(TIMELINE_INVENTORY_KEY) or [])
    editor_timelines = dict(existing.get(EDITOR_TIMELINES_KEY) or {})
    carried_edits = dict(existing.get(CARRIED_EDITS_KEY) or {})

    doc = {
        "plan_path": os.path.abspath(plan_path),
        "plan_content_hash": content_hash,
        "built_at": now,
        "built_reels": sorted(built),
        # Per reel, because a partial rebuild must not speak for its
        # neighbours. A reel with no entry has no recorded caption plan,
        # which is a REFUSAL to grade rather than a pass.
        "caption_hashes": captions,
        # Per reel: the footage each reel's captions were computed
        # against. A caption whose content hash matches but whose
        # footage binding does not is placed against footage that moved.
        "footage_binding_hashes": bindings,
        # Per reel: WHEN this reel was built, and WHICH engine code
        # built it. Absent on records written before this half existed
        # - which is said, never backfilled, because a stamp invented
        # after the fact is not a measurement.
        "built_at_reels": built_at_reels,
        "built_with": built_with,
        # Per reel: the two digests that answer whether the NEXT build
        # needs to place this reel again at all
        # (`library/tools/reel_rebuild_need.py`). A reel with no entry,
        # or with either half empty, is REBUILT - the decision is
        # fail-closed, so an absent record costs a placement rather
        # than risking a reel that needed one.
        "build_signatures": signatures,
        # Per declared asset path: the digest of its bytes at build
        # time, so a file replaced on disk reads as changed rather
        # than current (`reel_divergence` compares these).
        "asset_hashes": assets,
        # Per live timeline name: the authoritative snapshot file and
        # the same-named files it superseded
        # (`record_snapshot_supersession`). Carried, never recomputed
        # here - a build records plan facts, promotion records
        # snapshot facts, and neither rewrites the other's.
        SNAPSHOT_PROVENANCE_KEY: snapshots_table,
        REN_TIMELINE_SNAPSHOTS_KEY: ren_snapshots,
        EDITOR_CHANGES_KEY: editor_changes,
        TIMELINE_INVENTORY_KEY: inventory_history,
        EDITOR_TIMELINES_KEY: editor_timelines,
        CARRIED_EDITS_KEY: carried_edits,
    }
    if superseded:
        doc["superseded_plan_hash"] = superseded
    out = Path(review_dir) / PROVENANCE_FILENAME
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return str(out)


def rename_reel_entries(review_dir: str, mapping: dict[str, str]) -> None:
    """Rename reel keys in the provenance sidecar, staging -> final.

    A staged build records its caption and footage-binding hashes under
    the staging container it actually placed, so the gate grades the
    staging against a recorded baseline. Promotion renames the claim to
    the final timeline name: the cards and the footage did not change,
    only the container's name did. Entries for reels outside `mapping`
    are left exactly as they are - the same merge rule `write_provenance`
    keeps for a partial rebuild.
    """
    if not mapping:
        return
    path = Path(review_dir) / PROVENANCE_FILENAME
    if not path.is_file():
        raise ValueError(
            f"cannot promote reels: no provenance record at {path}")
    doc = json.loads(path.read_text(encoding="utf-8"))
    missing = [old for old in mapping if old not in
               set(doc.get("built_reels") or [])]
    if missing:
        raise ValueError(
            f"cannot promote reels: provenance records no build for "
            f"{missing} - refusing to rename a baseline that was never "
            f"written")
    renamed = [mapping.get(name, name)
               for name in (doc.get("built_reels") or [])]
    doc["built_reels"] = sorted(renamed)
    for key in ("caption_hashes", "footage_binding_hashes",
                "built_at_reels", "built_with", "build_signatures"):
        entries = dict(doc.get(key) or {})
        for old, new in mapping.items():
            if old in entries:
                entries[new] = entries.pop(old)
        doc[key] = entries
    # The snapshot table is keyed by live timeline name too, so its
    # keys move with the promotion. The filenames inside stay: the
    # snapshots were written after the rename, under the final names.
    if SNAPSHOT_PROVENANCE_KEY in doc:
        snapshots_table = dict(doc.get(SNAPSHOT_PROVENANCE_KEY) or {})
        for old, new in mapping.items():
            if old in snapshots_table:
                snapshots_table[new] = snapshots_table.pop(old)
        doc[SNAPSHOT_PROVENANCE_KEY] = snapshots_table
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def record_carried_digests(review_dir: str, digests: dict) -> None:
    """Close the carried half of each promoted reel's build signature.

    Called by `promote_staged_reels` straight after the renames, which
    is the only moment a reel's carried state means "what is
    approved": the build placed a staging container, and until
    promotion the final name still holds the reel being replaced.

    A reel with no derivation half on record is SKIPPED rather than
    given a carried-only entry: a signature whose derivation is
    unknown can never match, so half of one is a row that reads like a
    record and answers nothing. Absent file is a no-op - there is
    nothing to close.
    """
    if not digests:
        return
    path = Path(review_dir) / PROVENANCE_FILENAME
    if not path.is_file():
        return
    doc = json.loads(path.read_text(encoding="utf-8"))
    entries = dict(doc.get("build_signatures") or {})
    for name, digest in digests.items():
        recorded = dict(entries.get(name) or {})
        if not recorded.get("derivation"):
            continue
        recorded["carried"] = digest or ""
        entries[name] = recorded
    doc["build_signatures"] = entries
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def drop_reel_entries(review_dir: str, names) -> None:
    """Remove reel keys from the provenance sidecar.

    The gate-fail path: a refused staging must not leave a baseline
    behind, or a later verifier would grade the approved timeline that
    survived against cards the refused build derived. Absent file or
    absent keys are no-ops - there is nothing to forget.
    """
    path = Path(review_dir) / PROVENANCE_FILENAME
    if not path.is_file():
        return
    doc = json.loads(path.read_text(encoding="utf-8"))
    drop = set(names or ())
    if not drop:
        return
    doc["built_reels"] = [name for name in (doc.get("built_reels") or [])
                            if name not in drop]
    for key in ("caption_hashes", "footage_binding_hashes",
                "built_at_reels", "built_with", "build_signatures"):
        entries = dict(doc.get(key) or {})
        for name in drop:
            entries.pop(name, None)
        doc[key] = entries
    # A refused staging leaves no snapshot baseline behind either, or
    # a later reader would grade the surviving timeline against files
    # the refused build named.
    if SNAPSHOT_PROVENANCE_KEY in doc:
        snapshots_table = dict(doc.get(SNAPSHOT_PROVENANCE_KEY) or {})
        for name in drop:
            snapshots_table.pop(name, None)
        doc[SNAPSHOT_PROVENANCE_KEY] = snapshots_table
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def _snapshot_bytes(path: Path) -> Optional[bytes]:
    """The bytes of a snapshot file, or None when unreadable."""
    try:
        return path.read_bytes()
    except OSError:
        return None


def _snapshot_timeline_name(document: dict) -> str:
    """The timeline name a snapshot document records, or "".

    Read from the snapshot's own `metadata.name` - what the timeline
    was called when the build wrote it - never from the filename, for
    the same reason `drift_check.newest_snapshots` does: promotion
    sanitises names into filenames, so the filename cannot be turned
    back into the timeline name.
    """
    metadata = document.get("metadata") if isinstance(document, dict) else None
    if not isinstance(metadata, dict):
        return ""
    return str(metadata.get("name") or "").strip()


def record_snapshot_supersession(
    review_dir: str,
    snapshots_by_timeline: dict,
) -> dict:
    """Record which snapshot each promoted timeline was built from.

    `snapshots_by_timeline` is `{live timeline name: path of the
    snapshot file just written for it}` - what
    `versions.store.record_reel_promotion` passes straight
    after serializing each promoted timeline.  Promotion is the only
    moment both halves are known: what the live timeline now holds
    (the bytes just written) and which older files name that same
    timeline.

    Per timeline the record holds the authoritative file (its
    review-relative filename plus the sha256 of its bytes, so a file
    replaced on disk afterwards reads as changed rather than
    current) and every OTHER same-named snapshot file as superseded,
    with the file that superseded it.  Timelines outside
    `snapshots_by_timeline` are left exactly as they are - the same
    merge rule `write_provenance` keeps for a partial rebuild.

    A file that cannot be read, or that names no timeline, is SKIPPED
    and never marked: supersession that cannot be established is not
    recorded.  The just-written files themselves are never
    superseded, even when a previous record listed them.

    Returns `{timeline_name: {"snapshot": ..., "superseded_now": [...]}}`.
    """
    review = Path(review_dir)
    provenance_path = review / PROVENANCE_FILENAME
    doc: dict = {}
    if provenance_path.is_file():
        try:
            loaded = json.loads(provenance_path.read_text(encoding="utf-8"))
            doc = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            doc = {}
    table = dict(doc.get(SNAPSHOT_PROVENANCE_KEY) or {})
    now = datetime.now(timezone.utc).isoformat()
    report: dict = {}
    for timeline_name, snapshot_path in (snapshots_by_timeline or {}).items():
        timeline_name = str(timeline_name or "").strip()
        if not timeline_name:
            continue
        written = Path(str(snapshot_path))
        raw = _snapshot_bytes(written)
        if raw is None:
            continue
        try:
            stored = written.relative_to(review).as_posix()
        except ValueError:
            stored = written.name
        digest = hashlib.sha256(raw).hexdigest()
        entry = dict(table.get(timeline_name) or {})
        previous: dict = {}
        stored_superseded = entry.get("superseded") or ()
        superseded_items = (stored_superseded.values()
                            if isinstance(stored_superseded, dict)
                            else stored_superseded)
        for item in superseded_items:
            if isinstance(item, dict) and item.get("snapshot"):
                previous[str(item["snapshot"])] = dict(item)
        superseded_now: list = []
        # A previous authoritative file under a different filename no
        # longer describes the live timeline - it is history now.
        old_snapshot = entry.get("snapshot")
        if (old_snapshot and old_snapshot != stored
                and old_snapshot not in previous):
            old_path = review / str(old_snapshot)
            old_raw = _snapshot_bytes(old_path)
            if old_raw is not None:
                previous[str(old_snapshot)] = {
                    "snapshot": str(old_snapshot),
                    "sha256": hashlib.sha256(old_raw).hexdigest(),
                    "superseded_by": stored,
                    "recorded_at": now,
                }
                superseded_now.append(str(old_snapshot))
        for candidate in sorted(review.glob("*.timeline.json")):
            if candidate.name == written.name and (
                    candidate == written
                    or candidate.resolve() == written.resolve()):
                continue
            candidate_raw = _snapshot_bytes(candidate)
            if candidate_raw is None:
                continue
            try:
                candidate_doc = json.loads(candidate_raw.decode("utf-8"))
            except ValueError:
                continue
            if _snapshot_timeline_name(candidate_doc) != timeline_name:
                continue
            try:
                candidate_stored = candidate.relative_to(
                    review).as_posix()
            except ValueError:
                candidate_stored = candidate.name
            if candidate_stored == stored:
                continue
            if candidate_stored not in previous:
                previous[candidate_stored] = {
                    "snapshot": candidate_stored,
                    "sha256": hashlib.sha256(candidate_raw).hexdigest(),
                    "superseded_by": stored,
                    "recorded_at": now,
                }
                superseded_now.append(candidate_stored)
            else:
                previous[candidate_stored]["superseded_by"] = stored
        # The authoritative file is never its own superseded entry,
        # whatever an earlier record said.
        previous.pop(stored, None)
        table[timeline_name] = {
            "snapshot": stored,
            "sha256": digest,
            "recorded_at": now,
            "superseded": [previous[key] for key in sorted(previous)],
        }
        report[timeline_name] = {"snapshot": stored,
                                 "superseded_now": sorted(superseded_now)}
    doc[SNAPSHOT_PROVENANCE_KEY] = table
    review.mkdir(parents=True, exist_ok=True)
    provenance_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return report


def built_from_snapshot(
    review_dir: str,
    timeline_name: str,
    provenance: Optional[dict] = None,
) -> tuple[Optional[dict], str]:
    """Which snapshot file the live timeline was built from, and why not.

    Returns `(entry, reason)` where `entry` is the recorded
    `{"snapshot", "sha256", "recorded_at"}`.  A `None` entry is never
    "any snapshot will do" - it is "nothing here can say", and the
    caller must report the refusal rather than grade a file the
    record does not bless.  That refusal is the whole of G6: the
    check that graded Reel 09's stale 36490 bound had no record to
    ask, so it graded whichever file it found first.

    The recorded digest is verified against the file's current bytes:
    a snapshot replaced on disk after the record reads as changed,
    never as current.
    """
    if provenance is None:
        provenance = read_provenance(review_dir)
    timeline_name = str(timeline_name or "").strip()
    if not provenance:
        return None, (
            f"{timeline_name}: no provenance record exists, so nothing "
            f"states which snapshot this live timeline was built from. "
            f"No snapshot is graded - a file found by filename alone "
            f"is not the one the build blessed.")
    table = provenance.get(SNAPSHOT_PROVENANCE_KEY) or {}
    entry = table.get(timeline_name)
    if not entry:
        return None, (
            f"{timeline_name}: provenance records no snapshot for this "
            f"timeline, so which file its live state came from is "
            f"unknown. No snapshot is graded. Promote it to record one.")
    stored = str(entry.get("snapshot") or "")
    path = Path(review_dir) / stored
    raw = _snapshot_bytes(path)
    if raw is None:
        return None, (
            f"{timeline_name}: the recorded snapshot {stored!r} is no "
            f"longer on disk. No snapshot is graded - a missing "
            f"baseline is not a matching one.")
    actual = hashlib.sha256(raw).hexdigest()
    if actual != entry.get("sha256"):
        return None, (
            f"{timeline_name}: the recorded snapshot {stored!r} changed "
            f"since the build recorded it - its bytes no longer hash "
            f"to the recorded digest. No snapshot is graded.")
    return dict(entry), (
        f"{timeline_name}: built from {stored} "
        f"(recorded {entry.get('recorded_at', '')})")


def is_snapshot_superseded(
    review_dir: str,
    snapshot: str,
    provenance: Optional[dict] = None,
) -> tuple[bool, str]:
    """Does this snapshot file read as superseded, and why.

    Returns `(superseded, reason)`.  `True` names the file that
    superseded it and when.  `False` is either "the authoritative
    record for its timeline" or "nothing here says" - the reason
    tells which, because an unrecorded file is not a live one.
    """
    if provenance is None:
        provenance = read_provenance(review_dir)
    name = Path(str(snapshot or "")).name
    if not provenance:
        return False, (
            f"{name}: no provenance record exists, so no snapshot "
            f"reads as superseded. Nothing here can say which file "
            f"is authoritative either.")
    table = provenance.get(SNAPSHOT_PROVENANCE_KEY) or {}
    for timeline_name, entry in table.items():
        if not isinstance(entry, dict):
            continue
        for item in entry.get("superseded") or ():
            if isinstance(item, dict) and item.get("snapshot") == name:
                return True, (
                    f"{name}: superseded by {item.get('superseded_by')} "
                    f"for {timeline_name} "
                    f"(recorded {item.get('recorded_at', '')}) - "
                    f"grading it grades history, not the live timeline.")
        if entry.get("snapshot") == name:
            return False, (
                f"{name}: the authoritative snapshot for "
                f"{timeline_name} - grading it grades the live timeline.")
    return False, (
        f"{name}: no supersession recorded for this file. It is "
        f"neither the authoritative snapshot of any timeline nor a "
        f"superseded one - nothing here can say what it is.")


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


def _mutate_preservation(review_dir: str, mutation) -> None:
    """Serialize preservation records with the project-file lock."""
    from library.tools.project_file_lock import lock_project_file

    path = Path(review_dir) / PROVENANCE_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with lock_project_file(path):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            doc = {}
        if not isinstance(doc, dict):
            raise ValueError(f"{path} is not a JSON object")
        mutation(doc)
        path.write_text(json.dumps(doc, indent=2, default=str),
                        encoding="utf-8")


def record_timeline_snapshot(review_dir: str, timeline_name: str,
                             snapshot: dict, *, action: str,
                             action_journal: str | None = None,
                             last_known: bool = True) -> dict:
    """Persist a full first-contact or verified Ren snapshot."""
    digest = hashlib.sha256(json.dumps(
        snapshot, sort_keys=True, separators=(",", ":"),
        default=str).encode("utf-8")).hexdigest()
    entry = {
        "snapshot": snapshot,
        "sha256": digest,
        "recorded_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"),
        "action": str(action),
    }
    if action_journal:
        entry["action_journal"] = str(action_journal)

    def update(doc):
        table = dict(doc.get(REN_TIMELINE_SNAPSHOTS_KEY) or {})
        name = str(timeline_name)
        previous = table.get(name) or {}
        history = list(previous.get("history") or [])
        history.append({key: value for key, value in entry.items()
                        if key != "history"})
        if last_known:
            entry["history"] = history
            table[name] = entry
        elif previous:
            previous = dict(previous)
            previous["history"] = history
            table[name] = previous
        else:
            table[name] = {"snapshot": None, "sha256": None,
                           "recorded_at": entry["recorded_at"],
                           "action": "no_ren_snapshot",
                           "history": history}
        doc[REN_TIMELINE_SNAPSHOTS_KEY] = table

    _mutate_preservation(review_dir, update)
    return entry


def record_editor_changes(review_dir: str, timeline_name: str,
                          records: list[dict]) -> None:
    """Append detected deltas idempotently."""
    def update(doc):
        table = dict(doc.get(EDITOR_CHANGES_KEY) or {})
        entries = list(table.get(str(timeline_name)) or [])
        known = {entry.get("id") for entry in entries}
        entries.extend(entry for entry in records
                       if entry.get("id") not in known)
        table[str(timeline_name)] = entries
        doc[EDITOR_CHANGES_KEY] = table

    _mutate_preservation(review_dir, update)


def pending_editor_changes(review_dir: str, timeline_name: str) -> list[dict]:
    doc = read_provenance(review_dir) or {}
    records = ((doc.get(EDITOR_CHANGES_KEY) or {}).get(
        str(timeline_name)) or [])
    return [dict(entry) for entry in records
            if entry.get("status") == "pending"]


def resolve_editor_change(review_dir: str, timeline_name: str,
                          record_id: str, *, status: str,
                          superseded_by: str | None = None) -> None:
    if status not in {"carried", "superseded", "restored"}:
        raise ValueError(f"unknown editor-change status {status!r}")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def update(doc):
        table = dict(doc.get(EDITOR_CHANGES_KEY) or {})
        entries = list(table.get(str(timeline_name)) or [])
        for entry in entries:
            if entry.get("id") == record_id:
                entry["status"] = status
                entry["resolved_at"] = now
                if superseded_by:
                    entry["superseded_by"] = str(superseded_by)
        table[str(timeline_name)] = entries
        doc[EDITOR_CHANGES_KEY] = table

    _mutate_preservation(review_dir, update)


def carried_editor_edits(review_dir: str, timeline_name: str) -> list[dict]:
    """The edits in force on `timeline_name`, oldest first."""
    doc = read_provenance(review_dir) or {}
    edits = ((doc.get(CARRIED_EDITS_KEY) or {}).get(
        str(timeline_name)) or [])
    return [dict(edit) for edit in edits if edit.get("status") == "active"]


def record_carried_edits(review_dir: str, timeline_name: str,
                         edits: list[dict], *,
                         superseded: dict | None = None) -> None:
    """Add `edits` and retire `superseded` (`{edit id: why}`) in one write.

    An edit whose id is already recorded is replaced in place: the id
    names the item and the property, so a re-ruling of the same thing
    is one entry, not two that disagree.
    """
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def update(doc):
        table = dict(doc.get(CARRIED_EDITS_KEY) or {})
        entries = list(table.get(str(timeline_name)) or [])
        index = {entry.get("id"): position
                 for position, entry in enumerate(entries)}
        for edit in edits:
            if edit["id"] in index:
                entries[index[edit["id"]]] = dict(edit)
            else:
                index[edit["id"]] = len(entries)
                entries.append(dict(edit))
        for edit_id, why in (superseded or {}).items():
            for entry in entries:
                if entry.get("id") == edit_id and \
                        entry.get("status") == "active":
                    entry["status"] = "superseded"
                    entry["superseded_at"] = now
                    entry["superseded_by"] = str(why)
        table[str(timeline_name)] = entries
        doc[CARRIED_EDITS_KEY] = table

    _mutate_preservation(review_dir, update)


def _timeline_identity(entry: dict) -> str:
    unique_id = entry.get("unique_id")
    return f"id:{unique_id}" if unique_id else f"name:{entry.get('name', '')}"


def editor_timeline_identities(review_dir: str) -> dict:
    doc = read_provenance(review_dir) or {}
    return dict(doc.get(EDITOR_TIMELINES_KEY) or {})


def protected_timeline_names(review_dir: str,
                             inventory: list[dict]) -> set[str]:
    protected = editor_timeline_identities(review_dir)
    return {str(entry["name"]) for entry in inventory
            if _timeline_identity(entry) in protected}


def record_unattributed_timeline_changes(review_dir: str,
                                        before: list[dict],
                                        after: list[dict], *,
                                        operation: str,
                                        ren_owned_ids=()) -> set[str]:
    """Protect timeline additions or renames not explained by Ren's write."""
    previous = {_timeline_identity(entry): entry for entry in before}
    known_ren_ids = {f"id:{value}" for value in ren_owned_ids or () if value}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    detected = {}
    for current in after:
        identity = _timeline_identity(current)
        old = previous.get(identity)
        created = old is None
        renamed = old is not None and old.get("name") != current.get("name")
        if (created or renamed) and identity not in known_ren_ids:
            detected[identity] = {
                "identity": identity,
                "first_seen_at": now,
                "first_seen_name": current.get("name"),
                "last_seen_name": current.get("name"),
                "reason": ("created by the editor" if created else
                           "renamed by the editor"),
                "operation": str(operation),
            }

    def update(doc):
        editor_timelines = dict(doc.get(EDITOR_TIMELINES_KEY) or {})
        for identity, entry in detected.items():
            editor_timelines.setdefault(identity, entry)
        doc[EDITOR_TIMELINES_KEY] = editor_timelines

    _mutate_preservation(review_dir, update)
    return {str(entry["name"]) for entry in after
            if _timeline_identity(entry) in detected}


def begin_timeline_inventory(review_dir: str, operation: str,
                             before: list[dict], *,
                             ren_created_names=(),
                             ren_created_ids=()) -> str:
    """Persist project timeline inventory before a Ren operation."""
    operation_id = hashlib.sha256(
        f"{datetime.now(timezone.utc).isoformat()}:{operation}".encode(
            "utf-8")).hexdigest()[:20]
    entry = {
        "id": operation_id,
        "operation": str(operation),
        "started_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"),
        "before": before,
        "after": None,
    }
    ren_created = {str(name) for name in ren_created_names or ()}
    ren_created_identities = {
        f"id:{unique_id}" for unique_id in ren_created_ids or ()
        if unique_id}

    def update(doc):
        history = list(doc.get(TIMELINE_INVENTORY_KEY) or [])
        completed = next((item for item in reversed(history)
                          if item.get("after") is not None), None)
        previous = {_timeline_identity(item): item
                    for item in (completed or {}).get("after", ())}
        editor_timelines = dict(doc.get(EDITOR_TIMELINES_KEY) or {})
        if completed is None:
            # On first contact, every timeline not named as a known Ren
            # target is already present before this operation. Its author
            # cannot be inferred, so preserve it as ownership-unknown and
            # keep it out of later cleanup and replacement scopes.
            for current in before:
                identity = _timeline_identity(current)
                known_ren_target = (
                    identity in ren_created_identities
                    or current.get("name") in ren_created)
                if not known_ren_target:
                    editor_timelines.setdefault(identity, {
                        "identity": identity,
                        "first_seen_at": entry["started_at"],
                        "first_seen_name": current.get("name"),
                        "last_seen_name": current.get("name"),
                        "reason": "ownership unknown at first inventory",
                        "first_seen_operation": operation_id,
                    })
        if completed is not None:
            for current in before:
                identity = _timeline_identity(current)
                old = previous.get(identity)
                renamed = old is not None and old.get("name") != current.get("name")
                newly_seen = old is None
                is_ren_created = (
                    identity in ren_created_identities
                    or current.get("name") in ren_created)
                if ((newly_seen or renamed) and not is_ren_created):
                    editor_timelines.setdefault(identity, {
                        "identity": identity,
                        "first_seen_at": entry["started_at"],
                        "first_seen_name": current.get("name"),
                        "last_seen_name": current.get("name"),
                        "reason": ("created by the editor" if newly_seen else
                                   "renamed by the editor"),
                        "first_seen_operation": operation_id,
                    })
                elif identity in editor_timelines:
                    editor_timelines[identity]["last_seen_name"] = current.get("name")
        doc[EDITOR_TIMELINES_KEY] = editor_timelines
        history.append(entry)
        doc[TIMELINE_INVENTORY_KEY] = history

    _mutate_preservation(review_dir, update)
    return operation_id


def assert_not_editor_timeline(review_dir: str, timeline) -> None:
    """Refuse to mutate a timeline first seen as editor-created or renamed."""
    try:
        unique_id = timeline.GetUniqueId()
    except Exception:  # noqa: BLE001
        unique_id = None
    identity = (f"id:{unique_id}" if unique_id else
                f"name:{timeline.GetName()}")
    entry = editor_timeline_identities(review_dir).get(identity)
    if entry:
        from library.tools.reel_replace_guard import EditorChangeRefused
        reason = entry.get("reason", "its ownership is unknown")
        raise EditorChangeRefused(
            f"REFUSING to replace {timeline.GetName()!r}: this timeline "
            f"is protected because {reason}; it remains untouched.")


def finish_timeline_inventory(review_dir: str, operation_id: str,
                              after: list[dict]) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def update(doc):
        history = list(doc.get(TIMELINE_INVENTORY_KEY) or [])
        for entry in history:
            if entry.get("id") == operation_id:
                entry["after"] = after
                entry["completed_at"] = now
                break
        else:
            raise ValueError(
                f"timeline inventory operation {operation_id!r} is missing")
        doc[TIMELINE_INVENTORY_KEY] = history

    _mutate_preservation(review_dir, update)


def current_plan_names(project_folder: str,
                       provenance: Optional[dict] = None) -> set[str]:
    """Every reel that belongs in Current plan: the live plan's APPROVED moments.

    Read from the proposals file (`reel_proposals_v2.json`), the same file
    the builder builds from (`reel_build.rebuild_reels_in_project` reads it
    through `reel_proposal.read_proposal`), filtered to what the captain
    approved. Whether some particular build call happened to rebuild a
    reel is irrelevant to where it is filed: a partial build leaves every
    other planned reel exactly where it was, and only a reel the live plan
    no longer names demotes to Earlier plans.

    This is deliberately NOT the provenance record's `built_reels`. That
    record says which plan a BUILD consumed and which reels it placed -
    and on a plan-hash change it drops every entry but the reels that
    built (see `write_provenance`: a different plan does not merge), so
    after one single-reel build it names one reel while the plan still
    names twenty-four. Filing by it reads a partial build as a plan
    change and files every other reel as history. Measured 2026-09-18 on
    the captain's project: building Reel 02 alone demoted eight accepted
    reels to Earlier plans, because the plan hash had rotated under them.

    Falls back to the provenance record's `built_reels` when the live
    plan cannot be read at all - said loudly     on stderr, because a filing
    by a stale record is a guess being kept quiet. Falling back keeps
    the current filing instead of mass-demoting: an unreadable plan is
    "nothing here can say", never evidence that every reel left it.
    """
    import sys as _sys

    try:
        from library.tools.reel_proposal import (
            proposal_path, read_proposal)
        moments = read_proposal(str(proposal_path(project_folder)))
    except Exception as exc:                        # noqa: BLE001
        fallback = set(((provenance or {}).get("built_reels")) or [])
        print(f"  live plan unreadable ({exc}) - filing by the "
              f"provenance record's {len(fallback)} built reel(s) "
              f"instead of demoting what nothing said left",
              file=_sys.stderr)
        return fallback
    return {m.timeline_name for m in moments
            if str(getattr(m.approval, "value", m.approval)) == "approved"}


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
