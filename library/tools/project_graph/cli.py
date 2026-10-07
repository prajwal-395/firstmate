"""Project-graph CLI: build the graph or run one query."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

from .builder import build_graph
from .queries import (
    find_alternate_takes,
    find_coverage,
    find_reaction_decisions,
    find_reaction_shots,
    find_shot_colour,
)

# ── CLI ───────────────────────────────────────────────────────────


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="project_graph",
        description="The Ren Project Graph - a persistent relational model "
                    "of people, scenes, shots, transcript, audio events, "
                    "graphics, references, timelines and edits.")
    sub = parser.add_subparsers(dest="command")

    p_build = sub.add_parser("build", help="build the project graph")
    p_build.add_argument("project")
    p_build.add_argument("--memory-root")

    p_query = sub.add_parser("query", help="query the project graph")
    p_query.add_argument("project")
    p_query.add_argument("--person", help="person id for reaction shots")
    p_query.add_argument("--t", type=float, help="time for reaction shots")
    p_query.add_argument("--clip", help="clip id for alternate takes")
    p_query.add_argument("--colour", action="store_true",
                         help="the colour registration for --clip")
    p_query.add_argument("--timeline-start", type=float)
    p_query.add_argument("--timeline-end", type=float)
    p_query.add_argument("--decisions", action="store_true",
                         help="reaction-cutaway EDIT DECISIONs, "
                              "optionally for --clip or at --t")
    p_query.add_argument("--memory-root")

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2

    root = Path(args.memory_root).expanduser() if args.memory_root else None

    if args.command == "build":
        graph = build_graph(args.project, root)
        print(json.dumps(graph, indent=2))
        return 0

    if args.command == "query":
        graph = build_graph(args.project, root)
        if args.decisions:
            results = find_reaction_decisions(
                graph, args.clip, args.t)
            print(json.dumps(results, indent=2))
            return 0
        if args.person and args.t is not None:
            results = find_reaction_shots(graph, args.person, args.t)
            print(json.dumps(results, indent=2))
            return 0
        if args.clip:
            results = find_alternate_takes(graph, args.clip)
            print(json.dumps(results, indent=2))
            return 0
        if args.colour and args.clip:
            results = find_shot_colour(graph, args.clip)
            print(json.dumps(results, indent=2))
            return 0
        if args.timeline_start is not None and args.timeline_end is not None:
            results = find_coverage(graph, args.timeline_start, args.timeline_end)
            print(json.dumps(results, indent=2))
            return 0
        print("query needs --decisions, --person + --t, --clip, "
              "--colour + --clip, or --timeline-start + --timeline-end",
              file=sys.stderr)
        return 2

    return 2
