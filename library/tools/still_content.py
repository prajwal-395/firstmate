"""Content-addressed stills: one pool item per distinct image.

Tonight's `still-overlays` pass extracted its own still per overlay item
(`<name>_still.png`), so the TV frame - the same graphic on every reel -
now exists as many separate stills instead of one reused one. This module
is the pure half of that fix: hashing an image's pixels and planning
which timeline items can share one pool item. It holds no Resolve calls,
so every rule in it is testable without the application running;
`library/tools/still_dedupe.py` is the half that drives Resolve, and
`library/tools/still_overlays.py` reuses `hash_pixels` at swap time so a
new swap re-points at an identical still instead of importing another.

The hash is over the decoded pixel bytes (the PNG's decompressed IDAT
stream), not the file bytes: two exports of the same frame may carry
different ancillary chunks while drawing the same picture. Anything that
is not a parseable PNG falls back to the whole-file hash - a refusal to
parse is never a claim of difference, and the planner treats an
unhashable file as its own group rather than merging it anywhere.
"""

from __future__ import annotations

import hashlib
import struct
import zlib


class DedupeRefused(Exception):
    """A dedupe plan cannot be proven safe, so nothing is re-pointed."""


def hash_pixels(path: str) -> str:
    """The sha256 of `path`'s decoded pixel bytes, as hex.

    A PNG is parsed with the standard library only: signature, chunk
    walk, IDAT concatenated and decompressed. Non-PNG files, truncated
    files and anything else that does not parse hash as whole-file
    bytes instead - identical files still hash identical, and different
    metadata around identical pixels still merges, which is the case
    that matters for tonight's duplicated TV frame stills.
    """
    try:
        with open(path, "rb") as handle:
            blob = handle.read()
    except OSError as exc:
        raise DedupeRefused(
            f"cannot read {path!r} to hash it - an unreadable still is "
            f"left alone, never merged on a guess: {exc}") from exc
    pixels = _decode_png_pixels(blob)
    if pixels is None:
        return hashlib.sha256(blob).hexdigest()
    return hashlib.sha256(pixels).hexdigest()


