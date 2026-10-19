"""What a project keeps once a reel is signed off: `lean` or `keep`, per user.

The captain, 2026-09-23 (D6): *"Until the reel is signed off, then
purge ... based on user preference. for me i like to have all bloat
removed to keep the workspace lean, but someone else might like to have
versioned copies and scratch work and renders just in case"*. Measured
the same day on geo-podcast: `pipeline_output` 4.9 GB, of which
superseded caption renders were up to 1.35 GB, `quarantine/` 1.5 GB
that nothing had ever purged, `scratch/` 1.0 GB, and `review/` 275
proposal snapshots plus 501 retirement and placement journals.

The setting
-----------
`REN_RETENTION` in the per-user config (`~/.config/ren/config.env`,
`library/tools/paths.py`), `lean` or `keep`, and `lean` when unset -
the captain's own default. A project's top-level `retention` key may
override that default for project cleanup. Anything else REFUSES: a
misspelt `keep` read as `lean` would delete what its owner asked to keep.

- `keep`: project artifacts and intermediate work are retained. The
  machine workspace collector may still collect explicitly disposable
  unreferenced Ren scratch files.
- `lean`: after a sign-off, six classes go (`CATEGORIES`).

The mechanism: plan, manifest, apply
------------------------------------
`plan_purge` is READ-ONLY. It computes the removal set, and
`write_plan` writes it as a manifest - one path per line with its size
- plus the plan's JSON beside it, under
`quarantine/purge_plans/`. Nothing is removed until `apply_purge` is
called on that manifest, and apply removes only the paths still listed
in it (strike a line to keep that path) after RE-PROVING every one of
them against a fresh plan. Any disagreement refuses the whole apply and
removes NOTHING.

What is never a candidate
-------------------------
The reachability rules are `caption_asset_gc`'s and `build_sweep`'s,
built on rather than restated:

- **Anything a live timeline references.** The Resolve roots are read
  through a COPY of `Project.db` (`caption_asset_gc.collect_resolve_roots`),
  and a purge with no readable database REFUSES: without the timelines,
  "nothing references this" is unprovable, and an unread root reads
  exactly like an empty project.
- **Anything an unsigned reel needs.** The render ledger stops pinning
  an entry whose timeline is gone (`caption_asset_gc._read_ledger_paths`),
  which is right for a signed-off reel and wrong for one still in
  flight: a reel between builds has no timeline, and its renders would
  read as orphans. So every ledger timeline whose REEL
  (`reel_signoff.base_name`) carries no sign-off is kept as if live.
  Only a signed-off reel's superseded renders are released.
- **The project's declared inputs.** Every candidate is passed through
  `ProjectLayout.assert_writable`, which refuses any path inside a
  `Kind.INPUT` area, the bare project root, or outside the project.
- **Live records.** Only files matching a declared stamped-journal
  family (`JOURNAL_FAMILIES`) are journals, and the newest of each
  family stays. `reel_proposals_v2.json`, `reel_signoffs.json`,
  `staging_holds.json`, `rounds.json` and every other unstamped record
  match no family, so they cannot be named.
- **Timeline interchange and recovery archives.** A path ending in
  `NEVER_PURGE_SUFFIXES` (`.drt`, `.drp`, `.otio`) is never a
  candidate, in any class: an interchange export or a recovery archive
  is the way back for work no render can reproduce. Measured
  2026-10-08 on geo-podcast: the scratch sweep named a caption-smoke
  `.drt` its class would otherwise have taken.
- **A live asset's render record.** In the reel asset areas a sidecar
  (props, full-frame still) belongs to its content-hash group, not to
  a mov the way caption siblings do: when any file of the group is
  placed, the group's non-movie files stay with it. Movies are judged
  one by one - a previous carriage (`post_header_*_tight.mov`,
  `tv_frame_*.mov`) or a legacy hold (`reel_freeze_*.mov`) no timeline
  plays is superseded whatever its group holds.
- **A trash collection's manifest.** `asset_gc`'s `manifest.json` is
  the record of what the collection moved and why; the purge removes
  the trashed files and keeps their map, the same way it keeps its own
  `purge_plans/` out of the quarantine class.

A purge also refuses while a pipeline run holds the project
(`run_control.is_running`).

Quarantine has a 24-hour grace period measured from the timestamped
caption sweep directory. The eligibility time is recorded in each plan;
an unknown quarantine path stays protected. Scratch with no readers is
proven disposable and remains eligible for immediate removal after a
fresh reachability plan. Recovery archives remain excluded by suffix.

`tests/unit/context/test_project_layout.py`.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import stat
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from library.tools import build_sweep, journal_naming
from library.tools.resolve_lock import under_lease
from library.tools import caption_asset_gc as gc
from library.tools.ren_refusal import RenRefusal
from library.tools.project_layout import (
    Area,
    ProjectLayout,
    ProjectLayoutViolation,
)

SETTING = "REN_RETENTION"
LEAN = "lean"
KEEP = "keep"
MODES = (LEAN, KEEP)
DEFAULT_MODE = LEAN
"""The captain's own preference (D6). A user who wants copies sets `keep`."""

SUPERSEDED_RENDER = "superseded-render"
QUARANTINED = "quarantine"
SCRATCH = "scratch"
STALE_JOURNAL = "stale-journal"
SUPERSEDED_REEL_ASSET = "superseded-reel-asset"
TRASHED_ASSET = "trashed-asset"
CATEGORIES = (SUPERSEDED_RENDER, QUARANTINED, SCRATCH, STALE_JOURNAL,
              SUPERSEDED_REEL_ASSET, TRASHED_ASSET)
"""Everything `lean` removes. COMPLETE: a class not here is never planned."""

PURGE_PLAN_DIR = "purge_plans"
"""Under `Area.QUARANTINE`, and exempt from the quarantine class: the
manifest is the record of what went, so a purge must not take it."""

PURGE_PREFIX = "purge"
QUARANTINE_GRACE_SECONDS = 24 * 60 * 60
DISPOSABLE = "proven-disposable"
RECOVERABLE = "recoverable-intermediate"
RECOVERY_CRITICAL = "recovery-critical"

NEVER_PURGE_SUFFIXES = (".drt", ".drp", ".otio")
"""Timelines and interchange a purge must never name, in any class.

`.drt` recovery archives are the way back for timeline work no render
reproduces; `.drp`/`.otio` are interchange exports of the same. The
suffix is the whole rule: no area, record shape or ledger entry can
re-admit one."""

