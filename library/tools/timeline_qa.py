from dataclasses import dataclass, field
from typing import List, Any
import math
import sys

@dataclass
class QACheck:
    name: str
    passed: bool
    expected: Any
    actual: Any
    severity: str = "error"

@dataclass
class QAReport:
    station: str
    passed: bool
    checks: List[QACheck] = field(default_factory=list)

@dataclass
class VisualQACheck:
    name: str
    passed: bool
    confidence: float
    frame_timecode: str
    model_used: str
    detail: str
    issues: List[str]
    severity: str = "warning"
    # Fields for QACheck compatibility and router integration
    expected: Any = None
    actual: Any = None
    image_path: str = ""

@dataclass
class VisualQAReport:
    station: str
    passed: bool
    qa_type: str = ""
    checks: List[VisualQACheck] = field(default_factory=list)
    # Alias used by qa_feedback_loop
    visual_checks: List[VisualQACheck] = field(default_factory=list)


def verify_clip_placement(timeline, track_items, manifest_clips) -> QAReport:
    """Station 1: After clip placement. Verify each clip is on the correct track at correct position."""
    report = QAReport(station="clip_placement", passed=True)
    
    # manifest_clips could be a list of clips or dict track_name -> clips
    # If track_items is a list of timeline items, we need to match them.
    # Usually we just check the V1 items. Let's assume manifest_clips is the V1 list for now, or a dict.
    # We will iterate through track_items if it's a dict { "V1": [...], "V2": [...] }
    if isinstance(track_items, dict) and isinstance(manifest_clips, dict):
        for track_name, items in track_items.items():
            expected_clips = manifest_clips.get(track_name, [])
            if len(items) != len(expected_clips):
                report.checks.append(QACheck(
                    name=f"{track_name}_clip_count", passed=False,
                    expected=len(expected_clips), actual=len(items)
                ))
                report.passed = False
                
            for i, (item, exp_clip) in enumerate(zip(items, expected_clips)):
                start = item.GetStart()
                exp_start = exp_clip.get("timeline_in_frame")
                if exp_start is not None and start != exp_start:
                    report.checks.append(QACheck(
                        name=f"{track_name}_clip_{i}_start", passed=False,
                        expected=exp_start, actual=start
                    ))
                    report.passed = False
                    
                duration = item.GetDuration()
                exp_end = exp_clip.get("timeline_out_frame")
                if exp_start is not None and exp_end is not None:
                    exp_dur = exp_end - exp_start
                    if duration != exp_dur:
                        report.checks.append(QACheck(
                            name=f"{track_name}_clip_{i}_duration", passed=False,
                            expected=exp_dur, actual=duration
                        ))
                        report.passed = False

    return report

def verify_transitions(timeline, track_items, fusion_transitions) -> QAReport:
    """Station 2: after the Fusion pass. Each transition must be on a clip.

    Takes `fusion_effects.transitions` - the list carrying `after_clip`,
    the index of the OUTGOING V1 clip. A transition is drawn as a tail
    effect on that clip and a head effect on the next one, so both must
    come back with a Fusion composition on them.

    This station used to be a stub that returned `passed=True` without
    looking at anything, which is why zero transitions reaching the video
    went unnoticed for the whole life of the pipeline.
    """
    report = QAReport(station="transitions", passed=True)
    if not fusion_transitions:
        return report

    v1_items = timeline.GetItemListInTrack("video", 1) or []

    def has_comp(index):
        if index >= len(v1_items):
            return None
        return bool(v1_items[index].GetFusionCompNameList())

    for spec in fusion_transitions:
        after_clip = spec.get("after_clip")
        ttype = spec.get("type", "?")
        if after_clip is None:
            report.checks.append(QACheck(
                name=f"transition_{ttype}_after_clip",
                passed=False, expected="a V1 clip index", actual=None,
            ))
            report.passed = False
            continue

        for role, index in (("tail", after_clip), ("head", after_clip + 1)):
            present = has_comp(index)
            if present:
                continue
            report.checks.append(QACheck(
                name=f"transition_{ttype}_{role}_on_v1[{index}]",
                passed=False,
                expected="a Fusion comp on the clip",
                actual="no clip at that index" if present is None else "no comp",
            ))
            report.passed = False

    return report

