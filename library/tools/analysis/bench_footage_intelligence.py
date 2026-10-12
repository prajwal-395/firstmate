"""FI-01: benchmark corpus manifest + fidelity/efficiency metric harness.

Plan item FI-01 (scout report §7) over the captain's plan §9 (Phase 0)
and §10 (evaluation). It measures the footage-intelligence lanes as
they exist today so later items can cite the numbers:

* (a) a corpus manifest naming the plan's 8 footage categories, which
  of the 2.5 are covered by the GEO podcast media on this machine and
  which 5.5 are absent, each absent one BY NAME with its reason;
* (b) perception-fidelity metrics from plan §10A, implemented as
  deterministic approximations over existing lanes - M7 candidates vs
  M8 verdicts, boundary error of judged spans, M3b identity
  consistency, lexical retrieval recall across M6 groups, numeric
  reproducibility across two reads, and coverage that distinguishes
  measured-absent from unmeasured;
* (c) resource-efficiency metrics from plan §10B - wall time per
  source minute, model calls per source hour, decode time, peak RSS,
  cache-hit rate, bytes per source hour;
* (d) a dated ``docs/benchmarks/`` report recording the baseline.

Deterministic approximations only: no learned estimator anywhere in
this module (plan §12F). The recall-loss tolerance (captain call C1)
and the 50-80% call-reduction target (C2) are NOT decided here - the
harness reports deltas and takes a ``--tolerance`` for the mechanical
reproduction check only. Nothing here promotes or demotes a later
scheduler.

Caller: ``python3 -m library.tools.analysis.bench_footage_intelligence
--corpus <manifest>``. Acceptance: on main this module does not
exist; after, that command over a scratch memory root reproduces the
baseline doc's numbers within the harness's own stated tolerance, and
every absent corpus category is reported by name, never silently
skipped.
"""

from __future__ import annotations

import argparse
import collections
import datetime as _dt
import json
import math
import statistics
import subprocess
import sys
import time
from pathlib import Path

try:
    import resource as _resource
except ImportError:  # pragma: no cover - non-POSIX hosts have no getrusage
    _resource = None

REPO_ROOT = Path(__file__).resolve().parents[3]

HARNESS_VERSION = 1
MANIFEST_VERSION = 1

# Float-reproduction tolerance. Basis: JSON round-trips float64 with at
# most 1 ulp of error (~2.2e-16 relative); 1e-9 relative / 1e-12
# absolute is six orders of magnitude looser than representation noise,
# so anything outside it is a real recomputation, not a serialisation
# artefact. This bounds the mechanical check only; it is not an
# evaluation bar (C1 stays open).
DEFAULT_REL_TOL = 1e-9
DEFAULT_ABS_TOL = 1e-12

# ── The plan's 8 corpus categories (plan §9 table, names verbatim) ──

PLAN_CORPUS_CATEGORIES = (
    {"key": "static_classroom",
     "footage_type": "Static classroom",
     "tests": "Sparse events and low information density"},
    {"key": "street",
     "footage_type": "Times Square/street footage",
     "tests": "Continuous movement and high novelty"},
    {"key": "podcast",
     "footage_type": "Podcast/interview",
     "tests": "Speech, faces, retakes, reactions"},
    {"key": "multicam_podcast",
     "footage_type": "Multicamera podcast",
     "tests": "Clock synchronization and alternate coverage"},
    {"key": "product",
     "footage_type": "Product footage",
     "tests": "Object recognition and changing visual focus"},
    {"key": "cinematic_broll",
     "footage_type": "Cinematic B-roll",
     "tests": "Motion, composition, exposure and visual filtering"},
    {"key": "noisy_audio",
     "footage_type": "Noisy or quiet audio",
     "tests": "VAD, acoustic activity and transcription coverage"},
    {"key": "complex_action",
     "footage_type": "Complex action",
     "tests": "Brief events and temporal understanding"},
)

CATEGORY_KEYS = tuple(c["key"] for c in PLAN_CORPUS_CATEGORIES)

COVERED = "covered"
PARTLY = "partly"
ABSENT = "absent"

# Measured basis for the default absent reasons: the scout's read-only
# survey of the declared roots at a3cd2dd found no such footage under
# the projects root, the podcast media folder or the shared assets
# (report §5). A manifest may override any reason; the default records
# what was actually looked at, not a guess.
DEFAULT_ABSENT_REASONS = {
    "static_classroom": ("no classroom footage under the declared "
                         "projects root, podcast media folder or shared "
                         "assets (scout survey at a3cd2dd)"),
    "street": ("no continuous high-motion street footage under the "
               "declared roots (scout survey at a3cd2dd)"),
    "product": ("no object-rich product footage under the declared "
                "roots; only incidental objects in podcast frames "
                "(scout survey at a3cd2dd)"),
    "cinematic_broll": ("no B-roll library on the machine; shared "
                        "assets hold sfx/music/remotion, not footage "
                        "(scout survey at a3cd2dd)"),
    "complex_action": ("no fast brief-event footage under the declared "
                       "roots; podcast gestures are the closest proxy "
                       "(scout survey at a3cd2dd)"),
}

