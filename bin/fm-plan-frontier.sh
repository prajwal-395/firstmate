#!/usr/bin/env bash
# fm-plan-frontier.sh - add advisory planning-compiler frontier notes to a Bearings payload.
#
# Usage:
#   fm-plan-frontier.sh --payload <board.json> [--data-dir <dir>] [--out <dir>]
#   fm-plan-frontier.sh --help
#
# Requires PC_BIN to name an executable planning-compiler `pc` binary. It never
# searches PATH for `pc`. It exports the active home's backlog through
# fm-plan-export.sh, then runs `pc check`, `pc compile`, and `pc frontier --json`
# once for each plan exported by that invocation. Exported plans go to --out,
# defaulting to the selected data directory (<FM_HOME>/data by default).
#
# The output is the input fm-bearings-board.v1 payload with only existing
# Charted Next reasons annotated for matching queued tasks. Compiler ranking
# never adds rows, changes dispatchable, or routes proposals/blocked alternatives;
# tasks-axi ready remains the dispatch source of truth. All other payload fields
# are preserved. The complete result is validated through fm-bearings-board.sh
# before it is written to stdout.
set -eu

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FM_ROOT="${FM_ROOT_OVERRIDE:-$(cd "$SCRIPT_DIR/.." && pwd)}"
FM_HOME="${FM_HOME:-${FM_ROOT_OVERRIDE:-$FM_ROOT}}"
DATA="${FM_DATA_OVERRIDE:-$FM_HOME/data}"
PAYLOAD=
DATA_FLAG=
OUT_FLAG=

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
if ! FM_HOME="$FM_HOME" FM_DATA_OVERRIDE="$DATA" \
  "$SCRIPT_DIR/fm-plan-export.sh" --data-dir "$DATA" --out "$OUT" \
  >"$exported_log" 2>&1; then
  cat "$exported_log" >&2
  fail "backlog export failed"
fi
cat "$exported_log" >&2

awk 'substr($0, 1, 7) == "wrote: " { line = substr($0, 8); sub(/ tasks [0-9]+ edges [0-9]+$/, "", line); print line }' \
  "$exported_log" > "$scratch/plans.list"
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

python3 - "$PAYLOAD" "$scratch/manifest.tsv" <<'PYEOF'
import json
import os
import sys

payload_path, manifest_path = sys.argv[1:]
plans = {}

with open(manifest_path, encoding="utf-8") as manifest:
    for line in manifest:
        plan_path, frontier_path = line.rstrip("\n").split("\t", 1)
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

json.dump(payload, sys.stdout, ensure_ascii=False, separators=(",", ":"))
sys.stdout.write("\n")
PYEOF
