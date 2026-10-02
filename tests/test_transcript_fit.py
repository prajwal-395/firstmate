"""A transcript row whose text does not fit its audio, measured.

Every number here was measured on the captain's shipped `geo-podcast`
transcript on 2026-09-16 and the fixture is a trimmed slice of it, not a
synthetic one: `tests/fixtures/reel_hearing/README.md` says exactly what
was kept and what was dropped.

Nothing here runs an aligner, opens audio, calls a model or touches a
real project.
"""
import json
from pathlib import Path

import pytest

from library.tools import reel_hearing
from library.tools import transcript_fit

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "reel_hearing"


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
