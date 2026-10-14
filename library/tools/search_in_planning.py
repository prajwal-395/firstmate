"""Footage search inside planning, through the capability's own interface.

Planning receives routed catalog, semantic-analysis, spine, assignment and
creative-direction context; B-roll selection picks from those inputs. This
module wires the footage search in beside that catalog-driven path: when a
plan or B-roll slot needs footage, the caller queries the index with the
slot's intent and takes only candidates with real evidence.

Backend-agnostic: every query goes through
`FootageIndex.search_report` with the index's own defaults, so a later
default-backend change needs no planning change. The module names no
encoder, no floor and no backend string.

The evidence rules, in order:

1. A visual slot is filled only from visual evidence kinds (`action`,
   `scene`, `camera`, `object`, `ocr`). A transcript hit (`speech`,
   `chunk`) is never visual evidence, even when it ranks first.
2. `not_analysed` is respected: a visual slot on an index with no visual
   coverage is REFUSED, never filled from transcript rows.
3. `degraded` falls back: an index with no dense embeddings answers
   keyword-only, which cannot tell "absent" from "phrased differently",
   so the caller keeps today's catalog-driven path and says so.
4. Every candidate span is verified against the catalog before it can be
   placed: inside the file, non-empty, or the next candidate is tried.
5. Every filled slot records which search result and evidence chose it
   (`segment_id`, kind, scores, span), beside the decision.

A missing index is the same fallback as a degraded one: the catalog path
runs and the step output says which path each slot took. Nothing here
raises for a missing or degraded index; programmer errors (no intent,
no catalog entry to verify against) still raise.

This module imports nothing from steps or processes: it reads state
through the same readers every consumer has, the way `picture_plan`
does.
"""

from __future__ import annotations

import sys

from library.tools.analysis.footage_query import FootageIndex
from library.tools.ren_refusal import RenRefusal

# Segment kinds that count as seeing the picture. `speech` and `chunk`
# are transcript rows: they say what was SAID near a span, never what
# the picture shows. A visual slot filled from one is a narration match
# wearing a picture's clothes.
VISUAL_EVIDENCE_KINDS = frozenset({"action", "scene", "camera", "object", "ocr"})

# A span may run this far past the catalog duration before it is not the
# file's span: the same tolerance `assign_aroll` grants a source range.
SPAN_TOLERANCE_SECONDS = 0.5


def verify_span(clip_id: str, start: float, end: float, catalog: list) -> dict:
    """The catalog's verdict on a search hit's span, or why it fails.

    Returns ``{"ok": True}`` when the span lies inside the clip's file,
    else ``{"ok": False, "reason": ...}``. A span nothing can place is
    refused here, where the evidence is in hand, not at compile time.
    """
    entry = next(
        (c for c in catalog if isinstance(c, dict) and c.get("clip_id") == clip_id),
        None,
    )
    if entry is None:
        return {"ok": False, "reason": f"{clip_id} is not in the clip catalog"}
    try:
        start_f, end_f = float(start), float(end)
    except (TypeError, ValueError):
        return {
            "ok": False,
            "reason": f"{clip_id} span {start!r}-{end!r} is not numeric",
        }
    if not end_f > start_f:
        return {
            "ok": False,
            "reason": f"{clip_id} span {start_f:.3f}-{end_f:.3f}s is empty",
        }
    if start_f < -1e-9:
        return {
            "ok": False,
            "reason": f"{clip_id} span starts at {start_f:.3f}s, before the file",
        }
    duration = float(entry.get("duration_seconds") or 0.0)
    if duration and end_f > duration + SPAN_TOLERANCE_SECONDS:
        return {
            "ok": False,
            "reason": f"{clip_id} span ends at {end_f:.3f}s, past the "
            f"file's {duration:.3f}s",
        }
    return {"ok": True}


def _is_visual_hit(hit: dict) -> bool:
    """Whether a search hit carries visual evidence."""
    if not isinstance(hit, dict) or hit.get("error"):
        return False
    if hit.get("visual_evidence") is True:
        return True
    return hit.get("kind") in VISUAL_EVIDENCE_KINDS


