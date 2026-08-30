"""Workspace > Scripts > VEP Pipeline Panel.

The pipeline, beside the timeline.  Five tabs: what every step produced
(drilled down, not dumped), what the pipeline knows about the clip under
the playhead, the run configuration and its controls, the review gates,
and the plan as a picture.

This file is the WIDGET LAYER and nothing else.  Everything it decides
lives in `library/tools/panel/` in the repository, which imports no Qt
and no Resolve and is driven by `tests/test_panel_*.py`.  The split is
the point: a panel that kept its own copy of the rules is what gave the
scout's prototype three steps the wrong status and hid one of two
failures on 001, and `project_layout.node_id_for` is the one translator.

`scripts/install_resolve_scripts.sh` copies this into the Scripts folder
with the checkout's path stamped into `REPO_ROOT`, so the installed copy
imports the real implementation and cannot drift from it.  Editing the
repository takes effect on the next click, with no re-install.

Three properties, in the order they matter
------------------------------------------
1. **No server and no browser.**  Everything on screen is read straight
   off disk with the standard library, and the one picture is encoded by
   `panel/strip.py` from `zlib` and `struct` in this process.  Resolve
   launches whatever interpreter it finds - stock `/usr/bin/python3` on
   macOS - so anything beyond the standard library is a thing that will
   one day not be installed.
2. **It never freezes Resolve, and never freezes itself.**  The panel
   owns its loop (`StepLoop(False)`) and every slow thing goes through
   `spawn` onto a worker thread.  The heartbeat under the header is the
   captain's own proof: if that number stops moving, the panel is stuck.
3. **It holds no credential.**  The model is reached by shelling out to
   the already-authenticated `claude` CLI.  Nothing in this file, and
   nothing in Resolve's application-support folder, is a secret.

Measured facts about this toolkit that shape the code below, all from
the scout's probe on Resolve 21.0.0b.28:

* `hasattr` is True for widgets that do not exist - judge by the value.
* `Stack.CurrentIndex` does not work: writing 1 reads back -2 and every
  page stays drawn over every other.  `Hidden` is the page switch.
* `Label.Pixmap = "/path.png"` is accepted and draws NOTHING.  `<img>`
  inside a read-only `TextEdit` draws, and scrolls with its prose.
* `ui.Timer` never fires.  The owned loop is the substitute.
* Qt decides a string is rich text by looking for a TAG, so a line of
  pure entities renders literally.
* `StepLoop` raises `KeyError: 'On'` for an event with no handler.
* **`fusionscript.so` crashes at interpreter exit** and the panel has to
  leave without running the C runtime's static destructors - see
  :func:`_leave`.
"""

import json
import os
import subprocess
import sys
import threading
import time
import traceback

REPO_ROOT = ""
"""Filled in by scripts/install_resolve_scripts.sh.  Left empty when this
file is run from inside the checkout, where it finds itself."""

PANEL_VERSION = "1.0"

# The model the Ask box uses. Overridable, and keyless either way: the
# panel shells out to a CLI that is already authenticated rather than
# holding a provider key in a folder that syncs and backs up.
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
MODEL = os.environ.get("VEP_PANEL_MODEL") or DEFAULT_MODEL
ASK_TIMEOUT_SECONDS = 180

SCRATCH = os.path.join(os.path.expanduser("~"), ".vep_panel")


def _repo_root():
    for candidate in (
        REPO_ROOT,
        os.environ.get("VEP_REPO_ROOT", ""),
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ):
        if candidate and os.path.isfile(
                os.path.join(candidate, "library", "tools", "panel",
                             "trace.py")):
            return candidate
    return ""


ROOT = _repo_root()
if ROOT and ROOT not in sys.path:
    sys.path.insert(0, ROOT)

sys.path.append("/Library/Application Support/Blackmagic Design/"
                "DaVinci Resolve/Developer/Scripting/Modules")
os.environ.setdefault(
    "RESOLVE_SCRIPT_API",
    "/Library/Application Support/Blackmagic Design/DaVinci Resolve/"
    "Developer/Scripting")
os.environ.setdefault(
    "RESOLVE_SCRIPT_LIB",
    "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/"
    "Fusion/fusionscript.so")


# ── Reading Resolve ──────────────────────────────────────────────────

def read_context(resolve, ResolveContext):
    """Page, timeline, playhead, the clip under it, and every marker.

    Both `name` and `note` are read off a marker: they are two pieces of
    typed text and reading only `note` loses everything typed into the
    field the cursor lands in (AGENTS.md section 15).
    """
    context = ResolveContext()
    try:
        context.page = resolve.GetCurrentPage() or ""
        project = resolve.GetProjectManager().GetCurrentProject()
        if not project:
            return context
        context.project = project.GetName() or ""
        timeline = project.GetCurrentTimeline()
        if not timeline:
            return context
        context.timeline = timeline.GetName() or ""
        context.timecode = timeline.GetCurrentTimecode() or ""
        try:
            context.fps = float(project.GetSetting("timelineFrameRate")
                                or 30.0)
        except (TypeError, ValueError):
            context.fps = 30.0
        context.timeline_frame = _playhead_frame(timeline, context.fps)
        context.clip = _item_dict(timeline.GetCurrentVideoItem())
        markers = timeline.GetMarkers() or {}
        for frame in sorted(markers):
            marker = markers[frame]
            context.markers.append({
                "frame": frame,
                "name": marker.get("name", ""),
                "note": marker.get("note", ""),
                "color": marker.get("color", ""),
                "custom": marker.get("customData", ""),
            })
    except Exception as exc:                        # noqa: BLE001 - reported
        context.error = "%s: %s" % (type(exc).__name__, exc)
    return context


def _item_dict(item, track=None):
    """One timeline item, as the plain dict `clip_context` reads."""
    if item is None:
        return None
    try:
        pool_item = item.GetMediaPoolItem()
        return {
            "name": item.GetName(),
            "start": item.GetStart(),
            "end": item.GetEnd(),
            "duration": item.GetDuration(),
            "left_offset": item.GetLeftOffset(),
            "track": track,
            "file": (pool_item.GetClipProperty("File Path")
                     if pool_item else None),
        }
    except Exception:                               # noqa: BLE001
        return None


def _playhead_frame(timeline, fps):
    """The playhead, in the timeline's own frame numbering.

    `GetStartFrame()` is what a timeline marker's frame is relative to
    (AGENTS.md section 15), and `GetStart()` on an item is absolute, so
    the two are only comparable once the start frame is added back.
    """
    try:
        from library.tools.panel import strip

        seconds = strip.timecode_seconds(timeline.GetCurrentTimecode(), fps)
        if seconds is None:
            return None
        return int(round(seconds * fps))
    except Exception:                               # noqa: BLE001
        return None


