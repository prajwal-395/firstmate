#!/usr/bin/env python3
"""Measure the DECLARED scope of one request, on both doors, before and
after it moved behind the canonical request.

AXI-004b: the legacy carrier's DECLARED extent and capability were the
last two things the canonical `EditRequest` could not say, so the two
semantic front doors read one request differently - the legacy door
believed the declaration, and the canonical door measured the extent and
derived the capability. The declaration now travels in the request, and
the request's compilation honours it.

This script measures three things over the SAME corpus, so the numbers
are comparable:

1. the FIDELITY of the legacy door: the durable patch it writes before
   this lane and the one it writes after, byte for byte;
2. the DIVERGENCE the two doors had, and the divergence they have now,
   on the capability that governs the write;
3. the EXTENT: whether the declared extent is carried unchanged, and
   what the MEASURED extent would have been - reported as unmeasured
   when the corpus carries no base snapshot to measure against, never
   as equal.

Every measurement carries its OWN denominator, and a measurement nobody
could take is reported as unmeasured - never as zero.

The corpus is the real serialized legacy carriers the protocol fixtures
hold (`native/ren-protocol/fixtures/edit-patch.json`): payloads the
Python code paths produced, not hand-written ones. A corpus may also be
named on the command line.

The BEFORE readings are modelled here explicitly, the way
`measure_backend_selection.py` models the sites it deleted, so the
before/after comparison is computed rather than asserted.

Usage:
    python3 scripts/measure_declared_scope.py [--corpus PATH ...] [--json]

Nothing here contacts Resolve, writes to a project, or starts a broker.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.tools import (
    edit_patch,
    edit_request,
    legacy_transaction,
    resolve_axi_core,
)

#: The real legacy carriers the protocol fixtures hold.
DEFAULT_CORPUS = (REPO_ROOT / "native" / "ren-protocol" / "fixtures"
                  / "edit-patch.json")


# ── the BEFORE readings ────────────────────────────────────────────────
#
# AXI-004b moved the declaration into the request. These are the two
# readings as they were before that, written out so the comparison is
# computed over the corpus rather than asserted.


def _before_request(carrier: dict) -> edit_request.EditRequest:
    """The request the adapter built BEFORE the declaration moved in."""
    import dataclasses

    identifier = str(carrier.get("id") or "")
    request = edit_request.from_algebra_ops(
        [dict(operation) for operation in carrier.get("operations") or ()],
        project=str(carrier.get("project") or ""),
        timeline=str(carrier.get("timeline") or ""),
        expected_base_generation=carrier.get("base_generation"),
        originator={"kind": "agent", "name": "resolve-axi"},
        preconditions=carrier.get("preconditions") or (),
        idempotency_key=identifier,
    )
    preservation = carrier.get("preservation") or ()
    changes: dict = {"id": identifier, "idempotency_key": identifier}
    if preservation:
        changes["preservation"] = tuple(dict(row) for row in preservation)
    return dataclasses.replace(request, **changes)


def before_legacy_patch(carrier: dict) -> dict:
    """The durable patch the legacy door wrote BEFORE this lane."""
    request = _before_request(carrier)
    compiled = edit_request.compile_request(request)
    patch = edit_patch.EditPatch.from_dict({
        "id": request.id,
        "project": str(carrier.get("project") or ""),
        "timeline": str(carrier.get("timeline") or ""),
        "base_generation": int(carrier.get("base_generation") or 0),
        "capability": str(carrier.get("capability") or ""),
        "affected_spans": [list(span) for span in
                            (carrier.get("affected_spans") or ())],
        "conflict_domains": list(carrier.get("conflict_domains") or ()),
        "operations": [dict(operation) for operation in compiled.operations],
        "preconditions": [dict(row) for row in
                          (carrier.get("preconditions") or ())],
        "postconditions": [dict(row) for row in
                           (carrier.get("postconditions") or ())],
        "preservation": [dict(row) for row in
                         (carrier.get("preservation") or ())],
    })
    return patch.to_dict()


def before_canonical_capability(carrier: dict) -> str:
    """The capability the CANONICAL door derived BEFORE this lane.

    The canonical door derived the narrowest registered contract that
    covers the request's operations. A request whose operations no
    registered contract covers had no capability at all - which is
    reported as the refusal it was, not as a number.
    """
    request = _before_request(carrier)
    compiled = edit_request.compile_request(request)
    try:
        return resolve_axi_core._patch_capability(compiled)
    except ValueError as exc:
        return f"no registered contract covers it: {exc}"


# ── the AFTER readings ─────────────────────────────────────────────────


def after_legacy_patch(carrier: dict) -> dict:
    """The durable patch the legacy door writes NOW."""
    return legacy_transaction.patch_for(carrier)


def after_canonical_capability(carrier: dict) -> str:
    """The capability the CANONICAL door reads NOW."""
    request = legacy_transaction.request_for(carrier)
    compiled = edit_request.compile_request(request)
    return resolve_axi_core._capability_for(request, compiled)


# ── the measurement ────────────────────────────────────────────────────


def measure(carriers: list[dict]) -> dict:
    """The measurement, with its denominators."""
    total = len(carriers)
    legacy_patch_identical = 0
    capability_diverged_before = 0
    capability_diverged_after = 0
    capability_derived_before = 0
    extent_carried = 0
    extent_measured = 0
    rows = []
    for carrier in carriers:
        before = before_legacy_patch(carrier)
        after = after_legacy_patch(carrier)
        identical = before == after
        legacy_patch_identical += int(identical)
        derived_before = before_canonical_capability(carrier)
        derived_after = after_canonical_capability(carrier)
        declared = str(carrier.get("capability") or "")
        capability_derived_before += int(
            not derived_before.startswith("no registered contract"))
        diverged_before = derived_before != declared
        diverged_after = derived_after != declared
        capability_diverged_before += int(diverged_before)
        capability_diverged_after += int(diverged_after)
        request = legacy_transaction.request_for(carrier)
        compiled = edit_request.compile_request(request)
        declared_spans = [list(span) for span in
                          (carrier.get("affected_spans") or ())]
        carried = [list(span) for span in compiled.affected_spans]
        extent_carried += int(carried == declared_spans)
        rows.append({
            "id": str(carrier.get("id") or ""),
            "declared_capability": declared,
            "derived_capability_before": derived_before,
            "derived_capability_after": derived_after,
            "capability_diverged_before": diverged_before,
            "capability_diverged_after": diverged_after,
            "declared_extent": declared_spans,
            "compiled_extent": carried,
            "extent_carried_unchanged": carried == declared_spans,
            "legacy_patch_identical": identical,
        })
    return {
        "carriers": total,
        "legacy_patch_identical_before_after": legacy_patch_identical,
        "legacy_patch_identical_denominator": total,
        "capability_derived_by_the_canonical_door_before":
            capability_derived_before,
        "capability_diverged_before": capability_diverged_before,
        "capability_diverged_after": capability_diverged_after,
        "extent_carried_unchanged": extent_carried,
        "extent_measured": extent_measured,
        "extent_measured_note": (
            "the corpus carries no base snapshot, so the MEASURED extent "
            "was not taken; it is unmeasured, never equal"),
        "rows": rows,
    }


def load_corpus(paths: list[Path]) -> list[dict]:
    carriers: list[dict] = []
    for path in paths:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        rows = payload if isinstance(payload, list) else [payload]
        for row in rows:
            if isinstance(row, dict) and row.get("operations"):
                carriers.append(row)
    return carriers


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 scripts/measure_declared_scope.py",
        description="Measure the declared scope of one request on both "
                    "doors, before and after it moved behind the "
                    "canonical request.")
    parser.add_argument("--corpus", action="append", default=None,
                        help="a JSON file of legacy carriers "
                             "(repeatable; defaults to the protocol "
                             "fixtures)")
    parser.add_argument("--json", action="store_true",
                        help="print the measurement as JSON")
    args = parser.parse_args(argv)

    paths = ([Path(path) for path in args.corpus] if args.corpus
             else [DEFAULT_CORPUS])
    carriers = load_corpus(paths)
    report = measure(carriers)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    total = report["carriers"]
    print(f"legacy carriers measured: {total}")
    print(f"legacy door's durable patch identical before and after: "
          f"{report['legacy_patch_identical_before_after']}/{total}")
    print(f"capability the canonical door derived (before): "
          f"{report['capability_derived_by_the_canonical_door_before']}/{total}")
    print(f"capability readings that diverged - before: "
          f"{report['capability_diverged_before']}/{total}, after: "
          f"{report['capability_diverged_after']}/{total}")
    print(f"declared extent carried unchanged: "
          f"{report['extent_carried_unchanged']}/{total}")
    print(f"measured extent: {report['extent_measured']}/{total} "
          f"({report['extent_measured_note']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
