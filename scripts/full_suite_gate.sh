#!/usr/bin/env bash
#
# LAYER 1 of the three-layer CI design: the full test suite, run LOCALLY,
# once, on the integrated branch, before a batch merges.  See
# `docs/CI_LAYERS.md` and the header of `.github/workflows/ci.yml`.
#
#     scripts/full_suite_gate.sh                  # the gate (parallel lanes)
#     scripts/full_suite_gate.sh --no-parallel    # serial control, same commit
#     scripts/full_suite_gate.sh --skip-heavy-ml  # CI-equivalent selection only
#
# It prints ONE verdict line, last, beginning `FULL-SUITE GATE:`.  A
# caller may grep for `FULL-SUITE GATE: PASS` and for nothing else.
#
# WHY IT IS SHAPED LIKE THIS.  This repository spent two days removing
# checks that reported success without executing (AGENTS.md 10.4: "a gate
# that cannot fail is worse than no gate, because it reads as coverage").
# So this script is FAIL-CLOSED and it does not trust its own exit codes
# alone:
#
#   - the verdict starts as DID NOT RUN and is only ever narrowed;
#   - PASS additionally requires a JUnit report that pytest itself wrote,
#     with a POSITIVE test count and zero failures and zero errors.  An
#     interpreter that is missing, a collection error, an empty selection
#     and an interrupted run all leave that report absent or empty, and
#     all of them therefore read DID NOT RUN rather than PASS;
#   - PASS means the FULL environment was present and every test either
#     passed or was part of a complementary pair.  When a capability is
#     missing and tests were skipped because of it, the verdict is
#     NARROWED PASS - never an unqualified PASS - and every missing
#     capability is named with what it costs and how to install it.
#
# TWO LANES, ONE VERDICT.  The `not heavy_ml` selection runs sharded:
# a PARALLEL lane (`pytest -n <workers> --dist loadfile`, which keeps
# each file's tests on one worker in collection order) over everything
# the boundary routes parallel, and a SERIAL lane (single-process) over
# exactly the files the boundary routes serial.  The boundary is
# executable code run fresh on every invocation
# (`library/tools/lane_routing.py`), never a checked-in list, so a
# new test is routed by the rule its own code matches.  `--no-parallel`
# runs the legacy single-process selection instead: the CONTROL the
# parallel result is compared against (same commit, executed count and
# pass/fail/skip SET, not merely the verdict - a sharded run that
# silently drops tests looks exactly like a big win).
#
# Five load-bearing details (data/vep-parallelise-the-test-gate/report.md):
# lane exit codes reach the verdict (a clean merged report with a dirty
# lane exit is FAIL, the 2026-09-12 shape); no upfront xdist probe (lazy
# detect: exit 4 with unrecognized-arguments and no lane report refuses
# as DID NOT RUN naming xdist); the undeclared-skip property is
# re-derived from the merged report (the root conftest hook goes blind
# under xdist); throttle-versus-race triage prints on FAIL, advisory
# only; per-lane executed/skipped counts print every run.
#
# The `heavy_ml` selection is deliberately part of the LOCAL layer: those
# tests exercise the real Apple-Silicon dependencies, which is why the
# GitHub `heavy-ml-suite` job is demoted to a deliberate label.  If the
# interpreter here cannot run them, the verdict says so out loud, through
# the SAME capability mechanism as every other environment gap.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "${REPO_ROOT}"

# `FULL_SUITE_GATE_PYTHON` still wins.  Without it, ASK the resolution
# rather than taking the ambient `python3`: on this machine that is 3.14
# with none of the ML stack, so the heavy tier went unmeasured unless
# somebody remembered the variable - and docs/ML_ENVIRONMENT.md had to
# carry a paragraph saying so in bold.  A default that finds the durable
# venv is what removes that paragraph's reason to exist.
PYTHON="${FULL_SUITE_GATE_PYTHON:-}"
if [ -z "${PYTHON}" ]; then
  PYTHON="$(python3 -c "
