"""INFRA-001: the portable multimodal evidence model - ONE record shape.

Ren stores evidence in many shapes. A Project Model observation carries
its evidence contract inside its payload; an event carries a certainty
and a list of the observations it rests on; a relation carries a
subject, a predicate and an object; a track carries samples; a mask
carries a representation. Each shape says something true, and a reader
who wants to know *where a claim came from* has to learn all of them.

This module is the ONE portable record every piece of evidence can be
lifted onto. It does not add a second vocabulary: the source reference
is the universal one (``library/tools/source_reference.py``, PR 3116),
the epistemic class is the one the claim producers declare
(``library/tools/claim_provenance.py``, PR 3137), and the coverage
kinds are the contract's (``library/tools/evidence_contract.py``). What
it adds is the part that had no home: a record says what KIND of thing
it is, which MODALITIES it is anchored into, and whether it is a LOSSY
description of the media rather than a reading of it.

**The record.** One shape, four kinds - ``interval`` (a span of a
source), ``entity`` (a thing that persists), ``event`` (something that
happened) and ``relationship`` (how two things stand to each other) -
and four modalities - ``video``, ``audio``, ``text`` and ``derived``.

**The three attributes every record carries.** A portable record names
the source it was read from, the epistemic class of the claim, and how
much of the source was actually covered. A record missing one of them
is not portable: it cannot be checked, and it cannot be carried to
another machine with its meaning intact.

**Immutable media, separately.** The portable document has two
sections. ``sources`` is the immutable original media - the asset, its
streams, the clock that counts its elements - keyed by the content
digest, so the same bytes have the same name on every machine. ``records``
is the derived measurement, and every record names the source it came
from. A record never names another record as its source: grounding a
claim in a description of a frame is grounding it in a caption.

**Recovering the exact original frames and audio samples.** A record
carries source references over the SOURCE's own clock, and the document
carries that clock's rate. A reference that cannot be placed on its
clock - a span longer than the source it claims to be in - is REFUSED
rather than emitted: a wrong reference is worse than a missing one,
because a wrong one is believed. A span that ends ONE FRAME past the
source's own length is the source's length, rounded by the duration it
was measured from, and the record says how far past it reached.

**A pre-contract index is an address until it lands.** The old
``source_frame`` spelling carries an index and no clock, so the index
may be an address in any space: a source frame, an ordinal in a
SAMPLED frame set, a number in a shot list. A record's own span says
where in the source it was measured, and an index that does not land
there is not a place in the source: it is DROPPED, by name, and the
span stands alone. The M3d region rows are exactly this case, and
upgrading their indices as they stand would recover the wrong frames.

**A standing between two things is a relationship.** A multicam offset
is not an interval of one source: it says how far one source's clock
sits from its group's reference. The record is a RELATIONSHIP, it
names its subject, its predicate and its object, and the object is the
other source, named by its content digest - the same name on every
machine, under any path.

**Semantic observations are explicitly lossy.** A transcription, a
scene description, a label: these describe the media, they are not the
media. A lossy record says so, and a lossy record may never be filed as
a measurement - that is the rule that keeps a model's description from
reading like an instrument's reading.

**Versioning and migration.** A record carries its version. A record
older than this reader is MIGRATED (the pre-contract reference
spellings become the modern ones; the contract nested in a payload is
lifted to the top level), and a record NEWER than this reader is
REFUSED, never half-read.

**The producers, one at a time.** A lane is MIGRATED when its records
can be lifted onto the portable shape without losing a value. The M1
transcript lane, the M3b identity lane, the M3d region lane and the M6
clock lane are migrated. The M3b lane writes observations AND the
identity links about them, and the links lift as RELATIONSHIP records:
a face track is this person's face, a voice turn is this speaker's
voice. A link is a description of the media rather than a reading of
it, so it is LOSSY, and it names the source it was read from rather
than the observation it rests on.

    python3 -m library.tools.portable_evidence census --model MODEL.json
    python3 -m library.tools.portable_evidence migrate --model MODEL.json \\
        --out portable.json [--reel reel-26] [--producer m1]
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from library.tools import (
    claim_provenance,
    epistemic_vocabulary,
    evidence_contract,
    source_reference,
)

SCHEMA = "ren.portable-evidence"
SCHEMA_VERSION = 1

#: The kinds of thing a portable record can be. The list IS the
#: contract: a kind that is not here has no reading.
RECORD_KINDS = ("interval", "entity", "event", "relationship")

#: The modalities a record can be anchored into. A record names the
#: media it was read from; ``derived`` is for a record computed over
#: other records rather than read off the media.
MODALITIES = ("video", "audio", "text", "derived")

#: The epistemic classes, the same vocabulary the claim producers
#: declare. ``unknown`` is the ABSENCE of a class: it is kept, never
#: filled in with a measurement.
PROVENANCE_CLASSES = ("measured", "inferred", "interpreted", "unknown")

#: The coverage kinds, the contract's own.
COVERAGE_KINDS = ("complete", "sampled", "inferred", "unavailable")

#: The stored observation modality -> the portable modality. A
#: transcription is a reading of WORDS, so it is a text record that
#: points at audio; a face box is a reading of the PICTURE; a
#: diarized voice turn is a reading of the SOUND and carries no words,
#: so it is an audio record.
MODALITY_OF_STORED = {
    "speech": "text",
    "speech_word": "text",
    "speech_pause": "text",
    "speech_rate": "text",
    "speech_completeness": "text",
    "speech_disfluency": "text",
    "speech_repetition": "text",
    "speech_take_link": "text",
    "voice_turn": "audio",
    "cross_modal_report": "text",
    "face": "video",
    "region_frame": "video",
    "region_track": "video",
    "image_measurement": "video",
    "scene": "video",
    "scene_boundary": "video",
    "cinematography_interval": "video",
    "sound_event": "audio",
    "source_metadata": "derived",
    "multicam_offset": "derived",
}

#: The modality a record was READ from, given the modality it is in.
#: A transcription is a text record that was read from the audio; a
#: face box is a video record read from the picture.
SOURCE_MODALITY = {
    "text": "audio",
    "video": "video",
    "audio": "audio",
}

#: The payload keys that carry a DESCRIPTION of the media rather than a
#: measurement of it. A record holding one of these is lossy: it
#: describes the picture or the sound, and it is not the picture.
DESCRIPTIVE_KEYS = (
    "text", "label", "location", "type", "lighting", "notable_features",
    "claim", "result", "description", "reason", "statement", "words",
)

#: The registry units whose value IS a description. A record whose value
#: is words or labels is a summary of the media, and it is lossy
#: whatever else it carries.
DESCRIPTIVE_UNITS = ("text", "labels", "regions")

#: The registry types whose claim is a STANDING between two sources
#: rather than an interval of one. A multicam offset says how far one
#: source's clock sits from its group's reference: it is a relation
#: between two assets, and the portable record says so.
RELATIONSHIP_TYPES = ("sync.multicam_offset",)

#: The key a relationship claim names its OTHER source by. The value is
#: the content digest of that source, so the second side is named by
#: its bytes and not by where this machine keeps them.
RELATION_OBJECT_KEY = "reference_digest"

#: The modality a lane's claim was READ from, when the record's own
#: modality cannot say. A multicam offset is computed over two
#: sources, but it was read from the AUDIO: the words it matched are
#: in the sound.
LANE_READ_MODALITY = {"m6": "audio"}

#: The producers this module has been given. One at a time, on purpose:
#: a producer is migrated when its records can be lifted onto the
#: portable shape without losing a value, and the census names the ones
#: that are still pending.
MIGRATED_PRODUCERS = ("m1", "m3b", "m3d", "m6", "m0", "reel-build")

#: The registry type a producer's records ARE, for a producer whose
#: stored rows carry no contract of their own.  The reel build wrote its
#: rows before the producers declared their class (PR 3137), so its
#: stored rows say nothing about themselves; what they ARE is the
#: registry's - transcribed utterances, the same type the M1 transcript
#: lane files its utterances under, so the two spell one thing once.
PRODUCER_MEASUREMENT_TYPE = {
    "reel-build": "speech.utterance",
}


class PortableEvidenceError(ValueError):
    """A record that is not a portable evidence record, or cannot be one.

    Carries the reason so a refusal names what was wrong rather than
    only that something was.
    """


# ── The record ─────────────────────────────────────────────────────


def build_record(*, id: str, kind: str, modality: str, asset_id: str,
                 stream_id: str, clock_id: str,
                 interval: Mapping[str, Any] | None = None,
                 source_references: Sequence[Mapping[str, Any]] = (),
                 modality_refs: Sequence[Mapping[str, Any]] = (),
                 provenance: Mapping[str, Any] | None = None,
                 coverage: Mapping[str, Any] | None = None,
                 lossy: bool = False,
                 semantic: Mapping[str, Any] | None = None,
                 value: Any = None,
                 entities: Sequence[str] = (),
                 relation: Mapping[str, Any] | None = None,
                 migrated_from: str | None = None) -> dict[str, Any]:
    """Build one portable evidence record.

    The record is validated before it is returned: a builder that can
    produce an invalid record is a builder that eventually does.
    """
    record: dict[str, Any] = {
        "evidence_model": SCHEMA,
        "version": SCHEMA_VERSION,
        "id": id,
        "kind": kind,
        "modality": modality,
        "asset_id": asset_id,
        "stream_id": stream_id,
        "clock_id": clock_id,
        "interval": dict(interval) if interval else None,
        "source_references": [dict(reference)
                              for reference in source_references],
        "modality_refs": [dict(ref) for ref in modality_refs],
        "provenance": dict(provenance) if provenance else None,
        "coverage": dict(coverage) if coverage else None,
        "lossy": bool(lossy),
        "semantic": dict(semantic) if semantic is not None else None,
        "value": value,
        "entities": list(entities),
        "relation": dict(relation) if relation else None,
        "migration": {"from_version": SCHEMA_VERSION if migrated_from is None
                      else 0, "migrated_from": migrated_from,
                      "span_end_overshoot_ticks": 0},
    }
    return parse_record(record)


def parse_record(record: Any) -> dict[str, Any]:
    """Check one portable record, or refuse it by name.

    The checks are the contract:

    * the version is not newer than this reader knows;
    * the kind and the modality are in the vocabularies;
    * the record names the immutable original media;
    * at least one source reference NAMES THE SOURCE - a record that
      points at nothing is a record about nothing;
    * the provenance names a class in the vocabulary, a producer and the
      registry type the claim was filed under;
    * the coverage names a kind, and an unavailable coverage carries
      neither a value nor samples;
    * a LOSSY record is never filed as a measurement.
    """
    if not isinstance(record, Mapping):
        raise PortableEvidenceError("a portable record is a mapping")
    version = record.get("version")
    if not isinstance(version, int):
        raise PortableEvidenceError("a portable record carries its version")
    if version > SCHEMA_VERSION:
        raise PortableEvidenceError(
            f"record stamped v{version}; this reader knows "
            f"v{SCHEMA_VERSION}")
    if record.get("evidence_model") not in (None, SCHEMA):
        raise PortableEvidenceError(
            f"record names model {record.get('evidence_model')!r}, "
            f"not {SCHEMA!r}")
    kind = record.get("kind")
    if kind not in RECORD_KINDS:
        raise PortableEvidenceError(
            f"record kind {kind!r} is not one of {RECORD_KINDS}")
    modality = record.get("modality")
    if modality not in MODALITIES:
        raise PortableEvidenceError(
            f"record modality {modality!r} is not one of {MODALITIES}")
    for key in ("id", "asset_id", "stream_id", "clock_id"):
        if not record.get(key):
            raise PortableEvidenceError(
                f"a portable record names its {key}")
    references = record.get("source_references")
    if not isinstance(references, list) or not references:
        raise PortableEvidenceError(
            "a portable record carries at least one source reference")
    names_source = False
    for reference in references:
        try:
            source_reference.parse(reference)
        except source_reference.SourceReferenceError as exc:
            raise PortableEvidenceError(
                f"source reference is not one: {exc}") from exc
        if source_reference.names_source(reference):
            names_source = True
    if not names_source:
        raise PortableEvidenceError(
            "a portable record carries a reference that NAMES THE SOURCE; "
            "a reference that names only the file is not a place in it")
    provenance = record.get("provenance")
    if not isinstance(provenance, Mapping):
        raise PortableEvidenceError("a portable record carries its provenance")
    klass = provenance.get("class")
    if klass not in PROVENANCE_CLASSES:
        raise PortableEvidenceError(
            f"provenance class {klass!r} is not one of {PROVENANCE_CLASSES}")
    if not provenance.get("producer"):
        raise PortableEvidenceError("provenance names its producer")
    if not provenance.get("measurement_type"):
        raise PortableEvidenceError(
            "provenance names the registry type its claim was filed under")
    coverage = record.get("coverage")
    if not isinstance(coverage, Mapping):
        raise PortableEvidenceError("a portable record carries its coverage")
    if coverage.get("kind") not in COVERAGE_KINDS:
        raise PortableEvidenceError(
            f"coverage kind {coverage.get('kind')!r} is not one of "
            f"{COVERAGE_KINDS}")
    samples = coverage.get("samples")
    if not isinstance(samples, int) or isinstance(samples, bool) or samples < 0:
        raise PortableEvidenceError("coverage carries a whole sample count")
    if coverage.get("kind") == "unavailable" and (samples or
                                                  record.get("value") is not None):
        raise PortableEvidenceError(
            "unavailable coverage carries no samples and no value")
    lossy = bool(record.get("lossy"))
    semantic = record.get("semantic")
    if semantic is not None and not lossy:
        raise PortableEvidenceError(
            "a record carrying a description is lossy, and says so")
    if lossy and klass == "measured":
        raise PortableEvidenceError(
            "a lossy description is not a measurement: a model said so, "
            "and no instrument read it")
    if kind in ("interval", "event"):
        interval = record.get("interval")
        if not isinstance(interval, Mapping) or not interval.get("clock_id"):
            raise PortableEvidenceError(
                f"a {kind} record carries the interval it happened over")
    if kind == "relationship":
        relation = record.get("relation")
        if (not isinstance(relation, Mapping)
                or not relation.get("subject")
                or not relation.get("predicate")
                or not relation.get("object")):
            raise PortableEvidenceError(
                "a relationship record names its subject, its predicate "
                "and its object")
    return dict(record)


# ── The model index ────────────────────────────────────────────────


class ModelIndex:
    """The parts of a Project Model a portable record is read against.

    The assets, the streams, the clocks and the provenance rows: enough
    to name the immutable original media, to place a span on its clock,
    and to say who produced a claim.
    """

    def __init__(self, model: Mapping[str, Any]) -> None:
        self.model = model
        self.assets = {row["id"]: row for row in model.get("assets") or []
                       if isinstance(row, Mapping) and row.get("id")}
        self.streams = {row["id"]: row for row in model.get("streams") or []
                        if isinstance(row, Mapping) and row.get("id")}
        self.clocks = {row["id"]: row for row in model.get("clocks") or []
                       if isinstance(row, Mapping) and row.get("id")}
        self.provenance = {row["id"]: row
                           for row in model.get("provenance") or []
                           if isinstance(row, Mapping) and row.get("id")}
        self._streams_by_clock: dict[str, list[dict[str, Any]]] = {}
        for stream in self.streams.values():
            clock_id = stream.get("clock_id")
            if clock_id:
                self._streams_by_clock.setdefault(clock_id, []).append(stream)
        self._words: dict[str, list[tuple[int, int, str]]] | None = None

    def clock(self, clock_id: str) -> dict[str, Any] | None:
        return self.clocks.get(clock_id)

    def stream_for(self, asset_id: str, clock_id: str) -> dict[str, Any] | None:
        """The stream of an asset that the given clock counts.

        A source asset carries a video stream and several audio
        streams; a clock names the stream it belongs to, and that is
        the one a reference into the source must use.
        """
        for stream in self._streams_by_clock.get(clock_id, []):
            if stream.get("asset_id") == asset_id:
                return stream
        for stream in self._streams_by_clock.get(clock_id, []):
            return stream
        for stream in self.streams.values():
            if stream.get("asset_id") == asset_id:
                return stream
        return None

    def streams_of_modality(self, asset_id: str, clock_id: str,
                            modality: str) -> list[dict[str, Any]]:
        """Every stream of one asset, on one clock, of one modality.

        A source carries one video stream and several audio channels;
        a reference into the source names ONE of them, and the count
        of the others is what tells a reader that the choice was not
        forced.
        """
        found = []
        for stream in self._streams_by_clock.get(clock_id, []):
            if stream.get("asset_id") != asset_id:
                continue
            if self.modality_of_stream(stream) != modality:
                continue
            found.append(stream)
        found.sort(key=lambda row: row.get("stream_index") or 0)
        return found

    def modality_of_stream(self, stream: Mapping[str, Any] | None
                           ) -> str:
        """The portable modality a stream belongs to."""
        if not stream:
            return "derived"
        codec_type = str((stream.get("metadata") or {}).get("codec_type")
                         or "")
        if codec_type == "video":
            return "video"
        if codec_type == "audio":
            return "audio"
        return "derived"

    def duration_ticks(self, asset_id: str, clock_id: str) -> int | None:
        """How many ticks of this clock the source itself holds.

        The asset's measured duration, on the clock's own rate, counted
        UP: a half-open span that ends on the source's last boundary
        is the source's own length, and the boundary past a measured
        duration is still the source's boundary. A span longer than
        this is not a span of the source, and a reference to it cannot
        be recovered.
        """
        asset = self.assets.get(asset_id) or {}
        duration = (asset.get("metadata") or {}).get("duration_seconds")
        clock = self.clock(clock_id) or {}
        rate = clock.get("ticks_per_second")
        if not isinstance(duration, (int, float)) or isinstance(duration, bool):
            return None
        if not isinstance(rate, int):
            return None
        if duration <= 0 or rate <= 0:
            return None
        return math.ceil(duration * rate)

    def frame_ticks(self, clock_id: str) -> int | None:
        """The length of one frame of the clock's own grid, in ticks.

        One frame is the unit a source's own length is measured in, so
        it is the tolerance a span that ends just past the source gets:
        a source whose duration was rounded to the millisecond ends a
        fraction of a frame past its measured length, and that is not a
        span on another grid.
        """
        clock = self.clock(clock_id) or {}
        rate = clock.get("ticks_per_second")
        frame_rate = clock.get("frame_rate") or {}
        numerator = frame_rate.get("numerator")
        denominator = frame_rate.get("denominator")
        if not isinstance(rate, int) or rate <= 0:
            return None
        if not isinstance(numerator, int) or not isinstance(denominator, int):
            return None
        if numerator <= 0 or denominator <= 0:
            return None
        return int(rate * denominator / numerator)

    def frames_covering(self, clock_id: str, start: int, end: int
                        ) -> tuple[int, int] | None:
        """The frames a span on this clock covers, or None.

        None when the clock has no frame grid: there are then no frames
        to cover, and a span on such a clock is not refused for that.
        """
        clock = self.clock(clock_id) or {}
        rate = clock.get("ticks_per_second")
        frame_rate = clock.get("frame_rate") or {}
        numerator = frame_rate.get("numerator")
        denominator = frame_rate.get("denominator")
        if not isinstance(rate, int) or rate <= 0:
            return None
        if not isinstance(numerator, int) or not isinstance(denominator, int):
            return None
        if numerator <= 0 or denominator <= 0:
            return None
        frames_per_second = numerator / denominator
        low = math.floor((start / rate) * frames_per_second)
        high = math.ceil((end / rate) * frames_per_second) - 1
        return low, max(low, high)

    def words_in(self, asset_id: str) -> list[tuple[int, int, str]]:
        """The source's own words, as its transcript measured them.

        A record that carries its words can be placed on its source by
        SEARCH: the source's transcript holds those words with their
        own timings.  This is the model's own index of them - it reads
        the stored word rows, never the media.
        """
        if self._words is None:
            self._words = self._index_words()
        return self._words.get(asset_id, [])

    def _index_words(self) -> dict[str, list[tuple[int, int, str]]]:
        """Index the stored word rows by the asset they were heard in."""
        words: dict[str, list[tuple[int, int, str]]] = {}
        for row in self.model.get("observations") or []:
            if not isinstance(row, Mapping):
                continue
            if str(row.get("modality")) != "speech_word":
                continue
            interval = row.get("interval")
            payload = row.get("payload")
            if not isinstance(interval, Mapping) or not isinstance(payload, Mapping):
                continue
            start, end = interval.get("start"), interval.get("end")
            if not isinstance(start, int) or not isinstance(end, int):
                continue
            if end <= start:
                continue
            for token in _words(payload.get("text")):
                words.setdefault(str(row.get("asset_id")), []).append(
                    (start, end, token))
        for rows in words.values():
            rows.sort()
        return words

    def provenance_row(self, provenance_id: str | None
                       ) -> dict[str, Any] | None:
        if not provenance_id:
            return None
        return self.provenance.get(provenance_id)


def lane_of(provenance_id: str | None) -> str:
    """The producer lane a provenance id names.

    ``prov-<digest>-m1`` is the M1 transcript lane; ``prov-reel-build``
    is the reel build. The lane is the unit a producer is migrated in.
    """
    if not isinstance(provenance_id, str) or not provenance_id:
        return "unknown"
    if provenance_id == "prov-reel-build":
        return "reel-build"
    tail = provenance_id.rsplit("-", 1)[-1]
    return tail or "unknown"


# ── Recovering a span the reel build re-based ─────────────────────


def _words(text: Any) -> list[str]:
    """A text's words, as the comparison sees them.

    Lower case, and without the punctuation a transcript carries: the
    source's own word rows end in full stops and the passage does not,
    and they are the same word.
    """
    return re.findall(r"[a-z0-9']+", str(text or "").lower())


def _align_words(passage: Sequence[str], source: Sequence[str],
                 lookahead: int = 3) -> tuple[int, int | None, int | None]:
    """The passage's words against the source's, in order.

    The source's own transcript may hold words the passage does not (a
    filler between two of them), and the passage may hold words the
    source's transcript does not (a different pass over the same
    recording); the matcher steps over a few of either, exactly as the
    speech sequence's own aligner does, and reports how many of the
    passage's words it could place.

    Returns ``(aligned, first, last)``: how many of the passage's words
    were placed, and the positions of the first and the last.  Nothing
    here decides anything: the numbers are what a reader sees.
    """
    p = 0
    c = 0
    matched: list[int] = []
    while p < len(passage) and c < len(source):
        if source[c] == passage[p]:
            matched.append(c)
            p += 1
            c += 1
            continue
        # The source's transcript holds a word the passage does not.
        step = None
        for skip in range(1, lookahead + 1):
            if c + skip < len(source) and source[c + skip] == passage[p]:
                step = c + skip
                break
        if step is not None:
            matched.append(step)
            p += 1
            c = step + 1
            continue
        # The passage holds a word the source's transcript does not.
        ahead = None
        for skip in range(1, lookahead + 1):
            if p + skip < len(passage) and source[c] == passage[p + skip]:
                ahead = p + skip
                break
        if ahead is not None:
            p = ahead
            continue
        c += 1
    if not matched:
        return 0, None, None
    return len(matched), matched[0], matched[-1]


def _rebase_factors(start: int, end: int, ratio: int) -> list[int]:
    """The whole numbers of grid ratios the recorded span carries.

    The reel build used to re-base its own rows onto the clock upgrade
    once per build, and every re-basing multiplied the span by the
    ratio between the lane's own grid and the clock's.  The recorded
    span is therefore the span that was measured, times a whole number
    of ratios - and the number of whole ratios it carries is read off
    the span itself: a ratio that does not divide the span's ticks is
    not a re-basing.
    """
    factors: list[int] = []
    power = 1
    while True:
        power *= ratio
        if start % power or end % power:
            break
        factors.append(power)
    return factors


def _recover_span(observation: Mapping[str, Any], index: ModelIndex,
                  ) -> tuple[tuple[int, int] | None, dict[str, Any] | None]:
    """The span a reel build row measured, recovered from the row itself.

    The recorded span is the measured span times a whole number of grid
    ratios, so the recovery is ARITHMETIC on the row's own integers: the
    span it recovers IS the span the reel build measured, not an
    approximation of it.

    Which multiple is undone is decided by the SOURCE. A span that does
    not fit inside its source is not a span of that source, so the
    candidates are the multiples whose span does fit - and where more
    than one of them fits, the source's own words say which: a passage
    is anchored by SEARCH (AGENTS.md section 6), never by the time a
    hint claims, and the candidate the source's transcript supports most
    wins - most words aligned, then the SHORTEST span, then the
    smallest leading gap.

    Where the arithmetic leaves ONE candidate, the row's own measurement
    has determined the span and the words are a report on it, not a
    condition of it: the transcript's word timings are another pass
    over the same recording, and a row whose words that pass does not
    hold is still the row the reel build measured.
    """
    interval = observation.get("interval")
    if not isinstance(interval, Mapping):
        return None, None
    start, end = interval.get("start"), interval.get("end")
    clock_id = interval.get("clock_id")
    asset_id = observation.get("asset_id")
    if not isinstance(start, int) or not isinstance(end, int):
        return None, None
    clock = index.clock(str(clock_id or "")) or {}
    rate = clock.get("ticks_per_second")
    grid = source_reference.SOURCE_TICKS_PER_SECOND
    if not isinstance(rate, int) or rate % grid or rate <= grid:
        return None, None
    ratio = rate // grid
    limit = index.duration_ticks(str(asset_id or ""), str(clock_id or ""))
    tolerance = index.frame_ticks(str(clock_id or "")) or 0
    payload = observation.get("payload")
    payload = payload if isinstance(payload, Mapping) else {}
    passage = _words(payload.get("text"))
    inside_source = index.words_in(str(asset_id or ""))
    candidates: list[tuple[int, int, int, int, int, int]] = []
    for factor in _rebase_factors(start, end, ratio):
        span_start, span_end = start // factor, end // factor
        if span_start >= span_end:
            continue
        if limit is not None and span_end > limit + tolerance:
            continue
        held = [row for row in inside_source
                if row[0] < span_end and row[1] > span_start]
        aligned, first, _last = _align_words(passage, [row[2] for row in held])
        leading_gap = (held[first][0] - span_start) if first is not None else 0
        candidates.append((-aligned, span_end - span_start, leading_gap,
                           span_start, span_end, factor))
    if not candidates:
        return None, None
    candidates.sort()
    _aligned, _length, leading_gap, span_start, span_end, factor = \
        candidates[0]
    aligned = -_aligned
    if len(candidates) > 1 and aligned == 0:
        # Several spans fit inside the source and the source's own
        # transcript supports none of them: the row cannot be placed.
        return None, None
    note = {
        "span_recovered": {
            "anchored_by": ("search: the source's own transcript"
                            if len(candidates) > 1
                            else "the row's own measurement: the re-basing "
                                 "undone"),
            "candidates": len(candidates),
            "rebased_by": factor,
            "grid_ratio": ratio,
            "words_aligned": aligned,
            "words": len(passage),
            "leading_gap_ticks": leading_gap,
        },
    }
    return (span_start, span_end), note


# ── Lifting a stored record onto the portable shape ────────────────


def _migrate_references(references: Any, clock_id: str
                         ) -> tuple[list[dict[str, Any]], list[str]]:
    """The source references of a stored record, in the modern spelling.

    The pre-contract spellings are READ and never written, so a stored
    ``source_frame`` becomes the modern ``element`` here. A reference
    that cannot be upgraded is dropped and named: it is not silently
    carried forward as if it were a modern reference.
    """
    modern: list[dict[str, Any]] = []
    dropped: list[str] = []
    if not isinstance(references, Sequence) or isinstance(references, (str, bytes)):
        return modern, dropped
    for reference in references:
        if not isinstance(reference, Mapping):
            dropped.append("not a mapping")
            continue
        kind = reference.get("kind")
        if kind in source_reference.LEGACY_REFERENCE_KINDS:
            upgraded = source_reference.upgrade_legacy(reference, clock_id)
            if upgraded is None:
                dropped.append(f"legacy reference {kind!r} has no modern "
                               f"spelling without a clock")
                continue
            modern.append(upgraded)
            continue
        try:
            modern.append(source_reference.parse(reference))
        except source_reference.SourceReferenceError as exc:
            dropped.append(str(exc))
    return modern, dropped


def _misaddressed(reference: Mapping[str, Any], start: int, end: int,
                  covered: tuple[int, int] | None) -> str | None:
    """Why a reference does not name a place inside the record's own span.

    The pre-contract spelling carries an index and no clock, so the
    index could be an address in ANY space: a source frame, an
    ordinal in a sampled frame set, a number in a shot list. The
    record's own span says where in the source it was measured, and an
    index that does not land there is not a place in the source at all.

    Such a reference is DROPPED and named. It is not carried forward
    as a modern element reference: a wrong reference is believed, and
    a believed wrong frame is worse than a missing one.
    """
    if reference.get("kind") != source_reference.ELEMENT:
        return None
    element = reference.get("element") or {}
    if element.get("kind") != source_reference.FRAME:
        return None
    index = element.get("frame_index")
    if not isinstance(index, int) or isinstance(index, bool):
        return None
    if covered is None:
        return None
    low, high = covered
    if low <= index <= high:
        return None
    return (f"the pre-contract frame index {index} is not a frame of the "
            f"span it was measured over (frames {low}..{high}): it names an "
            f"address in another space, not a place in this source")


def _names_a_place(reference: Mapping[str, Any], start: int, end: int,
                   covered: tuple[int, int] | None) -> bool:
    """Whether a reference names a place in the source it claims.

    A reference that reaches the source but lands outside the record's
    own span is an address in another space, and it does not name a
    place in this source.
    """
    if not source_reference.names_source(reference):
        return False
    return _misaddressed(reference, start, end, covered) is None


def _is_lossy(payload: Mapping[str, Any], unit: str | None) -> bool:
    """Whether a record DESCRIBES the media rather than measuring it.

    A transcription, a scene description, a label: the record is a
    summary of the media and not the media. A number is not lossy - it
    is a reading of the media, and the reading is exact enough to
    compare. A rate computed over a transcript is a reading; the words
    it was computed from are the description.
    """
    if unit in DESCRIPTIVE_UNITS:
        return True
    for key in DESCRIPTIVE_KEYS:
        if key in payload:
            return True
    return False


def _semantic_payload(payload: Mapping[str, Any], unit: str | None,
                      value: Any) -> dict[str, Any] | None:
    """The descriptive part of a record, or None when it has none.

    A description carried under a descriptive key is the description.
    A record whose registry unit IS a description keeps its value as
    the description: that value is the words or the labels.
    """
    described = {key: payload[key] for key in DESCRIPTIVE_KEYS
                 if key in payload}
    if described:
        return described
    if unit in DESCRIPTIVE_UNITS and isinstance(value, Mapping):
        return dict(value)
    return None


def _declared_class(measurement_type: str) -> str:
    """The epistemic class the PRODUCER declared for a registry type.

    The class is a consequence of the registry type (PR 3137): a type a
    model produced is an inference, whatever the stored record says.
    A type the registry does not know stays UNKNOWN - it is never
    defaulted to a measurement.
    """
    declared = claim_provenance.class_for_type(measurement_type)
    if declared in PROVENANCE_CLASSES:
        return declared
    return claim_provenance.UNKNOWN_CLASS


def _filed_class(contract: Mapping[str, Any]) -> str | None:
    """The class a stored record was FILED under, if it says one.

    Kept beside the declared class so the disagreement is visible: a
    record that filed a model's description as a measurement is a
    defect somebody has to see, and the portable record carries the
    evidence of it.
    """
    filed = contract.get("certainty")
    if isinstance(filed, str) and filed in PROVENANCE_CLASSES:
        return filed
    return None


def _relation_object(value: Any) -> str | None:
    """The other source a relationship claim stands between.

    The value names that source by its content digest, so the second
    side is named by its bytes: the same name on every machine, under
    any path.
    """
    if not isinstance(value, Mapping):
        return None
    digest = value.get(RELATION_OBJECT_KEY)
    if not isinstance(digest, str) or not digest:
        return None
    try:
        return source_reference.asset_id_for_digest(digest)
    except source_reference.SourceReferenceError:
        return None


def _declared_contract(observation: Mapping[str, Any], index: ModelIndex
                       ) -> dict[str, Any] | None:
    """The contract a producer's own rows declare, when they carry none.

    The reel build wrote its rows before the producers declared their
    class (PR 3137), so its stored rows say nothing about themselves.
    What they ARE is the registry's: transcribed utterances, the same
    type the M1 transcript lane files.  The contract is built here from
    that declaration and from the producer's own provenance row - never
    from the stored row, which says nothing, and never from the class
    the row happens to have been filed under.
    """
    lane = lane_of(observation.get("provenance_id"))
    measurement_type = PRODUCER_MEASUREMENT_TYPE.get(lane)
    if measurement_type is None:
        return None
    try:
        unit = evidence_contract.registry_entry(measurement_type)["unit"]
    except evidence_contract.EvidenceContractError:
        return None
    payload = observation.get("payload")
    payload = payload if isinstance(payload, Mapping) else {}
    provenance_row = index.provenance_row(observation.get("provenance_id")) or {}
    value = {key: payload[key] for key in ("text", "speaker_entity_id")
             if key in payload}
    return {
        "evidence_type": "inference",
        "measurement_type": measurement_type,
        "value": value or None,
        "unit": unit,
        "method": provenance_row.get("producer"),
        "producer": provenance_row.get("producer"),
        "producer_version": provenance_row.get("producer_version"),
        "coverage": "complete",
        "coverage_samples": 1,
        "coverage_scope": None,
        "coverage_status": None,
        "freshness": None,
        "references": [],
    }


def portable_record_from_observation(observation: Mapping[str, Any],
                                     index: ModelIndex,
                                     entity_ids: Sequence[str] = (),
                                     ) -> tuple[dict[str, Any] | None, str | None]:
    """Lift one stored observation onto the portable record shape.

    Returns ``(record, None)`` or ``(None, reason)``. A record that
    cannot be lifted is REFUSED, never approximated: a portable record
    that points at the wrong place in the source is worse than one that
    is absent, because the wrong one is believed.
    """
    contract = epistemic_vocabulary.contract_of(observation)
    if not isinstance(contract, Mapping):
        contract = _declared_contract(observation, index)
    if not isinstance(contract, Mapping):
        return None, "the record carries no evidence contract"
    measurement_type = contract.get("measurement_type")
    if not isinstance(measurement_type, str) or not measurement_type:
        return None, "the contract names no registry type"
    asset_id = observation.get("asset_id")
    interval = observation.get("interval")
    if not isinstance(asset_id, str) or not asset_id:
        return None, "the record names no asset"
    if not isinstance(interval, Mapping):
        return None, "the record carries no interval"
    clock_id = interval.get("clock_id")
    if not isinstance(clock_id, str) or not clock_id:
        return None, "the record's interval names no clock"
    start, end = interval.get("start"), interval.get("end")
    if not isinstance(start, int) or not isinstance(end, int):
        return None, "the record's interval is not counted in whole ticks"
    if end <= start:
        return None, "the record's interval is empty"
    stream = index.stream_for(asset_id, clock_id)
    if stream is None:
        return None, "the record names an asset with no stream on its clock"
    # The span has to FIT the source it claims to be in. A span longer
    # than the source is a span on another grid, and a reference to it
    # would recover the wrong frames. A span that ends ONE FRAME past
    # the source's own length is the source's length, rounded by the
    # duration it was measured from: that is not another grid. And a
    # span the reel build re-based onto the clock upgrade is recovered
    # from the row's own integers, and the source's own words say which
    # span it is.
    limit = index.duration_ticks(asset_id, clock_id)
    overshoot = 0
    recovery: dict[str, Any] | None = None
    if limit is not None and end > limit:
        tolerance = index.frame_ticks(clock_id)
        if tolerance is not None and end - limit <= tolerance:
            overshoot = end - limit
        else:
            recovered, recovery = _recover_span(observation, index)
            if recovered is None:
                return None, (f"the record's interval ends at tick {end}, past "
                              f"the source's own {limit} ticks on this clock: "
                              f"the span is not a span of this source")
            start, end = recovered
    modality = MODALITY_OF_STORED.get(str(observation.get("modality")),
                                      "derived")
    lane = lane_of(observation.get("provenance_id"))
    source_modality = SOURCE_MODALITY.get(modality)
    if source_modality is None:
        # A DERIVED record was read from ONE of the sources it was
        # computed over, and the lane says which: a multicam offset
        # was read from the audio, because the words it matched are in
        # the sound.
        source_modality = LANE_READ_MODALITY.get(lane)
    if source_modality is None:
        source_modality = index.modality_of_stream(stream)
    # The reference names the stream of the modality the record was
    # READ from: a transcription is a reading of the audio, and a face
    # box is a reading of the picture. A source with several streams
    # of one modality (four audio channels, say) is a choice, and the
    # count of the alternatives travels with the record.
    modality_streams = index.streams_of_modality(asset_id, clock_id,
                                                 source_modality)
    if not modality_streams:
        modality_streams = [stream]
    reference_stream = modality_streams[0]
    stream_id = str(reference_stream.get("id"))
    covered = index.frames_covering(clock_id, start, end)
    references, dropped = _migrate_references(contract.get("references"),
                                              clock_id)
    kept: list[dict[str, Any]] = []
    for reference in references:
        reason = _misaddressed(reference, start, end, covered)
        if reason:
            dropped.append(reason)
            continue
        kept.append(reference)
    references = kept
    kind = "interval"
    relation = None
    entities: list[str] = []
    if measurement_type in RELATIONSHIP_TYPES:
        # A STANDING between two sources is not an interval of one: it
        # names a subject, a predicate and an object, and the object is
        # the other source, named by its content digest.
        other = _relation_object(contract.get("value"))
        if other is None:
            return None, ("the relationship names no other source: a "
                          "standing between two things needs the second one")
        kind = "relationship"
        relation = {"subject": asset_id, "predicate": measurement_type,
                    "object": other}
        entities = [asset_id, other]
        references.append(source_reference.asset(other))
    if not any(source_reference.names_source(reference)
               for reference in references):
        # Nothing the record carried reaches the source, so the span it
        # was measured over stands in as the reference: it is the one
        # place in the source the record is about.
        references.insert(0, source_reference.interval(stream_id, clock_id,
                                                       start, end))
    if not any(source_reference.names_source(reference)
               for reference in references):
        return None, "the record's references name no source"

    modality_refs = []
    for reference in references:
        reference_stream_id = reference.get("stream_id")
        if not reference_stream_id:
            # A reference to a whole asset names no stream, and so
            # anchors no modality.
            continue
        referenced = index.streams.get(reference_stream_id)
        anchor = {
            "modality": index.modality_of_stream(referenced)
            if referenced else source_modality,
            "reference": dict(reference),
        }
        alternatives = index.streams_of_modality(
            asset_id, clock_id, anchor["modality"])
        if len(alternatives) > 1:
            anchor["streams_of_this_modality"] = len(alternatives)
        modality_refs.append(anchor)

    payload = observation.get("payload")
    payload = payload if isinstance(payload, Mapping) else {}
    unit = None
    try:
        unit = evidence_contract.registry_entry(measurement_type)["unit"]
    except evidence_contract.EvidenceContractError:
        unit = None
    value = contract.get("value")
    semantic = _semantic_payload(payload, unit, value)
    lossy = _is_lossy(payload, unit)
    provenance_row = index.provenance_row(observation.get("provenance_id"))
    provenance = {
        "class": _declared_class(measurement_type),
        "filed_class": _filed_class(contract),
        "producer": (provenance_row or {}).get("producer")
        or contract.get("producer") or "unknown",
        "producer_version": (provenance_row or {}).get("producer_version")
        or contract.get("producer_version"),
        "method": contract.get("method"),
        "measurement_type": measurement_type,
        "provenance_id": observation.get("provenance_id"),
    }
    coverage = {
        "kind": contract.get("coverage") or "unavailable",
        "samples": contract.get("coverage_samples") or 0,
        "scope": contract.get("coverage_scope"),
        "status": contract.get("coverage_status"),
        "freshness": contract.get("freshness"),
    }
    if coverage["kind"] not in COVERAGE_KINDS:
        coverage["kind"] = "unavailable"
    # The entities the record is ABOUT: the lane's own identity links
    # say who a face track and a voice turn belong to, and a record
    # that says nothing about WHO it measured is half a record.
    for entity in entity_ids:
        if entity not in entities:
            entities.append(entity)
    record = build_record(
        id=str(observation.get("id") or ""),
        kind=kind,
        modality=modality,
        asset_id=asset_id,
        stream_id=stream_id,
        clock_id=clock_id,
        interval={"clock_id": clock_id, "start": start, "end": end},
        source_references=references,
        modality_refs=modality_refs,
        provenance=provenance,
        coverage=coverage,
        lossy=lossy,
        semantic=semantic or None,
        value=value,
        entities=entities,
        relation=relation,
        migrated_from=str(observation.get("id") or ""),
    )
    record["migration"]["span_end_overshoot_ticks"] = overshoot
    if dropped:
        record["migration"]["dropped_references"] = dropped
    if recovery:
        record["migration"].update(recovery)
    return record, None


# ── The identity lane: relationships ──────────────────────────────


def identity_links(model: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The identity links the model holds.

    A link is a row, not an evidence contract: it says how two things
    stand to each other and nothing else. It is stored evidence, and it
    is the M3b lane's own.
    """
    return [dict(row) for row in model.get("identity_links") or []
            if isinstance(row, Mapping)]


