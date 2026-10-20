"""A retrieval question is MADE OF parts; this module names the parts.

"the moment he explains the pricing rule, while the pricing slide is up"
is not one requirement.  It is a claim about SPEECH (the words he says), a
claim about PICTURE (the slide), a claim about ON-SCREEN TEXT (what the
slide says) and a claim about WHEN (the pricing beat, not the small talk).

The shipped search fuses all of that into ONE score
(`library/tools/analysis/footage_query.py`), so a hit cannot say which
part it satisfied and an empty answer cannot say which part was never
searched.  An agent reasoning over the result has to guess which, and it
guesses wrong.

`decompose` is that guess removed: a query becomes a `RetrievalPlan` of
typed requirements, one per modality the query actually names.  It is
DETERMINISTIC - a closed table of cues, no model - because the point of
the whole lane is that the machinery exposes capabilities the model can
reason about.  A plan is something a reader can check and disagree with.

**Why a closed table and not a parser.**  A cue table is auditable: the
cues are IN this file, so "why did this query become a visual
requirement" has an answer you can point at.  A learned or model-written
decomposition would be better at prose nobody anticipated and worse at
being CHECKED, and every downstream module (`coverage`, `verify`) needs
to know which lane a requirement belongs to - which means a requirement
has to be a value, not a guess.

**Compound questions are the normal case.**  A clause may produce
SEVERAL requirements - "the shot where he laughs on camera" names a
visual requirement and an acoustic one - and the connective BETWEEN two
clauses is itself a requirement, because "before", "after" and "while"
are claims about TIME and about RELATION, not decoration.  Requirements
are never collapsed: an editor's question routinely spans modalities, and
collapsing them is the conflation this module exists to remove.

**What the plan is NOT.**  It is not a decision about what to keep in the
cut, and it does not rank.  It says what was asked and by which lane it
could be answered.  Ranking stays in the index; the editorial judgement
stays with the model.

Rules relocated from AGENTS.md 10.1
-----------------------------------
**A rule lives with the code it governs; AGENTS.md keeps the headline and
points at it.**  These rules govern this module.

**A retrieval requirement is TYPED, and the type names the LANE.**
`MODALITIES` is the enumeration; `REQUIREMENT_KINDS` says which kinds
each modality has.  A requirement with an unknown modality or kind raises
rather than matching nothing.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace

MODALITIES = ("textual", "visual", "acoustic", "temporal", "relational")
"""The five modalities a retrieval question can be made of.

Each answers a different question, so each is searched and judged on its
own:

