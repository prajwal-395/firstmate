"""L5 live-producer calibration: detector tiers through the loop.

Decodes stills from a read-only source, runs the Vision nomination
tier and the OWLv2 named-query tier cold and warm, then executes one
`evidence_acquisition.run_acquisition` loop with the tiered detector
as its producer: merge the produced rows, requery, stop on the
answer. Writes a JSON report; prints nothing else.

Source media is never written. Frames land in the caller scratch dir
(outside the worktree). No weight is downloaded: missing cached
weights refuse with the reason instead of fetching.
"""

from __future__ import annotations

import argparse
import json
import resource
import subprocess
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from library.tools import evidence_acquisition as ea
from library.tools import requirement_graph as rg

SCHEMA_VERSION = 1


def _rss_gb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 ** 3) if sys.platform == "darwin" else peak / 1024 ** 2


def decode_frames(source: str, times: list[float], out_dir: Path) -> list[Path]:
    """One PNG per sample second via ffmpeg; source stays read-only."""
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for at in times:
        target = out_dir / f"frame-{at:07.2f}.png"
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-ss", str(at), "-i", source, "-frames:v", "1",
             str(target)],
            capture_output=True, text=True, encoding="utf-8", check=False)
        if proc.returncode != 0 or not target.is_file():
            raise RuntimeError(f"ffmpeg could not decode {source} "
                               f"at {at}s: {proc.stderr.strip()[-400:]}")
        frames.append(target)
    return frames


def time_detector(detector: str, frames: list[Path], phrases: list[str],
                  ) -> dict:
    """Cold (load plus first frame) and warm per-frame wall seconds."""
    from library.tools.analysis import open_vocabulary_detect as ovd

    cold_started = time.perf_counter()
    first = ovd.detect_image(detector, str(frames[0]), phrases)
    cold_s = time.perf_counter() - cold_started
    warm_rows = []
    for frame in frames:
        started = time.perf_counter()
        detections = ovd.detect_image(detector, str(frame), phrases)
        warm_rows.append({"frame": frame.name,
                          "wall_s": round(time.perf_counter() - started, 3),
                          "n_boxes": len(detections),
                          "phrases": sorted({row.phrase for row in detections
                                             if row.phrase})})
    return {"detector": detector, "cold_first_frame_s": round(cold_s, 3),
            "cold_n_boxes": len(first),
            "warm": warm_rows,
            "peak_rss_gb": round(_rss_gb(), 3)}


def model_precision(detector: str) -> str:
    """Recorded precision of the loaded backend, never assumed."""
    from library.tools.analysis import open_vocabulary_detect as ovd

    if detector == "vision_foreground":
        return "system (Apple Vision, no weights)"
    loaded = ovd.load_detector(detector)
    model = loaded.get("model")
    dtype = str(getattr(model, "dtype", "unknown"))
    return f"{loaded.get('model_id', detector)} dtype={dtype}"


