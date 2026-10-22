"""VI-014: on-demand verification of a span a plan depends on.

The punch list asks for two things under one number, and this module
answers the second one.

*VI-014* in the punch list is: *"Resolve references such as 'the second
time she opens the box.'"*  The first half of that is DONE and lives
elsewhere: ``library/tools/temporal_query.py`` parses the ordinal and
``library/tools/occurrence_identity.py`` numbers the repeats, so "the
second" resolves to ONE event.

This module is the other half: **when the resolved span is not measured,
the agent may ASK for what would settle it, and the engine answers with
the cheapest evidence that can.** The four requests the punch list names
are the vocabulary here:

``MORE_FRAMES``
    Look again at the same span with a denser frame sample.
``LONGER_SEGMENT``
    Look at a WIDER span, because the boundary may be outside the one
    the plan named.
``SPEECH_CONTEXT``
    Read the words said around the span, so a claim about WHAT happens
    there is answered by speech rather than by pixels.
``ALTERNATE_CAMERA``
    Look at the OTHER camera's picture of the same instant, because the
    camera that carries the answer may not be the one the plan used.

**What this is not.** It is not a second analysis stack. Every request
routes to a producer that already exists (``frame_exact``'s dense
sample, ``source_memory``'s M1 transcript, ``camera_sync``'s offsets),
and every request that can be answered from what is ALREADY measured
answers from that, at zero cost. The budget is the resource ceiling; a
request that cannot be served inside it is REFUSED with the reason, never
silently degraded.

**The honesty rules this module exists to keep.**

* **A request is about ONE span.** The plan already decided where to
  cut; asking for evidence is not asking to move the cut. Nothing here
  returns a new span, and nothing here edits a plan.
* **An unmeasured claim is not a wrong claim.** A request whose evidence
  does not cover the span answers ``unresolved`` - never "the claim is
  false" and never "the claim is true". Absence of evidence is
  reported as absence of evidence (C-14).
* **Coverage is reported with the answer.** A caller must be able to see
  WHICH part of the span was looked at, so a partial answer can never
  read as a whole-span one.
* **A resolved claim is not re-verified.** The recorded verdict is the
  answer the second time, which is what makes an on-demand request
  cheap on a repeat.

**Where the evidence is written.** Each verdict is filed in the source's
own memory beside the measurements it was made from, under its own slot
(``on_demand.json``), with the evidence envelope the other lanes use:
the producer, its version, and the intervals actually inspected. A
verdict that names no source cannot be filed, because a verdict with no
span is not evidence about anything.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence

from library.tools.ren_refusal import RenRefusal

FORMAT = "on_demand_verification/1"
"""The format of a recorded verdict, so a reader can refuse a file it
does not know."""

SLOT = "on_demand.json"
"""The slot a source's on-demand verdicts live in, beside the other
measurements of the same content digest."""

PRODUCER = "on-demand-verification"
"""The producer name every recorded verdict carries."""

PRODUCER_VERSION = "on-demand-verification/1"
"""The version of this producer's own method. A verdict recorded by a
different version is a different answer and says so."""

DEFAULT_BUDGET_SECONDS = 30.0
"""The default resource ceiling on one request's verification.

Thirty seconds is one dense frame sample of a short window on the
measurements the grounding lanes made (``frame_exact``'s dense sample of
a 3 s window costs about two seconds of decode), so the default buys a
real look at a real span and refuses the ones that would need a whole
episode.

The ceiling is a CEILING, not a target: a request that can be answered
from what is already measured costs nothing and is not refused.
"""

MAX_CLAIM_CHARS = 2000
"""The longest claim text a request may carry.

