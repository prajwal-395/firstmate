"""
project_migration.py - bring an existing project folder onto the layout.

`project_layout.py` says where a project's files go.  Projects that
predate it do not know that, and project 001 - the only one that exists -
had accumulated nine hand-made state backups, six one-off Python scripts,
two unattributed .mov files, a mock SFX directory, two ad-hoc status
directories and a 517 MB archive directory, all at its top level.

This module organises a folder like that.  Three rules govern it, and
they are not negotiable:

    NOTHING IS DELETED.  Every action is a move or a copy.  A file whose
    purpose cannot be established goes to `pipeline_output/unsorted/`
    under a subdirectory that says why it is there.  An admitted unknown
    beats a confident wrong guess, and both beat a deletion.

    INPUT DIRECTORIES ARE NEVER MODIFIED.  `raw/`, `music/`, `assets/`,
    `brand_assets/` and `compositions/` are the captain's.  Pipeline
    output found inside one is COPIED out, not moved, so the input
    directory is left exactly as it was found.

    EVERY ACTION IS RECORDED.  A manifest lands in
    `pipeline_output/migrations/`, listing each action with its source,
    destination, byte count and reason, plus the byte totals before and
    after.  `revert_from_manifest` reads it back and undoes the moves.

Usage:
    python3 manage_project.py organize <slug>            # plan only
    python3 manage_project.py organize <slug> --apply
    python3 manage_project.py organize <slug> --revert <manifest.json>


Rules relocated from AGENTS.md 8
--------------------------------
These are the engine's rules for this module.  They lived in
AGENTS.md until it was split by subsystem; the wording is unchanged,
so each rule is findable by its own words, and AGENTS.md 8
keeps the headline and points here.

**A project that predates the layout is brought onto it with `manage_project.py organize <slug>`.**
`library/tools/project_migration.py`.
- **It never deletes.** Every action is a move or a copy, and a file whose purpose cannot be established goes to `pipeline_output/unsorted/<bucket>/` with a stated reason, never a guess. Measure what you can - `media_facts` records a file's duration, format and encoder - so an admitted unknown is an examined one.
- **It never deletes.** Unidentifiable files go to `pipeline_output/unsorted/` with a stated reason.
- **It never modifies an input directory.** Pipeline output found inside one is COPIED out.
- Every run writes a manifest to `pipeline_output/migrations/`.
- `tests/test_project_migration.py`.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from library.tools.project_layout import (
    AREAS,
    BACKUP_SUBDIR,
    LEGACY_BACKUP_SUBDIR,
    STEP_BY_ID,
    STEP_OUTPUT_FILE,
    STEP_SUMMARY_FILE,
    WRITABLE_KINDS,
    Area,
    Kind,
    ProjectLayout,
)
from library.tools.step_exporter import step_id_for_export

# Files this big get a head/tail digest rather than a full hash.  A whole
# -file sha256 of project 001's 973 MB top-level .mov is pure IO for a
# number nobody compares; size plus the ends is enough to tell a moved
# file from a different one, and it is the same compromise
# `footage_identity` already makes for exactly this reason.
FULL_HASH_CEILING_BYTES = 64 * 1024 * 1024
DIGEST_CHUNK_BYTES = 1024 * 1024

MANIFEST_VERSION = 1

# Where an unidentified thing goes, and what the label means.
UNSORTED_BUCKETS = {
    "loose_scripts": (
        "One-off Python left in the project folder. Not pipeline code and "
        "not footage; kept because it is the captain's."),
    "media": (
        "A video or audio file at the project root that could not be "
        "attributed to raw/ (source) or exports/ (a render)."),
    "status_files": (
        "Ad-hoc status and result files written by hand or by a test run."),
    "mock_data": (
        "Stand-in data used during development, found inside a real project."),
    "misc": (
        "Everything else that had no home and no obvious kind."),
}

VIDEO_EXTENSIONS = {".mov", ".mp4", ".m4v", ".avi", ".mkv", ".mxf", ".webm"}
AUDIO_EXTENSIONS = {".wav", ".mp3", ".aiff", ".aif", ".flac", ".m4a"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".heic", ".tif", ".tiff"}

# Left exactly where they are, and why.
KEEP_AT_ROOT = {
    "project.yaml": "the project's configuration - it belongs at the root",
    "pipeline_data.json": "pipeline run state - the root is where the runner looks",
    "pipeline_run.json": "the runner's account of itself (run_control.py)",
    "pipeline.pid": "the runner's pid file (run_control.py)",
    "pipeline.hold": "the handbrake (run_control.py)",
    "README-LAYOUT.md": "the generated description of this layout",
    ".DS_Store": (
        "a Finder artifact, not the captain's work. Moving it achieves "
        "nothing - the Finder writes it again on the next look"),
}


@dataclass
class Action:
    """One thing the migration does to one path."""

    action: str          # "move" or "copy"
    src: str
    dest: str
    kind: str            # "file" or "dir"
    bytes: int
    digest: str
    reason: str


@dataclass
class Plan:
    project: str
    actions: list = field(default_factory=list)
    left_in_place: list = field(default_factory=list)
    unidentified: list = field(default_factory=list)


# ── Measuring ───────────────────────────────────────────────────────

def tree_bytes(path: Path) -> int:
    """Total size of a file, or of every file under a directory."""
    if path.is_file():
        return path.stat().st_size
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            p = Path(root) / name
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def digest(path: Path) -> str:
    """A fingerprint good enough to match a file to where it went.

    Full sha256 under FULL_HASH_CEILING_BYTES; size plus the first and
    last mebibyte above it.  A directory gets its file count and total
    size, because hashing 517 MB of archive to prove a `mv` happened is
    IO nobody asked for.
    """
    if path.is_dir():
        n = sum(len(f) for _r, _d, f in os.walk(path))
        return f"dir:{n}files:{tree_bytes(path)}bytes"
    size = path.stat().st_size
    h = hashlib.sha256()
    with open(path, "rb") as f:
        if size <= FULL_HASH_CEILING_BYTES:
            for chunk in iter(lambda: f.read(DIGEST_CHUNK_BYTES), b""):
                h.update(chunk)
            return f"sha256:{h.hexdigest()}"
        h.update(f.read(DIGEST_CHUNK_BYTES))
        f.seek(max(0, size - DIGEST_CHUNK_BYTES))
        h.update(f.read(DIGEST_CHUNK_BYTES))
    return f"size{size}+ends:{h.hexdigest()}"


# ── Planning ────────────────────────────────────────────────────────

def media_facts(path: Path) -> str:
    """What ffprobe can say about a media file, as one clause.

    An unattributed file is still a measurable one.  "121 MB" tells a
    reader nothing; "61.7s, 1080x1920, written by DaVinci Resolve Studio"
    tells them it is a render and roughly which one, which is the
    difference between an admitted unknown and an unexamined one.
    """
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60, check=False)
        if proc.returncode != 0:
            return ""
        data = json.loads(proc.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""

    fmt = data.get("format", {})
    bits = []
    try:
        bits.append(f"{float(fmt.get('duration', 0)):.1f}s")
    except (TypeError, ValueError):
        pass
    for st in data.get("streams", []):
        if st.get("codec_type") == "video":
            bits.append(f"{st.get('width')}x{st.get('height')} "
                        f"{st.get('codec_name')}")
            break
    for st in data.get("streams", []):
        if st.get("codec_type") == "audio":
            bits.append(f"audio {st.get('codec_name')}")
            break
    encoder = (fmt.get("tags") or {}).get("encoder")
    if encoder:
        bits.append(f"written by {encoder}")
    created = (fmt.get("tags") or {}).get("creation_time")
    if created:
        bits.append(f"created {created[:10]}")
    return ", ".join(b for b in bits if b)


def image_facts(path: Path) -> str:
    """A PNG's pixel dimensions, read off its header."""
    try:
        head = path.read_bytes()[:24]
        if head[:8] != b"\x89PNG\r\n\x1a\n":
            return ""
        w, h = struct.unpack(">II", head[16:24])
        return f"{w}x{h}"
    except (OSError, struct.error):
        return ""


