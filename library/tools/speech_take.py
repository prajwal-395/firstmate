"""FI-13: speech and take measurements from transcript data only.

Preserve all speech spans, with typed rate, pause, disfluency,
repetition, completeness and take-link records. Identical, rephrased
and repeated takes keep BOTH instances and are never auto-deleted:
every link below REPORTS a textual relation between two utterances -
it names no loser, carries no ``dropped_*`` span and no recommended
action. Which telling plays is judgement for FI-19 (alternate take
selection) and its policy; this module never makes it.

**What this is.** Deterministic measurements over M1 transcript words
(``transcript.words.json`` utterances) plus the M6 multicam clock
records for cross-source comparisons. No model, no audio decode, no
Resolve: the only inputs are word timings, utterance texts and clock
offsets a lane already measured.

**What this is not.** Not a retake cutter. ``retake_scan`` reports
abandoned tellings with a strike recommendation for the reel path;
this module's links carry no strike - the same two instances also
reach the model as two full utterance observations, so a reader that
ignores links still hears everything.

**Uncertainty.** A same-source link compares word timings on one
clock: no offset is applied, so no offset uncertainty travels with
it. A cross-source link carries each side's M6
``offset_to_reference_seconds`` and the measured spread of the direct
n-gram edge behind it (``(p95 - p5) / 2`` off
``direct_measurement``), or ``None`` with the basis stated when the
edge is transitive or the pair shares no M6 group. Offsets outside
that uncertainty refuse "same instant" - but that refusal is FI-16's
consumer-side verdict, not this record: the link states the numbers
and the basis, never the verdict.

**Floors.** One structural floor: a rephrased/repeated link needs a
shared ordered run of ``SHARED_RUN_TOKENS`` raw tokens. Two raw
tokens ("of the", "and then") recur constantly in fluent speech, so
a two-token run is not a distinctive-text claim; the floor keeps the
link one. Identical links need no floor: full-text equality is exact,
with no false-positive class. An abandoned first telling
(``abandoned_first``) is REPORTED on every link so a consumer can
tell a restarted flub from deliberate repetition - the link itself is
kept either way.
"""

from __future__ import annotations

import math
import re
from typing import Any

try:
    from library.tools.retake_scan import FILLERS
except ImportError:  # imported as `tools.*` from inside library/
    from tools.retake_scan import FILLERS

#: A rephrased/repeated link needs this many shared ordered raw tokens.
#: Structural, not taste: without content-word filtering (which would
#: need a stopword vocabulary this module refuses to carry), a
#: two-token run matches ordinary collocations everywhere. See module
#: docstring.
SHARED_RUN_TOKENS = 3

#: The smallest intra-utterance repeat worth naming: a single repeated
#: word ("the the") is a stutter, not a take shape - the same floor
#: ``retake_scan`` documents for its own longest-self-repeat.
MIN_REPEAT_TOKENS = 2

#: Terminal punctuation closing a delivered telling. The transcriber's
#: own signal, the same one ``retake_scan._ends_complete`` reads off
#: ``reel_build.SENTENCE_TERMINALS``; restated here so this
#: transcript-only module never imports the reel path.
SENTENCE_TERMINALS = frozenset({".", "?", "!"})

_PUNCTUATION = re.compile(r"[^a-z0-9\s']+")


def normalize_text(text: Any) -> str:
    """Lowercase, punctuation-stripped, whitespace-collapsed text."""
    collapsed = " ".join(str(text or "").lower().split())
    return _PUNCTUATION.sub("", collapsed).strip()


def tokens_of(text: Any) -> list[str]:
    """Normalized word tokens of a text; [] for empty."""
    normalized = normalize_text(text)
    return normalized.split() if normalized else []


def is_partial(token: str) -> bool:
    """A single-letter token that is not a word: an aborted word.

    ``retake_scan._is_partial``'s rule, restated without the reel
    import: "a" and "I" are words, "s" ("um s best") is a speaker
    stopping mid-word.
    """
    return len(token) == 1 and token.isalpha() and token not in ("a", "i")


def ends_complete(text: Any) -> bool:
    """Does this telling end on terminal punctuation?"""
    stripped = str(text or "").strip()
    return bool(stripped) and stripped[-1] in SENTENCE_TERMINALS


def _finite_span(start: Any, end: Any) -> tuple[float, float] | None:
    """A usable (start, end) float pair, or None when refused."""
    try:
        start_f, end_f = float(start), float(end)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(start_f) and math.isfinite(end_f)):
        return None
    if end_f <= start_f:
        return None
    return (start_f, end_f)


