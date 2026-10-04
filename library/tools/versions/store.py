"""The STORE: per-project version control for pipeline data (AGENTS.md 3: run state).

The substrate of the version model (`library/tools/versions/__init__.py`).

A project folder gets its own git repo holding the diffable TEXT
pipeline record: declarations, run state, step outputs, the assembly
manifest, generated Fusion comps, the per-build timeline record,
prompts, responses, gates, review notes, marker pulls, provenance and
the two generated audit documents. It also holds a committed `.drt`
recovery archive for each promoted reel. The text remains the source
of truth for review and merging; `.drt` files are recovery artefacts,
never inputs to diff or merge decisions.

The 2026-09-10 ruling was revised by the captain on 2026-09-21:
approved reels may be exported to `.drt` as a recovery artefact after
their Fusion-comp text exports land. Timeline-level merge and `.drp`
surgery remain closed. The Resolve project stays a derived artefact;
the `.drt` is the recovery exception, not the version record.

1. `init_project_repo` writes the allow-list `.gitignore` and runs
   `git init`.  The ignore is an ALLOW-LIST, not a deny-list: `*`
   first, then `!` exceptions for the text record.  A new binary
   directory somebody adds tomorrow is ignored by default, which is
   what keeps the 599 MB caption renders seen on 2026-09-10 out of
   the repo.  `tests/unit/reels/test_version_rounds.py` pins this with a
   binary directory the module never names.
2. `record_finished_timeline` runs at the end of step 6.01, AFTER
   comps and grade, and writes the machine-made timeline export
   beside the renderer: a final OTIO of the FINISHED timeline (the
   mix path's `.delivered.otio` only exists when levels were
   delivered, so a build that set nothing has no export without
   this), the serializer read, and a BUILD-RECORD note.  The OTIO
   comes back with Fusion comps empty and CDL grades absent, so a
   per-build record that silently omits the look is this project's
   recurring failure - the blind spot is named IN THE RECORD, and
   the versioned `.comp` files committed in the same commit are the
   complementary record of what the picture carries.
3. `render_commit_message` formats what the run already recorded -
   mode, argv, profile, steps run and the restart classification
   from `pipeline_run.json`, completed/failed steps from the
   ledgers in `pipeline_data.json`, the run id from provenance -
   and invents no new metadata.  A line is omitted when its source
   is absent rather than filled in.
4. `stage_for_commit` stages the text record plus the explicitly named
   promoted-reel `.drt` recovery archives. The allow-list decides which
   PATHS are versioned; `TEXT_SUFFIXES` plus a content sniff decide
   which of those paths are committable text. A versioned binary
   (Resolve still PNGs and their unasked `.drx` sidecars under
   `marker_feedback/stills/`, evidence stills under `timeline_captures/`,
   anything binary a future step drops on a versioned path) is never
   staged, except for the `.drt` recovery archives: other binaries are
   recorded instead by
   path, size and sha256 in `BINARY_MANIFEST_REL`, a text file
   that IS committed, so a regenerated binary can be checked
   against its hash with `verify_binary_manifest`. A binary tracked
   before this rule existed is `rm --cached` (the working
   tree is untouched, history is untouched) and joins the
   manifest. D7 (2026-09-23): other binaries are recorded by hash
   and regenerated.

Machine-local absolute paths are COMMITTED VERBATIM, deliberately:
the repo is checkout-and-rebuild, so rewriting paths at commit time
would make the checked-out copy differ from what the pipeline wrote
and break the rebuild the repo exists for.  Paths are stable on the
captain's single build machine; a project that moves machines
accepts the one-time diff.  `test_committed_bytes_are_verbatim`
pins the no-rewrite half of that decision.

Setup is explicit and per project: `init_project_repo` once, then
every build calls `commit_build`, which stages the allow-list and
commits - or reports `committed: False` with reason `clean` when
the build changed nothing, so a no-op build produces no spurious
commit.  Without an initialised repo the per-build hook declines
with reason `no-repo` rather than initialising unasked: adding
version control to the captain's project directory is purely
additive (a `.git` directory, a `.gitignore`, commits), and it
stays an explicit act.
"""

from __future__ import annotations

from library.tools.resolve_lock import under_lease

import datetime
import hashlib
import json
import os
import subprocess
from pathlib import Path