def _unsorted_dest(layout: ProjectLayout, bucket: str, name: str) -> Path:
    assert bucket in UNSORTED_BUCKETS, bucket
    return layout.read_path(Area.UNSORTED, bucket, name)


def plan_organization(project_folder) -> Plan:
    """What organising this folder would do.  Reads only."""
    layout = ProjectLayout(project_folder)
    root = layout.root
    plan = Plan(project=str(root))

    known_dirs = {}
    for area, spec in AREAS.items():
        if spec.relpath == "." or "/" in spec.relpath:
            continue
        known_dirs[spec.relpath] = (area, spec)

    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        name = entry.name

        if name in KEEP_AT_ROOT:
            plan.left_in_place.append({
                "path": name, "bytes": tree_bytes(entry),
                "reason": KEEP_AT_ROOT[name]})
            continue

        if entry.is_dir() and name in known_dirs:
            area, spec = known_dirs[name]
            plan.left_in_place.append({
                "path": name + "/", "bytes": tree_bytes(entry),
                "reason": f"{spec.kind.value}: {spec.purpose}"})
            continue

        act = _classify(layout, entry)
        if act is None:
            plan.left_in_place.append({
                "path": name, "bytes": tree_bytes(entry),
                "reason": "already where the layout puts it"})
        else:
            plan.actions.append(act)
            if "/unsorted/" in act.dest:
                plan.unidentified.append({
                    "path": name,
                    "bytes": act.bytes,
                    "bucket": Path(act.dest).parent.name,
                    "why_unknown": act.reason,
                })

    relocations = _plan_legacy_relocations(layout)
    plan.actions.extend(relocations)
    plan.actions.extend(_plan_input_dir_rescues(layout))
    # Last, and told what the relocations are taking with them: the
    # husks are only empty once those have run.
    plan.actions.extend(_plan_legacy_dir_cleanup(
        layout, moving=[a.src for a in relocations if a.action == "move"]))
    plan.actions.extend(_plan_scaffold_empty_cleanup(layout))
    return plan


