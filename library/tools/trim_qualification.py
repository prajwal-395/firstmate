"""What a precise trim claims on a built reel, and how each claim is judged.

A trim is not one edit.  It is a SOURCE-WINDOW change to one clip, a
RECORD-SPAN change to every item that change moves, and a set of
promises about the rows it does not name.  A receipt that says "trim"
without saying which of those it proved has proved nothing, so this
module is the ONE place the claims are written down and judged: the
touch-up path (`library/tools/reel_touchup.py`) and the semantic AXI
route (`library/tools/edit_execution_adapters.py`) read the same claims
off the same read-backs.

THE CLAIMS
----------

`handle_validation`
    The requested source window lies inside the MEASURED bounds of the
    clip it names: inside the clip's own source range, and inside the
    frames its media file actually has.  A trim that reaches past a
    handle places frames the file does not hold, and the hole is
    invisible until the reel renders.

`source_frame_precision`
    The clip reads back at the SOURCE frames the request asked for, to
    the frame.  This is the claim that distinguishes a trim from a
    re-place that happens to start in the right place.

`record_frame_precision`
    The clip reads back at the RECORD frames the request asked for, to
    the frame, and its record duration equals its source duration.  A
    source window and a record window that disagree by a frame are a
    trim that slipped.

`ripple_semantics`
    A RIPPLE move: every item the plan moved reads back at the span
    the plan gave it, and every item the plan did not move reads back
    where it was.

`non_ripple_semantics`
    A NON-RIPPLE move: nothing but the target moved, and the frames it
    vacated are a gap the caller owns.  The two are SEPARATE claims
    because they are different edits: a trim that shifts the rest of
    the reel is not a trim that leaves a gap, and a receipt that says
    "trim" without saying which one it proved proved neither.

`linked_audio_preserved`
    The target's linked partners read back unchanged - identity, record
    span, source span and enablement.  A picture trimmed off its audio
    is a silent reel that looks right.

`caption_timing`
    Every caption row reads back at the span the plan gave it, and no
    two caption cards on one row overlap.  A caption track is timed to
    WORDS, so a ripple that moves the picture without moving the
    captions leaves the reel saying the wrong thing at the wrong time.

UNMEASURED IS NEVER A PASS
--------------------------

A claim whose evidence is missing reads `unmeasured`, never `passed`.
The verdict carries the MEASURED numbers it judged on, so a reader can
see what was checked rather than being told that it was.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field


RIPPLE = "ripple"
NON_RIPPLE = "non_ripple"
"""The two EDIT SEMANTICS, kept apart.

`NON_RIPPLE` narrows one clip and leaves a gap the caller owns.
`RIPPLE` narrows one clip and shifts everything after it.  The two are
different edits with different proofs, so they are named apart here
rather than being one "trim" with a flag.
"""

CAPTION_ROW_NAMES = ("subtitles", "captions", "caption")
"""Row NAMES that carry caption cards.

