from __future__ import annotations
import pytest
from library.tools.subtitle_qa import verify_subtitle_timing
import json
from types import SimpleNamespace
from library.tools import subtitle_coverage as sc
from library.tools.reel_conformance_verifier import (
    FindingClass,
    ReelPlan,
    ReelTimeline,
    check_subtitle_word_coverage,
    verify_reel,
)
import shutil
import subprocess
import sys
from pathlib import Path


def test_a_planned_non_speech_beat_is_not_a_caption_gap():
    """`subtitle_gaps` reads the spine, so a B-roll breath is not a fault.

    On project 001 the four reported gaps were 3.0, 3.5, 3.0 and 3.0
    seconds - exactly the four B-roll breaths the plan wrote at spine
    blocks 1, 4, 6 and 9 - and the finding reached a creative step (#332)
    carrying a legend that blamed the aligner. Caption coverage inside
    the speech blocks was 84.3%.
    """
    subs = [
        {"timeline_start": 0.0, "timeline_end": 2.398, "text": "hook line"},
        {"timeline_start": 5.398, "timeline_end": 8.38, "text": "second line"},
    ]
    spine = [
        {"block_type": "hook", "position": 0, "clip_id": "clip_011",
         "timeline_start": 0.0, "timeline_end": 2.398},
        {"block_type": "transition_slot", "position": 1,
         "timeline_start": 2.398, "timeline_end": 5.398},
        {"block_type": "speech", "position": 2, "clip_id": "clip_011",
         "timeline_start": 5.398, "timeline_end": 8.38},
    ]

    without = next(r for r in verify_subtitle_timing(subs)
                   if r.metric == "subtitle_gaps")
    assert not without.passed
    assert "whole timeline" in without.detail

    with_spine = next(r for r in verify_subtitle_timing(
        subs, spine_blocks=spine) if r.metric == "subtitle_gaps")
    assert with_spine.passed, with_spine.value
    assert "speech blocks" in with_spine.detail


def test_uncaptioned_seconds_inside_a_speech_block_are_still_reported():
    """The check is repaired, not switched off."""
    subs = [
        {"timeline_start": 0.0, "timeline_end": 1.0, "text": "one"},
        {"timeline_start": 9.0, "timeline_end": 10.0, "text": "two"},
    ]
    spine = [{"block_type": "speech", "position": 0, "clip_id": "clip_011",
              "timeline_start": 0.0, "timeline_end": 10.0}]
    result = next(r for r in verify_subtitle_timing(subs, spine_blocks=spine)
                  if r.metric == "subtitle_gaps")
    assert not result.passed
    assert result.value == [(0, 1, 8.0)]


# --------------------------------------------------------------------------
# From test_subtitle_coverage.py
#
# Word-level subtitle coverage: played == captioned == spine.
#
# A test per disagreement class, each built from a real specimen off the
# field-test project (`Podcast (field test)` / geo-podcast, 2026-09-19)
# rather than a synthesised one. Numbers below are the reel seconds,
# words and card placements the check itself derived from the cached
# transcript, the Sep-18 timeline snapshots and the rendered props
# artefacts; each header names the source span so a reader can re-derive
# them. Fixtures are frozen literals - no test reaches a real project
# (AGENTS.md 8) - but every one demonstrates the CONSEQUENCE (a viewer
# hearing words with nothing on screen, reading words never said, or
# watching a highlight that can never sweep), not just the shape.
#
# `library/tools/subtitle_coverage.py`; wired as F25 in
# `library/tools/reel_conformance_verifier.py`.

FPS = 24000 / 1001


def _w(word, start, end, card="card.mov", degenerate=False):
    return {"word": word, "norm": sc.normalize_word(word),
            "reel_start": start, "reel_end": end, "card": card,
            "degenerate": degenerate}


def _card(name, start, end):
    return {"card": name, "reel_start": start, "reel_end": end}


