#!/usr/bin/env bash
# Behavior tests for bin/fm-brief.sh.
#
# Regression coverage for the heredoc-in-command-substitution parse bug (issues
# #166, #958, #1069). Building a variable with `VAR=$(cat <<EOF ... EOF)` is
# unsafe on Bash 3.2 (macOS /bin/bash): the lexer scans for the matching `)` of
# the command substitution textually and tracks quote state through the heredoc
# body, so a single apostrophe, unbalanced quote, or unbalanced paren anywhere
# in that body breaks parsing of the *entire rest of the script* - `bash -n`
# fails, not just the generated brief. The DOD and Herdr-section builders now
# use `IFS= read -r -d '' VAR <<EOF || true` instead, which removes the `$(...)`
# wrapper and eliminates the whole defect class regardless of future prose.
# test_no_heredoc_in_command_substitution guards that structure directly.
# Ambient `bash -n` here is Bash 5 and cannot see the bug, so the real
# cross-version enforcement lives in the macos-stock-bash CI job.
set -u

# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

TMP_ROOT=$(fm_test_tmproot fm-brief)
BRIEF_HOME="$TMP_ROOT/home"
mkdir -p "$BRIEF_HOME/data"

# The script itself must always parse under the ambient bash. That is Bash 5 in
# CI and locally, where the issue #958/#1069 parser bug does not fire, so this
# is a weak guard on its own; test_no_heredoc_in_command_substitution and the
# macos-stock-bash CI job carry the real cross-version enforcement.
test_script_parses() {
  local out rc
  out=$(bash -n "$ROOT/bin/fm-brief.sh" 2>&1); rc=$?
  expect_code 0 "$rc" "bash -n bin/fm-brief.sh must parse cleanly (got: $out)"
  [ -z "$out" ] || fail "bash -n bin/fm-brief.sh emitted unexpected output: $out"
  pass "fm-brief.sh: bash -n succeeds"
}

# Structural class guard (issues #166, #958, #1069): never build a variable by
# wrapping a heredoc in a command substitution (`VAR=$(cat <<EOF ... EOF)`).
# That construct is what breaks Bash 3.2 parsing, and pinning one historical
# apostrophe phrase (as the old test did) missed the #945 reintroduction. This
# guards the *shape* directly against the whole file, so any future DOD or
# section builder that reintroduces the class fails here regardless of prose.
test_no_heredoc_in_command_substitution() {
  local unsafe safe
  unsafe="$TMP_ROOT/heredoc-in-substitution.sh"
  safe="$TMP_ROOT/plain-heredoc.sh"
  # shellcheck disable=SC2016 # Literal shell fixtures must remain unexpanded.
  printf '%s\n' 'value=$(' '  cat <<EOF' 'body' 'EOF' ')' > "$unsafe"
  # shellcheck disable=SC2016 # Literal shell fixtures must remain unexpanded.
  printf '%s\n' 'cat <<EOF' '$(' '  cat <<INNER' 'INNER' ')' 'EOF' > "$safe"
  if no_heredoc_in_command_substitution "$unsafe"; then
    fail "structural guard accepted a multiline heredoc nested in a command substitution"
  fi
  no_heredoc_in_command_substitution "$safe" \
    || fail "structural guard treated heredoc body prose as shell structure"
  no_heredoc_in_command_substitution "$ROOT/bin/fm-brief.sh" \
    || fail "fm-brief.sh wraps a heredoc in a command substitution (breaks Bash 3.2 parsing)"
  pass "fm-brief.sh: no heredoc is nested inside a command substitution (Bash 3.2 parse-safe)"
}

no_heredoc_in_command_substitution() {
  perl - "$1" <<'PERL'
use strict;
use warnings;

my $path = shift;
open my $source, '<', $path or die "$path: $!\n";
my @frames;
my @heredocs;
my $quote = '';
my $line_number = 0;

while (my $line = <$source>) {
  $line_number++;
  if (@heredocs) {
    my $candidate = $line;
    $candidate =~ s/\r?\n\z//;
    $candidate =~ s/^\t+// if $heredocs[0]{strip_tabs};
    shift @heredocs if $candidate eq $heredocs[0]{delimiter};
    next;
  }

  my $length = length $line;
  for (my $i = 0; $i < $length; $i++) {
    my $char = substr($line, $i, 1);
    if ($quote eq "'") {
      $quote = '' if $char eq "'";
      next;
    }
    if ($char eq '\\') {
      $i++;
      next;
    }
    if ($quote eq '"' && $char eq '"') {
      $quote = '';
      next;
    }
    if ($char eq "'" && $quote eq '') {
      $quote = "'";
      next;
    }
    if ($char eq '"' && $quote eq '') {
      $quote = '"';
      next;
    }
    if ($char eq '#' && $quote eq '' && ($i == 0 || substr($line, $i - 1, 1) =~ /[\s;|&()]/)) {
      last;
    }
    if ($char eq '$' && substr($line, $i + 1, 1) eq '(') {
      push @frames, { depth => 1, quote => $quote };
      $quote = '';
      $i++;
      next;
    }
    if (@frames && $quote eq '' && $char eq '(') {
      $frames[-1]{depth}++;
      next;
    }
    if (@frames && $quote eq '' && $char eq ')') {
      $frames[-1]{depth}--;
      if ($frames[-1]{depth} == 0) {
        my $frame = pop @frames;
        $quote = $frame->{quote};
      }
      next;
    }
    next unless $quote eq '' && $char eq '<' && substr($line, $i + 1, 1) eq '<';
    if (@frames) {
      print STDERR "$path:$line_number\n";
      exit 1;
    }

    my $j = $i + 2;
    my $strip_tabs = substr($line, $j, 1) eq '-';
    $j++ if $strip_tabs;
    $j++ while substr($line, $j, 1) =~ /[ \t]/;
    my $delimiter = '';
    my $delimiter_quote = '';
    for (; $j < $length; $j++) {
      my $token = substr($line, $j, 1);
      if ($delimiter_quote) {
        if ($token eq $delimiter_quote) {
          $delimiter_quote = '';
        } elsif ($token eq '\\' && $delimiter_quote eq '"') {
          $j++;
          $delimiter .= substr($line, $j, 1);
        } else {
          $delimiter .= $token;
        }
        next;
      }
      if ($token eq "'" || $token eq '"') {
        $delimiter_quote = $token;
        next;
      }
      if ($token eq '\\') {
        $j++;
        $delimiter .= substr($line, $j, 1);
        next;
      }
      last if $token =~ /[\s;|&()<>]/;
      $delimiter .= $token;
    }
    push @heredocs, { delimiter => $delimiter, strip_tabs => $strip_tabs };
    $i = $j - 1;
  }
}

exit 0;
PERL
}

test_help_includes_entire_header() {
  local help
  help=$("$ROOT/bin/fm-brief.sh" --help)
  assert_contains "$help" "Refuses to overwrite an existing brief." "fm-brief.sh --help omitted its header terminator"
  pass "fm-brief.sh: --help renders the complete header"
}

# Registry with one project per delivery mode. fm-brief.sh no longer reads it -
# the ship mode arrives as an explicit flag - so this fixture exists to prove the
# scaffold ignores the registered posture (test_ship_mode_is_explicit_not_registry).
write_registry() {
  local home=$1
  mkdir -p "$home/data"
  cat > "$home/data/projects.md" <<'EOF'
- direct-proj [direct-PR] - fixture for direct-PR mode (added 2026-07-01)
- local-proj [local-only] - fixture for local-only mode (added 2026-07-01)
EOF
}

