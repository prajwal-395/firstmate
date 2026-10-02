"""Every VFX type the handoff offers must draw something.

Three of the five advertised effects rendered nothing: `zoom_emphasis`
emitted `zoom_percent`, `screen_shake` emitted `intensity_px`, `cut_in`
emitted `scale_factor`, and the renderer dispatches on parameter NAMES,
none of which it read. `slow_zoom` was not in the intensity map and
silently became a default 3% zoom. `cut_out` was removed (captain's
ruling 2026-08-17, superseded by the framing parameter).
"""
from __future__ import annotations
import re
import pytest
from library.steps.step_4_03_plan_vfx.post_bridge import (
    EFFECT_ALIASES,
    TOOLKIT_PARAMETERS,
    WITHDRAWN_ALIASES,
    resolve_vfx,
)
from library.tools.fusion.comp_builder import build_effect_comp
import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import pathlib
import subprocess
from library.tools.execution.apply_fusion_comps import (  # noqa: E402
    legacy_vfx_effects,
    unmapped_comp_failures,
)
import os
from library.tools.execution import apply_fusion_comps as afc


CLIP_DUR = 120
#: The source frame every comp below is built at. The builder takes no
#: default frame, so each call states it - the same numbers the
#: removed default carried.
SOURCE_RES = (1080, 1920)


def _spine(*positions):
    return {"structure": [
        {"position": p, "block_type": "speech",
         "timeline_start": float(i * 5), "timeline_end": float(i * 5 + 5)}
        for i, p in enumerate(positions)
    ]}


# ── Every name the toolkit advertises reaches the renderer ──
#
# This is the guarantee `INTENSITY_MAP` used to carry by construction. It
# is removed (captain, 2026-09-02: a three-point scale is still the engine
# choosing how strong an effect is), so the VALUES are the plan's - but
# the NAMES still have to reach a reader, or an effect is recorded as
# planned and draws nothing (AGENTS.md §10.2).


def test_the_ken_burns_spelling_reaches_the_renderer():
    """The extension must draw, not just resolve: a reasoned `ken_burns`
    entry becomes a drift entry whose params change the comp."""
    resolved = resolve_vfx(
        [{"target_block_position": 1, "effect_type": "ken_burns",
          "params": {"zoom_start": 1.0, "zoom_end": 1.03},
          "rationale": "a locked hold that goes dead under the point"}],
        _spine(1, 2),
    )
    assert len(resolved) == 1
    neutral = build_effect_comp({"vignette": False}, CLIP_DUR,
                                source_res=SOURCE_RES)
    drawn = build_effect_comp(
        dict(resolved[0]["params"], vignette=False), CLIP_DUR,
        source_res=SOURCE_RES)
    assert drawn != neutral


def _value_for(name):
    """A non-neutral value in the shape the renderer expects.

    Only has to differ from the default: this asks whether the NAME
    reaches a reader, not what any number does. Zooms stay under the comp
    validator's animated-Size refusal in `fusion/nodes.py`, which is a
    separate, pre-existing rule (AGENTS.md §5).
    """
    if name.endswith("_frames"):
        return 3
    if name.startswith("pan_"):
        return (0.45, 0.5)
    if name.startswith("shake_"):
        return 0.004
    return 1.03


@pytest.mark.parametrize("effect_type,names", sorted(
    (k, v) for k, v in TOOLKIT_PARAMETERS.items()))
def test_every_advertised_parameter_name_changes_the_comp(effect_type, names):
    """Every advertised name must change what is drawn.

    `zoom_percent`, `intensity_px` and `scale_factor` were advertised and
    read by nothing, which is how three of five effects rendered nothing
    while the manifest recorded them as planned. `pan_start` is the same
    shape and is why it is NOT in the enumeration - `fx.zoom` takes it and
    draws nothing with it.

    A name is added to the effect's own first name rather than tested
    alone, because some are MODIFIERS: `shake_decay_frames` shapes the
    shake that `shake_x` starts and reaches no dispatch on its own.
    """
    neutral = build_effect_comp({"vignette": False}, CLIP_DUR,
                                source_res=SOURCE_RES)
    base = {names[0]: _value_for(names[0]), "vignette": False}
    base_comp = build_effect_comp(base, CLIP_DUR, source_res=SOURCE_RES)
    assert base_comp != neutral, (
        f"{effect_type}: `{names[0]}` is advertised and draws nothing")

    for name in names[1:]:
        if effect_type == "zoom_emphasis" and name in {
                "zoom_in_seconds", "zoom_out_seconds"}:
            # Timing is resolved together with its two material anchors
            # into frame parameters before the comp builder can read it.
            continue
        comp = build_effect_comp(
            dict(base, **{name: _value_for(name)}), CLIP_DUR,
            source_res=SOURCE_RES)
        assert comp != base_comp, (
            f"{effect_type}: `{name}` is advertised and changes nothing")


