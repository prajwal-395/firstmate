"""AGENT-011: one deterministic owner for "which clip did you mean".

A caller who knows the USER-FACING task - "trim the second interview B-roll",
"replace the segment from 12s to 18s" - must not have to read the timeline,
count rows and guess a position. Six resolvers already existed, each with
its own input vocabulary, and none of them accepted a description
(`timeline_addressing` resolves a live item, `edit_preparation` resolves
an `EditRequest` target, `sub_block_anchor` resolves an anchor inside a
spine block, `marker_routing` resolves a note's frame, `reel_address`
resolves a reel number, `timeline_layout` resolves a planned row). An
agent hand-counted rows in a TOON table instead, and the `items` table did
not even print the one column that would let it address a clip by its
stable identity.

This module is the ONE place a described target becomes a concrete clip:

- `SELECTOR_KINDS` is the ONE vocabulary of how a target may be described.
- `parse_selector` turns the compact `key=value` text form into a
  `TargetSelector`; an unknown key is refused, never ignored.
- `resolve_target` resolves a selector against ONE source of truth - a
  timeline's clip records - and returns the clips it names.

Two rules make the answer trustworthy:

**A resolution that is not by stable identity says so.** A clip named by
its row position or by its name is `assumed`, with the assumption written
down: a reorder or a rename since the description was written misresolves
it. `edit_preparation` already draws that line (`ResolvedTarget.status`),
and this module draws the same one rather than inventing a second scale.

**A description that does not name exactly one thing is refused, not
guessed.** Zero matches is `TargetNotFoundRefused`; more than one is
`TargetAmbiguousRefused`, and the refusal names the candidates. A frame
range is the one selector that may name several clips, because a range
that spans two clips means both of them - that is a `group`, and it says
so.

The refusals are `RenRefusal`s, so they reach the `ren` front door and the
AXI CLI in the engine's one refusal shape, with a recovery class and a fix.
"""
from __future__ import annotations

import re
import shlex
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from library.tools.ren_refusal import RenRefusal

SELECTOR_KINDS = ("identity", "name", "ordinal", "time_range")
"""The ONE vocabulary of ways a target may be described.

`identity` - the clip's own stable id (`unique_id`).
`name` - the clip's exact name, optionally narrowed by row and media type.
`ordinal` - the nth clip on a row, counted the way a person counts
(1-based).
`time_range` - the record-frame span the clip occupies.

Anything a caller wants to describe that is not one of these is not a
target this module resolves, and saying so is better than a guess.
"""


class TargetResolutionRefused(RenRefusal):
    """A described target did not resolve to exactly one clip."""

    CODE = "REN_TARGET_RESOLUTION_REFUSED"
    RECOVERY_CLASS = "auto_recoverable"
    USER_MESSAGE = ("I could not tell which clip you meant, so I changed "
                    "nothing. The options below name what I did find.")


class TargetNotFoundRefused(TargetResolutionRefused):
    """Nothing on the timeline matches the description."""

    CODE = "REN_TARGET_NOT_FOUND"
    RECOVERY_CLASS = "auto_recoverable"


class TargetAmbiguousRefused(TargetResolutionRefused):
    """More than one clip matches the description."""

    CODE = "REN_TARGET_AMBIGUOUS"
    RECOVERY_CLASS = "needs_question"
    USER_MESSAGE = ("More than one clip matches what you described, so I "
                    "changed nothing. The options below name them all.")


# ── The description ──────────────────────────────────────────────────


_ROW_CODE = re.compile(r"^(video|v|audio|a)\s*(\d+)$", re.IGNORECASE)
"""`V2`, `video2`, `A1`, `audio1` - a row by its Resolve track."""


