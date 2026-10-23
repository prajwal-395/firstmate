#!/usr/bin/env bash
# fm-migrate-unified-dispatch.sh - migrate a home's v1 policy to unified v2.
# Usage: FM_HOME=<home> bin/fm-migrate-unified-dispatch.sh [--force]
# The source policy and model catalog are snapshotted before writes. Routes
# retain their existing order and profile fields; the command never edits a
# live home unless the operator invokes it with that home's FM_HOME.
set -u

FM_HOME=${FM_HOME:-}
[ -n "$FM_HOME" ] || { echo 'error: FM_HOME must name the Firstmate home to migrate' >&2; exit 1; }
CONFIG=${FM_CONFIG_OVERRIDE:-$FM_HOME/config}
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXAMPLE="$SCRIPT_DIR/../docs/examples/dispatch-policy.json"
POLICY="$CONFIG/dispatch-policy.json"
CATALOG="$CONFIG/model-catalog.json"
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    -h|--help) sed -n '2,7p' "$0" | sed 's/^# //'; exit 0 ;;
    *) echo "error: unknown flag $arg" >&2; exit 1 ;;
  esac
done
command -v jq >/dev/null 2>&1 || { echo 'error: jq is required' >&2; exit 1; }
[ -f "$POLICY" ] && [ ! -L "$POLICY" ] || { echo "error: no regular policy at $POLICY" >&2; exit 1; }
jq -e 'type == "object" and (.version == 1 or .version == 2)' "$POLICY" >/dev/null 2>&1 \
  || { echo "error: expected a version 1 or 2 policy at $POLICY" >&2; exit 1; }

tmp=$(mktemp -d "${TMPDIR:-/tmp}/fm-migrate-unified-dispatch.XXXXXX") || exit 1
trap 'rm -rf "$tmp"' EXIT
cp "$POLICY" "$tmp/source.json" || exit 1
if [ -e "$CATALOG" ] || [ -L "$CATALOG" ]; then
  [ -f "$CATALOG" ] && [ ! -L "$CATALOG" ] || { echo "error: refusing non-regular catalog $CATALOG" >&2; exit 1; }
  cp "$CATALOG" "$tmp/catalog.json" || exit 1
else
  printf '{"models":[]}\n' > "$tmp/catalog.json"
fi
jq -e '.models | type == "array"' "$tmp/catalog.json" >/dev/null 2>&1 \
  || { echo "error: malformed model catalog $CATALOG" >&2; exit 1; }

if jq -e '.version == 2' "$tmp/source.json" >/dev/null; then
  echo "already current: $POLICY"
  exit 0
fi

jq --slurpfile template "$EXAMPLE" '
  def profiles($v): if ($v | type) == "array" then $v elif ($v | type) == "object" then [$v] else [] end;
  def rung($i; $prefix): . as $r | $r + {
    id: ($r.id // "\($prefix)-rung-\($i)"),
    accounts: ($r.accounts // $r.workspaces // []),
    account_strategy: ($r.account_strategy // $r.pick // "ordered")
  } | del(.workspaces, .pick);
  def ladder($v; $prefix): [profiles($v) | to_entries[] | . as $entry | .value | rung($entry.key + 1; $prefix)];
  . as $source
  | {
      version: 2,
      rules: [($source.rules // []) | to_entries[] | . as $entry | .value
        | . + {ladder: ladder(.ladder // .use; .id)} | del(.use)],
      default_ladder: ladder($source.default_ladder // $source.default; "default"),
      constraints: ($source.constraints // []),
      providers: (
        ([$source.providers[]?]
          + [$template[0].providers[]?]
          + [(([$source.rules[]?.use, $source.rules[]?.ladder[]?, $source.default[]?, $source.default_ladder[]?] | flatten)[]? | .provider? | select(type == "string") | {id:., refusal_patterns:[]})])
        | reduce .[] as $provider ([]; if any(.[]; .id == $provider.id) then . else . + [$provider] end)
      )
    }
  | .constraints += [
      {id:"worker-claude-approval",when:{scope:"worker",provider:"anthropic"},approval:"captain"},
      {id:"research-no-claude",when:{home_role:"research",provider:"anthropic"},deny:true}
    ]
  | .constraints |= unique_by(.id)
' "$tmp/source.json" > "$tmp/policy.json" || { echo 'error: policy conversion failed' >&2; exit 1; }
jq -e '.version == 2 and (.rules | type == "array") and (.default_ladder | type == "array") and all(.default_ladder[]; (.id | type == "string") and (.harness | type == "string") and (.model | type == "string") and (.provider | type == "string"))' "$tmp/policy.json" >/dev/null 2>&1 \
  || { echo 'error: converted policy failed schema checks' >&2; exit 1; }
"$SCRIPT_DIR/fm-dispatch-policy-validate.sh" "$tmp/policy.json" || exit 1

if [ -e "$POLICY" ] && [ "$FORCE" -ne 1 ]; then
  echo "error: replacing the v1 policy requires --force; inspect the converted policy first" >&2
  cp "$tmp/policy.json" "$tmp/preview.json"
  echo "preview: $tmp/preview.json" >&2
  exit 1
fi
chmod 600 "$tmp/policy.json" || exit 1
mv "$tmp/policy.json" "$POLICY" || exit 1
if [ ! -e "$CATALOG" ]; then
  chmod 600 "$tmp/catalog.json" || exit 1
  mv "$tmp/catalog.json" "$CATALOG" || exit 1
fi
echo "wrote: $POLICY (v2; existing route order retained)"
echo "catalog preserved: $CATALOG"
