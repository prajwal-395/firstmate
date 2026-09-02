"""Tests for generator preset routing to the overlay track.

The generator routing has three layers:
1. Planning: resolve_generator_overlays extracts generators from the
   creative plan and produces overlay entries with timeline placement.
2. Rejection: resolve_vfx still rejects generators from the clip-effect
   path - this is the existing half that PR 95 delivered.
3. Manifest: compile_manifest passes generator_overlays through to the
   renderer.

These tests exercise all three layers headlessly, without Resolve.
"""
import io
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.steps.step_4_03_plan_vfx.post_bridge import (
    EFFECT_ALIASES,
    resolve_generator_overlays,
    resolve_vfx,
)
from library.tools.builtin_effect_loader import (
    is_generator_effect,
    list_generator_effects,
)


def _spine(*positions):
    return {"structure": [
        {"position": p, "block_type": "speech",
         "timeline_start": float(i * 5), "timeline_end": float(i * 5 + 5)}
        for i, p in enumerate(positions)
    ]}


# ── Layer 1: Planning ──────────────────────────────────────────────

class TestResolveGeneratorOverlays:
    """resolve_generator_overlays routes generators to overlay entries."""

    def test_generator_produces_overlay_entry(self):
        """A generator preset in the creative plan becomes an overlay."""
        plan = [{"target_block_position": 1, "effect_type": "fireworks"}]
        overlays = resolve_generator_overlays(plan, _spine(1, 2))
        assert len(overlays) == 1
        assert overlays[0]["effect_name"] == "fireworks"
        assert overlays[0]["timeline_start"] == 0.0
        assert overlays[0]["timeline_end"] == 5.0
        assert overlays[0]["target_block_position"] == 1
        assert "overlay_id" in overlays[0]

    def test_clip_effect_not_in_overlays(self):
        """A clip effect (with image input) is not routed to overlays."""
        plan = [{"target_block_position": 1, "effect_type": "advanced_camera_shake"}]
        overlays = resolve_generator_overlays(plan, _spine(1, 2))
        assert len(overlays) == 0

    def test_mixed_plan_separates_generators(self):
        """A plan with both generators and clip effects only overlays the generators."""
        plan = [
            {"target_block_position": 1, "effect_type": "fireworks"},
            {"target_block_position": 2, "effect_type": "advanced_camera_shake"},
            {"target_block_position": 3, "effect_type": "snow"},
        ]
        spine = _spine(1, 2, 3)
        overlays = resolve_generator_overlays(plan, spine)
        clip_vfx = resolve_vfx(plan, spine)

        # Generators go to overlays
        overlay_names = {o["effect_name"] for o in overlays}
        assert overlay_names == {"fireworks", "snow"}

        # Clip effects go to VFX
        vfx_names = {v["effect_type"] for v in clip_vfx}
        assert "advanced_camera_shake" in vfx_names
        assert "fireworks" not in vfx_names
        assert "snow" not in vfx_names

    def test_overlay_ids_are_sequential(self):
        """Overlay IDs are sequential gen_001, gen_002, etc."""
        plan = [
            {"target_block_position": 1, "effect_type": "fireworks"},
            {"target_block_position": 2, "effect_type": "snow"},
            {"target_block_position": 3, "effect_type": "embers"},
        ]
        overlays = resolve_generator_overlays(plan, _spine(1, 2, 3))
        assert [o["overlay_id"] for o in overlays] == ["gen_001", "gen_002", "gen_003"]

    def test_overlay_respects_block_timing(self):
        """Overlay entry inherits timeline_start/end from the spine block."""
        plan = [{"target_block_position": 2, "effect_type": "bubbles"}]
        spine = _spine(1, 2, 3)
        overlays = resolve_generator_overlays(plan, spine)
        assert len(overlays) == 1
        assert overlays[0]["timeline_start"] == 5.0
        assert overlays[0]["timeline_end"] == 10.0

    def test_invalid_block_position_dropped(self):
        """A generator targeting a non-existent block is dropped."""
        plan = [{"target_block_position": 99, "effect_type": "fireworks"}]
        overlays = resolve_generator_overlays(plan, _spine(1, 2))
        assert len(overlays) == 0

    def test_duplicate_block_position_dropped(self):
        """Only one generator per block position is allowed."""
        plan = [
            {"target_block_position": 1, "effect_type": "fireworks"},
            {"target_block_position": 1, "effect_type": "snow"},
        ]
        overlays = resolve_generator_overlays(plan, _spine(1, 2))
        assert len(overlays) == 1
        assert overlays[0]["effect_name"] == "fireworks"

    def test_default_composite_mode_is_screen(self):
        """Default composite mode for generator overlays is screen."""
        plan = [{"target_block_position": 1, "effect_type": "fireworks"}]
        overlays = resolve_generator_overlays(plan, _spine(1, 2))
        assert overlays[0]["composite_mode"] == "screen"

    def test_custom_composite_mode_preserved(self):
        """A plan entry can specify a custom composite mode."""
        plan = [{
            "target_block_position": 1,
            "effect_type": "fireworks",
            "composite_mode": "add",
        }]
        overlays = resolve_generator_overlays(plan, _spine(1, 2))
        assert overlays[0]["composite_mode"] == "add"

    def test_empty_plan_returns_empty(self):
        """An empty creative plan produces no overlays."""
        overlays = resolve_generator_overlays([], _spine(1, 2))
        assert overlays == []

    def test_plan_with_only_clip_effects_returns_empty(self):
        """A plan with only clip effects produces no overlays."""
        plan = [{"target_block_position": 1, "effect_type": "advanced_camera_shake"}]
        overlays = resolve_generator_overlays(plan, _spine(1, 2))
        assert overlays == []

    def test_aliased_generator_resolved(self, monkeypatch):
        """An aliased effect_type that resolves to a generator is routed.

        This used to search `EFFECT_ALIASES` for an entry pointing at a
        generator and skip when it found none.  It never finds one: the
        only alias is `push_in -> zoom_emphasis`, a clip effect, and an
        alias may only RENAME a capability, never choose one - so the
        test skipped in every environment and always would have.  The
        routing it is about is real either way, so the alias is supplied
        here instead of hunted for.
        """
        assert "fireworks" in list_generator_effects()
        monkeypatch.setitem(EFFECT_ALIASES, "firework_burst", "fireworks")
        plan = [{"target_block_position": 1, "effect_type": "firework_burst"}]
        overlays = resolve_generator_overlays(plan, _spine(1, 2))
        assert len(overlays) == 1
        assert overlays[0]["effect_name"] == "fireworks"


