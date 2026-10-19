"""The cascade engine: one dependency graph over the existing skip paths.

Lane D of the linked-model cascade plan (firstmate
`data/vep-linked-model-cascade-plan/report.md`, section 3.1): the cascade
primitives already exist as separate hash/skip schemes that were never
joined into one graph. This module joins them WITHOUT adding a new skip
mechanism - it holds the static edge table and the shared `mark_dirty`
walker, and the three existing skip paths call it.

The edge table below is the report's section 3.1 table, row for row.
Each edge names the hash/key that already decides it (the owner module
in parentheses) and the reuse path that already serves a clean edge.
Nothing here reimplements those hashes: the dirty TEST for an edge is
the stored hash compared against a fresh one, run by the owner, and this
module only records which edges a change reaches and whether each
reached edge still matches what is on disk.

A change is `(entity_kind, entity_id, revision)`. `mark_dirty` walks
dependents; at each edge it runs the edge's dirty test against the
stored hashes in the reel's `derived_registry.json`. Anything clean is
reused through the existing skip paths; anything dirty regenerates and
re-hashes. Fail-closed throughout: an unknown kind, an unresolvable
item, or a missing hash marks the whole reel dirty - today's
`dirty_regions` / `reel_rebuild_need` behaviour, kept.

`derived_registry.json` generalises three stores into one file per reel:
`plan_provenance.json`'s `build_signatures`, the caption ledger's
`caption_hashes` / `footage_binding_hashes`, and the subtitle render
ledger. Lane E retired the provenance writes (the build files only
this registry, and deliver stamps the same hashes onto the model's
render receipt) - the registry is the one place a dirty walk reads
all of them, with the legacy maps as fallback for reels that
predate it.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

#: The registry's own format tag. A reader that does not know this tag
#: refuses rather than guessing - a registry from a newer engine is not
#: evidence that anything is clean.
REGISTRY_FORMAT = "derived_registry/1"

REGISTRY_DIRNAME = "derived_registry"


#: The report's section 3.1 edge/input-hash table, row for row. Each edge
#: carries the named hash (owner), the dirty test, and the existing reuse
#: path. The new piece is joining them; every hash and every path below
#: already exists today.
EDGES = (
    {
        "edge": "bytes -> lanes",
        "hash": "footage_identity.fingerprint",
        "dirty_test": "digest+size differ",
        "reuse": "lane files kept per digest; old digest never served",
    },
    {
        "edge": "lanes+catalog -> index",
        "hash": "ingest_fingerprint (analysis/footage_segments.py)",
        "dirty_test": "`stale` verb compare",
        "reuse": "no rebuild; queries refuse when stale",
    },
    {
        "edge": "drawing -> caption .mov",
        "hash": "content_key triple (render_cache.py)",
        "dirty_test": "file + `_reuse_key.txt` equal?",
        "reuse": "skip render; hard-link dedupe across stems",
    },
    {
        "edge": "plan -> overlay .mov",
        "hash": "`_reuse_key.txt` + `find_by_key` (render_cache.py)",
        "dirty_test": "key match",
        "reuse": "hard-link materialise",
    },
    {
        "edge": "words+pixels+renderer -> caption binding",
        "hash": "caption_content_hash v1 (plan_provenance.py)",
        "dirty_test": "string compare",
        "reuse": "keep placed segment",
    },
    {
        "edge": "picture-under-words",
        "hash": "footage_binding_hash v1 (plan_provenance.py)",
        "dirty_test": "string compare",
        "reuse": "keep placements",
    },
    {
        "edge": "reel derivation",
        "hash": "derivation_digest + ENGINE_CODE_TREES + "
                "DECLARATION_SOURCES (reel_rebuild_need.py)",
        "dirty_test": "both-halves match",
        "reuse": "LEAVE_ALONE",
    },
    {
        "edge": "timeline as-built",
        "hash": "carried_digest over CARRIED_CLIP_KEYS "
                "(reel_rebuild_need.py)",
        "dirty_test": "compare via `cursor_excursion` self-read",
        "reuse": "skip placement",
    },
    {
        "edge": "planning input",
        "hash": "planning_reuse full-closure sha (planning_reuse.py)",
        "dirty_test": "witness match",
        "reuse": "skip planning step",
    },
    {
        "edge": "verify scope",
        "hash": "dirty_regions spans widened by HANDLE_SECONDS "
                "(dirty_regions.py)",
        "dirty_test": "receipt block",
        "reuse": "`run_scoped_render_qa(window)`",
    },
    {
        "edge": "mask file",
        "hash": "digest+size+fps+face_box "
                "(`_stored_segmentation_reusable`)",
        "dirty_test": "triple compare",
        "reuse": "skip SAM re-measure",
    },
)

#: Every edge name, for callers that address one directly.
EDGE_NAMES = tuple(edge["edge"] for edge in EDGES)


#: What a change can BE. A change outside this set is not a narrower
#: question - it is an unknown one, and `mark_dirty` answers it with
#: the whole reel dirty.
CHANGE_KINDS = (
    "source_bytes",
    "lane",
    "catalog",
    "index",
    "caption_drawing",
    "overlay_plan",
    "caption_binding",
    "footage_binding",
    "derivation",
    "carried",
    "planning_input",
    "mask",
    "render",
)


#: Which edges a change of each kind can reach. Edges not listed are
#: clean by construction: a title text edit cannot dirty a mask file,
#: so the mask edge is never even asked. Anything NOT in
#: `CHANGE_KINDS` reaches every edge (fail-closed in `mark_dirty`).
DEPENDENTS: dict[str, tuple[str, ...]] = {
    "source_bytes": ("bytes -> lanes",),
    "lane": ("lanes+catalog -> index",),
    "catalog": ("lanes+catalog -> index", "picture-under-words"),
    "index": ("lanes+catalog -> index",),
    "caption_drawing": (
        "drawing -> caption .mov",
        "words+pixels+renderer -> caption binding",
    ),
    "overlay_plan": ("plan -> overlay .mov",),
    "caption_binding": (
        "words+pixels+renderer -> caption binding",
        "reel derivation",
    ),
    "footage_binding": ("picture-under-words", "reel derivation"),
    "derivation": ("reel derivation",),
    "carried": ("timeline as-built",),
    "planning_input": ("planning input",),
    "mask": ("mask file", "plan -> overlay .mov", "reel derivation"),
    "render": ("verify scope", "reel derivation"),
}


def edge_of(name: str) -> Optional[dict]:
    """The edge row named `name`, or None when no edge carries it."""
    for edge in EDGES:
        if edge["edge"] == name:
            return dict(edge)
    return None


def skip_allowed(edge_name: str, fresh: str | None,
                 recorded: str | None) -> tuple[bool, str]:
    """Whether an edge's skip may be taken - the SHARED dirty test.

    This is the walker the three skip paths call instead of each
    spelling its own comparison: one string compare, fail-closed. A
    skip is allowed exactly when both halves are present and equal.
    Empty can never match - an unestablished key (`content_key` returns
    `""` when the renderer tree cannot be read) renders rather than
    skips, and a missing record rebuilds rather than reading as
    unchanged. No caller may invert this: it never authorises reuse,
    it only refuses it.
    """
    if edge_of(edge_name) is None:
        return False, (
            f"unknown cascade edge {edge_name!r} - refusing the skip, "
            f"because an edge nothing names is never a matching one")
    if not fresh:
        return False, (
            f"{edge_name}: the fresh hash could not be established - "
            f"an unknown derivation is never a matching one")
    if not recorded:
        return False, (
            f"{edge_name}: no hash on record for this edge - nothing "
            f"to compare, so it regenerates")
    if fresh != recorded:
        return False, (
            f"{edge_name}: the fresh hash differs from the recorded "
            f"one - the input changed since the artefact was made")
    return True, (
        f"{edge_name}: fresh and recorded hashes agree - the input "
        f"is unchanged, so the existing skip path may serve it")


def caption_reuse_allowed(fresh_key: str | None,
                          recorded_key: str | None) -> tuple[bool, str]:
    """The subtitle `content_key` skip, through the shared walker."""
    return skip_allowed("drawing -> caption .mov", fresh_key, recorded_key)


def overlay_reuse_allowed(fresh_key: str | None,
                          recorded_key: str | None) -> tuple[bool, str]:
    """The motion-graphics `find_by_key` skip, through the shared walker."""
    return skip_allowed("plan -> overlay .mov", fresh_key, recorded_key)


def planning_reuse_allowed(fresh: str | None,
                           recorded: str | None) -> tuple[bool, str]:
    """The planning witness skip, through the shared walker."""
    return skip_allowed("planning input", fresh, recorded)


def mark_dirty(change: Mapping[str, Any],
               registry: Optional[Mapping[str, Any]] = None) -> dict:
    """Which edges a change dirties, walked from the edge table.

    `change` is `{"entity_kind", "entity_id", "revision"}`. `registry`
    is the reel's derived registry (`read_registry`) or None. Returns
    `{"dirty", "clean", "whole_reel", "reason"}`.

    Fail-closed in three ways, and each is said in `reason`:

    - the change is not a mapping, names no known kind, or carries no
      id: the whole reel is dirty;
    - the registry is absent or carries no hash for a reached edge:
      that edge is dirty (an absent record costs work, never a wrong
      skip);
    - a reached edge whose recorded hash differs from the fresh one
      the change carries: dirty.

    A change may carry `fresh_hashes{edge: hash}`; a reached edge whose
    recorded hash equals the carried fresh one is clean. Without fresh
    hashes every reached edge is dirty - a change that says what moved
    but not what it moved to still costs the edges it reaches.
    """
    if not isinstance(change, Mapping):
        return {"dirty": list(EDGE_NAMES), "clean": [],
                "whole_reel": True,
                "reason": "the change is not a mapping - an unknown "
                          "change dirties the whole reel"}
    kind = change.get("entity_kind")
    entity_id = change.get("entity_id")
    if kind not in DEPENDENTS:
        return {"dirty": list(EDGE_NAMES), "clean": [],
                "whole_reel": True,
                "reason": f"unknown change kind {kind!r} - an unknown "
                          f"op dirties the whole reel"}
    if not entity_id:
        return {"dirty": list(EDGE_NAMES), "clean": [],
                "whole_reel": True,
                "reason": "the change names no entity id - an "
                          "unresolvable item dirties the whole reel"}
    reached = DEPENDENTS[kind]
    fresh_hashes = change.get("fresh_hashes") or {}
    if not isinstance(fresh_hashes, Mapping):
        fresh_hashes = {}
    stored = {}
    if isinstance(registry, Mapping):
        hashes = registry.get("hashes")
        if isinstance(hashes, Mapping):
            stored = dict(hashes)
    dirty: list[str] = []
    clean: list[str] = []
    for edge in EDGE_NAMES:
        if edge not in reached:
            clean.append(edge)
            continue
        fresh = fresh_hashes.get(edge)
        recorded = stored.get(edge)
        allowed, _ = skip_allowed(edge, fresh, recorded)
        # `skip_allowed` True means the edge still matches what is on
        # disk, so the change does NOT dirty it. Every other outcome
        # - including a missing registry - dirties it.
        if allowed:
            clean.append(edge)
        else:
            dirty.append(edge)
    whole = not stored or not fresh_hashes
    if dirty and (not stored or not fresh_hashes):
        reason = (f"{kind} {entity_id}: {len(dirty)} edge(s) dirty "
                  f"({', '.join(dirty)}); "
                  f"{'no derived registry on record' if not stored else 'the change carries no fresh hashes'} - "
                  f"missing hashes dirty, never clean")
    elif dirty:
        reason = (f"{kind} {entity_id}: {len(dirty)} edge(s) dirty "
                  f"({', '.join(dirty)}); "
                  f"{len(clean)} edge(s) clean")
    else:
        reason = (f"{kind} {entity_id}: every reached edge still "
                  f"matches - nothing to regenerate")
        whole = False
    return {"dirty": dirty, "clean": clean,
            "whole_reel": whole and bool(dirty), "reason": reason}


def gate_rebuild_decision(decision, *,
                          fresh_derivation: Optional[str] = None,
                          recorded: Optional[Mapping[str, Any]] = None,
                          registry: Optional[Mapping[str, Any]] = None):
    """The `reel_rebuild_need` LEAVE_ALONE verdict, through the walker.

    D2's third wiring: a LEAVE_ALONE stands only when the shared walker
    agrees the derivation edge is clean - against the recorded build
    signature AND, where one exists, the reel's derived registry. A
    LEAVE_ALONE the walker cannot confirm becomes a REBUILD naming
    why; a REBUILD is never turned into a LEAVE_ALONE here - this gate
    only refuses skips, it never authorises one. An absent registry is
    no opinion (the build signatures stay authoritative until Lane E),
    never a rebuild: the first build after this lane populates the
    registry going forward instead of re-placing the fleet once.
    """
    from library.tools import reel_rebuild_need as _need

    if decision is None or not getattr(decision, "leave_alone", False):
        return decision
    recorded_derivation = None
    if isinstance(recorded, Mapping):
        recorded_derivation = recorded.get("derivation")
    allowed, why = skip_allowed("reel derivation", fresh_derivation,
                                recorded_derivation)
    if not allowed:
        return _need.Decision(
            reel=decision.reel,
            action=_need.REBUILD,
            reason=(f"the cascade walker refuses the skip ({why}) - "
                    f"the reel rebuilds; original verdict was: "
                    f"{decision.reason}"),
            derivation=fresh_derivation,
            carried=getattr(decision, "carried", None),
            recorded=getattr(decision, "recorded", {}),
        )
    if isinstance(registry, Mapping):
        hashes = registry.get("hashes")
        recorded_registry = hashes.get("reel derivation") \
            if isinstance(hashes, Mapping) else None
        registry_allowed, registry_why = skip_allowed(
            "reel derivation", fresh_derivation, recorded_registry)
        if not registry_allowed:
            return _need.Decision(
                reel=decision.reel,
                action=_need.REBUILD,
                reason=(f"the derived registry refuses the skip "
                        f"({registry_why}) - the reel rebuilds; the "
                        f"build signatures agreed, so one of the two "
                        f"stores is stale and the fail-closed answer "
                        f"is a placement"),
                derivation=fresh_derivation,
                carried=getattr(decision, "carried", None),
                recorded=getattr(decision, "recorded", {}),
            )
    return decision


# ── The per-reel derived registry ────────────────────────────────────
#
# One file per reel, generalising three stores: `build_signatures`
# (derivation+carried), the caption hashes (caption binding + footage
# binding), and the subtitle render ledger's content keys. Lane E
# retired the provenance writes, so this registry is where new
# baselines land - the legacy maps stay readable for reels that
# predate it, and the render receipt carries the same hashes from
# deliver on.


def registry_dir(review_dir: str) -> str:
    """The directory holding every reel's derived registry."""
    return os.path.join(str(review_dir), REGISTRY_DIRNAME)


