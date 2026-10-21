"""AXI-012: MEASURE what Resolve 21.1 writes on an audio item, in rendered audio.

The punchlist asks for complete audio-operation semantics, and a
declaration is only worth what a measurement is worth.  This is the
measurement: it creates its OWN scratch timeline in the qualification
project, places two synthetic clips (a 440 Hz tone and a 660 Hz tone,
linked A/V), writes four different audio states onto the audio item, and
RENDERS each one.  The numbers that matter are read off the renders with
ffmpeg - loudness and silence - never off an API return value.

What it measures, and why each one is a different route:

``unity``
    the baseline: both clips at their own level, nothing written.
``gain_minus6``
    the native route, if the item accepts it: ``SetProperty("AudioVolume",
    -6)`` plus ``AudioVolumeEnabled``.  A -6 dB request must MEASURE -6 dB
    in the render, and the other clip must not move.
``muted``
    ``SetClipEnabled(False)``: the muted clip's span must be SILENCE in
    the render, and the other clip must not move.
``faded``
    ``SetFades``: the level across the fade window must follow the
    fade, and the other clip must not move.
``otio_gain_minus6``
    the route Ren actually uses for a level: the OTIO build.  The
    exported OTIO carries Fairlight clip volume in plain JSON, in dB, so
    a -6 dB level is written into the clip's ``volume`` parameter and
    imported back.  The import REBUILDS the timeline, so this is also
    the measurement of what survives a rebuild: the linked A/V pair and
    the OTHER clip's level.
``otio_automation``
    the same route with a keyframed curve: the level must follow the
    curve, which is the only thing a build can carry that a single
    value cannot.

The report is written to ``tests/live/reports/axi012-audio-measurement.json``
and every row in it is a measurement, so a reader can check the numbers
instead of taking the summary's word for it.

Running it
----------
It runs INSIDE Resolve, on the broker's own scripting connection, in a
borrowed qualification session - never as a direct scripting client, and
never on a captain's project::

    python3 -m library.tools.resolved submit resolve.qualification_session \\
      '{"project": "Ren Qualification", "steps": [{"resolve_axi": ["run",
        "--file", "<this file>", "--unsafe", "--json"]}]}' --qualification

The run needs the EXCLUSIVE instance lease, so it queues behind whatever
else the machine is doing; that is the broker's job, not this script's.
"""

from __future__ import annotations

import array
import json
import math
import os
import subprocess
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))) if "__file__" in globals() else ""

#: The qualification media: two synthetic clips with a tone each.  They
#: are the run's OWN copies of the shared fixture pool, never edited in
#: place.
MEDIA_A = os.path.expanduser(
    "~/.local/share/vep/qualification/622307e41719/qual_a.mp4")
MEDIA_B = os.path.expanduser(
    "~/.local/share/vep/qualification/622307e41719/qual_b.mp4")

#: Where the renders and the report go.  The renders are scratch, OUTSIDE
#: the checkout: a measurement is not build output.  The report is
#: written to the fixed path ALWAYS, because `resolve-axi run` execs
#: this file without a `__file__`, and to the checkout's own report
#: directory when the file knows where it lives.
OUT_DIR = "/tmp/axi012_renders"
REPORT_PATH = "/tmp/axi012-audio-measurement.json"
REPO_REPORT_PATH = os.path.join(
    REPO_ROOT, "tests", "live", "reports", "axi012-audio-measurement.json"
) if REPO_ROOT else ""

TIMELINE_NAME = "AXI-012 audio probe"

FPS = 24
A_FRAMES = 4 * FPS          # qual_a: 4 s
B_FRAMES = 3 * FPS          # qual_b: 3 s
GAIN_DB = -6.0
FADE_FRAMES = 48            # 2 s
AUTOMATION = {0: 0.0, 48: -18.0, 96: 0.0}
SAMPLE_RATE = 48000

#: The measured windows.  Each one sits INSIDE its clip, clear of the
#: boundary frame where the two clips meet: a window that straddles the
#: cut mixes one clip's audio into the other's number, and a fade that
#: starts at the cut belongs to the clip that plays it.
WINDOW_A = (0.5, 3.0)        # inside qual_a, after the head, before the tail
WINDOW_A_HEAD = (0.25, 1.25)  # inside qual_a's fade-in
WINDOW_A_TAIL = (2.5, 3.5)    # inside qual_a, after the fade-in
WINDOW_B = (4.5, 6.5)        # inside qual_b

#: Resolve's own constant for ``Timeline.Export`` with OTIO.
EXPORT_OTIO = 15

