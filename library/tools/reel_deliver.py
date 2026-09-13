"""Render ONE chosen reel timeline to a video file. The explicit verb, and
the ONLY path that renders a reel.

Why this exists
---------------
The reels process builds timelines and stops there: `build_reels` places
pictures on Resolve timelines and `verify_reels` grades them, and after
eight reels and five weeks not one rendered reel file existed. Every
frame the captain reviewed, they watched inside Resolve themselves.
Their definition of done is a video the pipeline made, WITHOUT them
touching it. This module is the last step that makes one.

Why it is a verb and not a node
-------------------------------
The captain's standing ruling (2026-09-09, 2026-09-10): *"Never render
unasked ... a timeline build is cheap on their machine and is the thing
they judge; a render is the expensive thing they must ask for."* On
2026-09-09 they cancelled a render mid-flight for exactly this reason.

So this module is called from ONE place - `manage_project.py
deliver-reel` - and from nowhere else. It is not a DAG node, not an
operation, and no existing command reaches it. `tests/
test_reel_deliver_is_explicit.py` fails the moment any of those gains a
reference to it: the input that breaks it is any call path from
`build-reels`, `run`, the DAGs or the operation registry into this
module. The answer to "what would have to happen for a render to start
unasked" is "the captain runs the verb" - anything else is a defect.

What is declared and what is derived
------------------------------------
The delivery preset (container + codec) and the file naming are
DECLARATIONS, not values this module picks (captain, 2026-08-28: *"i
want no hardcoded values"*). They live in the project's own
`project.yaml` under `pipeline:` as `deliver_preset` (a mapping with
`format` and `codec`) and `deliver_naming` (a filename, optionally
carrying `{timeline}` and `{ext}`). Both empty means UNDECLARED, and
`resolve_deliver_settings` says so on its face rather than presenting a
fallback as a decision.

A run still needs SOMETHING to render with, so what is underived is
derived from what the project already declares, and said plainly:

* the frame comes from `delivery_format.resolve_delivery_format` - the
  declared delivery format `delivery_format.py` owns - and the render
  reads the width/height off the TIMELINE itself
  (`execution.resolve_render` refuses rather than inheriting the
  project's default);
* the container/codec fall back to `resolve_render`'s own mechanism
  defaults (`mp4`/`H264`) and are reported as `preset_declared: False`;
* the filename falls back to `{timeline}.mp4` and is reported as
  `naming_declared: False`.

The preset and naming NEED THE CAPTAIN'S WORD. The fallback renders a
watchable file; it is not a house standard.

Known unknowns, answered by reading (2026-09-13)
------------------------------------------------
* `resolve_render.render_timeline` assumes NO master. It takes any
  timeline name, selects it exactly, and reads the resolution off the
  timeline it was given. The reel-shaped caller below passes the reel's
  exact timeline name and nothing else.
* Render presets need NO hand-saved preset in the project. Format/codec
  go through `SetCurrentRenderFormatAndCodec` and everything else
  through `SetRenderSettings` - the whole preset is API-driven, audio
  (`ExportAudio`) stated explicitly.
* Overlays are BAKED IN, following the master: the reel build places
  caption comps onto the reel timeline itself (`reel_build`), so the
  render captures them exactly as the master render captures its own.
* "Not black" for a reel that opens on black: reels carry no
  `spine_contract` black beats on this path, so there is nothing honest
  to excuse a head hold against. `verify_deliverable` reports EVERY
  black segment with its timestamps, names a head segment (`start ==
  0`) as `opens_on_black` - an OBSERVATION, never a pass - and the
  verdict fails only when no picture exists at all (black covers the
  whole file). A naive any-black-fails check would fail the correct
  TV-switch-on reel; a whole-file check cannot.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional


class DeliverRefused(Exception):
    """The reel will not be rendered, and this says why."""


# The vocabulary a declared preset may carry. A key outside this is a
# declaration nothing reads, and those are refused (AGENTS.md 10.1).
DELIVER_PRESET_KEYS = ("format", "codec")

# The mechanism's own fallback when the project declares no preset.
# Reported as underived every time it is used - see the module docstring.
FALLBACK_FORMAT = "mp4"
FALLBACK_CODEC = "H264"

# The mechanism's own fallback filename. `{timeline}` is the exact reel
# timeline name, `{ext}` follows the container.
FALLBACK_NAMING = "{timeline}.{ext}"

CONTAINER_EXTENSIONS = {
    "mp4": "mp4",
    "mov": "mov",
    "mkv": "mkv",
}


def _project_pipeline_block(project_folder: str) -> dict:
    """The `pipeline:` mapping of the project's project.yaml.

    One parse, in `brand_registry.py` - this module, `delivery_format`
    and `framing_intent` all read the same block.
    """
    from library.tools.brand_registry import project_pipeline_block
    return project_pipeline_block(project_folder) or {}


def read_deliver_declaration(project_folder: str) -> dict:
    """The project's own deliver declaration, or its absence.

    Returns `{"preset": {...}, "naming": str}`. Both empty means the
    captain has not declared them - that is a fact the caller reports,
    never a gap it fills quietly.
    """
    block = _project_pipeline_block(project_folder)
    preset = block.get("deliver_preset") or {}
    naming = block.get("deliver_naming") or ""
    if preset is not None and not isinstance(preset, dict):
        raise DeliverRefused(
            f"pipeline.deliver_preset must be a mapping of "
            f"{list(DELIVER_PRESET_KEYS)}, got "
            f"{type(preset).__name__}: {preset!r}.")
    unknown = sorted(set(preset) - set(DELIVER_PRESET_KEYS))
    if unknown:
        raise DeliverRefused(
            f"pipeline.deliver_preset declares {unknown}, which nothing "
            f"reads. It takes {list(DELIVER_PRESET_KEYS)}.")
    for key, value in preset.items():
        if not isinstance(value, str) or not value.strip():
            raise DeliverRefused(
                f"pipeline.deliver_preset[{key!r}] must be a non-empty "
                f"string, got {value!r}.")
    if not isinstance(naming, str):
        raise DeliverRefused(
            f"pipeline.deliver_naming must be a string, got "
            f"{type(naming).__name__}: {naming!r}.")
    return {"preset": dict(preset), "naming": naming.strip()}


def resolve_deliver_settings(project_folder: str) -> dict:
    """Everything a render needs, each half labelled derived or declared.

    The frame is always DECLARED (delivery_format.py owns it). The
    container/codec and the filename are declared only when the project
    declares them; otherwise the mechanism's fallback, said plainly.
    """
    from library.tools.delivery_format import (
        delivery_format_name,
        resolve_delivery_format,
    )

    declaration = read_deliver_declaration(project_folder)
    preset = declaration["preset"]
    naming = declaration["naming"]

    width, height = resolve_delivery_format(project_folder)
    fmt = (preset.get("format") or "").strip() or FALLBACK_FORMAT
    codec = (preset.get("codec") or "").strip() or FALLBACK_CODEC
    ext = CONTAINER_EXTENSIONS.get(fmt.lower(), fmt.lower())

    return {
        "format": fmt,
        "codec": codec,
        "width": width,
        "height": height,
        "format_name": delivery_format_name(project_folder),
        "naming": naming or FALLBACK_NAMING,
        "extension": ext,
        "preset_declared": bool(preset),
        "naming_declared": bool(naming),
        # What a reader who was not here needs: which halves still want
        # the captain's word.
        "needs_captain_word": [
            half for half, declared in (
                ("deliver_preset", bool(preset)),
                ("deliver_naming", bool(naming)),
            ) if not declared
        ],
    }


def _proposal_moments(project_folder: str) -> list:
    """The plan's moments, or a refusal naming what is missing."""
    from library.tools.reel_proposal import (
        ProposalError,
        proposal_path,
        read_proposal,
    )

    path = proposal_path(project_folder)
    if not Path(path).is_file():
        raise DeliverRefused(
            f"no reel plan at {path} - nothing has been proposed for "
            f"this project, so there is no reel to deliver.")
    try:
        return read_proposal(path)
    except ProposalError as exc:
        raise DeliverRefused(str(exc)) from None


