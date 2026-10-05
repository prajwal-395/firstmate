"""Attaching a creative brief is a choice a project makes, and declining is said out loud.

**What this changes.**  Until now, a project that named a
`creative_brief` path had it injected into eight prompts on every run,
automatically, with no way to say "not this time"; and a project that
named none got silence - eight steps planned a video with no brief and
nothing anywhere recorded that they had been asked to.

The captain, 2026-09-02: *"this should be like an optional attachment we
can add as context if we want, not something that automatically goes in.
if this was like a TUI interface if a user declines to attach a creative
brief, then it should prompt the LLM to ask some briefing questions for
the user"*, and *"it should be optionally configurable by the user
whether we are even adding a creative brief or not"*.

So two things, and they are separate:

1. **This module** decides whether a brief is attached, and makes every
   answer an explicit reading rather than a silence.
2. **`library/tools/briefing_interview.py`** is what happens when it is
   not: the steps that would have read one are asked what they would
   need to know, instead of proceeding brief-less and saying nothing.

## Three readings, and the key is a THREE-state declaration

``ATTACHED``
    The brief goes into the prompts, exactly as before.

``DECLINED``
    ``pipeline.attach_creative_brief: false``.  One line, and it wins
    even when a path is declared - a project may keep its brief on file
    and choose not to send it.  Declining must never be harder than
    attaching, so this is one boolean and nothing else.

``NONE_DECLARED``
    No path at all.  Also not attached, and also interviewed - a project
    that never wrote a brief has exactly the same need as one that
    declined to send the brief it has.

**An absent `attach_creative_brief` key is not read as `false`.**  It is
read as "the PATH is the declaration", and the run SAYS it read it that
way.  Reading the absent key as a decline would make every project that
already declares a brief lose it on its next run, silently - which is
the defect this whole change exists to remove, arriving from the other
direction.  Writing the key is how a project overrides that reading in
either direction.

**The one refusal.**  `attach_creative_brief: true` with no path is
refused by name.  A declaration to attach a document that does not exist
is not a decision anybody can act on, and the alternative - attaching
nothing and carrying on - is the silence again.

`tests/unit/context/test_brief.py`.


Rules relocated from AGENTS.md 3
--------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 3 keeps the headline
and points here.

One enumeration, `library/tools/brief_attachment.py` for the choice and `library/tools/briefing_interview.py` for what happens when it goes the other way. The brief used to be injected automatically from the project's `creative_brief` path, and a project that declared none got SILENCE - eight steps planning a video with no brief and nothing recording that they had been asked to.

Captain's ruling, 2026-09-02: *"this should be like an optional attachment we can add as context if we want, not something that automatically goes in"*, and *"if this was like a TUI interface if a user declines to attach a creative brief, then it should prompt the LLM to ask some briefing questions for the user"*.
- **THREE readings, and an absent key is not `false`.** `pipeline.attach_creative_brief` (top level or under `pipeline:`, the same two places the path is read from) declares it. `true` attaches, `false` DECLINES even when a path exists, and ABSENT means **the PATH is the declaration** - so a project already declaring a brief keeps it, and the run header SAYS it read the absence that way. Reading an absent key as a decline is the same silence arriving from the other direction.
- **One refusal, by name**: `attach_creative_brief: true` with no path. A declaration to attach a document that does not exist cannot be acted on, and attaching nothing and carrying on is the silence again.
- **The path reaches state only when the reading is ATTACHED**, so `gather_step_inputs`, the whitelist and the replay bench all see exactly what a project with no brief sees. No second place can answer differently.
- **A run with no brief attached INTERVIEWS.** The steps whose manifests declare `creative_brief` are asked for `briefing_questions` - what they would have put to the person commissioning the video. **DERIVED from the manifests**, so a step that starts or stops declaring the brief cannot fall out of the interview silently; `mesh_spine` is in it though its handoff never names a brief (§10.1).
- **It is `undetermined.py`'s third sibling** - same `take`/`record`/`summary_lines` surface, same collector, same route into the prompt as DATA, same THREE readings (`asked` / `nothing_to_ask` / `not_declared`), one record per model ATTEMPT numbered, and `state["briefing_questions"]` MERGED rather than replaced. Do not build a fourth shape. **It differs in one way: it is CONDITIONAL** - a step handed the captain's own brief and then asked what it wished the captain had said is being invited to manufacture a gap.
- **Who answers, and on which run: the captain, out of band.** The pipeline is not interactive and a run that stopped to wait would never complete, so the interview is COLLECTED, not conducted. The questions print in the run summary and land on state; the captain answers by writing or extending the brief and attaching it, and the next run reads it. **The brief IS the answer format** - an answers file beside it would be a brief under another name.
- **The prompt tells the step to decide anyway, in full.** Asking is not licence to hedge.
- `tests/unit/context/test_brief.py`, `tests/unit/context/test_brief.py`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

#: The project.yaml key. Readable at the top level or under `pipeline:`,
#: the same two places `creative_brief` itself is read from.
DECLARATION_KEY = "attach_creative_brief"

#: Where the path is declared. One spelling, here.
PATH_KEY = "creative_brief"

ATTACHED = "attached"
DECLINED = "declined"
NONE_DECLARED = "none_declared"

READINGS = (ATTACHED, DECLINED, NONE_DECLARED)


class BriefAttachmentError(ValueError):
    """A declaration this module refuses, by name."""


@dataclass(frozen=True)
class Attachment:
    """What this project decided about its brief, and how that was read."""

    reading: str
    path: str = ""
    #: How the reading was arrived at, in words. Carried onto the run
    #: rather than recomputed, so nothing downstream has to re-derive it
    #: and reach a different answer.
    basis: str = ""

    @property
    def attached(self) -> bool:
        return self.reading == ATTACHED

    @property
    def interview(self) -> bool:
        """Whether the steps should be asked for briefing questions.

        Both not-attached readings, deliberately. A project that
        declined and a project that never wrote one are in the same
        position at the moment a step has to decide something.
        """
        return self.reading in (DECLINED, NONE_DECLARED)


def _declared(data: dict, key: str):
    """A key read from the top level or from under `pipeline:`.

    Both places, because that is where `creative_brief` itself is read
    from and a project should not have to remember that one of its two
    brief keys lives somewhere else.
    """
    if key in data:
        return data[key]
    pipeline = data.get("pipeline")
    if isinstance(pipeline, dict) and key in pipeline:
        return pipeline[key]
    return None


def _as_bool(value, where: str) -> Optional[bool]:
    """YAML's several spellings of a boolean, or a refusal.

    Unlike `generate_motion_props._as_bool` there is NO default here: an
    unreadable value is refused rather than silently taking a side, because
    the side it would take is the difference between a brief reaching
    eight prompts and not.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "yes", "on", "1"):
            return True
        if text in ("false", "no", "off", "0"):
            return False
    raise BriefAttachmentError(
        f"{where}: {DECLARATION_KEY} must be true or false, got {value!r}. "
        f"An unreadable value is refused rather than guessed at - it is "
        f"the difference between the brief reaching every planning step "
        f"and reaching none of them."
    )


