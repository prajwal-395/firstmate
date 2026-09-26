#!/usr/bin/env python3
"""
Step 6.02: Validate Output

Automated pre-checks on the rendered video before (optionally) handing
off to the LLM for subjective quality review.

Checks:
  1. File existence and reasonable size
  2. Technical validation via ffprobe (resolution, duration, codec, audio)
  3. Duration comparison against manifest expected duration
  4. Black frame detection (render sample frames, check file sizes)
  5. Audio presence and level check
  6. Every planned transition sits at a V1 cut point or V1/V2 seam
  7. The V1 track tiles with no gaps, where a B-roll cutaway or a
     declared black beat covers

Classification: Nondeterministic / Evaluation & Judgment
Input:  { "rendered_output": {...}, "assembly_manifest": {...} }
Output: { "validation_result": { status, checks, ... } }
"""
import json
import os
import subprocess
import sys
import tempfile
import traceback

# Import new QA modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))
from library.tools.project_layout import Area, ProjectLayout
from library.tools.render_qa import (
    DEFAULT_LUFS_TARGET,
    DEFAULT_TRUE_PEAK_CEILING_DBTP,
    RenderQAResult,
    run_full_render_qa,
)
from library.tools import render_watch
from library.tools.spine_contract import declared_black_beat_ranges
from library.tools.subtitle_qa import verify_subtitle_timing
from library.tools.transition_vocabulary import CUT_TYPES


def _run_ffprobe(filepath, *args):
    """Run ffprobe and return parsed JSON output."""
    try:
        cmd = [
            'ffprobe', '-v', 'quiet',
            '-print_format', 'json',
            *args,
            filepath,
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError,
            json.JSONDecodeError, OSError):
        return None


def _extract_frame(filepath, frame_num, output_path, fps=30):
    """Extract a single frame as PNG using ffmpeg. Returns True on success.

    Success means ffmpeg exited 0 AND a non-empty file is on disk - a
    zero-byte file is a capture that did not happen (see
    `marker_capture`'s "WHEN THE ROUTE FAILS"), and True beside one is
    what produced a false finding on 2026-09-10.
    """
    import os
    timestamp = frame_num / fps
    try:
        result = subprocess.run(
            ['ffmpeg', '-y', '-ss', f'{timestamp:.3f}',
             '-i', filepath, '-frames:v', '1',
             '-f', 'image2', output_path],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
        )
        return (result.returncode == 0 and os.path.exists(output_path)
                and os.path.getsize(output_path) > 0)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False



def _declared_black_beats(assembly_manifest: dict) -> list:
    """Black beats the plan declared, as timeline ranges.

    `compile_manifest` already accepted these holes on the strength of the
    declaration; the render carries the same ruling into the final gate,
    so a beat that survived compilation is not failed here after a full
    render. Black nobody declared still fails.
    """
    return declared_black_beat_ranges(assembly_manifest.get("_spine_blocks") or [])


def _framing_spans(assembly_manifest: dict) -> list:
    """What each stretch of the timeline was supposed to look like.

    One `render_qa.FramingSpan` per picture clip, so every sampled frame
    of the master can be judged against the framing in force over it
    rather than against a flat set of declarations for the whole video.

    The intent read is `framing_delivered`, NOT `framing_intent`. The
    first is what the clip's geometry produces and the second is what was
    asked for, and they part company wherever a source already covers the
    delivery frame - a portrait cutaway fills a 9:16 frame however firmly
    the project declared bars, because it has none to give. Judging the
    render against the declaration would fail exactly the videos whose
    framing is right. See `library/tools/framing_intent.py`, "Declared is
    not delivered". A manifest compiled before that key existed carries
    only `framing_intent`, which is the same number on every clip that
    was actually conformed, so it is read as the fallback.

    V1 first and V2 second, because `render_qa._intent_at` lets the later
    span win an overlap and V2 is the track that covers V1.
    """
    from library.tools.render_qa import FramingSpan

    project_settings = assembly_manifest.get("project", {})
    fps = project_settings.get("frame_rate", 30.0)

    spans = []
    for track in ("V1", "V2"):
        for clip in assembly_manifest.get("tracks", {}).get(track, {}).get("clips", []):
            delivered = clip.get("framing_delivered")
            if delivered is None:
                delivered = clip.get("framing_intent")
            if delivered is None:
                continue
            start_frame = clip.get("timeline_in_frame")
            end_frame = clip.get("timeline_out_frame")
            if start_frame is None or end_frame is None or end_frame <= start_frame:
                continue
            spans.append(FramingSpan(float(start_frame) / fps, float(end_frame) / fps,
                                     float(delivered)))
    return spans


