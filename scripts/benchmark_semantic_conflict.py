#!/usr/bin/env python3
"""Measure semantic conflict detection on a Ren-owned scratch reel.

The measurement the punch list asks for (VC-005): pairs of edits on a
scratch reel - two independent edits, two overlapping edits, and a hand
edit beside a Ren edit - and what the detection says about each pair,
with denominators.

The corpus is the RECORDED state of a Ren-owned scratch project in the
machine's shadow store (`library/tools/timeline_shadow.py`): every
generation is a read of a timeline, and the difference between two
generations is a change nobody has to guess at. The store is opened
READ-ONLY: a measurement must not write to the state it measures.

What is measured
----------------
The rule that changed is the CARRY. A patch is planned against one
recorded generation; when a hand edit lands between that generation
and the head, the patch has to move onto the head over it. The old
rule refused EVERY such carry, because nothing could say what the hand
edit met. Now the two are compared - source intervals, timeline
regions, targets, property keys - and the patch moves on when they are
provably disjoint.

The denominator is every (hand edit, later change) pair in the corpus:
under the old rule each of them was a refusal, and under the new rule
each is either a refusal that names both sides or a carry that lands
with the hand edit standing.

    before   every one refused
    after    the provably disjoint ones allowed, the rest refused

The three named cases are read out of the same corpus: two independent
changes, two overlapping changes, and a hand edit beside a Ren edit.

Usage:
    python3 scripts/benchmark_semantic_conflict.py
    python3 scripts/benchmark_semantic_conflict.py --project "Ren Scratch - breaktest"
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
if str(_HERE.parents[1]) not in sys.path:
    sys.path.insert(0, str(_HERE.parents[1]))

from library.tools import semantic_conflict

DEFAULT_PROJECT = "Ren Qualification"


def _shadow_db() -> Path:
    from library.tools import timeline_shadow as shadow

    return Path(shadow.default_db_path())


def _generations(db, project: str, timeline: str | None):
    """The recorded generations of a project's timelines, in order."""
    if timeline:
        rows = db.execute(
            "SELECT timeline_id, timeline_name, generation, source, hash "
            "FROM generations WHERE project = ? AND timeline_name = ? "
            "ORDER BY timeline_id, generation", (project, timeline)).fetchall()
    else:
        rows = db.execute(
            "SELECT timeline_id, timeline_name, generation, source, hash "
            "FROM generations WHERE project = ? "
            "ORDER BY timeline_id, generation", (project,)).fetchall()
    by_timeline = {}
    for timeline_id, name, generation, source, digest in rows:
        by_timeline.setdefault((timeline_id, name), []).append(
            {"generation": generation, "source": source, "hash": digest})
    return by_timeline


def _snapshot(db, digest: str):
    row = db.execute("SELECT body FROM snapshots WHERE hash = ?",
                     (digest,)).fetchone()
    if row is None:
        return None
    return json.loads(row[0])


def _origin(source: str) -> str:
    return (semantic_conflict.ORIGIN_REN if source == "patch"
            else semantic_conflict.ORIGIN_CAPTAIN)


def _change_between(db, before_hash, after_hash, version, origin):
    """The change between two recorded states."""
    before = _snapshot(db, before_hash)
    after = _snapshot(db, after_hash)
    if before is None or after is None:
        return []
    return semantic_conflict.edits_between(
        before, after, origin=origin, version=version)


