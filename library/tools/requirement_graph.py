"""L5 typed requirement graph and multimodal query planner.

Covers punch-list ACQ-001 (typed requirement graph), ACQ-002 (required
modalities per predicate) and RET-027 through RET-034 (typed planner:
conjunctions, temporal ordering, negatives, camera/quality, spatial and
audio-visual relationships).

**What this is.** A deterministic parse of a user query string into a
requirement graph: nodes name one of seven modalities (speech, image,
action, relationship, sound, ocr, technical), edges record conjunction
and temporal order. Each node compiles to either an
:mod:`library.tools.evidence_query` plan answered by measurements
already in the Project Model, a bounded verification task for a
candidate lane (action, relationship), or an explicit ``unanswerable``
record naming the missing measurement and the lane that could produce
it. Nothing here loads a model, reads footage, or guesses.

**What this is not.** Not a natural-language understander: cues are
fixed word lists, and a query with no recognizable cue is refused
rather than mapped onto a plausible measurement. Not a retriever: it
emits plans; :mod:`library.tools.evidence_acquisition` runs them.
Negation never becomes a negated predicate (the evidence query has
none); a negated requirement compiles the positive plan and is read
for absence, following the ``has_speech=False`` rule in
``plan_from_filter``.

Time is source seconds on the way in; compiled plans carry no clock
scope unless the caller supplies one, so every predicate is read
inside the caller's asset/clock scope at answer time.
"""

from __future__ import annotations

import re
from typing import Any

try:
    from library.tools import evidence_query
except ImportError:  # imported as `tools.*` from inside library/
    from tools import evidence_query

SCHEMA_VERSION = 1

SPEECH = "speech"
IMAGE = "image"
ACTION = "action"
RELATIONSHIP = "relationship"
SOUND = "sound"
OCR = "ocr"
TECHNICAL = "technical"

FAMILIES = (SPEECH, IMAGE, ACTION, RELATIONSHIP, SOUND, OCR, TECHNICAL)
"""The seven RET-027 requirement families."""

# Lane names as they appear in `prov-{digest}-{lane}` provenance ids and
# in `evidence_query` required-lane reporting.
LANE_SPEECH = "m1"
LANE_SCENE = "m4"
LANE_SOUND = "m5"
LANE_EVENT = "m7"
LANE_RELATION = "m10"
LANE_OCR = "ocr_extraction"
LANE_OBJECT_DETECTION = "object_detection"

# Action verbs this planner maps onto lane-3 temporal-action candidate
# kinds (`temporal_action_candidates.py`). Anything else stays a
# free-text action needing a verification producer.
ACTION_KIND_BY_VERB = {
    "drink": "drink_from_cup",
    "drinks": "drink_from_cup",
    "drinking": "drink_from_cup",
    "sip": "drink_from_cup",
    "sips": "drink_from_cup",
    "pick up": "pick_up_put_down",
    "picks up": "pick_up_put_down",
    "picking up": "pick_up_put_down",
    "put down": "pick_up_put_down",
    "puts down": "pick_up_put_down",
    "putting down": "pick_up_put_down",
    "touch face": "hand_to_face",
    "touches face": "hand_to_face",
    "touching face": "hand_to_face",
    "hand to face": "hand_to_face",
    "covers mouth": "hand_to_face",
    "covering mouth": "hand_to_face",
    "adjust cap": "hand_to_face",
    "adjusting cap": "hand_to_face",
}

_SOUND_LABELS = (
    "laughter", "laughing", "applause", "clapping", "music", "doorbell",
    "barking", "siren", "alarm", "engine", "hum", "silence", "quiet",
    "cheering", "cough", "coughing", "footsteps", "rain", "wind",
)

_TECHNICAL_CUES = (
    "stable", "steady", "static", "still", "locked-off", "locked off",
    "low motion", "no motion", "wide shot", "wide", "close-up", "close up",
    "sharp", "in focus", "focused", "bright", "dark", "well lit",
    "well-lit", "unobstructed", "clear view", "no blur", "blurry",
    "shaky", "handheld", "panning", "tilting", "zooming",
)

