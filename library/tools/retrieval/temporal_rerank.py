"""A candidate INTERVAL has to establish the query, not merely resemble it.

**A ranked frame is a candidate, and the punch list's question is what
turns a candidate into an answer.**  The frame lane ranks sampled frames
by how much they resemble the query text, and hands the editor the top
of that ranking.  That is enough for "a laptop on a desk" and it is not
enough for "the moment someone opens the laptop": CLIP cannot tell a
closed lid from an open one, and it cannot tell a frame that CONTAINS a
laptop from the frame in which the laptop is being OPENED.  A ranking
that answers the second question with the first is not a weak answer -
it is a wrong one, and it is wrong in the way an editor notices.

So this module is the second half of the visual lane:

* **CLASSIFY** the query - does it need appearance matching, action
  recognition, temporal order, identity, or object relationships?
* **INSPECT** the candidate intervals the cheap index proposed, using
  the SOURCE MEMORY's own frame sample - the index says WHERE to look,
  the memory says what is there;
* **REJECT** a candidate whose evidence does not establish the requested
  thing, and
* **REPORT UNCERTAIN** when the verification does not establish the
  claim either way - an unparseable answer, a yes with no timestamp
  for an edit-critical event - never dressed up as a rejection, and
* **REFINE** an established event to the timestamps the evidence
  covers, beside the shot window the pass presents, and
* **JUSTIFY** the interval that is presented, by the verdict that
  established it.

**The classification is derived, not parsed twice.**  `requirements
.decompose` already names what a query asks for, in a closed and
auditable table; this module maps those requirement kinds onto the five
kinds the punch list names, and adds nothing of its own.  Two
classifiers would be two vocabularies to keep in step, and the
decomposition's is the one the rest of the lane already reads.

**The statement asked of the model is the editor's own words.**  The
framing words ("a shot of", "the moment") are stripped and what is left
is the claim: "the open laptop on the desk", "someone opens the
laptop".  A claim is what a verdict can be about; a query string is not.

**The cost is recorded, because it is a cost.**  The pass calls a local
vision model, and the report says how many calls it made, how many
came from the recorded verdicts, how many seconds they took and which
model answered - the incremental cost of the pass, next to what it
bought.  A pass whose cost is invisible is a pass nobody can decide to
keep.

**What this module does NOT do.**  It does not build an index, it does
not decode anything the source memory has not already decoded, and it
does not answer a question one interval cannot answer.  Temporal order
and object relationships are two of those: they are claims about two
things, and this pass reports that it cannot establish them from one
interval and names the lane that owns them instead.
"""

from __future__ import annotations

import re
import time
from collections.abc import Mapping, Sequence

from library.tools.retrieval import requirements
from library.tools.retrieval import semantic_verify as _semantic_verify
from library.tools.retrieval.requirements import decompose

KINDS = (
    "appearance_matching",
    "action_recognition",
    "temporal_order",
    "identity",
    "object_relationships",
)
"""The five kinds of evidence a visual query can need.

They are the punch list's own words.  A query is classified into the
kinds it names, and the kinds decide what the temporal pass may claim:
an interval can establish appearance, an action and an identity; it
cannot establish an ORDER or a RELATIONSHIP, which are claims about two
things at once.
"""

#: Which VI-003 kind each requirement kind implies.  The decomposition's
#: kinds are about WHAT is claimed; these are about WHAT EVIDENCE
#: establishes it, and the table is the only place the two meet.
REQUIREMENT_KIND_TO_KIND = {
    "visual:object": "appearance_matching",
    "visual:state": "appearance_matching",
    "visual:scene": "appearance_matching",
    "visual:composition": "appearance_matching",
    "visual:person": "identity",
    "visual:action": "action_recognition",
    "temporal:ordering": "temporal_order",
    "relational:same_subject": "object_relationships",
    "relational:shot_relation": "object_relationships",
    "relational:cross_clip": "object_relationships",
}
"""The requirement kind -> the evidence kind it needs."""

