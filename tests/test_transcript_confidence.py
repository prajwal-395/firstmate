"""The selector sees how sure the transcriber was, and still no threshold.

Three directions at once: the confidence (and the script flag) REACH the
prompt, the word timings and ids do NOT come back with them, and NOTHING
fires on any of it (AGENTS.md 10.5). History: docs/evidence/transcription.md.
"""

import ast
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from library.processes.edit_video.run_pipeline import project_step_context
from library.tools.context_views import build_view
from library.tools.toon_serializer import json_to_toon
from library.tools import transcript_confidence as tc

STEP_DIR = REPO / "library" / "steps" / "step_3_04_select_reels"


def manifest() -> dict:
    return json.loads((STEP_DIR / "manifest.json").read_text(encoding="utf-8"))


def segment(speaker, start, end, text, item="clip-a", avg_logprob=None):
    """One transcript row, carrying everything the real document carries.

    The noise is IN the fixture on purpose: a test that leaves it out
    proves nothing about a projection whose whole job is to remove it.
    """
    words = []
    step = (end - start) / max(len(text.split()), 1)
    for i, word in enumerate(text.split()):
        words.append({"word": word, "start": round(start + i * step, 3),
                      "end": round(start + (i + 1) * step, 3), "timed": True})
    row = {
        "speaker": speaker,
        "text": text,
        "timeline_start": start,
        "timeline_end": end,
        "source_file": "/Volumes/Media/podcast media/LC4932.MXF",
        "source_start": start + 100.0,
        "source_end": end + 100.0,
        "resolve_item_id": item,
        "words": words,
        "read_from_words": False,
    }
    if avg_logprob is not None:
        row["avg_logprob"] = avg_logprob
    return row


# The measured shape of the disputed stretch, small enough to read.
# 281.07 is the clean reading of the payoff line; 284.13 is the same
# sentence again with eight Hangul characters in front of it, and it is
# where the reel was cut short.
CLEAN = ("So one which is Google is a search engine, and the other chat GPT "
         "is a decision engine.")
GARBLED = "같이 라고 on Google search, and chat GPT is a decision engine."
PICKUP = "decision engine i like that name i mean the stats show too"


def document(with_confidence: bool) -> dict:
    """The stretch, with and without what the ASR recorded about it."""
    conf = (-0.21, -0.83, -0.19) if with_confidence else (None, None, None)
    return {
        "measurement": "Speech transcribed by WhisperX. Times are timeline time.",
        "derived_from": {"duration_seconds": 400.0, "fps": 23.976,
                         "picture_holes": []},
        "segments": [
            segment("Akshita", 281.07, 284.07, CLEAN, "clip-b", conf[0]),
            segment("Akshita", 284.13, 288.06, GARBLED, "clip-b", conf[1]),
            segment("Craig", 290.73, 299.41, PICKUP, "clip-c", conf[2]),
        ],
    }


NOISE_KEYS = ("words", "source_file", "source_start", "source_end",
              "resolve_item_id", "read_from_words")


def projected(doc: dict) -> dict:
    """What step 3.04's prompt really carries, through the real projection."""
    from library.steps.step_3_04_select_reels.bridge import build_context

    tables = build_context({"timeline_transcript": doc})
    inputs = dict(tables)
    inputs["timeline_transcript"] = doc
    inputs["project_folder"] = "/nowhere/project"
    return project_step_context(inputs, manifest(), set(tables))


# ── Direction one: the confidence reaches the prompt ─────────────────


def test_the_transcribers_own_number_is_on_the_row_with_the_words():
    context = json_to_toon(projected(document(with_confidence=True)))
    assert "avg_logprob" in context
    for value in ("-0.21", "-0.83", "-0.19"):
        assert value in context, (
            f"{value} is what the transcriber recorded and the model "
            f"cannot see it")
    assert tc.CONFIDENCE_LEGEND in context, (
        "a number with no legend is a number the model has to guess the "
        "scale of")


def test_the_number_is_verbatim_and_nothing_is_derived_from_it():
    """The model gets the number and judges: no row is dropped, reordered
    or marked by it, and nothing is computed from it."""
    doc = document(True)
    doc["segments"][1]["avg_logprob"] = -9.5      # as bad as it gets
    view = build_view("spoken_lines",
                      {"timeline_transcript": doc})["spoken_lines"]
    assert [row["start"] for row in view["lines"]] == [281.07, 284.13, 290.73]
    assert view["lines"][1]["text"] == GARBLED
    assert [row["avg_logprob"] for row in view["lines"]] == [-0.21, -9.5,
                                                             -0.19]
    for row in view["lines"]:
        assert set(row) == {"speaker", "start", "end", "text", "avg_logprob"}, (
            "a row carries the ASR's own number and nothing computed "
            "from it")


