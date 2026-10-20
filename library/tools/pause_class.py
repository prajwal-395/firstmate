"""Is this silence a pause to keep, or a pause to close?

What this is
------------
`library/tools/speech_take.py` measures every gap between spoken words
and deliberately refuses to judge it: "a 40 ms hesitation and a 4 s
silence are both gaps with durations, and judging them is the
consumer's job." The measurement was right to stop there - it had no
evidence to judge with.

This module is the judge, and it judges only from MEASUREMENTS:

- the transcript's own words (a filler or an aborted word right after
  the gap),
- the transcript's own text (a sentence that closed before the gap),
- the transcript's own speaker attribution (a different voice after the
  gap than before it),
- the measured non-speech events (something in the gap that is not
  speech at all).

None of these is taste. A marker is a fact somebody measured, and the
verdict says which facts were found and which measurements were not
run.

The three verdicts
------------------
``linguistically_important`` - a marker was measured. The gap carries
something: a disfluency, a closed clause, a change of voice, or a
measured event.

``edit_safe`` - no marker was measured, and every measurement that
could have produced one RAN. This is a claim of completeness, and it
is only made when it is true.

``undecided`` - no marker was measured and something that could have
produced one did not run. This is the absence of an answer, and it is
not "edit_safe with a footnote".

What is a marker, exactly
-------------------------
``disfluency_lead`` - the word right after the gap is a filler ("um",
"uh") or an aborted word. The gap is the hesitation; the disfluency
is what it was waiting for.

``clause_final`` - the text before the gap ends a sentence. Closing the
gap runs two sentences together.

``turn_change`` - the speaker after the gap is not the speaker before
it. The gap is where one voice gave way to another.

``event_occupied`` - a measured non-speech event (applause, a laugh,
an impact) occupies the gap. The gap is not empty.

What has to have run
--------------------
The transcript IS the measurement of speech: a gap is by definition a
stretch where the transcriber heard no word. So the one thing the
transcript cannot see is a NON-SPEECH event, and the sound-event
measurement covers that.

A gap BETWEEN two utterances is where a change of voice would hide, so
it also needs speaker attribution. A gap INSIDE one utterance cannot
hide one, so it does not.

A gap between two utterances with no speaker attribution available is
therefore ``undecided``, never ``edit_safe``.

``tests/unit/audio/test_pause_class.py``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from library.tools import speech_take

MARKER_DISFLUENCY_LEAD = "disfluency_lead"
MARKER_CLAUSE_FINAL = "clause_final"
MARKER_TURN_CHANGE = "turn_change"
MARKER_EVENT_OCCUPIED = "event_occupied"

PAUSE_MARKERS = (
    MARKER_DISFLUENCY_LEAD,
    MARKER_CLAUSE_FINAL,
    MARKER_TURN_CHANGE,
    MARKER_EVENT_OCCUPIED,
)
"""Every marker a pause can carry. One enumeration, spelled once."""

EDIT_SAFE = "edit_safe"
LINGUISTICALLY_IMPORTANT = "linguistically_important"
UNDECIDED = "undecided"

EDIT_SAFETY = (EDIT_SAFE, LINGUISTICALLY_IMPORTANT, UNDECIDED)
"""Every verdict a pause can carry."""

COVERAGE_MEASURED = "measured"
COVERAGE_UNMEASURED = "unmeasured"

COVERAGE_SOUND_EVENTS = "sound_events"
COVERAGE_SPEAKER_ATTRIBUTION = "speaker_attribution"

DISFLUENCY_LEAD_TOLERANCE_S = 0.05
"""How close a disfluency has to sit to the gap's right edge to count
as what the gap was waiting for.

The gap ends where the next word starts, so the disfluency that
follows it starts at the gap's edge. The tolerance is there for the
rounding of two independently measured clocks, not for a judgement."""

EVENT_OVERLAP_TOLERANCE_S = 0.1
"""The tolerance an event and a gap are allowed to miss each other by.