def _grade_spans(assembly_manifest: dict):
    """One `render_qa.GradeSpan` per graded picture clip.

    Finding 28: the build judges the write (`cdl_readback` off
    `GetCDL`); 6.02 judges the exported pixels. Each track clip whose
    source carries a per-clip CDL becomes a span bounded by its
    timeline in/out, with the source file as the ungraded reference
    and the four specified terms as the demand. V1 first and V2
    second, the same order the picture reads in. A clip with an empty
    CDL (identity) still becomes a span: it demands nothing and reads
    as info, which keeps "graded nowhere" distinct from "judged
    nowhere". Returns None when the manifest names no color grade at
    all - ungraded by declaration, not by measurement.
    """
    from library.tools.render_qa import GradeSpan

    color_grade = assembly_manifest.get("color_grade")
    if color_grade is None:
        return None
    import os
    lookup = {}
    for adj in color_grade.get("per_clip_adjustments", []) or []:
        src = adj.get("source_file", "")
        if src:
            lookup[os.path.basename(src).lower()] = adj.get("cdl_values",
                                                            {}) or {}
    spans = []
    project_fps = (assembly_manifest.get("project", {})
                   .get("frame_rate", 30.0)) or 30.0
    for track in ("V1", "V2"):
        for clip in assembly_manifest.get("tracks", {}).get(
                track, {}).get("clips", []):
            src = clip.get("source_file", "")
            if not src:
                continue
            cdl = lookup.get(os.path.basename(src).lower())
            if cdl is None:
                continue
            # Seconds first; frames (over the project rate) only where
            # the seconds were never written - mixing the two without
            # converting judges a span hundreds of seconds long.
            try:
                if (clip.get("timeline_in") is not None
                        and clip.get("timeline_out") is not None):
                    start = float(clip["timeline_in"])
                    end = float(clip["timeline_out"])
                else:
                    start = float(clip["timeline_in_frame"]) / project_fps
                    end = float(clip["timeline_out_frame"]) / project_fps
                if (clip.get("source_in") is not None
                        and clip.get("source_out") is not None):
                    source_start = float(clip["source_in"])
                    source_end = float(clip["source_out"])
                else:
                    source_start = source_end = None
            except (TypeError, ValueError, KeyError, ZeroDivisionError):
                continue
            if end <= start:
                continue
            spans.append(GradeSpan(
                label=clip.get("label") or os.path.basename(src),
                timeline_start=start, timeline_end=end,
                source_path=src, cdl=dict(cdl),
                source_start=source_start, source_end=source_end))
    return spans


# The manifest keys carrying a rendered overlay, and the fps each track
# declares its `source_in_frame` in. Kept in step with
# `step_5_04_compile_manifest.OVERLAY_TRACKS`, which is the enumeration
# of what the manifest may carry: an overlay track this list forgets is
# an overlay whose ink reads as picture, which is the defect
# `measure_frame_occupancy` reopened three times.
OVERLAY_MANIFEST_KEYS = ("subtitle_overlay", "motion_graphics_overlay",
                         "timed_text_overlay")


def _overlay_segments(assembly_manifest: dict):
    """What the render drew OVER the picture, for the occupancy gate.

    P1 measures the letterbox bars, and an overlay drawn over a bar is
    neither dark nor flat, so the bar walk stops at it and the picture
    reads as taller than it is. It used to guess the footprint from the
    safe area, and every guess has been wrong for the next element
    somebody drew. These segments are the overlays themselves - P1 reads
    each one's alpha and masks exactly the pixels it drew.

    Returns None when the manifest names no overlay track AT ALL, because
    that is a manifest that has not been asked the question - a different
    claim from a manifest that carries the tracks and declares them
    empty, which is `[]` and is exact.
    """
    from library.tools.render_qa import OverlaySegment

    if not any(key in assembly_manifest for key in OVERLAY_MANIFEST_KEYS):
        return None

    segments = []
    for key in OVERLAY_MANIFEST_KEYS:
        track = assembly_manifest.get(key) or {}
        # The overlay steps render at the timeline's own rate and record
        # `source_in_frame` in it, so the second of the file that plays
        # at `timeline_start` is that frame over the track's fps.
        fps = float(track.get("fps") or 0.0) or float(
            (assembly_manifest.get("project") or {}).get("frame_rate") or 30.0)
        for seg in track.get("segments") or []:
            path = seg.get("overlay_path")
            start = seg.get("timeline_start")
            end = seg.get("timeline_end")
            if not path or start is None or end is None or end <= start:
                continue
            segments.append(OverlaySegment(
                str(path), float(start), float(end),
                float(seg.get("source_in_frame") or 0.0) / fps))
    return segments


