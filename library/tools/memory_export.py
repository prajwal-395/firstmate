"""`ren export-memory`: the versioned, path-portable export of a footage memory.

The separable-product report (item 3) asked for an output a consumer can
read without this machine: a stable asset id, the file fingerprint, the
measured fields and their time ranges, which model or tool measured each,
coverage and confidence, and per-asset status with the failure reason -
with original absolute paths kept LOCAL-ONLY.

    ren export-memory <project-or-collection> [--out FILE]
        [--include-embeddings] [--include-frame-measurements]

Two files land in `pipeline_output/footage_memory/` (`Area.FOOTAGE_MEMORY`):

* `footage_memory.v<SCHEMA_VERSION>.json` - portable. Every asset is keyed
  by `asset_id`, the content digest `footage_identity.fingerprint` already
  computes (`sha256(size, first MiB, last MiB)`): the same bytes get the
  same id on any machine, under any name or folder. Times are seconds from
  the start of the source file. The only name an asset carries is its
  basename, as a display label.
* `footage_memory.local.json` - local-only: asset id to the absolute paths
  it was observed at, the memory directory, the project folder. It exists
  so this machine can map an export back onto its files; it is never part
  of what is shared.

**Portability is CHECKED, not hoped for.** `assert_portable` walks every
string of the export before it is written and refuses (raises
`PathLeak`) if any contains a local path this export knows about: a
source path or its folder, the project folder, the memory root, or the
home directory. A record that grows a new path-bearing field fails the
export the first time it is exported, rather than leaking quietly.

**Nothing absent is exported as measured.** A memory slot no lane wrote is
`{"status": "absent"}` with no fields; a slot whose record no longer
matches the file is `stale` and its fields are withheld; a lane the last
`ren analyze` run recorded as failed carries that run's reason. The
transcript's per-utterance `confidence` is 0.0 in every record because no
transcriber arm publishes one (docs/SOURCE_MEMORY.md, M1), so it is
exported as `"confidence": null` with that reason, never as 0.0.

The export is a READER of the memory and of `analysis_run.json`. It
builds nothing, takes no lock, and never touches the media beyond the
fingerprint read that decides whether a record is still current.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

from library.tools import footage_analysis, footage_identity, source_memory
from library.tools.project_layout import Area, ProjectLayout
from library.tools.ren_refusal import REFUSAL_EXIT_CODE, RenRefusal

SCHEMA = "ren.footage-memory"
SCHEMA_VERSION = 1
LOCAL_FILE = "footage_memory.local.json"

FINGERPRINT_METHOD = (
    "sha256 over the decimal size, the first 1 MiB and the last 1 MiB "
    "(library/tools/footage_identity.py fingerprint)")
TIME_BASE = "seconds from the start of the source file"
COORDINATES = "image-normalised, top-left origin, x right, y down"

# Which module writes each memory slot - the provenance a reader needs to
# know which code to ask about a number.
WRITERS = {
    source_memory.SLOT_SOURCE: "library/tools/source_memory.py (M0)",
    source_memory.SLOT_TRANSCRIPT: "library/tools/source_memory.py (M1)",
    source_memory.SLOT_FRAMES_INDEX: "library/tools/source_memory.py (M2)",
    source_memory.SLOT_PERSONS: "library/tools/person_measurements.py (M3)",
    source_memory.SLOT_IDENTITY: "library/tools/person_entity.py (M3b)",
    source_memory.SLOT_EXPRESSIONS: "library/tools/expression_classifier.py (M3c)",
    source_memory.SLOT_CLOCK: "library/tools/conversation_clock.py (M6)",
    source_memory.SLOT_EVENTS: "library/tools/event_spans.py (M7)",
    source_memory.SLOT_VERDICTS: "library/tools/span_verification.py (M8)",
}

# Lane name (footage_analysis.LANES) behind each exported section.
SECTION_LANE = {
    "media": "transcript", "transcript": "transcript", "frames": "frames",
    "persons": "persons", "identity": "identity", "expressions": "expressions",
    "clock": "clock", "events": "events",
}


class PathLeak(ValueError):
    """An export string carries a local path. The export is not written."""


def export_path(project: str) -> Path:
    return ProjectLayout(project).read_path(
        Area.FOOTAGE_MEMORY, f"footage_memory.v{SCHEMA_VERSION}.json")


# ── Reading the memory ──────────────────────────────────────────────


def _load(digest: str, slot: str, root: Optional[Path]) -> Optional[dict]:
    try:
        with open(source_memory.source_dir(digest, root) / slot,
                  encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _built_at(digest: str, slot: str, root: Optional[Path]) -> Optional[str]:
    try:
        mtime = (source_memory.source_dir(digest, root) / slot).stat().st_mtime
    except OSError:
        return None
    return _dt.datetime.fromtimestamp(mtime, _dt.timezone.utc).isoformat(
        timespec="seconds")


def _provenance(digest: str, slot: str, root: Optional[Path],
                instrument=None, **extra) -> dict:
    out = {"written_by": WRITERS[slot],
           "built_at": _built_at(digest, slot, root)}
    if instrument is not None:
        out["instrument"] = instrument
    out.update(extra)
    return out


def _current(doc: Optional[dict], digest: str, size: Optional[int]) -> str:
    """`present`, `stale` or `absent` for one record of one digest."""
    if doc is None:
        return "absent"
    if doc.get("content_digest") != digest:
        return "stale"
    if size is not None and doc.get("size_bytes") not in (None, size):
        return "stale"
    return "present"


def _r(value, places=3):
    return None if value is None else round(float(value), places)


# ── One section per slot ────────────────────────────────────────────


def _media(digest, root, size):
    m0 = _load(digest, source_memory.SLOT_SOURCE, root)
    state = _current(m0, digest, size)
    if state != "present":
        return {"status": state}
    return {
        "status": "present",
        "duration_seconds": m0.get("duration_seconds"),
        "video_streams": m0.get("video_streams", []),
        "audio_streams": m0.get("audio_streams", []),
        "measured_levels_db": m0.get("measured_levels_db", {}),
        "program_track": m0.get("program_track"),
        "gop_frames": m0.get("gop_frames"),
        "provenance": _provenance(digest, source_memory.SLOT_SOURCE, root,
                                  {"probe": "ffprobe", "levels": "ffmpeg"}),
    }


def _transcript(digest, root, duration):
    doc, status = source_memory.read_m1(digest, root)
    if doc is None:
        return {"status": "absent" if status == "missing" else status}
    if status != "fresh":
        # A recorded refusal is the measured answer: no audio, silence,
        # a refused declaration. It is exported as such, with its reason.
        return {"status": status, "reason": doc.get("reason")
                or doc.get("detail"),
                "provenance": _provenance(digest, source_memory.SLOT_TRANSCRIPT,
                                          root, doc.get("instrument"))}
    utterances = []
    for u in doc.get("utterances", []):
        utterances.append({
            "start": _r(u.get("start")), "end": _r(u.get("end")),
            "text": u.get("text", ""),
            "words": [[w.get("word"), _r(w.get("start")), _r(w.get("end"))]
                      for w in u.get("words", [])],
            "method": u.get("method"),
        })
    speech = doc.get("speech_seconds") or 0.0
    return {
        "status": "present",
        "language": (doc.get("instrument") or {}).get("detected_language"),
        "utterance_count": doc.get("utterance_count", len(utterances)),
        "word_count": doc.get("word_count"),
        "words_format": ["word", "start", "end"],
        "coverage": {"speech_seconds": speech,
                     "speech_fraction": (_r(speech / duration, 4)
                                         if duration else None)},
        "confidence": None,
        "confidence_reason": ("no transcriber arm publishes a confidence; the "
                              "memory records 0.0 for every utterance"),
        "utterances": utterances,
        "provenance": _provenance(digest, source_memory.SLOT_TRANSCRIPT, root,
                                  doc.get("instrument"),
                                  utterance_cut=doc.get("utterance_cut")),
    }


def _frames(digest, root, size, duration):
    doc = source_memory.read_m2(digest, root)
    state = _current(doc, digest, size)
    if state != "present":
        return {"status": state}
    times = [_r(f.get("t")) for f in doc.get("frames", [])]
    gaps = [b - a for a, b in zip(times, times[1:])]
    return {
        "status": "present",
        "frame_count": len(times),
        "rate_hz_nominal": doc.get("rate_hz_nominal"),
        "width": doc.get("width"),
        "times": times,
        "coverage": {
            "mean_spacing_seconds": _r(sum(gaps) / len(gaps)) if gaps else None,
            "max_gap_seconds": _r(max(gaps)) if gaps else None,
            "first": times[0] if times else None,
            "last": times[-1] if times else None,
            "duration_seconds": duration,
        },
        "pixels": "local-only (thumbnails stay in the memory directory)",
        "provenance": _provenance(digest, source_memory.SLOT_FRAMES_INDEX,
                                  root, doc.get("instrument"),
                                  gop_frames=doc.get("gop_frames")),
    }


def _persons(digest, root, size, full):
    doc = _load(digest, source_memory.SLOT_PERSONS, root)
    state = _current(doc, digest, size)
    if state != "present":
        return {"status": state}
    frames = doc.get("frames", [])
    with_face = sum(1 for f in frames if f.get("faces"))
    with_hand = sum(1 for f in frames if f.get("hands"))
    face_conf = [fc.get("confidence") for f in frames
                 for fc in f.get("faces", []) if fc.get("confidence") is not None]
    out = {
        "status": doc.get("status"),
        "coordinates": COORDINATES,
        "frame_count": len(frames),
        "coverage": {
            "frames_with_face_fraction": (_r(with_face / len(frames), 4)
                                          if frames else None),
            "frames_with_hand_fraction": (_r(with_hand / len(frames), 4)
                                          if frames else None),
        },
        "confidence": {"face_mean": (_r(sum(face_conf) / len(face_conf), 4)
                                     if face_conf else None),
                       "basis": "Apple Vision face observation confidence"},
        "provenance": _provenance(digest, source_memory.SLOT_PERSONS, root,
                                  doc.get("instrument")),
    }
    if full:
        out["frames"] = frames
    return out


def _identity(digest, root, embeddings):
    doc = _load(digest, source_memory.SLOT_IDENTITY, root)
    state = _current(doc, digest, None)
    if state != "present":
        return {"status": state}
    faces = []
    for track in doc.get("faces", []):
        spans = track.get("spans", [])
        scores = [s.get("det_score") for s in spans
                  if s.get("det_score") is not None]
        entry = {"track_id": track.get("track_id"),
                 "spans": [{"start": _r(s.get("start")), "end": _r(s.get("end")),
                            "box": s.get("box"),
                            "det_score": _r(s.get("det_score"), 4)}
                           for s in spans],
                 "det_score_mean": (_r(sum(scores) / len(scores), 4)
                                    if scores else None)}
        if embeddings:
            entry["embedding"] = track.get("embedding")
        faces.append(entry)
    voices = []
    for track in doc.get("voices", []):
        entry = {"track_id": track.get("track_id"),
                 "spans": [[_r(a), _r(b)] for a, b in track.get("spans", [])]}
        if embeddings:
            entry["embedding"] = track.get("embedding")
        voices.append(entry)
    return {
        "status": doc.get("status"),
        "scope": "tracks WITHIN this source; cross-source identity is "
                 "face-only and resolved per project (person_entity)",
        "face_match_threshold": doc.get("face_match_threshold"),
        "faces": faces,
        "voices": voices,
        "speech_face_links": doc.get("speech_face_links", []),
        "embeddings": ("included" if embeddings else
                       "withheld (biometric); pass --include-embeddings"),
        "provenance": _provenance(digest, source_memory.SLOT_IDENTITY, root,
                                  doc.get("instrument")),
    }


def _expressions(digest, root, full):
    doc = _load(digest, source_memory.SLOT_EXPRESSIONS, root)
    state = _current(doc, digest, None)
    if state != "present":
        return {"status": state}
    frames = doc.get("frames", [])
    faces = [f for fr in frames for f in fr.get("faces", [])]
    conf = [f.get("confidence") for f in faces
            if f.get("confidence") is not None]
    out = {
        "status": doc.get("status"),
        "frame_count": len(frames),
        "faces_classified": doc.get("faces_classified"),
        "expression_counts": doc.get("expression_counts", {}),
        "confidence": {"mean": (_r(sum(conf) / len(conf), 4) if conf else None),
                       "basis": "classifier confidence per face"},
        "provenance": _provenance(digest, source_memory.SLOT_EXPRESSIONS,
                                  root, doc.get("instrument")),
    }
    if full:
        out["frames"] = frames
    return out


def _clock(digest, root):
    doc = _load(digest, source_memory.SLOT_CLOCK, root)
    if doc is None:
        return {"status": "absent",
                "reason": "in no measured multicam group, or never built"}
    if doc.get("content_digest") != digest:
        return {"status": "stale"}
    direct = doc.get("direct_measurement") or {}
    return {
        "status": "present",
        "group_id": doc.get("group_id"),
        "group_members": [f"sha256:{d}" for d in doc.get("group_members", [])],
        "reference_asset_id": f"sha256:{doc.get('reference_digest')}",
        "offset_to_reference_seconds": doc.get("offset_to_reference_seconds"),
        "rule": "this_time + offset_to_reference_seconds == reference_time",
        "confidence": {"agreement_fraction": direct.get("agreement_fraction"),
                       "p5_seconds": direct.get("p5_seconds"),
                       "p95_seconds": direct.get("p95_seconds"),
                       "basis": ("direct n-gram match" if direct else
                                 "transitive or reference (no direct "
                                 "measurement)")},
        "provenance": _provenance(digest, source_memory.SLOT_CLOCK, root,
                                  {"ngram_size": doc.get("ngram_size")},
                                  measured_at=doc.get("built_at")),
    }


def _events(digest, root, size):
    doc = _load(digest, source_memory.SLOT_EVENTS, root)
    state = _current(doc, digest, size)
    if state != "present":
        return {"status": state}
    assigned = doc.get("faces_assigned") or 0
    unassigned = doc.get("faces_unassigned") or 0
    predicates = {}
    for name, block in (doc.get("predicates") or {}).items():
        predicates[name] = {
            "basis": block.get("basis"),
            "spans": [{k: (_r(v) if k in ("start", "end") else v)
                       for k, v in span.items()}
                      for span in block.get("spans", [])],
        }
        if "voice_unavailable_reason" in block:
            predicates[name]["voice_unavailable_reason"] = block[
                "voice_unavailable_reason"]
    candidates = {}
    for name, block in (doc.get("candidates") or {}).items():
        candidates[name] = {
            "basis": block.get("basis"),
            "is_answer": False,
            "spans": [{"face_track": s.get("face_track"),
                       "start": _r(s.get("start")), "end": _r(s.get("end")),
                       "frames": len(s.get("frames", []))}
                      for s in block.get("spans", [])],
        }
    return {
        "status": doc.get("status"),
        "coverage": {"faces_assigned": assigned, "faces_unassigned": unassigned,
                     "assigned_fraction": (_r(assigned / (assigned + unassigned), 4)
                                           if assigned + unassigned else None)},
        "predicates": predicates,
        "candidates": candidates,
        "provenance": _provenance(digest, source_memory.SLOT_EVENTS, root,
                                  measured_at=doc.get("built_at")),
    }


def _verdicts(digest, root):
    doc = _load(digest, source_memory.SLOT_VERDICTS, root)
    if doc is None:
        return {"status": "absent",
                "reason": "no span was ever verified on this source"}
    rows = []
    for key, v in sorted((doc.get("verdicts") or {}).items()):
        rows.append({"key": key, "statement": v.get("statement"),
                     "answer": v.get("answer"), "frames": v.get("frames"),
                     "reason": v.get("reason"), "model": v.get("model"),
                     "prompt_version": v.get("prompt_version"),
                     "seconds": v.get("seconds")})
    counts: Dict[str, int] = {}
    for row in rows:
        counts[row["answer"]] = counts.get(row["answer"], 0) + 1
    return {"status": "present", "counts": counts, "verdicts": rows,
            "provenance": _provenance(digest, source_memory.SLOT_VERDICTS, root)}


# ── The asset ───────────────────────────────────────────────────────


def _asset(src: dict, run_outcomes: Dict[str, dict], root: Optional[Path],
           embeddings: bool, full_frames: bool) -> dict:
    digest = src["digest"]
    path = src["path"]
    live = None
    if path:
        try:
            live = footage_identity.fingerprint(path)
        except OSError:
            live = None
    m0 = _load(digest, source_memory.SLOT_SOURCE, root) if digest else None
    size = (live or {}).get("size_bytes") or (m0 or {}).get("size_bytes")
    duration = (m0 or {}).get("duration_seconds") or src.get("duration_seconds")

    asset = {
        "asset_id": f"sha256:{digest}" if digest else None,
        "fingerprint": {"method": FINGERPRINT_METHOD, "content_digest": digest,
                        "size_bytes": size},
        "name": os.path.basename(path) if path else None,
        "media_online": live is not None,
        "duration_seconds": duration,
    }
    if digest is None:
        asset["status"] = "failed"
        asset["failure_reason"] = "no content digest: media offline and never fingerprinted"
        return asset

    sections = {
        "media": _media(digest, root, size),
        "transcript": _transcript(digest, root, duration),
        "frames": _frames(digest, root, size, duration),
        "persons": _persons(digest, root, size, full_frames),
        "identity": _identity(digest, root, embeddings),
        "expressions": _expressions(digest, root, full_frames),
        "clock": _clock(digest, root),
        "events": _events(digest, root, size),
        "verdicts": _verdicts(digest, root),
    }
    failures = []
    for section, lane in SECTION_LANE.items():
        outcome = run_outcomes.get(lane, {}).get(src["clip_id"])
        if outcome:
            sections[section]["last_run"] = outcome
            if outcome["status"] == footage_analysis.FAILED:
                failures.append(f"{lane}: {outcome.get('reason')}")
    asset.update(sections)

    # Clock and verdicts are legitimately absent (no multicam partner, no
    # question asked yet); every other section is a lane the run owes.
    owed = ("media", "transcript", "frames", "persons", "identity",
            "expressions", "events")
    missing = [s for s in owed if sections[s]["status"] in ("absent", "stale")]
    if failures:
        asset["status"] = "failed"
        asset["failure_reason"] = "; ".join(failures)
    elif missing:
        asset["status"] = "partial"
        asset["missing"] = missing
    else:
        asset["status"] = "complete"
    return asset


# ── Portability ─────────────────────────────────────────────────────


def local_paths(project: str, sources: List[dict],
                root: Optional[Path]) -> List[str]:
    """Every local path this export knows about, longest first."""
    found = {os.path.abspath(project), str(Path.home()),
             str(source_memory.source_dir("x", root).parent)}
    for src in sources:
        if src["path"]:
            found.add(os.path.abspath(src["path"]))
            found.add(os.path.dirname(os.path.abspath(src["path"])))
            found.add(os.path.realpath(src["path"]))
            found.add(os.path.dirname(os.path.realpath(src["path"])))
    return sorted((p for p in found if p and p != os.sep), key=len,
                  reverse=True)


def assert_portable(payload, forbidden: List[str], where: str = "$") -> None:
    """Raise PathLeak naming the first string that carries a local path."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            assert_portable(value, forbidden, f"{where}.{key}")
    elif isinstance(payload, list):
        for i, value in enumerate(payload):
            assert_portable(value, forbidden, f"{where}[{i}]")
    elif isinstance(payload, str):
        for path in forbidden:
            if path in payload:
                raise PathLeak(f"{where} carries the local path {path!r}; "
                               "an export is portable or it is not written")