# Refusal statuses that mean "measured and absent", one per lane that
# records them. Anything else present-with-digest is "measured";
# anything missing is "unmeasured". The three states are never merged.
MEASURED_ABSENT_BY_SLOT = {
    "transcript.words.json": ("untranscribed-",),
    "identity.json": ("no-faces-detected",),
}

# Lane slots the coverage matrix reads, in lane order
# (footage_analysis.LANES, minus the two project-wide indexes which
# carry no per-digest state).
COVERAGE_SLOTS = (
    "source.json",
    "transcript.words.json",
    "speakers.json",
    "frames.index.json",
    "persons.json",
    "identity.json",
    "expressions.json",
    "regions.json",
    "saliency.json",
    "region_stats.json",
    "scenes.json",
    "sound.json",
    "clock.json",
    "events.json",
    "verdicts.json",
    "world_model.json",
    "relationships.json",
)


# ── Small readers (never raise; missing input is unmeasured) ──


def _read_json(path: Path):
    try:
        with open(path, encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _git_head() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True,
            cwd=str(REPO_ROOT), timeout=30, check=False,
            encoding="utf-8")
    except (OSError, ValueError):
        return None
    sha = (out.stdout or "").strip()
    return sha or None


def _peak_rss_mb() -> float | None:
    if _resource is None:
        return None
    try:
        rss = _resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss
    except (OSError, ValueError):
        return None
    if sys.platform == "darwin":
        return round(rss / (1024 * 1024), 3)
    return round(rss / 1024, 3)


# ── Corpus manifest ──


def empty_manifest(head_sha: str | None = None) -> dict:
    """All 8 plan categories present and marked absent until covered."""
    return {
        "manifest_version": MANIFEST_VERSION,
        "harness_version": HARNESS_VERSION,
        "created_at": _dt.datetime.now(_dt.UTC).isoformat(
            timespec="seconds"),
        "measured_head_sha": head_sha or _git_head(),
        "tolerance": {
            "rel": DEFAULT_REL_TOL,
            "abs": DEFAULT_ABS_TOL,
            "basis": ("float64 JSON round-trip noise; bounds the "
                      "mechanical reproduction check only, not an "
                      "evaluation bar (captain call C1 stays open)"),
        },
        "categories": [
            {"key": cat["key"],
             "footage_type": cat["footage_type"],
             "tests": cat["tests"],
             "status": ABSENT,
             "reason": DEFAULT_ABSENT_REASONS.get(cat["key"],
                                                  "not surveyed"),
             "sources": []}
            for cat in PLAN_CORPUS_CATEGORIES
        ],
    }


def discover_sources(memory_root: Path,
                     media_dir: Path | None = None) -> list[dict]:
    """Per-digest rows read off ``source.json`` files, read-only.

    ``media_dir`` restricts to digests whose observed paths sit under
    it; ``None`` returns every digest with a readable ``source.json``.
    Rows carry digest, duration, size, path and the M6 group id when a
    ``clock.json`` names one.
    """
    rows = []
    for source_path in sorted(memory_root.glob("*/source.json")):
        doc = _read_json(source_path)
        if doc is None:
            continue
        digest = doc.get("content_digest") or source_path.parent.name
        paths = list(doc.get("observed_paths") or [])
        if media_dir is not None and not any(
                _is_under(p, media_dir) for p in paths):
            continue
        clock = _read_json(source_path.parent / "clock.json") or {}
        rows.append({
            "clip_id": Path(paths[0]).stem if paths else digest[:12],
            "digest": digest,
            "duration_seconds": doc.get("duration_seconds"),
            "size_bytes": doc.get("size_bytes"),
            "path": paths[0] if paths else None,
            "m6_group": clock.get("group_id"),
        })
    return rows


def _is_under(path: str, root: Path) -> bool:
    try:
        Path(path).expanduser().resolve().relative_to(
            root.expanduser().resolve())
    except (OSError, ValueError):
        return False
    return True


