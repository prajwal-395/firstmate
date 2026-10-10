#!/usr/bin/env bash
# Resolve a project's REGISTERED delivery posture from the data/projects.md registry.
# Default usage prints two words to stdout: "<mode> <yolo>" where mode is one of
# no-mistakes|direct-PR|local-only and yolo is on|off.
# --branch-prefix instead prints one value: the project's registered ship-branch
# prefix, "fm/" when the project registers none, is unregistered, or the registry
# is absent, so every existing installation keeps its current "fm/<task-id>"
# branch names unchanged.
# With --forge it prints one word instead: the project's registered forge,
# none|gerrit. The forge is asked for explicitly, so the default output stays
# the same two words for every project, bound or not.
#
# MECHANICAL CONSUMERS ONLY. This answers "what posture did the captain register
# for this project", never "how does this task ship". A task's delivery mode,
# yolo, and ship-branch prefix are resolved by firstmate at intake and passed
# explicitly to bin/fm-brief.sh, bin/fm-spawn.sh, and bin/fm-promote.sh (AGENTS.md
# section 7; bin/fm-brief.sh's own header owns the --branch-prefix flag it accepts).
# The consumers are bin/fm-fleet-sync.sh (skip local-only clones),
# bin/fm-home-seed.sh and bin/fm-remote-home-seed.sh (refuse local-only seeding,
# run no-mistakes init), bin/fm-spawn.sh's advisory registry-deviation notice,
# and --forge for bin/fm-spawn.sh's forge agreement and yolo refusal and for
# bin/fm-promote.sh, which takes the forge binding from here because it is a
# project fact rather than a task choice.
#
# Registry line format (data/projects.md):
#   - <name> - <desc> (added <date>)                  -> no-mistakes off  (legacy default)
#   - <name> [<mode>] - <desc> (added <date>)          -> <mode> off
#   - <name>,<alias> [<mode> +yolo] - <desc>           -> <mode> on (for both name and alias)
#
# Registered modes:
#   no-mistakes            full pipeline -> PR -> configured merge authority (default)
#   direct-PR              push + PR via gh-axi, no pipeline
#   local-only             local branch, no remote/PR, guarded local merge
#   no-mistakes-prod-only  a conditional policy, not a task mode: firstmate
#                          classifies each task's surface at intake (the
#                          project-management skill owns that classification).
#                          Mechanical output maps it to its most rigorous leg,
#                          no-mistakes, so sync, seeding, and init treat such a
#                          project as the remote-backed pipeline project it is.
# yolo (orthogonal) = merge authority only: when on, firstmate merges green,
#   in-scope work itself (AGENTS.md section 7).
# branch=<prefix> (orthogonal) = overrides the "fm/" ship-branch prefix so a
#   project's branch and PR do not read as firstmate-authored, e.g. for a
#   third-party repo that does not use this tooling. Query it with
#   --branch-prefix; it never appears in the default "<mode> <yolo>" output, so
#   existing mechanical callers are unaffected by its presence.
# forge (orthogonal, and orthogonal to yolo too) = which forge the project's
#   remote actually is, never inferred from mode, remote name, host, or protocol.
#   `none` means a forge whose pull requests and checks no-mistakes already
#   drives, and `gerrit` means a Gerrit server: no pull requests, so the worker
#   publishes a change with gerrit-axi instead (bin/fm-dod-lib.sh owns what that
#   changes for a worker in each publishing mode).
#   The binding is EXPLICIT because a provider family must never be guessed;
#   bin/fm-forge-detect.sh proposes it from a protocol fact at project-add
#   intake, and the captain's confirmation is what this record holds.
#   A forge describes what a mode publishes, so it composes with no-mistakes and
#   direct-PR and is REFUSED on local-only, which publishes nothing: that mode
#   lands by fast-forwarding local main, which on a review-server project
#   advances it with content the server has never seen
#   (docs/gerrit-forge-integration.md section 3).
#
# A registered `forge=gerrit` project reports yolo=off with an explicit stderr
# refusal, on the captain's decision of 2026-09-15: a Gerrit Code-Review+2 is a
# positive attributed claim that a named human approved, read by colleagues and
# by any audit, and firstmate must not manufacture one.
#
# --raw prints the registered mode annotation unmapped, so a caller that must
# tell a conditional policy apart from a flat mode sees "no-mistakes-prod-only"
# itself. Not combined with --branch-prefix, which has no conditional-policy leg.
#
# An unknown/missing project or unknown mode falls back to "no-mistakes off" (or
# "fm/" under --branch-prefix) and warns to stderr, so a typo never silently
# drops the gate. Other annotation tokens are ignored, as they always were, keyed
# ones included: a `<key>=<value>` token whose key is neither exactly `forge` nor
# `branch` resolves as it did before the forge existed, and in the mode slot it
# is read as an unknown mode. A key one or two edits from `forge` (such as
# `forg=` or `Forge=`) is still ignored, with one stderr warning naming the token
# and the forge=gerrit spelling. The one refusal is a malformed forge binding - a
# `forge=` token whose value is empty or outside the closed set - which is
# REFUSED in the default and --forge output forms: nothing on stdout, exit
# status 3, the token named. Resolving it to "no registered forge" would hand a
# Gerrit project the pull-request contract the binding exists to prevent.
# local-only with a forge is refused the same way. --branch-prefix does not make
# that check: it answers only the registered prefix, and a prefix is orthogonal
# to the forge binding, so it prints even when the forge token is malformed;
# every path that reads the forge binding (default, --forge, and spawn's
# forge-agreement check) still refuses.
# Usage: fm-project-mode.sh [--raw|--branch-prefix|--forge] <project-name>
set -eu

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FM_ROOT="${FM_ROOT_OVERRIDE:-$(cd "$SCRIPT_DIR/.." && pwd)}"
FM_HOME="${FM_HOME:-${FM_ROOT_OVERRIDE:-$FM_ROOT}}"
DATA="${FM_DATA_OVERRIDE:-$FM_HOME/data}"
REG="$DATA/projects.md"
RAW=0
BRANCH_PREFIX_QUERY=0
WANT_FORGE=0
case "${1:-}" in
  --raw) RAW=1; shift ;;
  --branch-prefix) BRANCH_PREFIX_QUERY=1; shift ;;
  --forge) WANT_FORGE=1; shift ;;