_SPATIAL_CUES = (
    "on the left", "on the right", "beside", "next to", "holding",
    "holds", "handing", "hand to", "looking at", "pointing at",
    "in hand", "in her hand", "in his hand",
)

_ACTION_CUES = tuple(sorted(set(
    list(ACTION_KIND_BY_VERB)
    + ["open", "opens", "opening", "close", "closes", "closing",
       "enter", "enters", "entering", "exit", "exits", "exiting",
       "demonstrate", "demonstrates",
       "gesture", "gestures", "gesturing", "point", "points", "pointing",
       "wave", "waves", "waving", "nod", "nods", "nodding",
       "stand up", "stands up", "standing up", "sit down", "sits down",
       "hand over", "hands over", "give", "gives", "take", "takes",
       "lift", "lifts", "lifting", "carry", "carries", "drop", "drops",
       ])))

_SPEECH_CUES = (
    "says", "say", "saying", "said", "discusses", "discussing", "discuss",
    "talks about", "talk about", "talking about", "mentions", "mention",
    "mentioning", "explains", "explain", "explaining", "explanation of",
    "conversation about", "speaks about", "speaking about", "speaking",
    "dialogue", "says the word", "answers", "asks about",
)

_IMAGE_CUES = (
    "wearing", "chart", "diagram", "slide", "whiteboard",
    "laptop", "phone", "product", "person", "people", "face", "car",
    "dog", "cat", "food", "book",
)
"""Concrete visible nouns only. Bare framing words ("visible",
"appears", "on screen", "in frame") modify a requirement and never
name an object: compiling them into object-detection plans would
schedule detector work for a word no detector answers."""

_OCR_CUES = ("on-screen text", "on screen text", "text reading",
             "text read", "reads", "read on screen", "caption", "captions",
             "subtitle", "subtitles", "lower third", "title card")

_OCR_STOP_AFTER = re.compile(
    r"\s+(while|and then|before|after|without|with no|during|appears?|"
    r"on screen|visible)\s*",
    re.IGNORECASE)


def _ocr_value_after(text: str, hit: int, cue: str) -> str | None:
    """On-screen wording after a 'text read(s)/reading' anchor."""
    tail = text[hit + len(cue):].strip(" :")
    if not tail:
        return None
    tail = _OCR_STOP_AFTER.split(tail, maxsplit=1)[0]
    tail = tail.strip().rstrip(".,;")
    if len(tail) < 2 or len(tail) > 120:
        return None
    return tail

# Values that name the same measurable presence and de-duplicate.
_VALUE_ALIASES = {
    "people": "person", "faces": "face", "persons": "person",
}

# Scaffolding and vibe words that never name a measurable thing. Any
# other unmatched alphabetic token is treated as an open-vocabulary
# object name (the detector tier answers names, never vibes): silently
# dropping "backpack" while answering "person" would weaken the
# conjunction without saying so.
_STOPWORDS = frozenset(["footage", "moment", "part", "clip", "scene", "video", "shot", "shots", "take", "interview", "episode", "podcast", "vlog", "film", "movie", "photo", "picture", "frame", "sequence", "where", "find", "visible", "appears", "appearing", "appear", "screen", "onscreen", "speaker", "speakers", "show", "shows", "showing", "showed", "look", "looks", "looking", "get", "gets", "give", "gives", "cover", "covers", "touch", "touches", "touching", "puts", "put", "make", "makes", "feel", "feels", "seem", "seems", "become", "becomes", "after", "before", "doing", "ever", "one", "boundary", "very", "really", "just", "more", "most", "best", "good", "nice", "great", "cool", "warm", "cold", "hot", "beautiful", "pretty", "cinematic", "aesthetic", "lovely", "amazing", "awesome", "stunning", "gorgeous", "vivid", "fast", "slow", "quick", "quickly", "briefly", "long", "short", "big", "small", "large", "tiny", "whole", "entire", "full", "half", "same", "other", "another", "each", "every", "any", "some", "few", "many", "much", "more", "less", "first", "last", "next", "previous", "same", "such", "only", "even", "also", "too", "either", "neither", "not", "never", "always", "often", "sometimes", "usually", "the", "a", "an", "of", "about", "on", "for", "and", "or", "to", "in", "is", "are", "was", "were", "be", "been", "it", "its", "this", "that", "these", "those", "there", "here", "which", "who", "whom", "whose", "what", "when", "how", "why", "my", "your", "his", "her", "their", "our", "me", "him", "them", "us", "we", "you", "i", "he", "she", "they", "do", "does", "did", "done", "have", "has", "had", "having", "with", "without", "from", "into", "over", "under", "again", "once", "twice", "during", "while", "then", "than", "s", "t", "d", "m", "ll", "re", "ve"])