def cover_category(manifest: dict, key: str, sources: list[dict],
                   status: str = COVERED, reason: str = "") -> dict:
    """Mark one manifest category covered/partly with its source rows."""
    if key not in CATEGORY_KEYS:
        raise KeyError(f"unknown corpus category {key!r}; "
                       f"plan §9 names {list(CATEGORY_KEYS)}")
    if status not in (COVERED, PARTLY):
        raise ValueError(f"status must be {COVERED!r} or {PARTLY!r}")
    for cat in manifest["categories"]:
        if cat["key"] == key:
            cat["status"] = status
            cat["reason"] = reason or (
                f"{len(sources)} source(s) measured on this machine")
            cat["sources"] = sources
            return manifest
    raise KeyError(key)  # unreachable; keys are fixed above


def absent_categories(manifest: dict) -> list[dict]:
    """Every absent/partly-missing category, by name with its reason."""
    return [c for c in manifest["categories"] if c["status"] == ABSENT]


def manifest_sources(manifest: dict) -> list[dict]:
    """All covered/partly source rows across categories, deduped."""
    seen: dict[str, dict] = {}
    for cat in manifest["categories"]:
        for src in cat.get("sources") or []:
            seen.setdefault(src["digest"], src)
    return list(seen.values())


# ── Coverage: measured vs measured-absent vs unmeasured ──


def slot_state(digest_dir: Path, slot: str,
               digest: str) -> dict:
    """One slot's state: measured, measured-absent or unmeasured.

    A recorded refusal (``untranscribed-*``, ``no-faces-detected``) is
    the measured answer, never a gap. A missing file, an unreadable
    file or a digest mismatch is unmeasured. The caller names the
    producing lane from ``COVERAGE_SLOTS`` order; nothing is defaulted.
    """
    doc = _read_json(digest_dir / slot)
    if doc is None:
        return {"state": "unmeasured", "reason": f"{slot} not on disk"}
    if doc.get("content_digest") != digest:
        return {"state": "unmeasured",
                "reason": f"{slot} names another digest"}
    markers = MEASURED_ABSENT_BY_SLOT.get(slot, ())
    status = str(doc.get("status") or "")
    if any(status.startswith(m) for m in markers):
        return {"state": "measured-absent",
                "reason": f"{slot} records refusal {status!r}"}
    return {"state": "measured", "reason": f"{slot} present"}


def coverage_matrix(memory_root: Path,
                    digests: list[str]) -> dict:
    """Per digest, per slot: the three-state coverage row."""
    matrix = {}
    for digest in digests:
        ddir = memory_root / digest
        matrix[digest] = {
            slot: slot_state(ddir, slot, digest)
            for slot in COVERAGE_SLOTS
        }
    return matrix


# ── Fidelity (a): M7 candidates vs M8 verdicts ──


def _candidate_spans(events_doc: dict) -> list[dict]:
    """Every M7 candidate span: the proposal set M8 judges.

    Only the ``candidates`` dict counts - those are the spans
    ``--verify`` verdicts are recorded against. Predicate spans
    (on_screen, speaking) are derived intervals, not proposals, so
    counting them here would let whole-clip spans absorb every
    verdict and flatter the match.
    """
    spans = []
    for name, cand in (events_doc.get("candidates") or {}).items():
        for span in (cand or {}).get("spans") or []:
            spans.append({"predicate": f"candidate:{name}",
                          "start": span.get("start"),
                          "end": span.get("end")})
    return [s for s in spans
            if isinstance(s["start"], (int, float))
            and isinstance(s["end"], (int, float))]


def _verdict_hits(verdicts_doc: dict) -> list[dict]:
    """Every M8 verdict with its judged frame times and answer."""
    hits = []
    for key, verdict in (verdicts_doc.get("verdicts") or {}).items():
        frames = [f for f in (verdict.get("frames") or [])
                  if isinstance(f, (int, float))]
        hits.append({"key": key,
                     "answer": verdict.get("answer"),
                     "statement": verdict.get("statement"),
                     "frames": frames,
                     "model_seconds": verdict.get("seconds")})
    return hits


def _overlaps(span: dict, frame_times: list[float]) -> bool:
    return any(span["start"] <= t <= span["end"] for t in frame_times)