def _verdict(report) -> str:
    """One word for a pair of change sets."""
    if report.conflicts:
        return "conflict"
    if report.unproven:
        return "unproven"
    return "independent"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 scripts/benchmark_semantic_conflict.py",
        description="Measure semantic conflict detection on recorded "
                    "generations of a Ren-owned scratch project.")
    parser.add_argument("--project", default=DEFAULT_PROJECT,
                        help="the Resolve project whose recorded "
                             "generations are measured")
    parser.add_argument("--timeline", default="",
                        help="one timeline's exact name (default: all)")
    parser.add_argument("--db", default="",
                        help="the shadow store to read (default: the "
                             "machine's own)")
    args = parser.parse_args(argv)

    from library.tools import timeline_shadow as shadow

    path = Path(args.db) if args.db else Path(shadow.default_db_path())
    if not path.exists():
        print(f"no shadow store at {path}", file=sys.stderr)
        return 2
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    timelines = _generations(db, args.project, args.timeline or None)
    if not timelines:
        print(f"no recorded generations for {args.project!r}", file=sys.stderr)
        return 2

    all_pairs = {"pairs": 0, "independent": 0, "conflict": 0, "unproven": 0}
    carries = {"pairs": 0, "allowed": 0, "conflict": 0, "unproven": 0}
    changes = 0
    named = {"independent": None, "overlapping": None, "hand_vs_ren": None}

    for (timeline_id, timeline_name), generations in sorted(timelines.items()):
        recorded = []
        previous = None
        for row in generations:
            generation = row["generation"]
            edit_set = []
            if previous is not None:
                edit_set = _change_between(
                    db, previous["hash"], row["hash"],
                    f"generation {generation}", _origin(row["source"]))
            if edit_set:
                changes += 1
                recorded.append({
                    "generation": generation,
                    "source": row["source"],
                    "origin": _origin(row["source"]),
                    "edits": edit_set,
                })
            previous = row

        for left in range(len(recorded)):
            for right in range(left + 1, len(recorded)):
                one, other = recorded[left], recorded[right]
                report = semantic_conflict.detect(
                    semantic_conflict.EditSet.of(
                        f"generation {one['generation']}", one["origin"],
                        one["edits"]),
                    semantic_conflict.EditSet.of(
                        f"generation {other['generation']}", other["origin"],
                        other["edits"]))
                verdict = _verdict(report)
                all_pairs["pairs"] += 1
                all_pairs[verdict] += 1
                if (one["source"] == "observed"
                        and right == left + 1):
                    # A hand edit and the next change after it: the
                    # carry case - what Ren's in-flight work does when
                    # the captain has just edited the timeline by hand.
                    carries["pairs"] += 1
                    if verdict == "independent":
                        carries["allowed"] += 1
                    else:
                        carries[verdict] += 1
                if verdict == "independent" and named["independent"] is None:
                    named["independent"] = (timeline_name, one, other, report)
                elif verdict == "conflict" and named["overlapping"] is None:
                    named["overlapping"] = (timeline_name, one, other, report)
                if (verdict == "conflict"
                        and one["source"] == "observed"
                        and other["source"] == "patch"
                        and named["hand_vs_ren"] is None):
                    named["hand_vs_ren"] = (timeline_name, one, other, report)

    print(f"corpus: {args.project!r} ({len(timelines)} recorded timelines, "
          f"{changes} recorded changes, {all_pairs['pairs']} pairs of "
          f"changes)")
    print(f"  classified: {all_pairs['independent']} independent, "
          f"{all_pairs['conflict']} conflicting, "
          f"{all_pairs['unproven']} unproven")
    print(f"carries over a hand edit (the next change after it): "
          f"{carries['pairs']} pairs")
    print(f"  before: 0 of {carries['pairs']} allowed "
          f"(every one refused: nothing could say what the hand edit met)")
    print(f"  after:  {carries['allowed']} of {carries['pairs']} allowed "
          f"(provably disjoint), {carries['pairs'] - carries['allowed']} "
          f"refused ({carries['conflict']} conflicts, "
          f"{carries['unproven']} unproven)")
    for key, title in (("independent", "two independent edits"),
                       ("overlapping", "two overlapping edits"),
                       ("hand_vs_ren",
                        "a hand edit beside a Ren edit on the same clip")):
        found = named[key]
        if not found:
            print(f"{title}: no pair in this corpus")
            continue
        timeline_name, one, other, report = found
        print(f"{title}: {timeline_name!r} generation "
              f"{one['generation']} and generation {other['generation']} "
              f"-> {report.resolution()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
