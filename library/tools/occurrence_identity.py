"""Occurrence identity: WHICH time a repeated event happened.

**What this is.** The canonical model's answer to "the second time the
person brings the device to their mouth". A repeated action is a SERIES:
one class (``hand_near_mouth``), one subject (the person), one source.
The series is numbered, and each event carries the number it holds.

**Why it is a number and not a name.** An event's own id names the
record the lane wrote; the OCCURRENCE names the position in the series
and is derived from the event's TIME ORDER inside it. A re-measure of
the same source rewrites the records, so it can rewrite their ids; it
does not move the second occurrence before the third. That is why the
index, and not the id, is what a receipt names.

**The scope is one source.** The series counts within ONE asset: the
same class, the same subject, the same source. A project that holds
three takes of one episode therefore numbers "the second time" three
times, once per take, and a query that does not say which take is
REFUSED rather than answered from whichever take happens to be first.

**Sibling numbering, not a replacement.** ``library.tools.temporal_events``
numbers the repeats of an action inside a CLIP, for the temporal index.
This module numbers them inside the canonical Project Model, across
the model's events, so the answer survives into the query layer and
the receipts. The two answer different questions and neither replaces
the other.

**What this is not.** Not a claim that an action happened exactly once,
and not a substitute for a frame-exact boundary: an occurrence names
WHICH event, not how many frames its edge is worth.
"""

from __future__ import annotations

from typing import Any

OCCURRENCE_FORMAT = "occurrence/1"
"""The format of an occurrence block and of its receipt."""

RECEIPT_FORMAT = "occurrence_receipt/1"
"""The format of the receipt a resolved occurrence hands back."""


class OccurrenceIdentityError(ValueError):
    """An occurrence cannot be named as asked."""


def event_class_of(event: dict[str, Any]) -> str:
    """The class an event counts within: the event's own ``kind``.

    The class is the model's own vocabulary, never a new one: a kind
    the model does not use names no series.
    """
    kind = str(event.get("kind") or "").strip()
    if not kind:
        raise OccurrenceIdentityError(
            "an event counts within a class, and it names none")
    return kind


def subject_of(event: dict[str, Any]) -> str | None:
    """The subject an event counts for: the participant it names.

    An event with several participants counts for the first of them by
    id, so the subject does not depend on the order the lane happened
    to list them in. An event that names no participant counts within
    its class alone, and says so with an absent subject rather than
    with a subject nobody measured.
    """
    participants = sorted(str(pid) for pid in (event.get("participant_ids") or [])
                          if isinstance(pid, str) and pid)
    return participants[0] if participants else None


def asset_of(model: dict[str, Any], event: dict[str, Any]) -> str | None:
    """The source an event belongs to, through its clock.

    A source that names no asset is reported as absent rather than
    guessed: an occurrence counted across sources would be a count of
    two different things.
    """
    clock_id = (event.get("interval") or {}).get("clock_id")
    for clock in model.get("clocks") or []:
        if not isinstance(clock, dict) or clock.get("id") != clock_id:
            continue
        domain = clock.get("domain") or {}
        asset_id = domain.get("asset_id") if isinstance(domain, dict) else None
        return str(asset_id) if isinstance(asset_id, str) and asset_id else None
    return None


def occurrence_of(event: dict[str, Any]) -> dict[str, Any] | None:
    """The occurrence block an event carries, when it carries one."""
    block = event.get("occurrence")
    return block if isinstance(block, dict) else None


def occurrence_key(model: dict[str, Any], event: dict[str, Any]) -> str:
    """The stable name of one occurrence.

    The key names the source, the class, the subject and the position,
    so two occurrences that mean the same thing across two revisions of
    the model share a key - which is what makes the index comparable
    across a re-measure.
    """
    block = _block_for(model, event)
    subject = block.get("subject_id")
    return "|".join([
        asset_of(model, event) or "-",
        str(block.get("class") or "-"),
        str(subject) if subject else "-",
        str(block.get("index")),
    ])


