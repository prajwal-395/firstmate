"""Where each short-form app paints its own UI over a 1080x1920 frame. One enumeration.

The captain, 2026-09-25: "add an overlay for the bounds of tiktok, and
instagram reels, and youtube shorts, and linkedin reels that allows us to
make sure our video and all of the elements in it are positioned outside
of the boundings of where the UI elements for these apps are (create
seperate overlays for each app as well as one that combines all of the
bounding together)".

This module is that data, the overlays drawn from it, and the check that
reads it.  It is a GUIDE: nothing here moves an element.  The layout
rules that position captions and graphics stay in ``safe_area.py`` (one
strictest profile, the captain's 2026-08-25 ruling); this file is what an
editor, or a check, holds a built reel up against.

Each platform is a list of COVERED rectangles on the 1080x1920 reference
frame - the bands its interface sits on - rather than four insets,
because TikTok's action rail is a notch, not an edge.  The combined
overlay is the union of all four platforms' rectangles.

Where each number comes from:

* TikTok, Instagram Reels and YouTube Shorts are MEASURED on the
  captain's own iPhone screenshots of each app (2026-09-25), because he
  asked that the bounds match what his phone shows, and where those and
  the platforms' published ad guidance disagree the screenshots win.
  They disagree a lot: the published numbers are ADS guidance and
  reserve far more of the bottom (660-672px) than the organic feed
  covers (260-466px here). Each row's ``source`` carries both readings,
  the difference, and the screenshot coordinates it was measured from;
  :data:`SCREENSHOT` states the screen-to-frame mapping.
* LinkedIn - NOT official and not measured. LinkedIn's video specs
  (https://business.linkedin.com/advertise/ads/sponsored-content/video-ads/specs)
  give the 9:16 frame and no margins. The numbers are the AI Carousels
  "LinkedIn Safe Zone Checker"
  (https://www.aicarousels.com/free-tools/linkedin-safe-zone-checker),
  citing LinkedIn's 2025 mobile layout: top 108, bottom 320, left 60,
  right 120.  ``basis`` says so on the row.

The overlays live at ``library/presets/safe-zones/`` and are GENERATED
from this table (``python3 -m library.tools.platform_safe_zones --write``);
``tests/test_platform_safe_zones.py`` fails when a PNG no longer draws
the table.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass

#: The frame every rectangle below is stated on. A different delivery
#: size scales the rectangles; the platforms publish for this one.
REFERENCE_SIZE = (1080, 1920)

Rect = tuple[int, int, int, int]  # (x0, y0, x1, y1), x1/y1 exclusive


@dataclass(frozen=True)
class PlatformZones:
    """One app's covered bands on the reference frame, and their source."""

    key: str
    label: str
    zones: tuple[tuple[str, Rect], ...]  # (what the UI is there, rect)
    colour: tuple[int, int, int]
    basis: str  # "measured" (screenshot), "published", "third-party"
    source: str
    source_date: str

    def safe_rect(self) -> Rect:
        """The largest edge-inset rectangle clear of every EDGE band.

        A notch (TikTok's rail) is not an edge, so it is not in this
        rectangle's answer - check a box with :func:`intrusions`.
        """
        w, h = REFERENCE_SIZE
        x0, y0, x1, y1 = 0, 0, w, h
        for name, (a, b, c, d) in self.zones:
            if name == "top":
                y0 = max(y0, d)
            elif name == "bottom":
                y1 = min(y1, b)
            elif name == "left":
                x0 = max(x0, c)
            elif name == "right":
                x1 = min(x1, a)
        return (x0, y0, x1, y1)


def _edges(top: int, bottom: int, left: int,
           right: int) -> tuple[tuple[str, Rect], ...]:
    w, h = REFERENCE_SIZE
    return (
        ("top", (0, 0, w, top)),
        ("bottom", (0, h - bottom, w, h)),
        ("left", (0, top, left, h - bottom)),
        ("right", (w - right, top, w, h - bottom)),
    )


