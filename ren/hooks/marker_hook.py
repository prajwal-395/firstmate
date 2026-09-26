"""The marker hook: Resolve marker notes become Ren work.

The captain types notes onto built timelines inside DaVinci Resolve.
`library/tools/marker_feedback.py` pulls them into
`<project>/marker_feedback/` (one file per pull, needs Resolve open)
and `library/tools/marker_routing.py` routes each note to the step
that owns the decision it is about (`ren notes` reads the same route
without Resolve).

This hook is the session side of that pair: it never probes Resolve
(a hook that calls Resolve at every session start would probe a
possibly-closed app on every session for every lane). What it does is
disk-only - it scans the projects root for pulled notes no routing
record has carried yet, and prints each note with the `ren spec` verb
that translates it. The typed operation, not a word
hit, supplies its owner; the pull itself (`ren markers pull`, Resolve
open) stays an explicit invocation.

Subcommands (all read-only, exit 0 even when there is work - a hook
must never fail a session start):

* `check`: scan every project for pending notes and print the work
  queue. No arguments, no Resolve, milliseconds.
* `work --project <dir>`: the pending notes for ONE project with the
  suggested translation command per note.

Installed by `ren setup-hooks [--app claude-code|opencode|codex
--write]` (`ren/setup_hooks.py`); templates live in
`ren/hooks/templates/`.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys

MARKER_DIRNAME = "marker_feedback"


def _projects_root(explicit: str = "") -> str:
    if explicit:
        return explicit
    for env in ("PIPELINE_PROJECTS_ROOT",):
        value = os.environ.get(env, "")
        if value:
            return value
    try:
        from library.tools import paths
        root = str(paths.PROJECTS_ROOT)
        if root:
            return root
    except Exception:
        pass
    return ""


def _pull_files(project_folder: str) -> list:
    marker_dir = os.path.join(project_folder, MARKER_DIRNAME)
    try:
        entries = sorted(os.listdir(marker_dir))
    except OSError:
        return []
    return [os.path.join(marker_dir, e) for e in entries
            if e.endswith(".json")]


def _routed_note_count(project_folder: str) -> int:
    """Notes already carried, off the routing record when present."""
    record = os.path.join(project_folder, MARKER_DIRNAME, "ROUTED-NOTES.md")
    try:
        with open(record, "r", encoding="utf-8") as handle:
            return sum(1 for line in handle if line.startswith("| "))
    except OSError:
        return 0


def pending_notes(project_folder: str) -> list:
    """Pulled notes not yet carried, as plain dicts.

    Disk-only. A pull file holds `{"notes": [...]}`; each note carries
    at least its typed text under `text` (older pulls) or `name`/`note`
    (Resolve's two fields). Whatever the spelling, keep the words
    rather than dropping the note.
    """
    pending = []
    link_id = _edit_link_id_reader()
    for path in _pull_files(project_folder):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            continue
        notes = payload.get("notes", payload if isinstance(payload, list) else [])
        for note in notes if isinstance(notes, list) else []:
            if isinstance(note, str):
                pending.append({"text": note, "source": path})
            elif isinstance(note, dict):
                text = (note.get("text") or " ".join(
                    p for p in (note.get("name", ""), note.get("note", ""))
                    if p).strip())
                if text:
                    timeline = (payload.get("timeline", "")
                                if isinstance(payload, dict) else "")
                    pending.append({"text": text, "source": path,
                                    "reel": note.get("reel", ""),
                                    "timeline": note.get("timeline", ""),
                                    "note_id": link_id(note, timeline, path),
                                    "project": project_folder})
    routed = _routed_note_count(project_folder)
    return pending[routed:] if routed and routed < len(pending) else pending


def _edit_link_id_reader():
    """`marker_routing.edit_link_id`, or a reader that names no id.

    A hook must never fail a session start, so a library that cannot be
    imported here degrades the suggestion rather than the hook.
    """
    try:
        from library.tools.marker_routing import edit_link_id
    except Exception:
        return lambda note, timeline, path: ""

    def read(note, timeline, path):
        try:
            return edit_link_id(note, timeline, path)
        except Exception:
            return ""
    return read


def suggest_verb(note_text: str, note_id: str = "",
                 project: str = "<project>") -> str:
    """Point every note at the typed translation path.

    Choosing a Ren verb from words in the note is the same false-match
    defect as routing a step from those words. The edit spec names an op
    first; its declared type supplies the owner. The command carries the
    marker's own link id: without it `ren spec prepare` files a second,
    duplicate note and the marker itself stays untranslated.
    """
    if not note_id:
        return ("ren spec prepare <project> --request "
                f"{shlex.quote(note_text)} (use --note-id from `ren notes` "
                "to link this marker to its typed operations)")
    return (f"ren spec prepare {shlex.quote(project)} --note-id "
            f"{shlex.quote(note_id)} --request {shlex.quote(note_text)}")


def cmd_check(args) -> int:
    root = _projects_root(getattr(args, "projects_root", ""))
    if not root or not os.path.isdir(root):
        print("ren-marker-hook: no projects root found; "
              "set PIPELINE_PROJECTS_ROOT.")
        return 0
    found = 0
    for entry in sorted(os.listdir(root)):
        project_folder = os.path.join(root, entry)
        if not os.path.isdir(project_folder):
            continue
        notes = pending_notes(project_folder)
        if not notes:
            continue
        found += len(notes)
        print(f"{entry}: {len(notes)} pending note(s)")
        for note in notes[:5]:
            print(f"  - {note['text'][:120]}")
            print(f"    work it with: "
                  f"{suggest_verb(note['text'], note.get('note_id', ''), project_folder)}")
        if len(notes) > 5:
            print(f"  ... and {len(notes) - 5} more "
                  f"(`ren notes {project_folder}` lists them all)")
    if not found:
        print("ren-marker-hook: no pending timeline notes.")
    else:
        print(f"ren-marker-hook: {found} pending note(s). "
              f"Pull new notes with Resolve open: "
              f"`bin/vep manage_project.py` has no pull verb - run "
              f"`python3 -m library.tools.marker_feedback pull "
              f"--project <project>` then `ren notes <project>`.")
    return 0


def cmd_work(args) -> int:
    notes = pending_notes(args.project)
    if not notes:
        print(f"ren-marker-hook: no pending notes in {args.project}.")
        return 0
    for note in notes:
        where = note.get("timeline") or note.get("reel") or "?"
        print(f"[{where}] {note['text']}")
        print(f"  work it with: "
              f"{suggest_verb(note['text'], note.get('note_id', ''), args.project)}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="ren-marker-hook",
        description="Turn Resolve marker notes into Ren work (disk-only).")
    sub = parser.add_subparsers(dest="command", required=True)
    p_check = sub.add_parser("check", help="scan all projects for pending notes")
    p_check.add_argument("--projects-root", default="",
                         help="scan this root instead of PIPELINE_PROJECTS_ROOT")
    p_check.set_defaults(func=cmd_check)
    p_work = sub.add_parser("work", help="pending notes for one project")
    p_work.add_argument("--project", required=True,
                        help="project slug path (absolute)")
    p_work.set_defaults(func=cmd_work)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
