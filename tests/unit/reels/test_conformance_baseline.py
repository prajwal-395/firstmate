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
import pytest
from library.tools import subtitle_coverage as sc
from library.tools.reel_conformance_verifier import (
    Finding,
    check_subtitle_word_coverage,
)
from library.tools.reel_conformance_verifier import CHECKER_VERSION
from library.tools.reel_conformance_verifier import (
    FindingClass,
    TimelineItem,
    check_semantic_visuals,
)
import json
from library.tools.reel_conformance_verifier import (
    _suppressed_played_words,
)
from library.tools import caption_timing as ct


FPS = 24000 / 1001  # 23.976 exact



def _card(start, end, text="words on screen"):
    from tests.unit.reels.test_reel_conformance_verifier import _caption_card

    return _caption_card(start, end, text)


def _mm_hmm(start=10.0, end=10.6):
    """The Reel 08 shape: an untimed row, no word timings at all.

    F5 falls back to the row envelope and REPORTS that it did, so the
    seconds are an upper bound - exactly what the live final ships.
    """
    from tests.unit.reels.test_reel_conformance_verifier import _row

    return _row(start, end, "SpeakerOne", "Mm-hmm.", words=())


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


# --------------------------------------------------------------------------
# From test_f25_refuses_new_word_mismatch_not_all.py
#
# F25 word_mismatch refuses NEW violations, not ALL.
#
# The F5 comparison's sibling (vep-f5-should-refuse-new-violations-not-all,
# extended 2026-09-20): Reel 03's rebuild was refused for a word_mismatch
# the live final ships by construction - the same card file by hash, the
# same audio, the same transcript for reel 0-60, so the finding is
# identical on both. The gate now judges a staging's word_mismatch errors
# against the live final's: pre-existing (card, word) pairs become loud
# warnings, only NEW pairs refuse.
#
# These tests drive the REAL instrument
# (`subtitle_coverage.check_word_coverage`, the function the gate calls,
# wrapped by `check_subtitle_word_coverage` exactly as the gate wraps it)
# on both sides, then the REAL rewrite, asserting the promote/refuse
# decision. `run_verification` itself needs live Resolve, so the decision
# half is what is pinned here.

def _w(word, start, end, card="card.mov"):
    return {"word": word, "norm": sc.normalize_word(word),
            "reel_start": start, "reel_end": end, "card": card}


def _card_2(name, start, end):
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
    cards = [_card_2("a.mov", 0.9, 1.6), _card_2("b.mov", 1.6, 2.6)]
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
    cards = [_card_2("a.mov", 0.9, 1.6), _card_2("b.mov", 1.6, 2.1)]
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


# --------------------------------------------------------------------------
# From test_f5_f25_baseline_checker_version.py
#
# The F5/F25 baseline is pinned like-for-like across checker versions.
#
# vep-f5-should-refuse-new-violations-not-all, version pin: the gate
# judges a staging against the live final it would replace, and that
# comparison is only valid within one checker version. A tightened
# instrument false-diffs every old final - seconds or words the old code
# never reported read as NEW violations and refuse a correct rebuild.
# So the finding set carries the version that produced it
# (`CHECKER_VERSION`, stamped on every report envelope), and both
# rewrites fail CLOSED on any version mismatch: the staging comes back
# unchanged, errors standing, nothing softened.
#
# These tests drive the REAL instruments and the REAL rewrites, as the
# sibling files do - the version guard wraps the same decision.

# This section imported these helpers from its two siblings above.
_card_3 = _card
_f5_errors_on_2 = _f5_errors_on
_mm_hmm_2 = _mm_hmm
_mismatch_findings_2 = _mismatch_findings

# ── F5 ───────────────────────────────────────────────────────────────

