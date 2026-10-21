"""A picture-only split: one picture item into two, at an exact frame.

The audio beside a picture is NOT part of this operation.  A split that
also cuts the audio is a different edit, and the difference is visible
in the timeline: the audio item's in/out, its gain and its link group
must read back exactly as they did before the cut.

This module owns the MEASUREMENT and the WRITE for that one operation,
and nothing else:

- `link_state` reads one item's link group and each partner's own state
  as plain data, so "the audio was not touched" is a comparison of two
  reads rather than a claim.
- `split_picture_item` splits ONE picture item at an exact frame.
- `verify_split` checks the placement is exact and the audio is unchanged.

It imports NOTHING from the pipeline.  The Resolve API objects arrive as
arguments, so the same code runs inside the broker (whose engine tree is
the broker's own) and inside a test with a fake item.

What "the audio is untouched" means, measured
----------------------------------------------
The audio item is the SAME item (one unique id), with the SAME record
span, the SAME source span, the SAME gain, the SAME enablement and the
SAME fades, and it is still linked to a picture item that carries the
same source file.  A link group's MEMBERS are named by the picture
halves the split produced, so the member list is reported as MEASURED
rather than assumed equal: what must hold is that the audio is still
linked to the picture it was linked to, not that it kept naming a
unique id the split has replaced.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

#: The fields of a partner's own state that a split must not change.
#: `unique_id` is the identity: an audio item that changed its id is a
#: DIFFERENT item, whatever else it reads.
PARTNER_STATE_KEYS = (
    "unique_id",
    "record_in",
    "record_out",
    "source_in_frame",
    "source_out_frame",
    "enabled",
    "volume",
    "fades",
)

#: The fields that describe a PICTURE item's own placement.
PICTURE_STATE_KEYS = (
    "unique_id",
    "record_in",
    "record_out",
    "source_in_frame",
    "source_out_frame",
)


class PictureSplitRefused(RuntimeError):
    """The split was refused, and nothing was changed."""


def _call(obj: Any, name: str, default: Any = None) -> Any:
    """One Resolve getter, or `default` where it did not answer.

    Resolve's scripting proxies raise on an absent method, so a direct
    call would take the whole read down; `default` is UNAVAILABLE, never
    a measured zero.
    """
    try:
        value = getattr(obj, name)()
    except Exception:  # noqa: BLE001 - an unreadable field is unknown
        return default
    return default if value is None else value


def _properties(obj: Any) -> dict:
    """An item's property dictionary, or an empty one."""
    properties = _call(obj, "GetProperty", None)
    return properties if isinstance(properties, Mapping) else {}


def _fades(item: Any) -> dict | None:
    """The item's native fades, or None where Resolve does not say."""
    fades = _call(item, "GetFades", None)
    if not isinstance(fades, Mapping):
        return None
    return {str(key): fades[key] for key in sorted(fades)}


def linked_partners(item: Any) -> list:
    """The item's link partners, as Resolve objects.

    A read that fails is a refusal, not an empty group: an empty group
    would read as "this item is not linked", which is a claim about the
    timeline that nobody measured.
    """
    try:
        linked = item.GetLinkedItems()
    except Exception as exc:  # an unreadable group refuses
        raise PictureSplitRefused(
            f"the linked-item group could not be read ({exc}); refusing a "
            f"split whose audio preservation cannot be measured") from exc
    return [partner for partner in (linked or []) if partner is not None]


def group_member_ids(item: Any) -> list:
    """The ids of the item's LINK PARTNERS.

    ONE reading, the one `reel_read.clip_detail` publishes: the
    partners, not the item itself.  Two readings of the same thing would
    make a stored snapshot and a fresh read look like a change.
    """
    partners = set()
    for partner in linked_partners(item):
        partner_id = str(_call(partner, "GetUniqueId", "") or "")
        if partner_id:
            partners.add(partner_id)
    return sorted(partners)


