"""PLAN-010 measured on the round-2 planning briefs.

The punch list asks (PLAN-010) to make the available ACTIONS discoverable,
and the lane's measurement is: "on the round-2 planning briefs, how many
planner action choices name an operation that exists and is qualified,
before vs after, with the denominator".

WHAT THE BRIEFS ARE.

The round-2 planning briefs are the BASE-003 corpus: thirteen briefs
written against real footage, with the answer key sealed outside the
engine (`evaluation/corpus/sealed/`).  Round 1 was the eight-brief
acceptance set; the corpus is the second round, and it is the set whose
premises the footage actually carries.

WHAT A "PLANNER ACTION CHOICE" IS HERE.

A planner, working a brief, names operations.  This tool measures the
choices the ENGINE OFFERS a planner for each brief, because that is the
thing a discoverability change moves:

    before   the shipped search surface (`resolve-axi capabilities
             --search "<the brief's instruction>"`), as it stands on
             main: ten rows, ranked over technical vocabulary, each row
             carrying an id and a purpose and nothing else.
    after    the new action surface (`resolve-axi actions "<the brief's
             instruction>"`): the SUPPORTED operations, ranked, each with
             its card, plus the declared-but-not-routable operations
             with the blocker that keeps them out.

Three views are reported, and they are kept apart:

    first choice     the operation the surface puts first - the one a
                     planner reaches for.
    the menu         every operation the surface offers - what the
                     planner may choose from at all.
    the key's own    the operations the corpus's answer key names for
                     the briefs - what a planner is TOLD to name.

An operation's three states are kept apart too: it may not exist, it
may exist without being routable, and it may exist and be routable.  A
brief whose correct outcome is to report back names no operation at all,
so the relevance view is measured over the briefs that expect an edit.

    python3 evaluation/tools/plan010_actions.py [--json <path>]
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

sys.path.insert(0, str(Path(__file__).resolve().parent))

from corpus import load_sealed_briefs  # noqa: E402

from library.tools import action_discovery, edit_algebra  # noqa: E402
from library.tools import resolve_axi_core_commands as axi  # noqa: E402


def _before_menu(request: str) -> list[dict]:
    """The shipped search surface's answer, as it stands on main."""
    args = _Args(request)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        axi.cmd_capabilities(args)
    payload = json.loads(buffer.getvalue())
    return list(payload.get("matches") or ())


class _Args:
    """The arguments `capabilities --search` reads."""

    def __init__(self, request: str):
        self.operation = ""
        self.search = request
        self.references = False
        self.json = True


def _state(op_id: str) -> tuple[bool, bool]:
    """Whether an operation exists, and whether it is routable."""
    status = edit_algebra.status(op_id)
    if status is None:
        return False, False
    return True, bool(status.production_enabled)


def measure() -> dict:
    """The measurement, over every brief in the corpus."""
    brief_set = load_sealed_briefs()
    briefs = list(brief_set.get("briefs") or ())
    rows: list[dict] = []
    for brief in briefs:
        key = brief.get("oracle") or {}
        allowed = list(key.get("allowed_operations") or ())
        expected = list(key.get("expected_operations") or ())
        instruction = str(brief.get("instruction") or "")

        before = _before_menu(instruction)
        before_ids = [str(row.get("id") or "") for row in before]
        after = action_discovery.discover(instruction, limit=5)
        after_ids = [str(match["id"]) for match in after["matches"]]

        before_first = before_ids[0] if before_ids else ""
        after_first = after_ids[0] if after_ids else ""
        before_exists, before_qualified = _state(before_first)
        after_exists, after_qualified = _state(after_first)
        menu_states = [_state(op_id) for op_id in after_ids]
        offered = list(after_ids)
        for row in after["not_routable"]:
            offered.append(str(row["id"]))
            offered.extend(str(alt) for alt in row["supported_alternatives"])
        offered_states = [_state(op_id) for op_id in offered]
        rows.append({
            "brief": brief.get("id"),
            "expects_an_edit": bool(expected or allowed),
            "allowed": allowed,
            "expected": expected,
            "before_first": before_first,
            "before_first_exists": before_exists,
            "before_first_qualified": before_qualified,
            "before_first_allowed": before_first in allowed,
            "before_menu": before_ids,
            "before_menu_qualified": sum(1 for _e, q in
                                          [_state(op_id) for op_id in before_ids]
                                          if q),
            "before_menu_size": len(before_ids),
            "after_first": after_first,
            "after_first_exists": after_exists,
            "after_first_qualified": after_qualified,
            "after_first_allowed": after_first in allowed,
            "after_menu": after_ids,
            "after_menu_qualified": sum(1 for _e, q in menu_states if q),
            "after_menu_size": len(after_ids),
            "after_menu_has_allowed_qualified": any(
                op_id in allowed and q for op_id, (_e, q)
                in zip(after_ids, menu_states)),
            "after_offered": offered,
            "after_offered_qualified": sum(1 for _e, q in offered_states if q),
            "after_offered_has_allowed_qualified": any(
                op_id in allowed and q for op_id, (_e, q)
                in zip(offered, offered_states)),
            "not_routable": [row["id"] for row in after["not_routable"]],
            "not_routable_alternatives": {
                row["id"]: list(row["supported_alternatives"])
                for row in after["not_routable"]},
        })

    edit_rows = [row for row in rows if row["expects_an_edit"]]
    key_choices: list[str] = []
    for brief in briefs:
        key = brief.get("oracle") or {}
        key_choices.extend(str(op) for op in (key.get("expected_operations") or ()))
        key_choices.extend(str(op) for op in (key.get("allowed_operations") or ()))
    key_states = [_state(op_id) for op_id in key_choices]

    def count(rows_: list[dict], field: str) -> int:
        return sum(1 for row in rows_ if row[field])

    return {
        "briefs": len(rows),
        "edit_briefs": len(edit_rows),
        "key_choices": len(key_choices),
        "key_choices_exist": sum(1 for exists, _q in key_states if exists),
        "key_choices_qualified": sum(1 for _e, q in key_states if q),
        "before_first_qualified": count(rows, "before_first_qualified"),
        "before_first_allowed": count(edit_rows, "before_first_allowed"),
        "before_menu_qualified": sum(row["before_menu_qualified"]
                                     for row in rows),
        "before_menu_rows": sum(row["before_menu_size"] for row in rows),
        "after_first_qualified": count(rows, "after_first_qualified"),
        "after_first_allowed": count(edit_rows, "after_first_allowed"),
        "after_menu_qualified": sum(row["after_menu_qualified"]
                                    for row in rows),
        "after_menu_rows": sum(row["after_menu_size"] for row in rows),
        "after_menu_has_allowed_qualified": count(
            edit_rows, "after_menu_has_allowed_qualified"),
        "after_offered_qualified": sum(row["after_offered_qualified"]
                                       for row in rows),
        "after_offered_rows": sum(len(row["after_offered"]) for row in rows),
        "after_offered_has_allowed_qualified": count(
            edit_rows, "after_offered_has_allowed_qualified"),
        "rows": rows,
    }


