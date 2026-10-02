"""Lane-report plumbing for the parallel full-suite gate.

The gate runs the unit and scenario selection in two lanes (parallel over
xdist, serial single-process) and merges the lane JUnit XMLs into ONE
report that the existing verdict logic reads unchanged.  Four helpers,
each executable from the gate script:

- ``merge``: combine lane XMLs, REFUSING duplicate nodeids across lanes.
  A test that ran in both lanes is a routing defect, not coverage, so
  the merge fails rather than double-counting it.
- ``counts``: per-lane ``executed / skipped`` counts, printed every run.
  Pool worktrees report 6 versus 72 skips for the same suite, so
  verdicts are not comparable between runs unless the counts are
  visible.
- ``undeclared-skips``: re-derive the root conftest's undeclared-skip
  property from the MERGED report.  Under xdist the hook's worker-local
  finding list never reaches the controller, so the hook silently
  passes in parallel; every ``<skipped message>`` must match a
  ``tests/skip_audit.py`` declaration, and any miss FAILS in the same
  direction as the serial hook.
- ``triage``: throttle-versus-race triage on FAIL, advisory only, never
  changing the verdict.  A rate-limit refusal names itself; a race does
  not.

Stdlib plus ``tests.skip_audit`` only, so the gate can run this with
the resolving ``python3`` rather than the gate interpreter.
"""

from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests import skip_audit

# A rate-limit refusal names itself; a race does not.  Keep the throttle
# patterns narrow (numeric codes and explicit quota language) so a
# genuine race is never mislabelled as throttling.
THROTTLE_PATTERNS = (
    r"429",
    r"rate.?limit",
    r"quota",
    r"retry-after",
    r"Retry-After",
    r"model.?server (timeout|timed out|refus)",
    r"GEMMA SERVER (timeout|timed out|refused|unavailable)",
    r"temporarily unavailable",
    r"service unavailable",
    r"too many requests",
)
_THROTTLE_RE = re.compile("|".join(THROTTLE_PATTERNS), re.IGNORECASE)


@dataclass(frozen=True)
class LaneCounts:
    tests: int
    failures: int
    errors: int
    skipped: int

    @property
    def executed(self) -> int:
        return self.tests - self.skipped


def _suites(root: ET.Element) -> list[ET.Element]:
    if root.tag == "testsuite":
        return [root]
    return list(root.iter("testsuite"))


def read_counts(xml_path: str) -> LaneCounts | None:
    """Executed/skipped counts from one lane XML, or None when unreadable."""
    try:
        root = ET.parse(xml_path).getroot()
    except (ET.ParseError, OSError):
        return None
    suites = _suites(root)
    if not suites:
        return None
    return LaneCounts(
        tests=sum(int(s.get("tests", 0)) for s in suites),
        failures=sum(int(s.get("failures", 0)) for s in suites),
        errors=sum(int(s.get("errors", 0)) for s in suites),
        skipped=sum(int(s.get("skipped", 0)) for s in suites),
    )


def _case_nodeids(root: ET.Element) -> list[tuple[str, str]]:
    """(classname, name) for every testcase in a report."""
    ids = []
    for case in root.iter("testcase"):
        ids.append((
            case.get("classname", ""),
            case.get("name", ""),
        ))
    return ids