def utterance_spans(m1_doc: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    """Every usable utterance of an M1 transcript document.

    Returns ``(kept, dropped)``: kept utterances carry
    ``utterance_index`` (position in the document), ``start``/``end``
    seconds, ``text`` and timed ``words`` (``word``/``start``/``end``
    each). An utterance missing its own bounds is read off its words;
    one with no usable word timing at all is dropped WITH its reason -
    never silently, and the migration reports it.
    """
    kept: list[dict] = []
    dropped: list[dict] = []
    utterances = (m1_doc or {}).get("utterances") or []
    for index, utterance in enumerate(utterances):
        if not isinstance(utterance, dict):
            dropped.append({"utterance_index": index,
                            "reason": "not-a-mapping"})
            continue
        words: list[dict] = []
        for word in utterance.get("words") or []:
            if not isinstance(word, dict):
                continue
            token = str(word.get("text", word.get("word")) or "").strip()
            if not token:
                continue
            span = _finite_span(word.get("start"), word.get("end"))
            if span is None:
                continue
            words.append({"word": token, "start": span[0], "end": span[1]})
        span = _finite_span(utterance.get("start"), utterance.get("end"))
        if span is None and words:
            span = (words[0]["start"], words[-1]["end"])
        text = str(utterance.get("text") or "").strip()
        if not text and words:
            text = " ".join(word["word"] for word in words)
        if span is None:
            dropped.append({"utterance_index": index,
                            "reason": "no-usable-timing"})
            continue
        if not text:
            dropped.append({"utterance_index": index,
                            "reason": "no-text"})
            continue
        kept.append({"utterance_index": index, "start": span[0],
                     "end": span[1], "text": text, "words": words})
    return kept, dropped


def rate_of(utterance: dict[str, Any]) -> dict[str, Any]:
    """Speech rate over one utterance: words per second off M1 timings."""
    word_count = len(utterance.get("words") or [])
    span_seconds = float(utterance["end"]) - float(utterance["start"])
    return {"word_count": word_count,
            "span_seconds": round(span_seconds, 3),
            "words_per_second": round(word_count / span_seconds, 3)}


def pauses_between(utterances: list[dict]) -> list[dict]:
    """Every positive silence gap: intra-utterance word gaps plus
    inter-utterance gaps, each with its location and duration.

    Abutting words (zero gap) produce no record: a zero-length span
    cannot be a half-open interval, and widening one would invent
    evidence. No floor otherwise - a 40 ms hesitation and a 4 s
    silence are both gaps with durations, and judging them is the
    consumer's job.
    """
    pauses: list[dict] = []
    ordered = sorted(utterances, key=lambda u: (u["start"], u["end"]))
    for utterance in ordered:
        words = sorted(utterance.get("words") or [],
                       key=lambda w: (w["start"], w["end"]))
        for first, second in zip(words, words[1:]):
            gap = float(second["start"]) - float(first["end"])
            if gap > 0.0:
                pauses.append({
                    "start": float(first["end"]), "end": float(second["start"]),
                    "gap_seconds": round(gap, 3), "kind": "word_gap",
                    "utterance_index": utterance["utterance_index"]})
    for first, second in zip(ordered, ordered[1:]):
        gap = float(second["start"]) - float(first["end"])
        if gap > 0.0:
            pauses.append({
                "start": float(first["end"]), "end": float(second["start"]),
                "gap_seconds": round(gap, 3), "kind": "utterance_gap",
                "before_utterance_index": first["utterance_index"],
                "after_utterance_index": second["utterance_index"]})
    pauses.sort(key=lambda p: (p["start"], p["end"]))
    return pauses


def disfluencies_in(utterance: dict[str, Any]) -> list[dict]:
    """Fillers and aborted words in one utterance, each with its
    seconds. A bare "um" in flowing speech is still heard here - it
    is a vocalization with a timestamp, not a verdict on the telling.
    """
    out: list[dict] = []
    for word_index, word in enumerate(utterance.get("words") or []):
        token = word["word"].lower()
        if token in FILLERS:
            kind = "filler"
        elif is_partial(_PUNCTUATION.sub("", token)):
            kind = "partial"
        else:
            continue
        out.append({"token": word["word"], "kind": kind,
                    "start": word["start"], "end": word["end"],
                    "word_index": word_index})
    return out


def repetition_in(utterance: dict[str, Any]) -> dict | None:
    """Longest non-overlapping ordered token repeat inside one
    utterance, or None. Raw normalized tokens (no stopword list):
    the repeat must run ``MIN_REPEAT_TOKENS`` or more to be a run of
    anything rather than a stutter.
    """
    tokens = tokens_of(utterance.get("text"))
    count = len(tokens)
    for length in range(count // 2, MIN_REPEAT_TOKENS - 1, -1):
        for i in range(count - 2 * length + 1):
            window = tokens[i:i + length]
            for j in range(i + length, count - length + 1):
                if tokens[j:j + length] == window:
                    return {"tokens": window,
                            "first_token_index": i,
                            "second_token_index": j,
                            "length": length}
    return None


def completeness_of(utterance: dict[str, Any]) -> dict[str, Any]:
    """Whether the transcriber closed this telling: terminal
    punctuation or a trailing off. A measurement of the punctuation,
    never a verdict on the thought.
    """
    text = str(utterance.get("text") or "").strip()
    terminal = text[-1] if text else None
    return {"complete": ends_complete(text),
            "terminal": terminal if terminal in SENTENCE_TERMINALS else None}


def _longest_shared_run(first: list[str],
                        second: list[str]) -> list[str]:
    """Longest contiguous token run both lists share, or [] ."""
    best_start, best_length = 0, 0
    following = [0] * (len(second) + 1)
    for i in range(len(first) - 1, -1, -1):
        row = [0] * (len(second) + 1)
        for j in range(len(second) - 1, -1, -1):
            if first[i] == second[j]:
                row[j] = following[j + 1] + 1
                # Reverse traversal plus >= preserves the original
                # earliest-first-text tie break exactly.
                if row[j] >= best_length:
                    best_start, best_length = i, row[j]
        following = row
    return first[best_start:best_start + best_length]


def classify_pair(text_a: Any, text_b: Any) -> dict | None:
    """The textual relation between two utterance texts, or None.

    ``identical``: normalized full-text equality (no length floor -
    equality is exact). ``repeated``: the later telling restates from
    the shared head (the run opens it) while the tails diverge.
    ``rephrased``: the same ordered run elsewhere - the same point
    twice, the second time in new words. Anything sharing less than
    ``SHARED_RUN_TOKENS`` ordered tokens is no link at all.
    """
    return _classify_tokens(tokens_of(text_a), tokens_of(text_b))


def _classify_tokens(tokens_a, tokens_b):
    if tokens_a and tokens_a == tokens_b:
        return {"kind": "identical", "shared_run": tokens_a}
    run = _longest_shared_run(tokens_a, tokens_b)
    if len(run) < SHARED_RUN_TOKENS:
        return None
    if tokens_b[:len(run)] == run:
        return {"kind": "repeated", "shared_run": run}
    return {"kind": "rephrased", "shared_run": run}


def _m6_uncertainty(clock_a: dict | None,
                    clock_b: dict | None) -> tuple[Any, Any, Any, str]:
    """(offset_a, offset_b, uncertainty, basis) for a cross-source pair.

    Offsets are each side's M6 ``offset_to_reference_seconds``. The
    uncertainty is the measured spread of the direct n-gram edge
    (``(p95 - p5) / 2``) when both sides name the same group AND at
    least one side carries that direct measurement; otherwise None
    with the basis saying why - a transitive offset was never measured
    as a spread, and inventing one would launder a guess into evidence.
    """
    offset_a = (clock_a or {}).get("offset_to_reference_seconds")
    offset_b = (clock_b or {}).get("offset_to_reference_seconds")
    group_a, group_b = (clock_a or {}).get("group_id"), \
        (clock_b or {}).get("group_id")
    if group_a is None or group_a != group_b:
        return offset_a, offset_b, None, "no_shared_m6_group"
    for clock in (clock_a, clock_b):
        direct = (clock or {}).get("direct_measurement") or {}
        try:
            spread = (float(direct["p95_seconds"])
                      - float(direct["p5_seconds"])) / 2.0
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(spread) and spread >= 0.0:
            return offset_a, offset_b, round(spread, 3), \
                "m6_direct_edge_p95_p5"
    return offset_a, offset_b, None, "m6_transitive_unmeasured"


def _link_record(kind: str, shared_run: list[str], abundant_a: bool,
                 digest_a: str, utt_a: dict, digest_b: str, utt_b: dict,
                 comparison: str, offset_a: Any, offset_b: Any,
                 uncertainty: Any, basis: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "utterance_a": {"digest": digest_a,
                        "utterance_index": utt_a["utterance_index"],
                        "start_s": utt_a["start"], "end_s": utt_a["end"],
                        "text": utt_a["text"]},
        "utterance_b": {"digest": digest_b,
                        "utterance_index": utt_b["utterance_index"],
                        "start_s": utt_b["start"], "end_s": utt_b["end"],
                        "text": utt_b["text"]},
        # Which telling the link is ANCHORED on (A's clock carries the
        # observation); both instances are preserved regardless.
        "anchor": "utterance_a",
        "abandoned_first": abundant_a,
        "shared_run": shared_run,
        "comparison": comparison,
        "offset_a_to_reference_seconds": offset_a,
        "offset_b_to_reference_seconds": offset_b,
        "uncertainty_seconds": uncertainty,
        "uncertainty_basis": basis,
    }


def same_source_links(digest: str,
                      utterances: list[dict]) -> list[dict]:
    """Take links between ADJACENT utterances of one source.

    Adjacency only: without speaker labels, a shared phrase two turns
    apart is as likely a callback as a retake, and only the adjacent
    abandoned telling reads as a restart. Both instances are kept;
    ``abandoned_first`` tells the consumer which shape it saw.
    """
    ordered = sorted(utterances, key=lambda u: (u["start"], u["end"]))
    links: list[dict] = []
    for first, second in zip(ordered, ordered[1:]):
        classified = classify_pair(first["text"], second["text"])
        if classified is None:
            continue
        links.append(_link_record(
            classified["kind"], classified["shared_run"],
            not ends_complete(first["text"]),
            digest, first, digest, second,
            "same_clock", 0.0, 0.0, 0.0, "same_clock_no_offset"))
    return links


def cross_source_links(utterances_by_digest: dict[str, list[dict]],
                       clocks_by_digest: dict[str, dict | None]
                       ) -> list[dict]:
    """Take links across sources: every pair, both instances kept.

    Text needs no clock, so pairs link even with no shared M6 group -
    the record then carries ``uncertainty_seconds`` None and says so.
    Anchor ``utterance_a`` is the lesser ``(digest, utterance_index)``
    so the record is deterministic regardless of call order.
    """
    digests = sorted(utterances_by_digest)
    token_rows = {digest: [tokens_of(utterance["text"])
                           for utterance in utterances_by_digest[digest]]
                  for digest in digests}
    indexes = {}
    exact = {}
    for digest in digests:
        grams, texts = {}, {}
        for position, tokens in enumerate(token_rows[digest]):
            if tokens:
                texts.setdefault(tuple(tokens), set()).add(position)
            for offset in range(len(tokens) - SHARED_RUN_TOKENS + 1):
                gram = tuple(tokens[offset:offset + SHARED_RUN_TOKENS])
                grams.setdefault(gram, set()).add(position)
        indexes[digest], exact[digest] = grams, texts
    links: list[dict] = []
    for i, digest_a in enumerate(digests):
        for digest_b in digests[i + 1:]:
            clock_a = (clocks_by_digest or {}).get(digest_a)
            clock_b = (clocks_by_digest or {}).get(digest_b)
            same_group = (
                clock_a is not None and clock_b is not None
                and clock_a.get("group_id") is not None
                and clock_a.get("group_id") == clock_b.get("group_id"))
            comparison = ("cross_clock_via_m6" if same_group
                          else "cross_clock_unaligned")
            offset_a, offset_b, uncertainty, basis = _m6_uncertainty(
                clock_a, clock_b)
            for position_a, utt_a in enumerate(utterances_by_digest[digest_a]):
                tokens_a = token_rows[digest_a][position_a]
                candidates = set(exact[digest_b].get(tuple(tokens_a), ()))
                for offset in range(len(tokens_a) - SHARED_RUN_TOKENS + 1):
                    gram = tuple(tokens_a[offset:offset + SHARED_RUN_TOKENS])
                    candidates.update(indexes[digest_b].get(gram, ()))
                # A non-identical pair cannot pass without one shared
                # floor-length gram. This eliminates no possible link;
                # equal one/two-word utterances use the exact-text index.
                for position_b in sorted(candidates):
                    utt_b = utterances_by_digest[digest_b][position_b]
                    classified = _classify_tokens(
                        tokens_a, token_rows[digest_b][position_b])
                    if classified is None:
                        continue
                    first = utt_a if (digest_a, utt_a["utterance_index"]) < (
                        digest_b, utt_b["utterance_index"]) else utt_b
                    second = utt_b if first is utt_a else utt_a
                    first_digest = digest_a if first is utt_a else digest_b
                    second_digest = digest_b if first is utt_a else digest_a
                    links.append(_link_record(
                        classified["kind"], classified["shared_run"],
                        not ends_complete(first["text"]),
                        first_digest, first, second_digest, second,
                        comparison, offset_a, offset_b, uncertainty, basis))
    links.sort(key=lambda link: (
        link["utterance_a"]["digest"],
        link["utterance_a"]["utterance_index"],
        link["utterance_b"]["digest"],
        link["utterance_b"]["utterance_index"]))
    return links