def _classify(layout: ProjectLayout, entry: Path):
    """The one action for one top-level entry, or None to leave it."""
    name = entry.name
    suffix = entry.suffix.lower()

    # Hand-made state backups.  These are the reason the retention policy
    # exists; they keep their names and go where they can never be pruned.
    if name.startswith("pipeline_data.json.") and ".bak" in name:
        return Action(
            "move", str(entry),
            str(layout.read_path(Area.BACKUPS, BACKUP_SUBDIR,
                                 LEGACY_BACKUP_SUBDIR, name)),
            "file", tree_bytes(entry), digest(entry),
            "a hand-made backup of pipeline_data.json, made before the "
            "retention policy existed. Kept verbatim in legacy/, which the "
            "pruner never touches.")

    if entry.is_dir():
        if name.startswith(("_archive", "archive")):
            return Action(
                "move", str(entry),
                str(layout.read_path(Area.BACKUPS, "run_archives", name)),
                "dir", tree_bytes(entry), digest(entry),
                "a hand-made snapshot of a previous run. It is a backup, so "
                "it lives with the backups.")
        if name == "pipeline_assets":
            return Action(
                "move", str(entry), str(layout.read_dir(Area.CARRIERS)),
                "dir", tree_bytes(entry), digest(entry),
                "transparent carrier clips. The layout calls this area "
                "carriers/, under the output root.")
        if "mock" in name.lower():
            return Action(
                "move", str(entry), str(_unsorted_dest(layout, "mock_data", name)),
                "dir", tree_bytes(entry), digest(entry),
                "stand-in data inside a real project. Nothing in the "
                "pipeline reads it, and it is not the captain's footage.")
        if name == "state":
            return Action(
                "move", str(entry),
                str(_unsorted_dest(layout, "status_files", name)),
                "dir", tree_bytes(entry), digest(entry),
                "ad-hoc run status files. No step writes or reads this "
                "directory; run state is pipeline_run.json at the root.")
        return Action(
            "move", str(entry), str(_unsorted_dest(layout, "misc", name)),
            "dir", tree_bytes(entry), digest(entry),
            "a directory at the project root that the layout does not name "
            "and no step reads.")

    if suffix == ".py":
        return Action(
            "move", str(entry),
            str(_unsorted_dest(layout, "loose_scripts", name)),
            "file", tree_bytes(entry), digest(entry),
            "a one-off script living in a content folder. Not imported by "
            "the pipeline, so what it did and whether it still works is "
            "unknown.")

    if suffix == ".status":
        return Action(
            "move", str(entry),
            str(_unsorted_dest(layout, "status_files", name)),
            "file", tree_bytes(entry), digest(entry),
            "an ad-hoc status file. Nothing in the pipeline writes or reads "
            "this name.")

    if suffix in VIDEO_EXTENSIONS or suffix in AUDIO_EXTENSIONS:
        facts = media_facts(entry)
        return Action(
            "move", str(entry), str(_unsorted_dest(layout, "media", name)),
            "file", tree_bytes(entry), digest(entry),
            "media at the project root"
            + (f" ({facts})" if facts else "")
            + ". It is not under raw/, so the scan does not enumerate it as "
              "source footage, and it is not under exports/, so no render "
              "report claims it. Nothing in pipeline_data.json names it. "
              "What it was FOR cannot be established from the folder.")

    if suffix in IMAGE_EXTENSIONS:
        facts = image_facts(entry)
        return Action(
            "move", str(entry), str(_unsorted_dest(layout, "media", name)),
            "file", tree_bytes(entry), digest(entry),
            "an image at the project root"
            + (f" ({facts})" if facts else "")
            + ", referenced by nothing the pipeline reads.")

    return Action(
        "move", str(entry), str(_unsorted_dest(layout, "misc", name)),
        "file", tree_bytes(entry), digest(entry),
        "a file at the project root that the layout does not name.")


