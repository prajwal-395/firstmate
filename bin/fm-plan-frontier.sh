#!/usr/bin/env bash
# fm-plan-frontier.sh - add advisory planning-compiler frontier notes to a Bearings payload.
#
# Usage:
#   fm-plan-frontier.sh --payload <board.json> [--data-dir <dir>] [--out <dir>]
#   fm-plan-frontier.sh --payload <board.json> --plan-dir <dir> [--data-dir <dir>]
#   fm-plan-frontier.sh --help
#
# Requires PC_BIN to name an executable planning-compiler `pc` binary. It never
# searches PATH for `pc`. It exports the active home's backlog through
# fm-plan-export.sh, then runs `pc check`, `pc compile`, and `pc frontier --json`
# once for each plan exported by that invocation. Exported plans go to --out,
# defaulting to the selected data directory (<FM_HOME>/data by default).
# --plan-dir runs one existing per-project plan without exporting it first.
#
# The output annotates existing Charted Next rows and routes blocked compiler
# alternatives through captain-held tasks into Captain's Call. Answered routing
# tasks feed pc accept/reject on the next invocation, followed by a recompile.
# tasks-axi ready remains the dispatch source of truth. The complete result is
# validated through fm-bearings-board.sh before it is written to stdout.
set -eu

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FM_ROOT="${FM_ROOT_OVERRIDE:-$(cd "$SCRIPT_DIR/.." && pwd)}"
FM_HOME="${FM_HOME:-${FM_ROOT_OVERRIDE:-$FM_ROOT}}"
DATA="${FM_DATA_OVERRIDE:-$FM_HOME/data}"
PAYLOAD=
DATA_FLAG=
OUT_FLAG=
PLAN_DIR_FLAG=

usage() {
  awk '
    NR == 1 { next }
    /^#/ { sub(/^# ?/, ""); print; next }
    { exit }
  ' "$0"
}

fail() {
  printf 'fm-plan-frontier: %s\n' "$*" >&2
  exit 2
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --payload)
      [ "$#" -ge 2 ] || fail "--payload needs a value"
      PAYLOAD=$2
      shift 2
      ;;
    --payload=*)
      PAYLOAD=${1#--payload=}
      shift
      ;;
    --data-dir)
      [ "$#" -ge 2 ] || fail "--data-dir needs a value"
      DATA_FLAG=$2
      shift 2
      ;;
    --data-dir=*)
      DATA_FLAG=${1#--data-dir=}
      shift
      ;;
    --out)
      [ "$#" -ge 2 ] || fail "--out needs a value"
      OUT_FLAG=$2
      shift 2
      ;;
    --out=*)
      OUT_FLAG=${1#--out=}
      shift
      ;;
    --plan-dir)
      [ "$#" -ge 2 ] || fail "--plan-dir needs a value"
      PLAN_DIR_FLAG=$2
      shift 2
      ;;
    --plan-dir=*)
      PLAN_DIR_FLAG=${1#--plan-dir=}
      shift
      ;;
    --)
      shift
      break
      ;;
    -* )
      fail "unknown flag: $1"
      ;;
    *)
      fail "unexpected argument: $1"
      ;;
  esac
done
[ "$#" -eq 0 ] || fail "unexpected argument: $1"
[ -n "$PAYLOAD" ] || fail "--payload is required"
[ -n "${PC_BIN:-}" ] || fail "PC_BIN is not set; set it to the executable planning-compiler pc binary"
[ -x "$PC_BIN" ] || fail "PC_BIN is not executable: $PC_BIN"
DATA=${DATA_FLAG:-$DATA}
OUT=${OUT_FLAG:-$DATA}
[ -f "$PAYLOAD" ] || fail "board payload does not exist: $PAYLOAD"
command -v jq >/dev/null 2>&1 || fail "jq is required"
command -v python3 >/dev/null 2>&1 || fail "python3 is required"
"$SCRIPT_DIR/fm-bearings-board.sh" validate "$PAYLOAD" >/dev/null \
  || fail "input does not satisfy the Bearings board payload contract"

scratch=$(umask 077; mktemp -d "${TMPDIR:-/tmp}/fm-plan-frontier.XXXXXX") \
  || fail "cannot create scratch directory"
trap 'rm -rf -- "$scratch"' EXIT HUP INT TERM
exported_log="$scratch/export.log"
if [ -n "$PLAN_DIR_FLAG" ]; then
  [ -f "$PLAN_DIR_FLAG/plan.toml" ] || fail "plan directory has no plan.toml: $PLAN_DIR_FLAG"
  printf '%s\n' "$PLAN_DIR_FLAG/plan.toml" > "$scratch/plans.list"