import sys
sys.path.insert(0, '${REPO_ROOT}')
from library.tools.shared_environment import python_interpreter
print(python_interpreter('${REPO_ROOT}')[0])
" 2>/dev/null || true)"
fi
PYTHON="${PYTHON:-python3}"

# The lane-routing, merge, triage and counts helpers are stdlib-only, so
# they run under the resolving `python3`, never under the gate
# interpreter: that interpreter may be a shim speaking only the pytest
# argv protocol (tests/test_ci_is_deliberate.py), which refuses runs the
# lanes would have handled.  Only pytest itself runs under ${PYTHON}.
HELPER_PYTHON="python3"

# These two drive the RUNNING DaVinci Resolve and switch the current
# timeline out from under whoever is using the app.  CI has no Resolve,
# so they skip there and excluding them costs no coverage - but a local
# gate has to remember it, every time.  Named in the verdict.
RESOLVE_DRIVING=(
  tests/test_marker_capture_against_resolve.py
  tests/test_marker_feedback_against_resolve.py
)

RUN_HEAVY_ML=1
PARALLEL=1
WORKERS="auto"
while [ $# -gt 0 ]; do
  case "${1:-}" in
    --skip-heavy-ml) RUN_HEAVY_ML=0; shift ;;
    --no-parallel) PARALLEL=0; shift ;;
    -n|--workers) WORKERS="${2:?missing worker count}"; shift 2 ;;
    --workers=*) WORKERS="${1#--workers=}"; shift ;;
    --help|-h) sed -n '2,12p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "usage: $0 [--skip-heavy-ml] [--no-parallel] [-n N | --workers N]" >&2; exit 64 ;;
  esac
done

REPORT_DIR="$(mktemp -d)"
# KEPT on a non-zero exit, and only then.  The trap used to be an
# unconditional `rm -rf`, so the JUnit reports this gate computes were
# deleted before anyone could read them: a FAIL printed a count and left
# nothing that says WHICH test failed.  On a transient failure - which is
# the case that most needs the report, because it may not reproduce -
# the evidence was gone by the time the verdict was on screen.  Measured
# once during #597: `main: 5078 executed, 1 failed` and no way to name
# the test.  Same defect class as the audit that found it (a measurement
# computed and then dropped), in the gate itself.
_keep_report_on_failure() {
  local status=$?
  if [ "${status}" -ne 0 ]; then
    echo
    echo "JUnit reports kept for diagnosis: ${REPORT_DIR}"
    echo "  (delete it yourself; a passing run cleans up after itself)"
  else
    rm -rf "${REPORT_DIR}"
  fi
}
trap _keep_report_on_failure EXIT

# Reads a JUnit report pytest wrote and prints `<state> <detail>`.
# Never prints `ok` unless the file exists, parses, counts more than zero
# tests, and records no failure and no error.
summarise() {
  local xml="$1" exit_code="$2"
  "${PYTHON}" - "$xml" "$exit_code" <<'PY' 2>/dev/null || echo "ran-nothing no readable JUnit report (the run produced no machine-readable result)"
import sys, xml.etree.ElementTree as ET, signal as _signal
path, exit_code = sys.argv[1], int(sys.argv[2])
try:
    root = ET.parse(path).getroot()
except Exception as exc:
    print(f"ran-nothing unreadable JUnit report ({exc.__class__.__name__})")
    raise SystemExit(0)
suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
if not suites:
    print("ran-nothing JUnit report contains no test suite")
    raise SystemExit(0)
n = sum(int(s.get("tests", 0)) for s in suites)
fail = sum(int(s.get("failures", 0)) for s in suites)
err = sum(int(s.get("errors", 0)) for s in suites)
skip = sum(int(s.get("skipped", 0)) for s in suites)
executed = n - skip
if n == 0:
    print("ran-nothing pytest collected 0 tests")
elif executed == 0:
    print(f"ran-nothing all {n} selected tests skipped, so nothing was measured")
elif fail or err:
    print(f"bad {executed} executed, {fail} failed, {err} errored")
elif exit_code != 0:
    # Exit codes >= 128 mean killed by a signal (exit = 128 + signum).
    # Distinguish this from a normal non-zero exit so the verdict names
    # the actual cause rather than a bare number.  The gate still fails -
    # a native crash is not acceptable - but the operator can now see
    # that the TESTS passed and the PROCESS died, and where to look.
    if exit_code > 128:
        signum = exit_code - 128
        try:
            signame = _signal.Signals(signum).name
        except (ValueError, AttributeError):
            signame = f"signal {signum}"
        print(
            f"crashed {executed} passed then NATIVE CRASH ({signame}) "
            f"- check ~/Library/Logs/DiagnosticReports/ for the faulting library"
        )
    else:
        print(f"bad {executed} executed with no recorded failure but pytest exited {exit_code}")
else:
    print(f"ok {executed} passed, {skip} skipped")
PY
}