def test_an_absent_confidence_is_STATED_rather_than_invented():
    """Every transcript written before this landed carries none, and
    saying so is the whole difference between a missing measurement and
    a measurement of zero."""
    view = build_view("spoken_lines",
                      {"timeline_transcript": document(False)})["spoken_lines"]
    assert view["transcription_confidence"] == tc.CONFIDENCE_ABSENT
    for row in view["lines"]:
        assert "avg_logprob" not in row, (
            "a row carries a confidence key on a transcript that records "
            "none, so absent and measured read the same")
    context = json_to_toon(projected(document(False)))
    assert tc.CONFIDENCE_ABSENT in context


# ── The other half: the script the line is written in ────────────────

def test_the_line_the_model_asked_about_is_the_line_that_is_flagged():
    flagged = tc.script_mismatches(document(False))
    assert len(flagged) == 1
    assert flagged[0]["start"] == 284.13 and flagged[0]["end"] == 288.06
    assert flagged[0]["speaker"] == "Akshita"
    assert flagged[0]["foreign"] == {"HANGUL": 4}


def test_the_flag_reaches_the_prompt_with_the_span_and_the_words():
    context = json_to_toon(projected(document(False)))
    assert "284.13-288.06" in context
    assert "HANGUL" in context
    assert tc.SCRIPT_LEGEND in context


def test_a_transcript_written_in_one_script_flags_nothing():
    """The ordinary case costs nothing to say, and a report that fires
    on clean material is worse than none."""
    clean = document(False)
    clean["segments"][1]["text"] = CLEAN
    assert tc.script_mismatches(clean) == []
    view = build_view("spoken_lines",
                      {"timeline_transcript": clean})["spoken_lines"]
    assert "script_mismatch" not in view


def test_the_dominant_script_comes_from_the_transcript_itself():
    """Not from anything declared about the project: a Korean episode
    with one English line must flag the ENGLISH one."""
    korean = document(False)
    for row in korean["segments"]:
        row["text"] = "이것은 한국어 문장입니다"
    korean["segments"][2]["text"] = "and this one is not"
    assert tc.dominant_script(korean["segments"]) == "HANGUL"
    flagged = tc.script_mismatches(korean)
    assert len(flagged) == 1 and flagged[0]["start"] == 290.73
    assert set(flagged[0]["foreign"]) == {"LATIN"}


# ── Direction two: the noise still cannot get through ────────────────

def test_no_word_timing_source_path_or_item_id_comes_back_with_it():
    output = projected(document(with_confidence=True))
    blob = json.dumps(output)
    for key in NOISE_KEYS:
        assert f'"{key}"' not in blob, (
            f"{key!r} is back in select_reels' prompt")
    assert ".MXF" not in blob and "/Volumes/" not in blob
    assert "clip-b" not in blob and "clip-c" not in blob
    assert set(output["timeline_transcript"]) == {"measurement"}


def test_the_revert_is_exactly_what_that_forbids():
    """Declaring the raw document again is the cheap way to buy a
    confidence column, and it must stay refused. Without this the
    assertions above would be describing a projection rather than
    gating one."""
    from library.steps.step_3_04_select_reels.bridge import build_context

    doc = document(with_confidence=True)
    reverted = manifest()
    reverted["context_fields"] = ["timeline_transcript", "reel_candidates"]
    tables = build_context({"timeline_transcript": doc})
    inputs = dict(tables)
    inputs["timeline_transcript"] = doc
    blob = json.dumps(project_step_context(inputs, reverted, set(tables)))
    for key in NOISE_KEYS:
        assert f'"{key}"' in blob, (
            f"the revert no longer carries {key!r}, so forbidding it "
            f"proves nothing")
    assert ".MXF" in blob and "clip-b" in blob


# ── Direction three: nothing fires on any of it ──────────────────────

def _numeric_comparisons(source: str) -> list:
    """Every `x < 0.5`-shaped expression in a module, by line.

    The unary minus is unwrapped deliberately: `avg_logprob` is
    NEGATIVE, so any threshold anyone were tempted to write on it would
    read `< -1.0` and arrive as a `UnaryOp` around the constant rather
    than as the constant itself.
    """
    def literal(node):
        while isinstance(node, ast.UnaryOp) and isinstance(node.op,
                                                           (ast.USub, ast.UAdd)):
            node = node.operand
        return (isinstance(node, ast.Constant)
                and isinstance(node.value, (int, float))
                and not isinstance(node.value, bool))

    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Compare):
            continue
        for operand in [node.left, *node.comparators]:
            if literal(operand):
                found.append((node.lineno, ast.unparse(node)))
    return found


