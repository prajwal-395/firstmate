"""`retake_scan` REPORTS false starts, paraphrases and cross-segment
verbatims with both texts, a basis and a recommended strike; it cuts
nothing, and legitimate repetition does not report.

History: docs/evidence/reel_take_cuts.md.
"""

from library.tools import retake_scan


def _even_words(text, start, end):
    """Timed words spread evenly across a span.

    Order-exact, timing-approximate: enough for detectors that read
    the wording and the segment boundaries, never for ones asserting
    exact word-edge spans (those carry real timings below).
    """
    parts = text.split(" ")
    n = len(parts)
    step = (end - start) / max(n, 1)
    return [{"word": word, "start": start + i * step,
             "end": start + (i + 1) * step, "timed": True}
            for i, word in enumerate(parts)]


def _seg(speaker, text, start, end, words=None, uid="u"):
    return {"speaker": speaker, "text": text,
            "timeline_start": start, "timeline_end": end,
            "resolve_item_id": uid, "source_file": "LC4932.MXF",
            "source_start": 0.0, "source_end": end - start,
            "words": words if words is not None
            else _even_words(text, start, end)}


def _real_words(entries, offset=0.0):
    return [{"word": word, "start": start + offset,
             "end": end + offset, "timed": True}
            for word, start, end in entries]


# ── Reel 06: a two-take pair past the pair-scan bars ──

def _reel06_transcript():
    """Akshita's two tellings, real texts and boundaries, even words.

    The pair scores containment 0.69 / Jaccard 0.39 - under both bars -
    while the shared head reads similarity 1.0 on the word stream.
    """
    return {"segments": [
        _seg("Akshita", "No, it doesn't.", 425.87, 426.66, uid="a0"),
        _seg("Akshita",
             "Um I've seen 10-person agencies get recommended in AI "
             "over companies fifty times their size.",
             426.51, 432.36, uid="a1"),
        _seg("Akshita", "Not at all.", 432.67, 433.09, uid="a2"),
        _seg("Akshita",
             "I've seen a 10-person agency get recommended over a "
             "company fifty times their size, and that's because every "
             "source they have consistently tells the same story.",
             432.83, 440.30, uid="a3"),
    ]}


def test_a_retake_shape_reports_its_strike():
    """Reel 06's shape: the head repeats word for word while the tails
    diverge past the pair-scan bars. Reported with the whole first
    telling as the strike - never cut, because a window is not a
    telling."""
    reported = retake_scan.scan_span(
        422.66, 461.40, _reel06_transcript())["reported"]
    verbatim = [c for c in reported if c["kind"] == "verbatim_retelling"]
    assert len(verbatim) == 1
    found = verbatim[0]
    assert (found["dropped_start"], found["dropped_end"]) == (426.51, 432.36)
    assert found["speaker"] == "Akshita"
    assert found["similarity"] == 1.0
    assert "426.51-432.36" in found["recommended_action"].replace(" ", "")
    _a_restart_inside_one_segment_reports_the_run_up()


# ── Reel 24: a false start and its restart in one segment ──

def _reel24_segment():
    """The marked segment, real text and real word timings."""
    words = _real_words([
        ("make", 2051.46, 2051.61), ("sure", 2051.61, 2051.77),
        ("there's", 2051.77, 2051.99), ("a", 2051.99, 2052.05),
        ("very", 2052.05, 2052.67), ("strong", 2052.95, 2053.66),
        ("um", 2054.17, 2054.64), ("okay", 2055.27, 2055.47),
        ("there's", 2055.55, 2055.73), ("a", 2055.73, 2055.77),
        ("very", 2055.77, 2055.94), ("strong", 2055.94, 2056.45),
        ("concise", 2056.45, 2057.24), ("but", 2057.78, 2058.18),
    ])
    return _seg("Akshita",
                "make sure there's a very strong um okay there's a "
                "very strong concise but",
                2051.46, 2058.18, words=words, uid="a1")


def _a_restart_inside_one_segment_reports_the_run_up():
    """Reel 24's shape: "there's a very strong" twice with "um okay"
    between, in a telling that ends on "but". The first copy is the
    flubbed run-up, at real word seconds."""
    transcript = {"segments": [
        _seg("Akshita", "YouTube Shorts, make sure that when you post.",
             2040.00, 2051.26, uid="a0"),
        _reel24_segment(),
        _seg("Akshita",
             "that there's a very concise description of your video.",
             2058.53, 2061.98, uid="a2"),
    ]}
    reported = retake_scan.scan_span(
        2040.00, 2070.00, transcript)["reported"]
    restarts = [c for c in reported
                if c["shape"] == "restart_inside_one_segment"]
    assert len(restarts) == 1
    found = restarts[0]
    assert found["kind"] == "false_start"
    assert (found["dropped_start"], found["dropped_end"]) == (2051.77, 2053.66)
    assert (found["kept_start"], found["kept_end"]) == (2055.55, 2056.45)


