"""Resolve swap-back: replace a render binding through its frame maps.

Unit 5, second half (the frame-map math landed in ren-core, PR 2242).
Every native and Ren render carries a receipt plus a piecewise
source/timeline/render frame map tied to its input revision (the
Project Model ``Render``: ``input_revision`` + ``receipt_id`` +
``frame_map_id``). This adapter consumes that lineage: given the prior
render and the new render, it swaps the new file in with ONE atomic
``clip.replace`` patch op, remaps every bound Fusion keyframe and grade
range through the old and new maps, reads back, verifies, and refuses
uncovered or ambiguous segments - through a version-checked EditPatch
under the Resolve lease.

The measured failure it fixes: Resolve's ``ReplaceClip`` kept the
grade and Fusion comp but left keys at 0/205/411 after a shorter
render (the native-engine capability report, "Linked-render handoff").
``ReplaceClip`` alone never moves keys; this adapter does.

Shape
-----
Planning is FREE and pure (no Resolve, no project writes): ``plan`` +
``build_patch`` turn model dicts into an EditPatch dict. The caller
commits it with ``edit_patch.apply_live_patch`` (capability
``reel.touchup``: an in-place timeline change), then calls ``accept``
which re-reads the live item under a read lease and judges the keys,
the grade identity and the comp identity. Refusals:

- plan time (``SwapbackRefused``, nothing written, previous binding
  retained): receipt/revision/clock mismatch, any played frame the new
  map does not cover, any key or range that is uncovered, ambiguous,
  generated, non-invertible or inexact, and key collisions on the new
  clock.
- apply time: the op's own read-back raises inside the patch (the
  generation records what Resolve holds; undoing is a new patch, per
  EditPatch semantics). Restoring the previous binding is the same
  code path with prior and new flipped - ``plan`` is symmetric - and
  is a caller decision, never an automatic rollback.
- accept time (``SwapbackRefused`` with ``readback_mismatch``): the
  patch committed but the live item does not read as planned.

Frame-map math mirrors ``native/ren-core/src/frame_map.rs`` exactly:
rates are output ticks per input tick, integer tick conversion is
required (a rational result between ticks is refused, never rounded),
half-open intervals, and at a shared boundary the segment beginning
there owns the tick. Usability matches too: ``generated``/``gap``
refuse, ``transition_blend`` is ambiguous, ``freeze``/``repeat`` are
ambiguous forward and non-invertible backward.

``tests/unit/resolve/test_resolve_swapback.py``.
"""

from __future__ import annotations

from typing import Any

from library.tools.ren_refusal import RenRefusal

#: The capability a swap-back patch commits through: an in-place
#: timeline change, whose declared patch operations include
#: ``clip.replace`` and whose conflict domains cover the structure,
#: fusion and color writes the swap makes.
SWAPBACK_CAPABILITY = "reel.touchup"

#: Conflict domains one swap-back patch declares. The single
#: ``clip.replace`` op carries the media binding, the remapped Fusion
#: keys and the grade assertion together - splitting them would allow
#: the exact half-swap (new media, stale keys) this adapter exists to
#: stop - so the patch declares every domain the op writes.
SWAPBACK_DOMAINS = ("timeline_structure", "fusion", "color")


class SwapbackRefused(RenRefusal, RuntimeError):
    """A swap-back that cannot proceed safely. Nothing was half-written."""

    CODE = "REN_SWAPBACK_REFUSED"


def _refuse(code: str, what: str, why: str, fix: str) -> SwapbackRefused:
    return SwapbackRefused(what=what, why=why, fix=fix, code=code,
                           recovery_class="blocked")


# ── Frame-map math (mirrors ren-core frame_map.rs) ───────────────────

def _interval_clock(interval: dict) -> str:
    return interval["clock_id"]


def _rate(segment: dict) -> tuple[int, int]:
    """Output ticks per input tick, from the explicit rate or the spans."""
    rate = segment.get("rate")
    if rate is not None:
        numerator, denominator = int(rate["numerator"]), int(rate["denominator"])
        if numerator <= 0 or denominator <= 0:
            raise _refuse(
                "REN_SWAPBACK_INVALID_RATE",
                f"frame map segment has non-positive rate {rate!r}",
                "a rate is output ticks per input tick and must be positive",
                "rebuild the render's frame map with a positive rate")
        return numerator, denominator
    from_len = int(segment["from"]["end"]) - int(segment["from"]["start"])
    to_len = int(segment["to"]["end"]) - int(segment["to"]["start"])
    if from_len <= 0 or to_len <= 0:
        raise _refuse(
            "REN_SWAPBACK_INVALID_RATE",
            f"frame map segment has an empty span {segment!r}",
            "map intervals are half-open [start, end) with start < end",
            "rebuild the render's frame map with non-empty intervals")
    return to_len, from_len


