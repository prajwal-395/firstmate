"""Footage Query Module - cross-clip search over a project's own ingest.

PROTOTYPE.  **Nothing in the DAG imports this, and nothing may.**  It is
landed so it can be judged; wiring it into a step is the captain's call,
not this module's.  `docs/FOOTAGE_INDEX_PROTOTYPE.md` carries the design,
the measurements and the queries it gets wrong.

Answers "where in all my footage does X happen" for two callers:

- **a person**, from the command line, the way
  `library/tools/analysis/sfx_query.py` is used;
- **an LLM step**, through `search`/`filter`/`search_and_filter`/
  `get_detail`/`summary` and `get_tool_definitions()`, so a planner can
  ASK for the footage it needs instead of being handed all of it.

Usage as a Python module:

    from library.tools.analysis.footage_query import FootageIndex, build_index

    build_index("/path/to/project")            # writes into the project's scratch area
    idx = FootageIndex("/path/to/project")
    idx.search("where does he talk about parking", top_k=5)
    idx.filter(kind="speech", framing="close-up", min_duration=2.0)
    idx.search_and_filter("parking lot", kind="scene", top_k=5)
    idx.get_detail("clip_012#speech#0003")
    idx.summary()

Usage from the CLI:

    python3 -m library.tools.analysis.footage_query build   <project>
    python3 -m library.tools.analysis.footage_query search  <project> "the parking lot"
    python3 -m library.tools.analysis.footage_query filter  <project> --kind speech --framing close-up
    python3 -m library.tools.analysis.footage_query hybrid  <project> "cars" --kind scene
    python3 -m library.tools.analysis.footage_query detail  <project> clip_012#speech#003
    python3 -m library.tools.analysis.footage_query summary <project>
    python3 -m library.tools.analysis.footage_query tools

Where this deviates from `sfx_query.py`, and why:

- **No FAISS.**  The precedent stores a `faiss.IndexFlatIP`, which IS an
  exhaustive dot product - it buys nothing at this size.  A project's
  footage is hundreds to low thousands of segments (001: 409), so the
  search is one 409x384 matmul in numpy: 2.5 ms per hybrid query end to
  end, 0.06 ms for the lexical half alone.  Dropping it leaves ONE
  on-disk format instead of two that can disagree, and removes a
  dependency the query path does not need.
- **The embedder is optional.**  `sentence-transformers` is NOT in
  requirements.txt and is not installed here, so `sfx_query.search` would
  raise on this machine today.  This module loads the SAME model
  (`all-MiniLM-L6-v2`) through `sentence-transformers` when it is there
  and through plain `transformers` with mean pooling when it is not - the
  same vectors either way - and falls back to lexical-only search when
  neither is available, saying so rather than returning nothing.
- **Search is hybrid by default.**  Dense-only misses proper nouns and
  concrete objects; lexical-only misses paraphrase.  `--mode` exposes
  each half so a result can be attributed to the half that found it.
- **The index is per project, not per library.**  SFX profiles are a
  shared library with one index; footage belongs to one project, so the
  index lives with it - in `pipeline_output/scratch/`, the area the
  layout defines as "working files with no reader", which is exactly what
  an unwired prototype's output is.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

if __package__ in (None, ""):  # direct `python3 footage_query.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from library.tools.analysis.footage_segments import (
    SEGMENT_KINDS,
    build_segments,
    coverage_report,
)

EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
INDEX_SUBDIR = os.path.join("pipeline_output", "scratch", "footage_index")
SEGMENTS_FILE = "footage_index.json"
EMBEDDINGS_FILE = "footage_embeddings.npy"

# Weight on the dense half of a hybrid score.  Both halves are normalised
# to 0..1 over the candidate set first, so this is a straight blend.
HYBRID_DENSE_WEIGHT = 0.6

_TOKEN = re.compile(r"[a-z0-9']+")
# A plain English stoplist, plus the question words a person types into a
# search box.  Both halves earn their place: without the second, "where is
# the parking lot" scores every segment containing "where" and "where does
# he laugh" ranks an utterance whose only match is the word "where".
_STOPWORD_TEXT = (
    "a an the and or of to in on at is are was were be been being am it its this "
    "that these those there here with for from as by he she they we you i his her "
    "their our your my me him them but so if then than into over under out up down "
    "about what when where who whom how why which does do did doing done has have "
    "had can could will would should shall may might must not no nor too very just "
    "also more most other some such only own same s t don now"
)
_STOPWORDS = frozenset(_STOPWORD_TEXT.split())


def index_dir_for(project_folder) -> Path:
    """Where a project's index lives by default.

    `pipeline_output/scratch/` per §8: an area explicitly outside every
    step's directory, safe to delete at any moment.  Building the index
    therefore cannot disturb a run's state or a step's output.
    """
    return Path(project_folder) / INDEX_SUBDIR


# ─── Embedding ────────────────────────────────────────────────────


def _load_embedder():
    """Return ``(encode_fn, backend_name)`` or ``(None, reason)``.

    Tries `sentence-transformers` first so the precedent's exact code path
    is used where it exists, then plain `transformers` with mean pooling
    over the same checkpoint, which produces the same vectors.
    """
    first_failure = ""
    try:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(EMBED_MODEL)

        def encode(texts):
            return np.asarray(
                model.encode(list(texts), normalize_embeddings=True), dtype="float32"
            )

        return encode, "sentence-transformers"
    except Exception as exc:  # noqa: BLE001 - import, download or load failure
        first_failure = f"{type(exc).__name__}: {exc}"

    try:
        import torch
        from transformers import AutoModel, AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(EMBED_MODEL)
        model = AutoModel.from_pretrained(EMBED_MODEL)
        model.eval()

        def encode(texts):
            out = []
            texts = list(texts)
            for i in range(0, len(texts), 64):
                batch = tokenizer(
                    texts[i:i + 64], padding=True, truncation=True,
                    max_length=256, return_tensors="pt",
                )
                with torch.no_grad():
                    hidden = model(**batch).last_hidden_state
                mask = batch["attention_mask"].unsqueeze(-1).float()
                pooled = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
                pooled = torch.nn.functional.normalize(pooled, dim=1)
                out.append(pooled.cpu().numpy().astype("float32"))
            return np.vstack(out) if out else np.zeros((0, 384), dtype="float32")

        return encode, "transformers+mean-pooling"
    except Exception as exc:  # noqa: BLE001
        return None, (f"no embedder available (sentence-transformers: "
                      f"{first_failure or 'absent'}; transformers: "
                      f"{type(exc).__name__}: {exc})")


# ─── Lexical scoring ──────────────────────────────────────────────


def _tokenize(text: str) -> list:
    return [t for t in _TOKEN.findall((text or "").lower()) if t not in _STOPWORDS]


class _BM25:
    """Okapi BM25 over the segment texts.

    Written out rather than pulled in: it is thirty lines, it has no
    dependency, and the whole point of the lexical half is that it keeps
    working when the embedder does not.
    """

    def __init__(self, documents, k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs = [_tokenize(d) for d in documents]
        self.lengths = [len(d) for d in self.docs]
        self.avg_len = (sum(self.lengths) / len(self.lengths)) if self.lengths else 0.0
        self.freqs = [Counter(d) for d in self.docs]
        df = Counter()
        for doc in self.docs:
            df.update(set(doc))
        n = len(self.docs)
        self.idf = {
            term: math.log(1 + (n - count + 0.5) / (count + 0.5))
            for term, count in df.items()
        }

    def scores(self, query: str) -> np.ndarray:
        terms = _tokenize(query)
        out = np.zeros(len(self.docs), dtype="float32")
        if not terms or not self.avg_len:
            return out
        for i, freqs in enumerate(self.freqs):
            length = self.lengths[i] or 1
            total = 0.0
            for term in terms:
                tf = freqs.get(term, 0)
                if not tf:
                    continue
                denom = tf + self.k1 * (1 - self.b + self.b * length / self.avg_len)
                total += self.idf.get(term, 0.0) * tf * (self.k1 + 1) / denom
            out[i] = total
        return out


def _normalize(scores: np.ndarray) -> np.ndarray:
    if scores.size == 0:
        return scores
    lo, hi = float(scores.min()), float(scores.max())
    if hi - lo < 1e-9:
        return np.zeros_like(scores)
    return (scores - lo) / (hi - lo)


# ─── Building ─────────────────────────────────────────────────────


def build_index(project_folder, index_dir=None, kinds=SEGMENT_KINDS, verbose=False) -> dict:
    """Cut, embed and store the index.  Returns build statistics.

    Reads the project's ingest output; writes ONLY into `index_dir`,
    which defaults to the project's scratch area.
    """
    started = time.perf_counter()
    target = Path(index_dir) if index_dir else index_dir_for(project_folder)
    target.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    segments = build_segments(project_folder, kinds=kinds)
    segment_seconds = time.perf_counter() - t0

    encode, backend = _load_embedder()
    embed_seconds, dimension = 0.0, 0
    if encode is not None and segments:
        t0 = time.perf_counter()
        matrix = encode([s.text for s in segments])
        embed_seconds = time.perf_counter() - t0
        dimension = int(matrix.shape[1])
        np.save(target / EMBEDDINGS_FILE, matrix)
    else:
        embeddings_path = target / EMBEDDINGS_FILE
        if embeddings_path.exists():
            embeddings_path.unlink()

    payload = {
        "project_folder": str(project_folder),
        "embed_model": EMBED_MODEL if dimension else None,
        "embed_backend": backend if dimension else None,
        "embed_dimension": dimension,
        "segment_count": len(segments),
        "kinds": list(kinds),
        "coverage": coverage_report(project_folder),
        "segments": [s.to_dict() for s in segments],
    }
    index_path = target / SEGMENTS_FILE
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1)

    stats = {
        "index_dir": str(target),
        "segment_count": len(segments),
        "kinds": dict(Counter(s.kind for s in segments)),
        "embed_backend": backend,
        "embed_dimension": dimension,
        "segment_seconds": round(segment_seconds, 3),
        "embed_seconds": round(embed_seconds, 3),
        "total_seconds": round(time.perf_counter() - started, 3),
        "bytes_on_disk": sum(
            p.stat().st_size for p in target.iterdir() if p.is_file()
        ),
        "coverage": payload["coverage"],
    }
    if verbose:
        print(json.dumps(stats, indent=2))
    return stats


# ─── Querying ─────────────────────────────────────────────────────


class FootageIndex:
    """Searchable index over one project's footage.

    Every loader is lazy, so constructing this is free and a caller that
    only wants `filter` never pays for the embedding model.
    """

    def __init__(self, project_folder, index_dir=None):
        self.project_folder = str(project_folder)
        self.index_dir = Path(index_dir) if index_dir else index_dir_for(project_folder)
        self._payload = None
        self._matrix = None
        self._bm25 = None
        self._embedder = None
        self._embed_backend = None

    # ── lazy state ──

    @property
    def payload(self) -> dict:
        if self._payload is None:
            path = self.index_dir / SEGMENTS_FILE
            if not path.exists():
                raise FileNotFoundError(
                    f"No footage index at {path}. Build it first: "
                    f"python3 -m library.tools.analysis.footage_query build "
                    f"'{self.project_folder}'"
                )
            with open(path, encoding="utf-8") as f:
                self._payload = json.load(f)
        return self._payload

    @property
    def segments(self) -> list:
        return self.payload["segments"]

    @property
    def matrix(self):
        """The embedding matrix, or None when the index was built without one."""
        if self._matrix is None:
            path = self.index_dir / EMBEDDINGS_FILE
            if not path.exists():
                return None
            self._matrix = np.load(path)
        return self._matrix

    @property
    def bm25(self) -> _BM25:
        if self._bm25 is None:
            self._bm25 = _BM25([s["text"] for s in self.segments])
        return self._bm25

    def _encode_query(self, query: str):
        if self._embedder is None:
            self._embedder, self._embed_backend = _load_embedder()
        if self._embedder is None:
            return None
        return self._embedder([query])[0]

    # ── result shaping ──

    @staticmethod
    def _timecode(seconds) -> str:
        try:
            total = float(seconds)
        except (TypeError, ValueError):
            return "?"
        minutes, rest = divmod(total, 60)
        return f"{int(minutes):02d}:{rest:06.3f}"

    def _result(self, segment: dict, scores: dict) -> dict:
        """A hit, trimmed to what a caller needs to act on it.

        `word_hits` is the granularity claim made good: a speech hit
        reports the words whose text matched, in SOURCE seconds, so the
        caller can cut at a word boundary rather than at the utterance.
        """
        out = {
            "segment_id": segment["segment_id"],
            "clip_id": segment["clip_id"],
            "filename": segment["filename"],
            "kind": segment["kind"],
            "start": segment["start"],
            "end": segment["end"],
            "duration": segment["duration"],
            "timecode": f"{self._timecode(segment['start'])}-{self._timecode(segment['end'])}",
            "text": segment["text"],
            "facets": segment.get("facets", {}),
        }
        out.update(scores)
        return out

    def _word_hits(self, segment: dict, query: str) -> list:
        terms = set(_tokenize(query))
        hits = []
        for word in segment.get("words") or []:
            if set(_tokenize(word.get("word", ""))) & terms:
                hits.append({
                    "word": word.get("word"),
                    "start": word.get("start"),
                    "end": word.get("end"),
                })
        return hits

    # ── core query methods (for LLM tool use) ──

    def search(self, query: str, top_k: int = 5, mode: str = "hybrid") -> list:
        """Find the footage segments most relevant to a natural-language query.

        Args:
            query: what to look for, e.g. "he talks about finding a place
                to park", "wide shot of the parking lot".
            top_k: number of results.
            mode: "hybrid" (default), "dense" (embedding similarity only)
                or "lexical" (BM25 only).  Hybrid is the recommendation;
                the other two exist so a hit can be attributed.

        Returns:
            Segments ordered by relevance, each with `clip_id`, `start`,
            `end`, a `timecode`, the text that matched, and - for speech -
            `word_hits` giving the matching words' own source timings.
        """
        if mode not in ("hybrid", "dense", "lexical"):
            raise ValueError(f"Unknown search mode {mode!r}; use hybrid, dense or lexical")
        segments = self.segments
        if not segments:
            return []

        lexical = self.bm25.scores(query)
        dense = None
        if mode in ("hybrid", "dense"):
            matrix = self.matrix
            if matrix is not None and len(matrix) == len(segments):
                vector = self._encode_query(query)
                if vector is not None:
                    dense = matrix @ vector

        if mode == "dense":
            if dense is None:
                return [{"error": "This index has no embeddings; use mode='lexical'."}]
            combined = dense
        elif mode == "lexical":
            combined = lexical
        elif dense is None:
            combined = lexical  # honest degradation: hybrid without an embedder
        else:
            combined = (
                HYBRID_DENSE_WEIGHT * _normalize(dense)
                + (1 - HYBRID_DENSE_WEIGHT) * _normalize(lexical)
            )

        order = np.argsort(-combined)[: min(top_k, len(segments))]
        results = []
        for rank in order:
            i = int(rank)
            scores = {"score": round(float(combined[i]), 4), "mode": mode}
            if dense is not None:
                scores["dense_score"] = round(float(dense[i]), 4)
            scores["lexical_score"] = round(float(lexical[i]), 4)
            hit = self._result(segments[i], scores)
            if segments[i]["kind"] == "speech":
                words = self._word_hits(segments[i], query)
                if words:
                    hit["word_hits"] = words
            results.append(hit)
        return results

    def filter(
        self,
        kind: str | None = None,
        clip_id: str | None = None,
        framing: str | None = None,
        camera_mode: str | None = None,
        stability: str | None = None,
        movement: str | None = None,
        scene_type: str | None = None,
        content_type: str | None = None,
        has_speech: bool | None = None,
        min_duration: float | None = None,
        max_duration: float | None = None,
        min_face_presence: float | None = None,
        max_motion: float | None = None,
        min_motion: float | None = None,
        contains: str | None = None,
    ) -> list:
        """Select footage by measurable properties, with no query at all.

        Args:
            kind: one of speech, action, scene, camera, object.
            clip_id: restrict to one clip ("clip_012").
            framing: shot size as the vision pass reported it
                ("close-up", "medium", "wide").
            camera_mode: "selfie", "mounted", "handheld" as reported.
            stability: "steady", "shaky" as reported.
            movement / scene_type / content_type: as reported.
            has_speech: True selects only speech segments.
            min_duration / max_duration: segment length in seconds.
            min_face_presence: mean face-detection rate over the span,
                0..1, reduced from step 1.04's 5 Hz curve.
            min_motion / max_motion: mean motion energy over the span,
                reduced from step 1.04's 30 Hz curve.
            contains: plain substring match against the segment text.

        Returns:
            Every matching segment, in index order.
        """
        if kind is not None and kind not in SEGMENT_KINDS:
            raise ValueError(f"Unknown kind {kind!r}; known kinds: {list(SEGMENT_KINDS)}")

        # A named facet must MATCH; an unmeasured numeric facet fails its
        # bound rather than passing it, because "not measured" is not
        # evidence that the footage is steady or that a face is present.
        named = {
            "framing": framing, "camera_mode": camera_mode,
            "stability": stability, "movement": movement,
            "scene_type": scene_type, "content_type": content_type,
        }
        bounds = (
            ("face_presence", min_face_presence, None),
            ("motion", min_motion, max_motion),
        )

        def keeps(segment: dict) -> bool:
            facets = segment.get("facets", {})
            if kind is not None and segment["kind"] != kind:
                return False
            if clip_id is not None and segment["clip_id"] != clip_id:
                return False
            for name, wanted in named.items():
                if wanted is None:
                    continue
                if str(facets.get(name, "")).lower() != wanted.lower():
                    return False
            if has_speech is not None and bool(facets.get("has_speech", False)) != has_speech:
                return False
            if min_duration is not None and segment["duration"] < min_duration:
                return False
            if max_duration is not None and segment["duration"] > max_duration:
                return False
            for name, low, high in bounds:
                if low is None and high is None:
                    continue
                value = facets.get(name)
                if value is None:
                    return False
                if low is not None and value < low:
                    return False
                if high is not None and value > high:
                    return False
            return not (contains is not None
                        and contains.lower() not in segment["text"].lower())

        return [self._result(s, {}) for s in self.segments if keeps(s)]

    def search_and_filter(self, query: str, top_k: int = 10, mode: str = "hybrid",
                          **filter_kwargs) -> list:
        """Semantic search restricted to segments passing the filters.

        This is the recommended method for an LLM: it lets the model say
        both what it is looking for and what the shot has to be, in one
        call.  Filters are applied FIRST so `top_k` counts survivors.
        """
        allowed = {s["segment_id"] for s in self.filter(**filter_kwargs)}
        if not allowed:
            return []
        pool = self.search(query, top_k=len(self.segments), mode=mode)
        return [r for r in pool if r.get("segment_id") in allowed][:top_k]

    def get_detail(self, segment_id: str) -> dict:
        """The full record for one segment, including its word timings."""
        for segment in self.segments:
            if segment["segment_id"] == segment_id:
                return segment
        for segment in self.segments:
            if segment_id.lower() in segment["segment_id"].lower():
                return segment
        return {"error": f"Segment '{segment_id}' not found"}

    def transcript(self, clip_id: str) -> list:
        """Every spoken utterance of one clip, in time order, with timings.

        The one bulk read this interface offers: a step that genuinely
        needs a clip's whole transcript should say so rather than
        searching for it in pieces.
        """
        return [
            {"start": s["start"], "end": s["end"],
             "timecode": f"{self._timecode(s['start'])}-{self._timecode(s['end'])}",
             "text": s["text"]}
            for s in self.segments
            if s["clip_id"] == clip_id and s["kind"] == "speech"
        ]

    def summary(self) -> dict:
        """What is in this index: clips, kinds, coverage, indexed seconds."""
        segments = self.segments
        kinds = Counter(s["kind"] for s in segments)
        clips = sorted({s["clip_id"] for s in segments})
        framings = Counter(
            s.get("facets", {}).get("framing", "unknown") for s in segments
        )
        speech = [s for s in segments if s["kind"] == "speech"]
        return {
            "project_folder": self.payload.get("project_folder"),
            "segment_count": len(segments),
            "clips": len(clips),
            "clip_ids": clips,
            "kinds": dict(kinds),
            "framings": dict(framings),
            "spoken_seconds": round(sum(s["duration"] for s in speech), 1),
            "spoken_words": sum(len(s.get("words") or []) for s in speech),
            "embed_model": self.payload.get("embed_model"),
            "embed_backend": self.payload.get("embed_backend"),
            "coverage": self.payload.get("coverage", {}),
        }

    # ── tool definitions (for LLM function calling) ──

    @staticmethod
    def get_tool_definitions() -> list:
        """OpenAI-style tool definitions for the five query functions.

        Pass these to a model's `tools` parameter so a planning step can
        query the footage instead of being handed all of it.  Nothing in
        the pipeline does this today, by design.
        """
        facet_properties = {
            "kind": {
                "type": "string",
                "enum": list(SEGMENT_KINDS),
                "description": (
                    "What kind of observation to search: 'speech' (one spoken "
                    "utterance, with word timings), 'action' (what the subject "
                    "did, roughly a 10s window), 'scene' (where it is), 'camera' "
                    "(how it is shot), 'object' (one appearance of one thing)."
                ),
            },
            "clip_id": {"type": "string", "description": "Restrict to one clip, e.g. 'clip_012'."},
            "framing": {"type": "string", "description": "Shot size: close-up, medium, wide."},
            "camera_mode": {"type": "string", "description": "selfie, mounted, handheld."},
            "stability": {"type": "string", "description": "steady or shaky."},
            "scene_type": {"type": "string", "description": "indoor or outdoor."},
            "has_speech": {"type": "boolean", "description": "True selects only spoken segments."},
            "min_duration": {"type": "number", "description": "Minimum segment length in seconds."},
            "max_duration": {"type": "number", "description": "Maximum segment length in seconds."},
            "min_face_presence": {
                "type": "number",
                "description": "0..1 mean face-detection rate over the span. Use ~0.5 to require the subject on screen.",
            },
            "max_motion": {
                "type": "number",
                "description": "0..1 mean motion energy over the span. Use a low value to find steady footage.",
            },
        }
        return [
            {
                "type": "function",
                "function": {
                    "name": "search_footage",
                    "description": (
                        "Search all of this project's footage by natural language and get "
                        "back timecoded spans. Use it to find where something is said, "
                        "where something happens, or where a place or object appears, "
                        "instead of reading every clip's analysis."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "What to look for, e.g. 'he complains about the pollen', 'wide shot of the parking lot'.",
                            },
                            "top_k": {"type": "integer", "description": "Number of results (default 5).", "default": 5},
                            "mode": {
                                "type": "string",
                                "enum": ["hybrid", "dense", "lexical"],
                                "description": "hybrid (default) blends embedding similarity with keyword match; dense is paraphrase-tolerant; lexical is exact-word.",
                            },
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "filter_footage",
                    "description": (
                        "Select footage by measurable properties with no text query: shot "
                        "size, camera mode, stability, whether anyone is speaking, how much "
                        "motion there is, whether a face is on screen."
                    ),
                    "parameters": {"type": "object", "properties": facet_properties},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "search_and_filter_footage",
                    "description": (
                        "Search by natural language AND constrain the shot at the same time. "
                        "The recommended call: say what you are looking for and what the "
                        "footage has to be."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "What to look for."},
                            "top_k": {"type": "integer", "description": "Number of results (default 5).", "default": 5},
                            **facet_properties,
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_footage_detail",
                    "description": (
                        "Get the full record of one segment, including every word timestamp "
                        "for a spoken one, so a cut can be placed on a word boundary."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "segment_id": {"type": "string", "description": "e.g. 'clip_012#speech#003'"}
                        },
                        "required": ["segment_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_clip_transcript",
                    "description": (
                        "Every spoken utterance of one clip in time order. Use when a whole "
                        "clip's speech is genuinely needed rather than a search hit."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {"clip_id": {"type": "string", "description": "e.g. 'clip_012'"}},
                        "required": ["clip_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "footage_summary",
                    "description": (
                        "What footage exists at all: how many clips, how many segments of "
                        "each kind, how much speech, and what the ingest failed to measure."
                    ),
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]


# ─── CLI ──────────────────────────────────────────────────────────


def _print_hits(results):
    for i, hit in enumerate(results):
        if "error" in hit:
            print(hit["error"])
            continue
        parts = [f"score {hit['score']:.4f}"] if "score" in hit else []
        if "dense_score" in hit:
            parts.append(f"dense {hit['dense_score']:.3f}")
        if "lexical_score" in hit:
            parts.append(f"bm25 {hit['lexical_score']:.3f}")
        facets = hit.get("facets", {})
        shot = " ".join(
            str(facets[k]) for k in ("framing", "camera_mode", "stability") if facets.get(k)
        )
        print(f"\n{i + 1}. [{hit['kind']}] {hit['clip_id']} ({hit['filename']}) "
              f"{hit['timecode']}  ({hit['duration']:.2f}s)")
        if parts:
            print(f"   {'  '.join(parts)}")
        if shot:
            print(f"   shot: {shot}")
        print(f"   {hit['text'][:200]}")
        for word in hit.get("word_hits", [])[:6]:
            print(f"     word '{word['word']}' at {word['start']}-{word['end']}s")


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="footage_query",
        description="Cross-clip footage search over a project's own ingest output.",
    )
    sub = parser.add_subparsers(dest="command")

    p_build = sub.add_parser("build", help="Cut and embed the index")
    p_build.add_argument("project")
    p_build.add_argument("--index-dir", help="Where to write (default: <project>/pipeline_output/scratch/footage_index)")
    p_build.add_argument("--kinds", nargs="+", choices=list(SEGMENT_KINDS), default=list(SEGMENT_KINDS))

    p_search = sub.add_parser("search", help="Semantic + keyword search")
    p_search.add_argument("project")
    p_search.add_argument("query")
    p_search.add_argument("-k", "--top-k", type=int, default=5)
    p_search.add_argument("--mode", choices=["hybrid", "dense", "lexical"], default="hybrid")
    p_search.add_argument("--index-dir")

    p_filter = sub.add_parser("filter", help="Select by measurable properties")
    p_filter.add_argument("project")
    p_filter.add_argument("--kind", choices=list(SEGMENT_KINDS))
    p_filter.add_argument("--clip-id")
    p_filter.add_argument("--framing")
    p_filter.add_argument("--camera-mode")
    p_filter.add_argument("--stability")
    p_filter.add_argument("--scene-type")
    p_filter.add_argument("--min-duration", type=float)
    p_filter.add_argument("--max-duration", type=float)
    p_filter.add_argument("--min-face-presence", type=float)
    p_filter.add_argument("--max-motion", type=float)
    p_filter.add_argument("--contains")
    p_filter.add_argument("--index-dir")

    p_hybrid = sub.add_parser("hybrid", help="Search restricted by filters")
    p_hybrid.add_argument("project")
    p_hybrid.add_argument("query")
    p_hybrid.add_argument("-k", "--top-k", type=int, default=5)
    p_hybrid.add_argument("--mode", choices=["hybrid", "dense", "lexical"], default="hybrid")
    p_hybrid.add_argument("--kind", choices=list(SEGMENT_KINDS))
    p_hybrid.add_argument("--clip-id")
    p_hybrid.add_argument("--framing")
    p_hybrid.add_argument("--min-face-presence", type=float)
    p_hybrid.add_argument("--max-motion", type=float)
    p_hybrid.add_argument("--index-dir")

    p_detail = sub.add_parser("detail", help="Full record for one segment")
    p_detail.add_argument("project")
    p_detail.add_argument("segment_id")
    p_detail.add_argument("--index-dir")

    p_transcript = sub.add_parser("transcript", help="Every utterance of one clip")
    p_transcript.add_argument("project")
    p_transcript.add_argument("clip_id")
    p_transcript.add_argument("--index-dir")

    p_summary = sub.add_parser("summary", help="What is in the index")
    p_summary.add_argument("project")
    p_summary.add_argument("--index-dir")

    sub.add_parser("tools", help="Print LLM tool definitions as JSON")

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0

    if args.command == "tools":
        print(json.dumps(FootageIndex.get_tool_definitions(), indent=2))
        return 0

    if args.command == "build":
        stats = build_index(args.project, index_dir=args.index_dir, kinds=tuple(args.kinds))
        print(json.dumps(stats, indent=2))
        return 0

    idx = FootageIndex(args.project, index_dir=args.index_dir)

    if args.command == "search":
        started = time.perf_counter()
        results = idx.search(args.query, top_k=args.top_k, mode=args.mode)
        elapsed = time.perf_counter() - started
        print(f"query: {args.query!r}  mode={args.mode}  "
              f"{len(idx.segments)} segments  {elapsed * 1000:.1f} ms")
        _print_hits(results)

    elif args.command == "filter":
        results = idx.filter(
            kind=args.kind, clip_id=args.clip_id, framing=args.framing,
            camera_mode=args.camera_mode, stability=args.stability,
            scene_type=args.scene_type, min_duration=args.min_duration,
            max_duration=args.max_duration, min_face_presence=args.min_face_presence,
            max_motion=args.max_motion, contains=args.contains,
        )
        print(f"{len(results)} matching segments:")
        for hit in results:
            print(f"  {hit['segment_id']:28s} {hit['timecode']:>22s} "
                  f"{hit['duration']:6.2f}s  {hit['text'][:70]}")

    elif args.command == "hybrid":
        started = time.perf_counter()
        results = idx.search_and_filter(
            args.query, top_k=args.top_k, mode=args.mode, kind=args.kind,
            clip_id=args.clip_id, framing=args.framing,
            min_face_presence=args.min_face_presence, max_motion=args.max_motion,
        )
        elapsed = time.perf_counter() - started
        print(f"query: {args.query!r}  mode={args.mode}  {elapsed * 1000:.1f} ms")
        _print_hits(results)

    elif args.command == "detail":
        print(json.dumps(idx.get_detail(args.segment_id), indent=2))

    elif args.command == "transcript":
        for line in idx.transcript(args.clip_id):
            print(f"  {line['timecode']}  {line['text']}")

    elif args.command == "summary":
        print(json.dumps(idx.summary(), indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
