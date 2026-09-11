"""A garbage collector for rendered caption assets, built on reachability.

`pipeline_output/steps/4_05_render_subtitles/` accumulates garbage two
ways: a re-rendered card leaves its superseded generation behind (same
card, new digest, old file untouched), and a renamed reel leaves its
older-slug assets behind. No run owns the directory's lifetime, so no
run cleans it up.

DO NOT COLLECT BY NAME, SLUG OR AGE. A reel may have been renamed while
its older-named assets are still placed on a timeline the captain keeps,
and their lower tiers hold reels they have NOT thrown away. Deleting a
referenced asset silently breaks a timeline they still own.

THE ONLY SAFE TEST IS REACHABILITY: an asset is garbage when NOTHING
references it. The reference roots are, at minimum:

- every timeline in the captain's Resolve project, read through a COPY
  of the database, never the live file;
- the pipeline's own current step records and manifests;
- anything a later step consumes downstream (the assembly manifest,
  which is what step 6.01 places).

An asset reachable from any root is LIVE. Everything else is a
candidate, and a candidate is not a deletion until the captain or
firstmate says so.

Three parts:

1. `mark` computes reachability from the roots and reports, per asset,
   LIVE or ORPHAN with the root that saved it. Read-only, always safe.
2. `sweep` acts on a marked set, defaulting to MOVE into quarantine,
   and REFUSES when the mark is stale or any root was unreadable. A
   root it could not read is a reason to REFUSE, never a reason to
   treat everything under it as unreachable: an unreadable database
   returns nothing, which reads exactly like a project with nothing on
   any timeline and would mark every file deletable. That failure
   direction deletes the captain's work, so it is stated here and
   tested in `tests/test_caption_asset_gc.py`.
3. The retention half lives in step 4.05 itself: when a card is
   re-rendered, the superseded generation is named on the new entry at
   once (`render_one_segment`), so it becomes an orphan candidate
   without waiting for a sweep. Reachability still rules the mark - a
   superseded file a timeline still places stays LIVE.
4. The recording half lives there too: every produced segment is
   merged into `render_ledger.json` beside the assets
   (`record_rendered_segments`), which the `pipeline:render_subtitles`
   root reads through the same harvester as the step output. Captions
   rendered before the ledger existed are adopted once by
   `reconcile_render_ledger`, which records only files carrying the
   render path's own props-sibling signature and embeds the
   pre-reconcile mark beside the entries, so the adoption is auditable.

Where things live is `library/tools/project_layout.py`'s decision: the
assets are `Area.SUBTITLE_SEGMENTS`, and quarantine plus the mark/sweep
records are `Area.QUARANTINE`. This module names areas, never paths.

Sibling lifetimes: a `.json`/`.txt` sibling has none of its own. It
lives when its `.mov` lives and goes when its `.mov` goes, because
nothing references a props file - timelines place the mov. A sibling
whose mov is absent (a render that failed after writing props) is an
orphan on its own. `_tight_box.json` belongs to its `_tight.mov`.

`_tight.mov` and `_tight_box.json` are a PREVIOUS CARRIAGE - nothing
writes them now that an overlay artefact is the delivery frame
(`library/tools/tight_box.py`) - and they are still swept, because
what is on the captain's disk is what this module is for.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from library.tools.project_layout import Area, ProjectLayout

LIVE = "live"
ORPHAN = "orphan"

OK = "ok"
NO_RECORD = "no-record"
UNREADABLE = "unreadable"

STEP_NODE_ID = "render_subtitles"

# The step's render ledger: every overlay the caption step produced,
# recorded where the `pipeline:render_subtitles` root reads it.
RENDER_LEDGER_NAME = "render_ledger.json"

LEDGER_VERSION = 1

# Step records live beside the assets but are never assets themselves.
STEP_RECORD_NAMES = ("output.json", "summary.md", RENDER_LEDGER_NAME)

_DIGEST_RE = re.compile(r"_([0-9a-f]{8})$")

_SIBLING_SUFFIXES = ("_props.json", "_reuse_key.txt", "_tight_box.json",
                       "_box.json")
"""Suffixed companions that live while their mov lives.

