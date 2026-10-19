"""resolve.ui_transaction: safe UI manipulation inside the one-Resolve model.

The broker job kind that gives Ren hands and eyes inside Resolve without
ever clicking around while another Ren process writes. The operation is
always EXCLUSIVE: a UI action changes the page, the selection, the
cursor, the panels, the focus and the window state, so it cannot run
beside any other Resolve job. The broker's executor holds the exclusive
lease for the whole job (`server.Broker._run`); this module runs inside
it.

One transaction (the plan's `ResolveUITransaction`)::

    {
      "project": "Scratch",              # the OPEN Resolve project
      "timeline": "Reel 07",              # established, not required current
      "target": {"clip_id": "abc"},       # what the actions act on
      "preconditions": {"page": "color"}, # refused where unmet
      "actions": [ResolveUIAction, ...],  # sandbox-checked, run in order
      "verification": {...},              # API/readback, run after
      "restoration": {"page": true, "timeline": true, "playhead": true},
      "foreground": {"reason": "...", "budget_seconds": 60},
                                          # announced screen take, Qt only
      "review_dir": "..."                # where the receipt is filed
    }

Execution (the plan's section 15): snapshot the interaction context,
establish the target project/timeline/playhead, run the declared actions
through the resolve-control driver, run the declared verification, restore
the recoverable UI state, write the receipt. The context is the plan's
`ResolveInteractionContext` - project, timeline, page, playhead, selected
clip/track, focused panel - read from what the scripting API and the
driver's accessibility tree expose. What the scripting API does not
expose (selection, focused panel) is recorded as None, never invented,
and left alone on restore.

The sandbox (the plan's section 17) is enforced here, not by convention:
a declared action outside the allowed set, or one whose target resolves
outside the Resolve window, is refused BEFORE anything runs. The driver
checks the window boundary again on every coordinate action.

The receipt (the plan's section 18) carries operation, provider, observed
before/after, actions, verification and result. It is returned as the
job's result and, where the transaction names a `review_dir`, filed into
the project's event log as a `ui_transaction` event - the UI path's
answer to the API edit's `timeline_operations` event, and therefore a
project-graph operation node - and, where it also names a timeline, into
Ren's edit history (`reel_edit_history`) as a `ui_transaction` entry with
the provider and UI-action fields in its refs - the per-reel account
`ren undo --list` and the history chain read.

The driver is the official `DriverClient` from
`library.tools.computer_use.client` - the resolve-control native driver's
ONE Python side. Element targets carry search criteria (role, title,
label, and the menu `path` above the element); the runner resolves them
to a ref via `find_element` before acting, so the driver's ref table is
honoured, and refuses an ambiguous match instead of acting on the first
of several.
"""

from __future__ import annotations

import contextlib
import logging
import subprocess
import time
from types import MappingProxyType

from library.tools.resolved.jobs import JobRefused

_logger = logging.getLogger(__name__)

#: The UI actions the sandbox allows (the plan's section 17). Anything
#: else - close, delete, quit, a filesystem dialog - is refused before
#: the transaction runs, so a forbidden act cannot reach the driver by
#: being misspelled into the allowed set.
ALLOWED_ACTIONS = frozenset({
    "press", "set_value", "choose", "type", "key", "click",
    "double_click", "drag", "scroll", "wait",
})

#: Targets that are never allowed, whatever the action. Named so a
#: transaction that declares one is refused by name rather than by
#: omission from `ALLOWED_ACTIONS`.
FORBIDDEN_INTENTS = frozenset({
    "close_application", "quit_application", "delete_project",
})

#: The target kinds the action language knows (the plan's section 3).
TARGET_KINDS = frozenset({
    "accessibility_element", "text_anchor", "icon_anchor",
    "qt_control", "viewer_coordinate", "window_relative_coordinate",
})

#: The search criteria an accessibility_element target may carry. The
#: runner resolves them to a ref via the driver's `find_element`. `path`
#: is the menu path above the element, outermost first (e.g.
#: `["Workspace", "Switch to Page"]`) - the driver's own doc requires
#: refining a bare title with its path before pressing, because one title
#: names several commands (every page name sits under both Switch to Page
#: and Show Page, measured 2026-10-06).
_ELEMENT_CRITERIA = ("role", "title", "title_contains", "label", "value",
                     "path")
#: What a transaction restores by default: the recoverable UI state the
#: plan names (page/timeline/playhead). Selection and focused panel are
#: snapshotted but not restored - the scripting API exposes no write
#: for them.
DEFAULT_RESTORATION = {"page": True, "timeline": True, "playhead": True}

#: The verification kinds the runner executes. An unknown kind is refused
#: rather than skipped - a verification that cannot run is not a
#: verification. `text_visible` asserts; `read_text` observes - it
#: returns what the screen says (for callers that must read a result
#: the plan could not name in advance) and always matches. It is not
#: an expects system: the replay-suite lane owns per-step expects once
#: merged, and this composes with it rather than duplicating it.
VERIFICATION_KINDS = frozenset({
    "text_visible", "read_text", "api_readback", "timeline_item_properties",
    "color_node_count", "fusion_parameters", "audio_parameters",
    "rendered_pixels",
})

#: The announced foreground window in seconds. Source: the firstmate
#: decision on the IntelliSearch lane, 2026-10-06 ("announced ~60s
#: take"): a take holds the captain's screen for at most this long.
#: A spec may name a SMALLER budget; a larger one is refused - a longer
#: take needs a new announcement, not a quieter overrun.
TAKE_SECONDS = 60

#: The api_readback getters the runner can read off the resolve handle.
_API_READBACKS = ("page", "playhead", "timeline")