def test_no_confidence_threshold_is_invented_anywhere():
    """`if confidence < 0.x` is the line the captain ruled out, and this
    is the module that would be where it went."""
    source = (REPO / "library" / "tools"
              / "transcript_confidence.py").read_text(encoding="utf-8")
    assert _numeric_comparisons(source) == [], (
        "a numeric comparison appeared in the module that publishes the "
        "transcriber's confidence")


# ── What the transcriber writes down, so a re-run has it ─────────────
#
# No current arm publishes segment confidence (docs/evidence/transcription.md);
# these keep old transcripts loading and the count honest.


def test_a_transcript_that_predates_this_still_loads():
    """`rebind_document` reconstructs every row, and every transcript on
    disk today was written without the field."""
    from library.tools.timeline_transcript import SpokenSegment

    old = segment("Craig", 1.0, 2.0, "hello", "clip-a")
    old["words"] = tuple(old["words"])
    assert SpokenSegment(**old).avg_logprob is None


def test_a_row_re_read_at_word_level_keeps_the_decodes_confidence():
    """`read_from_words` splits a row it did not re-hear, so every piece
    carries the confidence of the decode that produced those words."""
    from library.tools.timeline_ingest import TimelineClip
    from library.tools.timeline_transcript import read_from_words

    clip = TimelineClip(
        resolve_item_id="clip-a", track_type="video", track_index=1,
        track_name="Craig", speaker="Craig", source_file="/x.MXF",
        source_in=100.0, source_out=110.0, source_in_frame=2400,
        source_out_frame=2640, source_frames=None,
        timeline_start=0.0, timeline_end=10.0, name="clip")
    words = [{"word": w, "start": 1.0 + i, "end": 1.5 + i, "timed": True}
             for i, w in enumerate(["one", "two", "three"])]
    pieces = read_from_words("Craig", "one two three", words, [clip],
                             avg_logprob=-0.42)
    assert pieces and all(p.avg_logprob == -0.42 for p in pieces)


def test_the_document_says_how_many_rows_carry_one():
    """Zero and "nobody looked" read the same from outside."""
    from library.tools.timeline_transcript import (SpokenSegment,
                                                   transcript_document)

    class Snapshot:
        project_name = "p"
        timeline_name = "t"
        fps = 23.976
        duration = 400.0
        clips = ()

    rows = [SpokenSegment("Akshita", CLEAN, 281.07, 284.07, "/x.MXF", 1.0,
                          4.0, "clip-b", (), False, -0.21),
            SpokenSegment("Akshita", GARBLED, 284.13, 288.06, "/x.MXF", 4.0,
                          8.0, "clip-b", (), False, None)]
    built = transcript_document(Snapshot(), rows)
    assert built["segments_with_asr_confidence"] == 1
    assert built["segments"][0]["avg_logprob"] == -0.21
    assert built["segments"][1]["avg_logprob"] is None


# ── The THIRD reading of a missing number, and it is new ─────────────
#
# "No `avg_logprob`" now means one of three different things, and only
# one of them is fixed by re-running: written before the number was
# kept, a row the aligner produced without one, and - since the hybrid
# seam - a transcriber that emits none at all. A reader handed the first
# sentence for the third case is told a re-run would produce the number.
# It would not.

def hybrid_document() -> dict:
    """The same stretch, heard by the hybrid: no `avg_logprob` anywhere,
    a per-word `alignment_score` on every word, and a `transcription`
    block saying which arm answered."""
    from library.tools import hybrid_transcription
    from library.tools.transcript_confidence import ALIGNMENT_SCORE

    doc = document(with_confidence=False)
    for index, row in enumerate(doc["segments"]):
        for word in row["words"]:
            word[ALIGNMENT_SCORE] = 0.9 - 0.1 * index
    doc["transcription"] = {
        "arms": {"Akshita": hybrid_transcription.ARM_HYBRID,
                 "Craig": hybrid_transcription.ARM_HYBRID},
        "by_speaker": {},
        "asr_confidence": hybrid_transcription.ASR_CONFIDENCE_ABSENT,
    }
    return doc


