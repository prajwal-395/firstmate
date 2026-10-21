#!/usr/bin/env python3
"""Capture real serialized payloads from the Python code paths.

The fixture corpus the Rust round-trip test reads. Every payload here is
produced by the Python code itself - a real `EditPatch.to_dict`, a real
`capabilities.spec_of`, a real `jobs.prepare` shape - never hand-written,
so a payload the Rust types cannot accept is a finding: either the Rust
type is wrong or Python has a key-name mismatch.

Unit-level only: nothing here touches Resolve. Run under the pipeline
interpreter:

    bin/vep python native/ren-protocol/capture_fixtures.py

Writes one JSON file per protocol type into `fixtures/`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _write(name: str, payload) -> None:
    path = FIXTURES / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    print(f"  {path.name}: {len(payload) if isinstance(payload, list) else 1} row(s)")


def edit_patches() -> list:
    """Real EditPatch dicts, through `edit_patch.EditPatch.to_dict`."""
    from library.tools import edit_patch

    def patch(patch_id, capability, operations, spans, domains,
              preconditions=(), postconditions=(), base_git_sha=""):
        return edit_patch.EditPatch(
            id=patch_id, project="Ren Qualification", timeline="Timeline 1",
            base_generation=7, capability=capability,
            affected_spans=tuple(tuple(s) for s in spans),
            conflict_domains=tuple(domains),
            operations=tuple(dict(op) for op in operations),
            preconditions=tuple(dict(c) for c in preconditions),
            postconditions=tuple(dict(c) for c in postconditions),
            base_git_sha=base_git_sha,
        ).to_dict()

    return [
        patch(
            "ren-trim-0000000000000001", "timeline.write",
            [{"op": "clip.trim", "unique_id": "clip_007",
              "in_frame": 120, "out_frame": 4312}],
            [(120, 4312)], ["timeline_structure"],
        ),
        patch(
            "ren-marker-0000000000000002", "timeline.write",
            [{"op": "marker.add", "frame": 4312, "color": "Blue",
              "name": "anchor", "note": "the turn", "duration": 30,
              "custom_data": "take-2"}],
            [(4312, 4342)], ["markers"],
        ),
        patch(
            "ren-insert-0000000000000003", "timeline.write",
            [
                {"op": "clip.insert", "unique_id": "clip_new",
                 "track_type": "video", "track_index": 1,
                 "record_in": 0, "record_out": 240,
                 "media_pool_item": "pool_abc",
                 "source_in": 100, "source_out": 340},
                {"op": "clip.set_property", "unique_id": "clip_new",
                 "key": "Pan", "value": 0.5},
                {"op": "clip.delete", "unique_id": "clip_old"},
            ],
            [(0, 240)], ["timeline_structure", "picture_transform"],
            preconditions=[{"kind": "clip_absent", "unique_id": "clip_new"}],
            postconditions=[{"kind": "clip_present", "unique_id": "clip_new"},
                            {"kind": "duration_unchanged"}],
        ),
        patch(
            "ren-retime-0000000000000004", "timeline.write",
            [{"op": "retime", "unique_id": "clip_010", "percent": 1.5}],
            [(0, 3600)], ["timeline_structure"],
        ),
        patch(
            "ren-segments-0000000000000005", "timeline.write",
            [{"op": "retime.segments", "unique_id": "clip_011",
              "segments": [{"record_frame": 0, "percent": 1.0},
                           {"record_frame": 120, "percent": 2.0}]}],
            [(0, 3600)], ["timeline_structure"],
        ),
        patch(
            "ren-track-0000000000000006", "timeline.write",
            [{"op": "track.add", "track_type": "video", "name": "V3"}],
            [(0, 3600)], ["timeline_structure"],
        ),
        patch(
            "ren-grade-0000000000000007", "color_grade.write",
            [{"op": "grade.node_set", "unique_id": "clip_012", "node_index": 1,
              "params": {"slope_r": 1.1, "slope_g": 1.0, "slope_b": 0.9,
                         "offset_r": 0.0, "offset_g": 0.01, "offset_b": -0.01,
                         "power_r": 1.0, "power_g": 1.0, "power_b": 1.0,
                         "saturation": 1.2}}],
            [(0, 3600)], ["color"],
        ),
        patch(
            "ren-plan-0000000000000008", "transitions.resolve",
            [{"op": "transition", "carrier": {"clip": "clip_013"},
              "transition": {"type": "dissolve", "duration": 12}}],
            [(0, 3600)], ["fusion"],
        ),
        patch(
            "ren-conditions-0000000000000009", "timeline.write",
            [{"op": "clip.ripple", "unique_id": "clip_014", "delta_frames": -30}],
            [(0, 3600)], ["timeline_structure"],
            preconditions=[{"kind": "clip_present", "unique_id": "clip_014"},
                           {"kind": "marker_absent", "frame": 100}],
            postconditions=[{"kind": "clip_count", "track_type": "video",
                             "track_index": 1, "count": 3},
                            {"kind": "clip_transform_equals",
                             "unique_id": "clip_014",
                             "properties": {"Pan": 0.5, "Tilt": -0.25}}],
            base_git_sha="01755778",
        ),
    ]


def capability_specs() -> list:
    """Real capability specs, through `capabilities.spec_of` + the
    protocol projection."""
    from library.tools import capabilities, operations

    specs = []
    seen_classes = set()
    for op in operations.all():
        spec = capabilities.spec_of(op)
        # A representative slice: the first capability of each cost class
        # plus every capability that authors an EditPatch.
        if spec.cost_class not in seen_classes or spec.execution.patch is not None:
            seen_classes.add(spec.cost_class)
            specs.append(capabilities.spec_to_dict(spec))
    return specs


def receipts() -> list:
    """The receipt shape `edit_patch.apply_patch` returns, as plain data.

    Built literally from the code's own construction (the receipt dict
    plus `_receipt_of`'s three additions), because the live path needs a
    Resolve connection. The shape is the contract; the round-trip test
    holds the Rust type to it.
    """
    from library.tools import timeline_shadow as shadow

    def receipt(patch_id, status, base_generation, operations,
                stopped=None, postcondition_failures=(), hold=0.1234):
        body = {
            "patch_id": patch_id,
            "status": status,
            "base_generation": base_generation,
            "operations": operations,
            "stopped": stopped,
            "postcondition_failures": list(postcondition_failures),
            "exclusive_hold_seconds": hold,
        }
        generation = shadow.Generation(
            project="Ren Qualification", timeline_id="tl_1",
            generation=base_generation + 1, timeline_name="Timeline 1",
            hash="a" * 64, source=shadow.PATCH, patch=None, receipt=None,
            recorded_at=1_700_000_000.5, verified_at=1_700_000_001.5)
        return {**body, "generation": generation.generation,
                "fingerprint": generation.hash,
                "readback": generation.summary()}

    return [
        receipt("ren-trim-0000000000000001", "committed", 7,
                [{"index": 0, "op": "clip.trim", "returned": True,
                  "failure": None}]),
        receipt("ren-insert-0000000000000003", "verification_failed", 7,
                [{"index": 0, "op": "clip.insert", "returned": True,
                  "failure": None},
                 {"index": 1, "op": "clip.set_property", "returned": True,
                  "failure": "clip 'clip_new' reads Pan=None, not 0.5"}],
                stopped=None,
                postcondition_failures=["clip_present: clip 'clip_new' is not on the timeline"]),
        receipt("ren-conditions-0000000000000009", "verification_failed", 7,
                [{"index": 0, "op": "clip.ripple", "returned": True,
                  "failure": None}],
                stopped={"index": 1, "op": "clip.delete",
                         "raised": "RuntimeError: clip 'clip_015' is not uniquely present"},
                postcondition_failures=[]),
    ]


def generation_records() -> list:
    """Real `timeline_shadow.Generation` values, serialized."""
    from library.tools import timeline_shadow as shadow

    def record(project, timeline_id, generation, timeline_name, hash_hex,
               source, patch=None, receipt=None):
        gen = shadow.Generation(
            project=project, timeline_id=timeline_id,
            generation=generation, timeline_name=timeline_name,
            hash=hash_hex, source=source, patch=patch, receipt=receipt,
            recorded_at=1_700_000_000.5, verified_at=1_700_000_001.5)
        return {
            "project": gen.project,
            "timeline_id": gen.timeline_id,
            "generation": gen.generation,
            "timeline_name": gen.timeline_name,
            "hash": gen.hash,
            "source": gen.source,
            "patch": gen.patch,
            "receipt": gen.receipt,
            "recorded_at": gen.recorded_at,
            "verified_at": gen.verified_at,
        }

    return [
        record("Ren Qualification", "tl_1", 7, "Timeline 1", "a" * 64,
               shadow.PATCH,
               patch={"id": "ren-trim-0000000000000001"},
               receipt={"patch_id": "ren-trim-0000000000000001",
                        "status": "committed"}),
        record("Ren Qualification", "tl_1", 8, "Timeline 1", "b" * 64,
               shadow.OBSERVED),
    ]


def scopes() -> list:
    """Real `scope.Scope` values, in the protocol's JSON form."""
    from library.tools import region, scope

    def to_dict(s: scope.Scope) -> dict:
        return {
            "kind": s.kind,
            "region_span": (
                {"timeline": s.region_span.timeline,
                 "start": s.region_span.start, "end": s.region_span.end}
                if s.region_span is not None else None),
            "clip_id": s.clip_id,
            "reel_ranges": (
                [{"timeline": r.timeline, "start": r.start, "end": r.end}
                 for r in s.reel_ranges]
                if s.reel_ranges is not None else None),
        }

    return [
        to_dict(scope.project()),
        to_dict(scope.clip("clip_007")),
        to_dict(scope.region(region.Region(None, 45.0, 72.0))),
        to_dict(scope.region(region.Region("Reel03", 0.0, 12.5))),
        to_dict(scope.reel([region.Region("Reel03", 0.0, 12.5),
                            region.Region("Reel03", 20.0, 30.0)])),
    ]