def live_loop(frames: list[Path], phrases: list[str], times: list[float],
              ) -> dict:
    """One live plan-execute-refresh-requery loop over decoded frames.

    Each planned step runs the producer the plan selected (never a
    heavier tier wearing the cheap tier's budget): Vision nominations
    run the Vision baseline, OWLv2 steps run OWLv2. Produced rows
    merge into the model as object-detection-lane observations; the
    requery decides. Steps naming any other producer defer with the
    reason: this loop proves the detector path live, not every lane.
    """
    from library.tools.analysis import open_vocabulary_detect as ovd

    graph = rg.parse_query(f"footage where {' and '.join(phrases)} visible")
    compiled = rg.compile_graph(graph)
    model: dict = {"observations": []}
    duration = max(times) + 10.0

    def frame_for(second: float) -> str | None:
        best: str | None = None
        best_gap = float("inf")
        for frame, at in zip(frames, times):
            gap = abs(at - second)
            if gap < best_gap:
                best, best_gap = str(frame), gap
        return best if best_gap <= 5.0 else None

    produced: list[dict] = []

    def cached(**kwargs):
        return None

    def append_row(start_s: float, end_s: float, det, version: str) -> None:
        produced.append({
            "id": f"live-seg-{len(produced)}",
            "provenance_id": "prov-live-object_detection",
            "asset_id": "asset-live",
            "modality": "object_detection",
            "interval": {"clock_id": "clk",
                         "start": int(start_s * 1000),
                         "end": int(end_s * 1000)},
            "payload": {"evidence_contract": {
                "measurement_type": "object.detection",
                "value": {"label": det.phrase, "category": det.phrase,
                          "score": det.score, "box": det.box,
                          "detector": det.detector},
                "coverage_status": "complete",
                "coverage": "complete",
                "freshness": "fresh",
                "verified_status": "verified",
                "producer_version": version}}})

    def produce_vision(*, start_s: float, end_s: float,
                       budget_seconds: float, max_peak_rss_gb: float,
                       **_ignored):
        """Nominations only: regions without names are samples, never
        named answers."""
        started = time.perf_counter()
        image = frame_for((start_s + end_s) / 2)
        nominations = (ovd.detect_image("vision_foreground", image, phrases)
                       if image else [])
        return {"measured": True, "cost_seconds": time.perf_counter() - started,
                "peak_rss_gb": _rss_gb(),
                "producer_version": "apple-vision",
                "samples": len(nominations), "coverage": "complete"}

    def produce_owlv2(*, start_s: float, end_s: float,
                      budget_seconds: float, max_peak_rss_gb: float,
                      **_ignored):
        import sys as _sys

        started = time.perf_counter()
        mid = (start_s + end_s) / 2
        image = frame_for(mid)
        hits = 0
        raw = 0
        if image is not None:
            for det in ovd.detect_image("owlv2", image, phrases):
                raw += 1
                if det.phrase.strip().lower() not in {
                        p.lower() for p in phrases}:
                    continue
                append_row(start_s, end_s, det, "owlv2-live")
                hits += 1
        cost = time.perf_counter() - started
        print(f"owlv2 window [{start_s},{end_s}] mid={mid} image={image} "
              f"raw={raw} hits={hits} cost={cost:.1f}s", file=_sys.stderr)
        return {"measured": hits > 0, "cost_seconds": cost,
                "peak_rss_gb": _rss_gb(), "producer_version": "owlv2-live",
                "samples": hits, "coverage": "complete"}

    windows = [{"start_s": t, "end_s": t + 10.0, "score": 1.0}
               for t in times]
    # Exhaustive over the six targeted windows (60 s of a 139 s
    # source): the candidates are already the narrowed set, so every
    # one runs rather than an arbitrary top-k.
    plan = ea.plan_acquisition(
        compiled, model, source_digest="live", duration_seconds=duration,
        scope={"clock_id": "clk", "asset_id": "asset-live"},
        exhaustive=True,
        retrieval_candidates=windows)
    before = {key: bool(value) for key, value in plan["satisfied"].items()}

    def refresh(_old):
        model["observations"] = list(produced)
        return model

    spent, peak = 0.0, 0.0
    rounds = []
    first = ea.run_acquisition(
        plan, cached=cached, producers={"object_detection": produce_vision},
        refresh_model=refresh, max_cost_seconds=600.0)
    spent += first["acquisition"].get("cost_seconds", 0.0) or 0.0
    peak = max(peak, first["acquisition"].get("peak_rss_gb", 0.0) or 0.0)
    rounds.append(_summarize_round(1, plan, first))
    answers = _answer_status(compiled, first["requery"] or model)
    escalated = ea.plan_escalation(plan, {
        req_id: {"status": ("present" if satisfied else "inconclusive")}
        for req_id, satisfied in answers.items()})
    second = None
    if escalated["action"] == "refine":
        second = ea.run_acquisition(
            escalated, cached=cached,
            producers={"object_detection": produce_owlv2},
            refresh_model=refresh, max_cost_seconds=600.0)
        spent += second["acquisition"].get("cost_seconds", 0.0) or 0.0
        peak = max(peak, second["acquisition"].get("peak_rss_gb", 0.0) or 0.0)
        rounds.append(_summarize_round(2, escalated, second))
        answers = _answer_status(compiled, second["requery"] or model)
    gain = ea.marginal_gain_per_second(before, answers, spent)
    return {"rounds": rounds,
            "escalation_steps": len(escalated["steps"]),
            "escalation_producer": (escalated["steps"][0]["producer"]
                                    if escalated["steps"] else None),
            "landed": first["landed"] or bool(second and second["landed"]),
            "spent_s": round(spent, 3), "peak_rss_gb": round(peak, 3),
            "produced_observations": len(produced),
            "requery_satisfied": answers,
            "answers_per_second": gain["answers_per_second"],
            "utility_before": gain["utility_before"],
            "utility_after": gain["utility_after"]}


