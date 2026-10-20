"""Temporal questions a person actually asks about footage.

``library/tools/temporal_events.py`` says WHAT happened and WHEN.  This
module answers the questions that follow from that:

* "the second time she opens the box" - an ORDINAL REFERENCE, resolved
  to one event with its own interval (VI-014);
* "what happened after that", "how long did it take", "how many times
  did she do it" - ORDER, DURATION and REPETITION over the events
  (VI-015);
* "what caused it" - a CAUSAL-LIKE relationship, always marked
  ``inferred``, never presented as measured (VI-015).

The ordinal resolver is deterministic and abstains loudly.  It reports
WHICH part of the question it could not use - a pronoun it could not
turn into a person, an action the vocabulary does not name, an ordinal
with no such repetition - rather than resolving to the nearest event
that looks plausible.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from library.tools import temporal_events
from library.tools.temporal_events import (
    TemporalEvent,
    TemporalEventIndex,
    classify_action,
)

ORDINAL_WORDS: dict[str, int] = {
    "first": 1, "1st": 1,
    "second": 2, "2nd": 2,
    "third": 3, "3rd": 3,
    "fourth": 4, "4th": 4,
    "fifth": 5, "5th": 5,
    "sixth": 6, "6th": 6,
    "seventh": 7, "7th": 7,
    "eighth": 8, "8th": 8,
    "ninth": 9, "9th": 9,
    "tenth": 10, "10th": 10,
    "last": -1, "final": -1,
}
"""Ordinal words and the number they name.  ``-1`` means the LAST one."""

OCCURRENCE_MARKERS = ("time", "times", "instance", "instances",
                      "occurrence", "occurrences", "occasion", "occasions")
"""The nouns that make a reference an ORDINAL rather than a description."""

PRONOUNS = ("she", "he", "they", "it", "him", "her", "them", "we", "i")
"""Words that name a person without saying which one."""

STOPWORDS = frozenset({
    "a", "an", "the", "of", "in", "on", "at", "to", "for", "with", "and",
    "or", "but", "then", "when", "where", "while", "that", "this", "these",
    "those", "there", "here", "is", "was", "are", "were", "be", "been",
    "being", "do", "does", "did", "shows", "show",
    "showing", "seen", "see", "happens", "happen", "happened",
    "moment", "part", "bit", "clip", "shot", "footage", "video",
})
"""Words that carry no meaning for matching."""

INSTANCE_WORDS = frozenset({"time", "times", "instance", "instances",
                            "occurrence", "occurrences", "occasion",
                            "occasions"})


def _ordinal_word(number: int) -> str:
    """``2`` reads as "2nd", not "2th"."""
    if number < 0:
        return "last"
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


@dataclass(frozen=True)
class OrdinalReference:
    """A parsed reference like "the second time she opens the box"."""

    raw: str
    ordinal: int | None = None
    action: str | None = None
    action_basis: str = ""
    terms: tuple[str, ...] = ()
    subjects: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()
    """Parts of the reference this resolver could not use."""

    def to_dict(self) -> dict[str, Any]:
        return {"raw": self.raw, "ordinal": self.ordinal,
                "action": self.action, "action_basis": self.action_basis,
                "terms": list(self.terms), "subjects": list(self.subjects),
                "unresolved": list(self.unresolved)}


def parse_ordinal_reference(query: str) -> OrdinalReference:
    """Parse "the second time she opens the box" into its parts.

    Deterministic, and it says what it could not read: a pronoun it
    cannot turn into a name is recorded in ``unresolved``, not quietly
    dropped, because dropping it would make the answer look sharper
    than it is.
    """
    raw = (query or "").strip()
    lowered = raw.lower()
    words = lowered.replace(",", " ").replace(".", " ").split()

    ordinal: int | None = None
    remainder: list[str] = []
    index = 0
    while index < len(words):
        word = words[index].strip("'\"")
        if ordinal is None and word in ORDINAL_WORDS:
            ordinal = ORDINAL_WORDS[word]
            index += 1
            # "the second time she ..." - the marker word and the word
            # after it ("that", "she") belong to the ordinal phrase.
            if index < len(words) and words[index] in OCCURRENCE_MARKERS:
                index += 1
            if index < len(words) and words[index] in ("that", "when",
                                                       "which"):
                index += 1
            continue
        remainder.append(word)
        index += 1

    unresolved: list[str] = []
    subjects = tuple(word for word in remainder if word in PRONOUNS)
    for pronoun in subjects:
        unresolved.append(
            f"the pronoun {pronoun!r} names no person this resolver can "
            f"turn into an identity")

    content = [word.strip("'\"") for word in remainder]
    content = [word for word in content if word and word not in STOPWORDS]
    content = [word for word in content if word not in PRONOUNS]
    content = [word for word in content if word not in INSTANCE_WORDS]

    action, basis = classify_action(" ".join(content))
    if action is None:
        unresolved.append(
            "no action class in the vocabulary matches this reference")
    else:
        trigger_words = set()
        for trigger in temporal_events.ACTION_LEXICON.get(action, ()):
            trigger_words.update(trigger.split())
        content = [word for word in content if word not in trigger_words]

    terms = tuple(dict.fromkeys(word for word in content if word))
    if ordinal is None and not terms and action is None:
        unresolved.append("the reference names no ordinal, action or term")

    return OrdinalReference(raw=raw, ordinal=ordinal, action=action,
                             action_basis=basis, terms=terms,
                             subjects=subjects, unresolved=tuple(unresolved))


def searchable_text(event: TemporalEvent) -> str:
    """Everything about an event a reference may match on."""
    parts = [event.action_text,
             " ".join(event.subject_labels),
             " ".join(event.object_labels),
             " ".join(event.subjects),
             " ".join(event.objects),
             event.event_type.replace("_", " ")]
    if event.action:
        parts.append(event.action.replace("_", " "))
    return " ".join(part for part in parts if part).lower()


def events_matching(index: TemporalEventIndex,
                     reference: OrdinalReference) -> list[TemporalEvent]:
    """The events a reference could be about, before the ordinal is used.

    Matching is on the ACTION CLASS and on the reference's TERMS.  An
    event whose text does not contain the reference's terms is not a
    candidate, whatever its ordinal says: "the second time she opens
    THE BOX" is not about any other box.
    """
    out = []
    for event in index.events:
        if reference.action and event.action != reference.action:
            continue
        if not reference.terms:
            out.append(event)
            continue
        text = searchable_text(event)
        if all(term in text for term in reference.terms):
            out.append(event)
    return out


def resolve_ordinal_reference(index: TemporalEventIndex,
                              query: str) -> dict[str, Any]:
    """Resolve "the second time she opens the box" to ONE event.

    Returns a report that always says how the answer was reached: the
    reference as parsed, the groups considered, the event chosen, and
    what was left unresolved.  When the reference cannot be resolved
    the report says so and carries no event - it never falls back to
    the nearest plausible moment.
    """
    reference = parse_ordinal_reference(query)
    candidates = events_matching(index, reference)
    report: dict[str, Any] = {
        "query": query,
        "reference": reference.to_dict(),
        "candidates": len(candidates),
        "status": "unresolved",
        "event": None,
        "groups_considered": [],
        "unresolved": list(reference.unresolved),
        "reasons": [],
    }
    if reference.ordinal is None:
        report["reasons"].append(
            "the reference names no ordinal, so it names one event "
            "only by its description")
    if not candidates:
        report["reasons"].append(
            "no event matches this reference's action and terms")
        return report

    groups: dict[tuple[str | None, str, str], list[TemporalEvent]] = {}
    for event in candidates:
        key = (event.clip_id, event.event_type, event.action or "")
        groups.setdefault(key, []).append(event)

    qualified: list[tuple[tuple[str | None, str, str], list[TemporalEvent]]] = []
    for key, rows in sorted(groups.items(), key=lambda item: str(item[0])):
        ordered = sorted(rows, key=lambda event: event.start_seconds)
        report["groups_considered"].append({
            "clip_id": key[0], "event_type": key[1], "action": key[2] or None,
            "instances": len(ordered),
            "occurrences": [event.occurrence for event in ordered],
        })
        ordinal = reference.ordinal
        if ordinal is None:
            continue
        if ordinal > 0 and len(ordered) < ordinal:
            continue
        qualified.append((key, ordered))

    if not qualified:
        report["reasons"].append(
            f"no group holds {reference.ordinal} matching instances")
        return report

    if len(qualified) > 1:
        report["reasons"].append(
            "the reference matches more than one group; the reference "
            "does not say which")
        report["status"] = "ambiguous"
        return report

    key, ordered = qualified[0]
    ordinal = reference.ordinal
    chosen = ordered[-1] if ordinal == -1 else ordered[ordinal - 1]
    report["status"] = "resolved"
    report["event"] = chosen.to_dict()
    report["group"] = {"clip_id": key[0], "event_type": key[1],
                       "action": key[2] or None}
    report["resolution"] = (
        f"the {_ordinal_word(ordinal)} matching instance in the group"
        if ordinal != -1 else
        "the last matching instance in the group")
    return report


# ── Order, duration, repetition, causality (VI-015) ───────────────────


def order_events(events: Sequence[TemporalEvent]) -> list[dict[str, Any]]:
    """The events in the order they happened.

    Ordering is by the clip's own source order when the caller knows
    it, and by the clock inside a clip otherwise; ties break on the
    event id so the order is total and stable.
    """
    ordered = sorted(events, key=lambda event: (
        event.clip_id or "", event.start_seconds, event.event_id))
    out = []
    for position, event in enumerate(ordered, start=1):
        row = event.to_dict()
        row["order"] = position
        out.append(row)
    return out


def durations(events: Sequence[TemporalEvent]) -> list[dict[str, Any]]:
    """Each event's duration, with the precision of its own edges.

    A duration is only as good as its edges: the row carries the
    boundary uncertainties, so a reader can tell a measured length
    from a guessed one.
    """
    out = []
    for event in sorted(events, key=lambda row: (row.clip_id or "",
                                                 row.start_seconds,
                                                 row.event_id)):
        edges = [event.onset, event.completion]
        known = [edge.uncertainty_seconds for edge in edges
                 if edge.uncertainty_seconds is not None]
        out.append({
            "event_id": event.event_id,
            "event_type": event.event_type,
            "action": event.action,
            "clip_id": event.clip_id,
            "start_seconds": event.start_seconds,
            "end_seconds": event.end_seconds,
            "duration_seconds": round(event.duration_seconds, 6),
            "duration_uncertainty_seconds": (max(known) if known else None),
            "duration_basis": ("the event's own edges; the uncertainty is "
                               "the larger of the two edge uncertainties"),
        })
    return out


def repetition_groups(events: Sequence[TemporalEvent],
                      event_type: str | None = None,
                      action: str | None = None) -> list[dict[str, Any]]:
    """How many times each thing happened, and where.

    A group is one kind of event in one clip.  A group of one is not a
    repetition and is reported as such.
    """
    groups: dict[tuple[str | None, str, str], list[TemporalEvent]] = {}
    for event in events:
        if event_type and event.event_type != event_type:
            continue
        if action and event.action != action:
            continue
        key = (event.clip_id, event.event_type, event.action or "")
        groups.setdefault(key, []).append(event)
    out = []
    for key, rows in sorted(groups.items(), key=lambda item: str(item[0])):
        ordered = sorted(rows, key=lambda event: event.start_seconds)
        out.append({
            "group_id": ":".join(part for part in
                                 (key[0] or "-", key[1], key[2] or "-")
                                 if part),
            "clip_id": key[0],
            "event_type": key[1],
            "action": key[2] or None,
            "count": len(ordered),
            "is_repetition": len(ordered) > 1,
            "instances": [{"event_id": event.event_id,
                           "occurrence": event.occurrence,
                           "start_seconds": event.start_seconds,
                           "end_seconds": event.end_seconds}
                          for event in ordered],
        })
    return out


def precedes(left: TemporalEvent, right: TemporalEvent) -> bool:
    """Whether one event measurably happens before another."""
    return left.end_seconds <= right.start_seconds


def overlaps(left: TemporalEvent, right: TemporalEvent) -> bool:
    """Whether two events share any of their time."""
    return (left.start_seconds < right.end_seconds
            and right.start_seconds < left.end_seconds)


def temporal_relations(events: Sequence[TemporalEvent]) -> list[dict[str, Any]]:
    """The MEASURED relations between events: precedence and overlap.

    Every row here is a fact about the clock: one event's span ends
    before the other's begins, or they share time.  Nothing in this
    list is a CAUSE.
    """
    ordered = sorted(events, key=lambda event: (event.start_seconds,
                                                event.event_id))
    out = []
    for index, left in enumerate(ordered):
        for right in ordered[index + 1:]:
            if overlaps(left, right):
                relation = "overlaps"
            elif precedes(left, right):
                relation = "precedes"
            else:
                continue
            out.append({
                "relation": relation,
                "from_event_id": left.event_id,
                "to_event_id": right.event_id,
                "certainty": "measured",
                "basis": "the two events' intervals on their clock",
            })
    return out


def causal_candidates(events: Sequence[TemporalEvent]) -> list[dict[str, Any]]:
    """Pairs a reader might CALL causal - always marked ``inferred``.

    A candidate is an event that measurably precedes another and
    shares a participant or an object with it.  That is a REASON to
    look at the two together; it is not a cause.  Every row says
    ``certainty: inferred`` and names the measurement underneath, so
    nothing downstream can present it as measured.
    """
    ordered = sorted(events, key=lambda event: (event.start_seconds,
                                                event.event_id))
    out = []
    for index, left in enumerate(ordered):
        for right in ordered[index + 1:]:
            if not precedes(left, right):
                continue
            shared = (set(left.subjects) & set(right.subjects)) | (
                set(left.objects) & set(right.objects))
            if not shared:
                continue
            out.append({
                "relation": "may_cause",
                "from_event_id": left.event_id,
                "to_event_id": right.event_id,
                "shared": sorted(shared),
                "certainty": "inferred",
                "basis": "the first event measurably precedes the second and "
                         "they share a participant or an object; no cause "
                         "was measured",
            })
    return out


# ── The free-text surface ─────────────────────────────────────────────


def search_events(index: TemporalEventIndex, query: str,
                  max_priority: int | None = None) -> dict[str, Any]:
    """Find events by what happened, in their own words.

    The query is matched against the same text a reference is matched
    against, so "she opens the box" and "the second time she opens the
    box" reach the same events.
    """
    reference = parse_ordinal_reference(query)
    matches = events_matching(index, reference)
    if max_priority is not None:
        matches = [event for event in matches
                   if event.priority <= max_priority]
    matches.sort(key=lambda event: (event.priority, event.clip_id or "",
                                     event.start_seconds, event.event_id))
    return {
        "query": query,
        "reference": reference.to_dict(),
        "count": len(matches),
        "events": [event.to_dict() for event in matches],
        "unmapped_kinds": list(index.unmapped_kinds),
    }


def answer(index: TemporalEventIndex, query: str,
           max_priority: int | None = None) -> dict[str, Any]:
    """The whole answer to one temporal question.

    One report carries the ordinal resolution (when the question names
    an ordinal), the matching events, their durations, the measured
    relations between them, the causality-LIKE candidates (marked
    inferred) and the repetitions they belong to.  A caller does not
    have to know which of those a question needs.
    """
    resolved = resolve_ordinal_reference(index, query)
    found = search_events(index, query, max_priority)
    matched_ids = {row["event_id"] for row in found["events"]}
    if resolved.get("event"):
        matched_ids.add(resolved["event"]["event_id"])
    matched = [event for event in index.events if event.event_id in matched_ids]
    return {
        "mode": "temporal",
        "query": query,
        "event_count": len(index.events),
        "unmapped_kinds": list(index.unmapped_kinds),
        "resolution": resolved,
        "events": found["events"],
        "order": order_events(matched),
        "durations": durations(matched),
        "repetitions": repetition_groups(matched),
        "relations": temporal_relations(matched),
        "causal_candidates": causal_candidates(matched),
    }


def main(argv: list[str] | None = None) -> int:
    """Ask a project a temporal question."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Answer a temporal question over a project's typed events")
    parser.add_argument("project")
    parser.add_argument("query", nargs="?", default="",
                        help="e.g. 'the second time she opens the box'")
    parser.add_argument("--max-priority", type=int, default=None)
    args = parser.parse_args(argv)

    index = temporal_events.build_temporal_events(args.project)
    if args.query:
        report = answer(index, args.query, args.max_priority)
    else:
        matched = list(index.events)
        if args.max_priority is not None:
            matched = [event for event in matched
                       if event.priority <= args.max_priority]
        report = {"mode": "temporal", "project": str(args.project),
                  "event_count": len(index.events),
                  "unmapped_kinds": list(index.unmapped_kinds),
                  "events": [event.to_dict() for event in matched]}

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