class RequirementGraphError(ValueError):
    """A query that cannot be planned without guessing."""


def _find_cue_spans(text: str, cues: tuple[str, ...]) -> list[tuple[int, str]]:
    """Word-boundary cue hits, longest cue first at each position."""
    lowered = text.lower()
    spans = []
    for cue in cues:
        start = 0
        while True:
            hit = lowered.find(cue, start)
            if hit < 0:
                break
            before = lowered[hit - 1] if hit > 0 else " "
            after = lowered[hit + len(cue)] if hit + len(cue) < len(lowered) else " "
            if (before.isalnum() or after.isalnum()):
                start = hit + 1
                continue
            spans.append((hit, cue))
            start = hit + len(cue)
    return spans


def _suppress_overlaps(hits: list[tuple[int, str, str]]
                       ) -> list[tuple[int, str, str]]:
    """Keep the longest cue where hits overlap; one cue per span.

    Hits are (position, cue, family). Without this, "discuss" fires
    inside "discusses" and "open" inside "opens", doubling
    requirements the query states once.
    """
    ordered = sorted(hits, key=lambda row: (row[0], -len(row[1])))
    kept: list[tuple[int, str, str]] = []
    covered: list[tuple[int, int]] = []
    for hit, cue, family in ordered:
        end = hit + len(cue)
        if any(hit < stop and start < end for start, stop in covered):
            continue
        kept.append((hit, cue, family))
        covered.append((hit, end))
    return sorted(kept)


def _quoted_strings(text: str) -> list[str]:
    return re.findall(r"'([^']{2,80})'|\"([^\"]{2,80})\"", text)


def _topic_after_about(text: str, hit: int, cue: str) -> str | None:
    tail = text[hit + len(cue):].strip(" :")
    if not tail:
        return None
    tail = re.split(r"\s+(while|and then|before|after|without|with no)\s+",
                    tail, maxsplit=1)[0]
    tail = tail.strip().rstrip(".,;")
    if len(tail) < 2 or len(tail) > 120:
        return None
    return tail