def timeline_name_for_reel(project_folder: str, reel: Optional[int]) -> str:
    """The EXACT Resolve timeline name for one reel number.

    Refuses when the reel is unnamed (no number given), unknown to the
    plan, or not approved by the captain. Prefers the name the build
    actually placed (the build record's `timelines_built`, which carries
    the `name_suffix` when one was used) over the plan's own name, and
    refuses on ambiguity rather than guessing between the two.
    """
    if reel is None:
        raise DeliverRefused(
            "no reel named - `deliver-reel` takes exactly one reel "
            "number, e.g. `manage_project.py deliver-reel <project> 3`. "
            "Rendering without naming one is the unasked render this "
            "verb exists to prevent.")
    try:
        number = int(reel)
    except (TypeError, ValueError):
        raise DeliverRefused(
            f"reel must be a reel number, got {reel!r}.") from None

    moments = _proposal_moments(project_folder)
    moment = next((m for m in moments if int(m.number) == number), None)
    if moment is None:
        known = sorted(int(m.number) for m in moments)
        raise DeliverRefused(
            f"the plan names no reel {number}. Known: "
            f"{known or '(the plan names none)'}.")
    from library.tools.reel_proposal import Approval
    if moment.approval is not Approval.APPROVED:
        raise DeliverRefused(
            f"reel {number} ({moment.slug!r}) is {moment.approval.value}, "
            f"not approved - the captain has not approved this reel, so "
            f"there is nothing to deliver.")

    plan_name = moment.timeline_name
    candidates = _built_names_for(project_folder, number, plan_name)
    if len(candidates) > 1:
        raise DeliverRefused(
            f"reel {number} has {len(candidates)} built names "
            f"({', '.join(sorted(candidates))}) - a suffixed rebuild "
            f"beside the plan's own name. Name the timeline explicitly "
            f"with --timeline-name rather than letting this guess.")
    if candidates:
        return candidates[0]
    return plan_name


