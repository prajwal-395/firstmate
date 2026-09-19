"""Retake and false-start candidates the segment-pair scan cannot see.

The captain, 2026-09-19, on six reel timelines: when a speaker says a
thing badly and then says the same thing better, the pipeline keeps
BOTH. Measured against the six marked spans, the existing cut lane
(``reel_build.redundant_takes``) misses every one of them, each for a
different reason:

- Reel 06: the retake's second telling grows a new tail (``and that's
  because every source ...``), so the whole-segment pair scores
  containment 0.69 / Jaccard 0.39, under both bars - while the shared
  head is a word-stream ``repeat`` at similarity 1.0 that
  ``redundant_takes`` discards because its two windows sit in different
  segments.
- Reel 08: the pair scan FINDS the retake and ``judge_take_cuts``
  withdraws it as ``cross_speaker`` - on Craig's mic hearing Akshita's
  own words back at the same instant (bleed), not on a second voice.
- Reel 11: the pair scan FINDS the retake and the judge withdraws it as
  ``mid_word_edge`` - the telling opens mid-word because WhisperX
  chunked two segments across one word (``Absolutely.`` runs to 782.63
  while the next segment opens at 782.39).
- Reel 21: an abandoned telling restated in new words
  (``your website is just a part of what's maybe`` -> ``your website
  is just a part of your whole profile``) - below every lexical bar.
- Reel 24: a false start and its restart inside ONE segment
  (``there's a very strong um okay there's a very strong``) - the pair
  scan needs two segments and the 4-word word-stream window needs three
  shared content words where only two repeat.
- Reel 03: a mid-sentence stumble (``C Rm ... best C RMs, um s best``)
  whose recovery continues the same segment - single-word repeats the
  word stream cannot see, inside a segment the pair scan cannot split.

This module MEASURES those shapes. It decides nothing: every finding
is REPORTED with both texts, its measured properties and a concrete
recommended action, for the model at selection time
(``step_3_04_select_reels.bridge.retake_candidates_inside``) and for
the operator at build time. Which telling plays is judgement; this
module never names one. A 4-word window match is not a telling, so a
window match cannot authorise dropping a whole telling mechanically -
but it can point at it (measured on Reel 08, where a similarity-1.0
window sits inside a 9-second telling whose sentences say different
things).

What it deliberately does NOT flag
----------------------------------
A speaker repeating a phrase for emphasis, a question echoed before it
is answered, and a thesis restated in a conclusion must all survive.
Three guards hold that line, and each is a fact about the transcript
rather than a threshold:

- one telling, one voice: both tellings carry the same speaker;
- first ends before second begins: overlapping tellings are
  simultaneous speech or bleed, never first-then-retake;
- the abandoned telling is INCOMPLETE: it ends without terminal
  punctuation. An emphasis repeat is a complete sentence said again;
  a question and its echo belong to two voices; a restated thesis
  sits sentences - and usually minutes - from its first telling,
  never adjacent to it.

A complete sentence restated adjacently in new words is therefore left
alone on purpose: word overlap cannot tell it from deliberate
restatement, and a reported candidate there would teach the model to
cut rhetoric. ``possible_retellings`` already owns the cross-turn
paraphrase; this module owns the adjacent abandoned telling.

No new numbers
--------------
Every bar here is read off an existing one: ``TAKE_SIMILARITY`` and the
4-word ``repeat`` band are the word stream's own, ``CUT_WINDOW_SECONDS``
and ``DURATION_RATIO`` are the cut's, ``EDGE_TOLERANCE`` is the judge's,
``SENTENCE_TERMINALS`` is the sentence reader's. The one structural
floor - a repeated prefix needs TWO content words to be a prefix of
anything - is documented where it is used.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence


#: Fillers the transcriber writes when a speaker stalls mid-telling.
#: MEASUREMENT, not a verdict: these tokens locate the edit region
#: between a flubbed run-up and its restart (Reel 24's "um okay",
#: Reel 03's "um s"). Nothing is cut on them alone - an intra-segment
#: restart also needs the repeated run, and a bare "um" in flowing
#: speech matches nothing and reports nothing.
FILLERS = frozenset({"um", "uh", "uhm", "er", "ah", "hmm"})

#: How much of its own lead-in and trail-off an abandoned telling may
#: carry. An abandoned run-up opens with throat-clearing ("Um I've",
#: "Yeah, so" - two content tokens) and trails off ("what's maybe" -
#: two); more than that either side is a telling of its own, not a
#: run-up. Measured on Reel 21, whose abandoned telling carries
#: exactly two content tokens before the shared run ("case",
#: "problem") and two after ("what's", "maybe").
RUN_UP_CONTENT_TOKENS = 2


def _proposal():
    from library.tools import reel_proposal as rp
    return rp


def _token_stream(segment: dict) -> list:
    """``(token, start, end, is_content)`` for every timed word."""
    import re
    rp = _proposal()
    out = []
    for word in segment.get("words") or ():
        if not word.get("timed"):
            continue
        token = re.sub(r"[^a-z0-9']+", "",
                       str(word.get("word", "")).lower())
        if not token:
            continue
        try:
            start, end = float(word["start"]), float(word["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end > start:
            out.append((token, start, end,
                        token not in rp._TAKE_STOPWORDS and len(token) > 1))
    return out


def _content_stream(segment: dict) -> list:
    """``(token, start, end)`` content words of one segment, in order."""
    rp = _proposal()
    out = [(token, start, end) for token, start, end, is_content
           in _token_stream(segment) if is_content]
    if not out:
        # No timed words: fall back to the segment text at the
        # segment's own span, the same fallback `_timed_content_words`
        # uses. Timings are coarse but the order is exact.
        start = float(segment.get("timeline_start", 0.0))
        for token in rp._content_words_ordered(segment.get("text", "")):
            out.append((token, start, start))
    return out


def _is_partial(token: str) -> bool:
    """A single-letter token that is not a word: an aborted word.

    "a" and "I" are words; "s" (Reel 03's "um s best") and "C" (its
    "C Rm") are a speaker stopping mid-word. Structural, not a list:
    length one, alpha, not a word.
    """
    return len(token) == 1 and token.isalpha() and token not in ("a", "i")


def _ends_complete(text: str) -> bool:
    """Does this telling end on terminal punctuation?

    Read off the transcriber's own punctuation, the same signal
    ``sentence_spans`` reads: an abandoned telling trails off mid-thought
    (``what's maybe``, ``concise but``), a delivered one closes
    (``AI checks both.``).
    """
    from library.tools.reel_build import SENTENCE_TERMINALS
    text = (text or "").strip()
    return bool(text) and text[-1] in SENTENCE_TERMINALS


def _longest_self_repeat(stream: list) -> Optional[dict]:
    """Longest ordered content-word repeat inside one token stream.

    Non-overlapping longest common subsequence of the stream against
    itself: the speaker saying a run of words and then saying that run
    again. A single repeated word (``the the``, ``best best best``) is
    a stutter, not a false start - a prefix needs TWO words to be a
    prefix of anything, so repeats under two content words are not
    reported. Returns ``None`` where nothing repeats.
    """
    tokens = [token for token, _, _ in stream]
    n = len(tokens)
    best = None
    # Longest first: the first length with a non-overlapping match wins,
    # so the reported repeat is the maximal one, not a fragment of it.
    for length in range(n // 2, 1, -1):
        found = None
        for i in range(n - 2 * length + 1):
            window = tokens[i:i + length]
            for j in range(i + length, n - length + 1):
                if tokens[j:j + length] == window:
                    found = (i, j, length)
                    break
            if found is not None:
                break
        if found is not None:
            i, j, length = found
            best = {"first_start": stream[i][1],
                    "first_end": stream[i + length - 1][2],
                    "second_start": stream[j][1],
                    "second_end": stream[j + length - 1][2],
                    "words": tokens[i:i + length]}
            break
    return best


def _tail_drop(segment: dict, run: list) -> Optional[tuple]:
    """Where an abandoned telling's run-up starts, and where it ends.

    The telling trails off into a run-up the next telling restates
    (``and your website is just a part of what's maybe``). The drop
    starts at the run's LAST occurrence in the telling - an earlier
    occurrence is subject matter, the last one is the run-up - extended
    back over joining words (``and your``), and ends at the telling's
    end. Returns ``(drop_start, drop_end)`` or ``None`` when the run
    is not the telling's tail: more than ``RUN_UP_CONTENT_TOKENS``
    content words after it means the telling goes on to say something
    of its own, and cutting from the run would eat that with it.
    Measured both ways on Reel 21 (two after: ``what's maybe``) and
    Reel 06 (nine after: the telling restates, then argues on).
    """
    content = _content_stream(segment)
    tokens = [token for token, _, _ in content]
    at = None
    for i in range(len(tokens) - len(run) + 1):
        if tokens[i:i + len(run)] == run:
            at = i
    if at is None:
        return None
    if len(tokens) - (at + len(run)) > RUN_UP_CONTENT_TOKENS:
        return None
    full = _token_stream(segment)
    run_start = content[at][1]
    pos = next((k for k, entry in enumerate(full)
                if entry[1] == run_start and entry[2] == content[at][2]),
               None)
    if pos is None:
        return None
    while pos > 0 and not full[pos - 1][3]:
        pos -= 1
    try:
        seg_end = float(segment["timeline_end"])
    except (KeyError, TypeError, ValueError):
        return None
    return (full[pos][1], seg_end)


def false_starts(start: float, end: float, transcript: dict) -> List[dict]:
    """Abandoned tellings: a false start and the restart beside it.

    Two shapes, both reported, neither cut:

    - INTRA-SEGMENT: one bound segment carries an ordered content-word
      repeat (``there's a very strong ... um okay ... there's a very
      strong``). The first copy is the flubbed run-up, the second the
      restart. Parallel structure (``you're gonna X, you're gonna Y``)
      is not that: the telling must end mid-thought, or the edit
      region between the copies must carry audible disfluency - a
      filler or an aborted word - for the repeat to read as a restart
      rather than rhetoric.
    - TRUNCATED PREFIX: a bound segment ending mid-thought whose
      content-word head reopens the next same-speaker segment
      (``your website is just a part of what's maybe`` ->
      ``and your website is just a part of your whole profile``).
      The first telling is a truncated prefix of the span that follows
      it - the degenerate case the field-test note names. The drop is
      the run-up's tail (`_tail_drop`), never the whole telling: what
      the speaker said before reaching for the restart survives.

    Single-word stutters inside an otherwise complete sentence
    (``best C RMs, um s best``) are BELOW this detector's floor and are
    said to be: pointing at the span is honest, word-level surgery on a
    stutter is the model's verdict on the span's own text, not a span
    this module can bound.
    """
    rp = _proposal()
    inside = [s for s in rp._speech_within(
        rp.bound_segments(transcript), float(start), float(end))]
    out: List[dict] = []

    for index, segment in enumerate(inside):
        speaker = segment.get("speaker") or ""
        if not speaker:
            continue
        try:
            seg_start = float(segment["timeline_start"])
            seg_end = float(segment["timeline_end"])
        except (KeyError, TypeError, ValueError):
            continue

        stream = _content_stream(segment)
        repeat = _longest_self_repeat(stream) if len(stream) >= 4 else None
        if repeat is not None and (
                not _ends_complete(segment.get("text", ""))
                or _edit_region_disfluent(segment, repeat)):
            out.append({
                "kind": "false_start",
                "shape": "restart_inside_one_segment",
                "speaker": speaker,
                "dropped_start": round(repeat["first_start"], 2),
                "dropped_end": round(repeat["first_end"], 2),
                "dropped_text": " ".join(repeat["words"]),
                "kept_start": round(repeat["second_start"], 2),
                "kept_end": round(repeat["second_end"], 2),
                "kept_text": " ".join(repeat["words"]),
                "basis": (
                    f"one segment says {' '.join(repeat['words'])!r} "
                    f"twice ({repeat['first_start']:.2f}-"
                    f"{repeat['first_end']:.2f}s and "
                    f"{repeat['second_start']:.2f}-"
                    f"{repeat['second_end']:.2f}s) - the first copy is "
                    f"the flubbed run-up and the second the restart"),
                "recommended_action": (
                    f"strike {repeat['first_start']:.2f}-"
                    f"{repeat['first_end']:.2f}s (the flubbed run-up) "
                    f"and keep the restart, or redraw the span past "
                    f"the telling that reads worse"),
            })

        # Truncated prefix of the following span.
        following = inside[index + 1] if index + 1 < len(inside) else None
        if following is not None and (
                following.get("speaker") or "") == speaker:
            try:
                f_start = float(following["timeline_start"])
                f_end = float(following["timeline_end"])
            except (KeyError, TypeError, ValueError):
                f_start = f_end = None
            if (f_start is not None and f_start >= seg_end
                    and _gap_ok(seg_end, f_start)
                    and not _ends_complete(segment.get("text", ""))
                    and not _verbatim_pair(segment, following)):
                head = _head_match(stream, _content_stream(following))
                tail = _tail_drop(segment, head) if head else None
                if head is not None and tail is not None:
                    out.append({
                        "kind": "false_start",
                        "shape": "abandoned_telling_restated",
                        "speaker": speaker,
                        "dropped_start": round(tail[0], 2),
                        "dropped_end": round(tail[1], 2),
                        "dropped_text": (segment.get("text")
                                         or "").strip(),
                        "kept_start": round(f_start, 2),
                        "kept_end": round(f_end, 2),
                        "kept_text": (following.get("text")
                                      or "").strip(),
                        "basis": (
                            f"an unfinished telling "
                            f"({seg_start:.2f}-{seg_end:.2f}s) trails "
                            f"into a run-up ({tail[0]:.2f}-"
                            f"{tail[1]:.2f}s) the next telling "
                            f"restates from its head "
                            f"({' '.join(head)!r}) at {f_start:.2f}s"),
                        "recommended_action": (
                            f"strike {tail[0]:.2f}-{tail[1]:.2f}s (the "
                            f"abandoned run-up) and keep the restated "
                            f"one at {f_start:.2f}-{f_end:.2f}s"),
                    })
    return out


def _edit_region_disfluent(segment: dict, repeat: dict) -> bool:
    """Does the span between the two copies carry audible disfluency?

    A filler (``um``) or an aborted word (``s``, ``C``) between the
    copies is the speaker stalling before the restart - the sound a
    false start makes. Parallel structure has none of that.
    """
    for token, start, end, _ in _token_stream(segment):
        if start >= repeat["first_end"] and end <= repeat["second_start"]:
            if token in FILLERS or _is_partial(token):
                return True
    return False


def _gap_ok(first_end: float, second_start: float) -> bool:
    from library.tools.reel_build import CUT_WINDOW_SECONDS
    return second_start - first_end <= CUT_WINDOW_SECONDS


def _verbatim_pair(a: dict, b: dict) -> bool:
    """Would the pair scan cut these two segments?

    Reported candidates are what the cut lane MISSES. A pair meeting
    the cut bars is the cut lane's business (found or withdrawn with
    its reason) - reporting it here too would name one repetition
    twice and invite two different verdicts on it.
    """
    from library.tools import reel_build as rb
    containment, jaccard = rb._pair_scores(a, b)
    if (containment < rb.CUT_CONTAINMENT
            or jaccard < rb.CUT_JACCARD):
        return False
    try:
        da = float(a["timeline_end"]) - float(a["timeline_start"])
        db = float(b["timeline_end"]) - float(b["timeline_start"])
    except (KeyError, TypeError, ValueError):
        return False
    if da <= 0 or db <= 0:
        return False
    return max(da, db) / min(da, db) <= rb.DURATION_RATIO


def _head_match(first_stream: list, second_stream: list
                ) -> Optional[list]:
    """Ordered content-word run opening the second stream and present
    in the first, two words or more.

    The match must OPEN the following telling: a shared phrase buried
    mid-sentence in both is subject matter recurring, not a restart.
    """
    first = [token for token, _, _ in first_stream]
    second = [token for token, _, _ in second_stream]
    if len(first) < 2 or len(second) < 2:
        return None
    # Longest head run first.
    for length in range(min(len(first), len(second)), 1, -1):
        head = second[:length]
        for i in range(len(first) - length + 1):
            if first[i:i + length] == head:
                return head
    return None


def paraphrases(start: float, end: float, transcript: dict) -> List[dict]:
    """Same point twice in new words, adjacent and abandoned.

    Consecutive same-speaker bound segments sharing an ordered run of
    two or more content words, where the first telling ends
    mid-thought. Below every lexical bar by construction (a pair the
    cut lane would take is ``verbatim``'s, not this), across no turn
    (a crossed turn is ``possible_retellings``' territory), and
    reported - choosing the telling that reads better is judgement,
    and the two texts plus the gap are what judging it takes. The
    drop is the run-up's tail (`_tail_drop`), for the same reason as
    the truncated prefix: what the speaker said before reaching for
    the restart survives.
    """
    rp = _proposal()
    inside = [s for s in rp._speech_within(
        rp.bound_segments(transcript), float(start), float(end))]
    out: List[dict] = []
    for index in range(len(inside) - 1):
        first, second = inside[index], inside[index + 1]
        speaker = first.get("speaker") or ""
        if not speaker or (second.get("speaker") or "") != speaker:
            continue
        try:
            b_start = float(second["timeline_start"])
            b_end = float(second["timeline_end"])
        except (KeyError, TypeError, ValueError):
            continue
        try:
            a_end = float(first["timeline_end"])
        except (KeyError, TypeError, ValueError):
            continue
        if b_start < a_end or not _gap_ok(a_end, b_start):
            continue
        if _ends_complete(first.get("text", "")):
            # A delivered sentence said again is emphasis until a
            # model says otherwise - not an abandoned telling.
            continue
        if _verbatim_pair(first, second):
            continue
        run = _shared_run(_content_stream(first),
                          _content_stream(second))
        if run is None:
            continue
        tail = _tail_drop(first, run)
        if tail is None:
            continue
        out.append({
            "kind": "paraphrase",
            "shape": "adjacent_restatement",
            "speaker": speaker,
            "dropped_start": round(tail[0], 2),
            "dropped_end": round(tail[1], 2),
            "dropped_text": (first.get("text") or "").strip(),
            "kept_start": round(b_start, 2),
            "kept_end": round(b_end, 2),
            "kept_text": (second.get("text") or "").strip(),
            "basis": (
                f"an unfinished telling shares {' '.join(run)!r} in "
                f"order with the telling that follows it - the same "
                f"point twice, the second time complete"),
            "recommended_action": (
                f"strike {tail[0]:.2f}-{tail[1]:.2f}s (the abandoned "
                f"run-up) and keep {b_start:.2f}-{b_end:.2f}s, or "
                f"redraw the span past the telling that reads worse"),
        })
    return out


def _shared_run(first_stream: list, second_stream: list
                ) -> Optional[list]:
    """Longest ordered content-word run the two streams share, two
    words or more, anywhere in either stream."""
    first = [token for token, _, _ in first_stream]
    second = [token for token, _, _ in second_stream]
    for length in range(min(len(first), len(second)), 1, -1):
        for i in range(len(first) - length + 1):
            window = first[i:i + length]
            for j in range(len(second) - length + 1):
                if second[j:j + length] == window:
                    return window
    return None


def verbatim_reports(start: float, end: float,
                     transcript: dict) -> List[dict]:
    """Word-stream verbatims across segments, REPORTED with a strike.

    ``duplicate_takes`` finds repeats off the word stream that the
    segment-pair scan cannot see - a shared head whose tails diverge
    (Reel 06, similarity 1.0 across two segments the pair scan scores
    0.69/0.39). ``redundant_takes`` only keeps those findings when
    both windows sit in ONE segment, so every cross-segment verbatim
    is measured and then discarded by the cut lane.

    These are REPORTED, not promoted to cuts, and the reason is
    measured on Reel 08: a 4-word window match at similarity 1.0 can
    sit inside a 9-second telling whose sentences say different things
    (the audit story against the ranking punchline). A window is not
    a telling, so a window match cannot authorise dropping the whole
    telling - but it CAN point at it. Each finding carries the shared
    head, both windows, the whole first telling it sits in, and the
    strike the model verdicts: drop the telling whose head repeats,
    keep the later one.

    Only the finding's own bands: ``repeat`` (the 4-word window at
    ``TAKE_SIMILARITY``), plus a weak-band (3-word) finding at
    similarity EXACTLY 1.0 - an exact content-vocabulary match, not a
    threshold: Reel 06's shared head reads 1.0 on three words
    (``seen 10 person``) because the fourth splits plural against
    singular. Inexact weak-band findings stay where they are
    (moment-level advisory): at 0.667 a three-word match is ordinary
    speech recurring. One speaker on both windows, the kept window
    after the dropped one inside ``CUT_WINDOW_SECONDS``, durations
    within ``DURATION_RATIO``, and no other speaker's words between
    them (a crossed turn is a retelling, not a retake). A pair the cut
    lane already pairs is the cut lane's business, found or withdrawn
    with its reason - never doubled here.
    """
    from library.tools import reel_build as rb

    rp = _proposal()
    hits = rp.duplicate_takes(float(start), float(end), transcript)
    out = []
    for hit in hits:
        if hit.get("band") != "repeat" and not (
                hit.get("band") == "possible"
                and float(hit.get("similarity", 0.0)) == 1.0):
            continue
        try:
            first_start = float(hit["first_start"])
            first_end = float(hit["first_end"])
            second_start = float(hit["second_start"])
            second_end = float(hit["second_end"])
        except (KeyError, TypeError, ValueError):
            continue
        if not second_start > first_end:
            continue
        if second_start - first_end > rb.CUT_WINDOW_SECONDS:
            continue
        first_segs = [s for s in rp.bound_segments(transcript)
                      if float(s["timeline_end"]) > first_start
                      and float(s["timeline_start"]) < first_end]
        if len(first_segs) != 1:
            # The repeated head spans a segment boundary: no whole
            # telling to drop, so no cut. Reported by the word stream
            # itself; inventing a partial drop would strand a fragment.
            continue
        telling = first_segs[0]
        speaker = telling.get("speaker") or ""
        if not speaker:
            continue
        kept_segs = [s for s in rp.bound_segments(transcript)
                     if float(s["timeline_end"]) > second_start
                     and float(s["timeline_start"]) < second_end
                     and (s.get("speaker") or "") == speaker]
        if not kept_segs:
            continue
        between = [s for s in rp.bound_segments(transcript)
                   if float(s["timeline_end"]) > first_end
                   and float(s["timeline_start"]) < second_start
                   and (s.get("speaker") or "")
                   and (s.get("speaker") or "") != speaker]
        if between:
            continue
        try:
            drop_start = float(telling["timeline_start"])
            drop_end = float(telling["timeline_end"])
            keep_start = float(kept_segs[0]["timeline_start"])
            keep_end = float(kept_segs[-1]["timeline_end"])
        except (KeyError, TypeError, ValueError):
            continue
        drop_dur = drop_end - drop_start
        keep_dur = keep_end - keep_start
        if drop_dur <= 0 or keep_dur <= 0:
            continue
        if max(drop_dur, keep_dur) / min(drop_dur, keep_dur) \
                > rb.DURATION_RATIO:
            continue
        telling_text = (telling.get("text") or "").strip()
        if _verbatim_pair(telling, kept_segs[0]) and len(kept_segs) == 1:
            continue
        out.append({
            "kind": "verbatim_retelling",
            "shape": "shared_head_across_segments",
            "speaker": speaker,
            "dropped_start": round(drop_start, 2),
            "dropped_end": round(drop_end, 2),
            "dropped_text": telling_text,
            "kept_start": round(keep_start, 2),
            "kept_end": round(keep_end, 2),
            "kept_text": " ".join(
                (s.get("text") or "").strip() for s in kept_segs),
            "similarity": round(float(hit.get("similarity", 0.0)), 3),
            "shared_head": (f"{first_start:.2f}-{first_end:.2f}s said "
                            f"again at {second_start:.2f}-"
                            f"{second_end:.2f}s"),
            "basis": (
                f"the telling's head repeats word for word "
                f"(similarity {float(hit.get('similarity', 0.0)):.2f}: "
                f"{first_start:.2f}-{first_end:.2f}s said again at "
                f"{second_start:.2f}-{second_end:.2f}s) while the two "
                f"tellings' tails diverge past the pair-scan bars - "
                f"one telling said twice, the second time kept"),
            "recommended_action": (
                f"strike {drop_start:.2f}-{drop_end:.2f}s (the first "
                f"telling) and keep {keep_start:.2f}-{keep_end:.2f}s, "
                f"or redraw the span past the telling that reads worse"),
        })
    return out


def telling_properties(start: float, end: float,
                       transcript: dict) -> dict:
    """One telling's measured properties, for the model judging it.

    How long it runs, how many content words it says, whether the
    transcriber closed it complete, and the disfluency heard inside it
    (fillers and aborted words with their seconds). The captain's
    "more concisely and clearly" is read off exactly these: fewer
    content words for the same point is concise, complete and
    undisfluent is clear. REPORTS, never prefers.
    """
    rp = _proposal()
    words = 0
    disfluent = []
    for segment in rp.bound_segments(transcript or {}):
        try:
            seg_start = float(segment["timeline_start"])
            seg_end = float(segment["timeline_end"])
        except (KeyError, TypeError, ValueError):
            continue
        if seg_end <= float(start) or seg_start >= float(end):
            continue
        for token, word_start, word_end, is_content in _token_stream(
                segment):
            if word_end <= float(start) or word_start >= float(end):
                continue
            if is_content:
                words += 1
            if token in FILLERS or _is_partial(token):
                disfluent.append(f"{token}@{word_start:.2f}s")
    complete = False
    for segment in rp.bound_segments(transcript or {}):
        try:
            seg_start = float(segment["timeline_start"])
            seg_end = float(segment["timeline_end"])
        except (KeyError, TypeError, ValueError):
            continue
        if seg_start <= float(start) < seg_end:
            complete = _ends_complete(segment.get("text", ""))
            break
    return {"duration_seconds": round(float(end) - float(start), 2),
            "content_words": words,
            "ends_complete": complete,
            "disfluencies": disfluent}


def scan_span(start: float, end: float, transcript: dict) -> dict:
    """Every retake-shaped repetition in a span the cut lane misses.

    All REPORTED, none cut: false starts, paraphrases and
    cross-segment word-stream verbatims, each with both texts, its
    measured basis and a concrete recommended action - for the model
    at selection time and the operator at build time. A repetition
    the pair scan already pairs is the cut lane's, found or withdrawn
    with its reason, and is never doubled here.
    """
    reported = []
    seen = set()
    for candidate in (false_starts(start, end, transcript)
                      + paraphrases(start, end, transcript)
                      + verbatim_reports(start, end, transcript)):
        # One pair, one candidate: a truncated-prefix false start and
        # an adjacent paraphrase can both match the same two tellings
        # (the shared run opens the following telling). The false
        # start reads more specifically - an abandoned telling
        # restated from its head - so it wins the pair and the
        # paraphrase twin is dropped, never doubled.
        key = (round(candidate["dropped_start"], 2),
               round(candidate["kept_start"], 2))
        if key in seen:
            continue
        seen.add(key)
        reported.append(candidate)
    reported.sort(key=lambda c: (c["dropped_start"], c["kept_start"]))
    return {"reported": reported}