def _reel_filename(reel: str) -> str:
    safe = "".join(c if (c.isalnum() or c in ("-", "_")) else "_"
                   for c in str(reel or "").strip())
    return f"{safe or 'unnamed'}.json"


def registry_path(review_dir: str, reel: str) -> str:
    """The derived registry file for one reel."""
    return os.path.join(registry_dir(review_dir), _reel_filename(reel))


def read_registry(review_dir: str, reel: str) -> Optional[dict]:
    """The reel's derived registry, or None when nothing is recorded.

    A registry from another format, or one that is not a mapping, is
    refused as absent: a record nothing here wrote is not evidence
    that anything is clean.
    """
    path = Path(registry_path(review_dir, reel))
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(doc, Mapping):
        return None
    if doc.get("format") != REGISTRY_FORMAT:
        return None
    if not isinstance(doc.get("hashes"), Mapping):
        return None
    return dict(doc)


def write_registry(review_dir: str, reel: str,
                   hashes: Mapping[str, Any],
                   extra: Optional[Mapping[str, Any]] = None) -> str:
    """Record one reel's derived hashes. Returns the file path.

    `hashes` maps edge names (or the short key names below) to the
    recorded hash. Merges over the existing record per key, so a
    partial rebuild updates only its own reel's entries - the same
    merge rule `plan_provenance.write_provenance` keeps.
    """
    directory = Path(registry_dir(review_dir))
    directory.mkdir(parents=True, exist_ok=True)
    path = Path(registry_path(review_dir, reel))
    existing: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, Mapping) and \
                    loaded.get("format") == REGISTRY_FORMAT:
                existing = dict(loaded.get("hashes") or {})
        except (OSError, ValueError):
            existing = {}
    merged = dict(existing)
    for key, value in (hashes or {}).items():
        if value is not None:
            merged[str(key)] = value
    doc: dict[str, Any] = {
        "format": REGISTRY_FORMAT,
        "reel": str(reel),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "hashes": merged,
    }
    if extra:
        doc["extra"] = dict(extra)
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return str(path)


