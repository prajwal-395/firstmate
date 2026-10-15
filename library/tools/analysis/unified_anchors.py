"""The unified multimodal anchor index (the captain's single search index).

Per 8 s media window - the same grid the text chunks and the video-chunk
lane cut (`footage_segments.CHUNK_S`, pinned equal in test) - this builds
FIVE linked anchors in the one EmbeddingGemma 2 space:

* ``video``      - the window's 1 fps frame stack, EG2 video side;
* ``still``      - the window's middle still, EG2 image side;
* ``audio``      - the window's mono 16 kHz audio, EG2 audio side;
* ``description``- Ren's analysis words for the window (action / scene /
                   object / camera rows overlapping it), EG2 text side -
                   one anchor PER ROW, not one concatenated doc, so a
                   distinctive phrase is never diluted by its neighbours;
* ``transcript`` - Ren's spoken words for the window (speech / chunk rows
                   overlapping it), EG2 text side, per row like above.

A text query is encoded once on the EG2 query side and scores the
routed anchors (``INTENT_ANCHORS``: visual and shot-type queries reach
the video/still anchors, spoken phrases reach the transcript, BM25 runs
everywhere); an image query is encoded on the EG2 image side and
scores the two visual anchors.  Each lane is its own reciprocal-rank
half - no blend weight to set (AGENTS.md 10.1).  Retention is judged on
the RAW signals, never on the fused order: the fused score ranks the
candidate set, so its top row is high even for a subject that is
nowhere in the footage.  Keyword or OCR evidence retains only with a
routed anchor in the weak band (an exact on-screen word retains on its
own); dense mode stands on dense evidence alone.

Coverage follows the 2218 rules: a visual-intent query against an
index with no visual evidence answers ``not_analysed``, never
``not_found``; a text lane that could not be built degrades to
keyword-only and says so.

This backend is OPT-IN.  The default index stays MiniLM + CLIP until a
measured bakeoff in docs/benchmarks says the unified index wins
clearly.  Nothing here changes the default path: `build_unified` and
`search_unified` run only when asked for.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

import numpy as np

from library.tools.analysis import footage_segments as segments_mod
from library.tools.analysis.footage_query import (
    _BM25,
    _OCR_WORD,
    ABSENT_MARGIN,
    ABSENT_PROBES,
    EG2_TEXT_DIM,
    EG2_TEXT_MODEL,
    RRF_K,
    VIDEO_EMBED_BATCH_CHUNKS,
    _has_visual_intent,
    cut_video_windows,
    extract_chunk_frames,
    ingest_fingerprint,
    load_catalog,
    load_eg2_audio_embedder,
    load_eg2_image_embedder,
    load_eg2_text_embedder,
    load_eg2_video_embedder,
    ocr_text_matches,
)
from library.tools.analysis.footage_query import (
    _tokenize as _query_tokenize,
)
from library.tools.ren_refusal import RenRefusal

#: The five linked anchors of one window.  Order is storage order, not a
#: ranking: every anchor lives in the same EG2 space and scores alike.
UNIFIED_ANCHORS = ("video", "still", "audio", "description", "transcript")

#: Anchors that carry picture evidence for the 2218 visual-intent gate.
VISUAL_ANCHORS = ("video", "still")

#: Anchors that carry heard evidence.
HEARD_ANCHORS = ("audio", "transcript")

#: One grid for every anchor lane: the video-chunk lane's 8 s window.
UNIFIED_WINDOW_S = 8.0

#: Decoded pixels never grow beyond this many windows per batch.  Like
#: the video-chunk writer, the ceiling is local: a runtime mutation of
#: the module constant must not turn streaming back into an
#: all-windows allocation.
UNIFIED_BATCH_WINDOWS = 4

#: Audio the EG2 audio side hears: mono, 16 kHz, float32.
UNIFIED_AUDIO_RATE = 16000

UNIFIED_DESCRIPTORS_FILE = "unified_anchors.json"
_UNIFIED_MATRIX_FILES = {
    "video": "unified_video.npy",
    "still": "unified_still.npy",
    "audio": "unified_audio.npy",
    "description": "unified_text_desc.npy",
    "transcript": "unified_text_trans.npy",
}

#: Description text comes from these analysis rows; transcript text from
#: these speech rows.  Camera rows describe the shot, so a shot-type
#: query has an anchor to land on.
_DESCRIPTION_KINDS = ("action", "scene", "object", "camera")
_TRANSCRIPT_KINDS = ("speech", "chunk")


def _keep_finite(vectors: np.ndarray, owners: list, anchor: str,
                  anchor_unembedded: dict) -> tuple:
    """Drop non-finite encoder rows, naming each dropped window.

    The real video encoder emits NaN for short tail windows (measured:
    two 1.5-1.9 s tails on 001); a non-finite row is not a measurement
    and must never reach a matrix whose cosines judge retention.
    Returns ``(kept_vectors, kept_positions)``: positions index
    ``owners``, so a caller with several rows per window maps each kept
    row back to its own window rather than by id membership.
    """
    vectors = np.asarray(vectors, dtype="float32")
    if vectors.shape[0] != len(owners):
        raise ValueError(
            f"{anchor} encoder returned {vectors.shape}; expected "
            f"({len(owners)}, {EG2_TEXT_DIM})")
    finite = np.isfinite(vectors).all(axis=1)
    for owner, ok in zip(owners, finite):
        if not ok:
            anchor_unembedded[anchor].append({
                "anchor_id": owner["anchor_id"],
                "clip_id": owner["clip_id"],
                "reason": (f"{anchor} encoder returned a non-finite vector "
                           f"for this window")})
    kept = [i for i, ok in enumerate(finite) if ok]
    return vectors[finite], kept


def _rrf(*halves: np.ndarray, k: int = RRF_K) -> np.ndarray:
    """Rank-only fusion over score halves (no weight to tune)."""
    fused = np.zeros(len(halves[0]), dtype="float64")
    for half in halves:
        order = np.argsort(-np.asarray(half, dtype="float64"), kind="stable")
        ranks = np.empty(len(half), dtype="float64")
        ranks[order] = np.arange(1, len(half) + 1)
        fused += 1.0 / (k + ranks)
    return fused


def _overlaps(a_start, a_end, b_start, b_end) -> bool:
    try:
        return max(float(a_start), float(b_start)) < min(float(a_end), float(b_end))
    except (TypeError, ValueError):
        return False


def _window_texts(all_segments: list, clip_id: str, start: float, end: float,
                   kinds: tuple) -> str:
    """The analysis words for one window, oldest first, de-duplicated."""
    return " ".join(text for _seg_id, text in
                    _window_rows(all_segments, clip_id, start, end, kinds))


def _window_rows(all_segments: list, clip_id: str, start: float, end: float,
                 kinds: tuple) -> list:
    """The ``(segment_id, text)`` analysis rows overlapping one window.

    Oldest first, de-duplicated by text: the same row set
    `_window_texts` joins, kept as rows so each can be embedded on its
    own (per-row text anchors).  A row whose segment carries no id is
    still embedded - its text is the evidence - and is recorded under
    its window's id.
    """
    seen, rows = set(), []
    for seg in all_segments:
        if seg.get("clip_id") != clip_id or seg.get("kind") not in kinds:
            continue
        if not _overlaps(seg.get("start"), seg.get("end"), start, end):
            continue
        text = (seg.get("text") or "").strip()
        if text and text not in seen:
            seen.add(text)
            rows.append((seg.get("segment_id")
                         or f"{clip_id}#row#{len(rows):03d}", text))
    return rows


def extract_window_audio(source_file, start: float, end: float,
                         sample_rate: int = UNIFIED_AUDIO_RATE) -> dict:
    """Mono float32 audio for ``[start, end)`` as an ST audio dict.

    Raises RenRefusal naming the fix when ffmpeg is missing, the media
    is absent, or the source carries no audio stream - a window with no
    audio is recorded unembedded, never silently zero-filled.
    """
    import shutil

    if shutil.which("ffmpeg") is None:
        raise RenRefusal(
            "ffmpeg is not on PATH",
            "window audio is cut with ffmpeg",
            "install ffmpeg and rebuild the unified anchors")
    if not source_file or not Path(source_file).is_file():
        raise RenRefusal(
            f"no media file at {source_file!r}",
            "a window's audio comes from its clip's media file",
            "build the unified anchors on a machine holding the media")
    length = float(end) - float(start)
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(start), "-t", str(length),
         "-i", str(source_file), "-vn", "-ac", "1", "-ar", str(sample_rate),
         "-f", "f32le", "-"],
        capture_output=True, check=False)
    if proc.returncode != 0 or not proc.stdout:
        raise RenRefusal(
            f"no audio for {source_file}@{start}-{end}",
            "the window cannot be audio-embedded without its samples",
            ((proc.stderr or b"").decode("utf-8", "replace")[-300:]
             or "the source may carry no audio stream"))
    samples = np.frombuffer(proc.stdout, dtype="<f4").astype("float32")
    if samples.size == 0:
        raise RenRefusal(
            f"no audio for {source_file}@{start}-{end}",
            "ffmpeg returned zero samples",
            "the source may carry no audio stream")
    return {"array": samples, "sampling_rate": sample_rate}


def calibrate_anchor_floors(matrices: dict, encode_query):
    """Per-anchor ``(confident, weak, source, probe_bests)`` from absent probes.

    Each anchor matrix is judged in its own space: the floor travels
    with the matrix it was measured on, beside it in the payload.  An
    anchor with no rows calibrates nothing.
    """
    floors = {}
    for anchor, matrix in matrices.items():
        if matrix is None or len(matrix) == 0:
            continue
        finite = matrix[np.isfinite(matrix).all(axis=1)]
        if len(finite) == 0:
            continue
        bests = {}
        for probe in ABSENT_PROBES:
            vector = encode_query([probe], is_query=True)[0]
            bests[probe] = round(float((finite @ vector).max()), 4)
        peak = max(bests.values())
        floors[anchor] = {
            "confident": round(peak + ABSENT_MARGIN, 4),
            "weak": round(peak, 4),
            "source": (f"absent-probe calibration at build: worst absent best "
                       f"{peak:.4f} over {len(ABSENT_PROBES)} probes + "
                       f"{ABSENT_MARGIN} margin"),
            "probes": bests,
        }
    return floors


def _signature(project_folder, windows: list) -> str:
    return hashlib.sha256(json.dumps(
        [ingest_fingerprint(project_folder), windows],
        separators=(",", ":")).encode("utf-8")).hexdigest()


def build_unified(project_folder, index_dir=None, verbose=False) -> dict:
    """Cut, embed and store the five linked anchors.  Returns build statistics.

    Reads the project's ingest output; writes ONLY into `index_dir`.
    Every encoder is bounded: decoded pixels and encoder inputs never
    grow beyond UNIFIED_BATCH_WINDOWS windows, audio is cut per window,
    and text anchors embed per batch.  An interrupted compatible build
    resumes from its checkpoint; a window whose media or text is absent
    is recorded in `unified_unembedded` with its reason, never
    silently dropped.
    """
    started = time.perf_counter()
    target = Path(index_dir) if index_dir else Path(
        str(project_folder)) / "pipeline_output" / "scratch" / "unified_index"
    target.mkdir(parents=True, exist_ok=True)

    all_segments = [s.to_dict() for s in segments_mod.build_segments(project_folder)]
    catalog = load_catalog(project_folder)
    windows: list = []
    for clip in catalog:
        clip_id = clip.get("clip_id")
        source = clip.get("source_file") or clip.get("path")
        try:
            duration = float(clip.get("duration_seconds"))
        except (TypeError, ValueError):
            duration = 0.0
        for n, (start, end) in enumerate(
                cut_video_windows(duration, window_s=UNIFIED_WINDOW_S) if clip_id else []):
            windows.append((clip_id, clip.get("filename", ""), source, n, start, end))

    vencode, video_backend = load_eg2_video_embedder()
    iencode, image_backend = load_eg2_image_embedder()
    aencode, audio_backend = load_eg2_audio_embedder()
    tencode, text_backend = load_eg2_text_embedder()
    backends = {"video": video_backend, "still": image_backend,
                "audio": audio_backend, "description": text_backend,
                "transcript": text_backend}
    encoders = {"video": vencode, "still": iencode, "audio": aencode,
                "description": tencode, "transcript": tencode}

    descriptors: list = []
    unembedded: list = []
    anchor_rows: dict = {anchor: [] for anchor in UNIFIED_ANCHORS}
    anchor_unembedded: dict = {anchor: [] for anchor in UNIFIED_ANCHORS}
    anchor_seconds: dict = {anchor: 0.0 for anchor in UNIFIED_ANCHORS}

    desc_path = target / UNIFIED_DESCRIPTORS_FILE
    checkpoint_path = target / (UNIFIED_DESCRIPTORS_FILE + ".checkpoint.json")
    checkpoint_tmp = target / (UNIFIED_DESCRIPTORS_FILE + ".checkpoint.tmp")
    partial_paths = {a: target / (f + ".partial.npy")
                     for a, f in _UNIFIED_MATRIX_FILES.items()}
    signature = _signature(project_folder, [list(w) for w in windows])
    # Text anchors hold one row per overlapping analysis row, so their
    # matrices are bigger than one row per window; the media anchors
    # stay one row per window.  Capacities are counted up front so the
    # durable memmaps never grow mid-build.
    anchor_capacity = {anchor: len(windows) for anchor in UNIFIED_ANCHORS}
    for anchor, kinds in (("description", _DESCRIPTION_KINDS),
                          ("transcript", _TRANSCRIPT_KINDS)):
        anchor_capacity[anchor] = sum(
            len(_window_rows(all_segments, clip_id, start, end, kinds))
            for clip_id, _filename, _source, _n, start, end in windows)

    checkpoint = None
    try:
        candidate = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if (candidate["signature"] == signature
                and candidate["capacity"] == len(windows)
                and candidate.get("anchor_capacity") == anchor_capacity
                and 0 <= candidate["processed"] <= len(windows)
                and isinstance(candidate.get("descriptors"), list)
                and all(isinstance(candidate.get(k), list)
                        for k in ("unembedded",) + tuple(f"rows_{a}" for a in UNIFIED_ANCHORS))):
            checkpoint = candidate
    except (OSError, ValueError, KeyError, TypeError):
        pass

    vmat = {}
    if checkpoint is None:
        for stale in [desc_path, checkpoint_path, checkpoint_tmp,
                      *[target / f for f in _UNIFIED_MATRIX_FILES.values()],
                      *partial_paths.values()]:
            stale.unlink(missing_ok=True)
        start_window = 0
    else:
        descriptors = checkpoint["descriptors"]
        unembedded = checkpoint["unembedded"]
        for anchor in UNIFIED_ANCHORS:
            anchor_rows[anchor] = checkpoint[f"rows_{anchor}"]
            anchor_unembedded[anchor] = checkpoint[f"missing_{anchor}"]
        start_window = checkpoint["processed"]

    if windows:
        for anchor in UNIFIED_ANCHORS:
            if encoders[anchor] is None:
                continue
            if anchor in vmat:
                continue
            if anchor_capacity[anchor] == 0:
                continue
            vmat[anchor] = np.lib.format.open_memmap(
                partial_paths[anchor],
                mode="r+" if (checkpoint is not None
                              and partial_paths[anchor].is_file()) else "w+",
                dtype="float32",
                shape=(anchor_capacity[anchor], EG2_TEXT_DIM))

    batch_size = max(1, min(VIDEO_EMBED_BATCH_CHUNKS, UNIFIED_BATCH_WINDOWS))
    failed_clips: set = set(checkpoint.get("failed_clips", []) if checkpoint else [])
    for batch_start in range(start_window, len(windows), batch_size):
        batch = windows[batch_start:batch_start + batch_size]
        # Per-anchor inputs for this batch, aligned to `owners`.
        batch_inputs: dict = {a: [] for a in UNIFIED_ANCHORS}
        owners: list = []
        for clip_id, filename, source, n, start, end in batch:
            if clip_id in failed_clips:
                continue
            description = _window_texts(all_segments, clip_id, start, end,
                                        _DESCRIPTION_KINDS)
            transcript = _window_texts(all_segments, clip_id, start, end,
                                       _TRANSCRIPT_KINDS)
            # Per-row text anchors: the same overlapping rows, kept as
            # rows.  The joined strings above stay for BM25 and display;
            # the matrices below hold one embedding per ROW, each tagged
            # with its window, and a window scores the max over its rows.
            description_rows = _window_rows(all_segments, clip_id, start, end,
                                            _DESCRIPTION_KINDS)
            transcript_rows = _window_rows(all_segments, clip_id, start, end,
                                           _TRANSCRIPT_KINDS)
            owners.append({
                "anchor_id": f"{clip_id}#u#{n:03d}",
                "clip_id": clip_id, "filename": filename,
                "start": start, "end": end,
                "description_text": description,
                "transcript_text": transcript,
                "description_rows": [seg_id for seg_id, _text in description_rows],
                "transcript_rows": [seg_id for seg_id, _text in transcript_rows],
            })
            batch_inputs["description"].append(description_rows)
            batch_inputs["transcript"].append(transcript_rows)
            try:
                frames = extract_chunk_frames(source, start, end)
            except RenRefusal as exc:
                failed_clips.add(clip_id)
                unembedded.append({"clip_id": clip_id,
                                   "reason": f"{exc.what} ({exc.fix})"})
                owners.pop()
                batch_inputs["description"].pop()
                batch_inputs["transcript"].pop()
                continue
            batch_inputs["video"].append(
                np.stack([np.asarray(f, dtype="uint8") for f in frames]))
            middle = frames[len(frames) // 2]
            batch_inputs["still"].append(middle)
            try:
                batch_inputs["audio"].append(
                    extract_window_audio(source, start, end))
            except RenRefusal as exc:
                batch_inputs["audio"].append(None)
                anchor_unembedded["audio"].append({
                    "anchor_id": f"{clip_id}#u#{n:03d}", "clip_id": clip_id,
                    "reason": f"{exc.what} ({exc.fix})"})
        if not owners:
            checkpoint_tmp.write_text(json.dumps({
                "signature": signature, "capacity": len(windows),
                "anchor_capacity": anchor_capacity,
                "processed": min(batch_start + batch_size, len(windows)),
                "descriptors": descriptors, "unembedded": unembedded,
                **{f"rows_{a}": anchor_rows[a] for a in UNIFIED_ANCHORS},
                **{f"missing_{a}": anchor_unembedded[a] for a in UNIFIED_ANCHORS},
                "failed_clips": sorted(failed_clips),
            }), encoding="utf-8")
            os.replace(checkpoint_tmp, checkpoint_path)
            continue
        t0 = time.perf_counter()
        for anchor in UNIFIED_ANCHORS:
            encode = encoders[anchor]
            if encode is None:
                continue
            inputs = batch_inputs[anchor]
            if anchor in ("description", "transcript"):
                # Text anchors embed only where there are words; a
                # window with no rows in this lane stays absent from
                # the matrix and is named, never zero-filled.  Each
                # overlapping ROW is embedded on its own: a window doc
                # concatenating every row dilutes the distinctive phrase
                # ("neutral expression" inside a 913-char doc scores
                # below its own floor while unrelated windows clear it -
                # round-1 finding).  The matrix holds one row per text
                # and `anchor_rows` tags each with its window's id, so a
                # window with several rows appears several times and
                # scores the max over them at query time.
                flat_texts, flat_windows, flat_clips = [], [], []
                for i, rows in enumerate(inputs):
                    if not rows:
                        anchor_unembedded[anchor].append({
                            "anchor_id": owners[i]["anchor_id"],
                            "clip_id": owners[i]["clip_id"],
                            "reason": f"no {anchor} text overlaps this window"})
                    for _seg_id, text in rows:
                        flat_texts.append(text)
                        flat_windows.append(owners[i]["anchor_id"])
                        flat_clips.append(owners[i]["clip_id"])
                if not flat_texts:
                    continue
                vectors, kept = _keep_finite(
                    np.asarray(encode(flat_texts), dtype="float32"),
                    [{"anchor_id": a, "clip_id": c}
                     for a, c in zip(flat_windows, flat_clips)],
                    anchor, anchor_unembedded)
                # `vectors` is compact (kept rows only); `kept` indexes
                # the originals, so each row maps back to its own
                # window even when a window contributed several rows.
                for new_i, pos in enumerate(kept):
                    vmat[anchor][len(anchor_rows[anchor])] = vectors[new_i]
                    anchor_rows[anchor].append(flat_windows[pos])
            elif anchor == "audio":
                present = [(i, a) for i, a in enumerate(inputs) if a is not None]
                if not present:
                    continue
                idx, audios = zip(*present)
                vectors, kept = _keep_finite(
                    np.asarray(encode(list(audios)), dtype="float32"),
                    [owners[i] for i in idx], anchor, anchor_unembedded)
                for new_i, pos in enumerate(kept):
                    owner = owners[idx[pos]]
                    vmat[anchor][len(anchor_rows[anchor])] = vectors[new_i]
                    anchor_rows[anchor].append(owner["anchor_id"])
            else:
                vectors, kept = _keep_finite(
                    np.asarray(encode(inputs), dtype="float32"),
                    owners, anchor, anchor_unembedded)
                for new_i, pos in enumerate(kept):
                    vmat[anchor][len(anchor_rows[anchor])] = vectors[new_i]
                    anchor_rows[anchor].append(owners[pos]["anchor_id"])
            vmat[anchor].flush()
            anchor_seconds[anchor] += time.perf_counter() - t0
            t0 = time.perf_counter()
        descriptors.extend(owners)
        checkpoint_tmp.write_text(json.dumps({
            "signature": signature, "capacity": len(windows),
            "anchor_capacity": anchor_capacity,
            "processed": min(batch_start + batch_size, len(windows)),
            "descriptors": descriptors, "unembedded": unembedded,
            **{f"rows_{a}": anchor_rows[a] for a in UNIFIED_ANCHORS},
            **{f"missing_{a}": anchor_unembedded[a] for a in UNIFIED_ANCHORS},
            "failed_clips": sorted(failed_clips),
        }), encoding="utf-8")
        os.replace(checkpoint_tmp, checkpoint_path)

    # Finalize: exact-size matrices without ever materializing the full
    # build in memory; anchors with no rows leave no file behind.
    dimensions = {}
    for anchor in UNIFIED_ANCHORS:
        rows = anchor_rows[anchor]
        dimensions[anchor] = EG2_TEXT_DIM if rows else 0
        dest = target / _UNIFIED_MATRIX_FILES[anchor]
        if not rows or anchor not in vmat:
            dest.unlink(missing_ok=True)
            dimensions[anchor] = 0
            continue
        final_tmp = target / (_UNIFIED_MATRIX_FILES[anchor] + ".final.npy")
        exact = np.lib.format.open_memmap(
            final_tmp, mode="w+", dtype="float32",
            shape=(len(rows), EG2_TEXT_DIM))
        part = vmat.get(anchor)
        if part is None:
            part = np.lib.format.open_memmap(
                partial_paths[anchor], mode="r")
        for start in range(0, len(rows), 256):
            end = min(start + 256, len(rows))
            exact[start:end] = part[start:end]
        exact.flush()
        del exact
        os.replace(final_tmp, dest)
    for handle in vmat.values():
        del handle
    for path in partial_paths.values():
        path.unlink(missing_ok=True)
    checkpoint_path.unlink(missing_ok=True)

    matrices = {}
    for anchor in UNIFIED_ANCHORS:
        path = target / _UNIFIED_MATRIX_FILES[anchor]
        matrices[anchor] = (np.load(path, mmap_mode="r")
                            if path.is_file() else None)
    floors = calibrate_anchor_floors(matrices, tencode) if tencode else {}

    from library.tools.analysis import footage_query as fq

    ocr_texts, ocr_methods = fq.ocr_texts_for_project(project_folder)
    coverage = segments_mod.coverage_report(project_folder)
    row_sets = {a: set(anchor_rows[a]) for a in UNIFIED_ANCHORS}
    clip_windows: dict = {}
    for d in descriptors:
        clip_windows.setdefault(d["clip_id"], []).append(d["anchor_id"])
    for clip_id, clip_coverage in coverage["per_clip"].items():
        clip_coverage["video_embeddings"] = any(
            rid in row_sets["video"] for rid in clip_windows.get(clip_id, []))
        clip_coverage["unified_anchors"] = sorted(
            a for a in UNIFIED_ANCHORS
            if dimensions[a] and any(
                rid in row_sets[a] for rid in clip_windows.get(clip_id, [])))

    payload = {
        "project_folder": str(project_folder),
        "backend": "unified-eg2",
        "embed_model": EG2_TEXT_MODEL,
        "embed_dimension": EG2_TEXT_DIM,
        "window_seconds": UNIFIED_WINDOW_S,
        "anchors": list(UNIFIED_ANCHORS),
        "anchor_backends": backends,
        "anchor_dimensions": dimensions,
        "window_count": len(descriptors),
        "built_at": time.time(),
        "ingest_fingerprint": ingest_fingerprint(project_folder),
        "coverage": coverage,
        "descriptors": descriptors,
        "anchor_rows": {a: anchor_rows[a] for a in UNIFIED_ANCHORS},
        "unified_unembedded": unembedded,
        "anchor_unembedded": {a: anchor_unembedded[a] for a in UNIFIED_ANCHORS},
        "anchor_floors": floors,
        "ocr_texts": ocr_texts,
        "ocr_methods": ocr_methods,
    }
    desc_path.write_text(json.dumps(payload, indent=1), encoding="utf-8")

    stats = {
        "index_dir": str(target),
        "backend": "unified-eg2",
        "window_count": len(descriptors),
        "anchor_dimensions": dimensions,
        "anchor_backends": backends,
        "unified_unembedded": unembedded,
        "anchor_unembedded_counts": {a: len(anchor_unembedded[a])
                                     for a in UNIFIED_ANCHORS},
        "anchor_seconds": {a: round(s, 3) for a, s in anchor_seconds.items()},
        "anchor_floors": {a: f["confident"] for a, f in floors.items()},
        "total_seconds": round(time.perf_counter() - started, 3),
        "bytes_on_disk": sum(
            p.stat().st_size for p in target.iterdir() if p.is_file()),
        "coverage": coverage,
    }
    if verbose:
        print(json.dumps(stats, indent=2))
    return stats


#: Query intent signals, checked in order: text, shot, spoken, then the
#: shared visual gate, else general.  Text first because a shot query can
#: name a sign ("show me the street sign on screen") and a text query can
#: quote speech ("what does the on-screen text TAB say") - the more
#: specific evidence wins the route.
_TEXT_INTENT = re.compile(
    r"\b(on[- ]screen|street signs?|road signs?|signs?|written|reads?|"
    r"texts?|labels?|captions?)\b",
    re.IGNORECASE)
_SHOT_INTENT = re.compile(
    r"\b(shots?|wide|close[- ]?up|medium|selfie|handheld|stead\w*|shak\w*|"
    r"footage|interior|exterior|scenes?|daylight|sunset|camera|angles?|"
    r"aerial|drone|tracking|static|driving|parking lot)\b",
    re.IGNORECASE)
_SPOKEN_INTENT = re.compile(
    r"\b(says?|said|saying|talks?|talked|talking|tells?|telling|told|"
    r"mentions?|asks?|asked|asking|shout out|quotes?|recites?)\b",
    re.IGNORECASE)

_MOTION_INTENT = re.compile(
    r"\b(motion|moving|moves|move|shaking|shaky|"
    r"camera movement|pan(?:ning)?|tilt(?:ing)?|tracking)\b", re.IGNORECASE)
_ACTION_INTENT = re.compile(
    r"\b(action|doing|drink(?:ing)?|pick(?:ing)? up|put(?:ting)? down|"
    r"wave(?:s|d|ing)?|point(?:s|ed|ing)|gesture|gestur(?:e|ing)|"
    r"open(?:s|ed|ing)?|close(?:s|d|ing)?)\b", re.IGNORECASE)
_LOCOMOTION_INTENT = re.compile(
    r"\b(?:(?:he|she|they|someone|person|man|woman|host)\s+"
    r"(?:is\s+)?|(?:while|as)\s+)(?:walking|running)\b", re.IGNORECASE)


def route_query_intent(query: str) -> str:
    """One of ``spoken``/``shot``/``text``/``visual``/``general``.

    A cheap deterministic route, not a judgement: each intent names the
    dense anchors that can answer it (``INTENT_ANCHORS``).  A query with
    no signal is ``general`` and keeps every anchor - routing excludes
    only lanes that cannot answer, so an unmarked query loses nothing.
    """
    text = query or ""
    if _TEXT_INTENT.search(text):
        return "text"
    if _SHOT_INTENT.search(text):
        return "shot"
    if _SPOKEN_INTENT.search(text):
        return "spoken"
    if _has_visual_intent(text):
        return "visual"
    return "general"


def route_query_intents(query: str) -> tuple[str, ...]:
    """Return every evidence intent a query needs, without loading a model."""
    text = query or ""
    intents = []
    if _TEXT_INTENT.search(text):
        intents.append("text")
    if _SPOKEN_INTENT.search(text):
        intents.append("spoken")
    if _MOTION_INTENT.search(text) or _LOCOMOTION_INTENT.search(text):
        intents.append("motion")
    if _ACTION_INTENT.search(text) or _LOCOMOTION_INTENT.search(text):
        intents.append("action")
    if _SHOT_INTENT.search(text) or _has_visual_intent(text):
        intents.append("shot")
    return tuple(intents or ("general",))


#: Dense anchors that can answer each intent.  Speech content lives in
#: the transcript: the audio tower hears the same speech, but a text
#: query against speech-audio similarity is unproven, so ``spoken``
#: takes the transcript alone.  Pixels plus the object/camera rows
#: answer shot and on-screen-text questions - speech cannot say what is
#: written on screen, so ``text`` drops the transcript and audio lanes.
#: ``visual`` and ``general`` keep every anchor, so the video and the
#: analysis description complement each other as the captain intends.
INTENT_ANCHORS = {
    "spoken": ("transcript",),
    "shot": ("video", "still", "description"),
    "text": ("video", "still", "description"),
    "visual": ("video", "still", "audio", "description", "transcript"),
    "general": ("video", "still", "audio", "description", "transcript"),
    "motion": ("video", "description"),
    "action": ("video", "description"),
}


def anchors_for_query(query: str) -> tuple[str, ...]:
    """Union required anchors while excluding unrelated expensive lanes."""
    return tuple(anchor for anchor in UNIFIED_ANCHORS
                 if any(anchor in INTENT_ANCHORS[intent]
                        for intent in route_query_intents(query)))


def required_capabilities_for_query(query: str) -> tuple[str, ...]:
    """Name transcript and temporal evidence readers required by the query."""
    capabilities = []
    for intent in route_query_intents(query):
        if intent == "spoken":
            capabilities.append("transcript_search")
        elif intent == "motion":
            capabilities.append("measured_motion")
        elif intent == "action":
            capabilities.extend(("temporal_action_candidates", "action_verification"))
    return tuple(dict.fromkeys(capabilities))


def _ocr_exact(query_tokens: list, text: str) -> bool:
    """Whether some query token exactly equals an on-screen word.

    An exact word on screen ("SHOP" for "shop") is strong evidence on
    its own: the text is really pictured, so a window carrying it
    retains without dense corroboration.  A substring-only OCR match
    still needs an anchor in the weak band to retain.
    """
    words = set(_OCR_WORD.findall((text or "").lower()))
    return any(token in words for token in query_tokens or [])


class UnifiedIndex:
    """Searchable unified anchors for one project's footage.

    Loaders are lazy: constructing this is free and a caller that only
    wants descriptors never pays for the model.
    """
    def __init__(self, project_folder, index_dir=None):
        self.project_folder = str(project_folder)
        base = (Path(index_dir) if index_dir else
                Path(str(project_folder)) / "pipeline_output" / "scratch"
                / "unified_index")
        self.index_dir = base
        self._payload = None
        self._matrices: dict = {}
        self._text_encoder = None
        self._text_backend = None
        self._image_encoder = None

    @property
    def payload(self) -> dict:
        if self._payload is None:
            path = self.index_dir / UNIFIED_DESCRIPTORS_FILE
            if not path.exists():
                raise RenRefusal(
                    f"no unified index at {path}",
                    "unified search reads the built anchors; nothing has "
                    "built them for this project yet",
                    "build them first: ren search-index "
                    f"'{self.project_folder}' --unified-anchors")
            with open(path, encoding="utf-8") as f:
                self._payload = json.load(f)
        return self._payload

    @property
    def descriptors(self) -> list:
        return self.payload["descriptors"]

    def matrix(self, anchor: str):
        if anchor not in self._matrices:
            path = self.index_dir / _UNIFIED_MATRIX_FILES[anchor]
            self._matrices[anchor] = (np.load(path) if path.exists() else None)
        return self._matrices[anchor]

    def _encode_text_query(self, query: str):
        if self._text_encoder is None:
            self._text_encoder, self._text_backend = load_eg2_text_embedder()
        if self._text_encoder is None:
            return None
        return self._text_encoder([query], is_query=True)[0]

    def _encode_image_query(self, image):
        if self._image_encoder is None:
            self._image_encoder, _ = load_eg2_image_embedder()
        if self._image_encoder is None:
            return None
        return self._image_encoder([image])[0]

    @staticmethod
    def _timecode(seconds) -> str:
        try:
            total = float(seconds)
        except (TypeError, ValueError):
            return "?"
        minutes, rest = divmod(total, 60)
        return f"{int(minutes):02d}:{rest:06.3f}"

    def _window_bm25(self) -> _BM25:
        texts = [f"{d.get('description_text', '')} {d.get('transcript_text', '')}"
                 for d in self.descriptors]
        return _BM25(texts)

    def _ocr_hits(self, query: str) -> list:
        # Stopword-filtered tokens and the shared specific rule: the old
        # both-directions substring on raw tokens let "the" match "ITHE"
        # and any shared letter match a 1-char string, flooding visual
        # queries with OCR rows.  Each hit records whether some query
        # token EXACTLY equals an on-screen word: an exact word on
        # screen ("SHOP") is strong evidence on its own, while a
        # substring-only match still needs dense corroboration to retain.
        tokens = _query_tokenize(query or "")
        if not tokens:
            return []
        filenames = {d["clip_id"]: d["filename"] for d in self.descriptors}
        hits = []
        for clip_id, detections in (self.payload.get("ocr_texts") or {}).items():
            for number, detection in enumerate(detections or []):
                text = detection.get("text", "")
                if not ocr_text_matches(tokens, text):
                    continue
                spans = detection.get("appearances") or [[0.0, 0.0]]
                for start, end in spans:
                    try:
                        start_f, end_f = float(start), float(end)
                    except (TypeError, ValueError):
                        continue
                    confidence = float(detection.get("confidence") or 0.0)
                    hits.append({
                        "segment_id": f"{clip_id}#ocr#{number:03d}",
                        "clip_id": clip_id,
                        "filename": filenames.get(clip_id, clip_id),
                        "kind": "ocr", "start": start_f, "end": end_f,
                        "duration": round(end_f - start_f, 3),
                        "timecode": (f"{self._timecode(start_f)}-"
                                     f"{self._timecode(end_f)}"),
                        "text": text,
                        "score": round(confidence, 4),
                        "ocr_score": round(confidence, 4),
                        "ocr_exact": _ocr_exact(tokens, text),
                        "visual_evidence": True,
                    })
        return hits

    def search_unified(self, query: str, top_k: int = 5, mode: str = "hybrid",
                       floor: float | None = None, query_image=None) -> dict:
        """One query against the routed anchors, fused with keyword + OCR.

        `query` is text; `query_image` (a PIL image or HWC array) scores
        the visual anchors instead of - or beside - the text.  The text
        is routed by intent to the anchors that can answer it
        (``INTENT_ANCHORS``) and the lanes fuse by reciprocal-rank
        fusion - one half per anchor, plus BM25 everywhere and OCR where
        it matched - so no blend weight decides between lanes.  Returns
        the report dict: per-window hits with per-anchor scores, the
        weak band, what was considered and retained, and whether the
        index abstained.  An empty `results` is a real answer.
        """
        if mode not in ("hybrid", "dense", "lexical"):
            raise RenRefusal(
                f"unknown search mode {mode!r}",
                "unified search runs hybrid, dense or lexical",
                "use --mode hybrid, --mode dense or --mode lexical")
        descriptors = self.descriptors
        n = len(descriptors)
        floors = self.payload.get("anchor_floors") or {}
        if floor is None:
            conf = {a: f["confident"] for a, f in floors.items()
                    if np.isfinite(f["confident"])}
            weak = {a: f["weak"] for a, f in floors.items()
                    if np.isfinite(f["weak"])}
            if not conf or not weak:
                raise RenRefusal(
                    "no calibrated anchor floor for this index",
                    "retention is judged on raw anchor cosines and no "
                    "anchor measured a floor to judge them by",
                    "rebuild the unified anchors with the EG2 model "
                    "available so calibration runs")
            # Retention is per anchor, each judged by ITS OWN floor: the
            # five anchors live in one space but their absent
            # distributions differ (a transcript floor of 0.79 would
            # silence every video hit).  The max below only orders the
            # report header, never the keep decision.
            mode_floor, weak_floor = max(conf.values()), max(weak.values())
            anchor_floors = conf
            anchor_weaks = weak
            floor_source = ("max over calibrated anchor floors; " +
                            ", ".join(f"{a}={floors[a]['confident']:.2f}"
                                       for a in sorted(floors)) if floors
                            else "no calibrated anchor floor - refusing")
        else:
            mode_floor, weak_floor = float(floor), min(float(floor), 0.60)
            floor_source = "explicit floor argument (--floor)"
            anchor_floors = {a: mode_floor for a in UNIFIED_ANCHORS}
            anchor_weaks = {a: weak_floor for a in UNIFIED_ANCHORS}

        visual_intent = _has_visual_intent(query or "")
        intents = route_query_intents(query or "")
        intent = intents[0] if len(intents) == 1 else "multi"
        lanes = anchors_for_query(query or "")
        coverage = self.payload.get("coverage", {})
        per_clip = coverage.get("per_clip", {})
        # The 2218 rule: a visual question needs PICTURE evidence.  Heard
        # anchors (audio, transcript) never count - an index that heard a
        # window but never saw it answers not_analysed, never not_found.
        visual_covered = any(
            any(clip.get(m, False) for m in ("picture", "scene", "action",
                                             "object", "ocr", "video_embeddings"))
            or any(a in (clip.get("unified_anchors") or [])
                   for a in VISUAL_ANCHORS)
            for clip in per_clip.values())
        if not per_clip:
            visual_covered = any(d.get("description_text") for d in descriptors)

        report = {
            "query": query, "mode": mode, "backend": "unified-eg2",
            "floor": mode_floor, "weak_floor": weak_floor,
            "floor_source": floor_source,
            "anchors": [a for a in UNIFIED_ANCHORS
                        if self.matrix(a) is not None],
            "window_count": n,
            "results": [], "weak": [], "considered": n, "retained": 0,
            "abstained": False, "degraded": None, "error": None,
            "query_intent": intent, "query_intents": list(intents),
            "intent_lanes": list(lanes),
            "required_capabilities": list(required_capabilities_for_query(query or "")),
            "answer_status": None, "coverage": coverage,
        }
        if n == 0:
            report["error"] = "This unified index holds no windows."
            return report
        if query_image is None and not (query or "").strip():
            report["error"] = "A unified search needs a text query, an image, or both."
            return report
        if visual_intent and not visual_covered:
            report["abstained"] = True
            report["answer_status"] = "not_analysed"
            report["error"] = ("not analysed: this index has no visual "
                               "evidence coverage")
            return report

        text_vector, image_vector = None, None
        dense = np.zeros(n, dtype="float32")
        per_anchor = {}
        row_index = {}
        for a in UNIFIED_ANCHORS:
            by_id: dict = {}
            for i, rid in enumerate(
                    (self.payload.get("anchor_rows") or {}).get(a) or []):
                by_id.setdefault(rid, []).append(i)
            row_index[a] = by_id

        def _place(anchor: str, vector, key: str | None = None) -> None:
            """Score one anchor's matrix, placing rows back on windows.

            A window with several text rows (per-row anchors) scores the
            max over its rows: one distinctive row is evidence even when
            its neighbours are not.
            """
            matrix = self.matrix(anchor)
            if matrix is None or vector is None:
                return
            scores = np.zeros(n, dtype="float32")
            by_id = row_index.get(anchor) or {}
            rows = (self.payload.get("anchor_rows") or {}).get(anchor) or []
            if by_id and len(matrix) == len(rows):
                hits = matrix @ vector
                for pos, d in enumerate(descriptors):
                    for row in by_id.get(d["anchor_id"], ()):
                        scores[pos] = max(scores[pos], hits[row])
            elif len(matrix) == n:
                scores = (matrix @ vector).astype("float32")
            else:
                return
            per_anchor[key or anchor] = scores

        if (query or "").strip() and mode in ("hybrid", "dense"):
            text_vector = self._encode_text_query(query)
            if text_vector is None:
                report["degraded"] = ("no EG2 text encoder - this answer is "
                                      "keyword match only")
            else:
                for anchor in lanes:
                    _place(anchor, text_vector)
                if per_anchor:
                    dense = np.max(list(per_anchor.values()), axis=0)
        if query_image is not None:
            image_vector = self._encode_image_query(query_image)
            if image_vector is None:
                report["degraded"] = ((report["degraded"] or "") +
                                      " no EG2 image encoder - the image was not scored.").strip()
            else:
                for anchor in VISUAL_ANCHORS:
                    _place(anchor, image_vector, key=f"{anchor}@image")
                if per_anchor:
                    dense = np.max(list(per_anchor.values()), axis=0)

        lexical = np.zeros(n, dtype="float32")
        if (query or "").strip():
            lexical = self._window_bm25().scores(query).astype("float32")
        ocr = np.zeros(n, dtype="float32")
        ocr_exact = np.zeros(n, dtype="float32")
        if (query or "").strip():
            ocr_hit_clips: dict = {}
            for hit in self._ocr_hits(query):
                ocr_hit_clips.setdefault(hit["clip_id"], []).append(hit)
            for i, d in enumerate(descriptors):
                for hit in ocr_hit_clips.get(d["clip_id"], []):
                    if hit["end"] > d["start"] and hit["start"] < d["end"]:
                        ocr[i] = max(ocr[i], 1.0)
                        if hit.get("ocr_exact"):
                            ocr_exact[i] = max(ocr_exact[i], 1.0)

        has_dense = bool(per_anchor)
        if mode == "dense" and not has_dense:
            report["error"] = "This unified index has no anchor vectors; use mode='lexical'."
            return report
        if mode == "lexical":
            combined = lexical
        elif mode == "dense":
            # Dense ranks the routed anchors only: each routed lane is
            # its own RRF half, so breadth across answering lanes beats
            # one loud lane, with no keyword half to promote neighbours.
            combined = _rrf(*per_anchor.values())
        elif not has_dense:
            combined = lexical
            if mode == "hybrid":
                report["degraded"] = ((report["degraded"] or "") +
                                      " keyword-only fallback.").strip()
                report["backend_answered"] = "bm25"
        else:
            halves = list(per_anchor.values()) + [lexical]
            if ocr.any():
                halves.append(ocr)
            combined = _rrf(*halves)

        if has_dense and mode_floor > 0:
            anchor_keep = np.zeros(n, dtype=bool)
            weak_any = np.zeros(n, dtype=bool)
            for anchor, scores in per_anchor.items():
                base = anchor.split("@")[0]
                anchor_keep |= scores >= anchor_floors.get(base, mode_floor)
                weak_any |= scores >= anchor_weaks.get(base, weak_floor)
            if mode == "dense":
                # Anchors only: a dense answer stands on dense evidence.
                keep = anchor_keep
            else:
                # Keyword or OCR evidence retains only with dense
                # corroboration - a bare word overlap ("birthday" in an
                # unrelated window) is not a finding on its own, which
                # is what held hybrid abstention at 0.000 on 001.  An
                # EXACT on-screen word is pictured text, so it retains
                # on its own like a dense hit does.
                keep = (anchor_keep | ((lexical > 0) & weak_any)
                        | (ocr_exact > 0) | ((ocr > 0) & weak_any))
        elif has_dense:
            keep = np.ones(n, dtype=bool)
        else:
            keep = (lexical > 0) | (ocr > 0)
        if visual_intent:
            keep &= np.array([bool(d.get("description_text")) for d in descriptors])
        if mode == "lexical":
            keep = (lexical > 0) | (ocr > 0)

        order = np.flatnonzero(keep)
        if order.size:
            ranking = order[np.argsort(-combined[order], kind="stable")][:max(0, top_k)]
            for pos in ranking:
                i = int(pos)
                d = descriptors[i]
                hit = {
                    "anchor_id": d["anchor_id"], "clip_id": d["clip_id"],
                    "filename": d["filename"], "kind": "window",
                    "start": d["start"], "end": d["end"],
                    "duration": round(d["end"] - d["start"], 3),
                    "timecode": (f"{self._timecode(d['start'])}-"
                                 f"{self._timecode(d['end'])}"),
                    "description_text": d.get("description_text", ""),
                    "transcript_text": d.get("transcript_text", ""),
                    "score": round(float(combined[i]), 4),
                    "lexical_score": round(float(lexical[i]), 4),
                    "ocr_score": round(float(ocr[i]), 4),
                }
                for anchor, scores in per_anchor.items():
                    hit[f"{anchor}_score"] = round(float(scores[i]), 4)
                if has_dense:
                    hit["dense_score"] = round(float(dense[i]), 4)
                report["results"].append(hit)
        report["retained"] = len(report["results"])

        if visual_intent and (query or "").strip():
            ocr_rows = self._ocr_hits(query)
            if ocr_rows:
                seen = {(r["clip_id"], r["start"]) for r in report["results"]}
                extra = [h for h in ocr_rows
                         if (h["clip_id"], h["start"]) not in seen]
                report["results"] = (extra + report["results"])[:max(0, top_k)]
                report["retained"] = len(report["results"])

        if has_dense and mode_floor > 0:
            weak_mask = np.zeros(n, dtype=bool)
            for anchor, scores in per_anchor.items():
                base = anchor.split("@")[0]
                weak_mask |= scores >= anchor_weaks.get(base, weak_floor)
            weak_mask &= ~keep
            weak_idx = np.flatnonzero(weak_mask)
            if weak_idx.size:
                near = weak_idx[np.argsort(-dense[weak_idx], kind="stable")]
                for pos in near[:5]:
                    i = int(pos)
                    d = descriptors[i]
                    report["weak"].append({
                        "anchor_id": d["anchor_id"], "clip_id": d["clip_id"],
                        "start": d["start"], "end": d["end"],
                        "dense_score": round(float(dense[i]), 4),
                        "description_text": d.get("description_text", "")[:160],
                    })
        report["abstained"] = not report["results"]
        report["answer_status"] = ("found" if report["results"] else
                                   "not_found" if visual_intent else "abstained")
        return report

    def summary(self) -> dict:
        """What is in this unified index: windows, anchors, coverage."""
        payload = self.payload
        return {
            "backend": "unified-eg2",
            "window_count": payload.get("window_count", 0),
            "anchors": payload.get("anchors", []),
            "anchor_dimensions": payload.get("anchor_dimensions", {}),
            "anchor_backends": payload.get("anchor_backends", {}),
            "anchor_floors": {a: f["confident"]
                              for a, f in (payload.get("anchor_floors") or {}).items()},
            "unified_unembedded": payload.get("unified_unembedded", []),
            "coverage": payload.get("coverage", {}),
        }