def _block_for(model: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    """The occurrence block of one event.

    A model that was never numbered still answers: the block is
    computed from the model's own events, which is the same assignment
    the migration writes, so a reader of an unnumbered model and a
    reader of a numbered one get the same answer.
    """
    block = occurrence_of(event)
    if block is not None:
        return block
    numbered = _numbered_blocks(model)
    computed = numbered.get(str(event.get("id") or ""))
    if computed is None:
        raise OccurrenceIdentityError(
            f"event {event.get('id')!r} names no class, so it counts "
            "within none")
    return computed


def _numbered_blocks(model: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The occurrence block of every event in the model, computed."""
    groups: dict[tuple[str | None, str, str | None],
                 list[dict[str, Any]]] = {}
    for event in model.get("events") or []:
        if not isinstance(event, dict):
            continue
        key = (asset_of(model, event), event_class_of(event),
               subject_of(event))
        groups.setdefault(key, []).append(event)
    blocks: dict[str, dict[str, Any]] = {}
    for key, group in groups.items():
        _asset_id, klass, subject = key
        for index, event in enumerate(sorted(group, key=_time_order), start=1):
            block: dict[str, Any] = {"class": klass, "index": index}
            if subject is not None:
                block["subject_id"] = subject
            blocks[str(event.get("id") or "")] = block
    return blocks


def assign_occurrences(model: dict[str, Any]) -> list[dict[str, Any]]:
    """Number every event among the repeats of its own class and subject.

    The events are ordered by their own time on their own clock, so the
    index is the order the events HAPPENED and not the order the lanes
    wrote them. Every event in the model is numbered, including the
    ones whose class appears once: the first occurrence of a class is
    the first occurrence, and saying so is cheaper for a reader than
    leaving the question open.

    The model's events are updated in place and returned.
    """
    events = [event for event in (model.get("events") or [])
              if isinstance(event, dict)]
    blocks = _numbered_blocks(model)
    for event in events:
        block = blocks.get(str(event.get("id") or ""))
        if block is not None:
            event["occurrence"] = dict(block)
    return events


def _time_order(event: dict[str, Any]) -> tuple[int, int, str]:
    """Events sort by when they happened, then by their id."""
    interval = event.get("interval") or {}
    start = interval.get("start")
    end = interval.get("end")
    return (int(start) if isinstance(start, int) else 0,
            int(end) if isinstance(end, int) else 0,
            str(event.get("id") or ""))


def events_of_class(model: dict[str, Any], event_class: str, *,
                    subject_id: str | None = None,
                    asset_id: str | None = None) -> list[dict[str, Any]]:
    """The model's events of one class, in the order they happened.

    The subject and the source narrow the series when they are named.
    """
    matches: list[dict[str, Any]] = []
    for event in model.get("events") or []:
        if not isinstance(event, dict):
            continue
        if event_class_of(event) != event_class:
            continue
        if subject_id is not None and subject_of(event) != subject_id:
            continue
        if asset_id is not None and asset_of(model, event) != asset_id:
            continue
        matches.append(event)
    return sorted(matches, key=_time_order)


def assets_of_class(model: dict[str, Any], event_class: str, *,
                    subject_id: str | None = None) -> list[str]:
    """The sources one class was measured in."""
    assets = {asset_of(model, event)
              for event in events_of_class(model, event_class,
                                           subject_id=subject_id)}
    return sorted(asset for asset in assets if asset)


def nth_event(model: dict[str, Any], event_class: str, *,
              subject_id: str | None = None,
              asset_id: str | None = None,
              n: int = 1) -> dict[str, Any] | None:
    """The n-th occurrence of a class for a subject, or ``None``.

    The n-th occurrence is the n-th event of that class, for that
    subject, in that source. When the class was measured in more than
    one source and the caller did not say which, this REFUSES: the
    second occurrence of an action is a different moment in each
    source, and picking one would be a guess.
    """
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        raise OccurrenceIdentityError("an occurrence index is 1-based")
    if asset_id is None:
        assets = assets_of_class(model, event_class, subject_id=subject_id)
        if len(assets) > 1:
            raise OccurrenceIdentityError(
                f"class {event_class!r} was measured in {len(assets)} "
                f"sources ({', '.join(assets)}): name the source, because "
                "the n-th occurrence is a different moment in each")
        if assets:
            asset_id = assets[0]
    events = events_of_class(model, event_class, subject_id=subject_id,
                             asset_id=asset_id)
    if len(events) < n:
        return None
    return events[n - 1]


def descriptor(model: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    """What a reader needs to name one occurrence: the short form."""
    block = _block_for(model, event)
    subject = block.get("subject_id")
    return {
        "event_id": str(event.get("id") or ""),
        "event_class": str(block.get("class") or event_class_of(event)),
        "subject_id": str(subject) if subject else None,
        "occurrence_index": block.get("index"),
        "occurrence_key": occurrence_key(model, event),
    }


def receipt(model: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    """The receipt of a resolved occurrence.

    It says WHICH event, WHICH occurrence of it, ON WHAT EVIDENCE and
    WHERE - the four things a later reader needs to check the answer
    without re-running the lane that produced it.
    """
    interval = event.get("interval") or {}
    ticks_per_second = _ticks_per_second(model, interval.get("clock_id"))
    start = interval.get("start")
    end = interval.get("end")
    out: dict[str, Any] = {
        "format": RECEIPT_FORMAT,
        **descriptor(model, event),
        "interval": {
            "clock_id": interval.get("clock_id"),
            "start": start,
            "end": end,
        },
        "evidence_ids": [str(eid) for eid in (event.get("evidence_ids") or [])],
    }
    if ticks_per_second and isinstance(start, int) and isinstance(end, int):
        out["interval"]["start_s"] = round(start / ticks_per_second, 6)
        out["interval"]["end_s"] = round(end / ticks_per_second, 6)
    return out


def resolve_target(model: dict[str, Any],
                  target: dict[str, Any]) -> dict[str, Any]:
    """Resolve a typed target that names ONE occurrence.

    The target names the class, the subject and the nth occurrence. The
    answer is the RECEIPT of what was resolved - one event id, one
    occurrence, one interval, and the evidence behind it - or a
    refusal that says why there is no such occurrence.

    A target that names no class, or an n-th that is not 1-based, is
    refused here rather than answered with a guess.
    """
    if not isinstance(target, dict):
        raise OccurrenceIdentityError("a target is a mapping")
    event_class = target.get("event_class")
    if not isinstance(event_class, str) or not event_class.strip():
        raise OccurrenceIdentityError("a target names its event_class")
    n = target.get("nth", target.get("occurrence_index"))
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise OccurrenceIdentityError("a target names a 1-based nth")
    subject_id = target.get("subject_id")
    if subject_id is not None and not isinstance(subject_id, str):
        raise OccurrenceIdentityError("subject_id must be a string")
    asset_id = target.get("asset_id")
    if asset_id is not None and not isinstance(asset_id, str):
        raise OccurrenceIdentityError("asset_id must be a string")
    try:
        event = nth_event(model, event_class.strip(), subject_id=subject_id,
                          asset_id=asset_id, n=n)
    except OccurrenceIdentityError as exc:
        return {"status": "refused", "reason": str(exc), "receipt": None}
    if event is None:
        known = len(events_of_class(model, event_class.strip(),
                                    subject_id=subject_id,
                                    asset_id=asset_id))
        return {"status": "not_found",
                "reason": (f"the model carries {known} occurrence(s) of "
                           f"{event_class.strip()!r}: there is no "
                           f"occurrence {n}"),
                "receipt": None}
    return {"status": "resolved", "reason": None,
            "receipt": receipt(model, event)}


def _ticks_per_second(model: dict[str, Any], clock_id: Any) -> int | None:
    for clock in model.get("clocks") or []:
        if isinstance(clock, dict) and clock.get("id") == clock_id:
            rate = clock.get("ticks_per_second")
            if isinstance(rate, int) and rate > 0:
                return rate
    return None
