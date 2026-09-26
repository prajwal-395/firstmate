"""Where each short-form app paints its own UI over a 1080x1920 frame, on ANY phone. One enumeration.

The captain, 2026-09-25: "add an overlay for the bounds of tiktok, and
instagram reels, and youtube shorts, and linkedin reels that allows us to
make sure our video and all of the elements in it are positioned outside
of the boundings of where the UI elements for these apps are (create
seperate overlays for each app as well as one that combines all of the
bounding together)" - and then: "make the bounding safe zone agnostic to
device so we can have a reference that allows us to confirm if our reels
are good no matter what device we are on".

This module is that data, the overlays drawn from it, and the check that
reads it.  It is a GUIDE: nothing here moves an element.  The layout
rules that position captions and graphics stay in ``safe_area.py`` (one
strictest profile, the captain's 2026-08-25 ruling); this file is what an
editor, or a check, holds a built reel up against.

Each platform is a list of COVERED rectangles on the 1080x1920 reference
frame, ONE PER UI ELEMENT - a search icon is its own box, not a band
across the whole top (the captain: the old Shorts band "says that the
whole area to the left of it is not safe when in fact the area to the
left of it could be more tightly bound") - plus the side strips a phone
crops off. The combined overlay is the union of all four platforms.

How a box is made DEVICE-AGNOSTIC:

1. MEASURED. Each element's glyph box is read off the captain's own
   iPhone 17 screenshot of the app (:data:`CAPTURES`), padded by
   :data:`PAD` screen pixels, and turned into points.
2. ANCHORED. An app lays its UI out in points, pinned to an edge: the
   action rail to the right, the caption rows to the bottom of the video
   region, the tabs under the status bar. Each element records its pin
   (:class:`Element`), so it can be laid out again on another screen.
3. LAID OUT on every phone in :data:`DEVICES` - small and large, iPhone
   and Android, 19.5:9 to 21:9 - with that phone's status-bar and
   home-bar insets, and mapped onto the frame by the scale the app draws
   the video at on that screen (:func:`video_window`): the apps FILL the
   screen, so a phone taller than 9:16 loses both sides of the frame, and
   a taller phone loses more.
4. UNIONED. The zone is each element's bounding box over every phone
   (and, where how the app scales the video is not known, over every way
   it could), and each side strip is the widest crop. A box outside
   every zone is visible, and clear of every element, on every modelled
   phone.

The model reproduces the captain's screenshots exactly on the iPhone 17
(``tests/test_platform_safe_zones.py``). The other iPhones are Apple's
published point sizes and insets; the Android rows are an ESTIMATE of
their dp sizes and insets, and assume the apps' Android UI has the iOS
geometry. A screen WIDER than 9:16 (an iPhone SE, a tablet) is not
modelled: no source says how the apps scale there.

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

#: The two zone names that are the strips a phone's crop cuts off each
#: side (:func:`video_window`), rather than UI drawn over the picture.
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


# ── The phones ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class Device:
    """A phone in portrait, in points (iOS) or dp (Android)."""

    name: str
    width: float
    height: float
    top_inset: float  # status bar
    bottom_inset: float  # home indicator / gesture bar
    cutout: tuple[float, float, float]  # camera: (width, y0, y1), centred
    basis: str


#: The envelope a reel has to survive. iPhones: Apple's point sizes and
#: safe-area insets (useyourloaf.com/blog/iphone-17-screen-sizes/, and
#: the iPhone 16e's), the island 126x37pt at 11pt, the 16e's notch about
#: 162x33pt. Android: 1080-wide panels at their default density
#: (Galaxy S25 384x832dp, Pixel 10 1080x2424 -> 412x923dp, Xperia 10 VI
#: 1080x2520 at 21:9 -> 411x960dp); the insets and punch-hole are
#: ESTIMATES.
DEVICES: tuple[Device, ...] = (
    Device("iPhone 16e", 390, 844, 47, 34, (162, 0, 33), "Apple"),
    Device("iPhone 17", 402, 874, 62, 34, (126, 11, 48), "measured"),
    Device("iPhone Air", 420, 912, 68, 34, (126, 11, 48), "Apple"),
    Device("iPhone 17 Pro Max", 440, 956, 62, 34, (126, 11, 48), "Apple"),
    Device("Galaxy S25", 384, 832, 32, 24, (28, 6, 34), "estimate"),
    Device("Pixel 10 (20:9)", 412, 923, 48, 24, (28, 8, 40), "estimate"),
    Device("Xperia 10 VI (21:9)", 411, 960, 32, 24, (28, 6, 34),
           "estimate"),
)

DEVICE_BY_NAME = {d.name: d for d in DEVICES}

#: The phone the captain's screenshots are from, and its capture size.
MEASURED_DEVICE = "iPhone 17"
SCREENSHOT = (920, 2000)
PX_PER_PT = SCREENSHOT[0] / DEVICE_BY_NAME[MEASURED_DEVICE].width

#: Screen pixels a glyph box is padded by on every side: the measured
#: ink is the UI's, not its reach.
PAD = 10


# ── The captures ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class Element:
    """One UI element, measured on the screenshot, and what it is pinned to.

    ``h``: ``left`` / ``right`` (a fixed distance from that edge),
    ``stretch`` (left edge from the left, right edge from the right - a
    text row that fills the column), ``centre``. ``v``: ``status`` (in
    the status bar, which scales with its inset), ``top`` (below the
    status bar), ``bottom`` (above the bottom of the video region).
    """

    name: str
    box: tuple[int, int, int, int]  # screen px on the screenshot
    h: str
    v: str
    ui: str


@dataclass(frozen=True)
class Capture:
    """One view of an app on the captain's phone: where its video region
    ends, how it scales the video, and the elements it draws over it.
    A platform with two views (LinkedIn's Video tab and its full-screen
    viewer) has two captures, and its zones are both."""

    platform: str
    region: int  # screen px: the video runs from y0 to here
    fills: tuple[str, ...]  # "cover" and/or "fit"
    elements: tuple[Element, ...]
    screenshot: str


#: The status bar every app draws the video under, from the screenshots.
_STATUS = (
    Element("status-time", (100, 55, 240, 95), "left", "status",
            "status bar: clock"),
    Element("status-icons", (658, 57, 840, 92), "right", "status",
            "status bar: signal, battery"),
)

#: Measured 2026-09-25 on the captain's iPhone 17 captures (920x2000).
#: The region is where the app's own opaque bar begins:
#:   TikTok  0..1728 (the "Search" strip below is opaque)
#:   Shorts  0..1810 (the nav bar below is opaque)
#:   Reels   0..1748 (below it is a blurred smear the app draws under
#:           its progress bar and nav pill, not picture - detail energy
#:           drops from ~13 to <1 across y1744..1756)
#:   LinkedIn, two views of ONE video:
#:           the Video tab  0..1808 (its tab bar below is opaque)
#:           the viewer     0..1920 (full screen above the home bar)
#:           The same video is drawn 6.2% larger in the viewer (avatar
#:           ring 189 vs 178px tall, title 672 vs 632 wide) - exactly
#:           1920 / 1808, and every bottom element sits 112px higher in
#:           the Video tab. Fitting the width would draw it the same
#:           size in both, so LinkedIn FILLS, like the other three.
#: TikTok, Reels and Shorts FILL too (the device research, 2026-09-25:
#: Reels "zoom in… parts of the video are cut off", TikTok crops "to
#: prevent black bars", Shorts "chose crop over letterbox"; none
#: publishes it).
CAPTURES: dict[str, Capture] = {
    "tiktok": Capture("tiktok", 1728, ("cover",), _STATUS + (
        Element("tabs", (37, 165, 880, 238), "stretch", "top",
                "LIVE, Following / For You tabs, search - the full width"),
        Element("rail", (805, 690, 895, 1700), "right", "bottom",
                "action rail: avatar, like, comment, save, share, disc"),
        Element("text", (28, 1450, 690, 1712), "stretch", "bottom",
                "username, title and up to four caption lines"),
    ), "phone-tiktok.png"),
    "instagram_reels": Capture("instagram_reels", 1748, ("cover",),
                               _STATUS + (
        Element("rail", (815, 1015, 885, 1642), "right", "bottom",
                "action rail: like, comment, repost, share, menu"),
        Element("profile", (35, 1518, 740, 1598), "stretch", "bottom",
                "avatar, username and collaborators"),
        Element("caption", (35, 1628, 740, 1668), "stretch", "bottom",
                "caption, one line"),
        Element("followed-by", (35, 1685, 740, 1737), "stretch", "bottom",
                "followed-by row"),
    ), "phone-ig-reels.png"),
    "youtube_shorts": Capture("youtube_shorts", 1810, ("cover",),
                              _STATUS + (
        Element("search", (718, 172, 765, 218), "right", "top",
                "search icon"),
        Element("menu", (845, 177, 862, 217), "right", "top",
                "three-dot menu"),
        Element("rail", (808, 1003, 884, 1600), "right", "bottom",
                "action rail: like, dislike, comment, share, remix"),
        Element("audio", (810, 1693, 882, 1768), "right", "bottom",
                "sound thumbnail"),
        Element("channel", (37, 1570, 790, 1648), "stretch", "bottom",
                "avatar, channel name and Subscribe"),
        Element("title", (37, 1660, 790, 1695), "stretch", "bottom",
                "title, to the rail"),
        Element("sound", (37, 1718, 790, 1770), "stretch", "bottom",
                "sound pill, to the rail"),
        Element("progress", (0, 1800, 920, 1810), "stretch", "bottom",
                "progress bar"),
    ), "phone-yt-shorts.png"),
    "linkedin_feed": Capture("linkedin", 1808, ("cover",), _STATUS + (
        Element("rail", (825, 1053, 890, 1673), "right", "bottom",
                "action rail: like, comment, share, save, more"),
        Element("author", (37, 1573, 770, 1666), "stretch", "bottom",
                "avatar, name, Follow and headline"),
        Element("caption", (37, 1683, 755, 1718), "stretch", "bottom",
                "caption, one line and ...more"),
        Element("progress", (37, 1763, 883, 1776), "stretch", "bottom",
                "progress bar"),
    ), "phone-linkedin-feed.png"),
    "linkedin_viewer": Capture("linkedin", 1920, ("cover",), _STATUS + (
        Element("back", (30, 178, 78, 215), "left", "top", "back arrow"),
        Element("rail", (825, 1165, 890, 1785), "right", "bottom",
                "action rail: like, comment, share, save, more"),
        Element("author", (37, 1685, 770, 1778), "stretch", "bottom",
                "avatar, name, Follow and headline"),
        Element("caption", (37, 1795, 755, 1830), "stretch", "bottom",
                "caption, one line and ...more"),
        Element("progress", (37, 1875, 883, 1888), "stretch", "bottom",
                "progress bar"),
    ), "phone-linkedin.png"),
}

#: The measured phone's home indicator, which an app's opaque bottom
#: bar includes; another phone's bar swaps it for its own inset.
_HOME_BAR_PT = DEVICE_BY_NAME[MEASURED_DEVICE].bottom_inset


def region_height(key: str, device: Device) -> float:
    """Points from the top of the screen to the app's opaque bottom bar."""
    bar = (SCREENSHOT[1] - CAPTURES[key].region) / PX_PER_PT
    if bar > 0:
        bar = max(0.0, bar - _HOME_BAR_PT + device.bottom_inset)
    return device.height - bar