VOLUME_EFFECT_NAME = "Fairlight Clip Volume and Fades"

evidence: dict = {"renders": [], "notes": [], "errors": []}


def note(text: str) -> None:
    evidence["notes"].append(str(text))
    print("AXI-012:", text, file=sys.stderr, flush=True)


def fail(stage: str, exc: BaseException) -> None:
    evidence["errors"].append("{}: {!r}".format(stage, exc))
    print("AXI-012-ERROR", stage, repr(exc), file=sys.stderr, flush=True)


def measure(path: str, start: float | None = None,
            duration: float | None = None) -> dict:
    """One window of the rendered audio, measured in SAMPLES.

    The window is cut from the decoded PCM, not from a filter's report:
    ffmpeg's `volumedetect` and `ebur128` measure the stream they are
    given, and a seek that lands on a frame boundary mixes the two
    clips' windows.  The numbers here are the window's own RMS, its own
    peak, and the fraction of its samples that are DIGITAL ZERO - which
    is how "the muted clip is silent" is told from "the muted clip is
    quiet".
    """
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", path,
           "-f", "s16le", "-acodec", "pcm_s16le", "-ac", "1", "-ar",
           "48000", "-"]
    proc = subprocess.run(cmd, capture_output=True, timeout=600,
                          check=False)
    samples = array.array("h")
    samples.frombytes(proc.stdout[:len(proc.stdout) - (len(proc.stdout) % 2)])
    total = len(samples)
    first = 0 if start is None else int(start * SAMPLE_RATE)
    last = total if duration is None else int((start or 0) * SAMPLE_RATE
                                              + duration * SAMPLE_RATE)
    window = samples[max(0, first):max(0, min(total, last))]
    out = {"window_seconds": round(len(window) / SAMPLE_RATE, 3),
           "rms_db": None, "peak_db": None, "digital_zero_fraction": None}
    if not window:
        return out
    total_energy = 0.0
    peak = 0
    zeros = 0
    for value in window:
        sample = value / 32768.0
        total_energy += sample * sample
        magnitude = abs(sample)
        if magnitude > peak:
            peak = magnitude
        if value == 0:
            zeros += 1
    rms = (total_energy / len(window)) ** 0.5
    out["rms_db"] = round(20.0 * math.log10(rms), 2) if rms > 0 else None
    out["peak_db"] = round(20.0 * math.log10(peak), 2) if peak > 0 else None
    out["digital_zero_fraction"] = round(zeros / len(window), 4)
    return out


def integrated_lufs(path: str) -> dict:
    """The whole render's integrated loudness, as ffmpeg measures it."""
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", path,
           "-af", "ebur128=peak=true", "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=600,
                          check=False)
    out = {"lufs": None, "peak": None}
    for line in proc.stderr.splitlines():
        line = line.strip()
        try:
            if line.startswith("I:"):
                out["lufs"] = float(line.split("I:")[1]
                                    .split("LUFS")[0].strip())
            elif line.startswith("Peak:"):
                out["peak"] = float(line.split("Peak:")[1]
                                    .split("dBFS")[0].strip())
        except ValueError:
            pass
    return out


def render(project, timeline, name: str) -> str:
    """Render the timeline's whole span to ``<OUT_DIR>/<name>.mp4``."""
    saved = project.GetCurrentRenderFormatAndCodec() or {}
    job_id = None
    try:
        project.SetCurrentRenderFormatAndCodec("mp4", "H.264")
        start = int(timeline.GetStartFrame())
        end = int(timeline.GetEndFrame())
        settings = {
            "TargetDir": OUT_DIR,
            "CustomName": name,
            "MarkIn": start,
            "MarkOut": max(start, end - 1),
            "SelectAllFrames": False,
            "FormatWidth": 1920,
            "FormatHeight": 1080,
            "ExportAudio": True,
            "AudioCodec": "aac",
            "AudioBitDepth": 16,
            "AudioSampleRate": 48000,
        }
        if not project.SetRenderSettings(settings):
            raise RuntimeError("SetRenderSettings refused")
        job_id = project.AddRenderJob()
        if not job_id:
            raise RuntimeError("AddRenderJob returned nothing")
        if not project.StartRendering([job_id], isInteractiveMode=False):
            raise RuntimeError("StartRendering refused")
        deadline = time.time() + 600
        while time.time() < deadline:
            if not project.IsRenderingInProgress():
                break
            time.sleep(1.0)
        else:
            project.StopRendering()
            raise RuntimeError("render timed out")
        status = project.GetRenderJobStatus(job_id) or {}
        if status.get("JobStatus") != "Complete":
            raise RuntimeError("render ended {!r}: {!r}".format(
                status.get("JobStatus"), status.get("Error")))
    finally:
        if job_id:
            try:
                project.DeleteRenderJob(job_id)
            except Exception:  # noqa: BLE001 - the job is already gone
                pass
        try:
            if saved.get("format") and saved.get("codec"):
                project.SetCurrentRenderFormatAndCodec(saved["format"],
                                                        saved["codec"])
        except Exception:  # noqa: BLE001 - the render settings are restored
            pass
    path = os.path.join(OUT_DIR, name + ".mp4")
    if not os.path.exists(path):
        raise RuntimeError("no render at {}".format(path))
    return path


