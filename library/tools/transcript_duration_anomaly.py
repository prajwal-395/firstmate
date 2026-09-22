"""Words the aligner stretched, flagged before a caption is planned.

Reel 12 (field test, 2026-09-19) is why this exists: the large-v3
transcription arm dropped three spoken words ("pull from there") and
stuttered one into "probably probably" in a single 46-word row, and MFA
force-aligned the defective list by stretching "hallucinate" across the
0.68s gap to 1.55s - under the fixed 3.0s bound the coverage gate
excludes, so the gate reported CLEAN on audio speaking 9 words where
the card captions 6. The two duration outliers in that row WERE the two
complaints: "hallucinate" 1.55s the row maximum, "probably" 0.72s the
runner-up, against a next-longest content word of 0.58s. The signal was
in the data and nothing looked at it.

Method: per transcript ROW, each timed word's duration is scored against
the row's own median and MAD (median absolute deviation) as a modified
z-score (Iglewicz-Hoaglin: 0.6745 * (d - median) / MAD). A score above
`MODIFIED_Z_THRESHOLD` warns. The seconds threshold this implies
(median + threshold * MAD / 0.6745) is DERIVED from the speaker's local
rate on that row - never a constant - which is what lets a naturally
long word in slow speech pass while a stretched word in fast speech
flags. A fixed seconds bound already failed once at 3.0s; a second one
would fail the same way at a different number.

The row is the most local window available: transcript rows (and spine
blocks) are single-speaker, so the median is that speaker's rate at
that instant. Aggregating across rows would mix takes, mics and
speakers into one number that describes none of them.

What this does NOT do: hear the audio. A word stretched across dropped
speech and a word genuinely spoken slowly have the same shape in the
timings; this flags the shape, and a flagged row is a prompt to listen,
not a verdict. Words the transcription never produced are invisible
here too - there is no duration to score. Both limits are said on the
run, in the warning itself.
"""

from __future__ import annotations

import re
import statistics
from typing import Optional, Sequence

# A row with fewer timed words cannot establish a local rate: one word
# is over a tenth of the sample and the median/MAD have no stability
# worth warning on. Whole transcript rows and spine blocks carry tens
# of words; anything shorter is a fragment, and a warning calibrated on
# a fragment is noise presented as measurement.
MIN_WORDS_PER_ROW = 8

# The Iglewicz-Hoaglin outlier cutoff for the modified z-score: 3.5.
# A published statistical convention, dimensionless, identical for every
# speaker and rate - the seconds number it implies moves with the row.
MODIFIED_Z_THRESHOLD = 3.5

# The 0.6745 scales MAD to standard deviations under normality, part of
# the same published score - not a tuning knob.
_MODIFIED_Z_SCALE = 0.6745

# Below this spread there is no variance to judge against: the row's
# words are stamped to (near-)identical durations and any "outlier" is
# quantisation, not speech. One millisecond - the transcript's own
# timestamp grain.
_ZERO_SPREAD_SECONDS = 0.001


def _norm(word: str) -> str:
    """Fold case and surrounding punctuation, keep the token.

    The same rule `subtitle_coverage.normalize_word` applies, kept as a
    local copy so this module stays import-light for the plan step:
    pure-punctuation tokens score nothing either way.
    """
    return re.sub(r"^[^\w']+|[^\w']+$", "", (word or "").lower(),
                  flags=re.UNICODE)


def _duration_seconds(word: dict) -> Optional[float]:
    """One word's spoken span, either key spelling.

    Transcript rows time words as `start`/`end`; spine blocks carry the
    same instants as `source_start`/`source_end`. Both are 1x source
    time, so the durations are identical whichever spelling arrives.
    None where the word carries no usable timing.
    """
    for start_key, end_key in (("start", "end"),
                               ("source_start", "source_end")):
        try:
            start = float(word[start_key])
            end = float(word[end_key])
        except (KeyError, TypeError, ValueError):
            continue
        span = end - start
        if span > 0:
            return span
        return None
    return None


def flag_duration_anomalies(rows: Sequence[dict]) -> dict:
    """Flag words far longer than their own row's local rate.

    `rows` are transcript rows or spine blocks as mappings carrying a
    `words` list; each row may name itself with `position`/`speaker`/
    `text` for the report. Returns `{"warnings", "rows_checked",
    "rows_skipped"}`: one warning per anomalously long word, sorted
    longest-surprise-first, each naming the word, its duration, the
    row's median and MAD, and the modified z-score that flagged it.
    `rows_skipped` counts rows with too few timed words (or no spread)
    to judge - never warned on, never silent about being skipped.

    Total: malformed words and rows are skipped, never raised on. This
    runs inside caption planning, where an advisory pass must not
    refuse captions.
    """
    warnings: list = []
    rows_checked = 0
    rows_skipped = 0
    for index, row in enumerate(rows or []):
        words = (row or {}).get("words") or []
        timed: list = []
        for word in words:
            if not isinstance(word, dict):
                continue
            if not _norm(str(word.get("word", ""))):
                continue
            span = _duration_seconds(word)
            if span is None:
                continue
            timed.append((str(word.get("word", "")), span))
        if len(timed) < MIN_WORDS_PER_ROW:
            rows_skipped += 1
            continue
        durations = [span for _, span in timed]
        median = statistics.median(durations)
        mad = statistics.median([abs(d - median) for d in durations])
        if mad < _ZERO_SPREAD_SECONDS:
            rows_skipped += 1
            continue
        rows_checked += 1
        position = (row or {}).get("position", index)
        speaker = (row or {}).get("speaker")
        for raw, span in timed:
            score = _MODIFIED_Z_SCALE * (span - median) / mad
            if score > MODIFIED_Z_THRESHOLD:
                warnings.append({
                    "position": position,
                    "speaker": speaker,
                    "word": raw,
                    "duration_seconds": round(span, 3),
                    "local_median_seconds": round(median, 3),
                    "local_mad_seconds": round(mad, 3),
                    "modified_z": round(score, 2),
                    "row_word_count": len(timed),
                })
    warnings.sort(key=lambda w: -w["modified_z"])
    return {"warnings": warnings, "rows_checked": rows_checked,
            "rows_skipped": rows_skipped}
