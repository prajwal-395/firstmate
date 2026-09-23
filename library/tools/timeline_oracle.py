"""The timeline is the oracle: preconditions evaluated against the live cut.

The captain hands implementation over and what he is after is that the
pipeline "feel like collaborating with a co-editor". A co-editor looks
at the timeline in front of them. This module is that look: it answers
what the pipeline's records CLAIM against what the live timeline SHOWS,
and when the two disagree it reads the difference as the captain's
INTENT - never as drift, never as something to correct back.

What this builds on, and what it deliberately does not
------------------------------------------------------
* The READING is `reel_read.read_tracks` projected through
  `reel_read.rows_of` - the same row projection the promotion guard
  already runs live-against-live on every promote
  (`reel_replace_guard.snapshot_timeline`). No new reader is written;
  a second reader for the same question is how two records disagree.
* The COMPARISON is `reel_replace_guard.diff_rows` (via
  `round_diff.diff_reel`, which adds the later-only rows back and the
  re-render classification). That diff is proven in production use.
* The comparison NEVER joins on `unique_id`. The scout measured 0 of 26
  identities surviving a rebuild, so a `unique_id` join reports every
  clip as both added and removed - a total rewrite on every read, worse
  than useless. Identity here is name plus span, which is the diff's
  whole identity already.

READ-ONLY against his Resolve. Every call here is a getter
(`GetTimelineCount`, `GetTimelineByIndex`, `GetName`, `GetTrackCount`,
`GetTrackName`, `GetItemListInTrack`, and the per-item getters inside
`reel_read.clip_detail`). Nothing here opens or creates a project or
timeline, nothing renders or builds, and nothing moves the
current-timeline cursor: timelines are reached by index and matched by
EXACT name, never via `SetCurrentTimeline`. The row slice deliberately
passes no `resolve_project`, so no currency check runs - rows carry
names and spans, which are current-independent, while a currency proof
would need a cursor excursion. He may be working in Resolve while this
runs; a read that moves his cursor is a defect even if nothing else
breaks.

SCOPE: the evaluation path, and the description of what it finds. NOT
in scope: changing what the pipeline DOES about what it finds, any
repair or reconciliation behaviour, or any change to how preconditions
are declared. Detecting and describing is the deliverable; deciding is
later work and his call. A hand edit this reports is CARRIED, not
corrected - see `render_report`.

    python3 -m library.tools.timeline_oracle --timeline "Reel 09 - moment"
    python3 -m library.tools.timeline_oracle --timeline "Reel 09" \\
        --expected-rows /tmp/expected_rows.json --live-rows /tmp/live_rows.json

`tests/test_timeline_oracle.py`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Mapping

_HERE = Path(__file__).resolve()
if str(_HERE.parents[2]) not in sys.path:  # repo root, for direct execution
    sys.path.insert(0, str(_HERE.parents[2]))

from library.tools.resolve_lock import under_lease


class TimelineOracleError(RuntimeError):
    """The live timeline could not be read honestly. Fail closed."""


class TimelineNotFound(TimelineOracleError):
    """No timeline under that EXACT name. Never a prefix, never a guess."""


# ── Finding the timeline without touching the cursor ──────────────
#
# Reached by index and matched by EXACT name (AGENTS.md 5). The open
# timeline is never assumed and never set: `SetCurrentTimeline` does not
# appear in this module, and a grep test pins that.


def find_timeline_exact(project, name: str):
    """The timeline named exactly `name`, via `GetTimelineByIndex`.

    Raises `TimelineNotFound` naming what Resolve actually lists, so the
    next action is obvious. Read-only: no open, no create, no cursor
    move.
    """
    if not (name or "").strip():
        raise TimelineNotFound("no timeline name was given.")
    listed: list = []
    try:
        count = project.GetTimelineCount() or 0
    except Exception as unreadable:
        raise TimelineOracleError(
            f"the timeline list could not be read ({unreadable}); "
            f"refusing rather than reading half a project."
        ) from unreadable
    for index in range(1, count + 1):
        try:
            timeline = project.GetTimelineByIndex(index)
        except Exception:
            continue
        if timeline is None:
            continue
        try:
            timeline_name = timeline.GetName()
        except Exception:
            continue
        listed.append(timeline_name)
        if timeline_name == name:
            return timeline
    raise TimelineNotFound(
        f"no timeline named exactly {name!r}. Resolve lists: {listed}. "
        f"Exact names only - a prefix lands on a different timeline."
    )


# ── The live reading: rows, read-only, cursor untouched ───────────


@under_lease("read the oracle rows", exclusive=False)
def snapshot_live_rows(timeline) -> dict:
    """Every row of one LIVE timeline, as the guard sees it.

    A slice of the one enumeration: items are read once, in
    `reel_read.read_tracks`, and projected through `reel_read.rows_of`.
    `resolve_project` is deliberately NOT passed: rows carry names and
    spans, which read identically whichever timeline is current, while
    passing it would demand a cursor excursion to prove currency - the
    exact cursor move this module refuses to make. A caller that needs
    transforms takes the full `reel_read.read_reel` route instead, which
    proves currency through its own guarded setter.
    """
    from library.tools import reel_read

    try:
        tracks = reel_read.read_tracks(timeline)
        return reel_read.rows_of({"tracks": tracks})
    except reel_read.ReelReadError as unreadable:
        raise TimelineOracleError(
            f"the live timeline could not be read ({unreadable}); "
            f"refusing rather than judging what cannot be seen."
        ) from unreadable
    except Exception as unreadable:
        raise TimelineOracleError(
            f"the live timeline could not be read ({unreadable}); "
            f"refusing rather than judging what cannot be seen."
        ) from unreadable


def live_rows_of_tracks(tracks: list) -> dict:
    """The same projection for tracks already in hand (tests, files)."""
    from library.tools import reel_read

    return reel_read.rows_of({"tracks": tracks})


# ── Preconditions, evaluated against the live timeline ────────────
#
# The pipeline's `requirements` layer answers these against its own
# records (state, recorded, supplied). The two functions below answer
# the same questions against what is actually on the timeline, which is
# the truth when he has hand-edited it. They CHANGE nothing about how
# preconditions are declared and nothing about what the pipeline does
# with the answer - they are the evaluation path the later work calls.


def live_has_picture(live_rows: Mapping) -> bool:
    """Does the live timeline SHOW at least one video clip?

    This is the "a rough cut exists" precondition read off the screen
    rather than off the paperwork: records can claim a cut while the
    timeline shows none, or claim none while he has already cut one by
    hand. Either way the timeline wins.
    """
    for key, row in (live_rows or {}).items():
        if not str(key).startswith("video:"):
            continue
        if int((row or {}).get("count") or 0) > 0:
            return True
    return False


def live_clip_count(live_rows: Mapping) -> int:
    """Every video item on the live timeline, across rows."""
    total = 0
    for key, row in (live_rows or {}).items():
        if not str(key).startswith("video:"):
            continue
        total += int((row or {}).get("count") or 0)
    return total


def evaluate_precondition_against_live(
    name: str, expected_rows: Mapping, live_rows: Mapping
) -> dict:
    """One named precondition, judged against the live timeline.

    `name` is currently `"rough_cut_exists"` only - the one precondition
    this lane was asked to make evaluable. An unknown name RAISES rather
    than answering, because a check that does not exist is not a check
    that passes (`external_inputs.CHECKS` holds the same line).

    Returns plain data: whether the LIVE timeline satisfies it, what the
    records expected, and the hand-edit description beside it. The caller
    decides what to do; this reports.
    """
    if name != "rough_cut_exists":
        raise TimelineOracleError(
            f"unknown live precondition {name!r}; the only one this "
            f"module evaluates is 'rough_cut_exists'."
        )
    expected = live_has_picture(expected_rows or {})
    live = live_has_picture(live_rows or {})
    diff = describe_hand_edits(expected_rows or {}, live_rows or {})
    return {
        "precondition": name,
        "expected_picture": bool(expected),
        "live_picture": bool(live),
        "satisfied_by_live_timeline": bool(live),
        "records_agree_with_live": bool(expected) == bool(live),
        "hand_edits": diff["intents"],
        "changed": diff["changed"],
    }


# ── The hand edit as intent ───────────────────────────────────────
#
# `round_diff.diff_reel` IS `reel_replace_guard.diff_rows` with the
# later-only rows put back and the re-render classification applied -
# the guard's own diff pointed at stored snapshots instead of two live
# timelines. Pointing it at expected-versus-live gives the captain's
# hand edit in the same vocabulary a promotion refusal already prints.


def describe_hand_edits(expected_rows: Mapping, live_rows: Mapping) -> dict:
    """What he changed by hand, as INTENT - never as drift.

    Returns `{"changed", "intents", "reel_diff"}` where each intent is
    plain data (`kind`, `row`, `detail`, `sentence`) and each sentence
    is written for him to read: what he did, in his terms, carried
    rather than corrected. An identical timeline reports no intents and
    says so; a re-render at identical spans (same pictures, same places,
    different files) is not reported as a change at all.
    """
    from library.tools import round_diff

    reel_diff = round_diff.diff_reel(
        dict(expected_rows or {}), dict(live_rows or {})
    )
    intents: list = []
    for row in reel_diff.get("rows") or ():
        intents.extend(_intents_for_row(row))
    return {
        "changed": [row for row in (reel_diff.get("rows") or ())
                    if row.get("state") not in ("unchanged", "re-rendered")],
        "unchanged": not bool(reel_diff.get("changed")),
        "intents": intents,
        "reel_diff": reel_diff,
    }


def _span(entry: Mapping) -> str:
    return f"@{entry['start']}..{entry['end']} ({entry['duration']}f)"


def _intents_for_row(row: Mapping) -> list:
    """One row's change, as one or more intent sentences."""
    key = row.get("key", "")
    state = row.get("state", "")
    if state in ("unchanged", "re-rendered"):
        return []
    if state == "row added":
        return [{
            "kind": "row_added",
            "row": key,
            "detail": {
                "count": row.get("later_count"),
                "frames": row.get("later_frames"),
                "items": list(row.get("gained") or ()),
            },
            "sentence": (
                f"you added a {key} row "
                f"({row.get('later_count')} clip(s), "
                f"{row.get('later_frames')} frames) - carried as your "
                f"intent."
            ),
        }]
    if state == "row gone":
        gone = list(row.get("gone") or ())
        names = ", ".join(repr(item["name"]) for item in gone[:3])
        more = f", and {len(gone) - 3} more" if len(gone) > 3 else ""
        return [{
            "kind": "row_removed",
            "row": key,
            "detail": {"count": row.get("earlier_count"),
                       "items": gone},
            "sentence": (
                f"you removed the {key} row "
                f"({row.get('earlier_count')} clip(s): {names}{more}) - "
                f"carried as your intent."
            ),
        }]
    # state == "changed": name what moved, what arrived, what left.
    intents = []
    gone = list(row.get("gone") or ())
    gained = list(row.get("gained") or ())
    gone_by_name = {item["name"] for item in gone}
    gained_by_name = {item["name"] for item in gained}
    moved = [item for item in gained if item["name"] in gone_by_name]
    removed = [item for item in gone if item["name"] not in gained_by_name]
    added = [item for item in gained if item["name"] not in gone_by_name]
    if moved:
        names = ", ".join(repr(item["name"]) for item in moved[:3])
        more = f", and {len(moved) - 3} more" if len(moved) > 3 else ""
        intents.append({
            "kind": "moved",
            "row": key,
            "detail": {"items": moved},
            "sentence": (
                f"you moved {names}{more} on {key} "
                f"({row.get('earlier_count')} -> {row.get('later_count')} "
                f"clip(s)) - carried as your intent."
            ),
        })
    for item in removed:
        intents.append({
            "kind": "removed",
            "row": key,
            "detail": {"item": item},
            "sentence": (
                f"you removed {item['name']!r} {_span(item)} from {key} "
                f"- carried as your intent."
            ),
        })
    for item in added:
        intents.append({
            "kind": "added",
            "row": key,
            "detail": {"item": item},
            "sentence": (
                f"you added {item['name']!r} {_span(item)} on {key} - "
                f"carried as your intent."
            ),
        })
    if not intents:
        # Same names at different spans with no clean move/swap reading:
        # still his, still carried, described at row grain.
        intents.append({
            "kind": "retimed",
            "row": key,
            "detail": {
                "earlier_count": row.get("earlier_count"),
                "later_count": row.get("later_count"),
                "earlier_frames": row.get("earlier_frames"),
                "later_frames": row.get("later_frames"),
            },
            "sentence": (
                f"you retimed {key} "
                f"({row.get('earlier_count')} -> {row.get('later_count')} "
                f"clip(s), {row.get('earlier_frames')} -> "
                f"{row.get('later_frames')} frames) - carried as your "
                f"intent."
            ),
        })
    return intents