def event_verdict_metrics(events_doc: dict,
                          verdicts_doc: dict) -> dict:
    """M7-vs-M8 agreement as deterministic approximations.

    * ``m7_precision_vs_m8``: judged candidate events the verifier
      confirmed ("yes" / judged). M7 proposes, M8 disposes.
    * ``m8_judgment_coverage_of_m7``: candidate spans carrying at
      least one verdict / all candidate spans. This is the recall
      half: how much of what M7 proposed was ever judged.
    * ``mean_boundary_error_s``: over yes-verdicts matched to a span,
      half the summed edge distance between the span and the judged
      frame extent. Matching is any temporal overlap - no IoU
      constant, and the IoU distribution ships beside the mean so a
      later item can set a bar from data (C1).

    Verdicts overlapping no span are counted as orphans, not dropped.
    """
    spans = _candidate_spans(events_doc or {})
    verdicts = _verdict_hits(verdicts_doc or {})
    judged = [v for v in verdicts if v["frames"]]
    unjudgeable = len(verdicts) - len(judged)
    matched_pairs = []
    orphan_verdicts = 0
    for verdict in judged:
        hit = [s for s in spans if _overlaps(s, verdict["frames"])]
        if not hit:
            orphan_verdicts += 1
            continue
        # The span sharing the most judged frames owns the verdict.
        best = max(hit, key=lambda s: sum(
            1 for t in verdict["frames"] if s["start"] <= t <= s["end"]))
        matched_pairs.append((best, verdict))
    yes = sum(1 for _, v in matched_pairs if v["answer"] == "yes")
    judged_matched = len(matched_pairs)
    boundary_errors = []
    ious = []
    for span, verdict in matched_pairs:
        if verdict["answer"] != "yes":
            continue
        lo, hi = min(verdict["frames"]), max(verdict["frames"])
        boundary_errors.append(
            (abs(span["start"] - lo) + abs(span["end"] - hi)) / 2.0)
        inter = max(0.0, min(span["end"], hi) - max(span["start"], lo))
        union = max(span["end"], hi) - min(span["start"], lo)
        ious.append(inter / union if union > 0 else 0.0)
    judged_spans = {id(s) for s, _ in matched_pairs}
    return {
        "m7_candidate_spans": len(spans),
        "m8_verdicts": len(verdicts),
        "m8_verdicts_without_frames": unjudgeable,
        "m8_orphan_verdicts": orphan_verdicts,
        "m7_precision_vs_m8": (yes / judged_matched
                               if judged_matched else None),
        "m8_judgment_coverage_of_m7": (
            len({id(s) for s, _ in matched_pairs}) / len(spans)
            if spans else None),
        "judged_spans": len(judged_spans),
        "mean_boundary_error_s": (
            statistics.fmean(boundary_errors) if boundary_errors
            else None),
        "boundary_error_n": len(boundary_errors),
        "iou_values": sorted(ious),
    }


# ── Fidelity (b): identity consistency vs the M3b roster ──


def identity_metrics(events_doc: dict, identity_doc: dict) -> dict:
    """How stably faces, voices and speaking spans hold identity.

    The roster is the M3b file itself: face tracks and voice tracks
    with embeddings. Every fraction reads the files directly; a lane
    the fixture lacks yields ``None`` with the producing lane named,
    never a default.
    """
    out: dict = {"m3b_lane": "identity"}
    if not events_doc:
        out["m7_lane"] = "events (missing)"
    faces = (identity_doc or {}).get("faces") or []
    voices = (identity_doc or {}).get("voices") or []
    out["face_tracks"] = len(faces)
    out["voice_tracks"] = len(voices)
    out["faces_with_embedding"] = sum(
        1 for f in faces if f.get("embedding"))
    assigned = (events_doc or {}).get("faces_assigned")
    unassigned = (events_doc or {}).get("faces_unassigned")
    if isinstance(assigned, (int, float)) and isinstance(
            unassigned, (int, float)) and assigned + unassigned > 0:
        out["m7_face_assignment_rate"] = assigned / (
            assigned + unassigned)
    else:
        out["m7_face_assignment_rate"] = None
    speaking = ((events_doc or {}).get("predicates") or {}).get(
        "speaking") or {}
    spans = speaking.get("spans") or []
    linked = [s for s in spans if s.get("face_track")]
    out["speaking_spans"] = len(spans)
    out["speaking_spans_with_face"] = (
        len(linked) / len(spans) if spans else None)
    voice_ids = {v.get("track_id") for v in voices}
    links = (identity_doc or {}).get("speech_face_links") or []
    linked_voices = {link.get("voice_track") for link in links}
    out["voices_with_face_link"] = (
        len(voice_ids & linked_voices) / len(voice_ids)
        if voice_ids else None)
    return out


# ── Fidelity (c): lexical retrieval recall across M6 groups ──


def _utterance_texts(transcript_doc: dict) -> list[dict]:
    out = []
    for i, utt in enumerate(transcript_doc.get("utterances") or []):
        text = utt.get("text") or " ".join(
            w.get("word", "") for w in (utt.get("words") or []))
        if text.strip():
            out.append({"index": i, "start": utt.get("start"),
                        "end": utt.get("end"), "text": text})
    return out


def _tokenize(text: str) -> list[str]:
    return ["".join(ch for ch in tok.lower() if ch.isalnum())
            for tok in text.split()]


