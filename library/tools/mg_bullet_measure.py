"""Bullet uniformity off rendered motion-graphics pixels.

The captain's Reels 19/24/25 complaint (2026-09-19) was that bullet
sizes VARY within one animation. The cause lived in the renderer, and
every read-back echoed it as declared - so the only honest check is
the rendered pixels, never the template source or a stored value.

`body_heights` measures one glyph x-height per text line the way
`draw_gain_probe` does: lit rows group into bands (one per list
item); within a band each connected ink component is one glyph, and
the x-height glyphs are the numerous short ones - caps, ascenders and
descenders are the tall minority. The median component height is the
size the eye reads, robust to which letters each item happens to
contain.

Uniform siblings measure within 1px of each other. The pre-fix ladder
measured 33 vs 21 (ratio 1.57, the 56/36 display/supporting ladder).
"""

from __future__ import annotations


def body_heights(png_path: str) -> list:
    """One body height per text line in `png_path`, in pixels.

    Raises `AssertionError` naming the band when a band holds no
    glyph-like component - a measurement that cannot see is refused,
    not zeroed.
    """
    import numpy as np
    from PIL import Image
    from scipy import ndimage

    image = np.asarray(Image.open(png_path).convert("RGB")).astype(int)
    lit = image.sum(axis=2) > 300
    rows = lit.any(axis=1)
    bands = []
    start = None
    for y, hit in enumerate(rows):
        if hit and start is None:
            start = y
        elif not hit and start is not None:
            if y - start >= 3:
                bands.append((start, y - 1))
            start = None
    if start is not None:
        bands.append((start, len(rows) - 1))
    bodies = []
    for low, high in bands:
        band = lit[low : high + 1, :]
        labels, _ = ndimage.label(band)
        found = ndimage.find_objects(labels)
        heights = sorted(
            (box[0].stop - box[0].start)
            for box in found
            if (labels[box] > 0).sum() >= 15
        )
        assert heights, f"no glyphs in band {low}-{high}"
        bodies.append(heights[len(heights) // 2])
    return bodies


def siblings_uniform(bodies: list, tolerance_px: int = 1) -> bool:
    """Whether measured sibling heights read as one size."""
    if not bodies:
        return False
    return max(bodies) - min(bodies) <= tolerance_px
