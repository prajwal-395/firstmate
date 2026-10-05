"""Fonts as the IR declares them: a file, a weight, and CSS's vertical metrics.

A text node names a ``font_file`` and a ``font_weight``. A browser applies
that weight to a variable font's ``wght`` axis; an SVG rasteriser that
cannot read font variations (usvg) draws the variable font's DEFAULT
instance instead - Montserrat-Variable's default is Thin 100, which is
how a caption declared at 800 came out hairline. So every consumer that
is not a browser draws and measures from a STATIC instance pinned at the
declared weight, cut here once per (file, weight) and named uniquely so
a font database can never pick the wrong face.

The vertical metrics follow the CSS rule a browser uses: typo metrics
when the font sets USE_TYPO_METRICS, else hhea. ``line-height: normal``
is ascent + descent + line gap; a numeric line height splits its leading
evenly above and below the content area.
"""

from __future__ import annotations

import hashlib
import io
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional


class FontUnreadable(ValueError):
    """A declared font file that cannot be read or instanced."""


@dataclass(frozen=True)
class VerticalMetrics:
    """Ascent, descent and line gap in ems (descent positive)."""

    ascent: float
    descent: float
    line_gap: float

    @property
    def normal_line_height(self) -> float:
        return self.ascent + self.descent + self.line_gap


def _signature(font_file: str) -> tuple[str, int, int]:
    stat = os.stat(font_file)
    return (os.path.abspath(font_file), int(stat.st_mtime_ns), stat.st_size)


@lru_cache(maxsize=64)
def _instance_bytes(signature: tuple[str, int, int], weight: Optional[int]) -> tuple[bytes, str]:
    from fontTools.ttLib import TTFont

    path = signature[0]
    try:
        font = TTFont(path)
    except Exception as exc:  # fontTools raises a zoo of types on bad input
        raise FontUnreadable(f"cannot read font file {path}: {exc}") from exc
    family = font["name"].getDebugName(16) or font["name"].getDebugName(1) or "Font"
    if "fvar" in font:
        from fontTools.varLib import instancer

        axes = {axis.axisTag: axis for axis in font["fvar"].axes}
        location = {}
        for tag, axis in axes.items():
            value = axis.defaultValue
            if tag == "wght" and weight is not None:
                value = min(max(float(weight), axis.minValue), axis.maxValue)
            location[tag] = value
        font = instancer.instantiateVariableFont(font, location)
    if weight is not None and "OS/2" in font:
        font["OS/2"].usWeightClass = int(weight)
    digest = hashlib.sha1(f"{signature}{weight}".encode()).hexdigest()[:10]
    unique = f"{family} ren{weight if weight is not None else 'd'} {digest}"
    name = font["name"]
    for name_id in (1, 4, 16, 6):
        name.removeNames(nameID=name_id)
    name.setName(unique, 1, 3, 1, 0x409)
    name.setName("Regular", 2, 3, 1, 0x409)
    name.setName(unique, 4, 3, 1, 0x409)
    name.setName(unique.replace(" ", "-"), 6, 3, 1, 0x409)
    buffer = io.BytesIO()
    font.save(buffer)
    return buffer.getvalue(), unique


def static_instance(font_file: str, weight: Optional[float]) -> tuple[bytes, str]:
    """``(ttf_bytes, family_name)`` of *font_file* pinned at *weight*.

    A static font comes back with only its names made unique, so the
    caller can treat every declared face the same way.
    """
    pinned = None if weight is None else int(round(float(weight)))
    return _instance_bytes(_signature(font_file), pinned)


