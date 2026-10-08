"""The recipe replay suite: one command runs every stored recipe end to end.

The captain's brief: tonight's live screen takes each ran once by hand
and mostly ended unproven - steps reported done without proving their
effect, activation raced, a menu matcher was wrong, a recipe searched
by text for an icon-only button, and the restore ran before the
result was read. This module is the standing answer: every recipe,
every run, each step with an expect that proves its effect, the
read-back taken BEFORE restore, and a per-recipe pass/fail report
with captures saved.

Three layers, smallest first:

1. Audit (no driver, no Resolve): every step carries an expect (a
   wait step's declared seconds counts - its duration is the proof)
   or is covered by a declared API proof; every anchor is general
   (`anchors.audit_anchors`). A step with neither is a named defect -
   never a silent pass.
2. Offline replay (fakes): `FakeDriver` + `FakeResolve` serve the
   whole route in-process - menu paths, AX finds, OCR labels,
   template matching against embedded crops, cycling captures, node
   counts - so the suite runs anywhere. The fakes prove the SUITE's
   plumbing (snapshots, comparisons, verdicts); the live run proves
   the UI.
3. Live replay (Ren Qualification only): each take enters `ui_take`
   and `qualification_project.borrowed()`; `resolve_lease` obtains one
   broker turn and holds the exclusive lease through action, readback,
   scratch cleanup and restore. Background recipes run any time;
   foreground recipes run as ONE batched take and only with the
   captain's window granted (`REN_REPLAY_CAPTAIN_WINDOW` `=granted`
   plus `--allow-foreground`). Scratch timelines are built per run and
   deleted after - nothing touches a timeline that was already there,
   and never the captain's projects.

A step record carries `proved` (`scripts._run_step`): `ok` with
`proved` False is done-means-accepted, and the recipe fails replay
until the step proves its effect - state read-back via the API
where possible (node counts, page, panels), else `pixels_changed`
on a named region. A menu press has no screen predicate (background
captures hold pre-switch pixels, mapped 2026-10-07), so menu steps
are covered by the caller's API read-back instead of a screen
expect - declared in the proof's `covers`, executed by the runner,
reported per step.
"""

from __future__ import annotations

import base64
import copy
import json
import os
import time
from contextlib import nullcontext
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Callable, Optional

from library.tools.computer_use import anchors as anchor_audit
from library.tools.computer_use.scripts import (
    Anchor,
    Expect,
    MenuClosureError,
    Script,
    run_script,
)

#: The captain-window grant: firstmate sets this when the captain
#: lends the screen. Foreground replays refuse without it - taking
#: the screen unasked is the one thing the suite never does.
CAPTAIN_WINDOW_ENV = "REN_REPLAY_CAPTAIN_WINDOW"
CAPTAIN_WINDOW_GRANTED = "granted"

#: Where the suite files its report and captures by default: per
#: machine, outside every checkout, beside the stored recipes.
REPLAY_OUT_ENV = "PIPELINE_REPLAY_DIR"


def _replay_sleep(seconds: float) -> None:
    """Wait in the live replay loop through a testable module-level seam."""
    time.sleep(seconds)


def default_replay_root() -> Path:
    """The per-machine replay output directory."""
    explicit = os.environ.get(REPLAY_OUT_ENV, "")
    if explicit:
        return Path(explicit).expanduser()
    from library.tools.shared_environment import vep_home

    return vep_home() / "recipe_replays"


def captain_window_granted() -> bool:
    """Did firstmate grant the captain's screen for foreground takes."""
    return os.environ.get(CAPTAIN_WINDOW_ENV, "") == CAPTAIN_WINDOW_GRANTED


# ── Fakes: the offline layer ──────────────────────────────────────────

def _png(color: tuple, embedded: Optional[tuple] = None,
         registry: tuple = ()) -> str:
    """One 128x128 PNG, base64 - a fake capture frame.

    `embedded` is `(template_path, x, y)`: a legacy crop pasted
    into the frame, so a file-path template anchor resolves
    offline the way it resolves live - against pixels holding the
    icon. `registry` names `templates.py` entries pasted the way
    the resolver sees them: rescaled from the entry's cut scale to
    the fake capture's 1.0, at fixed non-overlapping sites. The
    fake window is 128x128 points at the origin and the capture is
    1:1, so capture pixels ARE window points here - no scale to
    mismatch.
    """
    from PIL import Image

    frame = Image.new("RGB", (128, 128), color)
    if embedded is not None:
        path, at_x, at_y = embedded
        with Image.open(path) as crop:
            frame.paste(crop.convert("RGB"), (int(at_x), int(at_y)))
    for name in registry:
        site = _REGISTRY_SITES.get(name)
        if site is None:
            continue
        frame.paste(_registry_crop(name), site)
    buf = BytesIO()
    frame.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


#: Where each embedded registry crop lands in the fake frame - each
#: site fits the crop rescaled to scale 1.0, clear of the legacy
#: embed at (8, 8) and of each other. A site that overflowed the
#: frame would paste a clipped icon the resolver can never match
#: (measured: 0.23 where 0.8 is the bar).
_REGISTRY_SITES = {
    "magic-mask-search": (96, 88),
    "magic-mask-toolbar": (100, 8),
}


def _registry_crop(name: str):
    """The registry crop as the resolver matches it at scale 1.0."""
    from PIL import Image

    from library.tools.computer_use.templates import load_template

    pixels, entry = load_template(name)
    crop = Image.fromarray(pixels)
    if float(entry.cut_scale) != 1.0:
        factor = 1.0 / float(entry.cut_scale)
        crop = crop.resize(
            (max(1, int(crop.width * factor)),
             max(1, int(crop.height * factor))),
            Image.BILINEAR)
    return crop.convert("RGB")


#: The fake capture cycles base colors, so a `pixels_changed` expect
#: sees the change a real UI redraw produces.
FAKE_COLORS = ((20, 20, 20), (200, 30, 30), (30, 200, 30))

#: The fake main window, in window points - clicks outside it are
#: refused OUTSIDE_RESOLVE, like the real driver refuses them.
FAKE_FRAME = {"x": 0.0, "y": 0.0, "width": 128.0, "height": 128.0}


class FakeResolve:
    """The API half the recipes read back: page, panels, nodes, track.

    Menu presses advance it the way Resolve would: a page switch
    lands the page, a panel toggle opens the panel, Add Serial Node
    grows the count, Delete Current Node shrinks it, a Tracker press
    arms the tracker. Anything else leaves it alone - a fake that
    changed state on every press would prove nothing.
    """

    def __init__(self, initial_nodes: int = 1) -> None:
        self.page = "edit"
        self.panels: set = set()
        self.tracked = False
        self.pressed_routes: list = []
        self._nodes = initial_nodes

    def node_count(self, clip: str = "clip") -> int:
        """The clip's node count."""
        return self._nodes

    def press(self, path: list) -> None:
        """Advance fake Resolve state for one menu path."""
        self.pressed_routes.append(list(path))
        leaf = (path or [""])[-1]
        if path[:2] == ["Workspace", "Switch to Page"]:
            self.page = {"Color": "color", "Edit": "edit",
                         "Media": "media"}.get(leaf, self.page)
        elif path[:2] == ["Workspace", "Show Panel in Workspace"]:
            self.panels.add(leaf)
        elif leaf == "Add Serial Node" or leaf.startswith(
                "Add Serial Node +"):
            self._nodes += 1
        elif leaf == "Delete Current Node":
            self._nodes = max(1, self._nodes - 1)
        elif path[:2] == ["Color", "Tracker"]:
            self.tracked = True