def derive_probes(transcript_docs: dict[str, dict],
                  per_source: int = 3) -> dict[str, list[str]]:
    """Probe queries from the corpus's own vocabulary, never constants.

    A probe term appears in at least two utterances of its source but
    in no more than 90% of them - topical enough to retrieve on, common
    enough to be more than a hapax. Single-character and pure-digit
    tokens are dropped. The document-frequency band is computed from
    the transcripts at hand, so the probes move with the corpus.
    """
    probes = {}
    for digest, doc in transcript_docs.items():
        utts = _utterance_texts(doc or {})
        if not utts:
            probes[digest] = []
            continue
        df: collections.Counter = collections.Counter()
        for utt in utts:
            df.update({t for t in _tokenize(utt["text"]) if t})
        n = len(utts)
        terms = sorted(
            (t for t, c in df.items()
             if c >= 2 and c <= 0.9 * n and len(t) > 1
             and not t.isdigit()),
            key=lambda t: (-df[t], t))
        probes[digest] = terms[:max(0, per_source)]
    return probes


def _lexical_score(query_terms: list[str], text: str) -> int:
    hay = set(_tokenize(text))
    return sum(1 for t in query_terms if t in hay)


def retrieval_recall(transcript_docs: dict[str, dict],
                     m6_groups: dict[str, str | None],
                     probes: dict[str, list[str]],
                     top_k: int = 3) -> dict:
    """Lexical probe recall: same-conversation retrieval across cameras.

    Each probe term searches every corpus source's utterances by token
    overlap (the deterministic half of ``ren search``'s hybrid path;
    no embedder, no model). A probe HITS when a source sharing its
    M6 group lands in the top_k. Probes from sources with no group
    mates are reported separately - they cannot test cross-camera
    recall, and counting them as hits would flatter the number.
    """
    scored_sources = sorted(transcript_docs)
    detail = []
    for digest, terms in probes.items():
        group = m6_groups.get(digest)
        mates = {d for d in scored_sources
                 if d != digest and m6_groups.get(d) == group
                 and group is not None}
        for term in terms:
            ranked = sorted(
                scored_sources,
                key=lambda d: (-_lexical_score(
                    [term], " ".join(u["text"] for u in _utterance_texts(
                        transcript_docs[d] or {}))), d))
            top = ranked[:max(1, top_k)]
            hits = [d for d in top if d in mates]
            detail.append({"probe": term, "source": digest[:12],
                           "m6_group": (group or "")[:12],
                           "group_mates": len(mates),
                           "hit": bool(hits),
                           "top": [d[:12] for d in top]})
    testable = [d for d in detail if d["group_mates"] > 0]
    return {
        "probes": len(detail),
        "testable_probes": len(testable),
        "untestable_probes": len(detail) - len(testable),
        "cross_camera_recall": (
            sum(1 for d in testable if d["hit"]) / len(testable)
            if testable else None),
        "detail": detail,
    }


# ── Fidelity (d): reproducibility across two reads ──


def reproducibility(values_a: list[float],
                    values_b: list[float]) -> dict:
    """Max/mean absolute difference between two numeric runs.

    The same instrument under the same method must reproduce its own
    numbers; anything above float repr noise is a real change. Pass is
    judged by the caller-supplied tolerance, never a constant here.
    """
    if len(values_a) != len(values_b):
        return {"n": None, "length_mismatch": [len(values_a),
                                               len(values_b)],
                "max_abs_diff": None, "mean_abs_diff": None}
    diffs = [abs(a - b) for a, b in zip(values_a, values_b)]
    return {"n": len(diffs),
            "max_abs_diff": max(diffs) if diffs else 0.0,
            "mean_abs_diff": (statistics.fmean(diffs) if diffs
                              else 0.0)}


# ── Efficiency (plan §10B) ──


def slot_bytes(digest_dir: Path) -> dict:
    """On-disk bytes per slot file, plus the frames/ sidecar if present."""
    sizes = {}
    for path in sorted(digest_dir.iterdir()):
        if path.is_file():
            try:
                sizes[path.name] = path.stat().st_size
            except OSError:
                continue
        elif path.is_dir() and path.name == "frames":
            total = 0
            try:
                for sub in path.rglob("*"):
                    if sub.is_file():
                        total += sub.stat().st_size
            except OSError:
                continue
            sizes["frames/"] = total
    return sizes