def test_an_effect_whose_params_reach_no_reader_is_dropped(capsys):
    """`zoom_in`, `amplitude` and `zoom` are not names the renderer reads.

    Passing them through would put an effect on the manifest that the
    viewer never sees - and say nothing. Nothing is substituted.
    """
    resolved = resolve_vfx(
        [{"target_block_position": 1, "effect_type": "screen_shake",
          "params": {"amplitude": 4, "frequency": 12}}],
        _spine(1, 2),
    )
    assert resolved == []
    err = capsys.readouterr().err
    assert "screen_shake" in err and "shake_x" in err


def test_only_the_unreadable_names_are_dropped_from_a_usable_entry():
    """An entry that reaches a reader survives, carrying only what does."""
    resolved = resolve_vfx(
        [{"target_block_position": 1, "effect_type": "slow_zoom_in",
          "params": {"zoom_start": 1.0, "zoom_end": 1.4, "bogus": 9},
          "rationale": "a static hold that wants a drift"}],
        _spine(1, 2),
    )
    assert len(resolved) == 1
    assert resolved[0]["params"] == {"zoom_start": 1.0, "zoom_end": 1.4}


def test_no_value_is_bounded_or_substituted():
    """The plan's number reaches the comp untouched.

    `INTENSITY_MAP` capped every zoom at 1.04 by citing AGENTS.md - a
    document this step never reads - and the captain removed it: how far
    a zoom travels is the plan's decision.
    """
    resolved = resolve_vfx(
        [{"target_block_position": 1, "effect_type": "cut_in",
          "params": {"zoom_start": 1.9, "zoom_mid": 1.9, "zoom_end": 1.9}}],
        _spine(1, 2),
    )
    assert resolved[0]["params"] == {
        "zoom_start": 1.9, "zoom_mid": 1.9, "zoom_end": 1.9}
    assert "1.9" in build_effect_comp(
        dict(resolved[0]["params"], vignette=False), CLIP_DUR,
        source_res=SOURCE_RES)


def test_a_static_reframe_is_actually_drawn():
    """fx.zoom used to return an empty block whenever start == mid == end,
    so a cut_in produced a Transform with Size left at its default."""
    comp = build_effect_comp(
        {"zoom_start": 1.25, "zoom_mid": 1.25, "zoom_end": 1.25,
         "vignette": False}, CLIP_DUR, source_res=SOURCE_RES)
    assert "Transform" in comp
    assert "1.25" in comp
    # An identity zoom still draws nothing.
    comp = build_effect_comp(
        {"zoom_start": 1.0, "zoom_mid": 1.0, "zoom_end": 1.0,
         "vignette": False}, CLIP_DUR, source_res=SOURCE_RES)
    assert "Transform" not in comp


def test_screen_shake_decays_to_stillness():
    comp = build_effect_comp(
        dict({"shake_x": 0.0037, "shake_y": 0.0037, "shake_decay_frames": 5}, vignette=False),
        CLIP_DUR, source_res=SOURCE_RES)
    # Every key past the decay window sits at the neutral centre.
    settled = re.findall(r"\[(\d+)\] = \{ 0\.5,", comp)
    assert settled, "the shake never settles"
    assert max(int(f) for f in settled) > 5


# ── The bridge does not invent effects ──

def test_an_aliased_effect_type_resolves():
    """An alias may RENAME an effect. `push_in` and `zoom_emphasis` are
    two names for the same punch-and-settle, so the mapping states a
    fact rather than making a choice."""
    resolved = resolve_vfx(
        [{"target_block_position": 1, "effect_type": "push_in",
          "params": {"zoom_start": 1.0, "zoom_mid": 1.04,
                     "zoom_end": 1.0, "zoom_in_seconds": 0.67,
                     "zoom_out_seconds": 0.67},
          "anchor": {"frame": 30}, "anchor_end": {"frame": 60}}],
        _spine(1, 2),
    )
    assert len(resolved) == 1
    assert resolved[0]["effect_type"] == EFFECT_ALIASES["push_in"]
    assert resolved[0]["params"]["zoom_mid"] > 1.0


