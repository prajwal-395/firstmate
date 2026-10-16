"""Step 2.02's aligner must not open a passage on the wrong occurrence.

An anchor SEARCH, not a threshold: completeness first, then the leading
gap, then the hint - so 001's body_3 mis-anchor (6.901s of unchosen audio)
moves and body_0's real dramatic pause stays. The fixture is 001's own
clip_011 data, written under `tmp_path`. History: docs/evidence/transcription.md.
"""
import ast
import inspect
import io
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import library.steps.step_2_02_speech_sequence.post_bridge as pb

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

# The aligned word spans, refined outward by the bounded silence pads.
# The fifth passage is the one whose anchor is corrected separately.
UNCHANGED_SPANS = {
    "body_0": (17.516, 20.498),
    "body_1": (24.021, 27.023),
    "body_2": (62.985, 66.685),
    "body_9": (119.084, 122.090),
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
    assert spans["body_3"] == (107.259, 116.515)

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

from library.tools.alignment_findings import (
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


# --------------------------------------------------------------------------
# From test_speech_sequence_duration_gate.py
#
# Step 2.02's duration verdict belongs to the script, not the model.
#
# The gate is HARD: `refuse_out_of_zone_sequence` raises and `main` exits 1
# with the numbers, so the post-bridge retry carries them back to the
# model that chose the passages - the step that can still cut. Speech owns
# the lower half of the zone (its ceiling is the declared TARGET, not the
# zone max); the band above is the room step 2.05's breaths, intro/outro
# and music/picture blocks extend into. Each refusal names its own fix (too
# long cuts, too short extends) and never which passage goes. Findings 9
# and 30 (execution-frontier report 2026-09-24): an over-long sequence
# once passed here and died a stage later at 3.03/2.05, and the too-short
# message said "fewer or shorter".
#
# The enrichment fixture is 001's own clip_011 data: five passages
# enriching to ~20.8s of real word timings. Nothing reaches a real project.

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.tools.duration_targets import (
    get_speech_duration_zone,
    get_target_duration_zone,
)


@pytest.fixture
def index_dir_2(tmp_path, clip_011):
    d = tmp_path / "temporal_index"
    d.mkdir()
    (d / "clip_011.json").write_text(
        json.dumps({"speech_regions": clip_011["speech_regions"]}),
        encoding="utf-8",
    )
    return str(d)


@pytest.fixture
def enriched(clip_011, index_dir_2):
    seq = {
        "body_sequence": [
            dict(clip_011["passages"][k], position=i)
            for i, k in enumerate(ORDER, start=1)
        ],
    }
    return pb.enrich_speech_sequence(seq, index_dir_2)


# ── The total is measured off aligned words, not the hint ────────────

def test_the_total_is_read_off_the_aligned_timings(enriched):
    assert pb.total_speech_seconds(enriched["body_sequence"]) == pytest.approx(
        21.946, abs=0.01)


def test_refined_edit_points_are_returned_as_a_durable_report(enriched):
    rows = enriched["edit_point_report"]
    assert len(rows) == len(enriched["body_sequence"])
    assert rows[0]["source_start_before"] == pytest.approx(17.666)
    assert rows[0]["source_start"] == pytest.approx(17.516)
    assert rows[0]["head_pad_seconds"] == pytest.approx(0.15)


def test_measured_prosody_reaches_the_cut_boundary_report(tmp_path):
    temporal_index = tmp_path / "temporal_index"
    temporal_index.mkdir()
    (temporal_index / "clip_voice.json").write_text(json.dumps({
        "speech_regions": [{
            "start": 1.0,
            "end": 1.4,
            "words": [{"word": "important", "start": 1.0, "end": 1.4}],
        }],
    }), encoding="utf-8")
    sequence = {"body_sequence": [{
        "clip_id": "clip_voice",
        "source_start": 1.0,
        "source_end": 1.4,
        "text": "important",
    }]}
    prosody = {"profiles": {"clip_voice": {"prosody": {
        "word_prosody": [{
            "word": "important", "start": 1.0, "end": 1.4,
            "emphasis": 0.8,
        }],
    }}}}

    result = pb.enrich_speech_sequence(
        sequence, str(temporal_index), prosody_analysis=prosody)

    assert "prosody_peak_near" in result["edit_point_report"][0]["head_flags"]


def test_measured_action_interval_reaches_cut_report_not_parent_window(tmp_path):
    temporal_index = tmp_path / "temporal_index"
    temporal_index.mkdir()
    (temporal_index / "clip_action.json").write_text(json.dumps({
        "speech_regions": [{
            "start": 1.0,
            "end": 1.4,
            "words": [{"word": "turn", "start": 1.0, "end": 1.4}],
        }],
    }), encoding="utf-8")
    sequence = {"body_sequence": [{
        "clip_id": "clip_action",
        "source_start": 1.0,
        "source_end": 1.4,
        "text": "turn",
    }]}
    semantic = [{
        "clip_id": "clip_action",
        "analysis_metadata": {"pipeline_version": "v3"},
        "scene": [],
        "camera": [],
        "actions": [{"window": [0.0, 10.0], "actions": [{
            "start": 0.9, "end": 1.2, "action": "turns toward camera",
        }]}],
    }]

    result = pb.enrich_speech_sequence(
        sequence, str(temporal_index),
        semantic_analysis_documents=semantic)

    assert "action_crossing" in result["edit_point_report"][0]["head_flags"]


def test_semantic_action_windows_without_measured_actions_are_not_actions():
    doc = {"actions": [{"window": [0.0, 10.0], "actions": []}]}
    assert pb._action_windows_for_clip(doc) == []


def test_semantic_parse_error_leaves_action_windows_unmeasured():
    doc = {"actions": [{"window": [0.0, 10.0], "parse_error": True,
                        "actions": []}]}
    assert pb._action_windows_for_clip(doc) is None


# ── The gate, both directions ─────────────────────────────────────────

def test_the_speech_gate_refuses_each_direction_with_its_own_fix():
    """(speech seconds, target, refusal fragments, forbidden fragment).
    30.3 s at a 30 s target is the B6 shape that passed the old full-zone
    gate; 28 s leaves the 30-33 s band for what extends the total."""
    rows = [
        (30.3, 30, ["30.3s", "fewer or shorter", "30.0-33.0s"], None),
        (28.0, 30, None, None),
        (40.1, 60, ["40.1s", "MORE or LONGER", "54.0-66.0s"],
         "fewer or shorter"),
    ]
    for seconds, target, fragments, forbidden in rows:
        data = {"project_config": {"target_duration_seconds": target}}
        args = (seconds, get_speech_duration_zone(data),
                get_target_duration_zone(data))
        if fragments is None:
            assert pb.refuse_out_of_zone_sequence(*args) is None
            continue
        with pytest.raises(pb.SpeechDurationError) as excinfo:
            pb.refuse_out_of_zone_sequence(*args)
        message = str(excinfo.value)
        for fragment in fragments:
            assert fragment in message, (seconds, message)
        if forbidden:
            assert forbidden not in message
    # No declared target is unchecked, not judged.
    assert pb.refuse_out_of_zone_sequence(30.3, None) is None


# ── The gate is wired into the step's entry point, not defined beside it

def _payload(clip_011, index_dir_2, target):
    data = {
        "speech_sequence": {
            "body_sequence": [
                dict(clip_011["passages"][k], position=i)
                for i, k in enumerate(ORDER, start=1)
            ],
        },
        "temporal_index": {"index_dir": index_dir_2},
    }
    if target is not None:
        data["project_config"] = {"target_duration_seconds": target}
    return json.dumps(data)


def test_main_refuses_speech_over_target_inside_the_total_max(
        clip_011, index_dir_2, capsys, monkeypatch):
    """2.02 must leave the upper band for non-speech the spine adds.

    The aligned speech is about 20.8 s. At a 19 s target it fits inside
    the total zone's 20.9 s maximum, but exceeds the speech ceiling.
    The old full-zone check let it through, leaving no room for breaths.
    """
    monkeypatch.setattr(sys, "stdin",
                        io.StringIO(_payload(clip_011, index_dir_2, 19)))
    with pytest.raises(SystemExit) as excinfo:
        pb.main()
    assert excinfo.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["step"] == "2.02_bridge"
    assert "above the speech ceiling" in out["error"]


def test_main_names_reversed_setup_and_payoff_beats(
        clip_011, index_dir_2, capsys, monkeypatch):
    payload = json.loads(_payload(clip_011, index_dir_2, None))
    payload["intent_model"] = {"beats": [
        {"beat_id": "beat:1", "purpose": "setup",
         "setup_for": "beat:2"},
        {"beat_id": "beat:2", "purpose": "payoff",
         "payoff_of": "beat:1"},
    ]}
    for index, passage in enumerate(
            payload["speech_sequence"]["body_sequence"]):
        passage["serves_beats"] = ["beat:2" if index == 0 else "beat:1"]

    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    with pytest.raises(SystemExit) as excinfo:
        pb.main()

    assert excinfo.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["step"] == "2.02_bridge"
    assert out["error"] == "speech sequence violates narrative constraints"
    assert out["problems"] == [
        ("setup beat 'beat:1' must precede payoff beat 'beat:2', "
         "but their speech passages are reversed or interleaved")
    ]


# --------------------------------------------------------------------------
# From test_speech_sequence_stated_trim.py
#
# Rung 7 (finding 33, C3.1): a stated trim is honored to the frame.
#
# On the scout's B7 run (C3.1 "trim 4f off the head of shot 2") the
# passage's source_start 9.699 -> 9.833 was re-anchored BACK to the word
# by 2.02's aligner - by design the bounds are a lookup hint (AGENTS.md
# 6), and no field said "this bound is a stated trim, keep it". The
# requester's number never reached the timeline.
#
# The fix: `trim_head_frames` / `trim_tail_frames` (frames when the
# request states frames) and `trim_head_seconds` / `trim_tail_seconds`
# (seconds when it states seconds) apply AFTER word alignment, to the
# aligned span. The words stay as measured; only the played bounds move.
# Both forms on one edge must agree past half a frame, frames with no
# timebase refuse, and a trim eating the passage refuses - a deletion,
# not a trim.

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


FPS = 30.0

# One clip: words "take the shot now" at 9.6-12.6s, one word every 0.6s
# with 0.5s of voice each (the shape of a spoken line, not a fixture).
WORDS = [
    {"word": "take", "start": 9.6, "end": 10.1},
    {"word": "the", "start": 10.2, "end": 10.7},
    {"word": "shot", "start": 10.8, "end": 11.3},
    {"word": "now", "start": 11.4, "end": 11.9},
]


@pytest.fixture
def index_dir_3(tmp_path):
    d = tmp_path / "temporal_index"
    d.mkdir()
    (d / "clip_001.json").write_text(json.dumps({
        "speech_regions": [{
            "start": 9.6, "end": 11.9,
            "text": "take the shot now",
            "words": WORDS,
        }],
    }), encoding="utf-8")
    return str(d)


def _passage(**over):
    base = {"clip_id": "clip_001", "source_start": 9.6,
            "source_end": 11.9, "text": "take the shot now"}
    base.update(over)
    return {"body_sequence": [base]}


def test_a_stated_trim_moves_only_the_played_bound(index_dir_3):
    """C3.1's shape: 4 frames off the head lands at 9.733, not the
    word's 9.6; seconds trim the tail; frames and seconds that agree on
    one edge ship the frames. The words stay as measured."""
    rows = [
        ({"trim_head_frames": 4}, 9.6 + 4 / FPS, 11.9),
        ({"trim_tail_seconds": 0.5}, 9.6, 11.4),
        ({"trim_head_frames": 4, "trim_head_seconds": 4 / FPS},
         9.6 + 4 / FPS, 11.9),
    ]
    for trim, start, end in rows:
        out = pb.enrich_speech_sequence(
            _passage(**trim), index_dir_3, frame_rate=FPS)
        (passage,) = out["body_sequence"]
        assert passage["source_start"] == pytest.approx(start), trim
        assert passage["source_end"] == pytest.approx(end), trim
        assert passage["word_timestamps"][0]["source_start"] == (
            pytest.approx(9.6)), trim
    out = pb.enrich_speech_sequence(
        _passage(trim_head_frames=4), index_dir_3, frame_rate=FPS)
    (report,) = out["alignment_report"]
    assert report["stated_trim"]["trim_head_seconds"] == pytest.approx(
        4 / FPS, abs=1e-3)


def test_frames_without_a_timebase_refuse(index_dir_3):
    """Frames have no grid without project_fps: refuse, never guess."""
    with pytest.raises(pb.PassageAlignmentError):
        pb.enrich_speech_sequence(
            _passage(trim_head_frames=4), index_dir_3, frame_rate=None)


def test_a_partial_sentence_head_expands_to_its_boundary_not_the_prior_sentence(
        tmp_path, capsys):
    """A response selected at "yep" keeps its spoken lead-in.

    The previous sentence stays excluded; the new passage opens at the
    nearest transcript sentence boundary and carries exact word timings
    for the captions that follow.
    """
    words = [
        ("There's", 10.00, 10.28),
        ("seven", 10.30, 10.58),
        ("modules", 10.60, 10.96),
        ("that", 10.98, 11.14),
        ("make", 11.16, 11.42),
        ("up", 11.44, 11.58),
        ("the", 11.60, 11.72),
        ("system.", 11.74, 12.12),
        ("So", 12.40, 12.56),
        ("it's", 12.58, 12.76),
        ("like,", 12.78, 13.00),
        ("yep,", 13.04, 13.28),
        ("your", 13.30, 13.48),
        ("website", 13.50, 13.86),
        ("checks", 13.88, 14.12),
        ("out.", 14.14, 14.44),
    ]
    temporal_index = tmp_path / "temporal_index"
    temporal_index.mkdir()
    (temporal_index / "clip_craig.json").write_text(json.dumps({
        "speech_regions": [{
            "start": words[0][1],
            "end": words[-1][2],
            "words": [
                {"word": word, "start": start, "end": end}
                for word, start, end in words
            ],
        }],
    }), encoding="utf-8")
    sequence = {"body_sequence": [{
        "clip_id": "clip_craig",
        "source_start": 13.04,
        "source_end": 14.44,
        "text": "yep, your website checks out.",
        "position": 1,
    }]}

    result = pb.enrich_speech_sequence(sequence, str(temporal_index))

    (passage,) = result["body_sequence"]
    assert passage["text"] == "So it's like, yep, your website checks out."
    assert passage["source_start"] == pytest.approx(12.26)
    assert passage["source_end"] == pytest.approx(14.44)
    assert [word["word"] for word in passage["word_timestamps"]] == [
        "So", "it's", "like,", "yep,", "your", "website", "checks", "out."
    ]
    assert result["alignment_report"][0]["sentence_boundary_adjustment"] == {
        "head": {
            "from": 13.04,
            "to": 12.40,
            "added_words": ["So", "it's", "like,"],
        },
    }
    assert "There\'s seven modules" not in passage["text"]
    assert "sentence edge expanded back head" in capsys.readouterr().err


def test_sentence_expansion_does_not_guess_a_boundary_at_region_start(tmp_path):
    """A VAD region can itself start mid-sentence; do not treat that as punctuation."""
    temporal_index = tmp_path / "temporal_index"
    temporal_index.mkdir()
    (temporal_index / "clip_craig.json").write_text(json.dumps({
        "speech_regions": [{
            "start": 12.40,
            "end": 14.44,
            "words": [
                {"word": "like,", "start": 12.40, "end": 12.62},
                {"word": "yep,", "start": 12.66, "end": 12.90},
                {"word": "your", "start": 12.92, "end": 13.10},
                {"word": "website", "start": 13.12, "end": 13.48},
                {"word": "checks", "start": 13.50, "end": 13.74},
                {"word": "out.", "start": 13.76, "end": 14.06},
            ],
        }],
    }), encoding="utf-8")
    sequence = {"body_sequence": [{
        "clip_id": "clip_craig",
        "source_start": 12.66,
        "source_end": 14.06,
        "text": "yep, your website checks out.",
    }]}

    result = pb.enrich_speech_sequence(sequence, str(temporal_index))

    (passage,) = result["body_sequence"]
    assert passage["source_start"] == pytest.approx(12.64)
    assert passage["text"] == "yep, your website checks out."


# ── Silence pads must not break the zone fit the model already met ────
#
# Defect (PR 2637 review, golden-monologue scenario): the F04 pads
# systematically EXPAND what plays, so a choice fitting under the
# declared speech ceiling came back over it - 7.35s of words plus
# 0.60s of pad against a 7.5s ceiling - and the post-bridge retry
# carried a violation the identical re-answer could never fix, failing
# the step after 3 attempts. The pads shrink to the ceiling the word
# spans already meet; nothing here weakens the gate itself.

def _pad_fixture(tmp_path):
    temporal_index = tmp_path / "temporal_index"
    temporal_index.mkdir()
    (temporal_index / "clip_pad.json").write_text(json.dumps({
        "duration": 10.0,
        "speech_regions": [{
            "start": 1.0,
            "end": 3.2,
            "words": [
                {"word": "alpha", "start": 1.0, "end": 1.2},
                {"word": "beta", "start": 2.0, "end": 2.2},
                {"word": "gamma", "start": 3.0, "end": 3.2},
            ],
        }],
    }), encoding="utf-8")
    sequence = {"body_sequence": [{
        "clip_id": "clip_pad",
        "source_start": 1.0,
        "source_end": 2.2,
        "text": "alpha beta",
    }]}
    return str(temporal_index), sequence


def test_full_pads_push_a_fitting_sequence_over_its_ceiling(tmp_path):
    """The defect, measured: 1.2s of words plus a 0.15s tail pad."""
    temporal_index, sequence = _pad_fixture(tmp_path)

    result = pb.enrich_speech_sequence(sequence, temporal_index)

    (passage,) = result["body_sequence"]
    assert passage["source_end"] == pytest.approx(2.35)
    assert pb.total_speech_seconds(result["body_sequence"]) == pytest.approx(
        1.35)
    assert pb.total_speech_seconds(result["body_sequence"]) > 1.3


def test_pads_shrink_to_the_ceiling_the_words_already_meet(tmp_path):
    temporal_index, sequence = _pad_fixture(tmp_path)

    result = pb.enrich_speech_sequence(
        sequence, temporal_index, speech_ceiling=1.3)

    (passage,) = result["body_sequence"]
    total = pb.total_speech_seconds(result["body_sequence"])
    assert total <= 1.3 + 1e-9
    (row,) = result["edit_point_report"]
    assert 0.0 < row["tail_pad_seconds"] < 0.15
    assert row["zone_capped"] is True
    assert row["zone_ceiling"] == pytest.approx(1.3)
    assert row["source_start"] == pytest.approx(passage["source_start"])
    assert row["source_end"] == pytest.approx(passage["source_end"])
    (align,) = result["alignment_report"]
    assert align["source_start"] == pytest.approx(passage["source_start"])
    assert align["source_end"] == pytest.approx(passage["source_end"])


def test_word_spans_over_ceiling_withhold_every_pad(tmp_path):
    """The refusal below must name the model's numbers, not this step's."""
    temporal_index, sequence = _pad_fixture(tmp_path)

    result = pb.enrich_speech_sequence(
        sequence, temporal_index, speech_ceiling=1.1)

    (passage,) = result["body_sequence"]
    assert passage["source_start"] == pytest.approx(1.0)
    assert passage["source_end"] == pytest.approx(2.2)
    (row,) = result["edit_point_report"]
    assert row["head_pad_seconds"] == pytest.approx(0.0)
    assert row["tail_pad_seconds"] == pytest.approx(0.0)
    assert row["zone_capped"] is True
