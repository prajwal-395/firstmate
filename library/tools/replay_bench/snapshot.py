"""A frozen snapshot of one project's state, and how staleness is noticed.

## The snapshot decision

A snapshot is **captured into a store outside the repository; only its
MANIFEST is committed.**

Committing the payload would make results reproducible by anyone and would
go stale silently: 001's `pipeline_data.json` alone is 4.2 MB of one
client's transcripts and vision documents, and the moment a step changes
what it emits the fixture describes a pipeline that no longer exists.
Capturing on demand is always current and makes two people's results
incomparable - and, right now, torn: a run writes 001 while this is read.

So the payload is captured and the manifest is committed.  The manifest is
a few kilobytes: every copied file with its sha256, every referenced area
with a digest of its listing, the source project, the capture time, the
repository HEAD at capture, and what the runner said it was doing.  Two
people can then establish that they hold the same bytes without either of
them shipping 7 MB of somebody's footage analysis, and a snapshot that has
drifted says so instead of quietly answering from stale state.

## What is copied and what is referenced

`pipeline_data.json` and `project.yaml` are COPIED: they move under a
running pipeline and they are what "frozen state" means.

`raw/`, `music/`, `assets/` and `pipeline_output/` are REFERENCED by
symlink.  001's music library is 3 GB and its output tree is 2 GB; copying
either per snapshot is not a snapshot, it is a backup.  They are also the
areas a pre-bridge reads and never writes - `raw/`, `music/` and `assets/`
are `Kind.INPUT`, which `project_layout` structurally refuses to write to.
Each reference carries a listing digest (name, size, mtime), so a
reference that has moved is reported rather than silently used.

## A replay never runs against the snapshot directory itself

**A replay runs in a throwaway CLONE of the snapshot project, and nothing in
it resolves into the source project.**  The references above are symlinks,
and a pre-bridge that writes - 3.02's footage analysis and window frames,
3.04's repeated-take diagnostics, 4.03's and 5.01's stills, 4.04's SFX
catalogue - wrote straight through them into the captain's live
`pipeline_output/` (measured 2026-10-01: 001 gained 395 files in those
areas after its last run).  `isolated_project` clones every area
copy-on-write (APFS `clonefile`, or a reflink elsewhere), so a 28 GB
project costs seconds and no disk, and
re-roots every absolute source-project path in the state at the clone, so a
bridge that writes to a path it READ from state lands in the clone too.  A
filesystem that cannot clone falls back to a real copy only below
`COPY_FALLBACK_LIMIT_BYTES`, and refuses above it: a slow replay is
recoverable, a write into the live project is not.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

# Files whose bytes ARE the snapshot.  These move under a running
# pipeline, so they are copied.
COPIED_FILES = ("pipeline_data.json", "project.yaml")

# Areas a reconstruction reads and no bridge writes.  Referenced, not
# copied, with a listing digest so drift is noticed.
REFERENCED_AREAS = ("raw", "music", "assets", "brand_assets", "compositions",
                    "pipeline_output")

# The archive of what the runner really wrote, when there is one.  This is
# what the byte-for-byte gate compares against.
ARCHIVE_DIRS = ("pipeline_output/llm_requests", "pipeline_output/llm_responses",
                "pipeline_output/reasoning")

DEFAULT_STORE = Path(
    os.environ.get("PIPELINE_REPLAY_SNAPSHOTS",
                   Path.home() / ".video_editing_pilot" / "replay_snapshots")
)

MANIFEST_NAME = "snapshot.json"

# Where a replay's throwaway clone lives: beside the snapshots, so on the
# store's volume, and never inside the source project.
WORKSPACES_DIR = "_workspaces"

# A filesystem that cannot clone gets a real copy only up to this size.
# Mechanical, not taste: above it a per-replay copy is a backup, not a
# replay, and the bench refuses rather than writing into the live project.
COPY_FALLBACK_LIMIT_BYTES = 2 * 1024 ** 3


class IsolationError(RuntimeError):
    """A replay could not be given a project that is not the live one."""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _listing_digest(root: Path) -> tuple:
    """Digest a referenced tree without reading its bytes.

    Name, size and mtime of every entry.  This is a DRIFT DETECTOR, not an
    integrity check: it answers "has the world this snapshot points at
    moved", which is the question a bench that compares futures against a
    frozen past has to be able to ask.
    """
    if not root.exists():
        return ("", 0)
    h = hashlib.sha256()
    count = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in sorted(filenames):
            p = Path(dirpath) / name
            try:
                st = p.lstat()
            except OSError:
                continue
            rel = str(p.relative_to(root))
            h.update(f"{rel}\0{st.st_size}\0{int(st.st_mtime)}\0".encode())
            count += 1
    return (h.hexdigest(), count)


@dataclass
class Snapshot:
    """A captured snapshot, addressed by its directory."""

    root: Path
    manifest: dict

    @property
    def snapshot_id(self) -> str:
        return self.manifest["snapshot_id"]

    @property
    def project_dir(self) -> Path:
        """The frozen project folder a reconstruction runs against."""
        return self.root / "project"

    @property
    def declared_project_folder(self) -> str:
        """The path the ORIGINAL run recorded.

        The context string carries `project_folder` verbatim, so a
        reconstruction that ran against the snapshot directory would differ
        from the archive by that one string.  The reconstructor substitutes
        this back and reports how many substitutions it made.
        """
        return self.manifest["declared_project_folder"]

    @property
    def state(self) -> dict:
        with open(self.project_dir / "pipeline_data.json", encoding="utf-8") as fh:
            return json.load(fh)

    @property
    def archive_dir(self) -> Path:
        return self.root / "archive" / "llm_requests"

    @property
    def responses_dir(self) -> Path:
        return self.root / "archive" / "llm_responses"

    def archived_steps(self) -> list:
        if not self.archive_dir.is_dir():
            return []
        return sorted(p.stem for p in self.archive_dir.glob("*.json"))

    def verify(self) -> dict:
        """Two questions, answered separately.

        `sealed` - do the copied bytes still hash to what was recorded?  A
        snapshot that has been edited is not a snapshot.

        `references_drifted` - has anything the snapshot points at rather
        than copies moved since capture?  A bench that silently compares
        against stale state is worse than none, so this is reported on
        every reconstruction, not only on demand.
        """
        broken = []
        for entry in self.manifest["files"]:
            p = self.root / entry["path"]
            if not p.exists():
                broken.append(f"{entry['path']}: missing")
            elif _sha256(p) != entry["sha256"]:
                broken.append(f"{entry['path']}: sha256 changed since capture")

        drifted = []
        now = {}
        for entry in self.manifest["references"]:
            target = Path(entry["target"])
            if not target.exists():
                drifted.append(f"{entry['area']}: target {target} no longer exists")
                now[entry["area"]] = ""
                continue
            digest, count = _listing_digest(target)
            now[entry["area"]] = digest
            if digest != entry["listing_sha256"]:
                drifted.append(
                    f"{entry['area']}: {entry['entry_count']} entries at capture, "
                    f"{count} now, listing digest differs"
                )
        return {
            "snapshot_id": self.snapshot_id,
            "sealed": not broken,
            "seal_breaks": broken,
            "references_drifted": drifted,
            # The CURRENT listing digest of every referenced area.  Drift
            # against capture is one question - "has the world moved since
            # this snapshot" - and on a project two other workers are
            # running, the answer is usually yes and usually harmless.
            # Comparing this dict before and against after a reconstruction
            # is a different and sharper question: did THIS run write
            # anything.  Keep them apart.
            "reference_digests": now,
        }


def load(snapshot_ref: str, store: Path | None = None) -> Snapshot:
    """Load by id (in the store) or by an explicit directory path."""
    p = Path(snapshot_ref).expanduser()
    if (p / MANIFEST_NAME).is_file():
        root = p
    else:
        root = (store or DEFAULT_STORE) / snapshot_ref
        if not (root / MANIFEST_NAME).is_file():
            raise FileNotFoundError(
                f"No snapshot at {p} and none named {snapshot_ref!r} in "
                f"{store or DEFAULT_STORE}. Capture one with: "
                f"python3 -m library.tools.replay_bench capture <project>"
            )
    with open(root / MANIFEST_NAME, encoding="utf-8") as fh:
        return Snapshot(root=root, manifest=json.load(fh))


def list_snapshots(store: Path | None = None) -> list:
    store = store or DEFAULT_STORE
    if not store.is_dir():
        return []
    out = []
    for d in sorted(store.iterdir()):
        if (d / MANIFEST_NAME).is_file():
            with open(d / MANIFEST_NAME, encoding="utf-8") as fh:
                out.append(json.load(fh))
    return out


def _git_head(repo_root: Path) -> str:
    import subprocess
    r = subprocess.run(["git", "-C", str(repo_root), "rev-parse", "HEAD"],
                       capture_output=True, text=True, encoding="utf-8",
                       check=False)
    # A snapshot of a project is worth having whether or not the engine it
    # was captured beside is under git.  Record the head when there is one.
    return r.stdout.strip() if r.returncode == 0 else ""


def _declared_brief_name(project: Path):
    """What this project calls its creative brief, or None.

    `brief_attachment.read_declaration` is the one reader of that
    declaration and is used here rather than a second parse of
    `project.yaml`, so a project that moves the key does not need this
    module changed too. A relative path is what the runner joins to the
    project folder, and an absolute one is already readable where it is.
    """
    try:
        from library.tools.brief_attachment import (
            BriefAttachmentError,
            read_declaration,
        )
    except ImportError:
        return None
    try:
        attachment = read_declaration(str(project))
    except (BriefAttachmentError, OSError, ValueError):
        return None
    path = getattr(attachment, "path", None)
    if not path or os.path.isabs(str(path)):
        return None
    return str(path)


def capture(project_folder: str, snapshot_id: str | None = None, store: Path | None = None,
            repo_root: Path | None = None, force: bool = False,
            state_path: str | None = None, archive_dir: str | None = None,
            note: str | None = None) -> Snapshot:
    """Freeze one project's state.  Reads the project; writes only the store.

    The runner's own account of itself (`pipeline_run.json`) is recorded
    alongside, because a snapshot taken mid-run is TORN - `pipeline_data.json`
    is rewritten after every step - and a bench that cannot say so would
    hand a torn snapshot to a comparison and call the result a measurement.

    `state_path` and `archive_dir` freeze bytes that are no longer at the
    project's own addresses.  Both are needed here more often than they
    look: `pipeline_output/llm_requests/` is LAST-WRITE-WINS PER STEP with
    nothing marking a run boundary, so a later run silently replaces the
    archive a state file belongs with, and `pipeline_data.json` is rewritten
    after every step.  Every copied file records the absolute path it came
    from, so a snapshot assembled this way says where each byte was found
    rather than implying it was all read from one place at one moment.
    """
    project = Path(project_folder).expanduser().resolve()
    if not (project / "pipeline_data.json").is_file():
        raise FileNotFoundError(f"{project} has no pipeline_data.json")

    store = store or DEFAULT_STORE
    captured_at = datetime.now(UTC)
    snapshot_id = snapshot_id or (
        f"{project.name}-{captured_at.strftime('%Y%m%dT%H%M%SZ')}")
    root = store / snapshot_id
    if root.exists():
        if not force:
            raise FileExistsError(
                f"Snapshot {snapshot_id} already exists at {root}. A snapshot "
                f"is immutable by design; pass force=True to replace it.")
        shutil.rmtree(root)

    run_status = None
    run_file = project / "pipeline_run.json"
    if run_file.is_file():
        try:
            with open(run_file, encoding="utf-8") as fh:
                run_status = json.load(fh)
        except (OSError, json.JSONDecodeError):
            run_status = {"status": "unreadable"}

    proj_dir = root / "project"
    proj_dir.mkdir(parents=True)

    overrides = {"pipeline_data.json": state_path}
    files = []
    for name in COPIED_FILES:
        src = Path(overrides[name]).expanduser().resolve() \
            if overrides.get(name) else project / name
        if not src.is_file():
            continue
        dst = proj_dir / name
        shutil.copy2(src, dst)
        files.append({"path": f"project/{name}", "sha256": _sha256(dst),
                      "bytes": dst.stat().st_size,
                      "source": str(src)})

    # The creative brief, which `project.yaml` NAMES rather than fixes.
    #
    # Ten steps declare `creative_brief` and the runner RAISES when a
    # declared brief cannot be read - deliberately, because a step that
    # reported success having read a filename was the defect that rule
    # replaced. So a snapshot without the brief cannot reconstruct any of
    # those ten, which is every step that makes a creative judgement.
    # Found 2026-09-05 when step 3.4 regained its declaration and the
    # bench stopped being able to replay it at all.
    #
    # Copied rather than referenced: it is small text the captain edits,
    # and the whole point of a snapshot is that a later edit does not
    # silently change what a replay reconstructs. Its NAME comes from the
    # project's own declaration - top level or under `pipeline:` - so a
    # project that calls it something else is still frozen.
    brief_name = _declared_brief_name(project)
    if brief_name:
        src = project / brief_name
        if src.is_file():
            dst = proj_dir / brief_name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            files.append({"path": f"project/{brief_name}",
                          "sha256": _sha256(dst),
                          "bytes": dst.stat().st_size,
                          "source": str(src)})

    references = []
    for area in REFERENCED_AREAS:
        src = project / area
        if not src.exists():
            continue
        digest, count = _listing_digest(src)
        os.symlink(src, proj_dir / area)
        references.append({"area": area, "target": str(src),
                           "listing_sha256": digest, "entry_count": count})

    archive_root = root / "archive"
    for rel in ARCHIVE_DIRS:
        src = (Path(archive_dir).expanduser().resolve() / Path(rel).name
               if archive_dir else project / rel)
        if not src.is_dir():
            continue
        dst = archive_root / Path(rel).name
        dst.mkdir(parents=True, exist_ok=True)
        for f in sorted(p for p in src.iterdir() if p.is_file()):
            shutil.copy2(f, dst / f.name)
            files.append({"path": f"archive/{Path(rel).name}/{f.name}",
                          "sha256": _sha256(dst / f.name),
                          "bytes": (dst / f.name).stat().st_size,
                          "source": str(f)})

    manifest = {
        "snapshot_id": snapshot_id,
        "captured_at": captured_at.isoformat(),
        "source_project": str(project),
        "declared_project_folder": json.loads(
            (proj_dir / "pipeline_data.json").read_text(encoding="utf-8")
        ).get("project_folder", str(project)),
        "capture_repo_head": _git_head(repo_root) if repo_root else "",
        "pipeline_run_at_capture": {
            "status": (run_status or {}).get("status"),
            "current_step": (run_status or {}).get("current_step"),
            "started_at": (run_status or {}).get("started_at"),
        } if run_status else None,
        "torn": bool(run_status and run_status.get("status") == "running"),
        "note": note,
        "state_from": str(Path(state_path).resolve()) if state_path else None,
        "archive_from": str(Path(archive_dir).resolve()) if archive_dir else None,
        "files": files,
        "references": references,
    }
    with open(root / MANIFEST_NAME, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
    return Snapshot(root=root, manifest=manifest)


def _tree_bytes(root: Path) -> int:
    if root.is_file():
        return root.stat().st_size
    total = 0
    for dirpath, _dirs, files in os.walk(root, followlinks=False):
        for name in files:
            try:
                total += (Path(dirpath) / name).lstat().st_size
            except OSError:
                continue
    return total


def _clone(src: Path, dst: Path) -> str:
    """Copy `src` to `dst` copy-on-write; a real copy only when small.

    Returns how it was copied.  Symlinks inside `src` are copied as links,
    and `isolated_project` re-points any that lead back into the source.
    """
    flag = "-c" if sys.platform == "darwin" else "--reflink=always"
    proc = subprocess.run(["cp", flag, "-R", str(src), str(dst)],
                          capture_output=True, text=True, encoding="utf-8",
                          check=False)
    if proc.returncode == 0:
        return "clone"
    if dst.exists() or dst.is_symlink():
        if dst.is_dir() and not dst.is_symlink():
            shutil.rmtree(dst)
        else:
            dst.unlink()
    size = _tree_bytes(src)
    if size > COPY_FALLBACK_LIMIT_BYTES:
        raise IsolationError(
            f"cannot clone {src} copy-on-write ({proc.stderr.strip()}) and it "
            f"is {size:,} B, over the {COPY_FALLBACK_LIMIT_BYTES:,} B a replay "
            f"may copy. Put the snapshot store on the project's volume "
            f"(PIPELINE_REPLAY_SNAPSHOTS or --store); the bench will not run "
            f"a replay against the live project instead.")
    if src.is_dir():
        shutil.copytree(src, dst, symlinks=True)
    else:
        shutil.copy2(src, dst, follow_symlinks=False)
    return "copy"


def _inside(path: str, roots) -> str | None:
    for root in roots:
        if path == root or path.startswith(root.rstrip(os.sep) + os.sep):
            return root
    return None


def _reroot(obj, roots, new_root: str, counter: list):
    """Rewrite every string rooted at a source path to the same place in the clone."""
    if isinstance(obj, str):
        root = _inside(obj, roots)
        if root is None:
            return obj
        counter[0] += 1
        return new_root + obj[len(root.rstrip(os.sep)):]
    if isinstance(obj, list):
        return [_reroot(v, roots, new_root, counter) for v in obj]
    if isinstance(obj, dict):
        return {k: _reroot(v, roots, new_root, counter) for k, v in obj.items()}
    return obj


def source_roots(snap: Snapshot) -> list:
    """The live project folders the snapshot was taken from, longest first.

    The source and the folder its run recorded, as written and as resolved.
    Every referenced area is `<source>/<area>`, so it is inside these.
    """
    roots = {snap.manifest["source_project"], snap.declared_project_folder}
    roots |= {os.path.realpath(r) for r in roots if r}
    return sorted((r for r in roots if r), key=len, reverse=True)


def escaping_paths(workspace: Path, roots) -> list:
    """Every path under `workspace` that resolves inside one of `roots`.

    Only the workspace itself and the symlinks under it are resolved: with
    the walk not following links, anything else is a real entry of a
    folder that has already been checked.
    """
    out = []
    if _inside(os.path.realpath(workspace), roots):
        out.append(str(workspace))
    for dirpath, dirnames, filenames in os.walk(workspace, followlinks=False):
        for name in dirnames + filenames:
            p = Path(dirpath) / name
            if p.is_symlink() and _inside(os.path.realpath(p), roots):
                out.append(str(p))
    return out


@contextmanager
def isolated_project(snap: Snapshot, parent: Path | None = None):
    """Yield a throwaway project folder a replay may write to freely.

    Everything in the snapshot's project folder is cloned, referenced areas
    from their live targets, so reads see what a symlink would have shown
    and writes land here.  The state's absolute source-project paths are
    re-rooted at the clone.  Before yielding, the clone is walked and a
    path that still resolves into the source REFUSES the replay.  Removed
    on exit.
    """
    parent = Path(parent or (snap.root.parent / WORKSPACES_DIR))
    parent.mkdir(parents=True, exist_ok=True)
    holder = Path(tempfile.mkdtemp(prefix=f"{snap.snapshot_id}-", dir=parent))
    try:
        workspace = (holder / "project").resolve(strict=False)
        workspace.mkdir()
        roots = source_roots(snap)
        for entry in sorted(snap.project_dir.iterdir()):
            src = Path(os.path.realpath(entry)) if entry.is_symlink() else entry
            if not src.exists():
                continue
            _clone(src, workspace / entry.name)

        # A link the source itself carries, pointing back into the source,
        # is re-pointed at the same place in the clone.
        for dirpath, dirnames, filenames in os.walk(workspace,
                                                    followlinks=False):
            for name in dirnames + filenames:
                p = Path(dirpath) / name
                if not p.is_symlink():
                    continue
                target = os.path.realpath(p)
                root = _inside(target, roots)
                if root is None:
                    continue
                p.unlink()
                os.symlink(str(workspace) + target[len(root.rstrip(os.sep)):], p)

        escaped = escaping_paths(workspace, roots)
        if escaped:
            raise IsolationError(
                f"{len(escaped)} path(s) in the replay workspace still "
                f"resolve into the source project, e.g. {escaped[0]}")

        state_file = workspace / "pipeline_data.json"
        with open(state_file, encoding="utf-8") as fh:
            state = json.load(fh)
        state = _reroot(state, roots, str(workspace), [0])
        with open(state_file, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
        yield workspace
    finally:
        shutil.rmtree(holder, ignore_errors=True)