* `textual`   - what is SAID, or written on screen.
* `visual`    - what is SEEN: objects, people, actions, states, scenes, framing.
* `acoustic`  - what is HEARD that is not speech.
* `temporal`  - WHEN: order, duration, repetition.
* `relational`- ACROSS SHOTS: the same subject, the other angle, continuity.
"""

REQUIREMENT_KINDS: dict[str, tuple[str, ...]] = {
    "textual": ("speech_content", "on_screen_text"),
    "visual": ("object", "person", "action", "state", "scene", "composition"),
    "acoustic": ("sound_event", "voice_character"),
    "temporal": ("ordering", "duration", "repetition", "interval"),
    "relational": ("same_subject", "shot_relation", "cross_clip"),
}
"""Which kinds each modality has.  A kind outside this table is an error,
not an unknown kind that matches nothing."""

# ── The cue tables ───────────────────────────────────────────────────
#
# Closed lists, on purpose (see the module docstring).  Each entry is a
# cue word or phrase; the cue that fired is recorded on the requirement,
# so a reader can see WHY a requirement exists.

SPEECH_CUES = (
    "say", "says", "said", "saying", "talk", "talks", "talked", "talking",
    "explain", "explains", "explained", "explaining", "mention", "mentions",
    "mentioned", "mentioning", "ask", "asks", "asked", "asking", "tell",
    "tells", "told", "telling", "describe", "describes", "described",
    "describing", "read", "reads", "reading", "speak", "speaks", "spoke",
    "speaking", "quote", "quotes", "quoted", "quoting", "discuss",
    "discusses", "discussed", "discussing", "narrate", "narrates",
    "narrating", "recount", "recounts", "recounting", "word", "words",
    "phrase", "phrases", "sentence", "sentences", "transcript",
)
"""Spoken CONTENT.  The words themselves are what a speech segment holds."""

ON_SCREEN_TEXT_CUES = (
    "on screen", "title card", "lower third", "lower-third", "caption",
    "captions", "subtitle", "subtitles", "sign", "signs", "signage",
    "banner", "headline", "text reads", "written text", "written on screen",
    "chiron", "chyrons", "graphic", "graphics",
)
"""Text that is WRITTEN rather than said.  It lives in the OCR lane, which
is a different measurement from the transcript lane."""

COMPOSITION_CUES = (
    "shot", "shots", "close-up", "closeup", "close ups", "wide", "medium",
    "angle", "angles", "camera", "cameras", "framing", "composition",
    "portrait", "b-roll", "b roll", "cutaway", "insert", "footage", "frame",
    "frames", "picture", "pictures", "view", "close", "establishing",
)
"""How the camera frames the subject.  A different question from WHAT is
in frame, and the index measures it as a facet."""

PERSON_CUES = (
    "man", "woman", "person", "people", "guy", "lady", "speaker", "host",
    "guest", "face", "interviewer", "interviewee", "presenter", "subject",
)
"""A person is in frame.  The index measures people by face track and
region, not by the prose a vision pass wrote."""

ACTION_CUES = (
    "gesture", "gestures", "gesturing", "expression", "expressions",
    "smiling", "smile", "frown", "frowning", "waving", "pointing",
    "nodding", "leaning", "moving", "movement", "motion", "handling",
    "holding", "typing", "writing", "wearing", "open", "opens", "opening",
    "close", "closes", "closing", "pick", "picks", "picking", "put",
    "puts", "placing", "drop", "drops", "dropping", "lift", "lifts",
    "turns", "turning", "push", "pushes", "pull", "pulls", "throw",
    "throws", "touch", "touches", "reach", "reaches", "hand over",
    "hands over", "picks up", "puts down",
)
"""Something is being DONE, which a still frame cannot establish."""

STATE_CUES = (
    "open", "opened", "closed", "raised", "lowered", "full", "empty",
    "upright", "upside down", "tilted", "folded", "unfolded", "broken",
    "locked", "unlocked", "covered", "uncovered", "spilled", "sliced",
    "lit", "unlit", "half", "bare",
)
"""The STATE of a thing.  A separate question from which thing: "the
open laptop" and "the laptop" are different searches, and only the first
can be answered by looking at whether the lid is up."""

SCENE_CUES = (
    "room", "studio", "office", "kitchen", "street", "city", "building",
    "background", "backdrop", "location", "scene", "setting", "outside",
    "inside", "environment", "surroundings",
)
"""WHERE it is.  A place, not a thing."""

OBJECT_CUES = (
    "laptop", "phone", "cellphone", "screen", "monitor", "slide", "deck",
    "chart", "graph", "table", "desk", "keyboard", "mouse", "cup", "mug",
    "coffee", "bottle", "book", "notebook", "paper", "pages", "whiteboard",
    "blackboard", "plant", "tree", "flower", "window", "door", "wall",
    "floor", "ceiling", "food", "meal", "plate", "microphone", "headphones",
    "glasses", "shirt", "jacket", "hat", "clock", "car", "vehicle", "bus",
    "road", "sky", "cloud", "mountain", "beach", "dog", "cat", "bird",
    "logo", "overlay", "lid", "box", "button", "remote", "glass", "napkin",
    "stick", "card",
)
"""A concrete thing in frame.  The index carries an object label for
these; whether one was measured is a coverage question, not a guess.

