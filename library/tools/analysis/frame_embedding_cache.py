"""C01: per-frame CLIP embedding reuse across index (re)builds.

Source frames are cached well (M2), but rebuilding a frame index
re-encoded every collected frame: ``build_frame_index`` called the
CLIP image encoder over the whole selected set on every build, so
adding one clip re-embedded every unchanged clip with it.

The embeddings are reusable source OBSERVATIONS - the same pixels
through the same model and preprocessing always give the same vector,
whatever project asked - so they are filed per source, beside the M2
sample they were derived from, and read back on the next build:

* the store is the source's own memory directory
  (``source_memory.source_dir(digest)``), not a new database: the
  cache lives with the sample it was derived from, so it shares
  exactly where the sample is shared (the legacy machine root) and
  stays private exactly where the sample is private (a project
  scope).  Cross-project reuse falls out of the addressing: same
  digest, same key, no re-encode.
* the key is the C05 vocabulary (``library.tools.cache_keys``): the
  source content digest, the model with its weights revision, the
  image preprocessing recipe, the embedding configuration, and this
  lane's own version.  A moved component is a MISS, never a false
  hit, so a model or preprocessing change re-encodes rather than
  serving another configuration's vectors.
* each cached row is keyed by the frame's own identity - its
  timestamp, its source frame index, and the content hash of the
  decoded file - so a frame whose bytes changed (an M2 rebuild with
  different pixels) misses even though its timestamp did not move,
  and a frame that merely moved projects hits.
* the wanted key is computable with NO model in the process: the
  preprocessing recipe is measured once and pinned (see
  ``PREPROCESSING_RECIPE``), the weights revision is read off the
  model cache's ``refs/main``, and the transformers version is read
  off the installed distribution metadata.  A full-hit rebuild
  therefore imports no torch, takes no lock, and encodes nothing.
  The pin is kept honest on the encode path: after the processor
  loads, the live recipe is compared against the pinned one and a
  drift REFUSES rather than filing vectors under a key that lies
  about them.

Only new or invalidated frames reach the encoder; the project matrix
is reassembled in selection order from cached plus fresh vectors, so
a reused build is row-for-row what a full rebuild would have
written.  ``docs/evidence/mm-c01-embed-reuse.md`` carries the
before/after measurement (frames embedded, wall seconds, held-out
recall).
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import tempfile
import time
from pathlib import Path

import numpy as np

from library.tools import cache_keys
from library.tools import source_memory

#: What answers: the same model the frame index reads.
MODEL_ID = "openai/clip-vit-large-patch14"

#: This lane's own version, the ``analysis_version`` half of every
#: key.  Bump it when the encode method changes (different features,
#: different normalisation): a changed key is a MISS, so the old
#: rows are re-encoded rather than served as current.
CACHE_VERSION = "clip-frame-embeddings-v1"

#: The file beside the M2 sample that holds one source's cached
#: vectors.  One generation per source: a key change files a new
#: generation over the old one.
CACHE_FILENAME = "clip_frame_embeddings.npz"

#: How the pixels become a vector.  The encoder reads
#: ``pooler_output`` and L2-normalises it (see ``_pooled_clip_features``
#: in ``footage_frames``); batching changes nothing per image, which
#: is what makes a cached row bitwise what a rebuild would encode.
EMBEDDING_METHOD = {"features": "pooler_output", "normalisation": "l2"}

#: The image preprocessing the encoder is asked with, MEASURED
#: 2026-10-22 off the live ``CLIPProcessor`` for ``MODEL_ID``
#: (``CLIPImageProcessor``, resize shortest edge 224, center crop 224,
#: rescale 1/255, CLIP mean/std, bicubic, RGB).  Pinned so the wanted
#: key needs no model in the process; verified against the live
#: processor on every encode, so a drift refuses rather than serving
#: another recipe's vectors under this one.
PREPROCESSING_RECIPE = {
    "processor": "CLIPImageProcessor",
    "size": {"shortest_edge": 224},
    "crop": {"center": [224, 224]},
    "rescale_factor": 1 / 255,
    "mean": [0.48145466, 0.4578275, 0.40821073],
    "std": [0.26862954, 0.26130258, 0.27577711],
    "resample": "bicubic",
    "convert_rgb": True,
}


def model_revision(model_id: str = MODEL_ID) -> str | None:
    """The weights the encoder would load, without loading them.

    Read off the local model cache's ``refs/main`` - the commit
    ``from_pretrained(..., local_files_only=True)`` resolves to - so
    a weights update moves the key even though no code changed.  A
    cache with no ref (or several snapshots and no ref) resolves as
    ``None`` and keys as ``"unresolved"``: the revision is then
    carried only by ``CACHE_VERSION``, and a silent weights swap is
    still a miss the moment the lane version moves.
    """
    try:
        try:
            from huggingface_hub import constants as hub_constants

            cache = Path(hub_constants.HF_HUB_CACHE)
        except Exception:  # noqa: BLE001 - hub layout unknown
            cache = Path(os.path.expanduser("~/.cache/huggingface/hub"))
        repo = cache / ("models--" + model_id.replace("/", "--"))
        ref = repo / "refs" / "main"
        if ref.is_file():
            commit = ref.read_text(encoding="utf-8").strip()
            if commit:
                return commit
        snapshots = sorted((repo / "snapshots").glob("*")) if (
            repo / "snapshots").is_dir() else []
        if len(snapshots) == 1:
            return snapshots[0].name
    except OSError:
        pass
    return None


def transformers_version() -> str:
    """The installed transformers version, without importing it.

    Importing transformers imports torch with it - the very weight a
    full-hit rebuild must not lift - so the version is read off the
    distribution metadata.  It is folded into the key because a
    transformers upgrade may change what the processor does with the
    same recipe: folding it in turns that drift into a MISS rather
    than a false hit.
    """
    try:
        return importlib.metadata.version("transformers")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def live_preprocessing_recipe(processor) -> dict:
    """The recipe a loaded processor actually runs, in pin shape.

    The encode path measures this off the processor it is about to
    encode with and ``assert_live_recipe`` compares it to the pin:
    the pin is what the key is built from, so the two must agree or
    the key is a lie about the vectors filed under it.
    """
    inner = getattr(processor, "image_processor", processor)
    size = getattr(inner, "size", None)
    if isinstance(size, dict):
        shortest = size.get("shortest_edge", size.get("height"))
    else:
        shortest = getattr(size, "shortest_edge", size)
    crop = getattr(inner, "crop_size", None)
    if isinstance(crop, dict):
        center = [crop.get("height"), crop.get("width")]
    else:
        center = [getattr(crop, "height", None),
                  getattr(crop, "width", None)]
    resample = getattr(inner, "resample", None)
    return {
        "processor": type(inner).__name__.replace("Pil", ""),
        "size": {"shortest_edge": shortest},
        "crop": {"center": center},
        "rescale_factor": float(getattr(inner, "rescale_factor", 0) or 0),
        "mean": [float(v) for v in (getattr(inner, "image_mean", None)
                                    or [])],
        "std": [float(v) for v in (getattr(inner, "image_std", None)
                                   or [])],
        "resample": ({2: "bilinear", 3: "bicubic"}.get(int(resample), resample)
                     if resample is not None else None),
        "convert_rgb": bool(getattr(inner, "do_convert_rgb", True)),
    }


def assert_live_recipe(processor) -> dict:
    """The live recipe matches the pin, or the build refuses.

    Called on the encode path only - the hit path never loads the
    processor - so a drift fails the build that would have filed bad
    rows rather than every build that only reads.  Returns the live
    recipe for the provenance record.
    """
    from library.tools.ren_refusal import RenRefusal

    live = live_preprocessing_recipe(
        getattr(processor, "image_processor", processor))
    if live != PREPROCESSING_RECIPE:
        raise RenRefusal(
            "the CLIP preprocessing recipe moved under the pinned key",
            "cached frame embeddings are keyed by the recipe they were "
            "encoded with; encoding with a different one would serve "
            "another recipe's vectors as current",
            f"live {live!r} != pinned {PREPROCESSING_RECIPE!r}: bump "
            f"CACHE_VERSION in library/tools/analysis/"
            f"frame_embedding_cache.py and rebuild",
            blocker="unsafe_state")
    return live


def configuration_payload(*, revision: str | None = None,
                          transformers: str | None = None) -> dict:
    """The C05 configuration: HOW the embedding was asked.

    No asking-context travels in it (no transcript, no prompt, no
    window): a frame embedding is true of the source whatever project
    asked, so it keys as an observation and ``observation_key``
    refuses the payload if context ever smuggles in.
    """
    return {
        **cache_keys.preprocessing_component(**PREPROCESSING_RECIPE),
        "embedding": dict(EMBEDDING_METHOD),
        "weights_revision": revision or model_revision() or "unresolved",
        "transformers": transformers or transformers_version(),
    }


def wanted_key(source_digest: str, *, revision: str | None = None,
               transformers: str | None = None) -> cache_keys.CacheKey:
    """The key a fresh embedding of this source is filed under."""
    resolved = revision or model_revision() or "unresolved"
    return cache_keys.observation_key(
        source_identity=str(source_digest),
        model=f"{MODEL_ID}@snapshot:{resolved}",
        configuration=configuration_payload(revision=resolved,
                                            transformers=transformers),
        analysis_version=CACHE_VERSION)


def content_sha(path: str | os.PathLike) -> str:
    """The content hash of one decoded frame file."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def identity_slot(t: float, frame_index) -> tuple:
    """The slot an identity belongs to: timestamp and source frame, no bytes.

    The same formatting `frame_identity` files, factored so the prune
    can compare a filed row against the current sample without
    re-hashing every decoded file.
    """
    try:
        stamp = f"{float(t):.3f}"
    except (TypeError, ValueError):
        stamp = "?"
    return (stamp, "-" if frame_index is None else str(frame_index))