#: What the UI in each band is, where the platform says so.
UI_IN_BAND = {
    "top": "top bar (status, tabs, search)",
    "bottom": "caption, profile, audio and CTA",
    "left": "edge margin, or the strip the app's cover crop cuts off",
    "right": "edge margin, or the strip the app's cover crop cuts off",
    "rail": "action rail (like, comment, share)",
}

#: How a phone screenshot maps onto the 1080x1920 frame. Each app scales
#: the 9:16 frame to COVER its video region - the screen from the top
#: down to where the app's own opaque bar begins - so the frame is cut
#: equally left and right and nothing top or bottom. Measured on the
#: captain's own iPhone captures (920x2000, 2026-09-25):
#:   TikTok  region y 0..1728 (the "Search" strip below is opaque)
#:   Shorts  region y 0..1810 (the nav bar below is opaque)
#:   Reels   region y 0..1748 (below it is a blurred smear the app
#:           draws under its progress bar and nav pill, not picture -
#:           detail energy drops from ~13 to <1 across y1744..1756)
#: A screenshot point (x, y) is frame (x + crop) / scale, y / scale,
#: scale = region / 1920, crop = (1080 * scale - 920) / 2.
SCREENSHOT = (920, 2000)


def _measured(top: int, bottom: int, crop: int,
              rail: tuple[int, int]) -> tuple[tuple[str, Rect], ...]:
    """Bands measured off a screenshot: the app's top UI, its bottom
    caption/profile block, the two side strips the cover crop cuts off
    screen, and the action rail from its left edge and first icon down
    to the bottom block."""
    w, h = REFERENCE_SIZE
    rail_x, rail_y = rail
    return _edges(top, bottom, crop, crop) + (
        ("rail", (rail_x, rail_y, w, h - bottom)),)


TIKTOK = PlatformZones(
    key="tiktok",
    label="TikTok",
    zones=_measured(267, 309, 29, (918, 767)),
    colour=(0, 242, 234),
    basis="measured",
    source=("MEASURED on the captain's iPhone screenshot (phone-tiktok.png, "
            "920x2000, 2026-09-25): tabs row ends y236, caption block "
            "starts y1450, rail from x800 y690, region 0..1728 -> scale "
            "0.900. Published (TikTok Ads Help Center In-Feed template, "
            "https://ads.tiktok.com/help/article/tiktok-auction-in-feed-ads, "
            "updated June 2026): top 240, bottom 660, sides 120, rail "
            "300 wide from y840. Screenshot vs published: top +27, bottom "
            "-351, side crop 29 vs 120, rail 162 wide from y767."),
    source_date="screenshot 2026-09-25",
)

INSTAGRAM_REELS = PlatformZones(
    key="instagram_reels",
    label="Instagram Reels",
    zones=_measured(110, 256, 35, (930, 1115)),
    colour=(225, 48, 108),
    basis="measured",
    source=("MEASURED on the captain's iPhone screenshot "
            "(phone-ig-reels.png, 920x2000, 2026-09-25): status bar ends "
            "y100, profile row starts y1515, rail from x815 y1015, video "
            "region 0..1748 (the blurred band below it is not picture - "
            "the captain's catch) -> scale 0.910. Published (Meta Ads "
            "Guide, https://www.facebook.com/business/ads-guide/update/"
            "video/instagram-reels): top 14% (269), bottom 35% (672), "
            "sides 6% (65). Screenshot vs published: top -159, bottom "
            "-416, side crop 35 vs 65, rail 150 wide from y1115."),
    source_date="screenshot 2026-09-25",
)

YOUTUBE_SHORTS = PlatformZones(
    key="youtube_shorts",
    label="YouTube Shorts",
    zones=_measured(239, 260, 52, (909, 1064)),
    colour=(255, 48, 48),
    basis="measured",
    source=("MEASURED on the captain's iPhone screenshot "
            "(phone-yt-shorts.png, 920x2000, 2026-09-25): search/menu "
            "icons end y225, channel row starts y1565, rail from x808 "
            "y1003, region 0..1810 -> scale 0.943. Published (Google Ads "
            "Help, https://support.google.com/google-ads/answer/9128498): "
            "top 288, bottom 672, left 48, right 192. Screenshot vs "
            "published: top -49, bottom -412, side crop 52 vs 48/192, "
            "rail 171 wide from y1064."),
    source_date="screenshot 2026-09-25",
)