def _affine(value: int, source: dict, target: dict,
            numerator: int, denominator: int) -> int:
    delta = int(value) - int(source["start"])
    product = delta * numerator
    if product % denominator != 0:
        raise _refuse(
            "REN_SWAPBACK_INEXACT_TICK",
            f"frame {value} maps between ticks and cannot land exactly",
            "integer tick conversion is required; a fractional result "
            "is refused rather than rounded",
            "re-render onto the shared tick grid or drop the key from "
            "the binding")
    return int(target["start"]) + product // denominator


def _usable(segment: dict, inverse: bool, where: str) -> None:
    kind = segment.get("mapping_kind")
    if kind in ("generated", "gap"):
        raise _refuse(
            "REN_SWAPBACK_GENERATED",
            f"{where} crosses a {kind} segment",
            "generated and gap ranges have no source anchor, so no key "
            "may move through them",
            "re-render without the generated range or drop the key "
            "from the binding")
    if kind == "transition_blend":
        raise _refuse(
            "REN_SWAPBACK_AMBIGUOUS",
            f"{where} crosses a transition-blend segment",
            "a blend belongs to two sources at once; moving a key "
            "through it would guess which one",
            "drop the key from the binding or split the blend out of "
            "the swapped range")
    if kind in ("freeze", "repeat"):
        if inverse:
            raise _refuse(
                "REN_SWAPBACK_NON_INVERTIBLE",
                f"{where} inverts a {kind} segment",
                "many render frames share one anchor frame, so the way "
                "back is not unique",
                "drop the key from the binding")
        raise _refuse(
            "REN_SWAPBACK_AMBIGUOUS",
            f"{where} crosses a {kind} segment",
            "one anchor frame fills many render frames, so the way "
            "forward is not unique",
            "drop the key from the binding")


def _segments_covering(frame_map: dict, side: str, tick: int) -> list:
    return [segment for segment in frame_map["segments"]
            if int(segment[side]["start"]) <= tick < int(segment[side]["end"])]


def map_tick(frame_map: dict, clock_id: str, tick: int) -> int:
    """One anchor tick onto the render clock, exactly or refused.

    Clocks are honoured PER SEGMENT (MU-21): a reel map over several
    sources carries one segment per clip use, each on its own source
    clock, while ``from_clock`` names only the first. The tick is
    judged against the segments carrying the caller's clock - a tick
    from another source is never mapped against this clock. Mirrors
    ``native/ren-core/src/frame_map.rs`` exactly.
    """
    if not any(segment.get("from", {}).get("clock_id") == clock_id
               for segment in frame_map["segments"]):
        if frame_map["from_clock"] == clock_id:
            raise _refuse(
                "REN_SWAPBACK_UNCOVERED",
                f"anchor frame {tick} is covered by no segment of map "
                f"{frame_map['id']!r}",
                "a key with no mapped anchor has nowhere to land",
                "extend the render's frame map over the played range or "
                "drop the key from the binding")
        raise _refuse(
            "REN_SWAPBACK_CLOCK_MISMATCH",
            f"frame map {frame_map['id']!r} carries no segment on "
            f"{clock_id!r} (its first clock is "
            f"{frame_map['from_clock']!r})",
            "a tick maps only on the source clock that measured it",
            "pass the tick on its own segment clock")
    matches = [segment for segment in frame_map["segments"]
               if segment.get("from", {}).get("clock_id") == clock_id
               and int(segment["from"]["start"]) <= tick < int(segment["from"]["end"])]
    if not matches:
        raise _refuse(
            "REN_SWAPBACK_UNCOVERED",
            f"anchor frame {tick} is covered by no segment of map "
            f"{frame_map['id']!r}",
            "a key with no mapped anchor has nowhere to land",
            "extend the render's frame map over the played range or "
            "drop the key from the binding")
    if len(matches) != 1:
        raise _refuse(
            "REN_SWAPBACK_AMBIGUOUS",
            f"anchor frame {tick} is covered by {len(matches)} segments "
            f"of map {frame_map['id']!r}",
            "overlapping input spans make the mapping a guess",
            "rebuild the render's frame map with disjoint input spans")
    segment = matches[0]
    _usable(segment, False, f"anchor frame {tick}")
    numerator, denominator = _rate(segment)
    return _affine(tick, segment["from"], segment["to"],
                   numerator, denominator)


