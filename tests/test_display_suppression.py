"""A pronounced token the reader never sees: mark, don't strike.

`library/tools/transcript_corrections.py` (Gap 1 of the 2026-09-19
field-test brief): a display suppression removes the token from
caption/subtitle text and quoted copy while the audio, the spine and
every timing stand untouched. These tests prove the three halves -
the transcript pass, the timed-word pass the caption planner reads,
and survival across a re-transcription - plus the guardrails (an
anchored "i" never touches the pronoun "I"; a caption's provenance
identity never re-keys).
"""

import copy
import sys

sys.path.insert(0, ".")

from library.tools import transcript_corrections as tc


def _project(tmp_path):
    import json
    import os
    root = str(tmp_path)
    os.makedirs(os.path.join(root, "learned_context"), exist_ok=True)
    with open(os.path.join(root, "learned_context", "learnings.json"),
              "w", encoding="utf-8") as handle:
        json.dump([], handle)
    return root


def _doc():
    return {"segments": [
        {"speaker": "Akshita", "text": "best CRMs, um s best",
         "source_clip_id": "LC4932.MXF",
         "source_start": 162.0, "source_end": 174.0,
         "words": [
             {"word": "best", "start": 162.0, "end": 162.3},
             {"word": "CRMs,", "start": 162.3, "end": 162.7},
             {"word": "um", "start": 162.7, "end": 162.9},
             {"word": "s", "start": 162.9, "end": 163.1},
             {"word": "best", "start": 163.1, "end": 163.4}]},
        {"speaker": "Craig", "text": "I think I know",
         "source_clip_id": "LCATL0012.MXF",
         "source_start": 10.0, "source_end": 12.0,
         "words": [
             {"word": "I", "start": 10.0, "end": 10.2},
             {"word": "think", "start": 10.2, "end": 10.6},
             {"word": "I", "start": 10.6, "end": 10.7},
             {"word": "know", "start": 10.7, "end": 11.0}]},
    ]}


def test_suppression_hides_token_but_keeps_audio_and_timings(tmp_path):
    project = _project(tmp_path)
    tc.record_display_suppression(project, "um", "reel 24 ums")
    tc.record_display_suppression(
        project, "s", "reel 17 stray s",
        scope={"speaker": "Akshita", "surface": "s",
               "prev": "um", "next": "best"})
    doc = _doc()
    before = copy.deepcopy(doc)
    report = tc.apply_to_document(doc, project)
    assert report["suppressed"] == 2  # "um" and the anchored "s"
    seg = doc["segments"][0]
    assert seg["text"] == "best CRMs, best"
    marked = [w for w in seg["words"] if w.get("display") is False]
    assert [w["word"] for w in marked] == ["um", "s"]
    # Every timing byte-identical, neighbours included.
    for after_seg, before_seg in zip(doc["segments"], before["segments"]):
        assert after_seg["source_start"] == before_seg["source_start"]
        assert after_seg["source_end"] == before_seg["source_end"]
        for after_w, before_w in zip(after_seg["words"],
                                     before_seg["words"]):
            assert after_w["start"] == before_w["start"]
            assert after_w["end"] == before_w["end"]
    # The audio span is untouched: nothing retimed, nothing struck.
    assert seg["source_start"] == 162.0 and seg["source_end"] == 174.0


def test_suppression_count_is_two_not_three(tmp_path):
    # Guard against the accounting above drifting: seg0 hides exactly
    # "um" and "s", nothing else.
    project = _project(tmp_path)
    tc.record_display_suppression(project, "um", "reel 24 ums")
    tc.record_display_suppression(
        project, "s", "reel 17 stray s",
        scope={"speaker": "Akshita", "surface": "s",
               "prev": "um", "next": "best"})
    doc = _doc()
    report = tc.apply_suppressions(doc, tc.suppressions(project))
    assert report["suppressed"] == 2
    assert report["segments_touched"] == 1


