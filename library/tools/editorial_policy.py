"""EDIT-025: the project's own editorial policy. EDIT-024: what kind of video it is.

The engine had no place for a project to say what kind of video it is
making, or how hard the cut should push.  Every threshold that decides an
editorial outcome lived in the module that used it, and every module
invented its own: `pacing.find_dead_sections` derived a motion threshold
from the median motion energy of the clips it was given, `retake_scan`
carried its own filler vocabulary, and `sequence_scoring` carried its own
redundancy threshold.  A project that wanted a different answer had no
declaration to make, so "make the cut tighter" could only be carried in a
brief that no measurement read.

This module is that place.  A project declares, in `project.yaml`:

    pipeline:
      editorial_policy:
        objective: interview
        slots:
          silence:
            max_gap_seconds: 0.9
            remove_fillers: true

and the behaviour modules (`library/tools/silence_reduction.py`,
`library/tools/angle_evidence.py`, `library/tools/shot_sequence_quality.py`,
`library/tools/pacing_hold.py`, `library/tools/content_significance.py`)
read the slots they own.  Nothing here has a default value, because a
default is a house style wearing a declaration's clothes (AGENTS.md 10.5).
A slot the project does not declare reads as UNDECLARED, and each
behaviour module states what it does with an undeclared slot.

The OBJECTIVE is a different thing from the slots, and the difference is
load-bearing.  A slot is a number the project chose.  An objective is the
kind of video being made, and it carries CONSTRAINTS - things that must
survive the cut because the genre requires them, not because somebody
prefers them.  A product demo whose demonstration is covered by a cutaway
is broken in a way a documentary whose chronology is shuffled is not.  The
constraints are checked by `library/tools/editorial_objective.py`; this
module owns their vocabulary and says what each one is.

**What this module does NOT do.**  It does not decide an edit.  It is a
declaration surface plus a vocabulary; the measurements live with the
measurements and the judgements live with the model.  It refuses a
malformed declaration rather than dropping the part it cannot read, and an
unknown slot name is refused rather than ignored, because a slot the
engine cannot read is a slot the editor believes is in force.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from library.tools.brand_registry import project_pipeline_block

POLICY_KEY = "editorial_policy"
"""The ``pipeline:`` key a project's editorial policy is declared under."""

FORMAT = "editorial_policy/1"


class EditorialPolicyError(ValueError):
    """A declared editorial policy is incomplete, unknown or malformed."""


# ── The slots a policy may declare ────────────────────────────────
#
# One registry, one owner.  A slot that is not in this table is refused
# at declaration time: a slot the engine cannot read is a slot the
# editor believes is in force and is not.

NUMBER = "number"
BOOLEAN = "boolean"
TEXT_LIST = "text_list"


@dataclass(frozen=True)
class Slot:
    """One value a project may declare about how its edit is cut."""

    name: str
    group: str
    what: str
    kind: str
    readers: tuple[str, ...]

    @property
    def key(self) -> str:
        return self.name