def inverse_tick(frame_map: dict, clock_id: str, tick: int) -> int:
    """One render tick back onto the anchor clock, exactly or refused."""
    if frame_map["to_clock"] != clock_id:
        raise _refuse(
            "REN_SWAPBACK_CLOCK_MISMATCH",
            f"frame map {frame_map['id']!r} ends on "
            f"{frame_map['to_clock']!r}, not {clock_id!r}",
            "keys are read on the render clock the map was built for",
            "pass the render's own frame map")
    matches = _segments_covering(frame_map, "to", tick)
    if not matches:
        raise _refuse(
            "REN_SWAPBACK_UNCOVERED",
            f"render frame {tick} is covered by no segment of map "
            f"{frame_map['id']!r}",
            "a key outside the mapped render range has no anchor",
            "extend the render's frame map or drop the key from the binding")
    if len(matches) != 1:
        raise _refuse(
            "REN_SWAPBACK_AMBIGUOUS",
            f"render frame {tick} is covered by {len(matches)} segments "
            f"of map {frame_map['id']!r}",
            "overlapping output spans make the way back a guess",
            "rebuild the render's frame map with disjoint output spans")
    segment = matches[0]
    _usable(segment, True, f"render frame {tick}")
    numerator, denominator = _rate(segment)
    return _affine(tick, segment["to"], segment["from"],
                   denominator, numerator)


def translate_keyframe(old_map: dict, new_map: dict, tick: int) -> int:
    """One bound key through the old render back to the anchor and out
    through the new render. Either leg may refuse."""
    if old_map["from_clock"] != new_map["from_clock"]:
        raise _refuse(
            "REN_SWAPBACK_CLOCK_MISMATCH",
            f"maps {old_map['id']!r} and {new_map['id']!r} hang from "
            f"different anchors",
            "a key moves through the anchor both renders share",
            "pass renders from the same project revision")
    anchor = inverse_tick(old_map, old_map["to_clock"], tick)
    return map_tick(new_map, new_map["from_clock"], anchor)


def translate_range(old_map: dict, new_map: dict, span: dict) -> dict:
    """One half-open render range onto the new render clock. A range
    translates only when one exact segment covers the whole range on
    each map; anything straddling a boundary is refused."""
    start, end = int(span["start"]), int(span["end"])
    if start >= end or span.get("clock_id") != old_map["to_clock"]:
        raise _refuse(
            "REN_SWAPBACK_INVALID_RANGE",
            f"range {span!r} is empty or not on the old render clock "
            f"{old_map['to_clock']!r}",
            "a grade range is a non-empty half-open span on the render "
            "being replaced",
            "pass the grade range on the old render's clock")
    if old_map["from_clock"] != new_map["from_clock"]:
        raise _refuse(
            "REN_SWAPBACK_CLOCK_MISMATCH",
            f"maps {old_map['id']!r} and {new_map['id']!r} hang from "
            f"different anchors",
            "a range moves through the anchor both renders share",
            "pass renders from the same project revision")
    old_cover = [segment for segment in old_map["segments"]
                 if int(segment["to"]["start"]) <= start
                 and end <= int(segment["to"]["end"])]
    if len(old_cover) != 1:
        raise _refuse(
            "REN_SWAPBACK_UNCOVERED" if not old_cover else "REN_SWAPBACK_AMBIGUOUS",
            f"range [{start}, {end}) is covered by {len(old_cover)} "
            f"segments of the old map",
            "a range straddling a segment boundary has no single anchor span",
            "split the grade range at the map's segment boundaries")
    old_segment = old_cover[0]
    _usable(old_segment, True, f"range [{start}, {end})")
    old_n, old_d = _rate(old_segment)
    anchor_start = _affine(start, old_segment["to"], old_segment["from"],
                           old_d, old_n)
    anchor_end = _affine(end, old_segment["to"], old_segment["from"],
                          old_d, old_n)
    # The anchor ticks live on the covering segment's source clock, so
    # the new map is searched on that clock only (MU-21).
    new_cover = [segment for segment in new_map["segments"]
                 if segment.get("from", {}).get("clock_id")
                 == old_segment.get("from", {}).get("clock_id")
                 and int(segment["from"]["start"]) <= anchor_start
                 and anchor_end <= int(segment["from"]["end"])]
    if len(new_cover) != 1:
        raise _refuse(
            "REN_SWAPBACK_UNCOVERED" if not new_cover else "REN_SWAPBACK_AMBIGUOUS",
            f"range [{start}, {end}) anchors to [{anchor_start}, "
            f"{anchor_end}), covered by {len(new_cover)} segments of "
            "the new map",
            "the new render does not carry that anchor span whole",
            "re-render covering the played range or narrow the grade range")
    new_segment = new_cover[0]
    _usable(new_segment, False, f"range [{start}, {end})")
    new_n, new_d = _rate(new_segment)
    out_start = _affine(anchor_start, new_segment["from"], new_segment["to"],
                        new_n, new_d)
    out_end = _affine(anchor_end, new_segment["from"], new_segment["to"],
                      new_n, new_d)
    if out_end <= out_start:
        raise _refuse(
            "REN_SWAPBACK_NON_INVERTIBLE",
            f"range [{start}, {end}) collapses on the new render clock",
            "a range that maps to nothing cannot carry a grade",
            "re-render without the collapsing retime")
    return {"clock_id": new_map["to_clock"], "start": out_start,
            "end": out_end}


