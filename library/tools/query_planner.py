"""R03: the explicit query-planning stage.

A natural-language editorial request becomes a TYPED retrieval plan
before anything is searched. The stage does five things, in order:

1. **Deterministic filters first.** The query is scanned for the
   constraints a measurement can answer without inference: a known
   speaker/person (track id, declared name, role), a source/camera
   (asset id, camera name), a transcript phrase (quoted wording or the
   topic a say-cue names), a time range, an object category, an
   action/state transition, a shot or scene context, and an ordinal
   ("the second time"). What is extracted here never goes back to a
   model for guessing.
2. **Ambiguous dimensions named.** Whatever is left that needs semantic
   inference is recorded as an ambiguous dimension with the reason and
   the inference it needs - an unresolved role ("the speaker" with no
   track), an open-vocabulary verb with no candidate kind, a framing
   word with no calibrated floor, a bare noun with no framing cue that
   may be words-about or picture-of. Ambiguity is a field, not a
   silent default.
3. **Ordered cheap and expensive steps.** Every requirement compiles to
   a step with a cost tier. Measurement lookups the index answers today
   (transcript, object detection, on-screen presence, identity) run
   FIRST; bounded verification of candidate lanes (m7/m8 action
   spans, m10 relations) runs after; model inference runs last; a
   requirement no lane can answer is recorded, never executed.
4. **Budgets and verification by complexity.** The plan carries a
   per-step candidate cap, a total cap, and the verification the
   expensive steps require. Harder queries earn larger budgets by a
   stated rule, not by a bigger default.
5. **Replan on missing or contradictory evidence.** ``replan`` takes a
   plan plus a coverage report and returns the plan with steps
   dropped, narrowed or held for review - each change logged with the
   reason. Nothing is silently widened to fit what is missing.

The parse substrate is :mod:`library.tools.requirement_graph` (seven
families, conjunctions, temporal order, verify/unanswerable split);
this stage EXTENDS it with filters, tiers, budgets and replanning, and
does not rebuild it. Nothing here loads a model, renders, or encodes:
the module reads stored analysis through the graph's cue tables and
fixtures only.

``plan_query`` is the entry point; its output is a typed dict (``plan``
subcommand prints it), never prose:

    python3 -m library.tools.query_planner plan \\
        "Find the close-up where the speaker picks up the phone after saying goodbye"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

try:
    from library.tools import requirement_graph as rg
    from library.tools.retrieval import requirements as rr
except ImportError:  # imported as `tools.*` from inside library/
    from tools import requirement_graph as rg
    from tools.retrieval import requirements as rr

SCHEMA_VERSION = 1

# ── Cost tiers ────────────────────────────────────────────────────

CHEAP = "cheap"
EXPENSIVE = "expensive"
UNANSWERABLE = "unanswerable"

#: Lanes a measurement lookup answers today, with no model in the loop.
CHEAP_LANES = (
    "m1",
    "m3b",
    "object_detection",
    "on_screen",
    "m4",
    "m5",
    "ocr_extraction",
)

#: Lanes that need bounded reanalysis of candidate spans.
VERIFY_LANES = ("m7", "m8", "m10")

# ── The filter slots ──────────────────────────────────────────────

FILTER_SLOTS = (
    "person",
    "source_camera",
    "transcript_phrase",
    "time_range",
    "object_category",
    "action_transition",
    "shot_scene",
    "ordinal",
)
"""The deterministic filters, extracted before anything else.