# Operation adapters available for the AXI registry to discover. These are
# explicit adapters for controls with no equivalent in the installed native
# API. They remain non-production until their declared live qualification is
# run; a descriptive row never enables a write. The underlying recipes own
# measured locators and their domain-specific verification.
UI_OPERATION_ADAPTERS = MappingProxyType({
    "color.window.create": MappingProxyType({
        "provider": "resolve.ui",
        "resolve_versions": ("21.1",),
        "implementation": "library.tools.computer_use.window_recipe",
        "target": ("timeline_unique_id", "node_index"),
        "control": "Color > Nodes > Add Serial Node + CPW/LPW/PPW/PCW",
        "preconditions": ("exact project and timeline", "Color page visible",
                          "selected clip unique_id matches target",
                          "target node is the appended node"),
        "verification": ("node count increments exactly once",
                          "rendered pixels change"),
        "recovery": "restore captured node graph; remove only the appended node after identity read-back",
        "qualification": "built, run deferred",
        "production_enabled": False,
    }),
    "color.window.modify": MappingProxyType({
        "provider": "resolve.ui",
        "resolve_versions": ("21.1",),
        "implementation": "library.tools.computer_use.window_modify",
        "target": ("timeline_unique_id", "node_index", "named parameters"),
        "control": "Color page node panel; measured OCR labels Size/Aspect/Pan/Tilt/Rotate/Opacity; value field offset +55x/+5pt",
        "preconditions": ("exact project and timeline", "Color page visible",
                          "selected clip unique_id and node index match target"),
        "verification": ("independent rendered-pixel comparison",
                          "property read-back when available"),
        "recovery": "restore captured node parameters and verify pixels and properties",
        "qualification": "built, run deferred",
        "production_enabled": False,
    }),
    "color.window.track": MappingProxyType({
        "provider": "resolve.ui",
        "resolve_versions": ("21.1",),
        "implementation": "library.tools.computer_use.window_track",
        "target": ("timeline_unique_id", "node_index", "viewer region"),
        "control": "Color page tracker controls through measured accessibility/OCR locators",
        "preconditions": ("exact project and timeline", "Color page visible",
                          "selected clip and node match target", "tracking range is explicit"),
        "verification": ("tracker result read-back", "rendered-pixel comparison over sampled frames"),
        "recovery": "restore captured tracker parameters and re-render the sampled range",
        "qualification": "built, run deferred",
        "production_enabled": False,
    }),
    "fairlight.eq.set": MappingProxyType({
        "provider": "resolve.ui",
        "resolve_versions": ("21.1",),
        "implementation": "library.tools.fairlight_eq",
        "target": ("dialogue track role", "band type", "frequency", "gain"),
        "control": "Fairlight page dialogue track EQ controls; unmapped controls refuse",
        "preconditions": ("exact project and timeline", "Fairlight page visible",
                          "one uniquely resolved dialogue track", "mapped control anchors"),
        "verification": ("control-value read-back", "audio render measurement"),
        "recovery": "restore captured band values; render and compare audio before accepting",
        "qualification": "built, run deferred",
        "production_enabled": False,
    }),
})


def ui_operation_adapter(operation_id: str) -> dict | None:
    """Return a copy of one adapter descriptor for AXI discovery/registration.

    The returned record is descriptive qualification metadata only. Routing
    still requires the canonical operation/backend registry to independently
    mark the pair qualified and production-enabled.
    """
    descriptor = UI_OPERATION_ADAPTERS.get(operation_id)
    if descriptor is None:
        return None
    return {**descriptor}


# ── The spec, validated before the job is queued ─────────────────────

def validate_spec(params: dict) -> dict:
    """Check one transaction spec, or raise `JobRefused`.

    Called from `jobs.prepare`, so a malformed transaction is refused
    before it is queued - the same fail-closed boundary the other job
    kinds have. Returns the normalised spec.
    """
    if not isinstance(params, dict):
        raise JobRefused("resolve.ui_transaction params must be a JSON object")
    actions = params.get("actions")
    if not isinstance(actions, list) or not actions:
        raise JobRefused(
            "resolve.ui_transaction needs 'actions', a non-empty list")
    for index, action in enumerate(actions):
        _validate_action(action, index, params)
    verification = params.get("verification")
    if verification is not None:
        _validate_verification(verification)
    restoration = params.get("restoration")
    if restoration is not None:
        if not isinstance(restoration, dict):
            raise JobRefused("restoration must be an object")
        unknown = set(restoration) - set(DEFAULT_RESTORATION)
        if unknown:
            raise JobRefused(
                f"restoration names unknown field(s) {sorted(unknown)}; "
                f"one of {sorted(DEFAULT_RESTORATION)}")
    foreground = params.get("foreground")
    if foreground is not None:
        _validate_foreground(foreground)
    return params


def _validate_foreground(foreground) -> None:
    """A screen take says why and for how long, or it does not run.

    Background input never reaches Resolve's Qt widgets (measured), so
    some transactions need the real screen - and a transaction that
    takes it without saying why takes it silently. `reason` names the
    need (it is announced); `budget_seconds` caps the take at the
    announced window (`TAKE_SECONDS`).
    """
    if not isinstance(foreground, dict):
        raise JobRefused("foreground must be an object")
    if not str(foreground.get("reason") or "").strip():
        raise JobRefused(
            "a foreground take needs 'reason', what needs the real "
            "screen - the take is announced, and an unannounced take "
            "is refused before anything runs")
    budget = foreground.get("budget_seconds", TAKE_SECONDS)
    if (not isinstance(budget, (int, float))
            or not (0 < float(budget) <= TAKE_SECONDS)):
        raise JobRefused(
            f"foreground budget_seconds must be within (0, "
            f"{TAKE_SECONDS}] - a longer take needs a new "
            f"announcement, not a quieter overrun")


def announce_take(reason: str, budget_seconds: float) -> str:
    """Tell the captain Ren is taking the screen. Best-effort, never raises.

    A macOS notification names the need and the window; the text is
    returned for the receipt. A notification that cannot fire (headless
    runner, osascript missing) is not a failed take - the receipt still
    records that the take was announced, and the take itself proceeds.
    """
    text = (f"Ren is taking the Resolve screen for up to "
            f"{float(budget_seconds):.0f}s: {reason}")
    try:
        safe = str(reason).replace('"', "'")[:200]
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{safe}" with title "Ren"'],
            capture_output=True, encoding="utf-8", timeout=5, check=False)
    except Exception:
        _logger.debug("could not announce foreground Resolve take", exc_info=True)
    return text