# ── The export ──────────────────────────────────────────────────────


def build_export(project: str, root: Optional[Path] = None,
                 embeddings: bool = False,
                 full_frames: bool = False) -> tuple:
    """`(portable, local)` for one project or collection."""
    sources = footage_analysis.catalog_sources(project)
    run = footage_analysis.read_run_record(project) or {}
    outcomes = {lane["lane"]: lane["sources"] for lane in run.get("lanes", [])}

    assets = []
    seen = set()
    for src in sources:
        if src["digest"] and src["digest"] in seen:
            continue  # one asset per content, however many clips name it
        seen.add(src["digest"])
        assets.append(_asset(src, outcomes, root, embeddings, full_frames))

    status_counts: Dict[str, int] = {}
    for a in assets:
        status_counts[a["status"]] = status_counts.get(a["status"], 0) + 1
    portable = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "exported_at": _dt.datetime.now(_dt.timezone.utc).isoformat(
            timespec="seconds"),
        "generator": {"tool": "ren export-memory",
                      "module": "library/tools/memory_export.py",
                      "code_revision": footage_analysis._git_head()},
        "time_base": TIME_BASE,
        "coordinates": COORDINATES,
        "asset_id": ("sha256:<content digest>; the same bytes have the same "
                     "id on every machine, under any name or folder"),
        "collection": {"name": os.path.basename(os.path.abspath(project))},
        "analysis_run": ({
            "status": run.get("status"),
            "started_at": run.get("started_at"),
            "finished_at": run.get("finished_at"),
            "code_revision": run.get("code_revision"),
            "steps": run.get("steps"),
            "lanes": [{"lane": l["lane"], "label": l["label"],
                       "seconds": l["seconds"],
                       "seconds_per_video_minute": l["seconds_per_video_minute"],
                       "error": l["error"]} for l in run.get("lanes", [])],
        } if run else None),
        "summary": {"assets": len(assets), "status": status_counts,
                    "video_minutes": round(sum(
                        (a.get("duration_seconds") or 0.0) for a in assets)
                        / 60.0, 2)},
        "assets": assets,
    }
    local = {
        "schema": SCHEMA + ".local",
        "schema_version": SCHEMA_VERSION,
        "warning": "LOCAL-ONLY: absolute paths on this machine. Never share.",
        "project_folder": os.path.abspath(project),
        "memory_root": str(source_memory.source_dir("x", root).parent),
        "assets": {f"sha256:{s['digest']}": {
            "clip_id": s["clip_id"],
            "observed_path": s["path"],
            "memory_dir": str(source_memory.source_dir(s["digest"], root)),
        } for s in sources if s["digest"]},
    }
    assert_portable(portable, local_paths(project, sources, root))
    return portable, local


