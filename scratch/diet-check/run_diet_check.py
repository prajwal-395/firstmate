#!/usr/bin/env python3
"""Diet-vs-full quality check: gemma4 12B window prompt comparison.

Compares the pre-1390 canonical PROMPT_WINDOW_ALL (FULL literal below,
taken verbatim from git commit 2f6c3cad^) against the current
PROMPT_WINDOW_ALL_COMPACT on identical 10 s source excerpts, same
settings (temp pinned 0.1 inside VisionAnalyzer.analyze), 2 runs each.

Usage (from worktree root):
  bin/vep scratch/diet-check/run_diet_check.py        # run all missing calls
  bin/vep scratch/diet-check/run_diet_check.py --cut-only   # cut excerpts only
  bin/vep scratch/diet-check/run_diet_check.py --report     # print table from results

Results accumulate in scratch/diet-check/results.json (resume-safe).
Excerpts are 720p + AAC, cut with the exact extract_video_clips recipe.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

WORKTREE = Path(__file__).resolve().parents[2]
SCRATCH = WORKTREE / "scratch" / "diet-check"
EXCERPTS = SCRATCH / "excerpts"
ROUND2 = "--round2" in sys.argv[1:]
RESULTS = SCRATCH / ("results_r2.json" if ROUND2 else "results.json")
FOOTAGE = Path("/Users/prajwal/Documents/work_stuff/Lucie consulting"
               "/Social Media/podcast media")

sys.path.insert(0, str(WORKTREE))

# Full canonical prompt, verbatim from 2f6c3cad^
# (git show 2f6c3cad^:library/tools/analysis/vision_pipeline_v3.py).
PROMPT_WINDOW_ALL_FULL = """This is a {window_dur:.0f}-second segment (seconds {window_start:.0f} to {window_end:.0f}) of a {duration:.0f}-second video clip.
{transcript_line}
This video clip carries its AUDIO TRACK - measured on mlx-vlm 0.7.2,
gemma4-unified accepts audio alongside video (`generate` takes
`audio=` with the audio marker in the prompt, and the checkpoint
carries `embed_audio` weights), so listen to HOW speech is delivered
(pace, effort, pauses, visible effort). The only WORDS that exist are
the transcript text above (if any). NEVER quote speech: do not put
words in quotation marks and do not attribute utterances to the person
on screen. `speech_cue` describes delivery - pace, effort, pauses,
mouth movement, gestures while talking - never words.

Answer all four parts in ONE response. Part 1 - actions: for each
distinct action or behavior change in this segment, report what the
person is physically doing, observable speech delivery cues (if
speaking): mouth movement, apparent volume, gestures while talking,
and observable facial expression and body language: posture, hand
position, head orientation, facial muscle state.

Part 2 - scene: pre-detected scene boundaries (from automated visual
analysis) within this segment: {boundaries}. Describe the physical
environment for each part of this segment, and identify any additional
subtle environment changes the detector may have missed (e.g.,
significant lighting shifts within the same location).

Part 3 - camera: describe the camera behavior in this segment. Create
a new entry ONLY when the camera mode meaningfully changes (e.g.,
static to walking, selfie to rear-facing, close-up to wide shot,
stable to shaky).

Part 4 - assessment: classify what this segment shows. content_type:
one of person_talking_to_camera, scenery, action_sequence,
multiple_people, object_showcase, transition. primary_subject_visible:
time ranges where the main person is visible.

Respond in this EXACT JSON format (no markdown, no explanation, no extra text):
{{
  "actions": [
    {{"start": <seconds>, "end": <seconds>, "action": "<physical description of what they are doing>", "speech_cue": "<observable speech delivery or null if not speaking>", "body_language": "<observable posture, gestures, facial expression>"}}
  ],
  "scene": [
    {{"start": <seconds>, "end": <seconds>, "location": "<specific physical place>", "type": "<indoor|outdoor|vehicle|mixed>", "lighting": "<observable lighting conditions>", "notable_features": ["<visible sign text>", "<visible structures or landmarks>"]}}
  ],
  "camera": [
    {{"start": <seconds>, "end": <seconds>, "mode": "<selfie|handheld|mounted|panning|tracking>", "framing": "<close-up|medium|wide>", "stability": "<description of how steady or shaky>", "movement": "<stationary|walking|panning_left|panning_right|tilting_up|tilting_down|zooming_in|zooming_out>"}}
  ],
  "assessment": {{"content_type": "type_here", "primary_subject_visible": [[<start>, <end>]]}}
}}

