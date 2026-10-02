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
that must pass. Grain removed and a j-cut deleted likewise move no
item count, which is why a count trigger is not a nuisance for the
legitimate reductions the captain actually makes.

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

So a row with fewer incoming items is a JOIN, and passes undeclared,
exactly when the incoming row holds at least as many frames AND
every distinct retired name still occurs among the incoming items.
Both halves are load-bearing, and the round's own cutaway is the
counter-example that proves frames alone are not enough: that loss
went 3 items to 2 over EQUAL frames (hole closed), so a frames-only
rule would have waved a real deletion through. Names refuse it.

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
from collections import Counter, defaultdict
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from hashlib import sha256

#: How many missing items a refusal names inline per row. The report the
#: promote result carries names every one; the message stays readable.
MISSING_SHOWN = 5


class ReplaceGuardUnreadable(RuntimeError):
    """A timeline in the comparison could not be read, and this says which."""


class ReplaceGuardRefused(RuntimeError):
    """The incoming timeline carries less than the one it would replace."""


class EditorChangeRefused(ReplaceGuardRefused):
    """Unattributed live edits would be lost by this replacement."""


PRESERVATION_FIELDS = (
    "track_type", "track_index", "track_name", "name", "source_identity",
    "source_in_frame", "source_out_frame", "record_in", "record_out",
    "duration", "enabled", "transform", "composite", "fusion", "color",
    "clip_color", "flags", "markers",
)
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