def _music_bed(assembly_manifest: dict):
    """(music file, automation windows, offset) for speech-above-bed.

    The bed is the first A2 clip. The plan is
    `audio_mix.music_automation`, because that is the half that carries a
    LEVEL: `_spine_blocks[*].music_behavior` names the same behaviour in
    the same vocabulary (both now resolve through
    `library/tools/music_behavior.py`) but no dB, and P3 judges the render
    against the plan's own numbers.

    The OFFSET is the third thing P3 needs and used not to be passed at
    all.  Step 2.04 chooses which SECTION of the track plays and the A2
    clip carries that as `source_in`, so timeline second *t* is music
    file second *t + (source_in - timeline_in)*.  P3 was fitting the
    file from 0 against a render built from second 60 - see
    docs/RULE_EVIDENCE.md#the-bed-was-fitted-from-the-wrong-second.

    It did not always name the same behaviour. `_spine_block_entry` used
    to recompute a two-word `full`/`ducked` value from `block_type`, in
    which a declared silence did not exist at all - see
    docs/RULE_EVIDENCE.md#silence-lost-in-the-two-word-vocabulary.
    """
    clips = assembly_manifest.get("tracks", {}).get("A2", {}).get("clips", [])
    music_path = clips[0].get("source_file") if clips else None
    automation = (assembly_manifest.get("audio_mix") or {}).get("music_automation") or []
    if not music_path or not os.path.exists(music_path):
        return None, [], None
    offset = (float(clips[0].get("source_in", 0.0) or 0.0)
              - float(clips[0].get("timeline_in", 0.0) or 0.0))
    return music_path, automation, offset