Each names a constraint a measurement answers without inference. A
slot the query does not fill stays absent; an absent slot is never a
wildcard that matches everything - it is a dimension the plan does not
constrain.
"""

_TRACK_ID = re.compile(r"\b(face|voice|person)_[0-9]{2,}\b", re.IGNORECASE)
_ASSET_ID = re.compile(r"\b[A-Z]{2,}[0-9]{2,}\b")
_CAMERA_NAME = re.compile(
    r"\bcamera\s+([a-z]|\d+)\b|\bcam\s+([a-z]|\d+)\b", re.IGNORECASE
)
_QUOTED = re.compile(r"'([^']{2,120})'|\"([^\"]{2,120})\"")
_TIME_AT = re.compile(r"\bat\s+(\d+(?:\.\d+)?)\s?s(?:econds?)?\b", re.IGNORECASE)
_TIME_RANGE = re.compile(
    r"\b(?:between|from)\s+(\d+(?:\.\d+)?)\s?(?:s(?:ec(?:ond)?s?)?)?"
    r"\s+(?:and|to)\s+(\d+(?:\.\d+)?)\s?s(?:econds?)?\b",
    re.IGNORECASE,
)
_CLOCK_TIME = re.compile(r"\bat\s+(\d+):(\d{2})(?::(\d{2}))?\b")
_ORDINAL_WORDS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5}
_ORDINAL_LAST = re.compile(r"\b(last)\b(?:\s+time)?", re.IGNORECASE)
_ORDINAL_DIGIT = re.compile(r"\b(\d+)(?:st|nd|rd|th)\b(?:\s+time)?", re.IGNORECASE)

_ROLE_WORDS = (
    "speaker",
    "host",
    "guest",
    "interviewer",
    "interviewee",
    "presenter",
    "narrator",
    "subject",
)

#: Framing words with no calibrated floor: read as shot context, never
#: compiled into a measurement threshold the query did not state.
FRAMING_WORDS = (
    "close-up",
    "close up",
    "closeup",
    "wide shot",
    "wide",
    "medium shot",
    "medium",
    "establishing",
    "tight",
    "wide angle",
    "telephoto",
    "shot",
    "shots",
)

#: Scene words: where the picture is, not what is in it.
SCENE_WORDS = tuple(sorted(set(rr.SCENE_CUES)))

#: Object names the detector tier answers (closed table, shared with
#: the L5 planner's image family and the retrieval index vocabulary).
OBJECT_WORDS = tuple(sorted(set(rr.OBJECT_CUES) | set(rg._IMAGE_CUES)))


def _object_spans(lowered: str) -> list[tuple[int, int, str]]:
    """Word-boundary hits for every known object name.

    Object names are singular: a trailing "s" folds to the singular
    ("cars" asks about "car") because the detector tier labels
    categories, never plurals.
    """
    spans = []
    for word in OBJECT_WORDS:
        for form in dict.fromkeys((word, word + "s")):
            start = 0
            while True:
                hit = lowered.find(form, start)
                if hit < 0:
                    break
                before = lowered[hit - 1] if hit > 0 else " "
                after = (
                    lowered[hit + len(form)] if hit + len(form) < len(lowered) else " "
                )
                if before.isalnum() or after.isalnum():
                    start = hit + 1
                    continue
                spans.append((hit, hit + len(form), word))
                start = hit + len(form)
    return spans


def _framing_spans(lowered: str) -> list[tuple[int, int, str]]:
    """Word-boundary hits for framing words, longest first."""
    spans = []
    for word in sorted(FRAMING_WORDS, key=len, reverse=True):
        start = 0
        while True:
            hit = lowered.find(word, start)
            if hit < 0:
                break
            before = lowered[hit - 1] if hit > 0 else " "
            after = lowered[hit + len(word)] if hit + len(word) < len(lowered) else " "
            if before.isalnum() or after.isalnum():
                start = hit + 1
                continue
            spans.append((hit, hit + len(word), word))
            start = hit + len(word)
    kept: list[tuple[int, int, str]] = []
    for span in sorted(spans):
        if any(span[0] < stop and start < span[1] for start, stop, _ in kept):
            continue
        kept.append(span)
    return kept


def _extract_filters_and_anchor(text: str) -> tuple[dict[str, Any], bool]:
    """Deterministic filters plus whether speech anchored the query."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("a query asks something in words")
    cleaned = " ".join(text.split())
    lowered = cleaned.lower()
    filters: dict[str, Any] = {}

    track = _TRACK_ID.search(cleaned)
    roles = [word for word in _ROLE_WORDS if re.search(r"\b" + word + r"\b", lowered)]
    person: dict[str, Any] = {}
    if track:
        person = {"track": track.group(0).lower(), "resolved": True}
    elif roles:
        person = {"role": roles[0], "resolved": False}
    if person:
        filters["person"] = person

    asset = _ASSET_ID.search(cleaned)
    camera = _CAMERA_NAME.search(cleaned)
    if asset or camera:
        source: dict[str, Any] = {}
        if asset:
            source["asset"] = asset.group(0)
        if camera:
            source["camera"] = re.sub(r"\s+", " ", camera.group(0).lower()).strip()
        filters["source_camera"] = source

    quotes = [first or second for first, second in _QUOTED.findall(cleaned)]
    _SAY_CUE = (
        r"says|say|saying|said|discusses?|discussing|"
        r"talks?\s+about|talking\s+about|mentions?|mentioning|"
        r"explains?|explaining|reads?|reading|"
        r"speaks?\s+about|speaking\s+about|answers?|asks?\s+about"
    )
    # A verb-final relative clause names its topic BEFORE the verb
    # ("the recipe he reads out"): when the tail past the cue is
    # empty or a bare particle, the topic is the clause before it.
    _PARTICLES = {"out", "off", "up", "aloud", "loud", "over", "through"}
    _PRONOUNS = {"he", "she", "they", "it", "him", "her", "them", "we", "you", "i"}
    has_speech_anchor = False
    if quotes:
        filters["transcript_phrase"] = {"phrase": quotes[0], "match": "quoted"}
        has_speech_anchor = True
    else:
        for match in re.finditer(
            r"\b(?:" + _SAY_CUE + r")\b\s*[:,;]?\s*(.+)$", cleaned, re.IGNORECASE
        ):
            tail = (
                re.split(
                    r"\s+(while|and then|before|after|without|with no|"
                    r"during|appears?|on screen|visible)\s+",
                    match.group(1),
                    maxsplit=1,
                )[0]
                .strip()
                .rstrip(".,;")
            )
            tail = re.sub(r"^(the|a|an)\s+", "", tail, flags=re.IGNORECASE)
            head_words = tail.split()
            if not head_words or head_words[0].lower() in _PARTICLES or len(tail) <= 1:
                before = cleaned[: match.start()].strip().rstrip(".,;")
                before = re.split(
                    r"\s+(while|and then|before|after|without|with no|"
                    r"during|when|where|with)\s+",
                    before,
                    maxsplit=1,
                )[-1]
                before = re.sub(r"^(the|a|an)\s+", "", before, flags=re.IGNORECASE)
                clipped = before.split()
                while clipped and clipped[-1].lower() in _PRONOUNS:
                    clipped.pop()
                tail = " ".join(clipped).strip().rstrip(".,;")
            if 1 < len(tail) <= 120:
                filters["transcript_phrase"] = {"phrase": tail, "match": "topic"}
                has_speech_anchor = True
            break
    # A topic head names the lane by itself: "the comparison of
    # X", "the story about Y" are speech topics the way "a shot
    # of Z" is picture. The head noun commits the lane; the tail
    # after of/about is the topic. Only relative heads qualify -
    # "part" and "bit" also name object pieces, so they stay out.
    _TOPIC_HEADS = (
        "comparison",
        "story",
        "question",
        "example",
        "explanation",
        "discussion",
        "debate",
        "account",
        "anecdote",
        "recap",
        "summary",
        "overview",
        "argument",
        "claim",
    )
    _head = re.search(
        r"\b(?:" + "|".join(_TOPIC_HEADS) + r")\b[^.\n]{0,24}"
        r"\b(?:about|of)\b\s+(.+)$",
        cleaned,
        re.IGNORECASE,
    )
    if _head is not None:
        tail = (
            re.split(
                r"\s+(while|and then|before|after|without|with no|"
                r"during|appears?|on screen|visible)\s+",
                _head.group(1),
                maxsplit=1,
            )[0]
            .strip()
            .rstrip(".,;")
        )
        tail = re.sub(r"^(the|a|an)\s+", "", tail, flags=re.IGNORECASE)
        if 1 < len(tail) <= 120:
            filters["transcript_phrase"] = {"phrase": tail, "match": "topic-head"}
            has_speech_anchor = True
    if "transcript_phrase" not in filters:
        # "the one about Y", "the bit about Y": no verb of
        # saying, but the about-phrase is a speech topic unless a
        # framing cue claims the picture instead.
        framing_here = _framing_spans(lowered)
        about = re.search(r"\babout\b\s+(.+)$", cleaned, re.IGNORECASE)
        if about and not framing_here:
            tail = (
                re.split(
                    r"\s+(while|and then|before|after|without|with no|"
                    r"during|appears?|on screen|visible)\s+",
                    about.group(1),
                    maxsplit=1,
                )[0]
                .strip()
                .rstrip(".,;")
            )
            tail = re.sub(r"^(the|a|an)\s+", "", tail, flags=re.IGNORECASE)
            if 1 < len(tail) <= 120:
                filters["transcript_phrase"] = {"phrase": tail, "match": "about"}
                has_speech_anchor = True

    window = _TIME_RANGE.search(cleaned)
    if window:
        filters["time_range"] = {
            "start": float(window.group(1)),
            "end": float(window.group(2)),
        }
    else:
        at = _TIME_AT.search(cleaned)
        if at:
            point = float(at.group(1))
            filters["time_range"] = {"start": point, "end": point}
        else:
            clock = _CLOCK_TIME.search(cleaned)
            if clock:
                hours_mins = int(clock.group(1)) * 60 + int(clock.group(2))
                total = hours_mins * 60 + int(clock.group(3) or 0)
                filters["time_range"] = {"start": float(total), "end": float(total)}

    _NUMBERS = {
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
        "eleven",
        "twelve",
        "twenty",
        "thirty",
        "forty",
        "fifty",
        "hundred",
        "thousand",
    }
    _COUNTED_PERSONS = {
        "man",
        "woman",
        "person",
        "people",
        "guy",
        "lady",
        "boy",
        "girl",
    }
    objects = _object_spans(lowered)
    track_span = _TRACK_ID.search(cleaned)
    track_base = track_span.group(1).lower() if track_span else None
    if objects:
        seen: list[str] = []
        for start, stop, word in sorted(objects):
            if track_span and track_span.start() <= start < track_span.end():
                continue  # the track id is a person, not an object
            canonical = rg._VALUE_ALIASES.get(word, word)
            if canonical == track_base:
                continue
            if canonical in _COUNTED_PERSONS:
                before = re.search(r"(\d+|[a-z]+)\s+$", lowered[:start])
                if before and (
                    before.group(1).isdigit() or before.group(1) in _NUMBERS
                ):
                    continue
            if canonical not in seen:
                seen.append(canonical)
        if seen:
            filters["object_category"] = seen

    actions: list[dict[str, Any]] = []
    _POSSESSIVE_PATTERNS = (
        (r"covers?(?:\s+(?:her|his|their|the))?\s+mouth", "hand_to_face"),
        (r"touch(?:es|ing)?(?:\s+(?:her|his|their|the))?\s+face", "hand_to_face"),
        (r"hand\s+(?:to|on|near)\s+(?:\w+\s+)?(?:face|mouth)", "hand_to_face"),
        (r"picks?(?:\s+(?:it|them|that|this))?\s+up", "pick_up_put_down"),
        (r"puts?(?:\s+(?:it|them|that|this))?\s+down", "pick_up_put_down"),
    )
    possessive_hits: list[tuple[int, str]] = []
    for pattern, kind in _POSSESSIVE_PATTERNS:
        for match in re.finditer(pattern, lowered):
            possessive_hits.append((match.start(), kind))
    for _, kind in sorted(possessive_hits):
        if all(row["candidate_kind"] != kind for row in actions):
            actions.append({"candidate_kind": kind, "inferred": False})
    for cue in sorted(set(rg.ACTION_KIND_BY_VERB), key=len, reverse=True):
        if re.search(r"\b" + re.escape(cue) + r"\b", lowered):
            kind = rg.ACTION_KIND_BY_VERB[cue]
            if all(row["candidate_kind"] != kind for row in actions):
                actions.append({"candidate_kind": kind, "inferred": False})
    open_verbs = []
    framing_covered = [(start, stop) for start, stop, _ in _framing_spans(lowered)]
    for cue in sorted(set(rg._ACTION_CUES)):
        if cue in rg.ACTION_KIND_BY_VERB:
            continue
        for match in re.finditer(r"\b" + re.escape(cue) + r"\b", lowered):
            if any(
                match.start() < stop and start < match.end()
                for start, stop in framing_covered
            ):
                continue  # "close" inside "close-up" is framing
            if cue == "point":
                # "two point oh" (2.0) is a decimal, never a gesture.
                prev = re.search(r"(\d+|[a-z]+)\s+$", lowered[: match.start()])
                if prev and (prev.group(1).isdigit() or prev.group(1) in _NUMBERS):
                    continue
            open_verbs.append(cue)
            break
    for cue in open_verbs:
        if all(row.get("verb") != cue for row in actions):
            actions.append({"verb": cue, "candidate_kind": None, "inferred": True})
    if actions:
        filters["action_transition"] = actions

    framing = [word for _, _, word in _framing_spans(lowered)]
    scenes = [
        word
        for word in SCENE_WORDS
        if re.search(r"\b" + re.escape(word) + r"\b", lowered)
    ]
    if framing or scenes:
        shot: dict[str, Any] = {}
        if framing:
            shot["framing"] = framing
        if scenes:
            shot["scene"] = scenes
        filters["shot_scene"] = shot

    ordinal = _ORDINAL_DIGIT.search(cleaned)
    if ordinal:
        filters["ordinal"] = int(ordinal.group(1))
    else:
        word = re.search(
            r"\b(first|second|third|fourth|fifth)\b(?:\s+time)?", cleaned, re.IGNORECASE
        )
        if word:
            filters["ordinal"] = _ORDINAL_WORDS[word.group(1).lower()]
        elif _ORDINAL_LAST.search(cleaned):
            filters["ordinal"] = "last"

    return filters, has_speech_anchor