# Where the by-KIND layout put things, and the area that owns them now.
#
# The first layout grouped by kind - `pipeline_output/prosody/`,
# `pipeline_output/temporal_index/`. That was a correct answer to the
# wrong question: the captain wants to walk the folder in pipeline order,
# which means one directory per STEP. A project laid out the old way is
# moved onto the new one from this table, and a project already on the
# new one finds nothing to do.
LEGACY_AREA_DIRS = {
    "vision_analysis": Area.VISION_ANALYSIS,
    "temporal_index": Area.TEMPORAL_INDEX,
    "audio_cache": Area.AUDIO_CACHE,
    "prosody": Area.PROSODY,
    "segmentation_data": Area.SEGMENTATION,
    "ocr_data": Area.OCR,
    "music": Area.MUSIC_ANALYSIS,
    "acquired_media": Area.ACQUIRED_MEDIA,
    "subtitle_segments": Area.SUBTITLE_SEGMENTS,
    "motion_graphics_segments": Area.MOTION_GRAPHICS_SEGMENTS,
    "timed_text_segments": Area.TIMED_TEXT_SEGMENTS,
    "fusion_comps": Area.FUSION_COMPS,
    "carriers": Area.CARRIERS,
    "qa_frames": Area.QA_FRAMES,
}

LEGACY_ROOT_FILES = {
    "pipeline_log.jsonl": (Area.LOGS, "pipeline_log.jsonl"),
    "assembly_manifest.json": (Area.ASSEMBLY_MANIFEST, "assembly_manifest.json"),
}