def test_each_provenance_is_told_apart_and_gets_its_own_sentence():
    """Old transcript, hybrid-heard, MFA-timed: three readings of a
    missing number, and only the first is fixed by re-running."""
    assert tc.transcribed_by_hybrid(hybrid_document())
    assert not tc.transcribed_by_hybrid(document(with_confidence=False))
    assert not tc.transcribed_by_hybrid({})
    assert tc.timed_by_mfa(mfa_document())
    assert not tc.timed_by_mfa(hybrid_document())
    assert not tc.timed_by_mfa(document(with_confidence=False))
    assert not tc.timed_by_mfa({})
    assert tc.confidence_notice(document(True)) == tc.CONFIDENCE_LEGEND
    assert (tc.confidence_notice(document(False))
            == tc.CONFIDENCE_ABSENT)
    assert (tc.confidence_notice(hybrid_document())
            == tc.CONFIDENCE_ABSENT_HYBRID)
    assert tc.CONFIDENCE_ABSENT_HYBRID != tc.CONFIDENCE_ABSENT


def test_the_aligners_score_reaches_the_view_under_its_own_name():
    """It is measured, so it has a reader. It is NOT a confidence, so it
    does not travel under that heading."""
    view = build_view("spoken_lines",
                      {"timeline_transcript": hybrid_document()})["spoken_lines"]
    assert view["transcription_confidence"] == tc.CONFIDENCE_ABSENT_HYBRID
    assert "avg_logprob" not in view["lines"][0]
    assert view["lines"][0]["alignment_score"] == 0.9
    assert tc.ALIGNMENT_SCORE_LEGEND in view["alignment_score_legend"]


def test_the_score_is_never_published_beside_the_confidence():
    """Two numbers answering different questions under one heading is
    how one gets read as the other."""
    doc = document(with_confidence=True)
    for row in doc["segments"]:
        for word in row["words"]:
            word[tc.ALIGNMENT_SCORE] = 0.42
    view = build_view("spoken_lines",
                      {"timeline_transcript": doc})["spoken_lines"]
    assert "avg_logprob" in view["lines"][0]
    assert "alignment_score" not in view["lines"][0]
    assert "alignment_score_legend" not in view


def test_a_row_whose_words_were_never_aligned_scores_nothing():
    """A word whose timing was interpolated was not judged by the
    aligner, and counting it as a zero would say it was judged badly."""
    assert tc.line_alignment_score(
        {"words": [{"word": "one", tc.ALIGNMENT_SCORE: None}]}) is None
    assert tc.line_alignment_score({"words": []}) is None
    assert tc.line_alignment_score({}) is None


def test_the_aligners_score_stops_being_thrown_away():
    """`interpolate_untimed_words` rebuilt each word without it - the
    half of the discard this module recorded that was never repaired."""
    from library.tools.timeline_transcript import interpolate_untimed_words

    kept = interpolate_untimed_words(
        [{"word": "What", "start": 0.0, "end": 0.3, "score": 0.81},
         {"word": "changed"},
         {"word": "here", "start": 1.0, "end": 1.4, "score": 0.62}])
    assert [w[tc.ALIGNMENT_SCORE] for w in kept] == [0.81, None, 0.62]
    assert kept[1]["timed"] is False


# ── The FOURTH reading: MFA emits no per-word score ────────────────
#
# Adopting MFA costs the per-word aligner score - the one per-row
# number the hybrid arm otherwise publishes. `mfa align` writes no
# score, and no second pass would produce one, so the absence is
# recorded explicitly rather than left as a blank column to discover.

def mfa_document() -> dict:
    """The same stretch, timed by MFA: no `avg_logprob`, no per-word
    score, and a `transcription` block naming the aligner per speaker."""
    from library.tools import hybrid_transcription

    doc = document(with_confidence=False)
    doc["transcription"] = {
        "arms": {"Akshita": hybrid_transcription.ARM_HYBRID,
                 "Craig": hybrid_transcription.ARM_HYBRID},
        "aligners": {"Akshita": hybrid_transcription.ALIGNER_MFA,
                     "Craig": hybrid_transcription.ALIGNER_MFA},
        "by_speaker": {},
        "asr_confidence": hybrid_transcription.ASR_CONFIDENCE_ABSENT,
    }
    return doc


def test_an_mfa_transcript_states_the_missing_score_rather_than_omitting_it():
    """A column that simply is not there reads as broken alignment.
    The legend key is present with the absence stated instead."""
    view = build_view("spoken_lines",
                      {"timeline_transcript": mfa_document()})["spoken_lines"]
    assert view["alignment_score_legend"] == tc.ALIGNMENT_SCORE_ABSENT_MFA
    assert "alignment_score" not in view["lines"][0]
    assert (view["transcription_confidence"]
            == tc.CONFIDENCE_ABSENT_HYBRID)
