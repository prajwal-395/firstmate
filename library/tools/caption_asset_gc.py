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

Where things live is `library/tools/project_layout.py`'s decision: the
assets are `Area.SUBTITLE_SEGMENTS`, and quarantine plus the mark/sweep
records are `Area.QUARANTINE`. This module names areas, never paths.

Sibling lifetimes: a `.json`/`.txt` sibling has none of its own. It
lives when its `.mov` lives and goes when its `.mov` goes, because
nothing references a props file - timelines place the mov. A sibling
whose mov is absent (a render that failed after writing props) is an
orphan on its own. `_tight_box.json` belongs to its `_tight.mov`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
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

# Step records live beside the assets but are never assets themselves.
STEP_RECORD_NAMES = ("output.json", "summary.md")

_DIGEST_RE = re.compile(r"_([0-9a-f]{8})$")

_SIBLING_SUFFIXES = ("_props.json", "_reuse_key.txt", "_tight_box.json")


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


def collect_pipeline_roots(project_folder: str) -> list[RootResult]:
    """The pipeline's own records as roots: step output and manifest.

    A project whose caption step never ran has no record to read, and
    that is `no-record` - an empty root, not an unreadable one.
    Refusing the sweep on a missing record would refuse it forever on
    exactly the projects whose garbage predates the records. A record
    that EXISTS but cannot be read is `unreadable` and refuses.
    """
    roots = []
    layout = ProjectLayout(project_folder)
    candidates = [
        ("pipeline:render_subtitles",
         [layout.pipeline_data_path,
          layout.step_dir(STEP_NODE_ID) / "output.json"]),
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
        except Exception as exc:  # noqa: BLE001 - any failure refuses
            roots.append(RootResult(
                name=name, status=UNREADABLE, kind="resolve",
                db_path=str(db_path), detail=f"cannot be read: {exc}"))
            continue
        roots.append(RootResult(
            name=name, status=OK, paths=set(placed), kind="resolve",
            db_path=str(db_path),
            detail=f"{len(placed)} placed path(s)"))
    return roots


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
                            db_path=r.get("db_path", ""))
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
            ("compile_manifest/assembly_manifest.json",
             str(layout.step_dir("compile_manifest")
                 / "assembly_manifest.json"))):
        versions[label] = _version_of(path)
    return versions


def _superseded_map(project_folder: str) -> dict[str, str]:
    """Old generation -> newer generation, as the caption step recorded.

    Step 4.05 names the generation each render superseded on the new
    entry (`render_one_segment`), so the mark can say not just that a
    file is orphaned but what replaced it. Read-only; absent/unreadable
    records contribute nothing rather than refusing, because this is
    attribution, not reachability.
    """
    mapping: dict[str, str] = {}
    try:
        layout = ProjectLayout(project_folder)
    except Exception:  # noqa: BLE001 - attribution only, never refuses
        return mapping
    path = layout.step_dir(STEP_NODE_ID) / "output.json"
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return mapping
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
          fresh_roots: list[RootResult] | None = None) -> dict:
    """Move a marked orphan set to quarantine.

    Refuses (moving NOTHING) when the mark file cannot be read, when
    the mark recorded any root as unreadable, when the asset directory
    changed since the mark (stale), when any root is unreadable NOW, or
    when any candidate became referenced since the mark. A root that
    could not be read is a reason to refuse, never a reason to treat
    everything under it as unreachable.

    The full manifest is written BEFORE anything moves, and every
    moved path - small records included - is in it.
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
    manifest_path = str(dest_base.parent / f"sweep_{stamp}_manifest.md")
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
        out.extend(collect_pipeline_roots(project_folder))
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
    args = parser.parse_args(argv)
    if args.command == "mark":
        layout = ProjectLayout(args.project)
        asset_dir = str(layout.read_dir(Area.SUBTITLE_SEGMENTS))
        roots = collect_resolve_roots(args.db) \
            + collect_pipeline_roots(args.project)
        result = mark(args.project, asset_dir, roots)
        written = write_mark_report(result, args.out)
        print(render_mark_report(result))
        print(f"mark written to {written['json']}")
        print(f"report written to {written['markdown']}")
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