LINKEDIN = PlatformZones(
    key="linkedin",
    label="LinkedIn",
    zones=_edges(108, 320, 60, 120),
    colour=(10, 102, 194),
    basis="third-party",
    source=("NOT OFFICIAL - LinkedIn publishes no vertical safe zone. "
            "AI Carousels 'LinkedIn Safe Zone Checker' (LinkedIn 2025 "
            "mobile layout). "
            "https://www.aicarousels.com/free-tools/"
            "linkedin-safe-zone-checker"),
    source_date="undated page; accessed 2026-09-25",
)

PLATFORMS: dict[str, PlatformZones] = {
    p.key: p for p in (TIKTOK, INSTAGRAM_REELS, YOUTUBE_SHORTS, LINKEDIN)
}

#: The overlay that is every platform at once.
COMBINED = "combined"

OVERLAY_NAMES = tuple(PLATFORMS) + (COMBINED,)


def platforms_for(name: str) -> list[PlatformZones]:
    """The platforms one overlay name covers; an unknown name raises."""
    if name == COMBINED:
        return list(PLATFORMS.values())
    if name not in PLATFORMS:
        raise KeyError(f"no safe-zone overlay {name!r}; known: "
                       f"{list(OVERLAY_NAMES)}")
    return [PLATFORMS[name]]


def _scale(rect: Rect, width: int, height: int) -> Rect:
    sx = width / REFERENCE_SIZE[0]
    sy = height / REFERENCE_SIZE[1]
    return (round(rect[0] * sx), round(rect[1] * sy),
            round(rect[2] * sx), round(rect[3] * sy))


def intrusions(box: Rect, name: str = COMBINED,
               frame: tuple[int, int] = REFERENCE_SIZE) -> list[dict]:
    """Every platform band ``box`` overlaps, with the overlap in pixels.

    ``box`` is ``(x0, y0, x1, y1)`` in ``frame`` pixels.  Empty means the
    box clears every zone of the named overlay.
    """
    out: list[dict] = []
    x0, y0, x1, y1 = box
    for platform in platforms_for(name):
        for band, rect in platform.zones:
            a, b, c, d = _scale(rect, *frame)
            ox = min(x1, c) - max(x0, a)
            oy = min(y1, d) - max(y0, b)
            if ox > 0 and oy > 0:
                out.append({"platform": platform.key, "band": band,
                            "ui": UI_IN_BAND[band],
                            "overlap": (max(x0, a), max(y0, b),
                                        min(x1, c), min(y1, d))})
    return out


def covered_mask(name: str, width: int, height: int):
    """A boolean numpy mask of every covered pixel of the named overlay."""
    import numpy as np

    mask = np.zeros((height, width), dtype=bool)
    for platform in platforms_for(name):
        for _band, rect in platform.zones:
            a, b, c, d = _scale(rect, width, height)
            mask[b:d, a:c] = True
    return mask


# ── Drawing ─────────────────────────────────────────────────────────

#: Opacity of a covered band, and of a platform's boundary line.
FILL_ALPHA = 70
LINE_ALPHA = 235
LINE_WIDTH = 4


def _font(size: int):
    from PIL import ImageFont

    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "..", "..", "remotion-subtitles", "public",
                        "fonts", "Montserrat-Variable.ttf")
    try:
        font = ImageFont.truetype(os.path.normpath(path), size)
        try:
            font.set_variation_by_axes([700])
        except (OSError, AttributeError):
            pass
        return font
    except OSError:
        return ImageFont.load_default()


