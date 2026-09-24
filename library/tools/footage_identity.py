"""The project's source footage, and the identity of each clip.

Two jobs, both of which used to be spread around:

**Enumeration.**  ``enumerate_footage`` is the one place that knows which
extensions count as footage and how a ``clip_id`` is assigned - sorted by
absolute path, ``clip_001`` upward.  Step 1.01 calls it, and so does the
runner's identity check.  The rule has to live in one place because the
two ends must agree: the runner decides whether ``clip_007``'s cached
analysis is still about the same file, and it can only do that if it
numbers clips exactly the way the scan did.

**Identity.**  ``fingerprint`` is the file's size plus a digest of its
first and last mebibyte.  Two things it is deliberately NOT:

*Not a whole-file hash.*  Reading seventeen multi-gigabyte camera files
on every run is sustained local IO for a question that two mebibytes
answer.  Two mebibytes per clip is milliseconds; the whole file is
minutes.

*Not mtime.*  mtime is the obvious cheap answer and it is the wrong one,
because the two ways of being wrong here are not symmetric.  A false
NEGATIVE - stale analysis surviving one more run - costs the operator one
``--rerun <step>:<clip_id>``.  A false POSITIVE destroys forty minutes of
WhisperX.  And mtime moves for reasons that have nothing to do with the
content: a restore, a ``cp`` without ``-p``, a sync client, a backup tool
walking the tree.  Content does not.

The residual blind spot, stated rather than hidden: a file edited only in
its middle, to exactly the same length, reads as unchanged.  For camera
footage that does not happen, and when it does the fix is one explicit
``--rerun``.

The identity check exists so that "preflight is skipped once done" is
SAFE rather than merely fast.  See ``library/tools/step_ledger.py`` for
the ledger it guards.
"""

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# The extensions the pipeline treats as raw footage.  Step 1.01 imports
# this rather than carrying its own copy.
SUPPORTED_VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".avi", ".mkv", ".mts", ".m4v", ".webm",
    # MXF is what professional cameras write - the GEO Podcast field
    # test is seven Sony XAVC files in MXF OP1A, and without this the
    # readiness check reported the project as having no footage at all.
    # Added on measurement rather than on principle: `extract_metadata`
    # read all seven on 2026-09-04, reporting 3840x2160 @ 23.976 for
    # each. See tests/test_footage_root.py.
    ".mxf",
}

# Voiceover and music audio: the takes and beds a project brings that
# carry no picture. Enumerated SEPARATELY from video (`enumerate_audio`
# below), in their own `audio_001` numbering, for one reason: clip ids
# are assigned centrally sorted by path, so admitting a voiceover wav
# into the video numbering would RENUMBER every video clip after it
# and orphan every cached per-clip analysis. A separate space means a
# project can add narration without invalidating its footage.
SUPPORTED_AUDIO_EXTENSIONS = {
    ".mp3", ".wav", ".aif", ".aiff", ".m4a", ".flac",
}

# How much of each end of a file the digest covers.  Big enough that two
# takes cannot collide on it, small enough that seventeen clips cost
# milliseconds.
DIGEST_WINDOW_BYTES = 1024 * 1024


def fingerprint(path: str) -> Dict[str, object]:
    """Cheap identity of one media file: its size, and both of its ends.

    Raises OSError if the file is gone - callers that treat a missing
    file as "removed" catch it themselves.
    """
    size = os.path.getsize(path)
    digest = hashlib.sha256()
    digest.update(str(size).encode("ascii"))
    with open(path, "rb") as f:
        digest.update(f.read(DIGEST_WINDOW_BYTES))
        if size > DIGEST_WINDOW_BYTES:
            f.seek(max(size - DIGEST_WINDOW_BYTES, DIGEST_WINDOW_BYTES))
            digest.update(f.read(DIGEST_WINDOW_BYTES))
    return {"size_bytes": size, "content_digest": digest.hexdigest()}


