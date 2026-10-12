#!/usr/bin/env bash
# fm-plan-export.sh - export this home's tasks-axi backlog to planning-compiler IR.
#
# Usage:
#   fm-plan-export.sh [--project <repo>] [--out <dir>] [--data-dir <dir>]
#   fm-plan-export.sh --help
#
# Reads the backlog ONLY through bin/fm-tasks-axi.sh `list` and `show --full`
# (both read-only) and writes one planning-compiler plan per project under the
# active home's data directory: <out>/<project>/plan.toml plus an empty sibling
# evidence.jsonl. The backlog itself is never written, moved, or reordered.
#
# Mapping (data/planning-compiler-master-plan/report.md section 3): every live
# (queued, in flight, held) tasks-axi row becomes one open IR task; blocked-by
# becomes order edges inside a single accepted decompose method on a per-project
# root task. Done rows are not live work and are left out of the plan.
#
# Fields with no IR counterpart (tasks-axi state, kind, repo, holds, date
# gates, priority, links, body) ride inside `tasks-axi-json` TOML comment lines
# with byte-exact values, per the captain's 2026-10-12 ruling that such fields
# are carried through untouched. Comments keep `pc check` clean: the IR schema
# denies unknown fields, so no extra TOML key may carry them.
#
# Edges that cannot enter the IR are dropped loudly, never silently: blockers
# naming another project, self-edges, duplicates, and edges that would close an
# order cycle (E007) are each printed as a `dropped-edge:` line and counted in
# the method assumptions. Rows `show --full` cannot parse are skipped with a
# `skipped:` line. Either list is empty on a clean export.
#
# tasks-axi `ready` stays the dispatch source of truth; nothing in dispatch
# reads this export, and this script never calls a mutating tasks-axi verb.
#
# The data directory is --data-dir, else FM_DATA_OVERRIDE, else $FM_HOME/data,
# else the code root's data/ (the same precedence bin/fm-tasks-axi.sh uses).
# Output defaults to that same data directory. Exit 0 writes at least one plan;
# exit 2 refuses (bad usage, missing tools, unreadable backlog, no live rows,
# or an unwritable output directory).
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FM_ROOT="${FM_ROOT_OVERRIDE:-$(cd "$SCRIPT_DIR/.." && pwd)}"
FM_HOME="${FM_HOME:-${FM_ROOT_OVERRIDE:-$FM_ROOT}}"
WRAPPER="$SCRIPT_DIR/fm-tasks-axi.sh"

usage() {
  awk '
    NR == 1 { next }
    /^#/ { sub(/^# ?/, ""); print; next }
    { exit }
  ' "$0"
}

fail() {
  printf 'fm-plan-export: %s\n' "$*" >&2
  exit 2
}

PROJECT=""
OUT=""
DATA_FLAG=""

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --project)
      [ $# -ge 2 ] || fail "--project needs a value"
      PROJECT="$2"
      shift 2
      ;;
    --project=*)
      PROJECT="${1#--project=}"
      shift
      ;;
    --out)
      [ $# -ge 2 ] || fail "--out needs a value"
      OUT="$2"
      shift 2
      ;;
    --out=*)
      OUT="${1#--out=}"
      shift
      ;;
    --data-dir)
      [ $# -ge 2 ] || fail "--data-dir needs a value"
      DATA_FLAG="$2"
      shift 2
      ;;
    --data-dir=*)
      DATA_FLAG="${1#--data-dir=}"
      shift
      ;;
    --)
      shift
      break
      ;;
    -*)
      fail "unknown flag: $1"
      ;;
    *)
      fail "unexpected argument: $1"
      ;;
  esac
