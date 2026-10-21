"""Identity continuity: does a reference to one person resolve to that
person, across every source angle and every time interval?

**The question this answers.** A person in a two-camera conversation is
MEASURED twice: once per camera, and the measurement of one camera is
the other speaker's voice whenever the other speaker is talking. A
reference to the speaker must resolve to the same person on both
cameras - and a reference must never resolve to the person who merely
happened to be on screen.

**The measurement.** A MANIFEST labels the corpus the way a person who
watched it would:

    {"sources": [
      {"content_digest": "<64 hex>",
       "person": "Craig",                     # who is ON SCREEN here
       "voices": {"voice_001": "Craig",       # who is SPEAKING here
                  "voice_002": "uncertain"}}]}

`person` is the camera's own person (a fact about the footage, not a
measurement of it). `voices` labels each diarized voice track by its
speaker, and `uncertain` marks a track whose identity the measurement
does not establish - a fragment that matches nobody above the measured
threshold. The labels are the ground truth; the engine's roster is what
is judged against them.

**What it reports**, always with its denominator:

- `same_speaker_resolution`: for each speaker, how much of their
  MEASURED speech the roster attributes to their own person - the person
  whose FACE the roster resolved on the cameras where they are on
  screen - and whether their identity splits across people or angles.
- `false_merges`: roster persons that carry more than one speaker's
  speech - the two people merged into one.
- `unresolved`: the uncertain tracks the roster LEFT alone, and the ones
  it force-attached anyway.

    python3 -m library.tools.identity_continuity study <project> \\
        --manifest <manifest.json> [--out report.json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

try:
    from library.tools import footage_identity, person_entity, source_memory
except ImportError:  # imported as `tools.*` from inside library/
    from tools import footage_identity, person_entity, source_memory

UNCERTAIN = "uncertain"
"""The manifest's label for a voice track whose speaker the measurement
does not establish."""


def _digest_of(source_file: str) -> Optional[str]:
    try:
        return footage_identity.fingerprint(source_file)["content_digest"]
    except OSError:
        return None


def load_manifest(manifest: dict) -> Dict[str, dict]:
    """`{content_digest: {"person": ..., "voices": {...}}}`, by digest.

    A manifest may name a source by `content_digest` or by
    `source_file`; the file is fingerprinted here (its two ends and its
    size - not a full read).
    """
    out: Dict[str, dict] = {}
    for entry in manifest.get("sources") or []:
        if not isinstance(entry, dict):
            continue
        digest = entry.get("content_digest")
        if not digest:
            source_file = entry.get("source_file") or ""
            digest = _digest_of(source_file) if source_file else None
        if not digest:
            continue
        voices = entry.get("voices") or {}
        out[digest] = {
            "person": entry.get("person"),
            "voices": {str(k): str(v) for k, v in voices.items()
                       if isinstance(v, str)},
            "source_file": entry.get("source_file"),
        }
    return out


def _spans_of(voice: Optional[dict]) -> List[tuple]:
    return [(float(start), float(end))
            for start, end in (voice or {}).get("spans") or []]


def measure_identity_continuity(roster: dict,
                                manifest: Dict[str, dict],
                                root=None) -> dict:
    """Judge a person roster against the corpus's own labels.

    `roster` is `person_entity.resolve_person_tracks`'s account;
    `manifest` is `load_manifest`'s digest-keyed labels.
    """
    persons = roster.get("persons") or []
    owners_of_span: Dict[tuple, List[str]] = {}
    for person in persons:
        person_id = person.get("person_id")
        for span in person.get("voice_spans") or []:
            if not isinstance(span, dict):
                continue
            key = (span.get("content_digest"), float(span["start"]),
                   float(span["end"]))
            owners_of_span.setdefault(key, []).append(person_id)

    # The roster person whose FACE the corpus says belongs to each
    # speaker: the camera's own person, on the cameras where they are
    # on screen. This is what "their own person" means for the report.
    face_person: Dict[str, Dict[str, int]] = {}
    for person in persons:
        person_id = person.get("person_id")
        for track in person.get("face_tracks") or []:
            digest = track.get("content_digest")
            labels = manifest.get(digest) or {}
            speaker = labels.get("person")
            if not speaker:
                continue
            face_person.setdefault(speaker, {})
            face_person[speaker][person_id] = \
                face_person[speaker].get(person_id, 0) + 1
    own_person = {speaker: max(sorted(counts.items()),
                               key=lambda row: row[1])[0]
                  for speaker, counts in face_person.items() if counts}

    speakers: Dict[str, dict] = {}
    false_merge_rows: Dict[str, Dict[str, Any]] = {}
    uncertain_attached: List[dict] = []
    uncertain_unresolved: List[dict] = []
    labelled_tracks = 0

    for digest, labels in sorted(manifest.items()):
        identity = person_entity.read_identity(digest, root)
        voices = {v.get("track_id"): v
                  for v in (identity or {}).get("voices") or []}
        for track_id, speaker in sorted(labels["voices"].items()):
            labelled_tracks += 1
            voice = voices.get(track_id)
            spans = _spans_of(voice)
            seconds = sum(end - start for start, end in spans)
            owners = sorted({owner
                             for span in spans
                             for owner in owners_of_span.get(
                                 (digest, span[0], span[1]), [])})
            if speaker == UNCERTAIN:
                row = {"content_digest": digest, "voice_track": track_id,
                       "attached_to": owners}
                (uncertain_attached if owners else uncertain_unresolved).append(row)
                continue
            row = speakers.setdefault(speaker, {
                "voice_tracks_labelled": 0,
                "voice_tracks_on_own_person": 0,
                "voice_seconds_labelled": 0.0,
                "voice_seconds_on_own_person": 0.0,
                "voice_tracks_unowned": 0,
                "person_ids": [],
                "own_person": own_person.get(speaker),
                "sources_heard_on": [],
            })
            row["voice_tracks_labelled"] += 1
            row["voice_seconds_labelled"] += seconds
            if labels.get("person") and labels["person"] not in row["sources_heard_on"]:
                row["sources_heard_on"].append(labels["person"])
            for owner in owners:
                if owner not in row["person_ids"]:
                    row["person_ids"].append(owner)
            if not owners:
                row["voice_tracks_unowned"] += 1
                continue
            mine = row["own_person"]
            if mine and mine in owners:
                row["voice_tracks_on_own_person"] += 1
                row["voice_seconds_on_own_person"] += seconds
            for owner in owners:
                held = false_merge_rows.setdefault(
                    owner, {"speakers": set(), "voice_seconds": 0.0})
                held["speakers"].add(speaker)
                held["voice_seconds"] += seconds

    merged = {person_id: {"speakers": sorted(row["speakers"]),
                          "voice_seconds": round(row["voice_seconds"], 3)}
              for person_id, row in false_merge_rows.items()
              if len(row["speakers"]) > 1}

    return {
        "same_speaker_resolution": {
            speaker: {
                "own_person": row["own_person"],
                "voice_tracks_on_own_person": row["voice_tracks_on_own_person"],
                "voice_tracks_labelled": row["voice_tracks_labelled"],
                "voice_seconds_on_own_person": round(
                    row["voice_seconds_on_own_person"], 3),
                "voice_seconds_labelled": round(row["voice_seconds_labelled"], 3),
                "voice_tracks_unowned": row["voice_tracks_unowned"],
                "person_ids_holding_this_speaker": row["person_ids"],
                "identity_splits_across_persons": len(row["person_ids"]) > 1,
                "sources_heard_on": row["sources_heard_on"],
            }
            for speaker, row in sorted(speakers.items())},
        "false_merges": {
            "persons_holding_two_speakers": merged,
            "count": len(merged),
            "denominator_persons": len(persons),
        },
        "unresolved": {
            "uncertain_labelled": len(uncertain_attached) + len(uncertain_unresolved),
            "left_unresolved": uncertain_unresolved,
            "force_attached": uncertain_attached,
        },
        "face_identity_splits": {
            speaker: sorted(set(owners))
            for speaker, owners in face_person.items()
            if len(set(owners)) > 1},
        "denominators": {
            "roster_persons": len(persons),
            "labelled_sources": len(manifest),
            "labelled_voice_tracks": labelled_tracks,
        },
    }


def run_study(project_folder: str, manifest: dict,
              root=None) -> dict:
    """Measure the roster of one project against a labelled corpus."""
    labels = load_manifest(manifest)
    if root is None:
        # The same place the project's own reader looks: this project's
        # private scope when it has one, the machine store when it does
        # not.
        root = source_memory.project_read_root(project_folder)
    roster = person_entity.resolve_person_tracks(project_folder, root)
    report = measure_identity_continuity(roster, labels, root)
    report["project_folder"] = os.path.abspath(project_folder)
    return report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="identity_continuity",
        description="Measure whether a reference to one person resolves to "
                    "that person across every source angle.")
    parser.add_argument("project", help="the project folder")
    parser.add_argument("--manifest", required=True,
                        help="the labelled corpus (JSON)")
    parser.add_argument("--out", default=None,
                        help="write the report here as well")
    args = parser.parse_args(argv)

    with open(args.manifest, encoding="utf-8") as handle:
        manifest = json.load(handle)
    report = run_study(args.project, manifest)
    print(json.dumps(report, indent=2))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
