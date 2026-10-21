"""PROD-003: one taxonomy of what BLOCKS a refusal, and the next action.

A refusal used to say what happened and what to do about it, and the
KIND of block was left to the reader.  The punch list asks for the kind
to be stated: "Distinguish missing media, unsupported operation, unsafe
state, ambiguous intent, insufficient evidence, and resource
contention."  Those six are the BLOCKERS, and this module is their one
enumeration.

Three vocabularies already answered three different questions about a
failure, and nothing joined them (`recovery_instruction` names them):
`ren_refusal.RECOVERY_CLASS` says whether the host should ACT, ASK or
WAIT; `transaction_orchestration.ExecutionRefusal.CATEGORIES` says what
KIND of failure the execution was; and this module says what BLOCKED
the request.  A blocker is not a failure kind: `stale` is a failure
kind, and the blocker behind it is `unsafe_state` - the state on the
timeline is not safe to change yet.

`FAMILY_BLOCKERS` is the INVENTORY: every refusal family in the engine
and the blocker that family always reports.  It is keyed by the
qualified class name (`module.Class`) because two families share a class
name (`AnchorRefused` twice, `DeliverRefused` twice, `PatchRefused`
twice) and they do NOT block for the same reason.  The key is resolved
along the class's MRO, so a subclass inherits its parent's blocker and
a family that declares its own overrides it.

The table is the GUARD's other half: `ren_refusal.RenRefusal` refuses to
be constructed without a blocker, so a refusal that nobody classified
fails loudly at the raise site instead of reaching a caller as prose
that says nothing about what blocked it.

`NEXT_ACTIONS` is the vocabulary of the next action a caller can
actually take, and `BLOCKER_NEXT_ACTION` says which one each blocker
demands.  A refusal that names a next action the caller cannot perform
is the defect this module exists beside: `assert_supplyable` checks a
refusal's fix against the vocabulary of what the caller can supply, and
raises when the fix names something outside it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable


BLOCKERS = (
    "missing_media",
    "unsupported_operation",
    "unsafe_state",
    "ambiguous_intent",
    "insufficient_evidence",
    "resource_contention",
)
"""The six things that block a request, and the only six.

`missing_media`
    the media (or the rendered file, the index, the sign-off) the
    request needs is not there.
`unsupported_operation`
    the engine has no proven route for what was asked - no executor, no
    capability, no template, no command that does it.
`unsafe_state`
    the state is not safe to act on yet: it is stale, it conflicts with
    another writer, it is unapproved, or a previous write did not land.
`ambiguous_intent`
    the request means more than one thing, and the choice changes the
    result.
`insufficient_evidence`
    what is on the record does not settle it, so a judgement would be a
    guess.
`resource_contention`
    something else holds the resource: the instance lease, the broker,
    the Resolve session, a deadline.
"""


BLOCKER_PLAIN = {
    "missing_media": "the media this change needs is not there",
    "unsupported_operation": "the engine has no proven way to do this",
    "unsafe_state": "the state on the timeline is not safe to change yet",
    "ambiguous_intent": "the request means more than one thing",
    "insufficient_evidence": "what is on the record does not settle it",
    "resource_contention": "something else is holding the resource",
}
"""Each blocker in plain language, for the line a caller reads first."""


NEXT_ACTIONS = (
    "supply_the_missing_media",
    "supply_the_missing_parameter",
    "use_a_supported_operation",
    "settle_the_state_first",
    "answer_the_question",
    "collect_the_evidence",
    "wait_for_the_resource",
    "state_the_intent",
)
"""The next actions a caller can take, and the only ones.

`supply_the_missing_media`
    put the file, the index or the record where the engine reads it.
`supply_the_missing_parameter`
    carry the argument the engine named - the parameter, the unique id,
    the target - that the request left out.
`use_a_supported_operation`
    ask for something the engine has a proven route for.
`settle_the_state_first`
    resolve what is unsafe - the stale base, the conflict, the
    approval - and then re-run.
`answer_the_question`
    a person answers the smallest question that unblocks this.
`collect_the_evidence`
    record or measure what is missing, then re-run.