SLOTS: dict[str, Slot] = {
    slot.name: slot for slot in (
        Slot(
            name="silence.max_gap_seconds",
            group="silence",
            what="the length a measured silence must exceed to be a "
                 "reduction candidate, in seconds. A silence this long or "
                 "shorter is never a candidate. Undeclared means the "
                 "material's own measured distribution is the threshold.",
            kind=NUMBER,
            readers=("library.tools.silence_reduction",),
        ),
        Slot(
            name="silence.remove_fillers",
            group="silence",
            what="whether a measured filler may be removed. Undeclared "
                 "means fillers are reported and none is proposed for "
                 "removal.",
            kind=BOOLEAN,
            readers=("library.tools.silence_reduction",),
        ),
        Slot(
            name="redundancy.max_similarity",
            group="redundancy",
            what="the content-word similarity above which two passages "
                 "count as the same statement. Undeclared means the "
                 "material's own measured distribution is the threshold.",
            kind=NUMBER,
            readers=("library.tools.sequence_scoring",),
        ),
        Slot(
            name="pacing.target_asl_seconds",
            group="pacing",
            what="the average shot length the cut aims for, in seconds. "
                 "Undeclared means the material's own measured "
                 "distribution is the reference.",
            kind=NUMBER,
            readers=("library.tools.pacing_hold",),
        ),
        Slot(
            name="pacing.hold_minimum_seconds",
            group="pacing",
            what="the shortest hold a deliberately slowed moment is given, "
                 "in seconds. Undeclared means a hold is proposed at the "
                 "length the moment already occupies.",
            kind=NUMBER,
            readers=("library.tools.pacing_hold",),
        ),
        Slot(
            name="shot.max_same_angle_run",
            group="shot",
            what="the longest run of one camera angle the cut may carry. "
                 "Undeclared means the material's own measured runs are "
                 "the reference.",
            kind=NUMBER,
            readers=("library.tools.shot_sequence_quality",),
        ),
        Slot(
            name="angle.order",
            group="angle",
            what="the order the four angle criteria are weighed in. "
                 "Undeclared means the order the criteria are named in.",
            kind=TEXT_LIST,
            readers=("library.tools.angle_evidence",),
        ),
        Slot(
            name="content.required_claims",
            group="content",
            what="the claim ids a reduction may not drop. Undeclared means "
                 "no claim is protected, and the preservation check "
                 "reports what it found rather than failing.",
            kind=TEXT_LIST,
            readers=("library.tools.content_significance",),
        ),
    )
}

GROUPS: tuple[str, ...] = tuple(sorted({slot.group for slot in SLOTS.values()}))


# ── The objectives ────────────────────────────────────────────────
#
# An objective is a genre, and a genre carries constraints.  A
# constraint here is not a preference and not a floor: it says a thing
# that must be TRUE of the cut for the video to be the kind of video the
# project declared.  There is no count in any of them, so none of them
# is a creative floor (library/tools/creative_floors.py).

@dataclass(frozen=True)
class Objective:
    """One kind of video, and the constraints that kind carries."""

    name: str
    what: str
    constraints: tuple[str, ...]
    basis: str


OBJECTIVES: dict[str, Objective] = {
    objective.name: objective for objective in (
        Objective(
            name="interview",
            what="a conversation between people, edited for the exchange.",
            constraints=("speaker_coverage", "question_answer_kept_together"),
            basis="An interview cut that drops one participant's point is "
                  "not a shorter interview, it is a different video; and a "
                  "question separated from its answer by a cut to another "
                  "speaker reads as two unrelated remarks.",
        ),
        Objective(
            name="podcast",
            what="a long-form conversation, cut for listening.",
            constraints=("speaker_coverage",),
            basis="A podcast cut that leaves one of the two voices out "
                  "loses the exchange the format exists for.",
        ),
        Objective(
            name="social_reel",
            what="a short piece made to be watched to the end.",
            constraints=("hook_present",),
            basis="A reel that opens without a hook has no reason to be "
                  "watched past the first second.",
        ),
        Objective(
            name="product_demo",
            what="a product being demonstrated.",
            constraints=("demonstration_visible", "cutaway_over_demonstration"),
            basis="A demonstration hidden under a cutaway is not "
                  "demonstrated; the cutaway is decoration and the "
                  "demonstration is the content.",
        ),
        Objective(
            name="tutorial",
            what="an ordered set of steps the viewer is meant to follow.",
            constraints=("step_order",),
            basis="A tutorial whose steps are reordered cannot be followed.",
        ),
        Objective(
            name="documentary",
            what="a piece where what happened, and in what order, is the "
                 "content.",
            constraints=("chronology_preserved",),
            basis="A documentary that reorders events stops being a record "
                  "of them.",
        ),
        Objective(
            name="promotional",
            what="a piece making a claim about something.",
            constraints=("claim_present",),
            basis="A promotional cut that drops the claim is an "
                  "advertisement with nothing advertised.",
        ),
    )
}

