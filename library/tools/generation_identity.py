"""THE DURABLE IDENTITY OF A GENERATION (VC-001).

The captain's punch list, 2026-10-20, VC-001: *"Establish durable
version identities."* Its four rules, kept whole:

- *Identify a generation by immutable version metadata and Resolve
  timeline identity.*
- *Treat a human-readable timeline name as an assertion, not the sole
  identity.*
- *Preserve parent generation, author/originator, accepted state, and
  asset dependencies.*
- *Reject ambiguous or stale generation targets.*

WHAT WAS MISSING
----------------
The engine already had three records of a generation, and none of them
was its identity:

- the timeline shadow keyed a generation by ``(project, timeline_id,
  generation number)`` - a durable key, but a bare pair of integers and
  a machine-local store;
- the undo journal recorded WHAT REN DID (a promotion names the
  candidate that became the reel's timeline);
- the reel version ledger recorded the reel's states under the reel's
  **NAME**, with no Resolve identity at all.

So the name was still the sole identity in the one ledger a person
reads, and every path that resolved a reel by name could be blocked by
a name that two live timelines share. Measured 2026-10-21 on
``Ren Scratch - vep-bench-demo2``: three Resolve timelines named
``Reel 13 - the-accounting-firm-ai-called-healthcare``, and every
write that addressed that name was refused before its first write
("recorded base for ... resolved to 3 timelines").

WHAT THIS IS
------------
One identity for one generation, minted from what cannot change:

    generation_id = sha256(resolve_project, resolve_timeline_id,
                           content_digest)

- ``resolve_project`` + ``resolve_timeline_id`` are the Resolve
  timeline identity (``GetUniqueId``), so a RENAME keeps the identity
  and two timelines that share a name do not collide;
- ``content_digest`` is the immutable version metadata (the recorded
  rows' digest), so the identity names a STATE and not a moment.

The name is recorded beside the identity as an ASSERTION: what the
generation was called when it was recorded. It is never part of the
identity and never the only way to reach it.

The identity travels on the version entry itself
(``library/tools/versions/reel_versions.py``), so it is versioned with
the project and survives a cleanup, a rename and a rebuild.

WHAT IT REFUSES
----------------
- ``AmbiguousGeneration`` - a target that names several generations
  (a reel name whose history spans more than one Resolve timeline).
  The refusal names each candidate by its durable id.
- ``StaleGeneration`` - a target that names a generation which is no
  longer the current state of its timeline.
- ``UnknownGeneration`` - a target that names nothing at all.

A refusal is not a failure: nothing was written, and the next step is
named. Every refusal here is a ``RenRefusal`` and carries what, why
and fix.

WHERE IT IS USED
----------------
``resolve_timeline_id`` is the ONE way a reel's current timeline is
named, and it answers in this order:

1. the identity the version ledger recorded for the reel's current
   generation (the durable identity);
2. the journal - what Ren itself last made current (PR 3131);
3. the shadow - the recorded generations, by exact name, which refuses
   when two of them share it.

The old behaviour - resolving by name alone - is what let a name
collision block a write. Every path that used to do that now asks
here: ``edit_spec._current_reel_timeline_id``,
``revise._recorded_reel``, ``edit_preparation._read_base_snapshot`` and
``resolve_axi_core._prepare_request_plan``.

``tests/unit/reels/test_generation_identity.py``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from library.tools.ren_refusal import RenRefusal

GENERATION_ID_PREFIX = "gen-"
"""The prefix every durable generation identity carries.

A generation id is recognisable by its shape, so a target that names
one is never mistaken for a name.
"""

ID_HEX_LENGTH = 24
"""The hex characters of the digest the identity is cut from.

