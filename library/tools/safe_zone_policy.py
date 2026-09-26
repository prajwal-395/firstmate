"""A project's safe-zone POLICY, and the layout it resolves to. One enumeration.

The captain, 2026-09-25: "properly build out and generalize the safe
area tooling (so it has the ability to bound but also load in
preferences and configs for specific platforms, but also custom rules
that might be set)".

Three layers, one direction:

* ``platform_safe_zones`` is the REFERENCE: what each app draws where,
  measured on the captain's phone and laid out on every modelled phone.
  It knows nothing about a project.
* This module is the POLICY: which of those platforms and phones a
  project is made for, how much room to leave round the apps' UI, which
  elements to ignore, and the project's own rules on top - declared at
  ``pipeline.safe_zones`` in ``project.yaml`` (:data:`DECLARATION_KEYS`).
  A project that declares nothing gets every platform on every phone,
  which is the captain's 2026-08-25 ruling that one master serves every
  platform and obeys the strictest.
* :class:`SafeLayout` is what the policy RESOLVES to, and what every
  consumer asks: where the zones are, what a box intrudes on, the
  widest clear span on a band of rows, the region no phone crops, how
  far a picture must shrink to sit inside it, and the four insets the
  older consumers read (``safe_area.resolve_safe_area`` derives them
  from here for the vertical formats).

A malformed declaration RAISES naming the key, like every declaration in
this tree: a rule that is silently dropped is a caption under an app's
UI with nothing to notice.

The declaration::

    pipeline:
      safe_zones:
        platforms: [tiktok, instagram_reels, youtube_shorts, linkedin]
        devices: all            # all | iphone | android | [names]
        margin: 8               # px of room round every app element
        ignore: [linkedin.back] # platform.element, never a side strip
        keep_out:               # the project's own rules
          - {rect: [0, 0, 1080, 90], reason: "the brand bug lives here"}
        reason: "why this policy"
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

#: Where the policy is declared, under ``pipeline:``.
DECLARATION_KEY = "safe_zones"

#: Everything the declaration may carry. Checked on read.
DECLARATION_KEYS = ("platforms", "devices", "margin", "ignore", "keep_out",
                    "reason")

#: The device groups ``devices`` may name instead of a list.
DEVICE_GROUPS = ("all", "iphone", "android")

#: The platform key a project's own ``keep_out`` rules are filed under.
CUSTOM = "custom"


class SafeZonePolicyError(ValueError):
    """A ``pipeline.safe_zones`` declaration that cannot be honoured."""


Rect = tuple[int, int, int, int]


@dataclass(frozen=True)
class LayoutZone:
    """One covered rectangle on the frame, and whose it is."""

    platform: str
    name: str
    rect: Rect
    ui: str


@dataclass(frozen=True)
class Policy:
    """A validated declaration (or the default, when there is none)."""

    platforms: tuple[str, ...]
    devices: tuple[str, ...]
    margin: int = 0
    ignore: tuple[str, ...] = ()
    keep_out: tuple[tuple[Rect, str], ...] = ()
    reason: str = ""
    declared: bool = False


def default_policy() -> Policy:
    """Every platform on every modelled phone, no margin, no rules."""
    from library.tools import platform_safe_zones as psz

    return Policy(platforms=tuple(psz.PLATFORMS),
                  devices=tuple(d.name for d in psz.DEVICES))


def _devices(raw) -> tuple[str, ...]:
    from library.tools import platform_safe_zones as psz

    names = [d.name for d in psz.DEVICES]
    if raw is None or raw == "all":
        return tuple(names)
    if raw == "iphone":
        return tuple(n for n in names if n.startswith("iPhone"))
    if raw == "android":
        return tuple(n for n in names if not n.startswith("iPhone"))
    if isinstance(raw, str) or not isinstance(raw, Sequence) or not raw:
        raise SafeZonePolicyError(
            f"pipeline.{DECLARATION_KEY}.devices must be one of "
            f"{list(DEVICE_GROUPS)} or a list of device names, got {raw!r}")
    unknown = sorted(set(map(str, raw)) - set(names))
    if unknown:
        raise SafeZonePolicyError(
            f"pipeline.{DECLARATION_KEY}.devices names {unknown}; the "
            f"modelled phones are {names}")
    return tuple(str(n) for n in raw)


def parse_policy(raw) -> Policy:
    """A declaration mapping as a :class:`Policy`; anything malformed raises."""
    from library.tools import platform_safe_zones as psz

    if raw is None:
        return default_policy()
    if not isinstance(raw, dict):
        raise SafeZonePolicyError(
            f"pipeline.{DECLARATION_KEY} must be a mapping, got "
            f"{type(raw).__name__}")
    unknown = sorted(set(raw) - set(DECLARATION_KEYS))
    if unknown:
        raise SafeZonePolicyError(
            f"pipeline.{DECLARATION_KEY} declares {unknown}, which nothing "
            f"reads; known keys: {list(DECLARATION_KEYS)}")
    platforms = raw.get("platforms")
    if platforms is None:
        platforms = list(psz.PLATFORMS)
    if (isinstance(platforms, str) or not isinstance(platforms, Sequence)
            or not platforms):
        raise SafeZonePolicyError(
            f"pipeline.{DECLARATION_KEY}.platforms must be a non-empty "
            f"list, got {platforms!r}")
    bad = sorted(set(map(str, platforms)) - set(psz.PLATFORMS))
    if bad:
        raise SafeZonePolicyError(
            f"pipeline.{DECLARATION_KEY}.platforms names {bad}; known: "
            f"{list(psz.PLATFORMS)}")
    margin = raw.get("margin", 0)
    if isinstance(margin, bool) or not isinstance(margin, int) or margin < 0:
        raise SafeZonePolicyError(
            f"pipeline.{DECLARATION_KEY}.margin must be whole pixels >= 0, "
            f"got {margin!r}")
    ignore = raw.get("ignore") or []
    if isinstance(ignore, str) or not isinstance(ignore, Sequence):
        raise SafeZonePolicyError(
            f"pipeline.{DECLARATION_KEY}.ignore must be a list of "
            f"'platform.element', got {ignore!r}")
    known = {f"{p.key}.{z.name}" for p in psz.PLATFORMS.values()
             for z in p.zones if z.name not in psz.SIDES}
    for entry in ignore:
        if str(entry) not in known:
            raise SafeZonePolicyError(
                f"pipeline.{DECLARATION_KEY}.ignore names {entry!r}, which "
                f"is no app element (a side strip is the phone's crop, "
                f"not UI, and cannot be ignored); known: {sorted(known)}")
    keep_out = []
    for index, rule in enumerate(raw.get("keep_out") or []):
        rect = rule.get("rect") if isinstance(rule, dict) else None
        reason = str((rule or {}).get("reason") or "").strip() \
            if isinstance(rule, dict) else ""
        if (not isinstance(rect, Sequence) or len(rect) != 4
                or not all(isinstance(v, int) and not isinstance(v, bool)
                           for v in rect)
                or rect[2] <= rect[0] or rect[3] <= rect[1]):
            raise SafeZonePolicyError(
                f"pipeline.{DECLARATION_KEY}.keep_out[{index}] needs rect: "
                f"[x0, y0, x1, y1] in whole frame pixels, x1 > x0 and "
                f"y1 > y0, got {rule!r}")
        if not reason:
            raise SafeZonePolicyError(
                f"pipeline.{DECLARATION_KEY}.keep_out[{index}] carries no "
                f"reason - a rule nobody can explain is a rule nobody can "
                f"retire")
        keep_out.append((tuple(int(v) for v in rect), reason))
    return Policy(platforms=tuple(str(p) for p in platforms),
                  devices=_devices(raw.get("devices")),
                  margin=int(margin),
                  ignore=tuple(str(e) for e in ignore),
                  keep_out=tuple(keep_out),
                  reason=str(raw.get("reason") or ""),
                  declared=True)


def project_policy(project_folder: str | None) -> Policy:
    """The project's declared policy, or the default."""
    if not project_folder:
        return default_policy()
    from library.tools.brand_registry import project_pipeline_block

    return parse_policy(project_pipeline_block(project_folder)
                        .get(DECLARATION_KEY))