def _plan_geometry_checks(assembly_manifest: dict) -> dict:
    """Transitions at their seams, V1 tiled end to end - as CHECKS.

    Both are plain arithmetic over `tracks` and `transitions`, with no
    taste in them: a transition's `cut_point_timeline` either sits
    where the picture actually cuts or it does not, and consecutive V1
    clips either abut or something covers the stretch between them.
    They used to be answerable only from the manifest rows in the
    prompt; holding them here is what lets those rows leave the prompt
    entirely.

    Two things the first draft of this check got wrong, measured on
    the round3 snapshot rather than reasoned out:

    - A V1 gap is not a hole when a B-roll cutaway covers it. Round3's
      V1 carries four multi-second gaps and every one is exactly a V2
      clip (`broll_1` over `[2.398, 5.398]`, and three more) - the
      combined picture is continuous, `compile_manifest` accepted it,
      and the render is correct. A V1-only tiling check fails that
      render, so a gap counts only when no V2 clip covers it. (AGENTS.md
      10.4: a gate that fails correct output is no coverage.)
    - A transition sits where the picture cuts, which is a V1 boundary
      OR a V1/V2 seam: round3's transitions claim the points where V1
      resumes after a cutaway, up to 0.24s off the clip edge. The
      quarter-second is not invented here - it is the tolerance
      `compile_manifest._v1_index_ending_at` already seats a drawn
      transition with, so a plan the compile accepted is a plan this
      gate accepts. And only DRAWN transitions are held at all: a
      `hard_cut` row draws nothing, so its drift from the edge (0.3s
      on the 001 snapshots' word-end+beat cuts) moves no pixel, and
      failing a render over it would be failing correct output.

    A gap the spine validly declared as an intentional black beat is
    likewise allowed through, exactly as `compile_manifest` allows it.

    Returns `{"transitions_at_seams": check, "v1_tiling": check}` in
    the shape `validate_output` merges into its verdict.
    """
    fps = float((assembly_manifest.get("project") or {}).get("frame_rate")
                or 30.0)
    # One frame, judged in seconds. The epsilon is for the threshold
    # itself: a gap of exactly one frame computes as
    # 0.03333333333333297 against a bound of 0.03333333333333333 and
    # would be missed by float noise - the same lesson
    # `compile_manifest._is_real_gap` documents.
    frame = 1.0 / max(fps, 1.0)
    tolerance = frame + 1e-6
    # The tolerance a drawn transition is seated with - see
    # `compile_manifest._v1_index_ending_at`. A plan the compile
    # accepted is a plan this gate accepts.
    SEAM_TOLERANCE = 0.25 + 1e-6

    tracks = assembly_manifest.get("tracks") or {}
    v1 = sorted(
        ((tracks.get("V1", {}) or {}).get("clips", []) or []),
        key=lambda c: c.get("timeline_in", 0.0),
    )
    v2 = sorted(
        ((tracks.get("V2", {}) or {}).get("clips", []) or []),
        key=lambda c: c.get("timeline_in", 0.0),
    )

    seams_check = {"pass": True, "issues": []}
    tiling_check = {"pass": True, "issues": []}
    if not v1:
        return {"transitions_at_seams": seams_check,
                "v1_tiling": tiling_check}

    # Every V1 edge is a place the picture can cut: an interior out is
    # a V1-to-V1 cut, an in after a gap is where V1 resumes under (or
    # after) a cutaway, and the last out is the V1-end/V2-start seam a
    # trailing cutaway begins on.
    edges = [c.get("timeline_in") for c in v1]
    edges += [c.get("timeline_out") for c in v1]
    edges = [e for e in edges if e is not None]
    for t in assembly_manifest.get("transitions") or []:
        tid = t.get("transition_id", "?")
        # CUT types draw NOTHING - the vocabulary says so in as many
        # words - so a hard/jump/match cut row carries no seating
        # obligation: its cut point is an aspiration (a word end, a
        # beat) recorded beside the V1 edge the mesh actually cut on,
        # and drift between the two moves no pixel. The same exclusion
        # `manifest_validator`'s uniformity check makes, for the same
        # reason. Only a DRAWN transition is seated from its cut point
        # - `compile_manifest` refuses one that sits at no V1 edge -
        # so only a drawn one can fail here.
        if t.get("transition_type") in CUT_TYPES:
            continue
        cut = t.get("cut_point_timeline", t.get("cut_point_original"))
        if cut is None:
            seams_check["pass"] = False
            seams_check["issues"].append(
                f"Transition {tid} names no cut point - neither "
                f"cut_point_timeline nor cut_point_original is set"
            )
            continue
        if not any(abs(cut - e) <= SEAM_TOLERANCE for e in edges):
            nearest = min(edges, key=lambda e: abs(cut - e))
            seams_check["pass"] = False
            seams_check["issues"].append(
                f"Transition {tid} claims cut point {cut:.3f}s, which "
                f"is {abs(cut - nearest):.3f}s from the nearest V1 "
                f"edge ({nearest:.3f}s) - no cut exists there"
            )

    beats = declared_black_beat_ranges(
        assembly_manifest.get("_spine_blocks") or [])

    def _declared(start: float, end: float) -> bool:
        return any(bs - 1e-6 <= start and end <= be + 1e-6
                   for bs, be in beats)

    def _v2_covers(start: float, end: float) -> bool:
        """A V2 clip spans the whole stretch, end to end.

        Covering is all-or-nothing: a cutaway over the middle with
        black peeking out on either side is still a hole, so slivers
        under one frame at either end are the only slack.
        """
        cursor = start
        for clip in v2:
            cin = clip.get("timeline_in")
            cout = clip.get("timeline_out")
            if cin is None or cout is None:
                continue
            if cin - cursor > tolerance:
                return False
            if cout > cursor:
                cursor = cout
            if cursor >= end - tolerance:
                return True
        return cursor >= end - tolerance

    def _hold_gap(start: float, end: float, what: str) -> None:
        if _v2_covers(start, end) or _declared(start, end):
            return
        tiling_check["pass"] = False
        tiling_check["issues"].append(
            f"V1 {what} of {end - start:.3f}s ({start:.3f}s to "
            f"{end:.3f}s) shows no A-roll and no B-roll covers it - "
            f"no spine block declares an intentional black beat "
            f"covering it"
        )

    first_in = v1[0].get("timeline_in", 0.0) or 0.0
    if first_in > tolerance:
        _hold_gap(0.0, first_in, "starts late, leaving a leading gap")
    for prev, curr in zip(v1, v1[1:]):
        prev_out = prev.get("timeline_out", 0.0) or 0.0
        curr_in = curr.get("timeline_in", 0.0) or 0.0
        gap = curr_in - prev_out
        if gap > tolerance:
            _hold_gap(
                prev_out, curr_in,
                f"gap between '{prev.get('label', '?')}' (ends "
                f"{prev_out:.3f}s) and '{curr.get('label', '?')}' "
                f"(starts {curr_in:.3f}s)")
        elif gap < -tolerance:
            tiling_check["pass"] = False
            tiling_check["issues"].append(
                f"V1 overlap of {-gap:.3f}s: "
                f"'{curr.get('label', '?')}' (starts {curr_in:.3f}s) "
                f"begins before '{prev.get('label', '?')}' ends "
                f"({prev_out:.3f}s)"
            )

    return {"transitions_at_seams": seams_check,
            "v1_tiling": tiling_check}