class FakeDriver:
    """The driver half: menus, AX, OCR, captures, input events.

    Serves the calls `run_script` and `build_observation` make, from
    scripted state: `labels` are the OCR-visible strings,
    `embed_template` pastes a real template crop into every capture
    (so template anchors resolve), captures cycle so pixel expects
    verify. Clicks outside the window are refused, like live. Every
    call is recorded - the report shows what the recipe drove. Node
    menu presses advance the paired `FakeResolve`, so API proofs
    read back a change.
    """

    def __init__(self, labels: tuple = ("Scopes",),
                 resolve: Optional[FakeResolve] = None,
                 embed_template: str = "",
                 embed_registry: tuple = ()) -> None:
        self.labels = list(labels)
        self.resolve = resolve or FakeResolve()
        self.embed_template = embed_template
        self.embed_registry = tuple(embed_registry)
        self.calls: list = []
        self.typed: list = []
        self._captures = 0
        self.activated = False
        self._last_find: list = []

    def _frame(self) -> str:
        color = FAKE_COLORS[self._captures % len(FAKE_COLORS)]
        embedded = ((self.embed_template, 8, 8)
                    if self.embed_template else None)
        return _png(color, embedded, self.embed_registry)

    def call(self, method: str, params: dict | None = None):
        """Serve one driver call, and record it."""
        params = dict(params or {})
        self.calls.append({"method": method, "params": copy.deepcopy(params)})
        if method == "get_windows":
            return {"windows": [{
                "id": 1, "title": "Ren Qualification",
                "frame": dict(FAKE_FRAME),
                "backing_scale": 2.0, "is_main": True}]}
        if method == "get_frontmost_app":
            return {"bundle_id": "com.blackmagic-design.Resolve",
                    "name": "DaVinci Resolve"}
        if method == "get_accessibility_tree":
            return {"tree": {"ref": "ax-root", "role": "AXApplication",
                             "title": "DaVinci Resolve", "children": []}}
        if method == "find_element":
            path = list(params.get("path") or [])
            title = params.get("title")
            if title:
                path = path + [title]
            self._last_find = path
            return {"elements": [{"ref": "ax-1",
                                  "title": (path or ["?"])[-1]}]}
        if method == "run_command":
            path = params.get("path") or []
            if not path:
                raise _driver_error("INVALID_PARAMS", "no path")
            self.resolve.press(list(path))
            return {"pressed": True, "path": list(path)}
        if method == "press":
            # An AX press on a menu item drives the same state the
            # server-side menu command drives live: the find recorded
            # the path, the press acts on it.
            if self._last_find:
                self.resolve.press(list(self._last_find))
                self._last_find = []
            return {"pressed": True}
        if method == "set_value":
            return {"set": True, "value": params.get("value")}
        if method == "click":
            x, y = params.get("x"), params.get("y")
            frame = FAKE_FRAME
            if (x is None or y is None
                    or not (frame["x"] <= x <= frame["x"] + frame["width"]
                            and frame["y"] <= y
                            <= frame["y"] + frame["height"])):
                raise _driver_error(
                    "OUTSIDE_RESOLVE",
                    f"click at ({x}, {y}) is outside the main window")
            return {"clicked": True, "at": [x, y]}
        if method == "key":
            if not params.get("key"):
                raise _driver_error("INVALID_PARAMS", "key needs a key name")
            return {"sent": True, "key": params.get("key")}
        if method == "type_text":
            if not params.get("text"):
                raise _driver_error("INVALID_PARAMS", "type_text needs text")
            self.typed.append(params["text"])
            return {"typed": len(params["text"])}
        if method == "capture_window":
            self._captures += 1
            return {"image": self._frame(), "width": 128.0, "height": 128.0,
                    "scale": 1.0}
        if method == "read_text":
            # Frames tile from (8, 8) in 12pt columns: every label's
            # center - and the +55/+5 value field beside one - lands
            # inside the fake 128x128 window, like live frames land
            # inside the live window.
            return {"elements": [
                {"text": label, "confidence": 0.99,
                 "frame": {"x": 8.0 + i * 12.0, "y": 8.0,
                           "width": 10.0, "height": 10.0}}
                for i, label in enumerate(self.labels)]}
        if method == "wait_for":
            predicate = params.get("predicate") or {}
            kind = predicate.get("type")
            if kind == "seconds":
                return {"met": True, "elapsed": 0.0}
            if kind == "text_visible":
                return {"met": predicate.get("text") in self.labels,
                        "elapsed": 0.0}
            if kind == "element_visible":
                return {"met": True, "elapsed": 0.0}
            raise _driver_error("INVALID_PARAMS", f"bad predicate {kind}")
        if method == "activate_resolve":
            self.activated = True
            return {"activated": True}
        raise _driver_error("INVALID_PARAMS", f"unknown method {method}")

    def close(self) -> None:
        """The client protocol's teardown; the fake holds nothing."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _driver_error(code: str, message: str):
    """A driver refusal shaped like the real client's."""
    from library.tools.computer_use.client import DriverError

    return DriverError(code, message)


# ── Registry: every recipe, one row ───────────────────────────────────

@dataclass
class ApiProof:
    """A state read-back that proves a recipe's effect.

    `read` takes the replay world (`{"resolve", "driver"}` - fake or
    live) and returns the value; `wanted` is the predicate the
    read-back must satisfy - `{"delta": 1}` against the before
    snapshot, or `{"equals": x}`. The runner snapshots before the
    first step and reads back after the last, BEFORE restore - a
    restore that runs first would read the captain's project, not
    the result. `covers` names the step indices this proof proves:
    a menu press has no screen predicate, so its proof lives here,
    declared and reported per step.
    """

    name: str
    read: Callable[[dict], object] = field(repr=False)
    wanted: dict = field(default_factory=dict)
    covers: tuple = ()

    def check(self, before, after) -> tuple[bool, str]:
        """Judge the after snapshot against the before one."""
        if "delta" in self.wanted:
            try:
                got = float(after) - float(before)
            except (TypeError, ValueError):
                return False, f"{self.name} unreadable: {before!r} -> {after!r}"
            if got == float(self.wanted["delta"]):
                return True, f"{self.name} moved {before!r} -> {after!r}"
            return False, (f"{self.name} moved {before!r} -> {after!r}, "
                           f"wanted delta {self.wanted['delta']}")
        if "equals" in self.wanted:
            if after == self.wanted["equals"]:
                return True, f"{self.name} reads {after!r}"
            return False, (f"{self.name} reads {after!r}, "
                           f"wanted {self.wanted['equals']!r}")
        if "contains" in self.wanted:
            if self.wanted["contains"] in (after or []):
                return True, f"{self.name} holds {after!r}"
            return False, (f"{self.name} holds {after!r}, "
                           f"missing {self.wanted['contains']!r}")
        return False, f"{self.name} declares no predicate"