esac
NAME=${1:?usage: fm-project-mode.sh [--raw|--branch-prefix|--forge] <project-name>}

if [ ! -f "$REG" ]; then
  echo "warn: no registry at $REG; defaulting $NAME to no-mistakes off" >&2
  if [ "$BRANCH_PREFIX_QUERY" -eq 1 ]; then
    echo "fm/"
  elif [ "$WANT_FORGE" -eq 1 ]; then echo none; else echo "no-mistakes off"; fi
  exit 0
fi

# awk emits "<mode> <yolo>" (one line) or "NOT_FOUND: name1 name2 ..." if the project is absent.
parsed=$(awk -v n="$NAME" '
  BEGIN { found=0; total_names=0 }
  /^-[ \t]/ && ($3 ~ /^\[/ || $0 ~ /\(added [0-9]{4}-[0-9]{2}-[0-9]{2}\)/) {
    num = split($2, names, ",");
    for (i=1; i<=num; i++) {
      all_names[++total_names] = names[i]
      if (names[i] == n && !found) {
        found=1;
        mode="no-mistakes"; yolo="off";
        if ($3 ~ /^\[/) {
          s="";
          for (j=3; j<=NF; j++) { s = s (s==""?"":" ") $j; if ($j ~ /\]$/) break }
          gsub(/^\[|\]$/, "", s);           # strip the surrounding brackets
          k = split(s, a, " ");
          if (a[1] != "" && a[1] != "+yolo") mode = a[1];
          for (l=1; l<=k; l++) if (a[l]=="+yolo") yolo="on";
        }
      }
    }
  }
  END {
    if (found) {
      print mode, yolo;
    } else {
      printf "NOT_FOUND:";
      for (i=1; i<=total_names; i++) {
        printf " %s", all_names[i];
      }
      printf "\n";
    }
  }
' "$REG")

if [[ "$parsed" == NOT_FOUND:* ]]; then
  registered_names="${parsed#NOT_FOUND: }"
  echo "warn: project \"$NAME\" not in registry; defaulting to no-mistakes off" >&2
  if [ -n "$registered_names" ]; then
    echo "warn: registered names are: $registered_names" >&2
  fi
  echo "no-mistakes off"
  exit 0
fi

if [ -z "$parsed" ]; then
  echo "warn: project \"$NAME\" not in registry; defaulting to no-mistakes off" >&2
  if [ "$BRANCH_PREFIX_QUERY" -eq 1 ]; then
    echo "fm/"
  elif [ "$WANT_FORGE" -eq 1 ]; then echo none; else echo "no-mistakes off"; fi
  exit 0
fi

posture=
while IFS=' ' read -r kind rest; do
  case "$kind" in
    near) echo "warn: ignoring \"$rest\" registered for $NAME in $REG; it is not a forge binding, and the forge binding is spelled forge=gerrit" >&2 ;;
    posture) posture=$rest ;;
  esac
