"""Read (and name) a project's creative history: commits, branches, tags.

The `ren` verbs over the commit graph `undo_journal` records -
`ren commits`, `ren branches`, `ren tag`, `ren cherry-pick`,
`ren revert` - reached as
`python3 -m library.tools.creative_history <mode> <project> [...]`,
one mode per verb the way `resolve_axi ownership` is one mode of its
module. Read-only, except `tag <name> <commit>`,
`cherry-pick <commit>` and `revert <commit>`, which record.

A commit id may be given whole or as a unique leading prefix. An
unknown id is refused naming what was looked up; a prefix two commits
share is refused naming the candidates, so a history read never
silently returns the WRONG commit.
"""

from __future__ import annotations

import argparse
import json
import sys

MODES = ("commits", "branches", "tag", "cherry-pick", "revert")


class HistoryRefused(RuntimeError):
    """A history read (or tag write) that declined, always by name."""


def _resolve_project(ref: str) -> str:
    """A project slug or path to its folder, the `perf_ledger` shape."""
    from library.tools.project_registry import get_project, resolve_project_path
    found = resolve_project_path(ref)
    if found is not None:
        return str(found.parent)
    try:
        config = get_project(ref)
    except FileNotFoundError as missing:
        raise HistoryRefused(
            f"no project {ref!r} - pass a project slug or a path to a "
            f"folder holding project.yaml ({missing})") from missing
    return str(config._project_root)