def test_an_alias_that_chose_a_direction_is_withdrawn(capsys):
    """`slow_zoom` names no direction.

    It used to resolve to `slow_zoom_in`, which answers "which way?" on
    the planner's behalf. A plan naming one is dropped with the toolkit
    listed, so the editor says which they meant.  (`ken_burns` used to
    sit beside it and no longer does: since 2026-09-09 it is the
    captain's name for the drift move, with the direction read off its
    own params - see tests/unit/context/test_plan_values.py.)"""
    for alias in WITHDRAWN_ALIASES:
        resolved = resolve_vfx(
            [{"target_block_position": 1, "effect_type": alias,
              "params": {"zoom_start": 1.0, "zoom_mid": 1.04,
                          "zoom_end": 1.0}}],
            _spine(1, 2),
        )
        assert resolved == []
        err = capsys.readouterr().err
        assert alias in err and "direction" in err


def test_a_builtin_fusion_effect_passes_through_without_parameters():
    resolved = resolve_vfx(
        [{"target_block_position": 1, "effect_type": "advanced_camera_shake"}],
        _spine(1, 2),
    )
    assert len(resolved) == 1
    assert resolved[0]["params"] == {}


def test_dropping_an_effect_frees_its_block_for_another(capsys):
    """An unknown effect type is dropped, said, never defaulted - and the
    drop must not consume the block's one-effect slot."""
    resolved = resolve_vfx(
        [
            {"target_block_position": 1, "effect_type": "sparkle_blast",
             "params": {"zoom_start": 1.0, "zoom_end": 1.04}},
            {"target_block_position": 1, "effect_type": "slow_zoom_in",
             "params": {"zoom_start": 1.0, "zoom_end": 1.04},
             "rationale": "a static hold that wants a drift"},
        ],
        _spine(1, 2),
    )
    assert len(resolved) == 1
    assert resolved[0]["effect_type"] == "slow_zoom_in"
    assert "sparkle_blast" in capsys.readouterr().err


# --------------------------------------------------------------------------
# From test_vfx_drops_reach_the_model.py
#
# Finding 34: on the first post-bridge pass a dropped VFX entry raises
# through `post_bridge_retry` so the model can correct the slip; a later
# pass (or a direct call with no retry path) ships what resolves, with the
# drops recorded. History: `docs/evidence/vfx_drops_reach_the_model.md`.

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.steps.step_4_03_plan_vfx import post_bridge as pb  # noqa: E402
from library.tools import post_bridge_retry  # noqa: E402


def _spine_2():
    return {"structure": [
        {"position": 1, "block_type": "speech", "clip_id": "clip_a",
         "source_start": 0.0, "source_end": 5.0,
         "timeline_start": 0.0, "timeline_end": 5.0,
         "word_timestamps": [], "alignment_method": "whisperx",
         "content": {}},
        {"position": 2, "block_type": "speech", "clip_id": "clip_a",
         "source_start": 5.0, "source_end": 10.0,
         "timeline_start": 5.0, "timeline_end": 10.0,
         "word_timestamps": [], "alignment_method": "whisperx",
         "content": {}},
    ]}


def _data(creative, **extra):
    data = {"a_roll_assignments": [],
            "timed_spine": _spine_2(),
            "frame_rate": 30.0,
            "vfx_creative": creative}
    data.update(extra)
    return data


def _run_main(data):
    stdin = io.StringIO(json.dumps(data))
    stdout = io.StringIO()
    stderr = io.StringIO()
    old_stdin = sys.stdin
    sys.stdin = stdin
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            pb.main()
    finally:
        sys.stdin = old_stdin
    return json.loads(stdout.getvalue()), stderr.getvalue()


def test_the_b8_slips_are_drops_with_reasons():
    """The evidence, pinned: `zoom` names nothing readable, `speed`
    is not a stepped segment."""
    dropped = []
    resolved = pb.resolve_vfx(
        [{"target_block_position": 1, "effect_type": "cut_in",
          "params": {"zoom": 1.15}, "rationale": "punch on quit"},
         {"target_block_position": 2, "effect_type": "speed_ramp",
          "params": {"speed": 200}, "rationale": "ramp in"}],
        _spine_2(), 30.0, dropped=dropped)
    assert resolved == []
    assert {d.reason for d in dropped} == {"no_readable_parameters",
                                          "not_a_speed_step"}


