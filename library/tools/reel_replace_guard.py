"""A replace is a diff, and the diff refuses (issue #925).

A build that REPLACES a timeline is judged only against its own PLAN, so
no gate can fail on a feature the plan does not name. Two rebuilds
proved it in one day: a `--only-reel` rebuild dropped the 24-frame
Akshita reaction cutaway (V1 3 clips -> 2, hole closed, conformance
clean), and a build from a tree with no Remotion `node_modules` dropped
the whole V5 'Semantic' row (4 items -> absent, conformance clean).

So `promote_staged_reels` - the ONE place a captain-visible timeline is
replaced - diffs the incoming timeline against the one it retires, LIVE
AGAINST LIVE, before the first rename. The plan is the thing that is
wrong, so it is neither side of the comparison.

What refuses
------------
Per final timeline, per row (keyed `"<media>:<track name>"`, e.g.
`"video:Semantic"` - indices shift when rows come and go, names do
not):

- a row of the retiring timeline that the incoming one does not have
  at all (a whole feature class gone);
- a row the incoming one holds FEWER items on, unless it reads as a
  JOIN (below).
- a disabled Semantic item missing from staging does not count as a loss
  when the disabled-carry check records that no enabled graphic occupies
  its place.

Frame totals are REPORTED per row, and join the trigger as the join
half: a shortened cut holds the same items over fewer frames, and
that must pass. The video:Subtitles row has a narrower content proof
for row reflow: exact visible words and their timed windows may justify
a shorter row when every word still lands within one frame. Grain
removed and a j-cut deleted likewise move no item count, which is why a
count trigger is not a nuisance for the legitimate reductions the
captain actually makes.

A join is not a loss
--------------------
Keep insistence `lc-0004` withdrew a take cut, so two adjacent Craig
clips placed as two items became one item placed over MORE frames -
and the count trigger read the merge as content lost. A count drop
with the frames kept or grown is a merge, not a deletion, PROVIDED
every retired item's name still plays on the incoming row: names are
the source-coverage proxy this snapshot carries (name plus span is
already the diff's whole identity - `reel_read.rows_of` - and reel rows
carry source names, so two halves of one source share one name while
a dropped cover like `LC4932 cover` has one nothing else carries).

So a non-subtitle row with fewer incoming items is a JOIN, and passes
undeclared, exactly when the incoming row holds at least as many frames
AND every distinct retired name still occurs among the incoming items.
Both halves are load-bearing, and the round's own cutaway is the
counter-example that proves frames alone are not enough: that loss
went 3 items to 2 over EQUAL frames (hole closed), so a frames-only
rule would have waved a real deletion through. Names refuse it.

Subtitle reflow uses `caption_tokens` from each rendered sidecar and,
when the row shrinks, `caption_word_windows` reconstructed in timeline
frames from each timed word and its clip placement. Visible copy must
match the sidecar's timed words. The complete row token sequence and
every word's start/end frame must match (one frame of rounding is
allowed); the only spelling normalization joins adjacent `19`/`20` and
two-digit year fragments. Missing text or timing evidence refuses.

What this still cannot see, stated plainly: a substitution that keeps
every name and grows the frames - one same-named item's seconds
swapped for another's - passes. Timeline spans cannot tell those
seconds apart; the guard never saw source ranges, before or now, so
this is the standing limit of a span-based diff, not a new hole.
A same-name substitution that SHRINKS the frames still refuses on
the count drop without join cover.

What a declared reduction looks like
------------------------------------
A reduction the build intends is declared by ROW, never by blanket: the
caller names the specific row(s) that may shrink. `allow_drops` is a
mapping of final timeline name to the row keys allowed to shrink on
it, e.g. `{"Reel 09 - ... (final)": ["video:Semantic"]}`. A bare track
name (`"Semantic"`) matches that name on any media type; the canonical
`"video:Semantic"` form is what the refusal message prints, so it can
be copied back verbatim.

Raw caller specs (`parse_specs`) add one convenience shape,
`"FINAL::video:Semantic"` for one reel and `"video:Semantic"` for every
reel the invocation promotes, which is what `build-reels --allow-drop`
collects. There is deliberately no "allow everything" value: a blanket
override is the same as no guard.

Fail closed
-----------
A retiring timeline that cannot be read REFUSES rather than passes - a
guard that fails open reads as protection. So does an incoming staging
that cannot be read: promoting what cannot be judged is the defect.
Fresh builds (no timeline under the final name yet) skip the diff for
that reel - there is nothing being replaced.

Editor changes: carried before they are refused
-----------------------------------------------
`detect_editor_changes` records what the editor changed since Ren's
last read; `library/tools/editor_edit_carry.py` applies the changes it
can state in source ranges (cuts, enabled state, transforms) to staging
and verifies them; `protect_editor_changes` then refuses whatever the
re-read staging still does not carry.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from library.tools.resolve_lock import under_lease

#: How many missing items a refusal names inline per row. The report the
#: promote result carries names every one; the message stays readable.
MISSING_SHOWN = 5


class ReplaceGuardUnreadable(RuntimeError):
    """A timeline in the comparison could not be read, and this says which."""


class ReplaceGuardRefused(RuntimeError):
    """The incoming timeline carries less than the one it would replace."""

    def __init__(self, message: str, allow_drop_rows=()) -> None:
        super().__init__(message)
        # Only the row-diff refusal knows which declaration would let it
        # proceed. Other subclasses in this module refuse for editor
        # changes, and must not be mistaken for `--allow-drop` refusals.
        self.allow_drop_rows = tuple(allow_drop_rows or ())


class EditorChangeRefused(ReplaceGuardRefused):
    """Unattributed live edits would be lost by this replacement."""


PRESERVATION_FIELDS = (
    "track_type", "track_index", "track_name", "name", "source_identity",
    "source_in_frame", "source_out_frame", "record_in", "record_out",
    "duration", "enabled", "transform", "composite", "fusion", "color",
    "clip_color", "flags", "markers",
)

# This is the replacement boundary's honest statement of what the shared
# Resolve reader can prove.  In particular, reading a grade/Fusion summary
# is not the same as being able to copy the native object and its full state.
# Keep the matrix alongside the guard that decides whether replacement is
# safe; docs/evidence/edit_preservation.md explains the evidence and limits.
PRESERVATION_CONTRACT = {
    "source_identity_and_windows": "supported",
    "placement_duration_and_track": "supported",
    "linked_groups": "unsupported",
    "transform_and_crop": "partially_observable",
    "enabled_state": "supported",
    "clip_and_timeline_markers": "partially_observable",
    "grades_and_node_state": "partially_observable",
    "fusion_comps_and_animation": "partially_observable",
    "audio_effects_automation_and_routing": "unsupported",
    "manual_edits": "partially_observable",
    "signoffs": "supported",
}


def _has_native_payload(value) -> bool:
    """Whether a read contains non-default native state worth preserving."""
    if value is None or value is False or value == 0 or value == "" \
            or value == []:
        return False
    if isinstance(value, dict):
        return any(_has_native_payload(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_has_native_payload(item) for item in value)
    return True


def _has_native_composite_payload(value) -> bool:
    """Ignore Resolve's default opacity and composite mode in a read."""
    if not isinstance(value, dict):
        return _has_native_payload(value)
    non_default = {}
    for key, item in value.items():
        if key == "Opacity" and _same_value(item, 100.0):
            continue
        if key == "CompositeMode" and item in (0, "Normal"):
            continue
        if _has_native_payload(item):
            non_default[key] = item
    return bool(non_default)


def _native_state_losses(live: dict, staged: dict) -> list[str]:
    """Find observed grade/Fusion/composite state lost on matching media.

    These properties are read only partially and have no qualified native
    copy operation. If the retiring item has a non-empty value, replacement
    must either read the same value on its matching source passage or refuse.
    """
    losses = []
    observed = ("color", "fusion", "composite")
    staged_by_passage = defaultdict(list)
    for item in staged.get("items", ()):
        key = (item.get("source_identity"), item.get("track_type"),
               item.get("track_index"))
        staged_by_passage[key].append(item)
    for item in live.get("items", ()):
        values = {}
        for field in observed:
            value = item.get(field)
            if field == "color":
                value = _normalized_color(value)
            has_payload = (
                _has_native_composite_payload(value)
                if field == "composite"
                else _has_native_payload(value))
            if has_payload:
                values[field] = value
        if not values:
            continue
        # A carried trim can shorten the source span while preserving the
        # same native grade/comp on the kept material. Resolve's readback
        # still gives that edit the same source identity, so accept one
        # staged passage wholly inside the observed passage. Requiring an
        # exact span here made a qualified, verified trim look like a lost
        # Fusion comp and blocked its promotion.
        passage_key = (item.get("source_identity"), item.get("track_type"),
                       item.get("track_index"))
        candidates = [candidate for candidate in staged_by_passage.get(
            passage_key, ())
            if candidate.get("source_in_frame") is not None
            and candidate.get("source_out_frame") is not None
            and item.get("source_in_frame") is not None
            and item.get("source_out_frame") is not None
            and candidate["source_in_frame"] >= item["source_in_frame"]
            and candidate["source_out_frame"] <= item["source_out_frame"]]
        if len(candidates) != 1 or any(
                not _same_preservation_value(field, value,
                                             candidates[0].get(field))
                for field, value in values.items()):
            label = item.get("name") or item.get("source_identity") or "item"
            losses.extend(f"{label}: {field}" for field in values)
    return losses
#: The transform properties stored in the project resolution's unit.
UNIT_EPOCH_KEYS = ("Pan", "Tilt")
TIMELINE_SETTING_KEYS = (
    "timelineFrameRate", "timelineResolutionWidth",
    "timelineResolutionHeight", "timelineStartTimecode",
    "timelinePixelAspectRatio", "timelineVideoMonitoringFormat",
    "timelineInterlaceProcessing",
)


