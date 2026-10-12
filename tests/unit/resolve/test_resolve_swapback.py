"""Unit 5 swap-back planning: maps, refusals and patch building.

FREE tests only: model dicts are built inline (never a real project -
a test builds its project under tmp_path or inline, per AGENTS.md 8),
and no Resolve call is made. The live half (ReplaceClip + key moves +
read-back) is proved by the scratch qualification on Ren Qualification.
"""

from __future__ import annotations

import pytest

from library.tools import resolve_swapback as swap


def _segment(a, b, c, d, kind="one_to_one", rate=None, clip_use=None):
    seg: dict = {"from": {"clock_id": "anchor", "start": a, "end": b},
                 "to": {"clock_id": "RENDER", "start": c, "end": d},
                 "mapping_kind": kind}
    if rate is not None:
        seg["rate"] = {"numerator": rate[0], "denominator": rate[1]}
    if clip_use is not None:
        seg["source_clip_use"] = clip_use
    return seg


def _map(map_id, to_clock, segments):
    return {"id": map_id, "from_clock": "anchor", "to_clock": to_clock,
            "segments": segments}


def _model(prior_map, new_map, prior_rev="rev-1", new_rev="rev-2",
           receipt_rev=None, receipt_asset="asset-new"):
    return {
        "renders": [
            {"id": "render-old", "input_revision": prior_rev,
             "output_asset_id": "asset-old", "receipt_id": "receipt-old",
             "frame_map_id": "map-old", "settings": {}},
            {"id": "render-new", "input_revision": new_rev,
             "output_asset_id": receipt_asset, "receipt_id": "receipt-new",
             "frame_map_id": "map-new", "settings": {}},
        ],
        "receipts": [
            {"id": "receipt-old", "status": "ok",
             "input_revision": prior_rev, "output_asset_id": "asset-old",
             "renderer": "test", "details": {}},
            {"id": "receipt-new", "status": "ok",
             "input_revision": receipt_rev if receipt_rev is not None else new_rev,
             "output_asset_id": receipt_asset,
             "renderer": "test", "details": {}},
        ],
        "frame_maps": [prior_map, new_map],
    }


def _one_to_one(map_id, to_clock, length):
    return _map(map_id, to_clock, [_segment(0, length, 0, length)])


def _base_model(old_len=412, new_shift=88):
    """Old 1:1 map; new map with `new_shift` prepended frames: anchor
    frame t lands on new frame t + shift, so every old key moves."""
    old = _one_to_one("map-old", "old-render", old_len)
    new = _map("map-new", "new-render",
               [_segment(0, old_len, new_shift, new_shift + old_len)])
    model = _model(old, new)
    return model


def _base_kwargs(**over):
    kwargs = {"prior_render_id": "render-old",
              "new_render_id": "render-new",
              "unique_id": "clip-1",
              "old_source": "/pool/old.mov",
              "new_source": "/pool/new.mov",
              "played_anchor": {"clock_id": "anchor", "start": 0,
                                "end": 412},
              "fusion_keys": [{"comp": "Comp1", "tool": "Transform1",
                               "param": "Size", "frame": 0, "value": 1.0},
                              {"comp": "Comp1", "tool": "Transform1",
                               "param": "Size", "frame": 205,
                               "value": 1.04},
                              {"comp": "Comp1", "tool": "Transform1",
                               "param": "Size", "frame": 411,
                               "value": 1.02}],
              "grade": {"luts": ["/luts/look.cube"], "cdl": {"s": 1.0},
                        "ranges": [{"start": 0, "end": 412}]}}
    kwargs.update(over)
    return kwargs


# ── Render / receipt / revision ──────────────────────────────────────

def test_plan_remaps_keys_through_a_length_change():
    plan_doc = swap.plan(_base_model(), **_base_kwargs())
    assert [(m["from_frame"], m["to_frame"], m["value"])
            for m in plan_doc["key_moves"]] == [
                (0, 88, 1.0), (205, 293, 1.04), (411, 499, 1.02)]
    assert plan_doc["grade"]["ranges"] == [
        {"clock_id": "new-render", "start": 88, "end": 500}]
    assert plan_doc["input_revision"] == "rev-2"


def test_plan_refuses_a_receipt_from_another_revision():
    model = _base_model()
    model["receipts"][1]["input_revision"] = "rev-0"
    with pytest.raises(swap.SwapbackRefused) as exc:
        swap.plan(model, **_base_kwargs())
    assert exc.value.code == "REN_SWAPBACK_REVISION_MISMATCH"


def test_plan_refuses_a_dangling_frame_map():
    model = _base_model()
    model["frame_maps"] = [model["frame_maps"][0]]
    with pytest.raises(swap.SwapbackRefused) as exc:
        swap.plan(model, **_base_kwargs())
    assert exc.value.code == "REN_SWAPBACK_MISSING_LINK"


def test_plan_refuses_maps_on_different_anchors():
    model = _base_model()
    model["frame_maps"][1]["from_clock"] = "other-anchor"
    with pytest.raises(swap.SwapbackRefused) as exc:
        swap.plan(model, **_base_kwargs())
    assert exc.value.code == "REN_SWAPBACK_CLOCK_MISMATCH"


