"""Step 2.02's aligner must not open a passage on the wrong occurrence.

Project 001's finished 59.437s export carries **6.901 seconds of audio
nobody chose, with no caption over it** - 11.6% of the video - because
`_align_words_to_text` anchored body_3 on the wrong occurrence of the
word *"i"*.  The passage the model wrote begins *"i get caught up in all
the numbers..."*; the aligner opened it seconds before that, and the
two-pointer then walked forward over everything in between with no
bound on the time it may skip.

Both existing guards are the right guards for a different failure.
`MAX_HINT_DRIFT` fired and re-anchored - onto a second wrong *"i"*.
`MIN_TEXT_OVERLAP` compares SETS, so a span that is 63% unmatched audio
still scores a perfect 1.0.

The fix is an anchor SEARCH, not a threshold: every occurrence of the
passage's first word is tried, and the alignments are ranked by words
aligned, then by the leading gap, and only then by proximity to the
model's time hint.  A leading gap survives exactly when no equally
complete anchor removes it - which is what protects body_0's dramatic
pause ("and ... i have an announcement to make"), where the silence IS
the line.

The fixture is 001's own data: clip_011's WhisperX word timings for the
whole clip and the four passages the model cut from it, verbatim from
the 2026-08-26 run that produced the shipped export.  It is written
under `tmp_path`; nothing here reaches a real project (AGENTS.md 8).
"""

import ast
import inspect
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import library.steps.step_2_02_speech_sequence.post_bridge as pb  # noqa: E402

FIXTURE = (REPO_ROOT / "tests" / "fixtures" / "captured_run"
           / "clip_011_speech_regions.json")

# Every number here is 001's, read off the frozen state of the run that
# produced the shipped export.
FULL_CLIP_ANCHOR = 88.366        # what whole-clip alignment reached
SHIPPED_DRIFT = 3.704            # ...which tripped MAX_HINT_DRIFT (2.0)
SHIPPED_ANCHOR = 100.519         # ...and re-anchored onto a second wrong "i"
SHIPPED_LEADING_GAP = 6.901      # ...leaving this much unintended audio
SHIPPED_VOICED = 0.368
CORRECT_ANCHOR = 107.369         # where the passage's text really begins
BODY_0_PAUSE = 1.121             # a real dramatic beat, and it must stay

# The spans the shipped export placed for the other four passages this
# clip was cut into.  None of them may move.
UNCHANGED_SPANS = {
    "body_0": (17.666, 20.348),
    "body_1": (24.171, 26.873),
    "body_2": (63.135, 66.675),
    "body_9": (119.234, 121.940),
}

ORDER = ["body_0", "body_1", "body_2", "body_3", "body_9"]


@pytest.fixture(scope="module")
def clip_011():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def index_dir(tmp_path, clip_011):
    """001's clip_011 index, written under tmp_path."""
    d = tmp_path / "temporal_index"
    d.mkdir()
    with open(d / "clip_011.json", "w", encoding="utf-8") as f:
        json.dump({"speech_regions": clip_011["speech_regions"]}, f)
    return str(d)


def _sequence(clip_011, keys=ORDER):
    return {
        "hook_segment": None,
        "body_sequence": [
            dict(clip_011["passages"][k], position=i)
            for i, k in enumerate(keys, start=1)
        ],
    }


def _hint_nearest_alignment(regions, passage):
    """The rule that shipped: anchor on the occurrence nearest the hint.

    Kept reachable so this fixture keeps its teeth - a test that can no
    longer fail is not coverage (AGENTS.md 10.4).
    """
    candidates = [w for r in regions for w in r["words"]]
    passage_words = pb.normalize(passage["text"]).split()
    hint = float(passage["source_start"])
    occurrences = [i for i, c in enumerate(candidates)
                   if pb.normalize(c["word"]) == passage_words[0]]
    nearest = min(occurrences,
                  key=lambda i: abs(candidates[i]["start"] - hint))
    return pb._align_from(
        candidates, passage_words, pb._anchor_backed_up(candidates, nearest))


def _report(result):
    return {e["block"]: e for e in result["alignment_report"]}


# ─── the defect, reproduced in both of its stages ──────────────