"train" is deliberately NOT here.  It is a vehicle in a shot and a verb
in a sentence, and an editorial query says "train" far more often as a
verb.  A cue that fires on the wrong half of the word is worse than no
cue: it routes the search to the frame lane for a line about training."""

SOUND_EVENT_CUES = (
    "laugh", "laughs", "laughing", "laughter", "giggle", "giggles",
    "chuckle", "chuckles", "applause", "clap", "claps", "clapping",
    "cheer", "cheers", "sigh", "sighs", "gasp", "gasps", "cough",
    "coughs", "sneeze", "sneezes", "scream", "screams", "shout",
    "shouts", "yell", "yells", "door", "doors", "slam", "slams", "bang",
    "bangs", "crash", "knock", "knocks", "thud", "chime", "chimes",
    "bell", "bells", "ring", "rings", "beep", "beeps", "buzzer", "alarm",
    "siren", "footstep", "footsteps", "engine", "pour", "pours", "sizzle",
    "clink", "rustle", "whistle", "music", "jingle", "ambience",
    "ambient", "room tone", "background noise", "quiet", "silence",
    "silent",
)
"""A sound that is not speech.  The index carries these as measured audio
events with a confidence; a sound the project never measured cannot be
found and must not be claimed."""

VOICE_CHARACTER_CUES = (
    "voice", "voiceover", "voice-over", "narration", "narrator",
    "tone of voice", "monologue",
)
"""HOW the voice sounds, as distinct from WHAT it says."""

REPETITION_CUES = (
    "first time", "second time", "third time", "fourth time", "fifth time",
    "again", "once more", "twice", "last time", "the last time",
)
"""A count of occurrences.  "the second time she opens the box" names an
ordinal, and the ordinal is part of the requirement, not decoration."""

DURATION_CUES = ("how long", "for how long", "pause", "pauses", "paused",
                 "for a moment", "for a second", "briefly", "shortly")
"""How long something lasts."""

SAME_SUBJECT_CUES = ("same person", "same man", "same woman", "same object",
                     "same laptop", "same screen", "same subject")
"""The SAME thing across two shots."""

SHOT_RELATION_CUES = ("other angle", "other camera", "reverse angle",
                      "second angle", "other side", "reaction shot",
                      "cutaway", "matching shot", "continuity", "cut to")
"""A shot that exists IN RELATION to another shot."""

CROSS_CLIP_CUES = ("other clip", "another take", "alt take", "elsewhere",
                   "other take", "b-roll clip", "another angle")
"""Evidence that lives in a different source file than the one asked
about."""

ORDINAL_WORDS = {"first": 1, "second": 2, "third": 3, "fourth": 4,
                 "fifth": 5}
"""The ordinal a repetition cue carries."""

# ── Clause splitting ─────────────────────────────────────────────────
#
# A compound query is split at the connectives that join two claims, so
# "he explains X while the slide is up" is two clauses joined by a
# connective, not one blob.  The connective is KEPT, because "before",
# "after" and "while" are claims about TIME and about RELATION - dropping
# them would lose the requirement the query actually made.

_CLAUSE_SPLIT = re.compile(
    r"\s*(,|;|\band\b|\bwhile\b|\bas\b|\bwhen\b|\bwith\b|\bduring\b|"
    r"\bafter\b|\bbefore\b|\bbut\b|\bplus\b|\balso\b|"
    r"\bthen\b|\bfinally\b)\s*",
    re.IGNORECASE,
)

ORDERING_CONNECTIVES = {"before": "before", "after": "after",
                         "then": "then", "finally": "finally"}
"""Connectives that say WHICH came first."""

INTERVAL_CONNECTIVES = {"while", "during", "throughout"}
"""Connectives that say two claims hold at the SAME time."""

PRESENCE_CUES = ("on camera", "in frame", "visible", "appears", "shows")
"""Words that say something is IN THE PICTURE, as against being talked
about."""

FRAMING_CUES = frozenset(COMPOSITION_CUES) | frozenset(PRESENCE_CUES)
"""The words that make a clause a claim about PICTURE.