def write_export(project: str, out: Optional[str] = None, **kwargs) -> dict:
    portable, local = build_export(project, **kwargs)
    layout = ProjectLayout(project)
    target = Path(out) if out else layout.write_path(
        Area.FOOTAGE_MEMORY, f"footage_memory.v{SCHEMA_VERSION}.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(portable, indent=1), encoding="utf-8")
    local_target = layout.write_path(Area.FOOTAGE_MEMORY, LOCAL_FILE)
    local_target.write_text(json.dumps(local, indent=1), encoding="utf-8")
    return {"export": str(target), "local": str(local_target),
            "bytes": target.stat().st_size, "summary": portable["summary"]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="ren export-memory",
        description=("Write the versioned, path-portable export of a "
                     "project's footage memory (and a local-only path map "
                     "beside it). Reads only."))
    parser.add_argument("project", help="a project or `ren analyze` collection")
    parser.add_argument("--out", help="where the portable file goes "
                        "(default: pipeline_output/footage_memory/)")
    parser.add_argument("--include-embeddings", action="store_true",
                        help="include face and voice embeddings (biometric)")
    parser.add_argument("--include-frame-measurements", action="store_true",
                        help="include M3's per-frame faces, lips and hands")
    args = parser.parse_args(argv)
    from library.tools.project_registry import resolve_project_path
    found = resolve_project_path(args.project)
    if found is None:
        raise RenRefusal(f"{args.project!r} is not a project",
                         "an export reads one project's catalog and memory",
                         "ren export-memory <path to project or collection>")
    result = write_export(str(found.parent), args.out,
                          embeddings=args.include_embeddings,
                          full_frames=args.include_frame_measurements)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RenRefusal as refused:
        print(refused.render(), file=sys.stderr)
        sys.exit(REFUSAL_EXIT_CODE)
