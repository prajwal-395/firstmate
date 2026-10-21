#!/usr/bin/env python3
"""How far the typed temporal index reaches on real projects.

The measurement the workstream B audit named for lane BL-7: the number
of typed events each project holds, and the fraction of those events
that carry a state change and a participant.  Both were ZERO on every
Ren scratch project before the lane, because the index read the
Project Model's ``events`` collection and nothing else.

Usage::

    python3 docs/benchmarks/temporal_event_reach_bench.py [project ...]

With no project, the eight Ren scratch projects are measured.  Every
project is READ: the index is derived, so the measurement writes
nothing.

The output is one row per project:

* ``events`` - the typed events the index holds;
* ``state_changes`` - the fraction of those events that carry at least
  one state change (an entity, a property, a before and an after);
* ``participants`` - the fraction that name at least one participant;
* ``participant_ids`` - the fraction that carry the event contract's
  own ``participant_ids`` field, and how many of those participants
  were OBSERVED rather than inferred;
* ``state_chains`` - the number of (entity, property) pairs whose
  value changed more than once, which is what a state TRANSITION
  looks like (``closed -> opening -> open``);
* ``unmapped`` - the Project Model event kinds the index could not
  type.  A kind it could not type is a hole in the surface, and it is
  reported rather than guessed into a type.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from library.tools import temporal_events

DEFAULT_PROJECTS = (
    "ren-scratch-breaktest",
    "ren-scratch-bench-demo",
    "ren-scratch-bench-demo2",
    "ren-scratch-retrieval-bench",
    "vep-live-workflows-e2e",
    "eval04-scratch-renders",
    "axc-reel-opening-qual",
    "axc-reel-opening-qual-hd",
)

PROJECTS_ROOT = Path.home() / "Documents" / "content_stuff" / "video_projects"


def measure(project_folder: str) -> dict:
    """One project's typed events, and the two fractions."""
    index = temporal_events.build_temporal_events(project_folder)
    events = index.events
    count = len(events)
    with_changes = sum(1 for event in events if event.state_changes)
    with_people = sum(1 for event in events
                      if event.subjects or event.objects)
    with_participant_ids = sum(1 for event in events
                               if event.participant_ids)
    observed = sum(1 for event in events if event.observed_participant_ids)
    inferred = sum(1 for event in events if event.inferred_participant_ids)
    chains = sum(1 for row in temporal_events.state_history(events)
                 if len(row["states"]) > 2)
    by_type: dict[str, int] = {}
    for event in events:
        by_type[event.event_type] = by_type.get(event.event_type, 0) + 1
    return {
        "project": Path(project_folder).name,
        "events": count,
        "state_changes": with_changes,
        "state_change_fraction": round(with_changes / count, 4) if count else 0.0,
        "participants": with_people,
        "participant_fraction": round(with_people / count, 4) if count else 0.0,
        "participant_ids": with_participant_ids,
        "participant_id_fraction": (round(with_participant_ids / count, 4)
                                    if count else 0.0),
        "observed_participant_events": observed,
        "inferred_participant_events": inferred,
        "state_chains": chains,
        "unmapped_kinds": list(index.unmapped_kinds),
        "sources": list(index.sources),
        "by_type": dict(sorted(by_type.items())),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure how far the typed temporal index reaches")
    parser.add_argument("projects", nargs="*",
                        help="project folders (default: the Ren scratch "
                             "projects)")
    parser.add_argument("--json", action="store_true",
                        help="print the rows as JSON")
    args = parser.parse_args(argv)

    folders = args.projects or [str(PROJECTS_ROOT / name)
                                for name in DEFAULT_PROJECTS]
    rows = []
    for folder in folders:
        if not Path(folder).is_dir():
            print(f"missing project: {folder}", file=sys.stderr)
            continue
        rows.append(measure(folder))

    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    header = (f"{'project':32s} {'events':>7s} {'state_changes':>14s} "
              f"{'participants':>14s} {'participant_ids':>16s} "
              f"{'obs/inf':>10s} {'chains':>7s}  unmapped")
    print(header)
    print("-" * len(header))
    for row in rows:
        print(f"{row['project']:32s} {row['events']:7d} "
              f"{row['state_changes']:6d} "
              f"({row['state_change_fraction'] * 100:5.1f}%) "
              f"{row['participants']:6d} "
              f"({row['participant_fraction'] * 100:5.1f}%) "
              f"{row['participant_ids']:8d} "
              f"({row['participant_id_fraction'] * 100:5.1f}%) "
              f"{row['observed_participant_events']:5d}/"
              f"{row['inferred_participant_events']:<4d} "
              f"{row['state_chains']:7d}  "
              f"{','.join(row['unmapped_kinds']) or '-'}")
    total = sum(row["events"] for row in rows)
    projects_with_events = sum(1 for row in rows if row["events"])
    print(f"\n{total} typed events across {projects_with_events}/{len(rows)} "
          f"projects")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