# Extracts missing capabilities from a JUnit XML and prints one line per
# missing capability: `<name> <skip_count> <install_hint>`.
# Prints nothing when the environment is complete.
missing_caps() {
  local xml="$1"
  "${PYTHON}" - "$xml" <<'PY' 2>/dev/null
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(sys.argv[1]))))
# The XML is in a tmpdir; we need the repo root for the import.
repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) if '__file__' in dir() else None
# When run via heredoc, __file__ is not set.  Use REPO_ROOT from env.
repo = os.environ.get("REPO_ROOT", os.getcwd())
sys.path.insert(0, repo)
from tests.skip_audit import missing_capabilities_from_junit
for mc in missing_capabilities_from_junit(sys.argv[1]):
    print(f"{mc.name}\t{mc.skip_count}\t{mc.install_hint}")
PY
}

# Per-lane `executed / skipped` counts, printed every run.  Pool
# worktrees report 6 versus 72 skips for the same suite, so verdicts are
# not comparable between runs unless the counts are visible.
lane_counts() {
  local xml="$1"
  "${HELPER_PYTHON}" -m library.tools.junit_lanes counts "$xml" 2>/dev/null \
    || echo "no readable lane report"
}

# ---- phase 1: the CI-equivalent selection ------------------------------
# Parallel by default (two lanes, one merged report); `--no-parallel`
# runs the legacy single-process selection as the control.
MAIN_XML="${REPORT_DIR}/main.xml"
PARALLEL_XML="${REPORT_DIR}/parallel.xml"
SERIAL_XML="${REPORT_DIR}/serial.xml"
MERGED_XML="${REPORT_DIR}/merged.xml"