def update_registry_from_build(review_dir: str, reel: str, *,
                               derivation: Optional[str] = None,
                               carried: Optional[str] = None,
                               caption_binding: Optional[str] = None,
                               footage_binding: Optional[str] = None,
                               model_revision: Any = None) -> str:
    """File what one reel's build derived, under the edge names.

    Reads the caption/binding halves off the caller (which already
    holds them - `plan_provenance` for the hashes, the build record
    for the digests), never recomputing: a second spelling of a hash
    is how a check passes a value the build did not carry.
    """
    hashes: dict[str, Any] = {}
    if derivation:
        hashes["reel derivation"] = derivation
    if carried:
        hashes["timeline as-built"] = carried
    if caption_binding:
        hashes["words+pixels+renderer -> caption binding"] = \
            caption_binding
    if footage_binding:
        hashes["picture-under-words"] = footage_binding
    extra = {}
    if model_revision is not None:
        extra["model_revision"] = model_revision
    return write_registry(review_dir, reel, hashes,
                          extra=extra or None)


# ── D3: scoped QA from dirty spans ───────────────────────────────────
#
# The default verify after any partial rebuild: the located detectors
# re-read only the dirty spans (`render_qa.run_scoped_render_qa`), and
# the hearing drift runs are filtered to the same spans. Both REPORT;
# neither gates - timing drift is report-only (`reel_hearing.GATES`),
# and a scoped pass that found nothing outside its spans proves
# nothing about those spans. The full conformance gate still runs; this
# is the cheap second half that says what the rebuild touched.