def build_watch_frames(video_path: str, project_folder: str) -> str:
    """Draw the strips the LLM half WATCHES, and map them for the prompt.

    The deterministic half draws them because drawing is measurement,
    not judgement - the same split `step_3_03_review_rough_cut` makes
    with `roughcut_window_frames`. The narrative half (handoff.md) is
    what reads them.

    This step's handoff opened "You are watching the RENDERED video"
    while its three inputs were a manifest, a render report and a
    folder: it was asked "would I post this" and shown a table. This is
    the frames half of ending that, and the handoff now states which of
    the two it got.

    Returns "" when nothing could be drawn. `validate_output` records
    that as `watched: false` rather than as a silent pass - see
    library/tools/render_watch.py.
    """
    if not project_folder:
        print("  No project_folder: no watch frames drawn", file=sys.stderr)
        return ""
    directory = str(ProjectLayout(project_folder).write_dir(
        Area.QA_FRAMES, step="validate"))
    # No duration is passed: the strips are of what was RENDERED, so
    # the length comes off the file. A span planned past the end of the
    # file is a strip of nothing.
    drawn = render_watch.draw_watch_strips(
        video_path, directory, label="render")
    print(f"  {len(drawn['rows'])} watch strip(s) at {directory}"
          + (f"; {len(drawn['missing'])} span(s) not drawn"
             if drawn["missing"] else ""), file=sys.stderr)
    if not drawn["rows"]:
        return ""
    return render_watch.build_watch_block(
        drawn["directory"], drawn["rows"], drawn["missing"],
        subject="the rendered video this step is validating",
        duration=drawn["duration"])