def parse_query(text: str) -> dict[str, Any]:
    """Parse a user query into a typed requirement graph, or raise.

    The graph is ``{"requirements": [...], "order": [...],
    "conjunction": True}``. ``order`` holds ``[before_id, after_id]``
    pairs for temporal-ordering cues (RET-029). Every requirement in
    one graph is conjunctive (RET-028): the answer must satisfy all of
    them on overlapping intervals. Refusals: empty text, no
    recognizable family cue, or a top-level " or " disjunction (two
    different answers, never one plan).
    """
    if not isinstance(text, str) or not text.strip():
        raise RequirementGraphError("a query asks something in words")
    cleaned = " ".join(text.split())
    if re.search(r"\bor\b", cleaned.lower()):
        raise RequirementGraphError(
            "a query with 'or' asks two different questions: "
            "split it into two queries, never one plan")
    requirements: list[dict[str, Any]] = []
    order: list[list[str]] = []
    positions: list[tuple[int, str]] = []

    def add(family: str, cue_text: str, hit: int,
            **fields: Any) -> dict[str, Any]:
        requirement = {"id": f"req-{len(requirements) + 1}",
                       "family": family, "cue": cue_text.strip(),
                       "negated": False, "order_after": [],
                       **fields}
        requirements.append(requirement)
        positions.append((hit, requirement["id"]))
        return requirement

    lowered = cleaned.lower()
    topic_spans: list[tuple[int, int]] = []
    negated_spans = [(m.start(), m.end()) for m in re.finditer(
        r"\b(no|without|with no|never|nobody|nothing|isn't|aren't"
        r"|not .*visible|no .*visible)\b", lowered)]

    def is_negated(hit: int, cue: str) -> bool:
        window_start = max(0, hit - 24)
        for start, end in negated_spans:
            if start >= window_start and end <= hit + len(cue):
                return True
        return False

    def canonical(value: str) -> str:
        lowered_value = value.lower()
        direct = _VALUE_ALIASES.get(lowered_value, lowered_value)
        if direct != lowered_value:
            return direct
        # Object names are singular: "cars" asks about "car",
        # because the detector tier labels categories, never plurals.
        if lowered_value.endswith("s") and len(lowered_value) > 3:
            singular = lowered_value[:-1]
            if (singular in _IMAGE_CUES
                    or singular in _VALUE_ALIASES.values()):
                return _VALUE_ALIASES.get(singular, singular)
        return direct

    # OCR first: quoted or anchored on-screen text, so later framing
    # words ("on screen", "visible") read as its modifiers, not as
    # separate image requirements.
    quotes = [first or second for first, second in _quoted_strings(cleaned)]
    ocr_hits = _find_cue_spans(cleaned, _OCR_CUES)
    if ocr_hits or (quotes and re.search(
            r"\b(screen|text|caption|chart|slide|reads|says)\b", lowered)):
        quoted = quotes[0] if quotes else None
        anchor_hit, anchor = (ocr_hits[0] if ocr_hits
                              else (max(0, lowered.find("text")),
                                    "quoted on-screen text"))
        value = quoted or _ocr_value_after(cleaned, anchor_hit, anchor)
        key = (OCR, "text", canonical(value or anchor))
        seen_keys: set[tuple[str, str, str]] = {key}
        req = add(OCR, anchor, anchor_hit, kind="text",
                  value=value or anchor)
        req["negated"] = is_negated(anchor_hit, anchor)
    else:
        seen_keys = set()

    # One scanner across families: the longest cue wins each span, so
    # "discusses" fires once and "discuss" does not fire inside it.
    hits: list[tuple[int, str, str]] = []
    for cue in _SPEECH_CUES:
        hits.extend((hit, cue, SPEECH)
                    for hit, _ in _find_cue_spans(cleaned, (cue,)))
    for label in _SOUND_LABELS:
        hits.extend((hit, label, SOUND)
                    for hit, _ in _find_cue_spans(cleaned, (label,)))
    for cue in _TECHNICAL_CUES:
        hits.extend((hit, cue, TECHNICAL)
                    for hit, _ in _find_cue_spans(cleaned, (cue,)))
    for cue in _SPATIAL_CUES:
        hits.extend((hit, cue, RELATIONSHIP)
                    for hit, _ in _find_cue_spans(cleaned, (cue,)))
    for cue in _ACTION_CUES:
        hits.extend((hit, cue, ACTION)
                    for hit, _ in _find_cue_spans(cleaned, (cue,)))
    for cue in _IMAGE_CUES:
        hits.extend((hit, cue, IMAGE)
                    for hit, _ in _find_cue_spans(cleaned, (cue,)))

    # Audio-visual join needs the speaker and the sync adjacent: a
    # distant "speaker" plus a distant "visible" is a conjunction of
    # two requirements, not one synchronized claim (RET-034).
    av_sync = bool(re.search(
        r"visible speaker|speaking on camera|talking head|"
        r"synchroni[sz]ed dialogue|synchroni[sz]ed speech", lowered))
    if av_sync:
        add(RELATIONSHIP, "visible speaker with synchronized dialogue",
            lowered.find("visible"), kind="audio_visual")

    # Possessive-tolerant action patterns: "covers her mouth" and
    # "touching his face" are the same candidates as the bare verbs,
    # which word-boundary cues never match across the possessive.
    _POSSESSIVE_ACTIONS = (
        (r"covers?(?:\s+(?:her|his|their|the))?\s+mouth", "hand_to_face"),
        (r"touch(?:es|ing)?(?:\s+(?:her|his|their|the))?\s+face",
         "hand_to_face"),
        ((r"hand\s+(?:to|on|near)\s+(?:(?:her|his|their|the)\s+)?"
          r"(?:face|mouth)"), "hand_to_face"),
        (r"picks?(?:\s+(?:it|them|that|this))?\s+up", "pick_up_put_down"),
        (r"puts?(?:\s+(?:it|them|that|this))?\s+down", "pick_up_put_down"),
    )
    possessive_spans: list[tuple[int, int]] = []
    for pattern, kind in _POSSESSIVE_ACTIONS:
        for match in re.finditer(pattern, lowered):
            key = (ACTION, "candidate", kind)
            if key in seen_keys:
                possessive_spans.append((match.start(), match.end()))
                continue
            seen_keys.add(key)
            req = add(ACTION, match.group(0), match.start(),
                      kind="candidate", value=kind)
            req["negated"] = is_negated(match.start(), match.group(0))
            possessive_spans.append((match.start(), match.end()))

    for hit, cue, family in _suppress_overlaps(
            [(hit, cue, family) for hit, cue, family in hits
             if not any(hit < stop and start < hit + len(cue)
                        for start, stop in possessive_spans)]):
        if family == SPEECH:
            if cue in ("speaking", "dialogue"):
                key = (SPEECH, "presence", "")
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                req = add(SPEECH, cue, hit, kind="presence")
            else:
                topic = _topic_after_about(cleaned, hit, cue)
                if topic:
                    keyword = canonical(_topic_keyword(topic))
                    key = (SPEECH, "topic", keyword)
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    req = add(SPEECH, f"{cue} {topic}", hit,
                              kind="topic", value=topic)
                    # The topic is spoken about, not a visible object:
                    # cover its words so the fallback never queries
                    # the detector for "revenue".
                    topic_at = lowered.find(topic.lower(), hit + len(cue))
                    if topic_at >= 0:
                        topic_spans.append(
                            (topic_at, topic_at + len(topic)))
                else:
                    key = (SPEECH, "presence", "")
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    req = add(SPEECH, cue, hit, kind="presence")
            req["negated"] = is_negated(hit, cue)
        elif family == SOUND:
            key = (SOUND, "event", canonical(cue))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            req = add(SOUND, cue, hit, kind="event", value=cue)
            req["negated"] = is_negated(hit, cue)
        elif family == TECHNICAL:
            key = (TECHNICAL, "constraint", canonical(cue))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            req = add(TECHNICAL, cue, hit, kind="constraint", value=cue)
            req["negated"] = is_negated(hit, cue)
        elif family == RELATIONSHIP:
            if av_sync and cue == "visible":
                continue
            key = (RELATIONSHIP, "spatial", canonical(cue))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            req = add(RELATIONSHIP, cue, hit, kind="spatial", value=cue)
            req["negated"] = is_negated(hit, cue)
        elif family == ACTION:
            kind = ACTION_KIND_BY_VERB.get(cue)
            key = (ACTION, "candidate" if kind else "open",
                   canonical(kind or cue))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            req = add(ACTION, cue, hit,
                      kind="candidate" if kind else "open",
                      value=kind or cue)
            req["negated"] = is_negated(hit, cue)
        elif family == IMAGE:
            key = (IMAGE, "presence", canonical(cue))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            req = add(IMAGE, cue, hit, kind="presence", value=cue)
            req["negated"] = is_negated(hit, cue)

    # (OCR was parsed before the scan above.)

    # Open-vocabulary fallback: unmatched content words name visible
    # things the fixed noun list does not know. Covered spans (every
    # matched cue plus the OCR anchor and value) never contribute;
    # stopwords never become queries.
    covered_spans: list[tuple[int, int]] = list(topic_spans)
    covered_spans.extend(possessive_spans)
    for hit, cue, _family in _suppress_overlaps(hits):
        covered_spans.append((hit, hit + len(cue)))
    for req in requirements:
        if req.get("family") == OCR:
            for part in (req.get("cue", ""), str(req.get("value", ""))):
                words = part.lower().split()
                cursor = 0
                for word in words:
                    at = lowered.find(word, cursor)
                    if at >= 0:
                        covered_spans.append((at, at + len(word)))
                        cursor = at + len(word)
    for match in re.finditer(r"[a-z][a-z\-]{2,30}", lowered):
        word, start = match.group(0), match.start()
        if any(start < stop and begin < match.end()
               for begin, stop in covered_spans):
            continue
        if word in _STOPWORDS:
            continue
        key = (IMAGE, "presence", canonical(word))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        req = add(IMAGE, word, start, kind="presence",
                  value=canonical(word))
        req["negated"] = is_negated(start, word)

    if not requirements:
        raise RequirementGraphError(
            "no speech, image, action, relationship, sound, ocr or "
            "technical requirement is recognizable: name what should be "
            "seen, heard or measured, never a vibe")

    # Temporal ordering cues between the requirements in textual order,
    # anchored on recorded cue positions (RET-029).
    order_markers = [(m.start(), m.group(0)) for m in re.finditer(
        r"\band then\b|\bthen\b|\bbefore\b|\bafter\b|\bfollowed by\b",
        lowered)]
    if order_markers and len(requirements) >= 2:
        ordered_positions = sorted(positions)
        event_like = {req["id"] for req in requirements
                      if req["family"] in (ACTION, RELATIONSHIP)}
        for mark_pos, marker in order_markers:
            before = [req_id for pos, req_id in ordered_positions
                      if pos < mark_pos]
            after = [req_id for pos, req_id in ordered_positions
                     if pos > mark_pos]
            if "after" in marker:
                before, after = after, before
            if before and after:
                # An ordering constrains EVENTS ("opens ... then shows"):
                # prefer the nearest action/relationship requirement on
                # each side over a nearer object mention. On the after
                # side the nearest event to the marker wins: in "A after
                # B" the anchor B is the marker's neighbour, and the
                # event of A is the requirement the marker governs, not
                # the earliest mention before it.
                event_before = [req_id for req_id in before
                                if req_id in event_like]
                first = event_before[-1] if event_before else before[-1]
                event_after = [req_id for req_id in after
                               if req_id in event_like]
                if event_after:
                    second = (event_after[0] if "after" not in marker
                              else event_after[-1])
                else:
                    second = after[0]
                if first != second and [first, second] not in order:
                    order.append([first, second])

    for first, second in order:
        target = next(req for req in requirements if req["id"] == second)
        if first not in target["order_after"]:
            target["order_after"].append(first)
    order = unique_ids(order)

    identifier = "query-" + _stable_suffix(cleaned)
    return {"schema_version": SCHEMA_VERSION, "id": identifier,
            "text": cleaned, "requirements": requirements, "order": order,
            "conjunction": True}