# ── Render / receipt / revision validation ────────────────────────────

def _lookup(model: dict, collection: str, doc_id: str,
            what: str) -> dict:
    for doc in model.get(collection) or []:
        if doc.get("id") == doc_id:
            return doc
    raise _refuse(
        "REN_SWAPBACK_MISSING_LINK",
        f"{what} {doc_id!r} is not in the project model",
        "a render carries its receipt and frame map by id; a dangling "
        "id is an unprovenanced render",
        f"attach the {what} to the model before swapping to this render")


def resolve_render(model: dict, render_id: str) -> tuple[dict, dict, dict]:
    """A render with its receipt and frame map, revision-tied or refused.

    The receipt must name the render's own input revision and output
    asset - that tie is what makes the map the map OF this render.
    """
    render = _lookup(model, "renders", render_id, "render")
    for key in ("input_revision", "output_asset_id", "receipt_id",
                "frame_map_id"):
        if not render.get(key):
            raise _refuse(
                "REN_SWAPBACK_MISSING_LINK",
                f"render {render_id!r} carries no {key}",
                "every render carries a receipt plus a frame map tied "
                "to its input revision",
                "re-emit the render through its engine adapter with "
                "receipt and frame map attached")
    receipt = _lookup(model, "receipts", render["receipt_id"], "receipt")
    frame_map = _lookup(model, "frame_maps", render["frame_map_id"],
                        "frame map")
    if (receipt.get("input_revision") != render["input_revision"]
            or receipt.get("output_asset_id") != render["output_asset_id"]):
        raise _refuse(
            "REN_SWAPBACK_REVISION_MISMATCH",
            f"receipt {receipt.get('id')!r} is tied to revision "
            f"{receipt.get('input_revision')!r}, not the render's "
            f"{render['input_revision']!r}",
            "a receipt from another revision proves nothing about this file",
            "re-render from the current revision and swap to that render")
    return render, receipt, frame_map


# ── Planning ──────────────────────────────────────────────────────────

def plan(model: dict, *, prior_render_id: str, new_render_id: str,
         unique_id: str, old_source: str, new_source: str,
         played_anchor: dict, fusion_keys: list | None = None,
         grade: dict | None = None) -> dict:
    """The complete swap, planned FREE or refused with nothing written.

    ``played_anchor`` is the played span as a half-open interval on the
    maps' shared anchor clock (``{"clock_id", "start", "end"}``) - the
    caller names the clock, never this adapter, because only the caller
    knows which clock its timeline range was read on. ``fusion_keys``
    are ``{"comp", "tool", "param", "frame", "value"}`` on the OLD
    render clock; ``grade`` is ``{"luts", "cdl", "ranges"}`` with
    ranges ``{"start", "end"}`` on the old render clock.
    """
    prior, _, old_map = resolve_render(model, prior_render_id)
    new, _, new_map = resolve_render(model, new_render_id)
    if old_map["from_clock"] != new_map["from_clock"]:
        raise _refuse(
            "REN_SWAPBACK_CLOCK_MISMATCH",
            f"renders {prior_render_id!r} and {new_render_id!r} hang from "
            f"different anchors",
            "a swap moves keys through the anchor both renders share",
            "swap between renders of the same project revision")
    if (_interval_clock(played_anchor) != old_map["from_clock"]
            or int(played_anchor["start"]) >= int(played_anchor["end"])):
        raise _refuse(
            "REN_SWAPBACK_INVALID_RANGE",
            f"played range {played_anchor!r} is empty or not on the "
            f"anchor clock {old_map['from_clock']!r}",
            "coverage is judged on the anchor the maps share",
            "pass the played span on the maps' from_clock")
    # Every played anchor frame must land on the new render: the output
    # must cover every played frame (AGENTS.md 10.2), and a shorter
    # render that leaves the tail uncovered is refused here - the exact
    # shape of the measured ReplaceClip failure.
    for tick in range(int(played_anchor["start"]),
                      int(played_anchor["end"])):
        map_tick(new_map, new_map["from_clock"], tick)

    moves: list[dict] = []
    for key in fusion_keys or []:
        for field in ("comp", "tool", "param", "frame"):
            if field not in key:
                raise _refuse(
                    "REN_SWAPBACK_MISSING_LINK",
                    f"fusion key {key!r} names no {field}",
                    "a bound key names its comp, tool, param and old-render "
                    "frame",
                    "bind the key fully or drop it from the swap")
        landed = translate_keyframe(old_map, new_map, int(key["frame"]))
        moves.append({"comp": key["comp"], "tool": key["tool"],
                      "param": key["param"],
                      "from_frame": int(key["frame"]), "to_frame": landed,
                      "value": key.get("value")})
    seen: dict[tuple, dict] = {}
    for move in moves:
        slot = (move["comp"], move["tool"], move["param"], move["to_frame"])
        if slot in seen and seen[slot]["value"] != move["value"]:
            raise _refuse(
                "REN_SWAPBACK_AMBIGUOUS",
                f"two keys land on {move['comp']}/{move['tool']}/"
                f"{move['param']} frame {move['to_frame']} with different "
                "values",
                "one frame cannot carry two values",
                "re-plan the edit so the mapping is one to one")
        seen.setdefault(slot, move)

    grade_plan = {"luts": list((grade or {}).get("luts") or []),
                  "cdl": dict((grade or {}).get("cdl") or {}),
                  "ranges": []}
    for span in (grade or {}).get("ranges") or []:
        grade_plan["ranges"].append(translate_range(
            old_map, new_map,
            {"clock_id": old_map["to_clock"],
             "start": int(span["start"]), "end": int(span["end"])}))

    return {"prior_render_id": prior_render_id,
            "new_render_id": new_render_id,
            "input_revision": new["input_revision"],
            "unique_id": unique_id,
            "old_source": old_source,
            "new_source": new_source,
            "played_anchor": {"clock_id": played_anchor["clock_id"],
                              "start": int(played_anchor["start"]),
                              "end": int(played_anchor["end"])},
            "key_moves": moves,
            "grade": grade_plan}


