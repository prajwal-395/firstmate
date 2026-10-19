# Resolve UI capability matrix

This matrix records what the UI driver can reach, what the native API
documents, and what has actually been exercised live. It is an evidence
snapshot, not a promise that every Resolve release or layout behaves the
same. Resolve evidence below is from Resolve Studio 21 on one macOS 26
machine, measured October 5-6, 2026. The exact Resolve patch build used in
the UI probe was not recorded; `21.1.00014` in the API-index example is not
asserted as the UI probe build. The probe is summarized in
[`RESOLVE_CONTROL_PROBE.md`](RESOLVE_CONTROL_PROBE.md). Native method names
are documented in the installed scripting reference and indexed by
`library/tools/resolve_api_capabilities.py`; a documented method is not
live qualification.

Status meanings:

- **Native API**: a documented native method family is known. Runtime
  semantics still need read-back and may be context-dependent.
- **UI path**: a concrete measured locator/action route exists. AX menu
  paths are background-safe; Qt controls need explicitly announced
  foreground input.
- **Live-qualified**: a live proof exercised the named operation and
  checked its state. A probe of visibility alone does not qualify a write.
- **Missing**: no demonstrated route or verification is known. A proposed
  route is marked speculative and is not production-enabled.

## Page coverage

| Page / control function | Native API | UI path and observed location | Preferred backend | State needed before acting | Verification contract | Live-qualified / missing |
|---|---|---|---|---|---|---|
| Media - bins, pool items, metadata | `MediaPool` and `MediaPoolItem` methods; see installed API index | Page body and pool rows are Qt-drawn; AX tree is the menu bar | Native API for supported pool operations | Exact project and pool item identity; refresh pool state | Re-read item identity, bin and properties through API | No Media-page UI write was qualified. Direct Qt row locators are unqualified. |
| Cut - page selection | `Resolve.OpenPage`; `GetCurrentPage` | `Workspace > Switch to Page > Cut` | Native `OpenPage` for workflow code; AX path for UI transaction | Exact open project; observe current page | Poll `GetCurrentPage()` until the requested page is returned | Page switching was exercised and read back on Resolve Studio 21. UI probe patch build was not recorded. Cut editing controls are not thereby qualified. |
| Cut - timeline assembly/editing surface | Timeline / TimelineItem methods cover selected structural edits | Timeline canvas and edit controls are Qt-drawn; foreground screen required for coordinate input | Native API or existing typed executor when its operation is registered; UI only for a specifically qualified gap | Exact project/timeline, fresh item identity, page, playhead and track state | Read back target, source, timing, track and collateral changes; `rendered_pixels` can sample output at the target item start; rendered-audio check remains external | No Cut-page UI edit transaction was qualified. Canvas selection, trim handles and UI read-back remain missing. Pixel verifier is unit-tested only, not live-qualified. |
| Edit - page selection and Inspector visibility | `Resolve.OpenPage`, `GetCurrentPage`; no universal Inspector API | `Workspace > Switch to Page > Edit`; `Workspace > Show Panel in Workspace > Inspector` | Native API for page selection; AX menu for Inspector visibility | Exact project; observe page and menu path | Page getter for page changes; confirm Inspector state through a fresh observation if a control exists | Page switch was live-read back; menu inventory measured. Inspector field writes are not qualified. |
| Edit - clip placement, trim, transform | Timeline / TimelineItem APIs and existing Ren executors, operation-specific | Timeline canvas is Qt-drawn; menu commands are AX reachable where present | Registered native operation; UI is a last qualified backend only | Exact timeline and persistent target identity; live generation; source/timing/track state | Read back item identity, source range, record span, track, transform and unrelated-item diff | No general UI transaction is qualified. Drag placement remains foreground-only and unqualified. |
| Fusion - page selection and menu commands | Fusion composition/tool APIs for supported operations | `Workspace > Switch to Page > Fusion`; `Fusion` menu command paths | Native API for registered comp operations; AX for supported menu commands | Exact project/timeline/item; active comp and tool identity | Read back comp/tool state and verify the intended item | Page switch and menu inventory measured. Node graph canvas controls are not qualified. |
| Fusion - tool parameters | `FusionComp`, `FindTool`, `FindInput`, `GetValue` where exposed by the installed version | Qt-drawn controls; custom Qt targets may use OCR/template locators | Native Fusion API when the named input is accessible; otherwise no preferred production backend yet | Resolve version, exact comp/tool/input, fresh UI layout | Read back the same named input; rendered pixels if the visual result matters | No parameter operation was live-qualified. Per-tool locators and version conditions are missing. |
| Color - page and node commands | `Resolve.OpenPage` / `GetCurrentPage`; `TimelineItem.GetNodeGraph` reads graph state | `Workspace > Switch to Page > Color`; `Color > Nodes > ...` | Native API for page and graph read; AX menu command for menu-only node operations | Exact project/timeline/item, current page, selected node/item as required | Read back graph/node state and exact affected item | Page switch and menu inventory measured; node command transactions are not live-qualified here. Node reorder has no measured menu/API route. |
| Color - windows, qualifiers, Magic Mask | Graph reads do not expose equivalent window/qualifier operations | Magic Mask labels are OCR-visible; canvas controls are Qt-drawn; AX exposes menu commands only | No production backend is preferred until an operation is qualified | Exact clip/node, page, Resolve version, fresh OCR/template observation | API read-back where available plus rendered-pixel check; screen text alone is insufficient | No per-operation UI qualification. Geometry, tracking, qualifier values and Magic Mask controls remain missing. |
| Fairlight - page, buses, routing, automation menus | Fairlight project/timeline methods cover selected track, bus and routing state | `Workspace > Switch to Page > Fairlight`; AX menu paths for bus, patch, monitoring and automation | Native API for registered operations; AX for menu-only commands | Exact timeline, track/bus identity, routing and automation state | Re-read track/bus/routing state and unrelated audio changes | Page switch and menu inventory measured; UI routing writes are not qualified. |
| Fairlight - EQ, dynamics, compressor, limiter, gate | Audio item/track APIs expose some audio state; no equivalent EQ-band API was found in the installed reference | Mixer labels partially OCR-visible; controls themselves are Qt-drawn | None until a control locator and read-back are qualified | Exact track, mixer channel, Resolve version and fresh visible control | Read back named parameters and verify rendered audio; OCR labels alone do not verify values | Missing. No stable parameter target/read-back or operation qualification. |
| Deliver - render settings and jobs | `Project.SetRenderSettings`, `AddRenderJob`, `StartRendering`, `GetRenderJobStatus`, `DeleteRenderJob` | `Workspace > Switch to Page > Deliver` | Native render API | Exact timeline, render range, output settings and owned job id | Check settings, job id, terminal job result, cleanup, then output pixels/audio | Existing native render paths check job lifecycle. This does not qualify a UI transaction or replace output verification. |

