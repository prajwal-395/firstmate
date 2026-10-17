"""Footage Query Module - cross-clip search over a project's own ingest.

A footage-intelligence CAPABILITY, independent of the editing pipeline.
It is not a pipeline stage: no DAG node runs it, and it imports nothing
from `library/steps/` or `library/processes/`, so it works on any analysed
project with no run behind it.  Agents and people query it through `ren
search` / `ren search-index` (AGENTS.md §2).  `docs/FOOTAGE_INDEX_PROTOTYPE.md`
carries the design, the measurements and the queries it gets wrong.

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
    python3 -m library.tools.analysis.footage_query search  <project> --sound laughter
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
  layout defines as working files no pipeline step reads, which is what
  a capability outside the pipeline writes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

from library.tools.ren_refusal import REFUSAL_EXIT_CODE, RenRefusal

import numpy as np

if __package__ in (None, ""):  # direct `python3 footage_query.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from library.tools.analysis.footage_segments import (
    SEGMENT_KINDS,
    build_segments,
    coverage_report,
    cut_chunks,
    ingest_fingerprint,
    load_catalog,
)
from library.tools import person_entity, source_memory

EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
INDEX_SUBDIR = os.path.join("pipeline_output", "scratch", "footage_index")
SEGMENTS_FILE = "footage_index.json"
EMBEDDINGS_FILE = "footage_embeddings.npy"

# EmbeddingGemma 2 as a TEXT encoder (unified-search lane).  The full
# checkpoint serves text through sentence-transformers >= 6.1; a
# modular text-only load and quantised builds are a later slice's
# measurement, not this one's.  Dimension: MRL-256 - the first scout
# re-ran its text query set at truncate_dim=256 and kept the identical
# top-1 (cos 0.9396), and the model card holds 256 keeps ~95% of
# image/video/speech retrieval.  128 is deliberately NOT offered: the
# card drops multimodal retrieval to ~75% there.
EG2_TEXT_MODEL = "google/embeddinggemma-2"
EG2_TEXT_BACKEND = "embeddinggemma-2-text"
EG2_TEXT_DIM = 256
# Task prefixes are REQUIRED for quality (model card).  Resolved
# against the checkpoint's own prompts dict at load time - the Scout
# runs used SearchQuery/Document, the RAG docs use
# Retrieval-query/Retrieval-document - and the winners are recorded in
# the backend string, because a silent prompt change moves every score.
EG2_TEXT_QUERY_PROMPTS = ("SearchQuery", "Retrieval-query")
EG2_TEXT_DOC_PROMPTS = ("Document", "Retrieval-document")

# `build --embedder` choices.  MiniLM stays the default; an EG2 index
# records its own model name so the query path loads the same encoder
# that wrote the matrix (768/384-d vectors never meet).
TEXT_EMBEDDERS = ("minilm", "embeddinggemma-2")
TEXT_EMBEDDER_MODELS = {
    "minilm": EMBED_MODEL,
    "embeddinggemma-2": EG2_TEXT_MODEL,
}

# Reciprocal-rank fusion over the two halves.  There is no blend
# weight to set: each half contributes 1/(k + rank), best first, so a
# row leading one half outranks a row middling in both and neither
# half's score scale can dominate.  k=60 is the Cormack et al. 2009
# constant, not a tuning on Ren data - it only sets how fast deep
# ranks decay.  Retention (the floor) still reads the RAW signals
# below; fusion only orders what the floor kept.
RRF_K = 60

# ─── The floor, and the abstain ───────────────────────────────────
#
# A ranking alone cannot say "not here".  The fused score ranks the
# candidate set, so its top row scores high for every query -
# including one whose subject appears nowhere in the footage.
# Asked to "find the shot with the cup" against 001, which has no cup,
# the unfloored index answered with three confident wrong rows.
#
# Retention is therefore judged on the RAW signals, which are the only
# numbers here that mean the same thing from one query to the next: the
# dense cosine, and a BM25 that is exactly zero when no query term
# appeared at all.
#
# Measured on 001 (17 clips, 409 segments) through the
# `transformers+mean-pooling` backend over `all-MiniLM-L6-v2` - twelve
# queries whose subject IS in the footage, eight whose subject is not:
#
#     subject present                       best cosine   at >=0.40
#     brick building                            0.878          36
#     someone walking                           0.745           5
#     motorcycle                                0.731          12
#     the pollen                                0.721           2
#     trees                                     0.665          10
#     the brewery sign                          0.645           4
#     he says look at me                        0.640           2
#     talking about goals and momentum          0.631           1
#     wide shot of the parking lot              0.601          36
#     driving the car                           0.578          39
#     where does he talk about parking          0.553           9
#     he laughs                                 0.347           0
#
#     subject absent                        best cosine   at >=0.40
#     a birthday cake with candles              0.396           0
#     find the shot with the cup                0.391           0
#     cup                                       0.325           0
#     snowboarding down a mountain              0.266           0
#     a horse galloping on a beach              0.264           0
#     someone playing the piano                 0.255           0
#     the president gives a speech about taxes  0.211           0
#     underwater coral reef                     0.204           0
#
# 0.40 separates them.  Every absent subject retains nothing; eleven of
# the twelve present ones retain at least one segment.  The twelfth,
# "he laughs", is the honest failure - the vision pass wrote "smiling"
# and never wrote "laughing" - and it is why there is a WEAK band as
# well as a floor: below 0.40 and above 0.28 a hit is reported as
# "the closest thing was this, and it is not confident", which is a
# different answer from both silence and a confident wrong row.
DENSE_SCORE_FLOOR = 0.40
DENSE_WEAK_FLOOR = 0.28

# ─── Floors per embedding backend ─────────────────────────────────
#
# One global floor cannot serve two encoders.  EmbeddingGemma 2 scores
# absent queries at 0.65-0.70 where MiniLM stays <= 0.18 (scout
# measurement: 17 podcast transcript segments, 6 queries - absent
# "birthday cake" 0.664 and "motorcycle" 0.696 against one-to-three-word
# filler segments, while MiniLM held both <= 0.18), so the 0.40 floor
# that keeps MiniLM honest would return confident wrong rows for an
# EmbeddingGemma index.  Each backend therefore carries its own
# (confident, weak) pair, and search resolves the pair from the INDEX's
# recorded backend - the matrix being judged was written by that
# backend, not by whatever loads at query time.
DENSE_FLOORS = {
    # Keyed by a substring matched case-insensitively, the recorded
    # embed_model first, then the recorded embed_backend.
    "embeddinggemma": (
        0.75, 0.60,
        "PROVISIONAL - scout toy corpus (17 texts; absent max 0.696, "
        "present min 0.841): confident sits above the absent max, weak "
        "above MiniLM's absent band. Re-measure on 001/409 before "
        "confident use.",
    ),
    "all-minilm-l6-v2": (
        0.40, 0.28,
        "001: 17 clips, 409 segments, 12 present + 8 absent queries - "
        "the calibration table in the comment above.",
    ),
    "sentence-transformers": (
        0.40, 0.28,
        "same MiniLM calibration; backend name recorded when the model "
        "string is absent (legacy index).",
    ),
    "transformers+mean-pooling": (
        0.40, 0.28,
        "same MiniLM calibration; fallback backend serving the same "
        "MiniLM checkpoint.",
    ),
}


def floors_for(embed_model=None, embed_backend=None):
    """The ``(floor, weak_floor, source)`` that judges an index's matrix.

    Matched against the backend that WROTE the index, because a cosine
    means what its encoder says it means.  An explicit ``floor=``
    argument to `search`/`search_report` still wins; this is only the
    default.  An unrecognised backend fails closed at the strictest
    measured confident floor, so a future encoder never inherits a
    floor that would turn its absent queries into confident rows.
    """
    model = (embed_model or "").lower()
    for key, (floor, weak, source) in DENSE_FLOORS.items():
        if key in model:
            return floor, weak, source
    backend = (embed_backend or "").lower()
    for key, (floor, weak, source) in DENSE_FLOORS.items():
        if key in backend:
            return floor, weak, source
    if not model.strip() and not backend.strip():
        return (DENSE_SCORE_FLOOR, DENSE_WEAK_FLOOR,
                "no backend recorded (legacy or lexical-only index) - "
                "MiniLM default")
    strict = max(DENSE_FLOORS.values(), key=lambda entry: entry[0])
    return (strict[0], strict[1],
            f"unknown backend {embed_model!r}/{embed_backend!r} - fail "
            "closed at the strictest measured floor")

# How many weak rows a report carries.  It is context for an abstain,
# not a second result list.
WEAK_BAND_LIMIT = 5

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

#: An OCR substring match counts only at this length or above.  An exact
#: word matches at any length; only the substring half is gated (see
#: `ocr_token_matches`).
OCR_SUBSTRING_MIN = 4

_OCR_WORD = re.compile(r"[a-z0-9]+")


def ocr_token_matches(token: str, word: str) -> bool:
    """Whether one query token and one on-screen word name the same text.

    Exact words always match ("shop" finds "SHOP").  A substring in
    either direction counts only when the shorter side reaches
    ``OCR_SUBSTRING_MIN``: without the gate, single-character tracked
    strings ("a", "N", "D") match any query sharing the letter ("d" is
    in "handheld") and flood visual queries ahead of the true window.
    Both inputs are expected lowercased (``_tokenize`` output and
    ``_OCR_WORD`` words); the comparison itself is exact, never folded.
    """
    if not token or not word:
        return False
    if token == word:
        return True
    if min(len(token), len(word)) < OCR_SUBSTRING_MIN:
        return False
    return token in word or word in token


def ocr_text_matches(query_tokens: list, text: str) -> bool:
    """Whether any query token names any word of an on-screen string.

    The word split is what keeps short tracked strings honest: "N"
    matches no multi-letter token (no exact word, substring gated), so
    `_text_matches` and the unified lane's `_ocr_hits` share this one
    rule rather than each re-deciding what "matches" means.
    """
    words = _OCR_WORD.findall((text or "").lower())
    if not words:
        return False
    return any(ocr_token_matches(token, word)
               for token in query_tokens or ()
               for word in words)


def index_dir_for(project_folder) -> Path:
    """Where a project's index lives by default.

    `pipeline_output/scratch/` per §8: an area explicitly outside every
    step's directory, safe to delete at any moment.  Building the index
    therefore cannot disturb a run's state or a step's output.
    """
    return Path(project_folder) / INDEX_SUBDIR


# ─── Embedding ────────────────────────────────────────────────────


def load_embedder():
    """Return ``(encode_fn, backend_name)`` or ``(None, reason)``.

    Tries MLX MiniLM first (mlx-community/all-MiniLM-L6-v2-4bit), then
    sentence-transformers, then plain transformers with mean pooling.

    Cached models load offline: each checkpoint is tried with
    ``local_files_only=True`` first, so a warm cache never revalidates
    against the Hub (LE-05).  Only on a cache miss does the load fall
    back to the network, printing one line naming the model fetched.

    Shared by footage search and the ML worker so the two never drift.
    """
    first_failure = ""

    try:
        from library.tools.analysis.mlx_minilm import get_mlx_encoder

        enc = get_mlx_encoder()
        if enc is not None:
            def encode(texts):
                return np.asarray(
                    enc.encode(list(texts)), dtype="float32"
                )
            return encode, "mlx-minilm"
    except Exception as exc:  # noqa: BLE001
        first_failure = f"{type(exc).__name__}: {exc}"

    try:
        from sentence_transformers import SentenceTransformer

        try:
            model = SentenceTransformer(EMBED_MODEL, local_files_only=True)
        except Exception:
            print(f"footage search: fetching {EMBED_MODEL} from the Hub "
                  f"(not in local cache) ...", file=sys.stderr)
            model = SentenceTransformer(EMBED_MODEL)

        def encode(texts):
            return np.asarray(
                model.encode(list(texts), normalize_embeddings=True,
                             show_progress_bar=False), dtype="float32"
            )

        return encode, "sentence-transformers"
    except Exception as exc:  # noqa: BLE001
        first_failure = f"{type(exc).__name__}: {exc}"

    try:
        import torch
        from transformers import AutoModel, AutoTokenizer

        try:
            tokenizer = AutoTokenizer.from_pretrained(
                EMBED_MODEL, local_files_only=True)
            model = AutoModel.from_pretrained(
                EMBED_MODEL, local_files_only=True)
        except Exception:
            print(f"footage search: fetching {EMBED_MODEL} from the Hub "
                  f"(not in local cache) ...", file=sys.stderr)
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
        return None, (f"no embedder available (mlx: {first_failure or 'absent'}; "
                      f"sentence-transformers: {first_failure or 'absent'}; "
                      f"transformers: {type(exc).__name__}: {exc})")


def _st_version_ok(version: str) -> bool:
    """Whether a sentence-transformers version carries EG2 support (>= 6.1)."""
    try:
        parts = []
        for piece in str(version).split("."):
            digits = "".join(ch for ch in piece if ch.isdigit())
            parts.append(int(digits) if digits else 0)
        return tuple(parts[:3]) >= (6, 1, 0)
    except Exception:  # noqa: BLE001 - unparsable version never gates in
        return False


def load_eg2_text_embedder():
    """Return a text adapter on the shared EG2 multimodal checkpoint."""
    multimodal, reason = load_eg2_multimodal_embedder()
    if multimodal is None:
        return None, reason

    def encode(texts, *, is_query=False):
        return multimodal(texts, modality="text", is_query=is_query)

    prompts_backend = getattr(multimodal, "backend", None)
    return encode, prompts_backend or f"{EG2_TEXT_BACKEND}+q:SearchQuery_d:Document"


# The checkpoint's SentenceTransformer loader exposes every EG2 modality
# through one shared vector space. Media examples are PIL/HWC images,
# ST audio dictionaries, and (T,H,W,C) video frame stacks.
def load_eg2_multimodal_embedder():
    """Return an encoder for text, image, audio and video, or a refusal reason."""
    try:
        import sentence_transformers
        from sentence_transformers import SentenceTransformer
    except Exception as exc:  # noqa: BLE001 - absent dependency
        return None, (f"no EmbeddingGemma embedder: sentence-transformers "
                      f"unavailable ({type(exc).__name__}: {exc})")
    version = getattr(sentence_transformers, "__version__", "unknown")
    if not _st_version_ok(version):
        return None, f"no EmbeddingGemma embedder: sentence-transformers {version} < 6.1.0"
    try:
        try:
            model = SentenceTransformer(EG2_TEXT_MODEL, local_files_only=True)
        except Exception:
            print(f"footage search: fetching {EG2_TEXT_MODEL} from the Hub "
                  f"(not in local cache) ...", file=sys.stderr)
            model = SentenceTransformer(EG2_TEXT_MODEL)
    except Exception as exc:  # noqa: BLE001 - load failure
        return None, (f"no EmbeddingGemma embedder: could not load "
                      f"{EG2_TEXT_MODEL} ({type(exc).__name__}: {exc})")
    supports = getattr(model, "supports", None)
    if callable(supports):
        missing = [name for name in ("text", "image", "audio", "video")
                   if not supports(name)]
        if missing:
            return None, f"{EG2_TEXT_MODEL} lacks modalities: {', '.join(missing)}"
    prompts = dict(getattr(model, "prompts", {}) or {})
    query_prompt = next((p for p in EG2_TEXT_QUERY_PROMPTS if p in prompts), None)
    document_prompt = next((p for p in EG2_TEXT_DOC_PROMPTS if p in prompts), None)

    def encode(inputs, *, modality, is_query=False):
        if modality not in ("text", "image", "audio", "video"):
            raise ValueError(f"unsupported EmbeddingGemma modality: {modality}")
        kwargs = {"normalize_embeddings": False,
                  "truncate_dim": EG2_TEXT_DIM,
                  "show_progress_bar": False}
        samples = list(inputs)
        if modality == "text":
            kwargs["prompt_name"] = query_prompt if is_query else document_prompt
        if modality == "video":
            # ST 6.1's batch collation does not preserve list-shaped videos.
            matrices = [model.encode([np.asarray(stack, dtype="uint8")], **kwargs)
                        for stack in samples]
            raw = np.concatenate(matrices, axis=0) if matrices else np.zeros(
                (0, EG2_TEXT_DIM), dtype="float32")
        else:
            raw = model.encode(samples, **kwargs)
        matrix = np.asarray(raw, dtype="float32")[:, :EG2_TEXT_DIM]
        if matrix.size == 0:
            return np.zeros((0, EG2_TEXT_DIM), dtype="float32")
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return (matrix / norms).astype("float32")

    backend = (f"{EG2_TEXT_BACKEND}+q:{query_prompt or '-'}"
               f"_d:{document_prompt or '-'}+image+audio+video")
    encode.backend = backend
    return encode, backend


def load_eg2_image_embedder():
    """Return the EG2 image adapter, accepting PIL images or HWC arrays."""
    encode, reason = load_eg2_multimodal_embedder()
    if encode is None:
        return None, reason
    return lambda images: encode(images, modality="image"), EG2_IMAGE_BACKEND


def load_eg2_audio_embedder():
    """Return the EG2 audio adapter, accepting ST audio dictionaries."""
    encode, reason = load_eg2_multimodal_embedder()
    if encode is None:
        return None, reason
    return lambda audio: encode(audio, modality="audio"), EG2_AUDIO_BACKEND


# Video chunks through the same checkpoint (unified-search lane): one
# 8 s window at 1 fps is one (T,H,W,C) video input and one 256-d vector
# in the SAME space as the text rows, so a text query embedding scores
# directly against chunk vectors.  Picks from the slice-3 matrix: 8 s
# windows (V2 beats single frames and shorter prefixes on top-1
# correctness), MRL-256 (keeps every top-1 on text and chunks; 128
# pushes absent peaks above the confident floor), fp32 (bf16 vectors
# sit at cosine 0.56 to fp32 - a different geometry, not noise).
EG2_VIDEO_BACKEND = "embeddinggemma-2-video"
EG2_IMAGE_BACKEND = "embeddinggemma-2-image"
EG2_AUDIO_BACKEND = "embeddinggemma-2-audio"
VIDEO_CHUNK_S = 8.0
# At 1 fps and 640x360, four stacks bound decoded pixels to roughly 18 MiB
# before encoder workspace. Keep this small: the model is the dominant RSS.
VIDEO_EMBED_BATCH_CHUNKS = 4
VIDEO_CHUNK_FPS = 1
VIDEO_FRAME_WIDTH = 640
CHUNK_EMBEDDINGS_FILE = "chunk_embeddings.npy"

# Absent probes for build-time floor calibration: subjects verified absent
# (silent at any floor) on the corpora measured so far - the 14-doc text
# set (scout: cake 0.664, motorcycle 0.696), the 6-chunk set, the
# 124-chunk live proof (motorcycle 0.627), and the round-2 bakeoff's
# hand-verified absent queries over 001 and geo-podcast
# (docs/benchmarks/2026-10-09-unified-multimodal-queries.json, X01-X12 /
# G22-G29: the first five below plus the seven after them).  Some of the
# seven name words that DO occur in footage ("cup", "baby") while their
# SUBJECT is absent - exactly the present-word/absent-subject case a
# floor must reject.  A probe that IS present in some stranger's footage
# only raises that index's floor (the fail-closed direction), and its
# best is recorded per probe, so the outlier is auditable.
ABSENT_PROBES = (
    "a birthday cake with candles",
    "a motorcycle parked outdoors",
    "a helicopter hovering overhead",
    "an underwater coral reef",
    "someone playing the piano on stage",
    "snowboarding down a mountain",
    "a horse galloping on a beach",
    "find the shot with the cup",
    "a dog barking loudly",
    "snow falling outside",
    "an airplane taking off",
    "a baby crying",
)

# Confident floor sits this far above the worst absent probe.  The
# smallest round value covering both measured uplifts: the toy corpus
# needed 0.696 -> 0.75 (0.054), the 124-chunk proof needs 0.627 ->
# 0.68x (0.053).  Revisit with pooled data when more indexes calibrate.
ABSENT_MARGIN = 0.06

# One grid for both chunk lanes: the video windows here and the text
# chunk rows in footage_segments share cut_chunks, so an 8 s window
# means the same span wherever it is cut from.  VIDEO_CHUNK_S names
# the lane's window beside its fps/width companions; it must equal
# footage_segments.CHUNK_S (pinned in test).
VIDEO_CHUNK_S = 8.0


def cut_video_windows(duration_s, window_s=VIDEO_CHUNK_S) -> list:
    """Fixed windows tiling ``[0, duration_s)``, short tail allowed.

    Delegates to footage_segments.cut_chunks: one cutter, one grid.
    """
    return cut_chunks(duration_s, chunk_s=window_s)


def extract_chunk_frames(source_file, start, end, fps=VIDEO_CHUNK_FPS,
                         width=VIDEO_FRAME_WIDTH) -> list:
    """1 fps stills for ``[start, end)`` of a media file, oldest first.

    Raises RenRefusal when ffmpeg is missing or the file does not
    exist; raises on an ffmpeg failure mid-extraction, because
    silently embedding half a chunk would misalign the matrix from
    the recorded windows.
    """
    import shutil
    import subprocess
    from PIL import Image

    if shutil.which("ffmpeg") is None:
        raise RenRefusal(
            "ffmpeg is not on PATH",
            "chunk stills are cut with ffmpeg",
            "install ffmpeg and rebuild the index with --video-chunks")
    if not source_file or not Path(source_file).is_file():
        raise RenRefusal(
            f"no media file at {source_file!r}",
            "a chunk's frames come from its clip's media file",
            "build the video chunks on a machine holding the media")
    length = float(end) - float(start)
    count = max(1, int(round(length * fps)))
    import tempfile
    tmpdir = tempfile.mkdtemp(prefix="ren-chunk-frames-")
    try:
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", str(start), "-t", str(length),
             "-i", str(source_file), "-vf", f"fps={fps},scale={width}:-1",
             "-frames:v", str(count), str(Path(tmpdir) / "f%03d.png")],
            capture_output=True, check=False)
        if proc.returncode != 0:
            raise RenRefusal(
                f"ffmpeg cut no frames for {source_file}@{start}-{end}",
                "the chunk cannot be embedded without its frames",
                (proc.stderr or b"").decode("utf-8", "replace")[-300:])
        frames = []
        for path in sorted(Path(tmpdir).glob("f*.png")):
            try:
                frames.append(Image.open(path).convert("RGB"))
            except Exception:  # noqa: BLE001 - a torn PNG is not a frame
                continue
        if not frames:
            raise RenRefusal(
                f"ffmpeg cut no frames for {source_file}@{start}-{end}",
                "the chunk cannot be embedded without its frames",
                "the media file may be shorter than the recorded window")
        return frames
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def calibrate_video_floors(chunk_matrix, encode_query):
    """Floors from this index's own absent-probe distribution.

    Runs ABSENT_PROBES (text, query side) against the built chunk
    matrix and returns ``(confident, weak, source, probe_bests)``:
    confident sits ABSENT_MARGIN above the worst absent best, weak IS
    the worst absent best - the band between known-absent peak and
    confident floor is exactly the near-miss interval.  Recorded into
    the payload beside the matrix, so the floor travels with the data
    it was measured on and names its source.  An empty matrix
    calibrates nothing (0 rows have no distribution).
    """
    if chunk_matrix is None or len(chunk_matrix) == 0:
        return None
    bests = {}
    for probe in ABSENT_PROBES:
        vector = encode_query([probe], is_query=True)[0]
        bests[probe] = round(float((chunk_matrix @ vector).max()), 4)
    peak = max(bests.values())
    confident = round(peak + ABSENT_MARGIN, 4)
    source = (f"absent-probe calibration at build: worst absent best "
              f"{peak:.4f} over {len(ABSENT_PROBES)} probes + "
              f"{ABSENT_MARGIN} margin")
    return confident, round(peak, 4), source, bests


def load_eg2_video_embedder():
    """Return the video adapter for 8-second (T,H,W,C) frame stacks."""
    multimodal, reason = load_eg2_multimodal_embedder()
    if multimodal is None:
        return None, reason
    return (lambda stacks: multimodal(stacks, modality="video"),
            EG2_VIDEO_BACKEND)


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


def _rrf(*halves: np.ndarray, k: int = RRF_K) -> np.ndarray:
    """Fuse full rankings: each half contributes ``1/(k + rank)``.

    Ranks are 1-based, best first, over the whole candidate set, so a
    row scoreless in one half still contributes its deep rank there
    rather than dropping out.  Equal scores share nothing: ties break
    by segment order (stable sort), which is deterministic for a fixed
    index.  An empty candidate set fuses to itself.
    """
    n = len(halves[0]) if halves else 0
    fused = np.zeros(n, dtype="float64")
    for scores in halves:
        order = np.argsort(-np.asarray(scores, dtype="float64"), kind="stable")
        ranks = np.empty(n, dtype="float64")
        ranks[order] = np.arange(1, n + 1)
        fused += 1.0 / (k + ranks)
    return fused


_VISUAL_INTENT = re.compile(
    r"\b(shot|picture|image|frame|visual|visually|scene|footage|"
    r"on[- ]screen|what does .+ look like|what is shown|what can you see|"
    r"find .+ (?:showing|with|of)|show me)\b", re.IGNORECASE)


def _has_visual_intent(query: str) -> bool:
    """Whether the query explicitly asks what the picture contains."""
    return bool(_VISUAL_INTENT.search(query or ""))


# ─── Building ─────────────────────────────────────────────────────


def sound_events_for_project(project_folder) -> tuple:
    """PANNs sound events per clip, from the M5 slot (`sound.json`).

    Returns ``({clip_id: [events]}, {clip_id: method})``.  A clip with no
    measured events is ABSENT from both maps, not present and empty: an
    unmeasured clip is not a silent one, and the two must stay
    distinguishable at query time.  Events are the `[{label, start, end,
    confidence}]` list `source_memory.write_sound` stores, unchanged.
    """
    catalog = load_catalog(project_folder)
    recorded = source_memory.load_recorded_fingerprints(project_folder)
    events_by_clip: dict = {}
    methods: dict = {}
    for clip in catalog:
        clip_id = clip.get("clip_id")
        if not clip_id:
            continue
        digest, _basis = source_memory.digest_for_clip(
            project_folder, clip, recorded)
        if digest is None:
            continue
        doc = source_memory.read_sound(digest)
        if not doc:
            continue
        events = doc.get("sound_events") or []
        if events:
            events_by_clip[clip_id] = events
            methods[clip_id] = doc.get("method")
    return events_by_clip, methods


def audio_classification_for_project(project_folder) -> dict:
    """Audio classification per clip, from the M5 slot (`sound.json`).

    Gap 12: absolute speech level (dBFS), music genre, ambience type.
    Returns ``{clip_id: audio_classification}``.  A clip with no
    measured classification is ABSENT, not present and empty.
    """
    catalog = load_catalog(project_folder)
    recorded = source_memory.load_recorded_fingerprints(project_folder)
    by_clip: dict = {}
    for clip in catalog:
        clip_id = clip.get("clip_id")
        if not clip_id:
            continue
        digest, _basis = source_memory.digest_for_clip(
            project_folder, clip, recorded)
        if digest is None:
            continue
        doc = source_memory.read_sound(digest)
        if not doc:
            continue
        classification = doc.get("audio_classification")
        if classification:
            by_clip[clip_id] = classification
    return by_clip


def ocr_texts_for_project(project_folder) -> tuple:
    """On-screen text per clip, from step 1.07's `ocr_result.json` files.

    Returns ``({clip_id: [tracked_texts]}, {clip_id: method})``.  A clip
    with no extraction on disk is ABSENT from both maps, not present and
    empty: an unextracted clip is not a text-free one, and the two must
    stay distinguishable at query time.  Texts are the `detections` list
    `OCRExtractor` stores - each a `{text, confidence, appearances,
    bbox, ...}` tracked string with the source spans it appeared at.
    """
    catalog = load_catalog(project_folder)
    texts_by_clip: dict = {}
    methods: dict = {}
    for clip in catalog:
        clip_id = clip.get("clip_id")
        if not clip_id:
            continue
        result_path = (Path(project_folder) / "pipeline_output" / "steps"
                       / "1_07_ocr_extraction" / clip_id / "ocr_result.json")
        if not result_path.exists():
            continue
        try:
            with open(result_path, encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            continue
        detections = doc.get("detections") or []
        if detections:
            texts_by_clip[clip_id] = detections
            methods[clip_id] = doc.get("method") or "EasyOCR"
    return texts_by_clip, methods


def build_index(project_folder, index_dir=None, kinds=SEGMENT_KINDS, verbose=False,
                embedder="minilm", video_chunks=False,
                unified_anchors=False) -> dict:
    """Cut, embed and store the index.  Returns build statistics.

    Reads the project's ingest output; writes ONLY into `index_dir`,
    which defaults to the project's scratch area.  `embedder` selects
    the text encoder (`TEXT_EMBEDDERS`); the query path loads the same
    one back from the recorded model name, so an index built with
    EmbeddingGemma 2 is never queried with MiniLM vectors.
    `video_chunks` additionally embeds one 8 s video vector per window
    of each clip's media into the same EG2 space (`chunk_embeddings`
    aligned to `video_chunks` descriptors); clips whose media is absent
    are reported in `video_unembedded`, never silently dropped.
    `unified_anchors` additionally builds the five linked per-window
    anchors (video, still, audio, description, transcript) through
    `unified_anchors.build_unified` into the same directory - opt-in,
    never the default.
    """
    if embedder not in TEXT_EMBEDDER_MODELS:
        raise RenRefusal(
            f"unknown text embedder {embedder!r}",
            "the footage index is built with a named text encoder",
            f"use one of {', '.join(TEXT_EMBEDDERS)}")
    started = time.perf_counter()
    target = Path(index_dir) if index_dir else index_dir_for(project_folder)
    target.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    segments = build_segments(project_folder, kinds=kinds)
    segment_seconds = time.perf_counter() - t0

    model_name = TEXT_EMBEDDER_MODELS[embedder]
    if embedder == "embeddinggemma-2":
        encode, backend = load_eg2_text_embedder()
    else:
        encode, backend = load_embedder()
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

    video_rows, video_unembedded = [], []
    video_backend, video_dimension = None, 0
    video_seconds = 0.0
    video_floor, video_floor_note = None, None
    if video_chunks:
        vencode, video_backend = load_eg2_video_embedder()
        if vencode is not None:
            catalog = load_catalog(project_folder)
            t0 = time.perf_counter()
            windows_to_embed = []
            for clip in catalog:
                clip_id = clip.get("clip_id")
                source = clip.get("source_file") or clip.get("path")
                try:
                    duration = float(clip.get("duration_seconds"))
                except (TypeError, ValueError):
                    duration = 0.0
                windows = cut_video_windows(duration) if clip_id else []
                if not windows:
                    continue
                windows_to_embed.extend(
                    (clip_id, clip.get("filename", ""), source, n, start, end)
                    for n, (start, end) in enumerate(windows))
            # The memmap is the durable checkpoint. It is preallocated for
            # descriptors only; decoded pixels and encoder inputs never grow
            # beyond VIDEO_EMBED_BATCH_CHUNKS. Checkpoint metadata is replaced
            # atomically after every encoded batch.
            partial_path = target / (CHUNK_EMBEDDINGS_FILE + ".partial.npy")
            checkpoint_path = target / (CHUNK_EMBEDDINGS_FILE + ".checkpoint.json")
            checkpoint_tmp = target / (CHUNK_EMBEDDINGS_FILE + ".checkpoint.tmp")
            final_tmp = target / (CHUNK_EMBEDDINGS_FILE + ".final.npy")
            signature = hashlib.sha256(json.dumps(
                [ingest_fingerprint(project_folder), windows_to_embed],
                separators=(",", ":")).encode("utf-8")).hexdigest()
            checkpoint = None
            try:
                candidate = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                if (candidate["signature"] == signature
                        and candidate["capacity"] == len(windows_to_embed)
                        and 0 <= candidate["processed_descriptors"] <= len(windows_to_embed)
                        and len(candidate["rows"]) <= candidate["capacity"]
                        and isinstance(candidate["video_unembedded"], list)
                        and isinstance(candidate["failed_clips"], list)
                        and partial_path.is_file()):
                    checkpoint = candidate
            except (OSError, ValueError, KeyError, TypeError):
                pass
            vmat = None
            if checkpoint is None:
                for stale in (partial_path, checkpoint_path, checkpoint_tmp, final_tmp):
                    stale.unlink(missing_ok=True)
                video_rows = []
                start_descriptor = 0
                if windows_to_embed:
                    vmat = np.lib.format.open_memmap(
                        partial_path, mode="w+", dtype="float32",
                        shape=(len(windows_to_embed), EG2_TEXT_DIM))
            else:
                video_rows = checkpoint["rows"]
                video_unembedded = checkpoint["video_unembedded"]
                start_descriptor = checkpoint["processed_descriptors"]
                vmat = np.lib.format.open_memmap(partial_path, mode="r+")
                if vmat.shape != (len(windows_to_embed), EG2_TEXT_DIM):
                    vmat = None
                    partial_path.unlink(missing_ok=True)
                    checkpoint_path.unlink(missing_ok=True)
                    video_rows, start_descriptor = [], 0
                    vmat = np.lib.format.open_memmap(
                        partial_path, mode="w+", dtype="float32",
                        shape=(len(windows_to_embed), EG2_TEXT_DIM))
            if windows_to_embed:
                if vmat is None:
                    vmat = np.lib.format.open_memmap(partial_path, mode="r+")
                failed_clips = set(checkpoint["failed_clips"] if checkpoint else ())
                offset = len(video_rows)
                # Keep the memory ceiling local to the writer. The module
                # constant is useful to report and test, but runtime
                # configuration or accidental mutation must not turn this
                # streaming loop back into an all-windows allocation.
                batch_size = 4
                for batch_start in range(
                        start_descriptor, len(windows_to_embed),
                        batch_size):
                    stacks, owners = [], []
                    for clip_id, filename, source, n, start, end in windows_to_embed[
                            batch_start:batch_start + batch_size]:
                        if clip_id in failed_clips:
                            continue
                        try:
                            frames = extract_chunk_frames(source, start, end)
                            stacks.append(np.stack([
                                np.asarray(frame, dtype="uint8") for frame in frames]))
                            owners.append({
                                "chunk_id": f"{clip_id}#vchunk#{n:03d}",
                                "clip_id": clip_id, "filename": filename,
                                "start": start, "end": end,
                            })
                        except RenRefusal as exc:
                            failed_clips.add(clip_id)
                            video_unembedded.append({
                                "clip_id": clip_id,
                                "reason": f"{exc.what} ({exc.fix})"})
                    if stacks:
                        vectors = np.asarray(vencode(stacks), dtype="float32")
                        if vectors.shape != (len(owners), EG2_TEXT_DIM):
                            raise ValueError(
                                f"video encoder returned {vectors.shape}; expected "
                                f"({len(owners)}, {EG2_TEXT_DIM})")
                        # The encoder emits NaN for short tail windows
                        # (measured on 001): a non-finite row is not a
                        # measurement and must never reach the matrix.
                        finite = np.isfinite(vectors).all(axis=1)
                        for owner, ok in zip(owners, finite):
                            if not ok:
                                video_unembedded.append({
                                    "clip_id": owner["clip_id"],
                                    "reason": ("video encoder returned a "
                                               "non-finite vector for "
                                               f"{owner['chunk_id']}")})
                        good = vectors[finite]
                        good_owners = [o for o, ok in zip(owners, finite) if ok]
                        vmat[offset:offset + len(good_owners)] = good
                        video_rows.extend(good_owners)
                        offset += len(good_owners)
                        vmat.flush()
                    checkpoint_tmp.write_text(json.dumps({
                        "signature": signature,
                        "completed_chunks": len(video_rows),
                        "capacity": len(windows_to_embed),
                        "processed_descriptors": min(
                            batch_start + batch_size,
                            len(windows_to_embed)),
                        "rows": video_rows,
                        "video_unembedded": video_unembedded,
                        "failed_clips": sorted(failed_clips),
                    }), encoding="utf-8")
                    os.replace(checkpoint_tmp, checkpoint_path)
                    del stacks
                # Repack successful rows to an exact-size NPY without ever
                # materializing the complete matrix in memory.
                video_dimension = EG2_TEXT_DIM if video_rows else 0
                if video_rows:
                    exact = np.lib.format.open_memmap(
                        final_tmp, mode="w+", dtype="float32",
                        shape=(len(video_rows), EG2_TEXT_DIM))
                    for start in range(0, len(video_rows), 256):
                        end = min(start + 256, len(video_rows))
                        exact[start:end] = vmat[start:end]
                    exact.flush()
                    del exact
                    del vmat
                    os.replace(final_tmp, target / CHUNK_EMBEDDINGS_FILE)
                    vmat = np.load(target / CHUNK_EMBEDDINGS_FILE, mmap_mode="r")
                else:
                    del vmat
                    (target / CHUNK_EMBEDDINGS_FILE).unlink(missing_ok=True)
                partial_path.unlink(missing_ok=True)
                checkpoint_path.unlink(missing_ok=True)
            video_seconds = time.perf_counter() - t0
            # Calibrate the video floors off this matrix's own absent
            # distribution (needs the text loader for the probe query
            # side - same checkpoint, second instance, transient at
            # build time).  Without it the registry floors stand and
            # the payload says why.
            if video_rows:
                tencode, _ = load_eg2_text_embedder()
                if tencode is not None:
                    video_floor = calibrate_video_floors(vmat, tencode)
                    if video_floor is None:
                        video_floor_note = (
                            "calibration skipped: empty chunk matrix")
                else:
                    video_floor_note = (
                        "calibration skipped: no EG2 text encoder at "
                        "build; registry floors apply")
        else:
            video_unembedded.append({"clip_id": "*",
                                     "reason": video_backend})
        if not video_rows:
            stale = target / CHUNK_EMBEDDINGS_FILE
            if stale.exists():
                stale.unlink()

    sound_events, sound_methods = sound_events_for_project(project_folder)
    audio_classification = audio_classification_for_project(project_folder)
    ocr_texts, ocr_methods = ocr_texts_for_project(project_folder)
    coverage = coverage_report(project_folder)
    for clip_id, clip_coverage in coverage["per_clip"].items():
        clip_coverage["video_embeddings"] = any(
            row["clip_id"] == clip_id for row in video_rows)
    text_lane = {
        "status": "built" if dimension else "degraded",
        "backend": backend,
        "dimension": dimension,
    }

    payload = {
        "project_folder": str(project_folder),
        "embed_model": model_name if dimension else None,
        "embed_backend": backend if dimension else None,
        "embed_dimension": dimension,
        "segment_count": len(segments),
        "kinds": list(kinds),
        "built_at": time.time(),
        # What this index was built FROM.  An index cannot notice that the
        # ingest moved under it unless it recorded the ingest it read.
        "ingest_fingerprint": ingest_fingerprint(project_folder),
        "coverage": coverage,
        "text_lane": text_lane,
        "segments": [s.to_dict() for s in segments],
        # The M5 sound lane: PANNs labels per clip, reachable from search.
        "sound_events": sound_events,
        "sound_event_methods": sound_methods,
        # Audio classification beyond speech/PANNs (Gap 12): absolute
        # speech level, music genre, ambience type.
        "audio_classification": audio_classification,
        # The on-screen text lane: step 1.07's tracked strings per clip,
        # reachable from search.  Absent entirely when the step never ran.
        "ocr_texts": ocr_texts,
        "ocr_methods": ocr_methods,
        # The video-chunk lane: one EG2 vector per 8 s media window, in
        # the same space as EG2 text rows.  Rows align to video_chunks
        # by position; clips without media are named in
        # video_unembedded, never silently dropped.
        "video_chunks": video_rows,
        "video_embed_model": EG2_TEXT_MODEL if video_dimension else None,
        "video_embed_backend": video_backend if video_dimension else None,
        "video_embed_dimension": video_dimension,
        "video_unembedded": video_unembedded,
        # Floors calibrated off this matrix's own absent distribution
        # (or the reason calibration was skipped).  The query path
        # prefers these over the registry: a corpus-scale floor travels
        # with the corpus it was measured on.
        "video_floor_confident": video_floor[0] if video_floor else None,
        "video_floor_weak": video_floor[1] if video_floor else None,
        "video_floor_source": (video_floor[2] if video_floor
                               else video_floor_note),
        "video_floor_probes": video_floor[3] if video_floor else None,
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
        "video_chunk_count": len(video_rows),
        "video_embed_backend": video_backend,
        "video_embed_dimension": video_dimension,
        "video_floor_confident": video_floor[0] if video_floor else None,
        "video_floor_source": (video_floor[2] if video_floor
                               else video_floor_note),
        "video_unembedded": video_unembedded,
        "segment_seconds": round(segment_seconds, 3),
        "embed_seconds": round(embed_seconds, 3),
        "video_seconds": round(video_seconds, 3),
        "total_seconds": round(time.perf_counter() - started, 3),
        "bytes_on_disk": sum(
            p.stat().st_size for p in target.iterdir() if p.is_file()
        ),
        "coverage": payload["coverage"],
        "text_lane": text_lane,
    }
    if verbose:
        print(json.dumps(stats, indent=2))
    if unified_anchors:
        # The unified lane lives beside the text/video lanes in the same
        # directory (distinct file names), and its summary is patched
        # onto the payload the text lane just wrote - so one directory
        # answers both `ren search` and `ren search --unified`.
        from library.tools.analysis import unified_anchors as ua
        unified_stats = ua.build_unified(project_folder, index_dir=target)
        stats["unified"] = unified_stats
        payload["unified_backend"] = unified_stats["backend"]
        payload["unified_window_count"] = unified_stats["window_count"]
        payload["unified_anchor_dimensions"] = unified_stats["anchor_dimensions"]
        payload["unified_anchor_floors"] = unified_stats["anchor_floors"]
        index_path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        if verbose:
            print(json.dumps({"unified": unified_stats}, indent=2))
    return stats


# ─── Querying ─────────────────────────────────────────────────────


# ─── External evidence (Resolve IntelliSearch) ───────────────────
#
# Ren search stays primary and persistent: an agent may open Resolve
# IntelliSearch, query it, and merge the matching clips as ADDITIONAL
# evidence. The scripting API exposes no IntelliSearch query verb - only
# `MediaPoolItem.AnalyzeForIntellisearch`, which indexes a pool clip but
# answers no question (`resolve-axi sense intellisearch`) - so the hits
# arrive through the UI routes Ren already has and land here as rows.
#
# The merge keeps the two evidences apart on purpose. Ren has no
# calibrated scale for Resolve's return order, so no score crosses this
# merge and no weight blends the two lists: Ren's `results`, `weak` and
# `abstained` are byte-identical with or without external rows, which sit
# beside them under `external_evidence` in the order Resolve returned
# them, each stamped with its provenance. A reader that cannot tell
# which engine produced a row will report Resolve's guess as Ren's
# measurement, so an unstamped row is the defect this refuses.

#: Provenance stamped on every row that came from Resolve IntelliSearch
#: rather than from Ren's own index.
EXTERNAL_SOURCE_INTELLISEARCH = "resolve_intellisearch"

#: Placement and display fields a Resolve-side collector may hand over
#: per hit. Ren score keys are deliberately absent: there is no scale on
#: which a Resolve ordering and a Ren cosine mean the same thing, so a
#: score handed over is ignored rather than blended.
_EXTERNAL_HIT_FIELDS = (
    "clip_id", "filename", "start", "end", "timecode",
    "text", "label", "query", "collected_by", "collected_at",
)


def merge_external_evidence(report: dict, hits: list,
                             source: str = EXTERNAL_SOURCE_INTELLISEARCH) -> dict:
    """Attach Resolve-side hits to a search report as extra evidence.

    `report` is a `search_report` dict, `hits` the rows the Resolve-side
    collector returned, in its order. Returns the same report carrying
    `external_evidence` (accepted rows, each with `provenance`) and
    `external_rejected` (rows that named no `clip_id`, each with its
    position and reason - refused by name, never silently dropped).
    `results`, `weak`, `abstained`, `retained` and `considered` are never
    touched: external evidence never replaces Ren search, and it never
    clears a Ren abstain.
    """
    if not isinstance(hits, list):
        raise RenRefusal(
            "external evidence must be a list of hit rows",
            f"{type(hits).__name__} cannot be merged with a search report",
            "hand over the JSON list the Resolve-side collector wrote")
    accepted = []
    rejected = []
    for position, hit in enumerate(hits):
        if not isinstance(hit, dict) or not hit.get("clip_id"):
            rejected.append({
                "position": position,
                "reason": "no clip_id - cannot place on any source",
                "row": hit,
            })
            continue
        row = {"provenance": source}
        for key in _EXTERNAL_HIT_FIELDS:
            if hit.get(key) is not None:
                row[key] = hit[key]
        accepted.append(row)
    report["external_source"] = source
    report["external_evidence"] = accepted
    report["external_rejected"] = rejected
    return report


#: Provenance stamped on every row that came from Resolve's own
#: transcription rather than from Ren's speech index.
TRANSCRIPT_SOURCE_RESOLVE = "resolve_transcription"

#: What counts as the same words. Lowercase, punctuation stripped,
#: whitespace collapsed - the smallest normalization under which two
#: transcripts can be compared without inventing a similarity scale.
#: Anything unequal under it is a finding carrying both texts, never a
#: score.
_WORD_FOLD = re.compile(r"[^a-z0-9]+")

#: A Resolve timecode, HH:MM:SS:FF as GetTranscription reports it.
_TIMECODE = re.compile(r"^(\d+):(\d+):(\d+):(\d+)$")


def _fold_text(text: str) -> str:
    return _WORD_FOLD.sub(" ", str(text or "").lower()).strip()


def parse_fps(value) -> float:
    """Frames per second from a float or an ffprobe `num/den` ratio.

    Refused plainly rather than guessed: the wrong rate shifts every
    converted frame by a growing error, so an unparseable rate stops
    the merge instead of skewing it.
    """
    text = str(value or "").strip()
    if "/" in text:
        numerator, _, denominator = text.partition("/")
        try:
            rate = float(numerator) / float(denominator)
            if rate > 0:
                return rate
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    else:
        try:
            if float(text) > 0:
                return float(text)
        except (TypeError, ValueError):
            pass
    raise RenRefusal(
        f"frame rate {value!r} parses as neither a number nor num/den",
        "every converted frame scales by this rate",
        "hand over ffprobe's r_frame_rate (e.g. 24000/1001)")


def resolve_time_to_seconds(value, fps=None, start_tc=None) -> float:
    """One Resolve time into clip seconds.

    Plain numbers with no `start_tc` already are clip seconds and pass
    through; with one they shift like everything else - absolute
    seconds from an API that counts from the timeline behave exactly
    like timecodes here. Either may itself be given as seconds or as
    a timecode. Anything else is refused naming the fix, because an
    unconvertible time silently becomes an orphan finding that reads
    as a real disagreement.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = None
    if number is not None:
        if start_tc is None:
            return number
        if (isinstance(start_tc, str)
                and _TIMECODE.match(start_tc.strip())):
            start_tc = resolve_time_to_seconds(start_tc, fps=fps,
                                               start_tc=0)
        return number - float(start_tc)
    match = _TIMECODE.match(str(value or "").strip())
    if match is None:
        raise RenRefusal(
            f"Resolve time {value!r} is neither clip seconds nor "
            f"HH:MM:SS:FF",
            "it cannot be placed on the clip",
            "hand over seconds, or timecodes with --resolve-fps and "
            "--resolve-start-tc")
    if fps is None or start_tc is None:
        raise RenRefusal(
            f"Resolve time {value!r} is a timecode with no rate or "
            f"opening to convert it by",
            "frames do not mean seconds without both",
            "hand over --resolve-fps and --resolve-start-tc")
    rate = parse_fps(fps)
    hours, minutes, seconds, frames = (int(part) for part in match.groups())
    absolute = hours * 3600 + minutes * 60 + seconds + frames / rate
    if isinstance(start_tc, str) and _TIMECODE.match(start_tc.strip()):
        start_tc = resolve_time_to_seconds(start_tc, fps=fps, start_tc=0)
    return absolute - float(start_tc)