def video_window(key: str, device: Device,
                 fill: str = "cover") -> tuple[float, float, float]:
    """(scale, ox, oy): the frame drawn at ``scale`` points a frame pixel,
    centred in the region; screen point (x, y) is frame
    ((x + ox) / scale, (y + oy) / scale)."""
    w, h = REFERENCE_SIZE
    region = region_height(key, device)
    pick = max if fill == "cover" else min
    scale = pick(device.width / w, region / h)
    return scale, (w * scale - device.width) / 2, (h * scale - region) / 2


def side_crop(key: str, device: str = MEASURED_DEVICE,
              fill: str = "cover") -> float:
    """Frame pixels one side of the 1080 frame loses on ``device``."""
    scale, ox, _oy = video_window(key, DEVICE_BY_NAME[device], fill)
    return max(0.0, ox / scale)


def crop_table() -> dict[str, dict[str, float]]:
    """{view: {device: per-side crop in frame px}}, filling the screen."""
    return {key: {d.name: round(side_crop(key, d.name), 1) for d in DEVICES}
            for key in CAPTURES}


def _element_points(element: Element, key: str,
                    device: Device) -> tuple[float, float, float, float]:
    """The element's padded box laid out on ``device``, in points."""
    measured = DEVICE_BY_NAME[MEASURED_DEVICE]
    pad = PAD / PX_PER_PT
    x0, y0, x1, y1 = (v / PX_PER_PT for v in element.box)
    x0, y0, x1, y1 = x0 - pad, y0 - pad, x1 + pad, y1 + pad

    def _x(x: float, pin: str) -> float:
        if pin == "left":
            return x
        if pin == "right":
            return device.width - (measured.width - x)
        return device.width / 2 + (x - measured.width / 2)

    def _y(y: float) -> float:
        if element.v == "status":
            return y * device.top_inset / measured.top_inset
        if element.v == "top":
            return device.top_inset + (y - measured.top_inset)
        return (region_height(key, device)
                - (region_height(key, measured) - y))

    pins = {"stretch": ("left", "right")}.get(element.h,
                                              (element.h, element.h))
    return (_x(x0, pins[0]), _y(y0), _x(x1, pins[1]), _y(y1))