def _built_names_for(project_folder: str, number: int,
                     plan_name: str) -> list:
    """What the build actually placed for this reel, if anything.

    Read off the build record (`step_outputs.build_reels.reel_build.
    timelines_built`), never inferred: the record carries the final
    names including any `--name-suffix`. A reel never built has no
    entry, which reads as "not built" rather than as the plan's name -
    the plan's name is what the caller falls back to only when the
    record says nothing at all.
    """
    from library.tools.project_layout import ProjectLayout

    path = ProjectLayout(project_folder).pipeline_data_path
    if not Path(path).is_file():
        return []
    try:
        state = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    record = ((state.get("step_outputs") or {}).get("build_reels")
              or {})
    build = record.get("reel_build") or {}
    head = f"Reel {int(number):02d} -"
    return [name for name in (build.get("timelines_built") or [])
            if isinstance(name, str) and name.startswith(head)]


def render_file_name(timeline_name: str, naming: str, ext: str) -> str:
    """The output filename for one timeline under one naming."""
    name = (naming or FALLBACK_NAMING)
    rendered = name.replace("{timeline}", timeline_name).replace(
        "{ext}", ext)
    if "{timeline}" not in name and "{ext}" not in name and name == naming:
        # A literal filename with no placeholder: taken as-is, so a
        # declared `reel_03_final.mp4` is honoured rather than mangled.
        rendered = naming
    return rendered


