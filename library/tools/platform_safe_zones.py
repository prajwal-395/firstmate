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
frame, ONE PER UI ELEMENT - a search icon is its own box, not a band
across the whole top (the captain, 2026-09-25: the old Shorts band
"says that the whole area to the left of it is not safe when in fact
the area to the left of it could be more tightly bound") - plus the two
side strips a phone's cover crop cuts off (:func:`side_crop`). The
combined overlay is the union of all four platforms' rectangles.

Where each number comes from:

* TikTok, Instagram Reels and YouTube Shorts are MEASURED on the
  captain's own iPhone 17 screenshots of each app (2026-09-25): each
  element's glyph box in screen pixels, padded by :data:`PAD` and mapped
  onto the frame by the app's cover scale (:data:`REGION`). Where those
  and the platforms' published ad guidance disagree, the screenshots
  win: the published numbers are ADS guidance and reserve whole bands.
* The side strips are MODELLED, per phone (:data:`DEVICES`): the apps
  fill the screen, so a phone taller than 9:16 loses both sides of the
  frame, and a taller phone loses more. The model reproduces the cut the
  captain saw on his iPhone 17; each strip is the widest cut among the
  modelled phones, and the overlay ticks where the iPhone 17 cuts.
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

#: The two zone names that are the strips a device's cover crop cuts off
#: each side (:func:`side_crop`), rather than UI drawn over the picture.
SIDES = ("left", "right")


@dataclass(frozen=True)
class Zone:
    """One covered rectangle: what the app draws there, and where."""

    name: str
    rect: Rect
    ui: str


@dataclass(frozen=True)
class PlatformZones:
    """One app's covered rectangles on the reference frame, and their source."""

    key: str
    label: str
    zones: tuple[Zone, ...]
    colour: tuple[int, int, int]
    basis: str  # "measured" (screenshot), "published", "third-party"
    source: str
    source_date: str

    def safe_columns(self) -> tuple[int, int]:
        """(x0, x1): the columns clear of the side strips.

        Everything else is a box, so check a box with :func:`intrusions`.
        """
        x0, x1 = 0, REFERENCE_SIZE[0]
        for zone in self.zones:
            if zone.name == "left":
                x0 = max(x0, zone.rect[2])
            elif zone.name == "right":
                x1 = min(x1, zone.rect[0])
        return (x0, x1)


def _edges(top: int, bottom: int, left: int, right: int) -> tuple[Zone, ...]:
    w, h = REFERENCE_SIZE
    return (
        Zone("top", (0, 0, w, top), "top bar"),
        Zone("bottom", (0, h - bottom, w, h),
             "caption, profile, audio and CTA"),
        Zone("left", (0, top, left, h - bottom), "edge margin"),
        Zone("right", (w - right, top, w, h - bottom),
             "edge margin and action rail"),
    )


# ── The screenshots, and the devices ─────────────────────────────────

#: The captain's own iPhone 17 captures (2026-09-25), 920x2000 - the
#: iPhone 17's 402x874pt screen at 2.2886px a point.
SCREENSHOT = (920, 2000)
SCREEN_PT = (402, 874)

#: Each app scales the 9:16 frame to COVER its video region - the screen
#: from the top down to where the app's own opaque bar begins - so the
#: frame is cut equally left and right and nothing top or bottom
#: (the device research, 2026-09-25: Reels "zoom in… parts of the video
#: are cut off", TikTok crops "to prevent black bars", Shorts "chose crop
#: over letterbox"; none of the three publishes it). Measured on the
#: screenshots:
#:   TikTok  region y 0..1728 (the "Search" strip below is opaque)
#:   Shorts  region y 0..1810 (the nav bar below is opaque)
#:   Reels   region y 0..1748 (below it is a blurred smear the app
#:           draws under its progress bar and nav pill, not picture -
#:           detail energy drops from ~13 to <1 across y1744..1756)
#: A screenshot point (x, y) is frame ((x + ox) / s, y / s), with
#: s = region / 1920 and ox = (1080 * s - 920) / 2.
REGION = {"tiktok": 1728, "youtube_shorts": 1810, "instagram_reels": 1748}

#: Phones in portrait, in points (iOS) or dp (Android). How much of the
#: frame's width a phone loses depends on the SHAPE of the app's video
#: region: the screen less the app's opaque bottom bar, which the
#: screenshots measure in points (TikTok 119pt, Shorts 83pt, Reels
#: 110pt). The model reproduces the iPhone 17 cut the captain saw to the
#: pixel. The other rows carry the same bars, which is exact for an iOS
#: app laid out in points and an ESTIMATE for Android, where the apps'
#: bars were not captured. A screen wider than 9:16 (an iPhone SE, a
#: tablet) crops no side; only phones taller than 9:16 are listed.
#: Sources: Apple's iPhone 17 family via useyourloaf.com/blog/
#: iphone-17-screen-sizes/, Pixel 10 1080x2424 at 2.625 via GSMArena.
DEVICES: dict[str, tuple[float, float]] = {
    "iPhone 17": (402, 874),
    "iPhone 17 Pro Max": (440, 956),
    "iPhone Air": (420, 912),
    "iPhone 16e": (390, 844),
    "Pixel 10 (20:9)": (1080 / 2.625, 2424 / 2.625),
}

#: The phone the captain's screenshots are from.
MEASURED_DEVICE = "iPhone 17"

#: Screen pixels a glyph box is padded by on every side before it is
#: mapped onto the frame: the measured ink is the UI's, not its reach.
PAD = 10


def _scale_offset(key: str) -> tuple[float, float]:
    s = REGION[key] / 1920
    return s, (1080 * s - SCREENSHOT[0]) / 2


def bar_points(key: str) -> float:
    """The app's opaque bottom bar, in points, off the screenshot."""
    return (SCREENSHOT[1] - REGION[key]) * SCREEN_PT[0] / SCREENSHOT[0]


def side_crop(key: str, device: str = MEASURED_DEVICE) -> float:
    """Frame pixels one side of the 1080 frame loses on ``device``."""
    w, h = DEVICES[device]
    aspect = w / (h - bar_points(key))
    return max(0.0, (REFERENCE_SIZE[0] - REFERENCE_SIZE[1] * aspect) / 2)


def crop_table() -> dict[str, dict[str, float]]:
    """{app: {device: per-side crop in frame px}} for every modelled phone."""
    return {key: {d: round(side_crop(key, d), 1) for d in DEVICES}
            for key in REGION}


def _screen_box(key: str, box: tuple[int, int, int, int]) -> Rect:
    """A screenshot glyph box, padded, as a frame rectangle."""
    s, ox = _scale_offset(key)
    x0, y0, x1, y1 = box
    w, h = REFERENCE_SIZE
    return (max(0, round((x0 - PAD + ox) / s)),
            max(0, round((y0 - PAD) / s)),
            min(w, round((x1 + PAD + ox) / s)),
            min(h, round((y1 + PAD) / s)))


#: The status bar and the Dynamic Island, the same in every app: the
#: video runs under them. The island is not in a screenshot (iOS leaves
#: it out); it is Apple's 126x37pt at 11pt from the top.
_STATUS = (
    ("status-time", (100, 55, 240, 95), "status bar: clock"),
    ("dynamic-island", (316, 25, 604, 110), "iPhone Dynamic Island"),
    ("status-icons", (658, 57, 840, 90), "status bar: signal, battery"),
)


def _measured(key: str, elements) -> tuple[Zone, ...]:
    """The side strips on the widest-cropping modelled phone, then one
    box per UI element measured on the screenshot. A text element that
    grows with its copy (a title, a caption) is measured to the column
    it wraps in, not to the example's own words."""
    w, h = REFERENCE_SIZE
    crop = round(max(side_crop(key, d) for d in DEVICES))
    zones = [Zone("left", (0, 0, crop, h),
                  "cut off by the cover crop on the tallest modelled phone"),
             Zone("right", (w - crop, 0, w, h),
                  "cut off by the cover crop on the tallest modelled phone")]
    for name, box, ui in _STATUS + tuple(elements):
        zones.append(Zone(name, _screen_box(key, box), ui))
    return tuple(zones)


TIKTOK = PlatformZones(
    key="tiktok",
    label="TikTok",
    zones=_measured("tiktok", (
        ("tabs", (37, 165, 880, 238),
         "LIVE, Following / For You tabs, search - the full width"),
        ("rail", (805, 690, 895, 1700),
         "action rail: avatar, like, comment, save, share, sound disc"),
        ("text", (28, 1450, 690, 1712),
         "username, title and up to four caption lines, wrapped at screen x690"),
    )),
    colour=(0, 242, 234),
    basis="measured",
    source=("MEASURED on the captain's iPhone 17 screenshot (phone-tiktok.png, "
            "920x2000, 2026-09-25), one box per UI element, region 0..1728 "
            "-> scale 0.900. Published (TikTok Ads Help Center In-Feed "
            "template, https://ads.tiktok.com/help/article/"
            "tiktok-auction-in-feed-ads, updated June 2026): top 240, "
            "bottom 660, sides 120 - ADS guidance, which reserves bands "
            "rather than the elements the organic feed draws."),
    source_date="screenshot 2026-09-25",
)

INSTAGRAM_REELS = PlatformZones(
    key="instagram_reels",
    label="Instagram Reels",
    zones=_measured("instagram_reels", (
        ("rail", (815, 1015, 885, 1642),
         "action rail: like, comment, repost, share, menu"),
        ("profile", (35, 1518, 740, 1598),
         "avatar, username and collaborators"),
        ("caption", (35, 1628, 740, 1668),
         "caption, one line truncated at screen x725"),
        ("followed-by", (35, 1685, 740, 1737), "followed-by row"),
    )),
    colour=(225, 48, 108),
    basis="measured",
    source=("MEASURED on the captain's iPhone 17 screenshot "
            "(phone-ig-reels.png, 920x2000, 2026-09-25), one box per UI "
            "element, video region 0..1748 (the blurred band below it is "
            "not picture - the captain's catch) -> scale 0.910. Published "
            "(Meta Ads Guide, https://www.facebook.com/business/ads-guide/"
            "update/video/instagram-reels): top 14% (269), bottom 35% "
            "(672), sides 6% (65) - ADS guidance."),
    source_date="screenshot 2026-09-25",
)

YOUTUBE_SHORTS = PlatformZones(
    key="youtube_shorts",
    label="YouTube Shorts",
    zones=_measured("youtube_shorts", (
        ("search", (718, 172, 765, 218), "search icon"),
        ("menu", (845, 177, 862, 217), "three-dot menu"),
        ("rail", (808, 1003, 884, 1600),
         "action rail: like, dislike, comment, share, remix"),
        ("audio", (810, 1693, 882, 1768), "sound thumbnail"),
        ("channel", (37, 1570, 790, 1648),
         "avatar, channel name and Subscribe"),
        ("title", (37, 1660, 790, 1695), "title, to the rail"),
        ("sound", (37, 1718, 790, 1770), "sound pill, to the rail"),
        ("progress", (0, 1800, 920, 1810), "progress bar"),
    )),
    colour=(255, 48, 48),
    basis="measured",
    source=("MEASURED on the captain's iPhone 17 screenshot "
            "(phone-yt-shorts.png, 920x2000, 2026-09-25), one box per UI "
            "element, region 0..1810 -> scale 0.943. Published (Google "
            "Ads Help, https://support.google.com/google-ads/answer/"
            "9128498): top 288, bottom 672, left 48, right 192 - ADS "
            "guidance."),
    source_date="screenshot 2026-09-25",
)

LINKEDIN = PlatformZones(
    key="linkedin",
    label="LinkedIn",
    zones=_edges(108, 320, 60, 120),
    colour=(10, 102, 194),
    basis="third-party",
    source=("NOT OFFICIAL and not measured - LinkedIn publishes no "
            "vertical safe zone and no screenshot was captured. AI "
            "Carousels 'LinkedIn Safe Zone Checker' (LinkedIn 2025 mobile "
            "layout). https://www.aicarousels.com/free-tools/"
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
    """Every platform zone ``box`` overlaps, with the overlap in pixels.

    ``box`` is ``(x0, y0, x1, y1)`` in ``frame`` pixels.  Empty means the
    box clears every zone of the named overlay.
    """
    out: list[dict] = []
    x0, y0, x1, y1 = box
    for platform in platforms_for(name):
        for zone in platform.zones:
            a, b, c, d = _scale(zone.rect, *frame)
            ox = min(x1, c) - max(x0, a)
            oy = min(y1, d) - max(y0, b)
            if ox > 0 and oy > 0:
                out.append({"platform": platform.key, "band": zone.name,
                            "ui": zone.ui,
                            "overlap": (max(x0, a), max(y0, b),
                                        min(x1, c), min(y1, d))})
    return out


def covered_mask(name: str, width: int, height: int):
    """A boolean numpy mask of every covered pixel of the named overlay."""
    import numpy as np

    mask = np.zeros((height, width), dtype=bool)
    for platform in platforms_for(name):
        for zone in platform.zones:
            a, b, c, d = _scale(zone.rect, width, height)
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


def _outline_inside_zones(draw, platform: PlatformZones, width: int,
                          height: int, rgba) -> None:
    """Trace each zone's boundary on the COVERED side of it.

    Every line pixel lies inside one of the platform's zones, so a pixel
    the overlay touches is always a covered pixel - which is what lets
    the test compare the PNG to the table exactly. A side strip is lined
    on its inner edge, with a thin tick where the MEASURED phone cuts;
    an edge band on its inner edge; an element box all round.
    """
    for zone in platform.zones:
        a, b, c, d = _scale(zone.rect, width, height)
        if zone.name in SIDES:
            inner = (c - LINE_WIDTH, a)[zone.name == "right"]
            draw.rectangle((inner, b, inner + LINE_WIDTH - 1, d - 1),
                           fill=rgba)
            if platform.key in REGION:
                cut = round(side_crop(platform.key) * width
                            / REFERENCE_SIZE[0])
                x = cut - 1 if zone.name == "left" else width - cut
                for y in range(b, d, 24):
                    draw.rectangle((x, y, x, min(d, y + 12) - 1), fill=rgba)
        elif zone.name == "top":
            draw.rectangle((a, d - LINE_WIDTH, c - 1, d - 1), fill=rgba)
        elif zone.name == "bottom":
            draw.rectangle((a, b, c - 1, b + LINE_WIDTH - 1), fill=rgba)
        else:
            draw.rectangle((a, b, c - 1, d - 1), outline=rgba,
                           width=min(LINE_WIDTH, (c - a) // 2, (d - b) // 2))


def _legend(image, platforms: list[PlatformZones], name: str) -> None:
    """The legend, set vertically inside the widest left strip - the one
    place every overlay covers the whole height of."""
    from PIL import Image, ImageDraw

    width, height = image.size
    strip = max(_scale(z.rect, width, height)[2]
                for p in platforms for z in p.zones if z.name == "left")
    size = max(8, round(strip * 0.42))
    font = _font(size)
    parts = [("SAFE ZONES" + (" - ALL PLATFORMS" if name == COMBINED
                              else ""), (255, 255, 255))]
    for platform in platforms:
        parts.append((platform.label + {"measured": " (measured)",
                                        "third-party": " (unofficial)"}.get(
                                            platform.basis, ""),
                      platform.colour))
    band = Image.new("RGBA", (height, strip), (0, 0, 0, 0))
    draw = ImageDraw.Draw(band)
    x = round(height * 0.06)
    y = (strip - size) // 2 - max(1, size // 8)
    for text, colour in parts:
        draw.text((x, y), text, font=font, fill=colour + (255,))
        x += round(draw.textlength(text, font=font)) + size
    image.alpha_composite(band.rotate(90, expand=True).crop(
        (0, 0, strip, height)))


def render_overlay(name: str, width: int = REFERENCE_SIZE[0],
                   height: int = REFERENCE_SIZE[1]):
    """The guide overlay for ``name`` as a transparent RGBA PIL image.

    Covered zones are a translucent wash in the platform's colour (a
    neutral wash for the combined overlay), each zone's boundary is a
    solid line on the covered side, and a legend naming the platforms
    and their source status runs up the left strip.
    """
    from PIL import Image, ImageDraw

    platforms = platforms_for(name)
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    wash = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    wash_draw = ImageDraw.Draw(wash)
    fill = ((255, 64, 64) if name == COMBINED
            else platforms[0].colour) + (FILL_ALPHA,)
    for platform in platforms:
        for zone in platform.zones:
            a, b, c, d = _scale(zone.rect, width, height)
            wash_draw.rectangle((a, b, c - 1, d - 1), fill=fill)
    image.alpha_composite(wash)

    draw = ImageDraw.Draw(image)
    for platform in platforms:
        _outline_inside_zones(draw, platform, width, height,
                              platform.colour + (LINE_ALPHA,))
    _legend(image, platforms, name)
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
        lines.append(f"{platform.label}: safe columns "
                     f"{platform.safe_columns()} ({platform.basis})"
                     f" - {platform.source} [{platform.source_date}]")
        for zone in platform.zones:
            lines.append(f"    {zone.name:<16} {zone.rect}  {zone.ui}")
    lines.append("Per-side crop in frame px, by phone:")
    for key, row in crop_table().items():
        lines.append(f"    {PLATFORMS[key].label}: {row}")
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