@dataclass(frozen=True)
class TargetSelector:
    """A described target, in the ONE vocabulary.

    `kind` says which of `SELECTOR_KINDS` this is. The fields that kind
    does not use stay empty, so a selector says exactly one thing.
    """

    kind: str
    row: str = ""
    ordinal: int = 0
    name: str = ""
    unique_id: str = ""
    record_start: int | None = None
    record_end: int | None = None
    media_type: str = ""
    source_file: str = ""

    def __post_init__(self) -> None:
        if self.kind not in SELECTOR_KINDS:
            raise ValueError(
                f"unknown target selector kind {self.kind!r}; one of "
                f"{list(SELECTOR_KINDS)}")
        if self.kind == "ordinal" and not self.row:
            raise ValueError(
                "an ordinal target names a row: without one, 'the second "
                "clip' has no meaning")
        if self.kind == "time_range" and (self.record_start is None
                                          or self.record_end is None):
            raise ValueError(
                "a time-range target needs both a start and an end frame")

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "row": self.row,
            "ordinal": self.ordinal,
            "name": self.name,
            "unique_id": self.unique_id,
            "record_start": self.record_start,
            "record_end": self.record_end,
            "media_type": self.media_type,
            "source_file": self.source_file,
        }

    def describe(self) -> str:
        """One line saying what was asked for, for a refusal or a note."""
        if self.kind == "identity":
            return f"clip id {self.unique_id!r}"
        if self.kind == "name":
            return f"the clip named {self.name!r}"
        if self.kind == "ordinal":
            return f"clip {self.ordinal} on {self.row!r}"
        return (f"the span from frame {self.record_start} to "
                f"{self.record_end}")


_TEXT_KEYS = {
    "id": "unique_id",
    "name": "name",
    "row": "row",
    "item": "ordinal",
    "start": "record_start",
    "end": "record_end",
    "media": "media_type",
    "source": "source_file",
}
"""The text form's keys, and the selector field each one sets."""


def parse_selector(text: str) -> TargetSelector:
    """The compact text form: `row=V2 item=2`, `name="Interview A"`.

    Quoted values keep their spaces. An unknown key is refused: a typo
    that was ignored would resolve to something the caller did not ask
    for, which is the defect this module exists to remove.
    """
    raw = (text or "").strip()
    if not raw:
        raise TargetResolutionRefused(
            what="no target was described",
            why="the description was empty",
            fix=("name the target: `id=<clip id>`, `name=<clip name>`, "
                 "`row=V2 item=2`, or `start=<frame> end=<frame>`"))
    try:
        tokens = shlex.split(raw)
    except ValueError as exc:
        raise TargetResolutionRefused(
            what=f"the target description {raw!r} could not be read",
            why=f"the quoting is unbalanced ({exc})",
            fix="quote a value that contains spaces: name=\"Interview A\"") \
            from exc
    fields: dict[str, Any] = {}
    for token in tokens:
        if "=" not in token:
            raise TargetResolutionRefused(
                what=f"the target description {raw!r} could not be read",
                why=f"{token!r} is not a key=value pair",
                fix=("write the target as key=value pairs: "
                     "`row=V2 item=2`, `name=\"Interview A\"`, "
                     "`id=<clip id>`, `start=<frame> end=<frame>`"))
        key, _, value = token.partition("=")
        key = key.strip().lower()
        if key not in _TEXT_KEYS:
            raise TargetResolutionRefused(
                what=f"the target description names an unknown key {key!r}",
                why=(f"a target is described with {sorted(_TEXT_KEYS)}; "
                     f"{key!r} is not one of them"),
                fix=("name the target with the keys above: "
                     "`row=V2 item=2`, `name=\"Interview A\"`, "
                     "`id=<clip id>`, `start=<frame> end=<frame>`"))
        field = _TEXT_KEYS[key]
        if field in ("ordinal", "record_start", "record_end"):
            try:
                fields[field] = int(value)
            except ValueError as exc:
                raise TargetResolutionRefused(
                    what=f"the target description gives {key}={value!r}",
                    why=f"{key} must be a whole number",
                    fix=f"write {key}=<number>") from exc
        else:
            fields[field] = value
    if "unique_id" in fields:
        kind = "identity"
    elif "name" in fields:
        kind = "name"
    elif "ordinal" in fields:
        kind = "ordinal"
    elif "record_start" in fields or "record_end" in fields:
        kind = "time_range"
    else:
        raise TargetResolutionRefused(
            what=f"the target description {raw!r} names no way to find a clip",
            why=("a target is a clip id, a clip name, a row and a "
                 "position, or a frame range"),
            fix=("name the target: `id=<clip id>`, `name=<clip name>`, "
                 "`row=V2 item=2`, or `start=<frame> end=<frame>`"))
    try:
        return TargetSelector(kind=kind, **fields)
    except ValueError as exc:
        raise TargetResolutionRefused(
            what=f"the target description {raw!r} is incomplete",
            why=str(exc),
            fix=("name the target: `id=<clip id>`, `name=<clip name>`, "
                 "`row=V2 item=2`, or `start=<frame> end=<frame>`")) from exc