def model_call_counts(memory_root: Path, digests: list[str]) -> dict:
    """Recorded model invocations per digest: M8 verdicts + M4 scenes.

    Each M8 verdict carries its VLM seconds; each M4 scene entry is one
    Gemma window. Both are counted from the files, so the per-hour
    rate is measured, not estimated.
    """
    counts = {}
    for digest in digests:
        ddir = memory_root / digest
        verdicts = (_read_json(ddir / "verdicts.json") or {}).get(
            "verdicts") or {}
        scenes_doc = _read_json(ddir / "scenes.json") or {}
        scenes = scenes_doc.get("scenes")
        counts[digest] = {
            "m8_verdict_calls": len(verdicts),
            "m8_verdict_seconds": round(sum(
                float(v.get("seconds") or 0) for v in verdicts.values()
                if isinstance(v.get("seconds"), (int, float))), 1),
            "m4_scene_windows": (len(scenes) if isinstance(
                scenes, list) else None),
        }
    return counts


def efficiency_metrics(memory_root: Path, sources: list[dict],
                       wall_seconds: float,
                       run_record: dict | None = None) -> dict:
    """Wall time, model calls, cache hits and bytes, all per source hour.

    ``run_record`` is an ``analysis_run.json`` when one exists; without
    it the cache-hit rate is unmeasured with the reason, never zero.
    """
    total_minutes = sum((s.get("duration_seconds") or 0)
                        for s in sources) / 60.0
    total_hours = total_minutes / 60.0 or 1e-9
    digests = [s["digest"] for s in sources]
    calls = model_call_counts(memory_root, digests)
    total_calls = sum(c["m8_verdict_calls"] for c in calls.values())
    scene_windows = [c["m4_scene_windows"] for c in calls.values()
                     if c["m4_scene_windows"] is not None]
    total_bytes = 0
    per_digest_bytes = {}
    for digest in digests:
        sizes = slot_bytes(memory_root / digest)
        per_digest_bytes[digest] = sizes
        total_bytes += sum(sizes.values())
    cache_hit_rate = None
    cache_basis = "no analysis_run.json at hand: unmeasured, not zero"
    if run_record:
        built = reused = 0
        for lane in run_record.get("lanes") or []:
            for outcome in (lane.get("sources") or {}).values():
                if outcome.get("status") == "built":
                    built += 1
                elif outcome.get("status") == "reused":
                    reused += 1
        if built + reused > 0:
            cache_hit_rate = reused / (built + reused)
            cache_basis = (f"{reused} reused / {built} built over "
                           f"{built + reused} lane-source builds")
    return {
        "wall_seconds": round(wall_seconds, 1),
        "video_minutes": round(total_minutes, 2),
        "wall_seconds_per_source_minute": (
            wall_seconds / total_minutes if total_minutes else None),
        "model_calls_per_source_hour": {
            "m8_verdict_calls": total_calls / total_hours,
            "m4_scene_windows_per_hour": (
                sum(scene_windows) / total_hours if scene_windows
                else None),
        },
        "decode_seconds": None,
        "decode_basis": ("measured only around a harness-driven decode "
                         "pass; lane builds record no decode split"),
        "peak_rss_mb": _peak_rss_mb(),
        "cache_hit_rate": cache_hit_rate,
        "cache_basis": cache_basis,
        "bytes_per_source_hour": total_bytes / total_hours,
        "total_bytes": total_bytes,
        "per_digest": {d: {"model_calls": calls[d],
                           "bytes": per_digest_bytes[d]}
                       for d in digests},
    }


# ── The run ──


