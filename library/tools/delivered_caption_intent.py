"""What the captions of ONE timeline were supposed to be.

The plan side of the delivered-caption check
(`library/tools/delivered_caption_spec.py`): the intent is what the
caption was supposed to say and where it was supposed to be, and every
MEASUREMENT that check makes is taken from the delivered file.

Where the intent comes from
---------------------------
The built timeline's own snapshot - the build's account of itself, on
disk (`pipeline_output/review/<timeline>.timeline.json`, read through
`export_verification.load_built`). Its `Subtitles` row says which
caption card was placed, when it played and where it was drawn; the
card's own props beside it (`<stem>_props.json`) say what the card
says, word by word, and which words are emphasised.

That is the whole plan side, and it is per TIMELINE: a reel's snapshot
describes that reel, so a reel's captions are the reel's own and not
the project's.

What it will not do
-------------------
It will not guess. A timeline with no snapshot, a row with no caption
cards, a card with no readable props, a card whose file cannot be
decoded - each of those yields NO intent and says so in
`CaptionPlan.reason`. An absent intent is an absence: the check then
has nothing to compare the delivered file against, and it says that
rather than passing a file it could not judge.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from library.tools.delivered_caption_spec import CaptionIntent

#: The row the build lays the caption cards on. The engine's own name
#: for it (`library/tools/caption_width.py`, `CAPTION_ROW`).
CAPTION_ROW = "Subtitles"


@dataclass(frozen=True)
class CaptionPlan:
    """The captions one timeline declared, and what else it declared.

    `intents` are the captions. `other_spans` are the windows where the
    plan declares SOME OTHER graphic - a lower third, a watermark, an
    end card - so that ink in those windows is not mistaken for an
    undeclared caption.

    `attributable` says the plan could be read for THIS timeline. A
    plan that could not be attributed leaves the check with nothing to
    compare against, and the check says so rather than passing.
    """
    intents: Tuple[CaptionIntent, ...] = ()
    other_spans: Tuple[Tuple[float, float], ...] = ()
    attributable: bool = False
    reason: str = ""
    source: str = ""

    def as_dict(self) -> dict:
        return {
            "intents": len(self.intents),
            "other_spans": len(self.other_spans),
            "attributable": bool(self.attributable),
            "reason": self.reason,
            "source": self.source,
        }


def caption_plan(project_folder: str, timeline_name: str) -> CaptionPlan:
    """The captions the built timeline of `timeline_name` declared.

    Never raises: an unreadable plan is an absence with a reason.
    """
    from library.tools import export_verification

    built = export_verification.load_built(project_folder, timeline_name)
    if not built:
        return CaptionPlan(
            reason=(f"the built timeline {timeline_name!r} has no snapshot "
                    "on disk, so its captions are unknown"))
    fps = export_verification._timeline_fps(None, built)
    rows = export_verification._built_rows(built)
    caption_clips: List[dict] = []
    other_spans: List[Tuple[float, float]] = []
    for row in rows:
        name = str(row.get("name") or "")
        is_caption_row = name.strip().lower() == CAPTION_ROW.lower()
        for clip in row.get("clips") or ():
            if is_caption_row:
                caption_clips.append(clip)
                continue
            span = _clip_span(clip, fps)
            if span is not None:
                other_spans.append(span)
    if not caption_clips:
        return CaptionPlan(
            other_spans=tuple(other_spans),
            attributable=True,
            reason=(f"the built timeline {timeline_name!r} declares no "
                    "caption card"),
            source="timeline-snapshot")
    intents: List[CaptionIntent] = []
    for clip in caption_clips:
        intents.extend(_intents_from_clip(clip, timeline_name, fps))
    return CaptionPlan(
        intents=tuple(intents),
        other_spans=tuple(other_spans),
        attributable=True,
        reason="" if intents else (
            "the caption cards on this timeline carry no readable props, so "
            "their words are unknown"),
        source="timeline-snapshot")


def _clip_span(clip: dict, fps: float) -> Optional[Tuple[float, float]]:
    """A clip's timeline window in seconds, or None when it states none.

    The built snapshot records a clip's place on the timeline as
    `record_in`/`record_out`, in FRAMES; the same rule the export
    verification reads a built span by
    (`export_verification._built_span`).
    """
    start = clip.get("record_in")
    end = clip.get("record_out")
    if start is None or end is None or not fps:
        return None
    try:
        start, end = float(start), float(end)
    except (TypeError, ValueError):
        return None
    if end <= start:
        return None
    return (start / fps, end / fps)


def _props_for(card_path: str) -> Optional[dict]:
    """The render props written beside a caption card, or None.

    The convention is the engine's own (`caption_width._props`,
    `edit_execution_adapters._caption_style_props`): `<stem>_props.json`
    beside the card the step rendered.
    """
    stem = str(card_path).removesuffix(".mov").removesuffix(".mp4")
    path = stem + "_props.json"
    try:
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError):
        return None
    return document if isinstance(document, dict) else None


def _intents_from_clip(clip: dict, timeline_name: str,
                       fps: float) -> List[CaptionIntent]:
    """The intents one placed caption card carries.

    A card may hold SEVERAL captions over its span (the cue list); each
    cue is one intent, placed where the cue's own frames say.
    """
    card_path = str(clip.get("file_path") or clip.get("source_file") or "")
    if not card_path:
        return []
    props = _props_for(card_path)
    if not props:
        return []
    cues = props.get("subtitles") or []
    if not isinstance(cues, list) or not cues:
        return []
    card_fps = float(props.get("fps") or 0.0) or fps
    span = _clip_span(clip, fps)
    if span is None:
        return []
    clip_start = span[0]
    source_in = float(clip.get("source_in") or 0.0)
    geometry = _card_geometry(card_path, props)
    out: List[CaptionIntent] = []
    for index, cue in enumerate(cues):
        if not isinstance(cue, dict):
            continue
        text = str(cue.get("text") or "").strip()
        if not text:
            continue
        try:
            first = float(cue.get("startFrame"))
            last = float(cue.get("endFrame"))
        except (TypeError, ValueError):
            continue
        start = clip_start + (first - source_in) / card_fps
        end = clip_start + (last - source_in) / card_fps
        if end <= start:
            continue
        label = os.path.basename(card_path)
        ink_height, lines = geometry.get(("cue", int(first)), (None, None))
        out.append(CaptionIntent(
            label=f"{label}#{index}",
            text=text,
            start=start,
            end=end,
            lines=lines,
            ink_height=ink_height,
            emphasis=_emphasis_windows(cue, clip_start, source_in, card_fps),
        ))
    return out


def _emphasis_windows(cue: dict, clip_start: float, source_in: float,
                      fps: float) -> Tuple[Tuple[str, float, float], ...]:
    """The windows the card's own emphasis words own.

    The words come from the card's props, so the emphasis the plan
    declared is the emphasis the card was drawn with - never a guess
    from the words themselves.
    """
    wanted = {str(word).strip().lower()
              for word in (cue.get("emphasisWords") or []) if str(word).strip()}
    if not wanted:
        return ()
    windows: List[Tuple[str, float, float]] = []
    for word in cue.get("words") or ():
        if not isinstance(word, dict):
            continue
        text = str(word.get("word") or "").strip().lower()
        if text not in wanted:
            continue
        try:
            first = float(word.get("startFrame"))
            last = float(word.get("endFrame"))
        except (TypeError, ValueError):
            continue
        windows.append((text,
                        clip_start + (first - source_in) / fps,
                        clip_start + (last - source_in) / fps))
    return tuple(windows)


def _card_geometry(card_path: str, props: dict) -> Dict[Any, Tuple[Any, Any]]:
    """Each cue's own ink height and line count, measured on the card.

    The card file is the PLAN's artefact: measuring it says what the
    card was drawn to be, so the delivered file can be compared with it.
    A card that cannot be decoded yields no geometry, and the legs that
    need it report that they were not judged.

    The frames are read at the cue's own frames - the emphasis bump and
    the wrap are the card's own, and a frame outside the cue says
    nothing about this cue.
    """
    from library.tools.delivered_caption_spec import extract_frames

    fps = float(props.get("fps") or 0.0) or 30.0
    out: Dict[Any, Tuple[Any, Any]] = {}
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001 - no Pillow means no geometry
        return out
    with tempfile.TemporaryDirectory(prefix="ren-card-geometry-") as tmp:
        for cue in props.get("subtitles") or ():
            if not isinstance(cue, dict):
                continue
            try:
                first = int(cue.get("startFrame"))
                last = int(cue.get("endFrame"))
            except (TypeError, ValueError):
                continue
            wanted = sorted({first, max(first, last - 1),
                             first + max(1, (last - first) // 2)})
            try:
                paths = extract_frames(card_path, wanted, tmp)
            except Exception:  # noqa: BLE001 - an unreadable card is absent
                continue
            heights: List[float] = []
            lines: List[int] = []
            for index in wanted:
                path = paths.get(index)
                if not path:
                    continue
                try:
                    with Image.open(path) as image:
                        alpha = image.convert("RGBA").getchannel("A")
                        bbox = alpha.getbbox()
                        if bbox is None:
                            continue
                        heights.append(float(bbox[3] - bbox[1]))
                        lines.append(_ink_bands(alpha, bbox))
                except OSError:
                    continue
            if heights:
                out[("cue", first)] = (max(heights), max(lines) if lines
                                       else None)
    return out


def _ink_bands(alpha, bbox) -> int:
    """How many text lines an alpha channel carries, by its ink bands.

    The rows that hold ink, grouped into runs: a caption's lines are
    separated by blank rows, so the number of runs is the number of
    lines the card drew.
    """
    crop = alpha.crop(bbox)
    width, height = crop.size
    if width <= 0 or height <= 0:
        return 0
    pixels = crop.load()
    bands = 0
    inside = False
    for y in range(height):
        inked = False
        for x in range(0, width, 2):
            if pixels[x, y] > 32:
                inked = True
                break
        if inked and not inside:
            bands += 1
            inside = True
        elif not inked:
            inside = False
    return bands


__all__ = [
    "CAPTION_ROW",
    "CaptionPlan",
    "caption_plan",
]