def _plan_legacy_relocations(layout: ProjectLayout) -> list:
    """Move a by-kind output tree onto the by-step one."""
    out = []
    out_root = layout.read_dir(Area.OUTPUT_ROOT)
    if not out_root.is_dir():
        return out

    for legacy_name, area in LEGACY_AREA_DIRS.items():
        src_dir = out_root / legacy_name
        dest_dir = layout.read_dir(area)
        if not src_dir.is_dir() or src_dir.resolve() == dest_dir.resolve():
            continue
        step = AREAS[area].step
        for src in sorted(src_dir.rglob("*")):
            if not src.is_file():
                continue
            dest = dest_dir / src.relative_to(src_dir)
            out.append(Action(
                "move", str(src), str(dest), "file",
                tree_bytes(src), digest(src),
                f"was grouped by kind under {legacy_name}/. It is step "
                f"{step}'s product, so it moves into that step's directory - "
                f"which is what makes pipeline_output/steps/ walkable."))

    # The runner's per-step exports, `<step_id>.json` at the output root.
    for src in sorted(out_root.glob("*")):
        if not src.is_file():
            continue
        if src.name in LEGACY_ROOT_FILES:
            area, name = LEGACY_ROOT_FILES[src.name]
            dest = layout.read_path(area, name)
            if src.resolve() != dest.resolve():
                out.append(Action(
                    "move", str(src), str(dest), "file",
                    tree_bytes(src), digest(src),
                    f"belongs in {AREAS[area].relpath}/ under the layout."))
            continue
        step_id = step_id_for_export(src.name)
        if step_id and step_id in STEP_BY_ID:
            leaf = (STEP_SUMMARY_FILE if src.name.endswith(".summary.md")
                    else STEP_OUTPUT_FILE)
            out.append(Action(
                "move", str(src), str(layout.step_dir(step_id) / leaf),
                "file", tree_bytes(src), digest(src),
                f"is step {step_id}'s own output, exported as a flat "
                f"{src.name} at the output root. It moves into that step's "
                f"directory as {leaf}."))
    return out


REMOVE_EMPTY = "remove_empty_dir"


def _plan_legacy_dir_cleanup(layout: ProjectLayout, moving=()) -> list:
    """Legacy directories left standing with nothing in them.

    The by-kind directories become empty husks once their contents move
    under the owning step, and `ls pipeline_output/` is the thing this
    whole change exists to make legible - so they go.

    This is the ONE action that removes anything, and it is bounded to
    the point of being uncontroversial: only a directory named in
    LEGACY_AREA_DIRS, only when it contains no file at any depth, and
    recorded in the manifest like every other action. No byte of the
    captain's is inside an empty directory.
    """
    out = []
    out_root = layout.read_dir(Area.OUTPUT_ROOT)
    if not out_root.is_dir():
        return out
    for legacy_name, area in LEGACY_AREA_DIRS.items():
        d = out_root / legacy_name
        dest = layout.read_dir(area)
        if not d.is_dir() or d.resolve() == dest.resolve():
            continue
        # `moving` is what the relocations above are about to take out
        # of here, so the plan says "this will be empty" rather than
        # needing a second run to notice that it is.
        leaving = set(moving)
        if any(p.is_file() and str(p) not in leaving for p in d.rglob("*")):
            continue
        out.append(Action(
            REMOVE_EMPTY, str(d), "", "dir", 0, digest(d),
            f"an empty directory left by the by-kind layout. Its contents "
            f"moved to {AREAS[area].relpath}/; nothing is inside it."))
    return out