# ── The layout ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class SafeLayout:
    """A policy resolved on the reference frame: the zones and the questions."""

    policy: Policy
    zones: tuple[LayoutZone, ...]
    frame: tuple[int, int] = (1080, 1920)
    notes: tuple[str, ...] = field(default=())

    # ── Bounding ──

    def intrusions(self, box: Rect) -> list[dict]:
        """Every zone ``box`` overlaps, with the overlap. Empty is clear."""
        x0, y0, x1, y1 = box
        out = []
        for zone in self.zones:
            a, b, c, d = zone.rect
            if min(x1, c) > max(x0, a) and min(y1, d) > max(y0, b):
                out.append({"platform": zone.platform, "band": zone.name,
                            "ui": zone.ui,
                            "overlap": (max(x0, a), max(y0, b),
                                        min(x1, c), min(y1, d))})
        return out

    def _in_rows(self, y0: int, y1: int) -> list[LayoutZone]:
        return [z for z in self.zones
                if min(y1, z.rect[3]) > max(y0, z.rect[1])]

    def clear_span(self, y0: int, y1: int,
                   through: float | None = None) -> tuple[int, int]:
        """The widest run of columns clear of every zone over rows
        ``y0..y1`` that contains column ``through`` (the frame's centre
        by default); ``(x, x)`` where that column itself is covered."""
        x = self.frame[0] / 2 if through is None else float(through)
        left, right = 0, self.frame[0]
        for zone in self._in_rows(y0, y1):
            a, _b, c, _d = zone.rect
            if a <= x < c:
                return (int(x), int(x))
            if c <= x:
                left = max(left, c)
            else:
                right = min(right, a)
        return (left, right)

    def centred_clear_width(self, y0: int, y1: int) -> tuple[int, dict | None]:
        """The widest box CENTRED on the frame over rows ``y0..y1`` that
        clears every zone, and the zone that bounds it."""
        centre = self.frame[0] / 2
        half, bound = centre, None
        for zone in self._in_rows(y0, y1):
            a, _b, c, _d = zone.rect
            reach = (0.0 if a <= centre < c
                     else centre - c if c <= centre else a - centre)
            if reach < half:
                half, bound = reach, {"platform": zone.platform,
                                      "band": zone.name, "ui": zone.ui,
                                      "rect": zone.rect}
        return int(2 * half), bound

    # ── What every phone shows ──

    def visible(self) -> Rect:
        """The part of the frame no phone in the policy crops off."""
        from library.tools import platform_safe_zones as psz

        x0, x1 = 0, self.frame[0]
        for zone in self.zones:
            if zone.name == "left" and zone.platform in psz.PLATFORMS:
                x0 = max(x0, zone.rect[2])
            elif zone.name == "right" and zone.platform in psz.PLATFORMS:
                x1 = min(x1, zone.rect[0])
        return (x0, 0, x1, self.frame[1])

    def fit_scale(self, box: Rect,
                  anchor: tuple[float, float] | None = None) -> float:
        """How far ``box`` must shrink about ``anchor`` (its own centre by
        default) to sit inside :meth:`visible`; 1.0 when it already does."""
        vx0, vy0, vx1, vy1 = self.visible()
        x0, y0, x1, y1 = box
        ax, ay = anchor or ((x0 + x1) / 2, (y0 + y1) / 2)
        scale = 1.0
        for edge, limit, point in ((x0, vx0, ax), (x1, vx1, ax),
                                   (y0, vy0, ay), (y1, vy1, ay)):
            reach = edge - point
            room = limit - point
            if reach and (room / reach) < scale:
                scale = max(0.0, room / reach)
        return scale

    # ── The older consumers ──

    def insets(self) -> dict[str, int]:
        """Four edge insets that clear every zone.

        A side strip, or a zone taller than half the frame, counts
        against its side; one wider than half the frame against the top
        or the bottom. Those settle the four edges first. A zone that
        spans neither - a corner icon - then counts against whichever
        edge it grows least, so a back arrow in the top-left corner costs
        nothing where the top is already deeper than it. Coarser than the
        zones (a notch becomes a whole edge), so a consumer that can ask
        for a span should (:meth:`clear_span`).
        """
        width, height = self.frame
        reach = {"top": 0, "right": 0, "bottom": 0, "left": 0}

        def depth(rect, edge):
            a, b, c, d = rect
            return {"top": d, "bottom": height - b, "left": c,
                    "right": width - a}[edge]

        corners = []
        for zone in self.zones:
            a, b, c, d = zone.rect
            if zone.name in ("left", "right"):
                edge = zone.name
            elif d - b > height / 2:
                edge = "left" if (a + c) / 2 < width / 2 else "right"
            elif c - a > width / 2:
                edge = "top" if (b + d) / 2 < height / 2 else "bottom"
            else:
                corners.append(zone.rect)
                continue
            reach[edge] = max(reach[edge], depth(zone.rect, edge))
        for rect in corners:
            edge = min(reach, key=lambda e: (
                max(0, depth(rect, e) - reach[e]), depth(rect, e)))
            reach[edge] = max(reach[edge], depth(rect, edge))
        return reach

    def describe(self) -> list[str]:
        policy = self.policy
        lines = [
            f"platforms: {', '.join(policy.platforms)}",
            f"phones: {', '.join(policy.devices)}",
            f"margin round app elements: {policy.margin}px",
            (f"visible on every phone: x {self.visible()[0]}.."
             f"{self.visible()[2]}"),
            f"insets: {self.insets()}",
        ]
        if policy.ignore:
            lines.append(f"ignored: {', '.join(policy.ignore)}")
        for rect, why in policy.keep_out:
            lines.append(f"keep out {rect}: {why}")
        if policy.reason:
            lines.append(f"why: {policy.reason}")
        return lines + list(self.notes)