def render(report: dict) -> str:
    """The report, with every denominator named."""
    lines: list[str] = []
    rows = report["rows"]
    lines.append("PLAN-010: the action surface, measured on the round-2 "
                 "planning briefs")
    lines.append("")
    lines.append(f"briefs: {report['briefs']} "
                 f"({report['edit_briefs']} expect an edit, "
                 f"{report['briefs'] - report['edit_briefs']} expect a "
                 "report back and name no operation)")
    lines.append("")
    lines.append("| brief | before: first choice | ok | after: first "
                 "choice | ok | allowed by the key |")
    lines.append("|---|---|---|---|---|---|")
    for row in rows:
        lines.append(
            f"| `{row['brief']}` | `{row['before_first'] or 'none'}` | "
            f"{'yes' if row['before_first_qualified'] else 'no'} | "
            f"`{row['after_first'] or 'none'}` | "
            f"{'yes' if row['after_first_qualified'] else 'no'} | "
            f"{'yes' if row['after_first_allowed'] else 'no'} |")
    lines.append("")
    lines.append("THE NAMED MEASUREMENT: planner action choices that name "
                 "an operation that EXISTS and is QUALIFIED")
    lines.append("")
    lines.append("| view | before | after |")
    lines.append("|---|---|---|")
    lines.append(
        f"| first choice per brief | {report['before_first_qualified']}"
        f"/{report['briefs']} | {report['after_first_qualified']}"
        f"/{report['briefs']} |")
    lines.append(
        f"| the menu offered per brief | {report['before_menu_qualified']}"
        f"/{report['before_menu_rows']} | {report['after_menu_qualified']}"
        f"/{report['after_menu_rows']} |")
    lines.append(
        f"| the key's own declared choices | {report['key_choices_qualified']}"
        f"/{report['key_choices']} | unchanged (the key is sealed) |")
    lines.append("")
    lines.append("THE GUARD: the choice the surface offers is also one the "
                 "brief's key allows (edit briefs only)")
    lines.append("")
    lines.append(f"| view | before | after |")
    lines.append("|---|---|---|")
    lines.append(
        f"| first choice is among the allowed choices | "
        f"{report['before_first_allowed']}/{report['edit_briefs']} | "
        f"{report['after_first_allowed']}/{report['edit_briefs']} |")
    lines.append(
        f"| the menu holds an allowed, qualified choice | - | "
        f"{report['after_menu_has_allowed_qualified']}"
        f"/{report['edit_briefs']} |")
    lines.append(
        f"| everything the surface offers holds an allowed, qualified "
        f"choice | - | {report['after_offered_has_allowed_qualified']}"
        f"/{report['edit_briefs']} |")
    lines.append("")
    lines.append("The LAST row counts the alternatives the surface names "
                 "beside an operation it refuses: a planner reading the "
                 "card is offered those too.")
    lines.append("")
    lines.append("A brief whose correct outcome is to report back names no "
                 "operation, so it is scored on existence and "
                 "qualification only.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="plan010_actions.py",
        description="Measure the action surface on the round-2 briefs.")
    parser.add_argument("--json", default=None,
                        help="write the whole measurement as JSON")
    args = parser.parse_args(argv)
    report = measure()
    print(render(report))
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2) + "\n",
                                   encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