@dataclass
class RecipeSpec:
    """One replayable recipe: how to build it, run it, prove it.

    `fake_labels` are the OCR strings the fakes serve (the labels
    the recipe's expects name); `fake_nodes` is the fake clip's
    starting node count; `fake_template` pastes a real legacy crop
    into fake captures; `fake_registry` pastes `templates.py`
    registry crops the way the resolver matches them.
    """

    name: str
    background: bool
    goal: str
    build: Callable[[], Script] = field(repr=False)
    proofs: list[ApiProof] = field(default_factory=list)
    proof_kind: str = "api"
    fake_labels: tuple = ("Scopes",)
    fake_nodes: int = 1
    fake_template: str = ""
    fake_registry: tuple = ()


def recipe_registry() -> list[RecipeSpec]:
    """Every stored recipe the suite replays, background first.

    Builders import lazily: the registry must list recipes on a
    machine without Resolve, cv2 or a template file - a builder
    that needs the live UI raises at BUILD time, and the audit
    records that instead of the import failing.
    """
    from library.tools.computer_use import scripts as scripts_mod

    def _grade_add() -> Script:
        return scripts_mod.grade_node_add_script()

    def _grade_remove() -> Script:
        return scripts_mod.grade_node_remove_script(2, 3)

    def _effect_panels() -> Script:
        return scripts_mod.effect_panels_script()

    def _mask_seed() -> Script:
        from library.tools import magic_mask
        from library.tools.mask_seed import MaskSeed, Point

        # Viewer (80, 40) at scale 2.0 clicks window point (40, 20):
        # inside the fake 128x128 window, like a live seed clicks
        # inside the live window.
        seed = MaskSeed(clip_id="qual_b", frame=48, subject="person",
                        source=Point(x=320.0, y=180.0),
                        viewer=Point(x=80.0, y=40.0),
                        object_id="q1", sample_index=0, seed_frame=48)
        return magic_mask.seed_script(
            seed, mode="BI", scale_factor=2.0,
            source_frame_range=magic_mask.SourceFrameRange(0, 119))

    def _mask_track() -> Script:
        from library.tools import magic_mask

        return magic_mask.track_script(
            "forward", magic_mask.SourceFrameRange(0, 119))

    def _window_create() -> Script:
        from library.tools.computer_use import window_recipe

        return window_recipe.build_window_create_script({
            "shape": "circle", "node_index": 2, "unique_id": "qual_b"})

    def _window_delete() -> Script:
        from library.tools.computer_use import window_delete

        return window_delete.build_window_delete_script()

    def _window_modify() -> Script:
        from library.tools.computer_use import window_modify

        return window_modify.build_window_modify_script({"size": "1.00"})

    def _window_track() -> Script:
        from library.tools.computer_use import window_track

        return window_track.build_window_track_script("forward")

    def _qualifier_sample() -> Script:
        from library.tools.computer_use import qualifier_recipe

        return qualifier_recipe.build_qualifier_sample_script(
            qualifier_recipe.eyedropper_template_path(), (32.0, 16.0))

    def _intellisearch() -> Script:
        return Script(
            goal="open the Media page for an IntelliSearch collection",
            steps=[scripts_mod.ScriptStep(
                action="press",
                anchors=[Anchor(via="ax_path", params={
                    "path": ["Workspace", "Switch to Page", "Media"]})],
                expect=Expect(via="text_visible",
                              params={"text": "Master"}),
            )],
        )

    palette = ("Scopes", "Master", "Person", "Magic Mask", "Track Forward",
               "Size")
    return [
        RecipeSpec(
            name="grade-node-add", background=True,
            goal="add one serial node on the Color page",
            build=_grade_add,
            proofs=[
                ApiProof(name="page",
                         read=lambda world: world["resolve"].page,
                         wanted={"equals": "color"}, covers=(0,)),
                ApiProof(name="nodes",
                         read=lambda world: world["resolve"].node_count(),
                         wanted={"delta": 1}, covers=(1,)),
            ],
            proof_kind="api"),
        RecipeSpec(
            name="grade-node-remove", background=True,
            goal="remove node 2 of 3 on the Color page",
            build=_grade_remove,
            proofs=[
                ApiProof(name="page",
                         read=lambda world: world["resolve"].page,
                         wanted={"equals": "color"}, covers=(0,)),
                ApiProof(name="nodes",
                         read=lambda world: world["resolve"].node_count(),
                         wanted={"equals": 2}, covers=(2, 4, 6, 8, 10, 12)),
            ],
            proof_kind="api", fake_nodes=3),
        RecipeSpec(
            name="effect-panels", background=True,
            goal="open the Effects Library and Inspector panels",
            build=_effect_panels,
            proofs=[
                ApiProof(name="page",
                         read=lambda world: world["resolve"].page,
                         wanted={"equals": "edit"}, covers=(0,)),
                ApiProof(name="panels",
                         read=lambda world: sorted(
                             world["resolve"].panels),
                         wanted={"contains": "Effects"}, covers=(1,)),
                ApiProof(name="inspector",
                         read=lambda world: sorted(
                             world["resolve"].panels),
                         wanted={"contains": "Inspector"}, covers=(2,)),
            ],
            proof_kind="api"),
        RecipeSpec(
            name="window-create", background=True,
            goal="create a circle window on node 2",
            build=_window_create,
            proofs=[ApiProof(
                name="nodes",
                read=lambda world: world["resolve"].node_count(),
                wanted={"delta": 1}, covers=(0,))],
            proof_kind="api"),
        RecipeSpec(
            name="window-delete", background=True,
            goal="delete the current node",
            build=_window_delete,
            proofs=[ApiProof(
                name="nodes",
                read=lambda world: world["resolve"].node_count(),
                wanted={"delta": -1}, covers=(0,))],
            proof_kind="api", fake_nodes=2),
        RecipeSpec(
            name="window-track", background=True,
            goal="track the window forward",
            build=_window_track,
            proofs=[ApiProof(
                name="tracker",
                read=lambda world: world["resolve"].tracked,
                wanted={"equals": True}, covers=(0,))],
            proof_kind="stills"),
        RecipeSpec(
            name="intellisearch-open", background=True,
            goal="open the Media page for collection",
            build=_intellisearch,
            proofs=[ApiProof(
                name="page",
                read=lambda world: world["resolve"].page,
                wanted={"equals": "media"}, covers=(0,))],
            proof_kind="api", fake_labels=("Scopes", "Master")),
        RecipeSpec(
            name="magic-mask-seed", background=False,
            goal="seed the Magic Mask on the subject",
            build=_mask_seed, proofs=[], proof_kind="pixels",
            fake_labels=palette,
            fake_registry=("magic-mask-search", "magic-mask-toolbar")),
        RecipeSpec(
            name="magic-mask-track", background=False,
            goal="track the Magic Mask forward",
            build=_mask_track,
            proofs=[ApiProof(
                name="route",
                read=lambda world: sum(
                    1 for call in world["driver"].calls
                    if call["method"] == "click"),
                wanted={"delta": 1}, covers=(0,))],
            proof_kind="plumbing",
            fake_labels=("Scopes", "Track Forward")),
        RecipeSpec(
            name="window-modify", background=False,
            goal="set the window panel Size field",
            build=_window_modify, proofs=[], proof_kind="pixels",
            fake_labels=("Scopes", "Size")),
        RecipeSpec(
            name="qualifier-sample", background=False,
            goal="sample the qualifier at the viewer point",
            build=_qualifier_sample, proofs=[], proof_kind="pixels",
            fake_labels=("Scopes",),
            fake_template="qualifier-eydropper"),
    ]