def slot_search(
    project_folder: str,
    intent: str,
    *,
    slot_kind: str = "visual",
    top_k: int = 5,
    mode: str = "hybrid",
    index_dir=None,
    catalog: list | None = None,
    filters: dict | None = None,
) -> dict:
    """Resolve one planning slot through the footage index.

    `intent` is the slot's own words: a block's `visual_note`, a B-roll
    `preferred_moment`, a picture plan's block text. `slot_kind` is
    `"visual"` (the slot needs a picture) or `"general"`.

    Returns a dict with `status`:

    - `filled`: the top verified candidate, with `clip_id`, `span`,
      `evidence` (segment_id, kind, text, scores, backend) and the
      `query` that found it.
    - `refused_not_analysed`: a visual slot on an index with no visual
      coverage. The slot is refused rather than filled from transcript.
    - `fallback_no_index`: nothing built an index; the caller runs the
      catalog path.
    - `fallback_degraded`: the index answers keyword-only; the caller
      runs the catalog path and says so.
    - `abstained_no_evidence`: nothing with real evidence cleared the
      floor (for a visual slot: no visual-evidence hit).
    - `abstained_unverified`: evidence existed but no span verified
      against the catalog.
    """
    if slot_kind not in ("visual", "general"):
        raise ValueError(f"unknown slot_kind {slot_kind!r}: visual or general")
    if not (intent or "").strip():
        raise ValueError("slot_search needs the slot's intent as the query")
    try:
        index = (
            FootageIndex(project_folder, index_dir=index_dir)
            if index_dir is not None
            else FootageIndex(project_folder)
        )
        # Visual planning requests need picture rows. Apply that cheap
        # deterministic constraint inside the index before dense ranking.
        effective_filters = dict(filters or {})
        if slot_kind == "visual":
            effective_filters.setdefault(
                "kinds", sorted(VISUAL_EVIDENCE_KINDS - {"ocr"}))
        report = index.search_report(
            intent, top_k=top_k, mode=mode,
            filters=effective_filters or None,
            before_embedding=lambda counts: print(
                "  Footage retrieval candidates: "
                f"{counts['index_candidates']} -> "
                f"{counts['constraint_candidates']} by constraints; "
                f"{counts['embedding_candidates']} to embedding",
                file=sys.stderr))
    except (RenRefusal, FileNotFoundError, OSError) as exc:
        return {"status": "fallback_no_index", "query": intent, "reason": str(exc)}
    except Exception as exc:  # noqa: BLE001 - best-effort assist
        return {
            "status": "fallback_no_index",
            "query": intent,
            "reason": f"index unreadable: {exc}",
        }
    if report.get("answer_status") == "not_analysed":
        return {
            "status": "refused_not_analysed",
            "query": intent,
            "reason": report.get("error") or "index has no visual evidence coverage",
            "query_intent": report.get("query_intent"),
            "retrieval": _retrieval_counts(report),
        }
    if report.get("error") and not report.get("results"):
        return {
            "status": "abstained_no_evidence",
            "query": intent,
            "reason": report.get("error"),
            "degraded": report.get("degraded"),
            "retrieval": _retrieval_counts(report),
        }
    candidates = report.get("results") or []
    if slot_kind == "visual":
        # Transcript hits never fill a visual slot, even ranked first.
        candidates = [h for h in candidates if _is_visual_hit(h)]
        if not candidates:
            return {
                "status": "abstained_no_evidence",
                "query": intent,
                "reason": "no visual-evidence hit cleared the floor; "
                "transcript hits are not picture evidence",
                "degraded": report.get("degraded"),
                "backend_answered": report.get("backend_answered"),
                "retrieval": _retrieval_counts(report),
            }
    degraded = report.get("degraded")
    if degraded and slot_kind == "visual":
        # Keyword-only cannot tell "absent" from "phrased differently":
        # a visual slot falls back to the catalog path saying so.
        return {
            "status": "fallback_degraded",
            "query": intent,
            "reason": degraded,
            "backend_answered": report.get("backend_answered"),
            "retrieval": _retrieval_counts(report),
        }
    catalog = catalog or []
    for hit in candidates:
        verdict = verify_span(
            hit.get("clip_id"), hit.get("start"), hit.get("end"), catalog
        )
        if not verdict["ok"]:
            continue
        return {
            "status": "filled",
            "query": intent,
            "clip_id": hit.get("clip_id"),
            "span": {
                "start": hit.get("start"),
                "end": hit.get("end"),
                "timecode": hit.get("timecode"),
            },
            "evidence": {
                "segment_id": hit.get("segment_id"),
                "kind": hit.get("kind"),
                "text": (hit.get("text") or "")[:300],
                "score": hit.get("score"),
                "dense_score": hit.get("dense_score"),
                "lexical_score": hit.get("lexical_score"),
                "backend_answered": report.get("backend_answered"),
            },
            "degraded": degraded,
            "retrieval": _retrieval_counts(report),
        }
    if not candidates:
        return {
            "status": "abstained_no_evidence",
            "query": intent,
            "reason": "the index retained nothing for this intent",
            "degraded": degraded,
            "backend_answered": report.get("backend_answered"),
            "retrieval": _retrieval_counts(report),
        }
    return {
        "status": "abstained_unverified",
        "query": intent,
        "reason": "evidence existed but no span verified against the catalog",
        "degraded": degraded,
        "retrieval": _retrieval_counts(report),
    }


