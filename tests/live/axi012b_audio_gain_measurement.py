"""AXI-012b: the native gain route, measured in the RENDERED audio.

PR 3053 measured the ROUTES (Resolve 21.1's ``AudioVolume`` writes a
level, ``SetClipEnabled(False)`` mutes audibly, ``SetFades`` fades
audibly, the OTIO build carries a level and a curve) and left the
EXECUTOR refusing, because nothing rendered the audio at request time:
a candidate receipt carried no independent audio-render evidence, so no
gain could be said to execute.

This is that missing measurement, and it measures the shipped code:
the script loads ``library/tools/audio_render_evidence.py`` BY PATH and
calls its functions on the live Resolve handles, so the numbers in the
report come from the implementation the executor uses - not from a copy
of it.  (The engine's package is not importable inside the broker's
session, which is why the module is loaded by path and why the module
itself imports nothing from the repository.)

What it measures, on a Ren-owned scratch timeline in the Qualification
project:

``gain``
    the native write (``SetProperty("AudioVolume", -6)``) read back,
    and the RENDER measured before and after: the change against the
    requested dB, and every other audio window's level, which is how
    the mix is shown not to have moved.
``linked``
    the linked A/V group, read before and after the write.
``pan``
    the pan, measured as sound: the render's per-channel balance with
    the pan hard left and hard right.  PR 3053 wrote the pan down as
    "the audible effect was not measured" - this measures it.

Running it
----------
It runs INSIDE Resolve, on the broker's own scripting connection, in a
borrowed qualification session - never as a direct scripting client,
and never on a captain's project::

    python3 tests/live/axi012b_submit.py

The submitter renders this file with the engine root substituted for
the module path, submits it as a ``resolve.qualification_session`` job
through the SERVING broker, and copies the report back into the
repository.  The run needs the EXCLUSIVE instance lease, so it queues
behind whatever else the machine is doing; that is the broker's job,
not this script's.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys

#: The qualification media: two synthetic clips with a tone each.  They
#: are the run's OWN copies of the shared fixture pool, never edited in
#: place.
MEDIA_A = os.path.expanduser(
    "~/.local/share/vep/qualification/622307e41719/qual_a.mp4")
MEDIA_B = os.path.expanduser(
    "~/.local/share/vep/qualification/622307e41719/qual_b.mp4")

#: The path to the shipped measurement module.  ``axi012b_submit.py``
#: substitutes the engine root it was given; a run that finds no module
#: refuses rather than measuring with a copy of it.
EVIDENCE_MODULE_PATH = "__AXI012B_MODULE_PATH__"

#: The path to the qualification DIMENSIONS module, loaded the same way:
#: the run measures the state its evidence was earned in, in the same
#: vocabulary the recorded predicates are written in.
DIMENSIONS_MODULE_PATH = "__AXI012B_DIMENSIONS_PATH__"

#: Where the renders and the report go.  The renders are scratch,
#: OUTSIDE the checkout: a measurement is not build output.  The report
#: is written to the fixed path ALWAYS, because ``resolve-axi run``
#: execs this file without a ``__file__``.
OUT_DIR = "/tmp/axi012b_renders"
REPORT_PATH = "/tmp/axi012b-audio-gain.json"

#: The report's place in the repository.  `resolve-axi run` execs this
#: file WITHOUT a `__file__`, so the path is substituted by the
#: submitter (which knows the engine root); the default is the scratch
#: copy when nothing was substituted.
REPO_REPORT_PATH = "__AXI012B_REPORT_PATH__"

TIMELINE_NAME = "AXI-012b audio probe"

FPS = 24
A_FRAMES = 4 * FPS          # qual_a: 4 s
B_FRAMES = 3 * FPS          # qual_b: 3 s
GAIN_DB = -6.0
PAN_LEFT = -1.0
PAN_RIGHT = 1.0

#: The pan values the sweep measures.  The property reads back whatever
#: was written, so the SCALE has to be measured: -1.0 may be a hard pan
#: or one percent of one, and only the render can say which.
PAN_SWEEP = (-100.0, -50.0, -1.0, 0.0, 1.0, 50.0, 100.0)

evidence: dict = {"renders": [], "notes": [], "errors": []}


def note(text: str) -> None:
    evidence["notes"].append(str(text))
    print("AXI-012b:", text, file=sys.stderr, flush=True)


def fail(stage: str, exc: BaseException) -> None:
    evidence["errors"].append("{}: {!r}".format(stage, exc))
    print("AXI-012b-ERROR", stage, repr(exc), file=sys.stderr, flush=True)


def load_module(name: str, path: str):
    """A shipped module, loaded by path."""
    if not path or path.startswith("__"):
        raise RuntimeError(
            f"{name} was not substituted; run this script through "
            f"tests/live/axi012b_submit.py")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_evidence_module():
    """The shipped measurement module, loaded by path."""
    return load_module("ren_audio_render_evidence", EVIDENCE_MODULE_PATH)


def qualification_state(timeline, module, dimensions, target_uid, version):
    """The state this run measured, in the qualification vocabulary.

    The recorded predicates are compared against the runtime state, so
    the state has to be MEASURED in the same vocabulary - not assumed.
    The rows are read off the live handles: every track, every item,
    what it is linked to, and what it already carries.
    """
    tracks = []
    rows = []
    for track_type in ("video", "audio"):
        try:
            count = int(timeline.GetTrackCount(track_type) or 0)
        except Exception:  # noqa: BLE001 - an unreadable row is empty
            count = 0
        for index in range(1, count + 1):
            try:
                items = timeline.GetItemListInTrack(track_type, index) or []
            except Exception:  # noqa: BLE001 - an unreadable row is empty
                items = []
            clips = []
            for item in items:
                uid = str(item.GetUniqueId() or "")
                try:
                    linked = module.linked_ids(item)
                except Exception:  # noqa: BLE001 - an unreadable group
                    linked = []
                try:
                    transform = dict(item.GetProperty() or {})
                except Exception:  # noqa: BLE001 - an unreadable property
                    transform = {}
                try:
                    speed = float((item.GetSpeed() or {}).get("Speed", 1.0))
                except Exception:  # noqa: BLE001 - an unreadable speed
                    speed = 1.0
                row = {
                    "unique_id": uid,
                    "media_type": "audio" if track_type == "audio" else "video",
                    "linked_unique_ids": linked,
                    "track_type": track_type,
                    "transform": transform,
                    "speed": speed * 100.0,
                }
                clips.append(row)
                rows.append(row)
            tracks.append({"type": track_type, "index": index,
                           "clips": clips})
    by_id = {str(row["unique_id"]): row for row in rows}
    chosen = [by_id[target_uid]] if target_uid in by_id else []
    settings = {}
    for key, cast in (("timelineFrameRate", float),
                      ("timelineResolutionWidth", int),
                      ("timelineResolutionHeight", int)):
        try:
            settings[key] = cast(timeline.GetSetting(key))
        except Exception:  # noqa: BLE001 - an unreadable setting is zero
            settings[key] = 0
    return {
        "resolve_version": re.sub(
            r"^\s*(?:DaVinci\s+Resolve(?:\s+Studio)?\b\s*)?", "", version,
            flags=re.IGNORECASE).strip(),
        "media_type": dimensions.media_type(chosen, []),
        "clip_composition": dimensions.clip_composition(chosen),
        "linked_audio_configuration": dimensions.linked_audio_configuration(
            chosen, by_id),
        "track_layout": dimensions.track_layout(tracks),
        "track_occupancy": dimensions.track_occupancy(tracks),
        "timeline_density": dimensions.timeline_density(tracks),
        "timeline_structure": dimensions.timeline_structure(tracks),
        "project_settings": dimensions.project_settings(
            int(settings["timelineResolutionWidth"]),
            int(settings["timelineResolutionHeight"])),
        "frame_rate": dimensions.frame_rate(settings["timelineFrameRate"]),
        "existing_effects": dimensions.existing_effects(chosen),
    }


def main() -> None:
    # `resolve-axi run` injects the running Resolve into this script's
    # globals.  It is read out of them by name rather than imported, so
    # the script runs on the BROKER's own connection and not on a second
    # scripting client - and so a test that imports this file does not
    # try to reach Resolve at all.
    handle = globals().get("resolve")
    if handle is None:
        raise RuntimeError(
            "this script runs inside `resolve-axi run`, which injects the "
            "running Resolve; it cannot run standalone")
    module = load_evidence_module()
    dimensions = load_module("ren_axi_qualification_dimensions",
                             DIMENSIONS_MODULE_PATH)
    os.makedirs(OUT_DIR, exist_ok=True)
    project = handle.GetCurrentProject()
    if not project:
        raise RuntimeError("no current project")
    pool = project.GetMediaPool()
    evidence["project"] = project.GetName()
    evidence["resolve_version"] = handle.GetVersionString()
    evidence["measurement_module"] = EVIDENCE_MODULE_PATH

    for index in range(1, project.GetTimelineCount() + 1):
        existing = project.GetTimelineByIndex(index)
        if existing and existing.GetName() == TIMELINE_NAME:
            pool.DeleteTimelines([existing])
    timeline = pool.CreateEmptyTimeline(TIMELINE_NAME)
    if not timeline:
        raise RuntimeError("CreateEmptyTimeline refused")
    project.SetCurrentTimeline(timeline)
    evidence["timeline"] = TIMELINE_NAME

    storage = handle.GetMediaStorage()
    imported = storage.AddItemListToMediaPool([MEDIA_A, MEDIA_B]) or []
    if len(imported) < 2:
        raise RuntimeError("media import refused: {!r}".format(imported))
    evidence["imported"] = [str(item.GetName()) for item in imported]

    placed_a = pool.AppendToTimeline([{
        "mediaPoolItem": imported[0],
        "startFrame": 0, "endFrame": A_FRAMES, "recordFrame": 0,
    }])
    placed_b = pool.AppendToTimeline([{
        "mediaPoolItem": imported[1],
        "startFrame": 0, "endFrame": B_FRAMES, "recordFrame": A_FRAMES,
    }])
    if not placed_a or not placed_b:
        raise RuntimeError("AppendToTimeline refused")
    evidence["placed"] = [
        {"start": int(item.GetStart()), "end": int(item.GetEnd()),
         "type": item.GetType(),
         "linked": module.linked_ids(item)}
        for item in list(placed_a) + list(placed_b)]

    fps = float(timeline.GetSetting("timelineFrameRate") or 0)
    timeline_start = int(timeline.GetStartFrame())
    evidence["fps"] = fps
    windows = {}
    for _index, item in module.audio_rows(timeline):
        uid = str(item.GetUniqueId() or "")
        windows[uid] = module.item_window(
            int(item.GetStart()), int(item.GetEnd()), fps, timeline_start)
    evidence["windows"] = windows
    audio_items = [(uid, item) for uid, item in
                   ((str(item.GetUniqueId() or ""), item)
                    for _index, item in module.audio_rows(timeline))]
    if not audio_items:
        raise RuntimeError("no audio item landed on the timeline")
    target_uid, target_item = audio_items[0]
    other_uid = next((uid for uid, _item in audio_items
                      if uid != target_uid), "")
    other_item = next((item for uid, item in audio_items
                       if uid == other_uid), None)
    evidence["target"] = {"uid": target_uid,
                           "window": windows.get(target_uid, {})}
    evidence["other"] = {"uid": other_uid,
                         "window": windows.get(other_uid, {})}

    def render(name: str) -> str:
        return module.render_timeline(
            project, timeline, os.path.join(OUT_DIR, name + ".mp4"))

    # ── the native gain write, measured ──
    gain = {}
    try:
        before_levels = module.audio_levels(timeline)
        linked_before = module.linked_ids(target_item)
        gain["previous_level"] = module.read_level(target_item)
        before_path = render("gain_before")
        write = module.write_level(target_item, GAIN_DB)
        gain["write"] = write
        after_path = render("gain_after")
        verdict = module.gain_evidence(before_path, after_path, windows,
                                       GAIN_DB, target=target_uid)
        gain["audio_evidence"] = verdict
        gain["linked_before"] = linked_before
        gain["linked_after"] = module.linked_ids(target_item)
        gain["linked_preserved"] = (gain["linked_before"]
                                    == gain["linked_after"])
        after_levels = module.audio_levels(timeline)
        moved = sorted(uid for uid, row in before_levels.items()
                       if uid != target_uid and after_levels.get(uid) != row)
        gain["levels_moved"] = moved
        gain["other_levels_unchanged"] = not moved
        gain["other_window_reading"] = verdict["readings"].get(other_uid)
        gain["verified"] = bool(
            verdict["target_matches_request"] and verdict["mix_preserved"]
            and gain["linked_preserved"] and not moved)
        gain["restored"] = module.restore_level(
            target_item, before_levels.get(target_uid, {}).get("level_db"),
            before_levels.get(target_uid, {}).get("enabled"))
        gain["restored_level"] = module.read_level(target_item)
    except Exception as exc:  # noqa: BLE001 - the failure IS the evidence
        fail("gain", exc)
    evidence["gain"] = gain

    # ── the pan, measured as sound ──
    pan = {}
    try:
        pan["spellings"] = {}
        for spelling, value in (("AudioPan", PAN_LEFT),
                                ("Pan", PAN_LEFT)):
            try:
                returned = target_item.SetProperty(spelling, value)
            except Exception as exc:  # noqa: BLE001 - a raised call is data
                returned = "raised: {!r}".format(exc)
            try:
                back = target_item.GetProperty(spelling)
            except Exception as exc:  # noqa: BLE001 - a raised read is data
                back = "raised: {!r}".format(exc)
            pan["spellings"][spelling] = {"set_return": returned,
                                          "read_back": back}
            target_item.SetProperty("AudioPan", 0.0)
        # The SCALE of the pan is measured, not assumed: the property
        # reads back whatever was written, and only the render says
        # whether the value moved the sound.  A sweep answers the
        # question the unit raises - is -1.0 a hard pan or one percent
        # of one?
        pan["sweep"] = {}
        for value in PAN_SWEEP:
            target_item.SetProperty("AudioPan", value)
            target_item.SetProperty("AudioPanEnabled", True)
            path = render("pan_" + str(value).replace("-", "m")
                          .replace(".", "p"))
            pan["sweep"][str(value)] = {
                "pan_read_back": target_item.GetProperty("AudioPan"),
                "balance": module.measure_pan(
                    path, windows[target_uid]["start"],
                    windows[target_uid]["duration"]),
                "level": module.measure_window(
                    path, windows[target_uid]["start"],
                    windows[target_uid]["duration"]),
            }
        target_item.SetProperty("AudioPan", 0.0)
        readings = pan["sweep"]
        # A SILENT difference signal is the strongest centre evidence
        # there is: the two channels carry the same thing.  It is read
        # as the floor, not as a missing measurement.
        floors = []
        for row in readings.values():
            difference = row["balance"]["difference_db"]
            floors.append(-120.0 if difference is None else difference)
        pan["difference_spread_db"] = (
            round(max(floors) - min(floors), 2) if floors else None)
        pan["audible"] = bool(
            floors and (max(floors) - min(floors)) > 3.0)
    except Exception as exc:  # noqa: BLE001 - the failure IS the evidence
        fail("pan", exc)
    evidence["pan"] = pan

    try:
        evidence["qualification_state"] = qualification_state(
            timeline, module, dimensions, target_uid,
            handle.GetVersionString())
    except Exception as exc:  # noqa: BLE001 - the state IS the evidence
        fail("qualification state", exc)

    try:
        evidence["timeline_left"] = TIMELINE_NAME in [
            project.GetTimelineByIndex(index).GetName()
            for index in range(1, project.GetTimelineCount() + 1)]
    except Exception as exc:  # noqa: BLE001 - the read is best effort
        fail("timeline list", exc)
    try:
        mine = [project.GetTimelineByIndex(index) for index in
                range(1, project.GetTimelineCount() + 1)]
        mine = [tl for tl in mine
                if tl and tl.GetName().startswith(TIMELINE_NAME)]
        if mine:
            pool.DeleteTimelines(mine)
        pool.DeleteClips(imported)
    except Exception as exc:  # noqa: BLE001 - the cleanup is best effort
        fail("cleanup", exc)

    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as handle:
        json.dump(evidence, handle, indent=1)
    try:
        os.makedirs(os.path.dirname(REPO_REPORT_PATH), exist_ok=True)
        with open(REPO_REPORT_PATH, "w", encoding="utf-8") as handle:
            json.dump(evidence, handle, indent=1)
    except OSError as exc:
        fail("repo report", exc)
    print(json.dumps({"report": REPORT_PATH,
                      "gain_verified": gain.get("verified"),
                      "pan_audible": pan.get("audible"),
                      "errors": evidence["errors"]}))


if globals().get("__name__", "__main__") == "__main__":
    # `resolve-axi run` execs this file in a sandbox with no `__name__`,
    # so the guard reads the sandbox's own globals: the script runs when
    # it is exec'd, and does NOT run when a test imports it.
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - the report is the evidence
        fail("main", exc)
        os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
        with open(REPORT_PATH, "w", encoding="utf-8") as handle:
            json.dump(evidence, handle, indent=1)
        print(json.dumps({"report": REPORT_PATH, "fatal": repr(exc)}))