# ── Layer 2: Rejection still works ────────────────────────────────

class TestGeneratorStillRejectedFromClipEffects:
    """resolve_vfx must still reject generators from V1 clip effects."""

    def test_generator_rejected_from_vfx(self):
        """A generator in the plan is rejected from resolve_vfx output."""
        plan = [{"target_block_position": 1, "effect_type": "fireworks"}]
        captured = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = captured
        try:
            result = resolve_vfx(plan, _spine(1, 2))
        finally:
            sys.stderr = old_stderr
        assert len(result) == 0
        assert "Rejected generator preset" in captured.getvalue()

    def test_generator_and_clip_effect_both_handled(self):
        """Generators rejected from vfx, but clip effects pass through."""
        plan = [
            {"target_block_position": 1, "effect_type": "fireworks"},
            {"target_block_position": 2, "effect_type": "advanced_camera_shake"},
        ]
        result = resolve_vfx(plan, _spine(1, 2))
        assert len(result) == 1
        assert result[0]["effect_type"] == "advanced_camera_shake"


# ── Layer 3: Data contract ────────────────────────────────────────

class TestGeneratorOverlayDataContract:
    """Generator overlay entries carry all required keys."""

    REQUIRED_KEYS = {
        "overlay_id", "effect_name", "target_block_position",
        "timeline_start", "timeline_end", "composite_mode",
    }

    def test_overlay_entry_has_required_keys(self):
        """Every overlay entry carries all required keys."""
        plan = [{"target_block_position": 1, "effect_type": "fireworks"}]
        overlays = resolve_generator_overlays(plan, _spine(1, 2))
        assert len(overlays) == 1
        missing = self.REQUIRED_KEYS - set(overlays[0].keys())
        assert not missing, f"Missing keys: {missing}"

    def test_overlay_entries_are_sorted_by_timeline(self):
        """Overlay entries are sorted by timeline_start."""
        plan = [
            {"target_block_position": 3, "effect_type": "fireworks"},
            {"target_block_position": 1, "effect_type": "snow"},
        ]
        overlays = resolve_generator_overlays(plan, _spine(1, 2, 3))
        assert overlays[0]["timeline_start"] < overlays[1]["timeline_start"]

    def test_all_111_generators_route_to_overlays(self):
        """Every one of the 111 generator presets is routable as an overlay."""
        generators = list_generator_effects()
        assert len(generators) == 111, f"Expected 111 generators, got {len(generators)}"
        # Pick 5 representative generators to test routing
        sample = list(generators.keys())[:5]
        for name in sample:
            plan = [{"target_block_position": 1, "effect_type": name}]
            overlays = resolve_generator_overlays(plan, _spine(1, 2))
            assert len(overlays) == 1, (
                f"Generator {name} should route to overlay, got {len(overlays)}"
            )
            assert overlays[0]["effect_name"] == name

    def test_generator_classification_consistent(self):
        """is_generator_effect agrees with resolve_generator_overlays routing."""
        generators = list_generator_effects()
        sample = list(generators.keys())[:10]
        for name in sample:
            assert is_generator_effect(name) is True
            plan = [{"target_block_position": 1, "effect_type": name}]
            overlays = resolve_generator_overlays(plan, _spine(1, 2))
            assert len(overlays) == 1


# ── Layer 4: Manifest integration ─────────────────────────────────

class TestManifestIntegration:
    """Generator overlays flow through enhancement_spec to the manifest."""

    def test_post_bridge_emits_generator_overlays(self):
        """The post_bridge output includes generator_overlays when present."""
        from library.steps.step_4_03_plan_vfx.post_bridge import (
            resolve_generator_overlays,
            resolve_vfx,
        )
        creative = [
            {"target_block_position": 1, "effect_type": "fireworks"},
            {"target_block_position": 2, "effect_type": "slow_zoom_in",
             "params": {"zoom_start": 1.0, "zoom_end": 1.04}},
        ]
        spine = _spine(1, 2, 3)
        vfx = resolve_vfx(creative, spine)
        gen_overlays = resolve_generator_overlays(creative, spine)

        output = {"enhancement_spec": {"visual_effects": vfx}}
        if gen_overlays:
            output["enhancement_spec"]["generator_overlays"] = gen_overlays

        # Verify the structure
        spec = output["enhancement_spec"]
        assert "generator_overlays" in spec
        assert len(spec["generator_overlays"]) == 1
        assert spec["generator_overlays"][0]["effect_name"] == "fireworks"
        # The clip effect should be in visual_effects
        assert len(spec["visual_effects"]) == 1
        assert spec["visual_effects"][0]["effect_type"] == "slow_zoom_in"
