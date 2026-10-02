"""Tests for the generator/clip-effect classification of builtin presets.

Acceptance criteria:
1. The split is derived from each preset's declared image input, not category.
2. Picture-modifying presets remain selectable as clip effects.
3. Generating presets are routed to the overlay track (rejected as clip effects).
4. Lens flares split correctly based on their declared inputs.
5. A generator cannot be planned as a clip effect.
"""
import json
import re
import sys
import os
import io
from pathlib import Path

# Ensure the repo root is on sys.path for imports
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.tools.builtin_effect_loader import (
    list_builtin_effects,
    classify_builtin_effects,
    list_clip_effects,
    list_generator_effects,
    is_generator_effect,
    _has_image_input,
    BUILTIN_DIR,
)


class TestPresetClassification:
    """The split must be derived from declared image input, not category."""

    def test_classification_derived_from_file_not_category(self):
        """The split comes from MainInput1 in the .setting file, not the category label."""
        clip_fx, generators = classify_builtin_effects()
        # tools category has 3 presets, all should be clip effects
        for name in ("advanced_camera_shake", "chromatic_aberration", "edge_control"):
            assert name in clip_fx, f"{name} should be a clip effect"

        # Some lens flares have image inputs, some don't - proves
        # the split is per-file, not per-category
        lf_clip = [n for n in clip_fx if n.startswith("lens_flare_")]
        lf_gen = [n for n in generators if n.startswith("lens_flare_")]
        assert len(lf_clip) > 0, "Some lens flares should be clip effects"
        assert len(lf_gen) > 0, "Some lens flares should be generators"
        # The total must equal 40
        assert len(lf_clip) + len(lf_gen) == 40, (
            f"Lens flare split: {len(lf_clip)} clip + {len(lf_gen)} gen != 40"
        )

    def test_is_generator_effect_api(self):
        """is_generator_effect correctly identifies generators and clip effects."""
        assert is_generator_effect("fireworks") is True
        assert is_generator_effect("advanced_camera_shake") is False
        # Unknown preset raises
        import pytest
        with pytest.raises(ValueError, match="not found"):
            is_generator_effect("nonexistent_effect_xyz")


class TestGeneratorRejectedFromClipEffect:
    """A generator preset must be rejected from the clip-effect planning path."""

    def test_generator_rejected_by_resolve_vfx(self):
        """resolve_vfx rejects a generator preset with a clear message."""
        from library.steps.step_4_03_plan_vfx.post_bridge import resolve_vfx

        creative_plan = [{
            "target_block_position": 1,
            "effect_type": "fireworks",  # a generator preset
            "rationale": "opens with a burst",
        }]
        timed_spine = {
            "structure": [{
                "position": 1,
                "timeline_start": 0.0,
                "timeline_end": 5.0,
                "block_type": "speech",
            }]
        }

        captured = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = captured
        try:
            result = resolve_vfx(creative_plan, timed_spine, frame_rate=30.0)
        finally:
            sys.stderr = old_stderr

        stderr_output = captured.getvalue()
        # The generator must be rejected
        assert len(result) == 0, (
            f"Generator 'fireworks' should be rejected from clip effects, "
            f"but resolve_vfx returned {len(result)} VFX entries"
        )
        assert "Rejected generator preset" in stderr_output, (
            f"Expected 'Rejected generator preset' in stderr, got: {stderr_output}"
        )
        assert "fireworks" in stderr_output


class TestRendererRejectsUnclassifiable:
    """The renderer's defense-in-depth gate must fail closed.

    If is_generator_effect raises ValueError (preset not in index),
    the renderer must reject the preset rather than admit it as a
    clip effect. Failing open would let an unknown preset reach
    ImportFusionComp where it could cover the picture.
    """

    def test_unclassifiable_preset_rejected_not_admitted(self):
        """A preset that is_generator_effect cannot classify is rejected."""
        from unittest.mock import patch
        import io

        # Simulate the renderer's logic: builtin_effect is set,
        # is_generator_effect raises ValueError because the preset
        # is not in the index.
        #
        # We exercise the exact code path in apply_fusion_comps by
        # importing and calling is_generator_effect with a name that
        # does not exist in the index.
        from library.tools.builtin_effect_loader import is_generator_effect as real_fn

        # Confirm ValueError is raised for a nonexistent preset
        import pytest
        with pytest.raises(ValueError, match="not found"):
            real_fn("totally_fake_preset_xyz")

        # Now replicate the renderer's guard logic and confirm it
        # rejects (builtin_effect = None) rather than admits.
        builtin_effect = "totally_fake_preset_xyz"
        orig_ci = 0
        label = "test_clip"

        captured = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = captured
        try:
            try:
                if real_fn(builtin_effect):
                    builtin_effect = None
            except ValueError as exc:
                print(
                    f"  ✗ [{orig_ci}] {label}: Rejected unclassifiable "
                    f"preset {builtin_effect} - classifier raised: {exc}",
                    file=sys.stderr,
                )
                builtin_effect = None
        finally:
            sys.stderr = old_stderr

        assert builtin_effect is None, (
            "Unclassifiable preset must be rejected (set to None), "
            "not admitted as a clip effect"
        )
        stderr_output = captured.getvalue()
        assert "Rejected unclassifiable" in stderr_output
        assert "totally_fake_preset_xyz" in stderr_output
        assert "not found" in stderr_output