def read_props(item) -> dict:
    """What the item reports about itself, under every spelling."""
    out = {}
    for name in ("GetProperty", "GetProperties"):
        call = getattr(item, name, None)
        if call is None:
            out[name] = {"present": False}
            continue
        try:
            out[name] = {"present": True, "value": dict(call() or {})}
        except Exception as exc:  # noqa: BLE001 - an unreadable read is data
            out[name] = {"present": True, "error": repr(exc)}
    return out


def volume_parameter(value_db: float, keyframes: dict | None = None) -> dict:
    """Resolve's own `volume` parameter shape, in dB, with keyframes."""
    return {
        "Parameter ID": "volume",
        "Parameter Value": float(value_db),
        "Default Parameter Value": 0.0,
        "Variant Type": "Double",
        "minValue": -100.0,
        "maxValue": 30.0,
        "Key Frames": {str(int(frame)): {"Value": float(value),
                                        "Variant Type": "Double"}
                       for frame, value in sorted((keyframes or {}).items())},
    }


def otio_clip(data: dict, path: str) -> dict | None:
    """The audio clip in an exported OTIO that plays ``path``."""
    for track in data.get("tracks", {}).get("children", []):
        if track.get("kind") != "Audio":
            continue
        for clip in track.get("children", []):
            if not str(clip.get("OTIO_SCHEMA", "")).startswith("Clip"):
                continue
            reference = (clip.get("media_references") or {}).get(
                clip.get("active_media_reference_key", "")) or {}
            url = reference.get("target_url", "") or ""
            if url.removeprefix("file://") == path:
                return clip
    return None


