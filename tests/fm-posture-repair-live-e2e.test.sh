#!/usr/bin/env bash
# Opt-in live guard for restored OpenCode posture repair and exit safety.
#
# It opens a real, prompt-free OpenCode TUI in a named Herdr lab, with a
# background shell job sharing the pane tty. The live backend probe must see
# that job as a separate process group before repair could send /exit. No
# model turn is submitted. Every Herdr call is routed through fm-herdr-lab.sh.
set -u

# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERDR_LAB_HELPER=${HERDR_LAB_HELPER:-$ROOT/bin/fm-herdr-lab.sh}

fail() { printf 'not ok - %s\n' "$1" >&2; exit 1; }
pass() { printf 'ok - %s\n' "$1"; }

fm_live_gate opt-in FM_POSTURE_REPAIR_LIVE_E2E herdr jq opencode

[ -x "$HERDR_LAB_HELPER" ] \
  || fail "FM_POSTURE_REPAIR_LIVE_E2E=1 but the Herdr lab helper is not executable at $HERDR_LAB_HELPER"

ORIGINAL_PATH=$PATH
HERDR_LAB_SESSION=$("$HERDR_LAB_HELPER" name fm-posture-repair-live-e2e)
TMP_ROOT=$(mktemp -d "$(cd "${TMPDIR:-/tmp}" && pwd -P)/fm-posture-repair-live-e2e.XXXXXX")
FAKEBIN="$TMP_ROOT/fakebin"
BG_PID_FILE="$TMP_ROOT/background.pid"
mkdir -p "$FAKEBIN"
PANE=
BG_PID=

lab() { env PATH="$ORIGINAL_PATH" "$HERDR_LAB_HELPER" run "$HERDR_LAB_SESSION" "$@"; }

cleanup() {
  local rc=$? screen=''
  trap - EXIT
  if [ -n "$PANE" ]; then
    screen=$(lab pane read "$PANE" --source recent --lines 80 2>/dev/null || true)
    if printf '%s\n' "$screen" | grep -Fq 'Ask anything'; then
      lab pane send-text "$PANE" '/exit' >/dev/null 2>&1 || true
      lab pane send-keys "$PANE" Enter >/dev/null 2>&1 || true
    fi
  fi
  if [ -z "$BG_PID" ] && [ -r "$BG_PID_FILE" ]; then
    BG_PID=$(cat "$BG_PID_FILE")
  fi
  case "$BG_PID" in
    ''|*[!0-9]*) ;;
    *) kill "$BG_PID" 2>/dev/null || true ;;
  esac
  if ! PATH="$ORIGINAL_PATH" "$HERDR_LAB_HELPER" teardown "$HERDR_LAB_SESSION"; then
    rc=1
  fi
  rm -rf "$TMP_ROOT"
  exit "$rc"
}
trap cleanup EXIT

