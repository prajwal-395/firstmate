"""What this video is built from, as the project declares it.

`source.shape` in project.yaml: `speech`, `music`, `both` or
`picture-led`. Undeclared ("") means the historical shape - speech-led -
and reads the same everywhere. Intake scaffolds the declaration
(`library/tools/project_registry.py`); this module is the reader.

Two answers, deliberately narrow:

- :func:`declared_shape` - the project's own word, or "" when it said
  nothing (or nothing readable). Malformed is NOT refused here:
  `ProjectConfig.validate` owns the refusal, the same split
  `library/tools/footage_identity.source_block` documents.
- :func:`expects_speech` - whether a run of this shape plans on hearing
  voices. Speech-led (`""`, `speech`) and interleaved (`both`) do; a
  music bed (`music`) and a picture-led montage (`picture-led`) do not.

Whether music plays is NOT answered here. A `picture-led` montage may
run scored or silent, and a `speech` piece may still take a bed - that
is the model conducting `music_bed` over measured candidates, not a
property of the shape. A module answering it from this declaration
would be inventing taste (AGENTS.md 10.5).
"""

from library.tools.footage_identity import source_block

KNOWN_SHAPES = ("speech", "music", "both", "picture-led")


def declared_shape(project_folder: str) -> str:
    """The project's declared `source.shape`, or "" when undeclared.

    Unreadable project.yaml and malformed values both read as "" - the
    historical speech-led shape - so a project that never answered the
    intake runs exactly as it always did.
    """
    if not project_folder:
        return ""
    raw = source_block(project_folder).get("shape")
    if isinstance(raw, str) and raw.strip().lower() in KNOWN_SHAPES:
        return raw.strip().lower()
    return ""


def expects_speech(shape: str = "", project_folder: str = "") -> bool:
    """True when a run of this shape plans on hearing voices.

    `shape` is the already-read declaration where the caller has one;
    the project folder is read otherwise. Undeclared ("") expects
    speech: every project before the setting existed was speech-led,
    and that reading stays it for projects that declare nothing.
    """
    if not shape and project_folder:
        shape = declared_shape(project_folder)
    return (shape or "") not in ("music", "picture-led")