def verify_deliverable(video_path: str, expected_seconds: float,
                       expected_width: int, expected_height: int) -> dict:
    """`render_qa`'s verdict on one delivered file: duration, resolution,
    audio present, and not black.

    This wires the existing checker in rather than writing a second
    one: `verify_duration`, `verify_resolution`, `verify_audio_streams`
    and `detect_black_frames` are all `render_qa`'s own. Reels carry no
    `spine_contract` black beats on this path, so no declared beats are
    passed - and a head segment (`start == 0`) is reported as
    `opens_on_black`, an observation rather than a pass, so the
    TV-switch-on reel does not fail a correct file (see the module
    docstring).
    """
    from library.tools import render_qa

    if not os.path.isfile(video_path):
        raise DeliverRefused(
            f"expected a rendered file at {video_path} and there is "
            f"none - the render reported success and produced no video.")

    duration = render_qa.verify_duration(video_path, expected_seconds)
    resolution = render_qa.verify_resolution(
        video_path, expected_width=expected_width,
        expected_height=expected_height)
    audio = render_qa.verify_audio_streams(video_path)
    black = render_qa.detect_black_frames(video_path)

    segments = list(black.value or []) if black.value else []
    black_seconds = sum(float(s.get("duration", 0.0)) for s in segments
                        if isinstance(s, dict))
    # The whole-file question, answered from the segments: some picture
    # exists when the black does not cover the file's own measured
    # duration.
    measured = float((duration.value or 0.0))
    content_present = bool(segments is not None) and (
        black_seconds < measured if measured > 0 else len(segments) == 0)
    opens_on_black = any(
        isinstance(s, dict) and float(s.get("start", -1)) == 0.0
        for s in segments)

    verdict = {
        "output_path": video_path,
        "size_bytes": os.path.getsize(video_path),
        "duration": _result_record(duration),
        "resolution": _result_record(resolution),
        "audio": _result_record(audio),
        "black": _result_record(black),
        "black_segments": segments,
        "opens_on_black": opens_on_black,
        "content_present": content_present,
        "passed": bool(duration.passed and resolution.passed
                       and audio.passed and content_present),
    }
    return verdict


def _result_record(result) -> dict:
    """A `RenderQAResult` as plain data for the deliver report."""
    return {
        "metric": result.metric,
        "passed": bool(result.passed),
        "value": result.value,
        "threshold": result.threshold,
        "severity": result.severity,
        "detail": result.detail,
    }


def _disk_resolution(path: str):
    """(width, height) the file on disk actually carries, or None.

    None means there is no video stream to compare (audio-only, missing
    or unreadable) - the preflight proves staleness and nothing more,
    so what cannot be compared is skipped rather than flagged.

    Read through `pool_stream_meta.disk_stream` - the one comparison
    the reel build's refresh uses too, so the two cannot disagree
    about what stale means.
    """
    from library.tools import pool_stream_meta

    stream = pool_stream_meta.disk_stream(path)
    if stream["width"] is None or stream["height"] is None:
        return None
    return (stream["width"], stream["height"])


def _pool_resolution(item) -> Optional[tuple]:
    """(width, height) Resolve's pool metadata claims for this item."""
    from library.tools import pool_stream_meta

    stream = pool_stream_meta.pool_stream(item)
    if stream["width"] is None or stream["height"] is None:
        return None
    return (stream["width"], stream["height"])


def _iter_bin_items(folder):
    """Every pool item under this folder, recursively. Read-only."""
    try:
        clips = folder.GetClipList() or ()
    except Exception:  # noqa: BLE001 - live API
        clips = ()
    for clip in clips:
        yield clip
    try:
        subs = folder.GetSubFolderList() or ()
    except Exception:  # noqa: BLE001 - live API
        subs = ()
    for sub in subs:
        yield from _iter_bin_items(sub)