# ── The patch ─────────────────────────────────────────────────────────

def build_patch(plan_doc: dict, *, project_name: str, timeline_name: str,
                timeline_id: str, base_generation: int,
                played_timeline_span: list,
                capability: str = SWAPBACK_CAPABILITY,
                idempotency_key: str = "") -> dict:
    """The complete, version-checked EditPatch dict for a planned swap.

    The id derives from ``(project, timeline id, idempotency_key)``
    exactly the way ``apply_live_patch`` derives its own, so a patch
    built here and a live-planned one retry as the same patch.
    ``base_generation`` is the shadow generation the plan was read
    against; ``apply_patch`` refuses a head that moved on. The one op
    carries the media binding, the remapped keys and the grade
    assertion together - atomic by construction, so a commit can never
    land new media under stale keys.
    """
    import hashlib
    import json

    if base_generation < 1:
        raise _refuse(
            "REN_SWAPBACK_STALE_BASE",
            "the swap names no base shadow generation",
            "a patch is planned against one recorded generation",
            "observe the timeline (`timeline_shadow.observe`) and plan "
            "against its generation")
    if (len(played_timeline_span) != 2
            or int(played_timeline_span[0]) >= int(played_timeline_span[1])):
        raise _refuse(
            "REN_SWAPBACK_INVALID_RANGE",
            f"timeline span {played_timeline_span!r} is not [start, end)",
            "the patch declares the frames it touches",
            "pass the item's record span as [record_in, record_out]")
    if not idempotency_key:
        raise _refuse(
            "REN_SWAPBACK_MISSING_LINK",
            "the swap names no idempotency key",
            "a patch id is a durable identity for one complete edit, "
            "and a retry replays it instead of writing twice",
            "pass a unique idempotency key per swap")
    identity = json.dumps([project_name, timeline_id, idempotency_key],
                          ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), default=str)
    patch_id = "ren-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
    operation: dict[str, Any] = {
        "op": "clip.replace",
        "unique_id": plan_doc["unique_id"],
        "source_file": plan_doc["new_source"],
    }
    if plan_doc["key_moves"]:
        operation["fusion_keys"] = [
            {"comp": move["comp"], "tool": move["tool"],
             "param": move["param"], "from_frame": move["from_frame"],
             "to_frame": move["to_frame"], "value": move["value"]}
            for move in plan_doc["key_moves"]]
    if plan_doc["grade"]["luts"] or plan_doc["grade"]["cdl"]:
        operation["expected_grade"] = {
            "luts": list(plan_doc["grade"]["luts"]),
            "cdl": dict(plan_doc["grade"]["cdl"])}
    return {"id": patch_id,
            "project": project_name,
            "timeline": timeline_name,
            "base_generation": int(base_generation),
            "capability": capability,
            "affected_spans": [list(played_timeline_span)],
            "conflict_domains": list(SWAPBACK_DOMAINS),
            "operations": [operation],
            "preconditions": [{"kind": "clip_present",
                               "unique_id": plan_doc["unique_id"]}],
            "postconditions": [{"kind": "duration_unchanged"}]}