@lru_cache(maxsize=64)
def _metrics(signature: tuple[str, int, int]) -> VerticalMetrics:
    from fontTools.ttLib import TTFont

    try:
        font = TTFont(signature[0], lazy=True)
    except Exception as exc:
        raise FontUnreadable(f"cannot read font file {signature[0]}: {exc}") from exc
    upem = float(font["head"].unitsPerEm)
    os2 = font["OS/2"] if "OS/2" in font else None
    if os2 is not None and os2.fsSelection & (1 << 7):
        ascent, descent, gap = os2.sTypoAscender, -os2.sTypoDescender, os2.sTypoLineGap
    else:
        hhea = font["hhea"]
        ascent, descent, gap = hhea.ascent, -hhea.descent, hhea.lineGap
    return VerticalMetrics(ascent / upem, descent / upem, max(0, gap) / upem)


def vertical_metrics(font_file: str) -> VerticalMetrics:
    """The CSS vertical metrics of *font_file*, in ems."""
    return _metrics(_signature(font_file))


class Advances:
    """Horizontal advances of one static face, with its pair kerning.

    What a browser's shaper does for Latin text, minus ligatures and
    contextual forms: each character's advance from ``hmtx``, adjusted
    by the GPOS ``kern`` feature's pair values. Built from the same
    static instance the renderer draws, so a width here is the width
    there.
    """

    def __init__(self, data: bytes):
        from fontTools.ttLib import TTFont

        font = TTFont(io.BytesIO(data))
        self.upem = float(font["head"].unitsPerEm)
        self.cmap = font.getBestCmap() or {}
        self.hmtx = {name: adv for name, (adv, _) in font["hmtx"].metrics.items()}
        self.pairs: dict[tuple[str, str], int] = {}
        self.class_tables: list = []
        if "GPOS" in font:
            self._read_kerning(font["GPOS"].table)

    def _read_kerning(self, gpos) -> None:
        if gpos.FeatureList is None or gpos.LookupList is None:
            return
        indices: list[int] = []
        for record in gpos.FeatureList.FeatureRecord:
            if record.FeatureTag == "kern":
                indices.extend(i for i in record.Feature.LookupListIndex if i not in indices)
        for index in sorted(indices):
            lookup = gpos.LookupList.Lookup[index]
            for sub in lookup.SubTable:
                kind = lookup.LookupType
                if kind == 9:
                    kind, sub = sub.ExtensionLookupType, sub.ExtSubTable
                if kind != 2:
                    continue
                if sub.Format == 1:
                    for first, pair_set in zip(sub.Coverage.glyphs, sub.PairSet):
                        for record in pair_set.PairValueRecord:
                            value = getattr(record.Value1, "XAdvance", 0) if record.Value1 else 0
                            self.pairs.setdefault((first, record.SecondGlyph), value or 0)
                elif sub.Format == 2:
                    self.class_tables.append((
                        set(sub.Coverage.glyphs),
                        sub.ClassDef1.classDefs if sub.ClassDef1 else {},
                        sub.ClassDef2.classDefs if sub.ClassDef2 else {},
                        sub.Class1Record,
                    ))

    def _kern(self, left: str, right: str) -> int:
        if (left, right) in self.pairs:
            return self.pairs[(left, right)]
        for coverage, classes1, classes2, records in self.class_tables:
            if left not in coverage:
                continue
            record = records[classes1.get(left, 0)].Class2Record[classes2.get(right, 0)]
            return getattr(record.Value1, "XAdvance", 0) or 0 if record.Value1 else 0
        return 0

    def width(self, text: str, size: float) -> float:
        """Advance width of *text* at *size* pixels, kerned."""
        glyphs = [self.cmap.get(ord(ch), ".notdef") for ch in text]
        units = sum(self.hmtx.get(g, 0) for g in glyphs)
        units += sum(self._kern(a, b) for a, b in zip(glyphs, glyphs[1:]))
        return units * size / self.upem


@lru_cache(maxsize=64)
def _advances(signature: tuple[str, int, int], weight: Optional[int]) -> Advances:
    data, _ = _instance_bytes(signature, weight)
    return Advances(data)


def advances(font_file: str, weight: Optional[float]) -> Advances:
    """The kerned advances of *font_file* pinned at *weight*."""
    pinned = None if weight is None else int(round(float(weight)))
    return _advances(_signature(font_file), pinned)