def hearing_drift_in_scope(runs: Sequence[Mapping[str, Any]],
                           dirty: Mapping[str, Any]) -> list:
    """The hearing drift runs overlapping a dirty span, in reel seconds.

    Pure: drift runs carry `planned_start`/`planned_end`, dirty spans
    carry `start_seconds`/`end_seconds` (`dirty_regions.spans_for` over
    the picture+audio domains). A run wholly outside every span is a
    run this rebuild did not move. An unreadable dirty block keeps
    every run - a missing statement of what changed is never read as
    "nothing changed".
    """
    rows = list(runs or ())
    if not rows:
        return []
    try:
        from library.tools.dirty_regions import spans_for
        spans = spans_for(dirty, ("picture", "audio"))
    except Exception:                                   # noqa: BLE001
        return list(rows)
    if not spans:
        if isinstance(dirty, Mapping) and dirty.get("whole_reel"):
            return list(rows)
        return []
    out = []
    for run in rows:
        if not isinstance(run, Mapping):
            continue
        try:
            start = float(run.get("planned_start", 0.0))
            end = float(run.get("planned_end", start))
        except (TypeError, ValueError):
            out.append(run)
            continue
        if any(start < hi and end > lo for lo, hi, _, _ in spans):
            out.append(run)
    return out


