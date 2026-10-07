"""Read the inspectable timeline structure from a Resolve `.drt` archive.

Resolve's archive is a ZIP containing XML sequence containers. This reader
never extracts files and never edits an archive. Opaque encoded blob fields
are not guessed: absent values remain ``None`` and are named in each item's
``missing_fields`` list.
"""

from __future__ import annotations

import json
import math
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree

_SEQUENCE_ENTRY = re.compile(
    r"(?:^|/)SeqContainer(?:\d*\.xml|/[^/]+\.xml)$", re.IGNORECASE)
_ITEM_NAMES = frozenset({
    "sm2tivideoclip", "sm2tiaudioclip", "sm2tigenerator",
    "sm2titransition", "sm2tisubtitleclip", "sm2ticaptionclip",
})
_TRACK_VECTORS = {
    "videotrackvec": "video",
    "audiotrackvec": "audio",
    "subtitletrackvec": "subtitle",
    "texttrackvec": "text",
    "datatrackvec": "data",
}
_TRANSFORM_KEYS = ("Pan", "Tilt", "ZoomX", "ZoomY")
_TAG_NAME = re.compile(r"(<\/?)([A-Za-z_][A-Za-z0-9_.:-]*)(?=[\s/>])")


def _local_name(element_or_tag) -> str:
    tag = (element_or_tag.tag if hasattr(element_or_tag, "tag")
           else str(element_or_tag))
    tag = tag.rsplit("}", 1)[-1]
    return tag.rsplit("__", 1)[-1].lower()


def _parse_xml(raw: bytes, member: str):
    try:
        # Resolve uses namespace-like `ListMgt::Tag` names that are not
        # accepted by ElementTree. Repair tag names only, so caption text or
        # attribute values containing `::` remain unchanged.
        source = raw.decode("utf-8-sig")
        source = _TAG_NAME.sub(
            lambda match: match[1] + match[2].replace("::", "__"), source)
        return ElementTree.fromstring(source)
    except (UnicodeDecodeError, ElementTree.ParseError) as exc:
        raise ValueError(f"cannot parse DRT XML member {member!r}: {exc}") from exc


def _field(element, *names: str) -> str | None:
    wanted = {name.lower() for name in names}
    for child in element.iter():
        if child is element:
            continue
        if _local_name(child) in wanted:
            value = (child.text or "").strip()
            if value:
                return value
    # A few Resolve XML objects encode named values as attributes instead
    # of child elements. Accept those without interpreting opaque blobs.
    for child in element.iter():
        attributes = {str(key).lower(): str(value)
                      for key, value in child.attrib.items()}
        key = attributes.get("name") or attributes.get("key")
        value = (attributes.get("value") or attributes.get("val")
                 or (child.text or "").strip())
        if key and key.lower() in wanted and value:
            return value
    return None


def _number(value: str | None, *, integer: bool = False):
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    if integer:
        return int(parsed) if parsed.is_integer() else None
    return parsed


def _marker(marker) -> dict:
    return {
        "name": _field(marker, "Name", "MarkerName"),
        "comment": _field(marker, "Comment", "MarkerComment"),
        "color": _field(marker, "Color", "MarkerColor"),
        "frame": _number(_field(marker, "Frame", "Start", "Position"),
                          integer=True),
        "duration_frames": _number(_field(marker, "Duration"), integer=True),
        "custom_data": _field(marker, "CustomData"),
    }


def _explicit_markers(element, *, exclude_item_markers=False) -> list[dict]:
    parents = {}
    if exclude_item_markers:
        parents = {child: parent for parent in element.iter()
                   for child in parent}
    markers = []
    for node in element.iter():
        if node is element or _local_name(node) not in {
                "marker", "sm2timarker", "sm2timarkeritem"}:
            continue
        if exclude_item_markers:
            parent = parents.get(node)
            while parent is not None and parent is not element:
                if _local_name(parent) in _ITEM_NAMES:
                    break
                parent = parents.get(parent)
            else:
                markers.append(_marker(node))
        else:
            markers.append(_marker(node))
    return markers


