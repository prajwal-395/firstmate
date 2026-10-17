"""PE-AV audiovisual retrieval micro-benchmark (AUD-059).

Same-source cross-modal matching over four 30 s Apple 001 clips: the
only scored claim is whether a clip's audio retrieves its own video
(and vice versa) - truth is the source identity, no human judgement.
Text queries are reported descriptively (ranked clips per query) with
no accuracy claim, because the corpus has no query-level gold.

Usage (foreground, under a model-memory profile)::

    bin/vep -m library.tools.heavy_work_lock run --owner vep-mu-sound \\
        --profile semantics.analyse:model_memory -- \\
        ./bin/vep docs/benchmarks/pe_av_bench.py \\
            --clips-dir /tmp/fm-vep-mu-sound/av-clips \\
            --out-dir docs/benchmarks

`timm` (the PE vision tower) loads from an isolated `--pydeps` dir so
the shared venv is never mutated; the checkpoint reads from the
lane's scratch HF cache. Both are deleted after the benchmark unless
the adoption decision keeps them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import resource
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

TEXT_QUERIES = (
    "outdoor ambient sound with passing traffic",
    "a person speaking to camera",
    "music playing in the background",
    "birds chirping in trees",
)


def _peak_rss_gb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e9


def _mapped_checkpoint_gb(cache_dir: Path, repo: str) -> float:
    total = 0
    for path in (cache_dir / f"models--{repo.replace('/', '--')}").rglob(
        "*.safetensors"
    ):
        total += path.stat().st_size
    return total / 1e9


def _cold_start_seconds(interpreter: str, repo: str, pydeps: str,
                        cache: str) -> float:
    program = (
        "import os, sys, time; "
        f"sys.path.insert(0, {pydeps!r}); "
        f"os.environ['HF_HUB_CACHE'] = {cache!r}; "
        "os.environ['HF_HUB_OFFLINE'] = '1'; "
        "t0 = time.perf_counter(); "
        "from transformers import AutoProcessor, PeAudioVideoModel; "
        f"p = AutoProcessor.from_pretrained({repo!r}); "
        f"m = PeAudioVideoModel.from_pretrained({repo!r}); "
        "m.eval(); "
        "print(time.perf_counter() - t0)"
    )
    proc = subprocess.run(
        [interpreter, "-c", program], capture_output=True, check=True,
        encoding="utf-8", timeout=1200, cwd=str(REPO_ROOT),
    )
    return float(proc.stdout.strip().splitlines()[-1])


def run(clips_dir: Path, out_dir: Path, repo: str, pydeps: Path,
        cache_dir: Path, interpreter: str, revision: str, tag: str) -> dict:
    import os

    import torch

    sys.path.insert(0, str(pydeps))
    os.environ["HF_HUB_CACHE"] = str(cache_dir)
    os.environ["HF_HUB_OFFLINE"] = "1"
    clips_dir = Path(clips_dir).resolve()
    out_dir = Path(out_dir).resolve()
    clips = sorted(clips_dir.glob("*.mp4"))
    assert len(clips) >= 2, "need at least two AV clips"

    wall_start = time.perf_counter()
    from transformers import AutoProcessor, PeAudioVideoModel

    processor = AutoProcessor.from_pretrained(repo)
    model = PeAudioVideoModel.from_pretrained(repo)
    model.eval()
    load_seconds = time.perf_counter() - wall_start

    audio_files = []
    for clip in clips:
        wav = clip.with_suffix(".wav")
        if not wav.is_file():
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-i", str(clip), "-map",
                 "0:a:0", "-ac", "1", "-ar", "16000", str(wav)],
                check=True, timeout=300,
            )
        audio_files.append(str(wav))

    embed_seconds = 0.0
    audio_embeds, video_embeds, joint_embeds = [], [], []
    embeds_path = out_dir / f"2026-10-16-pe-av-{tag}.embeds.json"
    cached = {}
    if embeds_path.is_file():
        cached = json.loads(embeds_path.read_text(encoding="utf-8"))
    with torch.inference_mode():
        for clip, wav in zip(clips, audio_files):
            if clip.stem in cached:
                entry = cached[clip.stem]
                audio_embeds.append(torch.tensor(entry["audio"]))
                video_embeds.append(torch.tensor(entry["video"]))
                joint_embeds.append(torch.tensor(entry["joint"]))
                embed_seconds += entry["seconds"]
                continue
            import numpy as _np
            from PIL import Image as _Image

            frame_dir = clip.with_suffix(".frames")
            frames = _np.stack([
                _np.asarray(_Image.open(frame_dir / f"f{i:03d}.png"))
                for i in range(1, 17)])
            tick = time.perf_counter()
            inputs = processor(videos=[frames], audio=[wav],
                               return_tensors="pt", padding=True)
            audio_embeds.append(model.get_audio_embeds(
                inputs["input_values"],
                inputs.get("padding_mask"))[0].detach())
            video_outputs = model.video_model.video_encoder(
                pixel_values_videos=inputs["pixel_values_videos"],
                return_dict=True)
            video_embeds.append(model.video_model.video_head(
                video_outputs.pooler_output)[0].detach())
            joint_embeds.append(model.get_audio_video_embeds(
                inputs["input_values"],
                inputs["pixel_values_videos"]).audio_video_embeds[0].detach())
            clip_seconds = time.perf_counter() - tick
            embed_seconds += clip_seconds
            cached[clip.stem] = {
                "audio": audio_embeds[-1].tolist(),
                "video": video_embeds[-1].tolist(),
                "joint": joint_embeds[-1].tolist(),
                "seconds": clip_seconds,
            }
            embeds_path.parent.mkdir(parents=True, exist_ok=True)
            embeds_path.write_text(json.dumps(cached, sort_keys=True)
                                   + "\n", encoding="utf-8")
        queries = processor(text=list(TEXT_QUERIES), return_tensors="pt",
                            padding=True)
        text_embeds = model.get_text_audio_video_embeds(
            queries["input_ids"],
            queries.get("attention_mask")).detach()

    import torch.nn.functional as norm

    audio = norm.normalize(torch.stack(audio_embeds), dim=1)
    video = norm.normalize(torch.stack(video_embeds), dim=1)
    joint = norm.normalize(torch.stack(joint_embeds), dim=1)
    text = norm.normalize(text_embeds, dim=1)

    names = [c.stem for c in clips]
    audio_video = (audio @ video.T).tolist()
    video_audio = (video @ audio.T).tolist()
    joint_text = (joint @ text.T).tolist()

    def top1(matrix):
        hits = sum(
            max(range(len(row)), key=lambda j: row[j]) == i
            for i, row in enumerate(matrix))
        return hits / len(matrix)

    margins = []
    for i in range(len(names)):
        cross = max(
            audio_video[i][j] for j in range(len(names)) if j != i)
        margins.append(audio_video[i][i] - cross)

    total_wall = time.perf_counter() - wall_start
    result = {
        "clips": names,
        "audio_to_video_top1": top1(audio_video),
        "video_to_audio_top1": top1(video_audio),
        "same_source_margin_mean": sum(margins) / len(margins),
        "audio_video_cosine": audio_video,
        "text_query_rankings": [
            {"query": query,
             "ranked_clips": sorted(names,
                                    key=lambda n, row=row: -row[names.index(n)])}
            for query, row in zip(TEXT_QUERIES, zip(*joint_text))
        ],
        "resources": {
            "cold_start_seconds": _cold_start_seconds(
                interpreter, repo, str(pydeps), str(cache_dir)),
            "load_seconds": load_seconds,
            "embed_seconds_per_clip": embed_seconds / len(clips),
            "total_wall_seconds": total_wall,
            "peak_rss_gb": _peak_rss_gb(),
            "mapped_weights_gb": _mapped_checkpoint_gb(cache_dir, repo),
            "precision": "fp32",
            "device": "cpu",
            "license": "Apache-2.0",
        },
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    result_path = out_dir / f"2026-10-16-pe-av-{tag}.result.json"
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True)
                           + "\n", encoding="utf-8")

    def metric(value, unit, dimension):
        return {"value": value, "unit": unit, "dimension": dimension}

    resources = result["resources"]
    report = {
        "format": "ren_evaluation_report/1",
        "revision": revision,
        "version": {"id": "pe-av-small-16f-fp32-cpu", "role": "candidate",
                    "models": {"retrieval": repo, "precision": "fp32",
                               "device": "cpu", "license": "Apache-2.0"}},
        "modality": "audiovisual_retrieval",
        "split": "calibration",
        "metrics": {
            "av_match_accuracy_audio_to_video": metric(
                result["audio_to_video_top1"],
                "fraction of 4 clips whose audio retrieves its own video",
                "quality"),
            "av_match_accuracy_video_to_audio": metric(
                result["video_to_audio_top1"],
                "fraction of 4 clips whose video retrieves its own audio",
                "quality"),
            "same_source_margin_mean": metric(
                result["same_source_margin_mean"],
                "mean same-source cosine minus best cross-source cosine",
                "quality"),
            "cold_start_seconds": metric(
                resources["cold_start_seconds"],
                "seconds, fresh interpreter, load only", "latency"),
            "embed_seconds_per_clip": metric(
                resources["embed_seconds_per_clip"],
                "seconds per 30 s clip (16 frames + audio)", "latency"),
            "peak_rss_gb": metric(resources["peak_rss_gb"],
                                  "GB process peak RSS (weights are mmap)",
                                  "memory"),
            "mapped_weights_gb": metric(resources["mapped_weights_gb"],
                                        "GB weights mapped, not all resident",
                                        "memory"),
            "measured_run_cost_wall_seconds": metric(
                resources["cold_start_seconds"]
                + resources["total_wall_seconds"],
                "seconds cold plus warm", "cost"),
        },
        "evidence": [{"id": "pe-av-retrieval-result",
                      "path": result_path.name,
                      "sha256": hashlib.sha256(
                          result_path.read_bytes()).hexdigest()}],
        "selection_basis": ["calibration"],
    }
    report_path = out_dir / f"2026-10-16-pe-av-{tag}.report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True)
                           + "\n", encoding="utf-8")
    print(json.dumps(
        {"audio_to_video": result["audio_to_video_top1"],
         "video_to_audio": result["video_to_audio_top1"],
         "margin": round(result["same_source_margin_mean"], 3),
         "embed_s_per_clip": round(resources["embed_seconds_per_clip"], 1),
         "peak_rss_gb": round(resources["peak_rss_gb"], 2)}, indent=2))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clips-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--repo", default="facebook/pe-av-small-16-frame")
    parser.add_argument("--pydeps", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--interpreter", required=True)
    parser.add_argument("--revision", default="unrecorded")
    parser.add_argument("--tag", default="retrieval")
    args = parser.parse_args(argv)
    run(args.clips_dir, args.out_dir, args.repo, args.pydeps,
        args.cache_dir, args.interpreter, args.revision, args.tag)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