REEL_ASSET_AREAS = (
    Area.REEL_POST_HEADERS,
    Area.REEL_CARDS,
    Area.REEL_FRAME_OVERLAYS,
    Area.REEL_SAFE_ZONE_GUIDES,
)
"""`build_reels` OUTPUT areas whose unreferenced files purge as
`SUPERSEDED_REEL_ASSET`. COMPLETE for the reel build's placed files:
every file a reel timeline places lives in one of these four, so a
file here no root names is a previous carriage the still migration
left behind (`post_header_*_tight.mov`, `tv_frame_*.mov`), a legacy
hold new builds never create (`reel_freeze_*.mov`), or a dead
content-hash group - all judged by the same roots as the swept caption
areas, through `caption_asset_gc.mark`."""

REEL_REGENERATE = {
    Area.REEL_POST_HEADERS:
        "manage_project.py run <project> --only build_reels "
        "--rerun build_reels",
    Area.REEL_CARDS:
        "manage_project.py run <project> --only build_reels "
        "--rerun build_reels",
    Area.REEL_FRAME_OVERLAYS:
        "manage_project.py run <project> --only build_reels "
        "--rerun build_reels",
    Area.REEL_SAFE_ZONE_GUIDES:
        "manage_project.py safe-zones <project> --overlay combined "
        "(library/tools/safe_zone_guide.py)",
}
"""The command that brings each reel area's files back. Post headers
re-render on demand when the still is missing (`reel_post_header`),
so a purged still costs a render, never the reel."""

TRASH_DIRNAME = "trash"
"""Under `pipeline_output/`, owned by `library/tools/asset_gc.py`: one
`asset_gc_<stamp>/` collection per run. Not a layout `Area` - nothing
writes here but the collector - so the purge walks it by name and the
layout guard reads it as the writable output root."""

TRASH_MANIFEST_BASENAMES = ("manifest.json",)
"""Collection records the purge keeps. The files a collection moved go;
their map stays, beside the purge's own manifest."""

JOURNAL_FAMILIES = (
    (Area.REVIEW, "reel_proposals_v2"),
    (Area.REVIEW, "resolve_retirements"),
    (Area.REVIEW, "resolve_placements"),
    (Area.REVIEW, "resolve_prune"),
    (Area.REVIEW, "resolve_remove_proof"),
    (Area.REVIEW, "resolve_timeline_deletions"),
)
"""The stamped journals `lean` thins to their newest, by area and prefix.

A journal is `<prefix>_<YYYYMMDDTHHMMSSZ>[_N]<tail>` (`journal_naming`),
and a journal and its `_manifest.md` share a stamp, so they are kept or
removed together. Declared, never discovered: an unknown stamped name
is left alone. `touchup_*` is absent on purpose - a touch-up's account
lives on its undo journal entry since cascade Lane E2 (R6), and the
pre-lane `touchup_*.json` files are ignored history, not this
module's to thin."""

KEEP_NEWEST_JOURNALS = 1
"""The newest act of each family is the one an undo would read."""

_STAMPED = re.compile(
    r"^(?P<prefix>.+?)_(?P<stamp>\d{8}T\d{6}Z)(?:_(?P<n>\d+))?"
    r"(?P<tail>_manifest\.md|\.json|\.txt)$")


class RetentionSettingInvalid(RenRefusal):
    """`REN_RETENTION` names neither mode."""


class PurgeRefused(RenRefusal):
    """The purge declined, and removed NOTHING."""


