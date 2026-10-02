"""F25 word_mismatch refuses NEW violations, not ALL.

The F5 comparison's sibling (vep-f5-should-refuse-new-violations-not-all,
extended 2026-09-20): Reel 03's rebuild was refused for a word_mismatch
the live final ships by construction - the same card file by hash, the
same audio, the same transcript for reel 0-60, so the finding is
identical on both. The gate now judges a staging's word_mismatch errors
against the live final's: pre-existing (card, word) pairs become loud
warnings, only NEW pairs refuse.

These tests drive the REAL instrument
(`subtitle_coverage.check_word_coverage`, the function the gate calls,
wrapped by `check_subtitle_word_coverage` exactly as the gate wraps it)
on both sides, then the REAL rewrite, asserting the promote/refuse
decision. `run_verification` itself needs live Resolve, so the decision
half is what is pinned here.
"""

import pytest

from library.tools import subtitle_coverage as sc
from library.tools.reel_conformance_verifier import (
    Finding,
    check_subtitle_word_coverage,
)


def _w(word, start, end, card="card.mov"):
    return {"word": word, "norm": sc.normalize_word(word),
            "reel_start": start, "reel_end": end, "card": card}


def _card(name, start, end):
    return {"card": name, "reel_start": start, "reel_end": end}


def _played(extra=()):
    """One played stream; the rebuild cuts elsewhere, so both sides hear it."""
    return [
        _w("hello", 1.0, 1.3), _w("keeper", 1.4, 1.55),
        _w("zebra", 1.7, 2.0), _w("world", 2.1, 2.4),
    ] + [_w(word, start, end) for word, start, end in extra]


def _mismatch_findings(captioned_b, played=None):
    """Real F25 word_mismatch errors for cardB dropping words."""
    played = played if played is not None else _played()
    captioned = ([_w("hello", 1.0, 1.3, card="a.mov"),
                 _w("keeper", 1.4, 1.55, card="a.mov")]
                 + [_w(w, s, e, card="b.mov")
                    for w, s, e in captioned_b])
    cards = [_card("a.mov", 0.9, 1.6), _card("b.mov", 1.6, 2.6)]
    findings = check_subtitle_word_coverage(
        "Reel 03 - x",
        {"played": played, "captioned": captioned, "cards": cards})
    mismatch = [f for f in findings
                if f.finding_class == "F25"
                and f.severity == "error"
                and (f.detail or {}).get("kind") == "word_mismatch"]
    return mismatch, findings


def test_preexisting_mismatch_promotes():
    """Staging drops the same word on the same card the final drops."""
    from library.tools.reel_conformance_verifier import (
        rewrite_f25_against_live_final)

    staging, _ = _mismatch_findings([("world", 2.1, 2.4)])
    assert len(staging) == 1, "the fixture must truly mismatch first"
    assert staging[0].detail["dropped"][0]["word"] == "zebra"
    final, _ = _mismatch_findings([("world", 2.1, 2.4)])

    rewritten = rewrite_f25_against_live_final(
        "Reel 03 - x (rebuild staging)", list(staging),
        "Reel 03 - x", list(final))

    assert [f for f in rewritten if f.severity == "error"] == [], \
        "a rebuild carrying only what the final ships must promote"
    preexisting = [f for f in rewritten
                   if (f.detail or {}).get("preexisting") is True]
    assert len(preexisting) == 1
    assert "Reel 03 - x" in preexisting[0].message


def test_added_mismatch_still_refuses():
    """Staging drops an extra word the final covers: the new word refuses."""
    from library.tools.reel_conformance_verifier import (
        rewrite_f25_against_live_final)

    staging, _ = _mismatch_findings(
        [("moon", 2.45, 2.55)],
        played=_played([("moon", 2.45, 2.55)]))
    dropped = sorted(
        d["word"] for f in staging for d in f.detail["dropped"])
    assert dropped == ["world", "zebra"]
    final, _ = _mismatch_findings(
        [("world", 2.1, 2.4), ("moon", 2.45, 2.55)],
        played=_played([("moon", 2.45, 2.55)]))

    rewritten = rewrite_f25_against_live_final(
        "Reel 03 - x (rebuild staging)", list(staging),
        "Reel 03 - x", list(final))

    errors = [f for f in rewritten if f.severity == "error"]
    assert len(errors) == 1, \
        "a rebuild that ADDS a mismatched word must still refuse"
    assert errors[0].detail["new_words"] == ["world"]
    assert errors[0].detail["preexisting"] is False


def test_mismatch_where_the_final_is_clean_refuses():
    """Final covers everything; the staging's drop is all new."""
    from library.tools.reel_conformance_verifier import (
        rewrite_f25_against_live_final)

    staging, _ = _mismatch_findings([("world", 2.1, 2.4)])
    final, all_final = _mismatch_findings(
        [("zebra", 1.7, 2.0), ("world", 2.1, 2.4)])
    assert not [f for f in all_final if f.severity == "error"]

    rewritten = rewrite_f25_against_live_final(
        "Reel 03 - x (rebuild staging)", list(staging),
        "Reel 03 - x", list(final))

    assert any(f.severity == "error" for f in rewritten)