def _to_frame(box, key: str, device: Device, fill: str) -> Rect | None:
    scale, ox, oy = video_window(key, device, fill)
    w, h = REFERENCE_SIZE
    x0, y0, x1, y1 = box
    rect = (max(0, round((x0 + ox) / scale)), max(0, round((y0 + oy) / scale)),
            min(w, round((x1 + ox) / scale)), min(h, round((y1 + oy) / scale)))
    return rect if rect[2] > rect[0] and rect[3] > rect[1] else None


def zones_on(key: str, device: Device, fill: str = "cover") -> list[Zone]:
    """Every element of ``key`` as one phone draws it, on the frame."""
    zones = []
    cw, cy0, cy1 = device.cutout
    camera = (device.width / 2 - cw / 2, cy0, device.width / 2 + cw / 2, cy1)
    rect = _to_frame(camera, key, device, fill)
    if rect:
        zones.append(Zone("camera", rect, "camera cutout (island, notch, "
                                          "punch-hole)"))
    for element in CAPTURES[key].elements:
        rect = _to_frame(_element_points(element, key, device), key, device,
                         fill)
        if rect:
            zones.append(Zone(element.name, rect, element.ui))
    return zones


def zones_for(platform: str,
              devices: Sequence[str] | None = None) -> tuple[Zone, ...]:
    """Each element's bounding box over every view of the platform, every
    phone in ``devices`` (all modelled phones by default) and every fill,
    and the widest side crop among them."""
    w, h = REFERENCE_SIZE
    boxes: dict[str, list[int]] = {}
    ui: dict[str, str] = {}
    crop = 0.0
    views = [k for k, c in CAPTURES.items() if c.platform == platform]
    phones = [d for d in DEVICES
              if devices is None or d.name in set(devices)]
    if not views or not phones:
        raise KeyError(f"no capture of {platform!r} on {devices!r}")
    for key, device in ((k, d) for k in views for d in phones):
        for fill in CAPTURES[key].fills:
            crop = max(crop, side_crop(key, device.name, fill))
            for zone in zones_on(key, device, fill):
                ui[zone.name] = zone.ui
                have = boxes.setdefault(zone.name, list(zone.rect))
                have[:] = [min(have[0], zone.rect[0]),
                           min(have[1], zone.rect[1]),
                           max(have[2], zone.rect[2]),
                           max(have[3], zone.rect[3])]
    strip = round(crop)
    zones = [Zone("left", (0, 0, strip, h),
                  "cropped off the side on the tallest modelled phone"),
             Zone("right", (w - strip, 0, w, h),
                  "cropped off the side on the tallest modelled phone")]
    zones += [Zone(n, tuple(b), ui[n]) for n, b in boxes.items()]
    return tuple(zones)