def normalize_resolve_segments(segments: list, fps=None,
                               start_tc=None) -> list:
    """Resolve segments with every time in clip seconds.

    Numbers pass through; timecodes convert (needing `fps` and
    `start_tc`); anything unconvertible or missing is refused by name.
    The merge aligns on overlap, so one unplaced row would read as a
    real disagreement - this refuses before that lie is told.
    """
    if not isinstance(segments, list):
        raise RenRefusal(
            "transcript evidence must be a list of Resolve segments",
            f"{type(segments).__name__} cannot be merged with a "
            f"transcript",
            "hand over the JSON list Resolve's transcription wrote")
    normalized = []
    for position, segment in enumerate(segments):
        if not isinstance(segment, dict):
            raise RenRefusal(
                f"Resolve segment {position} is not an object",
                "it carries no time to place",
                "hand over the JSON list Resolve's transcription wrote")
        row = dict(segment)
        for key in ("start", "end"):
            if row.get(key) is None:
                raise RenRefusal(
                    f"Resolve segment {position} names no {key}",
                    "a span without an edge cannot overlap",
                    "hand over segments with start and end")
            try:
                row[key] = resolve_time_to_seconds(
                    row[key], fps=fps, start_tc=start_tc)
            except RenRefusal as exc:
                raise RenRefusal(
                    f"Resolve segment {position}: {exc}",
                    "it cannot be placed on the clip",
                    "hand over seconds, or timecodes with --resolve-fps "
                    "and --resolve-start-tc") from exc
        normalized.append(row)
    return normalized


