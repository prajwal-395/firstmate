"""Round-trip the built timeline through OTIO so the mix is really on it.

`library/tools/otio_mix.py` owns the FORMAT - what a level looks like in
Resolve's OTIO and where a keyframe's frame number is measured from.
This owns the TRANSACTION: export, write the plan in, import the result
back as the timeline the rest of the build works on, and put the original
back if Resolve declines.

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