# ── The answer ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class TargetMatch:
    """One clip the description named, with the identity to address it by."""

    clip: Mapping[str, Any]
    """The clip record exactly as the timeline holds it, so every
    existing consumer (`EditTarget.from_shadow_clip`) can read it."""

    position: int
    """0-based position on its row - the `--index` an AXI verb takes."""

    assumption: str = ""
    """Why this match is not by stable identity; empty when it is."""

    @property
    def unique_id(self) -> str:
        return str(self.clip.get("unique_id") or "")

    @property
    def name(self) -> str:
        return str(self.clip.get("name") or "")

    @property
    def track_type(self) -> str:
        return str(self.clip.get("track_type") or "")

    @property
    def track_index(self) -> int:
        try:
            return int(self.clip.get("track_index") or 0)
        except (TypeError, ValueError):
            return 0

    @property
    def track_name(self) -> str:
        return str(self.clip.get("track_name") or "")

    @property
    def row(self) -> str:
        """`V2` / `A1` - the row label every other reader uses."""
        return row_label(self.track_type, self.track_index)

    @property
    def record_span(self) -> tuple[int, int] | None:
        return _span(self.clip, "record_in", "record_out")

    @property
    def source_span(self) -> tuple[int, int] | None:
        return _span(self.clip, "source_in_frame", "source_out_frame")

    @property
    def source_file(self) -> str:
        return str(self.clip.get("source_file") or "")

    @property
    def media_type(self) -> str:
        return str(self.clip.get("media_type") or "")

    def as_dict(self) -> dict:
        record = self.record_span or (None, None)
        source = self.source_span or (None, None)
        return {
            "unique_id": self.unique_id,
            "name": self.name,
            "row": self.row,
            "track": f"{self.track_type}{self.track_index}",
            "track_name": self.track_name,
            "index": self.position,
            "record_in": record[0],
            "record_out": record[1],
            "source_in": source[0],
            "source_out": source[1],
            "source_file": self.source_file,
            "media_type": self.media_type,
            "assumption": self.assumption,
        }


@dataclass(frozen=True)
class Resolution:
    """What a description named.

    `status` is `resolved` (named by stable identity), `assumed` (named
    by a rule that a rename or a reorder would change) or `group` (a
    frame range that names several clips, and means all of them).
    """

    selector: TargetSelector
    status: str
    matches: tuple[TargetMatch, ...]
    note: str = ""

    @property
    def single(self) -> TargetMatch:
        """The one match; refuses when there is not exactly one."""
        if len(self.matches) != 1:
            raise TargetAmbiguousRefused(
                what=f"the target {self.selector.describe()} names "
                      f"{len(self.matches)} clips",
                why="a single target needs exactly one clip",
                fix="narrow the description, or act on the whole group")
        return self.matches[0]

    def as_dict(self) -> dict:
        return {
            "selector": self.selector.as_dict(),
            "status": self.status,
            "note": self.note,
            "matches": [match.as_dict() for match in self.matches],
        }


# ── Resolution ───────────────────────────────────────────────────────


def row_label(track_type: str, track_index: Any) -> str:
    """`V2` / `A1` - the row label `edit_preparation` and the AXI use."""
    kind = str(track_type or "")
    prefix = "V" if kind.lower().startswith("v") else (
        "A" if kind.lower().startswith("a") else kind[:1].upper())
    return f"{prefix}{track_index}"


def _span(clip: Mapping[str, Any], start_key: str, end_key: str
          ) -> tuple[int, int] | None:
    try:
        return (int(clip[start_key]), int(clip[end_key]))
    except (KeyError, TypeError, ValueError):
        return None


def clips_of(snapshot: Mapping[str, Any]) -> list[dict]:
    """Every clip in a timeline snapshot, in timeline order.

    The same flattening `timeline_read.clip_records` does, so a caller
    that already has a snapshot does not learn a second shape.
    """
    from library.tools import timeline_read

    return list(timeline_read.clip_records(dict(snapshot)))


