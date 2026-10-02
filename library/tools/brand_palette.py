"""Which entry of a brand palette is the text, the outline, the accent.

One rule, in one place. Captions ask (P3.2) and motion graphics ask
(P3.1), and if they answered differently the same video would carry two
different "brand" colours - which is the sort of drift a shared palette
exists to prevent.

The rule, stated so it is arguable rather than magic:

- **text** is the LIGHTEST entry. Captions and titles sit over footage,
  and light text with a dark outline is what reads.
- **outline** is the DARKEST entry.
- **accent** is the most SATURATED entry, which is what a palette's brand
  colour almost always is - but only if it would actually be visible.

That last qualification is the whole subtlety. A dark accent only reads if
it is vivid. A cinematic-style palette like
`["#223344", "#aabbcc", "#111111"]`, whose most saturated entry is a dark
muted navy that would sit almost on top of its own near-black outline and
read as a smudge; a shortform-style one names `#ff0055`, just as dark by
luminance and unmistakable on screen. Luminance alone cannot tell those
apart, which is why vividness is the second term.

Anything the palette cannot supply is simply absent from the result, so
callers keep their own value rather than being handed a bad one.
"""

from typing import Dict, List, Optional, Sequence

# A palette entry must be at least this saturated to serve as an accent;
# below it, it is a neutral and would vanish into the text.
MIN_ACCENT_SATURATION = 0.25

# Below this luminance an entry must also clear MIN_VIVID_SATURATION, or
# it is rejected: a dark accent only reads on screen if it is vivid.
MIN_ACCENT_LUMINANCE = 0.25
MIN_VIVID_SATURATION = 0.80


def hex_to_rgb(value: str) -> Optional[tuple]:
    text = (value or "").strip().lstrip("#")
    if len(text) == 3:
        text = "".join(c * 2 for c in text)
    if len(text) != 6:
        return None
    try:
        return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None


def luminance(rgb: Sequence[int]) -> float:
    r, g, b = rgb
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0


def saturation(rgb: Sequence[int]) -> float:
    hi, lo = max(rgb), min(rgb)
    if hi == 0:
        return 0.0
    return (hi - lo) / float(hi)


def is_usable_accent(rgb: Sequence[int]) -> bool:
    """Would this colour read as emphasis on screen?"""
    sat = saturation(rgb)
    if sat < MIN_ACCENT_SATURATION:
        return False
    if luminance(rgb) < MIN_ACCENT_LUMINANCE and sat < MIN_VIVID_SATURATION:
        return False
    return True


def roles_from_palette(palette: Optional[List[str]]) -> Dict[str, str]:
    """Map a brand palette onto {"text", "outline", "accent"}.

    Keys are omitted rather than guessed: a one-colour or malformed
    palette degrades to the caller's own values instead of failing or
    inventing.
    """
    parsed = []
    for entry in palette or []:
        rgb = hex_to_rgb(entry) if isinstance(entry, str) else None
        if rgb is not None:
            parsed.append((entry.strip(), rgb))
    if not parsed:
        return {}

    out = {
        "text": max(parsed, key=lambda p: luminance(p[1]))[0],
        "outline": min(parsed, key=lambda p: luminance(p[1]))[0],
    }
    candidate = max(parsed, key=lambda p: saturation(p[1]))
    if is_usable_accent(candidate[1]):
        out["accent"] = candidate[0]
    return out


def accent_color(
    palette: Optional[List[str]],
    fallback: str,
) -> str:
    """The brand accent, or `fallback` when the palette has no usable one."""
    return roles_from_palette(palette).get("accent", fallback)


def describe_palette_state(palette: Optional[List[str]]) -> dict:
    """The palette as a run record reads it: entries, roles, accent.

    A palette that resolves `text`/`outline` but no usable `accent` is
    the shape that drew fifteen unusable graphics on one project: every
    entry asking for the brand's colour got the lightest entry of a
    palette nobody chose for that series, and nothing on the run said
    so before the renders. The state is REPORTED, never a gate - a
    template refines and does not gate - so this returns facts
    (`has_usable_accent`, the resolved `roles`) and the caller records
    them on its own output. `tests/unit/captions/test_motion_graphics_plan.py`.
    """
    entries = [entry.strip() for entry in (palette or [])
               if isinstance(entry, str) and hex_to_rgb(entry) is not None]
    roles = roles_from_palette(palette)
    return {
        "entries": entries,
        "roles": roles,
        "has_usable_accent": "accent" in roles,
    }