#: The kinds one interval can establish.  The other two are claims about
#: two things, and no single interval carries them.
INTERVAL_KINDS = ("appearance_matching", "action_recognition", "identity")

#: The kinds that need more than one interval, and the lane that owns
#: each of them.  The pass names the lane rather than answering them
#: badly.
MULTI_INTERVAL_LANES = {
    "temporal_order": "the timeline (the time lane)",
    "object_relationships": "the project graph (a named relation query)",
}

FRAMING_PREFIXES = (
    "a shot of ", "the shot of ", "a clip of ", "the clip of ",
    "a footage of ", "the footage of ", "some footage of ",
    "a moment of ", "the moment ", "a part where ", "the part where ",
    "a bit where ", "the bit where ", "a scene where ", "the scene where ",
    "a passage where ", "the passage where ",
)
"""The words that frame a query rather than name what is in the picture.

They are stripped before the statement is built, because "a shot of the
open laptop" is a request for a picture and "the open laptop" is the
claim a verdict can be about.  The list is closed, like every other cue
table in this lane: the reason a statement reads the way it does is
visible here.
"""

EVENT_HALF_WINDOW_S = 2.0
"""How far either side of a sighting an EVENT candidate is inspected on.

An action lives in seconds, not in the twenty seconds a shot lives in.
A candidate for "someone opens the laptop" is therefore inspected on the
seconds around the sighting that proposed it - the event's own
neighbourhood - and not spread across the whole shot the sighting sits
in.  A wider inspection would ask the model about footage the event is
not in, and the frames spread across a shot would step over a gesture
that lasts a second.
"""

MAX_FRAMES_PER_INTERVAL = 4
"""Frames shown per candidate interval."""

CANDIDATES_PER_CLIP = 3
"""How many candidates of one camera may be inspected.

Three is the number of shots an editor will look at before they decide
the camera is not it, and it bounds the cost of one query.
"""

MAX_CANDIDATES = 24
"""How many candidates one query may inspect, whatever the cameras say."""

MAX_WINDOWS_PER_SOURCE = 3
"""How many candidate windows ONE source may hold in the answer.

Three is the number of shots an editor will look at before they decide
the camera is not it, and it is the number of DISTINCT MOMENTS one
source may be inspected at (`CANDIDATES_PER_CLIP`).  A source that the
evidence establishes at one moment holds one window; a source that the
evidence establishes at several moments may hold several, because the
thing the editor asked for is THERE, AGAIN AND AGAIN, and one window of
it is one sighting of a passage the editor has to scrub.
"""

MOMENTS_PER_SOURCE = 2
"""How many distinct moments of one source the pass inspects.

Two: the moment the source was asked about, and the next one that shows
it AGAIN.  The second is what makes the answer's extra slot for a
source EVIDENCE rather than a guess - the pass puts the second moment
to the model, and the slot is taken only if the model establishes the
query there too.  It is also the cost: one extra model call per source
that holds a slot.
"""

EVENT_GAP_S = 15.0
"""Two moments of one source further apart than this are two events.

The lane's merge gap (`library.tools.analysis.footage_frames.MERGE_GAP_S`),
the same number: the gap is the tolerance for ONE missed extraction, so
two sightings that close are the same sighting.  The pass cannot import
the lane's constant (the lane imports this module), so the number is
declared here and the lane's value is passed in by the caller.
"""

MIN_INTERVAL_FRAMES = 2
"""A candidate shown one frame is not an inspection.

A sighting the pool found on its own is inspected with the frames
around it, so the verdict is about a stretch of footage and not about
one still.  The WINDOW the pass presents is still the shot the lane
proposed: the pass selects and justifies, it does not re-shape the
lane's geometry (and a window of no length is the lane's and the
metric's business, not this pass's).
"""


def strip_framing(query: str) -> str:
    """The query without the words that frame it.

    "a shot of the open laptop on the desk" -> "the open laptop on the
    desk".  The framing words say HOW the editor wants it; the rest is
    the claim.
    """
    text = (query or "").strip()
    changed = True
    while changed:
        changed = False
        for prefix in FRAMING_PREFIXES:
            if text.lower().startswith(prefix):
                text = text[len(prefix):].strip()
                changed = True
                break
    return text or (query or "").strip()


