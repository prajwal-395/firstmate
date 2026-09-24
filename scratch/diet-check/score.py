#!/usr/bin/env python3
"""Score diet-vs-full answers. Reads scratch/diet-check/results.json,
prints a per-window comparison table. Human richness read done separately.
"""
import json
import sys
from pathlib import Path

WORKTREE = Path(__file__).resolve().parents[2]
RESULTS = WORKTREE / "scratch" / "diet-check" / (
    "results_r2.json" if "--round2" in sys.argv[1:] else "results.json")


def segsum(secs, wstart, wend):
    if not secs:
        return "none"
    parts = []
    for s in secs:
        try:
            parts.append(f"[{s['start']}-{s['end']}]")
        except Exception:
            parts.append("?")
    return f"n={len(secs)} " + ",".join(parts)


def main():
    res = json.loads(RESULTS.read_text(encoding="utf-8"))
    wins = sorted({v["window"] for v in res["windows"].values()})
    for w in wins:
        rows = [v for v in res["windows"].values() if v["window"] == w]
        meta = rows[0]
        print(f"{'='*100}\n{w} {meta['project']} {meta['source']} "
              f"[{meta['start']}-{meta['end']}] shot={meta['shot']}")
        for arm in ("full", "diet"):
            for v in sorted([r for r in rows if r["arm"] == arm],
                            key=lambda r: r["run"]):
                p = v["parsed"]
                if not p:
                    print(f"  {arm} r{v['run']}: PARSE-FAIL "
                          f"(wall={v['wall']}s raw={v['raw_chars']}ch "
                          f"path={v.get('prompt_path')})")
                    continue
                acts = p.get("actions") or []
                sc = p.get("scene") or []
                cam = p.get("camera") or []
                asm = p.get("assessment") or {}
                types = ",".join(sorted({s.get("type", "?") for s in sc}))
                modes = ",".join(sorted({c.get("mode", "?") for c in cam}))
                print(f"  {arm} r{v['run']}: wall={v['wall']}s "
                      f"act={segsum(acts, 0, 0)} scene={segsum(sc, 0, 0)} "
                      f"cam={segsum(cam, 0, 0)} path={v.get('prompt_path')}")
                print(f"    scene_type=[{types}] cam_mode=[{modes}] "
                      f"content={asm.get('content_type', '?')}")
                for a in acts:
                    print(f"    A: {a.get('action', '?')} | cue: "
                          f"{a.get('speech_cue')} | body: "
                          f"{a.get('body_language', '?')}")
                for s in sc:
                    print(f"    S: {s.get('location', '?')} | "
                          f"{s.get('lighting', '?')} | feats={s.get('notable_features')}")
                for c in cam:
                    print(f"    C: {c.get('framing', '?')} | "
                          f"{c.get('stability', '?')} | {c.get('movement', '?')}")


if __name__ == "__main__":
    main()