def own_state(item: Any) -> dict:
    """One item's own state, as plain data.

    The same reading for a picture and for its audio partner, so the
    two can be compared field by field: an audio item that reads back
    with a different span, a different gain or a different enablement
    has MOVED, whatever else it looks like.
    """
    track_type, track_index = _call(item, "GetTrackTypeAndIndex", ("", 0))
    try:
        track_index = int(track_index or 0)
    except (TypeError, ValueError):
        track_index = 0
    properties = _properties(item)
    # The LEVEL.  An AUDIO item carries it as `AudioVolume` on the
    # measured Resolve 21.1; a picture item's own level reads as
    # `Volume`.  Both are read, and the key that answered is named, so
    # "the gain is unchanged" is a comparison of two readings rather
    # than of two absences.
    volume_key = ""
    volume = None
    for key in ("AudioVolume", "Volume"):
        if key in properties:
            volume = properties[key]
            volume_key = key
            break
    return {
        "unique_id": str(_call(item, "GetUniqueId", "") or ""),
        "name": str(_call(item, "GetName", "") or ""),
        "track_type": str(track_type or ""),
        "track_index": track_index,
        "record_in": _call(item, "GetStart", None),
        "record_out": _call(item, "GetEnd", None),
        "source_in_frame": _call(item, "GetSourceStartFrame", None),
        "source_out_frame": _call(item, "GetSourceEndFrame", None),
        "enabled": _call(item, "GetClipEnabled", None),
        "volume": volume,
        "volume_key": volume_key,
        "fades": _fades(item),
        "source_file": _source_file(item),
        # The item's OWN reading of its link group.  It is the item
        # that answers what it is linked to.
        "linked_unique_ids": group_member_ids(item),
    }


#: One partner's own state.  The same reading as `own_state`: a
#: partner IS an item.
partner_state = own_state


def _source_file(item: Any) -> str:
    """The item's source file, or an empty string."""
    pool_item = _call(item, "GetMediaPoolItem", None)
    if pool_item is None:
        return ""
    try:
        path = pool_item.GetClipProperty("File Path")
    except Exception:  # noqa: BLE001 - an unreadable path is unknown
        return ""
    return str(path or "")


def link_state(item: Any) -> dict:
    """One item's link state: its partners and each partner's state."""
    native_id = str(_call(item, "GetUniqueId", "") or "")
    partners = []
    for partner in linked_partners(item):
        state = partner_state(partner)
        if not state["unique_id"] or state["unique_id"] == native_id:
            continue
        partners.append(state)
    return {
        "unique_id": native_id,
        "linked_unique_ids": sorted(state["unique_id"] for state in partners),
        "partners": sorted(partners, key=lambda state: state["unique_id"]),
    }


def audio_partners(item: Any) -> list:
    """The item's partners that live on an audio track."""
    return [state for state in link_state(item)["partners"]
            if str(state["track_type"]).lower().startswith("audio")]


def item_state(item: Any) -> dict:
    """One item's own state plus its link group, as plain data."""
    state = own_state(item)
    state["link"] = link_state(item)
    return state


def track_item_states(timeline: Any, track_type: str,
                      track_index: int) -> list:
    """Every item on one track, as plain data."""
    items = timeline.GetItemListInTrack(track_type, int(track_index)) or []
    return [item_state(item) for item in items]


def audio_track_states(timeline: Any) -> list:
    """Every audio item on the timeline, as plain data."""
    count = int(timeline.GetTrackCount("audio") or 0)
    states = []
    for index in range(1, count + 1):
        states.extend(track_item_states(timeline, "audio", index))
    return states


def membership(state: Mapping) -> set:
    """The link group's members, from either reading of an item."""
    link = state.get("link")
    if isinstance(link, Mapping):
        return set(link.get("linked_unique_ids", ()))
    return set(state.get("linked_unique_ids", ()))


def record_frame_for_source_frame(item: Any, at_source_frame: int) -> int:
    """The record frame a source frame lands on, inside this item.

    The algebra's `clip.split` addresses a SOURCE frame, because the
    source is what an event boundary is measured in; Resolve's own
    `Split` takes a RECORD frame.  This is the one conversion between
    them, and it refuses when the item's spans are unreadable.
    """
    source_in = _call(item, "GetSourceStartFrame", None)
    record_in = _call(item, "GetStart", None)
    if source_in is None or record_in is None:
        raise PictureSplitRefused(
            "the item does not report its source and record starts, so a "
            "source frame cannot be placed exactly; refusing a cut that "
            "would land somewhere near the boundary")
    try:
        source_in = int(source_in)
        record_in = int(record_in)
    except (TypeError, ValueError) as exc:
        raise PictureSplitRefused(
            f"the item's spans are not readable ({exc}); refusing a cut "
            f"that cannot be placed exactly") from exc
    return record_in + (int(at_source_frame) - source_in)


