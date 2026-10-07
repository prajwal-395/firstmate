"""Where the loop may act: the AppTarget abstraction.

The loop, the observation and the action schema were built against one
app - Resolve, through the driver. The captain's third use is the same
setup reused for apps with no scripting API, with Canva (a web app in
Chrome) as the worked example. An `AppTarget` is what varies between
the two: which window is the target, what safety gate bounds the work,
and which input routes reach it in the background.

The Resolve target reuses everything the earlier slices built - the
driver is its executor, `qualification_project.borrowed()` plus the
resolve lease is its gate. Its background routes are exactly the
probe's measured table (`docs/RESOLVE_CONTROL_PROBE.md`, "Background
input"): AX press and AX set-value reach Resolve with the captain's
cursor untouched; CGEventPostToPid clicks, drags, scrolls and keys
changed 0 pixels. Nothing else is claimed.

The Canva target is observed and focused through the loop and gated
by design. Its executor is `run_loop` over the driver's chrome route:
screenshots, AX and OCR of Chrome windows read fine and
focus-without-raise lands with nothing stolen. Clicks are the honest
limit - measured filtered on Chromium 154 (probe doc) - so every act
goes through verify-or-escalate and a click that changes nothing fails
its step loudly. What the Canva target enforces is the scratch-design
gate: the Resolve analog of the qualification project. `CANVA_SCRATCH_DESIGNS` names the design
edit URLs the loop may touch (comma-separated); any other design -
and every design when the variable is empty - is refused. The match
is exact: no Canva URL semantics are parsed or assumed.

Bundle IDs are measured, not guessed: `defaults read` on this Mac
(2026-10-06) answers `com.blackmagic-design.DaVinciResolve` for
`/Applications/DaVinci Resolve/DaVinci Resolve.app` and
`com.google.Chrome` for `/Applications/Google Chrome.app`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from library.tools.ren_refusal import RenRefusal

#: Measured on this Mac 2026-10-06 (`defaults read ... CFBundleIdentifier`).
RESOLVE_BUNDLE_ID = "com.blackmagic-design.DaVinciResolve"
CHROME_BUNDLE_ID = "com.google.Chrome"

#: The design edit URLs the loop may touch, comma-separated. Empty
#: means none - the gate defaults closed, the way an unnamed
#: qualification project means the driver refuses every action.
CANVA_SCRATCH_ENV = "CANVA_SCRATCH_DESIGNS"


class TargetError(RenRefusal):
    """A target that could not be selected or entered - refused with the fix."""


@dataclass(frozen=True)
class AppTarget:
    """One app the computer-use layer may act on.

    `background_routes` names the measured input routes that reach the
    target while it stays in the background; empty means none is
    measured, and `background_input` is False then. `background_why`
    records the measurement or its absence next to the flag, so a
    reader never has to guess what the boolean rests on.
    `has_executor` is whether anything in the repo can drive the
    target today - the Resolve driver exists, a Chrome one does not.
    """

    name: str
    bundle_id: str
    title_contains: str = ""
    background_input: bool = False
    background_routes: tuple[str, ...] = ()
    background_why: str = ""
    has_executor: bool = False
    gate: str = ""


def resolve_target() -> AppTarget:
    """The Resolve target: the driver executes, the probe's AX rows reach it."""
    return AppTarget(
        name="resolve",
        bundle_id=RESOLVE_BUNDLE_ID,
        background_input=True,
        background_routes=("ax_press", "ax_set_value"),
        background_why=(
            "probe-measured 2026-10-05 (docs/RESOLVE_CONTROL_PROBE.md, "
            "'Background input'): AX press and AX set-value reached Resolve "
            "with the frontmost app unchanged; CGEventPostToPid click, drag, "
            "scroll and key changed 0 pixels"
        ),
        has_executor=True,
        gate="qualification_project.borrowed() plus the resolve lease",
    )


def canva_target() -> AppTarget:
    """The Canva target: observed and focused through the loop, gated by design.

    The executor is the observe-decide-act loop over the driver's chrome
    route: screenshots, AX and OCR of Chrome windows read fine, and
    focus-without-raise lands with nothing stolen. Clicks are the honest
    limit - measured filtered on Chromium 154 (probe doc, four recipe
    variants, tap-verified) - so every act goes through the loop's
    verify-or-escalate and a click that changes nothing fails its step
    loudly instead of reading as success. Nothing lands silently.
    """
    return AppTarget(
        name="canva",
        bundle_id=CHROME_BUNDLE_ID,
        title_contains="Canva",
        background_input=False,
        background_routes=(),
        background_why=(
            "measured filtered on Chromium 154 (docs/RESOLVE_CONTROL_PROBE.md, "
            "Chrome SkyLight section): focus lands, clicks post clean and "
            "the renderer drops them - so no input route is claimed"
        ),
        has_executor=True,
        gate=f"${CANVA_SCRATCH_ENV} scratch-design allowlist",
    )


_TARGETS = {
    "resolve": resolve_target,
    "canva": canva_target,
}


def select_target(name: str) -> AppTarget:
    """Select a target by name. An unknown name is refused, not guessed."""
    try:
        return _TARGETS[name]()
    except KeyError:
        raise TargetError(
            f"unknown computer-use target {name!r}",
            f"the targets are: {', '.join(sorted(_TARGETS))}",
            "pass --target resolve or --target canva",
            code="TARGET_UNKNOWN",
        ) from None


def canva_scratch_designs() -> tuple[str, ...]:
    """The allowlisted scratch-design URLs, or empty when none is named."""
    raw = os.environ.get(CANVA_SCRATCH_ENV, "")
    return tuple(entry.strip() for entry in raw.split(",") if entry.strip())


def check_canva_design(url: str) -> str:
    """Gate one design URL against the scratch allowlist.

    Returns the matched allowlist entry. A design nobody allowlisted -
    and any design when the allowlist is empty - is refused naming the
    variable to set, the way the driver refuses work outside the
    qualification project.
    """
    allowed = canva_scratch_designs()
    for entry in allowed:
        if url == entry:
            return entry
    raise TargetError(
        "that Canva design is not an allowlisted scratch design",
        (
            f"${CANVA_SCRATCH_ENV} names {len(allowed)} design(s); "
            "an empty allowlist refuses everything"
        ),
        f"add the scratch design's edit URL to ${CANVA_SCRATCH_ENV} and retry",
        code="TARGET_NOT_SCRATCH",
    )


def require_executor(target: AppTarget) -> None:
    """Refuse a task on a target nothing can drive yet.

    Both shipped targets currently have executors (Resolve through the
    driver, Canva through the loop's chrome route), so this passes them
    through; it stays as the gate a future executorless target trips.
    """
    if not target.has_executor:
        raise TargetError(
            f"the {target.name} target has no executor in the repo",
            "no input route reaches it backgrounded or announced",
            "run with --target resolve",
            code="TARGET_NO_EXECUTOR",
        )