def unique_ids(pairs: list[list[str]]) -> list[list[str]]:
    """De-duplicated order pairs preserving first occurrence."""
    kept: list[list[str]] = []
    for pair in pairs:
        if pair not in kept:
            kept.append(pair)
    return kept


def _stable_suffix(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]


def compile_requirement(requirement: dict[str, Any], *,
                        scope: dict[str, Any] | None = None
                        ) -> dict[str, Any]:
    """Compile one requirement to a plan, a verify task, or unanswerable.

    ``plan`` carries an :func:`evidence_query.compile_plan` dict the
    Project Model can answer today. ``verify`` names a candidate lane
    and hint for bounded reanalysis (actions, relationships). A
    negated requirement compiles the positive plan with
    ``negated: True``: the caller reads ``confidently_absent`` and
    treats partial coverage as abstention (RET-031), never as proof.
    """
    family = requirement.get("family")
    kind = requirement.get("kind")
    value = requirement.get("value")
    negated = bool(requirement.get("negated"))
    scope = dict(scope or {})

    def plan(predicates: list[dict[str, Any]]) -> dict[str, Any]:
        compiled = evidence_query.compile_plan(predicates, scope=scope or None)
        return {"requirement_id": requirement["id"], "kind": "plan",
                "plan": compiled, "negated": negated,
                "required_lanes": compiled["required_lanes"]}

    def verify(lane: str, hint: dict[str, Any]) -> dict[str, Any]:
        return {"requirement_id": requirement["id"], "kind": "verify",
                "lane": lane, "hint": hint, "negated": negated}

    def unanswerable(reason: str, required_lane: str,
                     needed: str) -> dict[str, Any]:
        return {"requirement_id": requirement["id"],
                "kind": "unanswerable", "reason": reason,
                "required_lane": required_lane, "needed": needed,
                "negated": negated}

    if family == SPEECH:
        if kind == "presence":
            return plan([{"predicate": "event_span", "event": "speaking"}])
        if kind == "topic" and isinstance(value, str) and value:
            keyword = _topic_keyword(value)
            return plan([{"predicate": "measurement",
                           "measurement_type": "speech.word",
                           "equals": keyword}])
        return unanswerable("a speech requirement names a topic or speech "
                            "presence", LANE_SPEECH,
                            "a topic keyword or a speech-presence cue")
    if family == SOUND:
        if kind == "event" and isinstance(value, str) and value:
            return plan([{"predicate": "measurement",
                           "measurement_type": "audio.sound_event",
                           "equals": value}])
        return unanswerable("a sound requirement names the sound",
                            LANE_SOUND, "a sound-event label")
    if family == OCR:
        if kind == "text" and isinstance(value, str) and value:
            return plan([{"predicate": "measurement",
                           "measurement_type": "image.ocr_text_track",
                           "equals": value}])
        return unanswerable("an ocr requirement names the on-screen text",
                            LANE_OCR, "quoted on-screen text")
    if family == IMAGE:
        if kind == "presence" and isinstance(value, str) and value:
            if value in ("person", "people", "face"):
                return plan([{"predicate": "event_span",
                               "event": "on_screen"}])
            return plan([{"predicate": "measurement",
                           "measurement_type": "object.detection",
                           "equals": value}])
        return unanswerable("an image requirement names what is visible",
                            LANE_OBJECT_DETECTION, "a visible-object name")
    if family == ACTION:
        if kind == "candidate" and isinstance(value, str) and value:
            return verify(LANE_EVENT,
                          {"candidate_kind": value,
                           "cue": requirement.get("cue", "")})
        return unanswerable(
            f"the action '{requirement.get('cue', '')}' matches no "
            f"verified action candidate kind", LANE_EVENT,
            "a bounded action-verification producer for this verb")
    if family == RELATIONSHIP:
        if kind == "audio_visual":
            return plan([
                {"predicate": "event_span", "event": "on_screen"},
                {"predicate": "event_span", "event": "speaking"},
            ])
        if kind == "spatial":
            return verify(LANE_RELATION,
                          {"relation_cue": value,
                           "cue": requirement.get("cue", "")})
        return unanswerable("a relationship requirement names the relation "
                            "or the audio-visual join", LANE_RELATION,
                            "a spatial or audio-visual relation cue")
    if family == TECHNICAL:
        if kind == "constraint" and isinstance(value, str):
            measurement = _technical_measurement(value)
            if measurement is not None:
                return plan([{"predicate": "measurement", **measurement}])
            return unanswerable(
                f"the constraint '{value}' states no number, so no "
                f"measurement floor can be compiled from it",
                _technical_lane(value),
                "a calibrated technical-measurement floor for this "
                "constraint (sharpness, motion, luma)")
        return unanswerable("a technical requirement names the constraint",
                            "no-lane-yet", "a camera or quality constraint")
    raise RequirementGraphError(
        f"unknown requirement family {family!r}: the planner answers "
        f"speech, image, action, relationship, sound, ocr and technical "
        f"only")