def test_the_rule_that_shipped_opens_body_3_on_the_wrong_i(clip_011):
    """Both stages of the mis-anchor, exactly as 001 recorded them."""
    passage = clip_011["passages"]["body_3"]
    regions = clip_011["speech_regions"]
    start = float(passage["source_start"])
    end = float(passage["source_end"])

    full = _hint_nearest_alignment(regions, passage)
    assert full[0]["start"] == pytest.approx(FULL_CLIP_ANCHOR)
    drift = max(abs(full[0]["start"] - start), abs(full[-1]["end"] - end))
    assert drift == pytest.approx(SHIPPED_DRIFT, abs=1e-3)
    assert drift > pb.MAX_HINT_DRIFT      # so the drift guard re-anchors...

    windowed = _hint_nearest_alignment(
        pb._clip_regions_to_window(regions, start, end), passage)
    assert windowed[0]["start"] == pytest.approx(SHIPPED_ANCHOR)
    assert windowed[0]["word"].lower().strip(" ,.") == "i"
    assert pb._leading_gap(windowed) == pytest.approx(
        SHIPPED_LEADING_GAP, abs=1e-3)
    assert pb._voiced_fraction(windowed) == pytest.approx(
        SHIPPED_VOICED, abs=1e-3)

    # ...and the words the viewer hears in that gap are not this
    # passage's.  They are a sentence nobody chose.
    heard = " ".join(
        w["word"] for r in regions for w in r["words"]
        if SHIPPED_ANCHOR < w["start"] < CORRECT_ANCHOR
    ).lower()
    assert "goals" in heard
    assert "goals" not in pb.normalize(passage["text"]).split()


def test_neither_existing_guard_can_see_it(clip_011):
    """MIN_TEXT_OVERLAP is set-based, so the bad span scores 1.0."""
    passage = clip_011["passages"]["body_3"]
    start = float(passage["source_start"])
    end = float(passage["source_end"])
    windowed = _hint_nearest_alignment(
        pb._clip_regions_to_window(clip_011["speech_regions"], start, end),
        passage)

    passage_words = set(pb.normalize(passage["text"]).split())
    found = set(pb.normalize(w["word"]) for w in windowed)
    ratio = len(passage_words & found) / len(passage_words)
    assert ratio == 1.0
    assert ratio >= pb.MIN_TEXT_OVERLAP

    # It is 36.8% voiced. Nothing in the module looked.
    assert pb._voiced_fraction(windowed) < 0.40


# ─── the fix ───────────────────────────────────────────────────

def test_body_3_is_re_anchored_onto_its_own_first_word(index_dir, clip_011):
    """The aligner opens body_3 where the model's text begins."""
    result = pb.enrich_speech_sequence(_sequence(clip_011), index_dir)
    body_3 = result["body_sequence"][ORDER.index("body_3")]

    assert body_3["source_start"] == pytest.approx(CORRECT_ANCHOR)
    assert body_3["source_end"] == pytest.approx(116.512)
    assert body_3["word_timestamps"][0]["word"].lower().strip(" ,.") == "i"
    assert body_3["word_timestamps"][1]["word"].lower().strip(" ,.") == "get"

    entry = _report(result)["Body[4]"]
    assert entry["leading_gap_seconds"] < 0.1
    # The unintended audio is gone.
    assert (SHIPPED_LEADING_GAP - entry["leading_gap_seconds"]) > 6.8


def test_the_re_anchor_trades_away_no_completeness(index_dir, clip_011):
    """The tighter anchor aligns every word the loose one did."""
    passage = clip_011["passages"]["body_3"]
    start = float(passage["source_start"])
    end = float(passage["source_end"])
    shipped = _hint_nearest_alignment(
        pb._clip_regions_to_window(clip_011["speech_regions"], start, end),
        passage)

    result = pb.enrich_speech_sequence(_sequence(clip_011), index_dir)
    body_3 = result["body_sequence"][ORDER.index("body_3")]
    assert len(body_3["word_timestamps"]) == len(shipped)


def test_the_re_anchor_says_what_it_did(index_dir, clip_011, capsys):
    """It never corrects silently - stderr and the durable record."""
    result = pb.enrich_speech_sequence(_sequence(clip_011), index_dir)
    entry = _report(result)["Body[4]"]

    assert entry["event"] == "reanchored"
    assert entry["hint_nearest_start"] == pytest.approx(SHIPPED_ANCHOR)
    assert entry["hint_nearest_leading_gap_seconds"] == pytest.approx(
        SHIPPED_LEADING_GAP, abs=1e-3)
    assert entry["chosen_start"] == pytest.approx(CORRECT_ANCHOR)
    assert entry["anchors_considered"] > 1

    err = capsys.readouterr().err
    assert "RE-ANCHORED" in err
    assert "100.519" in err and "107.369" in err
    assert "6.901s" in err