def extract_filters(text: str) -> dict[str, Any]:
    """The deterministic filters a query states, slot by slot.

    Every slot is filled from a closed table or a stated anchor (a
    track id, an asset id, a number, a quote) - nothing is inferred.
    A slot with nothing stated is absent from the result.
    """
    filters, _ = _extract_filters_and_anchor(text)
    return filters


def ambiguous_dimensions(
    text: str, filters: dict[str, Any], has_speech_anchor: bool = False
) -> list[dict[str, Any]]:
    """The dimensions that need semantic inference, each with its need.

    A dimension lands here when the query asks something no stored
    measurement answers by lookup: an identity nobody stated, a verb no
    candidate kind covers, a framing word with no calibrated floor, a
    bare noun that may be words-about or picture-of. Each entry names
    the dimension, the reason, and the inference that would resolve it.
    """
    cleaned = " ".join(text.split())
    lowered = cleaned.lower()
    dimensions: list[dict[str, Any]] = []

    person = filters.get("person") or {}
    if person and not person.get("resolved"):
        dimensions.append(
            {
                "dimension": "person_identity",
                "reason": (
                    "the query names a role or a name but no track: "
                    f"{person.get('role') or person.get('name')}"
                ),
                "needs": (
                    "identity resolution over the face/voice tracks - "
                    "a semantic inference, never a lookup"
                ),
            }
        )

    for row in filters.get("action_transition") or []:
        if row.get("candidate_kind") is None:
            dimensions.append(
                {
                    "dimension": "action_kind",
                    "reason": (
                        f"the verb {row.get('verb')!r} matches no "
                        "verified action candidate kind"
                    ),
                    "needs": (
                        "a bounded action-verification producer for "
                        "this verb, or a model judgement over the "
                        "candidate spans"
                    ),
                }
            )

    shot = filters.get("shot_scene") or {}
    for word in shot.get("framing") or []:
        dimensions.append(
            {
                "dimension": "framing_measure",
                "reason": (
                    f"{word!r} states no number, so no measurement "
                    "floor can be compiled from it"
                ),
                "needs": (
                    "a calibrated shot-scale reading (face-size "
                    "fraction) before this constrains the search"
                ),
            }
        )

    objects = _object_spans(lowered)
    framing_hits = _framing_spans(lowered)
    person_hits = [
        m.start()
        for m in re.finditer(
            r"\b(man|woman|person|people|face|speaker|host|guest)\b", lowered
        )
    ]
    # A bare content noun with no framing cue may be words-about the
    # thing or picture-of the thing: the honest plan searches both and
    # says which guess it made (retrieval.requirements FRAMING_CUES).
    content = [
        token
        for token in re.findall(r"[a-z0-9']+", lowered)
        if token not in rr._STOPWORDS and len(token) > 2
    ]
    matched = {word for _, _, word in objects}
    if (
        content
        and not framing_hits
        and not person_hits
        and matched
        and has_speech_anchor
    ):
        dimensions.append(
            {
                "dimension": "words_or_picture",
                "reason": (
                    "the query names speech and a visible thing with "
                    "no framing cue: the thing may be spoken about "
                    "or shown"
                ),
                "needs": (
                    "search both the transcript and the frame lanes "
                    "and report which lane answered"
                ),
            }
        )

    transcript = filters.get("transcript_phrase") or {}
    if transcript.get("match") == "topic":
        dimensions.append(
            {
                "dimension": "phrase_exactness",
                "reason": (
                    f"the wording {transcript['phrase']!r} is a topic, "
                    "not a quote: the transcript may paraphrase it"
                ),
                "needs": (
                    "semantic match over the utterances, scored and "
                    "reported - never a silent keyword hit"
                ),
            }
        )

    return dimensions


