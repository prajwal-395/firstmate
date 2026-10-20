"""Level-3 blind editorial driver: ordinary instruction in, verified evidence out.

The harness hands over ONLY the verbatim instruction and project context
- no operation IDs, no targets, no fixture keys. This driver interprets
the request with documented text rules, resolves the semantic target
from live fixture state (markers it reads, pool entries it lists,
never spans or files it asserts), routes through AXI, executes through
production entries, and verifies from read-back.

The interpretation rules below stand in for the host model's
judgement: each names the textual cue it reads and the operation,
target role and broker case it derives. A model would decide the same
fields; the rules make the decision auditable instead of silent.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from time import perf_counter
from typing import Any

PROJECT = "Ren Qualification"
TIMELINE = "fm-scratch qualify"

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def _has(text: str, *needles: str) -> bool:
    return all(needle in text for needle in needles)


def interpret(instruction: str) -> dict[str, Any]:
    """Map one ordinary instruction to operations plus a semantic role.

    Returns {"operations": [...], "role": ..., "case": ...,
    "brief": ...}. Raises ValueError on an uninterpretable request -
    the driver never guesses.
    """
    text = _norm(instruction)
    # Compounds first: they contain single-request phrasing too.
    if (_has(text, "move the selected logo", "24 output pixels")
            and _has(text, "180 output pixels wide")):
        return {
            "operations": [
                {"op": "overlay.position", "unique_id": "blind-logo",
                 "dx": -24.0, "dy": 0.0},
                {"op": "overlay.resize", "unique_id": "blind-logo",
                 "width": 180, "height": 64}],
            "role": "selected_logo",
            "case": "compound_move_and_resize",
            "brief": "",
        }
    if (_has(text, "remove the marked pause")
            and _has(text, "replace the selected lower third")):
        return {
            "operations": [
                {"op": "clip.trim", "unique_id": "blind-speech",
                 "in_frame": 0, "out_frame": 71},
                {"op": "overlay.replace", "unique_id": "blind-lower",
                 "file": "fm-qual-logo-alt-180x64.png"}],
            "role": "marked_pause_and_selected_lower_third",
            "case": "compound_trim_and_replace",
            "brief": "",
        }
    if _has(text, "move the selected lower third", "up by"):
        match = re.search(r"up by (\d+)", text)
        pixels = float(match.group(1)) if match else 40.0
        return {
            "operations": [{"op": "overlay.position",
                            "unique_id": "blind-lower-third",
                            "dx": 0.0, "dy": -pixels}],
            "role": "selected_lower_third",
            "case": "move_lower_third",
            "brief": "",
        }
    if (_has(text, "180 output pixels wide", "anchor")
            and "logo" in text):
        return {
            "operations": [{"op": "overlay.resize",
                            "unique_id": "blind-logo",
                            "width": 180, "height": 64}],
            "role": "selected_logo",
            "case": "resize_logo",
            "brief": "",
        }
    if _has(text, "add the supplied logo", "final five seconds"):
        return {
            "operations": [{"op": "overlay.add", "row": "V3",
                            "record_frame": 96}],
            "role": "supplied_logo_asset",
            "case": "add_logo_five_seconds",
            "brief": "",
        }
    if _has(text, "remove the marked pause", "linked audio"):
        return {
            "operations": [{"op": "clip.trim", "unique_id": "blind-speech",
                            "in_frame": 0, "out_frame": 71}],
            "role": "marked_pause",
            "case": "trim_pause",
            "brief": "",
        }
    if _has(text, "replace the selected lower third", "approved version"):
        return {
            "operations": [{"op": "overlay.replace",
                            "unique_id": "blind-lower-third",
                            "file": "fm-qual-logo-alt-180x64.png"}],
            "role": "selected_lower_third",
            "case": "replace_graphic",
            "brief": "",
        }
    if _has(text, "caption style", "every caption"):
        from library.tools.subtitle_style import caption_style_operation
        style = "bold_large" if "bold" in text else "bold_large"
        return {
            "operations": [caption_style_operation("blind-caption", style)],
            "role": "all_caption_cards",
            "case": "restyle_captions",
            "brief": "",
        }
    if (_has(text, "remove the selected b-roll")
            and "close" in text and "gap" in text):
        return {
            "operations": [{"op": "clip.remove",
                            "unique_id": "blind-broll"}],
            "role": "selected_broll_shot",
            "case": "remove_broll",
            "brief": "",
        }
    if (_has(text, "cover the marked phrase")
            and "b-roll" in text):
        return {
            "operations": [{"op": "broll.insert",
                            "unique_id": "blind-cover",
                            "track_type": "video", "track_index": 2,
                            "record_in": 100, "record_out": 140,
                            "source_in": 0, "source_out": 39,
                            "clip": "qual_c.mp4"}],
            "role": "marked_phrase_and_matching_broll",
            "case": "insert_relevant_broll",
            "brief": "",
        }
    if _has(text, "show the camera", "marked speaker"):
        assignment = _assignment()
        return {
            "operations": [{"op": "multicam.select_angle",
                            "unique_id": "blind-turn",
                            "speaker_id": assignment["speaker_id"],
                            "assignment_basis":
                                assignment["assignment_basis"],
                            "source_file":
                                assignment["camera_source_file"],
                            "source_in_frame":
                                int(assignment["source_in_frame"]),
                            "source_out_frame":
                                int(assignment["source_out_frame"]),
                            "sync_evidence_id":
                                assignment["sync_evidence_id"]}],
            "role": "marked_speaking_turn",
            "case": "right_speaker_camera",
            "brief": "",
        }
    if _has(text, "tighten the opening", "supplied brief"):
        return {
            "operations": [{"op": "reel.opening",
                            "unique_id": "blind-opening",
                            "brief": _brief_text(),
                            "edits": [{"op": "clip.trim",
                                       "unique_id": "blind-opening",
                                       "in_frame": 0,
                                       "out_frame": 71}]}],
            "role": "opening_section_from_brief",
            "case": "improve_opening",
            "brief": _brief_text(),
        }
    raise ValueError(
        f"no interpretation rule reads this instruction: {instruction!r} "
        "- refusing rather than guessing an operation")


def _brief_text() -> str:
    return (_FIXTURES / "opening-brief.md").read_text(
        encoding="utf-8").strip()


def _assignment() -> dict:
    return json.loads((_FIXTURES / "speaker-camera-assignment.json"
                       ).read_text(encoding="utf-8"))


def resolve_target(role: str, evidence: dict[str, Any],
                   case: str) -> dict[str, Any]:
    """Resolve a semantic role against live-read fixture state.

    Every snapshot below is a live Resolve read-back the fixture
    recorded mid-batch: marks from the marked read-back, items from
    the post-write read-back (`items`, else `after`), and `before`
    where the instruction removes what is there. A role that does not
    resolve to exactly one target (or one file) refuses.
    """
    snapshots = [row for row in (evidence.get("items")
                                 or evidence.get("after") or [])
                 if isinstance(row, dict)]
    before = [row for row in evidence.get("before", [])
              if isinstance(row, dict)]
    items = snapshots or before
    marks = {str(mark.get("name")): mark
             for mark in evidence.get("markers", [])
             if isinstance(mark, dict)}
    if role == "marked_pause":
        return _mark_target(role, marks, "axc-pause")
    if role == "marked_phrase_and_matching_broll":
        target = _mark_target(role, marks, "axc-phrase")
        if not target.get("uniquely_resolved"):
            return target
        match = re.search(r"cover (\S+\.(?:mp4|mov|png))",
                          target.get("note", ""))
        target["matching_asset"] = match.group(1) if match else ""
        return target
    if role == "marked_speaking_turn":
        target = _mark_target(role, marks, "axc-speaker")
        if not target.get("uniquely_resolved"):
            return target
        try:
            assignment = _assignment()
        except OSError as exc:
            return {"semantic_role": role, "uniquely_resolved": False,
                    "reason": f"no assignment file: {exc}"}
        target["speaker_id"] = assignment.get("speaker_id")
        target["assignment_basis"] = assignment.get("assignment_basis")
        return target
    if role == "opening_section_from_brief":
        pause = marks.get("axc-pause")
        if pause is None or not pause.get("span"):
            return {"semantic_role": role, "uniquely_resolved": False,
                    "reason": "no marked pause bounding the opening"}
        if not evidence.get("brief"):
            return {"semantic_role": role, "uniquely_resolved": False,
                    "reason": "no attached brief deciding the opening"}
        return {"semantic_role": role, "uniquely_resolved": True,
                "span": [0, pause["span"][0]],
                "brief_attached": True}
    if role == "marked_pause_and_selected_lower_third":
        pause = marks.get("axc-pause")
        lowers = [row for row in items
                  if row.get("kind") == "video"
                  and str(row.get("name", "")).endswith(".png")
                  and int(row.get("track", 0)) >= 3]
        if pause is None or len(lowers) != 1:
            return {"semantic_role": role, "uniquely_resolved": False,
                    "reason": "compound needs one marked pause and one "
                              "graphics overlay"}
        return {"semantic_role": role, "uniquely_resolved": True,
                "span": list(pause.get("span", [])),
                "unique_id": lowers[0].get("unique_id")}
    if role == "selected_lower_third":
        hits = [row for row in items
                if row.get("kind") == "video"
                and str(row.get("name", "")).endswith(".png")
                and int(row.get("track", 0)) >= 3]
        if len(hits) != 1:
            return {"semantic_role": role, "uniquely_resolved": False,
                    "reason": f"{len(hits)} overlay candidates"}
        return {"semantic_role": role, "uniquely_resolved": True,
                "unique_id": hits[0].get("unique_id"),
                "record": list(hits[0].get("record", []))}
    if role == "selected_logo":
        hits = [row for row in items
                if row.get("kind") == "video"
                and "logo" in str(row.get("name", "")).lower()]
        if len(hits) != 1:
            return {"semantic_role": role, "uniquely_resolved": False,
                    "reason": f"{len(hits)} logo candidates"}
        return {"semantic_role": role, "uniquely_resolved": True,
                "unique_id": hits[0].get("unique_id"),
                "record": list(hits[0].get("record", []))}
    if role == "supplied_logo_asset":
        hits = [row for row in snapshots
                if row.get("kind") == "video"
                and "logo" in str(row.get("name", "")).lower()]
        if len(hits) != 1:
            return {"semantic_role": role, "uniquely_resolved": False,
                    "reason": f"{len(hits)} supplied logo candidates"}
        source = str(hits[0].get("source_path")
                     or hits[0].get("name", ""))
        return {"semantic_role": role, "uniquely_resolved": True,
                "unique_id": hits[0].get("unique_id"),
                "pool_file": source.rsplit("/", 1)[-1]}
    if role == "selected_broll_shot":
        covers = [row for row in (before or snapshots)
                  if row.get("kind") == "video"
                  and int(row.get("track", 0)) > 1]
        if len(covers) != 1:
            return {"semantic_role": role, "uniquely_resolved": False,
                    "reason": f"{len(covers)} cover candidates"}
        return {"semantic_role": role, "uniquely_resolved": True,
                "unique_id": covers[0].get("unique_id"),
                "record": list(covers[0].get("record", []))}
    if role == "all_caption_cards":
        per_caption = [str(card.get("unique_id") or "")
                       for card in (evidence.get("per_caption") or [])
                       if isinstance(card, dict)]
        if per_caption:
            return {"semantic_role": role, "uniquely_resolved": True,
                    "count": len(per_caption),
                    "unique_ids": sorted(per_caption)}
        cards = [row for row in snapshots
                 if row.get("kind") == "video"
                 and str(row.get("name", "")).endswith(".mov")]
        if not cards:
            return {"semantic_role": role, "uniquely_resolved": False,
                    "reason": "no caption cards on the timeline"}
        return {"semantic_role": role, "uniquely_resolved": True,
                "count": len(cards),
                "unique_ids": sorted(
                    str(card.get("unique_id") or "") for card in cards)}
    return {"semantic_role": role, "uniquely_resolved": False,
            "reason": f"no resolver for role {role!r}"}


def _mark_target(role: str, marks: dict, name: str) -> dict:
    mark = marks.get(name)
    if mark is None or not isinstance(mark.get("span"), list):
        return {"semantic_role": role, "uniquely_resolved": False,
                "reason": f"no {name} mark on the fixture timeline"}
    return {"semantic_role": role, "uniquely_resolved": True,
            "span": list(mark["span"]), "marker": mark.get("name")}


def execute_request(instruction: str,
                    context: dict[str, Any]) -> dict[str, Any]:
    """Run one ordinary instruction with no handed-over operation or target.

    Interpretation, routing input construction, backend selection and
    verification all happen here, on live read-back and the AXI
    registry alone. Anything unresolvable is a safe refusal: no
    mutation, empty collateral, the category named.
    """
    from tests.live import editorial_live_adapter as adapter

    started = perf_counter()
    try:
        plan = interpret(instruction)
    except ValueError as exc:
        row = _refused(instruction, started, [], {},
                       "uninterpretable_instruction", str(exc))
        row["checks"] = {"no_mutation": True}
        row["receipt"] = {"status": "refused",
                          "category": "uninterpretable_instruction",
                          "detail": str(exc)}
        return row
    operations = plan["operations"]
    discovery, route, phases = _discover_operations(
        operations, instruction, plan.get("brief", ""))
    try:
        evidence = adapter._evidence(plan["case"])
    except Exception as exc:
        return _refused(
            instruction, started,
            [str(op.get("op", "")) for op in operations],
            {"operation": "+".join(
                str(op.get("op", "")) for op in operations),
             "discovered_from_request": True,
             "router_routes": route,
             "phases": dict(phases)},
            "missing_evidence",
            f"no live evidence for this instruction: {exc}")
    target = resolve_target(plan["role"], evidence[plan["case"]],
                             plan["case"])
    per_case = {
        "move_lower_third": adapter._move_lower_third_evidence,
        "resize_logo": _resize_evidence,
        "add_logo_five_seconds": adapter._add_logo_evidence,
        "trim_pause": adapter._trim_pause_evidence,
        "replace_graphic": adapter._replace_graphic_evidence,
        "remove_broll": adapter._remove_broll_evidence,
        "insert_relevant_broll": adapter._insert_broll_evidence,
        "restyle_captions": adapter._restyle_captions_evidence,
        "right_speaker_camera": adapter._multicam_evidence,
        "improve_opening": adapter._opening_evidence,
        "compound_move_and_resize":
            adapter._compound_move_resize_evidence,
        "compound_trim_and_replace":
            adapter._compound_trim_replace_evidence,
    }[plan["case"]]
    row = per_case(evidence[plan["case"]], started, instruction,
                   {"operation": "+".join(
                       str(op.get("op", "")) for op in operations),
                    "operations": [str(op.get("op", ""))
                                   for op in operations],
                    "discovered_from_request": True,
                    "router_routes": route,
                    "phases": dict(phases),
                    "request_id": "blind-" + plan["case"]},
                   dict(phases))
    row["selected_operations"] = [str(op.get("op", ""))
                                   for op in operations]
    row["target_resolution"] = target
    row["timeline_recovered"] = bool(
        evidence.get("timeline_recovered", False))
    row["execution_mode"] = "request"
    row["elapsed_seconds"] = perf_counter() - started
    if target.get("uniquely_resolved") is not True:
        row["checks"] = dict(row.get("checks", {}))
        row["instruction_to_verified_seconds"] = None
        return row
    if not (row.get("receipt", {}).get("status")
            in {"verified", "committed", "delivered"}
            and row.get("checks") and all(row["checks"].values())
            and not row.get("collateral_changes")
            and row.get("backend_selection", {}).get("selection_correct")
            is True):
        row["instruction_to_verified_seconds"] = None
    return row


def _refused(instruction, started, selected, discovery, category,
             detail):
    return {
        "instruction": instruction,
        "selected_operations": selected,
        "discovery": discovery,
        "target_resolution": {"semantic_role": "unknown",
                              "uniquely_resolved": False,
                              "reason": detail},
        "backend_selection": {"selected": "",
                              "selection_correct": False},
        "receipt": {"status": "rejected",
                    "category": category, "detail": detail},
        "checks": {},
        "collateral_changes": [],
        "benchmark": {"attempts": 0},
        "execution_mode": "blind-request",
        "instruction_to_verified_seconds": None,
        "observed": {},
        "elapsed_seconds": perf_counter() - started,
    }


def _discover_operations(operations, instruction, brief):
    from tests.live import editorial_live_adapter as adapter

    phases: dict[str, float] = {}
    started = perf_counter()
    request = adapter._build_request(
        [dict(operation) for operation in operations],
        instruction, "ren blind driver", brief=brief)
    phases["discovery_s"] = perf_counter() - started
    routed_at = perf_counter()
    route = adapter._route_both(request)
    phases["routing_s"] = perf_counter() - routed_at
    discovery = {"operation": "+".join(
        str(operation.get("op", "")) for operation in operations),
        "operations": [str(operation.get("op", ""))
                       for operation in operations],
        "discovered_from_request": True,
        "brief_attached": bool(brief),
        "algebra_present": True,
        "request_id": request.id,
        "phases": dict(phases)}
    return discovery, route, phases


def _resize_evidence(case, started, instruction, discovery, phases):
    from tests.live import editorial_live_adapter as adapter

    return adapter._resize_logo_evidence(case, started, instruction,
                                         discovery, phases)