def commit(plan_doc: dict, *, project: Any, timeline: Any,
           played_timeline_span: list, idempotency_key: str,
           store=None, project_folder: str | None = None) -> dict:
    """Version-freeze and commit a swap under the caller's lease.

    Observes the live timeline for the base generation, builds the
    patch and commits it - the caller holds the Resolve lease (a
    broker qualification session or ``resolve_lease``). Returns the
    commit receipt.
    """
    from library.tools import edit_patch
    from library.tools import timeline_shadow as shadow

    store = store or shadow.ShadowStore()
    base = shadow.observe(project, timeline, store)
    project_name, timeline_id, timeline_name = shadow.timeline_key(
        project, timeline)
    patch = build_patch(plan_doc, project_name=project_name,
                        timeline_name=timeline_name, timeline_id=timeline_id,
                        base_generation=base.generation,
                        played_timeline_span=played_timeline_span,
                        idempotency_key=idempotency_key)
    return edit_patch.apply_patch(patch, resolve=None, project=project,
                                  timeline=timeline, store=store,
                                  project_folder=project_folder)


# ── Fusion key moves (live Resolve) ─────────────────────────────────
#
# ReplaceClip preserves the comp object but never moves its keys, so
# the swap moves each bound key through the maps itself. Reads reuse
# the ``reel_read`` input-object pattern (``tool.<Param>`` +
# ``GetKeyFrames``); writes go through the two primitives below, each
# judged by an immediate re-read. The Unit 5 probe proved which
# primitives Resolve 21.1 honours - see the probe notes on each.


def _fusion_tool(item: Any, comp_name: str, tool_name: str,
                 label: str) -> Any:
    try:
        comp = item.GetFusionCompByName(comp_name)
    except Exception as exc:  # noqa: BLE001 - unreadable comp is not one
        raise RuntimeError(
            f"{label}: comp {comp_name!r} would not open ({exc})") from exc
    if comp is None or isinstance(comp, str):
        raise RuntimeError(f"{label}: comp {comp_name!r} is not on the clip")
    try:
        tool = comp.FindTool(tool_name)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"{label}: tool {tool_name!r} would not open in comp "
            f"{comp_name!r} ({exc})") from exc
    if tool is None:
        raise RuntimeError(
            f"{label}: tool {tool_name!r} is not in comp {comp_name!r}")
    return tool


def set_fusion_key(item: Any, comp_name: str, tool_name: str, param: str,
                   frame: int, value: Any) -> None:
    """Key one Fusion input at one frame, judged by re-read.

    Proven by the Unit 5 probe on Resolve 21.1: ``SetInput`` with an
    explicit time writes a key on an ALREADY-animated input (an input
    with keys), and the key reads back through the input object. On a
    static input the same call sets the static value instead of keying
    (the matte-comp measurement in ``edit_algebra._build_matte_comp``),
    so every bound input must already carry keys - the plan binds keys
    read off the old render, which do.
    """
    tool = _fusion_tool(item, comp_name, tool_name, "set_fusion_key")
    try:
        wrote = tool.SetInput(param, value, int(frame))
    except Exception as exc:  # noqa: BLE001 - a refused write is not a key
        raise RuntimeError(
            f"set_fusion_key: {comp_name}/{tool_name}/{param} at frame "
            f"{frame} raised ({exc})") from exc
    if wrote is False:
        raise RuntimeError(
            f"set_fusion_key: {comp_name}/{tool_name}/{param} at frame "
            f"{frame} answered False")
    from library.tools import reel_read
    back = reel_read._input_key_times(tool, param)
    if int(frame) not in back:
        raise RuntimeError(
            f"set_fusion_key: {comp_name}/{tool_name}/{param} shows keys "
            f"at {back}, not frame {frame}")
    try:
        read_value = tool.GetInput(param, int(frame))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"set_fusion_key: {comp_name}/{tool_name}/{param} at frame "
            f"{frame} would not re-read ({exc})") from exc
    if read_value != value and not (
            isinstance(read_value, float) and isinstance(value, float)
            and abs(read_value - value) <= 1e-9):
        raise RuntimeError(
            f"set_fusion_key: {comp_name}/{tool_name}/{param} at frame "
            f"{frame} reads {read_value!r}, not {value!r}")


def delete_fusion_key(item: Any, comp_name: str, tool_name: str, param: str,
                      frame: int) -> None:
    """Remove one Fusion key, judged by re-read.

    Proven by the Unit 5 probe on Resolve 21.1: the input object
    exposes no key deletion (its delete-sounding attributes read as
    None - dynamic dispatch answers every name, so this code checks
    `callable`, never `hasattr`), but the BezierSpline modifier driving
    the input does: resolved through the input's connected output onto
    the comp's spline tool, ``DeleteKeyFrames(frame)`` removes the key
    at the frame. A frame carrying no key is already deleted and reads
    as success.
    """
    tool = _fusion_tool(item, comp_name, tool_name, "delete_fusion_key")
    from library.tools import reel_read
    if int(frame) not in reel_read._input_key_times(tool, param):
        return
    spline = _spline_for(tool, param, "delete_fusion_key")
    try:
        spline.DeleteKeyFrames(int(frame))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"delete_fusion_key: {comp_name}/{tool_name}/{param} at "
            f"frame {frame} raised ({exc})") from exc
    if int(frame) in reel_read._input_key_times(tool, param):
        raise RuntimeError(
            f"delete_fusion_key: {comp_name}/{tool_name}/{param} still "
            f"shows a key at frame {frame}")


