"""Each recorded duplicate-take fix replays through the build's exact
cascade (exclusion cuts -> lead-in/tail growth -> `reel_ranges`): the bad
take is gone, the kept telling plays whole, nothing refuses.

History: docs/evidence/reel_take_cuts.md.
"""

from types import SimpleNamespace

from library.tools import reel_build
from library.tools import transcript_corrections as tc


def _w(entries):
    """Timed words from `(text, start, end[, timed])` triples."""
    out = []
    for entry in entries:
        text, start, end = entry[0], entry[1], entry[2]
        timed = entry[3] if len(entry) > 3 else True
        out.append({"word": text, "start": start, "end": end, "timed": timed})
    return out


def _even(text, start, end):
    """Evenly spread timed words - interior filler only, never an edge
    under test."""
    parts = text.split(" ")
    step = (end - start) / max(len(parts), 1)
    return [
        {
            "word": word,
            "start": start + i * step,
            "end": start + (i + 1) * step,
            "timed": True,
        }
        for i, word in enumerate(parts)
    ]


def _seg(speaker, text, start, end, words, uid="u"):
    assert len(words) == len(text.split(" ")), (
        f"{text!r}: {len(words)} timings for {len(text.split(' '))} words"
    )
    return {
        "speaker": speaker,
        "text": text,
        "timeline_start": start,
        "timeline_end": end,
        "resolve_item_id": uid,
        "words": words,
    }


def _cascade(body, spec, transcript):
    """The build's exact cascade for one recorded strike."""
    cuts = tc.exclusion_cuts_for_span(
        body[0], body[1], [{"start": spec[0], "end": spec[1], "id": "SPEC"}]
    )
    cuts = tc.grow_cuts_over_wordless_leadin(cuts, transcript)
    cuts, _held = tc.grow_cuts_over_wordless_tail(cuts, transcript)
    moment = SimpleNamespace(timeline_start=body[0], timeline_end=body[1])
    return reel_build.reel_ranges(moment, transcript, extra_cuts=cuts)


def _struck(ranges, span):
    """No played range touches the struck span."""
    return all(b <= span[0] or a >= span[1] for a, b in ranges)


def _plays(ranges, span):
    """Some played range covers the whole span."""
    return any(a <= span[0] and b >= span[1] for a, b in ranges)


# ── Reel 06: doubled answer + flubbed telling (lc-0093) ──


def _reel06():
    craig = _seg(
        "Craig",
        "So in this case size really doesn't matter.",
        422.68,
        425.48,
        _even("So in this case size really doesn't", 422.68, 425.13)
        + _w([("matter.", 425.13, 425.48)]),
        uid="c0",
    )
    first = _seg(
        "Akshita",
        "No, it doesn't.",
        425.87,
        426.66,
        _w(
            [
                ("No,", 425.87, 426.16),
                ("it", 426.16, 426.25),
                ("doesn't.", 426.25, 426.66),
            ]
        ),
        uid="a0",
    )
    flub = _seg(
        "Akshita",
        "Um I've seen 10-person agencies get recommended in AI over "
        "companies fifty times their size.",
        426.51,
        432.36,
        _w(
            [
                ("Um", 426.51, 426.63),
                ("I've", 426.70, 427.11),
                ("seen", 427.11, 427.96),
                ("10-person", 428.01, 428.03, False),
                ("agencies", 427.96, 428.66),
                ("get", 428.66, 428.78),
                ("recommended", 428.78, 429.28),
                ("in", 429.28, 429.40),
                ("AI", 429.40, 429.83),
                ("over", 429.83, 430.18),
                ("companies", 430.18, 430.98),
                ("fifty", 431.19, 431.43),
                ("times", 431.43, 431.74),
                ("their", 431.74, 431.86),
                ("size.", 431.86, 432.36),
            ]
        ),
        uid="a1",
    )
    nod = _seg(
        "Akshita",
        "Not at all.",
        432.67,
        433.09,
        _w([("Not", 432.67, 432.82), ("at", 432.82, 432.89), ("all.", 432.89, 433.09)]),
        uid="a2",
    )
    retake = _seg(
        "Akshita",
        "I've seen a 10-person agency get recommended over a company "
        "fifty times their size, and that's because every source they "
        "have consistently tells the same story.",
        432.83,
        440.30,
        _w([("I've", 432.83, 433.13)])
        + _even(
            "seen a 10-person agency get recommended over a company "
            "fifty times their size, and that's because every source "
            "they have consistently tells the same story.",
            433.13,
            440.30,
        ),
        uid="a3",
    )
    return {"segments": [craig, first, flub, nod, retake]}


