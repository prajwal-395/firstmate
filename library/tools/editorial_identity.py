"""The ONE durable identity of a placed clip.

What a clip IS, as distinct from where it sits and what Resolve calls it.

Three identities do three different jobs, and the confusion between them
is the defect this module exists to remove:

- the NATIVE id (``TimelineItem.GetUniqueId()``) addresses one Resolve
  OBJECT.  A re-placed clip gets a new one, so it cannot be an identity
  that survives a rebuild.
- the ADDRESSING selector (``timeline_addressing.editorial_identity``)
  finds a live item by its source window.  It is a query, and a trim
  changes the window it asks for - which is correct for a query and
  wrong for an identity.
- the EDITORIAL identity (this module) says which editorial MOMENT a
  clip is.  It is derived from the MATERIAL it plays and the ROLE it
  plays it in, so it survives an insert, a trim, a rebuild, and a
  replacement of the same material.

What each survives
------------------

============================  =====================  ===================
event                         editorial identity      native id
============================  =====================  ===================
an insert before it           unchanged              unchanged
a trim of it                  unchanged (same        CHANGED
                              material and role)
a rebuild of the same plan    unchanged              CHANGED
a replacement, same material  unchanged              CHANGED
a replacement, other material  CHANGED (it is a      CHANGED
                              different moment)
============================  =====================  ===================

The material's own identity is NOT re-derived here: the replace guard
already answers "what does this item play?" in one place
(``reel_replace_guard.item_source_identity``), and a second answer would
be a second thing to keep in step.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

#: The prefix of a material identity.
MATERIAL = "material"
#: The prefix of a durable clip identity.
CLIP = "clip"
#: The prefix of a passage identity.
PASSAGE = "passage"


def _sha16(*parts: Any) -> str:
    """A deterministic sixteen hex characters over canonical JSON parts."""
    canonical = "\x00".join(
        json.dumps(part, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=False, default=str)
        for part in parts
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Identity:
    """Which editorial moment a clip is.

    ``token`` is the DURABLE identity: it is derived from the material
    and the role alone, so it survives a trim.  ``passage_id`` names the
    exact passage the clip plays RIGHT NOW, and it changes when the clip
    is trimmed.  Both travel together, because a consumer that needs the
    moment needs the durable part and a consumer that needs the extent
    needs the passage.
    """

    material_id: str
    role: str
    token: str
    passage_id: str
    source_span: tuple[int, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "material_id": self.material_id,
            "role": self.role,
            "token": self.token,
            "passage_id": self.passage_id,
            "source_span": list(self.source_span),
        }


def material_id_of(clip: Mapping[str, Any]) -> str:
    """The identity of the MEDIA a clip plays, or "" where it plays none.

    The replace guard already answers "what does this item play?" in one
    place (``reel_replace_guard.item_source_identity``): the media pool
    id when there is one, else the normalised file path, else a
    generator description.  Reusing it is deliberate - two answers to
    "what does this play?" would be two things to keep in step.

    A GENERATOR names no media.  Its only identity is Resolve's own
    object id, which a rebuild does not preserve, so it is not a
    material and this returns "".
    """
    from library.tools.reel_replace_guard import item_source_identity

    identity = item_source_identity(clip)
    if identity.startswith("generator"):
        return ""
    return identity


def role_of(clip: Mapping[str, Any], role: str = "") -> str:
    """The editorial ROLE a clip plays in.

    A caller that knows the row's plan role passes it: the plan role is
    the editorial truth.  Without one the role is the clip's MEDIA TYPE,
    which every reader can compute - the track's NAME is not, because a
    track can be renamed and a rename is not a change of editorial role,
    and because a reader that knows only the media type and a reader that
    knows only the track name would otherwise name two different roles
    for the same clip.

    Never empty: a role nobody can name cannot identify anything.
    """
    if role:
        return str(role)
    media_type = str(clip.get("track_type") or "").strip()
    return media_type or "unknown"


def token_of(material_id: str, role: str) -> str:
    """The durable identity of one material in one role."""
    return f"{CLIP}:{_sha16(material_id, role)}"


def passage_of(material_id: str, role: str,
               source_span: tuple[int, int]) -> str:
    """The identity of one passage of one material in one role."""
    return f"{PASSAGE}:{_sha16(material_id, role, int(source_span[0]),
                               int(source_span[1]))}"


def use_token_of(material_id: str, role: str,
                 source_span: tuple[int, int], ordinal: int = 0) -> str:
    """A durable id for one USE of one material in one role.

    The source span is part of it because one material can play twice in
    one role, and two uses of the same material are two different
    moments.  ``ordinal`` separates two uses of the SAME passage in the
    same role, which is the only case the span cannot separate.

    This is the id the Project Model mints for a clip use, and it is
    derived from the CONTENT rather than from the clip's position in a
    list: a positional id renames every later clip when one is inserted
    in front of them.  The Project Model passes its PLAN role, which is
    richer than the media type a live read can compute; the two name the
    same thing at different resolutions, and the model's is the
    authority.
    """
    return _sha16(material_id, role, int(source_span[0]),
                  int(source_span[1]), int(ordinal))


def identity_of(clip: Mapping[str, Any], role: str = "") -> Identity | None:
    """The editorial identity of a clip read, or None where it names none.

    A clip that plays no media - a generator with no pool item and no
    path - has no material, and its only identity is Resolve's object
    id, which a rebuild does not preserve.  That is reported as None
    rather than as an identity over nothing.
    """
    material_id = material_id_of(clip)
    if not material_id:
        return None
    resolved_role = role_of(clip, role)
    span = _source_span(clip)
    return Identity(
        material_id=material_id,
        role=resolved_role,
        token=token_of(material_id, resolved_role),
        passage_id=passage_of(material_id, resolved_role, span),
        source_span=span,
    )


def _source_span(clip: Mapping[str, Any]) -> tuple[int, int]:
    """The clip's source window, as the pair of frames it plays."""
    start = clip.get("source_in_frame")
    end = clip.get("source_out_frame")
    try:
        return (int(start), int(end))
    except (TypeError, ValueError):
        return (0, 0)


def carry_identity(previous: Identity | None,
                   current: Identity | None) -> Identity | None:
    """The identity a trim or a replacement keeps, or None.

    A TRIM keeps the identity: the same material in the same role, over a
    different passage.  A REPLACEMENT with the same material keeps it
    too - the replacement stands in for the same editorial moment.  A
    replacement with DIFFERENT material does not, and must not: a clip
    that plays other footage is a different editorial moment, and letting
    it inherit the identity would let a substitution pass as the thing it
    replaced.

    The returned identity carries the CURRENT passage and the durable
    token, so a caller can record both the extent that changed and the
    identity that did not.
    """
    if previous is None or current is None:
        return None
    if previous.material_id != current.material_id:
        return None
    if previous.role != current.role:
        return None
    return Identity(
        material_id=current.material_id,
        role=current.role,
        token=previous.token,
        passage_id=current.passage_id,
        source_span=current.source_span,
    )


def is_substitution(previous: Identity | None,
                    current: Identity | None) -> bool:
    """Whether a carry would be a SUBSTITUTION rather than a trim.

    A substitution changes the material.  It is the one case where the
    identity does NOT survive, and the one a preservation check has to
    hear about rather than absorb.
    """
    if previous is None or current is None:
        return False
    return previous.material_id != current.material_id


def source_path_key(path: str) -> str:
    """A file path as one key, whatever the working directory.

    The same media under two relative paths is the same media, and a
    material identity that split on the spelling would make one moment
    look like two.
    """
    if not path:
        return ""
    return os.path.normcase(os.path.realpath(str(path)))