def test_reel05_phrase_timed_as_one_master_token_reaches_its_caption_and_f25():
    """The rebuild's 54.93s card must retain the MFA phrase token.

    Frozen from the master transcript row on LCATL0012: its sentence says
    "AI sees" as two words, while MFA gives that phrase one measured
    interval. The exact-match caption card starts at 54.4s and the phrase
    plays at 54.928s on the failed Reel 05 staging timeline.
    """
    from library.steps.step_4_01_plan_subtitles.step import generate_subtitles
    from library.tools.reel_spine import spine_for_reel

    source_file = "/field-test/LCATL0012.MXF"
    segment = {
        "speaker": "Craig",
        "text": ("So if you want to see how you're ranking, how AI sees "
                 "you, go to our website, the link's in the bio."),
        "timeline_start": 186.41,
        "timeline_end": 192.33,
        "source_start": 248.472,
        "source_end": 254.392,
        "source_file": source_file,
        "resolve_item_id": "93f6b72c-d223-41f0-89ee-20703ff3d56e",
        "words": [
            {"word": "So", "start": 186.41, "end": 186.69},
            {"word": "if", "start": 187.13, "end": 187.31},
            {"word": "you", "start": 187.31, "end": 187.38},
            {"word": "want", "start": 187.38, "end": 187.55},
            {"word": "to", "start": 187.55, "end": 187.59},
            {"word": "see", "start": 187.59, "end": 187.81},
            {"word": "how", "start": 187.81, "end": 187.97},
            {"word": "you're", "start": 187.97, "end": 188.18},
            {"word": "ranking,", "start": 188.18, "end": 188.56},
            {"word": "how", "start": 188.56, "end": 188.94},
            {"word": "AI sees", "start": 189.02, "end": 189.94},
            {"word": "you,", "start": 189.94, "end": 190.34},
            {"word": "go", "start": 190.67, "end": 190.79},
            {"word": "to", "start": 190.79, "end": 190.85},
            {"word": "our", "start": 190.85, "end": 190.95},
            {"word": "website,", "start": 190.95, "end": 191.47},
            {"word": "the", "start": 191.6, "end": 191.71},
            {"word": "link's", "start": 191.71, "end": 191.96},
            {"word": "in", "start": 191.96, "end": 192.02},
            {"word": "the", "start": 192.02, "end": 192.08},
            {"word": "bio.", "start": 192.08, "end": 192.33},
        ],
    }
    transcript = {"segments": [segment]}

    # The body occupies 45.738s before the approved 179.83-192.391 CTA.
    # This is the placement arithmetic from the failed staging card: the
    # master row starts at reel 52.318s, putting "AI sees" at 54.928s.
    body_end = 179.83
    body_start = body_end - 45.738
    cta = SimpleNamespace(timeline_start=179.83, timeline_end=192.391)
    moment = SimpleNamespace(
        timeline_start=body_start, timeline_end=body_end,
        call_to_action=cta, number=5,
    )
    ranges = [(body_start, body_end), (179.83, 192.391)]
    spine = spine_for_reel(moment, transcript, ranges=ranges)
    plan = generate_subtitles(spine)
    entries = plan["subtitle_plan"]["subtitle_entries"]
    phrase_card = next(
        entry for entry in entries
        if any(word["word"].casefold() == "ai sees"
               for word in entry["words"])
    )

    assert phrase_card["timeline_start"] <= 54.928
    assert phrase_card["timeline_end"] >= 55.848
    assert "ai sees" in phrase_card["text"].casefold()

    played = sc.played_words_from_transcript(
        transcript["segments"],
        [{"source_file": source_file,
          "source_start": 248.472,
          "source_end": 254.392,
          "reel_start": 52.318}],
    )
    assert next(word for word in played["words"]
                if word["word"] == "AI sees")["reel_start"] == pytest.approx(
                    54.928)
    captioned = [
        _w(word["word"], word["start"], word["end"], card=entry["id"])
        for entry in entries for word in entry["words"]
    ]
    cards = [
        _card(entry["id"], entry["timeline_start"], entry["timeline_end"])
        for entry in entries
    ]
    findings = check_subtitle_word_coverage(
        "Reel 05 - ai-cant-form-a-clear-picture-of-you (rebuild staging)",
        {"played": played["words"], "captioned": captioned,
         "cards": cards},
    )
    assert [finding for finding in findings if finding.severity == "error"] == []


# ── normalisation ────────────────────────────────────────────────

class TestNormalizeWord:
    def test_matches_renderer_emphasis_rule(self):
        assert sc.normalize_word("AI") == "ai"
        assert sc.normalize_word("ai,") == "ai"
        assert sc.normalize_word("it's") == "it's"


# ── played but not captioned ─────────────────────────────────────
#
# Reel 29, Craig tail: the reel plays LCATL0014 to reel frame 217
# (9.05s) but the last caption ends at frame 162 (6.76s). Transcript
# row LCATL0014 src 404.79-408.89 carries "It's only giving you four
# to five search ..." (captain's frame-162 note).

class TestPlayedNotCaptioned:
    PLAYED = [
        _w("It's", 7.074, 7.254), _w("only", 7.254, 7.574),
        _w("giving", 7.574, 7.864), _w("you", 7.864, 7.974),
        _w("four", 7.974, 8.344), _w("to", 8.344, 8.404),
        _w("five", 8.404, 8.784), _w("search", 8.784, 9.051),
    ]
    CARDS = [
        _card("sub_craig_tail_a.mov", 5.589, 6.756),
        _card("sub_akshita_next.mov", 9.050, 10.801),
    ]

    def test_uncaptioned_tail_is_one_error_run(self):
        result = sc.check_word_coverage(self.PLAYED, [], self.CARDS)
        errors = [f for f in result["findings"]
                  if f["kind"] == "played_not_captioned"
                  and f["severity"] == "error"]
        assert len(errors) == 1
        assert "four to five" in errors[0]["message"]
        assert errors[0]["detail"]["reel_start"] == pytest.approx(7.074)
        # Covered words stay silent.
        captioned = [_w("It's", 7.074, 7.254, card="c.mov"),
                     _w("only", 7.254, 7.574, card="c.mov")]
        cards = [_card("c.mov", 7.0, 7.6)]
        result = sc.check_word_coverage(
            self.PLAYED[:2], captioned, cards)
        assert [f for f in result["findings"]
                if f["severity"] == "error"] == []