# ── Step construction ─────────────────────────────────────────────


def _tier_of(compiled: dict[str, Any]) -> str:
    """The cost tier of one compiled requirement, by compiled kind.

    A ``plan`` is a measurement the Project Model answers today (a
    lookup over stored analysis, whatever lane stores it); a
    ``verify`` needs bounded reanalysis of candidate spans; an
    ``unanswerable`` needs a lane that was never built. The compile
    already classified the cost - the tier reads it, never re-derives
    it from the lane name (every event span, measured or candidate,
    reports the events lane)."""
    if compiled.get("kind") == "unanswerable":
        return UNANSWERABLE
    if compiled.get("kind") == "verify":
        return EXPENSIVE
    return CHEAP


def _lane_of(compiled: dict[str, Any], requirement: dict[str, Any]) -> str:
    """The lane that answers one compiled requirement."""
    if compiled.get("kind") == "verify":
        return str(compiled.get("lane", "m7"))
    if compiled.get("kind") == "unanswerable":
        return str(compiled.get("required_lane", "no-lane-yet"))
    lanes = compiled.get("required_lanes") or ()
    if lanes:
        return str(lanes[0])
    family = requirement.get("family")
    return {
        "speech": "m1",
        "image": "object_detection",
        "action": "m7",
        "relationship": "m10",
        "sound": "m5",
        "ocr": "ocr_extraction",
        "technical": "no-lane-yet",
    }.get(str(family), "no-lane-yet")


