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

    A group whose items disagree on duration is refused as a whole:
    re-pointing keeps the record span, and a still placed at the wrong
    span would move the cut.
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
        spans = {(tuple(member.get("record") or ()),
                  member.get("duration"))
                 for member in members}
        if len(spans) != 1:
            raise DedupeRefused(
                f"{len(members)} items draw identical pixels "
                f"({canonical.get('file_path')!r}) at "
                f"{len(spans)} different spans - re-pointing keeps the "
                f"record span, and a still at the wrong span would move "
                f"the cut. Nothing was re-pointed.")
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
