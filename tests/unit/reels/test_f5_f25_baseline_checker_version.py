"""The F5/F25 baseline is pinned like-for-like across checker versions.

vep-f5-should-refuse-new-violations-not-all, version pin: the gate
judges a staging against the live final it would replace, and that
comparison is only valid within one checker version. A tightened
instrument false-diffs every old final - seconds or words the old code
never reported read as NEW violations and refuse a correct rebuild.
So the finding set carries the version that produced it
(`CHECKER_VERSION`, stamped on every report envelope), and both
rewrites fail CLOSED on any version mismatch: the staging comes back
unchanged, errors standing, nothing softened.

These tests drive the REAL instruments and the REAL rewrites, as the
sibling files do - the version guard wraps the same decision.
"""

from library.tools.reel_conformance_verifier import CHECKER_VERSION
from tests.unit.reels.test_f5_refuses_new_violations_not_all import (
    _card,
    _f5_errors_on,
    _mm_hmm,
)
from tests.unit.reels.test_f25_refuses_new_word_mismatch_not_all import (
    _mismatch_findings,
)


# ── F5 ───────────────────────────────────────────────────────────────

def test_f5_older_final_keeps_errors():
    """The final was graded by an older checker: no comparison, no soften."""
    from library.tools.reel_conformance_verifier import (
        rewrite_f5_against_live_final)

    gap = [_card(0.0, 10.0), _card(10.6, 20.0)]
    staging = _f5_errors_on(gap)
    assert any(f.severity == "error" for f in staging)
    final = _f5_errors_on(gap)

    rewritten = rewrite_f5_against_live_final(
        "Reel 08 - x (rebuild staging)", list(staging),
        "Reel 08 - x", list(final),
        staging_checker_version=CHECKER_VERSION,
        final_checker_version=CHECKER_VERSION - 1)

    assert rewritten == list(staging), \
        "a version mismatch must not rewrite anything"
    assert any(f.severity == "error" for f in rewritten), \
        "fail-closed: the staging's errors stand"


def test_f5_unknown_final_version_keeps_errors():
    """Findings off disk with no recorded version: unknown is a mismatch."""
    from library.tools.reel_conformance_verifier import (
        rewrite_f5_against_live_final)

    gap = [_card(0.0, 10.0), _card(10.6, 20.0)]
    staging = _f5_errors_on(gap)
    final = _f5_errors_on(gap)

    rewritten = rewrite_f5_against_live_final(
        "Reel 08 - x (rebuild staging)", list(staging),
        "Reel 08 - x", list(final),
        final_checker_version=None)

    assert rewritten == list(staging)
    assert any(f.severity == "error" for f in rewritten)


def test_f5_new_violation_error_carries_baseline_version():
    """Even the refusing error says which checker version it compared."""
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

    errors = [f for f in rewritten if f.severity == "error"]
    assert len(errors) == 1
    assert errors[0].detail["live_final_checker_version"] == \
        CHECKER_VERSION


# ── F25 ──────────────────────────────────────────────────────────────

def test_f25_older_final_keeps_errors():
    """Same pre-existing word on both sides, different checker: refuse."""
    from library.tools.reel_conformance_verifier import (
        rewrite_f25_against_live_final)

    staging, _ = _mismatch_findings([("world", 2.1, 2.4)])
    assert len(staging) == 1
    final, _ = _mismatch_findings([("world", 2.1, 2.4)])

    rewritten = rewrite_f25_against_live_final(
        "Reel 03 - x (rebuild staging)", list(staging),
        "Reel 03 - x", list(final),
        staging_checker_version=CHECKER_VERSION,
        final_checker_version=CHECKER_VERSION - 1)

    assert rewritten == list(staging)
    assert any(f.severity == "error" for f in rewritten), \
        "fail-closed: a tightened word check must not clear old finals"


# ── Report envelope ──────────────────────────────────────────────────