def test_body_0s_dramatic_pause_survives(index_dir, clip_011, capsys):
    """A real 1.121s beat is held, not flattened - and it says so.

    "and ... i have an announcement to make": the pause IS the line.  No
    alternative anchor aligns this passage as completely with a smaller
    leading gap, so the silence stands.
    """
    result = pb.enrich_speech_sequence(_sequence(clip_011), index_dir)
    body_0 = result["body_sequence"][ORDER.index("body_0")]

    assert (round(body_0["source_start"], 3),
            round(body_0["source_end"], 3)) == UNCHANGED_SPANS["body_0"]

    entry = _report(result)["Body[1]"]
    assert entry["event"] == "held"
    assert entry["leading_gap_seconds"] == pytest.approx(BODY_0_PAUSE,
                                                         abs=1e-3)
    err = capsys.readouterr().err
    assert "HELD" in err
    assert "1.121s" in err


# ─── every passage 001 cut from this clip ──────────────────────

def test_only_body_3_moves(index_dir, clip_011):
    """The search moves the passage that is wrong and no other."""
    result = pb.enrich_speech_sequence(_sequence(clip_011), index_dir)
    spans = {
        k: (round(p["source_start"], 3), round(p["source_end"], 3))
        for k, p in zip(ORDER, result["body_sequence"])
    }
    for key, span in UNCHANGED_SPANS.items():
        assert spans[key] == span, key
    assert spans["body_3"] == (107.369, 116.512)

    events = {b: e["event"] for b, e in _report(result).items()}
    assert events == {
        "Body[1]": "held",        # body_0, the 1.121s dramatic pause
        "Body[2]": "held",        # body_1, a 0.120s pause
        "Body[3]": "ok",
        "Body[4]": "reanchored",  # body_3, the defect
        "Body[5]": "ok",
    }


def test_the_report_measures_every_passage(index_dir, clip_011):
    """The record is a measurement of all of them, not just the bad one."""
    report = pb.enrich_speech_sequence(
        _sequence(clip_011), index_dir)["alignment_report"]

    assert len(report) == len(ORDER)
    for entry in report:
        assert entry["leading_gap_seconds"] is not None
        assert entry["voiced_fraction"] is not None
        assert entry["largest_gap_seconds"] is not None
        assert entry["anchors_considered"] >= 1


# ─── the measurements are measurements, not thresholds ─────────

def test_nothing_is_judged_against_a_constant():
    """No magic number: a gap is judged against its own passage.

    The whole decision lives in `_record_alignment`, and the only
    literals in it are 0 and 1 - it compares the leading gap to the
    LARGEST gap of the same alignment.  A band fitted to 001 (a 6.9s
    ceiling, a 61-86% voiced floor) would be a constant chosen from one
    recording, and this is what stops one being added.
    """
    tree = ast.parse(inspect.getsource(pb._record_alignment))
    literals = {
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
    }
    assert literals <= {0, 1}, literals

    source = inspect.getsource(pb._record_alignment)
    assert "gap >= largest" in source

    module_src = (REPO_ROOT / "library" / "steps"
                  / "step_2_02_speech_sequence" / "post_bridge.py"
                  ).read_text(encoding="utf-8")
    for forbidden in ("MAX_LEADING_GAP", "MIN_VOICED_FRACTION",
                      "MIN_LEADING_GAP", "MAX_VOICED"):
        assert forbidden not in module_src, forbidden


def test_the_ranking_puts_completeness_before_the_gap():
    """A tighter anchor never wins by aligning fewer words.

    That ordering is what keeps a passage from being trimmed to a short
    tight run of words that happens to match.
    """
    candidates = [
        # A tight "and i" that is not this passage at all.
        {"word": "and", "start": 1.00, "end": 1.10},
        {"word": "i", "start": 1.12, "end": 1.20},
        {"word": "left", "start": 1.30, "end": 1.50},
        # The real passage, opening on a one-second pause.
        {"word": "and", "start": 5.00, "end": 5.10},
        {"word": "i", "start": 6.10, "end": 6.20},
        {"word": "have", "start": 6.22, "end": 6.40},
        {"word": "news", "start": 6.42, "end": 6.70},
    ]
    notes = {}
    aligned = pb._align_words_to_text(
        candidates, "and i have news", hint_start=5.0, hint_end=6.7,
        notes=notes)

    assert [w["word"] for w in aligned] == ["and", "i", "have", "news"]
    assert notes["leading_gap"] == pytest.approx(1.0)
    assert notes["event"] == "held"