def _superseded_by(
    requirement: dict[str, Any], filters: dict[str, Any], query_text: str
) -> str | None:
    """The filter that already consumed this requirement's cue, if any.

    The deterministic filters run first, so a requirement asking for
    the same token a second time is recorded against the filter that
    owns it instead of being searched twice. Returns the consuming
    slot's name, or None when the requirement stands on its own.
    """
    cue = str(requirement.get("cue") or "").lower().strip()
    token = rg._VALUE_ALIASES.get(cue, cue)
    person = filters.get("person") or {}
    track = str(person.get("track") or "")
    if track and token == track.split("_")[0]:
        return "person"
    if "ordinal" in filters:
        if token in _ORDINAL_WORDS or token == "last":
            return "ordinal"
        if token == "time":
            words = re.findall(r"[a-z0-9]+", query_text.lower())
            for index, word in enumerate(words):
                if word in _ORDINAL_WORDS or word == "last":
                    window = words[index + 1 : index + 4]
                    if "time" in window:
                        return "ordinal"
    return None


def build_steps(
    graph: dict[str, Any],
    compiled: dict[str, Any],
    filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """The ordered retrieval steps: cheap filters first, then verify,
    then inference, then the recorded refusals.

    Temporal order edges become ``depends_on`` links between the steps
    they constrain, so a later step never runs before the anchor it is
    ordered after. Within a tier, steps keep the query's own order.

    A requirement whose cue is exactly what a deterministic filter
    already consumed (the ``face`` of ``face_001``, the ``second`` of
    an ordinal) is SUPERSEDED: kept in the plan with the filter that
    consumed it and a zero budget, never executed as a second search
    for the same token.
    """
    requirements = list(graph.get("requirements") or [])
    rows = list(compiled.get("compiled") or [])
    by_id = {req["id"]: req for req in requirements}

    def order_key(item: dict[str, Any]) -> tuple[int, int]:
        tier = _tier_of(item)
        tier_rank = {CHEAP: 0, EXPENSIVE: 1, UNANSWERABLE: 2}[tier]
        position = next(
            (
                index
                for index, req in enumerate(requirements)
                if req["id"] == item.get("requirement_id")
            ),
            len(requirements),
        )
        return (tier_rank, position)

    ordered = sorted(rows, key=order_key)
    step_of: dict[str, str] = {}
    steps: list[dict[str, Any]] = []
    for position, item in enumerate(ordered, start=1):
        step_id = f"step-{position}"
        step_of[item["requirement_id"]] = step_id
        tier = _tier_of(item)
        requirement = by_id.get(item["requirement_id"]) or {}
        step: dict[str, Any] = {
            "id": step_id,
            "requirement_id": item["requirement_id"],
            "family": requirement.get("family"),
            "kind": "filter"
            if tier == CHEAP
            else ("verify" if tier == EXPENSIVE else "refused"),
            "tier": tier,
            "lane": _lane_of(item, requirement),
            "depends_on": [],
            "detail": item,
        }
        superseded_by = _superseded_by(
            requirement, filters or {}, graph.get("text", "")
        )
        if superseded_by is not None:
            step["status"] = "superseded"
            step["superseded_by"] = superseded_by
        steps.append(step)

    for first, second in compiled.get("order") or []:
        if first in step_of and second in step_of:
            target = next(step for step in steps if step["id"] == step_of[second])
            if step_of[first] not in target["depends_on"]:
                target["depends_on"].append(step_of[first])
    return steps


# ── Budgets ───────────────────────────────────────────────────────

CHEAP_STEP_BUDGET = 50
VERIFY_STEP_BUDGET = 20
INFERENCE_STEP_BUDGET = 10
TOTAL_BUDGET_BY_COMPLEXITY = {"simple": 100, "moderate": 200, "complex": 300}


def complexity_of(
    graph: dict[str, Any], steps: list[dict[str, Any]], ambiguous: list[dict[str, Any]]
) -> dict[str, Any]:
    """How hard the query is, by a stated rule.

    One point per requirement, two per verify/inference step and per
    ambiguous dimension, two when temporal order constrains the plan,
    one for an ordinal (only one occurrence may survive). Simple is 3
    or fewer, moderate 4 to 6, complex above.
    """
    n_requirements = len(graph.get("requirements") or [])
    n_heavy = sum(1 for step in steps if step.get("kind") == "verify")
    # Inference steps derive from the ambiguous dimensions and are
    # counted there, not here: counting them twice would price one
    # ambiguity as two complexities.
    n_ambiguous = len(ambiguous)
    has_order = bool(graph.get("order"))
    has_ordinal = any(
        re.search(
            r"\b(first|second|third|fourth|fifth|last)\b"
            r"(?:\s+time)?|\b\d+(?:st|nd|rd|th)\b",
            (req.get("cue") or ""),
            re.IGNORECASE,
        )
        for req in graph.get("requirements") or []
    )
    score = (
        n_requirements
        + 2 * n_heavy
        + 2 * n_ambiguous
        + (2 if has_order else 0)
        + (1 if has_ordinal else 0)
    )
    band = "simple" if score <= 3 else "moderate" if score <= 6 else "complex"
    return {
        "score": score,
        "band": band,
        "n_requirements": n_requirements,
        "n_verify_or_inference": n_heavy,
        "n_ambiguous": n_ambiguous,
        "has_order": has_order,
        "has_ordinal": has_ordinal,
    }


def budgets_for(
    steps: list[dict[str, Any]], complexity: dict[str, Any]
) -> dict[str, Any]:
    """Candidate budgets and verification, scaled by complexity.

    Cheap filter steps earn room to be broad; verify and inference
    steps earn narrow caps because each candidate costs reanalysis.
    Verification is required exactly when a verify step survives -
    never by default, never by silence.
    """
    per_step: dict[str, int] = {}
    for step in steps:
        if step.get("status") == "superseded":
            per_step[step["id"]] = 0
        elif step.get("kind") == "inference":
            per_step[step["id"]] = INFERENCE_STEP_BUDGET
        elif step["tier"] == CHEAP:
            per_step[step["id"]] = CHEAP_STEP_BUDGET
        elif step["tier"] == EXPENSIVE:
            per_step[step["id"]] = VERIFY_STEP_BUDGET
        else:
            per_step[step["id"]] = 0
    verify_lanes = sorted(
        {
            step["lane"]
            for step in steps
            if step["tier"] == EXPENSIVE and step["lane"] != "semantic_inference"
        }
    )
    return {
        "per_step_candidates": per_step,
        "total_candidates": TOTAL_BUDGET_BY_COMPLEXITY[complexity["band"]],
        "verification": {
            "required": bool(verify_lanes),
            "lanes": verify_lanes,
            "max_spans_per_verify": VERIFY_STEP_BUDGET,
        },
    }


# ── The plan ──────────────────────────────────────────────────────


def plan_query(text: str, *, scope: dict[str, Any] | None = None) -> dict[str, Any]:
    """Plan a natural-language editorial request, as a typed dict.

    Raises ``ValueError`` for an empty query or one with no
    recognizable requirement - a query nobody can plan is refused,
    never mapped onto a plausible measurement.
    """
    graph = rg.parse_query(text)
    compiled = rg.compile_graph(graph, scope=scope)
    filters, has_speech_anchor = _extract_filters_and_anchor(text)
    ambiguous = ambiguous_dimensions(text, filters, has_speech_anchor)
    steps = build_steps(graph, compiled, filters)

    inference_position = len(steps) + 1
    for dimension in ambiguous:
        steps.append(
            {
                "id": f"step-{inference_position}",
                "requirement_id": None,
                "family": None,
                "kind": "inference",
                "tier": EXPENSIVE,
                "lane": "semantic_inference",
                "depends_on": [],
                "detail": dimension,
            }
        )
        inference_position += 1

    complexity = complexity_of(graph, steps, ambiguous)
    budgets = budgets_for(steps, complexity)

    inference_ids = [step["id"] for step in steps if step["kind"] == "inference"]
    cheap_ids = [prior["id"] for prior in steps if prior["tier"] == CHEAP]
    for step in steps:
        if step["kind"] == "inference" and not step["depends_on"]:
            step["depends_on"] = list(cheap_ids)

    return {
        "schema_version": SCHEMA_VERSION,
        "query": graph["text"],
        "graph_id": graph["id"],
        "filters": filters,
        "ambiguous": ambiguous,
        "steps": steps,
        "order": list(graph.get("order") or []),
        "complexity": complexity,
        "budgets": budgets,
        "inference_step_ids": inference_ids,
        "answerable": list(compiled.get("answerable") or []),
        "needs_verification": list(compiled.get("needs_verification") or []),
        "unanswerable": list(compiled.get("unanswerable") or []),
        "replan_revision": 0,
        "replan_log": [],
    }


# ── Replanning ────────────────────────────────────────────────────


def replan(plan: dict[str, Any], coverage: dict[str, Any]) -> dict[str, Any]:
    """Revise a plan against missing or contradictory evidence.

    ``coverage`` carries ``lanes_absent``, ``lanes_partial`` and
    ``contradictions`` (each ``{"steps": [...], "note": ...}``). A
    step whose lane is absent is DROPPED but kept in the plan with
    its reason; a step on a partial lane is NARROWED to half its
    budget; a contradicted step is HELD for review. Every change is
    logged; a replan that changes nothing says so.
    """
    revised = json.loads(json.dumps(plan))
    absent = set(coverage.get("lanes_absent") or ())
    partial = set(coverage.get("lanes_partial") or ())
    contradictions = list(coverage.get("contradictions") or ())
    log: list[dict[str, Any]] = []

    for step in revised.get("steps") or []:
        lane = step.get("lane")
        if lane in absent and step.get("status", "active") == "active":
            step["status"] = "dropped"
            step["drop_reason"] = (
                f"lane {lane} was never built: the plan cannot search it"
            )
            log.append(
                {"step": step["id"], "change": "dropped", "reason": step["drop_reason"]}
            )
        elif lane in partial and step.get("status", "active") == "active":
            step["status"] = "narrowed"
            per_step = revised.get("budgets", {}).get("per_step_candidates", {})
            if step["id"] in per_step:
                per_step[step["id"]] = max(1, per_step[step["id"]] // 2)
            step["narrow_reason"] = (
                f"lane {lane} is only partly "
                "built: the budget is halved and "
                "a partial answer may not claim "
                "absence"
            )
            log.append(
                {
                    "step": step["id"],
                    "change": "narrowed",
                    "reason": step["narrow_reason"],
                }
            )

    for entry in contradictions:
        names = entry.get("steps") or []
        note = entry.get("note") or "contradictory evidence"
        for step in revised.get("steps") or []:
            if step["id"] in names and step.get("status", "active") == "active":
                step["status"] = "needs_review"
                step["review_reason"] = note
                log.append(
                    {"step": step["id"], "change": "held_for_review", "reason": note}
                )

    if not log:
        log.append(
            {
                "step": None,
                "change": "unchanged",
                "reason": (
                    "every step's lane is built and no contradiction names a step"
                ),
            }
        )
    revised["replan_revision"] = int(plan.get("replan_revision", 0)) + 1
    revised["replan_log"] = list(plan.get("replan_log") or []) + log
    for step in revised.get("steps") or []:
        step.setdefault("status", "active")
    return revised


# ── The CLI ───────────────────────────────────────────────────────


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="query_planner",
        description="R03: plan a natural-language editorial request "
        "into a typed retrieval plan with ordered cheap "
        "and expensive steps, budgets, and replanning.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_plan = sub.add_parser("plan", help="plan one query")
    p_plan.add_argument("query")

    p_replan = sub.add_parser("replan", help="revise a plan against a coverage report")
    p_replan.add_argument("plan_json")
    p_replan.add_argument("coverage_json")

    args = parser.parse_args(argv)
    if args.command == "plan":
        try:
            plan = plan_query(args.query)
        except (ValueError, rg.RequirementGraphError) as exc:
            print(json.dumps({"error": str(exc)}))
            return 1
        print(json.dumps(plan, indent=1, sort_keys=True))
        return 0
    if args.command == "replan":
        try:
            with open(args.plan_json, encoding="utf-8") as handle:
                plan = json.load(handle)
            with open(args.coverage_json, encoding="utf-8") as handle:
                coverage = json.load(handle)
        except (OSError, ValueError) as exc:
            print(json.dumps({"error": str(exc)}))
            return 1
        print(json.dumps(replan(plan, coverage), indent=1, sort_keys=True))
        return 0
    return 2  # pragma: no cover - argparse enforces the commands


if __name__ == "__main__":
    sys.exit(main())
