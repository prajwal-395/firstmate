#!/usr/bin/env python3
"""
Apply the per-clip Fusion comps the manifest plans.

`build_effect_comp` dispatches on parameter NAMES, so a planner emitting a
name nothing here reads produces a comp without that effect and no warning.

Rules relocated from AGENTS.md 10.2
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.2 keeps the headline
and points here.

**A capability is only real where the renderer reads it.**
The renderer dispatches on parameter NAMES (`library/tools/execution/apply_fusion_comps.build_effect_comp`), so a planner emitting a name nothing reads produces a comp without that effect and no warning. [why](docs/RULE_EVIDENCE.md#unread-parameter-names)
- When you add a knob, add it to `build_effect_comp` in the same commit and assert it draws nodes (`tests/test_vfx_delivery.py`).
- When a design node cannot be delivered, record the reason where the design lives. Withdrawal is a legitimate outcome; a silent unread key is not.
- Every TOP-LEVEL manifest key is held to this by `tests/test_manifest_readers.py`: name a reader that really contains `manifest[key]`, plus one sentence saying what that reader does to the picture or the sound - or put it in `EXEMPTED_KEYS` with a reason.
- `docs/PIPELINE_PLAN.md` is the standing audit of which manifest keys have a reader. Check it before assuming a stage's output reaches the picture, and update it when you wire or withdraw one.


Rules relocated from AGENTS.md 5
--------------------------------
These are the engine's rules for this module.  They lived in
AGENTS.md until it was split by subsystem; the wording is unchanged,
so each rule is findable by its own words, and AGENTS.md 5
keeps the headline and points here.

- Never create a timeline and use `ImportFusionComp` in the same Python process.
"""

import sys
import os
import json
import argparse

sys.path.append("/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules")
os.environ["RESOLVE_SCRIPT_API"] = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
os.environ["RESOLVE_SCRIPT_LIB"] = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
try:
    import DaVinciResolveScript as dvr
except ImportError as e:
    err_msg = str(e)
    class _MissingDVR:
        def __getattr__(self, name):
            def _missing(*args, **kwargs):
                raise RuntimeError(f"DaVinciResolveScript is not installed or not found on PYTHONPATH: {err_msg}")
            return _missing
    dvr = _MissingDVR()

# This module runs both as a script (launched by resolve_build_timeline in
# its own process) and as `library.tools.execution.apply_fusion_comps`, so
# put library/tools on the path rather than assume either entry point.
_TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)
# And the REPOSITORY ROOT, because the comp builder this pass calls
# imports its neighbours by their package name
# (`library.tools.tv_power`, `library.tools.subject_grade`). Run as a
# script, `sys.path[0]` is this file's directory and the repository root
# is on the path only if the launcher happened to put it there - so the
# switch animation and the subject-scoped grade raised
# ModuleNotFoundError inside the subprocess, which the caller sees only
# as "the Fusion pass failed". Added here rather than made defensive at
# each import: there is one entry point and three importers.
_REPO_ROOT = os.path.dirname(os.path.dirname(_TOOLS_DIR))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
from library.tools.resolve_lock import under_lease
try:
    from library.tools.resolve_lock import (
        ResolveRaceError, assert_current_timeline)
except ImportError:  # pragma: no cover - script entry point
    from resolve_lock import ResolveRaceError, assert_current_timeline
from transition_vocabulary import canonical_type, is_cut, withdrawal_reason

from fusion.comp_builder import ZOOM_KEYS, build_effect_comp, normalize_effects

try:
    from library.tools.treatment_verify import (
        verify_and_undo, verify_and_undo_drift)
    from library.tools import pipeline_skills as _skills
    from library.tools import comp_media_window as _comp_window
except ImportError:  # pragma: no cover - script entry point
    from treatment_verify import verify_and_undo, verify_and_undo_drift
    import pipeline_skills as _skills
    import comp_media_window as _comp_window

# Both entry points again: as a script the package path does not exist.
try:
    from execution.fusion_tracks import fusion_comp_tracks
except ImportError:  # pragma: no cover - package import path
    from library.tools.execution.fusion_tracks import fusion_comp_tracks


