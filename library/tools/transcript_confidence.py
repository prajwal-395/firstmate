"""What the TRANSCRIBER said about its own reading of a line.

The gap this closes
-------------------
A clean selector run over the whole 45-minute episode chose
267.36-284.07 and wrote this into its own `could_not_determine`:

    "Whether a garbled line in the transcript means garbled AUDIO.
     284.13-288.06 reads '[Hangul] on Google search, and chat GPT is a
     decision engine' - Hangul in the middle of an English sentence,
     which is a transcription failure signature rather than a speech
     one. It is the entire basis of where I ended this reel. I cut at
     284.07 to keep the clean reading of the payoff line and leave the
     damaged one outside the span, which cost me Craig's 290.73-299.41
     pickup and about 15 seconds of length. If the audio at
     284.13-288.06 is clean, that trade was wrong and 267.36-299.41 was
     the reel.

     What would have helped: a per-line transcription confidence beside
     the text - the ASR already produces one - or a flag saying a line's
     characters fall outside the language the rest of the transcript is
     in."

Both halves are answered here, and they are DIFFERENT questions.
`avg_logprob` says how sure the transcriber was of the words it wrote.
The script check says the words it wrote are not in the language the
rest of the conversation is in - which no confidence number states, and
which a confident wrong decode will not show.

What the ASR really produces, measured 2026-09-06
-------------------------------------------------
`faster_whisper` emits `avg_logprob`, `no_speech_prob` and
`compression_ratio` per segment; `whisperx.align` emits a per-word
`score`.  **None of it was on disk.**  The stored transcript for the
field-test episode carries 940 segments whose keys are exactly
`speaker, text, timeline_start, timeline_end, source_file, source_start,
source_end, resolve_item_id, words, read_from_words`, and 8,509 words
whose keys are exactly `word, start, end, timed`.  It was discarded at
write time, in two places, both now fixed in
`library/tools/timeline_transcript.py`:

  * `transcribe_audio` rebuilt each segment as `{start, end, text}`
    before handing it to the aligner, and
  * `interpolate_untimed_words` rebuilt each word as
    `{word, start, end, timed}`.

So a transcript written before that fix carries NO confidence, and this
module says so rather than deriving one.  **A file on disk is not a
measurement** (AGENTS.md 10.3); a number computed from something that is
not the ASR's own output would be a fabrication wearing its name.

Why `avg_logprob` ALONE
-----------------------
It is the one the aligner carries.  `whisperx.align` copies
`avg_logprob` onto every aligned segment and through the groupby that
merges subsegments, so it survives the pass that produces the
transcript with no re-attachment machinery at all.

`no_speech_prob` and `compression_ratio` do NOT survive: `align` builds
each output subsegment explicitly and neither key is in it, and one
input segment can become several output subsegments, so putting them
back afterwards would mean matching aligned spans to raw spans by time
containment.  That is an INFERENCE, and a number that arrived by
inference is not the number the ASR produced.  They are left out and
this is the record of why.

There is NO threshold
---------------------
Not here, not in the view, not in the step.  The captain's standing
ruling is that the model gets the number and judges (AGENTS.md 10.5,
and `tests/test_no_creative_floors.py`).  A cutoff below which a line is
"bad" would be the engine deciding, for every project and every
recording condition, what a struggling transcriber sounds like - and
the measured evidence says it would have decided wrong here: re-heard in
isolation the disputed span transcribes as clean English at
`avg_logprob=-0.2014`, a perfectly ordinary value.

`tests/test_transcript_confidence.py` fails if a numeric comparison
against a literal appears in this module.

The script check, and what it costs
-----------------------------------
A line whose LETTERS fall outside the script the rest of the transcript
is written in is a transcription failure signature rather than a speech
one: a speaker does not change writing system mid-sentence.  It needs no
threshold and no model call - a character's script either is the
dominant one or it is not.

Measured over the field-test episode's 940 segments: 38,008 LATIN
letters and 8 HANGUL, and the 8 are all on one line - 284.13-288.06,
exactly the line the model named.  One flagged row in 940, no false
positives.

**It does not catch everything, and must not be read as if it did.**
The same transcript carries `299.999-300.500 'kalabrahat Correct.'`,
which is the same failure in LATIN characters.  This check is blind to
it by construction, which is the honest limit of a script test.
"""

import unicodedata
from typing import Iterable, Optional


# ── The ASR's own number ─────────────────────────────────────────────

AVG_LOGPROB = "avg_logprob"
"""The key an ASR confidence travels under, spelled once.

`faster_whisper` emits it, `whisperx.align` carries it, and
`timeline_transcript.SpokenSegment` stores it under the same name so a
reader comparing the transcript to the library it came out of is looking
at one word.
"""

CONFIDENCE_LEGEND = (
    "`avg_logprob` is the TRANSCRIBER's own mean per-token log "
    "probability for that line, exactly as it emitted it: 0 is certain "
    "and more negative is less certain, on an open-ended scale. It says "
    "how sure the transcriber was of the WORDS it wrote. It is not a "
    "measurement of the audio, and no threshold is applied to it "
    "anywhere - read it against the other lines in this same transcript "
    "and judge it yourself."
)

CONFIDENCE_ABSENT = (
    "No line carries `avg_logprob`: this transcript was written before "
    "the transcriber's own per-line confidence was kept, so the number "
    "does not exist for these words and none has been derived. A line "
    "you doubt cannot be checked against it."
)


