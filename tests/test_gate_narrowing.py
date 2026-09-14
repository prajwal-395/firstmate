"""The gate must not report PASS on a narrowed run.

`scripts/full_suite_gate.sh` was printing PASS when environment capabilities
were absent, causing tests to skip silently.  The same script in two worktrees
- one with remotion-subtitles/node_modules and one without - produced 4673 vs
4654 passed tests, both reporting PASS.  Twenty tests that RAN in one place
SKIPPED in the other with no difference in the verdict.

This module tests the capability-detection mechanism that stops it:
`EnvironmentCondition.capability` groups skip reasons into named capabilities,
and `missing_capabilities_from_reasons` / `missing_capabilities_from_junit`
extract which ones were absent from a run's skip reasons or JUnit XML.

The gate script uses these to distinguish PASS (full) from NARROWED PASS
(some capabilities absent), and heavy_ml reports through the same mechanism
rather than being a special case in prose.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.skip_audit import (  # noqa: E402
    ENVIRONMENT_CONDITIONS,
    missing_capabilities_from_junit,
    missing_capabilities_from_reasons,
)


class TestCapabilityAnnotations:
    """Every narrowing condition names a capability and an install hint."""

    def test_every_condition_with_capability_has_install_hint(self):
        for cond in ENVIRONMENT_CONDITIONS:
            if cond.capability:
                assert cond.install_hint, (
                    f"condition {cond.pattern!r} has capability "
                    f"{cond.capability!r} but no install_hint")

    def test_complementary_pairs_have_no_capability(self):
        """Complementary pairs always fire on one side; they do not narrow."""
        complementary_patterns = [
            "parselmouth is present",
            "missing-dependency failure mode",
            "tiktoken is installed here",
        ]
        for cond in ENVIRONMENT_CONDITIONS:
            for frag in complementary_patterns:
                if frag in cond.pattern:
                    assert not cond.capability, (
                        f"complementary condition {cond.pattern!r} should "
                        f"not have a capability - one side always fires")


class TestMissingCapabilitiesFromReasons:
    """The function that maps skip reasons to missing capabilities."""

    def test_a_remotion_skip_reports_the_remotion_capability(self):
        reasons = [
            "needs remotion-subtitles/node_modules, npx and ffmpeg",
        ]
        caps = missing_capabilities_from_reasons(reasons)
        assert len(caps) == 1
        assert caps[0].name == "remotion"
        assert caps[0].skip_count == 1
        # The hint is the ONE install, not a per-checkout `npm install`
        # (docs/SHARED_ENVIRONMENT.md): a gate that told a reader to fill
        # this lane's own node_modules would teach the 585-MB-per-lane
        # duplication the store replaced.
        assert "install_node_deps.sh" in caps[0].install_hint

    def test_ffmpeg_skips_are_grouped(self):
        reasons = [
            "ffmpeg is not available",
            "ffmpeg/ffprobe not available",
            "face sampling is an ffmpeg pipeline",
        ]
        caps = missing_capabilities_from_reasons(reasons)
        names = {mc.name for mc in caps}
        assert "ffmpeg" in names
        # The first two both match the ffmpeg capability
        ffmpeg = next(mc for mc in caps if mc.name == "ffmpeg")
        assert ffmpeg.skip_count == 3

    def test_complementary_pair_does_not_narrow(self):
        reasons = [
            "parselmouth is present, so measurement succeeds",
            "tiktoken is installed here; the absent path is elsewhere",
        ]
        caps = missing_capabilities_from_reasons(reasons)
        assert caps == [], (
            f"complementary pairs should not produce capabilities: {caps}")

    def test_multiple_capabilities_are_sorted(self):
        reasons = [
            "needs remotion-subtitles/node_modules, npx and ffmpeg",
            "ffmpeg is not available",
            "node is not on PATH, so the plugin's JavaScript cannot "
            "be run here",
        ]
        caps = missing_capabilities_from_reasons(reasons)
        names = [mc.name for mc in caps]
        assert names == sorted(names)

    def test_empty_reasons_produce_no_capabilities(self):
        assert missing_capabilities_from_reasons([]) == []

    def test_undeclared_reason_is_ignored(self):
        """An undeclared skip reason is not a capability gap."""
        caps = missing_capabilities_from_reasons(["some random reason"])
        assert caps == []


class TestMissingCapabilitiesFromJunit:
    """Reading missing capabilities from a JUnit XML file."""

    def test_reads_skipped_messages_from_junit(self, tmp_path):
        xml = tmp_path / "report.xml"
        xml.write_text(textwrap.dedent("""\
            <?xml version="1.0" encoding="utf-8"?>
            <testsuites>
              <testsuite name="pytest" tests="5" skipped="2"
                         failures="0" errors="0">
                <testcase name="test_ok" classname="test_mod"/>
                <testcase name="test_ok2" classname="test_mod"/>
                <testcase name="test_ok3" classname="test_mod"/>
                <testcase name="test_skip1" classname="test_mod">
                  <skipped message="needs remotion-subtitles/node_modules, npx and ffmpeg"/>
                </testcase>
                <testcase name="test_skip2" classname="test_mod">
                  <skipped message="needs remotion-subtitles/node_modules, npx and ffmpeg"/>
                </testcase>
              </testsuite>
            </testsuites>
        """), encoding="utf-8")
        caps = missing_capabilities_from_junit(str(xml))
        assert len(caps) == 1
        assert caps[0].name == "remotion"
        assert caps[0].skip_count == 2

    def test_no_skips_produces_no_capabilities(self, tmp_path):
        xml = tmp_path / "report.xml"
        xml.write_text(textwrap.dedent("""\
            <?xml version="1.0" encoding="utf-8"?>
            <testsuite name="pytest" tests="3" skipped="0"
                       failures="0" errors="0">
              <testcase name="test_ok"/>
            </testsuite>
        """), encoding="utf-8")
        caps = missing_capabilities_from_junit(str(xml))
        assert caps == []

    def test_complementary_skips_do_not_narrow(self, tmp_path):
        xml = tmp_path / "report.xml"
        xml.write_text(textwrap.dedent("""\
            <?xml version="1.0" encoding="utf-8"?>
            <testsuite name="pytest" tests="2" skipped="1"
                       failures="0" errors="0">
              <testcase name="test_ok"/>
              <testcase name="test_skip">
                <skipped message="parselmouth is present, so measurement succeeds"/>
              </testcase>
            </testsuite>
        """), encoding="utf-8")
        caps = missing_capabilities_from_junit(str(xml))
        assert caps == []

    def test_missing_file_returns_empty(self):
        caps = missing_capabilities_from_junit("/nonexistent/report.xml")
        assert caps == []

    def test_multiple_capabilities_from_one_report(self, tmp_path):
        xml = tmp_path / "report.xml"
        xml.write_text(textwrap.dedent("""\
            <?xml version="1.0" encoding="utf-8"?>
            <testsuite name="pytest" tests="4" skipped="3"
                       failures="0" errors="0">
              <testcase name="test_ok"/>
              <testcase name="test_a">
                <skipped message="needs remotion-subtitles/node_modules, npx and ffmpeg"/>
              </testcase>
              <testcase name="test_b">
                <skipped message="ffmpeg is not available"/>
              </testcase>
              <testcase name="test_c">
                <skipped message="node is not on PATH, so the plugin's JavaScript cannot be run here"/>
              </testcase>
            </testsuite>
        """), encoding="utf-8")
        caps = missing_capabilities_from_junit(str(xml))
        names = {mc.name for mc in caps}
        assert "remotion" in names
        assert "ffmpeg" in names
        assert "node" in names
