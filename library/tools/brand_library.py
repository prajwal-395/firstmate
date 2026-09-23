"""Version control for the SERIES-shared brand library (AGENTS.md 14).

Where shared brand masters live - established, not assumed.  Found
2026-09-17 while verifying the logo frame-rate fix: the Lucie
``_shared/brand-assets/motion/`` folder (the logo reveal, its 23.976
conform, the transition bumper, its candidate conform, the eye-test
evidence) is not a git repository and is not inside one - while the
project declaration that names the conform IS versioned, in the
project's own store.  A generated file every reel now plans, with a
documented-but-unreproduced ffmpeg recipe, and no history, no undo
and no integrity record: delete or overwrite it and every reel build
loses its ending with nothing to restore from.

Three places it could live, and the evidence for each:

1. **Its own store, beside the projects that share it - SELECTED.**
   The declaration already says it: geo-podcast's project.yaml calls
   the closing logo "the SERIES' own file, beside the projects that
   share it", named by absolute path because a reference is a path
   plus a map (AGENTS.md 10.1).  One master, many referencing
   projects, one history.  This module is the store's companion:
   ``init_shared_repo`` once, then a ``MANIFEST.json`` beside the
   files it covers, verified with ``verify_manifest``.
2. **Inside each project's data store - REJECTED.**  The asset is one
   series' file shared across projects; a copy per project store is a
   copy that diverges, with nothing saying which is the master.  And
   the project store is a TEXT-ONLY allow-list by design
   (``build_version_control.py``): binaries are ignored by default,
   deliberately, which is what keeps renders and caches out.  Filing
   25 MB ProRes masters there fights that ruling.
3. **In the engine repo - REJECTED.**  A Lucie logo fails the
   substitution question (AGENTS.md 14: it encodes one series'
   identity), so the engine stays series-neutral; and PR 1181 states
   it outright - "Asset files are project data, so they never enter
   a pipeline-repo commit."

What "under version control" means here is three things, because the
gap was three things: HISTORY and UNDO come from git (the store is
its own repo, initialised once by an explicit act, never unasked -
the same rule ``init_project_repo`` keeps for project folders), and
the INTEGRITY RECORD is ``MANIFEST.json``: per file a whole-file
sha256, the byte size, the ffprobe measurement, and whatever
curation the captain states (role, recipe, note, which declarations
point at it).  The manifest is versioned text beside the binaries,
so a reader of the log can see what the file WAS, and ``git show``
brings it back.

Polarity, stated because it is the inverse of the project store: the
project store is an ALLOW-LIST (``*`` first, then ``!`` exceptions),
because a project folder holds pipeline output where binaries are
byproducts.  The shared store is a DENY-LIST - version everything
except machine droppings - because here the binaries ARE the record.

A manifest covers ONE asset directory (``brand-assets/motion/`` has
its own; a future ``stills/`` gets its own) and lives inside it, so
no grand index has to change when a folder gains a file.  A plan
that places a manifest-covered asset reports drift the way the
frame-rate conform is reported (``full_frame_element``: on the card
and on stderr, never a gate) - ``library_note_for_asset`` is that
reader, and a manifest nothing reads would be the same defect this
closes.

    python3 -m library.tools.brand_library --init <shared_dir>
    python3 -m library.tools.brand_library --write-manifest <shared_dir>
    python3 -m library.tools.brand_library --verify <shared_dir>

``docs/BRAND_LIBRARY_STORE.md`` is the full record: the selection,
the layout, and the exact commands the store is brought under
version control with.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import subprocess
import sys

#: What makes a directory a series-shared store: the folder the
#: projects beside it all point into.  Recognised by what it holds,
#: never by its name - a store renamed is still a store.
ASSET_DIRNAME = "brand-assets"

#: The integrity record's filename.  One per asset directory, living
#: inside the directory it covers.
MANIFEST_FILENAME = "MANIFEST.json"

#: The asset directory this module's commands default to.  A store
#: with more than one names the other explicitly.
DEFAULT_ASSET_SUBDIR = os.path.join(ASSET_DIRNAME, "motion")

#: What a listed file IS, in PR 1181's own audit language.  One
#: enumeration and an unknown name raises: a role the tooling does
#: not know is curation nobody can query.  ``asset`` is the default
#: for a file nobody has classified - the manifest's job is
#: integrity first, and an unclassified file with a hash is still
#: restorable.
ROLES = ("master", "conform", "candidate", "evidence", "asset")

#: The manifest schema this writer reads and writes.  A manifest
#: carrying a newer schema is refused, not rewritten: downgrading
#: what a newer writer recorded would silently drop fields.
SCHEMA_VERSION = 1

_CHUNK_BYTES = 1024 * 1024


class BrandLibraryError(ValueError):
    """A shared-store path, manifest or verification this module refuses."""


class NotASharedStore(BrandLibraryError):
    """The directory holds no ``brand-assets/`` - nothing here says a
    series shares it, so initialising version control would be
    versioning an arbitrary folder."""


class UnknownRole(BrandLibraryError):
    """A manifest entry names a role outside :data:`ROLES`."""


class ManifestDrift(BrandLibraryError):
    """A manifest-covered file is missing or changed on disk.

    Raised only by :func:`require_clean` - the strict surface.  The
    plan-time reader reports instead of raising, because a report
    nothing can act on mid-plan is a gate, and this channel never
    gates.
    """


def is_shared_store(path: str) -> bool:
    """Whether this directory is a series-shared store."""
    return os.path.isdir(os.path.join(path or "", ASSET_DIRNAME))


def _git(shared_dir: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=shared_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,  # callers judge returncode; a throw would lie
    )


def gitignore_body() -> str:
    """Render the shared store's deny-list .gitignore.

    The inverse polarity of the project store, on purpose (see the
    module docstring): machine droppings are ignored, everything
    else - masters, conforms, review copies, READMEs, the manifest
    itself - is the record and is versioned.
    """
    return (
        "# Generated by library/tools/brand_library.py - do not hand-edit.\n"
        "# A DENY-LIST, not an allow-list: the binaries ARE the record\n"
        "# here, so everything is versioned except machine droppings.\n"
        ".DS_Store\n"
        "Thumbs.db\n"
        "*~\n"
        "*.tmp\n"
    )


def init_shared_repo(shared_dir: str) -> dict:
    """``git init`` plus the deny-list .gitignore for a shared store.

    Purely additive - a ``.git`` directory and a ``.gitignore`` - and
    an explicit act: without it every other command in this module
    declines with reason ``no-repo`` rather than initialising
    unasked, the same rule ``init_project_repo`` keeps.  Nothing is
    staged and nothing is committed here: the first commit is the
    captain's, made with the commands this returns under ``next``.
    """
    if not is_shared_store(shared_dir):
        raise NotASharedStore(
            f"{shared_dir!r} holds no {ASSET_DIRNAME!r} directory - "
            f"a shared store is recognised by what it holds, and "
            f"there is nothing here a series shares. Refusing to "
            f"version an arbitrary folder.")
    git_dir = os.path.join(shared_dir, ".git")
    created = not os.path.exists(git_dir)
    if created:
        proc = _git(shared_dir, "init")
        if proc.returncode != 0:
            return {"initialised": False,
                    "reason": proc.stderr.strip()[-400:]}
    ignore = os.path.join(shared_dir, ".gitignore")
    with open(ignore, "w", encoding="utf-8") as fh:
        fh.write(gitignore_body())
    # A repo-local identity so the store's history never depends on -
    # or alters - the captain's global git config.  Same rule as the
    # project store's init.
    cfg = _git(shared_dir, "config", "--local", "user.name")
    if not cfg.stdout.strip():
        _git(shared_dir, "config", "--local", "user.name",
             "firstmate build recorder")
    cfg = _git(shared_dir, "config", "--local", "user.email")
    if not cfg.stdout.strip():
        _git(shared_dir, "config", "--local", "user.email",
             "firstmate@localhost")
    manifest = os.path.join(shared_dir, DEFAULT_ASSET_SUBDIR,
                            MANIFEST_FILENAME)
    return {
        "initialised": True,
        "created": created,
        "gitignore": ignore,
        "next": [
            f"python3 -m library.tools.brand_library "
            f"--write-manifest {shared_dir}",
            f"git -C {shared_dir} add -A",
            f"git -C {shared_dir} commit -m "
            f"'brand library: initial store record'",
            f"python3 -m library.tools.brand_library "
            f"--verify {shared_dir}",
        ],
        "manifest": manifest,
    }


def _sha256(path: str) -> tuple[str, int]:
    """The whole file's digest and size.

    Whole-file, not the footage fingerprint's two-mebibyte window:
    these are masters of tens of megabytes, not multi-gigabyte
    camera files, and an edit to a conform's middle that keeps its
    length is exactly the overwrite this record exists to catch.
    Verification re-hashes one covered file per planned card, which
    is a tenth of a second - not a reason to weaken the record.
    """
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _default_measure(path: str) -> dict:
    """Measure a brand file through the engine's own instrument.

    :func:`brand_motion.measure_source` is the one reading of these
    files - one measurement, not two, so the manifest and the
    renderer cannot disagree about what a file is.  Imported late:
    ``brand_motion`` pulls in the overlay measurement stack, which
    this module only needs when it actually measures.
    """
    from library.tools import brand_motion as bm
    source = bm.measure_source(path)
    return {
        "width": source.width,
        "height": source.height,
        "fps": source.fps,
        "frames": source.frame_count,
        "duration_seconds": source.duration_seconds,
        "container": source.container,
        "video_codec": source.video_codec,
        "has_audio": source.has_audio,
    }


def _manifest_file(shared_dir: str, asset_subdir: str) -> str:
    return os.path.join(shared_dir, asset_subdir, MANIFEST_FILENAME)


def _load_existing(manifest_file: str) -> dict:
    """The current manifest's curated fields, keyed by relpath.

    A present-but-unparseable manifest raises rather than being
    overwritten: failing the refresh is how a hand-edit mistake gets
    found, while silently rewriting would bless it.
    """
    if not os.path.isfile(manifest_file):
        return {}
    try:
        with open(manifest_file, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise BrandLibraryError(
            f"manifest {manifest_file!r} cannot be read "
            f"({exc}) - fix or remove it by hand; the writer will "
            f"not overwrite what it cannot parse.") from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA_VERSION:
        raise BrandLibraryError(
            f"manifest {manifest_file!r} carries schema "
            f"{data.get('schema') if isinstance(data, dict) else '?'}; "
            f"this writer reads schema {SCHEMA_VERSION}. A newer "
            f"writer's fields must not be silently dropped.")
    files = data.get("files")
    if not isinstance(files, dict):
        raise BrandLibraryError(
            f"manifest {manifest_file!r} has no files mapping - "
            f"fix it by hand; the writer will not overwrite it.")
    return files


def scan_declarations(store_root: str, asset_subdir: str,
                      relpaths: list,
                      project_yaml_paths: list) -> dict:
    """Which declarations point at each listed file.

    Reads each project.yaml as TEXT and matches the asset's absolute
    path verbatim - the same verbatim rule the project store keeps
    for committed bytes: rewriting paths would make the record differ
    from what the pipeline read.  An unreadable yaml is REPORTED
    under ``unreadable``, never raised: a project folder mid-build
    must not fail the store's own record.
    """
    declared: dict = {rel: [] for rel in relpaths}
    unreadable: list = []
    for yaml_path in project_yaml_paths or []:
        try:
            with open(yaml_path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            unreadable.append(yaml_path)
            continue
        for rel in relpaths:
            absolute = os.path.join(store_root, asset_subdir, rel)
            if absolute in text:
                declared[rel].append(yaml_path)
    return {"declared_by": declared, "unreadable": unreadable}


def write_manifest(shared_dir: str,
                   asset_subdir: str = DEFAULT_ASSET_SUBDIR,
                   measure=None,
                   project_yaml_paths: tuple | list = ()) -> dict:
    """Write (or refresh) the integrity record for one asset directory.

    Every non-dotfile in the directory gets an entry: sha256, bytes,
    and the measurement when the file admits one.  A file that
    admits no measurement (a README, an eye-test mp4 the measurer
    declines) still gets its hash - integrity first, description
    where available - with the reason recorded under
    ``unmeasured`` rather than left silent.

    Refreshing preserves curation: role, derived_from, recipe, note
    and declared_by survive per relpath, so a re-measure after a new
    conform lands does not clobber what the captain stated.  A
    preserved role outside :data:`ROLES` raises - a malformed
    declaration raises rather than being defaulted back.
    """
    if not is_shared_store(shared_dir):
        raise NotASharedStore(
            f"{shared_dir!r} holds no {ASSET_DIRNAME!r} directory.")
    asset_dir = os.path.join(shared_dir, asset_subdir)
    if not os.path.isdir(asset_dir):
        raise BrandLibraryError(
            f"asset directory {asset_dir!r} is not a directory - "
            f"nothing to record.")
    measure = measure or _default_measure
    manifest_file = os.path.join(asset_dir, MANIFEST_FILENAME)
    previous = _load_existing(manifest_file)
    names = sorted(name for name in os.listdir(asset_dir)
                   if not name.startswith(".")
                   and name != MANIFEST_FILENAME
                   and os.path.isfile(os.path.join(asset_dir, name)))
    declarations = scan_declarations(
        shared_dir, asset_subdir, names, list(project_yaml_paths or []))
    files: dict = {}
    preserved = 0
    new = 0
    for name in names:
        path = os.path.join(asset_dir, name)
        digest, size = _sha256(path)
        entry: dict = {"sha256": digest, "bytes": size}
        try:
            entry["measured"] = measure(path)
        except Exception as exc:  # noqa: BLE001 - per-file tolerance
            # One unmeasurable file must not fail the store's whole
            # record; the reason is carried on the entry, where a
            # reader of the manifest can see what was not proven.
            entry["unmeasured"] = f"{exc!r}"[:400]
        old = previous.get(name)
        if old is not None:
            preserved += 1
            role = old.get("role", "asset")
            if role not in ROLES:
                raise UnknownRole(
                    f"manifest entry {name!r} names role {role!r}, "
                    f"outside {', '.join(ROLES)} - fix it by hand; "
                    f"the writer will not default it back.")
            for key in ("role", "derived_from", "recipe", "note"):
                if old.get(key) is not None:
                    entry[key] = old[key]
            if project_yaml_paths:
                entry["declared_by"] = declarations["declared_by"][name]
            elif old.get("declared_by") is not None:
                entry["declared_by"] = old["declared_by"]
        else:
            new += 1
            entry["role"] = "asset"
            if project_yaml_paths:
                entry["declared_by"] = declarations["declared_by"].get(
                    name, [])
        files[name] = entry
    stamped = datetime.datetime.now(datetime.timezone.utc).isoformat()
    data = {
        "schema": SCHEMA_VERSION,
        "writer": "library/tools/brand_library.py",
        "store": shared_dir,
        "asset_dir": asset_subdir,
        "written_utc": stamped,
        "files": files,
    }
    with open(manifest_file, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
    report: dict = {"manifest": manifest_file, "files": sorted(files),
                    "preserved": preserved, "new": new}
    if declarations.get("unreadable"):
        report["unreadable_yaml"] = declarations["unreadable"]
    return report


def read_manifest(manifest_file: str) -> dict:
    """Read and validate a manifest, or refuse it by name."""
    files = _load_existing(manifest_file)
    with open(manifest_file, encoding="utf-8") as fh:
        data = json.load(fh)
    for rel, entry in files.items():
        role = entry.get("role", "asset") if isinstance(entry, dict) else None
        if role not in ROLES:
            raise UnknownRole(
                f"manifest {manifest_file!r} entry {rel!r} names role "
                f"{role!r}, outside {', '.join(ROLES)}.")
        if not isinstance(entry, dict) or not entry.get("sha256"):
            raise BrandLibraryError(
                f"manifest {manifest_file!r} entry {rel!r} carries no "
                f"digest - an integrity record without one is a "
                f"comment, not a record.")
    data["files"] = files
    return data


def verify_manifest(shared_dir: str,
                    asset_subdir: str = DEFAULT_ASSET_SUBDIR,
                    manifest: dict | None = None) -> dict:
    """Check the covered files against the manifest.  Never raises.

    Identity is sha256 plus bytes; a file whose bytes match is clean
    WITHOUT re-measuring, on purpose - re-measuring would need
    ffprobe on every plan and would turn an ffprobe version skew
    into a false drift.  The measurement in the manifest is
    description (what the file is); the digest is identity (which
    file it is).  Files on disk the manifest never saw are REPORTED
    under ``untracked``, never refused: a new conform lands before
    its manifest refresh, and landing new work is not drift.
    """
    manifest_file = _manifest_file(shared_dir, asset_subdir)
    if manifest is None:
        if not os.path.isfile(manifest_file):
            return {"verified": False, "reason": "no-manifest",
                    "manifest": manifest_file, "mismatches": [],
                    "untracked": []}
        try:
            manifest = read_manifest(manifest_file)
        except BrandLibraryError as exc:
            return {"verified": False, "reason": f"unreadable: {exc}",
                    "manifest": manifest_file, "mismatches": [],
                    "untracked": []}
    asset_dir = os.path.join(shared_dir, asset_subdir)
    recorded = manifest.get("files") or {}
    mismatches: list = []
    for rel in sorted(recorded):
        path = os.path.join(asset_dir, rel)
        entry = recorded[rel] if isinstance(recorded[rel], dict) else {}
        if not os.path.isfile(path):
            mismatches.append({"path": rel, "kind": "missing",
                               "expected": entry.get("sha256")})
            continue
        try:
            digest, size = _sha256(path)
        except OSError as exc:
            mismatches.append({"path": rel, "kind": "unreadable",
                               "reason": f"{exc}"[:200]})
            continue
        if (digest != entry.get("sha256")
                or size != entry.get("bytes")):
            mismatches.append({"path": rel, "kind": "changed",
                               "expected": entry.get("sha256"),
                               "found": digest})
    try:
        on_disk = set(name for name in os.listdir(asset_dir)
                      if not name.startswith(".")
                      and name != MANIFEST_FILENAME
                      and os.path.isfile(os.path.join(asset_dir, name)))
    except OSError:
        on_disk = set()
    untracked = sorted(on_disk - set(recorded))
    return {"verified": not mismatches,
            "manifest": manifest_file,
            "checked": len(recorded),
            "mismatches": mismatches,
            "untracked": untracked}


def require_clean(report: dict) -> None:
    """Raise on a verification that found drift.  The strict surface.

    Missing or changed files raise :class:`ManifestDrift` naming
    what moved; untracked files never raise - they are new work
    awaiting a manifest refresh, and refusing new work would make
    the record the enemy of the authoring it exists to protect.  A
    store with no manifest (reason ``no-manifest``) raises too: the
    strict surface is chosen exactly when the record must exist.
    """
    if report.get("verified"):
        return
    if report.get("reason") == "no-manifest":
        raise ManifestDrift(
            f"no manifest at {report.get('manifest')!r} - the store "
            f"is not under version control yet; write one with "
            f"--write-manifest before requiring it clean.")
    if report.get("reason"):
        raise ManifestDrift(str(report["reason"]))
    first = (report.get("mismatches") or [{}])[0]
    rest = len(report.get("mismatches") or []) - 1
    raise ManifestDrift(
        f"brand library drift: {first.get('path')!r} is "
        f"{first.get('kind')} "
        f"(manifest {report.get('manifest')!r}"
        f"{f'; plus {rest} more' if rest > 0 else ''}). "
        f"Restore it with git before planning over it - a reel "
        f"built on a moved master bakes the move into Resolve.")


def _covering_manifest(asset_path: str) -> tuple[str, str] | None:
    """The nearest MANIFEST.json above the asset, with the relpath.

    None when no manifest covers the file - the pre-existing state
    of a store not yet under version control, which is ordinary, not
    drift.  A manifest that does not actually contain the asset's
    directory is ignored the same way: proximity is not coverage.
    """
    directory = os.path.dirname(os.path.abspath(asset_path or ""))
    previous = ""
    while directory and directory != previous:
        candidate = os.path.join(directory, MANIFEST_FILENAME)
        if os.path.isfile(candidate):
            try:
                rel = os.path.relpath(os.path.abspath(asset_path),
                                      directory)
            except ValueError:
                return None
            if rel.startswith(".." + os.sep) or rel == "..":
                return None
            return candidate, rel
        previous = directory
        directory = os.path.dirname(directory)
    return None


def library_note_for_asset(asset_path: str) -> str:
    """What the shared store says about this file, or "".

    The plan-time reader: a missing manifest-covered file, a file
    whose bytes moved, or a file no manifest ever saw each get one
    SAYABLE line, and everything else - no store, no manifest, a
    clean record, an unreadable manifest - gets silence.  NEVER
    RAISES: a report channel that breaks planning is worse than no
    channel, and the strict surface is ``--verify`` plus
    :func:`require_clean`.
    """
    try:
        found = _covering_manifest(asset_path)
        if found is None:
            return ""
        manifest_file, rel = found
        try:
            manifest = read_manifest(manifest_file)
        except BrandLibraryError:
            return ""
        recorded = manifest.get("files") or {}
        if rel not in recorded:
            return (
                f"brand library: {os.path.basename(asset_path)!r} is "
                f"not in {manifest_file!r} - a file with no integrity "
                f"record. Refresh the manifest before relying on it.")
        entry = recorded[rel] if isinstance(recorded[rel], dict) else {}
        if not os.path.isfile(asset_path):
            return (
                f"brand library: {rel!r} is in the manifest but "
                f"missing on disk - restore it with git before "
                f"planning over it.")
        digest, size = _sha256(asset_path)
        if digest != entry.get("sha256") or size != entry.get("bytes"):
            return (
                f"brand library: {rel!r} changed on disk since the "
                f"manifest was written - restore it with git or "
                f"refresh the manifest before planning over it.")
        return ""
    except Exception:  # noqa: BLE001 - the never-raises contract
        return ""


def main(argv: list | None = None) -> int:
    """The three commands from the module docstring."""
    args = list(sys.argv[1:] if argv is None else argv)
    asset_subdir = DEFAULT_ASSET_SUBDIR
    yamls: list = []
    rest: list = []
    index = 0
    while index < len(args):
        if args[index] == "--asset-subdir" and index + 1 < len(args):
            asset_subdir = args[index + 1]
            index += 2
        elif args[index] == "--project-yaml" and index + 1 < len(args):
            yamls.append(args[index + 1])
            index += 2
        else:
            rest.append(args[index])
            index += 1
    if len(rest) == 2 and rest[0] == "--init":
        try:
            result = init_shared_repo(rest[1])
        except BrandLibraryError as exc:
            print(f"brand_library: refused: {exc}", file=sys.stderr)
            return 2
        if not result.get("initialised"):
            print(f"brand_library: refused: {result.get('reason')}",
                  file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if len(rest) == 2 and rest[0] == "--write-manifest":
        try:
            result = write_manifest(rest[1], asset_subdir=asset_subdir,
                                    project_yaml_paths=yamls)
        except BrandLibraryError as exc:
            print(f"brand_library: refused: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if len(rest) == 2 and rest[0] == "--verify":
        report = verify_manifest(rest[1], asset_subdir=asset_subdir)
        print(json.dumps(report, indent=2, sort_keys=True))
        if report.get("untracked"):
            print(f"brand_library: {len(report['untracked'])} file(s) "
                  f"not in the manifest - refresh it with "
                  f"--write-manifest", file=sys.stderr)
        if report.get("verified"):
            return 0
        if report.get("reason") == "no-manifest":
            print(f"brand_library: refused: {report['manifest']!r} "
                  f"does not exist - write one with --write-manifest "
                  f"first", file=sys.stderr)
            return 2
        for mismatch in report.get("mismatches", []):
            print(f"brand_library: drift: {mismatch.get('path')!r} is "
                  f"{mismatch.get('kind')}", file=sys.stderr)
        return 3
    print("usage: python3 -m library.tools.brand_library "
          "--init <shared_dir> | --write-manifest <shared_dir> "
          "[--asset-subdir <dir>] [--project-yaml <yaml>]... | "
          "--verify <shared_dir> [--asset-subdir <dir>]",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