`wait_for_the_resource`
    the holder finishes; the same call may be repeated afterwards.
`state_the_intent`
    say which of the several things was meant.
"""


BLOCKER_NEXT_ACTION = {
    "missing_media": "supply_the_missing_media",
    "unsupported_operation": "use_a_supported_operation",
    "unsafe_state": "settle_the_state_first",
    "ambiguous_intent": "state_the_intent",
    "insufficient_evidence": "collect_the_evidence",
    "resource_contention": "wait_for_the_resource",
}
"""The next action each blocker demands, when the refusal names no other."""


FAILURE_CATEGORY_BLOCKER = {
    "unsupported": "unsupported_operation",
    "stale": "unsafe_state",
    "conflict": "unsafe_state",
    "backend_failure": "insufficient_evidence",
    "verification_failed": "insufficient_evidence",
    "recovery_required": "unsafe_state",
    "broken": "insufficient_evidence",
}
"""The execution failure vocabulary, joined to the blocker it reports.

`transaction_orchestration.ExecutionRefusal.CATEGORIES` is the failure
KIND; this is what KIND of block produced it.  A stale base is a
failure kind and an `unsafe_state` blocker: the timeline moved since
the request was written against it.
"""


ASK_REASON_BLOCKER = {
    "ambiguous_intent": "ambiguous_intent",
    "missing_authorization": "unsafe_state",
    "insufficient_evidence": "insufficient_evidence",
}
"""`ren_refusal.ASK_REASONS`, joined to the blocker each one reports.