def read_video_items(timeline, video_tracks=8):
    """Every video item on the timeline, once, as plain dicts.

    Read on a worker and CACHED: `GetItemListInTrack` plus four getters
    per item is a hundred-odd scripting calls, and the playhead moves
    twice a second. What changes per tick is the FRAME, and deciding
    which item covers a frame is arithmetic `clip_context.picture_at`
    does with no Resolve at all.
    """
    items = []
    if timeline is None:
        return items
    for track in range(1, video_tracks + 1):
        try:
            found = timeline.GetItemListInTrack("video", track) or []
        except Exception:                           # noqa: BLE001
            continue
        for item in found:
            entry = _item_dict(item, track)
            if entry:
                items.append(entry)
    return items


def find_project_from_timeline(timeline):
    """Which pipeline project is this timeline?  Ask the timeline.

    Every V1 clip's source file lives inside the project folder, so walk
    up from one until `pipeline_data.json` appears.  This is the thing
    the browser dashboard structurally cannot do - it has to be told - so
    it is kept live and is never replaced by a path.
    """
    if timeline is None:
        return None
    for track in (1, 2, 3):
        try:
            items = timeline.GetItemListInTrack("video", track) or []
        except Exception:                           # noqa: BLE001
            items = []
        for item in items:
            try:
                pool_item = item.GetMediaPoolItem()
                path = (pool_item.GetClipProperty("File Path")
                        if pool_item else None)
            except Exception:                       # noqa: BLE001
                path = None
            if not path:
                continue
            probe = os.path.dirname(path)
            for _ in range(6):
                if os.path.isfile(os.path.join(probe, "pipeline_data.json")):
                    return probe
                parent = os.path.dirname(probe)
                if parent == probe:
                    break
                probe = parent
    return None


# ── The model, keylessly ─────────────────────────────────────────────

def ask_model(question, context_block, model=MODEL,
              timeout=ASK_TIMEOUT_SECONDS):
    """Shell out to an ALREADY-AUTHENTICATED CLI. Worker thread only."""
    prompt = (
        "You are answering a question from inside DaVinci Resolve about a "
        "video edit. The editor is looking at the timeline right now, and "
        "everything below was measured by the editing pipeline itself.\n\n"
        + context_block
        + "\n\nTheir question: " + question
        + "\n\nAnswer from what is above. Be concrete. If something was not "
          "measured, say so rather than guessing. Under 150 words.")
    started = time.time()
    try:
        proc = subprocess.run(
            ["claude", "-p", "--model", model],
            input=prompt, capture_output=True, encoding="utf-8",
            timeout=timeout)
        text = (proc.stdout or "").strip() or (proc.stderr or "").strip()
        return {"ok": proc.returncode == 0, "text": text, "model": model,
                "seconds": round(time.time() - started, 1),
                "prompt_chars": len(prompt), "prompt": prompt}
    except Exception as exc:                        # noqa: BLE001 - reported
        return {"ok": False, "text": "%s: %s" % (type(exc).__name__, exc),
                "model": model, "seconds": round(time.time() - started, 1),
                "prompt_chars": len(prompt), "prompt": prompt}


# ═══════════════════════════════════════════════════════════ the panel ══

# How many characters the `preview` column can draw: 400 px at ~7.2 px
# per character in the 12 px monospace. The string is bounded to this so
# it ends in its own ellipsis instead of being clipped by the widget.
PREVIEW_COLUMNS = 52

# The strip is drawn to the width of the pane it goes in, so the picture
# does not stop short of the right edge with dead space beside it.
STRIP_WIDTH = 1120

TABS = ("Trace", "This clip", "Run", "Gates", "Timeline")
PAGE_TRACE, PAGE_CLIP, PAGE_RUN, PAGE_GATES, PAGE_STRIP = range(5)