# fm-brief.sh must exit 0 and produce a brief with no unreplaced shell
# metacharacter corruption for every ship delivery mode. This also guards
# against any *new* unescaped apostrophe or unbalanced quote later added to
# one of these DOD blocks, since a broken heredoc corrupts or empties the
# generated brief content, not just the script's own syntax.
test_ship_modes_generate_clean_briefs() {
  local home id mode brief status
  home="$TMP_ROOT/ship-home"
  write_registry "$home"

  for id_mode in "brief-nomistakes-a1:no-mistakes" "brief-directpr-a2:direct-PR" "brief-localonly-a3:local-only"; do
    id=${id_mode%%:*}
    mode=${id_mode##*:}
    FM_HOME="$home" "$ROOT/bin/fm-brief.sh" "$id" some-proj --mode "$mode" >/dev/null 2>&1; status=$?
    expect_code 0 "$status" "fm-brief.sh $id --mode $mode should exit 0"
    brief="$home/data/$id/brief.md"
    assert_present "$brief" "$id: brief was not scaffolded"
    assert_grep "# Definition of done" "$brief" "$id: brief missing Definition of done section"
    grep -qx "Delivery contract: mode=$mode" "$brief" \
      || fail "$id: brief did not record its machine-readable delivery contract line"
    assert_grep "{TASK}" "$brief" "$id: brief missing the {TASK} placeholder"
    assert_grep "{FIRSTMATE_SPEC}" "$brief" "$id: brief missing the {FIRSTMATE_SPEC} placeholder"
    assert_grep "## Captain's intent" "$brief" "$id: brief missing Captain's intent subsection"
    assert_grep "## Firstmate spec" "$brief" "$id: brief missing Firstmate spec subsection"
    assert_grep 'never a bare number such as "PR 108"' "$brief" "$id: brief missing the full-PR-URL rule"
    assert_grep "mid-task \`working:\` line (including setup complete) is nonterminal" "$brief" \
      "$id: brief missing nonterminal working:/setup-complete gate protection"
    assert_no_grep "EOF" "$brief" "$id: brief leaked a heredoc EOF marker (unterminated heredoc)"
  done
  pass "fm-brief.sh: no-mistakes/direct-PR/local-only briefs generate cleanly"
}

# A ship task's delivery mode is firstmate's per-task decision, so a missing or
# unusable value must stop the scaffold instead of silently defaulting. The
# no-mistakes-prod-only row is the conditional registry policy: it is never a task
# mode, and its refusal must say to classify the task's surface first.
test_ship_mode_is_required_and_closed_set() {
  local home id out status label flag expect
  home="$TMP_ROOT/mode-required-home"
  mkdir -p "$home/data"
  id=0
  while IFS='|' read -r label flag expect; do
    [ -n "$label" ] || continue
    id=$((id + 1))
    # shellcheck disable=SC2086  # flag is an intentional word-split arg list (may be empty)
    out=$(FM_HOME="$home" "$ROOT/bin/fm-brief.sh" "brief-required-$id" some-proj $flag 2>&1)
    status=$?
    [ "$status" -ne 0 ] || fail "$label: expected a non-zero exit"
    assert_contains "$out" "$expect" "$label: refusal did not explain the contract"
    assert_absent "$home/data/brief-required-$id/brief.md" "$label: refused scaffold still wrote a brief"
  done <<'ROWS'
missing --mode||ship briefs require --mode
empty --mode value|--mode|requires a value
unknown mode value|--mode nope|must be one of no-mistakes, direct-PR, local-only
conditional policy is not a task mode|--mode no-mistakes-prod-only|classify this task's surface
ROWS
  pass "fm-brief.sh: ship --mode is required and closed-set validated"
}

# The registry is the captain's standing posture, not this task's answer: the
# scaffold must follow the explicit flag even when the project is registered
# with a different mode, and must not consult the registry at all.
test_ship_mode_is_explicit_not_registry() {
  local home brief
  home="$TMP_ROOT/explicit-over-registry-home"
  write_registry "$home"
  FM_HOME="$home" "$ROOT/bin/fm-brief.sh" brief-explicit-a5 direct-proj --mode no-mistakes >/dev/null 2>&1 \
    || fail "explicit no-mistakes brief on a direct-PR project should scaffold"
  brief="$home/data/brief-explicit-a5/brief.md"
  grep -qx "Delivery contract: mode=no-mistakes" "$brief" \
    || fail "registered direct-PR posture overrode the explicit --mode"
  assert_grep "Firstmate will then instruct you to run /no-mistakes" "$brief" \
    "explicit no-mistakes brief did not render the pipeline definition of done"

  # An unregistered project is not a blocker either, because nothing is looked up.
  FM_HOME="$home" "$ROOT/bin/fm-brief.sh" brief-explicit-a6 never-registered --mode local-only >/dev/null 2>&1 \
    || fail "unregistered project should still scaffold from the explicit mode"
  grep -qx "Delivery contract: mode=local-only" "$home/data/brief-explicit-a6/brief.md" \
    || fail "unregistered project did not honour the explicit --mode"
  pass "fm-brief.sh: the explicit ship mode wins over the registered posture"
}

# yolo is firstmate's merge authority and never reaches the worker, and a scout
# or charter carries no delivery contract. Each must refuse rather than accept and
# discard the flag, which would look recorded but change nothing.
test_delivery_flags_are_refused_where_they_do_not_apply() {
  local home out status label args expect
  home="$TMP_ROOT/refused-flags-home"
  mkdir -p "$home/data"
  while IFS='|' read -r label args expect; do
    [ -n "$label" ] || continue
    # shellcheck disable=SC2086  # args is an intentional word-split arg list
    out=$(FM_HOME="$home" "$ROOT/bin/fm-brief.sh" $args 2>&1)
    status=$?
    [ "$status" -ne 0 ] || fail "$label: expected a non-zero exit"
    assert_contains "$out" "$expect" "$label: refusal did not explain why"
  done <<'ROWS'
yolo on a ship brief|brief-refused-b1 some-proj --mode direct-PR --yolo on|--yolo is not a brief input
yolo=value form on a ship brief|brief-refused-b2 some-proj --mode direct-PR --yolo=off|--yolo is not a brief input
mode on a scout brief|brief-refused-b3 some-proj --scout --mode direct-PR|--mode applies only to ship briefs
mode on a secondmate charter|brief-refused-b4 --secondmate --no-projects --mode no-mistakes|--mode applies only to ship briefs
ROWS
  pass "fm-brief.sh: --yolo and scout/secondmate --mode are refused, never silently dropped"
}

test_faster_paths_use_configured_authority_without_stacked_review() {
  local home id brief
  home="$TMP_ROOT/configured-authority-home"
  write_registry "$home"
  id="brief-direct-authority-a4"
  FM_HOME="$home" "$ROOT/bin/fm-brief.sh" "$id" direct-proj --mode direct-PR >/dev/null 2>&1
  brief="$home/data/$id/brief.md"
  assert_grep "The configured merge authority decides whether to merge the PR; firstmate relays the outcome." "$brief" \
    "direct-PR brief lost configured merge authority"
  assert_no_grep "The captain reviews and merges the PR" "$brief" \
    "direct-PR brief hard-coded captain-only authority"
  id="brief-local-authority-a4"
  FM_HOME="$home" "$ROOT/bin/fm-brief.sh" "$id" local-proj --mode local-only >/dev/null 2>&1
  brief="$home/data/$id/brief.md"
  assert_grep "The configured merge authority approves the ready branch, then firstmate merges it into local \`main\` through the guarded fast-forward path." "$brief" \
    "local-only brief lost configured merge authority and guarded landing"
  assert_no_grep "The captain approves the ready branch" "$brief" \
    "local-only brief hard-coded captain-only authority"
  assert_no_grep "Firstmate then reviews your branch diff" "$brief" \
    "local-only brief retained a personal review stacked on the selected delivery path"
  assert_no_grep "pass \`--intent\` as only this brief's \`## Captain's intent\`" "$home/data/$id/brief.md" \
    "local-only brief must not include the no-mistakes --intent contract"
  id="brief-direct-intent-a4"
  FM_HOME="$home" "$ROOT/bin/fm-brief.sh" "$id" direct-proj --mode direct-PR >/dev/null 2>&1
  assert_no_grep "pass \`--intent\` as only this brief's \`## Captain's intent\`" "$home/data/$id/brief.md" \
    "direct-PR brief must not include the no-mistakes --intent contract"
  pass "fm-brief.sh: faster paths use configured authority without stacked review"
}

# Pin the specific line the bug lived on: the no-mistakes DOD's no-mistakes
# reference must render as plain prose with no dangling apostrophe artifact.
test_no_mistakes_dod_wording() {
  local home id brief
  home="$TMP_ROOT/wording-home"
  mkdir -p "$home/data"
  id="brief-wording-b1"
  FM_HOME="$home" "$ROOT/bin/fm-brief.sh" "$id" some-proj --mode no-mistakes >/dev/null 2>&1
  brief="$home/data/$id/brief.md"
  assert_present "$brief" "brief was not scaffolded"
  assert_grep "no-mistakes itself provides for the mechanics" "$brief" \
    "no-mistakes DOD lost its guidance-reference sentence"
  # shellcheck disable=SC2016  # single quotes are deliberate: the backticks must stay literal
  assert_grep '`no-mistakes axi run --help`' "$brief" \
    "no-mistakes DOD must render literal backticks around the help command"
  # shellcheck disable=SC2016  # single quotes are deliberate: the backticks must stay literal
  assert_grep '`help`' "$brief" \
    "no-mistakes DOD must render literal backticks around help"
  assert_grep "pass \`--intent\` as only this brief's \`## Captain's intent\`" "$brief" \
    "no-mistakes DOD must require --intent to be the Captain's intent subsection"
  assert_grep "plus any later words the captain actually said" "$brief" \
    "no-mistakes DOD must allow later captain words in --intent"
  assert_grep "Do not include \`## Firstmate spec\`" "$brief" \
    "no-mistakes DOD must keep Firstmate spec out of --intent"
  assert_grep "or your own decisions and tradeoffs" "$brief" \
    "no-mistakes DOD must keep worker tradeoffs out of --intent"
  assert_grep "This replaces the no-mistakes skill's advice to enrich \`--intent\`" "$brief" \
    "no-mistakes DOD must override the external skill's enrich-with-decisions guidance"
  # A bare reference cannot preserve the captain's ask, so the rendered DOD states
  # the self-sufficiency rule and requires referenced material to be resolved into
  # its substance.
  assert_grep "The \`--intent\` string you pass must be self-sufficient" "$brief" \
    "no-mistakes DOD must require a self-sufficient --intent string"
  assert_grep "write the substance of the referenced items into \`--intent\`" "$brief" \
    "no-mistakes DOD must tell the worker to resolve report, decision, and PR references into substance"

  # The --yes ban is a fleet-wide prohibition, not a preference, and it must not
  # claim an enforcement the tool does not provide: this is instruction only.
  assert_grep "NEVER pass \`--yes\` (or \`-y\`) to \`no-mistakes axi run\` or \`no-mistakes axi respond\`. It is banned fleet-wide." "$brief" \
    "no-mistakes DOD must state the --yes ban as a prohibition"
  assert_grep "answering your own ask-user finding is a hard rule violation" "$brief" \
    "no-mistakes DOD must say why --yes is banned"
  assert_no_grep "Avoid \`--yes\`" "$brief" \
    "no-mistakes DOD still states the --yes ban as a preference"
  assert_no_grep "no-mistakes refuses" "$brief" \
    "no-mistakes DOD must not claim the tool itself refuses --yes"
  pass "fm-brief.sh: no-mistakes DOD keeps its apostrophe prose and bans --yes outright"
}

# The captain's standing never-upstream ruling reaches the worker from the
# scaffold itself: both PR-creating delivery contracts carry the fork target
# automatically, so no brief author has to remember it. Modes that never open a
# PR carry no PR-target prose.
test_ship_pr_modes_carry_never_upstream_rule() {
  local home id brief
  home="$TMP_ROOT/fork-rule-home"
  mkdir -p "$home/data"
  id="brief-fork-direct-b2"
  FM_HOME="$home" "$ROOT/bin/fm-brief.sh" "$id" some-proj --mode direct-PR >/dev/null 2>&1
  brief="$home/data/$id/brief.md"
  assert_present "$brief" "direct-PR brief was not scaffolded"
  # shellcheck disable=SC2016  # single quotes are deliberate: the backticks must stay literal
  assert_grep 'pass `-R prajwal-395/firstmate` on every PR command' "$brief" \
    "direct-PR brief lost the fork target"
  assert_grep 'kunchenguid/firstmate' "$brief" \
    "direct-PR brief must name the refused upstream repo"
  assert_grep 'standing never-upstream ruling' "$brief" \
    "direct-PR brief must name the standing ruling behind the refusal"
  assert_grep 'no brief prose overrides that refusal' "$brief" \
    "direct-PR brief must say the refusal is not overridable by prose"
  id="brief-fork-nm-b2"
  FM_HOME="$home" "$ROOT/bin/fm-brief.sh" "$id" some-proj --mode no-mistakes >/dev/null 2>&1
  brief="$home/data/$id/brief.md"
  assert_present "$brief" "no-mistakes brief was not scaffolded"
  # shellcheck disable=SC2016  # single quotes are deliberate: the backticks must stay literal
  assert_grep 'must target the fork `prajwal-395/firstmate`, never upstream `kunchenguid/firstmate`' "$brief" \
    "no-mistakes brief lost the fork target"
  assert_grep 'standing never-upstream ruling' "$brief" \
    "no-mistakes brief must name the standing ruling behind the refusal"
  assert_grep 'blocked: PR targets upstream, not the fork' "$brief" \
    "no-mistakes brief must tell the worker to stop instead of reporting an upstream PR done"
  assert_grep 'no brief prose overrides that refusal' "$brief" \
    "no-mistakes brief must say the refusal is not overridable by prose"
  id="brief-fork-local-b2"
  FM_HOME="$home" "$ROOT/bin/fm-brief.sh" "$id" some-proj --mode local-only >/dev/null 2>&1
  assert_no_grep 'prajwal-395/firstmate' "$home/data/$id/brief.md" \
    "local-only brief must carry no PR target: it never opens a PR"
  assert_no_grep 'never-upstream' "$home/data/$id/brief.md" \
    "local-only brief must carry no upstream-refusal prose: it never opens a PR"
  id="brief-fork-scout-b2"
  FM_HOME="$home" "$ROOT/bin/fm-brief.sh" "$id" some-proj --scout >/dev/null 2>&1
  assert_no_grep 'never-upstream' "$home/data/$id/brief.md" \
    "scout brief must carry no upstream-refusal prose: it never opens a PR"
  pass "fm-brief.sh: PR-creating ship briefs carry the never-upstream fork rule from the scaffold"
}

test_ask_user_escalation_format() {
  local home id brief mode other_id other_brief
  home="$TMP_ROOT/ask-user-home"
  mkdir -p "$home/data"
  id="brief-ask-user-d1"
  FM_HOME="$home" "$ROOT/bin/fm-brief.sh" "$id" some-proj --mode no-mistakes >/dev/null 2>&1
  brief="$home/data/$id/brief.md"
  assert_present "$brief" "brief was not scaffolded"

  # A no-mistakes ask-user gate must escalate its ask-user findings as one status
  # event plus one verbatim findings snapshot file, using that same shape even
  # for a single finding, never paraphrased into the status line.
  assert_grep "escalate all ask-user findings as one event plus one snapshot file" "$brief" \
    "ship rule 6 lost the one-event-plus-snapshot-file ask-user contract"
  assert_grep "using that same shape even when the gate holds only a single ask-user finding" "$brief" \
    "ship rule 6 must require the same shape for a single finding"
  assert_grep "write only the ask-user findings, verbatim and unparaphrased (id, severity, file, line, description, authority)" "$brief" \
    "ship rule 6 must limit the verbatim axi slice to ask-user findings"
  # shellcheck disable=SC2016  # single quotes are deliberate: backticks and the key/findings/file tokens must stay literal
  assert_grep 'needs-decision [key=nm-<run>-<step>]: ask-user findings=<id1>,<id2>,... file='"$home/data/$id/nm-<run>-findings.txt" "$brief" \
    "ship rule 6 must render the exact needs-decision ask-user status line"
  assert_grep "$home/data/$id/nm-<run>-findings.txt" "$brief" \
    "ship rule 6 must point the snapshot file under this task's own data directory"
  assert_grep "The status line only points at the file; it never restates or summarizes a finding's content." "$brief" \
    "ship rule 6 must forbid paraphrasing ask-user findings into the status line"

  # The DOD's own ask-user paragraph must point back at rule 6's format
  # (one-owner rule) rather than restating or bare-citing it.
  assert_grep "escalate to firstmate using rule 6's ask-user format" "$brief" \
    "no-mistakes DOD ask-user paragraph must point at rule 6's format instead of a bare citation"
  assert_no_grep "escalate to firstmate (rule 6) and stop." "$brief" \
    "no-mistakes DOD ask-user paragraph still uses the old bare rule-6 pointer"

  other_id="brief-no-ask-user-scout"
  FM_HOME="$home" "$ROOT/bin/fm-brief.sh" "$other_id" some-proj --scout >/dev/null 2>&1
  other_brief="$home/data/$other_id/brief.md"
  assert_no_grep "destructive actions, ask-user findings" "$other_brief" \
    "scout brief received a no-mistakes-only decision case"

  for mode in direct-PR local-only; do
    other_id="brief-no-ask-user-$(printf '%s' "$mode" | tr '[:upper:]' '[:lower:]')"
    FM_HOME="$home" "$ROOT/bin/fm-brief.sh" "$other_id" some-proj --mode "$mode" >/dev/null 2>&1
    other_brief="$home/data/$other_id/brief.md"
    assert_no_grep "nm-<run>-findings.txt" "$other_brief" \
      "$mode brief received a no-mistakes-only escalation format"
    assert_no_grep "destructive actions, ask-user findings" "$other_brief" \
      "$mode brief received a no-mistakes-only decision case"
  done

  pass "fm-brief.sh: no-mistakes ask-user findings use one event plus a verbatim snapshot"
}

test_ship_project_memory_wording() {
  local home id brief
  home="$TMP_ROOT/project-memory-home"
  mkdir -p "$home/data"
  id="brief-memory-c1"
  FM_HOME="$home" "$ROOT/bin/fm-brief.sh" "$id" some-proj --mode no-mistakes >/dev/null 2>&1
  brief="$home/data/$id/brief.md"
  assert_present "$brief" "brief was not scaffolded"
  assert_grep "Record only project knowledge useful to almost every future session." "$brief" \
    "project-memory contract lost the durable-knowledge bar"
  assert_grep "prefer a pointer to the authoritative file, command, or doc over copying the detail" "$brief" \
    "project-memory contract lost pointer-over-copy guidance"
  assert_grep "follow \`$ROOT/bin/fm-ensure-agents-md.sh\`'s self-governance contract" "$brief" \
    "project-memory contract no longer defers to the ensure helper"
  pass "fm-brief.sh: ship project-memory wording carries the AGENTS.md authoring bar"
}

test_herdr_lab_contract_is_explicit_and_complete() {
  local home id brief
  home="$TMP_ROOT/herdr-lab-home"
  mkdir -p "$home/data"
  id="brief-herdr-lab-d1"
  FM_HOME="$home" "$ROOT/bin/fm-brief.sh" "$id" firstmate --mode no-mistakes --herdr-lab >/dev/null 2>&1
  brief="$home/data/$id/brief.md"
  assert_present "$brief" "Herdr lab brief was not scaffolded"
  assert_grep "# Herdr isolation - HARD SAFETY CONTRACT" "$brief" \
    "Herdr lab brief missing its hard safety contract"
  assert_grep "HERDR_LAB_HELPER='$ROOT/bin/fm-herdr-lab.sh'" "$brief" \
    "Herdr lab brief must bind the absolute Firstmate helper path"
  assert_grep "HERDR_LAB_SESSION=\$(\"\$HERDR_LAB_HELPER\" name $id)" "$brief" \
    "Herdr lab brief missing helper-owned session naming"
  assert_grep "\"\$HERDR_LAB_HELPER\" provision \"\$HERDR_LAB_SESSION\"" "$brief" \
    "Herdr lab brief missing helper-owned provisioning"
  assert_grep "\"\$HERDR_LAB_HELPER\" teardown \"\$HERDR_LAB_SESSION\"" "$brief" \
    "Herdr lab brief missing helper-owned teardown"
  assert_grep "required trailing \`--session \"\$HERDR_LAB_SESSION\"\`" "$brief" \
    "Herdr lab brief missing the per-call trailing session contract"
  assert_grep "direct \`herdr server stop\`" "$brief" \
    "Herdr lab brief missing the forbidden server-global command list"
  assert_grep "records the live default session before provisioning" "$brief" \
    "Herdr lab brief missing the before tripwire"
  assert_grep "verifies the identical fleet state after teardown" "$brief" \
    "Herdr lab brief missing the after tripwire"
  assert_no_grep "Herdr lifecycle declaration - NOT ENABLED" "$brief" \
    "Herdr lab brief retained the unguarded declaration"
  pass "fm-brief.sh: --herdr-lab emits the complete hard safety contract"
}

test_herdr_lab_contract_quotes_foreign_firstmate_path() {
  local home id brief foreign_root helper
  home="$TMP_ROOT/herdr-lab-foreign-home"
  foreign_root="$TMP_ROOT/firstmate helper's root"
  mkdir -p "$home/data"
  id="brief-herdr-lab-foreign-d2"
  helper=$(printf '%s' "$foreign_root/bin/fm-herdr-lab.sh" | sed "s/'/'\\\\''/g")
  helper="'$helper'"
  FM_HOME="$home" FM_ROOT_OVERRIDE="$foreign_root" "$ROOT/bin/fm-brief.sh" "$id" foreign --scout --herdr-lab >/dev/null 2>&1
  brief="$home/data/$id/brief.md"
  assert_grep "HERDR_LAB_HELPER=$helper" "$brief" \
    "Herdr lab brief must shell-quote an absolute Firstmate helper path"
  assert_no_grep "bin/fm-herdr-lab.sh name $id" "$brief" \
    "Herdr lab brief must not invoke a worktree-relative helper"
  pass "fm-brief.sh: --herdr-lab uses its quoted Firstmate-owned helper path"
}

test_herdr_lab_omission_is_loud_for_ship_and_scout() {
  local home id brief
  home="$TMP_ROOT/herdr-gate-home"
  mkdir -p "$home/data"
  for kind in ship scout; do
    id="brief-herdr-gate-$kind"
    if [ "$kind" = scout ]; then
      FM_HOME="$home" "$ROOT/bin/fm-brief.sh" "$id" firstmate --scout >/dev/null 2>&1
    else
      FM_HOME="$home" "$ROOT/bin/fm-brief.sh" "$id" firstmate --mode no-mistakes >/dev/null 2>&1
    fi
    brief="$home/data/$id/brief.md"
    assert_grep "# Herdr lifecycle declaration - NOT ENABLED" "$brief" \
      "$kind brief silently omitted the Herdr declaration"
    assert_grep "regenerate the brief with \`--herdr-lab\` before dispatch" "$brief" \
      "$kind brief missing the fail-visible regeneration instruction"
  done
  pass "fm-brief.sh: ship and scout scaffolds make omitted Herdr intent fail-visible"
}

# Regression (issue #2575): AGENTS.md section 11 and this script's own help tell
# firstmate to fill `{TASK}` and `{FIRSTMATE_SPEC}`. The unguarded Herdr gate used
# to quote `{TASK}` in its own prose, so that documented global replace spliced
# the whole task body into the middle of the gate's sentence - silently
# destroying the one contract that exists precisely because the scaffold cannot
# see the task text. Each placeholder must exist only at its genuine fill site,
# so the documented fill leaves the gate intact and each body appears once.
test_documented_global_replace_leaves_the_herdr_gate_intact() {
  local home id brief kind count content filled body spec
  home="$TMP_ROOT/task-fill-site-home"
  mkdir -p "$home/data"
  body='Restart the herdr session, then profile it'
  spec='Use the isolated lab helper for every lifecycle call'
  for kind in ship scout; do
    id="brief-fill-site-$kind"
    if [ "$kind" = scout ]; then
      FM_HOME="$home" "$ROOT/bin/fm-brief.sh" "$id" firstmate --scout >/dev/null 2>&1
    else
      FM_HOME="$home" "$ROOT/bin/fm-brief.sh" "$id" firstmate --mode no-mistakes >/dev/null 2>&1
    fi
    brief="$home/data/$id/brief.md"
    assert_present "$brief" "$kind brief was not scaffolded"
    count=$(grep -c -F '{TASK}' "$brief")
    [ "$count" = 1 ] \
      || fail "$kind brief must carry exactly one {TASK} fill site, found $count"
    count=$(grep -c -F '{FIRSTMATE_SPEC}' "$brief")
    [ "$count" = 1 ] \
      || fail "$kind brief must carry exactly one {FIRSTMATE_SPEC} fill site, found $count"
    content=$(cat "$brief")
    filled=${content//'{TASK}'/$body}
    filled=${filled//'{FIRSTMATE_SPEC}'/$spec}
    count=$(printf '%s\n' "$filled" | grep -c -F "$body")
    [ "$count" = 1 ] \
      || fail "$kind brief: the documented {TASK} replace duplicated the intent body $count times"
    count=$(printf '%s\n' "$filled" | grep -c -F "$spec")
    [ "$count" = 1 ] \
      || fail "$kind brief: the {FIRSTMATE_SPEC} replace duplicated the spec body $count times"
    printf '%s\n' "$filled" | grep -qF 'this scaffold cannot inspect the task text' \
      || fail "$kind brief: the Herdr safety gate did not survive the documented fill"
  done
  pass "fm-brief.sh: the documented {TASK} and {FIRSTMATE_SPEC} fills cannot corrupt the Herdr safety gate"
}

test_secondmate_no_projects_charter() {
  local home brief status
  home="$TMP_ROOT/no-projects-home"
  mkdir -p "$home/data"

  # The deliberate --no-projects signal scaffolds a valid project-less charter for
  # a domain whose subject is the firstmate repo itself (no clones needed).
  FM_HOME="$home" FM_SECONDMATE_CHARTER='firstmate self-development' \
    FM_SECONDMATE_SCOPE='firstmate repo work' \
    "$ROOT/bin/fm-brief.sh" fdev --secondmate --no-projects >/dev/null 2>&1; status=$?
  expect_code 0 "$status" "--no-projects secondmate brief should exit 0"
  brief="$home/data/fdev/brief.md"
  assert_present "$brief" "project-less charter was not scaffolded"
  assert_grep "# Project clones" "$brief" "project-less charter dropped the Project clones heading"
  assert_grep "None. This is a project-less domain" "$brief" \
    "project-less charter did not render a sensible no-clones note"
  assert_grep "its crews take pooled worktrees of that repo" "$brief" \
    "project-less charter operating model lost the pooled-worktree note"
  assert_no_grep "The projects above are local clones" "$brief" \
    "project-less charter kept the with-projects operating-model line"
  assert_grep '# The captain and the parent channel' "$brief" \
    "secondmate charter lost the parent-channel section"
  assert_grep 'Nobody reads this chat' "$brief" \
    "secondmate charter no longer says the chat is unread"
  assert_grep 'in this home it IS the captain' "$brief" \
    "secondmate charter no longer names the parent channel as the captain"
  assert_grep 'working [key=<work-slug>]' "$brief" \
    "secondmate charter did not key material routed-work phases"
  assert_grep 'resolved [key=<work-slug>]' "$brief" \
    "secondmate charter did not close a quietly ended routed-work phase"
  assert_grep 'use the same key in the same position on its later' "$brief" \
    "secondmate charter did not supersede working phases with later states"
  if grep -nE '^-[[:space:]]*$' "$brief" >/dev/null; then
    fail "project-less charter left a stray empty project bullet"
  fi

  # Accidental omission (no projects, no signal) still fails loudly, writing nothing.
  FM_HOME="$home" FM_SECONDMATE_CHARTER='x' "$ROOT/bin/fm-brief.sh" oops --secondmate >/dev/null 2>&1; status=$?
  expect_code 1 "$status" "secondmate brief with no projects and no --no-projects must fail"
  assert_absent "$home/data/oops/brief.md" "loud-failure secondmate brief still wrote a file"

  # --no-projects is mutually exclusive with a project list.
  FM_HOME="$home" FM_SECONDMATE_CHARTER='x' "$ROOT/bin/fm-brief.sh" oops2 --secondmate --no-projects alpha >/dev/null 2>&1; status=$?
  expect_code 1 "$status" "--no-projects combined with a project list must fail"

  # --no-projects applies only to secondmate charters, never a ship/scout brief.
  FM_HOME="$home" "$ROOT/bin/fm-brief.sh" oops3 somerepo --no-projects >/dev/null 2>&1; status=$?
  expect_code 1 "$status" "--no-projects on a ship brief must fail"

  pass "fm-brief.sh: --no-projects scaffolds a project-less charter and guards misuse"
}

test_secondmate_marked_request_reporting_contract() {
  local home brief
  home="$TMP_ROOT/marked-request-reporting-home"
  mkdir -p "$home/data"
  FM_HOME="$home" FM_CLASSIFY_PAUSED_VERB=paused \
    FM_SECONDMATE_CHARTER='Handle routed domain work.' \
    "$ROOT/bin/fm-brief.sh" marked-request-reporting --secondmate --no-projects >/dev/null 2>&1
  brief="$home/data/marked-request-reporting/brief.md"

  assert_grep 'A marked request requires one correlated answer after the work' "$brief" \
    "secondmate charter did not require the correlated answer after the work"
  assert_grep 'does not require a separate receipt or start acknowledgement' "$brief" \
    "secondmate charter did not reject a separate receipt/start acknowledgement"
  assert_grep "Never append \`working:\` merely to acknowledge receipt or announce that a marked request has started." "$brief" \
    "secondmate charter did not forbid a generic working acknowledgement"
  assert_no_grep "Give every routed-work phase a stable key: open it with \`working" "$brief" \
    "secondmate charter retained the unconditional working opener"
  assert_grep 'When a routed-work phase has a supervisor-actionable material change worth reporting under the rule above' "$brief" \
    "secondmate charter did not limit keyed phases to reportable material changes"
  assert_grep "If its first reportable event is \`working [key=<work-slug>]: {material phase}\`" "$brief" \
    "secondmate charter lost keyed working syntax for a reportable material phase"
  assert_grep "use the same key in the same position on its later \`paused\`, \`done\`, \`failed\`, \`needs-decision\`, or \`blocked\` event" "$brief" \
    "secondmate charter lost same-key closure for a reportable material phase"
  assert_grep 'resolved [key=<work-slug>]' "$brief" \
    "secondmate charter lost resolved closure for a keyed material phase"

  assert_grep 'include that exact token in your parent status reply' "$brief" \
    "secondmate charter lost correlated parent results"
  assert_grep 'bin/fm-secondmate-report.sh <verb> <corr_id> <note>' "$brief" \
    "secondmate charter lost the mechanical helper invocation"
  assert_grep 'do not pass a status path' "$brief" \
    "secondmate charter still tells the mate to pass a hand path to the helper"
  assert_grep 'For a terse result, a status line is the whole answer.' "$brief" \
    "secondmate charter lost terse result reporting"
  assert_grep 'append a status line that points to that doc' "$brief" \
    "secondmate charter lost detailed document pointers"
  assert_grep 'Report only true captain-relevant outcomes or a declared external wait' "$brief" \
    "secondmate charter lost declared external waits"
  assert_grep 'a captain decision, a real blocker, a failure, work ready for review, or work you landed' "$brief" \
    "secondmate charter lost decisions, blockers, failures, ready outcomes, or landed work"
  # Under standing merge authority nothing is ever "ready for review", so the
  # landed merge is the trigger a charter without this line silently omits.
  assert_grep 'a merge you performed yourself under standing merge authority and one the captain merged on the forge' "$brief" \
    "secondmate charter did not name a landed merge as a reporting trigger"
  assert_grep 'States: working, needs-decision, blocked, paused, done, failed.' "$brief" \
    "secondmate charter changed the preserved status vocabulary"
  pass "fm-brief.sh: marked requests avoid generic acknowledgements and preserve material reporting"
}

test_secondmate_directory_paths_are_absolute_and_output_is_stable() {
  local root home data_override state_override brief baseline err status
  root="$TMP_ROOT/relative-directory-inputs"
  mkdir -p "$root"
  root=$(cd "$root" && pwd -P)
  home="$root/home"
  data_override="$root/data-override"
  state_override="$root/state-override"
  mkdir -p "$home/data" "$home/state" "$data_override" "$state_override" \
    "$root/cdpath/home/data" "$root/cdpath/home/state" \
    "$root/cdpath/data-override" "$root/cdpath/state-override"

  brief="$home/data/relative-home/brief.md"
  FM_HOME="$home" FM_SECONDMATE_CHARTER=x \
    "$ROOT/bin/fm-brief.sh" relative-home --secondmate --no-projects >/dev/null 2>&1
  baseline="$root/absolute-home-charter"
  cp "$brief" "$baseline"
  rm -f "$brief"
  (
    cd "$root" || exit 1
    CDPATH="$root/cdpath" FM_HOME=home FM_SECONDMATE_CHARTER=x \
      "$ROOT/bin/fm-brief.sh" relative-home --secondmate --no-projects >/dev/null 2>&1
  )
  cmp -s "$baseline" "$brief" \
    || fail "relative FM_HOME changed charter bytes compared with the same absolute home"
  assert_grep ">> '$home/state/relative-home.status'" "$brief" \
    "relative FM_HOME did not render an absolute secondmate status path"

  brief="$home/data/relative-state/brief.md"
  FM_HOME="$home" FM_STATE_OVERRIDE="$state_override" FM_SECONDMATE_CHARTER=x \
    "$ROOT/bin/fm-brief.sh" relative-state --secondmate --no-projects >/dev/null 2>&1
  baseline="$root/absolute-state-charter"
  cp "$brief" "$baseline"
  rm -f "$brief"
  (
    cd "$root" || exit 1
    CDPATH="$root/cdpath" FM_HOME="$home" FM_STATE_OVERRIDE=state-override FM_SECONDMATE_CHARTER=x \
      "$ROOT/bin/fm-brief.sh" relative-state --secondmate --no-projects >/dev/null 2>&1
  )
  cmp -s "$baseline" "$brief" \
    || fail "relative FM_STATE_OVERRIDE changed charter bytes compared with the same absolute state directory"
  assert_grep ">> '$state_override/relative-state.status'" "$brief" \
    "relative FM_STATE_OVERRIDE did not render an absolute secondmate status path"

  brief="$data_override/relative-data/brief.md"
  FM_HOME="$home" FM_DATA_OVERRIDE="$data_override" FM_SECONDMATE_CHARTER=x \
    "$ROOT/bin/fm-brief.sh" relative-data --secondmate --no-projects >/dev/null 2>&1
  baseline="$root/absolute-data-charter"
  cp "$brief" "$baseline"
  rm -f "$brief"
  (
    cd "$root" || exit 1
    CDPATH="$root/cdpath" FM_HOME="$home" FM_DATA_OVERRIDE=data-override FM_SECONDMATE_CHARTER=x \
      "$ROOT/bin/fm-brief.sh" relative-data --secondmate --no-projects >/dev/null 2>&1
  )
  cmp -s "$baseline" "$brief" \
    || fail "relative FM_DATA_OVERRIDE changed charter bytes compared with the same absolute data directory"
  assert_grep ">> '$home/state/relative-data.status'" "$brief" \
    "relative FM_DATA_OVERRIDE changed the absolute default status path"

  err="$root/unresolved.err"
  (
    cd "$root" || exit 1
    FM_HOME=missing-home FM_SECONDMATE_CHARTER=x \
      "$ROOT/bin/fm-brief.sh" unresolved-home --secondmate --no-projects >/dev/null 2>"$err"
  ); status=$?
  expect_code 1 "$status" "an unresolved relative FM_HOME must fail"
  assert_grep "FM_HOME directory cannot be resolved: missing-home" "$err" \
    "unresolved relative FM_HOME did not fail loudly"

  (
    cd "$root" || exit 1
    FM_HOME="$home" FM_STATE_OVERRIDE=missing-state FM_SECONDMATE_CHARTER=x \
      "$ROOT/bin/fm-brief.sh" unresolved-state --secondmate --no-projects >/dev/null 2>"$err"
  ); status=$?
  expect_code 1 "$status" "an unresolved relative FM_STATE_OVERRIDE must fail"
  assert_grep "FM_STATE_OVERRIDE directory cannot be resolved: missing-state" "$err" \
    "unresolved relative FM_STATE_OVERRIDE did not fail loudly"

  (
    cd "$root" || exit 1
    FM_HOME="$home" FM_DATA_OVERRIDE=missing-data FM_SECONDMATE_CHARTER=x \
      "$ROOT/bin/fm-brief.sh" unresolved-data --secondmate --no-projects >/dev/null 2>"$err"
  ); status=$?
  expect_code 1 "$status" "an unresolved relative FM_DATA_OVERRIDE must fail"
  assert_grep "FM_DATA_OVERRIDE directory cannot be resolved: missing-data" "$err" \
    "unresolved relative FM_DATA_OVERRIDE did not fail loudly"

  pass "fm-brief.sh: relative directory inputs ignore CDPATH, render stable absolute charter paths, or fail loudly"
}

test_herdr_lab_contract_applies_to_scouts_but_not_secondmates() {
  local home brief status=0
  home="$TMP_ROOT/herdr-kind-home"
  mkdir -p "$home/data"
  FM_HOME="$home" "$ROOT/bin/fm-brief.sh" herdr-scout firstmate --scout --herdr-lab >/dev/null 2>&1
  brief="$home/data/herdr-scout/brief.md"
  assert_grep "# Herdr isolation - HARD SAFETY CONTRACT" "$brief" \
    "scout --herdr-lab brief missing the contract"

  FM_HOME="$home" FM_SECONDMATE_CHARTER=ops "$ROOT/bin/fm-brief.sh" herdr-secondmate --secondmate firstmate --herdr-lab >/dev/null 2>&1 || status=$?
  expect_code 1 "$status" "secondmate --herdr-lab must be rejected"
  assert_absent "$home/data/herdr-secondmate/brief.md" \
    "rejected secondmate --herdr-lab still wrote a brief"
  pass "fm-brief.sh: Herdr lab contract covers scouts and rejects secondmate misuse"
}

test_pause_verb_override_renders_all_brief_scaffolds() {
  local home kind id brief
  home="$TMP_ROOT/pause-verb-home"
  mkdir -p "$home/data"

  for kind in ship scout secondmate; do
    id="brief-pause-verb-$kind"
    case "$kind" in
      ship)
        FM_HOME="$home" FM_CLASSIFY_PAUSED_VERB=awaiting \
          "$ROOT/bin/fm-brief.sh" "$id" firstmate --mode no-mistakes >/dev/null 2>&1
        ;;
      scout)
        FM_HOME="$home" FM_CLASSIFY_PAUSED_VERB=awaiting \
          "$ROOT/bin/fm-brief.sh" "$id" firstmate --scout >/dev/null 2>&1
        ;;
      secondmate)
        FM_HOME="$home" FM_CLASSIFY_PAUSED_VERB=awaiting \
          "$ROOT/bin/fm-brief.sh" "$id" --secondmate --no-projects >/dev/null 2>&1
        ;;
    esac
    brief="$home/data/$id/brief.md"
    assert_grep "States: working, needs-decision, blocked, awaiting, done, failed." "$brief" \
      "$kind brief did not render the configured pause verb in its states list"
    # shellcheck disable=SC2016 # Literal backticks and braces must remain unexpanded.
    assert_grep 'Use `awaiting: {why}`' "$brief" \
      "$kind brief did not instruct the configured pause status"
    # shellcheck disable=SC2016 # Literal backticks and braces must remain unexpanded.
    assert_no_grep '`paused: {why}`' "$brief" \
      "$kind brief still instructs the default paused status"
    assert_grep 'a blocker or wait clears' "$brief" \
      "$kind brief did not require durable resolution when a blocker clears"
    assert_grep 'even when the answer is what started that work' "$brief" \
      "$kind brief did not warn that an answer-started done/working never closes a decision"
  done
  pass "fm-brief.sh: custom pause verb renders in every scaffold"
}