def frame_identity(t: float, frame_index, sha: str) -> str:
    """One decoded frame's stable identity.

    The timestamp and the source's own frame index say WHICH frame it
    is; the content hash says WHICH PIXELS it holds.  All three travel
    in the identity so a frame whose bytes changed (an M2 rebuild with
    different pixels at the same second) misses even though its
    timestamp did not move.
    """
    stamp, frame = identity_slot(t, frame_index)
    return f"t={stamp} frame={frame} sha={sha}"


def identity_pair(identity: str) -> tuple:
    """The slot a filed identity belongs to, parsed back out."""
    stamp, frame = "?", "-"
    for part in str(identity).split(" "):
        key, _, value = part.partition("=")
        if key == "t":
            stamp = value
        elif key == "frame":
            frame = value
    return (stamp, frame)


def prune_rows(rows: dict, m2_frames: list, fresh=()) -> dict:
    """Drop the rows no current frame answers for.

    A row survives when its slot (timestamp, source frame) is still in
    the source's current M2 sample.  Two stalenesses go: rows for
    frames the sample no longer holds (a shrunk or re-cut sample),
    and rows a fresh encode superseded (same slot, different bytes -
    the old pixels are gone, so the old identity can never be
    selected again).  Fresh rows always survive, so a missing sample
    degrades to keeping the fresh encode, never to filing nothing.
    Pure: no I/O, no model.
    """
    slots = {identity_slot(frame.get("t"), frame.get("frame_index"))
             for frame in m2_frames if isinstance(frame, dict)}
    fresh_set = set(fresh)
    fresh_slots = {identity_pair(identity) for identity in fresh_set}
    pruned = {}
    for identity, vector in rows.items():
        if identity in fresh_set:
            pruned[identity] = vector
            continue
        pair = identity_pair(identity)
        if pair not in slots:
            continue
        if pair in fresh_slots:
            continue
        pruned[identity] = vector
    return pruned