class Panel(object):

    def __init__(self, resolve, ui, dispatcher, modules):
        self.resolve = resolve
        self.ui = ui
        self.disp = dispatcher
        self.m = modules                            # the panel package

        os.makedirs(SCRATCH, exist_ok=True)
        self.strip_path = os.path.join(SCRATCH, "strip.png")
        self.beat_log = os.path.join(SCRATCH, "panel_beats.jsonl")
        open(self.beat_log, "w").close()

        self.results = []
        self.lock = threading.Lock()
        self.jobs = 0
        self.running = True
        self.beats = 0
        self.started = time.time()
        self.page = PAGE_TRACE

        self.project_folder = ""
        self.state = None
        self.rows = []
        self.row = None
        self.source = self.m.trace.FROM_STATE
        self.path = ()
        self.context = self.m.clip_context.ResolveContext()
        self.facts = self.m.clip_context.ClipFacts()
        self.preview = None
        self.profile_name = ""
        self.gate = None
        self.log_path = ""
        self.trace_result = None
        self.gates_seen = []
        self.clip_job = False
        self.joined_key = None
        self.items = []
        self.items_for = ""
        self._in_flight = []
        self._build()

    # ─────────────────────────────────────────────────────────── layout ──
    def _build(self):
        ui = self.ui
        rt = self.m.richtext
        self.win = self.disp.AddWindow(
            {"ID": "VEPPanel", "WindowTitle": "VEP Pipeline Panel",
             "Geometry": [70, 90, 1180, 860]},
            [ui.VGroup([
                ui.HGroup({"Weight": 0}, [
                    ui.Label({"ID": "ctx", "Text": "reading Resolve...",
                              "Weight": 1}),
                    ui.Button({"ID": "reload", "Text": "Reload",
                               "Weight": 0}),
                ]),
                ui.Label({"ID": "beat", "Text": "", "Weight": 0}),
                ui.TabBar({"ID": "tabs", "Weight": 0}),
                ui.Stack({"ID": "stack", "Weight": 1}, [

                    # 1. TRACE - every step's output, one level at a time.
                    ui.VGroup({"ID": "page0"}, [
                        ui.Label({"ID": "runhead", "Text": "loading...",
                                  "Weight": 0}),
                        ui.HGroup({"Weight": 1}, [
                            ui.VGroup({"Weight": 1.0}, [
                                ui.Tree({"ID": "steps", "Weight": 1}),
                            ]),
                            ui.VGroup({"Weight": 2.4}, [
                                ui.Label({"ID": "crumb",
                                          "Text": "select a step on the left",
                                          "Weight": 0}),
                                ui.HGroup({"Weight": 0}, [
                                    ui.Button({"ID": "up", "Text": "Up"}),
                                    ui.Button({"ID": "root", "Text": "Top"}),
                                    ui.Button({"ID": "source", "Text":
                                               "source: pipeline_data.json"}),
                                    ui.Button({"ID": "summary",
                                               "Text": "summary.md"}),
                                ]),
                                ui.Tree({"ID": "level", "Weight": 1}),
                                ui.TextEdit({"ID": "value", "ReadOnly": True,
                                             "Weight": 1}),
                            ]),
                        ]),
                    ]),

                    # 2. THIS CLIP - the join, and the question.
                    ui.VGroup({"ID": "page1"}, [
                        ui.TextEdit({"ID": "clipbody", "ReadOnly": True,
                                     "Weight": 1}),
                        ui.TextEdit({"ID": "question", "Weight": 0,
                                     "MinimumSize": [0, 62],
                                     "PlaceholderText":
                                         "what does this clip show?"}),
                        ui.HGroup({"Weight": 0}, [
                            ui.Button({"ID": "ask",
                                       "Text": "Ask about this clip"}),
                            ui.Button({"ID": "showctx",
                                       "Text": "Show what would be sent"}),
                            ui.Label({"ID": "askstate", "Text": "idle",
                                      "Weight": 1}),
                        ]),
                    ]),

                    # 3. RUN - the configuration, previewed, then driven.
                    ui.VGroup({"ID": "page2"}, [
                        ui.HGroup({"Weight": 0}, [
                            ui.Label({"ID": "runstate", "Text": "",
                                      "Weight": 1}),
                        ]),
                        ui.Label({"ID": "profhead", "Weight": 0, "Text":
                                  "Run profile - click one to see what it "
                                  "would do"}),
                        ui.HGroup({"Weight": 1}, [
                            # A profile list is two or three rows. It gets
                            # a narrow column so the plan beside it - which
                            # really does scroll - gets the width.
                            ui.VGroup({"Weight": 0.85}, [
                                ui.Tree({"ID": "profiles", "Weight": 1}),
                            ]),
                            ui.VGroup({"Weight": 3.15}, [
                                ui.TextEdit({"ID": "planbody",
                                             "ReadOnly": True, "Weight": 1}),
                            ]),
                        ]),
                        ui.HGroup({"Weight": 0}, [
                            ui.Button({"ID": "start", "Text": "Start"}),
                            ui.Button({"ID": "hold", "Text": "Handbrake"}),
                            ui.Button({"ID": "release", "Text": "Release"}),
                            ui.Button({"ID": "resume", "Text": "Resume"}),
                            ui.Button({"ID": "tail", "Text": "Log"}),
                        ]),
                    ]),

                    # 4. GATES - the pause, at the timeline.
                    ui.VGroup({"ID": "page3"}, [
                        ui.Label({"ID": "gatehead", "Text": "Review gates",
                                  "Weight": 0}),
                        ui.HGroup({"Weight": 1}, [
                            ui.VGroup({"Weight": 0.85}, [
                                ui.Tree({"ID": "gatelist", "Weight": 1}),
                            ]),
                            ui.VGroup({"Weight": 3.15}, [
                                ui.TextEdit({"ID": "gatebody",
                                             "ReadOnly": True, "Weight": 1}),
                            ]),
                        ]),
                        ui.Label({"ID": "gatenotehead",
                                  "Text": "Note (recorded with the answer)",
                                  "Weight": 0}),
                        ui.TextEdit({"ID": "gatenote", "Weight": 0,
                                     "MinimumSize": [0, 50]}),
                        ui.Label({"ID": "gaterevhead",
                                  "Text": "Revision - a JSON object, "
                                          "deep-merged into the step's output",
                                  "Weight": 0}),
                        ui.TextEdit({"ID": "gaterev", "Weight": 0,
                                     "MinimumSize": [0, 66],
                                     "PlaceholderText": '{"key": "value"}'}),
                        ui.HGroup({"Weight": 0}, [
                            ui.Button({"ID": "approve", "Text": "Approve"}),
                            ui.Button({"ID": "reject", "Text": "Reject"}),
                            ui.Button({"ID": "revise", "Text": "Revise"}),
                            ui.Label({"ID": "gatestate", "Text": "",
                                      "Weight": 1}),
                        ]),
                    ]),

                    # 5. TIMELINE - the plan, drawn here.
                    ui.VGroup({"ID": "page4"}, [
                        ui.Label({"ID": "striphead", "Text": "", "Weight": 0}),
                        ui.TextEdit({"ID": "stripbody", "ReadOnly": True,
                                     "Weight": 1}),
                    ]),
                ]),
            ])])

        self.itm = self.win.GetItems()
        for wid in ("ctx", "beat", "runhead", "crumb", "runstate", "profhead",
                    "gatehead", "gatestate", "askstate", "striphead",
                    "gatenotehead", "gaterevhead"):
            self._style(wid, "color:%s; font-size:12px;" % rt.INK)
        for wid in ("value", "clipbody", "planbody", "gatebody", "stripbody",
                    "question", "gatenote", "gaterev", "steps", "level",
                    "profiles", "gatelist"):
            self._style(wid, "background-color:%s; color:%s; "
                             "font-family:'SF Mono',Menlo,monospace; "
                             "font-size:12px;" % (rt.BACKGROUND, rt.INK))

        for label in TABS:
            self.itm["tabs"].AddTab(label)

        # Widths are sized to the LONGEST value each column really holds,
        # measured off this pipeline: 27 characters for both
        # `4_06_render_motion_graphics` and `camera_motion_decomposition`,
        # at ~7.2 px per character in the 12 px monospace, plus the
        # tree's own indent. A step's name is the primary key of the
        # trace and must never need scrolling to read.
        #
        # `ledger`, `files` and `size` came OUT of the step list: 62 px
        # of "ledger" rendered as "ledge" and "preflight" as "pref",
        # which read as complete words - a truncation indistinguishable
        # from a value. They are in the detail heading instead, whole.
        self._columns("steps", ["step", "status"], widths=[268, 84])
        self._columns("level", ["key", "kind", "size", "preview"],
                      widths=[240, 54, 76, 380])
        self._columns("profiles", ["profile", "where"], widths=[130, 96])
        self._columns("gatelist", ["step", "status"], widths=[140, 96])

        w = self.win.On
        w.VEPPanel.Close = lambda ev: setattr(self, "running", False)
        w.reload.Clicked = lambda ev: self.reload()
        w.tabs.CurrentChanged = self.on_tab
        w.steps.ItemClicked = self.on_step
        w.steps.CurrentItemChanged = self.on_step
        w.level.ItemClicked = self.on_level
        w.up.Clicked = lambda ev: self.on_up()
        w.root.Clicked = lambda ev: self.on_root()
        w.source.Clicked = lambda ev: self.on_source()
        w.summary.Clicked = lambda ev: self.on_summary()
        w.ask.Clicked = lambda ev: self.on_ask()
        w.showctx.Clicked = lambda ev: self.on_showctx()
        w.profiles.ItemClicked = self.on_profile
        w.start.Clicked = lambda ev: self.on_start()
        w.hold.Clicked = lambda ev: self.on_hold()
        w.release.Clicked = lambda ev: self.on_release()
        w.resume.Clicked = lambda ev: self.on_resume()
        w.tail.Clicked = lambda ev: self.on_tail()
        w.gatelist.ItemClicked = self.on_gate
        w.approve.Clicked = lambda ev: self.on_answer("approved")
        w.reject.Clicked = lambda ev: self.on_answer("rejected")
        w.revise.Clicked = lambda ev: self.on_answer("revised")

    def _columns(self, wid, headers, widths):
        """Every column gets a width. Setting only the first leaves the
        rest at Qt's default and the row reads as one clipped string."""
        try:
            tree = self.itm[wid]
            tree.ColumnCount = len(headers)
            tree.SetHeaderLabels(headers)
            for index, width in enumerate(widths):
                tree.ColumnWidth[index] = width
        except Exception:                           # noqa: BLE001
            pass

    def _style(self, wid, css):
        try:
            self.itm[wid].StyleSheet = css
        except Exception:                           # noqa: BLE001
            pass

    def _set(self, wid, attr, value):
        try:
            self.itm[wid][attr] = value
        except Exception:                           # noqa: BLE001
            pass

    def _html(self, wid, html):
        try:
            self.itm[wid].HTML = html
        except Exception:                           # noqa: BLE001
            self._set(wid, "Text", html)

    def _text(self, wid):
        try:
            return (self.itm[wid].PlainText or "").strip()
        except Exception:                           # noqa: BLE001
            return ""

    # ────────────────────────────────────────────────── worker plumbing ──
    def spawn(self, tag, fn, *args, **kwargs):
        """Every slow thing goes through here. Nothing slow on the loop."""
        self.jobs += 1

        def run():
            try:
                result, error = fn(*args, **kwargs), None
            except Exception:                       # noqa: BLE001 - reported
                result, error = None, traceback.format_exc()
            with self.lock:
                self.results.append((tag, result, error))

        threading.Thread(target=run, daemon=True).start()

    def drain(self):
        with self.lock:
            batch, self.results = self.results, []
        for tag, result, error in batch:
            self.jobs -= 1
            if error:
                self._set("runhead", "Text",
                          "worker %s failed - see %s" % (tag, self.beat_log))
                with open(self.beat_log, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"tag": tag,
                                             "error": error}) + "\n")
                continue
            if tag == "clip":
                self.clip_job = False
                self.facts = result
                self.paint_clip()
            elif tag == "state":
                self.state, self.rows = result
                self.paint_steps()
                self.refresh_clip()
                self.spawn("strip", self.build_strip)
                self.spawn("preview", self.build_preview)
                self.paint_gates()
            elif tag == "trace":
                self.paint_trace(result)
            elif tag == "ask":
                self.paint_answer(result)
            elif tag == "strip":
                self.paint_strip(result)
            elif tag == "preview":
                self.preview = result
                self.paint_plan()
            elif tag == "launch":
                self.paint_launch(result)
            elif tag == "tail":
                self.paint_tail(result)
            elif tag == "items":
                self.items_for, self.items = result
                self.resolve_picture()
                self.joined_key = None

    # ──────────────────────────────────────────────────────── handlers ──
    def show_page(self, index):
        """`Stack.CurrentIndex` is broken on Resolve 21 - writing 1 reads
        back -2 and every page stays drawn over every other. `Hidden`
        works and reads back correctly."""
        self.page = index
        for i in range(len(TABS)):
            try:
                self.itm["page%d" % i].Hidden = (i != index)
            except Exception:                       # noqa: BLE001
                pass
        try:
            self.itm["tabs"].CurrentIndex = index
        except Exception:                           # noqa: BLE001
            pass

    def on_tab(self, ev):
        try:
            self.show_page(self.itm["tabs"].CurrentIndex)
        except Exception:                           # noqa: BLE001
            pass

    def _current(self, wid, column=0):
        try:
            item = self.itm[wid].CurrentItem()
            return item.Text[column] if item else ""
        except Exception:                           # noqa: BLE001
            return ""

    def on_step(self, ev):
        name = self._current("steps")
        for row in self.rows:
            if row.dirname == name:
                self.row, self.path = row, ()
                self.trace_now()
                self.show_page(PAGE_TRACE)
                return

    def on_level(self, ev):
        key = self._current("level")
        if not self.row or not key or self.trace_result is None:
            return
        for entry in self.trace_result.entries:
            if entry.key != key:
                continue
            if entry.descendable:
                self.path = tuple(self.path) + (entry.step,)
                self.trace_now()
            else:
                # A leaf: descending would land on a value with no
                # members, so the panel shows it here WHOLE (bounded, and
                # saying what did not fit) rather than moving the path.
                self.paint_leaf(entry)
            return

    def on_up(self):
        if self.row and self.path:
            self.path = tuple(self.path)[:-1]
            self.trace_now()

    def on_root(self):
        if self.row:
            self.path = ()
            self.trace_now()

    def on_source(self):
        self.source = (self.m.trace.FROM_EXPORT
                       if self.source == self.m.trace.FROM_STATE
                       else self.m.trace.FROM_STATE)
        self._set("source", "Text", "source: %s" % self.source)
        self.path = ()
        self.trace_now()

    def on_summary(self):
        if not self.row:
            return
        rt = self.m.richtext
        text = self.m.trace.summary_markdown(self.row)
        self._html("value", rt.document(
            rt.heading("%s - summary.md" % self.row.dirname, 3),
            rt.block(text) if text else
            rt.paragraph("This step wrote no summary.md.", "dim")))

    def trace_now(self):
        if not self.row:
            return
        self._set("crumb", "Text", "reading...")
        self.spawn("trace", self.m.trace.trace_step, self.project_folder,
                   self.row, self.source, self.path, self.state)

    # ─────────────────────────────────────────────────── clip and model ──
    def resolve_picture(self):
        """Turn Resolve's `current item` into the PICTURE under the
        playhead, using the cached item list.

        On a finished build the topmost video item is a subtitle card, so
        asking Resolve what is current and joining THAT to the footage
        catalog answers about the wrong clip. `picture_at` picks the
        highest track carrying footage; the overlays are still reported.
        """
        cc = self.m.clip_context
        frame = self.context.timeline_frame
        if not self.items:
            return
        picture = cc.picture_at(self.items, frame)
        self.context.overlays = cc.overlays_at(self.items, frame)
        current = self.context.clip
        if picture is None:
            return
        if current and not cc.is_overlay(current.get("file") or ""):
            self.context.topmost = None
            return
        self.context.topmost = current
        self.context.clip = picture

    def clip_key(self):
        """What makes this a DIFFERENT clip to join.

        The playhead moves every tick and the join costs a ledger build,
        so the panel re-joins when the CLIP changes rather than when the
        timecode does.
        """
        clip = self.context.clip or {}
        return (clip.get("file"), clip.get("start"), clip.get("duration"))

    def refresh_clip(self):
        """Re-join, on a worker. Never on the loop: the join builds the
        whole decision ledger off the manifest."""
        if self.state is None or self.clip_job:
            return
        self.clip_job = True
        self.joined_key = self.clip_key()
        self.spawn("clip", self.join_clip, self.state, self.context)

    def join_clip(self, state, context):
        """A worker: the catalog join, the vision read, the transcript
        projection and the decision ledger."""
        try:
            return self.m.clip_context.clip_facts(state, context)
        except Exception:                           # noqa: BLE001 - reported
            facts = self.m.clip_context.ClipFacts()
            facts.absences.append(
                "The join failed: " + traceback.format_exc().splitlines()[-1])
            return facts

    def context_block(self):
        return self.m.clip_context.prompt_block(
            self.context, self.facts,
            open_step=self.row.dirname if self.row else "",
            project_folder=self.project_folder)

    def on_showctx(self):
        rt = self.m.richtext
        self._html("clipbody", rt.document(
            rt.heading("What the panel would send", 2),
            rt.lead("Nothing leaves this machine until you press Ask. "
                    "Every line below was read off disk or off Resolve."),
            rt.block(self.context_block())))
        self._set("askstate", "Text", "not sent")

    def on_ask(self):
        question = self._text("question")
        if not question:
            self._set("askstate", "Text", "type a question first")
            return
        self._set("askstate", "Text",
                  "asking %s - the panel keeps running (watch the heartbeat)"
                  % MODEL)
        self.spawn("ask", ask_model, question, self.context_block())

    # ─────────────────────────────────────────────────────────── the run ──
    def build_preview(self):
        return self.m.run_view.preview(self.project_folder, self.profile_name,
                                       state=self.state)

    def on_profile(self, ev):
        name = self._current("profiles")
        self.profile_name = "" if name in ("(none)", "") else name
        self._set("runstate", "Text", "resolving %s..."
                  % (self.profile_name or "a plain run"))
        self.spawn("preview", self.build_preview)

    def on_start(self):
        if not self.preview or not self.preview.can_run:
            self._set("runstate", "Text",
                      "This configuration cannot run - the reason is on the "
                      "right. Nothing was started.")
            return
        argv, why_not = self.m.run_view.run_argv(
            ROOT, self.project_folder, self.preview)
        if not argv:
            self._html("planbody", self.m.richtext.document(
                self.m.richtext.heading("Cannot start a run", 2),
                self.m.richtext.block(why_not)))
            return
        self.spawn("launch", self.m.run_view.start_run, ROOT,
                   self.project_folder, argv)

    def on_hold(self):
        self._set("runstate", "Text",
                  self.m.run_view.handbrake(self.project_folder))

    def on_release(self):
        self._set("runstate", "Text",
                  self.m.run_view.release(self.project_folder))

    def on_resume(self):
        rt = self.m.richtext
        self._html("planbody", rt.document(
            rt.heading("Carrying on from a gate", 2),
            rt.lead("A breakpoint is armed per RUN, so a resume without the "
                    "flags that armed it sails past the next one. This is "
                    "this project's own last run, plus --resume."),
            rt.block(self.m.run_view.resume_hint(self.project_folder)),
            rt.paragraph("Answer the pending gate on the Gates tab first.",
                         "dim")))

    def on_tail(self):
        path = self.log_path or os.path.join(
            self.project_folder, "pipeline_output", "logs", "run_panel.log")
        self._set("runstate", "Text", "reading %s..." % path)
        self.spawn("tail", self.read_tail, path)

    def read_tail(self, path):
        """A worker: up to 64 kB off disk is not loop work."""
        return path, self.m.run_view.tail_log(path)

    def paint_tail(self, result):
        rt = self.m.richtext
        path, text = result
        self._html("planbody", rt.document(
            rt.heading("Run log", 2), rt.lead(path),
            rt.block(text or "(nothing yet)")))

    # ───────────────────────────────────────────────────────── the gates ──
    def on_gate(self, ev):
        self.gate = self._current("gatelist")
        self.paint_gate()

    def on_answer(self, action):
        if not self.gate:
            self._set("gatestate", "Text", "select a gate first")
            return
        message = self.m.run_view.answer_gate(
            self.project_folder, self.gate, action,
            note=self._text("gatenote"),
            revision_json=self._text("gaterev"))
        self._set("gatestate", "Text", message)
        self.paint_gates()
        self.paint_gate()

    # ──────────────────────────────────────────────────────── painting ──
    def paint_ctx(self):
        rt = self.m.richtext
        context = self.context
        clip = context.clip or {}
        self._set("ctx", "Text",
                  "<b style='color:%s'>%s</b> &#160; page <b>%s</b> &#160; "
                  "%s &#160; under playhead: <b style='color:%s'>%s</b> "
                  "&#160; markers: <b>%d</b>"
                  % (rt.HEADING, rt.esc(context.timeline or "no timeline"),
                     rt.esc(context.page or "-"),
                     rt.esc(context.timecode or "--"), rt.GOOD,
                     rt.esc(clip.get("name") or "-"),
                     len(context.markers or [])))

    def paint_steps(self):
        rt, trace = self.m.richtext, self.m.trace
        counts = trace.counts(self.rows)
        stranded = trace.stranded_failures(self.rows, self.state)
        head = ("<b>%s</b> &#160; <span style='color:%s'>%d done</span> / "
                "<span style='color:%s'>%d failed</span> / "
                "<span style='color:%s'>%d pending</span> / %d unwired, "
                "of %d steps"
                % (rt.esc(os.path.basename(self.project_folder)), rt.GOOD,
                   counts["done"], rt.BAD, counts["failed"], rt.INK_DIM,
                   counts["pending"], counts["unwired"], counts["total"]))
        if stranded:
            head += ("<br><span style='color:%s'>! %s recorded as failed and "
                     "no longer in this DAG - no run can clear this</span>"
                     % (rt.WARN, rt.esc(", ".join(stranded))))
        self._set("runhead", "Text", head)

        tree = self.itm["steps"]
        try:
            tree.Clear()
        except Exception:                           # noqa: BLE001
            pass
        for row in self.rows:
            item = tree.NewItem()
            item.Text[0] = row.dirname
            item.Text[1] = row.status
            self._colour(item, 1, {"done": rt.GOOD, "failed": rt.BAD,
                                   "pending": rt.INK_DIM,
                                   "unwired": rt.WARN}[row.status])
            tree.AddTopLevelItem(item)

    def _colour(self, item, column, hexcolour):
        try:
            item.TextColor[column] = {
                "R": int(hexcolour[1:3], 16) / 255.0,
                "G": int(hexcolour[3:5], 16) / 255.0,
                "B": int(hexcolour[5:7], 16) / 255.0, "A": 1.0}
        except Exception:                           # noqa: BLE001
            pass

    def paint_trace(self, result):
        rt, trace = self.m.richtext, self.m.trace
        self.trace_result = result
        self._set("crumb", "Text",
                  "<b>%s</b> &#160; <span style='color:%s'>%s</span>"
                  % (rt.esc(trace.breadcrumb(result.path,
                                             result.row.dirname)),
                     rt.INK_DIM, rt.esc(result.source)))

        tree = self.itm["level"]
        try:
            tree.Clear()
        except Exception:                           # noqa: BLE001
            pass
        for entry in result.entries:
            item = tree.NewItem()
            item.Text[0] = entry.key
            item.Text[1] = entry.kind
            item.Text[2] = entry.size
            # Bounded to what the 400 px column can DRAW, so the string
            # ends in its own "..." rather than being cut mid-value by
            # Qt with nothing to say it was cut.
            item.Text[3] = trace.preview(result.value_of(entry),
                                         width=PREVIEW_COLUMNS)
            if entry.descendable:
                self._colour(item, 0, rt.SUBHEADING)
            tree.AddTopLevelItem(item)

        row = result.row
        parts = [rt.heading("%s  -  %s" % (row.dirname, row.status), 2),
                 rt.paragraph(
                     "%s ledger  -  %d files, %s  -  %s"
                     % ("no" if row.ledger == "-" else row.ledger,
                        row.files, rt.bytes_human(row.bytes), row.node_id),
                     "dim")]
        if result.error:
            parts.append(rt.paragraph(result.error, "bad"))
        elif result.entries:
            parts.append(rt.lead(
                "%d entries at this level. Click one to descend; nothing "
                "below it has been read." % len(result.entries)))
            if result.withheld_entries:
                parts.append(rt.paragraph(
                    "%d more entries at this level are not listed."
                    % result.withheld_entries, "warn"))
        elif result.leaf is not None:
            parts.append(rt.block(result.leaf.text))
            if result.leaf.truncated:
                parts.append(rt.paragraph(result.leaf.note(), "warn"))
        if result.row.error:
            parts.append(rt.heading("step_errors", 3))
            parts.append(rt.paragraph(result.row.error, "bad"))
        if result.other_files:
            parts.append(rt.heading("other files this step wrote", 3))
            parts.append(rt.block("\n".join(result.other_files[:60])))
        self._html("value", rt.document(*parts))

    def paint_leaf(self, entry):
        """One leaf of the current level, whole and bounded.

        The value is taken from the level already in hand, so reading a
        leaf costs nothing beyond what the level cost."""
        rt, trace = self.m.richtext, self.m.trace
        result = self.trace_result
        try:
            value = trace.walk(result.value, (entry.step,))
        except trace.PathError as exc:
            self._html("value", rt.document(rt.paragraph(str(exc), "bad")))
            return
        leaf = trace.render_leaf(value, tuple(result.path) + (entry.step,))
        parts = [rt.heading("%s / %s" % (trace.breadcrumb(result.path,
                                                          result.row.dirname),
                                         entry.key), 3),
                 rt.paragraph("%s, %s" % (entry.kind, entry.size), "dim"),
                 rt.block(leaf.text)]
        if leaf.truncated:
            parts.append(rt.paragraph(leaf.note(), "warn"))
        self._html("value", rt.document(*parts))

    def paint_clip(self):
        rt = self.m.richtext
        facts = self.facts
        context = self.context
        parts = [rt.heading("The clip under the playhead", 2)]
        clip = context.clip or {}
        if not clip:
            parts.append(rt.paragraph(
                "There is no video clip under the playhead.", "dim"))
        else:
            parts.append(rt.table(
                ["", ""],
                [["file", clip.get("name") or "-"],
                 ["timeline frames", "%s - %s" % (clip.get("start"),
                                                  clip.get("end"))],
                 ["source frames", "%s + %s" % (clip.get("left_offset"),
                                                clip.get("duration"))],
                 ["catalog id", facts.clip_id or "not in the catalog"]]))
        if facts.observations:
            parts.append(rt.heading("What the vision pass observed", 3))
            parts.append(rt.table(
                ["", ""],
                [[key.replace("_", " "), facts.observations[key]]
                 for key in ("description", "activity", "framing", "stability",
                             "movement", "content_type", "usable_ranges",
                             "subjects")
                 if facts.observations.get(key)]))
        if facts.speech_coverage:
            parts.append(rt.heading("Speech", 3))
            parts.append(rt.paragraph("coverage: %s" % facts.speech_coverage))
        if facts.transcript:
            parts.append(rt.block("\n".join(
                "[%6.2f-%6.2f] %s" % (r["start"], r["end"], r["text"])
                for r in facts.transcript)))
        if facts.placement:
            parts.append(rt.heading("What put this clip here", 3))
            parts.append(rt.table(
                ["", ""],
                [["track", facts.placement.get("track")],
                 ["label", facts.placement.get("label")],
                 ["decided by", facts.placement.get("decided_by")],
                 ["basis", facts.placement.get("basis")],
                 ["why that step", facts.placement.get("why_this_step") or "-"]]
                + ([["cutaway window", facts.window_basis]]
                   if facts.window_basis else [])
                + ([["own audio", "never heard - placed video only"]]
                   if facts.video_only else [])))
            if facts.reasoning_routes:
                parts.append(rt.paragraph("the reasoning on disk:", "dim"))
                parts.append(rt.block("\n".join(
                    "%-10s %s" % (name, path) for name, path
                    in sorted(facts.reasoning_routes.items()))))
        if facts.absences:
            parts.append(rt.heading("What could not be joined", 3))
            parts.append(rt.paragraph(
                "These are absences, not measurements of nothing.", "dim"))
            parts.append(rt.block("\n".join("- " + a for a in facts.absences)))
        if context.markers:
            parts.append(rt.heading("Markers you typed on this timeline", 3))
            parts.append(rt.block("\n".join(
                "frame %-6s [%s] %s | %s"
                % (m.get("frame"), m.get("color", ""), m.get("name", ""),
                   m.get("note", "")) for m in context.markers)))
        self._html("clipbody", rt.document(*parts))

    def paint_answer(self, result):
        rt = self.m.richtext
        if not result:
            return
        self._set("askstate", "Text",
                  "answered in %ss by %s" % (result.get("seconds"),
                                             result.get("model")))
        self._html("clipbody", rt.document(
            rt.heading("Answer" if result.get("ok") else "The call failed", 2),
            rt.markdown(result.get("text", "")),
            rt.heading("how it was asked", 3),
            rt.paragraph(
                "%s characters of context, %ss round trip, on a worker "
                "thread. The panel holds no API key: it shelled out to an "
                "already-authenticated CLI."
                % (result.get("prompt_chars"), result.get("seconds")), "dim"),
            rt.heading("the context that was attached", 3),
            rt.block(self.context_block())))

    def paint_plan(self):
        rt = self.m.richtext
        preview = self.preview
        if preview is None:
            return
        self.paint_profiles()
        parts = []
        if preview.profile_error:
            parts += [rt.heading("This profile cannot be used", 2),
                      rt.block(preview.profile_error)]
        elif preview.breakpoint_error:
            parts += [rt.heading("These breakpoints cannot be armed", 2),
                      rt.block(preview.breakpoint_error)]
        elif preview.refusal:
            parts += [rt.heading("REFUSED - before the run starts", 2),
                      rt.lead("Nothing has been deleted, saved or executed. "
                              "This is the same refusal the runner would "
                              "make, made here instead."),
                      rt.block(preview.refusal)]
        else:
            profile = preview.profile
            if profile.is_declared:
                parts += [rt.heading(profile.name, 2),
                          rt.lead(profile.description),
                          rt.paragraph("%s, %s" % (
                              profile.source,
                              "adopted by project.yaml" if profile.adopted
                              else "chosen here"), "dim")]
            else:
                parts += [rt.heading("A plain full run", 2),
                          rt.lead("No profile. Every step this pipeline runs "
                                  "by default.")]
            gates = preview.breakpoints
            parts.append(rt.heading("Where it stops", 3))
            parts.append(rt.block("\n".join(gates.describe(
                preview.steps_to_run))))
            estimate = preview.estimated_seconds or {}
            parts.append(rt.paragraph(
                "Estimated %ss of work selected, %ss skipped. An estimate, "
                "and named as one - the real number is what the ledger "
                "recorded last time."
                % (estimate.get("selected"), estimate.get("skipped")), "dim"))
            parts.append(rt.heading("Will run (%d)"
                                    % len(preview.steps_to_run), 3))
            parts.append(rt.block("\n".join(preview.steps_to_run)))
            if preview.skipped:
                parts.append(rt.heading("Will NOT run (%d), and why"
                                        % len(preview.skipped), 3))
                parts.append(rt.table(
                    ["step", "reason"],
                    [[step, preview.reasons.get(step, "")]
                     for step in preview.skipped]))
            if preview.default_off:
                parts.append(rt.paragraph(
                    "Off by default: " + ", ".join(preview.default_off),
                    "dim"))
            if preview.from_cache:
                parts.append(rt.heading("Satisfied from a previous run", 3))
                parts.append(rt.table(
                    ["producer", "still feeding"],
                    [[producer, ", ".join(consumers)]
                     for producer, consumers in preview.from_cache.items()]))
            if preview.from_external:
                parts.append(rt.heading("Supplied from outside the pipeline",
                                        3))
                parts.append(rt.table(
                    ["state key", "feeding"],
                    [[key, ", ".join(consumers)]
                     for key, consumers in preview.from_external.items()]))
        self._html("planbody", rt.document(*parts))
        self.paint_run_state()

    def paint_profiles(self):
        tree = self.itm["profiles"]
        try:
            tree.Clear()
        except Exception:                           # noqa: BLE001
            pass
        adopted = self.m.run_view.adopted_profile(self.project_folder)
        item = tree.NewItem()
        item.Text[0] = "(none)"
        item.Text[1] = "-"
        tree.AddTopLevelItem(item)
        for entry in self.m.run_view.available_profiles(self.project_folder):
            item = tree.NewItem()
            item.Text[0] = entry.name
            item.Text[1] = ("adopted" if entry.name == adopted
                            else entry.source)
            tree.AddTopLevelItem(item)

    def paint_run_state(self):
        """Whether a run is going, in words, and the mode LABELLED.

        This read `no run up  resume, manual LLM, breakpoints at scan` -
        which is not English anyone reads at a glance, and which sat a
        finished run's settings next to "no run up" as though they
        described something happening now. `pipeline_run.json` is the
        LAST run's account of itself, so it is named as that.
        """
        rt = self.m.richtext
        status = self.m.run_view.run_state(self.project_folder)
        live = status.get("live_pid")
        mode = status.get("mode") or ""
        if live:
            text = ("<span style='color:%s'>A run is going</span> (pid %s)"
                    % (rt.GOOD, live))
            if mode:
                text += " &#160;-&#160; %s" % rt.esc(mode)
        else:
            text = ("<span style='color:%s'>No run is going.</span>"
                    % rt.INK_DIM)
            if mode:
                text += ("<span style='color:%s'> &#160;Last run: %s</span>"
                         % (rt.INK_DIM, rt.esc(mode)))
        if status.get("hold"):
            text += (" &#160; <span style='color:%s'>handbrake engaged - the "
                     "step in flight finishes, then it stops</span>" % rt.WARN)
        if status.get("paused_at_gate"):
            text += (" &#160; <span style='color:%s'>paused at a breakpoint "
                     "after %s - answer it on the Gates tab</span>"
                     % (rt.WARN, rt.esc(status["paused_at_gate"])))
        self._set("runstate", "Text", text)

    def paint_gates(self):
        rt = self.m.richtext
        entries = self.m.run_view.gates(self.project_folder)
        self._set("gatehead", "Text",
                  "Review gates - %d, %d pending"
                  % (len(entries),
                     sum(1 for g in entries if g.status == "pending")))
        tree = self.itm["gatelist"]
        try:
            tree.Clear()
        except Exception:                           # noqa: BLE001
            pass
        for gate in entries:
            item = tree.NewItem()
            item.Text[0] = gate.step_id
            item.Text[1] = gate.status
            self._colour(item, 1, rt.WARN if gate.status == "pending"
                         else rt.INK_DIM)
            tree.AddTopLevelItem(item)
        self.gates_seen = entries

    def paint_gate(self):
        rt = self.m.richtext
        entries = self.gates_seen
        gate = next((g for g in entries if g.step_id == self.gate), None)
        if gate is None:
            self._html("gatebody", rt.document(
                rt.heading("No gate selected", 2),
                rt.lead("A gate appears here when a run stops at an armed "
                        "breakpoint. Arm one with `--break <step>` or with a "
                        "run profile's `breakpoints:` list.")))
            return
        snapshot = self.m.review_gate.load_gate_snapshot(self.project_folder,
                                                         gate.step_id)
        parts = [rt.heading("%s - %s" % (gate.step_id, gate.status), 2),
                 rt.paragraph("snapshot taken %s" % (gate.created_at or "-"),
                              "dim")]
        if snapshot is not None:
            output = snapshot.step_output or {}
            entries_, withheld = self.m.trace.entries(output)
            parts.append(rt.heading("What the step produced", 3))
            parts.append(rt.table(
                ["key", "kind", "size", "preview"],
                [[e.key, e.kind, e.size, e.preview] for e in entries_]))
            if withheld:
                parts.append(rt.paragraph("%d more keys not listed."
                                          % withheld, "warn"))
            parts.append(rt.paragraph(
                "Inputs the step was handed: "
                + (", ".join(sorted(snapshot.upstream_context or {}))
                   or "(none recorded)"), "dim"))
            parts.append(rt.paragraph(
                "The snapshot records the NAMES of the upstream inputs and "
                "not their values. Open the producing step on the Trace tab "
                "to read what they held.", "dim"))
        if gate.feedback_action:
            parts.append(rt.heading("Answered", 3))
            parts.append(rt.paragraph("%s - %s" % (gate.feedback_action,
                                                   gate.feedback_text or "")))
        self._html("gatebody", rt.document(*parts))

    def build_strip(self):
        state = self.state or {}
        outputs = state.get("step_outputs") or {}
        manifest = ((outputs.get("compile_manifest") or {})
                    .get("assembly_manifest") or {})
        spine = (((outputs.get("mesh_spine") or {}).get("timed_spine") or {})
                 .get("structure") or [])
        lanes = self.m.strip.lanes_from_manifest(manifest, spine)
        playhead = self.m.strip.timecode_seconds(self.context.timecode,
                                                 self.context.fps)
        return self.m.strip.draw(self.strip_path, lanes,
                                 [row.status for row in self.rows],
                                 playhead_seconds=playhead,
                                 width=STRIP_WIDTH)

    def paint_strip(self, drawn):
        rt = self.m.richtext
        if drawn is None:
            return
        self._set("striphead", "Text",
                  "<b>%.1fs</b> of planned timeline &#160;|&#160; the white "
                  "line is the live playhead &#160;|&#160; the ribbon is the "
                  "%d step directories"
                  % (drawn.total_seconds, len(self.rows)))
        self._html("stripbody", rt.document(
            rt.image(drawn.path, drawn.width),
            rt.heading("What this is", 2),
            rt.lead("The lanes are the PLAN, read off the assembly manifest "
                    "the last build compiled. The white line is Resolve's "
                    "live playhead. They agree only while the timeline on "
                    "screen is the one that manifest built."),
            rt.table(["lane", "spans"],
                     [[lane.name, str(lane.count)] for lane in drawn.lanes]),
            rt.paragraph(
                "Encoded by the panel, in this process, from zlib and "
                "struct: %s, %d bytes. No Pillow, no matplotlib, no server, "
                "no browser. Drop-frame timecode is not handled."
                % (drawn.path, drawn.bytes_written), "dim")))

    def paint_launch(self, launch):
        rt = self.m.richtext
        if launch is None:
            return
        self.log_path = launch.log_path or self.log_path
        self._set("runstate", "Text", rt.esc(launch.message))
        if launch.argv:
            self._html("planbody", rt.document(
                rt.heading("Started", 2),
                rt.lead(launch.message),
                rt.block(" ".join(launch.argv))))

    # ───────────────────────────────────────────────────────── the loop ──
    def reload(self):
        if not self.project_folder:
            return
        self._set("runhead", "Text", "re-reading on a worker thread...")
        self.spawn("state", self._read_project)

    def ensure_items(self):
        """Re-read the timeline's items when the TIMELINE changes.

        Not per tick: a hundred scripting calls twice a second would be
        the panel making Resolve's own queue its problem, for a list
        that only changes when the edit does. `Reload` forces it.
        """
        name = self.context.timeline or ""
        if not name or (name == self.items_for and self.items):
            return
        if any(tag == "items" for tag, _ in self._in_flight):
            return
        self._in_flight.append(("items", name))
        self.spawn("items", self.read_items, name)

    def read_items(self, name):
        """A worker: `GetItemListInTrack` over every video track."""
        try:
            project = self.resolve.GetProjectManager().GetCurrentProject()
            timeline = project.GetCurrentTimeline() if project else None
            return name, read_video_items(timeline)
        finally:
            self._in_flight[:] = [pair for pair in self._in_flight
                                  if pair[0] != "items"]

    def _read_project(self):
        state = self.m.trace.read_state(self.project_folder)
        return state, self.m.trace.step_rows(self.project_folder, state)

    def discover_project(self):
        try:
            project = self.resolve.GetProjectManager().GetCurrentProject()
            folder = (find_project_from_timeline(project.GetCurrentTimeline())
                      if project else None)
        except Exception:                           # noqa: BLE001
            folder = None
        return folder or os.environ.get("VEP_PANEL_PROJECT") or ""

    def run(self, seconds=None, on_beat=None):
        self.win.Show()
        self.show_page(PAGE_TRACE)
        self.context = read_context(self.resolve,
                                    self.m.clip_context.ResolveContext)
        self.paint_ctx()
        self.project_folder = self.discover_project()
        if self.project_folder:
            self._set("runhead", "Text", "found the project from the "
                                         "timeline: %s - reading..."
                      % self.project_folder)
            self.spawn("state", self._read_project)
        else:
            self._set("runhead", "Text",
                      "No pipeline project found by walking up from this "
                      "timeline's clips. Open a timeline built from a "
                      "project, or set VEP_PANEL_PROJECT.")

        last_context = 0.0
        while self.running:
            now = time.time()
            if seconds and now - self.started > seconds:
                break
            try:
                self.disp.StepLoop(False)
            except Exception:                       # noqa: BLE001 - StepLoop
                pass                                # raises KeyError 'On' for
            self.drain()                            # an unhandled event
            self.beats += 1
            if now - last_context > 0.5:
                last_context = now
                self.context = read_context(
                    self.resolve, self.m.clip_context.ResolveContext)
                self.ensure_items()
                self.resolve_picture()
                self.paint_ctx()
                if (self.state is not None
                        and self.clip_key() != self.joined_key):
                    self.refresh_clip()
                self._set("beat", "Text",
                          "<span style='color:%s'>panel loop: %d passes, "
                          "%.0fs, %d job(s) in flight - if this stops moving "
                          "the panel is frozen</span>"
                          % (self.m.richtext.INK_DIM, self.beats,
                             now - self.started, self.jobs))
                with open(self.beat_log, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps({
                        "t": round(now - self.started, 3),
                        "beats": self.beats, "jobs": self.jobs}) + "\n")
            if on_beat:
                on_beat(self)
            time.sleep(0.02)
        self.win.Hide()
        return self.beats


