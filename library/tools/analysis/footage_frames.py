"""Frame-level CLIP search beside the text footage index.

PROTOTYPE.  **Nothing in the DAG imports this, and nothing may.**  The same
constraint as `footage_query.py`: `tests/test_footage_query_prototype.py`
fails the moment a step, the DAG or a manifest reaches for it.  `ren
search-index` / `ren search` may - a person searching their own rushes from
a chat is a person using a tool, not the pipeline making a decision.

Scope (firstmate decision 2026-10-01 on the vep-clip-frame-search eval,
`data/vep-video-intelligence-entities-and-search/` + `../vep-clip-frame-search/eval/`):
objects, people and scenes.  The eval measured P@5 5/5, 5/5 and 3/5 on
those, but fine-grained actions at chance (mouth-cover 1/5, drinking 0/5 -
CLIP matches the scene and the person and ignores the interaction), so
action and gesture queries are REFUSED here and directed to the Apple
Vision pose/hand lane, which measures bodies instead of ranking pixels.

Ranked candidates only.  The same eval found NO abstention floor that
separates present from absent (absent max 0.191 inside the present band
0.197-0.232; an all-miss query topped every query at 0.232), so this index
never abstains and never claims absence: every answer is top-k with scores,
every hit is labelled unverified, and the report says absence cannot be
concluded.  Adding a floor here would repeat the failure the text index's
floor was built to fix.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from library.tools.ren_refusal import RenRefusal

import numpy as np

if __package__ in (None, ""):  # direct `python3 footage_frames.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from library.tools.analysis import footage_segments
from library.tools.analysis.footage_query import INDEX_SUBDIR

CLIP_MODEL = "openai/clip-vit-large-patch14"

# Sampling geometry, as measured in the eval: 1 frame per 10 s at 960 px
# wide embeds at ~172 ms/frame CPU batched, so a full 82-minute episode
# indexes in ~85 s.  Denser sampling is the caller's choice, not a default:
# the eval showed density does not fix action resolution.
FRAME_STEP_S = 10.0
FRAME_WIDTH = 960
EMBED_BATCH = 16

# Adjacent frame hits merge into one time range when they are this close.
# Three sampling steps: one missed extraction must not split a moment, while
# two genuinely separate sightings stay separate.
MERGE_GAP_S = 30.0

# How many top frames the range merger reads.  Ranges are for a person to
# scrub, not for exhaustive recall - forty frames at 10 s spacing cover
# almost seven minutes of sightings per query.
SEARCH_POOL = 40

FRAME_INDEX_FILE = "frame_index.json"
FRAME_EMBEDDINGS_FILE = "frame_embeddings.npy"
FRAMES_DIRNAME = "frames"

# What this index answers, and the notice every answer carries.  "Cannot be
# concluded" is the whole of (iii): a ranking cannot say "not here".
SCOPE = "objects, people and scenes"
UNVERIFIED_NOTICE = (
    "Ranked candidates, not verified finds: every hit below is UNVERIFIED, "
    "and absence cannot be concluded from this list - no abstention floor "
    "separates present from absent subjects for this signal."
)

# Action and gesture queries, refused rather than ranked.  Each pattern is
# one measured failure or its close kin: the eval watched CLIP return
# confident rows for "covering the mouth" and "drinking" that contained
# neither.  The list is fail-OPEN on purpose - anything unlisted still
# searches - because over-blocking a person looking for their footage is
# worse than ranking it; `--include-actions` overrides in either direction.
ACTION_PATTERNS = (
    ("covering the mouth/face",
     r"cover(?:ing|ed|s)?\s+(?:its|his|her|their|the|a)?\s*(mouth|face)\b"),
    ("hand at the mouth",
     r"\bmouth\b.{0,24}\bhand\b|\bhand\b.{0,24}\bmouth\b"),
    ("drinking", r"\bdrink(?:ing|s)?\b"),
    ("sipping", r"\bsip(?:ping|s)?\b"),
    ("eating", r"\beat(?:ing|s)?\b"),
    ("gesturing", r"\bgestur(?:e|ing|es)\b"),
    ("waving", r"\bwav(?:e|ing|es)\b"),
    ("clapping", r"\bclap(?:ping|s)?\b"),
    ("pointing", r"\bpoint(?:ing|s)?\b"),
    ("nodding", r"\bnod(?:ding|s)?\b"),
    ("shrugging", r"\bshrug(?:ging|s)?\b"),
)


def action_refusal_match(query: str):
    """The (name, pattern) an action/gesture query trips, or None.

    Factored out so the CLI and the tests ask the same question the search
    asks - a gate nobody can point at is a gate nobody knows still works.
    """
    lowered = query.lower()
    for name, pattern in ACTION_PATTERNS:
        if re.search(pattern, lowered):
            return name
    return None


def _pooled_clip_features(feats):
    """L2-normalised CLIP features across the transformers 5.x boundary.

    `get_image_features` / `get_text_features` return
    `BaseModelOutputWithPooling` from transformers 5.x up (measured
    2026-09-30: `.norm` raises AttributeError there) and a bare tensor
    before.  `pooler_output` IS the feature vector in both shapes, so read
    that and normalise it.
    """
    tensor = getattr(feats, "pooler_output", feats)
    return tensor / tensor.norm(p=2, dim=-1, keepdim=True)


def _load_clip():
    """Return ``(encode_images, encode_text, backend)`` or ``(None, None, reason)``.

    Cache only: `local_files_only` refuses rather than downloading
    gigabytes over the captain's network mid-search.  Imports live inside
    so merely importing this module never drags torch in.
    """
    try:
        import torch
        from transformers import CLIPModel, CLIPProcessor

        processor = CLIPProcessor.from_pretrained(CLIP_MODEL, local_files_only=True)
        model = CLIPModel.from_pretrained(CLIP_MODEL, local_files_only=True)
        model.eval()

        def encode_images(paths):
            from PIL import Image

            out = []
            with torch.no_grad():
                for i in range(0, len(paths), EMBED_BATCH):
                    batch = [Image.open(p).convert("RGB") for p in paths[i:i + EMBED_BATCH]]
                    inputs = processor(images=batch, return_tensors="pt")
                    feats = _pooled_clip_features(model.get_image_features(**inputs))
                    out.append(feats.cpu().numpy().astype("float32"))
            return np.vstack(out) if out else np.zeros((0, 768), dtype="float32")

        def encode_text(texts):
            with torch.no_grad():
                inputs = processor(text=list(texts), return_tensors="pt", padding=True)
                feats = _pooled_clip_features(model.get_text_features(**inputs))
                return feats.cpu().numpy().astype("float32")

        return encode_images, encode_text, "clip-vit-large-patch14"
    except Exception as exc:  # noqa: BLE001 - import, cache or load failure
        return None, None, f"no CLIP available ({type(exc).__name__}: {exc})"


def _extract_frames(video_path, out_pattern, step_s: float, width: int) -> list:
    """Sample a clip to stills.  Returns ``[(path, t_seconds)]`` in time order.

    `t` is arithmetic, not probed: the fps filter emits a frame every
    `step_s` seconds starting at zero, so frame N sits at (N-1)*step_s.
    """
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-i", str(video_path),
         "-vf", f"fps=1/{step_s:g},scale={width}:-1",
         "-q:v", "3", str(out_pattern)],
        capture_output=True, encoding="utf-8", errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        raise RenRefusal(
            f"ffmpeg could not read {video_path}",
            (proc.stderr or "").strip()[-300:] or "ffmpeg exited nonzero",
            "that clip stays out of the frame index; the rest still build")
    parent = Path(str(out_pattern).rsplit("%", 1)[0]).parent
    found = sorted(parent.glob(Path(out_pattern).name.replace("%06d", "*")))
    return [(str(p), i * step_s) for i, p in enumerate(found)]


def build_frame_index(project_folder, index_dir=None,
                      step_s: float = FRAME_STEP_S,
                      width: int = FRAME_WIDTH, verbose=False) -> dict:
    """Sample every catalogued clip, embed the stills, store the index.

    Reads the project's catalog; writes ONLY into `index_dir`, which
    defaults to the project's scratch area beside the text index.  A clip
    whose file is missing or unreadable is SKIPPED with its reason, never
    fatal: one moved file must not take down the other six clips' index.
    """
    from library.tools.analysis.footage_query import index_dir_for

    started = time.perf_counter()
    target = Path(index_dir) if index_dir else index_dir_for(project_folder)
    target.mkdir(parents=True, exist_ok=True)
    frames_root = target / FRAMES_DIRNAME
    frames_root.mkdir(exist_ok=True)

    catalog = footage_segments.load_catalog(project_folder)
    frames, sources, skipped = [], [], []
    t0 = time.perf_counter()
    for clip in catalog:
        clip_id = clip.get("clip_id", "?")
        src = clip.get("source_file") or clip.get("path")
        if not src or not os.path.exists(src):
            skipped.append({"clip_id": clip_id, "reason": "file not on disk"})
            continue
        try:
            stat = os.stat(src)
        except OSError as exc:
            skipped.append({"clip_id": clip_id, "reason": f"stat failed: {exc}"})
            continue
        clip_dir = frames_root / clip_id
        clip_dir.mkdir(exist_ok=True)
        try:
            extracted = _extract_frames(
                src, str(clip_dir / f"{clip_id}_t%06d.jpg"), step_s, width)
        except RenRefusal as refused:
            skipped.append({"clip_id": clip_id, "reason": refused.render()})
            continue
        for path, t in extracted:
            frames.append({
                "file": str(Path(path).relative_to(target)),
                "clip_id": clip_id,
                "filename": clip.get("filename", ""),
                "t": round(float(t), 3),
            })
        sources.append({"clip_id": clip_id, "path": str(src),
                        "size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns})
    extract_seconds = time.perf_counter() - t0

    encode_images, _, backend = _load_clip()
    embed_seconds, dimension = 0.0, 0
    if encode_images is not None and frames:
        t0 = time.perf_counter()
        matrix = encode_images([str(target / f["file"]) for f in frames])
        embed_seconds = time.perf_counter() - t0
        dimension = int(matrix.shape[1])
        np.save(target / FRAME_EMBEDDINGS_FILE, matrix)
    else:
        embeddings_path = target / FRAME_EMBEDDINGS_FILE
        if embeddings_path.exists():
            embeddings_path.unlink()
        if backend is None:
            backend = "unavailable"

    payload = {
        "project_folder": str(project_folder),
        "clip_model": CLIP_MODEL if dimension else None,
        "embed_backend": backend if dimension else None,
        "embed_dimension": dimension,
        "frame_count": len(frames),
        "step_s": step_s,
        "width": width,
        "scope": SCOPE,
        "built_at": time.time(),
        # What this index was built FROM.  Frames come off the video files,
        # not the ingest JSON, so staleness compares the video files'
        # own size/mtime - a replaced MXF must read as a moved index.
        "sources": sources,
        "skipped": skipped,
        "frames": frames,
    }
    with open(target / FRAME_INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1)

    stats = {
        "index_dir": str(target),
        "frame_count": len(frames),
        "clips": len(sources),
        "skipped": skipped,
        "embed_backend": backend,
        "embed_dimension": dimension,
        "extract_seconds": round(extract_seconds, 1),
        "embed_seconds": round(embed_seconds, 1),
        "total_seconds": round(time.perf_counter() - started, 1),
        "bytes_on_disk": sum(
            p.stat().st_size for p in target.rglob("*") if p.is_file()
        ),
    }
    if verbose:
        print(json.dumps(stats, indent=2))
    return stats


class FrameIndex:
    """Ranked CLIP search over one project's sampled frames.

    Every loader is lazy, so constructing this is free and a caller that
    only wants staleness never pays for the CLIP weights.  There is no
    floor and no abstain: `search_frames` always returns the top-k with
    their scores, each labelled unverified.
    """

    def __init__(self, project_folder, index_dir=None):
        from library.tools.analysis.footage_query import index_dir_for

        self.project_folder = str(project_folder)
        self.index_dir = Path(index_dir) if index_dir else index_dir_for(project_folder)
        self._payload = None
        self._matrix = None
        self._clip = None

    @property
    def payload(self) -> dict:
        if self._payload is None:
            path = self.index_dir / FRAME_INDEX_FILE
            if not path.exists():
                raise RenRefusal(
                    f"no frame index at {path}",
                    "visual search reads the built frame index; nothing has "
                    "built one for this project yet",
                    f"build it first: ren search-index '{self.project_folder}' --frames")
            with open(path, encoding="utf-8") as f:
                self._payload = json.load(f)
        return self._payload

    @property
    def frames(self) -> list:
        return self.payload["frames"]

    @property
    def matrix(self):
        if self._matrix is None:
            path = self.index_dir / FRAME_EMBEDDINGS_FILE
            if not path.exists():
                return None
            self._matrix = np.load(path)
        return self._matrix

    def _encode_text(self, query: str):
        if self._clip is None:
            _, encode_text, backend = _load_clip()
            self._clip = (encode_text, backend)
        encode_text, _ = self._clip
        if encode_text is None:
            return None
        return encode_text([query])[0]

    @staticmethod
    def _timecode(seconds) -> str:
        try:
            total = float(seconds)
        except (TypeError, ValueError):
            return "?"
        minutes, rest = divmod(total, 60)
        return f"{int(minutes):02d}:{rest:06.3f}"

    def search_frames(self, query: str, top_k: int = 5,
                      include_actions: bool = False) -> dict:
        """Rank sampled frames against a query.  Always top-k, never empty
        by judgement - an empty `results` means the index itself is empty,
        and `refused` means the query is out of scope (see below).

        Action and gesture queries ("covering the mouth", "drinking",
        "gesturing"...) are refused: the eval measured CLIP at chance on
        them, and ranking chance reads as an answer.  They belong to the
        Apple Vision pose/hand lane.  `include_actions` overrides, for the
        caller who wants the ranking anyway and says so.
        """
        report = {
            "query": query,
            "scope": SCOPE,
            "notice": UNVERIFIED_NOTICE,
            "clip_model": self.payload.get("clip_model"),
            "embed_backend": self.payload.get("embed_backend"),
            "frame_count": len(self.frames),
            "results": [],
            "refused": None,
        }
        matched = action_refusal_match(query)
        if matched is not None and not include_actions:
            report["refused"] = {
                "matched": matched,
                "reason": ("frame search covers objects, people and scenes; "
                           f"{matched!r} is an action/gesture query, which this "
                           "signal does not resolve (measured at chance)"),
                "hint": ("ask the Apple Vision pose/hand lane instead, or "
                         "retry with --include-actions to rank anyway"),
            }
            return report
        if not self.frames or self.matrix is None:
            report["error"] = ("this frame index holds no embeddings; "
                               "build it with `ren search-index <project> --frames`")
            return report
        vector = self._encode_text(query)
        if vector is None:
            report["error"] = "CLIP is not available on this machine"
            return report
        scores = self.matrix @ vector
        order = np.argsort(-scores)[:max(0, top_k)]
        for rank in order:
            i = int(rank)
            frame = self.frames[i]
            report["results"].append({
                "clip_id": frame["clip_id"],
                "filename": frame["filename"],
                "t": frame["t"],
                "timecode": self._timecode(frame["t"]),
                "score": round(float(scores[i]), 4),
                "verified": False,
            })
        return report

    def search_ranges(self, query: str, top_ranges: int = 5,
                      pool: int = SEARCH_POOL,
                      merge_gap_s: float = MERGE_GAP_S,
                      include_actions: bool = False) -> dict:
        """Top frame hits merged into time ranges per clip.

        The range is the unit a person scrubs: adjacent pool hits on one
        clip within `merge_gap_s` become one `{start, end}` span ranked by
        its best frame score.  Same no-floor, no-abstain, unverified terms
        as `search_frames`.
        """
        report = self.search_frames(query, top_k=pool,
                                    include_actions=include_actions)
        if report["refused"] is not None or report.get("error"):
            report["ranges"] = []
            return report
        by_clip: dict = {}
        for hit in report["results"]:
            by_clip.setdefault(hit["clip_id"], []).append(hit)
        ranges = []
        for clip_id, hits in by_clip.items():
            hits.sort(key=lambda h: h["t"])
            start = current_end = hits[0]["t"]
            best = hits[0]["score"]
            members = [hits[0]]
            for hit in hits[1:]:
                if hit["t"] - current_end <= merge_gap_s:
                    current_end = hit["t"]
                    members.append(hit)
                    best = max(best, hit["score"])
                else:
                    ranges.append(self._range(clip_id, hits[0]["filename"],
                                              start, current_end, best, members))
                    start = current_end = hit["t"]
                    best, members = hit["score"], [hit]
            ranges.append(self._range(clip_id, hits[0]["filename"],
                                      start, current_end, best, members))
        ranges.sort(key=lambda r: -r["best_score"])
        report["ranges"] = ranges[:max(0, top_ranges)]
        return report

    def _range(self, clip_id, filename, start, end, best, members) -> dict:
        return {
            "clip_id": clip_id,
            "filename": filename,
            "start": start,
            "end": end,
            "duration": round(end - start, 3),
            "timecode": f"{self._timecode(start)}-{self._timecode(end)}",
            "best_score": round(float(best), 4),
            "n_frames": len(members),
            "verified": False,
        }

    def staleness(self) -> dict:
        """Whether a source video moved since this index was built."""
        recorded = {s["clip_id"]: s for s in self.payload.get("sources", [])}
        now_missing, changed = [], []
        for clip_id, source in recorded.items():
            try:
                stat = os.stat(source["path"])
            except OSError:
                now_missing.append(clip_id)
                continue
            if (stat.st_size != source["size_bytes"]
                    or stat.st_mtime_ns != source["mtime_ns"]):
                changed.append(clip_id)
        out = {
            "built_at": self.payload.get("built_at"),
            "sources_then": len(recorded),
            "missing_now": now_missing,
            "changed": changed,
        }
        if now_missing or changed:
            out.update(stale=True,
                       reason=("source video moved since this index was built: "
                               f"missing={now_missing} changed={changed}"))
        else:
            out.update(stale=False,
                       reason="every source video is byte-for-byte what this "
                              "index was built from")
        return out

    @staticmethod
    def get_tool_definitions() -> list:
        """LLM tool definition for the frame half of footage search."""
        return [
            {
                "type": "function",
                "function": {
                    "name": "search_footage_frames",
                    "description": (
                        "Search a project's sampled FRAMES by natural language "
                        "and get back time ranges per clip. Use it to find "
                        "where an object, person or scene appears - a laptop "
                        "on a table, the woman with glasses, the wide "
                        "two-shot. Every hit is UNVERIFIED and absence can "
                        "never be concluded from the answer. Do NOT use it "
                        "for actions or gestures (covering the mouth, "
                        "drinking, waving): this signal cannot resolve those "
                        "and will refuse; they belong to the pose/hand lane."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "An object, person or scene to look for.",
                            },
                            "top_k": {"type": "integer",
                                      "description": "Number of time ranges (default 5).",
                                      "default": 5},
                        },
                        "required": ["query"],
                    },
                },
            },
        ]