def cache_path(root, digest: str) -> Path:
    """The cache file beside the source's own M2 sample."""
    return source_memory.source_dir(digest, root) / CACHE_FILENAME


def load(root, digest: str, wanted: cache_keys.CacheKey) -> dict:
    """The usable cached rows for one source, or why there are none.

    Never raises on a bad file: a missing, corrupt, or stale-keyed
    cache is a MISS (re-encode), never a failed build.  A corrupt
    file is left for the next successful store to replace.
    """
    path = cache_path(root, digest)
    try:
        raw = np.load(path, allow_pickle=False)
        identities = [str(v) for v in raw["identities"]]
        matrix = np.asarray(raw["embeddings"], dtype="float32")
        provenance = json.loads(str(raw["__provenance__"]))
    except (OSError, ValueError, KeyError, StopIteration) as exc:
        if path.exists():
            return {"status": "corrupt", "reason": f"{type(exc).__name__}: {exc}",
                    "rows": {}, "matrix": None, "provenance": {},
                    "differing": list(cache_keys.KEY_COMPONENTS)}
        return {"status": "empty", "reason": "no cache file yet",
                "rows": {}, "matrix": None, "provenance": {},
                "differing": list(cache_keys.KEY_COMPONENTS)}
    if matrix.shape[0] != len(identities) or matrix.ndim != 2:
        return {"status": "corrupt",
                "reason": (f"{len(identities)} identities for a "
                           f"{matrix.shape!r} matrix"),
                "rows": {}, "matrix": None, "provenance": {},
                "differing": list(cache_keys.KEY_COMPONENTS)}
    differing = cache_keys.differing_components(provenance.get("key"), wanted)
    if differing:
        return {"status": "stale-key",
                "reason": f"key moved: {', '.join(differing)}",
                "differing": differing,
                "rows": {}, "matrix": None, "provenance": provenance}
    rows = {identity: matrix[index] for index, identity in enumerate(identities)}
    return {"status": "ready", "reason": f"{len(rows)} cached rows",
            "differing": [], "rows": rows, "matrix": matrix,
            "provenance": provenance}