def run_corpus(manifest: dict, memory_root: Path,
               probes_per_source: int = 3,
               top_k: int = 3) -> dict:
    """All metrics over the manifest's covered sources. Times itself."""
    t0 = time.perf_counter()
    sources = manifest_sources(manifest)
    digests = [s["digest"] for s in sources]
    docs: dict[str, dict] = {}
    for digest in digests:
        ddir = memory_root / digest
        docs[digest] = {
            slot: (_read_json(ddir / slot) or {})
            for slot in ("transcript.words.json", "events.json",
                         "verdicts.json", "identity.json")
        }
    fidelity = {}
    for digest in digests:
        d = docs[digest]
        fidelity[digest] = {
            "clip_id": next(
                (s["clip_id"] for s in sources if s["digest"]
                 == digest), digest[:12]),
            "events": event_verdict_metrics(
                d["events.json"], d["verdicts.json"]),
            "identity": identity_metrics(
                d["events.json"], d["identity.json"]),
        }
    # Reproducibility: the same files read twice must agree exactly.
    # A caller measuring two fresh decodes passes both payloads to
    # reproducibility() directly; here the harness pins the read path.
    repro = {}
    for digest in digests:
        ddir = memory_root / digest
        first = _read_json(ddir / "transcript.words.json") or {}
        second = _read_json(ddir / "transcript.words.json") or {}
        words_a = [w.get("start") for u in first.get("utterances", [])
                   for w in (u.get("words") or [])
                   if isinstance(w.get("start"), (int, float))]
        words_b = [w.get("start") for u in second.get("utterances", [])
                   for w in (u.get("words") or [])
                   if isinstance(w.get("start"), (int, float))]
        repro[digest] = reproducibility(words_a, words_b)
    transcripts = {d: docs[d]["transcript.words.json"] for d in digests}
    probes = derive_probes(transcripts, per_source=probes_per_source)
    m6_groups = {s["digest"]: s.get("m6_group") for s in sources}
    retrieval = retrieval_recall(transcripts, m6_groups, probes,
                                 top_k=top_k)
    wall_seconds = time.perf_counter() - t0
    efficiency = efficiency_metrics(memory_root, sources, wall_seconds)
    absent = absent_categories(manifest)
    return {
        "harness_version": HARNESS_VERSION,
        "measured_at": _dt.datetime.now(_dt.UTC).isoformat(
            timespec="seconds"),
        "measured_head_sha": manifest.get("measured_head_sha"),
        "memory_root": str(memory_root),
        "sources": sources,
        "absent_categories": [
            {"key": c["key"], "footage_type": c["footage_type"],
             "reason": c["reason"]} for c in absent
        ],
        "coverage": coverage_matrix(memory_root, digests),
        "fidelity": fidelity,
        "reproducibility": repro,
        "retrieval": retrieval,
        "efficiency": efficiency,
        "tolerance": manifest.get("tolerance"),
    }


def check_within_tolerance(report: dict, baseline: dict,
                           rel: float, abs_tol: float) -> dict:
    """Mechanical reproduction check: numbers within tolerance.

    Compares per-digest fidelity scalars, retrieval recall, coverage
    states and byte counts. Coverage states must match exactly;
    floats must land within ``rel``/``abs_tol``. Returns per-metric
    verdicts plus an overall ``reproduces`` flag.
    """
    verdicts = []
    ok = True

    def _close(path: str, new, old) -> None:
        nonlocal ok
        if new is None and old is None:
            verdicts.append({"metric": path, "reproduces": True,
                             "detail": "both unmeasured"})
            return
        if new is None or old is None:
            ok = False
            verdicts.append({"metric": path, "reproduces": False,
                             "detail": f"measured-vs-unmeasured: "
                                       f"{new!r} vs {old!r}"})
            return
        if isinstance(new, bool) or isinstance(old, bool):
            match = new == old
        elif isinstance(new, (int, float)) and isinstance(
                old, (int, float)):
            match = math.isclose(new, old, rel_tol=rel,
                                 abs_tol=abs_tol)
        else:
            match = new == old
        if not match:
            ok = False
        verdicts.append({"metric": path, "reproduces": match,
                         "new": new, "baseline": old})

    for digest, fnew in (report.get("fidelity") or {}).items():
        fold = (baseline.get("fidelity") or {}).get(digest, {})
        for section in ("events", "identity"):
            for key, value in (fnew.get(section) or {}).items():
                if key == "iou_values":
                    continue
                _close(f"fidelity.{digest[:8]}.{section}.{key}", value,
                       (fold.get(section) or {}).get(key))
    rnew, rold = report.get("retrieval") or {}, baseline.get(
        "retrieval") or {}
    _close("retrieval.cross_camera_recall",
           rnew.get("cross_camera_recall"), rold.get(
               "cross_camera_recall"))
    for digest, cnew in (report.get("coverage") or {}).items():
        cold = (baseline.get("coverage") or {}).get(digest, {})
        for slot, state in cnew.items():
            _close(f"coverage.{digest[:8]}.{slot}.state",
                   state.get("state"),
                   (cold.get(slot) or {}).get("state"))
    enew, eold = report.get("efficiency") or {}, baseline.get(
        "efficiency") or {}
    _close("efficiency.bytes_per_source_hour",
           enew.get("bytes_per_source_hour"),
           eold.get("bytes_per_source_hour"))
    return {"reproduces": ok, "rel_tol": rel, "abs_tol": abs_tol,
            "metrics": verdicts}


# ── CLI: the harness's real caller ──