`_box.json` is the current carriage's placement record
(`tight_box`'s box sidecar, written beside the tight `.mov`); it
belongs to `<owner>.mov` directly, unlike `_tight_box.json`, which
belongs to `<owner>_tight.mov` and is matched first below - a name
ending in `_tight_box.json` also ends in `_box.json`, so the order
here is load-bearing. Measured 2026-09-10: without this entry every
rebuild's sweep quarantined the live captions' sidecars as
`unknown`, and the next build's reuse fell through to a full
measured re-render per caption - the treadmill that made the
missing-sidecar path the common one."""


class SweepRefused(Exception):
    """Something about the sweep is not safe, so NOTHING moves."""


@dataclass
class RootResult:
    """One reference root, and what it said.

    `status` is `ok` (read, contributing `paths`), `no-record` (the
    record legitimately does not exist - a project that never ran the
    caption step - contributing nothing and refusing nothing), or
    `unreadable` (contributing nothing and refusing every sweep).
    """

    name: str
    status: str
    paths: set = field(default_factory=set)
    detail: str = ""
    # How to re-establish this root at sweep time. `resolve` re-reads
    # the database at `db_path`; `pipeline` re-collects from the
    # project; `memory` cannot be re-established (tests only).
    kind: str = "memory"
    db_path: str = ""
    # The timeline names this root saw. Only a `resolve` root fills it,
    # and `live_timelines_from` is the ONE place that reads it - so the
    # mark and the sweep cannot derive different live sets from the same
    # roots and disagree about what is superseded.
    timelines: set = field(default_factory=set)


@dataclass
class AssetVerdict:
    """One file's reachability verdict."""

    path: str
    size_bytes: int
    status: str            # LIVE | ORPHAN
    saved_by: str = ""     # the root that saved it; empty when orphan
    reason: str = ""       # why it is an orphan; empty when live
    card: str = ""         # the card it belongs to (mov stem minus digest)
    digest: str = ""       # its own generation, if it has one
    kind: str = ""         # mov | sibling | lone-sibling | frames-dir | unknown
    superseded_by: str = ""  # newer generation, when the step named one


# ── Filenames ─────────────────────────────────────────────────────────


def parse_caption_filename(name: str) -> dict | None:
    """Split a step-directory filename into card, digest and role.

    Returns None for step records (`output.json`, `summary.md`), which
    are never assets. Everything else is an asset, including shapes
    this parser does not recognise - those come back `kind="unknown"`
    and are judged by path reachability alone, because an unrecognised
    shape is a reason to be careful, never a reason to delete.
    """
    if name in STEP_RECORD_NAMES:
        return None
    if name.endswith(".mov"):
        stem = name[:-4]
        tight = stem.endswith("_tight")
        if tight:
            stem = stem[:-6]
        match = _DIGEST_RE.search(stem)
        if not match:
            return {"card": stem, "digest": "",
                    "tight": tight, "kind": "mov",
                    "mov_stem": name[:-4]}
        return {"card": stem[:match.start()], "digest": match.group(1),
                "tight": tight, "kind": "mov",
                "mov_stem": name[:-4]}
    for suffix in _SIBLING_SUFFIXES:
        if name.endswith(suffix):
            owner = name[:-len(suffix)]
            if suffix == "_tight_box.json":
                # The placement record of a tight render: its mov is
                # `<owner>_tight.mov`.
                owner = owner + "_tight"
            return {"card": "", "digest": "", "tight": False,
                    "kind": "sibling", "mov_stem": owner}
    return {"card": "", "digest": "", "tight": False,
            "kind": "unknown", "mov_stem": ""}


def card_key(mov_stem: str) -> tuple[str, bool]:
    """The card a mov stem belongs to: stem minus digest, plus tightness.

    Two generations of one card share this key and differ in digest.
    This is the grouping the retention rule in step 4.05 uses to name
    what a re-render superseded.
    """
    tight = mov_stem.endswith("_tight")
    stem = mov_stem[:-6] if tight else mov_stem
    match = _DIGEST_RE.search(stem)
    card = stem[:match.start()] if match else stem
    return card, tight


def _mov_digest(mov_stem: str) -> str:
    stem = mov_stem[:-6] if mov_stem.endswith("_tight") else mov_stem
    match = _DIGEST_RE.search(stem)
    return match.group(1) if match else ""


# ── Enumeration ───────────────────────────────────────────────────────


def _tree_size(path: str) -> int:
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for name in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                continue
    return total


def enumerate_assets(asset_dir: str) -> list[dict]:
    """Every asset in the caption step directory, with its siblings.

    Returns file-level records: one per `.mov`/frames-dir plus one per
    sibling, each naming its owning mov stem (or "" when it has none).
    Read-only: nothing is created, moved or written.
    """
    records = []
    try:
        names = sorted(os.listdir(asset_dir))
    except OSError:
        return records
    for name in names:
        full = os.path.join(asset_dir, name)
        if os.path.isdir(full) and not os.path.islink(full):
            # A frames-direct sequence (container=frames) is one asset.
            records.append({"path": full, "size_bytes": _tree_size(full),
                            "kind": "frames-dir", "mov_stem": "",
                            "card": "", "digest": ""})
            continue
        if not os.path.isfile(full):
            continue
        parsed = parse_caption_filename(name)
        if parsed is None:
            continue  # a step record, not an asset
        try:
            size = os.path.getsize(full)
        except OSError:
            size = 0
        record = {"path": full, "size_bytes": size,
                  "kind": parsed["kind"],
                  "mov_stem": parsed["mov_stem"]}
        if parsed["kind"] == "mov":
            record["card"], _tight = card_key(parsed["mov_stem"])
            record["digest"] = _mov_digest(parsed["mov_stem"])
        records.append(record)
    return records


# ── Roots ─────────────────────────────────────────────────────────────


def _read_json_paths(path: str) -> tuple[set[str], str]:
    """Overlay paths named by one JSON step record.

    Returns the paths and "" on success, or (empty, reason) when the
    file cannot be read. A missing file is NOT an error here - the
    caller decides whether absence is `no-record` or `unreadable`.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        return set(), f"cannot be read: {exc}"
    found = set()

    def harvest(node) -> None:
        if isinstance(node, dict):
            overlay = node.get("overlay_path")
            if isinstance(overlay, str) and overlay:
                found.add(overlay)
            frames = node.get("frames")
            if isinstance(frames, dict):
                directory = frames.get("dir")
                if isinstance(directory, str) and directory:
                    found.add(directory)
            for value in node.values():
                harvest(value)
        elif isinstance(node, list):
            for value in node:
                harvest(value)

    overlay = (data.get("subtitle_overlay")
               if isinstance(data, dict) else None)
    harvest(overlay if overlay is not None else data)
    return found, ""


class LedgerError(Exception):
    """The render ledger could not be written. Nothing was recorded."""


class LedgerUnreadable(LedgerError):
    """The ledger exists but cannot be parsed, so it was left untouched.

    Overwriting it would convert an UNREADABLE root (which refuses every
    sweep) into a freshly valid one naming only the latest pass - exactly
    the failure direction that turns this collector into a data-loss
    event. The step refuses loudly instead, and the sweep keeps refusing
    until someone reads the file.
    """


# Provenances the ledger records. The authority is the caption step's
# own `PROVENANCES`; these literals repeat it so this module does not
# import step code (the step imports this module). A FAILED entry names a
# path whose pixels were never rendered - recording it would pin a file
# the mark must be free to orphan.
_RECORDED_PROVENANCES = ("rendered", "reused")

# Ledger entry keys carried over from a step segment entry. The binding
# rides along when the step recorded one; reconciled entries (below) have
# none, because the filename's slug is lossy and an invented binding is
# worse than an absent one.
_LEDGER_ENTRY_KEYS = (
    "segment_id",
    "overlay_path",
    "binding",
    "timeline_start",
    "timeline_end",
    "block_position",
    "provenance",
    "reuse_key",
    "superseded",
    "geometry",
    "container",
    "tight_box",
    # Why this card is full canvas although the project declared
    # tight, or "" when that did not happen. Carried because the
    # ledger is the only record a staging render leaves (no step
    # output), and without it a verify-gate fallback reads exactly
    # like a render that never tried tight.
    "tight_fallback",
    "frames",
    "source_in_frame",
    "source_out_frame",
    "total_frames",
    "rendered_frames",
    "reconciled",
    "reconciled_at",
)


def ledger_path_for(asset_dir: str) -> str:
    """The ledger beside the assets it vouches for. Never an asset."""
    return os.path.join(asset_dir, RENDER_LEDGER_NAME)


def _ledger_entry_path(entry: dict) -> str:
    overlay = entry.get("overlay_path") or ""
    if overlay:
        return str(overlay)
    frames = entry.get("frames") or {}
    return str(frames.get("dir") or "")


def _ledger_entry_placement(entry: dict) -> str:
    """Which placing an entry vouches for: its binding's timeline.

    One file is now shared by several timelines' placements, and each
    placement keeps its own entry - provenance, timeline bounds and
    binding differ per placing while the path agrees. Keying the merge
    on the segment id alone would collapse them into one and unprotect
    every placing but the last recorded. Entries with no binding (the
    reconciled adoptions) carry "": they vouch for the file, not for a
    placing.
    """
    binding = entry.get("binding") or {}
    if not isinstance(binding, dict):
        return ""
    return str(binding.get("timeline") or "")


def _ledger_merge_key(entry: dict) -> tuple[str, str]:
    return (str(entry["segment_id"]), _ledger_entry_placement(entry))


def _anchor_to_dir(asset_dir: str, path: str) -> str:
    """A recorded path, anchored where the ledger lives.

    The step records absolute paths and they pass through unchanged. A
    relative one is anchored to the asset dir rather than left to
    resolve against whatever working directory a later mark runs from -
    a silently unprotected asset under a different cwd.
    """
    if not path or os.path.isabs(path):
        return path
    return os.path.normpath(os.path.join(asset_dir, path))


def _lock(handle) -> None:
    try:
        import fcntl  # noqa: PLC0415 - platform seam, not a dependency
    except ImportError:
        return
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock(handle) -> None:
    try:
        import fcntl  # noqa: PLC0415 - platform seam, not a dependency
    except ImportError:
        return
    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def record_rendered_segments(asset_dir: str, segments: list[dict],
                             only_new: bool = False,
                             extra_top_level: dict | None = None) -> str:
    """Merge produced caption segments into the step's render ledger.

    The ledger is the file the `pipeline:render_subtitles` root reads
    (`collect_pipeline_roots`), in the shape its harvester already
    understands - `subtitle_overlay.segments` carrying `overlay_path`
    (and `frames.dir` for sequences). No new contract: the same keys,
    the same empty-vs-unreadable distinction. An empty `segments` writes
    a valid empty set (an `ok` root with no paths); a ledger that cannot
    be written raises, and a ledger that exists but cannot be parsed
    raises `LedgerUnreadable` WITHOUT being overwritten, so the root
    keeps reading UNREADABLE and the sweep keeps refusing.

    Merge, never replace, keyed by `(segment_id, placing)`: one step
    directory holds several timelines' batches, and a pass covers one
    plan, so a replace would unprotect every batch the pass did not
    render. One file is now shared by several placings, and EACH
    placing keeps its entry (`_ledger_merge_key`) - collapsing them
    would unprotect every placing but the last recorded, which is the
    shared-file deletion this collector exists to refuse. Entries whose
    path THIS placing names as `superseded` are dropped for that
    placing only - that is the re-render retention rule, and a file
    another placing still names stays referenced. Reachability still
    has the last word. Entries whose file is gone from disk are pruned:
    an absent path protects nothing. With `only_new`, entries already
    present keep their genuinely-recorded records (the reconcile path
    below).
    """
    path = ledger_path_for(asset_dir)
    try:
        os.makedirs(asset_dir, exist_ok=True)
    except OSError as exc:
        raise LedgerError(
            f"the render ledger at {path} cannot be written ({exc}): "
            f"{len(segments)} produced segment(s) would be left with no "
            f"root that resolves them.") from exc
    fresh = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        if segment.get("provenance") not in _RECORDED_PROVENANCES:
            continue
        if not segment.get("segment_id"):
            print(f"warning: a produced caption segment with no "
                  f"segment_id is not ledgerable and is skipped "
                  f"({segment.get('overlay_path', '?')})")
            continue
        trimmed = {k: segment[k] for k in _LEDGER_ENTRY_KEYS
                   if k in segment}
        trimmed["overlay_path"] = _anchor_to_dir(
            asset_dir, str(trimmed.get("overlay_path") or ""))
        frames = trimmed.get("frames")
        if isinstance(frames, dict) and frames.get("dir"):
            frames = dict(frames)
            frames["dir"] = _anchor_to_dir(asset_dir, str(frames["dir"]))
            trimmed["frames"] = frames
        trimmed["superseded"] = [
            _anchor_to_dir(asset_dir, str(p))
            for p in (trimmed.get("superseded") or [])
            if isinstance(p, str) and p]
        fresh.append(trimmed)
    try:
        handle = open(path, "a+", encoding="utf-8")
    except OSError as exc:
        raise LedgerError(
            f"the render ledger at {path} cannot be written ({exc}): "
            f"{len(fresh)} produced segment(s) would be left with no "
            f"root that resolves them.") from exc
    with handle:
        _lock(handle)
        try:
            handle.seek(0)
            raw = handle.read()
            if raw.strip():
                try:
                    data = json.loads(raw)
                except ValueError as exc:
                    raise LedgerUnreadable(
                        f"the render ledger at {path} exists but cannot "
                        f"be parsed ({exc}): left untouched, so the "
                        f"pipeline root keeps reading UNREADABLE and "
                        f"the sweep keeps refusing.") from exc
                if not isinstance(data, dict):
                    raise LedgerUnreadable(
                        f"the render ledger at {path} holds "
                        f"{type(data).__name__}, not an object: left "
                        f"untouched, so the sweep keeps refusing.")
            else:
                data = {}
            overlay = data.get("subtitle_overlay")
            if overlay is None:
                overlay = {}
                data["subtitle_overlay"] = overlay
            if not isinstance(overlay, dict):
                raise LedgerUnreadable(
                    f"the render ledger at {path} holds a non-object "
                    f"subtitle_overlay: left untouched, so the sweep "
                    f"keeps refusing.")
            existing = overlay.get("segments")
            if existing is None:
                existing = []
            if not isinstance(existing, list):
                raise LedgerUnreadable(
                    f"the render ledger at {path} holds a non-list "
                    f"segments: left untouched, so the sweep keeps "
                    f"refusing.")
            by_id = {}
            for entry in existing:
                if isinstance(entry, dict) and entry.get("segment_id"):
                    by_id[_ledger_merge_key(entry)] = entry
            existing_ids = {merge_key[0] for merge_key in by_id}
            for entry in fresh:
                merge_key = _ledger_merge_key(entry)
                if only_new and merge_key in by_id:
                    continue
                if only_new and entry.get("reconciled") \
                        and merge_key[0] in existing_ids:
                    # Reconstructed evidence never duplicates genuine
                    # evidence for the same file: the recorded entry
                    # already vouches for the path, whatever placing
                    # it was recorded under, so the adoption adds
                    # nothing and is skipped rather than stored
                    # beside it.
                    continue
                by_id[merge_key] = entry
            # The supersede-drop is per placing: a re-render unpins the
            # generation ITS placing replaced, never a generation
            # another placing still names. Dropping every entry naming
            # the path would unprotect a file two live timelines still
            # use the moment one of them moves on - the shared-file
            # deletion. Reachability still rules the mark: the dropped
            # placing's file becomes an orphan candidate, and a file
            # any entry still names stays LIVE.
            for entry in fresh:
                placing = _ledger_entry_placement(entry)
                dropped_here = {
                    str(p) for p in (entry.get("superseded") or [])
                    if isinstance(p, str) and p}
                for merge_key in [
                        merge_key for merge_key, old in by_id.items()
                        if merge_key[1] == placing
                        and _ledger_entry_path(old) in dropped_here]:
                    del by_id[merge_key]
            kept = {}
            for merge_key, entry in by_id.items():
                at = _ledger_entry_path(entry)
                if at and os.path.exists(at):
                    kept[merge_key] = entry
            overlay["segments"] = [
                kept[merge_key] for merge_key in sorted(kept)]
            data["ledger_updated_at"] = datetime.now(timezone.utc).isoformat()
            data["ledger_version"] = LEDGER_VERSION
            if extra_top_level:
                data.update(extra_top_level)
            handle.seek(0)
            handle.truncate()
            json.dump(data, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            _unlock(handle)
    return path


def _props_signature(props_path: str) -> bool:
    """The render path's own write signature: props beside the pixels.

    The step writes the props file before rendering and the mov (or
    frames) after succeeding, so a mov with a parseable props sibling
    is the step's output. A mov without one is not provably produced by
    any run and is left for the other roots to judge.
    """
    try:
        with open(props_path, encoding="utf-8") as handle:
            return isinstance(json.load(handle), dict)
    except (OSError, ValueError):
        return False


def _reconcile_entries(asset_dir: str) -> list[dict]:
    """Ledger entries for the step-signature outputs on disk, or [].

    One entry per `.mov` (or frames-dir) whose props sibling parses -
    the files the render path demonstrably wrote. No binding: the
    filename's slug is lossy and an invented binding is worse than an
    absent one. No `superseded`: unknowable without the producing run's
    record. A later genuine render of the same card upserts the entry
    wholesale by `segment_id`, promoting it from reconciled to recorded.
    """
    entries = []
    for record in enumerate_assets(asset_dir):
        if record["kind"] == "mov":
            stem = os.path.basename(record["path"])[:-4]
            props = os.path.join(asset_dir, stem + "_props.json")
            if not _props_signature(props):
                continue
            segment_id = (stem[:-6] if stem.endswith("_tight") else stem)
            entry: dict = {
                "segment_id": segment_id,
                "overlay_path": record["path"],
                "provenance": "rendered",
                "superseded": [],
                "reconciled": True,
            }
            key_path = os.path.join(asset_dir, stem + "_reuse_key.txt")
            try:
                key = Path(key_path).read_text(encoding="utf-8").strip()
            except OSError:
                key = ""
            if key:
                entry["reuse_key"] = key
            entries.append(entry)
        elif record["kind"] == "frames-dir":
            dirname = os.path.basename(record["path"])
            if not dirname.endswith("_frames"):
                continue
            base = dirname[:-len("_frames")]
            props = os.path.join(asset_dir, base + "_props.json")
            if not _props_signature(props):
                continue
            segment_id = (base[:-6] if base.endswith("_tight") else base)
            try:
                count = sum(1 for name in os.listdir(record["path"])
                            if name.endswith(".png"))
            except OSError:
                count = 0
            entries.append({
                "segment_id": segment_id,
                "overlay_path": "",
                "provenance": "rendered",
                "superseded": [],
                "frames": {"dir": record["path"],
                           "pattern": "", "count": count},
                "reconciled": True,
            })
    return entries


def reconcile_render_ledger(project_folder: str,
                            db_paths: list[str] | None = None) -> dict:
    """Adopt the step's on-disk outputs into the render ledger, once.

    For projects whose captions were rendered before the ledger existed:
    every `.mov` (or frames-dir) carrying the render path's own write
    signature is recorded, with `reconciled: true` saying how the entry
    got there. Entries the ledger already holds are kept (`only_new`) -
    genuinely recorded evidence is never overwritten by reconstructed
    evidence. Files already swept to quarantine are not in the directory
    and so are never adopted: the captain's unreviewed quarantine is
    untouched by construction.

    The pre-reconcile mark is embedded in the ledger beside the entries,
    so a later reader sees what the adoption pinned and what every root
    said at the time. Read-only except for the one ledger file: no
    asset is created, moved or deleted, and nothing is swept.
    """
    layout = ProjectLayout(project_folder)
    asset_dir = str(layout.read_dir(Area.SUBTITLE_SEGMENTS))
    roots = collect_resolve_roots(list(db_paths or []))
    roots += collect_pipeline_roots(
        project_folder, live_timelines=live_timelines_from(roots))
    pre = mark(project_folder, asset_dir, roots)
    entries = _reconcile_entries(asset_dir)
    stamped = datetime.now(timezone.utc).isoformat()
    for entry in entries:
        entry["reconciled_at"] = stamped
    path = record_rendered_segments(
        asset_dir, entries, only_new=True,
        extra_top_level={
            "reconciled_at": stamped,
            "reconciled_from": "on-disk step outputs carrying the "
            "render path's props-sibling signature",
            "pre_reconcile_mark": {
                "created_at": pre.created_at,
                "live": len(pre.live),
                "orphans": len(pre.orphans),
                "roots": {r.name: {"status": r.status,
                                   "paths": len(r.paths),
                                   "detail": r.detail}
                          for r in pre.roots},
            },
        })
    return {"ledger": path, "recorded": len(entries), "mark": pre}


def rename_ledger_timelines(asset_dir: str, claimed: dict) -> dict:
    """Re-point ledger bindings when a staging reel is promoted.

    `promote_staged_reels` renames the staging timeline to its final
    name and DELETES the staging container. Every other record of that
    build is renamed with it (`plan_provenance.rename_reel_entries`,
    `explainer_plan.rename_plan_reels`, ...); the render ledger was
    not, so its entries went on naming a timeline that no longer
    exists.

    That is not a cosmetic drift. `collect_pipeline_roots` reads the
    ledger as a reference ROOT, so an entry bound to a dead timeline
    pins its mov LIVE for ever and no sweep can ever reclaim it.
    Measured on geo-podcast, 2026-09-10: all 23 movs the ledger alone
    held live were bound to `Reel 09 ... (rebuild staging)`, a
    timeline the project does not have - which is why a hand-run sweep
    reclaimed 15.7 MB of 584 MB.

    `claimed` is `{old_name: new_name}`, the same shape the sibling
    renamers take. Returns `{"renamed": n, "ledger": path}`; a ledger
    that is absent is nothing to rename, and one that cannot be parsed
    is left untouched (it stays UNREADABLE, and the sweep keeps
    refusing) rather than being rewritten from scratch.
    """
    path = ledger_path_for(asset_dir)
    if not claimed or not os.path.isfile(path):
        return {"renamed": 0, "ledger": path}
    try:
        handle = open(path, "r+", encoding="utf-8")
    except OSError as exc:
        raise LedgerError(
            f"the render ledger at {path} cannot be opened ({exc}), so "
            f"its bindings cannot be re-pointed at the promoted "
            f"names.") from exc
    renamed = 0
    with handle:
        _lock(handle)
        try:
            raw = handle.read()
            try:
                data = json.loads(raw) if raw.strip() else {}
            except ValueError as exc:
                raise LedgerUnreadable(
                    f"the render ledger at {path} exists but cannot be "
                    f"parsed ({exc}): left untouched, so the pipeline "
                    f"root keeps reading UNREADABLE and the sweep keeps "
                    f"refusing.") from exc
            if not isinstance(data, dict):
                raise LedgerUnreadable(
                    f"the render ledger at {path} holds "
                    f"{type(data).__name__}, not an object: left "
                    f"untouched, so the sweep keeps refusing.")
            overlay = data.get("subtitle_overlay")
            segments = (overlay or {}).get("segments") \
                if isinstance(overlay, dict) else None
            if not isinstance(segments, list):
                return {"renamed": 0, "ledger": path}
            for entry in segments:
                if not isinstance(entry, dict):
                    continue
                binding = entry.get("binding")
                if not isinstance(binding, dict):
                    continue
                new = claimed.get(str(binding.get("timeline") or ""))
                if new and new != binding.get("timeline"):
                    binding["timeline"] = new
                    renamed += 1
            if renamed:
                data["ledger_updated_at"] = \
                    datetime.now(timezone.utc).isoformat()
                handle.seek(0)
                handle.truncate()
                json.dump(data, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            _unlock(handle)
    return {"renamed": renamed, "ledger": path}


def _read_ledger_paths(path: str,
                       live_timelines: frozenset | None
                       ) -> tuple[set[str], str]:
    """Overlay paths the render ledger references, timelines considered.

    With `live_timelines` given, an entry counts as a reference only
    when the timeline it binds to still EXISTS. A ledger is a record of
    what was rendered; once the timeline it was rendered for is gone,
    the entry records history and references nothing, so keeping it as
    a root makes the ledger a monotonic accumulator that no sweep can
    ever drain.

    The failure direction is chosen deliberately. `None` means the set
    of live timelines is UNKNOWN - no database was read - and then
    EVERY entry references, because an unknown timeline set reads
    exactly like a project with no timelines and would orphan the whole
    directory. An entry carrying no binding at all references too, for
    the same reason.
    """
    if live_timelines is None:
        return _read_json_paths(path)
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        return set(), f"cannot be read: {exc}"
    overlay = data.get("subtitle_overlay") if isinstance(data, dict) else None
    segments = (overlay or {}).get("segments") \
        if isinstance(overlay, dict) else None
    if not isinstance(segments, list):
        return _read_json_paths(path)
    found: set[str] = set()
    for entry in segments:
        if not isinstance(entry, dict):
            continue
        binding = entry.get("binding")
        named = str((binding or {}).get("timeline") or "") \
            if isinstance(binding, dict) else ""
        if named and named not in live_timelines:
            continue
        at = entry.get("overlay_path")
        if isinstance(at, str) and at:
            found.add(at)
        frames = entry.get("frames")
        if isinstance(frames, dict) and isinstance(frames.get("dir"), str) \
                and frames["dir"]:
            found.add(frames["dir"])
    return found, ""


def collect_pipeline_roots(project_folder: str,
                           live_timelines: Iterable[str] | None = None
                           ) -> list[RootResult]:
    """The pipeline's own records as roots: step output and manifest.

    A project whose caption step never ran has no record to read, and
    that is `no-record` - an empty root, not an unreadable one.
    Refusing the sweep on a missing record would refuse it forever on
    exactly the projects whose garbage predates the records. A record
    that EXISTS but cannot be read is `unreadable` and refuses.

    `live_timelines` narrows the RENDER LEDGER only, and only when it
    is given: see `_read_ledger_paths` for why an unknown set keeps
    everything. The step output and the manifest are read whole either
    way - they describe the current plan, not an accumulated history.
    """
    live = None if live_timelines is None else frozenset(live_timelines)
    roots = []
    layout = ProjectLayout(project_folder)
    ledger = layout.step_dir(STEP_NODE_ID) / RENDER_LEDGER_NAME
    candidates = [
        ("pipeline:render_subtitles",
         [layout.pipeline_data_path,
          layout.step_dir(STEP_NODE_ID) / "output.json",
          ledger]),
        ("pipeline:assembly-manifest",
         [layout.step_dir("compile_manifest") / "assembly_manifest.json",
          layout.step_dir("compile_manifest") / "output.json"]),
    ]
    for name, paths in candidates:
        existing = [p for p in paths if p.is_file()]
        if not existing:
            roots.append(RootResult(name=name, status=NO_RECORD,
                                    detail="no record: this project never "
                                    "wrote one", kind="pipeline"))
            continue
        merged: set[str] = set()
        problems = []
        for path in existing:
            if path == ledger:
                found, problem = _read_ledger_paths(str(path), live)
            else:
                found, problem = _read_json_paths(str(path))
            if problem:
                problems.append(f"{path}: {problem}")
            merged |= {str(layout.resolve_project_relative(p))
                       for p in found}
        if problems:
            roots.append(RootResult(
                name=name, status=UNREADABLE, kind="pipeline",
                detail="; ".join(problems)))
        else:
            roots.append(RootResult(
                name=name, status=OK, paths=merged, kind="pipeline",
                detail=f"{len(merged)} referenced path(s)"))
    return roots


def collect_resolve_roots(db_paths: list[str]) -> list[RootResult]:
    """Every timeline's placed files, per database, through a COPY.

    `placed_paths_from_database` copies the database into a temp dir
    and opens the copy read-only, so the captain's live file is never
    opened in place. A database that cannot be read is an `unreadable`
    root - a reason to refuse, never a reason to treat everything
    under it as unreachable.
    """
    from library.tools.execution.prune_orphans import (
        placed_paths_from_database,
        timeline_names_from_database,
    )
    roots = []
    for db_path in db_paths:
        name = f"resolve:{Path(str(db_path)).parent.name}"
        if not os.path.isfile(str(db_path)):
            roots.append(RootResult(
                name=name, status=UNREADABLE, kind="resolve",
                db_path=str(db_path),
                detail=f"no database at {db_path}"))
            continue
        try:
            placed = placed_paths_from_database(str(db_path))
            timelines = timeline_names_from_database(str(db_path))
        except Exception as exc:  # noqa: BLE001 - any failure refuses
            roots.append(RootResult(
                name=name, status=UNREADABLE, kind="resolve",
                db_path=str(db_path), detail=f"cannot be read: {exc}"))
            continue
        roots.append(RootResult(
            name=name, status=OK, paths=set(placed), kind="resolve",
            db_path=str(db_path), timelines=set(timelines),
            detail=f"{len(placed)} placed path(s), "
                   f"{len(timelines)} timeline(s)"))
    return roots


def live_timelines_from(roots: Iterable[RootResult]) -> frozenset | None:
    """The timelines that EXIST, off the resolve roots, or None.

    `None` means no readable Resolve root was among them, so the live
    set is unknown - and every reader here treats unknown as "keep
    everything" rather than "nothing is live". One function, because
    the mark and the sweep deriving this differently would let a sweep
    refuse for ever on a mark it should have agreed with.
    """
    readable = [r for r in roots if r.kind == "resolve" and r.status == OK]
    if not readable:
        return None
    names: set[str] = set()
    for root in readable:
        names |= set(root.timelines)
    return frozenset(names)


# ── Mark ──────────────────────────────────────────────────────────────


@dataclass
class MarkResult:
    """What the mark computed, in full."""

    project_folder: str
    asset_dir: str
    created_at: str
    roots: list[RootResult]
    assets: list[AssetVerdict]
    fingerprint: dict = field(default_factory=dict)
    root_versions: dict = field(default_factory=dict)

    @property
    def live(self) -> list[AssetVerdict]:
        return [a for a in self.assets if a.status == LIVE]

    @property
    def orphans(self) -> list[AssetVerdict]:
        return [a for a in self.assets if a.status == ORPHAN]

    @property
    def bytes_live(self) -> int:
        return sum(a.size_bytes for a in self.live)

    @property
    def bytes_orphaned(self) -> int:
        return sum(a.size_bytes for a in self.orphans)

    def saved_per_root(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for asset in self.live:
            counts[asset.saved_by] = counts.get(asset.saved_by, 0) + 1
        return counts

    def to_dict(self) -> dict:
        return {
            "project_folder": self.project_folder,
            "asset_dir": self.asset_dir,
            "created_at": self.created_at,
            "roots": [{"name": r.name, "status": r.status,
                       "kind": r.kind, "db_path": r.db_path,
                       "detail": r.detail,
                       "timelines": sorted(r.timelines),
                       "paths": sorted(r.paths)}
                      for r in self.roots],
            "assets": [vars(a) for a in self.assets],
            "fingerprint": self.fingerprint,
            "root_versions": self.root_versions,
        }

    def write_json(self, path: str) -> str:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, indent=2)
        return path

    @staticmethod
    def read_json(path: str) -> "MarkResult":
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        roots = [RootResult(name=r["name"], status=r["status"],
                            paths=set(r.get("paths", [])),
                            detail=r.get("detail", ""),
                            kind=r.get("kind", "memory"),
                            db_path=r.get("db_path", ""),
                            timelines=set(r.get("timelines", [])))
                 for r in data.get("roots", [])]
        assets = [AssetVerdict(**a) for a in data.get("assets", [])]
        return MarkResult(
            project_folder=data.get("project_folder", ""),
            asset_dir=data.get("asset_dir", ""),
            created_at=data.get("created_at", ""),
            roots=roots, assets=assets,
            fingerprint=data.get("fingerprint", {}),
            root_versions=data.get("root_versions", {}))


def fingerprint_dir(asset_dir: str) -> dict:
    """Every file in the directory, sized and timestamped.

    Covers ALL files, not just recognised assets: a new render, a new
    record, a migration-lane write - any of them makes a mark stale.
    """
    snap = {}
    try:
        names = sorted(os.listdir(asset_dir))
    except OSError:
        return snap
    for name in names:
        full = os.path.join(asset_dir, name)
        try:
            if os.path.isdir(full) and not os.path.islink(full):
                snap[name] = ["dir", _tree_size(full)]
            else:
                stat = os.stat(full)
                snap[name] = [stat.st_size, stat.st_mtime_ns]
        except OSError:
            snap[name] = ["missing", 0]
    return snap


def _version_of(path: str) -> list:
    try:
        stat = os.stat(path)
    except OSError:
        return ["absent", 0, 0]
    return ["present", stat.st_size, stat.st_mtime_ns]


def root_versions(project_folder: str,
                  roots: list[RootResult]) -> dict:
    """The versions of everything the mark's roots were read from."""
    versions = {}
    for root in roots:
        if root.kind == "resolve" and root.db_path:
            versions[root.name] = _version_of(root.db_path)
    layout = ProjectLayout(project_folder)
    for label, path in (
            ("pipeline_data.json", str(layout.pipeline_data_path)),
            ("render_subtitles/output.json",
             str(layout.step_dir(STEP_NODE_ID) / "output.json")),
            ("render_subtitles/render_ledger.json",
             str(layout.step_dir(STEP_NODE_ID) / RENDER_LEDGER_NAME)),
            ("compile_manifest/assembly_manifest.json",
             str(layout.step_dir("compile_manifest")
                 / "assembly_manifest.json"))):
        versions[label] = _version_of(path)
    return versions


def _superseded_map(project_folder: str) -> dict[str, str]:
    """Old generation -> newer generation, as the caption step recorded.

    Step 4.05 names the generation each render superseded on the new
    entry (`render_one_segment`), so the mark can say not just that a
    file is orphaned but what replaced it. Read from the step output
    and the render ledger alike, so renders recorded outside a pipeline
    run attribute the same as ones inside it. Read-only;
    absent/unreadable records contribute nothing rather than refusing,
    because this is attribution, not reachability.
    """
    mapping: dict[str, str] = {}
    try:
        layout = ProjectLayout(project_folder)
    except Exception:  # noqa: BLE001 - attribution only, never refuses
        return mapping
    for path in (layout.step_dir(STEP_NODE_ID) / "output.json",
                 layout.step_dir(STEP_NODE_ID) / RENDER_LEDGER_NAME):
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            continue
        segments = []
        if isinstance(data, dict):
            overlay = data.get("subtitle_overlay") or {}
            segments = overlay.get("segments") or []
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            newer = segment.get("overlay_path") or ""
            for older in segment.get("superseded") or []:
                if isinstance(older, str) and older:
                    mapping[str(layout.resolve_project_relative(older))] = \
                        str(newer)
    return mapping


def mark(project_folder: str, asset_dir: str,
         roots: list[RootResult]) -> MarkResult:
    """Compute reachability. Read-only: writes nothing, anywhere."""
    records = enumerate_assets(asset_dir)
    mov_paths = {r["path"] for r in records if r["kind"] == "mov"}
    reachable: dict[str, str] = {}
    for root in roots:
        if root.status != OK:
            continue
        for path in root.paths:
            reachable.setdefault(path, root.name)
    # The step's own superseded ledger: which generation replaced which.
    superseded_by = _superseded_map(project_folder)
    assets = []
    for record in records:
        path = record["path"]
        if record["kind"] in ("mov", "frames-dir"):
            saver = reachable.get(path, "")
            if saver:
                assets.append(AssetVerdict(
                    path=path, size_bytes=record["size_bytes"],
                    status=LIVE, saved_by=saver,
                    card=record.get("card", ""),
                    digest=record.get("digest", ""),
                    kind=record["kind"]))
            else:
                newer = superseded_by.get(path, "")
                reason = ("placed on no timeline and named by no "
                          "pipeline record or manifest")
                if newer:
                    reason += f"; superseded at re-render time by {newer}"
                assets.append(AssetVerdict(
                    path=path, size_bytes=record["size_bytes"],
                    status=ORPHAN, reason=reason,
                    card=record.get("card", ""),
                    digest=record.get("digest", ""),
                    kind=record["kind"],
                    superseded_by=newer))
        elif record["kind"] == "sibling":
            owner = os.path.join(asset_dir, record["mov_stem"] + ".mov")
            if owner in mov_paths:
                saver = reachable.get(owner, "")
                if saver:
                    assets.append(AssetVerdict(
                        path=path, size_bytes=record["size_bytes"],
                        status=LIVE, saved_by=saver,
                        reason="sibling of a live mov",
                        kind="sibling"))
                else:
                    assets.append(AssetVerdict(
                        path=path, size_bytes=record["size_bytes"],
                        status=ORPHAN,
                        reason="sibling of an orphaned mov - "
                        "siblings follow their mov",
                        kind="sibling"))
            else:
                assets.append(AssetVerdict(
                    path=path, size_bytes=record["size_bytes"],
                    status=ORPHAN,
                    reason="sibling whose mov is absent - the render "
                    "that owned it never produced one",
                    kind="lone-sibling"))
        else:
            saver = reachable.get(path, "")
            if saver:
                assets.append(AssetVerdict(
                    path=path, size_bytes=record["size_bytes"],
                    status=LIVE, saved_by=saver, kind="unknown"))
            else:
                assets.append(AssetVerdict(
                    path=path, size_bytes=record["size_bytes"],
                    status=ORPHAN,
                    reason="unrecognised shape, placed on no timeline "
                    "and named by no record", kind="unknown"))
    return MarkResult(
        project_folder=str(project_folder),
        asset_dir=str(asset_dir),
        created_at=datetime.now(timezone.utc).isoformat(),
        roots=list(roots), assets=assets,
        fingerprint=fingerprint_dir(asset_dir),
        root_versions=root_versions(project_folder, roots))


# ── Sweep ─────────────────────────────────────────────────────────────


def _quarantine_base(project_folder: str, when: str) -> Path:
    layout = ProjectLayout(project_folder)
    return Path(str(layout.write_path(
        Area.QUARANTINE, "caption_assets", when)))


def render_sweep_manifest(result: MarkResult, record: dict) -> str:
    """Every moved path, in full: the manifest is the only way back."""
    gib = record["bytes_reclaimed"] / (1024 ** 3)
    lines = [
        "# Caption assets moved to quarantine",
        "",
        f"- Pipeline project: `{result.project_folder}`",
        f"- Asset directory: `{result.asset_dir}`",
        f"- Marked at: {result.created_at}",
        f"- Swept at: {record['swept_at']}",
        "",
        "Every path below was a rendered caption asset NOTHING "
        "referenced - placed on no timeline and named by no pipeline "
        "record or manifest - at mark time, re-proved at move time. "
        "See `library/tools/caption_asset_gc.py`.",
        "",
        "## Counts",
        "",
        f"- Assets moved: **{record['moved_count']}**",
        f"- Bytes reclaimed to quarantine: "
        f"**{gib:.2f} GiB** ({record['bytes_reclaimed']} B)",
        f"- Assets still live: **{record['live_count']}** "
        f"({record['bytes_live'] / (1024 ** 3):.2f} GiB)",
        "",
        "## Roots that saved the live assets",
        "",
    ]
    for name in sorted(record["saved_per_root"]):
        lines.append(f"- `{name}`: {record['saved_per_root'][name]} "
                     f"asset(s)")
    lines += ["", "## Moved to quarantine "
              f"({record['moved_count']})", "",
              "Moved, never deleted: each path below now lives under "
              "`quarantine/caption_assets/<stamp>/` with its filename "
              "unchanged.", ""]
    for path in record["moved"]:
        lines.append(f"- `{path}`")
    lines.append("")
    return "\n".join(lines)


def render_mark_report(result: MarkResult) -> str:
    """The mark, in the sentences an operator has to read."""
    gib_live = result.bytes_live / (1024 ** 3)
    gib_orph = result.bytes_orphaned / (1024 ** 3)
    lines = [
        "# Caption asset reachability mark",
        "",
        f"- Pipeline project: `{result.project_folder}`",
        f"- Asset directory: `{result.asset_dir}`",
        f"- Marked at: {result.created_at}",
        "",
        f"- LIVE: **{len(result.live)}** assets, "
        f"**{gib_live:.2f} GiB**",
        f"- ORPHAN: **{len(result.orphans)}** assets, "
        f"**{gib_orph:.2f} GiB** reclaimable to quarantine",
        "",
        "## Roots",
        "",
    ]
    for root in result.roots:
        lines.append(f"- `{root.name}`: {root.status} - {root.detail}")
    lines += ["", "## Assets saved per root", ""]
    for name in sorted(result.saved_per_root()):
        lines.append(f"- `{name}`: {result.saved_per_root()[name]} "
                     "asset(s)")
    unreadable = [r for r in result.roots if r.status == UNREADABLE]
    if unreadable:
        lines += ["",
                  "## REFUSED ROOTS",
                  "",
                  "These roots could not be read. The sweep will REFUSE "
                  "while any root is unreadable - an unreadable root "
                  "reads exactly like an empty project and would mark "
                  "every file deletable.",
                  ""]
        for root in unreadable:
            lines.append(f"- `{root.name}`: {root.detail}")
    lines.append("")
    return "\n".join(lines)


def write_mark_report(result: MarkResult,
                      report_path: str = "") -> dict[str, str]:
    """The mark as JSON (for the sweep) plus markdown (for a human)."""
    if not report_path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        layout = ProjectLayout(result.project_folder)
        report_path = str(layout.write_path(
            Area.QUARANTINE, f"mark_{stamp}.json"))
    Path(report_path).parent.mkdir(parents=True, exist_ok=True)
    result.write_json(report_path)
    markdown_path = os.path.splitext(report_path)[0] + ".md"
    with open(markdown_path, "w", encoding="utf-8") as handle:
        handle.write(render_mark_report(result))
    return {"json": report_path, "markdown": markdown_path}


def sweep(mark_path: str, project_folder: str = "",
          db_paths: list[str] | None = None,
          fresh_roots: list[RootResult] | None = None,
          manifest_tag: str = "") -> dict:
    """Move a marked orphan set to quarantine.

    Refuses (moving NOTHING) when the mark file cannot be read, when
    the mark recorded any root as unreadable, when the asset directory
    changed since the mark (stale), when any root is unreadable NOW, or
    when any candidate became referenced since the mark. A root that
    could not be read is a reason to refuse, never a reason to treat
    everything under it as unreachable.

    The full manifest is written BEFORE anything moves, and every
    moved path - small records included - is in it.  `manifest_tag`
    names the swept area in the manifest filename (the build sweep
    passes one per area); without it the legacy untagged name is
    kept.  Either way the name is suffixed past a collision, because
    two areas swept in the same second - measured 2026-09-10, when
    the motion-graphics manifest landed on the subtitle one - must
    not share a file.
    """
    project_folder = project_folder or ""
    try:
        result = MarkResult.read_json(mark_path)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SweepRefused(
            f"the mark at {mark_path} cannot be read ({exc}): there is "
            f"no marked set to act on. Nothing was moved.") from exc
    if result.project_folder and project_folder \
            and result.project_folder != project_folder:
        raise SweepRefused(
            f"the mark is for {result.project_folder} but the sweep was "
            f"asked for {project_folder}. Nothing was moved.")
    if not project_folder:
        project_folder = result.project_folder
    asset_dir = result.asset_dir

    # Staleness first: any landing since the mark voids it.
    current_print = fingerprint_dir(asset_dir)
    if current_print != result.fingerprint:
        raise SweepRefused(
            f"the mark at {mark_path} (taken {result.created_at}) is "
            f"stale: the asset directory changed since. Re-run the "
            f"mark. Nothing was moved.")

    # The roots NOW, re-established rather than carried over.
    if fresh_roots is not None:
        current = list(fresh_roots)
    else:
        current = _restablish(result, project_folder, db_paths)
    by_name = {r.name: r for r in current}

    # A root the MARK could not read voids the mark's orphan set,
    # unless that same root reads now.
    for recorded in result.roots:
        if recorded.status != UNREADABLE:
            continue
        now = by_name.get(recorded.name)
        if now is None or now.status == UNREADABLE:
            raise SweepRefused(
                f"root {recorded.name!r} was unreadable at mark time "
                f"({recorded.detail}) and is still unreadable: an "
                f"unreadable root reads exactly like an empty project "
                f"and would mark every file deletable. Nothing was "
                f"moved.")
    for root in current:
        if root.status == UNREADABLE:
            raise SweepRefused(
                f"root {root.name!r} is unreadable now ({root.detail}): "
                f"an unreadable root reads exactly like an empty "
                f"project and would mark every file deletable. Nothing "
                f"was moved.")

    # Re-prove every candidate against the CURRENT union: the mark was
    # a claim about an earlier moment and this is the move.
    reachable_now: set[str] = set()
    for root in current:
        reachable_now |= set(root.paths)
    orphans = [a for a in result.assets if a.status == ORPHAN]
    for asset in orphans:
        if asset.path in reachable_now:
            raise SweepRefused(
                f"{asset.path} became referenced since the mark "
                f"(saved now by a current root). The mark no longer "
                f"describes what is there. Nothing was moved.")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest_base = _quarantine_base(project_folder, stamp)
    tag = f"{manifest_tag}_" if manifest_tag else ""
    manifest_path = str(dest_base.parent / f"sweep_{tag}{stamp}_manifest.md")
    sibling = 2
    while os.path.exists(manifest_path):
        manifest_path = str(
            dest_base.parent / f"sweep_{tag}{stamp}_{sibling}_manifest.md")
        sibling += 1
    moved = []
    for asset in orphans:
        if not os.path.exists(asset.path):
            continue  # already gone; the manifest says so by absence
        dest = dest_base / os.path.basename(asset.path)
        n = 1
        while dest.exists():
            dest = dest_base / (os.path.basename(asset.path)
                                + f".{n}")
            n += 1
        dest_base.mkdir(parents=True, exist_ok=True)
        shutil.move(asset.path, str(dest))
        moved.append(asset.path)
    record = {
        "swept_at": datetime.now(timezone.utc).isoformat(),
        "mark_path": mark_path,
        "mark_created_at": result.created_at,
        "moved": sorted(moved),
        "moved_count": len(moved),
        "live_count": len(result.live),
        "bytes_live": result.bytes_live,
        "bytes_reclaimed": sum(
            a.size_bytes for a in orphans
            if a.path in set(moved)),
        "saved_per_root": result.saved_per_root(),
        "quarantine_dir": str(dest_base),
        "manifest_path": manifest_path,
    }
    Path(manifest_path).parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w", encoding="utf-8") as handle:
        handle.write(render_sweep_manifest(result, record))
    return record


def _restablish(result: MarkResult, project_folder: str,
                db_paths: list[str] | None) -> list[RootResult]:
    """Re-collect the mark's roots from their sources.

    Resolve roots re-read their recorded database paths (an explicit
    `db_paths` wins over the recorded ones); pipeline roots
    re-collect from the project. A root that cannot be re-established
    refuses rather than being skipped.
    """
    out: list[RootResult] = []
    recorded_db = [r.db_path for r in result.roots
                   if r.kind == "resolve" and r.db_path]
    if db_paths is not None:
        out.extend(collect_resolve_roots(list(db_paths)))
    elif recorded_db:
        out.extend(collect_resolve_roots(recorded_db))
    if any(r.kind == "pipeline" for r in result.roots):
        # The SAME derivation the mark used, off the roots just
        # re-read: a sweep that narrowed the ledger differently from
        # the mark would find candidates "newly referenced" and refuse
        # for ever on a mark it should have agreed with.
        out.extend(collect_pipeline_roots(
            project_folder, live_timelines=live_timelines_from(out)))
    recorded_memory = [r.name for r in result.roots
                       if r.kind == "memory"]
    if recorded_memory:
        raise SweepRefused(
            f"root(s) {recorded_memory} cannot be re-established from "
            f"a source: re-run the mark, or pass fresh_roots. Nothing "
            f"was moved.")
    return out


# ── CLI: the mark is read-only; the sweep moves ───────────────────────


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Reachability-based garbage collection for rendered "
        "caption assets. The mark is read-only; the sweep moves to "
        "quarantine and never deletes.")
    sub = parser.add_subparsers(dest="command", required=True)
    mark_p = sub.add_parser("mark", help="compute reachability")
    mark_p.add_argument("--project", required=True)
    mark_p.add_argument("--db", action="append", default=[],
                        help="Resolve Project.db path (repeatable); "
                        "the copy-before-open is handled inside")
    mark_p.add_argument("--out", default="",
                        help="mark JSON path; default is the project's "
                        "quarantine area")
    sweep_p = sub.add_parser("sweep", help="move a marked set")
    sweep_p.add_argument("--mark", required=True)
    sweep_p.add_argument("--project", default="")
    sweep_p.add_argument("--db", action="append", default=None)
    rec_p = sub.add_parser(
        "reconcile",
        help="adopt the step's on-disk outputs into its render ledger")
    rec_p.add_argument("--project", required=True)
    rec_p.add_argument("--db", action="append", default=[],
                       help="Resolve Project.db path (repeatable); "
                       "read through a copy, for the embedded pre-image")
    args = parser.parse_args(argv)
    if args.command == "mark":
        layout = ProjectLayout(args.project)
        asset_dir = str(layout.read_dir(Area.SUBTITLE_SEGMENTS))
        roots = collect_resolve_roots(args.db)
        roots += collect_pipeline_roots(
            args.project, live_timelines=live_timelines_from(roots))
        result = mark(args.project, asset_dir, roots)
        written = write_mark_report(result, args.out)
        print(render_mark_report(result))
        print(f"mark written to {written['json']}")
        print(f"report written to {written['markdown']}")
        return 0
    if args.command == "reconcile":
        adopted = reconcile_render_ledger(args.project,
                                          db_paths=args.db)
        pre = adopted["mark"]
        print(render_mark_report(pre))
        print(f"ledger: {adopted['ledger']} "
              f"({adopted['recorded']} adopted segment(s))")
        return 0
    record = sweep(args.mark, project_folder=args.project,
                   db_paths=args.db)
    print(f"moved {record['moved_count']} asset(s), "
          f"{record['bytes_reclaimed'] / (1024 ** 3):.2f} GiB "
          f"to {record['quarantine_dir']}")
    print(f"manifest: {record['manifest_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())