def _plan_scaffold_empty_cleanup(layout: ProjectLayout) -> list:
    """Empty directories left by the old `ensure()` pre-creation.

    Before this change, `ensure()` pre-created every writable area and
    every step directory regardless of whether the step had run.  That
    produced ~40 empty directories in a project whose pipeline had not
    yet completed.  The new `ensure()` only creates the containers;
    this planner cleans up the leftovers from the old one.

    Bounded by the same rule as `_plan_legacy_dir_cleanup`: only a
    directory that holds no file at any depth, and only a directory that
    the layout NAMES (a step directory in STEPS, or a writable area in
    AREAS).  Re-checked at the moment of removal.  Recorded in the
    manifest.  Revertible.
    """
    out = []
    out_root = layout.read_dir(Area.OUTPUT_ROOT)
    if not out_root.is_dir():
        return out

    from library.tools.project_layout import STEPS, _OUT, _STEPS

    # Empty step directories: a step that has not run should not show.
    for step in STEPS:
        d = layout.root / _STEPS / step.dirname
        if not d.is_dir():
            continue
        if any(p.is_file() for p in d.rglob("*")):
            # The step ran and has output. But check for empty
            # sub-areas within it (e.g. render/carriers/ created by
            # write_dir() but left empty when the step decided not to
            # write there).
            for sub in sorted(d.iterdir(), reverse=True):
                if not sub.is_dir():
                    continue
                if any(p.is_file() for p in sub.rglob("*")):
                    continue
                out.append(Action(
                    REMOVE_EMPTY, str(sub), "", "dir", 0, digest(sub),
                    f"an empty sub-area inside step {step.node_id}. "
                    f"The step ran but wrote nothing here."))
            continue
        out.append(Action(
            REMOVE_EMPTY, str(d), "", "dir", 0, digest(d),
            f"an empty step directory pre-created by the old scaffold. "
            f"Step {step.node_id} has not run, so the directory carries no "
            f"information. It will reappear when the step writes output."))

    # Empty project-level areas: gates/, annotations/, messages/ etc.
    for area, spec in AREAS.items():
        if spec.step:
            continue  # step dirs are handled above
        if spec.kind not in WRITABLE_KINDS or spec.relpath == ".":
            continue
        if spec.relpath in (_OUT, _STEPS):
            continue  # the containers stay
        d = layout.root / spec.relpath
        if not d.is_dir():
            continue
        if any(p.is_file() for p in d.rglob("*")):
            continue
        out.append(Action(
            REMOVE_EMPTY, str(d), "", "dir", 0, digest(d),
            f"an empty {area.value} directory pre-created by the old "
            f"scaffold. Nothing has written to it. It will reappear when "
            f"something writes."))

    # Empty directories inside backup archives: gates/ with 23 empty
    # subdirs, messages/, thumbnails/ etc. inside _archive_* directories.
    # These are safe to remove because they carry no files.
    archives = layout.root / _OUT / "backups" / "run_archives"
    if archives.is_dir():
        # Walk bottom-up so parent dirs become empty after children go.
        # Collect candidates first, then sort deepest-first.
        candidates = []
        for d in sorted(archives.rglob("*"), reverse=True):
            if not d.is_dir():
                continue
            if any(p.is_file() for p in d.rglob("*")):
                continue
            candidates.append(d)
        for d in candidates:
            out.append(Action(
                REMOVE_EMPTY, str(d), "", "dir", 0, digest(d),
                "an empty directory inside a backup archive. Nothing was "
                "archived in it; it was a scaffold artifact."))
    return out


def _plan_input_dir_rescues(layout: ProjectLayout) -> list:
    """Pipeline output found inside an input directory.

    COPIED, never moved.  Input directories belong to the captain and
    this tool does not modify them - so the cache ends up where the code
    now looks for it, and `raw/` is left byte for byte as it was found.
    """
    out = []
    legacy_vision = layout.read_path(Area.RAW, "analysis")
    if legacy_vision.is_dir():
        dest = layout.read_dir(Area.VISION_ANALYSIS)
        for src in sorted(legacy_vision.glob("*.json")):
            # The source stays where it is, so without this check every
            # subsequent run would copy it again beside itself as
            # `clip_profile_IMG_1806_v3__2.json` - and step 1.03 globs
            # `clip_profile_*.json`, so it would read that as a
            # seventeen-clip project with a stem it has never seen.
            landed = dest / src.name
            if landed.is_file() and landed.stat().st_size == src.stat().st_size:
                continue
            out.append(Action(
                "copy", str(src), str(dest / src.name),
                "file", tree_bytes(src), digest(src),
                "a vision profile written into raw/ before the layout "
                "existed. COPIED, not moved: raw/ is the captain's and this "
                "tool does not modify it. The copy is what step 1.03 now "
                "reads, so the analysis is not recomputed."))
    return out