def _source_resolution(mpi):
    """(width, height) of the frame Fusion composites over, or None.

    Read off the MediaPoolItem rather than assumed, and judged by what
    Resolve returns: `GetClipProperty("Resolution")` gives "1920x1080".
    None means "could not tell", and the caller refuses the comp
    rather than sizing a canvas by guess.

    Rotation is deliberately NOT applied. Resolve reports the STORED
    frame, and Fusion's MediaIn delivers the stored frame - the display
    orientation is applied downstream, on the timeline. Swapping the axes
    here would put the band back on exactly the clips that do not have
    it.
    """
    if not mpi:
        return None
    raw = mpi.GetClipProperty("Resolution")
    if not raw or "x" not in str(raw):
        return None
    try:
        width, height = (int(part) for part in str(raw).split("x", 1))
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return (width, height)


class DestinationMismatchError(Exception):
    """Raised when the current Resolve project or timeline does not match
    the expected destination.

    This is the REFUSAL that prevents the Fusion subprocess from writing
    comps onto the wrong timeline.  The subprocess is launched as a
    separate process (because ImportFusionComp cannot share a process
    with timeline creation), and between launch and first mutation the
    current project or timeline can change - eleven live davinci-resolve-mcp
    server processes were measured on this machine, each able to call
    SetCurrentTimeline.  Verify-immediately-before-and-refuse is what
    protects a write from a mutator that never agreed to any lock.
    """


def verify_destination(resolve, expected_project, expected_timeline):
    """Check that the current Resolve project and timeline match expectations.

    Must be called IMMEDIATELY before the first mutation, not at import
    time or argument parsing time - the window between check and use must
    be as small as possible.

    Returns (project, timeline) on success.
    Raises DestinationMismatchError on any mismatch.
    """
    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()
    if not project:
        raise DestinationMismatchError(
            "No Resolve project is open. Expected project "
            f"{expected_project!r}, timeline {expected_timeline!r}."
        )

    actual_project = project.GetName()
    if actual_project != expected_project:
        raise DestinationMismatchError(
            f"Wrong Resolve project: expected {expected_project!r}, "
            f"got {actual_project!r}. Refusing to write Fusion comps "
            f"onto the wrong project."
        )

    timeline = project.GetCurrentTimeline()
    if not timeline:
        raise DestinationMismatchError(
            f"No timeline is current in project {actual_project!r}. "
            f"Expected timeline {expected_timeline!r}."
        )

    actual_timeline = timeline.GetName()
    if actual_timeline != expected_timeline:
        raise DestinationMismatchError(
            f"Wrong timeline: expected {expected_timeline!r}, "
            f"got {actual_timeline!r} in project {actual_project!r}. "
            f"Refusing to write Fusion comps onto the wrong timeline."
        )

    return project, timeline


def assert_destination(resolve, expected_project, expected_timeline):
    """Set the cursor to the expected timeline, then verify it.

    The subprocess is handed its destination across the process
    boundary, and whatever the current timeline happens to be when it
    starts is ambient shared state - a sibling lane's final, the
    captain's hand, another tool's scratch. Depending on it stalls
    every concurrent wave on a refusal (measured 2026-09-20: a
    duplicate-take rebuild died on a sibling's Reel 05). Under the
    caller's exclusive lease, ASSERT the cursor to the timeline this
    pass is about to composite, then read it back with
    `verify_destination` - the read-back stays, because against a
    mutator that never agreed to any lock the only protection is
    verify-immediately-before-mutation and refuse.

    The project is never switched, only the timeline within it: a
    wrong project refuses exactly as before.
    """
    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()
    if not project:
        raise DestinationMismatchError(
            "No Resolve project is open. Expected project "
            f"{expected_project!r}, timeline {expected_timeline!r}."
        )
    if project.GetName() != expected_project:
        raise DestinationMismatchError(
            f"Wrong Resolve project: expected {expected_project!r}, "
            f"got {project.GetName()!r}. Refusing to move the cursor "
            f"onto another project's timeline."
        )
    target = None
    for index in range(1, project.GetTimelineCount() + 1):
        candidate = project.GetTimelineByIndex(index)
        if candidate and candidate.GetName() == expected_timeline:
            target = candidate
            break
    if target is None:
        raise DestinationMismatchError(
            f"Timeline {expected_timeline!r} not found in project "
            f"{expected_project!r}: the staging this pass was to "
            f"composite is gone, so there is nothing to assert onto."
        )
    # Through the guard, under this pass's exclusive lease: the
    # establishment is itself a write to instance-global state, and a
    # direct set bypasses the lease refusal. An unleased cursor move
    # killed a sibling lane's pass on 2026-09-20. A race the guard
    # reports is this pass's own refusal type: the destination would
    # not stay put, so there is nothing to composite onto.
    try:
        assert_current_timeline(project, target)
    except ResolveRaceError as exc:
        raise DestinationMismatchError(
            f"Wrong timeline: expected {expected_timeline!r}, but the "
            f"cursor would not stay put ({exc}). Refusing to write "
            f"Fusion comps onto a moving destination."
        ) from exc
    return verify_destination(resolve, expected_project, expected_timeline)