def _load_manifest(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"cannot read corpus manifest {path}: {exc}")
    keys = {c.get("key") for c in doc.get("categories", [])}
    if keys != set(CATEGORY_KEYS):
        raise SystemExit(
            f"manifest {path} names {sorted(keys)}, not the plan §9 "
            f"eight {sorted(CATEGORY_KEYS)}")
    return doc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="FI-01 footage-intelligence benchmark harness: "
                    "corpus manifest plus fidelity/efficiency metrics.")
    parser.add_argument("--corpus", type=Path, default=None,
                        help="corpus manifest JSON to measure")
    parser.add_argument("--memory-root", type=Path, default=None,
                        help="source-memory root (default: the machine's)")
    parser.add_argument("--out", type=Path, default=None,
                        help="write the full report JSON here")
    parser.add_argument("--check", type=Path, default=None,
                        help="baseline report JSON to reproduce")
    parser.add_argument("--rel-tol", type=float, default=None,
                        help="reproduction relative tolerance "
                             "(default: the manifest's own)")
    parser.add_argument("--abs-tol", type=float, default=None,
                        help="reproduction absolute tolerance "
                             "(default: the manifest's own)")
    parser.add_argument("--probes-per-source", type=int, default=3)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--emit-manifest", type=Path, default=None,
                        help="write a fresh all-absent manifest and exit")
    parser.add_argument("--media-dir", type=Path, default=None,
                        help="with --discover: only digests observed "
                             "under this folder")
    parser.add_argument("--discover", action="store_true",
                        help="print discovered source rows as JSON "
                             "and exit")
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.emit_manifest is not None:
        manifest = empty_manifest()
        args.emit_manifest.write_text(json.dumps(manifest, indent=1),
                                      encoding="utf-8")
        print(f"wrote all-absent manifest {args.emit_manifest}")
        return 0
    memory_root = Path(args.memory_root) if args.memory_root else None
    if memory_root is None:
        from library.tools import source_memory
        memory_root = source_memory.memory_root()
    if args.discover:
        rows = discover_sources(memory_root, args.media_dir)
        print(json.dumps(rows, indent=1))
        return 0
    if args.corpus is None:
        parser.error("--corpus <manifest> is required")
    manifest = _load_manifest(args.corpus)
    report = run_corpus(manifest, memory_root,
                        probes_per_source=args.probes_per_source,
                        top_k=args.top_k)
    # Absent categories are always reported by name, never skipped.
    absent = report["absent_categories"]
    if absent:
        print("absent corpus categories "
              f"({len(absent)} of {len(CATEGORY_KEYS)}):")
        for cat in absent:
            print(f"  - {cat['key']} ({cat['footage_type']}): "
                  f"{cat['reason']}")
    else:
        print("all 8 plan §9 categories covered")
    if args.out is not None:
        args.out.write_text(json.dumps(report, indent=1),
                            encoding="utf-8")
        print(f"wrote report {args.out}")
    if args.check is not None:
        try:
            with open(args.check, encoding="utf-8") as handle:
                baseline = json.load(handle)
        except (OSError, ValueError) as exc:
            raise SystemExit(f"cannot read baseline {args.check}: "
                             f"{exc}")
        tol = manifest.get("tolerance") or {}
        rel = (args.rel_tol if args.rel_tol is not None
               else tol.get("rel", DEFAULT_REL_TOL))
        abs_tol = (args.abs_tol if args.abs_tol is not None
                   else tol.get("abs", DEFAULT_ABS_TOL))
        verdict = check_within_tolerance(report, baseline, rel,
                                         abs_tol)
        failed = [v for v in verdict["metrics"]
                  if not v["reproduces"]]
        print(f"reproduction: {len(verdict['metrics']) - len(failed)}/"
              f"{len(verdict['metrics'])} within tolerance "
              f"(rel={rel}, abs={abs_tol})")
        for item in failed:
            print(f"  MISMATCH {item['metric']}: "
                  f"{item.get('new')!r} vs {item.get('baseline')!r}"
                  + (f" ({item.get('detail')})" if item.get(
                      "detail") else ""))
        return 0 if verdict["reproduces"] else 1
    _print_summary(report)
    return 0


def _print_summary(report: dict) -> None:
    eff = report.get("efficiency") or {}
    print(f"sources: {len(report.get('sources') or [])}, "
          f"video minutes: {eff.get('video_minutes')}, "
          f"wall s/source-min: "
          f"{eff.get('wall_seconds_per_source_minute')}")
    ret = report.get("retrieval") or {}
    print(f"retrieval: cross-camera recall "
          f"{ret.get('cross_camera_recall')} over "
          f"{ret.get('testable_probes')} testable probes "
          f"({ret.get('untestable_probes')} untestable)")
    for digest, f in (report.get("fidelity") or {}).items():
        ev = f.get("events") or {}
        print(f"  {digest[:8]} precision={ev.get('m7_precision_vs_m8')} "
              f"judgment-coverage="
              f"{ev.get('m8_judgment_coverage_of_m7')} "
              f"boundary-err={ev.get('mean_boundary_error_s')}")


if __name__ == "__main__":
    raise SystemExit(main())