OBJECTIVE_NAMES: tuple[str, ...] = tuple(sorted(OBJECTIVES))


# ── The policy ────────────────────────────────────────────────────

@dataclass(frozen=True)
class EditorialPolicy:
    """What a project declared about the edit it wants made."""

    id: str
    version: int
    project_id: str
    objective: str
    slots: Mapping[str, Any] = field(default_factory=dict)
    origin: str = "project.yaml"

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise EditorialPolicyError("an editorial policy needs an id")
        if (not isinstance(self.version, int) or isinstance(self.version, bool)
                or self.version < 1):
            raise EditorialPolicyError(
                "an editorial policy needs a positive integer version")
        if not isinstance(self.project_id, str) or not self.project_id:
            raise EditorialPolicyError(
                "an editorial policy needs the project it belongs to")
        if self.objective not in OBJECTIVES:
            raise EditorialPolicyError(
                f"unknown editorial objective {self.objective!r}; "
                f"known objectives are {list(OBJECTIVE_NAMES)}")
        unknown = sorted(set(self.slots) - set(SLOTS))
        if unknown:
            raise EditorialPolicyError(
                f"unknown editorial policy slot(s) {unknown}; a slot the "
                f"engine cannot read is a slot the editor believes is in "
                f"force and is not")

    @property
    def ref(self) -> str:
        return f"{self.id}@v{self.version}"

    @property
    def objective_spec(self) -> Objective:
        return OBJECTIVES[self.objective]

    @property
    def constraints(self) -> tuple[str, ...]:
        """The constraints this objective carries, in declaration order."""
        return self.objective_spec.constraints

    def slot(self, name: str) -> Any:
        """A declared slot's value, or None when the project declared none."""
        if name not in SLOTS:
            raise EditorialPolicyError(f"unknown editorial policy slot {name!r}")
        value = self.slots.get(name)
        return None if value is None else value

    def declared(self, name: str) -> bool:
        return name in self.slots

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": FORMAT,
            "id": self.id,
            "version": self.version,
            "project_id": self.project_id,
            "objective": self.objective,
            "slots": dict(self.slots),
            "origin": self.origin,
        }