def _item(node, track_kind: str, track_name: str, container: str) -> dict:
    item_type = _local_name(node)
    name = _field(node, "Name", "PrettyType")
    start = _number(_field(node, "Start"), integer=True)
    duration = _number(_field(node, "Duration"), integer=True)
    record_end = start + duration if start is not None and duration is not None else None
    source_start = _number(_field(node, "In"), integer=True)
    source_end = _number(_field(node, "Out"), integer=True)
    markers = _explicit_markers(node)
    caption_track = any(token in (track_name or "").casefold()
                        for token in ("subtitle", "caption"))
    is_caption = (caption_track or "subtitle" in item_type
                  or "caption" in item_type)
    transforms = {
        key: _number(_field(node, key)) for key in _TRANSFORM_KEYS
    }
    caption_text = _field(node, "Text", "SubtitleText", "CaptionText")
    if is_caption and caption_text is None:
        # Resolve represents some subtitle items by their displayed name.
        caption_text = name

    missing = []
    if start is None:
        missing.append("record_start_frame")
    if duration is None:
        missing.append("record_duration_frames")
    if source_start is None:
        missing.append("source_start_frame")
    if source_end is None:
        missing.append("source_end_frame")
    if track_kind == "video" and not is_caption:
        missing.extend(key for key, value in transforms.items()
                       if value is None)
    if is_caption and caption_text is None:
        missing.append("caption_text")

    return {
        "container": container,
        "uid": node.attrib.get("DbId"),
        "kind": item_type.removeprefix("sm2ti"),
        "track_kind": track_kind,
        "track_name": track_name,
        "name": name,
        "record_range_frames": {"start": start, "end": record_end},
        "source_range_frames": {"start": source_start, "end": source_end},
        "transform": transforms,
        "markers": markers,
        "caption": ({"text": caption_text} if is_caption else None),
        "missing_fields": missing,
    }


def _read_container(root, member: str) -> tuple[list[dict], list[dict]]:
    items = []
    tracks = []
    for vector in root.iter():
        track_kind = _TRACK_VECTORS.get(_local_name(vector))
        if track_kind is None:
            continue
        for track in vector.iter():
            if track is vector or _local_name(track) != "sm2titrack":
                continue
            track_name = _field(track, "Name") or ""
            item_nodes = [node for node in track.iter()
                          if node is not track and _local_name(node) in _ITEM_NAMES]
            track_items = [_item(node, track_kind, track_name, member)
                           for node in item_nodes]
            items.extend(track_items)
            tracks.append({"kind": track_kind, "name": track_name,
                           "items": track_items})
    return tracks, items


def read_drt_archive(path: str | Path) -> dict:
    """Parse `.drt` sequence containers into ranges, transforms and captions.

    Record ranges and explicit source in/out values are reported in frames.
    Omitted source or transform values remain ``None``; no default values
    are inferred from the container's opaque blobs.
    """
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.suffix.lower() != ".drt":
        raise ValueError(f"expected a .drt archive, got {source.name!r}")
    if not zipfile.is_zipfile(source):
        raise ValueError(f"not a ZIP-based DRT archive: {source}")

    timelines = []
    all_items = []
    all_markers = []
    with zipfile.ZipFile(source) as archive:
        members = sorted(
            info for info in archive.infolist()
            if not info.is_dir() and _SEQUENCE_ENTRY.search(info.filename))
        if not members:
            raise ValueError(f"DRT archive has no SeqContainer XML: {source}")
        for info in members:
            root = _parse_xml(archive.read(info), info.filename)
            tracks, items = _read_container(root, info.filename)
            # Item markers are already attached to their clip. Do not count
            # them again as sequence-level markers.
            markers = _explicit_markers(root, exclude_item_markers=True)
            all_items.extend(items)
            all_markers.extend(
                marker for item in items for marker in item["markers"])
            all_markers.extend(markers)
            timelines.append({"container": info.filename,
                              "tracks": tracks,
                              "markers": markers})

    missing_counts = Counter(field for item in all_items
                             for field in item["missing_fields"])
    captions = [item for item in all_items if item["caption"] is not None]
    return {
        "archive_path": str(source.resolve()),
        "timelines": timelines,
        "item_count": len(all_items),
        "caption_count": len(captions),
        "marker_count": len(all_markers),
        "missing_field_counts": dict(sorted(missing_counts.items())),
    }


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -m library.tools.drt_reader <archive.drt>",
              file=sys.stderr)
        return 2
    try:
        result = read_drt_archive(args[0])
    except Exception as exc:  # noqa: BLE001 - CLI reports the parse failure
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