CONNECTIVE_TAIL = ("and", "with", "while", "when", "as", "during", "after",
                    "before", "but", "plus", "also", "then", "finally")
"""The words that join two clauses.

A clause the decomposition did not claim as visual is removed from the
claim, and what it leaves behind can be a dangling connective ("a person
gesturing with").  The connective is not part of the claim, so it goes.
"""


def visual_claim(query: str, plan=None) -> str:
    """The VISUAL claim of the query, in the editor's own words.

    The claim is the query without the words that frame it and without
    the APPOSITION the editor added to name the thing they just
    described.  "the shot of the woman wearing glasses, the host" is
    asked on the visual lane: the picture asked for is "the woman
    wearing glasses", and "the host" is the editor's label for her,
    which no frame can establish and which would only make the model
    argue about who is hosting.

    An apposition is a clause the decomposition did not claim for the
    picture, set off by a comma.  A clause joined by "with" or "while"
    is part of the claim and stays: "the table with the plant behind
    it" asks for both.
    """
    plan = plan or decompose(query)
    claimed = {clause.strip() for clause in requirements.visual_clauses(query)}
    text = query
    pairs = requirements.clauses_with_connectives(query)
    for index, (clause, _connective) in enumerate(pairs):
        if index == 0:
            continue
        preceding = pairs[index - 1][1]
        if preceding not in (",", ";"):
            continue
        stripped = clause.strip()
        if stripped and stripped not in claimed:
            text = text.replace(stripped, " ")
    text = re.sub(r"\s+", " ", text).strip()
    text = strip_framing(text)
    words = text.split()
    while words and words[-1].lower().strip(",.;") in CONNECTIVE_TAIL:
        words.pop()
    return " ".join(words).strip(" ,;.")


def names_event(plan) -> bool:
    """Does the claim name something that HAPPENS?

    An action with a STATE beside it is a picture of a thing - "the open
    laptop on the desk" names a state, and a state is in the frame.  An
    action with no state beside it is a claim about time - "someone opens
    the laptop" - and a single frame cannot establish it, so the
    candidate is inspected on the seconds around the sighting instead of
    across the whole shot.
    """
    kinds = {requirement.key for requirement in plan.requirements}
    return "visual:action" in kinds and "visual:state" not in kinds


def classify(query: str) -> dict:
    """What the query needs, and whether one interval can establish it.

    The kinds are read off the decomposition, so a reader can check the
    classification against the cues that fired.  The PRIMARY kind is the
    one that decides how a candidate is judged: an action is judged on
    the event's neighbourhood, everything else on the shot.
    """
    plan = decompose(query)
    kinds: list[str] = []
    keys: list[str] = []
    for requirement in plan.requirements:
        kind = REQUIREMENT_KIND_TO_KIND.get(requirement.key)
        if kind is None or kind not in KINDS:
            continue
        if requirement.key not in keys:
            keys.append(requirement.key)
        if kind not in kinds:
            kinds.append(kind)
    # An ACTION is the most demanding kind an interval can be judged on,
    # and an IDENTITY claim is the next: both are claims about more than
    # a still.  Appearance is what is left when nothing else fires.
    primary = "appearance_matching"
    for kind in ("action_recognition", "identity", "temporal_order",
                 "object_relationships", "appearance_matching"):
        if kind in kinds:
            primary = kind
            break
    multi = [kind for kind in kinds if kind not in INTERVAL_KINDS]
    return {
        "query": query,
        "kinds": tuple(kinds),
        "primary": primary,
        "requirement_keys": tuple(keys),
        "statement": visual_claim(query, plan),
        "event": names_event(plan),
        "interval_evidence": not multi,
        "multi_interval_kinds": tuple(multi),
        "owner_lanes": {kind: MULTI_INTERVAL_LANES[kind] for kind in multi},
    }


