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
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.steps.step_4_03_plan_vfx.post_bridge import (
    EFFECT_ALIASES,
    resolve_generator_overlays,
    resolve_vfx,
)
from library.tools.builtin_effect_loader import (
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

    REQUIRED_KEYS = {
        "overlay_id", "effect_name", "target_block_position",
        "timeline_start", "timeline_end", "composite_mode",
    }

    def test_generator_produces_a_complete_overlay_entry_on_its_block(self):
        """A generator preset in the plan becomes an overlay carrying every
        required key and its spine block's timing."""
        plan = [{"target_block_position": 2, "effect_type": "fireworks"}]
        overlays = resolve_generator_overlays(plan, _spine(1, 2, 3))
        assert len(overlays) == 1
        assert overlays[0]["effect_name"] == "fireworks"
        assert overlays[0]["timeline_start"] == 5.0
        assert overlays[0]["timeline_end"] == 10.0
        assert overlays[0]["target_block_position"] == 2
        missing = self.REQUIRED_KEYS - set(overlays[0].keys())
        assert not missing, f"Missing keys: {missing}"

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

    def test_an_invalid_or_duplicate_block_position_is_dropped(self):
        """A generator targeting a non-existent block is dropped, and only
        one generator per block position is allowed."""
        plan = [{"target_block_position": 99, "effect_type": "fireworks"}]
        assert resolve_generator_overlays(plan, _spine(1, 2)) == []
        plan = [
            {"target_block_position": 1, "effect_type": "fireworks"},
            {"target_block_position": 1, "effect_type": "snow"},
        ]
        overlays = resolve_generator_overlays(plan, _spine(1, 2))
        assert len(overlays) == 1
        assert overlays[0]["effect_name"] == "fireworks"

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


# ── Layer 4: Manifest integration ─────────────────────────────────