# ── Reel 21: an abandoned telling restated from its head ──

def _reel21_a_words():
    """Reel 21's abandoned telling, real text and real word timings."""
    return _real_words([
        ("If", 1794.86, 1795.04), ("that's", 1795.04, 1795.28),
        ("the", 1795.28, 1795.39), ("case", 1795.39, 1795.79),
        ("that's", 1795.92, 1796.17), ("a", 1796.17, 1796.24),
        ("problem", 1796.24, 1796.82), ("and", 1796.89, 1797.06),
        ("your", 1797.06, 1797.22), ("website", 1797.22, 1797.87),
        ("is", 1797.87, 1798.14), ("just", 1798.14, 1798.48),
        ("a", 1798.55, 1798.60), ("part", 1798.60, 1799.31),
        ("of", 1799.34, 1799.51), ("what's", 1799.51, 1799.90),
        ("maybe", 1799.90, 1800.39),
    ])


# ── Positive controls: legitimate repetition that must NOT report ──

def test_legitimate_repetition_does_not_report():
    """lc-0005's rhetorical triple ("geo geo geo", emphasis inside one
    telling); Reel 21's unmarked contrast questions (one shared content
    word, the first ends complete); parallel structure in one complete
    sentence. Real texts and boundaries, even words."""
    cases = [
        ((9.0, 16.0), [
            _seg("Craig", "their CMO or their head of marketing",
                 9.154, 11.120, uid="c0"),
            _seg("Craig",
                 "we've got to get into geo geo geo i get it it's "
                 "something that's going to continually eat into",
                 11.241, 15.680, uid="c1"),
        ]),
        ((1791.00, 1795.00), _contrast_questions()),
        ((99.0, 105.0), [
            _seg("Akshita",
                 "if you're gonna post daily you're gonna burn out fast.",
                 100.00, 104.00, uid="a0"),
        ]),
    ]
    for (start, end), segments in cases:
        assert retake_scan.scan_span(
            start, end, {"segments": segments})["reported"] == [], segments


def _contrast_questions():
    return [
        _seg("Akshita",
             "Are you actually showing up the way you want there?",
             1791.08, 1793.47, uid="a0"),
        _seg("Akshita", "Are you not even showing up at all?",
             1793.17, 1794.59, uid="a1"),
    ]


def test_telling_properties_measure_concise_and_clear():
    """The properties the model reads: durations, content-word counts,
    completeness, and disfluency with seconds."""
    transcript = {"segments": [
        _seg("Akshita",
             "If that's the case that's a problem and your website is "
             "just a part of what's maybe",
             1794.86, 1800.39, words=_reel21_a_words(), uid="a1"),
    ]}
    props = retake_scan.telling_properties(1794.86, 1800.39, transcript)
    assert props["duration_seconds"] == 5.53
    assert props["ends_complete"] is False
    assert props["content_words"] > 0
    assert props["disfluencies"] == []


# ── The selection bridge carries candidates to the model ──

def test_the_bridge_reports_candidates_with_a_verdict_line():
    """Reel 21's shape: "your website is just a part of what's maybe"
    restated as "your website is just a part of your whole profile".
    The scan reports the run-up's tail from the joining "and" (the setup
    "that's a problem" survives, at real word seconds), and
    `retake_candidates_inside` gives the model what judging takes: both
    tellings with measured properties, the basis, and a concrete
    recommended strike - marked as work the build will not do."""
    from library.steps.step_3_04_select_reels import bridge

    transcript = {"segments": [
        _seg("Akshita", "Are you not even showing up at all?",
             1793.17, 1794.59, uid="a0"),
        _seg("Akshita",
             "If that's the case that's a problem and your website is "
             "just a part of what's maybe",
             1794.86, 1800.39, words=_reel21_a_words(), uid="a1"),
        _seg("Akshita",
             "and your website is just a part of your whole profile "
             "and that's only twenty percent.",
             1800.62, 1804.88, uid="a2"),
    ]}
    reported = retake_scan.scan_span(
        1793.00, 1805.00, transcript)["reported"]
    assert [(c["kind"], c["shape"], c["kept_start"], c["kept_end"])
            for c in reported] == [
        ("false_start", "abandoned_telling_restated", 1800.62, 1804.88)]
    found = bridge.retake_candidates_inside(1793.00, 1805.00, transcript)
    assert len(found) == 1
    context = found[0]
    assert (context["dropped_start"], context["dropped_end"]) == (1796.89, 1800.39)
    assert context["judge"]["build_removes"] is False
    assert context["recommended_action"].startswith("strike 1796.89")
    assert context["dropped_ends_complete"] is False
    assert context["kept_ends_complete"] is True
    assert context["basis"]
    assert bridge.retake_candidates_inside(
        1791.00, 1795.00, {"segments": _contrast_questions()}) == []