# ── a cut drops the opening word ─────────────────────────────────
#
# Reel 29 frame 1199: V1 cuts to the closer mid-sentence. Played "If"
# (LC4932 src 2533.61, reel 50.05-50.20s) falls one frame past the
# previous card's end and the next card ("you're running a",
# 1203-1232f) never carries it (captain's frame-1199 note).

class TestDroppedWordAtCut:
    def test_if_has_no_caption(self):
        played = [_w("those.", 49.8, 50.0),
                  _w("If", 50.05, 50.20),
                  _w("you're", 50.20, 50.31)]
        captioned = [_w("those.", 49.8, 50.0, card="prev.mov"),
                     _w("you're", 50.20, 50.31, card="next.mov")]
        cards = [_card("prev.mov", 48.71, 50.01),
                 _card("next.mov", 50.18, 51.39)]
        result = sc.check_word_coverage(played, captioned, cards)
        errors = [f for f in result["findings"]
                  if f["severity"] == "error"]
        assert len(errors) == 1
        assert errors[0]["kind"] == "played_not_captioned"
        assert "If" in errors[0]["message"]


# ── captioned but not played ─────────────────────────────────────
#
# Reel 12: played "twenty percent." runs reel 10.93-11.64s
# (LCATL0013 src 1774.6-1775.4) while the card carrying it sits at
# 11.43-12.51s - half a second late, over words already captioned
# elsewhere. The card's words have no played counterpart beside them.

class TestCaptionedNotPlayed:
    PLAYED = [
        _w("about", 10.696, 10.926), _w("twenty", 10.926, 11.156),
        _w("percent.", 11.156, 11.636), _w("I'm", 11.416, 11.696),
        _w("sure", 11.696, 11.886),
    ]
    CARD_WORDS = [
        _w("twenty", 11.428, 11.637, card="late.mov"),
        _w("percent.", 11.428, 11.637, card="late.mov"),
        _w("I'm", 11.428, 11.720, card="late.mov"),
        _w("sure", 11.720, 11.887, card="late.mov"),
    ]

    def test_late_card_words_have_no_played_counterpart(self):
        cards = [_card("late.mov", 11.428, 12.511)]
        result = sc.check_word_coverage(self.PLAYED, self.CARD_WORDS,
                                        cards)
        kinds = {f["kind"] for f in result["findings"]
                 if f["severity"] == "error"}
        # The played words are uncovered AND the card words are
        # unplayed: both directions of one mistimed card.
        assert "played_not_captioned" in kinds
        assert "word_mismatch" in kinds


# ── empty karaoke windows ────────────────────────────────────────
#
# Reel 12 card sub_craig_..._1774344-1780764_9c0ba988: the props give
# 'twenty' startFrame 12 endFrame 6 - the word ends before its own
# card begins, so the clamp inverts it and the sweep travels past it.
# The word IS drawn (the renderer draws every entry in `words` -
# omitting it deleted Reel 05's numbers), only the highlight skips
# it: a warning naming the word and the card, never an error. Pure
# artefact self-contradiction: no transcript needed, so the finding
# fires even where the words match.

class TestEmptyWindow:
    def test_inverted_window_warns_never_errors(self):
        captioned = [_w("twenty", 11.428, 11.178, card="late.mov"),
                     _w("percent.", 11.428, 11.637, card="late.mov")]
        result = sc.check_word_coverage(
            [], captioned, [_card("late.mov", 11.428, 12.511)])
        warned = [f for f in result["findings"]
                  if f["kind"] == "empty_window"]
        assert len(warned) == 1
        assert warned[0]["severity"] == "warning"
        assert "twenty" in warned[0]["message"]
        # Healthy words stay silent.
        result = sc.check_word_coverage(
            [_w("shop,", 11.135, 11.385)],
            [_w("shop,", 11.135, 11.385, card="c.mov")],
            [_card("c.mov", 11.135, 11.927)])
        assert result["findings"] == []

    def test_zero_width_pile_up_word_warns_never_errors(self):
        # Reel 05 card "shop, you're a 50-person shop,": the transcript
        # stamped '50-person' onto a 0.02s pile-up and the props carry
        # it at 31 -> 31 - drawn on screen, never highlighted.
        played = [_w("shop,", 11.135, 11.385),
                  _w("50-person", 11.923, 11.943, degenerate=True)]
        captioned = [_w("shop,", 11.135, 11.385, card="c.mov"),
                     _w("50-person", 11.927, 11.927, card="c.mov")]
        result = sc.check_word_coverage(
            played, captioned, [_card("c.mov", 11.135, 11.927)])
        assert [f for f in result["findings"]
                if f["severity"] == "error"] == []
        warned = [f for f in result["findings"]
                  if f["kind"] == "empty_window"]
        assert len(warned) == 1
        assert "50-person" in warned[0]["message"]