# ── Audit: the static layer ───────────────────────────────────────────

@dataclass
class RecipeAudit:
    """What the static layer found: expects, anchors, verdict."""

    name: str
    steps: int
    missing_expects: list[int] = field(default_factory=list)
    anchor_findings: list = field(default_factory=list)
    build_error: str = ""

    @property
    def passed(self) -> bool:
        """No missing expect, no anchor defect, and it built."""
        return (not self.build_error and not self.missing_expects
                and not self.anchor_findings)


def _covered(spec: RecipeSpec, index: int) -> bool:
    """Does a declared proof cover this step's effect."""
    return any(index in proof.covers for proof in spec.proofs)


def audit_recipe(spec: RecipeSpec) -> tuple[RecipeAudit, Optional[Script]]:
    """Audit one recipe without touching a driver or Resolve.

    A wait step's declared seconds counts as its expect; a menu
    step covered by a declared API proof counts through the proof.
    Everything else without an expect is a named defect. Anchor
    generality comes from `anchors.audit_anchors`. A builder that
    needs the live UI records its build error instead of raising.
    """
    audit = RecipeAudit(name=spec.name, steps=0)
    try:
        script = spec.build()
    except Exception as exc:  # noqa: BLE001 - the report names it
        audit.build_error = f"{type(exc).__name__}: {exc}"
        return audit, None
    audit.steps = len(script.steps)
    for index, step in enumerate(script.steps):
        if (step.expect is None and step.action != "wait"
                and not _covered(spec, index)):
            audit.missing_expects.append(index)
    audit.anchor_findings = anchor_audit.audit_anchors(script)
    return audit, script


# ── Offline replay: the fakes layer ───────────────────────────────────

@dataclass
class RecipeReport:
    """One recipe's replay: steps, proofs, captures, verdict."""

    name: str
    background: bool
    audit: RecipeAudit
    steps: list[dict] = field(default_factory=list)
    proofs: list[dict] = field(default_factory=list)
    captures: list[str] = field(default_factory=list)
    verdict: str = "not-run"
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "background": self.background,
            "verdict": self.verdict,
            "reason": self.reason,
            "audit": {
                "steps": self.audit.steps,
                "missing_expects": self.audit.missing_expects,
                "anchor_findings": [
                    {"step": finding.step, "via": finding.via,
                     "defect": finding.defect, "fix": finding.fix}
                    for finding in self.audit.anchor_findings],
                "build_error": self.audit.build_error,
                "passed": self.audit.passed,
            },
            "steps": self.steps,
            "proofs": self.proofs,
            "captures": self.captures,
        }


def _resolve_fake_template(spec: RecipeSpec) -> str:
    """The real crop the fakes embed, or "" where the recipe needs none.

    `qualifier-eydropper` names the qualifier's measured eyedropper
    crop (`qualifier_recipe.eyedropper_template_path`): embedding
    the real crop means the template anchor resolves offline the
    way it resolves live - against pixels holding the icon.
    """
    if spec.fake_template != "qualifier-eydropper":
        return ""
    from library.tools.computer_use import qualifier_recipe

    path = qualifier_recipe.eyedropper_template_path()
    return path if Path(path).exists() else ""


def replay_offline(spec: RecipeSpec, out_dir: Path) -> RecipeReport:
    """Replay one recipe end to end against the fakes.

    The fake driver serves the labels the recipe's expects name and
    embeds the template crop where one is declared; the fake
    Resolve advances node counts, pages, panels and the tracker
    under menu presses. API proofs snapshot before the first step
    and read back after the last - before any (fake) restore. A
    step is proved by its own expect or by a covering proof that
    passed. Captures go to the out dir.
    """
    audit, script = audit_recipe(spec)
    report = RecipeReport(name=spec.name, background=spec.background,
                          audit=audit)
    if script is None:
        report.verdict = "fail"
        report.reason = f"build failed: {audit.build_error}"
        return report
    if not audit.passed:
        report.verdict = "fail"
        report.reason = _audit_reason(audit)
        return report
    fake_resolve = FakeResolve(initial_nodes=spec.fake_nodes)
    driver = FakeDriver(labels=spec.fake_labels, resolve=fake_resolve,
                        embed_template=_resolve_fake_template(spec),
                        embed_registry=spec.fake_registry)
    world = {"resolve": fake_resolve, "driver": driver}
    before = {proof.name: proof.read(world) for proof in spec.proofs}
    try:
        result = run_script(
            driver, script,
            lease_factory=lambda _purpose: nullcontext())
    except Exception as exc:  # noqa: BLE001 - the report names it
        report.verdict = "fail"
        report.reason = f"runner raised {type(exc).__name__}: {exc}"
        return report
    report.steps = result.steps
    if result.outcome != "done":
        report.verdict = "fail"
        report.reason = f"runner ended {result.outcome}"
        return report
    proof_by_name: dict = {}
    for proof in spec.proofs:
        after = proof.read(world)
        ok, detail = proof.check(before[proof.name], after)
        proof_by_name[proof.name] = ok
        report.proofs.append({"name": proof.name, "passed": ok,
                              "detail": detail,
                              "covers": list(proof.covers)})

    def _proved(index: int, entry: dict) -> bool:
        if entry.get("proved"):
            return True
        return any(index in proof.covers
                   and proof_by_name.get(proof.name, False)
                   for proof in spec.proofs)

    unproved = [entry["step"] for entry in result.steps
                if not _proved(entry["step"], entry)]
    failed_proofs = [proof for proof in report.proofs
                     if not proof["passed"]]
    if unproved or failed_proofs:
        report.verdict = "fail"
        bits = []
        if unproved:
            bits.append(f"steps {unproved} report done without "
                        f"proving their effect")
        bits.extend(proof["detail"] for proof in failed_proofs)
        report.reason = "; ".join(bits)
        return report
    shot = driver.call("capture_window")
    capture_path = out_dir / f"{spec.name}-final.png"
    capture_path.parent.mkdir(parents=True, exist_ok=True)
    capture_path.write_bytes(base64.b64decode(shot["image"]))
    report.captures = [str(capture_path)]
    report.verdict = "pass"
    report.reason = (f"{len(report.steps)} steps proved, "
                     f"{len(report.proofs)} api proofs read back")
    return report


