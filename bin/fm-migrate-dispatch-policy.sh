#!/usr/bin/env bash
# fm-migrate-dispatch-policy.sh - one-shot migration from crew-dispatch.json.
# Usage: FM_HOME=<home> bin/fm-migrate-dispatch-policy.sh [--force]
#
# Reads the home's legacy config/crew-dispatch.json and writes
# config/dispatch-policy.json (version 1, stable rule ids, explicit triggers,
# derived default_ladder) plus config/model-catalog.json seeded from the
# policy's model ids. The source file is only read, never modified or
# deleted, so the operator can diff the result before removing it by hand.
# Run once in each live home at migration time; secondmate homes receive
# both files through the ordinary inheritance flow instead.
#
# Idempotence: re-running over an unchanged source reproduces byte-identical
# output. An existing dispatch-policy.json that already matches is left alone
# (exit 0, "already current"); one that differs is refused unless --force is
# given, so a hand edit is never silently clobbered. The catalog merge never
# un-retires a model: existing entries keep their status and replacement, and
# only previously unseen model ids are added as active.
# Exit 0 on success or already-current, 1 on refusal or failure.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FM_HOME=${FM_HOME:-}
[ -n "$FM_HOME" ] || { echo 'error: FM_HOME must name the Firstmate home to migrate' >&2; exit 1; }
CONFIG=${FM_CONFIG_OVERRIDE:-$FM_HOME/config}
FORCE=0
for a in "$@"; do
  case "$a" in
    --force) FORCE=1 ;;
    -h|--help) sed -n '2,20p' "$0" | sed 's/^# //;s/^#//'; exit 0 ;;
    *) echo "error: unknown flag $a" >&2; exit 1 ;;
  esac
done
command -v jq >/dev/null 2>&1 || { echo 'error: jq is required' >&2; exit 1; }

SRC="$CONFIG/crew-dispatch.json"
POLICY="$CONFIG/dispatch-policy.json"
CATALOG="$CONFIG/model-catalog.json"
[ -f "$SRC" ] && [ ! -L "$SRC" ] || { echo "error: no regular legacy policy at $SRC" >&2; exit 1; }
jq -e . "$SRC" >/dev/null 2>&1 || { echo "error: malformed JSON in $SRC" >&2; exit 1; }

WORK=$(mktemp -d "${TMPDIR:-/tmp}/fm-migrate-dispatch.XXXXXX") || exit 1
trap 'rm -rf "$WORK"' EXIT