# ── Applying ────────────────────────────────────────────────────────

def _unique(dest: Path) -> Path:
    """A destination that does not already exist.

    Never overwrite.  A collision means two things share a name, and both
    are the captain's.
    """
    if not dest.exists():
        return dest
    n = 2
    while True:
        cand = dest.with_name(f"{dest.stem}__{n}{dest.suffix}")
        if not cand.exists():
            return cand
        n += 1


def organize_project(project_folder, apply: bool = False) -> dict:
    """Plan the reorganisation, optionally perform it, return the manifest."""
    layout = ProjectLayout(project_folder)
    root = layout.root
    plan = plan_organization(root)

    before = _snapshot(root)
    performed = []

    if apply:
        layout.ensure()
        for act in plan.actions:
            src = Path(act.src)
            if not src.exists():
                continue
            if act.action == REMOVE_EMPTY:
                # Re-checked at the moment of removal, not just when the
                # plan was made: a relocation in this same run may have
                # emptied it, and something else may have filled it.
                if any(p.is_file() for p in src.rglob("*")):
                    continue
                shutil.rmtree(src)
                performed.append(act)
                continue
            dest = _unique(Path(act.dest))
            dest.parent.mkdir(parents=True, exist_ok=True)
            # The guard, applied to every destination: a migration that
            # can write into an input directory is not a migration.
            layout.assert_writable(dest)
            if act.action == "copy":
                shutil.copy2(src, dest)
            else:
                shutil.move(str(src), str(dest))
            act.dest = str(dest)
            performed.append(act)
        # Post-apply sweep: moves in this run may have carried empty
        # subdirectories into the backup archives (e.g. an archive
        # containing gates/ with 23 empty per-step subdirs).  A second
        # plan round would catch them, but the point is stability: one
        # run should be a no-op the second time.
        post = _plan_scaffold_empty_cleanup(layout)
        for act in post:
            src = Path(act.src)
            if not src.exists():
                continue
            if any(p.is_file() for p in src.rglob("*")):
                continue
            shutil.rmtree(src)
            performed.append(act)
        _write_bucket_readmes(layout, plan)

    after = _snapshot(root)

    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "applied": bool(apply),
        "project": str(root),
        "layout_owner": "library/tools/project_layout.py",
        "policy": {
            "nothing_deleted": True,
        "empty_directories_removed": (
            "Only a directory named by the by-kind layout, only when it "
            "holds no file at any depth. Every one is listed as a "
            f"{REMOVE_EMPTY!r} action below."),
            "input_dirs_unmodified": sorted(
                spec.relpath for spec in AREAS.values()
                if spec.kind is Kind.INPUT and spec.relpath != "."),
            "unsorted_buckets": UNSORTED_BUCKETS,
        },
        "totals": {
            "before_bytes": before["total_bytes"],
            "after_bytes": after["total_bytes"],
            "moved_bytes": sum(a.bytes for a in plan.actions
                               if a.action == "move"),
            "copied_bytes": sum(a.bytes for a in plan.actions
                                if a.action == "copy"),
        },
        "before": before,
        "after": after,
        "actions": [asdict(a) for a in (performed if apply else plan.actions)],
        "left_in_place": plan.left_in_place,
        "unidentified": plan.unidentified,
    }

    if apply:
        path = layout.write_path(
            Area.MIGRATIONS,
            f"migration_{time.strftime('%Y%m%dT%H%M%S')}.json")
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        manifest["manifest_path"] = str(path)
        md = path.with_suffix(".md")
        md.write_text(render_manifest_markdown(manifest), encoding="utf-8")
        manifest["manifest_markdown_path"] = str(md)

    return manifest