def test_rerendered_card_with_same_word_promotes():
    """Same word, different card render: pre-existing, by design.

    Measured 2026-09-20 on Reel 03: the live render (2fb0d46d) and the
    rebuild render (881e055c) carry byte-identical text while differing
    in one highlight entry, so card identity does not survive a rebuild
    and cannot be the match key. The cards are REPORTED, never matched.
    """
    from library.tools.reel_conformance_verifier import (
        rewrite_f25_against_live_final)

    staging = [Finding(
        finding_class="F25", reel="Reel 03 - x",
        message="caption b2.mov drops 1 played word(s): zebra",
        severity="error",
        detail={"kind": "word_mismatch", "card": "b2.mov",
                "dropped": [{"word": "zebra"}]})]
    final = [Finding(
        finding_class="F25", reel="Reel 03 - x",
        message="caption b.mov drops 1 played word(s): zebra",
        severity="error",
        detail={"kind": "word_mismatch", "card": "b.mov",
                "dropped": [{"word": "zebra"}]})]

    rewritten = rewrite_f25_against_live_final(
        "Reel 03 - x (rebuild staging)", staging,
        "Reel 03 - x", final)

    assert not [f for f in rewritten if f.severity == "error"]
    warned = [f for f in rewritten
              if (f.detail or {}).get("preexisting") is True]
    assert len(warned) == 1
    assert warned[0].detail["live_final_cards"] == ["b.mov"], \
        "the final's card is reported though never matched on"


# ── empty_window: drawn without a sweep, warned never refused ─────
#
# Reel 03, 2026-09-20: the live render gives the overlapped "that." a
# zero-width highlight window while the rebuild render carries no
# highlight entry for it at all. Same text drawn, same audio, same
# transcript. Under the 2026-09-19 contract both were errors (a sweep
# that could never move read as a defect); the 2026-09-21 rewrite of
# the Reel 05 marker showed that contract deleting numbers from the
# captions. A collapsed window is now drawn unswept - the word is on
# screen, only the highlight skips it - so both sides warn and neither
# refuses. A word the props drop entirely (from `words` AND `text`)
# still refuses, through played_not_captioned/word_mismatch, which the
# tests above pin.

def _empty_window_findings(zero_width=True):
    """A real empty_window warning: a captioned word drawn unswept."""
    that = (1.335, 1.335) if zero_width else (1.3, 1.6)
    played = [_w("hello", 1.0, 1.3), _w("that.", 1.3, 1.6),
              _w("world", 1.7, 2.0)]
    captioned = [_w("hello", 1.0, 1.3, card="a.mov"),
                 _w("that.", *that, card="a.mov"),
                 _w("world", 1.7, 2.0, card="b.mov")]
    cards = [_card("a.mov", 0.9, 1.6), _card("b.mov", 1.6, 2.1)]
    findings = check_subtitle_word_coverage(
        "Reel 03 - x",
        {"played": played, "captioned": captioned, "cards": cards})
    return [f for f in findings
            if f.finding_class == "F25"
            and (f.detail or {}).get("kind") == "empty_window"]


def test_collapsed_window_warns_never_errors():
    """Both sides give 'that.' no sweep: the same dust, not a defect."""
    findings = _empty_window_findings()
    assert len(findings) == 1, "the fixture must truly misfire first"
    assert findings[0].severity == "warning"
    assert "that." in findings[0].message


def test_empty_window_matches_word_mismatch_across_kinds():
    """The Reel 03 shape: live empty_window, staging word_mismatch."""
    from library.tools.reel_conformance_verifier import (
        rewrite_f25_against_live_final)

    staging, _ = _mismatch_findings([("world", 2.1, 2.4)])
    assert any(d["word"] == "zebra" for f in staging
               for d in f.detail["dropped"])
    # reshape to the Reel 03 surfaces: the staging drops "that."
    staging = [Finding(
        finding_class="F25", reel="Reel 03 - x",
        message="caption b.mov drops 1 played word(s): that.",
        severity="error",
        detail={"kind": "word_mismatch", "card": "b.mov",
                "dropped": [{"word": "that."}]})]
    final = [Finding(
        finding_class="F25", reel="Reel 03 - x",
        message="caption a.mov gives 'that.' no time on screen",
        severity="error",
        detail={"kind": "empty_window", "card": "a.mov",
                "word": "that."})]

    rewritten = rewrite_f25_against_live_final(
        "Reel 03 - x (rebuild staging)", staging,
        "Reel 03 - x", final)

    assert [f for f in rewritten if f.severity == "error"] == [], \
        "one defect in two wordings is still one pre-existing defect"