def _validate_action(action, index: int, params: dict) -> None:
    if not isinstance(action, dict):
        raise JobRefused(f"action {index} must be an object")
    name = action.get("action")
    if name not in ALLOWED_ACTIONS:
        raise JobRefused(
            f"action {index}: {name!r} is not an allowed UI action; "
            f"one of {sorted(ALLOWED_ACTIONS)} - the sandbox acts inside "
            f"the Resolve window only, and a forbidden act is refused "
            f"before anything runs")
    intent = str(action.get("intent") or "")
    if intent in FORBIDDEN_INTENTS:
        raise JobRefused(
            f"action {index}: {intent!r} is forbidden; the sandbox "
            f"refuses to close or delete anything")
    if name == "wait":
        if "for" not in action:
            raise JobRefused(
                f"action {index}: a wait action needs 'for', what to wait "
                f"for (e.g. text_visible)")
        return
    target = action.get("target")
    if not isinstance(target, dict):
        raise JobRefused(f"action {index} needs 'target', an object")
    kind = target.get("kind")
    if kind not in TARGET_KINDS:
        raise JobRefused(
            f"action {index}: target kind {kind!r} is not one of "
            f"{sorted(TARGET_KINDS)}")
    if kind == "accessibility_element":
        if not any(target.get(c) for c in _ELEMENT_CRITERIA):
            raise JobRefused(
                f"action {index}: an accessibility_element target needs "
                f"search criteria - one of {_ELEMENT_CRITERIA}")
        path = target.get("path")
        if path is not None and (
                not isinstance(path, list) or not path
                or not all(isinstance(bit, str) and bit for bit in path)):
            raise JobRefused(
                f"action {index}: 'path' is the menu path above the "
                f"element - a non-empty list of menu titles, outermost "
                f"first (e.g. ['Workspace', 'Switch to Page'])")
        if target.get("disambiguation") is not None and not isinstance(
                target["disambiguation"], dict):
            raise JobRefused("disambiguation must be an object of AX criteria")
        if (isinstance(target.get("disambiguation"), dict)
                and set(target["disambiguation"]) - set(_ELEMENT_CRITERIA)):
            raise JobRefused(
                "disambiguation may use only AX element criteria: "
                f"{_ELEMENT_CRITERIA}")
        confidence = target.get("min_confidence", "low")
        if confidence not in {"low", "medium", "high"}:
            raise JobRefused("min_confidence must be low, medium or high")
    if (kind in ("viewer_coordinate", "window_relative_coordinate")
            and ("x" not in target or "y" not in target)):
        raise JobRefused(
            f"action {index}: a {kind} target needs 'x' and 'y'")
    if kind == "text_anchor" and not target.get("text"):
        raise JobRefused(
            f"action {index}: a text_anchor target needs 'text', "
            f"the OCR label to click")
    if kind == "text_anchor" and target.get("region") is not None:
        region = target["region"]
        if (not isinstance(region, dict) or not all(
                isinstance(region.get(k), (int, float))
                and not isinstance(region.get(k), bool)
                for k in ("x", "y", "width", "height"))
                or region.get("width", 0) <= 0
                or region.get("height", 0) <= 0):
            raise JobRefused(
                f"action {index}: text_anchor region needs positive numeric "
                "x, y, width and height in current window points")
    if kind == "icon_anchor" and not target.get("image"):
        raise JobRefused(
            f"action {index}: an icon_anchor target needs 'image', "
            f"the template file to match")
    if kind == "qt_control":
        if name not in ("click", "double_click"):
            raise JobRefused(
                "qt_control locators resolve click targets only; use a "
                "separate foreground key/type action after verified focus")
        locators = target.get("locators")
        if not isinstance(locators, list) or not locators:
            raise JobRefused(
                f"action {index}: qt_control needs ordered 'locators' "
                "(text, template, relative, window_relative, absolute)")
        for locator in locators:
            if not isinstance(locator, dict) or locator.get("kind") not in {
                    "text_anchor", "icon_anchor", "window_relative_coordinate",
                    "viewer_coordinate"}:
                raise JobRefused(
                    f"action {index}: unsupported Qt locator {locator!r}; "
                    "custom Qt controls are resolved from fresh OCR, "
                    "templates or measured coordinates")
            if locator["kind"] == "text_anchor" and not locator.get("text"):
                raise JobRefused("Qt text locator needs exact OCR text")
            if locator["kind"] == "icon_anchor" and not locator.get("image"):
                raise JobRefused("Qt icon locator needs a template image")
            if locator["kind"] in {"window_relative_coordinate",
                                    "viewer_coordinate"} and not locator.get("why"):
                raise JobRefused(
                    "coordinate Qt locator needs a measured 'why' and is "
                    "only a fallback after semantic locators")
    if kind != "accessibility_element" and name not in ("click", "double_click"):
        foreground_ok = (name in ("type", "key")
                         and isinstance(params.get("foreground"), dict))
        if not foreground_ok:
            raise JobRefused(
                f"action {name!r} cannot act on a {kind} target - "
                f"only click and double_click resolve anchors; "
                f"press, choose and set_value act on accessibility_element; "
                f"type and key reach a coordinate target only inside an "
                f"announced foreground take (background keys never reach "
                f"Qt)")


def _validate_verification(verification) -> None:
    if not isinstance(verification, dict):
        raise JobRefused("verification must be an object")
    kind = verification.get("kind")
    if kind not in VERIFICATION_KINDS:
        raise JobRefused(
            f"verification kind {kind!r} is not one of "
            f"{sorted(VERIFICATION_KINDS)}")
    if kind == "text_visible" and not verification.get("text"):
        raise JobRefused("a text_visible verification needs 'text'")
    if kind == "read_text":
        region = verification.get("region")
        if region is not None and not isinstance(region, dict):
            raise JobRefused(
                "a read_text verification's 'region' is an object "
                "with x/y/width/height, or omitted for the full window")
        include = verification.get("include_elements", False)
        if not isinstance(include, bool):
            raise JobRefused(
                "a read_text verification's 'include_elements' is true "
                "or false")
    if (kind == "api_readback"
            and verification.get("getter") not in _API_READBACKS):
        raise JobRefused(
            f"api_readback getter {verification.get('getter')!r} is "
            f"not one of {_API_READBACKS}")
    if kind == "api_readback" and "equals" not in verification:
        raise JobRefused("api_readback needs an explicit equals value")
    if kind in {"timeline_item_properties", "fusion_parameters",
                "audio_parameters"}:
        if not verification.get("unique_id"):
            raise JobRefused(f"{kind} needs the target item's unique_id")
        expected = verification.get("equals")
        if not isinstance(expected, dict) or not expected:
            raise JobRefused(f"{kind} needs a non-empty equals object")
    if kind == "color_node_count":
        if not verification.get("unique_id"):
            raise JobRefused("color_node_count needs the target item's unique_id")
        if not isinstance(verification.get("equals"), int):
            raise JobRefused("color_node_count equals must be an integer")
    if kind == "rendered_pixels":
        if not verification.get("unique_id"):
            raise JobRefused("rendered_pixels needs the target item's unique_id")
        samples = verification.get("samples")
        if not isinstance(samples, list) or not samples:
            raise JobRefused("rendered_pixels needs at least one pixel sample")
        for index, sample in enumerate(samples):
            if not isinstance(sample, dict):
                raise JobRefused(f"rendered_pixels sample {index} must be an object")
            if (type(sample.get("x")) is not int or sample["x"] < 0
                    or type(sample.get("y")) is not int or sample["y"] < 0):
                raise JobRefused(
                    f"rendered_pixels sample {index} needs non-negative integer x/y")
            rgb = sample.get("rgb")
            if (not isinstance(rgb, list) or len(rgb) != 3
                    or any(type(channel) is not int or not 0 <= channel <= 255
                           for channel in rgb)):
                raise JobRefused(
                    f"rendered_pixels sample {index} rgb must be three bytes")
            tolerance = sample.get("tolerance", 0)
            if type(tolerance) is not int or not 0 <= tolerance <= 255:
                raise JobRefused(
                    f"rendered_pixels sample {index} tolerance must be 0..255")


