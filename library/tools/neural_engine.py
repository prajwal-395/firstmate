"""
Neural Engine AI integration tools for DaVinci Resolve.
Wraps the DaVinci Resolve Scripting API for Neural Engine features.
"""

import sys

# Magic Mask has no wrapper here on purpose. `TimelineItem.CreateMagicMask`
# exists in the API and returns False for every mode ("F", "B", "BI") on
# the supported Resolve build, on the Color page and off it, and
# RegenerateMagicMask likewise. Nothing can honour a magic_mask directive,
# so compile_manifest does not emit one. Resolve's own capability study
# reaches the same conclusion for rotoscoping and motion tracking - see
# research/drp_reverse_engineering.md.

# Smart Reframe has no wrapper here either, and the manifest no longer
# carries a `smart_reframe` key. The wrapper that used to live here was
# the last surviving instance of the bug the neural-directive block in
# `resolve_build_timeline` was fixed for: it guarded on
# `hasattr(clip, 'SmartReframe')`, which is True for every name on a
# Resolve proxy including invented ones, called the method, and the one
# caller discarded the answer and printed "✓ Applied Smart Reframe"
# unconditionally. It was also handed a Timeline rather than a clip, and
# Resolve exposes Smart Reframe as a ResolveFX plugin, not as a scripting
# method on Timeline or TimelineItem. Its own manifest entry said
# `"unverified_by_design": true, "status": "behaviour unknown"`. Nothing
# in the repository ever confirmed it did anything, and framing is now a
# per-clip creative parameter delivered by `_apply_conform`
# (`framing_intent`/`framing_pan_x`), which a working Smart Reframe would
# have fought. Do not re-add a wrapper without a render that proves the
# call changes the picture.

# `hasattr` is useless on Resolve's scripting proxies: every attribute
# lookup succeeds, including invented ones. The wrappers below therefore
# call the method and judge it by its return value.


def apply_super_scale(clip, scale_factor=2, sharpness="Medium", noise_reduction="Medium"):
    """
    AI upscaling using Super Scale.

    Super Scale is a MEDIA POOL item property, not a timeline item one -
    setting it on the timeline item returned False and changed nothing.
    The value must be an int; the string "2" is rejected. The companion
    property names are "SuperScale Sharpness"/"SuperScale Noise Reduction"
    (no space after Super), which is why the old spellings never took.

    Known quirk: 3x and 4x modes may not reliably switch to "Enhanced"
    mode. Start with 2x.
    """
    try:
        media_item = None
        try:
            media_item = clip.GetMediaPoolItem()
        except Exception:
            pass
        target = media_item or clip
        success = target.SetClipProperty("Super Scale", int(scale_factor))
        if success:
            target.SetClipProperty("SuperScale Sharpness", sharpness)
            target.SetClipProperty("SuperScale Noise Reduction", noise_reduction)
        else:
            print(f"Warning: Failed to set Super Scale on {clip.GetName()}", file=sys.stderr)
        return bool(success)
    except Exception as e:
        print(f"Error applying Super Scale to {clip.GetName()}: {e}", file=sys.stderr)
        return False


def apply_stabilization(clip, mode="perspective"):
    """
    Neural Engine stabilization. `TimelineItem.Stabilize()` runs the
    analysis and returns whether it succeeded.
    """
    try:
        return bool(clip.Stabilize())
    except Exception as e:
        print(f"Error applying Stabilization to {clip.GetName()}: {e}", file=sys.stderr)
        return False
