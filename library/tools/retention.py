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
the captain's own default. Anything else REFUSES: a misspelt `keep`
read as `lean` would delete what its owner asked to keep.

- `keep`: nothing is purged, ever. The plan says so and lists nothing.
- `lean`: after a sign-off, four classes go (`CATEGORIES`).

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

A purge also refuses while a pipeline run holds the project
(`run_control.is_running`).

Deletion, not quarantine: `lean` is the owner's instruction that these
bytes go. Every render class is regenerable by the command
`build_sweep.REGENERATE` names, and the manifest is kept.

`tests/unit/context/test_project_layout.py`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
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
CATEGORIES = (SUPERSEDED_RENDER, QUARANTINED, SCRATCH, STALE_JOURNAL)
"""Everything `lean` removes. COMPLETE: a class not here is never planned."""

PURGE_PLAN_DIR = "purge_plans"
"""Under `Area.QUARANTINE`, and exempt from the quarantine class: the
manifest is the record of what went, so a purge must not take it."""

PURGE_PREFIX = "purge"

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
is left alone. `touchup_*` is absent on purpose - a touch-up's record is
the undo lane's to retire, not this module's."""

KEEP_NEWEST_JOURNALS = 1
"""The newest act of each family is the one an undo would read."""

_STAMPED = re.compile(
    r"^(?P<prefix>.+?)_(?P<stamp>\d{8}T\d{6}Z)(?:_(?P<n>\d+))?"
    r"(?P<tail>_manifest\.md|\.json|\.txt)$")


class RetentionSettingInvalid(RenRefusal):
    """`REN_RETENTION` names neither mode."""


class PurgeRefused(RenRefusal):
    """The purge declined, and removed NOTHING."""


def retention_mode(environ=None) -> str:
    """The user's retention mode: `lean` (default) or `keep`, or raise."""
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

    @property
    def reclaimable_bytes(self) -> int:
        """Bytes that really leave the disk.

        A hard-linked file (`render_cache.adopt`) is counted once, and
        only when EVERY link to it is in the set: removing one name of
        a file another name still holds frees nothing.
        """
        by_inode: dict = {}
        total = 0
        for c in self.candidates:
            if not c.inode:
                total += c.size_bytes
                continue
            entry = by_inode.setdefault(tuple(c.inode[:2]),
                                        [c.size_bytes, c.inode[2], 0])
            entry[2] += 1
        for size, links, listed in by_inode.values():
            if listed >= links:
                total += size
        return total

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
            protected_timelines=data.get("protected_timelines", []))
        plan.candidates = [Candidate(**{**c, "inode": tuple(c["inode"])})
                           for c in data["candidates"]]
        return plan


def _stat_candidate(path: str, category: str, reason: str) -> Candidate:
    st = os.lstat(path)
    is_dir = os.path.isdir(path) and not os.path.islink(path)
    size = gc._tree_size(path) if is_dir else st.st_size
    inode = () if is_dir else (st.st_dev, st.st_ino, st.st_nlink)
    return Candidate(path=path, size_bytes=size, category=category,
                     reason=reason, is_dir=is_dir,
                     mtime_ns=st.st_mtime_ns, inode=inode)


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


def plan_purge(project_folder: str, db_paths, mode: str | None = None
               ) -> PurgePlan:
    """The removal set, computed and nothing more. Writes NOTHING."""
    from library.tools import run_control

    project_folder = str(project_folder)
    mode = mode or retention_mode()
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
    for area in build_sweep.SWEPT_AREAS:
        asset_dir = str(layout.read_dir(area))
        if not os.path.isdir(asset_dir):
            continue
        plan.render_fingerprints[asset_dir] = gc.fingerprint_dir(asset_dir)
        for asset in gc.mark(project_folder, asset_dir, roots).orphans:
            found.append((asset.path, SUPERSEDED_RENDER, asset.reason))

    quarantine = layout.read_dir(Area.QUARANTINE)
    for path in _files_under(quarantine, exclude=quarantine / PURGE_PLAN_DIR):
        found.append((path, QUARANTINED,
                      "moved aside by a build sweep; never placed since"))
    for path in _files_under(layout.read_dir(Area.SCRATCH)):
        found.append((path, SCRATCH, ("working file with no reader after "
                                     "the step that wrote it")))
    for path, reason in _journal_candidates(layout):
        found.append((path, STALE_JOURNAL, reason))

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
        plan.candidates.append(_stat_candidate(path, category, reason))
    return plan