test_scout_and_secondmate_load_decision_hold_policy() {
  local home scout charter
  home="$TMP_ROOT/decision-policy-home"
  mkdir -p "$home/data"
  FM_HOME="$home" FM_ROOT_OVERRIDE="$ROOT" \
    "$ROOT/bin/fm-brief.sh" sample-investigation sample --scout >/dev/null 2>&1
  scout="$home/data/sample-investigation/brief.md"
  assert_grep "$ROOT/.agents/skills/captain-hold-lifecycle/SKILL.md" "$scout" \
    "scout brief did not load the captain-call policy before done"
  assert_grep "pass its shared completion gate for the report and any visual review" "$scout" \
    "scout brief did not cross-reference visual-review completion"
  FM_HOME="$home" FM_ROOT_OVERRIDE="$ROOT" FM_SECONDMATE_CHARTER='sample reviews' \
    "$ROOT/bin/fm-brief.sh" sample-mate --secondmate --no-projects >/dev/null 2>&1
  charter="$home/data/sample-mate/brief.md"
  assert_grep "load \`captain-hold-lifecycle\`" "$charter" \
    "secondmate charter did not load the shared captain-call policy for detailed investigations"
  pass "fm-brief.sh: investigation and visual-review completions load the shared decision policy"
}

# A scout brief offers the Lavish review loop only when bootstrap confirms the
# supported lavish-axi floor at scaffold time; a missing or older build gets a
# text-report instruction instead, so a scout never drives a below-floor Lavish.
test_scout_lavish_line_follows_presentation_floor() {
  local base label version expect case_dir fakebin brief n=0
  local hosting='you may host the Lavish review loop yourself'
  local text_only='deliver your findings as a text report without Lavish'
  base=$(fm_test_base_path_sans "${FM_TEST_BASE_PATH:-/usr/bin:/bin:/usr/sbin:/sbin}" lavish-axi)
  while IFS='^' read -r label version expect; do
    [ -n "$label" ] || continue
    n=$((n + 1))
    case_dir="$TMP_ROOT/scout-lavish-$n"
    mkdir -p "$case_dir/home/data"
    fakebin=$(fm_fakebin "$case_dir")
    [ "$version" = absent ] || fm_fake_version_tool "$fakebin" lavish-axi FM_FAKE_LAVISH_AXI_VERSION "$version"
    PATH="$fakebin:$base" FM_HOME="$case_dir/home" \
      "$ROOT/bin/fm-brief.sh" scout-lavish alpha --scout >/dev/null \
      || fail "$label: scout scaffold failed"
    brief="$case_dir/home/data/scout-lavish/brief.md"
    if [ "$expect" = hosting ]; then
      assert_grep "$hosting" "$brief" "$label: scout brief did not offer the Lavish review loop"
      assert_no_grep "$text_only" "$brief" "$label: scout brief withheld Lavish from a compatible build"
    else
      assert_grep "$text_only" "$brief" "$label: scout brief did not ask for a text report"
      assert_no_grep "$hosting" "$brief" "$label: scout brief offered a below-floor Lavish"
    fi
  done <<'ROWS'
lavish-axi at the floor^0.1.46^hosting
lavish-axi above the floor^0.2.0^hosting
lavish-axi just below the floor^0.1.45^text
absent lavish-axi^absent^text
ROWS
  pass "fm-brief.sh: scout Lavish hosting follows the bootstrap lavish-axi floor"
}

