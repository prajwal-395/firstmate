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
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
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
ARCHIVE_DIRS = ("pipeline_output/llm_requests", "pipeline_output/llm_responses")

DEFAULT_STORE = Path(
    os.environ.get("PIPELINE_REPLAY_SNAPSHOTS",
                   Path.home() / ".video_editing_pilot" / "replay_snapshots")
)

MANIFEST_NAME = "snapshot.json"


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
    captured_at = datetime.now(timezone.utc)
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
        for f in sorted(src.glob("*.json")):
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