def test_each_recorded_verdict_strikes_its_take_and_keeps_the_telling():
    """lc-0093 (425.87-432.36): the doubled answer and the flubbed
    telling go; "Not at all." and the clean retake play."""
    transcript = _reel06()
    ranges = _cascade((422.75, 461.24), (425.87, 432.36), transcript)
    assert _struck(ranges, (425.87, 432.36)), f"bad take still plays: {ranges}"
    assert _plays(ranges, (432.67, 433.09)), f"'Not at all.' lost: {ranges}"
    assert _plays(ranges, (432.83, 434.0)), f"retake opening lost: {ranges}"
    _reel21_verdict_keeps_the_restart()
    _reel24_verdict_removes_the_whole_bad_take()
    _reel03_verdict_keeps_the_recovery_whole()


# ── Reel 11: consolidation is a take choice (lc-0094) ──


def _reel11():
    craig = _seg(
        "Craig",
        "your website can be perfectly fine, but a lot of times "
        "everything else is broken.",
        776.32,
        781.31,
        _even(
            "your website can be perfectly fine, but a lot of times everything else is",
            776.32,
            780.83,
        )
        + _w([("broken.", 780.83, 781.31)]),
        uid="c0",
    )
    op = _seg(
        "Akshita",
        "Absolutely.",
        781.92,
        782.63,
        _w([("Absolutely.", 781.92, 782.63)]),
        uid="a0",
    )
    first = _seg(
        "Akshita",
        "Your website is your resume and everything else are your "
        "references and just like a hiring manner hiring manager would "
        "check both, AI checks both.",
        782.39,
        790.22,
        _w(
            [
                ("Your", 782.39, 782.68),
                ("website", 782.68, 783.21),
                ("is", 783.21, 783.37),
                ("your", 783.37, 783.46),
                ("resume", 783.46, 783.84),
                ("and", 783.84, 784.10),
                ("everything", 784.19, 784.61),
                ("else", 784.61, 784.81),
                ("are", 784.81, 784.87),
                ("your", 784.87, 784.99),
                ("references", 784.99, 785.79),
                ("and", 785.89, 786.21),
                ("just", 786.21, 786.43),
                ("like", 786.43, 786.57),
                ("a", 786.57, 786.63),
                ("hiring", 786.63, 787.01),
                ("manner", 787.01, 787.34),
                ("hiring", 787.55, 787.93),
                ("manager", 787.93, 788.30),
                ("would", 788.30, 788.43),
                ("check", 788.43, 788.80),
                ("both,", 788.94, 789.29),
                ("AI", 789.50, 789.71),
                ("checks", 789.71, 789.99),
                ("both.", 789.99, 790.22),
            ]
        ),
        uid="a1",
    )
    retake = _seg(
        "Akshita",
        "Yeah, so your website is your resume and everything else are "
        "your references, and just like a hiring manager would "
        "definitely check both, AI checks both.",
        790.55,
        797.57,
        _w([("Yeah,", 790.55, 790.72)])
        + _even(
            "so your website is your resume and everything else are "
            "your references, and just like a hiring manager would "
            "definitely check both, AI checks both.",
            790.72,
            797.57,
        ),
        uid="a2",
    )
    return {"segments": [craig, op, first, retake]}


def test_reel11_verdict_consolidates_two_tellings():
    """lc-0094 (781.92-790.22): "Absolutely." and the stumbled first
    telling go; the restated telling plays. The pair scan finds this
    pair and the judge correctly withdraws it (mid-word edge), so only
    the verdict removes it."""
    transcript = _reel11()
    cuts = reel_build.redundant_takes(775.64, 819.13, transcript)
    kept, withdrawn = reel_build.judge_take_cuts(cuts, 775.64, 819.13, transcript)
    assert kept == [], f"nothing mechanical may cut here: {kept}"
    assert [w["reason"] for w in withdrawn] == ["mid_word_edge"]
    ranges = _cascade((775.64, 819.13), (781.92, 790.22), transcript)
    assert _struck(ranges, (781.92, 790.22)), f"first telling still plays: {ranges}"
    assert _plays(ranges, (790.55, 791.5)), f"restated telling lost: {ranges}"


