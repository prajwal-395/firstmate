"""Step 2.02's aligner must not open a passage on the wrong occurrence.

An anchor SEARCH, not a threshold: completeness first, then the leading
gap, then the hint - so 001's body_3 mis-anchor (6.901s of unchosen audio)
moves and body_0's real dramatic pause stays. The fixture is 001's own
clip_011 data, written under `tmp_path`. History: docs/evidence/transcription.md.
"""

import ast
import inspect
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
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


# ─── every passage 001 cut from this clip ──────────────────────

def test_only_body_3_moves_completely_and_says_so(index_dir, clip_011,
                                                  capsys):
    """The search moves the passage that is wrong and no other, trades
    away no completeness, and never corrects silently: stderr and the
    durable record say RE-ANCHORED for body_3 and HELD for body_0's real
    1.121s pause ("and ... i have an announcement to make")."""
    passage = clip_011["passages"]["body_3"]
    shipped = _hint_nearest_alignment(
        pb._clip_regions_to_window(clip_011["speech_regions"],
                                   float(passage["source_start"]),
                                   float(passage["source_end"])),
        passage)

    result = pb.enrich_speech_sequence(_sequence(clip_011), index_dir)
    body_3 = result["body_sequence"][ORDER.index("body_3")]
    assert len(body_3["word_timestamps"]) == len(shipped)
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

    entry = _report(result)["Body[4]"]
    assert entry["hint_nearest_start"] == pytest.approx(SHIPPED_ANCHOR)
    assert entry["hint_nearest_leading_gap_seconds"] == pytest.approx(
        SHIPPED_LEADING_GAP, abs=1e-3)
    assert entry["chosen_start"] == pytest.approx(CORRECT_ANCHOR)
    assert entry["anchors_considered"] > 1
    assert _report(result)["Body[1]"]["leading_gap_seconds"] == \
        pytest.approx(BODY_0_PAUSE, abs=1e-3)

    err = capsys.readouterr().err
    assert "RE-ANCHORED" in err
    assert "100.519" in err and "107.369" in err
    assert "6.901s" in err
    assert "HELD" in err and "1.121s" in err


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


# ─── the report reaches a reader (`alignment_findings`) ────────
#
# It ORDERS and REPORTS; no number here decides anything (AGENTS.md 6).
# On 001's run of record body[0] shipped a 1.169s silence inside a 2.982s
# block, voiced_fraction 0.474, the worst of eight, and nothing said so.

from library.tools.alignment_findings import (  # noqa: E402
    passage_rows,
    summary_lines,
)

# The eight records 001's run of record really wrote, trimmed to the
# keys this module reads.
REPORT = [
    {"block": "Hook", "clip_id": "clip_011", "event": "ok",
     "source_start": 0.836, "source_end": 3.234,
     "leading_gap_seconds": 0.02, "largest_gap_seconds": 0.08,
     "voiced_fraction": 0.852, "anchors_considered": 26},
    {"block": "Body[#1]", "clip_id": "clip_011", "event": "ok",
     "source_start": 9.699, "source_end": 12.681,
     "leading_gap_seconds": 0.02, "largest_gap_seconds": 1.169,
     "voiced_fraction": 0.474, "anchors_considered": 2},
    {"block": "Body[#2]", "clip_id": "clip_017", "event": "reanchored",
     "source_start": 45.441, "source_end": 55.59,
     "leading_gap_seconds": 0.038, "largest_gap_seconds": 1.072,
     "voiced_fraction": 0.579, "anchors_considered": 4},
]


def test_the_worst_internal_gap_comes_first():
    rows = passage_rows(REPORT)
    assert [r["block"] for r in rows] == ["Body[#1]", "Body[#2]", "Hook"]
    assert rows[0]["largest_gap_seconds"] == 1.169
    assert rows[0]["duration_seconds"] == 2.982
    assert rows[0]["voiced_fraction"] == 0.474


def test_an_unmeasured_passage_keeps_its_row_and_says_so():
    rows = passage_rows([{"block": "Body[#1]", "clip_id": "clip_009"}])
    assert len(rows) == 1
    assert rows[0]["largest_gap_seconds"] is None
    assert rows[0]["voiced_fraction"] is None
    assert "unmeasured" in "\n".join(summary_lines(
        [{"block": "Body[#1]", "clip_id": "clip_009"}]))
