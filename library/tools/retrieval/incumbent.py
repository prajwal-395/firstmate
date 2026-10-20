"""A change to retrieval is measured against what it REPLACES.

A newer model, a new floor, a new ranking rule - each of them arrives
with its own numbers, and the temptation is to adopt it because the
numbers are new.  The numbers are not the argument.  The argument is
the DIFFERENCE against the thing that is already shipping, measured on
the same queries, on the same corpus, with the same scoring.

`compare` takes two retrieval results - the incumbent's and the
candidate's - and reports the difference, per query and in aggregate.

**The two halves of retrieval are both weighed.**  A candidate that
finds more of what was asked for but returns more windows that hold
nothing the editor asked for is not better; it trades one failure for
another.  So the comparison reports recall AND the false-positive
consequence, and the verdict needs both to agree.

**When the metrics disagree, the harness does not decide.**  A candidate
that gains recall and loses false positives is a policy question, not
a measurement question: weighting the two is the edit policy's call
(`library/tools/retrieval/consequence.py` says the same about the
seconds).  So the verdict is `not_decided` and the two deltas are
reported, rather than a total invented here.

**A candidate that ties is NOT adopted.**  `indistinguishable` means the
candidate is retained and the incumbent stays.  "Newer" is not a
measurement.

**Only common queries are compared.**  A candidate that answered a
different set of queries has not been compared with the incumbent at
all, and the queries only one of them answered are reported as
unmatched rather than silently dropped.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

CANDIDATE_BETTER = "candidate_better"
INCUMBENT_BETTER = "incumbent_better"
INDISTINGUISHABLE = "indistinguishable"
NOT_DECIDED = "not_decided"
NO_VERDICT = "no_verdict"

ADOPT = "adopt"
RETAIN = "retain_incumbent"


def _num(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rows(result: Mapping) -> dict:
    """The scored queries of a retrieval result, keyed by query id."""
    if not isinstance(result, Mapping):
        return {}
    rows = {}
    for row in result.get("results") or ():
        if not isinstance(row, Mapping):
            continue
        identifier = row.get("id")
        if identifier:
            rows[str(identifier)] = row
    return rows


def _wrong_seconds(row: Mapping) -> float:
    consequence = row.get("consequence") or {}
    value = _num(consequence.get("wrong_seconds_selected"))
    return value if value is not None else 0.0


def _missing_seconds(row: Mapping) -> float:
    consequence = row.get("consequence") or {}
    value = _num(consequence.get("missing_seconds"))
    return value if value is not None else 0.0


def compare(incumbent: Mapping, candidate: Mapping, *,
            families: Sequence[str] = ()) -> dict:
    """Compare a candidate retrieval result against the incumbent's.

    `incumbent` and `candidate` are retrieval results in the shape
    `library.tools.retrieval_eval.run` returns.  `families` narrows the
    comparison; empty means every family.
    """
    incumbent_rows = _rows(incumbent)
    candidate_rows = _rows(candidate)
    common = sorted(set(incumbent_rows) & set(candidate_rows))
    if families:
        common = [identifier for identifier in common
                  if incumbent_rows[identifier].get("family") in families]
    unmatched = sorted(set(incumbent_rows) ^ set(candidate_rows))

    per_query = []
    for identifier in common:
        before = incumbent_rows[identifier]
        after = candidate_rows[identifier]
        before_recall = _num(before.get("recall"))
        after_recall = _num(after.get("recall"))
        before_precision = _num(before.get("precision"))
        after_precision = _num(after.get("precision"))
        row = {
            "id": identifier,
            "family": before.get("family"),
            "incumbent_recall": before_recall,
            "candidate_recall": after_recall,
            "recall_delta": (None if before_recall is None or after_recall is None
                             else round(after_recall - before_recall, 4)),
            "incumbent_precision": before_precision,
            "candidate_precision": after_precision,
            "precision_delta": (
                None if before_precision is None or after_precision is None
                else round(after_precision - before_precision, 4)),
            "incumbent_wrong_seconds": _wrong_seconds(before),
            "candidate_wrong_seconds": _wrong_seconds(after),
            "incumbent_missing_seconds": _missing_seconds(before),
            "candidate_missing_seconds": _missing_seconds(after),
        }
        row["wrong_seconds_delta"] = round(
            row["candidate_wrong_seconds"] - row["incumbent_wrong_seconds"], 3)
        row["missing_seconds_delta"] = round(
            row["candidate_missing_seconds"] - row["incumbent_missing_seconds"], 3)
        per_query.append(row)

    def mean(values):
        values = [value for value in values if value is not None]
        return round(sum(values) / len(values), 4) if values else None

    before_recall = mean(row["incumbent_recall"] for row in per_query)
    after_recall = mean(row["candidate_recall"] for row in per_query)
    before_wrong = sum(row["incumbent_wrong_seconds"] for row in per_query)
    after_wrong = sum(row["candidate_wrong_seconds"] for row in per_query)
    before_missing = sum(row["incumbent_missing_seconds"] for row in per_query)
    after_missing = sum(row["candidate_missing_seconds"] for row in per_query)

    recall_delta = (None if before_recall is None or after_recall is None
                    else round(after_recall - before_recall, 4))
    wrong_delta = round(after_wrong - before_wrong, 3)
    missing_delta = round(after_missing - before_missing, 3)

    verdict, reason = _verdict(recall_delta, wrong_delta, missing_delta,
                               len(per_query))
    adoption = (ADOPT if verdict == CANDIDATE_BETTER
                else RETAIN if verdict in (INCUMBENT_BETTER, INDISTINGUISHABLE)
                else NOT_DECIDED)

    return {
        "queries_compared": len(per_query),
        "queries_unmatched": len(unmatched),
        "unmatched_query_ids": unmatched,
        "per_query": per_query,
        "macro": {
            "incumbent_recall": before_recall,
            "candidate_recall": after_recall,
            "recall_delta": recall_delta,
            "incumbent_precision": mean(row["incumbent_precision"]
                                         for row in per_query),
            "candidate_precision": mean(row["candidate_precision"]
                                         for row in per_query),
            "incumbent_wrong_seconds": round(before_wrong, 3),
            "candidate_wrong_seconds": round(after_wrong, 3),
            "wrong_seconds_delta": wrong_delta,
            "incumbent_missing_seconds": round(before_missing, 3),
            "candidate_missing_seconds": round(after_missing, 3),
            "missing_seconds_delta": missing_delta,
        },
        "verdict": verdict,
        "verdict_reason": reason,
        "adoption": adoption,
        "basis": (
            "both halves of retrieval must agree: a candidate that finds "
            "more but returns more wrong windows has traded one failure "
            "for another, and that trade is the edit policy's to make, not "
            "the harness's.  A candidate that ties is NOT adopted - newer "
            "is not a measurement."),
    }


def _verdict(recall_delta, wrong_delta, missing_delta, compared: int):
    """The verdict, and why."""
    if compared == 0:
        return NO_VERDICT, ("the two results answered no query in common, "
                            "so they have not been compared")
    if recall_delta is None:
        return NO_VERDICT, ("the compared queries carry no recall, so there "
                            "is nothing to compare")
    if recall_delta > 0 and wrong_delta <= 0 and missing_delta <= 0:
        return CANDIDATE_BETTER, (
            "the candidate finds more of what was asked for and returns no "
            "more wrong picture than the incumbent")
    if recall_delta < 0 and wrong_delta >= 0 and missing_delta >= 0:
        return INCUMBENT_BETTER, (
            "the candidate finds less and returns no fewer wrong windows "
            "than the incumbent")
    if recall_delta == 0 and wrong_delta == 0 and missing_delta == 0:
        return INDISTINGUISHABLE, (
            "the candidate and the incumbent measure the same on these "
            "queries; a tie is not a reason to change")
    return NOT_DECIDED, (
        "the two halves of retrieval disagree, and weighing a wrong window "
        "against a missed moment is the edit policy's decision")


def main(argv=None) -> int:
    """Compare two retrieval results: the incumbent's and a candidate's.

        python3 -m library.tools.retrieval.incumbent INCUMBENT.json CANDIDATE.json

    The results are the JSON that `ren eval-search --out-json` writes.
    """
    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.retrieval.incumbent",
        description=("Compare a candidate retrieval result against the "
                     "incumbent it would replace. Reports; decides nothing "
                     "on its own."))
    parser.add_argument("incumbent", help="the incumbent result JSON")
    parser.add_argument("candidate", help="the candidate result JSON")
    parser.add_argument("--families", nargs="+", default=[],
                        help="narrow the comparison to these families")
    parser.add_argument("--out-json", help="write the comparison here")
    args = parser.parse_args(argv)
    with open(args.incumbent, encoding="utf-8") as handle:
        incumbent = json.load(handle)
    with open(args.candidate, encoding="utf-8") as handle:
        candidate = json.load(handle)
    report = compare(incumbent, candidate, families=args.families)
    text = json.dumps(report, indent=1)
    if args.out_json:
        Path(args.out_json).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