done <<EOF
$parsed
EOF
while IFS=' ' read -r m y f b; do
  mode=$m; yolo=$y; rest_forge=$f; branch=$b
done <<EOF
$posture
EOF
forge=${rest_forge:-none}
case "$mode" in
  no-mistakes|direct-PR|local-only|no-mistakes-prod-only) ;;
  *) echo "warn: unknown mode \"$mode\" for $NAME; defaulting to no-mistakes off" >&2; mode=no-mistakes; yolo=off; branch=fm/ ;;
esac
case "$yolo" in on|off) ;; *) yolo=off ;; esac
if [ "$BRANCH_PREFIX_QUERY" -eq 1 ]; then
  echo "$branch"
  exit 0
fi

case "$forge" in
  none|forge=gerrit) forge=${forge#forge=} ;;
  forge=)
    echo "refused: empty forge binding \"forge=\" registered for $NAME in $REG; the accepted value is forge=gerrit, or no forge token at all for a forge whose pull requests no-mistakes already drives; correct the registry entry" >&2
    exit 3 ;;
  *)
    echo "refused: unknown forge \"${forge#forge=}\" registered for $NAME in $REG; the accepted value is forge=gerrit, or no forge token at all for a forge whose pull requests no-mistakes already drives; correct the registry entry" >&2
    exit 3 ;;
esac
if [ "$forge" != none ] && [ "$mode" = local-only ]; then
  echo "refused: $NAME is registered local-only with forge=$forge in $REG; local-only publishes nothing, so a forge has no meaning there, and its landing would fast-forward local main with content the review server has never seen; register no-mistakes or direct-PR to publish through the forge, or drop the forge token to keep the project local" >&2
  exit 3
fi
if [ "$WANT_FORGE" -eq 1 ]; then
  echo "$forge"
  exit 0
fi
if [ "$forge" = gerrit ] && [ "$yolo" = on ]; then
  echo "refused: +yolo is registered for $NAME but yolo is inactive for forge=gerrit, so this reports yolo=off: a Gerrit Code-Review+2 is a positive attributed claim that a named human approved, and firstmate must not manufacture one (captain's decision 2026-09-15)" >&2
  yolo=off
fi
# A conditional policy is not a task mode. Mechanical callers get its most
# rigorous leg; --raw callers get the annotation itself (see the header).
if [ "$RAW" -eq 0 ] && [ "$mode" = no-mistakes-prod-only ]; then
  mode=no-mistakes
fi
echo "$mode $yolo"