# ── Reel 21: abandoned run-up, restart kept (lc-0095) ──


def _reel21():
    abandoned = _seg(
        "Akshita",
        "If that's the case that's a problem and your website is just "
        "a part of what's maybe",
        1794.86,
        1800.39,
        _even("If that's the", 1794.86, 1795.39)
        + _w(
            [
                ("case", 1795.39, 1795.79),
                ("that's", 1795.92, 1796.17),
                ("a", 1796.17, 1796.24),
                ("problem", 1796.24, 1796.82),
                ("and", 1796.89, 1797.06),
                ("your", 1797.06, 1797.22),
                ("website", 1797.22, 1797.87),
                ("is", 1797.87, 1798.14),
                ("just", 1798.14, 1798.48),
                ("a", 1798.55, 1798.60),
                ("part", 1798.60, 1799.31),
                ("of", 1799.34, 1799.51),
                ("what's", 1799.51, 1799.90),
                ("maybe", 1799.90, 1800.39),
            ]
        ),
        uid="a0",
    )
    restated = _seg(
        "Akshita",
        "and your website is just a part of your whole profile and "
        "that's only twenty percent.",
        1800.62,
        1804.88,
        _w([("and", 1800.62, 1800.71)])
        + _even(
            "your website is just a part of your whole profile and "
            "that's only twenty percent.",
            1800.71,
            1804.88,
        ),
        uid="a1",
    )
    return {"segments": [abandoned, restated]}


def _reel21_verdict_keeps_the_restart():
    """lc-0095 (1796.89-1800.39): the run-up tail goes; the setup and
    the restated telling play. An aborted telling that restarts clean
    keeps the RESTART, not the first attempt."""
    transcript = _reel21()
    ranges = _cascade((1743.05, 1814.68), (1796.89, 1800.39), transcript)
    assert _struck(ranges, (1796.89, 1800.39)), f"run-up still plays: {ranges}"
    assert _plays(ranges, (1795.39, 1796.82)), (
        f"setup ('that's a problem') lost: {ranges}"
    )
    assert _plays(ranges, (1800.62, 1801.5)), f"restated telling lost: {ranges}"


# ── Reel 24: whole bad-take segment (lc-0096) ──


def _reel24():
    prior = _seg(
        "Akshita",
        "The reason is YouTube counts as a very trustworthy source of "
        "information, geo optimized,",
        2032.40,
        2051.26,
        _even(
            "The reason is YouTube counts as a very trustworthy "
            "source of information, geo",
            2032.40,
            2050.68,
        )
        + _w([("optimized,", 2050.68, 2051.26)]),
        uid="a0",
    )
    bad = _seg(
        "Akshita",
        "make sure there's a very strong um okay there's a very strong concise but",
        2051.46,
        2058.18,
        _w(
            [
                ("make", 2051.46, 2051.61),
                ("sure", 2051.61, 2051.77),
                ("there's", 2051.77, 2051.99),
                ("a", 2051.99, 2052.05),
                ("very", 2052.05, 2052.67),
                ("strong", 2052.95, 2053.66),
                ("um", 2054.17, 2054.64),
                ("okay", 2055.27, 2055.47),
                ("there's", 2055.55, 2055.73),
                ("a", 2055.73, 2055.77),
                ("very", 2055.77, 2055.94),
                ("strong", 2055.94, 2056.45),
                ("concise", 2056.45, 2057.24),
                ("but", 2057.78, 2058.18),
            ]
        ),
        uid="a1",
    )
    completion = _seg(
        "Akshita",
        "that there's a very concise description of what exactly your video is about.",
        2058.53,
        2061.98,
        _w([("that", 2058.53, 2058.66)])
        + _even(
            "there's a very concise description of what exactly your video is about.",
            2058.66,
            2061.98,
        ),
        uid="a2",
    )
    return {"segments": [prior, bad, completion]}


def _reel24_verdict_removes_the_whole_bad_take():
    """lc-0096 (2051.46-2058.18): every bit of the bad take goes; the
    concise completion plays."""
    transcript = _reel24()
    ranges = _cascade((2009.53, 2079.92), (2051.46, 2058.18), transcript)
    assert _struck(ranges, (2051.46, 2058.18)), f"bad take still plays: {ranges}"
    assert _plays(ranges, (2058.53, 2059.8)), f"completion lost: {ranges}"


# ── Reel 03: mid-sentence stumble, recovery whole (lc-0097) ──