def test_f5_older_final_keeps_errors():
    """The final was graded by an older checker: no comparison, no soften."""
    from library.tools.reel_conformance_verifier import (
        rewrite_f5_against_live_final)

    gap = [_card_3(0.0, 10.0), _card_3(10.6, 20.0)]
    staging = _f5_errors_on_2(gap)
    assert any(f.severity == "error" for f in staging)
    final = _f5_errors_on_2(gap)

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

    gap = [_card_3(0.0, 10.0), _card_3(10.6, 20.0)]
    staging = _f5_errors_on_2(gap)
    final = _f5_errors_on_2(gap)

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

    long_row = [_mm_hmm_2(10.0, 11.2)]
    staging = _f5_errors_on_2([_card_3(0.0, 10.0), _card_3(11.2, 20.0)],
                            rows=long_row)
    final = _f5_errors_on_2([_card_3(0.0, 10.0), _card_3(10.6, 20.0)],
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

    staging, _ = _mismatch_findings_2([("world", 2.1, 2.4)])
    assert len(staging) == 1
    final, _ = _mismatch_findings_2([("world", 2.1, 2.4)])

    rewritten = rewrite_f25_against_live_final(
        "Reel 03 - x (rebuild staging)", list(staging),
        "Reel 03 - x", list(final),
        staging_checker_version=CHECKER_VERSION,
        final_checker_version=CHECKER_VERSION - 1)

    assert rewritten == list(staging)
    assert any(f.severity == "error" for f in rewritten), \
        "fail-closed: a tightened word check must not clear old finals"


# ── Report envelope ──────────────────────────────────────────────────


# --------------------------------------------------------------------------
# From test_f22_exempts_recorded_suppressions.py
#
# F22 exempts a planned segment the build recorded as suppressed.
#
# `do_not_draw` suppresses the PLACEMENT, never the plan - so the first
# reel rebuilt after the captain's Reel 01 deletion (mg_geo-podcast_b46f83e1,
# beat_accent) recorded the segment and placed nothing, and F22 failed the
# build on the absence. The exemption fires only on segment ids the build
# itself stamped onto the record (`suppressed`), never on a rule merely
# existing.

def _item(start_frame, frames, name="mg_x"):
    return TimelineItem(
        track_type="video",
        track_index=5,
        start_frame=start_frame,
        end_frame=start_frame + frames,
        duration_frames=frames,
        source_start_frame=0,
        source_end_frame=frames,
        source_file="/tmp/mg_x.mov",
        speaker=None,
        name=name,
    )


def _planned(suppressed=()):
    return {
        "reel": "Reel 01 - test",
        "basis": "planned",
        "entries": [],
        "dropped": [],
        "segments": [
            {
                "overlay_path": "/tmp/mg_a.mov",
                "segment_id": "mg_a",
                "placement_label": "vox_test_00",
                "timeline_start": 10.0,
                "timeline_end": 13.5,
                "total_frames": 84,
                "elements": ["title_lockup"],
            },
            {
                "overlay_path": "/tmp/mg_b.mov",
                "segment_id": "mg_b",
                "placement_label": "vox_test_01",
                "timeline_start": 24.066,
                "timeline_end": 24.483,
                "total_frames": 10,
                "elements": ["beat_accent"],
            },
        ],
        "suppressed": [{"segment_id": sid} for sid in suppressed],
    }


def test_suppressed_segment_absent_draws_no_f22():
    planned = _planned(suppressed=("mg_b",))
    placed_a = _item(int(round(10.0 * FPS)), 84, name="mg_a")
    findings = check_semantic_visuals(
        "Reel 01 - test", [placed_a], planned, FPS)
    assert [f for f in findings
            if f.finding_class == FindingClass.F22] == []


def test_unsuppressed_missing_segment_still_errors():
    planned = _planned(suppressed=())
    placed_a = _item(int(round(10.0 * FPS)), 84, name="mg_a")
    findings = check_semantic_visuals(
        "Reel 01 - test", [placed_a], planned, FPS)
    errors = [f for f in findings
              if f.finding_class == FindingClass.F22
              and f.severity == "error"]
    assert len(errors) == 1
    assert "577" in errors[0].message


# --------------------------------------------------------------------------
# From test_f25_exempts_recorded_suppressions.py
#
# F25 exempts played words a recorded display suppression hides.
#
# Reel 09, SpeakerOne tail (2026-09-19): she says "you can be completely
# re like misrecommended" - the "re" a false start the audio keeps -
# and learning lc-0061 (display suppression, anchored on SpeakerOne /
# completely / re / like) hides exactly that token from captions and
# quoted copy. Step 4.01 enforces it when planning the cards, so the
# placed card reads "you can be completely like misrecommended." and
# F25, diffing played against drawn without the same step, refused the
# reel with `word_mismatch ... drops 1 played word(s) ... : re`. The
# gate failed captions that obey a recorded correction (AGENTS.md
# 10.4): played words under an active suppression sit out the identity
# diff while coverage still counts them, and the skip is a warning
# naming the suppression, never silence.
#
# `library/tools/subtitle_coverage.py` (`check_word_coverage`);
# wired as F25 in `library/tools/reel_conformance_verifier.py`
# (`_suppressed_played_words`, `_derive_word_coverage`).

CARD = "sub_speakerone_tail.mov"


def _w_2(word, start, end, card=CARD, speaker="SpeakerOne"):
    return {"word": word, "norm": sc.normalize_word(word),
            "reel_start": start, "reel_end": end, "card": card,
            "speaker": speaker}


def _played_2():
    # Reel seconds off the failed Reel 09 build: "re" plays
    # 58.701-58.881 inside the tail card's 57.8-60.0s span.
    return [
        _w_2("you", 57.90, 58.05),
        _w_2("can", 58.05, 58.20),
        _w_2("be", 58.20, 58.35),
        _w_2("completely", 58.35, 58.68),
        _w_2("re", 58.701, 58.881),
        _w_2("like", 58.95, 59.15),
        _w_2("misrecommended.", 59.20, 59.95),
    ]


def _captioned_without_re():
    return [dict(w, card=CARD)
            for w in _played_2() if w["word"] != "re"]


def _cards():
    return [{"card": CARD, "reel_start": 57.8, "reel_end": 60.0}]


def test_suppressed_false_start_draws_no_word_mismatch():
    played = _played_2()
    dropped = [w for w in played if w["word"] == "re"]
    for word in dropped:
        word["suppression"] = "lc-0061"
    result = sc.check_word_coverage(
        played, _captioned_without_re(), _cards(), suppressed=dropped)
    assert [f for f in result["findings"]
            if f["severity"] == "error"] == []
    warnings = [f for f in result["findings"]
                if f["kind"] == "suppressed"]
    assert len(warnings) == 1
    assert "lc-0061" in warnings[0]["message"]
    assert "re" in warnings[0]["message"]
    assert result["meta"]["suppressed_words"] == 1


def test_unsuppressed_dropped_word_still_errors():
    result = sc.check_word_coverage(
        _played_2(), _captioned_without_re(), _cards())
    errors = [f for f in result["findings"]
              if f["kind"] == "word_mismatch"
              and f["severity"] == "error"]
    assert len(errors) == 1
    assert "re" in errors[0]["message"]


def _write_store(tmp_path):
    store = [{
        "id": "lc-0061",
        "kind": "mistake_fix",
        "status": "active",
        "read_by": ["*"],
        "said_by": "the pipeline",
        "source": {"correction_type": "display_suppression",
                   "heard": "re",
                   "scope": {"speaker": "SpeakerOne", "surface": "re",
                             "prev": "completely", "next": "like"},
                   "proposed_by": "model"},
        "detail": "MODEL: 'completely re like misrecommended' false start",
    }]
    path = tmp_path / "learned_context"
    path.mkdir()
    (path / "learnings.json").write_text(json.dumps(store),
                                         encoding="utf-8")
    return str(tmp_path)


def test_helper_marks_only_the_anchored_token(tmp_path):
    project = _write_store(tmp_path)
    marked = _suppressed_played_words(_played_2(), project)
    assert [w["word"] for w in marked] == ["re"]
    assert marked[0]["suppression"] == "lc-0061"


# --------------------------------------------------------------------------
# From test_f25_caption_timing_pins.py
#
# F25 grades caption-timing-pinned cards in the unpinned frame.
#
# Reel 13 (2026-09-20): the captain hand-moved the closing caption
# cards 7 frames later on the live timeline (09-11); the move is
# recorded as caption-timing pins the build applies. F25 compared the
# placed cards against strict word timings and refused the reel with
# ten word_mismatch/played_not_captioned findings - the gate failing
# the captain's recorded decision (AGENTS.md 10.4). Pinned card spans
# now grade shifted back by the pin, matched with the pin owner's own
# predicate; unpinned cards, other timelines' pins, and genuine drops
# past the pins still fail exactly as before.
#
# `library/tools/caption_timing.grade_spans` (the inverse); wired as
# F25 in `library/tools/reel_conformance_verifier._derive_word_coverage`.

FRAME = 1001 / 24000

TIMELINE = "Reel 13 - the-accounting-firm-ai-called-healthcare"
PIN = {
    "scope": {"speaker": "SpeakerOne",
              "source_start_at_or_after": 500.78,
              "source_start_before": 512.759,
              "timeline": TIMELINE},
    "offset_frames": 7,
    "reason": "test: the captain's 09-11 hand move",
}

# The real Reel 13 numbers, shrunk to one card: played "And ... the"
# at strict times, the placed card 7 frames later, block binding as
# the props artefact records it.
PLAYED = [
    {"word": "And", "norm": "and",
     "reel_start": 67.591, "reel_end": 67.851},
    {"word": "that's", "norm": "that's",
     "reel_start": 67.851, "reel_end": 68.011},
    {"word": "why", "norm": "why",
     "reel_start": 68.011, "reel_end": 68.111},
    {"word": "we've", "norm": "we've",
     "reel_start": 68.111, "reel_end": 68.291},
    {"word": "been", "norm": "been",
     "reel_start": 68.291, "reel_end": 68.461},
    {"word": "building", "norm": "building",
     "reel_start": 68.461, "reel_end": 68.991},
    {"word": "the", "norm": "the",
     "reel_start": 68.991, "reel_end": 69.121},
    {"word": "Lucie", "norm": "lucie",
     "reel_start": 69.121, "reel_end": 69.371},
]
CARD_2 = "sub_speakerone_xxx_505862-509292_yyy.mov"
CARD2 = "sub_speakerone_xxx_505862-509292_zzz.mov"
# Placed at record 1628 / 1662, seven frames past the words.
SHIFT = 7 * FRAME
CAPTIONED = [
    {"word": word["word"], "norm": word["norm"], "card": CARD_2,
     "reel_start": word["reel_start"] + SHIFT,
     "reel_end": word["reel_end"] + SHIFT}
    for word in PLAYED[:6]
] + [
    {"word": word["word"], "norm": word["norm"], "card": CARD2,
     "reel_start": word["reel_start"] + SHIFT,
     "reel_end": word["reel_end"] + SHIFT}
    for word in PLAYED[6:]
]
CARDS = [{
    "card": CARD_2,
    "reel_start": 1628 / FPS,
    "reel_end": 1662 / FPS,
    "binding": {"speaker": "SpeakerOne",
                "source_clip_id": "5ae8f521-647e-49c6-bf3d",
                "source_start": 505.862,
                "source_end": 509.292},
}, {
    "card": CARD2,
    "reel_start": 1662 / FPS,
    "reel_end": 1710 / FPS,
    "binding": {"speaker": "SpeakerOne",
                "source_clip_id": "5ae8f521-647e-49c6-bf3d",
                "source_start": 505.862,
                "source_end": 509.292},
}]


def _errors(result):
    return [f for f in result["findings"]
            if f["severity"] == "error"]


def test_pinned_offset_grades_clean():
    graded = ct.grade_spans(CARDS, [PIN], FPS, timeline=TIMELINE)
    result = sc.check_word_coverage(PLAYED, CAPTIONED, graded)
    assert _errors(result) == []


def test_same_shift_without_pin_still_errors():
    graded = ct.grade_spans(CARDS, [], FPS, timeline=TIMELINE)
    result = sc.check_word_coverage(PLAYED, CAPTIONED, graded)
    assert _errors(result) != []


def test_pin_for_another_timeline_is_not_honoured():
    other = dict(PIN)
    other["scope"] = dict(PIN["scope"], timeline="Reel 21 - something-else")
    graded = ct.grade_spans(CARDS, [other], FPS, timeline=TIMELINE)
    result = sc.check_word_coverage(PLAYED, CAPTIONED, graded)
    assert _errors(result) != []


def test_genuine_drop_past_the_pin_still_errors():
    graded = ct.grade_spans(CARDS, [PIN], FPS, timeline=TIMELINE)
    short = [dict(entry) for entry in CAPTIONED if entry["norm"] != "why"]
    result = sc.check_word_coverage(PLAYED, short, graded)
    errors = _errors(result)
    assert errors != []
    assert any("why" in error["message"] for error in errors)


# --------------------------------------------------------------------------
# From test_f25_degenerate_text_spill.py
#
# F25 forgives a pile-up-timed word the adjacent card draws but cannot time.
#
# Reel 27 (2026-09-20): the transcript stamps 'five-star' at 20ms
# inside 'reviews,' (aligner pile-up - `degenerate_indices` flags both,
# neither position trustworthy). The placed card reads "5-star reviews,
# AI does see that." - the viewer reads the word while it is spoken -
# but its karaoke words start at "reviews,": nothing honestly timeable
# exists for the 20ms stamp, so the card starts one frame past it and
# the coverage leg refused the reel with `played_not_captioned ... :
# 5-star`. The gate failed a caption the viewer can read (AGENTS.md
# 10.4): a degenerate-timing played word within edge spill of a card
# whose RENDERED TEXT carries it is timing dust, forgiven with a
# warning naming the word and the card, never silence. Cleanly timed
# words keep the strict timed-norm rule - the Reel 29 "If" precedent
# (one frame past the card, absent from it) still errors.
#
# `library/tools/subtitle_coverage.py` (`check_word_coverage`);
# wired as F25 in `library/tools/reel_conformance_verifier.py`.

CARD_A = "sub_speakerone_leaving_all.mov"
CARD_B = "sub_speakerone_5star_reviews.mov"


def _w_3(word, start, end, card, degenerate=False):
    return {"word": word, "norm": sc.normalize_word(word),
            "reel_start": start, "reel_end": end, "card": card,
            "degenerate": degenerate}


def _played_3(degenerate=True):
    # Reel seconds off the failed Reel 27 staging: "all" through
    # "reviews,"; '5-star' stamped 18.76-18.78 (20ms, pile-up).
    return [
        _w_3("all", 18.60, 18.71, CARD_A),
        _w_3("5-star", 18.76, 18.78, CARD_A, degenerate=degenerate),
        _w_3("reviews,", 18.71, 19.22, CARD_B),
        _w_3("AI", 19.25, 19.40, CARD_B),
    ]


def _captioned():
    # What the renderer timed: card A through "all", card B from
    # "reviews," - "5-star" drawn, never timed.
    return [
        _w_3("all", 18.60, 18.71, CARD_A),
        _w_3("reviews,", 18.81, 19.22, CARD_B),
        _w_3("AI", 19.25, 19.40, CARD_B),
    ]


def _cards_2():
    return [
        {"card": CARD_A, "reel_start": 17.14, "reel_end": 18.73},
        {"card": CARD_B, "reel_start": 18.81, "reel_end": 20.55,
         "text_norms": ["5-star", "reviews", "ai", "does", "see", "that"]},
    ]


def test_degenerate_word_drawn_on_adjacent_card_warns_never_errors():
    result = sc.check_word_coverage(
        _played_3(degenerate=True), _captioned(), _cards_2())
    assert [f for f in result["findings"]
            if f["severity"] == "error"] == []
    spilled = [f for f in result["findings"]
               if f["kind"] == "degenerate_text_spill"]
    assert len(spilled) == 1
    assert spilled[0]["severity"] == "warning"
    assert "5-star" in spilled[0]["message"]
    assert CARD_B in str(spilled[0]["detail"])


def test_cleanly_timed_word_missing_from_timings_still_errors():
    # Same hole, but the word carries trustworthy timings: the strict
    # rule holds - drawn text does not excuse a mistimed clean word.
    result = sc.check_word_coverage(
        _played_3(degenerate=False), _captioned(), _cards_2())
    errors = [f for f in result["findings"]
              if f["kind"] == "played_not_captioned"]
    assert len(errors) == 1
    assert "5-star" in errors[0]["message"]
