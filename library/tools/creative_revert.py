"""Revert a creative commit: record its inverse as a new commit.

The `ren revert` verb over the commit graph `undo_journal` records -
reached as `python3 -m library.tools.creative_revert <project>
<commit> [...]`, one mode per verb the way `resolve_axi ownership`
is one mode of its module.

The commit id is WHOLE, not a prefix: unique-prefix addressing
belongs to the history reads, and this verb stays exact so the two
never duplicate addressing logic. List what is recorded with the
history reads; the id is the commit directory's name under
`pipeline_output/review/commits/`.

A revert stands on the timeline's head, so history only moves
forward: the reverted commit stays readable beside its revert.
"""

from __future__ import annotations

import argparse
import json
import sys


def _resolve_project(ref: str) -> str:
    """A project slug or path to its folder, the `perf_ledger` shape."""
    from library.tools.project_registry import get_project, resolve_project_path
    found = resolve_project_path(ref)
    if found is not None:
        return str(found.parent)
    config = get_project(ref)
    return str(config._project_root)


def main(argv=None) -> int:
    from library.tools import undo_journal as uj

    parser = argparse.ArgumentParser(
        prog="ren revert",
        description="Revert a creative commit: record its inverse as a "
                    "new commit standing on the timeline's head. "
                    "The commit id is whole - see the module docstring.")
    parser.add_argument("project", help="a project slug or path")
    parser.add_argument("commit", help="the whole commit id to revert")
    parser.add_argument("--note", default="",
                        help="why the commit is being reverted")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        project = _resolve_project(args.project)
    except FileNotFoundError as missing:
        print(f"creative_revert: no project {args.project!r} - pass a "
              f"project slug or a path to a folder holding project.yaml "
              f"({missing})", file=sys.stderr)
        return 2
    try:
        reverted = uj.revert_commit(project, args.commit,
                                    note=args.note or "")
    except uj.UndoRefused as missing:
        print(f"creative_revert: {missing.render()}", file=sys.stderr)
        return 2
    except uj.RevertRefused as refused:
        print(f"creative_revert: {refused}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(reverted, indent=2, sort_keys=True, default=str))
    else:
        print(f"reverted {args.commit[:12]} -> "
              f"{str(reverted.get('id') or '')[:12]} "
              f"[{reverted.get('timeline')}] {reverted.get('intent')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