# Scout and secondmate paths still scaffold well-formed briefs.
test_scout_and_secondmate_scaffold() {
  local brief
  FM_HOME="$BRIEF_HOME" "$ROOT/bin/fm-brief.sh" brief-scout-q6 alpha --scout >/dev/null 2>&1 \
    || fail "fm-brief.sh scout scaffold exited non-zero"
  brief="$BRIEF_HOME/data/brief-scout-q6/brief.md"
  assert_present "$brief" "scout brief was not scaffolded"
  assert_grep "SCOUT task" "$brief" "scout brief must declare itself a scout task"
  assert_grep "report.md" "$brief" "scout brief must point at the report deliverable"
  assert_grep "## Captain's intent" "$brief" "scout brief missing Captain's intent subsection"
  assert_grep "## Firstmate spec" "$brief" "scout brief missing Firstmate spec subsection"
  assert_grep "{FIRSTMATE_SPEC}" "$brief" "scout brief missing the spec placeholder"

  FM_SECONDMATE_CHARTER='Supervise the alpha domain.' \
    FM_HOME="$BRIEF_HOME" "$ROOT/bin/fm-brief.sh" brief-sm-q6 --secondmate alpha >/dev/null 2>&1 \
    || fail "fm-brief.sh secondmate scaffold exited non-zero"
  brief="$BRIEF_HOME/data/brief-sm-q6/brief.md"
  assert_present "$brief" "secondmate charter was not scaffolded"
  assert_grep "persistent second mate" "$brief" \
    "secondmate charter must declare its role"
  assert_no_grep "## Captain's intent" "$brief" \
    "secondmate charter must not grow ship/scout Task subsections"
  assert_no_grep "{FIRSTMATE_SPEC}" "$brief" \
    "secondmate charter must not carry the Firstmate spec placeholder"
  pass "fm-brief: scout and secondmate code paths still scaffold well-formed briefs"
}