def _audit_reason(audit: RecipeAudit) -> str:
    """The one-line reason an audit failure reports."""
    bits = []
    if audit.missing_expects:
        bits.append(f"steps {audit.missing_expects} declare no expect")
    for finding in audit.anchor_findings:
        bits.append(f"step {finding.step} {finding.via}: {finding.defect}")
    return "; ".join(bits) or "audit failed"


# ── Live replay: Ren Qualification only ───────────────────────────────

def _live_guard(spec: RecipeSpec, allow_foreground: bool) -> str | None:
    """Why this recipe may not run live now, or None where it may.

    Foreground recipes need BOTH the flag and the grant: the flag
    says the run accepts taking the screen, the grant says firstmate
    arranged it with the captain. Background recipes run any time -
    but never on a locked screen the driver itself would refuse.
    """
    if not spec.background and not (allow_foreground
                                    and captain_window_granted()):
        if not allow_foreground:
            return ("foreground recipe needs --allow-foreground: it takes "
                    "the captain's screen")
        return (f"foreground recipe needs the captain's window: "
                f"{CAPTAIN_WINDOW_ENV}={CAPTAIN_WINDOW_GRANTED} "
                f"(firstmate grants it)")
    return None


def _scratch_timeline(project, pool, stem: str):
    """A scratch timeline holding one qual_b clip, or a refusal.

    Named per run and deleted after: nothing touches a timeline
    that was already there, and never the captain's projects (the
    caller runs inside `borrowed()` on Ren Qualification).
    """
    import uuid as _uuid

    from library.tools.qualification_project import QUALIFICATION_PROJECT

    if project.GetName() != QUALIFICATION_PROJECT:
        raise RuntimeError(
            f"live replay runs on {QUALIFICATION_PROJECT!r}, "
            f"not {project.GetName()!r}")
    clip = None
    for item in project.GetMediaPool().GetRootFolder().GetClipList() or []:
        name = item.GetName() or ""
        if name == "qual_b.mp4" or name.rsplit(".", 1)[0] == "qual_b":
            clip = item
            break
    if clip is None:
        raise RuntimeError("no qual_b in the pool - reset first: "
                           "ren qualification reset")
    name = f"Q Replay {stem} " + _uuid.uuid4().hex[:8]
    timeline = pool.CreateEmptyTimeline(name)
    if timeline is None:
        raise RuntimeError(f"could not create scratch timeline {name!r}")
    from library.tools.resolve_lock import assert_current_timeline

    assert_current_timeline(project, timeline)
    if not pool.AppendToTimeline([clip]):
        raise RuntimeError(f"could not place qual_b on {name!r}")
    items = timeline.GetItemListInTrack("video", 1) or []
    if len(items) != 1:
        raise RuntimeError(f"scratch {name!r} holds {len(items)} clips")
    return timeline, items[0]


def _delete_scratch(pool, timeline) -> None:
    """Delete the scratch timeline; never raises past the report."""
    try:
        pool.DeleteTimelines([timeline])
    except Exception:  # noqa: BLE001 - the report names the replay
        pass


def _live_node_count(item) -> int | None:
    """The clip's Color-page node count, or None where unreadable."""
    try:
        count = int(item.GetNumNodes())
    except Exception:  # noqa: BLE001 - unreadable is a refusal
        return None
    return count if not isinstance(count, bool) else None


#: Background recipes whose press acts on the Color page: the menu
#: press is accepted on any page but moves no node anywhere else.
#: Measured live 2026-10-07: the replay pressed
#: `Color > Nodes > Add Serial Node + CPW` and `Delete Current Node`
#: against the Edit page - `ok` via ax_path, nodes 1 -> 1. The
#: established runners (`run_window_create`, `run_window_delete`,
#: `run_window_track`) put the playhead on the clip, open the Color
#: page and read its Scopes label back before pressing; the live runner
#: below does the same, or it refuses with what read back.
COLOR_PAGE_SPECS = ("grade-node-remove", "window-create",
                    "window-delete", "window-track")