# ── Coverage: the measured ReplaceClip failure ───────────────────────

def test_plan_refuses_a_shorter_render_that_leaves_keys_uncovered():
    """The scout's case: keys at 205/411 past a shorter render's end."""
    old = _one_to_one("map-old", "old-render", 412)
    new = _one_to_one("map-new", "new-render", 200)
    with pytest.raises(swap.SwapbackRefused) as exc:
        swap.plan(_model(old, new), **_base_kwargs())
    assert exc.value.code == "REN_SWAPBACK_UNCOVERED"


def test_plan_refuses_a_shorter_render_even_with_no_keys():
    old = _one_to_one("map-old", "old-render", 412)
    new = _one_to_one("map-new", "new-render", 200)
    kwargs = _base_kwargs(fusion_keys=[], grade={})
    with pytest.raises(swap.SwapbackRefused) as exc:
        swap.plan(_model(old, new), **kwargs)
    assert exc.value.code == "REN_SWAPBACK_UNCOVERED"


# ── Map semantics ────────────────────────────────────────────────────

def test_multi_clock_map_resolves_per_segment_clock():
    """MU-21 (mirror of ren-core): a tick maps only on the source
    clock that measured it. On main the map's ``from_clock`` (the
    first source) answered for every segment's ticks."""
    def seg(clock, a, b, c, d):
        out = _segment(a, b, c, d)
        out["from"]["clock_id"] = clock
        return out
    frame_map = {"id": "map-two", "from_clock": "source-a",
                 "to_clock": "reel",
                 "segments": [seg("source-a", 10000, 15000, 0, 5000),
                              seg("source-b", 20000, 25000, 5000, 10000)]}
    assert swap.map_tick(frame_map, "source-b", 20000) == 5000
    assert swap.map_tick(frame_map, "source-a", 10000) == 0
    with pytest.raises(swap.SwapbackRefused) as exc:
        swap.map_tick(frame_map, "source-a", 20000)
    assert exc.value.code == "REN_SWAPBACK_UNCOVERED"
    with pytest.raises(swap.SwapbackRefused) as exc:
        swap.map_tick(frame_map, "no-such-clock", 20000)
    assert exc.value.code == "REN_SWAPBACK_CLOCK_MISMATCH"


def test_generated_segments_are_never_guessed():
    old = _map("map-old", "old-render",
               [_segment(0, 412, 0, 412, kind="generated")])
    new = _one_to_one("map-new", "new-render", 412)
    with pytest.raises(swap.SwapbackRefused) as exc:
        swap.plan(_model(old, new), **_base_kwargs())
    assert exc.value.code == "REN_SWAPBACK_GENERATED"


def test_overlapping_spans_are_ambiguous():
    old = _map("map-old", "old-render",
               [_segment(0, 300, 0, 300), _segment(200, 412, 200, 412)])
    new = _one_to_one("map-new", "new-render", 412)
    kwargs = _base_kwargs(
        fusion_keys=[{"comp": "C", "tool": "T", "param": "P",
                      "frame": 250, "value": 1.0}])
    with pytest.raises(swap.SwapbackRefused) as exc:
        swap.plan(_model(old, new), **kwargs)
    assert exc.value.code == "REN_SWAPBACK_AMBIGUOUS"


def test_fractional_ticks_are_refused_not_rounded():
    old = _map("map-old", "old-render",
               [_segment(0, 3, 0, 2, kind="retimed", rate=(2, 3))])
    new = _one_to_one("map-new", "new-render", 3)
    kwargs = _base_kwargs(
        played_anchor={"clock_id": "anchor", "start": 0, "end": 3},
        fusion_keys=[{"comp": "C", "tool": "T", "param": "P",
                      "frame": 1, "value": 1.0}],
        grade={})
    with pytest.raises(swap.SwapbackRefused) as exc:
        swap.plan(_model(old, new), **kwargs)
    assert exc.value.code == "REN_SWAPBACK_INEXACT_TICK"


def test_freeze_is_non_invertible_on_the_way_back():
    old = _map("map-old", "old-render",
               [_segment(0, 10, 0, 30, kind="freeze")])
    new = _one_to_one("map-new", "new-render", 30)
    kwargs = _base_kwargs(
        played_anchor={"clock_id": "anchor", "start": 0, "end": 10},
        fusion_keys=[{"comp": "C", "tool": "T", "param": "P",
                      "frame": 5, "value": 1.0}],
        grade={})
    with pytest.raises(swap.SwapbackRefused) as exc:
        swap.plan(_model(old, new), **kwargs)
    assert exc.value.code == "REN_SWAPBACK_NON_INVERTIBLE"