# ── pile-up timings: warn, never false-positive ──────────────────
#
# Reel 05 row LCATL0013 src 633.32-642.0: MFA stamped six words onto
# 353.89-353.91 ('50-person', 'shop,', 'maybe', "you're", 'a',
# 'hundred') and 'person' overlaps them. The cards are exactly right
# ("shop, you're a 50-person shop," / "maybe you're a hundred person
# shop,"), so a word diff reports the transcript's defect as the
# captions'. The check must skip identity there and say so.

PILE_UP_ROW = [
    ("you're", 353.38, 353.48), ("a", 353.48, 353.84),
    ("50-person", 353.89, 353.91), ("shop,", 353.89, 353.91),
    ("maybe", 353.89, 353.91), ("you're", 353.89, 353.91),
    ("a", 353.89, 353.91), ("hundred", 353.89, 353.91),
    ("person", 353.84, 353.91 + 0.46), ("shop,", 354.37, 356.04),
]


class TestPileUpTimings:
    def test_degenerate_indices_flags_the_pile_up(self):
        words = [{"word": w, "start": a, "end": b}
                 for w, a, b in PILE_UP_ROW]
        bad = sc.degenerate_indices(words)
        flagged = {words[i]["word"] for i in bad}
        assert {"50-person", "shop,", "maybe", "hundred",
                "person"} <= flagged
        # A healthy row flags nothing.
        words = [{"word": w, "start": float(i), "end": float(i) + 0.2}
                 for i, w in enumerate(["and", "so", "it's", "gonna"])]
        assert sc.degenerate_indices(words) == set()

    def test_identity_skipped_and_said_not_failed(self):
        # Frozen Reel-05 numbers: played (pile-up flagged degenerate)
        # against the two correct cards. No identity error may fire;
        # the skip is reported; the zero-width artefact words still
        # fire empty_window (true positives off the artefact).
        played = [
            _w("a", 11.513, 11.873),
            _w("person", 11.873, 12.403, degenerate=True),
            _w("50-person", 11.923, 11.943, degenerate=True),
            _w("shop,", 11.923, 11.943, degenerate=True),
            _w("maybe", 11.923, 11.943, degenerate=True),
            _w("you're", 11.923, 11.943, degenerate=True),
            _w("a", 11.923, 11.943, degenerate=True),
            _w("hundred", 11.923, 11.943, degenerate=True),
            _w("shop,", 12.403, 14.073),
        ]
        captioned = [
            _w("shop,", 11.135, 11.385, card="c2.mov"),
            _w("you're", 11.385, 11.510, card="c2.mov"),
            _w("a", 11.510, 11.844, card="c2.mov"),
            _w("50-person", 11.927, 11.927, card="c2.mov"),
            _w("shop,", 11.927, 11.927, card="c2.mov"),
            _w("maybe", 11.927, 11.927, card="c3.mov"),
            _w("you're", 11.927, 11.927, card="c3.mov"),
            _w("a", 11.927, 11.927, card="c3.mov"),
            _w("hundred", 11.927, 11.927, card="c3.mov"),
            _w("person", 11.927, 12.386, card="c3.mov"),
            _w("shop,", 12.386, 14.056, card="c3.mov"),
        ]
        cards = [_card("c2.mov", 11.135, 11.927),
                 _card("c3.mov", 11.927, 14.056)]
        result = sc.check_word_coverage(played, captioned, cards)
        assert [f for f in result["findings"]
                if f["kind"] in ("played_not_captioned", "word_mismatch",
                                 "captioned_not_played")
                and f["severity"] == "error"] == []
        assert [f for f in result["findings"]
                if f["kind"] == "degenerate_timing"
                and f["severity"] == "warning"]
        assert [f for f in result["findings"]
                if f["kind"] == "empty_window"]


# ── spine leg ────────────────────────────────────────────────────

class TestSpineLeg:
    def test_spine_drift_is_info_and_absence_is_said(self):
        played = [_w("hello", 0.0, 0.3), _w("world", 0.3, 0.6)]
        spine = [{"word": "hello", "norm": "hello"},
                 {"word": "there", "norm": "there"},
                 {"word": "world", "norm": "world"}]
        result = sc.check_word_coverage(played, [], [], spine=spine)
        drifts = [f for f in result["findings"]
                  if f["kind"] == "spine_drift"]
        assert len(drifts) == 1
        assert drifts[0]["severity"] == "info"
        # An absent spine says so in meta.
        word = _w("hi", 0.0, 0.2, card="c.mov")
        result = sc.check_word_coverage([word], [word],
                                        [_card("c.mov", 0.0, 1.0)])
        assert result["meta"]["spine_compared"] is False
        assert result["findings"] == []


# ── transcript currency ──────────────────────────────────────────