def declared_type_for_lane(lane: str) -> str | None:
    """The registry type a lane's claims are filed under.

    Read from the lane's own declaration (``claim_provenance``), so
    the answer is the producer's, not this module's. A lane that
    declares SEVERAL types has no single answer and is refused rather
    than guessed at.
    """
    types = {declaration.get("measurement_type")
             for declaration in claim_provenance.CLAIM_DECLARATIONS.values()
             if declaration.get("lane") == lane}
    types.discard(None)
    if len(types) != 1:
        return None
    return str(types.pop())


def relationship_record_from_link(link: Mapping[str, Any],
                                   observation: Mapping[str, Any],
                                   index: ModelIndex,
                                   ) -> tuple[dict[str, Any] | None, str | None]:
    """Lift one identity link onto a portable RELATIONSHIP record.

    A link says how two things stand to each other: this face track is
    this person's face, this voice turn is this speaker's voice. The
    SUBJECT is the thing the media was read from - the track - and the
    OBJECT is the entity it stands to, the same way a standing between
    two sources names the other source as its object.

    It is a DESCRIPTION of the media rather than a reading of it, so
    the record is LOSSY, and the model refuses to file it as a
    measurement. The record names the SOURCE the link was read from -
    the frames or the samples it was measured over - and never the
    observation it rests on: grounding a claim in a description of a
    frame is grounding it in a caption.
    """
    entity_id = link.get("entity_id")
    method = link.get("method")
    if not isinstance(entity_id, str) or not entity_id:
        return None, "the link names no entity"
    if not isinstance(method, str) or not method:
        return None, "the link names no method"
    payload = observation.get("payload")
    payload = payload if isinstance(payload, Mapping) else {}
    track = payload.get("face_track") or payload.get("voice_track")
    if not isinstance(track, str) or not track:
        return None, "the link's observation names no track to be about"
    asset_id = observation.get("asset_id")
    interval = observation.get("interval")
    if not isinstance(asset_id, str) or not asset_id:
        return None, "the link's observation names no asset"
    if not isinstance(interval, Mapping):
        return None, "the link's observation carries no interval"
    clock_id = interval.get("clock_id")
    if not isinstance(clock_id, str) or not clock_id:
        return None, "the link's observation names no clock"
    start, end = interval.get("start"), interval.get("end")
    scope = link.get("scope")
    if isinstance(scope, Mapping) and scope.get("clock_id") == clock_id:
        start, end = scope.get("start", start), scope.get("end", end)
    if not isinstance(start, int) or not isinstance(end, int):
        return None, "the link's span is not counted in whole ticks"
    if end <= start:
        return None, "the link's span is empty"
    stream = index.stream_for(asset_id, clock_id)
    if stream is None:
        return None, "the link names an asset with no stream on its clock"
    limit = index.duration_ticks(asset_id, clock_id)
    if limit is not None and end > limit:
        return None, (f"the link's span ends at tick {end}, past the "
                      f"source's own {limit} ticks on this clock: the span "
                      f"is not a span of this source")
    modality = MODALITY_OF_STORED.get(str(observation.get("modality")),
                                      "derived")
    if modality == "derived":
        modality = index.modality_of_stream(stream)
    source_modality = SOURCE_MODALITY.get(modality) \
        or index.modality_of_stream(stream)
    modality_streams = index.streams_of_modality(asset_id, clock_id,
                                                 source_modality)
    if not modality_streams:
        modality_streams = [stream]
    reference_stream = modality_streams[0]
    stream_id = str(reference_stream.get("id"))
    reference = source_reference.interval(stream_id, clock_id, start, end)
    modality_refs = [{
        "modality": source_modality,
        "reference": dict(reference),
    }]
    if len(modality_streams) > 1:
        modality_refs[0]["streams_of_this_modality"] = len(modality_streams)
    lane = lane_of(link.get("provenance_id"))
    measurement_type = declared_type_for_lane(lane)
    if measurement_type is None:
        return None, (f"the lane {lane!r} declares no single registry "
                      f"type for its claims")
    provenance_row = index.provenance_row(link.get("provenance_id"))
    provenance = {
        "class": _declared_class(measurement_type),
        "filed_class": None,
        "producer": (provenance_row or {}).get("producer") or "unknown",
        "producer_version": (provenance_row or {}).get("producer_version"),
        "method": method,
        "measurement_type": measurement_type,
        "provenance_id": link.get("provenance_id"),
    }
    contract = epistemic_vocabulary.contract_of(observation) or {}
    coverage = {
        "kind": contract.get("coverage") or "unavailable",
        "samples": contract.get("coverage_samples") or 0,
        "scope": contract.get("coverage_scope"),
        "status": contract.get("coverage_status"),
        "freshness": contract.get("freshness"),
    }
    if coverage["kind"] not in COVERAGE_KINDS:
        coverage["kind"] = "unavailable"
    record = build_record(
        id=str(link.get("id") or ""),
        kind="relationship",
        modality=source_modality,
        asset_id=asset_id,
        stream_id=stream_id,
        clock_id=clock_id,
        interval={"clock_id": clock_id, "start": start, "end": end},
        source_references=[reference],
        modality_refs=modality_refs,
        provenance=provenance,
        coverage=coverage,
        lossy=True,
        semantic={"status": link.get("status"),
                  "confidence": link.get("confidence")},
        value=None,
        entities=[entity_id],
        relation={"subject": str(track), "predicate": method,
                  "object": entity_id},
        migrated_from=str(link.get("id") or ""),
    )
    return record, None