# Every scaffold that tells a worker to use a decision key must state WHERE
# the token goes and show a complete correct example: a bare `[key=<slug>]`
# with no position is what taught a worker to append the token at the end of
# the line, where the fold used to read it as prose and two such lines
# silently shared the "default" bucket.
test_decision_key_position_is_taught() {
  local home brief
  home="$TMP_ROOT/key-position"
  FM_HOME="$home" "$ROOT/bin/fm-brief.sh" keypos-ship alpha --mode direct-PR >/dev/null 2>&1 \
    || fail "ship scaffold failed"
  brief="$home/data/keypos-ship/brief.md"
  assert_grep 'needs-decision [key=<slug>]: {summary of options}' "$brief" \
    "ship rule 6 lost the keyed needs-decision example"
  assert_grep 'resolved [key=<slug>]: {how it cleared}' "$brief" \
    "ship rule 6 lost the keyed resolved example"
  assert_no_grep "(same \`[key=<slug>]\` if you opened it with one)" "$brief" \
    "ship rule 6 retained the position-less bare token"

  FM_HOME="$home" "$ROOT/bin/fm-brief.sh" keypos-scout alpha --scout >/dev/null 2>&1 \
    || fail "scout scaffold failed"
  brief="$home/data/keypos-scout/brief.md"
  assert_grep 'needs-decision [key=<slug>]: {summary of options}' "$brief" \
    "scout rule 6 lost the keyed needs-decision example"
  assert_grep 'resolved [key=<slug>]: {how it cleared}' "$brief" \
    "scout rule 6 lost the keyed resolved example"

  FM_HOME="$home" FM_SECONDMATE_CHARTER='Supervise assigned work.' \
    "$ROOT/bin/fm-brief.sh" keypos-sm --secondmate --no-projects >/dev/null 2>&1 \
    || fail "secondmate scaffold failed"
  brief="$home/data/keypos-sm/brief.md"
  assert_grep 'resolved [key=<slug>]: {how it cleared}' "$brief" \
    "secondmate charter lost the keyed resolved example"
  assert_no_grep "(keyed with \`[key=<slug>]\` if you opened it with one)" "$brief" \
    "secondmate charter retained the position-less bare token"
  pass "fm-brief: every scaffold states the decision-key position with a complete example"
}