def _resolve_commit(project: str, prefix: str) -> dict:
    """The one commit `prefix` names: whole id or unique leading prefix.

    Refuses an id nothing answers, and a prefix two commits share -
    naming the candidates - so `--show` never returns the wrong commit.
    """
    from library.tools import undo_journal as uj

    commits = uj.list_commits(project)
    exact = [c for c in commits if c.get("id") == prefix]
    if exact:
        return exact[0]
    matches = [c for c in commits if str(c.get("id") or "").startswith(prefix)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise HistoryRefused(
            f"no commit {prefix!r} - "
            f"`ren commits <project> --list` shows what is recorded")
    candidates = ", ".join(
        f"{str(c.get('id') or '')[:12]} ({c.get('timeline')}: "
        f"{c.get('intent')})" for c in matches[:5])
    raise HistoryRefused(
        f"{prefix!r} names {len(matches)} commits, not one: {candidates} - "
        f"pass a longer prefix")


def _short(commit_id) -> str:
    return str(commit_id or "")[:12]


def _change_counts(changes) -> str:
    from library.tools import timeline_diff
    summary = timeline_diff.summarize(changes or ())
    if not summary:
        return "no changes"
    return ", ".join(f"{count} {op}" for op, count in sorted(summary.items()))


def _commit_line(commit: dict) -> str:
    return (f"#{commit.get('sequence')} {_short(commit.get('id'))} "
            f"[{commit.get('timeline')}] ({commit.get('agent')}) "
            f"{commit.get('intent')} "
            f"({_change_counts(commit.get('changes'))})")


def _render_commit(commit: dict) -> str:
    lines = [f"commit {commit.get('id')}",
             f"timeline: {commit.get('timeline')}",
             f"agent: {commit.get('agent')}",
             f"intent: {commit.get('intent')}",
             f"parent: {commit.get('parent') or '(root)'}"]
    if commit.get("second_parent"):
        lines.append(f"second_parent: {commit.get('second_parent')}")
    lines.append(f"changes: {_change_counts(commit.get('changes'))}")
    if commit.get("evaluation"):
        lines.append(f"evaluation: {json.dumps(commit['evaluation'], sort_keys=True)}")
    if commit.get("artifacts"):
        lines.append(f"artifacts: {json.dumps(commit['artifacts'], sort_keys=True)}")
    return "\n".join(lines)


def _cmd_commits(project: str, args) -> int:
    from library.tools import undo_journal as uj

    if args.show is not None and args.timeline is not None:
        print("creative_history: --show and --timeline select different "
              "commits - pass one", file=sys.stderr)
        return 2
    if args.name is not None or args.commit is not None:
        print("creative_history: commits takes no name or commit - "
              "a tag names one (`ren tag <project> <name> <commit>`), "
              "a replay takes one "
              "(`ren cherry-pick <project> <commit>`), "
              "a revert takes one (`ren revert <project> <commit>`)",
              file=sys.stderr)
        return 2
    if args.show is not None:
        try:
            commit = _resolve_commit(project, args.show)
        except HistoryRefused as refused:
            print(f"creative_history: {refused}", file=sys.stderr)
            return 2
        print(json.dumps(commit, indent=2, sort_keys=True, default=str)
              if args.json else _render_commit(commit))
        return 0
    commits = uj.list_commits(project)
    if args.timeline is not None:
        commits = [c for c in commits if c.get("timeline") == args.timeline]
        if not commits:
            known = sorted({str(c.get("timeline") or "")
                            for c in uj.list_commits(project) if c.get("timeline")})
            print(f"creative_history: no commits for timeline "
                  f"{args.timeline!r} in {project}"
                  + (f" - timelines with commits: {', '.join(known)}"
                     if known else " - no commits are recorded yet"),
                  file=sys.stderr)
            return 1
    if not commits:
        print(f"creative_history: no creative commits in {project} - "
              f"no touch-up has recorded one yet", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(commits, indent=2, sort_keys=True, default=str))
    else:
        print("\n".join(_commit_line(c) for c in commits))
    return 0


def _cmd_branches(project: str, args) -> int:
    from library.tools import undo_journal as uj

    if args.timeline is not None:
        print("creative_history: --timeline selects commits, not branches - "
              "`ren commits <project> --timeline` is the read you want",
              file=sys.stderr)
        return 2
    if args.name is not None or args.commit is not None:
        print("creative_history: branches takes no name or commit - "
              "a branch is read with --show, a version named with "
              "`ren tag <project> <name> <commit>`, a change replayed "
              "with `ren cherry-pick <project> <commit>`, reverted "
              "with `ren revert <project> <commit>`",
              file=sys.stderr)
        return 2
    if args.show is not None:
        try:
            branch = uj.read_branch(project, args.show)
        except uj.BranchRefused as refused:
            print(f"creative_history: {refused}", file=sys.stderr)
            return 2
        print(json.dumps(branch, indent=2, sort_keys=True, default=str)
              if args.json else
              f"branch {branch.get('name')} -> {_short(branch.get('base'))} "
              f"[{branch.get('timeline')}] {branch.get('note') or ''}".rstrip())
        return 0
    branches = uj.list_branches(project)
    if not branches:
        print(f"creative_history: no branches in {project} - "
              f"no branch has been recorded yet", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(branches, indent=2, sort_keys=True, default=str))
    else:
        print("\n".join(
            f"{b.get('name')} -> {_short(b.get('base'))} "
            f"[{b.get('timeline')}] {b.get('note') or ''}".rstrip()
            for b in branches))
    return 0


def _cmd_tag(project: str, args) -> int:
    from library.tools import undo_journal as uj

    if args.timeline is not None or args.show is not None:
        print("creative_history: --timeline/--show select commits - "
              "a tag takes its target as a positional "
              "(`ren tag <project> <name> <commit>`)",
              file=sys.stderr)
        return 2
    if args.list:
        if args.name or args.commit:
            print("creative_history: --list takes no name or commit - "
                  "drop them to list, or drop --list to record",
                  file=sys.stderr)
            return 2
        tags = uj.list_tags(project)
        if not tags:
            print(f"creative_history: no tags in {project} - "
                  f"nothing has been named a version yet", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(tags, indent=2, sort_keys=True, default=str))
        else:
            print("\n".join(
                f"{t.get('name')} -> {_short(t.get('commit'))} "
                f"{t.get('note') or ''}".rstrip() for t in tags))
        return 0
    if not args.name or not args.commit:
        print("creative_history: tag needs a name and a commit "
              "(`ren tag <project> <name> <commit>`) or --list",
              file=sys.stderr)
        return 2
    try:
        target = _resolve_commit(project, args.commit)
    except HistoryRefused as refused:
        print(f"creative_history: {refused}", file=sys.stderr)
        return 2
    try:
        tag = uj.record_tag(project, name=args.name,
                            commit=target.get("id"),
                            note=args.note or "", move=args.move)
    except uj.TagRefused as refused:
        print(f"creative_history: {refused}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(tag, indent=2, sort_keys=True, default=str))
    else:
        print(f"{tag.get('name')} -> {_short(tag.get('commit'))}")
    return 0


def _read_dest_tracks(path):
    """The destination's current track reads from a JSON file, or None.

    Accepts a bare tracks list or an object with a `tracks` key (a
    timeline export reads back in the latter shape). A missing or
    unparsable file is refused naming the path; the SHAPE is the
    store's to judge, so a well-formed file of the wrong shape
    reaches `cherry_pick` and is refused there.
    """
    if path is None:
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError) as unreadable:
        raise HistoryRefused(
            f"destination tracks at {path!r} could not be read "
            f"({unreadable}) - pass a JSON file holding the "
            f"destination timeline's current track reads") from unreadable
    if isinstance(document, dict):
        document = document.get("tracks")
    return document


def _cmd_cherry_pick(project: str, args) -> int:
    from library.tools import undo_journal as uj

    if args.timeline is not None or args.show is not None:
        print("creative_history: --timeline/--show select commits - "
              "a replay takes its source as a positional "
              "(`ren cherry-pick <project> <commit>`)",
              file=sys.stderr)
        return 2
    if args.name is not None and args.commit is not None:
        print("creative_history: cherry-pick takes one commit, not two - "
              "`ren cherry-pick <project> <commit>`",
              file=sys.stderr)
        return 2
    target_ref = args.commit or args.name
    if not target_ref:
        print("creative_history: cherry-pick needs a commit "
              "(`ren cherry-pick <project> <commit>`)",
              file=sys.stderr)
        return 2
    if args.list or args.move:
        print("creative_history: --list/--move name versions - a replay "
              "records a commit (`ren cherry-pick <project> <commit>`)",
              file=sys.stderr)
        return 2
    try:
        target = _resolve_commit(project, target_ref)
    except HistoryRefused as refused:
        print(f"creative_history: {refused}", file=sys.stderr)
        return 2
    try:
        dest_tracks = _read_dest_tracks(args.dest_tracks)
    except HistoryRefused as refused:
        print(f"creative_history: {refused}", file=sys.stderr)
        return 2
    try:
        replayed = uj.cherry_pick(project, target.get("id"),
                                  note=args.note or "",
                                  dest_tracks=dest_tracks)
    except uj.CherryPickRefused as refused:
        print(f"creative_history: {refused}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(replayed, indent=2, sort_keys=True, default=str))
    else:
        print(f"replayed {_short(target.get('id'))} -> "
              f"{_short(replayed.get('id'))} "
              f"[{replayed.get('timeline')}] {replayed.get('intent')}")
    return 0


def _cmd_revert(project: str, args) -> int:
    from library.tools import undo_journal as uj

    if args.timeline is not None or args.show is not None:
        print("creative_history: --timeline/--show select commits - "
              "a revert takes its target as a positional "
              "(`ren revert <project> <commit>`)",
              file=sys.stderr)
        return 2
    if args.list or args.move:
        print("creative_history: --list/--move name versions - a revert "
              "records a commit (`ren revert <project> <commit>`)",
              file=sys.stderr)
        return 2
    target_ref = args.commit or args.name
    if args.name is not None and args.commit is not None:
        print("creative_history: revert takes one commit, not two - "
              "`ren revert <project> <commit>`",
              file=sys.stderr)
        return 2
    if target_ref is None:
        print("creative_history: revert needs a commit "
              "(`ren revert <project> <commit>`)",
              file=sys.stderr)
        return 2
    try:
        target = _resolve_commit(project, target_ref)
    except HistoryRefused as refused:
        print(f"creative_history: {refused}", file=sys.stderr)
        return 2
    try:
        dest_tracks = _read_dest_tracks(args.dest_tracks)
    except HistoryRefused as refused:
        print(f"creative_history: {refused}", file=sys.stderr)
        return 2
    try:
        reverted = uj.revert_commit(project, target.get("id"),
                                    note=args.note or "",
                                    dest_tracks=dest_tracks)
    except uj.RevertRefused as refused:
        print(f"creative_history: {refused}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(reverted, indent=2, sort_keys=True, default=str))
    else:
        print(f"reverted {_short(target.get('id'))} -> "
              f"{_short(reverted.get('id'))} "
              f"[{reverted.get('timeline')}] {reverted.get('intent')}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="creative_history",
        description="Read (and name) a project's creative history: the "
                    "commits touch-ups record, the branches that point at "
                    "them, the tags that name versions, the replays and "
                    "reverts of earlier changes. Read-only, except "
                    "`tag <name> <commit>`, `cherry-pick <commit>` and "
                    "`revert <commit>`.")
    parser.add_argument("mode", choices=MODES)
    parser.add_argument("project", help="a project slug or path")
    parser.add_argument("name", nargs="?",
                        help="tag: the version name to record; "
                             "cherry-pick: the commit to replay; "
                             "revert: the commit to revert")
    parser.add_argument("commit", nargs="?",
                        help="tag: the commit the version points at "
                             "(whole id or unique prefix)")
    parser.add_argument("--timeline",
                        help="commits: only this timeline's chain (exact name)")
    parser.add_argument("--show",
                        help="commits: one commit by id or unique prefix; "
                             "branches: one branch by name")
    parser.add_argument("--list", action="store_true",
                        help="tag: list tags instead of recording one")
    parser.add_argument("--note", default="",
                        help="tag: why this commit is worth keeping; "
                             "cherry-pick: why it is replayed; "
                             "revert: why it is reverted")
    parser.add_argument("--dest-tracks", default=None,
                        help="cherry-pick, revert: JSON file with the "
                             "destination timeline's current track reads "
                             "(a tracks list, or an object with a tracks "
                             "key) - every replayed or restored change "
                             "must still hold there")
    parser.add_argument("--move", action="store_true",
                        help="tag: move a name that already points somewhere")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        project = _resolve_project(args.project)
    except HistoryRefused as refused:
        print(f"creative_history: {refused}", file=sys.stderr)
        return 2
    if args.mode not in ("cherry-pick", "revert") \
            and args.dest_tracks is not None:
        print("creative_history: --dest-tracks proves a replay or a "
              "revert - `ren cherry-pick <project> <commit> --dest-tracks "
              "<file>` or `ren revert <project> <commit> --dest-tracks "
              "<file>`",
              file=sys.stderr)
        return 2
    if args.mode == "commits":
        return _cmd_commits(project, args)
    if args.mode == "branches":
        return _cmd_branches(project, args)
    if args.mode == "cherry-pick":
        return _cmd_cherry_pick(project, args)
    if args.mode == "revert":
        return _cmd_revert(project, args)
    return _cmd_tag(project, args)


if __name__ == "__main__":
    raise SystemExit(main())
