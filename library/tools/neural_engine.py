"""
Neural Engine AI integration tools for DaVinci Resolve.
Wraps the DaVinci Resolve Scripting API for Neural Engine features.
"""

import sys

def apply_magic_mask(clip, mode="F"):
    """
    Create a Neural Engine Magic Mask on a clip.
    Mode "F" = forward tracking, "B" = backward, "FB" = bidirectional.
    """
    try:
        # Some API versions use CreateMagicMask, some might use ApplyMagicMask
        if hasattr(clip, 'CreateMagicMask'):
            result = clip.CreateMagicMask(mode)
            if not result:
                print(f"Warning: CreateMagicMask returned False for clip {clip.GetName()}", file=sys.stderr)
            return bool(result)
        return False
    except Exception as e:
        print(f"Error applying Magic Mask to {clip.GetName()}: {e}", file=sys.stderr)
        return False


def apply_smart_reframe(clip, target_aspect="9:16"):
    """
    AI-driven reframing for different aspect ratios.
    """
    try:
        # The API can be tricky here, usually SmartReframe is a method or property
        if hasattr(clip, 'SmartReframe'):
            result = clip.SmartReframe() # Often doesn't take args directly, or takes dict
            return bool(result)
        elif hasattr(clip, 'SetClipProperty'):
            result = clip.SetClipProperty('Smart Reframe', target_aspect)
            return bool(result)
        elif hasattr(clip, 'SetProperty'):
            result = clip.SetProperty('Smart Reframe', target_aspect)
            return bool(result)
        return False
    except Exception as e:
        print(f"Error applying Smart Reframe to {clip.GetName()}: {e}", file=sys.stderr)
        return False


def apply_super_scale(clip, scale_factor=2, sharpness="medium", noise_reduction="medium"):
    """
    AI upscaling using Super Scale.
    Known quirk: 3x and 4x modes may not reliably switch to "Enhanced" mode. Start with 2x.
    """
    try:
        success = False
        if hasattr(clip, 'SetClipProperty'):
            success = clip.SetClipProperty("Super Scale", scale_factor)
            if success:
                clip.SetClipProperty("Super Scale Sharpness", sharpness)
                clip.SetClipProperty("Super Scale Noise Reduction", noise_reduction)
        elif hasattr(clip, 'SetProperty'):
            success = clip.SetProperty("Super Scale", scale_factor)
            if success:
                clip.SetProperty("Super Scale Sharpness", sharpness)
                clip.SetProperty("Super Scale Noise Reduction", noise_reduction)
                
        if not success:
            print(f"Warning: Failed to set Super Scale on {clip.GetName()}", file=sys.stderr)
        return bool(success)
    except Exception as e:
        print(f"Error applying Super Scale to {clip.GetName()}: {e}", file=sys.stderr)
        return False


def apply_stabilization(clip, mode="perspective"):
    """
    Neural Engine stabilization.
    """
    try:
        if hasattr(clip, 'Stabilize'):
            result = clip.Stabilize()
            return bool(result)
        elif hasattr(clip, 'SetClipProperty'):
            result = clip.SetClipProperty("Stabilization Mode", mode)
            # Typically need to trigger analysis if possible
            return bool(result)
        elif hasattr(clip, 'SetProperty'):
            result = clip.SetProperty("Stabilization Mode", mode)
            return bool(result)
        return False
    except Exception as e:
        print(f"Error applying Stabilization to {clip.GetName()}: {e}", file=sys.stderr)
        return False