else
  if ! FM_HOME="$FM_HOME" FM_DATA_OVERRIDE="$DATA" \
    "$SCRIPT_DIR/fm-plan-export.sh" --data-dir "$DATA" --out "$OUT" \
    >"$exported_log" 2>&1; then
    cat "$exported_log" >&2
    fail "backlog export failed"
  fi
  cat "$exported_log" >&2
  awk 'substr($0, 1, 7) == "wrote: " { line = substr($0, 8); sub(/ tasks [0-9]+ edges [0-9]+$/, "", line); print line }' \
    "$exported_log" > "$scratch/plans.list"
fi
[ -s "$scratch/plans.list" ] || fail "export produced no plans"

: > "$scratch/manifest.tsv"
index=0
while IFS= read -r plan_file; do
  [ -f "$plan_file" ] || fail "exported plan is missing: $plan_file"
  case "$plan_file" in
    */plan.toml) plan_dir=${plan_file%/plan.toml} ;;
    *) fail "export returned an unexpected plan path: $plan_file" ;;
  esac
  index=$((index + 1))
  frontier_file="$scratch/frontier-$index.json"
  if ! "$PC_BIN" check "$plan_dir" >"$scratch/check-$index.log" 2>&1; then
    cat "$scratch/check-$index.log" >&2
    fail "pc check failed for $plan_dir"
  fi
  if ! "$PC_BIN" compile "$plan_dir" >"$scratch/compile-$index.log" 2>&1; then
    cat "$scratch/compile-$index.log" >&2
    fail "pc compile failed for $plan_dir"
  fi
  if ! "$PC_BIN" frontier "$plan_dir" --json >"$frontier_file" 2>"$scratch/frontier-$index.err"; then
    cat "$scratch/frontier-$index.err" >&2
    fail "pc frontier --json failed for $plan_dir"
  fi
  jq -e 'type == "object" and (.items | type == "array") and (.unestimated | type == "array") and (.blocked | type == "array")' \
    "$frontier_file" >/dev/null \
    || fail "pc frontier --json returned an unexpected shape for $plan_dir"
  printf '%s\t%s\n' "$plan_file" "$frontier_file" >> "$scratch/manifest.tsv"
done < "$scratch/plans.list"

python3 - "$PAYLOAD" "$scratch/manifest.tsv" "$SCRIPT_DIR" "$PC_BIN" <<'PYEOF'
import json
import os
import re
import subprocess
import sys

payload_path, manifest_path, script_dir, pc_bin = sys.argv[1:]
plans = {}
routes = []

def run(args):
    return subprocess.run(args, text=True, capture_output=True)

def route_id(project, method):
    value = f"pc-route-{project}--{method}"
    if len(value) > 128 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value):
        raise SystemExit(f"fm-plan-frontier: blocked alternative cannot be routed with a safe task id: {value}")
    return value

def consume_answers(project, plan_path, frontier_path):
    wrapper = os.environ.get("FM_PLAN_TASKS_AXI_BIN", os.path.join(script_dir, "fm-tasks-axi.sh"))
    listing = run([wrapper, "list", "--state", "done"])
    if listing.returncode:
        raise SystemExit(f"fm-plan-frontier: cannot list answered routing tasks: {listing.stderr.strip()}")
    for line in listing.stdout.splitlines():
        if not line.startswith("  ") or line.startswith("  -"):
            continue
        task_id = line.strip().split(",", 1)[0].strip()
        prefix = f"pc-route-{project}--"
        if not task_id.startswith(prefix):
            continue
        method = task_id[len(prefix):]
        detail = run([wrapper, "show", task_id, "--full"])
        if detail.returncode:
            raise SystemExit(f"fm-plan-frontier: cannot read answered routing task {task_id}: {detail.stderr.strip()}")
        if not re.search(r"(?m)^\s*Resolution mode: answered\s*$", detail.stdout):
            continue
        match = re.search(r"(?m)^\s*Captain decision: (accept|reject)\s*$", detail.stdout)
        if not match:
            raise SystemExit(f"fm-plan-frontier: answered routing task {task_id} must contain exactly accept or reject")
        explanation = run([pc_bin, "explain", f"method:{method}", plan_path])
        rejected_notice = "is rejected and ignored by every pass"
        if explanation.returncode and rejected_notice in explanation.stderr:
            state = "rejected"
        elif explanation.returncode:
            raise SystemExit(f"fm-plan-frontier: cannot inspect {method} in {plan_path}: {explanation.stderr.strip()}")
        else:
            state_match = re.search(r"\bstatus \((accepted|rejected|proposed|derived)\)", explanation.stdout)
            state = state_match.group(1) if state_match else explanation.stdout.strip().splitlines()[0].lower().split()[0]
        action = match.group(1)
        if state == action + "ed":
            continue
        if state != "proposed":
            raise SystemExit(f"fm-plan-frontier: answered method {method} has unexpected state: {state}")
        decision = run([pc_bin, action, f"method:{method}", plan_path])
        if decision.returncode:
            raise SystemExit(f"fm-plan-frontier: pc {action} failed for {method}: {decision.stderr.strip()}")
        print(f"plan-answer: {task_id} {action}", file=sys.stderr)
        compiled = run([pc_bin, "compile", plan_path])
        if compiled.returncode:
            raise SystemExit(f"fm-plan-frontier: recompile failed for {plan_path}: {compiled.stderr.strip()}")
        print(f"recompiled: {plan_path}", file=sys.stderr)
        frontier = run([pc_bin, "frontier", plan_path, "--json"])
        if frontier.returncode:
            raise SystemExit(f"fm-plan-frontier: frontier failed after answer for {plan_path}: {frontier.stderr.strip()}")
        with open(frontier_path, "w", encoding="utf-8") as output:
            output.write(frontier.stdout)