def _timeline_bins(project, timeline_name: str) -> list:
    """The pool folders carrying this reel's own overlays, by exact name.

    Caption and motion-graphics renders are filed under bins named for
    the timeline that placed them, so the bins named exactly the reel's
    name ARE its overlay set - no layout coupling beyond the name, and
    another reel's stale file can never refuse this reel's deliver.
    """
    found = []

    def _search(folder):
        try:
            subs = folder.GetSubFolderList() or ()
        except Exception:  # noqa: BLE001 - live API
            return
        for sub in subs:
            try:
                name = sub.GetName()
            except Exception:  # noqa: BLE001 - live API
                continue
            if name == timeline_name:
                found.append(sub)
            _search(sub)

    try:
        _search(project.GetMediaPool().GetRootFolder())
    except Exception:  # noqa: BLE001 - live API
        pass
    return found


def overlay_staleness(project, timeline_name: str) -> list:
    """Generated overlays whose pool metadata disagrees with their file.

    Each entry names the clip, its path, what the pool claims and what
    the disk carries - or `missing: True` when the file is gone. Empty
    means every overlay the reel binds decodes as what Resolve thinks
    it is. Read-only: this refuses, it never repairs, because repairing
    means rebinding the captain's timeline and that is a build's act,
    not a deliver's.
    """
    stale = []
    for folder in _timeline_bins(project, timeline_name):
        for item in _iter_bin_items(folder):
            try:
                path = item.GetClipProperty("File Path")
            except Exception:  # noqa: BLE001 - live API
                continue
            if not path:
                continue
            try:
                clip = item.GetClipProperty("Clip Name") or path
            except Exception:  # noqa: BLE001 - live API
                clip = path
            if not os.path.isfile(path):
                stale.append({"clip": str(clip), "path": path,
                              "missing": True})
                continue
            disk = _disk_resolution(path)
            pool = _pool_resolution(item)
            if disk is not None and pool is not None and disk != pool:
                stale.append({
                    "clip": str(clip),
                    "path": path,
                    "pool_resolution": f"{pool[0]}x{pool[1]}",
                    "disk_resolution": f"{disk[0]}x{disk[1]}",
                })
    return stale


def _refuse_stale_overlays(stale: list) -> str:
    """The refusal, naming every stale overlay and the way out."""
    lines = [
        "the reel's overlays changed underneath Resolve's pool metadata - "
        "rendering now would fail decoding them, so this refuses BEFORE "
        "queueing a job rather than burning a render to learn it:",
    ]
    for entry in stale:
        if entry.get("missing"):
            lines.append(f"  - {entry['clip']}: file is gone "
                         f"({entry['path']})")
        else:
            lines.append(
                f"  - {entry['clip']}: pool says "
                f"{entry['pool_resolution']}, disk carries "
                f"{entry['disk_resolution']} ({entry['path']})")
    lines += [
        "",
        "Re-rendered overlay files are re-imported with fresh metadata "
        "only by a BUILD, at its staging step where rebinding is safe - "
        "a deliver that repaired this itself would rebind the captain's "
        "live timeline outside a build, which is refused. Rebuild the "
        "reel once the pool-metadata refresh lands, then deliver again.",
    ]
    return "\n".join(lines)


def _timeline_expected_seconds(timeline) -> float:
    """How long the timeline plays, in seconds. Refused, never guessed."""
    try:
        fps = float(timeline.GetSetting("timelineFrameRate"))
    except (TypeError, ValueError, AttributeError):
        fps = 0.0
    if not fps or fps <= 0:
        raise DeliverRefused(
            "the timeline reports no usable frame rate, so the render "
            "could not be checked against its duration. Refusing rather "
            "than guessing.")
    try:
        start = int(timeline.GetStartFrame())
        end = int(timeline.GetEndFrame())
    except (TypeError, ValueError, AttributeError):
        raise DeliverRefused(
            "the timeline reports no usable frame range, so the render "
            "could not be checked against its duration. Refusing rather "
            "than guessing.")
    frames = max(end - start + 1, 1)
    return frames / fps