A missing authorization is an `unsafe_state`: the change is the
person's to approve and nobody has said yes, so acting now would be
unsafe - not ambiguous, and not a gap in the evidence.
"""


FAMILY_BLOCKERS = {
    "library.schemas.project_config.ProjectConfigInvalid": "unsafe_state",
    "library.steps.step_4_02_plan_transitions.post_bridge.TransitionSpecRefused":
        "unsupported_operation",
    "library.tools.asset_generators.GeneratorRefused": "unsupported_operation",
    "library.tools.asset_routing.NeedRefused": "unsupported_operation",
    "library.tools.asset_transformations.TransformationRefused":
        "unsupported_operation",
    "library.tools.assignment_edit_workflow.WorkflowRefused":
        "ambiguous_intent",
    "library.tools.assignment_spine.AssignmentDecisionRequired": "unsafe_state",
    "library.tools.behind_subject.BehindSubjectRefused": "missing_media",
    "library.tools.breakpoints.BreakpointError": "unsupported_operation",
    "library.tools.caption_timing.CaptionRebaseRefused": "missing_media",
    "library.tools.computer_use.client.DriverDead": "resource_contention",
    "library.tools.computer_use.client.DriverError": "unsupported_operation",
    "library.tools.computer_use.model.ModelError": "insufficient_evidence",
    "library.tools.computer_use.scripts.ScriptError": "missing_media",
    "library.tools.computer_use.take.TakeExpired": "resource_contention",
    "library.tools.computer_use.targets.TargetError": "unsupported_operation",
    "library.tools.computer_use.templates.TemplateMissing": "missing_media",
    "library.tools.dag_adapter.NoLegacyNode": "unsupported_operation",
    "library.tools.decision_implication.ImplicationRefused":
        "insufficient_evidence",
    "library.tools.deliver_and_verify.DeliverRefused": "unsafe_state",
    "library.tools.delivered_note_anchor.AnchorRefused":
        "unsupported_operation",
    "library.tools.delivery_normalize.DeliveryNormalizeRefused":
        "unsupported_operation",
    "library.tools.depth_da3.Da3ResearchOnly": "unsupported_operation",
    "library.tools.depth_da3.Da3Unavailable": "unsupported_operation",
    "library.tools.depth.DepthUnavailable": "unsupported_operation",
    "library.tools.dialogue_cleanup.DialogueCleanupRefused":
        "unsupported_operation",
    "library.tools.edit_input_digest.DigestError": "insufficient_evidence",
    "library.tools.edit_patch.PatchRefused": "unsafe_state",
    "library.tools.execution_router.RoutingRefused": "unsupported_operation",
    "library.tools.execution.deliver_loaders.LoaderDeliveryRefused":
        "missing_media",
    "library.tools.external_inputs.ExternalStateError": "insufficient_evidence",
    "library.tools.fabric_permissions.PermissionRefused": "unsafe_state",
    "library.tools.fabric_permissions.ProposalRefused": "unsafe_state",
    "library.tools.frameio_import.FrameioImportRefused":
        "unsupported_operation",
    "library.tools.hooks.HookError": "unsupported_operation",
    "library.tools.jl_cut.JLCutRefused": "unsupported_operation",
    "library.tools.mask_seed.MaskSeedRefused": "insufficient_evidence",
    "library.tools.master_touchup.MasterTouchupRefused": "unsafe_state",
    "library.tools.model_task.LLMError": "insufficient_evidence",
    "library.tools.native_ops.NativeSpeedRefused": "unsupported_operation",
    "library.tools.native_ops.NativeTransitionRefused":
        "unsupported_operation",
    "library.tools.operations.OperationError": "unsupported_operation",
    "library.tools.plan_keys.UnreadPlanKey": "unsupported_operation",
    "library.tools.plan_splice.SpliceRefused": "unsafe_state",
    "library.tools.preservation_invariant.PreservationUnprovable":
        "insufficient_evidence",
    "library.tools.project_asset.ProjectAssetNotFoundError": "missing_media",
    "library.tools.project_format.ProjectFormatRefused": "unsupported_operation",
    "library.tools.project_model_cli.ProjectModelCliRefused":
        "unsupported_operation",
    "library.tools.project_model_qualification.Pm9Refused": "unsafe_state",
    "library.tools.punch_timing.PunchTimingRefused": "unsafe_state",
    "library.tools.reel_acceptance.AcceptanceNotDeclared": "unsafe_state",
    "library.tools.reel_address.ReelAddressRefused": "missing_media",
    "library.tools.reel_build.ReplaceGuardPromotionRefused": "unsafe_state",
    "library.tools.reel_deliver.DeliverRefused": "unsafe_state",
    "library.tools.reel_incremental.IncrementalRefused": "unsafe_state",
    "library.tools.reel_incremental.IncrementalStale": "unsafe_state",
    "library.tools.reel_incremental.IncrementalConflict": "unsafe_state",
    "library.tools.reel_incremental.IncrementalBroken": "insufficient_evidence",
    "library.tools.reel_look.ReelLookRefused": "unsupported_operation",
    "library.tools.reel_preview.PreviewRefused": "unsupported_operation",
    "library.tools.reel_proposal.NotApproved": "unsafe_state",
    "library.tools.reel_proposal.ProposalError": "unsupported_operation",
    "library.tools.reel_signoff.SignOffNotDeclared": "unsafe_state",
    "library.tools.reel_signoff.SignOffsUnreadable": "insufficient_evidence",
    "library.tools.reel_state.UnknownReel": "missing_media",
    "library.tools.reel_touchup.TouchupRefused": "unsafe_state",
    "library.tools.region_stats.UnsourcedSubjectGrade": "insufficient_evidence",
    # PERF-011: a refusal the engine already decided on this exact state.
    # The block is the state: the cause has not changed, so acting on it
    # again would decide the same thing.
    "library.tools.retry_guard.RepeatedRefusal": "unsafe_state",
    "library.tools.render_watch.NoPictureChanged": "insufficient_evidence",
    "library.tools.render_watch.NotDelivered": "missing_media",
    "library.tools.render_watch.NothingWasWatched": "missing_media",
    "library.tools.replay_ledger.ReplayLedgerRefused": "unsafe_state",
    "library.tools.requirements.MissingPrerequisiteRefused": "missing_media",
    "library.tools.requirements.OverrideError": "ambiguous_intent",
    "library.tools.resolve_deadline.ResolutionNotApplied":
        "insufficient_evidence",
    "library.tools.resolve_session.SessionProjectRefused": "unsafe_state",
    "library.tools.resolved.client.ShutdownRefused": "unsafe_state",
    "library.tools.resolve_deadline.ResolveCallTimeout": "resource_contention",
    "library.tools.resolve_lock.ResolveBusy": "resource_contention",
    "library.tools.resolve_swapback.SwapbackRefused": "unsafe_state",
    "library.tools.resolve_watchdog.ResolveDegraded": "resource_contention",
    "library.tools.resolved.client.TestSpawnRefused": "unsafe_state",
    "library.tools.retention.PurgeRefused": "unsafe_state",
    "library.tools.retention.RetentionSettingInvalid": "unsupported_operation",
    "library.tools.revise.ReviseRefused": "unsupported_operation",
    "library.tools.room_tone.RoomToneRefused": "insufficient_evidence",
    "library.tools.round_page.RoundPageRefused": "unsafe_state",
    "library.tools.run_profile.ProfileError": "unsupported_operation",
    "library.tools.run_scope.ScopeError": "unsupported_operation",
    "library.tools.source_transcript.BuildNeedsATimeline": "unsafe_state",
    "library.tools.source_transcript.SourceTranscriptRefused":
        "insufficient_evidence",
    "library.tools.state_splice.StateSpliceRefused": "unsafe_state",
    "library.tools.sub_block_anchor.AnchorRefused": "unsupported_operation",
    "library.tools.take_pick_preview.TakePickPreviewError":
        "unsupported_operation",
    "library.tools.target_resolution.TargetResolutionRefused":
        "ambiguous_intent",
    "library.tools.transcript_hygiene.HygieneError": "insufficient_evidence",
    "library.tools.uncarried_notes.DischargeRefused": "ambiguous_intent",
    "library.tools.uncarried_notes.UncarriedNotesOpen": "unsafe_state",
    "library.tools.uncarried_notes.UncarriedNotesUnreadable":
        "insufficient_evidence",
    "library.tools.uncarried_notes.UncarriedNoteUnknown":
        "insufficient_evidence",
    "library.tools.undo_journal.QueueRefused": "unsafe_state",
    "library.tools.undo_journal.UndoRefused": "unsafe_state",
    "library.tools.variant_exploration.VariantRefused": "unsupported_operation",
    "library.tools.versions.variants.ChoiceRefused": "unsafe_state",
}
"""The INVENTORY: every refusal family and the blocker it reports.