def validate_output(rendered_output: dict, assembly_manifest: dict,
                    project_folder: str = "") -> dict:
    """Run automated validation checks on the rendered video using render_qa."""
    video_path = rendered_output.get('output_path', '')
    project_settings = assembly_manifest.get('project', {})
    expected_fps = project_settings.get('frame_rate', 30)
    # The frame the render was built at is DECLARED by the manifest
    # (`compile_manifest` writes `resolve_delivery_format` there). A
    # `.get('resolution', [1080, 1920])` would read a missing
    # declaration as vertical instead of failing (AGENTS.md 10.1: a
    # promised key that is missing fails loudly) - and grading a
    # render against a guessed frame is the incident this gate
    # exists for.
    expected_resolution = project_settings.get('resolution')
    if not expected_resolution or len(list(expected_resolution)) < 2:
        raise ValueError(
            "assembly_manifest['project'] declares no 'resolution' - "
            "the frame the render was built at. compile_manifest "
            "writes the delivery format there; a manifest without one "
            "cannot be validated."
        )
    expected_duration = project_settings.get('duration_seconds', 0)

    checks = {}
    
    # ── Check 1: File existence and size ──
    file_check = {"pass": False, "issues": []}
    if not video_path or not os.path.exists(video_path):
        file_check["issues"].append(f"Rendered file not found: {video_path}")
    else:
        size_bytes = os.path.getsize(video_path)
        size_mb = size_bytes / (1024 * 1024)
        file_check["size_mb"] = round(size_mb, 2)

        if size_bytes < 100_000:
            file_check["issues"].append(f"File suspiciously small: {size_mb:.2f} MB")
        else:
            file_check["pass"] = True

    checks["file_exists"] = file_check

    if not file_check["pass"]:
        return {
            "status": "fail",
            "checks": checks,
            "distribution_ready": False,
            "summary": "Rendered file missing or empty",
        }

    # ── Plan geometry: transitions at their seams, V1 tiled ──
    #
    # Plain arithmetic over tracks + transitions, held here so the
    # manifest rows that used to carry it can leave the prompt. It runs
    # before the QA toolkit on purpose: it needs no render, only the
    # plan, so a broken plan fails fast rather than after every
    # measurement.
    geometry = _plan_geometry_checks(assembly_manifest)

    # ── Run QA Toolkit ──
    qa_results = []
    music_path, music_automation, music_offset = _music_bed(assembly_manifest)
    audio_mix = assembly_manifest.get("audio_mix", {}) or {}
    try:
        qa_results = run_full_render_qa(
            video_path, expected_duration,
            target_lufs=float(audio_mix.get(
                "delivery_lufs_target", DEFAULT_LUFS_TARGET)),
            true_peak_ceiling=float(audio_mix.get(
                "delivery_true_peak_ceiling_dbtp",
                DEFAULT_TRUE_PEAK_CEILING_DBTP)),
            declared_black_beats=_declared_black_beats(assembly_manifest),
            # The delivery format the manifest was compiled at. These two
            # were computed above and then never passed, so the gate
            # judged every render against a hardcoded 1080x1920/30fps.
            expected_resolution=expected_resolution,
            expected_fps=expected_fps,
            framing_spans=_framing_spans(assembly_manifest),
            # No chroma floor is passed, deliberately: the value is an
            # open captain decision and P2 reports its number until one
            # exists. See render_qa.CHROMA_PRESENCE_GATES.
            music_path=music_path,
            music_automation=music_automation,
            music_offset_seconds=music_offset,
            spine_blocks=assembly_manifest.get("_spine_blocks") or [],
            # What was drawn over the picture, so P1 masks the pixels the
            # overlays really touched rather than the strips a centred
            # caption was assumed to leave alone.
            overlay_segments=_overlay_segments(assembly_manifest),
            # The per-clip grades, so P11 judges each graded span's
            # exported pixels against the source they were cut from
            # (finding 28 - SetCDL's return is not the verdict).
            grade_spans=_grade_spans(assembly_manifest),
        )
    except Exception as e:
        print(f"Error running render_qa: {e}", file=sys.stderr)
        traceback.print_exc()
        # A QA toolkit that did not run must not read as a passing one:
        # every individual measurement fails closed on its own error, so
        # the aggregate does the same. The render is UNVALIDATED, and the
        # verdict says so instead of reporting pass with zero measurements.
        qa_results = [RenderQAResult(
            "render_qa", False, str(e), None, "error",
            f"Render QA did not run ({type(e).__name__}: {e}) - no "
            f"automated check measured this render")]

    # Subtitle QA
    subtitles = assembly_manifest.get("subtitles", [])
    if subtitles:
        try:
            # The spine is what tells the gap check that a stretch with
            # no caption on it is a beat the plan wrote rather than dead
            # caption time. See library/tools/subtitle_qa.py.
            qa_results.extend(verify_subtitle_timing(
                subtitles,
                spine_blocks=assembly_manifest.get("_spine_blocks") or []))
        except Exception as e:
            print(f"Error running subtitle_qa: {e}", file=sys.stderr)
            traceback.print_exc()
            # Same fail-closed shape as the render_qa path above: a
            # subtitle pass that did not run must not read as a clean
            # one. The verdict says UNVALIDATED instead of reporting
            # pass with zero subtitle measurements.
            qa_results.append(RenderQAResult(
                "subtitle_qa", False, str(e), None, "error",
                f"Subtitle QA did not run ({type(e).__name__}: {e}) - no "
                f"subtitle check measured this render"))
    # Process QA Results into existing checks format for compatibility
    tech_check = {"pass": True, "issues": []}
    framing_check = {"pass": True, "issues": []}
    duration_check = {"pass": True, "issues": []}
    black_frame_check = {"pass": True, "issues": []}
    audio_check = {"pass": True, "issues": []}
    # Reporting-only (see the aggregate below): every subtitle metric
    # lands here so a failing caption reads in the verdict instead of
    # passing silently beside it.
    subtitle_check = {"pass": True, "issues": []}
    # Reporting-only, the same shape: every grade metric lands here so
    # a span whose exported pixels contradict its grade (finding 28)
    # reads in the verdict instead of passing silently beside it.
    # Promoting it to the gate is a captain's call, not a change here.
    grade_check = {"pass": True, "issues": []}
    
    qa_report = []
    
    for r in qa_results:
        # Convert dataclass to dict
        r_dict = {
            "metric": r.metric,
            "passed": r.passed,
            "value": r.value,
            "threshold": r.threshold,
            "severity": r.severity,
            "detail": r.detail
        }
        qa_report.append(r_dict)
        
        # Map to legacy checks
        if r.metric in ["resolution", "framerate"]:
            if not r.passed:
                tech_check["pass"] = False
                tech_check["issues"].append(r.detail)
        elif r.metric == "duration":
            if not r.passed:
                duration_check["pass"] = False
                duration_check["issues"].append(r.detail)
        elif r.metric == "black_frames":
            if not r.passed:
                black_frame_check["pass"] = False
                black_frame_check["issues"].append(r.detail)
        elif r.metric == "frame_occupancy":
            # P1 gates. The picture filling the delivery frame and keeping
            # one geometry is the largest visible defect project 001
            # shipped, and nothing in this pipeline looked at it.
            if not r.passed:
                framing_check["pass"] = False
                framing_check["issues"].append(r.detail)
        elif r.metric == "face_intact":
            # A face the render still shows must not be cut by the frame
            # edge. It gates through the same check as P1 because it is
            # the same question - what the conform did to the picture -
            # and a reviewer reading "framing" wants both answers there.
            # The verdict on a conform too tight to leave a detectable
            # face at all is carried by manifest_validator's P8, before
            # the render.
            if not r.passed:
                framing_check["pass"] = False
                framing_check["issues"].append(r.detail)
        elif r.metric in ("chroma_presence", "speech_above_bed"):
            # P2 and P3 report and do not gate - their thresholds are open
            # captain decisions. The number is in qa_report either way,
            # which is the point: promoting them is a boolean in render_qa,
            # not a change here.
            pass
        elif r.metric in ["audio_streams", "lufs"]:
            if not r.passed:
                audio_check["pass"] = False
                audio_check["issues"].append(r.detail)
        elif r.metric in ("subtitle_overflow", "subtitle_overlap",
                          "subtitle_too_short", "subtitle_too_long",
                          "subtitle_gaps", "subtitle_read_speed"):
            # The six subtitle metrics `verify_subtitle_timing` returns.
            # REPORTED, not gated: whether one should FAIL a build, and
            # at what threshold, is the captain's call, so a failing
            # caption is visible in checks/all_issues/qa_report without
            # moving status or distribution_ready. Promoting one is a
            # boolean here, not a change in subtitle_qa.
            if not r.passed:
                subtitle_check["pass"] = False
                subtitle_check["issues"].append(r.detail)
        elif r.metric == "grade_delivery":
            # P11 REPORTED, not gated, the same shape: a span whose
            # pixels contradict its grade is visible in
            # checks/all_issues/qa_report without moving status or
            # distribution_ready.
            if not r.passed:
                grade_check["pass"] = False
                grade_check["issues"].append(r.detail)
        elif r.metric in ("render_qa", "subtitle_qa"):
            # The toolkit itself failed - fail the technical check so the
            # verdict and distribution_ready reflect an unvalidated render
            # rather than a measured one. Both producers fail closed the
            # same way; a subtitle pass that never ran is UNVALIDATED,
            # not clean.
            if not r.passed:
                tech_check["pass"] = False
                tech_check["issues"].append(r.detail)
                
    checks["technical"] = tech_check
    checks["framing"] = framing_check
    checks["duration"] = duration_check
    checks["black_frames"] = black_frame_check
    checks["audio_levels"] = audio_check
    checks["subtitles"] = subtitle_check
    checks["grades"] = grade_check
    checks["transitions_at_seams"] = geometry["transitions_at_seams"]
    checks["v1_tiling"] = geometry["v1_tiling"]

    # ── Aggregate result ──
    # `subtitles` and `grades` are deliberately outside the gate: their
    # issues reach the verdict through checks/all_issues/qa_report, but
    # status and distribution_ready move only on the checks above. The
    # set below is exactly the keys the old `all(checks.values())`
    # read, plus the two plan-geometry checks this step now holds so
    # the prompt no longer has to.
    gating = ("file_exists", "technical", "framing", "duration",
              "black_frames", "audio_levels", "transitions_at_seams",
              "v1_tiling")
    all_passed = all(checks.get(k, {}).get("pass", False) for k in gating)
    critical_passed = all(
        checks.get(k, {}).get("pass", False)
        for k in ["file_exists", "technical", "framing", "black_frames"]
    )

    all_issues = []
    for name, check in checks.items():
        for issue in check.get("issues", []):
            all_issues.append(f"[{name}] {issue}")

    # Write QA report JSON.
    #
    # exports/ by name, not `dirname(video_path)`. The report belongs
    # with the deliverable it judges, and exports/ is one of the two
    # areas two steps legitimately share - 6.01 writes the render, 6.02
    # writes this. See library/tools/project_layout.py.
    if project_folder:
        qa_report_path = str(ProjectLayout(project_folder).write_path(
            Area.EXPORTS, "qa_report.json", step="validate"))
    else:
        qa_report_path = os.path.join(os.path.dirname(video_path),
                                      "qa_report.json")
    try:
        with open(qa_report_path, 'w') as f:
            json.dump(qa_report, f, indent=2)
    except Exception as e:
        print(f"Failed to write qa_report.json: {e}", file=sys.stderr)

    return {
        "status": "pass" if all_passed else "fail",
        "checks": checks,
        "all_issues": all_issues,
        "distribution_ready": all_passed,
        "critical_checks_passed": critical_passed,
        "summary": "All automated checks passed" if all_passed else f"{len(all_issues)} issue(s) found",
        "recommended_action": None if all_passed else "Review issues and re-render if critical checks failed",
        "qa_report_path": qa_report_path,
        "qa_report": qa_report
    }

