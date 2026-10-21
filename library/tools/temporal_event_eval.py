"""Ren's typed temporal events, written as the held-out benchmark's dataset.

The temporal-event benchmark (``library/tools/analysis/temporal_event_benchmark.py``)
scores PROPOSALS against ground-truth intervals a corpus custodian holds
outside every tuning workspace.  Its dataset format is the contract.  This
module writes that format from the typed events, so the events can be
scored the moment the labels are released - with no translation invented
at scoring time, and with no quality number produced from predictions the
custodian has not judged.

What this module CANNOT do, and says so
-----------------------------------------

The corpus's held-out labels are not in this repository.
``docs/benchmarks/2026-10-15-editorial-eval-corpus.json`` records
``held_out_access.status = "labels_not_created"``, and every source's
annotation is ``not_started``.  The benchmark therefore REFUSES to score
any dataset against the shipped corpus manifest, and
:func:`dataset_readiness` reports the same refusal in the open.

So the honest state is: the request is well-formed, the machinery is
ready, and the labels are the only thing missing.  A quality number
claimed before the custodian releases them would be scored against
Ren's own labels, which is exactly what the benchmark's split policy
forbids.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from library.tools.analysis.temporal_event_benchmark import EVENT_CLASSES
from library.tools.temporal_events import TemporalEvent, TemporalEventIndex

SCHEMA_VERSION = 1

BENCHMARK_CLASS_BY_ACTION: dict[str, str | None] = {
    # The benchmark's frozen classes, spelled the same way.
    "reach": "reach",
    "contact": "contact",
    "pick_up": "pick_up",
    "use": "use",
    "set_down": "set_down",
    "hand_raise": "hand_raise",
    "pointing": "pointing",
    "nodding": "nodding",
    "head_shake": "head_shake",
    "waving": "waving",
    "person_enters": "person_enters",
    "person_leaves": "person_leaves",
    "opening": "opening",
    "closing": "closing",
    # The classes the benchmark does not name, mapped onto the one it
    # does, with the reason spelled out here rather than left to the
    # scorer.
    "hand_to_mouth": "contact",
    "hand_to_face": "contact",
    "drink": "use",
    "position_change": None,
}
"""The action class to the benchmark's event class.

``None`` means the benchmark's vocabulary names no class for it, and the
event is REPORTED rather than scored against a class that means
something else.
"""

DECISION_YES = "yes"
DECISION_NOT_RUN = "not_run"
"""The benchmark's two decisions Ren's events can carry.

