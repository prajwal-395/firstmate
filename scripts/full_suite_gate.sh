#!/usr/bin/env bash
#
# LAYER 1 of the three-layer CI design: the full test suite, run LOCALLY,
# once, on the integrated branch, before a batch merges.  See
# `docs/CI_LAYERS.md` and the header of `.github/workflows/ci.yml`.
#
#     scripts/full_suite_gate.sh                  # the gate (parallel lanes)
#     scripts/full_suite_gate.sh --no-parallel    # serial control, same commit
#     scripts/full_suite_gate.sh --skip-real-model  # without real ML measurement
#
# Runtime is telemetry, never a test category. The semantic categories
# are unit, scenario, resolve_live, and real_model. Timing from the last
# complete run only orders files for xdist sharding.
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
# TWO SELECTIONS, SHARED LANES, ONE VERDICT. Unit and scenario tests run
# together, sharded by last-run file timings. Real-model tests run in a
# separate capability-gated process. Resolve-live tests are listed and
# excluded because they control the captain's open Resolve instance.
# The boundary is executable code run fresh on every invocation
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
# The `real_model` selection is deliberately local: those tests exercise
# actual ML dependencies. If this interpreter cannot run them, the
# verdict says so through the same capability mechanism as other gaps.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "${REPO_ROOT}"

# Every lane that can contend with local model inference, render or Resolve
# work uses the same machine lock. The wrapper owns the lock for the gate's
# full lifetime and forwards termination signals to its child. When a lane
# already holds it, the inherited owner token makes this check succeed and
# the gate runs reentrantly under that existing acquisition.
if ! python3 -m library.tools.heavy_work_lock owns; then
  exec python3 -m library.tools.heavy_work_lock run \
    --owner "scripts/full_suite_gate.sh" -- bash "$0" "$@"
fi

# `FULL_SUITE_GATE_PYTHON` still wins.  Without it, ASK the resolution
# rather than taking the ambient `python3`: on this machine that is 3.14
# with none of the ML stack, so real-model qualification went unmeasured unless
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
# argv protocol (tests/test_gate_smoke.py), which refuses runs the
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

RUN_REAL_MODEL=1
PARALLEL=1
WORKERS="auto"
while [ $# -gt 0 ]; do
  case "${1:-}" in
    --skip-real-model|--skip-heavy-ml) RUN_REAL_MODEL=0; shift ;;
    --no-parallel) PARALLEL=0; shift ;;
    -n|--workers) WORKERS="${2:?missing worker count}"; shift 2 ;;
    --workers=*) WORKERS="${1#--workers=}"; shift ;;
    --help|-h) sed -n '2,20p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "usage: $0 [--skip-real-model] [--no-parallel] [-n N | --workers N]" >&2; exit 64 ;;
  esac
done

# A fresh worktree does not carry the ignored `node_modules` bind from the
# checkout that populated the machine-wide cache. Rebind it before pytest so
# capability-gated Remotion tests run in worker copies too. The installer is
# lockfile-keyed and idempotent: a warm machine only links the existing tree;
# the first run for a new lockfile fills the shared store once. A bootstrap
# failure does not change gate semantics - pytest and the capability audit
# below still decide whether this run is narrowed.
if command -v node >/dev/null 2>&1; then
  echo "=== full-suite gate: bind Remotion dependencies ==="
  if ! INSTALL_NODE_DEPS_PYTHON="${HELPER_PYTHON}" \
      "${REPO_ROOT}/scripts/install_node_deps.sh"; then
    echo "Remotion dependency bootstrap failed; the capability audit will report any affected skips." >&2
  fi
fi

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

# ---- routing: which files run serial -----------------------------------
# File-level and marker-agnostic, so it is computed ONCE and shared by the
# unit/scenario and real-model phases below: a file routes serial for what
# its own code does (library/tools/lane_routing.py), whatever `-m` a phase
# selects inside it.
SERIAL_FILES=()
PARALLEL_IGNORES=()
ROUTER_FAILED=0
if [ "${PARALLEL}" -eq 1 ]; then
  echo "=== full-suite gate: lane routing (${HELPER_PYTHON} -m library.tools.lane_routing) ==="
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
fi

