"""PR enumeration from the edit ledger, read-only (a6).

Baseline rec #4 (`data/vep-where-the-reel-wall-clock-goes/report.md`):
the 09-18 captions fix needed a hand-enumerated 134-card BEFORE/AFTER
PR body plus a correction round trip (31 vs 30 timelines). The ledger
(`library/tools/edit_ledger.py`) already carries every row of a change -
op, anchor (which card/placement), params (the after value), reel scope -
so a worker pastes this verb's output instead of enumerating by hand.

Read only: this renders `edit_ledger.load_rows` and changes no schema,
no file, no timeline. `tests/unit/context/test_ledgers.py`.
"""

from __future__ import annotations

import sys

UNSCOPED_HEADING = "Every reel in scope"


def _q(value: str) -> str:
    """The hand-written 1209 convention: single quotes, doubling to
    double quotes where the value itself carries an apostrophe - so
    the generated line pastes without the worker re-quoting it."""
    if "'" in value and '"' not in value:
        return f'"{value}"'
    return f"'{value}'"


def render_row(row: dict) -> str:
    """One PR-body bullet for one ledger row, naming before and after.

    `caption_fix` and `redraw_closer` are the two ops that carry both
    sides: the anchor names the before, the params name the after.
    Every other op carries only the held value, so it renders as a
    single SET/ISOLATE/GRADE line - inventing a before it never
    recorded would be the confidently-wrong enumeration this verb
    exists to stop. An op outside the vocabulary still renders BY
    NAME rather than vanishing: a silently dropped row is the K3
    defect back again.
    """
    op = row.get("op")
    anchor = row.get("anchor") or {}
    phrase = anchor.get("phrase", "") if anchor.get("kind") == "words" \
        else "the whole reel"
    params = row.get("params") or {}
    if op == "caption_fix":
        return (f"- BEFORE {_q(phrase)}  ->  "
                f"AFTER {_q(params.get('replacement', ''))}")
    if op == "redraw_closer":
        return (f"- BEFORE the closer opens on "
                f"{_q(params.get('from_phrase', ''))}  ->  "
                f"AFTER it opens on {_q(phrase)}")
    if op == "drop_fragment":
        return (f"- REMOVED {_q(phrase)} (everything after moves up)")
    if op == "span_retime":
        return (f"- TRIM: the {params.get('edge')} of the span speaking "
                f"{_q(phrase)} sits on those words' own edge")
    if op == "transform_override":
        return (f"- FRAMING: wherever the speech says {_q(phrase)}, "
                f"{params.get('property')} holds {params.get('value')}")
    if op == "voice_isolation":
        return (f"- ISOLATE: voice isolation on audio "
                f"{params.get('track')} at {params.get('amount')}")
    if op == "clip_lut":
        return (f"- GRADE: LUT {params.get('lut')!r} on node "
                f"{params.get('node', 1)} wherever the speech says "
                f"{_q(phrase)}")
    if op == "retime":
        percent = params.get("percent")
        segments = params.get("segments")
        what = (f"at {percent}%" if percent is not None
                else f"over {len(segments)} segment(s)"
                if segments is not None else "at the stated speed")
        return f"- RETIME: the words saying {_q(phrase)} play {what}"
    if op == "grade":
        route = next((key for key in ("lut", "cdl", "drx")
                      if key in params), "the stated route")
        return (f"- GRADE: the clips speaking {_q(phrase)} take "
                f"{route} {params.get(route, '')!r}")
    if op == "angle_plan":
        camera = params.get("camera") or "the planned angle"
        return (f"- ANGLE: the clips speaking {_q(phrase)} hold "
                f"{camera}")
    if op == "plan_change":
        values = ", ".join(
            f"{key} {value.get('value')!r} {value.get('unit', '')}".rstrip()
            for key, value in (params.get("values") or {}).items())
        where = (f"where the speech says {_q(phrase)}"
                 if anchor.get("kind") == "words" else "across the whole reel")
        return (f"- PLAN: {params.get('operation_type')} for "
                f"{params.get('owner')} {where}: {values}")
    return f"- {op}: {_q(phrase)} -> {params!r}"


def render_enumeration(rows: list, reel_prefixes: tuple = ()) -> str:
    """The per-item enumeration section of a PR body, from ledger rows.

    Groups hold ledger order: unscoped rows (they hold on every reel)
    under `UNSCOPED_HEADING` first, then one section per reel scope in
    first-seen order. `reel_prefixes` narrows to a change: unscoped
    rows are ALWAYS kept - they hold on the filtered reels too, and a
    filter that dropped them would silently lose decisions.
    """
    kept = []
    for row in rows or []:
        scope = row.get("reel")
        if not reel_prefixes:
            kept.append(row)
        elif scope is None:
            kept.append(row)
        elif any(scope.startswith(prefix)
                 for prefix in reel_prefixes):
            kept.append(row)
    unscoped = [row for row in kept if row.get("reel") is None]
    scoped_order: list = []
    for row in kept:
        scope = row.get("reel")
        if scope is not None and scope not in scoped_order:
            scoped_order.append(scope)
    lines = [f"## Full enumeration ({len(kept)} ledger row(s))", ""]
    if unscoped:
        lines.append(f"### {UNSCOPED_HEADING} ({len(unscoped)})")
        lines += [render_row(row) for row in unscoped]
        lines.append("")
    for scope in scoped_order:
        group = [row for row in kept if row.get("reel") == scope]
        lines.append(f"### {scope} ({len(group)})")
        lines += [render_row(row) for row in group]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main(argv=None) -> int:
    import argparse as _argparse

    parser = _argparse.ArgumentParser(
        description="Generate the per-item enumeration section of a PR "
                    "body from the edit ledger (read only).")
    parser.add_argument("project",
                        help="Project slug, or an absolute path to the "
                             "project directory")
    parser.add_argument("--reel", action="append", default=[],
                        metavar="PREFIX",
                        help="Only rows scoped to this reel-name prefix "
                             "(plus unscoped rows, which hold "
                             "everywhere). Repeatable")
    args = parser.parse_args(argv)

    from library.tools import edit_ledger as _ledger

    try:
        rows = _ledger.load_rows(args.project)
    except _ledger.EditLedgerError as exc:
        print(f"REFUSED\n\n{exc}\n")
        return 1
    if not rows:
        print(f"No edit-ledger rows in {args.project}/external/.")
        return 0
    sys.stdout.write(render_enumeration(
        rows, reel_prefixes=tuple(args.reel)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