def store(root, digest: str, wanted: cache_keys.CacheKey,
          identities: list, matrix: np.ndarray, *,
          recipe: dict | None = None,
          backend: str | None = None) -> Path:
    """File one source's rows, atomically.

    ``identities`` parallels ``matrix`` row for row.  The write lands
    on a temp file in the same directory and is renamed over the old
    generation, so a killed build leaves the previous generation (or
    nothing), never half a matrix.
    """
    target = cache_path(root, digest)
    target.parent.mkdir(parents=True, exist_ok=True)
    provenance = {
        "key": wanted.to_dict(),
        "signature": wanted.signature(),
        "model": MODEL_ID,
        "cache_version": CACHE_VERSION,
        "preprocessing": recipe or dict(PREPROCESSING_RECIPE),
        "embedding": dict(EMBEDDING_METHOD),
        "embed_backend": backend or "unknown",
        "transformers": transformers_version(),
        "dimension": int(np.asarray(matrix).shape[1]) if len(identities) else 0,
        "frame_count": len(identities),
        "stored_at": time.time(),
    }
    handle, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=target.name,
                                   suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as stream:
            np.savez_compressed(
                stream,
                embeddings=np.asarray(matrix, dtype="float32"),
                identities=np.asarray(list(identities)),
                __provenance__=np.array(cache_keys.canonical_json(provenance)))
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return target


def partition(identities: list, rows: dict) -> tuple:
    """Split selected frames into cache hits and frames to encode.

    ``identities`` parallels the index rows: one stable identity per
    frame, in selection order.  An identity the cache does not hold -
    a new frame, changed bytes, a stale key - is a miss.  Returns
    ``(hits, misses)`` where hits are ``(position, vector)`` and
    misses are positions.  Pure: no I/O, no model.
    """
    hits, misses = [], []
    for position, identity in enumerate(identities):
        vector = rows.get(identity)
        if vector is not None:
            hits.append((position, vector))
        else:
            misses.append(position)
    return hits, misses
