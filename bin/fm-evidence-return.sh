#!/usr/bin/env bash
# fm-evidence-return.sh - append completed task PR timing as planning evidence.
#
# Usage:
#   PC=/path/to/pc fm-evidence-return.sh [--data-dir <dir>]
#   fm-evidence-return.sh --help
#
# Reads completed rows through bin/fm-tasks-axi.sh and their merged PR timing
# through gh-axi. Each unique PR URL is recorded on the matching task with
# duration_minutes, unit minutes, and basis absolute. The source identifies
# the PR open-to-merge measurement. Evidence is appended only by `pc record`.
# PC must name the planning-compiler executable; absence is a refusal.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FM_ROOT="${FM_ROOT_OVERRIDE:-$(cd "$SCRIPT_DIR/.." && pwd)}"
FM_HOME="${FM_HOME:-${FM_ROOT_OVERRIDE:-$FM_ROOT}}"
WRAPPER="$SCRIPT_DIR/fm-tasks-axi.sh"

usage() {
  awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$0"
}
fail() { printf 'fm-evidence-return: %s\n' "$*" >&2; exit 2; }

DATA="${FM_DATA_OVERRIDE:-$FM_HOME/data}"
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --data-dir) [ $# -ge 2 ] || fail "--data-dir needs a value"; DATA="$2"; shift 2 ;;
    --data-dir=*) DATA="${1#--data-dir=}"; shift ;;
    --) shift; break ;;
    -*) fail "unknown flag: $1" ;;
    *) fail "unexpected argument: $1" ;;
  esac
done
[ $# -eq 0 ] || fail "unexpected argument: $1"
[ -n "${PC:-}" ] || fail "PC must name the planning-compiler executable"
[ -x "$PC" ] || fail "PC is not executable: $PC"
[ -x "$WRAPPER" ] || fail "cannot execute $WRAPPER"
command -v tasks-axi >/dev/null 2>&1 || fail "tasks-axi is not on PATH"
command -v gh-axi >/dev/null 2>&1 || fail "gh-axi is not on PATH"
command -v python3 >/dev/null 2>&1 || fail "python3 is not on PATH"

export FM_DATA_OVERRIDE="$DATA"
python3 - "$WRAPPER" "$DATA" "$PC" <<'PYEOF'
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime

wrapper, data_dir, pc = sys.argv[1:]
PR_RE = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/(\d+)")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def run(args, *, env=None):
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, env=env)


def unquote(value):
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value[1:-1]
    return value


def parse_show(text):
    result = {}
    for line in text.splitlines():
        if not line.startswith("  ") or line.startswith("  -"):
            continue
        key, sep, value = line.strip().partition(":")
        if sep:
            result[key.strip()] = unquote(value.strip())
    return result


def slug(repo):
    value = re.sub(r"[^a-z0-9]+", "-", repo.lower()).strip("-")
    return value or "backlog"


def pr_times(url, owner, repo, number):
    api_repo = f"{owner}/{repo}"
    proc = run(["gh-axi", "api", f"repos/{api_repo}/pulls/{number}",
                "--jq", '.created_at + "\\t" + (.merged_at // "") + "\\t" + (.html_url // "")'])
    if proc.returncode:
        raise RuntimeError(f"gh-axi could not read {url}: {proc.stderr.strip()}")
    try:
        opened, merged, canonical = proc.stdout.strip().split("\t", 2)
        if not merged:
            return None
        start = datetime.fromisoformat(opened.replace("Z", "+00:00"))
        end = datetime.fromisoformat(merged.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError) as exc:
        raise RuntimeError(f"gh-axi returned invalid merged PR data for {url}: {exc}")
    if end < start:
        raise RuntimeError(f"PR is not merged or has invalid timestamps: {url}")
    return round((end - start).total_seconds() / 60, 3), merged, canonical or url


def already_recorded(evidence_path, subject, source):
    try:
        with open(evidence_path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("subject") == subject and row.get("source", "").startswith(source):
                    return True
    except FileNotFoundError:
        pass
    return False


listing = run([wrapper, "list", "--state", "done", "--fields", "links"])
if listing.returncode:
    sys.stderr.write(f"fm-evidence-return: tasks-axi list failed: {listing.stderr.strip()}\n")
    sys.exit(2)

ids = []
for line in listing.stdout.splitlines():
    if line.startswith("  ") and not line.startswith("  -"):
        candidate = line.strip().split(",", 1)[0].strip()
        if candidate and candidate not in ids:
            ids.append(candidate)

appended = skipped = 0
for task_id in ids:
    if not ID_RE.fullmatch(task_id):
        print(f"skipped: {task_id} (invalid task id)")
        skipped += 1
        continue
    detail = run([wrapper, "show", task_id, "--full"])
    if detail.returncode:
        sys.stderr.write(f"fm-evidence-return: tasks-axi show {task_id} failed: {detail.stderr.strip()}\n")
        sys.exit(2)
    record = parse_show(detail.stdout)
    links = record.get("links", "")
    prs = list(dict.fromkeys(match.group(0) for match in PR_RE.finditer(links)))
    if not prs:
        print(f"skipped: {task_id} (no GitHub PR link)")
        skipped += 1
        continue
    subject = f"task:{task_id}"
    for url in prs:
        match = PR_RE.fullmatch(url)
        project = slug(match.group(2))
        evidence_path = os.path.join(data_dir, project, "evidence.jsonl")
        if not os.path.isfile(os.path.join(data_dir, project, "plan.toml")):
            print(f"skipped: {task_id} (no exported plan for {project})")
            skipped += 1
            continue
        try:
            timing = pr_times(url, match.group(1), match.group(2), match.group(3))
        except RuntimeError as exc:
            sys.stderr.write(f"fm-evidence-return: {exc}\n")
            sys.exit(2)
        if timing is None:
            print(f"skipped: {task_id} ({url} is not merged)")
            skipped += 1
            continue
        value, merged, canonical = timing
        source = f"{canonical} open-to-merge; task {task_id}"
        if already_recorded(evidence_path, subject, source):
            print(f"skipped: {task_id} ({canonical} already recorded)")
            skipped += 1
            continue
        evidence = {
            "subject": subject,
            "metric": "duration_minutes",
            "value": value,
            "unit": "minutes",
            "time": merged,
            "source": source,
            "basis": "absolute",
            "artifact_hash": None,
        }
        fd, evidence_file = tempfile.mkstemp(prefix="fm-evidence-return-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(evidence, handle, ensure_ascii=False, separators=(",", ":"))
                handle.write("\n")
            proc = run([pc, "record", evidence_file, os.path.dirname(evidence_path)])
            if proc.returncode:
                sys.stderr.write(f"fm-evidence-return: pc record failed for {task_id}: {proc.stderr.strip()}\n")
                sys.exit(2)
            sys.stdout.write(proc.stdout)
            appended += 1
        finally:
            os.unlink(evidence_file)

print(f"evidence-return: appended {appended} record(s), skipped {skipped} completed task(s)")
PYEOF