def scoped_verify(video_path: str, dirty: Mapping[str, Any], *,
                  hearing_runs: Optional[Sequence[Mapping[str, Any]]] = None,
                  declared_black_beats=None,
                  declared_ending_black_spans=None,
                  declared_silence_spans=None) -> dict:
    """The D3 verify: scoped render QA plus the in-scope drift runs.

    Returns `{"results", "not_rechecked", "drift_in_scope",
    "drift_total", "detail"}`. Never raises on content: an
    unmeasurable render yields an error row, not an exception - a
    verify that throws where the gate expected rows is the defect
    AGENTS.md 10.4 names. Hearing drift stays report-only here
    whatever it finds; script divergence and caption coverage still
    gate through the hearing record itself (`reel_hearing.GATES`).
    """
    from library.tools import render_qa as _qa

    if not isinstance(dirty, Mapping):
        return {"results": [], "not_rechecked": [],
                "drift_in_scope": [], "drift_total": len(
                    hearing_runs or ()),
                "detail": "no dirty block was given - nothing scoped "
                          "was re-checked"}
    # The drift re-check is measured first and independently: it is
    # pure arithmetic over the hearing record, so a render the
    # detectors cannot read (missing ffmpeg, unreadable file) still
    # gets its drift verdict.
    in_scope = hearing_drift_in_scope(hearing_runs or (), dirty)
    try:
        results, not_rechecked = _qa.run_scoped_render_qa(
            video_path, dirty,
            declared_black_beats=declared_black_beats,
            declared_ending_black_spans=declared_ending_black_spans,
            declared_silence_spans=declared_silence_spans)
    except Exception as exc:                            # noqa: BLE001
        results, not_rechecked = [], []
        detail = (f"scoped render QA could not be measured: {exc}; "
                  f"{len(in_scope)} of {len(hearing_runs or ())} "
                  f"hearing drift run(s) overlap the dirty spans "
                  f"(report-only)")
    else:
        found = sum(1 for row in results if not row.passed)
        detail = (f"scoped QA over the dirty spans: "
                  f"{len(results)} detector(s) re-read, "
                  f"{len(not_rechecked)} not reached by the dirty "
                  f"domains, {found} finding(s); "
                  f"{len(in_scope)} of {len(hearing_runs or ())} "
                  f"hearing drift run(s) overlap the dirty spans "
                  f"(report-only)")
    return {"results": [row.as_row() if hasattr(row, "as_row") else
                        dict(row) for row in results],
            "not_rechecked": list(not_rechecked),
            "drift_in_scope": [dict(r) if isinstance(r, Mapping) else r
                               for r in in_scope],
            "drift_total": len(hearing_runs or ()),
            "detail": detail}


def scoped_verify_partial_build(project_folder: str,
                                built_reels: Sequence[str],
                                dirty_by_reel: Optional[Mapping[str, Any]] = None,
                                hearing_runs_by_reel: Optional[Mapping[
                                    str, Sequence[Mapping[str, Any]]]] = None,
                                exports_dir: Optional[str] = None) -> dict:
    """D3's default verify after a partial rebuild, REPORT-only.

    For every rebuilt reel that has an export on disk, re-reads only
    the dirty spans (`run_scoped_render_qa`) and re-checks the hearing
    drift runs against the same spans (`hearing_drift_in_scope`). A
    reel with no export yet defers with a note (delivery lands the
    render later); a reel whose dirty spans are unknown runs nothing
    scoped - a whole-reel change is what the full conformance gate
    already graded. Never raises and never gates: a scoped pass that
    found nothing outside its spans proves nothing about those spans,
    and hearing drift is report-only (`reel_hearing.GATES`).

    The drift runs arrive IN MEMORY (`hearing_runs_by_reel`, the
    `runs` of the `reel_hearing.Hearing` the deliver path already
    holds) - never read back off the hearing record file,
    which stays write-only
    (`tests/unit/reels/test_reel_hearing.py` pins that no gate opens
    it; the gate acts through `reel_hearing.refusal`).

    Returns `{"checked", "deferred", "findings", "detail"}` for the
    build log. Every outcome is said on stderr beside the return, so
    a deferred reel never reads as a verified one.
    """
    import sys as _sys

    dirty_by_reel = dirty_by_reel or {}
    hearing_runs_by_reel = hearing_runs_by_reel or {}
    if exports_dir is None:
        exports_dir = os.path.join(str(project_folder), "exports")
    checked: list[str] = []
    deferred: list[dict] = []
    findings: list[dict] = []
    for reel in built_reels or ():
        video = _export_for_reel(exports_dir, str(reel))
        if video is None:
            deferred.append({"reel": str(reel),
                             "reason": "no export on disk - scoped QA "
                                       "defers to delivery"})
            print(f"  cascade scoped QA: {reel} has no export yet - "
                  f"deferred to delivery", file=_sys.stderr)
            continue
        dirty = dirty_by_reel.get(str(reel))
        if not isinstance(dirty, Mapping):
            deferred.append({"reel": str(reel),
                             "reason": "no dirty spans on record - the "
                                       "full conformance gate already "
                                       "graded this reel whole"})
            print(f"  cascade scoped QA: {reel} carries no dirty spans - "
                  f"the full gate stands as its verify", file=_sys.stderr)
            continue
        if dirty.get("whole_reel"):
            deferred.append({"reel": str(reel),
                             "reason": f"whole-reel dirty "
                                       f"({dirty.get('whole_reel_reason')}) - "
                                       f"the full gate already graded it"})
            print(f"  cascade scoped QA: {reel} is whole-reel dirty - "
                  f"the full gate stands as its verify", file=_sys.stderr)
            continue
        hearing_runs = hearing_runs_by_reel.get(str(reel)) or ()
        try:
            report = scoped_verify(video, dirty,
                                   hearing_runs=hearing_runs)
        except Exception as exc:                        # noqa: BLE001
            deferred.append({"reel": str(reel),
                             "reason": f"scoped QA could not run: {exc}"})
            print(f"  cascade scoped QA: {reel} could not run ({exc})",
                  file=_sys.stderr)
            continue
        checked.append(str(reel))
        findings.append({"reel": str(reel), "video": video,
                         "report": report})
        print(f"  cascade scoped QA: {reel}: {report['detail']}",
              file=_sys.stderr)
    detail = (f"{len(checked)} reel(s) scoped, {len(deferred)} deferred; "
              f"scoped QA reports, never gates")
    return {"checked": checked, "deferred": deferred,
            "findings": findings, "detail": detail}