def _timeline_settings(timeline) -> dict:
    settings = {}
    try:
        complete = timeline.GetSetting()
    except Exception:  # noqa: BLE001 - Resolve versions may require a key
        complete = None
    if isinstance(complete, dict):
        settings.update({key: _plain_setting(value)
                         for key, value in complete.items()})
    for key in TIMELINE_SETTING_KEYS:
        try:
            settings[key] = _plain_setting(timeline.GetSetting(key))
        except Exception:  # noqa: BLE001
            settings.setdefault(key, None)
    return settings


def _pan_tilt_unit_resolution(project) -> list[int] | None:
    """The project resolution under which Resolve stores Pan and Tilt.

    A timeline's output resolution is not this unit. Unknown stays
    unknown so a later carry can refuse an unprovable comparison.
    """
    try:
        width = int(project.GetSetting("timelineResolutionWidth"))
        height = int(project.GetSetting("timelineResolutionHeight"))
    except Exception:  # noqa: BLE001 - an unreadable unit is recorded as unknown
        return None
    return [width, height] if width > 0 and height > 0 else None


def _plain_setting(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _plain_setting(item)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_setting(item) for item in value]
    return str(value)


@under_lease("read project timeline inventory", exclusive=False)
def timeline_inventory(project) -> list[dict]:
    """Read project timeline names, unique ids and settings as plain data."""
    entries = []
    try:
        count = int(project.GetTimelineCount() or 0)
        for index in range(1, count + 1):
            timeline = project.GetTimelineByIndex(index)
            if timeline is None:
                raise ValueError(f"timeline {index} returned no object")
            settings = _timeline_settings(timeline)
            try:
                unique_id = timeline.GetUniqueId()
            except Exception:  # noqa: BLE001
                unique_id = None
            entries.append({
                "name": timeline.GetName(),
                "unique_id": str(unique_id) if unique_id else None,
                "settings": settings,
            })
    except Exception as unreadable:
        raise ReplaceGuardUnreadable(
            f"the project timeline inventory could not be read "
            f"({unreadable}); replacement refuses rather than guess "
            f"which timelines it may touch.") from unreadable
    return entries


def assert_target_inventory_unchanged(before: list[dict],
                                     current: list[dict],
                                     finals) -> None:
    """Refuse targets that appeared or changed identity during a build."""
    by_name_before = {entry["name"]: entry for entry in before}
    by_name_now = {entry["name"]: entry for entry in current}
    for final in finals:
        old, now = by_name_before.get(final), by_name_now.get(final)
        if now is None:
            if old is not None and old.get("unique_id"):
                renamed = next((entry for entry in current
                                if str(entry.get("unique_id")) ==
                                str(old["unique_id"])), None)
                if renamed is not None:
                    raise EditorChangeRefused(
                        f"REFUSING to replace {final!r}: its build-start "
                        f"timeline was renamed to {renamed['name']!r} during "
                        f"the build. The editor's timeline remains "
                        f"untouched.")
            continue
        if old is None:
            raise EditorChangeRefused(
                f"REFUSING to replace {final!r}: this timeline was not in "
                f"the project's inventory when the build began. It may be "
                f"a timeline the editor created during the build, so it "
                f"remains untouched.")
        if not old.get("unique_id") or not now.get("unique_id"):
            raise EditorChangeRefused(
                f"REFUSING to replace {final!r}: its identity cannot be "
                f"compared with the build-start inventory, so ownership "
                f"is unknown and the timeline remains untouched.")
        if str(old["unique_id"]) != str(now["unique_id"]):
            raise EditorChangeRefused(
                f"REFUSING to replace {final!r}: it now has timeline id "
                f"{now['unique_id']!r}, while the build-start inventory "
                f"recorded {old['unique_id']!r}. A newly created or renamed "
                f"editor timeline remains untouched.")


def item_source_identity(detail: dict) -> str:
    """What a `reel_read` item plays, as the snapshot names it."""
    source_file = str(detail["source_file"] or "")
    media_id = str(detail["media_pool_item_id"] or "")
    item_id = str(detail.get("unique_id") or "")
    if media_id:
        return f"media:{media_id}"
    if source_file:
        return "file:" + os.path.normcase(os.path.realpath(source_file))
    if item_id:
        return f"generator-item:{item_id}"
    return (f"generator:{detail['track_type']}:"
            f"{detail['track_name']}:{detail['name']}")


def snapshot_items(tracks) -> list[dict]:
    """The snapshot's items from `reel_read.read_tracks` rows."""
    items = []
    for track in tracks:
        for detail in track["clips"]:
            if not isinstance(detail["enabled"], bool):
                raise TypeError(
                    f"enabled state for {detail['name']!r} is unreadable")
            item = {key: detail.get(key) for key in
                    PRESERVATION_FIELDS if key in detail}
            item["source_identity"] = item_source_identity(detail)
            # Carried for the file-identity fallback
            # (`_same_file_candidates`, `editor_edit_carry._file_items`)
            # and deliberately NOT compared: the stored baselines predate
            # it, and a key every live read has that no baseline holds
            # would read as an editor change on every item (Reel 09,
            # 2026-10-06: 64 items, 64 phantom changes).
            item["source_file"] = detail.get("source_file") or ""
            item["unique_id"] = str(detail.get("unique_id") or "")
            # Headroom is needed before a source-window extension is
            # composed. It is not a preservation delta by itself: the
            # source range already states what the editor changed.
            item["left_offset"] = detail.get("left_offset")
            item["right_offset"] = detail.get("right_offset")
            transform = detail.get("transform") or {}
            item["composite"] = {
                key: transform.get(key)
                for key in ("Opacity", "CompositeMode")
                if key in transform
            }
            items.append(item)
    return items


def full_timeline_snapshot(timeline, project, project_folder=None) -> dict:
    """Capture editable state through the shared reel reader."""
    from library.tools import marker_feedback, reel_read
    from library.tools.resolve_lock import cursor_excursion

    try:
        with cursor_excursion(project, timeline,
                              f"snapshot {timeline.GetName()}"):
            tracks = reel_read.read_tracks(
                timeline, resolve_project=project)
            raw_markers = marker_feedback.read_notes(
                timeline, project_folder=project_folder)
            markers = []
            for marker in raw_markers:
                note = (asdict(marker) if is_dataclass(marker)
                        else dict(marker))
                if note.get("source") != "timeline_marker":
                    continue
                # `read_notes` also returns clip and media-pool notes,
                # which are captured on each item by `reel_read`. Keep
                # only the timeline plane here, and omit derived context
                # such as `read_at`, attached clips, and timecode so the
                # snapshot is stable across reads.
                markers.append({
                    "source": "timeline_marker",
                    "frame": note["frame"],
                    "frame_in_timeline_space":
                        note["frame_in_timeline_space"],
                    "color": note["color"],
                    "name": note["name"],
                    "note": note["note"],
                    "duration_frames": note["duration_frames"],
                    "custom_data": note["custom_data"],
                    "custom_data_raw": note["custom_data_raw"],
                })
            settings = _timeline_settings(timeline)
            for key in ("timelineFrameRate", "timelineResolutionWidth",
                        "timelineResolutionHeight"):
                if settings[key] in (None, ""):
                    raise ValueError(f"timeline setting {key!r} is unreadable")
            float(settings["timelineFrameRate"])
            int(settings["timelineResolutionWidth"])
            int(settings["timelineResolutionHeight"])
            try:
                unique_id = timeline.GetUniqueId()
            except Exception:  # noqa: BLE001
                unique_id = None
            items = snapshot_items(tracks)
            if project_folder:
                from library.tools.reel_disabled_clip_carry import (
                    add_semantic_graphic_identities,
                )
                add_semantic_graphic_identities(
                    project_folder, timeline.GetName(), items)
            return {
                "metadata": {
                    "transform_unit_resolution":
                        _pan_tilt_unit_resolution(project),
                },
                "timeline": {
                    "name": timeline.GetName(),
                    "unique_id": str(unique_id) if unique_id else None,
                    "settings": settings,
                    "start_frame": timeline.GetStartFrame(),
                    "end_frame": timeline.GetEndFrame(),
                },
                "items": items,
                "markers": markers,
            }
    except Exception as unreadable:
        raise ReplaceGuardUnreadable(
            f"the full state of timeline {timeline.GetName()!r} could not "
            f"be read ({unreadable}); replacement refuses rather than "
            f"overwrite what it cannot see.") from unreadable


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      default=str)


#: How far two numeric reads may differ and still be one value. Resolve
#: hands some properties back as a float where they were read as an int
#: (`AudioPitchSemiTones` 0 -> 0.0 on a re-placed item, measured
#: 2026-10-02) and some one ulp off (`edit_state_preservation` tolerance).
VALUE_TOLERANCE = 1e-6

#: How far two transform reads may differ and still be one value: half
#: of the 0.001 the Inspector displays Pan, Tilt, Zoom and the rest to,
#: so two values that show the same number are the same, and a change
#: anyone could see is not. Reel 13, 2026-10-05: the rebuild computed
#: Tilt -174.01 where the captain's reel held -174.01025 - one value,
#: rounded differently - and the guard refused it as an editor change.
TRANSFORM_TOLERANCE = 5e-4