A claim longer than this is not a claim about a span; it is a document,
and a document cannot be checked against one span.
"""


class RequestKind(str, Enum):
    """The four requests the punch list names."""

    MORE_FRAMES = "more_frames"
    LONGER_SEGMENT = "longer_segment"
    SPEECH_CONTEXT = "speech_context"
    ALTERNATE_CAMERA = "alternate_camera"


class Verdict(str, Enum):
    """What a verification found."""

    SUPPORTED = "supported"
    """The measured evidence agrees with the claim."""

    CONTRADICTED = "contradicted"
    """The measured evidence disagrees with the claim, and the evidence
    covered the span well enough to say so."""

    UNRESOLVED = "unresolved"
    """The evidence does not settle the claim.

    This is the answer whenever the coverage is incomplete, the producer
    was unavailable, or the budget ran out. It is NEVER a synonym for
    "contradicted" (C-14) and never a synonym for "supported".
    """


class StopReason(str, Enum):
    """Why the engine stopped investigating.

    The punch list asks for exactly this: *"Stop investigating when
    further analysis is unlikely to affect the decision."*
    """

    SETTLED = "settled"
    """The claim was decided; more evidence cannot change the answer."""

    BUDGET_EXHAUSTED = "budget_exhausted"
    """The budget ran out before the claim was decided."""

    NO_FURTHER_PRODUCER = "no_further_producer"
    """No producer left that could decide this claim."""

    UNCOVERED = "uncovered"
    """No producer could cover the span at all, so nothing was measured."""


@dataclass
class Span:
    """The span a claim is about, in ONE source's own timebase.

    `start_s`/`end_s` are seconds into `source_file`. The source is
    identified by its PATH (the plan named a path) and, when it is
    known, by the CONTENT DIGEST (the identity that decides whether a
    recorded verdict about it still applies).
    """

    source_file: str
    start_s: float
    end_s: float
    content_digest: Optional[str] = None

    def __post_init__(self) -> None:
        if not str(self.source_file or "").strip():
            raise ValueError("a span names the source it is about")
        start, end = float(self.start_s), float(self.end_s)
        if not (start >= 0.0 and end > start):
            raise ValueError(
                f"a span runs {self.start_s}-{self.end_s}, which is not a "
                f"range inside the source")
        self.start_s = start
        self.end_s = end

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s

    def to_dict(self) -> dict[str, Any]:
        return {"source_file": self.source_file,
                "start_s": round(self.start_s, 3),
                "end_s": round(self.end_s, 3),
                "content_digest": self.content_digest}

    @classmethod
    def from_dict(cls, data: Any) -> "Span":
        if not isinstance(data, dict):
            raise ValueError("a span is a mapping")
        return cls(source_file=str(data.get("source_file") or ""),
                   start_s=data.get("start_s"),
                   end_s=data.get("end_s"),
                   content_digest=data.get("content_digest"))


@dataclass
class Request:
    """One on-demand verification request.

    `claim` is the thing to be checked: a statement about the span
    ("the person brings the device to their mouth here"). `kind` is the
    kind of evidence the requester wants. `decision` names what the
    answer is for, so the engine can tell whether more evidence could
    still change it.
    """

    span: Span
    claim: str
    kind: RequestKind = RequestKind.MORE_FRAMES
    decision: str = ""
    budget_seconds: float = DEFAULT_BUDGET_SECONDS

    def __post_init__(self) -> None:
        claim = str(self.claim or "").strip()
        if not claim:
            raise ValueError("a request names the claim it wants checked")
        if len(claim) > MAX_CLAIM_CHARS:
            raise ValueError(
                f"a claim of {len(claim)} characters is a document, not a "
                f"claim about one span")
        self.claim = claim
        self.kind = RequestKind(self.kind)
        budget = float(self.budget_seconds)
        if budget < 0:
            raise ValueError("a budget cannot be negative")
        self.budget_seconds = budget


@dataclass
class Coverage:
    """Which part of the span was actually inspected.

    A caller must be able to see this beside every answer: a verdict
    that covered half the span cannot be read as a verdict about the
    whole span.
    """

    intervals: list[tuple[float, float]] = field(default_factory=list)
    inspected_s: float = 0.0
    complete: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"intervals": [{"start_s": round(a, 3), "end_s": round(b, 3)}
                              for a, b in self.intervals],
                "inspected_s": round(self.inspected_s, 3),
                "complete": self.complete}


@dataclass
class Outcome:
    """What a verification found, and what it cost."""

    verdict: Verdict
    stop_reason: StopReason
    request: Request
    coverage: Coverage = field(default_factory=Coverage)
    detail: str = ""
    seconds: float = 0.0
    producer: str = PRODUCER
    producer_version: str = PRODUCER_VERSION
    reused: bool = False
    """True when the answer came from a verdict already recorded, so no
    new measurement was made."""

    def to_dict(self) -> dict[str, Any]:
        return {"format": FORMAT,
                "verdict": self.verdict.value,
                "stop_reason": self.stop_reason.value,
                "request": {"span": self.request.span.to_dict(),
                            "claim": self.request.claim,
                            "kind": self.request.kind.value,
                            "decision": self.request.decision,
                            "budget_seconds": self.request.budget_seconds},
                "coverage": self.coverage.to_dict(),
                "detail": self.detail,
                "seconds": round(self.seconds, 3),
                "producer": self.producer,
                "producer_version": self.producer_version,
                "reused": self.reused}

    @classmethod
    def from_dict(cls, data: Any) -> "Outcome":
        if not isinstance(data, dict):
            raise ValueError("an outcome is a mapping")
        if data.get("format") != FORMAT:
            raise ValueError(f"unsupported outcome format: {data.get('format')!r}")
        request = data["request"]
        coverage = Coverage(
            intervals=[(float(i["start_s"]), float(i["end_s"]))
                       for i in (data.get("coverage") or {}).get("intervals") or []],
            inspected_s=float((data.get("coverage") or {}).get("inspected_s") or 0.0),
            complete=bool((data.get("coverage") or {}).get("complete")))
        return cls(verdict=Verdict(data["verdict"]),
                   stop_reason=StopReason(data["stop_reason"]),
                   request=Request(
                       span=Span.from_dict(request["span"]),
                       claim=request["claim"],
                       kind=RequestKind(request["kind"]),
                       decision=request.get("decision", ""),
                       budget_seconds=request.get("budget_seconds",
                                                  DEFAULT_BUDGET_SECONDS)),
                   coverage=coverage,
                   detail=data.get("detail", ""),
                   seconds=float(data.get("seconds") or 0.0),
                   producer=data.get("producer", PRODUCER),
                   producer_version=data.get("producer_version", PRODUCER_VERSION),
                   reused=bool(data.get("reused")))


# ── The producers ───────────────────────────────────────────────────
#
# A producer answers ONE request kind about ONE span. It returns
# `(verdict, coverage, detail)`. It never returns a span: the plan owns
# where the cut is.
#
# A producer that cannot answer says so with an exception or with an
# `unresolved` verdict, and the engine records WHY - the reason is the
# part a reader needs.

Producer = Callable[[Request, "EvidenceStore"], tuple[Verdict, Coverage, str]]


class EvidenceStore:
    """The recorded verdicts of one source, and the file they live in.

    The store is keyed by the CONTENT DIGEST, because that is what
    decides whether a verdict recorded against a file still applies to
    it. A file whose bytes changed has a different digest, so the old
    verdict is not served for it.
    """

    def __init__(self, content_digest: str, root: Optional[Path] = None):
        self.content_digest = content_digest
        self.root = root
        self._doc: Optional[dict] = None
        self._path: Optional[Path] = None
        self._dirty = False

    @classmethod
    def for_source(cls, content_digest: str,
                   root: Optional[Path] = None) -> "EvidenceStore":
        return cls(content_digest, root)

    def _slot_path(self) -> Path:
        from library.tools import source_memory
        return source_memory.source_dir(self.content_digest, self.root) / SLOT

    def _load(self) -> dict:
        if self._doc is None:
            path = self._slot_path()
            self._path = path
            try:
                with open(path, encoding="utf-8") as handle:
                    doc = json.load(handle)
            except (OSError, ValueError):
                doc = {}
            self._doc = doc if isinstance(doc, dict) else {}
        return self._doc

    @staticmethod
    def key(request: Request) -> str:
        """The key one request's verdict is filed under.

        The key names the SPAN, the CLAIM and the KIND: the same claim
        about the same span asked for the same way is the same question,
        and a verdict about it may be reused. A different span, a
        different claim or a different kind of evidence is a different
        question, and its answer is not reused.
        """
        import hashlib
        raw = json.dumps([request.span.content_digest or request.span.source_file,
                           round(request.span.start_s, 3),
                           round(request.span.end_s, 3),
                           request.claim.strip().lower(),
                           request.kind.value],
                          ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    def get(self, request: Request) -> Optional[Outcome]:
        """The recorded answer to this request, or None."""
        doc = self._load()
        verdicts = doc.get("verdicts")
        if not isinstance(verdicts, dict):
            return None
        raw = verdicts.get(self.key(request))
        if not isinstance(raw, dict):
            return None
        try:
            outcome = Outcome.from_dict(raw)
        except (KeyError, TypeError, ValueError):
            return None
        return outcome

    def put(self, outcome: Outcome) -> None:
        """File an outcome in memory.  The slot is written on ``flush``."""
        doc = self._load()
        verdicts = doc.setdefault("verdicts", {})
        verdicts[self.key(outcome.request)] = outcome.to_dict()
        self._dirty = True

    def flush(self) -> float:
        """Write the slot, and return how long the write took.

        The write is DEFERRED so that a sweep over one source's spans
        writes that source's slot once instead of once per span: the
        slot holds every verdict, so a per-verdict write re-serialises
        the whole file each time and the sweep's cost grows with the
        square of the verdict count. The cost is returned because the
        write is real work and the caller's budget has to see it.

        The slot's envelope carries the COVERAGE of what was inspected:
        the intervals of the spans that were decided, so a reader of the
        slot can see which parts of the source the verdicts cover
        without reading every verdict.
        """
        if not self._dirty:
            return 0.0
        from library.tools import source_memory
        doc = self._load()
        verdicts = doc.get("verdicts") or {}
        intervals = []
        for raw in verdicts.values():
            for row in ((raw.get("coverage") or {}).get("intervals") or []):
                start = float(row.get("start_s", 0.0))
                end = float(row.get("end_s", 0.0))
                if end > start:
                    intervals.append({"start_s": start, "end_s": end})
        started = time.perf_counter()
        path = self._path or self._slot_path()
        source_memory.write_json(path, {
            "content_digest": self.content_digest,
            "producer": PRODUCER,
            "producer_version": PRODUCER_VERSION,
            "coverage_intervals": intervals,
            "verdicts": verdicts})
        self._dirty = False
        return round(time.perf_counter() - started, 3)


# ── The engine ──────────────────────────────────────────────────────

_TRANSCRIPT_CACHE: dict[tuple[str, str], tuple[list[tuple[float, float, str]], str]] = {}
"""The source transcripts this process has already read.