def resolve_layout(policy: Policy | None = None,
                   frame: tuple[int, int] = (1080, 1920)) -> SafeLayout:
    """The layout a policy resolves to, on ``frame``."""
    from library.tools import platform_safe_zones as psz

    policy = policy or default_policy()
    width, height = frame
    sx, sy = width / psz.REFERENCE_SIZE[0], height / psz.REFERENCE_SIZE[1]

    def scaled(rect) -> Rect:
        return (round(rect[0] * sx), round(rect[1] * sy),
                round(rect[2] * sx), round(rect[3] * sy))

    zones: list[LayoutZone] = []
    for key in policy.platforms:
        for zone in psz.zones_for(key, devices=policy.devices):
            if f"{key}.{zone.name}" in policy.ignore:
                continue
            a, b, c, d = zone.rect
            if zone.name not in psz.SIDES and policy.margin:
                m = policy.margin
                a, b = max(0, a - m), max(0, b - m)
                c = min(psz.REFERENCE_SIZE[0], c + m)
                d = min(psz.REFERENCE_SIZE[1], d + m)
            zones.append(LayoutZone(key, zone.name, scaled((a, b, c, d)),
                                    zone.ui))
    for rect, why in policy.keep_out:
        zones.append(LayoutZone(CUSTOM, "keep-out", scaled(rect), why))
    return SafeLayout(policy=policy, zones=tuple(zones), frame=(width, height))