# ── The allow-list ──────────────────────────────────────────────
# Project-relative paths that ARE versioned.  Everything else -
# rendered overlays, audio caches, acquired media, thumbnails, QA
# frames, finished renders, scratch/, quarantine contents beyond the
# mark and sweep records - is ignored by the leading `*`.
#
# Written to .gitignore by init_project_repo.  A directory needs its
# own `!` line AND a `/**` line: the first lets git descend, the
# second un-ignores what is inside.
ALLOW_LIST = [
    # The allow-list itself, so a change to what is versioned is reviewed.
    "/.gitignore",
    # Stable declarations the captain owns. Rebuildable supplied state
    # under external/state/ is intentionally ignored and never merged.
    "/project.yaml",
    "/external/",
    "/external/declarations/",
    "/external/declarations/**",
    "/context/",
    "/context/**",
    "/profiles/",
    "/profiles/**",
    # Run state at the project root.
    "/pipeline_data.json",
    "/pipeline_run.json",
    # What the run learned back: the captain's corrections, fixed
    # mistakes and settled decisions (`library/tools/learned_context.py`
    # writes exactly one file, `learnings.json`, one JSON list, UTF-8).
    # Named, not whole-directory - the way the generator already narrows
    # quarantine/ to its mark and sweep records - so a crash-leftover
    # `.learnings.*.tmp` beside it stays ignored with every other binary.
    # Read by `transcript_corrections`, `project_context`, `reel_build`,
    # `reel_conformance_verifier`, `layer_coherence` and `captain_edits`:
    # a keep exclusion is data that controls the edit, and data that
    # controls the edit is what this store exists to version.
    "/learned_context/",
    "/learned_context/learnings.json",
    # What each step decided: every step's output.json and summary.md,
    # the assembly manifest, generated Fusion comps, and the per-build
    # timeline record (otio/).  `*` is one path component, so these
    # hold for whatever step directory a future step adds.
    "/pipeline_output/",
    "/pipeline_output/steps/",
    "/pipeline_output/steps/*/",
    "/pipeline_output/steps/*/output.json",
    "/pipeline_output/steps/*/summary.md",
    "/pipeline_output/steps/*/assembly_manifest.json",
    "/pipeline_output/steps/*/fusion_comps/",
    "/pipeline_output/steps/*/fusion_comps/**",
    "/pipeline_output/steps/*/otio/",
    "/pipeline_output/steps/*/otio/**",
    # Prompts, responses, gates, review, marker pulls, provenance.
    "/pipeline_output/llm_requests/",
    "/pipeline_output/llm_requests/**",
    "/pipeline_output/llm_responses/",
    "/pipeline_output/llm_responses/**",
    "/pipeline_output/gates/",
    "/pipeline_output/gates/**",
    "/pipeline_output/review/",
    "/pipeline_output/review/**",
    "/marker_feedback/",
    "/marker_feedback/**",
    # Hand-edit evidence moved out of the pipeline repo (its captures/
    # drop zone held the captain's live timeline state until it was
    # moved here, beside the reels it describes).  Text state,
    # transcripts, measurements and the inventory - plus the two stills
    # that are the evidence behind the positioning findings.  Named
    # evidence, not renders, which is why those two frames are
    # versioned while every other binary stays out.
    "/timeline_captures/",
    "/timeline_captures/**",
    "/pipeline_output/provenance/",
    "/pipeline_output/provenance/**",
    # Quarantine: the mark and sweep records only, never the moved
    # media beside them (library/tools/caption_asset_gc.py names
    # mark_<stamp>.json/.md and sweep_<stamp>_manifest.md).
    "/pipeline_output/quarantine/",
    "/pipeline_output/quarantine/mark_*.json",
    "/pipeline_output/quarantine/mark_*.md",
    "/pipeline_output/quarantine/sweep_*_manifest.md",
    # The two generated audit documents
    # (library/tools/run_traceback.py).
    "/pipeline_output/RUN-TRACEBACK.md",
    "/pipeline_output/ARTIFACTS.md",
]

GITIGNORE_HEADER = (
    "# Generated by library/tools/versions/store.py - do not hand-edit.\n"
    "# An ALLOW-LIST, not a deny-list: everything is ignored except the\n"
    "# text pipeline record and promoted-reel .drt recovery archives.\n"
    "# A new binary directory is ignored by default, keeping renders,\n"
    "# caches and scratch out.\n"
)

# Only Resolve timeline exports at this exact project-relative location
# are committed as binary recovery artefacts. Every other binary still
# goes to the hash manifest and stays out of Git.
DRT_RECOVERY_DIRNAME = "recovery_archives"

# What the OTIO export cannot see, named IN THE RECORD rather than in
# a docstring.  {} takes the fusion_comps directory, project-relative.
BLIND_SPOT_TEXT = (
    "What this export cannot see: Resolve's OTIO comes back with Fusion "
    "comps empty and CDL grades absent, so this file describes the cut, "
    "the timing and the mix - never the look. The generated Fusion .comp "
    "files under {comps_rel}, committed in the same commit, are the "
    "complementary record of what the picture carries."
)


def gitignore_body() -> str:
    """Render the allow-list .gitignore."""
    return GITIGNORE_HEADER + "*\n" + "".join(f"!{p}\n" for p in ALLOW_LIST)


# ── Text-first staging (D7, with the approved `.drt` exception) ────
# The allow-list above decides which PATHS are versioned.  This
# decides which of those paths are committable TEXT: the suffix must
# be a known text type (or absent, as in `.gitignore`), AND the content
# must sniff as text. The sole binary exception is the approved-reel
# `.drt` recovery archive; every other versioned binary goes to the
# manifest, never the repo.
TEXT_SUFFIXES = frozenset({
    ".json", ".jsonl", ".md", ".yaml", ".yml", ".otio", ".comp", ".txt",
})

# Committed, on the allow-list via /pipeline_output/provenance/**.
BINARY_MANIFEST_REL = "pipeline_output/provenance/binary_manifest.json"

_SNIFF_BYTES = 8192


def _sniffs_as_text(path: Path) -> bool:
    """True when the file's leading bytes read as text.

    NUL or undecodable UTF-8 means binary - the same shape as git's
    own binary heuristic, deliberately strict: a corrupt `.json`
    carrying NULs is a binary and joins the manifest rather than
    landing as a blob.
    """
    try:
        with open(path, "rb") as fh:
            chunk = fh.read(_SNIFF_BYTES)
    except OSError:
        return False
    if not chunk:
        return True
    if b"\x00" in chunk:
        return False
    try:
        chunk.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def is_committable_text(path: Path) -> bool:
    """Whether this versioned path may enter the repo as a blob."""
    suffix = path.suffix.lower()
    if suffix and suffix not in TEXT_SUFFIXES:
        return False
    return _sniffs_as_text(path)


