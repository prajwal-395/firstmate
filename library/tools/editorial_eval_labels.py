"""The held-out labels the CUSTODIAN holds, and how the harness reads them.

**Why the labels are not in this repository.**  The corpus manifest says
the held-out labels are sealed from tuning, and it is right to: the
negative and relational queries - an action, an object's state, a
person's identity, a change of room, an event that is NOT there - are
exactly the queries a retrieval FLOOR is tuned against.  A label anybody
can read is a label the floor can be tuned on, and the held-out set
stops being held out.

So the labels live outside the repository, in a store the custodian
holds, and the harness reads them at SCORING time:

* `REN_EDITORIAL_EVAL_LABELS` points at the store.  The variable names
  the store; it does not carry a machine-specific path in the
  repository.
* A checkout with no store scores the set exactly as it did before: the
  entries stay `unlabelled`, they are reported as unlabelled, and they
  are never counted as a miss.
* A checkout WITH the store scores those entries for real, and the
  report says which entries came from the store.

**What a label carries.**  The same vocabulary the set uses, so the
scorer needs no second shape: `expect` (`present` or `absent`),
`moments` (the wanted intervals, per asset), and - for an entry whose
answer is that the thing is NOT there - a `negative_assertion` naming
the scope it was checked over and why it is absent.  The category
record the corpus schema asks for travels beside it as `annotation`.

**What the loader refuses.**  A label for an entry the set does not
carry, a label whose `expect` the scorer does not know, and a label
that claims a moment on an asset the set does not hold.  A label that
cannot be read is a label that is not applied, and the entry stays
unlabelled rather than being scored on a payload nobody checked.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

SCHEMA = "editorial-evidence-v1"

#: The environment variable that names the sealed store.
STORE_ENV = "REN_EDITORIAL_EVAL_LABELS"

#: The states the scorer knows how to score.
EXPECTED_STATES = ("present", "absent")


def store_path() -> Path | None:
    """The sealed store, when one is configured."""
    raw = os.environ.get(STORE_ENV, "")
    raw = raw.strip()
    return Path(raw).expanduser() if raw else None


def load_store(path: str | Path | None = None) -> dict[str, dict]:
    """Every label in the sealed store, keyed by the entry it labels.

    A store that is not there is an empty store: the harness then scores
    the set exactly as it did before the labels existed.  A store that is
    there but unreadable is an ERROR, because a label nobody can read is
    a label the report would silently skip.
    """
    target = Path(path).expanduser() if path is not None else store_path()
    if target is None:
        return {}
    if not target.exists():
        return {}
    raw = json.loads(target.read_text(encoding="utf-8"))
    if isinstance(raw, Mapping):
        entries = raw.get("labels")
        if isinstance(entries, Mapping):
            return {str(key): dict(value) for key, value in entries.items()
                    if isinstance(value, Mapping)}
        if isinstance(entries, list):
            return _by_entry(entries)
        raise ValueError(f"{target} carries no 'labels' object")
    if isinstance(raw, list):
        return _by_entry(raw)
    raise ValueError(f"{target} holds neither a list of labels nor an object")


def _by_entry(entries: list) -> dict[str, dict]:
    """A list of label records, keyed by the entry each one labels."""
    out: dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        identifier = str(entry.get("entry_id") or "")
        if identifier:
            out[identifier] = dict(entry)
    return out


def label_problems(label: Mapping[str, Any], query: Mapping[str, Any],
                   corpus_assets: Sequence[str] = ()) -> list[str]:
    """Every way a label cannot be read, named.  Empty means it holds."""
    problems: list[str] = []
    expect = str(label.get("expect") or "")
    if expect not in EXPECTED_STATES:
        problems.append(f"expect {expect!r} is not one of {list(EXPECTED_STATES)}")
    moments = label.get("moments")
    if not isinstance(moments, list):
        problems.append("moments is not a list")
        return problems
    if expect == "present" and not moments:
        problems.append("a present label declares no moment")
    for moment in moments:
        if not isinstance(moment, Mapping):
            problems.append("a malformed moment")
            continue
        for instance in (moment.get("instances") or ()):
            if not isinstance(instance, Mapping):
                problems.append("a malformed instance")
                continue
            asset = str(instance.get("asset") or "")
            start = instance.get("start")
            end = instance.get("end")
            if not asset:
                problems.append("an instance names no asset")
            elif corpus_assets and asset not in corpus_assets:
                problems.append(f"an instance names {asset!r}, which the set "
                                "does not hold")
            if not isinstance(start, (int, float)) or not isinstance(
                    end, (int, float)) or end <= start:
                problems.append("an instance carries no positive interval")
    if expect == "absent" and not label.get("negative_assertion"):
        problems.append("an absent label states no negative assertion")
    return problems


def apply_labels(eval_set: Mapping[str, Any],
                 labels: Mapping[str, Mapping[str, Any]] | None = None
                 ) -> tuple[dict[str, Any], list[str]]:
    """The set with the sealed labels applied, and the entries applied.

    An entry the set declares `unlabelled` and the store labels becomes
    scorable: its `expect` and its `moments` come from the label.  An
    entry with no label stays unlabelled, and is never counted as a
    miss - that is the whole point of declaring it unlabelled.
    """
    applied: list[str] = []
    queries = []
    corpus_assets = list((eval_set.get("corpus") or {}).get("assets") or {})
    for query in eval_set.get("queries") or []:
        query = dict(query)
        if query.get("expect") != "unlabelled":
            queries.append(query)
            continue
        identifier = str(query.get("id") or "")
        label = (labels or {}).get(identifier)
        if not label:
            queries.append(query)
            continue
        problems = label_problems(label, query, corpus_assets)
        if problems:
            query = dict(query)
            query["label_problems"] = problems
            queries.append(query)
            continue
        query["expect"] = str(label.get("expect"))
        query["moments"] = list(label.get("moments") or [])
        if label.get("negative_assertion"):
            query["negative_assertion"] = dict(label["negative_assertion"])
        if label.get("annotation"):
            query["annotation"] = dict(label["annotation"])
        query["labels"] = "sealed_custodian_store"
        applied.append(identifier)
        queries.append(query)
    applied_set = dict(eval_set)
    applied_set["queries"] = queries
    applied_set["sealed_labels_applied"] = applied
    return applied_set, applied