# Runs one marker selection through the two lanes (parallel + serial, or
# the single-process selection under --no-parallel / router failure) and
# leaves the verdict inputs in PHASE_STATE / PHASE_DETAIL.  Both sharded
# Unit and scenario phases go through here, so a
# lane that silently drops tests, a dirty lane exit, or an undeclared
# skip fails every phase the same way.
#
#   $1 = tag naming the phase in logs and scratch files (main)
#   $2 = the pytest -m expression this phase runs
#   $3 = path of this phase's merged JUnit report
run_sharded_phase() {
  local tag="$1" marker="$2" xml="$3"
  local parallel_xml="${REPORT_DIR}/${tag}.parallel.xml"
  local serial_xml="${REPORT_DIR}/${tag}.serial.xml"
  local merged_xml="${REPORT_DIR}/${tag}.merged.xml"
  local phase_result=""
  local main_exit=0 parallel_exit=0 serial_exit=0
  local parallel_ran=0 serial_ran=0 effective_exit=0
  local merge_detail="" undeclared_n="" undeclared_out="" triage_out=""

  if [ "${PARALLEL}" -eq 1 ] && [ "${ROUTER_FAILED:-0}" -eq 0 ]; then
    if [ "${#SERIAL_FILES[@]}" -gt 0 ]; then
      echo "[$tag] serial lane: ${SERIAL_FILES[*]}"
    else
      echo "[$tag] serial lane: empty (no file matches a serial clause)"
    fi
    local parallel_err="${REPORT_DIR}/${tag}.parallel.err"
    echo "=== full-suite gate [$tag]: parallel lane - pytest -n ${WORKERS} --dist loadfile -m '${marker}' (${PYTHON}) ==="
    "${PYTHON}" -m pytest tests/ -p scripts.pytest_timing --timing-shard \
      -m "${marker}" -rs --tb=short \
      "${RESOLVE_DRIVING[@]/#/--ignore=}" \
      "${PARALLEL_IGNORES[@]:-}" \
      -n "${WORKERS}" --dist loadfile \
      --junitxml="${parallel_xml}" 2>"${parallel_err}"
    parallel_exit=$?
    parallel_ran=1
    # No upfront xdist probe: the gate interpreter can be a shim speaking
    # only the pytest argv protocol.  Detect lazily - exit 4 with
    # unrecognized-arguments and no lane report refuses as DID NOT RUN,
    # naming xdist.
    local xdist_missing=0
    if [ "${parallel_exit}" -eq 4 ] && [ ! -f "${parallel_xml}" ] \
        && grep -qi "unrecognized arguments" "${parallel_err}" 2>/dev/null; then
      xdist_missing=1
      echo "[$tag] parallel lane refused: $(grep -i "unrecognized arguments" "${parallel_err}" | head -1)"
    fi
    serial_exit=0
    serial_ran=0
    if [ "${#SERIAL_FILES[@]}" -gt 0 ]; then
      local serial_err="${REPORT_DIR}/${tag}.serial.err"
      echo "=== full-suite gate [$tag]: serial lane - single-process over ${#SERIAL_FILES[@]} file(s) -m '${marker}' ==="
      "${PYTHON}" -m pytest "${SERIAL_FILES[@]}" -p scripts.pytest_timing \
        -m "${marker}" -rs --tb=short \
        --junitxml="${serial_xml}" 2>"${serial_err}"
      serial_exit=$?
      serial_ran=1
      # Exit 5 means nothing was collected: the serial files are all
      # deselected by this selection (the real_model file under the main
      # selection). A lane
      # with nothing to run is empty, not failed - but only when its own
      # report says zero tests; exit 5 with no report stays a failure.
      if [ "${serial_exit}" -eq 5 ] \
          && [ "$("${HELPER_PYTHON}" -m library.tools.junit_lanes counts "${serial_xml}" 2>/dev/null || true)" = "0 executed, 0 skipped" ]; then
        echo "[$tag] serial lane collected 0 tests under -m '${marker}' (deselected) - counting it empty"
        serial_exit=0
      fi
    fi
    # The merge refuses duplicate nodeids across lanes rather than
    # double-counting a test that ran in both.
    local merge_inputs=()
    [ -f "${parallel_xml}" ] && merge_inputs+=("${parallel_xml}")
    [ -f "${serial_xml}" ] && merge_inputs+=("${serial_xml}")
    merge_detail=""
    if [ "${xdist_missing}" -eq 1 ]; then
      phase_result="ran-nothing parallel lane needs pytest-xdist, which is not installed for ${PYTHON} (install: pip install -r requirements.txt)"
    elif [ "${#merge_inputs[@]}" -eq 0 ]; then
      phase_result="ran-nothing no lane produced a JUnit report (the run produced no machine-readable result)"
    elif ! merge_detail="$("${HELPER_PYTHON}" -m library.tools.junit_lanes merge --out "${merged_xml}" "${merge_inputs[@]}" 2>&1)"; then
      phase_result="ran-nothing lane merge refused: ${merge_detail}"
    else
      cp "${merged_xml}" "${xml}"
      echo "[$tag] merged report: ${merge_detail}"
      # Lane exit codes reach the verdict: the 2026-09-12 shape carries
      # its failure in the EXIT CODE while the JUnit report is clean, so
      # reading only the merge turns that FAIL into a PASS.  Take the
      # first nonzero lane exit when the merged report itself is clean.
      effective_exit=0
      [ "${parallel_ran}" -eq 1 ] && [ "${parallel_exit}" -ne 0 ] && effective_exit="${parallel_exit}"
      [ "${effective_exit}" -eq 0 ] && [ "${serial_ran}" -eq 1 ] && [ "${serial_exit}" -ne 0 ] && effective_exit="${serial_exit}"
      phase_result="$(summarise "${xml}" "${effective_exit}")"
      [ -z "${phase_result}" ] && phase_result="ran-nothing the summariser itself produced no output"
      # The undeclared-skip hook goes blind under xdist (worker-local
      # findings the controller never sees), so re-derive the property
      # from the merged report and FAIL in the same direction.
      local undeclared_out="${REPORT_DIR}/${tag}.undeclared.out"
      if ! "${HELPER_PYTHON}" -m library.tools.junit_lanes undeclared-skips "${xml}" >"${undeclared_out}" 2>&1; then
        undeclared_n="$(grep -c "^SKIPPED (undeclared)" "${undeclared_out}" || true)"
        cat "${undeclared_out}"
        phase_result="bad undeclared skips in the merged report (${undeclared_n}), same direction as the serial hook"
      fi
    fi
  else
    if [ "${ROUTER_FAILED:-0}" -eq 1 ]; then
      echo "=== full-suite gate [$tag]: pytest -m '${marker}' (${PYTHON}) [router fallback] ==="
    else
      echo "=== full-suite gate [$tag]: pytest -m '${marker}' (${PYTHON}) [serial control] ==="
    fi
    "${PYTHON}" -m pytest tests/ -m "${marker}" -rs --tb=short \
    "${RESOLVE_DRIVING[@]/#/--ignore=}" \
      --junitxml="${xml}"
    main_exit=$?
    phase_result="$(summarise "${xml}" "${main_exit}")"
    [ -z "${phase_result}" ] && phase_result="ran-nothing the summariser itself produced no output"
    if [ -f "${xml}" ]; then
      local single_undeclared_out="${REPORT_DIR}/${tag}.single.undeclared.out"
      if ! "${HELPER_PYTHON}" -m library.tools.junit_lanes undeclared-skips \
          "${xml}" >"${single_undeclared_out}" 2>&1; then
        local single_undeclared_n
        single_undeclared_n="$(grep -c "^SKIPPED (undeclared)" "${single_undeclared_out}" || true)"
        cat "${single_undeclared_out}"
        phase_result="bad undeclared skips in the report (${single_undeclared_n})"
      fi
    fi
  fi
  PHASE_STATE="${phase_result%% *}"
  PHASE_DETAIL="${phase_result#* }"

  # Per-lane executed/skipped counts print every run, so verdicts stay
  # comparable between runs and between lanes.
  echo
  echo "LANE COUNTS [$tag]:"
  if [ "${PARALLEL}" -eq 1 ] && [ "${ROUTER_FAILED:-0}" -eq 0 ]; then
    if [ "${parallel_ran}" -eq 1 ]; then
      echo "  parallel: $(lane_counts "${parallel_xml}") (exit ${parallel_exit})"
    else
      echo "  parallel: did not run"
    fi
    if [ "${serial_ran}" -eq 1 ]; then
      echo "  serial:   $(lane_counts "${serial_xml}") (exit ${serial_exit})"
    else
      echo "  serial:   empty - no file matched a serial clause"
    fi
  else
    echo "  single-process: $(lane_counts "${xml}") (exit ${main_exit})"
  fi

  # Throttle-versus-race triage on FAIL, advisory only, never changing the
  # verdict.  A rate-limit refusal names itself; a race does not.  A
  # THROTTLE-LIKE failure needs a serial-lane re-run before anyone calls
  # it a race.
  if [ "${PHASE_STATE}" = "bad" ] || [ "${PHASE_STATE}" = "crashed" ]; then
    if [ -f "${xml}" ]; then
      triage_out="$("${HELPER_PYTHON}" -m library.tools.junit_lanes triage "${xml}" 2>/dev/null || true)"
      if [ -n "${triage_out}" ]; then
        echo
        echo "--------------------------------------------------------------------"
        echo "FAILURE TRIAGE [$tag] (advisory - does not change the verdict):"
        echo "${triage_out}"
        echo "THROTTLE-LIKE needs a serial-lane re-run first; RACE-CANDIDATE"
        echo "is the concurrency hunt's set."
        echo "--------------------------------------------------------------------"
      fi
    fi
  fi
}

