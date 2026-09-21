#!/usr/bin/env bash
# fm-stock-bash-baseline.sh - recompute the checked-in modern-bash baselines.
#
# Runs the two stock-Bash-lane suites under a modern bash, counts their
# '^ok - ' lines, and writes tests/*.stock-bash-baseline. The CI lane then
# runs each suite once under stock Bash and compares against these counts
# instead of re-running the modern baseline on every run: the duplicate
# executions were about half the lane's wall time, which is what kept the
# lane straddling its 10-minute timeout.
#
# Usage:
#   bin/fm-stock-bash-baseline.sh
set -eu

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

die() {
  printf 'fm-stock-bash-baseline.sh: %s\n' "$*" >&2
  exit 1
}

case "${BASH_VERSION:-}" in
  3.2.*) die "refusing to compute a modern-bash baseline under stock Bash $BASH_VERSION" ;;
esac

command -v tasks-axi >/dev/null || die "tasks-axi is required to compute the baselines"
command -v jq >/dev/null || die "jq is required to compute the baselines"

refresh_one() { # <suite>
  local suite=$1 tmpdir output count
  tmpdir=$(mktemp -d "${TMPDIR:-/tmp}/fm-stock-bash-baseline.XXXXXX")
  output=$(TMPDIR="$tmpdir" bash "$ROOT/tests/$suite.test.sh") || {
    rm -rf "$tmpdir"
    die "$suite failed; baseline not written"
  }
  rm -rf "$tmpdir"
  printf '%s\n' "$output" | grep -q '^ALL TESTS COMPLETED$' || {
    die "$suite did not complete; baseline not written"
  }
  count=$(printf '%s\n' "$output" | grep -c '^ok - ')
  cat > "$ROOT/tests/$suite.stock-bash-baseline" <<EOF
# Checked-in modern-bash baseline for the stock macOS Bash CI lane.
# The lane (.github/workflows/ci.yml) runs tests/$suite.test.sh once under stock Bash.
# It requires the suite's '^ok - ' count to equal the count below.
# Refresh with bin/fm-stock-bash-baseline.sh after adding or removing tests.
# Never hand-edit the count.
# A mismatch fails loudly so a stale baseline cannot hide a stock-bash regression.
count=$count
EOF
  printf 'fm-stock-bash-baseline.sh: %s -> %s\n' "$suite" "$count"
}

refresh_one fm-fleet-snapshot-view
refresh_one fm-bearings-snapshot
