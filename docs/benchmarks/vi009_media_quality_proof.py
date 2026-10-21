#!/usr/bin/env python3
"""VI-009 proof: measure two cameras of the podcast and compare them.

This is the scratch-copy proof for the technical leg. It measures the
two camera sources of the podcast episode (read-only), files the
measurements in the SCRATCH project's own source memory, and reports
the technical leg of one line as each camera recorded it.

The media is the captain's and is never written to. The measurements
land in the scratch project's private memory, not in the machine store
and not in the captain's project.

    python3 docs/benchmarks/vi009_media_quality_proof.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from library.tools import audio_quality, footage_identity, media_quality
from library.tools import source_memory
from library.tools.analysis import picture_quality
from library.tools.heavy_work_lock import heavy_work_lock

MEDIA_ROOT = os.path.expanduser(
    "~/Documents/work_stuff/Lucie consulting/Social Media/podcast media")
SCRATCH_PROJECT = os.path.expanduser(
    "~/Documents/content_stuff/video_projects/fm-vi009-scratch")

#: The two cameras of one conversation, as the sources' own clock group
#: records them (M6): LC4930.MXF is the group's reference and
#: LCATL0011.MXF is the other angle of the same room. Their timelines
#: are aligned by the clock, not by a guess.
CAMERAS = ("LC4930.MXF", "LCATL0011.MXF")


def measure_source(path: str, root) -> dict:
    """Measure one source: its picture track and its audio quality."""
    digest = footage_identity.fingerprint(path)["content_digest"]
    with heavy_work_lock("vep-vi009", profile="temporal.index:image_defects"):
        picture = picture_quality.persist_source_track(path, root=root)
    with heavy_work_lock("vep-vi009", profile="temporal.index:image_defects"):
        audio = audio_quality.measure_audio_quality(path, root=root)
    return {
        "content_digest": digest,
        "picture_samples": len((picture or {}).get("measurements") or []),
        "picture_method": (picture or {}).get("method"),
        "audio_channels": len((audio or {}).get("channels") or []),
        "audio_method": (audio or {}).get("method"),
    }


def same_line(first: str, second: str) -> tuple:
    """One line both cameras recorded, and each camera's own span for it.

    The span comes from the sources' own clock group (M6): the second
    camera's offset to the reference is its own recorded number. The
    transcripts confirm it - the line's words are the same in both.
    """
    def utterances(digest):
        doc, _status = source_memory.read_m1(
            digest, root=source_memory.machine_memory_root())
        return (doc or {}).get("utterances") or []

    def clock(digest):
        path = (source_memory.machine_memory_root() / digest /
                source_memory.SLOT_CLOCK)
        try:
            with open(path, encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return {}

    second_clock = clock(second)
    offset = float(second_clock.get("offset_to_reference_seconds") or 0.0)
    first_utterances = utterances(first)
    second_utterances = utterances(second)

    def words_of(utterance):
        return " ".join(
            str(word.get("word", "")).strip().lower()
            for word in utterance.get("words") or [])

    for utterance in first_utterances:
        text = words_of(utterance)
        if len(text.split()) < 6:
            continue
        for other in second_utterances:
            other_text = words_of(other)
            if text != other_text and not (len(text) > 30 and text in other_text):
                continue
            shifted = dict(other)
            shifted["start"] = other["start"] - offset
            shifted["end"] = other["end"] - offset
            return (utterance, shifted, offset)
    return (None, None, offset)


def _v3_document(camera, path, duration, line):
    """A v3-shaped semantic document, so the candidate table has a row."""
    return {
        "clip_id": camera,
        "file_path": path,
        "duration_s": duration,
        "fps": 23.976,
        "resolution": [3840, 2160],
        "scene": [
            {"start": 0.0, "end": duration,
             "location": "A room with two people talking",
             "type": "indoor", "lighting": "Practical",
             "notable_features": ["A table", "Two chairs"]},
        ],
        "camera": [{"start": 0, "end": duration, "mode": "handheld",
                    "framing": "medium", "stability": "stable",
                    "movement": "stationary"}],
        "actions": [
            {"window": [line["start"], line["end"]],
             "actions": [
                 {"start": line["start"], "end": line["end"],
                  "action": "The two people talk across the table.",
                  "speech_cue": None,
                  "body_language": "Neutral, attentive."},
             ]},
        ],
        "objects": [
            {"label": "a person in a dark shirt", "role": "primary_subject",
             "category": "person",
             "appearances": [[line["start"], line["end"]]],
             "readable_text": None},
        ],
        "assessment": {
            "content_type": "person_talking_to_camera",
            "camera_stability": "stable",
            "usable_ranges": [[line["start"], line["end"]]],
            "usable_ranges_method": "deterministic_v1",
            "primary_subject_visible": [[line["start"], line["end"]]],
        },
    }


def run_bridge(catalog, documents, line):
    """The candidate table the planner reads, built by the real bridge."""
    import subprocess

    bridge = (Path(__file__).resolve().parents[2] / "library" / "steps" /
              "step_3_02_select_broll" / "bridge.py")
    payload = {
        "clip_catalog": catalog,
        "a_roll_assignments": [
            {"segment_id": "block_1", "spine_block_position": 1,
             "video_segments": [{"clip_id": catalog[0]["clip_id"]}]},
        ],
        "semantic_analysis_documents": documents,
        "timed_spine": {"structure": [
            {"position": 1, "block_type": "transition_slot",
             "timeline_start": 0.0, "timeline_end": 3.03}]},
        "project_folder": SCRATCH_PROJECT,
    }
    env = dict(os.environ)
    env["REN_EVENT_LOG"] = str(
        Path(SCRATCH_PROJECT) / "pipeline_output" / "logs" / "event_log.jsonl")
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    proc = subprocess.run([sys.executable, str(bridge)],
                          input=json.dumps(payload), capture_output=True,
                          text=True, encoding="utf-8", env=env)
    if proc.returncode != 0:
        print(proc.stdout + proc.stderr)
        return None
    return json.loads(proc.stdout)


def main() -> int:
    root = source_memory.project_memory_root(SCRATCH_PROJECT)
    root.mkdir(parents=True, exist_ok=True)
    print(f"Scratch project: {SCRATCH_PROJECT}")
    print(f"Measurements land in: {root}\n")

    digests = {}
    for camera in CAMERAS:
        path = os.path.join(MEDIA_ROOT, camera)
        print(f"Measuring {camera} ...", flush=True)
        report = measure_source(path, root)
        digests[camera] = report["content_digest"]
        print(f"  {json.dumps(report)}\n", flush=True)

    first, second = CAMERAS
    line_a, line_b, offset = same_line(digests[first], digests[second])
    if line_a is None:
        print("No shared line found in the two transcripts.")
        return 1
    print(f"Line: {' '.join(str(w.get('word')) for w in line_a.get('words') or [])}")
    print(f"Clock offset to the reference: {offset:+.2f}s\n")

    legs = {}
    for camera, line in ((first, line_a), (second, line_b)):
        path = os.path.join(MEDIA_ROOT, camera)
        leg = media_quality.technical_leg_for_clip(
            SCRATCH_PROJECT,
            {"clip_id": camera, "source_file": path,
             "duration_seconds": line["end"]},
            start_seconds=line["start"], end_seconds=line["end"],
            root=root)
        legs[camera] = leg
        print(f"--- {camera}  ({line['start']:.2f}-{line['end']:.2f}s)")
        print(media_quality.render_leg(leg))
        print()

    comparison = media_quality.compare_legs(
        legs[first], legs[second],
        left_label=first, right_label=second,
        editorial_preference="right")
    print("--- Technical difference")
    print(media_quality.render_comparison(comparison))
    print()
    print(json.dumps({
        "editorial_preference": comparison["editorial_preference"],
        "editorial_preference_side": second,
        "editorial_selection_unchanged":
            comparison["editorial_selection_unchanged"],
        "differences": [
            {k: difference[k] for k in
             ("indicator", "left", "right", "unit", "delta")}
            for difference in comparison["differences"]],
    }, indent=2))

    # The planner's own surface: the candidate table the model reads,
    # with the technical leg of each candidate in it.
    catalog = []
    documents = []
    for camera, line in ((first, line_a), (second, line_b)):
        path = os.path.join(MEDIA_ROOT, camera)
        catalog.append({"clip_id": camera, "path": path,
                        "source_file": path,
                        "duration_seconds": line["end"] + 1.0})
        documents.append(_v3_document(camera, path, line["end"] + 1.0, line))
    result = run_bridge(catalog, documents, line_a)
    if not result:
        print("The candidate table could not be built.")
        return 1
    print("\n--- The candidate table the planner reads")
    for line_text in result["broll_candidates_toon"].splitlines():
        print(line_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