# ---- phase 1: unit and scenario tests ----------------------------------
# Timing never changes membership. Unit and scenario tests both run on
# every full gate; previous timings only order whole files across workers.
MAIN_XML="${REPORT_DIR}/main.xml"
run_sharded_phase "main" "not real_model and not resolve_live" "${MAIN_XML}"
MAIN_STATE="${PHASE_STATE}"
MAIN_DETAIL="${PHASE_DETAIL}"

# ---- phase 2: real-model qualification, local-only ---------------------
# The real_model tests prove an actual measurement happened rather
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
REAL_MODEL_DEPS="parselmouth"
real_model_is_runnable() {
  local missing=""
  for module in ${REAL_MODEL_DEPS}; do
    "${PYTHON}" -c "import ${module}" >/dev/null 2>&1 || missing="${missing} ${module}"
  done
  [ -z "${missing}" ] && return 0
  echo "${missing# } not importable by ${PYTHON}"
  return 1
}

if [ "${RUN_REAL_MODEL}" -eq 1 ] && ! WHY="$(real_model_is_runnable)"; then
  RUN_REAL_MODEL=0
  REAL_MODEL_SKIP_REASON="${WHY}"
fi

if [ "${RUN_REAL_MODEL}" -eq 1 ]; then
  REAL_MODEL_XML="${REPORT_DIR}/real_model.xml"
  echo
  echo "=== full-suite gate: pytest -m real_model (${PYTHON}) ==="
  "${PYTHON}" -m pytest tests/ -p scripts.pytest_timing \
    -m real_model -rs --tb=short --junitxml="${REAL_MODEL_XML}"
  REAL_MODEL_EXIT=$?
  REAL_MODEL_RESULT="$(summarise "${REAL_MODEL_XML}" "${REAL_MODEL_EXIT}")"
  [ -z "${REAL_MODEL_RESULT}" ] && REAL_MODEL_RESULT="ran-nothing the summariser itself produced no output"