def resource_demands() -> list:
    """Real scheduler demands, through `resource_scheduler.profiles`."""
    from library.tools import resource_scheduler

    profiles = resource_scheduler.profiles()
    out = []
    for name in ("machine", "full_suite_gate"):
        demand = profiles[name]
        out.append({resource: amount for resource, amount in demand.items()})
    # One capability phase's demand, the shape a heavy-work acquisition
    # declares.
    for profile, demand in profiles.items():
        if ":" in profile:
            out.append({resource: amount for resource, amount in demand.items()})
            break
    return out


def verification_findings() -> list:
    """Real `qa_report.json` rows, the shape `qa_findings` reads."""
    return [
        {"metric": "lufs", "passed": False, "severity": "error",
         "detail": "Integrated loudness -20.94 LUFS, target -14.14",
         "value": -20.94, "threshold": -14.14},
        {"metric": "black_frames", "passed": True, "severity": "info",
         "detail": "No undeclared black", "value": 0, "threshold": 0},
        {"metric": "chroma_presence", "passed": True, "severity": "warning",
         "detail": "Chroma present in 41 of 45 clips", "value": 41,
         "threshold": 45},
        {"metric": "subtitle_gaps", "passed": False, "severity": "error",
         "detail": "3 uncaptioned seconds inside a speech block",
         "value": 3.2, "threshold": 0.0},
    ]


