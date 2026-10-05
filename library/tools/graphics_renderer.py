"""Which graphics engine draws the pipeline's programmatic pictures.

One seam. Every render call site - step 4.05's caption cards, step 4.06's
motion graphics, the timed-text segments, the bookend cards and the
full-frame reel cards - asks this module which engine draws, and the
answer is one of two names:

* ``remotion`` - the personal edition's default. Public builds exclude
  it because of its commercial license terms.
* ``hyperframes`` - the public edition's default and an optional personal
  renderer, drawn from ``hyperframes/compositions/`` and rendered through
  ``library/tools/hyperframes_render.py``.

Precedence is project over user over default. The per-user setting lives
in the file ``library/tools/paths.py`` already loads -
``~/.config/ren/config.env`` - as ``PIPELINE_GRAPHICS_RENDERER``, so an
exported variable beats it the way it beats every other machine path.
The per-project preference is ``pipeline.graphics_renderer`` in the
project's own ``project.yaml`` (``library/schemas/project_config.py``
validates and round-trips the key), and a project that declares one
wins over the user setting, because the look of a video belongs to the
video rather than to the machine it happens to render on.

An unknown name REFUSES rather than falling back: a fallback would draw
with an engine nobody chose while reporting success, and a typo'd
selection would read as honoured.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

ENGINE_REMOTION = "remotion"
ENGINE_HYPERFRAMES = "hyperframes"

ENGINES = (ENGINE_REMOTION, ENGINE_HYPERFRAMES)
"""Every graphics engine, complete. A port that adds one edits this tuple."""

DEFAULT_ENGINE = ENGINE_REMOTION
"""Personal checkouts keep their historical Remotion default."""

USER_SETTING_KEY = "PIPELINE_GRAPHICS_RENDERER"
"""The per-user key, read from the process environment.

``paths.py`` loads ``~/.config/ren/config.env`` into ``os.environ`` at
import, so reading the environment reads the user's file - and an
explicit export wins, the same precedence every other machine path in
that module carries.
"""


class UnknownGraphicsEngine(ValueError):
    """A graphics engine name nothing renders with."""


def normalise(engine: str, *, where: str) -> str:
    """Lowercase-and-strip *engine*, or refuse it naming *where* it came from."""
    name = (engine or "").strip().lower()
    if name not in ENGINES:
        raise UnknownGraphicsEngine(
            f"{where} names graphics engine {engine!r}, which nothing "
            f"renders with. It takes {list(ENGINES)}.")
    return name


def user_engine() -> str:
    """The engine the user selected, or ``""`` when they selected none.

    Unset (and blank) means "no selection", which resolves to the
    default downstream - an unset key is the absence of a preference,
    not a choice of Remotion. A SET key that names nothing refuses,
    because a typo'd selection drawing the default would read as
    honoured.
    """
    raw = os.environ.get(USER_SETTING_KEY, "")
    if not raw.strip():
        return ""
    return normalise(raw, where=f"{USER_SETTING_KEY}")


def project_engine(project_folder: Optional[str] = None) -> str:
    """The engine the project selected, or ``""`` when it declares none.

    Read off the project's own ``project.yaml`` (``pipeline:`` block)
    with only the stdlib YAML a missing dependency cannot break - this
    module stays importable where `yaml` is not, because the render seam
    is not the place a missing parser should surface. A project that
    declares nothing (or has no file to declare it in) selects nothing,
    and the user setting answers instead.
    """
    if not project_folder:
        return ""
    candidate = Path(project_folder) / "project.yaml"
    if not candidate.is_file():
        return ""
    try:
        text = candidate.read_text(encoding="utf-8")
    except OSError:
        return ""
    value = _read_pipeline_key(text, "graphics_renderer")
    if value is None or not str(value).strip():
        return ""
    return normalise(str(value), where=f"{candidate} pipeline.graphics_renderer")


def _read_pipeline_key(text: str, key: str) -> Optional[str]:
    """The value of one scalar key under the top-level ``pipeline:`` block.

    Indentation-scoped, comment-stripped, quote-stripped - enough for
    the one scalar this seam reads, without importing a parser. A key
    the scan cannot prove is under ``pipeline:`` is not read at all,
    because reading another block's same-named key as this one would be
    the project winning with a word it never said.
    """
    in_pipeline = False
    pipeline_indent: Optional[int] = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 0 and stripped.rstrip(":") != "" and line.rstrip().endswith(":"):
            in_pipeline = stripped.startswith("pipeline:")
            pipeline_indent = None
            continue
        if not in_pipeline:
            continue
        if pipeline_indent is None:
            if indent == 0:
                in_pipeline = False
                continue
            pipeline_indent = indent
        if indent < (pipeline_indent or 1):
            in_pipeline = False
            continue
        name, sep, value = stripped.partition(":")
        if not sep or name.strip() != key:
            continue
        value = value.split("#", 1)[0].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        return value
    return None


def resolve_engine(project_folder: Optional[str] = None) -> str:
    """Which engine draws: the project, else the user, else the default.

    Project wins over user - the video's declaration over the machine's -
    and both lose to the edition default: personal builds resolve to
    Remotion, while public builds resolve to HyperFrames. An explicit
    personal-only renderer refuses in a public build.
    """
    from ren.edition import PUBLIC, current_edition, require_component, select_component

    project = project_engine(project_folder)
    user = user_engine()
    explicit = project or user
    if current_edition() == PUBLIC:
        if explicit:
            selected = normalise(explicit, where="graphics renderer setting")
            component_id = ("renderer.remotion" if selected == ENGINE_REMOTION
                            else "renderer.hyperframes")
            require_component(component_id, action="select")
            return selected
        component_id = select_component(
            "graphics rendering",
            personal_component="renderer.remotion",
            public_component="renderer.hyperframes")
        return (ENGINE_REMOTION if component_id == "renderer.remotion"
                else ENGINE_HYPERFRAMES)
    if project:
        return project
    if user:
        return user
    return DEFAULT_ENGINE


def is_hyperframes(project_folder: Optional[str] = None) -> bool:
    """True when the resolved engine is HyperFrames. The call-site branch."""
    return resolve_engine(project_folder) == ENGINE_HYPERFRAMES
