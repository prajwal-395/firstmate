# Resolve UI capability inventory

This inventory records what Ren can currently reach through Resolve's UI
driver. It is an execution-capability description, not a claim that a UI edit
is production-qualified. UI mutations remain restricted to Ren Qualification
until a named operation has live evidence and an AXI operation contract.

## Targeting and evidence

| Mechanism | Current support | Evidence and limits |
|---|---|---|
| Accessibility | AX paths can identify accessible controls and invoke supported actions | Implemented by `native/resolve-control/src/ax.rs` and `library/tools/computer_use/targets.py`. A control absent from the accessibility tree is not addressable by this path. |
| OCR | Fresh screenshots can be OCR'd, and OCR labels/values can be used by measured panel recipes | Implemented by `native/resolve-control/src/ocr.rs`, `library/tools/computer_use/observe.py`, and `window_modify.py`. A control is supported only where its label/value has been observed and its read-back is defined. |
| Visual anchors | Image templates and relative/window/viewer coordinates are supported with fresh observation and window-bound checks | Implemented by `anchors.py`, `templates.py`, and `resolved/ui_transaction.py`. Template presence and coordinate geometry are recipe-specific; absolute coordinates are not a general capability. |
| Foreground input | Explicit, announced foreground takes can activate Resolve and send input | Guarded by `resolved/ui_transaction.py`; a foreground action is bounded, surfaced in the receipt, and cannot overlap a Resolve write lease. |

## Page coverage

The Resolve API provides project, media-pool, timeline, timeline-item, color,
gallery and media-storage objects. The scripting reference does not declare
page-specific control coverage, so the native column below identifies only
what the object API establishes. No page/control pair is live-qualified by
this inventory alone.

| Page | Native scripting coverage evidenced by installed API | UI driver path | Live-qualified UI controls |
|---|---|---|---|
| Media | Media storage, project media pool, folders and media-pool items | Accessibility/OCR/template/coordinate primitives; no general Media-page recipe catalog | None declared here |
| Cut | Timeline and timeline-item operations; page-specific controls are not declared | Generic UI transaction actions | None declared here |
| Edit | Timeline, timeline-item and track operations | Generic UI transaction actions and measured window recipes | None declared here |
| Fusion | Fusion compositions/tools through timeline-item APIs where exposed | Generic UI transaction actions; no complete node/editor map | None declared here |
| Color | Color groups, gallery stills and supported timeline-item color methods | The Window Transform controls Size, Aspect, Pan, Tilt, Rotate and Opacity have measured OCR labels/values and OCR read-back in `computer_use/window_modify.py`; generic UI transaction actions are also available | No production-enabled UI operation declared here; the measured window recipe remains Qualification-only |
| Fairlight | Audio-related timeline/item API methods where exposed | Generic UI transaction actions; no mixer/control coverage map | None declared here |
| Deliver | Render settings, jobs and queue operations through project APIs | Generic UI transaction actions | None declared here |

## Qualification rule

"Implemented" means a driver primitive or a measured recipe exists. It does
not mean the full Resolve page is covered. "Demonstrated" requires a recorded
live qualification for the exact control and Resolve version. "Speculative"
means a page or control is reachable only by generic input primitives without
a tested locator and read-back. Before production use, record the exact page,
control, locator type, Resolve version, state needed, verifier, restoration
behavior and qualification evidence in the capability registry.

Owners: `native/resolve-control/`, `library/tools/computer_use/`, and
`library/tools/resolved/ui_transaction.py`. The driver documents its request
and safety contract in `native/resolve-control/README.md`.