def job_submissions() -> list:
    """Real broker submissions and their schedulable shapes, through
    `jobs.prepare`."""
    from library.tools.resolved import jobs

    out = []
    for kind, params in (
        ("lease", {"exclusive": True, "interactive": True,
                   "project": "Ren Qualification", "timeline": "Timeline 1"}),
        ("timeline.snapshot", {"project": "Ren Qualification",
                               "timeline": "Timeline 1"}),
        ("timeline.apply_patch", {"patch": {
            "id": "ren-trim-0000000000000001", "project": "Ren Qualification",
            "timeline": "Timeline 1", "base_generation": 7,
            "capability": "reel.touchup", "affected_spans": [[4312, 4342]],
            "conflict_domains": ["markers"],
            "operations": [{"op": "marker.add", "frame": 4312,
                            "color": "Blue", "name": "anchor"}]}}),
    ):
        shape = jobs.prepare(kind, params)
        out.append({"op": "submit", "kind": kind, "params": params,
                    "owner": "capture", "qualification": False,
                    "shape": shape})
    return out


def refusals() -> list:
    """Real refusals, through `RenRefusal.to_dict`.

    One refusal per BLOCKER (`refusal_taxonomy.BLOCKERS`), so the Rust
    mirror's enum is exercised by the round-trip in every variant, plus
    a family that ASKS the person (a `needs_question` with an ask
    reason) and the base refusal shape.

    The base `RenRefusal` names its own blocker, the way every raise
    site does: a refusal that does not say what kind of block it was
    cannot be constructed at all.
    """
    from library.tools import (
        decision_implication, edit_patch, operations, requirements, resolve_lock,
    )
    from library.tools.reel_state import UnknownReel
    from library.tools.ren_refusal import RenRefusal

    patch_refused = edit_patch.PatchRefused(
        what="patch 'ren-x' names capability 'nope', which is not registered",
        why="a capability's id is its identity (AGENTS.md 3)",
        fix="name one of `capabilities.ids()`")
    stale = edit_patch.StalePatch(
        __import__("library.tools.edit_patch", fromlist=["EditPatch"]).EditPatch(
            id="ren-x", project="p", timeline="t", base_generation=3,
            capability="timeline.write", affected_spans=(),
            conflict_domains=(), operations=()),
        7, observed=True, rebase_possible=False,
        reason="it composes with every generation since")
    base = RenRefusal(what="what happened", why="why", fix="the fix",
                      blocker="unsupported_operation")
    # One per blocker kind, so every variant of the Rust enum is
    # round-tripped against a real refusal.
    kinds = [
        UnknownReel(
            what="reel 21 is not recorded for this project",
            why="the builds recorded reels 4 and 9",
            fix="list the recorded reels, then name one of them"),
        operations.OperationError(
            what="no operation named 'reel.regrade'",
            why="the registry serves the operations it declares",
            fix="ask for one of `operations.ids()`"),
        patch_refused,
        requirements.OverrideError(
            what="override '--nope' names no requirement",
            why="an override names a requirement this run declares",
            fix="name one of the run's requirement keys"),
        decision_implication.ImplicationRefused(
            what="the verdict names a decision the candidates never supplied",
            why="a verdict cannot imply a decision that was not asked",
            fix="supply the decision the verdict names"),
        resolve_lock.ResolveBusy(
            what="another writer holds the Resolve instance",
            why="the instance lease is exclusive",
            fix="wait for the holder to finish, then retry"),
    ]
    return ([refusal.to_dict() for refusal in kinds]
            + [stale.to_dict(), base.to_dict()])


def main() -> int:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    print(f"capturing fixtures into {FIXTURES}")
    _write("edit-patch", edit_patches())
    _write("capability-spec", capability_specs())
    _write("receipt", receipts())
    _write("generation-record", generation_records())
    _write("scope", scopes())
    _write("resource-demand", resource_demands())
    _write("verification-finding", verification_findings())
    _write("job-submission", job_submissions())
    _write("refusal", refusals())
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
