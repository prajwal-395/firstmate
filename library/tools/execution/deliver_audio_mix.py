"""Round-trip the built timeline through OTIO so the mix is really on it.

`library/tools/otio_mix.py` owns the FORMAT - what a level looks like in
Resolve's OTIO and where a keyframe's frame number is measured from.
This owns the TRANSACTION: export, write the plan in, import the result
back as the timeline the rest of the build works on, and put the original
back if Resolve declines.

Resolve's own OTIO export has moved source ranges by a frame on measured
clips. The picture rows are therefore fingerprinted before export and
compared after import. A drifted or unreadable replacement is deleted,
and the untouched placement timeline is put back.

**The import rebuilds the timeline, so it has to happen at PLACEMENT
time.**  Fusion comps and CDL grades do not survive an OTIO import
(AGENTS.md section 5); placement, transform, markers and native
transitions do.  The renderer already placed every clip before it drew a
comp or set a grade, so calling this straight after the last audio clip
is placed costs nothing - but it is now a load-bearing order, not an
accident, and `tests/scenarios/test_plan_reaches_the_manifest.py` holds it.

**Everything here is judged by what Resolve returns**, per AGENTS.md
section 5.  `ImportTimelineFromFile` answers None or a timeline, with
nothing in between and no diagnostic, and it answers None for at least
three different reasons: a missing media file, a relative path, and a
name already taken.  The first two are checked before the call so the
run can say which; the third is why the placement timeline is renamed out
of the way first.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys

_TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO = os.path.dirname(os.path.dirname(_TOOLS))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from library.tools import otio_mix  # noqa: E402
from library.tools.project_layout import Area, ProjectLayout  # noqa: E402
from library.tools.resolve_lock import assert_current_timeline  # noqa: E402

# What the placement timeline is called while the mixed one takes its
# name.  It exists for the length of one import and is deleted, or
# renamed back when the import declines.
PREMIX_SUFFIX = "__premix"

# Resolve's own constant for `Timeline.Export`.  Read off the live
# `resolve` object when there is one; this is the documented value and
# the fallback for a caller that hands over a stub.
EXPORT_OTIO = 15


def _picture_structure(timeline) -> dict:
    """Read the picture rows by values that survive a timeline rebuild.

    Resolve assigns new timeline-item ids on an OTIO import, so ids cannot
    identify corresponding clips. The media path, row, source frames and
    record frames are the cut itself and are all read back directly.
    """
    track_count = timeline.GetTrackCount("video")
    if track_count is None:
        raise RuntimeError("video track count is unreadable")
    tracks = []
    for index in range(1, int(track_count) + 1):
        items = timeline.GetItemListInTrack("video", index) or []
        clips = []
        for item in items:
            pool_item = item.GetMediaPoolItem()
            if pool_item is None:
                raise RuntimeError(
                    f"V{index} item {item.GetName()!r} has no media pool item")
            media_path = pool_item.GetClipProperty("File Path")
            if not media_path:
                raise RuntimeError(
                    f"V{index} item {item.GetName()!r} has no readable media path")
            values = {
                "media_path": str(media_path),
                "source_start": item.GetSourceStartFrame(),
                "source_end": item.GetSourceEndFrame(),
                "record_start": item.GetStart(),
                "record_end": item.GetEnd(),
            }
            if any(value is None for value in values.values()):
                raise RuntimeError(
                    f"V{index} item {item.GetName()!r} has an unreadable "
                    "source or record range")
            clips.append({
                key: value if key == "media_path" else int(value)
                for key, value in values.items()
            })
        clips.sort(key=lambda clip: (
            clip["record_start"], clip["record_end"], clip["media_path"]))
        tracks.append({"index": index, "clips": clips})
    return {"video_tracks": tracks}


def _picture_fingerprint(structure: dict) -> str:
    canonical = json.dumps(structure, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _picture_differences(before: dict, after: dict) -> list[str]:
    differences = []
    before_tracks = before["video_tracks"]
    after_tracks = after["video_tracks"]
    if len(before_tracks) != len(after_tracks):
        differences.append(
            f"video track count {len(before_tracks)} -> {len(after_tracks)}")
    fields = ("media_path", "source_start", "source_end",
              "record_start", "record_end")
    for track_index in range(max(len(before_tracks), len(after_tracks))):
        if track_index >= len(before_tracks):
            differences.append(f"V{track_index + 1} appeared")
            continue
        if track_index >= len(after_tracks):
            differences.append(f"V{track_index + 1} disappeared")
            continue
        left = before_tracks[track_index]["clips"]
        right = after_tracks[track_index]["clips"]
        if len(left) != len(right):
            differences.append(
                f"V{track_index + 1} clip count {len(left)} -> {len(right)}")
        for clip_index in range(max(len(left), len(right))):
            label = f"V{track_index + 1} clip {clip_index + 1}"
            if clip_index >= len(left):
                differences.append(f"{label} appeared")
                continue
            if clip_index >= len(right):
                differences.append(f"{label} disappeared")
                continue
            for field in fields:
                if left[clip_index][field] != right[clip_index][field]:
                    differences.append(
                        f"{label} {field} "
                        f"{left[clip_index][field]!r} -> "
                        f"{right[clip_index][field]!r}")
    return differences


def _refuse_picture_drift(project, media_pool, original, replacement,
                          original_name: str) -> None:
    """Delete a drifted replacement and put the untouched timeline back."""
    if not media_pool.DeleteTimelines([replacement]):
        raise RuntimeError(
            "picture drift was detected, but Resolve refused to delete "
            "the replacement timeline")
    if original.GetName() != original_name and not original.SetName(original_name):
        raise RuntimeError(
            "picture drift was detected, but the original timeline could "
            "not be renamed back")
    assert_current_timeline(project, original)


def deliver_mix(resolve, project, media_pool, timeline, manifest, *,
                fps: float, project_folder: str) -> dict:
    """Put the planned levels on the timeline.  Returns what happened.

    On success `timeline` in the report is the NEW timeline object and
    the caller must use it from then on: the one it was handed is gone.
    On any refusal the original timeline is untouched and returned, and
    `reason` says why - which is what the marker fallback is written from.
    """
    report = {
        "delivered": False, "reason": "", "timeline": timeline,
        "timeline_name": timeline.GetName(), "applied": [], "unmatched": [],
        "complaints": [], "otio_path": "", "mixed_otio_path": "",
        "picture_fingerprint_before": "",
        "picture_fingerprint_after": "",
        "picture_differences": [],
    }

    targets = otio_mix.mix_targets(manifest, fps=fps)
    swaps = otio_mix.stem_swaps(manifest)
    if not targets and not swaps:
        planned = len((manifest.get("audio_mix", {}) or {})
                      .get("music_automation", []) or [])
        report["reason"] = (
            f"{planned} music level(s) planned and no A2 music clip to carry "
            f"them" if planned else
            "nothing planned: no music automation, no clip volumes, "
            "and no dialogue-cleanup stems")
        return report

    if not project_folder:
        report["reason"] = (
            "no project_folder, so there is nowhere in the project layout "
            "to write the OTIO")
        return report

    # The captain's declared levels (`external/mix_intent.json`,
    # `library/tools/mix_intent.py`): held post-plan and stamped
    # declared, through the one applier every path shares. No
    # declaration and the targets below are the plan's own, exactly as
    # before - a project that declares nothing gets the mix it always
    # got, byte for byte.
    from library.tools import mix_intent as _intent
    try:
        _pins = _intent.load_intent(project_folder)
    except _intent.MixIntentError as exc:
        raise RuntimeError(
            f"mix_intent cannot be honoured: {exc}. A declared level "
            f"the build cannot read must refuse, never mix past it.")
    if _pins:
        from library.tools.timeline_transcript import (
            transcript_path as _transcript_path)
        _transcript: dict = {}
        try:
            _transcript_file = str(_transcript_path(project_folder))
            if os.path.isfile(_transcript_file):
                with open(_transcript_file, encoding="utf-8") as handle:
                    _transcript = json.load(handle)
        except (OSError, ValueError):
            _transcript = {}
        if not isinstance(_transcript, dict) or not (
                _transcript.get("segments")):
            raise RuntimeError(
                "mix_intent declares levels but no transcript is on "
                "file: pins anchor to spoken words, so without measured "
                "speech the build cannot honour them. Transcribe first "
                "(`python3 -m library.tools.timeline_transcript "
                "<project> --write`).")
        targets, _applied, _skipped, _stale = (
            _intent.apply_declared_mix(
                targets, manifest, _transcript, _pins, fps=fps))
        for row in _applied:
            print(f"  mix intent: {row['label']} holds "
                  f"{row['level_db']:.1f}dB over "
                  f"{row['master_span'][0]:.2f}-"
                  f"{row['master_span'][1]:.2f}s "
                  f"({row['anchor_phrase']!r}) - {row['reason']}",
                  file=sys.stderr)
        for row in _skipped:
            print(f"  mix intent SKIPPED {row.get('label', '?')}: "
                  f"{row.get('reason', '')}", file=sys.stderr)
        for row in _stale:
            print(f"  mix intent STALE {row.get('anchor_phrase', '?')}: "
                  f"{row.get('reason', '')}", file=sys.stderr)
        report["mix_intent"] = {"applied": _applied,
                                "skipped": _skipped, "stale": _stale}

    layout = ProjectLayout(project_folder)
    name = timeline.GetName()
    export_path = str(layout.write_path(
        Area.TIMELINE_INTERCHANGE, f"{name}.otio", step="render"))
    mixed_path = str(layout.write_path(
        Area.TIMELINE_INTERCHANGE, f"{name}.mixed.otio", step="render"))
    report["otio_path"] = export_path

    try:
        picture_before = _picture_structure(timeline)
    except Exception as exc:  # noqa: BLE001 - Resolve getters fail independently.
        report["reason"] = (
            "could not fingerprint picture before the OTIO export; "
            f"refusing the round-trip: {exc}")
        return report
    report["picture_fingerprint_before"] = _picture_fingerprint(
        picture_before)

    exported = timeline.Export(export_path, getattr(resolve, "EXPORT_OTIO", EXPORT_OTIO))
    if not exported or not os.path.exists(export_path):
        report["reason"] = f"Resolve declined to export OTIO (returned {exported!r})"
        return report

    otio = otio_mix.load(export_path)

    missing = otio_mix.unresolvable_media(otio)
    if missing:
        # The import would return None here and say nothing at all.
        report["reason"] = (
            f"{len(missing)} referenced media file(s) are not on disk, and "
            f"the import answers None without naming one: "
            + ", ".join(os.path.basename(p) for p in missing[:4]))
        return report

    written = otio_mix.apply_mix(otio, targets)
    report["unmatched"] = written["unmatched"]
    stemmed = otio_mix.apply_stem_swaps(otio, swaps)
    report["stem_applied"] = stemmed["applied"]
    report["stem_unmatched"] = stemmed["unmatched"]
    for row in stemmed["applied"]:
        print(f"  cleanup stem: {row['label']} swaps in "
              f"{os.path.basename(row['stem_file'])}", file=sys.stderr)
    for row in stemmed["unmatched"]:
        print(f"  cleanup stem UNMATCHED {row.get('label', '?')}: "
              f"{row.get('reason', '')}", file=sys.stderr)
    if not written["applied"] and not stemmed["applied"]:
        report["reason"] = (
            f"none of {len(targets)} planned level(s) and "
            f"{len(swaps)} stem(s) matched a clip on the exported "
            f"timeline")
        return report

    otio_mix.save(otio, mixed_path, timeline_name=name)
    report["mixed_otio_path"] = mixed_path

    premix = f"{name}{PREMIX_SUFFIX}"
    if not timeline.SetName(premix):
        report["reason"] = f"could not rename the placement timeline to {premix}"
        return report

    imported = media_pool.ImportTimelineFromFile(
        os.path.abspath(mixed_path),
        {"timelineName": name, "importSourceClips": True})
    if not imported:
        timeline.SetName(name)
        report["reason"] = (
            "ImportTimelineFromFile returned None for the mixed timeline; "
            "the placement timeline was kept")
        return report

    # Establishing the cursor goes through the guard: the imported
    # timeline is what the rest of the build works on, and a direct
    # set bypasses the lease refusal and the fence's drift record.
    assert_current_timeline(project, imported)
    try:
        picture_after = _picture_structure(imported)
    except Exception as exc:  # noqa: BLE001 - unreadable results must fail closed.
        _refuse_picture_drift(project, media_pool, timeline, imported, name)
        report["reason"] = (
            "could not verify picture after the OTIO import; deleted the "
            f"replacement and kept the original timeline: {exc}")
        return report
    report["picture_fingerprint_after"] = _picture_fingerprint(picture_after)
    differences = _picture_differences(picture_before, picture_after)
    report["picture_differences"] = differences
    if differences:
        _refuse_picture_drift(project, media_pool, timeline, imported, name)
        examples = "; ".join(differences[:4])
        remaining = len(differences) - 4
        if remaining > 0:
            examples += f"; and {remaining} more"
        report["reason"] = (
            f"picture structure drifted across the OTIO round-trip "
            f"({len(differences)} difference(s): {examples}); "
            "refusing the mix and keeping the original timeline")
        return report

    report["timeline"] = imported
    report["timeline_name"] = imported.GetName()
    report["applied"] = written["applied"]
    report["delivered"] = True

    # Read the mix back off the imported timeline. Resolve accepting the
    # file is not evidence that a level is on a clip - the one claim this
    # module makes that it cannot check in its own code.
    check_path = str(layout.write_path(
        Area.TIMELINE_INTERCHANGE, f"{name}.delivered.otio", step="render"))
    if imported.Export(check_path, getattr(resolve, "EXPORT_OTIO", EXPORT_OTIO)) \
            and os.path.exists(check_path):
        reloaded = otio_mix.load(check_path)
        report["complaints"] = otio_mix.verify(
            reloaded, written["applied"])
        report["complaints"].extend(
            otio_mix.verify_stems(reloaded, stemmed["applied"]))
    else:
        report["complaints"] = ["could not re-export the mixed timeline to check it"]

    media_pool.DeleteTimelines([timeline])
    return report