def main():
    input_data = json.loads(sys.stdin.read())

    rendered_output = input_data.get("rendered_output", {})
    assembly_manifest = input_data.get("assembly_manifest", {})

    # Step 6.01 always exports now, so an absent output_path means the
    # export did not happen. Validating the build report instead used to
    # let a run finish "pass" with distribution_ready: false and nobody
    # noticing there was no video.
    project_folder = input_data.get("project_folder", "")
    if rendered_output.get("output_path"):
        result = validate_output(rendered_output, assembly_manifest,
                                 project_folder)
    else:
        result = {
            "status": "fail",
            "mode": "render_validation",
            "checks": {
                "render_output": {
                    "pass": False,
                    "issues": [
                        "Render was expected but output_path is missing. "
                        "The render step may have failed or not been triggered."
                    ],
                },
            },
            "all_issues": [
                "[render_output] Render was expected but output_path is missing"
            ],
            "distribution_ready": False,
            "critical_checks_passed": False,
            "summary": "Render output missing - render step failed or was not triggered",
            "recommended_action": (
                "Check step 6.01 render logs for errors. Ensure DaVinci Resolve "
                "is running and the render completed successfully."
            ),
        }

    # ── The frames the LLM half WATCHES ──
    #
    # Emitted as its own key rather than folded into the verdict: the
    # deterministic half MEASURES and this block is what the narrative
    # half SEES, and the two have different readers. `watched` goes into
    # the verdict so "nobody looked" and "somebody looked and saw
    # nothing" stay different facts (library/tools/render_watch.py).
    out = {"deterministic_validation": result}
    block = ""
    if rendered_output.get("output_path") and os.path.exists(
            rendered_output["output_path"]):
        try:
            block = build_watch_frames(
                rendered_output["output_path"], project_folder)
        except Exception as exc:  # noqa: BLE001 - drawing is best-effort
            print(f"Error drawing watch frames: {exc}", file=sys.stderr)
    if block:
        out["render_watch_frames"] = block
    result["watched"] = bool(block)
    if not block:
        # A watch that did not happen is SAID. It never fails the gate -
        # the picture half reports, it does not refuse (AGENTS.md 10.4) -
        # but a verdict that is silent about having no eyes is the exact
        # thing this step was doing before.
        result.setdefault("all_issues", []).append(
            "[watched] no frames were drawn from the render, so NOTHING "
            "SAW THIS PICTURE - the verdict below is measurements only")

    json.dump(out, sys.stdout, indent=2)

if __name__ == "__main__":
    main()