if [ "${PARALLEL}" -eq 1 ]; then
  echo "=== full-suite gate: parallel lanes (${PYTHON}, -n ${WORKERS}) ==="
  ROUTER_ERR="${REPORT_DIR}/router.err"
  ROUTER_OUT="${REPORT_DIR}/router.out"
  if "${HELPER_PYTHON}" -m library.tools.lane_routing tests \
      --serial-only >"${ROUTER_OUT}" 2>"${ROUTER_ERR}"; then
    # The boundary marks the Resolve drivers serial so any future runner
    # that stops ignoring them still routes them correctly - but THIS
    # gate ignores them by name (they drive the live app), so they run
    # in neither lane.
    SERIAL_FILES=()
    PARALLEL_IGNORES=()
    while IFS= read -r routed; do
      [ -z "${routed}" ] && continue
      PARALLEL_IGNORES+=("--ignore=${routed}")
      ignored=0
      for f in "${RESOLVE_DRIVING[@]}"; do
        if [ "${routed}" = "${f}" ]; then ignored=1; break; fi
      done
      [ "${ignored}" -eq 0 ] && SERIAL_FILES+=("${routed}")
    done <"${ROUTER_OUT}"
  else
    echo "WARNING: lane routing refused ($(<"${ROUTER_ERR}"))"
    echo "WARNING: running the whole selection serial rather than sharding it"
    SERIAL_FILES=()
    PARALLEL_IGNORES=()
    ROUTER_FAILED=1
  fi

  if [ "${ROUTER_FAILED:-0}" -eq 1 ]; then
    echo "=== full-suite gate: pytest -m 'not heavy_ml' (${PYTHON}) [router fallback] ==="
    "${PYTHON}" -m pytest tests/ -m "not heavy_ml" -rs --tb=short \
      "${RESOLVE_DRIVING[@]/#/--ignore=}" \
      --junitxml="${MAIN_XML}"
    MAIN_EXIT=$?
    PARALLEL_EXIT=0
    SERIAL_EXIT=0
    PARALLEL_RAN=0
    SERIAL_RAN=0
  else
    if [ "${#SERIAL_FILES[@]}" -gt 0 ]; then
      echo "serial lane: ${SERIAL_FILES[*]}"
    else
      echo "serial lane: empty (no file in this selection matches a serial clause)"
    fi
    PARALLEL_ERR="${REPORT_DIR}/parallel.err"
    echo "=== full-suite gate: parallel lane - pytest -n ${WORKERS} --dist loadfile ==="
    "${PYTHON}" -m pytest tests/ -m "not heavy_ml" -rs --tb=short \
      "${RESOLVE_DRIVING[@]/#/--ignore=}" \
      "${PARALLEL_IGNORES[@]:-}" \
      -n "${WORKERS}" --dist loadfile \
      --junitxml="${PARALLEL_XML}" 2>"${PARALLEL_ERR}"
    PARALLEL_EXIT=$?
    PARALLEL_RAN=1
    # No upfront xdist probe: the gate interpreter can be a shim speaking
    # only the pytest argv protocol.  Detect lazily - exit 4 with
    # unrecognized-arguments and no lane report refuses as DID NOT RUN,
    # naming xdist.
    XDIST_MISSING=0
    if [ "${PARALLEL_EXIT}" -eq 4 ] && [ ! -f "${PARALLEL_XML}" ] \
        && grep -qi "unrecognized arguments" "${PARALLEL_ERR}" 2>/dev/null; then
      XDIST_MISSING=1
      echo "parallel lane refused: $(grep -i "unrecognized arguments" "${PARALLEL_ERR}" | head -1)"
    fi
    SERIAL_EXIT=0
    SERIAL_RAN=0
    if [ "${#SERIAL_FILES[@]}" -gt 0 ]; then
      SERIAL_ERR="${REPORT_DIR}/serial.err"
      echo "=== full-suite gate: serial lane - single-process over ${#SERIAL_FILES[@]} file(s) ==="
      "${PYTHON}" -m pytest "${SERIAL_FILES[@]}" -m "not heavy_ml" -rs --tb=short \
        --junitxml="${SERIAL_XML}" 2>"${SERIAL_ERR}"
      SERIAL_EXIT=$?
      SERIAL_RAN=1
      # Exit 5 means nothing was collected: the serial files are all
      # deselected by this selection (today that is the heavy_ml tier,
      # which phase 2 runs separately).  A lane with nothing to run is
      # empty, not failed - but only when its own report says zero
      # tests; exit 5 with no report stays a failure.
      if [ "${SERIAL_EXIT}" -eq 5 ] \
          && [ "$("${HELPER_PYTHON}" -m library.tools.junit_lanes counts "${SERIAL_XML}" 2>/dev/null || true)" = "0 executed, 0 skipped" ]; then
        echo "serial lane collected 0 tests under -m 'not heavy_ml' (deselected) - counting it empty"
        SERIAL_EXIT=0
      fi
    fi
    # The merge refuses duplicate nodeids across lanes rather than
    # double-counting a test that ran in both.
    MERGE_INPUTS=()
    [ -f "${PARALLEL_XML}" ] && MERGE_INPUTS+=("${PARALLEL_XML}")
    [ -f "${SERIAL_XML}" ] && MERGE_INPUTS+=("${SERIAL_XML}")
    MERGE_DETAIL=""
    if [ "${XDIST_MISSING}" -eq 1 ]; then
      MAIN_RESULT="ran-nothing parallel lane needs pytest-xdist, which is not installed for ${PYTHON} (install: pip install -r requirements.txt)"
    elif [ "${#MERGE_INPUTS[@]}" -eq 0 ]; then
      MAIN_RESULT="ran-nothing no lane produced a JUnit report (the run produced no machine-readable result)"
    elif ! MERGE_DETAIL="$("${HELPER_PYTHON}" -m library.tools.junit_lanes merge --out "${MERGED_XML}" "${MERGE_INPUTS[@]}" 2>&1)"; then
      MAIN_RESULT="ran-nothing lane merge refused: ${MERGE_DETAIL}"
    else
      cp "${MERGED_XML}" "${MAIN_XML}"
      echo "merged report: ${MERGE_DETAIL}"
      # Lane exit codes reach the verdict: the 2026-09-12 shape carries
      # its failure in the EXIT CODE while the JUnit report is clean, so
      # reading only the merge turns that FAIL into a PASS.  Take the
      # first nonzero lane exit when the merged report itself is clean.
      EFFECTIVE_EXIT=0
      [ "${PARALLEL_RAN}" -eq 1 ] && [ "${PARALLEL_EXIT}" -ne 0 ] && EFFECTIVE_EXIT="${PARALLEL_EXIT}"
      [ "${EFFECTIVE_EXIT}" -eq 0 ] && [ "${SERIAL_RAN}" -eq 1 ] && [ "${SERIAL_EXIT}" -ne 0 ] && EFFECTIVE_EXIT="${SERIAL_EXIT}"
      MAIN_RESULT="$(summarise "${MAIN_XML}" "${EFFECTIVE_EXIT}")"
      [ -z "${MAIN_RESULT}" ] && MAIN_RESULT="ran-nothing the summariser itself produced no output"
      # The undeclared-skip hook goes blind under xdist (worker-local
      # findings the controller never sees), so re-derive the property
      # from the merged report and FAIL in the same direction.
      UNDECLARED_OUT="${REPORT_DIR}/undeclared.out"
      if ! "${HELPER_PYTHON}" -m library.tools.junit_lanes undeclared-skips "${MAIN_XML}" >"${UNDECLARED_OUT}" 2>&1; then
        UNDECLARED_N="$(grep -c "^SKIPPED (undeclared)" "${UNDECLARED_OUT}" || true)"
        cat "${UNDECLARED_OUT}"
        MAIN_RESULT="bad undeclared skips in the merged report (${UNDECLARED_N}), same direction as the serial hook"
      fi
    fi
  fi
