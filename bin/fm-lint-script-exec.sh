#!/usr/bin/env bash
# fm-lint-script-exec.sh - owner of firstmate's sibling-script exec lint.
#
# Rejects a firstmate shell script that runs another firstmate shell script by
# path (`"$dir/fm-x.sh" args`) instead of through an explicit interpreter
# (`bash "$dir/fm-x.sh" args`). On macOS 26 the kernel leaks one 1 KiB
# kalloc.1024 element, held until reboot, every time a process that was itself
# started through a `#!` line execs another `#!` script by path; an explicit
# interpreter execs a Mach-O binary instead and leaks nothing. A leaked zone
# eventually panics the machine, so firstmate's script-to-script calls must not
# take the `#!` path.
#
# A finding is a command word naming a tracked bin/, bin/backends/, or tests/
# shell script by path, or a variable this same file assigned such a path,
# at command position: line start, after a control operator, a compound
# keyword or case pattern, `exec`, `$(`, a backtick, leading NAME=value
# assignments, or a command wrapper: env, timeout, nohup, command, nice,
# caffeinate, a bare $VAR followed by assignments, and firstmate's own
# fm_run_timed, run_bounded, run_step, isolate_process, branch_env, and lab_run.
# Passing a script path as data to another command, a quoted string, an array
# assignment, and heredoc bodies are not findings. Backslash-continued lines
# are read as one line, where leading lines holding only NAME=value assignments
# end a command prefix. The scan is a heuristic, not a shell parser.
#
# Allowlist, for a by-path exec that must stay (for example a path that a test
# deliberately points at a non-bash stub, or prose a worker reads):
#   ... "$dir/fm-x.sh"  # fm-script-exec-ok: <reason>       this line only
#   # fm-script-exec-ok: <reason>                            the next line only
#   # fm-script-exec-ok-begin: <reason>  ...  # fm-script-exec-ok-end
# Every directive needs a non-empty reason after the colon.
#
# bin/fm-lint.sh invokes this owner on bin/*.sh, bin/backends/*.sh, and
# tests/*.sh, or on the explicit lint roots it was given.
#
# Usage:
#   fm-lint-script-exec.sh                lint the canonical shell set
#   fm-lint-script-exec.sh <path>...      lint explicit files
#   fm-lint-script-exec.sh --fix [path]...  insert the target's interpreter before each finding
#   fm-lint-script-exec.sh --help
set -u

SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
SELF="$SELF_DIR/fm-lint-script-exec.sh"
ROOT="$(cd "$SELF_DIR/.." && pwd -P)"

case "${1:-}" in
  -h|--help)
    sed -n '2,/^set -u$/{/^set -u$/d;s/^# \{0,1\}//;p;}' "$SELF"
    exit 0
    ;;
esac

PERL_BIN=$(command -v perl 2>/dev/null) || {
  printf 'fm-lint-script-exec.sh: perl is required.\n' >&2
  exit 127
}

FIX=()
if [ "${1:-}" = --fix ]; then
  FIX=(--fix)
  shift
fi
if [ "$#" -eq 0 ]; then
  set -- "$ROOT"/bin/*.sh "$ROOT"/bin/backends/*.sh "$ROOT"/tests/*.sh
fi

# shellcheck disable=SC2016 # Perl, not the shell, expands these variables.
FM_LINT_SCRIPT_EXEC_ROOT="$ROOT" exec "$PERL_BIN" -e '
use strict;
use warnings;

my $root = $ENV{FM_LINT_SCRIPT_EXEC_ROOT};
# Each known script maps to the interpreter --fix inserts: the absolute path
# its shebang names (#!/bin/bash), else plain bash (#!/usr/bin/env bash).
my %known;
for my $dir ("bin", "bin/backends", "tests") {
  opendir(my $dh, "$root/$dir") or next;
  for my $name (readdir $dh) {
    next unless $name =~ /\.sh\z/ && -f "$root/$dir/$name";
    my $interp = "bash";
    if (open(my $sh, "<", "$root/$dir/$name")) {
      my $first = <$sh> // "";
      $interp = $1 if $first =~ m{\A#!(/\S*/bash)\s*\z};
      close $sh;
    }
    $known{$name} = $interp;
  }
  closedir $dh;
}