def _export_for_reel(exports_dir: str, reel: str) -> Optional[str]:
    """The reel's export mp4 on disk, or None.

    Matched by the reel's file-safe stem, never by prefix: a Resolve
    project is addressed by its exact listed name, and so is a file
    this verify grades.
    """
    try:
        names = sorted(os.listdir(exports_dir))
    except OSError:
        return None
    safe = "".join(c if (c.isalnum() or c in ("-", "_", " ")) else "_"
                   for c in reel).strip()
    for name in names:
        if not name.lower().endswith(".mp4"):
            continue
        stem = os.path.splitext(name)[0]
        if stem == reel or stem == safe or stem.replace("_", " ") == reel:
            return os.path.join(exports_dir, name)
    return None


# ── VC-13: precise component dependency proofs ─────────────────────
#
# `mark_dirty` above walks the coarse edge table: a change dirties
# whole edges, and `reel_rebuild_need.engine_code_digest` digests
# whole source trees, so during active engine work every build
# invalidates every reel. Correct, and the price VC-13 removes.
#
# A reel is COMPONENTS, each produced by NAMED implementations - the
# operation-registry ids whose `run` is the step's own function - and
# each versioned by content (`code_identity`). The registry is read
# DYNAMICALLY at proof time, so a rename or a new producing step
# reaches the proof with nothing to update here. Anything unknown -
# an unknown component, an unknown operation, an unhashable file -
# proves nothing, and the caller fails closed to a rebuild. Every
# invalidation and every reuse below carries its reason.


#: What one reel is made of, and what each piece is produced by. Each
#: `implementations` entry is an operation-registry id, resolved
#: dynamically by `code_identity.operation_implementation_version`.
#: A component whose producing operation is renamed must land here
#: under its new id - until then its proof is incomplete and it
#: rebuilds, which is the fail-closed direction.
REEL_COMPONENTS = (
    {
        "component": "footage",
        "produces": "ranges + placements (what picture plays, where)",
        "implementations": (
            "spine.mesh",
            "picture.plan",
            "aroll.assign",
            "reel.build",
        ),
    },
    {
        "component": "audio",
        "produces": "music bed + mixed speech/sfx windows",
        "implementations": (
            "music.resolve",
            "audio_mix.resolve",
            "sfx.resolve",
            "reel.build",
        ),
    },
    {
        "component": "captions",
        "produces": "rendered caption segments + their placements",
        "implementations": (
            "subtitles.plan",
            "subtitles.render",
            "reel.build",
        ),
    },
    {
        "component": "lower_thirds",
        "produces": "speaker lower-third segments + their placements",
        "implementations": (
            "motion_graphics.render",
            "reel.build",
        ),
    },
    {
        "component": "overlays",
        "produces": "overlay/explainer/semantic segments + motion plan",
        "implementations": (
            "motion_graphics.render",
            "vfx.resolve",
            "transitions.resolve",
            "reel.build",
        ),
    },
    {
        "component": "cards",
        "produces": "intro/outro/end cards drawn over the tail",
        "implementations": (
            "motion_graphics.render",
            "reel.build",
        ),
    },
    {
        "component": "grade",
        "produces": "per-clip CDL/look/power-grade decisions",
        "implementations": (
            "color_grade.resolve",
            "reel.build",
        ),
    },
    {
        "component": "ending",
        "produces": "where the reel ends and what draws over its tail",
        "implementations": (
            "reel.build",
        ),
    },
)

#: Every component, for callers that address one directly.
COMPONENT_NAMES = tuple(row["component"] for row in REEL_COMPONENTS)


#: Downstream invalidation: a dirty component breaks the assumptions
#: of the components listed beside it, so they are dirty too. Captions,
#: lower thirds and overlays are PLACED against the footage ranges -
#: a footage change keeps their rendered bytes (the artefacts are
#: still reusable) but their placements no longer prove anything.
COMPONENT_DOWNSTREAM: dict[str, tuple[str, ...]] = {
    "footage": ("captions", "lower_thirds", "overlays"),
    "audio": (),
    "captions": (),
    "lower_thirds": (),
    "overlays": (),
    "cards": (),
    "grade": (),
    "ending": ("cards",),
}


#: VC-15 change kinds, and the components each reaches. Separate from
#: `DEPENDENTS` above - the edge walker and its tests stand - because
#: these answer per-COMPONENT reuse, not per-edge dirt. A kind not
#: listed here is unprovable and invalidates the whole reel.
COMPONENT_CHANGE_REACH: dict[str, tuple[str, ...]] = {
    # One reel's declaration invalidates only that reel (the caller
    # scopes by reel; `selective_invalidation` answers per reel).
    # An optional `components` list on the change narrows further.
    "reel_declaration": ("footage", "audio", "captions", "lower_thirds",
                         "overlays", "cards", "grade", "ending"),
    # A lower-third asset reaches only its lower thirds: no footage,
    # no audio, no captions are re-rendered or re-placed.
    "lower_third_asset": ("lower_thirds",),
    "caption_content": ("captions",),
    # Timing is fixed without re-rendering: the frame mappings are
    # recomputed and the rendered bytes are reused.
    "caption_timing": ("captions",),
    "placement_timing": ("footage",),
    "grade_decision": ("grade",),
    "ending_declaration": ("ending",),
    "overlay_plan": ("overlays",),
    "audio_decision": ("audio",),
    "card_asset": ("cards",),
}