def test_two_keys_landing_on_one_frame_with_two_values_refuse():
    old = _map("map-old", "old-render",
               [_segment(0, 10, 0, 10), _segment(0, 10, 10, 20)])
    new = _one_to_one("map-new", "new-render", 10)
    kwargs = _base_kwargs(
        played_anchor={"clock_id": "anchor", "start": 0, "end": 10},
        fusion_keys=[{"comp": "C", "tool": "T", "param": "P",
                      "frame": 2, "value": 1.0},
                     {"comp": "C", "tool": "T", "param": "P",
                      "frame": 12, "value": 2.0}],
        grade={})
    with pytest.raises(swap.SwapbackRefused) as exc:
        swap.plan(_model(old, new), **kwargs)
    assert exc.value.code == "REN_SWAPBACK_AMBIGUOUS"


def test_a_range_straddling_a_boundary_refuses():
    old = _map("map-old", "old-render",
               [_segment(0, 100, 0, 100), _segment(100, 412, 100, 412)])
    new = _one_to_one("map-new", "new-render", 412)
    kwargs = _base_kwargs(
        fusion_keys=[],
        grade={"luts": [], "cdl": {},
               "ranges": [{"start": 50, "end": 150}]})
    with pytest.raises(swap.SwapbackRefused) as exc:
        swap.plan(_model(old, new), **kwargs)
    assert exc.value.code in ("REN_SWAPBACK_UNCOVERED",
                              "REN_SWAPBACK_AMBIGUOUS")


# ── The patch ────────────────────────────────────────────────────────

def test_build_patch_is_a_complete_version_checked_patch():
    plan_doc = swap.plan(_base_model(), **_base_kwargs())
    patch = swap.build_patch(plan_doc, project_name="Ren Qualification",
                             timeline_name="Q Swapback",
                             timeline_id="tl-1", base_generation=7,
                             played_timeline_span=[0, 412],
                             idempotency_key="swap-1")
    assert patch["capability"] == "reel.touchup"
    assert patch["base_generation"] == 7
    assert patch["conflict_domains"] == ["timeline_structure", "fusion",
                                         "color"]
    assert patch["preconditions"] == [{"kind": "clip_present",
                                       "unique_id": "clip-1"}]
    assert patch["postconditions"] == [{"kind": "duration_unchanged"}]
    [operation] = patch["operations"]
    assert operation["op"] == "clip.replace"
    assert operation["source_file"] == "/pool/new.mov"
    assert [(k["from_frame"], k["to_frame"]) for k in
            operation["fusion_keys"]] == [(0, 88), (205, 293), (411, 499)]
    assert operation["expected_grade"] == {"luts": ["/luts/look.cube"],
                                          "cdl": {"s": 1.0}}
    assert patch["id"].startswith("ren-")
    again = swap.build_patch(plan_doc, project_name="Ren Qualification",
                             timeline_name="Q Swapback",
                             timeline_id="tl-1", base_generation=9,
                             played_timeline_span=[0, 412],
                             idempotency_key="swap-1")
    assert again["id"] == patch["id"]


def test_build_patch_omits_empty_carriage():
    plan_doc = swap.plan(_base_model(),
                         **_base_kwargs(fusion_keys=[], grade={}))
    patch = swap.build_patch(plan_doc, project_name="P", timeline_name="T",
                             timeline_id="tl", base_generation=1,
                             played_timeline_span=[0, 412],
                             idempotency_key="k")
    [operation] = patch["operations"]
    assert "fusion_keys" not in operation
    assert "expected_grade" not in operation


def test_build_patch_refuses_a_missing_base_generation():
    plan_doc = swap.plan(_base_model(), **_base_kwargs())
    with pytest.raises(swap.SwapbackRefused) as exc:
        swap.build_patch(plan_doc, project_name="P", timeline_name="T",
                         timeline_id="tl", base_generation=0,
                         played_timeline_span=[0, 412],
                         idempotency_key="k")
    assert exc.value.code == "REN_SWAPBACK_STALE_BASE"


def test_build_patch_refuses_an_empty_span_and_a_missing_key():
    plan_doc = swap.plan(_base_model(), **_base_kwargs())
    with pytest.raises(swap.SwapbackRefused):
        swap.build_patch(plan_doc, project_name="P", timeline_name="T",
                         timeline_id="tl", base_generation=1,
                         played_timeline_span=[412, 412],
                         idempotency_key="k")
    with pytest.raises(swap.SwapbackRefused):
        swap.build_patch(plan_doc, project_name="P", timeline_name="T",
                         timeline_id="tl", base_generation=1,
                         played_timeline_span=[0, 412],
                         idempotency_key="")


def test_built_patch_validates_against_a_base_snapshot():
    """The patch the adapter builds is one EditPatch accepts."""
    from library.tools import edit_patch

    plan_doc = swap.plan(_base_model(), **_base_kwargs())
    patch = swap.build_patch(plan_doc, project_name="P", timeline_name="T",
                             timeline_id="tl", base_generation=3,
                             played_timeline_span=[0, 412],
                             idempotency_key="k")
    base = {"start_frame": 0, "end_frame": 412,
            "tracks": [{"type": "video", "index": 1, "name": "V1",
                        "clips": [{"unique_id": "clip-1", "name": "c",
                                   "track_type": "video", "track_index": 1,
                                   "record_in": 0, "record_out": 412,
                                   "duration": 412}]}],
            "markers": {"timeline": []}}
    edit_patch.validate(edit_patch.EditPatch.from_dict(patch), base)