else
  echo "=== full-suite gate: pytest -m 'not heavy_ml' (${PYTHON}) [serial control] ==="
  "${PYTHON}" -m pytest tests/ -m "not heavy_ml" -rs --tb=short \
    "${RESOLVE_DRIVING[@]/#/--ignore=}" \
    --junitxml="${MAIN_XML}"
  MAIN_EXIT=$?
  PARALLEL_EXIT=0
  SERIAL_EXIT=0
  PARALLEL_RAN=0
  SERIAL_RAN=0
  MAIN_RESULT="$(summarise "${MAIN_XML}" "${MAIN_EXIT}")"
  [ -z "${MAIN_RESULT}" ] && MAIN_RESULT="ran-nothing the summariser itself produced no output"
fi
MAIN_STATE="${MAIN_RESULT%% *}"
MAIN_DETAIL="${MAIN_RESULT#* }"

# Per-lane executed/skipped counts print every run, so verdicts stay
# comparable between runs and between lanes.
echo
echo "LANE COUNTS:"
if [ "${PARALLEL}" -eq 1 ] && [ "${ROUTER_FAILED:-0}" -eq 0 ]; then
  if [ "${PARALLEL_RAN}" -eq 1 ]; then
    echo "  parallel: $(lane_counts "${PARALLEL_XML}") (exit ${PARALLEL_EXIT})"
  else
    echo "  parallel: did not run"
  fi
  if [ "${SERIAL_RAN}" -eq 1 ]; then
    echo "  serial:   $(lane_counts "${SERIAL_XML}") (exit ${SERIAL_EXIT})"
  else
    echo "  serial:   empty - no file in this selection matched a serial clause"
  fi