def _topic_keyword(topic: str) -> str:
    words = re.findall(r"[a-z0-9]+", topic.lower())
    stop = {"the", "a", "an", "of", "about", "on", "for", "and", "or",
            "to", "in", "is", "are", "was", "were", "it", "this", "that"}
    for word in words:
        if word not in stop and len(word) > 2:
            return word
    return words[0] if words else topic.lower()


def _technical_lane(cue: str) -> str:
    """Nearest existing lane for a technical cue, or no-lane-yet.

    Sharpness and luma map onto migrated m3 image measurements; camera
    motion and framing stability have no migrated lane, so the planner
    says so instead of borrowing the scene lane.
    """
    lowered = cue.lower()
    if any(word in lowered for word in
           ("sharp", "focus", "blur", "bright", "luma", "lit", "dark",
            "expos", "flicker", "frozen", "black")):
        return "m3"
    return "no-lane-yet"


def _technical_measurement(cue: str) -> dict[str, Any] | None:
    """Map a technical cue with an explicit number to a measurement.

    Cues without numbers never map: a floor the query did not state
    would be an invented threshold, not evidence.
    """
    numbers = re.findall(r"\d+(?:\.\d+)?", cue)
    if not numbers:
        return None
    number = float(numbers[0])
    if "sharp" in cue or "focus" in cue or "blur" in cue:
        return {"measurement_type": "image.sharpness.laplacian_variance",
                "min": number}
    if "bright" in cue or "luma" in cue or "lit" in cue:
        return {"measurement_type": "image.luma.rec709_p50",
                "min": number}
    return None