def render_report(evaluation: Mapping) -> str:
    """The evaluation, in the sentences he has to read.

    Every sentence says what HE did and that it is carried. No sentence
    says drift, correct, fix, or reconcile: this lane detects and
    describes, and deciding what to do about it is his call.
    """
    lines = []
    if evaluation.get("precondition"):
        live = "shows" if evaluation.get("live_picture") else "shows no"
        lines.append(
            f"The live timeline {live} picture "
            f"({evaluation.get('precondition')}: "
            f"{'satisfied' if evaluation.get('satisfied_by_live_timeline') else 'not satisfied'} "
            f"against the live cut)."
        )
        if not evaluation.get("records_agree_with_live", True):
            lines.append(
                "The pipeline records disagree with the live cut - the "
                "timeline wins, because it is what you are looking at."
            )
    intents = list(evaluation.get("hand_edits") or ())
    if not intents and not evaluation.get("changed"):
        lines.append("Nothing moved: the live cut matches the records.")
    for intent in intents:
        lines.append(intent.get("sentence", ""))
    return "\n".join(line for line in lines if line)


# ── Loading either side off disk ──────────────────────────────────


def load_rows(path: str | Path) -> dict:
    """Rows from a file: a bare rows dict, a `read_reel` result, or a round entry.

    Accepts what the pipeline actually writes so the expected side needs
    no new artifact: `reel_read.rows_of` output, a full `read_reel`
    result (which carries `tracks`), or a `round_version` reel entry
    (which carries `rows`). Anything else raises rather than reading as
    empty - an empty side reads exactly like a side that removed
    everything.
    """
    text = Path(path).read_text(encoding="utf-8")
    try:
        document = json.loads(text)
    except json.JSONDecodeError as broken:
        raise TimelineOracleError(
            f"{path} is not JSON ({broken}); refusing rather than "
            f"diffing against what cannot be read."
        ) from broken
    if not isinstance(document, dict):
        raise TimelineOracleError(
            f"{path} holds {type(document).__name__}, not an object; "
            f"refusing rather than diffing against what cannot be read."
        )
    if "tracks" in document:
        from library.tools import reel_read

        return reel_read.rows_of(document)
    for key in ("rows", "expected_rows", "live_rows"):
        rows = document.get(key)
        if isinstance(rows, dict):
            return dict(rows)
    # A bare rows mapping: every value carries items.
    if document and all(
        isinstance(value, dict) and "items" in value
        for value in document.values()
    ):
        return dict(document)
    raise TimelineOracleError(
        f"{path} carries no rows (no 'tracks', 'rows', 'expected_rows' "
        f"or 'live_rows', and not a bare rows mapping); refusing rather "
        f"than diffing against nothing."
    )