def _same_value(a, b, tolerance: float = VALUE_TOLERANCE) -> bool:
    """One value, read twice: numbers by magnitude, containers by member."""
    numbers = (int, float)
    if (isinstance(a, numbers) and isinstance(b, numbers)
            and not isinstance(a, bool) and not isinstance(b, bool)):
        return abs(float(a) - float(b)) <= tolerance
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(
            _same_value(a[key], b[key], tolerance) for key in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(
            _same_value(x, y, tolerance) for x, y in zip(a, b))
    return _canonical(a) == _canonical(b)


def _normalized_color(value):
    """Treat Resolve's default color graph shape as no authored grade.

    Resolve has returned the same ungraded item with `luts` omitted, set to
    `None`, and set to `[]`. Its one-node default graph is structural, not
    a grade. Those shapes carry no color edit. A graph with additional nodes
    or an assigned LUT remains part of the color value and is compared.
    """
    if not isinstance(value, dict):
        return value
    normalized = dict(value)
    if normalized.get("node_count") == 1:
        normalized.pop("node_count", None)
    luts = normalized.get("luts")
    if luts is None or (isinstance(luts, (list, tuple)) and not luts):
        normalized.pop("luts", None)
    return normalized


def _normalized_fusion(value):
    """Normalize the derived Fusion window and absent matte readback.

    PR 1786 (2026-10-05) added `mattes` to every fusion read, `None` on an
    item with no matte comp. Every snapshot recorded before it lacks the
    key, so a rebuild compared every untouched item's `{...}` against
    `{..., 'mattes': None}` and refused six reels on "the Fusion comp
    changed". `media_windows` is a measurement of where MediaIn reads,
    which necessarily changes when an approved trim is replayed and the
    comp pass is rerun on the kept range. Comp identity and matte state
    remain fully compared; comp coverage is checked by the comp-window
    verifier after the pass.
    """
    if isinstance(value, dict):
        value = {key: item for key, item in value.items()
                 if key != "media_windows"}
        if value.get("mattes") is None:
            value.pop("mattes", None)
    return value


def _same_preservation_value(field: str, before, after) -> bool:
    """Compare one preserved item field using its domain semantics."""
    if field == "color":
        before, after = _normalized_color(before), _normalized_color(after)
    if field == "fusion":
        before, after = _normalized_fusion(before), _normalized_fusion(after)
    if field == "transform":
        return _same_value(before, after, TRANSFORM_TOLERANCE)
    return _same_value(before, after)


# Fields whose read gained a no-op shape after records were written: an
# empty LUT list (color) and `mattes: None` (fusion, #1786). A pending
# record of either compares the same today and is no edit.
_LEGACY_NO_OP_FIELDS = ("color", "fusion")


def _normalize_pending_editor_record(record: dict) -> dict:
    """Drop legacy no-op color and fusion deltas without touching other edits.

    #1816 taught the comparison that `mattes: None` is no matte, but
    records already pending kept their `fusion` delta and still refused
    Reels 03 and 08 on items nobody touched.
    """
    normalized_changes = []
    for change in record.get("changes", ()):
        if change.get("kind") != "item_changed":
            normalized_changes.append(change)
            continue
        changed = change.get("changed")
        if not isinstance(changed, dict):
            normalized_changes.append(change)
            continue
        meaningful = {}
        for field, values in changed.items():
            no_op = (
                field in _LEGACY_NO_OP_FIELDS
                and isinstance(values, dict)
                and "before" in values
                and "after" in values
                and _same_preservation_value(field, values["before"], values["after"])
            )
            if not no_op:
                meaningful[field] = values
        if not meaningful:
            continue
        normalized_changes.append(
            change if meaningful == changed else {**change, "changed": meaningful}
        )
    return {**record, "changes": normalized_changes}


def stable_item_key(item: dict) -> tuple:
    return (item.get("track_type"), item.get("track_index"),
            item.get("track_name"),
            item.get("source_identity"), item.get("source_in_frame"),
            item.get("source_out_frame"))


def _marker_key(marker: dict) -> str:
    return _canonical(marker)


def _marker_content_key(marker: dict) -> tuple:
    """Marker content identity, excluding its timeline placement."""
    return tuple(_canonical(marker.get(field)) for field in (
        "source", "color", "name", "note", "duration_frames",
        "custom_data", "custom_data_raw"))


def snapshot_diff(before: dict, after: dict) -> list[dict]:
    """Describe item, setting and marker changes without inferring intent."""
    changes = []
    old_items, new_items = {}, {}
    for item in before.get("items", ()):
        old_items.setdefault(stable_item_key(item), []).append(item)
    for item in after.get("items", ()):
        new_items.setdefault(stable_item_key(item), []).append(item)
    for key in sorted(set(old_items) | set(new_items), key=_canonical):
        old_group, new_group = old_items.get(key, []), new_items.get(key, [])
        paired = min(len(old_group), len(new_group))
        for old, new in zip(old_group[:paired], new_group[:paired]):
            changed = {
                field: {"before": old.get(field), "after": new.get(field)}
                for field in PRESERVATION_FIELDS
                if not _same_preservation_value(
                    field, old.get(field), new.get(field))
            }
            if changed:
                changes.append({"kind": "item_changed", "identity": key,
                                "before": old, "after": new,
                                "changed": changed})
        for old in old_group[paired:]:
            changes.append({"kind": "item_removed", "identity": key,
                            "before": old, "after": None, "changed": {}})
        for new in new_group[paired:]:
            changes.append({"kind": "item_added", "identity": key,
                            "before": None, "after": new, "changed": {}})

    old_tl = before.get("timeline") or {}
    new_tl = after.get("timeline") or {}
    for field in ("name", "unique_id"):
        old, new = old_tl.get(field), new_tl.get(field)
        if _canonical(old) != _canonical(new):
            changes.append({"kind": "timeline_identity", "field": field,
                            "before": old, "after": new})
    old_settings = old_tl.get("settings") or {}
    new_settings = new_tl.get("settings") or {}
    for key in sorted(set(old_settings) | set(new_settings)):
        old, new = old_settings.get(key), new_settings.get(key)
        if _canonical(old) != _canonical(new):
            changes.append({"kind": "timeline_setting", "field": key,
                            "before": old, "after": new})
    for field in ("start_frame", "end_frame"):
        old, new = old_tl.get(field), new_tl.get(field)
        if _canonical(old) != _canonical(new):
            changes.append({"kind": "timeline_setting", "field": field,
                            "before": old, "after": new})

    old_markers = {_marker_key(marker): marker
                   for marker in before.get("markers", ())}
    new_markers = {_marker_key(marker): marker
                   for marker in after.get("markers", ())}
    for key in sorted(old_markers.keys() - new_markers.keys()):
        changes.append({"kind": "marker_removed", "identity": key,
                        "before": old_markers[key], "after": None})
    for key in sorted(new_markers.keys() - old_markers.keys()):
        changes.append({"kind": "marker_added", "identity": key,
                        "before": None, "after": new_markers[key]})
    return changes


#: What a re-planned caption card must match of the editor's card: all
#: but what names the render (`_replanned_caption_cards`).
_CAPTION_SPAN_FIELDS = tuple(
    field for field in PRESERVATION_FIELDS
    if field not in ("name", "source_identity", "source_in_frame",
                     "source_out_frame"))


def _replanned_caption_cards(change: dict, staged: dict) -> list[dict]:
    """Staged cards that re-render the caption card `change` names.

    A rebuild that re-plans the captions renders every card under a new
    name and media pool item, so an editor's change to a card can never
    match staging by source identity. The card's stable identity is its
    caption segment (`subtitle_segment_id.stable_prefix`) on the same
    row; what must then hold is everything but the render's name and
    source frames - its record span above all, which is what
    `editor_edit_carry` carries (`caption_span`, and ripples).
    """
    from library.tools import subtitle_segment_id

    def segment(item):
        name = str(item.get("name") or "")
        if name.lower().endswith(".mov"):
            name = name[:-4]
        if not subtitle_segment_id.is_segment_id(name):
            return None
        return subtitle_segment_id.stable_prefix(name)

    item = change.get("after") or {}
    wanted = segment(item)
    if wanted is None:
        return []
    row = (item.get("track_type"), item.get("track_index"),
           item.get("track_name"))
    return [other for other in staged.get("items", ())
            if (other.get("track_type"), other.get("track_index"),
                other.get("track_name")) == row
            and segment(other) == wanted]


#: What a change matched by source file must still hold: every preserved
#: field but the media pool identity, which is the whole reason it
#: matched by file - the same file under two pool items is two identities.
_FILE_MATCH_FIELDS = tuple(
    field for field in PRESERVATION_FIELDS if field != "source_identity")


def _same_file_candidates(change: dict, staged: dict) -> list[dict]:
    """Staged items playing the changed item's source FILE over its range.

    The same source file under two media pool items (an accidental
    duplicate import) is two identities, and a rebuild that picks the
    other one leaves the change nothing to match by identity. The file is
    the rebuild-invariant half of that identity, so a staged item playing
    the same file over the same range on the same row is the same
    placement. Zero or 2+ candidates leave the change unmatched - never
    guessed onto one of them.
    """
    item = change.get("after") or change.get("before") or {}
    source_file = str(item.get("source_file") or "")
    if not source_file:
        return []
    start, end = item.get("source_in_frame"), item.get("source_out_frame")
    if not isinstance(start, int) or not isinstance(end, int):
        return []
    row = (item.get("track_type"), item.get("track_index"),
           item.get("track_name"))
    return [other for other in staged.get("items", ())
            if (other.get("track_type"), other.get("track_index"),
                other.get("track_name")) == row
            and str(other.get("source_file") or "") == source_file
            and (other.get("source_in_frame"),
                 other.get("source_out_frame")) == (start, end)]


#: What a carried extend must still hold: the placement, not the
#: fresh build's cosmetics. A rebuild re-derives the transform, the
#: Fusion comps and the grade of every item it places, and the conformance
#: and verify passes judge that render - the editor-change guard judges
#: whether the editor's EDIT survived, which for an extend is the file and
#: the source and record spans on the row. Measured 2026-10-06 (Reel 13):
#: the carried +6f LC4932 extend played identically on staging, but the
#: fresh comps and holds differ field-by-field from the hand-tuned live
#: item, so the full preservation comparison refused a carried edit.
_EXTEND_SPAN_FIELDS = (
    "track_type", "track_index", "track_name", "source_file",
    "source_in_frame", "source_out_frame", "record_in", "record_out",
    "duration",
)


def _extend_span_candidates(change: dict, staged: dict) -> list[dict]:
    """Staged items playing the added file over the added spans.

    `_same_file_candidates` already filters by file, source span and row,
    but two placements of one file on a row (Reel 13's V1 plays LC4932
    twice) need the record span to tell them apart. Zero or 2+
    candidates leave the change unmatched - never guessed onto one.
    """
    item = change.get("after") or {}
    return [other for other in _same_file_candidates(change, staged)
            if (other.get("record_in"), other.get("record_out"))
            == (item.get("record_in"), item.get("record_out"))]


def _note_content(marker: dict) -> tuple:
    return tuple(_canonical(marker.get(field)) for field in
                 ("color", "name", "note", "duration", "custom_data"))


def _note_landed_on_row(change: dict, staged: dict) -> bool:
    """A markers-only change whose notes sit on the same row of staging.

    The captain's ruling, 2026-10-05: a note typed on an item the rebuild
    re-rendered under a new file is carried onto the one item on that
    row playing the same record frame (`marker_carry.plan_clip_carry`).
    The item it lands on has a new identity, so what is judged is the
    note: every note he added is on an item of that row, and none he
    removed is. The key is the re-rendered item's own source frame, so
    the comparison is the note's content, not its key.
    """
    if (change.get("kind") != "item_changed"
            or set(change.get("changed") or ()) != {"markers"}):
        return False
    item = change.get("after") or {}
    row = (item.get("track_type"), item.get("track_index"),
           item.get("track_name"))
    values = change["changed"]["markers"]
    before = {_note_content(m) for m in values.get("before") or ()}
    after = {_note_content(m) for m in values.get("after") or ()}
    on_row = {_note_content(marker)
              for other in staged.get("items", ())
              if (other.get("track_type"), other.get("track_index"),
                  other.get("track_name")) == row
              for marker in other.get("markers") or ()}
    return (after - before) <= on_row and not ((before - after) & on_row)


def _replaced_stills(change: dict, staged: dict) -> list[dict]:
    """Staged stills that re-render the still `change` names.

    A rebuild re-renders a still overlay (the post header) under a new
    file and media pool item, and a still reads a zero-length source
    range, so neither identity nor source passage finds it. The one
    still on the same row over the same record span is the same overlay;
    `editor_edit_carry._replaced_still` lands the editor's edit there.
    """
    item = change.get("after") or {}
    start, end = item.get("source_in_frame"), item.get("source_out_frame")
    if change.get("kind") != "item_changed" or not (
            isinstance(start, int) and start == end):
        return []
    row = (item.get("track_type"), item.get("track_index"),
           item.get("track_name"))
    span = (item.get("record_in"), item.get("record_out"))
    return [other for other in staged.get("items", ())
            if (other.get("track_type"), other.get("track_index"),
                other.get("track_name")) == row
            and isinstance(other.get("source_in_frame"), int)
            and other.get("source_in_frame") == other.get("source_out_frame")
            and (other.get("record_in"), other.get("record_out")) == span]


def _change_within_replaced_span(change: dict, replaced) -> bool:
    """Whether this pending delta sits on an item the touch spec owns.

    A `swap_pixels` / `remove_overlay` states its item's replacement
    wholesale - new pixels at a stated span, verified by re-read - and a
    `repair_hold_window` states its item's comp-input rewrite the same
    way, so a baseline-vs-live delta on exactly that item is attributed
    to the touch, never carried as the editor's and never an
    unattributed loss: the old item is deleted or rewritten by
    declaration and its replacement is the spec, not a silent drop.
    Anything added, any marker, any setting, and any item outside the
    stated spans still refuses below.
    `replaced` is `(track_type, track_index, record_in, duration)` spans
    from the qualified spec; unreadable values fail closed (no match).
    """
    if not replaced:
        return False
    if change.get("kind") not in ("item_changed", "item_removed"):
        return False
    before = change.get("before") or {}
    try:
        rec_in = int(before.get("record_in"))
        rec_out = int(before.get("record_out"))
        track_index = int(before.get("track_index"))
    except (TypeError, ValueError):
        return False
    for span in replaced:
        try:
            span_type, span_index, start, duration = span
            if (str(before.get("track_type")).lower()
                    == str(span_type).lower()
                    and track_index == int(span_index)
                    and rec_in >= int(start)
                    and rec_out <= int(start) + int(duration)):
                return True
        except (TypeError, ValueError):
            continue
    return False


def _change_is_carried(change: dict, staged: dict) -> bool:
    kind = change["kind"]
    if kind.startswith("item_"):
        candidates = [item for item in staged.get("items", ())
                     if stable_item_key(item) == tuple(change["identity"])]
        if not candidates and kind == "item_changed":
            after = change.get("after") or {}
            identity = after.get("graphic_identity")
            changed = change.get("changed") or {}
            row_name = str(after.get("track_name") or "")
            if (identity and set(changed) == {"enabled"}
                    and after.get("track_type") == "video"
                    and "semantic" in row_name.casefold()):
                candidates = [
                    item for item in staged.get("items", ())
                    if item.get("track_type") == "video"
                    and item.get("track_name") == row_name
                    and item.get("graphic_identity") == identity
                ]
        fields = PRESERVATION_FIELDS
        if not candidates:
            file_candidates = _same_file_candidates(change, staged)
            if len(file_candidates) == 1:
                candidates = file_candidates
                fields = _FILE_MATCH_FIELDS
        if kind == "item_removed":
            return not candidates
        if not candidates and _note_landed_on_row(change, staged):
            return True
        if not candidates:
            candidates = (_replanned_caption_cards(change, staged)
                          or _replaced_stills(change, staged))
            fields = _CAPTION_SPAN_FIELDS
        if not candidates:
            return False
        if kind == "item_added":
            wanted = change["after"]
            if any(all(_same_preservation_value(
                               field, item.get(field), wanted.get(field))
                           for field in fields)
                       for item in candidates):
                return True
            extended = _extend_span_candidates(change, staged)
            return len(extended) == 1 and all(
                _same_preservation_value(
                    field, extended[0].get(field), wanted.get(field))
                for field in _EXTEND_SPAN_FIELDS)
        wanted = change["changed"]
        if fields is _CAPTION_SPAN_FIELDS or fields is _FILE_MATCH_FIELDS:
            # The render's name and source frames are not the editor's; a
            # file-matched item's pool identity is not either.
            wanted = {field: values for field, values in wanted.items()
                      if field in fields}
        return any(all(_same_preservation_value(
                           field, item.get(field), values["after"])
                       for field, values in wanted.items())
                   for item in candidates)
    if kind.startswith("marker_"):
        if kind == "marker_removed":
            wanted = change.get("before") or {}
            return not any(
                _marker_content_key(marker) == _marker_content_key(wanted)
                for marker in staged.get("markers", ()))
        wanted = change.get("after") or {}
        return any(_marker_content_key(marker) ==
                   _marker_content_key(wanted)
                   for marker in staged.get("markers", ()))
    timeline = staged.get("timeline") or {}
    if kind == "timeline_identity":
        return _canonical(timeline.get(change["field"])) == \
            _canonical(change.get("after"))
    if change["field"] in ("start_frame", "end_frame"):
        return _canonical(timeline.get(change["field"])) == \
            _canonical(change.get("after"))
    return _canonical((timeline.get("settings") or {}).get(
        change["field"])) == _canonical(change.get("after"))


def _change_summary(change: dict) -> str:
    def frame(value):
        return f"{int(value):,}" if isinstance(value, int) else value

    kind = change["kind"]
    if kind.startswith("item_"):
        item = change.get("before") or change.get("after") or {}
        row = f"{item.get('track_type')}:{item.get('track_name')}"
        source = (f"source {frame(item.get('source_in_frame'))}.."
                  f"{frame(item.get('source_out_frame'))}")
        record = (f"record {frame(item.get('record_in'))}.."
                  f"{frame(item.get('record_out'))}")
        if kind == "item_removed":
            return f"{row} {item.get('name')!r}: removed {source} at {record}"
        if kind == "item_added":
            return f"{row} {item.get('name')!r}: added {source} at {record}"
        edits = ", ".join(
            f"{field} {values['before']!r}->{values['after']!r}"
            for field, values in change["changed"].items())
        return f"{row} {item.get('name')!r} {source} at {record}: {edits}"
    if kind.startswith("marker_"):
        marker = change.get("before") or change.get("after") or {}
        return (f"{kind.replace('_', ' ')} at {marker.get('frame')} "
                f"{marker.get('name')!r}: {marker.get('note')!r}")
    return (f"timeline {change['field']}: {change.get('before')!r}->"
            f"{change.get('after')!r}")


def _snapshot_digest(snapshot: dict) -> str:
    return sha256(_canonical(snapshot).encode("utf-8")).hexdigest()


def accepts_editor_changes(final: str, raw) -> bool:
    """Match an explicit timeline or reel-number acceptance declaration."""
    import re

    values = [raw] if isinstance(raw, (str, int)) else list(raw or ())
    number = re.match(r"Reel\s+(\d+)", str(final), flags=re.IGNORECASE)
    accepted = {str(value).strip() for value in values}
    return (str(final) in accepted
            or (number is not None and number.group(1) in accepted))


def accepted_editor_drop_rows(editor_report: dict) -> set[str]:
    """Rows whose manual items an explicit acceptance will supersede."""
    if not editor_report.get("accepted"):
        return set()
    rows = set()
    for change in editor_report.get("uncarried", ()):
        if change.get("kind") not in {"item_added", "item_changed"}:
            continue
        item = change.get("after") or {}
        track_type, track_name = item.get("track_type"), item.get("track_name")
        if track_type and track_name:
            rows.add(row_key(str(track_type), str(track_name)))
    return rows


def journaled_touch_snapshot(project_folder: str, final: str,
                             live: dict) -> dict | None:
    """Ren's own last write of `final`, when its newest act is a touch.

    A reel Ren touched before snapshots were recorded
    (`ren_timeline_snapshots`) still has a record of what Ren left on
    it: the touch's undo journal `after` read
    (`library/tools/undo_journal.py`), taken by the same reader as a
    snapshot. Only the NEWEST live act counts - an older touch under a
    later rebuild describes a timeline that no longer exists - and
    only a touch: a build promotion records no full read. The journal
    reads items, not the timeline's settings or markers, so those come
    from `live` and first contact cannot see an editor change to them
    (`marker_carry` owns markers either way); nor can it see a Pan/Tilt
    change, because a journal records no unit epoch (below). None when
    there is no such record.
    """
    from library.tools import undo_journal
    from library.tools.versions import reel_versions

    act = reel_versions.latest_act(project_folder, final)
    if (not act or act.get("kind") != reel_versions.KIND_TOUCH
            or not act.get("journal")):
        return None
    entry = undo_journal.read_entry(project_folder, act["journal"])
    if (entry.get("final") != final or entry.get("status") != "applied"
            or not (entry.get("after") or {}).get("tracks")):
        return None
    items = snapshot_items(entry["after"]["tracks"])
    # Pan and Tilt are stored in the PROJECT resolution's unit, and
    # Resolve rescales every stored value when that changes while the
    # picture stays put (`drift_check.unit_epoch_mismatch`). A journal
    # records no unit epoch, so its Pan/Tilt cannot be compared with
    # live: they are taken from the live item that plays the same
    # passage. Every other field is still compared. Measured on Reel 09,
    # 2026-10-02: 69 Pan/Tilt reads exactly x4 its touch journal, and
    # nothing else moved but a pass-through Fusion comp.
    live_by_key = {}
    for item in live.get("items", ()):
        live_by_key.setdefault(stable_item_key(item), []).append(item)
    for item in items:
        paired = live_by_key.get(stable_item_key(item))
        if not paired:
            continue
        live_transform = paired.pop(0).get("transform") or {}
        transform = dict(item.get("transform") or {})
        for key in UNIT_EPOCH_KEYS:
            if key in live_transform:
                transform[key] = live_transform[key]
        item["transform"] = transform
    return {"metadata": dict(live.get("metadata") or {}),
            "timeline": dict(live.get("timeline") or {}),
            "items": items,
            "markers": list(live.get("markers") or ()),
            "journal": act["journal"]}


def _moments_at_or_after(moment: str, floor: str) -> bool:
    """Whether an ISO moment is at or after another one."""
    from datetime import datetime

    if not moment or not floor:
        return True
    try:
        left = datetime.fromisoformat(moment)
        right = datetime.fromisoformat(floor)
    except ValueError:
        return str(moment) >= str(floor)
    if left.tzinfo is None:
        left = left.replace(tzinfo=datetime.now().astimezone().tzinfo)
    if right.tzinfo is None:
        right = right.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return left >= right


def ren_write_attribution(project_folder: str, final: str, live: dict, *,
                           baseline_recorded_at: str = "") -> dict:
    """Whether the live state is a state Ren itself wrote.

    A lane that edits a timeline in place records its write in the
    project's event journal, with the state it read back.  The replace
    guard's baseline is a DIFFERENT ledger - the project's timeline
    snapshot - so a lane's own write reads as a difference between the
    baseline and the live timeline, and the guard files that difference
    as an UNATTRIBUTED editor change.  On a scratch timeline two lanes
    share, the difference then refuses every later edit, and each retry
    files it again.

    This is the attribution: when the live state is exactly the state
    Ren's own write recorded, the difference is Ren's, and it is
    attributed to that transaction.  A live state that is NOT a state
    Ren wrote - a change nobody recorded - stays unattributed and still
    refuses: a real human change is never auto-accepted.
    """
    from library.tools import event_log

    newest = None
    try:
        events = event_log.read_events(project_folder)
    except Exception:  # noqa: BLE001 - an unreadable journal attributes nothing
        events = []
    for event in events:
        fields = event.get("fields") or {}
        if str(fields.get("timeline") or "") != str(final):
            continue
        state = fields.get("actual_state") or fields.get("observed_state")
        if not isinstance(state, dict):
            continue
        at = str(event.get("at") or "")
        if not _moments_at_or_after(at, str(baseline_recorded_at or "")):
            continue
        if newest is None or at >= newest[0]:
            newest = (at, fields, state)
    if newest is None:
        return {"attributed": False,
                "reason": "no Ren write to this timeline is recorded after "
                          "the baseline"}
    at, fields, state = newest
    try:
        items = snapshot_items(state.get("tracks") or ())
    except Exception as unreadable:  # noqa: BLE001 - unreadable is not proof
        return {"attributed": False,
                "reason": f"the state Ren recorded could not be read "
                          f"({unreadable})"}
    if not items:
        return {"attributed": False,
                "reason": "the state Ren recorded holds no items"}
    live_items = list(live.get("items") or ())
    if snapshot_diff({"items": items}, {"items": live_items}):
        return {"attributed": False,
                "reason": "the live timeline is not the state Ren recorded"}
    return {"attributed": True,
            "reason": "the live timeline is exactly the state Ren's own "
                      "write recorded",
            "transaction_id": str(fields.get("transaction_id") or ""),
            "stage": str(fields.get("stage") or ""),
            "at": at}


def detect_editor_changes(project_folder: str, final: str, live: dict,
                          staged_initial: dict) -> dict:
    """Record the live timeline's unattributed deltas; return what is pending.

    The baseline is Ren's last known snapshot of `final`. On first
    contact (none recorded) it is Ren's last journaled touch where one
    exists (`journaled_touch_snapshot`), and only otherwise the incoming
    staging - which makes every difference the rebuild itself brings
    read as an editor change, so it is the last resort.

    A difference that is Ren's OWN write is attributed, not filed: a
    lane that edits a timeline in place records the state it read back,
    and the baseline ledger did not learn of it, so the difference read
    as an editor change and refused the next edit. On a scratch
    timeline two lanes share, that is what turns one lane's work into a
    pileup. A difference that is NOT a state Ren wrote stays
    unattributed and still refuses - a human change is never
    auto-accepted.

    Idempotent: a record's id is the digest of its two sides, so a
    second call over the same reads adds nothing. Returns
    `{"first_contact", "baseline", "detected", "pending"}`.
    """
    from library.tools import plan_provenance

    review_dir = os.path.join(project_folder, "pipeline_output", "review")
    provenance = plan_provenance.read_provenance(review_dir) or {}
    baseline_entry = (provenance.get("ren_timeline_snapshots") or {}).get(final)
    if baseline_entry and not baseline_entry.get("snapshot"):
        baseline_entry = None
    first_contact = baseline_entry is None
    journaled = (journaled_touch_snapshot(project_folder, final, live)
                 if first_contact else None)
    if not first_contact:
        before_snapshot = baseline_entry["snapshot"]
        baseline = "ren_last_known_snapshot"
    elif journaled is not None:
        before_snapshot = journaled
        baseline = "first_contact_journaled_touch"
    else:
        before_snapshot = staged_initial
        baseline = "first_contact_staging"
    changes = snapshot_diff(before_snapshot, live)
    if first_contact:
        # The staging container necessarily has its own name and timeline
        # id; first contact compares its contents with the editor's live
        # state, so container identity is not part of that comparison.
        changes = [change for change in changes
                   if change["kind"] != "timeline_identity"]
    if first_contact:
        plan_provenance.record_timeline_snapshot(
            review_dir, final, live, action="first_contact_baseline",
            action_journal=(journaled or {}).get("journal"))

    attribution = {"attributed": False, "reason": ""}
    if changes and not first_contact:
        attribution = ren_write_attribution(
            project_folder, final, live,
            baseline_recorded_at=str(baseline_entry.get("recorded_at") or ""))
    detected = []
    if changes:
        before_digest = _snapshot_digest(before_snapshot)
        after_digest = _snapshot_digest(live)
        record_id = sha256(
            f"{final}\0{before_digest}\0{after_digest}".encode()
        ).hexdigest()[:32]
        detected = [{
            "id": record_id,
            "recorded_at": datetime.now(UTC).isoformat(
                timespec="microseconds"),
            "timeline": final,
            "baseline": baseline,
            "ren_action_journal": (
                baseline_entry.get("action_journal") if not first_contact
                else (journaled or {}).get("journal")),
            "before_digest": before_digest,
            "after_digest": after_digest,
            "before_snapshot": before_snapshot,
            "after_snapshot": live,
            "changes": changes,
            "status": "pending",
        }]
        if attribution.get("attributed"):
            # Ren's own write is this difference. It is attributed to
            # the transaction that made it, the baseline learns the
            # state, and the difference stops being an open question.
            detected[0]["status"] = "attributed"
            detected[0]["attribution"] = dict(attribution)
            plan_provenance.record_timeline_snapshot(
                review_dir, final, live, action="ren_attributed_write",
                action_journal=str(attribution.get("transaction_id") or ""))
            for record in plan_provenance.pending_editor_changes(
                    review_dir, final):
                if record.get("after_digest") == after_digest:
                    plan_provenance.resolve_editor_change(
                        review_dir, final, str(record.get("id") or ""),
                        status="attributed")
        else:
            plan_provenance.record_editor_changes(review_dir, final, detected)

    pending = plan_provenance.pending_editor_changes(review_dir, final)
    # Pending records predate the empty-LUT and `mattes` normalizations
    # above. Remove those legacy no-ops in memory so they do not keep
    # refusing a replacement after this fix ships. Other fields and
    # structural changes are left exactly as recorded.
    normalized_pending = [
        _normalize_pending_editor_record(record) for record in pending
    ]

    return {
        "first_contact": first_contact,
        "baseline": baseline,
        "detected": detected,
        "pending": normalized_pending,
    }


def _moved_by_carried_ripples(carry_report) -> set:
    """Items the editor's carried, verified ripples moved, by stable key.

    `editor_edit_carry.derive_edits` records on each rippled edit every
    item whose shift that ripple alone explains (`ripple_moved`). The
    editor did not edit those items; his ripple moved them, and once the
    ripple is replayed on staging and verified, so is the move - even on
    an item the rebuild re-rendered (Reel 13's freeze, end card and
    captions after record 1,497), which no identity match can find.
    """
    if not isinstance(carry_report, dict):
        return set()
    return {tuple(key) for edit in carry_report.get("edits") or ()
            if edit.get("ripple")
            for key in edit.get("ripple_moved") or ()}


def _moved_by_ripple(change: dict, rippled: set) -> bool:
    return (change.get("kind") == "item_changed"
            and set(change.get("changed") or ()) <= {"record_in",
                                                     "record_out"}
            and bool(change.get("changed"))
            and tuple(change.get("identity") or ()) in rippled)


def protect_editor_changes(project_folder: str, final: str, live: dict,
                           staged_initial: dict, staged_after: dict,
                           *, accept=False, detection=None,
                           carried_edits=None, replaced_spans=None) -> dict:
    """Record deltas and refuse edits the staged timeline does not carry.

    `detection` is an earlier `detect_editor_changes` over the same
    reads, taken before the carried edits were applied to staging
    (`library/tools/editor_edit_carry.py`); `carried_edits` is that
    carry's report, kept on this one so the promotion records both.
    `replaced_spans` is the qualified touch spec's own removal spans -
    `(track_type, track_index, record_in, duration)` - whose items the
    spec replaces wholesale; a pending delta confined to one is
    attributed to the touch (`touch_replaced`), not refused as an
    unattributed loss and not misfiled as carried.
    """
    if detection is None:
        detection = detect_editor_changes(
            project_folder, final, live, staged_initial)
    native_losses = _native_state_losses(live, staged_after)
    first_contact = detection["first_contact"]
    detected = detection["detected"]
    pending = detection["pending"]
    carried, uncarried, touch_replaced = [], [], []
    replaced = list(replaced_spans or ())
    rippled = _moved_by_carried_ripples(carried_edits)
    for record in pending:
        unattributed = []
        record_replaced = []
        for change in record.get("changes", ()):
            if _change_is_carried(change, staged_after):
                continue
            if _moved_by_ripple(change, rippled):
                continue
            if _change_within_replaced_span(change, replaced):
                record_replaced.append(change)
                continue
            unattributed.append(change)
        if unattributed:
            uncarried.extend((record, change) for change in unattributed)
        elif record_replaced:
            touch_replaced.append(record["id"])
        else:
            carried.append(record)
    if uncarried and not accept:
        reasons = {}
        carry_report = carried_edits if isinstance(carried_edits, dict) else {}
        for problem in carry_report.get("uncarried") or ():
            change = problem.get("change") or {}
            if not str(change.get("kind") or "").startswith("item_"):
                continue
            item = change.get("before") or change.get("after") or {}
            key = (problem.get("record_id"), change.get("kind"),
                   stable_item_key(item))
            reasons.setdefault(key, []).append(problem["why"])
        details = []
        for record, change in uncarried:
            key = None
            if str(change.get("kind") or "").startswith("item_"):
                item = change.get("before") or change.get("after") or {}
                key = (record.get("id"), change.get("kind"),
                       stable_item_key(item))
            summary = _change_summary(change)
            if key is not None and reasons.get(key):
                summary += " - " + "; ".join(reasons[key])
            details.append(f"  {summary}")
        raise EditorChangeRefused(
            f"REFUSING to replace {final!r}: unattributed editor changes "
            f"are not carried into the staged timeline. Nothing was "
            f"renamed; the live timeline is still in the project.\n" +
            "\n".join(details) +
            f"\nTo accept this loss deliberately, pass "
            f"--accept-editor-changes {final!r}.")
    # A recorded edit has a more useful refusal above. Keep the native
    # state guard for differences that were not captured by that ledger,
    # and for accepted edits: the explicit edit override cannot authorize
    # dropping an observed grade, Fusion comp, or composite setting.
    if native_losses:
        raise ReplaceGuardRefused(
            f"REFUSING to replace {final!r}: observed native Resolve state "
            "is absent or different on the matching staged passage. "
            "This state has no qualified carry operation, so nothing was "
            "renamed. Resolve the grade, Fusion, or composite difference "
            "and verify it before replacing the timeline.\n  " +
            "\n  ".join(native_losses))
    return {"first_contact": first_contact, "detected": detected,
            "preservation": {
                "contract": dict(PRESERVATION_CONTRACT),
                # Partial and unsupported properties are present in the
                # matrix by design. Never turn a successful partial read
                # into a claim that every Resolve-native property survived.
                "full_preservation": all(
                    value == "supported"
                    for value in PRESERVATION_CONTRACT.values()),
                "observed_native_losses": native_losses,
            },
            "carried_edits": carried_edits,
            "carried": [record["id"] for record in carried],
            "touch_replaced": touch_replaced,
            "superseded": (sorted({record["id"] for record, _ in uncarried})
                           if accept else []),
            "uncarried": [change for _record, change in uncarried],
            "accepted": bool(accept and uncarried)}


def row_key(media_type: str, track_name: str) -> str:
    """The canonical identity of a row: `"video:Semantic"`."""
    return f"{media_type}:{track_name}"


def snapshot_timeline(timeline, timeline_name: str,
                       side: str = "retiring") -> dict:
    """Every row of a live timeline: each item's enabled state and span.

    A slice of the one enumeration: the items are read once, in
    `reel_read.read_tracks`, and this is the row projection of it. It is
    deliberately NOT the full `read_reel` - the guard's diff reads rows
    only, and a guard that refuses because markers or settings would not
    answer is a guard refusing over what it never looks at. The second
    function of this name (`timeline_ingest.snapshot_timeline`) is the
    ground-truth producer for external inputs, not a rival: one takes a
    slice, the other feeds the pipeline.

    Raises `ReplaceGuardUnreadable` on ANY read failure - a half-read
    timeline must refuse, never pass on the rows that happened to read.
    `side` names which half of the comparison this is (`"retiring"` or
    `"staged"`), so the refusal says what could not be seen.
    """
    from library.tools import reel_read

    try:
        tracks = reel_read.read_tracks(timeline)
        unreadable_enabled = [
            (track, clip) for track in tracks
            for clip in track.get("clips", ())
            if not isinstance(clip["enabled"], bool)]
        if unreadable_enabled:
            track, clip = unreadable_enabled[0]
            raise ReplaceGuardUnreadable(
                f"the enabled state of {clip['name']!r} on "
                f"{track['type']}:{track['name'] or track['index']} "
                f"could not be read")
        rows = reel_read.rows_of({"tracks": tracks})
        _attach_subtitle_tokens(rows, tracks)
        return rows
    except ReplaceGuardUnreadable as unreadable:
        raise ReplaceGuardUnreadable(
            f"the {side} timeline {timeline_name!r} could not be read "
            f"({unreadable}); the replace guard refuses rather than "
            f"promoting over what it cannot see.") from unreadable
    except reel_read.ReelReadError as unreadable:
        raise ReplaceGuardUnreadable(
            f"the {side} timeline {timeline_name!r} could not be read "
            f"({unreadable}); the replace guard refuses rather than "
            f"promoting over what it cannot see.") from unreadable
    except Exception as unreadable:
        raise ReplaceGuardUnreadable(
            f"the {side} timeline {timeline_name!r} could not be read "
            f"({unreadable}); the replace guard refuses rather than "
            f"promoting over what it cannot see.") from unreadable


def _span(entry: dict) -> str:
    return f"@{entry['start']}..{entry['end']} ({entry['duration']}f)"


def _match_key(entry: dict) -> tuple:
    return (entry["name"], entry["start"], entry["end"])


_CAPTION_WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)