def _decode_png_pixels(blob: bytes) -> bytes | None:
    """Decompressed IDAT bytes, or None when `blob` is not a PNG."""
    if blob[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    offset = 8
    idat = bytearray()
    try:
        while offset + 8 <= len(blob):
            (length, kind) = struct.unpack(">I4s", blob[offset:offset + 8])
            data = blob[offset + 8:offset + 8 + length]
            if len(data) != length:
                return None
            if kind == b"IDAT":
                idat.extend(data)
            elif kind == b"IEND":
                break
            offset += 8 + length + 4
    except struct.error:
        return None
    if not idat:
        return None
    try:
        return zlib.decompress(bytes(idat))
    except zlib.error:
        return None


def content_still_filename(digest: str) -> str:
    """The on-disk name for a still with this pixel hash.

    Content-addressed: two swaps of the same picture name the same
    file, so the second finds the first instead of importing another.
    """
    return f"still_{digest[:16]}.png"


def decode_png_gray(path: str) -> tuple:
    """`path` as `(width, height, rows)` of 8-bit gray values.

    Standard-library PNG decode: signature, chunk walk, IDAT
    decompressed, scanline filters (None/Sub/Up/Average/Paeth)
    reversed. Colour types gray, truecolour and truecolour+alpha at 8
    or 16 bits; anything else (palette, interlace, odd depths)
    raises DedupeRefused - an undecodable still is left out of
    perceptual grouping, never merged on a guess.
    """
    try:
        with open(path, "rb") as handle:
            blob = handle.read()
    except OSError as exc:
        raise DedupeRefused(
            f"cannot read {path!r} to decode it: {exc}") from exc
    return _decode_png_gray_blob(blob, path)


def _decode_png_gray_blob(blob: bytes, path: str) -> tuple:
    import struct as _struct

    if blob[:8] != b"\x89PNG\r\n\x1a\n":
        raise DedupeRefused(f"{path!r} is not a PNG")
    offset, width, height = 8, None, None
    depth = ctype = interlace = None
    idat = bytearray()
    try:
        while offset + 8 <= len(blob):
            (length, kind) = _struct.unpack(">I4s", blob[offset:offset + 8])
            data = blob[offset + 8:offset + 8 + length]
            if len(data) != length:
                raise DedupeRefused(f"{path!r} is truncated")
            if kind == b"IHDR":
                (width, height, depth, ctype, _comp, _filt,
                 interlace) = _struct.unpack(">IIBBBBB", data)
            elif kind == b"IDAT":
                idat.extend(data)
            elif kind == b"IEND":
                break
            offset += 8 + length + 4
    except _struct.error as exc:
        raise DedupeRefused(f"{path!r} does not parse: {exc}") from exc
    if width is None:
        raise DedupeRefused(f"{path!r} has no IHDR")
    if interlace:
        raise DedupeRefused(
            f"{path!r} is interlaced - left out of perceptual grouping")
    if ctype == 3 or depth not in (8, 16) or ctype not in (0, 2, 6):
        raise DedupeRefused(
            f"{path!r} (colour type {ctype}, depth {depth}) is outside "
            f"the decoded set - left out of perceptual grouping")
    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error as exc:
        raise DedupeRefused(f"{path!r} does not decompress: {exc}") from exc
    channels = {0: 1, 2: 3, 6: 4}[ctype]
    step = (2 if depth == 16 else 1) * channels
    stride = width * step
    rows: list = []
    previous = bytearray(stride)
    pos = 0
    for _ in range(height):
        if pos + 1 + stride > len(raw):
            raise DedupeRefused(f"{path!r} ends mid-row")
        filt = raw[pos]
        pos += 1
        scan = bytearray(raw[pos:pos + stride])
        pos += stride
        bpp = step
        for index in range(stride):
            left = scan[index - bpp] if index >= bpp else 0
            up = previous[index]
            upper_left = previous[index - bpp] if index >= bpp else 0
            if filt == 1:
                scan[index] = (scan[index] + left) & 0xFF
            elif filt == 2:
                scan[index] = (scan[index] + up) & 0xFF
            elif filt == 3:
                scan[index] = (scan[index] + ((left + up) >> 1)) & 0xFF
            elif filt == 4:
                pred = _paeth(left, up, upper_left)
                scan[index] = (scan[index] + pred) & 0xFF
            elif filt != 0:
                raise DedupeRefused(
                    f"{path!r} uses filter {filt} - undecodable here")
        previous = scan
        rows.append(bytes(scan))
    gray = []
    for scan in rows:
        line = []
        for pixel in range(width):
            base = pixel * step
            if ctype == 0:
                value = (scan[base] << 8 | scan[base + 1]) if depth == 16 \
                    else scan[base]
            else:
                if depth == 16:
                    red = scan[base] << 8 | scan[base + 1]
                    green = scan[base + 2] << 8 | scan[base + 3]
                    blue = scan[base + 4] << 8 | scan[base + 5]
                else:
                    red, green, blue = (scan[base], scan[base + 1],
                                        scan[base + 2])
                value = (red * 299 + green * 587 + blue * 114) // 1000
                if depth == 16:
                    value >>= 8
            line.append(value & 0xFF)
        gray.append(line)
    return width, height, gray


def _paeth(left: int, up: int, upper_left: int) -> int:
    estimate = left + up - upper_left
    pick_left = abs(estimate - left)
    pick_up = abs(estimate - up)
    pick_corner = abs(estimate - upper_left)
    if pick_left <= pick_up and pick_left <= pick_corner:
        return left
    if pick_up <= pick_corner:
        return up
    return upper_left


def dhash_gray(width: int, height: int, gray: list) -> int:
    """The 64-bit difference hash of an 8-bit gray image.

    Downscaled to 9x8 by box average, then one bit per horizontal
    neighbour comparison. Near-identical pictures - the same graphic
    through different x264 encodes - hash within a few bits; different
    copy differs widely.
    """
    small = []
    for row in range(8):
        line = []
        for col in range(9):
            total, count = 0, 0
            for source_row in range(row * height // 8,
                                   (row + 1) * height // 8 or 1):
                for source_col in range(col * width // 9,
                                        (col + 1) * width // 9 or 1):
                    total += gray[min(source_row, height - 1)][
                        min(source_col, width - 1)]
                    count += 1
            line.append(total // max(count, 1))
        small.append(line)
    digest = 0
    for row in small:
        for left, right in zip(row, row[1:]):
            digest = (digest << 1) | (1 if left > right else 0)
    return digest


def phash_still(path: str) -> int:
    """The perceptual hash of a still file. Raises DedupeRefused."""
    width, height, gray = decode_png_gray(path)
    return dhash_gray(width, height, gray)


def hamming(first: int, second: int) -> int:
    """Bits differing between two perceptual hashes."""
    return bin(first ^ second).count("1")


def crop_difference(first: str, second: str) -> float:
    """Mean absolute gray difference over the shared center quarter.

    The confirmation number beside a perceptual match: two renders of
    one graphic differ by single-digit noise, and a different graphic
    differs by an order of magnitude more. Raises DedupeRefused where
    either file does not decode.
    """
    first_w, first_h, first_g = decode_png_gray(first)
    second_w, second_h, second_g = decode_png_gray(second)
    width, height = min(first_w, second_w), min(first_h, second_h)
    if width < 4 or height < 4:
        raise DedupeRefused("still too small for a crop comparison")
    left, top = width // 4, height // 4
    right, bottom = left + width // 2, top + height // 2
    total, count = 0, 0
    for row in range(top, bottom):
        first_row = row * first_h // height
        second_row = row * second_h // height
        for col in range(left, right):
            first_col = col * first_w // width
            second_col = col * second_w // width
            total += abs(first_g[first_row][first_col]
                         - second_g[second_row][second_col])
            count += 1
    return total / max(count, 1)


def png_info(path: str) -> dict | None:
    """Width, height, depth and colour type off the IHDR alone.

    The cheap half of perceptual grouping: decoding a 1080x1920 PNG
    costs seconds, so the driver reads headers for every still and
    decodes only the files a provenance group actually has to compare.
    Returns None where the file is not a parseable PNG.
    """
    import struct as _struct

    try:
        with open(path, "rb") as handle:
            blob = handle.read(33)
    except OSError:
        return None
    if len(blob) < 26 or blob[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    try:
        (length, kind) = _struct.unpack(">I4s", blob[8:16])
        if kind != b"IHDR" or length != 13:
            return None
        width, height, depth, ctype, _c, _f, _i = _struct.unpack(
            ">IIBBBBB", blob[16:29])
    except _struct.error:
        return None
    return {"width": width, "height": height, "depth": depth,
            "color_type": ctype}


def provenance_key(filename: str) -> str | None:
    """The graphic `filename` renders, or None where files never merge.

    The project declares ONE tv_frame asset (`tv_frame.resolve_tv_frame`
    is project-wide) drawn at delivery-fixed geometry
    (`reel_look.frame_overlay_segments`: identity is source plus drawn
    geometry), so every tv_frame render - `tv_frame_<hash>[_still].png`
    and the shared `tv_still.png` - is one graphic however many encodes
    it passed through. Anything else (per-reel post-header copy,
    motion-graphics renders) carries its own copy and merges with
    nothing: a shared name there would be a claim, not provenance.
    """
    import os as _os
    import re as _re

    stem = _os.path.basename(filename)
    if _re.match(r"^tv_frame_[0-9a-f]{10}(_still)?\.png$", stem):
        return "tv_frame"
    if stem == "tv_still.png":
        return "tv_frame"
    return None


def group_by_graphic(items: list) -> dict:
    """Items with a provenance key, grouped by it. No decoding."""
    groups: dict = {}
    for item in items:
        key = provenance_key(str(item.get("file_path") or ""))
        if key is None:
            continue
        groups.setdefault(key, []).append(item)
    return groups


def plan_graphic_group(members: list, max_distance: int = 4) -> dict:
    """One canonical still per graphic, and who re-points at it.

    `members` share a provenance key and carry `file_path`, `phash`,
    `depth`, `size`, plus the per-item `record`/`props`/`markers`/
    `pool_uid`/`timeline` a re-point preserves. Every pair must hash
    within `max_distance` bits or the group refuses naming the outlier
    distances - provenance says same source, the tight match proves
    the renders agree, and either one alone is not a merge. The
    canonical member is the cleanest source: a direct render over a
    video extract (no `_still` in the name), then greatest bit depth,
    then largest file, then the smallest path so the plan is stable.
    """
    files = sorted({member.get("file_path") for member in members})
    if len(files) < 2:
        return {"groups": [], "repoints": [], "skipped": []}
    hashes = {}
    for member in members:
        path = member.get("file_path")
        if path not in hashes:
            digest = member.get("phash")
            if digest is None:
                return {"groups": [], "repoints": [],
                        "skipped": [{
                            "file_path": path,
                            "why": "no perceptual hash - left alone, "
                                   "never merged"}]}
            hashes[path] = digest
    worst, worst_pair = 0, None
    paths = sorted(hashes)
    for index, first in enumerate(paths):
        for second in paths[index + 1:]:
            distance = hamming(hashes[first], hashes[second])
            if distance > worst:
                worst, worst_pair = distance, (first, second)
    if worst > max_distance:
        raise DedupeRefused(
            f"one graphic by provenance renders {worst} bits apart "
            f"({(worst_pair[0] or '').rsplit('/', 1)[-1]} vs "
            f"{(worst_pair[1] or '').rsplit('/', 1)[-1]}) - past the "
            f"tight match of {max_distance}. Same source is not same "
            f"picture; nothing was re-pointed.")
    import os as _os

    def cleanliness(member: dict) -> tuple:
        name = str(member.get("file_path") or "")
        return (0 if "_still" not in _os.path.basename(name) else 1,
                -(member.get("depth") or 0),
                -(member.get("size") or 0),
                str(member.get("file_path") or ""),
                str(member.get("pool_uid") or ""))

    ordered = sorted(members, key=cleanliness)
    canonical = ordered[0]
    repoints = []
    for member in ordered[1:]:
        if member.get("pool_uid") == canonical.get("pool_uid"):
            continue
        repoints.append({
            "timeline": member.get("timeline"),
            "record": member.get("record"),
            "pool_uid": member.get("pool_uid"),
            "file_path": member.get("file_path"),
            "props": member.get("props") or {},
            "markers": member.get("markers") or {},
            "canonical_pool_uid": canonical.get("pool_uid"),
            "canonical_file": canonical.get("file_path"),
        })
    return {"groups": [{
        "graphic": None,
        "canonical": canonical.get("file_path"),
        "members": len(members),
        "worst_distance": worst,
    }], "repoints": repoints, "skipped": []}


def plan_dedupe(items: list) -> dict:
    """Which still items share one pool item, without touching Resolve.

    `items` are plain dicts with `timeline`, `record` ([start, end]),
    `pool_uid`, `file_path` and `digest` (from `hash_pixels`). Returns
    `{"groups", "repoints", "skipped"}`: a group holds every item
    drawing identical pixels; each group's canonical item is the one
    with the smallest `(file_path, pool_uid)` so the plan is stable
    across runs; every other item in the group is a repoint naming the
    canonical pool item and file it must play. Items with no digest
    are skipped with the reason, never merged - tonight's
    `<name>_still.png` files all hash, so a skip is evidence of an
    unreadable file, not a quiet pass.

    Spans are never compared: every re-point keeps its own record
    span, transforms and markers, so items at different durations -
    the same still held longer on another reel - re-point alike.
    """
    by_digest: dict[str, list] = {}
    skipped = []
    for item in items:
        digest = item.get("digest")
        if not digest:
            skipped.append({
                "timeline": item.get("timeline"),
                "record": item.get("record"),
                "file_path": item.get("file_path"),
                "why": "no pixel hash - left alone, never merged",
            })
            continue
        by_digest.setdefault(digest, []).append(item)
    groups, repoints = [], []
    for digest, members in sorted(by_digest.items()):
        if len(members) < 2:
            continue
        if len({member.get("file_path") for member in members}) < 2:
            continue
        ordered = sorted(
            members,
            key=lambda m: (str(m.get("file_path") or ""),
                           str(m.get("pool_uid") or "")))
        canonical = ordered[0]
        groups.append({
            "digest": digest,
            "canonical": {
                "timeline": canonical.get("timeline"),
                "record": canonical.get("record"),
                "pool_uid": canonical.get("pool_uid"),
                "file_path": canonical.get("file_path"),
            },
            "members": len(members),
        })
        for member in ordered[1:]:
            repoints.append({
                "timeline": member.get("timeline"),
                "record": member.get("record"),
                "pool_uid": member.get("pool_uid"),
                "file_path": member.get("file_path"),
                "props": member.get("props") or {},
                "markers": member.get("markers") or {},
                "canonical_pool_uid": canonical.get("pool_uid"),
                "canonical_file": canonical.get("file_path"),
            })
    return {"groups": groups, "repoints": repoints, "skipped": skipped}