class _Modules(object):
    """The panel package, gathered once so the widget layer holds one
    reference rather than a dozen imports scattered through it."""

    def __init__(self):
        from library.tools import review_gate
        from library.tools.panel import (clip_context, richtext, run_view,
                                         strip, trace)
        self.clip_context = clip_context
        self.richtext = richtext
        self.run_view = run_view
        self.strip = strip
        self.trace = trace
        self.review_gate = review_gate


def _leave(status):
    """Exit WITHOUT running the C runtime's static destructors.

    Blackmagic's `fusionscript.so` does not join its own `RemoteApp`
    thread before its static destructor frees the pool that thread is
    using, so a process that connected to Resolve can segfault on the
    way OUT - after its work is finished, with nothing of ours running.
    Measured from the crash report: the main thread is inside
    `exit -> __cxa_finalize_ranges -> Fusion::ReusePoolManager::~ReusePoolManager`
    while thread `RemoteApp` is in `Fusion::RemoteApp::DispatchPacket`
    dereferencing freed memory. The captain sees "Python quit
    unexpectedly" and has every reason to read it as the panel dying.

    `os._exit` hands the status straight to the kernel, so
    `__cxa_finalize_ranges` never runs and the race has nothing to lose.
    That is safe HERE and nowhere else: everything the panel writes -
    the strip, the heartbeat log, the gate feedback - is closed at the
    point it is written, and the streams are flushed on the line above.
    It is not a fix to Blackmagic's library; it is declining to be in
    the room when it tears itself down.
    """
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:                               # noqa: BLE001
        pass
    os._exit(status)


def main():
    if not ROOT:
        print("VEP Pipeline Panel: cannot find the video editing pipeline "
              "repository. Re-run scripts/install_resolve_scripts.sh from "
              "the checkout, or set VEP_REPO_ROOT.")
        return 3
    try:
        import DaVinciResolveScript as dvr
    except ImportError:
        print("VEP Pipeline Panel: DaVinci Resolve's scripting modules are "
              "not importable. Is Resolve installed?")
        return 3

    resolve = dvr.scriptapp("Resolve")
    if resolve is None:
        print("VEP Pipeline Panel: Resolve is not running, or scripting is "
              "disabled in Preferences > System > General.")
        return 2
    ui = resolve.Fusion().UIManager
    dispatcher = dvr.UIDispatcher(ui)
    panel = Panel(resolve, ui, dispatcher, _Modules())
    seconds = (float(os.environ["VEP_PANEL_SECONDS"])
               if os.environ.get("VEP_PANEL_SECONDS") else None)
    beats = panel.run(seconds=seconds)
    print("VEP Pipeline Panel closed after %d loop passes" % beats)
    return 0


if __name__ == "__main__":
    _leave(main())
