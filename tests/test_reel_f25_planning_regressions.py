"""The reel build's subtitle planner must produce words F25 can pair.

These regressions use `reel_build.reel_subtitle_segments`, the same
planning entry point as a staged reel build, then compare its plan with
the offline played-word coverage used by F25. Remotion rendering is
stubbed; no Resolve or project output is involved.
"""

from library.steps.step_4_05_render_subtitles import generate_remotion_props
from library.tools import reel_build, subtitle_coverage, transcript_corrections
from library.tools import reel_conformance_verifier as verifier
from library.tools.reel_proposal import ReelMoment

FPS = 24000 / 1001
SOURCE = "fixture.mxf"


def _moment(start, end, source_start=100.0):
    return ReelMoment(
        number=12,
        slug="caption-planning-regression",
        reason="regression fixture",
        timeline_start=start,
        timeline_end=end,
        source_spans=({
            "source_file": SOURCE,
            "source_start": source_start,
            "source_end": source_start + (end - start),
        },),
    )


def _segment(text, words, start, end, source_start, speaker="Craig"):
    return {
        "speaker": speaker,
        "text": text,
        "timeline_start": start,
        "timeline_end": end,
        "source_file": SOURCE,
        "source_start": source_start,
        "source_end": source_start + (end - start),
        "resolve_item_id": "fixture-clip",
        "words": words,
    }


def _plan(transcript, ranges, project_folder):
    start = min(row[0] for row in ranges)
    end = max(row[1] for row in ranges)
    source_start = min(
        segment["source_start"] for segment in transcript["segments"])
    moment = _moment(start, end, source_start)
    return reel_build.reel_subtitle_segments(
        moment, transcript, ranges, str(project_folder), fps=FPS,
        width=1080, height=1920)


def _f25_errors(plan, transcript, ranges, project_folder):
    audio_spans = [{
        "source_file": segment["source_file"],
        "source_start": segment["source_start"],
        "source_end": segment["source_end"],
        "reel_start": reel_build.reel_time(
            segment["timeline_start"], ranges),
    } for segment in transcript["segments"]]
    played_result = subtitle_coverage.played_words_from_transcript(
        transcript["segments"], audio_spans)
    played = subtitle_coverage.read_words_for_comparison(
        played_result["words"],
        transcript_corrections.spelling_corrections(str(project_folder)))
    suppressed = verifier._suppressed_played_words(
        played, str(project_folder))
    captioned = []
    cards = []
    for entry in plan.caption_entries:
        cards.append({
            "card": entry["id"],
            "reel_start": entry["timeline_start"],
            "reel_end": entry["timeline_end"],
            "text_norms": sorted({
                subtitle_coverage.normalize_word(token)
                for token in entry["text"].split()
                if subtitle_coverage.normalize_word(token)
            }),
        })
        for word in entry.get("words") or []:
            norm = subtitle_coverage.normalize_word(word["word"])
            if norm:
                captioned.append({
                    "word": word["word"],
                    "norm": norm,
                    "card": entry["id"],
                    "reel_start": word["start"],
                    "reel_end": word["end"],
                })
    result = subtitle_coverage.check_word_coverage(
        played, captioned, cards, suppressed=suppressed)
    return [finding for finding in result["findings"]
            if finding["severity"] == "error"]


def _skip_render(monkeypatch):
    monkeypatch.setattr(
        generate_remotion_props, "generate_subtitle_props_per_block",
        lambda *args, **kwargs: [],
    )


def test_subframe_word_is_not_planned_and_f25_passes(tmp_path, monkeypatch):
    _skip_render(monkeypatch)
    words = [
        {"word": "there", "start": 1.0, "end": 1.3, "timed": True},
        {"word": "a", "start": 1.31, "end": 1.32, "timed": True},
        {"word": "reason", "start": 1.4, "end": 1.9, "timed": True},
    ]
    transcript = {"segments": [
        _segment("there a reason", words, 1.0, 2.0, 101.0),
    ]}
    ranges = [(1.0, 2.0)]

    plan = _plan(transcript, ranges, tmp_path)

    assert "a" not in " ".join(entry["text"]
                                for entry in plan.caption_entries).split()
    assert _f25_errors(plan, transcript, ranges, tmp_path) == []


def test_word_at_float_noisy_range_head_is_planned_and_f25_passes(
        tmp_path, monkeypatch):
    _skip_render(monkeypatch)
    words = [
        {"word": "and", "start": 1.0, "end": 1.11, "timed": True},
        {"word": "if", "start": 1.11, "end": 1.3, "timed": True},
        {"word": "you", "start": 1.3, "end": 1.5, "timed": True},
    ]
    transcript = {"segments": [
        _segment("and if you", words, 1.0, 2.0, 101.0),
    ]}
    # The transcript and the derived range head differ by floating-point
    # representation noise only. Placement rounds them to the same edge.
    ranges = [(1.0000000000001, 2.0)]

    plan = _plan(transcript, ranges, tmp_path)

    assert "and" in " ".join(entry["text"]
                              for entry in plan.caption_entries).split()
    assert _f25_errors(plan, transcript, ranges, tmp_path) == []


