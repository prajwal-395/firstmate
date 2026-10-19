# Remaining Resolve route evaluation: geometry, cuts and Fairlight

Evaluated against the installed Resolve 21.1 scripting reference, the
repository's AXI backends, the installed Blackmagic MCP tool inventory, and
the existing Fairlight UI recipes. This is route discovery, not qualification;
no live Resolve mutation or qualification run was performed.

## Route order and findings

| Operation family | Native scripting | Blackmagic MCP mutation | Fairlight/API and UI | Current disposition |
|---|---|---|---|---|
| `overlay.resize` | `TimelineItem.SetProperties` / `GetProperties` expose Pan, Tilt, ZoomX and ZoomY. `layout_model` can lower output geometry, and `overlay_verify` can measure rendered output. This is the first promising route, but no adapter currently joins all three with staged promotion. | The installed bundle has `run_script` and `run_script_unsafe`, which execute arbitrary code. Neither provides semantic authorization, collateral checks, recovery or operation receipts. Do not add as a backend. | Fairlight is unrelated. Workflow Integration adds a host wrapper over the same API, not an independent transform path. | Build a staged native transform adapter with rendered pixel evidence. Keep production routing closed until live qualification. |
| `clip.split` | The installed `TimelineItem` reference has no split method. The composed native route can delete/place on a staging copy, but preserving grades, Fusion state, links and exact two-span identity needs an adapter and proof. | Arbitrary `run_script` has the same AXI contract gap and does not add a split primitive. | Fairlight is unrelated; Workflow Integration exposes the same scripting surface. | Staged delete/place reconstruction is the first candidate, not yet implemented or qualified. |
| `clip.slide` | No native Slide method. Reconstructing the target plus both neighboring source windows is expressible with staged delete/place, but existing move only relocates the target and does not implement a slide. | No operation-specific semantics or independent preservation guarantees. | Fairlight is unrelated; Workflow Integration exposes the same scripting surface. | Compose all three items on staging; verify exact source/record spans and full collateral state before promotion. |
| `clip.roll` | No native Roll method. Staged delete/place can express the shared-cut source-window changes, but the current touch-up path has no shared-cut composition. | No operation-specific semantics or independent preservation guarantees. | Fairlight is unrelated; Workflow Integration exposes the same scripting surface. | Compose both items on staging; verify both source spans, unchanged record spans and collateral state before promotion. |
| `audio.gain` / `audio.fade` | OTIO mix is the existing gain route; `TimelineItem.SetFades` / `GetFades` is the existing native fade route. The route adapters are protected staged candidates and still need rendered audio qualification. Native audio properties have unclear key/unit semantics for gain. | Arbitrary script execution does not improve read-back, rendered audio evidence or recovery. | The current Fairlight API reference and `fairlight_eq.py` cover preset/normalization helpers, not semantic EQ-band/dynamics controls. UI recipes use anchored AX/OCR locators and clicks; they remain the last route and require exact control-state and rendered-audio verification plus compensation. No equivalent supported backend has been established. | Keep gain/fade route evaluations distinct from Fairlight EQ/dynamics. No Fairlight adapter accepted. |

## Decision

The route order is native/composed scripting first, specialized AXI or
interchange paths where their preservation contract fits, MCP only when it adds
a typed and verifiable capability, and UI last. For these four requested
operations, no direct native one-call operation exists for split/slide/roll;
their first viable candidate is staged composition. For resize, documented
native setters exist, but a transformation write is not sufficient without
independent rendered-pixel evidence. The MCP script tools are not safe
operation backends. Fairlight EQ/dynamics remains UI-only among the routes
examined, and is not qualified for agent routing.

## Evidence pointers

- Installed Resolve reference: `/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/DaVinciResolveScript.pyi`.
- MCP inventory and AXI boundary: `docs/RESOLVE_API_CAPABILITY_INVENTORY.md`.
- Fairlight/UI coverage: `docs/RESOLVE_UI_API_COVERAGE.md` and `library/tools/fairlight_eq.py`.
- Staged composition: `library/tools/composed_edit.py` and `library/tools/reel_touchup.py`.
- Geometry and rendered verification: `library/tools/layout_model.py`, `library/tools/overlay_placement.py`, and `library/tools/overlay_verify.py`.