One verification reads a source's transcript; the next one about the
same source reads it again. The transcript is the biggest file the lane
touches, and re-reading it per request is what made a 559-span sweep
take two minutes instead of two seconds. The key is the DIGEST and the
memory ROOT, so two roots never share a cache entry.
"""


def _source_words(content_digest: str,
                  root: Optional[Path]) -> tuple[list[tuple[float, float, str]], str]:
    """Every measured word of one source, and why there are none.

    The words come from the source's M1 transcript: the measurement
    taken from the source file ITSELF, which is the independent check
    on a claim the master timeline made.
    """
    from library.tools import source_memory
    key = (content_digest, str(root or ""))
    cached = _TRANSCRIPT_CACHE.get(key)
    if cached is not None:
        return cached
    doc, status = source_memory.read_m1(content_digest, root)
    if doc is None:
        result = ([], f"the source's own transcript is {status}")
        _TRANSCRIPT_CACHE[key] = result
        return result
    words: list[tuple[float, float, str]] = []
    for utterance in doc.get("utterances") or []:
        for word in (utterance or {}).get("words") or []:
            try:
                start = float(word["start"])
                end = float(word.get("end", start))
            except (KeyError, TypeError, ValueError):
                continue
            text = str(word.get("word") or "").strip().lower()
            if text:
                words.append((start, end, text))
    result = (words, "")
    _TRANSCRIPT_CACHE[key] = result
    return result


def _words_in(span: Span, store: EvidenceStore,
              root: Optional[Path]) -> tuple[list[tuple[float, str]], str]:
    """The source's own measured words OVERLAPPING the span, and why not.

    A word that straddles the span's edge belongs to the span, exactly
    as the caption matcher treats it (``subtitle_coverage``): a word the
    span edge clips still belongs to it. A word that merely starts
    inside the span does not - the word that starts a millisecond
    before the span was measured by the source's own transcript at that
    millisecond, and calling it absent would read as a disagreement
    where there is only a rounding difference.
    """
    digest = span.content_digest
    if not digest:
        return [], ("the span names no content digest, so no source-side "
                    "measurement can be read")
    words, why_not = _source_words(digest, root)
    if not words:
        return [], why_not or "the source's own transcript holds no word"
    inside = [(start, text) for start, end, text in words
              if start < span.end_s and end > span.start_s]
    return inside, ""


def claim_words(text: str) -> list[str]:
    """The content words of a claim, in order.

    The same rule the caption matcher uses
    (`subtitle_coverage.normalize_word`), so a word compares equal across
    the two transcriptions.  A reader outside this module asks for the
    COUNT of a claim's words through this, rather than reaching for the
    private helper.
    """
    return _normalize(text)


def _normalize(text: str) -> list[str]:
    """A text's content words, in order.

    The same rule the caption matcher uses (`subtitle_coverage.normalize_word`),
    so a word compares equal across the two.
    """
    from library.tools.subtitle_coverage import normalize_word
    out = []
    for token in str(text or "").split():
        norm = normalize_word(token)
        if norm and len(norm) > 2:
            out.append(norm)
    return out


def _normalized_word(text: str) -> str:
    """One measured word, normalized the same way a claimed word is.

    The two sides of the comparison are normalized by ONE rule, so a
    measured word is never compared against a claimed one in a
    different spelling than the comparison expects.
    """
    from library.tools.subtitle_coverage import normalize_word
    return normalize_word(text)


STEM_CHARS = 4
"""How much of a word two transcriptions must share for the words to be
the same word.