def test_legacy_suppressed_i_does_not_steal_ive_alignment(
        tmp_path, monkeypatch):
    _skip_render(monkeypatch)
    transcript_corrections.record_display_suppression(
        str(tmp_path), "I", "the anchored second I is a false start",
        scope={"speaker": "Craig", "surface": "I",
               "prev": "I've", "next": "thought"})
    words = [
        {"word": "I've", "start": 1.0, "end": 1.45, "timed": True},
        {"word": "I", "start": 1.46, "end": 1.58, "timed": True,
         "display": False},
        {"word": "thought", "start": 1.6, "end": 2.0, "timed": True},
    ]
    transcript = {"segments": [
        # This is the cached text left by the old apostrophe-boundary bug.
        _segment("'ve I thought", words, 1.0, 2.0, 101.0),
    ]}
    ranges = [(1.0, 2.0)]

    plan = _plan(transcript, ranges, tmp_path)

    planned = " ".join(entry["text"]
                       for entry in plan.caption_entries).lower()
    assert "i've" in planned
    assert _f25_errors(plan, transcript, ranges, tmp_path) == []


def test_project_phrase_spelling_reaches_reel_alignment_and_f25(
        tmp_path, monkeypatch):
    _skip_render(monkeypatch)
    transcript_corrections.record_spelling(
        str(tmp_path), "Jim and I", "Gemini",
        reason="captain-confirmed transcript correction")
    words = [
        {"word": "Jim", "start": 1.0, "end": 1.2, "timed": True},
        {"word": "and", "start": 1.2, "end": 1.35, "timed": True},
        {"word": "I", "start": 1.35, "end": 1.55, "timed": True},
        {"word": "on", "start": 1.6, "end": 1.8, "timed": True},
        {"word": "Google", "start": 1.8, "end": 2.2, "timed": True},
    ]
    transcript = {"segments": [
        _segment("Jim and I on Google", words, 1.0, 2.3, 101.0),
    ]}
    ranges = [(1.0, 2.3)]

    plan = _plan(transcript, ranges, tmp_path)

    planned = " ".join(entry["text"]
                       for entry in plan.caption_entries).lower()
    assert "gemini" in planned
    assert _f25_errors(plan, transcript, ranges, tmp_path) == []


def test_overlapped_same_speaker_word_moves_to_covering_card(
        tmp_path, monkeypatch):
    _skip_render(monkeypatch)
    first_words = [
        {"word": "what", "start": 30.82, "end": 31.02, "timed": True},
        {"word": "you", "start": 31.02, "end": 31.2, "timed": True},
        {"word": "can", "start": 31.2, "end": 31.38, "timed": True},
        {"word": "say", "start": 31.38, "end": 31.52, "timed": True},
        {"word": "about", "start": 31.52, "end": 31.76, "timed": True},
        {"word": "it.", "start": 31.782, "end": 31.92, "timed": True},
    ]
    second_words = [
        {"word": "and", "start": 31.751, "end": 31.791, "timed": True},
        {"word": "AI", "start": 31.92, "end": 32.15, "timed": True},
        {"word": "really", "start": 32.15, "end": 32.38, "timed": True},
        {"word": "likes", "start": 32.38, "end": 32.7, "timed": True},
        {"word": "that.", "start": 32.7, "end": 33.08, "timed": True},
    ]
    transcript = {"segments": [
        _segment("what you can say about it.", first_words,
                 30.82, 31.92, 130.82),
        _segment("and AI really likes that.", second_words,
                 31.751, 33.08, 131.751),
    ]}
    ranges = [(30.82, 33.08)]

    plan = _plan(transcript, ranges, tmp_path)

    cards = plan.caption_entries
    it_words = [
        (entry, word) for entry in cards
        for word in entry.get("words") or []
        if subtitle_coverage.normalize_word(word["word"]) == "it"
    ]
    assert len(it_words) == 1, cards
    entry, word = it_words[0]
    assert entry["timeline_start"] <= word["start"]
    assert entry["timeline_end"] >= word["end"], cards
    assert _f25_errors(plan, transcript, ranges, tmp_path) == []


def test_shared_boundary_word_onset_keeps_transcript_order_and_f25_passes(
        tmp_path, monkeypatch):
    _skip_render(monkeypatch)
    first_words = [
        {"word": "what", "start": 1.0, "end": 1.2, "timed": True},
        {"word": "your", "start": 1.2, "end": 1.4, "timed": True},
        {"word": "differentiator", "start": 1.4, "end": 1.7,
         "timed": True},
        {"word": "is.", "start": 1.8, "end": 1.9, "timed": True},
    ]
    second_words = [
        {"word": "If", "start": 1.8, "end": 1.89, "timed": True},
        {"word": "you", "start": 2.0, "end": 2.2, "timed": True},
        {"word": "think", "start": 2.2, "end": 2.5, "timed": True},
        {"word": "your", "start": 2.5, "end": 2.7, "timed": True},
    ]
    transcript = {"segments": [
        _segment("what your differentiator is.", first_words,
                 1.0, 2.0, 101.0),
        _segment("If you think your", second_words,
                 1.8, 2.7, 101.8),
    ]}
    ranges = [(1.0, 2.7)]

    plan = _plan(transcript, ranges, tmp_path)

    is_cards = [
        entry for entry in plan.caption_entries
        if any(word["word"].casefold() == "is."
               for word in entry.get("words") or [])
    ]
    if_cards = [
        entry for entry in plan.caption_entries
        if any(word["word"].casefold() == "if"
               for word in entry.get("words") or [])
    ]
    assert len(is_cards) == len(if_cards) == 1
    assert is_cards[0] is not if_cards[0]
    assert plan.caption_entries.index(is_cards[0]) < plan.caption_entries.index(
        if_cards[0])
    assert _f25_errors(plan, transcript, ranges, tmp_path) == []