# ── The interaction context: snapshot and restore ────────────────────

def snapshot_context(resolve, driver) -> dict:
    """Read the interaction context, recording what is NOT exposed as None.

    Project, timeline, page and playhead come from the scripting API.
    The focused panel comes from the driver's accessibility tree. The
    selected clip/track is read where the scripting API exposes it and
    is None where it does not - an absent measurement is never invented.
    """
    project = resolve.GetProjectManager().GetCurrentProject()
    timeline = None
    if project is not None:
        try:
            timeline = project.GetCurrentTimeline()
        except Exception:                               # noqa: BLE001
            timeline = None
    page = _read_page(resolve)
    playhead = _read_playhead(timeline)
    selected = _read_selection(timeline)
    focused = _read_focused(driver)
    return {
        "project": (project.GetName() or None) if project else None,
        "timeline": (timeline.GetName() or None) if timeline else None,
        "page": page,
        "playhead": playhead,
        "selected": selected,
        "focused_panel": focused,
    }


def _read_page(resolve) -> str | None:
    try:
        return resolve.GetCurrentPage() or None
    except Exception:                                   # noqa: BLE001
        return None


def _read_playhead(timeline) -> str | None:
    if timeline is None:
        return None
    try:
        return timeline.GetCurrentTimecode() or None
    except Exception:                                   # noqa: BLE001
        return None


def _read_selection(timeline) -> dict | None:
    """The selected clip/track, where the scripting API exposes it.

    The stable scripting API has no selection read on every Resolve
    build, so this is None unless the handle offers one - recorded as
    absence, never as an empty selection.
    """
    if timeline is None:
        return None
    reader = getattr(timeline, "GetSelectedItemsInTrack", None)
    if reader is None:
        return None
    try:
        selected = reader("video", 1) or []
    except Exception:                                   # noqa: BLE001
        return None
    if not selected:
        return None
    return {"track": "video:1", "count": len(selected)}


def _read_focused(driver) -> str | None:
    try:
        tree = driver.call("get_accessibility_tree", {}).get("tree")
    except Exception:                                   # noqa: BLE001
        return None
    focused = _focused_label(tree)
    return focused


def _focused_label(tree) -> str | None:
    if not isinstance(tree, dict):
        return None
    if tree.get("focused"):
        return (str(tree.get("label") or tree.get("title") or "")
                or None)
    for child in tree.get("children") or ():
        hit = _focused_label(child)
        if hit is not None:
            return hit
    return None


def restore_context(resolve, before: dict, restoration: dict,
                    purpose: str = "ui_transaction") -> dict:
    """Put back the recoverable UI state the transaction moved.

    Only what the transaction's `restoration` names, and only what the
    scripting API can write: page, timeline, playhead. Selection and
    focused panel are left alone - the scripting API exposes no write
    for them, and a restore that guessed would be worse than none.

    The timeline restore is a cursor write and goes through
    `resolve_lease(exclusive=True)` - the contract's AST visitor
    recognizes the exclusive context.
    """
    from library.tools.resolve_lock import resolve_lease

    restored: dict = {}
    report = {"restored": restored, "not_restored": {
        "selected": "Resolve API exposes no measured selection restore",
        "focused_panel": "no safe focus restore was measured",
    }}
    for field in ("page", "timeline", "playhead"):
        if not restoration.get(field):
            report["not_restored"][field] = "restore disabled by transaction"
            continue
        value = before.get(field)
        if value is None:
            report["not_restored"][field] = "not observable before transaction"
            continue
        try:
            if field == "page":
                resolve.OpenPage(value)
                if _read_page(resolve) != value:
                    raise RuntimeError("page read-back disagreed")
            elif field == "timeline":
                project = resolve.GetProjectManager().GetCurrentProject()
                timeline = _find_timeline(project, value) if project else None
                if timeline is None:
                    raise RuntimeError("original timeline no longer exists")
                with resolve_lease(purpose, exclusive=True):
                    project.SetCurrentTimeline(timeline)
                if (project.GetCurrentTimeline().GetName() or "") != value:
                    raise RuntimeError("timeline read-back disagreed")
            else:
                project = resolve.GetProjectManager().GetCurrentProject()
                timeline = project.GetCurrentTimeline() if project else None
                if timeline is None:
                    raise RuntimeError("no current timeline")
                timeline.SetCurrentTimecode(value)
                if _read_playhead(timeline) != value:
                    raise RuntimeError("playhead read-back disagreed")
            restored[field] = value
        except Exception as exc:  # noqa: BLE001
            report["not_restored"][field] = f"{type(exc).__name__}: {exc}"
    return report


# ── Establish, act, verify ───────────────────────────────────────────