else
  echo "  single-process: $(lane_counts "${MAIN_XML}") (exit ${MAIN_EXIT:-${EFFECTIVE_EXIT:-0}})"
fi

# Throttle-versus-race triage on FAIL, advisory only, never changing the
# verdict.  A rate-limit refusal names itself; a race does not.  A
# THROTTLE-LIKE failure needs a serial-lane re-run before anyone calls
# it a race.
if [ "${MAIN_STATE}" = "bad" ] || [ "${MAIN_STATE}" = "crashed" ]; then
  if [ -f "${MAIN_XML}" ]; then
    TRIAGE_OUT="$("${HELPER_PYTHON}" -m library.tools.junit_lanes triage "${MAIN_XML}" 2>/dev/null || true)"
    if [ -n "${TRIAGE_OUT}" ]; then
      echo
      echo "--------------------------------------------------------------------"
      echo "FAILURE TRIAGE (advisory - does not change the verdict):"
      echo "${TRIAGE_OUT}"
      echo "THROTTLE-LIKE needs a serial-lane re-run first; RACE-CANDIDATE"
      echo "is the concurrency hunt's set."
      echo "--------------------------------------------------------------------"
    fi
  fi
fi

# ---- phase 2: the heavy ML selection, local-only -----------------------
# The heavy_ml tests exist to prove a real measurement happened rather
# than a hollow file being written (AGENTS.md 10.3).  They therefore
# ASSERT on the measurement instead of skipping, so an interpreter
# without the dependency fails them for a reason that is nothing to do
# with the code.  That is a gate FAILING correct output, which AGENTS.md
# 10.4 says is no more coverage than a gate that cannot fail - and a
# permanently red gate is one everybody learns to ignore.
#
# So the tier is refused rather than mis-reported, and the verdict line
# NAMES the module that is missing.  This reports through the SAME
# capability mechanism as every other environment gap.
HEAVY_DEPS="parselmouth whisperx"
heavy_ml_is_runnable() {
  local missing=""
  for module in ${HEAVY_DEPS}; do
    "${PYTHON}" -c "import ${module}" >/dev/null 2>&1 || missing="${missing} ${module}"
  done
  [ -z "${missing}" ] && return 0
  echo "${missing# } not importable by ${PYTHON}"
  return 1
}

if [ "${RUN_HEAVY_ML}" -eq 1 ] && ! WHY="$(heavy_ml_is_runnable)"; then
  RUN_HEAVY_ML=0
  HEAVY_SKIP_REASON="${WHY}"
fi

if [ "${RUN_HEAVY_ML}" -eq 1 ]; then
  HEAVY_XML="${REPORT_DIR}/heavy.xml"
  echo
  echo "=== full-suite gate: pytest -m heavy_ml (${PYTHON}) ==="
  "${PYTHON}" -m pytest tests/ -m heavy_ml -rs --tb=short --junitxml="${HEAVY_XML}"
  HEAVY_EXIT=$?
  HEAVY_RESULT="$(summarise "${HEAVY_XML}" "${HEAVY_EXIT}")"
  [ -z "${HEAVY_RESULT}" ] && HEAVY_RESULT="ran-nothing the summariser itself produced no output"
else
  HEAVY_RESULT="ran-nothing ${HEAVY_SKIP_REASON:---skip-heavy-ml was passed}"
fi
HEAVY_STATE="${HEAVY_RESULT%% *}"
HEAVY_DETAIL="${HEAVY_RESULT#* }"

# ---- capability audit: what this run skipped ---------------------------
# Collect missing capabilities from both JUnit reports, plus the heavy_ml
# preflight.  A capability that is absent means the run is NARROWER than
# a full environment, and the verdict must say so.
NARROWED_CAPS=""
NARROWED_COUNT=0