def _live_establish_color_page(resolve, timeline, item, client) -> str | None:
    """Put the scratch clip on the Color page; None on success.

    A reason string on any miss: the playhead did not land on the
    clip, the Color page did not open, or no Scopes label read back
    (the API switch alone does not repaint the page in background).
    The caller fails the recipe with the reason instead of pressing
    against the wrong page and claiming blind.
    """
    from library.tools.qualification_project import FPS

    try:
        uid = item.GetUniqueId()
    except Exception:  # noqa: BLE001 - unreadable is a refusal
        return "the scratch clip has no readable unique id"
    try:
        start = int(item.GetStart())
        mid = start + int(item.GetDuration()) // 2
        fps = int(FPS)
        ff = mid % fps
        ss = (mid // fps) % 60
        mm = (mid // (fps * 60)) % 60
        hh = mid // (fps * 3600)
        timeline.SetCurrentTimecode(f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}")
        current = timeline.GetCurrentVideoItem()
        current_uid = current.GetUniqueId() if current else None
    except Exception:  # noqa: BLE001 - unestablished is a refusal
        return "the playhead did not land on the scratch clip"
    if current_uid != uid:
        return (f"the playhead reads current clip {current_uid!r}, "
                f"not the scratch clip")
    try:
        opened = resolve.OpenPage("color")
    except Exception:  # noqa: BLE001 - a dead call is a refusal
        opened = False
    if not opened:
        return "the Color page did not open"
    labels: set = set()
    for _attempt in range(3):
        _replay_sleep(4)
        try:
            labels = {e.get("text", "") for e in
                      client.call("read_text").get("elements", [])}
        except Exception:  # noqa: BLE001 - retry covers it
            labels = set()
        if "Scopes" in labels:
            break
    if "Scopes" not in labels:
        return ("reads no Color page (no Scopes label) - the menu "
                "would press against the wrong page")
    return None


def _live_ensure_node_count(item, client, target: int,
                            recipe_name: str) -> str | None:
    """Grow a scratch clip to exactly ``target`` nodes; None on success.

    The delete menu removes the current node and no API reads which
    that is - only the recipe's declared starting count is
    deterministic, and deleting the last node of one is refused
    upstream. A fresh scratch holds one, so the replay adds serials
    one at a time, verifies each menu closure and node-count read-back,
    and refuses an unexpected starting count or overshoot.
    """
    before = _live_node_count(item)
    if before is None:
        return "the scratch clip's node count did not read back"
    if before > target:
        return (f"the scratch clip has {before} nodes, above the "
                f"{target}-node setup for {recipe_name}")
    if before == target:
        return None
    while before < target:
        try:
            receipt = client.call(
                "run_command",
                {"path": ["Color", "Nodes", "Add Serial Node"]})
            if (not isinstance(receipt, dict)
                    or receipt.get("menus_closed") is not True):
                raise MenuClosureError(
                    f"{recipe_name} setup serial menu closure was not "
                    f"verified: {receipt!r}")
        except Exception as exc:  # noqa: BLE001 - the reason names it
            return (f"the {recipe_name} setup serial did not press: "
                    f"{type(exc).__name__}: {exc}")
        after = _live_node_count(item)
        for _poll in range(5):
            if after == before + 1:
                break
            if after is None or after > before + 1:
                break
            _replay_sleep(3)
            after = _live_node_count(item)
        if after != before + 1:
            return (f"the {recipe_name} setup serial read "
                    f"{before} -> {after}, expected exactly "
                    f"{before + 1} nodes")
        before = after
    return None


def _live_ensure_two_nodes(item, client) -> str | None:
    """Compatibility wrapper for the delete recipe's two-node setup."""
    return _live_ensure_node_count(item, client, 2, "window-delete")


def _activate_resolve_for_live_take(client) -> None:
    """Run the driver's NSWorkspace activation path and require its receipt.

    Qt AX menu presses can be accepted without dispatching while
    Resolve is inactive. Live takes activate once after borrowing the
    qualification project and before any UI action.
    """
    receipt = client.call("activate_resolve", {"allow_foreground": True})
    if not isinstance(receipt, dict) or receipt.get("activated") is not True:
        raise RuntimeError(f"Resolve activation did not read back: {receipt!r}")


def _live_run_background(spec: RecipeSpec, take, out: Path) -> RecipeReport:
    """Replay one background recipe live: establish, act, read back.

    The read-back (node counts, page, captures) runs BEFORE the
    scratch timeline is deleted and BEFORE the take restores - a
    result read after restore reads the captain's project, not
    the replay. Every miss is a refusal with what read back.
    """
    from library.tools.computer_use.client import DriverClient
    from library.tools.computer_use.scripts import run_script

    audit, script = audit_recipe(spec)
    report = RecipeReport(name=spec.name, background=True, audit=audit)
    if script is None or not audit.passed:
        report.verdict = "fail"
        report.reason = audit.build_error or _audit_reason(audit)
        return report
    resolve = take.resolve
    manager = resolve.GetProjectManager()
    from library.tools.qualification_project import switch_project

    project = switch_project(manager, "Ren Qualification")
    pool = project.GetMediaPool()
    timeline, item = _scratch_timeline(project, pool, spec.name)
    try:
        from library.tools.resolve_lock import assert_current_timeline

        assert_current_timeline(project, timeline)
        with DriverClient() as client:
            if spec.name in COLOR_PAGE_SPECS:
                reason = _live_establish_color_page(
                    resolve, timeline, item, client)
                if reason is not None:
                    report.verdict = "fail"
                    report.reason = f"{spec.name}: {reason}"
                    _capture_live(client, spec, out, report, "failed")
                    return report
            if spec.name == "window-delete":
                reason = _live_ensure_two_nodes(item, client)
                if reason is not None:
                    report.verdict = "fail"
                    report.reason = f"{spec.name}: {reason}"
                    _capture_live(client, spec, out, report, "failed")
                    return report
            elif spec.name == "grade-node-remove":
                reason = _live_ensure_node_count(item, client, 3,
                                                 spec.name)
                if reason is not None:
                    report.verdict = "fail"
                    report.reason = f"{spec.name}: {reason}"
                    _capture_live(client, spec, out, report, "failed")
                    return report
            before_nodes = _live_node_count(item)
            before_page = resolve.GetCurrentPage()
            try:
                result = run_script(client, script, deadline=take.deadline)
            except Exception:
                # Preserve the screen before borrowed() restores the
                # captain's project. In particular, an unverified menu
                # close must leave evidence in the per-recipe report.
                _capture_live(client, spec, out, report, "failed")
                raise
            report.steps = result.steps
            unverified_menu = next(
                (entry for entry in report.steps
                 if entry.get("menus_closed") is False), None)
            after_nodes = _live_node_count(item)
            if spec.name in ("grade-node-remove", "window-create",
                             "window-delete"):
                # Graph edits can land asynchronously; poll before
                # refusing an unchanged immediate read-back.
                for _poll in range(5):
                    if _live_nodes_ok(spec.name, before_nodes,
                                      after_nodes):
                        break
                    _replay_sleep(3)
                    after_nodes = _live_node_count(item)
            after_page = resolve.GetCurrentPage()
            report.proofs = [
                {"name": "nodes",
                 "passed": _live_nodes_ok(spec.name, before_nodes,
                                          after_nodes),
                 "detail": f"nodes {before_nodes} -> {after_nodes}"},
                {"name": "page",
                 "passed": (spec.name not in COLOR_PAGE_SPECS
                            or after_page == "color"),
                 "detail": f"page {before_page} -> {after_page}"},
            ]
            unproved = [entry["step"] for entry in report.steps
                        if not entry.get("proved")
                        and not _covered(spec, entry["step"])]
            if unverified_menu is not None:
                report.verdict = "fail"
                report.reason = unverified_menu.get(
                    "error", "MenuClosureError: menu close was not verified")
            elif result.outcome != "done":
                report.verdict = "fail"
                report.reason = f"runner ended {result.outcome}"
            elif unproved:
                report.verdict = "fail"
                report.reason = (f"steps {unproved} report done without "
                                 f"proving their effect")
            elif any(not proof["passed"] for proof in report.proofs):
                report.verdict = "fail"
                report.reason = "; ".join(
                    proof["detail"] for proof in report.proofs
                    if not proof["passed"])
            else:
                report.verdict = "pass"
                report.reason = (f"{len(report.steps)} steps proved live, "
                                 f"read back before restore")
            _capture_live(client, spec, out, report,
                          "passed" if report.verdict == "pass" else "failed")
    except Exception as exc:  # noqa: BLE001 - the report names it
        report.verdict = "fail"
        report.reason = f"{type(exc).__name__}: {exc}"
    finally:
        _delete_scratch(pool, timeline)
    return report


def _capture_live(client, spec: RecipeSpec, out: Path,
                  report: RecipeReport, outcome: str) -> None:
    """Save the screen evidence before take restoration, when available."""
    import base64 as _b64

    try:
        shot = client.call("capture_window")
        capture_path = out / f"{spec.name}-live-{outcome}.png"
        capture_path.parent.mkdir(parents=True, exist_ok=True)
        capture_path.write_bytes(_b64.b64decode(shot["image"]))
        report.captures.append(str(capture_path))
    except Exception:  # noqa: BLE001 - capture failure never hides the recipe verdict
        pass


def _live_nodes_ok(name: str, before, after) -> bool:
    """The node-count read-back for the recipes that move it."""
    if name in ("grade-node-add", "window-create"):
        return (before is not None and after is not None
                and after == before + 1)
    if name == "window-delete":
        return (before is not None and after is not None
                and after == before - 1)
    if name == "grade-node-remove":
        return before == 3 and after == 2
    return True


def _live_run_foreground_batch(specs: list[RecipeSpec], take,
                               out: Path) -> list[RecipeReport]:
    """Replay the foreground batch in ONE take: establish once, act all.

    One scratch timeline, one lease, one take bound: the batch reads
    every result back before the scratch is deleted and before the
    take restores. Each recipe runs through its own established
    runner (`run_window_create`, `run_qualifier_sample`, ...), which
    refuses with what read back instead of claiming blind.
    """
    import base64 as _b64

    from library.tools.computer_use.client import DriverClient
    from library.tools.qualification_project import switch_project

    reports = []
    if not specs:
        return reports
    resolve = take.resolve
    project = switch_project(resolve.GetProjectManager(),
                             "Ren Qualification")
    pool = project.GetMediaPool()
    timeline, item = _scratch_timeline(project, pool, "foreground-batch")
    try:
        from library.tools.resolve_lock import assert_current_timeline

        assert_current_timeline(project, timeline)
        uid = item.GetUniqueId()
        with DriverClient() as client:
            activation_error = None
            try:
                _activate_resolve_for_live_take(client)
            except Exception as exc:  # noqa: BLE001 - report each refused recipe
                activation_error = f"{type(exc).__name__}: {exc}"
            for index, spec in enumerate(specs):
                audit, _ = audit_recipe(spec)
                report = RecipeReport(name=spec.name, background=False,
                                      audit=audit)
                menu_unverified = False
                try:
                    if activation_error is not None:
                        raise RuntimeError(activation_error)
                    receipt = _foreground_runner(spec, client, resolve,
                                                 project, timeline, item,
                                                 uid)
                    report.steps = [{"step": 0, "action": spec.name,
                                     "outcome": "ok", "via": "recipe",
                                     "proved": True}]
                    report.proofs = [{"name": "receipt",
                                      "passed": True,
                                      "detail": json.dumps(receipt,
                                                           sort_keys=True,
                                                           default=str)}]
                    try:
                        shot = client.call("capture_window")
                        capture_path = out / f"{spec.name}-live.png"
                        capture_path.write_bytes(
                            _b64.b64decode(shot["image"]))
                        report.captures = [str(capture_path)]
                    except Exception:  # noqa: BLE001 - never fails a run
                        pass
                    report.verdict = "pass"
                    report.reason = "receipt read back before restore"
                except Exception as exc:  # noqa: BLE001 - named in report
                    report.verdict = "fail"
                    report.reason = f"{type(exc).__name__}: {exc}"
                    menu_unverified = isinstance(exc, MenuClosureError)
                reports.append(report)
                if menu_unverified:
                    reports.extend(_not_run_live_report(
                        remaining,
                        "not run: foreground batch stopped because the "
                        f"menu after {spec.name} was not verified closed")
                                   for remaining in specs[index + 1:])
                    break
    finally:
        _delete_scratch(pool, timeline)
    return reports


def _foreground_runner(spec: RecipeSpec, client, resolve, project,
                       timeline, item, uid: str) -> dict:
    """One foreground recipe's established runner, by name."""
    if spec.name == "window-create":
        from library.tools.computer_use.window_recipe import run_window_create

        before = _live_node_count(item)
        receipt = run_window_create(client, resolve, {
            "shape": "circle", "node_index": (before or 1) + 1,
            "unique_id": uid, "timeline": timeline.GetName(),
            "foreground": True})
        return receipt
    if spec.name == "window-modify":
        from library.tools.computer_use.window_modify import run_window_modify

        return run_window_modify(client, resolve, {
            "unique_id": uid, "values": {"size": "1.00"},
            "timeline": timeline.GetName(), "foreground": True})
    if spec.name == "qualifier-sample":
        from library.tools.computer_use import qualifier_recipe

        return qualifier_recipe.run_qualifier_sample(client, resolve, {
            "unique_id": uid, "point": [0.5, 0.5],
            "timeline": timeline.GetName(), "foreground": True})
    if spec.name == "magic-mask-seed":
        from library.tools import magic_mask
        from library.tools.computer_use.scripts import run_script
        from library.tools.mask_seed import MaskSeed, Point

        seed = MaskSeed(clip_id=uid, frame=0, subject="person",
                        source=Point(x=320.0, y=180.0),
                        viewer=Point(x=640.0, y=360.0),
                        object_id="q1", sample_index=0, seed_frame=0)
        source_frame_range = magic_mask.source_frame_range_for_item(
            item, seed_frame=seed.seed_frame)
        from library.tools.computer_use.__main__ import _main_window_scale

        scale = _main_window_scale(client)
        script = magic_mask.seed_script(
            seed, scale_factor=scale or 2.0,
            source_frame_range=source_frame_range)
        result = run_script(client, script, foreground=True)
        if result.outcome != "done":
            raise RuntimeError(f"seed script ended {result.outcome}")
        return {"op": "magic-mask-seed", "steps": result.steps}
    if spec.name == "magic-mask-track":
        from library.tools import magic_mask
        from library.tools.computer_use.scripts import run_script

        source_frame_range = magic_mask.source_frame_range_for_item(item)
        result = run_script(
            client, magic_mask.track_script("forward", source_frame_range),
            foreground=True)
        if result.outcome != "done":
            raise RuntimeError(f"track script ended {result.outcome}")
        return {"op": "magic-mask-track", "steps": result.steps}
    raise RuntimeError(f"no foreground runner for {spec.name!r}")


# ── Suite: one command ────────────────────────────────────────────────

def run_suite_offline(out_dir: Path | None = None,
                      only: tuple = ()) -> dict:
    """Replay every recipe against the fakes; the report, as data.

    `only` names recipes to run (everything otherwise). Returns the
    suite report: per-recipe verdicts plus the background/foreground
    split the live runner batches on.
    """
    out = Path(out_dir) if out_dir else default_replay_root()
    reports = []
    for spec in recipe_registry():
        if only and spec.name not in only:
            continue
        reports.append(replay_offline(spec, out))
    return _summarize(reports, mode="offline", out=str(out))


def _summarize(reports: list[RecipeReport], mode: str, out: str) -> dict:
    """The suite report: verdicts, the batched split, captures."""
    passed = [report.name for report in reports
              if report.verdict == "pass"]
    failed = [report.name for report in reports
              if report.verdict != "pass"]
    return {
        "mode": mode,
        "out": out,
        "passed": passed,
        "failed": failed,
        "background": [report.name for report in reports
                       if report.background],
        "foreground": [report.name for report in reports
                       if not report.background],
        "recipes": [report.to_dict() for report in reports],
    }


def write_report(report: dict, out_dir: Path) -> Path:
    """File the suite report beside its captures; the path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"replay-{int(time.time())}.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    return path


def _record_take_restore(report: RecipeReport, take,
                         error: Exception | None = None) -> bool:
    """Record borrowed()'s read-back against this take's own snapshot.

    A failed or never-entered borrow is a failed proof and stops later
    live takes. The comparison itself belongs to `borrowed()`; this
    records its outcome and the exact snapshot it promised to restore.
    """
    if take is not None and take.restore_verified:
        detail = ("borrowed() restored and read back this take's start: "
                  + json.dumps(take.borrowed_from, sort_keys=True,
                               default=str))
        passed = True
    elif take is not None:
        detail = ("borrowed() restore did not read back this take's start: "
                  + (take.restore_error or "no restore receipt"))
        passed = False
    else:
        detail = "borrowed() did not yield a take-start snapshot"
        if error is not None:
            detail += f" ({type(error).__name__}: {error})"
        passed = False
    report.proofs.append({"name": "restore", "passed": passed,
                          "detail": detail})
    if not passed:
        report.verdict = "fail"
        report.reason = "; ".join(
            part for part in (report.reason, detail) if part)
    return passed


def _failed_live_report(spec: RecipeSpec, error: Exception) -> RecipeReport:
    """Create a per-recipe failure when a take failed before returning."""
    audit, _ = audit_recipe(spec)
    return RecipeReport(
        name=spec.name, background=spec.background, audit=audit,
        verdict="fail",
        reason=f"take raised {type(error).__name__}: {error}")


def _not_run_live_report(spec: RecipeSpec, reason: str) -> RecipeReport:
    """Keep an interrupted suite's per-recipe ledger complete."""
    audit, _ = audit_recipe(spec)
    return RecipeReport(name=spec.name, background=spec.background,
                        audit=audit, verdict="fail", reason=reason)


def main(argv=None) -> int:
    """`ren computer-use replay`: the one command.

    Offline by default: audits plus fakes, no Resolve, no screen.
    `--live` replays the background recipes in Ren Qualification
    through the broker borrow wrapper (and the foreground batch
    with `--allow-foreground` under the captain-window grant).
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="ren computer-use replay",
        description="Replay every stored recipe end to end.")
    parser.add_argument("--live", action="store_true",
                        help="run live in Ren Qualification (default: fakes)")
    parser.add_argument("--allow-foreground", action="store_true",
                        help="take the captain's screen for the foreground "
                             "batch (needs the firstmate grant)")
    parser.add_argument("--only", default="",
                        help="comma-separated recipe names (default: all)")
    parser.add_argument("--out", default="",
                        help="report + captures dir (default: per-machine)")
    args = parser.parse_args(argv)

    only = tuple(name.strip() for name in args.only.split(",")
                 if name.strip())
    out = Path(args.out) if args.out else default_replay_root()
    if not args.live:
        report = run_suite_offline(out, only=only)
        path = write_report(report, out)
        print(f"replay offline: {len(report['passed'])} passed, "
              f"{len(report['failed'])} failed")
        for recipe in report["recipes"]:
            print(f"  {recipe['verdict']:4} {recipe['name']}: "
                  f"{recipe['reason']}")
        print(f"report: {path}")
        return 0 if not report["failed"] else 1
    return _main_live(args, out, only)


def _main_live(args, out: Path, only: tuple) -> int:
    """Queue the live replay: background now, foreground batched.

    Each take enters through `ui_take`; its `resolve_lease` obtains one
    broker turn and holds one exclusive lease while `borrowed()` opens
    Ren Qualification and restores the captain's project with read-back.
    """
    from library.tools.computer_use.take import ui_take
    from library.tools.resolved import client as broker

    if not broker.serving():
        print("live replay refuses: the Resolve broker is not serving")
        return 2

    specs = [spec for spec in recipe_registry()
             if not only or spec.name in only]
    refused = [(spec.name, _live_guard(spec, args.allow_foreground))
               for spec in specs]
    refused = [(name, reason) for name, reason in refused if reason]
    runnable = [spec for spec in specs
                if _live_guard(spec, args.allow_foreground) is None]
    if refused:
        print("live replay refuses:")
        for name, reason in refused:
            print(f"  {name}: {reason}")
    if not runnable:
        return 2
    background = [spec for spec in runnable if spec.background]
    foreground = [spec for spec in runnable if not spec.background]
    reports: list[RecipeReport] = []
    out.mkdir(parents=True, exist_ok=True)
    stopped = False
    for index, spec in enumerate(background):
        purpose = f"recipe replay {spec.name}"
        take = None
        recipe_report = None
        take_error = None
        try:
            with ui_take(purpose) as take:
                recipe_report = _live_run_background(spec, take, out)
        except Exception as exc:  # noqa: BLE001 - saved per recipe below
            take_error = exc
        if recipe_report is None:
            recipe_report = _failed_live_report(
                spec, take_error or RuntimeError("take returned no report"))
        elif take_error is not None:
            recipe_report.verdict = "fail"
            recipe_report.reason = "; ".join(
                part for part in (
                    recipe_report.reason,
                    f"take raised {type(take_error).__name__}: {take_error}")
                if part)
        restored = _record_take_restore(recipe_report, take, take_error)
        reports.append(recipe_report)
        menu_unverified = "MenuClosureError" in recipe_report.reason
        activation_failed = (
            "activation did not read back" in recipe_report.reason.lower())
        if not restored or menu_unverified or activation_failed:
            if not restored:
                stop_detail = "the borrowed() cursor restore did not read back"
            elif menu_unverified:
                stop_detail = "a recipe menu did not read back closed"
            else:
                stop_detail = "Resolve activation did not read back"
            stop_reason = (f"not run: live suite stopped because {stop_detail} "
                           f"after {spec.name}")
            reports.extend(_not_run_live_report(remaining, stop_reason)
                           for remaining in background[index + 1:])
            stopped = True
            break
    if foreground:
        if stopped:
            reports.extend(_not_run_live_report(
                spec, "not run: live suite stopped after an earlier safety failure")
                           for spec in foreground)
        else:
            purpose = "recipe replay foreground batch"
            take = None
            batch_reports = []
            take_error = None
            try:
                with ui_take(purpose) as take:
                    batch_reports = _live_run_foreground_batch(
                        foreground, take, out)
            except Exception as exc:  # noqa: BLE001 - saved per recipe below
                take_error = exc
            if not batch_reports:
                if take_error is None:
                    take_error = RuntimeError(
                        "foreground batch returned no reports")
                batch_reports = [_failed_live_report(spec, take_error)
                                 for spec in foreground]
            elif take_error is not None:
                for recipe_report in batch_reports:
                    recipe_report.verdict = "fail"
                    recipe_report.reason = "; ".join(
                        part for part in (
                            recipe_report.reason,
                            f"take raised {type(take_error).__name__}: "
                            f"{take_error}") if part)
            for recipe_report in batch_reports:
                _record_take_restore(recipe_report, take, take_error)
            reports.extend(batch_reports)
    report = _summarize(reports, mode="live", out=str(out))
    path = write_report(report, out)
    print(f"replay live: {len(report['passed'])} passed, "
          f"{len(report['failed'])} failed")
    for recipe in report["recipes"]:
        print(f"  {recipe['verdict']:4} {recipe['name']}: "
              f"{recipe['reason']}")
    print(f"report: {path}")
    return 0 if not report["failed"] else 1
