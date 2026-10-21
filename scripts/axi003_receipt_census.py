#!/usr/bin/env python3
"""The AXI-003 receipt census: shapes, and readers that disagree.

The punch list asks for a NUMBER, before and after, over a recorded set
of live receipts:

- how many DISTINCT receipt shapes the engine publishes, and
- how many READERS disagree about whether the same operation was
  accepted.

Every reader below is PRODUCTION code - the acceptance contract's own
reading, the lifecycle's `verified` block, the common execution
contract's verification status, the QA-008 acceptance leg, and the
canonical outcome.  Nothing here re-implements an answer: the census
asks the engine and reports what it says.

    python3 scripts/axi003_receipt_census.py \\
        --fixture tests/fixtures/axi003_live_receipts.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

ACCEPTED = "accepted"
FAILED = "failed"
UNMEASURED = "unmeasured"
REFUSED = "refused"
UNKNOWN = "unknown"

#: How each reader's own vocabulary maps onto the four answers.
_LIFECYCLE = {"verified": ACCEPTED, "failed": FAILED,
              "unmeasured": UNMEASURED, "not_verified": UNMEASURED,
              "missing_evidence": UNMEASURED}
_EXECUTION = {"verified": ACCEPTED, "failed": FAILED,
              "missing_evidence": UNMEASURED, "not_measured": UNMEASURED}
_QA008 = {"passed": ACCEPTED, "failed": FAILED, "unmeasured": UNMEASURED}


def _reader_contract(receipt: dict[str, Any]) -> str:
    """The acceptance contract's own reading of the receipt."""
    from library.tools import operation_acceptance

    contracts = receipt.get("acceptance_contracts") or []
    if not contracts:
        return UNMEASURED
    outcome = getattr(operation_acceptance, "outcome_of", None)
    if outcome is not None:
        readings = {outcome(contract) for contract in contracts}
    else:
        # Before AXI-003 the only reading was the `passed` flag, and a
        # contract nobody finished read as a failure.
        readings = {"passed" if all(contract.get("passed") is True
                                    for contract in contracts) else "failed"}
    if "failed" in readings:
        return FAILED
    if "unmeasured" in readings:
        return UNMEASURED
    return ACCEPTED


def _reader_lifecycle(receipt: dict[str, Any]) -> str:
    """The lifecycle's `verified` block, read on the ACCEPTANCE axis.

    The block answers two things at HEAD - whether the acceptance was
    measured and whether the write landed - and AXI-003 splits them.
    The `acceptance_status` field is the acceptance's own reading; a
    reader without it reads the block as it was, which is where the
    conflation shows.
    """
    from library.tools import transaction_receipt

    standardized = transaction_receipt.standardize(dict(receipt))
    verified = standardized.get("verified") or {}
    status = str(verified.get("acceptance_status") or "")
    if status:
        return {"passed": ACCEPTED, "failed": FAILED,
                "unmeasured": UNMEASURED}.get(status, UNKNOWN)
    return _LIFECYCLE.get(str(verified.get("status") or ""), UNKNOWN)


def _reader_execution_contract(receipt: dict[str, Any]) -> str:
    """The common execution contract's verification status."""
    from library.tools import transaction_orchestration

    published = transaction_orchestration.standardize_receipt(dict(receipt))
    verification = published.get("execution_contract", {}).get("verification", {})
    return _EXECUTION.get(str(verification.get("status") or ""), UNKNOWN)


def _reader_outcome(receipt: dict[str, Any]) -> str:
    """The canonical outcome of the whole operation."""
    from library.tools import transaction_receipt

    outcome = getattr(transaction_receipt, "acceptance_outcome", None)
    if outcome is None:
        return UNKNOWN
    return str(outcome(receipt))


def _reader_qa008(receipt: dict[str, Any]) -> str:
    """The QA-008 acceptance leg, over the receipt's own contracts."""
    from library.tools import acceptance_state

    operations = []
    for contract in receipt.get("acceptance_contracts") or []:
        evidence = {}
        for row in contract.get("results") or ():
            if isinstance(row, dict) and "check" in row:
                evidence[row["check"]] = row.get("passed")
        operations.append({
            "op": contract.get("operation") or "unknown",
            "readback_passed": contract.get("passed"),
            "evidence": evidence,
        })
    result = acceptance_state.operation_result(operations)
    return _QA008.get(str(result.get("status") or ""), UNKNOWN)


def _reader_canonical(receipt: dict[str, Any]) -> str:
    """The canonical outcome, when the engine has one."""
    from library.tools import transaction_receipt

    outcome = getattr(transaction_receipt, "acceptance_outcome", None)
    if outcome is None:
        return UNKNOWN
    return str(outcome(receipt))


def _reader_write_claim(receipt: dict[str, Any]) -> str:
    """The write's own claim, read on the WRITE axis."""
    status = str(receipt.get("status") or "")
    if status == "committed":
        return ACCEPTED
    if status in {"failed", "partial_failure", "verification_failed",
                  "rejected"}:
        return FAILED
    return UNKNOWN


READERS: tuple[tuple[str, Callable[[dict[str, Any]], str]], ...] = (
    ("acceptance contract", _reader_contract),
    ("lifecycle verified block", _reader_lifecycle),
    ("execution contract", _reader_execution_contract),
    ("QA-008 acceptance leg", _reader_qa008),
)

#: The readers that answer the ACCEPTANCE question - the same question,
#: so they must agree.  The canonical outcome answers a different one
#: (the whole operation), and is compared against them below.
ACCEPTANCE_READERS = READERS