def line_confidence(segment: dict) -> Optional[float]:
    """The ASR confidence a transcript row carries, or None.

    Verbatim.  A row that has none returns None rather than a stand-in,
    because the stand-in is the defect this module exists to prevent.
    """
    if not isinstance(segment, dict):
        return None
    value = segment.get(AVG_LOGPROB)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def any_line_carries_confidence(segments: Iterable[dict]) -> bool:
    """Whether this transcript records the transcriber's confidence at all."""
    return any(line_confidence(s) is not None for s in segments)


# ── The script the rest of the transcript is in ──────────────────────

def script_of(character: str) -> Optional[str]:
    """The Unicode script a LETTER belongs to, as its own name says.

    `unicodedata.name` spells every letter's script first - "LATIN SMALL
    LETTER A", "HANGUL SYLLABLE GA", "CYRILLIC CAPITAL LETTER DE" - so
    the first word of the name IS the script, with no table to ship and
    keep in step with Unicode.

    Only letters are asked.  Digits, punctuation and whitespace are
    shared across scripts and say nothing about which one a line is
    written in.
    """
    if not character.isalpha():
        return None
    try:
        return unicodedata.name(character).split()[0]
    except ValueError:
        return None


def script_census(text: str) -> dict:
    """How many letters of each script one string holds."""
    counts: dict = {}
    for character in text or "":
        script = script_of(character)
        if script:
            counts[script] = counts.get(script, 0) + 1
    return counts


def dominant_script(segments: Iterable[dict]) -> Optional[str]:
    """The script this transcript is written in, from the transcript.

    Counted over every letter of every segment, so it is the document's
    own population rather than anything declared about the project.  A
    transcript with no letters at all has no dominant script and nothing
    is flagged against it.
    """
    counts: dict = {}
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        for script, n in script_census(segment.get("text") or "").items():
            counts[script] = counts.get(script, 0) + n
    if not counts:
        return None
    # Ties are broken by the script name so the answer is stable across
    # runs; a tie means the document is genuinely bilingual and nothing
    # useful is flagged either way.
    return max(sorted(counts), key=lambda s: counts[s])


def foreign_scripts(text: str, dominant: str) -> dict:
    """The letters of `text` that are not in `dominant`, by script."""
    return {script: n for script, n in script_census(text).items()
            if script != dominant}


SCRIPT_LEGEND = (
    "A line flagged here has letters in a writing system the rest of "
    "this transcript is not written in. A speaker does not change "
    "writing system mid-sentence, so this is a TRANSCRIPTION failure "
    "signature and not a speech one: the words are wrong, and nothing "
    "here says the audio under them is. It is exact and it is narrow - "
    "a garbled reading that comes out in the same script as everything "
    "else is not flagged, so an unflagged line is not a line proven "
    "clean."
)


def script_mismatches(document: dict) -> list:
    """Every segment whose letters fall outside the transcript's script.

    Returns a list of `{start, end, speaker, text, foreign}` in timeline
    order, where `foreign` maps script name to how many of its letters
    the line holds.  Empty when the transcript is written in one script,
    which is the ordinary case and costs nothing to say.
    """
    segments = [s for s in (document.get("segments") or [])
                if isinstance(s, dict)]
    dominant = dominant_script(segments)
    if dominant is None:
        return []

    flagged = []
    for segment in segments:
        text = (segment.get("text") or "").strip()
        foreign = foreign_scripts(text, dominant)
        if not foreign:
            continue
        flagged.append({
            "start": round(float(segment.get("timeline_start") or 0.0), 2),
            "end": round(float(segment.get("timeline_end") or 0.0), 2),
            "speaker": segment.get("speaker"),
            "text": text,
            "foreign": foreign,
        })
    flagged.sort(key=lambda row: row["start"])
    return flagged


MAX_MISMATCHES_LISTED = 20
"""How many flagged lines are named one by one.

Past this the report says how many it did not name.  An unbounded list
of them would be the raw-value-list defect (AGENTS.md 10.1) wearing the
clothes of a report.
"""


def mismatch_report(document: dict) -> Optional[str]:
    """The flagged lines as ONE line of prose, or None when there are none.

    One report rather than a per-row column, and the reason is measured:
    the field-test episode flags 1 row of 929, and a sparse column costs
    945 characters of empty cells to carry that one value while this
    costs about 250 and names the span, the speaker, the scripts and the
    words.
    """
    flagged = script_mismatches(document)
    if not flagged:
        return None

    named = flagged[:MAX_MISMATCHES_LISTED]
    held_back = len(flagged) - len(named)
    spans = []
    for row in named:
        scripts = ", ".join(f"{n} {script}" for script, n
                            in sorted(row["foreign"].items()))
        spans.append(f"{row['start']:.2f}-{row['end']:.2f} "
                     f"{row['speaker'] or 'unattributed'}: {scripts} "
                     f"character(s) in: {row['text']}")
    dominant = dominant_script([s for s in document.get("segments") or []
                                if isinstance(s, dict)])
    return (f"{len(flagged)} line(s) carry letters outside {dominant}, "
            f"which the rest of this transcript is written in. "
            + SCRIPT_LEGEND + " " + "; ".join(spans)
            + (f"; and {held_back} more" if held_back else ""))