Keyed by the qualified class name, because three class names are used
twice by families that block for different reasons.  A family that is
not here cannot be raised: `ren_refusal.RenRefusal` refuses to be
constructed without a blocker, so a NEW refusal family has to be
classified here before it can be raised at all.
"""


NAME_BLOCKERS: dict[str, str] = {}
"""The same inventory, keyed by the family NAME alone.

A module can be imported under two names - `library.tools.native_ops`
and `tools.native_ops` both reach the same file - so the qualified key
is not always the name the class carries at runtime.  The name is the
stable part, and the three families whose name is used twice
(`AnchorRefused`, `DeliverRefused`, `PatchRefused`) block for the same
reason in both places, so the name is enough.
"""

for _key, _blocker in FAMILY_BLOCKERS.items():
    _name = _key.rsplit(".", 1)[-1]
    _existing = NAME_BLOCKERS.get(_name)
    if _existing and _existing != _blocker:
        raise ValueError(
            f"the refusal families named {_name!r} block for different "
            f"reasons ({_existing} and {_blocker}); name them by module "
            "in FAMILY_BLOCKERS and declare BLOCKER on the family")
    NAME_BLOCKERS[_name] = _blocker
del _key, _blocker, _name, _existing


def blocker_for(cls: type) -> str:
    """The blocker a refusal family reports, found along its MRO.

    A subclass inherits its parent's blocker, and a family that declares
    its own (`RenRefusal.BLOCKER`) overrides the table.
    """
    for klass in getattr(cls, "__mro__", (cls,)):
        declared = getattr(klass, "BLOCKER", "")
        if declared:
            return str(declared)
        key = f"{klass.__module__}.{klass.__name__}"
        found = FAMILY_BLOCKERS.get(key) or NAME_BLOCKERS.get(
            klass.__name__)
        if found:
            return found
    raise ValueError(
        f"no blocker is classified for {cls.__module__}.{cls.__name__}; "
        "classify it in library/tools/refusal_taxonomy.py "
        "(FAMILY_BLOCKERS) before raising it")


def plain_reason(blocker: str) -> str:
    """The blocker's plain-language sentence."""
    if blocker not in BLOCKER_PLAIN:
        raise ValueError(f"unknown blocker {blocker!r}; one of "
                         f"{list(BLOCKERS)}")
    return BLOCKER_PLAIN[blocker]