A VERIFIED event is a proposal the verifier confirmed.  A CANDIDATE is
not a negative answer: no verdict was recorded for it, and the
benchmark's own rule is that a missing verdict is ``not_run``, never
``no``.
"""


def benchmark_class(action: str | None) -> str | None:
    """The benchmark event class an action class scores as, or None."""
    if action is None:
        return None
    try:
        return BENCHMARK_CLASS_BY_ACTION[action]
    except KeyError:
        raise KeyError(
            f"no benchmark class for the action class {action!r}") from None


def _identity_id(event: TemporalEvent) -> str | None:
    """The identity the benchmark matches a proposal by.

    A tracked entity id is the identity when the event carries one; a
    subject label is the next best thing; an event with neither is
    reported as having no identity rather than given one.
    """
    if event.subjects:
        return event.subjects[0]
    if event.subject_labels:
        return event.subject_labels[0]
    return None


def _verdict(event: TemporalEvent) -> dict[str, Any]:
    """The verifier record a proposal carries.

    ``seconds`` is None because Ren's typed events record whether a
    verdict exists, not how long the verifier took; the benchmark's
    cost columns stay empty rather than carry a number Ren never
    measured.
    """
    if event.status == "verified":
        return {"decision": DECISION_YES, "seconds": None,
                "model_id": event.producer or None}
    return {"decision": DECISION_NOT_RUN, "seconds": None,
            "model_id": event.producer or None}


def build_dataset(index: TemporalEventIndex, *,
                  split: str = "calibration",
                  source_id_by_clip: Mapping[str, str] | None = None,
                  proposal_generation_seconds: float | None = None,
                  ) -> dict[str, Any]:
    """Ren's typed events as the benchmark's dataset document.

    ``split`` is the CALLER's declaration and defaults to
    ``calibration``: the benchmark refuses to score a calibration split
    as held-out quality, and that refusal is right - these are Ren's
    own predictions, not the custodian's ground truth.

    Every event whose action class the benchmark's vocabulary does not
    name is REPORTED in ``unmapped_actions`` instead of being dropped
    or scored under a class that means something else.  An event that
    names no action at all is not one of those: the benchmark scores
    actions, so it is COUNTED per source and nothing else.
    """
    by_clip: dict[str, list[TemporalEvent]] = {}
    for event in index.events:
        by_clip.setdefault(event.clip_id or "", []).append(event)

    sources: list[dict[str, Any]] = []
    unmapped: list[dict[str, Any]] = []
    for clip_id in sorted(by_clip):
        events = sorted(by_clip[clip_id],
                        key=lambda event: (event.start_seconds,
                                           event.event_id))
        source_id = (source_id_by_clip or {}).get(clip_id, clip_id)
        duration = index.clip_durations.get(clip_id)
        proposals: list[dict[str, Any]] = []
        without_action = 0
        for number, event in enumerate(events):
            if event.action is None:
                # The event names no ACTION at all - a scene
                # transition, a pause between words, a measured sound.
                # The benchmark scores actions, so there is nothing to
                # map: the event is counted, and it is NOT reported as
                # an action the benchmark's vocabulary failed to name.
                without_action += 1
                continue
            event_class = benchmark_class(event.action)
            if event_class is None:
                unmapped.append({
                    "event_id": event.event_id,
                    "clip_id": clip_id,
                    "action": event.action,
                    "event_type": event.event_type,
                    "reason": ("the benchmark's vocabulary names no event "
                               "class for this action"),
                })
                continue
            if event_class not in EVENT_CLASSES:
                raise ValueError(
                    f"the mapped event class {event_class!r} is outside the "
                    f"benchmark's vocabulary")
            proposals.append({
                "proposal_id": f"{source_id}-p{number:03d}",
                "event_class": event_class,
                "identity_id": _identity_id(event),
                "start": round(event.start_seconds, 3),
                "end": round(event.end_seconds, 3),
                "proposal_method": event.producer or "unknown",
                "verifier": _verdict(event),
            })
        sources.append({
            "source_id": source_id,
            "duration_seconds": duration,
            "proposal_generation_seconds": proposal_generation_seconds,
            "proposals": proposals,
            "events": [],
            "hard_negatives": [],
            "events_without_action_class": without_action,
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "split": split,
        "sources": sources,
        "unmapped_actions": unmapped,
    }


def dataset_readiness(dataset: Mapping[str, Any],
                      corpus_manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Whether this dataset COULD be scored, and what is in the way.

    The custodian gate is the corpus manifest's own: held-out labels
    must be released under custodian control before any quality number
    may be produced.  The answer here is that gate's answer, read from
    the manifest, so nobody has to re-derive it.
    """
    access = corpus_manifest.get("held_out_access") or {}
    released = (access.get("status") == "sealed_custodian_only"
                and access.get("tuning_access") is False
                and access.get("label_payloads_in_repository") is False)
    reasons: list[str] = []
    if not released:
        reasons.append(
            f"the corpus manifest's held-out access is "
            f"{access.get('status')!r}, not 'sealed_custodian_only'; no "
            f"held-out quality score may be produced")
    if dataset.get("split") != "held_out":
        reasons.append(
            f"the dataset declares the split {dataset.get('split')!r}; the "
            f"benchmark scores quality only on the held_out split")
    manifest_sources = {row.get("source_id") for row in
                        (corpus_manifest.get("sources") or [])}
    unknown = [source["source_id"] for source in dataset.get("sources") or []
               if manifest_sources and source["source_id"] not in manifest_sources]
    if unknown:
        reasons.append(
            f"these sources are not in the corpus manifest: {unknown}")
    return {
        "ready": not reasons,
        "held_out_labels_released": released,
        "dataset_split": dataset.get("split"),
        "proposal_count": sum(len(source.get("proposals") or [])
                              for source in dataset.get("sources") or []),
        "unmapped_actions": len(dataset.get("unmapped_actions") or []),
        "reasons": reasons,
    }


def main(argv: list[str] | None = None) -> int:
    """Write a project's typed events as the benchmark's dataset."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Write typed temporal events as a temporal-event "
                    "benchmark dataset")
    parser.add_argument("project")
    parser.add_argument("--split", default="calibration",
                        help="the split this dataset declares (default: "
                             "calibration; the benchmark scores quality only "
                             "on held_out)")
    parser.add_argument("--corpus-manifest", default=None,
                        help="the corpus manifest to check readiness against")
    parser.add_argument("--out", default=None,
                        help="write the dataset here instead of stdout")
    args = parser.parse_args(argv)

    from library.tools import temporal_events

    index = temporal_events.build_temporal_events(args.project)
    dataset = build_dataset(index, split=args.split)
    report: dict[str, Any] = {"dataset": dataset}
    if args.corpus_manifest:
        try:
            with open(args.corpus_manifest, encoding="utf-8") as handle:
                manifest = json.load(handle)
        except (OSError, ValueError) as exc:
            report["readiness"] = {
                "ready": False,
                "reasons": [f"the corpus manifest could not be read: {exc}"],
            }
        else:
            report["readiness"] = dataset_readiness(dataset, manifest)
    payload = json.dumps(report, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(payload)
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