TIKTOK = PlatformZones(
    key="tiktok",
    label="TikTok",
    zones=zones_for("tiktok"),
    colour=(0, 242, 234),
    basis="measured",
    source=("MEASURED on the captain's iPhone 17 screenshot (phone-tiktok.png, "
            "920x2000, 2026-09-25), laid out on every modelled phone. "
            "Published (TikTok Ads Help Center In-Feed template, "
            "https://ads.tiktok.com/help/article/tiktok-auction-in-feed-ads, "
            "updated June 2026): top 240, bottom 660, sides 120 - ADS "
            "guidance, which reserves bands rather than the elements the "
            "organic feed draws."),
    source_date="screenshot 2026-09-25",
)

INSTAGRAM_REELS = PlatformZones(
    key="instagram_reels",
    label="Instagram Reels",
    zones=zones_for("instagram_reels"),
    colour=(225, 48, 108),
    basis="measured",
    source=("MEASURED on the captain's iPhone 17 screenshot "
            "(phone-ig-reels.png, 920x2000, 2026-09-25), laid out on every "
            "modelled phone; the video region ends at the blurred band "
            "(the captain's catch). Published (Meta Ads Guide, "
            "https://www.facebook.com/business/ads-guide/update/video/"
            "instagram-reels): top 14% (269), bottom 35% (672), sides 6% "
            "(65) - ADS guidance."),
    source_date="screenshot 2026-09-25",
)

YOUTUBE_SHORTS = PlatformZones(
    key="youtube_shorts",
    label="YouTube Shorts",
    zones=zones_for("youtube_shorts"),
    colour=(255, 48, 48),
    basis="measured",
    source=("MEASURED on the captain's iPhone 17 screenshot "
            "(phone-yt-shorts.png, 920x2000, 2026-09-25), laid out on every "
            "modelled phone. Published (Google Ads Help, "
            "https://support.google.com/google-ads/answer/9128498): top "
            "288, bottom 672, left 48, right 192 - ADS guidance."),
    source_date="screenshot 2026-09-25",
)

