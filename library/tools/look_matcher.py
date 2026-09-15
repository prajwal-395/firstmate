import os
import math

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


class LookMatchUnavailable(RuntimeError):
    """A frame's colours could not be measured, so nothing may be matched.

    Raised rather than returning a neutral-looking default: the caller
    turns these statistics into a CDL it applies to a clip, and a
    default that reads as "measured neutral" would grade the clip from
    fiction while the record says "AI Look Match CDL applied".
    """


def analyze_frame_colors(image_path: str) -> dict:
    """
    Extract 3-zone RGB statistics from an image.
    Zones: shadows < 0.2, midtones 0.2-0.7, highlights > 0.7
    Returns: {"shadows": [R, G, B], "midtones": [R, G, B], "highlights": [R, G, B]}
    where values are mean normalized float RGB per zone.

    Raises `LookMatchUnavailable` when Pillow is missing, and lets file
    errors (`FileNotFoundError`, `UnidentifiedImageError`, `OSError`)
    propagate with their real types: a frame that cannot be decoded
    must refuse the match, never stand in for a measured one.
    """
    if not PIL_AVAILABLE:
        raise LookMatchUnavailable(
            "Pillow is not installed, so no frame's colours can be "
            "measured. It is declared in requirements.txt (Pillow>=9.0): "
            "pip install Pillow>=9.0")

    img = Image.open(image_path).convert('RGB')
    # To avoid being too slow, we can resize the image first
    img.thumbnail((256, 256))
    pixels = list(img.getdata())

    shadow_sums = [0.0, 0.0, 0.0]
    mid_sums = [0.0, 0.0, 0.0]
    high_sums = [0.0, 0.0, 0.0]

    shadow_count = 0
    mid_count = 0
    high_count = 0

    for r, g, b in pixels:
        # normalize
        nr, ng, nb = r / 255.0, g / 255.0, b / 255.0
        # luminance approximation for zoning
        lum = 0.2126 * nr + 0.7152 * ng + 0.0722 * nb

        if lum < 0.2:
            shadow_sums[0] += nr
            shadow_sums[1] += ng
            shadow_sums[2] += nb
            shadow_count += 1
        elif lum > 0.7:
            high_sums[0] += nr
            high_sums[1] += ng
            high_sums[2] += nb
            high_count += 1
        else:
            mid_sums[0] += nr
            mid_sums[1] += ng
            mid_sums[2] += nb
            mid_count += 1

    def safe_mean(sums, count):
        if count == 0:
            return [0.0, 0.0, 0.0]
        return [sums[0] / count, sums[1] / count, sums[2] / count]

    return {
        "shadows": safe_mean(shadow_sums, shadow_count),
        "midtones": safe_mean(mid_sums, mid_count),
        "highlights": safe_mean(high_sums, high_count)
    }

def clamp(val, min_val, max_val):
    return max(min_val, min(val, max_val))

def compute_match_cdl(reference_stats: dict, target_stats: dict) -> dict:
    """
    Compute ASC CDL parameters to match target to reference.
    slope_r = ref_highlight_r / max(target_highlight_r, 0.001)
    offset_r = ref_shadow_r - target_shadow_r
    power_r = log(max(ref_mid_r, 0.001)) / log(max(target_mid_r, 0.001))
    Returns {"slope": [r,g,b], "offset": [r,g,b], "power": [r,g,b], "saturation": float}
    Clamped to ranges: slope [0.1, 10.0], offset [-0.5, 0.5], power [0.1, 4.0]
    """
    slope = [0.0, 0.0, 0.0]
    offset = [0.0, 0.0, 0.0]
    power = [0.0, 0.0, 0.0]
    
    for i in range(3):
        ref_high = reference_stats["highlights"][i]
        tgt_high = target_stats["highlights"][i]
        
        ref_shadow = reference_stats["shadows"][i]
        tgt_shadow = target_stats["shadows"][i]
        
        ref_mid = reference_stats["midtones"][i]
        tgt_mid = target_stats["midtones"][i]
        
        # Calculate CDL
        raw_slope = ref_high / max(tgt_high, 0.001)
        raw_offset = ref_shadow - tgt_shadow
        # power should be computed so that: tgt_mid ^ power = ref_mid. 
        # log(ref_mid) / log(tgt_mid)
        # using base e for math.log
        raw_power = math.log(max(ref_mid, 0.001)) / math.log(max(tgt_mid, 0.001))
        
        # Clamp
        slope[i] = clamp(raw_slope, 0.1, 10.0)
        offset[i] = clamp(raw_offset, -0.5, 0.5)
        power[i] = clamp(raw_power, 0.1, 4.0)
        
    return {
        "slope": slope,
        "offset": offset,
        "power": power,
        "saturation": 1.0  # Default saturation
    }

def match_clips_to_reference(reference_image: str, clip_frames: dict) -> dict:
    """
    For each clip, extract frame stats, compute CDL match against reference.
    Returns per-clip CDL specs mapping clip ID to CDL dict.

    Raises whatever `analyze_frame_colors` raises when the reference or
    any clip frame cannot be measured: a match from unmeasured colours
    is fiction, so the match refuses rather than returning one.
    """
    ref_stats = analyze_frame_colors(reference_image)
    results = {}
    
    for clip_id, frame_path in clip_frames.items():
        if not os.path.exists(frame_path):
            raise FileNotFoundError(
                f"Cannot match clip {clip_id!r}: frame path "
                f"{frame_path!r} does not exist. A match from an "
                f"unmeasured frame is fiction, so the match refuses "
                f"rather than returning an identity CDL.")
        target_stats = analyze_frame_colors(frame_path)
        cdl = compute_match_cdl(ref_stats, target_stats)
        results[clip_id] = cdl
            
    return results
