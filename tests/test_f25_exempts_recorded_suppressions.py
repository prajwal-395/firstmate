"""F25 exempts played words a recorded display suppression hides.

Reel 09, Akshita tail (2026-09-19): she says "you can be completely
re like misrecommended" - the "re" a false start the audio keeps -
and learning lc-0061 (display suppression, anchored on Akshita /
completely / re / like) hides exactly that token from captions and
quoted copy. Step 4.01 enforces it when planning the cards, so the
placed card reads "you can be completely like misrecommended." and
F25, diffing played against drawn without the same step, refused the
reel with `word_mismatch ... drops 1 played word(s) ... : re`. The
gate failed captions that obey a recorded correction (AGENTS.md
10.4): played words under an active suppression sit out the identity
diff while coverage still counts them, and the skip is a warning
naming the suppression, never silence.

`library/tools/subtitle_coverage.py` (`check_word_coverage`);
wired as F25 in `library/tools/reel_conformance_verifier.py`
(`_suppressed_played_words`, `_derive_word_coverage`).
"""

import json

from library.tools import subtitle_coverage as sc
from library.tools.reel_conformance_verifier import (
    FindingClass,
    _suppressed_played_words,
    check_subtitle_word_coverage,
)

CARD = "sub_akshita_tail.mov"


def _w(word, start, end, card=CARD, speaker="Akshita"):
    return {"word": word, "norm": sc.normalize_word(word),
            "reel_start": start, "reel_end": end, "card": card,
            "speaker": speaker}


def _played():
    # Reel seconds off the failed Reel 09 build: "re" plays
    # 58.701-58.881 inside the tail card's 57.8-60.0s span.
    return [
        _w("you", 57.90, 58.05),
        _w("can", 58.05, 58.20),
        _w("be", 58.20, 58.35),
        _w("completely", 58.35, 58.68),
        _w("re", 58.701, 58.881),
        _w("like", 58.95, 59.15),
        _w("misrecommended.", 59.20, 59.95),
    ]


def _captioned_without_re():
    return [dict(w, card=CARD)
            for w in _played() if w["word"] != "re"]


def _cards():
    return [{"card": CARD, "reel_start": 57.8, "reel_end": 60.0}]


def test_suppressed_false_start_draws_no_word_mismatch():
    played = _played()
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
        _played(), _captioned_without_re(), _cards())
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
                   "scope": {"speaker": "Akshita", "surface": "re",
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
    marked = _suppressed_played_words(_played(), project)
    assert [w["word"] for w in marked] == ["re"]
    assert marked[0]["suppression"] == "lc-0061"