## Targeting hierarchy and current evidence

`resolve.ui_transaction` uses the following order and refuses a stale or
ambiguous locator:

1. Accessibility element with exact criteria and optional menu `path`.
   The locator is resolved against a freshly-read AX tree; its optional
   Resolve version and actionable-layout fingerprint must still match.
   Multiple matches fail closed. `disambiguation` adds exact AX criteria.
2. Exact OCR text on the current window, when the text is visible and
   unique. A matching label does not prove that a click changes the intended
   control; the transaction must verify the resulting edit state.
3. Image template with measured template provenance and a measured
   threshold. Unmeasured templates do not resolve.
4. Coordinates relative to a measured viewer or window. They require a
   documented reason and are fallback-only in a `qt_control` locator.
5. Absolute screen coordinates are not a locator kind. Window-relative
   points are mapped through the current window; screen coordinates would
   cross the Resolve-window safety boundary.

Custom Qt locator chains can currently use OCR, templates, and measured
window-relative coordinates. Resolve's Qt widgets were observed to be
absent from the AX tree. OCR and template matching are concrete targeting
mechanisms, but an individual control's locator, confidence, version, and
postcondition remain unqualified until a live Ren Qualification run proves
them. Confidence is based on exact unique matches, not an invented model
score; template confidence thresholds must come from measured hit/false-hit
provenance.

## Transaction read-back and restoration

Supported state verifiers read the actual Resolve scripting objects:

- `timeline_item_properties` and `audio_parameters`: exact named values
  from one uniquely resolved timeline item's `GetProperty()` result.
- `color_node_count`: `GetNodeGraph().GetNumNodes()` for one exact item.
- `fusion_parameters`: named `Tool.Input` values from a live Fusion comp.
- `api_readback`: current page, timeline, or playhead.

Text visibility and OCR are observation evidence only; they do not by
themselves verify an edit. `rendered_pixels` renders one frame at the target
item start and compares declared RGB samples with their tolerances. It is
unit-tested, but has not been live-qualified. Rendered audio is not an
acceptance verifier in this UI transaction and must be checked by an external
output verifier; no UI receipt may claim that dimension was verified. The
receipt reports `restored` and
`not_restored`, an overall `restoration_status`, and a
`recoverable_state_status` for page, timeline, and playhead. Selection and
focused panel remain explicitly un-restorable because no write API was
measured for them.

No new UI editing operation is marked live-qualified or production-enabled
by this matrix. UI mutation qualification remains on Ren Qualification;
Resolve doubles and locator tests are not live evidence. Existing AXI
operation status remains the registration authority. A UI-backed algebra
operation must be added there with its exact backend/operation evidence
before it can be described as qualified.
