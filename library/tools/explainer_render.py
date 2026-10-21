"""Render an explainer SCENE: the beat route's renderer.

What this is
------------
The beat route (`library/tools/explainer_plan.py`) draws a reel's
explainer as a SCENE: a declared ground, an optional declared camera,
and one layer per beat - the picture that lands on the words it
illustrates, with the words reduced to a supporting label.

The scene is drawn by the SAME composition the animation IR is lowered
into - `StagedScene` in `remotion-subtitles/src/compositions` - because
that composition is the one that already owns a camera, a ground, world
space and frame space, a layer's opacity over time and a run that
COMPLETES on a second rather than starting there.  There is no second
scene renderer in this engine, and this module does not build one.

The rendering itself is the SHARED batch (`library/tools/remotion_batch.py`):
one bundle, one browser, every job through it - the same seam
`full_frame_element.render_reel_cards` and step 4.05 use.

The assets
----------
A beat's picture is a PROJECT-owned still (AGENTS.md 14: the engine
ships no artwork and states none).  It reaches the renderer through the
project's own `brand_assets/`, staged into Remotion's `public/brand/` by
`library/tools/remotion_brand_linker`, and the props carry the staged
`brand/<file>` path.  A beat whose still is not on disk is refused
before it is ever planned - `explainer_plan.plan_beats` - because a
picture that is not there is a hole in the frame, and a hole in the
frame is not a picture.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from library.tools.remotion_batch import (
    RemotionBatchError,
    RenderJob,
    render_batch,
)

#: The composition that draws a scene.  Registered in
#: `remotion-subtitles/src/Root.tsx`.
SCENE_COMPOSITION = "StagedScene"

#: A scene is a reel's body at most; past this a render is refused
#: rather than attempted, the same reading `full_frame_element
#: .MAX_CARD_SECONDS` gets.
MAX_SCENE_SECONDS = 300.0

RENDER_TIMEOUT_SECONDS = 900

#: How long a `delayRender()` handle may stay open inside the renderer.
#:
#: Remotion's own default is 30 seconds.  A scene is LONG - a reel's
#: whole body, hundreds of frames - and on a busy machine the renderer
#: can reload its page mid-render and re-open the FONT handle while the
#: box is saturated; the handle then outlives the default and the
#: render dies with a message about a font it was never waiting on.
#: Measured on a 950-frame scene: 400 frames rendered, 950 did not,
#: with the same props and the same machine.
SCENE_RENDER_TIMEOUT_MS = 300000


class ExplainerRenderError(RuntimeError):
    """A scene could not be turned into a file."""


def stage_project_assets(project_folder: Optional[str]) -> Dict[str, Any]:
    """Copy the project's own brand assets into Remotion's `public/brand/`.

    Lazy: a project whose beats name no still stages nothing.  The
    return value says what was staged, so a caller can tell an empty
    `brand/` from a failed one.
    """
    if not project_folder:
        return {"linked": False, "reason": "no project folder"}
    from library.tools.remotion_brand_linker import link_brand_assets

    return link_brand_assets(project_folder)


def render_scene(props: dict, out_path: str, work_dir: str, *,
                 repo_root: Optional[str] = None,
                 timeout: Optional[int] = None) -> str:
    """Draw one scene to one file, and say where it went.

    `props` is the scene renderer's own props - `explainer_plan
    .beat_scene_props` builds them.  `out_path` is the file to write.

    A failed render RAISES.  Nothing downstream would notice a missing
    scene: the reel would simply show the picture it was always going to
    show, which is exactly what it looks like when no explainer was
    declared at all.
    """
    if not isinstance(props, dict) or not props:
        raise ExplainerRenderError(
            "a scene with no props draws nothing; there is no default "
            "scene (AGENTS.md 10.5).")
    frames = int(props.get("durationInFrames") or 0)
    fps = float(props.get("fps") or 0.0)
    if frames <= 0 or fps <= 0:
        raise ExplainerRenderError(
            f"a scene of {frames} frame(s) at {fps}fps has no length; a "
            f"scene must occupy at least one frame.")
    if frames / fps > MAX_SCENE_SECONDS:
        raise ExplainerRenderError(
            f"a scene of {frames / fps:.1f}s is past the "
            f"{MAX_SCENE_SECONDS:.0f}s a scene may hold. Past that it is "
            f"not an explainer over a reel, it is a film.")
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    props_path = os.path.join(
        work_dir, os.path.splitext(os.path.basename(out_path))[0]
        + "_props.json")
    with open(props_path, "w", encoding="utf-8") as handle:
        json.dump(props, handle, indent=2, sort_keys=True)
    job = RenderJob(props=props, out_path=out_path)
    try:
        results = render_batch(
            [job], composition=SCENE_COMPOSITION, work_dir=work_dir,
            repo_root=repo_root,
            timeout=timeout or RENDER_TIMEOUT_SECONDS,
            render_timeout_ms=SCENE_RENDER_TIMEOUT_MS)
    except RemotionBatchError as exc:
        raise ExplainerRenderError(
            f"could not render the scene through {SCENE_COMPOSITION}: "
            f"{exc}") from exc
    result = (results or [{}])[0]
    if not result.get("ok"):
        raise ExplainerRenderError(
            f"render of the scene failed: "
            f"{result.get('error', 'no result for this scene')[:500]}")
    if not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
        raise ExplainerRenderError(
            f"render reported success but {out_path} is missing or empty")
    return out_path