def test_anchored_stray_never_touches_the_pronoun(tmp_path):
    project = _project(tmp_path)
    tc.record_display_suppression(
        project, "i", "reel 18 stray i",
        scope={"speaker": "Craig", "surface": "i",
               "prev": "uh", "next": "kind"})
    doc = _doc()
    tc.apply_to_document(doc, project)
    # Both pronouns stand: neither is a lowercase "i" after "uh".
    pronouns = [w for w in doc["segments"][1]["words"]
                if w["word"] == "I"]
    assert len(pronouns) == 2
    assert all(w.get("display") is not False for w in pronouns)
    assert doc["segments"][1]["text"] == "I think I know"


def test_cased_anchor_hits_lowercase_spine_words():
    # Step 1.04 lowercases every temporal word, so the caption
    # planner meets "probably" where the transcript said "Probably".
    # The anchor (speaker + neighbours) is the guard, never the
    # casing: one anchor fixes both, or the transcript and the
    # caption silently disagree (Reel 12, 2026-09-19).
    words = [
        {"word": "which", "start": 1.0, "end": 1.2},
        {"word": "probably", "start": 1.2, "end": 1.9},
        {"word": "probably", "start": 1.9, "end": 2.1},
        {"word": "most", "start": 2.1, "end": 2.4},
    ]
    anchor = {"id": "x", "heard": "Probably",
              "scope": {"speaker": "Akshita", "surface": "Probably",
                        "prev": "probably", "next": "most"}}
    kept, dropped = tc.filter_words(words, "Akshita", [anchor])
    assert [w["word"] for w in kept] == ["which", "probably", "most"]
    assert [w["word"] for w in dropped] == ["probably"]


def test_suppression_does_not_rekey_provenance(tmp_path):
    # A caption's identity is its provenance (speaker, clip, span),
    # never its word list - so hiding a word re-keys nothing after it.
    project = _project(tmp_path)
    tc.record_display_suppression(project, "um", "reel 24 ums")
    doc = _doc()
    before_keys = [(s.get("speaker"), s.get("source_clip_id"),
                    s.get("source_start"), s.get("source_end"))
                   for s in doc["segments"]]
    before_count = len(doc["segments"])
    tc.apply_to_document(doc, project)
    after_keys = [(s.get("speaker"), s.get("source_clip_id"),
                   s.get("source_start"), s.get("source_end"))
                  for s in doc["segments"]]
    assert after_keys == before_keys
    assert len(doc["segments"]) == before_count


def test_correction_survives_retranscription(tmp_path):
    # A re-transcription produces a FRESH document: no display flags,
    # original text. The pass re-applies from the store alone.
    project = _project(tmp_path)
    tc.record_display_suppression(project, "um", "reel 24 ums")
    tc.record_spelling(project, "AICs", "AI sees", "reel 05 aics")
    fresh = {"segments": [
        {"speaker": "Craig", "text": "how AICs you, um see",
         "words": [
             {"word": "how", "start": 1.0, "end": 1.2},
             {"word": "AICs", "start": 1.2, "end": 1.5},
             {"word": "you,", "start": 1.5, "end": 1.7},
             {"word": "um", "start": 1.7, "end": 1.9},
             {"word": "see", "start": 1.9, "end": 2.1}]}]}
    report = tc.apply_to_document(fresh, project)
    seg = fresh["segments"][0]
    assert seg["text"] == "how AI sees you, see"
    assert seg["words"][1]["word"] == "AI sees"
    assert seg["words"][3].get("display") is False
    # And the second run is a no-op with honest zeros.
    again = tc.apply_to_document(fresh, project)
    assert again["replacements"] == 0
    assert again["suppressed"] == 1  # re-marked, nothing newly dropped


def test_filter_words_partitions_without_retiming():
    words = [
        {"word": "same", "start": 1.0, "end": 1.3},
        {"word": "f", "start": 1.3, "end": 1.5},
        {"word": "um,", "start": 1.5, "end": 1.8},
        {"word": "same", "start": 1.8, "end": 2.0},
    ]
    suppressions = [
        {"id": "x", "heard": "um", "scope": None},
        {"id": "y", "heard": "f",
         "scope": {"speaker": "Akshita", "surface": "f",
                   "prev": "same", "next": "um"}},
    ]
    kept, dropped = tc.filter_words(words, "Akshita", suppressions)
    assert [w["word"] for w in kept] == ["same", "same"]
    assert [w["word"] for w in dropped] == ["f", "um,"]
    # Partitioned, never edited: the survivors keep their timings.
    assert kept[0]["start"] == 1.0 and kept[1]["start"] == 1.8
    # Another speaker's identical words stand.
    kept2, dropped2 = tc.filter_words(words, "Craig", suppressions)
    assert [w["word"] for w in kept2] == ["same", "f", "same"]
    assert [w["word"] for w in dropped2] == ["um,"]