def retention_mode(environ=None, project_folder: str = "") -> str:
    """Resolve machine preference, overridden by a project's declaration.

    The project key is top-level ``retention`` in ``project.yaml``. The
    environment setting remains the machine default for projects without
    an override. A malformed project declaration refuses before cleanup.
    """
    if project_folder:
        config_path = Path(project_folder) / "project.yaml"
        try:
            import yaml
        except ImportError as exc:
            raise RetentionSettingInvalid(
                "cannot parse project retention without PyYAML",
                "cleanup cannot establish the project's preference",
                "install the declared YAML dependency and plan again") from exc
        try:
            with config_path.open(encoding="utf-8") as handle:
                config = yaml.safe_load(handle) or {}
        except (OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
            raise RetentionSettingInvalid(
                f"cannot read project retention from {config_path}",
                f"({exc}); cleanup cannot establish the project's preference",
                "repair project.yaml and plan cleanup again") from exc
        if not isinstance(config, dict):
            raise RetentionSettingInvalid(
                f"project config {config_path} is not a mapping",
                "cleanup cannot establish the project's preference",
                "repair project.yaml and plan cleanup again")
        if "retention" in config:
            raw_project = str(config["retention"] or "").strip().lower()
            if raw_project not in MODES:
                raise RetentionSettingInvalid(
                    f"project retention {raw_project!r} is not a retention mode",
                    "unknown project policy cannot authorize deletion",
                    f"set retention to one of {list(MODES)} in {config_path}")
            return raw_project
    if environ is None:
        import library.tools.paths  # noqa: F401 - loads the user config
        environ = os.environ
    raw = str(environ.get(SETTING, "") or "").strip().lower()
    if not raw:
        return DEFAULT_MODE
    if raw not in MODES:
        raise RetentionSettingInvalid(
            f"{SETTING}={raw!r} is not a retention mode",
            f"reading an unknown value as `lean` would delete what its "
            f"owner may have asked to keep",
            f"set {SETTING} to one of {list(MODES)}, or unset it for "
            f"the default")
    return raw


# ── The plan ──────────────────────────────────────────────────────────


@dataclass
class Candidate:
    path: str
    size_bytes: int
    category: str
    reason: str
    is_dir: bool = False
    mtime_ns: int = 0
    inode: tuple = ()
    identity: tuple = ()
    eligible_after: str = ""
    deletion_class: str = RECOVERABLE
    allocated_bytes: int = 0


def _reclaimable_bytes(candidates: list[Candidate]) -> int:
    """Count bytes freed by removing these paths, accounting for hardlinks."""
    by_inode: dict = {}
    total = 0
    for candidate in candidates:
        if not candidate.inode:
            total += candidate.size_bytes
            continue
        entry = by_inode.setdefault(
            tuple(candidate.inode[:2]),
            [candidate.size_bytes, candidate.inode[2], 0])
        entry[2] += 1
    for size, links, listed in by_inode.values():
        if listed >= links:
            total += size
    return total


def _physical_reclaimable_bytes(candidates: list[Candidate]) -> int:
    """Estimate allocated bytes freed, counting only fully removed inodes."""
    by_inode: dict[tuple[int, int], list[int]] = {}
    total = 0
    for candidate in candidates:
        allocated = candidate.allocated_bytes or candidate.size_bytes
        if not candidate.inode:
            total += allocated
            continue
        entry = by_inode.setdefault(
            tuple(candidate.inode[:2]), [allocated, candidate.inode[2], 0])
        entry[2] += 1
    for allocated, links, listed in by_inode.values():
        if listed >= links:
            total += allocated
    return total


def _free_space_snapshot(project_folder: str, *, required: bool = True) -> int | None:
    """Read actual free space on the project filesystem for purge receipts."""
    try:
        return shutil.disk_usage(project_folder).free
    except OSError as exc:
        if not required:
            return None
        raise PurgeRefused(
            f"cannot measure free space for {project_folder}: {exc}",
            "physical recovery cannot be verified; nothing further was removed",
            "restore filesystem access and retry the manifest") from exc


@dataclass
class PurgePlan:
    project_folder: str
    created_at: str
    mode: str
    candidates: list = field(default_factory=list)
    roots: list = field(default_factory=list)
    kept: list = field(default_factory=list)
    render_fingerprints: dict = field(default_factory=dict)
    protected_timelines: list = field(default_factory=list)
    applied: dict = field(default_factory=dict)

    @property
    def reclaimable_bytes(self) -> int:
        """Bytes that really leave the disk.

        A hard-linked file (`render_cache.adopt`) is counted once, and
        only when EVERY link to it is in the set: removing one name of
        a file another name still holds frees nothing.
        """
        return _reclaimable_bytes([
            candidate for candidate in self.candidates
            if candidate.deletion_class == DISPOSABLE
            or (candidate.category == QUARANTINED
                and candidate.eligible_after)])

    def to_dict(self) -> dict:
        data = asdict(self)
        data["reclaimable_bytes"] = self.reclaimable_bytes
        return data

    @staticmethod
    def from_dict(data: dict) -> PurgePlan:
        plan = PurgePlan(
            project_folder=data["project_folder"],
            created_at=data["created_at"], mode=data["mode"],
            roots=data.get("roots", []), kept=data.get("kept", []),
            render_fingerprints=data.get("render_fingerprints", {}),
            protected_timelines=data.get("protected_timelines", []),
            applied=data.get("applied", {}))
        plan.candidates = [Candidate(**{
            **c, "inode": tuple(c["inode"]),
            "identity": tuple(c.get("identity", ()))})
            for c in data["candidates"]]
        return plan


def _stat_candidate(path: str, category: str, reason: str,
                    eligible_after: str = "") -> Candidate:
    st = os.lstat(path)
    is_dir = os.path.isdir(path) and not os.path.islink(path)
    size = gc._tree_size(path) if is_dir else st.st_size
    inode = () if is_dir else (st.st_dev, st.st_ino, st.st_nlink)
    if is_dir:
        allocated_inodes = {}
        for root, _, files in os.walk(path, followlinks=False):
            for name in files:
                child = Path(root) / name
                info = os.lstat(child)
                if stat.S_ISREG(info.st_mode):
                    entry = allocated_inodes.setdefault(
                        (info.st_dev, info.st_ino),
                        [getattr(info, "st_blocks", 0) * 512,
                         info.st_nlink, 0])
                    entry[2] += 1
        allocated = sum(size for size, links, inside in allocated_inodes.values()
                        if inside >= links)
    else:
        allocated = getattr(st, "st_blocks", 0) * 512
    deletion_class = DISPOSABLE if category == SCRATCH else RECOVERABLE
    if path.endswith(NEVER_PURGE_SUFFIXES):
        deletion_class = RECOVERY_CRITICAL
    return Candidate(path=path, size_bytes=size, category=category,
                     reason=reason, is_dir=is_dir,
                     mtime_ns=st.st_mtime_ns, inode=inode,
                     identity=(st.st_dev, st.st_ino, st.st_mode,
                               st.st_size, st.st_mtime_ns),
                     eligible_after=eligible_after,
                     deletion_class=deletion_class,
                     allocated_bytes=allocated)


def _quarantine_eligible_after(path: str, quarantine_root: Path):
    """Read the sweep's recorded UTC stamp; unknown quarantine is protected."""
    try:
        relative = Path(path).relative_to(quarantine_root)
        stamp = relative.parts[1]
        quarantined_at = datetime.strptime(
            stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except (ValueError, IndexError):
        return None
    return quarantined_at + timedelta(seconds=QUARANTINE_GRACE_SECONDS)


def _files_under(root: Path, exclude: Path | None = None):
    """Every file under `root`, never following a link out of it."""
    if not root.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        if exclude is not None:
            dirnames[:] = [d for d in dirnames
                           if Path(dirpath, d) != exclude]
        for name in sorted(filenames):
            yield os.path.join(dirpath, name)


def _path_key(path, layout: ProjectLayout) -> str:
    resolved = layout.resolve_project_relative(path)
    return os.path.normcase(os.path.abspath(os.path.normpath(str(resolved))))


def _declared_step_file_inputs(project_folder: str) -> dict[str, list[str]]:
    """Project files declared as inputs by any process step.

    Step manifests own these paths. Enumerate the processes through their
    registry and resolve each path against the project so a declared input
    survives even when it sits in a purgeable area.
    """
    from library.tools import processes

    layout = ProjectLayout(project_folder)
    by_path: dict[str, list[str]] = {}
    for process_id in processes.process_ids():
        dag = processes.load_dag(process_id)
        manifests = processes.load_manifests(dag)
        for node_id, manifest in manifests.items():
            inputs = ((manifest.get("interface") or {}).get("inputs") or [])
            for declared in inputs:
                if "file_path" not in declared:
                    continue
                key = _path_key(declared["file_path"], layout)
                by_path.setdefault(key, []).append(
                    f"{node_id}.{declared['name']}")
    return by_path


def ledger_timelines(project_folder: str) -> set:
    """Every timeline the caption render ledger binds an entry to."""
    layout = ProjectLayout(project_folder)
    ledger = layout.step_dir(gc.STEP_NODE_ID) / gc.RENDER_LEDGER_NAME
    if not ledger.is_file():
        return set()
    try:
        data = json.loads(ledger.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PurgeRefused(
            f"the render ledger {ledger} cannot be read",
            f"({exc}): which reels its renders serve is unknown, and an "
            f"unknown root reads like an unreferenced file. Nothing was "
            f"planned",
            "restore the render ledger, then plan the purge again") \
            from exc
    names = set()
    for entry in ((data.get("subtitle_overlay") or {}).get("segments")
                  or []):
        binding = entry.get("binding") if isinstance(entry, dict) else None
        if isinstance(binding, dict) and binding.get("timeline"):
            names.add(str(binding["timeline"]))
    return names


def unsigned_timelines(project_folder: str, timelines) -> set:
    """The timelines whose REEL carries no sign-off - kept as if live."""
    from library.tools import reel_signoff

    signed = set(reel_signoff.signed_off(project_folder))
    return {t for t in timelines if reel_signoff.base_name(t) not in signed}


def collect_roots(project_folder: str, db_paths) -> tuple[list, set]:
    """The reachability roots, with every unsigned reel's ledger kept.

    Refuses when no database is given or any root is unreadable: both
    read exactly like a project that references nothing.
    """
    if not db_paths:
        raise PurgeRefused(
            "no Resolve project database to read",
            "without the timelines, whether a live timeline references a "
            "file is unprovable. Nothing was planned",
            "pass --db <Project.db> (repeatable), or run with Resolve "
            "open so the running project is read")
    resolve = gc.collect_resolve_roots(list(db_paths))
    live = gc.live_timelines_from(resolve)
    protected = set(live or ()) | unsigned_timelines(
        project_folder, ledger_timelines(project_folder))
    roots = resolve + gc.collect_pipeline_roots(
        project_folder, live_timelines=protected)
    unreadable = [r for r in roots if r.status == gc.UNREADABLE]
    if unreadable or live is None:
        detail = ", ".join(f"{r.name!r} ({r.detail})" for r in unreadable)
        raise PurgeRefused(
            f"root(s) {detail or 'Resolve'} could not be read",
            "an unreadable root reads exactly like a project with "
            "nothing on any timeline. Nothing was planned",
            "make the database readable (open Resolve, or fix the --db "
            "path), then plan the purge again")
    return roots, protected


def _journal_candidates(layout: ProjectLayout) -> list:
    families = [(layout.read_dir(area), prefix)
                for area, prefix in JOURNAL_FAMILIES]
    families.append((layout.read_dir(Area.QUARANTINE) / PURGE_PLAN_DIR,
                     PURGE_PREFIX))
    out = []
    for directory, prefix in families:
        if not directory.is_dir():
            continue
        groups: dict = {}
        for name in os.listdir(directory):
            match = _STAMPED.match(name)
            if not match or match["prefix"] != prefix:
                continue
            key = (match["stamp"], int(match["n"] or 1))
            groups.setdefault(key, []).append(str(directory / name))
        for key in sorted(groups)[:-KEEP_NEWEST_JOURNALS or None]:
            for path in sorted(groups[key]):
                out.append((path, (f"{prefix} journal older than the "
                                  f"newest {KEEP_NEWEST_JOURNALS}")))
    return out


_POST_HEADER_GROUP = re.compile(r"^(post_header_[0-9a-f]{10})")
"""The content-hash group a post-header file belongs to.

`reel_post_header` renders `post_header_<stamp>.png` keyed by its
props and cuts `<stamp>_tight.png` / `<stamp>_tight_still.png` from
it, so every file one render left shares the stem. A sidecar belongs
to that group, never to a mov the way caption siblings do."""


def _reel_group_stem(path: str) -> str | None:
    """The render group `path` belongs to, or None when it has none."""
    match = _POST_HEADER_GROUP.match(os.path.basename(path))
    return match.group(1) if match else None


def _reel_asset_candidates(project_folder: str, layout: ProjectLayout,
                            roots: list, plan: PurgePlan) -> list:
    """Orphaned files in the reel asset areas, group-aware.

    `caption_asset_gc.mark` judges every file by the same roots the
    swept caption areas use. Movies go one by one; a sidecar of a
    group with a placed member stays with it - the render record of a
    live asset is not debris. Returns `(path, category, reason)` like
    every other class below.
    """
    found = []
    for area in REEL_ASSET_AREAS:
        asset_dir = str(layout.read_dir(area))
        if not os.path.isdir(asset_dir):
            continue
        plan.render_fingerprints[asset_dir] = gc.fingerprint_dir(asset_dir)
        marked = gc.mark(project_folder, asset_dir, roots)
        live_stems = {_reel_group_stem(a.path) for a in marked.live}
        live_stems.discard(None)
        for asset in marked.orphans:
            stem = _reel_group_stem(asset.path)
            if stem in live_stems and not asset.path.endswith(".mov"):
                plan.kept.append(
                    {"path": asset.path, "category": SUPERSEDED_REEL_ASSET,
                     "why": (f"render record of live group {stem}: "
                             f"a placed file of the same render stays")})
                continue
            found.append((asset.path, SUPERSEDED_REEL_ASSET, asset.reason))
    return found


def _trash_candidates(project_folder: str, plan: PurgePlan) -> list:
    """Files `asset_gc` moved aside, re-proved unreferenced.

    The collection already proved each file unplaced when it moved it;
    the purge proves it again against the current roots before naming
    it, the same as every other class. Collection manifests stay (the
    record of what the collector did); the files go.
    """
    trash_root = os.path.join(project_folder, "pipeline_output",
                              TRASH_DIRNAME)
    if not os.path.isdir(trash_root):
        return []
    plan.render_fingerprints[trash_root] = gc.fingerprint_dir(trash_root)
    found = []
    for path in _files_under(Path(trash_root)):
        if os.path.basename(path) in TRASH_MANIFEST_BASENAMES:
            plan.kept.append(
                {"path": path, "category": TRASHED_ASSET,
                 "why": "trash collection manifest: the record of what "
                        "the collector moved"})
            continue
        found.append((path, TRASHED_ASSET,
                      "moved aside by asset_gc; placed on no timeline "
                      "and named by no pipeline record or manifest"))
    return found


def plan_purge(project_folder: str, db_paths, mode: str | None = None
                ) -> PurgePlan:
    """The removal set, computed and nothing more. Writes NOTHING."""
    from library.tools import run_control

    project_folder = str(project_folder)
    mode = mode or retention_mode(project_folder=project_folder)
    if mode not in MODES:
        raise RetentionSettingInvalid(
            f"unknown retention mode {mode!r}",
            "a purge plans only under a declared mode",
            f"pass mode {list(MODES)[0]!r} or {list(MODES)[1]!r}")
    plan = PurgePlan(project_folder=project_folder,
                     created_at=datetime.now(UTC).isoformat(),
                     mode=mode)
    if mode == KEEP:
        return plan
    if run_control.is_running(project_folder):
        raise PurgeRefused(
            f"a pipeline run holds {project_folder} "
            f"(pid {run_control.running_pid(project_folder)})",
            "a render in flight has no record yet. Nothing was planned",
            "wait for the run to finish, then plan the purge again")
    layout = ProjectLayout(project_folder)
    roots, protected = collect_roots(project_folder, db_paths)
    plan.protected_timelines = sorted(protected)
    plan.roots = [{"name": r.name, "status": r.status, "detail": r.detail}
                  for r in roots]
    reachable: set = set()
    for root in roots:
        reachable |= set(root.paths)

    found: list = []
    quarantine_expirations: dict[str, str] = {}
    for area in build_sweep.SWEPT_AREAS:
        asset_dir = str(layout.read_dir(area))
        if not os.path.isdir(asset_dir):
            continue
        plan.render_fingerprints[asset_dir] = gc.fingerprint_dir(asset_dir)
        for asset in gc.mark(project_folder, asset_dir, roots).orphans:
            found.append((asset.path, SUPERSEDED_RENDER, asset.reason))

    quarantine = layout.read_dir(Area.QUARANTINE)
    for path in _files_under(quarantine, exclude=quarantine / PURGE_PLAN_DIR):
        if Path(path).is_relative_to(quarantine / PURGE_PLAN_DIR):
            continue
        eligible_after = _quarantine_eligible_after(path, quarantine)
        if eligible_after is None:
            plan.kept.append({"path": path, "category": QUARANTINED,
                              "why": "quarantine time is not recorded; protected"})
            continue
        if datetime.now(UTC) < eligible_after:
            plan.kept.append({
                "path": path, "category": QUARANTINED,
                "eligible_after": eligible_after.isoformat(),
                "why": "recoverable quarantine grace period has not expired"})
            continue
        quarantine_expirations[path] = eligible_after.isoformat()
        found.append((path, QUARANTINED,
                      "moved aside by a build sweep; never placed since"))
    for path in _files_under(layout.read_dir(Area.SCRATCH)):
        found.append((path, SCRATCH, ("working file with no reader after "
                                     "the step that wrote it")))
    for path, reason in _journal_candidates(layout):
        found.append((path, STALE_JOURNAL, reason))
    found.extend(_reel_asset_candidates(project_folder, layout, roots,
                                        plan))
    found.extend(_trash_candidates(project_folder, plan))

    declared_inputs = _declared_step_file_inputs(project_folder)
    for path, category, reason in found:
        input_names = declared_inputs.get(_path_key(path, layout))
        if input_names:
            plan.kept.append({"path": path, "category": category,
                              "why": ("declared build input: "
                                      + ", ".join(input_names))})
            continue
        if path in reachable:
            plan.kept.append({"path": path, "category": category,
                              "why": "referenced by a current root"})
            continue
        if path.endswith(NEVER_PURGE_SUFFIXES):
            plan.kept.append(
                {"path": path, "category": category,
                 "why": ("timeline interchange or recovery archive "
                         "(.drt/.drp/.otio): never purged, in any class")})
            continue
        try:
            layout.assert_writable(path)
        except ProjectLayoutViolation as outside:
            raise PurgeRefused(
                f"{outside}",
                "a purge candidate outside the project layout cannot be "
                "judged. Nothing was planned",
                "move the file inside the project layout, then plan "
                "again") from outside
        if not os.path.lexists(path):
            continue
        if os.path.islink(path):
            plan.kept.append({"path": path, "category": category,
                              "why": "symbolic links are protected"})
            continue
        candidate_eligible_after = ""
        if category == QUARANTINED:
            candidate_eligible_after = quarantine_expirations[path]
        plan.candidates.append(_stat_candidate(
            path, category, reason, candidate_eligible_after))
    return plan


# ── The manifest ──────────────────────────────────────────────────────


def _json_path_for(manifest_path: str) -> str:
    return os.path.splitext(manifest_path)[0] + ".json"


def _schedule_quarantine_expiry(plan: PurgePlan, progress: dict) -> None:
    expirations = [
        row["eligible_after"] for row in plan.kept
        if row.get("category") == QUARANTINED and row.get("eligible_after")]
    expirations.extend(row["eligible_after"]
                       for row in progress.get("quarantined", {}).values())
    if not expirations:
        return
    from library.tools import artifact_lifecycle
    not_before = min(expirations)
    project_id = Path(plan.project_folder).resolve().name
    job_id = "quarantine-expiry-" + hashlib.sha256(
        f"{project_id}\0{not_before}".encode("utf-8")).hexdigest()[:16]
    artifact_lifecycle.schedule_cleanup_job(
        plan.project_folder,
        event_id=f"quarantine-expiry:{project_id}:{not_before}",
        project_id=project_id, run_id=plan.created_at, job_id=job_id,
        reason="purge recoverable quarantine after grace period",
        not_before=not_before)
    eligible = datetime.fromisoformat(not_before)
    delay = max(0.0, (eligible - datetime.now(UTC)).total_seconds())
    timer = threading.Timer(delay, resume_cleanup_jobs,
                            args=(plan.project_folder,))
    timer.daemon = True
    timer.start()


def render_manifest(plan: PurgePlan, manifest_path: str) -> str:
    gib = plan.reclaimable_bytes / (1024 ** 3)
    lines = [
        (f"# Ren purge plan ({plan.mode} retention) - NOTHING HAS BEEN "
         f"REMOVED."),
        f"# project: {plan.project_folder}",
        f"# planned: {plan.created_at}",
        f"# {len(plan.candidates)} path(s), {gib:.2f} GiB reclaimable.",
        (f"# Kept though a class would take them: {len(plan.kept)} "
         f"(referenced, declared, recorded or never-purged - see the "
         f"JSON)."),
        (f"# Apply: ren purge {plan.project_folder!r} --apply "
         f"{manifest_path!r}"),
        ("# Delete a line to keep that path: apply removes only what is "
         "listed."),
        "# <bytes>\t<category>\t<path>",
    ]
    for c in sorted(plan.candidates, key=lambda c: (c.category, c.path)):
        lines.append(f"{c.size_bytes}\t{c.category}\t{c.path}")
    return "\n".join(lines) + "\n"


def write_plan(plan: PurgePlan) -> str:
    """Write the manifest and its JSON. Returns the manifest path."""
    layout = ProjectLayout(plan.project_folder)
    directory = layout.write_path(Area.QUARANTINE, PURGE_PLAN_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    manifest = journal_naming.unique_path(str(directory), PURGE_PREFIX,
                                          suffix=".txt")
    Path(manifest).write_text(render_manifest(plan, manifest),
                              encoding="utf-8")
    Path(_json_path_for(manifest)).write_text(
        json.dumps(plan.to_dict(), indent=2, default=list),
        encoding="utf-8")
    return manifest


def listed_paths(manifest_path: str) -> list:
    out = []
    for line in Path(manifest_path).read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t", 2)
        if len(parts) != 3:
            raise PurgeRefused(
                f"{manifest_path}: line {line!r} is not "
                f"<bytes>\\t<category>\\t<path>",
                "a hand-edited manifest line cannot be trusted. Nothing "
                "was removed",
                "re-plan the purge instead of hand-editing the manifest")
        out.append(parts[2])
    return out


# ── Apply ─────────────────────────────────────────────────────────────


def apply_purge(manifest_path: str, db_paths,
                project_folder: str = "") -> dict:
    """Remove what the manifest lists, once every path is re-proved.

    Refuses, removing nothing, when: the plan cannot be read or is for
    another project; retention is no longer `lean`; the manifest lists a
    path the plan did not; a fresh plan no longer names a listed path,
    or names it with another size or mtime; or a render directory
    changed since the plan.
    """
    try:
        planned = PurgePlan.from_dict(json.loads(
            Path(_json_path_for(manifest_path)).read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise PurgeRefused(
            f"the plan beside {manifest_path} cannot be read",
            f"({exc}). Nothing was removed",
            "re-plan the purge and apply the fresh manifest") from exc
    if project_folder and os.path.realpath(project_folder) != \
            os.path.realpath(planned.project_folder):
        raise PurgeRefused(
            f"the plan is for {planned.project_folder}, not "
            f"{project_folder}",
            "a purge plan is bound to the project it was planned for. "
            "Nothing was removed",
            "re-plan the purge for this project and apply that manifest")
    if retention_mode(project_folder=planned.project_folder) != LEAN:
        raise PurgeRefused(
            f"{SETTING} is no longer {LEAN!r}",
            "retention changed since the plan was made. Nothing was "
            "removed",
            f"set {SETTING}={LEAN} again, re-plan, and apply the fresh "
            f"manifest")
    by_path = {c.path: c for c in planned.candidates}
    listed = listed_paths(manifest_path)
    progress = planned.applied or {"removed": [], "deleting": [], "bytes": 0}
    progress.setdefault("deleting", [])
    progress.setdefault("bytes", 0)
    progress.setdefault("removed", [])
    progress.setdefault("quarantined", {})
    completed = set(progress["removed"])
    deleting = set(progress["deleting"])
    staged = set(progress["quarantined"])
    if any(os.path.lexists(path) for path in completed):
        raise PurgeRefused(
            "a completed purge path reappeared",
            "the durable job will not remove a new object at an old path",
            "inspect the path and create a fresh purge plan")
    actionable = [path for path in listed
                  if path not in completed and path not in staged]
    if not actionable:
        _drop_empty_dirs(planned.project_folder)
        record = {"applied_at": datetime.now(UTC).isoformat(),
                  "manifest": manifest_path, "removed": progress["removed"],
                  "removed_count": len(progress["removed"]),
                  "bytes": progress["bytes"],
                  "space_recovery": {"verified": True,
                                     "free_bytes_before": None,
                                     "free_bytes_after": None,
                                     "physical_bytes_freed": 0,
                                     "hardlink_aware_expected_bytes": 0},
                  "quarantined": progress["quarantined"]}
        data = planned.to_dict()
        data["applied"] = record
        Path(_json_path_for(manifest_path)).write_text(
            json.dumps(data, indent=2, default=list), encoding="utf-8")
        _schedule_quarantine_expiry(planned, progress)
        return record
    layout = ProjectLayout(planned.project_folder)
    declared_inputs = _declared_step_file_inputs(planned.project_folder)
    requested_inputs = [
        (path, declared_inputs[_path_key(path, layout)])
        for path in listed
        if _path_key(path, layout) in declared_inputs
    ]
    if requested_inputs:
        path, names = requested_inputs[0]
        raise PurgeRefused(
            f"{path} is a declared build input ({', '.join(names)})",
            "a purge must never remove a file a build step declares as an "
            "input. Nothing was removed",
            "remove that path from the hand-edited manifest and re-plan")
    unplanned = [p for p in actionable if p not in by_path]
    if unplanned:
        raise PurgeRefused(
            f"the manifest lists {len(unplanned)} path(s) the plan never "
            f"named, first {unplanned[0]}",
            "applying removes exactly what the plan proved unreferenced. "
            "Nothing was removed",
            "re-plan the purge and apply the fresh manifest")

    fresh = plan_purge(planned.project_folder, db_paths, mode=LEAN)
    for directory, before in planned.render_fingerprints.items():
        if gc.fingerprint_dir(directory) != before:
            raise PurgeRefused(
                f"{directory} changed since the plan was made",
                "a render directory that moved under the plan invalidates "
                "it. Nothing was removed",
                "re-plan the purge and apply the fresh manifest")
    now = {c.path: c for c in fresh.candidates}
    recovered = []
    for path in actionable:
        was, current = by_path[path], now.get(path)
        if current is None:
            if path in deleting and not os.path.lexists(path):
                recovered.append(path)
                continue
            raise PurgeRefused(
                f"{path} is no longer removable (a current root "
                f"references it, or it is gone)",
                "the re-proof before removal failed. Nothing was removed",
                "re-plan the purge and apply the fresh manifest")
        if (current.size_bytes, current.mtime_ns) != \
                (was.size_bytes, was.mtime_ns):
            raise PurgeRefused(
                f"{path} changed since the plan was made",
                "size or mtime moved under the plan. Nothing was removed",
                "re-plan the purge and apply the fresh manifest")
        if current.identity != was.identity:
            raise PurgeRefused(
                f"{path} was replaced since the plan was made",
                "device/inode identity moved under the plan. Nothing was removed",
                "re-plan the purge and apply the fresh manifest")

    plan_json = Path(_json_path_for(manifest_path))

    def persist_progress() -> None:
        planned.applied = progress
        plan_json.write_text(json.dumps(planned.to_dict(), indent=2,
                                        default=list), encoding="utf-8")

    staged_sources = set(progress["quarantined"])
    for path in actionable:
        candidate = by_path[path]
        if (candidate.deletion_class != RECOVERABLE
                or candidate.category == QUARANTINED
                or path in staged_sources):
            continue
        if candidate.is_dir:
            raise PurgeRefused(
                f"recoverable directory lacks a single recorded file identity: {path}",
                "ambiguous intermediate ownership is protected",
                "inspect and restore or remove the directory explicitly")
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        digest = hashlib.sha256(
            f"{path}\0{candidate.identity}".encode("utf-8")).hexdigest()[:16]
        destination_dir = (layout.write_path(
            Area.QUARANTINE, "recoverable", stamp))
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / f"{digest}-{Path(path).name}"
        if destination.exists():
            raise PurgeRefused(
                f"quarantine destination already exists: {destination}",
                "a staged artifact cannot overwrite another object",
                "inspect the existing quarantine entry and re-plan")
        current_stat = os.lstat(path)
        current_identity = (current_stat.st_dev, current_stat.st_ino,
                            current_stat.st_mode, current_stat.st_size,
                            current_stat.st_mtime_ns)
        if tuple(candidate.identity) != current_identity:
            raise PurgeRefused(
                f"{path} changed before quarantine",
                "the planned filesystem identity no longer matches",
                "re-plan cleanup")
        os.replace(path, destination)
        eligible_after = (datetime.strptime(
            stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC) + timedelta(
                seconds=QUARANTINE_GRACE_SECONDS)).isoformat()
        progress["quarantined"][path] = {
            "path": str(destination), "eligible_after": eligible_after,
            "identity": list(candidate.identity),
            "deletion_class": RECOVERABLE}
        staged_sources.add(path)
        persist_progress()

    for path in recovered:
        progress["removed"].append(path)
        progress["deleting"].remove(path)
        progress["bytes"] = _reclaimable_bytes(
            [by_path[p] for p in progress["removed"]])
        persist_progress()

    free_before_delete = _free_space_snapshot(planned.project_folder)
    removed_this_apply = []
    for path in actionable:
        if path in recovered or path not in now:
            continue
        candidate = by_path[path]
        if candidate.deletion_class == RECOVERABLE \
                and candidate.category != QUARANTINED:
            continue
        try:
            current_stat = os.lstat(path)
        except OSError as exc:
            raise PurgeRefused(
                f"{path} disappeared at delete time ({exc})",
                "the selected filesystem identity no longer exists",
                "re-plan the purge and apply the fresh manifest") from exc
        identity = (current_stat.st_dev, current_stat.st_ino,
                    current_stat.st_mode, current_stat.st_size,
                    current_stat.st_mtime_ns)
        if identity != candidate.identity:
            raise PurgeRefused(
                f"{path} was replaced at delete time",
                "device/inode identity moved under the plan. Nothing further was removed",
                "re-plan the purge and apply the fresh manifest")
        if path not in deleting:
            progress["deleting"].append(path)
            deleting.add(path)
            persist_progress()
        if candidate.is_dir:
            shutil.rmtree(path)
        else:
            os.unlink(path)
        progress["deleting"].remove(path)
        deleting.remove(path)
        progress["removed"].append(path)
        removed_this_apply.append(path)
        progress["bytes"] = _reclaimable_bytes(
            [by_path[p] for p in progress["removed"]])
        completed.add(path)
        persist_progress()
    _drop_empty_dirs(planned.project_folder)
    free_after_delete = _free_space_snapshot(
        planned.project_folder, required=False)
    expected_freed = _physical_reclaimable_bytes(
        [by_path[path] for path in removed_this_apply if path in by_path])
    observed_delta = (free_after_delete - free_before_delete
                      if free_after_delete is not None else None)
    record = {"applied_at": datetime.now(UTC).isoformat(),
              "manifest": manifest_path, "removed": progress["removed"],
              "removed_count": len(progress["removed"]),
              "bytes": progress["bytes"],
              "space_recovery": {
                  "verified": (observed_delta is not None
                               and observed_delta >= expected_freed),
                  "free_bytes_before": free_before_delete,
                  "free_bytes_after": free_after_delete,
                  "free_space_delta_bytes": observed_delta,
                  "physical_bytes_freed": max(0, observed_delta or 0),
                  "hardlink_aware_expected_bytes": expected_freed,
              },
              "quarantined": progress["quarantined"]}
    if free_after_delete is None:
        record["space_recovery"]["measurement_error"] = (
            "free-space read failed after deletion")
    data = planned.to_dict()
    data["applied"] = record
    plan_json.write_text(
        json.dumps(data, indent=2, default=list), encoding="utf-8")
    _schedule_quarantine_expiry(planned, progress)
    return record


def _drop_empty_dirs(project_folder: str) -> None:
    """Directories the purge emptied inside scratch, quarantine and trash."""
    layout = ProjectLayout(project_folder)
    areas = [layout.read_dir(area)
             for area in (Area.SCRATCH, Area.QUARANTINE)]
    trash_root = Path(project_folder) / "pipeline_output" / TRASH_DIRNAME
    if trash_root.is_dir():
        areas.append(trash_root)
    for root in areas:
        if not root.is_dir():
            continue
        for dirpath, _, _ in sorted(os.walk(root), reverse=True):
            here = Path(dirpath)
            if here == root or here == root / PURGE_PLAN_DIR:
                continue
            try:
                here.rmdir()
            except OSError:
                pass


def restore_quarantined(project_folder: str, quarantined_path: str) -> dict:
    """Restore one staged file to its exact recorded original path.

    The purge-plan JSON is the durable move receipt. No filename-derived
    destination is accepted, and an existing destination is never replaced.
    """
    project = Path(project_folder).resolve()
    layout = ProjectLayout(str(project))
    quarantine_root = layout.read_dir(Area.QUARANTINE).resolve()
    source = Path(os.path.abspath(quarantined_path))
    try:
        relative_source = source.relative_to(quarantine_root)
    except ValueError as exc:
        raise PurgeRefused(
            f"{source} is outside this project's quarantine",
            "restore requires a recorded project quarantine object",
            "use the exact quarantined path from its cleanup receipt") from exc
    cursor = quarantine_root
    for part in relative_source.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise PurgeRefused(
                f"quarantine path traverses a symbolic link: {cursor}",
                "symbolic links cannot authorize a restore",
                "inspect the quarantine entry manually")
    if not source.is_file():
        raise PurgeRefused(
            f"{source} is not a regular file",
            "unknown or ambiguous quarantine objects are protected",
            "restore only an owned regular file")
    plan_dir = quarantine_root / PURGE_PLAN_DIR
    matches = []
    for receipt_path in plan_dir.glob("*.json"):
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise PurgeRefused(
                f"cannot read quarantine receipt {receipt_path}: {exc}",
                "an unreadable receipt cannot authorize a restore",
                "repair the receipt or restore manually") from exc
        staged = (receipt.get("applied") or {}).get("quarantined", {})
        matches.extend((receipt_path, original, record)
                       for original, record in staged.items()
                       if os.path.realpath(record.get("path", ""))
                       == str(source))
    if len(matches) != 1:
        raise PurgeRefused(
            f"{source} has {len(matches)} exact quarantine receipts",
            "restore requires one unambiguous recorded owner",
            "inspect the cleanup manifests before restoring")
    receipt_path, original, record = matches[0]
    destination = Path(original)
    layout.assert_writable(destination)
    expected = tuple(record.get("identity", ()))
    current = os.lstat(source)
    actual = (current.st_dev, current.st_ino, current.st_mode,
              current.st_size, current.st_mtime_ns)
    if actual != expected:
        raise PurgeRefused(
            f"{source} changed since it was quarantined",
            "the recorded file identity no longer matches",
            "leave it in quarantine and inspect it manually")
    if os.path.lexists(destination):
        raise PurgeRefused(
            f"restore destination already exists: {destination}",
            "restore never overwrites a project file",
            "choose a separate destination or move the existing file")
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, destination)
    applied = receipt.get("applied") or {}
    applied.setdefault("restored", {})[str(source)] = {
        "path": str(destination), "restored_at": datetime.now(UTC).isoformat()}
    receipt["applied"] = applied
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n",
                           encoding="utf-8")
    return {"restored": str(destination), "from": str(source),
            "receipt": str(receipt_path)}


# ── Inside Ren: after a sign-off ──────────────────────────────────────


@under_lease("read Resolve database path for retention", exclusive=False)
def database_paths(project_folder: str) -> list:
    """This project's `Project.db`, off the running Resolve. READ-ONLY.

    The root is Resolve's own answer; the folder and project name are the
    project's declarations (`resolve.folder`, `resolve.project_name`).
    Raises `PurgeRefused` when either is missing or the file is absent.
    """
    import yaml

    from library.tools.execution import prune_orphans
    from library.tools.marker_feedback import connect_resolve

    config_path = os.path.join(project_folder, "project.yaml")
    with open(config_path, encoding="utf-8") as handle:
        declared = (yaml.safe_load(handle) or {}).get("resolve") or {}
    name = str(declared.get("project_name") or "")
    if not name:
        raise PurgeRefused(
            f"{config_path} declares no resolve.project_name",
            "there is no timeline database to read. Nothing was planned",
            f"set resolve.project_name in {config_path}, then plan the "
            f"purge again")
    database = (connect_resolve().GetProjectManager().GetCurrentDatabase()
                or {})
    if (database.get("DbType") or "") != "Disk":
        raise PurgeRefused(
            f"the current Resolve database is {database!r}",
            "it is not a Disk database with a Project.db to read. "
            "Nothing was planned",
            "point Resolve at a Disk database, or pass --db <Project.db> "
            "explicitly")
    root = database.get("DbPath") or prune_orphans._disk_database_root(
        database.get("DbName") or "")
    folder = str(declared.get("folder") or "")
    path = os.path.join(root, "Resolve Projects", "Users", "guest",
                        "Projects", *[p for p in folder.split("/") if p],
                        name, prune_orphans.PROJECT_DB_NAME)
    if not os.path.isfile(path):
        raise PurgeRefused(
            f"no project database at {path}",
            "without the timelines, whether a live timeline references "
            "a file is unprovable. Nothing was planned",
            "pass --db <Project.db> explicitly, or fix resolve.folder in "
            "project.yaml")
    return [path]


_cleanup_worker_lock = threading.Lock()


def _process_cleanup_queue(project_folder: str, db_paths=None) -> None:
    """Drain durable sign-off jobs once project activity and leases clear."""
    from library.tools import artifact_lifecycle, run_control
    from library.tools.resource_leases import (
        LeaseDeclaration, ResourceRef, ResourceLeaseBusy, resource_lease)

    while True:
        artifact_lifecycle.recover_interrupted_cleanup_jobs(project_folder)
        jobs = artifact_lifecycle.pending_cleanup_jobs(project_folder)
        if not jobs:
            return
        if run_control.is_running(project_folder):
            time.sleep(1)
            continue
        project_ref = ResourceRef("project", str(Path(project_folder).resolve()))
        declaration = LeaseDeclaration(exclusive=frozenset({project_ref}))
        try:
            lease = resource_lease(
                declaration, purpose="signoff-retention-cleanup",
                project=project_folder, wait=False)
            with lease:
                if run_control.is_running(project_folder):
                    time.sleep(1)
                    continue
                artifact_lifecycle.recover_interrupted_cleanup_jobs(
                    project_folder)
                jobs = artifact_lifecycle.pending_cleanup_jobs(project_folder)
                for job in jobs:
                    artifact_lifecycle.transition_cleanup_job(
                        project_folder, job, "running")
                    try:
                        paths = db_paths or database_paths(project_folder)
                        plan = plan_purge(project_folder, paths, mode=LEAN)
                        manifest = write_plan(plan)
                        result = apply_purge(
                            manifest, paths, project_folder=project_folder)
                        artifact_lifecycle.transition_cleanup_job(
                            project_folder, job, "complete",
                            outcome=(f"removed {result['removed_count']} paths; "
                                     f"reclaimed {result['bytes']} bytes; "
                                     f"manifest {manifest}"))
                    except Exception as refused:  # noqa: BLE001 - sign-off stands
                        artifact_lifecycle.transition_cleanup_job(
                            project_folder, job, "refused", outcome=str(refused))
        except ResourceLeaseBusy:
            time.sleep(1)


def resume_cleanup_jobs(project_folder: str, db_paths=None) -> None:
    """Resume queued work in the background; the job itself is durable."""
    from library.tools import artifact_lifecycle
    # The project activity lease is entered for ordinary runs too. Do not
    # create a lifecycle log or output tree when there is no work to resume.
    if not artifact_lifecycle.incomplete_cleanup_jobs(project_folder):
        return
    if not _cleanup_worker_lock.acquire(blocking=False):
        return

    def worker():
        try:
            _process_cleanup_queue(project_folder, db_paths)
        finally:
            _cleanup_worker_lock.release()

    threading.Thread(target=worker, name="ren-retention-cleanup",
                     daemon=True).start()


def after_signoff(project_folder: str, db_paths=None, *,
                  lifecycle_event_id: str) -> str:
    """What a sign-off does about retention. Returns the lines to print.

    `keep` does nothing. `lean` writes a deduplicated durable job to the
    project event log, then a worker waits for project activity and leases
    before planning and applying. A refusal is recorded on the job; the
    sign-off already landed and is never rolled back.
    """
    try:
        mode = retention_mode(project_folder=project_folder)
        if mode == KEEP:
            return f"Retention is {KEEP!r}: nothing is purged."
        from library.tools import artifact_lifecycle
        project_id = Path(project_folder).resolve().name
        job_id = f"signoff-retention-{lifecycle_event_id}"
        artifact_lifecycle.schedule_cleanup_job(
            project_folder, event_id=lifecycle_event_id,
            project_id=project_id, run_id=lifecycle_event_id,
            job_id=job_id, reason="successful reel sign-off")
        resume_cleanup_jobs(project_folder, db_paths)
        return f"Lean retention cleanup queued as durable job {job_id}."
    except (PurgeRefused, RetentionSettingInvalid, OSError, ValueError) as refused:
        return f"Purge job not queued: {refused}. Sign-off remains recorded."
    except Exception as unavailable:  # noqa: BLE001 - sign-off already landed
        return f"Purge job not queued: {unavailable}. Sign-off remains recorded."