my $assign = q~[A-Za-z_][A-Za-z0-9_]*=(?:"(?:[^"\\\\$]|\\\\.|\$\(\([^()]*\)\)|\$\([^()]*\)|\$(?!\())*"|\x27[^\x27]*\x27|\$\(\([^()]*\)\)|\$\([^()]*\)|[^\s;&|()]+)?~;
# Words env takes as its own arguments: "$@", an array, an optional array, or a
# bare variable expanding to NAME=value pairs.
my $expansion = q~(?:"\$@"|"\$\{[A-Za-z_][A-Za-z0-9_]*\[@\]\}"|\$\{[A-Za-z_][A-Za-z0-9_]*\[@\]\+"\$\{[A-Za-z_][A-Za-z0-9_]*\[@\]\}"\}|\$\{?[A-Za-z_][A-Za-z0-9_]*\}?)~;
my $arg = q{(?:"[^"]*"|[^\s"\x27;&|()]+)};
my $wrapper = qr{(?:
    env(?:\s+(?:-[uCS]\s+\S+|-\S+))*(?:\s+(?:$assign|$expansion))*
  | (?:fm_run_timed|isolate_process)\s+$arg
  | run_bounded\s+$arg\s+$arg\s+$arg
  | run_step | branch_env | lab_run(?:\s+$assign)*
  | timeout(?:\s+(?:-[ks]\s+\S+|-\S+))*\s+[0-9][0-9.]*[smhd]?
  | nohup | command | exec
  | \$\{?[A-Za-z_][A-Za-z0-9_]*\}?(?:\s+$assign)+
  | nice(?:\s+-n\s*\S+|\s+-[0-9]+)?
  | caffeinate(?:\s+-\S+)*
)}x;
my $start = qr%(?:^|^\s*\(?[^()\s"]+\)|[;&|{!`]|(?<!=)\(|\$\(|\b(?:then|do|else|elif|if|while|until|time)\b)%;
my $prefix = qr{$start\s*(?:$assign\s+)*(?:$wrapper\s+)*}x;
my $path_word = qr<(?<q>"?)(?<word>(?:\$\{?[A-Za-z_][A-Za-z0-9_]*\}?|\$\([^()]*\)|[A-Za-z0-9_.~/{}:+-])*/(?<base>[A-Za-z0-9_.-]+\.sh))\k<q>(?=[\s;&|)`]|\z)>;

my $fix = 0;
if (@ARGV && $ARGV[0] eq "--fix") { $fix = 1; shift @ARGV }

# True when offset $at of $line sits inside a quoted string rather than at
# shell level (a command substitution opened inside double quotes is shell).
sub quoted_at {
  my ($line, $at) = @_;
  my ($dq, $sq, @outer) = (0, 0);
  my $text = substr($line, 0, $at);
  while ($text =~ /(\\.|"|\x27|\$\(|\))/g) {
    my $t = $1;
    if ($sq) { $sq = 0 if $t eq "\x27"; next }
    next if $t =~ /\A\\/;
    if ($t eq "\x27" && !$dq) { $sq = 1 }
    elsif ($t eq "\"") { $dq = !$dq }
    elsif ($t eq "\$(") { push @outer, $dq; $dq = 0 }
    elsif ($t eq ")" && @outer && !$dq) { $dq = pop @outer }
  }
  return $sq || $dq;
}

# Find every finding in one logical line (continuations joined); with $fix,
# insert the interpreter of the target before each one. Returns [offset, word] pairs.
sub scan {
  my ($line_ref, $var_word, $vars) = @_;
  # Join continuations. While every line so far holds only NAME=value
  # assignments, the break reads as a command start, so env-prefix layouts with
  # complex values still count; offsets stay those of the original text.
  my ($line, $prefix_only) = ("", 1);
  for my $piece (split /(?<=\n)/, $$line_ref) {
    if ($piece =~ /\A(.*)\\\n\z/s) {
      my $head = $1;
      if ($prefix_only) {
        my $words = $head;
        1 while $words =~ s/\$\(\([^()]*\)\)|\$\([^()]*\)|`[^`]*`/Q/g;
        $words =~ s/"[^"]*"|\x27[^\x27]*\x27/Q/g;
        $words =~ s/^\s*(?:[A-Za-z_][A-Za-z0-9_]*=)?\$?\(//;
        $prefix_only = !grep { !/^[A-Za-z_][A-Za-z0-9_]*=/ } split " ", $words;
      }
      $piece = $prefix_only ? "$head; " : "$head  ";
    }
    $line .= $piece;
  }
  my %at;
  while ($line =~ /$prefix$path_word/g) {
    my ($word, $base, $offset) = ($+{word}, $+{base}, $-[0] + length($&) - length($+{q} . $+{word} . $+{q}));
    next unless $known{$base} && $word =~ m{\$|\A(?:\.{0,2}/|~)};
    $at{$offset} = [$word, $known{$base}];
  }
  if (defined $var_word) {
    while ($line =~ /$prefix($var_word)/g) {
      my ($word, $offset) = ($1, $-[1]);
      $word =~ /([A-Za-z_][A-Za-z0-9_]*)/;
      $at{$offset} = [$word, $vars->{$1}];
    }
  }
  my @hits;
  for my $offset (sort { $b <=> $a } keys %at) {
    next if quoted_at($line, $offset);
    my ($word, $interp) = @{ $at{$offset} };
    unshift @hits, [$offset, $word];
    substr($$line_ref, $offset, 0) = "$interp ";
  }
  return @hits;
}

my $findings = 0;
for my $file (@ARGV) {
  open(my $fh, "<", $file) or do { print STDERR "fm-lint-script-exec.sh: cannot read $file\n"; $findings++; next };
  my @lines = <$fh>;
  close $fh;
  my (%vars, $block, $next_ok, @heredocs);
  # First pass: variables this file assigns a known script path.
  for my $line (@lines) {
    while ($line =~ /(?:^|[\s;(])(?:local\s+|readonly\s+|export\s+|declare\s+(?:-\w+\s+)*)?([A-Za-z_][A-Za-z0-9_]*)="?(?:\$\{[A-Za-z_][A-Za-z0-9_]*:-)?[^"\s;]*\/([A-Za-z0-9_.-]+\.sh)\}?"?(?=[\s;)]|\z)/g) {
      $vars{$1} = $known{$2} if $known{$2};
    }
  }
  my $var_alt = join("|", map { quotemeta } sort keys %vars);
  my $var_word = $var_alt eq "" ? undef : qr<"?\$(?:\{(?:$var_alt)\}|(?:$var_alt)\b)"?(?=[\s;&|)`]|\z)>;
  my ($i, $changed) = (0, 0);
  while ($i < @lines) {
    my $line = $lines[$i];
    # Heredoc bodies are data (prose, generated stubs, other languages).
    if (@heredocs) {
      my ($strip, $delim) = @{ $heredocs[0] };
      (my $body = $line) =~ s/\r?\n\z//;
      $body =~ s/\A\t+// if $strip;
      shift @heredocs if $body eq $delim;
      $i++;
      next;
    }
    # One logical line: a trailing backslash continues onto the next line.
    my $j = $i;
    $j++ while $j < $#lines && $lines[$j] =~ /\\\n\z/ && $lines[$j] !~ /^\s*#/;
    my $unit = join("", @lines[$i .. $j]);
    my $ok_here = $next_ok;
    $next_ok = 0;
    my $skip = 0;
    if ($line =~ /#\s*fm-script-exec-ok-begin:\s*\S/) { $block = 1; $skip = 1 }
    elsif ($line =~ /#\s*fm-script-exec-ok-end\b/) { $block = 0; $skip = 1 }
    elsif ($line =~ /^\s*#\s*fm-script-exec-ok:\s*\S/) { $next_ok = 1; $skip = 1 }
    $skip ||= $block || $ok_here || $unit =~ /#\s*fm-script-exec-ok:\s*\S/ || $line =~ /^\s*#/;
    unless ($skip) {
      my $copy = $unit;
      for my $hit (scan(\$copy, $var_word, \%vars)) {
        my ($offset, $word) = @$hit;
        my $n = $i + 1 + (substr($unit, 0, $offset) =~ tr/\n//);
        $findings++;
        print "$file:$n: runs firstmate script $word by path; call it through bash (see bin/fm-lint-script-exec.sh)\n";
      }
      if ($fix && $copy ne $unit) {
        splice(@lines, $i, $j - $i + 1, split(/(?<=\n)/, $copy));
        $changed = 1;
      }
    }
    for my $k ($i .. $j) {
      while ($lines[$k] =~ /(?<!<)<<(-?)\s*(["\x27]?)\\?([A-Za-z_][A-Za-z0-9_]*)\2/g) {
        push @heredocs, [$1 eq "-", $3];
      }
    }
    $i = $j + 1;
  }
  if ($changed) {
    open(my $out, ">", $file) or die "fm-lint-script-exec.sh: cannot write $file\n";
    print $out @lines;
    close $out;
  }
}
exit($findings ? 1 : 0);
' -- "${FIX[@]}" "$@"
