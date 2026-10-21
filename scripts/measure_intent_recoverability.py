#!/usr/bin/env python3
"""How much of a captain's instruction survives into the canonical intent.

The question this answers is narrow and measured, not rhetorical: given
the structured reading of a captain's instruction, how many of the
instructions they stated are RECOVERABLE from the canonical editorial
intent record - in the slot their own facet names.

Two metrics are reported, and the difference between them matters:

  SLOT     the instruction is carried as its own addressable row, under
           the field its facet names (a goal under `goals`, a target
           role under a request's `target_roles`, and so on).  This is
           the acceptance metric: a reader can find the instruction and
           act on it.
  TEXT     the instruction's words appear somewhere in the record.  A
           summary line that happens to contain the words counts here.
           It is reported so the weaker reading is never mistaken for
           the stronger one.

The record is built the way a RUN builds it - through `from_state`, on
the same `intent_model.editorial_spec` a run carries - so the numbers
describe the production path and not a hand-fed object.

Usage:
    python3 scripts/measure_intent_recoverability.py
    python3 scripts/measure_intent_recoverability.py --module /tmp/base.py
    python3 scripts/measure_intent_recoverability.py --json

The `--module` flag loads any editorial_intent module from a file, which
is how the BEFORE numbers are taken: the same corpus, the same reading,
the old contract.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORPUS = REPO / "tests/fixtures/intent_contract/captain_instructions.json"

#: The slot each facet must be carried in.  A facet with no slot is one
#: the canonical record has nowhere to put, and every instruction in it
#: is unrecoverable by construction.
SLOTS = {
    "goal": ("goals", "statement"),
    "inferred_goal": ("inferred_goals", "statement"),
    "requested_change": ("requests", "statement"),
    "target_role": ("requests", "target_roles"),
    "explicit_constraint": ("requests", "explicit_constraints"),
    "preservation": ("requests", "preservation"),
    "required_output": ("requests", "outputs"),
    "accepted_autonomy": ("requests", "accepted_autonomy"),
}

#: The facets whose text is the captain's own words, and must therefore
#: be a fragment of the request as it was written.
STATED_FACETS = set(SLOTS) - {"inferred_goal"}


def load_module(path: str | None):
    """The editorial intent module under measurement."""
    if path:
        spec = importlib.util.spec_from_file_location(
            "editorial_intent_under_measurement", path)
        module = importlib.util.module_from_spec(spec)
        # A dataclass resolves its own module out of sys.modules, so the
        # module has to be there before it is executed.
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    sys.path.insert(0, str(REPO))
    from library.tools import editorial_intent
    return editorial_intent


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _slot_values(record: dict, facet: str) -> list[str]:
    """The values the record carries for one facet."""
    field, key = SLOTS[facet]
    values = []
    for row in record.get(field) or ():
        if not isinstance(row, dict):
            continue
        value = row.get(key)
        if isinstance(value, list):
            values.extend(str(item) for item in value)
        elif value:
            values.append(str(value))
    return values


def build_record(module, entry: dict) -> tuple[dict | None, str]:
    """The canonical record a RUN would build from this reading."""
    state = {"intent_model": {"editorial_spec": entry["editorial_spec"]}}
    try:
        intent = module.from_state(state)
    except Exception as exc:  # noqa: BLE001 - a refused reading is data
        return None, f"{type(exc).__name__}: {exc}"
    return intent.to_dict(), ""


def measure(module) -> dict:
    """Every stated instruction, and whether the record carries it."""
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    entries = []
    totals = {"stated": 0, "slot": 0, "text": 0, "verbatim": 0,
              "invalid": 0, "unbuildable": 0}
    for entry in corpus["entries"]:
        record, refusal = build_record(module, entry)
        blob = json.dumps(record, ensure_ascii=False) if record else ""
        problems = []
        if record is not None:
            try:
                problems = module.validate_intent(
                    module.EditorialIntent.from_dict(record))
            except Exception as exc:  # noqa: BLE001 - a broken record is data
                problems = [f"{type(exc).__name__}: {exc}"]
        if record is None:
            totals["unbuildable"] += 1
        if problems:
            totals["invalid"] += 1
        rows = []
        for stated in entry["stated"]:
            facet = stated["facet"]
            text = stated["text"]
            totals["stated"] += 1
            verbatim = (facet not in STATED_FACETS
                        or normalise(text) in normalise(entry["request_text"]))
            if verbatim:
                totals["verbatim"] += 1
            in_slot = False
            if record is not None:
                in_slot = any(normalise(text) == normalise(value)
                              for value in _slot_values(record, facet))
            in_text = bool(record) and normalise(text) in normalise(blob)
            if in_slot:
                totals["slot"] += 1
            if in_text:
                totals["text"] += 1
            rows.append({
                "facet": facet,
                "text": text,
                "verbatim": verbatim,
                "slot": in_slot,
                "text_present": in_text,
            })
        entries.append({
            "id": entry["id"],
            "source": entry["source"],
            "built": record is not None,
            "refusal": refusal,
            "problems": problems,
            "rows": rows,
        })
    return {"entries": entries, "totals": totals}


def report(result: dict) -> None:
    totals = result["totals"]
    print("stated instructions:  ", totals["stated"])
    print("  verbatim fragments: ", totals["verbatim"])
    print("  recoverable in slot:", totals["slot"])
    print("  recoverable as text:", totals["text"])
    print("  entries refused:    ", totals["unbuildable"])
    print("  entries invalid:    ", totals["invalid"])
    print()
    for entry in result["entries"]:
        print(f"{entry['id']}")
        if not entry["built"]:
            print(f"  the record does not build: {entry['refusal']}")
        for problem in entry["problems"]:
            print(f"  PROBLEM: {problem}")
        for row in entry["rows"]:
            flags = []
            if not row["verbatim"]:
                flags.append("not-verbatim")
            if not row["slot"]:
                flags.append("not-in-its-slot")
            elif not row["text_present"]:
                flags.append("slot-only")
            mark = "ok " if not flags else "GAP"
            print(f"  {mark} {row['facet']:<20} {row['text'][:64]}"
                  + (f"  ({', '.join(flags)})" if flags else ""))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module", help="an editorial_intent module to measure")
    parser.add_argument("--json", action="store_true", help="print JSON")
    args = parser.parse_args(argv)
    module = load_module(args.module)
    result = measure(module)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        report(result)
    totals = result["totals"]
    return 0 if totals["slot"] == totals["stated"] else 1


if __name__ == "__main__":
    sys.exit(main())