def _plain_setting(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _plain_setting(item)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_setting(item) for item in value]
    return str(value)


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
    except Exception as unreadable:  # noqa: BLE001
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
                raise ValueError(
                    f"enabled state for {detail['name']!r} is unreadable")
            item = {key: detail.get(key) for key in
                    PRESERVATION_FIELDS if key in detail}
            item["source_identity"] = item_source_identity(detail)
            item["unique_id"] = str(detail.get("unique_id") or "")
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
            return {
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
    except Exception as unreadable:  # noqa: BLE001
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
#: 2026-10-02) and some one ulp off (`composed_edit.READBACK_TOLERANCE`).
VALUE_TOLERANCE = 1e-6


def _same_value(a, b) -> bool:
    """One value, read twice: numbers by magnitude, containers by member."""
    numbers = (int, float)
    if (isinstance(a, numbers) and isinstance(b, numbers)
            and not isinstance(a, bool) and not isinstance(b, bool)):
        return abs(float(a) - float(b)) <= VALUE_TOLERANCE
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(
            _same_value(a[key], b[key]) for key in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(
            _same_value(x, y) for x, y in zip(a, b))
    return _canonical(a) == _canonical(b)


def _stable_item_key(item: dict) -> tuple:
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
        old_items.setdefault(_stable_item_key(item), []).append(item)
    for item in after.get("items", ()):
        new_items.setdefault(_stable_item_key(item), []).append(item)
    for key in sorted(set(old_items) | set(new_items), key=_canonical):
        old_group, new_group = old_items.get(key, []), new_items.get(key, [])
        paired = min(len(old_group), len(new_group))
        for old, new in zip(old_group[:paired], new_group[:paired]):
            changed = {
                field: {"before": old.get(field), "after": new.get(field)}
                for field in PRESERVATION_FIELDS
                if not _same_value(old.get(field), new.get(field))
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


def _change_is_carried(change: dict, staged: dict) -> bool:
    kind = change["kind"]
    if kind.startswith("item_"):
        candidates = [item for item in staged.get("items", ())
                     if _stable_item_key(item) == tuple(change["identity"])]
        if kind == "item_removed":
            return not candidates
        if not candidates:
            return False
        if kind == "item_added":
            wanted = change["after"]
            return any(all(_same_value(item.get(field), wanted.get(field))
                           for field in PRESERVATION_FIELDS)
                       for item in candidates)
        wanted = change["changed"]
        return any(all(_same_value(item.get(field), values["after"])
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
        live_by_key.setdefault(_stable_item_key(item), []).append(item)
    for item in items:
        paired = live_by_key.get(_stable_item_key(item))
        if not paired:
            continue
        live_transform = paired.pop(0).get("transform") or {}
        transform = dict(item.get("transform") or {})
        for key in UNIT_EPOCH_KEYS:
            if key in live_transform:
                transform[key] = live_transform[key]
        item["transform"] = transform
    return {"timeline": dict(live.get("timeline") or {}),
            "items": items,
            "markers": list(live.get("markers") or ()),
            "journal": act["journal"]}


def detect_editor_changes(project_folder: str, final: str, live: dict,
                          staged_initial: dict) -> dict:
    """Record the live timeline's unattributed deltas; return what is pending.

    The baseline is Ren's last known snapshot of `final`. On first
    contact (none recorded) it is Ren's last journaled touch where one
    exists (`journaled_touch_snapshot`), and only otherwise the incoming
    staging - which makes every difference the rebuild itself brings
    read as an editor change, so it is the last resort.

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

    detected = []
    if changes:
        before_digest = _snapshot_digest(before_snapshot)
        after_digest = _snapshot_digest(live)
        record_id = sha256(
            f"{final}\0{before_digest}\0{after_digest}".encode("utf-8")
        ).hexdigest()[:32]
        detected = [{
            "id": record_id,
            "recorded_at": datetime.now(timezone.utc).isoformat(
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
        plan_provenance.record_editor_changes(review_dir, final, detected)

    return {"first_contact": first_contact, "baseline": baseline,
            "detected": detected,
            "pending": plan_provenance.pending_editor_changes(
                review_dir, final)}


def protect_editor_changes(project_folder: str, final: str, live: dict,
                           staged_initial: dict, staged_after: dict,
                           *, accept=False, detection=None,
                           carried_edits=None) -> dict:
    """Record deltas and refuse edits the staged timeline does not carry.

    `detection` is an earlier `detect_editor_changes` over the same
    reads, taken before the carried edits were applied to staging
    (`library/tools/editor_edit_carry.py`); `carried_edits` is that
    carry's report, kept on this one so the promotion records both.
    """
    if detection is None:
        detection = detect_editor_changes(
            project_folder, final, live, staged_initial)
    first_contact = detection["first_contact"]
    detected = detection["detected"]
    pending = detection["pending"]
    carried, uncarried = [], []
    for record in pending:
        remaining = [change for change in record.get("changes", ())
                     if not _change_is_carried(change, staged_after)]
        if remaining:
            uncarried.extend((record, change) for change in remaining)
        else:
            carried.append(record)
    if uncarried and not accept:
        details = [f"  {_change_summary(change)}"
                   for _record, change in uncarried]
        raise EditorChangeRefused(
            f"REFUSING to replace {final!r}: unattributed editor changes "
            f"are not carried into the staged timeline. Nothing was "
            f"renamed; the live timeline is still in the project.\n" +
            "\n".join(details) +
            f"\nTo accept this loss deliberately, pass "
            f"--accept-editor-changes {final!r}.")
    return {"first_contact": first_contact, "detected": detected,
            "carried_edits": carried_edits,
            "carried": [record["id"] for record in carried],
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
        return reel_read.rows_of({"tracks": tracks})
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
    the row. All three must hold - the cutaway loss this guard first
    caught proves frames alone are not sufficient (3 items to 2 over
    equal frames, with `LC4932 cover` gone), and names alone are not
    either (a shrunken same-named row keeps every name while losing
    seconds).
    """
    if new["count"] >= old["count"]:
        return False
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
                      safe_disabled_drops=None) -> dict:
    """Refuse by name when the incoming timeline holds less, or report.

    Returns the per-reel report: every retired row with its counts on
    both sides, what is missing where, and which declarations covered
    which reduction. Raises `ReplaceGuardRefused` naming the row, the
    counts and what is missing for the first undeclared loss - with the
    exact declaration that would proceed deliberately.
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
        for item in old["items"]:
            identity = (verdict["key"], item["name"], item["start"],
                        item["end"])
            if safe_drop_counts[identity]:
                safe_drop_counts[identity] -= 1
                safe.append((item, safe_drop_entries[identity].pop(0)))
            else:
                remaining.append(item)
        effective_old = {
            **old,
            "items": remaining,
            "count": len(remaining),
            "frames": sum((item["duration"] or 0) for item in remaining),
        }
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
    raise ReplaceGuardRefused("\n".join(lines))