def _num(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _spread(frames: Sequence[Mapping], limit: int) -> list[dict]:
    """Up to `limit` frames spread across `frames`, ends included.

    The ends are always kept: an interval is judged on what is at its
    beginning and at its end, and dropping either end would judge a
    stretch of footage the verdict never saw.
    """
    ordered = sorted(frames, key=lambda f: f["t"])
    if len(ordered) <= limit:
        return list(ordered)
    picked = [ordered[0]]
    span = len(ordered) - 1
    for step in range(1, limit - 1):
        picked.append(ordered[round(span * step / (limit - 1))])
    picked.append(ordered[-1])
    seen, out = set(), []
    for frame in picked:
        if frame["t"] in seen:
            continue
        seen.add(frame["t"])
        out.append(frame)
    return out


def _nearest(frames: Sequence[Mapping], t: float, limit: int) -> list[dict]:
    """The `limit` frames nearest to `t`, in time order."""
    ordered = sorted(frames, key=lambda f: abs(f["t"] - t))[:limit]
    return sorted(ordered, key=lambda f: f["t"])


def is_a_different_event(entry: Mapping, chosen: Sequence[Mapping],
                         event_gap_s: float = EVENT_GAP_S) -> bool:
    """Is this moment of one source a DIFFERENT event from the ones chosen?

    The lane's own test (`library.tools.analysis.footage_frames.
    _is_a_different_event`), spelled here so this module can choose the
    moments it inspects without importing the lane: a moment further
    apart than the gap from ANY of the chosen ones is its own event.
    """
    start = _num(entry.get("start"))
    end = _num(entry.get("end"))
    if start is None or end is None:
        return False
    for other in chosen:
        other_start = _num(other.get("start"))
        other_end = _num(other.get("end"))
        if other_start is None or other_end is None:
            continue
        gap = max(start - other_end, other_start - end)
        if gap > event_gap_s:
            return True
    return False


def candidate_intervals(ranges: Sequence[Mapping], classification: Mapping,
                        frames_by_clip: Mapping[str, Sequence[Mapping]],
                        per_clip: int = CANDIDATES_PER_CLIP,
                        event_gap_s: float = EVENT_GAP_S
                        ) -> dict[str, list[dict]]:
    """The candidate intervals the pass may inspect, per camera.

    **The candidates of one camera are DISTINCT MOMENTS of it.**  The
    shots the lane merged are slices of a passage, and three adjacent
    slices of one passage are ONE sighting - the lane's merge gap says
    so, and a pass that inspects three of them puts the same question
    to the model three times and learns nothing.  So the candidates are
    the camera's best moment, and then its best moments that are a
    DIFFERENT event from the ones already chosen: the moments at which
    the camera shows the thing AGAIN.

    The WINDOW the pass presents is that shot, UNCHANGED - the pass selects
    and justifies, it does not re-shape the lane's geometry.  The
    WINDOW the pass presents is that shot, UNCHANGED - the pass selects
    and justifies, it does not re-shape the lane's geometry.  A shot of
    no length stays a shot of no length: the pass says which frames it
    inspected and the verdict that established them, and the window
    keeps the length the lane gave it.  (Whether a window of no length
    is the right window is the lane's and the metric's business, not
    this pass's: `library/tools/retrieval/regions.py` counts a window
    only when it OVERLAPS the wanted span.)

    The FRAMES inspected follow the claim.  A claim about what the shot
    shows is inspected across the shot, so the model sees the whole
    thing; a claim about something HAPPENING is inspected on the seconds
    around the sighting, because an event lives in seconds and frames
    spread across a shot step over it.
    """
    event = bool(classification.get("event"))
    by_clip: dict[str, list[Mapping]] = {}
    for entry in ranges or ():
        by_clip.setdefault(entry.get("clip_id"), []).append(entry)
    out: dict[str, list[dict]] = {}
    for clip_id, entries in by_clip.items():
        entries = sorted(entries,
                         key=lambda e: -(_num(e.get("best_score")) or 0.0))
        chosen: list[Mapping] = []
        for entry in entries:
            if len(chosen) >= per_clip:
                break
            if chosen and not is_a_different_event(entry, chosen, event_gap_s):
                continue
            chosen.append(entry)
        candidates = []
        for entry in chosen:
            start = _num(entry.get("start"))
            end = _num(entry.get("end"))
            if start is None:
                continue
            frames = list(frames_by_clip.get(clip_id) or ())
            if not frames:
                candidates.append({
                    "clip_id": clip_id, "filename": entry.get("filename", ""),
                    "start": start, "end": end,
                    "score": _num(entry.get("best_score")),
                    "timecode": entry.get("timecode"),
                    "frames": [],
                    "reason": "no extracted frame to inspect",
                })
                continue
            inside = [f for f in frames
                      if start - 1e-6 <= f["t"] <= end + 1e-6]
            if event:
                peak = _num(entry.get("peak_t"))
                if peak is None:
                    peak = start
                low = peak - EVENT_HALF_WINDOW_S
                high = peak + EVENT_HALF_WINDOW_S
                dense = [f for f in frames if low <= f["t"] <= high]
                shown = _spread(dense or inside, MAX_FRAMES_PER_INTERVAL)
            elif len(inside) < MIN_INTERVAL_FRAMES:
                shown = _nearest(frames, start, MAX_FRAMES_PER_INTERVAL)
            else:
                shown = _spread(inside, MAX_FRAMES_PER_INTERVAL)
            candidates.append({
                "clip_id": clip_id,
                "filename": entry.get("filename", ""),
                "start": start, "end": end,
                "score": _num(entry.get("best_score")),
                "timecode": entry.get("timecode"),
                "frames": [{"t": f["t"], "file": f["file"]} for f in shown],
            })
        out[clip_id] = candidates
    return out


def select(candidates: Sequence[Mapping], top_ranges: int,
           per_source: int = 1, different_event=None,
           max_per_source: int = MAX_WINDOWS_PER_SOURCE,
           source_durations: Mapping[str, float] | None = None) -> list[dict]:
    """The windows the pass presents, and why each one is there.

    **The camera that holds the thing ACROSS ITS RECORDING comes
    first.**  The lane's rule was one window per camera, and it stays a
    RULE: a camera holds one window for one moment.  What changed is
    what a camera that the evidence establishes at SEVERAL moments is
    worth.  The evidence - the verdicts, not the similarity score - says
    how many moments a camera holds, and a camera that holds the thing
    across a SHARE OF ITS OWN RECORDING may take more than one of the
    editor's slots: the editor asked for a passage and scrubs a
    passage, and a camera that shows the thing once in an hour does not
    hold one.

    The order the cameras are served in is therefore the number of
    moments they hold, then the share of their own recording those
    moments span, and the score breaks the last tie.  The leftovers - a
    slot the cameras with moments did not fill - go to the best
    remaining established moment, as before.

    A window is presented only when the pass established it.  A
    candidate whose evidence did not establish the requested thing is
    not offered with a lower rank: it is not offered.
    """
    verified = sorted(
        (c for c in candidates if c.get("verdict") == "yes"),
        key=lambda c: -(_num(c.get("score")) or 0.0))
    limit = max(0, top_ranges)
    cap = max(1, per_source)
    durations = source_durations or {}
    by_clip: dict[str, list[dict]] = {}
    for candidate in verified:
        by_clip.setdefault(candidate["clip_id"], []).append(candidate)
    order = sorted(
        by_clip,
        key=lambda clip: (-len(by_clip[clip]),
                          -_span_share(by_clip[clip], durations.get(clip)),
                          -(_num(by_clip[clip][0].get("score")) or 0.0)))
    picked: list[dict] = []
    seen: dict = {}
    for clip_id in order:
        taken = 0
        allowance = min(max_per_source, max(cap, len(by_clip[clip_id])))
        for candidate in by_clip[clip_id]:
            if taken >= allowance:
                break
            if taken and different_event is not None and not different_event(
                    candidate, picked, clip_id):
                continue
            seen[clip_id] = taken + 1
            taken += 1
            picked.append(candidate)
            if len(picked) >= limit:
                return _by_score(picked)
    for candidate in verified:
        if len(picked) >= limit:
            break
        if any(candidate is other for other in picked):
            continue
        if different_event is not None and not different_event(
                candidate, picked, candidate["clip_id"]):
            continue
        picked.append(candidate)
    return _by_score(picked)


def _by_score(picked: Sequence[Mapping]) -> list[dict]:
    """The windows in the order the editor reads them: best first.

    The editor's first pick is the first window, so the order is the
    SCORE order - the lane never changed that, and the order the
    cameras were served in is not the order the editor is shown.
    """
    return sorted(picked, key=lambda c: -(_num(c.get("score")) or 0.0))


def _span_share(entries: Sequence[Mapping], duration: float | None) -> float:
    """The share of a source's own recording its established moments span.

    A camera whose moments are established across most of its recording
    shows the thing throughout it; a camera with two moments in an hour
    of take does not.  The share is what tells them apart, and it is
    measured from the moments the evidence established.
    """
    if not entries or not duration:
        return 0.0
    starts = [_num(e.get("start")) for e in entries]
    ends = [_num(e.get("end")) for e in entries]
    starts = [s for s in starts if s is not None]
    ends = [e for e in ends if e is not None]
    if not starts or not ends:
        return 0.0
    span = max(ends) - min(starts)
    return max(0.0, min(1.0, span / float(duration)))


def _represented(candidates: Sequence[Mapping], top_ranges: int,
                 per_source: int, different_event,
                 source_durations: Mapping[str, float] | None = None) -> set:
    """The cameras that hold a slot in the answer as it stands."""
    return {c.get("clip_id") for c in
            select(candidates, top_ranges, per_source=per_source,
                   different_event=different_event,
                   source_durations=source_durations)}


def _settled(candidates: Sequence[Mapping], per_camera: Mapping,
             top_ranges: int, per_source: int, different_event,
             source_durations: Mapping[str, float] | None = None) -> bool:
    """Is the answer settled: full, and every camera in it judged again?

    The pass stops when the editor's list is full AND every camera that
    holds a slot has been judged at its second moment - the moment
    that shows the query AGAIN.  A camera that holds no slot is not
    judged again: its first moment was judged and it did not earn one.
    """
    picked = select(candidates, top_ranges, per_source=per_source,
                    different_event=different_event,
                    source_durations=source_durations)
    if len(picked) < top_ranges:
        return False
    for clip_id in {c.get("clip_id") for c in picked}:
        entries = per_camera.get(clip_id) or []
        want = min(MOMENTS_PER_SOURCE, len(entries))
        got = sum(1 for c in candidates if c.get("clip_id") == clip_id)
        if got < want:
            return False
    return True


def rerank(*, project_folder, query: str, ranges: Sequence[Mapping],
           frames_by_clip: Mapping[str, Sequence[Mapping]],
           top_ranges: int = 5, classification: Mapping | None = None,
           verify=None, per_clip: int = CANDIDATES_PER_CLIP,
           max_candidates: int = MAX_CANDIDATES, per_source: int = 1,
           event_gap_s: float = EVENT_GAP_S,
           source_durations: Mapping[str, float] | None = None,
           different_event=None) -> dict:
    """Inspect the candidate intervals and present the justified ones.

    **Cameras are visited round robin, and each of them is judged at the
    moments that show the query AGAIN.**  One camera's best three shots
    are not more informative than the other cameras' best shots, and a
    query that is answered by the first camera inspected must not cost
    three calls per camera to find out.  So the candidates are inspected
    one per camera at a time; and then the cameras that hold a slot are
    judged at their SECOND moment - the moment that shows the query
    again - because that is what lets a camera that shows the thing
    repeatedly take a second of the editor's slots.  The pass stops the
    moment the list is full and every camera in it has been judged
    twice.

    `verify` is the callable that puts one candidate to the model; it
    defaults to the shipped interval verifier.  It is an argument so a
    test can name the defect without a model, and so the cost of the
    pass can be measured without one.
    """
    classification = classification or classify(query)
    started = time.perf_counter()
    per_camera = candidate_intervals(ranges, classification, frames_by_clip,
                                     per_clip=per_clip, event_gap_s=event_gap_s)
    statement = classification["statement"]
    order = sorted(per_camera,
                   key=lambda clip: -(_num(per_camera[clip][0].get("score"))
                                      if per_camera[clip] else 0.0))
    inspected: list[dict] = []
    calls = 0
    deepening: set = set()
    if not any(candidate.get("frames")
               for candidates in per_camera.values()
               for candidate in candidates):
        # Nothing to inspect: the source memory holds no frame for any
        # candidate, so the pass cannot run at all.  It says so rather
        # than rejecting every candidate and returning nothing.
        return {
            "ranges": [],
            "candidates": [],
            "cost": {"calls": 0, "cached_calls": 0, "candidates": 0,
                     "verified": 0, "rejected": 0, "uncertain": 0,
                     "seconds": 0.0,
                     "seconds_per_call": None, "wall_seconds": 0.0},
            "classification": classification,
            "status": "unavailable",
            "reason": ("no candidate has an extracted frame to inspect: "
                       "the source memory holds no frame sample for this "
                       "project's sources"),
        }
    for depth in range(per_clip):
        progressed = False
        for clip_id in order:
            entries = per_camera.get(clip_id) or []
            if depth >= len(entries):
                continue
            if depth and clip_id not in deepening:
                # A camera that holds no slot is not judged again: its
                # first moment was judged and it did not earn one.
                # The set of cameras to judge again is taken ONCE, when
                # the first round is done: a camera that loses its slot
                # to a camera that shows the thing AGAIN must not be
                # starved of its own second moment by that very thing.
                continue
            candidate = entries[depth]
            if calls >= max_candidates:
                break
            calls += 1
            verdict = _verify_candidate(project_folder, candidate, statement,
                                        verify)
            candidate = {**candidate,
                         **_judge(candidate, verdict, classification)}
            inspected.append(candidate)
            progressed = True
            if depth and _settled(inspected, per_camera, top_ranges,
                                  per_source, different_event,
                                  source_durations):
                break
        if depth:
            if _settled(inspected, per_camera, top_ranges, per_source,
                        different_event, source_durations):
                break
            # The next round judges the cameras that hold a slot NOW.
            deepening = _represented(inspected, top_ranges, per_source,
                                     different_event, source_durations)
            continue
        if not progressed:
            break
        # The first round is done: the cameras that hold a slot are the
        # ones judged again, and that set is taken ONCE, so a camera that
        # loses its slot to a camera that shows the thing AGAIN is not
        # starved of its own second moment by that very thing.
        deepening = _represented(inspected, top_ranges, per_source,
                                 different_event, source_durations)
    inspected.sort(key=lambda c: -(c.get("score") or 0.0))
    picked = select(inspected, top_ranges, per_source=per_source,
                    different_event=different_event,
                    source_durations=source_durations)
    verified = [c for c in inspected if c.get("verdict") == "yes"]
    rejected = [c for c in inspected if c.get("verdict") == "no"]
    uncertain = [c for c in inspected if c.get("verdict") == "uncertain"]
    seconds = sum(float(c.get("seconds") or 0.0) for c in inspected)
    fresh = [c for c in inspected if c.get("cached") is False]
    cached = [c for c in inspected if c.get("cached") is True]
    cost = {
        "calls": len(fresh),
        "cached_calls": len(cached),
        "candidates": len(inspected),
        "verified": len(verified),
        "rejected": len(rejected),
        "uncertain": len(uncertain),
        "seconds": round(seconds, 2),
        "seconds_per_call": (round(seconds / len(fresh), 2) if fresh else None),
        "wall_seconds": round(time.perf_counter() - started, 2),
    }
    return {
        "ranges": [_window(c, statement) for c in picked],
        "candidates": inspected,
        "cost": cost,
        "classification": classification,
        "status": "ran",
    }


def _window(candidate: Mapping, statement: str) -> dict:
    """The presented window, with the verdict that justified it.

    It carries the lane's own window fields as well as the
    justification: a window the pass presents is a window the editor
    scrubs, and the printer and the JSON answer read the same fields
    they read on a ranked window.
    """
    return {
        "clip_id": candidate["clip_id"],
        "filename": candidate.get("filename", ""),
        "start": candidate["start"],
        "end": candidate["end"],
        "duration": round(candidate["end"] - candidate["start"], 3),
        "timecode": candidate.get("timecode"),
        "best_score": candidate.get("score"),
        "n_frames": len(candidate.get("frames") or ()),
        "verified": True,
        # The ORIGINAL MEDIA behind the verdict: the recording, and the
        # extracted frames of it the model was shown.  A reader who
        # doubts the verdict follows this and looks at the same frames.
        "original": {
            "media": candidate.get("filename", ""),
            "frame_files": [frame.get("file")
                            for frame in candidate.get("frames") or ()
                            if frame.get("file")],
        },
        "justification": {
            "verdict": candidate.get("verdict"),
            "reason": candidate.get("reason"),
            "frames_shown": [f["t"] for f in candidate.get("frames") or ()],
            "statement": statement,
            **({"refined_span": candidate["refined_span"]}
               if candidate.get("refined_span") else {}),
        },
    }


def _judge(candidate: Mapping, answer: Mapping,
           classification: Mapping) -> dict:
    """One candidate's verdict in the semantic stage's vocabulary.

    The judge answers yes, no, or something unusable; the stage reads
    those as verified, rejected, and UNCERTAIN (see
    `semantic_verify.map_answer`) - only an explicit "no" rejects.
    An established EVENT must also name WHEN: the refined timestamps
    travel beside the verdict, and a yes that names no timestamp is
    uncertain rather than verified, because the editor cannot cut on
    it.  The shot window itself is untouched (the pass selects and
    justifies, it does not re-shape the lane's geometry); the
    refinement is evidence beside it.
    """
    stage = _semantic_verify.map_answer((answer or {}).get("answer"))
    verdict = {"verified": "yes", "rejected": "no"}.get(stage,
                                                               "uncertain")
    reason = (answer or {}).get("reason")
    refined = None
    if verdict == "yes" and classification.get("event"):
        refined = _semantic_verify.refine_event_span(candidate, answer or {})
        if refined is None:
            verdict = "uncertain"
            reason = ("the judge said yes but named no frame, so there "
                       "is no timestamp the editor can cut on: an "
                       "unrefined event is not a verification")
    judged: dict = {"verdict": verdict, "reason": reason,
                    "seconds": (answer or {}).get("seconds"),
                    "cached": (answer or {}).get("cached")}
    if (answer or {}).get("frame_t") is not None:
        judged["frame_t"] = (answer or {}).get("frame_t")
    if refined is not None:
        judged["refined_span"] = refined
    return judged


def _verify_candidate(project_folder, candidate, statement, verify):
    """Put one candidate to the model, or to the caller's verifier."""
    if verify is not None:
        return verify([candidate], statement)[0] or {}
    from library.tools import source_memory, span_verification
    from library.tools.analysis import footage_segments

    if not candidate.get("frames"):
        return {"answer": "no", "reason": candidate.get("reason"),
                "seconds": 0.0, "cached": False}
    catalog = {clip.get("clip_id"): clip
               for clip in footage_segments.load_catalog(project_folder)}
    clip = catalog.get(candidate["clip_id"])
    if clip is None:
        return {"answer": "no",
                "reason": "the camera is not in the project catalog",
                "seconds": 0.0, "cached": False}
    digest, _basis = source_memory.digest_for_clip(project_folder, clip)
    if digest is None:
        return {"answer": "no",
                "reason": "the source has no measured frame sample to inspect",
                "seconds": 0.0, "cached": False}
    times = [f["t"] for f in candidate["frames"]]
    gap = (times[-1] - times[0]) / (len(times) - 1) if len(times) > 1 else 0.0
    answers = span_verification.verify_intervals(
        digest, [{"frames": candidate["frames"]}], statement,
        gap_seconds=gap)
    return answers[0] if answers else {"answer": "no", "reason": None}