test_worker_role_scope() {
  local kind home brief
  home="$TMP_ROOT/worker-role"
  for kind in no-mistakes direct-PR local-only scout; do
    if [ "$kind" = scout ]; then
      FM_HOME="$home" "$ROOT/bin/fm-brief.sh" "$kind" arbitrary-project-name --scout >/dev/null || fail "scout scaffold failed"
    else
      FM_HOME="$home" "$ROOT/bin/fm-brief.sh" "$kind" arbitrary-project-name --mode "$kind" >/dev/null || fail "$kind scaffold failed"
    fi
    brief="$home/data/$kind/brief.md"
    assert_no_grep '# Current worker role contract' "$brief" "$kind scaffolded a second owner of the role scope fm-spawn.sh delivers"
  done
  FM_HOME="$home" FM_SECONDMATE_CHARTER='Supervise assigned work.' \
    "$ROOT/bin/fm-brief.sh" supervisor --secondmate --no-projects >/dev/null || fail "secondmate scaffold failed"
  brief="$home/data/supervisor/brief.md"
  assert_no_grep '# Current worker role contract' "$brief" "secondmate received the worker exception"
  assert_no_grep 'do not adopt the supervisor identity' "$brief" "secondmate received the worker exception"
  assert_grep "The local \`AGENTS.md\` is your job description" "$brief" "secondmate lost its supervisor contract"
  assert_grep 'That file is your parent channel' "$brief" "secondmate lost its parent channel"
  pass "fm-brief: scaffolds leave the worker role scope to the launch boundary and keep the secondmate contract"
}

# --from-task renders the two `# Task` subsections from the task record so the
# same content is composed once at filing instead of re-typed into the brief.
# These tests seed a markdown backlog file directly (the backend reads it with
# no write round-trip) and render through the real tasks-axi binary, skipping
# cleanly where it is absent.
from_task_home() {
  local home=$1
  mkdir -p "$home/data"
  cat > "$home/data/backlog.md" <<'EOF'
## Queued

## In flight
- [ ] render-src - Shrink the dispatch payload to its task section (repo: firstmate) (kind: ship) (since 2026-09-21)
  Ship on firstmate, mode=direct-PR yolo=on per registry standing posture (no deviation). Follow-up to a merged rule-match task.

  Captain intent (verbatim 2026-09-21): trim the payload to the task-relating part - more efficient, and less clutter should mean more accuracy.

  Direction: send the Task section only and measure both payload shapes. Out of scope: quota work, ladder order, AGENTS.md wording. Fork rule: PR against prajwal-395/firstmate with explicit -R, never upstream.
- [ ] render-single - One-line filing with posture and ask together (repo: firstmate) (kind: ship) (since 2026-09-21)
  Ship on firstmate, mode=direct-PR yolo=on per registry (no deviation). Captain-approved (verbatim 2026-09-21): stop re-typing intent and spec twice; compose once at filing, render mechanically.
- [ ] render-structured - Explicitly sectioned filing (repo: firstmate) (kind: scout) (since 2026-09-21)
  Ship on firstmate, mode=direct-PR yolo=on per registry (no deviation).
  ## Captain's intent
  Map the render contract before any code.
  Keep the second paragraph of the ask too.
  ## Firstmate spec
  Add the render path with tests.
  Out of scope: dispatch rules.
- [ ] render-empty - A record with no body filed yet (repo: firstmate) (kind: ship) (since 2026-09-21)
- [ ] render-quiet - A record with no captain markers (repo: firstmate) (kind: ship) (since 2026-09-21)
  Ship on firstmate, mode=direct-PR yolo=on per registry (no deviation).

  Direction: do the quiet thing. Out of scope: nothing else.
- [ ] render-tokens - A record naming the placeholders literally (repo: firstmate) (kind: ship) (since 2026-09-21)
  Ship on firstmate, mode=direct-PR yolo=on per registry (no deviation).

  Captain intent (verbatim 2026-09-21): keep literal `{TASK}` examples intact in the docs.

  Direction: keep literal `{FIRSTMATE_SPEC}` examples intact as well.

## Done
EOF
  printf '%s\n' "$home"
}

from_task_skip_unless_tasks_axi() {
  command -v tasks-axi >/dev/null 2>&1 || { pass "$1 (skip: tasks-axi absent)"; return 1; }
  return 0
}

test_render_from_task_legacy_bodies() {
  from_task_skip_unless_tasks_axi "fm-brief.sh: --from-task renders legacy bodies" || return 0
  local home brief intent_body
  home="$TMP_ROOT/from-task-legacy-home"
  from_task_home "$home" >/dev/null

  FM_HOME="$home" "$ROOT/bin/fm-brief.sh" render-ship some-proj --mode direct-PR --from-task=render-src >/dev/null 2>&1 \
    || fail "ship --from-task=render-src should exit 0"
  brief="$home/data/render-ship/brief.md"
  assert_present "$brief" "rendered ship brief was not written"
  if grep -qxF -e '{TASK}' -e '{FIRSTMATE_SPEC}' "$brief"; then
    fail "rendered ship brief kept a placeholder fill site"
  fi
  assert_grep "Shrink the dispatch payload to its task section" "$brief" \
    "rendered intent lost the record title"
  assert_grep "trim the payload to the task-relating part" "$brief" \
    "rendered intent lost the captain-verbatim paragraph"
  assert_grep "Delivery contract: mode=direct-PR" "$brief" \
    "rendered ship brief lost its machine-readable delivery contract"
  assert_grep "Verify isolation before anything else" "$brief" \
    "rendered ship brief lost the worktree-isolation assertion"
  assert_grep "# Herdr lifecycle declaration - NOT ENABLED" "$brief" \
    "rendered ship brief lost the unguarded Herdr declaration"
  intent_body=$(awk '$0 == "## Captain'"'"'s intent" { emit=1; next } emit && /^## / { exit } emit { print }' "$brief")
  assert_contains "$intent_body" "trim the payload" \
    "intent subsection is missing the captain paragraph"
  assert_not_contains "$intent_body" "Direction:" \
    "intent subsection leaked the Firstmate direction paragraph"
  assert_grep "Direction: send the Task section only" "$brief" \
    "spec lost the filed direction"

  FM_HOME="$home" "$ROOT/bin/fm-brief.sh" render-src some-proj --scout --from-task >/dev/null 2>&1 \
    || fail "bare scout --from-task should render from the same id"
  brief="$home/data/render-src/brief.md"
  if grep -qxF -e '{TASK}' -e '{FIRSTMATE_SPEC}' "$brief"; then
    fail "rendered scout brief kept a placeholder fill site"
  fi
  assert_grep "SCOUT task" "$brief" "rendered scout brief lost its scout contract"
  assert_grep "trim the payload to the task-relating part" "$brief" \
    "rendered scout brief lost the captain paragraph"

  FM_HOME="$home" "$ROOT/bin/fm-brief.sh" render-single-copy some-proj --mode direct-PR --from-task=render-single >/dev/null 2>&1 \
    || fail "single-line --from-task should exit 0"
  brief="$home/data/render-single-copy/brief.md"
  intent_body=$(awk '$0 == "## Captain'"'"'s intent" { emit=1; next } emit && /^## / { exit } emit { print }' "$brief")
  assert_contains "$intent_body" "stop re-typing intent and spec twice" \
    "single-line render lost the captain tail"
  assert_not_contains "$intent_body" "Ship on firstmate" \
    "single-line render leaked the delivery posture into the intent"
  assert_grep "Ship on firstmate, mode=direct-PR" "$brief" \
    "single-line render lost the delivery posture from the spec"
  pass "fm-brief.sh: --from-task renders legacy bodies into intent and spec"
}