class TestTranscriptCurrency:
    DOC = {"derived_from": {"fps": FPS, "duration_seconds": 2656.57,
                            "picture_holes": [[2469.967, 2494.409]]}}

    def test_current_passes_and_recut_master_is_stale(self):
        out = sc.transcript_currency(
            self.DOC, FPS, 2656.60, [[2469.967, 2494.409]])
        assert out == {"current": True, "mismatches": []}
        # A recut master is stale.
        out = sc.transcript_currency(
            self.DOC, FPS, 2700.0, [[2469.967, 2494.409]])
        assert out["current"] is False
        assert out["mismatches"][0]["field"] == "duration_seconds"


# ── derivation off artefacts ─────────────────────────────────────

class TestDerivation:
    def test_played_words_map_source_to_reel_and_unbound_are_undetermined(self):
        segments = [{
            "speaker": "Craig", "text": "But now",
            "timeline_start": 2330.0, "timeline_end": 2331.0,
            "source_file": "/m/LCATL0014.MXF",
            "source_start": 402.0, "source_end": 403.0,
            "resolve_item_id": "x",
            "words": [{"word": "But", "start": 2330.0, "end": 2330.2,
                       "timed": True},
                      {"word": "now", "start": 2330.2, "end": 2330.5,
                       "timed": True}],
        }]
        spans = [{"source_file": "/m/LCATL0014.MXF",
                  "source_start": 402.06, "source_end": 408.89,
                  "reel_start": 0.0}]
        out = sc.played_words_from_transcript(segments, spans)
        # "But" starts just before the span but overlaps it: clipped,
        # not dropped (Reel 12 opens on "That" the same way).
        assert [w["word"] for w in out["words"]] == ["But", "now"]
        assert out["words"][0]["reel_start"] == pytest.approx(0.0)
        assert out["words"][0]["reel_end"] == pytest.approx(0.14)

        # Unbound rows are undetermined, never played.
        segments = [{
            "speaker": "Craig", "text": "unbound",
            "timeline_start": 1.0, "timeline_end": 2.0,
            "source_file": None, "source_start": None,
            "resolve_item_id": None,
            "words": [{"word": "unbound", "start": 1.0, "end": 2.0,
                       "timed": True}],
        }]
        out = sc.played_words_from_transcript(
            segments, [{"source_file": "/m/a.MXF", "source_start": 0.0,
                        "source_end": 10.0, "reel_start": 0.0}])
        assert out["words"] == []
        assert len(out["undetermined"]) == 1

    def test_captioned_words_read_the_props_artefact_or_say_unreadable(
            self, tmp_path):
        # Frozen excerpt of Reel 12's mistimed card props
        # (sub_craig_..._1774344-1780764_9c0ba988): words AND their
        # render-relative frames are what the check reads.
        props = {
            "subtitles": [{
                "text": "twenty percent.",
                "startFrame": 12, "endFrame": 38,
                "words": [
                    {"word": "twenty", "startFrame": 12, "endFrame": 6},
                    {"word": "percent.", "startFrame": 12,
                     "endFrame": 17},
                ]}],
        }
        (tmp_path / "card_9c0ba988_props.json").write_text(
            json.dumps(props), encoding="utf-8")
        out = sc.captioned_words_from_placed_cards(
            [{"clip_path": "/p/card_9c0ba988.mov",
              "reel_start_frame": 274, "reel_end_frame": 300,
              "source_in_frame": 12}],
            str(tmp_path), FPS)
        assert [w["word"] for w in out["words"]] == ["twenty",
                                                    "percent."]
        assert out["words"][0]["reel_end"] < out["words"][0]["reel_start"]
        assert out["unreadable"] == []
        # Missing props are unreadable, never silent.
        out = sc.captioned_words_from_placed_cards(
            [{"clip_path": "/p/gone.mov", "reel_start_frame": 0,
              "reel_end_frame": 10, "source_in_frame": 0}],
            str(tmp_path), FPS)
        assert len(out["unreadable"]) == 1
        assert out["words"] == []


# ── verifier wiring (F25) ────────────────────────────────────────

def _coverage(played, captioned, cards, spine=None):
    return {"played": played, "captioned": captioned, "cards": cards,
            "spine": spine or [], "unreadable": [], "undetermined": [],
            "stretched": [], "degenerate_rows": [],
            "currency": {"current": True, "mismatches": []}}


