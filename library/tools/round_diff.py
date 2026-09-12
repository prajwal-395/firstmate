"""What changed between round 3 and round 4, in the captain's terms.

This is the payoff of the version object, and the reason the versioning
question mattered at all. Without it a round is SUPERSEDED: the new reel
is there and the old one is gone, and the only way to know what a round
did is to remember. With it a round is REVIEWABLE.

It is also nearly free, which is why it is a module and not a project.
The scout report measured it
(`data/vep-can-it-hold-up-in-a-real-editing-workflow` §3): the only
differ in the repository joins timeline items on `unique_id` and 0 of 26
identities survive a rebuild, so it reports every clip as both added and
removed. But `reel_replace_guard.diff_rows` over `reel_read.rows_of`
- the diff the promotion guard already runs live-against-live on every
promote - runs perfectly well over two STORED snapshots, off disk, in
milliseconds, with no Resolve. `round_version` stores exactly those rows
per reel per round, so this module is the guard's own diff pointed at
two rounds instead of two live timelines.

One diff, two callers
---------------------
Nothing new is computed here. `diff_rows` is the guard's, `rows_of` is
`reel_read`'s, and the row key (`"video:Semantic"`) is the vocabulary the
refusal messages already print - so a row named in a round diff and a row
named in a promotion refusal are the same row, spelled the same way. A
second differ for the same question is how two records come to disagree.

What it reports, and what it deliberately does not
--------------------------------------------------
Per reel, per row: items and frames on each side, and the items present
in one and not the other. A reel promoted in one round and not the other
is reported as such - that is a real answer ("round 4 did not touch Reel
13"), not a gap.

It does NOT report WHY anything changed. The rows say a Semantic row
went from 2 items to 4; the feedback the round was opened by is printed
beside it, and joining the two is the reader's judgement. A tool that
guessed which note caused which row would be inventing a finding.

The standing limit is the guard's own, stated in its docstring: a
substitution that keeps every item name and span is invisible to a
span-based diff. That is the limit of what a row snapshot can see, not a
new hole.

`tests/test_round_diff.py`.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping
from pathlib import Path

_HERE = Path(__file__).resolve()
if str(_HERE.parents[2]) not in sys.path:      # repo root, direct execution
    sys.path.insert(0, str(_HERE.parents[2]))

UNCHANGED = "unchanged"
CHANGED = "changed"
RERENDERED = "re-rendered"
ADDED_ROW = "row added"
LOST_ROW = "row gone"


def _spans(row) -> list:
    return [(item["start"], item["end"], item["duration"])
            for item in (row or {}).get("items") or ()]


def _is_rerender(earlier, later) -> bool:
    """Same pictures, same places, different files.

    Every overlay row in this pipeline is a rendered `.mov` whose
    filename carries a content digest (`subtitle_segment_id`,
    `motion_graphics_plan`), so a rebuild that changed nothing about
    the plan still lands 34 new filenames at 34 identical spans - and
    a plain name diff reports that as 34 items gone and 34 new. Every
    round diff would then be mostly noise, and noise is how a real
    change goes unread.

    A row is a RE-RENDER exactly when the spans match one for one, in
    order. That is a measurement of the two snapshots, not a reading of
    the filenames: no naming scheme is parsed, so a row of camera
    footage that genuinely did not move reports the same way, correctly.
    Whether the PIXELS differ is beyond what a span snapshot can see -
    the diff says the placement is identical and says nothing it cannot
    measure.
    """
    return bool(_spans(earlier)) and _spans(earlier) == _spans(later)


def diff_reel(earlier_rows: Mapping, later_rows: Mapping) -> dict:
    """One reel, two rounds: every row that differs, and how.

    `reel_replace_guard.diff_rows` answers for every row the EARLIER
    side has - rows only the later side has are new features, which the
    guard rightly ignores because it is asked about loss. A round diff
    is asked about CHANGE, so the rows only the later side carries are
    added back here rather than a second differ being written.
    """
    from library.tools import reel_replace_guard as guard

    verdicts = guard.diff_rows(dict(earlier_rows or {}),
                               dict(later_rows or {}))
    rows = []
    for verdict in verdicts:
        later = (later_rows or {}).get(verdict["key"]) or {}
        earlier = (earlier_rows or {}).get(verdict["key"]) or {}
        if verdict["lost_row"]:
            state = LOST_ROW
        elif (verdict["retired_count"] == verdict["incoming_count"]
              and verdict["retired_frames"] == verdict["incoming_frames"]
              and not verdict["missing"]):
            state = UNCHANGED
        elif _is_rerender(earlier, later):
            state = RERENDERED
        else:
            state = CHANGED
        gained = []
        if state != LOST_ROW:
            earlier_items = {
                (item["name"], item["start"], item["end"])
                for item in ((earlier_rows or {})
                             .get(verdict["key"], {}).get("items") or ())}
            gained = [item for item in (later.get("items") or ())
                      if (item["name"], item["start"], item["end"])
                      not in earlier_items]
        rows.append({
            "key": verdict["key"],
            "name": verdict["name"],
            "state": state,
            "earlier_count": verdict["retired_count"],
            "later_count": verdict["incoming_count"],
            "earlier_frames": verdict["retired_frames"],
            "later_frames": verdict["incoming_frames"],
            "gone": list(verdict["missing"]),
            "gained": gained,
        })
    for key, row in sorted((later_rows or {}).items()):
        if key in (earlier_rows or {}):
            continue
        rows.append({
            "key": key, "name": row.get("name", ""), "state": ADDED_ROW,
            "earlier_count": None, "later_count": row.get("count"),
            "earlier_frames": None, "later_frames": row.get("frames"),
            "gone": [], "gained": list(row.get("items") or ()),
        })
    return {"rows": rows,
            "changed": [row for row in rows
                        if row["state"] not in (UNCHANGED, RERENDERED)],
            "rerendered": [row for row in rows
                           if row["state"] == RERENDERED]}


def diff_rounds(project_folder, earlier: int, later: int) -> dict:
    """Everything that changed between two rounds of this project.

    Both rounds must exist; a missing one RAISES rather than diffing
    against nothing, because an empty side reads exactly like a round
    that removed everything.
    """
    from library.tools import round_version

    document = round_version.read_rounds(project_folder)
    by_number = {entry.get("round"): entry
                 for entry in document.get("rounds") or ()}
    for number in (earlier, later):
        if number not in by_number:
            raise ValueError(
                f"round {number} is not recorded in this project "
                f"(recorded: {sorted(n for n in by_number if n)}). "
                f"`round-diff --backfill` reconstructs the rounds "
                f"already in the project's git history.")
    first, second = by_number[earlier], by_number[later]
    first_reels = first.get("reels") or {}
    second_reels = second.get("reels") or {}
    reels = {}
    for name in sorted(set(first_reels) | set(second_reels)):
        if name not in first_reels:
            reels[name] = {"state": "first built in this round",
                           "rows": [], "changed": []}
            continue
        if name not in second_reels:
            reels[name] = {"state": "not rebuilt in this round",
                           "rows": [], "changed": []}
            continue
        entry = diff_reel(first_reels[name].get("rows") or {},
                          second_reels[name].get("rows") or {})
        entry["state"] = (UNCHANGED if not entry["changed"]
                          else "rebuilt and changed")
        entry["built_with"] = second_reels[name].get("built_with", "")
        entry["promoted_at"] = second_reels[name].get("promoted_at", "")
        reels[name] = entry
    return {"earlier": first, "later": second, "reels": reels}


def row_line(row: Mapping) -> str:
    """One row's change, in one line.

    Public because `variant_choice.render_comparison` renders the same
    measurement for two VERSIONS of a reel rather than two rounds of
    one, and two spellings of one row diff is how a reader comes to
    believe they are different measurements."""
    if row["state"] == LOST_ROW:
        return (f"      {row['key']}: {row['earlier_count']} item(s) "
                f"-> the row is gone "
                f"({row['earlier_frames']} frames)")
    if row["state"] == ADDED_ROW:
        return (f"      {row['key']}: a new row - {row['later_count']} "
                f"item(s), {row['later_frames']} frames")
    return (f"      {row['key']}: {row['earlier_count']} -> "
            f"{row['later_count']} item(s), "
            f"{row['earlier_frames']} -> {row['later_frames']} frames")


def render(diff: Mapping, show_items: int = 3) -> str:
    """The round diff, in the sentences the captain has to read."""
    earlier, later = diff["earlier"], diff["later"]
    lines = [
        f"── Round {earlier.get('round')} -> round {later.get('round')} ──",
        (f"  Round {later.get('round')} opened "
         f"{later.get('opened_at') or '(at the first build)'} - "
         f"{later.get('why', '')}"),
    ]
    asked = later.get("reels_asked") or []
    if asked:
        lines.append(
            f"  {len(later.get('opened_by') or [])} ask(s) from the "
            f"captain, on: {', '.join(asked)}")
    touched = [name for name, entry in diff["reels"].items()
               if entry["state"] not in ("not rebuilt in this round",)]
    lines.append(
        f"  {len(touched)} reel(s) built in round {later.get('round')}, "
        f"of {len(diff['reels'])} the two rounds name between them.")
    for name in sorted(diff["reels"]):
        entry = diff["reels"][name]
        if entry["state"] == "not rebuilt in this round":
            lines.append(f"    {name}: not rebuilt in round "
                         f"{later.get('round')} - unchanged since round "
                         f"{earlier.get('round')}")
            continue
        if entry["state"] == "first built in this round":
            lines.append(f"    {name}: first built in round "
                         f"{later.get('round')}")
            continue
        rerendered = entry.get("rerendered") or []
        if entry["state"] == UNCHANGED:
            lines.append(
                f"    {name}: rebuilt, and nothing moved"
                + (f" ({len(rerendered)} row(s) re-rendered at "
                   f"identical spans)" if rerendered else ""))
            continue
        lines.append(f"    {name}: {len(entry['changed'])} row(s) "
                     f"changed")
        for row in rerendered:
            lines.append(
                f"      {row['key']}: {row['later_count']} item(s) "
                f"re-rendered at identical spans - nothing moved")
        for row in entry["changed"]:
            lines.append(row_line(row))
            for item in row["gone"][:show_items]:
                lines.append(
                    f"        gone:   {item['name']!r} "
                    f"@{item['start']}..{item['end']} "
                    f"({item['duration']}f)")
            if len(row["gone"]) > show_items:
                lines.append(f"        ... and {len(row['gone']) - show_items} "
                             f"more gone")
            for item in row["gained"][:show_items]:
                lines.append(
                    f"        new:    {item['name']!r} "
                    f"@{item['start']}..{item['end']} "
                    f"({item['duration']}f)")
            if len(row["gained"]) > show_items:
                lines.append(
                    f"        ... and {len(row['gained']) - show_items} "
                    f"more new")
    return "\n".join(lines)


def last_two(project_folder) -> tuple:
    """The last two rounds that actually PROMOTED something.

    A round the captain opened with feedback that has not been built
    yet holds no reels, so diffing against it would report every reel as
    missing. The two most recent rounds with reels in them is what "the
    last two rounds" means to a reader.
    """
    from library.tools import round_version

    numbers = [entry.get("round")
               for entry in round_version.read_rounds(project_folder)
               .get("rounds") or ()
               if entry.get("reels")]
    if len(numbers) < 2:
        raise ValueError(
            f"this project has {len(numbers)} round(s) with a promoted "
            f"reel in them, so there is no pair to diff. "
            f"`round-diff --backfill` reconstructs the rounds already "
            f"in the project's git history.")
    return numbers[-2], numbers[-1]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.round_diff",
        description="What changed between two rounds. Read-only, off "
                    "disk; never touches Resolve.")
    parser.add_argument("project", help="Path to the project folder")
    parser.add_argument("--from", dest="earlier", type=int, default=None)
    parser.add_argument("--to", dest="later", type=int, default=None)
    parser.add_argument("--backfill", action="store_true",
                        help="reconstruct rounds from the committed "
                             "timeline snapshots first")
    parser.add_argument("--list", action="store_true",
                        help="list the rounds instead of diffing")
    args = parser.parse_args(argv)
    from library.tools import round_version

    if args.backfill:
        report = round_version.backfill(args.project)
        print(f"Reconstructed {report['reels']} reel promotion(s) across "
              f"{report['rounds']} round(s) from {report['commits']} "
              f"commit(s).")
    if args.list:
        print(round_version.render(round_version.read_rounds(args.project)))
        return 0
    if args.earlier is None or args.later is None:
        try:
            earlier, later = last_two(args.project)
        except ValueError as thin:
            print(str(thin))
            return 1
        args.earlier = args.earlier if args.earlier is not None else earlier
        args.later = args.later if args.later is not None else later
    try:
        diff = diff_rounds(args.project, args.earlier, args.later)
    except ValueError as missing:
        print(str(missing))
        return 1
    print(render(diff))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