def test_drops_raise_on_the_first_pass_so_the_model_can_correct():
    """A first-pass answer that drops entries raises naming each one -
    which is what travels the post_bridge_retry path."""
    import pytest

    data = _data(
        [{"target_block_position": 1, "effect_type": "cut_in",
          "params": {"zoom": 1.15}, "rationale": "punch on quit"}],
        **{post_bridge_retry.ATTEMPT_KEY: 1})
    with pytest.raises(ValueError, match="no_readable_parameters"):
        _run_main(data)


def test_drops_ship_with_their_reasons_once_retried():
    """The retry already happened (the model kept the slip), or there is
    no retry path to travel (a direct call outside the runner - the replay
    bench, the manifest tests): the resolved plan ships and the drop is
    recorded, never silent."""
    creative = [{"target_block_position": 1, "effect_type": "cut_in",
                 "params": {"zoom": 1.15}, "rationale": "punch on quit"}]
    for extra in ({post_bridge_retry.ATTEMPT_KEY: 2}, {}):
        out, _ = _run_main(_data(creative, **extra))
        spec = out["enhancement_spec"]
        assert spec["visual_effects"] == []
        assert spec["planning_basis"]["basis"] == "every_entry_dropped"
        (drop,) = spec["planning_basis"]["dropped"]
        assert drop["reason"] == "no_readable_parameters"


def test_a_clean_plan_never_raises():
    data = _data(
        [{"target_block_position": 1, "effect_type": "cut_in",
          "params": {"zoom_start": 1.0, "zoom_mid": 1.15,
                     "zoom_end": 1.0},
          "rationale": "punch on quit"}],
        **{post_bridge_retry.ATTEMPT_KEY: 1})
    out, _ = _run_main(data)
    assert len(out["enhancement_spec"]["visual_effects"]) == 1


# --------------------------------------------------------------------------
# From test_vfx_freeze_hold.py
#
# Rung 7 (K1, RT3.3): freeze duration uses the requester's units.
#
# `hold_seconds` and `hold_frames` set the freeze span from its anchor.
# Resolve's native speed write needs one timeline item spanning exactly
# that operation, so a sub-block hold is refused until the builder can
# place a separate item for it. These tests pin both the exact value that
# reaches a matching item and the refusal that prevents a partial span
# from being claimed as built.

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools.native_ops import NativeSpeedRefused

FPS = 30.0


def _block(duration=10.0, quit_at=2.0):
    return {
        "position": 1, "block_type": "speech", "clip_id": "clip_001",
        "source_start": 0.0, "source_end": duration,
        "timeline_start": 0.0, "timeline_end": duration,
        "word_timestamps": [
            {"word": "quit", "source_start": quit_at,
             "source_end": min(quit_at + 0.5, duration)},
            {"word": "now", "source_start": min(quit_at + 0.6, duration),
             "source_end": min(quit_at + 1.0, duration)},
        ],
    }


def _resolve(entries, block=None, dropped=None):
    return resolve_vfx(
        entries, {"structure": [block or _block()]}, frame_rate=FPS,
        dropped=dropped)


def test_the_hold_in_the_requesters_units_reaches_a_matching_item():
    """42 stated frames resolve to an item whose whole span is 42 frames;
    1.0 stated seconds to one second; agreeing seconds and frames ship the
    frames."""
    rows = [({"hold_frames": 42}, 42 / FPS),
            ({"hold_seconds": 1.0}, 1.0),
            ({"hold_seconds": 1.4, "hold_frames": 42}, 42 / FPS)]
    for hold, duration in rows:
        (entry,) = _resolve([dict({
            "target_block_position": 1, "effect_type": "freeze_frame",
            "anchor": {"word": "quit"}, "rationale": "stop time"}, **hold)],
            block=_block(duration=duration, quit_at=0.0))
        assert entry["effect_type"] == "freeze_frame"
        assert entry["timeline_start"] == pytest.approx(0.0)
        assert entry["timeline_end"] == pytest.approx(duration), hold
        assert "holds" in entry.get("anchor_method", "")


def test_sub_block_hold_is_reported_until_builder_can_split_the_item():
    dropped = []
    assert _resolve([{
        "target_block_position": 1, "effect_type": "freeze_frame",
        "anchor": {"word": "quit"}, "hold_frames": 42,
        "rationale": "stop time on the word"}], dropped=dropped) == []
    assert dropped[0].reason == "speed_span_subdivides_block"
    assert "2.000-3.400s" in dropped[0].detail