def verify_color_grades(timeline, track_items, manifest_color) -> QAReport:
    """Station 3: After color grading. Readback CDL values and compare."""
    report = QAReport(station="color_grades", passed=True)
    
    per_clip = manifest_color.get("per_clip_adjustments", [])
    color_lookup = {}
    for adj in per_clip:
        src = adj.get("source_file", "")
        if src:
            import os
            color_lookup[os.path.basename(src)] = adj.get("cdl_values", {})
            
    v1_items = timeline.GetItemListInTrack("video", 1) or []
    for item in v1_items:
        mpi = item.GetMediaPoolItem()
        if not mpi: continue
        clip_name = mpi.GetClipProperty("File Name") or item.GetName()
        cdl_vals = color_lookup.get(clip_name)
        if cdl_vals:
            # We can't actually read back CDL easily via API if it was set via SetCDL dictionary.
            # But we can try GetCDL() as per prompt: "clip.GetCDL() -> compare slope/offset/power against spec"
            try:
                actual_cdl = item.GetCDL()
                if not actual_cdl:
                    # Try GetClipProperty fallback if GetCDL isn't returning dict
                    slope = item.GetClipProperty("Slope")
                    if slope:
                        actual_cdl = {"Slope": slope}
                        
                if actual_cdl and isinstance(actual_cdl, dict):
                    # Compare Slope, Offset, Power
                    for key in ["Slope", "Offset", "Power"]:
                        actual_str = actual_cdl.get(key, "")
                        if not actual_str:
                            continue
                        # e.g., "1.000 1.000 1.000"
                        parts = [float(x) for x in actual_str.split()]
                        if parts:
                            exp_val = cdl_vals.get(f"{key.lower()}_r", 1.0 if key != "Offset" else 0.0)
                            if abs(parts[0] - exp_val) > 0.01:
                                report.checks.append(QACheck(
                                    name=f"{clip_name}_{key.lower()}_r", passed=False,
                                    expected=exp_val, actual=parts[0]
                                ))
                                report.passed = False
            except Exception as e:
                # A readback that raises used to vanish here, and the
                # station reported pass. It cannot fail the render - the
                # grade may well be on the clip and only the readback
                # broke - but it must not be silent either.
                report.checks.append(QACheck(
                    name=f"{clip_name}_cdl_readback", passed=False,
                    expected="a CDL readback", actual=f"{type(e).__name__}: {e}",
                    severity="warning",
                ))

    return report

def verify_audio(timeline, project, manifest_audio) -> QAReport:
    """Station 4: after audio. Check the timeline has audio tracks at all.

    It does NOT check per-track levels, and the docstring used to say it
    did. Nothing in the pipeline sets a per-track level: audio mixing is
    out of scope by ruling and `audio_mix` reaches the timeline as
    markers for a human editor. Do not restore the claim without a
    reader that sets a level.
    """
    report = QAReport(station="audio", passed=True)
    audio_track_count = timeline.GetTrackCount("audio")
    if audio_track_count == 0:
        report.checks.append(QACheck(name="audio_track_count", passed=False, expected=">0", actual=0))
        report.passed = False

    return report