def _spline_for(tool: Any, param: str, label: str) -> Any:
    """The spline tool driving one animated input, or refused."""
    try:
        inp = getattr(tool, param)
    except AttributeError as exc:
        raise RuntimeError(
            f"{label}: input {param!r} is not on the tool ({exc})") from exc
    connected = None
    for method in ("GetConnectedOutput", "GetConnectedInput"):
        try:
            candidate = getattr(inp, method)()
        except Exception:  # noqa: BLE001 - not every input connects
            continue
        if candidate is not None:
            connected = candidate
            break
    if connected is None:
        raise RuntimeError(
            f"{label}: input {param!r} drives no connected modifier; "
            f"only spline-driven keys can be deleted")
    try:
        tool_handle = connected.GetTool()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"{label}: the modifier driving {param!r} exposes no tool "
            f"({exc})") from exc
    # Dynamic dispatch answers EVERY attribute name (missing ones read
    # as None), so only a callable member is a real primitive.
    deleter = getattr(tool_handle, "DeleteKeyFrames", None)
    if not callable(deleter):
        raise RuntimeError(
            f"{label}: the spline driving {param!r} exposes no callable "
            f"DeleteKeyFrames")
    return tool_handle


def move_fusion_keys(item: Any, moves: list, label: str) -> None:
    """Every bound key to its remapped frame, judged exactly.

    Sets each new key first, then removes each vacated frame, then
    asserts the whole input reads exactly the planned keys: no missing
    landings, no stale keys. Grouped by input so one read judges one
    write set.
    """
    from library.tools import reel_read

    grouped: dict[tuple, list] = {}
    for move in moves:
        for field in ("comp", "tool", "param", "from_frame", "to_frame"):
            if field not in move:
                raise RuntimeError(
                    f"{label}: key move {move!r} names no {field}")
        grouped.setdefault((move["comp"], move["tool"],
                            move["param"]), []).append(move)
    for (comp_name, tool_name, param), group in grouped.items():
        for move in group:
            if int(move["to_frame"]) != int(move["from_frame"]):
                set_fusion_key(item, comp_name, tool_name, param,
                               int(move["to_frame"]), move.get("value"))
            elif int(move["to_frame"]) not in reel_read._input_key_times(
                    _fusion_tool(item, comp_name, tool_name, label), param):
                set_fusion_key(item, comp_name, tool_name, param,
                               int(move["to_frame"]), move.get("value"))
        for move in group:
            if int(move["from_frame"]) != int(move["to_frame"]):
                delete_fusion_key(item, comp_name, tool_name, param,
                                  int(move["from_frame"]))
        tool = _fusion_tool(item, comp_name, tool_name, label)
        live = {int(frame) for frame in
                reel_read._input_key_times(tool, param)}
        wanted = {int(move["to_frame"]) for move in group}
        if live != wanted:
            raise RuntimeError(
                f"{label}: {comp_name}/{tool_name}/{param} reads keys at "
                f"{sorted(live)}, not {sorted(wanted)}")


# ── Acceptance: the live item reads as planned ───────────────────────

def _live_item_keys(item: Any, comp: str, tool: str,
                    param: str) -> dict[int, Any]:
    """Every keyed frame of one Fusion input with its value, live."""
    fusion = item.GetFusionCompByName(comp)
    if fusion is None:
        raise SwapbackRefused(
            what=f"comp {comp!r} is not on the clip",
            why="the swap promised that comp's identity and it is gone",
            fix="restore the previous binding with the mirrored plan",
            code="REN_SWAPBACK_READBACK_MISMATCH",
            recovery_class="blocked")
    handle = fusion.FindTool(tool)
    if handle is None:
        raise SwapbackRefused(
            what=f"tool {tool!r} is not in comp {comp!r}",
            why="the swap promised that tool's keys and it is gone",
            fix="restore the previous binding with the mirrored plan",
            code="REN_SWAPBACK_READBACK_MISMATCH",
            recovery_class="blocked")
    from library.tools import reel_read
    frames = reel_read._input_key_times(handle, param)
    out: dict[int, Any] = {}
    for frame in frames:
        try:
            out[int(frame)] = handle.GetInput(param, frame)
        except Exception:  # noqa: BLE001 - unreadable key is not a value
            continue
    return out