def test_disagreeing_seconds_and_frames_refuse():
    with pytest.raises(NativeSpeedRefused):
        _resolve([{
            "target_block_position": 1, "effect_type": "freeze_frame",
            "anchor": {"word": "quit"}, "hold_seconds": 1.0,
            "hold_frames": 42, "rationale": "two numbers"}])


# --------------------------------------------------------------------------
# From test_vfx_plan_basis.py
#
# An empty VFX plan says WHY it is empty: a plan the model left empty and
# one whose every entry the post-bridge dropped are different records, driven
# through the REAL post-bridge subprocess. The record never pads a plan and
# carries no creative value. History: `docs/evidence/vfx_plan_basis.md`.

REPO_2 = pathlib.Path(__file__).resolve().parents[3]
POST_BRIDGE = "library.steps.step_4_03_plan_vfx.post_bridge"


def _spine_3():
    """Four blocks, of the shape `mesh_spine` really emits."""
    return {"structure": [
        {"position": "hook", "block_type": "hook", "clip_id": "clip_1",
         "timeline_start": 0.0, "timeline_end": 2.4},
        {"position": 7, "block_type": "speech", "clip_id": "clip_2",
         "timeline_start": 2.4, "timeline_end": 18.4},
        {"position": 14, "block_type": "speech", "clip_id": "clip_3",
         "timeline_start": 18.4, "timeline_end": 27.1},
        {"position": 15, "block_type": "speech", "clip_id": "clip_4",
         "timeline_start": 27.1, "timeline_end": 29.8},
    ]}


def _run(plan):
    """The post-bridge, run the way `run_pipeline` runs it."""
    payload = {
        "a_roll_assignments": [
            {"spine_block_position": 7,
             "video_segments": [{"clip_id": "clip_2",
                                 "source_file": "/tmp/IMG_1.mov"}]},
        ],
        "timed_spine": _spine_3(),
        "vfx_creative": plan,
    }
    proc = subprocess.run(
        [sys.executable, "-m", POST_BRIDGE],
        input=json.dumps(payload), capture_output=True,
        encoding="utf-8", cwd=str(REPO_2),
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)["enhancement_spec"], proc.stderr


# ── The distinction, driven through the real post-bridge ──────────────

def test_a_plan_the_model_left_empty_says_so():
    spec, stderr = _run([])
    assert spec["visual_effects"] == []
    assert spec["planning_basis"] == {
        "basis": "no_effects_planned",
        "proposed": 0, "resolved": 0, "dropped": [],
    }
    assert "no_effects_planned" in stderr
    # Recording the basis never pads the plan (AGENTS.md 10.5).
    assert "generator_overlays" not in spec


def test_a_plan_whose_every_entry_was_dropped_says_that_instead():
    spec, stderr = _run([
        {"target_block_position": 14, "effect_type": "slow_zoom",
         "params": {}, "rationale": "a"},
        {"target_block_position": 7, "effect_type": "glitch",
         "params": {}, "rationale": "b"},
        {"target_block_position": 99, "effect_type": "slow_zoom_in",
         "params": {}, "rationale": "c"},
    ])
    assert spec["visual_effects"] == []
    basis = spec["planning_basis"]
    assert basis["basis"] == "every_entry_dropped"
    assert basis["proposed"] == 3 and basis["resolved"] == 0
    assert [d["reason"] for d in basis["dropped"]] == [
        "withdrawn_alias", "unknown_effect_type",
        "not_a_spine_block",
    ]
    for drop in basis["dropped"]:
        assert drop["detail"], "a drop states its own sentence"
    assert "every_entry_dropped" in stderr


def test_a_partly_dropped_plan_is_planned_and_names_its_casualties():
    spec, _ = _run([
        {"target_block_position": 14, "effect_type": "slow_zoom_in",
         "params": {"zoom_start": 1.0, "zoom_end": 1.03},
         "rationale": "the one long static hold"},
        {"target_block_position": 7, "effect_type": "glitch",
         "params": {"zoom_start": 1.0, "zoom_end": 1.04}, "rationale": "b"},
    ])
    basis = spec["planning_basis"]
    assert basis["basis"] == "planned"
    assert basis["proposed"] == 2 and basis["resolved"] == 1
    assert [d["reason"] for d in basis["dropped"]] == ["unknown_effect_type"]
    assert len(spec["visual_effects"]) == 1
    # The record carries no creative value: a verbatim echo of the ask.
    drop = basis["dropped"][0]
    assert drop["effect_type"] == "glitch"
    assert drop["target_block_position"] == 7