Rules:
- All timestamps must be within [{window_start}, {window_end}].
- Describe ONLY what is physically visible - "frowning, arms crossed" not "feeling upset"; no mood, atmosphere, or interpretation.
- If one continuous action spans the whole window, return a single action entry.
- If the setting never changes, return a single scene entry spanning the window.
- If the camera stays in one mode the whole window, return a single camera entry.
- speech_cue should be null (not the string "null") if the person is not speaking.
- mode: selfie (front-facing, subject holding camera), handheld (rear-facing, hand-held),
  mounted (tripod/fixed), panning (rotating), tracking (following a subject).
- No quotation marks anywhere in your answer: quoted words cannot be
  verified against the transcript, and a deterministic check strips them.
- notable_features should include any readable text on signs or buildings."""

# (window_id, project, source file, start, end, shot class)
WINDOWS = [
    ("W01", "geo-podcast", "LC4930.MXF", 25, 35, "talking head"),
    ("W02", "geo-podcast", "LC4930.MXF", 60, 70, "motion"),
    ("W03", "geo-podcast", "LC4931.MXF", 55, 65, "talking head"),
    ("W04", "geo-podcast", "LC4932.MXF", 60, 70, "motion"),
    ("W05", "geo-podcast", "LC4932.MXF", 95, 105, "multi-person edge"),
    ("W06", "geo-podcast", "LC4932.MXF", 1695, 1705, "talking head"),
    ("W07", "podcast-roughcut", "LCATL0011.MXF", 55, 65, "talking head"),
    ("W08", "podcast-roughcut", "LCATL0012.MXF", 195, 205, "talking head"),
    ("W09", "podcast-roughcut", "LCATL0013.MXF", 60, 70, "motion"),
    ("W10", "podcast-roughcut", "LCATL0013.MXF", 295, 305, "multi-person edge"),
    ("W11", "podcast-roughcut", "LCATL0014.MXF", 95, 105, "multi-person edge"),
    ("W12", "podcast-roughcut", "LCATL0014.MXF", 495, 505, "motion"),
]

ARMS = ("full", "diet")
RUNS = (1, 2)

DURATIONS = {
    "LC4930.MXF": 225.225, "LC4931.MXF": 287.621, "LC4932.MXF": 4941.103,
    "LCATL0011.MXF": 214.548, "LCATL0012.MXF": 288.955,
    "LCATL0013.MXF": 4099.596, "LCATL0014.MXF": 838.004,
}


def cut_excerpt(src, start, end, out):
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        return
    # Fast input seek (keyframe) + exact output trim: identical bytes
    # policy to extract_video_clips (ultrafast/crf23/720p/aac), without
    # decoding the whole file from zero for late windows.
    pre = max(0, start - 5)
    cmd = ["ffmpeg", "-y", "-ss", str(pre), "-i", str(src),
           "-ss", str(start - pre), "-t", str(end - start),
           "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
           "-vf", "scale=-2:min(720\\,ih)",
           "-c:a", "aac", "-loglevel", "error", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0 or not out.exists():
        raise RuntimeError(f"excerpt cut failed: {r.stderr[-500:]}")


def heavy_work_check():
    r = subprocess.run(
        ["ps", "aux"], capture_output=True, text=True, encoding="utf-8")
    hits = [ln for ln in r.stdout.splitlines()
            if any(k in ln.lower() for k in
                   ("mlx", "whisper", "ffmpeg", "ollama", "xcodebuild"))
            and "ps aux" not in ln and "grep" not in ln]
    other = [ln for ln in hits if "run_diet_check" not in ln]
    print(f"  [heavy-work ps check: {len(other)} other heavy jobs] "
          + ("CLEAR" if not other else "; ".join(other[:3])), flush=True)
    return not other


def load_results():
    if RESULTS.exists():
        return json.loads(RESULTS.read_text(encoding="utf-8"))
    return {"windows": {}, "meta": {}}


def save_results(res):
    RESULTS.write_text(json.dumps(res, indent=1), encoding="utf-8")


def main():
    import library.tools.analysis.vision_pipeline_v3 as vp

    only_cut = "--cut-only" in sys.argv[1:]
    only_report = "--report" in sys.argv[1:]

    # Sanity: compact prompt still the landed one.
    assert "max 12 words" in vp.PROMPT_WINDOW_ALL_COMPACT
    assert vp.MAX_TOKENS["window_all_compact"] == 600
    assert vp.MAX_TOKENS["window_all"] == 2000

    res = load_results()
    if only_report:
        print_report(res)
        return

    # Cut all excerpts first (cheap, no model).
    for wid, proj, fname, start, end, shot in WINDOWS:
        cut_excerpt(FOOTAGE / fname, start, end,
                    EXCERPTS / f"{wid}_{fname.replace('.MXF','')}_{start}-{end}.mp4")
    print(f"excerpts ready in {EXCERPTS}")
    if only_cut:
        return

    from mlx_vlm import load
    print("loading model (one job, low priority)...")
    heavy_work_check()
    model, proc = load(vp.MODEL_ID)
    analyzer = vp.VisionAnalyzer(model, proc, step_id="diet_quality_check")
    print(f"model {vp.MODEL_ID} loaded")

    transcript_line = "\n(No speech in this segment.)"
    for wid, proj, fname, start, end, shot in WINDOWS:
        dur = DURATIONS[fname]
        boundaries = vp._scene_boundaries_text(None, start=start, end=end)
        excerpt = str(EXCERPTS / f"{wid}_{fname.replace('.MXF','')}_{start}-{end}.mp4")
        args = {"window_start": start, "window_end": end,
                "window_dur": end - start, "duration": dur,
                "transcript_line": transcript_line,
                "boundaries": boundaries}
        prompts = {
            "full": PROMPT_WINDOW_ALL_FULL.format(**args),
            "diet": vp.PROMPT_WINDOW_ALL_COMPACT.format(**args),
        }
        fallback_prompt = vp.PROMPT_WINDOW_ALL.format(**args)
        max_tokens = {"full": vp.MAX_TOKENS["window_all"],
                      "diet": vp.MAX_TOKENS["window_all_compact"]}
        for arm in ARMS:
            for run in RUNS:
                key = f"{wid}/{arm}/r{run}"
                if key in res["windows"]:
                    print(f"skip {key} (done)")
                    continue
                heavy_work_check()
                t0 = time.time()
                if ROUND2 and arm == "diet":
                    # Faithful production path: compact -> repair ->
                    # full fallback, one wall clock per window.
                    parsed, raw, elapsed, path = vp.run_window_call(
                        analyzer, prompts["diet"], fallback_prompt,
                        excerpt, excerpt, f"{wid} diet r{run}")
                    wall = time.time() - t0
                else:
                    parsed, raw, elapsed = analyzer.analyze_with_retry(
                        prompts[arm], vp.parse_json_object, video=excerpt,
                        audio=excerpt, max_tokens=max_tokens[arm],
                        label=f"{wid} {arm} r{run}")
                    if arm == "diet":
                        parsed = vp._expand_or_canonical(parsed)
                    wall = time.time() - t0
                    path = None
                res["windows"][key] = {
                    "window": wid, "project": proj, "source": fname,
                    "start": start, "end": end, "shot": shot,
                    "arm": arm, "run": run,
                    "elapsed_model": round(elapsed, 1),
                    "wall": round(wall, 1),
                    "raw_chars": len(raw or ""),
                    "parsed": parsed,
                    "raw": raw,
                    "prompt_path": path,
                }
                save_results(res)
                na = len((parsed or {}).get("actions", [])) if parsed else -1
                print(f"done {key}: wall={wall:.1f}s model={elapsed:.1f}s "
                      f"actions={na} raw_chars={len(raw or '')} "
                      f"path={path}", flush=True)

    print_report(res)


def print_report(res):
    import statistics
    for arm in ARMS:
        walls = [v["wall"] for v in res["windows"].values() if v["arm"] == arm]
        if walls:
            print(f"{arm}: n={len(walls)} median_wall={statistics.median(walls):.1f}s "
                  f"min={min(walls):.1f} max={max(walls):.1f}")


if __name__ == "__main__":
    main()