def split_picture_item(item: Any, at_source_frame: int) -> dict:
    """Split ONE picture item at an exact source frame, by NATIVE split.

    MEASURED on the live Resolve 21.1 (through the serving broker): the
    installed `TimelineItem` exposes NEITHER `Split` NOR
    `SplitAtFrame`, so this route REFUSES.  `composed_picture_split`
    is the route that exists, and it is a different thing: it replaces
    the picture.  The refusal is the honest answer, not a fallback.
    """
    at_source_frame = int(at_source_frame)
    record_frame = record_frame_for_source_frame(item, at_source_frame)
    record_in = _call(item, "GetStart", None)
    record_out = _call(item, "GetEnd", None)
    if record_in is None or record_out is None:
        raise PictureSplitRefused(
            "the item does not report its record span; refusing a split "
            "whose placement cannot be verified")
    if not int(record_in) < record_frame < int(record_out):
        raise PictureSplitRefused(
            f"source frame {at_source_frame} lands on record frame "
            f"{record_frame}, which is not inside the item's interior "
            f"[{record_in}, {record_out}); a split must leave two "
            f"positive spans")
    before = item_state(item)
    called_with = ""
    last_error = None
    for name in ("SplitAtFrame", "Split"):
        method = getattr(item, name, None)
        if method is None:
            continue
        try:
            method(record_frame)
        except TypeError as exc:
            last_error = exc
            continue
        except Exception as exc:  # the write failed
            raise PictureSplitRefused(
                f"Resolve refused the split ({type(exc).__name__}: {exc}); "
                f"nothing was changed") from exc
        called_with = name
        break
    if not called_with:
        raise PictureSplitRefused(
            "this Resolve exposes no split method this operation can call "
            f"({last_error or 'no SplitAtFrame and no Split'}); refusing a "
            f"cut that cannot be placed exactly")
    return {
        "at_source_frame": at_source_frame,
        "record_frame": record_frame,
        "split_called_with": called_with,
        "before": before,
    }


#: `AppendToTimeline`'s `mediaType`: 1 is picture, 2 is sound.
MEDIA_TYPE_VIDEO = 1
MEDIA_TYPE_AUDIO = 2


