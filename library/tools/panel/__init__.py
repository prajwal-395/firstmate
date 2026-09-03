"""The Resolve panel's logic, with no Qt and no Resolve in it.

`resolve_scripts/VEP Pipeline Panel.py` is the entry point Resolve calls:
it builds the widgets, owns the event loop and talks to the scripting
API.  Everything it DECIDES lives here, so that half is ordinary Python
that ordinary tests can drive - which is the whole reason the split
exists.  The widget layer is covered by looking at it and by the
responsiveness measurement in the PR; this half is covered by
`tests/test_panel_*.py`.

The rule that keeps the split honest: **nothing in this package may
import Qt, `DaVinciResolveScript` or `BlackmagicFusion`, and nothing in
it may call Resolve.**  Live Resolve facts arrive as a plain dict the
entry point assembles (`clip_context.ResolveContext`), so a test can
hand one over without an application running.
`tests/test_panel_is_importable_without_resolve.py` enforces it.

The other rule, from the scout's own structural recommendation: the
panel IMPORTS the repository's modules rather than keeping a second copy
of the rules.  The prototype resolved a step directory to a node id by
taking the longest match against the ids in the state file, and gave
three steps the wrong status and hid one of two failures on 001.
`project_layout.node_id_for` is the one translator and this package
calls it.


Rules relocated from AGENTS.md 15
---------------------------------
These are the engine's rules for this module.  They lived in
AGENTS.md until it was split by subsystem; the wording is unchanged,
so each rule is findable by its own words, and AGENTS.md 15
keeps the headline and points here.

**The pipeline is readable from inside Resolve, and the panel's whole reason to exist is that it knows where the PLAYHEAD is.**
`resolve_scripts/VEP Pipeline Panel.py` is the entry point; everything it DECIDES is in `library/tools/panel/`, which imports no Qt and no Resolve. [why](docs/RULE_EVIDENCE.md#the-panel-handed-the-model-a-filename)
- **The split is the rule.** Nothing under `library/tools/panel/` may import `DaVinciResolveScript`, `BlackmagicFusion` or a Qt binding; live Resolve facts arrive as `clip_context.ResolveContext`. `tests/test_panel_boundary.py`.
- **The clip under the playhead is joined to what the pipeline measured** - the catalog id, the vision observations, the transcript of the seconds that PLAY, and which step chose the placement (`timeline_decisions`). Every fact is a READING, never a document pasted in, and what could not be joined is SAID.
- **The picture is not the topmost item.** Use `clip_context.picture_at` (highest track carrying FOOTAGE), not `GetCurrentVideoItem()`. `clip_context.picture_at` takes the highest track carrying FOOTAGE and `overlays_at` reports the rest.
- **What the captain is LOOKING AT outranks what they last clicked**, and the prompt says which is which. `prompt_block` is bounded and says what it cut.
- **A large output is drilled down, never dumped.** `panel/trace.py` navigates one LEVEL at a time; a path that does not exist is refused by name.
- **The run is previewed before it starts**: the profile, the steps it will and will not fire with each reason, and the `run_scope` refusal VERBATIM. `panel/run_view.py` calls the same resolver the runner does and has no opinion of its own.
- **The panel launches the runner with the checkout's `.venv/bin/python3`, never `sys.executable`.** A checkout with no venv is REFUSED by name.
- **The handbrake stays advisory** and the panel never kills the runner.
- **It holds no credential**: the model is reached by shelling out to the already-authenticated `claude` CLI, overridable with `VEP_PANEL_MODEL`. Nothing secret is written into Resolve's application-support folder.
- **The FRAME under the playhead goes with the question.** `library/tools/panel/frame_attach.py` decides placement; `marker_capture.grab_still` is the ONE grabber. Stills go to `~/.vep_panel/frames/`, never under the project. A grab that cannot happen degrades to text-only with a stated reason. `VEP_PANEL_NO_FRAME=1` declines it. [why](docs/RULE_EVIDENCE.md#the-model-was-told-a-filename-and-not-shown-the-frame)
- **The model call runs where it can READ what the prompt points at.** The CLI will not open a file outside its working directory, and the panel's call set none, so it inherited whatever Resolve was launched with. The failure that comes out is the model answering *"I need permission to read the screenshot file"* - exit code 0, a plausible sentence, and nothing that looks like a bug. `frame_attach.call_site` names the directory and `frame_attach.reaches_the_file` is what the test pins, so the guarantee is *the call can read the still* and not *the call sets cwd* - `--add-dir` satisfies it too. Any future feature that hands the model a path depends on this. `tests/test_panel_frame_attach.py`.
- **Every slow thing goes on a worker thread** (`StepLoop(False)`), and the heartbeat under the header is proof it has not stuck.
- Toolkit facts that bite, all measured: `hasattr` is True for widgets that do not exist; `Stack.CurrentIndex` is broken and `Hidden` is the page switch; `Label.Pixmap` draws nothing and `<img>` in a read-only `TextEdit` does; `ui.Timer` never fires; `MinimumSize` is ignored by the layout and a stretch RATIO is not; Qt decides a string is rich text by looking for a tag.
- **A process that connected to Resolve leaves through `os._exit`, never `sys.exit`.** `fusionscript.so` does not join its own `RemoteApp` thread before its static destructor frees the pool that thread is using, so the C runtime's teardown can SEGFAULT. The panel's `_leave` flushes both streams and hands the status to the kernel.
- **A column is sized to the longest value it really holds, and a truncation may never read as a word.** A column that cannot be widened is dropped whole to the detail pane.
- **A screenshot of this panel is opened and read before it is committed.** Nothing else tells a picture of the panel from a picture of what was behind it; `docs/panel/README.md` records the one that got through.
- **Footage Search is deliberately not in the panel.** `library/dashboard/footage_search.py` is the only authorised caller of the footage index (§2) and widening that is the captain's call.
"""
