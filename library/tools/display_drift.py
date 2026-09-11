"""A hand edit to a display file announces itself before it dies.

Eight edit classes lose a shallow fix SILENTLY: the owning
computation regenerates the display and the hand value vanishes
without a word. Refusal is not reachable there - the pipeline
cannot intercept a value typed into a file it is about to
overwrite, and it cannot read a hand move inside Resolve's project
database at all. What IS reachable is a witness: fingerprint every
display file the pipeline writes, and at the next pre-run, name
every file that changed since the pipeline wrote it, with both
values (snapshot hash and size against today's) plus the owning
class and the deep path. A loud loss is survivable; a
silent one is the defect this module closes.

Lifecycle (explicit, because the alternative blesses hand edits)
---------------------------------------------------------------
- After a clean build the operator snapshots:
  `python3 -m library.tools.display_drift snapshot <project>`.
- Every pre-run checks: `... check <project>` (wired into
  `run_pipeline`, print-only, never refusing - a stale display
  must not stop an unrelated build, but it must SAY so).
- After an INTENTIONAL regeneration the operator re-snapshots, or
  the next pre-run flags the pipeline's own fresh output once.
  Flag fatigue is the honest cost of a witness without writer
  wiring; writer-side snapshots at each display site are the named
  follow-up, not this module.

What is fingerprinted: the displays a rebuild overwrites and a
human might hand-fix - subtitle plans and overlays, the live reel
proposal, motion-graphics payloads, assembly manifests, timeline
interchange (OTIO). Reports (`conformance_report.json`),
prompt echoes (`llm_requests/`, `llm_responses/`), history
(`backups/`, timestamped snapshots) and timeline measurements
(`timeline_captures/`) are never fingerprinted: reports and echoes
are audit trail, history is the past, and measurements belong to
the parallel lane's live project.

Resolve-side displays (a moved timeline item, a hand-graded node)
have no file carrier the pipeline wrote, so no fingerprint can
witness them. For those two classes the refusal function
(`edit_depth.refuse_display_edit`) is the mechanism, consulted by
whoever routes the edit - and the coherence informational section
shows what the live timelines currently say without grading them.
Which classes those are, and what the user sees instead where even
a flag cannot run (`picture_position`), is stated per class in
`edit_depth.REFUSAL_REACHABILITY` - no class is reported as covered
because it was hard.

`tests/test_display_drift.py`.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone

from library.tools.timeline_transcript import (
    SCRATCH_SUBDIR as _TRANSCRIPT_SUBDIR,
    TRANSCRIPT_FILENAME as _TRANSCRIPT_FILENAME,
)

#: The ledger, at the project root beside `pipeline_run.json`:
#: runner-owned run state, written by snapshot, read by check.
LEDGER_FILENAME = "display_drift.json"

#: Version 2 records `{sha, size}` per file; version 1 recorded the bare
#: sha string and is still read (size then prints as unknown).
LEDGER_VERSION = 2

#: The timeline transcript, named by its OWN module rather than spelled
#: again here: `timeline_transcript` owns what that file is called and
#: which scratch subdirectory holds it, and a witness that spells them
#: itself goes blind the day either is renamed
#: (`tests/test_operations.py`).
_TRANSCRIPT_DISPLAY = (
    f"pipeline_output/scratch/{_TRANSCRIPT_SUBDIR}/{_TRANSCRIPT_FILENAME}")

#: Display roots fingerprinted, relative to the project folder.
#: The live proposal only - timestamped copies are history.
DISPLAY_ROOTS = (
    "subtitle_plans",
    "subtitle_overlays",
    "pipeline_output/steps",
    "pipeline_output/review/reel_proposals_v2.json",
    _TRANSCRIPT_DISPLAY,
)

#: Exact filenames (not directories) among the roots above.
_SINGLE_FILES = {
    "pipeline_output/review/reel_proposals_v2.json",
    _TRANSCRIPT_DISPLAY,
}

#: Suffixes worth witnessing. Renders (`.mp4`, `.mov`, stills) are
#: excluded deliberately: they are heavy, and their sources (the
#: payloads and plans) are fingerprinted instead. Generated Fusion
#: `.comp` files ARE witnessed: they are text, they are the look's
#: file carrier (`look_grade`), and a hand edit to one dies on the
#: next comp build.
WITNESSED_SUFFIXES = (".json", ".srt", ".vtt", ".ass", ".otio", ".txt",
                      ".comp")

#: Never fingerprinted, even under a witnessed root.
SKIPPED_DIRS = ("backups", "llm_requests", "llm_responses", "exports",
                "timeline_captures", "quarantine")

#: History snapshots are the past, not a layer.
import re as _re
_HISTORY_RE = _re.compile(r"_20\d{6}T\d+Z\.json$")


def _classes_for(relpath: str) -> list:
    """The edit classes a display file belongs to, for the loud message.

    One file can display several classes - the manifest carries
    structure, assets and timing on the same clips - so this is a
    list, and the flag names every owner with its deep path.
    """
    if relpath.split("/", 1)[0] in ("subtitle_plans", "subtitle_overlays"):
        return ["wording"]
    if "motion_graphic" in relpath or "timed_text" in relpath:
        return ["mg_content"]
    if relpath.endswith(".comp") or "carrier" in relpath:
        return ["look_grade"]
    # Owner stores (`external/overlay_intent.json`,
    # `external/captain_edits.json`, `external/mix_intent.json`,
    # `external/placed_assets.json`) are deliberately unmapped: they
    # are the declarations the pipeline READS, and flagging the
    # captain's own deep-path edit as drift would punish the fix this
    # witness exists to route toward. This function only ever sees
    # files the pipeline WROTE (see `display_files`), so a branch for
    # one would be dead as well as wrong.
    if "box.json" in relpath or "props.json" in relpath:
        return ["overlay_position"]
    if relpath.endswith(".otio"):
        return ["audio_levels"]
    if "manifest.json" in relpath:
        # `assembly_manifest.json` matches here too (it ends in
        # `manifest.json`): one file carries structure, assets and
        # timing on the same clips, so the flag names every owner.
        return ["structure", "assets", "clip_timing"]
    if "reel_proposal" in relpath or "select_reels" in relpath:
        return ["structure"]
    if "transcript.json" in relpath:
        return ["wording"]
    return ["structure"]


def _class_for(relpath: str) -> str:
    """First owner, for callers that need one."""
    return _classes_for(relpath)[0]


def ledger_path(project_folder: str) -> str:
    return os.path.join(str(project_folder), LEDGER_FILENAME)


def _sha(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint(path: str) -> dict:
    """Both values the witness compares: content hash and byte size."""
    return {"sha": _sha(path), "size": os.path.getsize(path)}


def display_files(project_folder: str) -> list:
    """Every witnessed display file, as project-relative paths."""
    root = str(project_folder)
    found = []
    for entry in DISPLAY_ROOTS:
        candidate = os.path.join(root, entry)
        if entry in _SINGLE_FILES:
            if os.path.isfile(candidate):
                found.append(entry)
            continue
        if not os.path.isdir(candidate):
            continue
        for dirpath, dirnames, filenames in os.walk(candidate):
            dirnames[:] = [d for d in dirnames
                           if d not in SKIPPED_DIRS]
            for filename in filenames:
                if not filename.endswith(WITNESSED_SUFFIXES):
                    continue
                if _HISTORY_RE.search(filename):
                    continue
                full = os.path.join(dirpath, filename)
                found.append(os.path.relpath(full, root))
    return sorted(found)


def snapshot(project_folder: str) -> dict:
    """Record current hashes. Returns `{"files": n, "taken_at": ...}`."""
    files = {}
    for relpath in display_files(project_folder):
        try:
            files[relpath] = _fingerprint(
                os.path.join(str(project_folder), relpath))
        except OSError:
            continue
    ledger = {"version": LEDGER_VERSION, "files": files,
              "taken_at": datetime.now(timezone.utc).isoformat()}
    with open(ledger_path(project_folder), "w",
              encoding="utf-8") as handle:
        json.dump(ledger, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return {"files": len(files), "taken_at": ledger["taken_at"]}


def check(project_folder: str) -> dict:
    """Compare current displays against the snapshot.

    Returns `{"drifted": [...], "vanished": [...], "files": n}` and
    prints the loud block on stderr when anything drifted: each file
    with its owning class and deep path, so the loss announces
    itself BEFORE the rebuild performs it.
    """
    from library.tools import edit_depth

    path = ledger_path(project_folder)
    if not os.path.isfile(path):
        return {"drifted": [], "vanished": [], "files": 0,
                "baseline": False}
    try:
        with open(path, encoding="utf-8") as handle:
            ledger = json.load(handle)
    except (OSError, ValueError):
        return {"drifted": [], "vanished": [], "files": 0,
                "baseline": False}
    known = ledger.get("files", {}) if isinstance(ledger, dict) else {}
    drifted, vanished = [], []
    details: dict = {}
    for relpath, old in known.items():
        # Version 1 ledgers record the bare sha string; version 2
        # records `{sha, size}`. Both read.
        old_sha = old.get("sha") if isinstance(old, dict) else old
        old_size = old.get("size") if isinstance(old, dict) else None
        full = os.path.join(str(project_folder), relpath)
        if not os.path.isfile(full):
            vanished.append(relpath)
            continue
        try:
            current = _fingerprint(full)
            if current["sha"] != old_sha:
                drifted.append(relpath)
                details[relpath] = (old_sha, old_size, current)
        except OSError:
            vanished.append(relpath)
    if drifted or vanished:
        print("DISPLAY DRIFT: files the pipeline wrote have changed "
              "by hand since the snapshot. Rebuilding their owning "
              "step replaces these values - route via the deep path "
              "instead:", file=sys.stderr)
        for relpath in drifted:
            old_sha, old_size, current = details[relpath]
            old_desc = (f"{str(old_sha)[:12]}"
                        + (f", {old_size} bytes" if old_size is not None
                           else ", size unknown (v1 snapshot)"))
            print(f"  ~ {relpath}", file=sys.stderr)
            print(f"      was {old_desc} -> now "
                  f"{current['sha'][:12]}, {current['size']} bytes",
                  file=sys.stderr)
            for edit_class in _classes_for(relpath):
                try:
                    deep = edit_depth.DEEP_PATH[edit_class]
                except KeyError:
                    deep = ("no classified owner - name one before "
                            "editing")
                print(f"      [{edit_class}] deep path: {deep}",
                      file=sys.stderr)
        for relpath in vanished:
            print(f"  - {relpath} (gone since snapshot)",
                  file=sys.stderr)
    return {"drifted": drifted, "vanished": vanished,
            "files": len(known), "baseline": True}


def main(argv=None) -> int:
    """`python3 -m library.tools.display_drift <snapshot|check>
    <project_folder>`."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="library.tools.display_drift",
        description="Fingerprint pipeline-written displays (snapshot) "
                    "and name hand edits before a rebuild eats them "
                    "(check).")
    parser.add_argument("verb", choices=["snapshot", "check"])
    parser.add_argument("project_folder")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.verb == "snapshot":
        report = snapshot(args.project_folder)
        print(f"snapshotted {report['files']} display file(s) at "
              f"{report['taken_at']}")
        return 0
    report = check(args.project_folder)
    if not report.get("baseline"):
        print("no snapshot on file - run snapshot first; nothing checked")
        return 0
    print(f"{len(report['drifted'])} drifted, "
          f"{len(report['vanished'])} vanished, "
          f"{report['files']} fingerprinted.")
    return 2 if (report["drifted"] or report["vanished"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
