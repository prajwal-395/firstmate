"""The caption swap reaches Resolve through the environment's Scripting dir.

`_resolve_live_project` imports `DaVinciResolveScript` when the swap half
of `rerender_and_swap` runs. `RESOLVE_SCRIPT_API` names the Scripting
directory (AGENTS.md 9) and the module lives in `Modules` beneath it -
every other consumer in the tree appends it. This step used the value
verbatim, so with the injected environment the import was attempted in a
directory holding no module and the swap reported "Resolve scripting is
unavailable" while Resolve was open (caption-text wave, 2026-09-20).
These tests pin the directory resolution without Resolve: the injected
value, an explicit modules value, and an end-to-end import off a stub
module through the resolved path.
"""

from __future__ import annotations

import importlib
import sys

import library.steps.step_4_05_render_subtitles.step as r405

SCRIPTING = ("/Library/Application Support/Blackmagic Design/"
             "DaVinci Resolve/Developer/Scripting")




def test_the_scripting_dir_gains_modules_and_a_stub_imports_through_it(
        tmp_path, monkeypatch):
    # The vep-env value: the Scripting directory, not the module.
    monkeypatch.setenv("RESOLVE_SCRIPT_API", SCRIPTING)
    assert r405._script_modules_dir() == SCRIPTING + "/Modules"

    scripting = tmp_path / "Scripting"
    modules = scripting / "Modules"
    modules.mkdir(parents=True)
    (modules / "DaVinciResolveScript.py").write_text(
        "MARKER = 'stub'\n", encoding="utf-8")
    monkeypatch.setenv("RESOLVE_SCRIPT_API", str(scripting))
    monkeypatch.syspath_prepend(r405._script_modules_dir())
    monkeypatch.delitem(sys.modules, "DaVinciResolveScript",
                        raising=False)
    try:
        module = importlib.import_module("DaVinciResolveScript")
        assert module.MARKER == "stub"
    finally:
        sys.modules.pop("DaVinciResolveScript", None)