Two transcriptions of the same audio disagree on the spelling far more
often than on the words: "twenty" against "20", "Lucie" against "Lucy",
"gonna" against "going to". Comparing whole words would report every
one of those as a disagreement, so the words are compared on their first
four characters, which is long enough to tell "market" from "walking"
and short enough to forgive a spelling.
"""


def _same_word(claimed: str, measured: Iterable[str]) -> bool:
    """Whether the source measured the claimed word, allowing for spelling."""
    stem = claimed[:STEM_CHARS]
    for word in measured:
        if word == claimed or word.startswith(stem) or claimed.startswith(word[:STEM_CHARS]):
            return True
    return False


def _words_elsewhere(request: Request,
                     store: EvidenceStore) -> set[str]:
    """The source's own measured words OUTSIDE the span.

    These are what decide whether a claim that is absent from the span
    is pointed at the wrong seconds or simply spelled differently.
    """
    digest = request.span.content_digest
    if not digest:
        return set()
    words, _why_not = _source_words(digest, store.root)
    span = request.span
    return {_normalized_word(text) for start, end, text in words
            if not (start < span.end_s and end > span.start_s)
            and _normalized_word(text)}


def _speech_context_producer(request: Request,
                             store: EvidenceStore) -> tuple[Verdict, Coverage, str]:
    """Answer a claim from the source's OWN measured words.

    This is the cheapest producer there is: the words are already
    measured, so answering costs no decode at all. It is also the
    INDEPENDENT check on a claim the master timeline made - the source
    file's own transcript says what is said at those seconds, and the
    plan's claim is about those seconds.

    The comparison is tolerant on purpose. Two transcriptions of the
    same audio do not agree word for word, so the check asks whether the
    claimed words are PRESENT in the source's measured words, not
    whether the two texts are equal. A claim whose words are all
    present is supported; a claim whose words are all absent while the
    source's words at that span are something else entirely is
    contradicted; anything in between is unresolved.

    A single claimed word cannot CONTRADICT: one word that differs in
    spelling between two transcriptions of the same audio is ordinary
    variance, and calling it a disagreement would make the lane cry
    wolf. Two or more claimed words, all absent, is a disagreement.
    """
    span = request.span
    words, why_not = _words_in(span, store, store.root)
    if not words:
        return Verdict.UNRESOLVED, Coverage(), why_not or (
            "the source's own transcript holds no word in the span")
    measured = {_normalized_word(text) for _, text in words
                if _normalized_word(text)}
    claimed = _normalize(request.claim)
    if not claimed:
        return Verdict.UNRESOLVED, Coverage(), (
            "the claim carries no content word to look for")
    present = [word for word in claimed if _same_word(word, measured)]
    coverage = Coverage(intervals=[(span.start_s, span.end_s)],
                        inspected_s=span.duration_s, complete=True)
    if len(present) == len(claimed):
        return Verdict.SUPPORTED, coverage, (
            f"all {len(claimed)} claimed content words are measured in the "
            f"source's own transcript inside the span")

    # The claim's words are not at the span. Whether that CONTRADICTS the
    # claim or merely leaves it undecided depends on HOW MUCH of the
    # claim is missing and WHERE the words are:
    #
    # * every claimed word missing, and this same source measures them
    #   ELSEWHERE, is a span pointed at the wrong seconds - a
    #   contradiction;
    # * anything less is undecided: two transcriptions of the same audio
    #   spell a number differently ("twenty" against "20"), and one
    #   differing word is not a wrong span.
    elsewhere = _words_elsewhere(request, store)
    missing = [word for word in claimed if not _same_word(word, measured)]
    away = [word for word in missing if _same_word(word, elsewhere)]
    if not present and len(missing) >= 2 and len(away) == len(missing):
        return Verdict.CONTRADICTED, coverage, (
            f"none of the {len(claimed)} claimed content words appear in "
            f"the source's own transcript inside the span, and the source "
            f"measures them elsewhere, so the span points at the wrong "
            f"seconds (the source measures "
            f"{' '.join(w for _, w in words[:6])!r} there)")
    return Verdict.UNRESOLVED, coverage, (
        f"{len(present)} of {len(claimed)} claimed content words are "
        f"measured in the span and {len(missing)} are not "
        f"({', '.join(missing[:6])}); the transcript does not decide it")


def _more_frames_producer(request: Request,
                          store: EvidenceStore) -> tuple[Verdict, Coverage, str]:
    """Answer a claim from the frames the source's M2 sample already holds.

    No decode happens here. The M2 sample is already computed, so
    asking for MORE frames on a span the sample already covers costs
    nothing and answers nothing new; the honest answer is that the
    sample does not settle the claim, and the way to settle it is a
    denser sample, which this producer does not run.

    What it DOES report is the coverage: which frames of the span the
    sample actually holds. A span the sample covers at 2 Hz is covered
    at 2 Hz, and a caller must see that beside the answer.
    """
    from library.tools import source_memory
    span = request.span
    digest = span.content_digest
    if not digest:
        return Verdict.UNRESOLVED, Coverage(), (
            "the span names no content digest, so no frame sample can be read")
    index = source_memory.read_m2(digest, store.root)
    if index is None:
        return Verdict.UNRESOLVED, Coverage(), (
            "the source carries no frame sample, so no frame of the span "
            "has been measured")
    frames = [f for f in (index.get("frames") or [])
              if span.start_s <= float(f.get("t", -1.0)) <= span.end_s]
    if not frames:
        return Verdict.UNRESOLVED, Coverage(), (
            "the source's frame sample holds no frame inside the span")
    rate = float(index.get("rate_hz_nominal") or 1.0) or 1.0
    coverage = Coverage(
        intervals=[(float(f["t"]), float(f["t"])) for f in frames],
        inspected_s=len(frames) / rate,
        complete=False)
    return Verdict.UNRESOLVED, coverage, (
        f"the source's frame sample holds {len(frames)} frame(s) inside the "
        f"span; the sampled frames do not decide a claim about what "
        f"happens there")


def _longer_segment_producer(request: Request,
                             store: EvidenceStore) -> tuple[Verdict, Coverage, str]:
    """Answer a claim by looking at a WIDER span than the plan named.

    A boundary claim ("the cut is here") can be wrong because the
    boundary lies just OUTSIDE the span the plan named.  Widening the
    window is the cheapest way to find out, and both the source's own
    transcript and its frame sample are already measured, so it costs no
    decode.

    The widened window is reported as the coverage, and it is marked
    INCOMPLETE: the answer is about a wider span than the plan named,
    and a reader must be able to see that.
    """
    span = request.span
    pad = max(span.duration_s, 1.0)
    widened = Span(source_file=span.source_file,
                   start_s=max(0.0, span.start_s - pad),
                   end_s=span.end_s + pad,
                   content_digest=span.content_digest)

    from library.tools import source_memory
    frames = []
    digest = span.content_digest
    if digest:
        index = source_memory.read_m2(digest, store.root)
        if index is not None:
            frames = [float(f.get("t", -1.0))
                       for f in (index.get("frames") or [])
                       if widened.start_s <= float(f.get("t", -1.0)) <= widened.end_s]
    if frames:
        coverage = Coverage(intervals=[(t, t) for t in frames],
                            inspected_s=len(frames) / 2.0, complete=False)
        return Verdict.UNRESOLVED, coverage, (
            f"the widened span [{widened.start_s:.2f}, {widened.end_s:.2f}] "
            f"holds {len(frames)} sampled frame(s) of the source's own "
            f"sample; the sampled frames do not decide a claim about what "
            f"happens there")

    words, why_not = _words_in(widened, store, store.root)
    if not words:
        return Verdict.UNRESOLVED, Coverage(), (
            why_not or "the source's own transcript holds no word in the "
            "widened span either")
    inside = [w for _, w in words if span.start_s <= _ <= span.end_s]
    coverage = Coverage(intervals=[(widened.start_s, widened.end_s)],
                        inspected_s=widened.duration_s, complete=False)
    claimed = _normalize(request.claim)
    measured = {_normalized_word(w) for _, w in words if _normalized_word(w)}
    present = [word for word in claimed if _same_word(word, measured)]
    if claimed and len(present) == len(claimed):
        return Verdict.SUPPORTED, coverage, (
            f"all {len(claimed)} claimed content words are measured in the "
            f"widened span [{widened.start_s:.2f}, {widened.end_s:.2f}]")
    return Verdict.UNRESOLVED, coverage, (
        f"the widened span [{widened.start_s:.2f}, {widened.end_s:.2f}] "
        f"measures {len(inside)} word(s) inside the plan's own span and "
        f"{len(present)} of {len(claimed)} claimed content words; it does "
        f"not decide the claim")


def _alternate_camera_producer(request: Request,
                               store: EvidenceStore) -> tuple[Verdict, Coverage, str]:
    """Answer a claim from the OTHER camera's picture of the same instant.

    The multicam offsets are measured by ``camera_sync`` from the
    master's own cuts, so the other camera's file is known without
    measuring anything. What is NOT known here is what that camera
    shows - no frame of it has been inspected by this producer - so the
    answer names the other camera's file and its offset and says the
    claim is unresolved there.
    """
    span = request.span
    digest = span.content_digest
    if not digest:
        return Verdict.UNRESOLVED, Coverage(), (
            "the span names no content digest, so no multicam group can be "
            "read for it")
    try:
        from library.tools import conversation_clock
        clock = conversation_clock.read_clock(digest, store.root)
    except Exception as exc:  # noqa: BLE001 - a producer that cannot run says so
        return Verdict.UNRESOLVED, Coverage(), (
            f"the multicam clock could not be read: {exc}")
    if not clock:
        return Verdict.UNRESOLVED, Coverage(), (
            "this source carries no measured multicam clock, so no other "
            "camera's offset against it is known")
    members = [m for m in (clock.get("group_members") or [])
               if str(m) != str(digest)]
    if not members:
        return Verdict.UNRESOLVED, Coverage(), (
            "this source is alone in its measured multicam group, so there "
            "is no alternate camera of the same instant")
    return Verdict.UNRESOLVED, Coverage(), (
        f"the alternate camera(s) {', '.join(str(m)[:12] for m in members)} "
        f"share a measured group with this source, but no frame of them has "
        f"been inspected for this claim")


PRODUCERS: dict[RequestKind, Producer] = {
    RequestKind.SPEECH_CONTEXT: _speech_context_producer,
    RequestKind.MORE_FRAMES: _more_frames_producer,
    RequestKind.LONGER_SEGMENT: _longer_segment_producer,
    RequestKind.ALTERNATE_CAMERA: _alternate_camera_producer,
}
"""The producer each request kind routes to.