Twenty-four hex characters is 96 bits: a collision between two
generations of one project is not a thing that happens.
"""


def generation_id(*, resolve_project: str, resolve_timeline_id: str,
                  content_digest: str) -> str:
    """The durable id of one generation.

    The id covers exactly the three things that cannot change: the
    Resolve project, the Resolve timeline's unique id and the
    generation's content digest. It does NOT cover the name, so a
    rename keeps the identity; it does NOT cover a timestamp, so
    recording the same generation twice yields the same id.

    The content digest is what makes the id a STATE rather than a
    moment: the same state recorded twice is the same generation.
    """
    payload = json.dumps(
        {
            "resolve_project": str(resolve_project or ""),
            "resolve_timeline_id": str(resolve_timeline_id or ""),
            "content_digest": str(content_digest or ""),
        },
        sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{GENERATION_ID_PREFIX}{digest[:ID_HEX_LENGTH]}"


@dataclass(frozen=True)
class GenerationIdentity:
    """One generation's durable identity, and the facts it preserves.

    ``generation_id`` is the identity. Everything else is what the
    identity PRESERVES (VC-001): the Resolve timeline identity, the
    name as an assertion, the parent generation, who originated it,
    whether it was accepted, and the assets it depends on.
    """

    generation_id: str
    resolve_project: str = ""
    resolve_timeline_id: str = ""
    timeline_name: str = ""
    content_digest: str = ""
    parent_generation_id: str = ""
    author: str = ""
    accepted: bool = False
    assets: tuple = ()
    recorded_at: str = ""
    revision_id: str = ""

    def as_dict(self) -> dict:
        return {
            "generation_id": self.generation_id,
            "resolve_project": self.resolve_project,
            "resolve_timeline_id": self.resolve_timeline_id,
            "timeline_name": self.timeline_name,
            "content_digest": self.content_digest,
            "parent_generation_id": self.parent_generation_id,
            "author": self.author,
            "accepted": bool(self.accepted),
            "assets": [dict(asset) for asset in self.assets],
            "recorded_at": self.recorded_at,
            "revision_id": self.revision_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping | None) -> GenerationIdentity | None:
        """The identity a record carries, or None when it carries none.

        A record written before identities existed carries none, and
        that is ABSENT evidence, never an invented one.
        """
        if not isinstance(data, Mapping):
            return None
        generation = str(data.get("generation_id") or "")
        if not generation:
            return None
        assets = tuple(dict(asset) for asset in (data.get("assets") or ())
                       if isinstance(asset, Mapping))
        return cls(
            generation_id=generation,
            resolve_project=str(data.get("resolve_project") or ""),
            resolve_timeline_id=str(data.get("resolve_timeline_id") or ""),
            timeline_name=str(data.get("timeline_name") or ""),
            content_digest=str(data.get("content_digest") or ""),
            parent_generation_id=str(data.get("parent_generation_id") or ""),
            author=str(data.get("author") or ""),
            accepted=bool(data.get("accepted")),
            assets=assets,
            recorded_at=str(data.get("recorded_at") or ""),
            revision_id=str(data.get("revision_id") or ""),
        )

    def matches(self, other: GenerationIdentity | None) -> bool:
        """Whether two identities name the SAME generation."""
        if other is None:
            return False
        return self.generation_id == other.generation_id


class GenerationRefused(RenRefusal):
    """A generation target cannot be resolved, and nothing was written."""

    CODE = "REN_GENERATION_REFUSED"

    USER_MESSAGE = (
        "I could not tell which generation you mean, so nothing was "
        "changed. Name the generation by its id, or the timeline by its "
        "Resolve identity.")

    RECOVERY_CLASS = "needs_question"

    ASK_REASON = "ambiguous_intent"
    """Why a person is needed: the target means more than one thing."""

    BLOCKER = "ambiguous_intent"

    NEXT_ACTION = "state_the_intent"


class AmbiguousGeneration(GenerationRefused):
    """A target names more than one generation, and the engine will not pick.

    A reel whose history spans two Resolve timelines (a rebuild under a
    new timeline, a renamed reel) has more than one generation the
    NAME could mean. The refusal names each candidate by its durable
    id, so the caller can name one.
    """

    CODE = "REN_AMBIGUOUS_GENERATION"


class StaleGeneration(GenerationRefused):
    """A target names a generation that is no longer the current state.

    A write planned against a generation the timeline has moved past
    would change the wrong things, so it is refused before the first
    write. The refusal names the generation that IS current.
    """

    CODE = "REN_STALE_GENERATION"

    BLOCKER = "unsafe_state"

    NEXT_ACTION = "settle_the_state_first"


class UnknownGeneration(GenerationRefused):
    """A target names a generation that is not recorded at all."""

    CODE = "REN_UNKNOWN_GENERATION"

    BLOCKER = "insufficient_evidence"

    NEXT_ACTION = "collect_the_evidence"


# ── Minting ─────────────────────────────────────────────────────────

def assets_of_rows(rows: Mapping | None) -> tuple:
    """The media a recorded set of rows depends on.

    The rows a version carries name their media under ``source_file``
    when the reader recorded it. A row set that names no media has no
    asset dependency to record - absent, never invented.
    """
    from library.tools.version_dependencies import MEDIA_PATH_KEYS

    found: list[str] = []
    for row in (rows or {}).values():
        items = row.get("items") if isinstance(row, Mapping) else None
        for item in (items or ()):
            if not isinstance(item, Mapping):
                continue
            for key in MEDIA_PATH_KEYS:
                path = item.get(key)
                if path and str(path) not in found:
                    found.append(str(path))
    return tuple({"path": path} for path in found)


def accepted_generation(project_folder, resolve_timeline_id: str,
                        content_digest: str = "") -> bool:
    """Whether this generation is the one the captain ACCEPTED.

    The acceptance record names the Resolve timeline identity and the
    rows digest of what was delivered (`library/tools/reel_acceptance`).
    A generation is accepted when the acceptance names its timeline and
    - when the acceptance carries a digest - that digest too.
    """
    from library.tools import reel_acceptance

    if not resolve_timeline_id:
        return False
    try:
        acceptances = reel_acceptance.acceptances(project_folder)
    except Exception:  # noqa: BLE001 - an unread record accepts nothing
        return False
    for entry in acceptances.values():
        if str(entry.get("timeline_id") or "") != str(resolve_timeline_id):
            continue
        digest = str(entry.get("rows_digest") or "")
        if digest and content_digest and digest != content_digest:
            continue
        return True
    return False


def mint(*, project_folder, resolve_project: str, resolve_timeline_id: str,
         timeline_name: str, content_digest: str, parent_generation_id: str = "",
         author: str = "", assets: Sequence = (), recorded_at: str = "",
         revision_id: str = "") -> GenerationIdentity:
    """The identity of one generation, with everything it preserves.

    ``accepted`` is read from the project's own acceptance record
    rather than asked of the caller: an acceptance is a fact about what
    was delivered, and the record that holds it already knows.
    """
    assets = tuple(dict(asset) for asset in (assets or ()))
    return GenerationIdentity(
        generation_id=generation_id(
            resolve_project=resolve_project,
            resolve_timeline_id=resolve_timeline_id,
            content_digest=content_digest),
        resolve_project=str(resolve_project or ""),
        resolve_timeline_id=str(resolve_timeline_id or ""),
        timeline_name=str(timeline_name or ""),
        content_digest=str(content_digest or ""),
        parent_generation_id=str(parent_generation_id or ""),
        author=str(author or ""),
        accepted=accepted_generation(project_folder, resolve_timeline_id,
                                     content_digest),
        assets=assets,
        recorded_at=str(recorded_at or ""),
        revision_id=str(revision_id or ""),
    )


# ── Reading the ledger ──────────────────────────────────────────────

def identity_of(entry: Mapping | None) -> GenerationIdentity | None:
    """The identity one version entry carries, or None."""
    if not isinstance(entry, Mapping):
        return None
    return GenerationIdentity.from_dict(entry.get("generation"))


def entries_of(project_folder, reel: str = "") -> list:
    """Every version entry of a project, oldest first.

    ``reel`` is matched on the reel's BASE name (the name without the
    build's own container suffixes), so a reel whose key was renamed
    keeps its history: the name is an assertion, and a renamed reel is
    still the same reel.
    """
    from library.tools.versions import reel_versions

    document = reel_versions.read(project_folder)
    entries: list = []
    for key, history in (document.get("reels") or {}).items():
        if reel and not _same_reel(key, reel):
            continue
        entries.extend(dict(entry) for entry in (history or ()))
    return entries


def _same_reel(left: str, right: str) -> bool:
    if str(left) == str(right):
        return True
    from library.tools.reel_signoff import base_name

    try:
        return base_name(left) == base_name(right)
    except Exception:  # noqa: BLE001 - an unparseable name is its own reel
        return False


def generations_of(project_folder, reel: str = "") -> list:
    """Every generation identity a project recorded, oldest first."""
    found = []
    for entry in entries_of(project_folder, reel):
        identity = identity_of(entry)
        if identity is not None:
            found.append(identity)
    return found


def current_generation(project_folder, reel: str) -> GenerationIdentity | None:
    """The reel's CURRENT generation: the newest one the ledger holds.

    The ledger is append-ordered, so the newest entry for the reel is
    the current state. A reel with no recorded version has no identity
    to offer, and that is absent evidence, never a guess.
    """
    found = generations_of(project_folder, reel)
    return found[-1] if found else None


def current_of_timeline(project_folder,
                        resolve_timeline_id: str) -> GenerationIdentity | None:
    """The current generation of ONE Resolve timeline."""
    found = [identity for identity in generations_of(project_folder)
             if identity.resolve_timeline_id == str(resolve_timeline_id)]
    return found[-1] if found else None


def parent_of(project_folder, reel: str, resolve_timeline_id: str,
              content_digest: str) -> str:
    """The generation this one continues, on the SAME Resolve timeline.

    A generation's parent is the previous state of ITS timeline. The
    reel's ledger may hold generations of other timelines under the
    same name - that is a collision, not a parent - so the parent is
    looked for on the timeline itself.
    """
    parent = ""
    for identity in generations_of(project_folder, reel):
        if identity.resolve_timeline_id != str(resolve_timeline_id):
            continue
        if identity.content_digest == str(content_digest):
            continue
        parent = identity.generation_id
    return parent


# ── Resolution ──────────────────────────────────────────────────────

def resolve(project_folder, target: str) -> GenerationIdentity:
    """The generation a target names, or a refusal naming why.

    A target is read in this order:

    - a generation id (it carries the prefix) names that generation
      and nothing else;
    - a Resolve timeline id names the current generation of that
      timeline;
    - anything else is a reel NAME, and a name whose history spans
      more than one Resolve timeline is ambiguous: the refusal names
      every candidate by its durable id.
    """
    text = str(target or "")
    if not text:
        raise UnknownGeneration(
            what="no generation was named.",
            why=("a target that names nothing cannot be resolved, and "
                 "guessing which generation to act on is how the wrong "
                 "timeline gets edited"),
            fix="name the generation by its id, or the reel by its name",
            blocker="insufficient_evidence")
    if text.startswith(GENERATION_ID_PREFIX):
        for identity in generations_of(project_folder):
            if identity.generation_id == text:
                return identity
        raise UnknownGeneration(
            what=f"generation {text!r} is not recorded in this project.",
            why=("a generation id that names nothing recorded cannot be "
                 "acted on: the identity is the only thing that says "
                 "which state was meant"),
            fix=("list the project's recorded generations with "
                 "`ren undo <project> --list` and name one of their ids"),
            blocker="insufficient_evidence")
    for identity in generations_of(project_folder):
        if identity.resolve_timeline_id and \
                identity.resolve_timeline_id == text:
            return identity
    named = generations_of(project_folder, text)
    if not named:
        raise UnknownGeneration(
            what=f"{text!r} names no recorded generation.",
            why=("the target does not match a recorded generation, and a "
                 "generation that was never recorded cannot be acted on"),
            fix=("name the reel as it is recorded, or its generation id; "
                 "observe the timeline first if it was never read"),
            blocker="insufficient_evidence")
    timelines = {identity.resolve_timeline_id for identity in named
                 if identity.resolve_timeline_id}
    if len(timelines) > 1:
        candidates = ", ".join(
            f"{identity.generation_id} ({identity.resolve_timeline_id})"
            for identity in named)
        raise AmbiguousGeneration(
            what=(f"{text!r} names {len(timelines)} different Resolve "
                  f"timelines: {candidates}."),
            why=("the name is shared by more than one generation, and "
                 "picking one of them would edit a timeline the caller "
                 "did not mean"),
            fix=("name the generation by its id, or the timeline by its "
                 "Resolve identity"),
            blocker="ambiguous_intent")
    return named[-1]


def require_current(project_folder, identity: GenerationIdentity
                    ) -> GenerationIdentity:
    """The generation, refused when it is no longer the current state.

    A generation that a later generation of the SAME timeline has
    replaced is stale: a write planned against it would change the
    wrong things. The refusal names the generation that IS current.
    """
    if identity is None:
        raise UnknownGeneration(
            what="no generation was named.",
            why="a target that names nothing cannot be checked for staleness",
            fix="name the generation by its id",
            blocker="insufficient_evidence")
    current = current_of_timeline(project_folder,
                                  identity.resolve_timeline_id)
    if current is None:
        return identity
    if current.generation_id != identity.generation_id:
        raise StaleGeneration(
            what=(f"generation {identity.generation_id!r} is no longer "
                  f"the current state of its timeline; the current "
                  f"generation is {current.generation_id!r}."),
            why=("the timeline moved on after that generation was "
                 "recorded, so a write planned against it would change "
                 "what the caller did not name"),
            fix=("re-read the reel and plan against its current "
                 "generation"),
            blocker="unsafe_state")
    return identity


def resolve_timeline_id(project_folder, project_name: str, reel: str,
                        timeline_id: str = "") -> str:
    """The Resolve timeline id of the reel's CURRENT generation.

    THE ONE WAY A REEL IS NAMED. A name is an assertion; this answers
    with the identity the records hold, in this order:

    1. the identity the version ledger recorded for the reel's current
       generation (the durable identity);
    2. the journal - what Ren itself last made current (PR 3131);
    3. the shadow - the recorded generations, by exact name, which
       refuses when two of them share it.

    The old behaviour resolved by the NAME alone, which is how a name
    shared by two live timelines blocked a write before its first
    write.
    """
    if timeline_id:
        return str(timeline_id)
    identity = current_generation(project_folder, reel)
    if identity is not None and identity.resolve_timeline_id:
        return identity.resolve_timeline_id
    from library.tools import undo_journal

    promoted = undo_journal.last_promoted_timeline(project_folder, reel)
    if promoted:
        return promoted
    from library.tools import timeline_shadow

    # The shadow's own refusal stands: two recorded timelines sharing a
    # name is two LIVE timelines, and the engine will not pick one of
    # them for a write (AGENTS.md 5). Its message names the candidates.
    return timeline_shadow.ShadowStore().resolve_timeline(
        project_name, reel)