def _reel03():
    head = (
        "So instead of someone searching C Rm small business on "
        "Google, now they're going to Chat GPT and instead"
    )
    tail = "the best CRM if I run a 10 person law firm?"
    segment = _seg(
        "Akshita",
        head + " typing best C RMs, um s best what's " + tail,
        232.71,
        244.16,
        _even(head, 232.71, 238.00)
        + _w(
            [
                ("typing", 238.00, 238.48),
                ("best", 238.71, 238.93),
                ("C", 238.93, 239.04),
                ("RMs,", 239.04, 239.82),
                ("um", 239.82, 240.16),
                ("s", 240.20, 240.39),
                ("best", 241.58, 241.85),
                ("what's", 241.85, 242.02),
                ("the", 242.02, 242.10),
            ]
        )
        + _even("best CRM if I run a 10 person law firm?", 242.10, 244.16),
        uid="a0",
    )
    return {"segments": [segment]}


def _reel03_verdict_keeps_the_recovery_whole():
    """lc-0097 (238.71-241.85): the stumble goes; the recovery plays
    whole from "what's"."""
    transcript = _reel03()
    ranges = _cascade((213.26, 248.27), (238.71, 241.85), transcript)
    assert _struck(ranges, (238.71, 241.85)), f"stumble still plays: {ranges}"
    assert _plays(ranges, (241.85, 243.0)), f"recovery lost: {ranges}"
    assert _plays(ranges, (237.0, 238.48)), f"lead-in ('typing') lost: {ranges}"


# ── Reel 08: the mechanical cut (no verdict recorded) ──


def _reel08():
    return {
        "segments": [
            _seg(
                "Akshita",
                "Mm-hmm.",
                613.64,
                614.57,
                _w([("Mm-hmm.", 613.64, 614.57)]),
                uid="a0",
            ),
            _seg(
                "Akshita",
                "Yeah, so ranking tells Google,",
                614.72,
                616.48,
                _w(
                    [
                        ("Yeah,", 614.72, 614.91),
                        ("so", 614.91, 615.09),
                        ("ranking", 615.09, 615.44),
                        ("tells", 615.44, 615.85),
                        ("Google,", 615.85, 616.48),
                    ]
                ),
                uid="a1",
            ),
            _seg(
                "Craig",
                "Yeah, so ranking tells Google.",
                614.77,
                616.48,
                _w(
                    [
                        ("Yeah,", 614.77, 614.97),
                        ("so", 614.97, 615.15),
                        ("ranking", 615.15, 615.48),
                        ("tells", 615.48, 615.88),
                        ("Google.", 615.88, 616.48),
                    ]
                ),
                uid="c1",
            ),
            _seg(
                "Akshita",
                "ranking tells Google that you exist.",
                616.51,
                618.12,
                _w(
                    [
                        ("ranking", 616.51, 616.79),
                        ("tells", 616.79, 617.03),
                        ("Google", 617.03, 617.35),
                        ("that", 617.35, 617.48),
                        ("you", 617.48, 617.62),
                        ("exist.", 617.62, 618.12),
                    ]
                ),
                uid="a2",
            ),
        ]
    }


def test_reel08_mechanical_cut_needs_no_verdict():
    """Reel 08's marked retake: the pair scan finds it (0.750/0.600),
    the bleed-aware judge keeps it, and the build drops the first
    telling - no recorded verdict required."""
    transcript = _reel08()
    cuts = reel_build.redundant_takes(588.258, 622.375, transcript)
    kept, withdrawn = reel_build.judge_take_cuts(cuts, 588.258, 622.375, transcript)
    assert [(round(c.dropped_start, 2), round(c.dropped_end, 2)) for c in kept] == [
        (614.72, 616.48)
    ]
    # The word-stream fragment inside the telling withdraws correctly -
    # cutting at 615.85 clips "tells" - while the whole-telling pair
    # cut survives it.
    assert [
        (round(w["cut"].dropped_start, 2), round(w["cut"].dropped_end, 2), w["reason"])
        for w in withdrawn
    ] == [(614.72, 615.85, "mid_word_edge")]
    moment = SimpleNamespace(timeline_start=588.258, timeline_end=622.375)
    ranges = reel_build.reel_ranges(moment, transcript)
    assert _struck(ranges, (614.72, 616.48)), f"doubled telling still plays: {ranges}"
    assert _plays(ranges, (616.51, 618.12)), f"completed telling lost: {ranges}"