export HERDR_LAB_SESSION HERDR_LAB_HELPER ORIGINAL_PATH
cat > "$FAKEBIN/herdr" <<'EOF'
#!/usr/bin/env bash
set -u
args=("$@")
n=${#args[@]}
if [ "$n" -ge 2 ] && [ "${args[$((n-2))]}" = --session ]; then
  [ "${args[$((n-1))]}" = "$HERDR_LAB_SESSION" ] || { echo "wrapper refused foreign session" >&2; exit 97; }
  args=("${args[@]:0:$((n-2))}")
else
  echo "wrapper requires trailing --session $HERDR_LAB_SESSION" >&2
  exit 98
fi
exec env PATH="$ORIGINAL_PATH" "$HERDR_LAB_HELPER" run "$HERDR_LAB_SESSION" "${args[@]}"
EOF
chmod +x "$FAKEBIN/herdr"

"$HERDR_LAB_HELPER" provision "$HERDR_LAB_SESSION" \
  || fail "could not provision the isolated Herdr lab"
export PATH="$FAKEBIN:$ORIGINAL_PATH"
export HERDR_SESSION="$HERDR_LAB_SESSION"

# shellcheck source=bin/fm-control-lib.sh
. "$ROOT/bin/fm-control-lib.sh"
# shellcheck source=bin/fm-posture-lib.sh
. "$ROOT/bin/fm-posture-lib.sh"
# shellcheck source=bin/fm-backend.sh
. "$ROOT/bin/fm-backend.sh"

HERDR_EVIDENCE=$(lab status --json) || fail "could not read the isolated Herdr client version"
HERDR_VERSION=$(printf '%s' "$HERDR_EVIDENCE" | jq -er '"\(.client.version) protocol \(.client.protocol)"') \
  || fail "isolated Herdr did not report its version and protocol"
OPENCODE_VERSION=$(PATH="$ORIGINAL_PATH" opencode --version 2>&1 | head -1) \
  || fail "could not read the installed OpenCode version"
OPENCODE_HELP=$(PATH="$ORIGINAL_PATH" opencode --help 2>&1) \
  || fail "could not read OpenCode's CLI help"
printf '%s\n' "$OPENCODE_HELP" | grep -Eq -- '--session([[:space:]]|=)' \
  || fail "OpenCode $OPENCODE_VERSION no longer documents an explicit session selector"

WS_JSON=$(lab workspace create --cwd "$ROOT" --label fm-posture-live --no-focus) \
  || fail "could not create a workspace in the isolated Herdr lab"
PANE=$(printf '%s' "$WS_JSON" | jq -er '.result.root_pane.pane_id') \
  || fail "workspace creation did not return a root pane id"
TARGET="$HERDR_LAB_SESSION:$PANE"

# Launch from the workspace shell with a real background job and then the
# prompt-free OpenCode TUI. The job remains in a different process group on the
# same tty while OpenCode owns the foreground group.
START_COMMAND="sleep 120 & bg=\$!; printf \"%s\\n\" \"\$bg\" > '$BG_PID_FILE'; OPENCODE_CONFIG_CONTENT='{\"permission\":{\"*\":\"allow\"}}' opencode; kill \"\$bg\" 2>/dev/null || true"
lab pane send-text "$PANE" "$START_COMMAND" >/dev/null \
  || fail "could not type the OpenCode launch command into the isolated pane"
lab pane send-keys "$PANE" Enter >/dev/null \
  || fail "could not start OpenCode in the isolated pane"

READY=0
ATTEMPT=0
SCREEN=
while [ "$ATTEMPT" -lt 60 ]; do
  SCREEN=$(lab pane read "$PANE" --source recent --lines 120 2>/dev/null || true)
  if printf '%s\n' "$SCREEN" | grep -Fq 'Ask anything'; then
    READY=1
    break
  fi
  ATTEMPT=$((ATTEMPT + 1))
  sleep 1
done
[ "$READY" -eq 1 ] \
  || fail "OpenCode $OPENCODE_VERSION did not render its empty composer in the isolated pane"
[ -s "$BG_PID_FILE" ] || fail "the pane did not start its background shell job"
BG_PID=$(cat "$BG_PID_FILE")
case "$BG_PID" in ''|*[!0-9]*) fail "the background job did not publish a valid pid" ;; esac

ACTUAL_PATH=$(fm_backend_current_path herdr "$TARGET" 2>/dev/null || true)
fm_posture_current_path_matches herdr "$TARGET" "$ROOT" \
  || fail "the live OpenCode process is not running in the recorded workspace (reported cwd: ${ACTUAL_PATH:-unavailable})"
background_status=0
fm_backend_tty_background_process_group herdr "$TARGET" || background_status=$?
[ "$background_status" -eq 2 ] \
  || fail "the live Herdr tty probe should positively identify the background job, got status $background_status"
pass "OpenCode $OPENCODE_VERSION opened without a prompt in Herdr $HERDR_VERSION; the recorded cwd matches and the live tty probe defers on a background process"

lab pane send-text "$PANE" '/exit' >/dev/null \
  || fail "could not exit the prompt-free OpenCode TUI"
lab pane send-keys "$PANE" Enter >/dev/null \
  || fail "could not submit OpenCode's /exit command"

SHELL_READY=0
ATTEMPT=0
while [ "$ATTEMPT" -lt 30 ]; do
  INFO=$(lab pane process-info --pane "$PANE" 2>/dev/null || true)
  if printf '%s' "$INFO" | jq -e '
    .result.process_info.shell_pid == .result.process_info.foreground_process_group_id
    and (.result.process_info.foreground_processes | length == 1)
  ' >/dev/null 2>&1; then
    SHELL_READY=1
    break
  fi
  ATTEMPT=$((ATTEMPT + 1))
  sleep 1
done
[ "$SHELL_READY" -eq 1 ] || fail "OpenCode did not return the isolated pane to its shell"
if kill -0 "$BG_PID" 2>/dev/null; then
  fail "OpenCode's shell did not clean up the exact background job it started"
fi
pass "OpenCode /exit returned to the isolated shell and its exact background job ended"

printf 'evidence: herdr=%s opencode=%s session=%s\n' \
  "$HERDR_VERSION" "$OPENCODE_VERSION" "$HERDR_LAB_SESSION"