def migrate_lane(model: Mapping[str, Any],
                 observations: Sequence[Mapping[str, Any]],
                 producers: Sequence[str] = MIGRATED_PRODUCERS,
                 ) -> dict[str, Any]:
    """Lift one producer lane's evidence onto the portable shape.

    A lane's evidence is two collections: the observations it wrote,
    and the identity links it wrote about them. Both are lifted, and
    the observations name the entities the links say they are about -
    a record that says nothing about WHO it measured is half a record.

    Returns the portable records, the portable relationship records,
    the stored links they came from, and the refusals by name.
    """
    index = ModelIndex(model)
    producers = tuple(producers)
    scoped = [row for row in observations
              if lane_of(row.get("provenance_id")) in producers]
    by_id = {str(row.get("id")): row for row in scoped
             if row.get("id")}
    links = [link for link in identity_links(model)
             if lane_of(link.get("provenance_id")) in producers
             and str(link.get("observation_id")) in by_id]
    entity_ids: dict[str, set[str]] = {}
    for link in links:
        entity = link.get("entity_id")
        if isinstance(entity, str) and entity:
            entity_ids.setdefault(str(link["observation_id"]), set()).add(
                entity)
    records, refusals = migrate_observations(model, scoped, entity_ids)
    link_records: list[dict[str, Any]] = []
    link_refusals: list[dict[str, str]] = []
    for link in links:
        observation = by_id[str(link["observation_id"])]
        record, reason = relationship_record_from_link(link, observation,
                                                       index)
        if record is None:
            link_refusals.append({"id": str(link.get("id") or ""),
                                  "reason": reason or "refused"})
            continue
        link_records.append(record)
    return {
        "records": records,
        "link_records": link_records,
        "stored_links": links,
        "refusals": refusals,
        "link_refusals": link_refusals,
    }


