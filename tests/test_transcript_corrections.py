"""Corrections that survive re-transcription and every regeneration.

The captain's question: "where does a correction live so that a
re-render carries it?" Editing a caption's props is undone by the next
render; editing the transcript is undone by the next transcription.
Neither is the root. A correction lives in the project's
`learned_context/` (pipeline-owned, never scratch) as a `correction`
kind with a machine-readable `source`, and is applied deterministically
at the transcript root - so every downstream consumer (captions,
explainer stages, motion-graphics anchors, the model-read context)
reads the corrected words with no changes of its own.
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _doc_with_lucy():
    return {
        "segments": [
            {
                "speaker": "Craig",
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
                "speaker": "Craig",
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
        "speaker": "Craig",
        "text": "I've I thought",
        "words": [
            {"word": "I've", "start": 1.0, "end": 1.4},
            {"word": "I", "start": 1.5, "end": 1.6},
            {"word": "thought", "start": 1.7, "end": 2.0},
        ],
    }]}
    suppression = [{
        "heard": "I",
        "scope": {"speaker": "Craig", "surface": "I",
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
            "speaker": "Craig" if i % 2 == 0 else "Akshita",
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
        "arms": {"Craig": hybrid_transcription.ARM_HYBRID},
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
        scope={"speaker": "Akshita", "surface": "different",
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
