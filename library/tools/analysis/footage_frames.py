"""Frame-level CLIP search beside the text footage index.

Part of the footage-search capability, as `footage_query.py` is: not a
pipeline stage, importing nothing from steps or processes
(`tests/unit/picture/test_footage_query.py` pins that).  Agents and people reach it
through `ren search --visual` and `ren search-index`.

Scope (firstmate decision 2026-10-01 on the vep-clip-frame-search eval,
`data/vep-video-intelligence-entities-and-search/` + `../vep-clip-frame-search/eval/`):
objects, people and scenes.  The eval measured P@5 5/5, 5/5 and 3/5 on
those, but fine-grained actions at chance (mouth-cover 1/5, drinking 0/5 -
CLIP matches the scene and the person and ignores the interaction), so
action and gesture queries are REFUSED here and directed to the event
pipeline (`event_spans`, M7), which measures actions as candidate spans
the VLM verifier disposes instead of ranking pixels.

Ranked candidates only.  The same eval found NO abstention floor that
separates present from absent (absent max 0.191 inside the present band
0.197-0.232; an all-miss query topped every query at 0.232), so this index
never claims absence: every answer is top-k with scores, every hit is
labelled unverified, and the report says absence cannot be concluded.

A floor is here, and it is MEASURED rather than guessed.  The 2026-10-20
editorial retrieval measurement (docs/benchmarks/2026-10-20-editorial-
retrieval-benchmark.md) found a query-level cutoff that separates present
from absent picture queries on the corpus it could measure - present min
0.1652, absent max 0.1447 - and the lane's own window-level calibration
reads 0.22195 (balanced accuracy 0.9565).  The query floor ships ON at
`MEASURED_QUERY_FLOOR` because the lane has to abstain SOMEWHERE: without
it an absent picture is handed to the editor as five wrong windows.  It
is a narrow gate (0.0152 below the weakest present query on that corpus)
and its risk is real - a floor that is too high turns a present moment
into "not found", a silent recall loss - so `floor=None` turns it off,
and the abstention names its near miss rather than going silent.  The
window-level floor is measured too, and ships OFF: it prices the wrong
seconds down but costs recall.  Neither floor ever claims absence - an
abstain says the signal could not resolve the query, not that the subject
is not in the footage.

A range is ONE sighting, and it is bounded by TIME and by SAMPLES: the
merge gap (15 s) says two sightings that close are the same sighting,
and a range holds at most two sampled frames of its source, so it claims
the footage its samples bracket and no more.  Without that rule the
merge chained across the recording and a range claimed minutes of
footage the evidence never covered (measured 2026-10-21 with the
corrected retrieval metric: the merged set's false-positive seconds
went 561.16 -> 1030.13 and the held-out wrong seconds 125.07 -> 204.65
when the pool and the floor shipped without the cap).  The cap is
RE-DERIVED for the deeper pool below, because a deeper pool chains: at
the shipped thirty-second cap the deeper pool grew the held-out set's
false-positive seconds from 267.66 to 437.83 while the recall did not
move.

THE POOL IS DIVERSE, and that is the fix for the deficit the VI-001
attribution named.  A global cut is a cut BY SOURCE SIZE: the editorial
corpus's seven cameras hold 1092 sampled frames between them and the
two close-up cameras the editor wanted hold 23 and 22 of them, so a
global pool of 200 frames is mostly the long cameras' and the wanted
moment is out of the pool before anything ranks it.  Measured on that
corpus (2026-10-21): 99 of the 167 uncredited wanted items of the
held-out set - 59 % - were never IN the candidate pool at all, and the
deficit the attribution reported as `not_in_candidate_pool` was the
POOL's, not the ranking's.

So the pool is the global top `SEARCH_POOL` frames PLUS each source's
own best `PER_SOURCE_POOL` frames: every camera is represented whatever
its length, and the global pool stays on top of it for the sources that
carry the bulk of the footage.  Measured on the same corpus, with the
same scorer and the same index: the held-out `not_in_candidate_pool`
count goes 99 -> 0 and the merged set's 58 -> 0, the held-out visual
recall goes 0.0312 -> 0.0388, the region recall 0.8889 -> 1.0, the
false-positive seconds FALL from 267.66 to 257.65 and the wrong seconds
the editor's first pick costs from 75.02 to 55.00.  The false-positive
WINDOWS go 29 -> 31 - the price of offering five candidates where the
narrow pool could offer three, while the false-positive SECONDS fall.
The full before/after, with the denominators and the latency, is in
`docs/benchmarks/2026-10-21-editorial-retrieval-candidate-pool.md`.

A source may contribute MORE THAN ONE window when the QUERY asks for
more than one occurrence of the thing ("every time the laptop is on
the desk"); one window per source is the rule, and it is the right
rule for a query that asks for one shot.  The second window must be a
different event - the editor asked for the occurrences, not for one
sighting shown twice.

Frames come from the per-source memory's shared I-frame sample (M2,
`library/tools/source_memory.py`), never from a decode of its own: the
long-footage memory scout (§2.2, §2.5) measured one shared 2 Hz
I-frame pass at 4.7 min per 82-minute file against the per-clip `-ss`
seek extraction this module used to run itself.  A clip with no fresh
M2 sample is SKIPPED with the build command to run, the same shape
`footage_segments` uses for an absent M1 transcript - this index reads
the memory, it does not build it.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

from library.tools.ren_refusal import RenRefusal

import numpy as np

if __package__ in (None, ""):  # direct `python3 footage_frames.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from library.tools import source_memory
from library.tools.analysis import footage_segments
from library.tools.analysis.footage_query import INDEX_SUBDIR

CLIP_MODEL = "openai/clip-vit-large-patch14"

# Sampling geometry for CLIP embedding, as measured in the eval: 1 frame
# per 10 s embeds at ~172 ms/frame CPU batched, so a full 82-minute
# episode indexes in ~85 s.  Denser sampling is the caller's choice, not
# a default: the eval showed density does not fix action resolution.
# This governs ONLY which M2 frames are picked for embedding - the
# decode that produces them runs once per source at M2's own 2 Hz,
# never per search-index build.
FRAME_STEP_S = 10.0
EMBED_BATCH = 16

# Adjacent frame hits merge into one time range when they are this close.
# One and a half sampling steps at the index's 10 s cadence: two sightings
# that close are the same sighting, and two genuinely separate sightings
# stay separate.
MERGE_GAP_S = 15.0

# The longest run of sightings that is still ONE range.  The merge gap
# above is a TOLERANCE for one missed extraction - it says two sightings
# this close are the same sighting - so a run of sightings that spans
# more than the gap is not one sighting any more, however close its
# neighbours are.  Without this the chain walked the whole recording: a
# pool of 200 merged hits 30 s apart into ranges of minutes, and a range
# that claims more time than the evidence carries is charged for it
# (measured 2026-10-21 with the corrected retrieval metric: the ranges
# grew the false-positive seconds of the merged set from 561.16 to
# 1030.13 and the held-out wrong seconds from 125.07 to 204.65).
#
# This is RE-DERIVED for the deeper candidate pool (2026-10-21).  The
# pool is deeper, so a source's samples are dense enough to CHAIN: at the
# shipped thirty-second cap a deeper pool grew the held-out set's false-
# positive seconds from the shipped 267.66 to 437.83 while the wrong
# seconds the editor's first pick costs stayed at 75.02 - the pool
# offered MORE footage the editor did not ask for and the recall did not
# move.  At this cap the same pool measures the false-positive seconds at
# 257.65 - BELOW the shipped 267.66 - and the wrong seconds at 55.00,
# with the recall unmoved (0.0388) and the region recall at 1.0.
MAX_RANGE_S = MERGE_GAP_S

# How many SAMPLED FRAMES of one source a range may hold.  A range is
# ONE sighting, and a sighting is what the samples of a source show: two
# samples ten seconds apart bracket ten seconds of footage, three bracket
# twenty.  A cap on the SAMPLES rather than only on the seconds is what
# makes a range's length the same whatever the source's sampling phase:
# at the time cap alone a chain of three samples broke on the chain's
# parity, and the wanted window of H01 and H02 became a single instant -
# which the credit rule cannot credit at all.
#
# Two is MEASURED on the editorial retrieval corpus with the deeper
# candidate pool (2026-10-21): with the sample cap the recall is 0.0388
# and the region recall 1.0 at every time cap measured, and without it
# the recall falls to 0.0231 at a twenty-second cap - the wanted window
# loses its length and its region is not found.
RANGE_SAMPLES = 2

# How many top frames the range merger reads.  Ranges are for a person to
# scrub, not for exhaustive recall - forty frames at 10 s spacing cover
# almost seven minutes of sightings per query, but they are also 3.7 % of
# a 1092-frame index, and the frames the editor wants are simply RANKED
# BELOW them: measured on the editorial retrieval corpus, the wanted
# frames of a present query sit at a median rank of 396-696 of 1092, so
# a pool of 40 could not see them at all.  Raising the pool to 200
# (measured: held-out macro recall 0.1095 -> 0.4316) costs one matmul
# over 1092x768 - negligible - and the merge is O(pool).
SEARCH_POOL = 200

# How many candidates EACH SOURCE contributes to the pool, whatever the
# source's size.  A global cut is a cut BY SOURCE SIZE: the corpus's
# seven cameras hold 1092 sampled frames between them, and the two
# close-up cameras the editor actually wants hold 23 and 22 of them, so
# a global pool of 200 frames is mostly the long cameras' and the wanted
# moment is out before anything ranks it (measured 2026-10-21 on the
# editorial retrieval corpus: 99 of the 167 uncredited wanted items of
# the held-out set - 59 % - were never IN the candidate pool at all).
#
# The quota is per SOURCE, so every camera is represented whatever its
# length, and the global pool stays on top of it for the sources that
# carry the bulk of the footage.  30 is MEASURED: the wanted spans of
# that corpus reach 220 s, which is 22 sampled frames at the index's
# 10 s cadence, so a quota below that leaves wanted frames outside the
# pool - at 20 frames the corpus still had 20 wanted items outside it,
# at 30 it has NONE (0 of 172 wanted items, measured).  The pool costs
# 402 candidates instead of 200, and the price is one matmul over
# 1092x768 either way plus an O(pool) merge.
PER_SOURCE_POOL = 30

# The measured cutoffs.  A query-level floor abstains on the whole query
# when its best frame scores below it; a window-level floor drops a
# WINDOW below it.  Both numbers come from the 2026-10-20 editorial
# retrieval measurement on one corpus: present queries' top-1 scores run
# 0.1652-0.2724, absent ones 0.1237-0.1447, and the lane's own
# calibration puts the window-level cutoff at 0.22195 (balanced accuracy
# 0.9565).
#
# The QUERY floor is the one that ships ON, because the lane has to
# abstain somewhere: without it an absent picture is handed to the
# editor as five wrong windows (measured: 10 of the 41 false-positive
# windows and 44 % of the false-positive seconds of the merged set).  It
# is a narrow gate - the weakest present query on the corpus scores
# 0.1652, so the floor sits 0.0152 below it - and the risk is stated:
# a floor that is too high turns a present moment into "not found",
# which is a silent recall loss.  `floor=None` turns it off.
#
# The WINDOW floor ships OFF: measured, it prices the wrong seconds well
# down (merged 93.01 -> 22.94) but it costs recall (held-out 0.4316 ->
# 0.3371), because it drops present windows whose best frame scores low.
MEASURED_QUERY_FLOOR = 0.15
MEASURED_WINDOW_FLOOR = 0.22195

FRAME_INDEX_FILE = "frame_index.json"
FRAME_EMBEDDINGS_FILE = "frame_embeddings.npy"

# What this index answers, and the notice every answer carries.  "Cannot be
# concluded" is the whole of (iii): a ranking cannot say "not here".
SCOPE = "objects, people and scenes"
UNVERIFIED_NOTICE = (
    "Ranked candidates, not verified finds: every hit below is UNVERIFIED, "
    "and absence cannot be concluded from this list - a candidate is a "
    "place to look, not a find."
)
# What an answer carries when a floor WAS given and the query fell below
# it.  It still does not claim absence: it says the lane abstained
# because nothing in the pool cleared the floor.
FLOOR_NOTICE = (
    "Abstained below the floor: nothing in the candidate pool scored high "
    "enough to stand behind, so no candidate is offered. This is not a "
    "claim that the subject is absent from the footage - it is a claim "
    "that this signal could not resolve the query."
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
    # "pointing" matches the GESTURE, not the noun.  A bare "point" is
    # overwhelmingly a noun, and it appeared in two of the sixteen
    # committed editorial queries as one: "to sit under the pricing
    # point" (E09) and "GEO just SEO two point oh" (E08).  Both are
    # picture/text queries about something else, and the refusal cost
    # E09 every window it could have returned (measured 2026-10-20, see
    # docs/benchmarks/2026-10-20-editorial-retrieval-benchmark.md).  The
    # verb forms and the "points at" reading still refuse.
    ("pointing", r"\bpoint(?:ing|ed)\b|\bpoints?\s+at\b"),
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


def event_pipeline_pointer() -> str:
    """The event pipeline's candidate predicates, read from it never copied.

    `event_spans` (M7) is the lane that measures actions and gestures: a
    geometric candidate rule over M3 hand/face geometry proposes spans and
    the local VLM verifier disposes.  Imported lazily so the ranking path
    pays nothing for a pointer it never renders, and the list itself is
    read from the pipeline so a predicate added there reaches the refusal
    without a second enumeration to keep stale.
    """
    from library.tools import event_spans

    return ", ".join(event_spans.CANDIDATE_PREDICATES)


def _repeated_events(query: str) -> int:
    """How many occurrences of the subject the query asks for.

    ONE by default.  Two when the query names MORE THAN ONE occurrence -
    "every time the laptop is on the desk", "the laptop, twice" - because
    a rule that gives one window per source answers such a query with a
    single sighting and calls it the whole thing.

    A query that names ONE occurrence - "the second time she opens the
    box", "again", "once more" - asks for ONE window: WHICH occurrence is
    a RANKING question, and the ranking lane owns it.  This is not that
    question, so the ordinal reading is not taken here.
    """
    from library.tools.retrieval.requirements import repeated_events

    return repeated_events(query)


def _is_a_different_event(entry: dict, picked: list, clip_id: str,
                          merge_gap_s: float) -> bool:
    """Is this window a DIFFERENT event from the ones already taken?

    The merge gap is the tolerance for one missed extraction, so two
    windows of one source further apart than the gap are two sightings
    and two windows of one sighting shown twice is one window.
    """
    for other in picked:
        if other.get("clip_id") != clip_id:
            continue
        gap = max(float(entry.get("start") or 0.0) - float(other.get("end") or 0.0),
                  float(other.get("start") or 0.0) - float(entry.get("end") or 0.0))
        if gap > merge_gap_s:
            return True
    return False


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


def _select_m2_frames(m2_frames: list, step_s: float) -> list:
    """The M2 record's dense ~2 Hz frames, thinned to one per `step_s`.

    M2 is read-only and shared across every per-frame consumer, so this
    index keeps its own embedding cadence by picking the nearest
    available M2 frame to each `step_s` mark rather than embedding
    every dense sample (that would multiply the CLIP cost ~17x for no
    benefit the eval measured). Pure and ffmpeg-free: no decode happens
    here, ever - `source_memory.build_frames` is the only thing that
    decodes.
    """
    ordered = sorted(m2_frames, key=lambda f: f["t"])
    if not ordered:
        return []
    duration = ordered[-1]["t"]
    selected, idx, n = [], 0, len(ordered)
    t = 0.0
    while t <= duration + 1e-6:
        while idx + 1 < n and abs(ordered[idx + 1]["t"] - t) <= abs(ordered[idx]["t"] - t):
            idx += 1
        if not selected or selected[-1]["t"] != ordered[idx]["t"]:
            selected.append(ordered[idx])
        t += step_s
    return selected


@source_memory._project_scoped
def build_frame_index(project_folder, index_dir=None,
                      step_s: float = FRAME_STEP_S, verbose=False) -> dict:
    """Embed a `step_s`-spaced thinning of every clip's M2 sample.

    Reads the project's catalog and the per-source memory's M2 frame
    sample (`source_memory.read_m2_for_clip`); writes ONLY into
    `index_dir`, which defaults to the project's scratch area beside
    the text index.  A clip with no fresh M2 sample is SKIPPED with
    the build command to run, never fatal - one unbuilt source must
    not take down the other six clips' index.  No ffmpeg call happens
    in this function; the decode already happened once, per source, in
    `source_memory.build_frames`.
    """
    from library.tools.analysis.footage_query import index_dir_for

    started = time.perf_counter()
    target = Path(index_dir) if index_dir else index_dir_for(project_folder)
    target.mkdir(parents=True, exist_ok=True)

    catalog = footage_segments.load_catalog(project_folder)
    recorded = source_memory.load_recorded_fingerprints(project_folder)
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
        digest, basis = source_memory.digest_for_clip(project_folder, clip, recorded)
        if digest is None:
            skipped.append({"clip_id": clip_id,
                            "reason": "no content digest (file unreadable)"})
            continue
        m2 = source_memory.read_m2(digest)
        if (m2 is None
                or not source_memory.m2_frames_present(digest, m2)
                or (basis == "live" and not source_memory.is_fresh(m2, src))):
            skipped.append({
                "clip_id": clip_id,
                "reason": ("no fresh frame sample (M2); build it with "
                          f"`python3 -m library.tools.source_memory frames "
                          f"{project_folder!r}`"),
            })
            continue
        for frame in _select_m2_frames(m2.get("frames") or [], step_s):
            frames.append({
                "file": source_memory.frame_abspath(digest, frame),
                "clip_id": clip_id,
                "filename": clip.get("filename", ""),
                "t": round(float(frame["t"]), 3),
            })
        sources.append({"clip_id": clip_id, "path": str(src),
                        "size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns})
    select_seconds = time.perf_counter() - t0

    encode_images, _, backend = _load_clip()
    embed_seconds, dimension = 0.0, 0
    if encode_images is not None and frames:
        t0 = time.perf_counter()
        matrix = encode_images([f["file"] for f in frames])
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
        "scope": SCOPE,
        "built_at": time.time(),
        # What this index was built FROM.  Frames reference the per-source
        # memory's M2 sample (absolute paths, never copied), but staleness
        # still compares the video files' own size/mtime - a replaced MXF
        # must read as a moved index even though M2 would also notice.
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
        "select_seconds": round(select_seconds, 1),
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
    only wants staleness never pays for the CLIP weights.  `search_frames`
    always returns the top-k with their scores, each labelled unverified;
    `search_ranges` adds the measured query floor (on by default, off
    with `floor=None`).
    """

    def __init__(self, project_folder, index_dir=None):
        from library.tools.analysis.footage_query import index_dir_for

        self.project_folder = str(project_folder)
        self.index_dir = Path(index_dir) if index_dir else index_dir_for(project_folder)
        self._payload = None
        self._matrix = None
        self._clip = None
        self._by_clip = None

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
                      include_actions: bool = False,
                      per_source_pool: int | None = None) -> dict:
        """Rank sampled frames against a query.  Always top-k, never empty
        by judgement - an empty `results` means the index itself is empty,
        and `refused` means the query is out of scope (see below).

        Action and gesture queries ("covering the mouth", "drinking",
        "gesturing"...) are refused: the eval measured CLIP at chance on
        them, and ranking chance reads as an answer.  They belong to the
        event pipeline (`event_spans`, M7), whose candidate predicates the
        refusal names.  `include_actions` overrides, for the caller who
        wants the ranking anyway and says so.

        `per_source_pool` widens the selection from a plain top-k to a
        CANDIDATE POOL: the global top-k plus each source's own best
        frames.  A global cut alone is a cut by source size, and the
        wanted moment of a short camera is out of the pool before
        anything ranks it (see `PER_SOURCE_POOL`).
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
            # CLIP's held-out score distributions overlap, so a ranked
            # candidate is never evidence enough to call a find or a miss.
            "answer_status": "not_analyzed",
        }
        matched = action_refusal_match(query)
        if matched is not None and not include_actions:
            report["refused"] = {
                "matched": matched,
                "reason": ("frame search covers objects, people and scenes; "
                           f"{matched!r} is an action/gesture query, which this "
                           "signal does not resolve (measured at chance)"),
                "hint": ("the event pipeline measures actions and gestures: "
                         "ren search <project> --person <name> "
                         f"--predicate <predicate> --verify \"<what you see>\" "
                         f"(candidate predicates: {event_pipeline_pointer()}); "
                         "or retry with --include-actions to rank anyway"),
            }
            report["answer_status"] = "not_analyzed"
            return report
        if not self.frames or self.matrix is None:
            report["error"] = ("this frame index holds no embeddings; "
                               "build it with `ren search-index <project> --frames`")
            report["answer_status"] = "not_analyzed"
            return report
        vector = self._encode_text(query)
        if vector is None:
            report["error"] = "CLIP is not available on this machine"
            report["answer_status"] = "not_analyzed"
            return report
        scores = self.matrix @ vector
        if per_source_pool is None:
            order = np.argsort(-scores)[:max(0, top_k)]
            report["search_pool"] = {
                "global_frames": max(0, top_k),
                "per_source_frames": 0,
                "candidates": len(order),
                "sources": len({self.frames[int(i)]["clip_id"]
                                for i in order}),
            }
        else:
            order = self._candidate_pool(scores, max(0, top_k),
                                          per_source_pool)
            report["search_pool"] = {
                "global_frames": max(0, top_k),
                "per_source_frames": max(0, per_source_pool),
                "candidates": len(order),
                "sources": len({self.frames[int(i)]["clip_id"]
                                for i in order}),
            }
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
        report["answer_status"] = "insufficient_evidence"
        return report

    def _candidate_pool(self, scores, global_frames: int,
                        per_source_frames: int) -> np.ndarray:
        """The candidate frames: the global top-N plus each source's best K.

        A global cut is a cut BY SOURCE SIZE.  The corpus's seven cameras
        hold 1092 sampled frames between them and the two the editor
        wants hold 23 and 22 of them, so a global pool of 200 is mostly
        the long cameras' and the wanted moment never reaches the
        ranking (measured 2026-10-21: 99 of the 167 uncredited wanted
        items of the held-out set were never IN the pool).

        The quota is per source, so every camera is represented whatever
        its length, and the global pool stays on top of it for the
        sources that carry the bulk of the footage.  The result is
        ordered by score, so the best candidates still come first.

        The cost is measured on that corpus: building the pool of 317
        candidates costs 0.145 ms at the least disturbed and 0.879 ms at
        the median on a shared machine, against the shipped pool's
        0.009-0.016 ms - and a CLIP text encode, the same query's other
        cost, is two orders of magnitude dearer.
        """
        picked = {int(i) for i in np.argsort(-scores)[:max(0, global_frames)]}
        if per_source_frames > 0:
            for indices in self._frames_by_clip().values():
                if len(indices) <= per_source_frames:
                    picked.update(int(i) for i in indices)
                    continue
                local = scores[indices]
                best = np.argpartition(-local, per_source_frames)[
                    :per_source_frames]
                picked.update(int(indices[int(i)]) for i in best)
        return np.array(sorted(picked, key=lambda i: -scores[i]), dtype=int)

    def _frames_by_clip(self) -> dict:
        """The frame indices of each clip, cached for the pool builder.

        The index is read-only once built, so the grouping is built once
        and reused by every query - the pool is rebuilt per query, the
        grouping is not.
        """
        if self._by_clip is None:
            by_clip: dict = {}
            for index, frame in enumerate(self.frames):
                by_clip.setdefault(frame["clip_id"], []).append(index)
            self._by_clip = {
                clip: np.array(indices, dtype=int)
                for clip, indices in by_clip.items()}
        return self._by_clip

    def search_ranges(self, query: str, top_ranges: int = 5,
                      pool: int = SEARCH_POOL,
                      merge_gap_s: float = MERGE_GAP_S,
                      include_actions: bool = False,
                      floor: float | None = MEASURED_QUERY_FLOOR,
                      window_floor: float | None = None,
                      max_range_s: float | None = MAX_RANGE_S,
                      max_range_frames: int | None = RANGE_SAMPLES,
                      per_source_pool: int | None = PER_SOURCE_POOL,
                      repeated_events: int | None = None) -> dict:
        """Top frame hits merged into time ranges per clip.

        The range is the unit a person scrubs: adjacent pool hits on one
        clip within `merge_gap_s` become one `{start, end}` span ranked by
        its best frame score.  Same unverified terms as `search_frames`.

        A range is ONE sighting, and it is bounded twice: by TIME
        (`max_range_s`, the merge gap - two sightings that close are the
        same sighting, and a run of them that spans more than the gap is
        not one sighting any more) and by SAMPLES (`max_range_frames`,
        the frames of one source a range may hold).  `max_range_s=None`
        turns the time cap off and `max_range_frames=None` turns the
        sample cap off.

        The candidate pool is DIVERSE: the global top `pool` frames plus
        each source's own best `per_source_pool` frames, so a camera is
        represented whatever its length (see `PER_SOURCE_POOL`).

        `floor` is the QUERY-level cutoff: when the best frame in the
        whole pool scores below it, the lane abstains instead of ranking
        noise - which is what makes an absent picture abstain rather
        than return five wrong windows.  It defaults to the measured
        cutoff (`MEASURED_QUERY_FLOOR`); `floor=None` turns it off.
        `window_floor` is the WINDOW-level cutoff: a window whose best
        frame scores below it is dropped, and the query keeps the windows
        that clear it.  It is measured (`MEASURED_WINDOW_FLOOR`) and it
        ships OFF, because measured it costs recall.

        The returned windows are DIVERSIFIED across clips: at most ONE
        range per clip, in rank order.  Five windows from one camera
        starve the other assets - the editor is shown the same angle
        five times - so the slots are filled with the other cameras'
        best ranges, and a clip with no range of its own does not appear
        twice.

        A source may take a SECOND window when the QUERY asks for more
        than one occurrence of the thing ("every time the laptop is on
        the desk"): then the second window must be a different event,
        separated by more than the merge gap, because the editor asked
        for the occurrences and not for one sighting twice.  The query
        decides, not the score - `repeated_events` overrides the reading.
        """
        report = self.search_frames(query, top_k=pool,
                                    include_actions=include_actions,
                                    per_source_pool=per_source_pool)
        repeats = (repeated_events if repeated_events is not None
                   else _repeated_events(query))
        report["search_policy"] = {
            "candidate_pool_size": max(0, pool),
            "per_source_pool_size": (max(0, per_source_pool)
                                     if per_source_pool is not None else 0),
            "candidates_considered": (report.get("search_pool") or {}).get(
                "candidates"),
            "sources_considered": (report.get("search_pool") or {}).get(
                "sources"),
            "merge_gap_seconds": float(merge_gap_s),
            "top_interval_limit": max(0, top_ranges),
            "query_floor": floor,
            "window_floor": window_floor,
            "max_range_seconds": max_range_s,
            "max_range_frames": max_range_frames,
            "windows_per_source": max(1, repeats),
        }
        report["abstained"] = False
        if report["refused"] is not None or report.get("error"):
            report["ranges"] = []
            return report
        best_score = max((hit["score"] for hit in report["results"]),
                         default=None)
        if floor is not None and best_score is not None and best_score < floor:
            report["ranges"] = []
            report["abstained"] = True
            report["answer_status"] = "abstained"
            report["abstention_reason"] = (
                f"the best frame in the pool scored {best_score:.4f}, below "
                f"the floor {floor}: the lane abstains rather than rank a "
                "candidate it cannot stand behind")
            # An abstain that cannot name its near miss reads as a broken
            # search: the reviewer needs to see what came closest.
            report["near_miss"] = report["results"][0] if report["results"] else None
            report["notice"] = FLOOR_NOTICE
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
                if (hit["t"] - current_end <= merge_gap_s
                        and (max_range_s is None
                             or hit["t"] - start <= max_range_s)
                        and (max_range_frames is None
                             or len(members) < max_range_frames)):
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
        if window_floor is not None:
            ranges = [r for r in ranges if r["best_score"] >= window_floor]
        report["ranges"] = self._select_windows(ranges, top_ranges,
                                                repeats, merge_gap_s)
        return report

    @staticmethod
    def _select_windows(ranges: list, top_ranges: int, per_source: int,
                         merge_gap_s: float = MERGE_GAP_S) -> list:
        """The windows the editor is shown, in rank order.

        ONE window per source by default: five windows of one camera is
        the same angle five times, and the other assets are never shown
        (measured 2026-10-21, PR 3022).  So each source contributes its
        single best range, and the slots are filled with the other
        sources' best ranges.

        When the query asks for MORE THAN ONE occurrence of the thing,
        a source may contribute a second window - and that window must be
        a DIFFERENT event, more than the merge gap away from the first,
        because "every time the laptop is on the desk" is answered by the
        occurrences and not by one sighting shown twice.
        """
        limit = max(0, top_ranges)
        cap = max(1, per_source)
        picked, seen = [], {}
        for entry in ranges:
            clip_id = entry["clip_id"]
            taken = seen.get(clip_id, 0)
            if taken >= cap:
                continue
            if taken and not _is_a_different_event(entry, picked, clip_id,
                                                   merge_gap_s):
                continue
            seen[clip_id] = taken + 1
            picked.append(entry)
            if len(picked) >= limit:
                break
        return picked

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
                        "and will refuse; they belong to the event pipeline "
                        "(ren search --person <name> --predicate <predicate>)."
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
