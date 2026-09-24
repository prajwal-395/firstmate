"""D10 regression: a considered `fail` verdict is not hollow output.

The end-to-end audit of project 001 reported::

    FAILED (HollowOutput): Step 'validate' reported success but produced
    no usable output:
      - Validation outcome was not successful: fail - 2 issue(s) found

The FAILED status was correct; the classification and the wording were
not.  `validate` produced a complete, useful verdict - the opposite of
emitting nothing - so it must fail the run under its own classification
(`ValidationFailed`) rather than borrowing `HollowOutput`'s "produced no
usable output" wording, which makes a real hollow output harder to
recognise.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
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


def test_fail_verdict_is_not_hollow():
    """The hollow-output gate must stay silent on a complete verdict."""
    assert check_output_is_real("validate", _validate_output("fail", 2)) == []


def test_fail_verdict_is_still_a_failure():
    """...while the verdict gate still fails the run, under its own name."""
    output = _validate_output("fail", 2)
    assert check_validation_verdict("validate", output) == [_expected_detail(output)]


def test_undetermined_verdict_is_not_hollow_but_still_a_failure():
    output = _validate_output("undetermined", 1)
    assert check_output_is_real("validate", output) == []
    assert check_validation_verdict("validate", output) == [_expected_detail(output)]




def test_verdict_gate_only_reads_validate():
    """A fail-shaped payload on any other node is not a verdict."""
    assert check_validation_verdict("render", _validate_output("fail", 2)) == []