def _map_clips_to_items(clips, items):
    """Match one track's manifest clip specs to the items really placed.

    A spec that names no source file, or whose file does not match the
    next unconsumed item, is skipped rather than guessed at: the index is
    what decides which clip a comp lands on.

    Matching is by FULL PATH only.  The basename fallback that used to
    sit here was the exact defect H2 describes: the captain's rough cut
    is built from the same footage, so on the wrong timeline the matcher
    does not fail - it SUCCEEDS, and writes Fusion comps onto the wrong
    clips.  A matcher that succeeds against wrong material is worse than
    one that fails.
    """
    mapping = {}
    item_idx = 0
    for orig_ci, clip_spec in enumerate(clips):
        if item_idx >= len(items):
            break
        src = clip_spec.get('source_file', '')
        if not src:
            continue
        mpi = items[item_idx].GetMediaPoolItem()
        mpi_path = mpi.GetClipProperty("File Path") if mpi else ""
        if mpi_path == src:
            mapping[orig_ci] = item_idx
            item_idx += 1
    return mapping


@under_lease("apply fusion comps")
def apply_fusion_comps(manifest, project_folder,
                       expected_project=None, expected_timeline=None,
                       step_id="render"):
    resolve = dvr.scriptapp("Resolve")
    if not resolve:
        print("ERROR: Could not connect to Resolve.", file=sys.stderr)
        return False

    # ── Destination guard ──
    # ASSERT the cursor under this pass's exclusive lease, then verify
    # IMMEDIATELY before the first mutation. Depending on ambient
    # current-timeline state stalls every concurrent wave on a refusal
    # (2026-09-20: a rebuild died on a sibling lane's final); the
    # read-back stays because against a mutator that never agreed to
    # any lock the only protection is verify-and-refuse.
    if expected_project and expected_timeline:
        try:
            project, timeline = assert_destination(
                resolve, expected_project, expected_timeline)
        except DestinationMismatchError as exc:
            print(f"DESTINATION MISMATCH: {exc}", file=sys.stderr)
            return False
    else:
        pm = resolve.GetProjectManager()
        project = pm.GetCurrentProject()
        if not project:
            print("ERROR: No project open.", file=sys.stderr)
            return False

        timeline = project.GetCurrentTimeline()
        if not timeline:
            print("ERROR: No timeline open.", file=sys.stderr)
            return False
        
    fps = float(timeline.GetSetting("timelineFrameRate") or 30)
    
    # Every track that carries per-clip Fusion comps, in build order.
    # V2 is here because the house look is merged onto B-roll as well as
    # A-roll, and reading V1 alone left every cutaway ungraded beside the
    # clip it was cut into. See library/tools/execution/fusion_tracks.py.
    comp_tracks = fusion_comp_tracks(manifest)
    v1_clips = comp_tracks[0][1]

    fusion_effects = manifest.get('fusion_effects', {})
    per_clip_effects = fusion_effects.get('per_clip', {})
    # fusion_effects.transitions is the list compile_manifest indexes
    # against the V1 clips and validates. The top-level manifest
    # 'transitions' key is the planner's record and carries no clip index
    # at all, so reading it put every transition on clip 0.
    transition_specs = fusion_effects.get('transitions', [])

    transition_by_clip = {}
    for tspec in transition_specs:
        ttype = canonical_type(tspec.get('type', tspec.get('transition_type')))
        if ttype is None:
            raise ValueError(
                f"Transition {tspec!r} names a type the renderer cannot "
                f"draw: {withdrawal_reason(tspec.get('type', tspec.get('transition_type')))}"
            )
        if is_cut(ttype):
            continue

        if 'after_clip' not in tspec:
            raise ValueError(
                f"Transition {tspec!r} carries no after_clip index. It used "
                f"to default to 0, which stacked every transition on the "
                f"first clip of the timeline."
            )
        after_idx = tspec['after_clip']

        dur_f = tspec.get('duration_frames', 12)
        transition_by_clip.setdefault(after_idx, {})
        transition_by_clip[after_idx]['tail_transition'] = ttype
        transition_by_clip[after_idx]['tail_transition_frames'] = dur_f
        next_idx = after_idx + 1
        transition_by_clip.setdefault(next_idx, {})
        transition_by_clip[next_idx]['head_transition'] = ttype
        transition_by_clip[next_idx]['head_transition_frames'] = dur_f

    has_any_effects = per_clip_effects or transition_by_clip
    # One aggregate record of what the treatment check saw, per clip.
    # Written as a verify_treatment receipt at the end of the pass, so
    # the look-before-you-ship evidence lives where the other skill
    # receipts live - readable back from disk, never a self-report.
    treatment_report = []
    # One row per comp whose media window was read back after import -
    # the conform's receipt, for the same reason `treatment_report`
    # exists: the window is what decides whether the reel renders at
    # all, so a build that repaired one says so rather than going quiet.
    comp_window_receipts = []

    # Imported at function scope, not inside the `has_any_effects` branch:
    # the generator-overlay pass at the bottom of this function reads
    # `import_effect_to_clip`, and a plan with generator overlays but no
    # per-clip VFX and no drawn transitions never entered that branch, so
    # the name was unbound and the whole subprocess died with a NameError.
    # resolve_build_timeline only records that as a warning, so the
    # generators silently never reached their carriers.
    #
    # import_customized_effect is deliberately NOT imported - see the
    # built-in branch below.
    try:
        from builtin_effect_loader import (
            list_builtin_effects, import_effect_to_clip, is_generator_effect,
        )
    except ImportError:
        list_builtin_effects = None
        import_effect_to_clip = None
        is_generator_effect = None

    # Map legacy vfx_entries. VFX are planned against V1 only - a vfx
    # entry names a timeline instant and compile_manifest resolves it to
    # the A-roll clip playing there.
    v1_items = timeline.GetItemListInTrack("video", 1) or []
    orig_to_item = _map_clips_to_items(v1_clips, v1_items)

    vfx_entries = manifest.get('vfx', [])
    if vfx_entries and v1_items:
        for vfx in vfx_entries:
            start_sec = vfx.get('timeline_start', 0)
            start_f = round(start_sec * fps)
            params = vfx.get('params', {})
            preset = vfx.get('effect_type', vfx.get('preset', ''))
            
            for idx, clip in enumerate(v1_items):
                if clip.GetStart() <= start_f < clip.GetEnd():
                    orig_ci = next((k for k, v in orig_to_item.items() if v == idx), None)
                    if orig_ci is not None:
                        label = v1_clips[orig_ci].get('label', f'clip_{orig_ci}')
                        if label not in per_clip_effects:
                            per_clip_effects[label] = {}
                        if preset:
                            per_clip_effects[label]['_preset'] = preset
                        for k, v in params.items():
                            per_clip_effects[label][k] = v
                        has_any_effects = True
                    break

    if has_any_effects:
        print(f"\n── Fusion .comp: {len(per_clip_effects)} VFX, {len(transition_specs)} transitions ──", file=sys.stderr)

        try:
            from custom_asset_bank import bank_comp
        except ImportError as e:
            print(f"Failed to import custom_asset_bank: {e}", file=sys.stderr)
            return False

        # One flat work list over every track that carries comps, so the
        # per-clip body below is written once. Each entry is a clip that
        # was really placed - a spec with no matching timeline item is
        # dropped by _map_clips_to_items rather than guessed at.
        work = []
        for track_index, track_clips, applies_transitions in comp_tracks:
            if not track_clips:
                continue
            track_items = (
                v1_items if track_index == 1
                else (timeline.GetItemListInTrack("video", track_index) or []))
            track_map = _map_clips_to_items(track_clips, track_items)
            for ci, spec in enumerate(track_clips):
                if ci in track_map:
                    work.append((track_index, track_items, track_map[ci],
                                 ci, spec, applies_transitions))

        for (track_index, track_items, item_idx, orig_ci, clip_spec,
             applies_transitions) in work:
            where = f"V{track_index}:{orig_ci}"
            label = clip_spec.get('label', f'clip_{orig_ci}')

            # A block-type preset used to be substituted here when a clip
            # carried no effects. It was unreachable twice over and is
            # deleted; see docs/PIPELINE_PLAN.md P3.4.
            effects = dict(per_clip_effects.get(label, {}))

            preset_name = effects.get('_preset', None)
            
            # Exact match only. The substring fallback that used to sit
            # here could turn one effect name into an unrelated built-in -
            # the handoff tells the planner to use the exact snake_case
            # name, so a near miss is a mistake to surface, not to guess at.
            builtin_effect = None
            if preset_name and list_builtin_effects:
                if preset_name in (list_builtin_effects() or {}):
                    builtin_effect = preset_name

            # Defense in depth: reject generator presets at the renderer.
            # A generator has no image input and would cover the clip's
            # picture rather than modify it. An unclassifiable preset is
            # rejected too - the gate must fail closed, not open.
            if builtin_effect and is_generator_effect:
                try:
                    if is_generator_effect(builtin_effect):
                        print(
                            f"  ✗ [{where}] {label}: Rejected generator "
                            f"preset {builtin_effect} - no image input, "
                            f"cannot modify the picture as a clip effect",
                            file=sys.stderr,
                        )
                        builtin_effect = None
                except ValueError as exc:
                    print(
                        f"  ✗ [{where}] {label}: Rejected unclassifiable "
                        f"preset {builtin_effect} - classifier raised: {exc}",
                        file=sys.stderr,
                    )
                    builtin_effect = None


            if builtin_effect and import_effect_to_clip:
                # DaVinci's own .setting file goes to Resolve byte-identical,
                # with no parser in the path.
                #
                # This used to branch to import_customized_effect whenever the
                # clip carried any parameter, which is always: compile_manifest
                # merges the film look onto every V1/V2 clip. That path parsed
                # the macro and re-serialized it, which dropped the
                # GroupOperator wrapper and every InstanceInput - including
                # MainInput1, the declaration that gives the macro its image
                # input. Chromatic Aberration came out with its DirectionalBlur
                # wired to nothing.
                #
                # The overrides it was applying are our own comp-engine
                # parameter names (glow_gain, film_grain, vignette...), read
                # only by library/tools/fusion/comp_builder.py. No Resolve tool
                # reads them, so setting them on a macro's nodes never did
                # anything but corrupt the file. Dropping them costs the
                # picture nothing.
                effects.pop('_preset', None)
                tl_clip = track_items[item_idx]
                for cn in (tl_clip.GetFusionCompNameList() or []):
                    tl_clip.DeleteFusionCompByName(cn)

                import_effect_to_clip(tl_clip, builtin_effect)

                comp_names = tl_clip.GetFusionCompNameList()
                if comp_names and len(comp_names) > 0:
                    print(f"  ✓ [{where}] {label}: Imported built-in effect {builtin_effect}", file=sys.stderr)
                else:
                    print(f"  ✗ [{where}] {label}: Import built-in effect {builtin_effect} failed", file=sys.stderr)
                continue

            preset_name = effects.pop('_preset', None)
            tl_clip = track_items[item_idx]

            # Transitions are indexed against the V1 clip list; see
            # fusion_tracks.TRANSITION_TRACK.
            trans_params = (transition_by_clip.get(orig_ci, {})
                            if applies_transitions else {})
            if trans_params:
                effects.update(trans_params)

            if not effects:
                continue

            mpi = tl_clip.GetMediaPoolItem()
            if not mpi:
                continue
            frames_prop = mpi.GetClipProperty('Frames')
            clip_dur = int(frames_prop) if frames_prop else tl_clip.GetDuration()
            # The frame FUSION sees, which is the source clip's own, not
            # the delivery format. Every Background node the comp builds
            # is a solid image merged over MediaIn, so the wrong size
            # paints a hard-edged rectangle in the middle of the picture.
            # No fallback size: a MediaPoolItem that will not state its
            # Resolution refuses the comp, by clip, rather than sizing
            # a canvas by guess.
            source_res = _source_resolution(mpi)
            if source_res is None:
                raise RuntimeError(
                    f"REFUSING to build: [{where}] {label} carries "
                    f"effects ({sorted(effects)}) but its MediaPoolItem "
                    f"states no Resolution, so no canvas can be sized. "
                    f"A guessed size paints a wrong-size Background "
                    f"over the picture."
                )

            # The played segment within the source clip.  The manifest
            # carries source_in/source_out in seconds; convert to frames
            # using the source clip's native FPS so the comp builder
            # places keyframes inside the window the timeline plays.
            src_in_sec = clip_spec.get('source_in', 0.0)
            src_out_sec = clip_spec.get('source_out', 0.0)
            if src_in_sec or src_out_sec:
                src_fps_str = mpi.GetClipProperty('FPS')
                src_fps = float(src_fps_str) if src_fps_str else fps
                effects['source_in_frame'] = round(src_in_sec * src_fps)
                effects['source_out_frame'] = round(src_out_sec * src_fps)

            has_zoom = any(k in effects for k in ZOOM_KEYS)
            normalize_effects(effects, has_zoom)

            # LOOK at what the treatment draws, where the damage happens.
            # The timeline item knows how many frames really render -
            # the source span can count more (pool fps vs timeline fps),
            # and an end-anchored animation keyed past the end of what
            # plays never draws. A failed treatment is undone here
            # (dropped, rebuilt, recorded) rather than shipped blind or
            # held for the captain to find by eye. A clip that will not
            # state its duration is checked against the assumed horizon,
            # and the receipt says so - an unread duration must not take
            # the check down with it.
            get_duration = getattr(tl_clip, "GetDuration", None)
            try:
                played = int(get_duration() or 0) or None
            except (TypeError, ValueError):
                played = None
            # A treatment nobody declared may be undone: it was the
            # look's own offer and the picture is fine without it. One
            # the project DECLARED may not - an element the captain
            # asked for by name that silently does not draw is the
            # defect `library/tools/reel_ending.py` exists to end, and
            # it is what dropped Reel 13's switch-off twice. The plan
            # says which is which (`<key>_declared`), read BEFORE the
            # undo strips the key.
            declared_treatments = {
                key for key in list(effects)
                if key.endswith("_declared") and effects.get(key)}
            effects, tv_rows = verify_and_undo(
                effects, clip_dur, played, source_res=source_res)
            for row in tv_rows:
                treatment_report.append({"label": label, "where": where,
                                         **row})
                if row["undone"]:
                    if f"{row['treatment']}_declared" in declared_treatments:
                        raise RuntimeError(
                            f"REFUSING to build: [{where}] {label} "
                            f"declares the treatment "
                            f"{row['treatment']!r} and it will not draw "
                            f"here ({row['failure']}) over "
                            f"{played or clip_dur} played frame(s). A "
                            f"declared element that is undone leaves a "
                            f"reel missing the thing that was asked "
                            f"for, with one line in a build log - so "
                            f"the build stops instead. Give it room "
                            f"(reel_ending's tail_hold) or withdraw the "
                            f"declaration.")
                    print(
                        f"  ! [{where}] {label}: treatment "
                        f"{row['treatment']} failed "
                        f"({row['failure']}) - undone, the picture keeps "
                        f"what the footage had",
                        file=sys.stderr,
                    )

            # A drift that draws nothing is the TV switch-off again: a
            # planned slow_zoom the viewer never sees, keyed flat over
            # everything rendered. Looked at here, where the played
            # horizon is known, and undone the same way - while a
            # constant reframe is SAID and kept, never failed.
            #
            # Guarded by `has_zoom`, read BEFORE `normalize_effects`
            # above: the normalizer injects all-1.0 zoom defaults where
            # the plan armed nothing, and those defaults are not a
            # drift to judge - checking them would fail every still
            # shot in the pipeline.
            if has_zoom:
                effects, drift_row = verify_and_undo_drift(
                    effects, clip_dur, played, source_res=source_res)
                treatment_report.append({"label": label, "where": where,
                                         **drift_row})
                if drift_row["undone"]:
                    print(
                        f"  ! [{where}] {label}: drift failed "
                        f"({drift_row['failure']}) - undone, the picture "
                        f"keeps what the footage had",
                        file=sys.stderr,
                    )
                elif not drift_row["motion_over_time"]:
                    print(
                        f"  . [{where}] {label}: drift is a constant "
                        f"reframe, not motion over time - kept, "
                        f"no framing moves",
                        file=sys.stderr,
                    )

            # 2. BUILD, then bank. Never the other way round.
            #
            # The bank used to be consulted FIRST, on a key taken over
            # the inputs alone, and a hit skipped the build entirely.
            # That let a comp built by a previous version of the engine
            # reach the timeline unchanged - see the note at the top of
            # `custom_asset_bank`, and the switch-on that shipped a
            # bottom-left crop six minutes after its repair. The comp is
            # string assembly; building it and then asking whether those
            # exact bytes are already banked costs nothing and cannot
            # import a sibling build's leftovers.
            comp_content = build_effect_comp(effects, clip_dur, source_res,
                                             played_frames=played)
            comp_path, reused = bank_comp(project_folder, label,
                                          comp_content)

            for cn in (tl_clip.GetFusionCompNameList() or []):
                tl_clip.DeleteFusionCompByName(cn)

            # Imported from the bank, not from a scratch copy of it: the
            # file that reached the timeline is then still on disk under
            # its own content digest, which is how a build is read back.
            tl_clip.ImportFusionComp(comp_path)
            if reused:
                print(f"  · [{where}] {label}: comp unchanged since "
                      f"{os.path.basename(comp_path)}", file=sys.stderr)

            # 3. READ THE MEDIA WINDOW BACK. A comp is rendered over the
            # frames its item PLAYS and its MediaIn has its own validity
            # range; where that range does not cover them Resolve does
            # not draw black, it FAILS the whole render job at the first
            # uncovered frame. Six of the captain's eight built reels
            # were shipped that way - see
            # `library/tools/comp_media_window.py` for the measurement
            # and for why the repair is a re-import rather than
            # `SetInput`. This is the staging step where a media window
            # is established, so it is where the conform belongs.
            comp_window_receipts.append(
                _comp_window.conform_item(tl_clip, played, comp_path,
                                          label=f"[{where}] {label}"))
            if comp_window_receipts[-1]["repaired"]:
                print(f"  ! [{where}] {label}: comp media window did not "
                      f"cover the frames this item plays "
                      f"({comp_window_receipts[-1]['reason']}) - "
                      f"re-imported and verified", file=sys.stderr)
            elif comp_window_receipts[-1]["unreadable"]:
                # SAID, not swallowed: the check could not run here, and
                # a conform that goes quiet where it could not look reads
                # as a conform that passed.
                print(f"  . [{where}] {label}: comp media window could not "
                      f"be read back - not judged", file=sys.stderr)

            comp_names = tl_clip.GetFusionCompNameList()

            if comp_names and len(comp_names) > 0:
                comp = tl_clip.GetFusionCompByName(comp_names[0])
                if comp:
                    resolve.OpenPage("fusion")
                    dummy = comp.AddTool("Merge")
                    if dummy:
                        dummy.Delete()
                tools = comp.GetToolList() if comp else {}
                real_tools = [t for t in tools.values() if t.GetAttrs().get('TOOLS_RegID') not in ('MediaIn', 'MediaOut')]
                if len(real_tools) == 0:
                    print(f"  ✗ [{where}] {label}: empty comp (bad file)", file=sys.stderr)
                    continue
                parts = []
                xf = comp.FindTool("Transform1")
                if xf:
                    v0 = xf.GetInput("Size", 0)
                    vm = xf.GetInput("Size", clip_dur // 2)
                    if v0 and vm:
                        parts.append(f"zoom:{v0:.3f}→{vm:.3f}")
                tt = effects.get('tail_transition')
                ht = effects.get('head_transition')
                if tt: parts.append(f"tail={tt}")
                if ht: parts.append(f"head={ht}")
                detail = f" ({', '.join(parts)})" if parts else ""
                print(f"  ✓ [{where}] {label}: {len(real_tools)} tools{detail}", file=sys.stderr)

                # 4. DELIVER the file-backed Loaders the import stripped.
                # ImportFusionComp turns Loader nodes into MediaIn
                # placeholders, so the title and matte the comp text
                # declares are not on the timeline after the import
                # above. The delivery re-attaches them under comp.Lock()
                # - which suppresses the file-browser dialog an
                # unlocked AddTool opens - and REFUSES by name where a
                # loader did not decode: a black composite must never
                # ship in silence. The refusal propagates out of this
                # subprocess and fails the build step with the file
                # named. Only behind-subject composites ride this;
                # subject grades keep their existing path until their
                # own delivery is measured.
                if comp and 'behind_subject_matte' in effects:
                    try:
                        from library.tools.behind_subject import (
                            loader_specs_from_effects)
                        from library.tools.execution.deliver_loaders import (
                            deliver_loaders)
                    except ImportError:  # pragma: no cover - script path
                        from behind_subject import loader_specs_from_effects
                        from deliver_loaders import deliver_loaders
                    delivered = deliver_loaders(
                        comp, loader_specs_from_effects(effects))
                    print(f"  ✓ [{where}] {label}: loaders delivered "
                          f"({', '.join(sorted(delivered))})",
                          file=sys.stderr)
            else:
                print(f"  ✗ [{where}] {label}: ImportFusionComp failed", file=sys.stderr)


    # ── Generator overlays ──
    # Generator presets (.setting files) produce content from nothing and
    # are placed on carrier clips by resolve_build_timeline. Here we
    # import the .setting file onto each carrier clip. The row is read
    # off each overlay's `timeline_row` (stamped by the builder from the
    # track plan); V5 is the fallback for overlays placed before the
    # plan existed, not the address.
    gen_overlays = fusion_effects.get('generator_overlays', [])
    if gen_overlays and import_effect_to_clip:
        _gen_rows = {int(g.get("timeline_row", 5)) for g in gen_overlays}
        _gen_items = []
        for _row in sorted(_gen_rows):
            _gen_items.extend(timeline.GetItemListInTrack("video", _row) or [])
        v5_items = _gen_items
        if v5_items:
            print(
                f"\n-- Generator Imports (rows {sorted(_gen_rows)}): "
                f"{len(gen_overlays)} overlays, {len(v5_items)} clips --",
                file=sys.stderr,
            )
            for gi, gen in enumerate(gen_overlays):
                if gi >= len(v5_items):
                    print(
                        f"  X [{gi}] {gen['effect_name']}: no carrier clip",
                        file=sys.stderr,
                    )
                    continue
                tl_clip = v5_items[gi]
                effect_name = gen['effect_name']

                # Clear any existing comps on the carrier clip
                for cn in (tl_clip.GetFusionCompNameList() or []):
                    tl_clip.DeleteFusionCompByName(cn)

                import_effect_to_clip(tl_clip, effect_name)

                comp_names = tl_clip.GetFusionCompNameList()
                if comp_names and len(comp_names) > 0:
                    print(
                        f"  V [{gi}] {gen['effect_name']}: "
                        f"imported generator preset",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"  X [{gi}] {gen['effect_name']}: "
                        f"import failed",
                        file=sys.stderr,
                    )
        else:
            print(
                "  WARNING: generator_overlays present but no carrier clips",
                file=sys.stderr,
            )

    # The pass's own account of what the treatment check saw: one row
    # per armed treatment, written by this code - never by a model's
    # claim that it checked. A failed treatment was already undone
    # above; this is the record, not a second enforcement.
    if project_folder and treatment_report:
        undone = sum(1 for r in treatment_report if r.get("undone"))
        _skills.write_receipt(
            project_folder, step_id, "verify_treatment",
            {"skill": "verify_treatment",
             "passed": undone == 0,
             "clips_checked": len(treatment_report),
             "treatments_undone": undone,
             "rows": treatment_report})
    # What every comp's media window read back as, INCLUDING the ones
    # that needed nothing. A conform that only records its repairs
    # cannot be told apart from a conform that never ran.
    if project_folder and comp_window_receipts:
        repaired = sum(1 for r in comp_window_receipts if r.get("repaired"))
        _skills.write_receipt(
            project_folder, step_id, "conform_comp_windows",
            {"skill": "conform_comp_windows",
             "passed": True,
             "comps_checked": len(comp_window_receipts),
             "windows_repaired": repaired,
             "rows": comp_window_receipts})
    return True

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest")
    parser.add_argument("--project-folder", default="",
                        help="Project directory - where the custom asset "
                             "bank lives. Without it the bank was keyed to "
                             "the temp directory the manifest was written to")
    parser.add_argument("--expected-project", default=None,
                        help="Resolve project name the subprocess expects to "
                             "find current. Mismatch refuses all mutations.")
    parser.add_argument("--expected-timeline", default=None,
                        help="Resolve timeline name the subprocess expects to "
                        "find current. Mismatch refuses all mutations.")
    parser.add_argument("--step-id", default="render",
                        help="DAG node id the verify_treatment receipt is "
                        "filed under (render on the master path, "
                        "build_reels on the reels path).")
    args = parser.parse_args()

    with open(args.manifest) as f:
        manifest = json.load(f)

    project_folder = args.project_folder or os.path.abspath(
        os.path.dirname(args.manifest))
    success = apply_fusion_comps(
        manifest, project_folder,
        expected_project=args.expected_project,
        expected_timeline=args.expected_timeline,
        step_id=args.step_id,
    )
    sys.exit(0 if success else 1)