def footage_root(project_folder: str) -> str:
    """Where this project's footage lives.

    ``<project_folder>/raw`` unless the project DECLARES a
    ``source.footage_root``, which is how a project whose media lives
    elsewhere says so.

    The alternative was to symlink or copy the media into ``raw``, and
    that is a write to the captain's own material to work around a
    missing capability - so the capability exists instead.  Measured
    case (2026-09-04): the GEO Podcast field test was cut in Resolve
    from footage under ``Lucie consulting/Social Media/podcast media``,
    ``raw`` was empty, and the readiness check refused the project as
    having no footage at all.

    A declared root must be ABSOLUTE and must exist.  Both are refused
    rather than defaulted back to ``raw``: silently falling back would
    turn a typo into "this project has no footage", which is the exact
    unhelpful refusal this closes.
    """
    declared = ""
    try:
        from library.schemas.project_config import load_project_config
        config = load_project_config(
            os.path.join(project_folder, "project.yaml"))
        declared = (config.source.footage_root or "").strip()
    except Exception:
        # No readable project.yaml is not an error here - step 1.01 and
        # the identity check both call this on bare directories in
        # tests. The default is what those expect.
        declared = ""

    if not declared:
        return os.path.join(project_folder, "raw")

    if not os.path.isabs(declared):
        raise FileNotFoundError(
            f"source.footage_root is {declared!r}, a relative path. "
            f"Media is addressed absolutely everywhere else in this "
            f"pipeline, and a relative one resolves against whichever "
            f"directory a step happens to run in.")
    if not os.path.isdir(declared):
        raise FileNotFoundError(
            f"source.footage_root names {declared}, which is not a "
            f"directory. Refusing rather than falling back to "
            f"{project_folder}/raw: a typo that silently becomes 'this "
            f"project has no footage' is the refusal this declaration "
            f"exists to prevent.")
    return declared