def write_volume(clip: dict, value_db: float,
                 keyframes: dict | None = None) -> bool:
    """Write the level into the clip's volume parameter, in place."""
    for effect in clip.get("effects", []) or []:
        metadata = (effect.get("metadata") or {}).get("Resolve_OTIO") or {}
        if metadata.get("Effect Name") != VOLUME_EFFECT_NAME:
            continue
        parameters = [p for p in (metadata.get("Parameters") or [])
                      if p.get("Parameter ID") != "volume"]
        parameters.append(volume_parameter(value_db, keyframes))
        metadata["Parameters"] = parameters
        return True
    return False


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
    os.makedirs(OUT_DIR, exist_ok=True)
    project = handle.GetCurrentProject()
    if not project:
        raise RuntimeError("no current project")
    pool = project.GetMediaPool()
    evidence["project"] = project.GetName()
    evidence["resolve_version"] = handle.GetVersionString()

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
         "linked": [str(getattr(x, "GetUniqueId", lambda: "")())
                    for x in (item.GetLinkedItems() or [])]}
        for item in list(placed_a) + list(placed_b)]

    # The audio items, read the way the rest of Ren reads them:
    # `GetTrackCount` / `GetItemListInTrack` / `GetTrackName`.  There is
    # no `Timeline.GetTrack`, and a clip's own LINKED partner is the
    # fallback: a placed video item's linked partner IS its audio item.
    audio_items = []
    audio_track_count = int(timeline.GetTrackCount("audio") or 0)
    for track_index in range(1, audio_track_count + 1):
        items = timeline.GetItemListInTrack("audio", track_index) or []
        if not items:
            continue
        audio_items.append((track_index,
                            timeline.GetTrackName("audio", track_index),
                            items[0]))
    evidence["audio_tracks"] = [
        {"index": index, "name": name,
         "items": len(timeline.GetItemListInTrack("audio", index) or [])}
        for index, name, _item in audio_items]
    if not audio_items:
        for partner in (placed_a[0].GetLinkedItems() or []):
            if str(partner.GetType()) == "audio":
                audio_items.append((0, "linked to V1", partner))
                break
    if not audio_items:
        raise RuntimeError(
            "no audio item landed on the timeline: the placement put "
            "video only, so there is nothing to write an audio level on")
    _row, track_name, audio_a = audio_items[0]
    b_item = next((item for _index, _name, item in audio_items
                   if int(item.GetStart()) >= A_FRAMES), None)
    evidence["audio_a"] = {"track": track_name,
                           "start": int(audio_a.GetStart()),
                           "end": int(audio_a.GetEnd()),
                           "type": audio_a.GetType()}
    evidence["props_before"] = read_props(audio_a)

    # The spellings: which of them Resolve 21.1 accepts, and which one
    # answers False.  This is the enumeration the declarations rest on.
    spellings = {}
    for spelling, value in (("Volume", GAIN_DB),
                             ("AudioVolume", GAIN_DB),
                             ("AudioVolumeEnabled", True),
                             ("AudioPan", 0.0),
                             ("Pan", 0.0)):
        try:
            returned = audio_a.SetProperty(spelling, value)
        except Exception as exc:  # noqa: BLE001 - a raised call is data
            returned = "raised: {!r}".format(exc)
        try:
            back = audio_a.GetProperty(spelling)
        except Exception as exc:  # noqa: BLE001 - a raised read is data
            back = "raised: {!r}".format(exc)
        spellings[spelling] = {"set_return": returned, "read_back": back}
    evidence["set_property_spellings"] = spellings

    for spelling, value in (("AudioVolume", 0.0),
                             ("AudioVolumeEnabled", True),
                             ("AudioPan", 0.0)):
        try:
            audio_a.SetProperty(spelling, value)
        except Exception as exc:  # noqa: BLE001 - the reset is best effort
            fail("reset " + spelling, exc)
    try:
        audio_a.SetClipEnabled(True)
    except Exception as exc:  # noqa: BLE001 - the reset is best effort
        fail("reset enabled", exc)
    evidence["props_unity"] = read_props(audio_a)

    def state(name: str) -> None:
        try:
            path = render(project, timeline, name)
        except Exception as exc:  # noqa: BLE001 - a failed render is a row
            fail("render " + name, exc)
            return
        evidence["renders"].append({
            "name": name, "path": path,
            "clip_a": measure(path, *WINDOW_A),
            "clip_a_head": measure(path, *WINDOW_A_HEAD),
            "clip_a_tail": measure(path, *WINDOW_A_TAIL),
            "clip_b": measure(path, *WINDOW_B),
            "whole": integrated_lufs(path),
        })

    state("unity")

    try:
        evidence["gain_write"] = {
            "set_return": audio_a.SetProperty("AudioVolume", GAIN_DB),
            "read_back": audio_a.GetProperty("AudioVolume"),
            "enabled_set": audio_a.SetProperty("AudioVolumeEnabled", True),
        }
    except Exception as exc:  # noqa: BLE001 - a failed write is a row
        fail("gain write", exc)
    state("gain_minus6")

    try:
        audio_a.SetProperty("AudioVolume", 0.0)
        evidence["mute_write"] = {
            "set_return": audio_a.SetClipEnabled(False),
            "read_back": audio_a.GetClipEnabled(),
        }
    except Exception as exc:  # noqa: BLE001 - a failed write is a row
        fail("mute write", exc)
    state("muted")

    try:
        audio_a.SetClipEnabled(True)
        evidence["fade_write"] = {
            "set_return": audio_a.SetFades({"FadeIn": FADE_FRAMES,
                                            "FadeOut": 0})}
        evidence["fade_read"] = dict(audio_a.GetFades() or {})
    except Exception as exc:  # noqa: BLE001 - a failed write is a row
        fail("fade write", exc)
    state("faded")

    # ── the OTIO route: what Ren writes a level with ──
    otio = {"exported": False, "imported": False, "errors": []}
    try:
        export_path = os.path.join(OUT_DIR, "probe.otio")
        if not timeline.Export(export_path, EXPORT_OTIO):
            raise RuntimeError("timeline.Export refused")
        with open(export_path, encoding="utf-8") as handle:
            data = json.load(handle)
        otio["exported"] = True

        target = otio_clip(data, MEDIA_A)
        if target is None:
            raise RuntimeError("the exported OTIO carries no clip for {}".format(MEDIA_A))
        if not write_volume(target, GAIN_DB):
            raise RuntimeError("no volume effect on the exported clip")
        mixed_path = os.path.join(OUT_DIR, "probe.mixed.otio")
        data["name"] = TIMELINE_NAME
        with open(mixed_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=1)
        if not timeline.SetName(TIMELINE_NAME + "__premix"):
            raise RuntimeError("could not rename the placement timeline")
        imported_timeline = pool.ImportTimelineFromFile(
            os.path.abspath(mixed_path),
            {"timelineName": TIMELINE_NAME, "importSourceClips": True})
        if not imported_timeline:
            raise RuntimeError("ImportTimelineFromFile returned None")
        otio["imported"] = True
        project.SetCurrentTimeline(imported_timeline)
        timeline = imported_timeline
        state("otio_gain_minus6")

        check_path = os.path.join(OUT_DIR, "probe.delivered.otio")
        if imported_timeline.Export(check_path, EXPORT_OTIO):
            with open(check_path, encoding="utf-8") as handle:
                check = json.load(handle)
            levels, links = [], []
            for track in check.get("tracks", {}).get("children", []):
                if track.get("kind") != "Audio":
                    continue
                for clip in track.get("children", []):
                    if not str(clip.get("OTIO_SCHEMA", "")).startswith("Clip"):
                        continue
                    links.append((clip.get("metadata", {})
                                  .get("Resolve_OTIO", {}) or {})
                                 .get("Link Group ID"))
                    reference = (clip.get("media_references") or {}).get(
                        clip.get("active_media_reference_key", "")) or {}
                    url = reference.get("target_url", "") or ""
                    path = url.removeprefix("file://")
                    for effect in clip.get("effects", []) or []:
                        metadata = ((effect.get("metadata") or {})
                                    .get("Resolve_OTIO") or {})
                        if metadata.get("Effect Name") != VOLUME_EFFECT_NAME:
                            continue
                        for parameter in metadata.get("Parameters") or []:
                            if parameter.get("Parameter ID") == "volume":
                                levels.append({
                                    "source": path,
                                    "level_db": parameter.get("Parameter Value"),
                                    "keyframes": parameter.get("Key Frames")
                                    or {},
                                })
            otio["levels_after_import"] = levels
            otio["link_group_ids_after"] = links

        with open(check_path, encoding="utf-8") as handle:
            data = json.load(handle)
        target = otio_clip(data, MEDIA_A)
        if target is None or not write_volume(target, 0.0, AUTOMATION):
            raise RuntimeError("could not write the automation curve")
        auto_path = os.path.join(OUT_DIR, "probe.auto.otio")
        data["name"] = TIMELINE_NAME
        with open(auto_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=1)
        if not timeline.SetName(TIMELINE_NAME + "__premix2"):
            raise RuntimeError("could not rename the mixed timeline")
        imported_again = pool.ImportTimelineFromFile(
            os.path.abspath(auto_path),
            {"timelineName": TIMELINE_NAME, "importSourceClips": True})
        if not imported_again:
            raise RuntimeError("ImportTimelineFromFile returned None (auto)")
        project.SetCurrentTimeline(imported_again)
        timeline = imported_again
        state("otio_automation")
        otio["automation_written"] = True
    except Exception as exc:  # noqa: BLE001 - the OTIO half is evidence too
        otio["errors"].append(repr(exc))
        fail("otio route", exc)
    evidence["otio"] = otio

    try:
        evidence["after"] = {
            "clip_b_enabled": b_item.GetClipEnabled() if b_item else None,
            "clip_b_props": read_props(b_item) if b_item else None,
            "audio_a_linked": [
                str(getattr(item, "GetUniqueId", lambda: "")())
                for item in (audio_a.GetLinkedItems() or [])],
            "audio_a_enabled": audio_a.GetClipEnabled(),
        }
    except Exception as exc:  # noqa: BLE001 - a failed read is a row
        fail("after read", exc)

    try:
        mine = [project.GetTimelineByIndex(i) for i in
                range(1, project.GetTimelineCount() + 1)]
        mine = [tl for tl in mine
                if tl and tl.GetName().startswith(TIMELINE_NAME)]
        if mine:
            pool.DeleteTimelines(mine)
    except Exception as exc:  # noqa: BLE001 - the cleanup is best effort
        fail("timeline delete", exc)
    try:
        pool.DeleteClips(imported)
    except Exception as exc:  # noqa: BLE001 - the cleanup is best effort
        fail("pool cleanup", exc)
    names = [project.GetTimelineByIndex(i).GetName()
             for i in range(1, project.GetTimelineCount() + 1)]
    evidence["timeline_left"] = TIMELINE_NAME in names

    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as handle:
        json.dump(evidence, handle, indent=1)
    if REPO_REPORT_PATH:
        os.makedirs(os.path.dirname(REPO_REPORT_PATH), exist_ok=True)
        with open(REPO_REPORT_PATH, "w", encoding="utf-8") as handle:
            json.dump(evidence, handle, indent=1)
    print(json.dumps({"report": REPORT_PATH,
                      "renders": len(evidence["renders"]),
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