def is_drt_recovery_archive(path: Path, project_root: Path) -> bool:
    """Whether this is one of the explicitly committed reel recovery files."""
    try:
        relative = path.relative_to(project_root)
    except ValueError:
        return False
    return (relative.parent == Path("pipeline_output") / "review"
            / DRT_RECOVERY_DIRNAME
            and relative.suffix.lower() == ".drt")


def _recovery_archive_stem(timeline_name: str) -> str:
    safe = "".join(
        char if char.isalnum() or char in "-_." else "_"
        for char in timeline_name) or "timeline"
    digest = hashlib.sha256(timeline_name.encode("utf-8")).hexdigest()[:10]
    return f"{safe}--{digest}"


def _export_reel_recovery_archives(
        resolve, project, names: list[str], timelines: dict,
        fusion_comp_export: dict, project_folder: str) -> dict:
    """Export complete promoted reels to `.drt` after their text comps land.

    The export reads an existing timeline handle and writes a file. It
    does not create or switch timelines, so the final reel inventory is
    unchanged. Per-reel failures leave any earlier archive intact.
    """
    report: dict = {"reels": {}, "files": [], "errors": []}
    comp_reels = fusion_comp_export.get("reels") or {}
    try:
        from library.tools.project_layout import Area, ProjectLayout

        layout = ProjectLayout(project_folder)
        archive_dir = (layout.write_dir(Area.REVIEW)
                       / DRT_RECOVERY_DIRNAME)
        archive_dir.mkdir(parents=True, exist_ok=True)
        scratch_dir = layout.write_dir(Area.SCRATCH)
    except Exception as exc:  # noqa: BLE001 - archive is best effort
        for name in names:
            reason = f"archive directory unavailable: {exc!r}"
            report["reels"][name] = {"archived": False, "reason": reason}
            report["errors"].append(f"{name}: {reason}")
        return report

    export_flag = getattr(resolve, "EXPORT_DRT", None)
    for name in names:
        result = {"archived": False}
        report["reels"][name] = result
        comp_record = comp_reels.get(name)
        if comp_record is None:
            reason = "Fusion-comp text export did not report this reel"
        elif comp_record.get("errors"):
            reason = ("Fusion-comp text export was incomplete: "
                      + "; ".join(str(e) for e in comp_record["errors"]))
        else:
            comp_files = [Path(path) for path in
                          (comp_record.get("files") or [])]
            expected_comp_dir = (Path(project_folder) / "pipeline_output"
                                 / "steps" / "7_01_build_reels"
                                 / "fusion_comps")
            if (comp_record.get("items_with_comps", 0) and not comp_files):
                reason = "Fusion comps exist but no text exports landed"
            elif any(path.parent != expected_comp_dir
                     or path.suffix != ".comp"
                     or not path.is_file()
                     or not is_committable_text(path)
                     for path in comp_files):
                reason = "Fusion-comp text export is missing or unreadable"
            elif name not in timelines:
                reason = "promoted timeline handle is unavailable"
            elif export_flag is None:
                reason = "Resolve does not expose EXPORT_DRT"
            else:
                reason = ""
        if reason:
            result["reason"] = reason
            report["errors"].append(f"{name}: {reason}")
            continue

        destination = archive_dir / f"{_recovery_archive_stem(name)}.drt"
        try:
            import tempfile

            from library.tools.resolve_deadline import call_with_deadline

            # Scratch is ignored by the project store. Export there first,
            # then atomically replace the current per-reel archive only
            # after Resolve returned success and left non-empty bytes.
            with tempfile.TemporaryDirectory(
                    prefix="drt-recovery-", dir=scratch_dir) as temporary:
                staged = Path(temporary) / destination.name
                exported = call_with_deadline(
                    f"DRT recovery export for {name}",
                    timelines[name].Export, str(staged), export_flag,
                    timeout_s=30.0)
                if not exported or not staged.is_file():
                    raise RuntimeError(
                        f"Timeline.Export returned {exported!r} or left "
                        "no .drt file")
                if staged.stat().st_size == 0:
                    raise RuntimeError("Resolve left an empty .drt file")
                os.replace(staged, destination)
            result.update({"archived": True, "path": str(destination)})
            report["files"].append(str(destination))
        except Exception as exc:  # noqa: BLE001 - preserve promotion
            reason = f"DRT export failed: {exc!r}"
            result["reason"] = reason
            report["errors"].append(f"{name}: {reason}")
    report["files"].sort()
    return report


