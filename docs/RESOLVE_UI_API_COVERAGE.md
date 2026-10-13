# Resolve UI workaround coverage against the shipped scripting API

The source of documented methods is Resolve's installed
`Developer/Scripting/DaVinciResolveScript.pyi` and `CHANGELOG.md`, parsed by
`library/tools/resolve_api_capabilities.py`. The map is keyed by the exact
version returned by `Resolve.GetVersionString()` and includes each public
class, method signature, docstring, a stub fingerprint, and that release's
changelog entries. The probe reports the map in
`library/steps/step_6_01_render/probe_resolve_capabilities.py`.
Emit the JSON map with
`python3 -m library.tools.resolve_api_capabilities --version 21.1.00014`.

| UI workaround or workflow | Location | API method that covers it, or none | Replaced |
|---|---|---|---|
| Switch to Deliver and render the full timeline | `library/tools/execution/resolve_render.py` | `Project.SetRenderSettings`, `AddRenderJob`, `StartRendering`, `GetRenderJobStatus`, `DeleteRenderJob` | Yes. Render directly through the API and check settings, job id, start result, terminal status, and cleanup result. No page switch. |
| Switch to Deliver and render QA ranges | `library/tools/segment_renderer.py` | `Project.SetRenderSettings`, `AddRenderJob`, `StartRendering`, `GetRenderJobStatus`, `DeleteRenderJob` | Yes. Existing API route no longer switches pages; it verifies the output and only removes its own jobs. |
| Switch to Deliver to capture a frame for grade verification | `library/tools/color_page_grade.py` | `Project.SetRenderSettings`, `AddRenderJob`, `StartRendering`, `GetRenderJobStatus`, `DeleteRenderJob` | Yes. Frame capture uses the API. Screenshot and rendered-pixel verification remain. |
| Switch to Deliver in the reusable frame capture helper, then clear the whole render queue | `.agents/skills/davinci_resolve_pipeline/scripts/render_frame.py` | Same `Project` render methods | Yes. It now starts and checks its own job, then deletes only that job. |
| Place a supported Resolve transition | `library/tools/native_ops_apply.py`, `library/steps/step_6_01_render/resolve_build_timeline.py` | `TimelineItem.AddTransition` | Already. It checks the returned transition item and its measured placement. |
| Set a clip's fade durations | No UI-based fade writer found; the mix curve writer is `library/tools/otio_mix.py` | `TimelineItem.GetFades` / `SetFades` (21.1) | No UI workaround to replace. The existing OTIO mix expresses per-block dB automation and crossfades; those curves are not equivalent to a pair of fade durations. |
| Set audio clip gain or map source channels | Mix levels: `library/tools/otio_mix.py`; no source-channel mapping UI automation path found | 21.1 documents native audio item properties and `TimelineItem.SetSourceAudioChannelMapping`; it does not define the property names or units needed to replace the measured OTIO dB curve | No. Keep the OTIO curve until a disposable qualification establishes the audio property key, units, readback, and rendered result. No channel mapping call site was found to replace. |
| Edit dialogue EQ in Fairlight with AX/OCR locators and clicks | `library/tools/fairlight_eq.py` and `library/tools/computer_use/` | None. The shipped reference has Fairlight preset and normalization methods, but no EQ-band control method | No. The API has no equivalent for the EQ controls this recipe changes. |
| Create, move, track, or remove Color page windows; operate the qualifier viewer | `library/tools/computer_use/window_recipe.py`, `window_modify.py`, `window_track.py`, `window_delete.py`, `qualifier_recipe.py` | None for these UI controls. `TimelineItem.GetNodeGraph` is graph access, not an equivalent documented window/qualifier control surface | No. |
| General user-directed Resolve actions executed as anchored UI transactions | `library/tools/resolved/ui_transaction.py`, `library/tools/computer_use/scripts.py` | None universal. The 410-method map can identify methods for a particular action, but there is no scripting API method equivalent to an arbitrary click, key, or UI sequence | No. Keep these transactions for actions without a method-level equivalent. |
| Inspect Resolve pixels with screenshots or rendered frames for visual QA | `library/tools/computer_use/`, `library/tools/color_page_grade.py`, `library/tools/marker_capture.py` | None. The API does not make a visual judgment | No. Screenshot-based visual verification stays. |
| Open Edit before `Timeline.SetTrackEnable` | `library/steps/step_6_01_render/resolve_build_timeline.py` | `Resolve.OpenPage` is the documented context switch; the API setter only takes effect on Edit, as measured in the capability probe | No. This is an API precondition, not an edit workaround. |

The 21.1 stub documents `TimelineItem.AddTransition`, `GetFades` and
`SetFades`, audio mapping/property APIs, and `Project.GetRenderJobStatus`.
Method documentation establishes availability and signatures; it does not
establish runtime success, accepted property keys, units, or visual effect.
Each write must still be judged by its return value and read back where the
API provides a getter. No live Resolve operation was run for this inventory.