class TestVerifierWiring:
    def test_gap_becomes_f25_error_and_clean_reel_draws_none(self):
        cov = _coverage(
            [_w("five", 8.404, 8.784)], [], [_card("c.mov", 0.0, 1.0)])
        findings = check_subtitle_word_coverage("Reel 29 - x", cov)
        assert findings
        assert {f.finding_class for f in findings} == {FindingClass.F25}
        assert any(f.severity == "error" for f in findings)
        # A clean reel draws no F25.
        word = _w("hi", 0.1, 0.3, card="c.mov")
        cov = _coverage([word], [word], [_card("c.mov", 0.0, 1.0)])
        assert check_subtitle_word_coverage("Reel 29 - x", cov) == []

    def test_stale_transcript_and_new_basis_reports_warn_never_fail(self):
        word = _w("hi", 0.1, 0.3, card="c.mov")
        cov = _coverage([word], [word], [_card("c.mov", 0.0, 1.0)])
        cov["currency"] = {"current": False, "mismatches": [
            {"field": "duration_seconds", "cached": 2656.57,
             "live": 2700.0}]}
        findings = check_subtitle_word_coverage("Reel 29 - x", cov)
        assert findings
        assert {f.severity for f in findings} == {"warning"}
        # The new basis reports (slivers, serialized) warn, never fail.
        cov = _coverage([word], [word], [_card("c.mov", 0.0, 1.0)])
        cov["slivers"] = [{"word": "because", "overlap_seconds": 0.005,
                           "timeline_start": 268.6}]
        cov["serialized"] = [{"word": "website.", "reel_start": 24.03,
                              "was_end": 24.6, "now_end": 24.26,
                              "overlaps": "Also"}]
        findings = check_subtitle_word_coverage("Reel 04 - x", cov)
        assert {f.severity for f in findings} == {"warning"}
        assert {f.detail["kind"] for f in findings} == {"slivers",
                                                        "serialized"}

    def test_verify_reel_runs_f25_only_with_a_basis(self):
        plan = ReelPlan(
            reel_name="Reel 29 - x", reel_number=29,
            plan_seconds=10.0, plan_frames=round(10.0 * FPS, 1),
            span_start=0.0, span_end=10.0, placements=(),
            keep_ranges=((0.0, 10.0),))
        timeline = ReelTimeline(
            reel_name="Reel 29 - x", fps=FPS, total_frames=240,
            video_items=(), audio_items=(), caption_items=())
        cov = _coverage(
            [_w("five", 8.404, 8.784)], [], [_card("c.mov", 0.0, 1.0)])
        result = verify_reel(plan, timeline, word_coverage=cov)
        assert any(f.finding_class == FindingClass.F25
                   and f.severity == "error"
                   for f in result.findings)
        # Without a basis, no F25 runs.
        result = verify_reel(plan, timeline, word_coverage=None)
        assert [f for f in result.findings
                if f.finding_class == FindingClass.F25] == []


# ── span-edge slivers are dust, not speech ─────────────────────────
#
# Reel 04, 2026-09-19: the approved head trim starts the body at
# 268.94s and "because" ends there to the millisecond. The placed
# span starts a dust-width earlier (frame quantisation), so the word
# mapped as a 5ms played sliver at reel 0.00s. The caption correctly
# drops a word the cut removes; the diff must not fail it.

class TestSpanEdgeSliver:
    def _segments(self):
        return [{
            "speaker": "Craig", "text": "because we've",
            "timeline_start": 268.0, "timeline_end": 270.0,
            "source_file": "/m/LCATL0013.MXF",
            "source_start": 268.0, "source_end": 270.0,
            "words": [{"word": "because", "start": 268.60, "end": 268.94,
                       "timed": True},
                      {"word": "we've", "start": 268.94, "end": 269.17,
                       "timed": True}],
        }]

    def test_a_five_ms_touch_is_dust_and_thirty_ms_still_plays(self):
        spans = [{"source_file": "/m/LCATL0013.MXF",
                  "source_start": 268.935, "source_end": 300.0,
                  "reel_start": 0.0}]
        out = sc.played_words_from_transcript(self._segments(), spans)
        assert [w["word"] for w in out["words"]] == ["we've"]
        assert len(out["slivers"]) == 1
        assert out["slivers"][0]["word"] == "because"

        # The Reel-12 "That" precedent (50ms surviving a cut counts)
        # stays above the sub-half-frame floor.
        spans = [{"source_file": "/m/LCATL0013.MXF",
                  "source_start": 268.91, "source_end": 300.0,
                  "reel_start": 0.0}]
        out = sc.played_words_from_transcript(self._segments(), spans)
        assert [w["word"] for w in out["words"]] == ["because", "we've"]
        assert out["slivers"] == []


# ── the diff reads what the planner wrote ──────────────────────────
#
# Reel 04, 2026-09-19: the caption reads "3 recommendations" (the
# numeral rule) where the reel plays "three". Raw transcript against
# read captions fails correct output; both sides must be in read
# space before they meet.