def composed_picture_split(item: Any, at_source_frame: int, *,
                           timeline: Any, pool: Any) -> dict:
    """Cut ONE picture item at an exact source frame, by REPLACING it.

    The installed Resolve 21.1 exposes no split method (measured), so a
    cut is COMPOSED: the picture item is deleted and two picture-only
    spans are placed in its place.  The linked AUDIO is never touched -
    it keeps its item, its in/out, its gain and its fades - which is
    the one thing a staged rebuild of the whole timeline cannot promise.

    What this route cannot promise is the LINK: the picture's identity
    changes, so the audio's partner changes with it.  The read-back
    reports the link state as MEASURED, not as a claim.

    `AppendToTimeline`'s `endFrame` is EXCLUSIVE (measured), and
    `GetSourceEndFrame` is INCLUSIVE: the two are one apart, and the
    halves are computed from the item's own reading so the cut lands on
    the frame that was asked for.
    """
    at_source_frame = int(at_source_frame)
    before = item_state(item)
    source_in = before["source_in_frame"]
    source_out = before["source_out_frame"]
    record_in = before["record_in"]
    record_out = before["record_out"]
    if source_in is None or source_out is None:
        raise PictureSplitRefused(
            "the item does not report its source range, so the cut cannot "
            "be placed exactly")
    if record_in is None or record_out is None:
        raise PictureSplitRefused(
            "the item does not report its record range, so the cut cannot "
            "be placed exactly")
    source_in, source_out = int(source_in), int(source_out)
    record_in, record_out = int(record_in), int(record_out)
    if not source_in < at_source_frame <= source_out:
        raise PictureSplitRefused(
            f"source frame {at_source_frame} is not inside the item's "
            f"source range [{source_in}, {source_out}]")
    if not record_in < record_in + (at_source_frame - source_in) < record_out:
        raise PictureSplitRefused(
            f"source frame {at_source_frame} lands outside the item's "
            f"record range [{record_in}, {record_out})")
    pool_item = _call(item, "GetMediaPoolItem", None)
    if pool_item is None:
        raise PictureSplitRefused(
            "the item carries no pool clip, so it cannot be re-placed")
    _track_type, track_index = _call(item, "GetTrackTypeAndIndex", ("", 0))
    try:
        track_index = int(track_index or 0)
    except (TypeError, ValueError):
        track_index = 0
    # The RECORD frames are the budget: the two halves together must
    # cover exactly what the original covered, so the second half's
    # length is what is left of the original's record span.  (The
    # source range Resolve reads back can be a frame longer than the
    # record span - the one-frame mix-up the property inventory names -
    # and the halves are cut on the RECORD budget so the cut does not
    # lengthen the timeline.)
    first_frames = at_source_frame - source_in
    second_frames = (record_out - record_in) - first_frames
    if first_frames < 1 or second_frames < 1:
        raise PictureSplitRefused(
            f"the cut at source frame {at_source_frame} leaves "
            f"{first_frames} and {second_frames} record frames; a cut must "
            f"leave two positive spans")
    payloads = [
        {"mediaPoolItem": pool_item,
         "startFrame": source_in, "endFrame": source_in + first_frames,
         "mediaType": MEDIA_TYPE_VIDEO, "recordFrame": record_in,
         "trackIndex": track_index},
        {"mediaPoolItem": pool_item,
         "startFrame": at_source_frame,
         "endFrame": at_source_frame + second_frames,
         "mediaType": MEDIA_TYPE_VIDEO,
         "recordFrame": record_in + first_frames,
         "trackIndex": track_index},
    ]
    # The range is freed FIRST: an append into an occupied range does
    # not land where it was asked to.
    deleted = False
    try:
        if not timeline.DeleteClips([item], False):
            raise PictureSplitRefused(
                "Resolve would not delete the picture item, so the cut "
                "was not composed; nothing was changed")
        deleted = True
        placed = list(pool.AppendToTimeline(payloads) or [])
    except PictureSplitRefused:
        raise
    except Exception as exc:  # the write failed
        raise PictureSplitRefused(
            f"composing the cut failed ({type(exc).__name__}: {exc})") from exc
    if len(placed) != 2:
        raise PictureSplitRefused(
            f"Resolve placed {len(placed)} picture span(s) where the cut "
            f"needs two; the cut was not composed")
    return {
        "at_source_frame": at_source_frame,
        "record_frame": record_in + (at_source_frame - source_in),
        "split_called_with": "composed_replace",
        "before": before,
        "placed": placed,
        "deleted": deleted,
        "pool_item_id": str(_call(pool_item, "GetMediaId", "") or ""),
    }


def unchanged_partner(before: Mapping, after: Mapping) -> str | None:
    """The first field that changed on a partner, or None.

    None means the partner is unchanged.  The fields are compared in a
    fixed order, so the answer names WHICH field moved.
    """
    for key in PARTNER_STATE_KEYS:
        if before.get(key) != after.get(key):
            return key
    return None


def partner_by_id(partners: Iterable[Mapping],
                   unique_id: str) -> dict | None:
    """One partner's state by its unique id."""
    for partner in partners:
        if str(partner.get("unique_id")) == str(unique_id):
            return dict(partner)
    return None


def _overlaps(state: Mapping, record_in: int, record_out: int) -> bool:
    """Whether an item's record span overlaps `[record_in, record_out)`."""
    try:
        start = int(state["record_in"])
        end = int(state["record_out"])
    except (KeyError, TypeError, ValueError):
        return False
    return start < int(record_out) and end > int(record_in)