else
  REAL_MODEL_RESULT="ran-nothing ${REAL_MODEL_SKIP_REASON:---skip-real-model was passed}"
fi
REAL_MODEL_STATE="${REAL_MODEL_RESULT%% *}"
REAL_MODEL_DETAIL="${REAL_MODEL_RESULT#* }"

# ---- capability audit: what this run skipped ---------------------------
# A missing capability means this run measured less than the full
# available environment. Report it in the same vocabulary as the skips.
NARROWED_CAPS=""
NARROWED_COUNT=0
for report in "${MAIN_XML}" "${REAL_MODEL_XML:-}"; do
  [ -f "${report}" ] || continue
  while IFS=$'\t' read -r cap_name cap_count cap_hint; do
    [ -z "${cap_name}" ] && continue
    NARROWED_CAPS="${NARROWED_CAPS}  ${cap_name} (${cap_count} tests skipped) - install: ${cap_hint}"$'\n'
    NARROWED_COUNT=$((NARROWED_COUNT + 1))
  done < <(REPO_ROOT="${REPO_ROOT}" missing_caps "${report}")
done

if [ "${RUN_REAL_MODEL}" -eq 0 ]; then
  NARROWED_CAPS="${NARROWED_CAPS}  real_model (category not measured) - install: ${REAL_MODEL_SKIP_REASON:---skip-real-model was passed}"$'\n'
  NARROWED_COUNT=$((NARROWED_COUNT + 1))
