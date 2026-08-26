"""CLI for the step-replay bench.

    python3 -m library.tools.replay_bench capture <project> [--id NAME]
    python3 -m library.tools.replay_bench list
    python3 -m library.tools.replay_bench check <snapshot>
    python3 -m library.tools.replay_bench replay <snapshot> <step> [--rev REV]
    python3 -m library.tools.replay_bench compare <snapshot> <step> --rev-a A --rev-b B
                                                  [--answer-a f.json --answer-b g.json]
    python3 -m library.tools.replay_bench verify <snapshot> [--rev REV]

`--rev` takes a git revision or the literal WORKTREE, which is this
checkout as it stands, uncommitted edits included.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import bench, tokens, trees
from . import snapshot as snapshot_mod


def _print_staleness(report: dict, out=sys.stdout) -> None:
    bits = []
    if not report.get("sealed", True):
        bits.append("SEAL BROKEN: " + "; ".join(report["seal_breaks"][:4]))
    if report.get("references_drifted"):
        bits.append("the project has MOVED since capture (harmless for the "
                    "frozen state, but the areas a bridge reads are live): "
                    + "; ".join(report["references_drifted"][:4]))
    if report.get("torn_at_capture"):
        bits.append("TORN: captured while a run was writing this project")
    if bits:
        print("  ! " + "\n  ! ".join(bits), file=out)
    else:
        print("  snapshot sealed, references unmoved", file=out)


def _cmd_capture(args) -> int:
    snap = snapshot_mod.capture(
        args.project, snapshot_id=args.id,
        store=Path(args.store) if args.store else None,
        repo_root=trees.repo_root(), force=args.force,
        state_path=args.state, archive_dir=args.archive, note=args.note)
    print(f"captured {snap.snapshot_id} -> {snap.root}")
    print(f"  archived steps: {', '.join(snap.archived_steps()) or '(none)'}")
    if snap.manifest.get("torn"):
        print("  ! captured while a run was writing this project - "
              "pipeline_data.json is rewritten after every step, so this "
              "snapshot may be torn across steps")
    man = Path(args.manifest_out) if args.manifest_out else None
    if man:
        man.parent.mkdir(parents=True, exist_ok=True)
        man.write_text(json.dumps(snap.manifest, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
        print(f"  manifest written to {man}")
    return 0


def _cmd_list(args) -> int:
    rows = snapshot_mod.list_snapshots(Path(args.store) if args.store else None)
    if not rows:
        print("no snapshots")
        return 0
    for m in rows:
        print(f"{m['snapshot_id']:36s} {m['captured_at']}  "
              f"{len(m['files'])} files  {m['source_project']}"
              + ("  [TORN]" if m.get("torn") else ""))
    return 0


def _cmd_check(args) -> int:
    snap = snapshot_mod.load(args.snapshot,
                             Path(args.store) if args.store else None)
    report = snap.verify()
    report["torn_at_capture"] = bool(snap.manifest.get("torn"))
    print(f"snapshot {snap.snapshot_id}  ({snap.root})")
    _print_staleness(report)
    return 0 if report["sealed"] and not report["references_drifted"] else 1


def _cmd_replay(args) -> int:
    r = bench.replay(args.snapshot, args.step, rev=args.rev,
                     store=Path(args.store) if args.store else None,
                     llm_authored=[k for k in (args.llm_authored or "").split(",") if k])
    if args.json:
        json.dump(r, sys.stdout, indent=2)
        print()
        return 0
    print(f"{r['step_id']}  ({r['step_type']})  at {r['revision']}")
    print(f"  snapshot {r['snapshot']}")
    _print_staleness(r["staleness"])
    for name, measured in (("prompt", r["prompt_tokens"]),
                           ("context", r["context_tokens"])):
        for tok, n in sorted(measured.items()):
            print(f"  {name:8s} {n:>10,}  [{tok}]")
    real = r["context_tokens"].get(tokens.O200K)
    heur = r["context_pipeline_heuristic"]
    ratio = f"{heur / real:.2f}x" if real else "n/a"
    print(f"  context {heur:>10,}  [len(s.split())*1.3 - what the pipeline "
          f"logs, {ratio} the o200k_base count; not a tokenizer, shown only "
          f"so the gap is visible]")
    print(f"  context keys: {', '.join(r['top_level_keys'])}")
    for note in r["notes"]:
        print(f"  note: {note}")
    if args.out:
        Path(args.out).write_text(
            r["prompt"] + "\n\nContext:\n" + r["context"], encoding="utf-8")
        print(f"  full prompt written to {args.out}")
    return 0


def _cmd_compare(args) -> int:
    r = bench.compare(
        args.snapshot, args.step, args.rev_a, args.rev_b,
        store=Path(args.store) if args.store else None,
        answer_a=Path(args.answer_a) if args.answer_a else None,
        answer_b=Path(args.answer_b) if args.answer_b else None,
        llm_authored=[k for k in (args.llm_authored or "").split(",") if k])
    if args.json:
        json.dump(r, sys.stdout, indent=2)
        print()
        return 0
    print(f"{r['step_id']}  on snapshot {r['snapshot']}")
    _print_staleness(r["staleness"])
    print(f"  A  {r['left']['revision']}")
    print(f"  B  {r['right']['revision']}")
    for side in ("left", "right"):
        for tok, n in sorted(r[side]["context_tokens"].items()):
            print(f"  {'A' if side == 'left' else 'B'} context "
                  f"{n:>10,}  [{tok}]")
    print(f"  context identical: {r['context_identical']}")
    print(f"  prompt  identical: {r['prompt_identical']}")
    if r["sections_only_left"]:
        print(f"  only in A: {', '.join(r['sections_only_left'])}")
    if r["sections_only_right"]:
        print(f"  only in B: {', '.join(r['sections_only_right'])}")
    for row in r["context_section_deltas"]:
        print(f"    {row['section']:34s} A={row['left_bytes']}  "
              f"B={row['right_bytes']}  {row['delta_bytes']:+,} B")
    if r["answers"] is not None:
        a = r["answers"]
        print(f"  answers identical: {a['identical']}")
        for row in a["differing_paths"][:40]:
            print(f"    {row['kind']:8s} {row['path']}")
            if row["kind"] == "changed":
                print(f"       A: {row['left']}")
                print(f"       B: {row['right']}")
        if len(a["differing_paths"]) > 40:
            print(f"    ... {len(a['differing_paths']) - 40} more")
    elif args.answer_a or args.answer_b:
        print("  answers: both --answer-a and --answer-b are needed to diff")
    return 0


def _cmd_verify(args) -> int:
    r = bench.verify_archive(
        args.snapshot, rev=args.rev,
        store=Path(args.store) if args.store else None,
        steps=args.steps.split(",") if args.steps else None)
    if args.json:
        json.dump(r, sys.stdout, indent=2)
        print()
        return 0 if r["unaccounted"] == 0 else 1
    print(f"snapshot {r['snapshot']}")
    print(f"revision {r['revision']}")
    print(f"tokenizers available: {', '.join(r['tokenizers'])}")
    _print_staleness(r["staleness"])
    print()
    print(f"{'step':22s} {'type':22s} {'recon B':>10s} {'archive B':>10s} "
          f"{'delta':>9s}  verdict")
    print("-" * 96)
    for row in r["rows"]:
        if row["verdict"] == "ERROR":
            print(f"{row['step_id']:22s} {'-':22s} {'-':>10s} "
                  f"{row['archived_bytes']:>10,} {'-':>9s}  ERROR")
            continue
        recon = row.get("explained_bytes", row["reconstructed_bytes"])
        delta = row.get("explained_delta_bytes", row["delta_bytes"])
        print(f"{row['step_id']:22s} {row['step_type']:22s} {recon:>10,} "
              f"{row['archived_bytes']:>10,} {delta:>+9,}  {row['verdict']}")
    print("-" * 96)
    print(f"{r['exact']} exact, {r['exact_explained']} exact after a named and "
          f"reproduced cause, {r['unaccounted']} unaccounted, of {r['total']}")
    print(f"project untouched by this run: {r['wrote_nothing']}  "
          f"(every referenced area re-digested after the reconstructions; "
          f"an unmoved listing means no file under the project was written)")
    for row in r["rows"]:
        if not (row.get("explanations") or row.get("residual_sections")
                or row.get("error")):
            continue
        print(f"\n{row['step_id']}:")
        for e in row.get("explanations", []):
            print(f"  - {e}")
        if row.get("explanations"):
            print(f"  raw delta {row['delta_bytes']:+,} B -> after the cause "
                  f"{row.get('explained_delta_bytes', row['delta_bytes']):+,} B")
        if row.get("residual_sections"):
            print("  unaccounted, by section:")
            for sec in row["residual_sections"]:
                print(f"    {sec['section']:32s} {sec['delta_bytes']:+,} B")
        if row.get("error"):
            print(f"  ERROR\n  {row['error'][:600]}")
    return 0 if r["unaccounted"] == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python3 -m library.tools.replay_bench",
        description="Rebuild a step's prompt and context off frozen state, "
                    "at a named revision. No pipeline run, no Resolve, no "
                    "project write.")
    ap.add_argument("--store", default=None,
                    help=f"snapshot store (default {snapshot_mod.DEFAULT_STORE})")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("capture", help="freeze one project's state")
    c.add_argument("project")
    c.add_argument("--id", default=None)
    c.add_argument("--force", action="store_true")
    c.add_argument("--state", default=None,
                   help="freeze this pipeline_data.json instead of the "
                        "project's own (it is rewritten after every step)")
    c.add_argument("--archive", default=None,
                   help="freeze the llm_requests/llm_responses under this "
                        "directory instead of the project's own; the "
                        "project archive is last-write-wins per step, so a "
                        "later run replaces the one a state file belongs with")
    c.add_argument("--note", default=None,
                   help="provenance a path cannot carry: where these bytes "
                        "came from and why they were frozen")
    c.add_argument("--manifest-out", default=None,
                   help="also write the manifest here, to be committed")
    c.set_defaults(func=_cmd_capture)

    c = sub.add_parser("list", help="snapshots in the store")
    c.set_defaults(func=_cmd_list)

    c = sub.add_parser("check", help="is this snapshot still trustworthy")
    c.add_argument("snapshot")
    c.set_defaults(func=_cmd_check)

    c = sub.add_parser("replay", help="rebuild one step's prompt and context")
    c.add_argument("snapshot")
    c.add_argument("step")
    c.add_argument("--rev", default=trees.WORKTREE)
    c.add_argument("--out", default=None, help="write the full prompt here")
    c.add_argument("--llm-authored", default=None)
    c.add_argument("--json", action="store_true")
    c.set_defaults(func=_cmd_replay)

    c = sub.add_parser("compare", help="one step at two revisions")
    c.add_argument("snapshot")
    c.add_argument("step")
    c.add_argument("--rev-a", required=True)
    c.add_argument("--rev-b", required=True)
    c.add_argument("--answer-a", default=None)
    c.add_argument("--answer-b", default=None)
    c.add_argument("--llm-authored", default=None)
    c.add_argument("--json", action="store_true")
    c.set_defaults(func=_cmd_compare)

    c = sub.add_parser("verify",
                       help="reconstruct every archived context and compare")
    c.add_argument("snapshot")
    c.add_argument("--rev", default=trees.WORKTREE)
    c.add_argument("--steps", default=None)
    c.add_argument("--json", action="store_true")
    c.set_defaults(func=_cmd_verify)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
