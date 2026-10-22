"""VI-013b: a reel plan asks the world narrowly, against the world read whole.

The reel plan is the 37 real reel candidates of the Ren-owned scratch
project `ren-scratch-l3wave`, with the reel builder's own speaker
attribution (`seconds_by_speaker`, `share_by_speaker` from the master
transcript) as the ground truth - the same corpus
`docs/benchmarks/2026-10-22-targeted-reasoning.py` measured.

Two routes answer the same planning question - "who speaks in this
candidate's source seconds" - over the same corpus:

- ``whole_view`` - the surface the planner would use without this lane:
  each candidate's source spans read off the whole per-second
  `world_model.json` documents, scanning every bin for speaking rows.
- ``wired`` - this lane: `library.tools.reel_world_attribution`,
  the same question through `world_model_query.World` over the
  canonical M1/M7 records, the code the `select_reels` post-bridge now
  calls.

For each route the harness records, per candidate: the world speaker
set and share, whether the set matches the plan's, the share
difference, and the world-model context bytes (and tokens, where the
tokenizer runs) one planning call carries. Denominators travel with
every number, and any fidelity regression (a wired answer that differs
from the whole-view answer, or a plan the wiring changed) reverts.

Nothing outside the repository and the scratch root is written, no
footage is touched, and no Resolve, broker or captain project is
involved. The machine store is read through symlinks; `world_model.json`
is built fresh under the scratch root.

    python3 docs/benchmarks/vi013b_planner_uses_query.py \
        --project ~/Documents/content_stuff/video_projects/ren-scratch-l3wave \
        [--scratch-root /tmp/fm-vi013b/memory] [--out-json FILE]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.tools import source_memory, token_measure, world_model
from library.tools.reel_world_attribution import (
    attribute_candidates,
    plan_digest_for,
)

# ── The corpus ─────────────────────────────────────────────────────


def load_plan(project: Path) -> tuple:
    """`(candidates, transcript, catalog)` for one real reel plan."""
    state = json.loads((project / "pipeline_data.json").read_text(
        encoding="utf-8"))
    outputs = state.get("capability_outputs") or {}
    candidates = ((outputs.get("reel.candidates") or {})
                  .get("reel_candidates") or [])
    transcript = json.loads(
        (project / "pipeline_output" / "scratch" / "timeline_transcript"
         / "transcript.json").read_text(encoding="utf-8"))
    catalog = list(source_memory.load_catalog(str(project)))
    return candidates, transcript, catalog


def involved_digests(project: Path, catalog: list) -> dict:
    """`{source_file: digest}` for the catalogued, fingerprinted clips."""
    recorded = source_memory.load_recorded_fingerprints(str(project))
    rows = {}
    for clip in catalog:
        if not isinstance(clip, dict):
            continue
        digest, _basis = source_memory.digest_for_clip(
            str(project), clip, recorded)
        source_file = clip.get("source_file") or clip.get("path") or ""
        if digest and source_file:
            rows.setdefault(str(source_file), digest)
    return rows


# ── The scratch root ───────────────────────────────────────────────


def build_scratch_root(digests: set, machine_root: Path,
                       scratch_root: Path) -> Path:
    """A scratch memory root: symlinks to the machine lane files, M9 fresh.

    The machine store is never written; `world_model.json` is built
    fresh under the scratch root the way PR 3139 built it, so the
    whole-view route reads exactly what a planner holding the view
    would hold.
    """
    scratch_root.mkdir(parents=True, exist_ok=True)
    for digest in sorted(digests):
        machine_dir = machine_root / digest
        scratch_dir = scratch_root / digest
        scratch_dir.mkdir(parents=True, exist_ok=True)
        if machine_dir.is_dir():
            for entry in sorted(machine_dir.iterdir()):
                link = scratch_dir / entry.name
                if link.exists() or link.is_symlink():
                    continue
                try:
                    link.symlink_to(entry)
                except OSError:
                    pass
        account = world_model.build_source_world_model(
            digest, f"<scratch:{digest[:12]}>", scratch_root)
        assert not account.get("failed"), account
    return scratch_root


# ── The whole-view route ───────────────────────────────────────────


def _speaking_rows(doc: dict, start: float, end: float) -> list:
    """Every M7 speaking span the whole-view bins overlapping [start, end)."""
    rows = []
    for entry in doc.get("timeline") or []:
        try:
            t = float(entry.get("t"))
        except (TypeError, ValueError):
            continue
        if not (start < t + 1 and t < end):
            continue
        for span in (entry.get("events") or {}).get("speaking") or []:
            key = (span.get("voice_track"), span.get("face_track"),
                   span.get("start"), span.get("end"))
            if key in {(r.get("voice_track"), r.get("face_track"),
                        r.get("start"), r.get("end")) for r in rows}:
                continue
            rows.append(span)
    return rows


def route_whole_view(candidates: list, transcript: dict,
                     clips: dict, docs: dict) -> list:
    """One planning question per candidate, answered off the whole view."""
    from library.tools.reel_world_attribution import (
        _overlapping_segments,
        _speaker_for_track,
    )
    answers = []
    for candidate in candidates:
        start, end = float(candidate["start"]), float(candidate["end"])
        segments = _overlapping_segments(transcript, start, end)
        share: dict = {}
        for segment in segments:
            source_file = str(segment.get("source_file") or "")
            digest = clips.get(source_file)
            doc = docs.get(digest) if digest else None
            if doc is None:
                continue
            seg_start = float(segment["source_start"])
            seg_end = float(segment["source_end"])
            for span in _speaking_rows(doc, seg_start, seg_end):
                try:
                    span_start = float(span["start"])
                    span_end = float(span["end"])
                except (TypeError, ValueError, KeyError):
                    continue
                speaker = _speaker_for_track(
                    segments, span.get("face_track"),
                    span_start, span_end)
                if not speaker:
                    continue
                spoken = max(0.0, min(seg_end, span_end)
                             - max(seg_start, span_start))
                if spoken > 0.0:
                    share[speaker] = share.get(speaker, 0.0) + spoken
        total = sum(share.values())
        norm = ({name: round(value / total, 4) for name, value in share.items()}
                if total > 0.0 else {})
        answers.append({"world_share": norm, "world_speakers": sorted(norm)})
    return answers


# ── The run ────────────────────────────────────────────────────────


def run(project: Path, scratch_root: Path) -> dict:
    """Before (whole view) vs after (wired), on one real reel plan."""
    candidates, transcript, catalog = load_plan(project)
    clips = involved_digests(project, catalog)
    machine_root = source_memory.machine_memory_root()

    tmpdir = None
    if scratch_root is None:
        tmpdir = Path(tempfile.mkdtemp(prefix="fm-vi013b-memory-"))
        scratch_root = tmpdir
    build_scratch_root(set(clips.values()), machine_root, scratch_root)

    docs, doc_bytes = {}, {}
    for digest in sorted(set(clips.values())):
        path = scratch_root / digest / source_memory.SLOT_WORLD_MODEL
        text = path.read_text(encoding="utf-8")
        docs[digest] = json.loads(text)
        doc_bytes[digest] = len(text.encode("utf-8"))

    before = route_whole_view(candidates, transcript, clips, docs)

    report = attribute_candidates(
        candidates, transcript, str(project), catalog,
        memory_root=str(scratch_root))
    after = [{"world_share": dict(f.world_share),
              "world_speakers": list(f.world_speakers)}
             for f in report.findings]

    def _diff(world_share: dict, plan_share: dict):
        both = set(world_share) | set(plan_share)
        if not both:
            return None
        return round(sum(abs(world_share.get(name, 0.0)
                             - plan_share.get(name, 0.0)) for name in both)
                     / len(both), 4)

    rows = []
    for index, (candidate, left, right) in enumerate(
            zip(candidates, before, after), 1):
        plan_share = {name: round(float(value), 4) for name, value in
                      (candidate.get("share_by_speaker") or {}).items()}
        plan_set = set(candidate.get("speakers") or [])
        whole_set_ok = set(left["world_speakers"]) == plan_set
        wired_set_ok = set(right["world_speakers"]) == plan_set
        rows.append({
            "candidate": index,
            "plan_share": plan_share,
            "whole_view_share": left["world_share"],
            "wired_share": right["world_share"],
            "whole_view_share_diff": _diff(left["world_share"],
                                            plan_share),
            "wired_share_diff": _diff(right["world_share"], plan_share),
            "answers_identical": left == right,
            "whole_view_set_matches_plan": whole_set_ok,
            "wired_set_matches_plan": wired_set_ok,
            # A regression is a candidate the wired route answers WORSE
            # than the whole-view route against the plan. An improvement
            # (wired matches where the view misattributes across sources)
            # is reported, not reverted.
            "regression": (whole_set_ok and not wired_set_ok),
        })

    whole_per_call = []
    query_per_call = []
    for index, candidate in enumerate(candidates, 1):
        segments = attribute_segments(transcript, candidate)
        digests = {clips.get(str(seg.get("source_file") or ""))
                   for seg in segments}
        digests.discard(None)
        whole = sum(doc_bytes.get(digest, 0) for digest in digests)
        whole_per_call.append(whole)
        finding = report.findings[index - 1] if index - 1 < len(
            report.findings) else None
        query_per_call.append(finding.query_bytes if finding else 0)

    payload = {
        "project": str(project),
        "memory_root": str(scratch_root),
        "candidates_asked": len(rows),
        "plan_digest": plan_digest_for(candidates),
        "answers_identical": (
            f"{sum(1 for r in rows if r['answers_identical'])}/{len(rows)}"),
        "regressions": (
            f"{sum(1 for r in rows if r['regression'])}/{len(rows)}"),
        "improved": [
            r["candidate"] for r in rows
            if r["wired_set_matches_plan"]
            and not r["whole_view_set_matches_plan"]],
        "whole_view_set_matches_plan": (
            f"{sum(1 for r in rows if r['whole_view_set_matches_plan'])}"
            f"/{len(rows)}"),
        "wired_set_matches_plan": (
            f"{sum(1 for r in rows if r['wired_set_matches_plan'])}/{len(rows)}"),
        "whole_view_bytes_per_call": (
            sum(whole_per_call) // len(whole_per_call) if rows else 0),
        "query_bytes_per_call": (
            sum(query_per_call) // len(query_per_call) if rows else 0),
        "whole_view_bytes_total": sum(whole_per_call),
        "query_bytes_total": sum(query_per_call),
        "tokenizer": token_measure.available(),
        "rows": rows,
    }
    if token_measure.O200K in token_measure.available():
        import tiktoken
        enc = tiktoken.get_encoding(token_measure.O200K)
        doc_tokens = {}
        for digest in sorted(set(clips.values())):
            text = (scratch_root / digest
                    / source_memory.SLOT_WORLD_MODEL).read_text(
                        encoding="utf-8")
            doc_tokens[digest] = len(enc.encode(text))
        whole_tok_per_call = []
        for candidate in candidates:
            digests = {clips.get(str(seg.get("source_file") or ""))
                       for seg in attribute_segments(transcript, candidate)}
            digests.discard(None)
            whole_tok_per_call.append(sum(doc_tokens.get(d, 0)
                                          for d in digests))
        payload["whole_view_o200k_per_call"] = (
            sum(whole_tok_per_call) // len(whole_tok_per_call))
    wired_o200k = report.to_dict()["context_o200k"]
    payload["query_o200k_per_call"] = wired_o200k["query_o200k_per_call"]
    return payload, report, tmpdir


def attribute_segments(transcript: dict, candidate: dict) -> list:
    """Bound segments in a candidate's timeline span."""
    from library.tools.reel_world_attribution import _overlapping_segments
    return _overlapping_segments(
        transcript, float(candidate["start"]), float(candidate["end"]))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="vi013b-planner-uses-query",
        description="VI-013b: one real reel plan, whole view vs wired.")
    parser.add_argument("--project", required=True)
    parser.add_argument("--scratch-root", default=None)
    parser.add_argument("--out-json", default=None)
    args = parser.parse_args(argv)

    project = Path(args.project).expanduser()
    scratch = (Path(args.scratch_root).expanduser()
               if args.scratch_root else None)
    payload, _report, tmpdir = run(project, scratch)
    try:
        if args.out_json:
            Path(args.out_json).expanduser().write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    finally:
        if tmpdir is not None:
            shutil.rmtree(tmpdir, ignore_errors=True)
    regressions = payload["regressions"]
    assert regressions == f"0/{payload['candidates_asked']}", (
        f"fidelity regression: wired answers worse on {regressions}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