def read_declaration(project_folder: str) -> Attachment:
    """What this project declared about attaching a brief.

    Reads `project.yaml` directly, the way `load_pipeline_state` reads
    the path itself: the runtime brief path does not go through
    `library/schemas/project_config.py` and adding a second route would
    be two answers to one question.
    """
    if not project_folder:
        return Attachment(NONE_DECLARED, basis="no project folder was given")
    project_yaml = os.path.join(project_folder, "project.yaml")
    if not os.path.exists(project_yaml):
        return Attachment(NONE_DECLARED,
                          basis="the project has no project.yaml")
    try:
        import yaml
        with open(project_yaml, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except BriefAttachmentError:
        raise
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        raise BriefAttachmentError(
            f"{project_yaml} could not be read for {DECLARATION_KEY}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        return Attachment(NONE_DECLARED,
                          basis="project.yaml is not a mapping")
    attachment = read_declaration_from(data, where=project_yaml)
    if attachment.path:
        # A portable declaration (`$HOME/...`, `$PROJECT/...`) resolves
        # against this project on this machine; relative stays relative
        # for the consumer to anchor at the project folder.
        from library.tools.portable_paths import expand
        expanded = expand(attachment.path, project_folder)
        if expanded != attachment.path:
            attachment = Attachment(attachment.reading, path=expanded,
                                    basis=attachment.basis)
    return attachment


def read_declaration_from(data: dict, where: str = "project.yaml"
                          ) -> Attachment:
    """The reading, off an already-loaded project.yaml mapping."""
    path = _declared(data, PATH_KEY)
    path = str(path).strip() if isinstance(path, (str, os.PathLike)) else ""
    declared = _as_bool(_declared(data, DECLARATION_KEY), where)

    if declared is False:
        return Attachment(
            DECLINED, path=path,
            basis=(f"{DECLARATION_KEY} is false"
                   + (" - the declared brief is on file and deliberately "
                      "not sent" if path else "")))
    if declared is True:
        if not path:
            raise BriefAttachmentError(
                f"{where}: {DECLARATION_KEY} is true and no {PATH_KEY} is "
                f"declared. A declaration to attach a document that does "
                f"not exist cannot be acted on; declare the path, or set "
                f"{DECLARATION_KEY} to false."
            )
        return Attachment(ATTACHED, path=path,
                          basis=f"{DECLARATION_KEY} is true")
    if path:
        return Attachment(
            ATTACHED, path=path,
            basis=(f"{DECLARATION_KEY} is not declared, so the declared "
                   f"{PATH_KEY} path is read as the choice to attach it"))
    return Attachment(
        NONE_DECLARED,
        basis=f"the project declares neither {PATH_KEY} nor {DECLARATION_KEY}")


def describe(attachment: Attachment) -> str:
    """One line for the run header, printed whichever way it read.

    The shape `brand_registry.describe_brand_absence` established: an
    absence that is stated once per run is a decision a reader can see,
    and an absence that is not stated is the silence.
    """
    if attachment.reading == ATTACHED:
        return (f"Creative brief: ATTACHED from {attachment.path!r} "
                f"({attachment.basis}).")
    if attachment.reading == DECLINED:
        return ("Creative brief: DECLINED by the project "
                f"({attachment.basis}). The planning steps will be asked "
                "what they would need to know instead - see the briefing "
                "questions in the run summary.")
    return ("Creative brief: NONE DECLARED "
            f"({attachment.basis}). The planning steps will be asked what "
            "they would need to know instead - see the briefing questions "
            "in the run summary.")
