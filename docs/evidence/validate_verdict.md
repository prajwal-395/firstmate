# A considered `fail` verdict is not hollow output (D10)

Moved from `tests/test_validate_verdict_is_not_hollow.py`.

The end-to-end audit of project 001 reported:

    FAILED (HollowOutput): Step 'validate' reported success but produced
    no usable output:
      - Validation outcome was not successful: fail - 2 issue(s) found

The FAILED status was correct; the classification and the wording were not.
`validate` produced a complete, useful verdict - the opposite of emitting
nothing - so it must fail the run under its own classification
(`ValidationFailed`) rather than borrowing `HollowOutput`'s "produced no
usable output" wording, which makes a real hollow output harder to recognise.