# ── CLI: read-only, and never while a build holds the project ─────


def _hold_active(project_folder) -> bool:
    if not project_folder:
        return False
    try:
        from library.tools import run_control

        return bool(run_control.hold_requested(str(project_folder)))
    except Exception:
        return False


def _resolve_for_cli():
    try:
        from library.tools import marker_feedback
    except Exception as exc:
        raise TimelineOracleError(
            f"Resolve support could not be loaded ({exc})."
        ) from exc
    try:
        _timeline, project = marker_feedback.current_timeline()
    except marker_feedback.ResolveUnavailable as exc:
        raise TimelineOracleError(f"cannot read Resolve: {exc}") from exc
    return project


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.timeline_oracle",
        description=(
            "Evaluate a precondition against the LIVE timeline and "
            "describe a hand edit as intent. Read-only: it changes no "
            "timeline and decides nothing."
        ),
    )
    parser.add_argument("--timeline", default=None,
                        help="the timeline's EXACT name (never a prefix)")
    parser.add_argument("--expected-rows", default=None,
                        help="JSON file carrying the expected rows "
                             "(rows dict, read_reel result, or round entry)")
    parser.add_argument("--live-rows", default=None,
                        help="JSON file carrying live rows INSTEAD of "
                             "reading Resolve (demonstration without a "
                             "running Resolve)")
    parser.add_argument("--precondition", default="rough_cut_exists",
                        help="the live precondition to evaluate "
                             "(only 'rough_cut_exists')")
    parser.add_argument("--project-folder", default=None,
                        help="pipeline project folder: refuses when a "
                             "build holds it")
    parser.add_argument("--out", default=None,
                        help="write the JSON evaluation here as well")
    args = parser.parse_args(argv)

    if _hold_active(args.project_folder):
        print("REFUSING: a build holds this project (pipeline.hold); a "
              "read taken mid-build is half a truth. Clear the hold or "
              "wait for the build, then re-run.", file=sys.stderr)
        return 4
    try:
        if args.live_rows:
            live = load_rows(args.live_rows)
            timeline_label = f"file:{args.live_rows}"
        else:
            if not args.timeline:
                print("error: --timeline names the live timeline, or pass "
                      "--live-rows to demonstrate off disk.",
                      file=sys.stderr)
                return 2
            project = _resolve_for_cli()
            timeline = find_timeline_exact(project, args.timeline)
            live = snapshot_live_rows(timeline)
            timeline_label = args.timeline
        expected = load_rows(args.expected_rows) if args.expected_rows else {}
        diff = describe_hand_edits(expected, live)
        evaluation = {
            "timeline": timeline_label,
            **evaluate_precondition_against_live(
                args.precondition, expected, live),
        }
        # The diff's own sentences plus the evaluation: one surface.
        evaluation["report"] = render_report(evaluation)
        _ = diff
    except (TimelineOracleError, TimelineNotFound) as exc:
        print(f"Cannot evaluate: {exc}", file=sys.stderr)
        return 1
    text = json.dumps(evaluation, indent=2, sort_keys=True, default=str)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(evaluation["report"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