def test_a_fully_resolved_plan_drops_nothing():
    spec, _ = _run([
        {"target_block_position": 14, "effect_type": "slow_zoom_in",
         "params": {"zoom_start": 1.0, "zoom_end": 1.03}, "rationale": "a"},
        {"target_block_position": 7, "effect_type": "zoom_emphasis",
         "params": {"zoom_start": 1.0, "zoom_mid": 1.03,
                    "zoom_end": 1.0, "zoom_in_seconds": 0.67,
                    "zoom_out_seconds": 0.67},
         "anchor": {"frame": 120}, "anchor_end": {"frame": 150},
         "rationale": "b"},
    ])
    basis = spec["planning_basis"]
    assert basis == {"basis": "planned", "proposed": 2, "resolved": 2,
                     "dropped": []}
    assert len(spec["visual_effects"]) == 2


# ── What the record must not do ───────────────────────────────────────


# --------------------------------------------------------------------------
# From test_vfx_window_frame_exact.py
#
# Effect windows are frame-exact (finding 27): the legacy Fusion fallback
# matches only a window WHOLLY inside one placed item. A planned comp that
# reaches no timeline item fails naming the clip (finding 15). Plain fakes,
# no Resolve writes. History: `docs/evidence/vfx_window_frame_exact.md`.

class _Item:
    def __init__(self, start, end):
        self._start = start
        self._end = end

    def GetStart(self):
        return self._start

    def GetEnd(self):
        return self._end


# Finding 27's geometry: V1 block 3 ends at frame 768, the V2 block
# starts at 767, and the planned window is 25.576-30.576 s.
V1_ITEMS = [_Item(0, 768), _Item(768, 1500)]
V1_CLIPS = [{"label": "speech_3_seg0"}, {"label": "speech_4_seg0"}]
ORIG_TO_ITEM = {0: 0, 1: 1}


def _entry(start, end, effect="slow_zoom_in"):
    return {
        "effect_type": effect,
        "timeline_start": start,
        "timeline_end": end,
        "target_block_position": 4,
        "params": {"zoom_start": 1.0, "zoom_end": 1.04},
    }


def test_boundary_overlap_matches_no_neighbour():
    """The finding's exact window: 25.576 s is frame 767, inside V1:3
    ([0, 768)) by the start edge - but the window runs to frame 917,
    past its end. The old start-edge match drew the zoom on V1:3 as
    well as the b-roll; the frame-exact match draws it on neither
    V1 item (compile keys it to the b-roll by label)."""
    assert legacy_vfx_effects(
        [_entry(25.576, 30.576)], V1_CLIPS, V1_ITEMS, ORIG_TO_ITEM,
        FPS) == {}


def test_a_window_wholly_inside_one_item_still_matches():
    assert legacy_vfx_effects(
        [_entry(26.0, 28.0)], V1_CLIPS, V1_ITEMS, ORIG_TO_ITEM,
        FPS) == {"speech_4_seg0": {
            "_preset": "slow_zoom_in",
            "zoom_start": 1.0, "zoom_end": 1.04}}


def test_routed_entries_never_ride_the_legacy_path():
    """A speed ramp and a stabilization carry their own applicators;
    merging their params here built empty comps."""
    entries = [
        {"effect_type": "speed_ramp", "route": "native_resolve",
         "timeline_start": 26.0, "timeline_end": 28.0,
         "params": {"segments": []}},
        {"effect_type": "stabilize", "route": "neural_engine",
         "timeline_start": 26.0, "timeline_end": 28.0, "params": {}},
    ]
    assert legacy_vfx_effects(
        entries, V1_CLIPS, V1_ITEMS, ORIG_TO_ITEM, FPS) == {}


class _PoolItem:
    def __init__(self, path):
        self._path = path

    def GetClipProperty(self, name):
        return {"File Path": self._path}.get(name, "")


class _PlacedItem:
    def __init__(self, path):
        self._mpi = _PoolItem(path) if path is not None else None

    def GetMediaPoolItem(self):
        return self._mpi


def test_unmapped_planned_comp_fails_naming_the_clip():
    """Finding 15's FR3.3 shape: per_clip keys a comp onto
    speech_12_seg0, but the spec file matches no placed item - the
    failure names the clip, the spec file and what was placed,
    instead of the pass staying green."""
    comp_tracks = [(1, [{"label": "speech_12_seg0",
                        "source_file": "/footage/IMG_1812.MOV"}], True)]
    items_by_track = {1: [_PlacedItem("/other/IMG_1800.MOV")]}
    failures = unmapped_comp_failures(
        comp_tracks, items_by_track, {},
        {"speech_12_seg0": {"_preset": "slow_zoom_in",
                            "zoom_start": 1.0, "zoom_end": 1.1}})
    assert len(failures) == 1
    assert "speech_12_seg0" in failures[0]
    assert "IMG_1812.MOV" in failures[0]
    assert "no timeline item" in failures[0]