def reel_components() -> tuple[str, ...]:
    """Every reel component VC-15 can invalidate independently."""
    return COMPONENT_NAMES


def component_implementations(component: str) -> Optional[tuple[str, ...]]:
    """The producing operation ids of one component, or None.

    None for an unknown component - an unlisted component is never a
    provable one, and the caller fails closed on it.
    """
    for row in REEL_COMPONENTS:
        if row["component"] == component:
            return tuple(row["implementations"])
    return None


def component_proof(component: str, repo_root=None) -> dict:
    """The implementation-version proof for one component.

    `{"component", "versions", "unproven", "complete", "detail"}` from
    `code_identity.implementation_proof` over the component's
    producing operations. Incomplete exactly when a producing
    implementation cannot be versioned - renamed operation, missing
    file - and an incomplete proof rebuilds the component.
    """
    from library.tools import code_identity as _identity

    implementations = component_implementations(component)
    if implementations is None:
        return {"component": component, "versions": {},
                "unproven": [component], "complete": False,
                "detail": (f"unknown reel component {component!r} - an "
                           f"unlisted component is never a provable one "
                           f"and it rebuilds")}
    proof = _identity.implementation_proof(implementations, repo_root)
    proof["component"] = component
    return proof


def prove_component_reuse(component: str,
                           recorded_versions: Optional[Mapping[str, Any]],
                           fresh_versions: Optional[Mapping[str, Any]]) -> tuple[bool, str]:
    """Whether one component's artefacts may be reused, and why.

    Reuse needs a positive proof: both halves present, every producing
    implementation versioned on both sides, and every version equal.
    Every other outcome refuses with its reason - including a recorded
    half that predates versioning, which rebuilds rather than reading
    as unchanged.
    """
    implementations = component_implementations(component)
    if implementations is None:
        return False, (
            f"{component}: unknown reel component - an unlisted "
            f"component is never reused")
    if not isinstance(recorded_versions, Mapping) or not recorded_versions:
        return False, (
            f"{component}: no recorded implementation versions - "
            f"nothing to compare, so it regenerates")
    if not isinstance(fresh_versions, Mapping) or not fresh_versions:
        return False, (
            f"{component}: the fresh implementation versions could not "
            f"be established - an unknown derivation is never a "
            f"matching one")
    for ref in implementations:
        recorded = recorded_versions.get(ref)
        fresh = fresh_versions.get(ref)
        if not recorded:
            return False, (
                f"{component}: no recorded version for producing "
                f"implementation {ref} - the record predates "
                f"versioning, so it regenerates")
        if not fresh:
            return False, (
                f"{component}: no fresh version for producing "
                f"implementation {ref} - an unversionable "
                f"implementation never proves reuse")
        if recorded != fresh:
            return False, (
                f"{component}: producing implementation {ref} changed "
                f"({recorded[:12]} -> {fresh[:12]}) - the component "
                f"regenerates")
    return True, (
        f"{component}: every producing implementation version "
        f"matches the record - its artefacts are reused")