def _check_number(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EditorialPolicyError(
            f"editorial policy slot {name!r} takes a number, got {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise EditorialPolicyError(
            f"editorial policy slot {name!r} takes a finite number")
    return number


def _check_boolean(name: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise EditorialPolicyError(
            f"editorial policy slot {name!r} takes true or false, got {value!r}")
    return value


def _check_text_list(name: str, value: Any) -> list[str]:
    if not isinstance(value, list):
        raise EditorialPolicyError(
            f"editorial policy slot {name!r} takes a list, got {value!r}")
    rows = []
    for row in value:
        if not isinstance(row, str) or not row.strip():
            raise EditorialPolicyError(
                f"editorial policy slot {name!r} takes non-empty strings")
        rows.append(row.strip())
    return rows


def _coerce_slot(slot: Slot, value: Any) -> Any:
    if slot.kind == NUMBER:
        return _check_number(slot.name, value)
    if slot.kind == BOOLEAN:
        return _check_boolean(slot.name, value)
    if slot.kind == TEXT_LIST:
        return _check_text_list(slot.name, value)
    raise EditorialPolicyError(
        f"editorial policy slot {slot.name!r} has an unknown kind")


def flatten_slots(declared: Any) -> dict[str, Any]:
    """A declared ``slots:`` mapping, flattened to ``group.key`` names.

    The declaration is nested because a project file reads better nested;
    the registry is flat because that is how a behaviour module names the
    slot it owns.  An unknown group or an unknown slot name is refused
    here rather than ignored.
    """
    if declared is None:
        return {}
    if not isinstance(declared, Mapping):
        raise EditorialPolicyError(
            "editorial policy 'slots' must be a mapping of groups")
    flat: dict[str, Any] = {}
    for group, values in declared.items():
        if group not in GROUPS:
            raise EditorialPolicyError(
                f"unknown editorial policy group {group!r}; known groups "
                f"are {list(GROUPS)}")
        if not isinstance(values, Mapping):
            raise EditorialPolicyError(
                f"editorial policy group {group!r} must be a mapping")
        for key, value in values.items():
            name = f"{group}.{key}"
            slot = SLOTS.get(name)
            if slot is None:
                raise EditorialPolicyError(
                    f"unknown editorial policy slot {name!r}")
            if value is None:
                continue
            flat[name] = _coerce_slot(slot, value)
    return flat


def parse_policy(declared: Any, *, project_id: str) -> EditorialPolicy:
    """Build a policy from a project's ``editorial_policy:`` declaration.

    The declaration is refused rather than partly read: a policy that
    names an unknown objective or an unreadable slot is a declaration the
    editor believes is in force and is not.
    """
    if not isinstance(declared, Mapping):
        raise EditorialPolicyError(
            "an editorial policy must be a mapping, got "
            f"{type(declared).__name__}")
    objective = declared.get("objective")
    if not isinstance(objective, str) or objective not in OBJECTIVES:
        raise EditorialPolicyError(
            f"editorial policy names objective {objective!r}; known "
            f"objectives are {list(OBJECTIVE_NAMES)}")
    policy_id = declared.get("id") or f"{project_id}-editorial"
    if not isinstance(policy_id, str) or not policy_id.strip():
        raise EditorialPolicyError("an editorial policy id must be a string")
    version = declared.get("version", 1)
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise EditorialPolicyError(
            "an editorial policy version must be a positive integer")
    return EditorialPolicy(
        id=policy_id.strip(),
        version=version,
        project_id=project_id,
        objective=objective,
        slots=flatten_slots(declared.get("slots")),
        origin="project.yaml",
    )


def read_project_policy(project_folder: str | None,
                        *, project_id: str = "") -> EditorialPolicy | None:
    """The policy a project DECLARES, or None when it declares none.

    None is a reading, not an absence of a policy: it means the project
    said nothing about how its edit should be cut, so every behaviour
    module falls back to the material's own measured distribution and
    reports what it found.
    """
    block = project_pipeline_block(project_folder)
    declared = block.get(POLICY_KEY)
    if declared is None:
        return None
    return parse_policy(declared, project_id=project_id or _project_id(project_folder))


def _project_id(project_folder: str | None) -> str:
    if not project_folder:
        return ""
    return str(project_folder.rstrip("/").rsplit("/", 1)[-1])


def policy_from_dict(value: Mapping[str, Any]) -> EditorialPolicy:
    """Rebuild a policy from its own ``to_dict`` shape."""
    if value.get("format") != FORMAT:
        raise EditorialPolicyError("unsupported editorial policy format")
    return EditorialPolicy(
        id=value.get("id"),
        version=value.get("version"),
        project_id=value.get("project_id"),
        objective=value.get("objective"),
        slots=dict(value.get("slots") or {}),
        origin=value.get("origin") or "project.yaml",
    )


def describe_policy(policy: EditorialPolicy | None) -> str:
    """One paragraph saying what the project declared, and what it did not."""
    if policy is None:
        return ("This project declares no editorial policy. Every editing "
                "behaviour falls back to the material's own measured "
                "distribution, and a threshold nobody chose is never "
                "invented.")
    objective = policy.objective_spec
    declared = sorted(policy.slots)
    lines = [
        f"Editorial policy {policy.ref}, objective {policy.objective}: "
        f"{objective.what}",
        f"  {objective.basis}",
        "  Constraints this objective carries: "
        + (", ".join(policy.constraints) or "none"),
        "  Declared slots: " + (", ".join(declared) or "none"),
    ]
    undeclared = sorted(set(SLOTS) - set(declared))
    if undeclared:
        lines.append("  Undeclared slots (read as UNDECLARED, never filled "
                     "in): " + ", ".join(undeclared))
    return "\n".join(lines)