class TestCaptionReadingComparison:
    def test_numeral_reads_equal_and_real_divergence_fails(self):
        played = [dict(_w("three", 14.0, 14.5), source_file="/m/a.MXF")]
        read = sc.read_words_for_comparison(played)
        assert [w["word"] for w in read] == ["3"]
        captioned = [_w("3", 14.0, 14.5, card="c.mov")]
        result = sc.check_word_coverage(
            read, captioned, [_card("c.mov", 13.5, 15.0)])
        assert [f for f in result["findings"]
                if f["severity"] == "error"] == []
        # A real divergence still fails.
        read = sc.read_words_for_comparison(
            [dict(_w("hello", 14.0, 14.5), source_file="/m/a.MXF")])
        result = sc.check_word_coverage(
            read, [_w("goodbye", 14.0, 14.5, card="c.mov")],
            [_card("c.mov", 13.5, 15.0)])
        assert any(f["kind"] == "word_mismatch"
                   and f["severity"] == "error"
                   for f in result["findings"])

    def test_recorded_respells_merge_both_sides(self):
        corrections = [{"id": "t",
                        "heard": "atlanta business chronicle",
                        "correct": "Atlanta Business Chronicle"}]
        played = [dict(_w("atlanta", 1.0, 1.3), source_file="/m/a.MXF"),
                  dict(_w("business", 1.3, 1.6), source_file="/m/a.MXF"),
                  dict(_w("chronicle,", 1.6, 2.1),
                       source_file="/m/a.MXF")]
        read = sc.read_words_for_comparison(played, corrections)
        assert len(read) == 1
        assert read[0]["norm"] == "atlanta business chronicle"
        captioned = [_w("Atlanta Business Chronicle,", 1.0, 2.1,
                        card="c.mov")]
        result = sc.check_word_coverage(
            read, captioned, [_card("c.mov", 0.5, 2.5)])
        assert [f for f in result["findings"]
                if f["severity"] == "error"] == []


# ── one file is one mouth ──────────────────────────────────────────
#
# Reel 04, 2026-09-19: the transcript runs Craig's "website." to
# 817.89s while starting his "Also" at 817.59s of the same LCATL0013.
# One mouth cannot say both; the overlap is alignment slop and the
# caption must serialize the words whatever the transcript stamps.
# Different files are different mics and keep their overlaps.

class TestSameFileSerialization:
    def _played(self, file_a="LCATL0013.MXF", file_b="LCATL0013.MXF"):
        return [
            {"word": "website.", "norm": "website",
             "reel_start": 24.03, "reel_end": 24.60,
             "source_file": f"/m/{file_a}"},
            {"word": "Also", "norm": "also",
             "reel_start": 24.26, "reel_end": 24.67,
             "source_file": f"/m/{file_b}"},
        ]

    def test_same_file_overlap_clips_sequential_other_files_keep_it(self):
        words = self._played()
        serialized = sc._serialize_same_file_overlaps(words)
        assert words[0]["reel_end"] == pytest.approx(24.26)
        assert words[1]["reel_start"] == pytest.approx(24.26)
        assert len(serialized) == 1
        assert serialized[0]["word"] == "website."
        # Different files are different mics and keep their overlap.
        words = self._played(file_b="LC4932.MXF")
        serialized = sc._serialize_same_file_overlaps(words)
        assert words[0]["reel_end"] == pytest.approx(24.60)
        assert serialized == []

    def test_serialized_overlap_passes_the_diff_coincident_stays_loud(self):
        words = self._played()
        sc._serialize_same_file_overlaps(words)
        captioned = [_w("website.", 23.98, 24.27, card="a.mov"),
                     _w("Also", 24.26, 24.67, card="b.mov")]
        result = sc.check_word_coverage(
            words, captioned,
            [_card("a.mov", 23.48, 24.26), _card("b.mov", 24.26, 25.75)])
        assert [f for f in result["findings"]
                if f["severity"] == "error"] == []

        # Coincident words stay loud, not silent.
        words = [
            {"word": "x", "norm": "x", "reel_start": 1.0, "reel_end": 2.0,
             "source_file": "/m/a.MXF"},
            {"word": "y", "norm": "y", "reel_start": 1.0, "reel_end": 2.0,
             "source_file": "/m/a.MXF"},
        ]
        serialized = sc._serialize_same_file_overlaps(words)
        assert words[0]["reel_end"] == pytest.approx(2.0)
        assert serialized and serialized[0].get("unresolved") is True


# --------------------------------------------------------------------------
# From test_subtitle_qa_sampling.py
#
# The subtitle QA gate must look at frames that have captions on them.
#
# A subtitle overlay is transparent between captions. The gate used to
# sample two fixed instants - 0.5s and 1.5s into the segment - and on
# project 001's `sub_block_10` both land in an ordinary pause: the first
# caption is the single word "i", ending 0.70s in, and the next arrives at
# 1.58s. The vision model was shown two blank frames and answered, quite
# correctly, that they were blank - failing a render whose subtitles were
# fine. On an earlier run the same model PASSED the same two blank frames,
# which is the worse half: the gate's verdict had nothing to do with the
# subtitles either way.
#
# So the frames are chosen by measuring the overlay's own alpha channel.
# These tests build real overlays with ffmpeg and check both directions:
# a segment with a gap is sampled where the ink is, and a segment with no
# ink at all still fails.

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools.qa.subtitle_qa import (  # noqa: E402
    ALPHA_INK_THRESHOLD,
    check_caption_geometry,
    find_inked_timestamps,
    run_subtitle_qa,
)

_SECTION_2_MARK = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available",
)


def _render_overlay(path: Path, draw_expr: str, seconds: float = 4.0) -> Path:
    """A 4s transparent 216x384 overlay, drawing a white box when `draw_expr`.

    Small frame and prores4444 so the alpha channel survives, which is
    what the probe reads.
    """
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi",
            "-i", f"color=c=black@0.0:s=216x384:d={seconds}:r=30,format=yuva444p",
            "-vf",
            f"drawbox=x=40:y=300:w=136:h=40:color=white@1.0:t=fill:replace=1"
            f":enable='{draw_expr}'",
            "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le",
            str(path),
        ],
        capture_output=True, check=True,
    )
    return path


