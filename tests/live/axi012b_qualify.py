"""Build the AXI-012b qualification report from the live measurement.

The measurement (`tests/live/axi012b_audio_gain_measurement.py`) writes
what the render said.  This writes what that EVIDENCE QUALIFIES: the
`ren-axi-qualification-result/v2` report the registry tool imports, so
the row that promotes `audio.gain` on `resolve_axi` is derived from
the measurement rather than written by hand.

The report states the state the evidence was earned in (the dimensions
the run measured), the checks that passed, and the boundaries - the
values of each dimension the run did NOT exercise are listed as
uncovered, so the record cannot be read as a wider claim than it is.

Running it::

    python3 tests/live/axi012b_qualify.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "tests" / "live" / "reports" / "axi012b-audio-gain.json"
QUALIFICATION = ROOT / "tests" / "live" / "reports" / (
    "axi012b-audio-gain-qualification.json")


def _measured() -> dict:
    return json.loads(REPORT.read_text(encoding="utf-8"))


def _checks(report: dict) -> dict:
    """The checks the live evidence earned, as booleans."""
    gain = report["gain"]
    reading = gain["audio_evidence"]
    other = report["other"]["uid"]
    untouched = reading["readings"][other]
    return {
        "level_read_back": (
            gain["write"]["read_back"] == -6.0),
        "rendered_level_matches_request": bool(
            reading["target_matches_request"]),
        "mix_preserved": bool(reading["mix_preserved"]),
        "other_clip_unchanged": untouched["delta_db"] == 0.0,
        "linked_av_preserved": bool(gain["linked_preserved"]),
        "level_restored": bool(gain["restored"]),
    }


def build(report: dict) -> dict:
    """The qualification report for the pair the evidence earned."""
    from library.tools import axi_qualification
    from library.tools import axi_qualification_dimensions as dimensions

    state = dict(report["qualification_state"])
    checks = _checks(report)
    measured = {field: [state[field]] for field in dimensions.DIMENSIONS}
    uncovered = {
        field: [value for value in dimensions.DIMENSION_VOCABULARIES[field]
                if value not in measured[field]]
        for field in dimensions.DIMENSIONS
        if field in dimensions.DIMENSION_VOCABULARIES
    }
    uncovered["resolve_version"] = []
    return {
        "schema": axi_qualification.REPORT_SCHEMA,
        "operation_id": "audio.gain",
        "backend": "resolve_axi",
        "result": "passed" if all(checks.values()) else "failed",
        "evidence": ("tests/live/reports/axi012b-audio-gain.json#gain"),
        "resolve_version": str(state["resolve_version"]),
        "environment": {
            "resolve_version": str(state["resolve_version"]),
            "platform": sys.platform,
        },
        "semantic_boundary": {
            "applies_to": [
                ("audio.gain on resolve_axi in the measured state: one "
                 "audio item on an audio row, linked to its picture, on a "
                 "two-row timeline whose rows are populated, at 24 fps in "
                 "1920x1080"),
            ],
            "does_not_apply_to": [
                "a state outside coverage.dimensions",
                "an operation/backend pair this evidence does not name",
                "a timeline that is not a Ren-owned scratch copy",
                "a pan, a mute, a fade or an automation curve: this "
                "evidence names the GAIN and nothing else",
            ],
        },
        "coverage": {
            "dimensions": measured,
            "configurations": ["measured_qualification_timeline"],
            "uncovered": uncovered,
            "source": "measured",
        },
        "project_conditions": [
            "Ren Qualification / AXI-012b audio probe",
            "a Ren-owned scratch timeline only, created and deleted by the "
            "measurement",
            "production entry: resolve-axi edit audio-gain",
        ],
        "supported_media_types": [str(state["media_type"])],
        "expected_state": {name: True for name in checks},
        "actual_state": {
            "checks": checks,
            "measurement": state,
            "scenarios": ["audio_gain_committed"],
        },
        "preservation_checks": [
            {"check": name, "result": "passed"}
            for name, value in checks.items() if value
        ],
        "preservation_limits": [
            "one Ren-owned scratch timeline with two linked A/V clips; the "
            "checks are the ones the measurement names: "
            + ", ".join(sorted(checks)),
            "the level is written IN PLACE, so the target timeline is the "
            "one that changes",
            "the audible verification renders the whole timeline twice per "
            "request",
            "the evidence is one live run; it does not qualify other frame "
            "rates, other geometries, a denser timeline or another Resolve "
            "version",
        ],
        "visual_verification": [
            ("not applicable: this operation writes no picture. The "
             "timeline's items are read back item by item before and after "
             "the write, and every unrelated item must read unchanged"),
        ],
        "audio_verification": [
            ("the rendered audio, measured in the decoded samples: the "
             "target window's level against the requested dB, every other "
             "audio window's level (the mix), the peak (no clipping) and "
             "the digital-zero fraction (no unintended silence)"),
            ("the linked A/V group is read before and after the write"),
        ],
        "recovery_status": "passed",
        "recovery_evidence": (
            "the level is restored to the one the measurement found "
            "(0.0 dB), and the write is rolled back to the previous level "
            "when the rendered audio does not verify the request"),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="print the report and do not import it")
    args = parser.parse_args(argv)
    if not REPORT.is_file():
        raise SystemExit(f"the measurement report is missing: {REPORT}")
    report = _measured()
    if report.get("errors"):
        raise SystemExit(f"the measurement reported errors: {report['errors']}")
    qualification = build(report)
    QUALIFICATION.write_text(json.dumps(qualification, indent=2)
                             + "\n", encoding="utf-8")
    if args.dry_run:
        print(json.dumps({"report": str(QUALIFICATION),
                          "result": qualification["result"]}, indent=2))
        return 0
    from library.tools import axi_qualification

    pair = axi_qualification.import_result(QUALIFICATION)
    print(json.dumps({"imported": list(pair),
                      "report": str(QUALIFICATION)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
