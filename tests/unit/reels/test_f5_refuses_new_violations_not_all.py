"""F5 refuses NEW violations, not ALL (vep-f5-should-refuse-new-violations-not-all).

A rebuild that strictly improves a reel was refused for an F5 violation
the live final already ships (Reel 08's 0.6s untimed "Mm-hmm",
2026-09-20). The gate now grades the live final through the same
`grade_one` and rewrites the staging's F5 errors: pre-existing seconds
become loud warnings, only NEW seconds refuse.

These tests drive the REAL F5 instrument (`check_caption_coverage`)
on both sides and then the REAL rewrite (`rewrite_f5_against_live_final`),
asserting the gate's promote/refuse decision:

- pre-existing violation and nothing new -> promotes (no errors);
- the rebuild ADDS uncaptioned speech -> still refuses (error names it).

`run_verification` itself needs live Resolve, so the decision half is
what is pinned here; the wiring (staging graded, final graded, findings
replaced) is one straight-line block with no branch of its own.
"""

FPS = 24000 / 1001  # 23.976 exact

import pytest


def _card(start, end, text="words on screen"):
    from tests.unit.reels.test_reel_conformance_verifier import _caption_card

    return _caption_card(start, end, text)


def _mm_hmm(start=10.0, end=10.6):
    """The Reel 08 shape: an untimed row, no word timings at all.

    F5 falls back to the row envelope and REPORTS that it did, so the
    seconds are an upper bound - exactly what the live final ships.
    """
    from tests.unit.reels.test_reel_conformance_verifier import _row

    return _row(start, end, "Akshita", "Mm-hmm.", words=())


def _f5_errors_on(cards, rows=None, keep=((0.0, 20.0),)):
    """The real F5 instrument, as the gate runs it on one timeline."""
    from library.tools.reel_conformance_verifier import (
        check_caption_coverage)

    return check_caption_coverage(
        "Reel 08 - top-three-on-google-hallucinated-by-ai",
        rows if rows is not None else [_mm_hmm()],
        cards, list(keep), FPS)


def test_preexisting_violation_promotes():
    """Staging ships the same 0.6s the live final ships: no F5 errors."""
    from library.tools.reel_conformance_verifier import (
        rewrite_f5_against_live_final)

    gap = [_card(0.0, 10.0), _card(10.6, 20.0)]
    staging = _f5_errors_on(gap)
    assert any(f.severity == "error" for f in staging), \
        "the fixture must actually violate F5 before the rewrite"
    final = _f5_errors_on(gap)

    rewritten = rewrite_f5_against_live_final(
        "Reel 08 - x (rebuild staging)", list(staging),
        "Reel 08 - x", list(final))

    errors = [f for f in rewritten if f.severity == "error"]
    assert errors == [], \
        "a rebuild carrying only what the final ships must promote"
    preexisting = [f for f in rewritten
                   if f.finding_class == "F5" and f.severity == "warning"
                   and (f.detail or {}).get("preexisting") is True]
    assert len(preexisting) == 1
    assert "Reel 08 - x" in preexisting[0].message, \
        "the warning names the live final it compared against"
    assert "0.6" in preexisting[0].message


def test_added_violation_still_refuses():
    """Staging leaves 1.2s where the final leaves 0.6s: the new 0.6 refuses."""
    from library.tools.reel_conformance_verifier import (
        rewrite_f5_against_live_final)

    long_row = [_mm_hmm(10.0, 11.2)]
    staging = _f5_errors_on([_card(0.0, 10.0), _card(11.2, 20.0)],
                            rows=long_row)
    final = _f5_errors_on([_card(0.0, 10.0), _card(10.6, 20.0)],
                          rows=long_row)

    rewritten = rewrite_f5_against_live_final(
        "Reel 08 - x (rebuild staging)", list(staging),
        "Reel 08 - x", list(final))

    errors = [f for f in rewritten
              if f.finding_class == "F5" and f.severity == "error"]
    assert len(errors) == 1, \
        "a rebuild that ADDS uncaptioned speech must still refuse"
    assert errors[0].detail["new_straddling_seconds"] == \
        pytest.approx(0.6, abs=0.06)
    assert errors[0].detail["preexisting"] is False
    assert "Reel 08 - x" in errors[0].message


def test_violation_where_the_final_is_clean_refuses():
    """Final fully captioned, staging leaves the Mm-hmm bare: error stands."""
    from library.tools.reel_conformance_verifier import (
        rewrite_f5_against_live_final)

    staging = _f5_errors_on([_card(0.0, 10.0), _card(10.6, 20.0)])
    final = _f5_errors_on([_card(0.0, 20.0)])
    assert not [f for f in final if f.severity == "error"]

    rewritten = rewrite_f5_against_live_final(
        "Reel 08 - x (rebuild staging)", list(staging),
        "Reel 08 - x", list(final))

    assert any(f.finding_class == "F5" and f.severity == "error"
               for f in rewritten)