def accept(project: Any, timeline: Any, plan_doc: dict) -> dict:
    """Judge the committed swap against the plan, live. Read-only.

    Asserts the new source, the comp identity, the grade identity and
    every moved key's frame and value; anything else is a
    ``readback_mismatch`` refusal. The generation stays recorded - the
    shadow describes the timeline, not the plan - and restoring the
    previous binding is a new (mirrored) swap, never a silent rollback.
    """
    from library.tools import reel_read

    unique_id = str(plan_doc["unique_id"])
    try:
        rows = reel_read.live_items(timeline, resolve_project=project)
    except Exception as exc:  # noqa: BLE001 - an unreadable timeline
        raise _refuse(
            "REN_SWAPBACK_READBACK_MISMATCH",
            f"the timeline would not re-read after the swap ({exc})",
            "an unreadable timeline is not a verified one",
            "read the timeline back by hand before planning anything else"
        ) from exc
    found = None
    for row in rows:
        for item in row.get("items") or []:
            try:
                same = str(item.GetUniqueId()) == unique_id
            except Exception:  # noqa: BLE001 - an unreadable item
                continue
            if not same:
                continue
            try:
                pool = item.GetMediaPoolItem()
                source = pool.GetClipProperty("File Path") if pool else ""
            except Exception:  # noqa: BLE001 - unreadable binding
                source = ""
            found = (item, source or "")
            break
        if found is not None:
            break
    if found is None:
        raise _refuse(
            "REN_SWAPBACK_READBACK_MISMATCH",
            f"clip {unique_id!r} is not on the timeline after the swap",
            "the commit receipt says the swap landed but the item is gone",
            "read the timeline back by hand before planning anything else")
    item, source = found
    if source != plan_doc["new_source"]:
        raise _refuse(
            "REN_SWAPBACK_READBACK_MISMATCH",
            f"clip reads source {source!r}, not {plan_doc['new_source']!r}",
            "the media binding did not land",
            "restore the previous binding with the mirrored plan")
    try:
        comp_names = list(item.GetFusionCompNameList() or [])
        graph = item.GetNodeGraph()
        luts = sorted(node["path"] for node in
                      (reel_read._node_luts(item) or []))
    except Exception as exc:  # noqa: BLE001 - an unreadable grade
        raise _refuse(
            "REN_SWAPBACK_READBACK_MISMATCH",
            f"the grade/Fusion state could not be re-read ({exc})",
            "an unreadable binding is not a preserved one",
            "read the clip's color page and Fusion comps by hand") from exc
    del graph
    try:
        cdl_raw = item.GetCDL()
    except Exception:  # noqa: BLE001 - this API answers None, not a CDL
        cdl_raw = None
    cdl = dict(cdl_raw or {})
    wanted_comps = sorted({move["comp"] for move in plan_doc["key_moves"]})
    missing = [name for name in wanted_comps if name not in comp_names]
    if missing:
        raise _refuse(
            "REN_SWAPBACK_READBACK_MISMATCH",
            f"comps {missing} are gone after the swap (have {comp_names})",
            "ReplaceClip must preserve the Fusion comps",
            "restore the previous binding with the mirrored plan")
    if sorted(plan_doc["grade"]["luts"]) != luts:
        raise _refuse(
            "REN_SWAPBACK_READBACK_MISMATCH",
            f"node LUTs read {luts}, not {plan_doc['grade']['luts']}",
            "ReplaceClip must preserve the grade",
            "restore the previous binding with the mirrored plan")
    if plan_doc["grade"]["cdl"] and cdl != plan_doc["grade"]["cdl"]:
        raise _refuse(
            "REN_SWAPBACK_READBACK_MISMATCH",
            f"CDL reads {cdl}, not {plan_doc['grade']['cdl']}",
            "ReplaceClip must preserve the grade",
            "restore the previous binding with the mirrored plan")
    for move in plan_doc["key_moves"]:
        live = _live_item_keys(item, move["comp"], move["tool"],
                               move["param"])
        got = live.get(int(move["to_frame"]), None)
        if got != move["value"]:
            raise _refuse(
                "REN_SWAPBACK_READBACK_MISMATCH",
                f"{move['comp']}/{move['tool']}/{move['param']} reads "
                f"{got!r} at frame {move['to_frame']}, not "
                f"{move['value']!r} (live keys: {sorted(live)})",
                "a moved key that does not read back did not move",
                "restore the previous binding with the mirrored plan")
        if (int(move["from_frame"]) != int(move["to_frame"])
                and int(move["from_frame"]) in live):
            raise _refuse(
                "REN_SWAPBACK_READBACK_MISMATCH",
                f"stale key still reads at frame {move['from_frame']} on "
                f"{move['comp']}/{move['tool']}/{move['param']}",
                "a move removes the old key; a leftover is two truths",
                "restore the previous binding with the mirrored plan")
    return {"status": "accepted",
            "render": plan_doc["new_render_id"],
            "keys": len(plan_doc["key_moves"]),
            "source": source, "comps": comp_names, "luts": luts}