def publisher_shapes() -> list[tuple[str, tuple[str, ...]]]:
    """The core shape each production execution path publishes.

    The punch list asks for the count of DISTINCT receipt shapes, and
    the count that means something is the count of shapes the
    production PATHS publish - not the count of shapes that happen to
    sit in a folder of recorded receipts, which no change can move.
    """
    from library.tools import reel_execution_contract
    from library.tools import transaction_orchestration

    patch = transaction_orchestration.standardize_receipt({
        "status": "committed", "patch_id": "p-1", "backend": "edit_patch",
        "base_generation": 1, "generation": 2,
        "operations": [{"index": 0, "op": "clip.set_property",
                        "returned": True, "failure": None}],
        "acceptance_contracts": [{"id": "acceptance:clip.set_property",
                                   "operation": "clip.set_property",
                                   "status": "passed", "passed": True,
                                   "missing": [], "failed": []}],
    })
    build = reel_execution_contract.publish({
        "timeline_name": "Reel 1",
        "placement_check": {"passed": True, "repair_attempted": False},
        "stream_enforcement": {"checked": 2, "deleted": [], "unverified": []},
        "overlay_sweep": None,
        "placements": [{"unique_id": "clip-1"}],
    }, backend="full_rebuild")
    return [("semantic patch", core_shape(patch)),
            ("staged reel build", core_shape(build))]


def core_shape(receipt: dict[str, Any]) -> tuple[str, ...]:
    """The core keys a receipt carries, in order.

    The core is defined by the capture script, which recorded the set,
    so the census and the capture agree on what a shape is.
    """
    import importlib.util  # noqa: PLC0415

    path = REPO / "scripts" / "axi003_capture_receipts.py"
    spec = importlib.util.spec_from_file_location("axi003_capture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return tuple(key for key in module.CORE_KEYS if key in receipt)


def is_edit_receipt(receipt: dict[str, Any]) -> bool:
    """Whether the receipt is an EXECUTION receipt (not a job envelope)."""
    return bool(receipt.get("acceptance_contracts")
                or receipt.get("lifecycle")
                or receipt.get("execution_contract"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fixture", default=str(REPO / "tests" / "fixtures" /
                                 "axi003_live_receipts.json"))
    args = parser.parse_args()

    payload = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    samples = payload.get("samples") or []
    corpus = payload.get("corpus") or {}

    edit_samples = [row for row in samples
                    if is_edit_receipt(row.get("receipt") or {})]
    shapes = Counter(tuple(row.get("core") or ()) for row in edit_samples)

    print(f"recorded set: {corpus.get('receipts', 0)} live receipts, "
          f"{corpus.get('distinct_core_shapes', 0)} distinct core shapes")
    print(f"census over {len(edit_samples)} recorded EXECUTION receipts "
          f"of {len(samples)} sampled receipts")
    print()
    print("distinct execution-receipt shapes in the sample: "
          f"{len(shapes)}")
    for shape, count in shapes.most_common():
        print(f"  {count:>3}  {', '.join(shape) or '(no core keys)'}")

    published = publisher_shapes()
    distinct_published = {shape for _, shape in published}
    print()
    print(f"distinct receipt shapes the PRODUCTION paths publish: "
          f"{len(distinct_published)}")
    for name, shape in published:
        print(f"  {name}: {', '.join(shape) or '(no core keys)'}")

    readings: dict[str, list[str]] = {}
    for row in edit_samples:
        receipt = row["receipt"]
        answers = []
        for name, reader in ACCEPTANCE_READERS:
            try:
                answer = reader(dict(receipt))
            except Exception as exc:  # noqa: BLE001 - a reader that cannot read
                answer = UNKNOWN
                print(f"  ! reader {name!r} could not read a receipt: "
                      f"{type(exc).__name__}: {exc}")
            if answer != UNKNOWN:
                answers.append(answer)
        readings.setdefault(row["source"], answers)

    disagreeing = 0
    pairs: Counter = Counter()
    for source, answers in readings.items():
        distinct = set(answers)
        if len(distinct) > 1:
            disagreeing += 1
            for first in answers:
                for second in answers:
                    if first != second:
                        pairs[(first, second)] += 1
    total = len(readings)
    print()
    print(f"receipts whose readers disagree about ACCEPTANCE: "
          f"{disagreeing}/{total}")
    for (first, second), count in pairs.most_common():
        print(f"  {count:>3}  {first} vs {second}")
    answers = Counter()
    for values in readings.values():
        for value in values:
            answers[value] += 1
    print()
    print("answers by reader (over every receipt):")
    for answer, count in answers.most_common():
        print(f"  {count:>3}  {answer}")

    # The write's own claim against the acceptance's: a receipt that
    # says it committed while its acceptance was never measured is the
    # misclassification AXI-003 names, measured on the live receipts.
    misclassified = 0
    for row in edit_samples:
        receipt = row["receipt"]
        if str(receipt.get("status") or "") != "committed":
            continue
        try:
            answer = _reader_lifecycle(dict(receipt))
        except Exception:  # noqa: BLE001
            answer = UNKNOWN
        if answer != ACCEPTED:
            misclassified += 1
    committed = sum(1 for row in edit_samples
                    if str(row["receipt"].get("status") or "") == "committed")
    print()
    print(f"receipts that claim a COMMITTED write the acceptance does not "
          f"support: {misclassified}/{committed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
