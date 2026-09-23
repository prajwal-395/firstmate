"""The sharded gate keeps the verdict contract.

A sharded run must not print a verdict a serial run could not: same
vocabulary (PASS / NARROWED PASS / FAIL / DID NOT RUN), same failure
directions.  These tests pin the lane-merge plumbing at the Python
level and the two load-bearing gate behaviours at the script level -
lane exit codes reaching the verdict, and the lazy xdist refusal -
using interpreter shims, never a suite run.
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.tools import junit_lanes  # noqa: E402

LOCAL_GATE = REPO_ROOT / "scripts" / "full_suite_gate.sh"


def _write_xml(path: Path, cases: str, *, tests: int, skipped: int = 0,
               failures: int = 0, errors: int = 0) -> str:
    path.write_text(textwrap.dedent(f"""\
        <?xml version="1.0" encoding="utf-8"?>
        <testsuites>
          <testsuite name="pytest" tests="{tests}" skipped="{skipped}"
                     failures="{failures}" errors="{errors}">
        {cases}
          </testsuite>
        </testsuites>
    """), encoding="utf-8")
    return str(path)


class TestMerge:
    def test_disjoint_lanes_merge(self, tmp_path):
        a = _write_xml(
            tmp_path / "a.xml",
            '<testcase name="t1" classname="m"/><testcase name="t2" classname="m"/>',
            tests=2)
        b = _write_xml(
            tmp_path / "b.xml",
            '<testcase name="t3" classname="m"/>',
            tests=1)
        out = str(tmp_path / "m.xml")
        ok, detail = junit_lanes.merge_reports([a, b], out)
        assert ok, detail
        counts = junit_lanes.read_counts(out)
        assert counts is not None
        assert (counts.tests, counts.executed, counts.skipped) == (3, 3, 0)

    def test_duplicate_nodeid_refuses(self, tmp_path):
        a = _write_xml(
            tmp_path / "a.xml",
            '<testcase name="t1" classname="m"/>', tests=1)
        b = _write_xml(
            tmp_path / "b.xml",
            '<testcase name="t1" classname="m"/>', tests=1)
        ok, detail = junit_lanes.merge_reports(
            [a, b], str(tmp_path / "m.xml"))
        assert not ok
        assert "duplicate nodeid" in detail
        assert "both lanes" in detail

    def test_zero_tests_refuses(self, tmp_path):
        a = _write_xml(tmp_path / "a.xml", "", tests=0)
        ok, detail = junit_lanes.merge_reports([a], str(tmp_path / "m.xml"))
        assert not ok
        assert "0 tests" in detail

    def test_missing_input_refuses(self, tmp_path):
        ok, _ = junit_lanes.merge_reports(
            [str(tmp_path / "gone.xml")], str(tmp_path / "m.xml"))
        assert not ok


class TestCounts:
    def test_executed_is_tests_minus_skipped(self, tmp_path):
        xml = _write_xml(
            tmp_path / "r.xml",
            '<testcase name="t1" classname="m"/>'
            '<testcase name="t2" classname="m">'
            '<skipped message="needs remotion-subtitles/node_modules, npx and ffmpeg"/>'
            "</testcase>",
            tests=2, skipped=1)
        counts = junit_lanes.read_counts(xml)
        assert counts is not None
        assert counts.executed == 1
        assert counts.skipped == 1

    def test_unreadable_is_none(self, tmp_path):
        assert junit_lanes.read_counts(str(tmp_path / "gone.xml")) is None


class TestUndeclaredSkipsRederived:
    def test_declared_skip_passes(self, tmp_path):
        xml = _write_xml(
            tmp_path / "r.xml",
            '<testcase name="t1" classname="m">'
            '<skipped message="needs remotion-subtitles/node_modules, npx and ffmpeg"/>'
            "</testcase>",
            tests=1, skipped=1)
        assert junit_lanes.undeclared_skips(xml) == []

    def test_undeclared_skip_fails(self, tmp_path):
        xml = _write_xml(
            tmp_path / "r.xml",
            '<testcase name="t1" classname="m">'
            '<skipped message="a brand-new reason nobody declared"/>'
            "</testcase>",
            tests=1, skipped=1)
        found = junit_lanes.undeclared_skips(xml)
        assert len(found) == 1
        assert found[0][0] == "m::t1"


class TestTriageIsAdvisory:
    def test_race_candidate(self, tmp_path):
        xml = _write_xml(
            tmp_path / "r.xml",
            '<testcase name="t1" classname="m">'
            '<failure message="assert False">assert False</failure>'
            "</testcase>",
            tests=1, failures=1)
        triaged = junit_lanes.triage_failures(xml)
        assert [cls for _, cls, _ in triaged] == ["RACE-CANDIDATE"]

    def test_throttle_like_names_itself(self, tmp_path):
        xml = _write_xml(
            tmp_path / "r.xml",
            '<testcase name="t1" classname="m">'
            '<failure message="429 Too Many Requests">quota exceeded</failure>'
            "</testcase>",
            tests=1, failures=1)
        triaged = junit_lanes.triage_failures(xml)
        assert [cls for _, cls, _ in triaged] == ["THROTTLE-LIKE"]

    def test_passing_report_triages_nothing(self, tmp_path):
        xml = _write_xml(
            tmp_path / "r.xml",
            '<testcase name="t1" classname="m"/>', tests=1)
        assert junit_lanes.triage_failures(xml) == []


def _run_gate(python_shim: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(LOCAL_GATE), *args],
        capture_output=True,
        encoding="utf-8",
        check=False,
        cwd=str(REPO_ROOT),
        env={**os.environ, "FULL_SUITE_GATE_PYTHON": python_shim},
    )


def _verdict(result: subprocess.CompletedProcess) -> str:
    verdicts = [
        line for line in result.stdout.splitlines()
        if line.startswith("FULL-SUITE GATE:")
    ]
    assert len(verdicts) == 1, (
        f"the gate must print exactly one verdict line; got {verdicts}\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    return verdicts[0]


class TestLaneExitsReachTheVerdict:
    """Acceptance criterion 1 at the script level: a clean merged
    report with a dirty lane exit is FAIL, the 2026-09-12 shape."""

    @pytest.mark.heavy
    def test_clean_lane_reports_with_dirty_exits_fail(self, tmp_path):
        shim = tmp_path / "lanes_clean_report_dirty_exit.sh"
        shim.write_text(textwrap.dedent("""\
            #!/bin/sh
            # A lane-aware stand-in: parallel invocations carry -n, the
            # serial lane does not, so each lane writes DISTINCT nodeids
            # (as real lanes would) with a clean report and a dirty exit.
            lane=serial
            for a in "$@"; do
              case "$a" in
                -n) lane=parallel ;;
                --junitxml=*) out="${a#--junitxml=}" ;;
              esac
            done
            # No --junitxml: not a lane run (the gate's own accounting
            # runs through ${PYTHON} too) - fall through to real python.
            if [ -z "${out:-}" ]; then exec python3 "$@"; fi
            cat > "$out" <<XML
            <?xml version="1.0" encoding="utf-8"?>
            <testsuites>
              <testsuite name="pytest" tests="2" skipped="0" failures="0" errors="0">
                <testcase name="test_${lane}_a" classname="m"/>
                <testcase name="test_${lane}_b" classname="m"/>
              </testsuite>
            </testsuites>
            XML
            exit 1
            """), encoding="utf-8")
        shim.chmod(0o755)
        result = _run_gate(str(shim), "--skip-heavy-ml")
        verdict = _verdict(result)
        assert verdict.startswith("FULL-SUITE GATE: FAIL"), verdict
        assert "no recorded failure but pytest exited 1" in verdict, verdict
        assert result.returncode != 0


class TestLazyXdistDetect:
    """Acceptance criterion 2 at the script level: no upfront probe -
    exit 4 with unrecognized-arguments and no lane report refuses as
    DID NOT RUN, naming xdist."""

    @pytest.mark.heavy
    def test_missing_xdist_is_did_not_run(self, tmp_path):
        shim = tmp_path / "no_xdist.sh"
        shim.write_text(textwrap.dedent("""\
            #!/bin/sh
            # pytest without xdist: -n/--dist are unrecognized, exit 4,
            # usage error before collection, so no JUnit report.
            for a in "$@"; do
              case "$a" in
                --junitxml=*) out="${a#--junitxml=}" ;;
              esac
            done
            case " $* " in
              *" -n "*) echo "$0: error: unrecognized arguments: -n --dist" >&2; exit 4 ;;
            esac
            cat > "$out" <<'XML'
            <?xml version="1.0" encoding="utf-8"?>
            <testsuites>
              <testsuite name="pytest" tests="1" skipped="0" failures="0" errors="0">
                <testcase name="test_s" classname="m"/>
              </testsuite>
            </testsuites>
            XML
            exit 0
            """), encoding="utf-8")
        shim.chmod(0o755)
        result = _run_gate(str(shim), "--skip-heavy-ml")
        verdict = _verdict(result)
        assert "DID NOT RUN" in verdict, verdict
        assert "xdist" in result.stdout, result.stdout
        assert result.returncode != 0