def merge_reports(inputs: list[str], output: str) -> tuple[bool, str]:
    """Merge lane XMLs into one report.

    Returns (ok, detail).  Refuses when no input parses, when the merge
    holds zero tests, or when a nodeid appears in more than one lane.
    """
    parsed: list[ET.Element] = []
    for path in inputs:
        try:
            parsed.append(ET.parse(path).getroot())
        except (ET.ParseError, OSError) as exc:
            return False, f"{path} did not parse ({exc.__class__.__name__})"
    if not parsed:
        return False, "no lane report to merge"

    seen: dict[tuple[str, str], str] = {}
    merged = ET.Element("testsuites")
    suite = ET.SubElement(merged, "testsuite", name="merged")
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for path, root in zip(inputs, parsed):
        for lane_suite in _suites(root):
            for key in totals:
                totals[key] += int(lane_suite.get(key, 0) or 0)
            for case in lane_suite.iter("testcase"):
                nodeid = (case.get("classname", ""), case.get("name", ""))
                if nodeid in seen:
                    return False, (
                        f"duplicate nodeid {nodeid[0]}::{nodeid[1]} "
                        f"in {seen[nodeid]} and {path} - a test ran in "
                        f"both lanes, so the merge refuses rather than "
                        f"double-counting it"
                    )
                seen[nodeid] = path
                suite.append(case)
    if totals["tests"] == 0:
        return False, "merged report contains 0 tests"
    for key, value in totals.items():
        merged.set(key, str(value))
        suite.set(key, str(value))
    tree = ET.ElementTree(merged)
    try:
        tree.write(output, encoding="utf-8", xml_declaration=True)
    except OSError as exc:
        return False, f"could not write {output} ({exc})"
    return True, (
        f"{totals['tests']} tests "
        f"({totals['tests'] - totals['skipped']} executed, "
        f"{totals['skipped']} skipped) from {len(inputs)} lanes"
    )


def undeclared_skips(xml_path: str) -> list[tuple[str, str]]:
    """(nodeid, reason) for skips no EnvironmentCondition declares."""
    try:
        root = ET.parse(xml_path).getroot()
    except (ET.ParseError, OSError):
        return []
    found: list[tuple[str, str]] = []
    for case in root.iter("testcase"):
        skipped = case.find("skipped")
        if skipped is None:
            continue
        reason = skipped.get("message", "")
        if skip_audit.declared_condition(reason) is None:
            nodeid = f"{case.get('classname', '')}::{case.get('name', '')}"
            found.append((nodeid, reason))
    return found


def triage_failures(xml_path: str) -> list[tuple[str, str, str]]:
    """(nodeid, class, text) for failures/errors, classed advisory-only.

    Class is THROTTLE-LIKE when the text names a rate-limit refusal,
    else RACE-CANDIDATE.  Advisory: never changes the verdict.  A
    THROTTLE-LIKE failure needs a serial-lane re-run before anyone
    calls it a race.
    """
    try:
        root = ET.parse(xml_path).getroot()
    except (ET.ParseError, OSError):
        return []
    out: list[tuple[str, str, str]] = []
    for case in root.iter("testcase"):
        texts = []
        for tag in ("failure", "error"):
            el = case.find(tag)
            if el is not None:
                texts.append((el.get("message", "") or "") + "\n" + (el.text or ""))
        if not texts:
            continue
        text = "\n".join(texts)
        cls = "THROTTLE-LIKE" if _THROTTLE_RE.search(text) else "RACE-CANDIDATE"
        nodeid = f"{case.get('classname', '')}::{case.get('name', '')}"
        out.append((nodeid, cls, text.strip().splitlines()[0][:200] if text.strip() else ""))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lane-report plumbing.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_merge = sub.add_parser("merge", help="merge lane XMLs into one report")
    p_merge.add_argument("--out", required=True)
    p_merge.add_argument("inputs", nargs="+")

    p_counts = sub.add_parser("counts", help="print executed/skipped counts")
    p_counts.add_argument("xml")

    p_undeclared = sub.add_parser(
        "undeclared-skips", help="list skips no declaration covers")
    p_undeclared.add_argument("xml")

    p_triage = sub.add_parser(
        "triage", help="classify failures advisory-only")
    p_triage.add_argument("xml")

    args = parser.parse_args(argv)

    if args.command == "merge":
        ok, detail = merge_reports(args.inputs, args.out)
        print(detail)
        return 0 if ok else 3

    if args.command == "counts":
        counts = read_counts(args.xml)
        if counts is None:
            print("no readable lane report")
            return 3
        print(f"{counts.executed} executed, {counts.skipped} skipped")
        return 0

    if args.command == "undeclared-skips":
        found = undeclared_skips(args.xml)
        for nodeid, reason in found:
            print(f"SKIPPED (undeclared) {nodeid}: {reason}")
        if found:
            for line in skip_audit.UNDECLARED_SKIP_ADVICE.splitlines():
                print(line)
        return 1 if found else 0

    if args.command == "triage":
        for nodeid, cls, first_line in triage_failures(args.xml):
            print(f"{cls} {nodeid}: {first_line}")
        return 0

    return 2  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())