def _versioned_files(root: Path) -> list[Path]:
    """Every file the allow-list versions, as absolute paths.

    Enumerated from ALLOW_LIST itself (so the set cannot drift from
    the ignore file `init_project_repo` writes), then filtered
    through `git check-ignore` so git-ignored files - crash
    leftovers, the quarantine media beside its records - never
    qualify.  Sorted for deterministic manifests.
    """
    candidates: set[Path] = set()
    for pattern in ALLOW_LIST:
        for match in root.glob(pattern.lstrip("/")):
            if match.is_dir():
                candidates.update(
                    p for p in match.rglob("*") if p.is_file())
            elif match.is_file():
                candidates.add(match)
    candidates = {p for p in candidates
                  if ".git" not in p.relative_to(root).parts}
    if not candidates:
        return []
    # The paths go over stdin, NUL-separated: git refuses `--stdin`
    # beside pathspec arguments, and a live project's candidates
    # (every file under every step directory) overflow argv anyway.
    rels = [str(p.relative_to(root)) for p in sorted(candidates)]
    proc = git(str(root), "check-ignore", "-z", "--stdin",
               input="\0".join(rels) + "\0")
    # 0: some ignored, 1: none ignored; anything else is a failure,
    # and an unfiltered set would hash every render as "versioned".
    if proc.returncode not in (0, 1):
        raise RuntimeError(
            f"git check-ignore failed: {proc.stderr.strip()[-400:]}")
    ignored = {p for p in proc.stdout.split("\0") if p}
    return sorted(
        p for p in candidates
        if str(p.relative_to(root)) not in ignored)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def binary_manifest_entries(project_folder: str) -> list[dict]:
    """Path/size/sha256 for every versioned binary except committed DRTs.

    Sorted by path.  Only binaries under VERSIONED paths qualify -
    renders, scratch and quarantine media are ignored files, not
    versioned binaries, so hashing them (gigabytes per build) is never
    attempted. `.drt` recovery archives are committed directly and do
    not also enter this manifest.
    """
    root = Path(project_folder)
    entries = []
    for path in _versioned_files(root):
        if is_drt_recovery_archive(path, root):
            continue
        if is_committable_text(path):
            continue
        rel = str(path.relative_to(root))
        try:
            size = path.stat().st_size
        except OSError:
            continue
        entries.append({"path": rel, "size": size,
                        "sha256": _sha256(path)})
    return entries


def write_binary_manifest(project_folder: str,
                          entries: list[dict] | None = None) -> Path:
    """Write the manifest atomically.  Always written, even when empty:
    an empty manifest is the record that the build carried no binary."""
    root = Path(project_folder)
    if entries is None:
        entries = binary_manifest_entries(project_folder)
    document = {"generated_by": "library/tools/versions/store.py",
                "files": entries}
    path = root / BINARY_MANIFEST_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_suffix(".json.staged")
    try:
        staged.write_text(json.dumps(document, indent=2, sort_keys=True,
                                     ensure_ascii=False) + "\n",
                          encoding="utf-8")
        os.replace(staged, path)
    except BaseException:
        try:
            os.unlink(staged)
        except OSError:
            pass
        raise
    return path


def verify_binary_manifest(project_folder: str) -> dict:
    """Check the binaries on disk against the committed manifest.

    The D7 loop closed: record at commit time, regenerate any time,
    check here.  `mismatched` means the bytes changed without a
    commit recording them; `missing` means the binary is gone and
    must be regenerated before its hash can be checked.
    """
    root = Path(project_folder)
    manifest = root / BINARY_MANIFEST_REL
    try:
        document = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"checked": 0, "ok": [], "mismatched": [], "missing": [],
                "reason": "no-manifest"}
    report: dict = {"checked": 0, "ok": [], "mismatched": [],
                    "missing": []}
    for entry in document.get("files") or []:
        rel = entry.get("path", "")
        candidate = root / rel
        report["checked"] += 1
        if not candidate.is_file():
            report["missing"].append(rel)
        elif (_sha256(candidate) == entry.get("sha256")
              and candidate.stat().st_size == entry.get("size")):
            report["ok"].append(rel)
        else:
            report["mismatched"].append(rel)
    return report


def stage_for_commit(project_folder: str) -> dict:
    """The store's own staging: the ONLY staging the version model uses.

    Replaces every raw `git add -A` site (the audit named four on an
    earlier base; P2 routed three through `commit_build` and dropped
    the fourth, leaving the one inside `commit_build` - this
    function).  Fail-closed: an untracked binary stays unstaged via
    the allow-list ignore, a staged binary (tracked before this
    rule, or force-added) is `rm --cached` - the working tree is never
    touched. Promoted-reel `.drt` recovery archives are the one explicit
    binary exception; all other versioned binaries land in the manifest,
    which is itself staged.
    """
    root = Path(project_folder)
    report: dict = {"staged": True, "binaries_recorded": [],
                    "untracked": []}
    # Existing project repos were initialized with a broader external/
    # allow-list. Refresh it before collecting files so new state paths
    # stay out and declarations remain versioned.
    ignore = root / ".gitignore"
    body = gitignore_body()
    if not ignore.is_file() or ignore.read_text(encoding="utf-8") != body:
        ignore.write_text(body, encoding="utf-8")
    try:
        entries = binary_manifest_entries(project_folder)
    except RuntimeError as exc:
        report["staged"] = False
        report["reason"] = str(exc)
        return report
    write_binary_manifest(project_folder, entries)
    report["binaries_recorded"] = entries
    add = git(project_folder, "add", "-A")
    if add.returncode != 0:
        report["staged"] = False
        report["reason"] = f"git add failed: {add.stderr.strip()[-400:]}"
        return report
    from library.tools.external_inputs import CHECKS, declaration_stems

    listed = git(project_folder, "ls-files", "-z", "--", "external")
    if listed.returncode != 0:
        report["staged"] = False
        report["reason"] = ("git ls-files failed while excluding external "
                            f"state: {listed.stderr.strip()[-400:]}")
        return report
    declarations = declaration_stems()
    state_keys = set(CHECKS) - declarations
    state_paths = [
        path for path in listed.stdout.split("\x00") if path
        and (path.startswith("external/state/")
             or (path.startswith("external/")
                 and path.count("/") == 1
                 and Path(path).suffix.lower() == ".json"
                 and Path(path).stem in state_keys))
    ]
    if state_paths:
        untrack = git(project_folder, "rm", "--cached", "-q", "-f", "--",
                      *state_paths)
        if untrack.returncode != 0:
            report["staged"] = False
            report["reason"] = ("git rm --cached failed while excluding "
                                f"external state: {untrack.stderr.strip()[-400:]}")
            return report
        report["external_state_untracked"] = sorted(state_paths)
    staged = git(project_folder, "diff", "--cached", "--name-only",
                 "-z", "--diff-filter=AM")
    staged_paths = [p for p in staged.stdout.split("\x00") if p.strip()]
    binaries = [p for p in staged_paths
                if not is_committable_text(root / p)
                and not is_drt_recovery_archive(root / p, root)]
    if binaries:
        untrack = git(project_folder, "rm", "--cached", "-q", "--",
                      *binaries)
        if untrack.returncode != 0:
            report["staged"] = False
            report["reason"] = (
                f"git rm --cached failed: "
                f"{untrack.stderr.strip()[-400:]}")
            return report
        report["untracked"] = sorted(binaries)
    manifest_add = git(project_folder, "add", "--", BINARY_MANIFEST_REL)
    if manifest_add.returncode != 0:
        report["staged"] = False
        report["reason"] = (
            f"git add manifest failed: "
            f"{manifest_add.stderr.strip()[-400:]}")
    return report