def selective_invalidation(change: Mapping[str, Any],
                           reel: Optional[str] = None) -> dict:
    """VC-15: which components of one reel a change invalidates.

    `change` is `{"kind", "entity_id", ...}` with an optional `reel`
    scope and an optional `components` narrowing. The change itself
    IS the input record: a reached component is dirty because the
    change moved its inputs, and no implementation-version proof
    overrules that - versions prove the CODE did not change, the
    event proves the INPUT did. (`prove_component_reuse` answers the
    other question: whether an UNREACHED component's artefacts may be
    reused across builds.) Returns `{"dirty_components",
    "reused_components", "remapped_components", "whole_reel",
    "explanations", "reason"}` - every component named with its why.

    Fail-closed throughout, and each is said:

    - an unmapped kind, a missing entity id, an unknown narrowing,
      or a change scoped to a different reel than the one asked
      about: the whole reel is dirty (or, for a foreign reel's
      declaration, nothing is);
    - downstream components whose assumptions a dirty component
      breaks are dirty too;
    - timing changes recompute frame mappings (`remapped_components`)
      instead of re-rendering: the rendered bytes are reused.
    """
    explanations: list[str] = []
    if not isinstance(change, Mapping):
        return {"dirty_components": list(COMPONENT_NAMES),
                "reused_components": [], "remapped_components": [],
                "whole_reel": True,
                "explanations": ["the change is not a mapping - an "
                                 "unknown change dirties the whole reel"],
                "reason": "the change is not a mapping - an unknown "
                          "change dirties the whole reel"}
    kind = change.get("kind", change.get("entity_kind"))
    entity_id = change.get("entity_id")
    if kind not in COMPONENT_CHANGE_REACH:
        return {"dirty_components": list(COMPONENT_NAMES),
                "reused_components": [], "remapped_components": [],
                "whole_reel": True,
                "explanations": [f"unknown change kind {kind!r} - an "
                                 f"unknown op dirties the whole reel"],
                "reason": f"unknown change kind {kind!r} - an unknown "
                          f"op dirties the whole reel"}
    if not entity_id:
        return {"dirty_components": list(COMPONENT_NAMES),
                "reused_components": [], "remapped_components": [],
                "whole_reel": True,
                "explanations": ["the change names no entity id - an "
                                 "unresolvable item dirties the whole reel"],
                "reason": "the change names no entity id - an "
                          "unresolvable item dirties the whole reel"}
    change_reel = change.get("reel")
    if change_reel is not None and reel is not None and change_reel != reel:
        explanations.append(
            f"{kind} {entity_id} is scoped to {change_reel} - "
            f"{reel} keeps every component")
        return {"dirty_components": [], "reused_components": list(
                    COMPONENT_NAMES), "remapped_components": [],
                "whole_reel": False, "explanations": explanations,
                "reason": (f"{kind} {entity_id} is scoped to "
                           f"{change_reel} - {reel} keeps every component")}
    reached = list(COMPONENT_CHANGE_REACH[kind])
    narrowed = change.get("components")
    if narrowed:
        known_narrowed = [c for c in narrowed if c in COMPONENT_NAMES]
        unknown_narrowed = [c for c in narrowed
                            if c not in COMPONENT_NAMES]
        if unknown_narrowed:
            return {"dirty_components": list(COMPONENT_NAMES),
                    "reused_components": [], "remapped_components": [],
                    "whole_reel": True,
                    "explanations": [
                        f"the change narrows to unknown component(s) "
                        f"{', '.join(unknown_narrowed)} - an unprovable "
                        f"narrowing dirties the whole reel"],
                    "reason": "the change narrows to unknown "
                              "component(s) - an unprovable narrowing "
                              "dirties the whole reel"}
        reached = [c for c in reached if c in known_narrowed]
    dirty: list[str] = []
    reused: list[str] = []
    remapped: list[str] = []
    for component in COMPONENT_NAMES:
        if component not in reached:
            reused.append(component)
            explanations.append(
                f"{component}: not reached by {kind} {entity_id} - "
                f"its artefacts are reused")
            continue
        # Reached is dirty: the change moved this component's inputs,
        # and an implementation-version proof cannot overrule the
        # event - versions prove the code, the event proves the input.
        dirty.append(component)
        explanations.append(
            f"{component}: reached by {kind} {entity_id} - its inputs "
            f"moved, so it regenerates")
    # Downstream invalidation: a dirty component breaks the assumptions
    # of its dependents, so they are dirty too - said, not silent.
    for component in list(dirty):
        for dependent in COMPONENT_DOWNSTREAM.get(component, ()):
            if dependent in reused:
                reused.remove(dependent)
                dirty.append(dependent)
                explanations.append(
                    f"{dependent}: {component} changed, and {dependent} "
                    f"is placed against it - its assumptions broke, so "
                    f"it regenerates")
    # Timing changes recompute frame mappings instead of re-rendering:
    # the rendered bytes are reused, the reel positions are not.
    if kind in ("caption_timing", "placement_timing"):
        for component in ("captions", "overlays", "lower_thirds"):
            if component in dirty:
                dirty.remove(component)
                remapped.append(component)
                explanations.append(
                    f"{component}: timing moved but its rendered bytes "
                    f"are unchanged - frame mappings recomputed, no "
                    f"re-render")
    reason = (f"{kind} {entity_id}: {len(dirty)} component(s) dirty "
              f"({', '.join(dirty) if dirty else 'none'}), "
              f"{len(reused)} reused, {len(remapped)} remapped")
    # The main path is always targeted, never whole-reel: unreached
    # components are clean by the reach table's own claim, and every
    # unprovable shape (unknown kind, missing entity, unknown
    # narrowing) returned whole-reel above. Whole-reel fallback lives
    # in those early returns, not here.
    return {"dirty_components": dirty, "reused_components": reused,
            "remapped_components": remapped, "whole_reel": False,
            "explanations": explanations, "reason": reason}


__all__ = [
    "CHANGE_KINDS",
    "COMPONENT_CHANGE_REACH",
    "COMPONENT_DOWNSTREAM",
    "COMPONENT_NAMES",
    "DEPENDENTS",
    "EDGES",
    "EDGE_NAMES",
    "REGISTRY_DIRNAME",
    "REGISTRY_FORMAT",
    "REEL_COMPONENTS",
    "caption_reuse_allowed",
    "component_implementations",
    "component_proof",
    "edge_of",
    "gate_rebuild_decision",
    "hearing_drift_in_scope",
    "mark_dirty",
    "overlay_reuse_allowed",
    "planning_reuse_allowed",
    "prove_component_reuse",
    "read_registry",
    "reel_components",
    "registry_dir",
    "registry_path",
    "scoped_verify",
    "scoped_verify_partial_build",
    "selective_invalidation",
    "skip_allowed",
    "update_registry_from_build",
    "write_registry",
]
