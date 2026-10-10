#!/usr/bin/env bash
# tests/fm-decision-fold-scale.test.sh - the decision fold stays fast and exact
# as status logs grow (bin/fm-classify-lib.sh). A 1.7 MB status log folded the
# whole stream in bash at several forks per line - about a minute per call -
# and that fold runs on the watcher's own poll path (fleet snapshot per task,
# pending-reply tick) and on every wake drain (replacement replay), stalling
# the liveness beacon past its grace. The fold now prefilters to the lines that
# can open or close a decision before touching bash. These drive the REAL fold,
# closing-verb, replacement, and origins functions over a crafted edge corpus
# (asserting byte-exact outputs) and over a large routine log (asserting the
# buried decisions still fold identically and quickly), never the fold's source.
set -u

# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# shellcheck source=bin/fm-classify-lib.sh
. "$ROOT/bin/fm-classify-lib.sh"

TMP_ROOT=$(fm_test_tmproot fm-decision-fold-scale-tests)

test_edge_corpus_folds_exactly() {
  local dir expected
  dir="$TMP_ROOT/edge"
  mkdir -p "$dir"
  cat > "$dir/edge.status" <<'EOF'
working: starting up
needs-decision [key=api-shape]: pick REST or RPC
working: still thinking
note: the api-shape question needs-decision prose mention
needs-decision: [key=late-key] colon-first form
blocked [key=dep]: waiting on vendor
resolved [key=api-shape]: captain picked REST
captain-held [key=late-key]: tracked by task late-key
needs-decision [key=pending-reply-abc]: pending-reply-abc: recovery delivery failed
needs-decision [key=pending-reply-abc]: foreign prose without the vocabulary
resolved [key=pending-reply-abc]: bare answered note that must not close
resolved [key=pending-reply-abc]: pending-reply-abc: resolved after redelivery
needs-decision: default bucket question one
needs-decision: default bucket question two replaces one
resolved: all default questions settled
corr=abcdef0123456789 needs-decision [key=corr-key]: token before verb
needs-decision [tag=x]: [key=tag-key] verb ends at tag
needs-decision [key=bad key]: invalid slug is ordinary status
working: [key=quiet-key] a keyed working line changes nothing
failed: something broke at the end
EOF
  expected=$(printf 'dep\tblocked\twaiting on vendor\ntag-key\tneeds-decision\tverb ends at tag')
  [ "$(status_open_decisions "$dir/edge.status")" = "$expected" ] \
    || fail "edge open-set mismatch: got '$(status_open_decisions "$dir/edge.status")'"
  [ "$(status_open_decisions_incremental "$dir/edge.status")" = "$expected" ] \
    || fail "edge incremental fold diverged from the whole-file fold"
  [ "$(status_key_closing_verb "$dir/edge.status" default)" = resolved ] \
    || fail "default key should read resolved"
  [ "$(status_key_closing_verb "$dir/edge.status" dep)" = blocked ] \
    || fail "dep key should stay blocked"
  [ -z "$(status_key_closing_verb "$dir/edge.status" quiet-key)" ] \
    || fail "a keyed working line should report no closing verb"
  [ "$(status_key_closing_verb "$dir/edge.status" pending-reply-abc)" = resolved ] \
    || fail "pending-reply-abc should read resolved after its vocabular close"
  [ -z "$(status_key_closing_verb "$dir/edge.status" corr-key)" ] \
    || fail "corr-prefixed line should not open under corr-key"
  [ -z "$(status_decision_replacements "$dir/edge.status")" ] \
    || fail "edge log should announce no replacements"
  expected=$(printf 'dep\t6\ntag-key\t17')
  [ "$(_fm_status_open_decision_origins "$dir/edge.status")" = "$expected" ] \
    || fail "edge origins mismatch: got '$(_fm_status_open_decision_origins "$dir/edge.status")'"
}

test_custom_verbs_stay_exact() {
  local dir expected
  dir="$TMP_ROOT/custom"
  mkdir -p "$dir"
  cat > "$dir/custom.status" <<'EOF'
working: routine line
needs-decision [key=a]: custom open
settled [key=a]: custom resolve closes a
needs-decision [key=b]: still open
handed-off [key=c]: custom held closes c
resolved: the default word is ordinary prose under custom verbs
EOF
  expected=$(printf 'b\tneeds-decision\tstill open')
  [ "$(FM_CLASSIFY_RESOLVE_VERB=settled FM_CLASSIFY_CAPTAIN_HELD_VERB=handed-off \
    status_open_decisions "$dir/custom.status")" = "$expected" ] \
    || fail "custom-verb open-set mismatch"
  [ "$(FM_CLASSIFY_RESOLVE_VERB=settled FM_CLASSIFY_CAPTAIN_HELD_VERB=handed-off \
    status_key_closing_verb "$dir/custom.status" a)" = settled ] \
    || fail "custom resolve verb should close key a"
}

test_large_log_stays_fast_and_exact() {
  local dir log i start now elapsed full incr repl closing
  dir="$TMP_ROOT/large"
  mkdir -p "$dir"
  log="$dir/vep.status"
  {
    printf 'working: starting video render\n'
    printf 'needs-decision [key=render-mode]: pick fast or final\n'
    i=0
    while [ "$i" -lt 20000 ]; do
      printf 'working: processed chunk %d of render queue\n' "$i"
      i=$((i + 1))
    done
    printf 'blocked [key=vendor]: waiting on caption file\n'
  } > "$log"
  start=$(date +%s)
  full=$(status_open_decisions "$log")
  incr=$(status_open_decisions_incremental "$log")
  repl=$(status_decision_replacements "$log")
  closing=$(status_key_closing_verb "$log" default)
  [ "$incr" = "$full" ] || fail "large-log incremental fold diverged"
  case "$full" in
    *'render-mode	needs-decision	pick fast or final'*'vendor	blocked	waiting on caption file'*) ;;
    *) fail "large log lost its buried decisions: '$full'" ;;
  esac
  [ -z "$repl" ] || fail "large log should announce no replacements, got '$repl'"
  [ -z "$closing" ] || fail "default key should have no closing verb, got '$closing'"
  now=$(date +%s)
  elapsed=$((now - start))
  [ "$elapsed" -lt 25 ] || fail "four large-log folds took ${elapsed}s, want under 25s"
}

test_edge_corpus_folds_exactly
test_custom_verbs_stay_exact
test_large_log_stays_fast_and_exact
pass "decision fold scale"