# ── The census ─────────────────────────────────────────────────────


def _stored_rows(observations: Sequence[Mapping[str, Any]],
                 index: ModelIndex) -> list[dict[str, Any]]:
    """One row per stored observation, with the three attributes read.

    The attributes are read the way the portable model reads them, so
    the before and the after are measured with the same ruler. The
    class the record was FILED under is kept beside the class its
    producer DECLARED: the disagreement between them is the defect
    this model exists to end.
    """
    rows: list[dict[str, Any]] = []
    for observation in observations:
        contract = epistemic_vocabulary.contract_of(observation) or {}
        clock_id = str((observation.get("interval") or {}).get("clock_id") or "")
        interval = observation.get("interval") or {}
        start, end = interval.get("start"), interval.get("end")
        references, _dropped = _migrate_references(
            contract.get("references"), clock_id)
        has_reference = any(source_reference.names_source(reference)
                            for reference in references)
        covered = None
        if isinstance(start, int) and isinstance(end, int):
            covered = index.frames_covering(clock_id, start, end)
        names_a_place = any(_names_a_place(reference, start, end, covered)
                            for reference in references)
        legacy = any(
            isinstance(reference, Mapping)
            and reference.get("kind") in source_reference.LEGACY_REFERENCE_KINDS
            for reference in (contract.get("references") or [])
            if isinstance(contract.get("references"), Sequence))
        measurement_type = contract.get("measurement_type")
        declared = _declared_class(measurement_type) \
            if isinstance(measurement_type, str) else claim_provenance.UNKNOWN_CLASS
        filed = _filed_class(contract)
        coverage = contract.get("coverage")
        payload = observation.get("payload")
        payload = payload if isinstance(payload, Mapping) else {}
        unit = None
        if isinstance(measurement_type, str):
            try:
                unit = evidence_contract.registry_entry(measurement_type)["unit"]
            except evidence_contract.EvidenceContractError:
                unit = None
        rows.append({
            "shape": tuple(sorted(observation)),
            "payload_shape": tuple(sorted(payload)),
            "source_reference": has_reference,
            "legacy_reference": legacy,
            "names_a_place": names_a_place,
            "provenance_class": filed,
            "declared_class": declared,
            "filed_class": filed,
            "coverage": coverage if coverage in COVERAGE_KINDS else None,
            "lossy": _is_lossy(payload, unit),
            "producer": lane_of(observation.get("provenance_id")),
            "kind": "interval",
        })
    return rows