# From main JUnit XML
if [ -f "${MAIN_XML}" ]; then
  while IFS=$'\t' read -r cap_name cap_count cap_hint; do
    [ -z "${cap_name}" ] && continue
    NARROWED_CAPS="${NARROWED_CAPS}  ${cap_name} (${cap_count} tests skipped) - install: ${cap_hint}"$'\n'
    NARROWED_COUNT=$((NARROWED_COUNT + 1))
  done < <(REPO_ROOT="${REPO_ROOT}" missing_caps "${MAIN_XML}")
fi

# From heavy JUnit XML
if [ "${RUN_HEAVY_ML}" -eq 1 ] && [ -f "${HEAVY_XML:-}" ]; then
  while IFS=$'\t' read -r cap_name cap_count cap_hint; do
    [ -z "${cap_name}" ] && continue
    NARROWED_CAPS="${NARROWED_CAPS}  ${cap_name} (${cap_count} tests skipped) - install: ${cap_hint}"$'\n'
    NARROWED_COUNT=$((NARROWED_COUNT + 1))
  done < <(REPO_ROOT="${REPO_ROOT}" missing_caps "${HEAVY_XML}")
fi

# The heavy_ml interpreter check is the SAME defect - a missing capability
# that narrows the run.  Report it through the same mechanism.
if [ "${RUN_HEAVY_ML}" -eq 0 ]; then
  NARROWED_CAPS="${NARROWED_CAPS}  heavy_ml (entire tier skipped) - install: ${HEAVY_SKIP_REASON:-set FULL_SUITE_GATE_PYTHON to an interpreter with the ML stack}"$'\n'
  NARROWED_COUNT=$((NARROWED_COUNT + 1))
fi

# ---- the verdict -------------------------------------------------------
# Fail-closed: DID NOT RUN unless phase 1 demonstrably measured something.
case "${MAIN_STATE}" in
  ok)  VERDICT="PASS" ;;
  crashed) VERDICT="FAIL" ;;
  bad) VERDICT="FAIL" ;;
  *)   VERDICT="DID NOT RUN" ;;
esac

case "${HEAVY_STATE}" in
  ok)  HEAVY_NOTE="heavy_ml ${HEAVY_DETAIL}" ;;
  crashed) HEAVY_NOTE="heavy_ml CRASHED - ${HEAVY_DETAIL}"; [ "${VERDICT}" = "PASS" ] && VERDICT="FAIL" ;;
  bad) HEAVY_NOTE="heavy_ml FAILED - ${HEAVY_DETAIL}"; [ "${VERDICT}" = "PASS" ] && VERDICT="FAIL" ;;
  *)   HEAVY_NOTE="heavy_ml NOT MEASURED - ${HEAVY_DETAIL}" ;;
esac

# A PASS with missing capabilities is a NARROWED PASS, not PASS.
if [ "${VERDICT}" = "PASS" ] && [ "${NARROWED_COUNT}" -gt 0 ]; then
  VERDICT="NARROWED PASS"
fi

echo
echo "--------------------------------------------------------------------"
echo "not measured by this gate, deliberately:"
for f in "${RESOLVE_DRIVING[@]}"; do
  echo "  ${f}  (drives the running DaVinci Resolve)"
done
echo "--------------------------------------------------------------------"

if [ "${NARROWED_COUNT}" -gt 0 ]; then
  echo
  echo "--------------------------------------------------------------------"
  echo "MISSING CAPABILITIES (${NARROWED_COUNT}):"
  echo "The following environment capabilities were absent.  Tests that"
  echo "depend on them were skipped, so this run measured LESS than a"
  echo "full environment.  The verdict is NARROWED PASS, not PASS."
  echo ""
  printf '%s' "${NARROWED_CAPS}"
  echo "--------------------------------------------------------------------"
fi

echo "FULL-SUITE GATE: ${VERDICT}  |  main: ${MAIN_DETAIL}  |  ${HEAVY_NOTE}"

[ "${VERDICT}" = "PASS" ] && exit 0
exit 1
