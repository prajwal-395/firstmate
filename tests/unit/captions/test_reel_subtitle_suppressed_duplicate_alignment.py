"""A hidden duplicate cannot steal a visible word's caption timing.

Reel 10 (2026-09-29): the cleaned sentence says ``I kind ...`` while the
transcript timing array carries ``I I kind ...``. The second ``I`` is an
anchored display suppression. Matching the cleaned sentence against every
timing token let SequenceMatcher choose that later hidden ``I`` because it
had the longer matching suffix, leaving the first played ``I`` at 4.62s
between caption cards. Exercise the same entry point the reel build uses and
F25's own played-vs-captioned comparison.
"""

from library.steps.step_4_05_render_subtitles import generate_remotion_props
from library.tools import reel_build, subtitle_coverage, transcript_corrections
from library.tools import reel_conformance_verifier as verifier
from library.tools.reel_proposal import ReelMoment

FPS = 24000 / 1001
PROJECT_RANGE = [(718.59, 775.52)]
SOURCE_FILE = "LCATL0013.MXF"
SEGMENT = {
    "speaker": "Craig",
    "text": "I kind of explain it as what you kind of mentioned.",
    "timeline_start": 723.08,
    "timeline_end": 726.2,
    "source_file": SOURCE_FILE,
    "source_start": 1587.818875,
    "source_end": 1590.938875,
    "resolve_item_id": "c170b6f6-14bc-49c8-95a0-fbd68bfb4efd",
    "words": [
        {"word": "Uh", "start": 723.08, "end": 723.13, "timed": True},
        {"word": "I", "start": 723.21, "end": 723.77, "timed": True},
        {"word": "I", "start": 723.96, "end": 724.07, "timed": True,
         "display": False},
        {"word": "kind", "start": 724.07, "end": 724.32, "timed": True},
        {"word": "of", "start": 724.32, "end": 724.39, "timed": True},
        {"word": "explain", "start": 724.39, "end": 724.81, "timed": True},
        {"word": "it", "start": 724.81, "end": 724.91, "timed": True},
        {"word": "as", "start": 724.91, "end": 725.33, "timed": True},
        {"word": "what", "start": 725.37, "end": 725.6, "timed": True},
        {"word": "you", "start": 725.6, "end": 725.66, "timed": True},
        {"word": "kind", "start": 725.66, "end": 725.83, "timed": True},
        {"word": "of", "start": 725.83, "end": 725.89, "timed": True},
        {"word": "mentioned.", "start": 725.89, "end": 726.2,
         "timed": True},
    ],
}


def _planner_input():
    moment = ReelMoment(
        number=10,
        slug="the-website-wasnt-broken-everything-else",
        reason="regression fixture",
        timeline_start=PROJECT_RANGE[0][0],
        timeline_end=PROJECT_RANGE[0][1],
        source_spans=({
            "source_file": SOURCE_FILE,
            "source_start": 1583.348875,
            "source_end": 1649.299875,
        },),
    )
    transcript = {"segments": [SEGMENT]}
    return moment, transcript


def _f25_inputs(plan, transcript, project_folder):
    segment = transcript["segments"][0]
    audio_spans = [{
        "source_file": SOURCE_FILE,
        "source_start": segment["source_start"],
        "source_end": segment["source_end"],
        "reel_start": reel_build.reel_time(
            segment["timeline_start"], PROJECT_RANGE),
    }]
    played_result = subtitle_coverage.played_words_from_transcript(
        transcript["segments"], audio_spans)
    played = subtitle_coverage.read_words_for_comparison(
        played_result["words"],
        transcript_corrections.spelling_corrections(project_folder))
    suppressed = verifier._suppressed_played_words(played, project_folder)

    cards = []
    captioned = []
    for entry in plan.caption_entries:
        card = entry["id"]
        cards.append({
            "card": card,
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
                    "card": card,
                    "reel_start": word["start"],
                    "reel_end": word["end"],
                })
    return played, captioned, cards, suppressed


def test_build_planner_covers_played_i_and_f25_passes(tmp_path, monkeypatch):
    project = str(tmp_path)
    transcript_corrections.record_display_suppression(
        project, "uh", "the filler before the sentence is not captioned")
    transcript_corrections.record_display_suppression(
        project, "I", "the second I is a false start",
        scope={"speaker": "Craig", "surface": "I",
               "prev": "I", "next": "kind"})
    moment, transcript = _planner_input()
    monkeypatch.setattr(
        generate_remotion_props, "generate_subtitle_props_per_block",
        lambda *args, **kwargs: [],
    )

    # This is the exact caption-planning entry point called from the reel
    # builder. Empty props skip rendering only; the pipeline plan is real.
    plan = reel_build.reel_subtitle_segments(
        moment, transcript, PROJECT_RANGE, project,
        fps=FPS, width=1080, height=1920,
    )
    played, captioned, cards, suppressed = _f25_inputs(
        plan, transcript, project)
    coverage = subtitle_coverage.check_word_coverage(
        played, captioned, cards, suppressed=suppressed)

    spoken_i = next(word for word in played
                    if word["norm"] == "i" and not word.get("suppression"))
    covering = [card for card in cards
                if card["reel_start"] <= spoken_i["reel_start"]
                and card["reel_end"] >= spoken_i["reel_end"]]
    assert covering, (
        "the build's planned cards leave the spoken I between cards")
    card = covering[0]
    assert any(word["card"] == card["card"] and word["norm"] == "i"
               and abs(word["reel_start"] - spoken_i["reel_start"])
               < 0.001
               for word in captioned)
    assert [finding for finding in coverage["findings"]
            if finding["severity"] == "error"] == []
