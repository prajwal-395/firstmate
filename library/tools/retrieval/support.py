"""A result that cannot be checked is a claim, not evidence.

A hit says "clip_002, 41.2 s to 48.9 s".  That is enough for a program
and not enough for a person: which words, which frames, which file, and
which version of that file.  An editor who has to open the project and
hunt for the second to decide whether a window is right will trust the
search less, and will trust it wrongly when they are in a hurry.

`supporting_evidence` attaches the three references a result needs to be
CHECKED:

* the **interval** - the exact source seconds, with the duration, so the
  window can be cut without re-deriving it;
* the **frames** - the cached stills inside the window, with their paths,
  so the picture can be LOOKED at rather than believed;
* the **source reference** - the content digest and the file it came
  from, so the result is pinned to the bytes it was measured from, not
  to a path that may have been replaced.

**The digest is the identity, not the path.**  The same footage at two
paths is the same footage; footage at one path that changed is not.  The
digest is what makes a result reproducible, and a result that cannot say
which bytes it came from cannot be reproduced.

**Nothing here measures anything.**  Every field is a reference to
something already measured; this module only says where it is.  A
window with no frames attached says so in `frames_not_found` rather than
carrying an empty list that reads like "there are none".
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence


def _num(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _timecode(seconds: float) -> str:
    minutes, rest = divmod(seconds, 60)
    return f"{int(minutes):02d}:{rest:06.3f}"


def frames_in_window(frames: Iterable[Mapping], clip_id: str,
                     start: float, end: float) -> list[dict]:
    """The cached stills of one clip that fall inside a window."""
    out = []
    for frame in frames or ():
        if not isinstance(frame, Mapping):
            continue
        if frame.get("clip_id") != clip_id:
            continue
        t = _num(frame.get("t"))
        if t is None:
            continue
        if start - 1e-6 <= t <= end + 1e-6:
            out.append({
                "t": round(t, 3),
                "timecode": _timecode(t),
                "path": frame.get("file"),
                "filename": frame.get("filename"),
            })
    out.sort(key=lambda frame: frame["t"])
    return out


def supporting_evidence(hit: Mapping, *, source_digest: str | None = None,
                         file: str | None = None,
                         frames: Iterable[Mapping] = ()) -> dict:
    """The references that let somebody CHECK one hit.

    `frames` is the frame index's own frame list; the frames inside the
    hit's window are attached with their paths.  Pass none and the report
    says the window has no stills attached, which is a different fact
    from "there are no stills".
    """
    clip_id = hit.get("clip_id")
    start = _num(hit.get("start"))
    end = _num(hit.get("end"))
    if start is not None and end is not None and end < start:
        start, end = end, start

    interval = {
        "clip_id": clip_id,
        "start_s": start,
        "end_s": end,
        "duration_s": round(end - start, 3) if (start is not None
                                               and end is not None) else None,
    }
    source = {
        "clip_id": clip_id,
        "source_digest": source_digest,
        "file": file or hit.get("filename") or hit.get("source_file"),
        "filename": hit.get("filename"),
    }
    attached: list[dict] = []
    if frames and clip_id and start is not None and end is not None:
        attached = frames_in_window(frames, str(clip_id), start, end)

    words = []
    for word in hit.get("word_hits") or []:
        if not isinstance(word, Mapping):
            continue
        words.append({
            "word": word.get("word"),
            "start": _num(word.get("start")),
            "end": _num(word.get("end")),
        })

    refs = []
    if source_digest:
        refs.append(f"source:{source_digest}")
    if clip_id:
        refs.append(f"clip:{clip_id}")
    if hit.get("segment_id"):
        refs.append(f"segment:{hit['segment_id']}")

    report = {
        "interval": interval,
        "source": source,
        "frames": attached,
        "words": words,
        "evidence_refs": refs,
    }
    if not attached:
        report["frames_not_found"] = (
            "no cached still falls inside this window; the interval and the "
            "source reference are the whole of the support")
    return report


def attach_support(hits: Iterable[Mapping], *, sources: Mapping | None = None,
                   frames: Iterable[Mapping] = ()) -> list[dict]:
    """Attach supporting evidence to every hit, in place of the bare row."""
    sources = sources or {}
    out = []
    for hit in hits or ():
        if not isinstance(hit, Mapping):
            continue
        source = sources.get(hit.get("clip_id")) or {}
        row = dict(hit)
        row["support"] = supporting_evidence(
            hit,
            source_digest=source.get("source_digest"),
            file=source.get("file"),
            frames=frames)
        out.append(row)
    return out


def support_summary(hits: Sequence[Mapping]) -> dict:
    """What the attached support covers, across a result set."""
    total = len(hits or ())
    with_frames = 0
    with_words = 0
    with_digest = 0
    for hit in hits or ():
        support = hit.get("support") if isinstance(hit, Mapping) else None
        if not isinstance(support, Mapping):
            continue
        if support.get("frames"):
            with_frames += 1
        if support.get("words"):
            with_words += 1
        if (support.get("source") or {}).get("source_digest"):
            with_digest += 1
    return {
        "hits": total,
        "with_frames": with_frames,
        "with_word_timings": with_words,
        "with_source_digest": with_digest,
        "fully_supported": sum(
            1 for hit in hits or ()
            if isinstance(hit, Mapping)
            and isinstance(hit.get("support"), Mapping)
            and hit["support"].get("frames")
            and (hit["support"].get("source") or {}).get("source_digest")),
    }