def source_block(project_folder: str) -> Dict[str, object]:
    """The project's raw `source:` block, or {}.

    Read straight off project.yaml - the same route
    `brand_registry.project_pipeline_block` takes for `pipeline:` -
    so a declaration works whether or not the key reached the run's
    broadcast `project_config`. Never raises: an absent or unreadable
    project.yaml reads as "nothing declared", and malformed values
    read as undeclared here. `ProjectConfig.validate` (in
    `library/schemas/project_config.py`) is what tells the project
    its declaration is malformed.
    """
    if not project_folder:
        return {}
    project_yaml = os.path.join(project_folder, "project.yaml")
    if not os.path.isfile(project_yaml):
        return {}
    try:
        import yaml
    except ImportError:
        return {}
    try:
        with open(project_yaml, encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except (OSError, ValueError):
        return {}
    block = data.get("source") or {}
    return block if isinstance(block, dict) else {}


def declared_program_stream(project_folder: str) -> Optional[int]:
    """The 1-based program-mix channel the project declares, or None.

    Which channel of multi-channel footage is the mix is a property of
    the footage: declared here (`source.program_stream`), or measured
    (`source.measure_program_stream`, honoured by step 1.02), never a
    bare constant in the code. Measured 2026-09-19 across all seven
    geo-podcast sources: CH1 is the mix on every file, CH2 is empty,
    CH3/CH4 are ISOs - so that project declares `1`.
    """
    declared = source_block(project_folder).get("program_stream")
    if (isinstance(declared, bool) or not isinstance(declared, int)
            or declared < 1):
        return None
    return declared


def measure_program_stream_flag(project_folder: str) -> bool:
    """Whether the project opts into measuring the program mix itself."""
    return bool(source_block(project_folder).get(
        "measure_program_stream", False))


def declared_language(project_folder: str) -> str:
    """The language the project declares its footage speaks, or "en".

    `source.language` in project.yaml. Undeclared, unreadable and
    malformed all read as English: every project transcribed English
    before the setting existed, and `ProjectConfig.validate` - not
    this - is what tells the project its declaration is malformed.
    """
    raw = source_block(project_folder).get("language")
    if isinstance(raw, str) and raw.strip():
        return raw.strip().lower()
    return "en"


def declared_speakers(project_folder: str) -> Optional[list]:
    """Who the project declares speaks in its footage, or None.

    `source.speakers` in project.yaml: None means undeclared (the
    historical two-speaker reading), `[]` means declared-zero - a
    project with no voices - and otherwise `[{name, role?}]`. Entries
    with no usable name are dropped; a non-list reads as undeclared.
    Malformed is not refused here: `ProjectConfig.validate` owns the
    refusal, the same split `source_block` documents.
    """
    raw = source_block(project_folder).get("speakers")
    if raw is None:
        return None
    if not isinstance(raw, list):
        return None
    out = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        clean = {"name": name.strip()}
        role = entry.get("role")
        if isinstance(role, str) and role.strip():
            clean["role"] = role.strip()
        out.append(clean)
    return out


def expected_speaker_count(project_folder: str = "",
                           declaration=None) -> Optional[int]:
    """How many speakers the project declares, or None when it doesn't.

    The count is what reel selection, judging and verification read
    instead of the bare constant 2: a declared one is a monologue, a
    declared zero is music/montage. Callers default None to 2, which
    is the historical reading and stays it for projects that declare
    nothing. `declaration` is the already-read roster where the caller
    has one; the project folder is read otherwise.
    """
    if declaration is None and project_folder:
        declaration = declared_speakers(project_folder)
    if declaration is None:
        return None
    return len(declaration)


def enumerate_footage(project_folder: str) -> Tuple[List[dict], List[dict]]:
    """Every video file under the project's footage root, with clip ids.

    The root is ``<project_folder>/raw`` unless the project declares a
    ``source.footage_root`` - see ``footage_root``.

    Returns ``(raw_footage_files, skipped_files)``.  Entries carry
    ``path`` (absolute), ``filename``, ``extension``, ``size_bytes`` and
    ``clip_id``.  Clip ids are assigned centrally, sorted by absolute
    path, so that steps running in parallel cannot desync on them.

    Note the consequence, which the identity check is built to absorb:
    adding a file that sorts early RENUMBERS every clip after it.  A
    per-clip artifact named ``clip_002.json`` then describes different
    footage than it did, which is why the recorded fingerprint carries
    the path and not only the size.
    """
    raw_dir = footage_root(project_folder)
    if not os.path.isdir(raw_dir):
        raise FileNotFoundError(
            f"Missing 'raw' subdirectory in project folder: {project_folder}"
        )

    files: List[dict] = []
    skipped: List[dict] = []

    for root, _dirs, names in os.walk(raw_dir):
        for fname in sorted(names):
            filepath = os.path.join(root, fname)
            ext = os.path.splitext(fname)[1].lower()
            if ext not in SUPPORTED_VIDEO_EXTENSIONS:
                continue
            if not os.access(filepath, os.R_OK):
                skipped.append({"path": filepath,
                                "reason": "permission denied"})
                continue
            size = os.path.getsize(filepath)
            if size == 0:
                skipped.append({"path": filepath,
                                "reason": "zero-byte file (likely corrupt)"})
                continue
            files.append({
                "path": os.path.abspath(filepath),
                "filename": fname,
                "extension": ext,
                "size_bytes": size,
            })

    files.sort(key=lambda entry: entry["path"])
    for i, entry in enumerate(files):
        entry["clip_id"] = f"clip_{i + 1:03d}"

    return files, skipped


def enumerate_audio(project_folder: str) -> Tuple[List[dict], List[dict]]:
    """Every audio-only file under the project's footage root.

    Voiceover takes and music beds: what the project brings that
    carries no picture. Same root as video (`footage_root`), same
    fingerprint mechanics, but a separate `audio_001` numbering - see
    `SUPPORTED_AUDIO_EXTENSIONS` for why the spaces must not mix.

    Returns `(raw_audio_files, skipped_files)`. Entries carry `path`,
    `filename`, `extension`, `size_bytes` and `audio_id`, mirroring
    `enumerate_footage` so the catalog and the identity check walk
    both lists the same way.
    """
    raw_dir = footage_root(project_folder)
    if not os.path.isdir(raw_dir):
        raise FileNotFoundError(
            f"Missing 'raw' subdirectory in project folder: {project_folder}"
        )

    files: List[dict] = []
    skipped: List[dict] = []

    for root, _dirs, names in os.walk(raw_dir):
        for fname in sorted(names):
            filepath = os.path.join(root, fname)
            ext = os.path.splitext(fname)[1].lower()
            if ext not in SUPPORTED_AUDIO_EXTENSIONS:
                continue
            if not os.access(filepath, os.R_OK):
                skipped.append({"path": filepath,
                                "reason": "permission denied"})
                continue
            size = os.path.getsize(filepath)
            if size == 0:
                skipped.append({"path": filepath,
                                "reason": "zero-byte file (likely corrupt)"})
                continue
            files.append({
                "path": os.path.abspath(filepath),
                "filename": fname,
                "extension": ext,
                "size_bytes": size,
            })

    files.sort(key=lambda entry: entry["path"])
    for i, entry in enumerate(files):
        entry["audio_id"] = f"audio_{i + 1:03d}"

    return files, skipped


def is_audio_id(value: object) -> bool:
    """True when this id names a catalogued audio file, not footage.

    The `audio_001` numbering is the whole of the answer: audio ids are
    assigned centrally by `enumerate_audio`, video ids by
    `enumerate_footage`, and the two spaces never mix - admitting a
    voiceover wav into the video numbering would renumber every video
    clip after it (see `SUPPORTED_AUDIO_EXTENSIONS`). A spine block or
    passage carrying one of these as its `clip_id` is voiceover-sourced
    speech: its words come from the audio file, and its picture - if it
    has one - comes from B-roll, never from the id.
    """
    return (isinstance(value, str)
            and value.startswith("audio_"))


def fingerprints_for(raw_footage_files: List[dict]) -> Dict[str, dict]:
    """``{clip_id: {path, size_bytes, content_digest}}`` for a footage list.

    A file that has vanished since it was enumerated is simply absent
    from the result, which reads downstream as "removed".
    """
    out: Dict[str, dict] = {}
    for i, entry in enumerate(raw_footage_files):
        if isinstance(entry, dict):
            path = entry.get("path", "")
            # Audio entries carry `audio_id`, video entries `clip_id` -
            # the two numbering spaces must not collide in one record.
            clip_id = (entry.get("clip_id") or entry.get("audio_id")
                       or f"clip_{i + 1:03d}")
        else:
            path = str(entry)
            clip_id = f"clip_{i + 1:03d}"
        if not path:
            continue
        try:
            fp = fingerprint(path)
        except OSError:
            continue
        out[clip_id] = {"path": os.path.abspath(path), **fp}
    return out


@dataclass
class SourceDelta:
    """What changed between the footage preflight saw and the footage now."""

    added: List[str] = field(default_factory=list)     # clip ids new here
    removed: List[str] = field(default_factory=list)   # clip ids gone
    changed: List[str] = field(default_factory=list)   # same id, other file

    @property
    def stale_clip_ids(self) -> List[str]:
        """Clip ids whose cached per-clip analysis can no longer be trusted.

        ``added`` is included: under the sorted-path numbering an "added"
        id may be a REUSED id that now points at different footage, and a
        genuinely new clip has no artifact to delete anyway.
        """
        return sorted(set(self.added) | set(self.removed) | set(self.changed))

    @property
    def footage_changed(self) -> bool:
        return bool(self.added or self.removed or self.changed)

    def describe(self) -> str:
        parts = []
        if self.added:
            parts.append(f"added {', '.join(sorted(self.added))}")
        if self.removed:
            parts.append(f"removed {', '.join(sorted(self.removed))}")
        if self.changed:
            parts.append(f"replaced {', '.join(sorted(self.changed))}")
        return "; ".join(parts) or "unchanged"


def compare(recorded: Dict[str, dict], current: Dict[str, dict]) -> SourceDelta:
    """Compare a recorded footage fingerprint against the footage on disk.

    A clip is ``changed`` when its id survives but the file behind it does
    not: a different path, a different size, or different content at
    either end.  All three are treated identically because all three mean
    the cached analysis describes something else.

    A record written before the digest existed carries no
    ``content_digest``.  It is compared on what it does have, so gaining
    the digest does not read as a project-wide footage change.
    """
    recorded = recorded or {}
    current = current or {}
    delta = SourceDelta()

    for clip_id in current:
        if clip_id not in recorded:
            delta.added.append(clip_id)
    for clip_id in recorded:
        if clip_id not in current:
            delta.removed.append(clip_id)

    for clip_id, now in current.items():
        was = recorded.get(clip_id)
        if not was:
            continue
        same = (
            os.path.abspath(str(was.get("path", "")))
            == os.path.abspath(str(now.get("path", "")))
            and was.get("size_bytes") == now.get("size_bytes")
            and (was.get("content_digest") is None
                 or was["content_digest"] == now.get("content_digest"))
        )
        if not same:
            delta.changed.append(clip_id)

    return delta


def stem_for(clip_id: str, fingerprints: Dict[str, dict]) -> str:
    """The media file stem behind a clip id.

    Step 1.03 caches vision profiles under the file STEM, not the clip
    id (see the note above ``_profile_stems`` in that step), so
    invalidating one clip's profile needs the translation.
    """
    entry = fingerprints.get(clip_id) or {}
    path = entry.get("path", "")
    return Path(path).stem if path else ""