def git(project_folder, *args: str,
        input: str | None = None) -> subprocess.CompletedProcess:
    """Run git in the project's store. The one git call of the version model.

    Never raises on a git failure: callers judge `returncode`, because a
    throw would lie about which half of a record landed.
    """
    return subprocess.run(
        ["git", *args],
        cwd=str(project_folder),
        input=input,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,  # callers judge returncode; a throw would lie
    )


def write_record(path, document, prefix: str) -> str:
    """Write a version record atomically: stage beside it, then replace.

    One spelling for every record the model keeps under
    `pipeline_output/review/` - sorted keys, indent 2, UTF-8 kept as
    written, a trailing newline - so a record diffs in the store like
    every other line. A half-written record would read as a version
    that is not there, so the write lands whole or not at all.
    """
    import tempfile

    path = str(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = json.dumps(dict(document), indent=2, sort_keys=True,
                         ensure_ascii=False) + "\n"
    handle, staged = tempfile.mkstemp(
        dir=os.path.dirname(path), prefix=prefix, suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(payload)
        os.replace(staged, path)
    except BaseException:
        try:
            os.unlink(staged)
        except OSError:
            pass
        raise
    return path


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def init_project_repo(project_folder: str) -> dict:
    """`git init` plus the allow-list .gitignore.  Purely additive.

    Writes nothing when the repo already exists except refreshing the
    generated .gitignore.  Never touches any other file.
    """
    root = Path(project_folder)
    git_dir = root / ".git"
    created = not git_dir.exists()
    if created:
        proc = git(project_folder, "init")
        if proc.returncode != 0:
            return {"initialised": False, "reason": proc.stderr.strip()[-400:]}
    ignore = root / ".gitignore"
    ignore.write_text(gitignore_body(), encoding="utf-8")
    # A repo-local identity so the per-build commit never depends on
    # - or alters - the captain's global git config.
    cfg = git(project_folder, "config", "--local", "user.name")
    if not cfg.stdout.strip():
        git(project_folder, "config", "--local", "user.name",
             "firstmate build recorder")
    cfg = git(project_folder, "config", "--local", "user.email")
    if not cfg.stdout.strip():
        git(project_folder, "config", "--local", "user.email",
             "firstmate@localhost")
    return {"initialised": True, "created": created,
            "gitignore": str(ignore)}


def _latest_run_id(project_folder: str) -> str:
    """The run id provenance most recently recorded, or ''."""
    try:
        import sys
        repo = Path(__file__).resolve().parents[2]
        if str(repo) not in sys.path:
            sys.path.insert(0, str(repo))
        from library.tools.provenance import Provenance
        runs = Provenance(project_folder).runs()
        if runs:
            return str(runs[-1].get("run_id", "") or "")
    except Exception:
        pass
    return ""


def render_commit_message(project_folder: str) -> str:
    """Render the commit message from what the run already recorded.

    Reads pipeline_run.json (mode, argv, profile, steps run, restart
    classification), pipeline_data.json (completed/failed ledgers) and
    provenance (run id).  A missing source omits its line; nothing is
    invented.
    """
    root = Path(project_folder)
    run = _read_json(root / "pipeline_run.json")
    state = _read_json(root / "pipeline_data.json")

    completed = sorted(set((state.get("preflight_completed") or {}).keys())
                       | set((state.get("edit_completed") or {}).keys()))
    # steps_to_run is what this invocation attempted, not what
    # completed - the ledgers above are the completed half.  Keep both
    # readings apart rather than merging them into one list.
    attempted = list(run.get("steps_to_run") or [])
    failed = list(state.get("failed_steps") or [])
    profile = run.get("profile") or {}
    profile_name = profile.get("name") or ""
    restart = run.get("restart")
    run_id = _latest_run_id(project_folder)

    what = (run.get("last_completed_step")
            or (attempted[0] if len(attempted) == 1 else ""))
    subject = f"build {what}".rstrip() if what else "build"
    status = run.get("status") or ""
    if status:
        subject += f" ({status})"

    lines = [subject, ""]
    if run.get("mode"):
        lines.append(f"mode: {run['mode']}")
    if run.get("argv"):
        lines.append(f"argv: {' '.join(str(a) for a in run['argv'])}")
    if profile_name:
        lines.append(f"profile: {profile_name}")
    if attempted:
        lines.append(f"steps run: {', '.join(attempted)}")
    if completed:
        lines.append(f"completed ({len(completed)}): {', '.join(completed)}")
    if failed:
        lines.append(f"failed ({len(failed)}): {', '.join(failed)}")
    if isinstance(restart, dict) and restart:
        basis = restart.get("basis", "")
        detail = f" (after {restart['previous_status']})" \
            if restart.get("previous_status") else ""
        lines.append(f"restart: {basis}{detail}".rstrip())
    elif isinstance(restart, str) and restart:
        lines.append(f"restart: {restart}")
    if run_id:
        lines.append(f"run: {run_id}")
    return "\n".join(lines).rstrip() + "\n"


def commit_build(project_folder: str, message: str | None = None) -> dict:
    """Stage the allow-list and commit when anything changed.

    Staging is `stage_for_commit` - never a raw `git add -A`: the
    .gitignore allow-list keeps untracked binaries out, and any binary
    that reaches the index is uncached into the binary manifest except
    for the explicit promoted-reel `.drt` recovery archive. A clean tree
    commits nothing and reports
    reason `clean`, so a no-op build produces no spurious commit.
    Files are committed verbatim - see the module docstring on
    machine-local absolute paths.
    """
    root = Path(project_folder)
    if not (root / ".git").exists():
        return {"committed": False, "reason": "no-repo",
                "binaries_recorded": []}
    staged = stage_for_commit(project_folder)
    if not staged.get("staged"):
        return {"committed": False,
                "reason": staged.get("reason", "staging failed"),
                "binaries_recorded": staged.get("binaries_recorded", [])}
    status = git(project_folder, "status", "--porcelain")
    if not status.stdout.strip():
        return {"committed": False, "reason": "clean",
                "binaries_recorded": staged.get("binaries_recorded", [])}
    msg = message if message is not None else render_commit_message(project_folder)
    commit = git(project_folder, "commit", "-m", msg)
    if commit.returncode != 0:
        return {"committed": False,
                "reason": f"git commit failed: {commit.stderr.strip()[-400:]}",
                "binaries_recorded": staged.get("binaries_recorded", [])}
    rev = git(project_folder, "rev-parse", "--short", "HEAD")
    files = git(project_folder, "show", "--pretty=format:", "--name-only", "HEAD")
    return {"committed": True,
            "commit": rev.stdout.strip(),
            "files": sorted(f for f in files.stdout.splitlines() if f.strip()),
            "binaries_recorded": staged.get("binaries_recorded", [])}


def write_build_record(project_folder: str, timeline_name: str,
                       otio_text: str | None,
                       timeline_state: dict | None) -> dict:
    """Write the per-build timeline record under the step's otio dir.

    Returns the paths written.  Either half may be absent (an export
    Resolve declined still leaves a record saying so) - the
    BUILD-RECORD note names the blind spot either way.
    """
    import sys
    repo = Path(__file__).resolve().parents[2]
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    from library.tools.project_layout import Area, ProjectLayout

    layout = ProjectLayout(project_folder)
    out_dir = Path(str(layout.write_dir(Area.TIMELINE_INTERCHANGE, step="render")))
    written: dict = {"dir": str(out_dir), "otio": "", "timeline_json": "",
                     "record": ""}
    safe = timeline_name or "untitled"
    if otio_text is not None:
        otio_path = out_dir / f"{safe}.build.otio"
        otio_path.write_text(otio_text, encoding="utf-8")
        written["otio"] = str(otio_path)
    if timeline_state is not None:
        state_path = out_dir / f"{safe}.timeline.json"
        state_path.write_text(json.dumps(timeline_state, indent=2,
                                         sort_keys=True),
                              encoding="utf-8")
        written["timeline_json"] = str(state_path)
    comps_rel = "pipeline_output/steps/6_01_render/fusion_comps"
    try:
        comps_rel = str(Path(str(layout.write_dir(
            Area.FUSION_COMPS, step="render"))).relative_to(project_folder))
    except Exception:
        pass
    record_path = out_dir / f"{safe}.BUILD-RECORD.md"
    stamped = datetime.datetime.now(datetime.timezone.utc).isoformat()
    missing = [k for k, v in (("otio", otio_text),
                              ("serializer read", timeline_state))
               if v is None]
    record_path.write_text(
        f"# Build record: {safe}\n\n"
        f"Recorded {stamped} at the end of step 6.01, after comps and grade.\n\n"
        f"{BLIND_SPOT_TEXT.format(comps_rel=comps_rel)}\n\n"
        + (f"Missing this build: {', '.join(missing)} "
           f"(Resolve declined; the build verdict is in the step output, "
           f"not here).\n" if missing else ""),
        encoding="utf-8")
    written["record"] = str(record_path)
    return written


@under_lease("record promoted reel versions", exclusive=False)
def record_reel_promotion(project_folder: str, resolve_project_name: str,
                          final_names, message: str | None = None) -> dict:
    """Record each promoted reel, archive its `.drt`, and commit.

    The reels flow (step 7.01/7.02) never called the 6.01 hook
    (`record_finished_timeline`), so every reel built since PR 920
    committed nothing and no baseline exists to diff the captain's
    hand edits against. Measured 2026-09-11: the project repo holds
    one hand-made capture (Reel 09 (final), c79c681) and no reel build
    ever committed through the hook.

    Call after `promote_staged_reels`, on both promotion paths (the
    in-build promote and the verify_reels-node promote).  Never raises:
    a record that breaks the build is worse than no record, so every
    failure is returned as `committed: False` with a reason.

    A snapshot failure COMMITS ANYWAY, naming the failure in the
    message.  It used to return without committing, which meant a build
    run with Resolve closed, busy, or showing another project left the
    captain's declarations uncommitted - and a declaration nothing in
    git remembers is one that goes missing the next time somebody edits
    the file.  The snapshot is the nice-to-have; the declarations, the
    run state and the step outputs are the record.

    The snapshots land under `pipeline_output/review/` (on the
    allow-list) as `<timeline>.timeline.json`, in the serializer's
    git-diffable shape - source/record in-out, transform, markers -
    beside the declaration the build read, so a later diff answers what
    moved.

    The Fusion comps land under
    `pipeline_output/steps/7_01_build_reels/fusion_comps/` (on the
    allow-list) as `<reel>__*.comp`, one plain-text export per comp per
    reel (`library/tools/reel_fusion_comps.py`). The serializer's
    record reduces 76 Fusion tools to a comp count plus a name, which
    is neither diffable nor mergeable; the exports are the plain Lua
    text Resolve wrote, committed VERBATIM in the same commit - never
    compressed, re-encoded, summarised or normalised. Best effort like
    the snapshots: a reel Resolve no longer holds is recorded, never
    raised. Once a reel's comp text files have all landed, its existing
    promoted timeline is exported as a `.drt` under
    `pipeline_output/review/recovery_archives/`. These binaries are
    committed beside the text record as recovery artefacts; the store's
    diff and merge decisions continue to read the text files only.
    """
    report: dict = {"committed": False, "reason": "", "files": [],
                    "snapshots": [], "recovery_archives": {}}
    if not project_folder:
        report["reason"] = "no project_folder, so nowhere to record"
        return report
    if not (Path(project_folder) / ".git").exists():
        report["reason"] = "no-repo"
        return report
    names = [n for n in (final_names or []) if n]
    if not names:
        report["reason"] = "no promoted timelines named"
        return report
    try:
        import sys
        repo = Path(__file__).resolve().parents[2]
        if str(repo) not in sys.path:
            sys.path.insert(0, str(repo))
        from library.tools.resolve_locale import (
            scriptapp_preserving_locale)
        from library.tools.resolve_locale import load_resolve_script
        dvr = load_resolve_script()
        resolve = scriptapp_preserving_locale(dvr, "Resolve")
        if not resolve:
            raise RuntimeError("Resolve is not running")
        project = resolve.GetProjectManager().GetCurrentProject()
        if not project or project.GetName() != resolve_project_name:
            raise RuntimeError(
                f"Resolve has {project.GetName()!r} open, not "
                f"{resolve_project_name!r} - refusing to snapshot "
                f"another project's timelines")
        from library.tools.timeline_serializer import (
            serialize_timeline_state)
        review_dir = Path(project_folder) / "pipeline_output" / "review"
        review_dir.mkdir(parents=True, exist_ok=True)
        # Read through each timeline's OWN handle: the serializer takes
        # it directly, so the cursor never moves - a snapshot that set
        # the current timeline per name walked the cursor across every
        # final unleashed, the move that killed a sibling lane's Fusion
        # pass on 2026-09-20.
        snapshots_by_timeline: dict = {}
        timelines_by_name: dict = {}
        for name in names:
            target = None
            for index in range(1, project.GetTimelineCount() + 1):
                candidate = project.GetTimelineByIndex(index)
                if candidate and candidate.GetName() == name:
                    target = candidate
                    break
            if target is None:
                report.setdefault("missing", []).append(name)
                continue
            timelines_by_name[name] = target
            state = serialize_timeline_state(resolve_mock=resolve,
                                             timeline=target)
            safe = "".join(
                c if c.isalnum() or c in "-_." else "_"
                for c in name) or "timeline"
            path = review_dir / f"{safe}.timeline.json"
            path.write_text(json.dumps(state, indent=2,
                                       sort_keys=True,
                                       ensure_ascii=False) + "\n",
                            encoding="utf-8")
            report["snapshots"].append(str(path))
            snapshots_by_timeline[name] = str(path)
        # The Fusion comps, off the same handles the snapshots just
        # read: plain Lua text per comp per reel, committed verbatim
        # in the same commit below. Best effort - a reel Resolve no
        # longer holds is recorded on the report, never raised, and
        # the commit below still goes ahead.
        try:
            from library.tools import reel_fusion_comps as _comps
            report["fusion_comps"] = _comps.export_built_reels(
                project, names, project_folder)
        except Exception as exc:  # noqa: BLE001 - never fail the build
            report["fusion_comps"] = {"reels": {}, "files": [],
                                      "errors": [f"export failed: {exc!r}"]}
        # A `.drt` is the recovery copy, not the version record. Only
        # export it after the matching Fusion-comp text files exist and
        # read as text, then commit both in the same project-store commit.
        report["recovery_archives"] = _export_reel_recovery_archives(
            resolve, project, names, timelines_by_name,
            report["fusion_comps"], project_folder)
        # Which snapshot each promoted timeline was built from, and
        # which same-named files that snapshot superseded
        # (`plan_provenance.record_snapshot_supersession`): without it
        # three files can name one reel with nothing saying which is
        # authoritative, and a later check grades the stale one
        # (Reel 09, 36510 vs 36490, 2026-09-19). Never fails the
        # promotion - the timelines are promoted and unaffected, and
        # a supersession failure never skips the comp export above
        # (or vice versa): each records its own failure on the report.
        try:
            from library.tools import plan_provenance as _provenance
            supersession = _provenance.record_snapshot_supersession(
                str(review_dir), snapshots_by_timeline)
            report["snapshot_supersession"] = supersession
        except Exception as exc:  # noqa: BLE001
            report["supersession_failed"] = f"{exc!r}"
    except Exception as exc:
        # COMMIT ANYWAY. The snapshot is the nice-to-have half; the
        # declarations, the run state and the step outputs on disk are
        # the record the store exists for, and they are already
        # written. Returning here left them uncommitted whenever
        # Resolve was closed, busy or showing another project - the
        # failure mode that loses a captain's declaration, because the
        # next rebuild reads the file and nothing in git remembers it
        # ever differed. The failure is carried into the commit
        # MESSAGE rather than dropped, so a reader of the log can see
        # which commits have no timeline snapshot behind them.
        report["snapshot_failed"] = f"{exc!r}"
        report["recovery_archives"] = {
            "reels": {
                name: {
                    "archived": False,
                    "reason": ("Fusion-comp text export was not reached "
                              "because reading the promoted timeline "
                              "failed")}
                for name in names},
            "files": [],
            "errors": [
                f"{name}: Fusion-comp text export was not reached because "
                "reading the promoted timeline failed"
                for name in names],
        }
        message = (message or (
            "reels build: " + ", ".join(names)
            + f"\n\nPromoted into the Resolve project "
              f"{resolve_project_name!r}.")) + (
            f"\n\nNO TIMELINE SNAPSHOT in this commit: reading the "
            f"promoted timelines back out of Resolve failed ({exc!r}). "
            f"The declarations and run state are committed anyway - "
            f"they are what a rebuild reads, and leaving them "
            f"uncommitted is how a declaration goes missing.\n\n"
            f"NO DRT RECOVERY ARCHIVE: Fusion-comp text exports were not "
            f"reached because reading the promoted timeline failed.")
        result = commit_build(project_folder, message)
        report.update(result)
        if not report.get("committed"):
            report["reason"] = (
                f"snapshot failed ({exc!r}) and the commit that should "
                f"have gone ahead anyway did not: "
                f"{result.get('reason', 'unknown')}")
        else:
            report["reason"] = f"snapshot failed: {exc!r}; committed anyway"
        return report
    if message is None:
        message = ("reels build: "
                   + ", ".join(names)
                   + "\n\nPromoted into the Resolve project "
                   f"{resolve_project_name!r}; snapshots beside the "
                   f"declaration they were built from.")
    archived = report["recovery_archives"].get("files") or []
    if archived:
        rel_archives = [str(Path(path).relative_to(project_folder))
                        for path in archived]
        message += ("\n\nDRT recovery archives committed:\n"
                    + "\n".join(f"- {path}" for path in rel_archives))
    archive_errors = report["recovery_archives"].get("errors") or []
    if archive_errors:
        message += ("\n\nDRT recovery archives not written:\n"
                    + "\n".join(f"- {error}" for error in archive_errors))
    result = commit_build(project_folder, message)
    report.update(result)
    if not report.get("committed") and not report.get("reason"):
        report["reason"] = result.get("reason", "")
    return report


def record_finished_timeline(resolve, timeline, project_folder: str,
                             timeline_name: str) -> dict:
    """Export the finished timeline and commit the per-build record.

    Runs at the end of step 6.01, after comps and grade, so the record
    describes the finished timeline.  Never raises: a record that
    breaks the build is worse than no record, so every failure is
    returned as `committed: False` with a reason the caller puts on
    the build's warnings.
    """
    report: dict = {"committed": False, "reason": "", "files": []}
    if not project_folder:
        report["reason"] = "no project_folder, so nowhere to record"
        return report
    if not (Path(project_folder) / ".git").exists():
        report["reason"] = "no-repo"
        return report
    try:
        export_flag = getattr(resolve, "EXPORT_OTIO", 15)
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".otio",
                                         delete=False) as tmp:
            tmp_path = tmp.name
        try:
            exported = timeline.Export(tmp_path, export_flag)
        except Exception as exc:
            report["reason"] = f"OTIO export raised {exc!r}"
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            return report
        otio_text = None
        if exported and os.path.exists(tmp_path):
            with open(tmp_path, encoding="utf-8", errors="replace") as fh:
                otio_text = fh.read()
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        if otio_text is None:
            report["reason"] = (
                f"Resolve declined the final OTIO export "
                f"(returned {exported!r})")
        try:
            import sys
            repo = Path(__file__).resolve().parents[2]
            if str(repo) not in sys.path:
                sys.path.insert(0, str(repo))
            from library.tools.timeline_serializer import (
                serialize_timeline_state)
            timeline_state = serialize_timeline_state(
                resolve_mock=resolve, timeline=timeline)
        except Exception as exc:
            timeline_state = None
            report["serializer_reason"] = f"serializer read failed: {exc!r}"
        write_build_record(project_folder, timeline_name or "untitled",
                           otio_text, timeline_state)
        result = commit_build(project_folder)
        report.update(result)
        if not report.get("committed") and not report.get("reason"):
            report["reason"] = result.get("reason", "")
        return report
    except Exception as exc:
        report["reason"] = f"version-control record failed: {exc!r}"
        return report
