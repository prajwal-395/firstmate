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
"""