def verify_fusion_comps(timeline, placed_labels_by_track, manifest_fusion_effects) -> QAReport:
    """Station 5: after the Fusion pass. Every clip the plan gave an
    effect must carry a comp.

    This station used to be vacuous: its only loop body was `pass` and it
    returned passed=True whatever the timeline held. It is the station
    that should have caught the collapse the renderer now catches with an
    ad-hoc count next to the transition station - no Fusion comp of any
    kind reaching the picture while the run reported success.

    `manifest_fusion_effects` is the manifest's `fusion_effects`, whose
    `per_clip` is keyed by CLIP LABEL, not by index. So the caller must
    hand over the labels it actually placed, per video track index, in
    timeline order: {1: v1_placed_labels, 2: v2_placed_labels}. Deriving
    a label from the index instead is what made the old body meaningless.

    `GetFusionCompNameList()` is the reliable readback; comp tool loading
    is lazy, so `GetToolList()` can be empty right after an import.
    """
    report = QAReport(station="fusion_comps", passed=True)

    per_clip = (manifest_fusion_effects or {}).get("per_clip", {}) or {}
    if not per_clip:
        return report

    labels_by_track = placed_labels_by_track or {}
    if not labels_by_track:
        report.checks.append(QACheck(
            name="fusion_comps_unverifiable", passed=False,
            expected=f"placed labels for {len(per_clip)} planned effects",
            actual="no labels supplied by the caller",
        ))
        report.passed = False
        return report

    expected = 0
    carried = 0
    for track_index in sorted(labels_by_track):
        labels = labels_by_track[track_index] or []
        items = timeline.GetItemListInTrack("video", track_index) or []
        for i, label in enumerate(labels):
            if label not in per_clip or i >= len(items):
                continue
            expected += 1
            comps = items[i].GetFusionCompNameList() or []
            if comps:
                carried += 1
            else:
                report.checks.append(QACheck(
                    name=f"V{track_index}_clip_{i}_fusion_comp",
                    passed=False, expected=f"a comp on '{label}'", actual="no comp",
                ))
                report.passed = False

    # Every planned effect missing is not N individual misses, it is the
    # Fusion pass never having run. Say so, so it is not read as noise.
    if expected and carried == 0:
        report.checks.append(QACheck(
            name="fusion_comps_all_missing", passed=False,
            expected=f"{expected} clips carrying a Fusion comp", actual=0,
        ))
        report.passed = False

    return report

def run_full_timeline_qa(timeline, project, manifest) -> QAReport:
    """Station 6: Final sweep after all phases."""
    report = QAReport(station="full_sweep", passed=True)
    
    v1_items = timeline.GetItemListInTrack("video", 1) or []
    prev_end = None
    
    for i, item in enumerate(v1_items):
        start = item.GetStart()
        end = item.GetEnd()
        
        # Missing media: any clips with offline status
        # Resolve API: item.GetClipProperty("Offline") or similar, or check MediaPoolItem
        mpi = item.GetMediaPoolItem()
        if mpi:
            status = mpi.GetClipProperty("Status")
            if status == "Offline":
                report.checks.append(QACheck(name=f"clip_{i}_offline", passed=False, expected="Online", actual="Offline"))
                report.passed = False
                
        if prev_end is not None:
            # Gap detection: gaps > 1 frame
            if start - prev_end > 1:
                report.checks.append(QACheck(name=f"gap_before_clip_{i}", passed=False, expected="<=1", actual=start-prev_end))
                report.passed = False
            # Overlap detection: check no two V1 items share frames
            if start < prev_end:
                report.checks.append(QACheck(name=f"overlap_before_clip_{i}", passed=False, expected=">=0", actual=start-prev_end))
                report.passed = False
        prev_end = end
        
    # Total duration within 10% of expected
    if v1_items:
        actual_dur_frames = v1_items[-1].GetEnd()
        fps = manifest.get("project", {}).get("frame_rate", 30)
        actual_dur_sec = actual_dur_frames / fps
        expected_dur_sec = manifest.get("project", {}).get("duration_seconds", 0)
        if expected_dur_sec > 0:
            diff = abs(actual_dur_sec - expected_dur_sec) / expected_dur_sec
            if diff > 0.1:
                report.checks.append(QACheck(name="total_duration", passed=False, expected=expected_dur_sec, actual=actual_dur_sec))
                report.passed = False

    return report