def _establish(resolve, spec: dict, purpose: str = "ui_transaction") -> None:
    """Make the target project/timeline/playhead current.

    The broker has already checked the OPEN project is the named one.
    The timeline is made current and the playhead set - both real cursor
    writes, which is why the job is EXCLUSIVE. The timeline write goes
    through `resolve_lease(exclusive=True)` - the contract's AST visitor
    recognizes the exclusive context. A precondition on the page is
    checked, not assumed.
    """
    from library.tools.resolve_lock import resolve_lease

    project = resolve.GetProjectManager().GetCurrentProject()
    if project is None:
        raise JobRefused("Resolve has no project open")
    timeline_name = spec.get("timeline") or ""
    if timeline_name:
        timeline = _find_timeline(project, timeline_name)
        with resolve_lease(purpose, exclusive=True):
            project.SetCurrentTimeline(timeline)
    else:
        timeline = project.GetCurrentTimeline()
    if timeline is None:
        raise JobRefused(
            f"no timeline {timeline_name!r} in {project.GetName()!r}")
    preconditions = spec.get("preconditions") or {}
    wanted_page = preconditions.get("page")
    if wanted_page:
        current = _read_page(resolve)
        if current is not None and current != wanted_page:
            raise JobRefused(
                f"precondition unmet: the page is {current!r}, not "
                f"{wanted_page!r}")
    playhead = (spec.get("target") or {}).get("playhead")
    if playhead:
        timeline.SetCurrentTimecode(str(playhead))