def _link_rows(links: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One row per stored identity link, with the three attributes read.

    A link is stored as a row of keys and nothing else: it names no
    source, declares no class and carries no coverage. The census says
    so, rather than assuming the attributes the portable record will
    give it.
    """
    rows: list[dict[str, Any]] = []
    for link in links:
        lane = lane_of(link.get("provenance_id"))
        measurement_type = declared_type_for_lane(lane)
        rows.append({
            "shape": tuple(sorted(link)),
            "payload_shape": (),
            "source_reference": False,
            "provenance_class": None,
            "declared_class": (_declared_class(measurement_type)
                               if measurement_type else
                               claim_provenance.UNKNOWN_CLASS),
            "filed_class": None,
            "coverage": None,
            "lossy": True,
            "producer": lane,
            "kind": "relationship",
        })
    return rows


def _portable_rows(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One row per portable record, with the three attributes read."""
    rows: list[dict[str, Any]] = []
    for record in records:
        provenance = record.get("provenance") or {}
        coverage = record.get("coverage") or {}
        declared = provenance.get("class")
        filed = provenance.get("filed_class")
        rows.append({
            "shape": tuple(sorted(record)),
            "payload_shape": (),
            "source_reference": any(
                source_reference.names_source(reference)
                for reference in record.get("source_references") or []),
            "legacy_reference": False,
            "names_a_place": any(
                source_reference.names_source(reference)
                for reference in record.get("source_references") or []),
            "provenance_class": declared
            if declared in PROVENANCE_CLASSES else None,
            "declared_class": declared,
            "filed_class": filed,
            "coverage": coverage.get("kind")
            if coverage.get("kind") in COVERAGE_KINDS else None,
            "lossy": bool(record.get("lossy")),
            "producer": lane_of((provenance or {}).get("provenance_id")),
            "kind": record.get("kind"),
        })
    return rows


def census_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Count the shapes and the three attributes of a set of rows.

    ``distinct_shapes`` is the number of record shapes the rows come
    in; ``with_all_three`` is the share of rows that carry a source
    reference, a provenance class AND a coverage - the three attributes
    a portable record must carry. ``class_defects`` counts the rows
    whose class disagrees with the class their producer declared: a
    model's description filed as a measurement.
    """
    total = len(rows)
    shapes: dict[str, int] = {}
    payload_shapes: dict[str, int] = {}
    record_shapes: set[tuple[str, str]] = set()
    by_producer: dict[str, dict[str, int]] = {}
    by_kind: dict[str, int] = {}
    with_reference = with_class = with_coverage = with_all = 0
    with_legacy = with_place = 0
    class_defects = 0
    filing_disagreements = 0
    lossy = 0
    for row in rows:
        shape = "|".join(row.get("shape") or ())
        shapes[shape] = shapes.get(shape, 0) + 1
        payload_shape = "|".join(row.get("payload_shape") or ())
        if payload_shape:
            payload_shapes[payload_shape] = payload_shapes.get(payload_shape, 0) + 1
        record_shapes.add((shape, payload_shape))
        producer = str(row.get("producer") or "unknown")
        bucket = by_producer.setdefault(producer, {
            "rows": 0, "with_source_reference": 0, "with_provenance_class": 0,
            "with_coverage": 0, "with_all_three": 0, "class_defects": 0,
            "with_legacy_reference": 0, "with_a_place_in_the_source": 0})
        bucket["rows"] += 1
        kind = str(row.get("kind") or "unknown")
        by_kind[kind] = by_kind.get(kind, 0) + 1
        reference = bool(row.get("source_reference"))
        klass = row.get("provenance_class")
        coverage = row.get("coverage")
        if reference:
            with_reference += 1
            bucket["with_source_reference"] += 1
        if row.get("legacy_reference"):
            with_legacy += 1
            bucket["with_legacy_reference"] += 1
        if row.get("names_a_place"):
            with_place += 1
            bucket["with_a_place_in_the_source"] += 1
        if klass:
            with_class += 1
            bucket["with_provenance_class"] += 1
        if coverage:
            with_coverage += 1
            bucket["with_coverage"] += 1
        if reference and klass and coverage:
            with_all += 1
            bucket["with_all_three"] += 1
        declared = row.get("declared_class")
        carried = row.get("provenance_class")
        if declared and declared != claim_provenance.UNKNOWN_CLASS \
                and carried and carried != declared:
            class_defects += 1
            bucket["class_defects"] += 1
        filed = row.get("filed_class")
        if declared and declared != claim_provenance.UNKNOWN_CLASS \
                and filed and filed != declared:
            filing_disagreements += 1
        if row.get("lossy"):
            lossy += 1
    return {
        "records": total,
        "distinct_shapes": len(shapes),
        "distinct_payload_shapes": len(payload_shapes),
        "distinct_record_shapes": len(record_shapes),
        "with_source_reference": with_reference,
        "with_legacy_reference": with_legacy,
        "with_a_place_in_the_source": with_place,
        "with_provenance_class": with_class,
        "with_coverage": with_coverage,
        "with_all_three": with_all,
        "share_with_all_three": (with_all / total) if total else 0.0,
        "class_defects": class_defects,
        "filing_disagreements": filing_disagreements,
        "lossy": lossy,
        "by_producer": by_producer,
        "by_kind": by_kind,
        "shapes": shapes,
    }


# ── The scope of a reel ────────────────────────────────────────────


def reel_assets(model: Mapping[str, Any], reel: str) -> list[str]:
    """The assets one reel draws its records from.

    A reel's clip uses name the assets it cut from; those assets' rows
    are the reel's records. A reel that names no clip use has no scope,
    and the census says so rather than counting the whole project.
    """
    assets: list[str] = []
    for use in model.get("clip_uses") or []:
        if not isinstance(use, Mapping):
            continue
        if reel not in str(use.get("id") or ""):
            continue
        asset_id = use.get("asset_id")
        if isinstance(asset_id, str) and asset_id and asset_id not in assets:
            assets.append(asset_id)
    return assets


def scope_observations(model: Mapping[str, Any], assets: Sequence[str]
                       ) -> list[dict[str, Any]]:
    """The observations of the given assets."""
    wanted = set(assets)
    return [row for row in model.get("observations") or []
            if isinstance(row, Mapping) and row.get("asset_id") in wanted]


def scope_clocks(model: Mapping[str, Any], assets: Sequence[str]
                 ) -> set[str]:
    """The clocks the given assets' records are counted on."""
    wanted = set(assets)
    index = ModelIndex(model)
    clocks: set[str] = set()
    for stream in index.streams.values():
        if stream.get("asset_id") in wanted and stream.get("clock_id"):
            clocks.add(str(stream["clock_id"]))
    return clocks


#: The collections that hold stored evidence besides the observations.
#: Each one is a shape of its own, and the census counts them so the
#: number of shapes in production is the whole number.
EVIDENCE_COLLECTIONS = ("events", "relations", "tracks", "masks",
                         "signals", "identity_links")


def other_evidence_shapes(model: Mapping[str, Any],
                           assets: Sequence[str]) -> dict[str, Any]:
    """The shapes of the stored evidence that is not an observation.

    A reel's events, relations, tracks, masks, signals and identity
    links are stored evidence too, and each collection has a shape of
    its own. The count is reported with the collection it belongs to,
    so the denominators are never combined.
    """
    clocks = scope_clocks(model, assets)
    wanted = set(assets)
    report: dict[str, Any] = {}
    for collection in EVIDENCE_COLLECTIONS:
        rows = [row for row in model.get(collection) or []
                if isinstance(row, Mapping)]
        if collection == "signals":
            rows = [row for row in rows if row.get("asset_id") in wanted]
        elif collection == "identity_links":
            # A link carries no clock: it is scoped by the SOURCE it
            # was measured in, which its provenance names by digest.
            # The reel build's own links name no digest and are
            # counted as they were.
            digests = {str((row or {}).get("content_digest") or "")
                       for row in model.get("assets") or []
                       if isinstance(row, Mapping)
                       and row.get("id") in wanted}
            rows = [row for row in rows
                    if str(row.get("provenance_id") or "")
                    == "prov-reel-build"
                    or any(digest and digest in str(row.get("provenance_id")
                                                   or "")
                           for digest in digests)]
        else:
            rows = [row for row in rows
                    if str((row.get("interval") or {}).get("clock_id")
                           or row.get("clock_id") or "") in clocks]
        shapes: dict[str, int] = {}
        for row in rows:
            shape = "|".join(sorted(row))
            shapes[shape] = shapes.get(shape, 0) + 1
        report[collection] = {"rows": len(rows),
                              "distinct_shapes": len(shapes),
                              "shapes": shapes}
    return report


# ── The portable document ──────────────────────────────────────────


def portable_sources(model: Mapping[str, Any], index: ModelIndex,
                     assets: Sequence[str]) -> list[dict[str, Any]]:
    """The immutable original media the records were read from.

    Keyed by the content digest, so the same bytes carry the same name
    on another machine. No path is written: the document is portable
    because it names the media, not where this machine keeps it.
    """
    wanted = set(assets)
    sources: list[dict[str, Any]] = []
    for asset_id in sorted(wanted):
        asset = index.assets.get(asset_id) or {}
        streams = []
        for stream in index.streams.values():
            if stream.get("asset_id") != asset_id:
                continue
            clock = index.clock(str(stream.get("clock_id") or "")) or {}
            streams.append({
                "stream_id": stream.get("id"),
                "modality": index.modality_of_stream(stream),
                "stream_index": stream.get("stream_index"),
                "clock_id": stream.get("clock_id"),
                "ticks_per_second": clock.get("ticks_per_second"),
                "frame_rate": clock.get("frame_rate"),
                "metadata": stream.get("metadata") or {},
            })
        streams.sort(key=lambda row: (row.get("modality") or "",
                                       row.get("stream_index") or 0))
        sources.append({
            "asset_id": asset_id,
            "content_digest": asset.get("content_digest"),
            "kind": asset.get("kind"),
            "duration_seconds": (asset.get("metadata") or {}).get(
                "duration_seconds"),
            "streams": streams,
        })
    return sources


def assets_named_by(records: Sequence[Mapping[str, Any]]) -> list[str]:
    """The assets the records name besides the ones they were read from.

    A relationship stands between two sources, and the document has to
    carry the second one: a record that names an asset the document
    does not describe is a record that cannot be checked.
    """
    named: list[str] = []
    for record in records:
        relation = record.get("relation") or {}
        for key in ("subject", "object"):
            value = relation.get(key)
            if isinstance(value, str) and value and value not in named:
                named.append(value)
    return named


def build_portable_document(model: Mapping[str, Any],
                             records: Sequence[Mapping[str, Any]],
                             index: ModelIndex,
                             assets: Sequence[str]) -> dict[str, Any]:
    """The portable document: the immutable media, and the records."""
    wanted = list(assets)
    for asset_id in assets_named_by(records):
        if asset_id not in wanted and asset_id in index.assets:
            wanted.append(asset_id)
    document = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "generated_from": {
            "model_id": model.get("id"),
            "schema_version": model.get("schema_version"),
        },
        "sources": portable_sources(model, index, wanted),
        "records": [dict(record) for record in records],
    }
    assert_portable(document)
    return document