A bare noun does not.  "the comparison of a ten person agency against a
company fifty times its size" names two things, and the request is for
the CONTENT about them; "a shot of a ten person agency" asks for the
PICTURE.  The difference is a framing word, and without one the noun is
a subject matter, not a thing to look at.

That is why the visual noun cues are gated on a framing cue, and why a
clause whose nouns have no framing cue is AMBIGUOUS rather than simply
visual: it may be the words about the laptop or the laptop itself, and
the honest answer is to look in both lanes and say which guess was made."""

_WORD = re.compile(r"[a-z0-9']+")

_STOPWORD_TEXT = (
    "a an the and or of to in on at is are was were be been being am it its "
    "this that these those there here with for from as by he she they we you "
    "i his her their our your my me him them but so if then than into over "
    "under out up down about what when where who whom how why which does do "
    "did doing done has have had can could will would should shall may might "
    "must not no nor too very just also more most other some such only own "
    "same s t don now"
)
_STOPWORDS = frozenset(_STOPWORD_TEXT.split())

_WORD_SETS = {
    "textual:speech_content": frozenset(SPEECH_CUES),
    "textual:on_screen_text": frozenset(ON_SCREEN_TEXT_CUES),
    "visual:composition": frozenset(COMPOSITION_CUES),
    "visual:person": frozenset(PERSON_CUES),
    "visual:action": frozenset(ACTION_CUES),
    "visual:state": frozenset(STATE_CUES),
    "visual:scene": frozenset(SCENE_CUES),
    "visual:object": frozenset(OBJECT_CUES),
    "acoustic:sound_event": frozenset(SOUND_EVENT_CUES),
    "acoustic:voice_character": frozenset(VOICE_CHARACTER_CUES),
    "temporal:repetition": frozenset(REPETITION_CUES),
    "temporal:duration": frozenset(DURATION_CUES),
    "relational:same_subject": frozenset(SAME_SUBJECT_CUES),
    "relational:shot_relation": frozenset(SHOT_RELATION_CUES),
    "relational:cross_clip": frozenset(CROSS_CLIP_CUES),
}

#: The lane each kind of requirement is answered by.  This is the
#: routing table the search uses, so a requirement knows where it goes.
REQUIREMENT_LANE = {
    "textual:speech_content": "text",
    "textual:on_screen_text": "ocr",
    "visual:composition": "visual",
    "visual:person": "visual",
    "visual:action": "visual",
    "visual:state": "visual",
    "visual:scene": "visual",
    "visual:object": "visual",
    "acoustic:sound_event": "sound",
    "acoustic:voice_character": "sound",
    "temporal:repetition": "time",
    "temporal:ordering": "time",
    "temporal:duration": "time",
    "temporal:interval": "time",
    "relational:same_subject": "relational",
    "relational:shot_relation": "relational",
    "relational:cross_clip": "relational",
}
"""Which lane answers which requirement kind.  `text` is the text index
(`ren search`), `visual` the frame lane (`ren search --visual`), `ocr`
the on-screen-text lane, `sound` the measured audio-event lane, `time`
the timeline itself, `relational` the project graph."""


class UnknownRequirement(ValueError):
    """A modality or kind that this module does not have."""


def requirement_kinds(modality: str) -> tuple[str, ...]:
    """The kinds one modality has, or raise for an unknown modality."""
    try:
        return REQUIREMENT_KINDS[modality]
    except KeyError:
        raise UnknownRequirement(
            f"unknown retrieval modality {modality!r}; "
            f"known: {', '.join(MODALITIES)}") from None


@dataclass(frozen=True)
class RetrievalRequirement:
    """One thing the query asks for, in one modality.

    `description` is the clause of the query that produced it, so a
    reader sees the requirement in the editor's own words.  `cues` are
    the cue phrases that fired, so the reader can see why.  `terms` are
    the content words of the clause, which is what a lane searches for.
    """

    modality: str
    kind: str
    description: str = ""
    terms: tuple[str, ...] = ()
    cues: tuple[str, ...] = ()
    mandatory: bool = True
    ordinal: int | None = None
    relation: str | None = None
    clause: str = ""
    clauses: tuple[str, ...] = ()
    ambiguous: bool = False

    def __post_init__(self) -> None:
        if self.modality not in REQUIREMENT_KINDS:
            raise UnknownRequirement(
                f"unknown retrieval modality {self.modality!r}; "
                f"known: {', '.join(MODALITIES)}")
        if self.kind not in REQUIREMENT_KINDS[self.modality]:
            raise UnknownRequirement(
                f"unknown {self.modality} requirement kind {self.kind!r}; "
                f"known: {', '.join(REQUIREMENT_KINDS[self.modality])}")

    @property
    def key(self) -> str:
        """``modality:kind`` - the identity used by the coverage table."""
        return f"{self.modality}:{self.kind}"

    @property
    def lane(self) -> str:
        """The lane that answers this requirement."""
        return REQUIREMENT_LANE[self.key]

    def to_dict(self) -> dict:
        out = {
            "modality": self.modality,
            "kind": self.kind,
            "lane": self.lane,
            "description": self.description,
            "terms": list(self.terms),
            "cues": list(self.cues),
            "mandatory": self.mandatory,
            "ambiguous": self.ambiguous,
        }
        if self.ordinal is not None:
            out["ordinal"] = self.ordinal
        if self.relation is not None:
            out["relation"] = self.relation
        if len(self.clauses) > 1:
            out["clauses"] = list(self.clauses)
        return out


@dataclass(frozen=True)
class RetrievalPlan:
    """A query, decomposed into the requirements it actually names.

    An EMPTY requirement list is not a plan: `decompose` always returns
    at least one requirement, because a query that names nothing still asks
    the index something, and the honest answer to "what is this asking?"
    is "the content" rather than "nothing".
    """

    query: str
    requirements: tuple[RetrievalRequirement, ...] = field(default_factory=tuple)

    @property
    def modalities(self) -> frozenset[str]:
        """The modalities this plan asks about."""
        return frozenset(r.modality for r in self.requirements)

    @property
    def lanes(self) -> tuple[str, ...]:
        """The lanes this plan needs answered, in a stable order."""
        seen: list[str] = []
        for requirement in self.requirements:
            if requirement.lane not in seen:
                seen.append(requirement.lane)
        return tuple(seen)

    def for_modality(self, modality: str) -> tuple[RetrievalRequirement, ...]:
        """The requirements of one modality."""
        return tuple(r for r in self.requirements if r.modality == modality)

    @property
    def mandatory_modalities(self) -> frozenset[str]:
        """The modalities whose absence makes the answer incomplete."""
        return frozenset(r.modality for r in self.requirements if r.mandatory)

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "modalities": sorted(self.modalities),
            "lanes": list(self.lanes),
            "requirements": [r.to_dict() for r in self.requirements],
        }


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _WORD.findall(text or "")]


def _content_tokens(text: str) -> tuple[str, ...]:
    """The content words of a clause, in order, without duplicates."""
    out: list[str] = []
    for token in _tokens(text):
        if token in _STOPWORDS or len(token) < 2:
            continue
        if token not in out:
            out.append(token)
    return tuple(out)


def _contains_sequence(tokens: Sequence[str], phrase: Sequence[str]) -> bool:
    if not phrase:
        return False
    span = len(phrase)
    for index in range(len(tokens) - span + 1):
        if tuple(tokens[index:index + span]) == tuple(phrase):
            return True
    return False


def _cues_in(clause: str, cues: Iterable[str]) -> tuple[str, ...]:
    """Which cue phrases occur in a clause, longest first.

    Matching is on TOKEN SEQUENCES, so "b-roll" matches "a b-roll clip"
    and "the second time" does not match "the second timeline".  A cue is
    matched whole or not at all - a cue that happened to be a prefix of
    another word does not fire.
    """
    tokens = _tokens(clause)
    hits = []
    for cue in cues:
        phrase = _tokens(cue)
        if phrase and _contains_sequence(tokens, phrase):
            hits.append(cue)
    return tuple(sorted(set(hits), key=lambda c: (-len(c), c)))


def _ordinal_of(clause: str) -> int | None:
    tokens = _tokens(clause)
    for index, token in enumerate(tokens):
        if token in ORDINAL_WORDS:
            following = tokens[index + 1:index + 2]
            if following == ["time"]:
                return ORDINAL_WORDS[token]
    return None


def _clauses_and_connectives(query: str) -> list[tuple[str, str]]:
    """Each clause, and the connective that follows it.

    The alignment matters: ", while" splits into two matches, so the
    connective and the clause it joins are not adjacent in the split.
    Keeping them paired is what stops a connective being attributed to
    the wrong clause.
    """
    parts = _CLAUSE_SPLIT.split(query or "")
    texts = [part.strip(" .?!") for part in parts[0::2]]
    connectives = [parts[position].strip().lower()
                   for position in range(1, len(parts), 2)]
    connectives.append("")
    return list(zip(texts, connectives))


def _requirements_for_clause(clause: str) -> list[RetrievalRequirement]:
    """The requirements one clause names.

    Two cues of the SAME kind in one clause are ONE requirement: "an
    open laptop with its lid raised" asks about the state once, not
    twice.  The cues are merged onto the one requirement, so a reader
    sees that both "open" and "raised" were asked about.

    A visual noun only claims the PICTURE when the clause carries a
    framing cue.  Without one it is ambiguous - the words about the
    laptop, or the laptop - and both readings are kept: the visual
    requirement is marked ambiguous, and a textual one is added beside
    it.  The plan then searches both lanes and says it guessed.
    """
    merged: dict[str, RetrievalRequirement] = {}
    framing = bool(_cues_in(clause, FRAMING_CUES))
    ambiguous_nouns = False
    for key, cues in _WORD_SETS.items():
        modality, kind = key.split(":", 1)
        fired = _cues_in(clause, cues)
        if not fired:
            continue
        if modality == "visual" and not framing:
            ambiguous_nouns = True
            requirement = RetrievalRequirement(
                modality=modality,
                kind=kind,
                description=clause,
                terms=_content_tokens(clause),
                cues=fired,
                clause=clause,
                ambiguous=True,
            )
        else:
            requirement = RetrievalRequirement(
                modality=modality,
                kind=kind,
                description=clause,
                terms=_content_tokens(clause),
                cues=fired,
                clause=clause,
            )
        if kind == "repetition":
            ordinal = _ordinal_of(clause)
            if ordinal is not None:
                requirement = replace(requirement, ordinal=ordinal)
        existing = merged.get(key)
        if existing is None:
            merged[key] = requirement
            continue
        merged[key] = replace(
            existing,
            cues=tuple(sorted(set(existing.cues) | set(fired),
                              key=lambda c: (-len(c), c))),
            ambiguous=existing.ambiguous and requirement.ambiguous)
    if ambiguous_nouns and "textual:speech_content" not in merged:
        merged["textual:speech_content"] = RetrievalRequirement(
            modality="textual",
            kind="speech_content",
            description=clause,
            terms=_content_tokens(clause),
            cues=(),
            clause=clause,
            ambiguous=True,
        )
    return list(merged.values())


def _requirements_for_connective(connective: str, left: str, right: str
                                 ) -> list[RetrievalRequirement]:
    """The requirement a connective between two clauses makes."""
    joined = f"{left} {connective} {right}".strip()
    terms = _content_tokens(joined)
    if connective in ORDERING_CONNECTIVES:
        return [RetrievalRequirement(
            modality="temporal",
            kind="ordering",
            description=joined,
            terms=terms,
            cues=(connective,),
            relation=ORDERING_CONNECTIVES[connective],
            clause=joined,
        )]
    if connective in INTERVAL_CONNECTIVES:
        return [RetrievalRequirement(
            modality="temporal",
            kind="interval",
            description=joined,
            terms=terms,
            cues=(connective,),
            clause=joined,
        )]
    return []


def _merge_requirements(requirements: Sequence[RetrievalRequirement]
                        ) -> list[RetrievalRequirement]:
    """One requirement per kind, carrying every clause that named it.

    "an open laptop with its lid raised" is two clauses and ONE question
    about an object's state.  Merging them keeps the plan readable and
    keeps the coverage verdict per KIND, which is the granularity the
    coverage report measures at.  The clauses are kept, so nothing about
    where the requirement came from is lost.
    """
    merged: dict[str, RetrievalRequirement] = {}
    for requirement in requirements:
        existing = merged.get(requirement.key)
        if existing is None:
            merged[requirement.key] = replace(
                requirement, clauses=(requirement.clause,))
            continue
        terms = list(existing.terms)
        for term in requirement.terms:
            if term not in terms:
                terms.append(term)
        cues = tuple(sorted(set(existing.cues) | set(requirement.cues),
                            key=lambda c: (-len(c), c)))
        clauses = list(existing.clauses)
        for clause in requirement.clauses or (requirement.clause,):
            if clause and clause not in clauses:
                clauses.append(clause)
        merged[requirement.key] = replace(
            existing,
            terms=tuple(terms),
            cues=cues,
            clauses=tuple(clauses),
            ambiguous=existing.ambiguous and requirement.ambiguous,
            ordinal=existing.ordinal if existing.ordinal is not None
            else requirement.ordinal,
            relation=existing.relation or requirement.relation)
    return list(merged.values())


def decompose(query: str) -> RetrievalPlan:
    """Split a query into the typed requirements it names.

    The decomposition is deterministic and total: the same query always
    yields the same plan, and a query that names no cue still yields one
    requirement (the content of what is said), because "what was asked"
    always has an answer.

    A clause may produce SEVERAL requirements - "the shot where he laughs
    on camera" names a visual requirement and an acoustic one - and each
    one is kept.  Requirements are not exclusive: an editor's question
    routinely spans modalities, and collapsing them is the conflation
    this module exists to remove.
    """
    pairs = _clauses_and_connectives(query)
    requirements: list[RetrievalRequirement] = []
    for text, _ in pairs:
        if text:
            requirements.extend(_requirements_for_clause(text))
    for index, (text, connective) in enumerate(pairs):
        if not connective or connective in (",", ";"):
            continue
        left = text or next((previous for previous, _ in reversed(pairs[:index])
                             if previous), "")
        right = next((later for later, _ in pairs[index + 1:] if later), "")
        if not left or not right:
            continue
        requirements.extend(_requirements_for_connective(connective, left, right))
    if not requirements:
        requirements.append(RetrievalRequirement(
            modality="textual",
            kind="speech_content",
            description=query,
            terms=_content_tokens(query),
            cues=(),
            clause=query,
            clauses=(query,),
        ))
    return RetrievalPlan(query=query,
                         requirements=tuple(_merge_requirements(requirements)))


def plan_for(query: str) -> dict:
    """`decompose`, as a plain dict, for callers that serialise a plan."""
    return decompose(query).to_dict()


def describe(plan: RetrievalPlan) -> str:
    """A one-line, human-readable reading of a plan."""
    parts = []
    for requirement in plan.requirements:
        ordinal = ""
        if requirement.ordinal is not None:
            ordinal = f"#{requirement.ordinal}"
        parts.append(f"{requirement.key}{ordinal}")
    return ", ".join(parts)


def merge_plans(plans: Sequence[RetrievalPlan]) -> RetrievalPlan:
    """Combine plans, keeping the first requirement of each key."""
    seen: set[str] = set()
    requirements: list[RetrievalRequirement] = []
    for plan in plans:
        for requirement in plan.requirements:
            marker = f"{requirement.key}:{requirement.ordinal}"
            if marker in seen:
                continue
            seen.add(marker)
            requirements.append(requirement)
    query = " ".join(p.query for p in plans if p.query)
    return RetrievalPlan(query=query, requirements=tuple(requirements))