with open(manifest_path, encoding="utf-8") as manifest:
    for line in manifest:
        plan_path, frontier_path = line.rstrip("\n").split("\t", 1)
        project = os.path.basename(os.path.dirname(plan_path))
        consume_answers(project, plan_path, frontier_path)
        tasks = {}
        for raw in open(plan_path, encoding="utf-8"):
            prefix = "# tasks-axi-json: "
            if not raw.startswith(prefix):
                continue
            record = json.loads(raw[len(prefix):])
            if record.get("id"):
                tasks[record["id"]] = record
        with open(frontier_path, encoding="utf-8") as frontier_file:
            frontier = json.load(frontier_file)
        for bucket, classification in (("items", "ranked"), ("unestimated", "unestimated")):
            for rank, item in enumerate(frontier[bucket], start=1):
                subject = item.get("subject", "")
                if not subject.startswith("task:"):
                    continue
                task_id = subject[len("task:"):]
                task = tasks.get(task_id)
                if not task or task.get("state") != "queued":
                    continue
                repo = task.get("repo") or os.path.basename(os.path.dirname(plan_path))
                key = (repo, task_id)
                if key in plans:
                    raise SystemExit(f"fm-plan-frontier: duplicate queued task in exported plans: {repo}/{task_id}")
                plans[key] = {
                    "classification": classification,
                    "rank": rank if classification == "ranked" else None,
                    "score": item.get("score") if classification == "ranked" else None,
                    "reason": item.get("reason", ""),
                }
        for item in frontier.get("blocked", []):
            if item.get("kind") != "blocked-alternative":
                continue
            subject = item.get("subject", "")
            if not subject.startswith("method:"):
                continue
            method = subject[len("method:"):]
            call_id = route_id(project, method)
            title = f"Planning compiler: rule on {method}"
            reason = f"Choose accept or reject for blocked alternative {method} in {project}: {item.get('reason', '')}"
            hold_bin = os.environ.get("FM_PLAN_CAPTAIN_HOLD_BIN", os.path.join(script_dir, "fm-captain-hold.sh"))
            held = run([hold_bin, "hold", call_id,
                        "--title", title, "--repo", project, "--reason", reason])
            if held.returncode:
                raise SystemExit(f"fm-plan-frontier: could not hold blocked alternative {method}: {held.stderr.strip()}")
            routes.append({
                "key": call_id, "type": "decision", "repo": project,
                "title": title, "detail": reason,
                "options": [{"value": "accept", "label": "Accept"}, {"value": "reject", "label": "Reject"}],
            })

with open(payload_path, encoding="utf-8") as payload_file:
    payload = json.load(payload_file)

for row in payload["charted"]:
    task_id = row.get("id")
    repo = row.get("repo")
    if not isinstance(task_id, str) or not isinstance(repo, str) or not repo:
        continue
    advisory = plans.get((repo, task_id))
    if not advisory:
        continue
    if advisory["classification"] == "ranked":
        score = advisory["score"]
        score_text = f"{score:.3f}" if isinstance(score, (int, float)) else "unknown"
        note = f"Plan frontier advisory: rank {advisory['rank']}, score {score_text}"
    else:
        note = "Plan frontier advisory: unestimated; no rank or score is available"
    if advisory["reason"]:
        note += f" ({advisory['reason']})"
    note += ". tasks-axi ready remains the dispatch source of truth."
    reason = row.get("reason", "")
    row["reason"] = f"{reason} · {note}" if reason else note

existing_keys = {item.get("key") for item in payload["captains_call"]}
payload["captains_call"].extend(card for card in routes if card["key"] not in existing_keys)

json.dump(payload, sys.stdout, ensure_ascii=False, separators=(",", ":"))
sys.stdout.write("\n")
PYEOF