def next_action_for(blocker: str) -> str:
    """The next action a blocker demands."""
    if blocker not in BLOCKER_NEXT_ACTION:
        raise ValueError(f"unknown blocker {blocker!r}; one of "
                         f"{list(BLOCKERS)}")
    return BLOCKER_NEXT_ACTION[blocker]


def blocker_for_failure_category(category: str) -> str:
    """The blocker an execution failure category reports."""
    found = FAILURE_CATEGORY_BLOCKER.get(str(category))
    if not found:
        raise ValueError(f"unknown failure category {category!r}; one of "
                         f"{sorted(FAILURE_CATEGORY_BLOCKER)}")
    return found


def blocker_for_ask_reason(reason: str) -> str:
    """The blocker an ask reason reports."""
    found = ASK_REASON_BLOCKER.get(str(reason))
    if not found:
        raise ValueError(f"unknown ask reason {reason!r}; one of "
                         f"{sorted(ASK_REASON_BLOCKER)}")
    return found


_FLAG = re.compile(r"--[A-Za-z0-9][A-Za-z0-9_-]*")
_BACKTICKED = re.compile(r"`([a-z][A-Za-z0-9_.-]*)`")
_QUOTED_KEY = re.compile(r"[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']\s*:")


def named_arguments(text: str) -> tuple[str, ...]:
    """The arguments a piece of prose NAMES.

    A fix that recommends an argument the caller cannot supply is the
    defect this finds (measured 2026-10-20: "a ripple that overlaps a
    caption row is refused with a fix the caller cannot apply", PR
    3076).  Three shapes count: a `--flag`, a backticked single word,
    and a quoted JSON key.  A command with a space in it (`ren touch`)
    is not an argument, so it is not read as one.
    """
    found: list[str] = []
    for pattern in (_FLAG, _BACKTICKED, _QUOTED_KEY):
        for match in pattern.finditer(str(text or "")):
            token = match.group(0) if pattern is _FLAG else match.group(1)
            token = token.strip()
            if token and token not in found:
                found.append(token)
    return tuple(found)


def _normalise(token: str) -> str:
    return token.lstrip("-").lower().replace("-", "_")


SUPPLYABLE_SURFACES = ("touchup_edit", "axi_command")
"""The surfaces whose vocabulary a refusal's fix may name."""


def supplyable(surface: str) -> tuple[str, ...]:
    """What the caller can supply on a named surface.

    A surface is a place where the caller hands the engine arguments:
    the touch-up edit object, the AXI command line.  The vocabulary is
    read from the OWNING module, so it cannot drift from what that
    module actually accepts.
    """
    if surface == "touchup_edit":
        from library.tools import reel_touchup

        return reel_touchup.supplyable_arguments()
    if surface == "axi_command":
        from library.tools import resolve_axi_core_commands

        return resolve_axi_core_commands.supplyable_commands()
    raise ValueError(f"unknown supplyable surface {surface!r}; one of "
                     f"{list(SUPPLYABLE_SURFACES)}")


def assert_supplyable(text: str, vocabulary: Iterable[str], *,
                       where: str = "") -> None:
    """Refuse a fix that names something the caller cannot supply.

    `vocabulary` is what the caller CAN supply on this surface - the
    edit keys a touchup accepts, the commands the AXI has.  A named
    argument outside it is a recommendation the caller cannot act on,
    and it is raised here so the refusal never reaches them.
    """
    allowed = {_normalise(token) for token in vocabulary}
    if not allowed:
        return
    for token in named_arguments(text):
        if _normalise(token) in allowed:
            continue
        raise ValueError(
            f"{where or 'the refusal'} recommends {token!r}, which the "
            f"caller cannot supply; the arguments available here are "
            f"{sorted(allowed)}")
