"""One content-keyed render cache for every Remotion-drawn overlay.

Caption segments (step 4.05) and motion graphics (step 4.06) used to be
NAMED FOR THE TIMELINE they belonged to, so three variant timelines
captioning the same words rendered the same pixels three times -
measured on the captain's project as 67 caption movs holding 23 unique
byte-contents (365.6 MB duplicate) and 12 motion-graphics movs holding
4 (see `docs/RULE_EVIDENCE.md`). The splitter was identity, not pixels:
the filename carried the timeline, and the reuse key hashed the whole
props object including placement metadata.

The split this module owns:

- **Content key (what is looked up):** a digest of the drawing inputs
  only, plus the renderer fingerprint plus the carriage version.
  Placement fields - timeline identity, block ordinals, absolute
  timeline bounds - are never part of it. Duration IS: it is the
  file's frame count, and two variants holding one caption for
  genuinely different lengths must still render twice, because the
  pixels really differ.
- **Provenance stem (what the file is called):** the captain's ruling
  of 2026-09-09 - a subtitle render is rooted in the SOURCE FOOTAGE
  it came from, motion graphics and other assets in the PROJECT - so
  a directory listing says where an artefact came from. The digest
  still decides reuse; the stem makes the file legible. Neither half
  names a timeline.

The filename carries the SHORT drawing digest, while the sidecar reuse
key carries the FULL three-factor key. That division is deliberate:
the filename answers "which pixels", the sidecar answers "still
current" - a Remotion edit or a new carriage changes the fingerprint
half, so the recorded key mismatches and the pass re-renders over the
same filename rather than orphaning a file whose name can no longer be
recomputed.

A skip still needs BOTH the file and its recorded key present and
matching - never mere presence. The filename carries no caption
content... and now it carries no timeline either, so presence alone
would serve one variant's pixels to another variant's identical words,
which is exactly the sharing this module exists for - but it must
still not serve last carriage's pixels to this one.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

#: How many hex characters of the drawing digest a filename carries.
#: Uniqueness never rested on the readable half - the sidecar key is
#: the authority - so this is a collision-avoidance prefix, not a
#: security boundary.
SHORT_DIGEST_CHARS = 8


def drawing_digest(drawing: dict) -> str:
    """A stable hash of the drawing inputs, and nothing else.

    The caller decides what "drawing inputs" are - for captions that
    is the props minus the placement/metadata keys (see step 4.05),
    for motion graphics the rendered props plus their carrying (see
    step 4.06). This function only promises stability: canonical JSON,
    full length, so a filename prefix and a sidecar key derived from
    it agree with each other across runs and machines.
    """
    canonical = json.dumps(drawing, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def short_digest(full: str) -> str:
    """The filename half of a digest. Empty stays empty, never a hit."""
    return (full or "")[:SHORT_DIGEST_CHARS]


def renderer_fingerprint(remotion_dir: str) -> str:
    """The identity of the code and fonts that turn props into pixels.

    Props do NOT capture the Remotion composition or the bundled font,
    so a drawing digest alone would skip every segment forever after
    an edit to `remotion-subtitles/src/` - the pixels change and the
    key does not. That is exactly the defect
    `library/tools/code_identity.py` exists to remove one layer down,
    in its own words: "the system reports success while the work did
    not happen."

    Returns `""` when the tree cannot be read, and `""` NEVER matches -
    see `content_key`. Unavailable evidence must not read as matching
    evidence.

    One spelling: step 4.05's `_reuse_key` used to carry its own copy.
    Two fingerprints of one tree is how a renderer edit reuses on one
    path and re-renders on the other.
    """
    root = Path(remotion_dir)
    parts = []
    for pattern in ("src/**/*.tsx", "src/**/*.ts", "public/fonts/*"):
        for path in sorted(root.glob(pattern)):
            if not path.is_file():
                continue
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                return ""
            parts.append(f"{path.relative_to(root)}={digest}")
    if not parts:
        return ""
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:32]


def hyperframes_fingerprint(hyperframes_dir: str) -> str:
    """The identity of the HyperFrames templates and vendor that draw.

    The same promise as `renderer_fingerprint`, for the second engine:
    props do not capture the HTML templates or the vendored GSAP build,
    so a template edit must mismatch every recorded key and re-render
    rather than serve pixels the old template drew. `""` never matches,
    for the same reason. Tracked inputs only - the staged per-card
    project (props baked in) is build output, and the brand files beside
    it belong to the project rather than to the renderer.
    """
    root = Path(hyperframes_dir)
    parts = []
    for pattern in ("compositions/*.html", "vendor/*"):
        for path in sorted(root.glob(pattern)):
            if not path.is_file():
                continue
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                return ""
            parts.append(f"{path.relative_to(root)}={digest}")
    if not parts:
        return ""
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:32]


def content_key(digest: str, renderer_dir: str, carriage: str,
                engine: str = "remotion") -> str:
    """The three things that have to match for a skip to be safe, or `""`.

    Empty means "cannot be established", and every comparison against
    it fails, so an unreadable renderer tree renders rather than
    skips.

    The CARRIAGE is in the key because an artefact from a previous
    carriage is not stale, it is UNUSABLE: the `pan-tilt` era rendered
    a small canvas whose position lived in a Pan/Tilt Resolve silently
    clamped, and reusing one under today's rule would place a small
    clip with no transform at all - the caption centred in the frame
    instead of in its band. A key that did not name the carriage would
    let exactly that through as a hit.

    `engine` names which renderer drew (`graphics_renderer`): a
    Remotion artefact reused under HyperFrames selection (or the
    reverse) would serve pixels the selected engine never drew, so the
    fingerprint half is read off the selected engine's own tree -
    `renderer_dir` is that engine's directory. Unset means Remotion,
    which is every caller written before the second engine existed.
    """
    if engine == "hyperframes":
        fingerprint = hyperframes_fingerprint(renderer_dir)
    else:
        fingerprint = renderer_fingerprint(renderer_dir)
    if not fingerprint or not digest:
        return ""
    return f"{digest}+{fingerprint}+{carriage}"


def project_stem(project_folder: str) -> str:
    """The provenance stem for project-rooted artefacts: the project.

    The captain's ruling roots motion graphics (and other assets) in
    the project itself - he wants to look at a file and know where it
    came from. The basename is what he recognises in a listing; the
    digest beside it is what decides reuse. Two projects never share
    an output directory (`ProjectLayout` is per project), so a shared
    basename cannot collide across them.
    """
    from library.tools.subtitle_segment_id import slug
    base = os.path.basename(str(project_folder or "").rstrip(os.sep))
    return slug(base or None, "noproject")


def motion_segment_name(project_folder: str, digest: str) -> str:
    """The file stem for one motion-graphics render: project + content.

    No timeline, no reel, no index: two variants rendering the same
    graphic compute the same name and share the file, while a graphic
    that draws anything different digests differently and cannot
    overwrite it. The per-variant `vox_<reel>_<index>` name survives
    as the entry's `placement_label` - which placing this file serves -
    never as the file's identity.
    """
    return f"mg_{project_stem(project_folder)}_{short_digest(digest)}"


# ── Reuse ACROSS names: the content-addressed half ────────────────────
#
# The filename is provenance plus a short digest, and a skip used to be
# asked of ONE name: the file this segment's own stem computes. So the
# same pixels under a different provenance stem - a passage re-anchored
# a few milliseconds along its source, which moves the span in the name
# and nothing that draws - rendered again beside a byte-identical twin.
# Measured on geo-podcast (P3 audit, 2026-09-23): 81 caption movs, 126
# MB, were exact duplicates of another file in the same directory.
#
# The authority stays the sidecar: a stem is a hit only where its
# recorded `_reuse_key.txt` equals the FULL three-factor key, re-read at
# the moment of the hit. The directory scan below is an index of those
# sidecars and nothing more - a stale index can only cost a miss.
#
# The hit is MATERIALISED under this segment's own name as a HARD LINK,
# so the listing stays legible (the captain's 2026-09-09 ruling: a
# subtitle file is named for the footage it came from) and the bytes
# are stored once. Sidecars are COPIED, never linked: each is rewritten
# in place by its owner (`open(..., "w")`), and a linked sidecar would
# rewrite its twin's. The artefact is linked, so anything that rewrites
# one in place must `detach` it first.

REUSE_KEY_SUFFIX = "_reuse_key.txt"

_key_index: dict[str, tuple[int, dict[str, str]]] = {}


def _index(directory: str) -> dict[str, str]:
    """`{full key: stem}` for every recorded key in `directory`.

    Memoised per directory on its mtime, which moves whenever a sidecar
    is CREATED. A sidecar rewritten in place does not move it, which is
    why every hit re-reads the sidecar it points at (`find_by_key`).
    """
    try:
        stamp = os.stat(directory).st_mtime_ns
    except OSError:
        return {}
    cached = _key_index.get(directory)
    if cached and cached[0] == stamp:
        return cached[1]
    keys: dict[str, str] = {}
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return {}
    for name in names:
        if not name.endswith(REUSE_KEY_SUFFIX):
            continue
        try:
            recorded = Path(directory, name).read_text(
                encoding="utf-8").strip()
        except OSError:
            continue
        if recorded:
            keys.setdefault(recorded, name[:-len(REUSE_KEY_SUFFIX)])
    _key_index[directory] = (stamp, keys)
    return keys


def find_by_key(directory: str, key: str, artefact_suffix: str,
                exclude: str = "") -> str:
    """The stem of a file in `directory` whose recorded key IS `key`.

    `""` when there is none, when `key` is `""` (an unestablished key
    never matches - see `content_key`), or when the only match is
    `exclude`. The match is re-proved against the sidecar on disk and
    the artefact must exist as a regular file.
    """
    if not key:
        return ""
    stem = _index(directory).get(key, "")
    if not stem or stem == exclude:
        return ""
    try:
        recorded = Path(directory, stem + REUSE_KEY_SUFFIX).read_text(
            encoding="utf-8").strip()
    except OSError:
        return ""
    if recorded != key or not os.path.isfile(
            os.path.join(directory, stem + artefact_suffix)):
        return ""
    return stem


def detach(path: str) -> None:
    """Break a hard link before `path` is rewritten in place.

    A renderer opens its output for writing, which TRUNCATES the inode:
    an adopted file shares its inode with the stem it was adopted from,
    so writing through it would rewrite pixels another placing plays -
    mid-render, a timeline reading that file reads a half-written one.
    Unlinking the name first gives the rewrite a fresh inode and leaves
    the twin untouched. A file with one link is left alone.
    """
    try:
        if os.path.isfile(path) and os.stat(path).st_nlink > 1:
            os.unlink(path)
    except OSError:
        pass


def adopt(directory: str, key: str, stem: str, artefact_suffix: str,
          sidecar_suffixes: tuple = ()) -> str:
    """Serve `stem` from a file another stem already rendered. Returns the source stem.

    A no-op returning `""` when `stem` already holds `key` (its own
    reuse path decides that) or when no other stem holds it. Otherwise
    the artefact is hard-linked to `stem + artefact_suffix` (copied
    where the filesystem refuses a link), each existing sidecar in
    `sidecar_suffixes` is copied, and the reuse key is written LAST: a
    killed adoption leaves no key claiming the file is current, so the
    next pass renders rather than trusting half a copy.
    """
    import shutil

    if not key:
        return ""
    own_key = os.path.join(directory, stem + REUSE_KEY_SUFFIX)
    own_artefact = os.path.join(directory, stem + artefact_suffix)
    try:
        if Path(own_key).read_text(encoding="utf-8").strip() == key \
                and os.path.isfile(own_artefact):
            return ""
    except OSError:
        pass
    source = find_by_key(directory, key, artefact_suffix, exclude=stem)
    if not source:
        return ""
    for stale in (own_key, own_artefact):
        if os.path.lexists(stale):
            os.unlink(stale)
    source_artefact = os.path.join(directory, source + artefact_suffix)
    try:
        os.link(source_artefact, own_artefact)
    except OSError:
        shutil.copy2(source_artefact, own_artefact)
    for suffix in sidecar_suffixes:
        sidecar = os.path.join(directory, source + suffix)
        if os.path.isfile(sidecar):
            shutil.copyfile(sidecar, os.path.join(directory, stem + suffix))
    Path(own_key).write_text(key, encoding="utf-8")
    return source