def test_mapped_comp_and_plain_clips_stay_quiet():
    """A spec whose file matches, and a plain clip carrying no comp
    intent, record nothing."""
    spec = {"label": "speech_12_seg0",
            "source_file": "/footage/IMG_1812.MOV"}
    plain = {"label": "speech_13_seg0",
             "source_file": "/footage/IMG_1813.MOV"}
    comp_tracks = [(1, [spec, plain], True)]
    items_by_track = {1: [_PlacedItem("/footage/IMG_1812.MOV"),
                          _PlacedItem("/footage/IMG_1813.MOV")]}
    assert unmapped_comp_failures(
        comp_tracks, items_by_track, {},
        {"speech_12_seg0": {"_preset": "slow_zoom_in"}}) == []


# --------------------------------------------------------------------------
# From test_loader_delivery.py
#
# Locked Loader delivery: wiring and read-back refusal.
#
# `TimelineItem.ImportFusionComp` turns Loader nodes into MediaIn
# placeholders, so the delivery re-attaches the files under
# comp.Lock(). Everything Resolve-shaped here is a fake with the same
# method names; the logic under test is which calls are made, in which
# order, and what read-back refuses.
#
# No timeline path rides this today - the behind-subject composite it
# was built for now reaches the timeline precomposited
# (library/tools/behind_subject.py) - so the specs and wiring here are
# synthetic. What stays covered is the generic executor, for a future
# file-backed delivery that measures.

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.execution import deliver_loaders as dl


SPECS = [
    {"role": "title", "first_frame": "/titles/t_00000.png",
     "trim_in": 12, "trim_out": 40},
    {"role": "matte", "first_frame": "/mattes/m_00000.png",
     "trim_in": 0, "trim_out": None},
]

WIRING = (
    ("TitleOver", "Foreground", "title", "Output"),
    ("SubjectOver", "EffectMask", "matte", "Mask"),
)

PLACEHOLDERS = ("BehindTitle", "SubjectMatte")


class _Input:
    def __init__(self, input_id, connected=True):
        self._attrs = {"INPS_ID": input_id, "INPB_Connected": connected}

    def GetAttrs(self):
        return dict(self._attrs)


class _Tool:
    def __init__(self, name, reg="Merge", attrs=None):
        self._name = name
        self._reg = reg
        # Both attribute tables, shaped like the measured loader:
        # the integer table parses the numbering (sane length, 0x0
        # dims until decode), the string table carries the decoded
        # file. A fake with only one table would pass code that
        # reads only that table and fail the build it ships to.
        self._attrs = {"TOOLS_Name": name, "TOOLS_RegID": reg,
                       "TOOLIT_Clip_Length": {1: 300},
                       "TOOLIT_Clip_Width": {1: 0},
                       "TOOLIT_Clip_Height": {1: 0},
                       "TOOLST_Clip_Length": {1: 300},
                       "TOOLST_Clip_Width": {1: 1920},
                       "TOOLST_Clip_Height": {1: 1080}}
        self._attrs.update(attrs or {})
        self.inputs = {}
        self.calls = []

    def GetAttrs(self):
        return dict(self._attrs)

    def SetMultiClip(self, path):
        self.calls.append(("SetMultiClip", path))

    def SetAttrs(self, values):
        self.calls.append(("SetAttrs", dict(values)))
        self._attrs.update(values)

    def ConnectInput(self, input_id, tool):
        self.calls.append(("ConnectInput", input_id, tool._name))
        return True

    def GetInputList(self):
        return {i: _Input(input_id, True)
                for i, input_id in enumerate(self.inputs)}

    def FindTool(self, name):
        raise AssertionError("wired through the comp, not the tool")

    def Delete(self):
        self.calls.append(("Delete",))


class _Comp:
    def __init__(self, tools):
        self.tools = dict(tools)
        self.calls = []
        self._locked = 0

    def Lock(self):
        self._locked += 1
        self.calls.append(("Lock",))

    def Unlock(self):
        self._locked -= 1
        self.calls.append(("Unlock",))

    def AddTool(self, reg, *_pos):
        tool = _Tool(f"Loader{len(self.tools)}", reg=reg)
        self.tools[tool._name] = tool
        self.calls.append(("AddTool", reg))
        return tool

    def FindTool(self, name):
        return self.tools.get(name)