def _find_timeline(project, name: str):
    for index in range(1, int(project.GetTimelineCount() or 0) + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline and (timeline.GetName() or "") == name:
            return timeline
    raise JobRefused(
        f"no timeline named exactly {name!r} in {project.GetName()!r}")


def _resolve_element(driver, target: dict) -> str:
    """Resolve an accessibility_element target's criteria to a ref.

    The driver's `find_element` returns elements with an opaque `ref`;
    the action then carries that ref. A target whose criteria match
    nothing is refused by name, and one matching MORE than one element
    is refused as ambiguous - acting on the first of several matches
    pressed the wrong command (a bare "Color" title matched six menu
    items, the page switch fourth, measured 2026-10-06). Refine with
    `path` until exactly one element answers.
    """
    observed = driver.call("get_accessibility_tree", {}).get("tree")
    from library.tools.computer_use.observe import layout_fingerprint, project_ax_tree
    fingerprint = layout_fingerprint(project_ax_tree(observed or {}))
    expected_fingerprint = target.get("layout_fingerprint")
    if expected_fingerprint and expected_fingerprint != fingerprint:
        raise JobRefused(
            f"stale UI locator: layout fingerprint changed from "
            f"{expected_fingerprint!r} to {fingerprint!r}; observe and resolve again")
    criteria = {c: target[c] for c in _ELEMENT_CRITERIA if target.get(c)}
    disambiguation = target.get("disambiguation") or {}
    if not isinstance(disambiguation, dict):
        raise JobRefused("locator disambiguation must be an object of AX criteria")
    criteria.update({c: disambiguation[c] for c in _ELEMENT_CRITERIA
                     if disambiguation.get(c)})
    elements = driver.call("find_element", criteria).get("elements") or []
    if not elements:
        raise JobRefused(
            f"no element matches {criteria} in the accessibility tree")
    if len(elements) > 1:
        raise JobRefused(
            f"{len(elements)} elements match {criteria}; refine with "
            f"'path' (the menu path above the element, outermost first) "
            f"until exactly one matches - the sandbox never acts on the "
            f"first of several")
    exact_fields = set(criteria) & {"role", "title", "label", "value", "path"}
    confidence = ("high" if {"title", "path"} <= exact_fields
                  else "medium" if len(exact_fields) > 1 else "low")
    confidence_rank = {"low": 1, "medium": 2, "high": 3}
    minimum = target.get("min_confidence", "low")
    if confidence_rank[confidence] < confidence_rank[minimum]:
        raise JobRefused(
            f"UI locator confidence {confidence!r} is below required "
            f"{minimum!r}")
    return elements[0]["ref"], {
        "kind": "accessibility_element", "confidence": confidence,
        "basis": "fresh AX observation, exact criteria, unique match",
        "layout_fingerprint": fingerprint,
    }


def _check_locator_version(resolve, target: dict) -> dict:
    expected = target.get("resolve_version")
    actual = resolve.GetVersionString() or None
    if expected and expected != actual:
        raise JobRefused(
            f"UI locator was qualified for Resolve {expected}, current version is "
            f"{actual!r}; refresh or re-qualify the locator")
    return {"expected": expected, "actual": actual,
            "matched": not expected or expected == actual}


def _execute_action(driver, action: dict, foreground: bool = False,
                    resolve=None) -> None:
    """Run one declared action through the driver - one driver call.

    `foreground` threads the announced take through: synthetic input
    (click, drag, scroll, type, key) carries `allow_foreground`, the
    AX element routes (press, choose, set_value, focus) never do - the
    menu bar they reach works in the background, and a foreground flag
    on an AX call is meaningless.
    """
    from library.tools.computer_use.scripts import resolve_anchor

    name = action["action"]
    target = action.get("target") or {}
    kind = target.get("kind")
    on_screen = {"allow_foreground": True} if foreground else {}
    if name == "wait":
        _execute_wait(driver, action)
        return
    if kind == "accessibility_element":
        if resolve is not None:
            _check_locator_version(resolve, target)
        ref, resolution = _resolve_element(driver, target)
        if name in ("press", "choose"):
            driver.call("press", {"element": ref})
        elif name == "set_value":
            driver.call("set_value", {"element": ref,
                                      "value": str(target.get("value") or "")})
        elif name == "focus":
            driver.call("focus", {"element": ref})
        else:
            raise JobRefused(
                f"action {name!r} cannot act on an accessibility_element")
        return resolution
    if (kind != "accessibility_element" and resolve is not None
            and target.get("resolve_version")):
        _check_locator_version(resolve, target)
    if (kind != "accessibility_element" and resolve is not None
            and target.get("layout_fingerprint")):
        from library.tools.computer_use.observe import (
            layout_fingerprint,
            project_ax_tree,
        )
        tree = driver.call("get_accessibility_tree", {}).get("tree") or {}
        actual = layout_fingerprint(project_ax_tree(tree))
        expected = target["layout_fingerprint"]
        if actual != expected:
            raise JobRefused(
                f"stale UI locator: layout fingerprint changed from "
                f"{expected!r} to {actual!r}; observe and resolve again")
    if name in ("click", "double_click") and kind != "accessibility_element":
        candidates = (target.get("locators", []) if kind == "qt_control"
                      else [target])
        resolved = None
        chosen_kind = None
        for candidate in candidates:
            anchor = _target_to_anchor(candidate)
            candidate_result = resolve_anchor(driver, anchor) if anchor else None
            if candidate_result is not None:
                resolved, chosen_kind = candidate_result, candidate.get("kind")
                break
        if resolved is None:
            raise JobRefused(
                f"action {name!r} on a {kind} target resolved nothing: "
                f"{target!r} matches nothing on screen - the layout "
                f"moved or the label is not visible")
        method, params = resolved
        if method != "click":
            raise JobRefused(
                f"action {name!r} on a {kind} target resolved to "
                f"{method!r}, not a point to click")
        if name == "double_click":
            params = {**params, "clicks": 2}
        driver.call("click", {**params, **on_screen})
        confidence = ("high" if chosen_kind == "text_anchor" else
                      "measured_threshold" if chosen_kind == "icon_anchor" else
                      "explicit_coordinate")
        return {"kind": kind, "confidence": confidence,
                "basis": "fresh unique OCR match" if chosen_kind == "text_anchor"
                else "fresh template match at measured threshold"
                if chosen_kind == "icon_anchor"
                else "caller supplied current window point"}
    if name == "click":
        if kind == "accessibility_element":
            raise JobRefused(
                "a click on an accessibility_element names no point - "
                "press, choose, set_value or focus act on elements, "
                "click acts on anchors and coordinates")
        driver.call("click", {"x": float(target["x"]),
                              "y": float(target["y"]), **on_screen})
        return {"kind": kind, "confidence": "explicit_coordinate",
                "basis": "caller supplied current window point"}
    if name == "double_click":
        x, y = float(target["x"]), float(target["y"])
        driver.call("click", {"x": x, "y": y, "clicks": 2, **on_screen})
        return
    if name == "drag":
        driver.call("drag", {"start_x": float(target.get("x", 0)),
                             "start_y": float(target.get("y", 0)),
                             "end_x": float(target.get("to_x", 0)),
                             "end_y": float(target.get("to_y", 0)),
                             **on_screen})
        return
    if name == "scroll":
        driver.call("scroll", {"x": float(target.get("x", 0)),
                               "y": float(target.get("y", 0)),
                               "delta_x": float(target.get("dx", 0)),
                               "delta_y": float(target.get("dy", 0)),
                               **on_screen})
        return
    if name == "type":
        driver.call("type_text", {"text": str(target.get("text") or ""),
                                  **on_screen})
        return
    if name == "key":
        driver.call("key", {"key": str(target.get("key") or ""),
                            "modifiers": list(target.get("modifiers") or ()),
                            **on_screen})
        return
    raise JobRefused(f"action {name!r} has no target route")


def _take_foreground(driver, spec: dict):
    """Announce and take the screen, or back off. Single attempt, no retry.

    Returns the take record (`announced`, `budget_seconds`, `started`)
    the receipt reads back, or None for a background transaction. A
    refused take (locked screen, denied focus) is a `JobRefused` naming
    the back-off: the captain's state is not a failure to hammer.
    """
    foreground = spec.get("foreground")
    if foreground is None:
        return None
    budget = float(foreground.get("budget_seconds", TAKE_SECONDS))
    announced = announce_take(str(foreground["reason"]), budget)
    try:
        driver.call("activate_resolve", {"allow_foreground": True})
    except Exception as exc:
        raise JobRefused(
            f"backing off: the screen take was refused "
            f"({type(exc).__name__}: {exc}) - no retry") from exc
    return {"announced": announced, "budget_seconds": budget,
            "started": time.monotonic()}


def _check_take_budget(take) -> None:
    """End the take where it was announced to end.

    The restore still runs - the receipt records a failed transaction
    with the UI put back, not a transaction abandoned mid-screen.
    """
    if take is None:
        return
    if time.monotonic() - take["started"] > take["budget_seconds"]:
        raise JobRefused(
            f"take budget exceeded ({take['budget_seconds']:.0f}s) - "
            f"the take ends where it was announced to end")


def _target_to_anchor(target: dict):
    """Map a non-element target to the scripts layer's anchor.

    The anchor vocabulary (`computer_use.scripts`) is the runtime half
    of these target kinds: text_anchor resolves like the `ocr` anchor,
    icon_anchor like `template`, both coordinate kinds like `position`.
    The driver speaks window points only - there is no viewer-rect
    geometry to map viewer space through, so both coordinate kinds are
    interpreted in that one space until it exists. Returns None for
    accessibility_element, which resolves through `_resolve_element`.
    """
    from library.tools.computer_use.scripts import Anchor

    kind = target.get("kind")
    if kind == "text_anchor":
        params = {"text": str(target.get("text") or "")}
        if target.get("region") is not None:
            params["region"] = dict(target["region"])
        return Anchor(via="ocr", params=params)
    if kind == "icon_anchor":
        params: dict = {"image": target.get("image")}
        if target.get("threshold") is not None:
            params["threshold"] = target["threshold"]
        return Anchor(via="template", params=params)
    if kind in ("viewer_coordinate", "window_relative_coordinate"):
        return Anchor(via="position", params={"x": target.get("x"),
                                              "y": target.get("y")})
    return None


def _execute_wait(driver, action: dict) -> None:
    spec = action.get("for") or {}
    timeout = float(action.get("timeout_seconds", 5))
    predicate = _build_predicate(spec)
    if predicate is None:
        raise JobRefused(
            f"a wait action's 'for' names nothing runnable: {spec!r}")
    result = driver.call("wait_for", {"predicate": predicate,
                                     "timeout": timeout})
    if not result.get("met"):
        raise JobRefused(f"timed out waiting for {spec}")


def _build_predicate(spec: dict) -> dict | None:
    """Translate a wait spec into the driver's declarative predicate."""
    if spec.get("text_visible"):
        return {"type": "text_visible", "text": str(spec["text_visible"])}
    if spec.get("element_visible"):
        return {"type": "element_visible",
                **dict(spec["element_visible"])}
    if spec.get("seconds"):
        return {"type": "seconds", "duration": float(spec["seconds"])}
    return None


def _check_expect(driver, action: dict) -> None:
    """An action's own post-condition, checked before the next action."""
    expect = action.get("expect") or {}
    if expect.get("text_visible"):
        text = str(expect["text_visible"])
        if text not in driver.call("read_text").get("text", ""):
            raise JobRefused(
                f"action {action.get('action')!r} did not produce "
                f"{text!r} on screen")


def _run_verification(resolve, driver, verification: dict | None) -> dict:
    """Run the declared verification. A verification that cannot run is refused."""
    if verification is None:
        return {"kind": "none", "matched": True, "verified": False,
                "reason": "no edit-state verification was declared"}
    kind = verification.get("kind")
    if kind == "text_visible":
        text = str(verification["text"])
        found = text in driver.call("read_text").get("text", "")
        return {"kind": kind, "text": text, "matched": found,
                "verified": False}
    if kind == "read_text":
        params = {}
        if isinstance(verification.get("region"), dict):
            params["region"] = dict(verification["region"])
        observed = driver.call("read_text", params)
        out = {"kind": kind,
               "text": str(observed.get("text", "")),
               "matched": True, "verified": False}
        if verification.get("include_elements", False):
            out["elements"] = observed.get("elements") or []
        return out
    if kind == "api_readback":
        getter = verification["getter"]
        actual = _api_readback(resolve, driver, getter)
        expected = verification.get("equals")
        return {"kind": kind, "getter": getter, "expected": expected,
                "actual": actual, "matched": actual == expected,
                "verified": True}
    if kind in {"timeline_item_properties", "audio_parameters"}:
        item = _find_timeline_item(resolve, verification["unique_id"])
        actual = item.GetProperty() or {}
        expected = verification["equals"]
        return {"kind": kind, "unique_id": verification["unique_id"],
                "expected": expected,
                "actual": {key: actual.get(key) for key in expected},
                "matched": all(actual.get(key) == value
                               for key, value in expected.items()),
                "verified": True}
    if kind == "color_node_count":
        item = _find_timeline_item(resolve, verification["unique_id"])
        graph = item.GetNodeGraph()
        actual = graph.GetNumNodes() if graph is not None else None
        expected = verification["equals"]
        return {"kind": kind, "unique_id": verification["unique_id"],
                "expected": expected, "actual": actual,
                "matched": actual == expected, "verified": True}
    if kind == "fusion_parameters":
        item = _find_timeline_item(resolve, verification["unique_id"])
        expected = verification["equals"]
        actual = _read_fusion_parameters(item, expected)
        return {"kind": kind, "unique_id": verification["unique_id"],
                "expected": expected, "actual": actual,
                "matched": actual == expected, "verified": True}
    if kind == "rendered_pixels":
        item = _find_timeline_item(resolve, verification["unique_id"])
        from library.tools.color_page_grade import render_one_frame_deliver

        project = resolve.GetProjectManager().GetCurrentProject()
        timeline = project.GetCurrentTimeline()
        frame = render_one_frame_deliver(resolve, project, timeline, item)
        height, width = frame.shape[:2]
        actual = []
        matched = True
        for sample in verification["samples"]:
            x, y = sample["x"], sample["y"]
            if x >= width or y >= height:
                raise JobRefused(
                    f"rendered pixel ({x}, {y}) is outside output frame "
                    f"{width}x{height}")
            rgb = [int(channel) for channel in frame[y, x, :3]]
            sample_match = all(
                abs(observed - wanted) <= sample.get("tolerance", 0)
                for observed, wanted in zip(rgb, sample["rgb"], strict=True))
            actual.append({"x": x, "y": y, "rgb": rgb,
                           "matched": sample_match})
            matched = matched and sample_match
        return {"kind": kind, "unique_id": verification["unique_id"],
                "expected": verification["samples"], "actual": actual,
                "rendered_frame": {"width": width, "height": height,
                                   "sample": "target item start"},
                "matched": matched, "verified": True}
    raise JobRefused(f"verification kind {kind!r} is not runnable")


def _find_timeline_item(resolve, unique_id: str):
    project = resolve.GetProjectManager().GetCurrentProject()
    timeline = project.GetCurrentTimeline() if project else None
    if timeline is None:
        raise JobRefused("edit-state verification needs a current timeline")
    matches = []
    for track_type in ("video", "audio"):
        for index in range(1, int(timeline.GetTrackCount(track_type) or 0) + 1):
            for item in timeline.GetItemListInTrack(track_type, index) or []:
                if (item.GetUniqueId() or "") == unique_id:
                    matches.append(item)
    if len(matches) != 1:
        raise JobRefused(
            f"timeline item {unique_id!r} resolved to {len(matches)} items; "
            "verification requires one exact live item")
    return matches[0]


def _read_fusion_parameters(item, expected: dict) -> dict:
    """Read named Fusion tool inputs, keyed as `ToolName.InputName`."""
    for index in range(1, int(item.GetFusionCompCount() or 0) + 1):
        comp = item.GetFusionCompByIndex(index)
        if comp is None:
            continue
        actual = {}
        for qualified in expected:
            if "." not in qualified:
                raise JobRefused(
                    f"Fusion parameter {qualified!r} must be ToolName.InputName")
            tool_name, input_name = qualified.split(".", 1)
            tool = comp.FindTool(tool_name)
            inp = tool.FindInput(input_name) if tool is not None else None
            actual[qualified] = inp.GetValue() if inp is not None else None
        return actual
    raise JobRefused("Fusion parameter verification found no Fusion composition")


def _api_readback(resolve, driver, getter: str):
    project = resolve.GetProjectManager().GetCurrentProject()
    if getter == "page":
        return _read_page(resolve)
    if getter == "timeline":
        timeline = (project.GetCurrentTimeline() if project else None)
        return (timeline.GetName() or None) if timeline else None
    if getter == "playhead":
        timeline = (project.GetCurrentTimeline() if project else None)
        return _read_playhead(timeline)
    raise JobRefused(f"api_readback getter {getter!r} is not runnable")


# ── The runner ──────────────────────────────────────────────────────

def _native_driver_factory(backend):
    from library.tools.computer_use.client import DriverClient
    return DriverClient()


#: The driver factory the runner uses. Production uses the official
#: `DriverClient`, which spawns the native resolve-control driver and
#: refuses the job when the driver is not installed. The tests
#: monkeypatch this to inject the fake - the fake lives in the tests
#: and is never the default.
driver_factory = _native_driver_factory


def run(job: dict, resolve, project=None, factory=None) -> dict:
    """Run one resolve.ui_transaction job. Returns its receipt.

    Snapshot, establish, act, verify, restore, receipt - in that order,
    with the restore and the receipt happening whether the transaction
    committed or failed. A failure is recorded in the receipt's status
    and result, and the receipt is still filed: a UI transaction that
    did not land is a fact Ren's history must hold, not a silent no-op.

    `factory` overrides the driver source for one run; the module-level
    `driver_factory` is the default. A factory that raises
    `DriverDead` refuses the job BEFORE anything runs - no snapshot, no
    action, no receipt - so a transaction that cannot reach the real
    driver never files a committed record.
    """
    from library.tools.computer_use.client import DriverDead

    driver_source = factory or driver_factory
    try:
        driver = driver_source(resolve)
    except DriverDead as exc:
        raise JobRefused(str(exc)) from exc
    spec = job["params"]
    before = snapshot_context(resolve, driver)
    restoration = {**DEFAULT_RESTORATION,
                   **(spec.get("restoration") or {})}
    executed: list = []
    target_resolutions: list = []
    verification: dict | None = None
    error: str | None = None
    purpose = f"ren-resolved resolve.ui_transaction {job['id']}"
    take = _take_foreground(driver, spec)
    try:
        _establish(resolve, spec, purpose=purpose)
        for action in spec.get("actions", []):
            _check_take_budget(take)
            resolution = _execute_action(
                driver, action, foreground=take is not None, resolve=resolve)
            _check_expect(driver, action)
            executed.append(action.get("action"))
            if resolution is not None:
                target_resolutions.append(resolution)
        verification = _run_verification(resolve, driver,
                                         spec.get("verification"))
        if (verification is not None and verification.get("kind") != "none"
                and not verification.get("matched")):
            raise JobRefused(
                f"verification failed: {verification.get('kind')} "
                f"{verification.get('text') or verification.get('getter')}"
                f" - expected {verification.get('expected')!r}, found "
                f"{verification.get('actual')!r}")
    except Exception as exc:                              # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
    restoration_result = restore_context(resolve, before, restoration,
                                         purpose=purpose)
    after = snapshot_context(resolve, driver)
    with contextlib.suppress(Exception):
        driver.close()
    take_record = None
    if take is not None:
        take_record = {
            "announced": take["announced"],
            "budget_seconds": take["budget_seconds"],
            "elapsed_seconds": round(time.monotonic() - take["started"], 3),
        }
    receipt = {
        "operation": "resolve.ui_transaction",
        "provider": "resolve.ui",
        "job_id": job["id"],
        "status": ("failed" if error else
                   "committed" if verification and verification.get("verified")
                   else "applied_unverified"),
        "observed_before": before,
        "observed_after": after,
        "actions": executed,
        "target_resolutions": target_resolutions,
        "verification": verification,
        "restored": restoration_result["restored"],
        "restoration": restoration_result,
        "restoration_status": (
            "complete" if not restoration_result["not_restored"] else "partial"),
        "recoverable_state_status": (
            "complete" if not any(
                field in restoration_result["not_restored"]
                for field in ("page", "timeline", "playhead"))
            else "partial"),
        "result": ({"error": error} if error else
                   {"committed": True} if verification and
                   verification.get("verified") else
                   {"committed": False,
                    "reason": "verification did not read actual edit state"}),
        "take": take_record,
    }
    _file_receipt(job, spec, receipt)
    return receipt


def _file_receipt(job: dict, spec: dict, receipt: dict) -> None:
    """File the receipt into Ren's records, where one is named.

    Two records, one receipt: the event log gains a `ui_transaction`
    event carrying the receipt's operation, provider, observed
    before/after, actions, verification and outcome - the UI path's
    answer to the API edit's `TIMELINE_OPERATIONS` event, and therefore
    a project-graph operation node and a `ren events` row with no
    further wiring - and, where the transaction names a `review_dir`
    and a timeline, the per-reel account `reel_edit_history` keeps one
    `ui_transaction` entry with the provider and the UI-action fields
    in its refs, linked to the job that ran it. Never fails the
    transaction - a receipt that cannot be filed is said on stderr and
    the job's own result still carries it.
    """
    review_dir = spec.get("review_dir") or ""
    if not review_dir:
        return
    _emit_receipt_event(str(review_dir), spec, receipt)
    final = spec.get("timeline") or ""
    if not final:
        return
    try:
        from library.tools import reel_edit_history as history

        history.try_record_ren_act(
            str(review_dir), final, act=history.ACT_UI_TRANSACTION,
            summary=(f"UI transaction on {final}: "
                     f"{receipt['status']} - "
                     f"{len(receipt['actions'])} action(s)"),
            refs={
                "provider": receipt["provider"],
                "job_id": receipt["job_id"],
                "actions": receipt["actions"],
                "observed_before": receipt["observed_before"],
                "observed_after": receipt["observed_after"],
                "verification": receipt["verification"],
                "restored": receipt["restored"],
                "restoration": receipt.get("restoration", {}),
                "restoration_status": receipt.get("restoration_status", "unknown"),
                "recoverable_state_status": receipt.get(
                    "recoverable_state_status", "unknown"),
                "target_resolutions": receipt.get("target_resolutions", []),
                "result": receipt["result"],
            },
            verification=receipt["verification"],
            decision_id="resolve.ui_transaction",
        )
    except Exception as exc:                              # noqa: BLE001
        import sys
        print(f"  UI transaction receipt unrecorded for {final}: {exc!r} - "
              f"the job's result still carries it", file=sys.stderr)


def _emit_receipt_event(review_dir: str, spec: dict, receipt: dict) -> None:
    """Emit one receipt event into the project's event log, or skip.

    The transaction is the act, the event log is the record of it: a
    failure to emit must never take down a transaction, the same rule
    the patch path (`edit_patch._emit_patch_event`) holds. A receipt
    filed without a project folder leaves no event, which is the
    honest answer - the log is per-project and the caller named none.
    The folder is the one owning `review_dir` (`reel_edit_history`
    already maps that layout); a failed transaction emits too, with
    its outcome naming the failure - a run that did not land is a fact
    the history must hold, not a silent no-op.
    """
    try:
        from library.tools import event_log
        from library.tools import reel_edit_history as history

        project_folder = history._project_folder_for(review_dir)
        event_log.emit(project_folder, event_log.UI_TRANSACTION,
                       job_id=receipt["job_id"],
                       timeline=str(spec.get("timeline") or ""),
                       operation=receipt["operation"],
                       provider=receipt["provider"],
                       status=receipt["status"],
                       observed_before=receipt["observed_before"],
                       observed_after=receipt["observed_after"],
                       actions=receipt["actions"],
                       target_resolutions=receipt.get("target_resolutions", []),
                       verification=receipt["verification"],
                       restored=receipt["restored"],
                       restoration=receipt.get("restoration", {}),
                       restoration_status=receipt.get("restoration_status", "unknown"),
                       recoverable_state_status=receipt.get(
                           "recoverable_state_status", "unknown"),
                       result=receipt["result"])
    except OSError:
        pass
