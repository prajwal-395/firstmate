"""D10 regression: a considered `fail` verdict is not hollow output.

`validate` fails the run under its own classification
(`ValidationFailed`), never borrowing `HollowOutput`'s "produced no usable
output" wording. History: docs/evidence/validate_verdict.md.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from library.processes.edit_video.run_pipeline import (
    check_output_is_real,
    check_validation_verdict,
)


def _validate_output(status, n_issues):
    """A `validate` step output shaped like the bridge's real one."""
    issues = [f"issue_{i}" for i in range(n_issues)]
    # The same expression `bridge.py` builds the summary from, not the
    # report's literal "2 issue(s) found".
    summary = f"{len(issues)} issue(s) found"
    return {"validation_result": {
        "status": status,
        "summary": summary,
        "distribution_ready": status == "pass",
        "all_issues": issues,
    }}


def _expected_detail(output):
    """The detail line, built from the verdict's own fields."""
    v = output["validation_result"]
    return f"Validation outcome was not successful: {v['status']} - {v['summary']}"


def test_a_considered_verdict_is_not_hollow_but_still_fails():
    """The hollow-output gate stays silent on a complete verdict, while
    the verdict gate still fails the run, under its own name."""
    for status, n_issues in (("fail", 2), ("undetermined", 1)):
        output = _validate_output(status, n_issues)
        assert check_output_is_real("validate", output) == []
        assert check_validation_verdict("validate", output) == [
            _expected_detail(output)]


def test_verdict_gate_only_reads_validate():
    """A fail-shaped payload on any other node is not a verdict."""
    assert check_validation_verdict("render", _validate_output("fail", 2)) == []