def project_layout(project_folder: str | None,
                   frame: tuple[int, int] = (1080, 1920)) -> SafeLayout:
    """The project's policy, resolved. The call a consumer makes."""
    return resolve_layout(project_policy(project_folder), frame)


# ── The guide ────────────────────────────────────────────────────────

#: The guide name that draws the PROJECT's policy rather than a platform.
PROJECT_OVERLAY = "project"

#: The colour a project's own ``keep_out`` rules are drawn in.
CUSTOM_COLOUR = (255, 200, 0)


def render_layout(layout: SafeLayout):
    """The guide for a resolved policy, as a transparent RGBA image: every
    zone the policy keeps, in its platform's colour, with the project's
    own rules in :data:`CUSTOM_COLOUR`."""
    from library.tools import platform_safe_zones as psz

    groups: dict[str, list] = {}
    for zone in layout.zones:
        groups.setdefault(zone.platform, []).append(
            psz.Zone(zone.name, zone.rect, zone.ui))
    platforms = []
    for key, zones in groups.items():
        known = psz.PLATFORMS.get(key)
        platforms.append(psz.PlatformZones(
            key=key, label=known.label if known else "Project rules",
            zones=tuple(zones),
            colour=known.colour if known else CUSTOM_COLOUR,
            basis=known.basis if known else "project",
            source=known.source if known else "pipeline.safe_zones",
            source_date=known.source_date if known else ""))
    return psz.render_platforms(
        platforms, layout.frame[0], layout.frame[1],
        title="SAFE ZONES - PROJECT POLICY",
        wash=(255, 64, 64))
