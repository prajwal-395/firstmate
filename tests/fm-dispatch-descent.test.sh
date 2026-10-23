#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
TMP_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/fm-dispatch-descent.XXXXXX")
trap 'rm -rf "$TMP_ROOT"' EXIT
HOME_DIR="$TMP_ROOT/home"
mkdir -p "$HOME_DIR/config" "$HOME_DIR/data/task" "$HOME_DIR/state" "$TMP_ROOT/bin"
cat > "$HOME_DIR/config/dispatch-policy.json" <<'JSON'
{"version":2,"rules":[],"default_ladder":[
  {"id":"first","harness":"codex","model":"model-a","provider":"provider-a"},
  {"id":"next","harness":"codex","model":"model-b","provider":"provider-b"}
],"constraints":[],"providers":[]}
JSON
printf 'Task\n\nContinue the existing work.\n' > "$HOME_DIR/data/task/brief.md"
cat > "$HOME_DIR/state/task.meta" <<'META'
kind=scout
harness=codex
model=model-a
dispatch_unified=1
dispatch_source=default
dispatch_route_provider=provider-a
dispatch_project=sample
META
cat > "$TMP_ROOT/bin/quota-axi" <<'SH'
#!/usr/bin/env bash
printf '{"schemaVersion":5,"providers":[]}\n'
SH
cat > "$TMP_ROOT/control.sh" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$FM_DISPATCH_TEST_CONTROL_LOG"
SH
chmod +x "$TMP_ROOT/bin/quota-axi" "$TMP_ROOT/control.sh"
export PATH="$TMP_ROOT/bin:$PATH"
export FM_HOME="$HOME_DIR" FM_DISPATCH_DESCENT_CONTROL_BIN="$TMP_ROOT/control.sh"
export FM_DISPATCH_TEST_CONTROL_LOG="$TMP_ROOT/control.log"
reset_ms=$(( ($(date +%s) + 3600) * 1000 ))
"$ROOT/bin/fm-dispatch-reset.sh" record "$HOME_DIR/state" provider-a model-a default "$reset_ms" fixture

out=$("$ROOT/bin/fm-dispatch-descent.sh" "$HOME_DIR/state")
case "$out" in
  'relaunched task model-a -> codex/model-b'*) ;;
  *) echo "not ok - unified descent did not select the next eligible rung: ${out:-<silent>}" >&2; exit 1 ;;
esac
grep -qF 'task relaunch --harness codex --model model-b' "$FM_DISPATCH_TEST_CONTROL_LOG" \
  || { echo 'not ok - unified descent did not hand the selected rung to the control plane' >&2; exit 1; }
[ "$(cat "$HOME_DIR/state/.dispatch-descent-task")" = "$reset_ms|codex|model-b|none" ] \
  || { echo 'not ok - unified descent did not latch the reset episode' >&2; exit 1; }
printf '%s\n' 'ok - launch reset evidence re-resolves the policy and relaunches on the next eligible rung'