@_SECTION_2_MARK
def test_a_gap_is_not_sampled_where_fixed_timestamps_were(tmp_path):
    """Ink only in the last second - the probe must find it there."""
    mov = _render_overlay(tmp_path / "gappy.mov", "between(t,3.0,4.0)")
    found = find_inked_timestamps(str(mov), 4.0, count=2)
    assert found, "the probe found no captions in a segment that has them"
    assert all(t >= 2.8 for t in found), (
        f"sampled {found}, but the only caption is after 3.0s - the gate "
        f"would be judging blank frames again"
    )
    # The regression, stated directly: the old fixed 0.5s and 1.5s are
    # both blank here.
    from library.tools.qa.subtitle_qa import _probe_alpha

    assert _probe_alpha(str(mov), 0.5) < ALPHA_INK_THRESHOLD
    assert _probe_alpha(str(mov), 1.5) < ALPHA_INK_THRESHOLD
    assert _probe_alpha(str(mov), 3.5) >= ALPHA_INK_THRESHOLD


@_SECTION_2_MARK
def test_an_empty_overlay_still_fails(tmp_path, monkeypatch):
    """The thing the gate exists to catch must still be caught.

    No ink anywhere is a blank overlay, not a pause, and it fails before
    the vision model is consulted at all.
    """
    mov = _render_overlay(tmp_path / "blank.mov", "between(t,99,100)")
    assert find_inked_timestamps(str(mov), 4.0, count=2) == []

    monkeypatch.delenv("SKIP_QA_CHECKS", raising=False)
    with pytest.raises(RuntimeError, match="draws nothing anywhere"):
        run_subtitle_qa(str(mov), str(tmp_path))


# ── The mechanical half is what decides ───────────────────────────────


def _frame(tmp_path: Path, box: tuple, size=(1080, 1920)) -> Path:
    """An RGBA frame with one opaque white box at `box` and nothing else."""
    from PIL import Image, ImageDraw

    im = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(im).rectangle(box, fill=(255, 255, 255, 255))
    path = tmp_path / f"frame_{box[0]}_{box[1]}.png"
    im.save(path)
    return path


@_SECTION_2_MARK
def test_caption_geometry_passes_a_normal_caption_and_names_each_fault(
        tmp_path):
    """"casey neistat," - the real caption the model called clipped and
    distorted - measured at alpha bbox (288, 1694, 776, 1770) in a
    1080x1920 frame, 150 clear rows below the type, passes."""
    from PIL import Image

    assert check_caption_geometry(
        str(_frame(tmp_path, (288, 1694, 776, 1770)))) == []
    problems = check_caption_geometry(
        str(_frame(tmp_path, (288, 1860, 776, 1919))))
    assert any("bottom" in p for p in problems), problems
    problems = check_caption_geometry(
        str(_frame(tmp_path, (288, 200, 776, 280))))
    assert any("upper" in p for p in problems), problems
    # A TIGHT canvas is only the caption's box: its ink sits mid-box, and
    # where it sits is the frame's question, asked through the canvas
    # origin. Judged on the box, every tight caption read "upper half"
    # and step 4.05 refused every edit (golden monologue, 2026-10-02).
    tight = str(_frame(tmp_path, (100, 200, 620, 272), size=(720, 480)))
    assert any("upper" in p for p in check_caption_geometry(tight))
    assert check_caption_geometry(
        tight, {"origin": [180, 1300], "frame": [1080, 1920]}) == []
    assert any("upper" in p for p in check_caption_geometry(
        tight, {"origin": [180, 100], "frame": [1080, 1920]}))
    path = tmp_path / "blank.png"
    Image.new("RGBA", (1080, 1920), (0, 0, 0, 0)).save(path)
    problems = check_caption_geometry(str(path))
    assert any("draws nothing" in p for p in problems), problems


@_SECTION_2_MARK
def test_the_vision_verdict_does_not_block(tmp_path, monkeypatch):
    """A FAIL from the model is recorded, not enforced.

    Measured 2026-08-20: gemma-4-12b-it-4bit answered FAIL three times out
    of three on the clean "casey neistat," frame, claiming the letters
    were "overlapping and distorted". Blocking a render on that is not
    coverage.
    """
    import library.tools.qa.subtitle_qa as qa

    mov = _render_overlay(tmp_path / "solid.mov", "gte(t,0)")
    monkeypatch.setattr(
        qa, "check_caption_geometry", lambda _path, _canvas=None: [])
    monkeypatch.setattr(
        qa, "_vision_observation",
        lambda _paths, **k: "FAIL\nthe letters are overlapping and distorted")

    result = qa.run_subtitle_qa(str(mov), str(tmp_path))
    assert result["passed"] is True
    assert "overlapping" in result["vision_observation"]