test_render_from_task_structured_markers() {
  from_task_skip_unless_tasks_axi "fm-brief.sh: --from-task renders sectioned filings" || return 0
  local home brief intent_body spec_body
  home="$TMP_ROOT/from-task-structured-home"
  from_task_home "$home" >/dev/null

  FM_HOME="$home" "$ROOT/bin/fm-brief.sh" render-structured-copy some-proj --scout --from-task=render-structured >/dev/null 2>&1 \
    || fail "sectioned --from-task should exit 0"
  brief="$home/data/render-structured-copy/brief.md"
  if grep -qxF -e '{TASK}' -e '{FIRSTMATE_SPEC}' "$brief"; then
    fail "sectioned render kept a placeholder fill site"
  fi
  intent_body=$(awk '$0 == "## Captain'"'"'s intent" { emit=1; next } emit && /^## / { exit } emit { print }' "$brief")
  spec_body=$(awk '$0 == "## Firstmate spec" { emit=1; next } emit && /^# / { exit } emit { print }' "$brief")
  assert_contains "$intent_body" "Map the render contract before any code." \
    "sectioned render lost the first intent paragraph"
  assert_contains "$intent_body" "Keep the second paragraph of the ask too." \
    "sectioned render lost the second intent paragraph"
  assert_not_contains "$intent_body" "Add the render path" \
    "sectioned render leaked spec material into the intent"
  assert_contains "$spec_body" "Add the render path with tests." \
    "sectioned render lost the filed spec"
  assert_contains "$spec_body" "Ship on firstmate, mode=direct-PR" \
    "sectioned render lost the unmarked filing context from the spec"
  assert_not_contains "$spec_body" "Map the render contract" \
    "sectioned render leaked intent material into the spec"
  pass "fm-brief.sh: --from-task honours explicitly sectioned filings"
}

test_render_hand_edit_after_render() {
  from_task_skip_unless_tasks_axi "fm-brief.sh: rendered briefs stay hand-editable" || return 0
  local home brief
  home="$TMP_ROOT/from-task-handedit-home"
  from_task_home "$home" >/dev/null
  # shellcheck source=bin/fm-dod-lib.sh
  . "$ROOT/bin/fm-dod-lib.sh"

  FM_HOME="$home" "$ROOT/bin/fm-brief.sh" render-edit some-proj --mode direct-PR --from-task=render-quiet >/dev/null 2>&1 \
    || fail "render of a marker-free record should exit 0 with a title-only intent"
  brief="$home/data/render-edit/brief.md"
  awk '$0 == "## Firstmate spec" { print; print "Hand note: keep the render mechanical."; next } { print }' \
    "$brief" > "$brief.hand" && mv "$brief.hand" "$brief"
  fm_brief_task_placeholders_present "$brief" \
    && fail "hand-edited render still reads as placeholder"
  fm_brief_task_content_valid "$brief" \
    || fail "hand-edited render failed the spawn/promote content backstop"

  FM_HOME="$home" "$ROOT/bin/fm-brief.sh" render-mentions some-proj --mode direct-PR --from-task=render-tokens >/dev/null 2>&1 \
    || fail "render of a record naming placeholders should exit 0"
  brief="$home/data/render-mentions/brief.md"
  fm_brief_task_placeholders_present "$brief" \
    && fail "a rendered brief that merely mentions placeholder tokens reads as unfilled"
  fm_brief_task_content_valid "$brief" \
    || fail "a rendered brief that merely mentions placeholder tokens failed content validation"

  awk '$0 == "## Firstmate spec" { emit=1; next } emit { next } { print }' \
    "$brief" > "$brief.gutted" && mv "$brief.gutted" "$brief"
  fm_brief_task_content_valid "$brief" \
    && fail "a render gutted to an empty spec passed the content backstop"
  pass "fm-brief.sh: rendered briefs stay hand-editable under the placeholder backstop"
}

test_render_refusals_write_nothing() {
  from_task_skip_unless_tasks_axi "fm-brief.sh: --from-task refusals" || return 0
  local home out status
  home="$TMP_ROOT/from-task-refusal-home"
  from_task_home "$home" >/dev/null

  out=$(FM_HOME="$home" "$ROOT/bin/fm-brief.sh" render-nope some-proj --mode direct-PR --from-task 2>&1)
  status=$?
  [ "$status" -ne 0 ] || fail "render from an unknown record should exit non-zero"
  assert_contains "$out" "render-nope" "unknown-record refusal did not name the record"
  assert_absent "$home/data/render-nope/brief.md" "unknown-record render still wrote a brief"

  out=$(FM_HOME="$home" "$ROOT/bin/fm-brief.sh" render-empty some-proj --mode direct-PR --from-task 2>&1)
  status=$?
  [ "$status" -ne 0 ] || fail "render from an empty body should exit non-zero"
  assert_contains "$out" "body is empty" "empty-body refusal did not explain the contract"
  assert_absent "$home/data/render-empty/brief.md" "empty-body render still wrote a brief"

  out=$(FM_HOME="$home" FM_SECONDMATE_CHARTER='x' "$ROOT/bin/fm-brief.sh" render-sm --secondmate --no-projects --from-task 2>&1)
  status=$?
  [ "$status" -ne 0 ] || fail "--from-task on a secondmate charter should exit non-zero"
  assert_contains "$out" "--from-task applies only to ship or scout briefs" \
    "charter refusal did not explain the untouched charter path"
  assert_absent "$home/data/render-sm/brief.md" "charter render still wrote a brief"

  FM_HOME="$home" "$ROOT/bin/fm-brief.sh" render-clash some-proj --mode direct-PR >/dev/null 2>&1 \
    || fail "plain placeholder scaffold should exit 0"
  out=$(FM_HOME="$home" "$ROOT/bin/fm-brief.sh" render-clash some-proj --mode direct-PR --from-task=render-src 2>&1)
  status=$?
  [ "$status" -ne 0 ] || fail "render over an existing brief should exit non-zero"
  assert_contains "$out" "already exists" "existing-brief render did not refuse to overwrite"
  assert_grep "{TASK}" "$home/data/render-clash/brief.md" \
    "existing-brief render overwrote the hand-fill scaffold"
  pass "fm-brief.sh: --from-task refusals write nothing"
}

# Scope-discipline contract (AGENTS.md section 11, single owner
# bin/fm-dod-lib.sh): ## Captain's intent carries the captain's own ask as the
# acceptance criteria, ## Firstmate spec only the build instructions that ask
# requires. bin/fm-brief.sh --check reports readiness while bin/fm-spawn.sh
# and bin/fm-promote.sh enforce the identical gate, so every test below pins
# both the --check verdict and the library verdict the launch paths consume.
scope_fill_ship_brief() {  # <brief> <intent-line> <spec-line>
  local brief=$1 intent=$2 spec=$3
  awk -v intent="$intent" -v spec="$spec" '
    $0 == "{TASK}" { print intent; next }
    $0 == "{FIRSTMATE_SPEC}" { print spec; next }
    { print }
  ' "$brief" > "$brief.filled" && mv "$brief.filled" "$brief"
}

test_scope_check_ready_brief_passes() {
  local home id brief out status
  home="$TMP_ROOT/scope-ready-home"
  mkdir -p "$home/data"
  id="scope-ready"
  FM_HOME="$home" "$ROOT/bin/fm-brief.sh" "$id" some-proj --mode direct-PR >/dev/null 2>&1 \
    || fail "ship scaffold failed"
  brief="$home/data/$id/brief.md"
  scope_fill_ship_brief "$brief" \
    "Fix the retry detector the captain reported." \
    "Patch bin/fm-opencode-retry.sh with a regression test."
  # shellcheck source=bin/fm-dod-lib.sh
  . "$ROOT/bin/fm-dod-lib.sh"
  fm_brief_scope_check "$brief" "this spawn" \
    || fail "a narrow brief failed the library scope gate the spawn consumes"
  out=$(FM_HOME="$home" "$ROOT/bin/fm-brief.sh" "$id" --check 2>&1); status=$?
  expect_code 0 "$status" "a narrow filled brief should pass --check (got: $out)"
  assert_contains "$out" "ready:" "--check did not report the ready case"
  pass "fm-brief.sh: --check passes a narrow filled brief"
}

test_scope_check_refuses_unfilled_brief() {
  local home id out status
  home="$TMP_ROOT/scope-unfilled-home"
  mkdir -p "$home/data"
  id="scope-unfilled"
  FM_HOME="$home" "$ROOT/bin/fm-brief.sh" "$id" some-proj --mode direct-PR >/dev/null 2>&1 \
    || fail "ship scaffold failed"
  out=$(FM_HOME="$home" "$ROOT/bin/fm-brief.sh" "$id" --check 2>&1); status=$?
  [ "$status" -ne 0 ] || fail "--check passed a brief that still carries placeholders"
  assert_contains "$out" "{TASK}" "--check did not name the unfilled placeholder"
  pass "fm-brief.sh: --check refuses a brief that still carries placeholders"
}

test_scope_check_refuses_intent_coverage_list() {
  local home id brief out status
  home="$TMP_ROOT/scope-coverage-home"
  mkdir -p "$home/data"
  # shellcheck source=bin/fm-dod-lib.sh
  . "$ROOT/bin/fm-dod-lib.sh"
  id="scope-coverage-bullets"
  mkdir -p "$home/data/$id"
  cat > "$home/data/$id/brief.md" <<'EOF'
# Task
## Captain's intent
Rebuild the brief system:
- Rewrite the scaffold
- Redesign spawn
- Rework promotion
- Document everything

## Firstmate spec
Add the check with tests.
EOF
  brief="$home/data/$id/brief.md"
  out=$(fm_brief_scope_check "$brief" "this spawn" 2>&1); status=$?
  [ "$status" -ne 0 ] || fail "the library gate passed a four-item intent coverage list"
  assert_contains "$out" "enumerated coverage list" "the library refusal did not name the coverage list"
  out=$(FM_HOME="$home" "$ROOT/bin/fm-brief.sh" "$id" --check 2>&1); status=$?
  [ "$status" -ne 0 ] || fail "--check passed a four-item intent coverage list"
  assert_contains "$out" "enumerated coverage list" "--check did not name the coverage list"
  id="scope-coverage-numbered"
  mkdir -p "$home/data/$id"
  cat > "$home/data/$id/brief.md" <<'EOF'
# Task
## Captain's intent
Fix the three lanes the captain named:
1. Redesign spawn
2. Rework promotion
3. Document everything

## Firstmate spec
Add the check with tests.
EOF
  brief="$home/data/$id/brief.md"
  out=$(FM_HOME="$home" "$ROOT/bin/fm-brief.sh" "$id" --check 2>&1); status=$?
  [ "$status" -ne 0 ] || fail "--check passed a three-item numbered intent coverage list"
  assert_contains "$out" "enumerated coverage list" "--check did not name the numbered coverage list"
  pass "fm-brief.sh: --check refuses an intent widened into a coverage list"
}