def test_phrase_spelling_merges_entries_without_inventing_time():
    words = [
        {"word": "with", "start": 1.0, "end": 1.2},
        {"word": "Jim", "start": 1.2, "end": 1.4},
        {"word": "and", "start": 1.4, "end": 1.5},
        {"word": "I", "start": 1.5, "end": 1.7},
        {"word": "on", "start": 1.7, "end": 1.9},
    ]
    out, made = tc.apply_spelling_to_words(
        words, [{"id": "a", "heard": "jim and i",
                 "correct": "Gemini"}])
    assert [(w["word"], w["start"], w["end"]) for w in out] == [
        ("with", 1.0, 1.2), ("Gemini", 1.2, 1.7), ("on", 1.7, 1.9)]
    assert made == {"a": 1}
    # Pure: the caller's list is untouched.
    assert words[1]["word"] == "Jim"


def test_possessive_survives_a_word_level_respell():
    words = [{"word": "ai's", "start": 1.0, "end": 1.3}]
    out, _ = tc.apply_spelling_to_words(
        words, [{"id": "a", "heard": "ai", "correct": "AI"}])
    assert out[0]["word"] == "AI's"
    assert (out[0]["start"], out[0]["end"]) == (1.0, 1.3)


def test_edge_punctuation_survives_a_word_level_respell():
    # Reel 16, 2026-09-19: respelling "chronicle," to "Chronicle"
    # must not eat the comma the caption proving the fix carries.
    words = [
        {"word": "the", "start": 1.0, "end": 1.1},
        {"word": "atlanta", "start": 1.1, "end": 1.4},
        {"word": "business", "start": 1.4, "end": 1.7},
        {"word": "chronicle,", "start": 1.7, "end": 2.1},
        {"word": "as", "start": 2.1, "end": 2.2},
    ]
    out, made = tc.apply_spelling_to_words(
        words, [{"id": "a", "heard": "atlanta business chronicle",
                 "correct": "Atlanta Business Chronicle"}])
    assert [w["word"] for w in out] == [
        "the", "Atlanta Business Chronicle,", "as"]
    # The merged entry spans the run it replaced - timings stand.
    assert (out[1]["start"], out[1]["end"]) == (1.1, 2.1)
    assert made == {"a": 1}


def test_single_word_respell_keeps_its_trailing_period():
    words = [{"word": "google.", "start": 1.0, "end": 1.3}]
    out, _ = tc.apply_spelling_to_words(
        words, [{"id": "a", "heard": "google", "correct": "Google"}])
    assert out[0]["word"] == "Google."


def test_comma_debris_leaves_with_its_token():
    assert tc._drop_token_occurrences("Um, and go", "um", [0]) == (
        "and go", 1)
    assert tc._drop_token_occurrences("same , same", "x", []) == (
        "same , same", 0)
    text, n = tc._drop_token_occurrences(
        "you know, um, same f um, same", "um", [0, 1])
    assert (text, n) == ("you know, same f, same", 2)


def test_suppress_display_drops_global_only():
    from library.tools import display_respell as dr
    obj = {"reason": "say um it now", "quote": "same f um same",
           "anchor_phrase": "um stays, pins are decisions"}
    report = dr.suppress_display(obj, [
        {"heard": "um", "scope": None},
        {"heard": "f", "scope": {"speaker": "A", "surface": "f",
                                 "prev": "same", "next": "um"}},
    ])
    assert obj["reason"] == "say it now"
    # Anchored "f" never reaches free text without its anchor.
    assert obj["quote"] == "same f same"
    # Pins are identity, never displays.
    assert obj["anchor_phrase"] == "um stays, pins are decisions"
    assert report["suppressed"] == 2  # reason + quote, global "um" both
