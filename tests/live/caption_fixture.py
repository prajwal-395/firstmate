"""Ren-rendered caption cards for the AXI-11 fixture (client-side build).

Two caption cards are rendered once with the real subtitle engine and
reused across runs: identical props render byte-identical MOVs beside
their render-props sidecars, so a second run reuses instead of
re-rendering. The broker imports the pool copies and places them on the
Subtitles row; restyling re-renders from the same props with a new
style.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
STEP_DIR = ROOT / "library" / "steps" / "step_4_05_render_subtitles"
if str(STEP_DIR) not in sys.path:
    sys.path.insert(0, str(STEP_DIR))

OUT_DIR = Path("/tmp/axc-captions")

# The fixture product is the landscape fm-scratch qualify timeline
# (measured 1920x1080 at 24 fps): cards render for the format the
# timeline really is, never the engine default. broker-side
# render_one_segment refuses props whose frame disagrees with the
# project delivery frame, so the stub project below and the temp run
# store the broker script stamps must declare the same name.
FIXTURE_FORMAT = "horizontal_1920x1080"
_FIXTURE_PROJECT = Path("/tmp/axc-fixture-project")

# Both texts must fit two lines even in bold_large (the restyle
# target): the render entry refuses a 3-line card. Measured with
# build_caption_fitter before changing any text here.
CARDS = (
    ("axc-caption-1", "tighten the opening",
     ["tighten", "the", "opening"], 0, 47),
    ("axc-caption-2", "keep captions readable",
     ["keep", "captions", "readable"], 0, 47),
)


def _fixture_project() -> str:
    """A stub project declaring the fixture's delivery format.

    Style resolution reads `pipeline.delivery_format` from the project
    folder; resolving against a bare dir silently yields the engine
    default (vertical) and renders a vertical style onto a landscape
    frame. The stub declares what the timeline measures.
    """
    from library.tools.delivery_format import resolve_format_name

    resolve_format_name(FIXTURE_FORMAT)  # raise here on a typo
    _FIXTURE_PROJECT.mkdir(parents=True, exist_ok=True)
    project_yaml = _FIXTURE_PROJECT / "project.yaml"
    project_yaml.write_text(
        "project_name: axc_caption_fixture\n"
        "pipeline:\n"
        f"  delivery_format: {FIXTURE_FORMAT}\n",
        encoding="utf-8")
    return str(_FIXTURE_PROJECT)


def _style(name: str) -> dict:
    from library.tools.edit_execution_adapters import (
        _resolve_named_caption_style,
    )

    return json.loads(json.dumps(
        _resolve_named_caption_style(_fixture_project(), name),
        default=str))


def _props(name: str, text: str, words: list[str],
           start: int, end: int, style: dict) -> dict:
    from library.tools.delivery_format import DELIVERY_FORMATS

    frame_w, frame_h = DELIVERY_FORMATS[FIXTURE_FORMAT]
    step = max(1, (end - start + 1) // max(1, len(words)))
    return {
        "_block_position": 1,
        "_timeline_start": 0.0,
        "_timeline_end": round((end + 1) / 24.0, 3),
        "_source_in_frame": 0,
        "_source_out_frame": end + 1,
        "_speaker": None,
        "_source_clip_id": name,
        "_source_start": 0.0,
        "_source_end": round((end + 1) / 24.0, 3),
        "durationInFrames": end + 1,
        "fps": 24,
        "width": frame_w,
        "height": frame_h,
        "style": dict(style),
        "subtitles": [{
            "text": text,
            "startFrame": start,
            "endFrame": end,
            "emphasisWords": [],
            "words": [
                {"word": word,
                 "startFrame": start + index * step,
                 "endFrame": min(end, start + (index + 1) * step - 1)}
                for index, word in enumerate(words)
            ],
            "fitScale": 1.0,
        }],
    }


def ensure_caption_cards() -> tuple[Path, Path]:
    """Render (once) and return the two fixture MOV paths."""
    from library.steps.step_4_05_render_subtitles.step import (
        render_one_segment,
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    style = _style("default_subtitles")
    movies = []
    for name, text, words, start, end in CARDS:
        props = _props(name, text, words, start, end, style)
        entry = render_one_segment(dict(props), str(OUT_DIR), "tl",
                                   reuse=True)
        movie = entry.get("overlay_path") or ""
        if entry.get("provenance") not in {"rendered", "reused"} \
                or not movie or not Path(movie).is_file():
            raise RuntimeError(
                f"caption fixture card {name!r} did not render: {entry}")
        sidecar = Path(movie).with_name(
            Path(movie).stem + "_props.json")
        if not sidecar.is_file():
            sidecar.write_text(json.dumps(props, indent=2, sort_keys=True)
                               + "\n", encoding="utf-8")
        movies.append(Path(movie))
    return (movies[0], movies[1])


def main() -> int:
    started = time.time()
    one, two = ensure_caption_cards()
    print(json.dumps({"caption_one": str(one),
                      "caption_two": str(two),
                      "elapsed_seconds": round(time.time() - started, 1)},
                     indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