def compile_graph(graph: dict[str, Any], *,
                  scope: dict[str, Any] | None = None
                  ) -> dict[str, Any]:
    """Compile every requirement in a parsed graph.

    Returns ``{"graph_id", "compiled": [...], "order", "answerable",
    "needs_verification", "unanswerable"}``. ``answerable`` lists the
    requirement ids with evidence plans; ``needs_verification`` lists
    the verify tasks; ``unanswerable`` lists the refusals with the
    lane that could answer them. A graph whose every requirement is
    unanswerable still returns (the caller reports what is missing);
    only an empty requirement list raises.
    """
    requirements = graph.get("requirements") or []
    if not requirements:
        raise RequirementGraphError("a graph plans at least one requirement")
    compiled = [compile_requirement(req, scope=scope)
                for req in requirements]
    return {
        "graph_id": graph.get("id"),
        "compiled": compiled,
        "order": list(graph.get("order") or []),
        "conjunction": bool(graph.get("conjunction", True)),
        "answerable": [row["requirement_id"] for row in compiled
                       if row["kind"] == "plan"],
        "needs_verification": [row["requirement_id"] for row in compiled
                               if row["kind"] == "verify"],
        "unanswerable": [row["requirement_id"] for row in compiled
                         if row["kind"] == "unanswerable"],
    }


def modalities_of(graph: dict[str, Any]) -> dict[str, list[str]]:
    """Required modalities per requirement id (ACQ-002)."""
    return {req["id"]: [req["family"]]
            for req in graph.get("requirements") or []}


def describe(graph: dict[str, Any]) -> list[str]:
    """One compact line per requirement for reasoning-model context.

    Carries ids, families, cues and order only: no transcripts, no
    payloads, no measurement rows (ACQ-029/030 support).
    """
    lines = []
    for req in graph.get("requirements") or []:
        marker = "NOT " if req.get("negated") else ""
        after = req.get("order_after") or []
        tail = f" after {','.join(after)}" if after else ""
        lines.append(f"{req['id']} [{req['family']}] "
                     f"{marker}{req.get('cue', '')}{tail}")
    return lines
