"""The reel build's subtitle planner must produce words F25 can pair.

These regressions use `reel_build.reel_subtitle_segments`, the same
planning entry point as a staged reel build, then compare its plan with
the offline played-word coverage used by F25. Remotion rendering is
stubbed; no Resolve or project output is involved.
"""

from pathlib import Path

from library.steps.step_4_05_render_subtitles import generate_remotion_props
from library.tools import (reel_build, subtitle_coverage,
                           timeline_transcript, transcript_corrections)
from library.tools import reel_conformance_verifier as verifier
from library.tools.reel_proposal import (
    CallToAction, ReelMoment, snap_moment_to_speech,
)

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


def test_reel15_cached_iso_merge_makes_caption_plan_pass_f25(
        tmp_path, monkeypatch):
    """A pre-1465 transcript is resolved from its cached ISO WAVs."""
    _skip_render(monkeypatch)
    words = ["Yeah", "so", "AI", "is", "actually", "better", "for",
             "small", "businesses"]

    def segment(speaker, start):
        duration = 2.37
        step = duration / len(words)
        timed = tuple({"word": word, "start": start + index * step,
                       "end": start + (index + 1) * step, "timed": True}
                      for index, word in enumerate(words))
        text = " ".join(words)
        return timeline_transcript.SpokenSegment(
            speaker=speaker, text=text, timeline_start=start,
            timeline_end=start + duration, source_file=SOURCE,
            source_start=100.0, source_end=102.37,
            resolve_item_id=f"{speaker}-clip", words=timed)

    audio_dir = (tmp_path / "pipeline_output" / "scratch"
                 / "timeline_transcript")
    audio_dir.mkdir(parents=True)
    (audio_dir / "craig.wav").write_bytes(b"cached Craig ISO")
    (audio_dir / "akshita.wav").write_bytes(b"cached Akshita ISO")
    monkeypatch.setattr(
        timeline_transcript, "_track_rms_dbfs",
        lambda path, _start, _end: (
            (-42.62 if Path(path).name == "craig.wav" else -27.12), None),
    )
    stale_document = {"segments": [row.as_dict() for row in
                                    (segment("Craig", 1.0),
                                     segment("Akshita", 1.05))]}
    transcript = timeline_transcript.resolve_document_mic_bleed(
        stale_document, str(tmp_path))
    merged = transcript["segments"]
    decisions = transcript["mic_bleed_resolution"]
    assert [row["speaker"] for row in merged] == ["Akshita"]
    assert decisions[0]["level_difference_db"] == 15.5

    ranges = [(1.0, 3.42)]
    moment = ReelMoment(
        number=15, slug="the-3d-nail-art-salon-beats-the-chains",
        reason="duplicate ISO transcript regression",
        timeline_start=ranges[0][0], timeline_end=ranges[0][1],
        source_spans=({"source_file": SOURCE, "source_start": 100.0,
                       "source_end": 102.42},),
    )
    plan = reel_build.reel_subtitle_segments(
        moment, transcript, ranges, str(tmp_path), fps=FPS,
        width=1080, height=1920)

    assert " ".join(entry["text"] for entry in plan.caption_entries).lower() == (
        "yeah so ai is actually better for small businesses")
    assert _f25_errors(plan, transcript, ranges, tmp_path) == []


def test_reel15_build_snap_keeps_full_edge_words_and_passes_f25(
        tmp_path, monkeypatch):
    """The build repairs both approved starts before placing and captioning.

    The approved body and CTA starts sit 111ms and 108ms into their first
    words. The build's normal boundary repair widens them to the measured
    word starts, so the reel plays complete words and 4.01 captions them.
    """
    _skip_render(monkeypatch)
    body_words = [
        {"word": "If", "start": 1186.83, "end": 1187.10, "timed": True},
        {"word": "you're", "start": 1187.10, "end": 1187.35, "timed": True},
        {"word": "a", "start": 1187.35, "end": 1187.44, "timed": True},
        {"word": "salon", "start": 1187.44, "end": 1187.80, "timed": True},
        {"word": "owner", "start": 1187.80, "end": 1188.15, "timed": True},
        {"word": "or", "start": 1188.15, "end": 1188.55, "timed": True},
    ]
    cta_words = [
        {"word": "And", "start": 333.69, "end": 333.95, "timed": True},
        {"word": "that's", "start": 333.95, "end": 334.11, "timed": True},
        {"word": "why", "start": 334.11, "end": 334.21, "timed": True},
        {"word": "we've", "start": 334.21, "end": 334.39, "timed": True},
        {"word": "been", "start": 334.39, "end": 334.56, "timed": True},
        {"word": "building", "start": 334.56, "end": 335.08, "timed": True},
    ]
    transcript = {"segments": [
        _segment("If you're a salon owner or", body_words,
                 1186.83, 1188.55, 100.0),
        _segment("And that's why we've been building", cta_words,
                 333.69, 335.08, 200.0, speaker="Akshita"),
    ]}
    moment = ReelMoment(
        number=15,
        slug="the-3d-nail-art-salon-beats-the-chains",
        reason="edge-word regression",
        timeline_start=1186.941,
        timeline_end=1188.55,
        source_spans=(
            {"source_file": SOURCE, "source_start": 100.0,
             "source_end": 101.72},
            {"source_file": SOURCE, "source_start": 200.0,
             "source_end": 201.39},
        ),
        call_to_action=CallToAction(
            timeline_start=333.798,
            timeline_end=335.08,
            text="And that's why we've been building",
            speaker="Akshita",
        ),
    )

    repaired, moves = snap_moment_to_speech(moment, transcript)
    assert [(move["boundary"], move["was"], move["now"])
            for move in moves if move["boundary"] in
            ("body_start", "cta_start")] == [
        ("body_start", 1186.941, 1186.83),
        ("cta_start", 333.798, 333.69),
    ]
    ranges = reel_build.reel_ranges(repaired, transcript)
    assert ranges == [(1186.83, 1188.55), (333.69, 335.08)]

    plan = reel_build.reel_subtitle_segments(
        repaired, transcript, ranges, str(tmp_path), fps=FPS,
        width=1080, height=1920)

    planned = " ".join(entry["text"] for entry in plan.caption_entries).lower()
    assert planned.startswith("if you're")
    assert "and that's why" in planned
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