LINKEDIN = PlatformZones(
    key="linkedin",
    label="LinkedIn",
    zones=zones_for("linkedin"),
    colour=(10, 102, 194),
    basis="measured",
    source=("MEASURED on the captain's iPhone 17 screenshots "
            "of the Video tab and the full-screen viewer "
            "(phone-linkedin-feed.png, phone-linkedin.png, 920x2000, "
            "2026-09-25), laid out on every modelled phone. LinkedIn "
            "publishes no vertical safe zone."),
    source_date="screenshot 2026-09-25",
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


def centred_clear_width(y0: int, y1: int, name: str = COMBINED,
                        frame: tuple[int, int] = REFERENCE_SIZE
                        ) -> tuple[int, dict | None]:
    """The widest box CENTRED on the frame, over rows ``y0..y1``, that
    clears every zone of the named overlay - and the zone that bounds
    it (None when nothing does).

    A centred box can only be as wide as twice the distance to the
    nearer zone, so a zone over the centre line leaves 0: that band is
    not a place for centred content at any width.
    """
    centre = frame[0] / 2
    half, bound = centre, None
    for platform in platforms_for(name):
        for zone in platform.zones:
            a, b, c, d = _scale(zone.rect, *frame)
            if min(y1, d) - max(y0, b) <= 0:
                continue
            reach = (0.0 if a <= centre < c
                     else centre - c if c <= centre else a - centre)
            if reach < half:
                half = reach
                bound = {"platform": platform.key, "band": zone.name,
                         "ui": zone.ui, "rect": (a, b, c, d)}
    return int(2 * half), bound


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
    an element box all round.
    """
    for zone in platform.zones:
        a, b, c, d = _scale(zone.rect, width, height)
        if zone.name in SIDES:
            inner = (c - LINE_WIDTH, a)[zone.name == "right"]
            draw.rectangle((inner, b, inner + LINE_WIDTH - 1, d - 1),
                           fill=rgba)
            views = [k for k, c in CAPTURES.items()
                     if c.platform == platform.key]
            if views:
                cut = round(max(side_crop(k) for k in views) * width
                            / REFERENCE_SIZE[0])
                x = cut - 1 if zone.name == "left" else width - cut
                for y in range(b, d, 24):
                    draw.rectangle((x, y, x, min(d, y + 12) - 1), fill=rgba)
        else:
            draw.rectangle((a, b, c - 1, d - 1), outline=rgba,
                           width=min(LINE_WIDTH, (c - a) // 2, (d - b) // 2))


def _legend(image, platforms: list[PlatformZones], title: str) -> None:
    """The legend, set vertically inside the widest left strip - the one
    place every overlay covers the whole height of."""
    from PIL import Image, ImageDraw

    width, height = image.size
    strip = max(_scale(z.rect, width, height)[2]
                for p in platforms for z in p.zones if z.name == "left")
    size = max(8, round(strip * 0.42))
    font = _font(size)
    parts = [(title, (255, 255, 255))]
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
    return render_platforms(
        platforms_for(name), width, height,
        title="SAFE ZONES" + (" - ALL PLATFORMS" if name == COMBINED
                              else ""),
        wash=(255, 64, 64) if name == COMBINED else None)


def render_platforms(platforms: list[PlatformZones],
                     width: int = REFERENCE_SIZE[0],
                     height: int = REFERENCE_SIZE[1], *, title: str,
                     wash: tuple[int, int, int] | None = None):
    """A guide drawing ``platforms``' zones, as a transparent RGBA image.

    ``wash`` is the fill for every zone (a neutral one where several
    platforms share the overlay); None fills in the first platform's
    colour. Zone rects are on the reference frame.
    """
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    wash_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    wash_draw = ImageDraw.Draw(wash_layer)
    fill = (wash or platforms[0].colour) + (FILL_ALPHA,)
    for platform in platforms:
        for zone in platform.zones:
            a, b, c, d = _scale(zone.rect, width, height)
            wash_draw.rectangle((a, b, c - 1, d - 1), fill=fill)
    image.alpha_composite(wash_layer)

    draw = ImageDraw.Draw(image)
    for platform in platforms:
        _outline_inside_zones(draw, platform, width, height,
                              platform.colour + (LINE_ALPHA,))
    _legend(image, platforms, title)
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
        lines.append(f"    {key}: {row}")
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