def _retrieval_counts(report: dict) -> dict:
    """Auditable deterministic reduction before embedding retrieval."""
    return {
        "index_candidates": report["segment_count"],
        "constraint_candidates": report["constraint_candidates"],
        "constraint_reduction": report["constraint_reduction"],
        "embedding_candidates": report["embedding_candidates"],
    }


def search_hints_for_blocks(
    project_folder: str, blocks: list, catalog: list, *, top_k: int = 3, index_dir=None
) -> dict:
    """A best-effort search hint per block position, keyed by position.

    Each block is queried with its `visual_note` (falling back to its
    text), as a visual slot. A block whose query fills names the clip
    and evidence the index vouches for; any other status travels with
    it so the caller can say which path each slot took. Never raises:
    a block with no intent carries `skipped_no_intent`, and an
    unreadable index marks every block `fallback_no_index`.
    """
    hints: dict = {}
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        position = str(block.get("position", block.get("spine_block_position", "?")))
        intent = (
            block.get("visual_note")
            or block.get("text")
            or (block.get("content") or {}).get("text")
            or ""
        )
        if not str(intent).strip():
            hints[position] = {"status": "skipped_no_intent"}
            continue
        hints[position] = slot_search(
            project_folder,
            str(intent),
            slot_kind="visual",
            top_k=top_k,
            catalog=catalog,
            index_dir=index_dir,
        )
    return hints


def format_hints_block(hints: dict) -> str:
    """The hints as prompt prose: one line per block, fallback said aloud."""
    if not hints:
        return (
            "Footage search: no hints - the index was not queried for "
            "these blocks; select from the candidate table."
        )
    lines = []
    for position in sorted(hints, key=str):
        hint = hints[position]
        status = hint.get("status")
        if status == "filled":
            span = hint.get("span") or {}
            lines.append(
                f"block {position}: search vouches for {hint.get('clip_id')} "
                f"({span.get('start')}-{span.get('end')}s, "
                f"{(hint.get('evidence') or {}).get('kind')}) - "
                f'"{(hint.get("evidence") or {}).get("text", "")[:120]}"'
            )
        elif status in ("fallback_no_index", "fallback_degraded"):
            lines.append(f"block {position}: catalog path - {hint.get('reason')}")
        elif status == "refused_not_analysed":
            lines.append(f"block {position}: search refused - {hint.get('reason')}")
        elif status == "skipped_no_intent":
            lines.append(f"block {position}: no slot intent to query with")
        else:
            lines.append(
                f"block {position}: search abstained - {hint.get('reason') or status}"
            )
    return (
        "Footage search hints (backend-agnostic; transcript hits never vouch for picture):\n"
        + "\n".join(f"- {line}" for line in lines)
    )