def _row_matches(clip: Mapping[str, Any], row: str) -> bool:
    """Whether a clip sits on the described row.

    A row is a Resolve track (`V2`, `video2`, `A1`, `audio1`) or the
    track's NAME. An empty row matches every row.
    """
    if not row:
        return True
    wanted = str(row).strip()
    code = _ROW_CODE.match(wanted)
    if code:
        media = code.group(1).lower()
        media = "video" if media.startswith("v") else "audio"
        try:
            index = int(code.group(2))
        except ValueError:
            return False
        try:
            track_index = int(clip.get("track_index") or -1)
        except (TypeError, ValueError):
            return False
        return (str(clip.get("track_type") or "").lower() == media
                and track_index == index)
    name = str(clip.get("track_name") or "")
    return bool(name) and name.strip().lower() == wanted.lower()


def _media_matches(clip: Mapping[str, Any], media_type: str) -> bool:
    """Whether a clip carries the described media.

    The clip's own pool type is the answer; when the pool did not say
    (an unreadable `Type` property), its ROW's type stands in, because a
    clip on the video row is the video.
    """
    if not media_type:
        return True
    wanted = media_type.lower()
    actual = str(clip.get("media_type") or "").lower()
    if actual:
        return actual == wanted
    return str(clip.get("track_type") or "").lower() == wanted


def _row_key(clip: Mapping[str, Any]) -> tuple[str, int]:
    try:
        index = int(clip.get("track_index") or 0)
    except (TypeError, ValueError):
        index = 0
    return (str(clip.get("track_type") or ""), index)


def _ordered(clips: Sequence[Mapping[str, Any]]
             ) -> list[tuple[Mapping[str, Any], int]]:
    """The clips with their position on their row, in timeline order."""
    counters: dict[tuple[str, int], int] = {}
    result: list[tuple[Mapping[str, Any], int]] = []
    for clip in clips:
        key = _row_key(clip)
        position = counters.get(key, 0)
        counters[key] = position + 1
        result.append((clip, position))
    return result


def _candidates(selector: TargetSelector,
                clips: Sequence[Mapping[str, Any]]
                ) -> list[tuple[Mapping[str, Any], int]]:
    """The clips a selector's qualifiers admit, in timeline order."""
    return [(clip, position) for clip, position in _ordered(clips)
            if _row_matches(clip, selector.row)
            and _media_matches(clip, selector.media_type)]


def _overlaps(clip: Mapping[str, Any], start: int, end: int) -> bool:
    span = _span(clip, "record_in", "record_out")
    if span is None:
        return False
    clip_start, clip_end = span
    return clip_start < end and start < clip_end