test_scope_check_allows_short_intent_structure() {
  local home id brief out status
  home="$TMP_ROOT/scope-short-home"
  mkdir -p "$home/data"
  id="scope-short"
  mkdir -p "$home/data/$id"
  cat > "$home/data/$id/brief.md" <<'EOF'
# Task
## Captain's intent
Fix the retry detector the captain reported:
- Detect the idle-after-cap lane
- Keep the sidecar-only lane working

## Firstmate spec
Patch bin/fm-opencode-retry.sh with a regression test.
EOF
  brief="$home/data/$id/brief.md"
  out=$(FM_HOME="$home" "$ROOT/bin/fm-brief.sh" "$id" --check 2>&1); status=$?
  expect_code 0 "$status" "a two-item intent structure should pass --check (got: $out)"
  pass "fm-brief.sh: --check allows the ask's own short structure"
}

test_scope_check_allows_item_references() {
  local home id brief out status
  home="$TMP_ROOT/scope-refs-home"
  mkdir -p "$home/data"
  id="scope-refs"
  mkdir -p "$home/data/$id"
  cat > "$home/data/$id/brief.md" <<'EOF'
# Task
## Captain's intent
Do items 1, 2 and 7 of the queued-lane report: shrink the dispatch payload to its task section.

## Firstmate spec
Send the Task section only and measure both payload shapes.
EOF
  brief="$home/data/$id/brief.md"
  out=$(FM_HOME="$home" "$ROOT/bin/fm-brief.sh" "$id" --check 2>&1); status=$?
  expect_code 0 "$status" "an inline item reference should pass --check (got: $out)"
  pass "fm-brief.sh: --check allows inline item references that point at the ask"
}

test_scope_check_ignores_fenced_lists() {
  local home id brief out status
  home="$TMP_ROOT/scope-fence-home"
  mkdir -p "$home/data"
  id="scope-fence"
  mkdir -p "$home/data/$id"
  cat > "$home/data/$id/brief.md" <<'EOF'
# Task
## Captain's intent
Fix the retry detector the captain reported. The failing output looks like:
```
- lane one reads OPEN
- lane two reads OPEN
- lane three reads OPEN
- lane four reads OPEN
```

## Firstmate spec
Patch bin/fm-opencode-retry.sh with a regression test.
EOF
  brief="$home/data/$id/brief.md"
  out=$(FM_HOME="$home" "$ROOT/bin/fm-brief.sh" "$id" --check 2>&1); status=$?
  expect_code 0 "$status" "a fenced illustration should pass --check (got: $out)"
  pass "fm-brief.sh: --check does not count fenced illustration lines as a coverage list"
}

test_scope_check_refuses_spec_sweep() {
  local home id brief out status n=0 phrase
  home="$TMP_ROOT/scope-sweep-home"
  mkdir -p "$home/data"
  while IFS='^' read -r phrase; do
    [ -n "$phrase" ] || continue
    n=$((n + 1))
    id="scope-sweep-$n"
    mkdir -p "$home/data/$id"
    cat > "$home/data/$id/brief.md" <<EOF
# Task
## Captain's intent
Fix the retry detector the captain reported.

## Firstmate spec
$phrase
EOF
    brief="$home/data/$id/brief.md"
    out=$(FM_HOME="$home" "$ROOT/bin/fm-brief.sh" "$id" --check 2>&1); status=$?
    [ "$status" -ne 0 ] || fail "--check passed a spec sweep ('$phrase')"
    assert_contains "$out" "## Firstmate spec" "--check did not name the widened spec ('$phrase')"
  done <<'ROWS'
Generalize the detector across every caller of the ladder.
Run a consistency sweep of the retry handling before shipping.
Roll the fix out repo-wide in the same change.
Harden all inputs on every endpoint in this task.
ROWS
  pass "fm-brief.sh: --check refuses a spec widened beyond the ask"
}

test_scope_check_allows_guarded_scope_language() {
  local home id brief out status
  home="$TMP_ROOT/scope-guarded-home"
  mkdir -p "$home/data"
  id="scope-guarded"
  mkdir -p "$home/data/$id"
  cat > "$home/data/$id/brief.md" <<'EOF'
# Task
## Captain's intent
Fix the retry detector the captain reported.

## Firstmate spec
Patch bin/fm-opencode-retry.sh with a regression test.
Do NOT run a consistency sweep of the repo; that is follow-up work.
Out of scope: fleet-wide rollout, which the captain did not ask for.
EOF
  brief="$home/data/$id/brief.md"
  out=$(FM_HOME="$home" "$ROOT/bin/fm-brief.sh" "$id" --check 2>&1); status=$?
  expect_code 0 "$status" "a spec that names its boundary should pass --check (got: $out)"
  pass "fm-brief.sh: --check allows a spec that names its boundary"
}

# The gate refuses list and sweep shapes only, never prose breadth: a brief
# that broadens the ask in plain sentences passes, and that limit is pinned
# here so it stays a recorded decision rather than a later discovery.
test_scope_check_documents_its_blind_spot() {
  local home id brief out status
  home="$TMP_ROOT/scope-blind-home"
  mkdir -p "$home/data"
  id="scope-blind"
  mkdir -p "$home/data/$id"
  cat > "$home/data/$id/brief.md" <<'EOF'
# Task
## Captain's intent
Make the pipeline robust and fast while you are there.

## Firstmate spec
Refactor the auth module for clarity along the way.
EOF
  brief="$home/data/$id/brief.md"
  out=$(FM_HOME="$home" "$ROOT/bin/fm-brief.sh" "$id" --check 2>&1); status=$?
  expect_code 0 "$status" "plain-prose breadth should pass --check (got: $out)"
  pass "fm-brief.sh: --check deliberately does not judge plain-prose breadth"
}

test_scope_check_legacy_brief_warns_and_passes() {
  local home id brief out status
  home="$TMP_ROOT/scope-legacy-home"
  mkdir -p "$home/data"
  id="scope-legacy"
  mkdir -p "$home/data/$id"
  cat > "$home/data/$id/brief.md" <<'EOF'
# Task
Fix the retry detector the captain reported, with a regression test.
EOF
  brief="$home/data/$id/brief.md"
  out=$(FM_HOME="$home" "$ROOT/bin/fm-brief.sh" "$id" --check 2>&1); status=$?
  expect_code 0 "$status" "a legacy brief should still dispatch (got: $out)"
  assert_contains "$out" "warning:" "--check did not warn that the legacy brief skips scope discipline"
  pass "fm-brief.sh: --check warns once for a legacy brief and still dispatches"
}

test_check_takes_only_a_task_id() {
  local home out status
  home="$TMP_ROOT/check-args-home"
  mkdir -p "$home/data"
  out=$(FM_HOME="$home" "$ROOT/bin/fm-brief.sh" missing --check 2>&1); status=$?
  [ "$status" -ne 0 ] || fail "--check on a missing brief should exit non-zero"
  assert_contains "$out" "no brief at" "--check did not name the missing brief"
  out=$(FM_HOME="$home" "$ROOT/bin/fm-brief.sh" x some-proj --mode direct-PR --check 2>&1); status=$?
  [ "$status" -ne 0 ] || fail "--check with --mode should exit non-zero"
  assert_contains "$out" "takes no --mode" "--check did not refuse the mode flag"
  out=$(FM_HOME="$home" "$ROOT/bin/fm-brief.sh" x some-proj --scout --check 2>&1); status=$?
  [ "$status" -ne 0 ] || fail "--check with --scout should exit non-zero"
  assert_contains "$out" "takes no --scout" "--check did not refuse the scout flag"
  out=$(FM_HOME="$home" "$ROOT/bin/fm-brief.sh" --check 2>&1); status=$?
  [ "$status" -ne 0 ] || fail "--check with no task id should exit non-zero"
  assert_contains "$out" "exactly one task id" "--check did not demand exactly one task id"
  pass "fm-brief.sh: --check inspects exactly one existing brief"
}

test_worker_role_scope
test_render_from_task_legacy_bodies
test_render_from_task_structured_markers
test_render_hand_edit_after_render
test_render_refusals_write_nothing
test_scope_check_ready_brief_passes
test_scope_check_refuses_unfilled_brief
test_scope_check_refuses_intent_coverage_list
test_scope_check_allows_short_intent_structure
test_scope_check_allows_item_references
test_scope_check_ignores_fenced_lists
test_scope_check_refuses_spec_sweep
test_scope_check_allows_guarded_scope_language
test_scope_check_documents_its_blind_spot
test_scope_check_legacy_brief_warns_and_passes
test_check_takes_only_a_task_id
test_decision_key_position_is_taught
test_script_parses
test_no_heredoc_in_command_substitution
test_help_includes_entire_header
test_ship_modes_generate_clean_briefs
test_ship_mode_is_required_and_closed_set
test_ship_mode_is_explicit_not_registry
test_delivery_flags_are_refused_where_they_do_not_apply
test_faster_paths_use_configured_authority_without_stacked_review
test_no_mistakes_dod_wording
test_ship_pr_modes_carry_never_upstream_rule
test_ask_user_escalation_format
test_ship_project_memory_wording
test_herdr_lab_contract_is_explicit_and_complete
test_herdr_lab_contract_quotes_foreign_firstmate_path
test_herdr_lab_omission_is_loud_for_ship_and_scout
test_documented_global_replace_leaves_the_herdr_gate_intact
test_herdr_lab_contract_applies_to_scouts_but_not_secondmates
test_secondmate_no_projects_charter
test_secondmate_marked_request_reporting_contract
test_secondmate_directory_paths_are_absolute_and_output_is_stable
test_pause_verb_override_renders_all_brief_scaffolds
test_scout_and_secondmate_load_decision_hold_policy
test_scout_and_secondmate_scaffold
test_scout_lavish_line_follows_presentation_floor