done
[ $# -eq 0 ] || fail "unexpected argument: $1"

if [ -n "$DATA_FLAG" ]; then
  DATA="$DATA_FLAG"
else
  DATA="${FM_DATA_OVERRIDE:-$FM_HOME/data}"
fi
if [ -z "$OUT" ]; then
  OUT="$DATA"
fi

[ -x "$WRAPPER" ] || fail "cannot execute $WRAPPER"
command -v python3 >/dev/null 2>&1 || fail "python3 is not on PATH"
command -v tasks-axi >/dev/null 2>&1 || fail "tasks-axi is not on PATH; run bin/fm-bootstrap.sh for the install command"

export FM_DATA_OVERRIDE="$DATA"

python3 - "$WRAPPER" "$OUT" "$PROJECT" <<'PYEOF'
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

wrapper, out_base, project_filter = sys.argv[1], sys.argv[2], sys.argv[3]

LIVE_STATES = ("queued", "in_flight", "held")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
SPLIT_RE = re.compile(r"[,\s;]+")


def run_wrapper(args):
    proc = subprocess.run(
        [wrapper] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    return proc.returncode, proc.stdout, proc.stderr


def list_ids(state):
    code, out, err = run_wrapper(["list", "--state", state])
    if code != 0:
        # An empty backlog reports zero rows with exit 0; any failure here is
        # addressing or backend trouble, so refuse rather than export a guess.
        sys.stderr.write(f"fm-plan-export: tasks-axi list --state {state} failed: {err.strip()}\n")
        sys.exit(2)
    ids = []
    for line in out.splitlines():
        if line.startswith("  ") and not line.startswith("  -"):
            first = line.strip().split(",", 1)[0].strip()
            if first and first not in ids:
                ids.append(first)
    return ids


def unquote(value):
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        body = value[1:-1]
        out = []
        i = 0
        while i < len(body):
            ch = body[i]
            if ch == "\\" and i + 1 < len(body):
                nxt = body[i + 1]
                out.append({"n": "\n", "t": "\t", "r": "\r"}.get(nxt, nxt))
                i += 2
            else:
                out.append(ch)
                i += 1
        return "".join(out)
    return value


def parse_show(text):
    record = {}
    for line in text.splitlines():
        if not line.startswith("  ") or line.startswith("  -"):
            continue
        stripped = line.strip()
        key, sep, value = stripped.partition(":")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if key == "task":
            continue
        record[key] = unquote(value)
    if "id" not in record or "title" not in record or "state" not in record:
        return None
    return record


def absent(value):
    return value is None or value == "" or value == "-" or value == "none"


def split_ids(value):
    if absent(value):
        return []
    return [part for part in SPLIT_RE.split(value) if part]


def sanitize(name):
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "backlog"


def toml_str(value):
    escaped = []
    for ch in value:
        if ch == "\\":
            escaped.append("\\\\")
        elif ch == '"':
            escaped.append('\\"')
        elif ch == "\n":
            escaped.append("\\n")
        elif ch == "\t":
            escaped.append("\\t")
        elif ch == "\r":
            escaped.append("\\r")
        elif ord(ch) < 0x20:
            escaped.append(f"\\u{ord(ch):04X}")
        else:
            escaped.append(ch)
    return '"' + "".join(escaped) + '"'


def comment_lines(prefix, value):
    lines = []
    for raw in value.split("\n"):
        clean = raw.replace("\r", "")
        lines.append(f"# {prefix}{clean}" if clean else "#")
    return lines


wanted = []
for state in LIVE_STATES:
    wanted.extend(list_ids(state))

records = []
skipped = []
seen = set()
for item_id in wanted:
    if item_id in seen:
        continue
    seen.add(item_id)
    if not ID_RE.match(item_id):
        skipped.append((item_id, "id is not a plain slug"))
        continue
    code, out, err = run_wrapper(["show", item_id, "--full"])
    if code != 0:
        skipped.append((item_id, f"show --full failed: {err.strip().splitlines()[0] if err.strip() else 'exit ' + str(code)}"))
        continue
    record = parse_show(out)
    if record is None:
        skipped.append((item_id, "unparseable show --full output"))
        continue
    if record.get("state") == "done":
        continue
    records.append(record)

if project_filter:
    records = [rec for rec in records if (rec.get("repo") or "") == project_filter]

if not records:
    sys.stderr.write("fm-plan-export: no live backlog rows to export\n")
    sys.exit(2)

groups = {}
for record in records:
    repo = record.get("repo") or ""
    if absent(repo):
        repo = "backlog"
    groups.setdefault(repo, []).append(record)

stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
summary_tasks = 0

for repo in sorted(groups):
    rows = sorted(groups[repo], key=lambda rec: rec["id"])
    plan_dir_name = sanitize(repo)
    plan_id = f"{plan_dir_name}-backlog"
    root_id = f"{plan_id}-root"
    while any(rec["id"] == root_id for rec in rows):
        root_id += "-root"
    method_id = f"{root_id}-decompose"

    present = {rec["id"] for rec in rows}
    wanted_edges = []
    dropped = []
    for record in rows:
        for target in split_ids(record.get("blocked_by")):
            if target == record["id"]:
                dropped.append((record["id"], target, "self-edge"))
            elif target not in present:
                dropped.append((record["id"], target, "target is outside this plan"))
            elif (record["id"], target) not in [(edge[1], edge[0]) for edge in wanted_edges]:
                wanted_edges.append((target, record["id"]))

    # Keep edges in sorted order, dropping each one that would close a cycle
    # so the emitted plan stays E007-clean while every drop is reported.
    accepted = []
    for edge in sorted(wanted_edges):
        source, dest = edge
        # Reachability from dest back to source through accepted edges means
        # this edge closes a cycle.
        back = {dest}
        changed = True
        while changed:
            changed = False
            for acc_source, acc_dest in accepted:
                if acc_source in back and acc_dest not in back:
                    back.add(acc_dest)
                    changed = True
        if source in back:
            dropped.append((source, dest, "would close an order cycle"))
        else:
            accepted.append(edge)
    accepted.sort()
    dropped.sort()

    lines = []
    lines.append("ir_version = 0")
    lines.append("")
    lines.append("[plan]")
    lines.append(f"id = {toml_str(plan_id)}")
    lines.append(f"title = {toml_str(f'{repo} backlog export')}")
    lines.append(
        f"description = {toml_str(f'Read-only export of {repo} backlog rows via tasks-axi list/show on {stamp}; backlog.md untouched; regenerate anytime.')}"
    )
    lines.append("")
    lines.append("[objective]")
    lines.append(f"root_task = {toml_str(root_id)}")
    lines.append("weights = { duration_minutes = 1.0 }")
    lines.append("")
    lines.append("[[objective.acceptance]]")
    lines.append('kind = "manual"')
    lines.append(
        'description = "tasks-axi ready stays the dispatch source of truth; this export is advisory."'
    )
    lines.append("")
    lines.append("[[task]]")
    lines.append(f"id = {toml_str(root_id)}")
    lines.append(f"title = {toml_str(f'{repo} backlog')}")
    lines.append('state = "decomposed"')
    for record in rows:
        carried = {
            "id": record.get("id", ""),
            "title": record.get("title", ""),
            "state": record.get("state", ""),
            "kind": "" if absent(record.get("kind")) else record.get("kind", ""),
            "repo": "" if absent(record.get("repo")) else record.get("repo", ""),
            "blocked": record.get("blocked", ""),
            "blocked_by": split_ids(record.get("blocked_by")),
            "held": record.get("held", ""),
            "hold_kind": "" if absent(record.get("hold_kind")) else record.get("hold_kind", ""),
            "hold_reason": "" if absent(record.get("hold_reason")) else record.get("hold_reason", ""),
            "hold_until": "" if absent(record.get("hold_until")) else record.get("hold_until", ""),
            "priority": "" if absent(record.get("priority")) else record.get("priority", ""),
            "created": "" if absent(record.get("created")) else record.get("created", ""),
            "deps": "" if absent(record.get("deps")) else record.get("deps", ""),
            "links": "" if absent(record.get("links")) else record.get("links", ""),
            "body": record.get("body", ""),
        }
        skim = f"{record['id']} state={record.get('state', '')} kind={carried['kind'] or '-'} repo={carried['repo'] or '-'}"
        if carried["blocked_by"]:
            skim += f" blocked-by={','.join(carried['blocked_by'])}"
        if carried["held"] == "yes":
            skim += f" held={carried['hold_reason'] or carried['hold_kind'] or 'yes'}"
        lines.append("")
        lines.extend(comment_lines("tasks-axi: ", skim))
        lines.extend(
            comment_lines(
                "tasks-axi-json: ",
                json.dumps(carried, sort_keys=True, ensure_ascii=False),
            )
        )
        lines.append("[[task]]")
        lines.append(f"id = {toml_str(record['id'])}")
        title = record.get("title", "")
        lines.append(f"title = {toml_str(title if title else record['id'])}")
        lines.append('state = "open"')
    lines.append("")
    lines.append("[[method]]")
    lines.append(f"id = {toml_str(method_id)}")
    lines.append(f"task = {toml_str(root_id)}")
    lines.append('kind = "decompose"')
    lines.append(
        "subtasks = ["
        + ", ".join(toml_str(rec["id"]) for rec in rows)
        + "]"
    )
    if accepted:
        lines.append(
            "order_edges = ["
            + ", ".join(
                f"{{ from = {toml_str(source)}, to = {toml_str(dest)} }}"
                for source, dest in accepted
            )
            + "]"
        )
    else:
        lines.append("order_edges = []")
    lines.append('status = "accepted"')
    lines.append('provenance = "agent:fm-plan-export"')
    lines.append("assumptions = [")
    lines.append(
        '  "Live tasks-axi rows map to open tasks; in-flight and held rows stay open with their state and hold carried in tasks-axi-json comments.",'
    )
    if dropped:
        lines.append(
            f'  "Dropped {len(dropped)} blocked-by edge(s) that cannot enter the IR: '
            + "; ".join(f"{source} -> {dest} ({reason})" for source, dest, reason in dropped)
            + '.",'
        )
    lines.append(
        '  "Fields with no IR counterpart ride as tasks-axi-json comments with exact values.",'
    )
    lines.append(
        '  "tasks-axi ready stays the dispatch source of truth; this plan never dispatches.",'
    )
    lines.append("]")

    plan_dir = os.path.join(out_base, plan_dir_name)
    try:
        os.makedirs(plan_dir, exist_ok=True)
    except OSError as exc:
        sys.stderr.write(f"fm-plan-export: cannot create {plan_dir}: {exc}\n")
        sys.exit(2)
    plan_path = os.path.join(plan_dir, "plan.toml")
    evidence_path = os.path.join(plan_dir, "evidence.jsonl")
    try:
        with open(plan_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        with open(evidence_path, "w", encoding="utf-8") as handle:
            handle.write("")
    except OSError as exc:
        sys.stderr.write(f"fm-plan-export: cannot write {plan_dir}: {exc}\n")
        sys.exit(2)

    summary_tasks += len(rows)
    print(f"wrote: {plan_path} tasks {len(rows)} edges {len(accepted)}")
    print(f"carried-through: {plan_id} {len(rows)} tasks-axi-json comment blocks")
    for source, dest, reason in dropped:
        print(f"dropped-edge: {source} -> {dest} ({reason})")

for item_id, reason in sorted(skipped):
    print(f"skipped: {item_id} ({reason})")
print(f"exported: {summary_tasks} live rows into {len(groups)} plan(s)")
PYEOF