def _wired_comp(**overrides):
    title_over = _Tool("TitleOver")
    title_over.inputs = {"Foreground": True}
    subject_over = _Tool("SubjectOver")
    subject_over.inputs = {"EffectMask": True}
    tools = {"TitleOver": title_over, "SubjectOver": subject_over}
    tools.update(overrides)
    return _Comp(tools)


def test_delivery_wires_both_loaders_under_lock():
    comp = _wired_comp()
    delivered = dl.deliver_loaders(comp, SPECS, WIRING, PLACEHOLDERS)
    assert sorted(delivered) == ["matte", "title"]
    assert comp.calls[0] == ("Lock",)
    assert comp.calls[-1] == ("Unlock",)
    assert comp._locked == 0
    title_over = comp.tools["TitleOver"]
    assert ("ConnectInput", "Foreground", delivered["title"]) in [
        (c[0], c[1], c[2]) for c in title_over.calls
        if c[0] == "ConnectInput"]


def test_a_loader_that_did_not_decode_or_resolve_refuses():
    comp = _wired_comp()
    real_add = comp.AddTool

    def _black_loader(reg, *_pos):
        tool = real_add(reg, *_pos)
        tool._attrs.update({"TOOLST_Clip_Width": {1: 0},
                            "TOOLST_Clip_Height": {1: 0}})
        return tool

    comp.AddTool = _black_loader
    with pytest.raises(dl.LoaderDeliveryRefused) as exc:
        dl.deliver_loaders(comp, SPECS, WIRING, PLACEHOLDERS)
    assert "did not decode" in str(exc.value)

    # An unreadable clip length refuses the same way.
    comp = _wired_comp()
    real_add_2 = comp.AddTool

    def _unreadable_loader(reg, *_pos):
        tool = real_add_2(reg, *_pos)
        tool._attrs.update({"TOOLST_Clip_Length": {1: 0xFFFFFFFF},
                            "TOOLIT_Clip_Length": {1: 0xFFFFFFFF}})
        return tool

    comp.AddTool = _unreadable_loader
    with pytest.raises(dl.LoaderDeliveryRefused) as exc:
        dl.deliver_loaders(comp, SPECS, WIRING, PLACEHOLDERS)
    assert "did not resolve" in str(exc.value)


def test_a_missing_merge_refuses_not_skips():
    comp = _Comp({})
    with pytest.raises(dl.LoaderDeliveryRefused) as exc:
        dl.deliver_loaders(comp, SPECS, WIRING, PLACEHOLDERS)
    assert "TitleOver" in str(exc.value)


def test_placeholders_are_removed_and_working_loaders_kept():
    placeholder = _Tool("BehindTitle", reg="MediaIn")
    comp = _wired_comp(BehindTitle=placeholder)
    dl.deliver_loaders(comp, SPECS, WIRING, PLACEHOLDERS)
    assert ("Delete",) in placeholder.calls


def test_trims_that_do_not_take_refuse():
    comp = _wired_comp()
    real_add = comp.AddTool

    def _stubborn_loader(reg, *_pos):
        tool = real_add(reg, *_pos)

        def _ignore(values):
            tool.calls.append(("SetAttrs", dict(values)))

        tool.SetAttrs = _ignore
        return tool

    comp.AddTool = _stubborn_loader
    with pytest.raises(dl.LoaderDeliveryRefused) as exc:
        dl.deliver_loaders(comp, SPECS, WIRING, PLACEHOLDERS)
    assert "wrong frames" in str(exc.value)


# --------------------------------------------------------------------------
# From test_missing_dvr_sentinel.py

def test_missing_dvr_sentinel_can_be_monkeypatched(monkeypatch):
    """
    Ensure the sentinel object bound to `dvr` when DaVinciResolveScript 
    is missing allows monkeypatching of attributes without raising an error 
    on attribute access. It should only raise when the attribute is called.
    """
    # Assuming dvr is the sentinel if the test is run in CI without DaVinciResolveScript.
    # If the real module is present, patching is obviously fine.
    
    # This should not raise an exception
    monkeypatch.setattr(afc.dvr, "scriptapp", lambda name: "mocked")
    
    # Verify the patch worked
    assert afc.dvr.scriptapp("Resolve") == "mocked"