def _spans_overlap(a_start, a_end, b_start, b_end) -> bool:
    try:
        return max(float(a_start), float(b_start)) < min(float(a_end),
                                                        float(b_end))
    except (TypeError, ValueError):
        return False


def _overlap_seconds(a_start, a_end, b_start, b_end) -> float:
    try:
        return max(0.0, min(float(a_end), float(b_end))
                   - max(float(a_start), float(b_start)))
    except (TypeError, ValueError):
        return 0.0


def merge_transcript_evidence(utterances: list, resolve_segments: list,
                              source: str = TRANSCRIPT_SOURCE_RESOLVE,
                              fps=None, start_tc=None,
                              ) -> dict:
    """Resolve's transcript rows beside Ren's own, disagreements as findings.

    `utterances` are Ren's `[{start, end, text}]` for one clip;
    `resolve_segments` are Resolve's voiced `[{text, start, end,
    speaker?}]` for the same clip, both in clip seconds. Returns the
    Ren rows untouched, the Resolve rows stamped with their provenance,
    the pairs aligned by best time overlap, and `findings` for every
    disagreement: `ren_only` (Ren heard what Resolve has no span for),
    `resolve_only` (Resolve heard what Ren has no span for - unmatched,
    never "wrong", the direction is unknown), and `text_differs`
    (same span, different folded words, carrying both texts for the
    reader to judge). No scores, no thresholds: overlap is any
    positive intersection, equality is the fold, and an empty Resolve
    side is stated (`resolve_empty`) rather than expanded into a
    finding per utterance - there is nothing to align against.

    Times arrive normalized first: Resolve reports `HH:MM:SS:FF`
    timecodes against the clip's opening timecode, which never
    overlaps Ren's zero-based spans until shifted (`fps` plus
    `start_tc`). Rows whose times cannot be placed are refused rather
    than left to read as disagreements.
    """
    resolve_segments = normalize_resolve_segments(
        resolve_segments, fps=fps, start_tc=start_tc)
    ren_rows = [dict(u) for u in utterances or []]
    resolve_rows = []
    for segment in resolve_segments:
        if not isinstance(segment, dict):
            continue
        row = {"provenance": source}
        for key in ("start", "end", "text", "speaker"):
            if segment.get(key) is not None:
                row[key] = segment[key]
        resolve_rows.append(row)
    report = {
        "ren_utterances": ren_rows,
        "resolve_segments": resolve_rows,
        "resolve_source": source,
        "resolve_empty": not resolve_rows,
        "agreement": [],
        "findings": [],
    }
    if not resolve_rows:
        return report
    # Resolve marks pauses with literal "(...)" segments carrying no
    # words. They are timing without text: aligning them against Ren's
    # utterances can only manufacture text_differs findings against
    # punctuation, so they are dropped and counted, never aligned.
    kept = []
    for row in resolve_rows:
        if _fold_text(row.get("text")):
            kept.append(row)
    report["dropped_pause_markers"] = len(resolve_rows) - len(kept)
    resolve_rows = kept
    if not resolve_rows:
        return report
    # Best overlap first, not utterance order: a short utterance that
    # barely touches a long segment must not claim it ahead of the
    # utterance the segment mostly covers. Measured live: Ren's opener
    # (1s overlap) stole Resolve's segment from the passage holding
    # 10s of it, turning one agreement into a differs plus an orphan.
    candidates = []
    for u_index, utterance in enumerate(ren_rows):
        for position, row in enumerate(resolve_rows):
            span = _overlap_seconds(utterance.get("start"),
                                    utterance.get("end"),
                                    row.get("start"), row.get("end"))
            if span > 0:
                candidates.append((span, u_index, position))
    candidates.sort(key=lambda triple: -triple[0])
    claimed_ren, claimed_resolve = set(), set()
    pairs = {}
    for _, u_index, position in candidates:
        if u_index in claimed_ren or position in claimed_resolve:
            continue
        claimed_ren.add(u_index)
        claimed_resolve.add(position)
        pairs[u_index] = position
    for u_index, utterance in enumerate(ren_rows):
        if u_index not in pairs:
            report["findings"].append({
                "kind": "ren_only",
                "ren": {"start": utterance.get("start"),
                        "end": utterance.get("end"),
                        "text": utterance.get("text")},
                "resolve": None,
            })
            continue
        row = resolve_rows[pairs[u_index]]
        if _fold_text(utterance.get("text")) == _fold_text(row.get("text")):
            report["agreement"].append({"ren": utterance, "resolve": row})
        else:
            report["findings"].append({
                "kind": "text_differs",
                "ren": {"start": utterance.get("start"),
                        "end": utterance.get("end"),
                        "text": utterance.get("text")},
                "resolve": {"start": row.get("start"),
                            "end": row.get("end"),
                            "text": row.get("text"),
                            "speaker": row.get("speaker")},
            })
    for position, row in enumerate(resolve_rows):
        if position in claimed_resolve:
            continue
        report["findings"].append({
            "kind": "resolve_only",
            "ren": None,
            "resolve": {"start": row.get("start"),
                        "end": row.get("end"),
                        "text": row.get("text"),
                        "speaker": row.get("speaker")},
        })
    return report


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
        self._chunk_matrix = None
        self._chunk_embedder = None
        self._chunk_embed_backend = None
        self._person_roster_cache = None

    # ── lazy state ──

    @property
    def payload(self) -> dict:
        if self._payload is None:
            path = self.index_dir / SEGMENTS_FILE
            if not path.exists():
                raise RenRefusal(
                    f"no footage index at {path}",
                    "search reads the built index; nothing has built one "
                    "for this project yet",
                    f"build it first: ren search-index '{self.project_folder}'")
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

    def _wants_eg2(self) -> bool:
        """Whether this index's matrix was written by EmbeddingGemma 2."""
        return "embeddinggemma" in (self.payload.get("embed_model") or "").lower()

    def _encode_query(self, query: str):
        if self._embedder is None:
            if self._wants_eg2():
                self._embedder, self._embed_backend = load_eg2_text_embedder()
            else:
                self._embedder, self._embed_backend = load_embedder()
        if self._embedder is None:
            return None
        if self._wants_eg2():
            return self._embedder([query], is_query=True)[0]
        return self._embedder([query])[0]

    def warm(self) -> dict:
        """Load the embedder now, and say which one answered.

        Loading the model is ~2 s and every query after it is ~2 ms, so a
        long-lived caller wants to pay that once, deliberately, rather
        than inside whichever query happens to be first.  Returns
        ``{"ready", "backend"}``; `backend` carries the reason when the
        load failed, because "no embedder" is a thing to report and not a
        thing to hide behind lexical results.
        """
        if self._embedder is None:
            if self._wants_eg2():
                self._embedder, self._embed_backend = load_eg2_text_embedder()
            else:
                self._embedder, self._embed_backend = load_embedder()
        return {"ready": self._embedder is not None,
                "backend": self._embed_backend or "not loaded"}

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
        `region` is the person region at the hit's own tick when the
        query named a roster person (MU-09): the M3d summary there, or
        an absence with a reason - never a missing key on a person hit.
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

    # ── scoring, shared by `search` and `search_report` ──

    def _sound_scores(self, query: str):
        """Per-segment sound-tag evidence: max matched PANNs confidence.

        Joins the payload's M5 sound events to segments by clip and
        time overlap; an event counts when its label matches a query
        token (`_sound_label_matches`).  A matched event without a
        recorded confidence scores 0 - an unweighted measurement cannot
        rank, though `--sound` still lists it.  All zeros when the
        index holds no sound events, which keeps the fusion a
        fewer-half one (see `_raw_scores`).  Shares `_map_lane_scores`
        with the OCR lane: one join for every clip-keyed lane.
        """
        entries = self.payload.get("sound_events") or {}

        def match(tokens, event):
            return self._sound_label_matches(tokens, event.get("label", ""))

        return self._map_lane_scores(query, entries, match)
    @staticmethod
    def _entry_spans(entry: dict, start: float, end: float) -> list:
        """The ``[(start, end)]`` an entry overlaps against.

        Detections carry `appearances` (a list of spans); sound-style
        events carry scalar start/end.  Either shape may be malformed;
        a span that does not parse is not a span.  Defaults cover a
        scalar missing its pair: an event naming only its start is read
        at that instant, not across the segment.
        """
        spans = []
        appearances = entry.get("appearances")
        if isinstance(appearances, list):
            for span in appearances:
                try:
                    spans.append((float(span[0]), float(span[1])))
                except (TypeError, ValueError, IndexError):
                    continue
            return spans
        try:
            e_start = float(entry.get("start", start))
            e_end = float(entry.get("end", e_start))
        except (TypeError, ValueError):
            return []
        return [(e_start, e_end)]

    @staticmethod
    def _confidence_of(entry: dict) -> float:
        """A match's rank weight, or 0 when unrecorded.

        An event without a recorded confidence cannot rank - 0 keeps it
        out of the fusion while the lane's own search still lists it.
        """
        confidence = entry.get("confidence")
        if isinstance(confidence, bool) or not isinstance(
                confidence, (int, float)):
            return 0.0
        return float(confidence)

    def _map_lane_scores(self, query: str, entries_by_clip: dict,
                         match) -> np.ndarray:
        """Per-segment max matched weight for a clip-keyed tag lane.

        `match(tokens, entry)` decides; overlap is by `_entry_spans`
        against the segment window; weight is `_confidence_of` - one
        loop for every clip-keyed lane (sound and OCR share it).
        """
        scores = np.zeros(len(self.segments), dtype="float32")
        if not entries_by_clip:
            return scores
            return scores
        tokens = _tokenize(query)
        if not tokens:
            return scores
        for i, segment in enumerate(self.segments):
            entries = entries_by_clip.get(segment["clip_id"]) or []
            try:
                start = float(segment["start"])
                end = float(segment["end"])
            except (TypeError, ValueError):
                continue
            best = 0.0
            for entry in entries:
                if not isinstance(entry, dict) or not match(tokens, entry):
                    continue
                spans = self._entry_spans(entry, start, end)
                if not any(s < end and e > start for s, e in spans):
                    continue
                best = max(best, self._confidence_of(entry))
            scores[i] = best
        return scores

    def _ocr_scores(self, query: str):
        """Per-segment on-screen-text evidence: max matched confidence.

        Joins the payload's 1.07 tracked strings by clip and appearance
        overlap on `_text_matches`.  All zeros when the index holds no
        OCR, which keeps the fusion a fewer-half one.
        """
        entries = self.payload.get("ocr_texts") or {}

        def match(tokens, detection):
            return self._text_matches(tokens, detection.get("text", ""))

        return self._map_lane_scores(query, entries, match)

    def _shot_type_scores(self, query: str):
        """Direct facet evidence for measured camera and shot descriptions.

        MiniLM and BM25 remain useful for broad visual queries, but a
        camera row already carries measured framing, camera mode,
        stability, movement, scene, lighting and content facets.  Let
        those words vote directly so a request such as "medium stationary
        shot inside the car" can rank the matching camera row without an
        EG2 index.
        """
        scores = np.zeros(len(self.segments), dtype="float32")
        query_tokens = set(_tokenize(query))
        if not query_tokens:
            return scores

        shot_cues = query_tokens & {
            "wide", "medium", "close", "closeup", "handheld", "mounted",
            "selfie", "stationary", "shaky", "stable", "tripod",
            "panning", "pan", "tilting", "tilt", "zoom", "framing",
        }
        if not shot_cues:
            return scores

        facet_names = ("framing", "camera_mode", "stability", "movement",
                       "scene_type", "lighting", "content_type")
        for i, segment in enumerate(self.segments):
            if segment.get("kind") != "camera":
                continue
            facets = segment.get("facets") or {}
            evidence = set()
            for name in facet_names:
                value = facets.get(name)
                if value:
                    evidence.update(_tokenize(str(value)))
            # The row text preserves the original measured wording where
            # a normalized facet (for example walking vs stationary) is
            # coarser than the source observation.
            evidence.update(_tokenize(segment.get("text", "")))
            matches = shot_cues & evidence
            if matches:
                scores[i] = len(matches) / len(shot_cues)
        return scores

    def _object_scores(self, query: str):
        """Per-segment object-label evidence: 1 where the label matches.

        Reads only object-kind rows, and only the LABEL (the text
        before the first stop) - the category/role/readable_text tail
        is lexical evidence already, and re-matching it here would
        double-count the same words.  Binary: a label either names what
        was asked for or it does not.
        """
        scores = np.zeros(len(self.segments), dtype="float32")
        tokens = _tokenize(query)
        if not tokens:
            return scores
        for i, segment in enumerate(self.segments):
            if segment.get("kind") != "object":
                continue
            label = (segment.get("text") or "").split(".")[0]
            if self._sound_label_matches(tokens, label):
                scores[i] = 1.0
        return scores

    def _person_roster(self) -> list:
        """The M3b roster, once per index instance, never raising.

        Face/voice identity resolution reads the project's person
        store; a project that never built one (or a store that does
        not parse) is not an error at query time - it is an empty
        roster, and the person lane stays out of the fusion exactly
        like any other unmeasured lane.
        """
        if self._person_roster_cache is None:
            try:
                roster = person_entity.resolve_person_tracks(
                    self.project_folder)
                self._person_roster_cache = (roster or {}).get("persons") or []
            except Exception:  # noqa: BLE001 - unbuilt/unreadable store
                self._person_roster_cache = []
        return self._person_roster_cache

    def _person_scores(self, query: str):
        """Per-segment person evidence: 1 where a named person's measured
        spans overlap.

        A query token matching a roster person_id or declared name
        (same both-directions substring rule as the other tag lanes)
        selects that person's union of face spans (seen) and voice
        spans (heard); segments on the same clip overlapping those
        spans score 1.  Binary like the object lane: a measured span
        either covers the segment or it does not.
        """
        scores = np.zeros(len(self.segments), dtype="float32")
        tokens = _tokenize(query)
        if not tokens:
            return scores
        windows = []
        for person in self._person_roster():
            names = [person.get("person_id", "")]
            if person.get("name"):
                names.append(person["name"])
            if not any(token and (token in (name or "").lower()
                                  or (name or "").lower() in token)
                       for token in tokens for name in names):
                continue
            for span in (person.get("face_spans") or []) + (
                    person.get("voice_spans") or []):
                try:
                    windows.append((span["clip_id"], float(span["start"]),
                                    float(span["end"])))
                except (TypeError, ValueError, KeyError):
                    continue
        if not windows:
            return scores
        for i, segment in enumerate(self.segments):
            try:
                start = float(segment["start"])
                end = float(segment["end"])
            except (TypeError, ValueError):
                continue
            for clip_id, w_start, w_end in windows:
                if clip_id == segment["clip_id"] and w_end > start \
                        and w_start < end:
                    scores[i] = 1.0
                    break
        return scores

    def _raw_scores(self, query: str, mode: str, signal_merge: bool = False,
                    candidate_mask=None):
        """``(combined, dense, lexical, sound, ocr, object, person, shot)`` or why not.

        `dense` is None when this index carries no embeddings or the
        embedder could not be loaded, and the caller decides whether that
        is a degradation to report or an outright refusal.
        """
        if mode not in ("hybrid", "dense", "lexical"):
            raise RenRefusal(
                f"unknown search mode {mode!r}",
                "the scorer only runs hybrid, dense or lexical",
                "use --mode hybrid, --mode dense or --mode lexical")

        lexical = self.bm25.scores(query)
        if candidate_mask is None:
            candidate_mask = np.ones(len(self.segments), dtype=bool)
        else:
            candidate_mask = np.asarray(candidate_mask, dtype=bool)
            if candidate_mask.shape != (len(self.segments),):
                raise ValueError("candidate mask must have one entry per segment")
        dense = None
        if mode in ("hybrid", "dense"):
            matrix = self.matrix
            # Deterministic constraints are the first retrieval stage. In
            # particular, do not load an encoder or issue a matrix operation
            # for an empty candidate set.
            if (candidate_mask.any() and matrix is not None
                    and len(matrix) == len(self.segments)):
                vector = self._encode_query(query)
                if vector is not None and vector.shape[-1] == matrix.shape[1]:
                    dense = np.zeros(len(self.segments), dtype="float32")
                    dense[candidate_mask] = matrix[candidate_mask] @ vector
                # A query vector whose width is not the matrix's - a
                # MiniLM query against an EmbeddingGemma matrix, or the
                # reverse - is not evidence for anything.  Dense stays
                # None and the caller degrades to lexical saying so,
                # instead of crashing in the matmul or scoring garbage.

        if mode == "dense":
            if dense is None:
                return None, None, None, None, None, None, None, None
            combined = dense
        elif mode == "lexical":
            combined = lexical
        elif dense is None:
            combined = lexical  # honest degradation: hybrid without an embedder
        else:
            combined = _rrf(dense, lexical)
        sound = self._sound_scores(query)
        ocr = self._ocr_scores(query)
        obj = self._object_scores(query)
        person = self._person_scores(query)
        shot = (self._shot_type_scores(query) if signal_merge else
                np.zeros(len(self.segments), dtype="float32"))
        if mode == "hybrid" and dense is not None:
            # A tag lane joins only where it has anything to say: an
            # all-zero extra half would still shift every rank by its
            # deep stable-order contribution, reordering indexes that
            # never measured that lane.
            halves = [dense, lexical]
            for lane in (sound, ocr, obj, person, shot):
                if lane.any():
                    halves.append(lane)
            if len(halves) > 2:
                combined = _rrf(*halves)
        return combined, dense, lexical, sound, ocr, obj, person, shot

    @staticmethod
    def _eligible(dense, lexical, sound, ocr, obj, person, floor: float):
        """Which segments count as EVIDENCE, before any ranking.

        Judged on the raw signals, never on `combined`: the fused score
        ranks the candidate set, so its top row is high for every query
        ever asked, including one whose subject is not in the footage
        at all.

        A segment is evidence when its dense cosine clears `floor`, or -
        when there is no dense half to judge - when at least one query
        term actually appeared in its text.  A zero BM25 is never
        evidence: no word of the query is in that segment.  A matched
        tag is evidence on its own: a laughter query over footage
        nobody transcribed has no words to match, and the PANNs label
        is the only thing that heard it; likewise a tracked on-screen
        string or an object label the transcript never names - or a
        person the roster knows by name, whose face or voice the
        segment overlaps.
        """
        tags = (sound > 0) | (ocr > 0) | (obj > 0) | (person > 0)
        if dense is None:
            return (lexical > 0) | tags
        if floor <= 0:
            return np.ones(len(dense), dtype=bool)
        return (dense >= floor) | tags

    def _rank(self, query, combined, dense, lexical, tags, keep, top_k,
              rank_by=None):
        """The `keep` segments, best first, shaped into result rows.

        `rank_by` overrides the ranking signal.  The weak band uses it to
        order by the raw cosine the floor actually judged on, so the first
        row of an abstain is the near miss the reviewer wants named.
        """
        segments = self.segments
        candidates = np.flatnonzero(keep)
        if candidates.size == 0:
            return []
        signal = combined if rank_by is None else rank_by
        # Stable: fused-score ties keep segment order instead of
        # whatever quicksort feels like, so a ranking re-runs
        # identically for a fixed index.
        order = candidates[np.argsort(-signal[candidates], kind="stable")][:max(0, top_k)]
        results = []
        for rank in order:
            i = int(rank)
            scores = {"score": round(float(combined[i]), 4)}
            if dense is not None:
                scores["dense_score"] = round(float(dense[i]), 4)
            scores["lexical_score"] = round(float(lexical[i]), 4)
            for lane, values in (tags or {}).items():
                if values is not None and np.any(values):
                    scores[f"{lane}_score"] = round(float(values[i]), 4)
            hit = self._result(segments[i], scores)
            if segments[i]["kind"] == "speech":
                words = self._word_hits(segments[i], query)
                if words:
                    hit["word_hits"] = words
            person_values = (tags or {}).get("person")
            if (person_values is not None and np.any(person_values)
                    and float(person_values[i]) > 0):
                self._attach_person_region(query, hit)
            results.append(hit)
        return results

    def _attach_person_region(self, query: str, hit: dict) -> None:
        """The region at a person hit's own tick, on the hit itself.

        The query's roster persons (the same substring rule the person
        lane ranks with) each get their M3d region at the hit midpoint;
        the first match answers `region` with `region_person` naming
        who, and every match is kept in `regions`. A person hit with no
        measured M3d still carries the absence and its reason - a hit
        that names nobody measured is data, not a missing key. A hit
        the query names nobody for is left alone. Never raises: an
        unreadable store is an absent region, the way `_person_roster`
        already treats it.
        """
        try:
            from library.tools import region_query
        except ImportError:  # pragma: no cover - same-tree import
            from tools import region_query  # type: ignore[no-redef]
        try:
            roster = self._person_roster()
            persons = region_query.roster_persons_for_tokens(
                roster, _tokenize(query))
            if not persons:
                return
            try:
                midpoint = (float(hit["start"]) + float(hit["end"])) / 2.0
            except (TypeError, ValueError, KeyError):
                return
            sources = clip_sources(self.project_folder)
            hit_digest = sources.get(hit.get("clip_id", ""), {}).get(
                "source_digest")
            docs = region_query.load_m3d_for_digests(
                [hit_digest] if hit_digest else [])
            regions = {}
            for person in persons:
                person_id = person.get("person_id", "")
                doc = docs.get(hit_digest or "")
                if doc is None:
                    regions[person_id] = {
                        "present": False,
                        "reason": region_query.ABSENT_NO_TRACKS,
                        "entity_id": person_id}
                    continue
                faces = {track.get("track_id")
                         for track in person.get("face_tracks") or []
                         if isinstance(track, dict)
                         and track.get("content_digest") == hit_digest}
                region = region_query.m3d_region_at(doc, faces, midpoint)
                region["entity_id"] = person_id
                region["content_digest"] = hit_digest
                regions[person_id] = region
            if regions:
                first = persons[0].get("person_id", "")
                hit["region_person"] = first
                hit["region"] = regions[first]
                if len(regions) > 1:
                    hit["regions"] = regions
        except Exception:  # noqa: BLE001 - region is annotation, not evidence
            return

    # ── core query methods (for LLM tool use) ──

    def search(self, query: str, top_k: int = 5, mode: str = "hybrid",
               floor: float | None = None,
               signal_merge: bool = False) -> list:
        """Find the footage segments most relevant to a natural-language query.

        Args:
            query: what to look for, e.g. "he talks about finding a place
                to park", "wide shot of the parking lot".
            top_k: number of results.
            mode: "hybrid" (default), "dense" (embedding similarity only)
                or "lexical" (BM25 only).  Hybrid is the recommendation;
                the other two exist so a hit can be attributed.
            floor: minimum dense cosine for a segment to count as evidence.
                Defaults to this index's backend floor (`floors_for`).
                Pass 0 to rank everything the way an unfloored index would.

        Returns:
            Segments ordered by relevance, each with `clip_id`, `start`,
            `end`, a `timecode`, the text that matched, and - for speech -
            `word_hits` giving the matching words' own source timings.
            **An empty list is a real answer**: it means nothing in this
            footage cleared the floor.  Use `search_report` when you need
            to say so in as many words.
        """
        report = self.search_report(query, top_k=top_k, mode=mode, floor=floor,
                                    signal_merge=signal_merge)
        if report.get("error"):
            return [{"error": report["error"]}]
        return report["results"]

    def search_report(self, query: str, top_k: int = 5, mode: str = "hybrid",
                      floor: float | None = None, filters: dict | None = None,
                      weak_k: int = WEAK_BAND_LIMIT,
                      external_hits: list | None = None,
                      signal_merge: bool = False,
                      before_embedding=None) -> dict:
        """`search`, plus everything needed to explain an empty answer.

        Returns a dict carrying the results, the WEAK band beneath the
        floor, what was considered, which backend answered and whether the
        index abstained.  A caller that cannot distinguish "nothing here"
        from "the search is broken" will report the second when it means
        the first, so this hands it both.

        `external_hits` carries Resolve IntelliSearch rows (the JSON list
        the Resolve-side collector wrote) merged through
        `merge_external_evidence`: extra evidence beside Ren's answer,
        never a replacement for it.
        """
        if floor is None:
            mode_floor, weak_floor, floor_source = floors_for(
                self.payload.get("embed_model"), self.payload.get("embed_backend"))
        else:
            mode_floor, weak_floor = float(floor), DENSE_WEAK_FLOOR
            floor_source = "explicit floor argument (--floor)"
        visual_intent = _has_visual_intent(query)
        coverage = self.payload.get("coverage", {})
        per_clip_coverage = coverage.get("per_clip", {})
        visual_modalities = ("picture", "scene", "action", "object", "ocr",
                             "video_embeddings")
        visual_covered = any(
            any(clip.get(modality, False) for modality in visual_modalities)
            for clip in per_clip_coverage.values()
        )
        if not per_clip_coverage:
            visual_covered = any(
                segment["kind"] in {"action", "scene", "camera", "object"}
                for segment in self.segments)
        report = {
            "query": query,
            "mode": mode,
            "floor": mode_floor,
            "weak_floor": weak_floor,
            "floor_source": floor_source,
            "segment_count": len(self.segments),
            "embed_backend": self.payload.get("embed_backend"),
            "embed_model": self.payload.get("embed_model"),
            "results": [],
            "weak": [],
            "considered": 0,
            "retained": 0,
            "constraint_candidates": 0,
            "constraint_reduction": len(self.segments),
            "embedding_candidates": 0,
            "abstained": False,
            "degraded": None,
            "error": None,
            "backend_answered": ("bm25" if mode == "lexical" else
                                self.payload.get("embed_backend") or "bm25"),
            "text_lane": self.payload.get("text_lane", {
                "status": "built" if self.payload.get("embed_dimension") else "degraded",
                "backend": self.payload.get("embed_backend"),
                "dimension": self.payload.get("embed_dimension", 0),
            }),
            "coverage": coverage,
            "query_intent": "visual" if visual_intent else "general",
            "answer_status": None,
        }
        if (mode in ("hybrid", "dense")
                and not self.payload.get("embed_dimension")):
            report["degraded"] = (
                "no dense text embeddings - this answer is keyword-only")
        if visual_intent and not visual_covered:
            report["abstained"] = True
            report["answer_status"] = "not_analysed"
            report["error"] = "not analysed: this index has no visual evidence coverage"
            if external_hits is not None:
                merge_external_evidence(report, external_hits)
            return report
        if not self.segments:
            report["error"] = "This index has no segments; build it against a project with ingest output."
            if external_hits is not None:
                merge_external_evidence(report, external_hits)
            return report

        merge_signals = signal_merge and mode == "hybrid"
        # A filter left unset is not a filter.  Stripping the Nones here
        # means a caller can hand over its whole form without deciding
        # which boxes the reviewer bothered to fill in.
        filters = {k: v for k, v in (filters or {}).items() if v is not None and v != ""}
        allowed = None
        if filters:
            report["filters"] = dict(filters)
            allowed = {h["segment_id"] for h in self.filter(**filters)}
            report["filtered_to"] = len(allowed)
        mask = np.ones(len(self.segments), dtype=bool)
        if allowed is not None:
            mask = np.array([s["segment_id"] in allowed for s in self.segments], dtype=bool)
        report["considered"] = int(mask.sum())
        report["constraint_candidates"] = int(mask.sum())
        report["constraint_reduction"] = len(self.segments) - int(mask.sum())
        if before_embedding is not None:
            before_embedding({
                "index_candidates": len(self.segments),
                "constraint_candidates": int(mask.sum()),
                "constraint_reduction": len(self.segments) - int(mask.sum()),
                "embedding_candidates": int(mask.sum()),
            })
        if not mask.any():
            report["abstained"] = True
            report["error_hint"] = "no segment passes the facet filters"
            report["embedding_candidates"] = 0
            report["answer_status"] = "abstained"
            if external_hits is not None:
                merge_external_evidence(report, external_hits)
            return report

        # The count is recorded before the query encoder is invoked, so a
        # caller can audit that deterministic reduction preceded retrieval.
        report["embedding_candidates"] = int(mask.sum())
        combined, dense, lexical, sound, ocr, obj, person, shot = self._raw_scores(
            query, mode, signal_merge=merge_signals, candidate_mask=mask)
        if combined is None:
            report["error"] = "This index has no embeddings; use mode='lexical'."
            report["answer_status"] = "insufficient_evidence"
            if external_hits is not None:
                merge_external_evidence(report, external_hits)
            return report

        if dense is None and mode in ("hybrid", "dense"):
            report["degraded"] = (
                "no embedder loaded - this answer is keyword match only, and a "
                "keyword floor cannot tell 'absent' from 'phrased differently'"
            )
            report["backend_answered"] = "bm25"
        elif mode == "lexical":
            report["backend_answered"] = "bm25"

        tags = {"sound": sound, "ocr": ocr, "object": obj,
                "person": person, "shot": shot}
        keep = mask & self._eligible(dense, lexical, sound, ocr, obj, person,
                                     mode_floor)
        if merge_signals:
            keep |= mask & (shot > 0)
        if visual_intent:
            keep &= np.array([s["kind"] in {"action", "scene", "camera", "object"}
                              for s in self.segments], dtype=bool)
        report["retained"] = int(keep.sum())
        report["results"] = self._rank(query, combined, dense, lexical, tags,
                                       keep, top_k)
        if visual_intent or (merge_signals and ocr.any()):
            # OCR is picture evidence but has no natural row in the text
            # segment table. Preserve its measured source span directly,
            # rather than attaching OCR confidence to a transcript row.
            filenames = {s["clip_id"]: s["filename"] for s in self.segments}
            ocr_hits = []
            for clip_id, detections in (self.payload.get("ocr_texts") or {}).items():
                for number, detection in enumerate(detections):
                    if not self._text_matches(_tokenize(query),
                                              detection.get("text", "")):
                        continue
                    spans = detection.get("appearances") or [[0.0, 0.0]]
                    for start, end in spans:
                        confidence = float(detection.get("confidence") or 0.0)
                        hit = {
                            "segment_id": f"{clip_id}#ocr#{number:03d}",
                            "clip_id": clip_id,
                            "filename": filenames.get(clip_id, clip_id),
                            "kind": "ocr",
                            "start": float(start), "end": float(end),
                            "duration": round(float(end) - float(start), 3),
                            "timecode": (f"{self._timecode(start)}-"
                                         f"{self._timecode(end)}"),
                            "text": detection["text"],
                            "score": round(confidence, 4),
                            "ocr_score": round(confidence, 4),
                            "visual_evidence": True,
                        }
                        ocr_hits.append(hit)
            if ocr_hits:
                report["results"] = (ocr_hits + report["results"])[:max(0, top_k)]

        # The weak band: what the floor turned away, and how close it came.
        # An abstain that cannot name its near miss reads as a broken search.
        if dense is not None and mode_floor > 0:
            weak = mask & ~keep & (dense >= weak_floor)
            report["weak"] = self._rank(query, combined, dense, lexical, tags,
                                          weak, weak_k, rank_by=dense)
            rejected = mask & ~keep
            if rejected.any():
                best = int(np.flatnonzero(rejected)[np.argmax(dense[rejected])])
                report["best_rejected"] = {
                    "dense_score": round(float(dense[best]), 4),
                    "text": self.segments[best]["text"][:160],
                    "clip_id": self.segments[best]["clip_id"],
                    "kind": self.segments[best]["kind"],
                }
        report["abstained"] = not report["results"]
        # A lexical-only fallback can rank useful leads, but it cannot
        # distinguish an absent subject from different wording. Keep those
        # rows visible as leads without presenting them as confirmed finds.
        if report["results"]:
            report["answer_status"] = (
                "insufficient_evidence" if dense is None or mode == "lexical"
                else "found")
        else:
            report["answer_status"] = (
                "not_found" if dense is not None
                else "insufficient_evidence")
        if external_hits is not None:
            merge_external_evidence(report, external_hits)
        return report

    # ── video chunks (the unified-space visual lane) ───────────────

    @property
    def chunk_matrix(self):
        """The per-window video matrix, or None when never built."""
        if self._chunk_matrix is None:
            path = self.index_dir / CHUNK_EMBEDDINGS_FILE
            if not path.exists():
                return None
            self._chunk_matrix = np.load(path)
        return self._chunk_matrix

    @property
    def video_chunks(self) -> list:
        return self.payload.get("video_chunks") or []

    def _encode_chunk_query(self, query: str):
        """A text query in the chunks' own space (EG2 text, query side)."""
        if self._chunk_embedder is None:
            self._chunk_embedder, self._chunk_embed_backend = (
                load_eg2_text_embedder())
        if self._chunk_embedder is None:
            return None
        return self._chunk_embedder([query], is_query=True)[0]

    def search_chunks(self, query: str, top_k: int = 5,
                      floor: float | None = None,
                      weak_k: int = WEAK_BAND_LIMIT) -> dict:
        """Rank the index's 8 s video windows against a text query.

        The same report shape as `search_report` (results, weak,
        abstained, best_rejected) over chunk rows carrying clip_id and
        source seconds.  Retention is judged on the raw cross-modal
        cosine against the recording encoder's floors (`floors_for`);
        an index with no chunk matrix, or one whose query encoder
        cannot load, refuses in words rather than ranking nothing.
        """
        if floor is None:
            recorded = self.payload.get("video_floor_confident")
            if recorded is not None:
                mode_floor = float(recorded)
                weak_floor = float(self.payload.get("video_floor_weak",
                                                    DENSE_WEAK_FLOOR))
                floor_source = self.payload.get("video_floor_source",
                                                "recorded at build")
            else:
                mode_floor, weak_floor, floor_source = floors_for(
                    self.payload.get("video_embed_model"),
                    self.payload.get("video_embed_backend"))
        else:
            mode_floor, weak_floor = float(floor), DENSE_WEAK_FLOOR
            floor_source = "explicit floor argument (--floor)"
        report = {
            "query": query,
            "mode": "video_chunks",
            "floor": mode_floor,
            "weak_floor": weak_floor,
            "floor_source": floor_source,
            "chunk_count": len(self.video_chunks),
            "video_embed_backend": self.payload.get("video_embed_backend"),
            "results": [],
            "weak": [],
            "considered": 0,
            "retained": 0,
            "abstained": False,
            "degraded": None,
            "error": None,
        }
        if not self.video_chunks:
            report["error"] = (
                "This index holds no video chunks; build it with "
                "--video-chunks on a machine holding the media.")
            return report
        matrix = self.chunk_matrix
        if matrix is None or len(matrix) != len(self.video_chunks):
            report["error"] = (
                "The recorded chunk rows have no embedding matrix; "
                "rebuild the index with --video-chunks.")
            return report
        vector = self._encode_chunk_query(query)
        if vector is None:
            report["error"] = (
                "The EG2 text encoder could not load, so the text query "
                "has no vector in the chunks' space; rebuild or retry "
                "with the model available.")
            return report
        if vector.shape[-1] != matrix.shape[1]:
            report["error"] = (
                "The query vector does not fit the chunk matrix; "
                "the text encoder and the video build disagree.")
            return report
        dense = matrix @ vector
        report["considered"] = len(self.video_chunks)

        def row(i, score):
            chunk = self.video_chunks[int(i)]
            return {
                "chunk_id": chunk["chunk_id"],
                "clip_id": chunk["clip_id"],
                "filename": chunk.get("filename", ""),
                "start": chunk["start"],
                "end": chunk["end"],
                "timecode": f"{self._timecode(chunk['start'])}-"
                            f"{self._timecode(chunk['end'])}",
                "dense_score": round(float(score), 4),
            }

        if mode_floor <= 0:
            keep = np.ones(len(dense), dtype=bool)
        else:
            keep = dense >= mode_floor
        order = np.flatnonzero(keep)[np.argsort(
            -dense[keep], kind="stable")][:max(0, top_k)]
        report["results"] = [row(i, dense[i]) for i in order]
        report["retained"] = int(keep.sum())
        if mode_floor > 0:
            weak = ~keep & (dense >= weak_floor)
            border = np.flatnonzero(weak)[np.argsort(
                -dense[weak], kind="stable")][:max(0, weak_k)]
            report["weak"] = [row(i, dense[i]) for i in border]
            rejected = ~keep
            if rejected.any():
                best = int(np.flatnonzero(rejected)[np.argmax(dense[rejected])])
                chunk = self.video_chunks[best]
                report["best_rejected"] = {
                    "dense_score": round(float(dense[best]), 4),
                    "chunk_id": chunk["chunk_id"],
                    "clip_id": chunk["clip_id"],
                }
        report["abstained"] = not report["results"]
        return report

    def search_unified(self, query: str, top_k: int = 5,
                       mode: str = "hybrid",
                       floor: float | None = None,
                       query_image=None) -> dict:
        """The opt-in unified backend: five linked anchors, one space.

        Reads the anchors `build --unified-anchors` wrote beside this
        index and fuses their scores with keyword and OCR.  The default
        text path is untouched; this only runs when asked for.
        """
        from library.tools.analysis.unified_anchors import UnifiedIndex
        return UnifiedIndex(
            self.project_folder,
            index_dir=self.index_dir).search_unified(
                query, top_k=top_k, mode=mode, floor=floor,
                query_image=query_image)

    # ── sound events (the M5 lane) ──────────────────────────────────

    @staticmethod
    def _sound_label_matches(query_tokens: list, label: str) -> bool:
        """Whether a query token and a PANNs label refer to the same sound.

        Both directions of substring, case-insensitive: "laughter" matches
        "Laughter", and "barking" matches "Bark" because the label is a
        substring of the query token.  A full sentence still works - the
        token "laughter" in "where does laughter happen" is what matches.
        """
        lowered = (label or "").lower()
        if not lowered:
            return False
        return any(
            token and (token in lowered or lowered in token)
            for token in query_tokens
        )

    def sound_search(self, query: str, top_k: int = 5) -> dict:
        """Find the spans where a PANNs sound-event label was measured.

        Answers "where does laughter happen" (or applause, music, impacts)
        from the M5 sound lane - the labels step 1.04 already measured and
        `source_memory.write_sound` already stored, joined here by label.

        The match is by label, not by text: there is no embedding and no
        transcript for a sound, so a query that matches no label is an
        honest empty that NAMES the labels this footage actually has, the
        same way `search_report` names its near miss.  An index with no
        sound events at all says that too, rather than answering "nowhere"
        to a question that was never measured.
        """
        events_by_clip = self.payload.get("sound_events") or {}
        report = {
            "query": query,
            "mode": "sound",
            "hits": [],
            "considered": 0,
            "matched": 0,
            "available_labels": [],
            "abstained": False,
            "error": None,
        }
        if not events_by_clip:
            report["abstained"] = True
            report["error"] = (
                "no sound events in this index - the M5 sound lane "
                "(sound.json) was never written for any clip, so there is "
                "no PANNs label to search")
            return report

        tokens = _tokenize(query)
        if not tokens:
            report["abstained"] = True
            report["error"] = "no sound label to look for"
            return report

        labels = sorted({
            event.get("label", "")
            for events in events_by_clip.values()
            for event in events
            if event.get("label")
        })
        report["available_labels"] = labels

        hits = []
        for clip_id in sorted(events_by_clip):
            for event in events_by_clip[clip_id]:
                report["considered"] += 1
                if not self._sound_label_matches(tokens, event.get("label", "")):
                    continue
                start = event.get("start")
                end = event.get("end")
                hits.append({
                    "clip_id": clip_id,
                    "label": event.get("label"),
                    "start": start,
                    "end": end,
                    "timecode": f"{self._timecode(start)}-{self._timecode(end)}",
                    "confidence": event.get("confidence"),
                })
        hits.sort(key=lambda h: (h["clip_id"], h["start"] or 0.0))
        report["hits"] = hits[:max(0, top_k)]
        report["matched"] = len(hits)
        report["abstained"] = not hits
        return report

    def project_graph(self, query: str, **kwargs) -> dict:
        """Query the Ren Project Graph: people, shots, transcript, edits.

        Answers the cross-source relational questions the project graph
        exists for: "find the best reaction shot" (another person
        visible while a speaker talks), "alternate takes" (other shots
        covering the same source), "coverage" (which shots cover a
        timeline span), "B-roll matching" (which shots match what is
        being said), "shot colour" (the colour track's registration for
        one shot - its measurement, its grade, its camera match),
        "reel versions" (which versions each reel has held),
        "editorial findings" (the compile's dead sections, trim
        proposals, J/L placements and decisions, reaction cutaways),
        "VFX detections" (which effects the edit graph recorded
        on which clips) and "audio events" (what the analysis heard
        on which clip and when - music changes, laughter) and
        "variant branches" (which branch each
        recorded variant owns, the base it forked and the direction
        it tries), "commits" (what changed on a reel timeline and
        why), "project events" (what the workers did: leases,
        checkpoints, sign-offs), "reaction decisions" (the
        reaction-cutaway decisions the run recorded, with reasons),
        "participants" (who holds at least a given authority level)
        and "proposals" (what awaits approval).

        The graph is DERIVED, never authored: it reads the project's
        catalog, world models, person tracks, edit graph, intent
        model, compile manifest and versions ledger. A state key the
        run has not produced is simply absent.
        """
        from library.tools import project_graph
        if query == "cross_project_assets":
            projects = kwargs.get("projects") or []
            if not projects:
                return {"query": query, "results": [], "error":
                        "cross_project_assets needs --projects", "abstained": True}
            answer = project_graph.query_cross_project_assets(
                self.project_folder, projects, kwargs.get("search_text", ""),
                categories=tuple(kwargs.get("categories") or (
                    "people", "scenes", "events", "usage", "lineage")))
            answer.update(query=query, mode="project_graph")
            return answer
        graph = project_graph.load_or_build(self.project_folder)
        report = {
            "query": query,
            "mode": "project_graph",
            "node_count": graph["node_count"],
            "edge_count": graph["edge_count"],
            "results": [],
            "error": None,
        }
        if query == "reaction_shots":
            person_id = kwargs.get("person_id")
            around_t = kwargs.get("around_t")
            if not person_id or around_t is None:
                report["error"] = (
                    "reaction_shots needs --person and --t")
                return report
            report["results"] = project_graph.find_reaction_shots(
                graph, person_id, around_t)
        elif query == "alternate_takes":
            clip_id = kwargs.get("clip_id")
            if not clip_id:
                report["error"] = "alternate_takes needs --clip"
                return report
            report["results"] = project_graph.find_alternate_takes(
                graph, clip_id)
        elif query == "coverage":
            timeline_start = kwargs.get("timeline_start")
            timeline_end = kwargs.get("timeline_end")
            if timeline_start is None or timeline_end is None:
                report["error"] = (
                    "coverage needs --timeline-start and --timeline-end")
                return report
            report["results"] = project_graph.find_coverage(
                graph, timeline_start, timeline_end)
        elif query == "broll_matches":
            segment_id = kwargs.get("segment_id")
            if not segment_id:
                report["error"] = "broll_matches needs --segment"
                return report
            report["results"] = project_graph.find_broll_matches(
                graph, segment_id)
        elif query == "shot_colour":
            clip_id = kwargs.get("clip_id")
            if not clip_id:
                report["error"] = "shot_colour needs --clip"
                return report
            record = project_graph.find_shot_colour(graph, clip_id)
            report["results"] = [record] if record is not None else []
        elif query == "reel_versions":
            report["results"] = project_graph.find_reel_versions(
                graph, kwargs.get("final"))
        elif query == "editorial_findings":
            kind = kwargs.get("kind")
            if kind is not None and kind not in project_graph.FINDING_KINDS:
                report["error"] = (
                    f"unknown finding kind {kind!r}; known: "
                    + ", ".join(project_graph.FINDING_KINDS))
                return report
            report["results"] = project_graph.find_editorial_findings(
                graph, kind, kwargs.get("clip_id"))
        elif query == "vfx_detections":
            report["results"] = project_graph.find_vfx_detections(
                graph, kwargs.get("clip_id"))
        elif query == "variant_branches":
            report["results"] = project_graph.find_variant_branches(
                graph, kwargs.get("exploration"), kwargs.get("timeline"))
        elif query == "audio_events":
            report["results"] = project_graph.find_audio_events(
                graph, kwargs.get("clip_id"), kwargs.get("label"))
        elif query == "commits":
            report["results"] = project_graph.find_commits(
                graph, kwargs.get("timeline"))
        elif query == "project_events":
            report["results"] = project_graph.find_project_events(
                graph, kwargs.get("task"), kwargs.get("event_type"))
        elif query == "reaction_decisions":
            report["results"] = project_graph.find_reaction_decisions(
                graph, kwargs.get("clip_id"), kwargs.get("at_time"))
        elif query == "participants":
            level = kwargs.get("level")
            if not level:
                report["error"] = "participants needs --level"
                return report
            try:
                report["results"] = project_graph.participants_with(
                    graph, level)
            except ValueError as exc:
                report["error"] = str(exc)
                return report
        elif query == "proposals":
            report["results"] = project_graph.open_proposals(graph)
        else:
            report["error"] = (
                f"unknown project graph query {query!r}; "
                "known: reaction_shots, alternate_takes, coverage, "
                "broll_matches, shot_colour, reel_versions, "
                "editorial_findings, vfx_detections, variant_branches, "
                "audio_events, commits, project_events, "
                "reaction_decisions, participants, proposals")
        return report

    @staticmethod
    def _text_matches(query_tokens: list, text: str) -> bool:
        """Whether a query token and an on-screen string refer to the same text.

        Specific, not substring-generous: an exact word matches at any
        length ("SHOP" finds "SHOP", "TAB" finds "TAB"), while a
        substring match counts only at ``OCR_SUBSTRING_MIN`` or above.
        The old both-directions rule fired on any shared letter, so
        single-character tracked strings ("a", "N", "D") flooded every
        visual query ("d" is in "handheld") ahead of the true camera
        window (round-2 bakeoff finding).  "SCUFFLEWA" still matches
        "SCUFFLEWA BREWING CO" as an exact word, and "brewing" in "where
        does the brewing sign appear" still matches the same way.
        """
        return ocr_text_matches(query_tokens, text)

    def ocr_search(self, query: str, top_k: int = 5) -> dict:
        """Find the spans where an on-screen string was read off the footage.

        Answers "where does the SCUFFLEWA sign appear" (or any tracked
        on-screen text) from step 1.07's extraction - the tracked strings
        EasyOCR read at 1 fps and `OCRExtractor` stored per clip, joined
        here by text.

        The match is by text, not by embedding: there is no embedding for
        an on-screen string, so a query that matches no tracked text is an
        honest empty that NAMES the strings this footage actually carries,
        the same way `sound_search` names its labels.  An index with no OCR
        at all says that too, rather than answering "nowhere" to a question
        that was never measured.
        """
        texts_by_clip = self.payload.get("ocr_texts") or {}
        report = {
            "query": query,
            "mode": "ocr",
            "hits": [],
            "considered": 0,
            "matched": 0,
            "available_texts": [],
            "abstained": False,
            "error": None,
        }
        if not texts_by_clip:
            report["abstained"] = True
            report["error"] = (
                "no on-screen text in this index - step 1.07 "
                "(ocr_extraction) never ran for any clip, so there is "
                "no tracked text to search")
            return report

        tokens = _tokenize(query)
        if not tokens:
            report["abstained"] = True
            report["error"] = "no on-screen text to look for"
            return report

        texts = sorted({
            detection.get("text", "")
            for detections in texts_by_clip.values()
            for detection in detections
            if detection.get("text")
        })
        report["available_texts"] = texts

        hits = []
        for clip_id in sorted(texts_by_clip):
            for detection in texts_by_clip[clip_id]:
                report["considered"] += 1
                if not self._text_matches(tokens, detection.get("text", "")):
                    continue
                for start, end in detection.get("appearances") or []:
                    hits.append({
                        "clip_id": clip_id,
                        "text": detection.get("text"),
                        "start": start,
                        "end": end,
                        "timecode": f"{self._timecode(start)}-{self._timecode(end)}",
                        "confidence": detection.get("confidence"),
                    })
        hits.sort(key=lambda h: (h["clip_id"], h["start"] or 0.0))
        report["hits"] = hits[:max(0, top_k)]
        report["matched"] = len(hits)
        report["abstained"] = not hits
        return report

    def combined_search(self, said: str, shown: str, top_k: int = 5) -> dict:
        """Join dialogue against on-screen text (GEO-027).

        Answers "when does he say X while Y is on screen" from the same
        two lanes `search` already reads: speech segments (what was SAID,
        with source intervals) and step 1.07's tracked text (what was
        SHOWN, with appearance intervals and normalized boxes). The join
        is overlap on the source clock and cites both provenances; it
        never invents a paraphrase. Delegates to
        `library.tools.analysis.ocr_combined_query.combined_query` so the
        benchmark and the product agree on what "same moment" means.
        """
        from library.tools.analysis import ocr_combined_query as ocq

        report: dict = {
            "query": {"said": said, "shown": shown},
            "mode": "combined",
            "hits": [],
            "considered": 0,
            "matched": 0,
            "abstained": False,
            "error": None,
        }
        texts_by_clip = self.payload.get("ocr_texts") or {}
        speech_rows = [
            {"segment_id": f"{s.get('clip_id')}#speech",
             "clip_id": s.get("clip_id"),
             "text": s.get("text", ""),
             "start_seconds": s.get("start"), "end_seconds": s.get("end")}
            for s in self.segments
            if s.get("kind") == "speech" and s.get("text")
        ]
        ocr_tracks = []
        for clip_id in sorted(texts_by_clip):
            for detection in texts_by_clip[clip_id]:
                ocr_tracks.append({
                    "track_id": detection.get("track_id"),
                    "clip_id": clip_id,
                    "text": detection.get("text", ""),
                    "appearances": detection.get("appearances") or [],
                    "bbox_normalized": detection.get("bbox_normalized"),
                    "artifact_ref": {
                        "step": "1_07_ocr_extraction", "clip_id": clip_id,
                        "method": (self.payload.get("ocr_methods") or {}
                                   ).get(clip_id),
                    },
                })
        if not speech_rows or not ocr_tracks:
            report["abstained"] = True
            report["error"] = (
                "combined search needs both lanes: "
                f"speech segments ({len(speech_rows)}) and tracked on-screen "
                f"text ({len(ocr_tracks)})")
            return report
        report["considered"] = len(speech_rows) * len(ocr_tracks)
        joined = ocq.combined_query(speech_rows, ocr_tracks,
                                    said=said, shown=shown)
        hits = []
        for row in joined:
            speech_span = row.get("speech_span") or [None, None]
            text_span = row.get("text_span") or [None, None]
            hits.append({
                "clip_id": row.get("clip_id"),
                "said": row.get("said"),
                "shown": row.get("shown"),
                "speech_span": speech_span,
                "text_span": text_span,
                "overlap_seconds": row.get("overlap_seconds"),
                "timecode": (
                    f"{self._timecode(speech_span[0])}-"
                    f"{self._timecode(text_span[1])}"
                    if speech_span[0] is not None
                    and text_span[1] is not None else None),
                "speech_provenance": row.get("speech_provenance"),
                "text_provenance": row.get("text_provenance"),
            })
        hits.sort(key=lambda h: (-(h["overlap_seconds"] or 0.0),
                                 h["clip_id"] or ""))
        report["hits"] = hits[:max(0, top_k)]
        report["matched"] = len(hits)
        report["abstained"] = not hits
        return report

    def _person_windows(self, person: str) -> list:
        """`[(clip_id, start, end)]` the entity store measured for one
        person - face spans (seen) union voice spans (heard), from
        `person_entity.resolve_person_tracks`. Raises when the query
        names nobody the roster knows, the same way an unknown `kind`
        raises: a silent empty filter would read as "this person has no
        footage" rather than "this name does not resolve"."""
        match = person_entity.find_person(self.project_folder, person)
        if match is None:
            raise RenRefusal(
                f"no person matching {person!r}",
                "the person entity store (M3b) has no face or declared "
                "name resolving to this query",
                "build it first: python3 -m library.tools.person_entity "
                f"build '{self.project_folder}', or check the spelling "
                "against `person_entity roster`")
        windows = [(span["clip_id"], span["start"], span["end"])
                  for span in match["face_spans"]]
        windows += [(span["clip_id"], span["start"], span["end"])
                   for span in match["voice_spans"]]
        return windows

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
        max_face_presence: float | None = None,
        max_motion: float | None = None,
        min_brightness: float | None = None,
        max_brightness: float | None = None,
        min_sharpness: float | None = None,
        max_temporal_noise: float | None = None,
        max_blockiness: float | None = None,
        max_underexposed_fraction: float | None = None,
        max_overexposed_fraction: float | None = None,
        max_camera_translation: float | None = None,
        min_subject_energy: float | None = None,
        max_subject_energy: float | None = None,
        min_subject_center_x: float | None = None,
        max_subject_center_x: float | None = None,
        min_subject_width_fraction: float | None = None,
        min_motion: float | None = None,
        contains: str | None = None,
        person: str | None = None,
        min_speech_level: float | None = None,
        max_speech_level: float | None = None,
        music_genre: str | None = None,
        ambience_type: str | None = None,
        kinds: list[str] | tuple[str, ...] | None = None,
    ) -> list:
        """Select footage by measurable properties, with no query at all.

        Args:
            kind: one of speech, action, scene, camera, object, chunk.
            kinds: any set of segment kinds, useful when a request accepts
                several evidence types but must exclude others before ranking.
            clip_id: restrict to one clip ("clip_012").
            framing: shot size as the vision pass reported it
                ("close-up", "medium", "wide").
            camera_mode: "selfie", "mounted", "handheld" as reported.
            stability: "steady", "shaky" as reported.
            movement / scene_type / content_type: as reported.
            has_speech: True selects only speech segments.
            min_duration / max_duration: segment length in seconds.
            min_face_presence / max_face_presence: mean face-detection rate
                over the span, 0..1, reduced from step 1.04's 5 Hz curve.
                A low `max_face_presence` is how "nobody in frame" is asked
                for - and note that a clip whose face curve 1.04 never wrote
                fails the bound rather than passing it, because an
                unmeasured face is not an absent one.
            min_motion / max_motion: mean motion energy over the span,
                reduced from step 1.04's 30 Hz curve.
            min_brightness / max_brightness: mean 0..1 luma brightness
                curve from step 1.04, reduced over the span.
            min_sharpness: minimum mean Laplacian variance, in squared
                8-bit luma levels, from VI-03's sampled 180px-short-side
                frames. This is a texture-sensitive sharpness proxy.
            max_temporal_noise: maximum mean global-luma-compensated
                temporal residual, normalized to 0..1 (VI-03).
            max_blockiness: maximum mean 8-pixel boundary contrast excess,
                normalized to 0..1 (VI-03). All three require measured
                coverage; missing measurements fail the bound closed.
            max_underexposed_fraction / max_overexposed_fraction:
                maximum mean fraction of decoded pixels at the black
                (luma <=4) / white (luma >=251) rail, 0..1, from VI-03's
                sampled frames. Shadow/highlight-loss proxies; missing
                coverage fails the bound closed.
            max_camera_translation: maximum mean per-sample global shift
                read as camera motion, in normalized proxy-frame
                displacements from the 1.04 flow decomposition. A low
                value is how "stable shot" is asked for; missing
                decomposition fails the bound closed.
            min_subject_energy / max_subject_energy: mean residual flow
                the camera model does not explain, in the same
                normalized units - the subject-motion side of the same
                decomposition. Missing decomposition fails closed.
            min_subject_center_x / max_subject_center_x: horizontal center
                of the largest detected face, normalized 0 at left and 1
                at right. This does not claim identity or full-body framing.
            min_subject_width_fraction: detected face width divided by
                display-frame width (0..1).
            contains: plain substring match against the segment text.
            person: a person_id ("person_001") or declared name
                ("Craig") from the entity store (M3b). Keeps segments
                whose clip and time range overlap that person's measured
                face or voice spans - this is how "who says X" and
                "clips of this person" are answered. Raises when the
                name resolves to nobody the store knows.
            min_speech_level / max_speech_level: absolute speech level
                in dBFS (Gap 12). Cross-clip comparable, unlike the
                per-clip normalized energy curve.
            music_genre: music genre label (Gap 12), e.g. "rock",
                "jazz", "classical". Matches clips whose music genre
                classification includes this label.
            ambience_type: ambience type (Gap 12), e.g. "indoor",
                "outdoor", "urban", "nature". Matches clips whose
                ambience classification equals this type.

        Returns:
            Every matching segment, in index order.
        """
        if kind is not None and kind not in SEGMENT_KINDS:
            raise RenRefusal(
                f"unknown kind {kind!r}",
                f"the index only holds these kinds: {sorted(SEGMENT_KINDS)}",
                f"use --kind {' / --kind '.join(sorted(SEGMENT_KINDS))}")
        if kinds is not None:
            unknown = set(kinds) - set(SEGMENT_KINDS)
            if unknown:
                raise RenRefusal(
                    f"unknown kinds {sorted(unknown)!r}",
                    f"the index only holds these kinds: {sorted(SEGMENT_KINDS)}",
                    f"use kinds from {sorted(SEGMENT_KINDS)}")

        person_windows = self._person_windows(person) if person else None

        # Audio classification per clip (Gap 12)
        audio_cls = self.payload.get("audio_classification") or {}

        # A named facet must MATCH; an unmeasured numeric facet fails its
        # bound rather than passing it, because "not measured" is not
        # evidence that the footage is steady or that a face is present.
        named = {
            "framing": framing, "camera_mode": camera_mode,
            "stability": stability, "movement": movement,
            "scene_type": scene_type, "content_type": content_type,
        }
        bounds = (
            ("face_presence", min_face_presence, max_face_presence),
            ("motion", min_motion, max_motion),
            ("brightness", min_brightness, max_brightness),
            ("sharpness_laplacian_variance", min_sharpness, None),
            ("temporal_noise_residual", None, max_temporal_noise),
            ("compression_blockiness_proxy", None, max_blockiness),
            ("underexposed_fraction", None, max_underexposed_fraction),
            ("overexposed_fraction", None, max_overexposed_fraction),
            ("camera_translation_magnitude", None, max_camera_translation),
            ("subject_energy", min_subject_energy, max_subject_energy),
            ("subject_center_x", min_subject_center_x, max_subject_center_x),
            ("subject_width_fraction", min_subject_width_fraction, None),
        )

        def _overlaps_person(segment: dict) -> bool:
            for window_clip, window_start, window_end in person_windows:
                if (segment["clip_id"] == window_clip
                        and segment["start"] < window_end
                        and segment["end"] > window_start):
                    return True
            return False

        def _audio_classification_for(segment: dict) -> dict:
            return audio_cls.get(segment["clip_id"]) or {}

        def keeps(segment: dict) -> bool:
            facets = segment.get("facets", {})
            if kind is not None and segment["kind"] != kind:
                return False
            if kinds is not None and segment["kind"] not in kinds:
                return False
            if person_windows is not None and not _overlaps_person(segment):
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
            # Audio classification filters (Gap 12)
            if (min_speech_level is not None or max_speech_level is not None
                    or music_genre is not None or ambience_type is not None):
                cls = _audio_classification_for(segment)
                speech_level = (cls.get("speech_level") or {}).get(
                    "speech_level_dbfs")
                if min_speech_level is not None:
                    if speech_level is None or speech_level < min_speech_level:
                        return False
                if max_speech_level is not None:
                    if speech_level is None or speech_level > max_speech_level:
                        return False
                if music_genre is not None:
                    genres = (cls.get("music_genre") or {}).get("genres") or []
                    genre_labels = [g.get("label", "").lower() for g in genres]
                    if music_genre.lower() not in genre_labels:
                        return False
                if ambience_type is not None:
                    ambience = (cls.get("ambience_type") or {}).get("ambience")
                    if ambience is None or ambience.lower() != ambience_type.lower():
                        return False
            return not (contains is not None
                        and contains.lower() not in segment["text"].lower())

        return [self._result(s, {}) for s in self.segments if keeps(s)]

    def search_and_filter(self, query: str, top_k: int = 10, mode: str = "hybrid",
                          floor: float | None = None, **filter_kwargs) -> list:
        """Semantic search restricted to segments passing the filters.

        This is the recommended method for an LLM: it lets the model say
        both what it is looking for and what the shot has to be, in one
        call.  Filters are applied FIRST so `top_k` counts survivors, and
        the score floor is applied to those survivors - so an empty list
        here means either "nothing is that kind of shot" or "nothing that
        kind of shot is about that".  `search_report` tells them apart.
        """
        report = self.search_report(query, top_k=top_k, mode=mode, floor=floor,
                                    filters=filter_kwargs)
        if report.get("error") and not report["results"]:
            return []
        return report["results"]

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

    def transcript_report(self, clip_id: str,
                         resolve_segments: list | None = None,
                         resolve_fps=None,
                         resolve_start_tc=None) -> dict:
        """`transcript`, plus Resolve's rows beside Ren's where given.

        `resolve_segments` carries Resolve's voiced segments for the
        clip (the JSON list its transcription wrote), merged through
        `merge_transcript_evidence`: extra evidence beside Ren's answer,
        never a replacement for it. Times normalize through
        `resolve_fps`/`resolve_start_tc` when Resolve reported
        timecodes rather than clip seconds.
        """
        report = {"clip_id": clip_id,
                  "utterances": self.transcript(clip_id)}
        if resolve_segments is not None:
            report.update(merge_transcript_evidence(
                report["utterances"], resolve_segments,
                fps=resolve_fps, start_tc=resolve_start_tc))
        return report

    def staleness(self) -> dict:
        """Whether the ingest has moved since this index was built.

        An index built before a re-transcription answers with the old
        words and has no way to know it, so the comparison is made
        explicit rather than left to whoever remembers.  Returns
        ``{"stale", "reason", "built_at", "files_now", "files_then"}``.

        An index written before this recorded a fingerprint cannot be
        judged, and says so rather than claiming to be current.
        """
        recorded = self.payload.get("ingest_fingerprint")
        now = ingest_fingerprint(self.project_folder)
        out = {
            "built_at": self.payload.get("built_at"),
            "files_now": now["files"],
            "files_then": (recorded or {}).get("files"),
        }
        if not recorded:
            out.update(stale=None,
                       reason="this index recorded no ingest fingerprint, so it cannot be checked")
            return out
        if recorded.get("digest") == now["digest"]:
            out.update(stale=False, reason="the ingest is byte-for-byte what this index was built from")
            return out
        moved = now["files"] - recorded.get("files", 0)
        detail = (f"{abs(moved)} ingest file(s) {'appeared' if moved > 0 else 'went away'}"
                  if moved else "an ingest file changed")
        out.update(stale=True, reason=f"{detail} since this index was built")
        return out

    def summary(self) -> dict:
        """What is in this index: clips, kinds, coverage, indexed seconds."""
        segments = self.segments
        kinds = Counter(s["kind"] for s in segments)
        clips = sorted({s["clip_id"] for s in segments})
        framings = Counter(
            s.get("facets", {}).get("framing", "unknown") for s in segments
        )
        speech = [s for s in segments if s["kind"] == "speech"]
        sound_events = self.payload.get("sound_events") or {}
        ocr_texts = self.payload.get("ocr_texts") or {}
        return {
            "project_folder": self.payload.get("project_folder"),
            "segment_count": len(segments),
            "clips": len(clips),
            "clip_ids": clips,
            "kinds": dict(kinds),
            "framings": dict(framings),
            "spoken_seconds": round(sum(s["duration"] for s in speech), 1),
            "spoken_words": sum(len(s.get("words") or []) for s in speech),
            "sound_event_clips": len(sound_events),
            "sound_event_count": sum(len(v) for v in sound_events.values()),
            "ocr_text_clips": len(ocr_texts),
            "ocr_text_count": sum(len(v) for v in ocr_texts.values()),
            "embed_model": self.payload.get("embed_model"),
            "embed_backend": self.payload.get("embed_backend"),
            "built_at": self.payload.get("built_at"),
            "coverage": self.payload.get("coverage", {}),
        }

    # ── tool definitions (for LLM function calling) ──

    @staticmethod
    def get_tool_definitions() -> list:
        """OpenAI-style tool definitions for the footage and graph queries.

        Pass these to a model's `tools` parameter so a planning step can
        query the footage instead of being handed all of it.  Nothing in
        the pipeline does this today, by design.
        """
        from library.tools import project_graph
        finding_kinds = list(project_graph.FINDING_KINDS)
        facet_properties = {
            "kind": {
                "type": "string",
                "enum": list(SEGMENT_KINDS),
                "description": (
                    "What kind of observation to search: 'speech' (one spoken "
                    "utterance, with word timings), 'action' (what the subject "
                    "did, roughly a 10s window), 'scene' (where it is), 'camera' "
                    "(how it is shot), 'object' (one appearance of one thing), "
                    "'chunk' (one fixed 8s window joining what it overlaps - "
                    "the coarse guarantee every span of footage has a row)."
                ),
            },
            "kinds": {
                "type": "array",
                "items": {"type": "string", "enum": list(SEGMENT_KINDS)},
                "description": "Restrict to any of these segment kinds before ranking.",
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
            "max_face_presence": {
                "type": "number",
                "description": "0..1 mean face-detection rate over the span. Use a low value to find footage with nobody in frame.",
            },
            "max_motion": {
                "type": "number",
                "description": "0..1 mean motion energy over the span. Use a low value to find steady footage.",
            },
            "min_motion": {
                "type": "number",
                "description": "Minimum 0..1 mean motion energy over the span.",
            },
            "min_brightness": {
                "type": "number",
                "description": "Minimum mean 0..1 brightness from the step 1.04 luma curve.",
            },
            "max_brightness": {
                "type": "number",
                "description": "Maximum mean 0..1 brightness from the step 1.04 luma curve.",
            },
            "min_sharpness": {
                "type": "number",
                "description": "Minimum mean VI-03 Laplacian variance (squared 8-bit luma levels); texture-sensitive proxy, missing coverage fails closed.",
            },
            "max_temporal_noise": {
                "type": "number",
                "description": "Maximum VI-03 global-luma-compensated temporal residual, normalized 0..1; proxy, missing coverage fails closed.",
            },
            "max_blockiness": {
                "type": "number",
                "description": "Maximum VI-03 8-pixel boundary contrast excess proxy, normalized 0..1; missing coverage fails closed.",
            },
            "max_underexposed_fraction": {
                "type": "number",
                "description": "Maximum mean fraction of decoded pixels at luma <=4 (shadow loss at the black rail), 0..1 from VI-03; missing coverage fails closed.",
            },
            "max_overexposed_fraction": {
                "type": "number",
                "description": "Maximum mean fraction of decoded pixels at luma >=251 (highlight loss at the white rail), 0..1 from VI-03; missing coverage fails closed.",
            },
            "max_camera_translation": {
                "type": "number",
                "description": "Maximum mean per-sample camera shift in normalized proxy-frame displacements (1.04 flow decomposition). Use a low value to find stable shots; missing decomposition fails closed.",
            },
            "min_subject_energy": {
                "type": "number",
                "description": "Minimum mean residual flow the camera model does not explain, same normalized units; the subject-motion side of the decomposition.",
            },
            "max_subject_energy": {
                "type": "number",
                "description": "Maximum mean residual flow the camera model does not explain, same normalized units.",
            },
            "min_subject_center_x": {
                "type": "number",
                "description": "Minimum detected largest-face horizontal center, normalized 0 left to 1 right.",
            },
            "max_subject_center_x": {
                "type": "number",
                "description": "Maximum detected largest-face horizontal center, normalized 0 left to 1 right.",
            },
            "min_subject_width_fraction": {
                "type": "number",
                "description": "Minimum detected face width divided by display-frame width, normalized 0..1.",
            },
            "person": {
                "type": "string",
                "description": (
                    "Restrict to one person's measured face or voice spans "
                    "(person_id like 'person_001', or a declared name like "
                    "'Craig') from the entity store. Use for 'who says X' "
                    "or 'clips of this person'."
                ),
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
            {
                "type": "function",
                "function": {
                    "name": "find_reaction_shots",
                    "description": (
                        "Find shots where another person is visible while a speaker talks. "
                        "Use for reaction cutaways: 'find the best reaction shot' for a "
                        "speaker at a given time."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "person_id": {"type": "string", "description": "The speaker's person_id (e.g. 'person_001')."},
                            "around_t": {"type": "number", "description": "The time in the speaker's source timeline to look around."},
                        },
                        "required": ["person_id", "around_t"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "find_alternate_takes",
                    "description": (
                        "Find other shots covering the same content as a clip. An alternate "
                        "take is a different clip where the same line is spoken. Use for "
                        "coverage: 'what other angles cover this moment'."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "clip_id": {"type": "string", "description": "The clip to find alternates for."},
                        },
                        "required": ["clip_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "find_coverage",
                    "description": (
                        "Find which shots cover a timeline span. Use to check coverage: "
                        "what footage is available for this part of the edit."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "timeline_start": {"type": "number", "description": "Start of the span in seconds."},
                            "timeline_end": {"type": "number", "description": "End of the span in seconds."},
                        },
                        "required": ["timeline_start", "timeline_end"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "find_broll_matches",
                    "description": (
                        "Find shots whose content matches a speech segment. Use for B-roll "
                        "selection: what footage illustrates what is being said."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "segment_id": {"type": "string", "description": "The transcript segment id (e.g. 'transcript:abc123:5')."},
                        },
                        "required": ["segment_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "find_shot_colour",
                    "description": (
                        "Get the colour track's registration for one shot: its measured "
                        "row (exposure, white balance, contrast, saturation, skin vector), "
                        "the grade delivered for it, and its camera match against the "
                        "scene's reference. Use to ask what contrast a shot has or "
                        "whether two angles match, without re-measuring the footage."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "clip_id": {"type": "string", "description": "The clip to read the colour registration for."},
                        },
                        "required": ["clip_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "find_reel_versions",
                    "description": (
                        "List the versions each reel has held: version number, kind "
                        "(build, touch, undo, rollback, redo) and when it landed. "
                        "Use to answer what changed between versions or which "
                        "version is current before referencing one."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "final": {"type": "string", "description": "Restrict to one reel (e.g. 'reel_001'). Omit for every reel."},
                        },
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "find_editorial_findings",
                    "description": (
                        "Read the compile's report-only findings: dead sections "
                        "(timeline spans where nothing happens, with reasons), "
                        "trim proposals, J/L placements with the plan's chosen "
                        "offset or rejection, and selected reaction cutaways. "
                        "Use to reference a finding by node instead of "
                        "re-describing a timeline span."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "kind": {"type": "string", "enum": finding_kinds,
                                     "description": "Restrict to one finding kind. Omit for every kind."},
                            "clip_id": {"type": "string", "description": "Restrict to findings about one clip. Omit for every clip."},
                        },
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "find_vfx_detections",
                    "description": (
                        "List the VFX detections the edit graph recorded: which "
                        "effect was detected on which clip and where. Use to "
                        "ask what was detected on a clip without re-reading "
                        "the edit graph."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "clip_id": {"type": "string", "description": "Restrict to one clip. Omit for every detection."},
                        },
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "find_variant_branches",
                    "description": (
                        "List the variant branches the explorations recorded: "
                        "which branch each variant owns, the base it forked, "
                        "the direction it tries and who planned it. Use to "
                        "compare the variants before the merge queue "
                        "evaluates them."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "exploration": {"type": "string", "description": "Restrict to one exploration id. Omit for every exploration."},
                            "timeline": {"type": "string", "description": "Restrict to variants of one timeline. Omit for every timeline."},
                        },
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "find_audio_events",
                    "description": (
                        "List the audio events the analysis measured: what was "
                        "heard on which clip and when - music changes, "
                        "laughter, applause. Use to ask when the music "
                        "changes without re-listening to the footage."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "clip_id": {"type": "string", "description": "Restrict to one clip. Omit for every clip."},
                            "label": {"type": "string", "description": "Restrict to one kind of sound, e.g. music. Matched case-insensitively. Omit for every sound."},
                        },
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "find_commits",
                    "description": (
                        "List what changed on a reel timeline and why: each "
                        "recorded touch, merge, revert and replay with its "
                        "lineage, agent and intent. Use to answer what a "
                        "reel's last change was without re-reading the "
                        "commit store."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "timeline": {"type": "string", "description": "Restrict to one reel timeline. Omit for every timeline."},
                        },
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "find_project_events",
                    "description": (
                        "List what the workers did: leases taken, checkpoints "
                        "left, reels signed off, with task, time and fields. "
                        "Use to answer what operations ran on a project."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "task": {"type": "string", "description": "Restrict to one worker task. Omit for every task."},
                            "event_type": {"type": "string", "description": "Restrict to one event type. Omit for every type."},
                        },
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "find_reaction_decisions",
                    "description": (
                        "Read the reaction-cutaway decisions the run "
                        "recorded: what each did, why, what evidence it "
                        "rests on and how confident it is. Use to ask why "
                        "a cutaway was chosen."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "clip_id": {"type": "string", "description": "Restrict to decisions naming one clip, speech or reaction. Omit for every clip."},
                            "at_time": {"type": "number", "description": "Restrict to the speech span reacting at this timeline time. Omit for every span."},
                        },
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "participants_with",
                    "description": (
                        "List the rostered participants whose authority "
                        "level grants a level: asking who may approve "
                        "returns approvers and above, never observe-only "
                        "watchers. Use to answer who may approve this."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "level": {"type": "string", "description": "The authority level to grant, e.g. approve."},
                        },
                        "required": ["level"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "open_proposals",
                    "description": (
                        "List the undecided proposals awaiting approval: "
                        "author, operation and rationale, oldest first. "
                        "Use to answer what awaits approval."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
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


def _print_external_evidence(report):
    """Resolve IntelliSearch rows as a person reads them: extra, not Ren.

    Printed after Ren's own answer on every path - including the abstain -
    so Resolve's guess can never be mistaken for Ren's measurement.
    """
    rows = report.get("external_evidence") or []
    refused = report.get("external_rejected") or []
    if not rows and not refused:
        return
    print(f"\nResolve {report.get('external_source')} matched "
          f"{len(rows)} clip(s) - extra evidence, NOT Ren-ranked; "
          f"Ren's answer above stands as measured.")
    for i, row in enumerate(rows):
        span = row.get("timecode")
        if span is None and row.get("start") is not None:
            span = f"{row.get('start')}-{row.get('end')}"
        what = row.get("text") or row.get("label") or row.get("query") or ""
        print(f"  E{i + 1}. {row['clip_id']}  {span or 'whole clip'}  :: {what}")
    for item in refused:
        print(f"  refused external row {item['position']}: {item['reason']}")


def _print_transcript_evidence(report):
    """Resolve transcription rows as a person reads them: extra, not Ren.

    Agreements pass silently; every disagreement prints with both
    sides and its spans, so a reader judges the words rather than a
    verdict.
    """
    rows = report.get("resolve_segments") or []
    if not rows and not report.get("resolve_empty"):
        return
    print(f"\nResolve {report.get('resolve_source')} heard "
          f"{len(rows)} segment(s) - extra evidence, NOT Ren-ranked; "
          f"Ren's utterances above stand as measured.")
    if report.get("resolve_empty"):
        print("  Resolve transcribed nothing on this clip - stated, "
              "not expanded into findings.")
        return
    print(f"  {len(report['agreement'])} in agreement, "
          f"{len(report['findings'])} finding(s):")
    for finding in report["findings"]:
        ren = finding.get("ren") or {}
        resolve = finding.get("resolve") or {}
        print(f"  {finding['kind']}: "
              f"ren {ren.get('start')}-{ren.get('end')} "
              f":: {ren.get('text')!r}  /  resolve "
              f"{resolve.get('start')}-{resolve.get('end')} "
              f":: {resolve.get('text')!r}")


def _print_report(report):
    """A search report as a person reads it, abstain included."""
    bits = [f"query: {report['query']!r}", f"mode={report['mode']}",
            f"floor={report['floor']:.2f}",
            f"{report['considered']}/{report['segment_count']} segments considered"]
    print("  ".join(bits))
    if "constraint_candidates" in report:
        print("retrieval candidates: "
              f"{report['segment_count']} -> {report['constraint_candidates']} "
              f"by constraints; {report['embedding_candidates']} to embedding")
    print(f"backend: {report.get('backend_answered') or 'unknown'}")
    coverage = report.get("coverage", {})
    per_clip = coverage.get("per_clip", {})
    if per_clip:
        modalities = ("speech", "picture", "scene", "action", "object",
                      "ocr", "expressions", "sound", "video_embeddings")
        counts = [f"{name}={sum(bool(c.get(name)) for c in per_clip.values())}/{len(per_clip)}"
                  for name in modalities]
        print("coverage: " + "  ".join(counts))
    if report.get("degraded"):
        print(f"DEGRADED: {report['degraded']}")
    if report.get("error"):
        print(report["error"])
        _print_external_evidence(report)
        return
    if report["results"]:
        _print_hits(report["results"])
        _print_external_evidence(report)
        return

    print("\nNOTHING in this footage cleared the floor - that is the answer, "
          "not an empty result set.")
    best = report.get("best_rejected")
    if best:
        print(f"  closest: {best['dense_score']:.3f} < {report['floor']:.2f}  "
              f"[{best['kind']}] {best['clip_id']} :: {best['text'][:100]}")
    if report["weak"]:
        print(f"\n  {len(report['weak'])} weak match(es) between "
              f"{report['weak_floor']:.2f} and {report['floor']:.2f} - not confident:")
        _print_hits(report["weak"])
    _print_external_evidence(report)


def _print_chunk_report(report):
    """A video-chunk report as a person reads it, abstain included."""
    bits = [f"query: {report['query']!r}", "mode=video_chunks",
            f"floor={report['floor']:.2f}",
            f"{report['considered']}/{report['chunk_count']} chunks considered"]
    print("  ".join(bits))
    if report.get("video_embed_backend"):
        print(f"backend: {report['video_embed_backend']}")
    if report.get("error"):
        print(report["error"])
        return
    if report["results"]:
        for i, hit in enumerate(report["results"]):
            print(f"\n{i + 1}. {hit['clip_id']} ({hit['filename']}) "
                  f"{hit['timecode']}  dense {hit['dense_score']:.3f}")
        return
    print("\nNOTHING in this footage cleared the floor - that is the answer, "
          "not an empty result set.")
    best = report.get("best_rejected")
    if best:
        print(f"  closest: {best['dense_score']:.3f} < {report['floor']:.2f}  "
              f"{best['chunk_id']} {best['clip_id']}")
    if report["weak"]:
        print(f"\n  {len(report['weak'])} weak match(es) between "
              f"{report['weak_floor']:.2f} and {report['floor']:.2f} - not confident:")
        for hit in report["weak"]:
            print(f"  {hit['clip_id']} {hit['timecode']}  "
                  f"dense {hit['dense_score']:.3f}")


def _print_unified_report(report):
    """A unified-anchor report as a person reads it, abstain included."""
    bits = [f"query: {report['query']!r}", "mode=unified",
            f"floor={report['floor']:.2f}",
            f"{report['considered']} windows considered",
            f"anchors: {','.join(report['anchors'])}"]
    print("  ".join(bits))
    if report.get("error") and not report["results"]:
        print(report["error"])
        return
    if report.get("degraded"):
        print(f"DEGRADED: {report['degraded']}")
    if report["results"]:
        for i, hit in enumerate(report["results"]):
            anchors = " ".join(
                f"{k}={hit[k]:.3f}" for k in sorted(hit)
                if k.endswith("_score") and k not in ("score", "lexical_score"))
            print(f"\n{i + 1}. {hit['clip_id']} {hit['timecode']}  "
                  f"score {hit['score']:.3f}  {anchors}")
            if hit.get("description_text"):
                print(f"   desc: {hit['description_text'][:120]}")
        return
    print("\nNOTHING in this footage cleared the floor - that is the answer, "
          "not an empty result set.")
    if report["weak"]:
        print(f"\n  {len(report['weak'])} weak match(es) between "
              f"{report['weak_floor']:.2f} and {report['floor']:.2f} - not confident:")
        for hit in report["weak"]:
            print(f"  {hit['clip_id']} {hit.get('anchor_id')}  "
                  f"dense {hit['dense_score']:.3f}")


def _print_frame_report(report):
    """A frame search report as a person reads it: ranges, never absence."""
    print(f"query: {report['query']!r}  scope={report['scope']}  "
          f"{report['frame_count']} frames indexed  "
          f"answer_status={report['answer_status']}")
    if report.get("embed_backend"):
        print(f"backend: {report['embed_backend']}")
    if report.get("error"):
        print(report["error"])
        return
    print(f"NOTICE: {report['notice']}")
    if not report["ranges"]:
        print("\nNo ranges - the frame index holds nothing to rank.")
        return
    for i, span in enumerate(report["ranges"]):
        print(f"\n{i + 1}. {span['clip_id']} ({span['filename']}) "
              f"{span['timecode']}  ({span['duration']:.1f}s)")
        print(f"   best {span['best_score']:.3f} across {span['n_frames']} frame(s)"
              f"  [UNVERIFIED]")


def _print_structured(answer):
    naming = answer["naming"]
    named = (f" = {naming['name']} ({naming['by']}, agreement "
             f"{naming['agreement']})" if naming.get("name")
             else f" ({naming['by']})")
    asked = (f"{answer['predicate']} verified \"{answer['verify']}\""
             if "verify" in answer else answer["predicate"])
    print(f"{answer['person']}{named} x {asked}: "
          f"{answer['spans']} source spans, {answer['source_seconds']} s; "
          f"{answer['spans_placed']} placed on the timeline")
    if "verify" in answer:
        cost = answer["cost"]
        print(f"  {answer['candidates']} candidates judged: {answer['spans']} yes, "
              f"{len(answer['rejected'])} no; {cost['vlm_calls']} VLM calls "
              f"({cost['vlm_seconds']} s), {cost['cached_verdicts']} recorded verdicts reused")
    for stage, signal in answer["signals"].items():
        print(f"  {stage:9s} {signal}")
    if not answer["timeline_transcript"]:
        print("  (no saved timeline transcript: nothing can be placed)")
    for hit in answer["hits"]:
        name = os.path.basename(hit["source_file"])
        quality = hit.get("capture_quality")
        ordered = (f"  face quality {quality:.2f}"
                   if isinstance(quality, (int, float)) else "")
        print(f"  {name} {hit['start']:9.2f}-{hit['end']:9.2f}{ordered}")
        if "verification" in hit:
            print(f"      VLM: {hit['verification']['reason']}")
        for p in hit["placements"]:
            print(f"      timeline {p['timeline_start']:8.2f}-{p['timeline_end']:8.2f}"
                  f"  item {p['resolve_item_id'][:8]} ({p['track_speaker']} track, "
                  f"{os.path.basename(p['source_file'])}, {p['via']})")


def _print_sound_report(report):
    """A sound-event search as a person reads it: spans, or what IS there."""
    print(f"query: {report['query']!r}  mode=sound  "
          f"{report['considered']} sound event(s) considered")
    if report.get("error"):
        print(report["error"])
        return
    if report["hits"]:
        for i, hit in enumerate(report["hits"]):
            confidence = hit.get("confidence")
            conf = f"  conf {confidence:.2f}" if isinstance(confidence, (int, float)) else ""
            print(f"\n{i + 1}. {hit['clip_id']}  {hit['timecode']}  "
                  f"({hit['label']}){conf}")
        return
    print(f"\nNo sound event labelled {report['query']!r} - that is the answer, "
          f"not an empty result set.")
    if report["available_labels"]:
        print(f"  labels this footage has: {', '.join(report['available_labels'])}")


def _print_ocr_report(report):
    """An on-screen text search as a person reads it: spans, or what IS there."""
    print(f"query: {report['query']!r}  mode=ocr  "
          f"{report['considered']} tracked text(s) considered")
    if report.get("error"):
        print(report["error"])
        return
    if report["hits"]:
        for i, hit in enumerate(report["hits"]):
            confidence = hit.get("confidence")
            conf = f"  conf {confidence:.2f}" if isinstance(confidence, (int, float)) else ""
            print(f"\n{i + 1}. {hit['clip_id']}  {hit['timecode']}  "
                  f"({hit['text']}){conf}")
        return
    print(f"\nNo on-screen text matching {report['query']!r} - that is the answer, "
          f"not an empty result set.")
    if report["available_texts"]:
        print(f"  text this footage carries: {', '.join(report['available_texts'])}")


# ─── Machine-readable answers (`--json` on every mode) ───
#
# LE-06: `ren search --json` works for text, --sound, --ocr and --visual
# queries, not only --predicate.  Every lane returns the same answer
# shape - one list of hits, each a `{source_digest, clip_id, file,
# start_s, end_s, kind, score, dense, band, text}` row - so an agent
# parses one schema whatever it asked.  `source_digest` is the clip's
# content digest (stable across renames and moves); `band` is the floor
# verdict per row (`strong` cleared the floor, `weak` sits in the band
# between `weak_floor` and `floor`).  An answer with no strong row
# carries `abstained: true` plus the floor, so "not in this footage" is
# a field and not prose.  This is the hit shape MU-09's `ren where`
# reuses.


def clip_sources(project_folder) -> dict:
    """`{clip_id: {"source_digest": ..., "file": ...}}` for one project.

    The digest is the clip's content identity (`digest_for_clip`: the
    live file first, the runner's recorded fingerprint when the media
    is offline).  A clip with no resolvable digest is ABSENT, not
    present and null: a reader cannot place a sourceless row, so the
    hit mapper leaves its `source_digest` unset rather than invent one.
    """
    catalog = load_catalog(project_folder)
    try:
        recorded = source_memory.load_recorded_fingerprints(project_folder)
    except (OSError, ValueError):
        recorded = {}
    sources = {}
    for clip in catalog:
        clip_id = clip.get("clip_id")
        if not clip_id:
            continue
        digest, _basis = source_memory.digest_for_clip(
            project_folder, clip, recorded)
        if digest is None:
            continue
        filename = (clip.get("filename")
                    or os.path.basename(
                        clip.get("source_file") or clip.get("path") or "")
                    or clip_id)
        sources[clip_id] = {"source_digest": digest, "file": filename}
    return sources


def _num(value):
    """A JSON-safe number, or None when the lane measured nothing."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _json_hit(*, source_digest, clip_id, file, start_s, end_s, kind,
              score, dense, band, text, region=None,
              region_person=None) -> dict:
    """One stable hit row, whatever lane produced it."""
    row = {
        "source_digest": source_digest,
        "clip_id": clip_id,
        "file": file,
        "start_s": _num(start_s),
        "end_s": _num(end_s),
        "kind": kind,
        "score": _num(score),
        "dense": _num(dense),
        "band": band,
        "text": text if text is not None else "",
    }
    if region is not None:
        row["region"] = region
    if region_person is not None:
        row["region_person"] = region_person
    return row


def _text_json_hits(report, sources) -> list:
    """`search_report` rows as stable hits: results are strong, `weak`
    rows carry the floor band.  The score is the fused rank; `dense` is
    the raw cosine the floor judged on (None on a lexical-only answer,
    which has no cosine to judge)."""
    hits = []
    for band, rows in (("strong", report.get("results") or []),
                       ("weak", report.get("weak") or [])):
        for row in rows:
            src = sources.get(row.get("clip_id", ""), {})
            hits.append(_json_hit(
                source_digest=src.get("source_digest"),
                clip_id=row.get("clip_id"),
                file=row.get("filename") or src.get("file", ""),
                start_s=row.get("start"), end_s=row.get("end"),
                kind=row.get("kind"),
                score=row.get("score"), dense=row.get("dense_score"),
                band=band, text=row.get("text", ""),
                region=row.get("region"),
                region_person=row.get("region_person")))
    return hits


def _sound_json_hits(report, sources) -> list:
    """`sound_search` rows as stable hits.  Every row matched a measured
    label, so every row is strong; there is no dense cosine and no
    floor on this lane, and the score is the PANNs confidence."""
    hits = []
    for row in report.get("hits") or []:
        src = sources.get(row.get("clip_id", ""), {})
        hits.append(_json_hit(
            source_digest=src.get("source_digest"),
            clip_id=row.get("clip_id"),
            file=src.get("file", ""),
            start_s=row.get("start"), end_s=row.get("end"),
            kind="sound",
            score=row.get("confidence"), dense=None,
            band="strong", text=row.get("label", "")))
    return hits


def _ocr_json_hits(report, sources) -> list:
    """`ocr_search` rows as stable hits.  Same terms as sound: an exact
    on-screen match is strong, scored by the reader's confidence."""
    hits = []
    for row in report.get("hits") or []:
        src = sources.get(row.get("clip_id", ""), {})
        hits.append(_json_hit(
            source_digest=src.get("source_digest"),
            clip_id=row.get("clip_id"),
            file=src.get("file", ""),
            start_s=row.get("start"), end_s=row.get("end"),
            kind="ocr",
            score=row.get("confidence"), dense=None,
            band="strong", text=row.get("text", "")))
    return hits


def _chunk_json_hits(report, sources) -> list:
    """`search_chunks` rows as stable hits.  The score IS the raw
    cross-modal cosine, so it doubles as `dense`; results are strong,
    the band rows are weak."""
    hits = []
    for band, rows in (("strong", report.get("results") or []),
                       ("weak", report.get("weak") or [])):
        for row in rows:
            src = sources.get(row.get("clip_id", ""), {})
            hits.append(_json_hit(
                source_digest=src.get("source_digest"),
                clip_id=row.get("clip_id"),
                file=row.get("filename") or src.get("file", ""),
                start_s=row.get("start"), end_s=row.get("end"),
                kind="video_chunk",
                score=row.get("dense_score"), dense=row.get("dense_score"),
                band=band, text=""))
    return hits


def _unified_json_hits(report, sources) -> list:
    """Unified-anchor rows as stable hits.  Results are strong, the band
    rows are weak; `dense` is present only when a dense anchor answered
    (a lexical-only answer has no cosine)."""
    hits = []
    for band, rows in (("strong", report.get("results") or []),
                       ("weak", report.get("weak") or [])):
        for row in rows:
            src = sources.get(row.get("clip_id", ""), {})
            hits.append(_json_hit(
                source_digest=src.get("source_digest"),
                clip_id=row.get("clip_id"),
                file=row.get("filename") or src.get("file", ""),
                start_s=row.get("start"), end_s=row.get("end"),
                kind=row.get("kind", "window"),
                score=row.get("score"), dense=row.get("dense_score"),
                band=band,
                text=row.get("description_text")
                or row.get("transcript_text", "")))
    return hits


def _frame_json_hits(report, sources) -> list:
    """Frame-index ranges as stable hits.  The frame lane has no floor
    and no abstain - it ranks whatever stills it holds, every range
    unverified - so every row is unverified, scored by its best CLIP
    cosine.  A range is picture evidence with no text; `text` is empty
    rather than invented."""
    hits = []
    for span in report.get("ranges") or []:
        src = sources.get(span.get("clip_id", ""), {})
        hits.append(_json_hit(
            source_digest=src.get("source_digest"),
            clip_id=span.get("clip_id"),
            file=span.get("filename") or src.get("file", ""),
            start_s=span.get("start"), end_s=span.get("end"),
            kind="visual",
            score=span.get("best_score"), dense=None,
            band="unverified", text=""))
    return hits


def json_answer(*, query, mode, floor=None, weak_floor=None, abstained=False,
                hits=(), error=None, answer_status=None, extra=None) -> dict:
    """The one `--json` envelope every search lane returns."""
    answer = {
        "query": query,
        "mode": mode,
        "floor": _num(floor),
        "weak_floor": _num(weak_floor),
        "abstained": bool(abstained),
        "hits": list(hits),
    }
    if error is not None:
        answer["error"] = error
    if answer_status is not None:
        answer["answer_status"] = answer_status
    if extra:
        answer.update(extra)
    return answer


def search_json(project_folder, query, *, mode="hybrid", top_k=5, floor=None,
                signal_merge=False, person=None, lane="text",
                include_actions=False, index_dir=None,
                external_hits=None, filters=None) -> dict:
    """One machine-readable answer for every `ren search` lane.

    `lane` is `text` (the default index), `sound`, `ocr`, `visual`
    (frame ranges), `chunks` or `unified`.  The `--predicate` lane is
    not here: it already prints its own JSON.  A refused visual query
    (an action the frame signal cannot resolve) raises `RenRefusal`
    the same way the human path does - a refusal is not an answer.
    """
    sources = clip_sources(project_folder)
    if lane == "sound":
        idx = FootageIndex(project_folder, index_dir=index_dir)
        report = idx.sound_search(query, top_k=top_k)
        return json_answer(
            query=query, mode="sound", abstained=report["abstained"],
            hits=_sound_json_hits(report, sources),
            error=report.get("error"),
            extra={"available_labels": report.get("available_labels", [])})
    if lane == "ocr":
        idx = FootageIndex(project_folder, index_dir=index_dir)
        report = idx.ocr_search(query, top_k=top_k)
        return json_answer(
            query=query, mode="ocr", abstained=report["abstained"],
            hits=_ocr_json_hits(report, sources),
            error=report.get("error"),
            extra={"available_texts": report.get("available_texts", [])})
    if lane == "visual":
        from library.tools.analysis import footage_frames
        vidx = footage_frames.FrameIndex(project_folder, index_dir=index_dir)
        report = vidx.search_ranges(query, top_ranges=top_k,
                                    include_actions=include_actions)
        if report["refused"] is not None:
            raise RenRefusal(
                f"frame search refused {report['query']!r}",
                report["refused"]["reason"],
                report["refused"]["hint"])
        return json_answer(
            query=query, mode="visual",
            abstained=not report["ranges"],
            hits=_frame_json_hits(report, sources),
            error=report.get("error"),
            answer_status=report["answer_status"],
            extra={"notice": report["notice"]})
    if lane == "chunks":
        idx = FootageIndex(project_folder, index_dir=index_dir)
        report = idx.search_chunks(query, top_k=top_k, floor=floor)
        answer = json_answer(
            query=query, mode="video_chunks", floor=report["floor"],
            weak_floor=report["weak_floor"], abstained=report["abstained"],
            hits=_chunk_json_hits(report, sources),
            error=report.get("error"))
        return answer
    if lane == "unified":
        from library.tools.analysis.unified_anchors import UnifiedIndex
        uidx = UnifiedIndex(project_folder, index_dir=index_dir)
        report = uidx.search_unified(query, top_k=top_k, mode=mode,
                                     floor=floor)
        return json_answer(
            query=query, mode="unified", floor=report.get("floor"),
            weak_floor=report.get("weak_floor"),
            abstained=report["abstained"],
            hits=_unified_json_hits(report, sources),
            error=report.get("error"))
    idx = FootageIndex(project_folder, index_dir=index_dir)
    report = idx.search_report(
        query, top_k=top_k, mode=mode, floor=floor,
        signal_merge=signal_merge,
        filters={"person": person} if filters is None else filters,
        external_hits=external_hits)
    answer = json_answer(
        query=query, mode=mode, floor=report["floor"],
        weak_floor=report["weak_floor"], abstained=report["abstained"],
        hits=_text_json_hits(report, sources),
        error=report.get("error"), answer_status=report["answer_status"])
    answer["retrieval"] = {
        "index_candidates": report["segment_count"],
        "constraint_candidates": report["constraint_candidates"],
        "constraint_reduction": report["constraint_reduction"],
        "embedding_candidates": report["embedding_candidates"],
    }
    for key in ("external_source", "external_evidence", "external_rejected"):
        if report.get(key) is not None:
            answer[key] = report[key]
    return answer


def where_answer(project_folder, person, at=None, memory_root=None) -> dict:
    """Where one person is: every M3d region track they own, or the one
    region at `at` seconds (MU-09, `ren where`).

    `person` is a roster person id or declared name; anything else
    refuses the way `--person` filters do, never a best-effort
    neighbour. Without `at`, answers each digest's owned region tracks
    with their spans; with `at`, answers per digest the region at that
    source second, or the absence and its reason - never the nearest
    sample. A digest with no measured M3d reports `m3d: "missing"`
    rather than inventing a region.
    """
    try:
        from library.tools import region_query
    except ImportError:  # pragma: no cover - same-tree import
        from tools import region_query  # type: ignore[no-redef]
    root = Path(memory_root).expanduser() if memory_root else None
    match = person_entity.find_person(project_folder, person, root)
    if match is None:
        raise RenRefusal(
            f"no person matching {person!r}",
            "the person entity store (M3b) has no face or declared "
            "name resolving to this query",
            "build it first: python3 -m library.tools.person_entity "
            f"build '{project_folder}', or check the spelling "
            "against `person_entity roster`")
    person_id = match["person_id"]
    catalog = load_catalog(project_folder)
    try:
        recorded = source_memory.load_recorded_fingerprints(project_folder)
    except (OSError, ValueError):
        recorded = {}
    digest_clips: dict[str, list] = {}
    for clip in catalog:
        clip_id = clip.get("clip_id")
        if not clip_id:
            continue
        digest, _basis = source_memory.digest_for_clip(
            project_folder, clip, recorded)
        if digest is None:
            continue
        digest_clips.setdefault(digest, []).append(clip_id)
    person_digests = {track.get("content_digest")
                      for track in match.get("face_tracks") or []
                      if isinstance(track, dict)
                      and track.get("content_digest")}
    answer: dict = {"person_id": person_id, "name": match.get("name"),
                    "project": os.path.abspath(project_folder)}
    if at is None:
        tracks = []
        for digest in sorted(person_digests):
            doc = region_query.load_m3d_for_digests([digest], root).get(digest)
            if doc is None:
                tracks.append({"content_digest": digest,
                               "clip_ids": digest_clips.get(digest, []),
                               "m3d": "missing"})
                continue
            faces = {track.get("track_id")
                     for track in match.get("face_tracks") or []
                     if isinstance(track, dict)
                     and track.get("content_digest") == digest}
            for track in doc.get("tracks") or []:
                if (isinstance(track, dict)
                        and track.get("face_track") in faces):
                    tracks.append({
                        "content_digest": digest,
                        "clip_ids": digest_clips.get(digest, []),
                        "track_id": track.get("track_id"),
                        "start": track.get("start"), "end": track.get("end"),
                        "samples": track.get("samples"),
                        "face_track": track.get("face_track")})
        answer["tracks"] = tracks
        return answer
    try:
        moment = float(at)
    except (TypeError, ValueError):
        raise RenRefusal(
            f"--at {at!r} is not a time",
            "a moment is source seconds, e.g. --at 12.5",
            f"ren where {project_folder} {person} --at 12.5")
    places = []
    for digest in sorted(person_digests):
        row: dict = {"content_digest": digest,
                     "clip_ids": digest_clips.get(digest, [])}
        doc = region_query.load_m3d_for_digests([digest], root).get(digest)
        if doc is None:
            row["m3d"] = "missing"
            row["region"] = {"present": False,
                             "reason": region_query.ABSENT_NO_TRACKS}
            places.append(row)
            continue
        faces = {track.get("track_id")
                 for track in match.get("face_tracks") or []
                 if isinstance(track, dict)
                 and track.get("content_digest") == digest}
        region = region_query.m3d_region_at(doc, faces, moment)
        region["entity_id"] = person_id
        region["content_digest"] = digest
        row["region"] = region
        places.append(row)
    answer["at"] = moment
    answer["places"] = places
    return answer


def _load_external_hits(path):
    """The Resolve-side collector's JSON list, or a refusal naming the file."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as exc:
        raise RenRefusal(
            f"cannot read external hits from {path}",
            f"{type(exc).__name__}: {exc}",
            "hand over the JSON list the Resolve-side collector wrote")


def _print_where(answer: dict) -> None:
    """One human-readable `ren where` answer: tracks, or the region at `--at`."""
    name = answer.get("name") or answer["person_id"]
    if "tracks" in answer:
        tracks = answer["tracks"]
        print(f"{name} ({answer['person_id']}): {len(tracks)} region track(s)")
        for track in tracks:
            clips = ", ".join(track.get("clip_ids") or [])
            if track.get("m3d") == "missing":
                print(f"  {track['content_digest'][:12]} [{clips}]: "
                      f"regions unmeasured")
            else:
                print(f"  {track['content_digest'][:12]} [{clips}] "
                      f"{track['track_id']}: {track['start']:.2f}-"
                      f"{track['end']:.2f}s, {track['samples']} samples")
        return
    print(f"{name} ({answer['person_id']}) at {answer['at']:.2f}s:")
    for place in answer["places"]:
        clips = ", ".join(place.get("clip_ids") or [])
        region = place["region"]
        if region.get("present"):
            print(f"  {place['content_digest'][:12]} [{clips}]: "
                  f"bbox {region['bbox']} interior {region['interior_point']}")
        else:
            print(f"  {place['content_digest'][:12]} [{clips}]: "
                  f"absent ({region['reason']})")


def main(argv=None):
    # Same default as the front door, for direct `-m` use: under a
    # harness the host parses `REN_RESULT` instead of prose (LE-03).
    # A `ren` verb already set it through the environment, and an
    # explicit value always wins.
    from ren import hosts
    hosts.ensure_harness_output_env()
    parser = argparse.ArgumentParser(
        prog="footage_query",
        description="Cross-clip footage search over a project's own ingest output.",
    )
    sub = parser.add_subparsers(dest="command")

    p_build = sub.add_parser("build", help="Cut and embed the index")
    p_build.add_argument("project")
    p_build.add_argument("--index-dir", help="Where to write (default: <project>/pipeline_output/scratch/footage_index)")
    p_build.add_argument("--kinds", nargs="+", choices=list(SEGMENT_KINDS), default=list(SEGMENT_KINDS))
    p_build.add_argument("--embedder", choices=list(TEXT_EMBEDDERS), default="minilm",
                         help="text encoder: minilm (default, 384-d) or "
                              "embeddinggemma-2 (768-d truncated to 256, "
                              "needs sentence-transformers>=6.1; refuses "
                              "otherwise and builds lexical-only)")
    p_build.add_argument("--frames", action="store_true",
                         help="Also sample the clips to stills and embed them with CLIP "
                              "(objects/people/scenes; writes beside the text index)")
    p_build.add_argument("--video-chunks", action="store_true",
                         help="Also embed one 8s video vector per media window "
                              "with EmbeddingGemma 2 (needs the model; clips "
                              "without media are named, never silently dropped)")
    p_build.add_argument("--unified-anchors", action="store_true",
                         help="Also build the five linked per-window anchors "
                              "(video, still, audio, description, transcript) "
                              "in the one EG2 space (opt-in; needs the model)")

    p_search = sub.add_parser("search", help="Semantic + keyword search")
    p_search.add_argument("project")
    p_search.add_argument("query", nargs="?",
                          help="what to look for (omit with --predicate)")
    p_search.add_argument("-k", "--top-k", type=int, default=5)
    p_search.add_argument("--visual", action="store_true",
                          help="Search the sampled FRAMES with CLIP instead of the text "
                               "index: top time ranges per clip, every hit unverified, "
                               "absence never concluded")
    p_search.add_argument("--chunks", action="store_true",
                          help="Search the 8s VIDEO CHUNKS with EmbeddingGemma 2 "
                               "instead of the text index: top windows per clip, "
                               "with the recording encoder's floor and abstain")
    p_search.add_argument("--unified", action="store_true",
                          help="Search the five linked UNIFIED ANCHORS with "
                               "EmbeddingGemma 2 instead of the text index: "
                               "top windows per clip with per-anchor scores, "
                               "fused with keyword and OCR (opt-in backend)")
    p_search.add_argument("--sound", action="store_true",
                          help="Search the PANNs SOUND-EVENT labels (the M5 sound lane) "
                               "instead of text: 'ren search --sound laughter' returns "
                               "the spans where laughter, applause, music or impacts "
                               "was heard")
    p_search.add_argument("--ocr", action="store_true",
                          help="Search the ON-SCREEN TEXT read by step 1.07 (OCR "
                               "extraction) instead of speech: 'ren search --ocr "
                               "\"SCUFFLEWA\"' returns the spans where that text "
                               "appeared. The step is deselected by default, so this "
                               "answers only for a project whose run included it")
    p_search.add_argument("--include-actions", action="store_true",
                          help="Rank even an action/gesture query against the frames "
                               "(the frame index refuses those by default: it cannot "
                               "resolve them)")
    p_search.add_argument("--mode", choices=["hybrid", "dense", "lexical"], default="hybrid")
    p_search.add_argument("--signal-merge", action="store_true",
                          help="opt in to direct shot-facet and precise OCR-span "
                               "signals (experimental; not the default)")
    p_search.add_argument("--floor", type=float, default=None,
                           help="minimum dense cosine to count as evidence "
                                "(default: the built index's backend floor; "
                                "0 disables the abstain)")
    p_search.add_argument("--person",
                          help="restrict to one person's measured face/voice "
                              "spans (person_id or declared name, e.g. "
                              "'Craig') - the entity store (M3b)")
    p_search.add_argument("--predicate",
                          help="STRUCTURED query instead of text: what the "
                               "--person is doing (speaking, on_screen), "
                               "answered from the per-source event spans "
                               "(M7), joined across angles on the M6 clock "
                               "and placed on the timeline")
    p_search.add_argument("--verify", metavar="STATEMENT",
                          help="with --predicate: have the local VLM judge "
                               "each CANDIDATE span against this statement "
                               "about the person (e.g. \"covers his mouth "
                               "with his hand\") and return only the spans "
                               "it verifies, each with its reason")
    p_search.add_argument("--max-tier", type=int, choices=[0, 1], default=None,
                          help="with --verify: maximum candidate tier sent to "
                               "the VLM (hand_near_mouth defaults to 0; use "
                               "1 to include marginal candidates)")
    p_search.add_argument("--json", action="store_true",
                          help="print the whole answer as JSON: one hit "
                               "list on every mode (text, --sound, --ocr, "
                               "--visual, --chunks, --unified), each hit a "
                               "{source_digest, clip_id, file, start_s, "
                               "end_s, kind, score, dense, band, text} row, "
                               "with abstained and the floor when nothing "
                               "cleared it")
    p_search.add_argument("--external-hits", metavar="JSON",
                           help="merge Resolve IntelliSearch rows from this JSON "
                                "list file as EXTRA evidence beside Ren's "
                                "answer (what the Resolve-side collector "
                                "wrote); never replaces Ren search")
    p_search.add_argument("--index-dir")

    p_where = sub.add_parser("where", help="Where is this person in the footage")
    p_where.add_argument("project")
    p_where.add_argument("person",
                         help="roster person id or declared name, e.g. "
                              "'person_001' or 'Craig'")
    p_where.add_argument("--at", type=float, default=None,
                         help="source seconds: the region at that moment, "
                              "per source (omit to list the person's region "
                              "tracks)")
    p_where.add_argument("--json", action="store_true",
                         help="print the whole answer as JSON")
    p_where.add_argument("--memory-root",
                         help="footage-memory root (default: the shared store)")

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
    p_filter.add_argument("--max-face-presence", type=float)
    p_filter.add_argument("--max-motion", type=float)
    p_filter.add_argument("--min-motion", type=float)
    p_filter.add_argument("--min-brightness", type=float)
    p_filter.add_argument("--max-brightness", type=float)
    p_filter.add_argument("--min-sharpness", type=float,
                          help="minimum VI-03 mean Laplacian variance")
    p_filter.add_argument("--max-temporal-noise", type=float,
                          help="maximum VI-03 temporal residual (0..1)")
    p_filter.add_argument("--max-blockiness", type=float,
                          help="maximum VI-03 8px blockiness proxy (0..1)")
    p_filter.add_argument("--max-underexposed-fraction", type=float,
                          help="maximum VI-03 mean fraction of pixels at luma <=4 (0..1)")
    p_filter.add_argument("--max-overexposed-fraction", type=float,
                          help="maximum VI-03 mean fraction of pixels at luma >=251 (0..1)")
    p_filter.add_argument("--max-camera-translation", type=float,
                          help="maximum mean camera shift in normalized proxy-frame displacements")
    p_filter.add_argument("--min-subject-energy", type=float,
                          help="minimum mean non-camera residual flow (same units)")
    p_filter.add_argument("--max-subject-energy", type=float,
                          help="maximum mean non-camera residual flow (same units)")
    p_filter.add_argument("--min-subject-center-x", type=float)
    p_filter.add_argument("--max-subject-center-x", type=float)
    p_filter.add_argument("--min-subject-width-fraction", type=float)
    p_filter.add_argument("--contains")
    p_filter.add_argument("--person",
                          help="restrict to one person's measured face/voice "
                              "spans (person_id or declared name)")
    p_filter.add_argument("--min-speech-level", type=float,
                          help="minimum absolute speech level in dBFS (Gap 12)")
    p_filter.add_argument("--max-speech-level", type=float,
                          help="maximum absolute speech level in dBFS (Gap 12)")
    p_filter.add_argument("--music-genre",
                          help="music genre label (Gap 12), e.g. rock, jazz")
    p_filter.add_argument("--ambience-type",
                          help="ambience type (Gap 12): indoor, outdoor, urban, nature")
    p_filter.add_argument("--index-dir")

    p_hybrid = sub.add_parser("hybrid", help="Search restricted by filters")
    p_hybrid.add_argument("project")
    p_hybrid.add_argument("query")
    p_hybrid.add_argument("-k", "--top-k", type=int, default=5)
    p_hybrid.add_argument("--mode", choices=["hybrid", "dense", "lexical"], default="hybrid")
    p_hybrid.add_argument("--signal-merge", action="store_true",
                          help="opt in to direct shot-facet and precise OCR-span "
                               "signals (experimental; not the default)")
    p_hybrid.add_argument("--floor", type=float, default=None,
                           help="minimum dense cosine to count as evidence "
                                "(default: the built index's backend floor; "
                                "0 disables the abstain)")
    p_hybrid.add_argument("--kind", choices=list(SEGMENT_KINDS))
    p_hybrid.add_argument("--clip-id")
    p_hybrid.add_argument("--framing")
    p_hybrid.add_argument("--min-face-presence", type=float)
    p_hybrid.add_argument("--max-face-presence", type=float)
    p_hybrid.add_argument("--max-motion", type=float)
    p_hybrid.add_argument("--min-motion", type=float)
    p_hybrid.add_argument("--min-brightness", type=float)
    p_hybrid.add_argument("--max-brightness", type=float)
    p_hybrid.add_argument("--min-sharpness", type=float)
    p_hybrid.add_argument("--max-temporal-noise", type=float)
    p_hybrid.add_argument("--max-blockiness", type=float)
    p_hybrid.add_argument("--max-underexposed-fraction", type=float)
    p_hybrid.add_argument("--max-overexposed-fraction", type=float)
    p_hybrid.add_argument("--max-camera-translation", type=float)
    p_hybrid.add_argument("--min-subject-energy", type=float)
    p_hybrid.add_argument("--max-subject-energy", type=float)
    p_hybrid.add_argument("--min-subject-center-x", type=float)
    p_hybrid.add_argument("--max-subject-center-x", type=float)
    p_hybrid.add_argument("--min-subject-width-fraction", type=float)
    p_hybrid.add_argument("--person",
                          help="restrict to one person's measured face/voice "
                               "spans (person_id or declared name)")
    p_hybrid.add_argument("--external-hits", metavar="JSON",
                           help="merge Resolve IntelliSearch rows from this JSON "
                                "list file as EXTRA evidence beside Ren's "
                                "answer; never replaces Ren search")
    p_hybrid.add_argument("--json", action="store_true",
                          help="print the whole answer as JSON: one hit "
                               "list, each hit a {source_digest, clip_id, "
                               "file, start_s, end_s, kind, score, dense, "
                               "band, text} row, with abstained and the "
                               "floor when nothing cleared it")
    p_hybrid.add_argument("--index-dir")

    p_detail = sub.add_parser("detail", help="Full record for one segment")
    p_detail.add_argument("project")
    p_detail.add_argument("segment_id")
    p_detail.add_argument("--index-dir")

    p_transcript = sub.add_parser("transcript", help="Every utterance of one clip")
    p_transcript.add_argument("project")
    p_transcript.add_argument("clip_id")
    p_transcript.add_argument("--resolve-transcript", metavar="JSON",
                              help="merge Resolve transcription rows from "
                                   "this JSON list file as EXTRA evidence "
                                   "beside Ren's utterances, disagreements "
                                   "as findings; never replaces them")
    p_transcript.add_argument("--resolve-fps", default=None,
                              help="frames per second (number or num/den) "
                                   "for Resolve HH:MM:SS:FF timecodes; "
                                   "needed with --resolve-start-tc")
    p_transcript.add_argument("--resolve-start-tc", default=None,
                              help="the clip's opening timecode (or "
                                   "seconds) that Resolve timecodes count "
                                   "from")
    p_transcript.add_argument("--index-dir")

    p_summary = sub.add_parser("summary", help="What is in the index")
    p_summary.add_argument("project")
    p_summary.add_argument("--index-dir")

    p_stale = sub.add_parser("stale", help="Has the ingest moved since the index was built?")
    p_stale.add_argument("project")
    p_stale.add_argument("--index-dir")

    p_pg = sub.add_parser("project-graph", help="Query the Ren Project Graph")
    p_pg.add_argument("project")
    p_pg.add_argument("query", choices=["reaction_shots", "alternate_takes",
                                          "coverage", "broll_matches",
                                          "shot_colour", "reel_versions",
                                          "editorial_findings",
                                          "vfx_detections",
                                          "variant_branches", "audio_events",
                                          "commits", "project_events",
                                          "reaction_decisions",
                                          "participants", "proposals",
                                          "cross_project_assets"])
    p_pg.add_argument("--person", help="person id for reaction_shots")
    p_pg.add_argument("--t", type=float, help="time for reaction_shots "
                                              "and reaction_decisions")
    p_pg.add_argument("--clip", help="clip id for alternate_takes, shot_colour, "
                                     "editorial_findings, vfx_detections, "
                                     "audio_events and reaction_decisions")
    p_pg.add_argument("--timeline-start", type=float, help="for coverage")
    p_pg.add_argument("--timeline-end", type=float, help="for coverage")
    p_pg.add_argument("--segment", help="transcript segment id for broll_matches")
    p_pg.add_argument("--final", help="reel id for reel_versions")
    p_pg.add_argument("--exploration",
                      help="exploration id for variant_branches")
    p_pg.add_argument("--timeline-name",
                      help="timeline for variant_branches and commits")
    p_pg.add_argument("--kind", help="finding kind for editorial_findings: "
                                     "dead_section, dead_section_trim, "
                                     "jl_placement, jl_decision, "
                                     "reaction_cutaway")
    p_pg.add_argument("--label", help="audio event label for audio_events, "
                                      "e.g. music - matched case-insensitively")
    p_pg.add_argument("--task", help="worker task for project_events")
    p_pg.add_argument("--event-type", help="event type for project_events")
    p_pg.add_argument("--level", help="authority level for participants, "
                                      "e.g. approve - who holds at least this")
    p_pg.add_argument("--projects", nargs="+", help="candidate project roots for cross_project_assets")
    p_pg.add_argument("--search-text", default="", help="asset terms for cross_project_assets")
    p_pg.add_argument("--category", dest="categories", action="append",
                      choices=["people", "scenes", "events", "usage", "lineage"],
                      help="result facets; repeatable")
    p_pg.add_argument("--index-dir")

    sub.add_parser("tools", help="Print LLM tool definitions as JSON")

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0

    if args.command == "tools":
        from library.tools.analysis import footage_frames
        print(json.dumps(FootageIndex.get_tool_definitions()
                         + footage_frames.FrameIndex.get_tool_definitions(),
                         indent=2))
        return 0

    if args.command == "build":
        stats = build_index(args.project, index_dir=args.index_dir, kinds=tuple(args.kinds),
                            embedder=args.embedder,
                            video_chunks=args.video_chunks,
                            unified_anchors=args.unified_anchors)
        print(json.dumps(stats, indent=2))
        if args.frames:
            from library.tools.analysis import footage_frames
            frame_stats = footage_frames.build_frame_index(
                args.project, index_dir=args.index_dir)
            print(json.dumps(frame_stats, indent=2))
        return 0

    if args.command == "search" and args.predicate:
        if not args.person or args.query:
            raise RenRefusal(
                "--predicate needs --person and no text query",
                "a structured query asks what one person is doing; it has "
                "no text to rank",
                f"ren search {args.project} --person <name> --predicate "
                f"{args.predicate}")
        from library.tools import event_spans
        started = time.perf_counter()
        if args.verify is not None:
            answer = event_spans.verified_query(args.project, args.person,
                                                args.predicate, args.verify,
                                                max_tier=args.max_tier)
        else:
            answer = event_spans.query(args.project, args.person, args.predicate)
        elapsed = time.perf_counter() - started
        if args.json:
            print(json.dumps(answer, indent=2))
        else:
            _print_structured(answer)
            print(f"\n{elapsed * 1000:.1f} ms")
        return 0

    if args.command == "search" and args.sound:
        if not args.query:
            raise RenRefusal(
                "--sound needs a label to look for",
                "a sound query asks what was HEARD; it has no text to rank",
                f"ren search {args.project} --sound \"<label>\" "
                f"(e.g. --sound laughter)")
        idx = FootageIndex(args.project, index_dir=args.index_dir)
        started = time.perf_counter()
        if args.json:
            answer = search_json(args.project, args.query, top_k=args.top_k,
                                 index_dir=args.index_dir, lane="sound")
        else:
            answer = idx.sound_search(args.query, top_k=args.top_k)
        elapsed = time.perf_counter() - started
        if args.json:
            print(json.dumps(answer, indent=2))
        else:
            _print_sound_report(answer)
            print(f"\n{elapsed * 1000:.1f} ms")
        return 0

    if args.command == "project-graph":
        idx = FootageIndex(args.project, index_dir=args.index_dir)
        answer = idx.project_graph(
            args.query,
            person_id=args.person,
            around_t=args.t,
            at_time=args.t,
            clip_id=args.clip,
            timeline_start=args.timeline_start,
            timeline_end=args.timeline_end,
            segment_id=args.segment,
            final=args.final,
            kind=args.kind,
            exploration=args.exploration,
            timeline=args.timeline_name,
            label=args.label,
            task=args.task,
            event_type=args.event_type,
            level=args.level,
            **({"projects": args.projects,
                "search_text": args.search_text,
                "categories": args.categories}
               if args.query == "cross_project_assets" else {}),
        )
        print(json.dumps(answer, indent=2))
        return 0

    if args.command == "search" and args.ocr:
        if not args.query:
            raise RenRefusal(
                "--ocr needs a string to look for",
                "an OCR query asks what was ON SCREEN; it has no text to rank",
                f"ren search {args.project} --ocr \"<on-screen text>\" "
                f"(e.g. --ocr \"SCUFFLEWA BREWING CO\")")
        idx = FootageIndex(args.project, index_dir=args.index_dir)
        started = time.perf_counter()
        if args.json:
            answer = search_json(args.project, args.query, top_k=args.top_k,
                                 index_dir=args.index_dir, lane="ocr")
        else:
            answer = idx.ocr_search(args.query, top_k=args.top_k)
        elapsed = time.perf_counter() - started
        if args.json:
            print(json.dumps(answer, indent=2))
        else:
            _print_ocr_report(answer)
            print(f"\n{elapsed * 1000:.1f} ms")
        return 0

    if args.command == "search" and args.verify is not None:
        raise RenRefusal("--verify needs --person and --predicate",
                         "the VLM judges a person's candidate spans, never "
                         "a whole episode",
                         f"ren search {args.project} --person <name> "
                         f"--predicate hand_near_mouth --verify \"<statement>\"")

    if args.command == "search" and not args.query:
        raise RenRefusal("search needs a query",
                         "nothing to look for was given",
                         f"ren search {args.project} \"<what>\", or "
                         f"--person <name> --predicate <what they do>")

    if args.command == "search" and args.visual:
        started = time.perf_counter()
        if args.json:
            answer = search_json(args.project, args.query, top_k=args.top_k,
                                 include_actions=args.include_actions,
                                 index_dir=args.index_dir, lane="visual")
            print(json.dumps(answer, indent=2))
            return 0
        from library.tools.analysis import footage_frames
        vidx = footage_frames.FrameIndex(args.project, index_dir=args.index_dir)
        report = vidx.search_ranges(args.query, top_ranges=args.top_k,
                                    include_actions=args.include_actions)
        elapsed = time.perf_counter() - started
        if report["refused"] is not None:
            raise RenRefusal(
                f"frame search refused {report['query']!r}",
                report["refused"]["reason"],
                report["refused"]["hint"])
        _print_frame_report(report)
        print(f"\n{elapsed * 1000:.1f} ms")
        return 0

    if args.command == "where":
        started = time.perf_counter()
        answer = where_answer(args.project, args.person, at=args.at,
                              memory_root=args.memory_root)
        elapsed = time.perf_counter() - started
        if args.json:
            print(json.dumps(answer, indent=2))
        else:
            _print_where(answer)
            print(f"\n{elapsed * 1000:.1f} ms")
        return 0

    idx = FootageIndex(args.project, index_dir=args.index_dir)

    if args.command == "search" and args.unified:
        started = time.perf_counter()
        if args.json:
            answer = search_json(args.project, args.query, top_k=args.top_k,
                                 mode=args.mode, floor=args.floor,
                                 index_dir=args.index_dir, lane="unified")
            print(json.dumps(answer, indent=2))
            return 0
        from library.tools.analysis.unified_anchors import UnifiedIndex
        uidx = UnifiedIndex(args.project, index_dir=args.index_dir)
        report = uidx.search_unified(args.query, top_k=args.top_k,
                                     mode=args.mode, floor=args.floor)
        elapsed = time.perf_counter() - started
        _print_unified_report(report)
        print(f"\n{elapsed * 1000:.1f} ms")
        return 0

    if args.command == "search" and args.chunks:
        started = time.perf_counter()
        if args.json:
            answer = search_json(args.project, args.query, top_k=args.top_k,
                                 floor=args.floor,
                                 index_dir=args.index_dir, lane="chunks")
            print(json.dumps(answer, indent=2))
            return 0
        report = idx.search_chunks(args.query, top_k=args.top_k,
                                   floor=args.floor)
        elapsed = time.perf_counter() - started
        _print_chunk_report(report)
        print(f"\n{elapsed * 1000:.1f} ms")
        return 0

    if args.command == "search":
        started = time.perf_counter()
        if args.json:
            answer = search_json(
                args.project, args.query, mode=args.mode, top_k=args.top_k,
                floor=args.floor, signal_merge=args.signal_merge,
                person=args.person, index_dir=args.index_dir, lane="text",
                external_hits=_load_external_hits(args.external_hits)
                if args.external_hits else None)
            print(json.dumps(answer, indent=2))
            return 0
        report = idx.search_report(args.query, top_k=args.top_k, mode=args.mode,
                                   floor=args.floor,
                                   signal_merge=args.signal_merge,
                                   filters={"person": args.person},
                                   external_hits=_load_external_hits(args.external_hits)
                                   if args.external_hits else None)
        elapsed = time.perf_counter() - started
        _print_report(report)
        print(f"\n{elapsed * 1000:.1f} ms")
        return 0

    elif args.command == "filter":
        results = idx.filter(
            kind=args.kind, clip_id=args.clip_id, framing=args.framing,
            camera_mode=args.camera_mode, stability=args.stability,
            scene_type=args.scene_type, min_duration=args.min_duration,
            max_duration=args.max_duration, min_face_presence=args.min_face_presence,
            max_face_presence=args.max_face_presence,
            max_motion=args.max_motion, contains=args.contains,
            min_motion=args.min_motion,
            min_brightness=args.min_brightness,
            max_brightness=args.max_brightness,
            min_sharpness=args.min_sharpness,
            max_temporal_noise=args.max_temporal_noise,
            max_blockiness=args.max_blockiness,
            max_underexposed_fraction=args.max_underexposed_fraction,
            max_overexposed_fraction=args.max_overexposed_fraction,
            max_camera_translation=args.max_camera_translation,
            min_subject_energy=args.min_subject_energy,
            max_subject_energy=args.max_subject_energy,
            min_subject_center_x=args.min_subject_center_x,
            max_subject_center_x=args.max_subject_center_x,
            min_subject_width_fraction=args.min_subject_width_fraction,
            person=args.person,
            min_speech_level=args.min_speech_level,
            max_speech_level=args.max_speech_level,
            music_genre=args.music_genre,
            ambience_type=args.ambience_type,
        )
        print(f"{len(results)} matching segments:")
        for hit in results:
            print(f"  {hit['segment_id']:28s} {hit['timecode']:>22s} "
                  f"{hit['duration']:6.2f}s  {hit['text'][:70]}")

    elif args.command == "hybrid":
        started = time.perf_counter()
        if args.json:
            answer = search_json(
                args.project, args.query, mode=args.mode, top_k=args.top_k,
                floor=args.floor, signal_merge=args.signal_merge,
                index_dir=args.index_dir, lane="text",
                filters={"kind": args.kind, "clip_id": args.clip_id,
                         "framing": args.framing,
                         "min_face_presence": args.min_face_presence,
                         "max_face_presence": args.max_face_presence,
                         "max_motion": args.max_motion,
                         "min_motion": args.min_motion,
                         "min_brightness": args.min_brightness,
                         "max_brightness": args.max_brightness,
                         "min_sharpness": args.min_sharpness,
                         "max_temporal_noise": args.max_temporal_noise,
                         "max_blockiness": args.max_blockiness,
                         "max_underexposed_fraction": args.max_underexposed_fraction,
                         "max_overexposed_fraction": args.max_overexposed_fraction,
                         "max_camera_translation": args.max_camera_translation,
                         "min_subject_energy": args.min_subject_energy,
                         "max_subject_energy": args.max_subject_energy,
                         "min_subject_center_x": args.min_subject_center_x,
                         "max_subject_center_x": args.max_subject_center_x,
                         "min_subject_width_fraction": args.min_subject_width_fraction,
                         "person": args.person},
                external_hits=_load_external_hits(args.external_hits)
                if args.external_hits else None)
            print(json.dumps(answer, indent=2))
            return 0
        report = idx.search_report(
            args.query, top_k=args.top_k, mode=args.mode, floor=args.floor,
            signal_merge=args.signal_merge,
            filters={"kind": args.kind, "clip_id": args.clip_id,
                     "framing": args.framing,
                     "min_face_presence": args.min_face_presence,
                     "max_face_presence": args.max_face_presence,
                     "max_motion": args.max_motion,
                     "min_motion": args.min_motion,
                     "min_brightness": args.min_brightness,
                     "max_brightness": args.max_brightness,
                     "min_sharpness": args.min_sharpness,
                     "max_temporal_noise": args.max_temporal_noise,
                     "max_blockiness": args.max_blockiness,
                     "max_underexposed_fraction": args.max_underexposed_fraction,
                     "max_overexposed_fraction": args.max_overexposed_fraction,
                     "max_camera_translation": args.max_camera_translation,
                     "min_subject_energy": args.min_subject_energy,
                     "max_subject_energy": args.max_subject_energy,
                     "min_subject_center_x": args.min_subject_center_x,
                     "max_subject_center_x": args.max_subject_center_x,
                     "min_subject_width_fraction": args.min_subject_width_fraction,
                     "person": args.person},
            external_hits=_load_external_hits(args.external_hits)
            if args.external_hits else None,
        )
        elapsed = time.perf_counter() - started
        _print_report(report)
        print(f"\n{elapsed * 1000:.1f} ms")

    elif args.command == "detail":
        print(json.dumps(idx.get_detail(args.segment_id), indent=2))

    elif args.command == "transcript":
        if args.resolve_transcript:
            report = idx.transcript_report(
                args.clip_id,
                _load_external_hits(args.resolve_transcript),
                resolve_fps=args.resolve_fps,
                resolve_start_tc=args.resolve_start_tc)
            for line in report["utterances"]:
                print(f"  {line['timecode']}  {line['text']}")
            _print_transcript_evidence(report)
        else:
            for line in idx.transcript(args.clip_id):
                print(f"  {line['timecode']}  {line['text']}")

    elif args.command == "summary":
        print(json.dumps(idx.summary(), indent=2))

    elif args.command == "stale":
        print(json.dumps(idx.staleness(), indent=2))

    return 0


if __name__ == "__main__":
    # ONE place renders every refusal on `ren search` / `ren
    # search-index`'s path, in the same shape as the front door.
    # With `REN_OUTPUT=json` both a refusal and an uncaught error end
    # with one final `REN_RESULT` line (LE-03).
    try:
        sys.exit(main())
    except RenRefusal as refused:
        from library.tools.ren_refusal import emit_refusal_result
        print(refused.render(), file=sys.stderr)
        emit_refusal_result(refused)
        sys.exit(REFUSAL_EXIT_CODE)
    except Exception as exc:  # noqa: BLE001 - unexpected errors are data too
        from library.tools.ren_refusal import report_unexpected
        sys.exit(report_unexpected(exc))