def _snapshot(root: Path) -> dict:
    """Every top-level entry with its byte count.  This is the accounting."""
    entries = []
    for p in sorted(root.iterdir(), key=lambda q: q.name):
        entries.append({
            "name": p.name + ("/" if p.is_dir() else ""),
            "bytes": tree_bytes(p),
        })
    return {
        "total_bytes": sum(e["bytes"] for e in entries),
        "entry_count": len(entries),
        "entries": entries,
    }


def _write_bucket_readmes(layout: ProjectLayout, plan: Plan) -> None:
    """Say, in each unsorted bucket, what put things there."""
    for bucket, why in UNSORTED_BUCKETS.items():
        d = layout.read_path(Area.UNSORTED, bucket)
        if not d.is_dir():
            continue
        (d / "README.md").write_text(
            f"# {bucket}\n\n{why}\n\n"
            "Nothing here was identified with confidence, and nothing here "
            "was deleted.\nThe migration manifest in "
            "`../../migrations/` records where each of these came from.\n",
            encoding="utf-8")


# ── Reading it back ─────────────────────────────────────────────────

def render_manifest_markdown(manifest: dict) -> str:
    t = manifest["totals"]
    lines = [
        f"# Project reorganisation - {manifest['generated_at']}",
        "",
        f"Project: `{manifest['project']}`",
        f"Applied: {manifest['applied']}",
        "",
        "Nothing was deleted. Every line below is a move or a copy, and",
        "`project_migration.revert_from_manifest` undoes the moves.",
        "",
        "## Byte accounting",
        "",
        f"- before: {t['before_bytes']:,} bytes",
        f"- after: {t['after_bytes']:,} bytes",
        f"- moved: {t['moved_bytes']:,} bytes",
        (f"- copied: {t['copied_bytes']:,} bytes (input directories are "
         f"copied out of, never moved)"),
        "",
        "## What moved",
        "",
        "| action | from | to | bytes | why |",
        "|---|---|---|---:|---|",
    ]
    root = manifest["project"].rstrip("/") + "/"
    for a in manifest["actions"]:
        src = a["src"].replace(root, "")
        dest = a["dest"].replace(root, "")
        dest_cell = f"`{dest}`" if dest else "(removed - it was empty)"
        lines.append(
            f"| {a['action']} | `{src}` | {dest_cell} | {a['bytes']:,} | "
            f"{a['reason']} |")

    lines += ["", "## What stayed, and why", "",
              "| path | bytes | why |", "|---|---:|---|"]
    for e in manifest["left_in_place"]:
        lines.append(f"| `{e['path']}` | {e['bytes']:,} | {e['reason']} |")

    if manifest["unidentified"]:
        lines += ["", "## Could not be identified", "",
                  "Named honestly rather than guessed at, and kept.", "",
                  "| path | bytes | bucket | why |", "|---|---:|---|---|"]
        for e in manifest["unidentified"]:
            lines.append(
                f"| `{e['path']}` | {e['bytes']:,} | {e['bucket']} | "
                f"{e['why_unknown']} |")
    lines.append("")
    return "\n".join(lines)


def revert_from_manifest(manifest_path, apply: bool = False) -> list:
    """Undo a reorganisation by reading its manifest.

    Moves go back to where they came from.  Copies are left alone: the
    source they were copied FROM was never touched, so undoing a copy
    would mean deleting something, and this tool does not delete.
    """
    data = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    undone = []
    for a in data["actions"]:
        if a["action"] == REMOVE_EMPTY:
            src = Path(a["src"])
            if apply and not src.exists():
                src.mkdir(parents=True, exist_ok=True)
            undone.append({"from": "(empty)", "to": str(src), "bytes": 0})
            continue
        if a["action"] != "move":
            continue
        src, dest = Path(a["src"]), Path(a["dest"])
        if not dest.exists() or src.exists():
            continue
        if apply:
            src.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dest), str(src))
        undone.append({"from": str(dest), "to": str(src), "bytes": a["bytes"]})
    return undone