The same tolerance `library/tools/sound_events.py` gives an event and
a block's source range, so an event starting on the cut is inside the
gap from either side."""


def _utterance_index(utterance: dict) -> Any:
    return utterance.get("utterance_index")


def _left_utterance(utterances: Sequence[dict], pause: dict) -> dict | None:
    """The utterance the gap follows, or None."""
    index = pause.get("utterance_index")
    if index is None:
        index = pause.get("before_utterance_index")
    if index is None:
        return None
    for utterance in utterances:
        if _utterance_index(utterance) == index:
            return utterance
    return None


def _right_utterance(utterances: Sequence[dict], pause: dict) -> dict | None:
    """The utterance the gap precedes, or None."""
    index = pause.get("after_utterance_index")
    if index is None:
        return None
    for utterance in utterances:
        if _utterance_index(utterance) == index:
            return utterance
    return None


def _disfluency_after(disfluencies: Sequence[dict], pause: dict) -> dict | None:
    """The disfluency the gap was waiting for, or None."""
    start = float(pause["start"])
    end = float(pause["end"])
    for row in disfluencies:
        try:
            row_start = float(row["start"])
            row_end = float(row["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if row_end < start:
            continue
        if row_start <= end + DISFLUENCY_LEAD_TOLERANCE_S:
            return row
    return None


def _event_in(sound_events: Sequence[dict], pause: dict) -> dict | None:
    """The measured non-speech event occupying the gap, or None."""
    start = float(pause["start"])
    end = float(pause["end"])
    for event in sound_events:
        try:
            event_start = float(event["start"])
            event_end = float(event["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if (event_start <= end + EVENT_OVERLAP_TOLERANCE_S
                and event_end >= start - EVENT_OVERLAP_TOLERANCE_S):
            return event
    return None


def _speaker_of(utterance: dict | None,
                speakers: dict | None) -> str | None:
    if utterance is None:
        return None
    if speakers is not None:
        label = speakers.get(_utterance_index(utterance))
        return str(label) if label is not None else None
    label = utterance.get("speaker")
    return str(label) if label else None


def _required_coverage(pause: dict) -> tuple[str, ...]:
    """The measurements a verdict for this pause needs.

    A gap between two utterances is where a change of voice would hide,
    so it needs speaker attribution as well. A gap inside one utterance
    cannot hide one.
    """
    if pause.get("kind") == "utterance_gap":
        return (COVERAGE_SOUND_EVENTS, COVERAGE_SPEAKER_ATTRIBUTION)
    return (COVERAGE_SOUND_EVENTS,)


def speakers_from_voice_tracks(utterances: Sequence[dict],
                                voice_tracks: Sequence[dict] | None
                                ) -> dict | None:
    """The speaker label of each utterance, from measured voice tracks.

    `voice_tracks` is the shape the diarization lane measures and the
    M1b slot stores: `[{"track_id": ..., "spans": [[start, end], ...]}]`.
    An utterance takes the track that overlaps it most.

    None when there are no tracks, or when none of them touches an
    utterance. That is "not measured", which is not the same as "one
    speaker": a recording nobody separated has no voices to compare.
    """
    if not voice_tracks:
        return None
    labelled: dict = {}
    for track in voice_tracks:
        if not isinstance(track, dict):
            continue
        track_id = track.get("track_id")
        if not track_id:
            continue
        for span in track.get("spans") or []:
            try:
                span_start = float(span[0])
                span_end = float(span[1])
            except (TypeError, ValueError, IndexError):
                continue
            labelled.setdefault(str(track_id), []).append(
                (span_start, span_end))
    if not labelled:
        return None
    speakers: dict = {}
    for utterance in utterances:
        try:
            start = float(utterance["start"])
            end = float(utterance["end"])
        except (KeyError, TypeError, ValueError):
            continue
        best = None
        best_overlap = 0.0
        for track_id, spans in labelled.items():
            overlap = sum(max(0.0, min(end, span_end) - max(start, span_start))
                          for span_start, span_end in spans)
            if overlap > best_overlap:
                best_overlap = overlap
                best = track_id
        if best is not None:
            speakers[_utterance_index(utterance)] = best
    return speakers or None


def classify_pauses(pauses: Sequence[dict], *, utterances: Sequence[dict],
                     sound_events: Sequence[dict] | None = None,
                     speakers: dict | None = None,
                     disfluencies: Sequence[dict] | None = None
                     ) -> list[dict]:
    """Read every measured gap for what it carries.

    `pauses` are the rows `speech_take.pauses_between` measured;
    `utterances` are the rows `speech_take.utterance_spans` kept.

    `sound_events` are the measured non-speech events on the same
    clock, `speakers` maps an utterance index to its speaker label, and
    `disfluencies` are the rows `speech_take.disfluencies_in` measured.
    What was NOT passed is what did not run, and the row says so: the
    coverage is read off the evidence, so a caller cannot claim a
    measurement it did not make.
    """
    if disfluencies is None:
        disfluencies = []
        for utterance in utterances:
            disfluencies.extend(speech_take.disfluencies_in(utterance))

    coverage = {
        COVERAGE_SOUND_EVENTS: (COVERAGE_MEASURED if sound_events is not None
                                else COVERAGE_UNMEASURED),
        COVERAGE_SPEAKER_ATTRIBUTION: (
            COVERAGE_MEASURED if speakers is not None
            else COVERAGE_UNMEASURED),
    }

    rows: list[dict] = []
    for pause in pauses:
        try:
            start = float(pause["start"])
            end = float(pause["end"])
        except (KeyError, TypeError, ValueError):
            continue
        markers: list[str] = []
        evidence: dict[str, Any] = {}

        disfluency = _disfluency_after(disfluencies, pause)
        if disfluency is not None:
            markers.append(MARKER_DISFLUENCY_LEAD)
            evidence[MARKER_DISFLUENCY_LEAD] = {
                "token": disfluency.get("token"),
                "kind": disfluency.get("kind"),
                "start": disfluency.get("start"),
                "end": disfluency.get("end"),
            }

        left = _left_utterance(utterances, pause)
        right = _right_utterance(utterances, pause)
        if pause.get("kind") == "utterance_gap" and left is not None \
                and speech_take.ends_complete(left.get("text")):
            markers.append(MARKER_CLAUSE_FINAL)
            evidence[MARKER_CLAUSE_FINAL] = {
                "text": left.get("text"),
                "terminal": speech_take.ends_complete(left.get("text")),
            }

        left_speaker = _speaker_of(left, speakers)
        right_speaker = _speaker_of(right, speakers)
        if left_speaker is not None and right_speaker is not None \
                and left_speaker != right_speaker:
            markers.append(MARKER_TURN_CHANGE)
            evidence[MARKER_TURN_CHANGE] = {
                "speaker_before": left_speaker,
                "speaker_after": right_speaker,
            }

        event = _event_in(sound_events or [], pause)
        if event is not None:
            markers.append(MARKER_EVENT_OCCUPIED)
            evidence[MARKER_EVENT_OCCUPIED] = {
                "label": event.get("label"),
                "start": event.get("start"),
                "end": event.get("end"),
            }

        required = _required_coverage(pause)
        missing = [name for name in required
                   if coverage.get(name) != COVERAGE_MEASURED]
        if markers:
            verdict = LINGUISTICALLY_IMPORTANT
            basis = ("measured markers: " + ", ".join(markers))
        elif missing:
            verdict = UNDECIDED
            basis = ("no marker measured, and these measurements did not "
                     "run: " + ", ".join(missing))
        else:
            verdict = EDIT_SAFE
            basis = ("no marker measured, and every measurement that could "
                     "have produced one ran")

        rows.append({
            "start": round(start, 3),
            "end": round(end, 3),
            "gap_seconds": pause.get("gap_seconds"),
            "kind": pause.get("kind"),
            "verdict": verdict,
            "markers": markers,
            "coverage": dict(coverage),
            "basis": basis,
            "evidence": evidence,
        })
    return rows


def edit_safe_pauses(rows: Sequence[dict]) -> list[dict]:
    """The rows a cut may close: the ones read as `edit_safe`.

    Nothing else is returned. A row that is `linguistically_important`
    carries a measured reason to stay, and a row that is `undecided`
    carries no answer, and a cut made on either is a cut made without
    looking.
    """
    return [row for row in rows if row.get("verdict") == EDIT_SAFE]