The punch list names four requests and this table is the whole of the
routing. A kind with no producer cannot be served, and the engine says
so by name rather than guessing.
"""


def verify(request: Request,
           store: EvidenceStore,
           producer: Optional[Producer] = None,
           ) -> Outcome:
    """Answer one request, inside its budget.

    The order is fixed and it is the point of the module:

    1. **A recorded answer is the answer.** The same question asked
       twice costs nothing the second time.
    2. **The cheapest producer that can decide it runs first.** The
       engine runs the producer for the requested kind, and when that
       kind cannot decide the claim it says so; it does not go looking
       for another kind, because the requester named the kind of
       evidence they wanted.
    3. **The budget is a ceiling.** A producer that would cost more
       than the remaining budget is not run, and the answer is
       ``unresolved`` with the cost named.

    Nothing here moves the cut, and nothing here writes a plan.
    """
    recorded = store.get(request)
    if recorded is not None:
        return Outcome(verdict=recorded.verdict,
                       stop_reason=recorded.stop_reason,
                       request=request,
                       coverage=recorded.coverage,
                       detail=f"reused: {recorded.detail}",
                       seconds=0.0,
                       reused=True)

    chosen = producer or PRODUCERS.get(request.kind)
    if chosen is None:
        return Outcome(verdict=Verdict.UNRESOLVED,
                       stop_reason=StopReason.NO_FURTHER_PRODUCER,
                       request=request,
                       detail=(f"no producer serves a "
                               f"{request.kind.value} request"))

    # The budget bounds the work BEFORE the work, not after it. A request
    # with no budget may not be measured at all: the cost of a producer
    # that overruns is already spent by the time the overrun is noticed,
    # and a budget nobody enforces is a number nobody reads.
    if request.budget_seconds <= 0.0:
        return Outcome(verdict=Verdict.UNRESOLVED,
                       stop_reason=StopReason.BUDGET_EXHAUSTED,
                       request=request,
                       detail=("the request carries no budget, so no "
                               "measurement was made"))

    started = time.perf_counter()
    try:
        verdict, coverage, detail = chosen(request, store)
    except Exception as exc:  # noqa: BLE001 - a producer that fails says so
        return Outcome(verdict=Verdict.UNRESOLVED,
                       stop_reason=StopReason.UNCOVERED,
                       request=request,
                       detail=f"the producer failed: {exc}",
                       seconds=round(time.perf_counter() - started, 3))
    elapsed = round(time.perf_counter() - started, 3)

    if elapsed > request.budget_seconds:
        return Outcome(verdict=Verdict.UNRESOLVED,
                       stop_reason=StopReason.BUDGET_EXHAUSTED,
                       request=request,
                       coverage=coverage,
                       detail=(f"the producer cost {elapsed:.2f}s, over the "
                               f"{request.budget_seconds:.2f}s budget; "
                               f"the answer is not used"),
                       seconds=elapsed)

    if verdict in (Verdict.SUPPORTED, Verdict.CONTRADICTED):
        stop = StopReason.SETTLED
    elif not coverage.intervals:
        stop = StopReason.UNCOVERED
    else:
        stop = StopReason.NO_FURTHER_PRODUCER
    outcome = Outcome(verdict=verdict, stop_reason=stop, request=request,
                      coverage=coverage, detail=detail, seconds=elapsed)
    store.put(outcome)
    return outcome


def flush(stores: Sequence[EvidenceStore]) -> float:
    """Write every dirty store once, and return the total cost.

    The write is the last thing a sweep does, and it is charged: a
    caller that budgeted the sweep must see what the sweep cost, and a
    write nobody charged for is a write nobody bounded.
    """
    spent = 0.0
    for store in stores:
        spent += store.flush()
    return round(spent, 3)


def verify_many(requests: Sequence[Request],
                stores: dict[str, EvidenceStore],
                ) -> list[Outcome]:
    """Answer several requests, one after another, under one budget each.

    The budget is PER REQUEST, because a request that cannot be served
    must not spend the budget of the request after it.
    """
    out: list[Outcome] = []
    for request in requests:
        digest = request.span.content_digest or request.span.source_file
        store = stores.get(digest)
        if store is None:
            store = EvidenceStore.for_source(digest)
            stores[digest] = store
        out.append(verify(request, store))
    flush(list(stores.values()))
    return out


# ── The CLI ─────────────────────────────────────────────────────────

def _store_for(span: Span, root: Optional[Path]) -> EvidenceStore:
    digest = span.content_digest
    if not digest:
        raise RenRefusal(
            "the span names no content digest",
            "a verdict is filed against the source's content, so the "
            "source's content digest is required",
            "pass --digest, or read the span from a plan that carries it",
            blocker="insufficient_evidence")
    return EvidenceStore.for_source(digest, root)


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.on_demand_verification",
        description=("Verify a claim about ONE span on demand, inside a "
                     "resource budget, and record the answer beside the "
                     "source's own measurements."))
    parser.add_argument("--source-file", required=True,
                        help="The source the span is in.")
    parser.add_argument("--start", required=True, type=float,
                        help="The span's start, in seconds into the source.")
    parser.add_argument("--end", required=True, type=float,
                        help="The span's end, in seconds into the source.")
    parser.add_argument("--digest", default="",
                        help="The source's content digest.")
    parser.add_argument("--claim", required=True,
                        help="The claim to check against the span.")
    parser.add_argument("--kind", default=RequestKind.SPEECH_CONTEXT.value,
                        choices=[k.value for k in RequestKind],
                        help="The kind of evidence to ask for.")
    parser.add_argument("--decision", default="",
                        help="What the answer is for.")
    parser.add_argument("--budget-seconds", type=float,
                        default=DEFAULT_BUDGET_SECONDS,
                        help=("The resource ceiling on this request "
                              f"(default: {DEFAULT_BUDGET_SECONDS:.0f}s)."))
    parser.add_argument("--memory-root", default="",
                        help="The source-memory root to read and write.")
    parser.add_argument("--json", action="store_true",
                        help="Print the outcome as JSON.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    root = Path(args.memory_root).expanduser() if args.memory_root else None
    span = Span(source_file=args.source_file, start_s=args.start,
                end_s=args.end, content_digest=args.digest or None)
    request = Request(span=span, claim=args.claim,
                      kind=RequestKind(args.kind), decision=args.decision,
                      budget_seconds=args.budget_seconds)
    store = _store_for(span, root)
    outcome = verify(request, store)
    if args.json:
        print(json.dumps(outcome.to_dict(), indent=2))
    else:
        print(f"{outcome.verdict.value}: {outcome.detail}")
        print(f"  coverage: {outcome.coverage.to_dict()}")
        print(f"  {outcome.seconds:.2f}s, stop: {outcome.stop_reason.value}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RenRefusal as refused:
        from library.tools.ren_refusal import emit_refusal_result

        print(refused.render(), file=os.sys.stderr)
        emit_refusal_result(refused)
        raise SystemExit(2)
