"""The selector sees how sure the transcriber was, and still no threshold.

Three directions at once: the confidence (and the script flag) REACH the
prompt, the word timings and ids do NOT come back with them, and NOTHING
fires on any of it (AGENTS.md 10.5). History: docs/evidence/transcription.md.
"""
from __future__ import annotations
import ast
import json
import sys
from pathlib import Path
import pytest
from library.tools import reel_hearing
from library.tools import transcript_fit
import os
import subprocess
from unittest.mock import MagicMock, patch


REPO = Path(__file__).resolve().parents[3]
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
            segment("SpeakerOne", 281.07, 284.07, CLEAN, "clip-b", conf[0]),
            segment("SpeakerOne", 284.13, 288.06, GARBLED, "clip-b", conf[1]),
            segment("SpeakerTwo", 290.73, 299.41, PICKUP, "clip-c", conf[2]),
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
    assert flagged[0]["speaker"] == "SpeakerOne"
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

    old = segment("SpeakerTwo", 1.0, 2.0, "hello", "clip-a")
    old["words"] = tuple(old["words"])
    assert SpokenSegment(**old).avg_logprob is None


def test_a_row_re_read_at_word_level_keeps_the_decodes_confidence():
    """`read_from_words` splits a row it did not re-hear, so every piece
    carries the confidence of the decode that produced those words."""
    from library.tools.timeline_ingest import TimelineClip
    from library.tools.timeline_transcript import read_from_words

    clip = TimelineClip(
        resolve_item_id="clip-a", track_type="video", track_index=1,
        track_name="SpeakerTwo", speaker="SpeakerTwo", source_file="/x.MXF",
        source_in=100.0, source_out=110.0, source_in_frame=2400,
        source_out_frame=2640, source_frames=None,
        timeline_start=0.0, timeline_end=10.0, name="clip")
    words = [{"word": w, "start": 1.0 + i, "end": 1.5 + i, "timed": True}
             for i, w in enumerate(["one", "two", "three"])]
    pieces = read_from_words("SpeakerTwo", "one two three", words, [clip],
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

    rows = [SpokenSegment("SpeakerOne", CLEAN, 281.07, 284.07, "/x.MXF", 1.0,
                          4.0, "clip-b", (), False, -0.21),
            SpokenSegment("SpeakerOne", GARBLED, 284.13, 288.06, "/x.MXF", 4.0,
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
        "arms": {"SpeakerOne": hybrid_transcription.ARM_HYBRID,
                 "SpeakerTwo": hybrid_transcription.ARM_HYBRID},
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
        "arms": {"SpeakerOne": hybrid_transcription.ARM_HYBRID,
                 "SpeakerTwo": hybrid_transcription.ARM_HYBRID},
        "aligners": {"SpeakerOne": hybrid_transcription.ALIGNER_MFA,
                     "SpeakerTwo": hybrid_transcription.ALIGNER_MFA},
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


# --------------------------------------------------------------------------
# From test_transcript_corrections.py
#
# Corrections that survive re-transcription and every regeneration.
#
# The captain's question: "where does a correction live so that a
# re-render carries it?" Editing a caption's props is undone by the next
# render; editing the transcript is undone by the next transcription.
# Neither is the root. A correction lives in the project's
# `learned_context/` (pipeline-owned, never scratch) as a `correction`
# kind with a machine-readable `source`, and is applied deterministically
# at the transcript root - so every downstream consumer (captions,
# explainer stages, motion-graphics anchors, the model-read context)
# reads the corrected words with no changes of its own.

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))


def _doc_with_lucy():
    return {
        "segments": [
            {
                "speaker": "SpeakerTwo",
                "text": "we're calling the lucy visibility system",
                "timeline_start": 10.0,
                "timeline_end": 13.0,
                "source_file": "LCATL0013.MXF",
                "source_start": 100.0,
                "source_end": 103.0,
                "resolve_item_id": "clip-1",
                "words": [
                    {"word": "we're", "start": 10.0, "end": 10.3,
                     "timed": True},
                    {"word": " calling", "start": 10.3, "end": 10.7,
                     "timed": True},
                    {"word": " the", "start": 10.7, "end": 10.9,
                     "timed": True},
                    {"word": " lucy", "start": 10.9, "end": 11.3,
                     "timed": True},
                    {"word": " visibility", "start": 11.3, "end": 11.9,
                     "timed": True},
                    {"word": " system", "start": 11.9, "end": 12.4,
                     "timed": True},
                ],
                "read_from_words": False,
                "avg_logprob": -0.2,
            },
            {
                "speaker": "SpeakerTwo",
                "text": "Lucy helps teams ship",
                "timeline_start": 20.0,
                "timeline_end": 22.0,
                "source_file": "LCATL0013.MXF",
                "source_start": 200.0,
                "source_end": 202.0,
                "resolve_item_id": "clip-2",
                "words": [
                    {"word": "Lucy", "start": 20.0, "end": 20.4,
                     "timed": True},
                    {"word": " helps", "start": 20.4, "end": 20.8,
                     "timed": True},
                ],
                "read_from_words": False,
                "avg_logprob": -0.1,
            },
        ],
    }


def test_spelling_correction_is_recorded_and_read_back_everywhere(tmp_path):
    """Recorded as a learned correction; the bias strings and the model
    note both carry it, and an empty store renders nothing."""
    from library.tools import transcript_corrections as tc
    assert tc.render_for_model(str(tmp_path)) == ""
    rec = tc.record_spelling(
        str(tmp_path), heard="lucy", correct="Lucie",
        reason="captain marker at frame 1516: the company Lucie Content, "
        "not a person")
    assert rec["kind"] == "correction"
    assert rec["status"] == "active"
    assert rec["source"]["heard"] == "lucy"
    assert rec["source"]["correct"] == "Lucie"
    store = json.loads(
        (tmp_path / "learned_context" / "learnings.json").read_text(
            encoding="utf-8"))
    assert len(store) == 1
    prompt, hotwords = tc.bias_strings(str(tmp_path))
    assert "Lucie" in prompt
    assert "Lucie" in hotwords
    note = tc.render_for_model(str(tmp_path))
    assert "lucy" in note and "Lucie" in note


def test_apply_rewrites_segment_text_and_words(tmp_path):
    from library.tools import transcript_corrections as tc
    tc.record_spelling(
        str(tmp_path), heard="lucy", correct="Lucie",
        reason="captain marker at frame 1516")
    doc = _doc_with_lucy()
    report = tc.apply_to_document(doc, str(tmp_path))
    assert report["replacements"] >= 2
    assert "lucy" not in doc["segments"][0]["text"].lower().replace(
        "lucie", "")
    assert "Lucie" in doc["segments"][0]["text"]
    words = [w["word"] for w in doc["segments"][0]["words"]]
    assert any("Lucie" in w for w in words)
    assert not any(w.strip().lower() == "lucy" for w in words)
    # Title-case heard is corrected too.
    assert "Lucie" in doc["segments"][1]["text"]
    # Timings untouched: a correction respells, never re-times.
    assert doc["segments"][0]["words"][3]["start"] == 10.9
    # Idempotent: a second pass finds nothing to do.
    again = tc.apply_to_document(doc, str(tmp_path))
    assert again["replacements"] == 0


def test_apply_preserves_possessives_and_punctuation():
    from library.tools import transcript_corrections as tc
    corrections = [{"heard": "lucy", "correct": "Lucie", "id": "lc-0001"}]
    out, n = tc.apply_spelling("we love lucy's work, lucy!", corrections)
    assert out == "we love Lucie's work, Lucie!"
    assert n == 2
    out, n = tc.apply_spelling("LUCY SHOUTS", corrections)
    assert out == "LUCIE SHOUTS"
    # Substrings are not words: "lucy's" handled, "hallucinate" untouched.
    out, _ = tc.apply_spelling("do not hallucinate", corrections)
    assert out == "do not hallucinate"


def test_suppression_boundary_does_not_split_an_apostrophe_contraction():
    from library.tools import transcript_corrections as tc

    document = {"segments": [{
        "speaker": "SpeakerTwo",
        "text": "I've I thought",
        "words": [
            {"word": "I've", "start": 1.0, "end": 1.4},
            {"word": "I", "start": 1.5, "end": 1.6},
            {"word": "thought", "start": 1.7, "end": 2.0},
        ],
    }]}
    suppression = [{
        "heard": "I",
        "scope": {"speaker": "SpeakerTwo", "surface": "I",
                  "prev": "I've", "next": "thought"},
    }]

    report = tc.apply_suppressions(document, suppression)

    segment = document["segments"][0]
    assert segment["text"] == "I've thought"
    assert segment["words"][0].get("display") is not False
    assert segment["words"][1]["display"] is False
    assert report["suppressed"] == 1


def test_transcribe_carries_bias_arguments_unread(monkeypatch):
    """Decoder biasing left with the fallback's decoder on 2026-09-24,
    so `initial_prompt`/`hotwords` are carried but read by nothing -
    and that carrying is itself the contract: a caller passing the
    project's recorded corrections must not TypeError, and the answer
    must not change for them. The enforcement half is the post pass
    (`apply_to_document`, tested above)."""
    from library.tools import hybrid_transcription
    from library.tools import timeline_transcript as tt

    def _refuse(audio_path, aligner, label=""):
        raise hybrid_transcription.FallbackRequired(
            hybrid_transcription.HEARD_NOTHING,
            "the transcriber returned no words")

    monkeypatch.setattr(
        hybrid_transcription, "transcribe_and_align", _refuse)
    aligned, record = tt.transcribe_audio(
        Path("/tmp/nowhere.wav"), initial_prompt="Lucie Content",
        hotwords="Lucie Content")
    assert aligned["segments"] == []
    assert record["fell_back_because"]["trigger"] == \
        hybrid_transcription.HEARD_NOTHING


def test_keep_exclusion_trims_an_edge_and_drops_an_interior_one():
    """Frame 528: "so what do they" at the range head goes, the rest of
    the audio stays - a trim, not a drop. An interior exclusion would
    split one reel into two, a new editorial decision: dropped with the
    reason."""
    from library.tools import transcript_corrections as tc
    moments = [{"start": 20.0, "end": 40.0, "slug": "reel-09"}]
    exclusions = [{"start": 20.0, "end": 23.5, "id": "lc-0002",
                   "reason": "captain: feels like a mistake"}]
    kept, dropped = tc.apply_keep_exclusions(moments, exclusions)
    assert len(kept) == 1 and not dropped
    assert kept[0]["start"] == 23.5
    assert kept[0]["end"] == 40.0
    assert kept[0]["trimmed_by"] == ["lc-0002"]

    exclusions = [{"start": 28.0, "end": 30.0, "id": "lc-0003",
                   "reason": "captain: mistake in the middle"}]
    kept, dropped = tc.apply_keep_exclusions(moments, exclusions)
    assert not kept
    assert len(dropped) == 1
    assert "lc-0003" in dropped[0]["reason"]


def _reel_transcript():
    segments = []
    for i in range(4):
        start = 20.0 + i * 5.0
        segments.append({
            "speaker": "SpeakerTwo" if i % 2 == 0 else "SpeakerOne",
            "text": f"line number {i} here",
            "timeline_start": start,
            "timeline_end": start + 5.0,
            "source_file": "LCATL0013.MXF",
            "source_start": start,
            "source_end": start + 5.0,
            "resolve_item_id": f"clip-{i}",
            "words": [{"word": "here", "start": start + 1.0,
                       "end": start + 1.5, "timed": True}],
            "read_from_words": False,
        })
    return {"derived_from": {"duration_seconds": 100.0},
            "segments": segments}


def test_select_reels_post_bridge_trims_a_recorded_exclusion(tmp_path):
    """Durability for keep ranges, by demonstration: with the exclusion
    on file, a regenerated proposal opens past the struck fragment -
    nobody re-argues it, nobody touches the render."""
    from library.tools import transcript_corrections as tc
    tc.record_keep_exclusion(
        str(tmp_path), 20.0, 23.5,
        reason="captain frame 528: 'so what do they' feels like a mistake")
    import importlib.util
    path = (REPO_ROOT / "library" / "steps" / "step_3_04_select_reels"
            / "post_bridge.py")
    spec = importlib.util.spec_from_file_location(
        "select_reels_post_bridge", str(path))
    post_bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(post_bridge)
    out = post_bridge.resolve(
        {"moments": [{"start": 21.0, "end": 39.0, "slug": "reel-09",
                      "reason": "the website point"}]},
        {"timeline_transcript": _reel_transcript(),
         "project_folder": str(tmp_path)})
    moments = out["reel_selection"]["moments"]
    assert len(moments) == 1
    # 23.5 sits inside the 20-25s segment, so the trimmed edge moves
    # onto the segment grid away from the struck seconds: 25.0.
    assert moments[0]["timeline_start"] == 25.0
    assert out["reel_selection"]["dropped"] == []
    # The struck line never reaches the preview the captain approves.
    assert "line number 0" not in moments[0]["transcript_preview"]


def test_played_speech_reads_lucie_after_correction(tmp_path):
    """The captain's demonstration at unit level: the SAME list the
    builder, the caption pass and the verifier all read
    (`reel_quality_bar.played_speech`) says "lucy" before the fix and
    "Lucie" after - with no render prop touched."""
    from library.tools import transcript_corrections as tc
    from library.tools.reel_quality_bar import played_speech

    class Moment:
        timeline_start, timeline_end = 9.0, 14.0
        call_to_action = None

    doc = _doc_with_lucy()
    before = played_speech(Moment(), doc)
    assert any("lucy" in line["text"].lower() for line in before)
    tc.record_spelling(
        str(tmp_path), heard="lucy", correct="Lucie",
        reason="captain marker at frame 1516")
    tc.apply_to_document(doc, str(tmp_path))
    after = played_speech(Moment(), doc)
    assert after
    assert all("lucy" not in line["text"].lower().replace("lucie", "")
               for line in after)
    assert any("Lucie" in line["text"] for line in after)
    with_words = played_speech(Moment(), doc, with_words=True)
    assert any("Lucie" in w.get("word", "")
               for line in with_words for w in line.get("words", []))


def test_correction_reaches_the_motion_graphics_planner_prompt(tmp_path):
    """The authored-copy half: a `read_by: ["*"]` correction is routed
    to the MG planner through `project_context`, so model-written copy
    spells it right."""
    from library.tools import learned_context as lc
    from library.tools import transcript_corrections as tc
    tc.record_spelling(
        str(tmp_path), heard="lucy", correct="Lucie",
        reason="captain marker at frame 1516")
    prompt_text = lc.render_for_prompt(
        str(tmp_path), "render_motion_graphics")
    assert "Lucie" in prompt_text


def test_reel_semantic_request_carries_the_correction(tmp_path):
    """The reel path builds its own ask outside the runner, so the
    correction is written into the request context directly - same
    store, same verdict, no second mechanism."""
    from library.tools import reel_semantic_visual as sem
    from library.tools import transcript_corrections as tc
    spine = {"structure": [{
        "position": 1, "block_type": "speech",
        "timeline_start": 0.0, "timeline_end": 4.0,
        "content": {"text": "we're calling the lucy visibility system"},
    }]}
    plain = sem.bridge_context(spine, "", 23.976)
    assert plain["transcript_corrections"] == ""
    tc.record_spelling(
        str(tmp_path), heard="lucy", correct="Lucie",
        reason="captain marker at frame 1516")
    context = sem.bridge_context(spine, str(tmp_path), 23.976)
    assert "Lucie" in context["transcript_corrections"]


# ── The brand name on a HYBRID transcript ────────────────────────────
#
# The on-device transcriber writes `lucy` 14 of 14 times and has NO
# prompt to bias, where WhisperX's `initial_prompt` recovers 12 of 14.
# So the after-the-fact repair is the only lever the hybrid arm has, and
# it is VERIFIED here rather than assumed - which is the whole reason
# these tests exist beside the seam that made it load-bearing.

def _hybrid_doc_with_lucy():
    """The same rows a hybrid pass writes: no `avg_logprob` anywhere, a
    per-word `alignment_score` on every word, and the `transcription`
    block naming the arm."""
    from library.tools import hybrid_transcription
    from library.tools.transcript_confidence import ALIGNMENT_SCORE

    doc = _doc_with_lucy()
    for row in doc["segments"]:
        row.pop("avg_logprob", None)
        for word in row["words"]:
            word[ALIGNMENT_SCORE] = 0.72
    doc["transcription"] = {
        "arms": {"SpeakerTwo": hybrid_transcription.ARM_HYBRID},
        "by_speaker": {},
        "asr_confidence": hybrid_transcription.ASR_CONFIDENCE_ABSENT,
    }
    return doc


def test_the_repair_reaches_a_hybrid_transcript_and_touches_only_text(
        tmp_path):
    """Respelling inherits correct timings (measured: 11 of 12 moved by
    zero ms), so timings and aligner scores stay, and the pass never
    fills in the confidence the transcriber never produced."""
    from library.tools import transcript_corrections as tc
    from library.tools.transcript_confidence import ALIGNMENT_SCORE

    tc.record_spelling(str(tmp_path), heard="lucy", correct="Lucie",
                       reason="captain marker at frame 1516")
    doc = _hybrid_doc_with_lucy()
    before = [(w["start"], w["end"], w[ALIGNMENT_SCORE])
              for w in doc["segments"][0]["words"]]
    report = tc.apply_to_document(doc, str(tmp_path))
    assert report["replacements"] >= 2
    assert "Lucie" in doc["segments"][0]["text"]
    assert not any(w["word"].strip().lower() == "lucy"
                   for w in doc["segments"][0]["words"])
    assert [(w["start"], w["end"], w[ALIGNMENT_SCORE])
            for w in doc["segments"][0]["words"]] == before
    assert all("avg_logprob" not in row or row["avg_logprob"] is None
               for row in doc["segments"])


def test_an_uncertain_model_proposal_records_pending_not_applied(tmp_path):
    """The Sheehan default, corrected: an unsure model proposal - a
    respelling or a suppression - is recorded for review, never enforced
    (no prompt, no pass, no bias) until a human promotes it."""
    from library.tools import learned_context as lc
    from library.tools import transcript_corrections as tc

    rec = tc.record_spelling(
        str(tmp_path), heard="Sheehan", correct="she even",
        reason="MODEL, NEEDS CAPTAIN CONFIRMATION: parallel take reads "
        "she even; retire if a client name",
        proposed_by="model", status=lc.PENDING)
    assert rec["status"] == "pending"
    assert rec["kind"] == "mistake_fix"
    assert tc.spelling_corrections(str(tmp_path)) == []
    doc = _doc_with_lucy()
    report = tc.apply_to_document(doc, str(tmp_path))
    assert report["replacements"] == 0
    prompt, hotwords = tc.bias_strings(str(tmp_path))
    assert (prompt, hotwords) == ("", "")
    assert tc.render_for_model(str(tmp_path)) == ""
    # Promotion enforces it from the next run.
    lc.promote(str(tmp_path), rec["id"],
               reason="Captain: it's not a name, keep the fix.")
    assert [c["correct"] for c in tc.spelling_corrections(
        str(tmp_path))] == ["she even"]

    rec = tc.record_display_suppression(
        str(tmp_path), "different",
        reason="MODEL, BORDERLINE: reduplication; retire if emphasis",
        scope={"speaker": "SpeakerOne", "surface": "different",
               "prev": "different", "next": "sources"},
        proposed_by="model", status=lc.PENDING)
    assert rec["status"] == "pending"
    assert tc.suppressions(str(tmp_path)) == []
    lc.promote(str(tmp_path), rec["id"],
               reason="Captain: confirmed, it is a false start.")
    assert len(tc.suppressions(str(tmp_path))) == 1


def test_pending_is_refused_a_captains_name(tmp_path):
    """A captain's note IS the confirmation - recording it pending
    would hold a decided verdict for a review that already happened."""
    import pytest

    from library.tools import learned_context as lc
    from library.tools import transcript_corrections as tc

    with pytest.raises(lc.LearnedContextError):
        tc.record_spelling(
            str(tmp_path), heard="lucy", correct="Lucie",
            reason="captain marker", proposed_by="captain",
            status=lc.PENDING)
    with pytest.raises(lc.LearnedContextError):
        tc.record_display_suppression(
            str(tmp_path), "um", reason="captain marker",
            proposed_by="captain", status=lc.PENDING)


# --------------------------------------------------------------------------
# From test_transcript_duration_anomaly.py
#
# Transcript duration anomalies warn before captions are planned.
#
# A row's own words are scored against its own local rate, so Reel 12's
# stretched "hallucinate"/"probably" warn while an ordinary row's naturally
# long word stays silent: the threshold is derived per row, never constant.
# `library/tools/transcript_duration_anomaly.py`, wired into
# `generate_subtitles`. History: docs/evidence/transcription.md.

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


from library.steps.step_4_01_plan_subtitles.step import generate_subtitles
from library.tools.transcript_duration_anomaly import flag_duration_anomalies


def _row(tokens, position=8, speaker="SpeakerOne"):
    """Stamp (word, duration) pairs onto cumulative timeline seconds."""
    words = []
    cursor = 1843.0
    for token, span in tokens:
        words.append({"word": token, "start": round(cursor, 3),
                      "end": round(cursor + span, 3), "timed": True})
        cursor += span + 0.02
    return {"position": position, "speaker": speaker,
            "source_start": 1843.91, "source_end": 1857.27,
            "text": " ".join(token for token, _ in tokens),
            "words": words}


# The Reel-12 row's shape: 46 words, bulk clustered at the speaker's
# rate, "hallucinate" at 1.55s the maximum, "probably" at 0.72s the
# runner-up, next-longest content word ("however,") 0.58s - and the
# squeezed stutter duplicate "probably" at 0.16s. Durations are the
# forensics report's numbers
# (`data/vep-ft-r12-missing-words-unexplained/report.md`); the ordinary
# tokens are filler at the same rate, which is all the detector reads.
R12_TOKENS = [
    ("and", 0.09), ("to", 0.11), ("a", 0.12), ("so", 0.13),
    ("of", 0.14), ("it's", 0.15), ("probably", 0.16), ("the", 0.17),
    ("gonna", 0.18), ("models", 0.1868), ("that", 0.1936),
    ("make", 0.2004), ("up", 0.2072), ("things", 0.214),
    ("say", 0.2208), ("which", 0.2276), ("over", 0.22),
    ("most", 0.2344), ("likely", 0.2412), ("people", 0.248),
    ("think", 0.2548), ("about", 0.2616), ("this", 0.2684),
    ("then", 0.27), ("when", 0.2752), ("they", 0.282),
    ("talk", 0.2888), ("with", 0.2956), ("what", 0.3024),
    ("still", 0.31), ("you", 0.3092), ("know", 0.316),
    ("really", 0.3228), ("just", 0.3296), ("like", 0.3364),
    ("because", 0.35), ("another", 0.37), ("something", 0.39),
    ("actually", 0.42), ("around", 0.45), ("before", 0.48),
    ("without", 0.52), ("together", 0.56), ("however,", 0.58),
    ("hallucinate", 1.55), ("probably", 0.72),
]


def test_the_r12_row_flags_both_complaints_against_its_local_rate():
    assert len(R12_TOKENS) == 46
    report = flag_duration_anomalies([_row(R12_TOKENS)])
    assert report["rows_checked"] == 1
    flagged = [(w["word"], w["duration_seconds"]) for w in report["warnings"]]
    assert {word for word, _ in flagged} >= {"hallucinate", "probably"}
    # The 0.58s content word is long but local: not an outlier.
    assert not [w for w in flagged if w[0] == "however,"]
    # The squeezed 0.16s duplicate is short: one-sided, never flags.
    assert ("probably", 0.16) not in flagged

    hallucinate = next(w for w in report["warnings"]
                       if w["word"] == "hallucinate")
    assert hallucinate["duration_seconds"] == 1.55
    assert hallucinate["speaker"] == "SpeakerOne"
    assert hallucinate["position"] == 8
    # Derived from this row, not a constant: the row median, far below
    # either flagged duration.
    assert hallucinate["local_median_seconds"] == pytest.approx(
        0.27, abs=0.05)
    assert hallucinate["local_median_seconds"] < 0.72
    assert hallucinate["modified_z"] > 3.5


SLOW_ROW = [
    ("well", 0.38), ("yesterday", 0.68), ("we", 0.30),
    ("talked", 0.52), ("about", 0.40), ("the", 0.32),
    ("whole", 0.48), ("afternoon", 0.62), ("and", 0.34),
    ("it", 0.30), ("was", 0.36), ("really", 0.50),
    ("quite", 0.42), ("something", 0.58), ("else", 0.44),
    ("entirely", 0.60), ("though", 0.46), ("indeed", 0.55),
]
HEALTHY_FAST_ROW = [(f"w{i}", 0.18 + (i % 7) * 0.03) for i in range(20)]


def test_ordinary_rows_stay_silent():
    """A slow speaker's ~0.7s word is unremarkable at a ~0.45s median -
    the same seconds the R12 row flags. One number cannot pass this row
    and flag that one."""
    for tokens in (SLOW_ROW, HEALTHY_FAST_ROW):
        report = flag_duration_anomalies([_row(tokens, position=3,
                                               speaker="SpeakerTwo")])
        assert report["rows_checked"] == 1
        assert report["warnings"] == []


def test_degenerate_rows_are_skipped_never_warned():
    """Too short, nothing timed but punctuation, or zero spread."""
    for row in (
            _row([("hi", 0.2), ("there", 5.0)]),
            {"position": 1, "words": [
                {"word": "hi", "start": 1.0, "end": 1.2},
                {"word": "...", "start": 1.2, "end": 2.2},
                {"word": "there"}]},
            _row([(f"w{i}", 0.25) for i in range(10)])):
        report = flag_duration_anomalies([row])
        assert report["warnings"] == []
        assert report["rows_skipped"] == 1
        assert report["rows_checked"] == 0


# ── planned through generate_subtitles, the load-bearing wiring ──

def _spine(tokens, position=8):
    """One speech block carrying `tokens` as its word timestamps."""
    words = []
    cursor = 100.0
    for token, span in tokens:
        words.append({"word": token, "source_start": round(cursor, 3),
                      "source_end": round(cursor + span, 3)})
        cursor += span + 0.02
    source_end = cursor
    return {"structure": [{
        "block_type": "speech",
        "position": position,
        "timeline_start": 47.0,
        "timeline_end": 47.0 + (source_end - 100.0),
        "source_start": 100.0,
        "source_end": source_end,
        "clip_id": "LC4932.MXF",
        "alignment_method": "mfa",
        "word_timestamps": words,
        "content": {"text": " ".join(token for token, _ in tokens)},
    }]}


def test_generate_subtitles_says_so_on_the_run_and_only_then(capsys):
    generate_subtitles(_spine(R12_TOKENS), caption_case="lowercase")
    err = capsys.readouterr().err
    assert "hallucinate" in err
    assert "probably" in err

    generate_subtitles(_spine(HEALTHY_FAST_ROW), caption_case="lowercase")
    err = capsys.readouterr().err
    assert "duration-anomaly" not in err
    assert "aligner stretch" not in err


# --------------------------------------------------------------------------
# From test_transcript_fit.py
#
# A transcript row whose text does not fit its audio, measured.
#
# Every number here was measured on the captain's shipped `geo-podcast`
# transcript on 2026-09-16 and the fixture is a trimmed slice of it, not a
# synthetic one: `tests/fixtures/reel_hearing/README.md` says exactly what
# was kept and what was dropped.
#
# Nothing here runs an aligner, opens audio, calls a model or touches a
# real project.

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "reel_hearing"


@pytest.fixture
def episode():
    return json.loads((FIXTURES / "episode.transcript.json").read_text(
        encoding="utf-8"))


@pytest.fixture
def report(episode):
    return transcript_fit.scan(episode)


# ── 1. The class the hybrid report measured and nobody read ──────────

def test_the_unfitted_rows_are_counted_exactly(report):
    """15 rows on the shipped transcript, 13 whole and 2 partial - among
    them the Reel 26 row that cost a delivered reel its captions, and a
    hallucinated clause whose five invented tokens the aligner found no
    audio for while its ten real words kept their timings (the class a
    `words: []` check reported as fine)."""
    assert report["unfitted_rows"] == 15
    assert report["whole_rows_lost"] == 13
    assert report["parts_of_rows_lost"] == 2
    assert report["words_with_no_timing"] == 217

    rows = [r for r in report["rows_detail"]
            if "answers specific questions" in r["text"]]
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["kind"] == transcript_fit.WHOLE_ROW
    assert row["text_words"] == 12
    assert row["timed_words"] == 0
    assert row["span_seconds"] == pytest.approx(0.92, abs=0.01)

    partial = [r for r in report["rows_detail"]
               if r["kind"] == transcript_fit.PART_OF_ROW]
    assert len(partial) == 2, partial
    worst = max(partial, key=lambda r: r["untimed_words"])
    assert worst["untimed_words"] == 5
    assert worst["timed_words"] == 10


# ── 2. There is no threshold, and there is a reference ───────────────

def test_the_comparison_has_no_false_positive_mechanism(episode):
    """No row anywhere carries MORE timings than its text has words.

    Both sides are the same whitespace tokenisation, so a shortfall is
    always a real shortfall. Measured over all 940 rows of the shipped
    transcript: 925 exact, 15 short, 0 long. If this ever fails, the
    detector has become a heuristic and needs a threshold it does not
    have today.
    """
    for segment in episode["segments"]:
        text_words = len(str(segment.get("text") or "").split())
        if not text_words:
            continue
        assert len(segment.get("words") or []) <= text_words, segment["text"]


def test_the_reference_rate_is_the_documents_own(report):
    """The fastest row that DID fit, not a constant: 11.76 words per
    second. Twelve unfitted rows assert more than that - the worst,
    eighteen words in sixty milliseconds - and three do not, which is the
    distinction a threshold would destroy."""
    assert report["fastest_fitted_words_per_second"] == pytest.approx(11.76,
                                                                     abs=0.01)
    assert report["rows_asserting_a_rate_no_fitted_row_reaches"] == 12
    worst = max(report["rows_detail"],
                key=lambda r: r["implied_words_per_second"] or 0)
    assert worst["implied_words_per_second"] == pytest.approx(300.0)
    assert worst["text_words"] == 18


# ── 3. What is NOT a finding, and is reported beside them ────────────

def test_the_numerals_the_aligner_cannot_pronounce_are_not_findings(
        episode, report):
    """All 19 are numerals, every one is PLACED, and none is a defect.

    wav2vec2 aligns characters against audio and `10` has none, so the
    transcriber interpolates. Folding these into the finding would put
    nineteen false rows in front of a reader on every episode.
    """
    assert report["interpolated_words"] == 19
    placed = transcript_fit.interpolated_words(episode)
    assert all(any(character.isdigit() for character in str(word["word"]))
               for word in placed), placed
    assert all(word["start"] is not None and word["end"] is not None
               for word in placed)
    # And none of them makes its own row unfitted.
    for segment in episode["segments"]:
        if any(not word.get("timed", True)
               for word in (segment.get("words") or [])):
            assert transcript_fit.row_fit(segment) is None, segment["text"]


# ── 4. A row is measured, never guessed at ───────────────────────────

def test_row_fit_measures_only_what_the_row_asserts(episode):
    fitted = [seg for seg in episode["segments"]
              if len(seg.get("words") or []) == len(seg["text"].split())]
    assert fitted
    assert all(transcript_fit.row_fit(seg) is None for seg in fitted)
    assert transcript_fit.row_fit({"text": "   ", "words": []}) is None
    # No source times (one real row is `source_file: null`): the
    # timeline span still gives a duration, or the row is under-reported.
    row = transcript_fit.row_fit({
        "text": "one two three four", "words": [],
        "source_start": None, "source_end": None,
        "timeline_start": 10.0, "timeline_end": 11.0})
    assert row is not None
    assert row.span_seconds == pytest.approx(1.0)
    assert row.implied_rate == pytest.approx(4.0)
    # No span at all: a row, asserting no rate.
    row = transcript_fit.row_fit({"text": "one two", "words": []})
    assert row is not None
    assert row.implied_rate is None
    assert row.as_row()["implied_words_per_second"] is None


# ── 5. The reel's own rows, without a render ─────────────────────────

def test_the_reels_rows_are_found_from_the_PLAN_alone(episode):
    """No audio, no render, no transcription: Reel 26's defect was
    knowable the moment `build_reels` serialized the timeline. A row the
    reel does not play is not reported."""
    timeline = json.loads((FIXTURES / "reel26.timeline.json").read_text(
        encoding="utf-8"))
    transcript = json.loads((FIXTURES / "reel26.transcript.json").read_text(
        encoding="utf-8"))
    played = reel_hearing.rows_played(timeline, transcript)
    assert len(played) == 1, played
    assert played[0]["reel_start"] == pytest.approx(25.62, abs=0.01)
    assert played[0]["untimed_words"] == 12
    assert len(reel_hearing.rows_played(timeline, episode)) < len(
        transcript_fit.unfitted_rows(episode))


# ── 6. It reports, and refuses only what is not there ────────────────

def test_the_module_reports_off_disk_and_refuses_a_missing_transcript(
        tmp_path, capsys):
    """Exit zero whatever it found: a report is not a failure."""
    assert transcript_fit.main([str(tmp_path / "nothing.json")]) == 1
    assert "REFUSED" in capsys.readouterr().err
    document = tmp_path / "transcript.json"
    document.write_text(json.dumps({"segments": [
        {"text": "one two three", "words": [], "source_start": 0.0,
         "source_end": 0.1}]}), encoding="utf-8")
    assert transcript_fit.main([str(document), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["unfitted_rows"] == 1


# --------------------------------------------------------------------------
# From test_transcript_hygiene.py
#
# The finding half proposes; a human (or keyed run) disposes.
#
# `library/tools/transcript_hygiene.py` (Gap 2 of the 2026-09-19
# field-test brief): deterministic shape-based nomination plus the
# model's sentence-level judgement, recorded as `mistake_fix` with
# evidence and provenance. These tests prove nomination nominates by
# shape rather than text, the verdict pipeline refuses what it cannot
# honour, recording attributes honestly, and a keyless environment
# degrades to UNEVALUATED rather than guessing.

sys.path.insert(0, ".")

from library.tools import transcript_hygiene as hy


def _project(tmp_path):
    root = str(tmp_path)
    os.makedirs(os.path.join(root, "learned_context"), exist_ok=True)
    with open(os.path.join(root, "learned_context", "learnings.json"),
              "w", encoding="utf-8") as handle:
        json.dump([], handle)
    return root


def _doc():
    return {"transcription": {"arms": {"A": "hybrid"},
                              "aligners": {"A": "mfa"}},
            "segments": [
                {"speaker": "SpeakerOne",
                 "text": "make niche qu niche questions",
                 "words": [
                     {"word": "make", "start": 1.0, "end": 1.2},
                     {"word": "niche", "start": 1.2, "end": 1.5},
                     {"word": "qu", "start": 1.5, "end": 1.7},
                     {"word": "niche", "start": 1.7, "end": 2.0},
                     {"word": "questions", "start": 2.0, "end": 2.5}]},
                {"speaker": "SpeakerTwo",
                 "text": "better than X, Y, and Z",
                 "words": [
                     {"word": "better", "start": 3.0, "end": 3.3},
                     {"word": "than", "start": 3.3, "end": 3.5},
                     {"word": "X,", "start": 3.5, "end": 3.7},
                     {"word": "Y,", "start": 3.7, "end": 3.9},
                     {"word": "and", "start": 3.9, "end": 4.0},
                     {"word": "Z.", "start": 4.0, "end": 4.2}]},
                {"speaker": "SpeakerTwo",
                 "text": "I I think so",
                 "words": [
                     {"word": "I", "start": 5.0, "end": 5.1},
                     {"word": "I", "start": 5.1, "end": 5.2},
                     {"word": "think", "start": 5.2, "end": 5.5},
                     {"word": "so", "start": 5.5, "end": 5.7}]},
            ]}


def test_nominate_measures_shapes_not_words():
    candidates = hy.nominate(_doc())
    # Placeholders nominate (single letters) and rely on the JUDGE to
    # keep them; the "I I" false start nominates as a duplicate; "qu"
    # nominates nothing - it is two chars, and no shape fires on two
    # chars without a list naming them, so the sentence-level judge
    # finds it in context instead.
    assert [(c["seg"], c["word"], c["shape"]) for c in candidates] == [
        (1, "X,", "single_char"), (1, "Y,", "single_char"),
        (1, "Z.", "single_char"), (2, "I", "duplicate")]
    # ...so add the single-char fragment shape check on its own doc.
    frag = {"segments": [
        {"speaker": "A", "text": "same f um same",
         "words": [{"word": "same", "start": 1.0, "end": 1.3},
                   {"word": "f", "start": 1.3, "end": 1.5},
                   {"word": "um", "start": 1.5, "end": 1.8},
                   {"word": "same", "start": 1.8, "end": 2.0}]}]}
    shapes = hy.nominate(frag)
    assert [(c["word"], c["shape"]) for c in shapes] == [("f", "single_char")]
    # And the dictionary singletons never nominate.
    arts = {"segments": [
        {"speaker": "A", "text": "a I see",
         "words": [{"word": "a", "start": 1.0, "end": 1.1},
                   {"word": "I", "start": 1.1, "end": 1.2},
                   {"word": "see", "start": 1.2, "end": 1.5}]}]}
    assert hy.nominate(arts) == []


def test_dispose_batch_refuses_what_it_cannot_honour():
    view = hy.segments_for_judgement(_doc())
    verdict = {
        "suppress": [
            {"seg": 0, "index": 2, "scope": "anchored",
             "why": "stray phoneme"},
            {"seg": 99, "index": 0, "scope": "anchored",
             "why": "no such segment"},
            {"seg": 0, "index": 0, "scope": "everywhere",
             "why": "wild scope"},
            {"seg": 0, "index": 1, "scope": "anchored", "why": ""},
        ],
        "respell": [
            {"heard": "aics", "correct": "AI sees",
             "why": "misheard product"},
            {"heard": "", "correct": "X", "why": "names nothing"},
        ],
    }
    (suppressions, respells), refused = hy._dispose_batch(view, verdict)
    assert [(s["seg"], s["index"], s["word"], s["scope"]) for s in suppressions] == [
        (0, 2, "qu", "anchored")]
    assert suppressions[0]["prev"] == "niche"
    assert suppressions[0]["next"] == "niche"
    assert respells == [{"heard": "aics", "correct": "AI sees",
                         "why": "misheard product"}]
    assert len(refused) == 4


def test_scan_records_with_evidence_and_provenance(tmp_path):
    """A preview (apply=False) records nothing; an applied scan records
    under the model's own name, and the next run enforces it."""
    from library.tools import learned_context as lc
    from library.tools import transcript_corrections as tc

    project = _project(tmp_path)

    def stub_judge(view, candidates, terms, project_folder):
        assert len(view) == 3  # every sentence, not just candidates
        return {"suppress": [
                    {"seg": 0, "index": 2, "word": "qu",
                     "speaker": "SpeakerOne", "prev": "niche",
                     "next": "niche", "scope": "anchored",
                     "why": "stray phoneme the reader does not need"}],
                "respell": [{"heard": "aics", "correct": "AI sees",
                             "why": "misheard product"}],
                "unevaluated": [], "refused": []}

    preview = hy.scan(project, _doc(), judge=stub_judge, apply=False)
    assert len(preview["suppress"]) == 1
    assert preview["recorded"] == []
    assert lc.active_for_step(project, "*") == []

    report = hy.scan(project, _doc(), judge=stub_judge, apply=True)
    assert report["recorded"] and len(report["recorded"]) == 2
    # Honest attribution: the model wears its own name.
    learnings = lc.active_for_step(project, "*")
    kinds = {l["id"]: (l["kind"], l["said_by"]) for l in learnings}
    assert all(kind == ("mistake_fix", "the pipeline")
               for kind in kinds.values())
    suppression = tc.suppressions(project)[0]
    assert suppression["scope"] == {"speaker": "SpeakerOne",
                                    "surface": "qu",
                                    "prev": "niche", "next": "niche"}
    # ...and the next run enforces what was recorded.
    doc = _doc()
    tc.apply_to_document(doc, project)
    assert "qu" not in doc["segments"][0]["text"].split()


def test_keyless_judge_returns_unevaluated(tmp_path, monkeypatch):
    project = _project(tmp_path)
    for var in ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    view = hy.segments_for_judgement(_doc())
    verdict = hy._llm_judge(view, hy.nominate(_doc()), [], project)
    assert verdict["suppress"] == [] and verdict["respell"] == []
    # Placeholders plus the duplicate: nominated, never guessed.
    assert len(verdict["unevaluated"]) == 4


def test_is_uncertain_reads_the_models_own_admissions():
    assert hy.is_uncertain("MODEL, NEEDS CAPTAIN CONFIRMATION: parallel "
                           "take settles it, retire if a name")
    assert hy.is_uncertain("low confidence: reads as a false start")
    assert hy.is_uncertain("BORDERLINE: keep if emphasis")
    assert hy.is_uncertain("unsure whether this is a name")
    assert not hy.is_uncertain("stray phoneme the reader does not need")
    assert not hy.is_uncertain("")
    assert not hy.is_uncertain(None)
    # The scanner's own composed template says "retire it if wrong" -
    # detection reads the row's `why`, never that template, so a
    # confident row wearing the template must not read as unsure.
    assert not hy.is_uncertain(
        "Model-proposed transcript spelling (not yet confirmed by the "
        "captain - retire it if wrong): reads as she even")


def test_scan_holds_uncertain_rows_pending_and_applies_confident(tmp_path):
    """The 2026-09-19 default, corrected end to end: the four rows the
    model flagged unsure record PENDING (held, never enforced) while
    the confident row beside them keeps auto-applying."""
    from library.tools import learned_context as lc
    from library.tools import transcript_corrections as tc

    project = _project(tmp_path)

    def stub_judge(view, candidates, terms, project_folder):
        return {"suppress": [
                    {"seg": 0, "index": 2, "word": "qu",
                     "speaker": "SpeakerOne", "prev": "niche",
                     "next": "niche", "scope": "anchored",
                     "why": "BORDERLINE: stray phoneme, keep if "
                            "emphasis"},
                    {"seg": 2, "index": 1, "word": "I",
                     "speaker": "SpeakerTwo", "prev": "I", "next": "think",
                     "scope": "anchored",
                     "why": "false-start repeat the reader does not "
                            "need"}],
                "respell": [{"heard": "Sheehan", "correct": "she even",
                             "why": "NEEDS CAPTAIN CONFIRMATION: "
                                    "parallel take reads she even"}],
                "unevaluated": [], "refused": []}

    report = hy.scan(project, _doc(), judge=stub_judge, apply=True)
    assert len(report["recorded"]) == 3
    assert len(report["pending"]) == 2
    held = {l["id"]: l for l in lc.pending(project)}
    assert set(report["pending"]) == set(held)
    # Held rows enforce nothing: the words stand on the next pass.
    assert tc.spelling_corrections(project) == []
    assert [s["heard"] for s in tc.suppressions(project)] == ["I"]
    doc = _doc()
    tc.apply_to_document(doc, project)
    assert doc["segments"][0]["text"].split()[2] == "qu"
    # The confident row beside them applied as before.
    assert len(lc.active_for_step(project, "*")) == 1
    # Promotion enforces the held rows from the next run.
    for held_id in report["pending"]:
        lc.promote(project, held_id, reason="Captain: keep.")
    assert len(tc.spelling_corrections(project)) == 1
    assert sorted(s["heard"] for s in tc.suppressions(project)) == [
        "I", "qu"]


# --------------------------------------------------------------------------
# From test_transcript_view.py
#
# Word timings are for the code that cuts on them, not for the prompt.
#
# WhisperX gives every speech region its text AND every word in it with a
# start and an end.  `temporal_index.*.speech_regions` put the whole of
# that in two prompts: on project 001 it is 110 regions, 1,439 word records
# and 93,246 bytes of `creative_direction`'s 113,053-byte context - 82.5%
# of the call - to say 7,184 bytes of English.  No creative model is asked
# anything a word boundary answers.
#
# Every reader of those timings is Python, and every one of them reads them
# somewhere other than the prompt: `speech_sequence`'s post-bridge opens the
# per-clip index files off disk, and `spine_contract`, `plan_subtitles` and
# the other post-bridges receive the UNPROJECTED inputs.  So the two facts
# this file has to hold together are:
#
#   * the prompt carries the transcript and no word timings, and
#   * the code still gets every word.
#
# The second is demonstrated by running the real post-bridge, not asserted.

sys.path.insert(0, str(REPO))

from library.tools.context_projector import project_fields
from library.tools.context_views import CONTEXT_VIEWS

STEPS = REPO / "library" / "steps"

# The two steps the view replaced the word-level regions in.  Both are
# still checked for word timings; only 2.01 still reads the VIEW.
TRANSCRIPT_STEPS = {
    "creative_direction": "step_2_01_creative_direction",
    "speech_sequence": "step_2_02_speech_sequence",
}

# The steps that read the VIEW: 2.02 builds its own `transcripts_toon`
# (`tests/contracts/test_context_contracts.py`), and 3.03 needs what the viewer
# actually hears (docs/RULE_EVIDENCE.md, "the review answered and nobody
# read it").
VIEW_STEPS = {
    "creative_direction": "step_2_01_creative_direction",
    "review_rough_cut": "step_3_03_review_rough_cut",
}

# The two steps handed their whole input set on a standing decision
# (tests/contracts/test_context_contracts.py NO_PROJECTION).  They carried word
# timings too, by a different route: `assembly_manifest.subtitles[*].words`
# from `plan_subtitles`.  6,953 bytes of 001's render call.
QA_STEPS = {
    "render": "step_6_01_render",
    "validate": "step_6_02_validate_output",
}

CONTRACTION = "okay, we're here, we're here."

TEMPORAL_INDEX = [
    {
        "clip_id": "clip_006",
        "source_file": "/nowhere/clip_006.mov",
        "duration": 20.0,
        "scene_boundaries": [{"time": 0.0, "score": 1.0, "type": "start"}],
        "speech_regions": [
            {
                "start": 14.68, "end": 16.085, "text": CONTRACTION,
                "confidence": -0.086, "method": "whisperx-wav2vec2-large-v3",
                "words": [
                    {"word": "okay,", "start": 14.68, "end": 15.202},
                    {"word": "we're", "start": 15.222, "end": 15.443},
                    {"word": "here,", "start": 15.463, "end": 15.643},
                    {"word": "we're", "start": 15.684, "end": 15.864},
                    {"word": "here.", "start": 15.884, "end": 16.085},
                ],
            },
        ],
        "energy_curve": {"sample_rate_hz": 5, "values": [0.1] * 100},
    },
    {
        "clip_id": "clip_009",
        "scene_boundaries": [{"time": 0.0, "score": 1.0, "type": "start"}],
        "speech_regions": [],
    },
]


def manifest_2(step_dir: str) -> dict:
    return json.loads((STEPS / step_dir / "manifest.json").read_text())


def declared_inputs(m: dict) -> set:
    return {i["name"] for i in m.get("interface", {}).get("inputs", [])}


def find_key(obj, name: str) -> bool:
    if isinstance(obj, dict):
        return name in obj or any(find_key(v, name) for v in obj.values())
    if isinstance(obj, list):
        return any(find_key(v, name) for v in obj)
    return False


# ── What the prompt carries ───────────────────────────────────────────

def test_no_word_timing_reaches_the_prompt():
    for node_id, step_dir in sorted(TRANSCRIPT_STEPS.items()):
        cf = manifest_2(step_dir)["context_fields"]
        projected = project_fields({"temporal_index": TEMPORAL_INDEX}, cf)
        context = json_to_toon(projected)

        assert not find_key(projected, "words"), (
            f"'{node_id}' still sends per-word timings to the model"
        )
        assert '"word":' not in context, (
            f"'{node_id}'s context still contains word records:\n{context}"
        )


def test_what_was_said_survives():
    """A saving bought by blinding the step is not a saving."""
    for node_id, step_dir in sorted(VIEW_STEPS.items()):
        cf = manifest_2(step_dir)["context_fields"]
        projected = project_fields({"temporal_index": TEMPORAL_INDEX}, cf)

        assert projected["transcript"] == [{
            "clip_id": "clip_006", "start": 14.68, "end": 16.085,
            "text": CONTRACTION,
        }], node_id
        # And it reaches the prompt spelled the way the captain said it.
        assert CONTRACTION in json_to_toon(projected), node_id


def test_no_word_timing_reaches_the_render_qa_prompt():
    """The other route in: `subtitles[*].words` off the assembly manifest.

    `render` is deliberately unprojected, so the removal is a drop-only
    declaration - everything it was handed, minus this one field -
    rather than an allow-list, which would quietly become the decision
    about what the QA calls should ask for. `validate` carries no
    manifest at all (pinned below).
    """
    inputs = {
        "assembly_manifest": {
            "subtitles": [
                {"id": "sub_001", "text": "i can feel the",
                 "timeline_start": 0.0, "timeline_end": 0.636,
                 "emphasis_words": ["feel"],
                 "words": [{"word": "i", "start": 0.0, "end": 0.036}]},
            ],
            "tracks": {"v1": [{"clip_id": "clip_001"}]},
        },
        "rendered_output": {"output_path": "/nowhere/out.mp4"},
    }
    cf = manifest_2(QA_STEPS["render"])["context_fields"]
    projected = project_fields(inputs, cf)

    assert not find_key(projected, "words")
    # Everything else it was handed is still there: this is a drop,
    # not an allow-list.
    sub = projected["assembly_manifest"]["subtitles"][0]
    assert sub["text"] == "i can feel the"
    assert sub["emphasis_words"] == ["feel"]
    assert projected["assembly_manifest"]["tracks"]["v1"]
    assert projected["rendered_output"]["output_path"] == "/nowhere/out.mp4"


def test_validate_prompt_carries_no_manifest_at_all():
    """#1282 dropped `assembly_manifest` whole from 6.02's prompt: the
    transition seating and V1 tiling the model used to re-derive are
    held by the bridge now, so the prompt carries the render record and
    the measurements, not the plan. No word timing reaches it because no
    manifest does."""
    inputs = {
        "assembly_manifest": {
            "subtitles": [
                {"id": "sub_001", "text": "i can feel the",
                 "words": [{"word": "i", "start": 0.0, "end": 0.036}]},
            ],
        },
        "rendered_output": {"output_path": "/nowhere/out.mp4"},
    }
    projected = project_fields(
        inputs, manifest_2(QA_STEPS["validate"])["context_fields"])
    assert "assembly_manifest" not in projected
    assert projected["rendered_output"]["output_path"] == "/nowhere/out.mp4"


def test_projecting_an_already_projected_tree_keeps_every_view():
    """An `llm_only` step is projected TWICE on every run.

    `gather_step_inputs` projects it and `present_llm_step` projects the
    result again.  The second pass sees a tree the first one already took
    `speech_regions` out of, so a view that rebuilt itself from scratch
    would delete the section it had just built - which is exactly what
    happened the first time this was wired.
    """
    for step_dir in VIEW_STEPS.values():
        cf = manifest_2(step_dir)["context_fields"]
        once = project_fields({"temporal_index": TEMPORAL_INDEX}, cf)
        twice = project_fields(once, cf)
        assert once["transcript"], step_dir
        # `.get`: a view whose source this fixture does not route builds
        # nothing on either pass, the same guarantee as "identical".
        for name in sorted(CONTEXT_VIEWS):
            assert twice.get(name) == once.get(name), (step_dir, name)


# ── And the code still gets every word ────────────────────────────────

def test_the_post_bridge_still_reads_every_word_timing(tmp_path):
    """Run the real post-bridge, over a real per-clip index, on disk.

    `speech_sequence`'s post-bridge is where a passage picks up its
    `word_timestamps`, and it reads them out of
    `pipeline_output/steps/1_04_temporal_index/<clip_id>.json` - never
    out of the prompt.  `run_hybrid_step` hands it `dict(inputs)`, the
    unprojected set, so nothing the projection does reaches it.
    """
    from library.tools.project_layout import Area, ProjectLayout

    project = tmp_path / "project"
    project.mkdir()
    layout = ProjectLayout(str(project))
    index_dir = layout.write_dir(Area.TEMPORAL_INDEX)
    (index_dir / "clip_006.json").write_text(
        json.dumps(TEMPORAL_INDEX[0]), encoding="utf-8")

    payload = {
        "project_folder": str(project),
        "temporal_index": {"index_dir": str(index_dir)},
        "speech_sequence": {
            "body_sequence": [{
                "clip_id": "clip_006", "text": CONTRACTION,
                "source_start": 14.68, "source_end": 16.085,
            }],
        },
    }

    proc = subprocess.run(
        [sys.executable,
         str(STEPS / "step_2_02_speech_sequence" / "post_bridge.py")],
        input=json.dumps(payload), capture_output=True, encoding="utf-8",
        env={**os.environ, "PYTHONPATH": str(REPO)},
    )
    assert proc.returncode == 0, proc.stderr
    hook = json.loads(proc.stdout)["speech_sequence"]["body_sequence"][0]

    words = [w["word"] for w in hook["word_timestamps"]]
    assert words == ["okay,", "we're", "here,", "we're", "here."], (
        f"the post-bridge lost the word timings the prompt no longer "
        f"carries: {hook.get('word_timestamps')}"
    )
    assert hook["word_timestamps"][0]["source_start"] == 14.68


# --------------------------------------------------------------------------
# From test_silent_window_speech_claims.py
#
# No quoted speech out of a window the transcript gives nothing.
#
# Folded windows carry their audio track, which the model hears
# (`extract_video_clips` keeps it and `analyze_windows` passes it as
# `audio=`). Delivery is heard; words still come only from the transcript
# text the prompt carries. Measured defect: on a window with no speech it
# invented a quotation and attributed it to the person on screen, and that
# text flowed into searchable action segments as though it had been
# spoken.
#
# Two halves, matching the fix:
# 1. `_strip_unheard_quotations` removes quoted spans the window's
#    transcript cannot verify (straight apostrophes are kept - they are
#    not quotes).
# 2. `analyze_windows` strips them on windows the transcript gives no
#    words of its own, and records `speech_quote_stripped` on the window
#    entry. Windows WITH word-timed speech may echo the transcript text
#    they were given, which is attributed correctly by construction.
#
# These tests name that defect: they fail if quoted spans from a
# wordless window reach the entry, or if the strip eats legitimate
# visible-delivery prose.

# Mock heavy mlx_vlm dependency before importing vision_pipeline_v3
mlx_mock = MagicMock()
mlx_prompt_utils = MagicMock()
mlx_prompt_utils.apply_chat_template.return_value = "prompt"
mlx_mock.prompt_utils = mlx_prompt_utils

from library.tools.analysis import vision_pipeline_v3 as vp


def test_only_quotations_are_stripped():
    """A quoted span goes and the description around it survives; a
    straight apostrophe is not a quotation; an unbalanced quote nulls
    the field rather than leaving half a quotation."""
    cleaned, stripped = vp._strip_unheard_quotations(
        'mouth moving as if speaking, says "hello there friends" loudly')
    assert stripped is True
    assert '"' not in cleaned
    assert "mouth moving" in cleaned
    assert "hello there friends" not in cleaned

    assert vp._strip_unheard_quotations(
        "person's mouth moving, head nodding") == (
        "person's mouth moving, head nodding", False)
    assert vp._strip_unheard_quotations(
        'mouth moving, says "hello there') == (None, True)


def test_window_transcript_reports_its_precision():
    temporal = {"speech_regions": [
        {"start": 1.0, "end": 3.0, "text": "hello world"},
    ]}
    text, timed = vp.get_window_transcript(temporal, 0.0, 10.0, "")
    assert (text, timed) == ("hello world", True)

    text, timed = vp.get_window_transcript(
        None, 0.0, 10.0, full_transcript="a b c d e f g h")
    assert timed is False and text != ""

    text, timed = vp.get_window_transcript(None, 0.0, 10.0, "")
    assert (text, timed) == ("", False)


def _analyzer_saying(raw_actions):
    analyzer = MagicMock()
    analyzer.analyze_with_retry.return_value = (
        {"actions": raw_actions}, "raw", 0.5)
    return analyzer


def test_only_a_wordless_window_has_its_quotation_stripped_and_recorded():
    """A window the transcript gives no words has the invented quotation
    stripped and `speech_quote_stripped` recorded, its visible delivery
    intact; a word-timed window may echo the transcript it was given."""
    analyzer = _analyzer_saying([{
        "start": 0.0, "end": 10.0,
        "action": "person talking to camera",
        "speech_cue": 'says "welcome back to the show" with energy',
        "body_language": "seated, hands visible",
    }])
    clips = [{"index": 0, "start": 0.0, "end": 10.0, "path": "/tmp/w.mp4"}]
    entry = vp.analyze_windows(analyzer, clips, 10.0, None, "")[0]
    assert entry.get("speech_quote_stripped") is True
    cue = entry["actions"][0]["speech_cue"]
    assert cue is None or '"' not in cue
    assert "welcome back to the show" not in str(entry["actions"])
    assert entry["actions"][0]["action"] == "person talking to camera"
    assert entry["actions"][0]["body_language"] == "seated, hands visible"

    analyzer = _analyzer_saying([{
        "start": 1.0, "end": 3.0,
        "action": "person talking to camera",
        "speech_cue": "mouth moving steadily",
        "body_language": "seated",
    }])
    temporal = {"speech_regions": [
        {"start": 1.0, "end": 3.0, "text": "hello world"},
    ]}
    out = vp.analyze_windows(analyzer, clips, 10.0, temporal, "")
    assert out[0].get("speech_quote_stripped") is None
    assert out[0]["actions"][0]["speech_cue"] == "mouth moving steadily"


# --------------------------------------------------------------------------
# From test_spoken_lines_view.py
#
# The selector can see a SUB-TURN boundary, and still cannot see the noise.
#
# Both directions at once, because a gate that can only fail one way is
# not a gate (AGENTS.md 10.4): the sub-turn boundaries and their words
# REACH step 3.04's prompt, and the word timings, source paths and item
# ids still do NOT. Incident (#583 and the 337.59 closer):
# docs/evidence/spoken_lines.md.

sys.path.insert(0, str(REPO))


def segment_2(speaker, start, end, text, item="clip-a"):
    """One transcript row, carrying everything the real document carries.

    The noise is IN the fixture on purpose: a test that leaves it out
    proves nothing about a projection whose whole job is to remove it.
    """
    words = []
    step = (end - start) / max(len(text.split()), 1)
    for i, word in enumerate(text.split()):
        words.append({"word": word, "start": round(start + i * step, 3),
                      "end": round(start + (i + 1) * step, 3), "timed": True})
    return {
        "speaker": speaker,
        "text": text,
        "timeline_start": start,
        "timeline_end": end,
        "source_file": "/Volumes/Media/podcast media/LCATL0013.MXF",
        "source_start": start + 100.0,
        "source_end": end + 100.0,
        "resolve_item_id": item,
        "words": words,
        "read_from_words": False,
    }


# The shape of the field-test episode, small enough to read: SpeakerOne's
# 328.61-341.27 turn is FOUR segments, and 337.59 is the third of them -
# the boundary the noisy run used and the clean run could not name.
CLOSER = "And if you want to see how your brand appears, you should go check it out."
BIO = "The link's in our bio."

DOCUMENT = {
    "measurement": "Speech transcribed by WhisperX. Times are timeline time.",
    "derived_from": {"duration_seconds": 400.0, "fps": 23.976,
                     "picture_holes": []},
    "segments": [
        segment_2("SpeakerTwo", 312.75, 320.10,
                "search didn't change, the question changed", "clip-a"),
        segment_2("SpeakerTwo", 320.40, 328.23,
                "and that is what decides where you show up", "clip-a"),
        segment_2("SpeakerOne", 328.61, 333.20,
                "yes and that is exactly what we found in the audit",
                "clip-b"),
        segment_2("SpeakerOne", 333.30, 337.40,
                "one is a search engine the other is a decision engine",
                "clip-b"),
        segment_2("SpeakerOne", 337.59, 340.47, CLOSER, "clip-b"),
        segment_2("SpeakerOne", 340.59, 341.27, BIO, "clip-b"),
        # Straddles a cut: no `resolve_item_id`. A boundary is never
        # placed on one, and it is REPORTED rather than hidden.
        {"speaker": "SpeakerOne", "text": "Yeah.",
         "timeline_start": 461.26, "timeline_end": 473.34,
         "source_file": None, "source_start": None, "source_end": None,
         "resolve_item_id": None, "words": [], "read_from_words": False},
    ],
}


def projected_2() -> dict:
    """What step 3.04's prompt really carries, through the real projection."""
    from library.steps.step_3_04_select_reels.bridge import build_context

    tables = build_context({"timeline_transcript": DOCUMENT})
    inputs = dict(tables)
    inputs["timeline_transcript"] = DOCUMENT
    inputs["project_folder"] = "/nowhere/project"
    return project_step_context(inputs, manifest(), set(tables))


# ── Direction one: the sub-turn boundaries reach the prompt ──────────

def test_every_bound_segment_is_a_row_and_a_straddling_one_is_reported():
    """A row carries its speaker, start, end and text - nothing else. A
    segment that straddles a cut is never offered as a boundary (the
    post-bridge will not snap to it), and hiding it is what let a
    borrowed closer land inside a 12.07s row nothing could see, so it is
    reported."""
    view = build_view("spoken_lines",
                      {"timeline_transcript": DOCUMENT})["spoken_lines"]
    lines = view["lines"]
    bound = [s for s in DOCUMENT["segments"] if s["resolve_item_id"]]
    assert len(lines) == len(bound)
    assert lines[0] == {"speaker": "SpeakerTwo", "start": 312.75, "end": 320.1,
                        "text": "search didn't change, the question changed"}
    for row in lines:
        assert set(row) == {"speaker", "start", "end", "text"}
    assert all(row["start"] != 461.26 for row in lines)
    said = view["not_a_boundary"]
    assert "461.26-473.34" in said and "SpeakerOne" in said
    assert "1 stretch(es)" in said


def test_the_boundary_inside_a_turn_reaches_the_prompt():
    """`SpeakerOne 337.59-341.27` is the measured case, and it is sub-turn.

    Her turn is 328.61-341.27. 337.59 exists only in `segments`, and the
    run that could not see it opened its reel on throat-clearing.
    """
    context = json_to_toon(projected_2())
    assert "328.61,341.27" in context, "the turn itself must still be there"
    assert "337.59" in context, (
        "the sub-turn boundary the earlier run cut on is invisible again")
    assert CLOSER in context, (
        "a boundary with no words at it is not a boundary anyone can use")


# ── Direction two: the noise still cannot get through ────────────────

def test_no_word_timing_source_path_item_id_or_undeclared_field_reaches():
    """#583's guarantee, including for a field invented after it landed."""
    output = projected_2()
    blob = json.dumps(output)
    for key in NOISE_KEYS:
        assert f'"{key}"' not in blob, (
            f"{key!r} is back in select_reels' prompt")
    assert ".MXF" not in blob and "/Volumes/" not in blob
    assert "clip-a" not in blob and "clip-b" not in blob
    assert set(output["timeline_transcript"]) == {"measurement"}, (
        "the transcript document is back beside the view it was "
        "replaced by")

    document = json.loads(json.dumps(DOCUMENT))
    document["a_field_nobody_declared"] = "x" * 64
    document["segments"][0]["another_undeclared_field"] = "y" * 64
    from library.steps.step_3_04_select_reels.bridge import build_context

    tables = build_context({"timeline_transcript": document})
    inputs = dict(tables)
    inputs["timeline_transcript"] = document
    blob = json.dumps(project_step_context(inputs, manifest(), set(tables)))
    assert "a_field_nobody_declared" not in blob
    assert "another_undeclared_field" not in blob
    assert "x" * 64 not in blob and "y" * 64 not in blob


def test_the_revert_is_exactly_what_the_noise_assertions_forbid():
    """The cheap way to buy sub-turn boundaries is to declare the raw
    document again, and it must stay refused.

    Without this the four assertions above would be describing a
    projection rather than gating one: they pass at `origin/main` too,
    because #583 is what makes them pass. This one shows what they are
    holding shut - swap the declaration for the revert and the words,
    the paths and the item ids all come straight back.
    """
    from library.steps.step_3_04_select_reels.bridge import build_context

    reverted = manifest()
    reverted["context_fields"] = ["timeline_transcript", "reel_candidates"]
    tables = build_context({"timeline_transcript": DOCUMENT})
    inputs = dict(tables)
    inputs["timeline_transcript"] = DOCUMENT
    blob = json.dumps(project_step_context(inputs, reverted, set(tables)))
    for key in NOISE_KEYS:
        assert f'"{key}"' in blob, (
            f"the revert no longer carries {key!r}, so forbidding it "
            f"proves nothing")
    assert ".MXF" in blob and "clip-a" in blob


def test_projecting_an_already_projected_tree_keeps_the_view():
    """A hybrid step can be projected twice; the second pass sees a tree
    the first one already took `segments` out of."""
    once = projected_2()
    twice = project_step_context(json.loads(json.dumps(once)), manifest(),
                                 {"turns", "reel_candidates"})
    assert twice["spoken_lines"] == once["spoken_lines"]


# ── The words are sent ONCE ──────────────────────────────────────────

def test_the_turn_table_no_longer_carries_the_words_the_view_carries():
    """A turn's text is its segments' texts joined by a space, to the
    byte, so publishing both is the summary and its own source."""
    output = projected_2()
    for row in output["turns"]:
        assert set(row) == {"speaker", "start", "end"}, (
            "`turns` carries the words a second time")
    context = json_to_toon(output)
    assert context.count(CLOSER) == 1, (
        "the speech reaches the prompt twice")
    assert context.count(BIO) == 1