def deliver_reel(project_folder: str, reel: Optional[int],
                 output_dir: str = "", file_name: str = "",
                 timeout_seconds: int = 1800) -> dict:
    """Render ONE approved, built reel to a file and verify the file.

    The ONLY path that renders a reel. Nothing calls this implicitly -
    the caller is `manage_project.py deliver-reel`, which the captain
    invokes by hand with a reel number.

    Touches nothing it is told not to: the render selects the reel's
    timeline and reads it; no timeline, marker or candidate is written.
    """
    from library.tools.project_layout import Area, ProjectLayout

    project_folder = os.path.abspath(project_folder)
    settings = resolve_deliver_settings(project_folder)
    timeline_name = timeline_name_for_reel(project_folder, reel)

    layout = ProjectLayout(project_folder)
    dest_dir = output_dir or str(layout.write_dir(Area.EXPORTS))
    os.makedirs(dest_dir, exist_ok=True)
    out_name = (file_name.strip() if file_name and file_name.strip()
                else render_file_name(timeline_name, settings["naming"],
                                      settings["extension"]))

    from library.tools import reel_build
    from library.tools.execution import resolve_render

    with open(os.path.join(project_folder, "project.yaml"),
              encoding="utf-8") as handle:
        import yaml as _yaml
        resolve_name = ((_yaml.safe_load(handle).get("resolve") or {}).get(
            "project_name", os.path.basename(project_folder)))
    project = reel_build._connect_resolve_project(resolve_name)
    # The captain's cursor, saved so it can be put back. Selecting the
    # reel is a write to global Resolve state; leaving it there would
    # hand the captain back a session pointing at a reel they did not
    # open. Restored best-effort in the finally below - a restore that
    # raises must not mask the render's own verdict.
    try:
        prior = project.GetCurrentTimeline()
        prior_name = prior.GetName() if prior is not None else ""
    except Exception:  # noqa: BLE001 - best-effort read of live state
        prior_name = ""
    timeline = resolve_render._select_timeline(project, timeline_name)
    expected_seconds = _timeline_expected_seconds(timeline)

    # Fail fast, naming the files: a render against stale overlay
    # metadata burns the captain's machine to learn what the pool
    # already says. (2026-09-13: every Reel 26 caption re-rendered at
    # the constant canvas decoded as its predecessor's width.)
    stale = overlay_staleness(project, timeline.GetName())
    if stale:
        raise DeliverRefused(_refuse_stale_overlays(stale))

    try:
        report = resolve_render.render_timeline(
            timeline_name=timeline.GetName(),
            output_dir=dest_dir,
            output_name=os.path.splitext(out_name)[0],
            fmt=settings["format"],
            codec=settings["codec"],
            timeout_seconds=timeout_seconds,
        )
    finally:
        if prior_name and prior_name != timeline.GetName():
            try:
                for i in range(1, project.GetTimelineCount() + 1):
                    candidate = project.GetTimelineByIndex(i)
                    if (candidate is not None
                            and candidate.GetName() == prior_name):
                        project.SetCurrentTimeline(candidate)
                        break
            except Exception:  # noqa: BLE001 - best-effort restore
                pass
    verdict = verify_deliverable(
        report["output_path"], expected_seconds,
        settings["width"], settings["height"])

    full = {
        "timeline_name": timeline_name,
        "reel": int(reel),
        "settings": settings,
        "render": report,
        "verification": verdict,
        "delivered": bool(verdict["passed"]),
    }
    sidecar = os.path.join(
        dest_dir, os.path.splitext(os.path.basename(
            report["output_path"]))[0] + ".deliver.json")
    with open(sidecar, "w", encoding="utf-8") as handle:
        json.dump(full, handle, indent=2, default=str)
    full["report_path"] = sidecar
    if not verdict["passed"]:
        raise DeliverRefused(
            f"rendered {report['output_path']} but verification FAILED "
            f"(duration={verdict['duration']['passed']}, "
            f"resolution={verdict['resolution']['passed']}, "
            f"audio={verdict['audio']['passed']}, "
            f"content_present={verdict['content_present']}). "
            f"Report: {sidecar}")
    return full