def _outline_inside_bands(draw, platform: PlatformZones, width: int,
                          height: int, rgba) -> None:
    """Trace the platform's safe boundary on the COVERED side of it.

    Every line pixel lies inside one of the platform's bands, so a pixel
    the overlay touches is always a covered pixel - which is what lets
    the test compare the PNG to the table exactly.
    """
    for band, rect in platform.zones:
        a, b, c, d = _scale(rect, width, height)
        if band == "top":
            draw.rectangle((a, d - LINE_WIDTH, c - 1, d - 1), fill=rgba)
        elif band == "bottom":
            draw.rectangle((a, b, c - 1, b + LINE_WIDTH - 1), fill=rgba)
        elif band == "left":
            draw.rectangle((c - LINE_WIDTH, b, c - 1, d - 1), fill=rgba)
        elif band == "right":
            draw.rectangle((a, b, a + LINE_WIDTH - 1, d - 1), fill=rgba)
        elif band == "rail":
            draw.rectangle((a, b, c - 1, d - 1), outline=rgba,
                           width=LINE_WIDTH)


def render_overlay(name: str, width: int = REFERENCE_SIZE[0],
                   height: int = REFERENCE_SIZE[1]):
    """The guide overlay for ``name`` as a transparent RGBA PIL image.

    Covered bands are a translucent wash in the platform's colour (a
    neutral wash for the combined overlay), each platform's boundary is
    a solid line on the covered side, and a label naming the platform
    and its source status sits inside the bottom band.
    """
    from PIL import Image, ImageDraw

    platforms = platforms_for(name)
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    wash = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    wash_draw = ImageDraw.Draw(wash)
    fill = ((255, 64, 64) if name == COMBINED
            else platforms[0].colour) + (FILL_ALPHA,)
    for platform in platforms:
        for _band, rect in platform.zones:
            a, b, c, d = _scale(rect, width, height)
            wash_draw.rectangle((a, b, c - 1, d - 1), fill=fill)
    image.alpha_composite(wash)

    draw = ImageDraw.Draw(image)
    for platform in platforms:
        _outline_inside_bands(draw, platform, width, height,
                              platform.colour + (LINE_ALPHA,))

    # The legend, inside the bottom band every platform covers.
    size = max(12, round(30 * height / REFERENCE_SIZE[1]))
    font = _font(size)
    shallowest_bottom = min(
        _scale(rect, width, height)[1]
        for p in platforms for band, rect in p.zones if band == "bottom")
    x = round(140 * width / REFERENCE_SIZE[0])
    y = height - round(40 * height / REFERENCE_SIZE[1]) - size
    for platform in reversed(platforms):
        text = platform.label + {"measured": " (measured)",
                                 "third-party": " (unofficial)"}.get(
                                     platform.basis, "")
        draw.text((x, y), text, font=font,
                  fill=platform.colour + (255,))
        y -= int(size * 1.35)
    title = ("SAFE ZONES - ALL PLATFORMS" if name == COMBINED
             else "SAFE ZONES")
    if y - size > shallowest_bottom:
        draw.text((x, y), title, font=font, fill=(255, 255, 255, 255))
    return image


def overlay_dir() -> str:
    """Where the generated overlays live in the repository."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "presets",
                                         "safe-zones"))


def overlay_path(name: str) -> str:
    """The checked-in PNG for one overlay name; unknown names raise."""
    platforms_for(name)
    return os.path.join(overlay_dir(), f"safe_zones_{name}.png")


def write_overlays(out_dir: str | None = None) -> list[str]:
    """Regenerate every overlay PNG from the table."""
    out_dir = out_dir or overlay_dir()
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for name in OVERLAY_NAMES:
        path = os.path.join(out_dir, f"safe_zones_{name}.png")
        render_overlay(name).save(path, optimize=True)
        written.append(path)
    return written


def describe() -> list[str]:
    """One line per platform: its safe rectangle and its source."""
    lines = []
    for platform in PLATFORMS.values():
        lines.append(f"{platform.label}: safe {platform.safe_rect()} "
                     f"({platform.basis})"
                     f" - {platform.source} [{platform.source_date}]")
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true",
                        help="regenerate library/presets/safe-zones/*.png")
    args = parser.parse_args(argv)
    if args.write:
        for path in write_overlays():
            print(path)
    for line in describe():
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
