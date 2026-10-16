"""Measure candidate visual encoders over a frozen retrieval set.

This report-only utility ranks the cached M2 frame JPEGs from the existing
geo-podcast frame index. It does not write to the project or change the
production index. PE Core uses Meta's upstream ``perception_models`` checkout
on ``PYTHONPATH``; CLIP reuses the production image embeddings; WeMM-2B uses
the official Tencent snapshot through sentence-transformers.

Example::

    bin/vep library/tools/visual_retrieval_candidate_benchmark.py \
      --project /path/to/geo-podcast --model PE-Core-S16-384 \
      --set library/tools/visual_retrieval_calib_extra.json \
      --output /tmp/pe-core-s-calib.json
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image

from library.tools.retrieval_eval import Corpus

CLIP_MODEL = "CLIP-ViT-L14"
WEMM_MODEL = "WeMM-2B"
WEMM_FULL_DIM = 2048
CORPUS_SET = Path(__file__).with_name("visual_retrieval_heldout_set.json")


def _wemm_snapshot() -> Path:
    """Resolve the pinned official WeMM-2B snapshot from the local HF cache."""
    cache = Path(
        os.environ.get("VEP_MU_HF_CACHE", Path.home() / ".cache" / "vep-mu-retrieval")
    )
    snapshots = sorted(
        (cache / "models--tencent--WeMM-Embedding-2B" / "snapshots").glob("*")
    )
    if not snapshots:
        raise ValueError("no local WeMM-2B snapshot; download it first")
    return snapshots[0]


def _clip_groups(frames: list[dict], size: int) -> list[list[int]]:
    """Partition frame positions into non-overlapping consecutive groups."""
    if size < 1:
        raise ValueError("clip size must be at least 1")
    return [
        list(range(i, min(i + size, len(frames)))) for i in range(0, len(frames), size)
    ]


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument(
        "--model",
        required=True,
        help="CLIP-ViT-L14, WeMM-2B, or an official PE Core config name",
    )
    parser.add_argument("--set", dest="eval_set", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--wemm-dim",
        type=int,
        default=WEMM_FULL_DIM,
        help="WeMM embedding width; 256 selects the MRL truncation",
    )
    parser.add_argument(
        "--save-image-matrix",
        type=Path,
        default=None,
        help="cache image vectors to .npy",
    )
    parser.add_argument(
        "--load-image-matrix",
        type=Path,
        default=None,
        help="reuse cached image vectors instead of encoding",
    )
    parser.add_argument(
        "--video-clip-frames",
        type=int,
        default=0,
        help="group N consecutive frames into one short-clip video unit (0 = stills)",
    )
    parser.add_argument(
        "--pe-image-size",
        type=int,
        default=0,
        help="override PE input resolution (0 = checkpoint native)",
    )
    return parser.parse_args()


def _mrl_truncate(matrix: np.ndarray, dim: int) -> np.ndarray:
    """Truncate to the first `dim` MRL dimensions and renormalize rows."""
    truncated = np.asarray(matrix)[..., :dim].astype(np.float64)
    norms = np.linalg.norm(truncated, axis=1, keepdims=True)
    return (truncated / np.maximum(norms, 1e-12)).astype(np.float32)


def _load_wemm(
    frames: list[dict], dim: int, cached: np.ndarray | None
) -> tuple[np.ndarray, object, float, float]:
    """Encode stills with official WeMM-2B (BF16, MPS) or reuse a cache."""
    from sentence_transformers import SentenceTransformer

    started = time.perf_counter()
    model = SentenceTransformer(str(_wemm_snapshot()), trust_remote_code=True)
    load_seconds = time.perf_counter() - started
    if cached is not None:
        if cached.shape[0] != len(frames):
            raise ValueError("cached matrix and frame count differ")
        full = cached
        image_seconds = 0.0
    else:
        images = [Image.open(frame["file"]).convert("RGB") for frame in frames]
        started = time.perf_counter()
        full = np.asarray(
            model.encode_document(images, batch_size=8, normalize_embeddings=True)
        )
        image_seconds = time.perf_counter() - started
    if dim != WEMM_FULL_DIM:
        full = _mrl_truncate(full, dim)

    def encode_text(texts: list[str]) -> np.ndarray:
        vectors = np.asarray(model.encode_query(texts, normalize_embeddings=True))
        if dim != WEMM_FULL_DIM:
            vectors = _mrl_truncate(vectors, dim)
        return vectors

    return full, encode_text, load_seconds, image_seconds


def _load_encoder(
    model_name: str,
    project: Path,
    frames: list[dict],
    device: torch.device,
    pe_image_size: int = 0,
):
    """Return image vectors, text encoder, and measured load/encode times."""
    if model_name == CLIP_MODEL:
        from library.tools.analysis.footage_frames import (
            FRAME_EMBEDDINGS_FILE,
            _load_clip,
        )

        started = time.perf_counter()
        _, encode_text, _ = _load_clip()
        load_seconds = time.perf_counter() - started
        matrix = np.load(
            project
            / "pipeline_output"
            / "scratch"
            / "footage_index"
            / FRAME_EMBEDDINGS_FILE
        )
        if matrix.shape[0] != len(frames):
            raise ValueError("CLIP matrix and frame index lengths differ")
        return matrix, encode_text, load_seconds, 0.0, 224

    # This optional upstream dependency is used only for the isolated bakeoff.
    from core.vision_encoder import pe, transforms

    started = time.perf_counter()
    model = pe.CLIP.from_config(model_name, pretrained=True).eval().to(device)
    load_seconds = time.perf_counter() - started
    input_size = pe_image_size or model.image_size
    image_transform = transforms.get_image_transform(input_size)
    tokenizer = transforms.get_text_tokenizer(model.context_length)

    image_vectors = []
    started = time.perf_counter()
    with torch.inference_mode():
        for offset in range(0, len(frames), 8):
            batch = frames[offset : offset + 8]
            images = torch.stack(
                [
                    image_transform(Image.open(frame["file"]).convert("RGB"))
                    for frame in batch
                ]
            ).to(device)
            image_vectors.append(
                model.encode_image(images, normalize=True).float().cpu().numpy()
            )
    image_seconds = time.perf_counter() - started

    def encode_text(texts: list[str]) -> np.ndarray:
        tokens = tokenizer(texts).to(device)
        with torch.inference_mode():
            return model.encode_text(tokens, normalize=True).float().cpu().numpy()

    return (
        np.concatenate(image_vectors),
        encode_text,
        load_seconds,
        image_seconds,
        input_size,
    )


def _positive_frame_indices(
    query: dict, frames: list[dict], corpus: Corpus, pad_seconds: float
) -> set[int]:
    positive = query.get("positive_frames", {})
    ambiguous = query.get("ambiguous_frames", {})
    relevant = set()
    for index, frame in enumerate(frames):
        asset = corpus.name_of_clip.get(frame["clip_id"])
        moment = float(frame["t"])
        if asset in positive and any(
            float(t) - pad_seconds <= moment <= float(t) + pad_seconds
            for t in positive[asset]
        ):
            relevant.add(index)
        if asset in ambiguous and any(
            float(t) - pad_seconds <= moment <= float(t) + pad_seconds
            for t in ambiguous[asset]
        ):
            relevant.discard(index)
    return relevant


def _query_metrics(
    query: dict,
    similarities: np.ndarray,
    frames: list[dict],
    corpus: Corpus,
    pad_seconds: float,
) -> dict:
    order = np.argsort(-similarities)
    relevant = _positive_frame_indices(query, frames, corpus, pad_seconds)
    top = []
    for rank, index in enumerate(order[:10], 1):
        frame = frames[int(index)]
        top.append(
            {
                "rank": rank,
                "asset": corpus.name_of_clip.get(frame["clip_id"]),
                "time": float(frame["t"]),
                "score": float(similarities[index]),
                "relevant": int(index) in relevant,
            }
        )

    result = {
        "id": query["id"],
        "query": query["query"],
        "positive_frame_count": len(relevant),
        "top10": top,
        "negative": not query.get("positive_frames")
        and query.get("expect") != "refused",
        "temporal_recall_complete_event": None,
    }
    for k in (1, 5, 10):
        hits = sum(row["relevant"] for row in top[:k])
        result[f"recall_at_{k}"] = hits / len(relevant) if relevant else None
        result[f"precision_at_{k}"] = hits / k
        result[f"hit_at_{k}"] = bool(hits)
    return result


def main() -> None:
    args = _arguments()
    project = args.project.resolve()
    index_dir = project / "pipeline_output" / "scratch" / "footage_index"
    index = json.loads((index_dir / "frame_index.json").read_text(encoding="utf-8"))
    eval_set = json.loads(args.eval_set.read_text(encoding="utf-8"))
    corpus_set = json.loads(CORPUS_SET.read_text(encoding="utf-8"))
    corpus = Corpus(str(project), corpus_set)
    all_frames = index["frames"]
    held_out_assets = set(eval_set.get("held_out_assets", []))
    selected = (
        [
            corpus.name_of_clip.get(frame["clip_id"]) in held_out_assets
            for frame in all_frames
        ]
        if held_out_assets
        else [True] * len(all_frames)
    )
    frames = [frame for frame, keep in zip(all_frames, selected) if keep]
    queries = [
        query
        for query in eval_set["queries"]
        if query.get("family", "visual") == "visual"
    ]
    if not all_frames or not frames or not queries:
        raise ValueError("evaluation requires indexed frames and visual queries")

    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    if args.model == WEMM_MODEL:
        cached = np.load(args.load_image_matrix) if args.load_image_matrix else None
        all_vectors, encode_text, load_seconds, image_seconds = _load_wemm(
            all_frames, args.wemm_dim, cached
        )
        input_resolution = "variable (VLM native)"
    else:
        all_vectors, encode_text, load_seconds, image_seconds, input_resolution = (
            _load_encoder(args.model, project, all_frames, device, args.pe_image_size)
        )
    vectors = all_vectors[np.asarray(selected, dtype=bool)]
    if args.save_image_matrix:
        args.save_image_matrix.parent.mkdir(parents=True, exist_ok=True)
        np.save(args.save_image_matrix, np.asarray(all_vectors))
    started = time.perf_counter()
    text_vectors = encode_text([query["query"] for query in queries])
    text_seconds = time.perf_counter() - started
    if vectors.shape[0] != len(frames):
        raise ValueError("embedding matrix and selected frame count differ")

    pad_seconds = float(eval_set.get("scoring", {}).get("frame_pad_seconds", 5.0))
    started = time.perf_counter()
    rows = [
        _query_metrics(query, vectors @ text_vectors[i], frames, corpus, pad_seconds)
        for i, query in enumerate(queries)
    ]
    rank_seconds = time.perf_counter() - started
    positives = [
        row for row in rows if not row["negative"] and row["positive_frame_count"]
    ]
    quality_keys = [
        f"{metric}_at_{k}"
        for k in (1, 5, 10)
        for metric in ("recall", "precision", "hit")
    ]
    quality = {
        key: round(
            float(np.mean([row[key] for row in positives if row[key] is not None])), 4
        )
        for key in quality_keys
    }
    if args.model == CLIP_MODEL:
        model_id = "openai/clip-vit-large-patch14"
    elif args.model == WEMM_MODEL:
        model_id = "tencent/WeMM-Embedding-2B"
    else:
        model_id = "facebook/" + args.model
    cache_roots = [
        Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")),
        Path(
            os.environ.get(
                "VEP_MU_HF_CACHE", Path.home() / ".cache" / "vep-mu-retrieval"
            )
        ),
    ]
    revisions = []
    for cache_root in cache_roots:
        snapshot_root = (
            cache_root
            / "hub"
            / ("models--" + model_id.replace("/", "--"))
            / "snapshots"
        )
        if snapshot_root.is_dir():
            revisions.extend(sorted(path.name for path in snapshot_root.iterdir()))
        direct = cache_root / ("models--" + model_id.replace("/", "--")) / "snapshots"
        if direct.is_dir():
            revisions.extend(sorted(path.name for path in direct.iterdir()))
    precision = (
        "bf16 / full precision (MPS)"
        if args.model == WEMM_MODEL
        else "float32 / full precision"
    )
    if args.model == WEMM_MODEL and args.wemm_dim != WEMM_FULL_DIM:
        precision += f"; MRL truncation to {args.wemm_dim}d with renormalize"
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_rss_mb = rss / (1024 * 1024)
    report = {
        "candidate": args.model
        + (f"-{args.wemm_dim}d" if args.model == WEMM_MODEL else "")
        + (f"@{args.pe_image_size}px" if args.pe_image_size else ""),
        "checkpoint_revision": revisions[0] if revisions else "existing-index-only",
        "precision": precision,
        "device": str(device),
        "input_resolution": input_resolution,
        "frame_count": len(all_frames),
        "sampling_basis": "existing shared M2 JPEG index; 10-second stride",
        "decode_seconds": 0.0,
        "model_load_seconds": round(load_seconds, 3),
        "image_encode_seconds": round(image_seconds, 3),
        "index_build_seconds_excluding_decode": round(load_seconds + image_seconds, 3),
        "text_encode_seconds": round(text_seconds, 3),
        "rank_and_score_seconds": round(rank_seconds, 3),
        "mean_query_text_encode_seconds": round(text_seconds / len(queries), 5),
        "peak_process_rss_mb": round(peak_rss_mb, 2),
        "index_storage_bytes": int(all_vectors.nbytes),
        "quality": quality,
        "negative_query_count": sum(row["negative"] for row in rows),
        "negative_abstention": "unsupported: no model-specific floor applied",
        "complete_event_temporal_recall": "unmeasured: this frozen set has sampled-frame labels, not event boundaries",
        "rows": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    serialize_started = time.perf_counter()
    output = json.dumps(report, indent=2)
    report["serialize_seconds"] = round(time.perf_counter() - serialize_started, 6)
    output = json.dumps(report, indent=2)
    report["output_bytes"] = len(output.encode("utf-8"))
    output = json.dumps(report, indent=2)
    args.output.write_text(output, encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "rows"}))


if __name__ == "__main__":
    main()
