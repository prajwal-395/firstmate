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


def candidate_intervals(ranges: Sequence[Mapping], classification: Mapping,
                        frames_by_clip: Mapping[str, Sequence[Mapping]],
                        per_clip: int = CANDIDATES_PER_CLIP
                        ) -> dict[str, list[dict]]:
    """The candidate intervals the pass may inspect, per camera.

    One candidate per shot, per camera: the shots the lane merged are
    what the editor is offered, so they are what the pass judges.  The
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
        candidates = []
        for entry in entries[:per_clip]:
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
           per_source: int = 1, different_event=None) -> list[dict]:
    """The windows the pass presents, and why each one is there.

    **Cameras first, then moments.**  The lane's rule is that the editor
    is not shown the same camera five times, and it stays: one window
    per camera while there are cameras to spend the slots on, and a
    second window of a camera only when the query asks for more than
    one occurrence of the thing and the second window is a DIFFERENT
    event.

    What the pass adds is what happens when there are FEWER cameras with
    an established window than there are slots: the leftover slot goes
    to another moment of a camera that has already contributed, and that
    moment must be a different event - the same test the lane applies
    to a camera's second window.  A slot the editor asked for is not
    left empty while a camera with a fifth sighting sits unshown.

    A window is presented only when the pass established it.  A
    candidate whose evidence did not establish the requested thing is
    not offered with a lower rank: it is not offered.
    """
    verified = [c for c in candidates if c.get("verdict") == "yes"]
    limit = max(0, top_ranges)
    cap = max(1, per_source)
    picked: list[dict] = []
    seen: dict = {}
    for candidate in verified:
        clip_id = candidate["clip_id"]
        taken = seen.get(clip_id, 0)
        if taken >= cap:
            continue
        if taken and different_event is not None and not different_event(
                candidate, picked, clip_id):
            continue
        seen[clip_id] = taken + 1
        picked.append(candidate)
        if len(picked) >= limit:
            return picked
    for candidate in verified:
        if len(picked) >= limit:
            break
        if any(candidate is other for other in picked):
            continue
        if different_event is not None and not different_event(
                candidate, picked, candidate["clip_id"]):
            continue
        picked.append(candidate)
    return picked


def rerank(*, project_folder, query: str, ranges: Sequence[Mapping],
           frames_by_clip: Mapping[str, Sequence[Mapping]],
           top_ranges: int = 5, classification: Mapping | None = None,
           verify=None, per_clip: int = CANDIDATES_PER_CLIP,
           max_candidates: int = MAX_CANDIDATES, per_source: int = 1,
           different_event=None) -> dict:
    """Inspect the candidate intervals and present the justified ones.

    **Cameras are visited round robin, and the pass stops as soon as the
    editor's list is full.**  One camera's best three shots are not more
    informative than the other cameras' best shots, and a query that is
    answered by the first camera inspected must not cost three calls per
    camera to find out.  So the candidates are inspected one per camera
    at a time, and the pass stops the moment the selection holds as many
    windows as the editor asked for.

    `verify` is the callable that puts one candidate to the model; it
    defaults to the shipped interval verifier.  It is an argument so a
    test can name the defect without a model, and so the cost of the
    pass can be measured without one.
    """
    classification = classification or classify(query)
    started = time.perf_counter()
    per_camera = candidate_intervals(ranges, classification, frames_by_clip,
                                     per_clip=per_clip)
    statement = classification["statement"]
    order = sorted(per_camera,
                   key=lambda clip: -(_num(per_camera[clip][0].get("score"))
                                      if per_camera[clip] else 0.0))
    inspected: list[dict] = []
    calls = 0
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
                     "verified": 0, "rejected": 0, "seconds": 0.0,
                     "seconds_per_call": None, "wall_seconds": 0.0},
            "classification": classification,
            "status": "unavailable",
            "reason": ("no candidate has an extracted frame to inspect: "
                       "the source memory holds no frame sample for this "
                       "project's sources"),
        }
    for depth in range(per_clip):
        for clip_id in order:
            entries = per_camera.get(clip_id) or []
            if depth >= len(entries):
                continue
            candidate = entries[depth]
            if calls >= max_candidates:
                break
            calls += 1
            verdict = _verify_candidate(project_folder, candidate, statement,
                                        verify)
            candidate = {**candidate, "verdict": verdict.get("answer"),
                         "reason": verdict.get("reason"),
                         "seconds": verdict.get("seconds"),
                         "cached": verdict.get("cached")}
            inspected.append(candidate)
            if len(select(inspected, top_ranges, per_source=per_source,
                          different_event=different_event)) >= top_ranges:
                break
        if len(select(inspected, top_ranges, per_source=per_source,
                      different_event=different_event)) >= top_ranges:
            break
    inspected.sort(key=lambda c: -(c.get("score") or 0.0))
    picked = select(inspected, top_ranges, per_source=per_source,
                    different_event=different_event)
    verified = [c for c in inspected if c.get("verdict") == "yes"]
    seconds = sum(float(c.get("seconds") or 0.0) for c in inspected)
    fresh = [c for c in inspected if c.get("cached") is False]
    cached = [c for c in inspected if c.get("cached") is True]
    cost = {
        "calls": len(fresh),
        "cached_calls": len(cached),
        "candidates": len(inspected),
        "verified": len(verified),
        "rejected": len(inspected) - len(verified),
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
        },
    }


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
