"""The top candidate is the one an editor will cut in. Read it properly.

The frame lane says so about every hit it returns:

    every hit below is UNVERIFIED, and absence cannot be concluded from
    this list

That is not a caveat bolted on.  The index samples frames on a fixed
stride - ten seconds for the shipped frame index - and a five-second
window can contain NO sampled frame at all, or one frame that happens to
miss the thing.  A rank computed from that sample is a rank over a
sample, and the editor is about to cut on it.

`verify_candidates` re-reads the top candidates at HIGHER FIDELITY and
reports what the denser read found:

* `confirmed` - the denser read agrees with the index's score.  The
  window holds up under a closer look;
* `weaker` - the denser read scores BELOW the index.  The window was
  ranked on a sample that flattered it;
* `unverifiable` - the closer look could not be taken (no CLIP, no
  media, no frames).  This is reported, never dressed up as a
  confirmation.

**It is a COMPARISON, not a probability.**  The report carries both
scores and the delta.  It does not say "there is a 92% chance this is
the right window", because nothing here measured that, and a number
nobody measured is worse than no number.

**Only the top candidates are verified.**  The verification decodes and
embeds, so verifying every returned window would cost as much as
building the index again.  The count is an argument, and the rest of
the list is reported as skipped rather than quietly dropped.

**The verifier is injectable.**  The orchestration is what is worth
testing; the decoding and the embedding are the shipped machinery's
(`footage_query.extract_chunk_frames` and `footage_frames._load_clip`),
and `frame_verifier` wires them together for the caller that has the
media.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path

CONFIRMED = "confirmed"
WEAKER = "weaker"
UNVERIFIABLE = "unverifiable"

DEFAULT_VERIFY_TOP_K = 3
"""How many top candidates are re-read.  The editor cuts the first one,
so the first three are the ones that matter."""

SCORE_TOLERANCE = 0.02
"""How much the denser read may fall below the index before the window
is called weaker.  CLIP cosines move by this much between neighbouring
frames of the same shot, so a smaller gap is noise rather than
contradiction."""

DENSE_STEP_SECONDS = 1.0
"""The stride of the verification read.  The shipped frame index samples
every ten seconds, so a short window is often unverified by accident;
one second sees the window."""

Verifier = Callable[[Mapping], dict]


def _num(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def verify_candidates(query: str, hits: Sequence[Mapping], *,
                       verify: Verifier, top_k: int = DEFAULT_VERIFY_TOP_K
                       ) -> dict:
    """Re-read the top candidates and report what the denser read found.

    `verify` is called once per candidate and returns the report for
    that one: `status` (`confirmed`, `weaker` or `unverifiable`), the
    scores, and whatever the verifier measured.  A verifier that raises
    is reported as `unverifiable` with the reason - a failure to verify
    is a fact about the verification, not about the window.
    """
    ranked = list(hits or ())
    verified = []
    skipped = []
    for rank, hit in enumerate(ranked, start=1):
        if rank > max(0, top_k):
            skipped.append({
                "rank": rank,
                "segment_id": hit.get("segment_id"),
                "clip_id": hit.get("clip_id"),
                "reason": ("only the top candidates are re-read; verifying "
                           "every window would cost as much as building "
                           "the index again"),
            })
            continue
        try:
            result = verify(hit)
        except Exception as error:  # noqa: BLE001 - a failed verify is data
            result = {"status": UNVERIFIABLE,
                      "reason": f"the closer look failed: {error}"}
        verified.append({
            "rank": rank,
            "segment_id": hit.get("segment_id"),
            "clip_id": hit.get("clip_id"),
            "start": _num(hit.get("start")),
            "end": _num(hit.get("end")),
            "index_score": _num(hit.get("score")),
            **result,
        })
    statuses = [row.get("status") for row in verified]
    return {
        "query": query,
        "verified": verified,
        "skipped": skipped,
        "verified_count": len(verified),
        "skipped_count": len(skipped),
        "confirmed": sum(1 for status in statuses if status == CONFIRMED),
        "weaker": sum(1 for status in statuses if status == WEAKER),
        "unverifiable": sum(1 for status in statuses if status == UNVERIFIABLE),
        "note": ("a comparison of two scores, not a probability; nothing "
                 "here measured the chance that the window is the right one"),
    }


def frame_verifier(project_folder, query: str, *,
                   dense_step_s: float = DENSE_STEP_SECONDS,
                   encode_images=None, encode_text=None) -> Verifier:
    """The verifier for the frame lane: cut the window densely, re-score it.

    It uses the shipped extraction (`footage_query.extract_chunk_frames`)
    and the shipped CLIP loader (`footage_frames._load_clip`), so the
    verification is scored by the same encoder that built the index.
    Without them the verifier says `unverifiable` and why - it never
    guesses.
    """
    from library.tools import footage_analysis
    from library.tools.analysis import footage_frames, footage_query

    sources = {row.get("clip_id"): row.get("path")
               for row in footage_analysis.catalog_sources(project_folder)}
    loaded: dict = {}

    def _encoders():
        """The CLIP encoders, loaded ONCE and only when a window needs them.

        Loading them when the verifier is built would pull torch in for a
        caller that only wanted to know whether the media was reachable,
        and the cheap check (is there a file for this clip?) comes first.
        """
        if "encoders" in loaded:
            return loaded["encoders"]
        if encode_images is not None and encode_text is not None:
            loaded["encoders"] = (encode_images, encode_text)
            return loaded["encoders"]
        images, text, _backend = footage_frames._load_clip()
        loaded["encoders"] = (images, text)
        return loaded["encoders"]

    def verify(hit: Mapping) -> dict:
        clip_id = hit.get("clip_id")
        source_file = sources.get(clip_id)
        if not source_file:
            return {"status": UNVERIFIABLE,
                    "reason": f"no media file is known for {clip_id}"}
        start = _num(hit.get("start"))
        end = _num(hit.get("end"))
        if start is None or end is None:
            return {"status": UNVERIFIABLE,
                    "reason": "the window has no interval to re-read"}
        encode_images, encode_text = _encoders()
        if encode_images is None or encode_text is None:
            return {"status": UNVERIFIABLE,
                    "reason": "no CLIP available to re-score the window"}
        from library.tools.ren_refusal import RenRefusal
        try:
            images = footage_query.extract_chunk_frames(
                source_file, start, end, fps=1.0 / max(dense_step_s, 1e-6))
        except RenRefusal as refused:
            return {"status": UNVERIFIABLE, "reason": refused.render()}
        if not images:
            return {"status": UNVERIFIABLE,
                    "reason": "the window yielded no frames to read"}
        paths = []
        tmpdir = tempfile.mkdtemp(prefix="ren-verify-frames-")
        try:
            for index, image in enumerate(images):
                path = Path(tmpdir) / f"f{index:04d}.png"
                image.save(path)
                paths.append(str(path))
            matrix = encode_images(paths)
            vector = encode_text([query])
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
        if matrix is None or len(matrix) == 0 or vector is None or len(vector) == 0:
            return {"status": UNVERIFIABLE,
                    "reason": "the closer look produced no scores"}
        best = float(max(float(matrix[i] @ vector[0]) for i in range(len(matrix))))
        index_score = _num(hit.get("score"))
        delta = None if index_score is None else round(best - index_score, 4)
        status = CONFIRMED
        if delta is not None and delta < -SCORE_TOLERANCE:
            status = WEAKER
        return {
            "status": status,
            "verification_score": round(best, 4),
            "index_score": index_score,
            "delta": delta,
            "frames_examined": len(paths),
            "stride_seconds": dense_step_s,
        }

    return verify


def verify_frame_hits(project_folder, query: str, hits: Sequence[Mapping], *,
                      top_k: int = DEFAULT_VERIFY_TOP_K,
                      dense_step_s: float = DENSE_STEP_SECONDS) -> dict:
    """Re-read the top frame hits and report the comparison."""
    verify = frame_verifier(project_folder, query, dense_step_s=dense_step_s)
    return verify_candidates(query, hits, verify=verify, top_k=top_k)


def summarise(report: Mapping) -> dict:
    """What the verification found, in one line's worth."""
    if not isinstance(report, Mapping):
        return {"verified": 0}
    return {
        "verified": report.get("verified_count", 0),
        "confirmed": report.get("confirmed", 0),
        "weaker": report.get("weaker", 0),
        "unverifiable": report.get("unverifiable", 0),
        "skipped": report.get("skipped_count", 0),
    }


def candidate_rows(report: Mapping) -> Iterable[Mapping]:
    """The verified rows of a report."""
    return (report.get("verified") or ()) if isinstance(report, Mapping) else ()