Named, never guessed from an index: the caption row is the one the
project's layout calls Subtitles, and a row INDEX says nothing about
what plays on it.
"""


class TrimClaimError(ValueError):
    """A trim claim that cannot be stated from what was measured."""


@dataclass(frozen=True)
class TrimRequest:
    """What the caller asked a trim to do, in frames.

    `source_in`/`source_out` are ABSOLUTE source frames of the target
    clip (the file's own frame space), inclusive at both ends.  A
    request that names only one of them leaves the other where it was,
    which is a narrowing of that end only.  `source_delta` is the
    change to the source window's OUT end, for the caller that knows
    the window as a delta rather than as two frames (a retime that
    lengthens or shortens the clip it names); it is applied to the
    BEFORE read-back, so the expectation is derived from what was
    measured rather than from an assumption about the file.

    `record_in` is the record frame the target must read back at, and
    `record_duration` the number of frames it must play there.  A
    caller that knows them states them; a caller that knows only the
    DELTA the change makes has them derived from the before read-back
    (the clip's own record duration plus the delta), so the
    expectation is measured arithmetic rather than an assumption about
    how Resolve numbers a source window - which it reports as the
    INCLUSIVE end on an existing item and the EXCLUSIVE end on a
    freshly appended one.

    `semantics` is `NON_RIPPLE` or `RIPPLE`, and it is REQUIRED: a trim
    that does not say which one it is has not said what it did to the
    rest of the reel.

    `moves` are the items a ripple plan moves, as
    `(row, item_index, record_in, record_out)`.  They are the PLAN's
    arithmetic, handed in rather than recomputed here: this module
    judges a plan against a read-back, it does not make plans.
    """

    row: str
    item_index: int
    source_in: int | None = None
    source_out: int | None = None
    source_delta: int = 0
    record_in: int | None = None
    record_duration: int | None = None
    semantics: str = NON_RIPPLE
    moves: tuple = ()
    preserve_linked_audio: bool = True

    def __post_init__(self) -> None:
        if self.semantics not in (RIPPLE, NON_RIPPLE):
            raise TrimClaimError(
                f"a trim states its semantics as {RIPPLE!r} or "
                f"{NON_RIPPLE!r}, not {self.semantics!r}")
        object.__setattr__(self, "row", str(self.row).upper())
        object.__setattr__(self, "item_index", int(self.item_index))
        object.__setattr__(self, "moves", tuple(self.moves or ()))


@dataclass
class TrimVerdict:
    """What was claimed, what was measured, and what it came to."""

    claims: dict = field(default_factory=dict)

    @property
    def measured(self) -> list:
        return [name for name, row in self.claims.items()
                if row.get("status") == "measured"]

    @property
    def unmeasured(self) -> list:
        return [name for name, row in self.claims.items()
                if row.get("status") == "unmeasured"]

    @property
    def failed(self) -> list:
        return [name for name, row in self.claims.items()
                if row.get("status") == "failed"]

    @property
    def passed(self) -> bool:
        """Whether every CLAIMED check held.

        A claim that was never measured is not a pass: the verdict says
        `unmeasured`, and this says so too.
        """
        return not self.failed and not self.unmeasured

    def to_dict(self) -> dict:
        return {
            "claims": dict(self.claims),
            "measured": self.measured,
            "unmeasured": self.unmeasured,
            "failed": self.failed,
            "passed": self.passed,
        }


def row_label(clip: Mapping) -> str:
    """`V3` / `A1` for a clip, off its own track type and index."""
    track_type = str(clip.get("track_type") or "").lower()
    prefix = "A" if track_type.startswith("a") else "V"
    return f"{prefix}{int(clip.get('track_index') or 0)}"


def is_caption_clip(clip: Mapping) -> bool:
    """Whether this clip is a caption card.

    Judged on the ROW NAME the project's layout gave it, and on the
    clip's own kind: a caption card is a rendered caption, not a piece
    of picture that happens to sit on the caption row.
    """
    name = str(clip.get("track_name") or "").strip().casefold()
    if name not in CAPTION_ROW_NAMES:
        return False
    source_file = str(clip.get("source_file") or "").casefold()
    return ("sub" in source_file or "caption" in source_file
            or str(clip.get("media_type") or "").casefold() == "subtitle")


def _index(clips: Sequence[Mapping]) -> dict:
    """`{(row, index): clip}` for a row of clips."""
    out: dict = {}
    for track in clips:
        row = str(track.get("row") or row_label(track)).upper()
        for index, clip in enumerate(track.get("clips") or ()):
            out[(row, index)] = clip
    return out


def _rows(clips: Sequence[Mapping]) -> list:
    """The clip rows, each carrying its row label."""
    rows = []
    for track in clips:
        row = str(track.get("row") or "").upper()
        if not row:
            row = (f"{'A' if str(track.get('type', '')).lower().startswith('a') else 'V'}"
                   f"{int(track.get('index') or 0)}")
        rows.append({**track, "row": row})
    return rows


def _clip_of(index: Mapping, row: str, item_index: int) -> Mapping | None:
    return index.get((str(row).upper(), int(item_index)))


def _frame(clip: Mapping, key: str) -> int | None:
    """One frame number off a clip, or None when it is unreadable.

    `clip.get(key) or -1` is wrong here: frame ZERO is a real frame,
    and the `or` idiom turns it into the sentinel.  A caption card at
    frame 0 is the first thing on the reel.
    """
    value = clip.get(key)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _same_span(left: Mapping, right: Mapping) -> bool:
    """Whether two clips read back at the same record span."""
    for key in ("record_in", "record_out"):
        first = _frame(left, key)
        second = _frame(right, key)
        if first is None or second is None or first != second:
            return False
    return True


def _claimed(status: str, **detail) -> dict:
    return {"status": status, **detail}


def judge(before: Sequence[Mapping], after: Sequence[Mapping],
          request: TrimRequest) -> TrimVerdict:
    """Judge one applied trim against the read-backs that surround it.

    `before` and `after` are the clip rows of the timeline before and
    after the edit, in the shape `library/tools/reel_read.py` reads
    them: `{"type", "index", "name", "clips": [...]}` per row, each
    clip carrying `record_in`, `record_out`, `source_in_frame`,
    `source_out_frame`, `left_offset`, `right_offset`, `source_frames`,
    `linked_unique_ids` and `linked_clips`.

    The verdict NEVER invents a pass: a claim whose evidence the
    read-backs do not carry reads `unmeasured`.
    """
    before_rows = _rows(before)
    after_rows = _rows(after)
    before_index = _index(before_rows)
    after_index = _index(after_rows)
    target = _clip_of(before_index, request.row, request.item_index)
    claims: dict = {}

    if target is None:
        claims["target_identity"] = _claimed(
            "failed", detail=f"no {request.row}[{request.item_index}] "
                             "in the read-back before the edit")
        return TrimVerdict(claims)

    requested_in = (int(target["source_in_frame"])
                    if request.source_in is None else int(request.source_in))
    requested_out = (int(target["source_out_frame"])
                     if request.source_out is None else int(request.source_out))
    if (request.source_in is None and request.source_out is None
            and request.source_delta):
        # The caller knows the window as a DELTA on the clip it named;
        # the expectation is derived from the before read-back, so it
        # is measured arithmetic rather than an assumption about how
        # the file numbers its frames.
        requested_out = int(target["source_out_frame"]) + int(
            request.source_delta)
    after_target = _clip_of(after_index, request.row, request.item_index)
    if after_target is None:
        # The trim REPLACED the item (Resolve assigns a new id when a
        # clip is re-placed), so the target is found by its source file
        # and its new span, not by its id.
        after_target = _find_replacement(after_rows, target,
                                         requested_in, requested_out)
    if after_target is None:
        claims["target_identity"] = _claimed(
            "failed", detail="the trimmed item does not read back on "
                             f"{request.row} at the requested source window")
        return TrimVerdict(claims)

    claims["target_identity"] = _claimed(
        "measured", detail="the trimmed item reads back at the requested "
                           "source window")

    # ── handle validation ──────────────────────────────────────────
    claims["handle_validation"] = _judge_handles(target, requested_in,
                                                  requested_out)

    # ── source-frame precision ─────────────────────────────────────
    measured_in = after_target.get("source_in_frame")
    measured_out = after_target.get("source_out_frame")
    if measured_in is None or measured_out is None:
        claims["source_frame_precision"] = _claimed(
            "unmeasured", detail="the read-back carries no source frames")
    else:
        # Resolve reports the source end of an EXISTING item as the
        # inclusive end and of a FRESHLY APPENDED one as the exclusive
        # end, so a measured end one frame past the requested one is the
        # same window read in the other convention - and one frame more
        # than that is not.
        ok = (int(measured_in) == requested_in
              and int(measured_out) in (requested_out, requested_out + 1))
        claims["source_frame_precision"] = _claimed(
            "measured" if ok else "failed",
            requested=[requested_in, requested_out],
            measured=[int(measured_in), _inclusive(measured_out)],
            detail="the source window reads back at the requested frames")

    # ── record-frame precision ─────────────────────────────────────
    record_in = after_target.get("record_in")
    record_out = after_target.get("record_out")
    if record_in is None or record_out is None:
        claims["record_frame_precision"] = _claimed(
            "unmeasured", detail="the read-back carries no record frames")
    else:
        if request.record_duration is not None:
            expected_duration = int(request.record_duration)
        elif (request.source_in is not None
                and request.source_out is not None):
            expected_duration = (int(request.source_out)
                                 - int(request.source_in) + 1)
        else:
            expected_duration = (
                int(target["record_out"]) - int(target["record_in"])
                + int(request.source_delta))
        measured_duration = int(record_out) - int(record_in)
        ok = measured_duration == expected_duration
        if request.record_in is not None:
            ok = ok and int(record_in) == int(request.record_in)
        claims["record_frame_precision"] = _claimed(
            "measured" if ok else "failed",
            requested_record_in=(int(request.record_in)
                                 if request.record_in is not None
                                 else int(record_in)),
            measured=[int(record_in), int(record_out)],
            requested_duration=expected_duration,
            measured_duration=measured_duration,
            detail="the record span's duration equals the source window's")

    # ── ripple / non-ripple, as SEPARATE claims ────────────────────
    if request.semantics == RIPPLE:
        claims["ripple_semantics"] = _judge_ripple(
            before_rows, after_rows, request)
        claims["non_ripple_semantics"] = _claimed(
            "not_claimed", detail="this edit is a ripple; the non-ripple "
                                  "claim does not apply to it")
    else:
        claims["non_ripple_semantics"] = _judge_non_ripple(
            before_rows, after_rows, request)
        claims["ripple_semantics"] = _claimed(
            "not_claimed", detail="this edit is not a ripple; the ripple "
                                  "claim does not apply to it")

    # ── linked audio ───────────────────────────────────────────────
    claims["linked_audio_preserved"] = _judge_linked_audio(
        before_rows, after_rows, target, after_target, request)

    # ── caption tracks ─────────────────────────────────────────────
    claims["caption_timing"] = _judge_captions(before_rows, after_rows,
                                                request, target)

    return TrimVerdict(claims)


def _inclusive(value) -> int:
    """A source end as an INCLUSIVE frame.

    Resolve reports the source end of a freshly appended item as the
    EXCLUSIVE end and the source end of an existing item as the
    INCLUSIVE one.  A trim's window is inclusive at both ends, so a
    measured end one frame past the requested one is the same window
    read in the other convention - and one frame MORE than that is not.
    """
    return int(value)


def _find_replacement(after_rows: Sequence[Mapping], target: Mapping,
                      requested_in: int, requested_out: int):
    """The item that took the target's place, when the id changed.

    A re-placed item keeps its SOURCE FILE and takes the requested
    source window; that pair identifies it among the items on its row.
    """
    row = row_label(target)
    for track in after_rows:
        if str(track.get("row") or "").upper() != row:
            continue
        for clip in track.get("clips") or ():
            if (str(clip.get("source_file") or "")
                    != str(target.get("source_file") or "")):
                continue
            measured_in = clip.get("source_in_frame")
            measured_out = clip.get("source_out_frame")
            if measured_in is None or measured_out is None:
                continue
            if (int(measured_in) == requested_in
                    and int(measured_out) in (requested_out,
                                              requested_out + 1)):
                return clip
    return None


def _judge_handles(target: Mapping, requested_in: int,
                   requested_out: int) -> dict:
    """The requested window must lie inside the MEASURED bounds."""
    measured_in = target.get("source_in_frame")
    measured_out = target.get("source_out_frame")
    if measured_in is None or measured_out is None:
        return _claimed("unmeasured",
                        detail="the clip's source range is unreadable")
    inside = (int(measured_in) <= requested_in
              and requested_out <= _inclusive(measured_out))
    frames = target.get("source_frames")
    file_bounds = None
    if frames is not None and target.get("left_offset") is not None:
        file_bounds = (int(target["left_offset"]),
                       int(target["left_offset"]) + int(frames))
        inside = inside and (file_bounds[0] <= requested_in
                             and requested_out < file_bounds[1])
    if requested_out < requested_in:
        return _claimed("failed",
                        detail="the requested source window is empty")
    return _claimed(
        "measured" if inside else "failed",
        requested=[requested_in, requested_out],
        clip_source_range=[int(measured_in), _inclusive(measured_out)],
        file_bounds=list(file_bounds) if file_bounds else None,
        detail="the requested window lies inside the clip's measured "
               "source bounds")


def _judge_ripple(before_rows: Sequence[Mapping],
                  after_rows: Sequence[Mapping],
                  request: TrimRequest) -> dict:
    """Every item the plan moved reads back where the plan put it."""
    before_index = _index(before_rows)
    after_index = _index(after_rows)
    moved = {(str(row).upper(), int(index)): (int(record_in),
                                             int(record_out))
             for row, index, record_in, record_out in request.moves}
    wrong = []
    missing = []
    for key, (record_in, record_out) in sorted(moved.items()):
        clip = after_index.get(key)
        if clip is None:
            missing.append(f"{key[0]}[{key[1]}]")
            continue
        measured_in = _frame(clip, "record_in")
        measured_out = _frame(clip, "record_out")
        if (measured_in != record_in or measured_out != record_out):
            wrong.append({
                "item": f"{key[0]}[{key[1]}]",
                "planned": [record_in, record_out],
                "measured": [measured_in, measured_out],
            })
    unmoved = []
    for key, clip in sorted(before_index.items()):
        if key in moved or key == (request.row, request.item_index):
            continue
        after_clip = after_index.get(key)
        if after_clip is None:
            continue
        if not _same_span(clip, after_clip):
            unmoved.append(f"{key[0]}[{key[1]}]")
    ok = not wrong and not missing and not unmoved
    return _claimed(
        "measured" if ok else "failed",
        moved=len(moved),
        wrong=wrong,
        missing=missing,
        moved_without_asking=unmoved,
        detail="every item the ripple moved reads back at its planned "
               "span, and every other item reads back where it was")


def _judge_non_ripple(before_rows: Sequence[Mapping],
                      after_rows: Sequence[Mapping],
                      request: TrimRequest) -> dict:
    """Nothing but the target moved."""
    before_index = _index(before_rows)
    after_index = _index(after_rows)
    target_key = (request.row, request.item_index)
    moved = []
    for key, clip in sorted(before_index.items()):
        if key == target_key:
            continue
        after_clip = after_index.get(key)
        if after_clip is None:
            moved.append({"item": f"{key[0]}[{key[1]}]", "why": "absent"})
            continue
        if not _same_span(clip, after_clip):
            moved.append({"item": f"{key[0]}[{key[1]}]",
                          "why": "moved"})
    return _claimed(
        "measured" if not moved else "failed",
        moved=moved,
        detail="nothing but the trimmed item moved; the frames it "
               "vacated are a gap the caller owns")


def _judge_linked_audio(before_rows: Sequence[Mapping],
                        after_rows: Sequence[Mapping],
                        target: Mapping, after_target: Mapping,
                        request: TrimRequest) -> dict:
    """The target's linked partners read back as the same partners.

    A linked partner's RECORD span may move when the plan moves it - a
    ripple carries the sound with the picture - so the span is judged
    against the PLAN.  What must not change is the partner's identity,
    its source window and its enablement: a picture that keeps its
    sound must keep the SAME sound.
    """
    if not request.preserve_linked_audio:
        return _claimed("not_claimed",
                        detail="this request does not promise linked audio")
    partners = [str(value) for value in
                (target.get("linked_unique_ids") or ())]
    if not partners:
        return _claimed("measured", partners=[],
                        detail="the trimmed item carries no linked partner")
    before_index = _index(before_rows)
    after_index = _index(after_rows)
    before_by_id = {str(clip.get("unique_id") or ""): clip
                    for clip in before_index.values()}
    after_by_id = {str(clip.get("unique_id") or ""): clip
                   for clip in after_index.values()}
    planned = {(str(row).upper(), int(index)): (int(record_in),
                                                int(record_out))
               for row, index, record_in, record_out in request.moves}
    problems = []
    for partner_id in partners:
        before_partner = before_by_id.get(partner_id)
        after_partner = after_by_id.get(partner_id)
        if after_partner is None:
            problems.append({"partner": partner_id, "why": "absent"})
            continue
        if before_partner is not None:
            for field_name in ("source_in_frame", "source_out_frame",
                               "enabled"):
                if (before_partner.get(field_name)
                        != after_partner.get(field_name)):
                    problems.append({
                        "partner": partner_id,
                        "field": field_name,
                        "before": before_partner.get(field_name),
                        "after": after_partner.get(field_name),
                    })
        expected = None
        for key, clip in before_index.items():
            if str(clip.get("unique_id") or "") != partner_id:
                continue
            expected = planned.get(key)
            if expected is None:
                expected = (_frame(clip, "record_in"),
                            _frame(clip, "record_out"))
        if expected is not None:
            measured_in = _frame(after_partner, "record_in")
            measured_out = _frame(after_partner, "record_out")
            if (measured_in != expected[0]
                    or measured_out != expected[1]):
                problems.append({
                    "partner": partner_id,
                    "field": "record_span",
                    "planned": list(expected),
                    "measured": [measured_in, measured_out],
                })
    after_partners = {str(value) for value in
                      (after_target.get("linked_unique_ids") or ())}
    lost = sorted(set(partners) - after_partners)
    if lost:
        problems.append({"why": "the link did not survive the edit",
                         "partners": lost})
    return _claimed(
        "measured" if not problems else "failed",
        partners=partners,
        problems=problems,
        detail="every linked partner reads back as the same partner, with "
               "the same source window and the same enablement")


def _judge_captions(before_rows: Sequence[Mapping],
                    after_rows: Sequence[Mapping],
                    request: TrimRequest,
                    target: Mapping) -> dict:
    """Every caption card reads back at its planned span, and none overlap.

    Two things about a caption row are said out loud, because neither is
    visible in the timeline:

    `words_removed` - the cards whose WORDS fall inside the frames the
    edit takes out.  Their text is stale whatever the ripple does: the
    words are gone, and a caption card is a RENDERED file, so nothing
    short of a re-render can fix them.

    `left_behind` - the cards the plan did NOT move although the words
    they carry moved with the picture.  A caller that excluded them
    owns the cost, and the receipt has to say so.
    """
    before_index = _index(before_rows)
    after_index = _index(after_rows)
    caption_keys = {key for key, clip in before_index.items()
                    if is_caption_clip(clip)}
    if not caption_keys:
        return _claimed("measured", cards=0,
                        detail="this reel carries no caption card")
    planned = {(str(row).upper(), int(index)): (int(record_in),
                                                int(record_out))
               for row, index, record_in, record_out in request.moves}
    wrong = []
    for key in sorted(caption_keys):
        after_clip = after_index.get(key)
        if after_clip is None:
            wrong.append({"item": f"{key[0]}[{key[1]}]", "why": "absent"})
            continue
        expected = planned.get(key)
        if expected is None:
            expected = (_frame(before_index[key], "record_in"),
                        _frame(before_index[key], "record_out"))
        measured_in = _frame(after_clip, "record_in")
        measured_out = _frame(after_clip, "record_out")
        if (measured_in != expected[0] or measured_out != expected[1]):
            wrong.append({
                "item": f"{key[0]}[{key[1]}]",
                "planned": list(expected),
                "measured": [measured_in, measured_out],
            })
    overlaps = []
    by_row: dict = {}
    for key in sorted(caption_keys):
        after_clip = after_index.get(key)
        if after_clip is None:
            continue
        by_row.setdefault(key[0], []).append(
            (_frame(after_clip, "record_in") or 0,
             _frame(after_clip, "record_out") or 0,
             f"{key[0]}[{key[1]}]"))
    for row, spans in sorted(by_row.items()):
        spans.sort()
        for first, second in zip(spans, spans[1:]):
            if second[0] < first[1]:
                overlaps.append({
                    "row": row,
                    "first": f"{first[2]} @{first[0]}..{first[1]}",
                    "second": f"{second[2]} @{second[0]}..{second[1]}",
                })
    removed = _removed_frames(target, request)
    words_removed = []
    left_behind = []
    cut = int(target.get("record_out") or 0)
    for key in sorted(caption_keys):
        clip = before_index[key]
        start = _frame(clip, "record_in")
        end = _frame(clip, "record_out")
        if start is None or end is None:
            continue
        if removed and start < removed[1] and removed[0] < end:
            words_removed.append({"item": f"{key[0]}[{key[1]}]",
                                  "span": [start, end]})
        elif (key not in planned and start >= cut
              and int(request.source_delta or 0) != 0):
            left_behind.append({"item": f"{key[0]}[{key[1]}]",
                                "span": [start, end]})
    ok = not wrong and not overlaps
    return _claimed(
        "measured" if ok else "failed",
        cards=len(caption_keys),
        wrong=wrong,
        overlaps=overlaps,
        words_removed=words_removed,
        left_behind=left_behind,
        detail="every caption card reads back at its planned span, and no "
               "two cards on a row overlap")


def _removed_frames(target: Mapping,
                    request: TrimRequest) -> tuple | None:
    """The frames the edit takes OUT of the target, if it takes any.

    A change that shortens the clip removes the frames at its tail: the
    clip plays fewer frames, and the ones it no longer plays are the
    ones it used to play last.
    """
    delta = int(request.source_delta or 0)
    if delta >= 0:
        return None
    end = _frame(target, "record_out")
    start = _frame(target, "record_in")
    if end is None or start is None:
        return None
    return (end + delta, end)


__all__ = [
    "RIPPLE", "NON_RIPPLE", "TrimRequest", "TrimVerdict", "TrimClaimError",
    "judge", "is_caption_clip", "row_label",
]