# ---- policy ---------------------------------------------------------------
jq '
  def slug: ascii_downcase | gsub("[^a-z0-9]+"; "-") | gsub("^-|-$"; "") | .[0:24];
  def profiles($v): if ($v | type) == "array" then $v elif ($v | type) == "object" then [$v] else [] end;
  def convert_rule($i):
    . as $r
    | (($r.when // "rule") | slug) as $base
    | {
        id: ($r.id // "\($base)-\($i)"),
        when: ($r.when // "migrated rule"),
        trigger: (
          if ($r.trigger | type) == "object" then $r.trigger
          elif (($r.projects | type) == "array") then {kind: "projects", names: $r.projects}
          else {kind: "declaration"} end
        )
      }
    + (if ($r.use | type) == "object" or ($r.use | type) == "array" then {use: $r.use} else {} end)
    + (if ($r.approval | type) == "string" then {approval: $r.approval} else {} end)
    + (if ($r.floor | type) == "object" then {floor: $r.floor} else {} end)
    + (if ($r.select | type) == "string" then {select: $r.select} else {} end)
    + (if ($r.why | type) == "string" then {why: $r.why} else {} end);
  def plus_filling:
    ([profiles(.default)[] | select(.harness == "codex" and (.model | type) == "string" and (.model | length) > 0)] | first);
  def go_filling:
    ([profiles(.default)[] | select(((.provider // "") == "opencode-go") or ((.model // "") | startswith("opencode-go/"))) | select((.model | type) == "string" and (.model | length) > 0)] | first);
  {
    version: 1,
    rules: ([.rules // [] | to_entries[] | . as $e | .value | convert_rule($e.key + 1)]),
    default: (.default // null),
    default_ladder: (
      if has("default") then
        [(plus_filling | select(. != null) | . + {id: "plus"}),
         (go_filling | select(. != null) | . + {id: "go"})]
      else [] end
    )
  }
  + (if (.agy_ladder | type) == "array" then {agy_ladder} else {} end)
  | del(.default_ladder | select(length == 0))
  | del(.default | select(. == null))
' "$SRC" > "$WORK/policy.json" || { echo "error: rule conversion failed for $SRC" >&2; exit 1; }

# A policy whose default names no Codex Plus or Go filling cannot declare the
# ladder: refuse rather than emit a policy the gates cannot enforce.
if jq -e 'has("default")' "$SRC" >/dev/null 2>&1; then
  jq -e '(.default_ladder // [] | map(.id) | sort) == ["go","plus"]' "$WORK/policy.json" >/dev/null 2>&1 || {
    echo "error: $SRC default names no Codex Plus or Go filling for default_ladder; add one before migrating" >&2
    exit 1
  }
fi
# Judgment routing is deleted, so two declaration-shaped rules cannot both
# survive: name them rather than silently dropping one.
jq -e '([.rules[]? | select(.trigger.kind == "declaration")] | length) <= 1' "$WORK/policy.json" >/dev/null 2>&1 || {
  dupes=$(jq -r '[.rules[] | select(.trigger.kind == "declaration") | .id] | join(", ")' "$WORK/policy.json")
  echo "error: $SRC converts to more than one declaration trigger ($dupes); keep one and re-run" >&2
  exit 1
}
jq -e '.version == 1 and (.rules | type) == "array" and all(.rules[]; (.id | type) == "string" and (.trigger.kind == "declaration" or .trigger.kind == "projects"))' \
  "$WORK/policy.json" >/dev/null 2>&1 || { echo "error: converted policy failed its own schema check; refusing to write" >&2; exit 1; }

if [ -e "$POLICY" ] || [ -L "$POLICY" ]; then
  if cmp -s "$WORK/policy.json" "$POLICY"; then
    echo "already current: $POLICY"
  elif [ "$FORCE" -eq 1 ]; then
    chmod 600 "$WORK/policy.json" || exit 1
    mv "$WORK/policy.json" "$POLICY" || exit 1
    echo "overwrote: $POLICY (--force)"
  else
    echo "error: $POLICY exists and differs from the converted policy; re-run with --force to overwrite" >&2
    exit 1
  fi
else
  chmod 600 "$WORK/policy.json" || exit 1
  mv "$WORK/policy.json" "$POLICY" || exit 1
  echo "wrote: $POLICY"
fi
if jq -e 'has("exhausted_ladder_fallback")' "$SRC" >/dev/null 2>&1; then
  echo "dropped: exhausted_ladder_fallback is retired and was not carried over"
fi
dropped=$(jq -r '[keys[] | select(startswith("_"))] | join(", ")' "$SRC")
[ -z "$dropped" ] || echo "dropped: prose fields not read by code ($dropped)"

# ---- catalog --------------------------------------------------------------
TODAY=$(date +%F)
if [ -e "$CATALOG" ]; then
  [ -f "$CATALOG" ] && [ ! -L "$CATALOG" ] || { echo "error: refusing non-regular catalog $CATALOG" >&2; exit 1; }
  jq -e '.models | type == "array"' "$CATALOG" >/dev/null 2>&1 || { echo "error: malformed catalog $CATALOG" >&2; exit 1; }
  cp "$CATALOG" "$WORK/old-catalog.json" || exit 1
else
  printf '{"models":[]}\n' > "$WORK/old-catalog.json"
fi
jq --slurpfile old "$WORK/old-catalog.json" --slurpfile policy "$POLICY" --arg date "$TODAY" '
  ($policy[0] |
   ([.default_ladder[]?.model] + [(.default | if type == "array" then .[] else . end | .model // empty)]
    + [.rules[]?.use | if type == "array" then .[] else . end | .model // empty])
   | map(select(type == "string" and length > 0)) | unique | sort) as $ids |
  ($old[0].models // []) as $previous |
  ([$ids[] | {id: ., status: "active",
               bills: (if startswith("opencode-go/") then "go" else "none" end),
               last_verified: $date, source: "migration from crew-dispatch.json"}]
   | map(. as $fresh | ($previous[] | select(.id == $fresh.id)) // $fresh)) as $merged |
  {models: (($merged + [$previous[] | select(.id as $id | ($ids | index($id)) == null)]) | group_by(.id) | map(.[-1]) | sort_by(.id))}
' "$WORK/old-catalog.json" > "$WORK/catalog.json" || { echo "error: catalog merge failed" >&2; exit 1; }
added=$(jq -r --slurpfile old "$WORK/old-catalog.json" '
  (($old[0].models // []) | map(.id)) as $had |
  [.models[] | select(.id as $id | ($had | index($id)) == null) | .id] | join(", ")' "$WORK/catalog.json")
if [ -e "$CATALOG" ] && cmp -s "$WORK/catalog.json" "$CATALOG"; then
  echo "already current: $CATALOG"
else
  chmod 600 "$WORK/catalog.json" || exit 1
  mv "$WORK/catalog.json" "$CATALOG" || exit 1
  if [ -n "$added" ]; then
    echo "wrote: $CATALOG (added $added; existing statuses kept)"
  else
    echo "wrote: $CATALOG (existing statuses kept)"
  fi
fi
echo "source untouched: $SRC (remove it by hand after diffing)"
echo "next: run bin/fm-model-catalog-refresh.sh to verify the catalog against vendor listings"