def _summarize_round(number: int, plan, report) -> dict:
    return {"round": number,
            "planned_steps": len(plan["steps"]),
            "planned_cost_s": plan["estimated_cost_seconds"],
            "planned_producer": (plan["steps"][0]["producer"]
                                 if plan["steps"] else None),
            "execution_steps": [
                {"lane": row.get("lane"), "status": row.get("status"),
                 "reason": row.get("reason"),
                 "cost_seconds": row.get("cost_seconds")}
                for row in report["acquisition"].get("steps", ())],
            "landed": report["landed"]}


def _answer_status(compiled, model) -> dict:
    from library.tools import evidence_query as eq

    status = {}
    for row in compiled["compiled"]:
        if row["kind"] == "plan":
            status[row["requirement_id"]] = (
                eq.evidence_for(model, row["plan"])["status"] == eq.PRESENT)
    return status


def verify_mode(source: str, span: tuple[float, float], statement: str,
                  memory_root: str, query_text: str) -> dict:
    """One live candidate verification through the run_acquisition
    verifier path. Builds the M2 frame sample into a scratch memory
    root (source stays read-only), asks the cached Gemma 4-bit VLM
    about the span, and runs the verdict through a verify-task plan.
    Verdicts record into the scratch root; the Project Model's
    measured rows are untouched (M8 owns verdicts)."""
    import resource as _resource

    from library.tools import footage_identity as identity
    from library.tools import source_memory as memory
    from library.tools import span_verification as span_verify

    root = Path(memory_root)
    digest = identity.fingerprint(source)["content_digest"]
    m2 = memory.build_frames(source, root=root)
    frames = memory.read_m2(digest, root)["frames"]

    graph = rg.parse_query(query_text)
    compiled = rg.compile_graph(graph)
    model: dict = {"observations": []}
    ledger: dict = {}

    def verifier(*, lane: str, start_s: float, end_s: float,
                 budget_seconds: float, **_ignored):
        picked = [{"t": float(row["t"]), "d": 0.0} for row in frames
                  if start_s <= float(row["t"]) <= end_s]
        if len(picked) < 2:
            return {"measured": False, "cost_seconds": 0.0,
                    "peak_rss_gb": 0.0, "producer_version": "span-verify",
                    "reason": "fewer than two M2 frames in the interval"}
        span = {"start": start_s, "end": end_s, "frames": picked}
        verdicts = span_verify.verify_spans(
            digest, [span], statement, root=root)
        verdict = verdicts[0]
        peak = _resource.getrusage(
            _resource.RUSAGE_SELF).ru_maxrss / (1024 ** 3)
        return {"measured": verdict.get("answer") in ("yes", "no"),
                "cost_seconds": float(verdict.get("seconds", 0.0)),
                "peak_rss_gb": round(peak, 3),
                "producer_version": str(verdict.get("model")),
                "verdict": {key: verdict.get(key) for key in
                            ("answer", "frame", "frame_t", "reason",
                             "cached", "seconds")},
                "coverage": "complete"}

    plan = ea.plan_acquisition(
        compiled, model, source_digest=digest,
        duration_seconds=span[1] + 5.0,
        scope={"clock_id": "clk", "asset_id": "asset-live",
               "start_s": span[0], "end_s": span[1]})
    report = ea.run_acquisition(
        plan, cached=lambda **kw: None,
        producers={},
        verifiers={step["lane"]: verifier for step in plan["steps"]},
        refresh_model=lambda old: model,
        ledger=ledger, max_cost_seconds=600.0)
    return {"content_digest": digest, "span_s": list(span),
            "statement": statement, "m2": {
                "reused": m2.get("reused"), "frame_count": m2.get("frame_count"),
                "timings": m2.get("timings")},
            "planned_steps": len(plan["steps"]),
            "verifications": report["verifications"],
            "landed": report["landed"]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["detect", "verify"],
                        default="detect")
    parser.add_argument("--source", required=True)
    parser.add_argument("--times", required=False, default="",
                        help="comma-separated sample seconds (detect mode)")
    parser.add_argument("--phrases", required=False, default="",
                        help="comma-separated named-object phrases")
    parser.add_argument("--frames-dir", required=False, default="")
    parser.add_argument("--out", required=True)
    parser.add_argument("--loop-only", action="store_true",
                        help="reuse decoded frames; run only the live loop")
    parser.add_argument("--span", default="",
                        help="verify span 'start,end' seconds (verify mode)")
    parser.add_argument("--statement", default="",
                        help="VLM statement (verify mode)")
    parser.add_argument("--memory-root", default="",
                        help="scratch source-memory root (verify mode)")
    parser.add_argument("--query", default="",
                        help="user query (verify mode)")
    args = parser.parse_args(argv)
    if args.mode == "verify":
        if not (args.span and args.statement and args.memory_root
                and args.query):
            raise SystemExit("verify mode needs --span, --statement, "
                             "--memory-root and --query")
        start, end = (float(part) for part in args.span.split(","))
        report = {"schema_version": SCHEMA_VERSION, "mode": "verify",
                  "source": args.source,
                  "live_verify": verify_mode(
                      args.source, (start, end), args.statement,
                      args.memory_root, args.query)}
        Path(args.out).write_text(json.dumps(report, indent=2),
                                  encoding="utf-8")
        return 0
    times = [float(part) for part in args.times.split(",")]
    phrases = [part.strip() for part in args.phrases.split(",") if part.strip()]

    frames_dir = Path(args.frames_dir)
    if args.loop_only:
        frames = [frames_dir / f"frame-{at:07.2f}.png" for at in times]
        missing = [str(path) for path in frames if not path.is_file()]
        if missing:
            raise RuntimeError(f"loop-only reuse needs decoded frames: "
                               f"missing {missing}")
        loop = live_loop(frames, phrases, times)
        report = {"schema_version": SCHEMA_VERSION, "loop_only": True,
                  "source": args.source, "times_s": times,
                  "phrases": phrases, "live_loop": loop}
    else:
        frames = decode_frames(args.source, times, frames_dir)
        vision = time_detector("vision_foreground", frames, phrases)
        vision_precision = model_precision("vision_foreground")
        owlv2 = time_detector("owlv2", frames, phrases)
        owlv2_precision = model_precision("owlv2")
        loop = live_loop(frames, phrases, times)
        report = {
            "schema_version": SCHEMA_VERSION,
            "source": args.source, "times_s": times, "phrases": phrases,
            "vision": {**vision, "precision": vision_precision},
            "owlv2": {**owlv2, "precision": owlv2_precision},
            "live_loop": loop,
        }
    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