def resolve_target(selector: TargetSelector,
                   clips: Iterable[Mapping[str, Any]]) -> Resolution:
    """Resolve a described target against a timeline's clips.

    Refuses rather than guesses: zero matches is
    `TargetNotFoundRefused`, more than one match for a name is
    `TargetAmbiguousRefused`, and a match by name or by position is
    `assumed` with the assumption written down.
    """
    ordered = list(clips)
    if not ordered:
        raise TargetNotFoundRefused(
            what=f"the target {selector.describe()} was not found",
            why="the timeline holds no clips to search",
            fix="read the timeline's clips first, then name the target")

    if selector.kind == "identity":
        found = [(clip, position) for clip, position in _ordered(ordered)
                 if str(clip.get("unique_id") or "") == selector.unique_id]
        if not found:
            raise TargetNotFoundRefused(
                what=f"no clip on this timeline has the id "
                      f"{selector.unique_id!r}",
                why="the id is not on the timeline this was read from",
                fix="read the timeline's clips, and use an id from that list")
        if len(found) > 1:
            raise TargetAmbiguousRefused(
                what=f"{len(found)} clips on this timeline share the id "
                      f"{selector.unique_id!r}: {_describe_found(found)}",
                why="a live identity must name one clip",
                fix="name the clip by its row and position instead")
        clip, position = found[0]
        return Resolution(selector, "resolved",
                           (TargetMatch(clip, position),),
                           "named by its stable id")

    if selector.kind == "name":
        found = [(clip, position) for clip, position in _ordered(ordered)
                 if str(clip.get("name") or "") == selector.name
                 and _row_matches(clip, selector.row)
                 and _media_matches(clip, selector.media_type)]
        if not found:
            raise TargetNotFoundRefused(
                what=f"no clip on this timeline is named {selector.name!r}",
                why=("the timeline holds no clip with that name on the "
                     "named row"),
                fix=("read the timeline's clips, and name one of them "
                     "exactly - or address it by row and position"))
        if len(found) > 1:
            raise TargetAmbiguousRefused(
                what=f"{len(found)} clips on this timeline are named "
                      f"{selector.name!r}: {_describe_found(found)}",
                why="a name that is not unique cannot name one clip",
                fix=("narrow the description with a row, or address the "
                     "clip by its id"))
        clip, position = found[0]
        return Resolution(
            selector, "assumed", (TargetMatch(
                clip, position,
                "matched by name - a rename since this was written would "
                "misresolve it"),),
            "matched by name")

    if selector.kind == "ordinal":
        row_clips = _candidates(selector, ordered)
        if not row_clips:
            raise TargetNotFoundRefused(
                what=f"this timeline has no row {selector.row!r}",
                why="no clip sits on the named row",
                fix="read the timeline's clips, and use a row from that list")
        index = selector.ordinal - 1
        if index < 0 or index >= len(row_clips):
            raise TargetNotFoundRefused(
                what=f"{selector.row} holds {len(row_clips)} clip(s), so "
                      f"clip {selector.ordinal} names nothing: "
                      f"{_describe_found(row_clips)}",
                why="the position is outside the row",
                fix=("read the timeline's clips, and use a position that "
                     "exists on that row"))
        clip, position = row_clips[index]
        return Resolution(
            selector, "assumed", (TargetMatch(
                clip, position,
                "matched by position - a reorder since this was written "
                "would misresolve it"),),
            "matched by position")

    if selector.kind == "time_range":
        start = int(selector.record_start or 0)
        end = int(selector.record_end or 0)
        if end <= start:
            raise TargetResolutionRefused(
                what=f"the span from frame {start} to {end} is empty",
                why="the end frame must come after the start frame",
                fix="write the span as start=<frame> end=<later frame>")
        found = [(clip, position) for clip, position in _ordered(ordered)
                 if _row_matches(clip, selector.row)
                 and _media_matches(clip, selector.media_type)
                 and _overlaps(clip, start, end)]
        if not found:
            raise TargetNotFoundRefused(
                what=f"no clip on this timeline covers frames {start} to {end}",
                why="the span falls in a gap, or on another row",
                fix=("read the timeline's clips, and use a span that "
                     "overlaps one of them"))
        matches = tuple(TargetMatch(clip, position) for clip, position in found)
        if len(matches) == 1:
            return Resolution(selector, "resolved", matches,
                              "the span covers one clip")
        rows = sorted({match.row for match in matches})
        return Resolution(
            selector, "group", matches,
            f"the span covers {len(matches)} clip(s) on {len(rows)} row(s) "
            f"({', '.join(rows)}) - it means all of them; narrow it with "
            "row=<row> to address one")

    raise TargetResolutionRefused(  # pragma: no cover - guarded by the kind
        what=f"unknown target selector kind {selector.kind!r}",
        why=f"one of {list(SELECTOR_KINDS)}",
        fix="name the target with the supported selector kinds")


def resolve_text(text: str, clips: Iterable[Mapping[str, Any]]) -> Resolution:
    """The compact text form, resolved. The one call a CLI needs."""
    return resolve_target(parse_selector(text), clips)


def _describe_found(found: Sequence[tuple[Mapping[str, Any], int]]) -> str:
    """The clips a description matched, in one line, for a refusal."""
    return "; ".join(
        f"{row_label(str(clip.get('track_type') or ''), clip.get('track_index') or 0)}"
        f" item {position}"
        f" ({str(clip.get('name') or '') or 'unnamed'}"
        f", id {clip.get('unique_id') or ''!s}, frames "
        f"{_span(clip, 'record_in', 'record_out')})"
        for clip, position in found) or "none"