def _caption_tokens(text: str) -> list[str]:
    return [token.casefold() for token in _CAPTION_WORD.findall(text)]


def _canonical_caption_tokens(tokens: list[str]) -> list[str]:
    """Treat adjacent spoken century/two-digit fragments as one year."""
    normalized = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if (token in {"19", "20"} and index + 1 < len(tokens)
                and re.fullmatch(r"\d{2}", tokens[index + 1])):
            normalized.append(token + tokens[index + 1])
            index += 2
            continue
        normalized.append(token)
        index += 1
    return normalized


def _canonical_caption_windows(words: list[dict]) -> list[dict]:
    """Join adjacent timed century and two-digit year fragments."""
    normalized = []
    index = 0
    while index < len(words):
        word = words[index]
        if (word["word"] in {"19", "20"} and index + 1 < len(words)
                and re.fullmatch(r"\d{2}", words[index + 1]["word"])):
            next_word = words[index + 1]
            normalized.append({
                "word": word["word"] + next_word["word"],
                "start": min(word["start"], next_word["start"]),
                "end": max(word["end"], next_word["end"]),
            })
            index += 2
            continue
        normalized.append(word)
        index += 1
    return normalized


def _subtitle_snapshot_data(source_file: str, detail: dict
                            ) -> tuple[list[str] | None,
                                       list[dict] | None]:
    """Read visible copy and timed word windows from one subtitle sidecar.

    `caption_word_windows` are in timeline frames. The sidecar's word
    windows are relative to the rendered overlay, while Resolve reports
    the source in-point and record in-point of the placed overlay.
    """
    if not source_file:
        return None, None
    path = Path(source_file)
    props_path = path.with_name(f"{path.stem}_props.json")
    try:
        props = json.loads(props_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    subtitles = props.get("subtitles") if isinstance(props, dict) else None
    if not isinstance(subtitles, list) or not subtitles:
        return None, None
    visible_tokens = []
    timed_words = []
    timed_evidence_valid = True
    for subtitle in subtitles:
        if not isinstance(subtitle, dict) or not isinstance(
                subtitle.get("text"), str):
            return None, None
        visible_tokens.extend(_caption_tokens(subtitle["text"]))
        word_rows = subtitle.get("words")
        if not isinstance(word_rows, list) or not word_rows:
            timed_evidence_valid = False
            continue
        for word in word_rows:
            if not isinstance(word, dict) or not isinstance(
                    word.get("word"), str):
                timed_evidence_valid = False
                continue
            word_tokens = _caption_tokens(word["word"])
            start_frame, end_frame = (word.get("startFrame"),
                                      word.get("endFrame"))
            if (len(word_tokens) != 1
                    or isinstance(start_frame, bool)
                    or not isinstance(start_frame, int)
                    or isinstance(end_frame, bool)
                    or not isinstance(end_frame, int)
                    or end_frame <= start_frame):
                timed_evidence_valid = False
                continue
            timed_words.append((word_tokens[0], start_frame, end_frame))
    if not visible_tokens:
        return None, None
    tokens = _canonical_caption_tokens(visible_tokens)
    if not timed_evidence_valid:
        return tokens, None

    timed_tokens = _canonical_caption_tokens(
        [word for word, _start, _end in timed_words])
    if timed_tokens != tokens:
        return tokens, None

    record_in = detail.get("record_in")
    source_in = detail.get("source_in_frame")
    if (isinstance(record_in, bool) or not isinstance(record_in, int)
            or isinstance(source_in, bool) or not isinstance(source_in, int)):
        return tokens, None

    timeline_words = [
        {"word": word,
         "start": record_in + start_frame - source_in,
         "end": record_in + end_frame - source_in}
        for word, start_frame, end_frame in timed_words
    ]
    return tokens, _canonical_caption_windows(timeline_words)


def _attach_subtitle_tokens(rows: dict, tracks: list[dict]) -> None:
    """Attach visible copy and timed words to the Subtitles row only."""
    for track in tracks:
        if (track["type"] != "video"
                or str(track["name"] or "").casefold() != "subtitles"):
            continue
        key = row_key(track["type"], track["name"])
        row = rows[key]
        if len(track["clips"]) != len(row["items"]):
            return
        row["caption_tokens"] = []
        row["caption_word_windows"] = []
        for detail, item in zip(track["clips"], row["items"]):
            tokens, windows = (
                (None, None) if item["enabled"] is False else
                _subtitle_snapshot_data(
                    str(detail.get("source_file") or ""), detail))
            row["caption_tokens"].append(tokens)
            row["caption_word_windows"].append(windows)


def _is_subtitle_join(old: dict, new: dict) -> bool:
    """Prove a subtitle card merge kept every visible word in order.

    Subtitle renders get content-derived filenames, so the ordinary
    name-presence proxy cannot recognize a merged or reflowed card. With
    equal or greater row duration, the old visible token sequence must be
    a subsequence of the new one. A shorter row needs an exact canonical
    token sequence and per-word timeline windows within one frame. Missing
    sidecar or timing evidence keeps the replace guard fail-closed.
    """
    old_items, new_items = old["items"], new["items"]
    if not old_items or not new_items:
        return False
    shrinking = (new["frames"] or 0) < (old["frames"] or 0)
    old_rows = old.get("caption_tokens")
    new_rows = new.get("caption_tokens")
    if (not isinstance(old_rows, list) or len(old_rows) != len(old_items)
            or not isinstance(new_rows, list) or len(new_rows) != len(new_items)):
        return False
    old_tokens, new_tokens = [], []
    for item, tokens in zip(old_items, old_rows):
        if item.get("enabled") is False:
            continue
        if not isinstance(tokens, list) or not tokens:
            return False
        old_tokens.extend(tokens)
    for item, tokens in zip(new_items, new_rows):
        if item.get("enabled") is False:
            continue
        if not isinstance(tokens, list) or not tokens:
            return False
        new_tokens.extend(tokens)
    old_tokens = _canonical_caption_tokens(old_tokens)
    new_tokens = _canonical_caption_tokens(new_tokens)
    if not old_tokens or not new_tokens:
        return False
    if shrinking:
        old_windows = old.get("caption_word_windows")
        new_windows = new.get("caption_word_windows")
        if (not isinstance(old_windows, list)
                or len(old_windows) != len(old_items)
                or not isinstance(new_windows, list)
                or len(new_windows) != len(new_items)):
            return False
        if any(item.get("enabled") is not False
               and (not isinstance(windows, list) or not windows)
               for items, rows in ((old_items, old_windows),
                                   (new_items, new_windows))
               for item, windows in zip(items, rows)):
            return False
        flattened_old = [window for item, windows in
                         zip(old_items, old_windows)
                         if item.get("enabled") is not False
                         for window in (windows or [])]
        flattened_new = [window for item, windows in
                         zip(new_items, new_windows)
                         if item.get("enabled") is not False
                         for window in (windows or [])]
        flattened_old = _canonical_caption_windows(flattened_old)
        flattened_new = _canonical_caption_windows(flattened_new)
        if not _same_caption_word_windows(flattened_old, flattened_new):
            return False
        return old_tokens == new_tokens
    cursor = iter(new_tokens)
    return all(any(candidate == token for candidate in cursor)
               for token in old_tokens)


def _same_caption_word_windows(old: list, new: list) -> bool:
    """Require identical timed words, allowing one frame of rounding."""
    if not old or len(old) != len(new):
        return False
    for before, after in zip(old, new):
        if not isinstance(before, dict) or not isinstance(after, dict):
            return False
        if before.get("word") != after.get("word"):
            return False
        for edge in ("start", "end"):
            old_frame, new_frame = before.get(edge), after.get(edge)
            if (isinstance(old_frame, bool) or not isinstance(old_frame, int)
                    or isinstance(new_frame, bool)
                    or not isinstance(new_frame, int)
                    or abs(old_frame - new_frame) > 1):
                return False
    return True


def diff_rows(retired: dict, incoming: dict) -> list:
    """One verdict per retired row, in retired row order.

    Rows only the incoming timeline has are new features, not losses,
    so they appear nowhere here. Frame totals ride along for the
    report; only item counts and row absence refuse.
    """
    verdicts = []
    for key, old in retired.items():
        new = incoming.get(key)
        if new is None:
            verdicts.append({
                "key": key,
                "media_type": old["media_type"],
                "index": old["index"],
                "name": old["name"],
                "retired_count": old["count"],
                "incoming_count": None,
                "retired_frames": old["frames"],
                "incoming_frames": None,
                "missing": list(old["items"]),
                "enabled_changes": [],
                "lost_row": True,
            })
            continue
        new_by_key = {}
        for entry in new["items"]:
            new_by_key.setdefault(_match_key(entry), []).append(entry)
        enabled_changes = []
        old_occurrences = {}
        for old_item in old["items"]:
            item_key = _match_key(old_item)
            occurrence = old_occurrences.get(item_key, 0)
            old_occurrences[item_key] = occurrence + 1
            matches = new_by_key.get(item_key, ())
            retired_enabled = old_item.get("enabled")
            incoming_enabled = (
                matches[occurrence].get("enabled")
                if occurrence < len(matches) else None)
            if (occurrence < len(matches)
                    and isinstance(retired_enabled, bool)
                    and isinstance(incoming_enabled, bool)
                    and retired_enabled is not incoming_enabled):
                enabled_changes.append({
                    "name": old_item["name"],
                    "start": old_item["start"],
                    "end": old_item["end"],
                    "retired_enabled": retired_enabled,
                    "incoming_enabled": incoming_enabled,
                })
        verdicts.append({
            "key": key,
            "media_type": old["media_type"],
            "index": old["index"],
            "name": old["name"],
            "retired_count": old["count"],
            "incoming_count": new["count"],
            "retired_frames": old["frames"],
            "incoming_frames": new["frames"],
            "missing": [entry for entry in old["items"]
                        if _match_key(entry) not in new_by_key],
            "enabled_changes": enabled_changes,
            "lost_row": False,
        })
    return verdicts


def include_disabled_carries(report: dict, carries: list[dict]) -> dict:
    """Report state differences matched by stable identity across retimes.

    The ordinary row diff can pair an item by its name and record span.
    A rerendered Semantic graphic has a new filename and may move with an
    opening shift, so its carry result supplies the content-and-intent match
    the span diff cannot see.
    """
    rows = {row["key"]: row for row in report["rows"]}
    for carry in carries:
        row = rows.get(carry["row"])
        if row is None:
            continue
        change = next((entry for entry in row["enabled_changes"]
                       if entry["name"] == carry["source_item"]
                       and entry["start"] == carry["source_frame"]), None)
        if change is None:
            change = {
                "name": carry["source_item"],
                "start": carry["source_frame"],
                "end": carry["source_end"],
                "retired_enabled": carry["source_enabled"],
                "incoming_enabled": carry["staged_enabled_before"],
            }
            row["enabled_changes"].append(change)
        change.update({
            "incoming_enabled": carry["staged_enabled_before"],
            "staged_item": carry["staged_item"],
            "staged_start": carry["staged_frame"],
            "staged_enabled_after": carry["staged_enabled_after"],
            "match_basis": carry["match_basis"],
        })
        if "element_type_change" in carry:
            change["element_type_change"] = carry["element_type_change"]
    return report


def _declared(key: str, name: str, allowed: set) -> bool:
    """Did the caller declare this row's reduction, canonically or bare."""
    return key in allowed or name in allowed


def _is_join(old: dict, new: dict) -> bool:
    """Whether a row's item-count drop reads as a merge, not a deletion.

    `old` and `new` are one row's snapshot halves (`snapshot_timeline`
    rows: `items` of name/start/end/duration, plus `count` and
    `frames`). A withdrawn take cut merges two adjacent placements
    into one continuous one: fewer items, at least as many frames
    (the restored seconds are back in), every retired name still on
    the row. Subtitle card reflow instead uses visible text and timed
    word windows, which permit a shorter row only when all spoken copy
    and word positions match. The generic join's three parts must hold -
    the cutaway loss this guard first
    caught proves frames alone are not sufficient (3 items to 2 over
    equal frames, with `LC4932 cover` gone), and names alone are not
    either (a shrunken same-named row keeps every name while losing
    seconds). Subtitle rows use their rendered sidecar word sequences
    instead of filenames, which are content-derived and change on a merge.
    """
    if new["count"] >= old["count"]:
        return False
    if (old["media_type"] == new["media_type"] == "video"
            and str(old["name"] or "").casefold() == "subtitles"
            and str(new["name"] or "").casefold() == "subtitles"):
        return _is_subtitle_join(old, new)
    if (new["frames"] or 0) < (old["frames"] or 0):
        return False
    incoming_names = {entry["name"] for entry in new["items"]}
    return all(entry["name"] in incoming_names
               for entry in old["items"])


def parse_specs(raw, finals) -> dict:
    """Raw caller specs to the per-final mapping the check reads.

    `raw` is a list of `"ROW"` (every reel this invocation promotes) or
    `"FINAL::ROW"` (that reel only) strings, or an already per-final
    mapping `{final: [rows]}`. Anything else is a caller bug and raises
    `ValueError` rather than reading as an empty declaration.
    """
    finals = list(finals or ())
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return {final: {str(row) for row in (rows or ())}
                for final, rows in raw.items()}
    if isinstance(raw, str):
        raw = [raw]
    try:
        specs = list(raw)
    except TypeError:
        raise ValueError(
            f"allow_drops must be a list of 'ROW' / 'FINAL::ROW' specs "
            f"or a {{final: [rows]}} mapping, got {raw!r}.")
    global_rows, per_final = [], {}
    for spec in specs:
        if not isinstance(spec, str) or not spec.strip():
            raise ValueError(
                f"allow_drops specs must be non-empty strings, got "
                f"{spec!r}. Declare the reduced row, e.g. "
                f"'video:Semantic'.")
        spec = spec.strip()
        if "::" in spec:
            final, _, row = spec.partition("::")
            final, row = final.strip(), row.strip()
            if not final or not row:
                raise ValueError(
                    f"allow_drops spec {spec!r} names no reel or no row. "
                    f"Write 'FINAL::ROW', e.g. "
                    f"'Reel 09 - moment (final)::video:Semantic'.")
            per_final.setdefault(final, set()).add(row)
        else:
            global_rows.append(spec)
    mapping = {final: set() for final in finals}
    for final in finals:
        mapping[final].update(global_rows)
        mapping[final].update(per_final.get(final, ()))
    for final, rows in per_final.items():
        mapping.setdefault(final, set()).update(rows)
    return mapping


def check_replacement(final: str, staging: str, retired: dict,
                       incoming: dict, allowed=None,
                       safe_disabled_drops=None, retired_end=None,
                       incoming_end=None, accept_end_shortfall=False) -> dict:
    """Refuse by name when the incoming timeline holds less, or report.

    Returns the per-reel report: every retired row with its counts on
    both sides, what is missing where, and which declarations covered
    which reduction. Raises `ReplaceGuardRefused` naming the row, the
    counts and what is missing for the first undeclared loss - with the
    exact declaration that would proceed deliberately.

    `retired_end` / `incoming_end` are the two timelines' end frames.
    When both read and the incoming tail ends before the retired one, a
    shortfall no row loss names - measured 2026-10-06 (Reel 13: the plan
    built the V1 tail to 1811 while the approved tail plays to 1815, and
    the item counts match, so the row diff passed silently) - refuses
    unless a row declaration puts the run in deliberate-restructuring
    mode or the shortfall is explicitly accepted. A LONGER incoming
    tail passes: extensions are additions, judged as items. Either end
    unreadable skips the comparison, never verdicts on it.
    """
    allowed = set(allowed or ())
    safe_drop_entries = defaultdict(list)
    for entry in safe_disabled_drops or ():
        key = (entry["row"], entry["source_item"], entry["source_frame"],
               entry["source_end"])
        safe_drop_entries[key].append(entry)
    safe_drop_counts = Counter({key: len(entries)
                                for key, entries in safe_drop_entries.items()})
    verdicts = diff_rows(retired, incoming)
    reduced = []
    for verdict in verdicts:
        old, new = retired[verdict["key"]], incoming.get(verdict["key"])
        safe = []
        remaining = []
        remaining_caption_tokens = []
        remaining_caption_word_windows = []
        old_caption_tokens = old.get("caption_tokens")
        old_caption_word_windows = old.get("caption_word_windows")
        for index, item in enumerate(old["items"]):
            identity = (verdict["key"], item["name"], item["start"],
                        item["end"])
            if safe_drop_counts[identity]:
                safe_drop_counts[identity] -= 1
                safe.append((item, safe_drop_entries[identity].pop(0)))
            else:
                remaining.append(item)
                if isinstance(old_caption_tokens, list):
                    remaining_caption_tokens.append(
                        old_caption_tokens[index]
                        if index < len(old_caption_tokens) else None)
                if isinstance(old_caption_word_windows, list):
                    remaining_caption_word_windows.append(
                        old_caption_word_windows[index]
                        if index < len(old_caption_word_windows) else None)
        effective_old = {
            **old,
            "items": remaining,
            "count": len(remaining),
            "frames": sum((item["duration"] or 0) for item in remaining),
        }
        if isinstance(old_caption_tokens, list):
            effective_old["caption_tokens"] = remaining_caption_tokens
        if isinstance(old_caption_word_windows, list):
            effective_old["caption_word_windows"] = (
                remaining_caption_word_windows)
        verdict["unchanged_disabled"] = [
            item for item, entry in safe if "replacement_item" not in entry]
        verdict["carried_disabled_replacements"] = [
            entry for _item, entry in safe if "replacement_item" in entry]
        verdict["effective_retired_count"] = effective_old["count"]
        verdict["joined"] = bool(
            new is not None and not verdict["lost_row"]
            and _is_join(effective_old, new))
        if new is None:
            unexplained_loss = (verdict["lost_row"]
                                and not (safe and not remaining))
        else:
            unexplained_loss = (
                new["count"] < effective_old["count"]
                and not _is_join(effective_old, new))
        if unexplained_loss:
            reduced.append(verdict)
    losses = [verdict for verdict in reduced
              if not _declared(verdict["key"], verdict["name"], allowed)]
    joined_keys = sorted({verdict["key"] for verdict in verdicts
                          if verdict["joined"]})
    covered = sorted({verdict["key"] for verdict in reduced
                      if _declared(verdict["key"], verdict["name"], allowed)})
    reduced_keys = ({verdict["key"] for verdict in reduced}
                    | {verdict["name"] for verdict in reduced})
    unused = sorted(key for key in allowed if key not in reduced_keys)
    report = {
        "final": final,
        "staging": staging,
        "rows": verdicts,
        "allowed": covered,
        "joined": joined_keys,
        "declared_unused": unused,
        "refused": bool(losses),
    }
    if not losses:
        if (retired_end is not None and incoming_end is not None
                and incoming_end < retired_end and not allowed
                and not accept_end_shortfall):
            raise ReplaceGuardRefused(
                f"REFUSING to promote {final!r}: the staged rebuild ends "
                f"at frame {incoming_end:,}f, "
                f"{retired_end - incoming_end:,}f before the approved "
                f"timeline's end at frame {retired_end:,}f (built as "
                f"{staging!r}). Nothing was renamed; the approved "
                f"timeline is still in the project.\n"
                f"To proceed deliberately, pass --accept-editor-changes "
                f"{final!r}.",
                allow_drop_rows=())
        return report
    lines = [(
        f"REFUSING to promote {final!r}: the staged rebuild carries less "
        f"than the approved timeline it would replace (built as "
        f"{staging!r}). Nothing was renamed; the approved timeline is "
        f"still in the project.")]
    for verdict in losses:
        label = (f"V{verdict['index']} {verdict['name']!r} "
                 f"({verdict['key']})")
        if verdict["lost_row"]:
            lines.append(
                f"  {label}: {verdict['retired_count']} item(s) -> row "
                f"absent (the whole feature class is gone; "
                f"{verdict['retired_frames']} frames).")
        else:
            lines.append(
                f"  {label}: {verdict['retired_count']} item(s) -> "
                f"{verdict['incoming_count']} "
                f"({verdict['retired_frames']}f -> "
                f"{verdict['incoming_frames']}f).")
        shown = verdict["missing"][:MISSING_SHOWN]
        for entry in shown:
            lines.append(
                f"    missing from the rebuild: "
                f"{entry['name']!r} {_span(entry)}.")
        hidden = len(verdict["missing"]) - len(shown)
        if hidden > 0:
            lines.append(f"    ... and {hidden} more (see replace_reports).")
    needed = sorted({verdict["key"] for verdict in losses})
    flags = " ".join(f"--allow-drop {key!r}" for key in needed)
    lines.append(
        f"To proceed deliberately, declare each reduced row: "
        f"`build-reels {flags}` "
        f"or `allow_drops={{{final!r}: {needed}}}`.")
    raise ReplaceGuardRefused("\n".join(lines), allow_drop_rows=needed)