# ── The manifest ──────────────────────────────────────────────────────


def _json_path_for(manifest_path: str) -> str:
    return os.path.splitext(manifest_path)[0] + ".json"


def render_manifest(plan: PurgePlan, manifest_path: str) -> str:
    gib = plan.reclaimable_bytes / (1024 ** 3)
    lines = [
        (f"# Ren purge plan ({plan.mode} retention) - NOTHING HAS BEEN "
         f"REMOVED."),
        f"# project: {plan.project_folder}",
        f"# planned: {plan.created_at}",
        f"# {len(plan.candidates)} path(s), {gib:.2f} GiB reclaimable.",
        (f"# Kept though a class would take them: {len(plan.kept)} "
         f"(a current root references them)."),
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
    if retention_mode() != LEAN:
        raise PurgeRefused(
            f"{SETTING} is no longer {LEAN!r}",
            "retention changed since the plan was made. Nothing was "
            "removed",
            f"set {SETTING}={LEAN} again, re-plan, and apply the fresh "
            f"manifest")
    by_path = {c.path: c for c in planned.candidates}
    listed = listed_paths(manifest_path)
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
    unplanned = [p for p in listed if p not in by_path]
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
    for path in listed:
        was, current = by_path[path], now.get(path)
        if current is None:
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

    removed = []
    for path in listed:
        if by_path[path].is_dir:
            shutil.rmtree(path)
        else:
            os.unlink(path)
        removed.append(path)
    _drop_empty_dirs(planned.project_folder)
    record = {"applied_at": datetime.now(UTC).isoformat(),
              "manifest": manifest_path, "removed": removed,
              "removed_count": len(removed),
              "bytes": sum(by_path[p].size_bytes for p in removed)}
    data = planned.to_dict()
    data["applied"] = record
    Path(_json_path_for(manifest_path)).write_text(
        json.dumps(data, indent=2, default=list), encoding="utf-8")
    return record


def _drop_empty_dirs(project_folder: str) -> None:
    """Directories the purge emptied inside scratch and quarantine."""
    layout = ProjectLayout(project_folder)
    for area in (Area.SCRATCH, Area.QUARANTINE):
        root = layout.read_dir(area)
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


def after_signoff(project_folder: str, db_paths=None) -> str:
    """What a sign-off does about retention. Returns the lines to print.

    `keep` does nothing. `lean` PLANS - writes the manifest, removes
    nothing - and says how to apply it, so the removal is always a
    second, named act on a list someone could read. A plan that cannot
    be made is reported, never raised: the sign-off already landed.
    """
    try:
        mode = retention_mode()
        if mode == KEEP:
            return f"Retention is {KEEP!r}: nothing is purged."
        if db_paths is None:
            db_paths = database_paths(project_folder)
        plan = plan_purge(project_folder, db_paths, mode=mode)
    except (PurgeRefused, RetentionSettingInvalid, OSError) as refused:
        return f"Purge not planned: {refused}"
    except Exception as unavailable:  # noqa: BLE001 - Resolve unreachable
        return (f"Purge not planned: {unavailable}. Plan it with "
                f"`ren purge {project_folder}` once Resolve is open.")
    manifest = write_plan(plan)
    gib = plan.reclaimable_bytes / (1024 ** 3)
    return (f"Lean retention: {len(plan.candidates)} path(s), {gib:.2f} GiB, "
            f"listed in {manifest}. Nothing removed yet - apply with "
            f"`ren purge {project_folder} --apply {manifest}`.")