def assert_portable(document: Any, where: str = "$") -> None:
    """Refuse a document that carries a path from this machine.

    A portable document names the media and the bytes, never where this
    machine keeps them: the content digest is the name, and it means
    the same thing everywhere.
    """
    if isinstance(document, Mapping):
        for key, value in document.items():
            assert_portable(value, f"{where}.{key}")
    elif isinstance(document, (list, tuple)):
        for index, value in enumerate(document):
            assert_portable(value, f"{where}[{index}]")
    elif isinstance(document, str):
        for marker in ("/Users/", "/home/", str(Path.home())):
            if marker and marker != "/" and marker in document:
                raise PortableEvidenceError(
                    f"{where} carries a local path ({marker!r}); a portable "
                    f"document names the media, not where this machine "
                    f"keeps it")


# ── The CLI ────────────────────────────────────────────────────────


def load_model(path: str | Path) -> dict[str, Any]:
    """Read a stored Project Model (a snapshot, a checkpoint, or a model)."""
    return epistemic_vocabulary.load_model(path)


def migrate_observations(model: Mapping[str, Any],
                         observations: Sequence[Mapping[str, Any]],
                         entity_ids: Mapping[str, Sequence[str]] | None = None,
                         ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Lift the given observations onto the portable shape.

    Returns the portable records and the refusals, one per record that
    could not be lifted. A refusal is named, never dropped: a record
    that cannot be portable is a defect somebody has to see.

    ``entity_ids`` names the entities each observation is about, keyed
    by the observation's id - the identity lane's links say who a face
    track and a voice turn belong to, and the record carries that with
    it.
    """
    index = ModelIndex(model)
    entity_ids = entity_ids or {}
    records: list[dict[str, Any]] = []
    refusals: list[dict[str, str]] = []
    for observation in observations:
        record, reason = portable_record_from_observation(
            observation, index,
            entity_ids=sorted(entity_ids.get(str(observation.get("id")), ())))
        if record is None:
            refusals.append({
                "id": str(observation.get("id") or ""),
                "reason": reason or "refused",
            })
            continue
        records.append(record)
    return records, refusals


def _census_report(model: Mapping[str, Any], reel: str | None,
                   producers: Sequence[str] | None) -> dict[str, Any]:
    assets = reel_assets(model, reel) if reel else []
    observations = scope_observations(model, assets) if reel else [
        row for row in model.get("observations") or []
        if isinstance(row, Mapping)]
    index = ModelIndex(model)
    stored = _stored_rows(observations, index)
    producers = tuple(producers) if producers else MIGRATED_PRODUCERS
    selected = [row for row in observations
                if lane_of(row.get("provenance_id")) in producers]
    stored_migrated = [row for row in stored
                       if row["producer"] in producers]
    lane = migrate_lane(model, observations, producers)
    before = census_rows(stored)
    before_migrated = census_rows(stored_migrated)
    before_links = census_rows(_link_rows(lane["stored_links"]))
    after = census_rows(_portable_rows(lane["records"]))
    after_links = census_rows(_portable_rows(lane["link_records"]))
    return {
        "reel": reel,
        "assets": assets,
        "migrated_producers": list(producers),
        "before": before,
        "before_migrated_producers": before_migrated,
        "before_migrated_links": before_links,
        "after": after,
        "after_links": after_links,
        "other_evidence": other_evidence_shapes(model, assets),
        "refusals": lane["refusals"],
        "link_refusals": lane["link_refusals"],
        "scope": {
            "observations_in_scope": len(observations),
            "observations_of_migrated_producers": len(selected),
            "identity_links_of_migrated_producers":
                len(lane["stored_links"]),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="portable_evidence",
        description="The portable multimodal evidence model: lift stored "
                    "evidence onto one record shape, and measure it.")
    sub = parser.add_subparsers(dest="command", required=True)

    census = sub.add_parser("census", help="measure the stored records")
    census.add_argument("--model", required=True,
                        help="a stored Project Model (snapshot or model)")
    census.add_argument("--reel", default=None,
                        help="the reel whose records to measure")
    census.add_argument("--producer", action="append", default=None,
                        help="the producer lane to migrate (repeatable)")

    migrate = sub.add_parser("migrate", help="write the portable document")
    migrate.add_argument("--model", required=True)
    migrate.add_argument("--out", required=True)
    migrate.add_argument("--reel", default=None)
    migrate.add_argument("--producer", action="append", default=None)

    args = parser.parse_args(argv)
    model = load_model(args.model)
    producers = tuple(args.producer) if args.producer else MIGRATED_PRODUCERS
    report = _census_report(model, args.reel, producers)
    if args.command == "census":
        print(json.dumps(report, indent=2, default=str))
        return 0
    assets = report["assets"]
    observations = scope_observations(model, assets) if args.reel else [
        row for row in model.get("observations") or []
        if isinstance(row, Mapping)]
    lane = migrate_lane(model, observations, producers)
    records = lane["records"] + lane["link_records"]
    refusals = lane["refusals"] + lane["link_refusals"]
    document = build_portable_document(model, records, ModelIndex(model),
                                       assets)
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, indent=1, default=str),
                      encoding="utf-8")
    print(json.dumps({
        "out": str(target),
        "records": len(records),
        "refusals": len(refusals),
        "census": report["after"],
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(main())