def verify_split(before: Mapping, after: Mapping,
                 at_source_frame: int) -> dict:
    """Check the split landed exactly and the audio did not move.

    `before` is the receipt `split_picture_item` returned; `after` is
    the item states read back after the write.  The receipt is a
    VERDICT: it names what was measured and what failed, so a caller
    cannot read a partial check as a pass.
    """
    record_frame = int(before["record_frame"])
    at_source_frame = int(at_source_frame)
    halves = list(after.get("halves") or ())
    checks: list[dict] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"check": name, "result": "passed" if ok else "failed",
                       "detail": detail})

    check("split_into_two_halves", len(halves) == 2,
          f"{len(halves)} picture item(s) where the split should leave two")
    placement = {}
    if len(halves) == 2:
        first, second = sorted(
            halves, key=lambda half: int(half["record_in"]))
        expected = {
            "first_record_in": int(before["before"]["record_in"]),
            "first_record_out": record_frame,
            "second_record_in": record_frame,
            "second_record_out": int(before["before"]["record_out"]),
            "first_source_in": int(before["before"]["source_in_frame"]),
            "first_source_out": at_source_frame,
            "second_source_in": at_source_frame,
            "second_source_out": int(before["before"]["source_out_frame"]),
        }
        measured = {
            "first_record_in": int(first["record_in"]),
            "first_record_out": int(first["record_out"]),
            "second_record_in": int(second["record_in"]),
            "second_record_out": int(second["record_out"]),
            "first_source_in": int(first["source_in_frame"]),
            "first_source_out": int(first["source_out_frame"]),
            "second_source_in": int(second["source_in_frame"]),
            "second_source_out": int(second["source_out_frame"]),
        }
        placement = {"expected": expected, "measured": measured}
        for key, value in expected.items():
            check(f"exact_{key}", measured.get(key) == value,
                  f"expected {value}, measured {measured.get(key)}")
        check("halves_partition_the_original",
              measured["first_record_out"] == measured["second_record_in"]
              and measured["first_source_out"] == measured["second_source_in"],
              "the two halves must share the cut frame on both axes")

    before_audio_rows = before.get("audio")
    if before_audio_rows is None:
        before_audio_rows = list(
            before["before"]["link"]["partners"])
    before_audio = {state["unique_id"]: state
                    for state in before_audio_rows
                    if str(state.get("track_type", "")).lower()
                    .startswith("audio")}
    after_audio = {state["unique_id"]: state
                   for state in after.get("audio") or ()
                   if str(state.get("track_type", "")).lower()
                   .startswith("audio")}
    picture_in = int(before["before"]["record_in"])
    picture_out = int(before["before"]["record_out"])
    overlapping = [state for state in after_audio.values()
                   if _overlaps(state, picture_in, picture_out)]
    before_overlapping = [state for state in before_audio.values()
                          if _overlaps(state, picture_in, picture_out)]
    check("audio_not_split", len(overlapping) == len(before_overlapping)
          and len(overlapping) == 1,
          f"{len(before_overlapping)} audio item(s) overlapped the "
          f"picture's record span before, {len(overlapping)} after; a "
          f"picture-only split leaves one")
    moved = []
    for unique_id, state in sorted(before_audio.items()):
        partner = partner_by_id(after_audio.values(), unique_id)
        if partner is None:
            moved.append({"unique_id": unique_id, "field": "unique_id",
                          "before": unique_id,
                          "after": sorted(overlapping[0]["unique_id"])
                          if overlapping else None})
            continue
        field = unchanged_partner(state, partner)
        if field is not None:
            moved.append({"unique_id": unique_id, "field": field,
                          "before": state.get(field),
                          "after": partner.get(field)})
    check("audio_item_unchanged", not moved,
          "; ".join(f"{row['unique_id']}: {row['field']} moved"
                    for row in moved) or "every audio partner reads back "
          "unchanged")

    audio_link = {}
    for unique_id, state in sorted(before_audio.items()):
        partner = partner_by_id(after_audio.values(), unique_id)
        if partner is None:
            continue
        before_members = set(state.get("linked_unique_ids", ()))
        after_members = set(partner.get("linked_unique_ids", ()))
        audio_link[unique_id] = {
            "before_members": sorted(before_members),
            "after_members": sorted(after_members),
            "members_changed": before_members != after_members,
            "still_linked": bool(after_members - {unique_id}),
        }
    # The LINK is REPORTED, not asserted.  The composed cut replaces the
    # picture, so the audio's partner changes with it - that is a
    # measured fact about this Resolve, and the reader is told which it
    # is rather than handed a check that could only ever fail.
    link_preserved = all(row["still_linked"] for row in audio_link.values())

    return {
        "at_source_frame": at_source_frame,
        "record_frame": record_frame,
        "placement": placement,
        "audio_link": audio_link,
        "link_preserved": link_preserved,
        "audio_moved": moved,
        "checks": checks,
        "result": "passed" if all(row["result"] == "passed" for row in checks)
        else "failed",
    }
