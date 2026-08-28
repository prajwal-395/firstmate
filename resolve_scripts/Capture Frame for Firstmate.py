"""Workspace > Scripts > Capture Frame for Firstmate.

Playhead on the moment, one click, done.  The frame the captain is
looking at is exported to `<project>/marker_feedback/stills/`, and a
record pointing at it is written into the marker's `customData` - making
the marker if there is none there, updating it if there is, and never
touching what the captain typed.

This file is the entry point Resolve calls.  Everything it does lives in
`library/tools/marker_capture.py` in the repository, so the copy sitting
in the captain's Scripts folder cannot drift from the source of truth:
`scripts/install_resolve_scripts.sh` puts it there and stamps the
repository's location into `REPO_ROOT` below.  Nothing else in the
pipeline writes into the application support folder, ever.

── The window, and what was measured of it ─────────────────────────────

A script launched from Workspace > Scripts writes to a console the
captain does not have open, so the result - and above all a failure -
needs a window.  Measured on Resolve Studio 21.0.0b.28: `fusion.UIManager`
answers, `UIDispatcher` builds, `AddWindow` with this layout returns a
window whose `GetItems()` carries `vepCapture`, `Body` and `Close`, and
`Body.PlainText` round-trips the text.

`RunLoop` was NOT provable from outside Resolve's own script host:
`BlackmagicFusion` is injected there and does not import in a plain
interpreter, and the dispatcher's loop driven from a foreign process did
not return.  So the window is built AFTER the capture is finished and
committed, and `_show` swallows its own exceptions: whatever the loop
does, it cannot lose a frame that is already on disk and already on the
marker.  That ordering is the guarantee, not the window.
"""

import os
import sys
import traceback

REPO_ROOT = ""
"""Filled in by scripts/install_resolve_scripts.sh.  Left empty when this
file is run from inside the checkout, where it finds itself."""


def _repo_root():
    for candidate in (
        REPO_ROOT,
        os.environ.get("VEP_REPO_ROOT", ""),
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ):
        if candidate and os.path.isfile(
            os.path.join(candidate, "library", "tools", "marker_capture.py")
        ):
            return candidate
    return ""


def _show(title, body, ui=None, dispatcher=None):
    """Say what happened, on screen if there is a UI and on stdout always.

    A script launched from Workspace > Scripts writes to a console the
    captain does not have open, so a failure with no window is a failure
    nobody sees.

    Called only AFTER the capture, and it swallows its own failures: a
    window that will not build must not be able to lose a frame that is
    already on disk and already on the marker.
    """
    print(f"{title}\n{body}")
    if ui is None or dispatcher is None:
        return
    try:
        _window(title, body, ui, dispatcher)
    except Exception:
        traceback.print_exc()


def _window(title, body, ui, dispatcher):
    window = dispatcher.AddWindow(
        {
            "ID": "vepCapture",
            "WindowTitle": title,
            "Geometry": [200, 200, 640, 260],
        },
        [
            ui.VGroup(
                [
                    ui.TextEdit(
                        {
                            "ID": "Body",
                            "Text": body,
                            "ReadOnly": True,
                            "Font": ui.Font({"Family": "Menlo",
                                             "MonoSpaced": True}),
                        }
                    ),
                    ui.Button({"ID": "Close", "Text": "Close"}),
                ]
            )
        ],
    )
    items = window.GetItems()
    items["Body"].PlainText = body

    def close(ev):
        dispatcher.ExitLoop()

    window.On.vepCapture.Close = close
    window.On.Close.Clicked = close
    window.Show()
    dispatcher.RunLoop()
    window.Hide()


def main():
    ui = dispatcher = None
    try:
        fu = fusion  # noqa: F821 - injected by Resolve's script host
    except NameError:
        fu = None
    if fu is not None:
        # Only when Resolve's own host injected `fusion`: run standalone
        # there is nobody looking at a window, and stdout is the right
        # place. The dispatcher comes from the injected `bmd`, falling
        # back to the library it is a view onto.
        try:
            ui = fu.UIManager
            try:
                factory = bmd.UIDispatcher  # noqa: F821 - injected too
            except NameError:
                import fusionscript
                factory = fusionscript.UIDispatcher
            dispatcher = factory(ui)
        except Exception:
            traceback.print_exc()
            ui = dispatcher = None

    root = _repo_root()
    if not root:
        _show(
            "Capture Frame - not installed",
            "This script cannot find the video editing pipeline "
            "repository.\n\nRe-run scripts/install_resolve_scripts.sh from "
            "the checkout, or set VEP_REPO_ROOT.",
            ui, dispatcher,
        )
        return 3
    if root not in sys.path:
        sys.path.insert(0, root)

    from library.tools.marker_capture import CaptureError, capture
    from library.tools.marker_feedback import ResolveUnavailable, current_timeline

    try:
        timeline, project = current_timeline(globals().get("resolve"))
        result = capture(timeline, project)
    except (CaptureError, ResolveUnavailable) as exc:
        _show("Capture Frame - nothing was captured", str(exc), ui, dispatcher)
        return 2
    except Exception:
        _show("Capture Frame - failed", traceback.format_exc(), ui, dispatcher)
        return 1

    body = result.summary()
    if result.gallery_note:
        body += f"\n\nNOTE: {result.gallery_note}"
    _show("Capture Frame - captured", body, ui, dispatcher)
    return 0


if __name__ == "__main__":
    sys.exit(main())