elif [ "${REAL_MODEL_STATE}" != "ok" ] \
    && [ "${REAL_MODEL_STATE}" != "bad" ] \
    && [ "${REAL_MODEL_STATE}" != "crashed" ]; then
  NARROWED_CAPS="${NARROWED_CAPS}  real_model (category not measured) - ${REAL_MODEL_DETAIL}"$'\n'
  NARROWED_COUNT=$((NARROWED_COUNT + 1))
fi

# ---- the verdict -------------------------------------------------------
# Fail closed unless the main selection demonstrably ran and the real
# model category either ran or narrowed the verdict.
case "${MAIN_STATE}" in
  ok) VERDICT="PASS" ;;
  bad|crashed) VERDICT="FAIL" ;;
  *) VERDICT="DID NOT RUN" ;;
esac
case "${REAL_MODEL_STATE}" in
  ok) REAL_MODEL_NOTE="real_model ${REAL_MODEL_DETAIL}" ;;
  bad) REAL_MODEL_NOTE="real_model FAILED - ${REAL_MODEL_DETAIL}"; [ "${VERDICT}" = "PASS" ] && VERDICT="FAIL" ;;
  crashed) REAL_MODEL_NOTE="real_model CRASHED - ${REAL_MODEL_DETAIL}"; [ "${VERDICT}" = "PASS" ] && VERDICT="FAIL" ;;
  *) REAL_MODEL_NOTE="real_model NOT MEASURED - ${REAL_MODEL_DETAIL}" ;;
esac
if [ "${VERDICT}" = "PASS" ] && [ "${NARROWED_COUNT}" -gt 0 ]; then
  VERDICT="NARROWED PASS"
fi

if [ "${VERDICT}" = "PASS" ]; then
  if ! "${HELPER_PYTHON}" "${REPO_ROOT}/scripts/pytest_timing.py" update \
      "${MAIN_XML}" "${REAL_MODEL_XML}"; then
    echo "timing telemetry not updated from this full run" >&2
  fi
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
  echo "The following environment capabilities were absent or not measured."
  echo "The verdict is NARROWED PASS, not PASS."
  echo ""
  printf '%s' "${NARROWED_CAPS}"
  echo "--------------------------------------------------------------------"
fi

echo "FULL-SUITE GATE: ${VERDICT}  |  unit+scenario: ${MAIN_DETAIL}  |  ${REAL_MODEL_NOTE}"

[ "${VERDICT}" = "PASS" ] && exit 0
exit 1
